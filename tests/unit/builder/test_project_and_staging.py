"""Project discovery, build configuration and staging commands (plan §3.6 steps 1-3)."""

from pathlib import Path

import pytest

from fsbpyr.config import BuildConfig, ConfigError, load_config
from fsbpyr.payload.spec import ZSTD_FAST_LEVEL, ZSTD_LEVEL
from fsbpyr.project import LockSource, Project, ProjectError
from fsbpyr.staging import (
    WHEEL,
    StagingError,
    StagingOptions,
    StagingPlan,
    find_uv,
    rebuild,
    rebuild_wheel,
    run_plan,
    staging_plan,
    uv_version,
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


def test_project_names_its_extras(tmp_path: Path) -> None:
    extras = '\n[project.optional-dependencies]\nfancy = ["rich"]\nfast = []\n'

    assert Project.load(_project(tmp_path, PYPROJECT)).extras == ()
    assert Project.load(_project(tmp_path, PYPROJECT + extras)).extras == ("fancy", "fast")


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


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    text = PYPROJECT + 'profile = "compact"\n'  # replaced by fast-compression (Phase 7)

    with pytest.raises(ConfigError, match=r"unknown .*'profile'.*fast-compression.*best"):
        load_config(Project.load(_project(tmp_path, text)))


def test_fast_compression_can_be_configured(tmp_path: Path) -> None:
    text = PYPROJECT + "fast-compression = true\n"

    fast = load_config(Project.load(_project(tmp_path, text)))
    default = load_config(Project.load(_project(tmp_path, PYPROJECT)))

    assert (fast.fast_compression, fast.zstd_level) == (True, ZSTD_FAST_LEVEL)
    assert (default.fast_compression, default.zstd_level) == (False, ZSTD_LEVEL)


def test_best_compression_can_be_configured_but_not_with_fast_compression(
    tmp_path: Path,
) -> None:
    best = load_config(Project.load(_project(tmp_path, PYPROJECT + "best-compression = true\n")))

    assert (best.best_compression, best.zstd_level) == (True, ZSTD_LEVEL)
    with pytest.raises(ConfigError, match="exclude each other"):
        load_config(Project.load(_project(tmp_path, PYPROJECT)), fast_compression=True,
                    best_compression=True)  # fmt: skip


def test_a_compression_flag_on_the_command_line_wins_over_the_project(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT + "best-compression = true\n"))

    config = load_config(project, fast_compression=True)

    assert (config.fast_compression, config.best_compression) == (True, False)


def test_allowed_system_libs_come_from_tool_table_or_cli(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT + 'allow-system-libs = ["libssl.so.3"]\n'))

    assert load_config(project).allowed_system_libs == ("libssl.so.3",)
    assert load_config(project, allowed_system_libs=("libpq.so.5",)).allowed_system_libs == (
        "libpq.so.5",
    )


@pytest.mark.parametrize("value", ['"libssl.so.3"', '["/usr/lib/libssl.so.3"]', '[""]'])
def test_allowed_system_libs_must_be_sonames(tmp_path: Path, value: str) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT + f"allow-system-libs = {value}\n"))

    with pytest.raises(ConfigError, match=r"allow-system-libs (has the wrong type|entry)"):
        load_config(project)


def test_fs_packages_come_from_tool_table_or_cli(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT + 'fs-packages = ["app.templates"]\n'))

    assert load_config(project).fs_packages == ("app.templates",)
    assert load_config(project, fs_packages=("other",)).fs_packages == ("other",)


@pytest.mark.parametrize("value", ['"app"', '["app/templates"]', '["1app"]', "[1]"])
def test_fs_packages_must_be_package_names(tmp_path: Path, value: str) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT + f"fs-packages = {value}\n"))

    with pytest.raises(ConfigError, match=r"fs-packages (has the wrong type|entry)"):
        load_config(project)


def test_interpreter_reexec_is_off_unless_the_project_or_the_command_line_asks(
    tmp_path: Path,
) -> None:
    default = load_config(Project.load(_project(tmp_path, PYPROJECT)))
    flagged = load_config(Project.load(_project(tmp_path, PYPROJECT)), interpreter_reexec=True)
    configured = load_config(
        Project.load(_project(tmp_path, PYPROJECT + "interpreter-reexec = true\n"))
    )

    assert (default.interpreter_reexec, flagged.interpreter_reexec) == (False, True)
    assert configured.interpreter_reexec is True


# --- staging ---------------------------------------------------------------------------------

