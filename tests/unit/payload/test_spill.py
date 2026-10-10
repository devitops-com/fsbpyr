"""A build's compressed bytes go to a spill file, not memory (D38, Phase 8c): files are
compressed a piece at a time from disk into it, their pieces on up to 8 threads (D39), giving
the very blobs compressing their bytes in memory gives, and blobs made in memory move their
bytes there; the writer streams them."""

import dataclasses
import random
from pathlib import Path
from typing import Any

import pytest

from fsbpyr.payload import format as format_module
from fsbpyr.payload.format import DIGEST_LEAF, XZ_PIECE, ZSTD_PIECE, Codec, tree_digest
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
        blob = spill.compress_files({"f": tmp_path / "f"}, level=9, digest=digest)["f"]
        again = spill.compress_files({"f": tmp_path / "f"}, level=9, digest=digest)["f"]

        _blobs_alike(blob, in_memory)
        assert blob.content() == data and blob.content_digest() == blob.known == tree_digest(data)
        assert stored_bytes(again.stored) == stored_bytes(blob.stored)  # reproducible


def test_a_file_that_does_not_compress_is_its_own_bytes(tmp_path: Path) -> None:
    data = random.Random(1).randbytes(DIGEST_LEAF + 7)  # noqa: S311  # incompressible test data
    (tmp_path / "f").write_bytes(data)
    with Spill(tmp_path) as spill:
        blob = spill.compress_files({"f": tmp_path / "f"}, digest=True)["f"]

        assert isinstance(blob.stored, Extent) and blob.stored.path == tmp_path / "f"
        _blobs_alike(blob, Blob.zstd(data, digest=True))


def test_a_sparse_file_compresses_as_its_zeros(tmp_path: Path) -> None:
    with (tmp_path / "f").open("wb") as file:
        file.truncate(5 * DIGEST_LEAF + 3)
        file.seek(2 * DIGEST_LEAF)
        file.write(b"data in a hole")
    data = (tmp_path / "f").read_bytes()
    with Spill(tmp_path) as spill:
        blob = spill.compress_files({"f": tmp_path / "f"}, digest=True)["f"]

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
        blobs = spill.compress_files({name: tmp_path / name for name in contents}, xz=True,
                                     digest=True)  # fmt: skip

        for name, data in contents.items():
            _blobs_alike(blobs[name], Blob.xz(data, digest=True))
        assert [blobs[n].codec for n in contents] == [Codec.XZ, Codec.PIECES, Codec.NONE]


LARGE = (COMPRESSIBLE * 8)[: 2 * ZSTD_PIECE + 5]  # three pieces, the last of 5 bytes


@pytest.mark.parametrize("threads", [1, 8])
def test_files_over_a_piece_compress_into_the_pieces_their_bytes_make(
    threads: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same bytes, whatever the number of threads: pieces are independent frames."""
    monkeypatch.setattr(format_module, "THREADS", threads)
    (tmp_path / "large").write_bytes(LARGE)
    (tmp_path / "small").write_bytes(COMPRESSIBLE)
    with Spill(tmp_path) as spill:
        blobs = spill.compress_files({"large": tmp_path / "large", "small": tmp_path / "small"},
                                     digest=True)  # fmt: skip

        _blobs_alike(blobs["large"], Blob.zstd(LARGE, digest=True))
        _blobs_alike(blobs["small"], Blob.zstd(COMPRESSIBLE, digest=True))
        assert blobs["large"].codec is Codec.ZSTD_PIECES and len(blobs["large"].pieces) == 3


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


def test_holes_are_never_sent_to_the_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D38, D39: a sparse file of 1 TiB has a million leaves of holes; each sent to a thread took
    0.15.0's raw build of it from 26 to 66 s. Only leaves with data are read on the threads."""
    path = tmp_path / "sparse"
    with path.open("wb") as file:
        file.truncate(40 * DIGEST_LEAF + 7)
        for leaf in (5, 30):
            file.seek(leaf * DIGEST_LEAF + 3)
            file.write(b"data")
    sent: list[int] = []
    real_map = format_module.Threads.map

    def counted(threads: format_module.Threads, function: Any, items: Any) -> Any:
        sent.extend(items)
        return real_map(threads, function, items)

    monkeypatch.setattr(format_module.Threads, "map", counted)
    extent = Extent.of_file(path)

    leaves = list(extent.leaves())

    assert sent == [5 * DIGEST_LEAF, 30 * DIGEST_LEAF]
    assert b"".join(piece for piece, _ in leaves) == path.read_bytes()
    assert [hole for _, hole in leaves].count(False) == 2
