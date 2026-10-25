"""RECORD entries -> payload modules, files and namespace packages (plan §3.6 steps 6-7)."""

import csv
import posixpath
import re
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath

from .closure import InstalledDist
from .compiler import Compiled, Compiler, Job, compiled, compressed
from .elf import ElfError, glibc_requirement, is_elf, is_executable, is_foreign
from .loose import ROOT
from .natives import NativeError, SystemUse, closures, is_extension_module, opened
from .payload.format import (
    NATIVE_KINDS,
    THREADS,
    FileFlag,
    FileKind,
    ModuleFlag,
    ModuleKind,
    is_module_part,
    pooled_thread,
    tree_digest,
)
from .payload.spec import ZSTD_LEVEL, Blob, Dist, File, Module, xz_pieces, xz_stream
from .payload.spill import Spill

# Written by installers, not by the wheel: they embed paths, timestamps or the installer's name,
# so packaging them would make builds machine-specific and non-reproducible.
INSTALLER_FILES = frozenset({
    "INSTALLER", "REQUESTED", "direct_url.json", "uv_cache.json", "RECORD.jws", "RECORD.p7s",
})  # fmt: skip


_SHARED_OBJECT = re.compile(r"\.so(\.\d[0-9A-Za-z]*)*$")  # libcrypto.so.1.1.1k too
_TEST_DIRECTORIES = frozenset({"tests", "test"})
# .pth files whose code only re-creates what PEP 420 namespaces (and the payload) provide
_REDUNDANT_PTH = re.compile(r"(-nspkg|^distutils-precedence)\.pth$")
_EXECUTABLE_MODE = 0o755  # fixed for resources that were executable in the wheel (D27)
_NATIVE_MODE = 0o755  # fixed: the installer's umask must not leak into the build
# Packages that need their data files on disk, whatever the configuration says (plan §3.5).
# Each one was verified to fail without (Phase 6b, fixture datafiles; litellm with aiagent;
# torch 2.14: "Unable to find torch_shm_manager"), except certifi, which hands out the path of
# cacert.pem: otherwise a temporary copy made again in every process.
BUILTIN_FS_PACKAGES = frozenset({"babel", "botocore", "certifi", "litellm", "matplotlib",
                                 "torch"})  # fmt: skip


def _digested(blob: Blob) -> Blob:
    """The digest from the bytes themselves (or what was learned of them as they were
    compressed), never one the blob arrived with."""
    return replace(blob, sha256=blob.known or tree_digest(blob.content()))


_IN_MEMORY = 16 << 20  # files up to this size are read whole; larger ones streamed (D38)
_COMPRESSING = 64 << 20  # bytes of files read whole waiting for a thread, at most (D38, D39)


def _native_blobs(
    natives: dict[str, Path], level: int, xz: bool, spill: Spill | None
) -> dict[str, Blob]:
    """zstd at ``level``, or xz (D33): the pieces of every file on min(8, cores) threads,
    largest first (zstd and lzma release the GIL while they compress; D39). With a spill,
    compressed from the files a piece at a time into it (D38)."""
    if spill is not None:
        return spill.compress_files(natives, xz=xz, level=level, digest=True)
    return _native_blobs_in_memory({rel: path.read_bytes() for rel, path in natives.items()},
                                   level, xz, THREADS)  # fmt: skip


def _native_blobs_in_memory(
    natives: dict[str, bytes], level: int, xz: bool, threads: int
) -> dict[str, Blob]:
    if not xz:
        return {rel: Blob.zstd(data, level=level, digest=True) for rel, data in natives.items()}
    pieces = {rel: xz_pieces(data) for rel, data in natives.items()}
    jobs = sorted(((rel, n) for rel, parts in pieces.items() for n in range(len(parts))),
                  key=lambda job: (-len(pieces[job[0]][job[1]]), job))  # fmt: skip
    with ThreadPoolExecutor(threads, initializer=pooled_thread) as pool:
        streams = dict(zip(jobs, pool.map(lambda job: xz_stream(pieces[job[0]][job[1]]), jobs),
                           strict=True))  # fmt: skip
    return {rel: Blob.from_xz(data, [streams[rel, n] for n in range(len(pieces[rel]))],
                              digest=True) for rel, data in natives.items()}  # fmt: skip


def _starts_elf(path: Path) -> bool:
    with path.open("rb") as file:
        return is_elf(file.read(4))


class ClassifyError(RuntimeError):
    """The installed files cannot be packaged (yet)."""


