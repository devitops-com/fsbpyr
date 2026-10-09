"""Where a build keeps what it compressed (D38, Phase 8c): one temporary file next to the output,
unlinked at once and appended to, so that no blob's bytes stay in memory however much a build
packages, and nothing is left behind however the build ends. Blobs then hold Extents of it
(read through its descriptor), which the writer streams into the executable."""

import dataclasses
import hashlib
import os
import tempfile
import threading
from collections.abc import Iterator, Mapping
from compression import zstd
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import TracebackType
from typing import Self

from .format import (
    DIGEST_LEAF,
    XZ_PIECE,
    ZERO_LEAF,
    Codec,
    TreeHasher,
    file_digest,
    is_hole,
    tree_digest,
)
from .spec import EXTENT_PIECE, ZSTD_LEVEL, Blob, Extent, xz_stream, zstd_options

_XZ_BATCH = 256  # pieces submitted at once: the futures held do not grow with the input


class Spill:
    """A temporary file in ``directory``, unlinked at once (only its descriptor is left), closed
    with the Spill; appends from threads are each whole. Its Extents are read until it is."""

    def __init__(self, directory: Path) -> None:
        fd, name = tempfile.mkstemp(dir=directory, prefix=".fsbpyr-spill-")
        os.unlink(name)
        self._fd, self.path, self._size = fd, Path(name), 0  # path: for messages
        self._lock = threading.Lock()

    def add(self, data: bytes) -> Extent:
        """``data`` appended; its Extent."""
        with self._lock:
            offset = self._size
            self._size += len(data)
        written = 0
        while written < len(data):  # each append has its own place: no lock while writing
            written += os.pwrite(self._fd, data[written:], offset + written)
        return Extent(self.path, offset, len(data), tree_digest(data), self._fd)

    def appender(self) -> Appender:
        """For one blob written a piece at a time; the spill takes no other append meanwhile."""
        return Appender(self)

    def keep(self, blob: Blob, content: bytes | None = None) -> Blob:
        """``blob`` with its stored bytes (and its pieces') here instead of in memory, and the
        content's digest known if ``content`` is given (the bytes it was made from)."""
        if isinstance(blob.stored, Extent) or blob.pieces:
            pieces = tuple(self.keep(piece) for piece in blob.pieces)
            return dataclasses.replace(blob, pieces=pieces, known=_digest_of(content, blob.known))
        return dataclasses.replace(blob, stored=self.add(blob.stored),
                                   known=_digest_of(content, blob.known))  # fmt: skip

    def zstd_file(self, path: Path, *, level: int = ZSTD_LEVEL, digest: bool = False) -> Blob:
        """A file's content as one zstd frame with its content size and checksum, as Blob.zstd
        makes it of the bytes (fed a piece at a time, zstd cuts its blocks elsewhere, but the
        same file always gives the same bytes), compressed into the spill and hashed as it is
        read; the file's own bytes (an Extent of it) if that is not smaller. A piece at most
        is in memory."""
        with _File(path) as file:
            compressor = zstd.ZstdCompressor(options=zstd_options(level))
            compressor.set_pledged_input_size(file.size)
            root = hashlib.sha256()
            appender = self.appender()
            try:
                for piece, hole in file.pieces():
                    root.update(ZERO_LEAF if hole and len(piece) == DIGEST_LEAF else
                                hashlib.sha256(piece).digest())  # fmt: skip
                    appender.write(compressor.compress(piece))
                    if appender.size >= file.size:  # not smaller: stored raw
                        break
                else:
                    appender.write(compressor.flush(zstd.ZstdCompressor.FLUSH_FRAME))
            except BaseException:
                appender.abandon()
                raise
            if appender.size < file.size:
                tree = root.digest()
                return Blob(appender.finish(), file.size, Codec.ZSTD, tree if digest else None,
                            known=tree)  # fmt: skip
            appender.abandon()
            return file.raw(digest=digest)

    def xz_files(self, paths: Mapping[str, Path], *, threads: int) -> dict[str, Blob]:
        """Blob.xz of each file, with its digest: the pieces of every file compressed on
        ``threads`` threads, largest first, each read from its file and put here as it is
        done; a file whose streams are not smaller is its own bytes (D33, D38)."""
        sizes = {rel: os.stat(path).st_size for rel, path in paths.items()}

        def length(job: tuple[str, int]) -> int:
            return min(XZ_PIECE, sizes[job[0]] - job[1] * XZ_PIECE)

        every = [(rel, n) for rel, size in sizes.items() for n in range(-(-size // XZ_PIECE))]
        jobs = sorted(every, key=lambda job: (-length(job), job))
        done: dict[tuple[str, int], tuple[Extent, int, bytes]] = {}

        def compress(job: tuple[str, int]) -> tuple[Extent, int, bytes]:
            rel, number = job
            with _File(paths[rel]) as file:
                piece = file.read(number * XZ_PIECE, length(job))
            leaves = b"".join(hashlib.sha256(piece[start : start + DIGEST_LEAF]).digest()
                              for start in range(0, len(piece), DIGEST_LEAF))  # fmt: skip
            return self.add(xz_stream(piece)), len(piece), leaves

        with ThreadPoolExecutor(threads) as pool:
            for first in range(0, len(jobs), _XZ_BATCH):
                batch = jobs[first : first + _XZ_BATCH]
                done.update(zip(batch, pool.map(compress, batch), strict=True))
        blobs = {}
        for rel, size in sizes.items():
            parts = [done[rel, n] for n in range(-(-size // XZ_PIECE))]
            root = hashlib.sha256(b"".join(leaves for _, _, leaves in parts))
            tree = root.digest()
            if sum(extent.size for extent, _, _ in parts) >= size:
                blobs[rel] = Blob(Extent(paths[rel], 0, size, tree), size, Codec.NONE, tree,
                                  known=tree)  # fmt: skip
            elif len(parts) == 1:
                blobs[rel] = Blob(parts[0][0], size, Codec.XZ, tree, known=tree)
            else:
                pieces = tuple(Blob(extent, length, Codec.XZ) for extent, length, _ in parts)
                blobs[rel] = Blob(b"", size, Codec.PIECES, tree, pieces, known=tree)
        return blobs

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()


class Appender:
    """Bytes appended to a Spill a piece at a time (a compressor's output), as one Extent."""

    def __init__(self, spill: Spill) -> None:
        self._spill = spill
        spill._lock.acquire()  # released by finish() or abandon()
        self._start = self._end = spill._size
        self._hash = TreeHasher()

    def write(self, data: bytes) -> None:
        written = 0
        while written < len(data):
            written += os.pwrite(self._spill._fd, data[written:], self._end + written)
        self._end += len(data)
        self._hash.update(data)

    @property
    def size(self) -> int:
        return self._end - self._start

    def finish(self) -> Extent:
        self._spill._size = self._end
        self._spill._lock.release()
        return Extent(self._spill.path, self._start, self.size, self._hash.digest(),
                      self._spill._fd)  # fmt: skip

    def abandon(self) -> None:
        """Nothing kept: the bytes written are overwritten by the next append."""
        self._spill._lock.release()


def _digest_of(content: bytes | None, known: bytes | None) -> bytes | None:
    return known if content is None else tree_digest(content)


class _File:
    """A file read a piece at a time: pieces inside holes are zeros, not read."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        self.size = os.fstat(self._fd).st_size

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        os.close(self._fd)

    def read(self, offset: int, length: int) -> bytes:
        data = os.pread(self._fd, length, offset)
        while len(data) < length:
            more = os.pread(self._fd, length - len(data), offset + len(data))
            if not more:
                raise OSError(f"{self.path} is shorter than it was (changed while built)")
            data += more
        return data

    def pieces(self) -> Iterator[tuple[bytes, bool]]:
        """Each EXTENT_PIECE of the file, and whether it is a hole."""
        for start in range(0, self.size, EXTENT_PIECE):
            length = min(EXTENT_PIECE, self.size - start)
            if is_hole(self._fd, start, length):
                yield bytes(length), True
            else:
                yield self.read(start, length), False

    def raw(self, *, digest: bool) -> Blob:
        """The file's own bytes, as Blob.raw_file makes them."""
        tree = file_digest(self._fd, self.size)
        return Blob(Extent(self.path, 0, self.size, tree), self.size, Codec.NONE,
                    tree if digest else None, known=tree)  # fmt: skip
