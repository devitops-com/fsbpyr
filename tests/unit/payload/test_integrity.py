"""Whole-file integrity digest (format v3, D31): the launcher checks it before anything else."""

import hashlib
import struct

import pytest

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
from fsbpyr.payload.reader import Executable
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
