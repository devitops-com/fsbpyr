"""Validating payload reader (build side, inspect, tests). See docs/format.md."""

import hashlib
import mmap
import os
from bisect import bisect_left
from compression import zstd
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from types import TracebackType
from typing import Self

from .format import (
    BLOB,
    BLOB_ALIGN,
    DIGEST_SIZE,
    DIST,
    FILE,
    FORMAT_VERSION,
    HEADER,
    HEADER_MAGIC,
    HEADER_SIZE,
    MAX_BLOB_SIZE,
    MAX_MODE,
    MODULE,
    NATIVE_KINDS,
    NONE,
    PAGE_SIZE,
    RECORD_SIZE,
    TABLE_ALIGN,
    TRAILER,
    TRAILER_MAGIC,
    TRAILER_SIZE,
    WORD,
    ZSTD_MAGIC,
    Codec,
    FileFlag,
    FileKind,
    ModuleFlag,
    ModuleKind,
    PayloadError,
    Section,
    canonical_dist_name,
    check_module_name,
    check_path,
    extractable,
)

type Buffer = bytes | bytearray | memoryview | mmap.mmap


@dataclass(frozen=True, slots=True)
class BlobRecord:
    index: int
    offset: int
    stored_size: int
    size: int
    codec: Codec
    digest: int


@dataclass(frozen=True, slots=True)
class ModuleRecord:
    index: int
    name: str
    path: str
    kind: ModuleKind
    flags: ModuleFlag
    code: int
    source: int
    file: int
    dist: int


@dataclass(frozen=True, slots=True)
class FileRecord:
    index: int
    path: str
    kind: FileKind
    flags: FileFlag
    mode: int
    blob: int
    dist: int
    closure: int


@dataclass(frozen=True, slots=True)
class DistRecord:
    index: int
    name: str
    dist_info: str
    version: str


def _enum[E: (Codec, ModuleKind, FileKind)](cls: type[E], value: int, what: str) -> E:
    try:
        return cls(value)
    except ValueError:
        raise PayloadError(f"invalid {what} {value}") from None


def _flags[F: (ModuleFlag, FileFlag)](cls: type[F], value: int, what: str) -> F:
    known = sum(int(member) for member in cls)
    if value & ~known:
        raise PayloadError(f"invalid {what} flags {value:#x}")
    return cls(value)


def _check_sorted(keys: list[bytes], what: str) -> None:
    if any(a >= b for a, b in pairwise(keys)):
        raise PayloadError(f"{what} table is not strictly sorted")


