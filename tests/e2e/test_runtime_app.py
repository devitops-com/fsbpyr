"""Phase 2 exit criteria, exercised on real executables built from the cached runtime."""

import hashlib
import json
import os
import shutil
import signal
import statistics
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import (
    BLOB,
    DIGEST_LEAF,
    DIGEST_SIZE,
    TRAILER_SIZE,
    Codec,
    FileKind,
    Section,
    tree_digest,
)
from fsbpyr.payload.reader import Executable
from fsbpyr.payload.spec import Blob, File
from fsbpyr.runtime.registry import Runtime

from ..support.apps import library
from .conftest import BuildApp, app_content

pytestmark = pytest.mark.slow

HELLO = {
    "hello.py": """\
import sys

def main():
    import argparse, decimal, hashlib, json, sqlite3, ssl
    print("argv", sys.argv[1:])
    print("json", json.dumps({"ok": [1, 2]}))
    print("ssl", ssl.OPENSSL_VERSION.startswith("OpenSSL"), "sqlite", bool(sqlite3.sqlite_version))
    print("latin-1", "é".encode("latin-1"), "idna", "bücher".encode("idna"))
    print("path", sys.path == [sys.executable], __file__ == sys.executable + "/hello.py")
    return int(sys.argv[1]) if sys.argv[1:] and sys.argv[1].isdigit() else 0
"""
}


def _run(app: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [app, *args], capture_output=True, text=True, env={**os.environ, **env}, check=False
    )


def test_app_runs_stdlib_from_payload_and_returns_exit_code(build_app: BuildApp) -> None:
    app = build_app(HELLO, "hello:main")

    result = _run(app, "7", "a b")

    assert result.returncode == 7, result.stderr
    assert result.stdout.splitlines() == [
        "argv ['7', 'a b']",
        'json {"ok": [1, 2]}',
        "ssl True sqlite True",
        "latin-1 b'\\xe9' idna b'xn--bcher-kva'",
        "path True True",
    ]


def test_uncaught_exception_shows_only_app_frames_with_source(build_app: BuildApp) -> None:
    app = build_app({"boom.py": "def main():\n    raise ValueError('bad input')\n"}, "boom:main")

    result = _run(app)

    assert result.returncode == 1
    assert "<string>" not in result.stderr
    assert "_fsbpyr_boot" not in result.stderr
    assert f'File "{app}/boom.py", line 2, in main' in result.stderr  # absolute virtual path
    assert "raise ValueError('bad input')" in result.stderr
    assert result.stderr.rstrip().endswith("ValueError: bad input")


def test_system_exit_and_sigint_keep_cpython_semantics(build_app: BuildApp) -> None:
    app = build_app(
        {
            "ctl.py": "import sys\ndef main():\n    if sys.argv[1] == 'x': sys.exit('bye')\n"
            "    raise KeyboardInterrupt\n"
        },
        "ctl:main",
    )

    assert (_run(app, "x").returncode, _run(app, "x").stderr.strip()) == (1, "bye")
    assert _run(app, "k").returncode == -signal.SIGINT


def test_module_entry_point_runs_package_main(build_app: BuildApp) -> None:
    app = build_app(
        {
            "tool/__init__.py": "",
            "tool/__main__.py": "import sys\nprint('main of', __name__, sys.argv[1:])\n",
        },
        "tool",
    )

    result = _run(app, "--flag")

    assert (result.returncode, result.stdout.strip()) == (0, "main of __main__ ['--flag']")


def test_selftest_imports_entry_module_without_calling_it(build_app: BuildApp) -> None:
    app = build_app({"svc.py": "print('imported')\ndef main():\n    print('called')\n"}, "svc:main")

    result = _run(app, FSBPYR_SELFTEST="1")

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["imported", "fsbpyr selftest ok: app"]


STRACE = shutil.which("strace")


@pytest.mark.skipif(STRACE is None, reason="strace not installed")
def test_startup_never_touches_the_stdlib_on_disk(build_app: BuildApp, tmp_path: Path) -> None:
    app = build_app(HELLO, "hello:main")
    log = tmp_path / "trace.txt"

    subprocess.run(
        [str(STRACE), "-f", "-o", log, "-e", "trace=%file,getdents64", app],
        check=True,
        capture_output=True,
    )

    paths = [line for line in log.read_text().splitlines() if '"' in line]
    offending = [
        line
        for line in paths
        if any(marker in line for marker in ("python3", '.py"', ".pyc", "lib-dynload", "getdents"))
    ]
    assert offending == []
    assert len(paths) < 40, paths


