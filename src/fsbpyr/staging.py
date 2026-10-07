"""Fresh staging install of the project from its lock (plan §3.6 step 3, decision D5).

The project itself is always built into a wheel *out of tree*: uv builds an sdist and then a
wheel from it; pip builds from a clean copy of the project. Stale in-tree build directories
(setuptools ``build/``) or cached builds can therefore never leak into the executable, and the
developer's tree is never written to. Dependencies are installed from the lock into a private
location (a uv project environment, or a ``--target`` directory), then the fresh wheel is
installed without dependencies. The developer's virtual environment is never read or modified.
Every command runs in the project directory, so relative paths in requirements files resolve
as the user expects.
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
_VERSION_PROBE = "import sys, pip; print('%d.%d' % sys.version_info[:2])"
_TOP_LEVEL_JUNK = frozenset({
    "build", "dist", ".venv", "venv", ".tox", ".nox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache",
})  # fmt: skip


class StagingError(RuntimeError):
    """The staging install could not be planned or failed."""


@dataclass(frozen=True, slots=True)
class Installer:
    uv: Path | None
    python: Path | None  # interpreter that runs pip, when uv is not used


@dataclass(frozen=True, slots=True)
class StagingOptions:
    python: str  # major.minor of the runtime
    extras: tuple[str, ...] = ()
    offline: bool = False
    requirements: str | None = None
    # uv's --python-platform (x86_64-manylinux_2_28): wheels for the runtime's glibc baseline
    # rather than the build host's. A dependency without such a wheel is built from its sdist.
    # pip would refuse every sdist with a platform, so it does not get one.
    platform: str | None = None


@dataclass(frozen=True, slots=True)
class StagingPlan:
    site: Path
    build: list[str]  # builds the project wheel into wheel_dir
    install: list[list[str]]  # may reference WHEEL
    wheel_dir: Path
    env: dict[str, str] = field(default_factory=dict)
    copy: Path | None = None  # copy the project here before building (pip builds in-tree)


def _python_version(interpreter: Path) -> str | None:
    try:
        result = subprocess.run(
            [interpreter, "-c", _VERSION_PROBE],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def find_interpreter(python: str, project_dir: Path, explicit: Path | None) -> Path:
    """A Python ``python`` with pip: --interpreter, $VIRTUAL_ENV, .venv, then pythonX.Y."""
    if explicit is not None:
        if _python_version(explicit) != python:
            raise StagingError(f"--interpreter {explicit} is not Python {python} with pip")
        return explicit
    candidates = [project_dir / ".venv" / "bin" / "python"]
    if virtual_env := os.environ.get("VIRTUAL_ENV"):
        candidates.insert(0, Path(virtual_env) / "bin" / "python")
    if found := shutil.which(f"python{python}"):
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.exists() and _python_version(candidate) == python:
            return candidate
    raise StagingError(f"no Python {python} interpreter with pip found; pass --interpreter")


def find_installer(
    source: LockSource, python: str, project_dir: Path, interpreter: Path | None
) -> Installer:
    uv = shutil.which("uv")
    if uv is not None:
        return Installer(uv=Path(uv), python=None)
    if source in (LockSource.UV_LOCK, LockSource.PYLOCK):
        raise StagingError(f"{source.value} requires uv on PATH")
    return Installer(uv=None, python=find_interpreter(python, project_dir, interpreter))


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
    installer: Installer,
    work: Path,
    options: StagingOptions,
) -> StagingPlan:
    if source is LockSource.UV_LOCK:
        if installer.uv is None:
            raise StagingError("uv.lock requires uv on PATH")
        return _uv_sync_plan(project, installer.uv, work, options)
    stage = work / "stage"
    copy = None
    if installer.uv is not None:
        build = _uv_build(installer.uv, work, project, options)
        base = [str(installer.uv), "pip", "install", "--target", str(stage)]
        base += ["--python", options.python, *_platform(options)]
        base += ["--offline"] * options.offline + ["--quiet"]
    elif installer.python is not None and source is not LockSource.PYLOCK:
        pip = [str(installer.python), "-m", "pip"]
        flags = ["--quiet", "--disable-pip-version-check"] + ["--no-index"] * options.offline
        copy = work / "src"
        build = [*pip, "wheel", "--no-deps", "--wheel-dir", str(work / "wheel"), *flags, str(copy)]
        base = [*pip, "install", "--target", str(stage), *flags]
    else:
        raise StagingError(f"{source.value} requires uv on PATH")
    wheel_dir = work / "wheel"
    if source is LockSource.UNLOCKED:
        extras = f"[{','.join(options.extras)}]" if options.extras else ""
        return StagingPlan(stage, build, [[*base, WHEEL + extras]], wheel_dir, copy=copy)
    if source is LockSource.PYLOCK:
        lock, hashes = project.directory / LockSource.PYLOCK.value, []
    else:
        lock = project.requirements_path(options.requirements)
        hashes = ["--require-hashes"] if "--hash=" in lock.read_text() else []
    install = [[*base, *hashes, "-r", str(lock)], [*base, "--no-deps", WHEEL]]
    return StagingPlan(stage, build, install, wheel_dir, copy=copy)


def copy_project(source: Path, target: Path) -> None:
    """Copy the sources pip builds from, without build products, caches or environments."""

    def ignore(directory: str, names: list[str]) -> set[str]:
        top = Path(directory) == source
        return {
            name for name in names
            if name == "__pycache__" or name.endswith(".egg-info")
            or (top and name in _TOP_LEVEL_JUNK)
        }  # fmt: skip

    shutil.copytree(source, target, ignore=ignore, symlinks=True)


def _run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    result = subprocess.run(
        command, cwd=cwd, env={**os.environ, **env}, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        output = (result.stderr or result.stdout)[-_ERROR_TAIL:]
        raise StagingError(f"{' '.join(command[:4])} ... failed:\n{output}")


def run_plan(plan: StagingPlan, project_dir: Path) -> Path:
    """Build the project wheel, install dependencies and the wheel; returns the wheel."""
    if plan.copy is not None:
        copy_project(project_dir, plan.copy)
    plan.wheel_dir.mkdir(parents=True, exist_ok=True)
    _run(plan.build, project_dir, plan.env)
    wheels = sorted(plan.wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise StagingError(f"expected one built wheel in {plan.wheel_dir}, found {len(wheels)}")
    for command in plan.install:
        _run([part.replace(WHEEL, str(wheels[0])) for part in command], project_dir, plan.env)
    return wheels[0]
