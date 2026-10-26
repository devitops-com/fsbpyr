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
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from packaging.utils import canonicalize_name

from . import pipenv
from .closure import marker_environment
from .foreign import export
from .loose import Package
from .project import LockSource, Project, has_hash_option
from .text import tail_lines
from .vcs import commit_time

WHEEL = "{wheel}"  # placeholder in install commands, replaced by the built wheel's path
SOURCE_DATE_EPOCH = "SOURCE_DATE_EPOCH"
EPOCH_FLOOR = 315532800  # 1980-01-01, the earliest time a zip entry can hold
_ERROR_TAIL = 3000
_VERSION_TIMEOUT = 30
_UV_VERSION = re.compile(r"uv (\S+)")
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
    # uv's --python-platform (x86_64-manylinux_2_34): wheels for the runtime's glibc baseline
    # rather than the build host's. A dependency without such a wheel is built from its sdist.
    platform: str | None = None
    strict: bool = False  # requirements files must pin and hash every line (uv --require-hashes)
    package: Package | None = None  # the files the build checked: the wheel of a loose project
    # the markers of the target, for the locks that are converted (Poetry, PDM and Pipenv)
    environment: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class StagingPlan:
    site: Path
    build: list[str]  # builds the project wheel into wheel_dir; empty: fsbpyr writes it (loose.py)
    install: list[list[str]]  # may reference WHEEL
    wheel_dir: Path
    env: dict[str, str] = field(default_factory=dict)
    forget: list[str] = field(default_factory=list)  # + names: drops uv's cached builds of them
    package: Package | None = None  # the loose project's files, as checked (empty ``build``)


def find_uv() -> Path:
    uv = shutil.which("uv")
    if uv is None:
        raise StagingError(_UV_REQUIRED)
    return Path(uv)


def uv_version(uv: Path) -> str:
    """uv's version, "0.13.1": it builds the project's wheel and installs everything, so the
    executable's bytes can depend on it (recorded in build_info, shown by fsbpyr inspect)."""
    try:
        result = subprocess.run([uv, "--version"], capture_output=True, text=True,
                                timeout=_VERSION_TIMEOUT, check=False)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StagingError(f"cannot run {uv} --version: {exc}") from exc
    match = _UV_VERSION.match(result.stdout)
    if result.returncode != 0 or match is None:
        output = tail_lines((result.stdout + result.stderr).strip(), _ERROR_TAIL)
        raise StagingError(f"{uv} --version did not report a version: {output}")
    return match.group(1)


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
    if project.loose:  # no build system: fsbpyr writes the wheel itself, _build_wheel
        return []
    command = [str(uv), "build", "--out-dir", str(work / "wheel"), "--python", options.python]
    # not --quiet: a failure's reason (setuptools' "Multiple top-level modules ...") is uv's output
    return command + ["--offline"] * options.offline + [str(project.directory)]


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
        forget=[str(uv), "cache", "clean"],
    )


def _script_lock_plan(
    project: Project, uv: Path, work: Path, options: StagingOptions
) -> StagingPlan:
    """The locked install of a script: ``uv sync --script`` into the staging environment, which
    ``--active`` and VIRTUAL_ENV name. ``--locked`` fails a lock the block has outgrown, and
    uv writes neither the lock nor anything else beside the script."""
    assert project.script is not None  # noqa: S101  the lock source says so
    stage = work / "stage"
    sync = [str(uv), "sync", "--script", str(project.script), "--active", "--locked",
            "--python", options.python, *_platform(options)]  # fmt: skip
    sync += ["--offline"] * options.offline + ["--quiet"]
    wheel = [str(uv), "pip", "install", "--python", str(stage / "bin" / "python"), "--no-deps"]
    wheel += _platform(options) + ["--offline"] * options.offline + ["--quiet", WHEEL]
    return StagingPlan(
        site=stage / "lib" / f"python{options.python}" / "site-packages",
        build=[],
        install=[sync, wheel],
        wheel_dir=work / "wheel",
        env={"VIRTUAL_ENV": str(stage)},
        forget=[str(uv), "cache", "clean"],
    )


def staging_plan(
    project: Project,
    source: LockSource,
    uv: Path,
    work: Path,
    options: StagingOptions,
) -> StagingPlan:
    """The plan for ``source``, its uv steps (and the rebuild of dists from sdists) running
    with SOURCE_DATE_EPOCH: the user's, else the project's last commit time, else 1980."""
    plan = replace(_plan(project, source, uv, work, options), package=options.package)
    if SOURCE_DATE_EPOCH in os.environ:
        return plan
    epoch = max(commit_time(project.directory) or 0, EPOCH_FLOOR)
    return replace(plan, env={**plan.env, SOURCE_DATE_EPOCH: str(epoch)})


