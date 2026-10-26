"""Stdlib selection, compilation with the target interpreter, and startup-module detection."""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..bytecode import canonicalize
from ..compiler import POOL_MODULES, worker_pool
from ..payload.format import (
    THREADS,
    FileKind,
    ModuleFlag,
    ModuleKind,
    is_module_part,
    parallel,
)
from ..payload.spec import ZSTD_LEVEL, Blob, File, Module
from ..text import tail_lines
from .pbs import PbsDist

EXCLUDED_DIRS = frozenset({
    "test", "idlelib", "tkinter", "turtledemo", "ensurepip", "__pycache__", "site-packages",
    "lib-dynload", "__phello__",
})  # fmt: skip
_FAILED = 0xFFFFFFFF
_ERROR_TAIL = 2000

# Runs under the PBS interpreter (-I -S): compile each listed file and frame the marshal data.
# stdin: NUL-separated pairs of (relative path, co_filename).
_COMPILE_WORKER = """
import marshal, sys
root, out = sys.argv[1], sys.stdout.buffer
fields = sys.stdin.read().split("\\0")
for rel, filename in zip(fields[::2], fields[1::2]):
    with open(root + "/" + rel, "rb") as handle:
        source = handle.read()
    try:
        data = marshal.dumps(compile(source, filename, "exec", dont_inherit=True, optimize=0))
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
    if not parts or not all(is_module_part(part) for part in parts):
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
        stderr = result.stderr.decode(errors="replace")
        raise StdlibError(f"{command[0]} failed: {tail_lines(stderr, _ERROR_TAIL)}")
    return result


def compile_files(
    interpreter: Path,
    root: Path,
    rels: list[str],
    filenames: dict[str, str] | None = None,
    *,
    warn: Callable[[str], None] | None = None,
) -> dict[str, bytes | None]:
    """Marshalled code compiled by ``interpreter`` (the exact target CPython).

    ``filenames`` overrides co_filename per path (default: the relative path itself). What a
    worker prints (the compiler's warnings) goes to ``warn``, a worker's at a time.
    """
    if not rels:
        return {}

    def chunk(part: list[str]) -> tuple[dict[str, bytes | None], str]:
        pairs = [field for rel in part for field in (rel, (filenames or {}).get(rel, rel))]
        result = _run(
            [str(interpreter), "-I", "-S", "-c", _COMPILE_WORKER, str(root)], "\0".join(pairs)
        )
        return decode_compiled(result.stdout, part), result.stderr.decode(errors="replace")

    parts = [rels[i::THREADS] for i in range(min(THREADS, len(rels)))]  # one process each (D39)
    compiled: dict[str, bytes | None] = {}
    for codes, printed in parallel(chunk, parts):
        compiled.update(codes)
        if warn is not None and printed.strip():
            warn(printed.rstrip())
    return {rel: compiled[rel] for rel in rels}  # in their order, whatever the number of cores


def startup_names(interpreter: Path) -> list[str]:
    """Modules CPython imports during init that are neither built-in nor frozen (S2)."""
    timeline = _run([str(interpreter), "-X", "importtime", "-X", "utf8", "-I", "-S", "-c", "pass"])
    known = set(_run([str(interpreter), "-I", "-S", "-c", _PROBE]).stdout.decode().split())
    return [n for n in parse_importtime(timeline.stderr.decode()) if n not in known]


def _finish(job: tuple[bytes, bytes | None]) -> tuple[Blob | None, Blob]:
    """A module's code (canonical, D15) and source blobs; no code if it did not compile."""
    source, raw = job
    code = None if raw is None else Blob.code(canonicalize(raw), level=ZSTD_LEVEL)
    return code, Blob.zstd(source, level=ZSTD_LEVEL)


def _finished(jobs: list[tuple[bytes, bytes | None]]) -> list[tuple[Blob | None, Blob]]:
    """_finish of each job, in order, on up to 8 worker processes (D39; the GIL keeps threads
    from canonicalizing): the same blobs whatever the number of cores."""
    if len(jobs) < POOL_MODULES or THREADS < 2:
        return [_finish(job) for job in jobs]
    with worker_pool() as pool:
        return list(pool.map(_finish, jobs, chunksize=16))


def _ignore(_: str) -> None:
    pass


def fragment(
    dist: PbsDist, *, warn: Callable[[str], None] = _ignore
) -> tuple[tuple[Module, ...], tuple[File, ...]]:
    """Stdlib modules (STARTUP ones uncompressed), sources and resources as payload specs; the
    compiler's warnings, and each module that does not compile, go to ``warn``."""
    tree = collect(dist.stdlib)
    startup = set(startup_names(dist.interpreter))
    # Frozen-style names (like CPython's own frozen modules): linecache never stats a relative path.
    filenames = {rel: f"<frozen {name}>" for name, rel, _ in tree.modules if name in startup}
    rels = [rel for _, rel, _ in tree.modules]
    compiled = compile_files(dist.interpreter, dist.stdlib, rels, filenames, warn=warn)
    modules: list[Module] = []
    files: list[File] = []
    jobs = [((dist.stdlib / rel).read_bytes(), compiled[rel]) for _, rel, _ in tree.modules]
    for (name, rel, is_package), (code_blob, source_blob) in zip(tree.modules, _finished(jobs),
                                                                 strict=True):  # fmt: skip
        if code_blob is None:  # not valid Python for this version: ship it as a plain resource
            warn(f"{rel}: not valid Python {dist.version}, kept as a plain file")
            files.append(File(rel, FileKind.RESOURCE, source_blob))
            continue
        modules.append(Module(
            name,
            rel,
            ModuleKind.PACKAGE if is_package else ModuleKind.MODULE,
            code=code_blob,
            source=source_blob,
            flags=ModuleFlag.STARTUP if name in startup else ModuleFlag(0),
        ))  # fmt: skip
        files.append(File(rel, FileKind.SOURCE, source_blob))
    for rel in tree.resources:
        data = (dist.stdlib / rel).read_bytes()
        files.append(File(rel, FileKind.RESOURCE, Blob.zstd(data, level=ZSTD_LEVEL)))
    missing = startup - {m.name for m in modules}
    if missing:
        raise StdlibError(f"startup modules missing from the stdlib selection: {sorted(missing)}")
    return tuple(modules), tuple(files)
