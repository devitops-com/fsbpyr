"""RECORD entries -> payload modules, files and namespace packages (plan §3.6 steps 6-7)."""

import csv
import re
import warnings
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from . import bytecode
from .closure import InstalledDist
from .elf import is_elf
from .natives import NativeError, closures
from .payload.format import MAX_MEMORY_BLOB, FileKind, ModuleKind
from .payload.spec import Blob, Dist, File, Module

# Written by installers, not by the wheel: they embed paths, timestamps or the installer's name,
# so packaging them would make builds machine-specific and non-reproducible.
INSTALLER_FILES = frozenset({
    "INSTALLER", "REQUESTED", "direct_url.json", "uv_cache.json", "RECORD.jws", "RECORD.p7s",
})  # fmt: skip


_SHARED_OBJECT = re.compile(r"\.so(\.\d+)*$")
_NATIVE_MODE = 0o755  # fixed: the installer's umask must not leak into the build


class ClassifyError(RuntimeError):
    """The installed files cannot be packaged (yet)."""


@dataclass(frozen=True, slots=True)
class Content:
    modules: tuple[Module, ...]
    files: tuple[File, ...]
    dists: tuple[Dist, ...]
    warnings: tuple[str, ...]


def module_name(rel: str) -> tuple[str, bool] | None:
    """Dotted name and is-package flag of a site-relative .py path, if importable."""
    parts = list(PurePosixPath(rel).with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    if not parts or not all(part.isidentifier() for part in parts):
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
        # CPython's FileFinder order: a package directory before a module file, and within
        # each, extension suffixes in order before .py. Lower wins.
        self.ranks: dict[str, tuple[bool, int]] = {}  # module -> (not package, loader rank)
        self.warnings: list[str] = []
        self.broken: set[str] = set()  # package directories whose __init__.py does not compile

    def add_dist(self, dist: InstalledDist) -> None:
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
        if rel.endswith(".pth"):
            self.warnings.append(
                f"{dist.name}: {rel} ignored (.pth files do not run in executables)"
            )
            return
        data = path.read_bytes()
        if rel.endswith(self.suffixes) or _SHARED_OBJECT.search(rel):
            if is_elf(data):
                self._add_native(dist, rel, data)
                return
            self.warnings.append(f"{dist.name}: {rel} kept as data (not an ELF shared object)")
        named = module_name(rel) if rel.endswith(".py") else None
        if named is not None and self._add_module(dist, rel, named, data):
            return
        is_metadata = rel.startswith(f"{dist.dist_info}/")
        self._add_file(dist, rel, FileKind.METADATA if is_metadata else FileKind.RESOURCE, data)

    def _add_module(
        self, dist: InstalledDist, rel: str, named: tuple[str, bool], data: bytes
    ) -> bool:
        try:
            code = compile_module(data, rel)
        except SyntaxError as exc:
            self.warnings.append(f"{dist.name}: {rel} kept as data ({exc.msg}, line {exc.lineno})")
            if named[1]:  # CPython cannot import this package either: no namespace stand-in
                self.broken.add(rel.rpartition("/")[0])
            return False
        kind = ModuleKind.PACKAGE if named[1] else ModuleKind.MODULE
        if not self._prevails(named[0], (not named[1], len(self.suffixes)), dist.name):
            return False
        source = Blob.zstd(data) if self.include_source else None
        self.modules[named[0]] = Module(named[0], rel, kind, code=Blob.code(code),
                                        source=source, dist=dist.name)  # fmt: skip
        if source is not None:
            self.files[rel] = File(rel, FileKind.SOURCE, source, dist=dist.name)
        self.owners[rel] = dist.name
        return True

    def _prevails(self, name: str, rank: tuple[bool, int], dist: str) -> bool:
        """Record ``name``'s rank if it beats the module already registered under that name."""
        existing = self.modules.get(name)
        if existing is not None:
            self.warnings.append(f"{name} is provided by {existing.dist} and {dist}")
            if self.ranks[name] <= rank:
                return False
            displaced = self.files.get(existing.path)
            if displaced is not None and displaced.kind is FileKind.SOURCE:  # now plain data
                self.files[existing.path] = replace(displaced, kind=FileKind.RESOURCE)
        self.ranks[name] = rank
        return True

    def _add_native(self, dist: InstalledDist, rel: str, data: bytes) -> None:
        self.natives[rel] = data
        self.owners[rel] = dist.name
        named = extension_name(rel, self.suffixes)
        if named is not None and self._prevails(named[0], (not named[1], named[2]), dist.name):
            self.modules[named[0]] = Module(named[0], rel, ModuleKind.EXTENSION, file=rel,
                                            dist=dist.name)  # fmt: skip

    def add_natives(self, allowed: frozenset[str]) -> None:
        """NATIVE_EXT files (backing an extension module) and NATIVE_LIB files, with closures."""
        extensions = {m.path for m in self.modules.values() if m.kind is ModuleKind.EXTENSION}
        try:
            needs = closures(self.natives, extensions, allowed=allowed)
        except NativeError as exc:
            raise ClassifyError(str(exc)) from exc
        for rel, data in self.natives.items():
            if len(data) > MAX_MEMORY_BLOB:  # extraction reads a blob whole (no streaming yet)
                raise ClassifyError(f"{rel} is too large to bundle ({len(data)} bytes)")
            kind = FileKind.NATIVE_EXT if rel in extensions else FileKind.NATIVE_LIB
            self.files[rel] = File(rel, kind, Blob.zstd(data, digest=True), mode=_NATIVE_MODE,
                                   dist=self.owners[rel], closure=needs.get(rel, ()))  # fmt: skip

    def _add_file(self, dist: InstalledDist, rel: str, kind: FileKind, data: bytes) -> None:
        self.files[rel] = File(rel, kind, Blob.zstd(data), dist=dist.name)
        self.owners[rel] = dist.name

    def namespaces(self) -> list[Module]:
        """Implicit namespace packages: identifier directories without __init__.py."""
        packages = {
            m.path.rpartition("/")[0] for m in self.modules.values() if m.kind is ModuleKind.PACKAGE
        }
        found: dict[str, Module] = {}
        for rel, owner in sorted(self.owners.items()):
            parts = PurePosixPath(rel).parts[:-1]
            for depth in range(1, len(parts) + 1):
                directory = "/".join(parts[:depth])
                if not all(p.isidentifier() for p in parts[:depth]) or directory in self.broken:
                    break
                name = directory.replace("/", ".")
                if directory not in packages and name not in self.modules and name not in found:
                    found[name] = Module(name, directory, ModuleKind.NAMESPACE, dist=owner)
        return list(found.values())


def classify(
    dists: list[InstalledDist],
    *,
    extension_suffixes: tuple[str, ...],
    include_source: bool,
    allowed_system_libs: frozenset[str] = frozenset(),
) -> Content:
    collector = _Collector(extension_suffixes, include_source)
    for dist in sorted(dists, key=lambda d: d.name):
        collector.add_dist(dist)
    collector.add_natives(allowed_system_libs)
    modules = [*collector.modules.values(), *collector.namespaces()]
    return Content(
        modules=tuple(sorted(modules, key=lambda m: m.name)),
        files=tuple(sorted(collector.files.values(), key=lambda f: f.path)),
        dists=tuple(
            Dist(d.name, d.dist_info, d.version) for d in sorted(dists, key=lambda d: d.name)
        ),
        warnings=tuple(collector.warnings),
    )
