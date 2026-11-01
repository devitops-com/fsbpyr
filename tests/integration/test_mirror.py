"""`make mirror`, tools/release/mirror.sh, with real git on two bare repositories: GitLab gets what
this checkout has, once GitHub is seen to have the same, and a branch goes only when main holds it.

What matters most is what does not happen: whenever the two disagree nothing moves, and a remote
that cannot be read is never taken for one with nothing to do."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ..support import requires

ROOT = Path(__file__).parents[2]


class Repositories:
    def __init__(self, root: Path) -> None:
        requires.tool("git")
        self.root = root
        self.tree = root / "tree"
        self.hub = root / "hub.git"
        self.origin = root / "origin.git"
        self.env = {**os.environ, "HOME": str(root / "home"), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}  # fmt: skip
        for bare in (self.hub, self.origin):
            self.git("init", "-q", "--bare", "-b", "main", bare, cwd=root)
        self.git("init", "-q", "-b", "main", self.tree, cwd=root)
        tools = self.tree / "tools" / "release"
        tools.mkdir(parents=True)
        shutil.copy2(ROOT / "tools" / "release" / "mirror.sh", tools / "mirror.sh")
        self.commit("first")
        self.git("remote", "add", "hub", self.hub)
        self.git("remote", "add", "origin", self.origin)
        self.push("main")

    def git(self, *args: str | Path, cwd: Path | None = None) -> str:
        done = subprocess.run(["git", *map(str, args)], cwd=cwd or self.tree, env=self.env,  # noqa: S607
                              check=True, capture_output=True, text=True)  # fmt: skip
        return done.stdout.strip()

    def commit(self, name: str, cwd: Path | None = None) -> str:
        (cwd or self.tree).joinpath(name).write_text(name)
        self.git("add", "-A", cwd=cwd)
        self.git("commit", "-q", "-m", name, cwd=cwd)
        return self.git("rev-parse", "HEAD", cwd=cwd)

    def push(self, *refs: str, to: tuple[str, ...] = ("hub", "origin")) -> None:
        for remote in to:
            self.git("push", "-q", remote, *refs)

    def merged_on_github(self, name: str) -> str:
        """A commit on main here and on GitHub, not yet on GitLab."""
        commit = self.commit(name)
        self.push("main", to=("hub",))
        return commit

    def branch(self, name: str) -> str:
        """A branch off main, on both remotes, with a commit main does not hold."""
        self.git("checkout", "-q", "-b", name)
        commit = self.commit(f"on-{name}")
        self.push(name)
        self.git("checkout", "-q", "main")
        return commit

    def clone(self) -> Path:
        """Another checkout of GitHub: what it commits, this one has never seen."""
        clone = self.root / "elsewhere"
        self.git("clone", "-q", self.hub, clone, cwd=self.root)
        return clone

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", "tools/release/mirror.sh", *args], cwd=self.tree,  # noqa: S607
                              env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, check=False)  # fmt: skip

    def refs(self, remote: Path) -> str:
        return self.git("ls-remote", remote)

    def has(self, remote: Path, ref: str) -> str:
        """The object ``ref`` names on ``remote``, by its whole name; nothing if it has none."""
        return next((line.split()[0] for line in self.refs(remote).splitlines()
                     if line.split()[1] == ref), "")  # fmt: skip

    def unchanged(self) -> tuple[str, str]:
        return self.refs(self.hub), self.refs(self.origin)


@pytest.fixture
def repos(tmp_path: Path) -> Repositories:
    return Repositories(tmp_path)


# --- main ---------------------------------------------------------------------------------------


def test_gitlab_main_follows(repos: Repositories) -> None:
    commit = repos.merged_on_github("merged")

    done = repos.run()

    assert done.returncode == 0, done.stderr
    assert repos.has(repos.origin, "refs/heads/main") == commit


def test_running_it_again_changes_nothing(repos: Repositories) -> None:
    repos.merged_on_github("merged")
    repos.run()
    before = repos.unchanged()

    done = repos.run()

    assert done.returncode == 0, done.stderr
    assert repos.unchanged() == before


def test_with_nothing_to_do_it_says_so(repos: Repositories) -> None:
    done = repos.run()

    assert done.returncode == 0, done.stderr
    assert "nothing to do" in done.stdout


def test_a_checkout_behind_github_pushes_nothing(repos: Repositories) -> None:
    """GitHub moved: that is the admin's to look at, and may be someone else's commit."""
    clone = repos.clone()
    repos.commit("not-looked-at", cwd=clone)
    repos.git("push", "-q", "origin", "main", cwd=clone)
    before = repos.unchanged()

    done = repos.run()

    assert done.returncode == 1
    assert "GitHub's main" in done.stderr
    assert repos.unchanged() == before


def test_a_checkout_ahead_of_github_pushes_nothing(repos: Repositories) -> None:
    repos.commit("not-on-github")
    before = repos.unchanged()

    done = repos.run()

    assert done.returncode == 1
    assert "GitHub's main" in done.stderr
    assert repos.unchanged() == before


@pytest.mark.parametrize("checkout", [("-b", "feature"), ("--detach",)],
                         ids=["a branch", "no branch"])  # fmt: skip
def test_only_main_is_mirrored(repos: Repositories, checkout: tuple[str, ...]) -> None:
    repos.merged_on_github("merged")
    repos.git("checkout", "-q", *checkout)
    before = repos.unchanged()

    done = repos.run()

    assert done.returncode == 1
    assert "on main" in done.stderr
    assert repos.unchanged() == before


def test_gitlab_ahead_of_github_stops_everything(repos: Repositories) -> None:
    """Main only moves forward: a commit GitHub lacks is lost to no push."""
    clone = repos.root / "elsewhere"
    repos.git("clone", "-q", repos.origin, clone, cwd=repos.root)
    repos.commit("only-on-gitlab", cwd=clone)
    repos.git("push", "-q", "origin", "main", cwd=clone)
    repos.merged_on_github("merged")
    repos.git("tag", "v1.2.0")
    repos.push("refs/tags/v1.2.0", to=("hub",))
    before = repos.unchanged()

    done = repos.run()

    assert done.returncode == 1
    assert "GitLab's main" in done.stderr
    assert repos.unchanged() == before


# --- tags ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("tag", ["v1.2.0", "el8-v1.2.0"])
@pytest.mark.parametrize("flags", [(), ("-a", "-m", "release")], ids=["light", "annotated"])
def test_a_release_tag_follows(repos: Repositories, tag: str, flags: tuple[str, ...]) -> None:
    repos.merged_on_github("merged")
    repos.git("tag", *flags, tag)
    repos.push(f"refs/tags/{tag}", to=("hub",))

    done = repos.run()

    assert done.returncode == 0, done.stderr
    assert repos.has(repos.origin, f"refs/tags/{tag}") == repos.has(repos.hub, f"refs/tags/{tag}")


def test_a_tag_this_checkout_has_never_seen_follows(repos: Repositories) -> None:
    """CI pushed it, say: an annotated tag is an object of its own, which is not here yet."""
    clone = repos.clone()
    repos.git("tag", "-a", "-m", "release", "v1.2.0", cwd=clone)
    repos.git("push", "-q", "origin", "refs/tags/v1.2.0", cwd=clone)

    done = repos.run()

    assert done.returncode == 0, done.stderr
    assert repos.has(repos.origin, "refs/tags/v1.2.0") == repos.has(repos.hub, "refs/tags/v1.2.0")
    assert not repos.git("tag", "--list", "v1.2.0")


def test_another_tag_does_not_follow(repos: Repositories) -> None:
    repos.git("tag", "nightly")
    repos.push("refs/tags/nightly", to=("hub",))

    assert repos.run().returncode == 0
    assert not repos.has(repos.origin, "refs/tags/nightly")


def test_a_tag_gitlab_has_at_another_commit_stops_everything(repos: Repositories) -> None:
    """A tag never moves: on one remote or the other, someone moved it."""
    first = repos.git("rev-parse", "HEAD")
    repos.git("tag", "v1.2.0")
    repos.push("refs/tags/v1.2.0", to=("origin",))
    repos.git("tag", "-d", "v1.2.0")
    repos.merged_on_github("merged")
    repos.git("tag", "v1.2.0")
    repos.push("refs/tags/v1.2.0", to=("hub",))
    before = repos.unchanged()

    done = repos.run()

    assert done.returncode == 1
    assert "v1.2.0" in done.stderr and "never moves" in done.stderr
    assert repos.unchanged() == before
    assert repos.has(repos.origin, "refs/tags/v1.2.0") == first


def test_a_tag_this_checkout_has_at_another_commit_stops_everything(repos: Repositories) -> None:
    repos.git("tag", "v1.2.0")
    repos.merged_on_github("merged")
    clone = repos.clone()
    repos.git("tag", "v1.2.0", cwd=clone)
    repos.git("push", "-q", "origin", "refs/tags/v1.2.0", cwd=clone)
    before = repos.unchanged()

    done = repos.run()

    assert done.returncode == 1
    assert "this checkout has v1.2.0" in done.stderr and "never moves" in done.stderr
    assert repos.unchanged() == before


def test_a_tag_outside_main_stops_everything(repos: Repositories) -> None:
    """A release tag on a side commit is no release of this main, and leaves nothing here."""
    clone = repos.clone()
    repos.git("checkout", "-q", "-b", "side", cwd=clone)
    repos.commit("side", cwd=clone)
    repos.git("tag", "v1.2.0", cwd=clone)
    repos.git("push", "-q", "origin", "refs/tags/v1.2.0", cwd=clone)
    repos.merged_on_github("merged")
    before = repos.unchanged()

    done = repos.run()

    assert done.returncode == 1
    assert "v1.2.0 is not on main" in done.stderr
    assert repos.unchanged() == before
    assert not repos.git("tag", "--list", "v1.2.0")


# --- branches -----------------------------------------------------------------------------------


def test_a_merged_branch_goes_from_both(repos: Repositories) -> None:
    repos.branch("feature")
    repos.git("merge", "-q", "--no-ff", "-m", "merged", "feature")
    repos.push("main", to=("hub",))

    done = repos.run()

    assert done.returncode == 0, done.stderr
    assert not repos.has(repos.hub, "refs/heads/feature")
    assert not repos.has(repos.origin, "refs/heads/feature")
    assert "feature" in done.stdout


@pytest.mark.parametrize("remote", ["hub", "origin"])
def test_a_branch_merged_and_left_on_one_remote_goes_from_it(
    repos: Repositories, remote: str
) -> None:
    other = "origin" if remote == "hub" else "hub"
    repos.branch("feature")
    repos.git("push", "-q", other, "--delete", "feature")
    repos.git("merge", "-q", "--no-ff", "-m", "merged", "feature")
    repos.push("main", to=("hub",))

    done = repos.run()

    assert done.returncode == 0, done.stderr
    assert not repos.has(getattr(repos, remote), "refs/heads/feature")


def test_a_branch_with_a_commit_main_lacks_stays(repos: Repositories) -> None:
    commit = repos.branch("feature")

    done = repos.run()

    assert done.returncode == 0, done.stderr
    assert repos.has(repos.hub, "refs/heads/feature") == commit
    assert repos.has(repos.origin, "refs/heads/feature") == commit


def test_a_branch_of_commits_never_seen_here_stays(repos: Repositories) -> None:
    clone = repos.clone()
    repos.git("checkout", "-q", "-b", "theirs", cwd=clone)
    commit = repos.commit("theirs", cwd=clone)
    repos.git("push", "-q", "origin", "theirs", cwd=clone)

    done = repos.run()

    assert done.returncode == 0, done.stderr
    assert repos.has(repos.hub, "refs/heads/theirs") == commit


def test_a_branch_outrunning_its_merge_stays(repos: Repositories) -> None:
    """Merged, and then a commit more: main does not hold that one."""
    repos.branch("feature")
    repos.git("merge", "-q", "--no-ff", "-m", "merged", "feature")
    repos.push("main", to=("hub",))
    repos.git("checkout", "-q", "feature")
    commit = repos.commit("after-the-merge")
    repos.push("feature")
    repos.git("checkout", "-q", "main")

    done = repos.run()

    assert done.returncode == 0, done.stderr
    assert repos.has(repos.hub, "refs/heads/feature") == commit


# --- what stops it ------------------------------------------------------------------------------


def _refuse_tags(repos: Repositories) -> None:
    hook = repos.origin / "hooks" / "update"
    hook.write_text('#!/bin/sh\ncase "$1" in refs/tags/*) exit 1 ;; esac\n')
    hook.chmod(0o755)


def test_a_push_gitlab_refuses_in_part_moves_nothing(repos: Repositories) -> None:
    """Main and the tags go in one atomic push, and no branch goes after a failed one."""
    repos.branch("feature")
    repos.git("merge", "-q", "--no-ff", "-m", "merged", "feature")
    repos.push("main", to=("hub",))
    repos.git("tag", "v1.2.0")
    repos.push("refs/tags/v1.2.0", to=("hub",))
    _refuse_tags(repos)
    before = repos.has(repos.origin, "refs/heads/main")

    done = repos.run()

    assert done.returncode != 0
    assert repos.has(repos.origin, "refs/heads/main") == before
    assert repos.has(repos.hub, "refs/heads/feature")
    assert repos.has(repos.origin, "refs/heads/feature")


@pytest.mark.parametrize("remote", ["hub", "origin"])
def test_a_remote_that_cannot_be_read_stops_it(repos: Repositories, remote: str) -> None:
    """Only its listing fails, and its pushes would work: an unread remote is no empty one."""
    repos.merged_on_github("merged")
    repos.git("branch", "--no-track", "feature", "HEAD~1")
    repos.push("feature")
    shim = repos.root / "bin" / "git"
    shim.parent.mkdir()
    shim.write_text(f'''#!/bin/sh
[ "$1 $4" != "ls-remote {remote}" ] || exit 128
exec {shutil.which("git")} "$@"
''')
    shim.chmod(0o755)
    repos.env = {**repos.env, "PATH": f"{shim.parent}:{repos.env['PATH']}"}
    before = repos.unchanged()

    done = repos.run()

    assert done.returncode != 0
    assert repos.unchanged() == before


def test_it_takes_no_arguments(repos: Repositories) -> None:
    done = repos.run("feature")

    assert done.returncode == 2
    assert "usage" in done.stderr


def test_make_mirror_runs_it() -> None:
    env = {name: value for name, value in os.environ.items()
           if name not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")}  # fmt: skip
    done = subprocess.run([requires.tool("make"), "-s", "-n", "mirror"], cwd=ROOT, env=env,
                          capture_output=True, text=True, check=False)  # fmt: skip

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "bash tools/release/mirror.sh"
