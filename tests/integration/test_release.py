"""release.sh and release-notes.sh against throwaway git repos, a bare origin and a fake gh.

release.sh only tags: it promotes the CHANGELOG (and, given a new version, bumps pyproject.toml,
src/fsbpyr/__init__.py and uv.lock to it), makes one commit, tags it and pushes both atomically.
The pushed tag triggers .github/workflows/release.yml, which builds, attests and publishes;
release.sh then just follows that run. Every guard must stop the release before anything is
committed, tagged or pushed. Everything lives under ``tmp_path``; nothing reaches GitHub.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import shutil
import subprocess
import textwrap
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

from fsbpyr.bytecode import SUPPORTED_PYTHON

ROOT = Path(__file__).parents[2]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
SYSTEM_PATH = "/usr/bin:/bin"
SCRIPT_TIMEOUT_S = 60.0


def write_program(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return path


def run_script(
    command: list[str], env: dict[str, str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run without a terminal (no stdin, no controlling tty), as in CI."""
    return subprocess.run(
        command,
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT_S,
        start_new_session=True,
        check=False,
    )


RELEASE_SH = ROOT / "tools" / "release" / "release.sh"
GREEN_RUN_SH = ROOT / "tools" / "release" / "green-run.sh"
RELEASE_NOTES_SH = ROOT / "tools" / "release" / "release-notes.sh"
TAG = f"v{VERSION}"
RUN_ID = "4242"
MAIN_RUN_ID = "4141"
FULL_RUN_ID = "4343"
RUN_URL = "https://github.com/devitops-com/fsbpyr/actions/runs"
DONE = "completed/success"
REPO_URL = "https://github.com/devitops-com/fsbpyr"

# A gh that is authenticated, logs every call and knows no release. `run list --branch main`
# (HEAD's run of a workflow on main, as "ID STATUS/CONCLUSION URL") answers $FAKE_GH_MAIN_RUN
# for release.yml and $FAKE_GH_FULL_RUN for full-suite.yml, nothing when it is empty; any other
# `run list` answers $FAKE_GH_RUN_ID from its (FAKE_GH_RUN_AFTER + 1)th call on. `run watch` of
# one of those runs (4000 too) exits FAKE_GH_CHECK_WATCH_RC, any other FAKE_GH_WATCH_RC.
# Anything else (such as `release create`) is not expected from release.sh and fails.
FAKE_GH = """\
echo "$*" >> "$FAKE_GH_LOG"
case "$1 $2" in
  "auth status") ;;
  "repo view") echo devitops-com/fsbpyr ;;
  "release view")
    case "$*" in
      *--json*) echo "https://github.com/devitops-com/fsbpyr/releases/tag/$3" ;;
      *) [ -n "$FAKE_GH_RELEASE_EXISTS" ] || exit 1 ;;
    esac ;;
  "run list")
    case "$*" in
      *"--branch main "*"--workflow full-suite.yml"*|*"--workflow full-suite.yml"*"--branch main "*)
        echo "$FAKE_GH_FULL_RUN"; exit ;;
      *"--branch main "*) echo "$FAKE_GH_MAIN_RUN"; exit ;;
    esac
    calls=$(cat "$FAKE_GH_LOG.runs" 2>/dev/null || echo 0)
    echo $((calls + 1)) > "$FAKE_GH_LOG.runs"
    if [ "$calls" -ge "${FAKE_GH_RUN_AFTER:-0}" ]; then echo "$FAKE_GH_RUN_ID"; fi ;;
  "run watch")
    case "$3" in
      4000|4141|4343) exit "${FAKE_GH_CHECK_WATCH_RC:-0}" ;;
      *) exit "${FAKE_GH_WATCH_RC:-0}" ;;
    esac ;;
  *) exit 64 ;;
esac
"""


# ---------------------------------------------------------------------------------- release.sh

RELEASED_ENTRY = "### Added\n- The first thing.\n- The second thing."


