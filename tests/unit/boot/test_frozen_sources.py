"""Sources of CPython-frozen stdlib modules come from the payload (inspect.getsource)."""

import dataclasses
import importlib.machinery
from pathlib import Path
from types import ModuleType

import pytest

from fsbpyr import bytecode
from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import FileKind, ModuleKind
from fsbpyr.payload.spec import Blob, File, Module

from .conftest import FAKE_RUNTIME

OS_SOURCE = "X = 'os from the payload'\n"


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    source = Blob.zstd(OS_SOURCE.encode())
    code = Blob.code(bytecode.dumps(compile(OS_SOURCE, "os.py", "exec", dont_inherit=True)))
    runtime = dataclasses.replace(
        FAKE_RUNTIME,
        modules=(Module("os", "os.py", ModuleKind.MODULE, code=code, source=source),),
        files=(File("os.py", FileKind.SOURCE, source),),
    )
    out = tmp_path_factory.mktemp("frozen") / "app"
    build_executable(runtime, App("app", "os", ()), out)
    return out


def test_frozen_stdlib_sources_fall_back_to_the_payload(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen = importlib.machinery.FrozenImporter
    monkeypatch.setattr(frozen, "get_source", frozen.get_source)  # restored afterwards
    assert frozen.get_source("os") is None  # CPython keeps no source for frozen modules

    boot._install_frozen_sources()

    assert frozen.get_source("os") == OS_SOURCE
    assert frozen.get_source("zipimport") is None  # frozen, but not in this payload
    with pytest.raises(ImportError):
        frozen.get_source("json")  # not frozen at all: CPython's own error
