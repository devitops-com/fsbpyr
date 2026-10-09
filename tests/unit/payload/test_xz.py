"""Native files compressed with xz (--best-compression, format v5, D33)."""

import lzma
import os
import struct

import pytest

from fsbpyr.payload.convert import spec_from_payload
from fsbpyr.payload.format import (
    FILE,
    XZ_PIECE,
    Codec,
    FileKind,
    ModuleKind,
    PayloadError,
    Section,
    tree_digest,
)
from fsbpyr.payload.reader import Payload
from fsbpyr.payload.spec import Blob, File, Module, PayloadSpec
from fsbpyr.payload.writer import encode_payload

from ...support.apps import library
from .conftest import make_spec

ELF = b"\x7fELF" + bytes(range(256)) * 400 + b"\x0f\x1f\x44\x00\x00" * 5000  # x86 nop padding
NATIVE = "x.libs/libx.so"


def _spec(*files: File, modules: tuple[Module, ...] = ()) -> PayloadSpec:
    return make_spec(files=files, modules=modules, dists=())


def _encoded(blob: Blob, kind: FileKind = FileKind.NATIVE_LIB) -> bytes:
    return encode_payload(_spec(File(NATIVE, kind, blob)))


def test_xz_blobs_hold_their_content_and_its_digest() -> None:
    blob = Blob.xz(ELF, digest=True)

    assert blob.codec is Codec.XZ and len(blob.stored) < len(ELF) // 4
    assert blob.content() == ELF and blob.sha256 == tree_digest(ELF)


def test_xz_streams_are_standard_with_no_check_of_their_own() -> None:
    """The file's integrity digest covers them; xz -t and lzma read them as they are."""
    stored = Blob.xz(ELF).stored

    assert lzma.decompress(stored, format=lzma.FORMAT_XZ) == ELF
    assert stored[7] & 0x0F == lzma.CHECK_NONE  # stream flags: the check type


def test_incompressible_data_is_stored_raw() -> None:
    assert Blob.xz(os.urandom(5000)).codec is Codec.NONE


def test_payloads_read_stream_and_reencode_xz_natives() -> None:
    raw = _encoded(Blob.xz(ELF, digest=True))
    payload = Payload(raw)
    record = payload.find_file(NATIVE)
    assert record is not None and payload.blob(record.blob).codec is Codec.XZ

    assert payload.read_blob(record.blob) == ELF
    assert payload.contains(record.blob, b"\x0f\x1f\x44\x00\x00" * 3, chunk=4096)
    assert not payload.contains(record.blob, b"not in there", chunk=4096)
    assert encode_payload(spec_from_payload(payload)) == raw


@pytest.mark.parametrize("kind", [FileKind.RESOURCE, FileKind.SOURCE, FileKind.METADATA])
def test_only_native_files_may_be_xz(kind: FileKind) -> None:
    with pytest.raises(PayloadError, match="xz"):
        _encoded(Blob.xz(ELF, digest=True), kind)


def test_module_sources_may_not_be_xz() -> None:
    module = Module("m", "m.py", ModuleKind.MODULE, code=Blob.code(b"c"), source=Blob.xz(ELF))

    with pytest.raises(PayloadError, match="xz"):
        encode_payload(_spec(modules=(module,)))


def test_reader_refuses_xz_blobs_of_other_files() -> None:
    raw = bytearray(_encoded(Blob.xz(ELF, digest=True)))
    files_at = struct.unpack_from("<Q", raw, 80 + 16 * Section.FILES)[0]
    struct.pack_into("<B", raw, files_at + 8, FileKind.RESOURCE)  # FILE.kind of the only file
    assert FILE.size == 24

    with pytest.raises(PayloadError, match="xz"):
        Payload(bytes(raw))


@pytest.mark.parametrize(
    ("stored", "size"),
    [
        (Blob.xz(ELF).stored, len(ELF) - 1),  # more content than the record says
        (Blob.xz(ELF).stored, len(ELF) + 1),  # less
        (Blob.xz(ELF).stored + b"junk", len(ELF)),  # data after the stream
        (Blob.xz(ELF).stored[:-40], len(ELF)),  # truncated
    ],
    ids=["longer", "shorter", "trailing", "truncated"],
)
def test_reader_refuses_xz_streams_that_do_not_match_their_record(stored: bytes, size: int) -> None:
    payload = Payload(_encoded(Blob(stored, size, Codec.XZ, bytes(32))))
    record = payload.find_file(NATIVE)
    assert record is not None

    with pytest.raises(PayloadError, match="xz"):
        payload.read_blob(record.blob)
    with pytest.raises(PayloadError, match="xz"):
        payload.contains(record.blob, b"never", chunk=4096)


def test_xz_blobs_need_format_version_5() -> None:
    older = bytearray(_encoded(Blob.xz(ELF, digest=True)))
    struct.pack_into("<I", older, 8, 4)

    with pytest.raises(PayloadError, match="format version 4 has no xz"):
        Payload(bytes(older))


