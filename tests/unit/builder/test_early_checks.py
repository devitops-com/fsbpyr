"""Failures a build reports before uv stages anything, and the extras it checks."""

from pathlib import Path

import pytest

from fsbpyr.build import BuildError, build, check_before_staging
from fsbpyr.closure import ClosureError
from fsbpyr.config import BuildConfig
from fsbpyr.project import Project, ProjectError


def _project(tmp_path: Path, pyproject: str | None) -> Project:
    if pyproject is None:
        (tmp_path / "setup.py").write_text("")
    else:
        (tmp_path / "pyproject.toml").write_text(pyproject)
    return Project.load(tmp_path)


TWO_SCRIPTS = (
    '[project]\nname = "app"\nversion = "1"\n'
    '[project.scripts]\na = "app:a"\nb = "app:b"\n'
    "[project.optional-dependencies]\nfancy = []\n"
)
DYNAMIC = '[project]\nname = "app"\nversion = "1"\ndynamic = ["optional-dependencies", "scripts"]\n'


def test_two_static_scripts_without_entry_fail_before_uv_is_looked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(TWO_SCRIPTS)
    monkeypatch.setattr("fsbpyr.build.find_uv", lambda: pytest.fail("uv"))

    with pytest.raises(ClosureError, match="has console scripts: a, b; choose one with --entry"):
        build(tmp_path, BuildConfig())


def test_an_entry_lets_two_scripts_through(tmp_path: Path) -> None:
    project = _project(tmp_path, TWO_SCRIPTS)

    check_before_staging(project, BuildConfig(entry="a"))


@pytest.mark.parametrize("pyproject", [DYNAMIC, None])
def test_dynamic_and_setup_py_projects_pass_both_checks_with_an_extra(
    tmp_path: Path, pyproject: str | None
) -> None:
    project = _project(tmp_path, pyproject)

    check_before_staging(project, BuildConfig(extras=("fancy",), output=tmp_path / "out"))


def test_an_output_that_is_a_directory_fails_early(tmp_path: Path) -> None:
    project = _project(tmp_path, DYNAMIC)

    with pytest.raises(BuildError, match="is a directory"):
        check_before_staging(project, BuildConfig(output=tmp_path))


def test_an_extra_the_static_project_lacks_fails_early(tmp_path: Path) -> None:
    project = _project(tmp_path, TWO_SCRIPTS)

    check_before_staging(project, BuildConfig(entry="a", extras=("Fancy",)))
    with pytest.raises(ProjectError, match=r"no extra 'nope'.*its extras: fancy"):
        check_before_staging(project, BuildConfig(entry="a", extras=("nope",)))


STATIC_NO_SCRIPTS = '[project]\nname = "app"\nversion = "1"\n'
DYNAMIC_ENTRY_POINTS = STATIC_NO_SCRIPTS + 'dynamic = ["entry-points"]\n'
GUARDED = 'if __name__ == "__main__":\n    pass\n'


def test_a_static_project_with_no_script_and_no_candidate_fails_early(tmp_path: Path) -> None:
    project = _project(tmp_path, STATIC_NO_SCRIPTS)
    (tmp_path / "lib.py").write_text("X = 1\n")

    with pytest.raises(ClosureError, match="choose one with --entry"):
        check_before_staging(project, BuildConfig())
    check_before_staging(project, BuildConfig(entry="lib"))


@pytest.mark.parametrize(
    ("pyproject", "files"),
    [
        (DYNAMIC_ENTRY_POINTS, {}),
        (STATIC_NO_SCRIPTS, {"app.py": GUARDED}),
        (STATIC_NO_SCRIPTS, {"src/app/__main__.py": ""}),
        (STATIC_NO_SCRIPTS, {"app.py": "﻿" + GUARDED}),
    ],
)
def test_a_possible_candidate_lets_a_scriptless_project_through(
    tmp_path: Path, pyproject: str, files: dict[str, str]
) -> None:
    project = _project(tmp_path, pyproject)
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")

    check_before_staging(project, BuildConfig())
