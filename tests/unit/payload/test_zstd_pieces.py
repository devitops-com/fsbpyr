"""zstd in pieces (format v6, D39): content over ZSTD_PIECE is stored as independent zstd frames
of ZSTD_PIECE bytes of content each, compressed and decoded on up to 8 threads; any file or
module source may be, and the bytes are the same whatever the number of threads."""

import random
import struct

import pytest

from fsbpyr.compat import zstd
from fsbpyr.payload import format as format_module
from fsbpyr.payload import spec as spec_module
from fsbpyr.payload.convert import spec_from_payload
from fsbpyr.payload.format import ZSTD_PIECE, Codec, FileKind, PayloadError, Section, tree_digest
from fsbpyr.payload.reader import Payload
from fsbpyr.payload.spec import Blob, File, PayloadSpec, stored_bytes, zstd_options
from fsbpyr.payload.writer import encode_payload

from .conftest import make_spec

PATH = "x/data.bin"
_WORDS = [bytes(random.Random(i).choices(b"abcdefghij", k=7)) for i in range(4096)]  # noqa: S311
_CHOSEN = random.Random(7).choices(_WORDS, k=(2 * ZSTD_PIECE + ZSTD_PIECE // 2) // 8)  # noqa: S311
BIG = b" ".join(_CHOSEN)[: 2 * ZSTD_PIECE + ZSTD_PIECE // 2]  # two and a half pieces


def _spec(*files: File) -> PayloadSpec:
    return make_spec(files=files, modules=(), dists=())


def _encoded(blob: Blob, kind: FileKind = FileKind.RESOURCE) -> bytes:
    return encode_payload(_spec(File(PATH, kind, blob)))


def test_content_over_a_piece_is_stored_as_frames_of_a_piece_each() -> None:
    blob = Blob.zstd(BIG, digest=True)

    assert blob.codec is Codec.ZSTD_PIECES and blob.stored == b""
    assert [p.size for p in blob.pieces] == [ZSTD_PIECE, ZSTD_PIECE, len(BIG) - 2 * ZSTD_PIECE]
    assert all(p.codec is Codec.ZSTD for p in blob.pieces)
    assert blob.content() == BIG and blob.sha256 == tree_digest(BIG)


def test_each_piece_is_one_frame_with_its_size_and_checksum() -> None:
    first = stored_bytes(Blob.zstd(BIG).pieces[0].stored)

    assert first == zstd.compress(BIG[:ZSTD_PIECE], options=zstd_options(spec_module.ZSTD_LEVEL))
    assert zstd.get_frame_info(first).decompressed_size == ZSTD_PIECE


def test_content_of_a_piece_or_less_is_one_frame() -> None:
    assert Blob.zstd(BIG[:ZSTD_PIECE]).codec is Codec.ZSTD


def test_the_bytes_are_the_same_whatever_the_number_of_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    eight = Blob.zstd(BIG)
    monkeypatch.setattr(format_module, "THREADS", 1)

    assert Blob.zstd(BIG) == eight


def test_code_over_a_piece_is_in_pieces_never_raw() -> None:
    noise = random.Random(3).randbytes(ZSTD_PIECE + 1)  # noqa: S311  incompressible: still frames

    blob = Blob.code(noise)

    assert blob.codec is Codec.ZSTD_PIECES and blob.content() == noise


def test_incompressible_content_is_stored_raw() -> None:
    noise = random.Random(4).randbytes(ZSTD_PIECE + 1)  # noqa: S311  test data

    assert Blob.zstd(noise).codec is Codec.NONE


def test_pieces_round_trip_and_read_across_their_boundaries() -> None:
    raw = _encoded(Blob.zstd(BIG, digest=True))
    payload = Payload(raw)
    record = payload.find_file(PATH)
    assert record is not None
    parent = payload.blob(record.blob)
    seam = BIG[ZSTD_PIECE - 6 : ZSTD_PIECE + 6]

    assert (parent.codec, parent.stored_size) == (Codec.ZSTD_PIECES, 3)  # the piece count
    assert payload.read_blob(record.blob) == BIG
    assert payload.contains(record.blob, seam, chunk=4096)
    assert encode_payload(spec_from_payload(payload)) == raw


@pytest.mark.parametrize("kind", [FileKind.RESOURCE, FileKind.NATIVE_LIB, FileKind.SOURCE])
def test_any_file_may_be_in_zstd_pieces(kind: FileKind) -> None:
    assert Payload(_encoded(Blob.zstd(BIG, digest=True), kind)).read_blob(0) is not None


def _blob_at(raw: bytearray, index: int) -> int:
    return int(struct.unpack_from("<Q", raw, 80 + 16 * Section.BLOBS)[0]) + index * 32


def _parent(raw: bytes) -> int:
    record = Payload(raw).find_file(PATH)
    assert record is not None
    return record.blob


@pytest.mark.parametrize(
    ("field", "value"),
    [(8, 2), (8, 4), (0, 10**6), (16, 3 * ZSTD_PIECE + 1)],
    ids=["too-few", "too-many", "outside", "too-large"],
)
def test_reader_refuses_pieces_that_do_not_add_up(field: int, value: int) -> None:
    raw = bytearray(_encoded(Blob.zstd(BIG, digest=True)))
    struct.pack_into("<Q", raw, _blob_at(raw, _parent(bytes(raw))) + field, value)

    with pytest.raises(PayloadError, match="pieces"):
        Payload(bytes(raw))


def test_reader_refuses_a_piece_that_is_not_zstd() -> None:
    raw = bytearray(_encoded(Blob.zstd(BIG, digest=True)))
    first = struct.unpack_from("<Q", raw, _blob_at(raw, _parent(bytes(raw))))[0]
    struct.pack_into("<B", raw, _blob_at(raw, first + 2) + 24, Codec.XZ)  # the last: small

    with pytest.raises(PayloadError, match="piece"):
        Payload(bytes(raw))


def test_blobs_refuse_pieces_of_the_wrong_codec_or_size() -> None:
    pieces = Blob.zstd(BIG).pieces
    raw_piece = Blob(bytes(ZSTD_PIECE), ZSTD_PIECE, Codec.NONE)

    with pytest.raises(PayloadError, match="pieces"):
        Blob(b"", len(BIG), Codec.ZSTD_PIECES, None, (raw_piece, *pieces[1:]))
    with pytest.raises(PayloadError, match="pieces"):
        Blob(b"", len(BIG) - 1, Codec.ZSTD_PIECES, None, pieces)
