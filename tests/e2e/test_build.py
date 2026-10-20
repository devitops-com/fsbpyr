"""Phase 4 exit criteria: `fsbpyr build` on real uv/pip projects, and self-hosting.

Needs the cached runtime and, on the first run, network access for the fixtures' packages
(the uv cache afterwards).
"""

import hashlib
import itertools
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fsbpyr.build import TARGET_PYTHON, rebuild_on_baseline
from fsbpyr.cli import main
from fsbpyr.compat import zstd
from fsbpyr.elf import glibc_requirement
from fsbpyr.payload.reader import Executable
from fsbpyr.payload.spec import ZSTD_FAST_LEVEL, ZSTD_LEVEL, Blob, stored_bytes
from fsbpyr.runtime import pins
from fsbpyr.runtime.registry import Runtime, cache_home, cache_root
from fsbpyr.staging import StagingPlan
from fsbpyr.vcs import git_state, own_version, version_string

from ..support.readme import quickstart

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parents[2]
VENV = Path(sys.prefix)  # the development environment: .venv, or .venv-3.X for make PYTHON=3.X
FIXTURES = ROOT / "tests" / "fixtures" / "projects"
UV = shutil.which("uv")
GIT = shutil.which("git")
RELEASE = "https://github.com/devitops-com/fsbpyr/releases/latest/download/fsbpyr"


@pytest.fixture(autouse=True)
def _runtime_available(runtime: Runtime) -> None:
    """Every test here needs the cached runtime (built once per session if missing)."""


def _fixture(name: str, tmp_path: Path, *, drop: tuple[str, ...] = ()) -> Path:
    project = tmp_path / name
    shutil.copytree(FIXTURES / name, project)
    for lock in drop:
        (project / lock).unlink()
    return project


def _run(executable: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [executable, *args], capture_output=True, text=True, check=False, timeout=60
    )


def _tree(directory: Path) -> dict[str, float]:
    return {str(p): p.lstat().st_mtime for p in directory.rglob("*")}


