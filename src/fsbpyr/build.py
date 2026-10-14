"""``fsbpyr build``: project -> staging install -> classify -> executable (plan §3.6)."""

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename

from .assemble import App, build_executable, runtime_module_names
from .classify import _record_path, classify
from .closure import InstalledDist, SiteIndex, resolve_entry
from .config import BuildConfig
from .elf import ElfError, glibc_requirement, is_elf
from .natives import opened
from .payload.format import NATIVE_KINDS
from .payload.spec import XZ_PRESET, File
from .payload.spill import Spill
from .project import LockSource, Project
from .runtime import pins
from .runtime.pbs import PbsError
from .runtime.registry import Runtime, cache_root, select_runtime
from .runtime.sysroot import SysrootError, build_flags, fetch_sysroot
from .staging import (
    StagingError,
    StagingOptions,
    StagingPlan,
    find_uv,
    rebuild,
    run_plan,
    staging_plan,
    uv_version,
)
from .vcs import git_state, own_version

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


def _glibc_needs(dist: InstalledDist) -> Iterable[tuple[int, int]]:
    for rel in map(_record_path, dist.record):
        path = dist.site / rel
        if ".so" not in path.name or not path.is_file():
            continue
        with opened(path) as data:
            if not is_elf(data[:4]):
                continue
            try:
                need = glibc_requirement(data)
            except ElfError:
                continue
        if need is not None:
            yield need


def built_here_above(dists: Iterable[InstalledDist], baseline: tuple[int, int]) -> list[str]:
    """Dependencies built from their sdists on the build host (wheel tags ``linux_*``) whose
    native files need a newer glibc than ``baseline``: they were compiled against the host's
    glibc. A wheel from the index got its glibc from its tag (``--python-platform``)."""
    found = []
    for dist in dists:
        wheel = dist.site / dist.dist_info / "WHEEL"
        lines = wheel.read_text(errors="replace").splitlines() if wheel.is_file() else []
        tags = [line.partition(":")[2].strip() for line in lines if line.startswith("Tag:")]
        if not tags or not all(tag.rpartition("-")[2].startswith("linux_") for tag in tags):
            continue
        if any(need > baseline for need in _glibc_needs(dist)):
            found.append(dist.name)
    return sorted(found)


def rebuild_on_baseline(  # noqa: PLR0913  the staging, what to rebuild, and offline keyword-only
    plan: StagingPlan,
    project_dir: Path,
    wheel: Path,
    names: list[str],
    baseline: pins.SysrootPin,
    *,
    offline: bool,
) -> list[str]:
    """Build ``names`` again from their sdists against the glibc baseline's sysroot (D25), with
    the build host's compiler; the warnings, if they keep the host's builds. Not offline: the
    rebuild drops uv's cached builds and downloads the sdists again."""
    listed, glibc = ", ".join(names), baseline.glibc
    if offline:
        return [f"{listed}: not rebuilt against the glibc {glibc} sysroot: the build is offline"]
    cache = cache_root()
    try:
        root = fetch_sysroot(baseline, cache)
    except (SysrootError, PbsError) as exc:
        return [f"{listed}: not rebuilt against the glibc {glibc} sysroot: {exc}"]
    try:
        rebuild(plan, project_dir, wheel, names, build_flags(root, os.environ))
    except StagingError as exc:
        reason = next((line for line in reversed(str(exc).splitlines()) if line.strip()), "")
        return [f"{listed}: building from source against the glibc {glibc} sysroot failed, the "
                f"build host's builds are packaged: {reason.strip()}"]  # fmt: skip
    return []


def builder_uv() -> str | None:
    """The uv version the build of this fsbpyr executable recorded; None from source, or for
    an executable built before uv versions were recorded."""
    try:
        import _fsbpyr  # noqa: PLC0415  # only in executables
    except ImportError:
        return None
    version = json.loads(_fsbpyr.info()["build_info"]).get("uv")
    return version if isinstance(version, str) else None


def uv_mismatch(*, builder: str | None, current: str) -> list[str]:
    """A warning when uv is not the one this fsbpyr was built with: uv builds the project's
    wheel (its uv_build stamps uv's version into the wheel's metadata) and installs every
    distribution, so another uv can give other bytes from identical sources."""
    if builder is None or builder == current:
        return []
    return [f"uv {current} is not uv {builder}, which built this fsbpyr: the executable can "
            f"differ from one built with uv {builder}, even from identical sources"]  # fmt: skip


