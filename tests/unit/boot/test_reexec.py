"""_fsbpyr_rt.reexec: ``-m`` and ``-c`` for children of the app itself (D23)."""

import importlib
import json
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from .conftest import BOOT_DIR

EXE = "/opt/app/bin/tool"
ON = json.dumps({"interpreter_reexec": True})
OFF = json.dumps({"interpreter_reexec": False})


def _runtime_modules() -> list[str]:
    return [n for n in sys.modules if n == "_fsbpyr_rt" or n.startswith("_fsbpyr_rt.")]


@pytest.fixture
def reexec(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.syspath_prepend(str(BOOT_DIR))
    for name in _runtime_modules():
        monkeypatch.delitem(sys.modules, name)
    yield importlib.import_module("_fsbpyr_rt.reexec")
    for name in _runtime_modules():
        del sys.modules[name]


@pytest.fixture
def child(reexec: ModuleType, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """A child the app started (pytest's parent is not pytest), with a __main__ of its own."""
    monkeypatch.setattr(reexec, "_parent_runs_this_executable", lambda: True)
    main = types.ModuleType("__main__")
    monkeypatch.setitem(sys.modules, "__main__", main)
    return main


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([EXE, "-u", "-B", "-m", "pkg.tool", "a", "-b"], ("-m", "pkg.tool", ["a", "-b"])),
        ([EXE, "-X", "dev", "-W", "ignore", "-c", "pass", "x"], ("-c", "pass", ["x"])),
        ([EXE, "-I", "-c", "pass"], ("-c", "pass", [])),
    ],
)
def test_module_or_code_after_interpreter_flags_is_recognized(
    reexec: ModuleType, argv: list[str], expected: tuple[str, str, list[str]]
) -> None:
    assert reexec.child_command(argv) == expected


@pytest.mark.parametrize(
    "argv",
    [[EXE], [EXE, "-m"], [EXE, "-q"], [EXE, "build", "-m", "x"], [EXE, "-", "-c", "pass"]],
)
def test_other_command_lines_belong_to_the_app(reexec: ModuleType, argv: list[str]) -> None:
    assert reexec.child_command(argv) is None


def test_nothing_runs_unless_the_build_allows_it(reexec: ModuleType, child: ModuleType) -> None:
    argv = [EXE, "-c", "ran = True"]

    assert reexec.run_child(argv, OFF) is False
    assert reexec.run_child(argv, json.dumps({})) is False  # built before D23
    assert not hasattr(child, "ran")


def test_a_caller_other_than_the_app_gets_the_app(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reexec, "_parent_runs_this_executable", lambda: False)
    argv = [EXE, "-c", "ran = True"]
    monkeypatch.setattr(sys, "argv", list(argv))

    assert reexec.run_child(argv, ON) is False
    assert not hasattr(child, "ran") and sys.argv == argv


def test_a_parent_with_another_uid_gets_the_app(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    mp = sys.modules["_fsbpyr_rt.mp"]
    same = types.SimpleNamespace(st_dev=1, st_ino=2)  # the same executable, other credentials
    monkeypatch.setattr(mp.os, "stat", lambda path: same)
    uid, gid = mp.os.getresuid()[:2], mp.os.getresgid()[:2]
    fake = f"Uid:\t{uid[0] + 1}\t{uid[1]}\t{uid[1]}\t{uid[1]}\nGid:\t{gid[0]}\t{gid[1]}\n"
    monkeypatch.setattr(mp, "_read_parent_status", lambda: fake)
    monkeypatch.setattr(reexec, "_parent_runs_this_executable", mp._parent_runs_this_executable)
    argv = [EXE, "-c", "ran = True"]

    assert reexec.run_child(argv, ON) is False
    assert not hasattr(child, "ran")


def test_code_runs_in_main_with_the_argv_cpython_leaves(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", [EXE, "-B", "-c", "import sys; seen = sys.argv[:]", "x"])

    assert reexec.run_child(sys.argv, ON) is True
    assert child.seen == ["-c", "x"]


def test_a_module_runs_as_main_with_its_path_first(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "helper_mod.py").write_text("import sys\nseen = (__name__, sys.argv[:])\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "argv", [EXE, "-u", "-B", "-m", "helper_mod", "a"])

    assert reexec.run_child(sys.argv, ON) is True
    assert child.seen == ("__main__", [str(tmp_path / "helper_mod.py"), "a"])
