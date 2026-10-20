"""Phase 2 exit criteria, exercised on real executables built from the cached runtime."""

import grp
import hashlib
import json
import os
import random
import shutil
import signal
import stat
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from fsbpyr.assemble import App, build_executable
from fsbpyr.boot._fsbpyr_rt import gc
from fsbpyr.build import TARGET_PYTHON
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
        [app, *args], capture_output=True, text=True, env={**os.environ, **env}, check=False,
        timeout=60,
    )


@pytest.mark.sanitized
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


def _setgid_group() -> int | None:
    """A group to give the app so that its egid differs from the caller's gid: nogroup for
    root, a supplementary group otherwise (only those can be set on a file)."""
    if os.geteuid() == 0:
        return grp.getgrnam("nogroup").gr_gid
    others = [g for g in os.getgroups() if g != os.getegid()]
    return others[0] if others else None


def test_a_set_gid_start_is_refused(build_app: BuildApp) -> None:
    """The kernel sets AT_SECURE when the egid differs from the real gid (SEC-3)."""
    app = build_app(HELLO, "hello:main")
    group = _setgid_group()
    if group is None:
        pytest.skip("needs a supplementary group")
    if os.statvfs(app).f_flag & os.ST_NOSUID:
        pytest.skip("needs a mount that allows set-id bits, not nosuid")
    if "NoNewPrivs:\t1" in Path("/proc/self/status").read_text():
        pytest.skip("needs set-id bits, which no_new_privs disables")
    os.chown(app, -1, group)
    app.chmod(app.stat().st_mode | stat.S_ISGID)

    result = _run(app)

    assert result.returncode == 70, result.stderr
    assert "refusing a privileged start" in result.stderr


@pytest.mark.sanitized
def test_uncaught_exception_shows_only_app_frames_with_source(build_app: BuildApp) -> None:
    app = build_app({"boom.py": "def main():\n    raise ValueError('bad input')\n"}, "boom:main")

    result = _run(app)

    assert result.returncode == 1
    assert "<string>" not in result.stderr
    assert "_fsbpyr_boot" not in result.stderr
    assert f'File "{app}/boom.py", line 2, in main' in result.stderr  # absolute virtual path
    assert "raise ValueError('bad input')" in result.stderr
    assert result.stderr.rstrip().endswith("ValueError: bad input")


@pytest.mark.sanitized
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


_SITE_BUILTINS = """\
import sys
def main():
    if sys.argv[1] == "exit":
        exit(3)
    if sys.argv[1] == "quit":
        quit()
    print(callable(help))
"""


@pytest.mark.sanitized
def test_exit_quit_and_help_are_defined_as_site_defines_them(build_app: BuildApp) -> None:
    """Built apps never import site (D14), which defines them in a venv."""
    app = build_app({"bye.py": _SITE_BUILTINS}, "bye:main")

    exited, quitted, helped = _run(app, "exit"), _run(app, "quit"), _run(app, "help")

    assert (exited.returncode, exited.stderr) == (3, "")
    assert (quitted.returncode, quitted.stderr) == (0, "")
    assert (helped.returncode, helped.stdout) == (0, "True\n"), helped.stderr


_SIGNALS = """\
import signal
NAMES = ("SIGINT", "SIGPIPE", "SIGXFSZ")
def main():
    print([str(signal.getsignal(getattr(signal, name))) for name in NAMES])
"""


@pytest.mark.sanitized
def test_signals_are_handled_as_python_handles_them(build_app: BuildApp) -> None:
    """D38: SIGPIPE and SIGXFSZ are ignored, as by `python`: a write to a closed pipe or socket
    raises BrokenPipeError, and a write past the file-size limit (ulimit -f) fails with EFBIG;
    both used to end the process."""
    app = build_app({"sig.py": _SIGNALS}, "sig:main")
    python = subprocess.run([sys.executable, "-c", _SIGNALS + "main()"], capture_output=True,
                            text=True, check=True)  # fmt: skip

    handled = _run(app).stdout

    assert handled == python.stdout
    assert handled.endswith(", '1', '1']\n")  # SIGPIPE and SIGXFSZ: SIG_IGN


