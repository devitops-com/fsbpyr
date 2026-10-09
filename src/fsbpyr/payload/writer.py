"""Deterministic payload encoder and executable assembler (docs/format.md)."""

import hashlib
import io
import os
import tempfile
from collections.abc import Iterable, Iterator
from compression import zstd
from enum import IntEnum
from pathlib import Path
from typing import BinaryIO

from .format import (
    BLOB,
    BLOB_ALIGN,
    BLOCK_MEMBER_MAX,
    BLOCK_SIZE,
    CODE_BLOCK_SIZE,
    DIGEST_SIZE,
    DIST,
    FILE,
    FORMAT_VERSION,
    HEADER,
    HEADER_MAGIC,
    HEADER_SIZE,
    MAX_MODE,
    MAX_STRTAB_SIZE,
    MODULE,
    NATIVE_KINDS,
    NONE,
    PAGE_SIZE,
    TABLE_ALIGN,
    TRAILER,
    TRAILER_MAGIC,
    TRAILER_SIZE,
    WORD,
    Codec,
    FileKind,
    ModuleFlag,
    ModuleKind,
    PayloadError,
    Section,
    align,
    canonical_dist_name,
    check_module_name,
    check_path,
    encode_text,
    extractable,
    file_digest,
    tree_digest,
)
from .spec import (
    EXTENT_PIECE,
    Blob,
    Dist,
    Extent,
    File,
    Module,
    PayloadSpec,
    Stored,
    stored_bytes,
    stored_size,
)


class _Placement(IntEnum):
    """Blob order on disk: startup code first, natives last (locality, fewer page-ins)."""

    STARTUP = 0
    CODE = 1
    RESOURCE = 2
    SOURCE = 3
    NATIVE = 4


_FILE_PLACEMENT = {
    FileKind.RESOURCE: _Placement.RESOURCE,
    FileKind.METADATA: _Placement.RESOURCE,
    FileKind.SOURCE: _Placement.SOURCE,
    FileKind.NATIVE_EXT: _Placement.NATIVE,
    FileKind.NATIVE_LIB: _Placement.NATIVE,
}

_BlobKey = tuple[bytes, int, Codec]


def _identity(stored: Stored) -> bytes:
    """What stands for stored bytes in a key: the bytes in memory, or an Extent's identity,
    each tagged so that neither can be taken for the other."""
    return b"\1" + stored.identity if isinstance(stored, Extent) else b"\0" + stored


def _blob_key(blob: Blob) -> _BlobKey:
    if blob.codec is Codec.PIECES:  # its streams are its identity
        streams = hashlib.sha256()
        for piece in blob.pieces:
            streams.update(hashlib.sha256(_identity(piece.stored)).digest())
        return streams.digest(), blob.size, blob.codec
    return _identity(blob.stored), blob.size, blob.codec


class _Strings:
    def __init__(self) -> None:
        self.data = bytearray()
        self.offsets: dict[str, int] = {}

    def add(self, text: str) -> tuple[int, int]:
        encoded = encode_text(text)
        offset = self.offsets.get(text)
        if offset is None:
            offset = self.offsets[text] = len(self.data)
            self.data += encoded + b"\0"
            if len(self.data) > MAX_STRTAB_SIZE:
                raise PayloadError("string table exceeds 4 GiB")
        return offset, len(encoded)


def _content(blob: Blob) -> bytes:
    """The content of a block member (at most BLOCK_MEMBER_MAX bytes): raw or one zstd frame."""
    stored = stored_bytes(blob.stored)
    try:
        content = stored if blob.codec is Codec.NONE else zstd.decompress(stored)
    except zstd.ZstdError as exc:
        raise PayloadError(f"blob is not a valid zstd frame: {exc}") from None
    if len(content) != blob.size:
        raise PayloadError("blob content does not match its size")
    return content


def _packable(placement: _Placement, size: int) -> bool:
    """Blobs that go into blocks (D30): not natives, not empty, under BLOCK_MEMBER_MAX."""
    return placement is not _Placement.NATIVE and 0 < size < BLOCK_MEMBER_MAX


