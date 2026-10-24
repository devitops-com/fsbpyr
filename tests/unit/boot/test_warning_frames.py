"""An import-time warning with stacklevel >= 2 names the importing module, as in a venv: the
frames of the payload loader are skipped by ``warnings`` like those of importlib."""

import importlib
import linecache
import sys
import warnings
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from fsbpyr import bytecode
from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import FileKind, ModuleKind
from fsbpyr.payload.spec import Blob, File, Module

from .conftest import FAKE_RUNTIME

NOISY = "import warnings\nwarnings.warn('noisy', DeprecationWarning, stacklevel=2)\n"
MODULES = {"noisy": "noisy.py", "pkg": "pkg/__init__.py"}
PAGED = "first\n\x0csecond\nthird\n"  # a form feed: a line break for str.splitlines only


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    source = Blob.zstd(NOISY.encode())
    modules = tuple(
        Module(
            name,
            path,
            ModuleKind.PACKAGE if path.endswith("__init__.py") else ModuleKind.MODULE,
            code=Blob.code(bytecode.dumps(compile(NOISY, path, "exec", dont_inherit=True))),
            source=source,
        )
        for name, path in MODULES.items()
    )
    files = (*(File(path, FileKind.SOURCE, source) for path in MODULES.values()),
             File("paged.py", FileKind.SOURCE, Blob.zstd(PAGED.encode())))
    out = tmp_path_factory.mktemp("warnings") / "app"
    build_executable(FAKE_RUNTIME, App("app", "noisy", modules, files), out)
    return out


@pytest.fixture
def imports(boot: ModuleType, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """The boot module's hooks installed as ``run()`` does."""
    monkeypatch.setattr(sys, "path_hooks", [boot.path_hook, *sys.path_hooks])
    monkeypatch.setattr(sys, "meta_path", [boot.META_FINDER, *sys.meta_path])
    monkeypatch.setattr(sys, "path", [boot.ROOT])
    monkeypatch.setattr(sys, "path_importer_cache", {})
    before = set(sys.modules)
    importlib.invalidate_caches()
    yield boot
    for name in set(sys.modules) - before:
        del sys.modules[name]


@pytest.mark.parametrize("name", ["noisy", "pkg"])
def test_import_time_warning_is_attributed_to_the_importer(imports: ModuleType, name: str) -> None:
    importer = compile(f"\nimport {name}\n", "importer.py", "exec")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        exec(importer, {})  # noqa: S102

    assert [(w.filename, w.lineno) for w in caught] == [("importer.py", 2)]


def test_warning_shows_the_source_line_of_a_payload_file(
    imports: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """warnings asks linecache for the line without module globals: no loader gets asked."""
    monkeypatch.setattr(linecache, "updatecache", linecache.updatecache)  # restored afterwards
    monkeypatch.setattr(linecache, "cache", dict(linecache.cache))
    path = imports.ROOT + "/noisy.py"
    assert linecache.getline(path, 2) == ""

    imports._install_payload_linecache(linecache)

    assert linecache.getline(path, 2) == NOISY.splitlines(keepends=True)[1]
    assert linecache.getline(imports.ROOT + "/paged.py", 3) == "third\n"  # as readlines() splits
    assert linecache.getline(imports.ROOT + "/missing.py", 1) == ""
