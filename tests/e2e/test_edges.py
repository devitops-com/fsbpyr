"""The launcher on every edge case of the format (D38, tests/support/edges.py), release and
sanitized: it refuses to start (exit 70, "fsbpyr: cannot start ..."), refuses when the record is
used (an exception from ``_fsbpyr``), or accepts; it never crashes, and the sanitized launcher
meets no memory error and no undefined behaviour on the way."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from fsbpyr.payload import writer
from fsbpyr.payload.format import FileKind
from fsbpyr.payload.spec import Blob, File, Module
from fsbpyr.runtime.registry import Runtime

from ..support import edges
from .conftest import app_content

pytestmark = [pytest.mark.slow, pytest.mark.sanitized]

SUPPORT = Path(__file__).parents[1] / "support"
WALKER = """\
import json
import _fsbpyr
import walk

def main():
    print(json.dumps(walk.run(_fsbpyr)))
"""
LAUNCHED = [case for case in edges.CASES if case.launcher is not None]


def _walker() -> tuple[tuple[Module, ...], tuple[File, ...]]:
    """The entry point that walks the executable it runs in, and the walk."""
    return app_content({"walker.py": WALKER, "walk.py": (SUPPORT / "walk.py").read_text()})


@pytest.fixture(scope="module")
def intact(runtime: Runtime, tmp_path_factory: pytest.TempPathFactory) -> bytes:
    modules, files = _walker()
    out = tmp_path_factory.mktemp("edges") / "edge"
    return edges.build(runtime, out, modules, files, "walker:main").read_bytes()


def _outcome(app: Path) -> str:
    result = subprocess.run(
        [app], capture_output=True, text=True, check=False, timeout=60,
        env={**os.environ, "FSBPYR_CACHE_DIR": os.fspath(app.parent / "cache")},
    )  # fmt: skip
    assert result.returncode >= 0, f"killed by signal {-result.returncode}: {result.stderr}"
    assert "Sanitizer" not in result.stderr and "runtime error" not in result.stderr, result.stderr
    if result.returncode == 70:
        assert result.stderr.startswith("fsbpyr: cannot start "), result.stderr
        return "start"
    if result.returncode == 0:
        return "access" if edges.refused(json.loads(result.stdout)) else "accept"
    # refused outside the walk (importing it, say): Python's traceback names the refusal
    assert result.returncode == 1 and any(name in result.stderr for name in edges.REFUSALS), (
        result.stderr
    )
    return "access"


def test_the_intact_executable_is_walked_without_a_refusal(intact: bytes, tmp_path: Path) -> None:
    app = tmp_path / "edge"
    app.write_bytes(intact)
    app.chmod(0o755)

    assert _outcome(app) == "accept"


@pytest.mark.parametrize("case", LAUNCHED, ids=lambda case: case.id)
def test_the_launcher_refuses_or_reads_each_edge_case(
    intact: bytes, case: edges.Case, tmp_path: Path
) -> None:
    app = tmp_path / "edge"
    app.write_bytes(edges.apply(case, intact))
    app.chmod(0o755)

    assert _outcome(app) == case.launcher


@pytest.mark.parametrize("case", edges.PATH_CASES, ids=lambda case: case.id)
def test_the_launcher_refuses_paths_a_writer_never_writes(
    runtime: Runtime, case: edges.PathCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(writer, "check_path", lambda path: None)  # build what must be refused
    native = File(case.path, FileKind.NATIVE_LIB, Blob.zstd(b"\x7fELF p" * 99, digest=True),
                  mode=0o755)  # fmt: skip
    modules, files = _walker()
    app = edges.build(runtime, tmp_path / "edge", modules, (*files, native), "walker:main")

    assert _outcome(app) == case.launcher
