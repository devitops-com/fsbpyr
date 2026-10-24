"""Native dependency analysis at build time (plan §3.5).

Every ``DT_NEEDED`` entry of every shared object in the payload must resolve either to another
payload file, through the object's RUNPATH (or RPATH) with ``$ORIGIN`` expanded, or to a system
library that manylinux wheels may rely on. An object without a RUNPATH also searches the RPATHs
of the objects that loaded it, as ld.so does. Because the native cache mirrors the site-packages
layout (D9), the auditwheel-style ``$ORIGIN`` paths keep working unchanged at run time.

Libraries an object opens by name at run time (``dlopen("libx_shared.so")``, onnxruntime's
provider bridge) have no ``DT_NEEDED`` entry. A bundled library in the object's own directory or
on its ``$ORIGIN`` search path whose file name the object contains as a C string counts as a
dependency too, so it is extracted next to it.

A library nothing on the search path provides may be one another distribution bundles
(torchvision's extensions need torch's ``libc10.so``, whose RPATH names the build machine of
PyTorch): it is taken from the payload's shared objects by SONAME or file name, if exactly one
has that name. At run time it is in the cache under its own site-relative path and already loaded
by its owner, whose import comes first, so ld.so reuses it by SONAME.
"""

import contextlib
import mmap
import posixpath
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .elf import ElfError, VersionNeed, dynamic_info, version_requirements
from .targets import host_target

# Libraries manylinux wheels may take from the system (auditwheel policy, manylinux_2_17 and
# later for x86-64 and aarch64, plus the dynamic loader and libz which current policies also
# allow).
_SYSTEM_LIBRARIES = frozenset({
    "libc.so.6", "libm.so.6", "libdl.so.2", "librt.so.1",
    "libpthread.so.0", "libutil.so.1", "libnsl.so.1", "libresolv.so.2", "libgcc_s.so.1",
    "libstdc++.so.6", "libz.so.1", "libX11.so.6", "libXext.so.6", "libXrender.so.1",
    "libICE.so.6", "libSM.so.6", "libGL.so.1", "libgobject-2.0.so.0", "libgthread-2.0.so.0",
    "libglib-2.0.so.0",
})  # fmt: skip
# The libraries of glibc itself: their versions are glibc's, reported as "glibc >= X".
_GLIBC_LIBRARIES = frozenset({
    "libc.so.6", "libm.so.6", "libdl.so.2", "librt.so.1",
    "libpthread.so.0", "libutil.so.1", "libnsl.so.1", "libresolv.so.2",
})  # fmt: skip


def _with_loader(libraries: frozenset[str]) -> frozenset[str]:
    loader = host_target().loader
    return libraries | {loader} if loader else libraries


def system_libraries() -> frozenset[str]:
    """What extensions may take from the system: the list and the host's dynamic loader."""
    return _with_loader(_SYSTEM_LIBRARIES)


def glibc_libraries() -> frozenset[str]:
    return _with_loader(_GLIBC_LIBRARIES)


# The newest symbol version per namespace that auditwheel's manylinux_2_34 policy allows
# (libstdc++ 11.x, libgcc_s, libz), per target, and the non-numeric CXXABI versions it allows:
# auditwheel's policy file read on 2026-10-22 (NAT-2). The targets differ in GCC only; the
# aarch64 policy lists CXXABI_TM_1 but not CXXABI_FLOAT128, and both use these markers.
MANYLINUX = "manylinux_2_34"
MANYLINUX_LIMITS: dict[str, dict[str, tuple[int, ...]]] = {
    "x86_64-linux-gnu": {
        "GLIBCXX": (3, 4, 29), "CXXABI": (1, 3, 13), "GCC": (7, 0, 0), "ZLIB": (1, 2, 9),
    },
    "aarch64-linux-gnu": {
        "GLIBCXX": (3, 4, 29), "CXXABI": (1, 3, 13), "GCC": (11, 0), "ZLIB": (1, 2, 9),
    },
}  # fmt: skip
MANYLINUX_MARKERS = frozenset({"CXXABI_TM_1", "CXXABI_FLOAT128"})


def manylinux_limits() -> dict[str, tuple[int, ...]]:
    """The host target's limits: fsbpyr builds for the host it runs on."""
    return MANYLINUX_LIMITS[host_target().name]
