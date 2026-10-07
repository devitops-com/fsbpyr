"""RECORD entries -> payload modules, files and namespace packages (plan §3.6 steps 6-7)."""

import csv
import hashlib
import posixpath
import re
import warnings
from collections.abc import Callable
from compression import zstd
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from . import bytecode
from .closure import InstalledDist
from .elf import ElfError, glibc_requirement, is_elf
from .natives import NativeError, closures
from .payload.format import (
    MAX_MEMORY_BLOB,
    NATIVE_KINDS,
    Codec,
    FileFlag,
    FileKind,
    ModuleFlag,
    ModuleKind,
    is_module_part,
)
from .payload.spec import Blob, Dist, File, Module
from .scan import file_relative_reads

# Written by installers, not by the wheel: they embed paths, timestamps or the installer's name,
# so packaging them would make builds machine-specific and non-reproducible.
INSTALLER_FILES = frozenset({
    "INSTALLER", "REQUESTED", "direct_url.json", "uv_cache.json", "RECORD.jws", "RECORD.p7s",
})  # fmt: skip


_SHARED_OBJECT = re.compile(r"\.so(\.\d+)*$")
_TEST_DIRECTORIES = frozenset({"tests", "test"})
# .pth files whose code only re-creates what PEP 420 namespaces (and the payload) provide
_REDUNDANT_PTH = re.compile(r"(-nspkg|^distutils-precedence)\.pth$")
_NATIVE_MODE = 0o755  # fixed: the installer's umask must not leak into the build
# Packages that need their data files on disk, whatever the configuration says (plan §3.5).
# Each one was verified to fail without (Phase 6b, fixture datafiles; litellm with aiagent),
# except certifi, which hands out the path of cacert.pem: otherwise a temporary copy made again
# in every process.
BUILTIN_FS_PACKAGES = frozenset({"babel", "botocore", "certifi", "litellm", "matplotlib"})


def _digested(blob: Blob) -> Blob:
    data = zstd.decompress(blob.stored) if blob.codec is Codec.ZSTD else blob.stored
    return replace(blob, sha256=hashlib.sha256(data).digest())


class ClassifyError(RuntimeError):
    """The installed files cannot be packaged (yet)."""


@dataclass(frozen=True, slots=True)
class Content:
    modules: tuple[Module, ...]
    files: tuple[File, ...]
    dists: tuple[Dist, ...]
    warnings: tuple[str, ...]
    glibc: tuple[tuple[int, int], str] | None = None  # newest need of a native file, and the file


