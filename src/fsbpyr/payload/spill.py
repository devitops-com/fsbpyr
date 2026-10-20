"""Where a build keeps what it compressed (D38, Phase 8c): one temporary file next to the output,
unlinked at once and appended to, so that no blob's bytes stay in memory however much a build
packages, and nothing is left behind however the build ends. Blobs then hold Extents of it
(read through its descriptor), which the writer streams into the executable."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Self

from ..compat import zstd
from .format import (
    DIGEST_LEAF,
    XZ_PIECE,
    ZERO_LEAF,
    ZSTD_PIECE,
    Codec,
    TreeHasher,
    file_digest,
    is_hole,
    parallel,
    tree_digest,
)
from .spec import EXTENT_PIECE, ZSTD_LEVEL, Blob, Extent, xz_stream, zstd_options

_BATCH = 64  # pieces compressed at once (at most 512 MiB of zstd content): bounded memory


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

    def compress_files(
        self, paths: Mapping[str, Path], *, xz: bool = False, level: int = ZSTD_LEVEL,
        digest: bool = False,
    ) -> dict[str, Blob]:  # fmt: skip
        """Each file as Blob.zstd (or, ``xz``, Blob.xz) makes it of its bytes: one frame or
        stream, or pieces of ZSTD_PIECE (XZ_PIECE) content (D33, D39), compressed into the
        spill. The pieces of every file are compressed together on up to THREADS threads,
        largest first, each read from its file (whole leaves of holes not read), hashed and
        compressed on its thread and put here as it is done; a file whose compressed bytes are
        not smaller is its own bytes, an Extent of it. A batch of pieces at most is in memory."""
        each = XZ_PIECE if xz else ZSTD_PIECE
        sizes = {rel: os.stat(path).st_size for rel, path in paths.items()}

        def count(rel: str) -> int:
            return max(1, -(-sizes[rel] // each))

        def length(job: tuple[str, int]) -> int:
            return min(each, sizes[job[0]] - job[1] * each)

        def compress(job: tuple[str, int]) -> tuple[Extent, int, bytes]:
            with _File(paths[job[0]]) as file:
                piece, leaves = file.piece(job[1] * each, length(job))
            stored = xz_stream(piece) if xz else zstd.compress(piece, options=zstd_options(level))
            return self.add(stored), len(piece), leaves

        every = [(rel, n) for rel in sizes for n in range(count(rel))]
        jobs = sorted(every, key=lambda job: (-length(job), job))
        done: dict[tuple[str, int], tuple[Extent, int, bytes]] = {}
        for first in range(0, len(jobs), _BATCH):
            batch = jobs[first : first + _BATCH]
            done.update(zip(batch, parallel(compress, batch), strict=True))
        return {
            rel: self._assembled(paths[rel], [done.pop((rel, n)) for n in range(count(rel))],
                                 xz=xz, digest=digest)
            for rel in sizes
        }  # fmt: skip

    @staticmethod
    def _assembled(path: Path, parts: list[tuple[Extent, int, bytes]], *, xz: bool,
                   digest: bool) -> Blob:  # fmt: skip
        """The blob of a file compressed in ``parts`` (an Extent, its content's length, its
        leaves' digests): as Blob.zstd and Blob.from_xz make them."""
        size = sum(length for _, length, _ in parts)
        tree = hashlib.sha256(b"".join(leaves for _, _, leaves in parts)).digest()
        listed = tree if digest else None
        if sum(extent.size for extent, _, _ in parts) >= size:
            return Blob(Extent(path, 0, size, tree), size, Codec.NONE, listed, known=tree)
        codec = Codec.XZ if xz else Codec.ZSTD
        if len(parts) == 1:
            return Blob(parts[0][0], size, codec, listed, known=tree)
        pieces = tuple(Blob(extent, length, codec) for extent, length, _ in parts)
        whole = Codec.PIECES if xz else Codec.ZSTD_PIECES
        return Blob(b"", size, whole, listed, pieces, known=tree)

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

    def piece(self, offset: int, length: int) -> tuple[bytes, bytes]:
        """[offset, offset + length) of the file (whole leaves inside holes are zeros, not
        read) and the digest of each of its DIGEST_LEAF leaves; ``offset`` is a leaf's."""
        parts, leaves = [], []
        for start in range(offset, offset + length, EXTENT_PIECE):
            size = min(EXTENT_PIECE, offset + length - start)
            if size == DIGEST_LEAF and is_hole(self._fd, start, size):
                parts.append(bytes(size))
                leaves.append(ZERO_LEAF)
            else:
                parts.append(self.read(start, size))
                leaves.append(hashlib.sha256(parts[-1]).digest())
        return b"".join(parts), b"".join(leaves)

    def raw(self, *, digest: bool) -> Blob:
        """The file's own bytes, as Blob.raw_file makes them."""
        tree = file_digest(self._fd, self.size)
        return Blob(Extent(self.path, 0, self.size, tree), self.size, Codec.NONE,
                    tree if digest else None, known=tree)  # fmt: skip