def _p50(argv: list[str], runs: int = 60) -> float:
    for _ in range(5):
        subprocess.run(argv, check=True, capture_output=True)
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        subprocess.run(argv, check=True, capture_output=True)
        samples.append(time.perf_counter() - start)
    return statistics.median(samples)


def _integrity_p50(app: Path, runs: int = 15) -> float:
    """Seconds the launcher spent checking its file (D31), as FSBPYR_TRACE reports it."""
    samples = []
    for _ in range(runs):
        stderr = _run(app, FSBPYR_TRACE="1").stderr
        trace = json.loads(stderr.rpartition("fsbpyr-trace ")[2])
        samples.append(trace["integrity_ns"] / 1e9)
    return float(statistics.median(samples))


def test_startup_is_within_budget_of_bare_cpython(build_app: BuildApp) -> None:
    """The whole-file check costs what hashing the file costs (D31, about 20 MB for the stub);
    everything else stays within 20% of a bare CPython start (docs/performance.md)."""
    app = build_app({"noop.py": "def main():\n    return 0\n"}, "noop:main")

    baseline = _p50([sys.executable, "-I", "-S", "-c", "pass"])
    measured = _p50([str(app)])
    integrity = _integrity_p50(app)

    assert 0 < integrity < measured
    assert measured - integrity <= 1.2 * baseline, (
        f"app {measured * 1e3:.2f} ms ({integrity * 1e3:.2f} ms integrity check) "
        f"vs {baseline * 1e3:.2f} ms")  # fmt: skip


@pytest.mark.parametrize("damage", ["truncate", "trailer"])
def test_corrupt_executable_fails_cleanly(build_app: BuildApp, damage: str) -> None:
    app = build_app({"x.py": "def main():\n    return 0\n"}, "x:main")
    data = bytearray(app.read_bytes())
    if damage == "truncate":
        del data[-100:]
    else:
        data[-64] ^= 0xFF
    app.write_bytes(bytes(data))
    _restamp(app)  # past the integrity check: the trailer's own checks

    result = _run(app)

    assert result.returncode == 70
    assert result.stderr.startswith("fsbpyr: cannot start")


@pytest.mark.parametrize("where", ["padding", "header", "tables", "blob data", "trailer", "digest"])
def test_any_changed_byte_stops_the_executable_before_it_starts(
    build_app: BuildApp, where: str
) -> None:
    """D31: the launcher checks the whole file before anything else, so it starts nothing."""
    app = build_app({"x.py": "def main():\n    print('ran')\n"}, "x:main")
    with Executable.open(app) as exe:
        start, size = exe.payload_offset, exe.payload_size
    end = start + size
    position = {
        "padding": start - 1,
        "header": start + 30,
        "tables": start + 300,
        "blob data": end - 1,
        "trailer": end + 20,
        "digest": end + TRAILER_SIZE - 1,
    }
    data = bytearray(app.read_bytes())
    data[position[where]] ^= 0x01
    app.write_bytes(bytes(data))

    result = _run(app)

    assert result.returncode == 70 and result.stdout == ""
    assert result.stderr == (f"fsbpyr: cannot start {app}: integrity check failed: "
                             "the file was modified or damaged\n")  # fmt: skip


def test_a_changed_byte_in_the_stub_stops_the_executable(build_app: BuildApp) -> None:
    """The ELF section headers: inside launcher and CPython, read by no loader, so only the
    whole-file check can notice."""
    app = build_app({"x.py": "def main():\n    print('ran')\n"}, "x:main")
    data = bytearray(app.read_bytes())
    (section_headers,) = struct.unpack_from("<Q", data, 0x28)  # e_shoff
    with Executable.open(app) as exe:
        assert 0 < section_headers < exe.payload_offset
    data[section_headers + 4] ^= 0x01
    app.write_bytes(bytes(data))

    result = _run(app)

    assert (result.returncode, result.stdout) == (70, "")
    assert "integrity check failed" in result.stderr


