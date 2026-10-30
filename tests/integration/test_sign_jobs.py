"""The sign job and what stands around it, in release.yml and el8.yml (docs/signing.md §5.3).
GitHub runs them, not these tests: they pin what can be read from the files, and run the steps'
shell with fake programs, the release script and real keys. Whether GitHub applies them as read,
the environment's rule above all, is in §11.2."""

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from ..support import requires
from ..support.signing import PASSPHRASE, Key, new_key

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
JOB = re.compile(r"^  ([a-z][a-z-]*):\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)", re.MULTILINE | re.DOTALL)
NAMES = ("release.yml", "el8.yml")
SUMS = {"release.yml": "SHA256SUMS", "el8.yml": "SHA256SUMS-glibc2.28"}
BEFORE = {"release.yml": "[build, sbom]",
          "el8.yml": "[plan, stub, build, tests, portability, corpus, vm-smoke]"}  # fmt: skip
ALLOWED_ACTIONS = {"actions/checkout", "actions/download-artifact", "actions/upload-artifact"}
CONDITION = """if: ${{ !cancelled() && needs.prepare.result == 'success'
            && (needs.sign.result == 'success'
                || (needs.sign.result == 'skipped' && vars.FSBPYR_RECOVERY == '1')) }}"""


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


@pytest.mark.parametrize("name", NAMES)
def test_only_the_sign_job_gets_the_environment_and_its_one_step_the_secrets(name: str) -> None:
    workflow = jobs(name)
    text = (WORKFLOWS / name).read_text(encoding="utf-8")

    assert [job for job, body in workflow.items() if "environment:" in body] == ["sign"]
    assert "    environment: release-signing" in workflow["sign"]
    secrets = re.findall(r"secrets\.FSBPYR_[A-Z_]+", text)
    assert sorted(secrets) == ["secrets.FSBPYR_RELEASE_KEY", "secrets.FSBPYR_SIGNING_PASSPHRASE"]
    step = step_text(workflow["sign"], "Sign")
    assert all(secret in step for secret in secrets)
    assert "secrets." not in workflow["sign"].replace(step, "")
    for job in set(workflow) - {"sign"}:
        assert "RELEASE_KEY" not in workflow[job], job


@pytest.mark.parametrize("name", NAMES)
def test_the_sign_job_runs_no_built_code_and_no_other_action(name: str) -> None:
    sign = jobs(name)["sign"]

    assert set(re.findall(r"uses: ([^@\s]+)@", sign)) == ALLOWED_ACTIONS
    commands = "\n".join(line for line in sign.splitlines() if not line.strip().startswith("#"))
    assert "fsbpyr " not in commands.replace("devitops-com/fsbpyr", "")
    assert "make-install" not in commands and "./dist" not in commands


@pytest.mark.parametrize("name", NAMES)
def test_the_sign_job_reads_and_needs_prepare(name: str) -> None:
    sign = jobs(name)["sign"].split("    steps:", 1)[0]

    assert "permissions:\n      contents: read\n" in sign and "write" not in sign
    assert re.search(r"needs: (prepare|\[plan, prepare\])\n", sign)
    assert "if: vars.FSBPYR_RECOVERY != '1'" in sign
    assert re.search(r"container: docker.io/library/debian:trixie@sha256:[0-9a-f]{64}\n", sign)


@pytest.mark.parametrize("name", NAMES)
def test_the_sign_job_installs_before_it_checks_out_and_keeps_all_history(name: str) -> None:
    """The image has no git, and checkout then takes a tarball with no tag in it. A shallow
    checkout has no other tag."""
    sign = jobs(name)["sign"]

    assert sign.index("apt-get install") < sign.index("actions/checkout@")
    assert sign.index("actions/checkout@") < sign.index("- name: Sign\n")
    assert "fetch-depth: 0" in sign
    for package in ("git", "openssl", "curl", "jq", "ca-certificates"):
        assert re.search(rf"\b{package}\b", sign.split("run: |", 1)[1].split("- uses", 1)[0])


@pytest.mark.parametrize("name", NAMES)
def test_prepare_has_no_secret_and_writes_nothing(name: str) -> None:
    prepare = jobs(name)["prepare"]
    header = prepare.split("    steps:", 1)[0]

    assert f"needs: {BEFORE[name]}\n" in header
    assert "permissions:\n      contents: read\n" in header and "write" not in header
    assert "secrets." not in prepare
    assert "environment:" not in header


