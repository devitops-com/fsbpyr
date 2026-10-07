"""Phase 5 exit criteria: native extensions from real wheels (fixture ``heavy``: numpy, orjson,
cryptography, pydantic), the lazy native cache, self-healing and concurrency.

Needs the cached runtime and, on the first run, network access for the fixture's wheels.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from fsbpyr.cli import main
from fsbpyr.payload.reader import Executable
from fsbpyr.runtime.registry import Runtime

pytestmark = pytest.mark.slow

FIXTURE = Path(__file__).parents[1] / "fixtures" / "projects" / "heavy"
HEAVY_DISTS = {"numpy", "orjson", "cryptography", "cffi", "pydantic", "pydantic-core"}
CONCURRENT_RUNS = 32
EXPECTED = {
    "np": "numpy [3, 5, 7] 1.0",
    "json": 'orjson {"a":[1,2]}',
    "crypto": "cryptography secret",
    "model": "pydantic {'x': 1, 'y': 2}",
}


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


def _run(executable: Path, cache: Path, *args: str) -> tuple[str, dict[str, object]]:
    process = _start(executable, cache, *args)
    stdout, stderr = process.communicate(timeout=120)
    assert process.returncode == 0, stderr
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


@pytest.mark.parametrize("command", sorted(EXPECTED))
def test_every_subcommand_runs(heavy: Path, tmp_path: Path, command: str) -> None:
    stdout, _ = _run(heavy, tmp_path / "cache", command)

    assert stdout.strip() == EXPECTED[command]


def test_concurrent_first_runs_all_succeed(heavy: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    processes = [_start(heavy, cache, "np") for _ in range(CONCURRENT_RUNS)]

    results = [(p.communicate(timeout=300), p.returncode) for p in processes]

    assert all(code == 0 for _, code in results), [err for (_, err), _ in results][:3]
    assert {out.strip() for (out, _), _ in results} == {EXPECTED["np"]}
    assert not [p for p in cache.rglob(".*")]  # no temporary files left behind


def test_damaged_cached_extension_is_repaired(heavy: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _run(heavy, cache, "np")
    (target,) = cache.rglob("_multiarray_umath.*.so")
    original = target.read_bytes()
    target.chmod(0o700)
    target.write_bytes(b"\0" * len(original))  # same size: only a failed dlopen reveals it

    stdout, trace = _run(heavy, cache, "np")

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
