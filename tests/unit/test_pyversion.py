"""The version rule (plan, Phase 9): which Python a project builds with."""

from pathlib import Path

import pytest

from fsbpyr.project import Project, ProjectError
from fsbpyr.pyversion import resolve

pytestmark = pytest.mark.version_rule


def _project(tmp_path: Path, requires: str | None, pin: str | None = None) -> Project:
    lines = ["[project]", 'name = "app"', 'version = "1"']
    if requires is not None:
        lines.append(f'requires-python = "{requires}"')
    (tmp_path / "pyproject.toml").write_text("\n".join(lines) + "\n")
    if pin is not None:
        (tmp_path / ".python-version").write_text(pin + "\n")
    return Project.load(tmp_path)


@pytest.mark.parametrize(("requires", "version"), [
    (">=3.10", "3.12"),  # a lower bound: the lowest supported version it allows
    (">=3.11", "3.12"),
    (">=3.12", "3.12"),
    (">=3.13", "3.13"),
    (">=3.14", "3.14"),
    (">=3.15", "3.15"),
    (">3.12", "3.12"),  # 3.12.15 is above 3.12
    (">=3.12,<3.14", "3.12"),
    (">=3.13,<3.15", "3.13"),
    ("~=3.13.1", "3.13"),
    (">=3.9,!=3.12.*", "3.13"),
    ("<3.14", "3.13"),  # only an upper bound: the highest below it
    ("<=3.13", "3.12"),  # 3.13.16 is above 3.13
    ("<3.13", "3.12"),
    ("<4", "3.15"),
    ("<3.16", "3.15"),
    ("<3.15", "3.14"),
    ("==3.12.*", "3.12"),  # a pinned minor as pinned
    ("==3.13.*", "3.13"),
    ("==3.14.*", "3.14"),
    ("==3.15.*", "3.15"),
    ("~=3.12.0", "3.12"),
    ("!=3.12.*", "3.14"),  # no bound that picks one: the Python of the running fsbpyr
    (None, "3.14"),
])  # fmt: skip
def test_requires_python_gives_the_runtime(
    tmp_path: Path, requires: str | None, version: str
) -> None:
    assert resolve(_project(tmp_path, requires), None, own="3.14").version == version


@pytest.mark.parametrize("own", ["3.12", "3.13", "3.14", "3.15"])
def test_a_project_without_a_picking_bound_uses_the_running_fsbpyrs_python(
    tmp_path: Path, own: str
) -> None:
    assert resolve(_project(tmp_path, None), None, own=own).version == own
    assert resolve(_project(tmp_path, "!=3.11.*"), None, own=own).version == own


@pytest.mark.parametrize("requires", ["<3.12", ">=3.16", "==3.11.*", "<3.12,>=3.10", "==3.99.*"])
def test_no_supported_version_is_an_error_naming_the_supported_ones(
    tmp_path: Path, requires: str
) -> None:
    with pytest.raises(ProjectError, match=rf"{requires!r}.*3\.12, 3\.13, 3\.14 and 3\.15"):
        resolve(_project(tmp_path, requires), None, own="3.14")


def test_an_invalid_requires_python_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="invalid requires-python"):
        resolve(_project(tmp_path, "three"), None, own="3.14")


# --- --python and python = "X.Y" ---------------------------------------------------------------


def test_the_requested_version_wins_over_the_pin_and_the_rule(tmp_path: Path) -> None:
    project = _project(tmp_path, ">=3.12", pin="3.13")

    assert resolve(project, "3.14", own="3.12").version == "3.14"
    assert resolve(project, "3.14", own="3.12").warnings == ()


@pytest.mark.parametrize(("requested", "message"), [
    ("3.11", "not supported; fsbpyr builds for 3.12, 3.13, 3.14 and 3.15"),
    ("3.16", "not supported"),
    ("3", "not a version X.Y"),
    ("3.13.1", "not a version X.Y"),
    ("latest", "not a version X.Y"),
])  # fmt: skip
def test_an_unsupported_or_malformed_request_is_an_error(
    tmp_path: Path, requested: str, message: str
) -> None:
    with pytest.raises(ProjectError, match=message):
        resolve(_project(tmp_path, ">=3.10"), requested, own="3.14")


def test_a_request_outside_requires_python_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match=r"Python 3\.14 is outside requires-python '<3\.14'"):
        resolve(_project(tmp_path, "<3.14"), "3.14", own="3.14")


# --- .python-version ---------------------------------------------------------------------------


@pytest.mark.parametrize(("requires", "pin", "version"), [
    (">=3.10", "3.13", "3.13"),  # a supported pin inside the range beats the rule
    (">=3.10", "3.14.8", "3.14"),
    (">=3.12", "cpython@3.13.2", "3.13"),
    ("<3.14", "3.12", "3.12"),
    (None, "3.13", "3.13"),
    ("~=3.12.1", "3.12.4", "3.12"),
])  # fmt: skip
def test_a_supported_pin_inside_the_range_is_used(
    tmp_path: Path, requires: str | None, pin: str, version: str
) -> None:
    choice = resolve(_project(tmp_path, requires, pin=pin), None, own="3.14")

    assert (choice.version, choice.warnings) == (version, ())


@pytest.mark.parametrize(("requires", "pin"), [
    (">=3.12,<3.14", "3.14"),
    (">=3.13", "3.12.4"),
    (">=3.12", "3.11"),  # unsupported, and outside the range: the range is the error
    (">=3.10", "3.9"),
])  # fmt: skip
def test_a_pin_outside_the_range_is_an_error(tmp_path: Path, requires: str, pin: str) -> None:
    with pytest.raises(ProjectError, match=rf"\.python-version pins {pin}, outside requires-py"):
        resolve(_project(tmp_path, requires, pin=pin), None, own="3.14")


@pytest.mark.parametrize("pin", ["3.11", "3.10.4", "pypy3.10", "3.13t", "3"])
def test_an_unsupported_pin_is_a_warning_and_the_rule_applies(tmp_path: Path, pin: str) -> None:
    choice = resolve(_project(tmp_path, ">=3.10", pin=pin), None, own="3.14")

    assert choice.version == "3.12"
    assert len(choice.warnings) == 1
    assert f".python-version pins {pin}" in choice.warnings[0]
    assert "3.12, 3.13, 3.14 and 3.15" in choice.warnings[0]


def test_the_request_is_not_second_guessed_by_the_pin(tmp_path: Path) -> None:
    project = _project(tmp_path, ">=3.12", pin="3.9")  # outside the range, but --python decides

    assert resolve(project, "3.12", own="3.14").version == "3.12"