def git(repo: Path, *args: str, env: dict[str, str]) -> str:
    result = run_script(["git", *args], env, cwd=repo)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def release_env(tmp_path: Path) -> dict[str, str]:
    """PATH with the fake gh, git isolated from the user's and the system's config, and no
    waiting for a workflow run to show up."""
    write_program(tmp_path / "gh-bin" / "gh", FAKE_GH)
    return {
        "HOME": str(tmp_path / "home"),
        "PATH": f"{tmp_path / 'gh-bin'}:{SYSTEM_PATH}",
        "TMPDIR": str(tmp_path),
        "FAKE_GH_LOG": str(tmp_path / "gh.log"),
        "FAKE_GH_RUN_ID": RUN_ID,
        "FAKE_GH_MAIN_RUN": f"{MAIN_RUN_ID} {DONE} {RUN_URL}/{MAIN_RUN_ID}",
        "FAKE_GH_FULL_RUN": f"{FULL_RUN_ID} {DONE} {RUN_URL}/{FULL_RUN_ID}",
        "FSBPYR_RELEASE_WATCH_WAIT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }


def pyproject_toml(version: str) -> str:
    """Only [project]'s version is the release version (the one the tag check in release.yml
    reads): another table's `version` key, even an earlier one, is neither read nor bumped."""
    return (
        '[tool.example]\nversion = "0.0.0"\n\n'
        f'[project]\nname = "fsbpyr"\nversion = "{version}"  # the release version\n'
        'description = "x"\n\n[tool.other]\nversion = "0.0.1"\n'
    )


def init_py(version: str) -> str:
    return f'"""fsbpyr."""\n\n__version__ = "{version}"\n'


def uv_lock(version: str) -> str:
    """fsbpyr's own [[package]] entry is bumped, not a dependency's before or after it."""
    return (
        'version = 1\n\n[[package]]\nname = "click"\nversion = "8.5.0"\n\n'
        f'[[package]]\nname = "fsbpyr"\nversion = "{version}"\nsource = {{ editable = "." }}\n'
        'dependencies = [\n    { name = "click" },\n]\n\n'
        '[[package]]\nname = "packaging"\nversion = "25.0"\n'
    )


def version_files(version: str) -> dict[str, str]:
    return {
        "pyproject.toml": pyproject_toml(version),
        "src/fsbpyr/__init__.py": init_py(version),
        "uv.lock": uv_lock(version),
    }


def assert_version_files(repo: Path, version: str) -> None:
    for name, text in version_files(version).items():
        assert (repo / name).read_text() == text, name


def make_release_repo(
    tmp_path: Path, env: dict[str, str], unreleased: str, version: str = VERSION
) -> Path:
    """A repo on main at ``version``, in sync with a bare 'origin' under tmp_path, carrying the
    real release scripts."""
    repo, origin = tmp_path / "repo", tmp_path / "origin.git"
    (repo / "tools" / "release").mkdir(parents=True)
    shutil.copy2(RELEASE_SH, repo / "tools" / "release" / "release.sh")
    shutil.copy2(RELEASE_NOTES_SH, repo / "tools" / "release" / "release-notes.sh")
    shutil.copy2(GREEN_RUN_SH, repo / "tools" / "release" / "green-run.sh")
    for name, text in version_files(version).items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    (repo / "README.md").write_text("fsbpyr\n")
    (repo / ".gitignore").write_text("dist/\n")
    (repo / "CHANGELOG.md").write_text(changelog(unreleased))
    run_script(["git", "init", "-q", "--bare", "-b", "main", str(origin)], env)
    git(repo.parent, "init", "-q", "-b", "main", str(repo), env=env)
    git(repo, "add", ".", env=env)
    git(repo, "commit", "-q", "-m", "initial", env=env)
    git(repo, "remote", "add", "hub", str(origin), env=env)
    git(repo, "push", "-q", "hub", "main", env=env)
    return repo


def changelog(unreleased: str) -> str:
    return (
        "# Changelog\n\nIntro.\n\n## [Unreleased]\n\n"
        f"{unreleased}\n\n## [0.0.1] - 2026-01-01\n\n- Old.\n"
    )


def promoted_changelog(day: str, version: str = VERSION) -> str:
    return changelog(RELEASED_ENTRY).replace(
        "## [Unreleased]\n", f"## [Unreleased]\n\n## [{version}] - {day}\n"
    )


def run_release(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return run_script(
        ["bash", "tools/release/release.sh", *args],
        env | {"FSBPYR_RELEASE_ASSUME_YES": "1"},
        cwd=repo,
    )


def gh_calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "gh.log"
    return log.read_text().splitlines() if log.exists() else []


def tag_run_calls(tmp_path: Path, prefix: str = "run ") -> list[str]:
    """gh's calls about the tag's workflow run: not the check of main's runs before the commit."""
    checks = tuple(f"run watch {i}" for i in (MAIN_RUN_ID, FULL_RUN_ID, "4000"))
    return [
        c
        for c in gh_calls(tmp_path)
        if c.startswith(prefix) and "--branch main " not in c and not c.startswith(checks)
    ]


def assert_nothing_built_or_published(tmp_path: Path) -> None:
    assert not [call for call in gh_calls(tmp_path) if call.startswith("release create")]


def assert_nothing_committed_or_pushed(repo: Path, tmp_path: Path, env: dict[str, str]) -> None:
    assert git(repo, "tag", "--list", env=env) == ""
    assert git(repo, "status", "--porcelain", env=env) == ""
    assert git(repo, "log", "--format=%s", env=env) == "initial"
    origin = tmp_path / "origin.git"
    assert git(origin, "log", "--format=%s", "main", env=env) == "initial"
    assert git(origin, "tag", "--list", env=env) == ""
    assert_nothing_built_or_published(tmp_path)
    assert not tag_run_calls(tmp_path)


def assert_released_at_origin(
    repo: Path, tmp_path: Path, env: dict[str, str], tag: str = TAG
) -> None:
    """One release commit on top of the last one and the annotated tag, both on origin, both
    at HEAD."""
    assert git(repo, "log", "--format=%s", env=env).splitlines() == [
        f"chore: release {tag}",
        "initial",
    ]
    assert git(repo, "cat-file", "-t", tag, env=env) == "tag"  # annotated
    assert git(repo, "tag", "-l", "--format=%(contents:subject)", tag, env=env) == f"fsbpyr {tag}"
    head = git(repo, "rev-parse", "HEAD", env=env)
    origin = tmp_path / "origin.git"
    assert git(origin, "rev-parse", "main", f"{tag}^{{commit}}", env=env).split() == [head, head]


def released_files(repo: Path, env: dict[str, str]) -> list[str]:
    return sorted(git(repo, "show", "--name-only", "--format=", "HEAD", env=env).splitlines())


def test_release_refuses_to_run_off_main(tmp_path: Path, release_env: dict[str, str]) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)
    git(repo, "checkout", "-q", "-b", "feature", env=release_env)

    result = run_release(repo, release_env)

    assert result.returncode == 1
    assert "not on 'main' (currently on 'feature')" in result.stderr
    git(repo, "checkout", "-q", "main", env=release_env)
    assert_nothing_committed_or_pushed(repo, tmp_path, release_env)
    assert_nothing_built_or_published(tmp_path)


def test_release_refuses_a_dirty_tree(tmp_path: Path, release_env: dict[str, str]) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)
    (repo / "README.md").write_text("# edited\n")

    result = run_release(repo, release_env)

    assert result.returncode == 1
    assert "working tree is not clean" in result.stderr
    git(repo, "checkout", "--", "README.md", env=release_env)
    assert_nothing_committed_or_pushed(repo, tmp_path, release_env)
    assert_nothing_built_or_published(tmp_path)


def test_release_refuses_untracked_files_that_git_status_is_told_to_hide(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """The build packs untracked files under src/ into the executable: it would ship code that
    is not in the tag."""
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)
    git(repo, "config", "status.showUntrackedFiles", "no", env=release_env)
    (repo / "tools" / "extra.py").write_text("print('not committed')\n")

    result = run_release(repo, release_env)

    assert result.returncode == 1
    assert "working tree is not clean" in result.stderr
    (repo / "tools" / "extra.py").unlink()
    assert_nothing_committed_or_pushed(repo, tmp_path, release_env)
    assert_nothing_built_or_published(tmp_path)


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_release_refuses_local_edits_hidden_from_git_status(
    flag: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)
    git(repo, "update-index", flag, "README.md", env=release_env)
    (repo / "README.md").write_text("# edited\n")

    result = run_release(repo, release_env)

    assert result.returncode == 1
    assert "hidden from git status" in result.stderr
    assert "  README.md" in result.stderr.splitlines()
    assert "git update-index --no-assume-unchanged --no-skip-worktree FILE" in result.stderr
    assert_nothing_built_or_published(tmp_path)


def test_release_refuses_when_main_is_not_in_sync_with_origin(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)
    git(repo, "commit", "-q", "--allow-empty", "-m", "unpushed", env=release_env)

    result = run_release(repo, release_env)

    assert result.returncode == 1
    assert "local main differs from hub/main" in result.stderr
    assert git(repo, "tag", "--list", env=release_env) == ""
    assert_nothing_built_or_published(tmp_path)


def check_calls(tmp_path: Path, workflow: str) -> list[str]:
    return [c for c in gh_calls(tmp_path)
            if c.startswith("run list") and f"--workflow {workflow}" in c
            and "--branch main " in c]  # fmt: skip


@pytest.mark.parametrize(
    ("variable", "workflow"),
    [("FAKE_GH_MAIN_RUN", "release.yml"), ("FAKE_GH_FULL_RUN", "full-suite.yml")],
)
def test_release_refuses_a_head_without_a_run_of_each_workflow_on_main(
    variable: str, workflow: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    """release.yml and full-suite.yml run on every push to main; only a commit both passed is
    tagged, so a failure shows on main before any tag exists."""
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)
    head = git(repo, "rev-parse", "HEAD", env=release_env)

    result = run_release(repo, release_env | {variable: ""})

    assert result.returncode == 1
    assert f"main at {head[:7]} has no {workflow} run" in result.stderr
    assert_nothing_committed_or_pushed(repo, tmp_path, release_env)
    (check,) = check_calls(tmp_path, workflow)
    for option in ("--branch main", "--event push", f"--commit {head}"):
        assert option in check


