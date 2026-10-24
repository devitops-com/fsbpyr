"""Does the built executable behave like the original application? (TST-2)

``run_both`` runs one fixture command twice, with the same environment and working directory:
the console script of a venv made with staging's sync flags (staging.py `_uv_sync_plan`, D5),
and the built executable. It returns both outcomes; ``assert_same`` compares return code, stdout
and stderr. Only documented differences are normalized: the venv's and the executable's own
paths, and the directory a module's file is named in (a warning's location, a traceback's
frames): the venv's site-packages, the executable (``<executable>/pkg/mod.py``) and, for the
modules of fs-packages, which are extracted, the cache's site directory
(``$FSBPYR_CACHE_DIR/runtime/<generation>/site``) are all ``<site>``. A fixture command that
prints sys.path, sys.prefix, sys.flags (D14) or ``sys.frozen`` (D21) would need more; none
does, so none is normalized.

Both runs are contained (tests/support/contain.py) and get the same few threads per BLAS and
OpenMP pool, so that parallel tests do not each start a thread per core. With ``home`` each
run gets an empty home directory of its own (``HOME`` and the XDG base directories), written
as ``<home>``: what a library keeps there (a font cache, a cpuinfo file) neither reaches the
user's home nor changes the other run's output.
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from fsbpyr.staging import find_uv

from . import contain
from .stderr import ONNX_HOST

TIMEOUT = 300  # the first sync of a project builds and downloads its wheels
COMMAND_TIMEOUT = 120  # one run of a command
THREADS = {name: "2" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                                  "NUMEXPR_NUM_THREADS", "LOKY_MAX_CPU_COUNT")}  # fmt: skip
XDG = {"XDG_CACHE_HOME": ".cache", "XDG_CONFIG_HOME": ".config", "XDG_DATA_HOME": ".local/share",
       "XDG_STATE_HOME": ".local/state"}  # fmt: skip


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
    """The caller's environment with THREADS (the caller's own values win), without the
    trace, whose line would land on stderr, and without what belongs to the test run itself:
    its pytest variables (PYTEST_ADDOPTS, PYTEST_CURRENT_TEST, pytest-xdist's), which an app
    running pytest reads, and the VIRTUAL_ENV of the `uv run` around it, which IPython warns
    about."""
    merged = {**THREADS, **os.environ, **(env or {})}
    merged.pop("FSBPYR_TRACE", None)
    merged.pop("VIRTUAL_ENV", None)
    return {name: value for name, value in merged.items() if not name.startswith("PYTEST_")}


def _run(
    command: list[str | Path], env: dict[str, str], cwd: Path | None, stdin: str | None = None
) -> Outcome:
    result = contain.run(command, env=env, cwd=cwd, stdin=stdin, timeout=COMMAND_TIMEOUT)
    return Outcome(result.returncode, result.stdout, result.stderr)


def run_both(
    venv: Path,
    executable: Path,
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    script: str | None = None,
    stdin: str | None = None,
    home: Path | None = None,
) -> tuple[Outcome, Outcome]:
    """(the venv's run, the executable's run) of ``argv``, each reading ``stdin`` if given; the
    console script is named like the executable unless ``script`` says otherwise, and runs
    under ``python -I -X utf8``, as built apps do (D14). ``home``: the directory below which
    each run gets its empty home directory."""
    environment = _environment(env)
    console = venv / "bin" / (script or executable.name)
    assert console.is_file(), f"{console.name}: no such console script in {venv}"
    python: list[str | Path] = [venv / "bin" / "python", "-I", "-X", "utf8", console]
    homes = (None, None) if home is None else (home / "venv", home / "executable")
    original = _run([*python, *argv], _homed(environment, homes[0]), cwd, stdin)
    built = _run([executable, *argv], _homed(environment, homes[1]), cwd, stdin)
    paths = _Paths(venv, executable, environment.get("FSBPYR_CACHE_DIR"), homes)
    return _normalized(original, paths), _normalized(built, paths)


def _homed(env: dict[str, str], home: Path | None) -> dict[str, str]:
    """``env`` with ``home``, made empty, as HOME and the XDG base directories below it."""
    if home is None:
        return env
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True)
    return {**env, "HOME": str(home), **{name: str(home / sub) for name, sub in XDG.items()}}


@dataclass(frozen=True)
class _Paths:
    venv: Path
    executable: Path
    cache: str | None  # FSBPYR_CACHE_DIR of the runs
    homes: tuple[Path | None, Path | None]


def _normalized(outcome: Outcome, paths: _Paths) -> Outcome:
    sites = [f"{site}/" for site in (paths.venv / "lib").glob("python3.*/site-packages")]
    extracted = re.escape(paths.cache) + r"/runtime/[0-9a-f]+/site/" if paths.cache else None

    def clean(text: str) -> str:
        for site in (*sites, f"{paths.executable}/"):
            text = text.replace(site, "<site>/")
        if extracted:
            text = re.sub(extracted, "<site>/", text)
        for home in paths.homes:
            if home is not None:
                text = text.replace(str(home), "<home>")
        venv, executable = str(paths.venv), str(paths.executable)
        return text.replace(venv, "<venv>").replace(executable, "<executable>")

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
