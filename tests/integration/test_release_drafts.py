"""What CI does to a release, in release.yml and el8.yml: it builds, attests and stops at a draft,
and no release key is anywhere near it (docs/signing.md §5.3). GitHub runs them, not these tests:
they pin what can be read from the files, and run the steps' shell with fake programs."""

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from fsbpyr.bytecode import SUPPORTED_PYTHON

from ..support import requires

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
JOB = re.compile(r"^  ([a-z][a-z-]*):\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)", re.MULTILINE | re.DOTALL)
NAMES = ("release.yml", "el8.yml")
BEFORE = {"release.yml": "[build, sbom]",
          "el8.yml": "[plan, stub, build, tests, portability, corpus, vm-smoke]"}  # fmt: skip


def jobs(name: str) -> dict[str, str]:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    return dict(JOB.findall(text.partition("\njobs:\n")[2]))


def step_script(job: str, name: str) -> str:
    """The shell of the step called ``name`` of a job's text."""
    step = job.split(f"- name: {name}\n", 1)[1].split("run: |\n", 1)[1]
    body = step.split("\n      #", 1)[0].split("\n      - ", 1)[0]
    return textwrap.dedent(body) + "\n"


def step_text(job: str, name: str) -> str:
    return job.split(f"- name: {name}\n", 1)[1].split("\n      - ", 1)[0]


