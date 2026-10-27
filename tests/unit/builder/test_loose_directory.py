"""Directories of loose scripts (no pyproject.toml, setup.py or setup.cfg): discovery, commands and
their names, files whose names are no Python names, and the dependencies the files declare."""

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from fsbpyr.build import check_script_imports, lock_warnings
from fsbpyr.config import BuildConfig
from fsbpyr.importcheck import check_command
from fsbpyr.links import deploy_recipe
from fsbpyr.loose import Package, directory_name, inline_metadata, python_shebang
from fsbpyr.project import LockSource, Project, ProjectError

from ...support import requires

GUARD = 'if __name__ == "__main__":\n    print("hi")\n'
SHEBANG = "#!/usr/bin/env python3\n"
git = requires.tools("git")


def _dir(root: Path, files: Mapping[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _block(*lines: str) -> str:
    return "# /// script\n" + "".join(f"# {line}\n" for line in lines) + "# ///\n"


# --- the trigger -----------------------------------------------------------------------------


def test_a_directory_of_scripts_is_a_loose_project_named_after_it(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "Ops_Scripts", {"check-disk.py": GUARD, "util.py": "X = 1\n"})

    project = Project.load(ops)

    assert (project.loose, project.bare, project.name) == (True, True, "ops-scripts")
    assert project.scripts == {"check-disk": "_fsbpyr_scripts.check_disk"}
    assert project.uv_root is None


def test_a_setup_cfg_alone_is_still_refused(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {"setup.cfg": "[metadata]\n", "a.py": GUARD})

    with pytest.raises(ProjectError, match=r"setup\.cfg"):
        Project.load(ops)


def test_a_pyproject_of_settings_only_is_the_settings_of_the_scripts_beside_it(
    tmp_path: Path,
) -> None:
    ops = _dir(tmp_path / "ops", {
        "a.py": GUARD, "b.py": GUARD, "util.py": "X = 1\n",
        "pyproject.toml": '[tool.fsbpyr]\nglibc = "2.28"\n\n[tool.ruff]\nline-length = 99\n',
    })  # fmt: skip

    project = Project.load(ops)

    assert (project.loose, project.bare, project.uv_root) == (True, True, None)
    assert project.scripts == {"a": "_fsbpyr_scripts.a", "b": "_fsbpyr_scripts.b"}
    assert project.tool == {"glibc": "2.28"}
    assert sorted(Package.load(ops).sources) == ["a.py", "b.py", "util.py"]


@pytest.mark.parametrize(
    "extra",
    [
        {"pyproject.toml": '[project]\nname = "x"\n'},
        {"pyproject.toml": '[build-system]\nrequires = []\n'},
        {"pyproject.toml": "[tool.poetry]\nname = 'x'\n"},
        {"pyproject.toml": "[tool.fsbpyr]\n", "setup.py": ""},
        {"pyproject.toml": "[tool.fsbpyr]\n", "setup.cfg": ""},
    ],
)  # fmt: skip
def test_a_pyproject_that_is_a_project_is_not_a_settings_file(
    tmp_path: Path, extra: Mapping[str, str]
) -> None:
    ops = _dir(tmp_path / "ops", {"a.py": GUARD, **extra})

    assert not Project.load(ops).bare


def test_a_tool_table_in_a_command_files_block_is_reported_as_ignored(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {
        "a.py": _block('[tool.fsbpyr]', 'glibc = "2.28"') + GUARD,
        "b.py": _block('[tool.ruff]', 'line-length = 99') + GUARD,
    })  # fmt: skip

    warnings = Package.load(ops).warnings

    assert len(warnings) == 1
    assert warnings[0].startswith("a.py: [tool.fsbpyr] in a '# /// script' block is ignored")


def test_a_uv_lock_beside_scripts_is_not_a_project_to_sync(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {"a.py": GUARD, "uv.lock": ""})

    assert Project.load(ops).lock_source() is LockSource.UNLOCKED


def test_the_home_directory_and_the_root_are_not_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dir(tmp_path, {"a.py": GUARD})
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(ProjectError, match="not a project directory"):
        Project.load(tmp_path)
    with pytest.raises(ProjectError, match="not a project directory"):
        Project.load(Path("/"))


def test_a_directory_without_python_files_has_nothing_to_package(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {"notes.txt": "x", "run.sh": "#!/bin/sh\n"})

    with pytest.raises(ProjectError, match="no Python files"):
        Project.load(ops)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("ops-scripts", "ops-scripts"), ("My Tools", "my-tools"), ("a..b", "a-b"), ("___", "scripts"),
     ("tmp.x7", "tmp-x7")],
)  # fmt: skip
def test_the_project_name_is_the_directory_name_made_valid(name: str, expected: str) -> None:
    assert directory_name(Path("/work") / name) == expected


