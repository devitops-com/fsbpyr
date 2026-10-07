"""Shared fixture: the boot module run in-process against the pure-Python _fsbpyr stand-in.

Each test module provides an ``executable`` fixture (the app the stand-in serves).
"""

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from fsbpyr.runtime.registry import Runtime

from ...support.standin import StandIn

BOOT_DIR = Path(__file__).parents[3] / "src" / "fsbpyr" / "boot"
FAKE_RUNTIME = Runtime(
    tag="cp314-x86_64-unknown-linux-gnu",
    runtime_id="cp314-x86_64-unknown-linux-gnu-test",
    python_version="3.14.8",
    hexversion=0x030E08F0,
    bytecode_magic=importlib.util.MAGIC_NUMBER,
    extension_suffixes=(".cpython-314-x86_64-linux-gnu.so", ".abi3.so", ".so"),
    stub=b"\x7fELF-fake",
    modules=(),
    files=(),
)


@pytest.fixture
def boot(executable: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    standin = StandIn(executable)
    monkeypatch.setitem(sys.modules, "_fsbpyr", standin)
    monkeypatch.syspath_prepend(str(BOOT_DIR))
    for name in [n for n in sys.modules if n == "_fsbpyr_rt" or n.startswith("_fsbpyr_rt.")]:
        monkeypatch.delitem(sys.modules, name)
    spec = importlib.util.spec_from_file_location("_fsbpyr_boot", BOOT_DIR / "_fsbpyr_boot.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    for name in [n for n in sys.modules if n == "_fsbpyr_rt" or n.startswith("_fsbpyr_rt.")]:
        del sys.modules[name]
    standin.close()
