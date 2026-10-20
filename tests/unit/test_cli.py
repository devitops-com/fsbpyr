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

    assert main(["--version"]) == 0
    assert capsys.readouterr().out == "0.1.0-6457a95b\n"


def test_executables_print_the_version_recorded_by_their_build(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    build_info = json.dumps({"git": {"commit": COMMIT, "tag": None}})
    monkeypatch.setitem(
        sys.modules, "_fsbpyr", types.SimpleNamespace(info=lambda: {"build_info": build_info})
    )
    monkeypatch.setattr("fsbpyr.vcs.git_state", lambda directory: pytest.fail("ran git"))

    assert main(["--version"]) == 0
    assert capsys.readouterr().out == "untagged-6457a95b\n"


def test_other_commands_never_run_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("fsbpyr.vcs._git", lambda *args, **kwargs: pytest.fail("ran git"))
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path))

    assert main(["runtime", "list"]) == 0


def test_main_without_command_prints_the_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == 0
    help_text = capsys.readouterr().out

    assert main([]) == 0
    assert capsys.readouterr() == (help_text, "")
    assert help_text.startswith("Usage: fsbpyr")


def test_module_entry_point() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fsbpyr", "--version"], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == version_string(git_state(ROOT))


def test_build_hints_are_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1"\n')
    seen: list[bool] = []

    def build(project: Path, config: object, **kwargs: object) -> None:
        seen.append(config.hints)  # type: ignore[attr-defined]
        raise OSError("stop here")

    monkeypatch.setattr("fsbpyr.build.build", build)

    main(["build", str(tmp_path)])
    main(["build", str(tmp_path), "--hints"])

    assert seen == [False, True]


def test_best_compression_is_opt_in_and_excludes_fast_compression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1"\n')
    seen: list[bool] = []

    def build(project: Path, config: object, **kwargs: object) -> None:
        seen.append(config.best_compression)  # type: ignore[attr-defined]
        raise OSError("stop here")

    monkeypatch.setattr("fsbpyr.build.build", build)

    main(["build", str(tmp_path)])
    main(["build", str(tmp_path), "--best-compression"])
    assert seen == [False, True]
    assert main(["build", str(tmp_path), "--best-compression", "--fast-compression"]) == 2
    assert "exclude each other" in capsys.readouterr().err


def test_fast_compression_is_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1"\n')
    seen: list[bool] = []

    def build(project: Path, config: object, **kwargs: object) -> None:
        seen.append(config.fast_compression)  # type: ignore[attr-defined]
        raise OSError("stop here")

    monkeypatch.setattr("fsbpyr.build.build", build)

    main(["build", str(tmp_path)])
    main(["build", str(tmp_path), "--fast-compression"])

    assert seen == [False, True]


def test_on_off_options_switch_off_what_the_project_switches_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "1"\n\n[tool.fsbpyr]\ninclude-source = false\n'
        "interpreter-reexec = true\nfast-compression = true\n"
    )
    seen: list[tuple[bool, bool, bool]] = []

    def build(project: Path, config: object, **kwargs: object) -> None:
        seen.append((config.include_source, config.interpreter_reexec,  # type: ignore[attr-defined]
                     config.fast_compression))  # type: ignore[attr-defined]  # fmt: skip
        raise OSError("stop here")

    monkeypatch.setattr("fsbpyr.build.build", build)

    main(["build", str(tmp_path)])
    main(["build", str(tmp_path), "--source", "--no-interpreter-reexec", "--no-fast-compression"])

    assert seen == [(False, True, True), (True, False, False)]


def test_help_has_a_short_option(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["build", "-h"]) == 0
    assert "--source / --no-source" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "collections"),
    [
        (["runtime", "list"], 1),
        (["inspect", "missing"], 1),
        ([], 0),
        (["build", "--help"], 0),
        (["inspect", "--help"], 0),
        (["runtime", "build", "--help"], 0),
        (["runtime", "list", "extra"], 0),
        (["runtime", "build", "--bogus"], 0),
        (["cache", "--help"], 0),
        (["completion", "bash"], 0),
        (["man"], 0),
    ],
)
def test_only_commands_that_run_collect_the_cache(
    argv: list[str], collections: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[None] = []
    monkeypatch.setattr("fsbpyr.cli._collect_when_due", lambda: calls.append(None))
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path))

    main(argv)

    assert len(calls) == collections


def test_runtime_list_on_empty_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path))

    assert main(["runtime", "list"]) == 0
    assert capsys.readouterr().out == ""


def test_runtime_list_shows_the_embedded_runtime_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cached = tmp_path / "runtimes" / "cp314-cached"
    cached.mkdir(parents=True)
    (cached / "runtime.json").write_text(
        '{"runtime_id": "cp314-cached", "python_version": "3.14.7"}'
    )
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(registry, "running_executable", lambda: tmp_path / "fsbpyr")
    embedded = types.SimpleNamespace(runtime_id="cp314-embedded", python_version="3.14.8")
    monkeypatch.setattr(registry.Runtime, "from_executable", lambda path: embedded)

    assert main(["runtime", "list"]) == 0
    assert capsys.readouterr().out == (
        "cp314-embedded  Python 3.14.8\ncp314-cached  Python 3.14.7\n"
    )


def test_a_failed_build_prints_its_warnings_before_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1"\n')

    def build(project: Path, config: object, **kwargs: object) -> None:
        error = OSError("no space left on device")
        error.add_note("unlocked build: dependencies resolved now, not reproducible")
        raise error

    monkeypatch.setattr("fsbpyr.build.build", build)

    assert main(["build", str(tmp_path)]) == 1
    assert capsys.readouterr().err.splitlines() == [
        "fsbpyr: warning: unlocked build: dependencies resolved now, not reproducible",
        "fsbpyr: error: no space left on device",
    ]


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


def test_an_unexpected_exception_prints_its_traceback_and_where_to_report_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(python: str, **kwargs: object) -> None:
        raise ZeroDivisionError("boom")

    monkeypatch.setattr(registry, "build_runtime", fail)

    assert main(["runtime", "build", "--python", "3.14"]) == 1
    err = capsys.readouterr().err
    assert "Traceback (most recent call last)" in err
    assert "ZeroDivisionError: boom" in err
    assert "fsbpyr: internal error (fsbpyr " in err
    assert "please report at https://github.com/devitops-com/fsbpyr/issues" in err
    assert "`fsbpyr inspect --json`" in err


@pytest.mark.parametrize("argv", [["build"], ["runtime", "build"]])
def test_build_commands_stop_first_on_any_python_but_3_14(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`uv pip install` and `uv tool install` ignore requires-python's upper bound."""
    monkeypatch.setattr("fsbpyr.cli._collect_when_due", lambda: pytest.fail("collected"))
    monkeypatch.setattr("fsbpyr.build.build", lambda *args, **kwargs: pytest.fail("built"))
    monkeypatch.setattr(registry, "build_runtime", lambda *args, **kwargs: pytest.fail("built"))
    monkeypatch.setattr(sys, "version_info", (3, 15, 0, "final", 0))

    assert main(argv) == 1
    assert capsys.readouterr() == (
        "",
        "fsbpyr: error: fsbpyr runs on CPython 3.14 only, not 3.15\n",
    )