_WRITES_TO_A_CLOSED_PIPE = """\
import os
def main():
    read, write = os.pipe()
    os.close(read)
    try:
        os.write(write, b"x")
    except BrokenPipeError:
        print("BrokenPipeError")
"""


@pytest.mark.sanitized
def test_a_write_to_a_closed_pipe_raises(build_app: BuildApp) -> None:
    app = build_app({"pipe.py": _WRITES_TO_A_CLOSED_PIPE}, "pipe:main")

    result = _run(app)

    assert (result.returncode, result.stdout) == (0, "BrokenPipeError\n"), result.stderr


_READS_AFTER_CLOSING_EVERY_DESCRIPTOR = """\
import hashlib, os
from importlib.resources import files

def main():
    os.closerange(3, 1 << 16)  # as daemons do: the launcher's own descriptor goes too
    reused = os.open("/dev/null", os.O_RDONLY)  # and its number names another file now
    print(reused, hashlib.sha256(files("x").joinpath("raw.bin").read_bytes()).hexdigest())
"""


@pytest.mark.sanitized
def test_raw_blobs_are_read_after_the_app_closed_every_descriptor(
    runtime: Runtime, tmp_path: Path
) -> None:
    """D38 (security review): raw blobs of a leaf or more are read from the executable, through
    a descriptor the launcher keeps; when the app closed it (and its number was reused), the
    launcher sees it is not the executable and opens /proc/self/exe again."""
    content = random.Random(9).randbytes(2 * DIGEST_LEAF)  # noqa: S311  # incompressible test data
    raw = File("x/raw.bin", FileKind.RESOURCE, Blob.raw(content))
    modules, files = app_content({"x/__init__.py": _READS_AFTER_CLOSING_EVERY_DESCRIPTOR})
    app = tmp_path / "app"
    build_executable(runtime, App("x", "x:main", modules=modules, files=(*files, raw)), app)

    result = _run(app, FSBPYR_CACHE_DIR=str(tmp_path / "cache"))

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"3 {hashlib.sha256(content).hexdigest()}\n"


_EXTRACTS_UNDER_A_FILE_SIZE_LIMIT = """\
import _fsbpyr
from _fsbpyr_rt.native import native_cache

def main():
    try:
        native_cache().sync(_fsbpyr.files_below("x.libs"))
    except OSError as exc:
        print("failed", exc.errno)
"""


@pytest.mark.sanitized
def test_a_file_size_limit_fails_an_extraction_and_ends_nothing(
    runtime: Runtime, tmp_path: Path
) -> None:
    """D38 (security review): under ulimit -f, writing a library past the limit fails with EFBIG
    and the sync says so; SIGXFSZ used to end the process with a core dump."""
    import resource  # noqa: PLC0415

    content = random.Random(8).randbytes(3 << 20)  # noqa: S311  # incompressible test data
    native = File("x.libs/libbig.so", FileKind.NATIVE_LIB, Blob.zstd(content, digest=True),
                  mode=0o755)  # fmt: skip
    modules, files = app_content({"x.py": _EXTRACTS_UNDER_A_FILE_SIZE_LIMIT})
    app = tmp_path / "app"
    build_executable(runtime, App("x", "x:main", modules=modules, files=(*files, native)), app)

    def limit() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, resource.RLIM_INFINITY))

    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp_path / "cache")}
    result = subprocess.run([app], capture_output=True, text=True, check=False, env=env,
                            preexec_fn=limit)  # fmt: skip

    assert (result.returncode, result.stdout) == (0, "failed 27\n"), result.stderr  # EFBIG
    left = [p.name for p in (tmp_path / "cache").rglob("*") if p.is_file()]
    assert sorted(left) == [gc.MARKER, gc.LOCK]  # nothing but the cache's marker, the tree's lock


@pytest.mark.sanitized
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


@pytest.mark.sanitized
def test_selftest_imports_entry_module_without_calling_it(build_app: BuildApp) -> None:
    app = build_app({"svc.py": "print('imported')\ndef main():\n    print('called')\n"}, "svc:main")

    result = _run(app, FSBPYR_SELFTEST="1")

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["imported", "fsbpyr selftest ok: app"]


