"""Pipfile.lock as the lock of a build (research UC3): the default section against develop, the
target's markers, hashes, VCS, path and URL entries, other indexes, the stale-lock check, the
Python the lock asks for, the lock's place among the sources, and the tool binary of a Pipfile.
The fixture is in tests/fixtures/projects/foreign/pipenv (README there)."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from fsbpyr.build import lock_warnings
from fsbpyr.closure import marker_environment
from fsbpyr.foreign import ForeignLockError
from fsbpyr.manifest import LockError, lock_record, read_lock
from fsbpyr.pipenv import export, other_indexes, python_of, stale
from fsbpyr.project import LockSource, Project, ProjectError
from fsbpyr.pyversion import resolve
from fsbpyr.staging import StagingOptions, staging_plan
from fsbpyr.tools import listed_distributions

FIXTURE = Path(__file__).parents[2] / "fixtures" / "projects" / "foreign" / "pipenv"
LINUX = marker_environment("3.14.1")
WINDOWS = {**LINUX, "sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"}
PIPFILE_LOCK = LockSource.PIPFILE_LOCK
DEFAULT = ["click", "markdown-it-py", "mdurl", "pygments", "rich"]
CLICK_WHEEL = "255bc9599cf7748b4b1a446ccc735421bd08a2ae529a8b88597d3de5664ee360"
CLICK_SDIST = "ba0d2089de75ea0310e2dde03160e6ca10009947fb95a182f9b54021bb272e34"
DIGEST = "ab" * 32
COMMIT = "0123456789abcdef0123456789abcdef01234567"
PYPI = {"name": "pypi", "url": "https://pypi.org/simple", "verify_ssl": True}


def _copy(tmp_path: Path) -> Path:
    project = tmp_path / "pipenv"
    shutil.copytree(FIXTURE, project)
    return project


def _lock(tmp_path: Path, default: dict[str, Any], **meta: Any) -> Path:
    """A directory holding a Pipfile.lock with the ``default`` section given."""
    directory = tmp_path / "p"
    directory.mkdir(parents=True, exist_ok=True)
    body = {"_meta": {"sources": [PYPI], **meta}, "default": default, "develop": {}}
    (directory / "Pipfile.lock").write_text(json.dumps(body))
    return directory


def _requirement(text: str, **extra: Any) -> dict[str, Any]:
    return {"version": f"=={text}", "hashes": [f"sha256:{DIGEST}"], "index": "pypi", **extra}


def _names(text: str) -> list[str]:
    return [line.split()[0] for line in text.splitlines() if line and line[0].isalpha()]


# --- the default section, markers and hashes -------------------------------------------------


def test_the_default_section_is_exported_without_develop_and_windows_only_packages(
    tmp_path: Path,
) -> None:
    exported = export(_copy(tmp_path), environment=LINUX)

    assert _names(exported.text) == [f"{n}==" + v for n, v in zip(
        DEFAULT, ["8.5.0", "4.2.0", "0.1.2", "2.21.0", "15.0.0"], strict=True)]  # fmt: skip
    assert "iniconfig" not in exported.text  # develop, never
    assert "colorama" not in exported.text  # sys_platform == 'win32'


def test_markers_are_evaluated_for_the_target(tmp_path: Path) -> None:
    exported = export(_copy(tmp_path), environment=WINDOWS)

    assert "colorama==0.4.6" in _names(exported.text)
    assert "iniconfig" not in exported.text


def test_every_hash_of_a_package_is_listed_and_the_file_demands_them(tmp_path: Path) -> None:
    exported = export(_copy(tmp_path), environment=LINUX)

    assert exported.hashed
    assert exported.count == len(DEFAULT)
    assert (f"click==8.5.0 \\\n    --hash=sha256:{CLICK_WHEEL} \\\n"
            f"    --hash=sha256:{CLICK_SDIST}\n") in exported.text  # fmt: skip


def test_a_package_wanted_by_an_extra_stays_in_when_its_marker_names_the_extra(
    tmp_path: Path,
) -> None:
    directory = _lock(tmp_path, {
        "requests": _requirement("2.31.0", extras=["socks"]),
        "pysocks": _requirement("1.7.1", markers="extra == 'socks'"),
        "other": _requirement("1.0", markers="extra == 'nobody'"),
    })

    exported = export(directory, environment=LINUX)

    assert _names(exported.text) == ["pysocks==1.7.1", "requests==2.31.0"]


def test_a_version_that_is_not_pinned_is_refused_naming_the_entry(tmp_path: Path) -> None:
    for spec in ({"hashes": []}, {"version": ">=1"}, {"version": "===1.0"}):
        directory = _lock(tmp_path, {"loose": spec})
        with pytest.raises(ForeignLockError, match=r"Pipfile.lock: loose is not pinned with `==`"):
            export(directory, environment=LINUX)


@pytest.mark.parametrize("digest", ["sha256:abc", "sha256:" + "a" * 63 + "\n--index-url=http://x/"])
def test_a_hash_that_is_not_a_digest_cannot_add_options_to_the_file(
    tmp_path: Path, digest: str
) -> None:
    directory = _lock(tmp_path, {"six": {"version": "==1.16.0", "hashes": [digest]}})

    with pytest.raises(ForeignLockError, match="not 64 hex digits"):
        export(directory, environment=LINUX)


def test_an_entry_without_hashes_is_not_demanded_to_have_them(tmp_path: Path) -> None:
    exported = export(_lock(tmp_path, {"six": {"version": "==1.16.0"}}), environment=LINUX)

    assert not exported.hashed
    assert exported.text.splitlines()[1:] == ["six==1.16.0"]


# --- VCS, path, file and index entries -------------------------------------------------------


def test_a_git_entry_is_kept_pinned_to_its_commit(tmp_path: Path) -> None:
    directory = _lock(tmp_path, {"lib": {"git": "https://example.org/lib.git", "ref": COMMIT,
                                          "subdirectory": "py", "editable": True}})  # fmt: skip

    exported = export(directory, environment=LINUX)

    assert exported.text.splitlines()[1:] == [
        f"lib @ git+https://example.org/lib.git@{COMMIT}#subdirectory=py"]
    assert not exported.hashed


@pytest.mark.parametrize(("ref", "said"), [("main", "is not a commit"), ("", "is missing")])
def test_a_git_entry_without_a_commit_is_refused_naming_the_entry(
    tmp_path: Path, ref: str, said: str
) -> None:
    directory = _lock(tmp_path, {"lib": {"git": "https://example.org/lib.git", "ref": ref}})

    with pytest.raises(ForeignLockError, match=rf"Pipfile.lock: lib comes from .*{said}"):
        export(directory, environment=LINUX)


def test_another_version_control_is_refused_naming_the_entry(tmp_path: Path) -> None:
    directory = _lock(tmp_path, {"lib": {"hg": "https://example.org/lib"}})

    with pytest.raises(ForeignLockError, match=r"Pipfile.lock: lib comes from hg"):
        export(directory, environment=LINUX)


def test_a_path_entry_is_a_regular_install_of_the_directory(tmp_path: Path) -> None:
    directory = _lock(tmp_path, {"local-lib": {"path": "./libs", "editable": True}})
    (directory / "libs").mkdir()

    exported = export(directory, environment=LINUX)

    assert exported.text.splitlines()[1:] == [f"local-lib @ {(directory / 'libs').as_uri()}"]
    assert not exported.hashed


def test_a_missing_path_is_refused_naming_the_entry(tmp_path: Path) -> None:
    directory = _lock(tmp_path, {"gone": {"path": "nowhere"}})

    with pytest.raises(ForeignLockError, match=r"Pipfile.lock: gone is the path nowhere"):
        export(directory, environment=LINUX)


def test_the_project_itself_is_not_installed_as_a_dependency(tmp_path: Path) -> None:
    directory = _lock(tmp_path, {"app": {"path": ".", "editable": True},
                                 "six": _requirement("1.16.0")})  # fmt: skip

    assert _names(export(directory, environment=LINUX).text) == ["six==1.16.0"]


def test_a_file_entry_is_installed_from_its_url_with_its_hash(tmp_path: Path) -> None:
    url = "https://example.org/dist/lib-1.0-py3-none-any.whl"
    directory = _lock(tmp_path, {"lib": {"file": url, "hashes": [f"sha256:{DIGEST}"]}})

    exported = export(directory, environment=LINUX)

    assert exported.text.splitlines()[1:] == [f"lib @ {url}#sha256={DIGEST}"]
    assert exported.hashed


def test_a_local_file_entry_is_installed_from_the_file(tmp_path: Path) -> None:
    directory = _lock(tmp_path, {"lib": {}})
    wheel = directory / "lib-1.0-py3-none-any.whl"
    wheel.write_bytes(b"")
    (directory / "Pipfile.lock").write_text(json.dumps(
        {"_meta": {}, "default": {"lib": {"file": wheel.as_uri()}}}))

    exported = export(directory, environment=LINUX)

    assert exported.text.splitlines()[1:] == [f"lib @ {wheel.as_uri()}"]


def test_packages_of_another_index_are_a_note_and_install_from_the_configured_ones(
    tmp_path: Path,
) -> None:
    sources = [PYPI, {"name": "corp", "url": "https://user:secret@pkgs.corp.example/simple"}]
    directory = _lock(tmp_path, {"six": _requirement("1.16.0"),
                                 "inner": _requirement("2.0", index="corp")})
    body = json.loads((directory / "Pipfile.lock").read_text())
    body["_meta"]["sources"] = sources
    (directory / "Pipfile.lock").write_text(json.dumps(body))

    note = other_indexes(directory)

    assert _names(export(directory, environment=LINUX).text) == ["inner==2.0", "six==1.16.0"]
    assert note is not None
    assert "takes inner from pkgs.corp.example, not PyPI" in note
    assert "secret" not in note and "user" not in note
    assert other_indexes(_copy(tmp_path / "plain")) is None


@pytest.mark.parametrize("text", ["", "[]", "not json", '{"default": []}'])
def test_a_file_that_is_no_pipenv_lock_fails_where_it_is_read(tmp_path: Path, text: str) -> None:
    (tmp_path / "Pipfile.lock").write_text(text)

    with pytest.raises(ForeignLockError, match=r"Pipfile\.lock"):
        export(tmp_path, environment=LINUX)
    if text:
        with pytest.raises(LockError, match=r"cannot read Pipfile\.lock"):
            read_lock(PIPFILE_LOCK, text)
    assert stale(tmp_path) is None  # the build fails with the lock's own message


def test_a_missing_lock_is_a_foreign_lock_error(tmp_path: Path) -> None:
    with pytest.raises(ForeignLockError, match=r"cannot read Pipfile.lock"):
        export(tmp_path, environment=LINUX)


# --- the Pipfile hash ------------------------------------------------------------------------


def test_the_lock_of_the_fixture_matches_its_pipfile(tmp_path: Path) -> None:
    project = _copy(tmp_path)

    assert stale(project) is None
    assert lock_warnings(Project.load(project), PIPFILE_LOCK, None) == []


def test_a_pipfile_changed_after_locking_makes_the_lock_stale(tmp_path: Path) -> None:
    project = _copy(tmp_path)
    pipfile = project / "Pipfile"
    pipfile.write_text(pipfile.read_text().replace('click = ">=8.1"', 'click = ">=8.2"'))

    message = stale(project)

    assert message is not None and message.startswith("Pipfile.lock is out of date")
    assert lock_warnings(Project.load(project), PIPFILE_LOCK, None) == [message]


def test_a_lock_without_a_pipfile_beside_it_has_nothing_to_be_older_than(tmp_path: Path) -> None:
    project = _copy(tmp_path)
    (project / "Pipfile").unlink()

    assert stale(project) is None


# --- the Python the lock asks for ------------------------------------------------------------


@pytest.mark.parametrize(("requires", "minor"), [
    ({"python_version": "3.13"}, "3.13"),
    ({"python_full_version": "3.14.2"}, "3.14"),
    ({"python_version": "3"}, None),
    ({}, None),
])  # fmt: skip
def test_the_requires_table_gives_a_minor_version(
    tmp_path: Path, requires: dict[str, str], minor: str | None
) -> None:
    directory = _lock(tmp_path, {}, requires=requires)

    assert python_of(directory / "Pipfile.lock") == minor


@pytest.mark.version_rule
def test_the_lock_s_python_is_the_build_s_unless_something_else_says(tmp_path: Path) -> None:
    project = Project.load(_copy(tmp_path))  # python_version = "3.12"
    directory = Project.load(_lock(tmp_path / "new", {}, requires={"python_version": "3.13"}))

    assert resolve(project, None, own="3.14").version == "3.12"
    assert resolve(directory, None, own="3.14").version == "3.13"
    assert resolve(directory, "3.15", own="3.14").version == "3.15"  # --python and the settings
    (directory.directory / ".python-version").write_text("3.14\n")
    assert resolve(directory, None, own="3.12").version == "3.14"


@pytest.mark.version_rule
def test_a_python_the_lock_asks_for_and_fsbpyr_cannot_build_is_a_warning(tmp_path: Path) -> None:
    directory = Project.load(_lock(tmp_path, {}, requires={"python_version": "3.8"}))

    choice = resolve(directory, None, own="3.14")

    assert choice.version == "3.14"
    assert len(choice.warnings) == 1 and "Pipfile.lock asks for Python 3.8" in choice.warnings[0]


def test_a_lock_that_is_not_the_lock_source_asks_for_no_python(tmp_path: Path) -> None:
    directory = _lock(tmp_path, {}, requires={"python_version": "3.13"})
    (directory / "requirements.txt").write_text("six==1.16.0\n")

    assert Project.load(directory).lock_python is None


# --- the project, the records and the plan ---------------------------------------------------


def test_the_pipfile_lock_ranks_after_the_other_locks_in_a_project_and_a_directory(
    tmp_path: Path,
) -> None:
    project = _copy(tmp_path)
    assert Project.load(project).lock_source() is PIPFILE_LOCK
    assert Project.load(project).lock_path(PIPFILE_LOCK) == project / "Pipfile.lock"

    (project / "requirements.txt").write_text("click==8.5.0\n")
    assert Project.load(project).lock_source() is LockSource.REQUIREMENTS
    (project / "requirements.txt").unlink()
    (project / "pylock.toml").write_text("")
    assert Project.load(project).lock_source() is LockSource.PYLOCK
    (project / "pylock.toml").unlink()

    (project / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "1"\n')
    (project / "poetry.lock").write_text("")
    assert Project.load(project).lock_source() is LockSource.POETRY_LOCK
    (project / "poetry.lock").unlink()
    (project / "pdm.lock").write_text("")
    assert Project.load(project).lock_source() is LockSource.PDM_LOCK
    (project / "uv.lock").write_text("")
    assert Project.load(project).lock_source() is LockSource.UV_LOCK


def test_a_directory_of_scripts_is_locked_by_its_pipfile_lock_and_poetry_pdm_are_not_read(
    tmp_path: Path,
) -> None:
    project = _copy(tmp_path)
    (project / "poetry.lock").write_text("")

    loaded = Project.load(project)

    assert loaded.bare and loaded.lock_source() is PIPFILE_LOCK


def test_the_lock_record_and_its_packages(tmp_path: Path) -> None:
    project = _copy(tmp_path)

    record, packages = lock_record(project, PIPFILE_LOCK, project / "Pipfile.lock")

    assert (record["kind"], record["path"], record["hashed"]) == ("Pipfile.lock",
                                                                 "Pipfile.lock", True)  # fmt: skip
    click = packages["click"][0]
    assert (click.kind, click.version) == ("registry", "8.5.0")
    assert click.pinned and not click.wheels and click.sdist is None  # the hashes have no names


def test_the_plan_installs_the_converted_file_that_lives_in_the_work_directory(
    tmp_path: Path,
) -> None:
    project = Project.load(_copy(tmp_path))
    before = sorted(path.name for path in project.directory.iterdir())
    work = tmp_path / "work"
    work.mkdir()

    plan = staging_plan(project, PIPFILE_LOCK, Path("/u/uv"), work,
                        StagingOptions("3.14", environment=LINUX, offline=True))  # fmt: skip

    requirements = work / "Pipfile.lock.requirements.txt"
    base = ["/u/uv", "pip", "install", "--python", str(work / "stage" / "bin" / "python"),
            "--offline", "--quiet"]  # fmt: skip
    assert plan.install[1] == [*base, "--require-hashes", "--no-editable", "-r", str(requirements)]
    assert requirements.read_text().startswith("# Pipfile.lock as fsbpyr reads it")
    assert sorted(path.name for path in project.directory.iterdir()) == before


# --- the tool binary -------------------------------------------------------------------------


def _tool_directory(tmp_path: Path, pipfile: str | None) -> Path:
    directory = _lock(tmp_path, {"flake8": _requirement("7.1.1"),
                                 "pyflakes": _requirement("3.2.0"),
                                 "win": _requirement("1", markers="sys_platform == 'win32'")})
    if pipfile is not None:
        (directory / "Pipfile").write_text(pipfile)
    return directory


def test_a_directory_with_only_a_pipfile_lock_is_a_tool_binary(tmp_path: Path) -> None:
    project = Project.load(_tool_directory(tmp_path, None))

    assert project.tools and project.lock_source() is PIPFILE_LOCK
    listed = listed_distributions(project, PIPFILE_LOCK, None)
    assert [(item.name, item.marker is None) for item in listed] == [
        ("flake8", True), ("pyflakes", True), ("win", False)]  # a lock names the dependencies too


def test_the_pipfile_s_packages_are_the_tools_with_their_extras_and_markers(
    tmp_path: Path,
) -> None:
    pipfile = ('[packages]\nflake8 = "*"\nWin_Tool = {version = "*", extras = ["Fast"],'
               ' markers = "sys_platform == \'win32\'"}\n')
    project = Project.load(_tool_directory(tmp_path, pipfile))

    listed = listed_distributions(project, PIPFILE_LOCK, None)

    assert [(item.name, sorted(item.extras), item.marker is None) for item in listed] == [
        ("flake8", [], True), ("win-tool", ["fast"], False)]


def test_a_directory_with_python_files_keeps_them_as_the_commands(tmp_path: Path) -> None:
    directory = _tool_directory(tmp_path, None)
    (directory / "run.py").write_text("if __name__ == '__main__':\n    pass\n")

    project = Project.load(directory)

    assert not project.tools and set(project.scripts) == {"run"}
    assert project.lock_source() is PIPFILE_LOCK


def test_a_directory_with_neither_python_files_nor_a_lock_source_is_still_an_error(
    tmp_path: Path,
) -> None:
    (tmp_path / "Pipfile").write_text('[packages]\nflake8 = "*"\n')  # the Pipfile alone: no lock

    with pytest.raises(ProjectError):
        Project.load(tmp_path)
