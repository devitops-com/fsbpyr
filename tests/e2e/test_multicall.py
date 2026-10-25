"""Several console scripts in one executable (fixture ``multi``): the links, the dispatcher
form, a renamed copy, the smoke test, the helper-interpreter rules and multiprocessing from a
link-named command. Needs the cached runtime."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from fsbpyr.cli import main
from fsbpyr.runtime.registry import Runtime

from ..support import contain

pytestmark = pytest.mark.slow

FIXTURES = Path(__file__).parents[1] / "fixtures" / "projects"
FIXTURE = FIXTURES / "multi"
CONTRACT = ["multi-alpha", "multi-beta"]


def _run(executable: Path, tmp: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp / "cache")}
    return contain.run([executable, *args], env=env, timeout=120)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> Path:
    """The project built to ``dist/``: the binary ``multi`` and its two links."""
    project = tmp_path_factory.mktemp("multi") / "multi"
    shutil.copytree(FIXTURE, project)
    assert main(["build", str(project)]) == 0
    return project / "dist"


@pytest.fixture(scope="module")
def reexec(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> Path:
    project = tmp_path_factory.mktemp("multi-reexec") / "multi"
    shutil.copytree(FIXTURE, project)
    assert main(["build", str(project), "--interpreter-reexec"]) == 0
    return project / "dist"


def test_the_build_writes_the_binary_and_a_relative_link_per_command(
    built: Path,
) -> None:
    assert sorted(path.name for path in built.iterdir()) == ["multi", *CONTRACT]
    assert not (built / "multi").is_symlink()
    assert [os.readlink(built / name) for name in CONTRACT] == ["multi", "multi"]


def test_a_single_command_build_records_a_plain_entrypoint_and_no_table_or_links(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "hello"
    shutil.copytree(FIXTURES / "hello", project)

    assert main(["build", str(project)]) == 0

    out = capsys.readouterr().out
    assert [path.name for path in (project / "dist").iterdir()] == ["hello"]
    assert "linked" not in out and "commands" not in out
    assert main(["inspect", str(project / "dist" / "hello"), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert "\n" not in document["entry"] and "commands" not in document


def test_the_summary_lists_the_commands_and_links(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "multi"
    shutil.copytree(FIXTURE, project)

    assert main(["build", str(project)]) == 0

    out = capsys.readouterr().out
    assert "2 commands" in out and "commands: multi-alpha, multi-beta" in out
    assert f"linked {project / 'dist' / 'multi-alpha'} -> multi" in out


def test_inspect_shows_the_commands(built: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inspect", str(built / "multi"), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["commands"] == {"multi-alpha": "multi.cli:alpha",
                                    "multi-beta": "multi.cli:beta"}  # fmt: skip

    assert main(["inspect", str(built / "multi")]) == 0
    text = capsys.readouterr().out
    assert "commands   multi-alpha = multi.cli:alpha" in text
    assert "           multi-beta = multi.cli:beta" in text
    assert "entry " not in text


@pytest.mark.parametrize("name", CONTRACT)
def test_a_link_runs_its_command_with_the_arguments(built: Path, tmp_path: Path, name: str) -> None:
    result = _run(built / name, tmp_path, "x", "--y")

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{name.removeprefix('multi-')} ['{name}', 'x', '--y']\n"


def test_the_dispatcher_form_shifts_argv(built: Path, tmp_path: Path) -> None:
    result = _run(built / "multi", tmp_path, "multi-beta", "x", "--y")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "beta ['multi-beta', 'x', '--y']\n"


@pytest.mark.parametrize("args", [(), ("nope",), ("/x/multi-alpha",)])
def test_without_a_command_the_binary_lists_them_and_exits_2(
    built: Path, tmp_path: Path, args: tuple[str, ...]
) -> None:
    result = _run(built / "multi", tmp_path, *args)

    assert result.returncode == 2 and result.stdout == ""
    assert result.stderr.startswith(("usage: multi COMMAND", "multi: unknown command"))
    assert "\n  multi-alpha\n  multi-beta\n" in result.stderr


def test_a_renamed_copy_takes_the_dispatcher_form_only(built: Path, tmp_path: Path) -> None:
    copy = tmp_path / "somewhere" / "renamed"
    copy.parent.mkdir()
    shutil.copy2(built / "multi", copy)

    named = _run(copy, tmp_path, "multi-alpha", "q")
    bare = _run(copy, tmp_path)

    assert named.returncode == 0 and named.stdout == "alpha ['multi-alpha', 'q']\n", named.stderr
    assert bare.returncode == 2 and "usage: renamed COMMAND" in bare.stderr


def test_the_smoke_test_passes_under_its_temporary_name_and_smoke_args_use_the_dispatcher_form(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "multi"
    shutil.copytree(FIXTURE, project)
    out = tmp_path / "bin" / "tool"  # a name that is no command

    assert main(["build", str(project), "-o", str(out), "--smoke-args", "multi-beta hi"]) == 0
    assert sorted(path.name for path in out.parent.iterdir()) == [
        "multi-alpha",
        "multi-beta",
        "tool",
    ]
    assert os.readlink(out.parent / "multi-beta") == "tool"

    assert main(["build", str(project), "-o", str(out), "--smoke-args", "nope"]) == 1
    assert "smoke run" in capsys.readouterr().err


def test_a_file_in_the_way_of_a_link_fails_the_build_and_writes_nothing(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "multi"
    shutil.copytree(FIXTURE, project)
    (project / "dist").mkdir()
    (project / "dist" / "multi-beta").write_text("mine")
    (project / "dist" / "multi-alpha").symlink_to("elsewhere")

    assert main(["build", str(project)]) == 1

    assert "multi-beta" in capsys.readouterr().err
    assert (project / "dist" / "multi-beta").read_text() == "mine"
    assert not (project / "dist" / "multi").exists()
    assert os.readlink(project / "dist" / "multi-alpha") == "elsewhere"  # not touched either


def test_an_existing_symlink_is_replaced(tmp_path: Path, runtime: Runtime) -> None:
    project = tmp_path / "multi"
    shutil.copytree(FIXTURE, project)
    (project / "dist").mkdir()
    (project / "dist" / "multi-alpha").symlink_to("elsewhere")

    assert main(["build", str(project)]) == 0

    assert os.readlink(project / "dist" / "multi-alpha") == "multi"


def test_a_command_starting_a_sibling_link_with_dash_m_runs_that_command(
    built: Path, tmp_path: Path
) -> None:
    result = _run(built / "multi-alpha", tmp_path, "sibling", "-m", "word")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0 beta ['multi-beta', '-m', 'word']"


def test_a_sibling_link_with_dash_c_is_not_run_as_python_with_interpreter_reexec(
    reexec: Path, tmp_path: Path
) -> None:
    result = _run(reexec / "multi-alpha", tmp_path, "sibling", "-c", "print('python ran')")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0 beta ['multi-beta', '-c', \"print('python ran')\"]"


def test_sys_executable_as_a_helper_interpreter_is_still_refused(
    built: Path, tmp_path: Path
) -> None:
    result = _run(built / "multi-alpha", tmp_path, "self")

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("70 fsbpyr: refusing to run `-m x` as Python")


@pytest.mark.parametrize("entry", ["link", "dispatcher"])
def test_every_start_method_works_from_a_command(built: Path, tmp_path: Path, entry: str) -> None:
    start = [built / "multi-beta"] if entry == "link" else [built / "multi", "multi-beta"]
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp_path / "cache")}

    result = contain.run([*start, "mp", "spawn", "forkserver", "fork"], env=env, timeout=120)

    assert result.returncode == 0, result.stderr
    expected = [line.format(method=m) for m in ("spawn", "forkserver", "fork") for line in (
        "{method} pool [0, 1, 4, 9]", "{method} executor [0, 1, 4]")]  # fmt: skip
    assert result.stdout.splitlines() == expected


def test_a_hard_link_is_a_start_under_its_own_name_that_keeps_the_old_build(
    tmp_path: Path, runtime: Runtime
) -> None:
    """A hard link is a file of its own: argv[0] and sys.executable are its path, so a command
    looks for its siblings beside the link, and a start through sys.executable is refused as
    for any other command (D44); a rebuild replaces the binary, not the link."""
    project = tmp_path / "multi"
    shutil.copytree(FIXTURE, project)
    assert main(["build", str(project)]) == 0
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    link, sibling = bin_dir / "multi-alpha", bin_dir / "multi-beta"
    os.link(project / "dist" / "multi", link)
    sibling.write_text("#!/bin/sh\necho SCRIPT\n")
    sibling.chmod(0o755)

    beside = _run(link, tmp_path, "sibling", "a")
    refused = _run(link, tmp_path, "self")

    assert beside.returncode == 0, beside.stderr
    assert beside.stdout.startswith("0 SCRIPT")  # sys.executable is the link, not dist/multi
    assert refused.stdout.startswith("70 fsbpyr: refusing to run `-m x` as Python")
    assert main(["build", str(project)]) == 0
    assert link.stat().st_ino != (project / "dist" / "multi").stat().st_ino  # the old build