_LIBPYTHON = re.compile(r"^libpython3")
_ORIGIN = re.compile(r"\$(ORIGIN(?![A-Za-z0-9_])|\{ORIGIN\})")  # as ld.so tokenizes it


class NativeError(ValueError):
    """A native dependency cannot be satisfied inside the executable or by the system."""


class MissingLibraries(NativeError):
    """Libraries one native file needs that are neither bundled nor allowed system ones;
    ``closures`` gathers them for all files and reports them at once."""

    def __init__(self, path: str, sonames: list[str]) -> None:
        super().__init__(f"{path} needs {', '.join(sonames)}")
        self.path = path
        self.sonames = tuple(sonames)


type Native = bytes | mmap.mmap  # a native file's content: in memory, or mapped from its file
_WINDOW = 64 << 20  # searches go a window at a time; a mapping's pages are dropped after each


@dataclass(slots=True)
class SystemUse:
    """What the payload takes from the system: which files need each library the system
    satisfies, the symbol versions they need of it, and the allowlist entries that were used."""

    needed_by: dict[str, set[str]] = field(default_factory=dict)  # library -> payload files
    versions: dict[str, dict[str, dict[str, VersionNeed]]] = field(default_factory=dict)
    allowed_used: set[str] = field(default_factory=set)
    unreadable: dict[str, str] = field(default_factory=dict)  # file -> why its table is lost
    may_load: dict[str, set[str]] = field(default_factory=dict)  # library -> Python files (guess)

    def record(
        self, path: str, data: Native, libraries: list[str], allowed: frozenset[str]
    ) -> None:
        """``path`` needs ``libraries`` from the system; their versions are read from ``data``.
        A version table that cannot be read loses the versions, not the build."""
        if path in self.versions or path in self.unreadable:
            return
        for library in libraries:
            self.needed_by.setdefault(library, set()).add(path)
            if library in allowed:
                self.allowed_used.add(library)
        try:
            found = version_requirements(data)
        except ElfError as exc:
            self.unreadable[path] = str(exc)
            return
        self.versions[path] = {lib: found[lib] for lib in libraries if lib in found}


@contextlib.contextmanager
def opened(native: bytes | Path) -> Iterator[Native]:
    """A native file's content as it was given, or its file mapped read-only (its ELF tables are
    read in place, a few pages; searches drop what they read): never its size of memory (D38)."""
    if not isinstance(native, Path):
        yield native
        return
    with native.open("rb") as file:
        if file.seek(0, 2) == 0:
            yield b""
            return
        mapping = mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ)
    try:
        yield mapping
    finally:
        mapping.close()


def _found(data: Native, needle: bytes) -> Iterator[int]:
    """Where ``needle`` starts in ``data`` (some places may come twice), a window at a time."""
    for start in range(0, len(data), _WINDOW):
        end = min(len(data), start + _WINDOW + len(needle) - 1)  # a match across the edge too
        position = data.find(needle, start, end)
        while position >= 0:
            yield position
            position = data.find(needle, position + 1, end)
        if isinstance(data, mmap.mmap):
            data.madvise(mmap.MADV_DONTNEED, start, min(_WINDOW, len(data) - start))


def _search_path(path: str, rpath: tuple[str, ...], runpath: tuple[str, ...]) -> list[str]:
    origin = posixpath.dirname(path) or "."  # top-level files: site-packages itself
    directories = []
    for entry in runpath or rpath:  # the loader ignores RPATH when RUNPATH is present
        if not _ORIGIN.match(entry):
            continue  # absolute or relative-to-cwd entries never point into the payload
        directories.append(posixpath.normpath(_ORIGIN.sub(lambda _: origin, entry)))
    return directories


def init_symbol(path: str) -> bytes:
    """The C string CPython looks up in an extension: ``PyInit_<name>``, where a compiled
    package ``__init__`` takes its package's name (Cython's ``cy/__init__....so``)."""
    directory, _, filename = path.rpartition("/")
    stem = filename.partition(".")[0]
    if stem == "__init__":
        stem = directory.rpartition("/")[2]
    return b"PyInit_" + stem.encode() + b"\0"