class _Sink:
    """The payload's bytes go into ``out`` (a seekable binary file) from offset ``base`` on, each
    write at its place; pieces of zeros are not written, so a file of runs of zeros is sparse
    (D38). Nothing of a blob's data stays in memory."""

    def __init__(self, out: BinaryIO, base: int) -> None:
        self.out, self.base, self.end = out, base, 0  # end: of what was written

    def write(self, offset: int, data: bytes) -> None:
        self.out.seek(self.base + offset)
        self.out.write(data)
        self.end = max(self.end, offset + len(data))

    def write_stored(self, offset: int, stored: Stored) -> None:
        """Whole pieces of zeros are not written: holes are a piece or more, inside a blob of a
        piece or more (the launcher reads such blobs from the file, not its mapping). An
        Extent's bytes are checked against its identity as they are copied: a file that changed
        since it was classified fails the build (OSError)."""
        leaves = stored.leaves() if isinstance(stored, Extent) else _leaves(stored)
        for piece, hole in leaves:
            if not hole and piece != _ZEROS:
                self.write(offset, piece)
            offset += len(piece)

    def finish(self, size: int) -> None:
        """The payload is ``size`` bytes, its last ones possibly a hole."""
        if self.end < size:
            self.write(size - 1, b"\0")


_ZEROS = bytes(EXTENT_PIECE)


def _leaves(data: bytes) -> Iterator[tuple[bytes, bool]]:
    with memoryview(data) as view:
        for start in range(0, len(data), EXTENT_PIECE):
            yield bytes(view[start : start + EXTENT_PIECE]), False


class _Blobs:
    """Deduplicates blobs, assigns indices in placement order, and packs small blobs into
    blocks: one stream of blocks per placement class (startup code, code, resources, sources),
    filled in blob order, so a package's modules share blocks and an import never decompresses
    sources. Block records follow the blob records, and the records of the pieces of large xz
    libraries (D33) follow them; a library's pieces lie where its data would. Only identities
    and sizes are held: a block's content is gathered from its members as it is written."""

    def __init__(
        self, modules: Iterable[Module], files: Iterable[File], *, blocks: bool, level: int
    ) -> None:
        self._blocks_on = blocks
        self._level = level
        self._by_content: dict[_BlobKey, _BlobKey] = {}
        self._blob: dict[_BlobKey, Blob] = {}  # a blob of each key: where its bytes are
        rank: dict[_BlobKey, tuple[_Placement, bytes]] = {}
        digests: dict[_BlobKey, bytes] = {}
        self.pieces: dict[_BlobKey, tuple[Blob, ...]] = {}

        def use(blob: Blob | None, placement: _Placement, ref: str) -> None:
            if blob is None:
                return
            key = self.key(blob, placement)
            self._blob.setdefault(key, blob)
            if blob.pieces:
                self.pieces[key] = blob.pieces
            candidate = (placement, ref.encode())
            rank[key] = min(rank.get(key, candidate), candidate)
            if blob.sha256 is not None:
                digests[key] = blob.sha256

        for module in modules:
            use(module.code, _code_placement(module), module.path)
            use(module.source, _Placement.SOURCE, module.path)
        for file in files:
            use(file.blob, _FILE_PLACEMENT[file.kind], file.path)
        self.order = sorted(rank, key=lambda k: (rank[k], k))
        self.index = {key: i for i, key in enumerate(self.order)}
        self.digests = [digests[k] for k in self.order if k in digests]
        self.digest_index = {k: i for i, k in enumerate(k for k in self.order if k in digests)}
        self.members: dict[_BlobKey, tuple[int, int]] = {}  # key -> (block number, position)
        self.blocks: list[list[_BlobKey]] = []  # the members of each block, in order
        if blocks:
            self._pack({key: rank[key][0] for key in self.order})

    def key(self, blob: Blob, placement: _Placement) -> _BlobKey:
        """Blobs bound for blocks are keyed by content: the same content raw or as a zstd frame
        at any level is one member (a runtime blob comes back raw from an executable, which
        keeps the self-hosting fixed point). Codec BLOCK in the key keeps a raw native with the
        same bytes a blob of its own."""
        original = _blob_key(blob)
        if not (self._blocks_on and _packable(placement, blob.size)):
            return original
        if original not in self._by_content:
            content = hashlib.sha256(_content(blob)).digest()
            self._by_content[original] = (content, blob.size, Codec.BLOCK)
        return self._by_content[original]

    def _pack(self, placements: dict[_BlobKey, _Placement]) -> None:
        filled, stream = 0, None
        for key in self.order:
            placement = placements[key]
            if not _packable(placement, key[1]):
                continue
            limit = CODE_BLOCK_SIZE if placement <= _Placement.CODE else BLOCK_SIZE
            if placement is not stream or filled + key[1] > limit or not self.blocks:
                self.blocks.append([])
                filled, stream = 0, placement
            self.members[key] = (len(self.blocks) - 1, filled)
            self.blocks[-1].append(key)
            filled += key[1]

    def _block(self, number: int) -> Blob:
        content = b"".join(_content(self._blob[key]) for key in self.blocks[number])
        return Blob.zstd(content, level=self._level, allow_raw=False)

    @property
    def count(self) -> int:
        return len(self.order) + len(self.blocks) + sum(map(len, self.pieces.values()))

    def ref(self, blob: Blob | None, placement: _Placement) -> int:
        return NONE if blob is None else self.index[self.key(blob, placement)]

    def write(self, sink: _Sink, data_start: int) -> tuple[list[bytes], int]:
        """Write the blob data area into ``sink``: data in blob order, each block where its
        first member would be. The BLOBS records, and where the data ends."""
        end = data_start
        rows: list[bytes] = [b""] * self.count
        first_block = len(self.order)
        next_piece = first_block + len(self.blocks)

        def place(stored: Stored) -> int:
            nonlocal end
            at = align(end, BLOB_ALIGN)
            sink.write_stored(at, stored)
            end = at + stored_size(stored)
            return at

        for key in self.order:
            _, size, codec = key
            digest = self.digest_index.get(key, NONE)
            member = self.members.get(key)
            if codec is Codec.PIECES:  # the first piece's index and the number of pieces
                pieces = self.pieces[key]
                rows[self.index[key]] = BLOB.pack(next_piece, len(pieces), size, codec, 0, 0,
                                                  digest)  # fmt: skip
                for piece in pieces:
                    rows[next_piece] = BLOB.pack(place(piece.stored), stored_size(piece.stored),
                                                 piece.size, Codec.XZ, 0, 0, NONE)  # fmt: skip
                    next_piece += 1
                continue
            if member is None:
                stored = self._blob[key].stored
                rows[self.index[key]] = BLOB.pack(place(stored), stored_size(stored), size, codec,
                                                  0, 0, digest)  # fmt: skip
                continue
            number, position = member
            if not rows[first_block + number]:
                block = self._block(number)
                rows[first_block + number] = BLOB.pack(
                    place(block.stored), stored_size(block.stored), block.size, Codec.ZSTD, 0, 0,
                    NONE
                )  # fmt: skip
            rows[self.index[key]] = BLOB.pack(first_block + number, position, size, Codec.BLOCK,
                                              0, 0, digest)  # fmt: skip
        return rows, end


