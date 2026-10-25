"""The files next to a script (research UC6, UC8, owner decision 2026-10-25): a script's
``__file__`` is next to the executable, so what it reads there is the host's file, while its code
is the payload's. Needs the cached runtime; no dependency is installed."""

import io
import os
import subprocess
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from fsbpyr.cli import main
from fsbpyr.runtime.registry import Runtime

from ..support import contain
from .conftest import BuildApp

pytestmark = pytest.mark.slow

OPS = {
    "show.py": "import os\nimport sys\n\nHERE = os.path.dirname(__file__)\n"
    "sys.path.append(HERE + '/pylib')\nimport plug\nimport stale\n\n"
    "if __name__ == '__main__':\n"
    "    exe = os.path.dirname(os.path.realpath(sys.executable))\n"
    "    own = __file__ == os.path.join(HERE, 'show.py')\n"
    "    print('beside', os.path.realpath(HERE) == exe, own)\n"
    "    print('config', open(os.path.join(HERE, 'config.ini')).read().strip())\n"
    "    print('plug', plug.WORD, plug.__file__ == HERE + '/pylib/plug.py')\n"
    "    print('stale', stale.WORD)\n",
    "boom.py": "def explode():\n    raise RuntimeError('boom-marker')\n\n"
    "if __name__ == '__main__':\n    explode()\n",
    "prog.py": "import argparse\n\nif __name__ == '__main__':\n"
    "    print('prog', argparse.ArgumentParser().prog)\n",
    "workers.py": "import multiprocessing\nimport sys\n\ndef square(n):\n    return n * n\n\n"
    "if __name__ == '__main__':\n    multiprocessing.set_start_method(sys.argv[1])\n"
    "    with multiprocessing.Pool(2) as pool:\n"
    "        print('squares', pool.map(square, range(4)))\n",
    "src.py": "import inspect\nimport sys\n\ndef where():\n    return 'payload-marker'\n\n"
    "if __name__ == '__main__':\n"
    "    print(inspect.getsource(where).splitlines()[1].strip())\n"
    "    print(inspect.getsource(sys.modules['__main__']).splitlines()[0])\n",
    "again.py": "import subprocess\nimport sys\n\nif __name__ == '__main__':\n"
    "    if sys.argv[1:] == ['child']:\n        print('child ran')\n"
    "    else:\n        print(subprocess.run([sys.executable, __file__, 'child'], check=True,\n"
    "                             capture_output=True, text=True).stdout.strip())\n",
    "stale.py": "WORD = 'payload'\n",
    "pylib/plug.py": "WORD = 'from the payload'\n",
    "config.ini": "the build tree's\n",
}


