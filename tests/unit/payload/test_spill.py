"""A build's compressed bytes go to a spill file, not memory (D38, Phase 8c): files are
compressed a piece at a time from disk into it, giving the very blobs compressing their bytes in
memory gives, and blobs made in memory move their bytes there; the writer streams them."""

import dataclasses
import random
from pathlib import Path

import pytest

from fsbpyr.payload.format import DIGEST_LEAF, XZ_PIECE, Codec, tree_digest
from fsbpyr.payload.spec import Blob, Extent, stored_bytes
from fsbpyr.payload.spill import Spill
from fsbpyr.payload.writer import assemble, encode_payload, write_executable

from .conftest import make_spec

COMPRESSIBLE = b"".join(b"%08d " % i for i in range(3 * DIGEST_LEAF // 9))  # about 3 MiB


def _blobs_alike(spilled: Blob, in_memory: Blob) -> None:
    assert (spilled.codec, spilled.size, spilled.sha256) == (
        in_memory.codec,
        in_memory.size,
        in_memory.sha256,
    )
    assert stored_bytes(spilled.stored) == stored_bytes(in_memory.stored)
    assert [stored_bytes(p.stored) for p in spilled.pieces] == [
        stored_bytes(p.stored) for p in in_memory.pieces
    ]


@pytest.mark.parametrize("digest", [False, True])
@pytest.mark.parametrize("size", [0, 1, 100, DIGEST_LEAF - 1, DIGEST_LEAF, len(COMPRESSIBLE)])
def test_a_file_compresses_into_a_frame_of_its_bytes(
    size: int, digest: bool, tmp_path: Path
) -> None:
    data = COMPRESSIBLE[:size]
    (tmp_path / "f").write_bytes(data)
    in_memory = Blob.zstd(data, level=9, digest=digest)
    with Spill(tmp_path) as spill:
        blob = spill.zstd_file(tmp_path / "f", level=9, digest=digest)
        again = spill.zstd_file(tmp_path / "f", level=9, digest=digest)

        assert (blob.codec, blob.size, blob.sha256) == (in_memory.codec, size, in_memory.sha256)
        assert blob.content() == data and blob.content_digest() == blob.known == tree_digest(data)
        assert stored_bytes(again.stored) == stored_bytes(blob.stored)  # reproducible


def test_a_file_that_does_not_compress_is_its_own_bytes(tmp_path: Path) -> None:
    data = random.Random(1).randbytes(DIGEST_LEAF + 7)  # noqa: S311  # incompressible test data
    (tmp_path / "f").write_bytes(data)
    with Spill(tmp_path) as spill:
        blob = spill.zstd_file(tmp_path / "f", digest=True)

        assert isinstance(blob.stored, Extent) and blob.stored.path == tmp_path / "f"
        _blobs_alike(blob, Blob.zstd(data, digest=True))


def test_a_sparse_file_compresses_as_its_zeros(tmp_path: Path) -> None:
    with (tmp_path / "f").open("wb") as file:
        file.truncate(5 * DIGEST_LEAF + 3)
        file.seek(2 * DIGEST_LEAF)
        file.write(b"data in a hole")
    data = (tmp_path / "f").read_bytes()
    with Spill(tmp_path) as spill:
        blob = spill.zstd_file(tmp_path / "f", digest=True)

        assert blob.codec is Codec.ZSTD and blob.content() == data
        assert blob.sha256 == tree_digest(data)


def test_files_compress_into_the_xz_blobs_their_bytes_make(tmp_path: Path) -> None:
    contents = {
        "small.so": COMPRESSIBLE[: XZ_PIECE // 2],
        "pieces.so": (COMPRESSIBLE * 4)[: 2 * XZ_PIECE + 5],
        "noise.so": random.Random(2).randbytes(XZ_PIECE + 1),  # noqa: S311
    }
    for name, data in contents.items():
        (tmp_path / name).write_bytes(data)
    with Spill(tmp_path) as spill:
        blobs = spill.xz_files({name: tmp_path / name for name in contents}, threads=4)

        for name, data in contents.items():
            _blobs_alike(blobs[name], Blob.xz(data, digest=True))
        assert [blobs[n].codec for n in contents] == [Codec.XZ, Codec.PIECES, Codec.NONE]


def test_blobs_kept_in_the_spill_write_the_same_executable(tmp_path: Path) -> None:
    spec = make_spec()
    with Spill(tmp_path) as spill:

        def kept(blob: Blob | None) -> Blob | None:
            return None if blob is None else spill.keep(blob, blob.content())

        spilled = dataclasses.replace(
            spec,
            modules=tuple(
                dataclasses.replace(m, code=kept(m.code), source=kept(m.source))
                for m in spec.modules
            ),
            files=tuple(dataclasses.replace(f, blob=spill.keep(f.blob)) for f in spec.files),
        )
        assert all(isinstance(f.blob.stored, Extent) or f.blob.pieces for f in spilled.files)
        write_executable(tmp_path / "app", b"STUB", spilled)

        assert (tmp_path / "app").read_bytes() == assemble(b"STUB", encode_payload(spec))
        assert not spill.path.exists()  # unlinked at once: nothing left, however a build ends


def test_kept_blobs_know_their_content_digest(tmp_path: Path) -> None:
    with Spill(tmp_path) as spill:
        blob = spill.keep(Blob.zstd(COMPRESSIBLE), COMPRESSIBLE)

        assert blob.sha256 is None and blob.known == tree_digest(COMPRESSIBLE)  # not listed
        assert blob.content() == COMPRESSIBLE
