"""A pytest plugin: a fixture, an option and a report of its own (the terminal reporter is off)."""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shout", action="store_true", help="upper-case the greeting")


@pytest.fixture
def greeting(request: pytest.FixtureRequest) -> str:
    text = "hello from the plugin"
    return text.upper() if request.config.getoption("--shout") else text


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when == "call" or report.failed:
        print(f"{report.outcome.upper()} {report.nodeid}")