def _code_placement(module: Module) -> _Placement:
    return _Placement.STARTUP if ModuleFlag.STARTUP in module.flags else _Placement.CODE


def _check_header(spec: PayloadSpec) -> None:
    if len(spec.generation_id) != 32:
        raise PayloadError("generation_id must be 32 bytes")
    if len(spec.bytecode_magic) != 4:
        raise PayloadError("bytecode_magic must be 4 bytes")
    if not 0 <= spec.python_hexversion <= 0xFFFFFFFF:
        raise PayloadError("python_hexversion out of range")
    if "/" in spec.app_name or spec.app_name in ("", ".", "..") or "\0" in spec.app_name:
        raise PayloadError(f"app name {spec.app_name!r} is not a single path component")


def _check_unique(kind: str, keys: list[str]) -> None:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise PayloadError(f"duplicate {kind} {key!r}")
        seen.add(key)


def _check_dists(dists: tuple[Dist, ...]) -> None:
    _check_unique("dist", [d.name for d in dists])
    for dist in dists:
        if dist.name != canonical_dist_name(dist.name) or not dist.name:
            raise PayloadError(f"dist name {dist.name!r} is not canonical")
        encode_text(dist.name)
        encode_text(dist.version)
        check_path(dist.dist_info)


def _check_files(files: tuple[File, ...], dists: set[str]) -> None:
    paths = [f.path for f in files]
    _check_unique("file", paths)
    known = {f.path: f for f in files}
    for file in files:
        check_path(file.path)
        if not 0 <= file.mode <= MAX_MODE:
            raise PayloadError(f"file mode {oct(file.mode)} not allowed for {file.path!r}")
        if file.dist is not None and file.dist not in dists:
            raise PayloadError(f"unknown dist {file.dist!r} for {file.path!r}")
        if extractable(file.kind, file.flags) and file.blob.sha256 is None:
            raise PayloadError(f"{file.path!r} is extracted at run time and needs a digest")
        if file.blob.codec in (Codec.XZ, Codec.PIECES) and file.kind not in NATIVE_KINDS:
            raise PayloadError(f"{file.path!r}: only native files may be xz-compressed")
        _check_closure(file, known)
    directories = {p.rsplit("/", i)[0] for p in paths for i in range(1, p.count("/") + 1)}
    clashes = sorted(directories & known.keys())
    if clashes:
        raise PayloadError(f"paths used both as file and directory: {clashes}")


