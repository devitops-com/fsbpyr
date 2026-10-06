"""Binary layout of the fsbpyr executable format, version 1 (normative spec: docs/format.md)."""

import re
import struct
from enum import IntEnum, IntFlag

FORMAT_VERSION = 1
PAGE_SIZE = 4096
BLOB_ALIGN = 16
TABLE_ALIGN = 8
NONE = 0xFFFFFFFF
DIGEST_SIZE = 32
MAX_MODE = 0o777
MAX_BLOB_SIZE = 1 << 40  # 1 TiB; keeps sizes far inside ssize_t
MAX_STRTAB_SIZE = 0xFFFFFFFF

TRAILER_MAGIC = b"FSBPYR\x00T"
HEADER_MAGIC = b"FSBPYR\x00H"

# magic, version, flags, payload_offset, payload_size, payload_sha256
TRAILER = struct.Struct("<8sIIQQ32s")
TRAILER_SIZE = TRAILER.size
# magic, version, header_size, python_hexversion, bytecode_magic, generation_id,
# app_name (off, len), entrypoint (off, len), build_info (off, len), 7 x section (offset, count)
HEADER = struct.Struct("<8sIII4s32s" + "II" * 3 + "QQ" * 7)
HEADER_SIZE = HEADER.size
# offset, stored_size, size, codec, reserved (u8, u16), digest
BLOB = struct.Struct("<QQQBBHI")
# name (off, len), path (off, len), kind, flags, reserved, code, source, file, dist, reserved
MODULE = struct.Struct("<IIIIBBHIIIII")
# path (off, len), kind, flags, mode, blob, dist, closure
FILE = struct.Struct("<IIBBHIII")
# name (off, len), dist_info (off, len), version (off, len)
DIST = struct.Struct("<IIIIII")
WORD = struct.Struct("<I")

_CANONICAL_DIST_SEPARATORS = re.compile(r"[-_.]+")


class PayloadError(ValueError):
    """The payload spec or the executable bytes violate the format."""


class Section(IntEnum):
    STRTAB = 0
    BLOBS = 1
    MODULES = 2
    FILES = 3
    DISTS = 4
    CLOSURES = 5
    DIGESTS = 6


RECORD_SIZE = {
    Section.STRTAB: 1,
    Section.BLOBS: BLOB.size,
    Section.MODULES: MODULE.size,
    Section.FILES: FILE.size,
    Section.DISTS: DIST.size,
    Section.CLOSURES: WORD.size,
    Section.DIGESTS: DIGEST_SIZE,
}


class Codec(IntEnum):
    NONE = 0
    ZSTD = 1


class ModuleKind(IntEnum):
    MODULE = 1
    PACKAGE = 2
    NAMESPACE = 3
    EXTENSION = 4


class ModuleFlag(IntFlag):
    STARTUP = 1
    FS_REQUIRED_ROOT = 2


class FileKind(IntEnum):
    RESOURCE = 1
    SOURCE = 2
    NATIVE_EXT = 3
    NATIVE_LIB = 4
    METADATA = 5


class FileFlag(IntFlag):
    FS_REQUIRED = 1


def align(value: int, alignment: int) -> int:
    return -(-value // alignment) * alignment


def encode_text(text: str) -> bytes:
    """UTF-8 bytes of ``text``; lone surrogates and NUL are rejected."""
    try:
        encoded = text.encode()
    except UnicodeEncodeError:
        raise PayloadError(f"string is not valid UTF-8: {text!r}") from None
    if b"\0" in encoded:
        raise PayloadError(f"string contains NUL: {text!r}")
    return encoded


def check_path(path: str) -> None:
    """Site-relative POSIX path without empty, '.', '..' components, backslashes or NUL."""
    parts = path.split("/")
    if not path or "\0" in path or "\\" in path or any(part in ("", ".", "..") for part in parts):
        raise PayloadError(f"unsafe path {path!r}")
    encode_text(path)


def check_module_name(name: str) -> None:
    if not name or not all(part.isidentifier() for part in name.split(".")):
        raise PayloadError(f"invalid module name {name!r}")


def canonical_dist_name(name: str) -> str:
    """PEP 503 normalization: lowercase, runs of '-', '_', '.' collapsed to '-'."""
    return _CANONICAL_DIST_SEPARATORS.sub("-", name).lower()
