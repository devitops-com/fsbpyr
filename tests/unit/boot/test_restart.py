"""_fsbpyr_rt.restart and the boot's use of it: the ``[sys.executable] + sys.argv`` restart idiom
of gunicorn and Django runs the app with the argv a venv's script gets (plan D22, D44)."""

import importlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from fsbpyr.assemble import build_executable

from ...support.apps import demo_app
from .conftest import BOOT_DIR, FAKE_RUNTIME

EXE = os.readlink("/proc/self/exe")  # the test process stands for the app


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("restart") / "demo"
    build_executable(FAKE_RUNTIME, demo_app(), out)
    return out


@pytest.fixture
def restart(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.syspath_prepend(str(BOOT_DIR))
    monkeypatch.delitem(sys.modules, "_fsbpyr_rt.restart", raising=False)
    yield importlib.import_module("_fsbpyr_rt.restart")
    sys.modules.pop("_fsbpyr_rt.restart", None)


def test_the_executables_own_path_after_flags_is_dropped_with_them(restart: ModuleType) -> None:
    argv = ["/usr/bin/python", "-u", "-W", "ignore", "-Xdev", EXE, "--bind", ":80"]

    assert restart.restart_argv(argv) == [EXE, "--bind", ":80"]


def test_a_link_to_the_executable_counts_and_is_kept_as_given(
    restart: ModuleType, tmp_path: Path
) -> None:
    link = tmp_path / "multicall-name"
    link.symlink_to(EXE)

    assert restart.restart_argv(["x", str(link), "a"]) == [str(link), "a"]


@pytest.mark.parametrize(
    "argv",
    [
        ["x"],
        ["x", "-u"],
        ["x", "-", EXE],
        ["x", "-m", EXE],
        ["x", "-c", EXE],
        ["x", "-u", "serve", EXE],  # the first argument after the flags only
        ["x", "/nowhere/at/all"],
        ["x", "bad\0path"],
        ["x", "/"],  # another file
        ["x", "--output", EXE, "y"],  # the app's own option, not an interpreter flag
        ["x", "-k", EXE],
    ],
)
def test_anything_else_is_not_a_restart(restart: ModuleType, argv: list[str]) -> None:
    assert restart.restart_argv(argv) is None


@pytest.fixture
def started(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[ModuleType]:
    monkeypatch.setattr(sys, "executable", EXE)
    yield boot


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([EXE, "-u", EXE, "a", "-b"], [EXE, "a", "-b"]),
        (["app", EXE, "a"], [EXE, "a"]),
        ([EXE, "a", EXE], [EXE, "a", EXE]),  # an argument, not the restart
        ([EXE, "/etc/hosts"], [EXE, "/etc/hosts"]),
        ([EXE], [EXE]),
    ],
)
def test_the_boot_rewrites_argv_of_a_restart_only(
    started: ModuleType, monkeypatch: pytest.MonkeyPatch, argv: list[str], expected: list[str]
) -> None:
    monkeypatch.setattr(sys, "argv", list(argv))

    started._restart()

    assert sys.argv == expected


def test_the_common_start_loads_nothing(
    started: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", [EXE, "--verbose", "input.txt"])

    started._restart()

    assert "_fsbpyr_rt.restart" not in sys.modules
    assert sys.argv == [EXE, "--verbose", "input.txt"]
