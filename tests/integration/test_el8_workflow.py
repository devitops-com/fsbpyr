"""The shape of .github/workflows/el8.yml, which links the EL8 stubs of a release from its tag and
publishes them in a companion release outside the release gate, and of full-suite.yml's EL8 jobs,
which check and test those stubs on every push to main and gate the release (owner, 2026-11-03).
GitHub runs them, not these tests: they pin what can be read from the files (triggers, the gate,
pins, permissions, asset names, and that no job of el8.yml checks out or runs anything but the
commit that started the run), and run the shell of some steps against fakes."""

import fnmatch
import hashlib
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

from fsbpyr.bytecode import SUPPORTED_PYTHON
from fsbpyr.selfupdate import release_numbers
from fsbpyr.vcs import git_state, release_stub_asset, release_stub_sums, version_string

from ..support import requires

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
EL8 = (WORKFLOWS / "el8.yml").read_text(encoding="utf-8")
FULL = (WORKFLOWS / "full-suite.yml").read_text(encoding="utf-8")
JOB = re.compile(r"^  ([a-z][a-z0-9-]*):\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)",
                 re.MULTILINE | re.DOTALL)  # fmt: skip
JOBS = dict(JOB.findall(EL8.partition("\njobs:\n")[2]))
FULL_JOBS = dict(JOB.findall(FULL.partition("\njobs:\n")[2]))
EL8_JOBS = {name: text for name, text in FULL_JOBS.items() if name.startswith("el8-")}
DEFAULT_PYTHON_JOBS = ("el8-build", "el8-tests", "el8-portability", "el8-corpus")
SUMS = release_stub_sums("2.28")
PYTHON_TARGETS = [(f"{major}.{minor}", target)
                  for major, minor in sorted(SUPPORTED_PYTHON)
                  for target in ("x86_64-linux-gnu", "aarch64-linux-gnu")]  # fmt: skip
USES = re.compile(r"uses: (\S+)@([0-9a-f]{40}) # (v\d+\.\d+\.\d+)$", re.MULTILINE)
DIGEST = re.compile(r"debian:trixie@sha256:([0-9a-f]{64})")


def script_of(job: str, name: str) -> str:
    """The shell of the step called `name` of a job's text."""
    step = job.split(f"- name: {name}\n", 1)[1].split("run: |\n", 1)[1]
    body = step.split("\n      #", 1)[0].split("\n      - ", 1)[0]  # up to the next comment or step
    return textwrap.dedent(body) + "\n"


def step_script(name: str) -> str:
    """The shell of the draft job's step called `name`."""
    return script_of(JOBS["draft"], name)


def code(text: str) -> str:
    """`text` without its comment lines."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_it_runs_on_the_release_tag_and_on_request_without_inputs_only() -> None:
    block = EL8.split("\non:\n", 1)[1].split("\n\n", 1)[0]
    lines = [line for line in block.splitlines() if not line.lstrip().startswith("#")]
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")

    assert lines == ["  push:", '    tags: ["v*"]', "  workflow_dispatch:"]
    assert '    tags: ["v*"]' in release  # the same trigger as the release's
    assert "inputs:" not in EL8 and "workflow_run" not in EL8
    assert not re.search(r"pull_request|schedule|workflow_run", block)


def test_no_job_runs_anything_but_the_commit_that_started_the_run() -> None:
    """CodeQL: a workflow in the default branch's context that checks out another ref (the tag
    of a workflow_run, an input) is an untrusted checkout. Here the ref is the run's own."""
    assert len(re.findall(r"uses: actions/checkout@", EL8)) == 2  # plan and stub
    assert not re.search(r"^\s+ref:", EL8, re.MULTILINE)
    assert "workflow_run" not in EL8 and "inputs." not in EL8
    published = 'gh release download "$COMPANION" --pattern SHA256SUMS'  # the idempotence check
    assert "gh release download" not in EL8.replace(published, "")
    assert EL8.count("persist-credentials: false") == 2


