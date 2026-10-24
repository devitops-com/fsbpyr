"""``fsbpyr build``: project -> staging install -> classify -> executable (plan §3.6)."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename

from . import pyversion
from .assemble import App, build_temporary, runtime_module_names
from .classify import BUILTIN_FS_PACKAGES, Uncounted, _record_path, classify
from .closure import (
    ClosureError,
    InstalledDist,
    SiteIndex,
    resolve_entry,
    several_scripts_error,
    unknown_extras_error,
)
from .config import BuildConfig
from .elf import ElfError, glibc_requirement, is_elf
from .manifest import SCHEMA, LockError, built_here, dist_records, lock_record
from .natives import opened
from .payload.format import NATIVE_KINDS, ModuleKind
from .payload.spec import XZ_PRESET, Dist, File, Module
from .payload.spill import Spill
from .payload.writer import commit
from .project import LockSource, Project, ProjectError, unpinned_requirements
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
    rebuild_wheel,
    run_plan,
    staging_plan,
    uv_version,
)
from .system import system_info, system_report, target_needs
from .targets import target_named
from .vcs import git_state, own_version, release_asset_record

TARGET_PYTHON = f"{sys.version_info[0]}.{sys.version_info[1]}"  # builder and apps run alike
_SMOKE_TIMEOUT = 120
_PACKAGES = (ModuleKind.PACKAGE, ModuleKind.NAMESPACE)


class BuildError(RuntimeError):
    """The executable could not be produced or failed its smoke test."""


class StrictError(BuildError):
    """--strict: the build has warnings about the app. The message lists them."""


@dataclass(frozen=True, slots=True)
class BuildResult:
    output: Path
    app_name: str
    entrypoint: str
    size: int
    warnings: tuple[str, ...]
    glibc: str | None = None  # the oldest glibc the app runs on, "2.25"
    target_needs: str | None = None  # "glibc >= 2.34; libstdc++.so.6 (...)"


def _check_entry_module(entrypoint: str, app: App, runtime: Runtime) -> None:
    module, colon, _ = entrypoint.partition(":")
    kinds = {m.name: m.kind for m in (*app.modules, *runtime.modules)}
    if module not in kinds:
        raise BuildError(f"entry point module {module!r} is not part of the application")
    main = f"{module}.__main__"  # what running a package runs, as `python -m` does
    if not colon and kinds[module] in _PACKAGES and main not in kinds:
        raise BuildError(f"entry point module {module!r} is a package without {main!r}")


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
    log: Callable[[str], None] = lambda _: None,
    verbose: bool = False,
) -> list[str]:
    """Build ``names`` again from their sdists against the glibc baseline's sysroot (D25), with
    the build host's compiler, and the project's own wheel when it is among them; the warnings,
    if they keep the host's builds. Not offline: the rebuild drops uv's cached builds and
    downloads the sdists again."""
    listed, glibc = ", ".join(names), baseline.glibc
    if offline:
        return [f"{listed}: not rebuilt against the glibc {glibc} sysroot: the build is offline"]
    cache = cache_root()
    try:
        root = fetch_sysroot(baseline, cache)
    except (SysrootError, PbsError) as exc:
        return [f"{listed}: not rebuilt against the glibc {glibc} sysroot: {exc}"]
    flags = build_flags(root, os.environ)
    try:
        if canonicalize_name(parse_wheel_filename(wheel.name)[0]) in names:
            wheel = rebuild_wheel(plan, project_dir, flags, log=log, verbose=verbose)
        rebuild(plan, project_dir, wheel, names, flags, log=log, verbose=verbose)
    except StagingError as exc:
        reason = next((line for line in reversed(str(exc).splitlines()) if line.strip()), "")
        return [f"{listed}: building from source against the glibc {glibc} sysroot failed, the "
                f"build host's builds are packaged: {reason.strip()}"]  # fmt: skip
    return []


def settings_record(config: BuildConfig) -> dict[str, object]:
    """build_info's ``settings``: what the configuration decided about the content, with the
    packages that must be on disk as they take effect (the built-in ones and the configured)."""
    return {
        "entry": config.entry,
        "fs_packages": sorted(BUILTIN_FS_PACKAGES | set(config.fs_packages)),
        "allow_system_libs": sorted(config.allowed_system_libs),
        "extras": sorted(config.extras),
        "include_source": config.include_source,
    }


def _provenance(  # noqa: PLR0913  what the records are made of, and where to log
    project: Project,
    source: LockSource,
    config: BuildConfig,
    index: SiteIndex,
    packaged: Iterable[Dist],
    *,
    project_dist: str,
    log: Callable[[str], None],
) -> tuple[tuple[str, object], ...]:
    """build_info's schema, ``lock``, ``dists`` and ``settings``. Dists built here from their
    sdists are named in one line of the build's output: information, not a warning, so
    --strict does not fail on it."""
    try:
        lock, locked = lock_record(project.directory, source,
                                   project.lock_path(source, config.requirements))  # fmt: skip
    except LockError as exc:
        raise BuildError(str(exc)) from exc
    dists = dist_records((index.dists[d.name] for d in packaged), locked, project=project_dist)
    if sdists := built_here(dists):
        log("built on this host from sdists, not reproducible elsewhere: " + ", ".join(sdists))
    return (("schema", SCHEMA), ("lock", lock), ("dists", dists),
            ("settings", settings_record(config)))  # fmt: skip


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
    return [Uncounted(f"uv {current} is not uv {builder}, which built this fsbpyr: the "
                      f"executable can differ from one built with uv {builder}, even from "
                      "identical sources")]  # fmt: skip


def best_compression_unused(*, best_compression: bool, files: Iterable[File]) -> list[str]:
    """A warning when --best-compression (or best-compression = true) has nothing to do: it
    compresses native files only (D33), and a pure-Python app has none."""
    if not best_compression or any(file.kind in NATIVE_KINDS for file in files):
        return []
    return ["best-compression has nothing to compress: no extension modules or shared "
            "libraries were packaged, and it compresses nothing else"]  # fmt: skip


# Libraries that start helper interpreters through sys.executable (D44): the module that shows
# one is packaged, its name, and what it starts.
HELPER_INTERPRETERS = (
    ("joblib.externals.loky", "joblib", "workers and a resource tracker for n_jobs"),
    ("loky", "loky", "workers and a resource tracker"),
    ("jupyter_client", "jupyter_client", "kernels"),
    ("cpuinfo", "py-cpuinfo", "its cpuinfo.py, for the CPU's details"),
    ("multiprocess", "multiprocess", "children with the spawn and forkserver start methods"),
    ("torch._inductor.compile_worker", "torch", "compile workers for torch.compile"),
)


def helper_interpreter_hints(modules: Iterable[Module], reexec: bool | None) -> list[str]:
    """A note on the packaged libraries that start helper interpreters, unless the build runs
    them (D23): without the setting they are refused, with ``false`` they run the app."""
    if reexec is True:
        return []
    names = {module.name for module in modules}
    found = [f"{who} ({what})" for module, who, what in HELPER_INTERPRETERS if module in names]
    if not found:
        return []
    outcome = ("they run the app (--no-interpreter-reexec)" if reexec is False else
               "they are refused (exit 70) unless the app is built with --interpreter-reexec")
    return [Uncounted(f"these may start helper interpreters through sys.executable: "
                      f"{', '.join(found)}; {outcome}")]  # fmt: skip


def unpinned_warning(unpinned: list[str]) -> list[str]:
    """A warning for requirements without ``==``: uv resolves them now, so the build is not
    reproducible, whatever the file is called."""
    if not unpinned:
        return []
    shown = ", ".join(unpinned[:3]) + (" ..." if len(unpinned) > 3 else "")
    return [f"requirements not pinned with ==, so resolved now and not reproducible: {shown}"]


def lock_warnings(project: Project, source: LockSource, requirements: str | None) -> list[str]:
    """What is not reproducible about the lock source: no lock at all, or unpinned lines."""
    if source is LockSource.UNLOCKED:
        return ["unlocked build: dependencies resolved now, not reproducible"]
    if source is LockSource.REQUIREMENTS:
        return unpinned_warning(unpinned_requirements(project.requirements_path(requirements)))
    return []


def check_strict(warnings: Iterable[str]) -> None:
    """Raise StrictError for the warnings that count. The uv mismatch and git errors describe
    the build host, and the ctypes guess is silenced only by fs-packages, which changes how
    the app runs: none of them counts (they are Uncounted)."""
    counted = [warning for warning in warnings if not isinstance(warning, Uncounted)]
    if counted:
        raise StrictError(f"--strict: the build has {len(counted)} warning"
                          f"{'s' * (len(counted) > 1)}; nothing was written:\n  "
                          + "\n  ".join(counted))


def check_output(output: Path) -> None:
    if output.is_dir():
        raise BuildError(f"output path {output} is a directory; pass -o with a file name")


def _installed_project(
    index: SiteIndex, wheel: Path, project: Project, config: BuildConfig
) -> InstalledDist:
    """The project as installed in the staging site; its extras are checked here when the
    pyproject could not say them before staging."""
    name = canonicalize_name(parse_wheel_filename(wheel.name)[0])
    dist = index.project(name=name, directory=project.directory)
    if not project.static_extras and (
        message := unknown_extras_error(dist.name, config.extras, dist.provides_extra)
    ):
        raise ClosureError(message)
    return dist


def check_before_staging(project: Project, config: BuildConfig) -> None:
    """What the project and the options already decide, before uv stages anything. Dynamic
    scripts and extras (and setup.py projects) are decided after staging."""
    name = project.name or project.directory.name
    if config.entry is None and project.static_scripts and len(project.scripts) > 1:
        raise several_scripts_error(name, project.scripts)
    if config.output is not None:
        check_output(config.output)
    if project.static_extras and (
        message := unknown_extras_error(name, config.extras, project.extras)
    ):
        raise ProjectError(message)


def smoke_test(executable: Path, app_name: str, output: Path | None = None) -> None:
    """Run the executable in selftest mode: boot, import the entry module, exit. An error names
    ``output``, the path the executable is to replace, when it is given."""
    shown = executable if output is None else f"the new {output}"
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
        raise BuildError(f"smoke test of {shown} failed: {exc}") from exc
    if result.returncode != 0 or f"fsbpyr selftest ok: {app_name}" not in result.stdout:
        raise BuildError(
            f"smoke test of {shown} failed (exit {result.returncode}):\n{result.stderr[-3000:]}"
        )


def smoke_run(executable: Path, output: Path, args: tuple[str, ...]) -> None:
    """Run the new executable with ``args`` as the app, as ``output``, and require exit 0. Only
    the user knows which arguments are safe, so this runs when smoke-args is set."""
    shown = shlex.join(args)
    try:
        result = subprocess.run(  # noqa: S603  the executable we just wrote
            [str(output), *args],
            executable=executable,  # argv[0] is the final path; the payload is /proc/self/exe
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_SMOKE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"smoke run of the new {output} with arguments {shown!r} failed: {exc}"
                         ) from exc
    if result.returncode != 0:
        raise BuildError(f"smoke run of the new {output} with arguments {shown!r} failed "
                         f"(exit {result.returncode}):\n{result.stderr[-3000:]}")


def build(
    directory: Path,
    config: BuildConfig,
    *,
    log: Callable[[str], None] = lambda _: None,
    verbose: bool = False,
) -> BuildResult:
    """Build the executable beside the output; it replaces the output only once it passed the
    smoke test (--no-smoke skips it). An error carries the warnings collected before it as
    notes, which cli.py prints. ``verbose`` shows uv's commands and lets its errors through."""
    warnings: list[str] = []
    written: list[Path] = []  # the new executable beside the output, until it is committed
    try:
        result = _build(directory, config, log, warnings, written, verbose=verbose)
        if config.smoke:
            log(f"smoke-testing {written[0]}")
            smoke_test(written[0], result.app_name, result.output)
            if config.smoke_args:
                log(f"running the new executable with {shlex.join(config.smoke_args)}")
                smoke_run(written[0], result.output, config.smoke_args)
        commit(written[0], result.output)
        written.clear()
        return result
    except Exception as exc:
        if not isinstance(exc, StrictError):  # its message lists them
            for warning in warnings:
                exc.add_note(warning)
        raise
    finally:
        for path in written:  # the build failed or was interrupted
            path.unlink(missing_ok=True)


