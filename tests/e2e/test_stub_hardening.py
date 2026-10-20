"""The built stub keeps its hardening flags (LINK_FLAGS and compile_flags in runtime/stub.py).

Reads the stub with binutils' readelf and objdump, which CI already has as a dependency of
clang.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fsbpyr.runtime.registry import Runtime

READELF = shutil.which("readelf")
OBJDUMP = shutil.which("objdump")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (READELF and OBJDUMP), reason="binutils not installed"),
]


def _tool(*command: str | Path) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def stub_path(runtime: Runtime, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("stub") / "stub"
    path.write_bytes(runtime.stub)
    return path


@pytest.fixture(scope="module")
def headers(stub_path: Path) -> str:
    assert READELF
    return _tool(READELF, "-lW", "-dW", stub_path)


def test_stub_is_position_independent(headers: str) -> None:
    flags_1 = re.search(r"\(FLAGS_1\)\s+Flags:(.*)", headers)

    assert flags_1 is not None and "PIE" in flags_1.group(1).split()


def test_stub_binds_symbols_at_load_time(headers: str) -> None:
    assert re.search(r"\(FLAGS\)\s+.*\bBIND_NOW\b", headers)


def test_stub_has_a_relro_segment(headers: str) -> None:
    assert re.search(r"^\s*GNU_RELRO\s", headers, re.MULTILINE)


def test_stub_stack_is_not_executable(headers: str) -> None:
    stack = re.search(r"^\s*GNU_STACK\s.*$", headers, re.MULTILINE)

    assert stack is not None
    assert "E" not in stack.group(0).split()[-2]


def test_stub_uses_stack_protector(stub_path: Path) -> None:
    assert OBJDUMP

    assert "__stack_chk_fail" in _tool(OBJDUMP, "-T", stub_path)