@pytest.mark.parametrize("workflow", ["release.yml", "full-suite.yml"])
@pytest.mark.parametrize("state", ["completed/failure", "completed/cancelled"])
def test_release_refuses_a_head_whose_run_did_not_succeed(
    workflow: str, state: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)
    variable = "FAKE_GH_FULL_RUN" if workflow == "full-suite.yml" else "FAKE_GH_MAIN_RUN"

    result = run_release(repo, release_env | {variable: f"4000 {state} {RUN_URL}/4000"})

    assert result.returncode == 1
    assert f"{workflow}'s run for main" in result.stderr
    assert f"did not succeed ({state}): {RUN_URL}/4000" in result.stderr
    assert_nothing_committed_or_pushed(repo, tmp_path, release_env)


@pytest.mark.parametrize("workflow", ["release.yml", "full-suite.yml"])
def test_release_waits_for_a_run_in_progress_and_goes_on_when_it_passes(
    workflow: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)
    variable = "FAKE_GH_FULL_RUN" if workflow == "full-suite.yml" else "FAKE_GH_MAIN_RUN"

    result = run_release(repo, release_env | {variable: f"4000 in_progress/ {RUN_URL}/4000"})

    assert result.returncode == 0, result.stderr
    assert "run watch 4000 --exit-status" in gh_calls(tmp_path)
    assert_released_at_origin(repo, tmp_path, release_env)


@pytest.mark.parametrize("state", ["in_progress/", "queued/"])
def test_release_refuses_when_the_run_it_waited_for_fails(
    state: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)
    env = release_env | {"FAKE_GH_FULL_RUN": f"4000 {state} {RUN_URL}/4000",
                         "FAKE_GH_CHECK_WATCH_RC": "1"}  # fmt: skip

    result = run_release(repo, env)

    assert result.returncode == 1
    assert "run watch 4000 --exit-status" in gh_calls(tmp_path)
    assert "full-suite.yml's run for main" in result.stderr
    assert "failed" in result.stderr
    assert_nothing_committed_or_pushed(repo, tmp_path, release_env)


@pytest.mark.parametrize("where", ["local", "origin", "github"])
def test_release_refuses_a_version_that_is_already_tagged_or_released(
    where: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)
    if where in ("local", "origin"):
        git(repo, "tag", TAG, env=release_env)
    if where == "origin":
        git(repo, "push", "-q", "hub", TAG, env=release_env)
        git(repo, "tag", "-d", TAG, env=release_env)
    env = release_env | ({"FAKE_GH_RELEASE_EXISTS": "1"} if where == "github" else {})

    result = run_release(repo, env)

    expected = {
        "local": f"tag {TAG} already exists locally",
        "origin": f"tag {TAG} already exists on hub",
        "github": f"a GitHub release for {TAG} already exists",
    }[where]
    assert result.returncode == 1
    assert expected in result.stderr
    assert git(repo, "log", "--format=%s", env=release_env) == "initial"
    assert_nothing_built_or_published(tmp_path)


def test_release_refuses_an_empty_unreleased_section(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env, "")

    result = run_release(repo, release_env)

    assert result.returncode == 1
    assert "'## [Unreleased]' section in CHANGELOG.md is empty" in result.stderr
    assert (repo / "CHANGELOG.md").read_text() == changelog("")
    assert_nothing_committed_or_pushed(repo, tmp_path, release_env)
    assert_nothing_built_or_published(tmp_path)


def test_release_refuses_without_confirmation_off_a_tty(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """No controlling terminal: /dev/tty exists but cannot be opened."""
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)

    result = run_script(["bash", "tools/release/release.sh"], release_env, cwd=repo)

    assert result.returncode == 1
    assert "FSBPYR_RELEASE_ASSUME_YES=1" in result.stderr
    assert_nothing_committed_or_pushed(repo, tmp_path, release_env)
    assert_nothing_built_or_published(tmp_path)


def test_release_promotes_unreleased_and_reverts_it_when_the_commit_fails(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)
    write_program(
        repo / ".git" / "hooks" / "pre-commit",
        "cp CHANGELOG.md ../changelog-at-commit.md\nexit 1\n",
    )
    before = datetime.date.today().isoformat()

    result = run_release(repo, release_env)

    after = datetime.date.today().isoformat()
    assert result.returncode == 1
    promoted = (tmp_path / "changelog-at-commit.md").read_text()
    assert promoted in {promoted_changelog(day) for day in (before, after)}
    assert "Reverted CHANGELOG.md" in result.stderr
    assert (repo / "CHANGELOG.md").read_text() == changelog(RELEASED_ENTRY)
    assert_nothing_committed_or_pushed(repo, tmp_path, release_env)


@pytest.mark.parametrize("args", [[], [VERSION]], ids=["no-version", "the-current-version"])
def test_release_commits_tags_and_pushes_without_building_or_publishing(
    args: list[str], tmp_path: Path, release_env: dict[str, str]
) -> None:
    """The tag push is the release: CI builds, attests and publishes. release.sh follows the
    workflow run the tag triggered and prints the release URL. Given the version pyproject.toml
    already has, nothing is bumped."""
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)

    result = run_release(repo, release_env, *args)

    assert result.returncode == 0, result.stderr
    assert f"## [{VERSION}] - " in (repo / "CHANGELOG.md").read_text()
    assert_released_at_origin(repo, tmp_path, release_env)
    assert released_files(repo, release_env) == ["CHANGELOG.md"]
    assert_version_files(repo, VERSION)
    assert f"version : {VERSION}\n" in result.stdout
    assert_nothing_built_or_published(tmp_path)
    head = git(repo, "rev-parse", "HEAD", env=release_env)
    (run_list,) = tag_run_calls(tmp_path, "run list")
    for option in ("--workflow release.yml", f"--branch {TAG}", f"--commit {head}", "--event push"):
        assert option in run_list
    assert f"run watch {RUN_ID} --exit-status" in gh_calls(tmp_path)
    assert f"{REPO_URL}/releases/tag/{TAG}" in result.stdout


def test_release_waits_for_the_workflow_run_to_show_up(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """GitHub creates the run a few seconds after the push."""
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)
    env = release_env | {"FAKE_GH_RUN_AFTER": "1", "FSBPYR_RELEASE_WATCH_WAIT": "30"}

    result = run_release(repo, env)

    assert result.returncode == 0, result.stderr
    assert len(tag_run_calls(tmp_path, "run list")) == 2
    assert f"run watch {RUN_ID} --exit-status" in gh_calls(tmp_path)


@pytest.mark.parametrize("answer", ["", "null"], ids=["empty", "null"])
def test_release_succeeds_when_no_workflow_run_shows_up(
    answer: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    """The tag is pushed and the workflow will run; only following it is not possible."""
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)

    result = run_release(repo, release_env | {"FAKE_GH_RUN_ID": answer})

    assert result.returncode == 0, result.stderr
    assert_released_at_origin(repo, tmp_path, release_env)
    assert f"{REPO_URL}/actions/workflows/release.yml" in result.stdout
    assert not [call for call in gh_calls(tmp_path) if call.startswith("run watch")]


