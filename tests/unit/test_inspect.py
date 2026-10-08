"""fsbpyr inspect: what an executable was built from, and with which fsbpyr."""

import dataclasses
import os
from pathlib import Path

import pytest

import fsbpyr
from fsbpyr.assemble import App, build_executable
from fsbpyr.cli import main
from fsbpyr.payload.convert import spec_from_payload
from fsbpyr.payload.reader import Executable
from fsbpyr.payload.spec import Dist
from fsbpyr.payload.writer import encode_payload, write_executable

from .boot.conftest import FAKE_RUNTIME

COMMIT = "4f9771de" + "0" * 32
DISTS = (Dist("aiagent", "aiagent-0.8.1.dist-info", "0.8.1"),
         Dist("click", "click-8.2.1.dist-info", "8.2.1"))  # fmt: skip


def _executable(tmp_path: Path, *build_info: tuple[str, object], dists: int = 2) -> Path:
    out = tmp_path / "aiagent"
    app = App("aiagent", "aiagent.cli:main", dists=DISTS[:dists], build_info=build_info)
    build_executable(FAKE_RUNTIME, app, out)
    return out


def _with_build_info(executable: Path, text: str) -> Path:
    with Executable.open(executable) as original:
        spec, stub = spec_from_payload(original.payload), original.stub_prefix()
    out = executable.with_name("tampered")
    write_executable(out, stub, encode_payload(dataclasses.replace(spec, build_info=text)))
    return out


def _inspect(path: Path, capsys: pytest.CaptureFixture[str]) -> list[str]:
    assert main(["inspect", str(path)]) == 0
    return capsys.readouterr().out.splitlines()


def test_inspect_shows_the_app_and_the_fsbpyr_it_was_built_with(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = _executable(
        tmp_path,
        ("project", {"name": "aiagent", "version": "0.8.1"}),
        ("git", {"commit": COMMIT, "tag": "v0.8.1"}),
        ("fsbpyr_version", "0.1.1-6d7ca726"),
        ("glibc", "2.28"),
    )

    lines = _inspect(executable, capsys)

    size = executable.stat().st_size / 1e6
    assert lines == [
        "app        aiagent 0.8.1 (0.8.1-4f9771de)",
        "built with fsbpyr 0.1.1-6d7ca726",
        "python     3.14.8, glibc >= 2.28",
        f"size       {size:.1f} MB, 2 distributions",
    ]


def test_builds_by_an_fsbpyr_without_git_show_its_release(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A wheel install of fsbpyr cannot see its commit: the version number still shows."""
    executable = _executable(tmp_path, ("fsbpyr_version", "untagged-unknown"), dists=1)

    lines = _inspect(executable, capsys)

    assert lines[1] == f"built with fsbpyr {fsbpyr.__version__}"
    assert lines[3].endswith(" 1 distribution")


def test_inspect_shows_what_older_builds_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Builds before 0.2.0 recorded neither the project nor fsbpyr's commit."""
    executable = _executable(
        tmp_path, ("fsbpyr", "0.1.0"), ("git", {"commit": COMMIT, "tag": None})
    )

    lines = _inspect(executable, capsys)

    assert lines[:3] == [
        "app        aiagent (untagged-4f9771de)",
        "built with fsbpyr 0.1.0",
        "python     3.14.8",
    ]


def test_text_from_the_file_cannot_drive_the_terminal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = _executable(
        tmp_path, ("project", {"name": "evil\x1b]0;title\x07", "version": "1\r2"}),
        ("fsbpyr_version", "\x1b[2J"),
    )  # fmt: skip

    lines = _inspect(executable, capsys)

    assert lines[0] == "app        'evil\\x1b]0;title\\x07' '1\\r2'"
    assert lines[1] == "built with fsbpyr '\\x1b[2J'"


def test_unexpected_build_information_shapes_are_tolerated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = _with_build_info(
        _executable(tmp_path), '{"git": "x", "project": ["y"], "glibc": 1}'
    )

    lines = _inspect(executable, capsys)

    assert lines[0] == "app        aiagent"


@pytest.mark.parametrize("text", ["[]", "null", "not json", "1" + "0" * 5000, "[" * 100_000])
def test_corrupt_build_information_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    executable = _with_build_info(_executable(tmp_path), text)

    assert main(["inspect", str(executable)]) == 1
    assert "has corrupt build information" in capsys.readouterr().err


def test_inspect_rejects_files_fsbpyr_did_not_build(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = tmp_path / "other"
    other.write_bytes(b"\x7fELF" + bytes(200))
    os.mkfifo(tmp_path / "fifo")  # opening it would wait for a writer forever

    assert main(["inspect", str(other)]) == 1
    assert main(["inspect", str(tmp_path / "missing")]) == 1
    assert main(["inspect", str(tmp_path / "fifo")]) == 1
    assert main(["inspect", str(tmp_path)]) == 1

    errors = capsys.readouterr().err.splitlines()
    assert errors[0].startswith(f"fsbpyr: error: {other} is not an executable fsbpyr built (")
    assert errors[1].startswith(f"fsbpyr: error: cannot read {tmp_path / 'missing'}")
    assert errors[2] == f"fsbpyr: error: {tmp_path / 'fifo'} is not a regular file"
    assert errors[3] == f"fsbpyr: error: {tmp_path} is not a regular file"
