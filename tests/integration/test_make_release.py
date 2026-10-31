"""`make release`, tools/release/release.sh: the order of its parts, what stops it, and that it can
go on after a stop. The parts have their own tests: here they are stand-ins that say they ran."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ..support import requires
from ..support.signing import on_a_terminal

ROOT = Path(__file__).parents[2]
PART = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$LOG"
case " ${FAIL-} " in *" $(basename "$0") "*) exit 1 ;; esac
"""
FAKE_GH = """#!/usr/bin/env bash
case "$1 $2" in
    "run list")
        case "$*" in
            *release.yml*) echo 1 ;;
            *el8.yml*) [ -n "${NO_EL8_RUN-}" ] || echo 2 ;;
        esac ;;
    "release view")
        case "$*" in
            *"--json isDraft"*) echo "${DRAFT:-true}" ;;
            *) echo https://example.invalid/release ;;
        esac ;;
esac
"""
FAKE_FOLLOW = """#!/usr/bin/env bash
echo "follow-run.sh $*" >> "$LOG"
case " ${FAILED_RUNS-} " in *" $1 "*) exit 1 ;; esac
"""
FAKE_MAKE = """#!/usr/bin/env bash
echo "make $*" >> "$LOG"
case " ${FAIL-} " in *" make-$3 "* | *" make-$2 "*) exit 1 ;; esac
"""


class Tree:
    def __init__(self, root: Path) -> None:
        requires.tool("git")
        self.tree = root / "tree"
        self.hub = root / "hub.git"
        self.log = root / "log"
        self.log.write_text("")
        self.bin = root / "bin"
        self.bin.mkdir()
        tools = self.tree / "tools" / "release"
        tools.mkdir(parents=True)
        for name in ("release.sh", "version-files.sh"):
            shutil.copy2(ROOT / "tools" / "release" / name, tools / name)
        for name in ("release-assets.sh", "release-sign.sh"):
            (tools / name).write_text(PART)
        (tools / "follow-run.sh").write_text(FAKE_FOLLOW)
        for name, text in (("gh", FAKE_GH), ("make", FAKE_MAKE)):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        (self.tree / "pyproject.toml").write_text('[project]\nname = "fsbpyr"\nversion = "1.2.0"\n')
        (self.tree / "uv.lock").write_text('[[package]]\nname = "fsbpyr"\nversion = "1.2.0"\n')
        (self.tree / "src" / "fsbpyr").mkdir(parents=True)
        (self.tree / "src" / "fsbpyr" / "__init__.py").write_text('__version__ = "1.2.0"\n')
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "LOG": str(self.log),
                    "HOME": str(root / "home"), "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@example.org", "FSBPYR_RELEASE_WATCH_WAIT": "0",
                    "FSBPYR_RELEASE_KEY_FILE": "/key/k.key"}  # fmt: skip
        self.env.pop("FAIL", None)
        self.git("init", "-q", "--bare", "-b", "main", self.hub, cwd=root)
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "release")
        self.git("remote", "add", "hub", self.hub)
        self.git("push", "-q", "hub", "main")

    def git(self, *args: str | Path, cwd: Path | None = None) -> None:
        subprocess.run(["git", *map(str, args)], cwd=cwd or self.tree, env=self.env, check=True,  # noqa: S607
                       capture_output=True)  # fmt: skip

    def tag_on_github(self, name: str = "v1.2.0") -> None:
        self.git("tag", name)
        self.git("push", "-q", "hub", f"refs/tags/{name}")

    def run(self, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", "tools/release/release.sh"], cwd=self.tree,  # noqa: S607
                              env={**self.env, **env}, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, start_new_session=True,
                              check=False)  # fmt: skip

    def parts(self) -> list[str]:
        return self.log.read_text().splitlines()


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
    return Tree(tmp_path)


WHOLE = ["release-assets.sh 1.2.0", "follow-run.sh 2", "release-sign.sh /key/k.key 1.2.0",
         "make --no-print-directory release-check VERSION=1.2.0",
         "make --no-print-directory wiki-publish"]  # fmt: skip


def test_it_tags_waits_signs_checks_and_publishes_the_wiki_in_that_order(tree: Tree) -> None:
    done = tree.run()

    assert done.returncode == 0, done.stderr
    assert tree.parts() == WHOLE
    assert "Released fsbpyr v1.2.0" in done.stdout


def test_after_a_stop_it_goes_on_from_the_tag(tree: Tree) -> None:
    tree.tag_on_github()

    done = tree.run()

    assert done.returncode == 0, done.stderr
    assert tree.parts() == ["follow-run.sh 1", *WHOLE[1:]]


def test_a_tag_whose_name_only_ends_like_it_is_no_tag(tree: Tree) -> None:
    """ls-remote's patterns match the tail of a ref."""
    tree.tag_on_github("w/refs/tags/v1.2.0")

    assert tree.run().returncode == 0
    assert tree.parts() == WHOLE


def test_with_everything_signed_it_needs_no_key_and_goes_on(tree: Tree) -> None:
    tree.tag_on_github()

    done = tree.run(DRAFT="false", FSBPYR_RELEASE_KEY_FILE="")

    assert done.returncode == 0, done.stderr
    assert tree.parts() == ["follow-run.sh 1", "follow-run.sh 2", *WHOLE[3:]]


