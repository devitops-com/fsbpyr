"""`fsbpyr self update` of a built fsbpyr with its real checks (docs/signing.md §5.5): release
v1.2.0 of a tagged copy of this checkout, which pins test keys, updating to v1.3.0 from a fake
GitHub on localhost, whose sums files the release script signs as the sign job does. Without this,
release N's checks would first run on real files in N+1's `make release-check`, when both
releases are immutable. Its limit: N updates to a build of the same code here; that N reads
N+1's real files is first checked there."""

import hashlib
import lzma
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from fsbpyr import dispatch, selfupdate
from fsbpyr.build import TARGET_PYTHON
from fsbpyr.runtime.registry import Runtime
from fsbpyr.targets import host_target
from fsbpyr.vcs import release_asset, release_stub_asset, release_stub_sums

from ..support import contain
from ..support.releases import SUMS, FakeGitHub, FakeReleases, sums_of
from ..support.signing import signed_as_release
from .conftest import ReleaseKeys, built_release

pytestmark = pytest.mark.slow

ASSET = release_asset(TARGET_PYTHON, host_target().name)
PACKED = f"{ASSET}.xz"
STUB = release_stub_asset(TARGET_PYTHON, host_target().name, "2.28")
STUB_SUMS = release_stub_sums("2.28")
TIMEOUT = 120


@pytest.fixture(scope="module")
def old(
    tmp_path_factory: pytest.TempPathFactory, runtime: Runtime, release_keys: ReleaseKeys
) -> Path:
    return built_release(tmp_path_factory.mktemp("old"), "v1.2.0", release_keys.k.pub,
                         release_keys.r.pub)  # fmt: skip


@pytest.fixture(scope="module")
def new(
    tmp_path_factory: pytest.TempPathFactory, runtime: Runtime, release_keys: ReleaseKeys
) -> Path:
    return built_release(tmp_path_factory.mktemp("new"), "v1.3.0", release_keys.k.pub,
                         release_keys.r.pub)  # fmt: skip


def _packed(path: Path) -> bytes:
    """At preset 0: 1.1 s for a build of 29.5 MB, against 4.8 s at the default."""
    return lzma.compress(path.read_bytes(), preset=0)


@pytest.fixture(scope="module")
def packed_new(new: Path) -> bytes:
    return _packed(new)


@pytest.fixture(scope="module")
def packed_old(old: Path) -> bytes:
    return _packed(old)


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    fake = FakeGitHub()
    fake.tag = "v1.3.0"
    yield fake
    fake.close()


@pytest.fixture
def installed(old: Path, tmp_path: Path) -> Path:
    path = tmp_path / "prefix" / "bin" / "fsbpyr"
    path.parent.mkdir(parents=True)
    shutil.copy2(old, path)
    return path


def publish(github: FakeGitHub, packed: bytes, key: Path, directory: Path) -> None:
    """The release as CI publishes it: the asset, the sums and K's signature of them."""
    github.assets[PACKED] = packed
    sums = sums_of({PACKED: packed})
    github.assets[SUMS] = sums
    github.assets[f"{SUMS}.sig"] = signed_as_release(key, SUMS, sums, directory / "sums")


def update(installed: Path, github: FakeGitHub, tmp_path: Path,
           downloads: str | None = None) -> subprocess.CompletedProcess[str]:  # fmt: skip
    env = {**os.environ, selfupdate.API_VARIABLE: f"{github.base}/api", "FSBPYR_GC": "0",
           "FSBPYR_CACHE_DIR": str(tmp_path / "cache"),
           dispatch.DOWNLOADS_VARIABLE: downloads or "http://127.0.0.1:9"}  # fmt: skip
    return contain.run([installed, "self", "update"], env=env, timeout=TIMEOUT)


def version(executable: Path) -> str:
    done = contain.run([executable, "--version"], env=dict(os.environ), timeout=TIMEOUT)
    return done.stdout.strip().partition("-")[0]


def assert_unchanged(installed: Path, old: Path) -> None:
    assert installed.read_bytes() == old.read_bytes()
    assert sorted(p.name for p in installed.parent.iterdir()) == ["fsbpyr"]


def test_a_signed_release_updates_it(
    installed: Path, github: FakeGitHub, packed_new: bytes, new: Path, tmp_path: Path,
    release_keys: ReleaseKeys
) -> None:
    publish(github, packed_new, release_keys.k.key, tmp_path)

    done = update(installed, github, tmp_path)

    assert done.returncode == 0, done.stderr
    assert installed.read_bytes() == new.read_bytes()
    assert version(installed) == "1.3.0"


def test_the_recovery_key_signs_a_release_too(
    installed: Path, github: FakeGitHub, packed_new: bytes, tmp_path: Path,
    release_keys: ReleaseKeys
) -> None:
    publish(github, packed_new, release_keys.r.key, tmp_path)

    done = update(installed, github, tmp_path)

    assert done.returncode == 0, done.stderr
    assert version(installed) == "1.3.0"


