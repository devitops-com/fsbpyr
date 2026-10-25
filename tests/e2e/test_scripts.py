"""Projects without a build system (fixture ``scripts``, the layout of ``uv init --no-package``):
fsbpyr writes the wheel of the project's Python files, and the app runs them as ``uv run
main.py`` does. Needs the cached runtime."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from fsbpyr.cli import main
from fsbpyr.runtime.registry import Runtime

from ..support import contain

pytestmark = pytest.mark.slow

FIXTURE = Path(__file__).parents[1] / "fixtures" / "projects" / "scripts"
FOREIGN = Path(__file__).parents[1] / "fixtures" / "projects" / "foreign"
PYPROJECT = '[project]\nname = "{name}"\nversion = "1.0"\nrequires-python = ">=3.12"\n{extra}'
GUARDED = 'import sys\n\nif __name__ == "__main__":\n    print("{who}", sys.argv)\n'


def _run(executable: Path, tmp: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp / "cache"), **env}
    return contain.run([executable, *args], env=env, timeout=120)


def _listing(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def _project(tmp_path: Path, name: str = "demo", extra: str = "", **files: str) -> Path:
    """An inline project without [build-system]; ``files`` maps a path (``__`` for ``/``,
    ``_py`` for ``.py``) to its text."""
    project = tmp_path / name
    project.mkdir()
    (project / "pyproject.toml").write_text(PYPROJECT.format(name=name, extra=extra))
    for key, text in files.items():
        path = project / key.replace("__", "/").replace("_py", ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return project


@pytest.fixture(scope="module")
def built(
    tmp_path_factory: pytest.TempPathFactory, runtime: Runtime
) -> tuple[Path, list[str], list[str]]:
    """The fixture built: the project, its tree before and after the build."""
    project = tmp_path_factory.mktemp("scripts") / "scripts"
    shutil.copytree(FIXTURE, project)
    before = _listing(project)
    assert main(["build", str(project), "-o", str(project.parent / "scripts-app")]) == 0
    return project, before, _listing(project)


def test_it_runs_like_uv_run_main_py(
    built: tuple[Path, list[str], list[str]], tmp_path: Path
) -> None:
    project = built[0]
    uv = shutil.which("uv")
    assert uv is not None  # the build needs it
    expected = subprocess.run(
        [uv, "run", "--offline", "--no-project", "main.py", "x", "y"], cwd=project,
        capture_output=True, text=True, check=False,
    )  # fmt: skip

    result = _run(project.parent / "scripts-app", tmp_path, "x", "y")

    assert expected.returncode == 0, expected.stderr
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == expected.stdout.splitlines()[0] == "hello X Y!"
    assert result.stdout.splitlines()[1] == "name __main__ args ['x', 'y']"
    assert expected.stdout.splitlines()[1] == result.stdout.splitlines()[1]


def test_nothing_is_written_into_the_source_tree(
    built: tuple[Path, list[str], list[str]],
) -> None:
    _, before, after = built

    assert after == before


def _build(project: Path, *args: str) -> int:
    return main(["build", str(project), "-o", str(project.parent / "out" / "app"), *args])


def test_two_builds_are_the_same_bytes(tmp_path: Path, runtime: Runtime) -> None:
    project = tmp_path / "scripts"
    shutil.copytree(FIXTURE, project)

    assert main(["build", str(project), "-o", str(tmp_path / "one")]) == 0
    assert main(["build", str(project), "-o", str(tmp_path / "two")]) == 0

    assert (tmp_path / "one").read_bytes() == (tmp_path / "two").read_bytes()


def test_a_single_module_with_project_scripts_behaves_as_it_did_with_the_legacy_build(
    tmp_path: Path, runtime: Runtime
) -> None:
    """Before, setuptools' fallback packaged ``tool.py`` as a top-level module; its output and
    exit status are what the project always had."""
    source = ("import sys\n\ndef run():\n    print('tool', sys.argv[1:])\n"
              "    return 3\n\nif __name__ == '__main__':\n    sys.exit(run())\n")
    project = _project(tmp_path, extra='[project.scripts]\nrunner = "tool:run"\n', tool_py=source)

    assert _build(project) == 0

    result = _run(tmp_path / "out" / "app", tmp_path, "a", "b")
    assert (result.returncode, result.stdout) == (3, "tool ['a', 'b']\n")


def test_a_script_whose_top_level_code_does_the_work_is_not_run_by_the_build(
    tmp_path: Path, runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "marker"
    monkeypatch.setenv("FSBPYR_TEST_MARKER", str(marker))
    source = ("import os\n\nopen(os.environ['FSBPYR_TEST_MARKER'], 'w').close()\n\n"
              "if __name__ == '__main__':\n    print('deployed')\n")
    project = _project(tmp_path, main_py=source)

    assert _build(project) == 0

    assert not marker.exists()  # the smoke test found the script, it did not import it
    result = _run(tmp_path / "out" / "app", tmp_path)
    assert result.stdout == "deployed\n" and marker.exists()


def test_several_guarded_modules_make_one_executable_with_a_command_per_link(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(
        tmp_path, name="ops", main_py=GUARDED.format(who="ops"),
        backup_py=GUARDED.format(who="backup"), tools__restart_py=GUARDED.format(who="restart"),
        lib_py="import backup\n", tools__helper_py="X = 1\n",
    )  # fmt: skip
    project.joinpath("tools", "restart.py").write_text(
        "import helper\n\nif __name__ == '__main__':\n    print('restart', helper.X)\n")

    assert _build(project) == 0

    out = tmp_path / "out"
    assert sorted(path.name for path in out.iterdir()) == ["app", "backup", "ops", "restart"]
    assert "commands: backup, ops, restart" in capsys.readouterr().out
    assert _run(out / "backup", tmp_path, "x").stdout == f"backup {[str(out / 'backup'), 'x']}\n"
    assert _run(out / "app", tmp_path, "restart").stdout == "restart 1\n"  # tools/ comes first
    assert _run(out / "ops", tmp_path).returncode == 0


def test_two_files_that_name_one_command_fail_the_build_naming_both(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path, name="backup", main_py=GUARDED.format(who="a"),
                       backup_py=GUARDED.format(who="b"))  # fmt: skip

    assert _build(project) == 1

    assert "backup.py and main.py are both the command 'backup'" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_an_import_the_executable_lacks_skips_that_command_with_file_and_line(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(
        tmp_path, name="ops", ok_py=GUARDED.format(who="ok"),
        bad_py="import sys\nimport requests\n" + GUARDED.format(who="bad"),
        soft_py="try:\n    import yaml\nexcept ImportError:\n    yaml = None\n"
        + GUARDED.format(who="soft"),
    )  # fmt: skip
    (project / ".venv").mkdir()

    assert _build(project) == 0

    err = capsys.readouterr().err
    assert "command bad skipped: not in the executable: bad.py:2: import requests" in err
    assert "uv pip freeze --python .venv/bin/python > requirements.txt" in err
    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == ["app", "ok", "soft"]


def test_strict_fails_on_a_skipped_command_and_a_build_with_no_command_left_fails(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path, name="ops", ok_py=GUARDED.format(who="ok"),
                       bad_py="import requests\n" + GUARDED.format(who="bad"))  # fmt: skip

    assert _build(project, "--strict") == 1
    assert "--strict" in capsys.readouterr().err and not (tmp_path / "out").exists()

    (project / "ok.py").unlink()
    assert _build(project) == 1
    assert "no command can run" in capsys.readouterr().err


def test_a_removed_standard_library_module_suggests_an_older_python(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    on_312 = runtime.python_version.startswith("3.12.")
    module, hint = (
        ("imp", "imp was removed in Python 3.12; no Python fsbpyr builds for has it")
        if on_312 else
        ("cgi", "cgi was removed in Python 3.13; build with --python 3.12")
    )  # fmt: skip
    project = _project(tmp_path, main_py=f"import {module}\n" + GUARDED.format(who="x"))

    assert _build(project) == 1

    err = capsys.readouterr().err
    assert f"main.py:1: import {module}" in err
    assert hint in err


def test_a_file_below_a_directory_that_is_no_python_name_is_reported(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path, main_py=GUARDED.format(who="x"), **{"sub-dir__c_py": "x = 1\n"})

    assert _build(project) == 0

    assert "sub-dir/c.py is not packaged" in capsys.readouterr().err


def test_a_file_next_to_a_script_is_read_beside_the_executable(
    tmp_path: Path, runtime: Runtime
) -> None:
    source = ("from pathlib import Path\n\nif __name__ == '__main__':\n"
              "    print(Path(__file__).with_name('data.json').read_text())\n")
    project = _project(tmp_path, main_py=source)
    (project / "data.json").write_text("{}")

    assert _build(project) == 0

    result = _run(tmp_path / "out" / "app", tmp_path)
    assert result.returncode == 1 and "FileNotFoundError" in result.stderr
    assert f"{tmp_path / 'out'}/data.json is missing" in result.stderr
    assert "--fs-package" not in result.stderr
    (tmp_path / "out" / "data.json").write_text('{"host": 1}')
    assert _run(tmp_path / "out" / "app", tmp_path).stdout == '{"host": 1}\n'


# --- Poetry and PDM locks (research UC3) -----------------------------------------------------

LOCKED = [("poetry2", "poetry.lock"), ("pdm", "pdm.lock")]
WHEEL_HASHES = {  # the wheels of the fixtures' locks
    "click": "255bc9599cf7748b4b1a446ccc735421bd08a2ae529a8b88597d3de5664ee360",
    "rich": "33bd4ef74232fb73fe9279a257718407f169c09b78a87ad3d296f548e27de0bb",
}


@pytest.mark.parametrize(("fixture", "lock"), LOCKED)
def test_a_poetry_or_pdm_lock_locks_the_build(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str], fixture: str, lock: str
) -> None:
    project = tmp_path / fixture
    shutil.copytree(FOREIGN / fixture, project)
    before = _listing(project)

    assert _build(project) == 0

    err = capsys.readouterr().err
    assert "unlocked" not in err and "out of date" not in err
    assert _listing(project) == before  # the converted file lives in the staging directory
    shown = _run(tmp_path / "out" / "app", tmp_path).stdout.splitlines()
    assert shown == ["click 8.5.0", "rich 15.0.0", "markdown-it-py 4.2.0", "mdurl 0.1.2",
                     "pygments 2.21.0", "colorama absent", "packaging absent",
                     "iniconfig absent"]  # fmt: skip

    assert main(["inspect", "--json", str(tmp_path / "out" / "app")]) == 0
    info = json.loads(capsys.readouterr().out)["build_info"]
    digest = hashlib.sha256((project / lock).read_bytes()).hexdigest()
    assert info["lock"] == {"kind": lock, "path": lock, "hashed": True, "sha256": digest}
    pins = {d["name"]: d for d in info["dists"]}
    assert {name: pins[name]["artifact_sha256"] for name in WHEEL_HASHES} == WHEEL_HASHES
    assert "colorama" not in pins and "iniconfig" not in pins
    assert main(["inspect", str(tmp_path / "out" / "app")]) == 0
    assert f"lock       {lock} sha256:{digest[:8]}" in capsys.readouterr().out


@pytest.mark.parametrize(("fixture", "lock"), LOCKED)
def test_an_extra_installs_what_the_lock_lists_for_it(
    tmp_path: Path, runtime: Runtime, fixture: str, lock: str
) -> None:
    project = tmp_path / fixture
    shutil.copytree(FOREIGN / fixture, project)

    assert _build(project, "--extra", "fancy") == 0

    assert "packaging 26.3" in _run(tmp_path / "out" / "app", tmp_path).stdout.splitlines()


@pytest.mark.parametrize(("fixture", "lock"), LOCKED)
def test_a_lock_older_than_the_dependencies_is_a_warning_and_an_error_with_strict(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str], fixture: str, lock: str
) -> None:
    project = tmp_path / fixture
    shutil.copytree(FOREIGN / fixture, project)
    pyproject = project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('"click>=8.1"', '"click>=8.2"'))

    assert _build(project) == 0
    assert f"{lock} is out of date" in capsys.readouterr().err

    assert _build(project, "--strict") == 1
    assert "--strict: the build has 1 warning" in capsys.readouterr().err


@pytest.mark.parametrize(("fixture", "lock"), LOCKED)
def test_a_changed_hash_in_the_lock_fails_the_build(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str], fixture: str, lock: str
) -> None:
    """The converted file demands the lock's hashes (uv --require-hashes)."""
    project = tmp_path / fixture
    shutil.copytree(FOREIGN / fixture, project)
    path = project / lock
    path.write_text(path.read_text().replace(WHEEL_HASHES["click"], "0" * 64))
    path.write_text(path.read_text().replace("ba0d2089de75ea0310e2dde03160e6ca10009947fb95a182f9b54"
                                             "021bb272e34", "1" * 64))  # fmt: skip

    assert _build(project) == 1

    assert "Hash mismatch" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()