# --- no key ------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda path: path.name)
def test_no_workflow_can_reach_a_release_key(path: Path) -> None:
    """Whoever holds the account can run any workflow with whatever it can reach, so nothing of
    the kind may exist to reach: K is on the admin's machine and stays there."""
    text = path.read_text(encoding="utf-8")

    assert "environment:" not in re.sub(r"^\s*#.*$", "", text, flags=re.MULTILINE)
    assert not re.search(r"secrets\.FSBPYR_|FSBPYR_RELEASE_KEY|FSBPYR_SIGNING_PASSPHRASE", text)
    assert "FSBPYR_RECOVERY" not in text and "vars." not in text
    assert "release-signing.sh" not in text and "check-signature.sh" not in text


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda path: path.name)
def test_no_workflow_publishes_a_release(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    assert "--draft=false" not in text
    assert not re.search(r"\bgh release (edit|upload|delete)\b", text)
    assert not re.search(r"^\s+draft: false", text, re.MULTILINE)
    for call in re.findall(r"gh release create [^\n]*(?:\\\n[^\n]*)*", text):
        assert "--draft" in call, call


@pytest.mark.parametrize("name", NAMES)
def test_only_the_jobs_named_here_make_up_the_end_of_a_release(name: str) -> None:
    workflow = jobs(name)

    assert "sign" not in workflow and "publish" not in workflow
    assert "draft" in workflow and "prepare" in workflow


@pytest.mark.parametrize("name", NAMES)
def test_prepare_has_no_secret_and_writes_nothing(name: str) -> None:
    prepare = jobs(name)["prepare"]
    header = prepare.split("    steps:", 1)[0]

    assert f"needs: {BEFORE[name]}\n" in header
    assert "permissions:\n      contents: read\n" in header and "write" not in header
    assert "secrets." not in prepare


@pytest.mark.parametrize("name", NAMES)
def test_draft_needs_prepare_and_keeps_its_implicit_success(name: str) -> None:
    """With no `if` of its own but el8's plan, a failed job before it stops it."""
    draft = jobs(name)["draft"].split("    steps:", 1)[0]

    assert re.search(r"needs: (prepare|\[plan, prepare\])\n", draft)
    assert "if:" not in draft and "cancelled()" not in draft


def test_release_yml_keeps_its_gate_in_prepare() -> None:
    prepare = jobs("release.yml")["prepare"]

    assert "if: github.event_name == 'push' && github.ref_type == 'tag'" in prepare


def test_draft_runs_no_built_code() -> None:
    draft = jobs("release.yml")["draft"]

    assert "make-install" not in draft
    assert "chmod +x" not in draft
    assert "bash tools/release/" in draft  # only the tag's own scripts, as before


def test_the_sums_are_attested_with_what_they_list() -> None:
    """make release-sign takes the sums' attestation as GitHub's word for every other file."""
    release, el8 = jobs("release.yml")["draft"], jobs("el8.yml")["draft"]

    assert "dist/SHA256SUMS\n" in step_text(release, "Attest build provenance")
    assert "dist/SHA256SUMS-glibc2.28\n" in step_text(el8, "Attest build provenance") + "\n"


# --- the sums, run for real ---------------------------------------------------------------------


def bash(body: str, cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([requires.tool("bash"), "-e", "-c", body], cwd=cwd, capture_output=True,
                          text=True, env={**os.environ, **env}, check=False)  # fmt: skip


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """What the draft job has before it attests: the build job's files and prepare's."""
    built, prepared = tmp_path / "dist", tmp_path / "prepared"
    built.mkdir()
    prepared.mkdir()
    for python in ("3.12", "3.13", "3.14", "3.15"):
        for target in ("x86_64-linux-gnu", "aarch64-linux-gnu"):
            asset = f"fsbpyr-{python}-{target}"
            for name in (asset, f"{asset}.xz", f"{asset}.cdx.json",
                         f"THIRD_PARTY_LICENSES-{python}-{target}.txt"):  # fmt: skip
                (built / name).write_text(f"the file {name}\n")
    (prepared / "install.sh").write_bytes(b"#!/bin/sh\n")
    work = tmp_path / "work"
    shutil.copytree(built, work)
    shutil.copy2(prepared / "install.sh", work)
    command = "sha256sum fsbpyr* THIRD_PARTY_LICENSES*.txt install.sh > ../prepared/SHA256SUMS"
    done = bash(command, work, LC_ALL="C")
    assert done.returncode == 0, done.stderr
    shutil.rmtree(work)
    return tmp_path


def _check_files(tree: Path) -> subprocess.CompletedProcess[str]:
    step = step_script(jobs("release.yml")["draft"], "Check the files against the sums")
    return bash(step, tree, LC_ALL="C")


def test_the_sums_prepare_wrote_pass_when_they_are_the_build_jobs(tree: Path) -> None:
    done = _check_files(tree)

    assert done.returncode == 0, done.stderr + done.stdout
    assert (tree / "dist" / "SHA256SUMS").is_file() and (tree / "dist" / "install.sh").is_file()


@pytest.mark.parametrize("change", [
    lambda sums: sums.replace("  fsbpyr-3.14", "  fsbpyr-3.14-evil"),
    lambda sums: f"{'0' * 64}  fsbpyr-3.14-x86_64-linux-gnu\n" + sums,
    lambda sums: sums + f"{'0' * 64}  a-file-never-published\n",
    lambda sums: "\n".join(sums.splitlines()[1:]) + "\n",
    lambda sums: "".join(reversed(sums.splitlines(keepends=True))),
], ids=["another name", "another digest", "a line more", "a line less", "another order"])
def test_sums_prepare_chose_stop_the_job(tree: Path, change: object) -> None:
    """prepare runs built code: these lines would be attested, and the admin would sign them."""
    sums = tree / "prepared" / "SHA256SUMS"
    sums.write_text(change(sums.read_text()))  # type: ignore[operator]

    done = _check_files(tree)

    assert done.returncode == 1
    assert "prepare's SHA256SUMS is not the sums of the build job's files" in done.stdout


def test_the_list_of_assets_is_every_pythons_and_targets() -> None:
    step = step_text(jobs("release.yml")["draft"], "Check the files against the sums")
    listed = re.search(r"for python in ([0-9. ]+); do", step)
    targets = re.search(r"for target in ([a-z0-9_ -]+); do", step)
    built = re.search(r'target: \[(.*?)\]', jobs("release.yml")["sbom"])

    assert listed and targets and built
    assert listed.group(1).split() == ["{}.{}".format(*version)
                                       for version in sorted(SUPPORTED_PYTHON)]  # fmt: skip
    assert targets.group(1).split() == built.group(1).replace('"', "").replace(",", "").split()


@pytest.mark.parametrize("name", ["fsbpyr-evil", "built-evil", "fsbpyr-3.14-x86_64-linux-gnu.evil"])
def test_a_file_too_many_among_the_build_jobs_stops_the_job(tree: Path, name: str) -> None:
    """`pattern: built-*` takes any artifact of the run of such a name, and prepare, which could
    upload one, would list it in its own sums."""
    (tree / "dist" / name).write_bytes(b"not an asset")
    work = bash(f"sha256sum {name} >> ../prepared/SHA256SUMS", tree / "dist")
    assert work.returncode == 0, work.stderr

    done = _check_files(tree)

    assert done.returncode == 1
    assert "the build job's files are not the assets expected" in done.stdout


def test_an_asset_missing_from_the_build_jobs_stops_the_job(tree: Path) -> None:
    (tree / "dist" / "fsbpyr-3.14-x86_64-linux-gnu").unlink()

    assert _check_files(tree).returncode == 1


def test_a_replaced_file_stops_the_job(tree: Path) -> None:
    """A later job can replace an earlier job's artifact of the same run."""
    (tree / "dist" / "fsbpyr-3.14-x86_64-linux-gnu").write_bytes(b"a replaced file")

    assert _check_files(tree).returncode == 1


def test_a_replaced_installer_stops_the_job(tree: Path) -> None:
    (tree / "prepared" / "install.sh").write_bytes(b"#!/bin/sh\necho evil\n")

    assert _check_files(tree).returncode == 1


def test_the_files_are_checked_before_they_are_attested_or_uploaded() -> None:
    draft = jobs("release.yml")["draft"]

    assert (draft.index("- name: Check the files against the sums")
            < draft.index("- name: Attest build provenance")
            < draft.index("- name: Make the draft release"))  # fmt: skip


def test_the_release_is_made_as_a_draft_with_no_signature(tmp_path: Path) -> None:
    logged = tmp_path / "gh.log"
    (tmp_path / "bin").mkdir()
    gh = tmp_path / "bin" / "gh"
    gh.write_text(f'#!/bin/sh\necho "$*" >> "{logged}"\n')
    gh.chmod(0o755)
    (tmp_path / "dist").mkdir()
    for name in ("fsbpyr-3.14-x86_64-linux-gnu", "THIRD_PARTY_LICENSES-3.14.txt", "install.sh",
                 "SHA256SUMS"):  # fmt: skip
        (tmp_path / "dist" / name).write_bytes(b"x")
    step = step_script(jobs("release.yml")["draft"], "Make the draft release")

    done = bash(step, tmp_path, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}", TAG="v1.2.0",
                NOTES="notes.md")  # fmt: skip

    assert done.returncode == 0, done.stderr
    call = logged.read_text()
    assert call.startswith("release create v1.2.0 --verify-tag --draft ")
    assert "dist/SHA256SUMS" in call and ".sig" not in call
