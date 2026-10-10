"""Native dependency analysis at build time (plan §3.5).

Every ``DT_NEEDED`` entry of every shared object in the payload must resolve either to another
payload file, through the object's RUNPATH (or RPATH) with ``$ORIGIN`` expanded, or to a system
library that manylinux wheels may rely on. Because the native cache mirrors the site-packages
layout (D9), the auditwheel-style ``$ORIGIN`` paths keep working unchanged at run time.

Libraries an object opens by name at run time (``dlopen("libx_shared.so")``, onnxruntime's
provider bridge) have no ``DT_NEEDED`` entry. A bundled library in the object's own directory or
on its ``$ORIGIN`` search path whose file name the object contains as a C string counts as a
dependency too, so it is extracted next to it.
"""

import contextlib
import mmap
import posixpath
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path

from .elf import ElfError, dynamic_info

# Libraries manylinux wheels may take from the system (auditwheel policy, manylinux_2_17 and
# later for x86-64, plus the dynamic loader and libz which current policies also allow).
SYSTEM_LIBRARIES = frozenset({
    "ld-linux-x86-64.so.2", "libc.so.6", "libm.so.6", "libdl.so.2", "librt.so.1",
    "libpthread.so.0", "libutil.so.1", "libnsl.so.1", "libresolv.so.2", "libgcc_s.so.1",
    "libstdc++.so.6", "libz.so.1", "libX11.so.6", "libXext.so.6", "libXrender.so.1",
    "libICE.so.6", "libSM.so.6", "libGL.so.1", "libgobject-2.0.so.0", "libgthread-2.0.so.0",
    "libglib-2.0.so.0",
})  # fmt: skip
_LIBPYTHON = re.compile(r"^libpython3")
_ORIGIN = re.compile(r"\$(ORIGIN(?![A-Za-z0-9_])|\{ORIGIN\})")  # as ld.so tokenizes it


class NativeError(ValueError):
    """A native dependency cannot be satisfied inside the executable or by the system."""


type Native = bytes | mmap.mmap  # a native file's content: in memory, or mapped from its file
_WINDOW = 64 << 20  # searches go a window at a time; a mapping's pages are dropped after each


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


def _direct_dependencies(
    path: str,
    data: Native,
    payload: set[str],
    libraries: dict[str, list[str]],
    allowed: frozenset[str],
) -> list[tuple[str, bool]]:
    """Bundled objects ``path`` loads, each with whether it is opened by name (not NEEDED)."""
    try:
        info = dynamic_info(data)
    except ElfError as exc:
        raise NativeError(f"{path}: {exc}") from exc
    directories = _search_path(path, info.rpath, info.runpath)
    dependencies = [(lib, True) for lib in _opened_by_name(path, data, libraries, directories)]
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
        if found is not None:
            dependencies.append((found, False))
        elif needed not in SYSTEM_LIBRARIES and needed not in allowed:
            raise NativeError(
                f"{path} needs {needed}, which is neither bundled nor a standard system library; "
                f"bundle it (auditwheel repair) or pass --allow-system-lib {needed}"
            )
    return dependencies


def closures(
    natives: Mapping[str, bytes | Path],
    extensions: set[str],
    *,
    allowed: frozenset[str],
    warn: Callable[[str], None] = lambda _: None,
) -> dict[str, tuple[str, ...]]:
    """Bundled shared objects each extension module loads, transitively (excluding itself):
    NEEDED ones, and libraries opened by name. A library opened by name whose own needs cannot
    be met (a CUDA provider without the CUDA runtime) is left out with a warning; it would not
    load anyway, and the build must not fail over a guess. A native given as a Path is mapped
    from its file while it is looked into (``opened``)."""
    payload = set(natives)
    libraries: dict[str, list[str]] = {}  # directory -> shared libraries (not extensions) in it
    for path in sorted(payload):
        with opened(natives[path]) as data:
            if not is_extension_module(path, data):
                libraries.setdefault(posixpath.dirname(path) or ".", []).append(path)
    direct: dict[str, list[tuple[str, bool]]] = {}
    left_out: dict[str, str] = {}

    def dependencies(path: str) -> list[tuple[str, bool]]:  # only reachable objects are checked
        if path not in direct:
            with opened(natives[path]) as data:
                direct[path] = _direct_dependencies(path, data, payload, libraries, allowed)
        return direct[path]

    result = {}
    for extension in sorted(extensions):
        seen: dict[str, bool] = {}  # object -> expanded through NEEDED edges only (strictly)
        pending = list(dependencies(extension))
        while pending:
            dependency, by_name = pending.pop()
            if dependency == extension or (by_name and dependency in left_out):
                continue
            if dependency in seen and (by_name or seen[dependency]):
                continue  # a NEEDED visit re-expands what a by-name visit tolerated
            try:
                following = dependencies(dependency)
            except NativeError as exc:
                if not by_name:
                    raise
                left_out[dependency] = (
                    f"{exc} (reached from {extension} through a library it opens by name; left out)"
                )
                continue
            seen[dependency] = not by_name
            pending.extend((library, by_name or named) for library, named in following)
        result[extension] = tuple(sorted(seen))
    for message in left_out.values():
        warn(message)
    return result
