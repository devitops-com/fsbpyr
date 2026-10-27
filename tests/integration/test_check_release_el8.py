"""The EL8 steps of tools/release/check-release.sh ("EL8 stubs" and "el8 build"), run for real
against a fake `gh` and `git`. The rest of the script needs a release to download and a clone of
its tag, so only these two blocks are cut out of it, with the script's own ok/fail/skip and
verify helpers."""

import hashlib
import lzma
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fsbpyr.vcs import release_stub_asset, release_stub_sums

ROOT = Path(__file__).parents[2]
SCRIPT = (ROOT / "tools" / "release" / "check-release.sh").read_text(encoding="utf-8")
SUMS = release_stub_sums("2.28")
ASSET = release_stub_asset("3.14", "x86_64-linux-gnu", "2.28")
STUB = b"the stub"
COMMIT = "c" * 40

HELPERS = "\n".join(re.findall(r"^(?:ok|fail|skip|step|verified)\(\).*$", SCRIPT, re.MULTILINE))
VERIFY = re.findall(r"^verifying=0.*?(?=^# Whether the nth)", SCRIPT, re.MULTILINE | re.DOTALL)[0]
CHECK = 'step "EL8 stubs' + SCRIPT.split('step "EL8 stubs', 1)[1].split(
    "# --- This target's executables", 1)[0]  # fmt: skip
BUILD = 'step "el8 build' + SCRIPT.split('step "el8 build', 1)[1].split('step "installed"', 1)[0]

PREAMBLE = r"""
failed=0 skipped=0
REPO=devitops-com/fsbpyr
EL8_SIGNER="$REPO/.github/workflows/el8.yml"
EL8_SUMS=SHA256SUMS-glibc2.28
PYTHONS=(3.14) TARGETS=(x86_64-linux-gnu) DEFAULT=3.14 HOST=x86_64-linux-gnu
TAG="v$VERSION" COMPANION="el8-v$VERSION"
LATEST="${FAKE_LATEST:-$TAG}"
COMMIT=cccccccccccccccccccccccccccccccccccccccc
mkdir -p "$work/src" "$work/attest"
# gh: FAKE_FLAGS is "isDraft isPrerelease" of the companion (unset: no such release), FAKE_ASSETS
# its asset names, FAKE_DIR holds what `release download` fetches; FAKE_UNATTESTED names the one
# file whose attestation does not verify.
gh() {
    case "$1 $2" in
        "release view")
            [ -n "${FAKE_FLAGS-}" ] || { echo "release not found" >&2; return 1; }
            case "$*" in
                *isDraft*) echo "$FAKE_FLAGS" ;;
                *) tr ' ' '\n' <<<"$FAKE_ASSETS" ;;
            esac ;;
        "release download")
            while [ $# -gt 0 ]; do [ "$1" = --dir ] && cp "$FAKE_DIR"/* "$2"; shift; done ;;
        "api repos/$REPO/releases/tags/$COMPANION") echo "${FAKE_IMMUTABLE-true}" ;;
        "attestation verify") [ "${3##*/}" != "${FAKE_UNATTESTED-}" ] ;;
    esac
}
git() { case "$3" in fetch) return 0 ;; rev-parse) echo "${FAKE_TAG_COMMIT:-$COMMIT}" ;; esac; }
command() { if [ "$1 $2" = "-v podman" ]; then return 1; fi; builtin command "$@"; }
managed() { return 0; }
# a build of the app: records what stood beside the executable, and fetches the stub if none did
building() {
    ls "${1%/*}" > "$work/beside"
    [ -e "${1%/*}/$ASSET.xz" ] && return 0
    mkdir -p "$work/cache/downloads"
    printf %s "$STUB" > "$work/cache/downloads/$ASSET-$TAG"
}
mkdir -p "$work/assets" "$work/src/tests/fixtures/projects/hello"
touch "$work/assets/fsbpyr-$DEFAULT-$HOST"
"""
needs_xz = pytest.mark.skipif(shutil.which("xz") is None, reason="xz is not installed")


