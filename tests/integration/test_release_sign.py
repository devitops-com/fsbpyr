"""make release-sign and make release-audit against a fake GitHub, a real bare repository, the real
release scripts and real keys: the attacks of someone who holds the fsbpyr account on GitHub, and
what the admin's machine then does. GitHub itself is taken as sound (docs/signing.md §5.3): only
FakeGitHub.attest makes an attestation."""

import hashlib
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from ..support import requires
from ..support.fakegithub import FakeGitHub
from ..support.signing import PASSPHRASE, Key, new_key

ROOT = Path(__file__).parents[2]
TOOLS = ("release-sign.sh", "audit.sh", "confirm.sh", "version-files.sh", "release-signing.sh",
         "check-signature.sh", "previous-release.sh")  # fmt: skip
TAG = "v1.2.0"
EL8_TAG = "el8-v1.2.0"
SUMS = "SHA256SUMS"
EL8_SUMS = "SHA256SUMS-glibc2.28"
EXECUTABLE = "fsbpyr-3.14-x86_64-linux-gnu"
STUB = "fsbpyr-stub-3.14-x86_64-linux-gnu-glibc2.28"


@dataclass(frozen=True)
class Keys:
    k: Key
    r: Key
    stray: Key


@pytest.fixture(scope="module")
def keys(tmp_path_factory: pytest.TempPathFactory) -> Keys:
    directory = tmp_path_factory.mktemp("keys")
    return Keys(*(new_key(directory / name) for name in ("k", "r", "stray")))


def digest_line(name: str, content: bytes) -> bytes:
    return f"{hashlib.sha256(content).hexdigest()}  {name}\n".encode()


