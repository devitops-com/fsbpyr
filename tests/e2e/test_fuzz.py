"""The launcher's payload parsing under libFuzzer, ASan and UBSan (D38; tests/fuzz/payload_fuzz.c):
every edge case once, then a run exploring from them. ``FSBPYR_FUZZ_SECONDS`` sets how long it
explores: 30 seconds in the test run; 600 before a release (docs/IMPLEMENTATION_PLAN.md,
Phase 8)."""

import os
import subprocess
from pathlib import Path

import pytest

from fsbpyr.payload.format import DIGEST_LEAF

from ..support import edges
from ..unit.boot.conftest import FAKE_RUNTIME
from .conftest import LauncherBuild

pytestmark = pytest.mark.slow

HARNESS = Path(__file__).parents[1] / "fuzz" / "payload_fuzz.c"
FUZZ_SECONDS = int(os.environ.get("FSBPYR_FUZZ_SECONDS", "30"))
SOURCES = ("payload.c", "integrity.c", "parallel.c", "cache.c", "way.c")


@pytest.fixture(scope="module")
def harness(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The harness, linked with the launcher's sources and the runtime's own zstd, liblzma and
    OpenSSL, as the launcher is."""
    out = tmp_path_factory.mktemp("harness") / "payload_fuzz"
    flags = (
        "-fsanitize=fuzzer,address,undefined",
        "-fno-sanitize-recover=undefined",
        f"-DFSB_STREAM_WINDOW={DIGEST_LEAF}u",
    )  # windows of a leaf: small inputs cross them
    return launcher_build.compile(out, [HARNESS, *SOURCES], *flags)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The intact edge-case executable and every edge case of it, one file each."""
    directory = tmp_path_factory.mktemp("corpus")
    intact = edges.build(FAKE_RUNTIME, tmp_path_factory.mktemp("intact") / "edge").read_bytes()
    (directory / "intact").write_bytes(intact)
    for number, case in enumerate(edges.CASES):
        (directory / f"case-{number:03}").write_bytes(edges.apply(case, intact))
    return directory


def _fuzz(harness: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([harness, *args], capture_output=True, text=True, check=False)


def test_the_parser_survives_every_edge_case(harness: Path, corpus: Path) -> None:
    files = sorted(os.fspath(path) for path in corpus.iterdir())

    result = _fuzz(harness, *files)  # each file run once

    assert result.returncode == 0, result.stderr[-4000:]
    assert f"Executed {files[-1]}" in result.stderr


def test_the_parser_survives_a_fuzzing_run(harness: Path, corpus: Path, tmp_path: Path) -> None:
    found = tmp_path / "found"  # new inputs the run keeps; the corpus stays as built
    found.mkdir()

    result = _fuzz(harness, f"-max_total_time={FUZZ_SECONDS}", "-rss_limit_mb=2048",
                   "-timeout=30", f"-max_len={1 << 20}", f"-artifact_prefix={tmp_path}/",
                   os.fspath(found), os.fspath(corpus))  # fmt: skip

    assert result.returncode == 0, result.stderr[-4000:]
    assert "Done " in result.stderr
