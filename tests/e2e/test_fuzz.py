"""The launcher's payload parsing under libFuzzer, ASan and UBSan (D38; tests/fuzz/payload_fuzz.c):
every edge case once, then a run exploring from them. ``FSBPYR_FUZZ_SECONDS`` sets how long it
explores: 30 seconds in the test run; 600 before a release (docs/IMPLEMENTATION_PLAN.md,
Phase 8). ``FSBPYR_FUZZ_CORPUS`` names the directory that keeps what the run finds, so that a
later run (full-suite.yml restores it from its cache) starts from it. The readers of signing's
.pub and .key files (tests/fuzz/sig_fuzz.c) get a shorter run of their own."""

import os
import subprocess
from pathlib import Path

import pytest

from fsbpyr.payload.format import DIGEST_LEAF

from ..support import edges
from ..support.signing import run_openssl
from ..unit.boot.conftest import FAKE_RUNTIME
from .conftest import LauncherBuild

pytestmark = pytest.mark.slow

HARNESS = Path(__file__).parents[1] / "fuzz" / "payload_fuzz.c"
SIG_HARNESS = Path(__file__).parents[1] / "fuzz" / "sig_fuzz.c"
SIG_FIXED = Path(__file__).parents[1] / "fixtures" / "signing" / "fixed.pub"
SIG_FUZZ_SECONDS = 30
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
    """The harness, forked by a shell. Started directly (vfork, then exec), a process keeps its
    parent's peak RSS as its own ru_maxrss, which libFuzzer compares with -rss_limit_mb (2048 by
    default): after a test that left this process's peak above that, as other test files can
    in a pytest-xdist worker, the harness stopped as out of memory at its first check."""
    command = ["sh", "-c", '"$@" & wait "$!"', "sh", os.fspath(harness), *args]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def test_the_parser_survives_every_edge_case(harness: Path, corpus: Path) -> None:
    files = sorted(os.fspath(path) for path in corpus.iterdir())

    result = _fuzz(harness, *files)  # each file run once

    assert result.returncode == 0, result.stderr[-4000:]
    assert f"Executed {files[-1]}" in result.stderr


def test_the_parser_survives_a_fuzzing_run(harness: Path, corpus: Path, tmp_path: Path) -> None:
    # new inputs the run keeps; the corpus stays as built
    found = Path(os.environ.get("FSBPYR_FUZZ_CORPUS") or tmp_path / "found")
    found.mkdir(parents=True, exist_ok=True)

    result = _fuzz(harness, f"-max_total_time={FUZZ_SECONDS}", "-rss_limit_mb=2048",
                   "-timeout=30", f"-max_len={1 << 20}", f"-artifact_prefix={tmp_path}/",
                   os.fspath(found), os.fspath(corpus))  # fmt: skip

    assert result.returncode == 0, result.stderr[-4000:]
    assert "Done " in result.stderr


@pytest.fixture(scope="module")
def sig_harness(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("sig-harness") / "sig_fuzz"
    flags = ("-DFSB_SIG_NO_PYTHON", "-fsanitize=fuzzer,address,undefined",
             "-fno-sanitize-recover=undefined")  # fmt: skip
    return launcher_build.compile(out, [SIG_HARNESS, "sig.c", "integrity.c", "parallel.c"], *flags)


def test_the_readers_of_keys_survive_a_fuzzing_run(sig_harness: Path, tmp_path: Path) -> None:
    corpus, found = tmp_path / "corpus", tmp_path / "found"
    corpus.mkdir()
    found.mkdir()
    (corpus / "pub").write_bytes(SIG_FIXED.read_bytes())
    blocks = []
    for algorithm in ("ED25519", "ML-DSA-65"):
        plain = run_openssl("genpkey", "-algorithm", algorithm)
        blocks.append(run_openssl("pkcs8", "-topk8", "-v2", "aes-256-cbc", "-v2prf",
                                  "hmacWithSHA256", "-iter", "1", "-passout", "pass:p",
                                  stdin=plain))  # fmt: skip
    (corpus / "key").write_bytes(b"".join(blocks))

    result = _fuzz(sig_harness, f"-max_total_time={SIG_FUZZ_SECONDS}", "-rss_limit_mb=2048",
                   "-timeout=30", f"-max_len={1 << 14}", f"-artifact_prefix={tmp_path}/",
                   os.fspath(found), os.fspath(corpus))  # fmt: skip

    assert result.returncode == 0, result.stderr[-4000:]
    assert "Done " in result.stderr
