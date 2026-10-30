"""The launcher's trigger of cache collections (D38, launcher/due.c), probed (tests/probes/due.c):
when a run is due, how one start of many claims it, where the root is, and the process it
starts: detached from the app (none of its descriptors, its session or its directory), at idle
priority, told the root."""

import fcntl
import os
import subprocess
import time
from pathlib import Path

import pytest

from fsbpyr.boot._fsbpyr_rt import gc

from .conftest import LauncherBuild

pytestmark = pytest.mark.slow

PROBE = Path(__file__).parents[1] / "probes" / "due.c"
DAY, HOUR = 86400, 3600


@pytest.fixture(scope="module")
def probe(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("probe") / "due"
    flags = ("-Wl,--wrap=statvfs", "-fsanitize=undefined", "-fno-sanitize-recover=undefined")
    return launcher_build.compile(out, [PROBE, "due.c", "way.c"], *flags)


def _cache(root: Path, *, stamp: float | None, marked: bool = True) -> Path:
    """A cache root of ours (``marked``), last collected ``stamp`` seconds ago (None: never)."""
    root.mkdir(parents=True, exist_ok=True)
    if marked:
        (root / gc.MARKER).write_bytes(gc.MARKER_TEXT)
    if stamp is not None:
        (root / gc.STAMP).write_bytes(b"")
        os.utime(root / gc.STAMP, (time.time() - stamp,) * 2)
    return root


def _start(probe: Path, out: Path, app: str = "x", cwd: Path | None = None,
           **env: str) -> subprocess.CompletedProcess[str]:  # fmt: skip
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("FSBPYR_", "PROBE_"))}
    return subprocess.run([probe, app], capture_output=True, text=True, check=True, timeout=10,
                          cwd=cwd, env={**clean, "PROBE_OUT": str(out), **env})  # fmt: skip


def test_the_process_it_starts_has_no_signing_variable(probe: Path, tmp_path: Path) -> None:
    """It may outlive a build, and starts before Python, so before the build removes them
    (docs/signing.md §3.1)."""
    out = tmp_path / "report"
    root = _cache(tmp_path / "cache", stamp=2 * DAY)

    _start(probe, out, FSBPYR_CACHE_DIR=str(root), FSBPYR_SIGNING_KEY="/keys/k.key",
           FSBPYR_SIGNING_PASSPHRASE="a passphrase")  # noqa: S106  # fmt: skip

    reports = _reports(out)
    assert len(reports) == 1 and reports[0]["signing"] == "0"


def _claimed(root: Path) -> bool:
    """Whether a start claimed a run: the stamp was touched just now."""
    stamp = root / gc.STAMP
    return stamp.is_file() and time.time() - stamp.stat().st_mtime < 60