class Check:
    """One run of the two blocks, in a scratch directory with the companion release `files`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files = root / "release"
        self.files.mkdir()

    def release(self, *, flags: str = "false false", missing: str = "") -> None:
        digest = hashlib.sha256(STUB).hexdigest()
        (self.files / SUMS).write_text(f"{digest}  {ASSET}\n", encoding="utf-8")
        (self.files / f"{ASSET}.xz").write_bytes(lzma.compress(STUB))
        self.flags = flags
        self.missing = missing

    flags = ""
    missing = ""

    def run(self, version: str = "0.27.0", **env: str) -> subprocess.CompletedProcess[str]:
        assets = [name for name in (SUMS, f"{ASSET}.xz") if name != self.missing]
        script = "\n".join([HELPERS, f'VERSION={version} work="$PWD/work"', 'mkdir -p "$work"',
                            PREAMBLE, VERIFY, CHECK, BUILD,
                            'echo "failed=$failed skipped=$skipped"'])  # fmt: skip
        environment = {"PATH": "/usr/bin:/bin", "FAKE_FLAGS": self.flags, "STUB": STUB.decode(),
                       "FAKE_ASSETS": " ".join(assets), "FAKE_DIR": str(self.files),
                       "ASSET": ASSET, **env}  # fmt: skip
        bash = shutil.which("bash") or "bash"
        return subprocess.run([bash, "-c", script], cwd=self.root, env=environment,
                              capture_output=True, text=True, check=False)  # fmt: skip

    @property
    def beside(self) -> list[str]:
        return (self.root / "work" / "beside").read_text(encoding="utf-8").split()


@pytest.fixture
def check(tmp_path: Path) -> Check:
    return Check(tmp_path)


def test_a_version_without_a_companion_release_has_no_stubs_to_check(check: Check) -> None:
    result = check.run()

    assert "skip  no EL8 stubs: no companion release el8-v0.27.0" in result.stdout
    assert "failed=0 skipped=2" in result.stdout  # this and the build's


@pytest.mark.parametrize("flags", ["true false", "false true"])
def test_a_draft_or_a_prerelease_companion_is_no_published_release(
        check: Check, flags: str) -> None:  # fmt: skip
    check.release(flags=flags)

    result = check.run()

    expected = f"FAIL  el8-v0.27.0 is not a published release (draft, prerelease: {flags})"
    assert expected in result.stdout
    assert "failed=1" in result.stdout


def test_a_companion_that_is_the_latest_release_fails(check: Check) -> None:
    check.release()

    result = check.run(FAKE_LATEST="el8-v0.27.0")

    assert "FAIL  el8-v0.27.0 is the latest release" in result.stdout


def test_a_companion_tag_on_another_commit_fails(check: Check) -> None:
    check.release()

    result = check.run(FAKE_TAG_COMMIT="d" * 40)

    assert "FAIL  the tag el8-v0.27.0 is not on the commit of v0.27.0 (cccccccc)" in result.stdout


def test_a_missing_asset_is_one_failure_not_two(check: Check) -> None:
    check.release(missing=f"{ASSET}.xz")

    result = check.run()

    assert f"FAIL  el8-v0.27.0 misses: {ASSET}.xz" in result.stdout
    assert "cannot download" not in result.stdout  # nothing was tried
    assert result.stdout.count("FAIL") == 1


@needs_xz
def test_a_complete_companion_release_passes_and_its_stub_is_fetched_by_a_later_fsbpyr(
        check: Check) -> None:  # fmt: skip
    check.release()

    result = check.run()

    assert "FAIL" not in result.stdout, result.stdout
    assert f"{ASSET}.xz unpacks to the digest listed" in result.stdout
    assert f"the stub it fetched is el8-v0.27.0's {ASSET}" in result.stdout
    assert check.beside == ["fsbpyr-3.14-x86_64-linux-gnu"]  # nothing placed beside it


@needs_xz
@pytest.mark.parametrize("unattested", [f"{ASSET}.xz", SUMS])
def test_each_file_whose_attestation_does_not_verify_fails_on_its_own(
        check: Check, unattested: str) -> None:  # fmt: skip
    check.release()

    result = check.run(FAKE_UNATTESTED=unattested)

    assert f"FAIL  {unattested}: no provenance attestation by el8.yml" in result.stdout
    assert result.stdout.count("FAIL") == 1, result.stdout


@needs_xz
def test_the_stub_of_0_26_0_is_placed_beside_the_executable_which_cannot_fetch_it(
        check: Check) -> None:  # fmt: skip
    check.release()

    result = check.run("0.26.0")

    assert "FAIL" not in result.stdout, result.stdout
    assert f"{ASSET}.xz is placed beside the executable" in result.stdout
    assert sorted(check.beside) == sorted([f"{ASSET}.xz", "fsbpyr-3.14-x86_64-linux-gnu"])
    assert "the stub it fetched" not in result.stdout
