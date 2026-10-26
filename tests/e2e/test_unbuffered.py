"""PYTHONUNBUFFERED, the one PYTHON* variable an app reads (D14): set and non-empty, stdout and
stderr are unbuffered as CPython has them, so a line printed to a pipe is there at once and a
SIGTERM does not lose it; otherwise a pipe is block-buffered, as `python` has it. JOURNAL_STREAM
naming the stdout pipe makes it line-buffered (D14)."""

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
    """What is readable from the pipe within ``seconds`` (nothing if no byte arrives), up to
    the end of the first line: print() writes "line" and "\\n" apart, and unbuffered, they
    reach the pipe as two writes that a read may separate."""
    deadline = time.monotonic() + seconds
    received = b""
    while time.monotonic() < deadline and not received.endswith(b"\n"):
        ready, _, _ = select.select([stream], [], [], 0.1)
        if ready:
            chunk = os.read(stream.fileno(), 4096)
            if not chunk:
                break
            received += chunk
    return received


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


_PARTIAL = """\
import sys, time

def main():
    print("line")
    sys.stdout.write("partial")
    time.sleep(60)
"""


@pytest.fixture(scope="module")
def partial_app(runtime: Runtime, tmp_path_factory: pytest.TempPathFactory) -> Path:
    modules, files = app_content({"x.py": _PARTIAL})
    out = tmp_path_factory.mktemp("journal") / "x"
    build_executable(runtime, App("x", "x:main", modules=modules, files=files), out)
    return out


def _start_on_pipe(
    app: Path, cache: Path, journal: str | None, unbuffered: bool
) -> tuple[subprocess.Popen[bytes], int]:
    """The app with stdout on a pipe of ours; ``journal`` is JOURNAL_STREAM's value, "pipe"
    for that pipe's own device:inode. Returns the process and the read end."""
    read, write = os.pipe()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONUNBUFFERED", "JOURNAL_STREAM")
    }
    env.update(FSBPYR_CACHE_DIR=str(cache), FSBPYR_GC="0")
    if journal == "pipe":
        status = os.fstat(write)
        env["JOURNAL_STREAM"] = f"{status.st_dev}:{status.st_ino}"
    elif journal is not None:
        env["JOURNAL_STREAM"] = journal
    if unbuffered:
        env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen([app], stdout=write, env=env)
    os.close(write)
    return process, read


def _drain(descriptor: int, seconds: float) -> bytes:
    """Everything readable from ``descriptor`` within ``seconds``."""
    data, deadline = b"", time.monotonic() + seconds
    while time.monotonic() < deadline:
        ready, _, _ = select.select([descriptor], [], [], 0.1)
        if ready and (chunk := os.read(descriptor, 4096)):
            data += chunk
    return data


def _first_line(descriptor: int) -> bytes:
    """The first read; the first run extracts, so it may take long."""
    ready, _, _ = select.select([descriptor], [], [], 30)
    return os.read(descriptor, 4096) if ready else b""


@pytest.mark.parametrize(
    ("journal", "unbuffered", "expected"),
    [
        ("pipe", False, b"line\n"),  # line-buffered: the line, not the partial one
        ("pipe", True, b"line\npartial"),  # PYTHONUNBUFFERED stays unbuffered
        ("1:2", False, b""),  # another file: block-buffered as before
        ("not a pair", False, b""),
    ],
    ids=["match", "match-and-unbuffered", "other-file", "garbage"],
)
def test_journal_stream_makes_a_matching_stdout_line_buffered(
    partial_app: Path, cache: Path, journal: str, unbuffered: bool, expected: bytes
) -> None:
    process, read = _start_on_pipe(partial_app, cache, journal, unbuffered)
    try:
        if expected:
            data = _first_line(read)
            data += _drain(read, WAIT_S)
        else:
            time.sleep(WAIT_S)  # the app has printed by now, if it is going to
            data = _drain(read, WAIT_S)
        assert data == expected
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=30) == -signal.SIGTERM
        assert _drain(read, 1) == b""  # nothing more: the block-buffered text died with it
    finally:
        process.kill()
        process.wait()
        os.close(read)
