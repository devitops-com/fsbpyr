"""Binary layout of the fsbpyr executable format, version 7 (normative spec: docs/format.md)."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import struct
import threading
from collections.abc import Buffer, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from enum import IntEnum, IntFlag

from ..compat import process_cpu_count

FORMAT_VERSION = 7
READABLE_VERSIONS = (1, 2, 3, 4, 5, 6, 7)  # the Python reader (inspect) reads older ones too
PAYLOAD_ALIGN = 65536  # v7: one file serves kernels with 4K, 16K and 64K pages
LEGACY_PAYLOAD_ALIGN = 4096  # versions 1 to 6
BLOB_ALIGN = 16
TABLE_ALIGN = 8
NONE = 0xFFFFFFFF
DIGEST_SIZE = 32
DIGEST_LEAF = 1 << 20  # the integrity digest hashes the file in pieces of this size (D31)
XZ_MEMLIMIT = 64 << 20  # decoder memory an xz stream may ask for (xz -6 needs about 9 MiB)
XZ_PIECE = 4 << 20  # libraries larger than this are xz in pieces of this much content (D33)
ZSTD_PIECE = 8 << 20  # content larger than this is zstd in pieces of this much content (D39)
MAX_MODE = 0o777
MAX_BLOB_SIZE = 1 << 62  # far inside off_t; no limit of ours below what OSes hold (D38)
MAX_STRTAB_SIZE = 0xFFFFFFFF

TRAILER_MAGIC = b"FSBPYR\x00T"
HEADER_MAGIC = b"FSBPYR\x00H"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# magic, version, flags, payload_offset, payload_size, digest: the file's last 32 bytes, the
# tree_digest of everything before them (versions 1 and 2: the payload's SHA-256)
TRAILER = struct.Struct("<8sIIQQ32s")
TRAILER_SIZE = TRAILER.size
# magic, version, header_size, python_hexversion, bytecode_magic, generation_id,
# app_name (off, len), entrypoint (off, len), build_info (off, len), 8 x section (offset, count)
HEADER = struct.Struct("<8sIII4s32s" + "II" * 3 + "QQ" * 8)
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
    STARTUP = 7


RECORD_SIZE = {
    Section.STRTAB: 1,
    Section.BLOBS: BLOB.size,
    Section.MODULES: MODULE.size,
    Section.FILES: FILE.size,
    Section.DISTS: DIST.size,
    Section.CLOSURES: WORD.size,
    Section.DIGESTS: DIGEST_SIZE,
    Section.STARTUP: WORD.size,
}


class Codec(IntEnum):
    NONE = 0
    ZSTD = 1
    BLOCK = 2  # v2: content inside a block (a zstd blob holding many small blobs, D30)
    XZ = 3  # v5: one xz stream, for native files only (--best-compression, D33)
    PIECES = 4  # v5: the concatenated content of XZ_PIECE-sized xz streams, decoded in parallel
    ZSTD_PIECES = 5  # v6: the same of ZSTD_PIECE-sized zstd frames (D39)


# A blob in pieces: the codec of its pieces, and the content each holds (the last, 1 to that).
PIECES_OF = {Codec.PIECES: (Codec.XZ, XZ_PIECE), Codec.ZSTD_PIECES: (Codec.ZSTD, ZSTD_PIECE)}


BLOCK_SIZE = 256 * 1024  # blocks of sources and data, rarely read
CODE_BLOCK_SIZE = 64 * 1024  # blocks of bytecode: an import decompresses the whole block
BLOCK_MEMBER_MAX = 64 * 1024  # smaller blobs (outside natives) go into blocks
MAX_BLOCK_SIZE = 1 << 20  # largest block readers accept (bounds the memory of a member read)


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


NATIVE_KINDS = frozenset({FileKind.NATIVE_EXT, FileKind.NATIVE_LIB})


class FileFlag(IntFlag):
    FS_REQUIRED = 1


def extractable(kind: FileKind, flags: FileFlag) -> bool:
    """Files written to the cache at run time; they carry an integrity digest (tree_digest)."""
    return kind in NATIVE_KINDS or FileFlag.FS_REQUIRED in flags


def tree_digest(data: Buffer) -> bytes:
    """SHA-256 over the SHA-256 of each DIGEST_LEAF piece of ``data``: the integrity digest of
    the executable (D31) and of every file it extracts (D32). The launcher's fsb_tree_digest
    computes the same, hashing the pieces in parallel."""
    root = hashlib.sha256()
    with memoryview(data) as items, items.cast("B") as view:  # bytes, as the launcher hashes
        for start in range(0, len(view), DIGEST_LEAF):
            root.update(hashlib.sha256(view[start : start + DIGEST_LEAF]).digest())
    return root.digest()


DIGEST_THREADS = 8  # as the launcher (D31): at most 8, never more than the usable cores
THREADS = min(DIGEST_THREADS, process_cpu_count() or 1)  # every phase's (D39)


_POOLED = threading.local()


def pooled_thread() -> None:
    """Initializer of a pool's threads: parallel() called on one runs in it, no pool in a pool."""
    _POOLED.busy = True


