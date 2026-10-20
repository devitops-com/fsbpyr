"""fsbpyr self update, against a fake GitHub served from localhost: nothing reaches the net."""

import json
import lzma
import os
import stat
import sys
import threading
import types
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from fsbpyr import selfupdate
from fsbpyr.assemble import App, build_executable
from fsbpyr.cli import main

from .boot.conftest import FAKE_RUNTIME


class FakeGitHub:
    """The release document at /api and the files below /files, from ``routes``."""

    def __init__(self) -> None:
        self.tag = "v1.2.0"
        self.assets = {selfupdate.ASSET: b""}
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
    monkeypatch.setattr(selfupdate, "_render", lambda new, command: b"new " + b" ".join(
        c.encode() for c in command))  # fmt: skip
    return path


def test_updates_the_executable_in_place_keeping_its_mode(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = _release_binary(tmp_path)
    github.assets[selfupdate.ASSET] = lzma.compress(binary)

    assert main(["self", "update"]) == 0

    assert installed.read_bytes() == binary
    assert stat.S_IMODE(installed.stat().st_mode) == 0o750
    assert sorted(p.name for p in installed.parent.iterdir()) == ["fsbpyr"]  # no temporary left
    assert "updated" in capsys.readouterr().out


def test_updates_through_a_symlink_target_the_real_file(
    github: FakeGitHub, installed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = tmp_path / "link"
    link.symlink_to(installed)
    monkeypatch.setattr(selfupdate, "running_executable", lambda: Path(os.path.realpath(link)))
    binary = _release_binary(tmp_path)
    github.assets[selfupdate.ASSET] = lzma.compress(binary)

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


def test_a_developer_build_is_refused_with_the_curl_command(
    github: FakeGitHub, installed: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(selfupdate, "own_version", lambda: "untagged-abcdef12")

    assert main(["self", "update"]) == 1

    err = capsys.readouterr().err
    assert "developer build" in err
    assert "curl -fLo ~/.local/bin/fsbpyr https://github.com/" in err
    assert github.requests == []


def test_a_checkout_is_refused(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "_fsbpyr", None)  # import fails
    monkeypatch.setattr(selfupdate, "own_version", lambda: "1.0.0-abcdef12")

    assert main(["self", "update"]) == 1
    assert "executable only" in capsys.readouterr().err
    assert github.requests == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_an_unwritable_directory_is_an_error_saying_how_to_update(
    github: FakeGitHub, installed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    installed.parent.chmod(0o555)
    try:
        assert main(["self", "update"]) == 1
    finally:
        installed.parent.chmod(0o755)

    assert "cannot write to" in capsys.readouterr().err
    assert github.requests == []


def test_a_damaged_download_is_never_installed(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = bytearray(_release_binary(tmp_path))
    binary[len(binary) // 2] ^= 0xFF
    github.assets[selfupdate.ASSET] = lzma.compress(bytes(binary))

    assert main(["self", "update"]) == 1

    assert installed.read_bytes() == b"old"
    assert sorted(p.name for p in installed.parent.iterdir()) == ["fsbpyr"]
    assert "verification" in capsys.readouterr().err


def test_something_that_is_not_an_fsbpyr_executable_is_never_installed(
    github: FakeGitHub, installed: Path
) -> None:
    github.assets[selfupdate.ASSET] = lzma.compress(b"#!/bin/sh\necho pwned\n")

    assert main(["self", "update"]) == 1
    assert installed.read_bytes() == b"old"


def test_a_truncated_xz_stream_is_an_error(
    github: FakeGitHub, installed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    packed = lzma.compress(_release_binary(tmp_path))
    github.assets[selfupdate.ASSET] = packed[: len(packed) // 2]

    assert main(["self", "update"]) == 1
    assert installed.read_bytes() == b"old"
    assert sorted(p.name for p in installed.parent.iterdir()) == ["fsbpyr"]
    assert "xz stream" in capsys.readouterr().err


def test_a_missing_asset_is_an_error(
    github: FakeGitHub, installed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    github.assets = {"other": b""}

    assert main(["self", "update"]) == 1
    assert "no fsbpyr.xz" in capsys.readouterr().err


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
    github.assets[selfupdate.ASSET] = lzma.compress(_release_binary(tmp_path))

    assert main(["self", "update"]) == 0

    assert man.read_bytes() == b"new man"
    assert stat.S_IMODE(man.stat().st_mode) == 0o640
    assert bash.read_bytes() == b"new completion bash"
    assert not (prefix / "share/zsh").exists()  # absent ones are not created
    assert [p.name for p in man.parent.iterdir()] == ["fsbpyr.1"]  # no temporary left
    assert "left alone" in capsys.readouterr().err  # the hint for ones not found


def test_an_executable_outside_a_bin_directory_gets_the_hint_only(
    github: FakeGitHub, installed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    elsewhere = tmp_path / "opt" / "fsbpyr"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"old")
    monkeypatch.setattr(selfupdate, "running_executable", lambda: elsewhere)
    github.assets[selfupdate.ASSET] = lzma.compress(_release_binary(tmp_path))

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
    github.assets[selfupdate.ASSET] = lzma.compress(_release_binary(tmp_path))

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
