"""`make release`, tools/release/release.sh: the order of its parts, what stops it, that it can go
on after a stop, that it asks nothing, and the JSON report it leaves. The parts have their own
tests: here they are stand-ins that say they ran, and print what the report must hold."""

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from ..support import requires
from ..support.signing import on_a_terminal

ROOT = Path(__file__).parents[2]
PART = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$LOG"
echo "output of $(basename "$0")"
echo "errors of $(basename "$0")" >&2
case " ${HANG-} " in *" $(basename "$0") "*) echo "$$" > "$LOG.hanging"; exec sleep 60 ;; esac
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
        for name in ("release.sh", "version-files.sh", "passphrase.sh"):
            shutil.copy2(ROOT / "tools" / "release" / name, tools / name)
        for name in ("release-assets.sh", "release-sign.sh", "release-signing.sh", "mirror.sh"):
            (tools / name).write_text(PART)
        (tools / "follow-run.sh").write_text(FAKE_FOLLOW)
        for name, text in (("gh", FAKE_GH), ("make", FAKE_MAKE)):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        (self.tree / "pyproject.toml").write_text('[project]\nname = "fsbpyr"\nversion = "1.2.0"\n')
        (self.tree / "uv.lock").write_text('[[package]]\nname = "fsbpyr"\nversion = "1.2.0"\n')
        (self.tree / "src" / "fsbpyr").mkdir(parents=True)
        (self.tree / "src" / "fsbpyr" / "__init__.py").write_text('__version__ = "1.2.0"\n')
        pins = self.tree / "src" / "fsbpyr" / "release-keys"
        pins.mkdir()
        (pins / "k.pub").write_text("pinned\n")
        self.state = root / "state"
        self.passphrase = root / "passphrase"
        self.passphrase.write_text("the secret passphrase\n")
        self.passphrase.chmod(0o600)
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "LOG": str(self.log),
                    "HOME": str(root / "home"), "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@example.org", "FSBPYR_RELEASE_WATCH_WAIT": "0",
                    "FSBPYR_RELEASE_KEY_FILE": "/key/k.key",
                    "FSBPYR_RELEASE_STATE": str(self.state),
                    "FSBPYR_RELEASE_PASSPHRASE_FILE": str(self.passphrase)}  # fmt: skip
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

    def printed(self, done: subprocess.CompletedProcess[str]) -> str:
        """Both streams: a step's go to the report together, and so to the terminal."""
        return done.stdout + done.stderr

    def report(self) -> dict[str, Any]:
        report: dict[str, Any] = json.loads((self.state / "reports" / "latest.json").read_text())
        return report

    def commit(self) -> str:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.tree, env=self.env, check=True,  # noqa: S607
                              capture_output=True, text=True).stdout.strip()  # fmt: skip


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
    return Tree(tmp_path)


CHECK_KEY = "release-signing.sh check-key /key/k.key src/fsbpyr/release-keys/k.pub"
WHOLE = [CHECK_KEY, "release-assets.sh 1.2.0", "follow-run.sh 2",
         "release-sign.sh /key/k.key 1.2.0",
         "make --no-print-directory release-check VERSION=1.2.0",
         "make --no-print-directory wiki-publish", "mirror.sh "]  # fmt: skip


def test_it_tags_waits_signs_checks_publishes_and_mirrors_in_that_order(tree: Tree) -> None:
    done = tree.run()

    assert done.returncode == 0, done.stderr
    assert tree.parts() == WHOLE
    assert "Released fsbpyr v1.2.0" in done.stdout


def test_after_a_stop_it_goes_on_from_the_tag(tree: Tree) -> None:
    tree.tag_on_github()

    done = tree.run()

    assert done.returncode == 0, done.stderr
    assert tree.parts() == [CHECK_KEY, "follow-run.sh 1", *WHOLE[2:]]


def test_a_tag_whose_name_only_ends_like_it_is_no_tag(tree: Tree) -> None:
    """ls-remote's patterns match the tail of a ref."""
    tree.tag_on_github("w/refs/tags/v1.2.0")

    assert tree.run().returncode == 0
    assert tree.parts() == WHOLE


def test_with_everything_signed_it_needs_no_key_and_goes_on(tree: Tree) -> None:
    tree.tag_on_github()

    done = tree.run(DRAFT="false", FSBPYR_RELEASE_KEY_FILE="")

    assert done.returncode == 0, done.stderr
    assert tree.parts() == ["follow-run.sh 1", "follow-run.sh 2", *WHOLE[4:]]


