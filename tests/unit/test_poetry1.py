"""A Poetry 1 pyproject.toml with package-mode = false and no [project] read as a [project]."""

import tomllib
from typing import Any

import pytest
from packaging.specifiers import SpecifierSet

from fsbpyr.poetry1 import project_table
from fsbpyr.project import ProjectError, _without_build_system, is_unpackaged


def _data(poetry: str) -> dict[str, Any]:
    return tomllib.loads(f"[tool.poetry]\npackage-mode = false\n{poetry}")


def _dependencies(**constraints: str) -> list[str]:
    body = "".join(f'{name} = "{spec}"\n' for name, spec in constraints.items())
    table = project_table(_data(f"[tool.poetry.dependencies]\n{body}"))
    return list(table["dependencies"])


@pytest.mark.parametrize(
    ("constraint", "specifier"),
    [("^1.2.3", ">=1.2.3,<2"), ("^0.2.3", ">=0.2.3,<0.3"), ("^0.0.3", ">=0.0.3,<0.0.4"),
     ("^0", ">=0,<1"), ("~1.2.3", ">=1.2.3,<1.3"), ("~1.2", ">=1.2,<1.3"), ("~1", ">=1,<2"),
     ("1.2.3", "==1.2.3"), ("1.2.*", "==1.2.*"), (">=1,<3", "<3,>=1"), ("*", "")],
)  # fmt: skip
def test_a_poetry_constraint_becomes_a_pep_440_specifier(constraint: str, specifier: str) -> None:
    assert _dependencies(pkg=constraint) == [f"pkg{SpecifierSet(specifier)}"]


def test_alternatives_are_refused() -> None:
    with pytest.raises(ProjectError, match="alternatives"):
        _dependencies(pkg="^1 || ^3")


def test_the_table_holds_name_version_python_dependencies_extras_and_scripts() -> None:
    data = _data(
        'name = "app"\nversion = "1.0"\n[tool.poetry.dependencies]\npython = "^3.12"\n'
        'click = ">=8"\nextra-lib = {version = "^2", optional = true}\n'
        'win = {version = "*", markers = "sys_platform == \'win32\'"}\n'
        '[tool.poetry.extras]\nfancy = ["extra-lib"]\n[tool.poetry.scripts]\napp = "app:main"\n')

    assert project_table(data) == {
        "name": "app", "version": "1.0", "requires-python": "<4,>=3.12",
        "dependencies": ["click>=8", "win; sys_platform == 'win32'"],
        "optional-dependencies": {"fancy": ["extra-lib<3,>=2"]}, "scripts": {"app": "app:main"},
    }  # fmt: skip


def test_a_git_dependency_is_refused() -> None:
    data = _data('[tool.poetry.dependencies]\nlib = {git = "https://example.com/lib.git"}\n')

    with pytest.raises(ProjectError, match="git dependency"):
        project_table(data)


def test_only_package_mode_false_without_a_project_table_is_unpackaged() -> None:
    assert is_unpackaged(_data(""))
    assert _without_build_system(_data(""))
    assert not is_unpackaged({"tool": {"poetry": {"name": "x"}}})
    assert not is_unpackaged({**_data(""), "project": {"name": "x"}})
