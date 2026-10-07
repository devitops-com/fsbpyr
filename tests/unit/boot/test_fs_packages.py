"""FS_REQUIRED packages in-process: files on disk, code from the payload (plan §3.5)."""

import importlib
import importlib.resources
import inspect
import json
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from fsbpyr import bytecode
from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import FileFlag, FileKind, ModuleFlag, ModuleKind
from fsbpyr.payload.spec import Blob, File, Module

from .conftest import BOOT_DIR, FAKE_RUNTIME

INIT = "import os\nDATA = open(os.path.join(os.path.dirname(__file__), 'data', 'a.txt')).read()\n"
SOURCES = {"tpl/__init__.py": INIT, "tpl/sub.py": "X = 1\n", "plain/__init__.py": ""}
NATIVE = "tpl/lib/libx.so"  # native files below an FS package come along
DATA = {"tpl/data/a.txt": (b"A", 0o644), "tpl/data/deep/b.txt": (b"B", 0o644),
        "tpl/run.sh": (b"#!/bin/sh\n", 0o755)}  # fmt: skip


NO_FLAGS = ModuleFlag(0)


def _module(rel: str, text: str, flags: ModuleFlag = NO_FLAGS) -> tuple[Module, File]:
    name = rel.removesuffix(".py").removesuffix("/__init__").replace("/", ".")
    kind = ModuleKind.PACKAGE if rel.endswith("__init__.py") else ModuleKind.MODULE
    fs = rel.startswith("tpl/")
    source = Blob.zstd(text.encode(), digest=fs)
    code = Blob.code(bytecode.dumps(compile(text, rel, "exec", dont_inherit=True)))
    file_flags = FileFlag.FS_REQUIRED if fs else FileFlag(0)
    return (Module(name, rel, kind, code=code, source=source, flags=flags),
            File(rel, FileKind.SOURCE, source, flags=file_flags))  # fmt: skip


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    built = [
        _module(
            rel, text, ModuleFlag.FS_REQUIRED_ROOT if rel == "tpl/__init__.py" else ModuleFlag(0)
        )
        for rel, text in SOURCES.items()
    ]
    modules = [m for m, _ in built] + [
        Module(name, name.replace(".", "/"), ModuleKind.NAMESPACE)
        for name in ("tpl.data", "tpl.data.deep")
    ]
    files = [f for _, f in built] + [
        File(rel, FileKind.RESOURCE, Blob.zstd(data, digest=True), mode=mode,
             flags=FileFlag.FS_REQUIRED)
        for rel, (data, mode) in DATA.items()
    ] + [File("plain/x.txt", FileKind.RESOURCE, Blob.zstd(b"x")),
         File(NATIVE, FileKind.NATIVE_LIB, Blob.zstd(b"\x7fELF", digest=True),
              mode=0o755)]  # fmt: skip
    out = tmp_path_factory.mktemp("fs") / "fs-demo"
    build_executable(FAKE_RUNTIME, App("fs-demo", "tpl", tuple(modules), tuple(files)), out)
    return out


