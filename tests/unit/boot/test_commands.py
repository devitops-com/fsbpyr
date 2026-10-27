"""A multi-command executable at start: the choice of the command (_fsbpyr_rt.commands), the
boot's use of it, the selftest, the D44 exception for links, and the forkserver preload."""

import contextlib
import importlib
import posix
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from fsbpyr.assemble import build_executable

from ...support.apps import demo_app
from .conftest import FAKE_RUNTIME
from .test_boot import isolated_sys  # noqa: F401  # the fixture, used through usefixtures

TABLE = "alpha=pkg.cli:main\nbeta=pkg.cli\nsolo.tool=pkg"
EXE = "/opt/app/bin/tool"


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("commands") / "tool"
    build_executable(
        FAKE_RUNTIME,
        replace(demo_app(), entrypoint="alpha=pkg.cli:main\nbeta=pkg.cli\nsolo=pkg.sub.deep:DEPTH"),
        out,
    )
    return out


@pytest.fixture
def commands(boot: ModuleType) -> ModuleType:
    return importlib.import_module("_fsbpyr_rt.commands")


def test_a_table_is_name_target_lines(commands: ModuleType) -> None:
    assert commands.parse(TABLE) == {"alpha": "pkg.cli:main", "beta": "pkg.cli", "solo.tool": "pkg"}


@pytest.mark.parametrize(
    ("argv", "target", "expected_argv"),
    [
        (["/x/alpha", "a"], "pkg.cli:main", None),  # a link: the name of argv[0]
        (["beta", "a"], "pkg.cli", None),
        (["/x/solo.tool.py", "a"], "pkg", None),  # `.py` stripped
        (["/x/alpha.py"], "pkg.cli:main", None),
        ([EXE, "beta", "a", "--b"], "pkg.cli", ["beta", "a", "--b"]),  # argv[1], shifted
        (["/x/alpha", "beta"], "pkg.cli:main", None),  # argv[0] wins over argv[1]
    ],
)
def test_the_command_is_argv0_then_argv0_without_py_then_argv1(
    commands: ModuleType, argv: list[str], target: str, expected_argv: list[str] | None
) -> None:
    table = commands.parse(TABLE)
    original = list(argv)

    assert commands.choose(table, argv) == target
    assert argv == (expected_argv or original)