@pytest.mark.sanitized
def test_selftest_of_a_package_entry_finds_its_main_without_running_it(
    build_app: BuildApp,
) -> None:
    with_main = build_app({"tool/__init__.py": "", "tool/__main__.py": "print('ran')\n"}, "tool")
    found = _run(with_main, FSBPYR_SELFTEST="1")
    without = _run(build_app({"tool/__init__.py": ""}, "tool"), FSBPYR_SELFTEST="1")

    assert (found.returncode, found.stdout) == (0, "fsbpyr selftest ok: app\n")
    assert without.returncode == 1 and "tool.__main__" in without.stderr


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
    everything else stays within 50% of a bare CPython start (docs/performance.md)."""
    app = build_app({"noop.py": "def main():\n    return 0\n"}, "noop:main")

    baseline = _p50([sys.executable, "-I", "-S", "-c", "pass"])
    measured = _p50([str(app)])
    integrity = _integrity_p50(app)

    assert 0 < integrity < measured
    assert measured - integrity <= 1.5 * baseline, (
        f"app {measured * 1e3:.2f} ms ({integrity * 1e3:.2f} ms integrity check) "
        f"vs {baseline * 1e3:.2f} ms")  # fmt: skip


@pytest.mark.sanitized
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


@pytest.mark.sanitized
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


@pytest.mark.sanitized
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


@pytest.mark.sanitized
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


@pytest.mark.sanitized
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


@pytest.mark.sanitized
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


_SYNCS_THE_LIBRARIES = """\
import _fsbpyr
from _fsbpyr_rt.native import native_cache

def main():
    try:
        print("written", native_cache().sync(_fsbpyr.files_below("x.libs")))
    except ImportError as exc:
        print("refused", exc)
"""


@pytest.mark.sanitized
@pytest.mark.parametrize(
    "unsafe", [b"x.libs/../evil.so", b"x.libs/libe\0il.so", b"x.libs/libe\\il.so"]
)
def test_launcher_never_extracts_a_file_whose_path_could_leave_the_cache(
    runtime: Runtime, tmp_path: Path, unsafe: bytes
) -> None:
    """D35: the payload reader refuses such paths; the launcher checks them again before it
    touches the cache, records and digest restamped as a deliberate change would have them."""
    natives = tuple(File(f"x.libs/lib{name}.so", FileKind.NATIVE_LIB,
                         Blob.zstd(b"\x7fELF " + name.encode(), digest=True), mode=0o755)
                    for name in ("evil", "good"))  # fmt: skip
    modules, files = app_content({"x.py": _SYNCS_THE_LIBRARIES})
    app = tmp_path / "app"
    build_executable(runtime, App("x", "x:main", modules=modules, files=(*files, *natives)), app)
    raw = app.read_bytes()
    assert raw.count(b"x.libs/libevil.so\0") == 1 and len(unsafe) == len("x.libs/libevil.so")
    app.write_bytes(raw.replace(b"x.libs/libevil.so\0", unsafe + b"\0"))
    _restamp(app)
    cache = tmp_path / "cache"

    result = _run(app, FSBPYR_CACHE_DIR=str(cache))

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("refused corrupt executable payload: extracted file")
    assert not cache.exists()  # every record is checked before anything is written


_CHECKS_IN_LITTLE_ADDRESS_SPACE = """\
import resource, sys
import _fsbpyr