@pytest.mark.parametrize(("failed", "ran"), [
    ("release-signing.sh", 1),
    ("release-assets.sh", 2),
    ("release-sign.sh", 4),
    ("make-release-check", 5),
], ids=["the key", "tag or build", "signing", "check"])
def test_a_part_that_fails_stops_what_comes_after_it(tree: Tree, failed: str, ran: int) -> None:
    done = tree.run(FAIL=failed)

    assert done.returncode != 0
    assert tree.parts() == WHOLE[:ran]


def test_a_failed_wiki_fails_the_command(tree: Tree) -> None:
    done = tree.run(FAIL="make-wiki-publish")

    assert done.returncode != 0
    assert "Released fsbpyr" not in done.stdout
    assert tree.parts() == WHOLE[:6]


def test_a_failed_mirror_fails_the_command(tree: Tree) -> None:
    done = tree.run(FAIL="mirror.sh")

    assert done.returncode != 0
    assert "Released fsbpyr" not in done.stdout
    assert tree.parts() == WHOLE


def test_a_failed_build_found_on_going_on_stops_it(tree: Tree) -> None:
    tree.tag_on_github()

    done = tree.run(FAILED_RUNS="1")

    assert done.returncode == 1
    assert "release.yml failed for v1.2.0" in tree.printed(done)
    assert tree.parts() == [CHECK_KEY, "follow-run.sh 1"]


def test_no_build_found_on_going_on_stops_it(tree: Tree, tmp_path: Path) -> None:
    tree.tag_on_github()
    (tree.bin / "gh").write_text("#!/bin/sh\n")

    done = tree.run()

    assert done.returncode == 1 and "has no release.yml run" in tree.printed(done)
    assert tree.parts() == []


@pytest.mark.parametrize("env", [{"FAILED_RUNS": "2"}, {"NO_EL8_RUN": "1"}], ids=["failed", "none"])
def test_el8_stubs_do_not_hold_the_release_back(tree: Tree, env: dict[str, str]) -> None:
    """They are outside the release gate, and it says so."""
    done = tree.run(**env)

    assert done.returncode == 0, done.stderr
    assert "goes out without EL8 stubs" in tree.printed(done)
    assert "release-sign.sh /key/k.key 1.2.0" in tree.parts()


def test_a_key_under_home_is_found(tree: Tree) -> None:
    assert tree.run(FSBPYR_RELEASE_KEY_FILE="~/k.key").returncode == 0

    home = tree.env["HOME"]
    assert f"release-sign.sh {home}/k.key 1.2.0" in tree.parts()


def test_no_key_stops_it_before_the_tag(tree: Tree) -> None:
    done = tree.run(FSBPYR_RELEASE_KEY_FILE="")

    assert done.returncode == 1 and "no release key" in tree.printed(done)
    assert tree.parts() == []


def test_no_passphrase_stops_it_before_the_tag(tree: Tree) -> None:
    done = tree.run(FSBPYR_RELEASE_PASSPHRASE_FILE="")

    assert done.returncode == 1 and "no passphrase: set" in tree.printed(done)
    assert tree.parts() == []


def _passphrase_seen(tree: Tree) -> Path:
    """Where the key check writes the passphrase it got."""
    script = tree.tree / "tools" / "release" / "release-signing.sh"
    script.write_text('#!/bin/sh\necho "$FSBPYR_SIGNING_PASSPHRASE" > "$LOG.passphrase"\n')
    return tree.log.with_name("log.passphrase")


def test_the_key_check_gets_the_passphrase_of_the_file(tree: Tree) -> None:
    seen = _passphrase_seen(tree)

    assert tree.run().returncode == 0
    assert seen.read_text() == "the secret passphrase\n"


def test_a_passphrase_file_with_x_bits_is_read_not_run(tree: Tree) -> None:
    """On exFAT or NTFS, or a vfat mounted by hand, every file has x bits: run, the passphrase
    would be a shell's command line, and its words would go to the terminal and the report."""
    seen = _passphrase_seen(tree)
    tree.passphrase.chmod(0o700)

    done = tree.run()

    assert done.returncode == 0, tree.printed(done)
    assert seen.read_text() == "the secret passphrase\n"
    assert "the secret passphrase" not in json.dumps(tree.report())


