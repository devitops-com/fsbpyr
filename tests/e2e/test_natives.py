"""Phase 5 exit criteria: native extensions from real wheels (fixture ``heavy``: numpy, orjson,
cryptography, pydantic, onnxruntime), the lazy native cache, self-healing and concurrency.

Needs the cached runtime and, on the first run, network access for the fixture's wheels.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fsbpyr.boot._fsbpyr_rt import gc
from fsbpyr.cli import main
from fsbpyr.payload.format import NATIVE_KINDS, Codec
from fsbpyr.payload.reader import Executable
from fsbpyr.runtime.registry import Runtime

from ..support.stderr import NO_HOSTNAME, TRACE_LINE, noise

pytestmark = pytest.mark.slow

FIXTURE = Path(__file__).parents[1] / "fixtures" / "projects" / "heavy"
HEAVY_DISTS = {"numpy", "orjson", "cryptography", "cffi", "pydantic", "pydantic-core"}
CONCURRENT_RUNS = 32
EXPECTED = {
    "np": "numpy [3, 5, 7] 1.0",
    "json": 'orjson {"a":[1,2]}',
    "crypto": "cryptography secret",
    "model": "pydantic {'x': 1, 'y': 2}",
    "onnx": "onnxruntime [[4.0, 3.0]]",
}

_RESTORING = re.compile(r"^fsbpyr: .* was modified after extraction; restoring it\n", re.MULTILINE)


def _build(project: Path, out: Path) -> Path:
    assert main(["build", str(project), "-o", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> Path:
    copy = tmp_path_factory.mktemp("heavy") / "heavy"
    shutil.copytree(FIXTURE, copy)
    return copy


@pytest.fixture(scope="module")
def heavy(project: Path) -> Path:
    return _build(project, project.parent / "bin" / "heavy")


def _start(executable: Path, cache: Path, *args: str) -> subprocess.Popen[str]:
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(cache), "FSBPYR_TRACE": "1"}
    return subprocess.Popen(
        [executable, *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def _run(
    executable: Path, cache: Path, *args: str, allowed: tuple[re.Pattern[str], ...] = ()
) -> tuple[str, dict[str, object]]:
    process = _start(executable, cache, *args)
    stdout, stderr = process.communicate(timeout=120)
    assert process.returncode == 0, stderr
    assert noise(stderr, TRACE_LINE, NO_HOSTNAME, *allowed) == ""
    return stdout, _trace(stderr)


def _trace(stderr: str) -> dict[str, object]:
    lines = [line for line in stderr.splitlines() if line.startswith("fsbpyr-trace ")]
    assert len(lines) == 1, stderr
    report: dict[str, object] = json.loads(lines[0].removeprefix("fsbpyr-trace "))
    return report


def test_help_loads_and_extracts_nothing_native(heavy: Path, tmp_path: Path) -> None:
    stdout, trace = _run(heavy, tmp_path / "cache", "--help")

    assert "Light CLI, heavy optional deps." in stdout
    assert HEAVY_DISTS.isdisjoint(trace["modules"])  # type: ignore[arg-type]
    assert trace["extracted"] == []
    assert not (tmp_path / "cache").exists()


def test_first_run_extracts_only_the_closure_it_needs(heavy: Path, tmp_path: Path) -> None:
    stdout, first = _run(heavy, tmp_path / "cache", "np")

    assert stdout.strip() == EXPECTED["np"]
    extracted: list[str] = first["extracted"]  # type: ignore[assignment]
    assert extracted and all(p.startswith(("numpy/", "numpy.libs/")) for p in extracted)
    assert any(p.startswith("numpy.libs/libscipy_openblas") for p in extracted)
    assert HEAVY_DISTS.intersection(first["modules"]) == {"numpy"}  # type: ignore[arg-type]

    _, second = _run(heavy, tmp_path / "cache", "np")
    assert second["extracted"] == []


# decompressed_bytes of `heavy np` on the first run, with a little room (D37: counts, not
# timings). The decoder's block cache (launcher/payload.c BLOCK_CACHE) decodes a block again
# when it was evicted; a cache too small for the imports shows up here.
NP_DECOMPRESSED_MAX = 44_000_000


def test_a_run_decompresses_no_more_than_recorded(heavy: Path, tmp_path: Path) -> None:
    _, trace = _run(heavy, tmp_path / "cache", "np")

    assert trace["decompressed_bytes"] <= NP_DECOMPRESSED_MAX, trace  # type: ignore[operator]


@pytest.mark.parametrize("command", sorted(EXPECTED))
def test_every_subcommand_runs(heavy: Path, tmp_path: Path, command: str) -> None:
    stdout, _ = _run(heavy, tmp_path / "cache", command)

    assert stdout.strip() == EXPECTED[command]


@pytest.fixture(scope="module")
def heavy_xz(project: Path) -> Path:
    out = project.parent / "bin" / "heavy-xz"
    assert main(["build", str(project), "-o", str(out), "--best-compression"]) == 0
    return out


def test_best_compression_stores_natives_as_xz_and_they_run(
    heavy: Path, heavy_xz: Path, tmp_path: Path
) -> None:
    """D33: every native file is xz, in pieces when larger than XZ_PIECE (raw when that is not
    smaller), and smaller overall."""
    with Executable.open(heavy_xz) as exe:
        payload = exe.payload
        codecs = {payload.blob(f.blob).codec for f in payload.files() if f.kind in NATIVE_KINDS}
        info = json.loads(payload.build_info)
    assert {Codec.XZ, Codec.PIECES} <= codecs <= {Codec.XZ, Codec.PIECES, Codec.NONE}
    assert info["native_compression"] == "xz -6"
    assert heavy_xz.stat().st_size < heavy.stat().st_size

    for command in ("np", "onnx", "crypto"):
        stdout, trace = _run(heavy_xz, tmp_path / "cache", command)
        assert stdout.strip() == EXPECTED[command]
    assert trace["extracted"]


def test_libraries_extensions_open_by_name_are_extracted_with_them(
    heavy: Path, tmp_path: Path
) -> None:
    """onnxruntime opens libonnxruntime_providers_shared.so next to its extension by name."""
    process = _start(heavy, tmp_path / "cache", "onnx")
    _, stderr = process.communicate(timeout=120)

    assert process.returncode == 0, stderr
    assert noise(stderr, TRACE_LINE, NO_HOSTNAME) == ""
    extracted: list[str] = _trace(stderr)["extracted"]  # type: ignore[assignment]
    assert "onnxruntime/capi/libonnxruntime_providers_shared.so" in extracted
    assert not any(p.startswith("onnxruntime/capi/libonnxruntime.so") for p in extracted)


def test_concurrent_first_runs_all_succeed(heavy: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    processes = [_start(heavy, cache, "np") for _ in range(CONCURRENT_RUNS)]

    results = [(p.communicate(timeout=300), p.returncode) for p in processes]

    assert all(code == 0 for _, code in results), [err for (_, err), _ in results][:3]
    assert {out.strip() for (out, _), _ in results} == {EXPECTED["np"]}
    ours = (gc.MARKER, gc.LOCK, gc.SCRATCH)  # the cache's marker, the tree's lock and scratch
    assert not [p for p in cache.rglob(".*") if p.name not in ours]
    assert not list(cache.rglob(f"{gc.SCRATCH}/*"))  # no temporary files left behind


def test_damaged_cached_extension_is_repaired(heavy: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _run(heavy, cache, "np")
    (target,) = cache.rglob("_multiarray_umath.*.so")
    original = target.read_bytes()
    target.chmod(0o700)
    target.write_bytes(b"\0" * len(original))  # same size: the hash before loading reveals it

    stdout, trace = _run(heavy, cache, "np", allowed=(_RESTORING,))

    assert stdout.strip() == EXPECTED["np"]
    assert target.read_bytes() == original
    assert [p for p in trace["extracted"] if "_multiarray_umath" in p]  # type: ignore[attr-defined]


def test_python_only_change_reuses_the_native_generation(
    heavy: Path, project: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    _run(heavy, cache, "np")
    cli = project / "src" / "heavy" / "cli.py"
    cli.write_text(cli.read_text().replace("Light CLI", "Light, changed CLI"))
    changed = _build(project, tmp_path / "heavy2")

    stdout, trace = _run(changed, cache, "np")

    with Executable.open(heavy) as old, Executable.open(changed) as new:
        assert old.payload.generation_id == new.payload.generation_id
    assert stdout.strip() == EXPECTED["np"]
    assert trace["extracted"] == []
    assert "Light, changed CLI" in _run(changed, cache, "--help")[0]