@pytest.mark.parametrize(("failed", "ran"), [
    ("release-assets.sh", 1),
    ("release-sign.sh", 3),
    ("make-release-check", 4),
], ids=["tag or build", "signing", "check"])
def test_a_part_that_fails_stops_what_comes_after_it(tree: Tree, failed: str, ran: int) -> None:
    done = tree.run(FAIL=failed)

    assert done.returncode != 0
    assert tree.parts() == WHOLE[:ran]


def test_a_failed_wiki_fails_the_command(tree: Tree) -> None:
    done = tree.run(FAIL="make-wiki-publish")

    assert done.returncode != 0
    assert "Released fsbpyr" not in done.stdout


def test_a_failed_build_found_on_going_on_stops_it(tree: Tree) -> None:
    tree.tag_on_github()

    done = tree.run(FAILED_RUNS="1")

    assert done.returncode == 1
    assert "release.yml failed for v1.2.0" in done.stderr
    assert tree.parts() == ["follow-run.sh 1"]


def test_no_build_found_on_going_on_stops_it(tree: Tree, tmp_path: Path) -> None:
    tree.tag_on_github()
    (tree.bin / "gh").write_text("#!/bin/sh\n")

    done = tree.run()

    assert done.returncode == 1 and "has no release.yml run" in done.stderr
    assert tree.parts() == []


@pytest.mark.parametrize("env", [{"FAILED_RUNS": "2"}, {"NO_EL8_RUN": "1"}], ids=["failed", "none"])
def test_el8_stubs_do_not_hold_the_release_back(tree: Tree, env: dict[str, str]) -> None:
    """They are outside the release gate, and it says so."""
    done = tree.run(**env)

    assert done.returncode == 0, done.stderr
    assert "goes out without EL8 stubs" in done.stderr
    assert "release-sign.sh /key/k.key 1.2.0" in tree.parts()


def test_a_key_under_home_is_found(tree: Tree) -> None:
    assert tree.run(FSBPYR_RELEASE_KEY_FILE="~/k.key").returncode == 0

    home = tree.env["HOME"]
    assert f"release-sign.sh {home}/k.key 1.2.0" in tree.parts()


def test_no_key_and_no_terminal_stops_before_it_signs(tree: Tree) -> None:
    done = tree.run(FSBPYR_RELEASE_KEY_FILE="")

    assert done.returncode == 1 and "no release key" in done.stderr
    assert not [part for part in tree.parts() if part.startswith("release-sign.sh")]


def test_with_no_key_given_it_asks_when_it_is_needed(tree: Tree) -> None:
    """The medium may be plugged in only now: a build takes a while."""
    env = {**tree.env, "FSBPYR_RELEASE_KEY_FILE": ""}
    cwd = Path.cwd()
    os.chdir(tree.tree)
    try:
        code, printed, _ = on_a_terminal(["bash", "tools/release/release.sh"], env,
                                         b"/typed/k.key", prompt=b"Release key file")  # fmt: skip
    finally:
        os.chdir(cwd)

    assert code == 0, printed
    assert "release-sign.sh /typed/k.key 1.2.0" in tree.parts()


def test_it_never_pulls() -> None:
    """A pull here would sign whatever GitHub's main says: main is the admin's to know."""
    text = (ROOT / "tools" / "release" / "release.sh").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.startswith("#"))

    for command in ("git pull", "git fetch", "git merge", "git reset", "git checkout"):
        assert command not in code


def test_usage(tree: Tree) -> None:
    done = subprocess.run(["bash", "tools/release/release.sh", "1.2.0", "more"], cwd=tree.tree,  # noqa: S607
                          env=tree.env, capture_output=True, text=True, check=False)  # fmt: skip

    assert done.returncode == 2


def test_a_version_that_is_no_version_is_refused(tree: Tree) -> None:
    done = subprocess.run(["bash", "tools/release/release.sh", "1.2.0; evil"], cwd=tree.tree,  # noqa: S607
                          env=tree.env, capture_output=True, text=True, check=False)  # fmt: skip

    assert done.returncode == 1 and tree.parts() == []


@pytest.mark.parametrize(("arguments", "env", "expected"), [
    (["KEY=/k.key"], {}, "FSBPYR_RELEASE_KEY_FILE='/k.key' bash tools/release/release.sh"),
    ([], {"KEY": "/exported"}, "bash tools/release/release.sh"),
    ([], {}, "bash tools/release/release.sh"),
], ids=["on the command line", "exported", "none"])
def test_make_passes_only_a_command_line_key(
    arguments: list[str], env: dict[str, str], expected: str
) -> None:
    outer = {name: value for name, value in os.environ.items()
             if name not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")}  # `make test`'s  # fmt: skip

    done = subprocess.run(["make", "-s", "-n", "release", *arguments], cwd=ROOT,  # noqa: S607
                          env={**outer, **env}, capture_output=True, text=True,
                          check=True)  # fmt: skip

    assert done.stdout.strip() == expected


def test_publishing_the_wiki_keeps_the_audit_record_up_to_date() -> None:
    plain = subprocess.run(["make", "-n", "wiki-publish"], cwd=ROOT, capture_output=True,  # noqa: S607
                           text=True, check=True)  # fmt: skip
    dry = subprocess.run(["make", "-n", "wiki-publish", "DRY_RUN=1"], cwd=ROOT,  # noqa: S607
                         capture_output=True, text=True, check=True)  # fmt: skip

    assert "audit.sh --record-wiki" in plain.stdout
    assert "audit.sh --record-wiki" not in dry.stdout