def test_a_passphrase_file_with_x_bits_that_others_can_read_is_refused(tree: Tree) -> None:
    tree.passphrase.chmod(0o755)

    done = tree.run()

    assert done.returncode == 1 and "others can read" in tree.printed(done)
    assert tree.parts() == []


def test_an_elf_program_is_run_for_the_passphrase(tree: Tree) -> None:
    """A program starts with "#!" or is an ELF file."""
    seen = _passphrase_seen(tree)
    source = '#include <stdio.h>\nint main(void) { puts("the secret passphrase"); }\n'
    subprocess.run([requires.tool("cc"), "-x", "c", "-o", tree.passphrase, "-"], input=source,
                   text=True, check=True)  # fmt: skip
    tree.passphrase.chmod(0o700)

    assert tree.run().returncode == 0
    assert seen.read_text() == "the secret passphrase\n"


def test_a_program_the_admin_may_run_but_not_read_is_run(tree: Tree) -> None:
    """Such as a helper of root's of mode 0711: what it starts with cannot be read, and a file
    that cannot be read cannot hold the passphrase."""
    seen = _passphrase_seen(tree)
    source = '#include <stdio.h>\nint main(void) { puts("the secret passphrase"); }\n'
    subprocess.run([requires.tool("cc"), "-x", "c", "-o", tree.passphrase, "-"], input=source,
                   text=True, check=True)  # fmt: skip
    tree.passphrase.chmod(0o111)

    done = tree.run()

    assert done.returncode == 0, tree.printed(done)
    assert seen.read_text() == "the secret passphrase\n"


@pytest.mark.parametrize("status", [0, 3])
def test_what_a_passphrase_program_prints_on_its_errors_is_in_no_report(
    tree: Tree, status: int
) -> None:
    """Its standard error is dropped: what a step prints goes to the report."""
    tree.passphrase.write_text('#!/bin/sh\necho "the secret passphrase"\n'
                               f'echo "the secret passphrase" >&2\nexit {status}\n')  # fmt: skip
    tree.passphrase.chmod(0o700)

    done = tree.run()

    assert "the secret passphrase" not in tree.printed(done)
    assert "the secret passphrase" not in json.dumps(tree.report())
    if status:
        assert done.returncode == 1
        assert f"the passphrase program {tree.passphrase} failed (exit 3)" in tree.printed(done)
        assert tree.parts() == []
    else:
        assert done.returncode == 0, tree.printed(done)


def test_on_a_terminal_it_asks_nothing(tree: Tree) -> None:
    """Nothing is typed: a question would wait for the terminal's time limit, and fail."""
    cwd = Path.cwd()
    os.chdir(tree.tree)
    try:
        code, printed, _ = on_a_terminal(["bash", "tools/release/release.sh"], tree.env)
    finally:
        os.chdir(cwd)

    assert code == 0, printed
    assert tree.parts() == WHOLE
    assert b"Release key file" not in printed


# --- the report ---------------------------------------------------------------------------------


def test_a_release_leaves_a_report_in_json(tree: Tree) -> None:
    done = tree.run()

    report = tree.report()
    assert done.returncode == 0, done.stderr
    assert report["schema"] == 1
    assert report["result"] == "success" and report["exit_code"] == 0
    assert (report["version"], report["tag"]) == ("1.2.0", "v1.2.0")
    assert report["repository"] == "devitops-com/fsbpyr"
    assert report["commit"] == tree.commit()
    assert report["failed_step"] is None and report["warnings"] == []
    assert report["started"] <= report["finished"]
    assert [step["name"] for step in report["steps"]] == [
        "key", "tag-and-build", "el8-stubs", "sign-and-publish", "check", "wiki", "mirror"]
    assert all(step["result"] == "success" and step["exit_code"] == 0 for step in report["steps"])


def test_the_report_holds_what_each_step_printed_on_both_streams(tree: Tree) -> None:
    tree.run()

    steps = {step["name"]: step for step in tree.report()["steps"]}
    assert "output of release-assets.sh" in steps["tag-and-build"]["output"]
    assert "errors of release-assets.sh" in steps["tag-and-build"]["output"]
    assert "output of mirror.sh" in steps["mirror"]["output"]
    assert "output of release-assets.sh" not in steps["mirror"]["output"]


def test_what_a_step_prints_still_reaches_the_terminal(tree: Tree) -> None:
    done = tree.run()

    assert "output of release-sign.sh" in tree.printed(done)
    assert "Report:" in done.stdout