def test_hello_from_uv_lock(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _fixture("hello", tmp_path)

    assert main(["build", str(project)]) == 0

    executable = project / "dist" / "hello"
    result = _run(executable, "5", "x y")
    assert (result.returncode, result.stdout) == (5, "hello ['5', 'x y']\n")
    assert f"built {executable}" in capsys.readouterr().out
    assert main(["inspect", str(executable)]) == 0
    assert capsys.readouterr().out.splitlines()[:2] == [
        "app        hello 1.0.0",  # a copy outside any repository: no commit
        f"built with fsbpyr {own_version()}",
    ]
    assert main(["inspect", "--json", "--verify", str(executable)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["verified"] is True and report["modules"]["hello"] == ["hello"]
    assert report["build_info"]["zstd_level"] == ZSTD_LEVEL
    info = report["build_info"]  # the manifest (REP-3)
    assert report["schema"] == info["schema"] == 1
    lock = hashlib.sha256((project / "uv.lock").read_bytes()).hexdigest()
    assert info["lock"] == {"kind": "uv.lock", "path": "uv.lock", "hashed": True, "sha256": lock}
    assert {"name": "hello", "version": "1.0.0", "source": "local"} in info["dists"]
    assert info["runtime_components"]["openssl"].startswith("OpenSSL 3.")
    assert report["shared_libraries"] == [] and "json" in report["runtime_modules"]


def test_readme_quickstart_works_on_a_new_account(tmp_path: Path) -> None:
    """The README's quickstart in a copy of hello, as on a new account: an empty home, whose
    new ~/.local/bin is not on PATH. A stand-in for curl puts the fsbpyr under test (not yet
    executable) where the release would go. uv keeps its cache (XDG_CACHE_HOME) and its
    Pythons; the runtime is in FSBPYR_CACHE_DIR (conftest)."""
    assert UV is not None
    project = _fixture("hello", tmp_path)
    with (project / "pyproject.toml").open("a") as pyproject:  # builds for the Python tests run on
        pyproject.write(f'\n[tool.fsbpyr]\npython = "{TARGET_PYTHON}"\n')
    home, tools, fsbpyr = tmp_path / "home", tmp_path / "tools", tmp_path / "fsbpyr"
    home.mkdir()
    tools.mkdir()
    (tools / "uv").symlink_to(UV)
    under_test = shlex.quote(str(VENV / "bin" / "fsbpyr"))
    fsbpyr.write_text(f'#!/bin/sh\nexec {under_test} "$@"\n')
    (tools / "curl").write_text(
        f'#!/bin/sh\ntest "$*" = "-fLo $HOME/.local/bin/fsbpyr {RELEASE}" ||\n'
        '  { echo "curl stand-in: unexpected arguments: $*" >&2; exit 2; }\n'
        f'cp {shlex.quote(str(fsbpyr))} "$HOME/.local/bin/fsbpyr"\n'
    )
    (tools / "curl").chmod(0o755)
    pythons = subprocess.run([UV, "python", "dir"], capture_output=True, text=True, check=True)
    path = os.pathsep.join([str(tools), "/usr/bin", "/bin"])
    env = {**os.environ, "HOME": str(home), "PATH": path, "XDG_CACHE_HOME": str(cache_home()),
           "UV_PYTHON_INSTALL_DIR": pythons.stdout.strip()}  # fmt: skip
    assert shutil.which("fsbpyr", path=path) is None

    result = subprocess.run(["/bin/sh", "-ec", quickstart()], cwd=project, env=env,
                            capture_output=True, text=True, check=False)  # fmt: skip

    assert result.returncode == 0, result.stderr
    assert "hello []" in result.stdout.splitlines()


@pytest.mark.parametrize(
    ("drop", "lock"),
    [
        ((), "uv.lock"),
        (("uv.lock",), "pylock.toml"),
        (("uv.lock", "pylock.toml"), "requirements"),
        (("uv.lock", "pylock.toml", "requirements.txt"), "unlocked"),
    ],
)
def test_clicky_from_each_lock_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], drop: tuple[str, ...], lock: str
) -> None:
    project = _fixture("clicky", tmp_path, drop=drop)
    out = tmp_path / "bin" / "clicky"

    assert main(["build", str(project), "-o", str(out)]) == 0

    result = _run(out, "Ada", "--shout")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "HELLO, ADA!"
    assert result.stdout.splitlines()[1].startswith("click 8.")
    unlocked_warning = "unlocked build" in capsys.readouterr().err
    assert unlocked_warning is (lock == "unlocked")


@pytest.mark.parametrize(
    "drop",
    [(), ("uv.lock",), ("uv.lock", "pylock.toml"), ("uv.lock", "pylock.toml", "requirements.txt")],
)
def test_builds_do_not_depend_on_the_build_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drop: tuple[str, ...]
) -> None:
    project = _fixture("clicky", tmp_path, drop=drop)
    first, second = tmp_path / "first", tmp_path / "second"
    assert main(["build", str(project), "-o", str(first), "--no-smoke"]) == 0
    other = tmp_path / "other-venv"  # uv prefers an active virtual environment's interpreter
    command = [UV or "uv", "venv", "--quiet", "--python", TARGET_PYTHON, str(other)]
    subprocess.run(command, check=True)
    monkeypatch.setenv("VIRTUAL_ENV", str(other))
    monkeypatch.setenv("UV_VENV_SEED", "1")  # would add pip to a venv uv creates

    assert main(["build", str(project), "-o", str(second), "--no-smoke"]) == 0

    assert first.read_bytes() == second.read_bytes()
    with Executable.open(first) as executable:  # installer-made scripts name a host Python
        assert not [f.path for f in executable.payload.files() if f.path.startswith("bin/")]

    # What the reproduce job of release.yml varies (REP-2): the checkout's path, the umask, the
    # cache directories and the number of cores. The runtime is the one already built.
    elsewhere = _fixture("clicky", tmp_path / "another" / "checkout", drop=drop)
    third = tmp_path / "third"
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(cache_root()))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    cores, old_umask = os.sched_getaffinity(0), os.umask(0o077)
    os.sched_setaffinity(0, {min(cores)})
    try:
        assert main(["build", str(elsewhere), "-o", str(third), "--no-smoke"]) == 0
    finally:
        os.sched_setaffinity(0, cores)
        os.umask(old_umask)

    assert third.read_bytes() == first.read_bytes()