def _write(root: Path, files: Mapping[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _run(executable: Path, tmp: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp / "cache")}
    return contain.run([executable, *args], env=env, timeout=120)


@pytest.fixture(scope="module")
def ops(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> Path:
    """The ops directory built; its ``dist`` holds the host's files, a stale copy of a script
    module and a ``prog.py`` link, beside the executable."""
    directory = _write(tmp_path_factory.mktemp("overlay-case") / "ops", OPS)
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        assert main(["build", str(directory)]) == 0, err.getvalue()
    dist = directory / "dist"
    _write(dist, {"config.ini": "the host's\n",
                  "stale.py": "WORD = 'stale on the disk'\n",
                  "boom.py": "raise SystemExit('the stale boom.py ran')\n"})  # fmt: skip
    (dist / "prog.py").symlink_to("ops")
    return dist


def test_a_script_reads_the_file_deployed_beside_the_executable(ops: Path, tmp_path: Path) -> None:
    result = _run(ops / "show", tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == ["beside True True", "config the host's"]


def test_the_pylib_hack_imports_from_the_payload(ops: Path, tmp_path: Path) -> None:
    result = _run(ops / "show", tmp_path)

    assert "plug from the payload True" in result.stdout.splitlines()


def test_a_stale_copy_beside_the_executable_is_not_imported(ops: Path, tmp_path: Path) -> None:
    assert (ops / "stale.py").is_file()

    result = _run(ops / "show", tmp_path)

    assert "stale payload" in result.stdout.splitlines()


def test_a_traceback_shows_the_payload_source(ops: Path, tmp_path: Path) -> None:
    result = _run(ops / "boom", tmp_path)

    assert result.returncode == 1 and "stale boom.py ran" not in result.stderr
    assert f'File "{ops / "ops"}/_fsbpyr_scripts/boom.py", line 2, in explode' in result.stderr
    assert "raise RuntimeError('boom-marker')" in result.stderr


@pytest.mark.parametrize("name", ["prog", "prog.py"])
def test_argparse_prog_is_the_name_the_command_was_started_by(
    ops: Path, tmp_path: Path, name: str
) -> None:
    result = _run(ops / name, tmp_path)

    assert result.stdout == f"prog {name}\n", result.stderr


@pytest.mark.parametrize("method", ["spawn", "forkserver"])
def test_multiprocessing_from_a_script_works(ops: Path, tmp_path: Path, method: str) -> None:
    result = _run(ops / "workers", tmp_path, method)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "squares [0, 1, 4, 9]\n"


def test_inspect_getsource_reads_the_payload_when_no_copy_is_beside_the_executable(
    ops: Path, tmp_path: Path
) -> None:
    result = _run(ops / "src", tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["return 'payload-marker'", "import inspect"]


def test_inspect_getsource_shows_a_copy_deployed_beside_the_executable(
    ops: Path, tmp_path: Path
) -> None:
    (ops / "src.py").write_text("# the host's copy\n" * 5)
    try:
        result = _run(ops / "src", tmp_path)
    finally:
        (ops / "src.py").unlink()

    assert result.stdout.splitlines()[1] == "# the host's copy"  # linecache reads the disk first


def test_a_script_that_restarts_itself_through_its_file_runs_again(
    ops: Path, tmp_path: Path
) -> None:
    plain = _run(ops / "again", tmp_path)
    (ops / "again.py").write_text("raise SystemExit('the stale again.py ran')\n")
    try:
        beside = _run(ops / "again", tmp_path)
    finally:
        (ops / "again.py").unlink()

    assert plain.stdout == beside.stdout == "child ran\n", (plain.stderr, beside.stderr)


def test_a_missing_file_is_named_by_the_error_note(ops: Path, tmp_path: Path) -> None:
    (ops / "config.ini").rename(ops / "config.moved")
    try:
        result = _run(ops / "show", tmp_path)
    finally:
        (ops / "config.moved").rename(ops / "config.ini")

    assert result.returncode == 1
    assert f"fsbpyr: {ops}/config.ini is missing" in result.stderr


def test_a_single_script_reads_the_file_beside_its_executable(
    tmp_path: Path, runtime: Runtime
) -> None:
    _write(tmp_path / "src", {
        "report.py": "import os\n\n"
        "print(open(os.path.join(os.path.dirname(__file__), 'x.ini')).read(), end='')\n",
        "x.ini": "the build tree's\n",
    })  # fmt: skip
    script = tmp_path / "src" / "report.py"
    out = tmp_path / "out" / "report"
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        assert main(["build", str(script), "-o", str(out)]) == 0, err.getvalue()
    (tmp_path / "out" / "x.ini").write_text("the host's\n")

    assert _run(out, tmp_path).stdout == "the host's\n"


def test_a_packaged_project_keeps_its_file_inside_the_executable(
    build_app: BuildApp, tmp_path: Path
) -> None:
    app = build_app({"pkg/__init__.py": "", "pkg/mod.py": "def main():\n    print(__file__)\n"},
                    "pkg.mod:main")  # fmt: skip

    result = _run(app, tmp_path)

    assert result.stdout == f"{app}/pkg/mod.py\n", result.stderr
