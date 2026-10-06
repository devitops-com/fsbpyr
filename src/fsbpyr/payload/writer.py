"""Deterministic payload encoder and executable assembler (docs/format.md)."""

import hashlib
import os
import tempfile
from collections.abc import Iterable
from enum import IntEnum
from pathlib import Path

from .format import (
    BLOB,
    BLOB_ALIGN,
    DIST,
    FILE,
    FORMAT_VERSION,
    HEADER,
    HEADER_MAGIC,
    HEADER_SIZE,
    MAX_MODE,
    MAX_STRTAB_SIZE,
    MODULE,
    NONE,
    PAGE_SIZE,
    TABLE_ALIGN,
    TRAILER,
    TRAILER_MAGIC,
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
)
from .spec import Blob, Dist, File, Module, PayloadSpec


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


def _blob_key(blob: Blob) -> _BlobKey:
    return blob.stored, blob.size, blob.codec


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


class _Blobs:
    """Deduplicates blobs and assigns indices in placement order."""

    def __init__(self, modules: Iterable[Module], files: Iterable[File]) -> None:
        rank: dict[_BlobKey, tuple[_Placement, bytes]] = {}
        digests: dict[_BlobKey, bytes] = {}

        def use(blob: Blob | None, placement: _Placement, ref: str) -> None:
            if blob is None:
                return
            key = _blob_key(blob)
            candidate = (placement, ref.encode())
            rank[key] = min(rank.get(key, candidate), candidate)
            if blob.sha256 is not None:
                digests[key] = blob.sha256

        for module in modules:
            startup = ModuleFlag.STARTUP in module.flags
            use(module.code, _Placement.STARTUP if startup else _Placement.CODE, module.path)
            use(module.source, _Placement.SOURCE, module.path)
        for file in files:
            use(file.blob, _FILE_PLACEMENT[file.kind], file.path)
        self.order = sorted(rank, key=lambda k: (rank[k], k))
        self.index = {key: i for i, key in enumerate(self.order)}
        self.digests = [digests[k] for k in self.order if k in digests]
        self.digest_index = {k: i for i, k in enumerate(k for k in self.order if k in digests)}

    def ref(self, blob: Blob | None) -> int:
        return NONE if blob is None else self.index[_blob_key(blob)]


def _check_header(spec: PayloadSpec) -> None:
    if len(spec.generation_id) != 32:
        raise PayloadError("generation_id must be 32 bytes")
    if len(spec.bytecode_magic) != 4:
        raise PayloadError("bytecode_magic must be 4 bytes")
    if not 0 <= spec.python_hexversion <= 0xFFFFFFFF:
        raise PayloadError("python_hexversion out of range")


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
        if target is None or target.kind is not FileKind.NATIVE_LIB:
            raise PayloadError(f"closure of {file.path!r}: {member!r} is not a NATIVE_LIB file")


def _check_module(module: Module, files: dict[str, File], dists: set[str]) -> None:
    check_module_name(module.name)
    check_path(module.path)
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
    if ModuleFlag.STARTUP in module.flags and (
        module.code is None or module.code.codec is not Codec.NONE
    ):
        raise PayloadError(f"startup module {module.name!r} needs uncompressed code")


def _validate(spec: PayloadSpec) -> None:
    _check_header(spec)
    _check_dists(spec.dists)
    dists = {d.name for d in spec.dists}
    _check_files(spec.files, dists)
    _check_unique("module", [m.name for m in spec.modules])
    files = {f.path: f for f in spec.files}
    for module in spec.modules:
        _check_module(module, files, dists)


def _by_key[T](items: Iterable[T], key: str) -> list[T]:
    return sorted(items, key=lambda item: str(getattr(item, key)).encode())


def encode_payload(spec: PayloadSpec) -> bytes:
    """Encode a spec into payload bytes; identical specs always give identical bytes."""
    _validate(spec)
    modules = _by_key(spec.modules, "name")
    files = _by_key(spec.files, "path")
    dists = _by_key(spec.dists, "name")
    file_index = {f.path: i for i, f in enumerate(files)}
    dist_index = {d.name: i for i, d in enumerate(dists)}
    blobs = _Blobs(modules, files)
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
                blobs.ref(f.blob),
                dist_ref(f.dist),
                closure,
            )
        )
    module_rows = [
        MODULE.pack(
            *strings.add(m.name),
            *strings.add(m.path),
            m.kind,
            m.flags,
            0,
            blobs.ref(m.code),
            blobs.ref(m.source),
            NONE if m.file is None else file_index[m.file],
            dist_ref(m.dist),
            0,
        )
        for m in modules
    ]
    sections = {
        Section.STRTAB: (bytes(strings.data), len(strings.data)),
        Section.BLOBS: (b"", len(blobs.order)),  # filled once data offsets are known
        Section.MODULES: (b"".join(module_rows), len(module_rows)),
        Section.FILES: (b"".join(file_rows), len(file_rows)),
        Section.DISTS: (b"".join(dist_rows), len(dist_rows)),
        Section.CLOSURES: (b"".join(WORD.pack(w) for w in words), len(words)),
        Section.DIGESTS: (b"".join(blobs.digests), len(blobs.digests)),
    }
    return _layout(spec, header_strings, sections, blobs)


def _layout(
    spec: PayloadSpec,
    header_strings: list[tuple[int, int]],
    sections: dict[Section, tuple[bytes, int]],
    blobs: _Blobs,
) -> bytes:
    cursor = HEADER_SIZE
    offsets: dict[Section, int] = {}
    for section in Section:
        cursor = offsets[section] = align(cursor, TABLE_ALIGN)
        if section is Section.BLOBS:
            cursor += BLOB.size * len(blobs.order)
        else:
            cursor += len(sections[section][0])
    data = bytearray()
    blob_rows = []
    data_start = align(cursor, BLOB_ALIGN)
    for key in blobs.order:
        stored, size, codec = key
        data += bytes(align(len(data), BLOB_ALIGN) - len(data))
        digest = blobs.digest_index.get(key, NONE)
        blob_rows.append(BLOB.pack(data_start + len(data), len(stored), size, codec, 0, 0, digest))
        data += stored
    sections[Section.BLOBS] = (b"".join(blob_rows), len(blob_rows))
    out = bytearray(data_start)
    section_fields = [v for s in Section for v in (offsets[s], sections[s][1])]
    HEADER.pack_into(
        out,
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
        out[offsets[section] : offsets[section] + len(body)] = body
    return bytes(out + data)


def assemble(stub: bytes, payload: bytes) -> bytes:
    """Executable image: stub, zero padding to a page boundary, payload, trailer."""
    offset = align(len(stub), PAGE_SIZE)
    trailer = TRAILER.pack(
        TRAILER_MAGIC, FORMAT_VERSION, 0, offset, len(payload), hashlib.sha256(payload).digest()
    )
    return stub + bytes(offset - len(stub)) + payload + trailer


def write_executable(path: Path, stub: bytes, payload: bytes) -> None:
    """Atomically write the assembled executable to ``path`` with mode 0755."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(assemble(stub, payload))
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
