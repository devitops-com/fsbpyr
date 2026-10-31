"""fsbpyr self update, against a fake GitHub served from localhost: nothing reaches the net."""

import hashlib
import http.client
import json
import lzma
import os
import stat
import sys
import types
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from fsbpyr import dispatch, selfupdate
from fsbpyr.assemble import App, build_executable
from fsbpyr.cli import main
from fsbpyr.targets import target_named

from ..support.releases import FakeGitHub, FakeReleases
from ..support.signing import (
    RELEASE_PUB,
    SIGNATURE_SIZE,
    fake_pub,
    fake_signature,
    release_signature,
    statement,
    use_stand_in,
)
from .boot.conftest import FAKE_RUNTIME

OWN = "fsbpyr-3.14-x86_64-linux-gnu"  # the asset the 'running' executable was released as
RUNNING = f"{sys.version_info.major}.{sys.version_info.minor}"  # the Python this test runs on
REFUSED = ("fsbpyr: error: new v1.2.0 has not been installed because it has no valid release "
           "signature.\nfsbpyr: currently installed fsbpyr version (1.0.0) has been kept "
           "unmodified.\n")  # fmt: skip
CHECKED = ["/api", "/files/SHA256SUMS", "/files/SHA256SUMS.sig"]  # before any asset is fetched


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGitHub]:
    fake = FakeGitHub(release_signature)
    fake.assets[f"{OWN}.xz"] = b""
    use_stand_in(monkeypatch)
    monkeypatch.setenv(selfupdate.API_VARIABLE, f"{fake.base}/api")
    yield fake
    fake.close()


def _release_binary(
    tmp_path: Path, name: str = "new", *, asset: str = OWN, tag: str = "v1.2.0",
    glibc: str | None = None,
) -> bytes:
    """An executable as a release builds it: its asset and version are in its build info."""
    out = tmp_path / name
    info = (("release_asset", asset), ("git", {"commit": "abcdef1234567890", "tag": tag}),
            ("glibc", glibc))  # fmt: skip
    build_executable(FAKE_RUNTIME, App("fsbpyr", "fsbpyr.cli:main", dists=(), build_info=info), out)
    return out.read_bytes()


