"""The stub of a released fsbpyr for the EL8 baseline (runtime/stubs.py, D46): where it comes
from (a file placed beside the executable, the cache, a download from the companion release of
the tag served by a fake server on localhost), that it is proven to be this fsbpyr's own, and
what is said when there is none."""

import hashlib
import lzma
from collections.abc import Iterator
from pathlib import Path

import pytest

from fsbpyr import dispatch
from fsbpyr.runtime import collect, pins, registry, stubs
from fsbpyr.runtime.stub import IDENTITY_MARKER, stub_identity
from fsbpyr.targets import host_target
from fsbpyr.vcs import release_stub_asset, release_stub_sums, release_stub_tag

from ...support.releases import FakeReleases
from .test_registry import _runtime

BASELINE = "2.28"
TAG = "v1.2.0"
COMPANION = release_stub_tag(BASELINE, TAG)
PYTHON = "3.14"
ASSET = release_stub_asset(PYTHON, host_target().name, BASELINE)
SUMS = release_stub_sums(BASELINE)
PIN = pins.pin_for(PYTHON, registry.host_triple())
EXPECTED = registry.runtime_id(PIN, baseline=BASELINE)
CACHED = f"{ASSET}-{EXPECTED}"  # a cached stub is named after the runtime id it carries


def make_stub(identity: str | None = EXPECTED, filler: bytes = b"") -> bytes:
    tail = b"" if identity is None else IDENTITY_MARKER + identity.encode() + b"\0"
    return b"\x7fELF-stub" + filler + tail + b"rest"


def sums_line(data: bytes, name: str = ASSET) -> bytes:
    return f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()


@pytest.fixture
def releases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeReleases]:
    fake = FakeReleases()
    monkeypatch.setenv(dispatch.DOWNLOADS_VARIABLE, fake.base)
    monkeypatch.setattr(stubs, "own_version", lambda: "1.2.0-abcdef12")
    monkeypatch.setattr(stubs, "own_paths", lambda: [tmp_path / "bin" / "fsbpyr"])
    (tmp_path / "bin").mkdir()
    yield fake
    fake.close()


def publish(releases: FakeReleases, data: bytes, *, sums: bytes | None = None) -> None:
    releases.files[f"/{COMPANION}/{ASSET}.xz"] = lzma.compress(data)
    releases.files[f"/{COMPANION}/{SUMS}"] = sums if sums is not None else sums_line(data)


def get(tmp_path: Path) -> bytes:
    return stubs.stub_for(PYTHON, BASELINE, TAG, tmp_path / "cache")


def cached(tmp_path: Path) -> list[str]:
    directory = tmp_path / "cache" / "downloads"
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


# --- the identity -----------------------------------------------------------------------------


def test_a_stubs_identity_is_read_from_its_bytes() -> None:
    assert stub_identity(make_stub("cp314-x86_64-unknown-linux-gnu-0123456789ab")) == (
        "cp314-x86_64-unknown-linux-gnu-0123456789ab")
    assert stub_identity(make_stub(None)) is None


def test_the_expected_identity_is_the_runtime_id_of_the_baseline() -> None:
    assert registry.runtime_id(PIN) != EXPECTED
    assert EXPECTED.startswith(f"cp314-{PIN.triple}-")


# --- where the stub comes from ----------------------------------------------------------------


def test_a_placed_file_wins_over_the_cache_and_the_release(
    releases: FakeReleases, tmp_path: Path
) -> None:
    placed = make_stub(filler=b"placed")
    (tmp_path / "bin" / ASSET).write_bytes(placed)
    (tmp_path / "cache" / "downloads").mkdir(parents=True)
    (tmp_path / "cache" / "downloads" / CACHED).write_bytes(make_stub(filler=b"cached"))
    publish(releases, make_stub(filler=b"released"))

    assert get(tmp_path) == placed
    assert releases.requests == []


def test_a_placed_xz_file_is_unpacked(releases: FakeReleases, tmp_path: Path) -> None:
    data = make_stub(filler=b"x" * 1000)
    (tmp_path / "bin" / f"{ASSET}.xz").write_bytes(lzma.compress(data))

    assert get(tmp_path) == data
    assert releases.requests == [] and cached(tmp_path) == []


