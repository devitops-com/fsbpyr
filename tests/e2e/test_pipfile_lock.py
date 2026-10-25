"""Pipfile.lock as the lock of a build (research UC3): a directory of scripts with a Pipfile and
its lock builds from the lock's default section, a stale lock warns, and a directory with a
lock and no Python file is a tool binary of the Pipfile's packages. Needs the cached runtime;
the fixture is tests/fixtures/projects/foreign/pipenv."""

import hashlib
import io
import json
import os
import shutil
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from fsbpyr.cli import main
from fsbpyr.runtime.registry import Runtime

from ..support import contain

pytestmark = pytest.mark.slow

FIXTURE = Path(__file__).parents[1] / "fixtures" / "projects" / "foreign" / "pipenv"
WHEEL_HASHES = {  # the wheels of the fixture's lock
    "click": "255bc9599cf7748b4b1a446ccc735421bd08a2ae529a8b88597d3de5664ee360",
    "rich": "33bd4ef74232fb73fe9279a257718407f169c09b78a87ad3d296f548e27de0bb",
}
PINS = {  # flake8 7.1.1 and what it needs, as uv hashes them (uv pip compile --generate-hashes)
    "flake8": ("7.1.1", ["049d058491e228e03e67b390f311bbf88fce2dbaa8fa673e7aea87b7198b8d38",
                         "597477df7860daa5aa0fdd84bf5208a043ab96b8e96ab708770ae0364dd03213"]),
    "mccabe": ("0.7.0", ["348e0240c33b60bbdf4e523192ef919f28cb2c3d7d5c7794f74009290f236325",
                         "6c2d30ab6be0e4a46919781807b4f0d834ebdd6c6e3dca0bda5a15f863427b6e"]),
    "pycodestyle": ("2.12.1", ["46f0fb92069a7c28ab7bb558f05bfc0110dac69a0cd23c61ea0040283a9d78b3",
                               "6838eae08bbce4f6accd5d5572075c63626a15ee3e6f842df996bf62f6d73521"]),
    "pyflakes": ("3.2.0", ["1c61603ff154621fb2a9172037d84dca3500def8c8b630657d1701f026f8af3f",
                           "84b5be138a2dfbb40689ca07e2152deb896a65c3a3e24c251c5c62489568074a"]),
}  # fmt: skip


def _run(executable: Path, tmp: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp / "cache")}
    return contain.run([executable, *args], env=env, timeout=120)


def _listing(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def _build(project: Path, *args: str) -> int:
    return main(["build", str(project), "-o", str(project.parent / "out" / "app"), *args])


def test_a_pipfile_lock_locks_the_build_of_a_directory_of_scripts(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "pipenv"
    shutil.copytree(FIXTURE, project)
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
    digest = hashlib.sha256((project / "Pipfile.lock").read_bytes()).hexdigest()
    assert info["lock"] == {"kind": "Pipfile.lock", "path": "Pipfile.lock", "hashed": True,
                            "sha256": digest}  # fmt: skip
    pins = {d["name"]: d for d in info["dists"]}
    assert {name: pins[name]["version"] for name in ("click", "rich", "pygments")} == {
        "click": "8.5.0", "rich": "15.0.0", "pygments": "2.21.0"}  # fmt: skip
    assert "colorama" not in pins and "iniconfig" not in pins
    assert main(["inspect", str(tmp_path / "out" / "app")]) == 0
    assert f"lock       Pipfile.lock sha256:{digest[:8]}" in capsys.readouterr().out


def test_a_pipfile_changed_after_locking_is_a_warning_and_an_error_with_strict(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "pipenv"
    shutil.copytree(FIXTURE, project)
    pipfile = project / "Pipfile"
    pipfile.write_text(pipfile.read_text().replace('click = ">=8.1"', 'click = ">=8.2"'))

    assert _build(project) == 0
    assert "Pipfile.lock is out of date" in capsys.readouterr().err

    assert _build(project, "--strict") == 1
    assert "--strict: the build has 1 warning" in capsys.readouterr().err


def test_a_changed_hash_in_the_lock_fails_the_build(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    """The converted file demands the lock's hashes (uv --require-hashes)."""
    project = tmp_path / "pipenv"
    shutil.copytree(FIXTURE, project)
    lock = project / "Pipfile.lock"
    lock.write_text(lock.read_text().replace(WHEEL_HASHES["click"], "0" * 64))
    lock.write_text(lock.read_text().replace("ba0d2089de75ea0310e2dde03160e6ca10009947fb95a182f9b54"
                                             "021bb272e34", "1" * 64))  # fmt: skip

    assert _build(project) == 1

    assert "Hash mismatch" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def _tool_directory(root: Path, *, pipfile: bool) -> Path:
    """A directory of a Pipfile and its lock, no Python file: flake8 and what it needs."""
    default = {name: {"version": f"=={version}", "hashes": [f"sha256:{h}" for h in hashes],
                      "index": "pypi"} for name, (version, hashes) in PINS.items()}  # fmt: skip
    lock = {"_meta": {"requires": {"python_version": "3.12"}, "pipfile-spec": 6,
                      "sources": [{"name": "pypi", "url": "https://pypi.org/simple"}]},
            "default": default, "develop": {}}  # fmt: skip
    root.mkdir(parents=True)
    (root / "Pipfile.lock").write_text(json.dumps(lock, indent=4, sort_keys=True) + "\n")
    if pipfile:
        (root / "Pipfile").write_text('[packages]\nflake8 = "*"\n\n[requires]\n'
                                      'python_version = "3.12"\n')  # fmt: skip
    return root


@pytest.mark.parametrize("pipfile", [True, False])
def test_a_directory_with_a_pipfile_lock_and_no_python_file_builds_the_tool_not_its_dependencies(
    tmp_path: Path, runtime: Runtime, pipfile: bool
) -> None:
    directory = _tool_directory(tmp_path / "linter", pipfile=pipfile)
    out, err = io.StringIO(), io.StringIO()

    with redirect_stdout(out), redirect_stderr(err):
        code = main(["build", str(directory)])

    assert code == 0, err.getvalue()
    assert [path.name for path in (directory / "dist").iterdir()] == ["flake8"]
    assert "commands: flake8 (flake8 7.1.1)" in out.getvalue()
    result = _run(directory / "dist" / "flake8", tmp_path, "--version")
    assert result.stdout.startswith("7.1.1 ")
