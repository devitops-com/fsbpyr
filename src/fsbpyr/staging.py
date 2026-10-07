"""Fresh staging install of the project from its lock (plan §3.6 step 3, decision D5).

uv does all of it (D26): pip projects (requirements.txt, unlocked) are installed by uv too, so
every build picks wheels for the runtime's glibc baseline. The project itself is always built
into a wheel *out of tree*: uv builds an sdist and then a wheel from it, so stale in-tree build
directories (setuptools ``build/``) or cached builds can never leak into the executable, and
the developer's tree is never written to. Dependencies are installed from the lock into a
throwaway virtual environment (a uv project environment, or one made by ``uv venv``), then the
fresh wheel is installed without dependencies. In a venv, the console scripts the installer
writes (whose ``#!`` names whichever interpreter uv picked) stay outside site-packages, so the
executable does not depend on the build host's Pythons. The developer's virtual environment is
never read or modified. Every command runs in the project directory, so relative paths in
requirements files resolve as the user expects.
"""

import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from packaging.utils import canonicalize_name

from .project import LockSource, Project

WHEEL = "{wheel}"  # placeholder in install commands, replaced by the built wheel's path
_ERROR_TAIL = 3000
_UV_REQUIRED = (
    "fsbpyr needs uv to build (it installs the project and picks wheels that run on the glibc "
    "baseline); install it from https://docs.astral.sh/uv/getting-started/installation/, e.g. "
    "`curl -LsSf https://astral.sh/uv/install.sh | sh` or `pipx install uv`"
)


class StagingError(RuntimeError):
    """The staging install could not be planned or failed."""


@dataclass(frozen=True, slots=True)
class StagingOptions:
    python: str  # major.minor of the runtime
    extras: tuple[str, ...] = ()
    offline: bool = False
    requirements: str | None = None
    # uv's --python-platform (x86_64-manylinux_2_28): wheels for the runtime's glibc baseline
    # rather than the build host's. A dependency without such a wheel is built from its sdist.
    platform: str | None = None


@dataclass(frozen=True, slots=True)
class StagingPlan:
    site: Path
    build: list[str]  # builds the project wheel into wheel_dir
    install: list[list[str]]  # may reference WHEEL
    wheel_dir: Path
    env: dict[str, str] = field(default_factory=dict)


def find_uv() -> Path:
    uv = shutil.which("uv")
    if uv is None:
        raise StagingError(_UV_REQUIRED)
    return Path(uv)


def local_packages(lock: Path, exclude: str | None) -> list[str]:
    """Packages a uv.lock builds from local sources (workspace members, path dependencies).

    uv caches such builds keyed on pyproject.toml rather than on the sources, so they are
    reinstalled on every build (--reinstall-package) to pick up source changes.
    """
    try:
        data = tomllib.loads(lock.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise StagingError(f"cannot read {lock}: {exc}") from exc
    names: list[str] = []
    for package in data.get("package", []):
        source = package.get("source", {})
        name = canonicalize_name(package.get("name", ""))
        if ("editable" in source or "directory" in source) and name != exclude:
            names.append(name)
    return names


def _platform(options: StagingOptions) -> list[str]:
    return ["--python-platform", options.platform] if options.platform else []


def _uv_build(uv: Path, work: Path, project: Project, options: StagingOptions) -> list[str]:
    command = [str(uv), "build", "--out-dir", str(work / "wheel"), "--python", options.python]
    return command + ["--offline"] * options.offline + ["--quiet", str(project.directory)]


def _uv_sync_plan(project: Project, uv: Path, work: Path, options: StagingOptions) -> StagingPlan:
    root = project.uv_root
    if root is None or project.name is None:
        raise StagingError("uv.lock projects need [project] name in pyproject.toml")
    stage = work / "stage"
    sync = [str(uv), "sync", "--locked", "--no-dev", "--no-default-groups", "--no-editable",
            "--no-install-project", "--python", options.python, "--project", str(root)]  # fmt: skip
    if root != project.directory:
        sync += ["--package", project.name]
    for name in local_packages(root / "uv.lock", project.name):
        sync += ["--reinstall-package", name]
    sync += [arg for extra in options.extras for arg in ("--extra", extra)]
    sync += _platform(options) + ["--offline"] * options.offline + ["--quiet"]
    wheel = [str(uv), "pip", "install", "--python", str(stage / "bin" / "python"), "--no-deps"]
    wheel += _platform(options) + ["--offline"] * options.offline + ["--quiet", WHEEL]
    return StagingPlan(
        site=stage / "lib" / f"python{options.python}" / "site-packages",
        build=_uv_build(uv, work, project, options),
        install=[sync, wheel],
        wheel_dir=work / "wheel",
        env={"UV_PROJECT_ENVIRONMENT": str(stage)},
    )


def staging_plan(
    project: Project,
    source: LockSource,
    uv: Path,
    work: Path,
    options: StagingOptions,
) -> StagingPlan:
    if source is LockSource.UV_LOCK:
        return _uv_sync_plan(project, uv, work, options)
    stage = work / "stage"
    site = stage / "lib" / f"python{options.python}" / "site-packages"
    build = _uv_build(uv, work, project, options)
    offline = ["--offline"] * options.offline
    venv = [str(uv), "venv", "--python", options.python, *offline, "--quiet", str(stage)]
    base = [str(uv), "pip", "install", "--python", str(stage / "bin" / "python")]
    base += [*_platform(options), *offline, "--quiet"]
    wheel_dir = work / "wheel"
    env = {"UV_VENV_SEED": "0"}  # a seeded venv would ship pip, depending on the environment
    if source is LockSource.UNLOCKED:
        extras = f"[{','.join(options.extras)}]" if options.extras else ""
        return StagingPlan(site, build, [venv, [*base, WHEEL + extras]], wheel_dir, env)
    if source is LockSource.PYLOCK:
        lock, hashes = project.directory / LockSource.PYLOCK.value, []
    else:
        lock = project.requirements_path(options.requirements)
        hashes = ["--require-hashes"] if "--hash=" in lock.read_text() else []
    install = [venv, [*base, *hashes, "-r", str(lock)], [*base, "--no-deps", WHEEL]]
    return StagingPlan(site, build, install, wheel_dir, env)


def _run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    result = subprocess.run(
        command, cwd=cwd, env={**os.environ, **env}, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        output = (result.stderr or result.stdout)[-_ERROR_TAIL:]
        raise StagingError(f"{' '.join(command[:4])} ... failed:\n{output}")


def run_plan(plan: StagingPlan, project_dir: Path) -> Path:
    """Build the project wheel, install dependencies and the wheel; returns the wheel."""
    plan.wheel_dir.mkdir(parents=True, exist_ok=True)
    _run(plan.build, project_dir, plan.env)
    wheels = sorted(plan.wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise StagingError(f"expected one built wheel in {plan.wheel_dir}, found {len(wheels)}")
    for command in plan.install:
        _run([part.replace(WHEEL, str(wheels[0])) for part in command], project_dir, plan.env)
    return wheels[0]
