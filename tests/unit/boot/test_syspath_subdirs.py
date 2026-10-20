"""sys.path entries below the executable (``<exe>/pkg/_vendor``, RT-4): modules there are
imported by their short names, as they are in a venv."""

import importlib
import inspect
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from fsbpyr import bytecode
from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import ModuleKind
from fsbpyr.payload.spec import Blob, Module

from .conftest import FAKE_RUNTIME

SOURCE = "VALUE = 'sub'\n"
MODULES = {  # payload name -> (path, kind)
    "pkg": ("pkg/__init__.py", ModuleKind.PACKAGE),
    "pkg._vendor": ("pkg/_vendor", ModuleKind.NAMESPACE),
    "pkg._vendor.vendored": ("pkg/_vendor/vendored/__init__.py", ModuleKind.PACKAGE),
    "pkg._vendor.vendored.sub": ("pkg/_vendor/vendored/sub.py", ModuleKind.MODULE),
    "pkg._vendor.ns": ("pkg/_vendor/ns", ModuleKind.NAMESPACE),
    "pkg._vendor.ns.mod": ("pkg/_vendor/ns/mod.py", ModuleKind.MODULE),
    "ns": ("ns", ModuleKind.NAMESPACE),
    "ns.top": ("ns/top.py", ModuleKind.MODULE),
}


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    code = Blob.code(bytecode.dumps(compile(SOURCE, "m.py", "exec", dont_inherit=True)))
    source = Blob.zstd(SOURCE.encode())
    modules = tuple(
        Module(name, path, kind, code=code, source=source)
        if kind != ModuleKind.NAMESPACE else Module(name, path, kind)
        for name, (path, kind) in MODULES.items()
    )  # fmt: skip
    out = tmp_path_factory.mktemp("subdirs") / "app"
    build_executable(FAKE_RUNTIME, App("app", "pkg", modules), out)
    return out


@pytest.fixture
def imports(boot: ModuleType, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """The boot module's hooks installed as ``run()`` does, with ``<exe>/pkg/_vendor`` added."""
    monkeypatch.setattr(sys, "path_hooks", [boot.path_hook, *sys.path_hooks])
    monkeypatch.setattr(sys, "meta_path", [boot.META_FINDER, *sys.meta_path])
    monkeypatch.setattr(sys, "path", [boot.ROOT, boot.ROOT + "/pkg/_vendor"])
    monkeypatch.setattr(sys, "path_importer_cache", {})
    before = set(sys.modules)
    importlib.invalidate_caches()
    yield boot
    for name in set(sys.modules) - before:
        del sys.modules[name]


def test_modules_below_a_path_entry_import_by_their_short_names(imports: ModuleType) -> None:
    root = imports.ROOT
    vendored = importlib.import_module("vendored")
    sub = importlib.import_module("vendored.sub")

    assert vendored.__file__ == f"{root}/pkg/_vendor/vendored/__init__.py"
    assert list(vendored.__path__) == [f"{root}/pkg/_vendor/vendored"]
    assert sub.__file__ == f"{root}/pkg/_vendor/vendored/sub.py" and sub.VALUE == "sub"
    assert inspect.getsource(sub) == SOURCE


def test_a_vendored_portion_merges_with_a_packaged_namespace(imports: ModuleType) -> None:
    root = imports.ROOT
    ns = importlib.import_module("ns")
    mod = importlib.import_module("ns.mod")
    top = importlib.import_module("ns.top")

    assert sorted(ns.__path__) == [f"{root}/ns", f"{root}/pkg/_vendor/ns"]
    assert mod.__file__ == f"{root}/pkg/_vendor/ns/mod.py"
    assert top.__file__ == f"{root}/ns/top.py"


def test_the_loader_resolves_records_by_the_public_name(imports: ModuleType) -> None:
    sub = importlib.import_module("vendored.sub")
    loader: Any = sub.__loader__

    assert loader.get_source("vendored.sub") == SOURCE
    assert loader.get_code("vendored.sub").co_filename == sub.__file__
    assert loader.is_package("vendored.sub") is False
    assert loader.get_filename("vendored.sub") == sub.__file__


def test_entries_that_are_not_identifier_paths_find_nothing(boot: ModuleType) -> None:
    assert boot.path_hook(boot.ROOT + "/pkg/_vendor-x").find_spec("vendored") is None
    assert boot.path_hook(boot.ROOT + "/pkg/_vendor").find_spec("missing") is None
    assert boot.path_hook(boot.ROOT).find_spec("vendored") is None
