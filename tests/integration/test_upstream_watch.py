"""tools/upstream-watch.sh against a fake gh that serves python-build-standalone releases.

The script compares the newest PBS release that has a CPython 3.14 x86-64 pgo+lto-full archive
with the one src/fsbpyr/runtime/pins.py pins (the real file, so a change of its layout that the
script cannot read fails here), and opens or updates a `runtime-update` issue when they differ.
Nothing reaches GitHub: the fake gh logs every call and answers from files under ``tmp_path``.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from fsbpyr.runtime.pins import pin_for

ROOT = Path(__file__).parents[2]
WATCH_SH = ROOT / "tools" / "upstream-watch.sh"
SYSTEM_PATH = "/usr/bin:/bin"
SCRIPT_TIMEOUT_S = 60.0
PBS = "astral-sh/python-build-standalone"
TRIPLE = "x86_64-unknown-linux-gnu"
PIN = pin_for("3.14", TRIPLE)
PINNED = f"{PIN.name}.tar.zst"
WRITES = (["issue", "create"], ["issue", "edit"], ["label", "create"])


def asset(version: str, tag: str, flavor: str = "pgo+lto-full", triple: str = TRIPLE) -> str:
    return f"cpython-{version}+{tag}-{triple}-{flavor}.tar.zst"


NEWEST = asset("3.14.9", "20991231")

# A gh that logs every call (one JSON list of its arguments per line) and answers from
# $FAKE_PBS: `releases` (the tags, newest first), <tag>/assets, <tag>/notes and <tag>/<asset>,
# and `open-issue` (the number of the open runtime-update issue, if any). The answers are what
# the script's --jq expressions select. Any other call fails.
FAKE_GH = """\
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
data = Path(os.environ["FAKE_PBS"])
command = args[:2]
if command == ["release", "list"]:
    sys.stdout.write((data / "releases").read_text())
elif command == ["release", "view"]:
    field = args[args.index("--json") + 1]
    answer = data / args[2] / {"assets": "assets", "body": "notes"}[field]
    sys.stdout.buffer.write(answer.read_bytes())  # the notes' CRs included
elif command == ["release", "download"]:
    sys.stdout.buffer.write((data / args[2] / args[args.index("--pattern") + 1]).read_bytes())
elif command == ["issue", "list"]:
    issue = data / "open-issue"
    sys.stdout.write(issue.read_text() if issue.exists() else "")
elif command not in (["issue", "create"], ["issue", "edit"], ["label", "create"]):
    sys.exit(64)
"""

# tar --zstd decompresses with `zstd -d`: the fake archives are plain tar files.
FAKE_ZSTD = "#!/bin/sh\nexec cat\n"


def write_program(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def archive(llvm: str) -> bytes:
    """A PBS "full" archive, reduced to the PYTHON.json the script reads."""
    info = json.dumps({"build_info": {"object_file_format": f"llvm-bitcode:{llvm}"}}, indent=4)
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        member = tarfile.TarInfo("python/PYTHON.json")
        member.size = len(info)
        tar.addfile(member, io.BytesIO(info.encode()))
    return out.getvalue()


def release(data: Path, tag: str, assets: list[str], notes: str = "", llvm: str = "") -> None:
    (data / tag).mkdir(parents=True)
    (data / tag / "assets").write_text("".join(f"{name}\n" for name in assets))
    (data / tag / "notes").write_bytes(notes.encode())
    for name in assets:
        (data / tag / name).write_bytes(archive(llvm))


def run_watch(tmp_path: Path, tags: list[str]) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    write_program(bin_dir / "gh", f"#!{sys.executable}\n{FAKE_GH}")
    write_program(bin_dir / "zstd", FAKE_ZSTD)
    (tmp_path / "pbs" / "releases").write_text("".join(f"{tag}\n" for tag in tags))
    env = {
        "PATH": f"{bin_dir}:{SYSTEM_PATH}",
        "HOME": str(tmp_path / "home"),
        "FAKE_GH_LOG": str(tmp_path / "gh.log"),
        "FAKE_PBS": str(tmp_path / "pbs"),
    }
    command = ["bash", str(WATCH_SH)]
    return subprocess.run(
        command,
        env=env,
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT_S,
        check=False,
    )


def gh_calls(tmp_path: Path) -> list[list[str]]:
    log = tmp_path / "gh.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def option(call: list[str], name: str) -> str:
    return call[call.index(name) + 1]


def issue_calls(tmp_path: Path) -> list[list[str]]:
    return [call for call in gh_calls(tmp_path) if call[:2] in WRITES]


@pytest.fixture
def pbs(tmp_path: Path) -> Path:
    """The pinned release and the one before it, with the pinned archive's LLVM 22.1.3."""
    data = tmp_path / "pbs"
    release(data, PIN.release, [PINNED], "pinned notes", "22.1.3")
    release(data, "20261003", [asset("3.14.8", "20261003")])
    return data


