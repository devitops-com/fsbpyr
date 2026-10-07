"""Phase 6 exit criteria: the compatibility matrix (fixture ``compat``) and multiprocessing
(fixture ``mp``) in single-file executables.

Needs the cached runtime and, on the first run, network access for the fixtures' wheels. The
HTTP clients talk to a local server, so the checks themselves need no network.
"""

import os
import pickle
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fsbpyr.cli import main
from fsbpyr.runtime.registry import Runtime

pytestmark = pytest.mark.slow

FIXTURES = Path(__file__).parents[1] / "fixtures" / "projects"
MATRIX = {
    "click": "click n=3",
    "typer": "typer hello Ada",
    "rich": "rich True",
    "certifi": "certifi True True",
    "pydantic": "pydantic {'x': 1, 'y': 0}",
    "jinja2": "jinja2 Hello Ada!",
    "pyyaml": "pyyaml {'a': [1, 2]} True",
    "numpy": "numpy [3, 5, 7]",
    "cryptography": "cryptography secret",
    "orjson": 'orjson {"a":[1,2]}',
    "zoneinfo": "zoneinfo 2:00:00 True",
    "sqlalchemy": "sqlalchemy 3 True",
    "paths": "paths True True",
    "requests": "requests 200 hello",
    "httpx": "httpx 200 hello",
}
MP_LINES = [
    "{method} pool [0, 1, 4, 9, 16]",
    "{method} executor [0, 1, 4]",
]


def _build(name: str, tmp: Path, *options: str) -> Path:
    project = tmp / name
    if not project.exists():
        shutil.copytree(FIXTURES / name, project)
    out = tmp / "bin" / f"{name}{len(options)}"
    assert main(["build", str(project), "-o", str(out), *options]) == 0
    return out


@pytest.fixture(scope="module")
def compat(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> Path:
    return _build("compat", tmp_path_factory.mktemp("compat"))


@pytest.fixture(scope="module")
def mp_tmp(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> Path:
    return tmp_path_factory.mktemp("mp")


class _Hello(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "5")
        self.end_headers()
        self.wfile.write(b"hello")

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Hello)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


def _run(executable: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(cache)}
    return subprocess.run([executable, *args], env=env, capture_output=True, text=True,
                          timeout=120, check=False)  # fmt: skip


@pytest.mark.parametrize("check", sorted(MATRIX))
def test_compatibility_matrix(compat: Path, url: str, tmp_path: Path, check: str) -> None:
    result = _run(compat, tmp_path / "cache", check, url)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == MATRIX[check]


def _site(cache: Path) -> Path:
    (site,) = cache.glob("runtime/*/site")
    return site


def test_fs_required_packages_live_in_the_cache(compat: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _run(compat, cache, "certifi")
    _run(compat, cache, "jinja2")

    site = _site(cache)
    assert (site / "certifi" / "cacert.pem").is_file()  # built-in rule: a stable path
    assert (site / "compat" / "templates" / "hello.txt").read_text() == "Hello {{ name }}!\n"


@pytest.mark.parametrize("entry", [(), ("--entry", "mp.cli")], ids=["function", "module"])
def test_every_start_method_works(mp_tmp: Path, tmp_path: Path, entry: tuple[str, ...]) -> None:
    executable = _build("mp", mp_tmp, *entry)

    result = _run(executable, tmp_path / "cache", "spawn", "forkserver", "fork")

    assert result.returncode == 0, result.stderr
    expected = [line.format(method=m) for m in ("spawn", "forkserver", "fork") for line in MP_LINES]
    assert result.stdout.splitlines() == [*expected, "shared_memory ok"]


def test_argv_is_never_run_as_python_code(mp_tmp: Path, tmp_path: Path) -> None:
    executable = _build("mp", mp_tmp)
    marker = tmp_path / "pwned"

    result = _run(executable, tmp_path / "cache", "-I", "-c",
                  f"import pathlib; pathlib.Path({str(marker)!r}).touch()")  # fmt: skip

    assert not marker.exists()
    assert "cannot find context for '-I'" in result.stderr  # the app ran, with these args


class _CreatesFile:
    """A pickle that creates ``path`` when loaded (``open(path, "w")``)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __reduce__(self) -> tuple[object, tuple[str, str]]:
        return open, (str(self.path), "w")


SPAWN = "from multiprocessing.spawn import spawn_main; spawn_main(pipe_handle={fd})"


def test_forged_multiprocessing_children_are_refused(mp_tmp: Path, tmp_path: Path) -> None:
    executable = _build("mp", mp_tmp)
    marker = tmp_path / "pwned"
    payload = pickle.dumps(_CreatesFile(marker))
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp_path / "cache")}

    forged = [str(executable), "-c", SPAWN.format(fd=0), "--multiprocessing-fork"]
    stdin = subprocess.run(forged, input=payload, env=env, capture_output=True, timeout=60,
                           check=False)  # fmt: skip
    read, write = os.pipe()
    os.write(write, payload)
    os.close(write)
    try:
        piped = subprocess.run(
            [executable, "-c", SPAWN.format(fd=read), "--multiprocessing-fork"],
            pass_fds=[read],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        os.close(read)

    assert not marker.exists()
    assert b"cannot find context for '-c'" in stdin.stderr  # stdio: not a child, the app ran
    assert piped.returncode == 1 and "refusing to run as a multiprocessing child" in piped.stderr


def test_children_run_the_parents_binary_after_an_in_place_upgrade(
    mp_tmp: Path, tmp_path: Path
) -> None:
    executable = tmp_path / "mp"
    shutil.copy(_build("mp", mp_tmp), executable)
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp_path / "cache")}
    process = subprocess.Popen([executable, "--wait", "spawn", "forkserver"], env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)  # fmt: skip
    assert process.stdout is not None and process.stdout.readline() == "ready\n"
    upgrade = tmp_path / "mp.new"
    shutil.copy(executable, upgrade)
    os.replace(upgrade, executable)  # a package manager's upgrade: a new file at the same path

    stdout, stderr = process.communicate("\n", timeout=120)

    assert process.returncode == 0, stderr
    expected = [line.format(method=m) for m in ("spawn", "forkserver") for line in MP_LINES]
    assert stdout.splitlines() == [*expected, "shared_memory ok"]


DATAFILES = {
    "botocore": "botocore s3",
    "pytz": "pytz Europe/Zagreb",
    "babel": "babel German",
    "matplotlib": "matplotlib True",
}


@pytest.fixture(scope="module")
def datafiles(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> Path:
    return _build("datafiles", tmp_path_factory.mktemp("datafiles"))


@pytest.mark.parametrize("check", sorted(DATAFILES))
def test_packages_reading_data_next_to_their_modules(
    datafiles: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, check: str
) -> None:
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))  # matplotlib's own font cache

    result = _run(datafiles, tmp_path / "cache", check)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == DATAFILES[check]
