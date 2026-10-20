"""The boot module and _fsbpyr_rt, run in-process against the pure-Python _fsbpyr stand-in."""

import builtins
import importlib.machinery
import importlib.metadata
import importlib.resources
import importlib.util
import io
import json
import os
import posix
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from fsbpyr.assemble import build_executable

from ...support.apps import demo_app
from .conftest import FAKE_RUNTIME


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("boot") / "demo"
    build_executable(FAKE_RUNTIME, demo_app(), out)
    return out


def _load(boot: ModuleType, name: str) -> ModuleType:
    directory = name.rpartition(".")[0].replace(".", "/")
    finder = boot.path_hook(boot.ROOT + ("/" + directory if directory else ""))
    spec = finder.find_spec(name)
    assert spec is not None, name
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# --- path hook, specs, loader -------------------------------------------------------------


def test_listed_modules_get_their_fixup_after_loading(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixups = importlib.import_module("_fsbpyr_rt.fixups")
    seen: list[tuple[str, str]] = []
    monkeypatch.setitem(fixups.FIXUPS, "pkg.cli", lambda m: seen.append((m.__name__, m.main())))
    monkeypatch.setattr(boot, "_FIXUPS", frozenset({"pkg.cli"}))

    _load(boot, "pkg.cli")
    _load(boot, "pkg")

    assert seen == [("pkg.cli", "cli")]  # after the module's code ran; only listed modules


def test_path_hook_claims_only_payload_paths(boot: ModuleType) -> None:
    assert boot.path_hook(boot.ROOT).directory == ""
    assert boot.path_hook(boot.ROOT + "/pkg/sub").directory == "pkg/sub"
    assert boot.path_hook(boot.ROOT + "/pkg/").directory == "pkg"
    assert boot.path_hook(boot.ROOT + "//pkg/./sub/").directory == "pkg/sub"
    for foreign in ("/usr/lib/python3.14", boot.ROOT + "x", boot.ROOT + "/pkg/../.."):
        with pytest.raises(ImportError):
            boot.path_hook(foreign)


def test_package_spec_and_execution(boot: ModuleType) -> None:
    spec = boot.path_hook(boot.ROOT).find_spec("pkg")

    assert spec.origin == boot.ROOT + "/pkg/__init__.py"
    assert spec.submodule_search_locations == [boot.ROOT + "/pkg"]
    assert spec.has_location
    assert _load(boot, "pkg").VALUE == "pkg"


def test_finder_only_answers_for_its_own_directory(boot: ModuleType) -> None:
    root = boot.path_hook(boot.ROOT)

    assert root.find_spec("pkg.cli") is None
    assert root.find_spec("missing") is None
    assert root.find_spec("\ud800") is None


def test_code_filenames_point_into_the_payload_tree(boot: ModuleType) -> None:
    cli = _load(boot, "pkg.cli")

    assert cli.main.__code__.co_filename == boot.ROOT + "/pkg/cli.py"
    assert boot.LOADER.get_code("pkg.cli").co_filename == boot.ROOT + "/pkg/cli.py"


def test_loader_introspection(boot: ModuleType) -> None:
    loader = boot.LOADER

    assert loader.is_package("pkg") and not loader.is_package("pkg.cli")
    assert loader.get_filename("pkg.cli") == boot.ROOT + "/pkg/cli.py"
    assert loader.get_source("pkg.cli") == "def main():\n    return 'cli'\n"
    with pytest.raises(ImportError):
        loader.get_code("missing")


def test_get_data_maps_virtual_paths(boot: ModuleType) -> None:
    loader = boot.LOADER

    assert loader.get_data(boot.ROOT + "/pkg/data/table.json") == b'{"k": 1}'
    with pytest.raises(FileNotFoundError):
        loader.get_data(boot.ROOT + "/pkg/data/nope")
    with pytest.raises(FileNotFoundError):
        loader.get_data("/etc/passwd")


def test_namespace_package_spec(boot: ModuleType) -> None:
    spec = boot.path_hook(boot.ROOT).find_spec("ns")

    assert spec.loader is None
    assert spec.submodule_search_locations == [boot.ROOT + "/ns"]
    assert _load(boot, "ns.a").VALUE == "a"


def test_meta_finder_serves_namespace_packages_with_resources(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "pkg", _load(boot, "pkg"))  # parents are imported first
    spec = boot.META_FINDER.find_spec("ns")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)

    assert isinstance(spec.loader, importlib.machinery.NamespaceLoader)
    assert list(module.__path__) == [boot.ROOT + "/ns"]
    assert sorted(p.name for p in importlib.resources.files(module).iterdir()) == ["a.py", "b.py"]
    data_spec = boot.META_FINDER.find_spec("pkg.data")
    assert data_spec is not None
    data = importlib.resources.files(importlib.util.module_from_spec(data_spec))
    assert data.joinpath("table.json").read_text() == '{"k": 1}'
    assert boot.META_FINDER.find_spec("pkg") is None  # regular packages: PathFinder


