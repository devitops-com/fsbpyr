"""tests/support/worker_venvs.py: no two pytest-xdist workers share a virtual environment. Each test
reads a real run: a pytest of three workers on the probe below, on the suite's own plugin, with
`--dist each`, so that every worker reports."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKERS = ["gw0", "gw1", "gw2"]
PLUGIN = "tests.support.worker_venvs"
PROBE = """
import json
import os
import shutil
import sys
from pathlib import Path

import click
import pytest


def test_report(worker_id):
    if worker_id == "gw0":  # the import that failed the self-hosting test: first in one worker
        from click.formatting import wrap_text

        wrap_text("a b c")
    Path(os.environ["REPORTS"], worker_id).write_text(json.dumps({
        "prefix": sys.prefix,
        "executable": sys.executable,
        "path": sys.path,
        "click": click.__file__,
        "pytest": pytest.__file__,
        "python_on_path": shutil.which("python"),
        "virtual_env": os.environ["VIRTUAL_ENV"],
        "textwrap_pyc": [str(pyc) for pyc in
                         Path(click.__file__).parent.glob("__pycache__/_textwrap.*.pyc")],
    }))
"""


# What decides whether and where Python writes a .pyc: the run writes them beside the modules, as
# the suite's workers would without these, whatever the environment the suite runs in sets.
PYC_VARIABLES = ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")


def _pytest(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    (directory / "test_probe.py").write_text(PROBE)
    env = {name: value for name, value in os.environ.items()
           if not name.startswith("PYTEST_") and name not in PYC_VARIABLES}  # fmt: skip
    env |= {"REPORTS": str(directory), "PYTHONPATH": str(directory)}  # the run's own PYTEST_ go
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-n", str(len(WORKERS)), "--dist", "each",
         "-p", PLUGIN, "-p", "no:cacheprovider", "--rootdir", directory,
         directory / "test_probe.py", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False)  # fmt: skip


@pytest.fixture(scope="module")
def reports(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    directory = tmp_path_factory.mktemp("workers")
    with pytest.MonkeyPatch.context() as patch:  # set, so that the run shows they stay out of it
        patch.setenv("PYTHONDONTWRITEBYTECODE", "1")
        patch.setenv("PYTHONPYCACHEPREFIX", str(directory / "pycache"))
        done = _pytest(directory)
    assert done.returncode == 0, done.stdout + done.stderr
    return {worker: json.loads((directory / worker).read_text()) for worker in WORKERS}


def _inside(path: str, prefix: str) -> bool:
    return path.startswith(prefix + os.sep)


def test_no_two_workers_share_an_environment(reports: dict[str, dict[str, Any]]) -> None:
    prefixes = {report["prefix"] for report in reports.values()}

    assert len(prefixes) == len(WORKERS)
    assert sys.prefix not in prefixes  # nor the one the run was started from


def test_a_worker_imports_from_its_own_environment(reports: dict[str, dict[str, Any]]) -> None:
    for report in reports.values():
        assert _inside(report["executable"], report["prefix"])
        assert _inside(report["click"], report["prefix"])
        assert _inside(report["pytest"], report["prefix"])


def test_no_sys_path_entry_is_in_another_environment(reports: dict[str, dict[str, Any]]) -> None:
    prefixes = [report["prefix"] for report in reports.values()]
    for report in reports.values():
        others = [prefix for prefix in [*prefixes, sys.prefix] if prefix != report["prefix"]]
        for entry in report["path"]:
            assert not [other for other in others if entry == other or _inside(entry, other)]


def test_a_workers_subprocesses_find_its_own_environment(
    reports: dict[str, dict[str, Any]],
) -> None:
    for report in reports.values():
        assert report["virtual_env"] == report["prefix"]
        assert _inside(report["python_on_path"], report["prefix"])


def test_what_one_worker_imports_first_appears_in_its_environment_alone(
    reports: dict[str, dict[str, Any]],
) -> None:
    assert len(reports["gw0"]["textwrap_pyc"]) == 1
    assert _inside(reports["gw0"]["textwrap_pyc"][0], reports["gw0"]["prefix"])
    assert not reports["gw1"]["textwrap_pyc"] + reports["gw2"]["textwrap_pyc"]


def test_the_environments_go_with_the_run(reports: dict[str, dict[str, Any]]) -> None:
    assert not [report["prefix"] for report in reports.values() if Path(report["prefix"]).exists()]


@pytest.mark.parametrize(
    ("undone", "named"),
    [
        ("import pytest\n"
         "from xdist import plugin, workermanage\n"
         "@pytest.hookimpl(trylast=True)\n"
         "def pytest_configure_node(node):\n"
         "    workermanage._sys_path = plugin._sys_path\n",
         "sys.path has"),
        ("import pytest, sys\n"
         "@pytest.hookimpl(trylast=True)\n"
         "def pytest_xdist_setupnodes(specs):\n"
         "    for spec in specs:\n"
         "        spec.python = sys.executable\n",
         "sys.prefix is"),
        ("import os, pytest\n"
         "@pytest.hookimpl(trylast=True)\n"
         "def pytest_xdist_setupnodes(specs):\n"
         "    for spec in specs:\n"
         "        spec.env['VIRTUAL_ENV'] = os.environ['VIRTUAL_ENV']\n",
         "VIRTUAL_ENV is"),
        ("import os, pytest\n"
         "@pytest.hookimpl(trylast=True)\n"
         "def pytest_xdist_setupnodes(specs):\n"
         "    for spec in specs:\n"
         "        spec.env['PATH'] = os.environ['PATH']\n",
         "python on PATH is"),
    ],
    ids=["sys.path", "interpreter", "VIRTUAL_ENV", "PATH"],
)
def test_a_worker_that_is_not_isolated_stops_the_run(
    tmp_path: Path, undone: str, named: str
) -> None:
    (tmp_path / "undo.py").write_text(undone)

    done = _pytest(tmp_path, "-p", "undo")

    assert done.returncode != 0
    assert named in done.stdout + done.stderr
    assert not (tmp_path / "gw0").exists()  # no test ran