@pytest.fixture
def installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A 'running' executable at <prefix>/bin/fsbpyr, version 1.0.0, mode 0o750."""
    path = tmp_path / "prefix" / "bin" / "fsbpyr"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old")
    path.chmod(0o750)
    monkeypatch.setitem(sys.modules, "_fsbpyr", types.ModuleType("_fsbpyr"))
    monkeypatch.setattr(selfupdate, "running_executable", lambda: path)
    monkeypatch.setattr(selfupdate, "own_version", lambda: "1.0.0-abcdef12")
    monkeypatch.setattr(selfupdate, "own_asset", lambda: OWN)
    monkeypatch.setattr(selfupdate, "_render", lambda new, command: b"new " + b" ".join(
        c.encode() for c in command))  # fmt: skip
    return path


def test_updates_the_executable_in_place_keeping_its_mode(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = _release_binary(tmp_path)
    github.assets[f"{OWN}.xz"] = lzma.compress(binary)

    assert main(["self", "update"]) == 0

    assert installed.read_bytes() == binary
    assert stat.S_IMODE(installed.stat().st_mode) == 0o750
    assert sorted(p.name for p in installed.parent.iterdir()) == ["fsbpyr"]  # no temporary left
    assert "updated" in capsys.readouterr().out


def test_a_variant_updates_to_its_own_variant_of_the_newer_release(
    github: FakeGitHub, installed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(selfupdate, "own_asset", lambda: "fsbpyr-3.12-x86_64-linux-gnu")
    variant = _release_binary(tmp_path, "variant", asset="fsbpyr-3.12-x86_64-linux-gnu")
    other = b"another Python's"
    github.assets = {"fsbpyr-3.14-x86_64-linux-gnu.xz": lzma.compress(other),
                     "fsbpyr-3.13-x86_64-linux-gnu.xz": lzma.compress(other),
                     "fsbpyr-3.12-x86_64-linux-gnu.xz": lzma.compress(variant)}  # fmt: skip

    assert main(["self", "update"]) == 0

    assert installed.read_bytes() == variant
    assert github.requests == [*CHECKED, "/files/fsbpyr-3.12-x86_64-linux-gnu.xz"]


def test_the_newest_pythons_build_stays_on_its_python_when_a_newer_one_is_released(
    github: FakeGitHub, installed: Path, tmp_path: Path
) -> None:
    binary = _release_binary(tmp_path)
    github.assets = {"fsbpyr-3.15-x86_64-linux-gnu.xz": lzma.compress(b"3.15"),
                     f"{OWN}.xz": lzma.compress(binary)}  # fmt: skip

    assert main(["self", "update"]) == 0

    assert installed.read_bytes() == binary
    assert github.requests == [*CHECKED, f"/files/{OWN}.xz"]


def test_the_curl_hint_runs_the_installer_for_the_running_python() -> None:
    hint = selfupdate.curl_hint()

    assert "/releases/latest/download/install.sh | sh -s -- --python " in hint
    assert hint.endswith(f"--python {RUNNING}")


def test_a_release_without_the_variants_asset_is_an_error_not_another_variant(
    github: FakeGitHub, installed: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(selfupdate, "own_asset", lambda: "fsbpyr-3.12-x86_64-linux-gnu")
    github.assets = {"fsbpyr-3.14-x86_64-linux-gnu.xz": b"", "fsbpyr-3.13-x86_64-linux-gnu.xz": b""}

    assert main(["self", "update"]) == 1

    err = capsys.readouterr().err
    assert "the latest release (v1.2.0) has no fsbpyr-3.12-x86_64-linux-gnu.xz" in err
    assert "another with --python X.Y" in err
    assert err.rstrip().endswith("/releases/latest/download/install.sh | sh")  # the newest
    assert installed.read_bytes() == b"old"


@pytest.mark.parametrize(("recorded", "asset"), [
    ({"release_asset": "fsbpyr-3.12-aarch64-macos"}, "fsbpyr-3.12-aarch64-macos"),
    ({"release_asset": "fsbpyr-3.13-x86_64-linux-gnu"}, "fsbpyr-3.13-x86_64-linux-gnu"),
    # anything else: the asset of the Python it runs on, for this host
    ({"release_asset": "fsbpyr-x86_64-linux-gnu"}, None),  # 0.21.0's unlabeled name
    ({"release_asset": "fsbpyr-3.13"}, None),  # 0.21.0's short names
    ({"release_asset": "fsbpyr"}, None),
    ({"release_asset": "fsbpyr-3.12-sparc-linux-gnu"}, None),  # not a target
    ({}, None),
    ({"release_asset": "../evil"}, None),
    ({"release_asset": 3}, None),
])  # fmt: skip
def test_own_asset_is_what_the_build_recorded(
    recorded: dict[str, object], asset: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = types.ModuleType("_fsbpyr")
    fake.info = lambda: {"build_info": json.dumps(recorded)}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_fsbpyr", fake)
    monkeypatch.setattr(selfupdate, "host_target", lambda: target_named("aarch64-linux-musl"))

    assert selfupdate.own_asset() == (asset or f"fsbpyr-{RUNNING}-aarch64-linux-musl")


def test_updates_through_a_symlink_target_the_real_file(
    github: FakeGitHub, installed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = tmp_path / "link"
    link.symlink_to(installed)
    monkeypatch.setattr(selfupdate, "running_executable", lambda: Path(os.path.realpath(link)))
    binary = _release_binary(tmp_path)
    github.assets[f"{OWN}.xz"] = lzma.compress(binary)

    assert main(["self", "update"]) == 0

    assert link.is_symlink()
    assert installed.read_bytes() == binary


def test_up_to_date_changes_nothing_and_downloads_nothing(
    github: FakeGitHub, installed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    github.tag = "v1.0.0"

    assert main(["self", "update"]) == 0

    assert installed.read_bytes() == b"old"
    assert github.requests == ["/api"]
    assert "up to date" in capsys.readouterr().out


def test_an_older_release_is_not_installed(github: FakeGitHub, installed: Path) -> None:
    github.tag = "v0.9.9"

    assert main(["self", "update"]) == 0
    assert installed.read_bytes() == b"old"


def test_a_developer_build_is_refused_with_the_installer_command(
    github: FakeGitHub, installed: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(selfupdate, "own_version", lambda: "untagged-abcdef12")

    assert main(["self", "update"]) == 1

    err = capsys.readouterr().err
    assert "developer build" in err
    assert f"/releases/latest/download/install.sh | sh -s -- --python {RUNNING}" in err
    assert github.requests == []


def test_a_checkout_is_refused(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "_fsbpyr", None)  # import fails
    monkeypatch.setattr(selfupdate, "own_version", lambda: "1.0.0-abcdef12")

    assert main(["self", "update"]) == 1
    assert "executable only" in capsys.readouterr().err
    assert github.requests == []


def test_an_unwritable_directory_is_an_error_saying_how_to_update(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Root ignores file modes: its 0o555 directory is still written to, and the update goes
    through."""
    binary = _release_binary(tmp_path)
    github.assets[f"{OWN}.xz"] = lzma.compress(binary)
    installed.parent.chmod(0o555)
    try:
        status = main(["self", "update"])
    finally:
        installed.parent.chmod(0o755)

    err = capsys.readouterr().err
    if os.geteuid() == 0:
        assert status == 0
        assert installed.read_bytes() == binary
        assert "cannot write to" not in err
        return
    assert status == 1
    assert "cannot write to" in err
    directory = installed.parent
    assert f"install.sh | FSBPYR_INSTALL_DIR={directory} sh -s -- --python {RUNNING}" in err
    assert github.requests == []


