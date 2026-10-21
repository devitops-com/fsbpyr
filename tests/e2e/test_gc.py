"""The app cache's collection, by the launcher (D38): a process holds the trees it uses and
writes through their scratch directories; `FSBPYR_GC=1 app` collects instead of running the
app; a start finds a run due a day after the last (or an hour, short of space) and starts it in
the background; `FSBPYR_GC=0` turns that off."""

import fcntl
import os
import select
import subprocess
import time
from pathlib import Path

import pytest

from fsbpyr.assemble import App, build_executable
from fsbpyr.boot._fsbpyr_rt import gc
from fsbpyr.payload.format import FileKind
from fsbpyr.payload.reader import Executable
from fsbpyr.payload.spec import Blob, File
from fsbpyr.runtime.registry import Runtime

from .conftest import app_content

pytestmark = pytest.mark.slow

DAY = 86400
OLD = "a" * 32
_SYNCS = """\
import sys
import _fsbpyr
from _fsbpyr_rt.native import native_cache

def main():
    native_cache().sync(_fsbpyr.files_below("x.libs"))
    print("synced", flush=True)
    sys.stdin.read()  # until the test closes it: the tree is held meanwhile
"""


@pytest.fixture(scope="module")
def app(runtime: Runtime, tmp_path_factory: pytest.TempPathFactory) -> Path:
    native = File("x.libs/libx.so", FileKind.NATIVE_LIB, Blob.zstd(b"\x7fELF x" * 999, digest=True),
                  mode=0o755)  # fmt: skip
    modules, files = app_content({"x.py": _SYNCS})
    out = tmp_path_factory.mktemp("gc") / "x"
    build_executable(runtime, App("x", "x:main", modules=modules, files=(*files, native)), out)
    return out


def _generation(app: Path) -> str:
    with Executable.open(app) as exe:
        return exe.payload.generation_id.hex()[:32]


def _env(cache: Path, **more: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "FSBPYR_GC"}
    return {**env, "FSBPYR_CACHE_DIR": str(cache), **more}