def test_a_release_without_a_signature_changes_nothing(
    installed: Path, old: Path, github: FakeGitHub, packed_new: bytes, tmp_path: Path,
    release_keys: ReleaseKeys
) -> None:
    publish(github, packed_new, release_keys.k.key, tmp_path)
    del github.assets[f"{SUMS}.sig"]

    done = update(installed, github, tmp_path)

    assert done.returncode == 1
    assert "release v1.3.0 is not signed; nothing was changed" in done.stderr
    assert f"/files/{PACKED}" not in github.requests
    assert_unchanged(installed, old)


def test_a_signature_by_a_key_it_does_not_pin_changes_nothing(
    installed: Path, old: Path, github: FakeGitHub, packed_new: bytes, tmp_path: Path,
    release_keys: ReleaseKeys
) -> None:
    publish(github, packed_new, release_keys.stray.key, tmp_path)

    done = update(installed, github, tmp_path)

    assert done.returncode == 1
    assert "release v1.3.0 is not signed by a key this fsbpyr pins" in done.stderr
    assert f"/files/{PACKED}" not in github.requests
    assert_unchanged(installed, old)


def test_an_xz_that_does_not_match_its_line_changes_nothing(
    installed: Path, old: Path, github: FakeGitHub, packed_new: bytes, packed_old: bytes,
    tmp_path: Path, release_keys: ReleaseKeys
) -> None:
    publish(github, packed_new, release_keys.k.key, tmp_path)
    github.assets[PACKED] = packed_old  # a mirror's: real, signed in its day, not this release's

    done = update(installed, github, tmp_path)

    assert done.returncode == 1 and "does not match the checksum" in done.stderr
    assert_unchanged(installed, old)


def test_a_signed_older_build_served_as_a_new_release_changes_nothing(
    installed: Path, old: Path, github: FakeGitHub, packed_old: bytes, tmp_path: Path,
    release_keys: ReleaseKeys
) -> None:
    """v1.2.0's files with a genuine signature, under the tag v1.3.0: a statement holds no
    version, so the build info decides."""
    publish(github, packed_old, release_keys.k.key, tmp_path)

    done = update(installed, github, tmp_path)

    assert done.returncode == 1 and "not newer than this fsbpyr" in done.stderr
    assert_unchanged(installed, old)


def test_three_pinned_keys_take_a_release_of_the_new_key(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, github: FakeGitHub, packed_new: bytes,
    release_keys: ReleaseKeys
) -> None:
    """The release that changes K pins old K, new K and R (§5.4), and the next is signed by the
    new K. What that release accepts is what its users can update to."""
    transition = built_release(tmp_path_factory.mktemp("transition"), "v1.2.0", release_keys.k.pub,
                               release_keys.new_k.pub, release_keys.r.pub)  # fmt: skip
    publish(github, packed_new, release_keys.new_k.key, tmp_path)

    done = update(transition, github, tmp_path)

    assert done.returncode == 0, done.stderr
    assert version(transition) == "1.3.0"


def test_a_client_that_skipped_the_key_change_refuses_and_says_why(
    installed: Path, old: Path, github: FakeGitHub, packed_new: bytes, tmp_path: Path,
    release_keys: ReleaseKeys
) -> None:
    publish(github, packed_new, release_keys.new_k.key, tmp_path)

    done = update(installed, github, tmp_path)

    assert done.returncode == 1
    assert "either the release keys changed in a release this fsbpyr did not install" in done.stderr
    assert "#release-keys" in done.stderr
    assert_unchanged(installed, old)


def test_a_placed_stub_whose_new_sums_fail_their_check_only_warns(
    installed: Path, github: FakeGitHub, packed_new: bytes, tmp_path: Path,
    release_keys: ReleaseKeys
) -> None:
    """During an update the old fsbpyr reads the new tag's EL8 sums, against its own pins."""
    publish(github, packed_new, release_keys.k.key, tmp_path)
    stub = installed.parent / STUB
    stub.write_bytes(b"a stub placed by the user")
    companion = FakeReleases()
    try:
        sums = f"{hashlib.sha256(stub.read_bytes()).hexdigest()}  {STUB}\n".encode()
        companion.files[f"/el8-v1.3.0/{STUB_SUMS}"] = sums
        companion.files[f"/el8-v1.3.0/{STUB_SUMS}.sig"] = signed_as_release(
            release_keys.stray.key, STUB_SUMS, sums, tmp_path / "stub-sums")
        done = update(installed, github, tmp_path, companion.base)
    finally:
        companion.close()

    assert done.returncode == 0, done.stderr
    assert version(installed) == "1.3.0"
    assert "could not be checked against the stubs of v1.3.0" in done.stderr
    assert stub.read_bytes() == b"a stub placed by the user"