# --- which files are Python ------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    ["#!/usr/bin/env python3", "#!/usr/bin/python3", "#!/usr/bin/env -S uv run --script",
     "#!/usr/bin/env python", "#!/usr/bin/python3.12 -u", "#!/usr/bin/env -S python3 -u",
     "#!/usr/bin/env -i PYTHONPATH=x python3", "#!/usr/bin/env uv run --script",
     "#!/usr/bin/env python3\r"],
)  # fmt: skip
def test_python_and_uv_run_shebangs_are_recognized(line: str) -> None:
    assert python_shebang(f"{line}\nprint(1)\n".encode())


@pytest.mark.parametrize(
    "data",
    [b"#!/bin/bash\n", b"#!/usr/bin/env python2\n", b"#!/usr/bin/python2.7\n",
     b"#!/usr/bin/env perl\n", b"#!/usr/bin/env pythonista\n", b"#!\n", b"#!/usr/bin/env\n",
     b"import sys\n", b"#!/usr/bin/env python3\0\x7fELF", b"", b"#!/usr/bin/env uv tool run x\n"],
)  # fmt: skip
def test_other_shebangs_and_binary_files_are_not_python(data: bytes) -> None:
    assert not python_shebang(data)


def test_only_python_files_are_packaged(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {
        "a.py": GUARD, "cleanup": SHEBANG + "print(1)\n", "bin/tool": SHEBANG + "print(2)\n",
        "shell": "#!/bin/bash\necho hi\n", "plain": "no shebang\n", "key": "-----BEGIN KEY-----\n",
        ".env": "SECRET=1\n", "id_rsa.pub": "ssh-rsa x\n", "data.json": "{}", "tool.sh": "x",
        "binary": "#!/usr/bin/env python3\0\x00\x01", ".hidden.py": "X = 1\n",
        "__pycache__/c.py": "X = 1\n", "node_modules/m.py": "X = 1\n", ".git/hook.py": "X = 1\n",
        "venv/pyvenv.cfg": "", "venv/lib/site.py": "X = 1\n", "build/b.py": "X = 1\n",
    })  # fmt: skip

    package = Package.load(ops)

    assert sorted(package.sources) == ["_x_cleanup.py", "a.py", "bin/_x_tool.py"]
    assert package.origins["bin/_x_tool.py"] == "bin/tool"


def test_a_file_is_not_read_to_find_out_it_is_hidden(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {"a.py": GUARD})
    fifo = ops / "pipe"
    os.mkfifo(fifo)  # opening it would block for ever

    assert sorted(Package.load(ops).sources) == ["a.py"]


def test_python_files_below_directories_that_are_no_python_names_are_stored_under_mangled_ones(
    tmp_path: Path,
) -> None:
    ops = _dir(tmp_path / "ops", {
        "a.py": GUARD, "sub-dir/c.py": "X = 1\n", "plugins-v2/check": SHEBANG + "print(1)\n",
        "2fa/d.py": "X = 1\n", "sub-dir/in-ner/e.py": GUARD,
    })  # fmt: skip

    package = Package.load(ops)

    assert sorted(package.sources) == ["_2fa/d.py", "a.py", "plugins_v2/_x_check.py",
                                       "sub_dir/c.py", "sub_dir/in_ner/e.py"]  # fmt: skip
    assert package.origins["sub_dir/in_ner/e.py"] == "sub-dir/in-ner/e.py"
    assert package.commands == {
        "a": "_fsbpyr_scripts.a", "check": "_fsbpyr_scripts.plugins_v2._x_check",
        "e": "_fsbpyr_scripts.sub_dir.in_ner.e"}  # fmt: skip
    assert package.warnings == ()


def test_two_directories_with_one_mangled_name_are_an_error(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {"a.py": GUARD, "sub-dir/c.py": "X = 1\n",
                                  "sub_dir/d/e.py": "X = 1\n"})  # fmt: skip

    with pytest.raises(ProjectError, match="the directories sub-dir and sub_dir are both sub_dir"):
        Package.load(ops)


