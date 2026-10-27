"""A tool binary (research candidate 10): a directory with no Python file and a requirements file
builds one executable whose commands are the console scripts of the tools it names. The real
tools (pyflakes, pycodestyle, flake8: pure Python, pinned) come from the index through uv's
cache; the odd cases use local distributions. Needs the cached runtime."""

import io
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from fsbpyr.cli import main
from fsbpyr.runtime.registry import Runtime

from ..support import contain

pytestmark = pytest.mark.slow

UV = str(shutil.which("uv"))
PYFLAKES, PYCODESTYLE, FLAKE8 = "pyflakes==3.2.0", "pycodestyle==2.12.1", "flake8==7.1.1"
FREEZE = f"{FLAKE8}\nmccabe==0.7.0\n{PYCODESTYLE}\n{PYFLAKES}\n"
SAMPLE = "import os\n\n\ndef f( x ):\n    return x+1\n"
LIBRARY = (
    '[project]\nname = "{name}"\nversion = "1.0"\nrequires-python = ">=3.12"\n{scripts}'
    '[build-system]\nrequires = ["uv_build>=0.12.17,<0.13.0"]\nbuild-backend = "uv_build"\n'
)


def _write(root: Path, files: Mapping[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _tool(root: Path, name: str, console: str = "", gui: str = "", requires: str = "") -> str:
    """A local distribution ``name`` whose console and gui scripts are the command names given
    (each prints its name and the dist's), requiring ``requires`` (a requirement); the
    requirement that names it."""
    module = name.replace("-", "_")

    def table(header: str, commands: str) -> str:
        lines = "".join(f'{c} = "{module}:main_{c.replace("-", "_")}"\n' for c in commands.split())
        return f"[project.{header}]\n{lines}" if lines else ""

    head = f'dependencies = ["{requires}"]\n' if requires else ""
    scripts = head + table("scripts", console) + table("gui-scripts", gui)
    body = "".join(f"def main_{c.replace('-', '_')}():\n    print({c!r}, {name!r})\n\n"
                   for c in (*console.split(), *gui.split()))  # fmt: skip
    _write(root, {"pyproject.toml": LIBRARY.format(name=name, scripts=scripts),
                  f"src/{module}/__init__.py": body or "X = 1\n"})  # fmt: skip
    return f"{name} @ {root.as_uri()}"


def _sample(directory: Path) -> Path:
    """The file the linters check, beside the tool directory (a Python file in it would make it
    a directory of scripts); the directory to run them in."""
    (directory.parent / "sample.py").write_text(SAMPLE)
    return directory.parent


def _run(executable: Path, tmp: Path, *args: str, cwd: Path | None = None
         ) -> subprocess.CompletedProcess[str]:  # fmt: skip
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp / "cache")}
    return contain.run([executable, *args], env=env, cwd=cwd, timeout=120)


def _reference(spec: str, command: str, *args: str, cwd: Path) -> str:
    """What the tool prints run by uv from the same requirement, in a throwaway environment."""
    result = subprocess.run(
        [UV, "tool", "run", "--python", sys.executable, "--from", spec, command, *args],
        cwd=cwd, capture_output=True, text=True, check=False)  # fmt: skip
    return result.stdout


def _build(directory: Path, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["build", str(directory), *args])
    return code, out.getvalue(), err.getvalue()