def test_release_reports_a_failed_workflow_run_with_its_recovery(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """A pushed tag is never moved: its version is burned, and the fix is released as the next."""
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)
    major, minor, patch = VERSION.split(".")
    following = f"{major}.{minor}.{int(patch) + 1}"

    result = run_release(repo, release_env | {"FAKE_GH_WATCH_RC": "1"})

    assert result.returncode == 1
    assert_released_at_origin(repo, tmp_path, release_env)  # the tag stays pushed
    assert f"{TAG} is pushed but not published" in result.stderr
    assert f"gh run view {RUN_ID} --log-failed" in result.stderr
    assert f"gh run rerun {RUN_ID} --failed" in result.stderr
    assert f"this version is burned: fix it on main and release {following}" in result.stderr
    assert f"make release VERSION={following}" in result.stderr
    assert f"the [{VERSION}] section of CHANGELOG.md back into [Unreleased]" in result.stderr
    assert f"the top of CHANGELOG.md that {TAG} has no release" in result.stderr
    assert "refs/tags" not in result.stderr
    assert "git tag" not in result.stderr


def test_release_pushes_commit_and_tag_atomically(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """origin refuses the tag: main must not move either, or a half-release would sit on main."""
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY)
    write_program(
        tmp_path / "origin.git" / "hooks" / "update",
        'case "$1" in refs/tags/*) echo "tags are frozen" >&2; exit 1 ;; esac\n',
    )

    result = run_release(repo, release_env)

    assert result.returncode == 1
    assert "push failed" in result.stderr
    origin = tmp_path / "origin.git"
    assert git(origin, "log", "--format=%s", "main", env=release_env) == "initial"
    assert git(origin, "tag", "--list", env=release_env) == ""
    assert git(repo, "log", "-1", "--format=%s", env=release_env) == f"chore: release {TAG}"
    assert f"git tag -d {TAG} && git reset --hard HEAD~1" in result.stderr
    assert f"git push --atomic hub main {TAG}" in result.stderr
    assert "gh release create" not in result.stderr
    assert_nothing_built_or_published(tmp_path)
    assert not tag_run_calls(tmp_path)


# ------------------------------------------------------------ release.sh X.Y.Z: bump and release

CURRENT = "1.9.2"  # pyproject.toml's version before a bump


@pytest.mark.parametrize("new", ["1.9.3", "1.9.10", "1.10.0", "2.0.0"])
def test_release_bumps_the_version_in_the_one_release_commit(
    new: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    """No separate bump commit (and no separate CI runs for it): the release commit carries the
    new version and its CHANGELOG section. Versions compare as numbers: 1.9.10 > 1.9.2."""
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY, version=CURRENT)
    tag = f"v{new}"

    result = run_release(repo, release_env, new)

    assert result.returncode == 0, result.stderr
    assert f"version : {CURRENT} -> {new}\n" in result.stdout
    assert_released_at_origin(repo, tmp_path, release_env, tag)
    assert released_files(repo, release_env) == [
        "CHANGELOG.md",
        "pyproject.toml",
        "src/fsbpyr/__init__.py",
        "uv.lock",
    ]
    assert_version_files(repo, new)
    day = git(repo, "log", "-1", "--format=%cs", env=release_env)
    assert (repo / "CHANGELOG.md").read_text() == promoted_changelog(day, new)
    (run_list,) = tag_run_calls(tmp_path, "run list")
    assert f"--branch {tag}" in run_list
    assert_nothing_built_or_published(tmp_path)


@pytest.mark.parametrize(
    "new",
    ["", "1.9", "1.9.3.0", "v1.9.3", "1.9.3rc1", "01.9.3", "1.09.3", "1.9.x", " 1.9.3", "1.9.3\n"],
)
def test_release_refuses_a_version_that_is_not_x_y_z(
    new: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY, version=CURRENT)

    result = run_release(repo, release_env, new)

    assert result.returncode == 1
    assert "is not a version X.Y.Z" in result.stderr
    assert_nothing_committed_or_pushed(repo, tmp_path, release_env)


@pytest.mark.parametrize("new", ["1.9.1", "1.9.0", "1.8.10", "0.10.0"])
def test_release_refuses_a_version_lower_than_the_current_one(
    new: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY, version=CURRENT)

    result = run_release(repo, release_env, new)

    assert result.returncode == 1
    assert f"version {new} is lower than the current version {CURRENT}" in result.stderr
    assert_nothing_committed_or_pushed(repo, tmp_path, release_env)


@pytest.mark.parametrize("where", ["local", "origin", "github"])
def test_release_refuses_a_new_version_that_is_already_tagged_or_released(
    where: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY, version=CURRENT)
    tag = "v1.9.3"
    if where in ("local", "origin"):
        git(repo, "tag", tag, env=release_env)
    if where == "origin":
        git(repo, "push", "-q", "hub", tag, env=release_env)
        git(repo, "tag", "-d", tag, env=release_env)
    env = release_env | ({"FAKE_GH_RELEASE_EXISTS": "1"} if where == "github" else {})

    result = run_release(repo, env, "1.9.3")

    expected = {
        "local": f"tag {tag} already exists locally",
        "origin": f"tag {tag} already exists on hub",
        "github": f"a GitHub release for {tag} already exists",
    }[where]
    assert result.returncode == 1
    assert expected in result.stderr
    assert git(repo, "log", "--format=%s", env=release_env) == "initial"
    assert git(repo, "status", "--porcelain", env=release_env) == ""
    assert_nothing_built_or_published(tmp_path)


def test_release_reverts_the_bump_and_the_changelog_when_the_commit_fails(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY, version=CURRENT)
    write_program(
        repo / ".git" / "hooks" / "pre-commit",
        "cp pyproject.toml ../pyproject-at-commit.toml\nexit 1\n",
    )

    result = run_release(repo, release_env, "1.9.3")

    assert result.returncode == 1
    assert (tmp_path / "pyproject-at-commit.toml").read_text() == pyproject_toml("1.9.3")
    (reverted,) = [line for line in result.stderr.splitlines() if "Reverted" in line]
    for name in ("CHANGELOG.md", "pyproject.toml", "src/fsbpyr/__init__.py", "uv.lock"):
        assert name in reverted
    assert_version_files(repo, CURRENT)
    assert (repo / "CHANGELOG.md").read_text() == changelog(RELEASED_ENTRY)
    assert_nothing_committed_or_pushed(repo, tmp_path, release_env)


@pytest.mark.parametrize("name", ["src/fsbpyr/__init__.py", "uv.lock"])
def test_release_refuses_versions_that_disagree(
    name: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY, version=CURRENT)
    (repo / name).write_text(version_files("1.9.1")[name])
    git(repo, "commit", "-q", "-am", "drift", env=release_env)
    git(repo, "push", "-q", "hub", "main", env=release_env)

    result = run_release(repo, release_env, "1.9.3")

    assert result.returncode == 1
    assert f"{name} has version 1.9.1, pyproject.toml has {CURRENT}" in result.stderr
    assert git(repo, "tag", "--list", env=release_env) == ""
    assert git(repo, "status", "--porcelain", env=release_env) == ""
    assert_nothing_built_or_published(tmp_path)


