"""Project discovery, build configuration and staging commands (plan §3.6 steps 1-3)."""

from pathlib import Path

import pytest

from fsbpyr.config import BuildConfig, ConfigError, load_config
from fsbpyr.project import LockSource, Project, ProjectError
from fsbpyr.staging import (
    Installer,
    StagingError,
    StagingOptions,
    StagingPlan,
    find_interpreter,
    run_plan,
    staging_plan,
)


def _project(tmp_path: Path, pyproject: str, **files: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(pyproject)
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return tmp_path


PYPROJECT = """\
[project]
name = "Hello_App"
version = "1.0"
requires-python = ">=3.12"
dependencies = ["click>=8"]

[project.scripts]
hello = "hello_app.cli:main"

[tool.fsbpyr]
extras = ["fancy"]
include-source = false
"""


# --- project ---------------------------------------------------------------------------------


def test_project_reads_pyproject(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT))

    assert project.name == "hello-app"  # canonical (PEP 503)
    assert project.requires_python == ">=3.12"
    assert project.scripts == {"hello": "hello_app.cli:main"}
    assert project.tool == {"extras": ["fancy"], "include-source": False}


@pytest.mark.parametrize(
    ("files", "requirements", "expected"),
    [
        ({"uv.lock": ""}, None, LockSource.UV_LOCK),
        ({"uv.lock": "", "pylock.toml": ""}, None, LockSource.UV_LOCK),
        ({"pylock.toml": ""}, None, LockSource.PYLOCK),
        ({"requirements.txt": "click==8.1.8\n"}, None, LockSource.REQUIREMENTS),
        ({"reqs/prod.txt": "click==8.1.8\n"}, "reqs/prod.txt", LockSource.REQUIREMENTS),
        ({}, None, LockSource.UNLOCKED),
    ],
)
def test_lock_source_priority(
    tmp_path: Path, files: dict[str, str], requirements: str | None, expected: LockSource
) -> None:
    (tmp_path / "reqs").mkdir()
    project = Project.load(_project(tmp_path, PYPROJECT, **{}))
    for name, text in files.items():
        (tmp_path / name).write_text(text)

    assert project.lock_source(requirements) == expected


def test_explicit_requirements_file_wins_over_locks(tmp_path: Path) -> None:
    files = {"uv.lock": "", "pylock.toml": "", "other.txt": "click==8\n"}
    project = Project.load(_project(tmp_path, PYPROJECT, **files))

    assert project.lock_source("other.txt") is LockSource.REQUIREMENTS


def test_explicit_requirements_file_must_exist(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT))

    with pytest.raises(ProjectError, match=r"missing\.txt"):
        project.lock_source("missing.txt")


def test_setup_py_projects_are_accepted(tmp_path: Path) -> None:
    (tmp_path / "setup.py").write_text("from setuptools import setup\nsetup(name='legacy')\n")

    project = Project.load(tmp_path)

    assert (project.name, project.scripts, project.requires_python) == (None, {}, None)


def test_directory_without_project_files_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match=r"pyproject\.toml or setup\.py"):
        Project.load(tmp_path)


def test_python_version_file_disagreement_is_reported(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT, **{".python-version": "3.12\n"}))

    assert project.python_version_mismatch("3.14") == "3.12"
    assert project.python_version_mismatch("3.12") is None


def test_requires_python_is_checked(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT.replace(">=3.12", "<3.14")))

    with pytest.raises(ProjectError, match="requires-python"):
        project.check_python("3.14.8")


# --- config ----------------------------------------------------------------------------------


def test_cli_overrides_tool_table(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT))

    config = load_config(project, entry="hello", include_source=True, extras=("x",))

    assert config == BuildConfig(entry="hello", extras=("x",), include_source=True)


def test_tool_table_supplies_defaults(tmp_path: Path) -> None:
    config = load_config(Project.load(_project(tmp_path, PYPROJECT)))

    assert config.extras == ("fancy",)
    assert config.include_source is False
    assert config.output is None and config.smoke is True


def test_unknown_or_later_phase_keys_are_rejected(tmp_path: Path) -> None:
    text = PYPROJECT + 'profile = "compact"\n'

    with pytest.raises(ConfigError, match="profile"):
        load_config(Project.load(_project(tmp_path, text)))


# --- staging ---------------------------------------------------------------------------------