def is_extension_module(path: str, data: Native) -> bool:
    """Whether a shared object is a Python extension: it has the PyInit_ CPython would call (a
    library may still end in ``.so``, an extension suffix)."""
    return next(_found(data, init_symbol(path)), None) is not None


def _contains_name(data: Native, name: bytes) -> bool:
    """Whether ``data`` holds ``name`` as a whole C string, or as the last part of a path."""
    return any(start == 0 or data[start - 1] in b"\0/" for start in _found(data, name + b"\0"))


def _contained_names(data: Native, names: Iterable[bytes]) -> set[bytes]:
    """The names ``data`` holds as whole C strings, or as the last part of a path: one pass for
    ``.so``, which every shared object's name holds (torch's 438 MB library has it 75 times:
    0.18 s instead of 1.02 s for its 12 neighbours searched one by one), each name checked
    where it would start; a name without it is searched alone."""
    names = list(dict.fromkeys(names))
    found = {name for name in names if b".so" not in name and _contains_name(data, name)}
    shared = {name: name.find(b".so") for name in names if b".so" in name}
    for position in _found(data, b".so") if shared else ():
        for name, offset in shared.items():
            start = position - offset
            if (name not in found and start >= 0 and data[start : start + len(name) + 1]
                    == name + b"\0" and (start == 0 or data[start - 1] in b"\0/")):  # fmt: skip
                found.add(name)
    return found