def test_iter_modules(boot: ModuleType) -> None:
    root = list(boot.path_hook(boot.ROOT).iter_modules(""))
    children = list(boot.path_hook(boot.ROOT + "/pkg").iter_modules("pkg."))

    assert {("pkg", True), ("entry", False)} <= set(root)
    assert not {name for name, _ in root} & {"ns", "_fsbpyr_boot", "_fsbpyr_rt"}  # like FileFinder
    assert children == [("pkg.cli", False), ("pkg.sub", True)]


# --- importlib.resources ---------------------------------------------------------------------


def test_resources_traversal(boot: ModuleType) -> None:
    files = importlib.resources.files(_load(boot, "pkg"))

    assert sorted(p.name for p in files.iterdir()) == ["__init__.py", "cli.py", "data", "sub"]
    data = files / "data"
    assert data.is_dir() and not data.is_file()
    assert sorted(p.name for p in data.iterdir()) == ["sub", "table.json"]
    table = data.joinpath("table.json")
    assert table.is_file() and table.read_text() == '{"k": 1}'
    assert files.joinpath("data", "sub", "x.txt").read_bytes() == b"x"
    with table.open("rb") as handle:
        assert handle.read() == b'{"k": 1}'
    with table.open(encoding="utf-8") as text:
        assert isinstance(text, io.TextIOBase) and text.read() == '{"k": 1}'
    on_disk = os.fspath(cast(Any, table))  # extracted, so open() and os functions work
    assert str(table) == on_disk and on_disk.endswith("/site/pkg/data/table.json")
    assert Path(on_disk).read_bytes() == b'{"k": 1}'
    assert table == files / "data" / "table.json" and hash(table) == hash(data / "table.json")
    assert table != data
    loose = cast(Any, table)  # Traversable's typed modes are only "r" and "rb"
    with loose.open("rt") as text_mode, loose.open("br") as binary_mode:
        assert text_mode.read() == binary_mode.read().decode()
    assert cast(Any, table).read_text(encoding="ascii", errors="strict") == '{"k": 1}'


