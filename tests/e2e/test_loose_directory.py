"""Directories of loose scripts, built as one executable with a command per link (research UC6,
UC7): each command prints what ``python3 <script>`` prints with the same dependencies. Needs the
cached runtime; the dependencies are local directories, so no network."""

import io
import json
import os
import shutil
import stat
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

SHEBANG = "#!/usr/bin/env python3\n"
LIBRARY = (
    '[project]\nname = "{name}"\nversion = "1.0"\nrequires-python = ">=3.12"\n'
    '[build-system]\nrequires = ["uv_build>=0.12.17,<0.13.0"]\nbuild-backend = "uv_build"\n'
)


@pytest.fixture(autouse=True)
def _no_active_venv(monkeypatch: pytest.MonkeyPatch) -> None:
    """The venv rung reads $VIRTUAL_ENV too; the one these tests run in must not supply pins."""
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)


def _write(root: Path, files: Mapping[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _library(root: Path, name: str, word: str) -> Path:
    """A local distribution ``name`` whose module ``name`` holds ``WORD``."""
    return _write(root, {"pyproject.toml": LIBRARY.format(name=name),
                         f"src/{name}/__init__.py": f"WORD = {word!r}\n"})  # fmt: skip


def _run(executable: Path, tmp: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp / "cache")}
    return contain.run([executable, *args], env=env, timeout=120)


def _python(script: Path, *args: str, libraries: tuple[Path, ...] = ()) -> str:
    """What ``python3 script`` prints, in a throwaway environment holding ``libraries``."""
    command = ["uv", "run", "--offline", "--no-project", "--python", sys.executable]
    for library in libraries:
        command += ["--with", str(library)]
    result = subprocess.run([*command, "python", str(script), *args], cwd=script.parent,
                            capture_output=True, text=True, check=False)  # fmt: skip
    assert result.returncode == 0, result.stderr
    return result.stdout


def _build(directory: Path, *args: str) -> int:
    return main(["build", str(directory), *args])


OPS = {
    "check-disk.py": SHEBANG + "import sys\nimport calendar\nimport greet\nimport util\n\n"
    "if __name__ == '__main__':\n"
    "    print('check-disk', util.VALUE, greet.WORD, calendar.MARK, sys.argv[1:], __name__)\n",
    "check_mem.py": "import util\n\nif __name__ == '__main__':\n    print('mem', util.VALUE)\n",
    "report.py": "import sys\n\nif __name__ == '__main__':\n    print('report', sys.argv[1:])\n",
    "10-backup": SHEBANG + "print('backup', __name__)\n",
    "util.py": "VALUE = 42\n",
    "calendar.py": "MARK = 'the local calendar'\n",
    "requirements.txt": "../greetlib\n",
}
COMMANDS = ("check-disk", "check_mem", "report", "10-backup")


@pytest.fixture(scope="module")
def ops(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> tuple[Path, Path, str, str]:
    """The flat ops directory built: it, the library, the build's stdout and stderr."""
    root = tmp_path_factory.mktemp("ops-case")
    library = _library(root / "greetlib", "greet", "hello from greet")
    directory = _write(root / "ops-scripts", OPS)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        assert _build(directory) == 0
    return directory, library, out.getvalue(), err.getvalue()


def test_one_binary_and_a_link_per_command(ops: tuple[Path, Path, str, str]) -> None:
    directory, _, out, err = ops
    dist = directory / "dist"

    assert (dist / "ops-scripts").is_file() and not (dist / "ops-scripts").is_symlink()
    for command in COMMANDS:
        assert (dist / command).is_symlink() and (dist / command).readlink() == Path("ops-scripts")
    assert sorted(path.name for path in dist.iterdir()) == sorted(["ops-scripts", *COMMANDS])
    assert "packaged although" not in err and "skipped" not in err
    assert "install -m 755" in out and "ln -sf ops-scripts" in out and "python3 $DEST/x.py" in out
    assert f"for c in {' '.join(sorted(COMMANDS))}; do" in out


@pytest.mark.parametrize(
    ("command", "script", "args"),
    [
        ("check-disk", "check-disk.py", ("-w", "5")),
        ("check_mem", "check_mem.py", ()),
        ("report", "report.py", ("a", "--b")),
        ("10-backup", "10-backup", ()),
    ],
)  # fmt: skip
def test_each_command_prints_what_python_prints(
    ops: tuple[Path, Path, str, str], tmp_path: Path, command: str, script: str,
    args: tuple[str, ...],
) -> None:  # fmt: skip
    directory, library, _, _ = ops

    result = _run(directory / "dist" / command, tmp_path, *args)

    assert result.returncode == 0, result.stderr
    assert result.stdout == _python(directory / script, *args, libraries=(library,))


def test_a_file_with_no_python_name_has_its_payload_path_as_file(
    runtime: Runtime, tmp_path: Path
) -> None:
    other = _write(tmp_path / "filedir", {"show-file.py": "import os\n"
        "if __name__ == '__main__':\n    print(os.path.basename(__file__))\n"})  # fmt: skip
    assert _build(other) == 0

    result = _run(other / "dist" / "show-file", tmp_path)

    assert result.stdout.strip() == "show_file.py"
    assert _python(other / "show-file.py").strip() == "show-file.py"


def test_a_stdlib_named_sibling_shadows_the_standard_library_as_it_does_for_python(
    ops: tuple[Path, Path, str, str], tmp_path: Path
) -> None:
    directory, library, _, _ = ops

    result = _run(directory / "dist" / "check-disk", tmp_path)

    assert "the local calendar" in result.stdout
    assert "the local calendar" in _python(directory / "check-disk.py", libraries=(library,))


def test_the_binary_serves_a_command_by_its_first_argument(
    ops: tuple[Path, Path, str, str], tmp_path: Path
) -> None:
    result = _run(ops[0] / "dist" / "ops-scripts", tmp_path, "report", "x")

    assert result.stdout == "report ['x']\n"


def test_a_link_with_an_absolute_target_in_another_directory_runs_its_command(
    ops: tuple[Path, Path, str, str], tmp_path: Path
) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "check_mem").symlink_to((ops[0] / "dist" / "ops-scripts").resolve())

    result = _run(plugins / "check_mem", tmp_path)

    assert (result.returncode, result.stdout) == (0, "mem 42\n"), result.stderr


# --- imports, siblings and paths -------------------------------------------------------------


def test_a_subdirectory_tool_imports_a_sibling_and_the_pylib_hack_finds_its_modules(
    tmp_path: Path, runtime: Runtime
) -> None:
    directory = _write(tmp_path / "fleet", {
        "tools/probe.py": SHEBANG + "import os\nimport sys\n\nimport sibling\n\n"
        "sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pylib'))\n"
        "import shared\n\nif __name__ == '__main__':\n"
        "    print('probe', sibling.NAME, shared.NAME, __name__)\n",
        "tools/sibling.py": "NAME = 'sibling'\n",
        "tools/pylib/shared.py": "NAME = 'shared from pylib'\n",
        "top.py": "if __name__ == '__main__':\n    print('top')\n",
    })  # fmt: skip

    assert _build(directory) == 0

    result = _run(directory / "dist" / "probe", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "probe sibling shared from pylib __main__\n"
    assert result.stdout == _python(directory / "tools" / "probe.py")
    assert _run(directory / "dist" / "top", tmp_path).stdout == "top\n"


def test_an_undeclared_import_skips_its_command_and_an_optional_one_does_not(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _write(tmp_path / "ops", {
        "good.py": "if __name__ == '__main__':\n    print('good')\n",
        "needs.py": "import os\nimport yaml\n\nif __name__ == '__main__':\n    print(yaml)\n",
        "optional.py": "try:\n    import yaml\nexcept ImportError:\n    yaml = None\n\n"
        "if __name__ == '__main__':\n    print('optional', yaml)\n",
        ".venv/pyvenv.cfg": "",
    })  # fmt: skip

    assert _build(directory) == 0

    err = capsys.readouterr().err
    assert "command needs skipped: not in the executable: needs.py:2: import yaml" in err
    assert "uv pip freeze --python .venv/bin/python > requirements.txt" in err
    assert "optional skipped" not in err and "good skipped" not in err
    dist = directory / "dist"
    assert sorted(path.name for path in dist.iterdir()) == ["good", "ops", "optional"]
    assert _run(dist / "optional", tmp_path).stdout == "optional None\n"


def test_strict_fails_on_a_skipped_command_and_writes_nothing(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _write(tmp_path / "ops", {
        "good.py": "if __name__ == '__main__':\n    print('good')\n",
        "needs.py": "import yaml\nif __name__ == '__main__':\n    print(yaml)\n",
    })  # fmt: skip

    assert _build(directory, "--strict") == 1

    assert "needs.py:1: import yaml" in capsys.readouterr().err
    assert not (directory / "dist" / "ops").exists()


def test_no_user_code_runs_during_the_build(
    tmp_path: Path, runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "marker"
    monkeypatch.setenv("FSBPYR_TEST_MARKER", str(marker))
    directory = _write(tmp_path / "ops", {
        "a.py": "import os\nopen(os.environ['FSBPYR_TEST_MARKER'], 'w').close()\n"
        "if __name__ == '__main__':\n    print('a')\n",
        "b": SHEBANG + "import os\nopen(os.environ['FSBPYR_TEST_MARKER'], 'w').close()\n",
    })  # fmt: skip

    assert _build(directory) == 0

    assert not marker.exists()
    assert _run(directory / "dist" / "a", tmp_path).stdout == "a\n"
    assert marker.exists()  # the app does run them


# --- dependencies ----------------------------------------------------------------------------


def test_the_pep_723_blocks_of_the_commands_give_the_dependencies(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    one = _library(tmp_path / "libs" / "greet", "greet", "from greet")
    two = _library(tmp_path / "libs" / "shout", "shout", "from shout")
    block = ('# /// script\n# requires-python = ">=3.12"\n# dependencies = ["{name} @ {url}"]\n'
             '# ///\n')
    directory = _write(tmp_path / "ops", {
        "a.py": block.format(name="greet", url=one.as_uri()) + "import greet\n"
        "if __name__ == '__main__':\n    print(greet.WORD)\n",
        "b": SHEBANG + block.format(name="shout", url=two.as_uri()) + "import shout\n"
        "print(shout.WORD)\n",
    })  # fmt: skip

    assert _build(directory) == 0

    assert "unlocked build" in capsys.readouterr().err
    assert _run(directory / "dist" / "a", tmp_path).stdout == "from greet\n"
    assert _run(directory / "dist" / "b", tmp_path).stdout == "from shout\n"


# --- the venv rung ---------------------------------------------------------------------------

UV = shutil.which("uv")
SHOW = ("from markdown_it import MarkdownIt\n\nif __name__ == '__main__':\n"
        "    print('show', MarkdownIt().render('*x*').strip())\n")


def _venv(path: Path, *requirements: str) -> dict[str, str]:
    """A venv at ``path`` with ``requirements`` installed from uv's cache; the version of each
    installed distribution by canonical name."""
    assert UV is not None
    subprocess.run([UV, "venv", "--quiet", "--python", sys.executable, str(path)], check=True)
    subprocess.run([UV, "pip", "install", "--offline", "--quiet", "--python",
                    str(path / "bin" / "python"), *requirements], check=True)  # fmt: skip
    listed = subprocess.run([UV, "pip", "list", "--format", "json", "--python",
                             str(path / "bin" / "python")], check=True, capture_output=True,
                            text=True).stdout  # fmt: skip
    return {item["name"]: item["version"] for item in json.loads(listed)}


def _inspect(executable: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    capsys.readouterr()
    assert main(["inspect", "--json", str(executable)]) == 0
    report: dict[str, object] = json.loads(capsys.readouterr().out)
    return report


needs_uv = pytest.mark.skipif(UV is None, reason="needs uv")


@needs_uv
def test_a_venv_in_the_directory_pins_the_undeclared_imports_and_their_closure(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _write(tmp_path / "ops", {"show.py": SHOW})
    installed = _venv(directory / ".venv", "markdown-it-py")
    pyvenv = (directory / ".venv" / "pyvenv.cfg").read_text()
    before = sorted(p.relative_to(directory).as_posix() for p in directory.rglob("*"))

    assert _build(directory, "--offline") == 0

    err = capsys.readouterr().err
    assert "no dependency is declared: the versions in .venv were read as files" in err
    assert "uv pip freeze --python .venv/bin/python > requirements.txt` declares them" in err
    assert "unlocked build" not in err
    report = _inspect(directory / "dist" / "show", capsys)
    pins = [f"markdown-it-py=={installed['markdown-it-py']}", f"mdurl=={installed['mdurl']}"]
    assert report["build_info"]["lock"] == {  # type: ignore[index]
        "kind": "inferred", "path": ".venv", "sha256": None, "hashed": False, "pins": pins}
    names = {d["name"]: d["version"] for d in report["distributions"]}  # type: ignore[attr-defined]
    assert names["markdown-it-py"] == installed["markdown-it-py"]
    assert names["mdurl"] == installed["mdurl"]
    assert "pip" not in names  # the venv's own tools are not copied
    result = _run(directory / "dist" / "show", tmp_path)
    assert result.stdout == "show <p><em>x</em></p>\n" and result.returncode == 0, result.stderr
    assert (directory / ".venv" / "pyvenv.cfg").read_text() == pyvenv
    after = sorted(p.relative_to(directory).as_posix() for p in directory.rglob("*")
                   if "dist" not in p.relative_to(directory).parts)
    assert after == before


@needs_uv
def test_the_venv_python_is_never_started_and_nothing_of_it_is_copied(
    tmp_path: Path, runtime: Runtime
) -> None:
    marker = tmp_path / "marker"
    directory = _write(tmp_path / "ops", {"show.py": SHOW})
    _venv(directory / ".venv", "markdown-it-py")
    shim = f"#!/bin/sh\ntouch {marker}\nexit 1\n"
    for name in ("python", "python3", f"python{sys.version_info[0]}.{sys.version_info[1]}"):
        (directory / ".venv" / "bin" / name).unlink(missing_ok=True)
        (directory / ".venv" / "bin" / name).write_text(shim)
        (directory / ".venv" / "bin" / name).chmod(stat.S_IRWXU)

    assert _build(directory, "--offline") == 0

    assert not marker.exists()
    assert _run(directory / "dist" / "show", tmp_path).stdout == "show <p><em>x</em></p>\n"


@needs_uv
def test_an_import_the_venv_does_not_provide_stays_unresolved(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _write(tmp_path / "ops", {
        "show.py": SHOW,
        "needs.py": "import yaml\nif __name__ == '__main__':\n    print(yaml)\n",
    })  # fmt: skip
    _venv(directory / ".venv", "markdown-it-py")

    assert _build(directory, "--offline") == 0

    err = capsys.readouterr().err
    assert "command needs skipped: not in the executable: needs.py:1: import yaml" in err
    assert "were read as files" in err and "mdurl==" in err  # show's import was found
    assert sorted(p.name for p in (directory / "dist").iterdir()) == ["show"]


@needs_uv
def test_strict_fails_on_inferred_dependencies_and_writes_nothing(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _write(tmp_path / "ops", {"show.py": SHOW})
    _venv(directory / ".venv", "markdown-it-py")

    assert _build(directory, "--offline", "--strict") == 1

    assert "no dependency is declared: the versions in .venv were read" in capsys.readouterr().err
    assert not (directory / "dist").exists()


def test_a_pin_uv_cannot_install_fails_with_uvs_reason(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _write(tmp_path / "ops", {
        "tool.py": "import vanished\nif __name__ == '__main__':\n    print(vanished)\n",
        ".venv/pyvenv.cfg": "",
        ".venv/lib/python3.14/site-packages/vanished-9.9.dist-info/METADATA":
            "Metadata-Version: 2.1\nName: vanished\nVersion: 9.9\n",
        ".venv/lib/python3.14/site-packages/vanished-9.9.dist-info/RECORD":
            "vanished/__init__.py,,\n",
    })  # fmt: skip

    assert _build(directory, "--offline") == 1

    err = capsys.readouterr().err
    assert "installing the project's wheel and its dependencies: uv failed" in err
    assert "vanished==9.9" in err and not (directory / "dist").exists()


@needs_uv
def test_a_declared_source_leaves_the_venv_unread(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    library = _library(tmp_path / "greetlib", "greet", "hello")
    directory = _write(tmp_path / "ops", {
        "show.py": "import greet\nimport markdown_it\nif __name__ == '__main__':\n    print(1)\n",
        "ok.py": "import greet\nif __name__ == '__main__':\n    print(greet.WORD)\n",
        "requirements.txt": f"{library}\n",
    })  # fmt: skip
    _venv(directory / ".venv", "markdown-it-py")

    assert _build(directory, "--offline") == 0

    err = capsys.readouterr().err
    assert "were read as files" not in err and "command show skipped" in err


# --- processes and files ---------------------------------------------------------------------


@pytest.mark.parametrize("method", ["spawn", "forkserver"])
def test_multiprocessing_children_of_a_script_command_work(
    tmp_path: Path, runtime: Runtime, method: str
) -> None:
    directory = _write(tmp_path / "ops", {
        "pool.py": "import multiprocessing as mp\n\nimport helper\n\n"
        "if __name__ == '__main__':\n"
        f"    mp.set_start_method({method!r})\n"
        "    with mp.Pool(2) as pool:\n        print(pool.map(helper.square, [1, 2, 3]))\n",
        "helper.py": "def square(n):\n    return n * n\n",
    })  # fmt: skip

    assert _build(directory) == 0

    result = _run(directory / "dist" / "pool", tmp_path)
    assert (result.returncode, result.stdout) == (0, "[1, 4, 9]\n"), result.stderr


def test_a_file_read_next_to_a_script_is_reported_and_the_error_says_where_it_is(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _write(tmp_path / "ops", {
        "reader.py": "import os\n\ndef read():\n"
        "    path = os.path.join(os.path.dirname(__file__), 'x.conf')\n"
        "    return open(path).read()\n\nif __name__ == '__main__':\n    print(read())\n",
        "x.conf": "setting\n",
    })  # fmt: skip

    assert _build(directory) == 0

    err = capsys.readouterr().err
    assert "reader.py:5: reads a file next to its own __file__" in err
    result = _run(directory / "dist" / "reader", tmp_path)
    assert result.returncode == 1
    assert "NotADirectoryError" in result.stderr
    assert "holds the project's Python files only" in result.stderr
    assert "--fs-package" not in result.stderr
