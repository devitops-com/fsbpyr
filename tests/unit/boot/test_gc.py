"""The cache collector (D38, _fsbpyr_rt.gc): what it removes from an app's cache, and what it
never touches; a run goes on past what it cannot remove; the marker only where a cache may be;
and taking a tree (cache.c's take, here its mirror) while a run collects it."""

import fcntl
import os
import re
import resource
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from fsbpyr.boot._fsbpyr_rt import gc

from ...support import cache

NOW = time.time()
DAY = 86400
OLD, NEW = "a" * 32, "b" * 32


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "cache"
    root.mkdir()
    (root / gc.MARKER).write_text("fsbpyr's cache\n")
    return root


def _tree(root: Path, name: str, *, days: float, kind: str = "runtime", lock: bool = True) -> Path:
    tree = root / kind / name
    (tree / "site" / "pkg").mkdir(parents=True)
    (tree / "site" / "pkg" / "lib.so").write_bytes(b"\x7fELF")
    when = NOW - days * DAY
    if lock:
        (tree / gc.LOCK).write_bytes(b"")
        os.utime(tree / gc.LOCK, (when, when))
    os.utime(tree, (when, when))
    return tree


def _held(path: Path, how: int = fcntl.LOCK_SH) -> int:
    fd = os.open(path, os.O_RDWR)
    fcntl.flock(fd, how)
    return fd


@pytest.fixture
def descriptors() -> Iterator[list[int]]:
    held: list[int] = []
    yield held
    for fd in held:
        os.close(fd)


def _left(root: Path) -> list[str]:
    return sorted(f"{kind}/{p.name}" for kind in gc.KINDS if (root / kind).is_dir()
                  for p in (root / kind).iterdir())  # fmt: skip


def test_a_root_without_the_marker_is_left_alone(root: Path) -> None:
    (root / gc.MARKER).unlink()
    _tree(root, OLD, days=30)

    report = gc.collect(str(root), now=NOW, pressure=False)

    assert not report.ours and _left(root) == [f"runtime/{OLD}"]
    assert "left alone" in report.text()


def test_trees_unused_for_seven_days_go(root: Path) -> None:
    _tree(root, OLD, days=7.1)
    _tree(root, NEW, days=6.9)
    _tree(root, "c" * 32, days=8, kind="resources")

    report = gc.collect(str(root), now=NOW, pressure=False)

    assert _left(root) == [f"runtime/{NEW}"]
    assert sorted(report.removed) == [f"resources/{'c' * 32}", f"runtime/{OLD}"]
    assert report.kept == 1 and not report.pressure
    assert not any((root / gc.TRASH).iterdir())  # removed, not only renamed
    assert NOW - os.stat(root / gc.STAMP).st_mtime < 60  # the run's start


def test_a_tree_in_use_stays_however_old(root: Path, descriptors: list[int]) -> None:
    tree = _tree(root, OLD, days=400)
    descriptors.append(_held(tree / gc.LOCK))

    gc.collect(str(root), now=NOW, pressure=True)

    assert _left(root) == [f"runtime/{OLD}"]


def test_under_pressure_every_tree_not_in_use_goes(root: Path, descriptors: list[int]) -> None:
    _tree(root, OLD, days=0)
    descriptors.append(_held(_tree(root, NEW, days=0) / gc.LOCK))

    report = gc.collect(str(root), now=NOW, pressure=True)

    assert _left(root) == [f"runtime/{NEW}"] and report.pressure
    assert "less than 10% free" in report.text()


def test_the_running_apps_own_trees_stay(root: Path) -> None:
    _tree(root, OLD, days=30)
    _tree(root, NEW, days=30, kind="resources")

    gc.collect(str(root), keep=frozenset({f"runtime/{OLD}", f"resources/{NEW}"}), now=NOW,
               pressure=True)  # fmt: skip

    assert _left(root) == [f"resources/{NEW}", f"runtime/{OLD}"]


def test_trees_from_before_their_locks_go_by_their_age(root: Path) -> None:
    """A lock is made for each (held while the tree is judged: a process taking the tree
    meanwhile waits); the tree's mtime stays its last use."""
    _tree(root, OLD, days=8, lock=False)
    kept = _tree(root, NEW, days=1, lock=False)

    gc.collect(str(root), now=NOW, pressure=False)

    assert _left(root) == [f"runtime/{NEW}"]
    assert abs(os.stat(kept / gc.LOCK).st_mtime - (NOW - DAY)) < 1


def test_nothing_but_our_own_names_is_touched(root: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "runtime").mkdir()
    (root / "runtime" / "notes").mkdir()  # not a tree name
    (root / "runtime" / OLD).symlink_to(elsewhere)  # a tree's name, but a symlink
    (root / "mine.txt").write_text("the user's")
    _tree(root, NEW, days=30, kind="other")  # not a kind of ours

    gc.collect(str(root), now=NOW, pressure=True)

    assert sorted(p.name for p in (root / "runtime").iterdir()) == [OLD, "notes"]
    assert (root / "mine.txt").exists() and (root / "other" / NEW).exists()
    assert elsewhere.exists()


