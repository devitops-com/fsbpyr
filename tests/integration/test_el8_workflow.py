"""The shape of .github/workflows/el8.yml, which publishes EL8 stubs in a companion release
outside the release gate. GitHub runs it, not these tests: they pin what can be read from the file
(triggers, decoupling from the release, pins, permissions, asset names, and that no job checks out
or runs anything but the commit that started the run), and run the shell of the publish steps
against a fake `gh`."""

import fnmatch
import hashlib
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

from fsbpyr.bytecode import SUPPORTED_PYTHON
from fsbpyr.vcs import release_stub_asset, release_stub_sums

from ..support import requires

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
EL8 = (WORKFLOWS / "el8.yml").read_text(encoding="utf-8")
JOB = re.compile(r"^  ([a-z][a-z-]*):\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)", re.MULTILINE | re.DOTALL)
JOBS = dict(JOB.findall(EL8.partition("\njobs:\n")[2]))
MATRIX_JOBS = ("stub", "build", "tests", "portability", "corpus")
SUMS = release_stub_sums("2.28")
PYTHON_TARGETS = [(f"{major}.{minor}", target)
                  for major, minor in sorted(SUPPORTED_PYTHON)
                  for target in ("x86_64-linux-gnu", "aarch64-linux-gnu")]  # fmt: skip
USES = re.compile(r"uses: (\S+)@([0-9a-f]{40}) # (v\d+\.\d+\.\d+)$", re.MULTILINE)


def step_script(name: str) -> str:
    """The shell of the publish job's step called `name`."""
    step = JOBS["publish"].split(f"- name: {name}\n", 1)[1].split("run: |\n", 1)[1]
    body = step.split("\n      #", 1)[0].split("\n      - ", 1)[0]  # up to the next comment or step
    return textwrap.dedent(body) + "\n"


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
    assert len(re.findall(r"uses: actions/checkout@", EL8)) == 8
    assert not re.search(r"^\s+ref:", EL8, re.MULTILINE)
    assert "workflow_run" not in EL8 and "inputs." not in EL8
    published = 'gh release download "$COMPANION" --pattern SHA256SUMS'  # the idempotence check
    assert "gh release download" not in EL8.replace(published, "")
    assert EL8.count("persist-credentials: false") == 8


def test_the_release_and_its_gate_know_nothing_of_it() -> None:
    """EL8 jobs are no part of the release or of the gate (owner, 2026-10-25): a release neither
    waits for them nor depends on them."""
    for path in (WORKFLOWS / "release.yml", WORKFLOWS / "full-suite.yml", WORKFLOWS / "build.yml",
                 ROOT / "tools" / "release" / "green-run.sh",
                 ROOT / "tools" / "release" / "release.sh"):  # fmt: skip
        text = path.read_text(encoding="utf-8").lower()
        assert "el8.yml" not in text and "glibc2.28" not in text, path.name
        assert "--glibc" not in text and "workflow_run" not in text, path.name


def test_a_tag_is_done_once() -> None:
    assert "group: el8-${{ github.ref }}" in EL8
    assert "cancel-in-progress: false" in EL8


def test_the_jobs_run_every_target_on_a_runner_of_the_targets_architecture() -> None:
    """The stub job links every Python; the others run the default Python only (owner,
    2026-10-26), which the plan job reads from the tag's .python-version."""
    supported = ", ".join(f'"{"{}.{}".format(*version)}"' for version in sorted(SUPPORTED_PYTHON))
    default = "python: ${{ fromJSON(format('[\"{0}\"]', needs.plan.outputs.python)) }}"
    for name in MATRIX_JOBS:
        text = JOBS[name].split("    steps:", 1)[0]
        assert "    runs-on: ${{ matrix.runner }}\n" in text, name
        if name == "stub":
            assert f"python: [{supported}]" in text
        else:
            assert default in text, name
            assert re.search(r"needs: (plan|\[plan, stub, build\])\n", text), name
        assert 'target: ["x86_64-linux-gnu", "aarch64-linux-gnu"]' in text, name
        assert '- {target: "x86_64-linux-gnu", runner: "ubuntu-24.04"}' in text, name
        assert '- {target: "aarch64-linux-gnu", runner: "ubuntu-24.04-arm"}' in text, name
        assert "fail-fast: false" in text, name
    assert "runs-on: ubuntu-24.04-arm" in JOBS["vm-smoke"]