def test_the_release_knows_nothing_of_el8_and_its_gate_tests_the_stubs() -> None:
    """A release neither waits for el8.yml nor depends on it (owner, 2026-10-25), and the stubs
    el8.yml publishes are tested in full-suite.yml, whose run gates the release (owner,
    2026-11-03): its EL8 jobs run on every trigger, and none may fail without failing the run."""
    for path in (WORKFLOWS / "release.yml", WORKFLOWS / "build.yml",
                 ROOT / "tools" / "release" / "green-run.sh",
                 ROOT / "tools" / "release" / "release-assets.sh"):  # fmt: skip
        text = path.read_text(encoding="utf-8").lower()
        assert "el8.yml" not in text and "glibc2.28" not in text, path.name
        assert "--glibc" not in text and "workflow_run" not in text, path.name
    assert "workflow_run" not in FULL and "gh release" not in FULL and "git push" not in FULL
    assert set(EL8_JOBS) == {"el8-python", "el8-stub", "el8-build", "el8-tests",
                             "el8-portability", "el8-corpus", "el8-vm-smoke"}  # fmt: skip
    for name, text in EL8_JOBS.items():
        assert "continue-on-error" not in text, name
        assert not re.search(r"^    if:", text.split("    steps:", 1)[0], re.MULTILINE), name


def test_a_tag_is_done_once() -> None:
    assert "group: el8-${{ github.ref }}" in EL8
    assert "cancel-in-progress: false" in EL8


def test_the_jobs_run_every_target_on_a_runner_of_the_targets_architecture() -> None:
    """The stubs are linked for every Python; the other EL8 jobs run the default Python only
    (owner, 2026-10-26), which el8-python reads from .python-version."""
    supported = ", ".join(f'"{"{}.{}".format(*version)}"' for version in sorted(SUPPORTED_PYTHON))
    default = "python: ${{ fromJSON(format('[\"{0}\"]', needs.el8-python.outputs.python)) }}"
    jobs = {"el8.yml stub": JOBS["stub"]} | {name: EL8_JOBS[name]
                                            for name in ("el8-stub", *DEFAULT_PYTHON_JOBS)}
    for name, job in jobs.items():
        text = job.split("    steps:", 1)[0]
        assert "    runs-on: ${{ matrix.runner }}\n" in text, name
        if name.endswith("stub"):
            assert f"python: [{supported}]" in text, name
        else:
            assert default in text, name
        assert 'target: ["x86_64-linux-gnu", "aarch64-linux-gnu"]' in text, name
        assert '- {target: "x86_64-linux-gnu", runner: "ubuntu-24.04"}' in text, name
        assert '- {target: "aarch64-linux-gnu", runner: "ubuntu-24.04-arm"}' in text, name
        assert "fail-fast: false" in text, name
    assert "runs-on: ubuntu-24.04-arm" in EL8_JOBS["el8-vm-smoke"]


def test_el8_python_reads_the_default_python() -> None:
    job = EL8_JOBS["el8-python"]

    assert "python: ${{ steps.python.outputs.version }}" in job
    assert 'python="$(cut -d. -f1,2 .python-version)"' in job
    for name in DEFAULT_PYTHON_JOBS:
        assert re.search(r"needs: (el8-python|\[el8-python, el8-stub, el8-build\])\n",
                         EL8_JOBS[name]), name  # fmt: skip
    for name in ("el8-portability", "el8-vm-smoke"):  # the stub and the executable must exist
        assert "needs: [el8-python, el8-stub, el8-build]" in EL8_JOBS[name], name
    assert "APP_PYTHON: ${{ needs.el8-python.outputs.python }}" in EL8_JOBS["el8-vm-smoke"]