def test_stale_temporary_files_go(root: Path, descriptors: list[int]) -> None:
    scratch = _tree(root, OLD, days=0) / gc.SCRATCH
    scratch.mkdir()
    for name in (".fsbpyr-1-0", ".fsbpyr-1-1", ".fsbpyr-1-2", "other"):
        (scratch / name).write_bytes(b"partial")
    old = NOW - 2 * gc.STALE
    for name in (".fsbpyr-1-0", ".fsbpyr-1-1", "other"):
        os.utime(scratch / name, (old, old))
    (scratch / ".fsbpyr-1-0").chmod(0o400)  # killed after its mode was set: still lockable
    descriptors.append(_held(scratch / ".fsbpyr-1-1", fcntl.LOCK_EX))  # still being written

    report = gc.collect(str(root), now=NOW, pressure=False)

    assert sorted(p.name for p in scratch.iterdir()) == [".fsbpyr-1-1", ".fsbpyr-1-2", "other"]
    assert report.temporaries == 1


def test_one_run_at_a_time(root: Path, descriptors: list[int]) -> None:
    _tree(root, OLD, days=30)
    (root / gc.STAMP).write_bytes(b"")
    descriptors.append(_held(root / gc.STAMP, fcntl.LOCK_EX))

    report = gc.collect(str(root), now=NOW, pressure=False)

    assert report.busy and _left(root) == [f"runtime/{OLD}"]
    assert "another process" in report.text()


def test_what_an_interrupted_run_left_in_the_trash_goes(root: Path) -> None:
    (root / gc.TRASH / f"runtime-{OLD}-1-0123abcd" / "site").mkdir(parents=True)
    (root / gc.TRASH / f"resources-{NEW}-1-0123abcd").write_bytes(b"")

    gc.collect(str(root), now=NOW, pressure=False)

    assert not any((root / gc.TRASH).iterdir())


def test_a_tree_taken_while_a_run_collects_it_is_taken_afresh(root: Path) -> None:
    """take() waits while a run holds the tree's lock; the run renamed the tree away, so the
    lock it then holds is the trashed tree's: it takes the tree again, a new one."""
    tree = _tree(root, OLD, days=30)
    collecting = _held(tree / gc.LOCK, fcntl.LOCK_EX)
    taken: list[int] = []
    chain = f"runtime/{OLD}/site"
    taker = threading.Thread(target=lambda: taken.append(cache.take(bytes(root), chain)))
    taker.start()
    time.sleep(0.2)  # waiting for the lock
    (root / gc.TRASH).mkdir()
    tree.rename(root / gc.TRASH / "doomed")
    os.close(collecting)
    taker.join(10)

    assert taken and taken[0] >= 0, "take() did not hold the tree"
    held = os.fstat(taken[0])
    os.close(taken[0])
    now = os.stat(root / "runtime" / OLD / gc.LOCK)
    assert (held.st_dev, held.st_ino) == (now.st_dev, now.st_ino)


def test_taking_a_tree_marks_a_new_root_and_touches_a_day_old_lock(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    os.close(cache.take(bytes(root), f"runtime/{OLD}/site"))
    lock = root / "runtime" / OLD / gc.LOCK
    os.utime(lock, (NOW - 2 * DAY, NOW - 2 * DAY))

    os.close(cache.take(bytes(root), f"runtime/{OLD}/site"))

    assert (root / gc.MARKER).read_bytes() == gc.MARKER_TEXT
    assert NOW - os.stat(lock).st_mtime < 60


def test_a_directory_of_the_users_is_never_marked(tmp_path: Path) -> None:
    """FSBPYR_CACHE_DIR may name one: a root with a name that is not ours gets no marker, so no
    run ever collects it; the tree is held all the same."""
    root = tmp_path / "home"
    root.mkdir()
    (root / "notes.txt").write_text("the user's")

    os.close(cache.take(bytes(root), f"runtime/{OLD}/site"))
    root_fd = os.open(root, os.O_RDONLY)
    try:
        marked = gc.mark(root_fd)
    finally:
        os.close(root_fd)

    assert not marked and not (root / gc.MARKER).exists()
    assert (root / "runtime" / OLD / gc.LOCK).exists()
    assert not gc.collect(str(root), now=NOW, pressure=True).ours


def test_the_launchers_names_are_the_collectors() -> None:
    """cache.c marks a root by the same names (OURS) and writes the same marker."""
    source = (Path(__file__).parents[3] / "src/fsbpyr/launcher/cache.c").read_text()
    listed = re.search(r"OURS\[\] = \{(.*?)\};", source, re.S)
    text = re.search(r'#define MARKER_TEXT "(.*)"', source)
    assert listed and text
    names = {name.strip().strip('"') for name in listed[1].split(",") if name.strip()}
    assert {gc.MARKER if name == "MARKER" else name for name in names} == gc.OURS
    assert text[1].encode().decode("unicode_escape").encode() == gc.MARKER_TEXT


def test_a_tree_that_cannot_go_is_reported_and_the_run_goes_on(root: Path) -> None:
    _tree(root, OLD, days=30)
    _tree(root, NEW, days=30)
    (root / gc.TRASH).write_bytes(b"")  # not a directory: nothing can be renamed into it

    report = gc.collect(str(root), now=NOW, pressure=False)

    assert _left(root) == [f"runtime/{OLD}", f"runtime/{NEW}"] and report.removed == []
    assert len(report.errors) == 2 and "failed: runtime/" in report.text()


def test_a_tree_with_a_read_only_directory_goes(root: Path) -> None:
    tree = _tree(root, OLD, days=30)
    (tree / "site" / "pkg").chmod(0o500)

    report = gc.collect(str(root), now=NOW, pressure=False)

    assert _left(root) == [] and report.errors == []


def test_a_tree_of_any_depth_goes_with_few_descriptors(root: Path) -> None:
    tree = _tree(root, OLD, days=30)
    deep = tree / "site"
    for _ in range(300):
        deep /= "d"
    deep.mkdir(parents=True)
    (deep / "leaf").write_bytes(b"x")
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (len(os.listdir("/proc/self/fd")) + 16, hard))
    try:
        report = gc.collect(str(root), now=NOW, pressure=False)
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))

    assert report.errors == [] and _left(root) == []