@pytest.mark.parametrize(
    "argv", [[EXE], [EXE, "gamma"], [EXE, "/opt/app/bin/alpha"], [EXE, "alpha.py"], [EXE, "-h"]]
)
def test_no_command_lists_them_on_stderr_and_exits_2(
    commands: ModuleType, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    with pytest.raises(SystemExit) as stop:
        commands.choose(commands.parse(TABLE), argv)

    out, err = capsys.readouterr()
    assert stop.value.code == 2 and out == ""
    assert err.startswith(("usage: tool COMMAND", "tool: unknown command"))
    assert "\n  alpha\n  beta\n  solo.tool\n" in err and argv[0] == EXE  # argv is not shifted


def test_a_sibling_is_a_command_named_otherwise_than_the_executable(commands: ModuleType) -> None:
    table = commands.parse(TABLE)

    assert commands.is_sibling(table, "/x/alpha", EXE)
    assert commands.is_sibling(table, "alpha.py", EXE)
    assert not commands.is_sibling(table, "/x/tool", EXE)
    assert not commands.is_sibling(table, "/x/other", EXE)
    assert not commands.is_sibling(table, EXE, "/y/alpha")  # a hard link: sys.executable is its own
    assert not commands.is_sibling(table, "alpha", "/y/alpha")


# --- run() ---------------------------------------------------------------------------------


@pytest.fixture
def started(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> Iterator[list[tuple[str, list[str]]]]:
    """run() with the target not called: the (entry point, sys.argv) it was to run."""
    seen: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(boot, "_resolve", lambda entry: lambda: seen.append((entry, sys.argv[:])))
    monkeypatch.setattr(sys, "executable", EXE)
    yield seen


@pytest.mark.usefixtures("isolated_sys")
@pytest.mark.parametrize(
    ("argv", "entry", "seen_argv"),
    [
        (["/x/alpha", "a"], "pkg.cli:main", ["/x/alpha", "a"]),
        ([EXE, "alpha", "a"], "pkg.cli:main", ["alpha", "a"]),
    ],
)
def test_run_calls_the_target_of_the_command(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch,
    started: list[tuple[str, list[str]]], argv: list[str], entry: str, seen_argv: list[str],
) -> None:  # fmt: skip
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit):  # sys.exit(main())
        boot.run()

    assert started == [(entry, seen_argv)]


@pytest.mark.usefixtures("isolated_sys")
def test_run_without_a_command_exits_2_and_runs_nothing(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch,
    started: list[tuple[str, list[str]]], capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    monkeypatch.setattr(sys, "argv", [EXE])

    with pytest.raises(SystemExit) as stop:
        boot.run()

    assert stop.value.code == 2 and started == []
    assert "alpha" in capsys.readouterr().err


@pytest.mark.usefixtures("isolated_sys")
def test_the_selftest_checks_every_target_whatever_argv0_is(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    resolved: list[str] = []
    monkeypatch.setattr(boot, "_check_entrypoint", resolved.append)
    monkeypatch.setitem(posix.environ, b"FSBPYR_SELFTEST", b"1")
    monkeypatch.setattr(sys, "argv", ["/x/.tool.1a2b3c4d.tmp"])  # the smoke test's file name

    boot.run()

    assert resolved == ["pkg.cli:main", "pkg.cli", "pkg.sub.deep:DEPTH"]
    assert capsys.readouterr().out == "fsbpyr selftest ok: demo\n"  # once


@pytest.mark.usefixtures("isolated_sys")
def test_the_selftest_imports_the_targets(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(posix.environ, b"FSBPYR_SELFTEST", b"1")
    monkeypatch.setattr(sys, "argv", ["/x/whatever"])

    boot.run()

    assert {"pkg.cli", "pkg.sub.deep"} <= set(sys.modules)
    assert capsys.readouterr().out == "fsbpyr selftest ok: demo\n"


# --- D44 and the sibling exception ---------------------------------------------------------


@pytest.fixture
def helper(boot: ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """_fsbpyr_rt.reexec.run_child, recording the argv it was asked about."""
    reexec = importlib.import_module("_fsbpyr_rt.reexec")
    asked: list[list[str]] = []

    def run_child(argv: list[str], *rest: object) -> bool:
        asked.append(argv[:])
        return False

    monkeypatch.setattr(reexec, "run_child", run_child)
    monkeypatch.setattr(sys, "executable", EXE)
    return asked


@pytest.mark.parametrize(
    ("argv", "asked"),
    [
        (["/x/alpha", "-m", "word"], False),  # a command's link: the command's own arguments
        (["alpha.py", "-c", "code", "x"], False),
        ([EXE, "-m", "word"], True),  # sys.executable -m: D44
        (["/x/other", "-m", "word"], True),
        (["/y/tool", "-m", "word"], True),  # not a command name, not the executable's
        ([EXE, "alpha", "-m", "word"], True),  # the dispatcher form is asked, and is no helper
    ],
)
def test_a_link_started_with_dash_m_is_not_a_helper_interpreter(
    boot: ModuleType, commands: ModuleType, monkeypatch: pytest.MonkeyPatch,
    helper: list[list[str]], argv: list[str], asked: bool,
) -> None:  # fmt: skip
    monkeypatch.setattr(sys, "argv", argv)

    boot._interpreter_child(commands.parse(TABLE))

    assert bool(helper) is asked


def test_a_single_command_build_asks_as_before(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch, helper: list[list[str]]
) -> None:
    monkeypatch.setattr(sys, "argv", ["/x/alpha", "-m", "word"])

    boot._interpreter_child()

    assert helper == [["/x/alpha", "-m", "word"]]


# --- the forkserver preload ----------------------------------------------------------------


@contextlib.contextmanager
def _started(
    argv: list[str], executable: str = "/bin/sleep", cwd: Path | None = None
) -> Iterator[int]:
    """The pid of a process that waits, with the command line ``argv``, once /proc shows it."""
    process = subprocess.Popen(
        argv, executable=executable, cwd=cwd, stdout=subprocess.DEVNULL
    )
    try:
        wanted = argv[0].encode() + b"\0"
        for _ in range(200):  # the command line shows a moment after the exec
            if Path(f"/proc/{process.pid}/cmdline").read_bytes().startswith(wanted):
                break
            time.sleep(0.01)
        yield process.pid
    finally:
        process.kill()
        process.wait()


def _parent(name: str) -> contextlib.AbstractContextManager[int]:
    return _started([name, "30"])


@pytest.mark.parametrize(
    ("name", "target"),
    [("/x/beta", "pkg.cli"), ("alpha", "pkg.cli:main"), ("/x/tool", ""), ("/x/unknown", "")],
)
def test_the_parents_command_is_the_one_the_preload_follows(
    commands: ModuleType, name: str, target: str
) -> None:
    mp = importlib.import_module("_fsbpyr_rt.mp")
    with _parent(name) as parent, pytest.MonkeyPatch.context() as patch:
        patch.setattr(mp.os, "getppid", lambda: parent)
        assert mp._parent_target(TABLE) == target


def test_the_dispatcher_form_of_the_parent_is_followed(
    commands: ModuleType, tmp_path: Path
) -> None:
    mp = importlib.import_module("_fsbpyr_rt.mp")
    (tmp_path / "alpha").write_text("sleep 30; true\n")  # sh runs this file: `tool alpha`
    with (
        _started(["/x/tool", "alpha"], "/bin/sh", tmp_path) as parent,
        pytest.MonkeyPatch.context() as patch,
    ):
        patch.setattr(mp.os, "getppid", lambda: parent)
        assert mp._parent_target(TABLE) == "pkg.cli:main"


def test_an_unreadable_parent_preloads_nothing(commands: ModuleType) -> None:
    mp = importlib.import_module("_fsbpyr_rt.mp")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mp.os, "getppid", lambda: 2**22 + 5)
        assert mp._parent_target(TABLE) == ""


def test_a_forkserver_preloads_the_module_of_the_parents_command(
    commands: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .test_mp import FORKSERVER, SYS_ARGV  # noqa: PLC0415

    mp = importlib.import_module("_fsbpyr_rt.mp")
    events: list[str] = []
    fake = ModuleType("multiprocessing.forkserver")
    fake.main = lambda *args, **kwargs: events.append("main")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "multiprocessing.forkserver", fake)
    monkeypatch.setattr(mp, "_parent_runs_this_executable", lambda: True)

    def import_module(name: str) -> Any:
        events.append(name)
        return fake

    monkeypatch.setattr(mp.importlib, "import_module", import_module)
    argv = [EXE, "-c", FORKSERVER % (3, 4, ["__main__"], {}), *(["x"] if SYS_ARGV else [])]
    monkeypatch.setattr(sys, "argv", argv[:])
    with _parent("/x/alpha") as parent:
        monkeypatch.setattr(mp.os, "getppid", lambda: parent)
        assert mp.run_child(argv, TABLE) is True

    assert events == ["pkg.cli", "multiprocessing.forkserver", "main"]
