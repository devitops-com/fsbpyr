"""The git state a build records, and the version string `fsbpyr --version` makes of it."""

import shutil
import subprocess
from pathlib import Path

import pytest

from fsbpyr.vcs import commit_time, git_state, version_string

COMMIT = "6457a95b" + "0" * 32
GIT = shutil.which("git") or "git"
LOCATION = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX")  # fmt: skip


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run([GIT, "-C", str(repo), *args], capture_output=True, text=True,
                            check=True)  # fmt: skip
    return result.stdout.strip()


def _commit(repo: Path, text: str) -> None:
    (repo / "f").write_text(text)
    _git(repo, "add", "f")
    _git(repo, "commit", "-qm", text)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")  # no signing or hooks from the user
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in LOCATION:  # set when the tests run from a git hook
        monkeypatch.delenv(name, raising=False)
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")
    _git(tmp_path, "init", "-q")
    _commit(tmp_path, "one")
    return tmp_path


def test_untagged_commits_record_no_tag(repo: Path) -> None:
    assert git_state(repo) == {"commit": _git(repo, "rev-parse", "HEAD"), "tag": None}


def test_release_tags_on_head_name_the_version(repo: Path) -> None:
    _git(repo, "tag", "-a", "v0.1.0", "-m", "release 0.1.0")
    (repo / "member").mkdir()  # projects below the repository root (workspace members)
    (repo / "member" / "pyproject.toml").write_text("")
    _git(repo, "add", "member")
    _git(repo, "commit", "-qm", "member")
    _git(repo, "tag", "v0.2.0")

    assert git_state(repo / "member") == {"commit": _git(repo, "rev-parse", "HEAD"),
                                          "tag": "v0.2.0"}  # fmt: skip
    _commit(repo, "two")
    assert git_state(repo) == {"commit": _git(repo, "rev-parse", "HEAD"), "tag": None}


def test_the_highest_release_tag_wins_and_others_are_ignored(repo: Path) -> None:
    for tag in ("v0.9.0", "v0.10.0", "v1.0.0", "v10", "v1.2.3-rc1", "nightly", "v2024-backup"):
        _git(repo, "tag", tag)

    state = git_state(repo)

    assert state is not None and state["tag"] == "v1.0.0"  # vX.Y.Z only, compared as versions


def test_directories_not_in_heads_tree_belong_to_no_commit(repo: Path) -> None:
    """A project copied into some other repository, or fsbpyr installed in a venv there."""
    for directory in (repo / ".venv" / "site-packages" / "fsbpyr", repo / "vendor" / "project"):
        directory.mkdir(parents=True)
        (directory / "pyproject.toml").write_text("")

        assert git_state(directory) is None


def test_nothing_is_recorded_without_a_repository_or_git(
    repo: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert git_state(tmp_path_factory.mktemp("plain")) is None
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert git_state(repo) is None


def test_inherited_repository_variables_are_ignored(
    repo: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path_factory.mktemp("other")
    _git(other, "init", "-q")
    _commit(other, "other")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))  # as in a deploy hook

    state = git_state(repo)

    assert state is not None and state["commit"] != _git(other, "rev-parse", "HEAD")


def test_git_failures_are_reported(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")  # git's "dubious ownership"
    warnings: list[str] = []

    assert git_state(repo, warn=warnings.append) is None
    assert len(warnings) == 1 and "dubious ownership" in warnings[0]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"commit": COMMIT, "tag": "v0.1.0"}, "0.1.0-6457a95b"),
        ({"commit": COMMIT, "tag": None}, "untagged-6457a95b"),
        (None, "untagged-unknown"),  # built without git, or from an sdist
        ({"tag": "v1.0.0"}, "untagged-unknown"),
    ],
)
def test_version_string(state: dict[str, str | None] | None, expected: str) -> None:
    assert version_string(state) == expected


def test_commit_time_is_heads(repo: Path) -> None:
    assert commit_time(repo) == int(_git(repo, "show", "--no-patch", "--format=%ct", "HEAD"))


def test_commit_time_needs_a_commit_that_contains_the_directory(tmp_path: Path, repo: Path) -> None:
    (repo / "later").mkdir()  # in the work tree only: HEAD's tree does not contain it
    assert commit_time(repo / "later") is None
    assert commit_time(tmp_path.parent / "no-such-directory") is None
