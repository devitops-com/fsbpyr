"""TST-3: real-world applications, one per risk class, built and compared with their venvs.

Each directory of tests/corpus is a project with the commands to run (commands.toml,
tests/corpus/README.md). The test builds it with the settings its pyproject.toml names, runs
every command in the project's venv (staging's sync flags) and in the executable through
TST-2's differential, and records what the session summary prints: false successes (the
executable exits 0 with another output than the venv) and the first-build success rate.
tests/fixtures/projects/plugins adds entry points across distributions and a PEP 420 namespace
split between them.

Runs in full-suite.yml's corpus job (`make test-corpus` here), not in ``make test-ci``: the
corpus needs the network for its wheels, and several apps carry hundreds of megabytes of
native code. An app's temporary files (venv, executable and cache: gigabytes for torch) are
removed when it passes.
"""

import os
import shutil
from pathlib import Path

import pytest

from fsbpyr.build import TARGET_PYTHON
from fsbpyr.cli import main
from fsbpyr.runtime import registry
from fsbpyr.runtime.registry import Runtime

from ..support import corpus
from ..support.differential import Outcome, make_venv, run_both

pytestmark = pytest.mark.slow

FIXTURES = Path(__file__).parents[1] / "fixtures" / "projects"


def _prepare(source: Path, name: str, tmp: Path, runtime: Runtime) -> tuple[Path, Path, Path]:
    """(the project's copy, its venv, the executable's path) for the project ``source``."""
    project = tmp / "src" / name
    shutil.copytree(source, project)
    python = runtime.python_version.rpartition(".")[0]
    venv = make_venv(project, tmp / "venv", python)
    return project, venv, tmp / "bin" / f"corpus-{name}"


def _compare(
    result: corpus.Result, where: tuple[Path, Path, Path], tmp: Path, app: str
) -> tuple[list[Outcome], list[str]]:
    """The executable's outcomes, and a description of each command whose runs differ."""
    project, venv, executable = where
    env = {"FSBPYR_CACHE_DIR": str(tmp / "cache")}
    outcomes, differences = [], []
    for command in corpus.commands(app):
        original, built = run_both(venv, executable, command.argv, env={**env, **command.env},
                                   cwd=project, stdin=command.stdin, home=tmp / "home")  # fmt: skip
        result.commands += 1
        if built != original:
            if built.returncode == 0:
                result.false_successes += 1
            else:
                result.failures += 1
            differences.append(f"{command.argv}:\n  venv:       {original}\n  executable: {built}")
        outcomes.append(built)
    return outcomes, differences


def _remove(tree: Path) -> None:
    """Removes ``tree``, whose caches hold read-only directories (the extracted files'); links
    are removed, not followed."""
    for directory, _, _ in os.walk(tree):
        os.chmod(directory, 0o700)
    shutil.rmtree(tree)


@pytest.mark.parametrize("app", corpus.apps())
def test_corpus_app_matches_its_venv(
    app: str, runtime: Runtime, tmp_path_factory: pytest.TempPathFactory,
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus.skip_without_wheels(app, TARGET_PYTHON)
    reason = corpus.known_failure(app, TARGET_PYTHON)
    if reason is not None:
        request.applymarker(pytest.mark.xfail(reason=reason, strict=True))
    tmp = tmp_path_factory.mktemp(app)
    # The build's cache: the session's runtimes, and what its smoke test extracts, removed with
    # the rest (the session's cache would keep every app's natives until the session ends).
    (tmp / "build-cache").mkdir()
    (tmp / "build-cache" / "runtimes").symlink_to(registry.cache_root() / "runtimes")
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp / "build-cache"))
    result = corpus.Result(app, corpus.settings(app), known=reason is not None)
    try:
        project, venv, executable = _prepare(corpus.CORPUS / app, app, tmp, runtime)
        result.built = main(["build", str(project), "-o", str(executable)]) == 0
        assert result.built, f"{app}: the build failed"
        outcomes, differences = _compare(result, (project, venv, executable), tmp, app)
    finally:
        corpus.record(request, result)

    shown = "\n".join(differences)
    assert result.false_successes == 0, f"{app}: exit 0, another output than the venv's\n{shown}"
    assert result.failures == 0, f"{app}: fails where the venv's run does not\n{shown}"
    assert any(corpus.expected(app) in outcome.stdout for outcome in outcomes), outcomes
    _remove(tmp)


def test_every_corpus_app_says_what_its_output_contains() -> None:
    assert all(corpus.expected(app) for app in corpus.apps())
    assert len(corpus.apps()) >= 8


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