def _plan(
    project: Project,
    source: LockSource,
    uv: Path,
    work: Path,
    options: StagingOptions,
) -> StagingPlan:
    if source is LockSource.SCRIPT_LOCK:
        return _script_lock_plan(project, uv, work, options)
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
    if source in (LockSource.UNLOCKED, LockSource.INFERRED):
        extras = f"[{','.join(options.extras)}]" if options.extras else ""
        return StagingPlan(site, build, [venv, [*base, WHEEL + extras]], wheel_dir, env,
                           [str(uv), "cache", "clean"])  # fmt: skip
    hashes: list[str] = []
    if source is LockSource.PYLOCK:
        lock = project.directory / LockSource.PYLOCK.value
    elif source in (LockSource.POETRY_LOCK, LockSource.PDM_LOCK, LockSource.PIPFILE_LOCK):
        lock, hashes = _foreign_requirements(project, source, work, options)
    else:
        lock = project.requirements_path(options.requirements)
        hashes = ["--require-hashes"] if options.strict or has_hash_option(lock.read_text()) else []
    # `-e ./libs/common` lines install as regular packages; check_no_editable stays the net
    editable = ["--no-editable"] if source is not LockSource.PYLOCK else []
    install = [venv, [*base, *hashes, *editable, "-r", str(lock)],
               [*base, "--no-deps", WHEEL]]
    return StagingPlan(site, build, install, wheel_dir, env, [str(uv), "cache", "clean"])


def _foreign_requirements(
    project: Project, source: LockSource, work: Path, options: StagingOptions
) -> tuple[Path, list[str]]:
    """The requirements file of a Poetry, PDM or Pipenv lock, written in the work directory, and
    uv's hash option for it: --require-hashes when every line carries a hash."""
    environment = options.environment or marker_environment(f"{options.python}.0")
    exported = (pipenv.export(project.directory, environment=environment)
                if source is LockSource.PIPFILE_LOCK
                else export(source, project.directory, extras=options.extras,
                            environment=environment))  # fmt: skip
    path = work / f"{source.value}.requirements.txt"
    path.write_text(exported.text)
    return path, ["--require-hashes"] * exported.hashed


def _tree_leftovers(project_dir: Path) -> set[Path]:
    """What a setuptools build leaves in the source tree: ``*.egg-info`` beside the project's
    files or in a directory below (``src/``), and ``build/``."""
    found = {*project_dir.glob("*.egg-info"), *project_dir.glob("*/*.egg-info")}
    return {*found, *({project_dir / "build"} if (project_dir / "build").is_dir() else set())}


@contextmanager
def leave_tree_unchanged(project_dir: Path) -> Iterator[None]:
    """Remove what the build in ``project_dir`` writes there (setuptools' egg_info runs in the
    source tree under ``uv build``); what existed before the build is never touched."""
    before = _tree_leftovers(project_dir)
    try:
        yield
    finally:
        for path in _tree_leftovers(project_dir) - before:
            shutil.rmtree(path, ignore_errors=True)


def _step(command: list[str], project_dir: Path) -> str:
    """What a uv command does, for the build's progress lines and its errors."""
    verb = command[1] if len(command) > 1 else ""
    if verb == "build":
        return f"building {project_dir.name}'s wheel"
    if verb == "venv":
        return "creating the staging environment"
    if verb == "cache":
        return "dropping uv's cached builds"
    if verb == "sync" or "-r" in command:
        return "installing the locked distributions"
    if "--no-deps" in command:
        return "installing the project's wheel"
    return "installing the project's wheel and its dependencies"


def _run(
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    *,
    log: Callable[[str], None],
    verbose: bool,
) -> None:
    """Run one uv command after logging its step. ``verbose`` prints the command on stderr and
    lets uv's stderr through (uv's installs are ``--quiet``: its errors and hints; its build is
    not: setuptools' own output too). A failure carries the tail of that output."""
    step = _step(command, cwd)
    log(f"uv: {step}")
    if verbose:
        print(f"fsbpyr: {shlex.join(command)}", file=sys.stderr, flush=True)
    result = subprocess.run(
        command, cwd=cwd, env={**os.environ, **env}, stdout=subprocess.PIPE,
        stderr=None if verbose else subprocess.PIPE, text=True, check=False
    )  # fmt: skip
    if result.returncode == 0:
        return
    if verbose:
        raise StagingError(f"{step}: uv failed (exit {result.returncode}), see its output above")
    output = tail_lines(result.stderr or result.stdout, _ERROR_TAIL)
    raise StagingError(f"{step}: uv failed (exit {result.returncode}):\n{output}")


