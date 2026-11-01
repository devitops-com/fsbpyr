"""Every test: no cache collection unless a test asks for one (D38): the tool's daily check and
the launcher's background runs would otherwise act on the caches tests use, or the user's.

Nothing in this suite skips (tests/support/requires.py): a test whose tool or environment is
missing fails with one line that says what to install or change. A test marked
``requires_tools`` fails before it runs when a tool is not on PATH; one marked ``needs_wheel``
(tests/support/wheels.py) is deselected where no-wheels.txt lists no wheel for its package. A
skip or xfail by whatever means (a plugin, an empty parameter set) fails the run at the end.

At the end, the builds of local projects the run made leave uv's cache: the tests build their
fixture projects (and fsbpyr) from new temporary copies every time, and uv keeps every build.
What it downloaded from an index stays."""

import functools
import inspect
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import settings

from fsbpyr.project import Project
from fsbpyr.runtime import pins
from tests.support import requires, wheels

# The glibc baseline the tests build for: the EL8 test mode when FSBPYR_TEST_GLIBC=2.28 (read
# again by tests/support/baseline.py; not imported, since tests/unit/test_requires.py runs this
# file outside the package)
TEST_GLIBC = os.environ.get("FSBPYR_TEST_GLIBC") or pins.DEFAULT_BASELINE
EL8_MODE = TEST_GLIBC != pins.DEFAULT_BASELINE
# The corpus projects (TST-3) carry test files of their own, which their built apps run
collect_ignore_glob = ["corpus/*"]
# Hypothesis in CI (GitHub sets CI): the same examples every run, so a failure there repeats
# and a red run is never a new example's luck. The default profile stays random locally. Neither
# has a per-example deadline: a time limit fails a slow or loaded machine, not a wrong example.
settings.register_profile("default", deadline=None)
settings.load_profile("default")
settings.register_profile("ci", derandomize=True, deadline=None)
if os.environ.get("CI"):
    settings.load_profile("ci")
_skips: list[tuple[str, str]] = []  # this run's, as (nodeid, reason)
_STARTED = time.time()


@pytest.fixture(scope="session", autouse=True)
def _no_collection() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("FSBPYR_GC", "0")
        yield


@pytest.fixture(scope="session", autouse=True)
def _no_signing_setup(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """A developer's exported key must not sign the suite's builds, and the tests of `key trust`
    must not write into their ~/.config, or read what they trust."""
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("FSBPYR_SIGNING_KEY", raising=False)
        patch.delenv("FSBPYR_SIGNING_PASSPHRASE", raising=False)
        patch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("config")))
        yield


@pytest.fixture(scope="session", autouse=True)
def _build_for_own_python() -> Iterator[None]:
    """A build in a test is for the Python the tests run on, as `--python` would say: the
    fixture projects' `requires-python = ">=3.12"` would hand them to the 3.12 variant
    (Phase 9). Session-wide, so that the builds of module-scoped fixtures follow it too."""
    from fsbpyr import pyversion  # noqa: PLC0415

    resolve = pyversion.resolve

    @functools.wraps(resolve)
    def own_python(project: Project, requested: str | None, *, own: str) -> pyversion.Choice:
        return resolve(project, requested or own, own=own)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pyversion, "resolve", own_python)
        yield


@pytest.fixture(scope="session", autouse=True)
def _build_for_baseline() -> Iterator[None]:
    """In the EL8 test mode (FSBPYR_TEST_GLIBC=2.28, tests/support/baseline.py) a build in a
    test is `--glibc 2.28` unless it says otherwise: the project's own setting and the default
    give way, so that the tests that build and run executables run the glibc 2.28 stub."""
    if not EL8_MODE:
        yield
        return
    from fsbpyr import config  # noqa: PLC0415

    load_config = config.load_config

    @functools.wraps(load_config)
    def for_the_baseline(project: Project, **given: object) -> config.BuildConfig:
        return load_config(project, **{**given, "glibc": given.get("glibc") or TEST_GLIBC})

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(config, "load_config", for_the_baseline)
        yield


@pytest.fixture(autouse=True)
def _version_rule(request: pytest.FixtureRequest) -> Iterator[None]:
    """A test marked `version_rule` runs the rule itself."""
    if not request.node.get_closest_marker("version_rule"):
        yield
        return
    from fsbpyr import pyversion  # noqa: PLC0415

    # not the monkeypatch fixture: using it would change the order other fixtures end in
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pyversion, "resolve", inspect.unwrap(pyversion.resolve))
        yield


