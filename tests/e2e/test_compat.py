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

from fsbpyr.build import TARGET_PYTHON
from fsbpyr.cli import main
from fsbpyr.payload.reader import Executable
from fsbpyr.runtime.registry import Runtime

from ..support import contain, wheels
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
    "distutils": "distutils True",  # RT-5: imported before setuptools
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
    return contain.run([executable, *args], env=env, timeout=120)


@pytest.mark.parametrize("check", sorted(MATRIX))
def test_compatibility_matrix(
    compat: Path, compat_venv: Path, url: str, tmp_path: Path, check: str
) -> None:
    wheels.skip_without(check, TARGET_PYTHON)  # a check is named after the package it exercises
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

    assert sorted(names) == sorted(MATRIX) and len(MATRIX) == 18


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
    [(("--interpreter-reexec",), ["0 helper ['a']", "0 helper ['c']", "0 code ['-c', 'b']", "4"]),
     ((), ["70", "70", "70", "70"]),  # refused: running the app would start it again (D44)
     (("--no-interpreter-reexec",), ["1", "1", "1", "1"])],  # the app, with those arguments
    ids=["interpreter-reexec", "default", "false"],
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


def test_a_script_started_through_sys_executable_finds_its_sibling_modules(
    mp_tmp: Path, mp_venv: Path, tmp_path: Path
) -> None:
    """A script's directory is sys.path[0], as with `python /path/pkg/a.py` (D23)."""
    executable = _build("mp", mp_tmp, "--interpreter-reexec")
    env = {"FSBPYR_CACHE_DIR": str(tmp_path / "cache")}

    result = assert_same(mp_venv, executable, ["sibling"], env=env, script="mp")

    assert result.stdout.strip() == "0 a sibling"


def _starts(program: Path, tmp: Path) -> dict[str, str]:
    """What ``mp argv0`` prints, started by bare name on PATH, by a relative path, by an
    absolute path, through a symlink and through ``exec -a``; ``program``'s directory is
    ``<dir>``."""
    directory, name = program.parent, program.name
    link = tmp / "links" / "other-name"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(program)
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp / "cache"), "PATH": f"{directory}:/usr/bin"}
    forms: dict[str, tuple[list[str | Path], Path | None]] = {
        "bare": ([name, "argv0"], None),
        "relative": ([f"./{name}", "argv0"], directory),
        "absolute": ([program, "argv0"], None),
        "symlink": ([link, "argv0"], None),
        "exec -a": (["/bin/bash", "-c", 'exec -a renamed "$0" argv0', program], None),
    }
    out = {}
    for form, (command, cwd) in forms.items():
        result = contain.run(command, env=env, cwd=cwd, timeout=120)
        assert result.returncode == 0, result.stderr
        text = result.stdout.strip().replace(str(directory), "<dir>")
        out[form] = text.replace(str(tmp), "<tmp>")
    return out


def test_argv0_is_the_path_execve_got_as_in_a_venv_unless_the_caller_renamed_it(
    mp_tmp: Path, mp_venv: Path, tmp_path: Path
) -> None:
    """A start by bare name, relative path, absolute path or symlink gives `sys.argv[0]` the
    full path of the start; `exec -a` keeps the caller's name; argparse's prog is the
    basename (D14)."""
    executable = _build("mp", mp_tmp)
    original = _starts(mp_venv / "bin" / "mp", tmp_path / "venv")
    built = _starts(executable, tmp_path / "app")

    assert built == {
        "bare": "<dir>/mp mp",
        "relative": "<dir>/mp mp",
        "absolute": "<dir>/mp mp",
        "symlink": "<tmp>/links/other-name other-name",
        "exec -a": "renamed renamed",
    }
    for form in ("bare", "absolute", "symlink"):
        assert original[form] == built[form]
    assert original["relative"] == "./mp mp"  # a venv keeps the relative path; the app absolute
    assert original["exec -a"] == "<dir>/mp mp"  # a shebang script ignores the caller's name


def test_the_restart_idiom_gives_the_child_the_argv_of_the_parent(
    mp_tmp: Path, mp_venv: Path, tmp_path: Path
) -> None:
    """`os.execv(sys.executable, [sys.executable] + sys.argv)`, as gunicorn and Django restart
    themselves: the child must not get the executable's path as an extra argument."""
    executable = _build("mp", mp_tmp)
    env = {"FSBPYR_CACHE_DIR": str(tmp_path / "cache")}

    result = assert_same(mp_venv, executable, ["restart", "x", "--y"], env=env, script="mp")

    assert result.stdout.splitlines() == ["argv ['restart', 'x', '--y']"] * 2


def test_a_missing_module_is_not_blamed_on_site_initialization(
    mp_tmp: Path, tmp_path: Path, runtime: Runtime
) -> None:
    """CPython 3.15 words its hint after `-S`, which built apps always are (D14)."""
    executable = _build("mp", mp_tmp)

    result = _run(executable, tmp_path / "cache", "missing")

    assert result.returncode == 1
    assert "No module named 'mp_surely_not_installed'" in result.stderr
    assert "Site initialization is disabled" not in result.stderr
    assert ("is not in the executable" in result.stderr) is (
        runtime.python_version.startswith("3.15"))  # fmt: skip


def test_an_app_starting_itself_without_end_stops_at_its_limit(
    mp_tmp: Path, tmp_path: Path
) -> None:
    executable = _build("mp", mp_tmp)
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp_path / "cache"), "FSBPYR_MAX_INSTANCES": "4"}

    result = contain.run([executable, "nest"], env=env, timeout=120)

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["0", "0", "0", "70"]  # four copies ran, the fifth exits 70


def test_an_app_starting_itself_through_a_shell_is_held_to_its_limit(
    mp_tmp: Path, tmp_path: Path
) -> None:
    """121 copies in four levels, each waiting for its three, with a shell between each and
    its parent, where the checks of D44 see no parent running the app: only the instance limit
    holds the tree (D45). Without it contain.run would find far more than 64 processes."""
    executable = _build("mp", mp_tmp)
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp_path / "cache"), "FSBPYR_MAX_INSTANCES": "4"}

    result = contain.run([executable, "fan", "4"], env=env, timeout=120)

    assert result.returncode == 70, result.stderr
    assert "fsbpyr: refusing to start: 4 copies of this app are running already" in result.stderr


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