def test_the_check_runs_on_one_core(build_app: BuildApp) -> None:
    app = build_app({"x.py": "def main():\n    print('ran')\n"}, "x:main")
    one = {min(os.sched_getaffinity(0))}

    def pinned() -> subprocess.CompletedProcess[str]:
        return subprocess.run([app], capture_output=True, text=True, check=False,
                              preexec_fn=lambda: os.sched_setaffinity(0, one))  # fmt: skip

    assert (pinned().returncode, pinned().stdout) == (0, "ran\n")
    data = bytearray(app.read_bytes())
    data[-DIGEST_SIZE] ^= 0x01
    app.write_bytes(bytes(data))
    assert pinned().returncode == 70


@pytest.mark.parametrize("remainder", [0, 1, DIGEST_LEAF - 1])
def test_launcher_and_writer_agree_on_the_digest_at_leaf_boundaries(
    build_app: BuildApp, remainder: int
) -> None:
    """Zero bytes before the trailer bring the covered length to ``remainder`` past a 1 MiB
    boundary; stamped with the writer's digest, the launcher must get past the check and stop
    at the trailer, whose payload size no longer fits the file."""
    app = build_app({"x.py": "def main():\n    return 0\n"}, "x:main")
    data = app.read_bytes()
    extra = (remainder - (len(data) - DIGEST_SIZE)) % DIGEST_LEAF
    covered = data[:-TRAILER_SIZE] + bytes(extra) + data[-TRAILER_SIZE:-DIGEST_SIZE]
    assert len(covered) % DIGEST_LEAF == remainder
    app.write_bytes(covered + tree_digest(covered))

    result = _run(app)

    assert result.returncode == 70
    assert "payload size does not match file size" in result.stderr, result.stderr


_READS_THE_LIBRARY = """\
import _fsbpyr

def main():
    record = _fsbpyr.find_file("x.libs/libbig.so")
    try:
        data = _fsbpyr.read(record[4])
    except _fsbpyr.PayloadError as exc:
        print("refused", exc)
    else:
        print("read", len(data))
"""


@pytest.mark.parametrize("damage", ["intact", "count", "piece codec", "piece size"])
def test_launcher_refuses_pieces_that_do_not_add_up(
    runtime: Runtime, tmp_path: Path, damage: str
) -> None:
    """D33: records damaged and the digest restamped, as a deliberate change would; the
    launcher checks a library's pieces as it decodes them, in parallel."""
    data = library(2300)  # three pieces
    big = File("x.libs/libbig.so", FileKind.NATIVE_LIB, Blob.xz(data, digest=True), mode=0o755)
    modules, files = app_content({"x.py": _READS_THE_LIBRARY})
    app = tmp_path / "app"
    build_executable(runtime, App("x", "x:main", modules=modules, files=(*files, big)), app)
    raw = bytearray(app.read_bytes())
    with Executable.open(app) as exe:
        start = exe.payload_offset
        record = exe.payload.find_file("x.libs/libbig.so")
        assert record is not None
        parent = exe.payload.blob(record.blob)
    blobs_at = start + struct.unpack_from("<Q", raw, start + 80 + 16 * Section.BLOBS)[0]
    parent_at, first_at = blobs_at + parent.index * BLOB.size, blobs_at + parent.offset * BLOB.size
    if damage == "count":
        struct.pack_into("<Q", raw, parent_at + 8, 4)  # four pieces cannot hold three pieces' size
    elif damage == "piece codec":
        struct.pack_into("<B", raw, first_at + 24, Codec.ZSTD)
    elif damage == "piece size":
        struct.pack_into("<Q", raw, first_at + 16, (4 << 20) - 1)
    app.write_bytes(bytes(raw))
    _restamp(app)

    result = _run(app)

    assert result.returncode == 0, result.stderr
    expected = f"read {len(data)}" if damage == "intact" else "refused corrupt executable payload"
    assert result.stdout.startswith(expected), result.stdout


def _restamp(app: Path) -> None:
    """Rewrite the digest, as a deliberate modification would: the checks behind it run."""
    data = bytearray(app.read_bytes())
    data[-DIGEST_SIZE:] = tree_digest(data[:-DIGEST_SIZE])
    app.write_bytes(bytes(data))