def module_name(rel: str) -> tuple[str, bool] | None:
    """Dotted name and is-package flag of a site-relative .py path, if importable. A module
    file's stem may contain hyphens: ``importlib.import_module`` reaches it (rich's tables).
    Like any module, such a file is kept as bytecode only under ``--no-source``."""
    parts = list(PurePosixPath(rel).with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    if not parts or not all(part.isidentifier() for part in parts[:-1]):
        return None
    if not (parts[-1].isidentifier() or (not is_package and is_module_part(parts[-1]))):
        return None
    return ".".join(parts), is_package


def extension_name(rel: str, suffixes: tuple[str, ...]) -> tuple[str, bool, int] | None:
    """Dotted name, is-package flag (``__init__`` compiled by Cython and the like) and suffix
    rank of an extension module file, if importable."""
    for rank, suffix in enumerate(suffixes):
        if rel.endswith(suffix):
            parts = rel[: -len(suffix)].split("/")
            is_package = len(parts) > 1 and parts[-1] == "__init__"
            if is_package:
                parts.pop()
            if all(part.isidentifier() for part in parts):
                return ".".join(parts), is_package, rank
    return None


def _record_path(line: str) -> str:
    """First CSV field of a RECORD line (paths containing commas are quoted)."""
    return next(csv.reader([line]), [""])[0]


def compile_module(source: bytes, rel: str) -> bytes:
    """Canonical bytecode, independent of the build interpreter's -O / PYTHONOPTIMIZE (D15)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        code = compile(source, rel, "exec", dont_inherit=True, optimize=0)
    return bytecode.dumps(code)


def _packaged(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return bool(parts) and not (
        rel.startswith("/") or ".." in parts or "__pycache__" in parts or rel.endswith(".pyc")
    )


class _Collector:
    def __init__(self, extension_suffixes: tuple[str, ...], include_source: bool) -> None:
        self.suffixes = extension_suffixes
        self.include_source = include_source
        self.modules: dict[str, Module] = {}
        self.files: dict[str, File] = {}
        self.owners: dict[str, str] = {}  # path -> dist
        self.natives: dict[str, bytes] = {}  # ELF shared objects, path -> content
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
        for line in dist.record:
            rel = _record_path(line)
            if rel.startswith(f"{info}/") and rel[len(info) + 1 :] in INSTALLER_FILES:
                continue
            if not _packaged(rel):  # scripts (staging shebangs), bytecode caches (mtimes)
                continue
            record_lines.append(line)
            if rel != f"{info}/RECORD":
                self._add_path(dist, rel)
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
        data = path.read_bytes()
        if rel.endswith(self.suffixes) or _SHARED_OBJECT.search(rel):
            if is_elf(data):
                self._add_native(dist, rel, data)
                return
            self.warnings.append(f"{dist.name}: {rel} kept as data (not an ELF shared object)")
        names = self._names(rel, module_name) if rel.endswith(".py") else []
        if names and self._add_module(dist, rel, names, data):
            return
        is_metadata = rel.startswith(f"{dist.dist_info}/")
        self._add_file(dist, rel, FileKind.METADATA if is_metadata else FileKind.RESOURCE, data)

    def _add_module(
        self, dist: InstalledDist, rel: str, names: list[tuple[tuple[str, bool], int]], data: bytes
    ) -> bool:
        try:
            code = compile_module(data, rel)
        except SyntaxError as exc:
            self.warnings.append(f"{dist.name}: {rel} kept as data ({exc.msg}, line {exc.lineno})")
            if names[0][0][1]:  # CPython cannot import this package either: no namespace stand-in
                self.broken.add(rel.rpartition("/")[0])
            return False
        if not _TEST_DIRECTORIES.intersection(rel.split("/")[:-1]):  # tests are never imported
            self.reads += [(dist.name, rel, line) for line in file_relative_reads(data, rel)]
        source = Blob.zstd(data) if self.include_source else None
        added = False
        for (name, is_package), entry in names:
            if self._prevails(name, (entry, not is_package, len(self.suffixes)), dist.name):
                kind = ModuleKind.PACKAGE if is_package else ModuleKind.MODULE
                self.modules[name] = Module(name, rel, kind, code=Blob.code(code),
                                            source=source, dist=dist.name)  # fmt: skip
                added = True
        if not added:
            return False
        if source is not None:
            self.files[rel] = File(rel, FileKind.SOURCE, source, dist=dist.name)
        self.owners[rel] = dist.name
        return True

    def _prevails(self, name: str, rank: tuple[int, bool, int], dist: str) -> bool:
        """Record ``name``'s rank if it beats the module already registered under that name."""
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

    def _add_native(self, dist: InstalledDist, rel: str, data: bytes) -> None:
        self.natives[rel] = data
        self.owners[rel] = dist.name
        for (name, is_package, rank), entry in self._names(
            rel, lambda tail: extension_name(tail, self.suffixes)
        ):
            if self._prevails(name, (entry, not is_package, rank), dist.name):
                self.modules[name] = Module(name, rel, ModuleKind.EXTENSION, file=rel,
                                            dist=dist.name)  # fmt: skip

    def add_natives(self, allowed: frozenset[str]) -> None:
        """NATIVE_EXT files (backing an extension module) and NATIVE_LIB files, with closures."""
        extensions = {m.path for m in self.modules.values() if m.kind is ModuleKind.EXTENSION}
        try:
            needs = closures(self.natives, extensions, allowed=allowed)
        except NativeError as exc:
            raise ClassifyError(str(exc)) from exc
        self.reached = {member for members in needs.values() for member in members}
        for rel, data in self.natives.items():
            if len(data) > MAX_MEMORY_BLOB:  # extraction reads a blob whole (no streaming yet)
                raise ClassifyError(f"{rel} is too large to bundle ({len(data)} bytes)")
            kind = FileKind.NATIVE_EXT if rel in extensions else FileKind.NATIVE_LIB
            self.files[rel] = File(rel, kind, Blob.zstd(data, digest=True), mode=_NATIVE_MODE,
                                   dist=self.owners[rel], closure=needs.get(rel, ()))  # fmt: skip

    def _add_file(self, dist: InstalledDist, rel: str, kind: FileKind, data: bytes) -> None:
        self.files[rel] = File(rel, kind, Blob.zstd(data), dist=dist.name)
        self.owners[rel] = dist.name

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
        self._hint_file_reads()
        self._hint_unused_natives()

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
        for rel, data in sorted(self.natives.items()):
            stem = rel.rpartition("/")[2].partition(".")[0]
            used = (
                rel in self.reached
                or rel.startswith(self.fs_directories)
                or b"PyInit_" + stem.encode() + b"\0" in data  # what CPython would call
            )
            if not used:
                unused.setdefault(self.owners[rel], []).append(rel)
        for dist, paths in sorted(unused.items()):
            self.warnings.append(
                f"{dist}: {', '.join(paths[:3])}{' ...' if len(paths) > 3 else ''} loaded by no "
                "extension module; if the package loads them itself (ctypes), add it to "
                "fs-packages"
            )

    def glibc(self) -> tuple[tuple[int, int], str] | None:
        """The newest glibc a packaged native file needs, and that file."""
        found = []
        for rel, data in sorted(self.natives.items()):
            try:
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


def classify(
    dists: list[InstalledDist],
    *,
    extension_suffixes: tuple[str, ...],
    include_source: bool,
    allowed_system_libs: frozenset[str] = frozenset(),
    fs_packages: frozenset[str] = frozenset(),
) -> Content:
    collector = _Collector(extension_suffixes, include_source)
    collector.read_pth_files(dists)
    for dist in sorted(dists, key=lambda d: d.name):
        collector.add_dist(dist)
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
    )