def test_the_projects_versions_agree() -> None:
    """What release.sh checks before a release, checked on every change."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    (locked,) = [p["version"] for p in lock["package"] if p["name"] == "fsbpyr"]
    init = (ROOT / "src" / "fsbpyr" / "__init__.py").read_text(encoding="utf-8")

    assert locked == VERSION
    assert f'__version__ = "{VERSION}"' in init


def test_release_takes_at_most_one_version(tmp_path: Path, release_env: dict[str, str]) -> None:
    repo = make_release_repo(tmp_path, release_env, RELEASED_ENTRY, version=CURRENT)

    result = run_release(repo, release_env, "1.9.3", "1.9.4")

    assert result.returncode == 2
    assert "usage: release.sh [X.Y.Z]" in result.stderr
    assert_nothing_committed_or_pushed(repo, tmp_path, release_env)


@pytest.mark.parametrize(
    ("args", "env", "command"),
    [
        ([], {}, "bash tools/release/release.sh"),
        (["VERSION=1.2.3"], {}, "bash tools/release/release.sh '1.2.3'"),
        # An exported VERSION (another tool's) is not a request to bump.
        ([], {"VERSION": "1.2.3"}, "bash tools/release/release.sh"),
    ],
    ids=["plain", "command-line", "environment"],
)
def test_make_release_passes_only_a_command_line_version_to_release_sh(
    args: list[str], env: dict[str, str], command: str
) -> None:
    result = run_script(["make", "-n", "release", *args], {"PATH": SYSTEM_PATH} | env, ROOT)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [command]


# --------------------------------------------------------------------------- release-notes.sh

NOTES_CHANGELOG = """\
# Changelog

## [Unreleased]

## [0.2.0] - 2026-09-24

### Fixed
- Two.

## [0.1.10] - 2026-05-01

- Ten.

## [0.1.0]

### Added
- One.
"""


def run_notes(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(NOTES_CHANGELOG)
    return run_script(["bash", str(RELEASE_NOTES_SH), *args, str(path)], {"PATH": SYSTEM_PATH})


@pytest.mark.parametrize(
    ("version", "notes"),
    [
        ("0.2.0", "### Fixed\n- Two.\n"),
        ("0.1.10", "- Ten.\n"),
        ("0.1.0", "### Added\n- One.\n"),  # the last section, without a date
    ],
)
def test_release_notes_prints_exactly_the_versions_section(
    version: str, notes: str, tmp_path: Path
) -> None:
    result = run_notes(tmp_path, version)

    assert result.returncode == 0, result.stderr
    assert result.stdout == notes


@pytest.mark.parametrize("version", ["0.1", "0.1.1", "0.3.0"])
def test_release_notes_refuses_a_version_without_a_section(version: str, tmp_path: Path) -> None:
    result = run_notes(tmp_path, version)

    assert result.returncode == 1
    assert f"no '## [{version}]' section in" in result.stderr
    assert result.stdout == ""


def test_release_notes_refuses_an_empty_section(tmp_path: Path) -> None:
    result = run_notes(tmp_path, "Unreleased")

    assert result.returncode == 1
    assert "'## [Unreleased]' section in" in result.stderr
    assert "is empty" in result.stderr


def test_release_notes_reads_the_projects_changelog_by_default(tmp_path: Path) -> None:
    """Whatever [Unreleased] holds right now (it is empty right after a release)."""
    explicit = run_script(
        ["bash", str(RELEASE_NOTES_SH), "Unreleased", str(ROOT / "CHANGELOG.md")],
        {"PATH": SYSTEM_PATH},
        cwd=tmp_path,
    )

    default = run_script(
        ["bash", str(RELEASE_NOTES_SH), "Unreleased"], {"PATH": SYSTEM_PATH}, cwd=tmp_path
    )

    assert (default.returncode, default.stdout, default.stderr) == (
        explicit.returncode,
        explicit.stdout,
        explicit.stderr,
    )
    assert default.returncode in (0, 1)
    assert default.stdout or "is empty" in default.stderr


@pytest.mark.parametrize("args", [[], ["1.0.0", "CHANGELOG.md", "extra"]], ids=["none", "three"])
def test_release_notes_rejects_bad_usage(args: list[str]) -> None:
    result = run_script(["bash", str(RELEASE_NOTES_SH), *args], {"PATH": SYSTEM_PATH})

    assert result.returncode == 2
    assert "usage: release-notes.sh VERSION [CHANGELOG]" in result.stderr


# The release toolchain pins (REP-1): files in the repository, the workflow and Containerfile that
# use them, bootstrap.sh's check of the uv, and the daily check that they have not aged.

BOOTSTRAP_SH = ROOT / "tools" / "bootstrap.sh"
TOOLCHAIN_WATCH_SH = ROOT / "tools" / "toolchain-watch.sh"
IMAGE_PIN = re.compile(r"debian:trixie@sha256:([0-9a-f]{64})")


def test_the_image_is_pinned_by_digest_in_the_workflow_and_the_containerfile() -> None:
    pins = {
        path: IMAGE_PIN.findall((ROOT / path).read_text(encoding="utf-8"))
        for path in (
            ".github/workflows/release.yml",
            ".github/workflows/full-suite.yml",
            "Containerfile",
        )
    }

    # build and reproduce (REP-2) use the image, the Containerfile once
    assert [len(found) for found in pins.values()] == [2, 1, 1], pins
    assert len({digest for found in pins.values() for digest in found}) == 1  # the same digest
    for path in pins:
        text = (ROOT / path).read_text(encoding="utf-8")
        assert not re.search(r"debian:trixie$", text, re.MULTILINE), path


def test_the_publish_job_checks_the_parent_commits_runs_and_names_the_suite_run() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    publish = workflow.split("\n  publish:\n")[1]

    assert "actions: read" in publish
    assert "fetch-depth: 2" in publish
    for workflow_name in ("release.yml", "full-suite.yml"):
        assert f'tools/release/green-run.sh {workflow_name} "$parent"' in publish
    assert "parent=\"$(git rev-parse HEAD^)\"" in publish
    assert "git diff --name-only HEAD^ HEAD" in publish
    assert "CHANGELOG.md|pyproject.toml|src/fsbpyr/__init__.py|uv.lock)" in publish
    assert "Full suite: %s" in publish


def test_the_full_suite_runs_on_main_nightly_and_weekly_once_per_python_version() -> None:
    workflow = (ROOT / ".github/workflows/full-suite.yml").read_text(encoding="utf-8")
    weekly = re.findall(r'^\s+- cron: "(.+)"$', workflow, re.MULTILINE)[1]

    assert "push:\n    branches: [main]" in workflow
    assert "workflow_dispatch:" in workflow
    assert len(re.findall(r"^\s+- cron:", workflow, re.MULTILINE)) == 2
    assert f'WEEKLY="{weekly}"' in workflow  # the fuzzing time reads the weekly schedule
    # the suite and the portability matrix
    assert len(re.findall(r'python: \["3.12", "3.13", "3.14"\]', workflow)) == 2
    assert "options: --privileged" in workflow and "timeout-minutes: 60" in workflow
    assert "--ignore=tests/e2e/test_portability.py" in workflow
    assert "FSBPYR_TESTS_REQUIRE_ALL=full" in workflow
    assert 'git diff --quiet "$last" HEAD -- src/fsbpyr/launcher tests/fuzz' in workflow


def test_the_portability_job_runs_on_the_host_after_the_suite_with_nothing_skipped() -> None:
    workflow = (ROOT / ".github/workflows/full-suite.yml").read_text(encoding="utf-8")
    job = workflow.split("\n  portability:\n")[1]

    assert "needs: suite" in job and "runs-on: ubuntu-24.04" in job
    assert "container:" not in job
    assert "tools/portability-apps.sh dist/fsbpyr$SUFFIX" in job
    assert "FSBPYR_TESTS_REQUIRE_ALL=portability" in job
    assert "FSBPYR_PORTABILITY_APPS=" in job and "--junitxml=portability.xml" in job
    assert "podman login docker.io" in job


def test_the_portability_tests_pull_images_without_a_network_in_them() -> None:
    source = (ROOT / "tests/e2e/test_portability.py").read_text(encoding="utf-8")

    assert '"--network=none"' in source


def test_uv_version_names_a_uv_that_the_build_backend_accepts() -> None:
    version, sha256 = (ROOT / "tools/release/uv.version").read_text(encoding="utf-8").split()
    backend = next(
        Requirement(r)
        for r in tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
            "build-system"
        ]["requires"]
        if r.startswith("uv_build")
    )

    assert re.fullmatch(r"[0-9a-f]{64}", sha256)
    assert backend.specifier.contains(version)  # uv_build is released with uv: same numbering


def test_the_apt_snapshot_is_a_timestamp_used_on_tag_runs_only() -> None:
    snapshot = (ROOT / "tools/release/apt-snapshot").read_text(encoding="utf-8").strip()
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert re.fullmatch(r"\d{8}T\d{6}Z", snapshot)
    step = workflow.split("- name: Use the Debian snapshot")[1].split("- name:")[0]
    assert "if: github.ref_type == 'tag'" in step
    assert "tools/release/apt-snapshot" in step and "debian-security" in step


def _fake_uv(directory: Path, version: str) -> dict[str, str]:
    write_program(directory / "uv", f'echo "uv {version} (abcdef 2026-01-01)"\n')
    return {"PATH": f"{directory}:{SYSTEM_PATH}"}


def test_bootstrap_accepts_only_the_uv_that_fsbpyr_uv_version_names(tmp_path: Path) -> None:
    env = _fake_uv(tmp_path, "0.13.1")

    wrong = run_script(["bash", str(BOOTSTRAP_SH), "--uv"], {**env, "FSBPYR_UV_VERSION": "0.13.2"})
    right = run_script(["bash", str(BOOTSTRAP_SH), "--uv"], {**env, "FSBPYR_UV_VERSION": "0.13.1"})
    unset = run_script(["bash", str(BOOTSTRAP_SH), "--uv"], env)

    assert wrong.returncode == 1
    assert "FSBPYR_UV_VERSION is 0.13.2, but uv on PATH is 0.13.1" in wrong.stderr
    assert (right.returncode, unset.returncode) == (0, 0)  # a user's own uv is accepted (D26)


def _watch_env(
    tmp_path: Path, *, uv: str, snapshot: str, image: str
) -> tuple[Path, dict[str, str]]:
    """A copy of tools/ and the Containerfile with these pins, a gh that knows the newest uv
    and logs its calls, and a curl that answers like the registry."""
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "tools", root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    (root / "tools/release/uv.version").write_text(f"{uv} {'0' * 64}\n", encoding="utf-8")
    (root / "tools/release/apt-snapshot").write_text(snapshot + "\n", encoding="utf-8")
    (root / "Containerfile").write_text(f"FROM docker.io/library/debian:trixie@{image}\n",
                                        encoding="utf-8")  # fmt: skip
    bin_dir = tmp_path / "bin"
    write_program(
        bin_dir / "gh",
        """\