def main():
    root, files, count = sys.argv[1], _fsbpyr.files_below("x.libs"), _fsbpyr.info()["files"]
    if sys.argv[2:] == ["limited"]:  # room for two of the libraries mapped at once
        with open("/proc/self/status") as status:
            size = next(int(line.split()[1]) for line in status if line.startswith("VmSize:"))
        hard = resource.getrlimit(resource.RLIMIT_AS)[1]
        resource.setrlimit(resource.RLIMIT_AS, ((size << 10) + (160 << 20), hard))
    try:
        memo = bytearray((count + 7) // 8)
        print("written", _fsbpyr.sync(root, "runtime/g/site", files, memo, False))
    except OSError as exc:
        print("failed", exc.errno)
"""


def test_cached_files_are_checked_in_less_address_space_than_they_take(
    runtime: Runtime, tmp_path: Path
) -> None:
    """D35, D36: under an address-space limit (ulimit -v) smaller than the cached libraries, the
    check neither counts them as changed nor fails the run: it reads them (D36); D35 mapped them,
    and had to hash and unmap a pass before mapping more."""
    natives = tuple(File(f"x.libs/lib{i}.so", FileKind.NATIVE_LIB,
                         Blob.zstd((b"%d" % i * (64 << 20))[: 64 << 20], digest=True), mode=0o755)
                    for i in range(6))  # fmt: skip
    modules, files = app_content({"x.py": _CHECKS_IN_LITTLE_ADDRESS_SPACE})
    app = tmp_path / "app"
    build_executable(runtime, App("x", "x:main", modules=modules, files=(*files, *natives)), app)
    root = str(tmp_path / "cache")

    first, limited = _run(app, root), _run(app, root, "limited")

    assert (first.stdout, first.stderr) == ("written 6\n", "")
    assert (limited.stdout, limited.stderr) == ("written 0\n", "")


_FORKS_WHILE_SYNCS_DECODE = """\
import os, shutil, sys, threading, time, warnings
import _fsbpyr

def main():
    warnings.simplefilter("ignore", DeprecationWarning)  # fork() with a thread running: the case
    root, files, count = sys.argv[1], _fsbpyr.files_below("x.data"), _fsbpyr.info()["files"]
    blobs = [_fsbpyr.find_file(path)[4] for path in ("x.data/big.bin", "x.data/r00.txt")]
    stop = threading.Event()

    def syncing():
        while not stop.is_set():  # the tree removed each time: every file decoded and written
            shutil.rmtree(os.path.join(root, "runtime"), ignore_errors=True)
            _fsbpyr.sync(root, "runtime/r", files, bytearray((count + 7) // 8), True)

    thread = threading.Thread(target=syncing)
    thread.start()
    hung = forked = 0
    while forked < 40 and not hung:
        forked += 1
        time.sleep(0.005)
        pid = os.fork()
        if pid == 0:  # the process's own decoder: zstd, and a block
            for blob in blobs:
                _fsbpyr.read(blob)
            os._exit(0)
        deadline = time.monotonic() + 3
        while os.waitpid(pid, os.WNOHANG) == (0, 0):
            if time.monotonic() > deadline:
                os.kill(pid, 9)
                os.waitpid(pid, 0)
                hung += 1
                break
            time.sleep(0.01)
    stop.set()
    thread.join()
    print(f"hung {hung} of {forked}")
"""


@pytest.mark.sanitized
def test_a_fork_while_a_sync_decodes_leaves_the_child_able_to_read(
    runtime: Runtime, tmp_path: Path
) -> None:
    """D35: a sync decodes with a decoder of its own, without the GIL; nothing it holds may stay
    held in a child forked meanwhile (a lock shared with read() would be)."""
    letters = bytes(b"abcdefgh"[i % 8] for i in range(256))  # 3 bits a byte: zstd decodes slowly
    big = random.Random(0).randbytes(8 << 20).translate(letters)  # noqa: S311  # test data
    data = {"x.data/big.bin": big} | {f"x.data/r{i:02}.txt": b"%d" % i * 500 for i in range(50)}
    resources = tuple(File(path, FileKind.RESOURCE, Blob.zstd(content))
                      for path, content in data.items())  # fmt: skip
    modules, files = app_content({"x.py": _FORKS_WHILE_SYNCS_DECODE})
    app = tmp_path / "app"
    build_executable(runtime, App("x", "x:main", modules=modules, files=(*files, *resources)), app)
    with Executable.open(app) as exe:
        record = exe.payload.find_file("x.data/r00.txt")
        assert record is not None and exe.payload.blob(record.blob).codec is Codec.BLOCK

    result = _run(app, str(tmp_path / "cache"))

    assert result.stdout == "hung 0 of 40\n", result.stderr


_RUNS_A_RESOURCE_TOOL = """\
import os, stat, subprocess
from importlib.resources import files

def main():
    tool = files("x") / "bin/tool"
    path = str(tool)
    ran = subprocess.run([path], capture_output=True, text=True).stdout.strip()
    print(os.access(path, os.X_OK), ran)
    big = str(files("x") / "bin/big")
    print(oct(stat.S_IMODE(os.stat(big).st_mode)))
"""


def test_an_executable_resource_runs_and_a_large_one_extracts_0500(
    runtime: Runtime, tmp_path: Path
) -> None:
    """RT-3 (D27): resources marked executable keep owner-exec when extracted (never writable)."""
    script = b"#!/bin/sh\necho tool-ran\n"
    big = b"#!/bin/sh\n" + bytes(17 << 20)  # over 16 MiB, compressible
    resources = (File("x/bin/tool", FileKind.RESOURCE, Blob.zstd(script), mode=0o755),
                 File("x/bin/big", FileKind.RESOURCE, Blob.zstd(big), mode=0o755))  # fmt: skip
    modules, files = app_content({"x/__init__.py": _RUNS_A_RESOURCE_TOOL})
    app = tmp_path / "app"
    build_executable(runtime, App("x", "x:main", modules=modules, files=(*files, *resources)), app)

    result = _run(app, FSBPYR_CACHE_DIR=str(tmp_path / "cache"))

    assert result.stdout == "True tool-ran\n0o500\n", result.stderr


_RESIDENT = """\
import mmap, os, sys
import _fsbpyr

def peak():
    \"\"\"MiB: peak RSS since exec (ru_maxrss includes the forked parent's).\"\"\"
    with open("/proc/self/status") as status:
        return next(int(line.split()[1]) for line in status if line.startswith("VmHWM:")) >> 10

def resident(path):
    \"\"\"KiB of ``path`` resident in this process (/proc/self/smaps).\"\"\"
    total, inside = 0, False
    for line in open("/proc/self/smaps"):
        fields = line.split()
        if "-" in fields[0] and ":" not in fields[0]:
            inside = fields[-1] == path
        elif inside and fields[0] == "Rss:":
            total += int(fields[1])
    return total

def main():
    started = peak()  # the executable checked itself
    root, files, count = sys.argv[1], _fsbpyr.files_below("x.libs"), _fsbpyr.info()["files"]
    _fsbpyr.sync(root, "runtime/g/site", files, bytearray((count + 7) // 8), False)
    checked = peak()
    path = os.path.join(root, "runtime/g/site/x.libs/libbig.so")
    with open(path, "rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as lib:
        lib[0]  # one page, as the dynamic loader touches a library
        print(started, checked, resident(path))
"""


def test_checks_read_files_without_making_them_resident(runtime: Runtime, tmp_path: Path) -> None:
    """D36: the executable's check and the cache check read files a leaf at a time on their
    threads instead of mapping them, so neither file's size shows in the process's RSS; a
    restored file is written in pieces of 1 MiB, so the page cache holds it in folios no larger,
    and touching a page of it makes kilobytes resident, not 2 MiB (156 instead of 60 MB of
    libtorch_cpu.so for torch, written whole on XFS)."""
    library = (bytes(range(256)) * (96 << 12))[: 96 << 20]  # 96 MiB
    noise = random.Random(1).randbytes(64 << 20)  # noqa: S311  # test data: 64 MiB stored raw
    files = (File("x.libs/libbig.so", FileKind.NATIVE_LIB, Blob.zstd(library, digest=True),
                  mode=0o755),
             File("x.data/noise.bin", FileKind.RESOURCE, Blob.zstd(noise)))  # fmt: skip
    modules, sources = app_content({"x.py": _RESIDENT})
    app = tmp_path / "app"
    build_executable(runtime, App("x", "x:main", modules=modules, files=(*sources, *files)), app)
    assert app.stat().st_size > 64 << 20
    disk = Path("/var/tmp")  # noqa: S108  # a disk file system (tmpfs keeps small pages only)
    with tempfile.TemporaryDirectory(dir=disk if disk.is_dir() else tmp_path) as cache:  # private
        first = _run(app, cache)  # extracts the library
        later = _run(app, cache)  # checks it

    assert first.returncode == later.returncode == 0, first.stderr + later.stderr
    started, checked, touched = map(int, later.stdout.split())
    assert started < 48 and checked < 48, later.stdout  # MiB: neither file resident
    assert 4 <= touched < 2048, later.stdout  # KiB: the page, and no 2 MiB folio for it


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


@pytest.mark.sanitized
@pytest.mark.parametrize("module", ["_fsbpyr_boot", "encodings"])
def test_corrupt_startup_module_fails_cleanly(build_app: BuildApp, module: str) -> None:
    app = build_app({"x.py": "def main():\n    return 0\n"}, "x:main")
    _flip_in_blob(app, module)

    result = _run(app)

    assert result.returncode == 70
    assert "corrupt startup module" in result.stderr


@pytest.mark.sanitized
def test_corrupt_app_module_raises_payload_error(build_app: BuildApp) -> None:
    app = build_app({"x.py": "def main():\n    import y\n", "y.py": "VALUE = 1\n" * 50}, "x:main")
    _flip_in_blob(app, "y")

    result = _run(app)

    assert result.returncode == 1
    assert "_fsbpyr.PayloadError: corrupt executable payload" in result.stderr


@pytest.mark.sanitized
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


@pytest.mark.sanitized
def test_executable_path_need_not_be_utf8(build_app: BuildApp, tmp_path: Path) -> None:
    app = build_app({"x.py": "import sys\ndef main():\n    print(sys.path == [sys.executable])\n"},
                    "x:main")  # fmt: skip
    odd = Path(os.fsdecode(bytes(tmp_path) + b"/odd\xffdir"))
    odd.mkdir()
    moved = odd / "app"
    app.rename(moved)

    result = _run(moved)

    assert (result.returncode, result.stdout.strip()) == (0, "True"), result.stderr


@pytest.mark.sanitized
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


@pytest.mark.sanitized
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


def _counts(app: Path) -> dict[str, int]:
    """The read counters FSBPYR_TRACE reports for one start (launcher/module.c ``stats``)."""
    trace = json.loads(_run(app, FSBPYR_TRACE="1").stderr.rpartition("fsbpyr-trace ")[2])
    return {key: trace[key] for key in ("reads", "stored_bytes", "decompressed_bytes")}


def test_startup_reads_the_same_with_a_large_unused_dist(runtime: Runtime, tmp_path: Path) -> None:
    """D37: counts, not timings. What a no-op start reads does not depend on what else the
    executable holds."""
    noop = {"noop.py": "def main():\n    return 0\n"}
    modules, files = app_content(noop)
    rng = random.Random(0)  # noqa: S311  # test data
    unused = tuple(
        File(f"unused/f{i:03}.bin", FileKind.RESOURCE, Blob.zstd(rng.randbytes(1 << 18)))
        for i in range(120)  # 30 MiB, incompressible
    )
    small, large = tmp_path / "small", tmp_path / "large"
    build_executable(runtime, App("app", "noop:main", modules=modules, files=files), small)
    build_executable(
        runtime, App("app", "noop:main", modules=modules, files=(*files, *unused)), large
    )

    assert large.stat().st_size > small.stat().st_size + (20 << 20)
    assert _counts(large) == _counts(small)
    assert _counts(small)["reads"] > 0


# The no-op app's size on each version's pinned runtime (D15 makes it deterministic).
NOOP_SIZES = {"3.12": 27_409_138, "3.13": 26_664_561, "3.14": 27_695_360}
NOOP_SIZE_TOLERANCE = 0.03


def test_noop_app_size_is_within_3_percent_of_the_recorded_size(build_app: BuildApp) -> None:
    """A change to the stdlib selection shows here only with a freshly built runtime: the e2e
    `runtime` fixture reuses a cached one (CI builds it fresh)."""
    app = build_app({"noop.py": "def main():\n    return 0\n"}, "noop:main")

    size = app.stat().st_size
    assert abs(size / NOOP_SIZES[TARGET_PYTHON] - 1) <= NOOP_SIZE_TOLERANCE, size
