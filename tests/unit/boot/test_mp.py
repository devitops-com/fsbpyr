"""_fsbpyr_rt.mp: recognizing the command lines multiprocessing re-executes the app with."""

import importlib.util
import subprocess
import sys
from multiprocessing import spawn, util
from types import ModuleType, SimpleNamespace

import pytest

from .conftest import BOOT_DIR

EXE = "/opt/app/bin/tool"
FLAGS = ["-B", "-S", "-I"]
MP_SOURCE = BOOT_DIR / "_fsbpyr_rt" / "mp.py"
FORKSERVER = (
    "import sys; from multiprocessing.forkserver import main; "
    "main(%d, %d, %r, sys_argv=sys.argv[1:], **%r)"
)  # the template of multiprocessing/forkserver.py (3.14)


@pytest.fixture(scope="module")
def mp() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_fsbpyr_rt_mp", MP_SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_spawn_command_from_cpython_is_recognized(mp: ModuleType) -> None:
    argv = [EXE, *spawn.get_command_line(tracker_fd=5, pipe_handle=7)[1:]]

    assert mp.child_command(argv) == (
        "multiprocessing.spawn", "spawn_main", (), {"tracker_fd": 5, "pipe_handle": 7},
        ["-c", "--multiprocessing-fork"],
    )  # fmt: skip


def test_forkserver_command_is_recognized(mp: ModuleType) -> None:
    keywords = {"sys_path": [EXE], "authkey_r": 9}
    argv = [EXE, *FLAGS, "-X", "dev", "-c", FORKSERVER % (3, 4, ["__main__"], keywords), "app"]

    assert mp.child_command(argv) == (
        "multiprocessing.forkserver", "main", (3, 4, ["__main__"]),
        {"authkey_r": 9, "sys_argv": ["app"]}, ["-c", "app"],
    )  # fmt: skip  # sys_path is dropped: the payload-only sys.path stays


def test_resource_tracker_command_is_recognized(mp: ModuleType) -> None:
    argv = [EXE, *util._args_from_interpreter_flags(), "-c",  # type: ignore[attr-defined]
            "from multiprocessing.resource_tracker import main;main(6)"]  # fmt: skip

    assert mp.child_command(argv) == ("multiprocessing.resource_tracker", "main", (6,), {}, ["-c"])


@pytest.mark.parametrize(
    "argv",
    [
        [EXE],
        [EXE, "-c", "config.toml"],  # an application's own -c option
        [EXE, "run", "-c", "from multiprocessing.resource_tracker import main;main(6)"],
        [EXE, "-c", "from multiprocessing.resource_tracker import main;main(6); import os"],
        [EXE, "-c", "from multiprocessing.resource_tracker import main;main(6)", "extra"],
        [EXE, "-c", "from multiprocessing.resource_tracker import main;main(__import__('os'))"],
        [EXE, "-c", "from multiprocessing.spawn import spawn_main; spawn_main(pipe_handle=7)"],
        [
            EXE,
            "-c",
            "from multiprocessing.spawn import spawn_main; spawn_main(evil=7)",
            "--multiprocessing-fork",
        ],
        [
            EXE,
            "-c",
            "from multiprocessing.spawn import spawn_main; spawn_main(pipe_handle=x)",
            "--multiprocessing-fork",
        ],
        [EXE, "-c", FORKSERVER % (3, 4, ["__main__"], {"evil": 1})],
        # stdio is never the channel a real parent hands its child (pass_fds gives fds >= 3)
        [
            EXE,
            "-c",
            "from multiprocessing.spawn import spawn_main; spawn_main(pipe_handle=0)",
            "--multiprocessing-fork",
        ],
        [
            EXE,
            "-c",
            "from multiprocessing.spawn import spawn_main; spawn_main(tracker_fd=2, pipe_handle=7)",
            "--multiprocessing-fork",
        ],
        [EXE, "-c", "from multiprocessing.resource_tracker import main;main(2)"],
        [EXE, "-c", FORKSERVER % (0, 4, ["__main__"], {})],
        [EXE, "-c", FORKSERVER % (3, 4, ["__main__"], {"authkey_r": 1})],
        # fsbpyr apps never send a main_path: it would run a file as __main__
        [EXE, "-c", FORKSERVER % (3, 4, ["__main__"], {"main_path": "/srv/x.py"}), "x"],
        [EXE, "-c", FORKSERVER % (3, 4, "__main__", {})],
        [EXE, "-c", FORKSERVER.replace("main(%d", "print(%d") % (3, 4, [], {})],
    ],
)
def test_anything_else_runs_the_application(mp: ModuleType, argv: list[str]) -> None:
    assert mp.child_command(argv) is None


