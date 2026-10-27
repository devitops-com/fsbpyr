"""fsbpyr self update, against a fake GitHub served from localhost: nothing reaches the net."""

import hashlib
import http.client
import json
import lzma
import os
import stat
import sys
import threading
import types
import urllib.request
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from fsbpyr import dispatch, selfupdate
from fsbpyr.assemble import App, build_executable
from fsbpyr.cli import main
from fsbpyr.targets import target_named

from ..support.releases import FakeReleases
from .boot.conftest import FAKE_RUNTIME

OWN = "fsbpyr-3.14-x86_64-linux-gnu"  # the asset the 'running' executable was released as
RUNNING = f"{sys.version_info.major}.{sys.version_info.minor}"  # the Python this test runs on


class FakeGitHub:
    """The release document at /api and the files below /files, from ``routes``."""

    def __init__(self) -> None:
        self.tag = "v1.2.0"
        self.assets = {f"{OWN}.xz": b""}
        self.api_status = 200
        self.requests: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                fake.requests.append(self.path)
                if self.path == "/api":
                    body = json.dumps({
                        "tag_name": fake.tag,
                        "assets": [{"name": n, "browser_download_url": f"{fake.base}/files/{n}"}
                                   for n in fake.assets],
                    }).encode()  # fmt: skip
                    status = fake.api_status
                elif self.path.startswith("/files/") and self.path[7:] in fake.assets:
                    body, status = fake.assets[self.path[7:]], 200
                else:
                    body, status = b"", 404
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGitHub]:
    fake = FakeGitHub()
    monkeypatch.setenv(selfupdate.API_VARIABLE, f"{fake.base}/api")
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


def _release_binary(tmp_path: Path, name: str = "new") -> bytes:
    out = tmp_path / name
    build_executable(FAKE_RUNTIME, App("fsbpyr", "fsbpyr.cli:main", dists=()), out)
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
    variant, other = _release_binary(tmp_path, "variant"), b"another Python's"
    github.assets = {"fsbpyr-3.14-x86_64-linux-gnu.xz": lzma.compress(other),
                     "fsbpyr-3.13-x86_64-linux-gnu.xz": lzma.compress(other),
                     "fsbpyr-3.12-x86_64-linux-gnu.xz": lzma.compress(variant)}  # fmt: skip

    assert main(["self", "update"]) == 0

    assert installed.read_bytes() == variant
    assert github.requests == ["/api", "/files/fsbpyr-3.12-x86_64-linux-gnu.xz"]


def test_the_newest_pythons_build_stays_on_its_python_when_a_newer_one_is_released(
    github: FakeGitHub, installed: Path, tmp_path: Path
) -> None:
    binary = _release_binary(tmp_path)
    github.assets = {"fsbpyr-3.15-x86_64-linux-gnu.xz": lzma.compress(b"3.15"),
                     f"{OWN}.xz": lzma.compress(binary)}  # fmt: skip

    assert main(["self", "update"]) == 0

    assert installed.read_bytes() == binary
    assert github.requests == ["/api", f"/files/{OWN}.xz"]


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
    fake = FakeReleases()
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
    assert companion.requests == [f"/{COMPANION}/SHA256SUMS-glibc2.28"]


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
    assert "`--glibc` will refuse" in err and "v1.2.0" in err
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
    assert "will refuse them;" not in err


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
    out = tmp_path / "needs"
    build_executable(FAKE_RUNTIME, App("fsbpyr", "fsbpyr.cli:main", dists=(),
                                       build_info=(("glibc", glibc),)), out)  # fmt: skip
    return out.read_bytes()


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
        lambda tmp: selfupdate.unpack("https://example.invalid/x.xz", tmp / "x"),
        lambda tmp: selfupdate.download_text("https://example.invalid/sums"),
    ],
    ids=["latest-release", "unpack", "download-text"],
)
def test_a_body_cut_short_is_an_update_error(
    fetch: Callable[[Path], object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not http.client's IncompleteRead, which no command reports: a traceback."""
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: _CutShort())
    monkeypatch.setenv(selfupdate.API_VARIABLE, "https://example.invalid/api")

    with pytest.raises(selfupdate.UpdateError):
        fetch(tmp_path)


def test_unpack_stops_at_its_limit(tmp_path: Path) -> None:
    packed = tmp_path / "big.xz"
    packed.write_bytes(lzma.compress(b"\0" * 1_000_000))

    with pytest.raises(selfupdate.UpdateError, match="unpacks to more than 1000 bytes"):
        selfupdate.unpack(packed.as_uri(), tmp_path / "out", 1000)