class Payload:
    """Parses and validates every table eagerly; lookups are binary searches."""

    def __init__(self, buffer: Buffer) -> None:
        self._buf = memoryview(buffer).cast("B")
        try:
            self._parse()
        except BaseException:
            self._buf.release()  # do not keep a caller's mmap exported on failure
            raise

    # --- parsing -------------------------------------------------------------------------

    def _parse(self) -> None:
        if len(self._buf) < HEADER_SIZE:
            raise PayloadError("truncated payload header")
        fields = HEADER.unpack_from(self._buf)
        magic, version, header_size, self.python_hexversion = fields[:4]
        if magic != HEADER_MAGIC:
            raise PayloadError("bad header magic")
        if version != FORMAT_VERSION:
            raise PayloadError(f"unsupported format version {version}")
        if header_size != HEADER_SIZE:
            raise PayloadError(f"bad header size {header_size}")
        self.bytecode_magic: bytes = fields[4]
        self.generation_id: bytes = fields[5]
        self._sections, self._data_start = self._parse_sections(fields[12:])
        strtab_offset, strtab_size = self._sections[Section.STRTAB]
        self._strtab = bytes(self._buf[strtab_offset : strtab_offset + strtab_size])
        self.app_name, self.entrypoint, self.build_info = (
            self._str(fields[i], fields[i + 1]) for i in (6, 8, 10)
        )
        self._words = self._parse_words(Section.CLOSURES)
        self._digests = self._sections[Section.DIGESTS][1]
        self._blobs = self._parse_blobs()
        self._dists = self._parse_dists()
        self._files = self._parse_files()
        for file in self._files:
            if extractable(file.kind, file.flags) and self._blobs[file.blob].digest == NONE:
                raise PayloadError(f"extracted file {file.path!r} has no digest")
            if file.closure != NONE:
                self._check_closure(file)
        self._modules = self._parse_modules()
        self._dist_keys = [d.name.encode() for d in self._dists]
        self._file_keys = [f.path.encode() for f in self._files]
        self._module_keys = [m.name.encode() for m in self._modules]
        _check_sorted(self._dist_keys, "dists")
        _check_sorted(self._file_keys, "files")
        _check_sorted(self._module_keys, "modules")
        self._startup = self._parse_startup()

    def _parse_sections(self, raw: tuple[int, ...]) -> tuple[dict[Section, tuple[int, int]], int]:
        sections = {}
        spans = []
        for section in Section:
            offset, count = raw[2 * section], raw[2 * section + 1]
            end = offset + count * RECORD_SIZE[section]
            if offset < HEADER_SIZE or offset % TABLE_ALIGN or end > len(self._buf):
                raise PayloadError(f"section {section.name} out of bounds")
            sections[section] = (offset, count)
            if end > offset:
                spans.append((offset, end))
        spans.sort()
        if any(a_end > b_start for (_, a_end), (b_start, _) in pairwise(spans)):
            raise PayloadError("sections overlap")
        return sections, max((end for _, end in spans), default=HEADER_SIZE)

    def _records(self, section: Section) -> list[tuple[int, ...]]:
        offset, count = self._sections[section]
        layout = {
            Section.BLOBS: BLOB,
            Section.MODULES: MODULE,
            Section.FILES: FILE,
            Section.DISTS: DIST,
        }[section]
        return [layout.unpack_from(self._buf, offset + i * layout.size) for i in range(count)]

    def _str(self, offset: int, length: int) -> str:
        end = offset + length
        if end >= len(self._strtab) or self._strtab[end] != 0:
            raise PayloadError(f"string at {offset} out of bounds or not NUL-terminated")
        raw = self._strtab[offset:end]
        if 0 in raw:
            raise PayloadError(f"string at {offset} contains NUL")
        try:
            return raw.decode()
        except UnicodeDecodeError:
            raise PayloadError(f"string at {offset} is not valid UTF-8") from None

    def _parse_words(self, section: Section) -> list[int]:
        offset, count = self._sections[section]
        return [WORD.unpack_from(self._buf, offset + 4 * i)[0] for i in range(count)]

    def _parse_startup(self) -> tuple[int, ...]:
        """STARTUP lists exactly the modules flagged STARTUP, in ascending index order."""
        indices = tuple(self._parse_words(Section.STARTUP))
        expected = tuple(m.index for m in self._modules if ModuleFlag.STARTUP in m.flags)
        if indices != expected:
            raise PayloadError("startup section does not match the STARTUP modules")
        return indices

    def _index(self, value: int, limit: int, what: str, *, optional: bool = True) -> int:
        if (value == NONE and optional) or value < limit:
            return value
        raise PayloadError(f"invalid {what} {value}")

    def _parse_blobs(self) -> tuple[BlobRecord, ...]:
        blobs = []
        records = self._records(Section.BLOBS)
        for i, (offset, stored, size, codec, res8, res16, digest) in enumerate(records):
            if res8 or res16:
                raise PayloadError(f"blob {i}: reserved fields are not zero")
            if offset % BLOB_ALIGN:
                raise PayloadError(f"blob {i} data is not {BLOB_ALIGN}-byte aligned")
            if offset < self._data_start or offset + stored > len(self._buf):
                raise PayloadError(f"blob range {i} outside the blob data area")
            if size > MAX_BLOB_SIZE:
                raise PayloadError(f"blob {i}: size {size} exceeds the limit")
            kind = _enum(Codec, codec, "codec")
            if kind is Codec.NONE and stored != size:
                raise PayloadError(f"blob {i}: stored size differs from size")
            self._index(digest, self._digests, "digest index")
            blobs.append(BlobRecord(i, offset, stored, size, kind, digest))
        return tuple(blobs)

    def _parse_dists(self) -> tuple[DistRecord, ...]:
        dists = []
        for i, (a, b, c, d, e, f) in enumerate(self._records(Section.DISTS)):
            record = DistRecord(i, self._str(a, b), self._str(c, d), self._str(e, f))
            if not record.name or record.name != canonical_dist_name(record.name):
                raise PayloadError(f"dist name {record.name!r} is not canonical")
            check_path(record.dist_info)
            dists.append(record)
        return tuple(dists)

    def _parse_files(self) -> tuple[FileRecord, ...]:
        files = []
        records = self._records(Section.FILES)
        for i, (off, length, kind, flags, mode, blob, dist, closure) in enumerate(records):
            if mode > MAX_MODE:
                raise PayloadError(f"invalid file mode {mode:#o}")
            path = self._str(off, length)
            check_path(path)
            files.append(
                FileRecord(
                    i,
                    path,
                    _enum(FileKind, kind, "file kind"),
                    _flags(FileFlag, flags, "file"),
                    mode,
                    self._index(blob, len(self._blobs), "blob index", optional=False),
                    self._index(dist, len(self._dists), "dist index"),
                    self._index(closure, len(self._words), "closure index"),
                )
            )
        return tuple(files)

    def _parse_modules(self) -> tuple[ModuleRecord, ...]:
        modules = []
        for i, row in enumerate(self._records(Section.MODULES)):
            name_off, name_len, path_off, path_len, kind, flags, res16, code, src, file, dist = row[
                :11
            ]
            if res16 or row[11]:
                raise PayloadError(f"module {i}: reserved fields are not zero")
            record = ModuleRecord(
                i,
                self._str(name_off, name_len),
                self._str(path_off, path_len),
                _enum(ModuleKind, kind, "module kind"),
                _flags(ModuleFlag, flags, "module"),
                self._index(code, len(self._blobs), "blob index"),
                self._index(src, len(self._blobs), "blob index"),
                self._index(file, len(self._files), "file index"),
                self._index(dist, len(self._dists), "dist index"),
            )
            check_module_name(record.name)
            check_path(record.path)
            self._check_module(record)
            modules.append(record)
        return tuple(modules)

    def _check_module(self, m: ModuleRecord) -> None:
        if m.kind in (ModuleKind.MODULE, ModuleKind.PACKAGE):
            valid = m.code != NONE and m.file == NONE
            valid = valid and self._blobs[m.code].codec is Codec.ZSTD
        elif m.kind is ModuleKind.NAMESPACE:
            valid = m.code == m.source == m.file == NONE
        else:
            valid = (
                m.code == NONE
                and m.file != NONE
                and (self._files[m.file].kind is FileKind.NATIVE_EXT)
            )
        if not valid:
            raise PayloadError(f"module {m.name!r} violates the {m.kind.name} kind invariants")
        if ModuleFlag.FS_REQUIRED_ROOT in m.flags and m.kind is not ModuleKind.PACKAGE:
            raise PayloadError(f"FS_REQUIRED_ROOT on {m.name!r}, which is not a regular package")

    def _closure_members(self, start: int) -> tuple[int, ...]:
        if start >= len(self._words):
            raise PayloadError(f"invalid closure index {start}")
        count = self._words[start]
        members = tuple(self._words[start + 1 : start + 1 + count])
        if not members or len(members) != count:
            raise PayloadError(f"invalid closure at {start}")
        if any(a >= b for a, b in pairwise(members)) or members[-1] >= len(self._files):
            raise PayloadError(f"invalid closure at {start}: members out of order or range")
        return members

    def _check_closure(self, owner: FileRecord) -> None:
        members = self._closure_members(owner.closure)
        if owner.kind is not FileKind.NATIVE_EXT or any(
            self._files[m].kind not in NATIVE_KINDS for m in members
        ):
            raise PayloadError(f"invalid closure of {owner.path!r}: kinds")
        if owner.index in members:
            raise PayloadError(f"invalid closure of {owner.path!r}: lists itself")

    # --- access ----------------------------------------------------------------------------

    def startup_modules(self) -> tuple[ModuleRecord, ...]:
        """Modules the launcher hands to CPython's frozen-module table before initialization."""
        return tuple(self._modules[i] for i in self._startup)

    def blobs(self) -> tuple[BlobRecord, ...]:
        return self._blobs

    def modules(self) -> tuple[ModuleRecord, ...]:
        return self._modules

    def files(self) -> tuple[FileRecord, ...]:
        return self._files

    def dists(self) -> tuple[DistRecord, ...]:
        return self._dists

    def blob(self, index: int) -> BlobRecord:
        if not 0 <= index < len(self._blobs):
            raise PayloadError(f"invalid blob index {index}")
        return self._blobs[index]

    def stored(self, index: int) -> bytes:
        """Stored (possibly compressed) bytes, for copying a blob verbatim into a new payload."""
        record = self.blob(index)
        return bytes(self._buf[record.offset : record.offset + record.stored_size])

    def read_blob(self, index: int) -> bytes:
        """Uncompressed content; never allocates more than ``size + 1`` bytes."""
        record = self.blob(index)
        stored = self._buf[record.offset : record.offset + record.stored_size]
        if record.codec is Codec.NONE:
            return bytes(stored)
        if not zstd_frame_has_checksum(stored):
            stored.release()
            raise PayloadError(f"blob {index}: zstd frame without content checksum")
        decompressor = zstd.ZstdDecompressor()
        try:
            data = decompressor.decompress(stored, max_length=record.size + 1)
        except zstd.ZstdError as exc:
            raise PayloadError(f"zstd frame invalid in blob {index}: {exc}") from None
        finally:
            stored.release()
        if len(data) != record.size or not decompressor.eof or decompressor.unused_data:
            raise PayloadError(f"blob {index}: decompressed size mismatch")
        return data

    def contains(self, index: int, needle: bytes, *, chunk: int = 1 << 20) -> bool:
        """Whether the blob's content holds ``needle``, decompressing ``chunk`` bytes at a time:
        the declared size of a hostile file is not trusted to fit in memory."""
        record = self.blob(index)
        stored = self._buf[record.offset : record.offset + record.stored_size]
        keep = len(needle) - 1  # bytes of a window that may start a match across windows
        try:
            if record.codec is Codec.NONE:
                for start in range(0, record.stored_size, chunk):
                    if needle in bytes(stored[max(start - keep, 0) : start + chunk]):
                        return True
                return False
            if not zstd_frame_has_checksum(stored):
                raise PayloadError(f"blob {index}: zstd frame without content checksum")
            decompressor, tail, produced = zstd.ZstdDecompressor(), b"", 0
            data: bytes | memoryview = stored
            while not decompressor.eof:
                output = decompressor.decompress(data, max_length=chunk)
                data = b""
                produced += len(output)
                if produced > record.size or (not output and decompressor.needs_input):
                    raise PayloadError(f"blob {index}: decompressed size mismatch")
                window = tail + output
                if needle in window:
                    return True
                tail = window[-keep:] if keep else b""
            return False
        except zstd.ZstdError as exc:
            raise PayloadError(f"zstd frame invalid in blob {index}: {exc}") from None
        finally:
            stored.release()

    def closure(self, index: int) -> tuple[int, ...]:
        return self._closure_members(index)

    def digest(self, index: int) -> bytes:
        if not 0 <= index < self._digests:
            raise PayloadError(f"invalid digest index {index}")
        offset = self._sections[Section.DIGESTS][0] + index * DIGEST_SIZE
        return bytes(self._buf[offset : offset + DIGEST_SIZE])

    def find_module(self, name: str) -> ModuleRecord | None:
        return _find(self._modules, self._module_keys, name)

    def find_file(self, path: str) -> FileRecord | None:
        return _find(self._files, self._file_keys, path)

    def find_dist(self, name: str) -> DistRecord | None:
        return _find(self._dists, self._dist_keys, name)

    def list_dir(self, path: str) -> tuple[str, ...]:
        """Names directly below directory ``path`` ('' is the root), sorted by UTF-8 bytes."""
        prefix = f"{path}/".encode() if path else b""
        children: set[bytes] = set()
        for i in range(bisect_left(self._file_keys, prefix), len(self._file_keys)):
            key = self._file_keys[i]
            if not key.startswith(prefix):
                break
            children.add(key[len(prefix) :].split(b"/", 1)[0])
        return tuple(child.decode() for child in sorted(children))

    def is_dir(self, path: str) -> bool:
        """Whether any file lives below directory ``path`` ('' is the root)."""
        prefix = f"{path}/".encode() if path else b""
        i = bisect_left(self._file_keys, prefix)
        return i < len(self._file_keys) and self._file_keys[i].startswith(prefix)

    def list_modules(self, prefix: str) -> tuple[tuple[str, ModuleKind], ...]:
        """Direct children of package ``prefix`` ("pkg." or "" for top level) with their kind."""
        encoded = prefix.encode()
        children = []
        for i in range(bisect_left(self._module_keys, encoded), len(self._module_keys)):
            key = self._module_keys[i]
            if not key.startswith(encoded):
                break
            tail = key[len(encoded) :]
            if b"." not in tail:
                children.append((tail.decode(), self._modules[i].kind))
        return tuple(children)

    def release(self) -> None:
        self._buf.release()


