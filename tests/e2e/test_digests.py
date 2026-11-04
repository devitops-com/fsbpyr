"""The launcher's integrity digest keeps a bounded window of leaf digests (D38): it hashes up to
FSB_PASS_LEAVES leaves in one parallel pass and folds them into each file's root before the next
pass, so a file of any size needs the same memory. Built here with passes of 3 leaves
(tests/probes/digests.c), the digests must still be the Python reader's tree_digest, for files
hashed together, from memory or through their descriptors, and for content given a piece at a
time, as extraction gives it. A leaf inside a hole is not read, a whole leaf of zeros not hashed
(it takes the digest of zeros), and past a file's end there is no hole."""

import os
import random
import subprocess
from pathlib import Path

import pytest

from fsbpyr.payload.format import DIGEST_LEAF, DIGEST_SIZE, tree_digest

from .conftest import LauncherBuild

pytestmark = pytest.mark.slow

PROBE = Path(__file__).parents[1] / "probes" / "digests.c"
PASS_LEAVES = 3
SIZES = (0, 1, DIGEST_LEAF - 1, DIGEST_LEAF, DIGEST_LEAF + 1, 3 * DIGEST_LEAF,
         3 * DIGEST_LEAF + 7, 7 * DIGEST_LEAF - 1, 10 * DIGEST_LEAF)  # fmt: skip


@pytest.fixture(scope="module")
def probe(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("probe") / "digests"
    flags = (f"-DFSB_PASS_LEAVES={PASS_LEAVES}u", "-Wl,--wrap=pread,--wrap=SHA256_Update",
             "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined")  # fmt: skip
    return launcher_build.compile(out, [PROBE, "integrity.c", "parallel.c"], *flags)


@pytest.fixture(scope="module")
def files(tmp_path_factory: pytest.TempPathFactory) -> list[tuple[Path, bytes]]:
    directory = tmp_path_factory.mktemp("contents")
    noise = random.Random(38)  # noqa: S311  # test data
    written = []
    for size in SIZES:
        content = noise.randbytes(size)
        (directory / f"{size}").write_bytes(content)
        written.append((directory / f"{size}", content))
    return written


def _run(probe: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([probe, *args], capture_output=True, text=True, check=False,
                          env={**os.environ, **env})  # fmt: skip


def _digests(probe: Path, *args: str) -> list[str]:
    result = _run(probe, *args)
    assert result.returncode == 0, result.stderr
    return result.stdout.split()


def _sparse(path: Path, size: int, extents: list[tuple[int, bytes]]) -> bytes:
    """A file of ``size`` bytes, a hole but for ``extents``; its content."""
    with path.open("wb") as file:
        file.truncate(size)
        for offset, data in extents:
            file.seek(offset)
            file.write(data)
    return path.read_bytes()


def test_files_hashed_together_have_their_own_digests(
    probe: Path, files: list[tuple[Path, bytes]]
) -> None:
    digests = _digests(probe, "batch", *(str(path) for path, _ in files))

    assert digests == [tree_digest(content).hex() for _, content in files]


@pytest.mark.parametrize("piece", [DIGEST_LEAF, 2 * DIGEST_LEAF, 4 * DIGEST_LEAF])
def test_content_given_a_piece_at_a_time_has_its_digest(
    probe: Path, files: list[tuple[Path, bytes]], piece: int
) -> None:
    for path, content in files:
        assert _digests(probe, "pieces", str(piece), str(path)) == [tree_digest(content).hex()]


def test_holes_are_not_read_and_leaves_of_zeros_not_hashed(probe: Path, tmp_path: Path) -> None:
    """Two sparse files, each hashed through its descriptor and from memory (the probe's batch
    alternates): only the leaves with data are read, and a whole leaf of zeros takes the digest
    of zeros unhashed, a hole's or one in memory."""
    sparse = _sparse(tmp_path / "sparse", 10 * DIGEST_LEAF + 7,  # and 7 bytes of trailing hole
                     [(0, b"head"), (4 * DIGEST_LEAF + 100, b"middle")])  # fmt: skip
    holes = _sparse(tmp_path / "holes", 5 * DIGEST_LEAF, [])
    paths = ["sparse", "sparse", "holes", "holes"]

    result = _run(probe, "batch", *(str(tmp_path / path) for path in paths))

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [tree_digest(c).hex() for c in (sparse, sparse, holes, holes)]
    data = 2 * (2 * DIGEST_LEAF + 7)  # leaves 0 and 4 of each copy, and the short last leaf
    leaves = 2 * 11 + 2 * 5  # each leaf's digest goes into its file's root
    assert result.stderr.split() == ["read", str(2 * DIGEST_LEAF),
                                     "hashed", str(data + leaves * DIGEST_SIZE)]  # fmt: skip


def test_past_a_files_end_there_is_no_hole(probe: Path, tmp_path: Path) -> None:
    """A file that shrank since its size was taken hashes as unreadable, not as zeros (D38)."""
    _sparse(tmp_path / "holes", 2 * DIGEST_LEAF, [])

    result = _run(probe, "batch", str(tmp_path / "holes"), PROBE_LONGER=str(DIGEST_LEAF))

    assert (result.returncode, result.stdout) == (1, "")  # EIO: no digest