def test_el8_yml_links_nothing_for_a_commit_whose_full_suite_run_did_not_pass() -> None:
    plan = JOBS["plan"]
    header = plan.split("    steps:", 1)[0]
    command = 'bash tools/release/green-run.sh full-suite.yml "$GITHUB_SHA"'
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")

    assert command in plan and command in release  # as release.yml's draft job checks it
    assert "      actions: read" in header and "write" not in header
    assert "fetch-depth: 0" in plan  # green-run.sh's documentation-only commits
    assert "GH_TOKEN: ${{ github.token }}" in plan and "GH_REPO: ${{ github.repository }}" in plan
    assert "if: steps.release.outputs.tag != ''" in plan  # a branch dispatch does nothing
    assert "    needs: plan\n" in JOBS["stub"]


def test_el8_yml_tests_nothing_and_links_each_stub_once_as_the_full_suite_links_it() -> None:
    """The tests and the second link are full-suite.yml's (owner, 2026-11-03)."""
    stub, linked = JOBS["stub"], EL8_JOBS["el8-stub"]

    for word in ("pytest", "make test", "vm-smoke", "portability", "test-corpus", "make bootstrap"):
        assert word not in code(EL8), word
    assert stub.count("fsbpyr runtime build") == 1 and "for n in" not in stub
    assert "for n in 1 2; do" in linked and 'FSBPYR_CACHE_DIR="/root/el8-cache-$n"' in linked
    assert 'cmp "stubs/1/$ASSET" "stubs/2/$ASSET"' in linked
    links = re.findall(r"uv run .*", stub)
    twice = re.findall(r"uv run .*", linked)
    assert links == [line.replace('"stubs/$n"', "stubs") for line in twice]
    assert len(links) == 2 and "tools/release/el8-stub.py" in links[1]


def test_the_image_is_the_builds_by_digest() -> None:
    build = DIGEST.findall((WORKFLOWS / "build.yml").read_text(encoding="utf-8"))
    published = DIGEST.findall(EL8)
    tested = [digest for job in EL8_JOBS.values() for digest in DIGEST.findall(job)]

    assert len(published) == 1  # stub
    assert len(tested) == 4  # el8-stub, el8-build, el8-tests and el8-corpus
    assert set(published) == set(tested) == set(build) and len(set(build)) == 1


def test_every_action_is_pinned_by_commit_as_the_other_workflows_pin_it() -> None:
    others = {}
    for name in ("build.yml", "release.yml", "full-suite.yml"):
        others.update({action: sha for action, sha, _ in USES.findall(
            (WORKFLOWS / name).read_text(encoding="utf-8"))})  # fmt: skip
    used = USES.findall(EL8)

    assert len(used) == len(re.findall(r"^\s+(?:- )?uses: ", EL8, re.MULTILINE))
    for action, sha, _ in used:
        assert others.get(action) == sha, action


def test_only_draft_writes_and_prepare_needs_every_other_job() -> None:
    assert len(re.findall(r"^ +permissions:", EL8, re.MULTILINE)) == 3  # plan, prepare, draft
    assert "permissions:\n  contents: read\n" in EL8
    draft = JOBS["draft"]
    header = draft.split("    steps:", 1)[0]
    prepare = JOBS["prepare"].split("    steps:", 1)[0]

    assert "      contents: write" in header
    assert "      id-token: write" in header and "      attestations: write" in header
    assert "actions:" not in header
    assert "    needs: [plan, stub]\n" in prepare
    assert "needs: [plan, prepare]" in header
    assert "      contents: read" in prepare and "write" not in prepare
    assert set(JOBS) == {"plan", "stub", "prepare", "draft"}
    assert "continue-on-error" not in EL8
    assert "checkout" not in draft  # it runs nothing of the tag's
    stubs = re.search(r"for python in ([0-9. ]+); do", JOBS["prepare"])
    assert stubs and stubs.group(1).split() == [
        "{}.{}".format(*version) for version in sorted(SUPPORTED_PYTHON)]  # draft: all of them

