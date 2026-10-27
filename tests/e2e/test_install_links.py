"""FSBPYR_INSTALL_LINKS=DIR on a built executable (research UC4/UC6/UC7): links to its real
path, ``name.py`` aliases for script commands, idempotence, what is in the way, and the app not
running. Needs the cached runtime."""

import os
import subprocess
from pathlib import Path

import pytest

from fsbpyr.cli import main
from fsbpyr.runtime.registry import Runtime

from ..support import contain

pytestmark = pytest.mark.slow

SCRIPTS = {
    "check-disk.py": "if __name__ == '__main__':\n    print('disk ok')\n",
    "report.py": "if __name__ == '__main__':\n    print('report ok')\n",
}


def _write(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True)
    for name, text in files.items():
        (root / name).write_text(text)
    return root


def _run(executable: Path, tmp: Path, directory: Path | str | None = None) -> (
    subprocess.CompletedProcess[str]
):
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp / "cache")}
    if directory is not None:
        env["FSBPYR_INSTALL_LINKS"] = str(directory)
    return contain.run([executable], env=env, timeout=120)


@pytest.fixture(scope="module")
def tools(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> Path:
    """A directory of two scripts built: its executable."""
    project = _write(tmp_path_factory.mktemp("links") / "tools", SCRIPTS)
    assert main(["build", str(project)]) == 0
    return (project / "dist" / "tools").resolve()


def test_links_name_the_real_path_with_py_aliases_and_each_runs_its_command(
    tools: Path, tmp_path: Path
) -> None:
    target = tmp_path / "bin"
    target.mkdir()

    result = _run(tools, tmp_path, target)

    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in target.iterdir()) == [
        "check-disk", "check-disk.py", "report", "report.py"]  # fmt: skip
    for path in target.iterdir():
        assert path.is_symlink() and os.readlink(path) == str(tools)
        assert f"linked {path} -> {tools}" in result.stdout
    for name, word in [("check-disk", "disk"), ("check-disk.py", "disk"), ("report.py", "report")]:
        ran = _run(target / name, tmp_path)
        assert (ran.returncode, ran.stdout) == (0, f"{word} ok\n"), ran.stderr


def test_the_app_does_not_run_and_a_second_run_changes_nothing(
    tools: Path, tmp_path: Path
) -> None:
    target = tmp_path / "bin"
    target.mkdir()
    first = _run(tools, tmp_path, target)
    before = {p.name: p.lstat().st_mtime_ns for p in target.iterdir()}

    again = _run(tools, tmp_path, target)

    assert "ok" not in first.stdout and "ok" not in again.stdout
    assert again.returncode == 0 and again.stdout.count("present ") == 4
    assert {p.name: p.lstat().st_mtime_ns for p in target.iterdir()} == before


def test_what_is_in_the_way_is_reported_and_kept_and_the_rest_is_linked(
    tools: Path, tmp_path: Path
) -> None:
    target = tmp_path / "bin"
    target.mkdir()
    (target / "check-disk").write_text("mine")
    (target / "report").symlink_to("/bin/true")
    (target / "report.py").mkdir()

    result = _run(tools, tmp_path, target)

    assert result.returncode == 1
    assert (target / "check-disk").read_text() == "mine"
    assert os.readlink(target / "report") == "/bin/true" and (target / "report.py").is_dir()
    assert os.readlink(target / "check-disk.py") == str(tools)
    for name in ("check-disk", "report", "report.py"):
        assert f"{target / name} exists and is not a link to {tools}" in result.stderr


def test_the_background_collection_child_makes_no_links(tools: Path, tmp_path: Path) -> None:
    target = tmp_path / "bin"
    target.mkdir()
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp_path / "cache"),
           "FSBPYR_INSTALL_LINKS": str(target), "FSBPYR_GC": "background"}  # fmt: skip

    result = contain.run([tools], env=env, timeout=120)

    assert result.returncode == 0, result.stderr
    assert list(target.iterdir()) == []


def test_the_executable_in_its_own_directory_is_not_in_the_way(
    tools: Path, tmp_path: Path
) -> None:
    result = _run(tools, tmp_path, tools.parent)

    assert result.returncode == 0, result.stderr
    assert tools.is_file() and not tools.is_symlink()


def test_a_missing_directory_is_an_error_before_any_link(tools: Path, tmp_path: Path) -> None:
    result = _run(tools, tmp_path, tmp_path / "absent")

    assert result.returncode == 1 and "is not a directory" in result.stderr
    assert not (tmp_path / "absent").exists()


def test_a_directory_without_write_permission_is_an_error(tools: Path, tmp_path: Path) -> None:
    target = tmp_path / "bin"
    target.mkdir(mode=0o500)

    result = _run(tools, tmp_path, target)

    if os.geteuid() == 0:  # root writes to it all the same
        assert result.returncode == 0, result.stderr
        assert len(list(target.iterdir())) == 4
        return
    assert result.returncode == 1 and f"cannot write to {target}" in result.stderr
    assert not list(target.iterdir())


def test_a_single_script_gets_its_name_and_py_alias(
    runtime: Runtime, tmp_path: Path
) -> None:
    script = tmp_path / "solo.py"
    script.write_text("print('solo ok')\n")
    assert main(["build", str(script), "-o", str(tmp_path / "out" / "solo")]) == 0
    target = tmp_path / "bin"
    target.mkdir()

    result = _run(tmp_path / "out" / "solo", tmp_path, target)

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in target.iterdir()) == ["solo", "solo.py"]
    assert _run(target / "solo.py", tmp_path).stdout == "solo ok\n"


def test_an_extensionless_script_also_gets_a_py_alias(
    runtime: Runtime, tmp_path: Path
) -> None:
    guarded = "if __name__ == '__main__':\n    print('ok')\n"
    files = {"backup": "#!/usr/bin/env python3\n" + guarded, "report.py": guarded}
    project = _write(tmp_path / "ops", files)
    assert main(["build", str(project)]) == 0
    target = tmp_path / "bin"
    target.mkdir()

    result = _run((project / "dist" / "ops").resolve(), tmp_path, target)

    assert result.returncode == 0, result.stderr
    names = sorted(p.name for p in target.iterdir())
    assert names == ["backup", "backup.py", "report", "report.py"]  # the over-approximation