UV = Path("/u/uv")
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
UV_VENV = ["/u/uv", "venv", "--python", "3.14", "--quiet", "{work}/stage"]
UV_STAGE = ["/u/uv", "pip", "install", "--python", "{work}/stage/bin/python", "--quiet"]


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
    ("source", "install"),
    [
        (LockSource.PYLOCK, [UV_VENV, [*UV_STAGE, "-r", "{project}/pylock.toml"],
                             [*UV_STAGE, "--no-deps", "{wheel}"]]),
        (LockSource.REQUIREMENTS, [UV_VENV, [*UV_STAGE, "--require-hashes", "-r",
                                             "{project}/requirements.txt"],
                                   [*UV_STAGE, "--no-deps", "{wheel}"]]),
        (LockSource.UNLOCKED, [UV_VENV, [*UV_STAGE, "{wheel}"]]),
    ],
)  # fmt: skip
def test_pip_projects_install_into_a_fresh_venv(
    tmp_path: Path, source: LockSource, install: list[list[str]]
) -> None:
    """A venv keeps installer-made scripts (which name the interpreter uv picked) outside
    site-packages, so they never reach the executable."""
    files = {"requirements.txt": "click==8.1.8 --hash=sha256:abc\n", "pylock.toml": ""}
    project = Project.load(_project(tmp_path, PYPROJECT, **files))
    work = tmp_path / "work"

    plan = staging_plan(project, source, UV, work, StagingOptions("3.14"))

    assert plan.site == work / "stage" / "lib" / "python3.14" / "site-packages"
    assert plan.env == {"UV_VENV_SEED": "0"}  # the user's environment must not add pip
    assert plan.build == _expand([UV_BUILD], work, tmp_path)[0]
    assert plan.install == _expand(install, work, tmp_path)


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        (LockSource.UV_LOCK, [True, True]),  # uv accepts the project's own linux wheel
        (LockSource.PYLOCK, [False, True, True]),  # creating the venv selects no wheels
        (LockSource.REQUIREMENTS, [False, True, True]),
        (LockSource.UNLOCKED, [False, True]),
    ],
)
def test_dependency_wheels_are_chosen_for_the_glibc_baseline(
    tmp_path: Path, source: LockSource, flagged: list[bool]
) -> None:
    files = {"requirements.txt": "click==8.1.8\n", "pylock.toml": "", "uv.lock": ""}
    project = Project.load(_project(tmp_path, PYPROJECT, **files))
    options = StagingOptions("3.14", platform="x86_64-manylinux_2_28")

    plan = staging_plan(project, source, UV, tmp_path / "w", options)

    platform = ["--python-platform", "x86_64-manylinux_2_28"]
    assert [_contains(command, platform) for command in plan.install] == flagged


def _contains(command: list[str], part: list[str]) -> bool:
    return any(command[i : i + len(part)] == part for i in range(len(command)))


def test_unlocked_install_keeps_extras_and_offline(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT))

    options = StagingOptions("3.14", ("a", "b"), offline=True)
    plan = staging_plan(project, LockSource.UNLOCKED, UV, tmp_path / "w", options)

    assert plan.install[-1][-1] == "{wheel}[a,b]"
    assert all("--offline" in command for command in [plan.build, *plan.install])


def test_requirements_without_hashes_do_not_require_them(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, PYPROJECT, **{"requirements.txt": "click==8\n"}))

    plan = staging_plan(
        project, LockSource.REQUIREMENTS, UV, tmp_path / "w", StagingOptions("3.14")
    )

    assert "--require-hashes" not in plan.install[1]


def test_builds_need_uv_and_say_how_to_get_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)

    with pytest.raises(StagingError, match=r"needs uv.*https://docs\.astral\.sh/uv"):
        find_uv()


def test_uv_is_found_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/opt/{name}/bin/{name}")

    assert find_uv() == Path("/opt/uv/bin/uv")


def _fake_uv(tmp_path: Path, body: str) -> Path:
    uv = tmp_path / "uv"
    uv.write_text(f"#!/bin/sh\n{body}\n")
    uv.chmod(0o755)
    return uv


def test_uv_reports_its_version(tmp_path: Path) -> None:
    uv = _fake_uv(tmp_path, 'echo "uv 0.13.1 (x86_64-unknown-linux-gnu)"')

    assert uv_version(uv) == "0.13.1"


@pytest.mark.parametrize("body", ["exit 1", "echo nonsense"])
def test_a_uv_that_cannot_tell_its_version_stops_the_build(tmp_path: Path, body: str) -> None:
    with pytest.raises(StagingError, match="version"):
        uv_version(_fake_uv(tmp_path, body))


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