def test_stdlib_frames_are_level_9_and_fast_builds_record_level_3(
    tmp_path: Path, runtime: Runtime, capsys: pytest.CaptureFixture[str]
) -> None:
    code = next(m.code for m in runtime.modules if m.name == "argparse")
    assert code is not None
    assert Blob.code(zstd.decompress(stored_bytes(code.stored)), level=ZSTD_LEVEL) == code
    project = _fixture("hello", tmp_path)

    assert main(["build", str(project), "--fast-compression", "--no-smoke"]) == 0
    capsys.readouterr()
    assert main(["inspect", "--json", str(project / "dist" / "hello")]) == 0

    assert json.loads(capsys.readouterr().out)["build_info"]["zstd_level"] == ZSTD_FAST_LEVEL


def test_best_compression_of_a_pure_python_app_is_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _fixture("hello", tmp_path)

    assert main(["build", str(project), "--best-compression", "--no-smoke"]) == 0

    assert "fsbpyr: warning: best-compression has nothing to compress" in capsys.readouterr().err


def test_builds_without_uv_explain_how_to_install_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _fixture("clicky", tmp_path, drop=("uv.lock", "pylock.toml"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))  # no uv (D26: uv is required)

    status = main(["build", str(project), "-o", str(tmp_path / "out" / "clicky")])

    assert status == 1
    assert "fsbpyr needs uv to build" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_sources_are_rebuilt_after_every_change(tmp_path: Path) -> None:
    project = _fixture("hello", tmp_path)
    main(["build", str(project), "--no-smoke"])
    cli = project / "src" / "hello" / "cli.py"
    cli.write_text(cli.read_text().replace('print("hello"', 'print("changed"'))

    assert main(["build", str(project)]) == 0

    assert _run(project / "dist" / "hello").stdout.startswith("changed")


def test_developer_venv_is_never_touched(tmp_path: Path) -> None:
    project = _fixture("clicky", tmp_path)
    assert UV is not None
    subprocess.run([UV, "sync", "--quiet", "--project", project], check=True)
    before = _tree(project / ".venv")

    assert main(["build", str(project)]) == 0

    assert _tree(project / ".venv") == before


def test_self_hosting_reaches_a_fixed_point(tmp_path: Path) -> None:
    """stage1 (venv) -> stage2 -> stage3 with only uv (and git, whose commit the build records
    for --version) on PATH and an empty cache."""
    assert UV is not None
    if GIT is None:
        pytest.skip("needs git: the build records the commit for --version")
    (tmp_path / "git").mkdir()
    (tmp_path / "git" / "git").symlink_to(GIT)
    path = os.pathsep.join([str(Path(UV).parent), str(tmp_path / "git")])
    env = {**os.environ, "PATH": path, "FSBPYR_CACHE_DIR": str(tmp_path / "c")}
    venv_before = _tree(VENV)
    stages = [tmp_path / f"stage{n}" for n in (1, 2, 3)]

    assert main(["build", str(ROOT), "-o", str(stages[0])]) == 0
    for builder, target in itertools.pairwise(stages):
        result = subprocess.run(
            [builder, "build", ROOT, "--python", TARGET_PYTHON, "-o", target],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    data = [stage.read_bytes() for stage in stages]
    assert data[1] == data[2], "stage2 and stage3 differ"
    assert data[0] == data[1], "stage1 and stage2 differ"
    infos = []
    for stage in stages[:2]:
        with Executable.open(stage) as exe:
            infos.append((json.loads(exe.payload.build_info), exe.stub_prefix()))
    (info, prefix), (other, _) = infos
    assert info["stub_sha256"] == other["stub_sha256"] == hashlib.sha256(prefix).hexdigest()
    version = _run(stages[2], "--version")  # recorded by the build: the binary has no git
    assert version.stdout == version_string(git_state(ROOT)) + "\n"
    assert not (tmp_path / "c" / "runtimes").exists()  # the binary used its own runtime
    assert _tree(VENV) == venv_before


def test_self_hosted_binary_builds_offline_without_clang(tmp_path: Path) -> None:
    assert UV is not None
    builder = tmp_path / "fsbpyr"
    assert main(["build", str(ROOT), "-o", str(builder)]) == 0
    project = _fixture("clicky", tmp_path)
    main(["build", str(project), "--no-smoke"])  # warm the uv cache for this project
    env = {**os.environ, "PATH": str(Path(UV).parent), "FSBPYR_CACHE_DIR": str(tmp_path / "c")}

    result = subprocess.run(
        [builder, "build", project, "--python", TARGET_PYTHON, "--offline", "-o",
         tmp_path / "offline"],
        env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip

    assert result.returncode == 0, result.stderr
    assert _run(tmp_path / "offline", "x").stdout.startswith("Hello, x!")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (('requires-python = ">=3.12"', 'requires-python = "<3.12"'), "requires-python"),
        (('[project.scripts]\nclicky = "clicky.cli:main"', ""), "--entry"),
    ],
)
def test_build_errors_are_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: tuple[str, str], message: str
) -> None:
    project = _fixture("clicky", tmp_path, drop=("uv.lock", "pylock.toml"))
    pyproject = project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace(*change))

    assert main(["build", str(project)]) == 1

    assert message in capsys.readouterr().err


