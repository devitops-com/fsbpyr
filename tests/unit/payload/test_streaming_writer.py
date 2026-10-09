"""The writer streams (D38, Phase 8c): blob data goes into the output file a piece at a time,
from memory or from the files it is in (Extents), after the space the tables take; the tables
follow, and the digest is read back from the file. Runs of zeros become holes."""

import dataclasses
import os
import random
from pathlib import Path

import pytest

from fsbpyr.payload.format import FileKind
from fsbpyr.payload.reader import Executable
from fsbpyr.payload.spec import EXTENT_PIECE, Blob, File, PayloadSpec
from fsbpyr.payload.writer import assemble, encode_payload, write_executable

from .conftest import make_spec

STUB = b"\x7fELF stub" * 50


def _with(*files: File) -> PayloadSpec:
    spec = make_spec()
    return dataclasses.replace(spec, files=(*spec.files, *files))


def test_the_streamed_executable_is_the_assembled_image(tmp_path: Path) -> None:
    spec = make_spec()

    write_executable(tmp_path / "app", STUB, spec)

    assert (tmp_path / "app").read_bytes() == assemble(STUB, encode_payload(spec))
    assert os.stat(tmp_path / "app").st_mode & 0o777 == 0o755


def test_a_file_is_written_from_disk_as_it_would_be_from_memory(tmp_path: Path) -> None:
    content = random.Random(3).randbytes(3 * EXTENT_PIECE + 5)  # noqa: S311  # test data
    (tmp_path / "big.bin").write_bytes(content)
    small = b"a block member"
    (tmp_path / "small.bin").write_bytes(small)
    from_disk = _with(
        File("x/big.bin", FileKind.RESOURCE, Blob.raw_file(tmp_path / "big.bin")),
        File("x/small.bin", FileKind.RESOURCE, Blob.raw_file(tmp_path / "small.bin")),
    )
    in_memory = _with(File("x/big.bin", FileKind.RESOURCE, Blob.raw(content)),
                      File("x/small.bin", FileKind.RESOURCE, Blob.raw(small)))  # fmt: skip

    write_executable(tmp_path / "disk", STUB, from_disk)
    write_executable(tmp_path / "memory", STUB, in_memory)

    assert (tmp_path / "disk").read_bytes() == (tmp_path / "memory").read_bytes()
    with Executable.open(tmp_path / "disk") as exe:
        exe.verify()
        record = exe.payload.find_file("x/big.bin")
        assert record is not None and exe.payload.read_blob(record.blob) == content


def test_runs_of_zeros_become_holes(tmp_path: Path) -> None:
    size = 64 * EXTENT_PIECE + 3
    with (tmp_path / "zeros").open("wb") as file:
        file.truncate(size)  # sparse: nothing to read but zeros
    spec = _with(File("x/zeros", FileKind.RESOURCE, Blob.raw_file(tmp_path / "zeros")))

    write_executable(tmp_path / "app", STUB, spec)

    allocated = os.stat(tmp_path / "app").st_blocks * 512
    assert os.path.getsize(tmp_path / "app") > size and allocated < 4 * EXTENT_PIECE, allocated
    with Executable.open(tmp_path / "app") as exe:
        exe.verify()
        record = exe.payload.find_file("x/zeros")
        assert record is not None and exe.payload.read_blob(record.blob) == bytes(size)


@pytest.mark.parametrize("change", ["truncated", "shortened to a piece", "replaced"])
def test_a_file_changed_since_it_was_classified_fails_the_write(
    change: str, tmp_path: Path
) -> None:
    """The writer checks an Extent's bytes against the identity read when the blob was made:
    a shorter file never ships zeros, nor a replaced one other bytes (D38, security review)."""
    content = random.Random(5).randbytes(3 * EXTENT_PIECE)  # noqa: S311  # test data
    (tmp_path / "big.bin").write_bytes(content)
    spec = _with(File("x/big.bin", FileKind.RESOURCE, Blob.raw_file(tmp_path / "big.bin")))
    with (tmp_path / "big.bin").open("r+b") as file:
        if change == "replaced":
            file.write(bytes(len(content)))
        else:
            file.truncate(0 if change == "truncated" else EXTENT_PIECE)

    with pytest.raises(OSError, match=r"changed|shorter"):
        write_executable(tmp_path / "app", STUB, spec)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["big.bin"]  # no temporary file left


def test_verify_checks_the_file_it_opened(tmp_path: Path) -> None:
    """Not whatever its path names by then (D38, security review)."""
    write_executable(tmp_path / "app", STUB, make_spec())
    with Executable.open(tmp_path / "app") as exe:
        (tmp_path / "other").write_bytes(b"not an executable")
        os.replace(tmp_path / "other", tmp_path / "app")

        exe.verify()