def test_draft_makes_the_companion_release_and_leaves_the_release_of_the_tag_alone() -> None:
    draft = JOBS["draft"]
    create = re.sub(r"\\\n\s*", "", step_script("Make the draft of the companion release"))
    created = re.findall(r"gh release create .*", create)

    assert "COMPANION: el8-${{ needs.plan.outputs.tag }}" in draft
    assert len(created) == 1
    assert created[0].startswith('gh release create "$COMPANION" --verify-tag --draft ')
    for flag in ('--latest=false', "dist/fsbpyr-stub-*.xz", f"dist/{release_stub_sums('2.28')}"):
        assert flag in created[0], flag
    assert "--target" not in created[0]  # the tag exists: make release pushed it
    assert not re.search(r"\bgh release (edit|delete|upload)\b", draft)  # only the admin publishes
    assert not re.search(r'gh release (create|view) "\$TAG"', draft)  # nor waits for the release
    assert "--clobber" not in draft
    assert "--source-ref refs/tags/v" in draft  # the attestations come from a run on the tag
    assert ("--signer-workflow $GH_REPO/.github/workflows/el8.yml --source-ref refs/tags/$TAG"
            in create)  # the notes say how to verify


def test_the_companion_tag_starts_no_workflow_and_names_no_release() -> None:
    """The prefix must not start with "v": release.yml and el8.yml run on tags v*, the ruleset
    of the release tags covers refs/tags/v*, and every tool that looks a release up takes vX.Y.Z."""
    companion = "el8-v1.2.3"
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")

    assert "COMPANION: el8-${{ needs.plan.outputs.tag }}" in JOBS["draft"]
    assert not fnmatch.fnmatchcase(companion, "v*")
    assert not re.match(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", companion)
    assert '    tags: ["v*"]' in release and '    tags: ["v*"]' in EL8


def test_it_is_the_companion_that_is_looked_up_for_the_idempotence_check() -> None:
    script = step_script("Is it published")

    assert 'gh release view "$COMPANION" --json isDraft --jq .isDraft' in script
    assert "jq " not in script.replace("--jq ", "")  # gh's own jq: nothing to install
    assert "gh release delete" not in script
    assert "is published with other stubs" in script and "immutable" in script
    assert "$TAG" not in script.replace("$TAG's", "")


def test_the_asset_names_are_those_the_stub_fetch_reads() -> None:
    asset = release_stub_asset("${{ matrix.python }}", "${{ matrix.target }}", "2.28")

    assert f"ASSET: {asset}\n" in JOBS["stub"] and f"ASSET: {asset}\n" in EL8_JOBS["el8-stub"]
    assert f"STUB: {asset}\n" in EL8_JOBS["el8-portability"]
    assert release_stub_sums("2.28") == "SHA256SUMS-glibc2.28"
    check = (ROOT / "tools" / "release" / "check-release.sh").read_text(encoding="utf-8")
    assert f'EL8_SUMS="{release_stub_sums("2.28")}"' in check
    assert 'el8+=("fsbpyr-stub-$py-$target-glibc2.28.xz")' in check


def test_the_tag_is_the_ref_through_the_environment_and_is_a_version() -> None:
    plan = JOBS["plan"]
    script = script_of(plan, "Which release")

    assert "REF: ${{ github.ref }}" in plan and "REF_NAME: ${{ github.ref_name }}" in plan
    assert "${{ " not in script  # never expanded into the script
    assert r"xyz='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'" in plan
    assert '[ "$REF" != "refs/tags/$REF_NAME" ]' in plan  # a branch dispatch does nothing
    assert 'echo "tag=" >> "$GITHUB_OUTPUT"' in plan
    for job in ("stub", "prepare"):
        assert "if: needs.plan.outputs.tag != ''" in JOBS[job], job
    for job in set(JOBS) - {"plan", "draft"}:
        assert "${{ needs.plan.outputs.tag }}" not in JOBS[job], job
    assert "TAG: ${{ needs.plan.outputs.tag }}" in JOBS["draft"]


def test_draft_waits_for_nothing() -> None:
    """The release of the tag stays a draft until the admin signs it: waiting for it would end the
    job, and a companion is a draft that publishes nothing."""
    draft = JOBS["draft"]

    assert "Wait for the release" not in draft and "sleep" not in draft
    assert "timeout-minutes: 15\n" in draft.split("steps:", 1)[0]


def test_el8_build_builds_the_executable_of_the_commit_tagged_as_its_release() -> None:
    """Only a release's fsbpyr pairs itself with a stub placed beside it; a developer build's
    version names no release (stubs.own_tag), so el8-build tags HEAD, in its job only, as the
    version the commit declares, before it builds."""
    build = EL8_JOBS["el8-build"]

    assert "run: make bootstrap" in build and "run: make build" in build
    assert "ASSET: fsbpyr-${{ matrix.python }}-${{ matrix.target }}" in build
    assert "fetch-depth: 0" in build and "persist-credentials: false" in build
    assert build.index('git tag --force "v$version" HEAD') < build.index("run: make bootstrap")
    assert "name: el8-fsbpyr-${{ matrix.python }}-${{ matrix.target }}" in build
    for job in ("el8-portability", "el8-vm-smoke"):
        assert "name: el8-fsbpyr-" in EL8_JOBS[job] and "gh release" not in EL8_JOBS[job], job


GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}  # fmt: skip


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,  # noqa: S607
                          check=True, env={**os.environ, **GIT_ENV})  # fmt: skip
    return done.stdout.strip()