def _reports(out: Path, within: float = 15) -> list[dict[str, str]]:
    """The reports of the processes started to collect, once one arrived (and a while after:
    a second would come by then)."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline and not out.exists():
        time.sleep(0.05)
    time.sleep(1)
    reports: list[dict[str, str]] = []
    for line in (out.read_text() if out.exists() else "").splitlines():
        key, _, value = line.partition(" ")
        if key == "fds":  # a report's first line
            reports.append({})
        reports[-1][key] = value
    return reports


def test_a_due_run_starts_detached_at_idle_priority(probe: Path, tmp_path: Path) -> None:
    root = _cache(tmp_path / "cache", stamp=2 * DAY)
    out = tmp_path / "report"
    release = tmp_path / "release"

    kept = _start(probe, out, FSBPYR_CACHE_DIR=str(root), PROBE_RELEASE=str(release)).stdout.strip()

    # The call came back while the collector was still waiting: the launcher did not wait for it,
    # and the collector holds none of our pipes (it would hold the call, until _start's timeout).
    release.touch()
    (report,) = _reports(out)
    assert report["fds"] == "0 1 2" and kept not in report["fds"].split()
    assert [report[str(fd)] for fd in range(3)] == ["/dev/null"] * 3
    assert (report["nice"], report["ioclass"], report["own"]) == ("19", "3", "session 1")
    assert (report["cwd"], report["root"]) == ("/", str(root))
    assert _claimed(root)


@pytest.mark.parametrize(("stamp", "free", "due"), [
    (2 * DAY, "50", True),
    (2 * HOUR, "50", False),
    (2 * HOUR, "5", True),  # short of space: an hour after the last run
    (HOUR / 2, "5", False),
    (-DAY, "50", True),  # dated ahead: the clock was set back
    (None, "50", True),  # a cache of ours never collected
], ids=["a day after", "two hours after", "short of space", "half an hour after, short",
        "stamp ahead", "never collected"])  # fmt: skip
def test_when_a_run_is_due(
    probe: Path, tmp_path: Path, stamp: float | None, free: str, due: bool
) -> None:
    root = _cache(tmp_path / "cache", stamp=stamp)

    _start(probe, tmp_path / "report", FSBPYR_CACHE_DIR=str(root), PROBE_FREE=free)

    assert _claimed(root) == due
    assert (len(_reports(tmp_path / "report", within=15 if due else 0)) == 1) == due


@pytest.mark.parametrize("stamp", [2 * DAY, None], ids=["a day after", "never collected"])
def test_no_run_where_there_is_no_cache_of_ours(
    probe: Path, tmp_path: Path, stamp: float | None
) -> None:
    root = _cache(tmp_path / "theirs", stamp=stamp, marked=False)

    _start(probe, tmp_path / "report", FSBPYR_CACHE_DIR=str(root))

    assert not _claimed(root) and (stamp is not None) == (root / gc.STAMP).exists()


def test_a_marker_that_is_no_file_of_ours_starts_nothing(probe: Path, tmp_path: Path) -> None:
    root = _cache(tmp_path / "cache", stamp=2 * DAY, marked=False)
    (tmp_path / "elsewhere").write_bytes(gc.MARKER_TEXT)
    (root / gc.MARKER).symlink_to(tmp_path / "elsewhere")

    _start(probe, tmp_path / "report", FSBPYR_CACHE_DIR=str(root))

    assert not _claimed(root)


@pytest.mark.parametrize("setting", ["0", "1", ""])
def test_fsbpyr_gc_set_starts_no_run(probe: Path, tmp_path: Path, setting: str) -> None:
    root = _cache(tmp_path / "cache", stamp=2 * DAY)

    _start(probe, tmp_path / "report", FSBPYR_CACHE_DIR=str(root), FSBPYR_GC=setting)

    assert not _claimed(root)


def test_no_run_while_one_holds_the_stamp(probe: Path, tmp_path: Path) -> None:
    root = _cache(tmp_path / "cache", stamp=2 * DAY)
    fd = os.open(root / gc.STAMP, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # a run, or another start claiming one

        _start(probe, tmp_path / "report", FSBPYR_CACHE_DIR=str(root))
    finally:
        os.close(fd)

    assert not _claimed(root)


def test_a_stamp_that_is_no_file_starts_nothing_and_never_blocks(
    probe: Path, tmp_path: Path
) -> None:
    root = _cache(tmp_path / "cache", stamp=None)
    os.mkfifo(root / gc.STAMP)
    os.utime(root / gc.STAMP, (time.time() - 2 * DAY,) * 2)

    _start(probe, tmp_path / "report", FSBPYR_CACHE_DIR=str(root))  # within its timeout

    assert _reports(tmp_path / "report", within=0) == []


def test_of_many_starts_at_once_one_starts_a_run(probe: Path, tmp_path: Path) -> None:
    root = _cache(tmp_path / "cache", stamp=2 * DAY)
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("FSBPYR_", "PROBE_"))}
    env = {**clean, "PROBE_OUT": str(tmp_path / "report"), "FSBPYR_CACHE_DIR": str(root)}

    starts = [subprocess.Popen([probe, "x"], stdout=subprocess.DEVNULL, env=env)
              for _ in range(8)]  # fmt: skip
    assert [start.wait(10) for start in starts] == [0] * 8

    assert len(_reports(tmp_path / "report")) == 1


@pytest.mark.parametrize(("env", "where"), [
    ({"FSBPYR_CACHE_DIR": "rel"}, "rel"),  # made absolute from the working directory
    ({"FSBPYR_CACHE_DIR": "sub/./../rel//"}, "rel"),  # and normalized, as os.path.abspath
    ({"XDG_CACHE_HOME": "{tmp}/xdg"}, "xdg/x"),
    ({"XDG_CACHE_HOME": "relative", "HOME": "{tmp}/home"}, "home/.cache/x"),  # XDG: ignored
], ids=["relative FSBPYR_CACHE_DIR", "normalized", "XDG_CACHE_HOME", "HOME"])  # fmt: skip
def test_the_root_is_where_the_boot_code_finds_it(
    probe: Path, tmp_path: Path, env: dict[str, str], where: str
) -> None:
    """native.cache_directory's rule; the process started is told the root, made absolute."""
    root = _cache(tmp_path / where, stamp=2 * DAY)
    env = {key: value.format(tmp=tmp_path) for key, value in env.items()}

    _start(probe, tmp_path / "report", cwd=tmp_path, **env)

    assert _claimed(root)
    assert [report["root"] for report in _reports(tmp_path / "report")] == [str(root)]


def test_an_app_name_the_boot_code_refuses_starts_nothing(probe: Path, tmp_path: Path) -> None:
    root = _cache(tmp_path / "xdg", stamp=2 * DAY)  # what "<XDG_CACHE_HOME>/inner/.." names

    _start(probe, tmp_path / "report", app="..", XDG_CACHE_HOME=str(tmp_path / "xdg" / "inner"))

    assert not _claimed(root)
