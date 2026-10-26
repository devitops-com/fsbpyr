"""FSBPYR_TESTS_REQUIRE_ALL (tests/conftest.py): pytest passes a run however many tests skip;
with a list named, a skip that the list does not expect fails it."""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

CONFTEST = Path(__file__).parents[1] / "conftest.py"
REQUIRE_ALL = "FSBPYR_TESTS_REQUIRE_ALL"


@pytest.fixture
def suite(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    """A test suite of its own under this repository's conftest, with no list named."""
    pytester.makeconftest(CONFTEST.read_text())
    monkeypatch.delenv(REQUIRE_ALL, raising=False)
    return pytester


def _probe(suite: pytest.Pytester) -> None:
    suite.makepyfile(test_probe="import pytest\n\ndef test_probe():\n    pytest.skip('x')\n")


def test_a_skip_the_list_does_not_expect_fails_the_run(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _probe(suite)
    monkeypatch.setenv(REQUIRE_ALL, "container")

    result = suite.runpytest()

    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*= skips the container list does not expect =*",
                                 "test_probe.py::test_probe: x"])  # fmt: skip


def test_the_full_list_expects_no_skip_at_all(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """full-suite.yml's container has everything the root-only and podman tests need."""
    suite.makepyfile(
        test_probe="import pytest\n\ndef test_probe():\n    pytest.skip('needs podman')\n"
    )
    monkeypatch.setenv(REQUIRE_ALL, "full")

    result = suite.runpytest()

    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["*= skips the full list does not expect =*",
                                 "test_probe.py::test_probe: needs podman"])  # fmt: skip


def test_with_no_list_named_a_skip_passes(suite: pytest.Pytester) -> None:
    _probe(suite)

    result = suite.runpytest()

    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(skipped=1)


def test_an_unknown_list_is_a_usage_error(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _probe(suite)
    monkeypatch.setenv(REQUIRE_ALL, "cl")

    result = suite.runpytest()

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(
        ["*FSBPYR_TESTS_REQUIRE_ALL=cl: no such list (container, full, el8, portability)*"]
    )


def test_container_expects_only_the_skips_of_a_stage_without_podman(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite.makepyfile(test_nested="import pytest\n\n@pytest.mark.skip(reason='needs podman')\n"
                     "def test_nested():\n    pass\n")  # fmt: skip
    monkeypatch.setenv(REQUIRE_ALL, "container")
    assert suite.runpytest().ret == pytest.ExitCode.OK

    _probe(suite)

    assert suite.runpytest().ret == pytest.ExitCode.TESTS_FAILED


def test_el8_expects_the_skips_of_a_container_without_podman_and_no_other(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """el8.yml's tests job: the full suite's container, minus podman."""
    suite.makepyfile(test_nested="import pytest\n\n@pytest.mark.skip(reason='needs podman')\n"
                     "def test_nested():\n    pass\n")  # fmt: skip
    monkeypatch.setenv(REQUIRE_ALL, "el8")
    assert suite.runpytest().ret == pytest.ExitCode.OK

    _probe(suite)

    assert suite.runpytest().ret == pytest.ExitCode.TESTS_FAILED
