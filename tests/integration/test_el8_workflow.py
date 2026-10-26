"""The shape of .github/workflows/el8.yml, which attaches EL8 stubs to a release outside the
release gate. GitHub runs it, not these tests: they pin what can be read from the file (triggers,
decoupling from the release, pins, permissions, asset names, and that no job checks out or runs
anything but the commit that started the run)."""

import re
from pathlib import Path

from fsbpyr.bytecode import SUPPORTED_PYTHON
from fsbpyr.vcs import release_stub_asset, release_stub_sums

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
EL8 = (WORKFLOWS / "el8.yml").read_text(encoding="utf-8")
JOB = re.compile(r"^  ([a-z][a-z-]*):\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)", re.MULTILINE | re.DOTALL)
JOBS = dict(JOB.findall(EL8.partition("\njobs:\n")[2]))
MATRIX_JOBS = ("stub", "build", "tests", "portability", "corpus")
USES = re.compile(r"uses: (\S+)@([0-9a-f]{40}) # (v\d+\.\d+\.\d+)$", re.MULTILINE)


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
    assert len(re.findall(r"uses: actions/checkout@", EL8)) == 7
    assert not re.search(r"^\s+ref:", EL8, re.MULTILINE)
    assert "workflow_run" not in EL8 and "inputs." not in EL8
    published = 'gh release download "$TAG" --pattern SHA256SUMS'  # publish's idempotence check
    assert "gh release download" not in EL8.replace(published, "")
    assert EL8.count("persist-credentials: false") == 7


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
    stubs = re.search(r"for python in ([0-9. ]+); do", JOBS["publish"])
    assert stubs and stubs.group(1).split() == [
        "{}.{}".format(*version) for version in sorted(SUPPORTED_PYTHON)]  # publish: all of them


def test_the_image_is_the_builds_by_digest() -> None:
    digests = re.findall(r"debian:trixie@sha256:([0-9a-f]{64})", EL8)
    build = re.findall(r"debian:trixie@sha256:([0-9a-f]{64})",
                       (WORKFLOWS / "build.yml").read_text(encoding="utf-8"))  # fmt: skip

    assert len(digests) == 4  # stub, build, tests and corpus
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
    assert re.search(r"needs: \[plan, stub, build, tests, portability, corpus, vm-smoke\]", header)
    assert set(JOBS) - {"publish"} == {"plan", "stub", "build", "tests", "portability", "corpus",
                                       "vm-smoke"}  # fmt: skip
    assert "continue-on-error" not in EL8
    assert "checkout" not in publish  # it runs nothing of the tag's


def test_publish_touches_the_el8_assets_of_the_release_only() -> None:
    publish = JOBS["publish"]
    uploads = re.findall(r"gh release upload .*", publish)

    assert uploads == ['gh release upload "$TAG" dist/fsbpyr-stub-*.xz --clobber',
                       f'gh release upload "$TAG" dist/{release_stub_sums("2.28")} --clobber']
    assert not re.search(r"gh release (create|edit|delete)|gh api", publish)
    assert "--source-ref refs/tags/v" in publish  # the attestations come from a run on the tag
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


def test_the_tag_is_the_ref_through_the_environment_and_is_a_version() -> None:
    plan = JOBS["plan"]
    script = plan.split("run: |", 1)[1]

    assert "REF: ${{ github.ref }}" in plan and "REF_NAME: ${{ github.ref_name }}" in plan
    assert "${{ " not in script  # never expanded into the script
    assert r"xyz='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'" in plan
    assert '[ "$REF" != "refs/tags/$REF_NAME" ]' in plan  # a branch dispatch does nothing
    assert 'echo "tag=" >> "$GITHUB_OUTPUT"' in plan
    assert 'echo "python=" >> "$GITHUB_OUTPUT"' in plan
    for job in ("stub", "build", "tests", "portability", "corpus", "vm-smoke", "publish"):
        assert "if: needs.plan.outputs.tag != ''" in JOBS[job], job
    for job in set(JOBS) - {"plan", "publish"}:
        assert "${{ needs.plan.outputs.tag }}" not in JOBS[job], job
    assert "TAG: ${{ needs.plan.outputs.tag }}" in JOBS["publish"]


def test_publish_waits_for_the_release_of_the_tag() -> None:
    publish = JOBS["publish"]
    wait = publish.split("name: Wait for the release", 1)[1].split("      - name:", 1)[0]

    assert "seq 1 120" in wait and "sleep 30" in wait  # up to an hour, every 30 seconds
    assert 'gh release view "$TAG"' in wait
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
