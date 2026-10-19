"""FSBPYR_TESTS_REQUIRE_ALL (tests/conftest.py): pytest passes a run however many tests skip;
with a list named, a skip that the list does not expect fails it."""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

CONFTEST = Path(__file__).parents[1] / "conftest.py"
REQUIRE_ALL = "FSBPYR_TESTS_REQUIRE_ALL"
# What release.yml's root container still skips once gcc, strace and groff are there: the rest
# of the 28 skips of the v0.20.0 run (39081258610), with their reasons as it printed them.
CI_SKIPS = {
    "tests/unit/boot/test_native.py": [
        ("test_an_unreadable_cached_file_is_replaced", "root ignores file modes"),
        ("test_unwritable_cache_is_reported_with_a_hint", "root ignores file modes"),
    ],
    "tests/unit/runtime/test_collect.py": [
        ("test_an_item_that_cannot_be_held_is_used_unheld", "root ignores file modes"),
    ],
    "tests/unit/boot/test_way.py": [
        ("test_a_directory_another_group_may_write_to_is_refused", "needs a supplementary group"),
        ("test_a_directory_of_another_user_is_refused", "needs root's /"),
        ("test_in_a_user_namespace_owners_outside_it_count_as_roots",
         "no such namespace here: ['/usr/bin/unshare', '-r']"),
        ("test_below_a_shared_directory_an_owner_outside_the_namespace_is_refused",
         "needs unshare, /tmp/.X"),
        ("test_where_the_overflow_uid_is_mapped_nobody_is_another_user", "needs podman"),
    ],
}  # fmt: skip


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
    monkeypatch.setenv(REQUIRE_ALL, "ci")

    result = suite.runpytest()

    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*= skips the ci list does not expect =*",
                                 "test_probe.py::test_probe: x"])  # fmt: skip


def test_with_no_list_named_a_skip_passes(suite: pytest.Pytester) -> None:
    _probe(suite)

    result = suite.runpytest()

    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(skipped=1)


def test_ci_expects_what_its_root_container_skips(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    for path, tests in CI_SKIPS.items():
        (suite.path / path).parent.mkdir(parents=True, exist_ok=True)
        (suite.path / path).write_text("import pytest\n" + "".join(
            f"\n@pytest.mark.skip(reason={reason!r})\ndef {name}():\n    pass\n"
            for name, reason in tests))  # fmt: skip
    monkeypatch.setenv(REQUIRE_ALL, "ci")

    result = suite.runpytest()

    assert result.ret == pytest.ExitCode.OK, result.stdout.str()
    result.assert_outcomes(skipped=8)


def test_an_unknown_list_is_a_usage_error(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _probe(suite)
    monkeypatch.setenv(REQUIRE_ALL, "cl")

    result = suite.runpytest()

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*FSBPYR_TESTS_REQUIRE_ALL=cl: no such list (ci)*"])
