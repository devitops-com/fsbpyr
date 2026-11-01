"""The "installer with FSBPYR_VERIFY=1" step of tools/release/check-release.sh, run with a stand-in
for the release's install.sh and a fake `gh`. The step itself runs the real ones, on a real release:
this is about what it makes of their answers. The rest of the script needs a release to download,
so only this block is cut out of it."""

import re
import subprocess
from pathlib import Path

import pytest

from ..support import requires

ROOT = Path(__file__).parents[2]
SCRIPT = (ROOT / "tools" / "release" / "check-release.sh").read_text(encoding="utf-8")
HELPERS = "\n".join(re.findall(r"^(?:ok|fail|skip|step)\(\).*$", SCRIPT, re.MULTILINE))
STEP = 'step "installer with FSBPYR_VERIFY=1"' + SCRIPT.split(
    'step "installer with FSBPYR_VERIFY=1"', 1)[1].split('step "dispatch"', 1)[0]  # fmt: skip
ASSET = b"the release's executable"
# What the installer does: with GH_TOKEN it is logged in. EVIL_* make it go wrong.
INSTALLER = """[ "$FSBPYR_VERIFY" = 1 ] || { echo "the check was not asked for" >&2; exit 9; }
if [ -z "${GH_TOKEN-}" ]; then
    if [ -n "${NO_LOGIN_INSTALLS-}" ]; then :
    elif [ -n "${NO_LOGIN_READS_AS_TAMPERING-}" ]; then
        echo "error: the attestation of the download does not verify" >&2; exit 1
    else
        echo "error: FSBPYR_VERIFY=1 needs gh to be logged in, even for a public repository" >&2
        exit 1
    fi
elif [ -n "${ATTESTATION_FAILS-}" ]; then
    echo "error: the attestation of the download does not verify" >&2; exit 1
fi
mkdir -p "$FSBPYR_INSTALL_DIR"
printf %s "${INSTALLED-the release's executable}" > "$FSBPYR_INSTALL_DIR/fsbpyr"
"""


def run(tmp_path: Path, *, signed: int = 1, token: str = "a-token",  # noqa: S107
        change: str = "") -> subprocess.CompletedProcess[str]:  # fmt: skip
    """``change``: set in the stand-in itself, since the step's `env -i` keeps variables out."""
    work = tmp_path / "work"
    (work / "assets").mkdir(parents=True)
    (work / "assets" / "install.sh").write_text(f"#!/bin/sh\n{change}\n{INSTALLER}")
    (work / "assets" / "fsbpyr-3.14-x86_64-linux-gnu").write_bytes(ASSET)
    fake = tmp_path / "bin"
    fake.mkdir()
    gh = fake / "gh"
    gh.write_text(f"#!/bin/sh\nprintf %s '{token}'\n")
    gh.chmod(0o755)
    script = "\n".join([HELPERS, f'work="{work}" signed={signed} TAG=v1.2.0 DEFAULT=3.14',
                        "HOST=x86_64-linux-gnu failed=0 skipped=0",
                        STEP, 'echo "failed=$failed skipped=$skipped"'])  # fmt: skip
    bash = requires.tool("bash")
    return subprocess.run([bash, "-c", script], cwd=tmp_path, capture_output=True, text=True,
                          env={"PATH": f"{fake}:/usr/bin:/bin"}, check=False)  # fmt: skip


def test_a_login_installs_and_no_login_stops_and_says_so(tmp_path: Path) -> None:
    done = run(tmp_path)

    assert "failed=0 skipped=0" in done.stdout, done.stdout + done.stderr
    assert "ok    FSBPYR_VERIFY=1: the attestation passes" in done.stdout
    assert "ok    FSBPYR_VERIFY=1: with no gh login it stops and says so" in done.stdout


def test_a_release_from_before_signing_is_left_out(tmp_path: Path) -> None:
    done = run(tmp_path, signed=0)

    assert "failed=0 skipped=1" in done.stdout
    assert "skip  v1.2.0 is from before signing" in done.stdout


def test_this_machine_having_no_login_is_a_failure_and_no_pass(tmp_path: Path) -> None:
    done = run(tmp_path, token="")

    assert "FAIL  FSBPYR_VERIFY=1: this machine has no gh login" in done.stdout


@pytest.mark.parametrize(("change", "printed"), [
    ("ATTESTATION_FAILS=1", "FAIL  FSBPYR_VERIFY=1: install.sh failed"),
    ("INSTALLED=another", "FAIL  FSBPYR_VERIFY=1: the installed file is not"),
    ("NO_LOGIN_INSTALLS=1", "FAIL  FSBPYR_VERIFY=1: it installed with no gh login"),
    ("NO_LOGIN_READS_AS_TAMPERING=1", "FAIL  FSBPYR_VERIFY=1: with no gh login: error: the attes"),
], ids=["attestation refused", "another file", "no login installs", "a false alarm"])
def test_what_goes_wrong_is_a_failure(tmp_path: Path, change: str, printed: str) -> None:
    done = run(tmp_path, change=change)

    assert printed in done.stdout, done.stdout + done.stderr
    assert "failed=0" not in done.stdout