def test_a_placed_file_beside_the_real_executable_is_found_too(
    releases: FakeReleases, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "opt"
    real.mkdir()
    monkeypatch.setattr(stubs, "own_paths",
                        lambda: [tmp_path / "bin" / "fsbpyr", real / "fsbpyr-3.14"])
    (real / ASSET).write_bytes(make_stub())

    assert get(tmp_path) == make_stub()


def test_a_damaged_placed_xz_file_is_an_error(releases: FakeReleases, tmp_path: Path) -> None:
    (tmp_path / "bin" / f"{ASSET}.xz").write_bytes(b"not xz at all")

    with pytest.raises(stubs.StubError, match=f"cannot read .*{ASSET}.xz"):
        get(tmp_path)


def test_the_cache_wins_over_the_release(releases: FakeReleases, tmp_path: Path) -> None:
    kept = make_stub(filler=b"cached")
    (tmp_path / "cache" / "downloads").mkdir(parents=True)
    (tmp_path / "cache" / "downloads" / CACHED).write_bytes(kept)
    publish(releases, make_stub(filler=b"released"))

    assert get(tmp_path) == kept
    assert releases.requests == []


def test_a_download_is_verified_and_kept_for_offline_use(
    releases: FakeReleases, tmp_path: Path
) -> None:
    data = make_stub(filler=b"d" * 5000)
    publish(releases, data)

    assert get(tmp_path) == data

    assert releases.requests == [f"/{COMPANION}/{SUMS}", f"/{COMPANION}/{ASSET}.xz"]
    assert cached(tmp_path) == [CACHED]  # whole, no temporary file left
    assert (tmp_path / "cache" / "downloads" / CACHED).read_bytes() == data
    releases.close()  # no network from here on
    assert get(tmp_path) == data


def test_the_download_url_follows_the_release_downloads_variable(
    releases: FakeReleases, tmp_path: Path
) -> None:
    publish(releases, make_stub())

    get(tmp_path)

    assert all(request.startswith(f"/{COMPANION}/") for request in releases.requests)


def test_the_stub_is_fetched_from_the_companion_release_and_not_from_the_release(
    releases: FakeReleases, tmp_path: Path
) -> None:
    data = make_stub()
    releases.files[f"/{TAG}/{ASSET}.xz"] = lzma.compress(data)  # a release takes no stubs
    releases.files[f"/{TAG}/{SUMS}"] = sums_line(data)

    with pytest.raises(stubs.StubError):
        get(tmp_path)

    assert COMPANION == "el8-v1.2.0"
    assert releases.requests == [f"/{COMPANION}/{SUMS}"]  # nothing under /v1.2.0/ is asked for


def test_sums_may_use_the_binary_marker_and_list_other_stubs(
    releases: FakeReleases, tmp_path: Path
) -> None:
    data = make_stub()
    digest = hashlib.sha256(data).hexdigest()
    other = "f" * 64
    sums = f"{other}  fsbpyr-stub-3.12-x86_64-linux-gnu-glibc2.28\n{digest} *{ASSET}\n"
    publish(releases, data, sums=sums.encode())

    assert get(tmp_path) == data


def test_a_download_that_does_not_match_the_published_sha256_is_refused(
    releases: FakeReleases, tmp_path: Path
) -> None:
    publish(releases, make_stub(), sums=sums_line(b"something else"))

    with pytest.raises(stubs.StubError, match="does not match the SHA-256 the release publishes"):
        get(tmp_path)

    assert cached(tmp_path) == []


# --- the identity is checked, whatever the source ---------------------------------------------


@pytest.mark.parametrize("identity", ["cp314-x86_64-unknown-linux-gnu-aaaaaaaaaaaa", None])
def test_a_placed_stub_of_another_release_is_refused_naming_the_expected_one(
    releases: FakeReleases, tmp_path: Path, identity: str | None
) -> None:
    (tmp_path / "bin" / ASSET).write_bytes(make_stub(identity))

    with pytest.raises(stubs.StubError) as error:
        get(tmp_path)

    message = str(error.value)
    assert f"fsbpyr ({TAG})" in message and f"expects {EXPECTED}" in message
    assert f"use the {ASSET} of release {COMPANION}" in message
    assert releases.requests == []  # a refused file is not replaced by a download


def test_a_downloaded_stub_of_another_python_is_refused_and_not_kept(
    releases: FakeReleases, tmp_path: Path
) -> None:
    other = registry.runtime_id(pins.pin_for("3.13", registry.host_triple()), baseline=BASELINE)
    publish(releases, make_stub(other))  # published, with a SHA-256 that matches

    with pytest.raises(stubs.StubError, match=f"stub of runtime {other}"):
        get(tmp_path)

    assert cached(tmp_path) == []


def test_a_cached_stub_is_reused_by_the_next_release_when_its_runtime_id_is_unchanged(
    releases: FakeReleases, tmp_path: Path
) -> None:
    publish(releases, make_stub(filler=b"d" * 100))
    first = get(tmp_path)
    releases.close()  # no network from here on

    assert stubs.stub_for(PYTHON, BASELINE, "v1.3.0", tmp_path / "cache") == first
    assert cached(tmp_path) == [CACHED]


def test_a_cached_stub_that_is_not_ours_is_fetched_again(
    releases: FakeReleases, tmp_path: Path
) -> None:
    (tmp_path / "cache" / "downloads").mkdir(parents=True)
    (tmp_path / "cache" / "downloads" / CACHED).write_bytes(make_stub(None))
    data = make_stub(filler=b"fresh")
    publish(releases, data)

    assert get(tmp_path) == data
    assert (tmp_path / "cache" / "downloads" / CACHED).read_bytes() == data


def test_an_unterminated_identity_is_no_identity() -> None:
    assert stub_identity(b"\x7fELF" + IDENTITY_MARKER + b"cp314-x") is None


def test_a_placed_file_beyond_the_size_limit_is_refused(
    releases: FakeReleases, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stubs, "_MAX_STUB", 100)
    (tmp_path / "bin" / ASSET).write_bytes(make_stub(filler=b"x" * 500))

    with pytest.raises(stubs.StubError, match="larger than"):
        get(tmp_path)


def test_the_default_baselines_stub_is_not_the_el8_one() -> None:
    default = registry.runtime_id(PIN)

    assert stub_identity(make_stub(default)) != EXPECTED


# --- a release without the stubs --------------------------------------------------------------


def _assert_the_plain_message(error: pytest.ExceptionInfo[stubs.StubError], tmp_path: Path) -> None:
    message = str(error.value)
    assert f"release {COMPANION} (the glibc {BASELINE} stubs of {TAG}) does not exist" in message
    assert "published separately and not for every release" in message
    assert f"place {ASSET} (or {ASSET}.xz) next to {tmp_path / 'bin' / 'fsbpyr'}" in message
    assert f"or use a release that has them: {stubs.RELEASES}" in message
    assert "Traceback" not in message


def test_a_release_without_the_sums_file_has_no_el8_stubs(
    releases: FakeReleases, tmp_path: Path
) -> None:
    with pytest.raises(stubs.StubError) as error:  # 404 on the sums
        get(tmp_path)

    _assert_the_plain_message(error, tmp_path)
    assert cached(tmp_path) == []


def test_a_release_whose_sums_lack_this_python_has_no_stub_for_it(
    releases: FakeReleases, tmp_path: Path
) -> None:
    other = "fsbpyr-stub-3.12-x86_64-linux-gnu"
    releases.files[f"/{COMPANION}/{SUMS}"] = sums_line(make_stub(), other)

    with pytest.raises(stubs.StubError) as error:
        get(tmp_path)

    _assert_the_plain_message(error, tmp_path)


def test_a_release_whose_asset_is_missing_has_no_el8_stub(
    releases: FakeReleases, tmp_path: Path
) -> None:
    releases.files[f"/{COMPANION}/{SUMS}"] = sums_line(make_stub())  # listed, not uploaded

    with pytest.raises(stubs.StubError) as error:
        get(tmp_path)

    _assert_the_plain_message(error, tmp_path)


def test_a_server_that_cannot_be_reached_is_not_called_a_release_without_stubs(
    releases: FakeReleases, tmp_path: Path
) -> None:
    releases.close()

    with pytest.raises(stubs.StubError) as error:
        get(tmp_path)

    message = str(error.value)
    assert f"release {COMPANION}'s {ASSET} could not be fetched" in message
    assert "no glibc" not in message and "does not exist" not in message
    assert f"place {ASSET} next to {tmp_path / 'bin' / 'fsbpyr'}" in message


# --- the executable's own paths ---------------------------------------------------------------


def test_own_paths_are_the_path_it_was_run_by_then_the_real_one(tmp_path: Path) -> None:
    ran = tmp_path / "link" / "fsbpyr"

    assert stubs.own_paths(str(ran), "/opt/fsbpyr-3.14") == [ran, Path("/opt/fsbpyr-3.14")]
    assert stubs.own_paths(str(ran), str(ran)) == [ran]


def test_own_paths_look_a_bare_name_up_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "bin" / "fsbpyr"
    tool.parent.mkdir()
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tool.parent))

    assert stubs.own_paths("fsbpyr", "/opt/real")[0] == tool


