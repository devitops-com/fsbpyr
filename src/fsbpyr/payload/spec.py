"""Immutable input model for the payload writer."""

from __future__ import annotations

import errno
import lzma
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from ..compat import zstd
from .format import (
    DIGEST_LEAF,
    MAX_BLOB_SIZE,
    PIECES_OF,
    XZ_PIECE,
    ZERO_LEAF,
    ZSTD_PIECE,
    Codec,
    FileFlag,
    FileKind,
    ModuleFlag,
    ModuleKind,
    PayloadError,
    Threads,
    TreeHasher,
    file_digest,
    is_hole,
    parallel,
    tree_digest,
)

ZSTD_MIN_LEVEL, ZSTD_MAX_LEVEL = 1, 22
# Builds and the runtime's stdlib frames (Phase 7). On aiagent's 405 MB of content: level 3
# gives 144.6 MB in 1 s, 9 gives 134.4 MB in 4 s, 19 gives 126.3 MB in 62 s; decompression
# speed is the same. `fsbpyr build --fast-compression` keeps level 3.
ZSTD_LEVEL = 9
ZSTD_FAST_LEVEL = 3
NO_MODULE_FLAGS = ModuleFlag(0)
NO_FILE_FLAGS = FileFlag(0)


EXTENT_PIECE = DIGEST_LEAF  # an extent is read this much at a time: a leaf, hashed as it comes
_LEAVES_AT_ONCE = 32  # leaves read and hashed at once (32 MiB): bounded memory (D38, D39)
_ZERO_PIECE = bytes(EXTENT_PIECE)  # every whole leaf of a hole: one buffer, not one each


@dataclass(frozen=True, slots=True)
class Extent:
    """``size`` bytes at ``offset`` of a file on disk: the stored data of a blob that is not
    held in memory (D38), a large file's own bytes or what a build spilled. ``identity`` is the
    tree_digest of the bytes: blobs are compared by it, and the bytes are checked against it
    as they are read. ``fd``: a descriptor to read instead of the path (a spill's, which has no
    holes and no path left)."""

    path: Path
    offset: int
    size: int
    identity: bytes
    fd: int = -1

    def leaves(self) -> Iterator[tuple[bytes, bool]]:
        """The bytes, a digest leaf (EXTENT_PIECE) at a time, each with whether it is a hole
        (zeros, not read). OSError once the bytes turn out to differ from the identity (a file
        changed while the build read it)."""
        fd = self.fd if self.fd >= 0 else os.open(self.path, os.O_RDONLY | os.O_CLOEXEC)
        hashed = TreeHasher()
        starts = range(0, self.size, EXTENT_PIECE)
        try:
            with Threads(len(starts)) as threads:  # read and hashed on up to 8 threads (D39)
                for first in range(0, len(starts), _LEAVES_AT_ONCE):
                    batch = starts[first : first + _LEAVES_AT_ONCE]
                    # holes found here, one lseek each: a thread per hole cost more than the hole
                    holes = [self._hole(fd, at) for at in batch]
                    data = [at for at, hole in zip(batch, holes, strict=True) if not hole]
                    read = iter(threads.map(lambda at: self._leaf(fd, at), data))
                    for at, hole in zip(batch, holes, strict=True):
                        piece, digest = self._zeros(at) if hole else next(read)
                        hashed.leaf(digest)
                        yield piece, hole
        finally:
            if self.fd < 0:
                os.close(fd)
        if hashed.digest() != self.identity:
            raise OSError(errno.EIO, f"{self.path} changed while the build read it")

    def _hole(self, fd: int, start: int) -> bool:
        """Whether the leaf at ``start`` lies inside a hole (zeros, not read)."""
        length = min(EXTENT_PIECE, self.size - start)
        return self.fd < 0 and is_hole(fd, self.offset + start, length)

    def _zeros(self, start: int) -> tuple[bytes, bytes]:
        """A hole's leaf at ``start`` and its digest: a whole one is the same bytes each time."""
        length = min(EXTENT_PIECE, self.size - start)
        if length == EXTENT_PIECE:
            return _ZERO_PIECE, ZERO_LEAF
        return bytes(length), sha256(bytes(length)).digest()

    def _leaf(self, fd: int, start: int) -> tuple[bytes, bytes]:
        """The leaf at ``start``, read, and its digest."""
        piece = _read(fd, self.offset + start, min(EXTENT_PIECE, self.size - start), self.path)
        return piece, sha256(piece).digest()

    def pieces(self) -> Iterator[bytes]:
        return (piece for piece, _ in self.leaves())

    def read(self) -> bytes:
        """The bytes, whole: for small extents only (block members)."""
        return b"".join(self.pieces())

    @classmethod
    def of_file(cls, path: Path) -> Extent:
        """A whole file; its identity is its tree_digest, read a leaf at a time."""
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        try:
            size = os.fstat(fd).st_size
            return cls(path, 0, size, file_digest(fd, size))
        finally:
            os.close(fd)