def test_the_plan_reads_the_default_python_of_the_tag() -> None:
    plan = JOBS["plan"]

    assert "python: ${{ steps.release.outputs.python }}" in plan
    assert 'python="$(cut -d. -f1,2 .python-version)"' in plan
    assert 'echo "python=" >> "$GITHUB_OUTPUT"' in plan  # no tag, no Python
    for name in ("portability", "vm-smoke"):  # the stub and the executable of the entry must exist
        assert "needs: [plan, stub, build]" in JOBS[name]
    stubs = re.search(r"for python in ([0-9. ]+); do", JOBS["prepare"])
    assert stubs and stubs.group(1).split() == [
        "{}.{}".format(*version) for version in sorted(SUPPORTED_PYTHON)]  # publish: all of them


def test_the_image_is_the_builds_by_digest() -> None:
    digests = re.findall(r"debian:trixie@sha256:([0-9a-f]{64})", EL8)
    build = re.findall(r"debian:trixie@sha256:([0-9a-f]{64})",
                       (WORKFLOWS / "build.yml").read_text(encoding="utf-8"))  # fmt: skip

    assert len(digests) == 5  # stub, build, tests, corpus and sign
    assert set(digests) == set(build) and len(set(build)) == 1


def test_every_action_is_pinned_by_commit_as_the_other_workflows_pin_it() -> None:
    others = {}
    for name in ("build.yml", "release.yml", "full-suite.yml"):
        others.update({action: sha for action, sha, _ in USES.findall(
            (WORKFLOWS / name).read_text(encoding="utf-8"))})  # fmt: skip
    used = USES.findall(EL8)

    assert len(used) == len(re.findall(r"^\s+(?:- )?uses: ", EL8, re.MULTILINE))
    for action, sha, _ in used:
        assert others.get(action) == sha, action


def test_only_publish_writes_and_prepare_needs_every_other_job() -> None:
    assert len(re.findall(r"^ +permissions:", EL8, re.MULTILINE)) == 3  # prepare, sign, publish
    assert "permissions:\n  contents: read\n" in EL8
    publish = JOBS["publish"]
    header = publish.split("    steps:", 1)[0]
    prepare = JOBS["prepare"].split("    steps:", 1)[0]

    assert "      contents: write" in header
    assert "      id-token: write" in header and "      attestations: write" in header
    assert "actions:" not in header
    assert re.search(r"needs: \[plan, stub, build, tests, portability, corpus, vm-smoke\]", prepare)
    assert "needs: [plan, prepare, sign]" in header
    assert "      contents: read" in prepare and "write" not in prepare
    assert set(JOBS) - {"publish"} == {"plan", "stub", "build", "tests", "portability", "corpus",
                                       "vm-smoke", "prepare", "sign"}  # fmt: skip
    assert "continue-on-error" not in EL8
    assert "checkout" not in publish  # it runs nothing of the tag's