def _libraries(
    natives: Mapping[str, bytes | Path],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """The shared libraries (not extensions) of the payload by directory, and by the names a
    ``DT_NEEDED`` entry may use for them: their file name and their SONAME."""
    libraries: dict[str, list[str]] = {}
    providers: dict[str, list[str]] = {}
    for path in sorted(natives):
        with opened(natives[path]) as data:
            if is_extension_module(path, data):
                continue
            try:
                soname = dynamic_info(data).soname
                names = {posixpath.basename(path), *([soname] if soname else [])}
            except ElfError:
                names = set()  # no library for another object; reported where its needs are read
        libraries.setdefault(posixpath.dirname(path) or ".", []).append(path)
        for name in names:
            providers.setdefault(name, []).append(path)
    return libraries, providers


def _provider(path: str, needed: str, providers: dict[str, list[str]]) -> str | None:
    """The one library named ``needed`` that a top-level directory other than ``path``'s (the
    nearest thing to another distribution) bundles; None if there is none. Different files of
    that name would load as one, so the build cannot tell which one the app gets."""
    own = path.partition("/")[0]
    found = sorted(lib for lib in providers.get(needed, ()) if lib.partition("/")[0] != own)
    if len(found) > 1:
        raise NativeError(f"{path} needs {needed}, which {' and '.join(found)} bundle; "
                          "the build cannot tell which one the app loads")  # fmt: skip
    return found[0] if found else None


def _opened_by_name(
    path: str, data: Native, libraries: dict[str, list[str]], directories: list[str]
) -> list[str]:
    """Bundled libraries where the loader (and code next to ``path``) looks for bare names,
    which ``path`` names: loaded with dlopen at run time."""
    candidates = [library
                  for directory in dict.fromkeys([posixpath.dirname(path) or ".", *directories])
                  for library in libraries.get(directory, ()) if library != path]  # fmt: skip
    named = _contained_names(data, (posixpath.basename(c).encode() for c in candidates))
    return [library for library in candidates if posixpath.basename(library).encode() in named]


def _direct_dependencies(  # noqa: PLR0913  the object, the payload, and inherited keyword-only
    path: str,
    data: Native,
    payload: set[str],
    libraries: dict[str, list[str]],
    allowed: frozenset[str],
    *,
    inherited: tuple[str, ...],
    providers: dict[str, list[str]],
    system: SystemUse | None = None,
) -> tuple[list[tuple[str, bool]], tuple[str, ...]]:
    """Bundled objects ``path`` loads, each with whether it is opened by name (not NEEDED), and
    the RPATH directories they inherit. ``inherited`` are those of the objects that loaded
    ``path``: ld.so searches them after its own RPATH unless it has a RUNPATH, and passes them
    on either way (gnureadline's bundled libncurses, without RPATH, finds libtinfo so)."""
    try:
        info = dynamic_info(data)
    except ElfError as exc:
        raise NativeError(f"{path}: {exc}") from exc
    own = _search_path(path, info.rpath, info.runpath)
    directories = own if info.runpath else list(dict.fromkeys([*own, *inherited]))
    passed_on = inherited if info.runpath else tuple(directories)
    dependencies = [(lib, True) for lib in _opened_by_name(path, data, libraries, directories)]
    from_system = []
    missing = []
    for needed in info.needed:
        if _LIBPYTHON.match(needed):
            raise NativeError(
                f"{path} links against {needed}; extensions must take libpython symbols from "
                "the interpreter (rebuild it as a manylinux wheel)"
            )
        if "/" in needed:  # ld.so would open it relative to the working directory
            raise NativeError(f"{path} needs {needed} by path, which cannot be bundled")
        candidates = (posixpath.normpath(posixpath.join(d, needed)) for d in directories)
        found = next((c for c in candidates if c in payload), None)
        if found is None and needed not in system_libraries() and needed not in allowed:
            found = _provider(path, needed, providers)  # bundled by another distribution
        if found is not None:
            dependencies.append((found, False))
        elif needed not in system_libraries() and needed not in allowed:
            missing.append(needed)
        else:
            from_system.append(needed)
    if missing:
        raise MissingLibraries(path, missing)
    if system is not None and from_system:
        system.record(path, data, from_system, allowed)
    return dependencies, passed_on


def closures(  # noqa: PLR0913  the payload's natives and executables, keyword-only options
    natives: Mapping[str, bytes | Path],
    extensions: set[str],
    *,
    allowed: frozenset[str],
    warn: Callable[[str], None] = lambda _: None,
    system: SystemUse | None = None,
    executables: Mapping[str, bytes | Path] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Bundled shared objects each extension module loads, transitively (excluding itself):
    NEEDED ones, and libraries opened by name. A library opened by name whose own needs cannot
    be met (a CUDA provider without the CUDA runtime) is left out with a warning; it would not
    load anyway, and the build must not fail over a guess. A native given as a Path is mapped
    from its file while it is looked into (``opened``). ``system`` collects what the objects
    reached take from the system (``SystemUse``); entries of ``allowed`` it never saw, and
    version tables it could not read, are warned about. Shared objects no extension reaches,
    and ``executables`` (ELF programs among the resources), are checked as far as their needs
    go, with warnings (``_check_unreached``); every RPATH/RUNPATH entry the loader would search
    outside the payload is warned about (``_warn_search_paths``)."""
    payload = set(natives)
    libraries, providers = _libraries(natives)
    # (object, RPATHs it inherits) -> (its dependencies, the RPATHs they inherit)
    direct: dict[tuple[str, tuple[str, ...]], tuple[list[tuple[str, bool]], tuple[str, ...]]] = {}
    left_out: dict[str, str] = {}
    unmet: dict[str, tuple[str, ...]] = {}  # object -> libraries it needs and nothing provides

    def dependencies(
        path: str, inherited: tuple[str, ...]
    ) -> list[tuple[str, bool, tuple[str, ...]]]:  # only reachable objects are checked
        if (path, inherited) not in direct:
            with opened(natives[path]) as data:
                direct[path, inherited] = _direct_dependencies(
                    path, data, payload, libraries, allowed, inherited=inherited,
                    providers=providers, system=system
                )
        found, passed_on = direct[path, inherited]
        return [(library, by_name, passed_on) for library, by_name in found]

    def tolerated(
        path: str, inherited: tuple[str, ...], *, strict: bool
    ) -> list[tuple[str, bool, tuple[str, ...]]]:
        """``dependencies``; for a NEEDED chain (``strict``) the libraries nothing provides are
        noted, not raised, so that all of them are reported together. Such an object has no
        dependencies as far as the walk goes."""
        try:
            return dependencies(path, inherited)
        except MissingLibraries as exc:
            if not strict:
                raise
            unmet[exc.path] = exc.sonames
            return []

    result = {}
    for extension in sorted(extensions):
        seen: dict[str, bool] = {}  # object -> expanded through NEEDED edges only (strictly)
        pending = tolerated(extension, (), strict=True)
        while pending:
            dependency, by_name, inherited = pending.pop()
            if dependency == extension or (by_name and dependency in left_out):
                continue
            if dependency in seen and (by_name or seen[dependency]):
                continue  # a NEEDED visit re-expands what a by-name visit tolerated
            try:
                following = tolerated(dependency, inherited, strict=not by_name)
            except NativeError as exc:
                if not by_name:
                    raise
                left_out[dependency] = (
                    f"{exc} (reached from {extension} through a library it opens by name; left out)"
                )
                continue
            seen[dependency] = not by_name
            pending.extend((library, by_name or named, passed_on)
                           for library, named, passed_on in following)  # fmt: skip
        result[extension] = tuple(sorted(seen))
    if unmet:
        raise NativeError(_unmet_message(unmet))
    for message in left_out.values():
        warn(message)
    reached = {member for members in result.values() for member in members}
    unreached = [
        lib
        for libs in libraries.values()
        for lib in libs
        if lib not in reached and lib not in left_out
    ]
    checked = {path: natives[path] for path in unreached} | dict(executables or {})
    _check_unreached(checked, payload, libraries, providers, allowed=allowed, warn=warn,
                     system=system)
    _warn_search_paths({**natives, **(executables or {})}, warn)
    if system is not None:
        _warn_system(system, allowed, warn)
    return result


def _check_unreached(  # noqa: PLR0913  what is checked, and what it is checked against
    files: Mapping[str, bytes | Path],
    payload: set[str],
    libraries: dict[str, list[str]],
    providers: dict[str, list[str]],
    *,
    allowed: frozenset[str],
    warn: Callable[[str], None],
    system: SystemUse | None,
) -> None:
    """Libraries no extension module reaches (ctypes and cffi loads, fs-package libraries) and
    executables are packaged unchecked otherwise: what they need that nothing provides is warned
    about, not raised (nothing may load them), and what they take from the system is recorded."""
    for path in sorted(files):
        with opened(files[path]) as data:
            try:
                _direct_dependencies(path, data, payload, libraries, allowed, inherited=(),
                                     providers=providers, system=system)  # fmt: skip
            except NativeError as exc:
                warn(f"{exc}; no extension module loads it, so this matters only if the "
                     "application opens it")  # fmt: skip


def _warn_search_paths(files: Mapping[str, bytes | Path], warn: Callable[[str], None]) -> None:
    """RPATH/RUNPATH entries the loader uses that are not based on ``$ORIGIN`` point outside
    the payload: ld.so searches them on the target (no patching, D9)."""
    for path in sorted(files):
        with opened(files[path]) as data:
            try:
                info = dynamic_info(data)
            except ElfError:
                continue  # reported where its needs are read
        kind = "RUNPATH" if info.runpath else "RPATH"
        for entry in info.runpath or info.rpath:
            if _ORIGIN.match(entry):
                continue
            if entry.startswith(("/", "$")):
                warn(f"{path}: {kind} entry {entry} is outside the executable; ld.so searches "
                     "it on the target")  # fmt: skip
            else:
                warn(f"{path}: {kind} entry {entry} is relative to the working directory; "
                     "ld.so searches it there on the target, whatever the build found")  # fmt: skip


def _unmet_message(unmet: Mapping[str, tuple[str, ...]]) -> str:
    """Every missing library, by the file that needs it, with the config line that allows them."""
    lines = [f"  {path} needs {', '.join(sonames)}" for path, sonames in sorted(unmet.items())]
    names = sorted({soname for sonames in unmet.values() for soname in sonames})
    allow = ", ".join(f'"{name}"' for name in names)
    return ("native files need libraries that are neither bundled nor standard system "
            "libraries:\n" + "\n".join(lines) + "\nbundle them (auditwheel repair), or allow "
            "the host's with --allow-system-lib NAME (repeat), or in [tool.fsbpyr]:\n"
            f"  allow-system-libs = [{allow}]")  # fmt: skip


def _warn_system(system: SystemUse, allowed: frozenset[str], warn: Callable[[str], None]) -> None:
    for library in sorted(allowed - system.allowed_used):
        warn(f"--allow-system-lib {library}: no native file in the payload needs it")  # fmt: skip
    for path, reason in sorted(system.unreadable.items()):
        warn(f"{path}: symbol versions unreadable ({reason}); the system libraries it needs "
             "are listed without versions")  # fmt: skip