@pytest.fixture
def commit(tmp_path: Path) -> Path:
    """A repository whose HEAD declares this checkout's version in the three files, after an
    earlier commit that carries that version's release tag."""
    repo = tmp_path / "repo"
    (repo / "src" / "fsbpyr").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "the release")
    for name in ("pyproject.toml", "uv.lock", "src/fsbpyr/__init__.py"):
        (repo / name).write_bytes((ROOT / name).read_bytes())
    version = release_version(repo)
    _git(repo, "tag", f"v{version}")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "after the release")
    return repo


def release_version(repo: Path) -> str:
    text = (repo / "src" / "fsbpyr" / "__init__.py").read_text(encoding="utf-8")
    found = re.search(r'^__version__ = "(.*)"$', text, re.MULTILINE)
    assert found
    return found[1]


def tag_the_commit(repo: Path) -> subprocess.CompletedProcess[str]:
    script = script_of(EL8_JOBS["el8-build"], "Tag the commit as its release")
    return subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=repo,  # noqa: S607
                          capture_output=True, text=True, check=False,
                          env={**os.environ, **GIT_ENV})  # fmt: skip


def test_the_tagged_commit_is_a_release_build_of_its_version(commit: Path) -> None:
    version = release_version(commit)

    done = tag_the_commit(commit)

    assert done.returncode == 0, done.stderr
    assert _git(commit, "tag", "--points-at", "HEAD") == f"v{version}"
    built = version_string(git_state(commit))  # what an executable built here records
    assert built.startswith(f"{version}-") and release_numbers(built.partition("-")[0])


def test_versions_that_disagree_tag_nothing(commit: Path) -> None:
    lock = commit / "uv.lock"
    version = release_version(commit)
    lock.write_text(lock.read_text(encoding="utf-8").replace(
        f'name = "fsbpyr"\nversion = "{version}"', 'name = "fsbpyr"\nversion = "0.0.1"'),
        encoding="utf-8")  # fmt: skip

    done = tag_the_commit(commit)

    assert done.returncode != 0 and "versions disagree" in done.stdout
    assert _git(commit, "tag", "--points-at", "HEAD") == ""


