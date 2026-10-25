"""Projects without a build system: the trigger, the files fsbpyr packages, the wheel it writes,
the commands it finds, and the static import check of those commands."""

import base64
import csv
import hashlib
import io
import shutil
import subprocess
import zipfile
from collections.abc import Mapping
from pathlib import Path

import pytest

from fsbpyr.build import (
    BuildError,
    StrictError,
    _unresolved_warning,
    build,
    check_script_imports,
    lock_warnings,
)
from fsbpyr.config import BuildConfig
from fsbpyr.importcheck import check_command, top_level_names
from fsbpyr.loose import Package
from fsbpyr.project import LockSource, Project, ProjectError
from fsbpyr.staging import (
    EPOCH_FLOOR,
    StagingError,
    StagingOptions,
    _write_loose_wheel,
    staging_plan,
)

HEAD = '[project]\nname = "My_Tool"\nversion = "1.2"\nrequires-python = ">=3.12"\n'
GUARD = 'if __name__ == "__main__":\n    print("hi")\n'


def _tree(root: Path, pyproject: str = HEAD, extra: Mapping[str, str] | None = None, /,
          **files: str) -> Path:  # fmt: skip
    """A project at ``root``; ``files`` maps a path (``__`` for ``/``, ``_py`` for ``.py``),
    ``extra`` a path as it is."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(pyproject)
    named = {key.replace("__", "/").replace("_py", ".py"): text for key, text in files.items()}
    for rel, text in {**named, **(extra or {})}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


# --- the trigger -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pyproject", "loose"),
    [
        (HEAD, True),
        (HEAD + "[build-system]\nrequires = ['uv_build']\nbuild-backend = 'uv_build'\n", False),
        (HEAD + "[build-system]\nrequires = ['poetry-core']\nbuild-backend = 'x'\n"
         "[tool.poetry]\npackage-mode = false\n", True),
        (HEAD + "[build-system]\nrequires = ['pdm-backend']\nbuild-backend = 'x'\n"
         "[tool.pdm]\ndistribution = false\n", True),
        (HEAD + "[tool.pdm]\ndistribution = true\n[build-system]\nrequires = []\n", False),
        (HEAD + "[tool.uv.workspace]\nmembers = ['a']\n", False),
    ],
)  # fmt: skip
def test_a_project_table_without_a_build_system_is_built_by_fsbpyr(
    tmp_path: Path, pyproject: str, loose: bool
) -> None:
    (tmp_path / "pyproject.toml").write_text(pyproject)

    assert Project.load(tmp_path).loose is loose


def test_a_pyproject_of_settings_only_beside_no_python_file_has_nothing_to_package(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.fsbpyr]\nstrict = true\n")

    with pytest.raises(ProjectError, match="no Python files to package"):
        Project.load(tmp_path)


def test_a_setup_py_project_is_not_loose(tmp_path: Path) -> None:
    (tmp_path / "setup.py").write_text("")

    assert not Project.load(tmp_path).loose


def test_a_poetry_or_pdm_lock_is_not_read_and_the_build_says_so(tmp_path: Path) -> None:
    project = Project.load(_tree(tmp_path))
    (tmp_path / "poetry.lock").write_text("")
    (tmp_path / "pdm.lock").write_text("")

    warnings = lock_warnings(project, LockSource.UNLOCKED, None)

    assert any(w.startswith("poetry.lock is not read") for w in warnings)
    assert any(w.startswith("pdm.lock is not read") for w in warnings)


# --- the files -------------------------------------------------------------------------------


def test_only_python_files_are_packaged(tmp_path: Path) -> None:
    project = _tree(tmp_path, main_py=GUARD, pkg__a_py="", pkg__data_json="{}", notes_txt="x")
    (project / ".env").write_text("TOKEN=1")
    (project / "server.key").write_text("-----BEGIN PRIVATE KEY-----")
    (project / "pkg" / "cert.pem").write_text("x")

    package = Package.load(project)

    assert list(package.sources) == ["main.py", "pkg/a.py"]


def test_hidden_build_and_dependency_directories_are_skipped(tmp_path: Path) -> None:
    project = _tree(tmp_path, HEAD, {"__pycache__/x.py": "", "site_packages/s.py": ""},
                    main_py=GUARD, build__b_py="", dist__d_py="",
                    node_modules__n_py="")  # fmt: skip
    for hidden in (".git", ".venv", "venv", "lib/python3.12/site-packages", ".tox"):
        (project / hidden).mkdir(parents=True)
        (project / hidden / "bad.py").write_text("")
    (project / "venv" / "pyvenv.cfg").write_text("home = /usr/bin")

    assert list(Package.load(project).sources) == ["main.py", "site_packages/s.py"]


def test_a_python_file_below_a_directory_that_is_no_name_is_reported_not_packaged(
    tmp_path: Path,
) -> None:
    project = _tree(tmp_path, main_py=GUARD, **{"sub-dir__c_py": "", "ok__d_py": ""})

    package = Package.load(project)

    assert list(package.sources) == ["main.py", "ok/d.py"]
    assert len(package.warnings) == 1 and package.warnings[0].startswith("sub-dir/c.py is not")


def test_a_link_to_a_file_outside_the_tree_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 1")
    project = _tree(tmp_path / "project", main_py=GUARD)
    (project / "leak.py").symlink_to(outside)

    with pytest.raises(ProjectError, match=r"leak\.py is a link to .*outside\.py"):
        Package.load(project)


def test_a_link_to_a_file_inside_is_packaged_and_a_link_to_a_directory_is_not_followed(
    tmp_path: Path,
) -> None:
    other = tmp_path / "other"
    _tree(other, lib_py="x = 1")
    project = _tree(tmp_path / "project", main_py=GUARD, real__m_py="y = 2")
    (project / "alias.py").symlink_to("real/m.py")
    (project / "dirlink").symlink_to(other)

    assert list(Package.load(project).sources) == ["alias.py", "main.py", "real/m.py"]


@pytest.mark.parametrize("which", ["root", "home"])
def test_the_root_and_the_home_directory_are_no_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _tree(tmp_path, main_py=GUARD)

    with pytest.raises(ProjectError, match="not a project directory"):
        Package.load(Path("/") if which == "root" else tmp_path)


def test_a_directory_with_no_python_file_has_nothing_to_package(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="no Python files"):
        Package.load(_tree(tmp_path))


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_in_a_git_work_tree_the_files_are_the_tracked_ones_and_untracked_ones_are_reported(
    tmp_path: Path,
) -> None:
    project = _tree(tmp_path, main_py=GUARD, tracked_py="", new_py="", secrets_py="TOKEN = 1",
                    gone_py="")  # fmt: skip
    (project / ".gitignore").write_text("secrets.py\n")
    git = ["git", "-C", str(project)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "main.py", "tracked.py", "gone.py"], check=True)
    (project / "gone.py").unlink()

    package = Package.load(project)

    assert list(package.sources) == ["main.py", "tracked.py"]
    assert [w.split(" is not packaged")[0] for w in package.warnings] == ["new.py"]
    assert "git does not track it" in package.warnings[0]


# --- the wheel -------------------------------------------------------------------------------


def _entries(wheel: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(wheel) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_the_wheel_holds_the_files_under_the_scripts_root_with_a_valid_record(
    tmp_path: Path,
) -> None:
    project = _tree(tmp_path / "p", HEAD, main_py=GUARD, pkg__m_py="x = 1")

    wheel = Package.load(project).write_wheel(tmp_path / "w", 1_700_000_000)

    assert wheel.name == "my_tool-1.2-py3-none-any.whl"
    entries = _entries(wheel)
    assert list(entries) == sorted(entries)
    assert [n for n in entries if not n.endswith(".dist-info/RECORD")][:2] == [
        "_fsbpyr_scripts/main.py", "_fsbpyr_scripts/pkg/m.py"]  # fmt: skip
    rows = csv.reader(io.StringIO(entries["my_tool-1.2.dist-info/RECORD"].decode()))
    record = {row[0]: row[1:] for row in rows}
    assert set(record) == set(entries)
    for name, data in entries.items():
        if not name.endswith("/RECORD"):
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
            assert record[name] == [f"sha256={digest.decode()}", str(len(data))]
    assert entries["my_tool-1.2.dist-info/WHEEL"] == (
        b"Wheel-Version: 1.0\nGenerator: fsbpyr\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
    with zipfile.ZipFile(wheel) as archive:
        assert {info.date_time for info in archive.infolist()} == {(2023, 11, 14, 22, 13, 20)}


def test_two_writes_are_the_same_bytes(tmp_path: Path) -> None:
    project = _tree(tmp_path / "p", HEAD, main_py=GUARD, b_py="y = 2", a_py="x = 1")

    first = Package.load(project).write_wheel(tmp_path / "w1", 1_700_000_000)
    second = Package.load(project).write_wheel(tmp_path / "w2", 1_700_000_000)

    assert first.read_bytes() == second.read_bytes()


def test_the_metadata_comes_from_the_project_table(tmp_path: Path) -> None:
    pyproject = HEAD + (
        'dependencies = ["requests>=2", "tomli; python_version < \'3.11\'"]\n'
        '[project.optional-dependencies]\n'
        'Fancy_X = ["rich", "colorama; sys_platform == \'win32\'"]\n'
    )
    project = _tree(tmp_path / "p", pyproject, main_py=GUARD)

    wheel = Package.load(project).write_wheel(tmp_path / "w", EPOCH_FLOOR)

    metadata = _entries(wheel)["my_tool-1.2.dist-info/METADATA"].decode().splitlines()
    assert metadata == [
        "Metadata-Version: 2.1", "Name: My_Tool", "Version: 1.2", "Requires-Python: >=3.12",
        "Requires-Dist: requests>=2", 'Requires-Dist: tomli; python_version < "3.11"',
        "Provides-Extra: fancy-x", 'Requires-Dist: rich; extra == "fancy-x"',
        'Requires-Dist: colorama; sys_platform == "win32" and extra == "fancy-x"',
    ]  # fmt: skip


def test_a_bad_dependency_or_version_names_what_is_wrong(tmp_path: Path) -> None:
    bad_dependency = HEAD + 'dependencies = ["not a requirement!"]\n'
    with pytest.raises(ProjectError, match="invalid requirement"):
        Package.load(_tree(tmp_path / "a", bad_dependency, main_py=GUARD))
    with pytest.raises(ProjectError, match="not a valid version"):
        Package.load(_tree(tmp_path / "b", HEAD.replace('"1.2"', '"one"'), main_py=GUARD))


def test_the_staging_step_stamps_the_epoch_of_the_plan_and_never_before_1980(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _tree(tmp_path / "p", HEAD, main_py=GUARD)
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)

    wheel = _write_loose_wheel(project, tmp_path / "w", {"SOURCE_DATE_EPOCH": "0"}, lambda _: None)

    with zipfile.ZipFile(wheel) as archive:
        assert {info.date_time for info in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}
    with pytest.raises(StagingError, match="not a number"):
        _write_loose_wheel(project, tmp_path / "w", {"SOURCE_DATE_EPOCH": "soon"}, print)


# --- the commands ----------------------------------------------------------------------------


def test_project_scripts_move_below_the_scripts_root_unless_they_are_a_dependencys(
    tmp_path: Path,
) -> None:
    pyproject = HEAD + '[project.scripts]\none = "main:run"\ntwo = "sub.tool:go [x]"\n' \
        'three = "dep.cli:main"\n'  # fmt: skip
    project = _tree(tmp_path, pyproject, main_py="def run(): ...", sub__tool_py="def go(): ...")

    assert Package.load(project).commands == {
        "one": "_fsbpyr_scripts.main:run", "two": "_fsbpyr_scripts.sub.tool:go",
        "three": "dep.cli:main"}  # fmt: skip


def test_one_guarded_module_is_the_command_of_the_project(tmp_path: Path) -> None:
    project = _tree(tmp_path, HEAD, tool_py=GUARD, lib_py="x = 1")

    assert Package.load(project).commands == {"my-tool": "_fsbpyr_scripts.tool"}


def test_several_guarded_modules_are_named_after_their_files_and_main_after_the_project(
    tmp_path: Path,
) -> None:
    project = _tree(tmp_path, HEAD, main_py=GUARD, backup_py=GUARD, sub__restart_py=GUARD,
                    lib_py="x = 1")  # fmt: skip

    assert Package.load(project).commands == {
        "my-tool": "_fsbpyr_scripts.main", "backup": "_fsbpyr_scripts.backup",
        "restart": "_fsbpyr_scripts.sub.restart"}  # fmt: skip


def test_a_main_module_without_a_guard_is_a_command_and_a_helper_without_one_is_not(
    tmp_path: Path,
) -> None:
    project = _tree(tmp_path, HEAD, {"__main__.py": "print(1)"}, helper_py="x = 1")

    assert Package.load(project).commands == {"my-tool": "_fsbpyr_scripts.__main__"}


def test_test_files_and_nested_main_modules_are_no_commands(tmp_path: Path) -> None:
    project = _tree(tmp_path, HEAD, main_py=GUARD, tests__test_a_py=GUARD, conftest_py=GUARD,
                    check_test_py=GUARD, pkg____main___py=GUARD)  # fmt: skip

    assert Package.load(project).commands == {"my-tool": "_fsbpyr_scripts.main"}


def test_a_script_target_in_src_names_the_path_it_needs(tmp_path: Path) -> None:
    pyproject = HEAD + "[project.scripts]\nrun = 'pkg.mod:main'\n"
    project = _tree(tmp_path, pyproject, src__pkg____init___py="",
                    src__pkg__mod_py="def main(): ...")  # fmt: skip

    with pytest.raises(ProjectError, match=r"_fsbpyr_scripts\.src\.pkg\.mod:main"):
        Package.load(project)


def test_two_files_with_one_command_name_fail_naming_both(tmp_path: Path) -> None:
    project = _tree(tmp_path, HEAD.replace("My_Tool", "backup"), main_py=GUARD,
                    backup_py=GUARD, other_py=GUARD)  # fmt: skip

    with pytest.raises(ProjectError,
                       match=r"backup\.py and main\.py are both the command 'backup'"):
        Package.load(project)


def test_no_script_and_no_guard_leave_no_command_and_the_build_says_so_before_uv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _tree(tmp_path, HEAD, lib_py="x = 1")
    monkeypatch.setattr("fsbpyr.build.find_uv", lambda: pytest.fail("uv"))

    assert Package.load(project).commands == {}
    with pytest.raises(ProjectError, match=r"no \[project.scripts\] and no Python file with"):
        build(project, BuildConfig())


# --- the import check ------------------------------------------------------------------------


def _check(
    files: dict[str, str], target: str = "_fsbpyr_scripts.main", known: str = "os"
) -> list[str]:
    sources = {path: text.encode() for path, text in files.items()}
    return [p.describe() for p in check_command(target, sources, known.split())]


def test_a_required_import_nothing_resolves_is_reported_with_file_and_line() -> None:
    files = {"main.py": "import os\nimport requests\nfrom yaml import safe_load\n"}

    assert _check(files) == ["main.py:2: import requests", "main.py:3: import yaml"]


def test_the_script_directory_comes_first_then_the_distributions_and_the_runtime() -> None:
    files = {"main.py": "import helper, pkg.mod, os\nfrom pkg import mod2\n", "helper.py": "",
             "pkg/__init__.py": "", "pkg/mod.py": "", "pkg/mod2.py": ""}  # fmt: skip

    assert _check(files) == []


def test_a_sibling_of_the_root_is_not_found_from_a_script_in_a_subdirectory() -> None:
    files = {"tools/run.py": "import utils\n", "utils.py": ""}

    assert _check(files, "_fsbpyr_scripts.tools.run") == ["tools/run.py:1: import utils"]


def test_the_closure_follows_the_projects_own_modules() -> None:
    files = {"main.py": "import a\n", "a.py": "from b import x\n", "b.py": "import nothing_here\n",
             "unused.py": "import also_missing\n"}  # fmt: skip

    assert _check(files) == ["b.py:1: import nothing_here"]


def test_relative_imports_resolve_against_the_file() -> None:
    files = {"main.py": "from .a import x\nfrom . import b\nfrom .gone import y\n",
             "a.py": "", "b.py": ""}  # fmt: skip

    assert _check(files) == ["main.py:3: import .gone"]


def test_optional_imports_are_not_required() -> None:
    source = (
        "from typing import TYPE_CHECKING\n"
        "try:\n    import ujson\nexcept ImportError:\n    import json_fallback\n"
        "try:\n    import a1\nexcept (OSError, ModuleNotFoundError):\n    pass\n"
        "try:\n    import a2\nexcept:\n    pass\n"
        "try:\n    import a3\nexcept Exception:\n    pass\n"
        "if TYPE_CHECKING:\n    import only_for_types\nelse:\n    import required_else\n"
        "try:\n    import a4\nexcept KeyError:\n    pass\n"
    )

    assert _check({"main.py": source}, known="typing") == [
        "main.py:21: import required_else", "main.py:23: import a4"]  # fmt: skip


def test_imports_under_platform_and_version_tests_are_optional() -> None:
    source = (
        "import sys, os\nif sys.platform == 'win32':\n    import msvcrt\n"
        "if sys.version_info < (3, 11):\n    import tomli\nelse:\n    import tomllib\n"
        "if os.name == 'nt':\n    import winreg\n"
    )

    assert _check({"main.py": source}, known="sys os") == []


def test_a_file_that_does_not_parse_is_reported() -> None:
    result = _check({"main.py": "def (:\n"})

    assert len(result) == 1 and result[0].startswith("main.py:1: cannot be parsed")


def test_a_dependencys_script_has_nothing_of_the_projects_to_follow() -> None:
    assert _check({"main.py": "import missing\n"}, "_fsbpyr_scripts.dep.cli:main") == []


def test_a_package_entry_follows_its_main_module() -> None:
    files = {"app/__init__.py": "", "app/__main__.py": "import missing\n"}

    assert _check(files, "_fsbpyr_scripts.app") == ["app/__main__.py:1: import missing"]


def test_the_top_level_names_of_a_site_are_its_modules_packages_and_extensions(
    tmp_path: Path,
) -> None:
    for name in ("six.py", "_ext.cpython-312-x86_64-linux-gnu.so", "pkg/__init__.py",
                 "six-1.0.dist-info/METADATA", "__pycache__/x.pyc"):  # fmt: skip
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text("")

    assert top_level_names(tmp_path) == {"six", "_ext", "pkg"}


def _package(tmp_path: Path, **files: str) -> tuple[Package, Project]:
    project = _tree(tmp_path, HEAD.replace("My_Tool", "tool"), **files)
    return Package.load(project), Project.load(project)


def _site(tmp_path: Path) -> Path:
    (tmp_path / "site").mkdir()
    return tmp_path / "site"


class _Runtime:
    python_version = "3.14.9"
    modules = ()


def test_a_command_with_an_unresolved_import_is_skipped_with_a_warning_and_the_rest_stay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, project = _package(tmp_path, a_py="import requests\n" + GUARD, b_py=GUARD)
    monkeypatch.setattr("fsbpyr.build.runtime_module_names", lambda _: frozenset())
    warnings: list[str] = []

    skipped = check_script_imports(package, project, BuildConfig(), _site(tmp_path), _Runtime(),  # type: ignore[arg-type]
                                   warnings=warnings)  # fmt: skip

    assert skipped == {"a"}
    assert len(warnings) == 1
    assert warnings[0].startswith(
        "command a skipped: not in the executable: a.py:1: import requests")
    assert "declare it in [project] dependencies" in warnings[0]


def test_strict_fails_on_the_skipped_command_and_no_command_left_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, project = _package(tmp_path, a_py="import requests\n" + GUARD, b_py=GUARD)
    only, _ = _package(tmp_path / "only", main_py="import requests\n" + GUARD)
    monkeypatch.setattr("fsbpyr.build.runtime_module_names", lambda _: frozenset())
    site = _site(tmp_path)

    with pytest.raises(StrictError, match=r"a\.py:1: import requests"):
        check_script_imports(package, project, BuildConfig(strict=True), site,
                             _Runtime(), warnings=[])  # type: ignore[arg-type]
    with pytest.raises(BuildError, match="no command can run"):
        check_script_imports(only, project, BuildConfig(), site, _Runtime(), warnings=[])  # type: ignore[arg-type]


def test_an_entry_checks_only_the_command_it_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, project = _package(tmp_path, a_py="import requests\n" + GUARD, b_py=GUARD)
    monkeypatch.setattr("fsbpyr.build.runtime_module_names", lambda _: frozenset())

    skipped = check_script_imports(package, project, BuildConfig(entry="b"), _site(tmp_path),
                                   _Runtime(), warnings=[])  # type: ignore[arg-type]  # fmt: skip

    assert skipped == frozenset()


@pytest.mark.parametrize(
    ("module", "version", "hint"),
    [
        ("cgi", "3.14.9", "cgi was removed in Python 3.13; build with --python 3.12"),
        ("imp", "3.13.1", "imp was removed in Python 3.12; build with --python 3.11"),
        ("distutils.core", "3.12.4", "distutils was removed in Python 3.12; build with --python 3.11"),  # noqa: E501
    ],
)  # fmt: skip
def test_a_removed_standard_library_module_names_the_version_that_dropped_it(
    tmp_path: Path, module: str, version: str, hint: str
) -> None:
    _, project = _package(tmp_path, main_py=GUARD)
    from fsbpyr.importcheck import Unresolved  # noqa: PLC0415

    major, minor = (int(part) for part in version.split(".")[:2])
    text = _unresolved_warning("tool", [Unresolved("main.py", 3, module)], project, (major, minor))

    assert hint in text
    assert text.startswith(
        f"command tool skipped: not in the executable: main.py:3: import {module}")


def test_a_venv_in_the_project_gets_the_freeze_hint_and_is_never_read(tmp_path: Path) -> None:
    from fsbpyr.importcheck import Unresolved  # noqa: PLC0415

    _, project = _package(tmp_path, main_py=GUARD)
    (tmp_path / ".venv").mkdir()

    text = _unresolved_warning("tool", [Unresolved("main.py", 1, "yaml")], project, (3, 14))

    assert "uv pip freeze --python .venv/bin/python > requirements.txt" in text


@pytest.mark.parametrize("source", [LockSource.UV_LOCK, LockSource.UNLOCKED, LockSource.PYLOCK])
def test_a_project_without_a_build_system_plans_no_uv_build(
    tmp_path: Path, source: LockSource
) -> None:
    project = Project.load(_tree(tmp_path, HEAD, {"uv.lock": "", "pylock.toml": ""}, main_py=GUARD))

    plan = staging_plan(project, source, Path("/u/uv"), tmp_path / "work", StagingOptions("3.14"))

    assert plan.build == []
    assert any("{wheel}" in part for command in plan.install for part in command)


def test_a_removed_module_and_an_undeclared_one_get_both_hints(tmp_path: Path) -> None:
    _, project = _package(tmp_path, main_py=GUARD)
    (project.directory / ".venv").mkdir()
    from fsbpyr.importcheck import Unresolved  # noqa: PLC0415

    text = _unresolved_warning(
        "tool", [Unresolved("main.py", 1, "cgi"), Unresolved("main.py", 2, "requests")],
        project, (3, 13))  # fmt: skip

    assert "build with --python 3.12" in text
    assert "uv pip freeze --python .venv/bin/python" in text