def zstd_frame_has_checksum(frame: bytes | memoryview) -> bool:
    """Frame magic followed by a descriptor with Content_Checksum_flag (RFC 8878 3.1.1.1.1)."""
    return len(frame) >= 6 and bytes(frame[:4]) == ZSTD_MAGIC and bool(frame[4] & 0x04)


def _find[R](records: tuple[R, ...], keys: list[bytes], key: str) -> R | None:
    encoded = key.encode()
    i = bisect_left(keys, encoded)
    return records[i] if i < len(keys) and keys[i] == encoded else None


class Executable:
    """An assembled executable: stub prefix, payload, trailer."""

    def __init__(self, buffer: Buffer, *, mapping: mmap.mmap | None = None) -> None:
        self._mapping = mapping
        self._view = memoryview(buffer).cast("B")
        self._payload: Payload | None = None
        try:
            self._parse()
        except BaseException:
            self.close()  # release views so the caller's mmap can be closed
            raise

    def _parse(self) -> None:
        size = len(self._view)
        if size < TRAILER_SIZE:
            raise PayloadError("truncated executable: no trailer")
        magic, version, flags, offset, payload_size, digest = TRAILER.unpack_from(
            self._view, size - TRAILER_SIZE
        )
        if magic != TRAILER_MAGIC:
            raise PayloadError("bad trailer magic")
        if version != FORMAT_VERSION:
            raise PayloadError(f"unsupported format version {version}")
        if flags:
            raise PayloadError(f"unknown trailer flags {flags:#x}")
        if offset % PAGE_SIZE:
            raise PayloadError("payload offset is not page aligned")
        if offset + payload_size + TRAILER_SIZE != size:
            raise PayloadError("payload size does not match file size")
        self.payload_offset: int = offset
        self.payload_size: int = payload_size
        self.payload_digest: bytes = digest
        window = self._view[offset : offset + payload_size]
        try:
            self._payload = Payload(window)
        finally:
            window.release()

    @property
    def payload(self) -> Payload:
        if self._payload is None:
            raise PayloadError("executable is closed")
        return self._payload

    @classmethod
    def parse(cls, image: bytes) -> Self:
        return cls(image)

    @classmethod
    def open(cls, path: Path) -> Self:
        with path.open("rb") as handle:
            if os.fstat(handle.fileno()).st_size < TRAILER_SIZE:
                raise PayloadError("truncated executable: no trailer")
            mapping = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        return cls(mapping, mapping=mapping)

    def stub_prefix(self) -> bytes:
        """Stub plus zero padding: everything before the payload, reusable for new payloads."""
        return bytes(self._view[: self.payload_offset])

    def verify(self) -> None:
        window = self._view[self.payload_offset : self.payload_offset + self.payload_size]
        try:
            actual = hashlib.sha256(window).digest()
        finally:
            window.release()
        if actual != self.payload_digest:
            raise PayloadError("payload digest mismatch")

    def close(self) -> None:
        """Release all views and the mapping; safe to call more than once."""
        if self._payload is not None:
            self._payload.release()
            self._payload = None
        self._view.release()
        if self._mapping is not None:
            self._mapping.close()
            self._mapping = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