def test_a_mangled_directory_and_a_module_file_with_one_payload_name_are_an_error(
    tmp_path: Path,
) -> None:
    ops = _dir(tmp_path / "ops", {"a.py": GUARD, "sub-dir/tool.py": "X = 1\n",
                                  "sub_dir.py": "X = 1\n"})  # fmt: skip

    with pytest.raises(ProjectError, match=r"directory sub-dir and sub_dir\.py are both sub_dir"):
        Package.load(ops)


@git
def test_git_decides_what_is_packaged_and_an_untracked_secret_is_not(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {
        "a.py": GUARD, "secrets.py": "KEY = 'x'\n", "local_settings.py": "PW = 'y'\n",
        "ignored.py": "Z = 1\n", "tool": SHEBANG + "print(1)\n", ".gitignore": "ignored.py\n",
        ".env": "K=1\n",
    })  # fmt: skip
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    git_exe = requires.tool("git")
    subprocess.run([git_exe, "init", "-q", str(ops)], check=True, env=env)
    subprocess.run([git_exe, "-C", str(ops), "add", "a.py"], check=True, env=env)

    package = Package.load(ops)

    # tracked files only: an untracked file, ignored or not, is left out; the ones that are
    # not ignored are named, so that nobody wonders where they went
    assert sorted(package.sources) == ["a.py"]
    named = sorted(w.split(" is not packaged")[0] for w in package.warnings)
    assert named == ["local_settings.py", "secrets.py", "tool"]


@git
def test_a_work_tree_with_nothing_tracked_packages_nothing_rather_than_everything(
    tmp_path: Path,
) -> None:
    ops = _dir(tmp_path / "ops", {"a.py": GUARD, "secrets.py": "KEY = 'x'\n"})
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run([requires.tool("git"), "init", "-q", str(ops)], check=True, env=env)

    with pytest.raises(ProjectError, match="no Python files"):
        Package.load(ops)


# --- links -----------------------------------------------------------------------------------


def test_a_link_to_a_file_outside_the_directory_is_never_packaged(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {"a.py": GUARD})
    (ops / "passwd").symlink_to("/etc/passwd")
    (ops / "hosts.txt").symlink_to("/etc/passwd")

    assert sorted(Package.load(ops).sources) == ["a.py"]  # extensionless: passed over, unread


def test_a_python_link_to_a_file_outside_the_directory_is_an_error(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {"a.py": GUARD})
    (ops / "evil.py").symlink_to("/etc/passwd")

    with pytest.raises(ProjectError, match=r"evil\.py is a link to /etc/passwd"):
        Package.load(ops)


def test_a_link_to_a_script_inside_the_directory_is_packaged(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {"real": SHEBANG + "print(1)\n"})
    (ops / "alias").symlink_to("real")

    assert sorted(Package.load(ops).sources) == ["_x_alias.py", "_x_real.py"]


def test_a_python_link_to_a_hidden_file_inside_the_directory_is_never_packaged(
    tmp_path: Path,
) -> None:
    ops = _dir(tmp_path / "ops", {"a.py": GUARD, ".env": "SECRET=1\n", ".git/config": "x\n"})
    (ops / "link.py").symlink_to(".env")
    (ops / "cfg.py").symlink_to(".git/config")

    assert sorted(Package.load(ops).sources) == ["a.py"]


def test_an_extensionless_script_is_not_importable_by_a_sibling(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {"queue": SHEBANG + "print(1)\n", "a.py": GUARD})

    assert sorted(Package.load(ops).sources) == ["_x_queue.py", "a.py"]


# --- commands --------------------------------------------------------------------------------


def test_files_with_a_main_guard_or_a_python_shebang_are_commands(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {
        "guarded.py": GUARD, "plain": SHEBANG + "print(1)\n", "helper.py": "X = 1\n",
        "lib/shebang.py": SHEBANG + "X = 1\n", "lib/inner.py": "X = 2\n",
    })  # fmt: skip

    package = Package.load(ops)

    assert package.commands == {
        "guarded": "_fsbpyr_scripts.guarded", "plain": "_fsbpyr_scripts._x_plain",
        "shebang": "_fsbpyr_scripts.lib.shebang",
    }  # fmt: skip
    assert "helper.py" in package.sources and "lib/inner.py" in package.sources


