"""The launcher's integrity digest keeps a bounded window of leaf digests (D38): it hashes up to
FSB_PASS_LEAVES leaves in one parallel pass and folds them into each file's root before the next
pass, so a file of any size needs the same memory. Built here with passes of 3 leaves
(tests/probes/digests.c), the digests must still be the Python reader's tree_digest, for files
hashed together, from memory or through their descriptors, and for content given a piece at a
time, as extraction gives it."""

import random
import subprocess
from pathlib import Path

import pytest

from fsbpyr.payload.format import DIGEST_LEAF, tree_digest

from .conftest import LauncherBuild

pytestmark = pytest.mark.slow

PROBE = Path(__file__).parents[1] / "probes" / "digests.c"
PASS_LEAVES = 3
SIZES = (0, 1, DIGEST_LEAF - 1, DIGEST_LEAF, DIGEST_LEAF + 1, 3 * DIGEST_LEAF,
         3 * DIGEST_LEAF + 7, 7 * DIGEST_LEAF - 1, 10 * DIGEST_LEAF)  # fmt: skip


@pytest.fixture(scope="module")
def probe(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("probe") / "digests"
    flags = (f"-DFSB_PASS_LEAVES={PASS_LEAVES}u", "-fsanitize=address,undefined",
             "-fno-sanitize-recover=undefined")  # fmt: skip
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


def _digests(probe: Path, *args: str) -> list[str]:
    result = subprocess.run([probe, *args], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.split()


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
