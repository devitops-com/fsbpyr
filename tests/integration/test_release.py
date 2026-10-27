"""release.sh and release-notes.sh against throwaway git repos, a bare origin and a fake gh.

release.sh only tags: it promotes the CHANGELOG (and, given a new version, bumps pyproject.toml,
src/fsbpyr/__init__.py and uv.lock to it), makes one commit, tags it and pushes the tag alone.
The pushed tag triggers .github/workflows/release.yml, which builds, attests, publishes and moves
main to the release commit; release.sh then just follows that run. Every guard must stop the
release before anything is committed, tagged or pushed. Everything lives under ``tmp_path``;
nothing reaches GitHub.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import platform
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
PREPARE_SH = ROOT / "tools" / "release" / "prepare.sh"
VERSION_FILES_SH = ROOT / "tools" / "release" / "version-files.sh"
GREEN_RUN_SH = ROOT / "tools" / "release" / "green-run.sh"
FOLLOW_RUN_SH = ROOT / "tools" / "release" / "follow-run.sh"
RELEASE_NOTES_SH = ROOT / "tools" / "release" / "release-notes.sh"
TAG = f"v{VERSION}"
RUN_ID = "4242"
FULL_RUN_ID = "4343"
RUN_URL = "https://github.com/devitops-com/fsbpyr/actions/runs"
DONE = "completed/success"
REPO_URL = "https://github.com/devitops-com/fsbpyr"

# A gh that is authenticated, logs every call and knows no release. `run list --branch main`
# answers $FAKE_GH_FULL_RUN for full-suite.yml (HEAD's run on main, as "ID STATUS/CONCLUSION
# URL"), $FAKE_GH_TESTED_RUN for the commit $FAKE_GH_TESTED_SHA and, without --commit, that
# commit as the recent runs' commits; nothing for another workflow or when it is empty; any
# other `run list` answers
# $FAKE_GH_RUN_ID from its (FAKE_GH_RUN_AFTER + 1)th call on. `run view` of the full suite's
# run (4000 too) shows it completed with FAKE_GH_CHECK_CONCLUSION, any other run with
# FAKE_GH_TAG_CONCLUSION (both "success" by default), as follow-run.sh's --jq prints it.
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
        case "$*" in
          *"--commit ${FAKE_GH_TESTED_SHA:-none} "*) echo "$FAKE_GH_TESTED_RUN" ;;
          *"--commit "*) echo "$FAKE_GH_FULL_RUN" ;;
          *) echo "${FAKE_GH_TESTED_SHA:-}" ;;
        esac
        exit ;;
      *"--branch main "*) exit ;;
    esac
    calls=$(cat "$FAKE_GH_LOG.runs" 2>/dev/null || echo 0)
    echo $((calls + 1)) > "$FAKE_GH_LOG.runs"
    if [ "$calls" -ge "${FAKE_GH_RUN_AFTER:-0}" ]; then echo "$FAKE_GH_RUN_ID"; fi ;;
  "run view")
    case "$3" in
      4000|4343) conclusion="${FAKE_GH_CHECK_CONCLUSION:-success}" ;;
      *) conclusion="${FAKE_GH_TAG_CONCLUSION:-success}" ;;
    esac
    printf '%s\n%s\tbuild\n' "$conclusion" "$conclusion" ;;
  *) exit 64 ;;
esac
"""


# ------------------------------------------------------------------- release.sh and prepare.sh

RELEASED_ENTRY = "### Added\n- The first thing.\n- The second thing."
PREPARED_DAY = "2026-10-22"


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
        "FAKE_GH_FULL_RUN": f"{FULL_RUN_ID} {DONE} {RUN_URL}/{FULL_RUN_ID}",
        "FSBPYR_RELEASE_WATCH_WAIT": "0",
        "FSBPYR_RELEASE_FOLLOW_POLL": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }


def pyproject_toml(version: str) -> str:
    """Only [project]'s version is the release version (the one the tag check in build.yml
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


def changelog(unreleased: str) -> str:
    return (
        "# Changelog\n\nIntro.\n\n## [Unreleased]\n\n"
        f"{unreleased}\n\n## [0.0.1] - 2026-01-01\n\n- Old.\n"
    )


def promoted_changelog(day: str, version: str = VERSION) -> str:
    return changelog(RELEASED_ENTRY).replace(
        "## [Unreleased]\n", f"## [Unreleased]\n\n## [{version}] - {day}\n"
    )


def make_release_repo(
    tmp_path: Path, env: dict[str, str], changelog_text: str | None = None, version: str = VERSION
) -> Path:
    """A repo on main at ``version``, in sync with a bare 'origin' under tmp_path, carrying the
    real release scripts. Its one commit, "initial", is that version's release commit (the
    version's CHANGELOG section, an empty [Unreleased]) unless ``changelog_text`` says
    otherwise."""
    repo, origin = tmp_path / "repo", tmp_path / "origin.git"
    (repo / "tools" / "release").mkdir(parents=True)
    for script in (RELEASE_SH, PREPARE_SH, VERSION_FILES_SH, RELEASE_NOTES_SH, GREEN_RUN_SH,
                   FOLLOW_RUN_SH):  # fmt: skip
        shutil.copy2(script, repo / "tools" / "release" / script.name)
    for name, text in version_files(version).items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    (repo / "README.md").write_text("fsbpyr\n")
    (repo / ".gitignore").write_text("dist/\n")
    (repo / "CHANGELOG.md").write_text(changelog_text or promoted_changelog(PREPARED_DAY, version))
    run_script(["git", "init", "-q", "--bare", "-b", "main", str(origin)], env)
    git(repo.parent, "init", "-q", "-b", "main", str(repo), env=env)
    git(repo, "add", ".", env=env)
    git(repo, "commit", "-q", "-m", "initial", env=env)
    git(repo, "remote", "add", "hub", str(origin), env=env)
    git(repo, "push", "-q", "hub", "main", env=env)
    return repo


def make_branch_repo(
    tmp_path: Path, env: dict[str, str], unreleased: str, version: str = VERSION
) -> Path:
    """A repo at ``version`` with ``unreleased`` under [Unreleased], on a new branch 'feature' off
    main: where make release-prep runs."""
    repo = make_release_repo(tmp_path, env, changelog(unreleased), version)
    git(repo, "checkout", "-q", "-b", "feature", env=env)
    return repo


def run_release(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return run_script(
        ["bash", "tools/release/release.sh", *args],
        env | {"FSBPYR_RELEASE_ASSUME_YES": "1"},
        cwd=repo,
    )


def run_prepare(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return run_script(["bash", "tools/release/prepare.sh", *args], env, cwd=repo)


def gh_calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "gh.log"
    return log.read_text().splitlines() if log.exists() else []


def tag_run_calls(tmp_path: Path, prefix: str = "run ") -> list[str]:
    """gh's calls about the tag's workflow run: not the check of main's runs before the tag."""
    checks = tuple(f"run view {i}" for i in (FULL_RUN_ID, "4000"))
    return [
        c
        for c in gh_calls(tmp_path)
        if c.startswith(prefix) and "--branch main " not in c and not c.startswith(checks)
    ]


def followed(tmp_path: Path) -> list[str]:
    """The runs follow-run.sh looked at, as "run view ID"."""
    return [" ".join(c.split()[:3]) for c in gh_calls(tmp_path) if c.startswith("run view ")]


def assert_nothing_built_or_published(tmp_path: Path) -> None:
    assert not [call for call in gh_calls(tmp_path) if call.startswith("release create")]


def assert_nothing_tagged_or_pushed(repo: Path, tmp_path: Path, env: dict[str, str]) -> None:
    assert git(repo, "tag", "--list", env=env) == ""
    assert git(repo, "status", "--porcelain", env=env) == ""
    assert git(repo, "log", "--format=%s", env=env) == "initial"
    origin = tmp_path / "origin.git"
    assert git(origin, "for-each-ref", "--format=%(refname) %(subject)", env=env) == (
        "refs/heads/main initial"
    )
    assert_nothing_built_or_published(tmp_path)
    assert not tag_run_calls(tmp_path)


def assert_released_at_origin(repo: Path, tmp_path: Path, env: dict[str, str]) -> None:
    """No commit: the annotated tag is on HEAD, the tested release commit, here and on origin,
    whose main is HEAD as before."""
    assert git(repo, "log", "--format=%s", env=env) == "initial"
    assert git(repo, "cat-file", "-t", TAG, env=env) == "tag"  # annotated
    assert git(repo, "tag", "-l", "--format=%(contents:subject)", TAG, env=env) == f"fsbpyr {TAG}"
    head = git(repo, "rev-parse", "HEAD", env=env)
    origin = tmp_path / "origin.git"
    assert git(origin, "rev-parse", "main", f"{TAG}^{{commit}}", env=env).split() == [head, head]


def released_files(repo: Path, env: dict[str, str]) -> list[str]:
    return sorted(git(repo, "show", "--name-only", "--format=", "HEAD", env=env).splitlines())


def test_release_refuses_to_run_off_main(tmp_path: Path, release_env: dict[str, str]) -> None:
    repo = make_release_repo(tmp_path, release_env)
    git(repo, "checkout", "-q", "-b", "feature", env=release_env)

    result = run_release(repo, release_env)

    assert result.returncode == 1
    assert "not on 'main' (currently on 'feature')" in result.stderr
    git(repo, "checkout", "-q", "main", env=release_env)
    assert_nothing_tagged_or_pushed(repo, tmp_path, release_env)