def test_test_files_are_packaged_and_never_commands(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {
        "a.py": GUARD, "conftest.py": GUARD, "test_a.py": GUARD, "a_test.py": GUARD,
        "tests/b.py": GUARD, "test/c.py": GUARD, "tests/sub/d": SHEBANG + "x = 1\n",
        "__init__.py": GUARD,
    })  # fmt: skip

    package = Package.load(ops)

    assert package.commands == {"a": "_fsbpyr_scripts.a"}
    assert {"test_a.py", "tests/b.py", "conftest.py", "tests/sub/_x_d.py"} <= set(package.sources)


def test_main_and_dunder_main_take_the_directorys_name(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops-tools", {"main.py": GUARD, "other.py": GUARD})
    dunder = _dir(tmp_path / "dunder", {"__main__.py": "print(1)\n"})

    assert Package.load(ops).commands == {
        "ops-tools": "_fsbpyr_scripts.main", "other": "_fsbpyr_scripts.other"}  # fmt: skip
    assert Package.load(dunder).commands == {"dunder": "_fsbpyr_scripts.__main__"}


@pytest.mark.parametrize(
    ("files", "first", "second"),
    [
        ({"a.py": GUARD, "sub/a.py": GUARD}, "a.py", "sub/a.py"),
        ({"tool": SHEBANG, "tool.py": GUARD}, "tool", "tool.py"),
        ({"main.py": GUARD, "__main__.py": GUARD}, "__main__.py", "main.py"),
        ({"ops.py": GUARD, "main.py": GUARD}, "main.py", "ops.py"),
        ({"check-disk.py": GUARD, "check_disk.py": GUARD}, "check-disk.py", "check_disk.py"),
        ({"check-disk": SHEBANG, "check_disk.py": GUARD}, "check-disk", "check_disk.py"),
    ],
)  # fmt: skip
def test_two_files_with_one_name_are_an_error_naming_both(
    tmp_path: Path, files: dict[str, str], first: str, second: str
) -> None:
    ops = _dir(tmp_path / "ops", files)

    with pytest.raises(ProjectError) as error:
        Package.load(ops)

    assert first in str(error.value) and second in str(error.value)


@pytest.mark.parametrize(
    ("file", "command", "payload"),
    [
        ("check-disk.py", "check-disk", "check_disk.py"),
        ("check-disk", "check-disk", "check_disk.py"),
        ("check_disk", "check_disk", "_x_check_disk.py"),
        ("2fa.py", "2fa", "_2fa.py"),
        ("sub/10-backup", "10-backup", "sub/_10_backup.py"),
        ("foo.bar.py", "foo.bar", "foo_bar.py"),
        ("ünï.py", "ünï", "ünï.py"),
    ],
)  # fmt: skip
def test_a_file_whose_name_is_no_python_name_is_stored_under_a_module_name_and_still_a_command(
    tmp_path: Path, file: str, command: str, payload: str
) -> None:
    source = GUARD if file.endswith(".py") else SHEBANG + "print(1)\n"
    package = Package.load(_dir(tmp_path / "ops", {file: source}))

    if command.isascii():
        assert package.commands == {command: "_fsbpyr_scripts." + payload[:-3].replace("/", ".")}
    assert payload in package.sources and package.origins[payload] == file


def test_a_command_name_that_is_not_one_is_a_warning_and_the_file_stays_a_module(
    tmp_path: Path,
) -> None:
    ops = _dir(tmp_path / "ops", {"a.py": GUARD, "_private.py": GUARD})

    package = Package.load(ops)

    assert package.commands == {"a": "_fsbpyr_scripts.a"}
    assert "_private.py" in package.sources
    assert package.warnings and "_private.py is not a command" in package.warnings[0]


def test_discovery_runs_no_user_code(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    body = f"open({str(marker)!r}, 'w').close()\n"
    ops = _dir(tmp_path / "ops", {"a.py": body + GUARD, "b": SHEBANG + body, "c.py": body})

    package = Project.load(ops), Package.load(ops)

    assert package[1].commands.keys() == {"a", "b"}
    assert not marker.exists()


# --- dependencies ----------------------------------------------------------------------------


def _requires(package: Package) -> list[str]:
    return [line for line in package.metadata if line.startswith("Requires-Dist")]


def test_the_pep_723_blocks_of_the_commands_give_the_union_of_their_dependencies(
    tmp_path: Path,
) -> None:
    ops = _dir(tmp_path / "ops", {
        "a.py": _block('dependencies = ["requests>=2", "rich"]') + GUARD,
        "b": SHEBANG + _block('dependencies = ["Rich", "click<9"]') + "print(1)\n",
        "helper.py": _block('dependencies = ["not-a-command-dep"]'),
    })  # fmt: skip

    package = Project.load(ops), Package.load(ops)

    assert _requires(package[1]) == [
        "Requires-Dist: requests>=2", "Requires-Dist: rich", "Requires-Dist: Rich",
        "Requires-Dist: click<9"]  # fmt: skip
    assert package[0].dependencies == package[1].dependencies and len(package[0].dependencies) == 4


def test_declared_dependencies_replace_the_blocks_but_not_their_python(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {
        "a.py": _block('requires-python = ">=3.13"', 'dependencies = ["rich"]') + GUARD,
    })  # fmt: skip

    package = Package.load(ops, declared=True)

    assert _requires(package) == []
    assert package.requires_python == ">=3.13" and "Requires-Python: >=3.13" in package.metadata


def test_the_requires_python_of_the_blocks_is_their_intersection(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {
        "a.py": _block('requires-python = ">=3.12"') + GUARD,
        "b.py": _block('requires-python = "<3.15"') + GUARD,
        "c.py": GUARD,
    })  # fmt: skip

    project = Project.load(ops)

    assert project.requires_python is not None
    project.check_python("3.13.2")
    with pytest.raises(ProjectError, match="excludes"):
        project.check_python("3.15.0")


def test_blocks_that_leave_no_python_are_an_error_naming_the_files(tmp_path: Path) -> None:
    ops = _dir(tmp_path / "ops", {
        "a.py": _block('requires-python = "<3.13"') + GUARD,
        "b.py": _block('requires-python = ">=3.14"') + GUARD,
    })  # fmt: skip

    with pytest.raises(ProjectError) as error:
        Project.load(ops)

    assert "a.py <3.13" in str(error.value) and "b.py >=3.14" in str(error.value)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (_block("dependencies = [") + GUARD, r"a\.py:1: invalid '# /// script' block"),
        (_block('dependencies = ["rich"]') + "x = 1\n" + _block("dependencies = []") + GUARD,
         r"a\.py has more than one"),
        (_block('dependencies = ["!!"]') + GUARD, r"a\.py: PEP 723 dependencies: invalid req"),
        (_block('dependencies = "rich"') + GUARD, r"must be a list"),
        (_block('requires-python = "nope"') + GUARD, r"a\.py: invalid requires-python"),
    ],
)  # fmt: skip
def test_a_broken_block_is_an_error_naming_the_file(
    tmp_path: Path, text: str, message: str
) -> None:
    with pytest.raises(ProjectError, match=message):
        Package.load(_dir(tmp_path / "ops", {"a.py": text}))


