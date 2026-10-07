"""Modules under a .pth directory: importable by their names relative to it (Phase 6b)."""

from pathlib import Path
from types import ModuleType

import pytest

from fsbpyr import bytecode
from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import ModuleKind
from fsbpyr.payload.spec import Blob, Module

from .conftest import FAKE_RUNTIME

MODULES = {  # name -> site-relative path, as classify records them for "vendored_libs" in a .pth
    "helper": "vendored_libs/helper.py",
    "bar": "vendored_libs/bar/__init__.py",
    "bar.baz": "vendored_libs/bar/baz.py",
    "bar.plug.m": "vendored_libs/bar/plug/m.py",
}


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    code = Blob.code(bytecode.dumps(compile("", "m.py", "exec", dont_inherit=True)))
    modules = tuple(
        Module(name, path, ModuleKind.PACKAGE if path.endswith("__init__.py") else
               ModuleKind.MODULE, code=code)
        for name, path in MODULES.items()
    )  # fmt: skip
    namespace = Module("bar.plug", "vendored_libs/bar/plug", ModuleKind.NAMESPACE)
    out = tmp_path_factory.mktemp("pth") / "app"
    build_executable(FAKE_RUNTIME, App("app", "helper", (*modules, namespace)), out)
    return out


def test_relative_names_resolve_through_their_packages(boot: ModuleType) -> None:
    top = boot.path_hook(boot.ROOT)
    helper, bar = top.find_spec("helper"), top.find_spec("bar")

    assert helper is not None and helper.origin == f"{boot.ROOT}/vendored_libs/helper.py"
    assert bar is not None and bar.origin == f"{boot.ROOT}/vendored_libs/bar/__init__.py"
    assert bar.submodule_search_locations is not None
    inside = boot.path_hook(bar.submodule_search_locations[0])
    baz, plug = inside.find_spec("bar.baz"), inside.find_spec("bar.plug")
    assert baz is not None and baz.origin == f"{boot.ROOT}/vendored_libs/bar/baz.py"
    assert plug is not None and plug.submodule_search_locations is not None
    m = boot.path_hook(plug.submodule_search_locations[0]).find_spec("bar.plug.m")
    assert m is not None and m.origin == f"{boot.ROOT}/vendored_libs/bar/plug/m.py"