def test_stubs_are_linked_in_the_stub_jobs_and_the_others_use_that_artifact() -> None:
    for job in (JOBS["stub"], EL8_JOBS["el8-stub"]):
        uploads = re.findall(r"upload-artifact@.*\n\s+with:\n\s+name: (.+)", job)
        assert uploads == ["el8-stub-${{ matrix.python }}-${{ matrix.target }}"]
    for job in ("el8-portability", "el8-vm-smoke"):
        assert "name: el8-stub-" in EL8_JOBS[job]
        assert "fsbpyr runtime build" not in EL8_JOBS[job]
    assert "pattern: el8-stub-*" in JOBS["prepare"] and "pattern: el8-stub-*" in JOBS["draft"]


def test_a_vanished_vm_pin_warns_in_the_el8_vm_job_as_in_the_vm_job() -> None:
    """In the gate, a pinned kernel or root file system gone from its mirror (exit 3 of
    tools/vm-smoke.py) must not block a release: a warning, until the pin is renewed."""
    for name in ("vm-smoke", "el8-vm-smoke"):
        job = FULL_JOBS[name]
        assert 'if [ "$status" -eq 3 ]; then' in job and "::warning::" in job, name
        assert 'exit "$status"' in job, name
    assert "--kernel 64k --userland el8" in FULL_JOBS["el8-vm-smoke"]


# ------------------------------------------------------------------- the draft steps, run for real

FAKE_GH = """#!/usr/bin/env bash
# The part of gh the draft job's steps use. FAKE_STATE holds the companion release, if there is one:
# draft (`gh release view --json isDraft --jq .isDraft`), assets (`--json assets --jq
# '.assets[].name'`, one name a line) and sums (its sums asset).
# FAKE_REF the commit of an existing tag ref of the companion (unset: none).
echo "$*" >> "$FAKE_STATE/log"
case "$1 $2" in
    "release view")
        [ -e "$FAKE_STATE/draft" ] || { echo "release not found" >&2; exit 1; }
        case "$*" in
            *"--json isDraft --jq .isDraft") cat "$FAKE_STATE/draft" ;;
            *"--json assets --jq .assets[].name") cat "$FAKE_STATE/assets" ;;
            *) echo "fake gh: no answer for: $*" >&2; exit 2 ;;
        esac ;;
    "release download")
        while [ $# -gt 0 ]; do [ "$1" = --dir ] && dir="$2"; shift; done
        mkdir -p "$dir"
        cp "$FAKE_STATE/sums" "$dir/SHA256SUMS-glibc2.28" ;;
    "api repos/devitops-com/fsbpyr/releases/latest") echo "${FAKE_LATEST:?}" ;;
    "api repos/devitops-com/fsbpyr/git/ref/tags/"*)
        [ -n "${FAKE_REF-}" ] || { echo "gh: Not Found (HTTP 404)" >&2; exit 1; } ;;
    "api repos/devitops-com/fsbpyr/commits/"*)
        echo "${FAKE_SHAS:?}" | tr ',' '\n' | grep "^${2##*/}=" | cut -d= -f2 ;;
esac
"""
TAG = "v1.2.0"
COMPANION = "el8-v1.2.0"
STUBS = [f"{release_stub_asset(python, target, '2.28')}.xz" for python, target in PYTHON_TARGETS]
SUMS_TEXT = "".join(f"{i:064x}  {name.removesuffix('.xz')}\n" for i, name in enumerate(STUBS))



