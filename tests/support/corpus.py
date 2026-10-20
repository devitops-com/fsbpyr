"""The real-world corpus (TST-3): tests/corpus/<app>/ holds a small project per risk class, each
with the commands to compare between the app's venv and its built executable (commands.toml).

``RESULTS`` collects one record per app; the end-to-end session prints it (tests/e2e/conftest.py)
so that the full-suite log shows the false-success count and the first-build success rate.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CORPUS = Path(__file__).parents[1] / "corpus"


def apps() -> list[str]:
    """The corpus directories, by name."""
    return sorted(path.name for path in CORPUS.iterdir() if path.is_dir())


def commands(app: str) -> list[list[str]]:
    """The argument lists in ``app``'s commands.toml, run from its project directory."""
    with (CORPUS / app / "commands.toml").open("rb") as file:
        return [list(entry["argv"]) for entry in tomllib.load(file)["command"]]


def known_failure(app: str) -> str | None:
    """Why ``app`` fails today, if its commands.toml says so; the test then expects the failure
    and goes red when it stops."""
    with (CORPUS / app / "commands.toml").open("rb") as file:
        reason = tomllib.load(file).get("known-failure")
    return str(reason) if reason else None


def settings(app: str) -> dict[str, object]:
    """The ``[tool.fsbpyr]`` table of ``app``'s pyproject.toml: what the app needed beyond the
    defaults (empty when nothing)."""
    with (CORPUS / app / "pyproject.toml").open("rb") as file:
        return dict(tomllib.load(file).get("tool", {}).get("fsbpyr", {}))


@dataclass
class Result:
    app: str
    settings: dict[str, object]
    built: bool = False
    commands: int = 0
    false_successes: int = 0  # the executable exited 0 and its output differs from the venv's
    failures: int = 0  # the outputs differ and the executable did not exit 0, or the build failed


RESULTS: list[Result] = []


@dataclass
class Totals:
    apps: int = 0
    first_builds: int = 0  # built, and every command agreed with the venv's run
    false_successes: int = 0
    needing_settings: list[str] = field(default_factory=list)


def totals() -> Totals:
    total = Totals()
    for result in RESULTS:
        total.apps += 1
        total.first_builds += result.built and not result.false_successes and not result.failures
        total.false_successes += result.false_successes
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
        f"false successes (target 0): {total.false_successes}",
        f"first-build success rate: {total.first_builds}/{total.apps}",
        "apps with [tool.fsbpyr] settings: " + (", ".join(total.needing_settings) or "none"),
    ]
