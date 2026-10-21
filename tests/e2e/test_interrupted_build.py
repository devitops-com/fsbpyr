"""A build that is killed with SIGTERM, or runs out of room for the executable, leaves the
output as it was and no temporary beside it (BLD-1 covers a failed write; these are the
signal and the file-size limit, with a real `fsbpyr build` process)."""

import resource
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from fsbpyr.build import TARGET_PYTHON
from fsbpyr.runtime.registry import Runtime

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "projects" / "hello"
OLD = b"#!/bin/sh\necho the working tool\n"
MARKER = "FSBPYR_TEST_MARKER"
WAIT = 600  # seconds the slowest runner may need to reach the smoke run: a bound, not a delay
# Smaller than the executable (the stub alone is megabytes), larger than the files the rest of
# the build writes (the staged sources, uv's metadata)
FSIZE_LIMIT = 1 << 20


@pytest.fixture(autouse=True)
def _runtime_available(runtime: Runtime) -> None:
    """Every test here needs the cached runtime (built once per session if missing)."""


def _project(tmp_path: Path) -> Path:
    """The hello fixture, whose `--wait` argument writes $FSBPYR_TEST_MARKER and then waits:
    the smoke run of the new executable is where the build stands while it is killed."""
    project = tmp_path / "hello"
    shutil.copytree(FIXTURE, project)
    cli = project / "src" / "hello" / "cli.py"
    cli.write_text(
        cli.read_text().replace(
            "def main() -> int:\n",
            "def main() -> int:\n    if sys.argv[1:] == ['--wait']:\n"
            f"        open(__import__('os').environ['{MARKER}'], 'w').close()\n"
            "        __import__('time').sleep(600)\n",
        )
    )
    return project


def _old_output(tmp_path: Path) -> Path:
    out = tmp_path / "bin" / "hello"
    out.parent.mkdir()
    out.write_bytes(OLD)
    return out


def _build(
    project: Path, out: Path, *args: str, preexec: Callable[[], None] | None = None
) -> subprocess.Popen[str]:
    command = [sys.executable, "-m", "fsbpyr", "build", str(project), "-o", str(out)]
    command += ["--python", TARGET_PYTHON, *args]
    return subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=preexec,  # noqa: PLW1509  only sets limits
    )


def test_a_build_killed_with_sigterm_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, out = _project(tmp_path), _old_output(tmp_path)
    marker = tmp_path / "running"
    monkeypatch.setenv(MARKER, str(marker))
    build = _build(project, out, "--smoke-args", "--wait")
    try:
        deadline = time.monotonic() + WAIT
        while not marker.exists():
            assert build.poll() is None, build.communicate()
            assert time.monotonic() < deadline, "the smoke run did not start"
            time.sleep(0.05)
        assert len(list(out.parent.iterdir())) == 2  # the new executable, beside the output

        build.send_signal(signal.SIGTERM)
        _, err = build.communicate(timeout=60)
    finally:
        build.kill()
        build.wait()

    assert build.returncode == 130 and "interrupted" in err
    assert out.read_bytes() == OLD
    assert sorted(p.name for p in out.parent.iterdir()) == ["hello"]


def _limit_file_size() -> None:
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)  # a write then fails with EFBIG, not a signal
    resource.setrlimit(resource.RLIMIT_FSIZE, (FSIZE_LIMIT, FSIZE_LIMIT))


def test_a_build_that_runs_out_of_room_fails_and_leaves_the_output_as_it_was(
    tmp_path: Path,
) -> None:
    project, out = _project(tmp_path), _old_output(tmp_path)

    build = _build(project, out, preexec=_limit_file_size)
    _, err = build.communicate(timeout=WAIT)

    assert build.returncode == 1, err
    assert "File too large" in err and "Traceback" not in err
    assert out.read_bytes() == OLD
    assert sorted(p.name for p in out.parent.iterdir()) == ["hello"]
