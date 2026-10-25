"""The real-world corpus (TST-3): tests/corpus/<app>/ holds a small project per risk class, each
with the commands to compare between the app's venv and its built executable (commands.toml,
described in tests/corpus/README.md).

The corpus test records one ``Result`` per app in its report (``record``); the end-to-end
conftest collects them, in pytest-xdist's controller as well, and the session prints them
(``summary``) so that the full-suite log shows the false-success count and the first-build
success rate.
"""

import os
import platform
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from . import locks, wheels

CORPUS = Path(__file__).parents[1] / "corpus"
_PROPERTY = "corpus-result"
SELECTION = "FSBPYR_CORPUS_APPS"  # a comma-separated subset for the corpus test to run


def apps() -> list[str]:
    """The corpus directories, by name."""
    return sorted(path.name for path in CORPUS.iterdir() if path.is_dir())


def selected() -> list[str]:
    """The apps the corpus test runs: ``apps()``, or the ones FSBPYR_CORPUS_APPS names (the EL8
    workflow runs a few native-heavy ones, with the glibc 2.28 stub: FSBPYR_TEST_GLIBC)."""
    chosen = [name.strip() for name in os.environ.get(SELECTION, "").split(",") if name.strip()]
    unknown = sorted(set(chosen) - set(apps()))
    if unknown:
        raise ValueError(f"{SELECTION} names no corpus app: {', '.join(unknown)}")
    return sorted(set(chosen)) or apps()


def _commands_toml(app: str) -> dict[str, Any]:
    with (CORPUS / app / "commands.toml").open("rb") as file:
        return tomllib.load(file)


@dataclass(frozen=True)
class Command:
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)  # added to the runs' environment
    stdin: str | None = None  # the text both runs read on stdin (none: no input)


def commands(app: str) -> list[Command]:
    """The commands in ``app``'s commands.toml, run from its project directory."""
    return [Command(list(entry["argv"]), dict(entry.get("env", {})), entry.get("stdin"))
            for entry in _commands_toml(app)["command"]]  # fmt: skip


def expected(app: str) -> str:
    """What one of ``app``'s outputs must contain, besides agreeing with the venv's: the
    ``expect`` of its commands.toml (so that two runs that fail alike do not pass)."""
    return str(_commands_toml(app)["expect"])


def known_failure(app: str, python: str) -> str | None:
    """Why ``app`` fails today on ``python`` (X.Y), if its commands.toml says so; the test then
    expects the failure and goes red when it stops. ``known-failure`` is the reason, or a table
    of the reason and the Pythons it fails on (``pythons``) when it fails on some only."""
    entry = _commands_toml(app).get("known-failure")
    if isinstance(entry, dict):
        return str(entry["reason"]) if python in entry["pythons"] else None
    return str(entry) if entry else None


def settings(app: str) -> dict[str, object]:
    """The ``[tool.fsbpyr]`` table of ``app``'s pyproject.toml: what the app needed beyond the
    defaults (empty when nothing)."""
    with (CORPUS / app / "pyproject.toml").open("rb") as file:
        return dict(tomllib.load(file).get("tool", {}).get("fsbpyr", {}))


def sdists(app: str) -> list[str]:
    """The packages ``app`` builds from their sdists, as its commands.toml's ``sdists`` lists
    them: pure-Python packages that publish no wheel (antlr4-python3-runtime)."""
    return [str(name) for name in _commands_toml(app).get("sdists", [])]


def gaps(app: str, python: str, machine: str) -> list[str]:
    """The packages ``app``'s lock installs on ``python`` and ``machine`` without a usable wheel
    there, but those it builds from their sdists."""
    found = locks.without_wheel(CORPUS / app / "uv.lock", python, machine)
    return [package for package in found if package not in sdists(app)]


def unlisted_gaps(app: str, python: str, machine: str) -> list[str]:
    """``gaps`` if no-wheels.txt lists none of them: the app would be skipped there for no
    reason written down (none, or the lock needs looking at). One listed gap explains the
    skip; the others need no line (an older pin in that app's lock, say)."""
    found = gaps(app, python, machine)
    return [] if any(wheels.missing(package, python) for package in found) else found


def skip_without_wheels(app: str, python: str) -> None:
    """Skips the test when a package ``app``'s lock installs here has no wheel for ``python``
    (X.Y) on this machine, as no-wheels.txt says."""
    machine = platform.machine()
    if found := gaps(app, python, machine):
        pytest.skip(f"{wheels.REASON} {python} on {machine}: {', '.join(found)}")


@dataclass
class Result:
    app: str
    settings: dict[str, object]
    built: bool = False
    commands: int = 0
    false_successes: int = 0  # the executable exited 0 and its output differs from the venv's
    failures: int = 0  # the outputs differ and the executable did not exit 0, or the build failed
    known: bool = False  # a known failure (commands.toml), expected to fail


def record(request: pytest.FixtureRequest, result: Result) -> None:
    """Attaches ``result`` to the test's call report (``collect`` reads it back); called as the
    test ends, since the report is made then. A dict: pytest-xdist sends reports to the
    controller, and its serializer takes no other objects."""
    request.node.user_properties.append((_PROPERTY, asdict(result)))


RESULTS: list[Result] = []


def collect(report: pytest.TestReport) -> None:
    """Keeps the result a corpus test's call report carries."""
    if report.when == "call":
        RESULTS.extend(Result(**cast("dict[str, Any]", value))
                       for name, value in report.user_properties if name == _PROPERTY)  # fmt: skip


@dataclass
class Totals:
    apps: int = 0
    first_builds: int = 0  # built, and every command agreed with the venv's run
    false_successes: int = 0
    known_false_successes: int = 0  # of them, in known failures
    needing_settings: list[str] = field(default_factory=list)


def totals() -> Totals:
    total = Totals()
    for result in RESULTS:
        total.apps += 1
        total.first_builds += result.built and not result.false_successes and not result.failures
        total.false_successes += result.false_successes
        total.known_false_successes += result.false_successes if result.known else 0
        if result.settings:
            total.needing_settings.append(f"{result.app} {sorted(result.settings)}")
    return total


def summary() -> list[str]:
    """The lines for the terminal summary; none when no corpus test ran."""
    if not RESULTS:
        return []
    total = totals()
    return [
        f"corpus apps: {total.apps}",
        f"false successes (target 0): {total.false_successes}, in known failures: "
        f"{total.known_false_successes}",
        f"first-build success rate: {total.first_builds}/{total.apps}",
        "apps with [tool.fsbpyr] settings: " + (", ".join(total.needing_settings) or "none"),
    ]