def _check_closure(file: File, files: dict[str, File]) -> None:
    if not file.closure:
        return
    if file.kind is not FileKind.NATIVE_EXT:
        raise PayloadError(f"closure on {file.path!r}: only NATIVE_EXT files carry closures")
    if len(set(file.closure)) != len(file.closure) or file.path in file.closure:
        raise PayloadError(f"closure of {file.path!r} has duplicates or itself")
    for member in file.closure:
        target = files.get(member)
        if target is None or target.kind not in NATIVE_KINDS:
            raise PayloadError(f"closure of {file.path!r}: {member!r} is not a native file")


def _check_module(module: Module, files: dict[str, File], dists: set[str], *, blocks: bool) -> None:
    check_module_name(module.name)
    check_path(module.path)
    if ModuleFlag.FS_REQUIRED_ROOT in module.flags and module.kind is not ModuleKind.PACKAGE:
        raise PayloadError(f"FS_REQUIRED_ROOT on {module.name!r}, which is not a regular package")
    if module.dist is not None and module.dist not in dists:
        raise PayloadError(f"unknown dist {module.dist!r} for module {module.name!r}")
    if module.kind in (ModuleKind.MODULE, ModuleKind.PACKAGE):
        if module.code is None or module.file is not None:
            raise PayloadError(f"module {module.name!r} requires code and no file")
    elif module.kind is ModuleKind.NAMESPACE:
        if (module.code, module.source, module.file) != (None, None, None):
            raise PayloadError(f"namespace package {module.name!r} carries code or files")
    else:
        if module.code is not None or module.file is None:
            raise PayloadError(f"extension {module.name!r} needs a file and no code")
        target = files.get(module.file)
        if target is None:
            raise PayloadError(f"extension {module.name!r} names unknown file {module.file!r}")
        if target.kind is not FileKind.NATIVE_EXT:
            raise PayloadError(f"extension {module.name!r} file is not NATIVE_EXT")
    if (
        module.code is not None
        and module.code.codec is not Codec.ZSTD
        and not (  # raw, packed into a checksummed block
            blocks and module.code.codec is Codec.NONE and 0 < module.code.size < BLOCK_MEMBER_MAX
        )
    ):
        raise PayloadError(f"code of {module.name!r} must be checksummed: a zstd frame (Blob.code)")
    if module.source is not None and module.source.codec in (Codec.XZ, Codec.PIECES):
        raise PayloadError(f"source of {module.name!r}: only native files may be xz-compressed")


def _validate(spec: PayloadSpec) -> None:
    _check_header(spec)
    _check_dists(spec.dists)
    dists = {d.name for d in spec.dists}
    _check_files(spec.files, dists)
    _check_unique("module", [m.name for m in spec.modules])
    files = {f.path: f for f in spec.files}
    for module in spec.modules:
        _check_module(module, files, dists, blocks=spec.blocks)


def _by_key[T](items: Iterable[T], key: str) -> list[T]:
    return sorted(items, key=lambda item: str(getattr(item, key)).encode())


def write_payload(spec: PayloadSpec, out: BinaryIO, base: int = 0) -> int:
    """Write the payload of ``spec`` into ``out`` (seekable) from offset ``base`` on: the blob
    data streamed after the space its tables take, then the header and the tables (D38). Its
    size; identical specs always give identical bytes."""
    _validate(spec)
    modules = _by_key(spec.modules, "name")
    files = _by_key(spec.files, "path")
    dists = _by_key(spec.dists, "name")
    file_index = {f.path: i for i, f in enumerate(files)}
    dist_index = {d.name: i for i, d in enumerate(dists)}
    blobs = _Blobs(modules, files, blocks=spec.blocks, level=spec.zstd_level)
    strings = _Strings()
    header_strings = [strings.add(s) for s in (spec.app_name, spec.entrypoint, spec.build_info)]

    def dist_ref(name: str | None) -> int:
        return NONE if name is None else dist_index[name]

    dist_rows = [
        DIST.pack(*strings.add(d.name), *strings.add(d.dist_info), *strings.add(d.version))
        for d in dists
    ]
    words: list[int] = []
    file_rows = []
    for f in files:
        closure = NONE
        if f.closure:
            closure = len(words)
            words += [len(f.closure), *sorted(file_index[p] for p in f.closure)]
        file_rows.append(
            FILE.pack(
                *strings.add(f.path),
                f.kind,
                f.flags,
                f.mode,
                blobs.ref(f.blob, _FILE_PLACEMENT[f.kind]),
                dist_ref(f.dist),
                closure,
            )
        )
    startup = [i for i, m in enumerate(modules) if ModuleFlag.STARTUP in m.flags]
    module_rows = [
        MODULE.pack(
            *strings.add(m.name),
            *strings.add(m.path),
            m.kind,
            m.flags,
            0,
            blobs.ref(m.code, _code_placement(m)),
            blobs.ref(m.source, _Placement.SOURCE),
            NONE if m.file is None else file_index[m.file],
            dist_ref(m.dist),
            0,
        )
        for m in modules
    ]
    sections = {
        Section.STRTAB: (bytes(strings.data), len(strings.data)),
        Section.BLOBS: (b"", blobs.count),  # written once the data's offsets are known
        Section.MODULES: (b"".join(module_rows), len(module_rows)),
        Section.FILES: (b"".join(file_rows), len(file_rows)),
        Section.DISTS: (b"".join(dist_rows), len(dist_rows)),
        Section.CLOSURES: (b"".join(WORD.pack(w) for w in words), len(words)),
        Section.DIGESTS: (b"".join(blobs.digests), len(blobs.digests)),
        Section.STARTUP: (b"".join(WORD.pack(i) for i in startup), len(startup)),
    }
    return _layout(spec, header_strings, sections, blobs, _Sink(out, base))