class Uncounted(str):
    """A warning that ``--strict`` does not count: a guess that only fs-packages silences."""


@dataclass(frozen=True, slots=True)
class Content:
    modules: tuple[Module, ...]
    files: tuple[File, ...]
    dists: tuple[Dist, ...]
    warnings: tuple[str, ...]
    glibc: tuple[tuple[int, int], str] | None = None  # newest need of a native file, and the file
    system: SystemUse = field(default_factory=SystemUse)  # what the natives take from the system


def module_name(rel: str) -> tuple[str, bool] | None:
    """Dotted name and is-package flag of a site-relative .py path, if importable. A module
    file's stem may contain hyphens or start with a digit: ``importlib.import_module`` reaches
    it (rich's tables, Django's ``0001_initial`` migrations). Like any module, such a file is
    kept as bytecode only under ``--no-source``."""
    parts = list(PurePosixPath(rel).with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    if not parts or not all(part.isidentifier() for part in parts[:-1]):
        return None
    if not (parts[-1].isidentifier() or (not is_package and is_module_part(parts[-1]))):
        return None
    return ".".join(parts), is_package


def _unimportable_warning(dist: InstalledDist, rel: str) -> list[str]:
    """A warning for a ``.py`` file of the project that is stored as a file because a directory
    above it is no Python name (``pkg/sub-dir/c.py``): no import reaches it."""
    if rel.startswith(f"{dist.dist_info}/") or all(
        part.isidentifier() for part in rel.split("/")[:-1]
    ):
        return []
    return [f"{rel} is stored as a file, not as a module: a directory name is not a valid "
            "Python name, so no import reaches it"]  # fmt: skip


def extension_name(rel: str, suffixes: tuple[str, ...]) -> tuple[str, bool, int] | None:
    """Dotted name, is-package flag (``__init__`` compiled by Cython and the like) and suffix
    rank of an extension module file, if importable. CPython calls ``PyInit_<stem>``, so a
    module file's stem may start with a digit (mypyc's shared ``<hash>__mypyc``, which the
    extensions it compiled import by name) but never contains a hyphen (``libbar-2.so``)."""
    for rank, suffix in enumerate(suffixes):
        if rel.endswith(suffix):
            parts = rel[: -len(suffix)].split("/")
            is_package = len(parts) > 1 and parts[-1] == "__init__"
            if is_package:
                parts.pop()
            last = parts[-1] if is_package else f"_{parts[-1]}"
            if all(part.isidentifier() for part in parts[:-1]) and last.isidentifier():
                return ".".join(parts), is_package, rank
    return None


def _library_stem(rel: str) -> bytes:
    """``libx`` for ``.../libx.so.1.2``: the part code names whatever the version."""
    return posixpath.basename(rel).partition(".so")[0].encode()


def _record_path(line: str) -> str:
    """First CSV field of a RECORD line (paths containing commas are quoted)."""
    return next(csv.reader([line]), [""])[0]


def _outside(rel: str) -> bool:
    """A RECORD entry that is not below site-packages (``../../../share/x``, ``/etc/x``)."""
    return rel.startswith("/") or ".." in PurePosixPath(rel).parts


def _outside_directory(rel: str) -> str:
    """The first real directory of an entry outside site-packages (``share`` for
    ``../../../share/x/y``)."""
    parts = [part for part in PurePosixPath(rel).parts if part not in ("/", "..")]
    return parts[0] if parts else ""


def _legacy_script_warnings(dist: InstalledDist) -> list[str]:
    """One warning for each file the project installs under bin/ that is neither a console or
    gui script nor a legacy script that starts with ``#!python`` (setup.py ``scripts=``, a shell
    script say): the bin/ exemption drops them."""
    entry_scripts = {*dist.console_scripts(), *dist.gui_scripts(), *dist.legacy}
    legacy = sorted({
        PurePosixPath(rel).name for rel in map(_record_path, dist.record)
        if _outside(rel) and _outside_directory(rel) == "bin"
        and PurePosixPath(rel).name not in entry_scripts
    })  # fmt: skip
    return [f"{dist.name}: legacy script {name} is not packaged (only a legacy script that "
            f"starts with #!python is; use [project.scripts] console scripts or --entry)"
            for name in legacy]


def _packaged(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return bool(parts) and not (
        _outside(rel) or "__pycache__" in parts or rel.endswith(".pyc")
    )


class _Collector:
    def __init__(  # noqa: PLR0913  the build options, keyword-only
        self,
        extension_suffixes: tuple[str, ...],
        include_source: bool,
        *,
        hints: bool = False,
        level: int = ZSTD_LEVEL,
        xz_natives: bool = False,
        spill: Spill | None = None,
        runtime_modules: frozenset[str] = frozenset(),
        project: str | None = None,
        origins: Mapping[str, str] | None = None,
    ) -> None:
        self.runtime_modules = runtime_modules  # found before the payload's (assemble)
        self.origins = origins or {}  # scripts root: payload path -> project file, if renamed
        self.project = project  # the project's distribution: its clashes fail the build
        self.level = level  # zstd level of every blob
        self.xz_natives = xz_natives  # native files as xz instead (D33)
        self.spill = spill  # where compressed bytes go instead of memory (D38)
        self.suffixes = extension_suffixes
        self.include_source = include_source
        self.hints = hints  # guesses (reads next to __file__): only when asked for
        self.modules: dict[str, Module] = {}
        self.files: dict[str, File] = {}
        self.owners: dict[str, str] = {}  # path -> dist
        self.natives: dict[str, Path] = {}  # ELF shared objects, path -> their file
        self.system = SystemUse()
        self.elf_resources: dict[str, Path] = {}  # ELF files kept as data: executables, maybe
        self.executables: dict[str, Path] = {}  # those that are programs (add_natives)
        # Directories that top-level .pth files add to sys.path, in site.py's order.
        self.roots: list[str] = []
        # CPython's order: sys.path entry (site-packages, then .pth directories), then within a
        # directory a package before a module file, then extension suffixes before .py.
        self.ranks: dict[str, tuple[int, bool, int]] = {}  # module -> (entry, not package, loader)
        self.warnings: list[str] = []
        self.broken: set[str] = set()  # package directories whose __init__.py does not compile
        self.sites: dict[str, Path] = {}  # dist -> site-packages directory
        self.reads: list[tuple[str, str, int]] = []  # (dist, path, line) reading next to __file__
        self.reached: set[str] = set()  # shared objects some extension's closure loads
        self.fs_directories: tuple[str, ...] = ()  # FS_REQUIRED package directories, with "/"
        self.large: dict[str, tuple[Path, FileKind, str, int]] = {}  # file, kind, dist, mode (D38)
        self.compiler: Compiler | None = None  # modules compiled ahead on workers (D39)
        self._compressor: ThreadPoolExecutor | None = None  # files compressed on threads (D39)
        # rel, kind, dist, mode, size, compressed blob and digest
        self._compressing: deque[
            tuple[str, FileKind, str, int, int, Future[tuple[Blob, bytes]]]
        ] = deque()
        self._compressing_bytes = 0

    def read_pth_files(self, dists: list[InstalledDist]) -> None:
        """Path lines of top-level .pth files become extra module roots; code lines never run."""
        found = sorted(
            ((rel, dist) for dist in dists for rel in map(_record_path, dist.record)
             if rel.endswith(".pth") and "/" not in rel and not rel.startswith(".")),
            key=lambda item: (item[0], item[1].name),
        )  # fmt: skip
        for rel, dist in found:
            if not (dist.site / rel).is_file():
                continue  # reported as missing when its dist is added
            text = (dist.site / rel).read_bytes().decode("utf-8-sig", errors="replace")
            has_code = False
            for line in text.splitlines():
                if not line.strip() or line.startswith("#"):
                    continue
                if line.startswith(("import ", "import\t")):
                    has_code = True
                    continue
                root = posixpath.normpath(line.rstrip())
                if root.startswith(("/", "../")) or root == "..":
                    self.warnings.append(
                        f"{dist.name}: {rel} adds {line.strip()!r}, outside site-packages; "
                        "it is not packaged"
                    )
                elif root not in self.roots:
                    self.roots.append(root)
            if has_code and not _REDUNDANT_PTH.search(rel):
                self.warnings.append(
                    f"{dist.name}: {rel} has code lines, which are not run in executables"
                )

    def _names[T](self, rel: str, name: Callable[[str], T | None]) -> list[tuple[T, int]]:
        """``name`` of ``rel`` relative to site-packages and to each .pth root, with the rank
        of that sys.path entry."""
        bases = [("", 0)] + [(root + "/", i) for i, root in enumerate(self.roots, 1)]
        found = []
        for base, entry in bases:
            if rel.startswith(base) and (named := name(rel[len(base) :])) is not None:
                found.append((named, entry))
        return found

    def add_dist(self, dist: InstalledDist) -> None:
        self.sites[dist.name] = dist.site
        info = dist.dist_info
        record_lines = []
        outside: dict[str, int] = {}  # data_files, headers: directory -> files
        for line in dist.record:
            rel = _record_path(line)
            if rel.startswith(f"{info}/") and rel[len(info) + 1 :] in INSTALLER_FILES:
                continue
            if not _packaged(rel):  # scripts (staging shebangs), bytecode caches (mtimes)
                if _outside(rel) and _outside_directory(rel) not in ("bin", ""):
                    directory = _outside_directory(rel)
                    outside[directory] = outside.get(directory, 0) + 1
                elif rel.endswith(".pyc") and "__pycache__" not in PurePosixPath(rel).parts:
                    self.warnings.append(
                        f"{dist.name}: {rel} is bytecode without source and is not packaged; "
                        "importing it fails"
                    )
                continue
            record_lines.append(line)
            if rel == "pkg_resources/__init__.py" and dist.name == "setuptools":
                self.warnings.append(
                    f"{dist.name}: pkg_resources finds no distributions in executables; "
                    "use importlib.metadata"
                )
            if rel != f"{info}/RECORD":
                self._add_path(dist, rel)
        if dist.name == self.project:
            self.warnings += _legacy_script_warnings(dist)
        if outside:
            where = ", ".join(f"{directory}/" for directory in sorted(outside))
            self.warnings.append(
                f"{dist.name}: {sum(outside.values())} files under {where} are not packaged; "
                "sys.prefix lookups will not find them"
            )
        record = ("\n".join(record_lines) + "\n").encode()
        self._add_file(dist, f"{info}/RECORD", FileKind.METADATA, record)

    def _add_path(self, dist: InstalledDist, rel: str) -> None:
        path = dist.site / rel
        if rel in self.owners:
            self.warnings.append(f"{rel} is installed by both {self.owners[rel]} and {dist.name}")
            return
        if not path.is_file():
            self.warnings.append(f"{dist.name}: {rel} is listed in RECORD but missing")
            return
        if rel.endswith(".pth") and "/" not in rel:  # read by read_pth_files()
            return
        if rel.endswith(self.suffixes) or _SHARED_OBJECT.search(rel):
            with path.open("rb") as file:
                head = file.read(64)
            if is_elf(head) and not is_foreign(head):
                self._add_native(dist, rel, path)
                return
            # debugpy's universal wheel, which aarch64 installs, has an x86-64 library
            what = ("an ELF object for another machine" if is_elf(head)
                    else "not an ELF shared object")  # fmt: skip
            self.warnings.append(f"{dist.name}: {rel} kept as data ({what})")
        names = self._names(rel, module_name) if rel.endswith(".py") else []
        if rel.endswith(".py") and not names and dist.name == self.project:
            self.warnings += _unimportable_warning(dist, rel)
        large = path.stat().st_size > _IN_MEMORY
        if names and large:  # compiling needs it whole (D38)
            self.warnings.append(f"{dist.name}: {rel} kept as data (over {_IN_MEMORY >> 20} MiB: "
                                 "too large to compile)")  # fmt: skip
        elif names and self._add_module(dist, rel, names, path):
            return
        kind = FileKind.METADATA if rel.startswith(f"{dist.dist_info}/") else FileKind.RESOURCE
        executable = kind is FileKind.RESOURCE and path.stat().st_mode & 0o100
        mode = _EXECUTABLE_MODE if executable else 0o644  # files(pkg) / "bin/tool" runs (D27)
        if executable and _starts_elf(path):
            self.elf_resources[rel] = path
        if self.spill is not None and large:  # streamed (D38), with the others (add_large)
            self.large[rel] = (path, kind, dist.name, mode)
            self.owners[rel] = dist.name
            return
        self._add_file(dist, rel, kind, path.read_bytes(), mode)

    def _add_module(
        self, dist: InstalledDist, rel: str, names: list[tuple[tuple[str, bool], int]], path: Path
    ) -> bool:
        tests = _TEST_DIRECTORIES.intersection(rel.split("/")[:-1])  # tests are never imported
        hints = self._reads_wanted(rel) and not tests
        result = self._compiled(dist, rel, path, hints)
        if result.code is None:
            message, line = result.error or ("does not compile", None)
            self.warnings.append(f"{dist.name}: {rel} kept as data ({message}, line {line})")
            if names[0][0][1]:  # CPython cannot import this package either: no namespace stand-in
                self.broken.add(rel.rpartition("/")[0])
            return False
        if dist.name == self.project:  # the app's own files; a dependency's stay silent
            self.warnings += [f"{self._shown(rel)}:{line}: {text}"
                              for line, text in result.warnings]  # fmt: skip
        if hints:
            self.reads += [(dist.name, rel, line) for line in result.reads]
        if hints and self.hints:
            for library in result.libraries:
                self.system.may_load.setdefault(library, set()).add(rel)
        source = None
        if result.source is not None:
            source = self._kept(result.source, result.source_digest)
        code_blob = self._kept(result.code, result.code_digest)
        added = False
        for (name, is_package), entry in names:
            if self._prevails(name, (entry, not is_package, len(self.suffixes)), dist.name):
                kind = ModuleKind.PACKAGE if is_package else ModuleKind.MODULE
                self.modules[name] = Module(name, rel, kind, code=code_blob, source=source,
                                            dist=dist.name)  # fmt: skip
                added = True
        if not added:
            return False
        if source is not None:
            self.files[rel] = File(rel, FileKind.SOURCE, source, dist=dist.name)
        self.owners[rel] = dist.name
        return True

    def _reads_wanted(self, rel: str) -> bool:
        """Whether the reads next to ``__file__`` of this module are looked for: with --hints,
        and always in the project's scripts (add_hints)."""
        return self.hints or rel.startswith(f"{ROOT}/")

    def _shown(self, rel: str) -> str:
        """A module of the scripts root as the project names its file."""
        if not rel.startswith(f"{ROOT}/"):
            return rel
        return self.origins.get(rel, rel.removeprefix(f"{ROOT}/"))

    def _compiled(self, dist: InstalledDist, rel: str, path: Path, hints: bool) -> Compiled:
        if self.compiler is not None:
            return self.compiler.get((dist.name, rel), path, rel, hints)
        return compiled(path.read_bytes(), rel, self.level, self.include_source, hints)

    def module_jobs(self, dists: list[InstalledDist]) -> list[Job]:
        """The modules add_dist will compile, in the order it reaches them (add_dist and
        _add_path's tests, without their effects): for the Compiler, ahead."""
        jobs = []
        for dist in sorted(dists, key=lambda d: d.name):
            info = dist.dist_info
            for line in dist.record:
                rel = _record_path(line)
                if not rel.endswith(".py") or not _packaged(rel) or rel.startswith(f"{info}/"):
                    continue
                path = dist.site / rel
                if not self._names(rel, module_name) or not path.is_file():
                    continue
                if path.stat().st_size > _IN_MEMORY:
                    continue
                tests = _TEST_DIRECTORIES.intersection(rel.split("/")[:-1])
                jobs.append(Job((dist.name, rel), path, rel, self._reads_wanted(rel) and not tests))
        return jobs

    def _prevails(self, name: str, rank: tuple[int, bool, int], dist: str) -> bool:
        """Record ``name``'s rank if it beats the module already registered under that name."""
        if name in self.runtime_modules and dist != self.project:
            # never imported, as in a venv, where the stdlib comes before site-packages
            # (gnureadline's readline.py, for Pythons without a readline module)
            self.warnings.append(f"{dist}: {name} is shadowed by the runtime's {name}; "
                                 "packaged as a file, not as a module")  # fmt: skip
            return False
        existing = self.modules.get(name)
        if existing is not None:
            compiled = min(self.ranks[name][2], rank[2]) < len(self.suffixes)
            if existing.dist != dist or not compiled:  # a dist's compiled builds and fallback
                self.warnings.append(f"{name} is provided by {existing.dist} and {dist}")
            if self.ranks[name] <= rank:
                return False
            displaced = self.files.get(existing.path)
            still_used = any(m.path == existing.path and n != name for n, m in self.modules.items())
            if displaced is not None and displaced.kind is FileKind.SOURCE and not still_used:
                self.files[existing.path] = replace(displaced, kind=FileKind.RESOURCE)
        self.ranks[name] = rank
        return True

    def _kept(self, blob: Blob, digest: bytes) -> Blob:
        """``blob`` (its content's ``tree_digest``): its bytes go to the spill if there is one
        (D38), the digest known."""
        return blob if self.spill is None else self.spill.keep(replace(blob, known=digest))

    def _add_native(self, dist: InstalledDist, rel: str, path: Path) -> None:
        self.natives[rel] = path
        self.owners[rel] = dist.name
        for (name, is_package, rank), entry in self._names(
            rel, lambda tail: extension_name(tail, self.suffixes)
        ):
            if self._prevails(name, (entry, not is_package, rank), dist.name):
                self.modules[name] = Module(name, rel, ModuleKind.EXTENSION, file=rel,
                                            dist=dist.name)  # fmt: skip

    def add_large(self) -> None:
        """The files too large to read whole (D38), compressed together from their files, their
        pieces on up to 8 threads (D39)."""
        if not self.large or self.spill is None:
            return
        paths = {rel: path for rel, (path, _, _, _) in self.large.items()}
        blobs = self.spill.compress_files(paths, level=self.level)
        for rel, (_, kind, dist, mode) in self.large.items():
            self.files[rel] = File(rel, kind, blobs[rel], mode=mode, dist=dist)

    def add_natives(self, allowed: frozenset[str]) -> None:
        """NATIVE_EXT files (backing an extension module) and NATIVE_LIB files, with closures."""
        extensions = {m.path for m in self.modules.values() if m.kind is ModuleKind.EXTENSION}
        self.executables = self._executables()
        try:
            needs = closures(self.natives, extensions, allowed=allowed, warn=self.warnings.append,
                             system=self.system, executables=self.executables)  # fmt: skip
        except NativeError as exc:
            raise ClassifyError(str(exc)) from exc
        self.reached = {member for members in needs.values() for member in members}
        known = {PurePosixPath(rel).name for rel in self.natives} | set(self.system.needed_by)
        for library in known & set(self.system.may_load):  # bundled or needed: not a guess
            del self.system.may_load[library]
        blobs = _native_blobs(self.natives, self.level, self.xz_natives, self.spill)
        for rel in self.natives:
            kind = FileKind.NATIVE_EXT if rel in extensions else FileKind.NATIVE_LIB
            self.files[rel] = File(rel, kind, blobs[rel], mode=_NATIVE_MODE,
                                   dist=self.owners[rel], closure=needs.get(rel, ()))  # fmt: skip

    def _executables(self) -> dict[str, Path]:
        """The ELF files kept as data that are programs, not libraries or other data."""
        found = {}
        for rel, path in self.elf_resources.items():
            with opened(path) as data:
                try:
                    program = is_executable(data)
                except ElfError:
                    continue  # not an ELF64 file of this host's machine: data to us
            if program:
                found[rel] = path
        return found

    def _add_file(
        self, dist: InstalledDist, rel: str, kind: FileKind, data: bytes, mode: int = 0o644
    ) -> None:
        """A file read whole, compressed on up to 8 threads (D39): in its place once done."""
        self.owners[rel] = dist.name
        future = None if self.compiler is None else self.compiler.compress(data)
        if future is None:  # no workers: threads of the build's own
            if self._compressor is None:
                self._compressor = ThreadPoolExecutor(THREADS, initializer=pooled_thread)
            future = self._compressor.submit(compressed, data, self.level)
        self._compressing.append((rel, kind, dist.name, mode, len(data), future))
        self._compressing_bytes += len(data)
        while self._compressing_bytes > _COMPRESSING:  # bounded memory (D38)
            self._file_done()

    def _file_done(self) -> None:
        rel, kind, dist, mode, size, future = self._compressing.popleft()
        self._compressing_bytes -= size
        blob, digest = future.result()
        self.files[rel] = File(rel, kind, self._kept(blob, digest), mode=mode, dist=dist)

    def files_done(self) -> None:
        """Every file _add_file compressed, in its place."""
        while self._compressing:
            self._file_done()
        if self._compressor is not None:
            self._compressor.shutdown()
            self._compressor = None

    def mark_fs_packages(self, configured: frozenset[str], namespaces: list[Module]) -> None:
        """FS_REQUIRED: every file below these packages is extracted when they are imported."""
        known = {m.name: m for m in namespaces} | self.modules
        directories = []
        for name in sorted(configured | BUILTIN_FS_PACKAGES):
            module = known.get(name)
            if module is None or module.kind is not ModuleKind.PACKAGE:
                if name in configured:
                    problem = "not a package of" if module is None else "not a regular package in"
                    raise ClassifyError(f"fs-packages: {name!r} is {problem} the application")
                continue
            self.modules[name] = replace(module, flags=module.flags | ModuleFlag.FS_REQUIRED_ROOT)
            directories.append(module.path.rpartition("/")[0] + "/")
        self.fs_directories = tuple(directories)
        sources = {m.path: m for m in self.modules.values() if m.source is not None}
        for rel, file in list(self.files.items()):
            if rel.startswith(tuple(directories)) and file.kind not in NATIVE_KINDS:
                self._require_on_disk(rel, file, sources.get(rel))

    def _require_on_disk(self, rel: str, file: File, module: Module | None) -> None:
        executable = (self.sites[self.owners[rel]] / rel).stat().st_mode & 0o111
        blob = _digested(file.blob)
        self.files[rel] = replace(file, blob=blob, flags=file.flags | FileFlag.FS_REQUIRED,
                                  mode=0o755 if executable else 0o644)  # fmt: skip
        if module is not None:  # the module's source is the same blob
            self.modules[module.name] = replace(module, source=blob)

    def add_hints(self) -> None:
        """Warnings for code that probably needs its files on disk (fs-packages)."""
        self._hint_script_reads()
        if self.hints:
            self._hint_file_reads()
        self._hint_unused_natives()
        self._hint_executables()

    def _hint_script_reads(self) -> None:
        """The reads next to ``__file__`` in the project's scripts (decision 5.7(a), reversed
        2026-10-25: their ``__file__`` is next to the executable): the executable holds their
        Python files only, so a file they read there is the host's, deployed beside it."""
        for _, rel, line in sorted(self.reads):
            if rel.startswith(f"{ROOT}/"):
                self.warnings.append(
                    f"{self._shown(rel)}:{line}: reads a file next to its own __file__, which "
                    "is read next to the executable (the executable holds only the project's "
                    "Python files): deploy the file there, beside the binary"
                )

    def _hint_file_reads(self) -> None:
        # (directory, name) of regular packages; sorted, the outermost container comes first
        packages = sorted(
            (m.path.rpartition("/")[0] + "/", m.name)
            for m in self.modules.values() if m.kind is ModuleKind.PACKAGE
        )  # fmt: skip
        first: dict[str, str] = {}
        for _, rel, line in self.reads:
            if rel.startswith(self.fs_directories):
                continue
            package = next(
                (name for directory, name in packages if rel.startswith(directory)), None
            )
            if package is not None:
                first.setdefault(package, f"{rel}:{line}")
        if first:
            listed = ", ".join(f"{name} ({where})" for name, where in sorted(first.items()))
            self.warnings.append(
                "these packages read files next to __file__ (a heuristic); add one to "
                f"fs-packages only if it fails at run time: {listed}"
            )

    def _hint_unused_natives(self) -> None:
        unused: dict[str, list[str]] = {}
        for rel, path in sorted(self.natives.items()):
            if rel in self.reached or rel.startswith(self.fs_directories):
                continue
            with opened(path) as data:
                used = is_extension_module(rel, data)
            if not used:
                unused.setdefault(self.owners[rel], []).append(rel)
        for dist, paths in sorted(unused.items()):
            named = self._named_by_python(paths)
            if named:
                self.warnings.append(Uncounted(
                    f"{dist}: {', '.join(named[:3])}{' ...' if len(named) > 3 else ''} loaded by "
                    f"no extension module but named by its Python code; {self._remedy(named)}"
                ))  # fmt: skip

    def _hint_executables(self) -> None:
        for rel in sorted(self.executables):
            if rel.startswith(self.fs_directories):
                continue  # extracted when its package is imported
            self.warnings.append(Uncounted(
                f"{self.owners[rel]}: {rel} is an executable; "
                f"{self._executable_remedy(rel)}"
            ))  # fmt: skip

    def _executable_remedy(self, rel: str) -> str:
        """What to do if the package runs it from a path built from ``__file__``: a path from
        importlib.resources needs nothing (D27), and fs-packages takes only regular packages."""
        top = rel.partition("/")[0]
        module = self.modules.get(top)
        if module is not None and module.kind is ModuleKind.PACKAGE:
            where = f'add "{top}" to fs-packages'
        else:
            where = "no fs-packages value covers it (it is not inside a regular package)"
        return f"if the package runs it from a path built from __file__, {where}"

    def _remedy(self, libraries: list[str]) -> str:
        """What to do if the code loads them itself (ctypes): fs-packages takes only regular
        packages, so name the one holding them, or say that none does."""
        tops = {rel.partition("/")[0] for rel in libraries}
        packages = sorted(
            top for top in tops if top in self.modules
            and self.modules[top].kind is ModuleKind.PACKAGE
        )  # fmt: skip
        if len(packages) == len(tops):
            listed = ", ".join(f'"{package}"' for package in packages)
            return f"if it loads them itself (ctypes), add {listed} to fs-packages"
        return ("if it loads them itself (ctypes), no fs-packages value covers them "
                "(they are not inside a regular package)")

    def _named_by_python(self, libraries: list[str]) -> list[str]:
        """The libraries some packaged .py file names as a word, as ``ctypes.CDLL(...)`` would
        (the loading package may be another distribution, ``foo`` loading ``foo-libs``). One
        nothing names is simply unused from Python (onnxruntime's C API library)."""
        patterns = {
            rel: re.compile(rb"(?<![\w.-])" + re.escape(_library_stem(rel)) + rb"(?![\w-])")
            for rel in libraries
        }
        named: set[str] = set()
        for rel, owner in sorted(self.owners.items()):
            if not rel.endswith(".py") or len(named) == len(patterns):
                continue
            if (self.sites[owner] / rel).stat().st_size > _IN_MEMORY:  # data, not code (D38)
                continue
            source = (self.sites[owner] / rel).read_bytes()
            named.update(lib for lib, pattern in patterns.items() if pattern.search(source))
        return sorted(named)

    def glibc(self) -> tuple[tuple[int, int], str] | None:
        """The newest glibc a packaged native file needs, and that file."""
        found = []
        for rel, path in sorted(self.natives.items()):
            try:
                with opened(path) as data:
                    requirement = glibc_requirement(data)
            except ElfError:
                continue  # an unreadable version table: the requirement stays a lower bound
            if requirement is not None:
                found.append((requirement, rel))
        return max(found, default=None)

    def namespaces(self) -> list[Module]:
        """Implicit namespace packages: identifier directories without __init__.py, named
        relative to site-packages and then to each .pth root (as sys.path orders them)."""
        packages = {
            m.path.rpartition("/")[0] for m in self.modules.values() if m.kind is ModuleKind.PACKAGE
        }
        found: dict[str, Module] = {}
        for base in ["", *(root + "/" for root in self.roots)]:
            for rel, owner in sorted(self.owners.items()):
                if not rel.startswith(base):
                    continue
                parts = PurePosixPath(rel[len(base) :]).parts[:-1]
                for depth in range(1, len(parts) + 1):
                    directory = base + "/".join(parts[:depth])
                    if not all(p.isidentifier() for p in parts[:depth]) or directory in self.broken:
                        break
                    name = ".".join(parts[:depth])
                    if directory not in packages and name not in self.modules and name not in found:
                        found[name] = Module(name, directory, ModuleKind.NAMESPACE, dist=owner)
        return list(found.values())


def classify(  # noqa: PLR0913  the build options, keyword-only
    dists: list[InstalledDist],
    *,
    extension_suffixes: tuple[str, ...],
    include_source: bool,
    allowed_system_libs: frozenset[str] = frozenset(),
    fs_packages: frozenset[str] = frozenset(),
    hints: bool = False,
    level: int = ZSTD_LEVEL,
    xz_natives: bool = False,
    spill: Spill | None = None,
    runtime_modules: frozenset[str] = frozenset(),
    project: str | None = None,
    origins: Mapping[str, str] | None = None,
) -> Content:
    """With ``spill``, no file's bytes stay in memory: they are compressed into it, the
    largest a piece at a time from their files (D38); the files must outlive the build.
    A module in ``runtime_modules`` is kept as a file, unless ``project`` installs it."""
    collector = _Collector(extension_suffixes, include_source, hints=hints, level=level,
                           xz_natives=xz_natives, spill=spill, runtime_modules=runtime_modules,
                           project=project, origins=origins)  # fmt: skip
    collector.read_pth_files(dists)
    collector.compiler = Compiler(collector.module_jobs(dists), level=level,
                                  include_source=include_source)  # fmt: skip
    try:
        for dist in sorted(dists, key=lambda d: d.name):
            collector.add_dist(dist)
        collector.files_done()
    finally:
        collector.compiler.close()
        collector.compiler = None
    collector.add_large()
    collector.add_natives(allowed_system_libs)
    namespaces = collector.namespaces()
    collector.mark_fs_packages(fs_packages, namespaces)
    collector.add_hints()
    modules = [*collector.modules.values(), *namespaces]
    return Content(
        modules=tuple(sorted(modules, key=lambda m: m.name)),
        files=tuple(sorted(collector.files.values(), key=lambda f: f.path)),
        dists=tuple(
            Dist(d.name, d.dist_info, d.version) for d in sorted(dists, key=lambda d: d.name)
        ),
        warnings=tuple(collector.warnings),
        glibc=collector.glibc(),
        system=collector.system,
    )