def test_a_damaged_download_is_never_installed(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = bytearray(_release_binary(tmp_path))
    binary[len(binary) // 2] ^= 0xFF
    github.assets[f"{OWN}.xz"] = lzma.compress(bytes(binary))

    assert main(["self", "update"]) == 1

    assert installed.read_bytes() == b"old"
    assert sorted(p.name for p in installed.parent.iterdir()) == ["fsbpyr"]
    assert "verification" in capsys.readouterr().err


def test_something_that_is_not_an_fsbpyr_executable_is_never_installed(
    github: FakeGitHub, installed: Path
) -> None:
    github.assets[f"{OWN}.xz"] = lzma.compress(b"#!/bin/sh\necho pwned\n")

    assert main(["self", "update"]) == 1
    assert installed.read_bytes() == b"old"


def test_a_truncated_xz_stream_is_an_error(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    packed = lzma.compress(_release_binary(tmp_path))
    github.assets[f"{OWN}.xz"] = packed[: len(packed) // 2]

    assert main(["self", "update"]) == 1
    assert installed.read_bytes() == b"old"
    assert sorted(p.name for p in installed.parent.iterdir()) == ["fsbpyr"]
    assert "xz stream" in capsys.readouterr().err


def test_a_missing_asset_is_an_error(
    github: FakeGitHub, installed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    github.assets = {"other": b""}

    assert main(["self", "update"]) == 1
    assert f"no {OWN}.xz" in capsys.readouterr().err


def test_a_failing_api_is_an_error(
    github: FakeGitHub, installed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    github.api_status = 500

    assert main(["self", "update"]) == 1
    assert "cannot read the latest release" in capsys.readouterr().err
    assert installed.read_bytes() == b"old"


def test_a_release_without_a_version_tag_is_an_error(
    github: FakeGitHub, installed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    github.tag = "nightly"

    assert main(["self", "update"]) == 1
    assert "vX.Y.Z" in capsys.readouterr().err


def test_man_page_and_completions_under_the_prefix_are_written_again(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prefix = installed.parent.parent
    man = prefix / "share/man/man1/fsbpyr.1"
    bash = prefix / "share/bash-completion/completions/fsbpyr"
    for path in (man, bash):
        path.parent.mkdir(parents=True)
        path.write_text("old")
    man.chmod(0o640)
    github.assets[f"{OWN}.xz"] = lzma.compress(_release_binary(tmp_path))

    assert main(["self", "update"]) == 0

    assert man.read_bytes() == b"new man"
    assert stat.S_IMODE(man.stat().st_mode) == 0o640
    assert bash.read_bytes() == b"new completion bash"
    assert not (prefix / "share/zsh").exists()  # absent ones are not created
    assert [p.name for p in man.parent.iterdir()] == ["fsbpyr.1"]  # no temporary left
    assert "left alone" in capsys.readouterr().err  # the hint for ones not found


STUB = "fsbpyr-stub-3.14-x86_64-linux-gnu-glibc2.28"
COMPANION = "el8-v1.2.0"


@pytest.fixture
def companion(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeReleases]:
    """The downloads server of the new release's companion, with no stubs published yet."""
    fake = FakeReleases(release_signature)
    monkeypatch.setenv(dispatch.DOWNLOADS_VARIABLE, fake.base)
    yield fake
    fake.close()


def _publish_stubs(companion: FakeReleases, *stubs: bytes) -> None:
    companion.files[f"/{COMPANION}/SHA256SUMS-glibc2.28"] = "".join(
        f"{hashlib.sha256(stub).hexdigest()}  {STUB}\n" for stub in stubs).encode()


def _update(github: FakeGitHub, tmp_path: Path) -> None:
    github.assets[f"{OWN}.xz"] = lzma.compress(_release_binary(tmp_path))
    assert main(["self", "update"]) == 0


def test_a_placed_el8_stub_the_new_release_lists_is_not_named(
    github: FakeGitHub, installed: Path, tmp_path: Path, companion: FakeReleases,
    capsys: pytest.CaptureFixture[str]
) -> None:
    stub = installed.parent / f"{STUB}.xz"
    stub.write_bytes(lzma.compress(b"same stub"))  # the sums list the stub, not the .xz
    _publish_stubs(companion, b"same stub")

    _update(github, tmp_path)

    assert "--glibc" not in capsys.readouterr().err
    assert companion.requests == [f"/{COMPANION}/SHA256SUMS-glibc2.28",
                                  f"/{COMPANION}/SHA256SUMS-glibc2.28.sig"]  # fmt: skip


def test_a_placed_el8_stub_the_new_release_does_not_list_is_named_in_a_warning(
    github: FakeGitHub, installed: Path, tmp_path: Path, companion: FakeReleases,
    capsys: pytest.CaptureFixture[str]
) -> None:
    old = installed.parent / STUB
    old.write_bytes(b"old stub")
    current = installed.parent / f"{STUB}.xz"
    current.write_bytes(lzma.compress(b"new stub"))
    _publish_stubs(companion, b"new stub")

    _update(github, tmp_path)

    err = capsys.readouterr().err
    assert str(old) in err and str(current) not in err
    assert "`--glibc` refuses them unless" in err and "v1.2.0" in err
    assert old.read_bytes() == b"old stub"  # the user's files are left alone


def test_placed_el8_stubs_are_looked_for_beside_the_path_fsbpyr_was_run_by_too(
    github: FakeGitHub, installed: Path, tmp_path: Path, companion: FakeReleases,
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    link = tmp_path / "links" / "fsbpyr"
    link.parent.mkdir()
    link.symlink_to(installed)
    monkeypatch.setattr(sys, "argv", [str(link), "self", "update"])
    beside_link = link.parent / STUB
    beside_link.write_bytes(b"old stub")
    _publish_stubs(companion, b"new stub")

    _update(github, tmp_path)

    assert str(beside_link) in capsys.readouterr().err


def test_without_a_companion_the_warning_is_conditional(
    github: FakeGitHub, installed: Path, tmp_path: Path, companion: FakeReleases,
    capsys: pytest.CaptureFixture[str]
) -> None:
    stub = installed.parent / STUB
    stub.write_bytes(b"some stub")  # the companion publishes nothing: 404

    _update(github, tmp_path)

    err = capsys.readouterr().err
    assert str(stub) in err
    assert "if the runtime id of v1.2.0 differs" in err and "names the stub to place" in err
    assert "refuses them unless" not in err


def test_a_sums_read_cut_short_is_an_unknown_not_a_traceback(
    github: FakeGitHub, installed: Path, tmp_path: Path, companion: FakeReleases,
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = installed.parent / STUB
    stub.write_bytes(b"some stub")

    real = urllib.request.urlopen

    def cut_short(request: urllib.request.Request, *args: Any, **kwargs: Any) -> Any:
        if request.full_url.endswith("SHA256SUMS-glibc2.28"):
            return _CutShort()
        return real(request, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", cut_short)

    _update(github, tmp_path)  # the update itself succeeded: exit status 0

    err = capsys.readouterr().err
    assert str(stub) in err and "if the runtime id of v1.2.0 differs" in err


def test_without_stubs_placed_the_companion_is_not_asked(
    github: FakeGitHub, installed: Path, tmp_path: Path, companion: FakeReleases,
    capsys: pytest.CaptureFixture[str]
) -> None:
    (installed.parent / "fsbpyr-stub-notes.txt").write_text("not a stub")

    _update(github, tmp_path)

    assert "--glibc" not in capsys.readouterr().err and companion.requests == []


def test_an_executable_outside_a_bin_directory_gets_the_hint_only(
    github: FakeGitHub, installed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    elsewhere = tmp_path / "opt" / "fsbpyr"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"old")
    monkeypatch.setattr(selfupdate, "running_executable", lambda: elsewhere)
    github.assets[f"{OWN}.xz"] = lzma.compress(_release_binary(tmp_path))

    assert main(["self", "update"]) == 0

    assert "fsbpyr man" in capsys.readouterr().err


def test_a_failing_new_executable_stops_before_anything_is_replaced(
    github: FakeGitHub, installed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    man = installed.parent.parent / "share/man/man1/fsbpyr.1"
    man.parent.mkdir(parents=True)
    man.write_text("old")

    def broken(new: Path, command: tuple[str, ...]) -> bytes:
        raise selfupdate.UpdateError("it crashed")

    monkeypatch.setattr(selfupdate, "_render", broken)
    github.assets[f"{OWN}.xz"] = lzma.compress(_release_binary(tmp_path))

    assert main(["self", "update"]) == 1

    assert installed.read_bytes() == b"old"
    assert man.read_text() == "old"
    assert sorted(p.name for p in installed.parent.iterdir()) == ["fsbpyr"]
    assert "it crashed" in capsys.readouterr().err


def test_render_runs_the_new_executable_with_the_command(tmp_path: Path) -> None:
    script = tmp_path / "fake"
    script.write_text("#!/bin/sh\necho \"$@\"\n")
    script.chmod(0o755)

    assert selfupdate._render(script, ("completion", "zsh")) == b"completion zsh\n"


def _host_glibc(monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    monkeypatch.setattr(os, "confstr", lambda name: f"glibc {version}")


def _binary_needing(tmp_path: Path, glibc: str) -> bytes:
    return _release_binary(tmp_path, "needs", glibc=glibc)


def test_an_asset_needing_a_newer_glibc_than_the_hosts_is_never_installed(
    github: FakeGitHub, installed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    _host_glibc(monkeypatch, "2.28")
    github.assets[f"{OWN}.xz"] = lzma.compress(_binary_needing(tmp_path, "2.34"))

    assert main(["self", "update"]) == 1

    err = capsys.readouterr().err
    assert "needs glibc 2.34" in err and "glibc 2.28" in err and "Keep the current" in err
    assert installed.read_bytes() == b"old"
    assert sorted(p.name for p in installed.parent.iterdir()) == ["fsbpyr"]


@pytest.mark.parametrize("host", ["2.34", "2.36", "2.100"])
def test_an_asset_needing_the_hosts_glibc_or_less_is_installed(
    github: FakeGitHub, installed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    host: str
) -> None:
    _host_glibc(monkeypatch, host)
    binary = _binary_needing(tmp_path, "2.34")
    github.assets[f"{OWN}.xz"] = lzma.compress(binary)

    assert main(["self", "update"]) == 0

    assert installed.read_bytes() == binary


@pytest.mark.parametrize(("baseline", "host", "refused"), [
    ("2.34", "glibc 2.33", True), ("2.34", "glibc 2.34", False), ("2.9", "glibc 2.10", False),
    ("2.10", "glibc 2.9", True), (None, "glibc 2.1", False), ("junk", "glibc 2.1", False),
    ("2.34", "musl", False), ("2.34", "", False),
])  # fmt: skip
def test_the_glibc_guard_compares_numerically(
    baseline: object, host: str, refused: bool
) -> None:
    if refused:
        with pytest.raises(selfupdate.UpdateError, match="needs glibc"):
            selfupdate.check_glibc(baseline, host)
    else:
        selfupdate.check_glibc(baseline, host)


def test_the_guard_is_skipped_where_the_host_has_no_glibc(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def musl(name: str) -> str:
        raise ValueError(name)

    monkeypatch.setattr(os, "confstr", musl)

    selfupdate.check_glibc("9.99")


class _CutShort:
    """A response whose body ends before its Content-Length."""

    def __enter__(self) -> "_CutShort":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def read(self, size: int = -1) -> bytes:
        raise http.client.IncompleteRead(b"partial")


@pytest.mark.parametrize(
    "fetch",
    [
        lambda tmp: selfupdate.latest_release(OWN),
        lambda tmp: selfupdate.fetch_checked("https://example.invalid/x.xz", "0" * 64, tmp / "x"),
        lambda tmp: selfupdate.download_bytes("https://example.invalid/sums", 100),
    ],
    ids=["latest-release", "fetch", "download-bytes"],
)
def test_a_body_cut_short_is_an_update_error(
    fetch: Callable[[Path], object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not http.client's IncompleteRead, which no command reports: a traceback."""
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: _CutShort())
    monkeypatch.setenv(selfupdate.API_VARIABLE, "https://example.invalid/api")

    with pytest.raises(selfupdate.UpdateError):
        fetch(tmp_path)


def test_unpacking_stops_at_its_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_stand_in(monkeypatch)
    server = FakeReleases()
    server.files["/big.xz"] = lzma.compress(b"\0" * 1_000_000)
    digest = hashlib.sha256(server.files["/big.xz"]).hexdigest()

    try:
        with pytest.raises(selfupdate.UpdateError, match="unpacks to more than 1000 bytes"):
            selfupdate.fetch_checked(f"{server.base}/big.xz", digest, tmp_path / "out", 1000)
    finally:
        server.close()


# --- a release is checked before any of it is used (docs/signing.md §5.5) ------------------------


def _refused(installed: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """``self update`` fails, and the executable and its directory are as they were."""
    assert main(["self", "update"]) == 1
    assert installed.read_bytes() == b"old"
    assert sorted(p.name for p in installed.parent.iterdir()) == ["fsbpyr"]
    return capsys.readouterr().err


def _no_lzma(monkeypatch: pytest.MonkeyPatch) -> None:
    def fails(*args: object, **kwargs: object) -> None:
        raise AssertionError("lzma saw a download that had not passed its check")

    monkeypatch.setattr(lzma, "LZMADecompressor", fails)


def test_a_release_without_a_signature_is_refused(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_lzma(monkeypatch)
    github.assets[f"{OWN}.xz"] = lzma.compress(_release_binary(tmp_path))
    github.assets["SHA256SUMS"] = b""
    github.signer = None

    err = _refused(installed, capsys)

    assert err == REFUSED
    assert f"/files/{OWN}.xz" not in github.requests


def test_a_release_without_a_sums_file_is_refused(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    github.assets[f"{OWN}.xz"] = lzma.compress(_release_binary(tmp_path))
    github.signer = None

    assert _refused(installed, capsys) == REFUSED


@pytest.mark.parametrize("signature", [
    fake_signature(fake_pub("not pinned"), statement("fsbpyr", b"anything")),
    bytes(SIGNATURE_SIZE), b"", bytes(SIGNATURE_SIZE - 1), bytes(SIGNATURE_SIZE + 1),
    bytes(SIGNATURE_SIZE + 2),
], ids=["another key", "zeros", "empty", "a byte short", "a byte more", "two bytes more"])
def test_a_signature_by_no_pinned_key_is_refused(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch, signature: bytes
) -> None:
    _no_lzma(monkeypatch)
    github.assets[f"{OWN}.xz"] = lzma.compress(_release_binary(tmp_path))
    github.signer = lambda sums: signature

    err = _refused(installed, capsys)

    assert f"/files/{OWN}.xz" not in github.requests
    assert err == REFUSED


def test_a_refusal_is_exactly_two_lines_with_no_link(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing else, on either stream: a hacked release comes with a hacked wiki, and an alarm in
    the tool helps nobody (owner, 2026-10-31). Incidents go through the usual channels."""
    github.assets[f"{OWN}.xz"] = lzma.compress(_release_binary(tmp_path))
    github.signer = lambda sums: release_signature(sums, fake_pub("the next release key"))

    assert main(["self", "update"]) == 1

    out, err = capsys.readouterr()
    assert out == "" and err == REFUSED
    assert "http" not in err and "wiki" not in err and "github" not in err.lower()


def test_sums_too_large_are_a_release_with_no_valid_signature(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch
) -> None:
    github.assets[f"{OWN}.xz"] = lzma.compress(_release_binary(tmp_path))
    monkeypatch.setattr(selfupdate, "_MAX_SUMS", 1)

    assert _refused(installed, capsys) == REFUSED


def test_a_signature_of_other_sums_is_refused(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = _release_binary(tmp_path)
    github.assets[f"{OWN}.xz"] = lzma.compress(binary)
    github.assets["SHA256SUMS.sig"] = release_signature(b"the sums of another release")

    assert _refused(installed, capsys) == REFUSED


def test_an_xz_that_does_not_match_its_line_never_reaches_lzma(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_lzma(monkeypatch)
    genuine = lzma.compress(_release_binary(tmp_path))
    github.assets["SHA256SUMS"] = f"{hashlib.sha256(genuine).hexdigest()}  {OWN}.xz\n".encode()
    github.assets[f"{OWN}.xz"] = genuine + b"\0"  # a mirror's

    assert _refused(installed, capsys) == REFUSED


def test_a_sums_file_that_does_not_list_the_asset_is_refused(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    github.assets[f"{OWN}.xz"] = lzma.compress(_release_binary(tmp_path))
    github.assets["SHA256SUMS"] = f"{'0' * 64}  another-file\n".encode()

    assert _refused(installed, capsys) == REFUSED


def test_another_file_of_the_signed_release_is_not_taken_for_the_asset(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The sums list every file of a release, an xz of another Python's fsbpyr among them, each
    under its own name: its line is good, and the build info tells them apart."""
    other = lzma.compress(_release_binary(tmp_path, asset="fsbpyr-3.13-x86_64-linux-gnu"))
    github.assets[f"{OWN}.xz"] = other
    github.assets["SHA256SUMS"] = f"{hashlib.sha256(other).hexdigest()}  {OWN}.xz\n".encode()

    assert f"not {OWN}" in _refused(installed, capsys)


@pytest.mark.parametrize("version", ["v1.0.0", "v0.9.9"])
def test_a_replayed_older_release_is_refused(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    version: str
) -> None:
    """An older release's files, with its genuine signature, under a newer tag: a statement holds
    no version, so the build info's decides."""
    github.tag = "v9.9.9"
    github.assets[f"{OWN}.xz"] = lzma.compress(_release_binary(tmp_path, tag=version))

    assert "not newer than this fsbpyr" in _refused(installed, capsys)


def test_a_download_too_large_is_cut_off(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_lzma(monkeypatch)
    monkeypatch.setattr(selfupdate, "_MAX_PACKED", 1000)
    github.assets[f"{OWN}.xz"] = b"x" * 5 * (1 << 20)

    assert "larger than 1000 bytes" in _refused(installed, capsys)


def test_a_release_build_info_that_is_not_a_string_is_refused() -> None:
    with pytest.raises(selfupdate.UpdateError, match="not asset"):
        selfupdate.check_release_identity({"release_asset": ["asset"]}, "asset")


def test_a_build_info_without_a_release_is_refused() -> None:
    with pytest.raises(selfupdate.UpdateError, match="records no release"):
        selfupdate.check_release_identity({"release_asset": "asset"}, "asset")


def test_three_pinned_keys_pass_a_release_of_a_key_change(
    github: FakeGitHub, installed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    new = fake_pub("the new release key")
    use_stand_in(monkeypatch, RELEASE_PUB, new, fake_pub("the recovery key"))
    binary = _release_binary(tmp_path)
    github.assets[f"{OWN}.xz"] = lzma.compress(binary)
    github.signer = lambda sums: release_signature(sums, new)

    assert main(["self", "update"]) == 0
    assert installed.read_bytes() == binary


def test_a_placed_stub_whose_new_sums_fail_their_check_only_warns(
    github: FakeGitHub, installed: Path, tmp_path: Path, companion: FakeReleases,
    capsys: pytest.CaptureFixture[str]
) -> None:
    stub = installed.parent / STUB
    stub.write_bytes(b"stub")
    _publish_stubs(companion, b"stub")
    companion.signer = lambda sums: release_signature(sums, fake_pub("not pinned"))

    _update(github, tmp_path)

    err = capsys.readouterr().err
    assert str(stub) in err and "could not be checked against the stubs" in err
    assert stub.read_bytes() == b"stub"


def test_a_build_of_another_version_than_the_tag_is_refused(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Newer than this fsbpyr and genuine, but not what the document says: the message would name
    a release that was not installed."""
    github.tag = "v9.9.9"
    github.assets[f"{OWN}.xz"] = lzma.compress(_release_binary(tmp_path, tag="v1.2.0"))

    assert "is not fsbpyr v9.9.9" in _refused(installed, capsys)


@pytest.mark.parametrize("url", ["file:///dev/stdin", "ftp://example.invalid/x", "data:,x",
                                 "no scheme", "http://[", 7, None])  # fmt: skip
def test_a_url_in_the_release_document_is_http_or_https(
    github: FakeGitHub, installed: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch, url: object
) -> None:
    """urllib takes file: and ftp: too, and its timeout does not hold there: /dev/stdin blocks."""
    original = github.answer

    def answer(path: str) -> tuple[int, bytes]:
        status, body = original(path)
        if path != "/api":
            return status, body
        document = json.loads(body)
        for asset in document["assets"]:
            asset["browser_download_url"] = url
        return status, json.dumps(document).encode()

    monkeypatch.setattr(github, "answer", answer)

    err = _refused(installed, capsys)

    assert "Traceback" not in err and "internal error" not in err


@pytest.mark.parametrize("body", [b"[" * 100000, b"{}", b"7", b"not json", b'{"tag_name": []}',
                                  b'{"tag_name": "v9.9.9", "assets": [7]}'], ids=repr)  # fmt: skip
def test_a_hostile_release_document_is_an_update_error(
    github: FakeGitHub, installed: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    monkeypatch.setattr(github, "answer", lambda path: (200, body))

    err = _refused(installed, capsys)

    assert "Traceback" not in err and "internal error" not in err


def test_a_release_document_too_large_is_refused(
    github: FakeGitHub, installed: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid one, so that only its size refuses it."""
    monkeypatch.setattr(selfupdate, "_MAX_DOCUMENT", 1000)
    body = json.dumps({"tag_name": "v9.9.9", "assets": [], "padding": "x" * 5000}).encode()
    monkeypatch.setattr(github, "answer", lambda path: (200, body))

    assert "larger than 1000 bytes" in _refused(installed, capsys)


@pytest.mark.parametrize("url", ["file:///dev/stdin", "ftp://example.invalid/x", "data:,x"])
def test_only_http_and_https_reach_urllib(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refused or not, what fails later says nothing of this: urllib must not open it at all."""
    opened: list[object] = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: opened.append(args))

    with pytest.raises(selfupdate.UpdateError, match="no http or https URL"):
        selfupdate.download_bytes(url, 100)

    assert opened == []


def test_text_from_the_network_cannot_rewrite_the_terminal(
    github: FakeGitHub, installed: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch
) -> None:
    original = github.answer

    def answer(path: str) -> tuple[int, bytes]:
        status, body = original(path)
        if path != "/api":
            return 404, b""
        document = json.loads(body)
        for asset in document["assets"]:
            asset["browser_download_url"] += "\x1b]0;x\x07"
        return status, json.dumps(document).encode()

    monkeypatch.setattr(github, "answer", answer)

    assert "\x1b" not in _refused(installed, capsys)