def best_compression_unused(*, best_compression: bool, files: Iterable[File]) -> list[str]:
    """A warning when --best-compression (or best-compression = true) has nothing to do: it
    compresses native files only (D33), and a pure-Python app has none."""
    if not best_compression or any(file.kind in NATIVE_KINDS for file in files):
        return []
    return ["best-compression has nothing to compress: no extension modules or shared "
            "libraries were packaged, and it compresses nothing else"]  # fmt: skip


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
    uv = find_uv()  # before anything slow: a build cannot go far without it (D26)
    uv_used = uv_version(uv)
    runtime = select_runtime(TARGET_PYTHON)
    project.check_python(runtime.python_version)
    warnings = uv_mismatch(builder=builder_uv(), current=uv_used)
    if pin := project.python_version_mismatch(TARGET_PYTHON):
        warnings.append(f".python-version pins {pin}; building for Python {runtime.python_version}")
    source = project.lock_source(config.requirements)
    if source is LockSource.UNLOCKED:
        warnings.append("unlocked build: dependencies resolved now, not reproducible")
    log(f"staging {project.name or project.directory.name} from {source.value}")
    # the staged files and the spill live until the executable is written: files too large to
    # hold are streamed from them (D38)
    with tempfile.TemporaryDirectory(prefix="fsbpyr-stage-") as tmp:
        baseline = pins.sysroot_for(runtime.tag.partition("-")[2])  # the stub's glibc (D25)
        options = StagingOptions(TARGET_PYTHON, config.extras, config.offline, config.requirements,
                                 platform=baseline.platform)  # fmt: skip
        plan = staging_plan(project, source, uv, Path(tmp), options)
        wheel = run_plan(plan, project.directory)
        index = SiteIndex.scan(plan.site)
        index.check_no_editable()
        if stale := built_here_above(index.dists.values(), baseline.baseline):
            log(f"rebuilding {', '.join(stale)} against the glibc {baseline.glibc} sysroot")
            failed = rebuild_on_baseline(plan, project.directory, wheel, stale, baseline,
                                         offline=config.offline)  # fmt: skip
            warnings += failed
            if not failed:
                index = SiteIndex.scan(plan.site)
        name = canonicalize_name(parse_wheel_filename(wheel.name)[0])
        dist = index.project(name=name, directory=project.directory)
        walk = index.walk(dist, extras=config.extras, python_version=runtime.python_version)
        app_name, entrypoint = resolve_entry(dist, config.entry)
        output = (config.output or project.directory / "dist" / app_name).resolve()
        check_output(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        log(f"collecting {len(index.dists)} distributions")
        level = config.zstd_level
        with Spill(output.parent) as spill:
            content = classify(
                list(index.dists.values()),
                extension_suffixes=runtime.extension_suffixes,
                include_source=config.include_source,
                allowed_system_libs=frozenset(config.allowed_system_libs),
                fs_packages=frozenset(config.fs_packages),
                hints=config.hints,
                level=level,
                xz_natives=config.best_compression,
                spill=spill,
                runtime_modules=runtime_module_names(runtime),
                project=dist.name,
            )
            warnings += walk.warnings
            if walk.unreachable:
                warnings.append(
                    f"packaged although not required by {dist.name}: "
                    f"{', '.join(walk.unreachable)} (they come from the lock or requirements file)"
                )
            warnings += content.warnings
            warnings += best_compression_unused(best_compression=config.best_compression,
                                                files=content.files)  # fmt: skip
            glibc, glibc_warnings = glibc_report(runtime.stub, content.glibc,
                                                 baseline=baseline.baseline)  # fmt: skip
            warnings += glibc_warnings
            natives = f"xz -{XZ_PRESET}" if config.best_compression else "zstd"
            app = App(
                app_name,
                entrypoint,
                modules=content.modules,
                files=content.files,
                dists=content.dists,
                build_info=(
                    ("project", {"name": dist.name, "version": dist.version}),
                    ("fsbpyr_version", own_version()),  # with the commit (fsbpyr inspect)
                    ("uv", uv_used),  # builds the wheel, installs everything
                    ("lock", source.value),
                    ("dists", sorted(f"{d.name}=={d.version}" for d in content.dists)),
                    ("glibc", glibc),
                    ("zstd_level", level),  # of the app's content and of every block
                    ("native_compression", natives),
                    ("interpreter_reexec", config.interpreter_reexec),  # read at start (D23)
                    ("git", git_state(project.directory, warn=warnings.append)),
                ),
                zstd_level=level,
            )
            _check_entry_module(entrypoint, app, runtime)
            log(f"writing {output}")
            build_executable(runtime, app, output)
    if config.smoke:
        smoke_test(output, app_name)
    return BuildResult(output, app_name, entrypoint, output.stat().st_size, tuple(warnings), glibc)
