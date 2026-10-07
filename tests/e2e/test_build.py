"""Phase 4 exit criteria: `fsbpyr build` on real uv/pip projects, and self-hosting.

Needs the cached runtime and, on the first run, network access for the fixtures' packages
(uv and pip caches afterwards).
"""

import itertools
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fsbpyr.cli import main
from fsbpyr.runtime.registry import Runtime

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "projects"
UV = shutil.which("uv")


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
    return subprocess.run([executable, *args], capture_output=True, text=True, check=False)


def _tree(directory: Path) -> dict[str, float]:
    return {str(p): p.lstat().st_mtime for p in directory.rglob("*")}


def test_hello_from_uv_lock(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _fixture("hello", tmp_path)

    assert main(["build", str(project)]) == 0

    executable = project / "dist" / "hello"
    result = _run(executable, "5", "x y")
    assert (result.returncode, result.stdout) == (5, "hello ['5', 'x y']\n")
    assert f"built {executable}" in capsys.readouterr().out


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


def test_pip_projects_build_without_uv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _fixture("clicky", tmp_path, drop=("uv.lock", "pylock.toml"))
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", venv], check=True)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # no uv here
    out = tmp_path / "out" / "clicky"
    python = str(venv / "bin" / "python")

    status = main(["build", str(project), "-o", str(out), "--interpreter", python])

    assert status == 0, capsys.readouterr().err
    assert _run(out, "pip").stdout.splitlines()[0] == "Hello, pip!"


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
    """stage1 (venv) -> stage2 -> stage3 with only uv on PATH and an empty cache."""
    assert UV is not None
    env = {**os.environ, "PATH": str(Path(UV).parent), "FSBPYR_CACHE_DIR": str(tmp_path / "c")}
    venv_before = _tree(ROOT / ".venv")
    stages = [tmp_path / f"stage{n}" for n in (1, 2, 3)]

    assert main(["build", str(ROOT), "-o", str(stages[0])]) == 0
    for builder, target in itertools.pairwise(stages):
        result = subprocess.run(
            [builder, "build", ROOT, "-o", target],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    data = [stage.read_bytes() for stage in stages]
    assert data[1] == data[2], "stage2 and stage3 differ"
    assert data[0] == data[1], "stage1 and stage2 differ"
    assert not (tmp_path / "c" / "runtimes").exists()  # the binary used its own runtime
    assert _tree(ROOT / ".venv") == venv_before


def test_self_hosted_binary_builds_offline_without_clang(tmp_path: Path) -> None:
    assert UV is not None
    builder = tmp_path / "fsbpyr"
    assert main(["build", str(ROOT), "-o", str(builder)]) == 0
    project = _fixture("clicky", tmp_path)
    main(["build", str(project), "--no-smoke"])  # warm the uv cache for this project
    env = {**os.environ, "PATH": str(Path(UV).parent), "FSBPYR_CACHE_DIR": str(tmp_path / "c")}

    result = subprocess.run(
        [builder, "build", project, "--offline", "-o", tmp_path / "offline"],
        env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip

    assert result.returncode == 0, result.stderr
    assert _run(tmp_path / "offline", "x").stdout.startswith("Hello, x!")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (('requires-python = ">=3.12"', 'requires-python = "<3.13"'), "requires-python"),
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
    fsbpyr = ROOT / ".venv" / "bin" / "fsbpyr"
    outputs = []
    for optimize in ("", "2"):
        out = tmp_path / f"hello-{optimize or 'plain'}"
        env = {**os.environ, "PYTHONOPTIMIZE": optimize} if optimize else dict(os.environ)
        subprocess.run([fsbpyr, "build", project, "-o", out, "--no-smoke"], env=env, check=True,
                       capture_output=True)  # fmt: skip
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
