"""Stdlib selection, compilation with the target interpreter, and startup-module detection."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..bytecode import canonicalize
from ..payload.format import FileKind, ModuleFlag, ModuleKind
from ..payload.spec import Blob, File, Module
from .pbs import PbsDist

EXCLUDED_DIRS = frozenset({
    "test", "idlelib", "tkinter", "turtledemo", "ensurepip", "__pycache__", "site-packages",
    "lib-dynload", "__phello__",
})  # fmt: skip
_FAILED = 0xFFFFFFFF
_ZSTD_LEVEL = 3

# Runs under the PBS interpreter (-I -S): compile each listed file and frame the marshal data.
_COMPILE_WORKER = """
import marshal, sys
root, out = sys.argv[1], sys.stdout.buffer
for rel in sys.stdin.read().split("\\0"):
    with open(root + "/" + rel, "rb") as handle:
        source = handle.read()
    try:
        data = marshal.dumps(compile(source, rel, "exec", dont_inherit=True))
    except SyntaxError:
        out.write(b"\\xff\\xff\\xff\\xff")
        continue
    out.write(len(data).to_bytes(4, "little"))
    out.write(data)
"""
_PROBE = "import sys, _imp; print(*sys.builtin_module_names, *_imp._frozen_module_names())"


class StdlibError(RuntimeError):
    """The stdlib could not be compiled or analysed."""


@dataclass(frozen=True, slots=True)
class StdlibTree:
    modules: tuple[tuple[str, str, bool], ...]  # (name, relative path, is_package), by name
    resources: tuple[str, ...]  # relative paths of non-Python files


def _module_name(rel: Path) -> tuple[str, bool] | None:
    parts = list(rel.with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    if not parts or not all(part.isidentifier() for part in parts):
        return None
    return ".".join(parts), is_package


def collect(stdlib: Path) -> StdlibTree:
    modules, resources = [], []
    for path in stdlib.rglob("*"):
        rel = path.relative_to(stdlib)
        directories = rel.parts[:-1]
        if not path.is_file() or any(
            part in EXCLUDED_DIRS or part.startswith("config-") for part in directories
        ):
            continue
        if path.suffix != ".py":
            resources.append(rel.as_posix())
        elif (named := _module_name(rel)) is not None:
            modules.append((named[0], rel.as_posix(), named[1]))
    return StdlibTree(tuple(sorted(modules)), tuple(sorted(resources)))


def parse_importtime(stderr: str) -> list[str]:
    names = []
    for line in stderr.splitlines():
        if line.startswith("import time:") and not line.rstrip().endswith("imported package"):
            names.append(line.rsplit("|", 1)[1].strip())
    return names


def decode_compiled(stream: bytes, rels: list[str]) -> dict[str, bytes | None]:
    result: dict[str, bytes | None] = {}
    pos = 0
    for rel in rels:
        if pos + 4 > len(stream):
            raise ValueError("truncated compile worker output")
        size = int.from_bytes(stream[pos : pos + 4], "little")
        pos += 4
        if size == _FAILED:
            result[rel] = None
            continue
        if pos + size > len(stream):
            raise ValueError("truncated compile worker output")
        result[rel] = stream[pos : pos + size]
        pos += size
    if pos != len(stream):
        raise ValueError("unexpected trailing compile worker output")
    return result


def _run(command: list[str], stdin: str = "") -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(command, input=stdin.encode(), capture_output=True, check=False)
    if result.returncode != 0:
        raise StdlibError(f"{command[0]} failed: {result.stderr.decode(errors='replace')[-2000:]}")
    return result


def compile_files(interpreter: Path, root: Path, rels: list[str]) -> dict[str, bytes | None]:
    """Marshalled code compiled by ``interpreter`` (the exact target CPython)."""
    result = _run([str(interpreter), "-I", "-S", "-c", _COMPILE_WORKER, str(root)], "\0".join(rels))
    return decode_compiled(result.stdout, rels)


def startup_names(interpreter: Path) -> list[str]:
    """Modules CPython imports during init that are neither built-in nor frozen (S2)."""
    timeline = _run([str(interpreter), "-X", "importtime", "-X", "utf8", "-I", "-S", "-c", "pass"])
    known = set(_run([str(interpreter), "-I", "-S", "-c", _PROBE]).stdout.decode().split())
    return [n for n in parse_importtime(timeline.stderr.decode()) if n not in known]


def fragment(dist: PbsDist) -> tuple[tuple[Module, ...], tuple[File, ...]]:
    """Stdlib modules (STARTUP ones uncompressed), sources and resources as payload specs."""
    tree = collect(dist.stdlib)
    compiled = compile_files(dist.interpreter, dist.stdlib, [rel for _, rel, _ in tree.modules])
    startup = set(startup_names(dist.interpreter))
    modules: list[Module] = []
    files: list[File] = []
    for name, rel, is_package in tree.modules:
        source = (dist.stdlib / rel).read_bytes()
        raw = compiled[rel]
        if raw is None:  # not valid Python for this version: ship it as a plain resource
            files.append(File(rel, FileKind.RESOURCE, Blob.zstd(source, level=_ZSTD_LEVEL)))
            continue
        code = canonicalize(raw)
        is_startup = name in startup
        source_blob = Blob.zstd(source, level=_ZSTD_LEVEL)
        modules.append(Module(
            name,
            rel,
            ModuleKind.PACKAGE if is_package else ModuleKind.MODULE,
            code=Blob.code(code, level=_ZSTD_LEVEL),
            source=source_blob,
            flags=ModuleFlag.STARTUP if is_startup else ModuleFlag(0),
        ))  # fmt: skip
        files.append(File(rel, FileKind.SOURCE, source_blob))
    for rel in tree.resources:
        data = (dist.stdlib / rel).read_bytes()
        files.append(File(rel, FileKind.RESOURCE, Blob.zstd(data, level=_ZSTD_LEVEL)))
    missing = startup - {m.name for m in modules}
    if missing:
        raise StdlibError(f"startup modules missing from the stdlib selection: {sorted(missing)}")
    return tuple(modules), tuple(files)