@pytest.mark.parametrize("name", NAMES)
def test_publish_needs_a_signature_or_a_recovery(name: str) -> None:
    publish = jobs(name)["publish"]

    assert CONDITION in publish
    assert "needs: [prepare, sign]" in publish or "needs: [plan, prepare, sign]" in publish
    assert "SIGNED: ${{ needs.sign.result == 'success' }}" in publish
    # needs.sign.result decides from here on: the variable is not read again
    assert publish.replace(CONDITION, "").count("FSBPYR_RECOVERY") == 0


def test_release_yml_keeps_its_gate_in_prepare() -> None:
    """Skipping `sign` skips what needs it, so publish's condition drops the implicit success()
    of its needs: a failed sbom job must stop the release in prepare."""
    prepare = jobs("release.yml")["prepare"]

    assert "if: github.event_name == 'push' && github.ref_type == 'tag'" in prepare


def test_publish_runs_no_built_code() -> None:
    publish = jobs("release.yml")["publish"]

    assert "make-install" not in publish
    assert "chmod +x" not in publish
    assert "bash tools/release/" in publish  # only the tag's own scripts, as before


# --- the sums, run for real ---------------------------------------------------------------------


def bash(body: str, cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([requires.tool("bash"), "-e", "-c", body], cwd=cwd, capture_output=True,
                          text=True, env={**os.environ, **env}, check=False)  # fmt: skip


@pytest.fixture
def signing_tree(tmp_path: Path) -> Path:
    """What the sign job has before it signs: the build job's files and prepare's."""
    built, prepared = tmp_path / "signed", tmp_path / "prepared"
    built.mkdir()
    prepared.mkdir()
    (built / "fsbpyr-3.14-x86_64-linux-gnu").write_bytes(b"an executable")
    (built / "THIRD_PARTY_LICENSES-3.14-x86_64-linux-gnu.txt").write_bytes(b"licences")
    (prepared / "install.sh").write_bytes(b"#!/bin/sh\n")
    work = built / "work"
    shutil.copytree(built, work)
    shutil.copy2(prepared / "install.sh", work)
    command = "sha256sum fsbpyr* THIRD_PARTY_LICENSES*.txt install.sh > ../../prepared/SHA256SUMS"
    done = bash(command, work, LC_ALL="C")
    assert done.returncode == 0, done.stderr
    shutil.rmtree(work)
    return tmp_path


def _write_again(tree: Path) -> subprocess.CompletedProcess[str]:
    return bash(step_script(jobs("release.yml")["sign"], "Write the sums again"), tree, LC_ALL="C")


def test_the_sums_prepare_wrote_pass_when_they_are_the_build_jobs(signing_tree: Path) -> None:
    done = _write_again(signing_tree)

    assert done.returncode == 0, done.stderr + done.stdout
    assert (signing_tree / "signed" / "SHA256SUMS").is_file()


@pytest.mark.parametrize("change", [
    lambda sums: sums.replace("  fsbpyr-3.14", "  fsbpyr-3.14-evil"),
    lambda sums: f"{'0' * 64}  fsbpyr-3.14-x86_64-linux-gnu\n" + sums,
    lambda sums: sums + f"{'0' * 64}  a-file-never-published\n",
    lambda sums: "\n".join(sums.splitlines()[1:]) + "\n",
    lambda sums: "".join(reversed(sums.splitlines(keepends=True))),
], ids=["another name", "another digest", "a line more", "a line less", "another order"])
def test_sums_prepare_chose_stop_the_job_before_k_is_there(
    signing_tree: Path, change: object
) -> None:
    """prepare runs built code: without this K signs whatever lines it wrote, and whoever
    intercepts a download could serve a file never published to `self update`."""
    sums = signing_tree / "prepared" / "SHA256SUMS"
    sums.write_text(change(sums.read_text()))  # type: ignore[operator]

    done = _write_again(signing_tree)

    assert done.returncode == 1
    assert "prepare's SHA256SUMS is not the sums of the build job's files" in done.stdout


def test_the_compare_comes_before_the_key() -> None:
    sign = jobs("release.yml")["sign"]

    assert sign.index("- name: Write the sums again") < sign.index("- name: Sign\n")


# --- publish's own check ------------------------------------------------------------------------


@pytest.fixture
def publishing(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "fsbpyr-3.14-x86_64-linux-gnu").write_bytes(b"an executable")
    prepared, signed = tmp_path / "prepared", tmp_path / "signed"
    prepared.mkdir()
    signed.mkdir()
    (prepared / "install.sh").write_bytes(b"#!/bin/sh\n")
    command = "cp ../prepared/install.sh . && sha256sum fsbpyr* install.sh && rm install.sh"
    lines = bash(command, dist)
    assert lines.returncode == 0, lines.stderr
    (signed / "SHA256SUMS").write_text(lines.stdout)
    (signed / "SHA256SUMS.sig").write_bytes(b"a signature")
    shutil.copy2(signed / "SHA256SUMS", prepared / "SHA256SUMS")
    return tmp_path


def _check_files(tree: Path, signed: bool) -> subprocess.CompletedProcess[str]:
    step = step_script(jobs("release.yml")["publish"], "Check the files against the sums")
    return bash(step, tree, SIGNED="true" if signed else "false")


@pytest.mark.parametrize("signed", [True, False], ids=["signed", "a recovery"])
def test_publish_takes_files_that_match_the_sums(publishing: Path, signed: bool) -> None:
    done = _check_files(publishing, signed)

    assert done.returncode == 0, done.stderr
    assert (publishing / "dist" / "SHA256SUMS.sig").exists() == signed


def test_publish_stops_at_a_file_that_does_not_match(publishing: Path) -> None:
    """A later job can replace an earlier job's artifact of the same run."""
    (publishing / "dist" / "fsbpyr-3.14-x86_64-linux-gnu").write_bytes(b"a replaced file")

    assert _check_files(publishing, True).returncode != 0


def test_publish_stops_at_a_replaced_installer(publishing: Path) -> None:
    (publishing / "prepared" / "install.sh").write_bytes(b"#!/bin/sh\necho evil\n")

    assert _check_files(publishing, True).returncode != 0


def test_the_files_are_checked_before_they_are_attested_or_uploaded() -> None:
    publish = jobs("release.yml")["publish"]

    assert (publish.index("- name: Check the files against the sums")
            < publish.index("- name: Attest build provenance")
            < publish.index("- name: Publish the GitHub release"))  # fmt: skip
    assert "dist/SHA256SUMS\n" in step_text(publish, "Attest build provenance")


def _publish_step(tmp_path: Path, signed: bool) -> str:
    logged = tmp_path / "gh.log"
    (tmp_path / "bin").mkdir(exist_ok=True)
    gh = tmp_path / "bin" / "gh"
    gh.write_text(f'#!/bin/sh\necho "$*" >> "{logged}"\n')
    gh.chmod(0o755)
    (tmp_path / "dist").mkdir(exist_ok=True)
    for name in ("fsbpyr-3.14-x86_64-linux-gnu", "THIRD_PARTY_LICENSES-3.14.txt", "install.sh",
                 "SHA256SUMS", "SHA256SUMS.sig"):  # fmt: skip
        (tmp_path / "dist" / name).write_bytes(b"x")
    step = step_script(jobs("release.yml")["publish"], "Publish the GitHub release")
    done = bash(step, tmp_path, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}", TAG="v1.2.0",
                NOTES="notes.md", SIGNED="true" if signed else "false")  # fmt: skip
    assert done.returncode == 0, done.stderr
    return logged.read_text()