UV = Installer(uv=Path("/u/uv"), python=None)
PIP = Installer(uv=None, python=Path("/p/python3.14"))
UV_BUILD = [
    "/u/uv",
    "build",
    "--out-dir",
    "{work}/wheel",
    "--python",
    "3.14",
    "--quiet",
    "{project}",
]
PIP_BASE = ["/p/python3.14", "-m", "pip"]
PIP_FLAGS = ["--quiet", "--disable-pip-version-check"]
PIP_BUILD = [*PIP_BASE, "wheel", "--no-deps", "--wheel-dir", "{work}/wheel", *PIP_FLAGS,
             "{work}/src"]  # fmt: skip


def _expand(commands: list[list[str]], work: Path, project: Path) -> list[list[str]]:
    return [[p.format(work=work, project=project, wheel="{wheel}") for p in c] for c in commands]


def test_uv_lock_syncs_dependencies_then_installs_the_fresh_wheel(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT, **{"uv.lock": ""}))
    work = tmp_path / "work"

    options = StagingOptions("3.14", ("fancy",), offline=True)
    plan = staging_plan(project, LockSource.UV_LOCK, UV, work, options)

    stage = work / "stage"
    assert plan.site == stage / "lib" / "python3.14" / "site-packages"
    assert plan.env == {"UV_PROJECT_ENVIRONMENT": str(stage)}
    assert plan.copy is None
    assert plan.build == ["/u/uv", "build", "--out-dir", str(work / "wheel"), "--python", "3.14",
                          "--offline", "--quiet", str(tmp_path)]  # fmt: skip
    assert plan.install == [
        ["/u/uv", "sync", "--locked", "--no-dev", "--no-default-groups", "--no-editable",
         "--no-install-project", "--python", "3.14", "--project", str(tmp_path),
         "--extra", "fancy", "--offline", "--quiet"],
        ["/u/uv", "pip", "install", "--python", str(stage / "bin" / "python"), "--no-deps",
         "--offline", "--quiet", "{wheel}"],
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("source", "installer", "build", "install"),
    [
        (LockSource.PYLOCK, UV, UV_BUILD, [
            ["/u/uv", "pip", "install", "--target", "{work}/stage", "--python", "3.14",
             "--quiet", "-r", "{project}/pylock.toml"],
            ["/u/uv", "pip", "install", "--target", "{work}/stage", "--python", "3.14",
             "--quiet", "--no-deps", "{wheel}"],
        ]),
        (LockSource.REQUIREMENTS, PIP, PIP_BUILD, [
            [*PIP_BASE, "install", "--target", "{work}/stage", *PIP_FLAGS, "--require-hashes",
             "-r", "{project}/requirements.txt"],
            [*PIP_BASE, "install", "--target", "{work}/stage", *PIP_FLAGS, "--no-deps",
             "{wheel}"],
        ]),
        (LockSource.UNLOCKED, PIP, PIP_BUILD, [
            [*PIP_BASE, "install", "--target", "{work}/stage", *PIP_FLAGS, "{wheel}"],
        ]),
    ],
)  # fmt: skip
def test_target_installs(
    tmp_path: Path,
    source: LockSource,
    installer: Installer,
    build: list[str],
    install: list[list[str]],
) -> None:
    files = {"requirements.txt": "click==8.1.8 --hash=sha256:abc\n", "pylock.toml": ""}
    project = Project.load(_project(tmp_path, PYPROJECT, **files))
    work = tmp_path / "work"

    plan = staging_plan(project, source, installer, work, StagingOptions("3.14"))

    assert plan.site == work / "stage" and plan.env == {}
    assert plan.build == _expand([build], work, tmp_path)[0]
    assert plan.install == _expand(install, work, tmp_path)
    assert plan.copy == (work / "src" if installer is PIP else None)


def test_unlocked_install_keeps_extras_and_offline_pip_uses_no_index(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT))

    options = StagingOptions("3.14", ("a", "b"), offline=True)
    plan = staging_plan(project, LockSource.UNLOCKED, PIP, tmp_path / "w", options)

    assert plan.install[0][-1] == "{wheel}[a,b]"
    assert "--no-index" in plan.build and "--no-index" in plan.install[0]


def test_requirements_without_hashes_do_not_require_them(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT, **{"requirements.txt": "click==8\n"}))

    plan = staging_plan(
        project, LockSource.REQUIREMENTS, PIP, tmp_path / "w", StagingOptions("3.14")
    )

    assert "--require-hashes" not in plan.install[0]


def test_uv_lock_and_pylock_need_uv(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT))

    for source in (LockSource.UV_LOCK, LockSource.PYLOCK):
        with pytest.raises(StagingError, match="uv"):
            staging_plan(project, source, PIP, tmp_path / "w", StagingOptions("3.14"))


