"""Immutable input model for the payload writer."""

import lzma
from compression import zstd
from dataclasses import dataclass, field

from .format import (
    MAX_BLOB_SIZE,
    XZ_PIECE,
    Codec,
    FileFlag,
    FileKind,
    ModuleFlag,
    ModuleKind,
    PayloadError,
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


@dataclass(frozen=True, slots=True)
class Blob:
    """Stored bytes of one resource, possibly compressed, plus optionally the integrity digest of
    its content (``tree_digest``: D32)."""

    stored: bytes
    size: int
    codec: Codec
    sha256: bytes | None = None
    pieces: tuple[Blob, ...] = ()  # codec PIECES: xz streams of XZ_PIECE content each (D33)

    def __post_init__(self) -> None:
        if not 0 <= self.size <= MAX_BLOB_SIZE:
            raise PayloadError(f"blob size {self.size} outside 0..{MAX_BLOB_SIZE}")
        if self.codec is Codec.NONE and len(self.stored) != self.size:
            raise PayloadError("blob size does not match stored data")
        if self.codec is Codec.XZ and self.size > XZ_PIECE:
            raise PayloadError("an xz stream holds at most XZ_PIECE bytes: more is in pieces")
        if self.sha256 is not None and len(self.sha256) != 32:
            raise PayloadError("blob digest must be 32 bytes")
        if (self.codec is Codec.PIECES) != bool(self.pieces):
            raise PayloadError("pieces belong to a blob of codec PIECES, which needs them")
        if self.pieces:
            sizes = [piece.size for piece in self.pieces]
            if (self.stored or len(sizes) < 2 or sum(sizes) != self.size
                    or any(piece.codec is not Codec.XZ for piece in self.pieces)
                    or any(size != XZ_PIECE for size in sizes[:-1])
                    or not 0 < sizes[-1] <= XZ_PIECE):  # fmt: skip
                raise PayloadError("pieces must be xz streams of XZ_PIECE bytes, the last of 1 to "
                                   "XZ_PIECE")  # fmt: skip

    def content(self) -> bytes:
        if self.codec is Codec.PIECES:
            return b"".join(piece.content() for piece in self.pieces)
        if self.codec is Codec.XZ:
            return lzma.decompress(self.stored, format=lzma.FORMAT_XZ)
        return self.stored if self.codec is Codec.NONE else zstd.decompress(self.stored)

    def content_digest(self) -> bytes:
        """``tree_digest`` of the content, whatever the representation (a block member comes back
        raw from a payload, the same content as a zstd frame from a build)."""
        return self.sha256 or tree_digest(self.content())

    @classmethod
    def raw(cls, data: bytes, *, digest: bool = False) -> Blob:
        return cls(data, len(data), Codec.NONE, _digest(data, digest))

    @classmethod
    def code(cls, data: bytes, *, level: int = ZSTD_LEVEL) -> Blob:
        """Marshalled code: always a checksummed zstd frame, so corruption is detected before
        ``marshal.loads`` (which is not safe against corrupt input) ever sees the bytes."""
        return cls.zstd(data, level=level, allow_raw=False)

    @classmethod
    def zstd(
        cls, data: bytes, *, level: int = ZSTD_LEVEL, digest: bool = False, allow_raw: bool = True
    ) -> Blob:
        """One zstd frame with content size and checksum; stored raw if that is not smaller
        (unless ``allow_raw`` is false)."""
        if not ZSTD_MIN_LEVEL <= level <= ZSTD_MAX_LEVEL:
            raise PayloadError(f"zstd level {level} out of range")
        options: dict[int, int] = {
            zstd.CompressionParameter.compression_level: level,
            zstd.CompressionParameter.checksum_flag: 1,
            zstd.CompressionParameter.content_size_flag: 1,
        }
        compressed = zstd.compress(data, options=options)
        if allow_raw and len(compressed) >= len(data):
            return cls.raw(data, digest=digest)
        return cls(compressed, len(data), Codec.ZSTD, _digest(data, digest))

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
