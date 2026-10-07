"""Smoke test and entry-point checks of the build orchestrator."""

from pathlib import Path

import pytest

from fsbpyr.build import BuildError, smoke_test
from fsbpyr.cli import main


def _script(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def test_smoke_test_accepts_selftest_output(tmp_path: Path) -> None:
    executable = _script(
        tmp_path / "app", 'test "$FSBPYR_SELFTEST" = 1 && echo "fsbpyr selftest ok: app"'
    )

    smoke_test(executable, "app")


@pytest.mark.parametrize(
    "body", ["exit 3", "echo something else", "echo 'fsbpyr selftest ok: other'"]
)
def test_smoke_test_rejects_failures(tmp_path: Path, body: str) -> None:
    with pytest.raises(BuildError, match="smoke test"):
        smoke_test(_script(tmp_path / "app", body), "app")


def test_build_without_project_files_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["build", str(tmp_path)]) == 1
    assert "has no pyproject.toml or setup.py" in capsys.readouterr().err


def test_output_must_not_be_a_directory(tmp_path: Path) -> None:
    from fsbpyr.build import check_output  # noqa: PLC0415

    with pytest.raises(BuildError, match="is a directory"):
        check_output(tmp_path)
    check_output(tmp_path / "new")