def test_the_pinned_release_is_up_to_date_and_no_issue_is_touched(
    tmp_path: Path, pbs: Path
) -> None:
    """A newer release without a CPython 3.14 x86-64 pgo+lto-full archive (only the
    free-threaded build, other flavors and other Pythons) does not count."""
    release(pbs, "20991231", [
        asset("3.14.9", "20991231", "freethreaded+pgo+lto-full"),
        asset("3.14.9", "20991231", "install_only").replace(".tar.zst", ".tar.gz"),
        asset("3.14.9", "20991231", triple="aarch64-unknown-linux-gnu"),
        asset("3.15.1", "20991231"),
    ])  # fmt: skip

    result = run_watch(tmp_path, ["20991231", PIN.release, "20261003"])

    assert result.returncode == 0, result.stderr
    assert f"{PINNED} is the newest" in result.stdout
    assert issue_calls(tmp_path) == []
    assert not [call for call in gh_calls(tmp_path) if call[:2] == ["release", "download"]]
    (listing,) = [call for call in gh_calls(tmp_path) if call[:2] == ["release", "list"]]
    assert option(listing, "--repo") == PBS
    assert {"--exclude-drafts", "--exclude-pre-releases"} <= set(listing)


def test_a_newer_release_opens_an_issue_with_its_notes_and_the_llvm_change(
    tmp_path: Path, pbs: Path
) -> None:
    release(pbs, "20991231", [NEWEST, asset("3.14.9", "20991231", "debug-full")],
            "newest\r\nnotes", "23.1.3")  # fmt: skip
    release(pbs, "20991230", [asset(PIN.version, "20991230")], "between notes")

    result = run_watch(tmp_path, ["20991231", "20991230", PIN.release, "20261003"])

    assert result.returncode == 0, result.stderr
    label, create = issue_calls(tmp_path)
    assert label[:3] == ["label", "create", "runtime-update"]
    assert option(create, "--label") == "runtime-update"
    title, body = option(create, "--title"), option(create, "--body")
    assert "20991231" in title
    assert "LLVM 22.1.3 -> 23.1.3" in title
    assert NEWEST in body
    assert PINNED in body
    assert "LLVM 22.1.3 -> 23.1.3" in body
    assert "> newest\n> notes" in body  # quoted, without the notes' CRs
    assert "> between notes" in body
    assert "pinned notes" not in body
    downloads = [call for call in gh_calls(tmp_path) if call[:2] == ["release", "download"]]
    assert [(call[2], option(call, "--pattern")) for call in downloads] == [
        (PIN.release, PINNED),
        ("20991231", NEWEST),
    ]


def test_the_open_issue_is_updated_instead_of_opening_another(tmp_path: Path, pbs: Path) -> None:
    release(pbs, "20991231", [NEWEST], "newest notes", "22.1.5")
    (pbs / "open-issue").write_text("7\n")

    result = run_watch(tmp_path, ["20991231", PIN.release, "20261003"])

    assert result.returncode == 0, result.stderr
    (edit,) = issue_calls(tmp_path)
    (listing,) = [call for call in gh_calls(tmp_path) if call[:2] == ["issue", "list"]]
    assert option(listing, "--label") == "runtime-update"
    assert option(listing, "--state") == "open"
    assert edit[:3] == ["issue", "edit", "7"]
    assert "20991231" in option(edit, "--title")
    assert "LLVM" not in option(edit, "--title")  # the same major
    assert "LLVM 22.1.3 -> 22.1.5, the same major" in option(edit, "--body")


def test_no_release_with_the_archive_since_the_pin_is_an_error(tmp_path: Path, pbs: Path) -> None:
    """The pinned release is not among the newest ones searched, and none of them has the
    archive: something changed upstream."""
    release(pbs, "20991231", [asset("3.15.1", "20991231")])

    result = run_watch(tmp_path, ["20991231"])

    assert result.returncode == 1
    assert "none of the newest 30 releases" in result.stderr
    assert issue_calls(tmp_path) == []
