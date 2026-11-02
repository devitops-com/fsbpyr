"""One virtual environment per pytest-xdist worker: no two workers share one (owner, 2026-11-02).

Python writes a module's .pyc beside it the first time it imports it, so workers on one environment
change it under each other: the self-hosting test compares the environment it runs in, and a .pyc
another worker wrote failed it in 4 of the 8 suite jobs of a run.

Each worker gets a new environment of uv.lock, removed when the run ends. Starting it on that
environment's Python is not enough: xdist gives a worker on this machine the controller's sys.path,
so it would run its own Python and import from the controller's environment; and `uv run` leaves
that environment on PATH and in VIRTUAL_ENV, for the worker's subprocesses to find. All of it
moves, and a worker that finds anything else stops the run. tests/integration/test_worker_venvs.py
runs this for real, because `xdist.workermanage._sys_path` is xdist's own: uv.lock pins xdist, and
that test and the check here fail if a newer one stops reading it."""

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

import execnet
import pytest
from xdist import plugin, workermanage  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
_HOME = pytest.StashKey[Path]()  # the controller's: the directory of this run's environments
_VENVS = pytest.StashKey[dict[int, str]]()  # the controller's: each spec's environment, by id()
_VENV = "fsbpyr_venv"  # in workerinput: the environment a worker must be in


def _moved(path: str, venv: str) -> str:
    """``path``, with the environment of this process, the controller, replaced by ``venv``."""
    if path == sys.prefix or path.startswith(sys.prefix + os.sep):
        return venv + path.removeprefix(sys.prefix)
    return path


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_setupnodes(config: pytest.Config, specs: Sequence[execnet.XSpec]) -> None:
    """Before the workers start: an environment for each, and the Python and variables it uses."""
    home = Path(tempfile.mkdtemp(prefix="fsbpyr-venvs-"))
    config.stash[_HOME] = home
    config.stash[_VENVS] = {}
    for spec in specs:
        venv = str(home / str(spec.id))
        config.stash[_VENVS][id(spec)] = venv
        sync = ["uv", "sync", "--locked", "--quiet", "--project", str(ROOT),
                "--python", sys.executable]  # fmt: skip
        subprocess.run(sync, env={**os.environ, "UV_PROJECT_ENVIRONMENT": venv}, check=True)
        path = [_moved(entry, venv) for entry in os.environ["PATH"].split(os.pathsep)]
        spec.python = f"{venv}/bin/python"
        spec.env["VIRTUAL_ENV"] = venv
        spec.env["PATH"] = os.pathsep.join(path)


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: workermanage.WorkerController) -> None:
    """Called for each worker just before xdist sends it sys.path, which it would otherwise send
    as the controller has it. By its spec: a worker that replaces a crashed one keeps the spec.
    Not read from the spec's Python, which is what the worker's check checks."""
    venv = node.config.stash[_VENVS][id(node.gateway.spec)]
    node.workerinput[_VENV] = venv
    workermanage._sys_path = [_moved(entry, venv) for entry in plugin._sys_path]


def _not_isolated(venv: str) -> str | None:
    """What is not ``venv`` in this process, or None."""
    own = venv + os.sep
    if sys.prefix != venv:
        return f"sys.prefix is {sys.prefix}"
    if os.environ.get("VIRTUAL_ENV") != venv:
        return f"VIRTUAL_ENV is {os.environ.get('VIRTUAL_ENV')}"
    python = shutil.which("python")
    if python is None or not python.startswith(own):
        return f"python on PATH is {python}"
    foreign = [entry for entry in sys.path if entry.endswith("site-packages")
               and not entry.startswith(own)]  # fmt: skip
    if foreign:
        return f"sys.path has {foreign[0]}"
    if not str(pytest.__file__).startswith(own):
        return f"pytest comes from {pytest.__file__}"
    return None


def pytest_configure(config: pytest.Config) -> None:
    workerinput = getattr(config, "workerinput", None)
    if workerinput is None:
        return
    problem = _not_isolated(workerinput[_VENV])
    if problem:
        raise pytest.UsageError(f"a worker must run in {workerinput[_VENV]} alone: {problem}")


def pytest_unconfigure(config: pytest.Config) -> None:
    home = config.stash.get(_HOME, None)
    if home is not None:
        shutil.rmtree(home)