def _read(fd: int, offset: int, length: int, path: Path) -> bytes:
    data = os.pread(fd, length, offset)
    while len(data) < length:
        more = os.pread(fd, length - len(data), offset + len(data))
        if not more:
            raise OSError(errno.EIO, f"{path} is shorter than it was (changed while built)")
        data += more
    return data


type Stored = bytes | Extent


def stored_size(stored: Stored) -> int:
    return stored.size if isinstance(stored, Extent) else len(stored)


def stored_bytes(stored: Stored) -> bytes:
    return stored.read() if isinstance(stored, Extent) else stored


@dataclass(frozen=True, slots=True)
class Blob:
    """Stored bytes of one resource, possibly compressed, plus optionally the integrity digest of
    its content (``tree_digest``: D32). The bytes are in memory, or an Extent of a file (D38):
    the writer streams those."""

    stored: Stored
    size: int
    codec: Codec
    sha256: bytes | None = None
    pieces: tuple[Blob, ...] = ()  # PIECES, ZSTD_PIECES: their streams or frames (D33, D39)
    known: bytes | None = None  # the content's tree_digest when known, but not listed (D38)

    def __post_init__(self) -> None:
        if not 0 <= self.size <= MAX_BLOB_SIZE:
            raise PayloadError(f"blob size {self.size} outside 0..{MAX_BLOB_SIZE}")
        if self.codec is Codec.NONE and stored_size(self.stored) != self.size:
            raise PayloadError("blob size does not match stored data")
        if self.codec is Codec.XZ and self.size > XZ_PIECE:
            raise PayloadError("an xz stream holds at most XZ_PIECE bytes: more is in pieces")
        if self.sha256 is not None and len(self.sha256) != 32:
            raise PayloadError("blob digest must be 32 bytes")
        if (self.codec in PIECES_OF) != bool(self.pieces):
            raise PayloadError("pieces belong to a blob of codec PIECES or ZSTD_PIECES, which "
                               "needs them")  # fmt: skip
        if self.pieces:
            codec, each = PIECES_OF[self.codec]
            sizes = [piece.size for piece in self.pieces]
            if (self.stored or len(sizes) < 2 or sum(sizes) != self.size
                    or any(piece.codec is not codec for piece in self.pieces)
                    or any(size != each for size in sizes[:-1])
                    or not 0 < sizes[-1] <= each):  # fmt: skip
                raise PayloadError(f"pieces must be {codec.name.lower()} blobs of {each} bytes, "
                                   "the last of 1 to that")  # fmt: skip

    def content(self) -> bytes:
        """The content, whole: in memory, so for blobs whose size allows it; pieces decoded on
        up to format.THREADS threads."""
        if self.pieces:
            return b"".join(parallel(Blob.content, self.pieces))
        stored = stored_bytes(self.stored)
        if self.codec is Codec.XZ:
            return lzma.decompress(stored, format=lzma.FORMAT_XZ)
        return stored if self.codec is Codec.NONE else zstd.decompress(stored)

    def content_digest(self) -> bytes:
        """``tree_digest`` of the content, whatever the representation (a block member comes back
        raw from a payload, the same content as a zstd frame from a build)."""
        return self.sha256 or self.known or tree_digest(self.content())

    @classmethod
    def raw(cls, data: bytes, *, digest: bool = False) -> Blob:
        return cls(data, len(data), Codec.NONE, _digest(data, digest))

    @classmethod
    def raw_file(cls, path: Path, *, digest: bool = False) -> Blob:
        """A file's bytes as they are, never read into memory: the writer streams them (D38)."""
        extent = Extent.of_file(path)  # its identity: the content's tree_digest
        return cls(extent, extent.size, Codec.NONE, extent.identity if digest else None,
                   known=extent.identity)  # fmt: skip

    @classmethod
    def code(cls, data: bytes, *, level: int = ZSTD_LEVEL) -> Blob:
        """Marshalled code: always a checksummed zstd frame, so corruption is detected before
        ``marshal.loads`` (which is not safe against corrupt input) ever sees the bytes."""
        return cls.zstd(data, level=level, allow_raw=False)

    @classmethod
    def zstd(
        cls, data: bytes, *, level: int = ZSTD_LEVEL, digest: bool = False, allow_raw: bool = True
    ) -> Blob:
        """One zstd frame with content size and checksum, or one per ZSTD_PIECE of content,
        compressed on up to format.THREADS threads (D39); stored raw if that is not smaller (unless
        ``allow_raw`` is false)."""
        if len(data) <= ZSTD_PIECE:
            compressed = zstd.compress(data, options=zstd_options(level))
            if allow_raw and len(compressed) >= len(data):
                return cls.raw(data, digest=digest)
            return cls(compressed, len(data), Codec.ZSTD, _digest(data, digest))
        parts = [data[start : start + ZSTD_PIECE] for start in range(0, len(data), ZSTD_PIECE)]
        frames = parallel(lambda part: zstd.compress(part, options=zstd_options(level)), parts)
        if allow_raw and sum(map(len, frames)) >= len(data):
            return cls.raw(data, digest=digest)
        pieces = tuple(cls(frame, len(part), Codec.ZSTD)
                       for frame, part in zip(frames, parts, strict=True))  # fmt: skip
        return cls(b"", len(data), Codec.ZSTD_PIECES, _digest(data, digest), pieces)

    @classmethod
    def xz(cls, data: bytes, *, digest: bool = False) -> Blob:
        """xz -6 with the x86 filter (D33), for native files only: one stream, or one per
        XZ_PIECE of content so that the launcher decodes them in parallel; no check of their
        own, since the executable's integrity digest covers them. Stored raw if that is not
        smaller."""
        return cls.from_xz(data, [xz_stream(piece) for piece in xz_pieces(data)], digest=digest)

    @classmethod
    def from_xz(cls, data: bytes, streams: list[bytes], *, digest: bool = False) -> Blob:
        """``data`` from the xz streams of its xz_pieces (compressed elsewhere, in parallel)."""
        if sum(map(len, streams)) >= len(data):
            return cls.raw(data, digest=digest)
        if len(streams) == 1:
            return cls(streams[0], len(data), Codec.XZ, _digest(data, digest))
        pieces = tuple(cls(stream, len(piece), Codec.XZ)
                       for stream, piece in zip(streams, xz_pieces(data), strict=True))  # fmt: skip
        return cls(b"", len(data), Codec.PIECES, _digest(data, digest), pieces)