def _layout(
    spec: PayloadSpec,
    header_strings: list[tuple[int, int]],
    sections: dict[Section, tuple[bytes, int]],
    blobs: _Blobs,
    sink: _Sink,
) -> int:
    cursor = HEADER_SIZE
    offsets: dict[Section, int] = {}
    for section in Section:
        cursor = offsets[section] = align(cursor, TABLE_ALIGN)
        if section is Section.BLOBS:
            cursor += BLOB.size * blobs.count
        else:
            cursor += len(sections[section][0])
    data_start = align(cursor, BLOB_ALIGN)
    rows, end = blobs.write(sink, data_start)
    sections[Section.BLOBS] = (b"".join(rows), len(rows))
    head = bytearray(data_start)
    section_fields = [v for s in Section for v in (offsets[s], sections[s][1])]
    HEADER.pack_into(
        head,
        0,
        HEADER_MAGIC,
        FORMAT_VERSION,
        HEADER_SIZE,
        spec.python_hexversion,
        spec.bytecode_magic,
        spec.generation_id,
        *(v for pair in header_strings for v in pair),
        *section_fields,
    )
    for section in Section:
        body = sections[section][0]
        head[offsets[section] : offsets[section] + len(body)] = body
    sink.write(0, bytes(head))
    sink.finish(end)
    return end


def encode_payload(spec: PayloadSpec) -> bytes:
    """The payload of ``spec`` in memory (for specs whose blobs it can hold)."""
    out = io.BytesIO()
    size = write_payload(spec, out)
    return out.getvalue()[:size]


def assemble(stub: bytes, payload: bytes) -> bytes:
    """Executable image: stub, zero padding to a page boundary, payload, trailer; the trailer
    ends with the digest of everything before it (D31)."""
    offset = align(len(stub), PAGE_SIZE)
    trailer = TRAILER.pack(
        TRAILER_MAGIC, FORMAT_VERSION, 0, offset, len(payload), bytes(DIGEST_SIZE)
    )
    image = stub + bytes(offset - len(stub)) + payload + trailer[:-DIGEST_SIZE]
    return image + tree_digest(image)


def write_executable(path: Path, stub: bytes, spec: PayloadSpec) -> None:
    """Atomically write the executable of ``spec`` to ``path`` with mode 0755, as ``assemble``
    lays it out: the payload streamed into the file, the digest then read back from it (D38)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w+b") as out:
            out.write(stub)
            offset = align(len(stub), PAGE_SIZE)
            size = write_payload(spec, out, offset)
            trailer = TRAILER.pack(TRAILER_MAGIC, FORMAT_VERSION, 0, offset, size,
                                   bytes(DIGEST_SIZE))  # fmt: skip
            out.seek(offset + size)
            out.write(trailer[:-DIGEST_SIZE])
            out.flush()
            hashed = offset + size + TRAILER_SIZE - DIGEST_SIZE
            digest = file_digest(out.fileno(), hashed)
            out.seek(hashed)  # file_digest moved the descriptor's offset
            out.write(digest)
            out.flush()
            os.fchmod(out.fileno(), 0o755)
            os.fsync(out.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)  # persist the rename itself
    finally:
        os.close(directory)