def test_a_symlink_in_a_tree_is_removed_never_followed(root: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "inside").mkdir(parents=True)
    tree = _tree(root, OLD, days=30)
    (tree / "site" / "link").symlink_to(elsewhere)
    (tree / "site" / "pkg" / "file-link").symlink_to(elsewhere / "inside")

    gc.collect(str(root), now=NOW, pressure=False)

    assert _left(root) == [] and (elsewhere / "inside").is_dir()


def test_a_tree_whose_lock_is_not_ours_is_left_alone(root: Path, tmp_path: Path) -> None:
    (tmp_path / "theirs").write_bytes(b"")
    linked = _tree(root, OLD, days=30, lock=False)
    (linked / gc.LOCK).symlink_to(tmp_path / "theirs")
    fifo = _tree(root, NEW, days=30, lock=False)
    os.mkfifo(fifo / gc.LOCK)  # opened without blocking: no hang

    gc.collect(str(root), now=NOW, pressure=True)

    assert _left(root) == [f"runtime/{OLD}", f"runtime/{NEW}"]


def test_only_what_a_run_renamed_into_the_trash_goes(root: Path) -> None:
    (root / gc.TRASH).mkdir()
    (root / gc.TRASH / "users-file").write_bytes(b"")

    gc.collect(str(root), now=NOW, pressure=False)

    assert [p.name for p in (root / gc.TRASH).iterdir()] == ["users-file"]


def test_a_tree_with_a_directory_we_may_not_open_goes(root: Path) -> None:
    tree = _tree(root, OLD, days=30)
    (tree / "site" / "pkg").chmod(0o000)

    report = gc.collect(str(root), now=NOW, pressure=False)

    assert _left(root) == [] and report.errors == []


def test_a_directory_named_like_a_tree_but_not_one_is_left_alone(root: Path) -> None:
    """Neither a lock nor a site: the user's (a uuid4().hex is 32 hex digits)."""
    theirs = root / "resources" / NEW
    theirs.mkdir(parents=True)
    (theirs / "data.csv").write_text("the user's")
    os.utime(theirs, (NOW - 30 * DAY,) * 2)

    gc.collect(str(root), now=NOW, pressure=True)

    assert (theirs / "data.csv").exists() and not (theirs / gc.LOCK).exists()


def test_a_root_of_names_like_ours_is_marked_only_with_a_tree_of_ours(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "resources" / NEW).mkdir(parents=True)  # the user's, named like a tree
    fd = os.open(root, os.O_RDONLY)
    try:
        refused = gc.mark(fd)
        _tree(root, OLD, days=0)  # a cache of an earlier version: a tree with its site
        accepted = gc.mark(fd)
    finally:
        os.close(fd)

    assert not refused and accepted


def test_the_launcher_marks_as_the_collector_does(tmp_path: Path) -> None:
    lookalike = tmp_path / "lookalike"
    (lookalike / "resources" / NEW).mkdir(parents=True)
    earlier = tmp_path / "earlier"
    _tree(earlier, NEW, days=0, lock=False)

    for root in (lookalike, earlier):
        os.close(cache.take(bytes(root), f"runtime/{OLD}/site"))

    assert not (lookalike / gc.MARKER).exists() and (earlier / gc.MARKER).exists()


def test_a_lock_dated_ahead_is_brought_back_to_now(root: Path) -> None:
    """The clock was set back: the tree ages again from now, instead of from the future."""
    tree = _tree(root, OLD, days=-30)

    report = gc.collect(str(root), now=NOW, pressure=False)

    assert report.kept == 1 and abs(os.stat(tree / gc.LOCK).st_mtime - NOW) < 1