def _flip_in_blob(app: Path, module: str) -> None:
    """Flip one byte inside the stored code of ``module`` (its zstd frame, or its block's) and
    restamp the digest."""
    with Executable.open(app) as exe:
        record = exe.payload.find_module(module)
        assert record is not None
        blob = exe.payload.blob(record.code)
        if blob.codec is Codec.BLOCK:
            blob = exe.payload.blob(blob.offset)
        position = exe.payload_offset + blob.offset + blob.stored_size // 2
    data = bytearray(app.read_bytes())
    data[position] ^= 0x5A
    app.write_bytes(bytes(data))
    _restamp(app)


@pytest.mark.parametrize("module", ["_fsbpyr_boot", "encodings"])
def test_corrupt_startup_module_fails_cleanly(build_app: BuildApp, module: str) -> None:
    app = build_app({"x.py": "def main():\n    return 0\n"}, "x:main")
    _flip_in_blob(app, module)

    result = _run(app)

    assert result.returncode == 70
    assert "corrupt startup module" in result.stderr


def test_corrupt_app_module_raises_payload_error(build_app: BuildApp) -> None:
    app = build_app({"x.py": "def main():\n    import y\n", "y.py": "VALUE = 1\n" * 50}, "x:main")
    _flip_in_blob(app, "y")

    result = _run(app)

    assert result.returncode == 1
    assert "_fsbpyr.PayloadError: corrupt executable payload" in result.stderr


def test_launcher_block_cache_rereads_evicted_blocks(build_app: BuildApp) -> None:
    """Two members of each block in turn (a miss, then a hit), then every block in reverse: the
    last 32 are hits, and with more blocks than the launcher keeps (32) the rest are decompressed
    again after their eviction."""
    reader = "import _fsbpyr, hashlib, sys\n\ndef main():\n    for i in sys.argv[1:]:\n" \
             "        print(hashlib.sha256(_fsbpyr.read(int(i))).hexdigest())\n"  # fmt: skip
    app = build_app({"x.py": reader}, "x:main")
    with Executable.open(app) as exe:
        by_block: dict[int, list[int]] = {}
        for blob in exe.payload.blobs():
            if blob.codec is Codec.BLOCK:
                by_block.setdefault(blob.offset, []).append(blob.index)
        order = [i for m in by_block.values() for i in (m[0], m[-1])]
        order += [m[0] for m in reversed(by_block.values())]
        expected = [hashlib.sha256(exe.payload.read_blob(i)).hexdigest() for i in order]
    assert len(by_block) > 32

    result = _run(app, *map(str, order))

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == expected


def test_executable_path_need_not_be_utf8(build_app: BuildApp, tmp_path: Path) -> None:
    app = build_app({"x.py": "import sys\ndef main():\n    print(sys.path == [sys.executable])\n"},
                    "x:main")  # fmt: skip
    odd = Path(os.fsdecode(bytes(tmp_path) + b"/odd\xffdir"))
    odd.mkdir()
    moved = odd / "app"
    app.rename(moved)

    result = _run(moved)

    assert (result.returncode, result.stdout.strip()) == (0, "True"), result.stderr


def test_unencodable_module_name_is_not_found(build_app: BuildApp) -> None:
    source = (
        "import importlib\n"
        "def main():\n"
        "    try:\n"
        "        importlib.import_module('\\ud800')\n"
        "    except ModuleNotFoundError:\n"
        "        print('not found')\n"
    )
    app = build_app({"x.py": source}, "x:main")

    result = _run(app)

    assert (result.returncode, result.stdout.strip()) == (0, "not found"), result.stderr


def test_inspect_finds_sources_of_cpython_frozen_stdlib_modules(build_app: BuildApp) -> None:
    source = (
        "import inspect, os, sys\n"
        "def main():\n"
        "    assert os.__spec__.origin == 'frozen'  # loaded by FrozenImporter, not the payload\n"
        "    print(inspect.getsource(os).splitlines()[0])\n"
    )
    app = build_app({"insp.py": source}, "insp:main")

    result = _run(app)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith('r"""OS routines for NT or Posix')