def python_warnings(project: Project, config: BuildConfig) -> tuple[str, ...]:
    """What the version rule says against the project's files; an error when the project
    builds with another Python than this fsbpyr's (the CLI hands such a build to the matching
    fsbpyr, so only a direct call gets here)."""
    chosen = pyversion.resolve(project, config.python, own=TARGET_PYTHON)
    if chosen.version != TARGET_PYTHON:
        raise BuildError(f"this project builds with Python {chosen.version}, not this fsbpyr's "
                         f"{TARGET_PYTHON}")  # fmt: skip
    return chosen.warnings


def _build(  # noqa: PLR0913  the build's state and the progress keyword
    directory: Path,
    config: BuildConfig,
    log: Callable[[str], None],
    warnings: list[str],
    written: list[Path],
    *,
    verbose: bool,
) -> BuildResult:
    """Everything up to the new executable, written beside the output: ``written`` holds it as
    soon as it exists."""
    project = Project.load(directory)
    check_before_staging(project, config)
    uv = find_uv()  # before anything slow: a build cannot go far without it (D26)
    uv_used = uv_version(uv)
    warnings += python_warnings(project, config)
    runtime = select_runtime(TARGET_PYTHON)
    project.check_python(runtime.python_version)
    warnings += uv_mismatch(builder=builder_uv(), current=uv_used)
    source = project.lock_source(config.requirements)
    warnings += lock_warnings(project, source, config.requirements)
    log(f"staging {project.name or project.directory.name} from {source.value}")
    # the staged files and the spill live until the executable is written: files too large to
    # hold are streamed from them (D38)
    with tempfile.TemporaryDirectory(prefix="fsbpyr-stage-") as tmp:
        triple = runtime.tag.partition("-")[2]
        baseline = pins.sysroot_for(triple)  # the stub's glibc (D25)
        options = StagingOptions(TARGET_PYTHON, config.extras, config.offline, config.requirements,
                                 platform=target_named(triple).uv_platform,
                                 strict=config.strict)  # fmt: skip
        plan = staging_plan(project, source, uv, Path(tmp), options)
        wheel = run_plan(plan, project.directory, log=log, verbose=verbose)
        index = SiteIndex.scan(plan.site)
        index.check_no_editable()
        if stale := built_here_above(index.dists.values(), baseline.baseline):
            log(f"rebuilding {', '.join(stale)} against the glibc {baseline.glibc} sysroot")
            failed = rebuild_on_baseline(plan, project.directory, wheel, stale, baseline,
                                         offline=config.offline, log=log,
                                         verbose=verbose)  # fmt: skip
            warnings += failed
            if not failed:
                index = SiteIndex.scan(plan.site)
        dist = _installed_project(index, wheel, project, config)
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
            warnings += [*content.warnings, *best_compression_unused(
                best_compression=config.best_compression, files=content.files),
                *helper_interpreter_hints(content.modules, config.interpreter_reexec)]  # fmt: skip
            glibc, glibc_warnings = glibc_report(runtime.stub, content.glibc,
                                                 baseline=baseline.baseline)  # fmt: skip
            libraries, system_warnings = system_report(runtime.stub, content.system)
            warnings += [*glibc_warnings, *system_warnings]
            app = App(
                app_name,
                entrypoint,
                modules=content.modules,
                files=content.files,
                dists=content.dists,
                build_info=(
                    *_provenance(
                        project,
                        source,
                        config,
                        index,
                        content.dists,
                        project_dist=dist.name,
                        log=log,
                    ),
                    ("project", {"name": dist.name, "version": dist.version}),
                    ("fsbpyr_version", own_version()),  # with the commit (fsbpyr inspect)
                    ("uv", uv_used),  # builds the wheel, installs everything
                    ("glibc", glibc),
                    ("system", system_info(libraries, content.system)),  # target needs, guesses
                    ("zstd_level", level),  # of the app's content and of every block
                    (
                        "native_compression",
                        f"xz -{XZ_PRESET}" if config.best_compression else "zstd",
                    ),
                    # read at start: true runs helper interpreters (D23), false runs the
                    # app in them, null refuses them (D44)
                    ("interpreter_reexec", config.interpreter_reexec),
                    ("git", git_state(project.directory,
                                      warn=lambda note: warnings.append(Uncounted(note)))),
                    # fsbpyr's own, self update
                    *release_asset_record(dist.name, TARGET_PYTHON, target_named(triple).name),
                ),
                zstd_level=level,
            )
            _check_entry_module(entrypoint, app, runtime)
            if config.strict:
                check_strict(warnings)
            log(f"writing {output}")
            written.append(build_temporary(runtime, app, output))
    size = written[0].stat().st_size
    return BuildResult(
        output, app_name, entrypoint, size, tuple(warnings), glibc, target_needs(libraries, glibc)
    )
