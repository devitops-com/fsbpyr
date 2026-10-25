"""PYTHONUNBUFFERED, the one PYTHON* variable an app reads (D14): set and non-empty, stdout and
stderr are unbuffered as CPython has them, so a line printed to a pipe is there at once and a
SIGTERM does not lose it; otherwise a pipe is block-buffered, as `python` has it."""

import os
import select
import signal
import subprocess
import time
from pathlib import Path
from typing import IO

import pytest

from fsbpyr.assemble import App, build_executable
from fsbpyr.runtime.registry import Runtime

from .conftest import app_content

pytestmark = pytest.mark.slow
_PRINTS = """\
import sys, time

def main():
    print("line")
    print("err", file=sys.stderr)
    time.sleep(60)
"""
WAIT_S = 3


@pytest.fixture(scope="module")
def app(runtime: Runtime, tmp_path_factory: pytest.TempPathFactory) -> Path:
    modules, files = app_content({"x.py": _PRINTS})
    out = tmp_path_factory.mktemp("unbuffered") / "x"
    build_executable(runtime, App("x", "x:main", modules=modules, files=files), out)
    return out


@pytest.fixture(scope="module")
def cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("cache")  # shared: only the first start extracts


def _start(app: Path, cache: Path, variable: str | None) -> subprocess.Popen[bytes]:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONUNBUFFERED"}
    env.update(FSBPYR_CACHE_DIR=str(cache), FSBPYR_GC="0")
    if variable is not None:
        env["PYTHONUNBUFFERED"] = variable
    return subprocess.Popen([app], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def _read_within(stream: IO[bytes], seconds: float) -> bytes:
    """What is readable from the pipe within ``seconds`` (nothing if no byte arrives)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ready, _, _ = select.select([stream], [], [], 0.1)
        if ready:
            return os.read(stream.fileno(), 4096)
    return b""


@pytest.mark.parametrize("variable", ["1", "x"])
def test_a_line_reaches_a_pipe_before_the_app_exits_and_survives_sigterm(
    app: Path, cache: Path, variable: str
) -> None:
    process = _start(app, cache, variable)
    try:
        assert process.stdout is not None and process.stderr is not None
        assert _read_within(process.stdout, 30) == b"line\n"  # the first run extracts: slow
        assert _read_within(process.stderr, WAIT_S) == b"err\n"
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=30) == -signal.SIGTERM
    finally:
        process.kill()
        process.communicate()


@pytest.mark.parametrize("variable", [None, ""], ids=["unset", "empty"])
def test_without_it_a_pipe_is_block_buffered(
    app: Path, cache: Path, variable: str | None
) -> None:
    process = _start(app, cache, variable)
    try:
        assert process.stdout is not None
        time.sleep(WAIT_S)  # the app has printed by now, if it is going to
        assert _read_within(process.stdout, WAIT_S) == b""
        process.send_signal(signal.SIGTERM)
        out, _ = process.communicate(timeout=30)
        assert out == b""  # lost with the process, as `python` loses it
    finally:
        process.kill()
        process.communicate()
