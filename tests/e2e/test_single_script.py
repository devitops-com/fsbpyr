"""One script built as an executable (research UC8): it prints what ``python3 <script>`` prints
with the same dependencies, its block's settings count, and uv writes nothing beside it. Needs the
cached runtime; the dependencies are local directories, so no network."""

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

UV_SHEBANG = "#!/usr/bin/env -S uv run --script\n"
LIBRARY = (
    '[project]\nname = "{name}"\nversion = "1.0"\nrequires-python = ">=3.12"\n'
    '[build-system]\nrequires = ["uv_build>=0.12.17,<0.13.0"]\nbuild-backend = "uv_build"\n'
)
UV = shutil.which("uv")


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
    return _write(root, {"pyproject.toml": LIBRARY.format(name=name),
                         f"src/{name}/__init__.py": f"WORD = {word!r}\n"})  # fmt: skip


def _block(*lines: str) -> str:
    return "# /// script\n" + "".join(f"# {line}\n" for line in lines) + "# ///\n"


def _listing(root: Path) -> list[tuple[str, int]]:
    return sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns) for p in root.rglob("*"))


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


def _build(script: Path, out: Path, *args: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(["build", str(script), "-o", str(out), *args])
    return code, stdout.getvalue(), stderr.getvalue()


SCRIPT = ("import sys\n\nimport helpers\n\nif __name__ == '__main__':\n"
          "    print('report', helpers.VALUE, sys.argv[1:], __name__)\n")


def test_a_script_with_a_block_and_a_sibling_prints_what_python_prints(
    tmp_path: Path, runtime: Runtime
) -> None:
    directory = _write(tmp_path / "ops", {
        "report.py": _block('requires-python = ">=3.12"', "dependencies = []") + SCRIPT,
        "helpers.py": "VALUE = 42\n",
        "other.py": "if __name__ == '__main__':\n    print('not packaged')\n",
    })  # fmt: skip
    before = _listing(directory)

    code, _, err = _build(directory / "report.py", tmp_path / "out" / "report")

    assert code == 0, err
    assert "unlocked" not in err
    assert _listing(directory) == before  # before python runs it and writes __pycache__
    result = _run(tmp_path / "out" / "report", tmp_path, "-x", "--y")
    assert result.returncode == 0, result.stderr
    assert result.stdout == _python(directory / "report.py", "-x", "--y")


def test_the_executable_defaults_to_dist_beside_the_script_named_after_the_file(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _write(tmp_path / "ops", {"check-disk.py": SCRIPT, "helpers.py": "VALUE = 1\n"})

    assert main(["build", str(directory / "check-disk.py")]) == 0

    assert (directory / "dist" / "check-disk").is_file()
    assert sorted(p.name for p in (directory / "dist").iterdir()) == ["check-disk"]
    result = _run(directory / "dist" / "check-disk", tmp_path)
    assert result.stdout == "report 1 [] __main__\n"


def test_the_payload_holds_the_closure_of_a_script_in_a_crowded_directory(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    files = {f"tool_{n}.py": "if __name__ == '__main__':\n    pass\n" for n in range(200)}
    files |= {"report.py": SCRIPT, "helpers.py": "VALUE = 7\n"}
    directory = _write(tmp_path / "ops", files)
    out = tmp_path / "out" / "report"

    assert main(["build", str(directory / "report.py"), "-o", str(out)]) == 0
    capsys.readouterr()

    assert main(["inspect", str(out), "--json", "--all-modules"]) == 0
    names = {name for dist in json.loads(capsys.readouterr().out)["modules"].values()
             for name in dist}  # fmt: skip
    scripts = {name for name in names if name.startswith("_fsbpyr_scripts")}
    assert scripts == {"_fsbpyr_scripts", "_fsbpyr_scripts.report", "_fsbpyr_scripts.helpers"}


def test_an_extensionless_uv_run_script_builds(tmp_path: Path, runtime: Runtime) -> None:
    directory = _write(tmp_path / "ops", {
        "report": UV_SHEBANG + _block('requires-python = ">=3.12"', "dependencies = []") + SCRIPT,
        "helpers.py": "VALUE = 3\n",
    })  # fmt: skip

    code, _, err = _build(directory / "report", tmp_path / "out" / "report")

    assert code == 0, err
    result = _run(tmp_path / "out" / "report", tmp_path, "a")
    assert result.stdout == _python(directory / "report", "a")


def test_the_dependencies_of_the_block_are_installed(
    tmp_path: Path, runtime: Runtime
) -> None:
    library = _library(tmp_path / "greetlib", "greet", "from greet")
    directory = _write(tmp_path / "ops", {
        "show.py": _block('requires-python = ">=3.12"',
                          f'dependencies = ["greet @ {library.as_uri()}"]')
        + "import greet\nprint(greet.WORD)\n"})  # fmt: skip

    before = _listing(directory)

    code, _, err = _build(directory / "show.py", tmp_path / "out" / "show")

    assert code == 0, err
    assert "unlocked build" in err
    assert _listing(directory) == before
    assert _run(tmp_path / "out" / "show", tmp_path).stdout == "from greet\n"


def test_unknown_keys_are_rejected_and_known_ones_take_effect(
    tmp_path: Path, runtime: Runtime
) -> None:
    library = _library(tmp_path / "greetlib", "greet", "from greet")
    dependencies = f'dependencies = ["greet @ {library.as_uri()}"]'
    _write(tmp_path / "ops", {
        "bad.py": _block(dependencies, "[tool.fsbpyr]", "no-such-key = true") + "print(1)\n",
        "strict.py": _block(dependencies, "[tool.fsbpyr]", "strict = true") + "print(1)\n",
    })  # fmt: skip

    bad = _build(tmp_path / "ops" / "bad.py", tmp_path / "bad")
    strict = _build(tmp_path / "ops" / "strict.py", tmp_path / "strict")

    assert bad[0] == 1 and "unknown [tool.fsbpyr] key 'no-such-key'" in bad[2]
    assert strict[0] == 1 and "--strict" in strict[2] and "unlocked build" in strict[2]
    assert not (tmp_path / "strict").exists()


def test_a_requires_python_no_supported_python_meets_is_an_error(
    tmp_path: Path, runtime: Runtime
) -> None:
    _write(tmp_path / "ops", {"a.py": _block('requires-python = "<3.12"') + "print(1)\n"})

    code, _, err = _build(tmp_path / "ops" / "a.py", tmp_path / "a")

    assert code == 1 and "requires-python '<3.12'" in err


def test_an_import_nothing_provides_skips_the_script_with_the_freeze_hint(
    tmp_path: Path, runtime: Runtime
) -> None:
    directory = _write(tmp_path / "ops", {"a.py": "import notinstalled\nprint(1)\n"})
    (directory / ".venv").mkdir()

    code, _, err = _build(directory / "a.py", tmp_path / "a")

    assert code == 1
    assert "a.py:1: import notinstalled" in err
    assert "uv pip freeze --python .venv/bin/python`" in err


needs_uv = pytest.mark.skipif(UV is None, reason="needs uv")


@needs_uv
def test_a_venv_beside_a_script_with_no_block_supplies_the_pins(
    tmp_path: Path, runtime: Runtime
) -> None:
    directory = _write(tmp_path / "ops", {
        "show.py": "from markdown_it import MarkdownIt\n\nif __name__ == '__main__':\n"
        "    print('show', MarkdownIt().render('*x*').strip())\n",
    })  # fmt: skip
    assert UV is not None
    subprocess.run([UV, "venv", "--quiet", "--python", sys.executable, str(directory / "venv")],
                   check=True)  # fmt: skip
    subprocess.run([UV, "pip", "install", "--offline", "--quiet", "--python",
                    str(directory / "venv" / "bin" / "python"), "markdown-it-py"],
                   check=True)  # fmt: skip

    code, _, err = _build(directory / "show.py", tmp_path / "out" / "show", "--offline")

    assert code == 0, err
    assert "the versions in venv were read as files" in err
    assert "`uv pip freeze --python venv/bin/python` lists them for the '# /// script' block" in err
    assert "requirements.txt" not in err
    result = _run(tmp_path / "out" / "show", tmp_path)
    assert (result.returncode, result.stdout) == (0, "show <p><em>x</em></p>\n"), result.stderr
    assert _build(directory / "show.py", tmp_path / "out" / "strict", "--offline",
                  "--strict")[0] == 1  # fmt: skip


@needs_uv
def test_a_script_lock_beside_the_script_locks_the_build_and_nothing_is_written(
    tmp_path: Path, runtime: Runtime
) -> None:
    library = _library(tmp_path / "greetlib", "greet", "from greet")
    directory = _write(tmp_path / "ops", {
        "show.py": _block('requires-python = ">=3.12"',
                          f'dependencies = ["greet @ {library.as_uri()}"]')
        + "import greet\nprint(greet.WORD)\n"})  # fmt: skip
    locked = subprocess.run([str(UV), "lock", "--script", "show.py", "--offline"], cwd=directory,
                            capture_output=True, text=True, check=False)  # fmt: skip
    assert locked.returncode == 0, locked.stderr
    before = _listing(directory)

    code, _, err = _build(directory / "show.py", tmp_path / "out" / "show")

    assert code == 0, err
    assert "unlocked" not in err
    assert _listing(directory) == before
    assert _run(tmp_path / "out" / "show", tmp_path).stdout == "from greet\n"
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert main(["inspect", str(tmp_path / "out" / "show"), "--json"]) == 0
    lock = json.loads(stdout.getvalue())["build_info"]["lock"]
    assert (lock["kind"], lock["path"], lock["hashed"]) == ("script.lock", "show.py.lock", True)


@needs_uv
def test_a_script_lock_the_block_has_outgrown_fails_the_build(
    tmp_path: Path, runtime: Runtime
) -> None:
    library = _library(tmp_path / "greetlib", "greet", "from greet")
    directory = _write(tmp_path / "ops", {
        "show.py": _block('requires-python = ">=3.12"', "dependencies = []") + "print(1)\n"})
    subprocess.run([str(UV), "lock", "--script", "show.py", "--offline"], cwd=directory,
                   capture_output=True, check=True)  # fmt: skip
    (directory / "show.py").write_text(
        _block('requires-python = ">=3.12"', f'dependencies = ["greet @ {library.as_uri()}"]')
        + "print(1)\n")  # fmt: skip
    before = _listing(directory)

    code, _, err = _build(directory / "show.py", tmp_path / "out" / "show")

    assert code == 1 and "needs to be updated" in err
    assert not (tmp_path / "out" / "show").exists()
    assert _listing(directory) == before
