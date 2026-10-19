"""Every test: no cache collection unless a test asks for one (D38): the tool's daily check and
the launcher's background runs would otherwise act on the caches tests use, or the user's.

pytest passes a run however many tests skip. FSBPYR_TESTS_REQUIRE_ALL names one of the lists
below (CI's test step sets `ci`), and a skip that the list does not expect fails the run."""

import os
from collections.abc import Iterator

import pytest

# The skips each list's environment cannot avoid, each entry a nodeid or the start of a reason
# (some reasons carry runtime text).
EXPECTED_SKIPS = {
    # release.yml's build job: a debian:trixie container, as root with no other group, where
    # `unshare -r` fails, without podman and with no directory of root's in /tmp
    "ci": (
        "root ignores file modes",
        "needs a supplementary group",
        "needs root's /",
        "tests/unit/boot/test_way.py::test_in_a_user_namespace_owners_outside_it_count_as_roots",
        "needs unshare, /tmp/.X",
        "needs podman",
    ),
}
_skips: list[tuple[str, str]] = []  # this run's, as (nodeid, reason)


@pytest.fixture(scope="session", autouse=True)
def _no_collection() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("FSBPYR_GC", "0")
        yield


def pytest_configure() -> None:
    name = os.environ.get("FSBPYR_TESTS_REQUIRE_ALL")
    if name and name not in EXPECTED_SKIPS:
        raise pytest.UsageError(f"FSBPYR_TESTS_REQUIRE_ALL={name}: no such list "
                                f"({', '.join(EXPECTED_SKIPS)})")  # fmt: skip


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.skipped and not hasattr(report, "wasxfail"):
        assert isinstance(report.longrepr, tuple)  # (path, line, "Skipped: <reason>")
        _skips.append((report.nodeid, report.longrepr[2].removeprefix("Skipped: ")))


def pytest_sessionfinish(session: pytest.Session) -> None:
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
