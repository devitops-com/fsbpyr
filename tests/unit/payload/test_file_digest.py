"""The integrity digest of a file, streamed (D38, Phase 8c): the builder and ``inspect --verify``
hash files of any size a leaf at a time, on threads, and a leaf inside a hole is not read (its
digest is that of zeros): a sparse terabyte hashes in seconds."""

import hashlib
import os
import random
from pathlib import Path

import pytest

from fsbpyr.payload.format import (
    DIGEST_LEAF,
    ZERO_LEAF,
    TreeHasher,
    file_digest,
    is_hole,
    tree_digest,
)

SIZES = [0, 1, DIGEST_LEAF - 1, DIGEST_LEAF, DIGEST_LEAF + 1, 5 * DIGEST_LEAF + 3]


def _digest(path: Path, offset: int = 0, size: int | None = None) -> bytes:
    fd = os.open(path, os.O_RDONLY)
    try:
        return file_digest(fd, os.fstat(fd).st_size - offset if size is None else size, offset)
    finally:
        os.close(fd)


@pytest.mark.parametrize("size", SIZES)
def test_a_file_has_the_digest_of_its_bytes(size: int, tmp_path: Path) -> None:
    data = random.Random(size).randbytes(size)  # noqa: S311  # test data
    (tmp_path / "f").write_bytes(data)

    assert _digest(tmp_path / "f") == tree_digest(data)


def test_a_range_of_a_file_has_the_digest_of_its_bytes(tmp_path: Path) -> None:
    data = random.Random(7).randbytes(3 * DIGEST_LEAF)  # noqa: S311
    (tmp_path / "f").write_bytes(data)

    assert _digest(tmp_path / "f", 4096, 2 * DIGEST_LEAF + 5) == tree_digest(
        data[4096 : 4096 + 2 * DIGEST_LEAF + 5]
    )


@pytest.mark.parametrize(
    "extents",
    [[], [(0, b"head")], [(3 * DIGEST_LEAF + 17, b"middle")], [(9 * DIGEST_LEAF - 2, b"end")],
     [(DIGEST_LEAF - 1, b"across a leaf"), (6 * DIGEST_LEAF, b"x" * DIGEST_LEAF)]],
    ids=["all hole", "head", "middle", "end", "across leaves"],
)  # fmt: skip
def test_holes_hash_as_zeros(extents: list[tuple[int, bytes]], tmp_path: Path) -> None:
    size = 9 * DIGEST_LEAF + 1
    with (tmp_path / "f").open("wb") as file:
        file.truncate(size)  # a hole of ``size`` bytes, then the data written into it
        for offset, data in extents:
            file.seek(offset)
            file.write(data)
    content = (tmp_path / "f").read_bytes()

    assert len(content) == size
    assert _digest(tmp_path / "f") == tree_digest(content)


def test_a_file_shorter_than_asked_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"short")

    with pytest.raises(OSError, match="shorter"):
        _digest(tmp_path / "f", 0, 10)


def test_past_the_end_of_a_file_is_no_hole(tmp_path: Path) -> None:
    """SEEK_DATA says ENXIO for a trailing hole and past the end alike: a file that shrank must
    not read as zeros (D38, security review)."""
    with (tmp_path / "f").open("wb") as file:
        file.truncate(2 * DIGEST_LEAF)
    fd = os.open(tmp_path / "f", os.O_RDONLY)
    try:
        assert is_hole(fd, DIGEST_LEAF, DIGEST_LEAF)  # a trailing hole
        with pytest.raises(OSError, match="shorter"):
            is_hole(fd, 2 * DIGEST_LEAF, 1)
    finally:
        os.close(fd)


@pytest.mark.parametrize("piece", [1, 4096, DIGEST_LEAF - 1, DIGEST_LEAF, 3 * DIGEST_LEAF])
def test_content_hashed_in_pieces_of_any_size_has_its_tree_digest(piece: int) -> None:
    data = random.Random(piece).randbytes(3 * DIGEST_LEAF + 11)  # noqa: S311  # test data
    hasher = TreeHasher()
    for start in range(0, len(data), piece):
        hasher.update(data[start : start + piece])

    assert hasher.digest() == tree_digest(data)


def test_the_digest_of_a_zero_leaf_is_written_out_correctly() -> None:
    assert hashlib.sha256(bytes(DIGEST_LEAF)).digest() == ZERO_LEAF