def test_run_plan_builds_and_installs_in_the_project_directory(tmp_path: Path) -> None:
    (tmp_path / "proj").mkdir()
    project_dir = _project(tmp_path / "proj", PYPROJECT)
    work = tmp_path / "work"
    wheel = "demo-1.0-py3-none-any.whl"
    plan = StagingPlan(
        site=work / "stage",
        build=["sh", "-c", f'mkdir -p "$0" && touch "$0/{wheel}"', str(work / "wheel")],
        install=[["sh", "-c", 'echo "$PWD $0" > installed.txt', "{wheel}[x]"]],
        wheel_dir=work / "wheel",
    )

    built = run_plan(plan, project_dir)

    assert built == work / "wheel" / wheel
    assert (project_dir / "installed.txt").read_text().split() == [str(project_dir), f"{built}[x]"]


def test_rebuild_forgets_the_cached_builds_then_repeats_the_installs(tmp_path: Path) -> None:
    """uv reuses a wheel it built before whatever the flags (--refresh-package only revalidates
    the index), so the cached builds of the named packages go first."""
    calls = tmp_path / "calls.txt"
    logged = ["sh", "-c", 'echo "$FLAGS $*" >> "$CALLS"', "sh"]
    plan = StagingPlan(
        site=tmp_path / "stage",
        build=["false"],  # the wheel is built already
        install=[
            ["false", "venv", "stage"],
            [*logged, "-r", "lock"],
            [*logged, "--no-deps", WHEEL],
        ],
        wheel_dir=tmp_path / "wheel",
        env={"CALLS": str(calls)},
        forget=[*logged, "cache", "clean"],
    )

    rebuild(plan, tmp_path, tmp_path / "app.whl", ["posix-ipc"], {"FLAGS": "sysroot"})

    assert calls.read_text().splitlines() == [
        "sysroot cache clean posix-ipc",
        "sysroot -r lock --reinstall-package posix-ipc",
        f"sysroot --no-deps {tmp_path / 'app.whl'} --reinstall-package posix-ipc",
    ]


def test_rebuild_wheel_builds_the_project_again_into_a_new_directory(tmp_path: Path) -> None:
    wheel = "demo-1.0-cp314-cp314-linux_x86_64.whl"
    plan = StagingPlan(
        site=tmp_path / "stage",
        build=["sh", "-c", f'echo "$FLAGS" > "$0/{wheel}"', str(tmp_path / "wheel")],
        install=[],
        wheel_dir=tmp_path / "wheel",
        env={"FLAGS": "host"},
    )
    first = run_plan(plan, tmp_path)

    again = rebuild_wheel(plan, tmp_path, {"FLAGS": "sysroot"})

    assert again.name == wheel and again.parent.parent == tmp_path and again != first
    assert (first.read_text(), again.read_text()) == ("host\n", "sysroot\n")


def test_run_plan_needs_exactly_one_wheel(tmp_path: Path) -> None:
    plan = StagingPlan(site=tmp_path, build=["true"], install=[], wheel_dir=tmp_path / "none")

    with pytest.raises(StagingError, match="wheel"):
        run_plan(plan, tmp_path)


def test_run_plan_logs_each_uv_step(tmp_path: Path) -> None:
    wheel = "demo-1.0-py3-none-any.whl"
    uv = str(_fake_uv(tmp_path, f'[ "$1" = build ] && mkdir -p "$2" && touch "$2/{wheel}"; exit 0'))
    plan = StagingPlan(
        site=tmp_path,
        build=[uv, "build", str(tmp_path / "wheel")],
        install=[[uv, "sync"], [uv, "pip", "install", "--no-deps", WHEEL]],
        wheel_dir=tmp_path / "wheel",
    )
    lines: list[str] = []

    run_plan(plan, tmp_path, log=lines.append)

    assert lines == [
        f"uv: building {tmp_path.name}'s wheel",
        "uv: installing the locked distributions",
        "uv: installing the project's wheel",
    ]


def test_a_failed_step_names_the_step_and_shows_uvs_output(tmp_path: Path) -> None:
    uv = str(_fake_uv(tmp_path, "echo boom >&2; exit 3"))
    plan = StagingPlan(site=tmp_path, build=[uv, "build"], install=[],
                       wheel_dir=tmp_path / "wheel")  # fmt: skip

    with pytest.raises(StagingError, match=r"building .*wheel: uv failed \(exit 3\):\nboom"):
        run_plan(plan, tmp_path)


def test_verbose_prints_the_command_and_lets_uvs_stderr_through(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    uv = str(_fake_uv(tmp_path, "echo boom >&2; exit 3"))
    plan = StagingPlan(site=tmp_path, build=[uv, "build", "a b"], install=[],
                       wheel_dir=tmp_path / "wheel")  # fmt: skip

    with pytest.raises(StagingError, match=r"building .*wheel: uv failed .*see its output above"):
        run_plan(plan, tmp_path, verbose=True)

    err = capfd.readouterr().err
    assert f"fsbpyr: {uv} build 'a b'" in err
    assert "boom" in err
