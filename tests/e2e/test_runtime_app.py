"""Phase 2 exit criteria, exercised on real executables built from the cached runtime."""

import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fsbpyr.payload.reader import Executable

from .conftest import BuildApp

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


def test_startup_is_within_budget_of_bare_cpython(build_app: BuildApp) -> None:
    app = build_app({"noop.py": "def main():\n    return 0\n"}, "noop:main")

    baseline = _p50([sys.executable, "-I", "-S", "-c", "pass"])
    measured = _p50([str(app)])

    assert measured <= 1.15 * baseline, f"app {measured * 1e3:.2f} ms vs {baseline * 1e3:.2f} ms"


@pytest.mark.parametrize("damage", ["truncate", "trailer"])
def test_corrupt_executable_fails_cleanly(build_app: BuildApp, damage: str) -> None:
    app = build_app({"x.py": "def main():\n    return 0\n"}, "x:main")
    data = bytearray(app.read_bytes())
    if damage == "truncate":
        del data[-100:]
    else:
        data[-64] ^= 0xFF
    app.write_bytes(bytes(data))

    result = _run(app)

    assert result.returncode == 70
    assert result.stderr.startswith("fsbpyr: cannot start")


def _flip_in_blob(app: Path, module: str) -> None:
    """Flip one byte inside the stored (zstd) code blob of ``module``."""
    with Executable.open(app) as exe:
        record = exe.payload.find_module(module)
        assert record is not None
        blob = exe.payload.blob(record.code)
        position = exe.payload_offset + blob.offset + blob.stored_size // 2
    data = bytearray(app.read_bytes())
    data[position] ^= 0x5A
    app.write_bytes(bytes(data))


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