class World:
    """The admin's clone of a tree that pins K and R, its remote `hub`, and GitHub."""

    def __init__(self, root: Path, keys: Keys) -> None:
        requires.tool("jq")
        requires.tool("git")
        self.keys = keys
        self.github = FakeGitHub(root / "github")
        self.tree = root / "tree"
        self.hub = root / "hub.git"
        self.wiki = root / "wiki.git"
        self.state = root / "state"
        self.env = {**os.environ, **self.github.env(), "HOME": str(root / "home"),
                    "TMPDIR": str(root), "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@example.org", "FSBPYR_RELEASE_ASSUME_YES": "1",
                    "FSBPYR_SIGNING_PASSPHRASE": PASSPHRASE,
                    "FSBPYR_RELEASE_STATE": str(self.state),
                    "FSBPYR_RELEASE_WIKI": str(self.wiki)}  # fmt: skip
        self.env.pop("FSBPYR_RELEASE_REPO", None)
        tools = self.tree / "tools" / "release"
        tools.mkdir(parents=True)
        for name in TOOLS:
            shutil.copy2(ROOT / "tools" / "release" / name, tools / name)
        (tools / "first-signed-release").write_text("1.2.0\n")
        (self.tree / "pyproject.toml").write_text(
            '[project]\nname = "fsbpyr"\nversion = "1.2.0"\n')
        (self.tree / "uv.lock").write_text('[[package]]\nname = "fsbpyr"\nversion = "1.2.0"\n')
        (self.tree / "src" / "fsbpyr").mkdir(parents=True)
        (self.tree / "src" / "fsbpyr" / "__init__.py").write_text('__version__ = "1.2.0"\n')
        pins = self.tree / "src" / "fsbpyr" / "release-keys"
        pins.mkdir()
        shutil.copy2(keys.k.pub, pins / "k.pub")
        shutil.copy2(keys.r.pub, pins / "r.pub")
        self.git("init", "-q", "--bare", "-b", "main", self.hub, cwd=root)
        self.git("init", "-q", "--bare", "-b", "main", self.wiki, cwd=root)
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "release")
        self.git("remote", "add", "hub", self.hub)
        self.commit = self.head()
        self.tag(TAG)
        self.tag(EL8_TAG)  # as make release: the companion's tag goes with the release's
        self.git("push", "-q", "hub", "main", TAG, EL8_TAG)
        wiki_clone = root / "wiki-clone"
        wiki_clone.mkdir()
        self.git("init", "-q", "-b", "main", cwd=wiki_clone)
        (wiki_clone / "Home.md").write_text("the wiki\n")
        self.git("add", "-A", cwd=wiki_clone)
        self.git("commit", "-q", "-m", "wiki", cwd=wiki_clone)
        self.git("push", "-q", self.wiki, "main", cwd=wiki_clone)
        self.wiki_clone = wiki_clone

    def git(self, *args: str | Path, cwd: Path | None = None) -> str:
        done = subprocess.run(["git", *map(str, args)], cwd=cwd or self.tree, env=self.env,  # noqa: S607
                              capture_output=True, text=True, check=False)  # fmt: skip
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def tag(self, tag: str) -> None:
        self.git("tag", "-a", tag, "-m", tag)

    def draft(self, tag: str = TAG, commit: str | None = None, extra: bytes = b""
              ) -> dict[str, bytes]:  # fmt: skip
        """What release.yml leaves: the files, their sums, and GitHub's attestation of those."""
        files = {EXECUTABLE: b"an executable\n" + extra, f"{EXECUTABLE}.xz": b"its xz\n" + extra,
                 "install.sh": b"#!/bin/sh\n"}  # fmt: skip
        sums = b"".join(digest_line(name, content) for name, content in sorted(files.items()))
        self.github.draft(tag, {**files, SUMS: sums})
        self.github.attest(sums, "release.yml", f"refs/tags/{tag}", commit or self.commit)
        return files

    def el8_draft(self, tag: str = TAG, commit: str | None = None) -> None:
        """What el8.yml leaves: only the .xz of a stub, and sums that list the stub too."""
        xz = b"a stub's xz\n"
        sums = digest_line(STUB, b"a stub\n") + digest_line(f"{STUB}.xz", xz)
        self.github.draft(f"el8-{tag}", {f"{STUB}.xz": xz, EL8_SUMS: sums})
        self.github.attest(sums, "el8.yml", f"refs/tags/{tag}", commit or self.commit)

    def sign(self, key: Path | None = None, *args: str) -> subprocess.CompletedProcess[str]:
        command = ["bash", "tools/release/release-sign.sh", str(key or self.keys.k.key), *args]
        return subprocess.run(command, cwd=self.tree, env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, start_new_session=True,
                              check=False)  # fmt: skip

    def audit(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = ["bash", "tools/release/audit.sh", *args]
        return subprocess.run(command, cwd=self.tree, env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, check=False)  # fmt: skip

    def verify(self, file: Path, *pubs: Path) -> subprocess.CompletedProcess[str]:
        command = ["bash", "tools/release/release-signing.sh", "verify", file, *pubs]
        return subprocess.run(list(map(str, command)), cwd=self.tree, env=self.env,
                              capture_output=True, text=True, check=False)  # fmt: skip

    def published(self) -> "World":
        """The admin's first release, signed and on record, and an audit that passes."""
        self.draft()
        assert self.audit("--accept").returncode == 0
        done = self.sign()
        assert done.returncode == 0, done.stderr + done.stdout
        self.github.load()
        return self


@pytest.fixture
def world(tmp_path: Path, keys: Keys) -> World:
    return World(tmp_path, keys)


@pytest.fixture
def ready(world: World) -> Iterator[World]:
    """A draft for your commit, and a clean record of GitHub as it was before."""
    assert world.audit("--accept").returncode == 0
    world.draft()
    yield world


def refused(world: World, done: subprocess.CompletedProcess[str], text: str, tag: str = TAG
            ) -> None:  # fmt: skip
    """It said why, nothing went to GitHub, and the draft is still a draft."""
    assert done.returncode == 1, done.stdout + done.stderr
    assert text in done.stderr, done.stderr
    assert world.github.writes() == []
    world.github.load()
    if tag in world.github.state["releases"]:
        assert world.github.state["releases"][tag]["draft"] is True
    assert not world.github.asset(tag, f"{SUMS}.sig").exists()


# --- signing ------------------------------------------------------------------------------------


def test_a_draft_of_your_commit_is_signed_and_published(ready: World) -> None:
    done = ready.sign()

    assert done.returncode == 0, done.stderr + done.stdout
    ready.github.load()
    assert ready.github.state["releases"][TAG]["draft"] is False
    assert ready.github.state["releases"][TAG]["immutable"] is True
    assert ready.github.state["latest"] == TAG
    sums = ready.github.asset(TAG, SUMS)
    assert ready.verify(sums, ready.keys.k.pub).returncode == 0
    assert ready.audit().returncode == 0


def test_only_the_signature_goes_to_github(ready: World) -> None:
    """K stays on this machine: never as a secret, an upload or an argument."""
    assert ready.sign().returncode == 0

    calls = ready.github.calls()
    upload, publish = ready.github.writes()
    assert upload.startswith(f"release upload {TAG} ")
    assert upload.split()[3].endswith(f"/{SUMS}.sig")
    assert publish.endswith("-F draft=false -f make_latest=true")
    assert not [call for call in calls if "secret" in call.split()[:2] or ".key" in call]
    assert not [call for call in calls if PASSPHRASE in call]


def test_the_signature_is_what_clients_check(ready: World) -> None:
    assert ready.sign().returncode == 0

    sums = ready.github.asset(TAG, SUMS)

    assert ready.verify(sums, ready.keys.k.pub).returncode == 0
    assert ready.verify(sums, ready.keys.stray.pub).returncode == 1
    assert ready.github.asset(TAG, f"{SUMS}.sig").stat().st_size == 3373


def test_the_recovery_key_goes_through_the_same_checks(ready: World) -> None:
    done = ready.sign(ready.keys.r.key)

    assert done.returncode == 0, done.stderr + done.stdout
    assert ready.verify(ready.github.asset(TAG, SUMS), ready.keys.r.pub).returncode == 0


def test_the_companion_is_signed_and_is_not_made_the_latest(ready: World) -> None:
    ready.el8_draft()

    done = ready.sign()

    assert done.returncode == 0, done.stderr + done.stdout
    ready.github.load()
    assert ready.github.state["latest"] == TAG
    assert ready.github.state["releases"][EL8_TAG]["draft"] is False
    assert ready.verify(ready.github.asset(EL8_TAG, EL8_SUMS), ready.keys.k.pub).returncode == 0
    assert ready.github.writes()[-1].endswith("-F draft=false -f make_latest=false")
    assert ready.audit().returncode == 0


def test_a_companion_that_comes_later_is_signed_by_a_second_run(ready: World) -> None:
    first = ready.sign()
    assert first.returncode == 0
    assert f"{EL8_TAG} has no draft yet" in first.stdout
    ready.el8_draft()

    done = ready.sign()

    assert done.returncode == 0, done.stderr + done.stdout
    assert f"{TAG} is published" in done.stdout
    assert ready.audit().returncode == 0


def test_a_second_release_passes_the_pins_of_the_first(world: World) -> None:
    world.published()
    (world.tree / "next").write_text("a change\n")
    world.git("add", "-A")
    world.git("commit", "-q", "-m", "next")
    world.commit = world.head()
    world.tag("v1.3.0")
    world.tag("el8-v1.3.0")
    world.git("push", "-q", "hub", "main", "v1.3.0", "el8-v1.3.0")
    world.draft("v1.3.0", extra=b"1.3.0")

    done = world.sign(None, "1.3.0")

    assert done.returncode == 0, done.stderr + done.stdout
    world.github.load()
    assert world.github.state["latest"] == "v1.3.0"
    assert world.audit().returncode == 0


# --- your commit, or no signature ---------------------------------------------------------------


def _other_commit(world: World) -> str:
    """A commit you do not have in main, as whoever pushes to GitHub makes one."""
    world.git("checkout", "-q", "--detach")
    (world.tree / "backdoor").write_text("evil\n")
    world.git("add", "-A")
    world.git("commit", "-q", "-m", "a backdoor")
    commit = world.head()
    world.git("checkout", "-q", "main")
    return commit


def test_a_commit_pushed_to_github_stops_it(ready: World) -> None:
    commit = _other_commit(ready)
    ready.git("push", "-q", "hub", f"{commit}:refs/heads/main")

    refused(ready, ready.sign(), "GitHub's main is at")


def test_a_tag_moved_on_github_stops_it(ready: World) -> None:
    commit = _other_commit(ready)
    ready.git("push", "-q", "--force", "hub", f"{commit}:refs/tags/{TAG}")

    refused(ready, ready.sign(), f"GitHub's {TAG} is at")


def test_an_annotated_tag_moved_on_github_stops_it(ready: World) -> None:
    """The peeled line of `ls-remote` is the commit: a tag object must not hide it."""
    commit = _other_commit(ready)
    ready.git("tag", "-a", "moved", "-m", "moved", commit)
    ready.git("push", "-q", "--force", "hub", f"refs/tags/moved:refs/tags/{TAG}")
    ready.git("tag", "-d", "moved")

    refused(ready, ready.sign(), f"GitHub's {TAG} is at")


def test_a_tag_gone_from_github_stops_it(ready: World) -> None:
    ready.git("push", "-q", "hub", f":refs/tags/{TAG}")

    refused(ready, ready.sign(), f"GitHub's {TAG} is at nothing")


def test_head_must_be_the_tag(ready: World) -> None:
    (ready.tree / "later").write_text("a change\n")
    ready.git("add", "-A")
    ready.git("commit", "-q", "-m", "later")
    ready.git("push", "-q", "hub", "main")

    refused(ready, ready.sign(), f"{TAG} is not this clone's HEAD")


def test_a_clone_without_the_tag_stops_it(ready: World) -> None:
    ready.git("tag", "-d", TAG)

    refused(ready, ready.sign(), f"this clone has no tag {TAG}")


def test_a_dirty_tree_stops_it(ready: World) -> None:
    (ready.tree / "untracked").write_text("x\n")

    refused(ready, ready.sign(), "the working tree is not clean")


def test_another_branch_stops_it(ready: World) -> None:
    ready.git("checkout", "-q", "-b", "side")

    refused(ready, ready.sign(), "not on main")


@pytest.mark.parametrize("change", [
    {"commit": "0" * 40},
    {"workflow": "attest-example.yml"},
    {"workflow": "el8.yml"},
    {"ref": "refs/tags/v9.9.9"},
    {"ref": "refs/heads/main"},
    {"self_hosted": True},
], ids=["another commit", "another workflow", "the other release workflow", "another tag",
        "a branch", "a runner of the attacker's"])
def test_an_attestation_that_is_not_for_your_commit_stops_it(
    ready: World, change: dict[str, object]
) -> None:
    sums = ready.github.asset(TAG, SUMS).read_bytes()
    made = {"workflow": "release.yml", "ref": f"refs/tags/{TAG}", "commit": ready.commit, **change}
    ready.github.attest(sums, str(made["workflow"]), str(made["ref"]), str(made["commit"]),
                        self_hosted=bool(made.get("self_hosted")))  # fmt: skip

    refused(ready, ready.sign(), "does not name your commit")


def test_a_draft_built_by_another_commit_of_the_same_account_stops_it(world: World) -> None:
    """The play's attacker: a real run of the real workflow, for their commit, on your tag."""
    assert world.audit("--accept").returncode == 0
    world.draft(commit=_other_commit(world))

    refused(world, world.sign(), "does not name your commit")


def test_files_replaced_with_their_sums_rewritten_stop_it(ready: World) -> None:
    """The sums match the files. Only the attestation of the sums tells them apart."""
    evil = b"a hacked executable\n"
    ready.github.asset(TAG, EXECUTABLE).write_bytes(evil)
    files = {name: ready.github.asset(TAG, name).read_bytes()
             for name in (EXECUTABLE, f"{EXECUTABLE}.xz", "install.sh")}  # fmt: skip
    ready.github.asset(TAG, SUMS).write_bytes(
        b"".join(digest_line(name, content) for name, content in sorted(files.items())))

    refused(ready, ready.sign(), "does not name your commit")


def test_a_file_replaced_under_attested_sums_stops_it(ready: World) -> None:
    ready.github.asset(TAG, EXECUTABLE).write_bytes(b"a hacked executable\n")

    refused(ready, ready.sign(), "is not what its attested")


def test_a_file_added_to_the_draft_stops_it(ready: World) -> None:
    ready.github.asset(TAG, "fsbpyr-evil").write_bytes(b"not listed\n")

    refused(ready, ready.sign(), "has files its attested")


def test_a_file_removed_from_the_draft_stops_it(ready: World) -> None:
    ready.github.asset(TAG, "install.sh").unlink()

    refused(ready, ready.sign(), "lacks files its attested")


def test_a_signature_already_on_the_draft_stops_it(ready: World) -> None:
    ready.github.asset(TAG, f"{SUMS}.sig").write_bytes(b"not ours")

    done = ready.sign()

    assert done.returncode == 1 and "already has a signature" in done.stderr
    assert ready.github.writes() == []


def test_a_draft_without_sums_stops_it(ready: World) -> None:
    ready.github.asset(TAG, SUMS).unlink()

    refused(ready, ready.sign(), f"has no {SUMS}")


def test_a_companion_file_replaced_stops_it(ready: World) -> None:
    ready.el8_draft()
    ready.github.asset(EL8_TAG, f"{STUB}.xz").write_bytes(b"a hacked stub\n")

    done = ready.sign()

    assert done.returncode == 1 and "is not what its attested" in done.stderr
    ready.github.load()
    assert ready.github.state["releases"][EL8_TAG]["draft"] is True
    assert not ready.github.asset(EL8_TAG, f"{EL8_SUMS}.sig").exists()


def test_a_companion_attested_for_another_commit_stops_it(ready: World) -> None:
    ready.el8_draft(commit="0" * 40)

    done = ready.sign()

    assert done.returncode == 1 and "does not name your commit" in done.stderr
    ready.github.load()
    assert ready.github.state["releases"][EL8_TAG]["draft"] is True


def test_an_answer_about_a_draft_that_is_neither_is_no_pass(ready: World) -> None:
    """Anything but true and false must not read as published, which would skip the release."""
    ready.github.state["releases"][TAG]["isDraft"] = "maybe"  # only `release view` says this
    ready.github.save()

    done = ready.sign()

    assert done.returncode == 1 and "isDraft" in done.stderr + done.stdout
    assert ready.github.writes() == []


def test_a_lookup_of_the_companion_that_fails_is_not_taken_for_no_draft(ready: World) -> None:
    """A release may have no EL8 stubs, so "no draft" is not an error, and an outage must not read
    as that."""
    ready.github.state["view_fails"] = [EL8_TAG]
    ready.github.save()

    done = ready.sign()

    assert done.returncode == 1 and "HTTP 500" in done.stderr
    assert ready.github.writes() == []


def test_a_file_replaced_while_it_is_published_is_reported(ready: World) -> None:
    """After the last check and before the release is immutable: clients refuse that file, since
    the signature covers the sums, and the admin must hear of it at once."""
    ready.github.state["swapped_while_publishing"] = [EXECUTABLE]
    ready.github.save()

    done = ready.sign()

    assert done.returncode == 1
    assert "the audit fails after publishing" in done.stderr
    assert f"{TAG} has files its signed {SUMS} does not list" in done.stdout


# --- where it was built -------------------------------------------------------------------------


def _attested_run(world: World, tag: str = TAG, sums: str = SUMS) -> list[dict[str, object]]:
    world.github.load()
    digest = hashlib.sha256(world.github.asset(tag, sums).read_bytes()).hexdigest()
    jobs: list[dict[str, object]] = world.github.state["runs"][
        world.github.state["attestations"][digest]["run"]]
    return jobs


def test_a_build_on_a_runner_of_the_attacker_stops_it(ready: World) -> None:
    """The attestation is GitHub's, for your commit, by a job on GitHub's machines: the job that
    hashed what an earlier job, on the attacker's machine, left."""
    _attested_run(ready)[0]["runner_group_id"] = 1
    ready.github.save()

    refused(ready, ready.sign(), "has jobs on machines of someone else: build")


def test_a_job_that_does_not_say_where_it_ran_stops_it(ready: World) -> None:
    _attested_run(ready)[0]["runner_group_id"] = None
    ready.github.save()

    refused(ready, ready.sign(), "has jobs on machines of someone else: build")


def test_a_job_that_was_skipped_ran_nowhere(ready: World) -> None:
    _attested_run(ready).append({"name": "x", "conclusion": "skipped", "runner_group_id": None})
    ready.github.save()

    assert ready.sign().returncode == 0


def test_a_companion_built_on_a_runner_of_the_attacker_stops_it(ready: World) -> None:
    ready.el8_draft()
    _attested_run(ready, EL8_TAG, EL8_SUMS)[0]["runner_group_id"] = 1
    ready.github.save()

    done = ready.sign()

    assert done.returncode == 1 and "has jobs on machines of someone else" in done.stderr
    ready.github.load()
    assert ready.github.state["releases"][EL8_TAG]["draft"] is True


@pytest.mark.parametrize("repository", ["devitops-com/other", "evil/fsbpyr"])
def test_an_attestation_that_names_a_run_elsewhere_stops_it(
    ready: World, repository: str
) -> None:
    digest = hashlib.sha256(ready.github.asset(TAG, SUMS).read_bytes()).hexdigest()
    ready.github.state["attestations"][digest]["repository"] = repository
    ready.github.save()

    refused(ready, ready.sign(), "names the run")


def test_an_attestation_that_names_no_run_stops_it(ready: World) -> None:
    """No run read is no job on someone else's machine, and must not read as that."""
    digest = hashlib.sha256(ready.github.asset(TAG, SUMS).read_bytes()).hexdigest()
    ready.github.state["attestations"][digest]["count"] = 0
    ready.github.save()

    refused(ready, ready.sign(), "names the run")


def test_every_attestation_of_the_sums_is_looked_at(ready: World) -> None:
    """Two runs of your commit made the same sums: the jobs of each are read."""
    digest = hashlib.sha256(ready.github.asset(TAG, SUMS).read_bytes()).hexdigest()
    ready.github.state["attestations"][digest]["count"] = 2
    ready.github.save()

    assert ready.sign().returncode == 0
    jobs = [call for call in ready.github.calls() if "/jobs" in call]
    assert len(jobs) == 2


def test_jobs_that_cannot_be_read_stop_it(ready: World) -> None:
    ready.github.state["broken"] = ["/attempts/"]
    ready.github.save()

    done = ready.sign()

    assert done.returncode != 0
    assert ready.github.writes() == []


# --- while it is being signed -------------------------------------------------------------------


def _after_download(world: World, command: str) -> None:
    world.github.state["after_download"] = command
    world.github.save()


def _unchanged_or_refused(world: World, text: str) -> None:
    done = world.sign()
    assert done.returncode == 1, done.stdout + done.stderr
    assert text in done.stderr, done.stderr
    assert world.github.writes() == []
    assert not world.github.asset(TAG, f"{SUMS}.sig").exists()


def test_a_file_replaced_after_the_checks_stops_it(ready: World) -> None:
    """install.sh is not checked by any client that runs it from curl: this is all that stands
    between such a swap and a signed, immutable release."""
    _after_download(ready, f"echo evil > {ready.github.asset(TAG, 'install.sh')}")

    _unchanged_or_refused(ready, "changed while it was being signed")


def test_a_file_added_after_the_checks_stops_it(ready: World) -> None:
    _after_download(ready, f"echo evil > {ready.github.asset(TAG, 'extra')}")

    _unchanged_or_refused(ready, "changed while it was being signed")


def test_a_file_removed_after_the_checks_stops_it(ready: World) -> None:
    _after_download(ready, f"rm {ready.github.asset(TAG, 'install.sh')}")

    _unchanged_or_refused(ready, "changed while it was being signed")


def test_a_draft_made_a_prerelease_stops_it(ready: World) -> None:
    ready.github.state["releases"][TAG]["prerelease"] = True
    ready.github.save()

    _unchanged_or_refused(ready, "was made a prerelease")


def test_a_second_draft_of_the_tag_stops_it(ready: World) -> None:
    """Upload and publishing by tag would take the newest draft, which nobody checked."""
    ready.github.state["second_draft"] = [TAG]
    ready.github.save()

    _unchanged_or_refused(ready, "is not exactly one draft")


def test_a_tag_moved_after_the_checks_stops_it(ready: World) -> None:
    commit = _other_commit(ready)
    _after_download(ready, f"git -C {ready.tree} push -q --force hub {commit}:refs/tags/{TAG}")

    _unchanged_or_refused(ready, f"GitHub's {TAG} is at")


def test_a_commit_pushed_after_the_checks_stops_it(ready: World) -> None:
    commit = _other_commit(ready)
    _after_download(ready, f"git -C {ready.tree} push -q hub {commit}:refs/heads/main")

    _unchanged_or_refused(ready, "GitHub's main is at")


def test_a_ref_whose_name_ends_like_the_tag_answers_for_nothing(ready: World) -> None:
    """ls-remote's patterns match the tail of a ref: with the real tag moved, a tag of this name at
    your commit would pass for it."""
    good = ready.commit
    ready.git("push", "-q", "hub", f"{good}:refs/tags/w/refs/tags/{TAG}")
    commit = _other_commit(ready)
    ready.git("push", "-q", "--force", "hub", f"{commit}:refs/tags/{TAG}")

    refused(ready, ready.sign(), f"GitHub's {TAG} is at")


def test_a_signature_left_by_a_run_that_stopped_says_how_to_go_on(ready: World) -> None:
    ready.github.asset(TAG, f"{SUMS}.sig").write_bytes(b"ours, from a run that stopped")

    done = ready.sign()

    assert done.returncode == 1 and f"gh release delete-asset {TAG} {SUMS}.sig" in done.stderr


def test_no_draft_stops_it(world: World) -> None:
    assert world.audit("--accept").returncode == 0

    refused(world, world.sign(), f"{TAG} has no draft")


def test_a_key_the_tag_does_not_pin_is_refused_before_anything_is_uploaded(ready: World) -> None:
    done = ready.sign(ready.keys.stray.key)

    assert done.returncode != 0
    assert ready.github.writes() == []
    ready.github.load()
    assert ready.github.state["releases"][TAG]["draft"] is True


def test_a_key_inside_the_checkout_is_refused(ready: World) -> None:
    """K is never in the repository, not even for a moment."""
    copy = ready.tree / "k.key"
    shutil.copy2(ready.keys.k.key, copy)

    refused(ready, ready.sign(copy), "a release key is never kept in the repository")


def test_a_link_to_a_key_inside_the_checkout_is_refused(ready: World, tmp_path: Path) -> None:
    copy = ready.tree / "k.key"
    shutil.copy2(ready.keys.k.key, copy)
    link = tmp_path / "elsewhere.key"
    link.symlink_to(copy)

    refused(ready, ready.sign(link), "a release key is never kept in the repository")


def test_a_key_that_is_not_there_is_refused(ready: World, tmp_path: Path) -> None:
    refused(ready, ready.sign(tmp_path / "none.key"), "no key file")


def test_no_login_stops_it(ready: World) -> None:
    ready.github.state["logged_in"] = False
    ready.github.save()

    refused(ready, ready.sign(), "gh is not authenticated")


def test_no_terminal_and_no_consent_stops_it(ready: World) -> None:
    ready.env.pop("FSBPYR_RELEASE_ASSUME_YES")

    refused(ready, ready.sign(), "not a TTY")


def test_an_audit_that_does_not_pass_stops_it_before_the_drafts_are_read(ready: World) -> None:
    ready.github.state["settings"]["repos/devitops-com/fsbpyr/hooks"] = [
        {"id": 7, "config": {"url": "https://evil.example"}, "active": True}]
    ready.github.save()

    refused(ready, ready.sign(), "the audit did not pass: nothing was signed")
    assert not [call for call in ready.github.calls() if call.startswith("release download")]


def test_a_version_that_is_no_version_is_refused(ready: World) -> None:
    done = ready.sign(None, "1.2.0; evil")

    assert done.returncode == 1 and "is no version" in done.stderr
    assert ready.github.writes() == []


@pytest.mark.parametrize("arguments", [(), ("",), ("a", "1.2.0", "more")])
def test_usage(world: World, arguments: tuple[str, ...]) -> None:
    done = subprocess.run(["bash", "tools/release/release-sign.sh", *arguments], cwd=world.tree,  # noqa: S607
                          env=world.env, capture_output=True, text=True, check=False)  # fmt: skip

    assert done.returncode == 2


# --- the audit ----------------------------------------------------------------------------------


def findings(done: subprocess.CompletedProcess[str], text: str, count: int | None = None) -> None:
    """``count``: how many findings in all, so that each of them is known to count."""
    assert done.returncode == 1, done.stdout + done.stderr
    assert text in done.stdout
    if count is not None:
        assert f"audit: {count} finding(s)" in done.stdout, done.stdout


def test_an_untouched_github_passes(world: World) -> None:
    world.published()

    done = world.audit()

    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 releases, the tags, main and the settings are as recorded" in done.stdout


def test_no_reference_is_no_pass(world: World) -> None:
    done = world.audit()

    assert done.returncode == 2 and "audit.sh --accept" in done.stderr


@pytest.mark.parametrize("endpoint", [
    "/collaborators", "/members?role=admin", "/members?per_page", "/installations", "/hooks",
    "/keys", "/rulesets?", "/rulesets/1", "/actions/secrets", "/variables",
    "orgs/devitops-com/actions/variables", "/environments",
    "/actions/runners", "/actions/permissions?", "/actions/permissions/workflow",
    "/immutable-releases", "/releases?", "/releases/latest",
])
def test_a_lookup_that_fails_is_no_pass(world: World, endpoint: str) -> None:
    """Nothing to compare is not a match: a blinded check would read as "no secrets", which is
    just what the record says."""
    world.published()
    world.github.state["broken"] = [endpoint]
    world.github.save()

    assert world.audit().returncode == 2


def test_a_wiki_that_cannot_be_read_is_no_pass(world: World, tmp_path: Path) -> None:
    world.published()
    world.env["FSBPYR_RELEASE_WIKI"] = str(tmp_path / "gone.git")

    assert world.audit().returncode == 2


@pytest.mark.parametrize("endpoint", ["/hooks", "/releases?"])
def test_accept_keeps_the_reference_when_a_lookup_fails(world: World, endpoint: str) -> None:
    """Or it would write an empty record, and every later audit would read that as the truth."""
    world.published()
    before = (world.state / "releases").read_text(), (world.state / "settings").read_text()
    world.github.state["broken"] = [endpoint]
    world.github.save()

    assert world.audit("--accept").returncode == 2

    assert ((world.state / "releases").read_text(),
            (world.state / "settings").read_text()) == before  # fmt: skip


def test_a_signature_of_a_release_that_cannot_be_read_is_no_pass(world: World) -> None:
    world.published()
    world.github.asset(TAG, f"{SUMS}.sig").write_bytes(b"")
    world.github.state["broken"] = []
    world.github.save()

    assert world.audit().returncode != 0


def test_a_github_without_main_is_no_pass(world: World) -> None:
    world.published()
    world.git("config", "receive.denyDeleteCurrent", "ignore", cwd=world.hub)
    world.git("push", "-q", "hub", ":refs/heads/main")

    assert world.audit().returncode == 2


def test_a_signature_that_cannot_be_checked_is_no_pass(world: World) -> None:
    """The keys of a tag this clone lacks cannot be read: exit 2 of check-signature.sh is no
    verdict, and must not read as a pass, whatever else is found."""
    world.published()
    world.git("tag", "-d", TAG)

    assert world.audit().returncode == 2


def test_a_release_list_that_fails_is_no_pass(world: World) -> None:
    world.published()
    world.github.state["broken"] = ["/releases"]
    world.github.save()

    assert world.audit().returncode == 2


def test_a_release_not_on_record_is_found(world: World) -> None:
    """Published by hand on GitHub, by whoever holds the account."""
    world.published()
    (world.tree / "x").write_text("x\n")
    world.git("add", "-A")
    world.git("commit", "-q", "-m", "x")
    world.tag("v1.3.0")
    world.git("push", "-q", "hub", "main", "v1.3.0")
    world.draft("v1.3.0", extra=b"evil")
    world.github.state["releases"]["v1.3.0"].update(draft=False, immutable=True)
    world.github.save()

    findings(world.audit(), "release v1.3.0 is on GitHub and not on record")


def test_a_signed_release_not_on_record_says_the_key_is_stolen(world: World) -> None:
    world.published()
    (world.tree / "x").write_text("x\n")
    world.git("add", "-A")
    world.git("commit", "-q", "-m", "x")
    world.tag("v1.3.0")
    world.git("push", "-q", "hub", "main", "v1.3.0")
    world.draft("v1.3.0", extra=b"evil")
    command = ["bash", "tools/release/release-signing.sh", "sign", str(world.keys.k.key),
               str(world.github.asset("v1.3.0", SUMS))]
    done = subprocess.run(command, cwd=world.tree, env=world.env, capture_output=True, text=True,
                          check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr
    world.github.state["releases"]["v1.3.0"].update(draft=False, immutable=True)
    world.github.save()

    findings(world.audit(), "a release key is stolen", 1)


def test_a_replaced_file_of_a_release_is_found(world: World) -> None:
    world.published()
    world.github.asset(TAG, EXECUTABLE).write_bytes(b"hacked")

    done = world.audit()

    findings(done, f"release {TAG} changed", 3)  # the file is unlisted, and the listed one lacking
    assert f"{TAG} has files its signed {SUMS} does not list" in done.stdout


def test_an_added_file_is_found(world: World) -> None:
    world.published()
    world.github.asset(TAG, "extra").write_bytes(b"added")

    done = world.audit()

    findings(done, f"release {TAG} changed", 2)
    assert f"{TAG} has files its signed {SUMS} does not list" in done.stdout


def test_a_removed_file_is_found(world: World) -> None:
    world.published()
    world.github.asset(TAG, "install.sh").unlink()

    done = world.audit()

    findings(done, f"release {TAG} changed", 2)
    assert f"{TAG} lacks files its signed {SUMS} lists" in done.stdout


def test_replaced_sums_are_found(world: World) -> None:
    """The record and the signature each catch it."""
    world.published()
    world.github.asset(TAG, SUMS).write_bytes(b"0" * 64 + b"  evil\n")

    done = world.audit()

    findings(done, f"the signature of {TAG}'s {SUMS} is not valid")
    assert f"release {TAG} changed" in done.stdout


def test_a_file_named_like_the_companions_sums_is_found(world: World) -> None:
    """Its dots are no wildcards: SHA256SUMS-glibc2x28 is a file like any other."""
    world.published()
    world.el8_draft()
    world.github.asset(EL8_TAG, "SHA256SUMS-glibc2x28").write_bytes(b"not listed")
    done = subprocess.run(["bash", "tools/release/release-signing.sh", "sign",  # noqa: S607
                           str(world.keys.k.key), str(world.github.asset(EL8_TAG, EL8_SUMS))],
                          cwd=world.tree, env=world.env, capture_output=True, text=True,
                          check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr
    world.github.state["releases"][EL8_TAG].update(draft=False, immutable=True)
    world.github.save()
    assert world.audit("--accept").returncode == 0

    findings(world.audit(), f"{EL8_TAG} has files its signed {EL8_SUMS} does not list")


@pytest.mark.parametrize("field", ["prerelease", "name", "body", "id"])
def test_a_release_changed_in_any_way_is_found(world: World, field: str) -> None:
    """Notes are read too: a release's text can send people anywhere. An id that differs is a
    release deleted and made again."""
    world.published()
    world.github.state["releases"][TAG][field] = True if field == "prerelease" else 999
    world.github.save()

    findings(world.audit(), f"release {TAG} changed")


def test_a_removed_signature_is_found(world: World) -> None:
    world.published()
    world.github.asset(TAG, f"{SUMS}.sig").unlink()

    done = world.audit()

    findings(done, f"{TAG} has no {SUMS}.sig")


def test_a_release_gone_from_github_is_found(world: World) -> None:
    world.published()
    del world.github.state["releases"][TAG]
    world.github.save()

    findings(world.audit(), f"release {TAG} is on record and gone from GitHub")


def test_a_release_that_is_not_immutable_is_found(world: World) -> None:
    world.published()
    world.github.state["releases"][TAG]["immutable"] = False
    world.github.save()

    findings(world.audit(), f"release {TAG} is not immutable")


def test_a_draft_for_a_tag_you_do_not_have_is_found(world: World) -> None:
    world.published()
    world.github.draft("v9.9.9", {"x": b"y"})

    findings(world.audit(), "a draft release v9.9.9, for a tag this clone lacks")


def test_a_held_back_update_is_found(world: World) -> None:
    world.published()
    world.github.state["latest"] = "v0.0.1"
    world.github.save()

    findings(world.audit(), "updates are held back")


def test_a_moved_tag_is_found(world: World) -> None:
    world.published()
    commit = _other_commit(world)
    world.git("push", "-q", "--force", "hub", f"{commit}:refs/tags/{TAG}")

    done = world.audit()

    findings(done, "the tags here (<) and on GitHub (>) differ")
    assert f"release {TAG} changed" in done.stdout


def test_a_tag_only_github_has_is_found(world: World) -> None:
    world.published()
    commit = _other_commit(world)
    world.git("push", "-q", "hub", f"{commit}:refs/tags/v9.9.9")

    findings(world.audit(), "the tags here (<) and on GitHub (>) differ")


def test_a_commit_only_github_has_on_main_is_found(world: World) -> None:
    world.published()
    commit = _other_commit(world)
    world.git("push", "-q", "hub", f"{commit}:refs/heads/main")

    findings(world.audit(), "which this clone's main does not hold")


def test_main_behind_your_clone_passes(world: World) -> None:
    """A commit not pushed yet is yours."""
    world.published()
    (world.tree / "unpushed").write_text("x\n")
    world.git("add", "-A")
    world.git("commit", "-q", "-m", "unpushed")

    assert world.audit().returncode == 0


REPOSITORY = "repos/devitops-com/fsbpyr"


@pytest.mark.parametrize(("endpoint", "body", "shown"), [
    (f"{REPOSITORY}/collaborators", [{"login": "admin", "role_name": "admin"},
                                     {"login": "evil", "role_name": "admin"}], "collaborator evil"),
    ("orgs/devitops-com/members", [{"login": "admin"}, {"login": "evil"}], "member evil"),
    ("orgs/devitops-com/installations", {"installations": [
        {"app_slug": "evil", "repository_selection": "all", "permissions": {}}]}, "app evil"),
    (f"{REPOSITORY}/hooks", [{"id": 7, "config": {"url": "https://evil.example"}, "active": True}],
     "webhook 7"),
    (f"{REPOSITORY}/keys", [{"id": 7, "title": "evil", "read_only": False}], "deploy-key 7"),
    (f"{REPOSITORY}/rulesets/1", {"name": "release tags", "target": "tag",
                                  "enforcement": "disabled", "bypass_actors": [],
                                  "conditions": {}, "rules": []},
     "ruleset release tags tag disabled"),
    (f"{REPOSITORY}/actions/secrets", {"secrets": [
        {"name": "EVIL", "updated_at": "now"}]}, "secret EVIL"),
    ("orgs/devitops-com/actions/secrets", {"secrets": [
        {"name": "EVIL", "updated_at": "now"}]}, "org-secret EVIL"),
    (f"{REPOSITORY}/actions/variables", {"variables": [
        {"name": "EVIL", "value": "1"}]}, "variable EVIL"),
    ("orgs/devitops-com/actions/variables", {"variables": [
        {"name": "EVIL", "value": "1"}]}, "org-variable EVIL"),
    (f"{REPOSITORY}/environments", {"environments": [{"name": "evil"}]}, "environment evil"),
    (f"{REPOSITORY}/actions/runners", {"runners": [
        {"name": "evil", "labels": [{"name": "ubuntu-24.04"}]}]}, "runner evil ubuntu-24.04"),
    ("orgs/devitops-com/actions/runners", {"runners": [
        {"name": "evil", "labels": [{"name": "ubuntu-24.04"}]}]}, "org-runner evil ubuntu-24.04"),
    (f"{REPOSITORY}/actions/permissions", {"enabled": True, "allowed_actions": "none"},
     "allowed=none"),
    (f"{REPOSITORY}/actions/permissions/workflow", {"default_workflow_permissions": "write",
                                                    "can_approve_pull_request_reviews": False},
     "token=write"),
    (f"{REPOSITORY}/immutable-releases", {"enabled": False}, "immutable-releases false"),
    (REPOSITORY, {"visibility": "public", "default_branch": "evil", "archived": False,
                  "has_wiki": True}, "default=evil"),
], ids=["a collaborator", "a member", "an app", "a webhook", "a deploy key", "a ruleset",
        "a secret", "an org secret", "a variable", "an org variable", "an environment", "a runner",
        "an org runner",
        "the Actions policy", "the token's default", "immutable releases", "the default branch"])
def test_a_changed_setting_is_found(
    world: World, endpoint: str, body: object, shown: str
) -> None:
    world.published()
    world.github.state["settings"][endpoint] = body
    world.github.save()

    done = world.audit()

    findings(done, "the settings differ from the record")
    assert shown in done.stdout


def test_an_owner_added_is_found(world: World) -> None:
    """Members and owners are one endpoint with `?role=admin`: the fake gives both the same."""
    world.published()
    world.github.state["settings"]["orgs/devitops-com/members"] = [{"login": "admin"},
                                                                   {"login": "x"}]  # fmt: skip
    world.github.save()

    findings(world.audit(), "owner x")


def test_a_changed_wiki_is_found(world: World) -> None:
    """The wiki holds the keys' fingerprints."""
    world.published()
    (world.wiki_clone / "Home.md").write_text("other fingerprints\n")
    world.git("add", "-A", cwd=world.wiki_clone)
    world.git("commit", "-q", "-m", "evil", cwd=world.wiki_clone)
    world.git("push", "-q", world.wiki, "main", cwd=world.wiki_clone)

    findings(world.audit(), "the settings differ from the record")


def test_accept_takes_what_is_there_and_then_it_passes(world: World) -> None:
    world.published()
    world.github.state["settings"][f"{REPOSITORY}/hooks"] = [
        {"id": 7, "config": {"url": "https://ci.example"}, "active": True}]
    world.github.save()
    assert world.audit().returncode == 1

    assert world.audit("--accept").returncode == 0
    assert world.audit().returncode == 0


def test_the_reference_is_private_to_the_admin(world: World) -> None:
    world.audit("--accept")

    assert world.state.stat().st_mode & 0o077 == 0
    assert (world.state / "releases").stat().st_mode & 0o077 == 0


def test_record_refuses_a_draft_and_a_release_on_record(world: World) -> None:
    world.draft()
    assert world.audit("--accept").returncode == 0

    assert world.audit("--record", TAG).returncode == 2
    world.github.state["releases"][TAG].update(draft=False, immutable=True)
    world.github.save()
    assert world.audit("--record", TAG).returncode == 0
    assert world.audit("--record", TAG).returncode == 2


@pytest.mark.parametrize("arguments", [("--what",), ("--record",), ("--accept", "x")])
def test_audit_usage(world: World, arguments: tuple[str, ...]) -> None:
    assert world.audit(*arguments).returncode == 2


def test_the_unsigned_releases_before_the_first_signed_one_pass(world: World) -> None:
    """They have no .sig, and the record alone stands for them."""
    world.git("tag", "-a", "v1.0.0", "-m", "old")
    world.git("push", "-q", "hub", "v1.0.0")
    world.github.draft("v1.0.0", {"old": b"an old asset"})
    world.github.state["releases"]["v1.0.0"].update(draft=False, immutable=True)
    world.github.state["latest"] = "v1.0.0"
    world.github.save()
    assert world.audit("--accept").returncode == 0

    done = world.audit()

    assert done.returncode == 0, done.stdout + done.stderr


def test_a_signed_version_without_its_signature_on_a_companion_is_found(world: World) -> None:
    world.published()
    world.el8_draft()
    world.github.state["releases"][EL8_TAG].update(draft=False, immutable=True)
    world.github.save()
    assert world.audit("--accept").returncode == 0

    findings(world.audit(), f"{EL8_TAG} has no {EL8_SUMS}.sig")
