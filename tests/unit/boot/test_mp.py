"""_fsbpyr_rt.mp: recognizing the command lines multiprocessing re-executes the app with."""

import contextlib
import importlib.util
import os
import re
import subprocess
import sys
from collections.abc import Callable
from multiprocessing import forkserver, process, resource_tracker, spawn, util
from types import ModuleType, SimpleNamespace

import pytest

from .conftest import BOOT_DIR

EXE = "/opt/app/bin/tool"
FLAGS = ["-B", "-S", "-I"]
MP_SOURCE = BOOT_DIR / "_fsbpyr_rt" / "mp.py"
# the templates of multiprocessing/forkserver.py: 3.12 has no sys_argv, 3.13 and 3.14 do
if sys.version_info < (3, 13):
    FORKSERVER = "from multiprocessing.forkserver import main; main(%d, %d, %r, **%r)"
    SYS_ARGV = False
else:
    FORKSERVER = (
        "import sys; from multiprocessing.forkserver import main; "
        "main(%d, %d, %r, sys_argv=sys.argv[1:], **%r)"
    )
    SYS_ARGV = True
AUTHKEY = sys.version_info >= (3, 14)  # 3.14's forkserver and later authenticate with authkey_r
ON_ERROR = sys.version_info >= (3, 15)  # 3.15's sends on_error when the preload names one


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


def _captured_command(monkeypatch: pytest.MonkeyPatch, start: Callable[[], object]) -> list[str]:
    """The argv CPython's own code passes to spawnv_passfds when it runs ``start``."""
    captured: list[list[str]] = []

    def spawnv_passfds(path: str, args: list[str], passfds: list[int]) -> int:
        captured.append(args)
        return 0

    monkeypatch.setattr(util, "spawnv_passfds", spawnv_passfds)
    monkeypatch.setattr(spawn, "get_executable", lambda: EXE)
    with contextlib.suppress(OSError):  # the pretend child holds no pipe ends: later writes fail
        start()
    return captured[0]


def test_forkserver_command_from_cpython_is_recognized(
    mp: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resource_tracker, "ensure_running", lambda: None)
    # an fsbpyr app's __main__ has no file, so its parent sends no main_path (pytest's has one)
    monkeypatch.setattr(spawn, "get_preparation_data", lambda name: {"sys_path": [EXE]})
    server = forkserver.ForkServer()
    server.set_forkserver_preload(["__main__"])
    argv = _captured_command(monkeypatch, server.ensure_running)
    code = argv[argv.index("-c") + 1]
    listener, alive = (int(n) for n in re.search(r"main\((\d+), (\d+),", code).groups())  # type: ignore[union-attr]

    command = mp.child_command(argv)

    assert command is not None
    assert command[:2] == ("multiprocessing.forkserver", "main")
    assert command[2] == (listener, alive, ["__main__"])
    assert code.startswith(FORKSERVER.split("%d")[0])  # the template is CPython's text
    assert ("authkey_r" in command[3]) is AUTHKEY
    assert ("sys_argv" in command[3]) is SYS_ARGV


def test_forkserver_command_is_recognized(mp: ModuleType) -> None:
    keywords = {"sys_path": [EXE], **({"authkey_r": 9} if AUTHKEY else {})}
    argv = [EXE, *FLAGS, "-X", "dev", "-c", FORKSERVER % (3, 4, ["__main__"], keywords)]
    expected_keywords = {
        **({"authkey_r": 9} if AUTHKEY else {}),
        **({"sys_argv": []} if SYS_ARGV else {}),
    }

    assert mp.child_command(argv) == (
        "multiprocessing.forkserver", "main", (3, 4, ["__main__"]), expected_keywords, ["-c"],
    )  # fmt: skip  # sys_path is dropped: the payload-only sys.path stays


def test_forkserver_arguments_become_sys_argv_where_cpython_sends_them(mp: ModuleType) -> None:
    argv = [EXE, "-c", FORKSERVER % (3, 4, ["__main__"], {}), "app"]

    assert mp.child_command(argv) == (
        (
            "multiprocessing.forkserver", "main", (3, 4, ["__main__"]),
            {"sys_argv": ["app"]}, ["-c", "app"],
        )
        if SYS_ARGV
        else None  # 3.12's forkserver takes no arguments
    )  # fmt: skip