def _run(app: Path, cache: Path, **more: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([app], input="", capture_output=True, text=True, check=False,
                          env=_env(cache, **more), timeout=60)  # fmt: skip


def _old_tree(cache: Path) -> Path:
    """A generation no build uses any more, its lock last touched eight days ago."""
    tree = cache / "runtime" / OLD
    (tree / "site").mkdir(parents=True)
    (tree / ".lock").write_bytes(b"")
    os.utime(tree / ".lock", (time.time() - 8 * DAY,) * 2)
    return tree


def _stamp(cache: Path, age: float) -> None:
    (cache / ".gc").write_bytes(b"")
    os.utime(cache / ".gc", (time.time() - age,) * 2)


def test_a_process_holds_its_tree_and_writes_through_scratch(app: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    tree = cache / "runtime" / _generation(app)
    with subprocess.Popen([app], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                          env=_env(cache, FSBPYR_GC="0")) as running:  # fmt: skip
        assert running.stdout is not None and running.stdout.readline() == "synced\n"
        fd = os.open(tree / ".lock", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):  # held shared by the running app
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        running.communicate("")

    assert (cache / ".fsbpyr-cache").is_file()
    assert (tree / "site" / "x.libs" / "libx.so").read_bytes() == b"\x7fELF x" * 999
    assert list((tree / ".scratch").iterdir()) == []  # written there, renamed out
    assert not [p for p in (tree / "site").rglob(".fsbpyr-*")]


def test_fsbpyr_gc_1_collects_instead_of_running_the_app(app: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    assert _run(app, cache, FSBPYR_GC="0").returncode == 0  # the app's own tree, in use lately
    _old_tree(cache)

    result = _run(app, cache, FSBPYR_GC="1")

    assert result.returncode == 0 and result.stdout == "", result.stderr
    assert f"removed runtime/{OLD}" in result.stderr
    assert sorted(p.name for p in (cache / "runtime").iterdir()) == [_generation(app)]


def _collected(cache: Path, within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not (cache / "runtime" / OLD).exists():
            return True
        time.sleep(0.1)
    return False


@pytest.mark.parametrize("last", [2 * DAY, None], ids=["a day ago", "never"])
def test_a_start_a_day_after_the_last_run_collects_in_the_background(
    app: Path, tmp_path: Path, last: float | None
) -> None:
    cache = tmp_path / "cache"
    assert _run(app, cache, FSBPYR_GC="0").returncode == 0
    _old_tree(cache)
    if last is not None:
        _stamp(cache, last)

    result = _run(app, cache)

    assert result.returncode == 0 and result.stdout == "synced\n", result.stderr
    assert _collected(cache, 30)
    assert time.time() - os.stat(cache / ".gc").st_mtime < 60
    assert (cache / "runtime" / _generation(app)).is_dir()


@pytest.mark.parametrize(("age", "setting"), [(2 * 3600, None), (2 * DAY, "0")],
                         ids=["collected two hours ago", "FSBPYR_GC=0"])  # fmt: skip
def test_no_collection_starts_when_none_is_due(
    app: Path, tmp_path: Path, age: float, setting: str | None
) -> None:
    if setting is None and gc.short_of_space(str(tmp_path)):
        pytest.skip("needs 10% free where the tests run: with less, a collection is due hourly")
    cache = tmp_path / "cache"
    assert _run(app, cache, FSBPYR_GC="0").returncode == 0
    _old_tree(cache)
    _stamp(cache, age)

    result = _run(app, cache) if setting is None else _run(app, cache, FSBPYR_GC=setting)

    assert result.returncode == 0, result.stderr
    assert not _collected(cache, 3)


def test_the_background_run_collects_the_root_the_app_uses(app: Path, tmp_path: Path) -> None:
    """No FSBPYR_CACHE_DIR: the launcher finds the root as the boot code does (XDG)."""
    env = {key: value for key, value in os.environ.items()
           if key not in ("FSBPYR_GC", "FSBPYR_CACHE_DIR")}  # fmt: skip
    env["XDG_CACHE_HOME"] = str(tmp_path / "xdg")
    cache = tmp_path / "xdg" / "x"
    first = subprocess.run([app], input="", capture_output=True, text=True, check=False,
                           env={**env, "FSBPYR_GC": "0"}, timeout=60)  # fmt: skip
    assert first.returncode == 0, first.stderr
    _old_tree(cache)
    _stamp(cache, 2 * DAY)

    again = subprocess.run([app], input="", capture_output=True, text=True, check=False,
                           env=env, timeout=60)  # fmt: skip

    assert again.returncode == 0, again.stderr
    assert _collected(cache, 30)


@pytest.mark.parametrize("setting", ["", "false", "2"])
def test_other_fsbpyr_gc_values_run_the_app(app: Path, tmp_path: Path, setting: str) -> None:
    cache = tmp_path / "cache"
    assert _run(app, cache, FSBPYR_GC="0").returncode == 0
    _old_tree(cache)

    result = _run(app, cache, FSBPYR_GC=setting)

    assert result.returncode == 0 and result.stdout == "synced\n", result.stderr
    assert (cache / "runtime" / OLD).exists()


def test_a_directory_of_the_users_is_never_marked_nor_collected(app: Path, tmp_path: Path) -> None:
    cache = tmp_path / "mine"
    cache.mkdir()
    (cache / "notes.txt").write_text("the user's")
    _old_tree(cache)

    for setting in ("0", "1"):
        assert _run(app, cache, FSBPYR_GC=setting).returncode == 0

    assert not (cache / gc.MARKER).exists() and not (cache / gc.STAMP).exists()
    assert (cache / "runtime" / OLD).exists() and (cache / "notes.txt").exists()


def test_a_start_waiting_for_a_tree_a_run_removes_takes_it_afresh(
    app: Path, tmp_path: Path
) -> None:
    """The launcher's take(): a start waits for the lock a run holds; the run renames the tree
    away, and the start then takes the tree again (made afresh) and runs."""
    cache = tmp_path / "cache"
    assert _run(app, cache, FSBPYR_GC="0").returncode == 0
    tree = cache / "runtime" / _generation(app)
    collecting = os.open(tree / gc.LOCK, os.O_RDWR)
    fcntl.flock(collecting, fcntl.LOCK_EX)
    with subprocess.Popen([app], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                          env=_env(cache, FSBPYR_GC="0")) as running:  # fmt: skip
        assert running.stdout is not None
        waited = not select.select([running.stdout], [], [], 1.5)[0]  # held by the "run"
        (cache / gc.TRASH).mkdir(exist_ok=True)
        tree.rename(cache / gc.TRASH / "doomed")
        os.close(collecting)
        synced = running.stdout.readline()
        fd = os.open(tree / gc.LOCK, os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):  # the new tree's lock, held by the start
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        running.communicate("")

    assert waited and synced == "synced\n" and running.returncode == 0
    assert (tree / "site" / "x.libs" / "libx.so").read_bytes() == b"\x7fELF x" * 999