def test_other_blocks_are_not_dependencies() -> None:
    source = b"# /// tool\n# dependencies = ['x']\n# ///\nprint(1)\n"

    assert inline_metadata(source, "a.py") == {}


def test_a_directory_that_declares_no_dependency_is_not_an_unlocked_build(
    tmp_path: Path,
) -> None:
    plain = Project.load(_dir(tmp_path / "plain", {"a.py": GUARD}))
    needs = Project.load(_dir(tmp_path / "needs", {
        "a.py": _block('dependencies = ["rich"]') + GUARD}))  # fmt: skip

    assert lock_warnings(plain, LockSource.UNLOCKED, None) == []
    assert lock_warnings(needs, LockSource.UNLOCKED, None)[0].startswith("unlocked build")


# --- the recipe ------------------------------------------------------------------------------


def test_the_deploy_recipe_installs_the_binary_and_links_each_command() -> None:
    lines = deploy_recipe(Path("/w/ops/dist/ops-scripts"), ["check-disk", "ops-scripts", "x y"])

    assert 'install -m 755 /w/ops/dist/ops-scripts "$DEST"/ops-scripts' in "\n".join(lines)
    assert any(line.strip() == "for c in check-disk 'x y'; do "
               'ln -sf ops-scripts "$DEST/$c"; done' for line in lines)  # fmt: skip
    assert "python3 $DEST/x.py" in lines[-1] and "`$DEST/x`" in lines[-1]


def test_a_single_command_needs_no_links() -> None:
    lines = deploy_recipe(Path("/w/dist/backup"), [])

    assert not [line for line in lines if "ln -sf" in line]


# --- sys.path hacks in the import check ------------------------------------------------------

HACK = "import os, sys\n{hack}\nimport shared\n"


