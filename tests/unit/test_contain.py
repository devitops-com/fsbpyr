"""tests/support/contain.py: commands that outlive their test, grow without end or hang are
killed, all their processes with them, and the test fails. Every command here stops by itself
(the runaway at 127 processes), so a broken containment cannot take the machine down."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from ..support import contain

ENV = dict(os.environ)


def _alive(marker: str) -> list[int]:
    """Processes whose command line carries ``marker``."""
    found = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                if marker.encode() in (entry / "cmdline").read_bytes():
                    found.append(int(entry.name))
            except OSError:
                continue
    return found


def _python(code: str, *args: str) -> list[str]:
    return [sys.executable, "-I", "-c", code, *args]


def test_a_command_runs_as_subprocess_run_would() -> None:
    code = "import sys; print(sys.stdin.read().upper()); print('err', file=sys.stderr); exit(3)"

    result = contain.run(_python(code), env=ENV, stdin="text", timeout=30)

    assert (result.returncode, result.stdout, result.stderr) == (3, "TEXT\n", "err\n")


def test_a_detached_process_that_outlives_the_command_is_killed() -> None:
    marker = f"contain-{uuid.uuid4().hex}"
    sleeper = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', {marker!r}], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"  # noqa: E501

    with pytest.raises(contain.Leftover, match="outlived the command"):
        contain.run(_python(sleeper), env=ENV, timeout=30)

    assert _alive(marker) == []


def test_a_descendant_holding_the_pipes_is_killed_after_the_grace_time() -> None:
    marker = f"contain-{uuid.uuid4().hex}"
    holder = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', {marker!r}])"  # noqa: E501

    with pytest.raises(contain.Leftover):
        contain.run(_python(holder), env=ENV, timeout=30)

    assert _alive(marker) == []


def test_a_timeout_kills_every_process_even_those_blocking_sigterm() -> None:
    marker = f"contain-{uuid.uuid4().hex}"
    sleeper = f"[sys.executable, '-c', 'import time; time.sleep(60)', {marker!r}]"
    blocker = ("import signal, subprocess, sys, time; "
               "signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM}); "
               f"subprocess.Popen({sleeper}); time.sleep(60)")  # fmt: skip

    with pytest.raises(subprocess.TimeoutExpired):
        contain.run(_python(blocker, marker), env=ENV, timeout=1)

    assert _alive(marker) == []


def test_a_command_restarting_itself_is_stopped_at_the_limit(tmp_path: Path) -> None:
    marker = f"contain-{uuid.uuid4().hex}"
    bomb = tmp_path / "bomb.py"  # two children per level, six levels: 127 processes at most
    bomb.write_text(
        "import subprocess, sys, time\n"
        "depth = int(sys.argv[1])\n"
        "if depth:\n"
        "    for _ in range(2):\n"
        "        subprocess.Popen([sys.executable, __file__, str(depth - 1), sys.argv[2]])\n"
        "time.sleep(60)\n"
    )

    with pytest.raises(contain.Runaway, match="limit 16") as raised:
        contain.run([sys.executable, "-I", str(bomb), "6", marker], env=ENV, timeout=60, limit=16)

    assert "bomb.py" in str(raised.value)  # names the processes
    assert _alive(marker) == []
