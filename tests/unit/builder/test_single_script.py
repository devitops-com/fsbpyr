"""One script as the project (research UC8): what is packaged, what its PEP 723 block decides, the
script lock, and that nothing is written beside it."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from fsbpyr.build import lock_warnings
from fsbpyr.config import ConfigError, load_config
from fsbpyr.loose import Package
from fsbpyr.project import LockSource, Project, ProjectError
from fsbpyr.pyversion import resolve
from fsbpyr.staging import SOURCE_DATE_EPOCH, StagingOptions, staging_plan

GUARD = 'if __name__ == "__main__":\n    print("hi")\n'
SHEBANG = "#!/usr/bin/env -S uv run --script\n"
UV = Path("/u/uv")


def _dir(root: Path, files: Mapping[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _block(*lines: str) -> str:
    return "# /// script\n" + "".join(f"# {line}\n" for line in lines) + "# ///\n"


def _listing(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def test_a_file_is_a_loose_project_named_after_its_stem(tmp_path: Path) -> None:
    root = _dir(tmp_path / "ops", {
        "check-disk.py": _block('requires-python = ">=3.12"', 'dependencies = ["click>=8"]',
                                "[tool.fsbpyr]", "strict = true") + GUARD,
        "pyproject.toml": "this is not read\n",
    })  # fmt: skip

    project = Project.load(root / "check-disk.py")

    assert (project.loose, project.bare, project.script) == (True, True, root / "check-disk.py")
    assert (project.directory, project.name) == (root, "check-disk")
    assert project.scripts == {"check-disk": "_fsbpyr_scripts.check_disk"}
    assert (project.requires_python, project.dependencies) == (">=3.12", ("click>=8",))
    assert project.tool == {"strict": True}
    assert project.uv_root is None


def test_an_extensionless_file_with_a_uv_run_shebang_is_a_script(tmp_path: Path) -> None:
    root = _dir(tmp_path / "ops", {"report": SHEBANG + "print(1)\n"})

    project = Project.load(root / "report")

    assert project.scripts == {"report": "_fsbpyr_scripts._x_report"}


@pytest.mark.parametrize(
    ("name", "text"),
    [("notes.txt", "x\n"), ("plain", "no shebang\n"), ("tool.sh", "#!/usr/bin/env python3\n"),
     ("shell", "#!/bin/bash\n")],
)  # fmt: skip
def test_other_files_are_refused(tmp_path: Path, name: str, text: str) -> None:
    root = _dir(tmp_path, {name: text})

    with pytest.raises(ProjectError, match="not a Python script"):
        Project.load(root / name)


def test_a_script_with_no_command_name_is_refused(tmp_path: Path) -> None:
    root = _dir(tmp_path, {"_private.py": GUARD})

    with pytest.raises(ProjectError, match="not a command name"):
        Project.load(root / "_private.py")


def test_two_blocks_are_an_error(tmp_path: Path) -> None:
    twice = _block("dependencies = []") + "x = 1\n" + _block("dependencies = []")
    root = _dir(tmp_path, {"a.py": twice})

    with pytest.raises(ProjectError, match="more than one"):
        Project.load(root / "a.py")


def test_only_the_import_closure_of_the_script_is_packaged(tmp_path: Path) -> None:
    files = {f"unrelated_{n}.py": GUARD for n in range(200)}
    files |= {
        "report.py": "import util\nfrom pkg import inner\nfrom . import sibling\n" + GUARD,
        "util.py": "import deep\n",
        "deep.py": "X = 1\n",
        "pkg/__init__.py": "",
        "pkg/inner.py": "from . import helper\n",
        "pkg/helper.py": "",
        "pkg/not_imported.py": "",
        "sibling.py": "import json\n",
        "json.py": "SHADOWS = True\n",
        "secrets.py": "KEY = 1\n",
        ".env": "SECRET=1\n",
    }
    root = _dir(tmp_path / "ops", files)

    project = Project.load(root / "report.py")

    package = Package.load(root, script=root / "report.py")

    assert sorted(package.sources) == ["deep.py", "json.py", "pkg/__init__.py", "pkg/helper.py",
                                       "pkg/inner.py", "report.py", "sibling.py", "util.py"]
    assert package.commands == {"report": "_fsbpyr_scripts.report"} == project.scripts


def test_a_directory_added_to_sys_path_from_file_is_followed(tmp_path: Path) -> None:
    root = _dir(tmp_path / "ops", {
        "tool.py": "import os, sys\nsys.path.append(os.path.join(os.path.dirname("
        "os.path.abspath(__file__)), 'pylib'))\nimport extra\n",
        "pylib/extra.py": "X = 1\n",
    })  # fmt: skip

    assert sorted(Package.load(root, script=root / "tool.py").sources) == [
        "pylib/extra.py", "tool.py"]  # fmt: skip


def test_a_file_stored_under_another_name_keeps_its_origin_and_may_not_collide(
    tmp_path: Path,
) -> None:

    root = _dir(tmp_path / "ops", {"check-disk.py": "import check_disk\n", "check_disk.py": ""})

    with pytest.raises(ProjectError, match=r"check-disk\.py is stored as check_disk\.py"):
        Package.load(root, script=root / "check-disk.py")
    _dir(root, {"check-disk.py": "import util\n", "util.py": ""})
    package = Package.load(root, script=root / "check-disk.py")
    assert package.origins == {"check_disk.py": "check-disk.py", "util.py": "util.py"}


def test_a_module_that_links_outside_the_directory_is_an_error(tmp_path: Path) -> None:

    outside = _dir(tmp_path / "other", {"secret.py": "KEY = 1\n"})
    root = _dir(tmp_path / "ops", {"a.py": "import secret\n"})
    (root / "secret.py").symlink_to(outside / "secret.py")

    with pytest.raises(ProjectError, match="outside the script's directory"):
        Package.load(root, script=root / "a.py")


def test_loading_a_script_writes_nothing_beside_it(tmp_path: Path) -> None:
    root = _dir(tmp_path / "ops", {"a.py": _block("dependencies = []") + "import b\n", "b.py": ""})
    before = _listing(root)

    Project.load(root / "a.py")

    assert _listing(root) == before


@pytest.mark.parametrize("lines", [["tool = 1"], ["[tool]", "fsbpyr = 1"]])
def test_a_block_whose_tool_tables_are_not_tables_is_an_error(
    tmp_path: Path, lines: list[str]
) -> None:
    root = _dir(tmp_path, {"a.py": _block(*lines) + GUARD})

    with pytest.raises(ProjectError, match=r"\[tool.fsbpyr\].*must be a table"):
        Project.load(root / "a.py")


def test_unknown_tool_keys_in_the_block_are_rejected_and_known_ones_count(tmp_path: Path) -> None:
    root = _dir(tmp_path, {
        "bad.py": _block("[tool.fsbpyr]", "fast-compresion = true") + GUARD,
        "good.py": _block("[tool.fsbpyr]", "fast-compression = true", 'output = "out/good"')
        + GUARD,
    })  # fmt: skip

    with pytest.raises(ConfigError, match=r"unknown \[tool.fsbpyr\] key 'fast-compresion'"):
        load_config(Project.load(root / "bad.py"))
    config = load_config(Project.load(root / "good.py"))
    assert config.fast_compression and config.output == root / "out/good"


def test_requires_python_of_the_block_chooses_the_python_as_a_projects_does(tmp_path: Path) -> None:
    root = _dir(tmp_path, {
        "old.py": _block('requires-python = "==3.12.*"') + GUARD,
        "none.py": _block('requires-python = "<3.12"') + GUARD,
    })  # fmt: skip

    assert resolve(Project.load(root / "old.py"), None, own="3.14").version == "3.12"
    with pytest.raises(ProjectError, match="allows none"):
        resolve(Project.load(root / "none.py"), None, own="3.14")
    with pytest.raises(ProjectError, match="outside requires-python"):
        resolve(Project.load(root / "old.py"), "3.13", own="3.14")


def test_the_directorys_requirements_and_pyproject_are_not_the_scripts(tmp_path: Path) -> None:
    root = _dir(tmp_path, {
        "a.py": _block('dependencies = ["x"]') + GUARD, "requirements.txt": "click==8\n",
        "uv.lock": "", "pylock.toml": "", "poetry.lock": "",
    })  # fmt: skip

    project = Project.load(root / "a.py")

    assert project.lock_source() is LockSource.UNLOCKED
    assert lock_warnings(project, LockSource.UNLOCKED, None) == [
        "unlocked build: dependencies resolved now, not reproducible"]  # fmt: skip


def test_a_script_lock_beside_the_file_locks_the_build(tmp_path: Path) -> None:
    root = _dir(tmp_path, {"a.py": _block('dependencies = ["x"]') + GUARD, "a.py.lock": "",
                           "b": SHEBANG + "print(1)\n", "b.lock": ""})  # fmt: skip

    for name in ("a.py", "b"):
        project = Project.load(root / name)
        assert project.lock_source() is LockSource.SCRIPT_LOCK
        assert project.lock_path(LockSource.SCRIPT_LOCK) == root / f"{name}.lock".replace(
            "b.py", "b")  # fmt: skip
        assert lock_warnings(project, LockSource.SCRIPT_LOCK, None) == []


def test_the_script_lock_is_synced_into_the_staging_environment(tmp_path: Path) -> None:
    root = _dir(tmp_path / "ops", {"a.py": GUARD, "a.py.lock": ""})
    project = Project.load(root / "a.py")
    work = tmp_path / "work"

    plan = staging_plan(project, LockSource.SCRIPT_LOCK, UV, work,
                        StagingOptions("3.14", offline=True))  # fmt: skip

    stage = work / "stage"
    assert plan.env == {"VIRTUAL_ENV": str(stage), SOURCE_DATE_EPOCH: plan.env[SOURCE_DATE_EPOCH]}
    assert plan.build == []
    assert plan.install == [
        ["/u/uv", "sync", "--script", str(root / "a.py"), "--active", "--locked", "--python",
         "3.14", "--offline", "--quiet"],
        ["/u/uv", "pip", "install", "--python", str(stage / "bin" / "python"), "--no-deps",
         "--offline", "--quiet", "{wheel}"],
    ]  # fmt: skip
