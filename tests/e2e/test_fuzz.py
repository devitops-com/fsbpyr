"""The launcher's payload parsing under libFuzzer, ASan and UBSan (D38; tests/fuzz/payload_fuzz.c):
every edge case once, then a run exploring from them. ``FSBPYR_FUZZ_SECONDS`` sets how long it
explores: 30 seconds in the test run; 600 before a release (docs/IMPLEMENTATION_PLAN.md,
Phase 8). ``FSBPYR_FUZZ_CORPUS`` names the directory that keeps what the run finds, so that a
later run (full-suite.yml restores it from its cache) starts from it. The readers of signing's
.pub and .key files (tests/fuzz/sig_fuzz.c) get a shorter run of their own, which keeps what it
finds in the directory beside that one whose name ends in -sig."""

import os
import re
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
FUZZ_SECONDS = int(os.environ.get("FSBPYR_FUZZ_SECONDS", "30"))
SOURCES = ("payload.c", "integrity.c", "parallel.c", "cache.c", "way.c")


def sig_fuzz_seconds(seconds: int) -> int:
    """How long the readers of keys explore when the payload harness explores ``seconds``: a
    quarter of it, but no less than the 30 seconds of the test run (or ``seconds``, when less).
    Their inputs are a few KiB; and the suite job waits for the worker that fuzzes, so 600 more
    seconds before a release would be 600 more for the job."""
    return min(seconds, max(30, seconds // 4))


def found(tmp_path: Path, suffix: str = "") -> Path:
    """Where a fuzzing run keeps the inputs it finds: ``FSBPYR_FUZZ_CORPUS`` with ``suffix`` on
    its name, a directory beside it rather than in it, since libFuzzer reads a corpus with its
    subdirectories; a temporary directory without it."""
    kept = os.environ.get("FSBPYR_FUZZ_CORPUS")
    if not kept:
        return tmp_path / "found"
    return Path(kept).with_name(Path(kept).name + suffix)


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
    kept = found(tmp_path)  # new inputs the run keeps; the corpus stays as built
    kept.mkdir(parents=True, exist_ok=True)

    result = _fuzz(harness, f"-max_total_time={FUZZ_SECONDS}", "-rss_limit_mb=2048",
                   "-timeout=30", f"-max_len={1 << 20}", f"-artifact_prefix={tmp_path}/",
                   os.fspath(kept), os.fspath(corpus))  # fmt: skip

    assert result.returncode == 0, result.stderr[-4000:]
    assert "Done " in result.stderr


@pytest.fixture(scope="module")
def sig_harness(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("sig-harness") / "sig_fuzz"
    flags = ("-DFSB_SIG_NO_PYTHON", "-fsanitize=fuzzer,address,undefined",
             "-fno-sanitize-recover=undefined")  # fmt: skip
    return launcher_build.compile(out, [SIG_HARNESS, "sig.c", "integrity.c", "parallel.c"], *flags)


def test_the_readers_of_keys_survive_a_fuzzing_run(sig_harness: Path, tmp_path: Path) -> None:
    corpus, kept = tmp_path / "corpus", found(tmp_path, "-sig")
    corpus.mkdir()
    kept.mkdir(parents=True, exist_ok=True)
    (corpus / "pub").write_bytes(SIG_FIXED.read_bytes())
    blocks = []
    for algorithm in ("ED25519", "ML-DSA-65"):
        plain = run_openssl("genpkey", "-algorithm", algorithm)
        blocks.append(run_openssl("pkcs8", "-topk8", "-v2", "aes-256-cbc", "-v2prf",
                                  "hmacWithSHA256", "-iter", "1", "-passout", "pass:p",
                                  stdin=plain))  # fmt: skip
    (corpus / "key").write_bytes(b"".join(blocks))

    result = _fuzz(sig_harness, f"-max_total_time={sig_fuzz_seconds(FUZZ_SECONDS)}",
                   "-rss_limit_mb=2048", "-timeout=30", f"-max_len={1 << 14}",
                   f"-artifact_prefix={tmp_path}/", os.fspath(kept), os.fspath(corpus))  # fmt: skip

    assert result.returncode == 0, result.stderr[-4000:]
    assert "Done " in result.stderr


@pytest.mark.parametrize(("seconds", "sig"), [(30, 30), (600, 150), (1200, 300), (10, 10)])
def test_the_readers_of_keys_explore_a_quarter_of_the_time_and_30_seconds_at_least(
    seconds: int, sig: int
) -> None:
    assert sig_fuzz_seconds(seconds) == sig


def test_each_harness_keeps_what_it_finds_apart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FSBPYR_FUZZ_CORPUS", "/kept/fuzz-corpus/")
    assert found(tmp_path) == Path("/kept/fuzz-corpus")
    assert found(tmp_path, "-sig") == Path("/kept/fuzz-corpus-sig")
    monkeypatch.delenv("FSBPYR_FUZZ_CORPUS")
    assert found(tmp_path) == found(tmp_path, "-sig") == tmp_path / "found"


def test_the_full_suite_caches_what_both_harnesses_find(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each directory a cache of its own, restored from the last run's of the same entry."""
    workflow = (Path(__file__).parents[2] / ".github/workflows/full-suite.yml").read_text()
    monkeypatch.setenv("FSBPYR_FUZZ_CORPUS", re.findall(r"FSBPYR_FUZZ_CORPUS=(\S+)", workflow)[0])
    caches = re.findall(r"uses: actions/cache@.*\n +with:\n +path: (\S+)\n +key: (.+)\n"
                        r" +restore-keys: (.+)\n", workflow)  # fmt: skip

    assert {path for path, _, _ in caches} == {str(found(tmp_path)), str(found(tmp_path, "-sig"))}
    assert len({key.rpartition("-")[0] for _, key, _ in caches}) == len(caches) == 2
    assert all(key.startswith(restore) for _, key, restore in caches)