WORKSPACE_LOCK = """\
version = 1
[[package]]
name = "ws"
version = "0.1.0"
source = { virtual = "." }
[[package]]
name = "member"
version = "1.0"
source = { editable = "packages/member" }
[[package]]
name = "shared-lib"
version = "1.0"
source = { editable = "packages/shared" }
[[package]]
name = "vendored"
version = "2.0"
source = { directory = "../vendored" }
[[package]]
name = "click"
version = "8.1.8"
source = { registry = "https://pypi.org/simple" }
"""


def _workspace(root: Path) -> Path:
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "ws"\nversion = "0.1.0"\n'
        '[tool.uv.workspace]\nmembers = ["packages/*"]\nexclude = ["packages/skip"]\n'
    )
    (root / "uv.lock").write_text(WORKSPACE_LOCK)
    for name in ("member", "shared", "skip"):
        directory = root / "packages" / name
        directory.mkdir(parents=True)
        (directory / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = "1"\n')
    return root


def test_workspace_member_uses_the_workspace_lock(tmp_path: Path) -> None:
    root = _workspace(tmp_path / "ws")
    project = Project.load(root / "packages" / "member")

    assert project.uv_root == root
    assert project.lock_source() is LockSource.UV_LOCK
    plan = staging_plan(project, LockSource.UV_LOCK, UV, tmp_path / "w", StagingOptions("3.14"))

    sync = plan.install[0]
    assert sync[sync.index("--project") + 1] == str(root)
    assert sync[sync.index("--package") + 1] == "member"
    reinstalls = [sync[i + 1] for i, arg in enumerate(sync) if arg == "--reinstall-package"]
    assert reinstalls == ["shared-lib", "vendored"]  # local sources rebuilt, never the registry


def test_excluded_workspace_directory_is_not_a_member(tmp_path: Path) -> None:
    root = _workspace(tmp_path / "ws")

    assert Project.load(root / "packages" / "skip").uv_root is None


def test_run_plan_copies_builds_and_installs_in_the_project_directory(tmp_path: Path) -> None:
    (tmp_path / "proj").mkdir()
    project_dir = _project(tmp_path / "proj", PYPROJECT)
    for junk in ("build/lib/old.py", "dist/x.whl", "pkg.egg-info/PKG-INFO", ".venv/bin/python",
                 "src/pkg/__pycache__/m.cpython-314.pyc"):  # fmt: skip
        (project_dir / junk).parent.mkdir(parents=True, exist_ok=True)
        (project_dir / junk).write_text("junk")
    (project_dir / "src" / "pkg" / "m.py").write_text("x = 1\n")
    (project_dir / ".git").mkdir()
    work = tmp_path / "work"
    wheel = "demo-1.0-py3-none-any.whl"
    plan = StagingPlan(
        site=work / "stage",
        build=["sh", "-c", f'mkdir -p "$0" && touch "$0/{wheel}"', str(work / "wheel")],
        install=[["sh", "-c", 'echo "$PWD $0" > installed.txt', "{wheel}[x]"]],
        wheel_dir=work / "wheel",
        copy=work / "src",
    )

    built = run_plan(plan, project_dir)

    assert built == work / "wheel" / wheel
    assert (project_dir / "installed.txt").read_text().split() == [str(project_dir), f"{built}[x]"]
    copied = {p.relative_to(work / "src").as_posix() for p in (work / "src").rglob("*")}
    assert {"src/pkg/m.py", ".git", "pyproject.toml"} <= copied
    assert not {"build", "dist", "pkg.egg-info", ".venv", "src/pkg/__pycache__"} & copied


def test_run_plan_needs_exactly_one_wheel(tmp_path: Path) -> None:
    plan = StagingPlan(site=tmp_path, build=["true"], install=[], wheel_dir=tmp_path / "none")

    with pytest.raises(StagingError, match="wheel"):
        run_plan(plan, tmp_path)


def test_find_interpreter_prefers_explicit_then_virtualenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_python(path: Path, version: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\necho {version}\n")
        path.chmod(0o755)
        return path

    explicit = fake_python(tmp_path / "x" / "python", "3.14")
    venv = fake_python(tmp_path / "venv" / "bin" / "python", "3.14")
    local = fake_python(tmp_path / "proj" / ".venv" / "bin" / "python", "3.13")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "venv"))
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))

    assert find_interpreter("3.14", tmp_path / "proj", explicit) == explicit
    assert find_interpreter("3.14", tmp_path / "proj", None) == venv
    monkeypatch.delenv("VIRTUAL_ENV")
    with pytest.raises(StagingError, match=r"3\.14"):
        find_interpreter("3.14", tmp_path / "proj", None)  # .venv has 3.13
    assert local.exists()