def test_the_report_names_the_step_that_failed(tree: Tree) -> None:
    done = tree.run(FAIL="release-sign.sh")

    report = tree.report()
    assert done.returncode == 1
    assert report["result"] == "failed" and report["exit_code"] == 1
    assert report["failed_step"] == "sign-and-publish"
    assert report["steps"][-1]["name"] == "sign-and-publish"
    assert report["steps"][-1]["result"] == "failed"
    assert "errors of release-sign.sh" in report["steps"][-1]["output"]
    assert "Report:" in done.stderr


def test_a_refused_start_is_in_the_report_too(tree: Tree) -> None:
    tree.run(FSBPYR_RELEASE_PASSPHRASE_FILE="")

    report = tree.report()
    assert report["failed_step"] == "key"
    assert any("no passphrase: set" in line for line in report["steps"][0]["output"])


def test_el8_stubs_left_out_are_a_warning_in_the_report(tree: Tree) -> None:
    tree.run(FAILED_RUNS="2")

    report = tree.report()
    assert report["result"] == "success"
    assert len(report["warnings"]) == 1 and "without EL8 stubs" in report["warnings"][0]


def test_every_run_keeps_its_report(tree: Tree) -> None:
    tree.run(FAIL="release-assets.sh")
    time.sleep(1.1)  # the file's name has the second it started in
    tree.run()

    names = sorted(path.name for path in (tree.state / "reports").glob("v1.2.0-*.json"))
    assert len(names) == 2
    assert tree.report()["result"] == "success"
    first = json.loads((tree.state / "reports" / names[0]).read_text())
    assert first["result"] == "failed"


def test_the_report_is_the_admins_alone_and_the_passphrase_is_not_in_it(tree: Tree) -> None:
    tree.run(FAIL="release-sign.sh")

    reports = tree.state / "reports"
    assert (reports.stat().st_mode & 0o077) == 0
    written = [path for path in reports.iterdir() if not path.is_symlink()]
    assert written and all((path.stat().st_mode & 0o077) == 0 for path in written)
    assert all("the secret passphrase" not in path.read_text() for path in written)


def test_nothing_of_the_run_is_left_beside_the_report(tree: Tree) -> None:
    tree.run()

    names = {path.name for path in (tree.state / "reports").iterdir()}
    assert len(names) == 2 and "latest.json" in names


def _held_in_its_part(tree: Tree) -> bool:
    """The release is held in release-assets.sh until a signal ends it, so it does not matter
    when the SIGINT comes: the part, whose pid its marker holds, has become `sleep`, which a
    SIGINT ends, and what it printed is in the step's log, which the report is made from.
    Each condition closes a race seen under load. A SIGINT that came between the part's last
    command and its `sleep` was taken by bash for that command, which exited by itself, and the
    part slept through it (6 of 3600 runs; full-suite run 41655931574). And tee, which a SIGINT
    ends too, had at times not read the part's output yet, which was lost (4 of 1200)."""
    try:
        pid = tree.log.with_name("log.hanging").read_text()
        if not pid.endswith("\n"):  # not written yet, or not all of it
            return False
        if (Path("/proc") / pid.strip() / "comm").read_text() != "sleep\n":
            return False
        logs = (tree.state / "reports").glob(".run.*/log")
        return any("output of release-assets.sh" in log.read_text() for log in logs)
    except (FileNotFoundError, ProcessLookupError):
        return False


def test_an_interrupted_release_says_so_with_what_it_had_printed(tree: Tree) -> None:
    with subprocess.Popen(["bash", "tools/release/release.sh"], cwd=tree.tree,  # noqa: S607
                          env={**tree.env, "HANG": "release-assets.sh"}, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          stdin=subprocess.DEVNULL, start_new_session=True) as process:  # fmt: skip
        for _ in range(600):
            if _held_in_its_part(tree):
                break
            time.sleep(0.05)
        else:
            os.killpg(process.pid, signal.SIGKILL)
            pytest.fail("the release never slept in release-assets.sh with its output logged")
        running = tree.report()
        os.killpg(process.pid, signal.SIGINT)
        process.communicate(timeout=30)

    assert running["result"] == "running" and running["running_step"] == "tag-and-build"
    report = tree.report()
    assert report["result"] == "interrupted" and report["running_step"] is None
    assert report["failed_step"] == "tag-and-build"
    assert report["steps"][-1]["result"] == "interrupted"
    assert "output of release-assets.sh" in report["steps"][-1]["output"]
    assert process.returncode != 0


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
