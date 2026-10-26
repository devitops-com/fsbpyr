"""The boot's ``sys.argv[0]``: the path ``execve`` was given, made absolute, unless the caller
renamed the start or the launcher re-executed itself (plan D14)."""

import sys
from pathlib import Path
from types import ModuleType

import pytest

from fsbpyr.assemble import build_executable

from ...support.apps import demo_app
from .conftest import FAKE_RUNTIME


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("execve-argv0") / "demo"
    build_executable(FAKE_RUNTIME, demo_app(), out)
    return out


def _argv0(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch, execfn: str | None, argv0: str
) -> str:
    monkeypatch.setitem(boot.INFO, "execfn", execfn)
    monkeypatch.setattr(sys, "argv", [argv0, "x"])
    boot._execve_argv0()
    return sys.argv[0]


@pytest.mark.parametrize(
    ("execfn", "argv0"),
    [
        (None, "demo"),
        ("/proc/self/exe", "/proc/self/exe"),
        ("/dev/fd/3", "3"),
        ("/usr/bin/demo", "other"),  # exec -a
    ],
)
def test_argv0_stays_when_there_is_no_path_or_the_start_was_renamed(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch, execfn: str | None, argv0: str
) -> None:
    assert _argv0(boot, monkeypatch, execfn, argv0) == argv0


def test_a_start_through_a_path_directory_gets_the_absolute_path(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _argv0(boot, monkeypatch, "/usr/local/bin/demo", "demo") == "/usr/local/bin/demo"


def test_a_relative_path_is_made_absolute_against_the_working_directory(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    assert _argv0(boot, monkeypatch, "./demo", "./demo") == f"{tmp_path}/demo"


def test_dot_dot_is_not_collapsed(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    assert _argv0(boot, monkeypatch, "link/../demo", "demo") == f"{tmp_path}/link/../demo"


def test_a_removed_working_directory_leaves_argv0(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()

    assert _argv0(boot, monkeypatch, "./demo", "./demo") == "./demo"