def test_the_projects_own_extension_is_rebuilt_against_the_glibc_sysroot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """cext's extension starts a thread. Built on a host with glibc 2.34 or newer (trixie has
    2.41), it binds pthread_create to GLIBC_2.34; against the 2.28 sysroot, to GLIBC_2.2.5.
    The build gets a uv cache of its own: `uv run`, which runs the tests, holds its cache shared
    until pytest exits, and the rebuild's `uv cache clean` waits to hold it alone."""
    if shutil.which("cc") is None:
        pytest.skip("no C compiler")
    project = _fixture("cext", tmp_path)
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    host_builds: list[tuple[int, int] | None] = []

    def staged_then_rebuilt(plan: StagingPlan, project_dir: Path, wheel: Path, names: list[str],
                            baseline: pins.SysrootPin, **kwargs: Any) -> list[str]:  # fmt: skip
        host_builds.extend(glibc_requirement(so.read_bytes())
                           for so in (plan.site / "cext").glob("*.so"))  # fmt: skip
        return rebuild_on_baseline(plan, project_dir, wheel, names, baseline, **kwargs)

    monkeypatch.setattr("fsbpyr.build.rebuild_on_baseline", staged_then_rebuilt)
    executable = tmp_path / "bin" / "cext"

    assert main(["build", str(project), "-o", str(executable)]) == 0

    out, err = capsys.readouterr()
    assert "rebuilding cext against the glibc 2.28 sysroot" in out
    [host_build] = host_builds
    assert host_build is not None and host_build > (2, 28)
    assert "glibc" not in err
    assert main(["inspect", "--json", str(executable)]) == 0
    glibc = json.loads(capsys.readouterr().out)["glibc"]
    assert tuple(map(int, glibc.split("."))) <= (2, 28)
    assert _run(executable, "21").stdout == "cext 42\n"


# --- review regressions (Phase 4) -------------------------------------------------------------


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _uv_project(name: str, deps: str = "", extra: str = "") -> str:
    return (
        f'[project]\nname = "{name}"\nversion = "1.0"\nrequires-python = ">=3.12"\n'
        f"dependencies = [{deps}]\n{extra}\n"
        '[build-system]\nrequires = ["uv_build>=0.12.17,<0.13.0"]\nbuild-backend = "uv_build"\n'
    )


def _payload_modules(executable: Path) -> set[str]:
    from fsbpyr.payload.reader import Executable  # noqa: PLC0415

    with Executable.open(executable) as exe:
        return {m.name for m in exe.payload.modules()}


def test_relative_requirement_paths_resolve_against_the_project(tmp_path: Path) -> None:
    project = _write(tmp_path / "app", {
        "pyproject.toml": _uv_project("app", '"greet"', '[project.scripts]\napp = "app:main"'),
        "src/app/__init__.py": "import greet\ndef main():\n    print(greet.WORD)\n",
        "requirements.txt": "./libs/greet\n",
        "libs/greet/pyproject.toml": _uv_project("greet"),
        "libs/greet/src/greet/__init__.py": "WORD = 'hi from greet'\n",
    })  # fmt: skip
    out = tmp_path / "out" / "app"

    assert main(["build", str(project), "-o", str(out)]) == 0  # cwd is the repository, not app/

    assert _run(out).stdout == "hi from greet\n"