def test_an_xz_stream_holds_at_most_a_piece() -> None:
    """More content is in pieces (D33), so a reader decodes an xz stream into a buffer of a
    piece's size at most: a tiny stream claiming more is refused before it is decoded (D38)."""
    bomb = Blob.xz(bytes(XZ_PIECE))

    with pytest.raises(PayloadError, match="at most XZ_PIECE"):
        Blob(bomb.stored, XZ_PIECE + 1, Codec.XZ, bytes(32))


# --- pieces: libraries over XZ_PIECE as independent xz streams, decoded in parallel ----------

BIG = library(2600)


def test_large_libraries_are_stored_as_pieces_of_independent_streams() -> None:
    assert len(BIG) > 2 * XZ_PIECE
    blob = Blob.xz(BIG, digest=True)

    assert blob.codec is Codec.PIECES and blob.stored == b""
    assert [piece.size for piece in blob.pieces] == [XZ_PIECE, XZ_PIECE, len(BIG) - 2 * XZ_PIECE]
    assert all(piece.codec is Codec.XZ for piece in blob.pieces)
    assert blob.content() == BIG and blob.sha256 == tree_digest(BIG)


def test_pieces_round_trip_and_stream_across_their_boundaries() -> None:
    raw = _encoded(Blob.xz(BIG, digest=True))
    payload = Payload(raw)
    record = payload.find_file(NATIVE)
    assert record is not None
    parent = payload.blob(record.blob)
    assert (parent.codec, parent.stored_size) == (Codec.PIECES, 3)  # stored_size: piece count
    seam = BIG[XZ_PIECE - 6 : XZ_PIECE + 6]  # across the first piece boundary

    assert payload.read_blob(record.blob) == BIG
    assert payload.contains(record.blob, seam, chunk=4096)
    assert encode_payload(spec_from_payload(payload)) == raw


def _parent(raw: bytes) -> int:
    payload = Payload(raw)
    record = payload.find_file(NATIVE)
    assert record is not None
    return record.blob


def _blob_at(raw: bytearray, index: int) -> int:
    return int(struct.unpack_from("<Q", raw, 80 + 16 * Section.BLOBS)[0]) + index * 32


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (8, 2, "pieces"),  # stored_size: two pieces cannot hold that size
        (8, 4, "pieces"),  # four: one more than the content needs
        (0, 10**6, "pieces"),  # offset: the first piece outside the table
        (16, 3 * XZ_PIECE + 1, "pieces"),  # size: more than three pieces hold
    ],
    ids=["too-few", "too-many", "outside", "too-large"],
)
def test_reader_refuses_pieces_that_do_not_add_up(field: int, value: int, message: str) -> None:
    raw = bytearray(_encoded(Blob.xz(BIG, digest=True)))
    struct.pack_into("<Q", raw, _blob_at(raw, _parent(bytes(raw))) + field, value)

    with pytest.raises(PayloadError, match=message):
        Payload(bytes(raw))


def test_reader_refuses_a_piece_that_is_not_xz() -> None:
    raw = bytearray(_encoded(Blob.xz(BIG, digest=True)))
    first = struct.unpack_from("<Q", raw, _blob_at(raw, _parent(bytes(raw))))[0]
    struct.pack_into("<B", raw, _blob_at(raw, first) + 24, Codec.ZSTD)

    with pytest.raises(PayloadError, match="piece"):
        Payload(bytes(raw))


def test_reader_refuses_files_pointing_at_a_piece() -> None:
    raw = bytearray(_encoded(Blob.xz(BIG, digest=True)))
    first = struct.unpack_from("<Q", raw, _blob_at(raw, _parent(bytes(raw))))[0]
    files_at = struct.unpack_from("<Q", raw, 80 + 16 * Section.FILES)[0]
    struct.pack_into("<I", raw, files_at + 12, first)  # FILE.blob of the only file

    with pytest.raises(PayloadError, match="piece"):
        Payload(bytes(raw))


def test_only_native_files_may_be_stored_as_pieces() -> None:
    with pytest.raises(PayloadError, match="xz"):
        _encoded(Blob.xz(BIG, digest=True), FileKind.RESOURCE)


def test_two_files_with_the_same_large_library_share_its_pieces() -> None:
    library_blob = Blob.xz(BIG, digest=True)
    files = (File(NATIVE, FileKind.NATIVE_LIB, library_blob),
             File("y.libs/liby.so", FileKind.NATIVE_LIB, library_blob))  # fmt: skip
    raw = encode_payload(_spec(*files))
    payload = Payload(raw)

    records = [payload.find_file(path) for path in (NATIVE, "y.libs/liby.so")]
    assert records[0] is not None and records[1] is not None
    assert records[0].blob == records[1].blob
    assert sum(b.codec is Codec.PIECES for b in payload.blobs()) == 1
    assert sum(b.codec is Codec.XZ for b in payload.blobs()) == 3
    assert encode_payload(_spec(*files)) == raw
