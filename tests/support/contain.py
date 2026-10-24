"""Commands that run built apps and their venvs, contained.

A built app whose library restarts ``sys.executable`` re-runs the app: on 2026-10-24 the corpus
app sklearn grew to 1,550 processes in 90 s and took its host down (joblib's loky workers and
resource tracker; the trackers outlive their parents and block SIGTERM). ``run`` starts a
command in a session of its own and tags its environment with a token that every descendant
inherits, detached or reparented ones included. It polls the tagged processes while the
command runs, and kills them all with SIGKILL when there are more than ``limit``, when the
command times out, and when any survive the command (``Runaway``, ``subprocess.TimeoutExpired``
and ``Leftover``; a test fails on each). The command also runs under an RLIMIT_NPROC a little
above the machine's current task count (prlimit), in case polling falls behind.
"""

import contextlib
import os
import shutil
import signal
import subprocess
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

LIMIT = 64  # tagged processes at once: a pool of workers fits, a restarting app does not
HEADROOM = 1024  # tasks (threads count) the command may add to the machine's
POLL = 0.2  # seconds between looks at the tagged processes
GRACE = 2.0  # seconds a descendant may take to exit after the command did
_TAG = "FSBPYR_TEST_CONTAINED"
_PRLIMIT = shutil.which("prlimit")

type Command = Sequence[str | Path]


class Runaway(AssertionError):
    """The command started more processes than its limit allows; all were killed."""


class Leftover(AssertionError):
    """Processes of the command survived it; they were killed."""


def _tagged(token: bytes) -> list[int]:
    """This user's processes that carry ``token`` in their environment."""
    uid, found = os.getuid(), []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid == uid and token in Path(entry.path, "environ").read_bytes():
                found.append(int(entry.name))
        except OSError:  # gone, a zombie, or not ours
            continue
    return found


def _describe(pids: list[int], shown: int = 5) -> str:
    lines = []
    for pid in pids[:shown]:
        try:
            argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        lines.append(f"  {pid}: {b' '.join(argv).decode(errors='replace').strip()[:200]}")
    return "\n".join(lines)


def _kill(token: bytes, session: int) -> None:
    """SIGKILL (SIGTERM may be blocked) for the session and every tagged process, until none
    is left: a process may fork while the others are killed."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(session, signal.SIGKILL)
    deadline = time.monotonic() + 10
    while (pids := _tagged(token)) and time.monotonic() < deadline:
        for pid in pids:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
        time.sleep(0.05)


def _task_cap() -> int:
    """The machine's task count (all users, threads included) plus HEADROOM: RLIMIT_NPROC
    counts the real user's tasks at every fork and thread start."""
    total = int(Path("/proc/loadavg").read_text().split()[3].partition("/")[2])
    return total + HEADROOM


def run(
    command: Command,
    *,
    env: Mapping[str, str],
    cwd: Path | None = None,
    stdin: str | None = None,
    timeout: float,
    limit: int = LIMIT,
) -> subprocess.CompletedProcess[str]:
    """Runs ``command`` like ``subprocess.run(..., capture_output=True, text=True)``, contained
    as the module says; ``stdin`` is its input, or it inherits the caller's."""
    token = uuid.uuid4().hex
    tag = f"{_TAG}={token}".encode()
    prefix = [_PRLIMIT, f"--nproc={_task_cap()}", "--"] if _PRLIMIT else []
    process = subprocess.Popen(
        [*prefix, *command], env={**env, _TAG: token}, cwd=cwd, text=True,
        stdin=subprocess.PIPE if stdin is not None else None, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True,
    )  # fmt: skip
    deadline, exited = time.monotonic() + timeout, None
    problem: Exception | None = None
    pending = stdin
    while True:
        try:
            stdout, stderr = process.communicate(pending, timeout=POLL)
            break
        except subprocess.TimeoutExpired:
            pending = None  # communicate keeps what it was given the first time
        pids = _tagged(tag)
        if len(pids) > limit:
            problem = Runaway(f"{len(pids)} processes (limit {limit}), all killed:\n"
                              f"{_describe(pids)}")  # fmt: skip
        elif time.monotonic() > deadline:
            problem = subprocess.TimeoutExpired(command, timeout)
        elif process.poll() is not None:  # the command exited; descendants hold its pipes
            exited = exited or time.monotonic()
            if time.monotonic() - exited > GRACE:
                problem = Leftover(f"processes outlived the command, killed:\n{_describe(pids)}")
        if problem is not None:
            _kill(tag, process.pid)
            stdout, stderr = process.communicate()
            raise problem
    if pids := _wait_for_descendants(tag):
        _kill(tag, process.pid)
        raise Leftover(f"processes outlived the command, killed:\n{_describe(pids)}\n"
                       f"stdout: {stdout}\nstderr: {stderr}")  # fmt: skip
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _wait_for_descendants(tag: bytes) -> list[int]:
    """The tagged processes still alive GRACE seconds after the command ended (none, usually:
    a resource tracker exits when its pipe closes)."""
    deadline = time.monotonic() + GRACE
    while (pids := _tagged(tag)) and time.monotonic() < deadline:
        time.sleep(0.05)
    return pids