def test_run_child_calls_the_function_with_cpythons_argv(
    mp: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []
    tracker = ModuleType("multiprocessing.resource_tracker")
    tracker.main = lambda fd: calls.append((fd, list(sys.argv)))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "multiprocessing.resource_tracker", tracker)
    monkeypatch.setattr(mp, "_parent_runs_this_executable", lambda: True)
    command = "from multiprocessing.resource_tracker import main;main(6)"
    monkeypatch.setattr(sys, "argv", [EXE, "-c", command])

    assert mp.run_child(sys.argv) is True
    assert calls == [(6, ["-c"])]
    assert mp.run_child([EXE, "--help"]) is False


def test_children_of_any_other_program_are_refused(mp: ModuleType) -> None:
    argv = [EXE, "-c", "from multiprocessing.resource_tracker import main;main(6)"]

    assert mp._parent_runs_this_executable() is False  # pytest was not started by pytest
    with pytest.raises(SystemExit, match="refusing"):
        mp.run_child(argv)


def test_a_child_of_the_same_executable_is_recognized(mp: ModuleType) -> None:
    probe = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('m', {str(MP_SOURCE)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "sys.exit(0 if m._parent_runs_this_executable() else 1)\n"
    )

    # the child runs sys.executable, as this process does
    assert subprocess.run([sys.executable, "-c", probe], check=False).returncode == 0


def _status(uid: tuple[int, int], gid: tuple[int, int]) -> str:
    """A /proc/<pid>/status file with the credential lines (real, effective, saved, fs)."""
    return (
        "Name:\ttool\nUmask:\t0022\n"
        f"Uid:\t{uid[0]}\t{uid[1]}\t{uid[1]}\t{uid[1]}\n"
        f"Gid:\t{gid[0]}\t{gid[1]}\t{gid[1]}\t{gid[1]}\n"
    )


@pytest.mark.parametrize(
    ("uid_shift", "gid_shift", "expected"),
    [(0, 0, True), (1, 0, False), (0, 1, False)],
)
def test_the_parent_needs_our_real_and_effective_ids(
    mp: ModuleType, monkeypatch: pytest.MonkeyPatch, uid_shift: int, gid_shift: int, expected: bool
) -> None:
    same = SimpleNamespace(st_dev=1, st_ino=2)  # the parent runs this very executable
    monkeypatch.setattr(mp.os, "stat", lambda path: same)
    uid, gid = mp.os.getresuid()[:2], mp.os.getresgid()[:2]
    status = _status((uid[0] + uid_shift, uid[1]), (gid[0] + gid_shift, gid[1]))
    monkeypatch.setattr(mp, "_read_parent_status", lambda: status)

    assert mp._parent_runs_this_executable() is expected


@pytest.mark.parametrize("status", ["", "Uid:\t1000\n", "Uid:\tx\ty\nGid:\t0\t0\n"])
def test_an_unreadable_status_counts_as_no(
    mp: ModuleType, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    same = SimpleNamespace(st_dev=1, st_ino=2)
    monkeypatch.setattr(mp.os, "stat", lambda path: same)
    monkeypatch.setattr(mp, "_read_parent_status", lambda: status)

    assert mp._parent_runs_this_executable() is False


def test_a_parent_with_other_ids_gets_no_child_run(
    mp: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    same = SimpleNamespace(st_dev=1, st_ino=2)
    monkeypatch.setattr(mp.os, "stat", lambda path: same)
    uid, gid = mp.os.getresuid()[:2], mp.os.getresgid()[:2]
    monkeypatch.setattr(mp, "_read_parent_status", lambda: _status((uid[0] + 1, uid[1]), gid))

    with pytest.raises(SystemExit, match="refusing"):
        mp.run_child([EXE, "-c", "from multiprocessing.resource_tracker import main;main(6)"])


def test_argv_is_never_evaluated_as_code() -> None:
    source = MP_SOURCE.read_text()

    assert "exec(" not in source and "eval(" not in source.replace("literal_eval(", "")