def test_editable_dependencies_are_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _write(tmp_path / "app", {
        "pyproject.toml": _uv_project("app", '"greet"', '[project.scripts]\napp = "app:main"'),
        "src/app/__init__.py": "def main():\n    pass\n",
        "requirements.txt": "-e ./libs/greet\n",
        "libs/greet/pyproject.toml": _uv_project("greet"),
        "libs/greet/src/greet/__init__.py": "",
    })  # fmt: skip

    assert main(["build", str(project)]) == 1

    assert "greet is installed in editable mode" in capsys.readouterr().err


def test_stale_setuptools_build_directories_never_leak(tmp_path: Path) -> None:
    project = _write(tmp_path / "st", {
        "pyproject.toml": (
            '[project]\nname = "st"\nversion = "1.0"\n[project.scripts]\nst = "st.cli:main"\n'
            '[build-system]\nrequires = ["setuptools>=69"]\n'
            'build-backend = "setuptools.build_meta"\n'
        ),
        "src/st/__init__.py": "",
        "src/st/cli.py": "def main():\n    print('ok')\n",
        "src/st/old.py": "SECRET = 1\n",
        "requirements.txt": "",
    })  # fmt: skip
    out = tmp_path / "st-bin"
    assert main(["build", str(project), "-o", str(out)]) == 0
    assert "st.old" in _payload_modules(out)
    (project / "src" / "st" / "old.py").unlink()

    assert main(["build", str(project), "-o", str(out)]) == 0

    assert "st.old" not in _payload_modules(out)
    assert not (project / "build").exists()  # wheels are built out of tree


def test_build_interpreter_optimization_does_not_change_the_output(tmp_path: Path) -> None:
    project = _fixture("hello", tmp_path)
    fsbpyr = VENV / "bin" / "fsbpyr"
    outputs = []
    for optimize in ("", "2"):
        out = tmp_path / f"hello-{optimize or 'plain'}"
        env = {**os.environ, "PYTHONOPTIMIZE": optimize} if optimize else dict(os.environ)
        command = [str(fsbpyr), "build", str(project), "--python", TARGET_PYTHON, "-o", str(out),
                   "--no-smoke"]
        subprocess.run(command, env=env, check=True, capture_output=True)
        outputs.append(out.read_bytes())

    assert outputs[0] == outputs[1]


