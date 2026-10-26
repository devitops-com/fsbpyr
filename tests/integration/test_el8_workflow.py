"""The shape of .github/workflows/el8.yml, which attaches EL8 stubs to a release outside the
release gate. GitHub runs it, not these tests: they pin what can be read from the file (triggers,
decoupling from the release, pins, permissions, asset names)."""

import re
from pathlib import Path

from fsbpyr.bytecode import SUPPORTED_PYTHON
from fsbpyr.vcs import release_stub_asset, release_stub_sums

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
EL8 = (WORKFLOWS / "el8.yml").read_text(encoding="utf-8")
JOB = re.compile(r"^  ([a-z][a-z-]*):\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)", re.MULTILINE | re.DOTALL)
JOBS = dict(JOB.findall(EL8.partition("\njobs:\n")[2]))
MATRIX_JOBS = ("stub", "tests", "portability", "corpus")
USES = re.compile(r"uses: (\S+)@([0-9a-f]{40}) # (v\d+\.\d+\.\d+)$", re.MULTILINE)


def test_it_runs_after_a_release_and_on_request_and_never_for_a_pull_request() -> None:
    block = EL8.split("\non:\n", 1)[1].split("\n\n", 1)[0]
    lines = [line for line in block.splitlines() if not line.lstrip().startswith("#")]

    assert lines[:4] == ["  workflow_run:", '    workflows: ["Release"]', "    types: [completed]",
                         "  workflow_dispatch:"]  # fmt: skip
    assert "tag:" in block and "required: true" in block
    assert not [line for line in lines if re.search(r"push|pull_request|schedule", line)]
    assert re.search(r"^name: Release$", (WORKFLOWS / "release.yml").read_text(), re.MULTILINE)


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
    assert "group: el8-${{ github.event.workflow_run.head_branch || inputs.tag }}" in EL8
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
            assert re.search(r"needs: (plan|\[plan, stub\])\n", text), name
        assert 'target: ["x86_64-linux-gnu", "aarch64-linux-gnu"]' in text, name
        assert '- {target: "x86_64-linux-gnu", runner: "ubuntu-24.04"}' in text, name
        assert '- {target: "aarch64-linux-gnu", runner: "ubuntu-24.04-arm"}' in text, name
        assert "fail-fast: false" in text, name
    assert "runs-on: ubuntu-24.04-arm" in JOBS["vm-smoke"]


def test_the_plan_reads_the_default_python_of_the_tag() -> None:
    plan = JOBS["plan"]

    assert "python: ${{ steps.release.outputs.python }}" in plan
    assert "contents/.python-version?ref=$tag" in plan
    assert 'echo "python=" >> "$GITHUB_OUTPUT"' in plan  # no tag, no Python
    for name in ("portability",):  # the stub of the entry's Python and target must exist
        assert "needs: [plan, stub]" in JOBS[name]
    stubs = re.search(r"for python in ([0-9. ]+); do", JOBS["publish"])
    assert stubs and stubs.group(1).split() == [
        "{}.{}".format(*version) for version in sorted(SUPPORTED_PYTHON)]  # publish: all of them


def test_the_image_is_the_builds_by_digest() -> None:
    digests = re.findall(r"debian:trixie@sha256:([0-9a-f]{64})", EL8)
    build = re.findall(r"debian:trixie@sha256:([0-9a-f]{64})",
                       (WORKFLOWS / "build.yml").read_text(encoding="utf-8"))  # fmt: skip

    assert len(digests) == 3  # stub, tests and corpus
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


def test_only_publish_writes_and_it_needs_every_other_job() -> None:
    assert re.findall(r"^ +permissions:", EL8, re.MULTILINE) == ["    permissions:"]
    assert "permissions:\n  contents: read\n" in EL8
    publish = JOBS["publish"]
    header = publish.split("    steps:", 1)[0]

    assert "      contents: write" in header
    assert "      id-token: write" in header and "      attestations: write" in header
    assert "actions:" not in header
    assert re.search(r"needs: \[plan, stub, tests, portability, corpus, vm-smoke\]", header)
    assert set(JOBS) - {"publish"} == {"plan", "stub", "tests", "portability", "corpus", "vm-smoke"}
    assert "continue-on-error" not in EL8
    assert "checkout" not in publish  # it runs nothing of the tag's


def test_publish_touches_the_el8_assets_of_the_release_only() -> None:
    publish = JOBS["publish"]
    uploads = re.findall(r"gh release upload .*", publish)

    assert uploads == ['gh release upload "$TAG" dist/fsbpyr-stub-*.xz --clobber',
                       f'gh release upload "$TAG" dist/{release_stub_sums("2.28")} --clobber']
    assert not re.search(r"gh release (create|edit|delete)|gh api", publish)
    assert uploads[-1].count("SHA256SUMS-glibc2.28") == 1  # the sums last


def test_the_asset_names_are_those_the_stub_fetch_reads() -> None:
    asset = release_stub_asset("${{ matrix.python }}", "${{ matrix.target }}", "2.28")

    assert f"ASSET: {asset}\n" in JOBS["stub"]
    assert f"STUB: {asset}\n" in JOBS["portability"]
    assert release_stub_sums("2.28") == "SHA256SUMS-glibc2.28"
    assert 'echo "version=$(cut -d. -f1,2 .python-version)"' in JOBS["vm-smoke"]
    check = (ROOT / "tools" / "release" / "check-release.sh").read_text(encoding="utf-8")
    assert f'EL8_SUMS="{release_stub_sums("2.28")}"' in check
    assert 'el8+=("fsbpyr-stub-$py-$target-glibc2.28.xz")' in check


def test_the_tag_comes_from_the_event_through_the_environment_and_is_a_version() -> None:
    plan = JOBS["plan"]

    assert "RUN_REF: ${{ github.event.workflow_run.head_branch }}" in plan
    assert "GIVEN: ${{ inputs.tag }}" in plan
    assert "${{ inputs." not in plan.split("run: |", 1)[1]  # never expanded into the script
    assert "${{ github.event.workflow_run.head_branch }}" not in plan.split("run: |", 1)[1]
    assert r"xyz='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'" in plan
    assert '[ "$RUN_CONCLUSION" != success ]' in plan and '[ "$RUN_EVENT" != push ]' in plan
    for job in ("stub", "tests", "portability", "corpus", "vm-smoke", "publish"):
        assert "if: needs.plan.outputs.tag != ''" in JOBS[job], job
        assert "ref: refs/tags/${{ needs.plan.outputs.tag }}" in JOBS[job] or job == "publish"


def test_stubs_are_linked_in_the_stub_job_and_the_others_use_that_artifact() -> None:
    uploads = re.findall(r"upload-artifact@.*\n\s+with:\n\s+name: (.+)", JOBS["stub"])

    assert uploads == ["el8-stub-${{ matrix.python }}-${{ matrix.target }}"]
    for job in ("portability", "vm-smoke"):
        assert "name: el8-stub-" in JOBS[job]
        assert "fsbpyr runtime build" not in JOBS[job]