def test_release_refuses_a_dirty_tree(tmp_path: Path, release_env: dict[str, str]) -> None:
    repo = make_release_repo(tmp_path, release_env)
    (repo / "README.md").write_text("# edited\n")

    result = run_release(repo, release_env)

    assert result.returncode == 1
    assert "working tree is not clean" in result.stderr
    git(repo, "checkout", "--", "README.md", env=release_env)
    assert_nothing_tagged_or_pushed(repo, tmp_path, release_env)


def test_release_refuses_untracked_files_that_git_status_is_told_to_hide(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """The build packs untracked files under src/ into the executable: it would ship code that
    is not in the tag."""
    repo = make_release_repo(tmp_path, release_env)
    git(repo, "config", "status.showUntrackedFiles", "no", env=release_env)
    (repo / "tools" / "extra.py").write_text("print('not committed')\n")

    result = run_release(repo, release_env)

    assert result.returncode == 1
    assert "working tree is not clean" in result.stderr
    (repo / "tools" / "extra.py").unlink()
    assert_nothing_tagged_or_pushed(repo, tmp_path, release_env)


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_release_refuses_local_edits_hidden_from_git_status(
    flag: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env)
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
    repo = make_release_repo(tmp_path, release_env)
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


def test_release_refuses_a_head_without_a_full_suite_run_on_main(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """full-suite.yml runs on every push to main, the release build and its checks included;
    only a commit it passed is tagged. release.yml does not run on main: nothing asks for it."""
    repo = make_release_repo(tmp_path, release_env)
    head = git(repo, "rev-parse", "HEAD", env=release_env)

    result = run_release(repo, release_env | {"FAKE_GH_FULL_RUN": ""})

    assert result.returncode == 1
    assert f"main at {head[:7]} has no full-suite.yml run" in result.stderr
    assert_nothing_tagged_or_pushed(repo, tmp_path, release_env)
    check, recent = check_calls(tmp_path, "full-suite.yml")  # HEAD's run, then an ancestor's
    for option in ("--branch main", "--event push", f"--commit {head}"):
        assert option in check
    assert "--commit" not in recent
    assert not check_calls(tmp_path, "release.yml")


def _documented(repo: Path, env: dict[str, str], *changed: str) -> tuple[str, str]:
    """(the tested commit, HEAD): a commit on main, pushed, that changes ``changed`` after the
    repository's first commit, the one full-suite.yml ran for."""
    tested = git(repo, "rev-parse", "HEAD", env=env)
    for name in changed:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        with (repo / name).open("a") as file:
            file.write("# changed\n")
    git(repo, "add", ".", env=env)
    git(repo, "commit", "-q", "-m", "docs: more of them", env=env)
    git(repo, "push", "-q", "hub", "main", env=env)
    return tested, git(repo, "rev-parse", "HEAD", env=env)


def test_a_commit_that_changes_only_documentation_is_released_on_its_tested_ancestors_run(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """full-suite.yml leaves a documentation-only push out (CONTRIBUTING.md): no run of its own;
    the run of the newest commit before it that has one stands for it."""
    repo = make_release_repo(tmp_path, release_env)
    tested, head = _documented(repo, release_env, "README.md", "docs/guide.md", "docs/a/b.txt")
    env = release_env | {"FAKE_GH_FULL_RUN": "", "FAKE_GH_TESTED_SHA": tested,
                         "FAKE_GH_TESTED_RUN": f"4000 {DONE} {RUN_URL}/4000"}  # fmt: skip

    result = run_release(repo, env)

    assert result.returncode == 0, result.stderr
    assert f"changes only documentation since {tested[:7]}" in result.stderr
    origin = tmp_path / "origin.git"
    assert git(origin, "rev-parse", f"{TAG}^{{commit}}", env=release_env) == head


@pytest.mark.parametrize("changed", ["pyproject.toml", "src/fsbpyr/x.py", "tests/test_x.py",
                                     ".github/workflows/x.yml", "Makefile"])  # fmt: skip
def test_a_commit_that_changes_more_than_documentation_needs_its_own_run(
    changed: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env)
    tested, head = _documented(repo, release_env, "README.md", changed)
    env = release_env | {"FAKE_GH_FULL_RUN": "", "FAKE_GH_TESTED_SHA": tested,
                         "FAKE_GH_TESTED_RUN": f"4000 {DONE} {RUN_URL}/4000"}  # fmt: skip

    result = run_release(repo, env)

    assert result.returncode == 1
    assert f"not only documentation changed since {tested[:7]}" in result.stderr
    assert changed in result.stderr
    assert f"main at {head[:7]} has no full-suite.yml run" in result.stderr
    assert git(repo, "tag", "--list", env=release_env) == ""


@pytest.mark.parametrize("message", [
    "Merge pull request #36: docs [skip ci]", "docs [ci skip]", "docs [no ci]",
    "docs [skip actions]", "docs [actions skip]", "docs [Skip CI]",
    "docs\n\nskip-checks: true",
])  # fmt: skip
def test_release_refuses_a_head_whose_message_skips_ci(
    message: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    """GitHub applies a commit's skip instruction to a tag pushed on it: release.yml would never
    run (v0.25.0's first tag, 2026-10-25)."""
    repo = make_release_repo(tmp_path, release_env)
    git(repo, "commit", "-q", "--allow-empty", "-m", message, env=release_env)
    git(repo, "push", "-q", "hub", "main", env=release_env)

    result = run_release(repo, release_env)

    assert result.returncode == 1
    assert "HEAD's commit message has a CI skip instruction" in result.stderr
    assert git(repo, "tag", "--list", env=release_env) == ""
    assert not check_calls(tmp_path, "full-suite.yml")  # refused before the run is looked up


def test_a_message_that_only_mentions_skipping_is_released(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env)
    message = "ci: say when skip ci applies"  # no brackets: not an instruction
    git(repo, "commit", "-q", "--allow-empty", "-m", message, env=release_env)
    git(repo, "push", "-q", "hub", "main", env=release_env)

    result = run_release(repo, release_env)

    assert result.returncode == 0, result.stderr


def test_the_full_suite_leaves_out_the_pushes_a_release_may_stand_on_without_a_run() -> None:
    """full-suite.yml's paths-ignore and green-run.sh's documentation are the same files."""
    full_suite = (ROOT / ".github/workflows/full-suite.yml").read_text(encoding="utf-8")
    on = full_suite.split("\non:\n", 1)[1].split("\n\n", 1)[0]

    assert '    paths-ignore: ["**.md", "docs/**"]' in on
    assert "*.md|docs/*) ;;" in GREEN_RUN_SH.read_text(encoding="utf-8")


def test_a_documentation_commit_is_not_released_on_a_failed_ancestors_run(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env)
    tested, _ = _documented(repo, release_env, "README.md")
    failed = f"4000 completed/failure {RUN_URL}/4000"
    env = release_env | {"FAKE_GH_FULL_RUN": "", "FAKE_GH_TESTED_SHA": tested,
                         "FAKE_GH_TESTED_RUN": failed}  # fmt: skip

    result = run_release(repo, env)

    assert result.returncode == 1
    assert f"run for main at {tested[:7]} did not succeed" in result.stderr
    assert git(repo, "tag", "--list", env=release_env) == ""


@pytest.mark.parametrize("state", ["completed/failure", "completed/cancelled"])
def test_release_refuses_a_head_whose_full_suite_run_did_not_succeed(
    state: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env)

    result = run_release(repo, release_env | {"FAKE_GH_FULL_RUN": f"4000 {state} {RUN_URL}/4000"})

    assert result.returncode == 1
    assert "full-suite.yml's run for main" in result.stderr
    assert f"did not succeed ({state}): {RUN_URL}/4000" in result.stderr
    assert_nothing_tagged_or_pushed(repo, tmp_path, release_env)


def test_release_waits_for_a_full_suite_run_in_progress_and_goes_on_when_it_passes(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env)
    running = f"4000 in_progress/ {RUN_URL}/4000"

    result = run_release(repo, release_env | {"FAKE_GH_FULL_RUN": running})

    assert result.returncode == 0, result.stderr
    assert "run view 4000" in followed(tmp_path)
    assert_released_at_origin(repo, tmp_path, release_env)


@pytest.mark.parametrize("state", ["in_progress/", "queued/"])
def test_release_refuses_when_the_run_it_waited_for_fails(
    state: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env)
    env = release_env | {"FAKE_GH_FULL_RUN": f"4000 {state} {RUN_URL}/4000",
                         "FAKE_GH_CHECK_CONCLUSION": "failure"}  # fmt: skip

    result = run_release(repo, env)

    assert result.returncode == 1
    assert "run view 4000" in followed(tmp_path)
    assert "full-suite.yml's run for main" in result.stderr
    assert "failed" in result.stderr
    assert_nothing_tagged_or_pushed(repo, tmp_path, release_env)


@pytest.mark.parametrize("where", ["local", "origin", "github"])
def test_release_refuses_a_version_that_is_already_tagged_or_released(
    where: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env)
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


def test_release_refuses_a_head_that_is_not_a_release_commit(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """The release notes are the version's CHANGELOG section, which make release-prep makes."""
    repo = make_release_repo(tmp_path, release_env, changelog(RELEASED_ENTRY))

    result = run_release(repo, release_env)

    assert result.returncode == 1
    assert f"no release notes for {VERSION}: make release-prep makes them" in result.stderr
    assert_nothing_tagged_or_pushed(repo, tmp_path, release_env)


def test_release_refuses_changes_merged_after_the_release_commit(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """They would ship in the tag without being in its notes."""
    later = promoted_changelog(PREPARED_DAY).replace(
        "## [Unreleased]\n", "## [Unreleased]\n\n- A later change.\n"
    )
    repo = make_release_repo(tmp_path, release_env, later)

    result = run_release(repo, release_env)

    assert result.returncode == 1
    assert "CHANGELOG.md has changes under [Unreleased]" in result.stderr
    assert f"they would ship in {TAG} without being in its notes" in result.stderr
    assert_nothing_tagged_or_pushed(repo, tmp_path, release_env)


def test_release_refuses_a_version_other_than_the_one_head_declares(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env)

    result = run_release(repo, release_env, "9.9.9")

    assert result.returncode == 1
    assert f"HEAD declares version {VERSION}, not 9.9.9" in result.stderr
    assert "make release-prep VERSION=9.9.9" in result.stderr
    assert_nothing_tagged_or_pushed(repo, tmp_path, release_env)


def test_release_refuses_without_confirmation_off_a_tty(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """No controlling terminal: /dev/tty exists but cannot be opened."""
    repo = make_release_repo(tmp_path, release_env)

    result = run_script(["bash", "tools/release/release.sh"], release_env, cwd=repo)

    assert result.returncode == 1
    assert "FSBPYR_RELEASE_ASSUME_YES=1" in result.stderr
    assert_nothing_tagged_or_pushed(repo, tmp_path, release_env)


@pytest.mark.parametrize("args", [[], [VERSION]], ids=["no-version", "heads-version"])
def test_release_tags_head_and_pushes_the_tag_without_building_or_publishing(
    args: list[str], tmp_path: Path, release_env: dict[str, str]
) -> None:
    """The tag push is the release: CI builds, attests and publishes. release.sh makes no
    commit, follows the workflow run the tag triggered and prints the release URL."""
    repo = make_release_repo(tmp_path, release_env)

    result = run_release(repo, release_env, *args)

    assert result.returncode == 0, result.stderr
    assert_released_at_origin(repo, tmp_path, release_env)
    assert f"version : {VERSION}\n" in result.stdout
    assert_nothing_built_or_published(tmp_path)
    head = git(repo, "rev-parse", "HEAD", env=release_env)
    (run_list,) = tag_run_calls(tmp_path, "run list")
    for option in ("--workflow release.yml", f"--branch {TAG}", f"--commit {head}", "--event push"):
        assert option in run_list
    assert f"run view {RUN_ID}" in followed(tmp_path)
    assert f"{REPO_URL}/releases/tag/{TAG}" in result.stdout


def test_release_waits_for_the_workflow_run_to_show_up(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """GitHub creates the run a few seconds after the push."""
    repo = make_release_repo(tmp_path, release_env)
    env = release_env | {"FAKE_GH_RUN_AFTER": "1", "FSBPYR_RELEASE_WATCH_WAIT": "30"}

    result = run_release(repo, env)

    assert result.returncode == 0, result.stderr
    assert len(tag_run_calls(tmp_path, "run list")) == 2
    assert f"run view {RUN_ID}" in followed(tmp_path)


@pytest.mark.parametrize("answer", ["", "null"], ids=["empty", "null"])
def test_release_succeeds_when_no_workflow_run_shows_up(
    answer: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    """The tag is pushed and the workflow will run; only following it is not possible."""
    repo = make_release_repo(tmp_path, release_env)

    result = run_release(repo, release_env | {"FAKE_GH_RUN_ID": answer})

    assert result.returncode == 0, result.stderr
    assert_released_at_origin(repo, tmp_path, release_env)
    assert f"{REPO_URL}/actions/workflows/release.yml" in result.stdout
    assert not followed(tmp_path)


def test_release_reports_a_failed_workflow_run_with_its_recovery(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """A pushed tag is never moved: its version is burned, and the fix is released as the next,
    whose notes carry the burned version's changes too."""
    repo = make_release_repo(tmp_path, release_env)
    major, minor, patch = VERSION.split(".")
    following = f"{major}.{minor}.{int(patch) + 1}"

    result = run_release(repo, release_env | {"FAKE_GH_TAG_CONCLUSION": "failure"})

    assert result.returncode == 1
    assert_released_at_origin(repo, tmp_path, release_env)  # the tag stays pushed
    assert f"{TAG} is pushed but not published" in result.stderr
    assert f"gh run view {RUN_ID} --log-failed" in result.stderr
    assert f"gh run rerun {RUN_ID} --failed" in result.stderr
    assert f"this version is burned: {TAG} stays as it is" in result.stderr
    assert f"the [{VERSION}] section of CHANGELOG.md into [Unreleased]" in result.stderr
    assert f"top of CHANGELOG.md that {TAG} has no release" in result.stderr
    assert f"make release-prep VERSION={following}" in result.stderr
    assert "refs/tags" not in result.stderr
    assert "git tag" not in result.stderr


def test_release_reports_a_refused_tag_push_with_its_recovery(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """origin refuses the tag: nothing reaches origin, and the local tag can go."""
    repo = make_release_repo(tmp_path, release_env)
    write_program(
        tmp_path / "origin.git" / "hooks" / "update",
        'case "$1" in refs/tags/*) echo "tags are frozen" >&2; exit 1 ;; esac\n',
    )

    result = run_release(repo, release_env)

    assert result.returncode == 1
    assert "push failed" in result.stderr
    origin = tmp_path / "origin.git"
    assert git(origin, "tag", "--list", env=release_env) == ""
    assert git(repo, "tag", "--list", env=release_env) == TAG
    assert f"(a) Abort: git tag -d {TAG}\n" in result.stderr
    assert f"git push hub {TAG}\n" in result.stderr
    assert_nothing_built_or_published(tmp_path)
    assert not tag_run_calls(tmp_path)


@pytest.mark.parametrize("name", ["src/fsbpyr/__init__.py", "uv.lock"])
@pytest.mark.parametrize("script", ["release.sh", "prepare.sh"])
def test_release_and_prepare_refuse_versions_that_disagree(
    script: str, name: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env, version="1.9.2")
    if script == "prepare.sh":
        git(repo, "checkout", "-q", "-b", "feature", env=release_env)
    (repo / name).write_text(version_files("1.9.1")[name])
    git(repo, "commit", "-q", "-am", "drift", env=release_env)

    result = run_script(["bash", f"tools/release/{script}"], release_env, cwd=repo)

    assert result.returncode == 1
    assert f"{name} has version 1.9.1, pyproject.toml has 1.9.2" in result.stderr
    assert git(repo, "tag", "--list", env=release_env) == ""
    assert git(repo, "log", "-1", "--format=%s", env=release_env) == "drift"
    assert_nothing_built_or_published(tmp_path)


@pytest.mark.parametrize("script", ["release.sh", "prepare.sh"])
def test_release_and_prepare_take_at_most_one_version(
    script: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_release_repo(tmp_path, release_env)

    result = run_script(["bash", f"tools/release/{script}", "1.9.3", "1.9.4"], release_env,
                        cwd=repo)  # fmt: skip

    assert result.returncode == 2
    assert f"usage: {script} [X.Y.Z]" in result.stderr
    assert_nothing_tagged_or_pushed(repo, tmp_path, release_env)


# ---------------------------------------------------------------------------------- follow-run.sh

# A gh whose nth `run view` prints the file state-n of $FAKE_GH_DIR (the last one again once
# they run out), as follow-run.sh's --jq does: the run's state, then a "STATE<TAB>JOB" line per
# job. A file that says "error" is a failed request.
FAKE_GH_STATES = """\
n=$(($(cat "$FAKE_GH_DIR/calls" 2>/dev/null || echo 0) + 1))
echo "$n" > "$FAKE_GH_DIR/calls"
while [ ! -e "$FAKE_GH_DIR/state-$n" ]; do n=$((n - 1)); done
[ "$(cat "$FAKE_GH_DIR/state-$n")" != error ] || exit 1
cat "$FAKE_GH_DIR/state-$n"
"""


def follow_run(tmp_path: Path, *states: str) -> subprocess.CompletedProcess[str]:
    fake = tmp_path / "gh-bin"
    write_program(fake / "gh", FAKE_GH_STATES)
    for n, state in enumerate(states, 1):
        (fake / f"state-{n}").write_text(state + "\n")
    env = {"PATH": f"{fake}:{SYSTEM_PATH}", "FAKE_GH_DIR": str(fake),
           "FSBPYR_RELEASE_FOLLOW_POLL": "0"}  # fmt: skip
    return run_script(["bash", str(FOLLOW_RUN_SH), "7"], env)


def progress(result: subprocess.CompletedProcess[str]) -> list[str]:
    """The lines without their time, which each line starts with."""
    lines = result.stdout.splitlines()
    assert all(re.fullmatch(r"\d\d:\d\d:\d\d  \S.*", line) for line in lines), lines
    return [line[10:] for line in lines]


def test_follow_run_prints_a_plain_line_when_a_job_starts_and_when_it_ends(
    tmp_path: Path,
) -> None:
    """No terminal control: a line per change, nothing redrawn. The run's state ends the wait:
    GitHub has left jobs of a finished run in_progress. A new run has no jobs yet."""
    result = follow_run(
        tmp_path,
        "queued",
        "queued\nqueued\tbuild (a)\nqueued\tbuild (b)",
        "in_progress\nin_progress\tbuild (a)\nqueued\tbuild (b)",
        "in_progress\nin_progress\tbuild (a)\nqueued\tbuild (b)",
        "in_progress\nsuccess\tbuild (a)\nin_progress\tbuild (b)",
        "success\nsuccess\tbuild (a)\nin_progress\tbuild (b)",
    )

    assert result.returncode == 0, result.stderr
    assert progress(result) == [
        "in_progress  build (a)",
        "success  build (a)",
        "in_progress  build (b)",
        "run 7 succeeded",
    ]
    assert "\x1b" not in result.stdout + result.stderr


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_follow_run_fails_when_the_run_does_not_succeed(conclusion: str, tmp_path: Path) -> None:
    result = follow_run(tmp_path, f"{conclusion}\nsuccess\tbuild (a)\n{conclusion}\tbuild (b)")

    assert result.returncode == 1
    assert progress(result) == [
        "success  build (a)",
        f"{conclusion}  build (b)",
        f"run 7: {conclusion}",
    ]


def test_follow_run_rides_out_a_failed_request_and_gives_up_after_five(tmp_path: Path) -> None:
    passing = follow_run(tmp_path / "once", "error", "success\nsuccess\tbuild (a)")
    failing = follow_run(tmp_path / "always", "error")

    assert passing.returncode == 0, passing.stderr
    assert progress(passing)[-1] == "run 7 succeeded"
    assert failing.returncode == 1
    assert "ERROR: cannot read run 7" in failing.stderr
    assert (tmp_path / "always" / "gh-bin" / "calls").read_text() == "5\n"

# ---------------------------------------------------------- prepare.sh: the release commit

CURRENT = "1.9.2"  # pyproject.toml's version before a bump


def assert_nothing_committed(repo: Path, tmp_path: Path, env: dict[str, str]) -> None:
    """The branch as it was, nothing tagged, nothing pushed, no gh call."""
    assert git(repo, "status", "--porcelain", env=env) == ""
    assert git(repo, "log", "--format=%s", env=env) == "initial"
    assert git(repo, "tag", "--list", env=env) == ""
    origin = tmp_path / "origin.git"
    assert git(origin, "for-each-ref", "--format=%(refname) %(subject)", env=env) == (
        "refs/heads/main initial"
    )
    assert gh_calls(tmp_path) == []


@pytest.mark.parametrize("args", [[], [VERSION]], ids=["no-version", "the-current-version"])
def test_prepare_commits_the_release_commit_on_the_branch_without_pushing(
    args: list[str], tmp_path: Path, release_env: dict[str, str]
) -> None:
    """The last commit of the branch: its pull request merges it, and the full suite then tests
    exactly what is released. Given the version pyproject.toml already has, nothing is bumped."""
    repo = make_branch_repo(tmp_path, release_env, RELEASED_ENTRY)
    before = datetime.date.today().isoformat()

    result = run_prepare(repo, release_env, *args)

    after = datetime.date.today().isoformat()
    assert result.returncode == 0, result.stderr
    assert git(repo, "log", "--format=%s", env=release_env).splitlines() == [
        f"chore: release {TAG}",
        "initial",
    ]
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD", env=release_env) == "feature"
    assert released_files(repo, release_env) == ["CHANGELOG.md"]
    assert (repo / "CHANGELOG.md").read_text() in {
        promoted_changelog(day) for day in (before, after)
    }
    assert_version_files(repo, VERSION)
    assert git(repo, "tag", "--list", env=release_env) == ""
    origin = tmp_path / "origin.git"
    assert git(origin, "for-each-ref", "--format=%(refname) %(subject)", env=release_env) == (
        "refs/heads/main initial"
    )
    assert gh_calls(tmp_path) == []
    assert "make release" in result.stdout


@pytest.mark.parametrize("new", ["1.9.3", "1.9.10", "1.10.0", "2.0.0"])
def test_prepare_bumps_the_version_in_the_one_release_commit(
    new: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    """No separate bump commit: the release commit carries the new version and its CHANGELOG
    section. Versions compare as numbers: 1.9.10 > 1.9.2."""
    repo = make_branch_repo(tmp_path, release_env, RELEASED_ENTRY, version=CURRENT)

    result = run_prepare(repo, release_env, new)

    assert result.returncode == 0, result.stderr
    assert git(repo, "log", "-1", "--format=%s", env=release_env) == f"chore: release v{new}"
    assert released_files(repo, release_env) == [
        "CHANGELOG.md",
        "pyproject.toml",
        "src/fsbpyr/__init__.py",
        "uv.lock",
    ]
    assert_version_files(repo, new)
    day = git(repo, "log", "-1", "--format=%cs", env=release_env)
    assert (repo / "CHANGELOG.md").read_text() == promoted_changelog(day, new)


@pytest.mark.parametrize(
    "new",
    ["", "1.9", "1.9.3.0", "v1.9.3", "1.9.3rc1", "01.9.3", "1.09.3", "1.9.x", " 1.9.3", "1.9.3\n"],
)
def test_prepare_refuses_a_version_that_is_not_x_y_z(
    new: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_branch_repo(tmp_path, release_env, RELEASED_ENTRY, version=CURRENT)

    result = run_prepare(repo, release_env, new)

    assert result.returncode == 1
    assert "is not a version X.Y.Z" in result.stderr
    assert_nothing_committed(repo, tmp_path, release_env)


@pytest.mark.parametrize("new", ["1.9.1", "1.9.0", "1.8.10", "0.10.0"])
def test_prepare_refuses_a_version_lower_than_the_current_one(
    new: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_branch_repo(tmp_path, release_env, RELEASED_ENTRY, version=CURRENT)

    result = run_prepare(repo, release_env, new)

    assert result.returncode == 1
    assert f"version {new} is lower than the current version {CURRENT}" in result.stderr
    assert_nothing_committed(repo, tmp_path, release_env)


@pytest.mark.parametrize("where", ["local", "origin"])
def test_prepare_refuses_a_version_that_is_already_tagged(
    where: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_branch_repo(tmp_path, release_env, RELEASED_ENTRY, version=CURRENT)
    tag = "v1.9.3"
    git(repo, "tag", tag, env=release_env)
    if where == "origin":
        git(repo, "push", "-q", "hub", tag, env=release_env)
        git(repo, "tag", "-d", tag, env=release_env)

    result = run_prepare(repo, release_env, "1.9.3")

    expected = {"local": f"tag {tag} already exists locally",
                "origin": f"tag {tag} already exists on hub"}[where]  # fmt: skip
    assert result.returncode == 1
    assert expected in result.stderr
    assert git(repo, "log", "--format=%s", env=release_env) == "initial"
    assert git(repo, "status", "--porcelain", env=release_env) == ""


def test_prepare_reverts_the_bump_and_the_changelog_when_the_commit_fails(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_branch_repo(tmp_path, release_env, RELEASED_ENTRY, version=CURRENT)
    write_program(
        repo / ".git" / "hooks" / "pre-commit",
        "cp pyproject.toml ../pyproject-at-commit.toml\nexit 1\n",
    )

    result = run_prepare(repo, release_env, "1.9.3")

    assert result.returncode == 1
    assert (tmp_path / "pyproject-at-commit.toml").read_text() == pyproject_toml("1.9.3")
    (reverted,) = [line for line in result.stderr.splitlines() if "Reverted" in line]
    for name in ("CHANGELOG.md", "pyproject.toml", "src/fsbpyr/__init__.py", "uv.lock"):
        assert name in reverted
    assert_version_files(repo, CURRENT)
    assert (repo / "CHANGELOG.md").read_text() == changelog(RELEASED_ENTRY)
    assert_nothing_committed(repo, tmp_path, release_env)


def test_prepare_refuses_main(tmp_path: Path, release_env: dict[str, str]) -> None:
    """main changes by pull requests only: the release commit ends the branch that one merges."""
    repo = make_release_repo(tmp_path, release_env, changelog(RELEASED_ENTRY))

    result = run_prepare(repo, release_env)

    assert result.returncode == 1
    assert "on 'main': make the release commit on the branch" in result.stderr
    assert_nothing_committed(repo, tmp_path, release_env)


def test_prepare_refuses_a_dirty_tree(tmp_path: Path, release_env: dict[str, str]) -> None:
    repo = make_branch_repo(tmp_path, release_env, RELEASED_ENTRY)
    (repo / "README.md").write_text("# edited\n")

    result = run_prepare(repo, release_env)

    assert result.returncode == 1
    assert "working tree is not clean" in result.stderr
    git(repo, "checkout", "--", "README.md", env=release_env)
    assert_nothing_committed(repo, tmp_path, release_env)


def test_prepare_refuses_an_empty_unreleased_section(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    repo = make_branch_repo(tmp_path, release_env, "")

    result = run_prepare(repo, release_env)

    assert result.returncode == 1
    assert "'## [Unreleased]' section in CHANGELOG.md is empty" in result.stderr
    assert (repo / "CHANGELOG.md").read_text() == changelog("")
    assert_nothing_committed(repo, tmp_path, release_env)


def test_the_projects_versions_agree() -> None:
    """What prepare.sh and release.sh check, checked on every change."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    (locked,) = [p["version"] for p in lock["package"] if p["name"] == "fsbpyr"]
    init = (ROOT / "src" / "fsbpyr" / "__init__.py").read_text(encoding="utf-8")

    assert locked == VERSION
    assert f'__version__ = "{VERSION}"' in init


@pytest.mark.parametrize(("target", "script"), [("release", "release.sh"),
                                                ("release-prep", "prepare.sh")])  # fmt: skip
@pytest.mark.parametrize(
    ("args", "env", "version"),
    [
        ([], {}, ""),
        (["VERSION=1.2.3"], {}, " '1.2.3'"),
        # An exported VERSION (another tool's) is not a request for a version.
        ([], {"VERSION": "1.2.3"}, ""),
    ],
    ids=["plain", "command-line", "environment"],
)
def test_make_passes_only_a_command_line_version_to_the_release_scripts(
    target: str, script: str, args: list[str], env: dict[str, str], version: str
) -> None:
    result = run_script(["make", "-n", target, *args], {"PATH": SYSTEM_PATH} | env, ROOT)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"bash tools/release/{script}{version}"]


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
            ".github/workflows/build.yml",
            ".github/workflows/full-suite.yml",
            "Containerfile",
        )
    }

    # build and reproduce (REP-2) use the image, the suite and corpus jobs, and the
    # Containerfile once
    assert [len(found) for found in pins.values()] == [2, 2, 1], pins
    assert len({digest for found in pins.values() for digest in found}) == 1  # the same digest
    for path in pins:
        text = (ROOT / path).read_text(encoding="utf-8")
        assert not re.search(r"debian:trixie$", text, re.MULTILINE), path


def test_a_release_is_one_run_and_a_pull_request_starts_none() -> None:
    """release.yml runs for a pushed tag only and pushes nothing; release.sh pushes the tag
    alone, on the main commit the full suite tested. full-suite.yml runs for pushes to main
    (merges), weekly and on request. Neither runs for pull requests: anyone can open one, and a
    branch is tested locally first."""
    triggers = {}
    for name in ("release.yml", "full-suite.yml"):
        workflow = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        block = workflow.split("\non:\n", 1)[1].split("\n\n", 1)[0]
        triggers[name] = [line for line in block.splitlines() if not line.lstrip().startswith("#")]

    assert triggers["release.yml"] == ["  push:", '    tags: ["v*"]']
    assert triggers["full-suite.yml"][:2] == ["  push:", "    branches: [main]"]
    assert not [line for lines in triggers.values() for line in lines if "pull_request" in line]
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "refs/heads/main" not in release and "git push" not in release
    script = RELEASE_SH.read_text(encoding="utf-8")
    assert script.count("git push") == script.count("git push hub $TAG") + script.count(
        'git push hub "$TAG"') == 2


def test_the_publish_job_checks_the_tagged_commits_full_suite_run_and_names_it() -> None:
    """The tag is on the main commit the full suite tested: no release commit after it."""
    publish = workflow_jobs("release.yml")["publish"]

    assert "actions: read" in publish
    assert 'tools/release/green-run.sh full-suite.yml "$GITHUB_SHA"' in publish
    assert "fetch-depth: 0" in publish  # a documentation-only commit is compared with its ancestor
    assert "green-run.sh release.yml" not in publish and "HEAD^" not in publish
    assert "Full suite: %s" in publish


def test_the_full_suite_runs_on_main_and_weekly_once_per_python_version() -> None:
    workflow = (ROOT / ".github/workflows/full-suite.yml").read_text(encoding="utf-8")
    [weekly] = re.findall(r'^\s+- cron: "(.+)"$', workflow, re.MULTILINE)  # no nightly run

    assert "push:\n    branches: [main]" in workflow
    assert "workflow_dispatch:" in workflow
    assert f'WEEKLY="{weekly}"' in workflow  # the fuzzing time reads the weekly schedule
    # the suite, the corpus and the portability matrix
    assert len(re.findall(r'python: \["3.12", "3.13", "3.14", "3.15"\]', workflow)) == 3
    assert len(re.findall(r'target: \["x86_64-linux-gnu", "aarch64-linux-gnu"\]', workflow)) == 3
    assert "options: --privileged" in workflow and "timeout-minutes: 60" in workflow
    assert "--ignore=tests/e2e/test_portability.py" in workflow
    assert "--ignore=tests/e2e/test_corpus.py" in workflow  # the corpus job's
    assert "FSBPYR_TESTS_REQUIRE_ALL=full" in workflow
    assert 'git diff --quiet "$last" HEAD -- src/fsbpyr/launcher tests/fuzz' in workflow


def test_the_corpus_job_runs_make_test_corpus_with_nothing_skipped_by_surprise() -> None:
    workflow = (ROOT / ".github/workflows/full-suite.yml").read_text(encoding="utf-8")
    job = workflow.split("\n  corpus:\n")[1].split("\n  portability:\n")[0]

    assert "make test-corpus $MAKE_ARGS" in job
    assert "FSBPYR_TESTS_REQUIRE_ALL=full" in job
    assert "--junitxml=corpus.xml" in job
    assert "tmpfs" not in job  # big apps: temporary files on disk


def test_the_portability_job_runs_on_the_host_after_the_suite_with_nothing_skipped() -> None:
    workflow = (ROOT / ".github/workflows/full-suite.yml").read_text(encoding="utf-8")
    job = workflow.split("\n  portability:\n")[1]

    assert "needs: suite" in job and "runs-on: ${{ matrix.runner }}" in job
    assert "container:" not in job
    assert "tools/portability-apps.sh dist/fsbpyr$SUFFIX" in job
    assert "FSBPYR_TESTS_REQUIRE_ALL=portability" in job
    assert "FSBPYR_PORTABILITY_APPS=" in job and "--junitxml=portability.xml" in job
    assert "podman login docker.io" in job


def test_the_portability_tests_pull_images_without_a_network_in_them() -> None:
    source = (ROOT / "tests/e2e/test_portability.py").read_text(encoding="utf-8")

    assert '"--network=none"' in source


def test_uv_version_names_a_uv_that_the_build_backend_accepts() -> None:
    version, *digests = (ROOT / "tools/release/uv.version").read_text(encoding="utf-8").split()
    backend = next(
        Requirement(r)
        for r in tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
            "build-system"
        ]["requires"]
        if r.startswith("uv_build")
    )

    assert len(digests) == 2  # the x86_64 and the aarch64 tarball
    assert all(re.fullmatch(r"[0-9a-f]{64}", sha256) for sha256 in digests)
    assert backend.specifier.contains(version)  # uv_build is released with uv: same numbering


def test_no_build_uses_a_snapshot_of_the_debian_archive() -> None:
    """The build jobs take Debian's released packages from the image's own sources, and only
    the few the build uses: the runtime and the launcher are linked by the pinned LLVM."""
    for name in ("build.yml", "full-suite.yml", "release.yml"):
        workflow = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        assert "snapshot.debian.org" not in workflow and "apt-snapshot" not in workflow, name
    assert not (ROOT / "tools/release/apt-snapshot").exists()
    build = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    installs = re.findall(r"apt-get install -y -q --no-install-recommends (.+)", build)
    assert installs == ["ca-certificates git curl xz-utils make zstd"] * 2  # build, reproduce


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


def _bootstrap_on(tmp_path: Path, machine: str) -> subprocess.CompletedProcess[str]:
    """bootstrap.sh as a fake `uname -m` says, with podman and make that only say they ran."""
    bin_dir = tmp_path / "bin"
    write_program(bin_dir / "uname", f'echo {machine}\n')
    write_program(bin_dir / "podman", 'echo "podman $*"\n')
    write_program(bin_dir / "make", 'echo "make $*"\n')
    env = {"PATH": f"{bin_dir}:{SYSTEM_PATH}", "FSBPYR_BOOTSTRAP": "podman"}
    return run_script(["bash", str(BOOTSTRAP_SH)], env)


@pytest.mark.parametrize("machine", ["x86_64", "aarch64"])
def test_bootstrap_builds_on_both_architectures_in_the_hosts_container(
    tmp_path: Path, machine: str
) -> None:
    result = _bootstrap_on(tmp_path, machine)

    assert result.returncode == 0, result.stderr
    assert "podman build --target build" in result.stdout  # no --arch: the host's own
    assert "--arch" not in result.stdout


def test_bootstrap_refuses_other_architectures(tmp_path: Path) -> None:
    result = _bootstrap_on(tmp_path, "riscv64")

    assert result.returncode == 1
    assert "x86-64 and aarch64 only (this is riscv64)" in result.stderr


def test_the_containerfile_installs_the_pinned_uv_for_both_architectures() -> None:
    version, sha_x86_64, sha_aarch64 = (
        (ROOT / "tools/release/uv.version").read_text(encoding="utf-8").split()
    )
    containerfile = (ROOT / "Containerfile").read_text(encoding="utf-8")

    assert f"ARG UV_VERSION={version}\n" in containerfile
    assert f"ARG UV_SHA256_AMD64={sha_x86_64}\n" in containerfile
    assert f"ARG UV_SHA256_ARM64={sha_aarch64}\n" in containerfile
    assert "uv-aarch64-unknown-linux-gnu" in containerfile and "sha256sum -c" in containerfile


def _watch_env(tmp_path: Path, *, uv: str, image: str) -> tuple[Path, dict[str, str]]:
    """A copy of tools/ and the Containerfile with these pins, a gh that knows the newest uv
    and logs its calls, and a curl that answers like the registry."""
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "tools", root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    (root / "tools/release/uv.version").write_text(f"{uv} {'0' * 64}\n", encoding="utf-8")
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


def test_toolchain_watch_opens_an_issue_for_old_pins(tmp_path: Path) -> None:
    root, env = _watch_env(tmp_path, uv="0.13.1", image="sha256:" + "b" * 64)

    result = run_script(["bash", str(root / "tools/toolchain-watch.sh")], env)

    log = (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "issue create" in log and "--label toolchain-update" in log
    assert "2 release pin(s)" in log
    assert "not the newest release, 0.13.3" in log and "snapshot" not in log.lower()
    assert "sha256:" + "b" * 64 in log and "sha256:" + "a" * 64 in log


def test_toolchain_watch_is_silent_when_the_pins_are_current(tmp_path: Path) -> None:
    root, env = _watch_env(tmp_path, uv="0.13.3", image="sha256:" + "a" * 64)

    result = run_script(["bash", str(root / "tools/toolchain-watch.sh")], env)

    assert result.returncode == 0, result.stderr
    assert "Up to date" in result.stdout
    assert "issue" not in (tmp_path / "gh.log").read_text(encoding="utf-8")


def test_the_workflows_build_every_supported_python_and_the_release_publishes_each_variant(
) -> None:
    """The matrices list the Pythons fsbpyr supports (bytecode.SUPPORTED_PYTHON); every asset
    names its Python and target (`fsbpyr-3.14-x86_64-linux-gnu`), each with its .xz, SBOM and
    licence notices, all in SHA256SUMS and attested. No short or unlabeled names."""
    supported = sorted("{}.{}".format(*version) for version in SUPPORTED_PYTHON)
    default = (ROOT / ".python-version").read_text(encoding="utf-8").strip().rpartition(".")[0]
    assert default in supported  # 3.14 stays the default while 3.15 is new (owner, 2026-10-23)
    listed = ", ".join(f'"{version}"' for version in supported)
    for name in ("build.yml", "release.yml", "full-suite.yml"):
        workflow = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        assert f"python: [{listed}]" in workflow, name
    suite = (ROOT / ".github/workflows/full-suite.yml").read_text(encoding="utf-8")
    assert f"for version in {' '.join(supported)}; do" in suite  # the other runtimes

    workflow = "".join((ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
                       for name in ("build.yml", "release.yml"))  # fmt: skip
    jobs = _jobs()
    build, publish = jobs["build"], jobs["publish"]
    assert "make bootstrap $MAKE_ARGS" in build
    assert 'echo "MAKE_ARGS=PYTHON=$MATRIX_PYTHON"' in build
    assert "xz $filter --lzma2=preset=9e --threads=1 --keep dist/fsbpyr$SUFFIX" in build
    asset = "ASSET: fsbpyr-${{ matrix.python }}-${{ matrix.target }}"
    assert all(asset in jobs[job] for job in ("build", "compare", "sbom"))
    assert "NOTICES: THIRD_PARTY_LICENSES-${{ matrix.python }}-${{ matrix.target }}.txt" in build
    assert '> "dist/$NOTICES"' in build
    assert 'cp "dist/fsbpyr$SUFFIX$ext" "dist/$ASSET$ext"' in build
    uploads = re.findall(r"upload-artifact@.*\n\s+with:\n\s+name: (.+)\n\s+path: \|\n"
                         r"((?:            dist/.*\n)+)", build)
    assert [(name, [line.strip() for line in paths.splitlines()]) for name, paths in uploads] == [
        ("built-${{ matrix.python }}-${{ matrix.target }}", [
            "dist/${{ env.ASSET }}",
            "dist/${{ env.ASSET }}.xz",
            "dist/${{ env.NOTICES }}",
            "dist/${{ env.ASSET }}.cdx.json",
        ]),
    ]
    assert "short" not in workflow
    assert 'release_asset\\": \\"$ASSET\\"' in build  # each records its asset
    assert 'built="built/$ASSET"' in jobs["compare"]
    assert "pattern: built-*" in publish and "merge-multiple: true" in publish
    assert "sha256sum fsbpyr* THIRD_PARTY_LICENSES*.txt install.sh > SHA256SUMS" in publish
    assert "dist/fsbpyr*\n            dist/THIRD_PARTY_LICENSES*.txt" in publish  # attested
    assert "dist/fsbpyr* dist/THIRD_PARTY_LICENSES*.txt dist/install.sh dist/SHA256SUMS" in publish
    # install.sh is made from the downloaded assets before they are attested, and attested too
    assert 'make-install.sh "${TAG#v}" dist dist/install.sh\n' in publish
    assert publish.index("make-install.sh") < publish.index("attest-build-provenance")
    assert "dist/THIRD_PARTY_LICENSES*.txt\n            dist/install.sh" in publish


def _jobs() -> dict[str, str]:
    """build.yml's build, reproduce and compare, and release.yml's sbom and publish."""
    release = workflow_jobs("release.yml")
    return workflow_jobs("build.yml") | {"sbom": release["sbom"], "publish": release["publish"]}


def test_build_and_reproduce_make_the_sbom_from_their_executable_and_compare_compares_them(
) -> None:
    """REP-7: one CycloneDX file per long-named executable, from `inspect --verify --json`,
    made by build from its frozen artifact and by reproduce from its rebuild, byte for byte."""
    jobs = _jobs()
    build, reproduce, compare = jobs["build"], jobs["reproduce"], jobs["compare"]

    assert "inspect --verify --json dist/fsbpyr$SUFFIX" in build
    assert "tools/release/sbom.py" in build and '-o "dist/$ASSET.cdx.json"' in build
    assert "stage3$SUFFIX/fsbpyr inspect --verify --json" in reproduce
    # run as root with GitHub's HOME, which another user owns: 3.12's and 3.13's fsbpyr extract
    # backports.zstd into a cache, and a cache under that HOME is refused
    step = reproduce.split("name: Make the SBOM of the rebuild")[1].split("- name:")[0]
    assert "XDG_CACHE_HOME: /root/.cache" in step
    assert "tools/release/sbom.py" in reproduce
    assert "stage3$SUFFIX/fsbpyr.cdx.json" in reproduce
    upload = reproduce.split("name: Upload the rebuilt executable")[1]
    assert upload.count("rebuild/dist/stage3${{ env.SUFFIX }}/fsbpyr") == 2  # one directory
    assert 'sbom="built/$ASSET.cdx.json"' in compare
    assert "cmp " in compare.split("Compare the SBOMs byte for byte")[1]
    assert "rebuilt/fsbpyr.cdx.json" in compare


def test_each_sbom_is_attested_to_its_executable_by_a_pinned_action_and_published() -> None:
    jobs = _jobs()
    sbom, publish = jobs["sbom"], jobs["publish"]
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "needs: build\n" in sbom
    assert "if: github.event_name == 'push' && github.ref_type == 'tag'" in sbom
    assert "id-token: write" in sbom and "attestations: write" in sbom
    assert "name: built-${{ matrix.python }}-${{ matrix.target }}" in sbom
    assert re.search(r"uses: actions/attest-sbom@[0-9a-f]{40} # v\d+\.\d+\.\d+\n", sbom)
    assert "subject-path: built/${{ env.ASSET }}\n" in sbom
    assert "sbom-path: built/${{ env.ASSET }}.cdx.json" in sbom
    assert "needs: [build, sbom]" in publish  # nothing is released unattested
    assert "id-token: write" in publish  # still its own provenance
    assert "dist/fsbpyr*" in publish  # the SBOMs travel with the executables
    assert "actions/attest-sbom" in workflow and workflow.count("attest-sbom@") == 1
    assert "spdx" not in workflow.lower()


@pytest.mark.parametrize(("suffix", "python"), [("", "3.14"), ("-3.12", "3.12")])
def test_every_release_asset_is_named_with_its_python_and_target(
    suffix: str, python: str, tmp_path: Path
) -> None:
    """The workflow's naming step, run under sh: the newest Python's build is labeled too."""
    workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    naming = re.search(r"- name: Name the release assets\n\s+run: \|\n((?:          .*\n)+)",
                       workflow)
    assert naming is not None
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / f"fsbpyr{suffix}").write_text("exe")
    (dist / f"fsbpyr{suffix}.xz").write_text("xz")
    asset = f"fsbpyr-{python}-x86_64-linux-gnu"
    env = {"PATH": os.environ["PATH"], "SUFFIX": suffix, "ASSET": asset}

    sh = shutil.which("sh") or "sh"
    done = subprocess.run([sh, "-ec", textwrap.dedent(naming.group(1))],
                          cwd=tmp_path, env=env, capture_output=True, text=True, check=False)

    assert done.returncode == 0, done.stderr
    assert (dist / asset).read_text() == "exe"
    assert (dist / f"{asset}.xz").read_text() == "xz"


def test_the_full_suite_checks_the_build_against_an_independent_rebuild() -> None:
    """build.yml is the one build: the full suite calls it with its checks, the independent
    rebuild and its comparison included; a release calls it without."""
    jobs = workflow_jobs("build.yml")

    assert "    needs:" not in jobs["reproduce"]  # in parallel with build
    assert "needs: [build, reproduce]" in jobs["compare"]
    for job in ("reproduce", "compare"):
        assert "\n    if: inputs.checks\n" in jobs[job], job
    for job in ("build", "reproduce", "compare"):
        assert 'python: ["3.12", "3.13", "3.14", "3.15"]' in jobs[job], job
    assert IMAGE_PIN.search(jobs["reproduce"])  # REP-1's pinned image
    call = "    uses: ./.github/workflows/build.yml\n    with:\n      checks: {}\n"
    assert call.format("true") in workflow_jobs("full-suite.yml")["build"]
    assert call.format("false") in workflow_jobs("release.yml")["build"]


def test_the_release_build_runs_no_check_and_the_full_suite_runs_them_all() -> None:
    """Every check of the build is the full suite's (inputs.checks); what a release publishes is
    made either way. The full suite also takes the tests the build job ran before, the coverage
    gate of payload/ and elf.py included."""
    build = workflow_jobs("build.yml")["build"]
    steps = {step.split("\n", 1)[0]: step for step in build.split("\n      - name: ")[1:]}
    checks = {"Compare with stage 3", "The variant builds itself", "Smoke test",
              "The .xz decompresses to the executable"}  # fmt: skip

    for name, step in steps.items():
        assert ("inputs.checks" in step.split("run:", 1)[0]) == (name in checks), name
    assert checks <= steps.keys()
    assert "make test-ci" not in build and "make check" not in build
    suite = workflow_jobs("full-suite.yml")["suite"]
    assert "--include='src/fsbpyr/payload/*,src/fsbpyr/elf.py' --fail-under=90" in suite
    assert suite.index("- name: The whole suite") < suite.index("--fail-under=90")


# clang/lld 23 (SEC-4): tools/release/llvm23 pins LLVM's release tarball for each
# architecture, and bootstrap.sh fetches the host's into the cache unless FSBPYR_LLVM23=0.

LLVM23_NAME = f"LLVM-23.1.3-Linux-{'ARM64' if platform.machine() == 'aarch64' else 'X64'}"


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
    write_program(tree / "bin/llvm-profdata", "exit 0\n")  # read clang 23's profiles
    write_program(tree / "bin/llvm-cov", "exit 0\n")
    write_program(tree / "bin/clang-tidy", "exit 1\n")  # not needed: left out
    (tree / "lib/clang/23/include").mkdir(parents=True)
    tarball = tmp_path / "llvm.tar.zst"
    packed = subprocess.run([tar, "-C", str(tmp_path / "tree"), "-cf", "-", LLVM23_NAME],
                            capture_output=True, check=True)  # fmt: skip
    subprocess.run([zstd, "-q", "-o", str(tarball)], input=packed.stdout, check=True)
    digest = sha256 or hashlib.sha256(tarball.read_bytes()).hexdigest()
    other = "0" * 64  # the other architecture's tarball: never fetched here
    digests = (other, digest) if platform.machine() == "aarch64" else (digest, other)
    (root / "tools/release/llvm23").write_text(f"23.1.3 {' '.join(digests)}\n", encoding="utf-8")
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


def test_llvm23_pin_names_a_version_and_a_sha256_per_architecture() -> None:
    version, *digests = (ROOT / "tools/release/llvm23").read_text(encoding="utf-8").split()

    assert re.fullmatch(r"23\.\d+\.\d+", version)
    assert len(digests) == 2  # the x86-64 and the aarch64 tarball
    assert all(re.fullmatch(r"[0-9a-f]{64}", sha256) for sha256 in digests)


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
    assert (clang.parent / "llvm-profdata").is_file() and (clang.parent / "llvm-cov").is_file()
    assert not (clang.parent / "clang-tidy").exists()
    assert (second.returncode, second.stdout.splitlines()[-1]) == (0, str(clang))
    assert [p.name for p in clang.parent.parent.parent.iterdir()] == [clang.parent.parent.name]


def test_bootstrap_unpacks_again_a_tree_without_llvm_cov(tmp_path: Path) -> None:
    root, _, env = _llvm23_repo(tmp_path)
    first = run_script(["bash", str(root / "tools/bootstrap.sh"), "--llvm23"], env)
    clang = Path(first.stdout.splitlines()[-1])
    (clang.parent / "llvm-cov").unlink()  # as the opt-in switch of 0.21.0 and 0.22.0 left it

    again = run_script(["bash", str(root / "tools/bootstrap.sh"), "--llvm23"], env)

    assert again.returncode == 0, again.stderr
    assert (clang.parent / "llvm-cov").is_file()


def test_bootstrap_fails_when_the_tarball_does_not_match_its_sha256(tmp_path: Path) -> None:
    root, _, env = _llvm23_repo(tmp_path, sha256="0" * 64)

    result = run_script(["bash", str(root / "tools/bootstrap.sh"), "--llvm23"], env)

    assert result.returncode == 1
    assert "sha256 of file://" in result.stderr and "0" * 64 in result.stderr
    assert list((tmp_path / "cache/fsbpyr/toolchains").iterdir()) == []  # nothing kept


def _runtime_build_call(tmp_path: Path) -> str:
    calls = (tmp_path / "uv.log").read_text(encoding="utf-8").splitlines()
    return next(c for c in calls if "runtime build" in c)


def test_bootstrap_links_with_the_pinned_llvm_unless_fsbpyr_llvm23_is_0(tmp_path: Path) -> None:
    root, _, env = _llvm23_repo(tmp_path)

    # The fake uv builds nothing, so bootstrap stops at its first verify: only the call to
    # `runtime build` matters here.
    pinned = run_script(["bash", str(root / "tools/bootstrap.sh"), "--from-source"], env)
    with_pin = _runtime_build_call(tmp_path)
    (tmp_path / "uv.log").unlink()
    without = run_script(
        ["bash", str(root / "tools/bootstrap.sh"), "--from-source"], {**env, "FSBPYR_LLVM23": "0"}
    )
    on_path = _runtime_build_call(tmp_path)

    assert "links with LLVM's clang/lld 23.1.3" in pinned.stdout
    assert with_pin.startswith("uv run --locked fsbpyr runtime build --clang ")
    assert with_pin.endswith("/bin/clang")
    assert "links with clang/lld 23 or newer on PATH" in without.stdout
    assert on_path.endswith("runtime build")


def test_bootstrap_python_runs_one_variants_stages_in_its_own_environment(tmp_path: Path) -> None:
    root, _, env = _llvm23_repo(tmp_path)

    result = run_script(
        ["bash", str(root / "tools/bootstrap.sh"), "--python", "3.12", "--from-source"], env
    )
    calls = (tmp_path / "uv.log").read_text(encoding="utf-8").splitlines()

    assert "Locked environment on Python 3.12" in result.stdout
    assert "uv sync --python 3.12 --locked" in calls
    assert any(c.startswith("uv run --python 3.12 --locked fsbpyr runtime build --python 3.12 "
                            "--clang ") for c in calls)  # fmt: skip
    assert ("uv run --python 3.12 --locked fsbpyr build . --python 3.12 "
            "-o dist/stage1-3.12/fsbpyr") in calls
    assert not (root / "dist/stage1").exists()  # the default's directories stay untouched


def test_bootstrap_python_takes_a_minor_version(tmp_path: Path) -> None:
    root, _, env = _llvm23_repo(tmp_path)

    result = run_script(["bash", str(root / "tools/bootstrap.sh"), "--python", "latest"], env)

    assert result.returncode == 2
    assert result.stderr == "usage: bootstrap.sh --python X.Y (3.12 to 3.15)\n"


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
    chmod = step.find("chmod +x dist/fsbpyr-3.*-x86_64-linux-gnu")
    assert 0 <= chmod < step.find("make-install.sh")


# The target dimension (Phase 9, "More platforms", step 2): every build, reproduce, compare,
# suite and portability job runs once per (target, Python) on a runner of the target's
# architecture, and the release and its gate need all of them.

RUNNERS = {"x86_64-linux-gnu": "ubuntu-24.04", "aarch64-linux-gnu": "ubuntu-24.04-arm"}
MATRIX_JOBS = [
    ("build.yml", "build"), ("build.yml", "reproduce"), ("build.yml", "compare"),
    ("full-suite.yml", "suite"), ("full-suite.yml", "portability"),
]  # fmt: skip


def workflow_jobs(name: str) -> dict[str, str]:
    workflow = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
    return dict(re.findall(r"^  ([a-z]+):\n(.*?)(?=^  [a-z]+:\n|\Z)", workflow, re.M | re.S))


@pytest.mark.parametrize(("name", "job"), MATRIX_JOBS)
def test_each_job_runs_every_target_and_python_on_a_runner_of_the_targets_architecture(
    name: str, job: str
) -> None:
    from fsbpyr.targets import target_named  # noqa: PLC0415

    text = workflow_jobs(name)[job]
    matrix = text.split("    strategy:\n", 1)[1].split("\n    steps:", 1)[0]

    assert "    runs-on: ${{ matrix.runner }}\n" in text
    assert 'target: ["x86_64-linux-gnu", "aarch64-linux-gnu"]' in matrix
    assert 'python: ["3.12", "3.13", "3.14", "3.15"]' in matrix
    assert "fail-fast: false" in matrix
    includes = re.findall(r'- \{target: "([^"]+)", runner: "([^"]+)"\}', matrix)
    assert dict(includes) == RUNNERS and len(includes) == 2
    for target in RUNNERS:
        assert target_named(target).libc == "glibc"


def test_a_run_needs_every_job_to_pass_for_the_commit_to_be_released() -> None:
    """No job tolerates a failure, publish needs every build and SBOM attestation (aarch64
    too) and green-run.sh requires the run's conclusion, which is that of all its jobs."""
    for name in ("build.yml", "release.yml", "full-suite.yml"):
        for job, text in workflow_jobs(name).items():
            header = text.split("    steps:", 1)[0]
            assert "continue-on-error" not in header, (name, job)
    assert "needs: [build, sbom]" in workflow_jobs("release.yml")["publish"]
    assert "needs: suite" in workflow_jobs("full-suite.yml")["portability"]
    gate = GREEN_RUN_SH.read_text(encoding="utf-8")
    assert "completed/success) ;;" in gate and "--jobs" not in gate


def test_every_trigger_of_the_full_suite_runs_every_target_and_python() -> None:
    """A push to main is the run before a release (main goes to GitHub for a release only), so
    no job is conditional on the event: scheduled, push and manual runs are the same matrix."""
    jobs = workflow_jobs("full-suite.yml")

    for job in ("suite", "portability"):
        assert "github.event_name" not in jobs[job].split("    steps:", 1)[0]
        assert "matrix.target" in jobs[job]
    assert "\n    if:" not in jobs["suite"] + jobs["portability"]


def test_the_artifacts_of_a_matrix_entry_carry_its_target() -> None:
    for name in ("build.yml", "full-suite.yml"):
        workflow = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        for artifact in re.findall(r"^\s+name: ((?:built|reproduced|coverage|fsbpyr|full-suite|"
                                   r"portability)-\S.*)$", workflow, re.MULTILINE):
            if "matrix.python" in artifact:
                assert artifact.endswith("-${{ matrix.target }}") or artifact.startswith(
                    "built-short-"), artifact
    compare = workflow_jobs("build.yml")["compare"]
    assert "built-${{ matrix.python }}-${{ matrix.target }}" in compare
    assert "fuzz-corpus-${{ matrix.target }}-${{ matrix.python }}-" in (
        ROOT / ".github/workflows/full-suite.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("target", "filter_option"),
    [("x86_64-linux-gnu", "--x86"), ("aarch64-linux-gnu", "--arm64"), ("riscv64-linux-gnu", None)],
)
def test_the_release_xz_uses_the_filter_of_the_targets_architecture(
    target: str, filter_option: str | None, tmp_path: Path
) -> None:
    """The compress step under dash with an xz that records its arguments."""
    build = workflow_jobs("build.yml")["build"]
    step = re.search(r"- name: Compress the executable\n\s+run: \|\n((?:          .*\n)+)", build)
    assert step is not None
    write_program(tmp_path / "bin" / "xz", 'echo "$*" >> "$XZ_LOG"\n')
    env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "TARGET": target, "SUFFIX": "-3.12",
           "XZ_LOG": str(tmp_path / "xz.log")}
    dash = shutil.which("dash") or shutil.which("sh") or "sh"

    done = subprocess.run([dash, "-e", "-c", textwrap.dedent(step.group(1))], cwd=tmp_path,
                          env=env, capture_output=True, text=True, check=False)

    if filter_option is None:
        assert done.returncode == 1 and f"no xz filter for {target}" in done.stdout
        assert not (tmp_path / "xz.log").exists()
        return
    calls = (tmp_path / "xz.log").read_text().splitlines()
    assert calls == [f"{filter_option} --lzma2=preset=9e --threads=1 --keep dist/fsbpyr-3.12"]


UV_RELEASE_URL = "https://github.com/astral-sh/uv/releases/download"


def uv_installer_env(tmp_path: Path, arch: str) -> tuple[dict[str, str], str]:
    """A PATH with a uname that says ``arch`` and a curl that 'downloads' a tarball of fake uv
    and uvx; returns the environment and the sha256 of that tarball."""
    import io  # noqa: PLC0415
    import tarfile  # noqa: PLC0415

    name = f"uv-{arch}-unknown-linux-gnu"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for tool in ("uv", "uvx"):
            data = f"#!/bin/sh\necho {tool} for {arch}\n".encode()
            info = tarfile.TarInfo(f"{name}/{tool}")
            info.size, info.mode = len(data), 0o755
            archive.addfile(info, io.BytesIO(data))
    tarball = tmp_path / "served.tar.gz"
    tarball.write_bytes(buffer.getvalue())
    write_program(tmp_path / "bin" / "uname", f'echo "{arch}"\n')
    write_program(
        tmp_path / "bin" / "curl",
        'while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift ;; http*) url="$1" ;; esac;'
        ' shift; done\n'
        f'echo "$url" >> "{tmp_path}/curl.log"\ncp "{tarball}" "$out"\n',
    )
    return {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin"}, hashlib.sha256(
        tarball.read_bytes()).hexdigest()  # fmt: skip


def installer_copy(tmp_path: Path, version: str, x86_64: str, aarch64: str) -> Path:
    """tools/release/install-uv.sh next to a uv.version of the test's."""
    tools = tmp_path / "tools"
    tools.mkdir()
    shutil.copy(ROOT / "tools/release/install-uv.sh", tools)
    (tools / "uv.version").write_text(f"{version} {x86_64} {aarch64}\n")
    return tools / "install-uv.sh"


@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
def test_install_uv_fetches_the_tarball_of_the_machines_architecture_and_checks_its_digest(
    arch: str, tmp_path: Path
) -> None:
    env, digest = uv_installer_env(tmp_path, arch)
    other = "0" * 64
    script = installer_copy(tmp_path, "9.9.9", *((digest, other) if arch == "x86_64"
                                                  else (other, digest)))
    dash = shutil.which("dash") or shutil.which("sh") or "sh"

    done = run_script([dash, str(script), str(tmp_path / "out" / "bin")], env)

    assert done.returncode == 0, done.stderr
    assert done.stdout == "9.9.9\n"  # what FSBPYR_UV_VERSION is set to
    assert (tmp_path / "curl.log").read_text() == (
        f"{UV_RELEASE_URL}/9.9.9/uv-{arch}-unknown-linux-gnu.tar.gz\n")
    assert sorted(p.name for p in (tmp_path / "out" / "bin").iterdir()) == ["uv", "uvx"]
    assert os.access(tmp_path / "out" / "bin" / "uv", os.X_OK)


@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
def test_install_uv_refuses_a_tarball_that_does_not_match_the_digest_of_its_architecture(
    arch: str, tmp_path: Path
) -> None:
    env, digest = uv_installer_env(tmp_path, arch)
    # the other architecture's digest is the right one for the tarball: the wrong column fails
    script = installer_copy(tmp_path, "9.9.9", *(("1" * 64, digest) if arch == "x86_64"
                                                  else (digest, "1" * 64)))
    dash = shutil.which("dash") or shutil.which("sh") or "sh"

    done = run_script([dash, str(script), str(tmp_path / "out")], env)

    assert done.returncode != 0 and "FAILED" in done.stderr
    assert not (tmp_path / "out").exists()  # nothing unpacked


def test_install_uv_has_no_uv_for_another_architecture(tmp_path: Path) -> None:
    env, digest = uv_installer_env(tmp_path, "riscv64")
    script = installer_copy(tmp_path, "9.9.9", digest, digest)

    done = run_script(["/bin/sh", str(script), str(tmp_path / "out")], env)

    assert done.returncode == 1 and "no uv is pinned for riscv64" in done.stderr


def test_install_uv_takes_one_directory(tmp_path: Path) -> None:
    assert run_script(["/bin/sh", str(ROOT / "tools/release/install-uv.sh")], {}).returncode == 2


def test_the_workflows_install_uv_with_the_script_and_not_the_x86_64_tarball() -> None:
    for name in ("build.yml", "full-suite.yml"):
        workflow = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        assert "uv-x86_64-unknown-linux-gnu" not in workflow, name
        assert "sh tools/release/install-uv.sh" in workflow, name


# ------------------------------------------------------------------------- check-release.sh

CHECK_RELEASE_SH = ROOT / "tools" / "release" / "check-release.sh"


@pytest.mark.parametrize(("args", "message"), [
    (["1.2"], "'1.2' is not a version X.Y.Z"), (["v1.2.3"], "'v1.2.3' is not a version X.Y.Z"),
    (["1.2.3", "4.5.6"], "usage: check-release.sh [X.Y.Z]"),
])  # fmt: skip
def test_the_release_check_refuses_what_is_no_version(
    args: list[str], message: str, tmp_path: Path, release_env: dict[str, str]
) -> None:
    """Before it fetches anything: the fake gh logs every call."""
    write_program(tmp_path / "gh-bin" / "uv", "exit 0\n")  # present; nothing may run it

    result = run_script(["bash", str(CHECK_RELEASE_SH), *args], release_env)

    assert result.returncode == 2
    assert message in result.stderr
    assert gh_calls(tmp_path) == []


def test_the_release_check_refuses_a_companion_release_as_the_latest(
    tmp_path: Path, release_env: dict[str, str]
) -> None:
    """The EL8 companion el8-vX.Y.Z is no release to check, and the latest it must never be."""
    write_program(tmp_path / "gh-bin" / "gh", "echo el8-v1.2.3\n")
    write_program(tmp_path / "gh-bin" / "uv", "exit 0\n")

    result = run_script(["bash", str(CHECK_RELEASE_SH)], release_env)

    assert result.returncode == 2
    assert "the latest release is el8-v1.2.3, not a version tag" in result.stderr


def test_the_release_before_a_release_is_the_one_of_a_version_not_a_companion(
        tmp_path: Path) -> None:  # fmt: skip
    """A companion has its release's commit, so its date: the list has it right beside its
    release, as it had el8-v0.26.0 beside v0.26.0."""
    script = CHECK_RELEASE_SH.read_text(encoding="utf-8")
    match = re.search(r'^previous="\$\(gh release list .*?\)"$', script, re.MULTILINE | re.DOTALL)
    assert match, "the previous-release lookup is gone"
    write_program(tmp_path / "gh", "printf '%s\\n' el8-v0.3.0 v0.3.0 v0.2.0 el8-v0.2.0 v0.1.0\n")
    bash = shutil.which("bash") or "bash"

    result = subprocess.run([bash, "-c", f'{match.group(0)}\necho "$previous"'],
                            env={"PATH": f"{tmp_path}:/usr/bin:/bin", "TAG": "v0.2.0",
                                 "REPO": "x/y"},
                            capture_output=True, text=True, check=True)  # fmt: skip

    assert result.stdout.split() == ["v0.1.0"]
