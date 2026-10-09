"""Phase 3 exit criteria on a real executable: import system, resources, metadata, contract."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from fsbpyr.assemble import build_executable
from fsbpyr.payload.reader import Executable
from fsbpyr.runtime.registry import Runtime

from ..support import contract
from ..support.apps import demo_app
from ..support.standin import StandIn

pytestmark = [pytest.mark.slow, pytest.mark.sanitized]


@pytest.fixture(scope="module")
def demo(runtime: Runtime, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("demo") / "demo"
    build_executable(runtime, demo_app(), out)
    return out.resolve()


@pytest.fixture(scope="module")
def probe(demo: Path) -> dict[str, Any]:
    result = subprocess.run([demo], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def test_c_module_and_standin_honour_the_same_contract(demo: Path) -> None:
    result = subprocess.run([demo, "contract"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    standin = StandIn(demo)
    try:
        expected = json.loads(json.dumps(contract.run(standin)))
    finally:
        standin.close()

    assert json.loads(result.stdout) == expected
    held = expected["sync"]["limited"][0][2:]  # both equal, and as they should be: (D36)
    assert held == [[["ok", []], ""]] * 2  # checked, through a pass's files held open
    # a name the file system refuses fails the sync, and nothing is reported as modified (D38)
    assert expected["sync"]["name too long"] == [["raises", "OSError", "ENAMETOOLONG"], ""]


@pytest.mark.skipif(shutil.which("strace") is None, reason="needs strace")
def test_extracted_files_are_never_synced_to_disk(demo: Path, tmp_path: Path) -> None:
    """D34: every process checks a cached file against its digest before it uses it and
    replaces it if it differs, so a file a crash left incomplete is repaired; syncing each file
    only cost time (torch's 12,151 files: 42.5 s instead of 2.3 s on the first run)."""
    log = tmp_path / "strace.log"
    calls = "fsync,fdatasync,sync,syncfs,sync_file_range"
    command = ["strace", "-f", "-qq", "-o", str(log), "-e", f"trace={calls}", str(demo), "contract"]

    result = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["sync"]["natives"]["first"][0][0] == "ok"
    assert log.read_text() == ""


def test_boot_imports_only_builtin_frozen_and_startup_modules(
    demo: Path, probe: dict[str, Any]
) -> None:
    with Executable.open(demo) as exe:
        startup = {m.name for m in exe.payload.startup_modules()}
    allowed = set(probe["builtin_or_frozen"]) | startup | {"__main__", "entry"}

    assert sorted(set(probe["boot_modules"]) - allowed) == []


def test_importlib_resources(probe: dict[str, Any]) -> None:
    assert probe["res_iterdir"] == ["__init__.py", "cli.py", "data", "sub"]
    assert probe["res_data"] == ["sub", "table.json"]
    assert probe["res_read"] == probe["as_file"] == probe["read_text_api"] == '{"k": 1}'
    assert probe["res_read_bytes"] == "x"
    assert probe["res_is"] == [True, False, True, False, False]
    assert "cli.py" in probe["module_anchor"]
    assert probe["res_namespace"] == ["a.py", "b.py"]
    assert probe["res_data_package"] == '{"k": 1}'


def test_packages_namespaces_and_pkgutil(probe: dict[str, Any]) -> None:
    assert probe["get_data"] == '{"k": 1}'
    assert probe["iter_pkg"] == [["pkg.cli", False], ["pkg.sub", True]]
    assert probe["iter_ns"] == ["a", "b"]
    assert probe["iter_top"] == ["json", "pkg", "probe"]  # like FileFinder: no namespace dirs
    assert probe["walk"] == ["pkg.cli", "pkg.sub", "pkg.sub.deep"]
    assert probe["find_spec"] == [True, True]
    assert probe["ns"] == [True, "a", "b"]
    assert probe["file"] == [True, True, True]


def test_importlib_metadata(probe: dict[str, Any]) -> None:
    assert probe["version"] == ["1.0", "1.0"]
    assert probe["metadata_name"] == "Demo"
    assert "demo=pkg.cli:main" in probe["eps"]
    assert probe["dist_files"] == ["pkg/__init__.py", "pkg/cli.py", "pkg/data/table.json"]
    assert probe["dist_file_read"] == ['{"k": 1}']
    assert probe["missing_dist"] == "missing"


def test_source_backed_tracebacks_and_inspect(probe: dict[str, Any]) -> None:
    assert probe["getsource"] == "def main():\n    return 'cli'\n"
    assert probe["co_filename"] is True
    assert probe["linecache"] == "return 'cli'"
    assert probe["startup_filename"] == "<frozen encodings>"


def test_cwd_files_never_shadow_payload_sources(demo: Path, tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "cli.py").write_text("SHADOW\n" * 5)

    result = subprocess.run([demo], capture_output=True, text=True, check=False, cwd=tmp_path)

    assert json.loads(result.stdout)["getsource"] == "def main():\n    return 'cli'\n"
