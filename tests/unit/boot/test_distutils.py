"""``import distutils`` with setuptools packaged (RT-5): the module setuptools ships answers,
before setuptools itself is imported, as setuptools' ``_distutils_hack`` arranges in a venv."""

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from fsbpyr import bytecode
from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import ModuleKind
from fsbpyr.payload.spec import Blob, Module

from .conftest import FAKE_RUNTIME

MODULES = {  # payload name -> (path, kind, source)
    "setuptools": ("setuptools/__init__.py", ModuleKind.PACKAGE, "import setuptools._distutils\n"),
    "setuptools._distutils": (
        "setuptools/_distutils/__init__.py", ModuleKind.PACKAGE, "MARK = 1\n"
    ),
    "setuptools._distutils.version": (
        "setuptools/_distutils/version.py", ModuleKind.MODULE, "class Version: ...\n"
    ),
}


def _module(name: str, path: str, kind: ModuleKind, source: str) -> Module:
    code = Blob.code(bytecode.dumps(compile(source, path, "exec", dont_inherit=True)))
    return Module(name, path, kind, code=code, source=Blob.zstd(source.encode()))


@pytest.fixture(params=["with-distutils", "without-distutils"])
def executable(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    bare = {"setuptools": MODULES["setuptools"]}
    modules = MODULES if request.param == "with-distutils" else bare
    built = tuple(_module(name, *entry) for name, entry in modules.items())
    out = tmp_path / "app"
    build_executable(FAKE_RUNTIME, App("app", "setuptools", built), out)
    return out


@pytest.fixture
def imports(boot: ModuleType, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.setattr(sys, "path_hooks", [boot.path_hook, *sys.path_hooks])
    monkeypatch.setattr(sys, "meta_path", [boot.META_FINDER, *sys.meta_path])
    monkeypatch.setattr(sys, "path", [boot.ROOT])
    monkeypatch.setattr(sys, "path_importer_cache", {})
    before = set(sys.modules)
    importlib.invalidate_caches()
    yield boot
    for name in set(sys.modules) - before:
        del sys.modules[name]


def test_distutils_resolves_to_setuptools_copy_before_setuptools(
    request: pytest.FixtureRequest, imports: ModuleType
) -> None:
    if "without" in request.node.callspec.id:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("distutils")
        return
    assert "setuptools" not in sys.modules

    version = importlib.import_module("distutils.version")

    assert sys.modules["distutils"] is sys.modules["setuptools._distutils"]
    assert sys.modules["distutils"].MARK == 1
    assert version.__file__ == f"{imports.ROOT}/setuptools/_distutils/version.py"
    assert hasattr(version, "Version")