def test_workspace_member_uses_the_workspace_lock_and_fresh_sibling_sources(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert UV is not None
    root = _write(tmp_path / "ws", {
        "pyproject.toml": '[tool.uv.workspace]\nmembers = ["packages/*"]\n',
        "packages/app/pyproject.toml": _uv_project(
            "app", '"lib"', '[project.scripts]\napp = "app:main"\n[tool.uv.sources]\n'
            "lib = { workspace = true }"),
        "packages/app/src/app/__init__.py": "import lib\ndef main():\n    print(lib.VALUE)\n",
        "packages/lib/pyproject.toml": _uv_project("lib"),
        "packages/lib/src/lib/__init__.py": "VALUE = 'first'\n",
    })  # fmt: skip
    subprocess.run([UV, "lock", "--quiet", "--project", root], check=True)
    member = root / "packages" / "app"
    out = tmp_path / "app"

    assert main(["build", str(member), "-o", str(out)]) == 0
    assert "from uv.lock" in capsys.readouterr().out
    assert _run(out).stdout == "first\n"
    (root / "packages" / "lib" / "src" / "lib" / "__init__.py").write_text("VALUE = 'second'\n")
    assert main(["build", str(member), "-o", str(out)]) == 0
    assert _run(out).stdout == "second\n"


def test_default_dependency_groups_are_not_packaged(tmp_path: Path) -> None:
    assert UV is not None
    groups = (
        '[project.scripts]\ngrp = "grpapp:main"\n[dependency-groups]\nlint = ["six"]\n'
        '[tool.uv]\ndefault-groups = ["lint"]'
    )
    project = _write(tmp_path / "grpapp", {
        "pyproject.toml": _uv_project("grpapp", "", groups),
        "src/grpapp/__init__.py": "def main():\n    print('grp')\n",
    })  # fmt: skip
    subprocess.run([UV, "lock", "--quiet", "--project", project], check=True)

    assert main(["build", str(project)]) == 0

    assert "six" not in _payload_modules(project / "dist" / "grp")


def test_smoke_test_resolves_the_entry_attribute(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _fixture("hello", tmp_path)

    assert main(["build", str(project), "--entry", "hello.cli:missing"]) == 1

    assert "smoke test" in capsys.readouterr().err


def test_a_failed_smoke_test_leaves_the_output_as_it_was(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`fsbpyr build ~/src/mytool -o ~/.local/bin/mytool` never replaces a working tool with
    a broken one."""
    project = _fixture("hello", tmp_path)
    out = tmp_path / "bin" / "hello"
    out.parent.mkdir()
    old = b"#!/bin/sh\necho the working tool\n"
    out.write_bytes(old)

    assert main(["build", str(project), "--entry", "hello.cli:missing", "-o", str(out)]) == 1

    assert f"smoke test of the new {out.resolve()} failed" in capsys.readouterr().err
    assert out.read_bytes() == old
    assert sorted(p.name for p in out.parent.iterdir()) == ["hello"]  # no .hello.*.tmp left


def test_smoke_args_run_the_new_executable_once(tmp_path: Path) -> None:
    project = _fixture("clicky", tmp_path)
    pyproject = project / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + '\n[tool.fsbpyr]\nsmoke-args = ["--help"]\n')
    out = tmp_path / "bin" / "clicky"

    assert main(["build", str(project), "-o", str(out)]) == 0

    assert out.read_bytes()[:4] == b"\x7fELF"


def test_failing_smoke_args_fail_the_build_and_leave_the_output_as_it_was(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _fixture("hello", tmp_path)
    cli = project / "src" / "hello" / "cli.py"
    cli.write_text(cli.read_text().replace(
        "def main() -> int:\n",
        "def main() -> int:\n    if sys.argv[1:] == ['--version']:\n        no_such_function()\n"))
    out = tmp_path / "bin" / "hello"
    out.parent.mkdir()
    old = b"#!/bin/sh\necho the working tool\n"
    out.write_bytes(old)

    assert main(["build", str(project), "-o", str(out), "--smoke-args", "--version"]) == 1

    err = capsys.readouterr().err
    assert "arguments '--version'" in err and "no_such_function" in err
    assert out.read_bytes() == old
    assert sorted(p.name for p in out.parent.iterdir()) == ["hello"]
    assert main(["build", str(project), "-o", str(out)]) == 0  # no smoke-args: not run


def test_strict_refuses_an_unlocked_build_and_leaves_the_output_as_it_was(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _fixture("hello", tmp_path, drop=("uv.lock",))
    out = tmp_path / "bin" / "hello"
    out.parent.mkdir()
    old = b"#!/bin/sh\necho the working tool\n"
    out.write_bytes(old)

    assert main(["build", str(project), "-o", str(out), "--strict"]) == 1

    assert "unlocked build" in capsys.readouterr().err
    assert out.read_bytes() == old
    assert sorted(p.name for p in out.parent.iterdir()) == ["hello"]
    assert main(["build", str(project), "-o", str(out)]) == 0  # not strict
    assert out.read_bytes()[:4] == b"\x7fELF"


def test_strict_refuses_requirements_without_hashes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _fixture("clicky", tmp_path, drop=("uv.lock", "pylock.toml"))
    requirements = project / "requirements.txt"
    pinned = [line.split()[0] for line in requirements.read_text().splitlines()
              if "==" in line and not line.startswith((" ", "#"))]  # fmt: skip
    requirements.write_text("\n".join(pinned) + "\n")
    out = tmp_path / "bin" / "clicky"

    assert main(["build", str(project), "-o", str(out), "--strict"]) == 1

    assert "all requirements must have a hash" in capsys.readouterr().err
    assert not out.exists()


def test_a_changed_hash_in_pylock_toml_fails_the_build(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """uv checks pylock.toml's hashes on its own (uv 0.13.1), so --strict adds nothing there."""
    project = _fixture("clicky", tmp_path, drop=("uv.lock",))
    lock = project / "pylock.toml"
    text = lock.read_text()
    digest = "255bc9599cf7748b4b1a446ccc735421bd08a2ae529a8b88597d3de5664ee360"
    assert digest in text
    lock.write_text(text.replace(digest, "0" * 64))
    out = tmp_path / "bin" / "clicky"

    assert main(["build", str(project), "-o", str(out)]) == 1

    assert "Hash mismatch" in capsys.readouterr().err
    assert not out.exists()
