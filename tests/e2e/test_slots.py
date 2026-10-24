"""The launcher's instance limit (D45, launcher/slots.c), probed (tests/probes/slots.c): a start
holds one of the executable's slots while it runs and is refused when every one is taken; the
kernel frees a slot when its holder ends, however it ends; a forked child shares its parent's,
an exec gives it up; each executable has slots of its own; a slot file that is not ours alone,
or none, leaves starts unguarded; the limit and its override."""

import os
import signal
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from .conftest import LauncherBuild

pytestmark = pytest.mark.slow

PROBE = Path(__file__).parents[1] / "probes" / "slots.c"
REFUSED = 70
CPUS = len(os.sched_getaffinity(0))


@pytest.fixture(scope="module")
def probe(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("probe") / "slots"
    flags = ("-fsanitize=undefined", "-fno-sanitize-recover=undefined")
    return launcher_build.compile(out, [PROBE, "slots.c"], *flags)


def _env(**env: str) -> dict[str, str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("FSBPYR_")}
    return {**clean, **env}


class Starts:
    """Starts of the probe that hold their slot until the test ends (or ``release``)."""

    def __init__(self, probe: Path) -> None:
        self.probe = probe
        self.running: list[subprocess.Popen[str]] = []

    def start(self, *dirs: Path | str, mode: str = "hold", ino: int = 7, limit: int = 3,
              **env: str) -> tuple[subprocess.Popen[str], list[str]]:  # fmt: skip
        """The start and what it printed: ["held"] or ["unguarded"], or [] if it was refused."""
        process = subprocess.Popen(
            [self.probe, mode, str(ino), *map(str, dirs)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=_env(FSBPYR_MAX_INSTANCES=str(limit), **env),
        )  # fmt: skip
        self.running.append(process)
        assert process.stdout is not None
        lines = [process.stdout.readline().strip() for _ in range(2 if mode == "exec" else 1)]
        return process, [line for line in lines if line]

    def release(self, process: subprocess.Popen[str]) -> None:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=10)

    def close(self) -> None:
        """Ends every start; closing its stdin also ends a forked child holding it (3.12's
        communicate() fails on a stdin already closed)."""
        for process in self.running:
            process.kill()
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


@pytest.fixture
def starts(probe: Path) -> Iterator[Starts]:
    started = Starts(probe)
    yield started
    started.close()


def _refused(process: subprocess.Popen[str]) -> str:
    """What a refused start printed on stderr; fails if it was not refused."""
    _, stderr = process.communicate(timeout=10)
    assert process.returncode == REFUSED, stderr
    return stderr


def _held_eventually(starts: Starts, *dirs: Path, limit: int, within: float = 5) -> bool:
    """Whether a start gets a slot before ``within`` seconds pass (a slot freed by a process
    that is ending)."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        process, lines = starts.start(*dirs, limit=limit)
        if lines == ["held"]:
            return True
        _refused(process)
        time.sleep(0.05)
    return False


def test_a_start_over_the_limit_is_refused_until_a_slot_is_freed(
    starts: Starts, tmp_path: Path
) -> None:
    holders = [starts.start(tmp_path, limit=3) for _ in range(3)]
    assert [lines for _, lines in holders] == [["held"]] * 3

    over, lines = starts.start(tmp_path, limit=3)

    assert lines == []
    stderr = _refused(over)
    assert "fsbpyr: refusing to start: 3 copies of this app are running already" in stderr
    assert "FSBPYR_MAX_INSTANCES" in stderr
    holders[0][0].send_signal(signal.SIGKILL)  # SIGTERM may be blocked: the kernel frees it
    holders[0][0].wait()
    assert starts.start(tmp_path, limit=3)[1] == ["held"]
    starts.release(holders[1][0])  # and when a holder ends by itself
    assert starts.start(tmp_path, limit=3)[1] == ["held"]
    assert _refused(starts.start(tmp_path, limit=3)[0])


def test_of_many_starts_at_once_only_the_limit_run(probe: Path, tmp_path: Path) -> None:
    command = [str(probe), "hold", "7", str(tmp_path)]
    processes = [subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True,
                                  env=_env(FSBPYR_MAX_INSTANCES="8"))
                 for _ in range(40)]  # fmt: skip
    try:
        printed = [process.stdout.readline().strip() for process in processes]  # type: ignore[union-attr]
    finally:
        for process in processes:
            process.communicate(timeout=10)  # its stdin closed: a holder ends

    assert printed.count("held") == 8
    assert [process.returncode for process in processes].count(REFUSED) == 32


def test_a_forked_child_keeps_its_parents_slot(starts: Starts, tmp_path: Path) -> None:
    parent, lines = starts.start(tmp_path, mode="fork", limit=1)
    assert lines == ["held"]
    assert parent.wait(timeout=10) == 0

    assert _refused(starts.start(tmp_path, limit=1)[0])  # the child holds it: one copy, one slot
    starts.release(parent)  # its stdin is the child's: the child ends
    assert _held_eventually(starts, tmp_path, limit=1)


def test_an_exec_gives_the_slot_up(starts: Starts, tmp_path: Path) -> None:
    """The descriptor is O_CLOEXEC: an app executing itself takes a slot again, not two."""
    process, lines = starts.start(tmp_path, mode="exec", limit=1)

    assert lines == ["held", "held"]
    assert _refused(starts.start(tmp_path, limit=1)[0])
    assert process.poll() is None


def test_the_slot_is_never_a_standard_descriptor(probe: Path, tmp_path: Path) -> None:
    """Started with stdin and stderr closed, the slot file would be opened as 0: Python would
    then take it for stdin, or for stderr and write into it."""
    result = subprocess.run([probe, "low", "7", str(tmp_path)], capture_output=True, text=True,
                            check=True, timeout=10, env=_env(FSBPYR_MAX_INSTANCES="3"))  # fmt: skip

    (fd,) = result.stdout.split()
    assert int(fd) >= 3


def test_every_executable_has_slots_of_its_own(starts: Starts, tmp_path: Path) -> None:
    assert starts.start(tmp_path, ino=7, limit=1)[1] == ["held"]

    assert starts.start(tmp_path, ino=8, limit=1)[1] == ["held"]
    assert _refused(starts.start(tmp_path, ino=7, limit=1)[0])


def _make(path: Path, kind: str) -> None:
    """Something in the slot file's place that is not a file of ours alone."""
    if kind == "open to the group":
        path.write_bytes(b"")
        path.chmod(0o640)
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        (path.parent / "elsewhere").write_bytes(b"")
        (path.parent / "elsewhere").chmod(0o600)
        path.symlink_to(path.parent / "elsewhere")
    else:  # someone else's: needs root to make
        path.write_bytes(b"")
        path.chmod(0o600)
        try:
            os.chown(path, 65534, 65534)
        except OSError:
            pytest.skip("making a file someone else's needs root")


@pytest.mark.parametrize("kind", ["open to the group", "fifo", "directory", "symlink",
                                  "someone else's"])  # fmt: skip
def test_a_slot_file_not_ours_alone_is_passed_over(
    starts: Starts, tmp_path: Path, kind: str
) -> None:
    bad, good = tmp_path / "bad", tmp_path / "good"
    bad.mkdir()
    good.mkdir()
    _make(bad / f"fsbpyr-{os.geteuid()}", kind)

    alone = [starts.start(bad, limit=1)[1] for _ in range(2)]
    assert alone == [["unguarded"], ["unguarded"]]  # never refused for the guard's own sake
    assert starts.start(bad, good, limit=1)[1] == ["held"]  # the next directory's file
    assert _refused(starts.start(bad, good, limit=1)[0])


def test_without_a_usable_directory_starts_are_unguarded(starts: Starts, tmp_path: Path) -> None:
    dirs = (tmp_path / "missing", "relative", tmp_path / ("x" * 5000))

    assert [starts.start(*dirs, limit=1)[1] for _ in range(2)] == [["unguarded"]] * 2
    assert not list(tmp_path.iterdir())


def test_the_slot_file_is_private(starts: Starts, tmp_path: Path) -> None:
    starts.start(tmp_path, limit=1)

    made = tmp_path / f"fsbpyr-{os.geteuid()}"
    assert made.stat().st_mode & 0o777 == 0o600
    assert made.stat().st_size == 0  # locks past the end: the file never grows


def test_a_background_collection_over_the_limit_ends_quietly(
    starts: Starts, tmp_path: Path
) -> None:
    starts.start(tmp_path, limit=1)

    process, lines = starts.start(tmp_path, limit=1, FSBPYR_GC="background")

    assert lines == []
    _, stderr = process.communicate(timeout=10)
    assert (process.returncode, stderr) == (0, "")  # it can wait for another day


def _limit(probe: Path, cpus: set[int] | None = None, **env: str) -> int:
    result = subprocess.run(
        [probe, "limit"], capture_output=True, text=True, check=True, timeout=10,
        env=_env(**env), preexec_fn=(lambda: os.sched_setaffinity(0, cpus)) if cpus else None,
    )  # fmt: skip
    return int(result.stdout)


@pytest.mark.parametrize(
    ("setting", "expected"),
    [("3", 3), ("0", 0), ("4096", 4096), ("9999999", 4096), ("0012", 12)],
)
def test_the_override_sets_the_limit(probe: Path, setting: str, expected: int) -> None:
    assert _limit(probe, FSBPYR_MAX_INSTANCES=setting) == expected


@pytest.mark.parametrize("setting", ["", "-1", " 5", "5 ", "12x", "+3", "12345678", "1e3"])
def test_a_malformed_override_leaves_the_default(probe: Path, setting: str) -> None:
    assert _limit(probe, FSBPYR_MAX_INSTANCES=setting) == _limit(probe)


def test_the_default_limit_is_four_per_cpu_and_sixteen(probe: Path) -> None:
    assert _limit(probe) == min(max(4 * CPUS + 16, 64), 1024)
    assert _limit(probe, cpus={min(os.sched_getaffinity(0))}) == 64  # at least 64


def test_limit_0_turns_the_guard_off(starts: Starts, tmp_path: Path) -> None:
    assert [starts.start(tmp_path, limit=0)[1] for _ in range(2)] == [["unguarded"]] * 2
    assert not list(tmp_path.iterdir())