def _write_loose_wheel(
    project_dir: Path,
    wheel_dir: Path,
    env: dict[str, str],
    log: Callable[[str], None],
    package: Package | None = None,
) -> Path:
    """The wheel of a project without a build system (``package``, else read now), stamped
    SOURCE_DATE_EPOCH (``env``, else the environment's; never before 1980)."""
    log(f"writing {project_dir.name}'s wheel")
    text = env.get(SOURCE_DATE_EPOCH, os.environ.get(SOURCE_DATE_EPOCH, ""))
    if not text.strip().isdecimal():
        raise StagingError(f"{SOURCE_DATE_EPOCH}={text!r} is not a number of seconds")
    package = package or Package.load(project_dir)
    return package.write_wheel(wheel_dir, max(int(text), EPOCH_FLOOR))


def _build_wheel(  # noqa: PLR0913  the plan, where, and the progress keywords
    plan: StagingPlan,
    project_dir: Path,
    wheel_dir: Path,
    env: dict[str, str],
    *,
    log: Callable[[str], None],
    verbose: bool,
) -> Path:
    """The plan's build of the project wheel, into ``wheel_dir`` with ``env`` added."""
    wheel_dir.mkdir(parents=True, exist_ok=True)
    if not plan.build:
        return _write_loose_wheel(project_dir, wheel_dir, {**plan.env, **env}, log, plan.package)
    build = [str(wheel_dir) if part == str(plan.wheel_dir) else part for part in plan.build]
    with leave_tree_unchanged(project_dir):
        _run(build, project_dir, {**plan.env, **env}, log=log, verbose=verbose)
    wheels = sorted(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise StagingError(f"expected one built wheel in {wheel_dir}, found {len(wheels)}")
    return wheels[0]


def rebuild_wheel(
    plan: StagingPlan,
    project_dir: Path,
    env: dict[str, str],
    *,
    log: Callable[[str], None] = lambda _: None,
    verbose: bool = False,
) -> Path:
    """Build the project wheel again with ``env`` added, into a new directory beside the first
    one's; returns the new wheel."""
    wheel_dir = tempfile.mkdtemp(prefix=f"{plan.wheel_dir.name}-", dir=plan.wheel_dir.parent)
    return _build_wheel(plan, project_dir, Path(wheel_dir), env, log=log, verbose=verbose)


def rebuild(  # noqa: PLR0913  the plan, what to rebuild and the progress keywords
    plan: StagingPlan,
    project_dir: Path,
    wheel: Path,
    names: list[str],
    env: dict[str, str],
    *,
    log: Callable[[str], None] = lambda _: None,
    verbose: bool = False,
) -> None:
    """Install ``names`` again, built anew from their sdists with ``env`` added: the plan's
    installs repeated, the environment not created again. uv reuses a wheel it built before,
    whatever the flags (``--refresh-package`` only revalidates the index), so its cached builds
    of ``names`` go first; the new builds take their place. uv builds before it installs, so a
    failed build leaves the staged site as it was."""
    if plan.forget:
        _run([*plan.forget, *names], project_dir, {**plan.env, **env}, log=log, verbose=verbose)
    again = [arg for name in names for arg in ("--reinstall-package", name)]
    for command in plan.install:
        if command[1:2] == ["venv"]:
            continue
        _run([part.replace(WHEEL, str(wheel)) for part in command] + again, project_dir,
             {**plan.env, **env}, log=log, verbose=verbose)  # fmt: skip


def run_plan(
    plan: StagingPlan,
    project_dir: Path,
    *,
    log: Callable[[str], None] = lambda _: None,
    verbose: bool = False,
) -> Path:
    """Build the project wheel, install dependencies and the wheel; returns the wheel."""
    wheel = _build_wheel(plan, project_dir, plan.wheel_dir, {}, log=log, verbose=verbose)
    for command in plan.install:
        _run([part.replace(WHEEL, str(wheel)) for part in command], project_dir, plan.env,
             log=log, verbose=verbose)  # fmt: skip
    return wheel
