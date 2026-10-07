"""Minimal CLI surface available in Phase 1."""

import json
import subprocess
import sys
import tomllib
import types
from pathlib import Path

import pytest

import fsbpyr
from fsbpyr.cli import main
from fsbpyr.runtime import registry
from fsbpyr.runtime.toolchain import ToolchainError
from fsbpyr.vcs import git_state, version_string

ROOT = Path(__file__).parents[2]
COMMIT = "6457a95b" + "0" * 32


def test_version_matches_pyproject() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

    assert fsbpyr.__version__ == project["version"]


def test_main_prints_the_version_of_the_checkout(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "fsbpyr.vcs.git_state", lambda directory: {"commit": COMMIT, "tag": "v0.1.0"}
    )

    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out == "0.1.0-6457a95b\n"


def test_executables_print_the_version_recorded_by_their_build(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    build_info = json.dumps({"git": {"commit": COMMIT, "tag": None}})
    monkeypatch.setitem(
        sys.modules, "_fsbpyr", types.SimpleNamespace(info=lambda: {"build_info": build_info})
    )
    monkeypatch.setattr("fsbpyr.vcs.git_state", lambda directory: pytest.fail("ran git"))

    with pytest.raises(SystemExit):
        main(["--version"])

    assert capsys.readouterr().out == "untagged-6457a95b\n"


def test_other_commands_never_run_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("fsbpyr.vcs._git", lambda *args, **kwargs: pytest.fail("ran git"))
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path))

    assert main(["runtime", "list"]) == 0


def test_main_without_command_shows_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage: fsbpyr" in capsys.readouterr().err


def test_module_entry_point() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fsbpyr", "--version"], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == version_string(git_state(ROOT))


def test_runtime_list_on_empty_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path))

    assert main(["runtime", "list"]) == 0
    assert capsys.readouterr().out == ""


def test_runtime_build_reports_errors_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(python: str, **kwargs: object) -> None:
        raise ToolchainError(f"clang >= 22 with ld.lld not found for {python}")

    monkeypatch.setattr(registry, "build_runtime", fail)

    assert main(["runtime", "build", "--python", "3.14"]) == 1
    assert capsys.readouterr().err.strip() == (
        "fsbpyr: error: clang >= 22 with ld.lld not found for 3.14"
    )