def test_a_signed_release_is_published_with_its_signature(tmp_path: Path) -> None:
    call = _publish_step(tmp_path, True)

    assert "dist/SHA256SUMS.sig" in call and "--draft" not in call


def test_in_a_recovery_the_release_stops_at_a_draft(tmp_path: Path) -> None:
    call = _publish_step(tmp_path, False)

    assert "--draft" in call and "SHA256SUMS.sig" not in call


# --- signing, run for real ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def k(tmp_path_factory: pytest.TempPathFactory) -> Key:
    return new_key(tmp_path_factory.mktemp("k") / "k")


@pytest.fixture
def checkout(tmp_path: Path, k: Key) -> Path:
    """The tagged checkout: the tools, the keys it pins and a tag."""
    requires.tool("git")
    tree = tmp_path / "checkout"
    (tree / "tools" / "release").mkdir(parents=True)
    for name in ("release-signing.sh", "check-signature.sh", "previous-release.sh"):
        shutil.copy2(ROOT / "tools" / "release" / name, tree / "tools" / "release" / name)
    (tree / "tools" / "release" / "first-signed-release").write_text("1.2.0\n")
    keys = tree / "src" / "fsbpyr" / "release-keys"
    keys.mkdir(parents=True)
    shutil.copy2(k.pub, keys / "k.pub")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-C", str(tree)]
    for command in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "x"],
                    ["tag", "v1.2.0"]):  # fmt: skip
        subprocess.run([*git, *command], check=True, capture_output=True)
    (tree / "signed").mkdir()
    (tree / "signed" / "SHA256SUMS").write_text("a line\n")
    (tree / "temp").mkdir()
    return tree