def zstd_options(level: int) -> dict[int, int]:
    """One zstd frame with its content size and a checksum (the launcher checks both)."""
    if not ZSTD_MIN_LEVEL <= level <= ZSTD_MAX_LEVEL:
        raise PayloadError(f"zstd level {level} out of range")
    return {
        zstd.CompressionParameter.compression_level: level,
        zstd.CompressionParameter.checksum_flag: 1,
        zstd.CompressionParameter.content_size_flag: 1,
    }


XZ_PRESET = 6  # owner decision (D33): xz -6 with the x86 filter, for native files
_XZ_FILTERS = ({"id": lzma.FILTER_X86}, {"id": lzma.FILTER_LZMA2, "preset": XZ_PRESET})


def xz_pieces(data: bytes) -> list[bytes]:
    """The content of each xz stream of a native file: XZ_PIECE bytes, the last fewer."""
    return [data[start : start + XZ_PIECE] for start in range(0, len(data), XZ_PIECE)]


def xz_stream(piece: bytes) -> bytes:
    return lzma.compress(piece, format=lzma.FORMAT_XZ, check=lzma.CHECK_NONE, filters=_XZ_FILTERS)


def _digest(data: bytes, wanted: bool) -> bytes | None:
    return tree_digest(data) if wanted else None


@dataclass(frozen=True, slots=True)
class Module:
    name: str
    path: str
    kind: ModuleKind
    code: Blob | None = None
    source: Blob | None = None
    file: str | None = None  # path of the NATIVE_EXT file record (extensions only)
    dist: str | None = None  # canonical distribution name
    flags: ModuleFlag = NO_MODULE_FLAGS


@dataclass(frozen=True, slots=True)
class File:
    path: str
    kind: FileKind
    blob: Blob
    mode: int = 0o644
    flags: FileFlag = NO_FILE_FLAGS
    dist: str | None = None
    closure: tuple[str, ...] = ()  # paths of bundled libraries extracted together with this file


@dataclass(frozen=True, slots=True)
class Dist:
    name: str  # canonical (PEP 503) distribution name
    dist_info: str
    version: str


@dataclass(frozen=True, slots=True)
class PayloadSpec:
    python_hexversion: int
    bytecode_magic: bytes
    generation_id: bytes
    app_name: str
    entrypoint: str
    build_info: str = "{}"
    modules: tuple[Module, ...] = field(default=())
    files: tuple[File, ...] = field(default=())
    dists: tuple[Dist, ...] = field(default=())
    blocks: bool = True  # pack small blobs into shared zstd blocks (D30)
    zstd_level: int = ZSTD_LEVEL  # of the blocks