def _unresolved(files: Mapping[str, str]) -> list[str]:
    sources = {path: text.encode() for path, text in files.items()}
    known = frozenset({"os", "sys", "pathlib"})
    return [p.describe() for p in check_command("_fsbpyr_scripts.tools.probe", sources, known)]


@pytest.mark.parametrize(
    "hack",
    [
        "sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pylib'))",
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'pylib'))",
        "sys.path.append(os.path.dirname(os.path.realpath(__file__)) + '/pylib')",
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent / 'pylib'))",
        "sys.path.extend([os.path.join(os.path.dirname(__file__), 'pylib')])",
        "sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools', "
        "'pylib')))",
    ],
)  # fmt: skip
def test_a_directory_built_from_file_and_put_on_sys_path_is_searched(hack: str) -> None:
    files = {"tools/probe.py": HACK.format(hack=hack), "tools/pylib/shared.py": "X = 1\n"}

    assert _unresolved(files) == []


def test_a_module_missing_from_that_directory_is_still_reported() -> None:
    hack = "sys.path.append(os.path.join(os.path.dirname(__file__), 'pylib'))"
    files = {"tools/probe.py": HACK.format(hack=hack), "tools/pylib/other.py": "X = 1\n"}

    assert _unresolved(files) == ["tools/probe.py:3: import shared"]


def test_the_files_found_through_that_directory_are_followed() -> None:
    hack = "sys.path.append(os.path.join(os.path.dirname(__file__), 'pylib'))"
    files = {"tools/probe.py": HACK.format(hack=hack), "tools/pylib/shared.py": "import deeper\n",
             "tools/pylib/deeper.py": "import absent\n"}  # fmt: skip

    assert _unresolved(files) == ["tools/pylib/deeper.py:1: import absent"]


def test_a_path_with_dotdot_is_followed_as_the_path_hook_normalizes_it() -> None:
    hack = "sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'lib'))"
    files = {"tools/probe.py": HACK.format(hack=hack), "lib/shared.py": "X = 1\n"}

    assert _unresolved(files) == []
    assert _unresolved({**files, "lib/shared.py": "import absent\n"}) == [
        "lib/shared.py:1: import absent"]  # fmt: skip


def test_a_path_with_dotdot_above_the_scripts_root_holds_none_of_the_projects_files() -> None:
    hack = "sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'lib'))"
    files = {"tools/probe.py": HACK.format(hack=hack), "lib/shared.py": "X = 1\n"}

    assert _unresolved(files) == ["tools/probe.py:3: import shared"]


def test_a_directory_that_is_no_python_name_is_searched_under_its_payload_name() -> None:
    hack = "sys.path.append(os.path.join(os.path.dirname(__file__), 'sub-dir'))"
    files = {"tools/probe.py": HACK.format(hack=hack), "tools/sub_dir/shared.py": "X = 1\n"}

    assert _unresolved(files) == []


def test_a_sys_path_change_that_cannot_be_followed_makes_imports_optional() -> None:
    hack = "sys.path.append(os.environ['TOOLS'])"
    files = {"tools/probe.py": HACK.format(hack=hack)}

    assert _unresolved(files) == []


# --- the import check of a directory ---------------------------------------------------------


class _Runtime:
    python_version = "3.14.9"
    modules = ()


def test_an_unresolved_import_names_the_project_file_not_the_stored_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ops = _dir(tmp_path / "ops", {
        "check-disk.py": "import requests\nimport cgi\n" + GUARD, "ok.py": GUARD,
        "10-backup": SHEBANG + "import yaml\n"})  # fmt: skip
    (ops / ".venv").mkdir()
    monkeypatch.setattr("fsbpyr.build.runtime_module_names", lambda _: frozenset())
    (tmp_path / "site").mkdir()
    warnings: list[str] = []

    skipped = check_script_imports(Package.load(ops), Project.load(ops), BuildConfig(),
                                   tmp_path / "site", _Runtime(), warnings=warnings)  # type: ignore[arg-type]  # fmt: skip

    assert skipped == {"check-disk", "10-backup"}
    text = "\n".join(warnings)
    assert "check-disk.py:1: import requests" in text and "10-backup:2: import yaml" in text
    assert "cgi was removed in Python 3.13; build with --python 3.12" in text
    assert "uv pip freeze --python .venv/bin/python > requirements.txt" in text
    assert "check_disk.py" not in text
