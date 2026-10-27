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

from fsbpyr import __version__
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

from ..support import contain, requires
from ..support.readme import quickstart
from ..support.sbom import load as load_sbom

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parents[2]
VENV = Path(sys.prefix)  # the development environment: .venv, or .venv-3.X for make PYTHON=3.X
FIXTURES = ROOT / "tests" / "fixtures" / "projects"
INSTALLER = "https://github.com/devitops-com/fsbpyr/releases/latest/download/install.sh"


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
    # inspect shows the release number when this fsbpyr cannot see its commit (a worktree's
    # copy in make test-container, whose .git points outside the container)
    unseen = own_version() == version_string(None)
    assert capsys.readouterr().out.splitlines()[:2] == [
        "app        hello 1.0.0",  # a copy outside any repository: no commit
        f"built with fsbpyr {__version__ if unseen else own_version()}",
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
    bom = json.loads(load_sbom().render(report))  # REP-7: the SBOM of this very executable
    assert bom["metadata"]["component"]["hashes"] == [
        {"alg": "SHA-256", "content": hashlib.sha256(executable.read_bytes()).hexdigest()}]
    cpes = {c["name"]: c["cpe"] for c in bom["components"] if "cpe" in c}
    openssl = info["runtime_components"]["openssl"].split()[1]
    assert cpes["openssl"] == f"cpe:2.3:a:openssl:openssl:{openssl}:*:*:*:*:*:*:*"
    assert cpes["sqlite"].split(":")[5] == info["runtime_components"]["sqlite"]
    assert cpes["cpython"].split(":")[5] == report["python"]
    expat = cpes["expat"].split(":")[5]  # "expat_2.9.0", as pyexpat says it, matches no CVE
    assert expat[:1].isdigit() and info["runtime_components"]["expat"].endswith(expat)


def test_readme_quickstart_works_on_a_new_account(tmp_path: Path) -> None:
    """The README's quickstart in a copy of hello, as on a new account: an empty home, whose
    new ~/.local/bin is not on PATH. A stand-in for curl serves an installer that puts the
    fsbpyr under test where the release's would go. uv keeps its cache (XDG_CACHE_HOME) and
    its Pythons; the runtime is in FSBPYR_CACHE_DIR (conftest)."""
    uv = requires.tool("uv")
    project = _fixture("hello", tmp_path)
    with (project / "pyproject.toml").open("a") as pyproject:  # builds for the Python tests run on
        pyproject.write(f'\n[tool.fsbpyr]\npython = "{TARGET_PYTHON}"\n')
    home, tools, fsbpyr = tmp_path / "home", tmp_path / "tools", tmp_path / "fsbpyr"
    home.mkdir()
    tools.mkdir()
    (tools / "uv").symlink_to(uv)
    under_test = shlex.quote(str(VENV / "bin" / "fsbpyr"))
    fsbpyr.write_text(f'#!/bin/sh\nexec {under_test} "$@"\n')
    (tools / "curl").write_text(
        f'#!/bin/sh\ntest "$*" = "-LsSf {INSTALLER}" ||\n'
        '  { echo "curl stand-in: unexpected arguments: $*" >&2; exit 2; }\n'
        "cat <<'EOF'\n"
        'mkdir -p "$HOME/.local/bin"\n'
        f'cp {shlex.quote(str(fsbpyr))} "$HOME/.local/bin/fsbpyr"\n'
        'chmod +x "$HOME/.local/bin/fsbpyr"\n'
        "EOF\n"
    )
    (tools / "curl").chmod(0o755)
    pythons = subprocess.run([uv, "python", "dir"], capture_output=True, text=True, check=True)
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
    command = [requires.tool("uv"), "venv", "--quiet", "--python", TARGET_PYTHON, str(other)]
    subprocess.run(command, check=True)
    monkeypatch.setenv("VIRTUAL_ENV", str(other))
    monkeypatch.setenv("UV_VENV_SEED", "1")  # would add pip to a venv uv creates

    assert main(["build", str(project), "-o", str(second), "--no-smoke"]) == 0

    assert first.read_bytes() == second.read_bytes()
    with Executable.open(first) as executable:  # installer-made scripts name a host Python
        assert not [f.path for f in executable.payload.files() if f.path.startswith("bin/")]

    # What the reproduce job of build.yml varies (REP-2): the checkout's path, the umask, the
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
    uv = requires.tool("uv")
    subprocess.run([uv, "sync", "--quiet", "--project", project], check=True)
    before = _tree(project / ".venv")

    assert main(["build", str(project)]) == 0

    assert _tree(project / ".venv") == before


def test_self_hosting_reaches_a_fixed_point(tmp_path: Path) -> None:
    """stage1 (venv) -> stage2 -> stage3 with only uv (and git, whose commit the build records
    for --version) on PATH and an empty cache."""
    uv = requires.tool("uv")
    git = requires.tool("git", "the build records the commit for --version")
    (tmp_path / "git").mkdir()
    (tmp_path / "git" / "git").symlink_to(git)
    path = os.pathsep.join([str(Path(uv).parent), str(tmp_path / "git")])
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
    uv = requires.tool("uv")
    builder = tmp_path / "fsbpyr"
    assert main(["build", str(ROOT), "-o", str(builder)]) == 0
    project = _fixture("clicky", tmp_path)
    main(["build", str(project), "--no-smoke"])  # warm the uv cache for this project
    env = {**os.environ, "PATH": str(Path(uv).parent), "FSBPYR_CACHE_DIR": str(tmp_path / "c")}

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
    """cext's extension parses its argument with strtol. Built on a host with glibc 2.38 or
    newer (trixie has 2.41), it binds __isoc23_strtol (GLIBC_2.38), above the 2.34 baseline;
    against Debian 12's sysroot, strtol (GLIBC_2.2.5).
    The build gets a uv cache of its own: `uv run`, which runs the tests, holds its cache shared
    until pytest exits, and the rebuild's `uv cache clean` waits to hold it alone."""
    requires.tool("cc")
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
    assert "rebuilding cext against the glibc 2.36 sysroot" in out
    [host_build] = host_builds
    assert host_build is not None and host_build > (2, 34)
    assert "glibc" not in err
    assert main(["inspect", "--json", str(executable)]) == 0
    glibc = json.loads(capsys.readouterr().out)["glibc"]
    assert tuple(map(int, glibc.split("."))) <= (2, 34)
    assert _run(executable, "21").stdout == "cext 42\n"
    # ctypes.CDLL of its own library by bare file name finds the bundled one (fixups)
    assert _run(executable, "--ctypes").stdout == "ctypes True\n"


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


def test_editable_requirements_install_as_regular_packages(tmp_path: Path) -> None:
    project = _write(tmp_path / "app", {
        "pyproject.toml": _uv_project("app", '"greet"', '[project.scripts]\napp = "app:main"'),
        "src/app/__init__.py": "import greet\ndef main():\n    print(greet.WORD)\n",
        "requirements.txt": "-e ./libs/greet\n",
        "libs/greet/pyproject.toml": _uv_project("greet"),
        "libs/greet/src/greet/__init__.py": "WORD = 'hi from greet'\n",
    })  # fmt: skip
    out = tmp_path / "out" / "app"

    assert main(["build", str(project), "-o", str(out)]) == 0

    assert _run(out).stdout == "hi from greet\n"


def test_strict_refuses_vcs_requirements_before_uv_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_uv() -> Path:
        raise AssertionError("uv was looked up")

    monkeypatch.setattr("fsbpyr.build.find_uv", no_uv)
    project = _fixture("clicky", tmp_path, drop=("uv.lock", "pylock.toml"))
    sha = "a" * 40
    (project / "requirements.txt").write_text(f"click==8.1.7\nlib @ git+https://h/r.git@{sha}\n")

    assert main(["build", str(project), "--strict"]) == 1

    err = capsys.readouterr().err
    assert "not a hash" in err
    assert "requirements.txt:2: lib @ git+https://h/r.git@" in err
    assert "uv failed" not in err
    assert not (project / "dist").exists()


def test_a_failed_uv_build_shows_the_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _write(tmp_path / "flat", {
        "pyproject.toml": (
            '[project]\nname = "flat"\nversion = "1.0"\n'
            '[build-system]\nrequires = ["setuptools>=69"]\n'
            'build-backend = "setuptools.build_meta"\n'
        ),
        "one.py": "if __name__ == '__main__':\n    pass\n",
        "two.py": "",
    })  # fmt: skip

    assert main(["build", str(project)]) == 1

    assert "Multiple top-level modules discovered" in capsys.readouterr().err


@pytest.mark.parametrize("layout", ["flat", "src"])
def test_building_a_setuptools_project_leaves_its_tree_unchanged(
    tmp_path: Path, layout: str
) -> None:
    module = "src/st/__init__.py" if layout == "src" else "st/__init__.py"
    project = _write(tmp_path / "st", {
        "pyproject.toml": (
            '[project]\nname = "st"\nversion = "1.0"\n[project.scripts]\nst = "st:main"\n'
            '[build-system]\nrequires = ["setuptools>=69"]\n'
            'build-backend = "setuptools.build_meta"\n'
        ),
        module: "def main():\n    print('ok')\n",
    })  # fmt: skip
    before = sorted(p.relative_to(project) for p in project.rglob("*"))

    assert main(["build", str(project), "-o", str(tmp_path / "st-bin")]) == 0

    assert sorted(p.relative_to(project) for p in project.rglob("*")) == before


def test_a_failed_build_leaves_no_empty_output_directory(tmp_path: Path) -> None:
    project = _fixture("hello", tmp_path, drop=("uv.lock",))

    assert main(["build", str(project), "--strict"]) == 1  # unlocked: a counted warning

    assert not (project / "dist").exists()


def test_a_failed_build_removes_every_output_directory_it_created(tmp_path: Path) -> None:
    project = _fixture("hello", tmp_path, drop=("uv.lock",))
    out = tmp_path / "new" / "sub" / "hello"

    assert main(["build", str(project), "-o", str(out), "--strict"]) == 1

    assert not (tmp_path / "new").exists()


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
    uv = requires.tool("uv")
    root = _write(tmp_path / "ws", {
        "pyproject.toml": '[tool.uv.workspace]\nmembers = ["packages/*"]\n',
        "packages/app/pyproject.toml": _uv_project(
            "app", '"lib"', '[project.scripts]\napp = "app:main"\n[tool.uv.sources]\n'
            "lib = { workspace = true }"),
        "packages/app/src/app/__init__.py": "import lib\ndef main():\n    print(lib.VALUE)\n",
        "packages/lib/pyproject.toml": _uv_project("lib"),
        "packages/lib/src/lib/__init__.py": "VALUE = 'first'\n",
    })  # fmt: skip
    subprocess.run([uv, "lock", "--quiet", "--project", root], check=True)
    member = root / "packages" / "app"
    out = tmp_path / "app"

    assert main(["build", str(member), "-o", str(out)]) == 0
    assert "from uv.lock" in capsys.readouterr().out
    assert _run(out).stdout == "first\n"
    (root / "packages" / "lib" / "src" / "lib" / "__init__.py").write_text("VALUE = 'second'\n")
    assert main(["build", str(member), "-o", str(out)]) == 0
    assert _run(out).stdout == "second\n"


def test_default_dependency_groups_are_not_packaged(tmp_path: Path) -> None:
    uv = requires.tool("uv")
    groups = (
        '[project.scripts]\ngrp = "grpapp:main"\n[dependency-groups]\nlint = ["six"]\n'
        '[tool.uv]\ndefault-groups = ["lint"]'
    )
    project = _write(tmp_path / "grpapp", {
        "pyproject.toml": _uv_project("grpapp", "", groups),
        "src/grpapp/__init__.py": "def main():\n    print('grp')\n",
    })  # fmt: skip
    subprocess.run([uv, "lock", "--quiet", "--project", project], check=True)

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


def test_a_packaged_poetry_1_project_is_locked_by_its_poetry_lock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """lock-version 2.0 names no group: the main packages are those the pyproject reaches."""
    project = tmp_path / "poetry1"
    shutil.copytree(FIXTURES / "foreign" / "poetry1", project)
    out = tmp_path / "bin" / "foreign-poetry1"

    assert main(["build", str(project), "-o", str(out)]) == 0

    err = capsys.readouterr().err
    assert "unlocked" not in err and "out of date" not in err
    shown = _run(out).stdout.splitlines()
    assert shown == ["click 8.5.0", "rich 15.0.0", "markdown-it-py 4.2.0", "mdurl 0.1.2",
                     "pygments 2.21.0", "colorama absent", "packaging absent",
                     "iniconfig absent"]  # fmt: skip
    assert main(["inspect", "--json", str(out)]) == 0
    lock = json.loads(capsys.readouterr().out)["build_info"]["lock"]
    assert (lock["kind"], lock["hashed"]) == ("poetry.lock", True)


def test_an_unpackaged_poetry_1_project_is_built_as_a_project_without_a_build_system(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """[tool.poetry] package-mode = false and no [project]: main.py runs, the lock installs."""
    project = tmp_path / "poetry1-unpackaged"
    shutil.copytree(FIXTURES / "foreign" / "poetry1-unpackaged", project)
    out = tmp_path / "bin" / "app"

    assert main(["build", str(project), "-o", str(out)]) == 0

    err = capsys.readouterr().err
    assert "unlocked" not in err and "out of date" not in err
    assert _run(out).stdout.splitlines() == [
        "click 8.5.0", "rich 15.0.0", "markdown-it-py 4.2.0", "mdurl 0.1.2", "pygments 2.21.0",
        "colorama absent", "packaging absent", "iniconfig absent"]  # fmt: skip
    assert main(["inspect", "--json", str(out)]) == 0
    lock = json.loads(capsys.readouterr().out)["build_info"]["lock"]
    assert (lock["kind"], lock["hashed"]) == ("poetry.lock", True)


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


_SETUPTOOLS = (
    '[build-system]\nrequires = ["setuptools>=69"]\nbuild-backend = "setuptools.build_meta"\n'
)
_MAIN_GUARD = "def main():\n    print('ran', __name__)\n\nif __name__ == '__main__':\n    main()\n"


def test_a_project_with_a_main_package_and_no_scripts_runs_as_python_m(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _write(tmp_path / "mp", {
        "pyproject.toml": '[project]\nname = "mp"\nversion = "1.0"\n' + _SETUPTOOLS,
        "src/mp/__init__.py": "",
        "src/mp/__main__.py": "import sys\nprint('main', __name__, sys.argv[1:])\n",
    })  # fmt: skip

    assert main(["build", str(project)]) == 0

    result = _run(project / "dist" / "mp", "a")
    assert (result.returncode, result.stdout) == (0, "main __main__ ['a']\n")
    assert "mp has no console scripts: package mp is the only one with __main__.py" in (
        capsys.readouterr().out
    )


def test_a_single_guarded_module_with_no_scripts_builds_and_runs(tmp_path: Path) -> None:
    project = _write(tmp_path / "solo", {
        "pyproject.toml": (
            '[project]\nname = "solo"\nversion = "1.0"\n' + _SETUPTOOLS
            + '[tool.setuptools]\npy-modules = ["solo"]\n'
        ),
        "solo.py": _MAIN_GUARD,
    })  # fmt: skip

    assert main(["build", str(project)]) == 0

    result = _run(project / "dist" / "solo")
    assert (result.returncode, result.stdout) == (0, "ran __main__\n")


def test_a_main_package_and_a_guarded_module_are_ambiguous(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _write(tmp_path / "both", {
        "pyproject.toml": (
            '[project]\nname = "both"\nversion = "1.0"\n' + _SETUPTOOLS
            + '[tool.setuptools]\npackages = ["pkg"]\npy-modules = ["tool"]\n'
        ),
        "pkg/__init__.py": "",
        "pkg/__main__.py": "",
        "tool.py": _MAIN_GUARD,
    })  # fmt: skip

    assert main(["build", str(project)]) == 1

    err = capsys.readouterr().err
    assert "pkg (package with __main__.py), tool (module with a main guard)" in err


def test_a_static_project_with_no_scripts_and_no_candidate_fails_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _write(tmp_path / "lib", {
        "pyproject.toml": '[project]\nname = "lib"\nversion = "1.0"\n' + _SETUPTOOLS,
        "src/lib/__init__.py": "",
        "src/lib/util.py": "def f():\n    pass\n",
    })  # fmt: skip
    monkeypatch.setattr("fsbpyr.build.find_uv", lambda: pytest.fail("uv was looked for"))

    assert main(["build", str(project)]) == 1

    assert "has console scripts: none; choose one with --entry" in capsys.readouterr().err
    assert not (project / "dist").exists()


def test_a_setuptools_scripts_file_is_reported_as_not_packaged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _write(tmp_path / "legacy", {
        "pyproject.toml": (
            '[project]\nname = "legacy"\nversion = "1.0"\n'
            '[project.scripts]\nlegacy = "legacy:main"\n'
            + _SETUPTOOLS
        ),
        "setup.py": "from setuptools import setup\nsetup(scripts=['bin/old-tool'])\n",
        "bin/old-tool": "#!/bin/sh\necho old\n",
        "legacy/__init__.py": "def main():\n    print('new')\n",
    })  # fmt: skip

    assert main(["build", str(project)]) == 0

    err = capsys.readouterr().err
    assert "legacy: legacy script old-tool is not packaged" in err
    assert "legacy: legacy script legacy " not in err


_LEGACY_TOOL = (
    "#!/usr/bin/env python\nimport sys\nimport lg\n"
    "import os\nprint(__name__, lg.VALUE, sys.argv[1:], "
    "sys.path[0] == os.path.dirname(__file__), '_fsbpyr_scripts' not in __file__)\n"
)


def _contained(command: list[Path | str]) -> subprocess.CompletedProcess[str]:
    return contain.run(command, env=os.environ, timeout=60)


def _legacy_project(root: Path, scripts: dict[str, str], *, console: bool = False) -> Path:
    """A setuptools project ``lg`` whose setup.py installs ``scripts`` (bin/ files) as legacy
    scripts, and with ``console`` a console script ``lg-cli`` as well."""
    entry = '\n[project.scripts]\nlg-cli = "lg:main"\n' if console else ""
    listed = [f"bin/{name}" for name in scripts]
    return _write(root, {
        "pyproject.toml": '[project]\nname = "lg"\nversion = "1.0"\n' + entry + _SETUPTOOLS,
        "setup.py": f"from setuptools import setup\nsetup(scripts={listed})\n",
        "lg/__init__.py": "VALUE = 7\n\ndef main():\n    print('console')\n",
        **{f"bin/{name}": text for name, text in scripts.items()},
    })  # fmt: skip


def test_a_legacy_python_script_is_the_only_command_and_runs_as_python_runs_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _legacy_project(tmp_path / "lg", {"tool": _LEGACY_TOOL})

    assert main(["build", str(project)]) == 0

    result = _contained([project / "dist" / "tool", "a", "-b"])
    assert (result.returncode, result.stdout) == (0, "__main__ 7 ['a', '-b'] True True\n")
    assert "legacy script" not in capsys.readouterr().err
    assert [p.name for p in (project / "dist").iterdir()] == ["tool"]


def test_legacy_scripts_and_console_scripts_are_commands_of_one_executable(
    tmp_path: Path,
) -> None:
    project = _legacy_project(
        tmp_path / "lg", {"tool": _LEGACY_TOOL, "other-tool": _LEGACY_TOOL}, console=True
    )

    assert main(["build", str(project), "-o", str(tmp_path / "out" / "lg")]) == 0

    out = tmp_path / "out"
    assert sorted(p.name for p in out.iterdir()) == ["lg", "lg-cli", "other-tool", "tool"]
    assert [os.readlink(out / name) for name in ("lg-cli", "other-tool", "tool")] == ["lg"] * 3
    assert _contained([out / "lg-cli"]).stdout == "console\n"
    for name in ("tool", "other-tool"):
        result = _contained([out / name, "x"])
        assert result.stdout == "__main__ 7 ['x'] True True\n"
    assert _contained([out / "lg", "tool", "y"]).stdout.startswith("__main__ 7 ['y']")


def test_install_links_alias_a_legacy_script_but_not_a_console_script_and_argv0_is_the_link(
    tmp_path: Path,
) -> None:
    project = _legacy_project(
        tmp_path / "lg", {"tool": "#!/usr/bin/env python\nimport sys\nprint(sys.argv[0])\n"},
        console=True,
    )
    assert main(["build", str(project), "-o", str(tmp_path / "out" / "lg")]) == 0
    target = tmp_path / "bin"
    target.mkdir()

    result = contain.run(
        [(tmp_path / "out" / "lg").resolve()],
        env={**os.environ, "FSBPYR_INSTALL_LINKS": str(target)}, timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in target.iterdir()) == ["lg-cli", "tool", "tool.py"]
    for name in ("tool", "tool.py"):
        assert _contained([target / name]).stdout == f"{target / name}\n"


def test_a_legacy_shell_script_still_warns_and_the_python_one_is_packaged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _legacy_project(
        tmp_path / "lg", {"tool": _LEGACY_TOOL, "old-tool": "#!/bin/sh\necho old\n"}
    )

    assert main(["build", str(project)]) == 0

    err = capsys.readouterr().err
    assert "lg: legacy script old-tool is not packaged" in err
    assert "legacy script tool " not in err
    assert [p.name for p in (project / "dist").iterdir()] == ["tool"]


def test_a_legacy_python_2_script_is_skipped_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _legacy_project(
        tmp_path / "lg", {"old": "#!/usr/bin/env python\nprint 'hi'\n"}, console=True
    )

    assert main(["build", str(project)]) == 0

    err = capsys.readouterr().err
    assert "lg: legacy script old is not packaged: it does not compile" in err
    assert [p.name for p in (project / "dist").iterdir()] == ["lg-cli"]


def test_a_legacy_script_named_like_a_console_script_fails_naming_both(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _legacy_project(tmp_path / "lg", {"lg-cli": _LEGACY_TOOL}, console=True)

    assert main(["build", str(project)]) == 1

    err = capsys.readouterr().err
    assert "'lg-cli' is both a console script (lg:main) and a legacy script (.data/scripts/" in err
    assert not (project / "dist").exists()


def test_compile_warnings_of_the_projects_own_files_are_build_warnings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _fixture("escapes", tmp_path)

    assert main(["build", str(project), "--no-smoke"]) == 0

    err = capsys.readouterr().err
    assert "fsbpyr: warning: escapes/cli.py:5: " in err and "invalid escape sequence" in err
    assert _run(project / "dist" / "escapes").stdout == "escapes ['1', '22']\n"  # still runs


def test_strict_counts_a_compile_warning_of_the_projects_own_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _fixture("escapes", tmp_path)

    assert main(["build", str(project), "--strict", "--no-smoke"]) == 1

    assert "escapes/cli.py:5: " in capsys.readouterr().err
    assert not (project / "dist" / "escapes").exists()