# --- pairing with the executable's own runtime ------------------------------------------------


def test_a_release_pairs_its_own_stdlib_with_the_stub_of_the_baseline(
    releases: FakeReleases, tmp_path: Path
) -> None:
    own = _runtime(python_version="3.14.9", stub=b"default stub")
    (tmp_path / "bin" / ASSET).write_bytes(make_stub())

    runtime = stubs.paired(own, BASELINE, tmp_path / "cache")

    assert runtime is not None
    assert runtime.stub == make_stub() and runtime.baseline == BASELINE
    assert runtime.runtime_id == EXPECTED
    assert runtime.modules == own.modules and runtime.files == own.files
    assert runtime.tag == own.tag and runtime.bytecode_magic == own.bytecode_magic
    sysroot = pins.sysroot_for(PIN.triple, BASELINE)
    assert runtime.inputs == registry.runtime_inputs(PIN, BASELINE)
    assert runtime.inputs["sysroot"] == sysroot.identity


def test_a_developer_build_has_no_release_to_pair_with(
    releases: FakeReleases, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stubs, "own_version", lambda: "untagged-abcdef12")
    (tmp_path / "bin" / ASSET).write_bytes(make_stub())

    assert stubs.paired(_runtime(), BASELINE, tmp_path / "cache") is None
    assert releases.requests == []