class Threads:
    """Up to THREADS threads for one task's batches (zstd, lzma and hashlib release the GIL);
    the caller's own thread when one would do: THREADS 1, or a pool's thread already (D39)."""

    def __init__(self, count: int = THREADS) -> None:
        inline = min(count, THREADS) < 2 or getattr(_POOLED, "busy", False)
        self._pool = None if inline else ThreadPoolExecutor(min(count, THREADS),
                                                            initializer=pooled_thread)  # fmt: skip

    def map[T, R](self, function: Callable[[T], R], items: Sequence[T]) -> list[R]:
        """``function`` of each item, in order."""
        if self._pool is None or len(items) < 2:
            return [function(item) for item in items]
        return list(self._pool.map(function, items))

    def __enter__(self) -> Threads:
        return self

    def __exit__(self, *_: object) -> None:
        if self._pool is not None:
            self._pool.shutdown(cancel_futures=True)


def parallel[T, R](function: Callable[[T], R], items: Sequence[T]) -> list[R]:
    """``function`` of each item, in order, on up to THREADS threads (Threads)."""
    if len(items) < 2:
        return [function(item) for item in items]
    with Threads(len(items)) as threads:
        return threads.map(function, items)


_PASS_LEAVES = 4096  # leaves hashed per pass: their digests are all the memory a file needs


def is_hole(fd: int, start: int, length: int) -> bool:
    """Whether [start, start + length) of the file open as ``fd`` lies inside a hole (SEEK_DATA,
    which moves the descriptor's offset). Past the file's end there is no hole: OSError, the file
    is shorter than it was."""
    try:
        return os.lseek(fd, start, os.SEEK_DATA) >= start + length
    except OSError as exc:
        if exc.errno != errno.ENXIO:  # ENXIO: no data after ``start``
            raise
    if os.fstat(fd).st_size < start + length:
        raise OSError(errno.EIO, "file shorter than it was (changed while read)")
    return True  # a hole up to the end


class TreeHasher:
    """tree_digest of bytes given in pieces of any size: a leaf at most is held."""

    def __init__(self) -> None:
        self._root, self._leaf, self._filled = hashlib.sha256(), hashlib.sha256(), 0

    def update(self, data: Buffer) -> None:
        with memoryview(data) as items, items.cast("B") as view:
            start = 0
            while start < len(view):
                part = view[start : start + DIGEST_LEAF - self._filled]
                self._leaf.update(part)
                self._filled += len(part)
                start += len(part)
                if self._filled == DIGEST_LEAF:
                    self.leaf(self._leaf.digest())
                    self._leaf, self._filled = hashlib.sha256(), 0

    def leaf(self, digest: bytes) -> None:
        """A whole leaf, hashed already (``update`` must be at a leaf's start)."""
        self._root.update(digest)

    def digest(self) -> bytes:
        if self._filled:
            self._root.update(self._leaf.digest())
            self._leaf, self._filled = hashlib.sha256(), 0
        return self._root.digest()


ZERO_LEAF = hashlib.sha256(bytes(DIGEST_LEAF)).digest()  # a leaf of zeros: a hole's


def _leaf_digest(fd: int, start: int, length: int) -> bytes:
    """The SHA-256 of ``length`` bytes at ``start`` of the file open as ``fd``; a leaf inside a
    hole is zeros, and not read."""
    if is_hole(fd, start, length):
        return ZERO_LEAF if length == DIGEST_LEAF else hashlib.sha256(bytes(length)).digest()
    data = os.pread(fd, length, start)
    while len(data) < length:
        more = os.pread(fd, length - len(data), start + len(data))
        if not more:
            raise OSError(errno.EIO, "file shorter than its digest's range (changed while read)")
        data += more
    return hashlib.sha256(data).digest()


def file_digest(fd: int, size: int, offset: int = 0) -> bytes:
    """tree_digest of ``size`` bytes at ``offset`` of the file open as ``fd``, read a leaf at a
    time on up to DIGEST_THREADS threads, a pass of leaves at a time, so that neither the file's
    size nor its pages count in memory (D38); a leaf inside a hole is not read (SEEK_DATA, which
    moves the descriptor's offset). OSError if the file is shorter."""
    if os.fstat(fd).st_size < offset + size:
        raise OSError(errno.EIO, f"file shorter than {offset + size} bytes")
    leaves = -(-size // DIGEST_LEAF)
    root = hashlib.sha256()

    def leaf(number: int) -> bytes:
        start = number * DIGEST_LEAF
        return _leaf_digest(fd, offset + start, min(DIGEST_LEAF, size - start))

    with ThreadPoolExecutor(min(DIGEST_THREADS, process_cpu_count() or 1)) as pool:
        for first in range(0, leaves, _PASS_LEAVES):
            for digest in pool.map(leaf, range(first, min(first + _PASS_LEAVES, leaves))):
                root.update(digest)
    return root.digest()


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


def is_module_part(part: str) -> bool:
    """An identifier, or a file stem that only ``importlib.import_module`` reaches: one with
    hyphens (sysconfig's ``_sysconfigdata__linux_x86_64-linux-gnu``) or starting with a digit
    (mypyc's shared ``81d243bd2c585b0f4821__mypyc``, which the extensions it compiled import)."""
    return part != "" and ("_" + part.replace("-", "_")).isidentifier()


def check_module_name(name: str) -> None:
    if not name or not all(is_module_part(part) for part in name.split(".")):
        raise PayloadError(f"invalid module name {name!r}")


def canonical_dist_name(name: str) -> str:
    """PEP 503 normalization: lowercase, runs of '-', '_', '.' collapsed to '-'."""
    return _CANONICAL_DIST_SEPARATORS.sub("-", name).lower()
