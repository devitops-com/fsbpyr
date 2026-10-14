"""The tool's cache, collected (D38, runtime/collect.py): only a root with the marker, which
hold() writes into a root that holds nothing but our names; what the running fsbpyr builds with
and what a process holds stay; other runtimes, distributions, sysroots and downloads go after 7
days unused (at once when the file system is short), and what a killed fetch or build left
after a day; nothing else in the root is touched. And ``fsbpyr cache gc``."""

import fcntl
import inspect
import os
import threading
import time
from pathlib import Path

import pytest

from fsbpyr import cli
from fsbpyr.boot._fsbpyr_rt import gc
from fsbpyr.runtime import collect, hold, pbs, registry, sysroot

NOW = time.time()
DAY = 86400
OTHER = "cp314-x86_64-unknown-linux-gnu-000000000000"
OLD_PBS = "cpython-3.13.0+20240101-x86_64-unknown-linux-gnu-pgo+lto-full"
OLD_SYSROOT = "debian9-glibc2.24-x86_64-0123456789ab"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "fsbpyr"
    root.mkdir()
    (root / gc.MARKER).write_bytes(gc.MARKER_TEXT)
    return root


def _item(root: Path, kind: str, name: str, *, days: float, lock: bool = True) -> Path:
    """An item as the tool makes it: what marks it complete (gc.PROOF), and its lock."""
    item = root / kind / name
    proof = item / gc.PROOF.get(kind, "data")
    proof.parent.mkdir(parents=True)
    proof.write_bytes(b"x")
    when = NOW - days * DAY
    if lock:
        (item / hold.LOCK).write_bytes(b"")
        os.utime(item / hold.LOCK, (when, when))
    os.utime(item, (when, when))
    return item


def _download(root: Path, name: str, *, days: float) -> Path:
    (root / collect.DOWNLOADS).mkdir(parents=True, exist_ok=True)
    path = root / collect.DOWNLOADS / name
    path.write_bytes(b"archive")
    os.utime(path, (NOW - days * DAY,) * 2)
    return path


def _left(root: Path) -> list[str]:
    return sorted(f"{kind}/{p.name}" for kind in (*collect.ITEMS, collect.DOWNLOADS)
                  if (root / kind).is_dir() for p in (root / kind).iterdir())  # fmt: skip


def test_what_the_running_fsbpyr_builds_with_stays(root: Path) -> None:
    needed = collect.needed()
    for kind in collect.ITEMS:
        for name in needed[kind]:
            _item(root, kind, name, days=400)
    for name in needed[collect.DOWNLOADS]:
        _download(root, name, days=400)

    report = collect.collect_tool(root, now=NOW, pressure=True)

    assert report.removed == [] and len(_left(root)) == sum(map(len, needed.values()))


def test_what_nobody_used_for_seven_days_goes(root: Path) -> None:
    _item(root, "runtimes", OTHER, days=7.1)
    _item(root, "runtimes", OTHER[:-1] + "1", days=6.9)
    _item(root, "pbs", OLD_PBS, days=30)
    _item(root, "sysroots", OLD_SYSROOT, days=30)
    _download(root, f"{OLD_PBS}.tar.zst", days=30)
    _download(root, "libc6_2.24-11_amd64.deb", days=1)

    report = collect.collect_tool(root, now=NOW, pressure=False)

    assert _left(root) == ["downloads/libc6_2.24-11_amd64.deb", f"runtimes/{OTHER[:-1]}1"]
    assert len(report.removed) == 4 and NOW - os.stat(root / collect.STAMP).st_mtime < 60
    assert not any((root / collect.TRASH).iterdir())


def test_an_item_held_stays_however_old(root: Path) -> None:
    item = _item(root, "runtimes", OTHER, days=400)
    assert hold.hold(item)

    collect.collect_tool(root, now=NOW, pressure=True)

    assert _left(root) == [f"runtimes/{OTHER}"]


def test_short_of_space_what_is_neither_needed_nor_held_goes(root: Path) -> None:
    _item(root, "runtimes", OTHER, days=0)
    _download(root, "libx_1.0_amd64.deb", days=0)

    collect.collect_tool(root, now=NOW, pressure=True)

    assert _left(root) == []