def test_select_runtime_pairs_an_executable_with_the_stub_of_another_baseline(
    releases: FakeReleases, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    own = _runtime(python_version="3.14.9")
    monkeypatch.setattr(registry, "running_executable", lambda: Path("/proc/self/exe"))
    monkeypatch.setattr(registry.Runtime, "from_executable", classmethod(lambda cls, path: own))
    publish(releases, make_stub())

    runtime = registry.select_runtime("3.14", tmp_path / "cache", BASELINE)

    assert runtime.baseline == BASELINE and runtime.stub == make_stub()
    assert registry.select_runtime("3.14", tmp_path / "cache") is own  # the default: its own


def test_select_runtime_of_a_developer_executable_asks_the_cache(
    releases: FakeReleases, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stubs, "own_version", lambda: "untagged-abcdef12")
    monkeypatch.setattr(registry, "running_executable", lambda: Path("/proc/self/exe"))
    monkeypatch.setattr(registry.Runtime, "from_executable",
                        classmethod(lambda cls, path: _runtime(python_version="3.14.9")))

    with pytest.raises(registry.RuntimeCacheError,
                       match=r"fsbpyr runtime build --python 3\.14 --glibc 2\.28`$"):
        registry.select_runtime("3.14", tmp_path / "cache", BASELINE)

    assert releases.requests == []


def test_a_cached_stubs_name_is_not_one_the_cache_collector_removes() -> None:
    assert not collect.NAMES[collect.DOWNLOADS].fullmatch(CACHED)


def test_only_the_default_runtime_error_points_at_the_release_binary(tmp_path: Path) -> None:
    with pytest.raises(registry.RuntimeCacheError, match="the release binary"):
        registry.find_runtime("3.14", cache=tmp_path)
    with pytest.raises(registry.RuntimeCacheError) as error:
        registry.find_runtime("3.14", cache=tmp_path, baseline=BASELINE)

    assert "release binary" not in str(error.value)  # a release's glibc 2.28 stub is another asset
