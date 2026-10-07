"""Immutable input model for the payload writer."""

import hashlib
from compression import zstd
from dataclasses import dataclass, field

from .format import (
    MAX_BLOB_SIZE,
    Codec,
    FileFlag,
    FileKind,
    ModuleFlag,
    ModuleKind,
    PayloadError,
)

ZSTD_MIN_LEVEL, ZSTD_MAX_LEVEL = 1, 22
NO_MODULE_FLAGS = ModuleFlag(0)
NO_FILE_FLAGS = FileFlag(0)


@dataclass(frozen=True, slots=True)
class Blob:
    """Stored bytes of one resource, possibly compressed, plus optional SHA-256 of the content."""

    stored: bytes
    size: int
    codec: Codec
    sha256: bytes | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.size <= MAX_BLOB_SIZE:
            raise PayloadError(f"blob size {self.size} outside 0..{MAX_BLOB_SIZE}")
        if self.codec is Codec.NONE and len(self.stored) != self.size:
            raise PayloadError("blob size does not match stored data")
        if self.sha256 is not None and len(self.sha256) != 32:
            raise PayloadError("blob digest must be 32 bytes")

    @classmethod
    def raw(cls, data: bytes, *, digest: bool = False) -> Blob:
        return cls(data, len(data), Codec.NONE, _digest(data, digest))

    @classmethod
    def code(cls, data: bytes, *, level: int = 3) -> Blob:
        """Marshalled code: always a checksummed zstd frame, so corruption is detected before
        ``marshal.loads`` (which is not safe against corrupt input) ever sees the bytes."""
        return cls.zstd(data, level=level, allow_raw=False)

    @classmethod
    def zstd(
        cls, data: bytes, *, level: int = 3, digest: bool = False, allow_raw: bool = True
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


def _digest(data: bytes, wanted: bool) -> bytes | None:
    return hashlib.sha256(data).digest() if wanted else None


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
