"""Whole-file integrity digest (format v3, D31): the launcher checks it before anything else."""

import hashlib
import struct
from array import array

import pytest

from fsbpyr.payload.convert import spec_from_payload
from fsbpyr.payload.format import (
    DIGEST_LEAF,
    DIGEST_SIZE,
    PAGE_SIZE,
    TRAILER,
    TRAILER_MAGIC,
    TRAILER_SIZE,
    PayloadError,
    tree_digest,
)
from fsbpyr.payload.reader import Executable, Payload
from fsbpyr.payload.spec import Blob
from fsbpyr.payload.writer import assemble, encode_payload

from .conftest import make_spec

STUB = b"\x7fELF" + bytes(100)


def test_tree_digest_is_sha256_over_the_sha256_of_each_leaf() -> None:
    data = bytes(range(256)) * (2 * DIGEST_LEAF // 256) + b"tail"  # two full leaves and a bit
    leaves = [data[i : i + DIGEST_LEAF] for i in range(0, len(data), DIGEST_LEAF)]
    assert [len(leaf) for leaf in leaves] == [DIGEST_LEAF, DIGEST_LEAF, 4]

    expected = hashlib.sha256(b"".join(hashlib.sha256(leaf).digest() for leaf in leaves))

    assert tree_digest(data) == expected.digest()


@pytest.mark.parametrize("length", [0, 1, DIGEST_LEAF - 1, DIGEST_LEAF, DIGEST_LEAF + 1])
def test_tree_digest_has_one_leaf_per_started_piece(length: int) -> None:
    data = b"x" * length
    leaves = [data[i : i + DIGEST_LEAF] for i in range(0, length, DIGEST_LEAF)]

    expected = hashlib.sha256(b"".join(hashlib.sha256(leaf).digest() for leaf in leaves))

    assert tree_digest(data) == expected.digest()
    assert len(leaves) == -(-length // DIGEST_LEAF)  # exact multiples get no empty leaf


def test_file_digests_are_tree_digests_whatever_the_representation() -> None:
    """D32: files extracted to the cache are checked with the executable's construction."""
    data = bytes(range(256)) * (DIGEST_LEAF // 256) + b"tail"  # two pieces
    blobs = (Blob.raw(data, digest=True), Blob.zstd(data, digest=True), Blob.zstd(data))

    assert {blob.content_digest() for blob in blobs} == {tree_digest(data)}
    assert blobs[0].sha256 == blobs[1].sha256 == tree_digest(data)


def test_tree_digest_hashes_bytes_whatever_the_item_size() -> None:
    """The launcher hashes Py_buffer.len bytes; so must the Python routine."""
    items = array("H", range(1000)) * 600  # 1.2 MB: two pieces of bytes, under a million items

    assert tree_digest(items) == tree_digest(items.tobytes())


def test_payloads_of_older_versions_are_never_reencoded() -> None:
    """Their DIGESTS are plain SHA-256: re-encoded in this version they would fail every check."""
    older = bytearray(encode_payload(make_spec()))
    struct.pack_into("<I", older, 8, 3)

    with pytest.raises(PayloadError, match="format version 3"):
        spec_from_payload(Payload(bytes(older)))


def test_executable_ends_with_the_digest_of_everything_before_it() -> None:
    image = assemble(STUB, encode_payload(make_spec()))

    assert image[-DIGEST_SIZE:] == tree_digest(image[:-DIGEST_SIZE])
    exe = Executable.parse(image)
    exe.verify()
    assert exe.digest == image[-DIGEST_SIZE:] and exe.covers_file


@pytest.mark.parametrize("where", ["stub", "padding", "payload", "digest"])
def test_any_changed_byte_fails_verification(where: str) -> None:
    image = bytearray(assemble(STUB, encode_payload(make_spec())))
    position = {"stub": 2, "padding": len(STUB) + 10, "payload": len(image) - TRAILER_SIZE - 1,
                "digest": len(image) - 1}[where]  # fmt: skip
    image[position] ^= 0x01
    exe = Executable.parse(bytes(image))  # the tables still parse: only the digest can tell

    with pytest.raises(PayloadError, match="file digest mismatch"):
        exe.verify()


def test_version_2_executables_verify_their_payload_digest() -> None:
    payload = bytearray(encode_payload(make_spec()))
    struct.pack_into("<I", payload, 8, 2)  # header format_version
    digest = hashlib.sha256(payload).digest()
    trailer = TRAILER.pack(TRAILER_MAGIC, 2, 0, PAGE_SIZE, len(payload), digest)
    image = bytearray(STUB + bytes(PAGE_SIZE - len(STUB)) + payload + trailer)
    exe = Executable.parse(bytes(image))
    exe.verify()
    assert not exe.covers_file
    image[2] ^= 0x01  # the stub was never covered
    Executable.parse(bytes(image)).verify()
    image[PAGE_SIZE + len(payload) - 1] ^= 0x01

    with pytest.raises(PayloadError, match="payload digest mismatch"):
        Executable.parse(bytes(image)).verify()
