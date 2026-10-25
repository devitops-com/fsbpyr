"""Every test: no cache collection unless a test asks for one (D38): the tool's daily check and
the launcher's background runs would otherwise act on the caches tests use, or the user's.

pytest passes a run however many tests skip. FSBPYR_TESTS_REQUIRE_ALL names one of the lists
below (the full-suite workflow sets `full` and `portability`, el8.yml `el8` and `portability`,
`make test-container` `container`), and a skip that the list does not expect fails the run.

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

# The skips each list's environment cannot avoid, each entry a nodeid or the start of a reason
# (some reasons carry runtime text).
EXPECTED_SKIPS = {
    # `make test-container`: the Containerfile's `test` stage, as `tester` (uid 1000, one
    # supplementary group) with no podman, and the Python of .python-version only
    "container": (
        "PBS distribution for",
        "needs Python ",  # the variant a dispatch test builds: another Python, its runtime
        "needs the Python ",
        "3.12's takes one resource name",
        "needs podman",
        "needs a mount that allows set-id bits",
        "needs set-id bits",
        "needs busybox",  # install.sh's tests run it under busybox sh and with wget:
        "needs wget",  # test-setup.sh installs both for the full suite
    ),
    # full-suite.yml's job: the same container with rootless podman and /proc/sys opened for
    # user namespaces, as `tester` -- every test the other lists skip runs (the job builds
    # the other versions' runtimes), except the one 3.12 skips by version and what needs a
    # package without a wheel for the Python (tests/support/no-wheels.txt)
    "full": ("3.12's takes one resource name", "no wheel for Python "),
    # el8.yml's tests job: the suite job's container without podman, for the stub-dependent tests
    "el8": ("3.12's takes one resource name", "no wheel for Python ", "needs podman"),
    # full-suite.yml's portability job: podman on the runner host, and every image pulled
    # (the release executable of that job has no glibc 2.28 stubs: the separate EL8 workflow
    # publishes them, and its apps join the prebuilt set then)
    "portability": ("no wheel for Python ", "no glibc 2.28 apps"),
}
# The glibc baseline the tests build for: the EL8 test mode when FSBPYR_TEST_GLIBC=2.28 (read
# again by tests/support/baseline.py; not imported, since tests/unit/test_expected_skips.py runs
# this file outside the package)
TEST_GLIBC = os.environ.get("FSBPYR_TEST_GLIBC") or pins.DEFAULT_BASELINE
EL8_MODE = TEST_GLIBC != pins.DEFAULT_BASELINE
# The corpus projects (TST-3) carry test files of their own, which their built apps run
collect_ignore_glob = ["corpus/*"]
# Hypothesis in CI (GitHub sets CI): the same examples every run, so a failure there repeats
# and a red run is never a new example's luck. The default profile stays random locally.
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
    name = os.environ.get("FSBPYR_TESTS_REQUIRE_ALL")
    if name and name not in EXPECTED_SKIPS:
        raise pytest.UsageError(f"FSBPYR_TESTS_REQUIRE_ALL={name}: no such list "
                                f"({', '.join(EXPECTED_SKIPS)})")  # fmt: skip
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
    except ToolchainError:  # no compiler here: the end-to-end tests skip in the workers
        pass


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.skipped and not hasattr(report, "wasxfail"):
        assert isinstance(report.longrepr, tuple)  # (path, line, "Skipped: <reason>")
        _skips.append((report.nodeid, report.longrepr[2].removeprefix("Skipped: ")))


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
    name = os.environ.get("FSBPYR_TESTS_REQUIRE_ALL")
    if not name:
        return
    expected = EXPECTED_SKIPS[name]
    unexpected = [f"{nodeid}: {reason}" for nodeid, reason in _skips
                  if not any(nodeid == e or reason.startswith(e) for e in expected)]  # fmt: skip
    if unexpected:
        writer = session.config.get_terminal_writer()
        writer.line()  # ends the progress line
        writer.sep("=", f"skips the {name} list does not expect", red=True)
        for line in unexpected:
            writer.line(line)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
