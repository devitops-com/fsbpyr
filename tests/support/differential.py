"""Does the built executable behave like the original application? (TST-2)

``run_both`` runs one fixture command twice, with the same environment and working directory:
the console script of a venv made with staging's sync flags (staging.py `_uv_sync_plan`, D5),
and the built executable. It returns both outcomes; ``assert_same`` compares return code, stdout
and stderr. Only documented differences are normalized: the venv's and the executable's own
paths. A fixture command that prints sys.path, sys.prefix, sys.flags (D14), a module's
``__file__`` prefix or ``sys.frozen`` (D21) would need more; none does, so none is normalized.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from fsbpyr.staging import find_uv

from .stderr import ONNX_HOST

TIMEOUT = 300  # the first sync of a project builds and downloads its wheels


@dataclass(frozen=True)
class Outcome:
    returncode: int
    stdout: str
    stderr: str


def make_venv(project: Path, venv: Path, python: str) -> Path:
    """The staging environment of a uv.lock project: its wheel (not an editable copy) and its
    locked dependencies, without dev groups, on the runtime's Python (not whichever
    version the project's ``requires-python`` lets uv pick)."""
    command = [str(find_uv()), "sync", "--locked", "--no-dev", "--no-default-groups"]
    command += ["--no-editable", "--python", python, "--project", str(project), "--quiet"]
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(venv)}
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=TIMEOUT,
                            check=False)  # fmt: skip
    assert result.returncode == 0, result.stderr
    return venv


def _environment(env: dict[str, str] | None) -> dict[str, str]:
    """The caller's environment without the trace, whose line would land on stderr."""
    merged = {**os.environ, **(env or {})}
    merged.pop("FSBPYR_TRACE", None)
    return merged


def _run(command: list[str | Path], env: dict[str, str], cwd: Path | None) -> Outcome:
    result = subprocess.run(command, env=env, cwd=cwd, capture_output=True, text=True,
                            timeout=TIMEOUT, check=False)  # fmt: skip
    return Outcome(result.returncode, result.stdout, result.stderr)


def run_both(
    venv: Path,
    executable: Path,
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    script: str | None = None,
) -> tuple[Outcome, Outcome]:
    """(the venv's run, the executable's run) of ``argv``; the console script is named like the
    executable unless ``script`` says otherwise, and runs under ``python -I -X utf8``, as built
    apps do (D14)."""
    environment = _environment(env)
    console = venv / "bin" / (script or executable.name)
    assert console.is_file(), f"{console.name}: no such console script in {venv}"
    original = _run([venv / "bin" / "python", "-I", "-X", "utf8", console, *argv], environment, cwd)
    built = _run([executable, *argv], environment, cwd)
    return _normalized(original, venv, executable), _normalized(built, venv, executable)


def _normalized(outcome: Outcome, venv: Path, executable: Path) -> Outcome:
    def clean(text: str) -> str:
        return text.replace(str(venv), "<venv>").replace(str(executable), "<executable>")

    # onnxruntime's host warning carries a timestamp, so the two runs' lines never match
    return Outcome(
        outcome.returncode, clean(outcome.stdout), clean(ONNX_HOST.sub("", outcome.stderr))
    )


def assert_same(
    venv: Path,
    executable: Path,
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    script: str | None = None,
) -> Outcome:
    """Fails unless the executable and the venv's console script agree on return code, stdout
    and stderr; returns the (shared) outcome so a check can add its own assertions."""
    original, built = run_both(venv, executable, argv, env=env, cwd=cwd, script=script)
    assert built == original, f"{' '.join(argv)}: the executable differs from the venv's run"
    return built