def test_functional_resources_api(boot: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pkg", _load(boot, "pkg"))

    assert importlib.resources.read_text("pkg", "data", "table.json", encoding="utf-8") == (
        '{"k": 1}'
    )
    assert importlib.resources.is_resource("pkg", "data", "table.json")


def test_resources_missing_and_escaping_paths(boot: ModuleType) -> None:
    files = importlib.resources.files(_load(boot, "pkg"))

    missing = files / "nope"
    assert not missing.is_file() and not missing.is_dir()
    with pytest.raises(FileNotFoundError):
        missing.read_bytes()
    with pytest.raises(NotADirectoryError):
        list((files / "data" / "table.json").iterdir())
    outside = files / ".." / ".." / "etc"
    assert not outside.is_dir() and not outside.is_file()


def test_resources_as_file_materializes_content(boot: ModuleType) -> None:
    files = importlib.resources.files(_load(boot, "pkg"))

    with importlib.resources.as_file(files / "data" / "table.json") as path:
        assert Path(path).read_bytes() == b'{"k": 1}'


def test_resources_module_anchor_is_containing_directory(boot: ModuleType) -> None:
    files = importlib.resources.files(_load(boot, "pkg.cli"))

    assert "cli.py" in {p.name for p in files.iterdir()}


# --- importlib.metadata ----------------------------------------------------------------------


def _context(**kwargs: Any) -> importlib.metadata.DistributionFinder.Context:
    return importlib.metadata.DistributionFinder.Context(**kwargs)


def test_distribution_lookup(boot: ModuleType) -> None:
    finder = boot.META_FINDER
    dists = list(finder.find_distributions(_context(name="DEMO", path=[boot.ROOT])))

    assert len(dists) == 1
    demo = dists[0]
    assert (demo.version, demo.metadata["Name"]) == ("1.0", "Demo")
    assert [f"{ep.name}={ep.value}" for ep in demo.entry_points] == ["demo=pkg.cli:main"]
    files = demo.files or []
    assert [str(f) for f in files] == ["pkg/__init__.py", "pkg/cli.py", "pkg/data/table.json"]
    assert files[2].read_text() == '{"k": 1}'
    assert demo.read_text("missing.txt") is None
    assert Path(os.fspath(demo.locate_file("pkg/cli.py"))).is_file()
    assert Path(os.fspath(demo._path), "METADATA").is_file()  # PathDistribution compat
    assert os.fspath(demo.locate_file("")) == boot.ROOT  # never the whole payload


def test_distribution_enumeration_respects_context(boot: ModuleType) -> None:
    finder = boot.META_FINDER

    assert [d.version for d in finder.find_distributions(_context(path=[boot.ROOT]))] == ["1.0"]
    assert list(finder.find_distributions(_context(name="missing", path=[boot.ROOT]))) == []
    assert list(finder.find_distributions(_context(name="demo", path=["/elsewhere"]))) == []
    assert finder.find_spec("anything") is None


# --- run(), entry points, tracebacks -------------------------------------------------------


@pytest.fixture
def isolated_sys(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Let boot.run() rewire the import system; everything is restored afterwards."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "path_hooks", list(sys.path_hooks))
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    monkeypatch.setattr(sys, "path_importer_cache", {})
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    frozen = importlib.machinery.FrozenImporter
    monkeypatch.setattr(frozen, "get_source", frozen.get_source)  # run() extends it
    for name in ("exit", "quit", "help"):
        monkeypatch.setattr(builtins, name, getattr(builtins, name))  # run() defines them
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        del sys.modules[name]


@pytest.mark.usefixtures("isolated_sys")
def test_resolve_entry_points(boot: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(posix.environ, b"FSBPYR_SELFTEST", b"1")
    boot.run()

    assert boot._resolve("pkg.cli:main")() == "cli"
    assert boot._resolve("pkg").VALUE == "pkg"


@pytest.mark.usefixtures("isolated_sys")
def test_run_selftest_installs_import_machinery(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(posix.environ, b"FSBPYR_SELFTEST", b"1")

    boot.run()

    assert sys.path == [boot.ROOT]
    assert sys.path_hooks[0] is boot.path_hook
    assert boot.META_FINDER in sys.meta_path
    assert sys.excepthook is boot._excepthook
    assert capsys.readouterr().out == "fsbpyr selftest ok: demo\n"


@pytest.mark.usefixtures("isolated_sys")
@pytest.mark.parametrize("package", ["pkg", "ns"])
def test_selftest_of_a_package_entry_requires_its_main(
    boot: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    package: str,
) -> None:
    """Running a package runs its __main__ module, as `python -m pkg` does."""
    monkeypatch.setitem(boot.INFO, "entrypoint", package)
    monkeypatch.setitem(posix.environ, b"FSBPYR_SELFTEST", b"1")

    with pytest.raises(ModuleNotFoundError, match=rf"{package}\.__main__"):
        boot.run()

    assert capsys.readouterr().out == ""


@pytest.mark.usefixtures("isolated_sys")
def test_run_defines_exit_quit_and_help_as_site_does(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    by_site = {name: getattr(builtins, name) for name in ("exit", "quit", "help")}
    for name in by_site:  # built apps never import site (D14)
        delattr(builtins, name)
    monkeypatch.setitem(posix.environ, b"FSBPYR_SELFTEST", b"1")

    boot.run()

    for name, defined in by_site.items():
        assert type(getattr(builtins, name)) is type(defined)
        assert repr(getattr(builtins, name)) == repr(defined)


@pytest.mark.usefixtures("isolated_sys")
@pytest.mark.parametrize("allowed", [True, False], ids=["interpreter-reexec", "default"])
def test_code_from_the_app_itself_runs_instead_of_it_only_when_built_so(
    boot: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    allowed: bool,
) -> None:
    reexec = importlib.import_module("_fsbpyr_rt.reexec")
    monkeypatch.setattr(reexec, "_parent_runs_this_executable", lambda: True)  # as in a child
    monkeypatch.setitem(boot.INFO, "build_info", json.dumps({"interpreter_reexec": allowed}))
    monkeypatch.setitem(posix.environ, b"FSBPYR_SELFTEST", b"1")
    monkeypatch.setitem(sys.modules, "__main__", types.ModuleType("__main__"))
    code = "import pkg.cli; print('child', pkg.cli.main())"  # from the payload
    monkeypatch.setattr(sys, "argv", [boot.ROOT, "-B", "-c", code])

    boot.run()

    expected = "child cli\n" if allowed else "fsbpyr selftest ok: demo\n"
    assert capsys.readouterr().out == expected


@pytest.mark.usefixtures("isolated_sys")
def test_multiprocessing_children_run_instead_of_the_app(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[int] = []
    tracker = types.ModuleType("multiprocessing.resource_tracker")
    tracker.main = started.append  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "multiprocessing.resource_tracker", tracker)
    mp = importlib.import_module("_fsbpyr_rt.mp")
    monkeypatch.setattr(mp, "_parent_runs_this_executable", lambda: True)  # pytest's parent is not
    monkeypatch.setitem(posix.environ, b"FSBPYR_SELFTEST", b"1")
    command = "from multiprocessing.resource_tracker import main;main(7)"
    monkeypatch.setattr(sys, "argv", [boot.ROOT, "-B", "-S", "-I", "-c", command])

    boot.run()

    assert started == [7] and sys.argv == ["-c"]
    assert sys.path == [boot.ROOT]  # the child imports from the payload like the app does
    assert "selftest" not in capsys.readouterr().out  # the application did not run


@pytest.mark.usefixtures("isolated_sys")
def test_trace_reports_payload_modules_by_dist_at_exit(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    registered: list[Any] = []
    monkeypatch.setattr("atexit.register", registered.append)
    monkeypatch.setitem(posix.environ, b"FSBPYR_SELFTEST", b"1")
    monkeypatch.setitem(posix.environ, b"FSBPYR_TRACE", b"1")
    boot.run()  # the selftest imports the entry module
    for name in ("pkg", "pkg.cli"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

    assert len(registered) == 1
    registered[0]()

    line = capsys.readouterr().err.strip()
    assert line.startswith("fsbpyr-trace ")
    report = json.loads(line.removeprefix("fsbpyr-trace "))
    assert report["modules"]["demo"] == ["pkg", "pkg.cli"]
    assert "entry" in report["modules"][""]  # modules without a distribution
    assert report["extracted"] == []
    assert set(report) >= {"reads", "stored_bytes", "decompressed_bytes", "integrity_ns"}


def test_excepthook_hides_only_leading_boot_frames(
    boot: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing() -> None:
        raise ValueError("bad input")

    try:
        failing()
    except ValueError as exc:
        boot._excepthook(ValueError, exc, exc.__traceback__)
    err = capsys.readouterr().err
    assert "failing" in err and err.rstrip().endswith("ValueError: bad input")
