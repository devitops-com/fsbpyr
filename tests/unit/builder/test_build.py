"""Smoke test and entry-point checks of the build orchestrator."""

from pathlib import Path

import pytest

from fsbpyr.build import BuildError, glibc_report, smoke_test
from fsbpyr.cli import main

from ...support.elf import make_elf


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


STUB_2_25 = make_elf(versions=(("libc.so.6", ("GLIBC_2.2.5", "GLIBC_2.25")),))


@pytest.mark.parametrize(
    ("natives", "expected", "warns"),
    [
        (None, "2.25", False),
        (((2, 17), "a/_x.so"), "2.25", False),
        (((2, 28), "a/_x.so"), "2.28", False),  # within the baseline the wheels were chosen for
        (((2, 34), "a/_new.so"), "2.34", True),
    ],
)
def test_glibc_requirement_is_the_highest_of_stub_and_natives(
    natives: tuple[tuple[int, int], str] | None, expected: str, warns: bool
) -> None:
    requirement, warnings = glibc_report(STUB_2_25, natives, baseline=(2, 28))

    assert requirement == expected
    assert bool(warnings) is warns
    assert not warnings or "a/_new.so needs glibc 2.34, more than the 2.28 baseline" in warnings[0]


def test_glibc_requirement_of_an_unreadable_stub_is_unknown() -> None:
    assert glibc_report(b"\x7fELF-fake", None, baseline=(2, 28)) == (None, [])