echo "$*" >> "$FAKE_GH_LOG"
case "$1 $2" in
  "release view") echo 0.13.3 ;;
  "issue list") ;;
  "label create") ;;
  "issue create") echo https://github.com/devitops-com/fsbpyr/issues/1 ;;
  *) exit 64 ;;
esac
""",
    )
    write_program(
        bin_dir / "curl",
        """\
case "$*" in
  *auth.docker.io*) echo '{"token":"t"}' ;;
  *) printf 'HTTP/2 200\\r\\ndocker-content-digest: sha256:%s\\r\\n' "$FAKE_REGISTRY_DIGEST" ;;
esac
""",
    )
    env = {"PATH": f"{bin_dir}:{SYSTEM_PATH}", "FAKE_GH_LOG": str(tmp_path / "gh.log"),
           "FAKE_REGISTRY_DIGEST": "a" * 64}  # fmt: skip
    return root, env


def _today_snapshot(days_ago: int) -> str:
    day = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago)
    return day.strftime("%Y%m%dT000000Z")


def test_toolchain_watch_opens_an_issue_for_old_pins(tmp_path: Path) -> None:
    root, env = _watch_env(
        tmp_path, uv="0.13.1", snapshot=_today_snapshot(120), image="sha256:" + "b" * 64
    )

    result = run_script(["bash", str(root / "tools/toolchain-watch.sh")], env)

    log = (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "issue create" in log and "--label toolchain-update" in log
    assert "3 release pin(s)" in log
    assert "not the newest release, 0.13.3" in log and "days old" in log
    assert "sha256:" + "b" * 64 in log and "sha256:" + "a" * 64 in log


def test_toolchain_watch_is_silent_when_the_pins_are_current(tmp_path: Path) -> None:
    root, env = _watch_env(
        tmp_path, uv="0.13.3", snapshot=_today_snapshot(5), image="sha256:" + "a" * 64
    )

    result = run_script(["bash", str(root / "tools/toolchain-watch.sh")], env)

    assert result.returncode == 0, result.stderr
    assert "Up to date" in result.stdout
    assert "issue" not in (tmp_path / "gh.log").read_text(encoding="utf-8")


def test_the_workflows_build_every_supported_python_and_the_release_publishes_each_variant(
) -> None:
    """The matrices list the Pythons fsbpyr supports (bytecode.SUPPORTED_PYTHON); the newest is
    the plain asset (`fsbpyr-x86_64-linux-gnu`), the others carry their version
    (`fsbpyr-3.12-x86_64-linux-gnu`), each with its .xz and licence notices, also published
    under the short names (`fsbpyr`, `fsbpyr-3.12`), all in SHA256SUMS and attested."""
    supported = sorted("{}.{}".format(*version) for version in SUPPORTED_PYTHON)
    newest = (ROOT / ".python-version").read_text(encoding="utf-8").strip().rpartition(".")[0]
    assert newest == supported[-1]
    listed = ", ".join(f'"{version}"' for version in supported)
    for name in ("release.yml", "full-suite.yml"):
        workflow = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        assert f"python: [{listed}]" in workflow, name
    suite = (ROOT / ".github/workflows/full-suite.yml").read_text(encoding="utf-8")
    assert f"for version in {' '.join(supported)}; do" in suite  # the other runtimes

    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    jobs = dict(re.findall(r"^  ([a-z]+):\n(.*?)(?=^  [a-z]+:\n|\Z)", workflow, re.M | re.S))
    build, publish = jobs["build"], jobs["publish"]
    assert "make bootstrap $MAKE_ARGS" in build
    assert 'echo "MAKE_ARGS=PYTHON=$MATRIX_PYTHON"' in build
    assert "xz --x86 --lzma2=preset=9e --threads=1 --keep dist/fsbpyr$SUFFIX" in build
    assert "TARGET: x86_64-linux-gnu" in workflow
    assert "> dist/THIRD_PARTY_LICENSES$SUFFIX-$TARGET.txt" in build
    assert 'cp "dist/fsbpyr$SUFFIX$ext" "dist/fsbpyr$SUFFIX-$TARGET$ext"' in build  # long names
    assert ('cp "dist/THIRD_PARTY_LICENSES$SUFFIX-$TARGET.txt" '
            '"dist/THIRD_PARTY_LICENSES$SUFFIX.txt"') in build
    uploaded = re.search(r"path: \|\n((?:            dist/.*\n)+)", build)
    assert uploaded is not None
    assert [line.strip() for line in uploaded.group(1).splitlines()] == [
        "dist/fsbpyr${{ env.SUFFIX }}",  # the short names, copies of the long ones
        "dist/fsbpyr${{ env.SUFFIX }}.xz",
        "dist/fsbpyr${{ env.SUFFIX }}-${{ env.TARGET }}",
        "dist/fsbpyr${{ env.SUFFIX }}-${{ env.TARGET }}.xz",
        "dist/THIRD_PARTY_LICENSES${{ env.SUFFIX }}.txt",
        "dist/THIRD_PARTY_LICENSES${{ env.SUFFIX }}-${{ env.TARGET }}.txt",
    ]
    assert "name: built-${{ matrix.python }}" in build
    assert 'release_asset\\": \\"fsbpyr$SUFFIX-$TARGET' in build  # each records its long asset
    assert "-name \"fsbpyr*-$TARGET\" ! -name '*.xz'" in jobs["compare"]
    assert "pattern: built-*" in publish and "merge-multiple: true" in publish
    assert "sha256sum fsbpyr* THIRD_PARTY_LICENSES*.txt install.sh > SHA256SUMS" in publish
    assert "dist/fsbpyr*\n            dist/THIRD_PARTY_LICENSES*.txt" in publish  # attested
    assert "dist/fsbpyr* dist/THIRD_PARTY_LICENSES*.txt dist/install.sh dist/SHA256SUMS" in publish
    # install.sh is made from the downloaded assets before they are attested, and attested too
    assert "tools/release/make-install.sh" in publish and "dist/install.sh dist/fsbpyr" in publish
    assert publish.index("make-install.sh") < publish.index("attest-build-provenance")
    assert "dist/THIRD_PARTY_LICENSES*.txt\n            dist/install.sh" in publish


@pytest.mark.parametrize(("suffix", "long_name"), [
    ("", "fsbpyr-x86_64-linux-gnu"), ("-3.12", "fsbpyr-3.12-x86_64-linux-gnu"),
])  # fmt: skip
def test_the_release_assets_are_named_with_the_target_and_the_short_names_are_copies(
    suffix: str, long_name: str, tmp_path: Path
) -> None:
    """The workflow's naming step and the compare job's selection, run under sh."""
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    naming = re.search(r"- name: Name the release assets\n\s+run: \|\n((?:          .*\n)+)",
                       workflow)
    assert naming is not None
    dist = tmp_path / "dist"
    dist.mkdir()
    for name, content in ((f"fsbpyr{suffix}", "exe"), (f"fsbpyr{suffix}.xz", "xz"),
                          (f"THIRD_PARTY_LICENSES{suffix}-x86_64-linux-gnu.txt", "notices")):
        (dist / name).write_text(content)
    env = {"PATH": os.environ["PATH"], "SUFFIX": suffix, "TARGET": "x86_64-linux-gnu"}

    sh, find = shutil.which("sh") or "sh", shutil.which("find") or "find"
    done = subprocess.run([sh, "-ec", textwrap.dedent(naming.group(1))], cwd=tmp_path, env=env,
                          capture_output=True, text=True, check=False)

    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in dist.iterdir()) == sorted([
        f"fsbpyr{suffix}", f"fsbpyr{suffix}.xz", long_name, f"{long_name}.xz",
        f"THIRD_PARTY_LICENSES{suffix}.txt", f"THIRD_PARTY_LICENSES{suffix}-x86_64-linux-gnu.txt",
    ])
    assert (dist / long_name).read_text() == (dist / f"fsbpyr{suffix}").read_text() == "exe"
    assert (dist / f"{long_name}.xz").read_text() == "xz"
    selected = subprocess.run(
        [find, str(dist), "-maxdepth", "1", "-type", "f", "-name", "fsbpyr*-x86_64-linux-gnu",
         "!", "-name", "*.xz"], capture_output=True, text=True, check=True)
    assert selected.stdout.split() == [str(dist / long_name)]  # compare's one executable


def test_publish_waits_for_the_independent_rebuild_to_match() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    jobs = dict(re.findall(r"^  ([a-z]+):\n(.*?)(?=^  [a-z]+:\n|\Z)", workflow, re.M | re.S))

    assert "    needs:" not in jobs["reproduce"]  # in parallel with build
    assert "needs: [build, reproduce]" in jobs["compare"]
    assert "needs: [build, compare]" in jobs["publish"]
    for job in ("build", "reproduce", "compare"):
        assert 'python: ["3.12", "3.13", "3.14"]' in jobs[job], job
    assert IMAGE_PIN.search(jobs["reproduce"])  # REP-1's pinned image


# The clang/lld 23 switch (SEC-4): tools/release/llvm23 pins LLVM's release tarball, and
# bootstrap.sh fetches it into the cache when FSBPYR_LLVM23=1.

LLVM23_NAME = "LLVM-23.1.3-Linux-X64"


def _llvm23_repo(tmp_path: Path, *, sha256: str | None = None) -> tuple[Path, Path, dict[str, str]]:
    """A copy of tools/ whose pin names a small tarball laid out like LLVM's, the tarball, and
    an environment with a cache under tmp_path and a fake uv that logs its calls."""
    zstd, tar = shutil.which("zstd"), shutil.which("tar")
    if zstd is None or tar is None:
        pytest.skip("zstd is not installed")
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "tools", root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    (root / ".python-version").write_text("3.14\n", encoding="utf-8")
    tree = tmp_path / "tree" / LLVM23_NAME
    write_program(tree / "bin/clang-23", 'echo "clang version 23.1.3"\n')
    (tree / "bin/clang").symlink_to("clang-23")
    write_program(tree / "bin/lld", 'echo "LLD 23.1.3"\n')
    (tree / "bin/ld.lld").symlink_to("lld")
    write_program(tree / "bin/clang-tidy", "exit 1\n")  # not needed to link: left out
    (tree / "lib/clang/23/include").mkdir(parents=True)
    tarball = tmp_path / "llvm.tar.zst"
    packed = subprocess.run([tar, "-C", str(tmp_path / "tree"), "-cf", "-", LLVM23_NAME],
                            capture_output=True, check=True)  # fmt: skip
    subprocess.run([zstd, "-q", "-o", str(tarball)], input=packed.stdout, check=True)
    digest = sha256 or hashlib.sha256(tarball.read_bytes()).hexdigest()
    (root / "tools/release/llvm23").write_text(f"23.1.3 {digest}\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    write_program(bin_dir / "uv", 'echo "uv $*" >> "$FAKE_UV_LOG"\n'
                                  '[ "$1" = --version ] && echo "uv 0.13.1"\nexit 0\n')  # fmt: skip
    env = {
        "PATH": f"{bin_dir}:{SYSTEM_PATH}",
        "HOME": str(tmp_path / "home"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "FAKE_UV_LOG": str(tmp_path / "uv.log"),
        "FSBPYR_LLVM23_URL": f"file://{tarball}",
    }
    return root, tarball, env


def test_llvm23_pin_names_a_version_and_a_sha256() -> None:
    version, sha256 = (ROOT / "tools/release/llvm23").read_text(encoding="utf-8").split()

    assert re.fullmatch(r"23\.\d+\.\d+", version)
    assert re.fullmatch(r"[0-9a-f]{64}", sha256)


def test_bootstrap_unpacks_only_what_linking_needs_and_fetches_once(tmp_path: Path) -> None:
    root, tarball, env = _llvm23_repo(tmp_path)

    first = run_script(["bash", str(root / "tools/bootstrap.sh"), "--llvm23"], env)
    tarball.unlink()  # a second run must not fetch again
    second = run_script(["bash", str(root / "tools/bootstrap.sh"), "--llvm23"], env)

    assert first.returncode == 0, first.stderr
    clang = Path(first.stdout.splitlines()[-1])
    assert clang.parent.parent.parent == tmp_path / "cache/fsbpyr/toolchains"
    assert clang.is_file() and (clang.parent / "ld.lld").exists()
    assert (clang.parent.parent / "lib/clang/23/include").is_dir()
    assert not (clang.parent / "clang-tidy").exists()
    assert (second.returncode, second.stdout.splitlines()[-1]) == (0, str(clang))
    assert [p.name for p in clang.parent.parent.parent.iterdir()] == [clang.parent.parent.name]


def test_bootstrap_fails_when_the_tarball_does_not_match_its_sha256(tmp_path: Path) -> None:
    root, _, env = _llvm23_repo(tmp_path, sha256="0" * 64)

    result = run_script(["bash", str(root / "tools/bootstrap.sh"), "--llvm23"], env)

    assert result.returncode == 1
    assert "sha256 of file://" in result.stderr and "0" * 64 in result.stderr
    assert list((tmp_path / "cache/fsbpyr/toolchains").iterdir()) == []  # nothing kept


def _runtime_build_call(tmp_path: Path) -> str:
    calls = (tmp_path / "uv.log").read_text(encoding="utf-8").splitlines()
    return next(c for c in calls if "runtime build" in c)


def test_bootstrap_links_with_the_system_clang_unless_fsbpyr_llvm23_is_set(
    tmp_path: Path,
) -> None:
    root, _, env = _llvm23_repo(tmp_path)

    # The fake uv builds nothing, so bootstrap stops at its first verify: only the call to
    # `runtime build` matters here.
    without = run_script(["bash", str(root / "tools/bootstrap.sh"), "--from-source"], env)
    plain = _runtime_build_call(tmp_path)
    (tmp_path / "uv.log").unlink()
    with_switch = run_script(
        ["bash", str(root / "tools/bootstrap.sh"), "--from-source"], {**env, "FSBPYR_LLVM23": "1"}
    )
    switched = _runtime_build_call(tmp_path)

    assert "links with clang/lld 22" in without.stdout and plain.endswith("runtime build")
    assert "links with clang/lld 23" in with_switch.stdout
    assert switched.startswith("uv run --locked fsbpyr runtime build --clang ")
    assert switched.endswith("/bin/clang")


def test_bootstrap_python_runs_one_variants_stages_in_its_own_environment(tmp_path: Path) -> None:
    root, _, env = _llvm23_repo(tmp_path)

    result = run_script(
        ["bash", str(root / "tools/bootstrap.sh"), "--python", "3.12", "--from-source"], env
    )
    calls = (tmp_path / "uv.log").read_text(encoding="utf-8").splitlines()

    assert "Locked environment on Python 3.12" in result.stdout
    assert "uv sync --python 3.12 --locked" in calls
    assert "uv run --python 3.12 --locked fsbpyr runtime build --python 3.12" in calls
    assert ("uv run --python 3.12 --locked fsbpyr build . --python 3.12 "
            "-o dist/stage1-3.12/fsbpyr") in calls
    assert not (root / "dist/stage1").exists()  # the default's directories stay untouched


def test_bootstrap_python_takes_a_minor_version(tmp_path: Path) -> None:
    root, _, env = _llvm23_repo(tmp_path)

    result = run_script(["bash", str(root / "tools/bootstrap.sh"), "--python", "latest"], env)

    assert result.returncode == 2
    assert result.stderr == "usage: bootstrap.sh --python X.Y (3.12, 3.13 or 3.14)\n"


def test_make_runs_on_the_python_given_on_its_command_line_only() -> None:
    make = shutil.which("make")
    if make is None:
        pytest.skip("make is not installed")
    env = {"PATH": SYSTEM_PATH, "HOME": "/nonexistent"}

    def dry_run(*arguments: str, **extra: str) -> str:
        result = run_script([make, "-n", *arguments], {**env, **extra}, cwd=ROOT)
        assert result.returncode == 0, result.stderr
        return result.stdout

    assert "uv run --python 3.12 --locked pytest" in dry_run("test-ci", "PYTHON=3.12")
    assert "uv run --locked ruff check" in dry_run("check", PYTHON="3.12")  # the environment's
    assert "--python 3.13 --locked fsbpyr build . --python 3.13 -o dist/fsbpyr-3.13" in dry_run(
        "build", "PYTHON=3.13"
    )
    assert "bootstrap.sh --python 3.14" in dry_run("bootstrap", "PYTHON=3.14")


def test_publish_job_makes_the_inspector_executable_before_building_install_sh() -> None:
    """download-artifact drops the executable bit, and make-install.sh runs the inspector."""
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    step = workflow.split("- name: Make install.sh", 1)[1].split("\n      # ", 1)[0]
    chmod = step.find("chmod +x dist/fsbpyr-x86_64-linux-gnu")
    assert 0 <= chmod < step.find("make-install.sh")