def test_publish_makes_the_companion_release_and_leaves_the_release_of_the_tag_alone() -> None:
    publish = JOBS["publish"]
    create = re.sub(r"\\\n\s*", "", step_script("Publish the companion release"))  # joined lines
    created = re.findall(r"gh release create .*", create)

    assert "COMPANION: el8-${{ needs.plan.outputs.tag }}" in publish
    assert len(created) == 1
    assert created[0].startswith('gh release create "$COMPANION" --verify-tag --draft ')
    for flag in ('--latest=false', "dist/fsbpyr-stub-*.xz", f"dist/{release_stub_sums('2.28')}"):
        assert flag in created[0], flag
    assert "--target" not in created[0]  # the tag exists: make release pushed it
    assert re.findall(r"gh release edit .*", create) == [
        'gh release edit "$COMPANION" --draft=false --latest=false']  # the publishing call too
    assert create.index("gh release create") < create.index("gh release edit")
    assert not re.search(r'gh release (upload|create|edit|delete) "\$TAG"', publish)
    assert "--clobber" not in publish and "gh release upload" not in publish
    assert re.findall(r"gh release delete .*", publish) == ['gh release delete "$COMPANION" --yes']
    assert "--cleanup-tag" not in publish  # a draft has no tag
    assert "--source-ref refs/tags/v" in publish  # the attestations come from a run on the tag
    assert ("--signer-workflow $GH_REPO/.github/workflows/el8.yml --source-ref refs/tags/$TAG"
            in create)  # the notes say how to verify