def test_what_a_killed_fetch_or_build_left_goes_after_a_day(root: Path) -> None:
    _item(root, "pbs", ".extract-old", days=2, lock=False)
    _item(root, "pbs", ".extract-new", days=0.1, lock=False)
    _item(root, "runtimes", f".{OTHER}.staging", days=0, lock=False)  # being saved
    _download(root, "tmpold.part", days=2)
    _download(root, "tmpnew.part", days=0.1)

    report = collect.collect_tool(root, now=NOW, pressure=True)

    assert sorted(report.removed) == ["downloads/tmpold.part", "pbs/.extract-old"]
    assert _left(root) == [
        "downloads/tmpnew.part",
        "pbs/.extract-new",
        f"runtimes/.{OTHER}.staging",
    ]


def test_a_directory_of_the_users_is_left_alone(tmp_path: Path) -> None:
    """FSBPYR_CACHE_DIR may name one: holding an item there writes no marker into it (it holds
    a name that is not ours), and a root without the marker is never collected."""
    work = tmp_path / "work"
    report = _download(work, f"{OLD_PBS}.tar.zst", days=30)
    item = _item(work, "pbs", OLD_PBS, days=30, lock=False)
    (work / "notes.txt").write_text("the user's")

    assert hold.hold(item)
    collected = collect.collect_tool(work, now=NOW, pressure=True)

    assert not collected.ours and report.exists() and item.exists()
    assert not (work / gc.MARKER).exists() and not (work / collect.STAMP).exists()


def test_only_names_the_tool_gives_are_removed(root: Path) -> None:
    """A directory of the user's that holds nothing but names like ours is marked: what is in
    it all the same stays, unless named as the tool names what it makes."""
    for kind in collect.ITEMS:
        _item(root, kind, "my-project", days=30, lock=False)
    _download(root, "report.pdf", days=30)
    _download(root, "notes.part", days=30)

    report = collect.collect_tool(root, now=NOW, pressure=True)

    assert report.removed == [] and len(_left(root)) == 5


def test_an_item_with_neither_lock_nor_content_of_ours_is_left_alone(root: Path) -> None:
    lookalike = root / "runtimes" / OTHER
    (lookalike / "notes").mkdir(parents=True)
    os.utime(lookalike, (NOW - 30 * DAY,) * 2)

    report = collect.collect_tool(root, now=NOW, pressure=True)

    assert report.removed == [] and (lookalike / "notes").is_dir()


def test_what_proves_an_item_ours_is_what_the_tool_writes() -> None:
    assert gc.PROOF["runtimes"] == registry._META
    assert gc.PROOF["sysroots"] == sysroot._DONE
    assert gc.PROOF["pbs"] == "python/PYTHON.json" and "PYTHON.json" in inspect.getsource(pbs.fetch)


def test_what_the_tool_builds_with_is_named_as_the_tool_names_it() -> None:
    needed = collect.needed()
    for kind, names in needed.items():
        assert names and all(collect.NAMES[kind].fullmatch(name) for name in names), kind


def test_holding_an_item_marks_a_root_that_holds_only_our_names(tmp_path: Path) -> None:
    root = tmp_path / "new"
    item = _item(root, "runtimes", OTHER, days=0)

    assert hold.hold(item)

    assert (root / gc.MARKER).read_bytes() == gc.MARKER_TEXT


def test_items_from_before_their_locks_go_by_their_age(root: Path) -> None:
    _item(root, "runtimes", OTHER, days=8, lock=False)
    _item(root, "runtimes", OTHER[:-1] + "1", days=1, lock=False)

    collect.collect_tool(root, now=NOW, pressure=False)

    assert _left(root) == [f"runtimes/{OTHER[:-1]}1"]


