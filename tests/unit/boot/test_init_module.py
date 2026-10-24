"""``from .__init__ import x`` (statsmodels.api): a venv's FileFinder finds a package's
``__init__.py`` by file name, so ``pkg.__init__`` is a module of its own that runs it again."""

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

SOURCE = "RUNS = globals().get('RUNS', 0) + 1\nTEST = 'init'\n"
MODULES = {  # payload name -> (path, kind)
    "pkg": ("pkg/__init__.py", ModuleKind.PACKAGE),
    "pkg.sub": ("pkg/sub.py", ModuleKind.MODULE),
    "ns": ("ns", ModuleKind.NAMESPACE),
    "ns.mod": ("ns/mod.py", ModuleKind.MODULE),
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
    out = tmp_path_factory.mktemp("initmod") / "app"
    build_executable(FAKE_RUNTIME, App("app", "pkg", modules), out)
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


def test_a_packages_init_imports_as_a_module_of_its_own(imports: ModuleType) -> None:
    pkg = importlib.import_module("pkg")
    init = importlib.import_module("pkg.__init__")

    assert init is not pkg and sys.modules["pkg.__init__"] is init
    assert init.__file__ == f"{imports.ROOT}/pkg/__init__.py" == pkg.__file__
    assert init.__package__ == "pkg" and init.__spec__ is not None
    assert init.__spec__.parent == "pkg" and not hasattr(init, "__path__")
    assert getattr(init.__loader__, "is_package")("pkg.__init__") is False  # noqa: B009
    assert init.TEST == "init" and init.RUNS == 1  # executed again, in a fresh namespace


def test_from_dot_init_import_works_inside_the_package(imports: ModuleType) -> None:
    pkg = importlib.import_module("pkg")
    ns: dict[str, object] = {"__name__": "pkg.sub", "__package__": "pkg"}
    exec("from .__init__ import TEST", ns)  # noqa: S102

    assert ns["TEST"] == "init" and "pkg.__init__" in sys.modules and pkg.TEST == "init"


def test_only_regular_packages_have_an_init_module(imports: ModuleType) -> None:
    for name in ("pkg.sub.__init__", "ns.__init__", "ns.mod.__init__", "pkg.nope.__init__"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)