def _sign(checkout: Path, key_secret: str, passphrase: str = PASSPHRASE
          ) -> subprocess.CompletedProcess[str]:  # fmt: skip
    step = step_script(jobs("release.yml")["sign"], "Sign")
    return bash(step, checkout, FSBPYR_RELEASE_KEY=key_secret, RUNNER_TEMP=str(checkout / "temp"),
                FSBPYR_SIGNING_PASSPHRASE=passphrase)  # fmt: skip


def test_the_sign_step_signs_and_leaves_no_key_behind(checkout: Path, k: Key) -> None:
    done = _sign(checkout, k.key.read_text())

    assert done.returncode == 0, done.stderr
    assert (checkout / "signed" / "SHA256SUMS.sig").stat().st_size == 3373
    assert list((checkout / "temp").iterdir()) == []


def test_a_secret_that_lost_its_last_lf_signs(checkout: Path, k: Key) -> None:
    assert _sign(checkout, k.key.read_text().rstrip("\n")).returncode == 0


@pytest.mark.parametrize("secret", ["", "not a key\n"], ids=["unset", "junk"])
def test_a_secret_that_is_no_key_stops_the_job_and_leaves_no_key_behind(
    checkout: Path, secret: str
) -> None:
    done = _sign(checkout, secret)

    assert done.returncode == 2
    assert not (checkout / "signed" / "SHA256SUMS.sig").exists()
    assert list((checkout / "temp").iterdir()) == []


def test_an_unset_passphrase_stops_the_job_on_no_terminal(checkout: Path, k: Key) -> None:
    step = step_script(jobs("release.yml")["sign"], "Sign")
    env = {**os.environ, "FSBPYR_RELEASE_KEY": k.key.read_text(), "FSBPYR_SIGNING_PASSPHRASE": "",
           "RUNNER_TEMP": str(checkout / "temp")}  # fmt: skip

    done = subprocess.run([requires.tool("bash"), "-e", "-c", step], cwd=checkout, env=env,
                          capture_output=True, text=True, start_new_session=True,
                          check=False)  # fmt: skip

    assert done.returncode == 2 and "no passphrase" in done.stderr


def _check_as_clients(checkout: Path, tmp_path: Path, tag: str = "v1.2.0"
                      ) -> subprocess.CompletedProcess[str]:  # fmt: skip
    """The sign job's check, with a fake curl for GitHub's list of releases."""
    fake = tmp_path / "curl-bin"
    fake.mkdir(exist_ok=True)
    curl = fake / "curl"
    curl.write_text("#!/bin/sh\necho '[]'\n")
    curl.chmod(0o755)
    requires.tool("jq")
    step = step_script(jobs("release.yml")["sign"], "Check the signature as the clients will")
    return bash(step, checkout, TAG=tag, PATH=f"{fake}:{os.environ['PATH']}")


def test_the_first_signed_release_passes_the_check_of_the_clients(
    checkout: Path, k: Key, tmp_path: Path
) -> None:
    assert _sign(checkout, k.key.read_text()).returncode == 0

    done = _check_as_clients(checkout, tmp_path)

    assert done.returncode == 0, done.stderr
    assert "is the first signed release" in done.stdout


def test_a_secret_holding_another_key_passes_sign_and_stops_at_the_clients_check(
    checkout: Path, tmp_path: Path
) -> None:
    other = new_key(tmp_path / "other")
    assert _sign(checkout, other.key.read_text()).returncode == 0

    done = _check_as_clients(checkout, tmp_path)

    assert done.returncode == 1


def test_a_release_other_than_the_first_needs_a_previous_release_that_pins_keys(
    checkout: Path, k: Key, tmp_path: Path
) -> None:
    """A lookup that goes wrong finds nothing: it stops the release, not the check."""
    (checkout / "tools" / "release" / "first-signed-release").write_text("9.9.9\n")
    assert _sign(checkout, k.key.read_text()).returncode == 0

    done = _check_as_clients(checkout, tmp_path)

    assert done.returncode == 2


def test_the_check_of_the_clients_is_after_signing_and_before_the_upload() -> None:
    for name in NAMES:
        sign = jobs(name)["sign"]

        assert (sign.index("- name: Sign\n")
                < sign.index("- name: Check the signature as the clients will")
                < sign.index("actions/upload-artifact"))  # fmt: skip
