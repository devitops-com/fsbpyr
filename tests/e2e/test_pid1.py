"""An app that is process 1 ends on SIGTERM with status 143 (plan D14, D41; decided while building
it, 2026-10-25). The kernel drops a signal that process 1 has no handler for, so `python app.py` as
PID 1 ignores `docker stop`; the boot installs a SystemExit(143) handler there, unless the
app's SIGTERM disposition is not the default."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from fsbpyr.assemble import App, build_executable
from fsbpyr.runtime.registry import Runtime

from ..support import contain, requires
from .conftest import app_content

pytestmark = pytest.mark.slow
TIMEOUT_S = 120
_UNSHARE = ["unshare", "--user", "--map-root-user", "--pid", "--fork", "--mount-proc"]
_PODMAN_IMAGE = "debian:13"
_SENDS_SIGTERM = """\
import os, signal, sys, time

def main():
    if len(sys.argv) > 1:
        signal.signal(signal.SIGTERM, lambda signum, frame: (print("own handler"), sys.exit(7)))
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(2)  # a SIGTERM that is dropped (or ignored) lets this run out
    print("survived")
"""


def _unshare_works() -> bool:
    if shutil.which("unshare") is None:
        return False
    return subprocess.run([*_UNSHARE, "true"], capture_output=True, check=False).returncode == 0


def _podman_works() -> bool:
    if shutil.which("podman") is None:
        return False
    probe = ["podman", "image", "exists", _PODMAN_IMAGE]
    return subprocess.run(probe, capture_output=True, check=False).returncode == 0


def _as_pid_1(app: Path) -> list[str | Path]:
    """The command that runs ``app`` as process 1 of a PID namespace, or a failure."""
    if _unshare_works():
        return [*_UNSHARE, app]
    if _podman_works():
        return ["podman", "run", "--rm", "--pids-limit", "64", "-v", f"{app}:/app:Z",
                _PODMAN_IMAGE, "/app"]  # fmt: skip
    requires.unmet(f"neither unshare --user --map-root-user --pid works nor podman with "
                   f"{_PODMAN_IMAGE}",
                   "allow user namespaces (kernel.apparmor_restrict_unprivileged_userns=0 on "
                   f"Ubuntu) or install rootless podman and pull {_PODMAN_IMAGE}")


@pytest.fixture(scope="module")
def app(runtime: Runtime, tmp_path_factory: pytest.TempPathFactory) -> Path:
    modules, files = app_content({"x.py": _SENDS_SIGTERM})
    out = tmp_path_factory.mktemp("pid1") / "x"
    build_executable(runtime, App("x", "x:main", modules=modules, files=files), out)
    return out


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return {**os.environ, "FSBPYR_CACHE_DIR": str(tmp_path / "cache"), "FSBPYR_GC": "0"}


def _run(command: list[str | Path], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return contain.run(command, env=env, timeout=TIMEOUT_S)


def test_sigterm_ends_an_app_that_is_process_1_with_status_143(
    app: Path, env: dict[str, str]
) -> None:
    result = _run(_as_pid_1(app), env)

    assert (result.returncode, result.stdout) == (143, "")


def test_an_app_that_installs_its_own_handler_keeps_it_as_process_1(
    app: Path, env: dict[str, str]
) -> None:
    result = _run([*_as_pid_1(app), "own"], env)

    assert (result.returncode, result.stdout) == (7, "own handler\n")


def test_an_app_that_is_not_process_1_dies_of_the_signal_as_always(
    app: Path, env: dict[str, str]
) -> None:
    result = _run([app], env)

    assert (result.returncode, result.stdout) == (-15, "")