class DraftJob:
    """The draft job's shell, run in a directory with the job's dist/ and a fake gh."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.state = root / "state"
        self.state.mkdir()
        (root / "bin").mkdir()
        gh = root / "bin" / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        (root / "dist").mkdir()
        (root / "dist" / SUMS).write_text(SUMS_TEXT, encoding="utf-8")
        for name in STUBS:
            (root / "dist" / name).write_bytes(b"xz")
        (root / "out").write_text("", encoding="utf-8")

    def release(self, *, draft: bool, sums: str = SUMS_TEXT, assets: list[str] | None = None,
                with_sums: bool = True, with_signature: bool = True) -> None:  # fmt: skip
        names = [*([SUMS] if with_sums else []), *([f"{SUMS}.sig"] if with_signature else []),
                 *(STUBS if assets is None else assets)]  # fmt: skip
        (self.state / "draft").write_text(f"{str(draft).lower()}\n", encoding="utf-8")
        (self.state / "assets").write_text("".join(f"{name}\n" for name in names), encoding="utf-8")
        (self.state / "sums").write_text(sums, encoding="utf-8")

    def run(self, step: str, **env: str) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
                       "FAKE_STATE": str(self.state), "TAG": TAG, "COMPANION": COMPANION,
                       "GH_REPO": "devitops-com/fsbpyr", "GITHUB_SHA": "a" * 40,
                       "GITHUB_OUTPUT": str(self.root / "out"), "RUNNER_TEMP": str(self.root),
                       **env}  # fmt: skip
        bash = requires.tool("bash")
        return subprocess.run([bash, "-e", "-c", step_script(step)], cwd=self.root, env=environment,
                              capture_output=True, text=True, check=False)  # fmt: skip

    @property
    def output(self) -> str:
        return (self.root / "out").read_text(encoding="utf-8").strip()

    @property
    def calls(self) -> list[str]:
        log = self.state / "log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


@pytest.fixture
def job(tmp_path: Path) -> DraftJob:
    return DraftJob(tmp_path)


def test_the_check_of_the_result_runs_whether_or_not_the_companion_was_just_published() -> None:
    draft = JOBS["draft"]
    check = draft.split("- name: Check the result\n", 1)[1].split("run: |", 1)[0]

    assert "if: steps.published.outputs.new != ''" in check
    assert "steps.published.outputs.new == 'true'" in draft.split(
        "- name: The companion tag\n", 1)[1].split("run: |", 1)[0]


def test_nothing_published_yet_goes_on_to_make_the_companion_release(job: DraftJob) -> None:
    result = job.run("Is it published")

    assert result.returncode == 0, result.stderr
    assert job.output == "new=true"
    assert job.calls == [f"release view {COMPANION} --json isDraft --jq .isDraft"]


def test_a_published_companion_with_the_same_stubs_has_nothing_to_publish(job: DraftJob) -> None:
    job.release(draft=False)

    result = job.run("Is it published")

    assert result.returncode == 0, result.stderr
    assert job.output == "new=false"
    assert "nothing to publish" in result.stdout
    assert not [call for call in job.calls if "delete" in call or "create" in call]


@pytest.mark.parametrize("case", ["other sums", "a stub missing", "no sums file"])
def test_a_published_companion_with_other_stubs_fails_and_is_left_alone(
        job: DraftJob, case: str) -> None:  # fmt: skip
    if case == "other sums":
        job.release(draft=False, sums=SUMS_TEXT.replace("0", "1"))
    elif case == "a stub missing":
        job.release(draft=False, assets=STUBS[1:])
    else:  # not downloaded: gh would fail with its own message, not ours
        job.release(draft=False, with_sums=False)

    result = job.run("Is it published")

    assert result.returncode == 1
    assert f"::error::{COMPANION} is published with other stubs" in result.stdout
    assert "immutable" in result.stdout
    assert job.output == ""
    assert not [call for call in job.calls if "delete" in call or "create" in call]


def test_a_draft_is_left_alone(job: DraftJob) -> None:
    """It may be the one that waits for the admin's signature, which a re-run must not delete."""
    job.release(draft=True, sums="other\n", assets=STUBS[:3], with_signature=False)

    result = job.run("Is it published")

    assert result.returncode == 1
    assert f"::error::{COMPANION} is a draft" in result.stdout
    assert not [call for call in job.calls if "delete" in call or "create" in call]


def test_the_companion_is_created_as_a_draft_with_no_signature_and_not_latest(
    job: DraftJob,
) -> None:
    result = job.run("Make the draft of the companion release")

    assert result.returncode == 0, result.stderr
    create, = job.calls  # no `release edit`: it is not published
    assert create.startswith(f"release create {COMPANION} --verify-tag --draft --latest=false ")
    assert "--target" not in create  # the token cannot make the tag: make release pushed it
    assert f"--title EL8 stubs for {TAG}" in create
    assert all(f"dist/{name}" in create for name in [*STUBS, SUMS])
    assert ".sig" not in create