def test_forkserver_command_lines_of_other_versions_are_refused(mp: ModuleType) -> None:
    others = [
        "from multiprocessing.forkserver import main; main(3, 4, [], **{})",  # 3.12
        "import sys; from multiprocessing.forkserver import main; "
        "main(3, 4, [], sys_argv=sys.argv[1:], **{})",  # 3.13 and 3.14
    ]
    other = others[1] if sys.version_info < (3, 13) else others[0]

    assert mp.child_command([EXE, "-c", other]) is None
    assert (mp.child_command([EXE, "-c", FORKSERVER % (3, 4, [], {"authkey_r": 9})]) is None) is (
        not AUTHKEY
    )  # 3.12's and 3.13's forkserver send no authkey_r
    on_error = FORKSERVER % (3, 4, [], {"on_error": "fail"})
    assert (mp.child_command([EXE, "-c", on_error]) is None) is not ON_ERROR  # 3.15 sends it


def test_forkserver_on_error_is_passed_on_where_cpython_sends_it(
    mp: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = FORKSERVER % (3, 4, [], {"on_error": "explode"})
    assert mp.child_command([EXE, "-c", bad]) is None  # only the values CPython knows
    if not ON_ERROR:
        return  # 3.12 to 3.14 send none
    monkeypatch.setattr(resource_tracker, "ensure_running", lambda: None)
    monkeypatch.setattr(spawn, "get_preparation_data", lambda name: {"sys_path": [EXE]})
    server = forkserver.ForkServer()
    server.set_forkserver_preload(["__main__"], on_error="fail")  # type: ignore[call-arg]
    argv = _captured_command(monkeypatch, server.ensure_running)

    command = mp.child_command(argv)

    assert command is not None and command[3]["on_error"] == "fail"


def test_resource_tracker_command_from_cpython_is_recognized(
    mp: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = resource_tracker.ResourceTracker()
    argv = _captured_command(monkeypatch, tracker.ensure_running)
    os.close(tracker._fd)  # type: ignore[attr-defined]
    tracker._fd = tracker._pid = None  # type: ignore[attr-defined]  # nothing to stop in __del__
    descriptor = int(argv[-1].rsplit("main(", 1)[1].rstrip(")"))

    assert mp.child_command(argv) == (
        "multiprocessing.resource_tracker", "main", (descriptor,), {}, ["-c"],
    )  # fmt: skip


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


def test_children_of_any_other_program_are_refused(
    mp: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails by design under user-mode QEMU: /proc/<pid>/exe names the emulator for every
    process, so every parent "runs the same executable" (CONTRIBUTING.md)."""
    argv = [EXE, "-c", "from multiprocessing.resource_tracker import main;main(6)"]
    other = subprocess.Popen(["/bin/sleep", "60"])  # ours, our ids: only the program differs
    # not pytest's own parent: under pytest-xdist that is pytest, this executable
    monkeypatch.setattr(os, "getppid", lambda: other.pid)
    try:
        assert mp._parent_runs_this_executable() is False
        with pytest.raises(SystemExit, match="refusing"):
            mp.run_child(argv)
    finally:
        other.kill()
        other.wait()


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


def _forkserver_run(mp: ModuleType, monkeypatch: pytest.MonkeyPatch, entrypoint: str) -> list[str]:
    """The names imported (as the preloaded main) before forkserver.main runs."""
    events: list[str] = []
    fake = ModuleType("multiprocessing.forkserver")
    fake.main = lambda *args, **kwargs: events.append("main")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "multiprocessing.forkserver", fake)
    monkeypatch.setattr(mp, "_parent_runs_this_executable", lambda: True)

    def import_module(name: str) -> ModuleType:
        events.append(name)
        return fake

    monkeypatch.setattr(mp.importlib, "import_module", import_module)
    argv = [EXE, "-c", FORKSERVER % (3, 4, ["__main__"], {}), *(["x"] if SYS_ARGV else [])]
    monkeypatch.setattr(sys, "argv", argv[:])
    assert mp.run_child(argv, entrypoint) is True
    return events


def test_forkserver_preloads_the_module_of_a_function_entry_point(
    mp: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _forkserver_run(mp, monkeypatch, "pkg.cli:main")

    assert events == ["pkg.cli", "multiprocessing.forkserver", "main"]  # imported first


def test_forkserver_preloads_nothing_for_a_module_entry_point(
    mp: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _forkserver_run(mp, monkeypatch, "pkg.cli")  # a venv's `-m` run sends no main_path

    assert events == ["multiprocessing.forkserver", "main"]


def test_forkserver_preload_failure_is_left_to_the_workers(
    mp: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(name: str) -> ModuleType:
        raise RuntimeError(name)

    monkeypatch.setattr(mp, "_parent_runs_this_executable", lambda: True)
    monkeypatch.setattr(mp.importlib, "import_module", fail)

    mp._preload_entry_module("pkg.cli:main")  # does not raise
    assert not getattr(process.current_process(), "_inheriting", False)