def pytest_configure(config: pytest.Config) -> None:
    if TEST_GLIBC not in pins.BASELINES:
        raise pytest.UsageError(f"FSBPYR_TEST_GLIBC={TEST_GLIBC}: not a glibc baseline "
                                f"({', '.join(pins.BASELINES)})")  # fmt: skip
    kept = os.environ.get("FSBPYR_TEST_DOWNLOADS")
    if kept and getattr(config.option, "numprocesses", None) and not hasattr(config, "workerinput"):
        _prepare_for_workers(Path(kept))


def _prepare_for_workers(kept: Path) -> None:
    """pytest-xdist: the runtime's downloads and both runtimes once, here, before the workers
    start, each of which would otherwise fetch and build them for its own cache (the glibc 2.34
    release and sanitized runtimes, and the glibc 2.28 one of the EL8 tests; in the EL8 test mode
    the glibc 2.28 one only: the tests run no other)."""
    from fsbpyr.build import TARGET_PYTHON  # noqa: PLC0415
    from fsbpyr.runtime.toolchain import ToolchainError  # noqa: PLC0415

    from .support import downloads  # noqa: PLC0415

    downloads.fill(TARGET_PYTHON, kept)
    try:
        runtimes = ((False, "2.28"),) if EL8_MODE else (
            (False, "2.34"), (True, "2.34"), (False, "2.28"))  # fmt: skip
        for sanitize, glibc in runtimes:
            downloads.build_kept_runtime(TARGET_PYTHON, kept, sanitize=sanitize, baseline=glibc)
    except ToolchainError as exc:
        raise pytest.UsageError(f"missing tool: {exc}; {requires.FIX}") from exc


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """A test marked ``requires_tools`` fails here, before its fixtures build anything, when one
    of the tools is not on PATH."""
    for marker in item.iter_markers(requires.MARKER):
        for name in marker.args:
            requires.tool(name)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """A test that requires podman is marked ``podman``, so that ``-m "not podman"`` leaves it
    out where the environment has none. The tests marked ``needs_wheel`` whose package has no
    wheel for their Python (no-wheels.txt) are deselected: they are not part of the run on that
    Python, and the summary counts them as deselected."""
    from fsbpyr.build import TARGET_PYTHON  # noqa: PLC0415

    for item in items:
        if any("podman" in marker.args for marker in item.iter_markers(requires.MARKER)):
            item.add_marker(pytest.mark.podman)
    dropped = [item for item in items
               if any(wheels.missing(marker.args[0], marker.kwargs.get("python") or TARGET_PYTHON)
                      for marker in item.iter_markers("needs_wheel"))]  # fmt: skip
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = [item for item in items if item not in dropped]


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if not report.skipped:
        return
    if isinstance(report.longrepr, tuple):  # (path, line, "Skipped: <reason>")
        reason = report.longrepr[2].removeprefix("Skipped: ")
    else:  # an xfail: the report carries the failure it expected
        reason = f"xfail: {getattr(report, 'wasxfail', '')}"
    _skips.append((report.nodeid, reason))


def _prune_local_builds() -> str | None:
    """Removes the builds of local projects uv cached since the run started (its path and
    editable sources, and wheels installed by URL: the tests' are files of theirs), with the
    unpacked wheels they link to. Not `uv cache prune`: that waits for the cache lock, which
    the `uv run` around the tests holds. The problem, if any."""
    uv = shutil.which("uv")
    if uv is None:
        return None
    found = subprocess.run([uv, "cache", "dir"], capture_output=True, text=True, check=False)
    if found.returncode != 0:
        return found.stderr.strip()
    cache = Path(found.stdout.strip())
    entries = [*cache.glob("sdists-v*/path/*"), *cache.glob("sdists-v*/editable/*"),
               *cache.glob("wheels-v*/url/*")]  # fmt: skip
    try:
        for entry in entries:
            if not entry.is_dir() or entry.stat().st_mtime < _STARTED:
                continue
            unpacked = {Path(directory, name).resolve()
                        for directory, names, files in os.walk(entry)  # links not followed
                        for name in (*names, *files) if Path(directory, name).is_symlink()}
            for wheel in unpacked:
                if wheel.parent.name.startswith("archive-v") and wheel.is_dir():
                    shutil.rmtree(wheel)
            shutil.rmtree(entry)
    except OSError as error:
        return str(error)
    return None


def pytest_sessionfinish(session: pytest.Session) -> None:
    if hasattr(session.config, "workerinput"):  # a pytest-xdist worker: the controller sees all
        return
    if problem := _prune_local_builds():
        session.config.get_terminal_writer().line(f"uv's cache keeps this run's builds: {problem}")
    if _skips:
        writer = session.config.get_terminal_writer()
        writer.line()  # ends the progress line
        writer.sep("=", "skipped tests: skipping is not allowed in this suite (CONTRIBUTING.md)",
                   red=True)  # fmt: skip
        for nodeid, reason in _skips:
            writer.line(f"{nodeid}: {reason}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
