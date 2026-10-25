"""`--glibc 2.28` (EL8, D46) and `[tool.fsbpyr] glibc` with the other ways to build: a directory of
loose scripts, a single script and a project of several commands. Each is built for EL8, run on
this host and inspected; test_portability.py runs EL8 apps in containers. Needs the cached
runtime for glibc 2.28 (the ``el8_runtime`` fixture)."""

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

BASELINE = "2.28"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "projects" / "multi"
GUARD = "import sys\n\nif __name__ == '__main__':\n    print('{name}', sys.argv[1:])\n"
OPS = {"alpha.py": GUARD.format(name="alpha"), "beta.py": GUARD.format(name="beta"),
       "util.py": "VALUE = 1\n"}  # fmt: skip
BLOCK = '# /// script\n# [tool.fsbpyr]\n# glibc = "2.28"\n# ///\n'


def _write(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        (root / rel).write_text(text)
    return root


def _run(executable: Path, tmp: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp / "cache")}
    return contain.run([executable, *args], env=env, timeout=120)


def _inspect(app: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    capsys.readouterr()
    assert main(["inspect", str(app), "--json"]) == 0
    document: dict[str, object] = json.loads(capsys.readouterr().out)
    return document


@pytest.mark.parametrize("persisted", [False, True], ids=["option", "tool-table"])
def test_a_directory_of_scripts_builds_for_el8_and_its_commands_run(
    tmp_path: Path, el8_runtime: Runtime, capsys: pytest.CaptureFixture[str], persisted: bool
) -> None:
    """The glibc comes from the option, or from the directory's pyproject.toml that holds
    settings only, which must not send the scripts to a build backend."""
    ops = _write(tmp_path / "ops", OPS)
    options = ["--glibc", BASELINE]
    if persisted:
        _write(ops, {"pyproject.toml": f'[tool.fsbpyr]\nglibc = "{BASELINE}"\n'})
        options = []

    assert main(["build", str(ops), *options]) == 0

    dist = ops / "dist"
    assert sorted(path.name for path in dist.iterdir()) == ["alpha", "beta", "ops"]
    assert [os.readlink(dist / name) for name in ("alpha", "beta")] == ["ops", "ops"]
    document = _inspect(dist / "ops", capsys)
    assert document["glibc_baseline"] == BASELINE and document["glibc"] == BASELINE
    assert sorted(document["commands"]) == ["alpha", "beta"]  # type: ignore[call-overload]
    result = _run(dist / "beta", tmp_path, "x", "--y")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "beta ['x', '--y']\n"


def test_a_single_script_whose_block_says_glibc_2_28_builds_for_el8(
    tmp_path: Path, el8_runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _write(tmp_path / "tools", {"report.py": BLOCK + GUARD.format(name="report")})
    out = tmp_path / "report"

    assert main(["build", str(script / "report.py"), "-o", str(out)]) == 0

    document = _inspect(out, capsys)
    assert document["glibc_baseline"] == BASELINE and document["glibc"] == BASELINE
    result = _run(out, tmp_path, "a")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "report ['a']\n"


def test_a_project_of_several_commands_builds_for_el8_with_its_links(
    tmp_path: Path, el8_runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "multi"
    shutil.copytree(FIXTURE, project)

    assert main(["build", str(project), "--glibc", BASELINE]) == 0

    dist = project / "dist"
    document = _inspect(dist / "multi", capsys)
    assert document["glibc_baseline"] == BASELINE
    assert document["commands"] == {"multi-alpha": "multi.cli:alpha",
                                    "multi-beta": "multi.cli:beta"}  # fmt: skip
    assert [os.readlink(dist / name) for name in ("multi-alpha", "multi-beta")] == ["multi"] * 2
    result = _run(dist / "multi-beta", tmp_path, "x")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "beta ['multi-beta', 'x']\n"
