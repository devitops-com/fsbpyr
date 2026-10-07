"""Resources extracted on demand (D27) into the app cache, in-process against the stand-in."""

import importlib
import json
import os
from pathlib import Path
from types import ModuleType

import pytest

from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import FileKind
from fsbpyr.payload.spec import Blob, File

from .conftest import FAKE_RUNTIME

DIRECTORIES = 300  # more than a low RLIMIT_NOFILE leaves free if every directory stayed open


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    files = [File(f"many/d{i:03}/f.txt", FileKind.RESOURCE, Blob.zstd(b"%d" % i))
             for i in range(DIRECTORIES)]  # fmt: skip
    out = tmp_path_factory.mktemp("tree") / "tree-demo"
    build_executable(FAKE_RUNTIME, App("tree-demo", "many", files=tuple(files)), out)
    return out


@pytest.fixture
def native(boot: ModuleType) -> ModuleType:
    return importlib.import_module("_fsbpyr_rt.native")


def _open_descriptors() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_large_trees_keep_few_directories_open(
    native: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline, peak = _open_descriptors(), [0]
    open_child = native._open_child

    def measured(parent: int, name: str, path: str) -> int:
        peak[0] = max(peak[0], _open_descriptors())
        return int(open_child(parent, name, path))

    monkeypatch.setattr(native, "_open_child", measured)

    directory = Path(native.materialize("many"))

    assert len(list(directory.iterdir())) == DIRECTORIES
    assert peak[0] - baseline < 16  # the chain from the cache root down, not every directory
    assert _open_descriptors() == baseline


def test_verify_repairs_resources_without_a_digest(
    native: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = Path(native.materialize("many/d007/f.txt"))
    copy.chmod(0o600)
    copy.write_bytes(b"9")  # same size, other content
    native._MATERIALIZED.clear()  # a new process

    assert Path(native.materialize("many/d007/f.txt")).read_bytes() == b"9"  # size check only
    monkeypatch.setenv("FSBPYR_VERIFY", "1")
    assert Path(native.materialize("many/d007/f.txt")).read_bytes() == b"7"


@pytest.mark.parametrize("build_info", ["{}", '{"resources": "../x"}', "not json"])
def test_a_corrupt_resources_id_is_reported(
    native: ModuleType, boot: ModuleType, monkeypatch: pytest.MonkeyPatch, build_info: str
) -> None:
    standin = importlib.import_module("_fsbpyr")
    info = standin.info()
    monkeypatch.setattr(standin, "info", lambda: {**info, "build_info": build_info})

    with pytest.raises(OSError, match="corrupt executable payload"):
        native.materialize("many/d001/f.txt")
    assert json.loads(boot.INFO["build_info"])["resources"]  # the real one is intact
