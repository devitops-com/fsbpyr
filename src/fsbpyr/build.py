"""``fsbpyr build``: project -> staging install -> classify -> executable (plan §3.6)."""

import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename

from .assemble import App, build_executable
from .classify import classify
from .closure import SiteIndex, resolve_entry
from .config import BuildConfig
from .elf import ElfError, glibc_requirement
from .project import LockSource, Project
from .runtime import pins
from .runtime.registry import Runtime, select_runtime
from .staging import StagingOptions, find_installer, run_plan, staging_plan

TARGET_PYTHON = "3.14"
_SMOKE_TIMEOUT = 120


class BuildError(RuntimeError):
    """The executable could not be produced or failed its smoke test."""


@dataclass(frozen=True, slots=True)
class BuildResult:
    output: Path
    app_name: str
    entrypoint: str
    size: int
    warnings: tuple[str, ...]
    glibc: str | None = None  # the oldest glibc the app runs on, "2.25"


def _check_entry_module(entrypoint: str, app: App, runtime: Runtime) -> None:
    module = entrypoint.partition(":")[0]
    known = {m.name for m in (*app.modules, *runtime.modules)}
    if module not in known:
        raise BuildError(f"entry point module {module!r} is not part of the application")


def _version(value: tuple[int, int]) -> str:
    return f"{value[0]}.{value[1]}"


def glibc_report(
    stub: bytes, natives: tuple[tuple[int, int], str] | None, *, baseline: tuple[int, int]
) -> tuple[str | None, list[str]]:
    """The app's glibc requirement (the stub's, raised by bundled native code) and a warning
    when a native file needs more than the baseline its wheels were chosen for."""
    try:
        own = glibc_requirement(stub)
    except ElfError:
        own = None
    needs = [own] if own is not None else []
    warnings = []
    if natives is not None:
        needs.append(natives[0])
        if natives[0] > max(own or baseline, baseline):
            warnings.append(
                f"{natives[1]} needs glibc {_version(natives[0])}, more than the "
                f"{_version(baseline)} baseline; on older systems it fails when imported"
            )
    return (_version(max(needs)) if needs else None), warnings


def check_output(output: Path) -> None:
    if output.is_dir():
        raise BuildError(f"output path {output} is a directory; pass -o with a file name")


def smoke_test(executable: Path, app_name: str) -> None:
    """Run the executable in selftest mode: boot, import the entry module, exit."""
    env = {**os.environ, "FSBPYR_SELFTEST": "1"}
    try:
        result = subprocess.run(  # noqa: S603  the executable we just wrote
            [executable],
            env=env,
            capture_output=True,
            text=True,
            timeout=_SMOKE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"smoke test of {executable} failed: {exc}") from exc
    if result.returncode != 0 or f"fsbpyr selftest ok: {app_name}" not in result.stdout:
        raise BuildError(
            f"smoke test of {executable} failed (exit {result.returncode}):\n"
            f"{result.stderr[-3000:]}"
        )


def build(
    directory: Path, config: BuildConfig, *, log: Callable[[str], None] = lambda _: None
) -> BuildResult:
    project = Project.load(directory)
    runtime = select_runtime(TARGET_PYTHON)
    project.check_python(runtime.python_version)
    warnings: list[str] = []
    if pin := project.python_version_mismatch(TARGET_PYTHON):
        warnings.append(f".python-version pins {pin}; building for Python {runtime.python_version}")
    source = project.lock_source(config.requirements)
    if source is LockSource.UNLOCKED:
        warnings.append("unlocked build: dependencies resolved now, not reproducible")
    installer = find_installer(source, TARGET_PYTHON, project.directory, config.interpreter)
    log(f"staging {project.name or project.directory.name} from {source.value}")
    with tempfile.TemporaryDirectory(prefix="fsbpyr-stage-") as tmp:
        baseline = pins.sysroot_for(runtime.tag.partition("-")[2])  # the stub's glibc (D25)
        options = StagingOptions(TARGET_PYTHON, config.extras, config.offline, config.requirements,
                                 platform=baseline.platform)  # fmt: skip
        plan = staging_plan(project, source, installer, Path(tmp), options)
        wheel = run_plan(plan, project.directory)
        index = SiteIndex.scan(plan.site)
        index.check_no_editable()
        name = canonicalize_name(parse_wheel_filename(wheel.name)[0])
        dist = index.project(name=name, directory=project.directory)
        walk = index.walk(dist, extras=config.extras, python_version=runtime.python_version)
        app_name, entrypoint = resolve_entry(dist, config.entry)
        log(f"collecting {len(index.dists)} distributions")
        content = classify(
            list(index.dists.values()),
            extension_suffixes=runtime.extension_suffixes,
            include_source=config.include_source,
            allowed_system_libs=frozenset(config.allowed_system_libs),
            fs_packages=frozenset(config.fs_packages),
        )
    warnings += walk.warnings
    if walk.unreachable:
        warnings.append(
            f"packaged although not required by {dist.name}: {', '.join(walk.unreachable)} "
            "(they come from the lock or requirements file)"
        )
    warnings += content.warnings
    glibc, glibc_warnings = glibc_report(runtime.stub, content.glibc, baseline=baseline.baseline)
    warnings += glibc_warnings
    app = App(
        app_name,
        entrypoint,
        modules=content.modules,
        files=content.files,
        dists=content.dists,
        build_info=(
            ("lock", source.value),
            ("dists", sorted(f"{d.name}=={d.version}" for d in content.dists)),
            ("glibc", glibc),
        ),
    )
    _check_entry_module(entrypoint, app, runtime)
    output = (config.output or project.directory / "dist" / app_name).resolve()
    check_output(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    log(f"writing {output}")
    build_executable(runtime, app, output)
    if config.smoke:
        smoke_test(output, app_name)
    return BuildResult(output, app_name, entrypoint, output.stat().st_size, tuple(warnings), glibc)