@pytest.fixture
def site(boot: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The payload installed as the import system, as boot.run() does; yields the cache site."""
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(sys, "path", [str(BOOT_DIR), boot.ROOT])  # _fsbpyr_rt from the sources
    monkeypatch.setattr(sys, "path_hooks", [boot.path_hook, *sys.path_hooks])
    monkeypatch.setattr(sys, "meta_path", [boot.META_FINDER, *sys.meta_path])
    monkeypatch.setattr(sys, "path_importer_cache", {})
    yield tmp_path / "cache" / "runtime" / boot.INFO["generation_id"].hex()[:32] / "site"
    for name in [n for n in sys.modules if n.partition(".")[0] in ("tpl", "plain")]:
        del sys.modules[name]


@pytest.fixture
def resources(site: Path, boot: ModuleType) -> Path:
    """Where resources asked for as paths are extracted: a tree of this build's own (D27)."""
    key = str(json.loads(boot.INFO["build_info"])["resources"])
    return site.parents[2] / "resources" / key / "site"


def test_nothing_is_extracted_before_the_package_is_imported(site: Path) -> None:
    importlib.import_module("plain")

    assert not site.exists()
    assert sys.modules["plain"].__file__.startswith(sys.path[1] + "/")  # type: ignore[union-attr]


def test_package_files_are_on_disk_before_its_code_runs(site: Path) -> None:
    tpl = importlib.import_module("tpl")

    assert tpl.DATA == "A"  # __init__ read data/a.txt relative to its __file__
    assert tpl.__file__ == f"{site}/tpl/__init__.py" and tpl.__path__ == [f"{site}/tpl"]
    assert stat.S_IMODE((site / "tpl/data/a.txt").stat().st_mode) == 0o400
    assert stat.S_IMODE((site / "tpl/run.sh").stat().st_mode) == 0o500
    assert stat.S_IMODE((site / NATIVE).stat().st_mode) == 0o500
    assert not (site / "plain").exists()


def test_submodules_keep_loading_from_the_payload(site: Path, boot: ModuleType) -> None:
    sub = importlib.import_module("tpl.sub")

    assert sub.X == 1 and sub.__file__ == f"{site}/tpl/sub.py"
    assert isinstance(sub.__loader__, boot.PayloadLoader)
    assert inspect.getsource(sub) == "X = 1\n"
    assert sub.__loader__.get_data(f"{site}/tpl/data/a.txt") == b"A"


def test_resources_and_namespaces_are_real_directories(site: Path) -> None:
    files = importlib.resources.files("tpl")

    assert isinstance(files, Path) and files == site / "tpl"
    with importlib.resources.as_file(files / "data" / "a.txt") as path:
        assert path == site / "tpl/data/a.txt"  # no temporary copy
    assert list(importlib.import_module("tpl.data").__path__) == [f"{site}/tpl/data"]
    assert sorted(p.name for p in importlib.resources.files("tpl.data").iterdir()) == [
        "a.txt", "deep",
    ]  # fmt: skip
    deep = importlib.import_module("tpl.data.deep")  # its parent's __path__ is a _NamespacePath
    assert list(deep.__path__) == [f"{site}/tpl/data/deep"]
    assert (
        importlib.resources.files("tpl.data.deep").joinpath("b.txt") == site / "tpl/data/deep/b.txt"
    )


def test_as_file_extracts_resources_that_outlive_the_with_block(
    site: Path, resources: Path
) -> None:
    files = importlib.resources.files("plain")

    with importlib.resources.as_file(files) as directory:
        pass

    assert directory == resources / "plain"  # not a temporary copy, gone after the block
    assert (directory / "x.txt").read_bytes() == b"x"
    assert stat.S_IMODE((directory / "x.txt").stat().st_mode) == 0o400
    assert not (site / "plain").exists()  # never in the tree shared by releases (D10)
    with importlib.resources.as_file(files / "x.txt") as path:
        assert path == resources / "plain/x.txt"
    with pytest.raises(FileNotFoundError), importlib.resources.as_file(files / "nope"):
        pass
    with pytest.raises(PermissionError), importlib.resources.as_file(files / ".."):
        pass  # the payload root: everything, natives and stdlib included


def test_resource_paths_given_to_os_functions_are_on_disk(
    resources: Path, boot: ModuleType
) -> None:
    files = importlib.resources.files("plain")

    with open(files / "x.txt") as handle:  # type: ignore[call-overload]  # os.fspath
        assert handle.read() == "x"
    assert str(files) == f"{resources}/plain" and os.path.isdir(str(files))
    assert str(files / "nope") == boot.ROOT + "/plain/nope"  # nothing to extract
    assert repr(files / "x.txt") == f"PayloadTraversable({boot.ROOT + '/plain/x.txt'!r})"


def test_paths_are_extracted_once_per_process(
    resources: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = cast(Any, importlib.resources.files("plain"))
    native = sys.modules["_fsbpyr_rt.native"]
    calls: list[tuple[int, ...]] = []
    sync = native.NativeCache.sync

    def counted(cache: object, indices: tuple[int, ...], **options: bool) -> list[str]:
        calls.append(indices)
        return list(sync(cache, indices, **options))

    monkeypatch.setattr(native.NativeCache, "sync", counted)

    paths = {str(files), str(files), os.fspath(files)}

    assert paths == {f"{resources}/plain"} and len(calls) == 1


def test_unusable_caches_fail_loudly_for_paths(
    boot: ModuleType, site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "file").write_text("")
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path / "file" / "cache"))
    files = importlib.resources.files("plain")

    with pytest.raises(OSError, match="FSBPYR_CACHE_DIR"):
        os.fspath(cast(Any, files / "x.txt"))
    with pytest.raises(OSError, match="FSBPYR_CACHE_DIR"), importlib.resources.as_file(files):
        pass
    with pytest.warns(RuntimeWarning, match="FSBPYR_CACHE_DIR"):  # str() is used in messages too
        assert str(files / "x.txt") == boot.ROOT + "/plain/x.txt"


def test_extraction_happens_once(site: Path, boot: ModuleType) -> None:
    importlib.import_module("tpl")
    native = sys.modules["_fsbpyr_rt.native"]
    extracted = sorted(native.EXTRACTED)

    for name in [n for n in sys.modules if n.partition(".")[0] == "tpl"]:
        del sys.modules[name]
    importlib.import_module("tpl")

    assert extracted == sorted([*DATA, NATIVE, "tpl/__init__.py", "tpl/sub.py"])
    assert sorted(native.EXTRACTED) == extracted
    assert os.path.isfile(site / "tpl/sub.py")


def test_verify_env_repairs_extracted_data_files(
    site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    importlib.import_module("tpl")
    data = site / "tpl/data/a.txt"
    data.chmod(0o600)
    data.write_bytes(b"Z")  # same size, different content
    for name in [n for n in sys.modules if n.partition(".")[0] == "tpl"]:
        del sys.modules[name]

    importlib.import_module("tpl")
    assert data.read_bytes() == b"Z"  # a cache hit is a size check
    del sys.modules["tpl"]
    monkeypatch.setenv("FSBPYR_VERIFY", "1")
    importlib.import_module("tpl")

    assert data.read_bytes() == b"A"