@pytest.fixture(scope="module")
def two(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> tuple[Path, str, str]:
    """A directory whose requirements.txt names two tools, built: it, stdout and stderr."""
    directory = _write(tmp_path_factory.mktemp("two") / "linters",
                       {"requirements.txt": f"{PYFLAKES}\n{PYCODESTYLE}\n"})
    _sample(directory)
    code, out, err = _build(directory)
    assert code == 0, err
    return directory, out, err


def test_two_tools_make_one_executable_with_a_link_per_command(
    two: tuple[Path, str, str]
) -> None:
    directory, out, err = two
    dist = directory / "dist"

    assert sorted(path.name for path in dist.iterdir()) == ["linters", "pycodestyle", "pyflakes"]
    assert not (dist / "linters").is_symlink()
    for command in ("pycodestyle", "pyflakes"):
        assert (dist / command).readlink() == Path("linters")
    assert "2 commands" in out and "commands: pycodestyle, pyflakes" in out
    assert "install -m 755" in out and "for c in pycodestyle pyflakes; do" in out
    assert "the tools are the 2 distributions requirements.txt names: pyflakes, pycodestyle" in out
    assert "commands: pyflakes (pyflakes 3.2.0); pycodestyle (pycodestyle 2.12.1)" in out
    assert "packaged although" not in err


def test_a_command_prints_what_the_tool_prints_under_uv(
    two: tuple[Path, str, str], tmp_path: Path
) -> None:
    directory, _, _ = two
    dist, work = directory / "dist", directory.parent

    for command, spec, args in [("pycodestyle", PYCODESTYLE, ("--version",)),
                                ("pycodestyle", PYCODESTYLE, ("--help",)),
                                ("pycodestyle", PYCODESTYLE, ("sample.py",)),
                                ("pyflakes", PYFLAKES, ("sample.py",))]:  # fmt: skip
        result = _run(dist / command, tmp_path, *args, cwd=work)
        expected = _reference(spec, command, *args, cwd=work)
        assert expected and result.stdout == expected, (command, args, result.stderr)


def test_the_binary_serves_a_command_by_its_first_argument(
    two: tuple[Path, str, str], tmp_path: Path
) -> None:
    directory, _, _ = two

    work = directory.parent

    result = _run(directory / "dist" / "linters", tmp_path, "pyflakes", "sample.py", cwd=work)

    assert result.stdout == _reference(PYFLAKES, "pyflakes", "sample.py", cwd=work)
    assert result.stdout.strip()


def test_the_executable_holds_the_tools_and_no_script_payload_of_its_own(
    two: tuple[Path, str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    directory, _, _ = two

    assert main(["inspect", str(directory / "dist" / "linters"), "--json", "--all-modules"]) == 0

    document = json.loads(capsys.readouterr().out)
    modules = [name for names in document["modules"].values() for name in names]
    assert not [name for name in modules if name.startswith("_fsbpyr_scripts")]
    assert {"pyflakes.api", "pycodestyle"} <= set(modules)
    assert document["app"]["name"] == "linters"
    assert document["commands"] == {"pycodestyle": "pycodestyle:_main",
                                    "pyflakes": "pyflakes.api:main"}  # fmt: skip


def test_the_licences_of_the_tools_are_collected_like_any_app_s(
    two: tuple[Path, str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    directory, _, _ = two

    assert main(["inspect", "--licenses", str(directory / "dist" / "linters")]) == 0

    headers = [h for h in capsys.readouterr().out.splitlines() if h.startswith("==== ")]
    assert "==== pyflakes 3.2.0: pyflakes-3.2.0.dist-info/LICENSE ====" in headers
    assert "==== pycodestyle 2.12.1: pycodestyle-2.12.1.dist-info/LICENSE ====" in headers
    assert "==== fsbpyr: _fsbpyr_licenses/LICENSE.cpython.txt ====" in headers


def test_a_freeze_of_a_tool_and_its_dependencies_has_the_commands_of_the_tool_alone(
    tmp_path: Path, runtime: Runtime
) -> None:
    directory = _write(tmp_path / "lint", {"requirements.txt": FREEZE})
    work = _sample(directory)

    code, out, err = _build(directory)

    assert code == 0, err
    assert "requirements.txt lists all 4 installed distributions, so the tools are the 1 that " \
           "no other listed distribution requires: flake8" in out
    assert "commands: flake8 (flake8 7.1.1)" in out
    dist = directory / "dist"
    assert [path.name for path in dist.iterdir()] == ["flake8"]  # one command: named after it
    result = _run(dist / "flake8", tmp_path, "sample.py", cwd=work)
    assert result.stdout == _reference(FLAKE8, "flake8", "sample.py", cwd=work)
    assert result.stdout.strip()
    assert _run(dist / "flake8", tmp_path, "--version", cwd=work).stdout.startswith("7.1.1 ")


def test_a_pylock_names_its_packages_and_a_directory_named_like_a_tool_builds(
    tmp_path: Path, runtime: Runtime
) -> None:
    directory = _write(tmp_path / "pyflakes", {"requirements.txt": f"{PYFLAKES}\n"})
    locked = subprocess.run(
        [UV, "pip", "compile", "--python", sys.executable, "--format", "pylock.toml",
         "-o", str(tmp_path / "pylock.toml"), str(directory / "requirements.txt")],
        capture_output=True, text=True, check=False)  # fmt: skip
    assert locked.returncode == 0, locked.stderr
    (directory / "requirements.txt").unlink()
    (directory / "pylock.toml").write_text((tmp_path / "pylock.toml").read_text())

    code, out, err = _build(directory)

    assert code == 0, err
    assert "the tools are the 1 distribution pylock.toml names: pyflakes" in out
    assert [path.name for path in (directory / "dist").iterdir()] == ["pyflakes"]


def test_a_directory_named_like_a_tool_keeps_the_tool_installed(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _write(tmp_path / "pycodestyle", {"requirements.txt": f"{PYCODESTYLE}\n"})
    code, _, err = _build(directory)
    assert code == 0, err

    result = _run(directory / "dist" / "pycodestyle", tmp_path, "--version")
    assert result.stdout == _reference(PYCODESTYLE, "pycodestyle", "--version", cwd=tmp_path)
    assert result.stdout == "2.12.1\n"
    assert main(["inspect", str(directory / "dist" / "pycodestyle"), "--json"]) == 0
    names = {d["name"] for d in json.loads(capsys.readouterr().out)["distributions"]}
    assert names == {"pycodestyle", "pycodestyle-tools"}


def test_the_requirements_option_names_the_file_of_a_directory_without_requirements_txt(
    tmp_path: Path, runtime: Runtime
) -> None:
    directory = _write(tmp_path / "ops", {"tools/lint.txt": f"{PYFLAKES}\n"})

    code, _, err = _build(directory, "--requirements", "tools/lint.txt")

    assert code == 0, err
    assert [path.name for path in (directory / "dist").iterdir()] == ["pyflakes"]


def test_entry_builds_one_of_the_commands_alone(tmp_path: Path, runtime: Runtime) -> None:
    directory = _write(tmp_path / "linters", {"requirements.txt": f"{PYFLAKES}\n{PYCODESTYLE}\n"})

    code, _, err = _build(directory, "--entry", "pycodestyle")

    assert code == 0, err
    assert [path.name for path in (directory / "dist").iterdir()] == ["pycodestyle"]
    assert _run(directory / "dist" / "pycodestyle", tmp_path, "--version").stdout == "2.12.1\n"


# --- the odd cases, with local distributions ---------------------------------------------------


def test_the_console_and_gui_scripts_of_a_tool_are_commands_not_its_dependencys(
    tmp_path: Path, runtime: Runtime
) -> None:
    dependency = _tool(tmp_path / "libs" / "dep", "dep-lib", console="dep-run")
    tool = _tool(tmp_path / "libs" / "app", "app-tool", console="app-run app-two", gui="app-gui",
                 requires=dependency)  # fmt: skip
    directory = _write(tmp_path / "ops", {"requirements.txt": f"{tool}\n"})

    code, out, err = _build(directory)

    assert code == 0, err
    assert "commands: app-gui, app-run, app-two (app-tool 1.0)" in out
    assert sorted(path.name for path in (directory / "dist").iterdir()) == [
        "app-gui", "app-run", "app-two", "ops"]  # fmt: skip
    assert _run(directory / "dist" / "app-two", tmp_path).stdout == "app-two app-tool\n"
    assert _run(directory / "dist" / "app-gui", tmp_path).stdout == "app-gui app-tool\n"
    assert _run(directory / "dist" / "ops", tmp_path, "app-run").stdout == "app-run app-tool\n"
    assert _run(directory / "dist" / "ops", tmp_path, "dep-run").returncode != 0


def test_a_meta_package_gives_the_commands_of_the_tools_it_requires(
    tmp_path: Path, runtime: Runtime
) -> None:
    dependency = _tool(tmp_path / "libs" / "dep", "dep-lib", console="dep-run")
    tool = _tool(tmp_path / "libs" / "core", "meta-pack-core", console="core-run", gui="core-gui",
                 requires=dependency)  # fmt: skip
    meta = _tool(tmp_path / "libs" / "meta", "meta-pack", requires=tool)
    directory = _write(tmp_path / "ops", {"requirements.txt": f"{meta}\n"})

    code, out, err = _build(directory)

    assert code == 0, err
    assert ("meta-pack has no console script of its own; "
            "the tools taken through it: meta-pack-core" in out)  # fmt: skip
    assert sorted(path.name for path in (directory / "dist").iterdir()) == [
        "core-gui", "core-run", "ops"]  # fmt: skip
    ran = _run(directory / "dist" / "ops", tmp_path, "core-run")
    assert ran.stdout == "core-run meta-pack-core\n"
    assert _run(directory / "dist" / "core-gui", tmp_path).stdout == "core-gui meta-pack-core\n"
    assert _run(directory / "dist" / "ops", tmp_path, "dep-run").returncode != 0


def test_a_tool_without_a_console_script_is_an_error_saying_what_the_file_named(
    tmp_path: Path, runtime: Runtime
) -> None:
    library = _tool(tmp_path / "libs" / "plain", "plain-lib")
    directory = _write(tmp_path / "ops", {"requirements.txt": f"{library}\n"})

    code, _, err = _build(directory)

    assert code == 1
    assert "requirements.txt names plain-lib, and none has a console or gui script" in err
    assert not (directory / "dist").exists()


def test_two_tools_with_one_script_name_are_an_error_naming_both(
    tmp_path: Path, runtime: Runtime
) -> None:
    one = _tool(tmp_path / "libs" / "one", "tool-one", console="run")
    two = _tool(tmp_path / "libs" / "two", "tool-two", console="run")
    directory = _write(tmp_path / "ops", {"requirements.txt": f"{one}\n{two}\n"})

    code, _, err = _build(directory)

    assert code == 1
    assert "tool-one and tool-two both have the script 'run'" in err
    assert not (directory / "dist").exists()


def test_a_venv_in_a_tool_directory_is_never_read_for_the_dependencies(
    tmp_path: Path, runtime: Runtime
) -> None:
    tool = _tool(tmp_path / "libs" / "app", "app-tool", console="app-run")
    site = ".venv/lib/python3.14/site-packages"
    directory = _write(tmp_path / "ops", {
        "requirements.txt": f"{tool}\n",
        ".venv/pyvenv.cfg": "",
        f"{site}/vanished-9.9.dist-info/METADATA": "Metadata-Version: 2.1\nName: vanished\n"
        "Version: 9.9\n",
        f"{site}/vanished-9.9.dist-info/RECORD": "vanished/__init__.py,,\n",
    })  # fmt: skip

    code, out, err = _build(directory)

    assert code == 0, err
    assert "were read as files" not in err and "vanished" not in err + out
    assert "commands: app-run (app-tool 1.0)" in out
    assert _run(directory / "dist" / "app-run", tmp_path).stdout == "app-run app-tool\n"


def test_a_directory_with_a_script_keeps_the_scripts_as_its_commands(
    tmp_path: Path, runtime: Runtime
) -> None:
    directory = _write(tmp_path / "ops", {
        "requirements.txt": f"{PYFLAKES}\n",
        "check.py": "import pyflakes\n\nif __name__ == '__main__':\n"
        "    print(pyflakes.__version__)\n",
    })  # fmt: skip

    code, out, err = _build(directory)

    assert code == 0, err
    assert "the tools are" not in out
    assert [path.name for path in (directory / "dist").iterdir()] == ["check"]
    assert _run(directory / "dist" / "check", tmp_path).stdout == "3.2.0\n"


def test_a_tool_binary_naming_a_helper_interpreter_library_says_so(
    tmp_path: Path, runtime: Runtime
) -> None:
    library = _tool(tmp_path / "libs" / "loky", "loky", console="loky-run")
    directory = _write(tmp_path / "ops", {"requirements.txt": f"{library}\n"})

    code, _, err = _build(directory)

    assert code == 0, err
    assert "may start helper interpreters" in err and "loky" in err
    assert "--interpreter-reexec" in err