def test_nothing_else_in_the_root_is_touched(root: Path, tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (root / "runtimes").mkdir()
    (root / "runtimes" / OTHER).symlink_to(tmp_path / "elsewhere")
    app = _item(root, "runtime", "a" * 32, days=400)  # the app cache of fsbpyr itself
    (root / gc.MARKER).write_bytes(b"")
    (root / "notes.txt").write_text("the user's")

    collect.collect_tool(root, now=NOW, pressure=True)

    assert (root / "runtimes" / OTHER).is_symlink() and (tmp_path / "elsewhere").exists()
    assert app.exists() and (root / "notes.txt").exists()


def test_a_held_item_is_touched_once_a_day_old_and_a_gone_one_is_no_hold(root: Path) -> None:
    item = _item(root, "pbs", "dist", days=2)

    assert hold.hold(item) and NOW - os.stat(item / hold.LOCK).st_mtime < 60
    assert not hold.hold(root / "pbs" / "gone")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_an_item_that_cannot_be_held_is_used_unheld(root: Path) -> None:
    """A cache we may not write (no lock can be made): the command goes on."""
    item = _item(root, "pbs", OLD_PBS, days=0, lock=False)
    item.chmod(0o500)
    try:
        assert hold.hold(item)
    finally:
        item.chmod(0o700)
    assert not (item / hold.LOCK).exists()


def test_an_item_collected_while_its_lock_was_waited_for_is_no_hold(root: Path) -> None:
    item = _item(root, "runtimes", OTHER, days=30)
    collecting = os.open(item / hold.LOCK, os.O_RDWR)
    fcntl.flock(collecting, fcntl.LOCK_EX)
    taken: list[bool] = []
    taker = threading.Thread(target=lambda: taken.append(hold.hold(item)))
    taker.start()
    time.sleep(0.2)  # waiting for the lock
    (root / collect.TRASH).mkdir()
    item.rename(root / collect.TRASH / "doomed")
    os.close(collecting)
    taker.join(10)

    assert taken == [False]


def test_a_collection_is_due_a_day_after_the_last(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FSBPYR_GC")
    (root / collect.STAMP).write_bytes(b"")
    os.utime(root / collect.STAMP, (NOW - 2 * 3600,) * 2)
    assert collect.collect_when_due(root) is None  # two hours ago, with room to spare

    os.utime(root / collect.STAMP, (NOW - 2 * DAY,) * 2)
    assert collect.collect_when_due(root) is not None

    os.utime(root / collect.STAMP, (NOW + DAY,) * 2)  # ahead: the clock was set back
    assert collect.collect_when_due(root) is not None

    os.utime(root / collect.STAMP, (NOW - 2 * DAY,) * 2)
    for value in ("0", "1", ""):  # set: the launcher's rule, the user decides
        monkeypatch.setenv("FSBPYR_GC", value)
        assert collect.collect_when_due(root) is None


def test_app_caches_are_the_marked_or_markable_ones_and_fsbpyr_cache_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("app", "theirs"):
        (tmp_path / "xdg" / name).mkdir(parents=True)
    (tmp_path / "xdg" / "app" / gc.MARKER).write_bytes(b"")
    (tmp_path / "xdg" / "earlier" / "runtime" / ("a" * 32) / "site").mkdir(parents=True)
    (tmp_path / "xdg" / "lookalike" / "runtime" / ("a" * 32)).mkdir(parents=True)
    (tmp_path / "explicit").mkdir()
    (tmp_path / "explicit" / gc.MARKER).write_bytes(b"")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path / "explicit"))

    caches = collect.app_caches()

    xdg = tmp_path / "xdg"
    assert caches == [xdg / "app", xdg / "earlier", tmp_path / "explicit"]
    assert (xdg / "earlier" / gc.MARKER).is_file()  # an app of an earlier version marked none
    assert not (xdg / "lookalike" / gc.MARKER).exists()


def test_cache_gc_collects_the_tools_cache_and_every_app_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("FSBPYR_CACHE_DIR", raising=False)
    _item(tmp_path / "fsbpyr", "runtimes", OTHER, days=30)
    (tmp_path / "fsbpyr" / gc.MARKER).write_bytes(b"")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / gc.MARKER).write_bytes(b"")
    _item(tmp_path / "app", "runtime", "a" * 32, days=30)
    _item(tmp_path / "theirs", "runtime", "a" * 32, days=30)
    (tmp_path / "theirs" / "notes.txt").write_text("not ours")  # so never marked

    assert cli.main(["cache", "gc"]) == 0

    out = capsys.readouterr().out
    assert f"removed runtimes/{OTHER}" in out and f"removed runtime/{'a' * 32}" in out
    assert not (tmp_path / "fsbpyr" / "runtimes" / OTHER).exists()
    assert not (tmp_path / "app" / "runtime" / ("a" * 32)).exists()
    assert (tmp_path / "theirs" / "runtime" / ("a" * 32)).exists()


def test_cache_gc_fails_when_something_cannot_be_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("FSBPYR_CACHE_DIR", raising=False)
    root = tmp_path / "fsbpyr"
    _item(root, "runtimes", OTHER, days=30)
    (root / gc.MARKER).write_bytes(b"")
    (root / collect.TRASH).write_bytes(b"")  # no directory: nothing can be renamed into it

    assert cli.main(["cache", "gc"]) == 1

    assert f"failed: runtimes/{OTHER}" in capsys.readouterr().out
