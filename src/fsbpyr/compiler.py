"""Modules compiled ahead of the classifier (D39): a build's modules are compiled,
canonicalized and compressed on up to 8 worker processes (the GIL keeps threads from compiling),
a window of them in flight, and handed to the classifier in the order it reaches them, so what it
registers, warns about and writes is the same whatever the number of cores; the files it reads
whole are compressed on the same workers, so that a build keeps to 8 cores. A build of a few
modules compiles them in its own process: starting workers would cost more. Workers are forked
(worker_pool); a program that builds from several threads gets them from a fork server, and its
main module must then do nothing more on import (``if __name__ == "__main__":``)."""

import multiprocessing
import os
import threading
import warnings
from collections import deque
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import bytecode
from .loose import ROOT
from .payload.format import THREADS, pooled_thread, tree_digest
from .payload.spec import ZSTD_LEVEL, Blob
from .scan import file_relative_reads, loaded_libraries

POOL_MODULES = 256  # fewer modules compile in the build's own process
_AHEAD = 4  # jobs in flight per worker: read ahead, never all at once (D38: bounded memory)
_AHEAD_BYTES = 64 << 20  # and at most this much of their sources (one at least)


def compile_module(source: bytes, rel: str) -> bytes:
    """Canonical bytecode, independent of the build interpreter's -O / PYTHONOPTIMIZE (D15)."""
    return compile_warned(source, rel)[0]


def compile_warned(source: bytes, rel: str) -> tuple[bytes, tuple[tuple[int, str], ...]]:
    """``compile_module``'s bytecode and the SyntaxWarnings and DeprecationWarnings compiling
    emitted, each as (line, "Category: message"); the warnings never change the bytecode."""
    # A copy: 3.12's pathlib interns the parts of a path, and marshal writes an interned filename
    # differently from the fresh string a worker process gets by pickle.
    filename = rel.encode().decode()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        code = compile(source, filename, "exec", dont_inherit=True, optimize=0)
    emitted = tuple((w.lineno, f"{w.category.__name__}: {w.message}") for w in caught
                    if issubclass(w.category, (SyntaxWarning, DeprecationWarning)))  # fmt: skip
    return bytecode.dumps(code), emitted


@dataclass(frozen=True, slots=True)
class Compiled:
    """A module's code and source blobs and their contents' digests (``tree_digest``), the
    lines that read next to ``__file__``, the system libraries it opens by name
    (``loaded_libraries``), the warnings compiling emitted (``compile_warned``); or why it does
    not compile (a SyntaxError's message and line)."""

    code: Blob | None
    code_digest: bytes = b""
    source: Blob | None = None
    source_digest: bytes = b""
    reads: tuple[int, ...] = ()
    libraries: tuple[str, ...] = ()
    warnings: tuple[tuple[int, str], ...] = ()
    error: tuple[str, int | None] | None = None


def compiled(  # noqa: PLR0913, PLR0917  a job's fields, as _work unpacks them
    data: bytes, rel: str, level: int, include_source: bool, hints: bool, main: bool = False
) -> Compiled:
    try:
        code, emitted = compile_warned(data, rel)
    except SyntaxError as exc:
        return Compiled(None, error=(exc.msg, exc.lineno))
    source = Blob.zstd(data, level=level) if include_source else None
    # a command's main-guard code runs; that of any module only imported never does
    script = rel.startswith(f"{ROOT}/")
    reads = tuple(file_relative_reads(data, rel, script=script, main=main)) if hints else ()
    libraries = tuple(loaded_libraries(data, rel)) if hints else ()
    return Compiled(Blob.code(code, level=level), tree_digest(code), source,
                    tree_digest(data) if include_source else b"", reads, libraries,
                    emitted)  # fmt: skip


def worker_pool() -> ProcessPoolExecutor:
    """Up to THREADS worker processes (D39): forked while the process runs one thread, so that
    no main module is imported again (a script read from stdin has none) and no other thread
    holds a lock; else started by a fork server, which imports the main module."""
    method = "fork" if _one_thread() else "forkserver"
    context = multiprocessing.get_context(method)
    return ProcessPoolExecutor(THREADS, mp_context=context, initializer=pooled_thread)


def _one_thread() -> bool:
    """Whether this process runs one thread. /proc counts the threads `threading` does not
    know (started by C code, or pytest-xdist's), as CPython's own fork warning does."""
    try:
        return len(os.listdir("/proc/self/task")) == 1
    except OSError:  # no /proc
        return threading.active_count() == 1


def _work(job: tuple[bytes, str, int, bool, bool, bool]) -> Compiled:
    return compiled(*job)


def compressed(data: bytes, level: int) -> tuple[Blob, bytes]:
    """A file read whole, as Blob.zstd makes it, and its content's ``tree_digest``."""
    return Blob.zstd(data, level=level), tree_digest(data)


@dataclass(frozen=True, slots=True)
class Job:
    key: tuple[str, str]  # (dist, path): what the classifier asks for
    path: Path
    rel: str
    hints: bool  # its reads next to __file__ are wanted
    main: bool = False  # it runs as __main__ (a command): its main guard runs


class Compiler:
    """The modules of ``jobs``, compiled ahead on worker processes, given in their order; a job
    the classifier passes over is dropped, one it asks for that was not ahead is compiled here."""

    def __init__(self, jobs: list[Job], *, level: int = ZSTD_LEVEL, include_source: bool) -> None:
        self.level, self.include_source = level, include_source
        self._pool: ProcessPoolExecutor | None = None
        if len(jobs) >= POOL_MODULES and THREADS > 1:
            self._pool = worker_pool()
        self._jobs = deque(jobs if self._pool is not None else ())
        self._ahead = {job.key for job in self._jobs}
        self._pending: deque[tuple[tuple[str, str], int, Future[Compiled]]] = deque()
        self._pending_bytes = 0
        self._fill()

    def _fill(self) -> None:
        pool = self._pool
        while pool is not None and self._jobs and len(self._pending) < _AHEAD * THREADS:
            if self._pending and self._pending_bytes >= _AHEAD_BYTES:
                return
            job = self._jobs.popleft()
            data = job.path.read_bytes()
            work = (data, job.rel, self.level, self.include_source, job.hints, job.main)
            self._pending.append((job.key, len(data), pool.submit(_work, work)))
            self._pending_bytes += len(data)

    def get(self, key: tuple[str, str], path: Path, rel: str, hints: bool,
            main: bool = False) -> Compiled:
        if key in self._ahead:
            while self._pending:
                ahead, size, future = self._pending.popleft()
                self._pending_bytes -= size
                self._ahead.discard(ahead)
                self._fill()
                if ahead == key:
                    return future.result()
                future.cancel()  # passed over (a path two dists install, say)
        return compiled(path.read_bytes(), rel, self.level, self.include_source, hints, main)

    def compress(self, data: bytes) -> Future[tuple[Blob, bytes]] | None:
        """``data`` compressed on a worker too, so that a build keeps to 8 cores; None without
        workers (the caller's threads do it)."""
        return None if self._pool is None else self._pool.submit(compressed, data, self.level)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(cancel_futures=True)
