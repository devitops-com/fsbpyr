"""tests/support/requires.py and the hooks of tests/conftest.py: a missing tool fails the test with
one line, a test for a package without a wheel is deselected, and a skip fails the run."""

import re
import tomllib
from pathlib import Path

import pytest

from tests.support import requires, wheels

pytest_plugins = ["pytester"]

ROOT = Path(__file__).parents[2]
CONFTEST = ROOT / "tests" / "conftest.py"
# Debian's essential packages, or pulled in by gcc; the test stage has no podman
IN_EVERY_DEBIAN = {"dash", "bash", "tar", "util-linux", "binutils", "coreutils"}
NOT_IN_THE_CONTAINER = {"podman", "uidmap", "passt"}


@pytest.fixture
def suite(pytester: pytest.Pytester) -> pytest.Pytester:
    """A test suite of its own under this repository's conftest."""
    pytester.makeconftest(CONFTEST.read_text())
    return pytester


def _packages() -> set[str]:
    return {package for entry in requires.TOOLS.values() for package in entry.package.split()}


def test_a_missing_tool_fails_with_one_line_naming_it_and_the_fix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(pytest.fail.Exception) as raised:
        requires.tool("strace")

    message = str(raised.value)
    assert "\n" not in message
    assert message.startswith("missing tool: strace (Debian package: strace): ")
    assert message.endswith("; see CONTRIBUTING.md, Running the tests")


def test_a_tool_with_several_packages_and_one_without_a_package_say_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(pytest.fail.Exception) as podman:
        requires.tool("podman")
    with pytest.raises(pytest.fail.Exception) as clang:
        requires.tool("clang", "links a test program")

    assert "(Debian packages: podman uidmap passt)" in str(podman.value)
    assert "(make puts fsbpyr's LLVM 23 first on PATH;" in str(clang.value)
    assert ": links a test program; see CONTRIBUTING.md" in str(clang.value)


def test_a_tool_that_is_there_is_returned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tool = tmp_path / "llvm-objdump"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    assert requires.tool("llvm-objdump") == str(tool)


def test_an_unmet_environment_fails_with_one_line_naming_the_change() -> None:
    with pytest.raises(pytest.fail.Exception) as raised:
        requires.unmet("no flux", "get some")

    assert str(raised.value) == (
        "unmet environment: no flux: get some; see CONTRIBUTING.md, Running the tests"
    )


def test_a_test_marked_with_a_missing_tool_fails_before_it_runs(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    suite.makepyfile(test_probe="""
        import pytest
        from tests.support import requires

        @pytest.fixture
        def built():
            raise AssertionError("a fixture ran before the tool was asked for")

        @requires.tools("strace")
        def test_probe(built):
            pass
    """)
    monkeypatch.setenv("PATH", str(tmp_path))

    result = suite.runpytest()

    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*missing tool: strace (Debian package: strace): *"])


def test_a_test_marked_with_a_tool_that_is_there_runs(suite: pytest.Pytester) -> None:
    suite.makepyfile(test_probe="""
        from tests.support import requires

        @requires.tools("ls")
        def test_probe():
            pass
    """)

    result = suite.runpytest()

    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1)


def test_a_test_that_requires_podman_is_marked_podman(suite: pytest.Pytester) -> None:
    suite.makepyfile(test_probe="""
        import pytest
        from tests.support import requires

        @requires.tools("podman")
        def test_needs_podman():
            raise AssertionError

        def test_does_not():
            pass
    """)
    suite.makeini("[pytest]\nmarkers =\n    podman\n    requires_tools\n")

    result = suite.runpytest("-m", "not podman")

    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1, deselected=1)


def test_a_test_needing_a_wheel_that_does_not_exist_is_deselected_not_skipped(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(wheels.NO_WHEELS, "3.14", frozenset({"made-up"}))
    suite.makepyfile(test_probe="""
        from tests.support import wheels

        @wheels.needs("made-up", python="3.14")
        def test_where_there_is_none():
            raise AssertionError

        @wheels.needs("made-up", python="3.15")
        def test_where_there_is_one():
            pass

        @wheels.needs("made_up", python="3.14")
        def test_under_another_spelling():
            raise AssertionError
    """)
    suite.makeini("[pytest]\nmarkers =\n    needs_wheel\n")

    result = suite.runpytest()

    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1, deselected=2)


@pytest.mark.parametrize(
    "body",
    ["pytest.skip('x')", "pytest.xfail('x')"],
    ids=["a skip", "an xfail"],
)
def test_a_skip_or_xfail_fails_the_run(suite: pytest.Pytester, body: str) -> None:
    suite.makepyfile(test_probe=f"import pytest\n\ndef test_probe():\n    {body}\n")

    result = suite.runpytest()

    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines([
        "*= skipped tests: skipping is not allowed in this suite (CONTRIBUTING.md) =*",
        "test_probe.py::test_probe: *",
    ])


def test_a_marked_xfail_fails_the_run_too(suite: pytest.Pytester) -> None:
    suite.makepyfile(test_probe="""
        import pytest

        @pytest.mark.xfail(strict=True)
        def test_probe():
            assert False
    """)

    result = suite.runpytest()

    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["test_probe.py::test_probe: xfail: *"])


def test_an_empty_parameter_set_is_a_collection_error_not_a_skip() -> None:
    options = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]

    assert options["empty_parameter_set_mark"] == "fail_at_collect"


def test_every_tool_and_package_is_in_contributing_md() -> None:
    contributing = (ROOT / "CONTRIBUTING.md").read_text()
    section = contributing.split("### What the tests need")[1].split("\n## ")[0]

    for name, entry in requires.TOOLS.items():
        assert any(re.search(rf"`{re.escape(command)}`|\b{re.escape(command)}\b", section)
                   for command in (name, *entry.commands)), name  # fmt: skip
    missing = [package for package in sorted(_packages()) if package not in section]
    assert not missing


def test_the_test_container_installs_every_package_the_tools_come_from() -> None:
    files = ("tools/test-setup.sh", "Containerfile")
    installed = "".join((ROOT / name).read_text() for name in files)

    missing = {package for package in _packages() - IN_EVERY_DEBIAN - NOT_IN_THE_CONTAINER
               if not re.search(rf"\b{re.escape(package)}\b", installed)}  # fmt: skip
    assert not missing
