"""TST-3: real-world applications, one per risk class, built and compared with their venvs.

Each directory of tests/corpus is a project with the commands to run (commands.toml). The test
builds it with the settings its pyproject.toml names, runs every command in the project's venv
(staging's sync flags) and in the executable through TST-2's differential, and records what the
session summary prints: false successes (the executable exits 0 with another output than the
venv) and the first-build success rate. tests/fixtures/projects/plugins adds entry points
across distributions and a PEP 420 namespace split between them.

Runs in full-suite.yml only (not in ``make test-ci``): the corpus needs the network for its
wheels, and scipy and pandas are large.
"""

import shutil
from pathlib import Path

import pytest

from fsbpyr.cli import main
from fsbpyr.runtime.registry import Runtime

from ..support import corpus
from ..support.differential import Outcome, make_venv, run_both

pytestmark = pytest.mark.slow

FIXTURES = Path(__file__).parents[1] / "fixtures" / "projects"
DOCUMENTED = {  # what a fixture's output must contain, besides agreeing with the venv's
    "black": "black, ",
    "pytest-plugin": "PASSED ",
    "alembic": "tables ['alembic_version', 'orders', 'users']",
    "pandas-csv": "north",
    "pillow": "WEBP",
    "lxml": "['b']",
    "scipy": "gamma",
    "flask-jinja": "Hello Ada!",
    "setuptools-vendor": "demo 1.2.3",
    "gnureadline": "2 second",
    "psycopg": "select",
}


def _prepare(source: Path, name: str, tmp: Path, runtime: Runtime) -> tuple[Path, Path, Path]:
    """(the project's copy, its venv, the executable's path) for the project ``source``."""
    project = tmp / "src" / name
    shutil.copytree(source, project)
    python = runtime.python_version.rpartition(".")[0]
    venv = make_venv(project, tmp / "venv", python)
    return project, venv, tmp / "bin" / f"corpus-{name}"


def _compare(
    result: corpus.Result, where: tuple[Path, Path, Path], tmp: Path, argvs: list[list[str]]
) -> list[Outcome]:
    project, venv, executable = where
    env = {"FSBPYR_CACHE_DIR": str(tmp / "cache"), "XDG_CACHE_HOME": str(tmp / "xdg")}
    outcomes = []
    for argv in argvs:
        original, built = run_both(venv, executable, argv, env=env, cwd=project)
        result.commands += 1
        if built != original:
            if built.returncode == 0:
                result.false_successes += 1
            else:
                result.failures += 1
        outcomes.append(built)
    return outcomes


@pytest.mark.parametrize("app", corpus.apps())
def test_corpus_app_matches_its_venv(
    app: str, runtime: Runtime, tmp_path_factory: pytest.TempPathFactory,
    request: pytest.FixtureRequest,
) -> None:
    if reason := corpus.known_failure(app):
        request.applymarker(pytest.mark.xfail(reason=reason, strict=True))
    tmp = tmp_path_factory.mktemp(app)
    project, venv, executable = _prepare(corpus.CORPUS / app, app, tmp, runtime)
    result = corpus.Result(app, corpus.settings(app))
    corpus.RESULTS.append(result)

    result.built = main(["build", str(project), "-o", str(executable)]) == 0
    assert result.built, f"{app}: the build failed"
    outcomes = _compare(result, (project, venv, executable), tmp, corpus.commands(app))

    assert result.false_successes == 0, f"{app}: exit 0 with another output than the venv's"
    assert result.failures == 0, f"{app}: fails where the venv's run does not"
    assert any(DOCUMENTED[app] in outcome.stdout for outcome in outcomes), outcomes


def test_every_corpus_app_has_a_documented_output() -> None:
    assert sorted(DOCUMENTED) == corpus.apps()
    assert len(DOCUMENTED) >= 8


def test_plugins_across_distributions_match_their_venv(
    runtime: Runtime, tmp_path_factory: pytest.TempPathFactory
) -> None:
    tmp = tmp_path_factory.mktemp("plugins")
    project, venv, executable = _prepare(FIXTURES / "plugins", "plugins", tmp, runtime)
    executable = tmp / "bin" / "plugins"
    assert main(["build", str(project), "-o", str(executable)]) == 0

    original, built = run_both(venv, executable, [], env={"FSBPYR_CACHE_DIR": str(tmp / "cache")})

    assert built == original
    assert built.returncode == 0, built.stderr
    assert built.stdout.splitlines()[:2] == [
        "alpha plugsns.alpha:hook plugin-alpha alpha(x)",
        "beta plugsns.beta:hook plugin-beta beta(x)",
    ]
    assert "namespace None 1" in built.stdout  # PEP 420: one directory in both