def _stubs_for_the_check(job: DraftJob) -> str:
    """stubs/ as draft downloads it, with sums that list each file in it."""
    stubs = job.root / "stubs"
    stubs.mkdir()
    lines = []
    for name in STUBS:
        (stubs / name).write_bytes(b"an xz")
        lines.append(f"{hashlib.sha256(b'an xz').hexdigest()}  {name}\n")
    (job.root / "dist" / SUMS).write_text("".join(lines), encoding="utf-8")
    return "Check the stubs against the sums"


def test_stubs_that_match_their_sums_go_on(job: DraftJob) -> None:
    step = _stubs_for_the_check(job)

    result = job.run(step)

    assert result.returncode == 0, result.stderr + result.stdout
    assert all((job.root / "dist" / name).is_file() for name in STUBS)


def test_a_stub_that_does_not_match_its_line_stops_the_job(job: DraftJob) -> None:
    step = _stubs_for_the_check(job)
    (job.root / "stubs" / STUBS[0]).write_bytes(b"another")

    assert job.run(step).returncode != 0


def test_a_file_too_many_among_the_stubs_stops_the_job(job: DraftJob) -> None:
    step = _stubs_for_the_check(job)
    (job.root / "stubs" / "fsbpyr-stub-evil.xz").write_bytes(b"not listed")

    result = job.run(step)

    assert result.returncode == 1 and "exactly the files the sums list" in result.stdout


def test_a_published_companion_without_a_signature_fails(job: DraftJob) -> None:
    """It cannot be repaired, and the stubs need a new version; before, it was "nothing to
    publish", exit 0."""
    job.release(draft=False, with_signature=False)

    result = job.run("Is it published")

    assert result.returncode == 1
    assert "is published with other stubs" in result.stdout
    assert not [call for call in job.calls if "delete" in call or "create" in call]


def test_the_companion_tag_must_exist_on_the_tags_commit_before_the_publication(
        job: DraftJob) -> None:  # fmt: skip
    shas = f"{TAG}={'a' * 40},{COMPANION}=" + "{}"

    missing = job.run("The companion tag")
    assert missing.returncode == 1
    assert f"::error::the tag {COMPANION} does not exist: make release pushes it" in missing.stdout
    here = job.run("The companion tag", FAKE_REF="1", FAKE_SHAS=shas.format("a" * 40))
    assert here.returncode == 0, here.stderr
    other = job.run("The companion tag", FAKE_REF="1", FAKE_SHAS=shas.format("b" * 40))
    assert other.returncode == 1
    assert f"::error::the tag {COMPANION} exists on another commit than {TAG}'s" in other.stdout
    assert not [call for call in job.calls if "release" in call]


def test_the_result_is_one_commit_for_both_tags_and_a_latest_release_that_is_not_the_companion(
        job: DraftJob) -> None:  # fmt: skip
    same = f"{TAG}={'a' * 40},{COMPANION}={'a' * 40}"

    assert job.run("Check the result", FAKE_LATEST=TAG, FAKE_SHAS=same).returncode == 0
    newer = job.run("Check the result", FAKE_LATEST="v1.3.0", FAKE_SHAS=same)  # a retry later
    assert newer.returncode == 0, newer.stdout
    wrong_latest = job.run("Check the result", FAKE_LATEST=COMPANION, FAKE_SHAS=same)
    assert wrong_latest.returncode == 1 and "latest" in wrong_latest.stdout
    other_commit = job.run("Check the result", FAKE_LATEST=TAG,
                               FAKE_SHAS=f"{TAG}={'a' * 40},{COMPANION}={'b' * 40}")  # fmt: skip
    assert other_commit.returncode == 1 and "::error::" in other_commit.stdout
