"""Phase 6 exit criteria: the compatibility matrix (fixture ``compat``) and multiprocessing
(fixture ``mp``) in single-file executables.

Needs the cached runtime and, on the first run, network access for the fixtures' wheels. The
HTTP clients talk to a local server, so the checks themselves need no network.
"""

import ast
import hashlib
import json
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
from fsbpyr.payload.reader import Executable
from fsbpyr.runtime.registry import Runtime

from ..support.differential import assert_same, make_venv
from ..support.stderr import noise

pytestmark = pytest.mark.slow

FIXTURES = Path(__file__).parents[1] / "fixtures" / "projects"
MATRIX = {  # check -> its output; the differential compares with the venv's run instead
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
    "charset_normalizer": "charset_normalizer True",
    "requests": "requests 200 hello",
    "httpx": "httpx 200 hello",
    "setuptools": "setuptools setuptools True",  # RT-4
}
MP_LINES = [
    "{method} pool [0, 1, 4, 9, 16]",
    "{method} executor [0, 1, 4]",
]


def _build(name: str, tmp: Path, *options: str) -> Path:
    project = tmp / name
    if not project.exists():
        shutil.copytree(FIXTURES / name, project)
    out = tmp / "bin" / f"{name}{len(options) or ''}"  # named like the console script
    assert main(["build", str(project), "-o", str(out), *options]) == 0
    return out


@pytest.fixture(scope="module")
def compat(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> Path:
    return _build("compat", tmp_path_factory.mktemp("compat"))


@pytest.fixture(scope="module")
def compat_venv(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> Path:
    return _venv("compat", tmp_path_factory, runtime)


@pytest.fixture(scope="module")
def mp_venv(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> Path:
    return _venv("mp", tmp_path_factory, runtime)


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


def _venv(name: str, factory: pytest.TempPathFactory, runtime: Runtime) -> Path:
    """The fixture project run from a venv, on the runtime's Python (TST-2)."""
    project = factory.mktemp(f"{name}-src") / name
    shutil.copytree(FIXTURES / name, project)
    python = runtime.python_version.rpartition(".")[0]
    return make_venv(project, factory.mktemp(f"{name}-venv"), python)


def _run(executable: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(cache)}
    return subprocess.run([executable, *args], env=env, capture_output=True, text=True,
                          timeout=120, check=False)  # fmt: skip


@pytest.mark.parametrize("check", sorted(MATRIX))
def test_compatibility_matrix(
    compat: Path, compat_venv: Path, url: str, tmp_path: Path, check: str
) -> None:
    env = {"FSBPYR_CACHE_DIR": str(tmp_path / "cache")}

    result = assert_same(compat_venv, compat, [check, url], env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() != ""
    assert noise(result.stderr) == ""


def test_every_command_of_the_compat_fixture_is_in_the_matrix() -> None:
    source = (FIXTURES / "compat" / "src" / "compat" / "cli.py").read_text()
    (checks,) = [n.value for n in ast.parse(source).body
                 if isinstance(n, ast.Assign) and n.targets[0].id == "CHECKS"]  # type: ignore[attr-defined]  # fmt: skip
    assert isinstance(checks, ast.Dict)
    names = [str(k.value) for k in checks.keys if isinstance(k, ast.Constant)]

    assert sorted(names) == sorted(MATRIX) and len(MATRIX) == 17


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


@pytest.mark.parametrize(
    ("options", "expected"),
    [(("--interpreter-reexec",), ["0 helper ['a']", "0 code ['-c', 'b']", "4"]),
     ((), ["1", "1", "1"])],  # the app ran in the children, with their arguments: an error
    ids=["interpreter-reexec", "default"],
)  # fmt: skip
def test_the_app_starts_helper_interpreters_only_when_built_so(
    mp_tmp: Path, tmp_path: Path, options: tuple[str, ...], expected: list[str]
) -> None:
    executable = _build("mp", mp_tmp, *options)

    result = _run(executable, tmp_path / "cache", "reexec")

    assert result.returncode == 0, result.stderr
    assert [line.rstrip() for line in result.stdout.splitlines()] == expected


def test_the_app_ran_like_the_original_when_it_calls_exit_in_a_child(
    mp_tmp: Path, mp_venv: Path, tmp_path: Path
) -> None:
    """RT-1: the child's `-c 'exit(4)'` needs site's builtins, which built apps define."""
    executable = _build("mp", mp_tmp, "--interpreter-reexec")
    env = {"FSBPYR_CACHE_DIR": str(tmp_path / "cache")}

    result = assert_same(mp_venv, executable, ["reexec"], env=env, script="mp")

    assert result.stdout.split()[-1] == "4"


def test_helper_interpreters_are_refused_to_other_callers(mp_tmp: Path, tmp_path: Path) -> None:
    executable = _build("mp", mp_tmp, "--interpreter-reexec")

    result = _run(executable, tmp_path / "cache", "-m", "mp.helper", "x")

    assert "helper" not in result.stdout
    assert "cannot find context for '-m'" in result.stderr  # the app ran, with these args


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


@pytest.fixture(scope="module")
def datafiles_venv(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> Path:
    return _venv("datafiles", tmp_path_factory, runtime)


@pytest.mark.parametrize("check", sorted(DATAFILES))
def test_packages_reading_data_next_to_their_modules(
    datafiles: Path, datafiles_venv: Path, tmp_path: Path, check: str
) -> None:
    env = {"FSBPYR_CACHE_DIR": str(tmp_path / "cache"), "MPLCONFIGDIR": str(tmp_path / "mpl")}

    result = assert_same(datafiles_venv, datafiles, [check], env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() != ""
    assert noise(result.stderr) == ""


def test_the_build_info_traces_every_dist_to_the_lock(compat: Path) -> None:
    """REP-3: the lock's SHA-256, and for each dist but the project the hash of the artifact
    the lock names for it."""
    lock = (FIXTURES / "compat" / "uv.lock").read_bytes()
    with Executable.open(compat) as executable:
        info = json.loads(executable.payload.build_info)

    assert info["lock"]["sha256"] == hashlib.sha256(lock).hexdigest()
    external = [d for d in info["dists"] if d["source"] != "local"]
    assert len(external) == len(info["dists"]) - 1
    assert all(d["artifact_sha256"] in lock.decode() for d in external)