def test_the_companion_tag_starts_no_workflow_and_names_no_release() -> None:
    """The prefix must not start with "v": release.yml and el8.yml run on tags v*, the ruleset
    of the release tags covers refs/tags/v*, and every tool that looks a release up takes vX.Y.Z."""
    companion = "el8-v1.2.3"
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")

    assert "COMPANION: el8-${{ needs.plan.outputs.tag }}" in JOBS["publish"]
    assert not fnmatch.fnmatchcase(companion, "v*")
    assert not re.match(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", companion)
    assert '    tags: ["v*"]' in release and '    tags: ["v*"]' in EL8


def test_it_is_the_companion_that_is_looked_up_for_the_idempotence_check() -> None:
    script = step_script("Is it published")

    assert 'gh release view "$COMPANION" --json isDraft --jq .isDraft' in script
    assert "jq " not in script.replace("--jq ", "")  # gh's own jq: nothing to install
    assert 'gh release delete "$COMPANION" --yes' in script
    assert "is published with other stubs" in script and "immutable" in script
    assert "$TAG" not in script.replace("$TAG's", "")


def test_the_asset_names_are_those_the_stub_fetch_reads() -> None:
    asset = release_stub_asset("${{ matrix.python }}", "${{ matrix.target }}", "2.28")

    assert f"ASSET: {asset}\n" in JOBS["stub"]
    assert f"STUB: {asset}\n" in JOBS["portability"]
    assert release_stub_sums("2.28") == "SHA256SUMS-glibc2.28"
    assert 'echo "version=$(cut -d. -f1,2 .python-version)"' in JOBS["vm-smoke"]
    check = (ROOT / "tools" / "release" / "check-release.sh").read_text(encoding="utf-8")
    assert f'EL8_SUMS="{release_stub_sums("2.28")}"' in check
    assert 'el8+=("fsbpyr-stub-$py-$target-glibc2.28.xz")' in check


def test_the_tag_is_the_ref_through_the_environment_and_is_a_version() -> None:
    plan = JOBS["plan"]
    script = plan.split("run: |", 1)[1]

    assert "REF: ${{ github.ref }}" in plan and "REF_NAME: ${{ github.ref_name }}" in plan
    assert "${{ " not in script  # never expanded into the script
    assert r"xyz='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'" in plan
    assert '[ "$REF" != "refs/tags/$REF_NAME" ]' in plan  # a branch dispatch does nothing
    assert 'echo "tag=" >> "$GITHUB_OUTPUT"' in plan
    assert 'echo "python=" >> "$GITHUB_OUTPUT"' in plan
    for job in ("stub", "build", "tests", "portability", "corpus", "vm-smoke", "prepare"):
        assert "if: needs.plan.outputs.tag != ''" in JOBS[job], job
    for job in set(JOBS) - {"plan", "sign", "publish"}:
        assert "${{ needs.plan.outputs.tag }}" not in JOBS[job], job
    assert "TAG: ${{ needs.plan.outputs.tag }}" in JOBS["publish"]
    assert "TAG: ${{ needs.plan.outputs.tag }}" in JOBS["sign"]


def test_publish_waits_for_the_release_of_the_tag() -> None:
    publish = JOBS["publish"]
    wait = publish.split("name: Wait for the release", 1)[1].split("      - name:", 1)[0]

    assert "seq 1 120" in wait and "sleep 30" in wait  # up to an hour, every 30 seconds
    assert 'gh release view "$TAG" --json isDraft --jq .isDraft' in wait  # published, not a draft
    assert "does not exist yet; dispatch el8.yml on the tag ref once it does" in wait
    assert "timeout-minutes: 75\n" in publish.split("steps:", 1)[0]


def test_the_build_job_builds_the_release_executable_and_nothing_downloads_it() -> None:
    build = JOBS["build"]

    assert "run: make bootstrap" in build and "run: make build" in build
    assert "ASSET: fsbpyr-${{ matrix.python }}-${{ matrix.target }}" in build
    assert "fetch-depth: 0" in build  # the tag the executable records
    assert "name: el8-fsbpyr-${{ matrix.python }}-${{ matrix.target }}" in build
    for job in ("portability", "vm-smoke"):
        assert "name: el8-fsbpyr-" in JOBS[job] and "gh release" not in JOBS[job], job


def test_stubs_are_linked_in_the_stub_job_and_the_others_use_that_artifact() -> None:
    uploads = re.findall(r"upload-artifact@.*\n\s+with:\n\s+name: (.+)", JOBS["stub"])

    assert uploads == ["el8-stub-${{ matrix.python }}-${{ matrix.target }}"]
    for job in ("portability", "vm-smoke"):
        assert "name: el8-stub-" in JOBS[job]
        assert "fsbpyr runtime build" not in JOBS[job]


# ----------------------------------------------------------------- the publish steps, run for real

FAKE_GH = """#!/usr/bin/env bash
# The part of gh the publish steps use. FAKE_STATE holds the companion release, if there is one:
# draft (`gh release view --json isDraft --jq .isDraft`), assets (`--json assets --jq
# '.assets[].name'`, one name a line) and sums (its sums asset).
# FAKE_TAG_DRAFT is isDraft of the release of the tag (unset: there is none), FAKE_REF the commit
# of an existing tag ref of the companion (unset: none).
echo "$*" >> "$FAKE_STATE/log"
case "$1 $2" in
    "release view")
        if [ "$3" = "v1.2.0" ]; then
            [ -n "${FAKE_TAG_DRAFT-}" ] || { echo "release not found" >&2; exit 1; }
            echo "$FAKE_TAG_DRAFT"
            exit 0
        fi
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



class Publish:
    """The publish job's shell, run in a directory with the job's dist/ and a fake gh."""

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
        (root / "dist" / f"{SUMS}.sig").write_bytes(b"a signature")
        for name in STUBS:
            (root / "dist" / name).write_bytes(b"xz")
        (root / "out").write_text("", encoding="utf-8")
        sleep = root / "bin" / "sleep"  # the wait step waits up to an hour
        sleep.write_text("#!/bin/sh\n", encoding="utf-8")
        sleep.chmod(0o755)

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
                       "SIGNED": "true", **env}  # fmt: skip
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
def publish(tmp_path: Path) -> Publish:
    return Publish(tmp_path)


def test_the_check_of_the_result_runs_whether_or_not_the_companion_was_just_published() -> None:
    publish = JOBS["publish"]
    check = publish.split("- name: Check the result\n", 1)[1].split("run: |", 1)[0]

    assert "if: steps.published.outputs.new != ''" in check
    assert "steps.published.outputs.new == 'true'" in publish.split(
        "- name: The companion tag\n", 1)[1].split("run: |", 1)[0]


def test_nothing_published_yet_goes_on_to_make_the_companion_release(publish: Publish) -> None:
    result = publish.run("Is it published")

    assert result.returncode == 0, result.stderr
    assert publish.output == "new=true"
    assert publish.calls == [f"release view {COMPANION} --json isDraft --jq .isDraft"]


def test_a_published_companion_with_the_same_stubs_has_nothing_to_publish(publish: Publish) -> None:
    publish.release(draft=False)

    result = publish.run("Is it published")

    assert result.returncode == 0, result.stderr
    assert publish.output == "new=false"
    assert "nothing to publish" in result.stdout
    assert not [call for call in publish.calls if "delete" in call or "create" in call]


@pytest.mark.parametrize("case", ["other sums", "a stub missing", "no sums file"])
def test_a_published_companion_with_other_stubs_fails_and_is_left_alone(
        publish: Publish, case: str) -> None:  # fmt: skip
    if case == "other sums":
        publish.release(draft=False, sums=SUMS_TEXT.replace("0", "1"))
    elif case == "a stub missing":
        publish.release(draft=False, assets=STUBS[1:])
    else:  # not downloaded: gh would fail with its own message, not ours
        publish.release(draft=False, with_sums=False)

    result = publish.run("Is it published")

    assert result.returncode == 1
    assert f"::error::{COMPANION} is published with other stubs" in result.stdout
    assert "immutable" in result.stdout
    assert publish.output == ""
    assert not [call for call in publish.calls if "delete" in call or "create" in call]


def test_a_draft_left_by_a_run_that_stopped_is_deleted_and_made_again(publish: Publish) -> None:
    publish.release(draft=True, sums="other\n", assets=STUBS[:3])

    result = publish.run("Is it published")

    assert result.returncode == 0, result.stderr
    assert publish.output == "new=true"
    assert publish.calls == [f"release view {COMPANION} --json isDraft --jq .isDraft",
                             f"release delete {COMPANION} --yes"]  # fmt: skip


def test_the_companion_is_created_as_a_draft_and_published_not_latest(publish: Publish) -> None:
    result = publish.run("Publish the companion release")

    assert result.returncode == 0, result.stderr
    create, edit = publish.calls
    assert create.startswith(f"release create {COMPANION} --verify-tag --draft --latest=false ")
    assert "--target" not in create  # the token cannot make the tag: make release pushed it
    assert f"--title EL8 stubs for {TAG}" in create
    assert all(f"dist/{name}" in create for name in [*STUBS, SUMS, f"{SUMS}.sig"])
    assert edit == f"release edit {COMPANION} --draft=false --latest=false"


def test_in_a_recovery_publish_stops_at_a_draft_without_a_signature(publish: Publish) -> None:
    """R signs by hand: the sums, once their attestation is checked, then the draft is published
    with `gh release edit --draft=false --latest=false`."""
    (publish.root / "dist" / f"{SUMS}.sig").unlink()

    result = publish.run("Publish the companion release", SIGNED="false")

    assert result.returncode == 0, result.stderr
    assert len(publish.calls) == 1  # no `release edit`: it is not published
    assert publish.calls[0].startswith(f"release create {COMPANION} --verify-tag --draft ")
    assert f"{SUMS}.sig" not in publish.calls[0]


def _stubs_for_the_check(publish: Publish) -> str:
    """stubs/ as publish downloads it, with sums that list each file in it."""
    stubs = publish.root / "stubs"
    stubs.mkdir()
    lines = []
    for name in STUBS:
        (stubs / name).write_bytes(b"an xz")
        lines.append(f"{hashlib.sha256(b'an xz').hexdigest()}  {name}\n")
    (publish.root / "dist" / SUMS).write_text("".join(lines), encoding="utf-8")
    return "Check the stubs against the sums"


def test_stubs_that_match_their_sums_go_on(publish: Publish) -> None:
    step = _stubs_for_the_check(publish)

    result = publish.run(step)

    assert result.returncode == 0, result.stderr + result.stdout
    assert all((publish.root / "dist" / name).is_file() for name in STUBS)


def test_a_stub_that_does_not_match_its_line_stops_publish(publish: Publish) -> None:
    step = _stubs_for_the_check(publish)
    (publish.root / "stubs" / STUBS[0]).write_bytes(b"another")

    assert publish.run(step).returncode != 0


def test_a_file_too_many_among_the_stubs_stops_publish(publish: Publish) -> None:
    step = _stubs_for_the_check(publish)
    (publish.root / "stubs" / "fsbpyr-stub-evil.xz").write_bytes(b"not listed")

    result = publish.run(step)

    assert result.returncode == 1 and "exactly the files the sums list" in result.stdout


def test_a_published_companion_without_a_signature_fails(publish: Publish) -> None:
    """It cannot be repaired, and the stubs need a new version; before, it was "nothing to
    publish", exit 0."""
    publish.release(draft=False, with_signature=False)

    result = publish.run("Is it published")

    assert result.returncode == 1
    assert "is published with other stubs" in result.stdout
    assert not [call for call in publish.calls if "delete" in call or "create" in call]


def test_in_a_recovery_a_draft_is_left_alone(publish: Publish) -> None:
    """It may be the one that waits for R's signature, which a re-run must not delete."""
    publish.release(draft=True, with_signature=False)

    result = publish.run("Is it published", SIGNED="false")

    assert result.returncode == 1
    assert f"::error::{COMPANION} is a draft" in result.stdout
    assert not [call for call in publish.calls if "delete" in call or "create" in call]


def test_the_wait_goes_on_until_the_release_of_the_tag_is_published(publish: Publish) -> None:
    assert publish.run("Wait for the release", FAKE_TAG_DRAFT="false").returncode == 0
    for state in ("true", ""):  # a draft is not the release, nor is no release
        result = publish.run("Wait for the release", FAKE_TAG_DRAFT=state)
        assert result.returncode == 1, state
        assert f"::error::release {TAG} does not exist yet" in result.stdout


def test_the_companion_tag_must_exist_on_the_tags_commit_before_the_publication(
        publish: Publish) -> None:  # fmt: skip
    shas = f"{TAG}={'a' * 40},{COMPANION}=" + "{}"

    missing = publish.run("The companion tag")
    assert missing.returncode == 1
    assert f"::error::the tag {COMPANION} does not exist: make release pushes it" in missing.stdout
    here = publish.run("The companion tag", FAKE_REF="1", FAKE_SHAS=shas.format("a" * 40))
    assert here.returncode == 0, here.stderr
    other = publish.run("The companion tag", FAKE_REF="1", FAKE_SHAS=shas.format("b" * 40))
    assert other.returncode == 1
    assert f"::error::the tag {COMPANION} exists on another commit than {TAG}'s" in other.stdout
    assert not [call for call in publish.calls if "release" in call]


def test_the_result_is_one_commit_for_both_tags_and_a_latest_release_that_is_not_the_companion(
        publish: Publish) -> None:  # fmt: skip
    same = f"{TAG}={'a' * 40},{COMPANION}={'a' * 40}"

    assert publish.run("Check the result", FAKE_LATEST=TAG, FAKE_SHAS=same).returncode == 0
    newer = publish.run("Check the result", FAKE_LATEST="v1.3.0", FAKE_SHAS=same)  # a retry later
    assert newer.returncode == 0, newer.stdout
    wrong_latest = publish.run("Check the result", FAKE_LATEST=COMPANION, FAKE_SHAS=same)
    assert wrong_latest.returncode == 1 and "latest" in wrong_latest.stdout
    other_commit = publish.run("Check the result", FAKE_LATEST=TAG,
                               FAKE_SHAS=f"{TAG}={'a' * 40},{COMPANION}={'b' * 40}")  # fmt: skip
    assert other_commit.returncode == 1 and "::error::" in other_commit.stdout
