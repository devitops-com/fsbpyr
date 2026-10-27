"""_fsbpyr_rt.native in-process: cache location, materialization, self-heal (plan §3.5)."""

import importlib
import os
import pwd
import stat
import types
from importlib.machinery import ExtensionFileLoader
from pathlib import Path
from typing import Any

import pytest

from fsbpyr import bytecode
from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import FileKind, ModuleKind
from fsbpyr.payload.spec import Blob, Dist, File, Module

from .conftest import FAKE_RUNTIME

SUFFIX = next(s for s in importlib.machinery.EXTENSION_SUFFIXES if s.startswith(".cpython-"))
EXT = f"fast/_speed{SUFFIX}"
LIB = "fast.libs/libhelper.so"
CY = f"cy/__init__{SUFFIX}"
RCY = f"vendored/rcy/__init__{SUFFIX}"  # below a .pth root, named rcy
DUPS = ("fast/libdup.so", "cy/libdup.so")  # one file name in two packages
PLUGIN = "fast.libs/plugins/libplugin.so"  # fast's distribution ships it; nothing links it
CONTENT = {EXT: b"\x7fELF speed" * 50, LIB: b"\x7fELF helper" * 50, CY: b"\x7fELF cy",
           RCY: b"\x7fELF rcy", DUPS[0]: b"\x7fELF dup fast",
           DUPS[1]: b"\x7fELF dup cy", PLUGIN: b"\x7fELF plugin"}  # fmt: skip
FAST_DIST = Dist("fast", "fast-1.0.dist-info", "1.0")


def _code(rel: str) -> Blob:
    return Blob.code(bytecode.dumps(compile("X = 1\n", rel, "exec", dont_inherit=True)))


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    natives = tuple(
        File(rel, FileKind.NATIVE_LIB if rel in (LIB, *DUPS, PLUGIN) else FileKind.NATIVE_EXT,
             Blob.zstd(data, digest=True), mode=0o755, closure=(LIB,) if rel == EXT else (),
             dist=FAST_DIST.name if rel.startswith("fast") else None)
        for rel, data in CONTENT.items()
    )  # fmt: skip
    modules = (
        Module("fast", "fast/__init__.py", ModuleKind.PACKAGE, code=_code("fast/__init__.py")),
        Module("fast._speed", EXT, ModuleKind.EXTENSION, file=EXT),
        Module("cy", CY, ModuleKind.EXTENSION, file=CY),
        Module("rcy", RCY, ModuleKind.EXTENSION, file=RCY),
        Module("cy.helpers", "cy/helpers.py", ModuleKind.MODULE, code=_code("cy/helpers.py")),
    )
    out = tmp_path_factory.mktemp("native") / "native-demo"
    build_executable(FAKE_RUNTIME, App("native-demo", "fast", modules, natives, (FAST_DIST,)), out)
    return out


@pytest.fixture
def native(
    boot: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> types.ModuleType:
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path / "cache"))
    return importlib.import_module("_fsbpyr_rt.native")


def _speed_spec(boot: types.ModuleType) -> importlib.machinery.ModuleSpec:
    spec: importlib.machinery.ModuleSpec | None = boot.path_hook(boot.ROOT + "/fast").find_spec(
        "fast._speed"
    )
    assert spec is not None
    return spec


def _files(spec: importlib.machinery.ModuleSpec) -> tuple[int, ...]:
    loader: Any = spec.loader
    files: tuple[int, ...] = loader.files
    return files


def test_cache_directory_precedence(
    native: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = {"HOME": "/home/u"}
    where = native.cache_directory

    assert where("app", {"FSBPYR_CACHE_DIR": "/c", "XDG_CACHE_HOME": "/x", **home}) == "/c"
    assert where("app", {"XDG_CACHE_HOME": "/x", **home}) == "/x/app"
    assert where("app", {"XDG_CACHE_HOME": "relative", **home}) == "/home/u/.cache/app"
    assert where("app", {"FSBPYR_CACHE_DIR": "rel", **home}) == os.path.abspath("rel")


@pytest.mark.parametrize("home", [None, "", "relative"], ids=["unset", "empty", "relative"])
def test_an_unusable_home_means_the_passwd_home(
    native: types.ModuleType, monkeypatch: pytest.MonkeyPatch, home: str | None
) -> None:
    """The rule the launcher's due.c follows too (its collection must find the same root)."""
    entry = types.SimpleNamespace(pw_dir="/home/passwd")
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: entry)
    environ = {} if home is None else {"HOME": home}

    assert native.cache_directory("app", environ) == "/home/passwd/.cache/app"


def test_extension_spec_points_into_the_generation_site_tree(
    boot: types.ModuleType, native: types.ModuleType, tmp_path: Path
) -> None:
    spec = _speed_spec(boot)

    generation = boot.INFO["generation_id"].hex()[:32]
    assert spec.origin == f"{tmp_path}/cache/runtime/{generation}/site/{EXT}"
    assert isinstance(spec.loader, native.PayloadExtensionLoader)
    assert spec.has_location and spec.submodule_search_locations is None
    assert not (tmp_path / "cache").exists()  # computing the spec touches nothing


def test_compiled_package_keeps_its_submodules_in_the_payload(boot: types.ModuleType) -> None:
    spec = boot.path_hook(boot.ROOT).find_spec("cy")

    assert spec is not None and spec.submodule_search_locations == [boot.ROOT + "/cy"]
    assert ("cy", True) in set(boot.path_hook(boot.ROOT).iter_modules())
    assert boot.path_hook(boot.ROOT + "/cy").find_spec("cy.helpers") is not None
    below_pth_root = boot.path_hook(boot.ROOT).find_spec("rcy")
    assert below_pth_root is not None
    assert below_pth_root.submodule_search_locations == [boot.ROOT + "/rcy"]  # by name


def test_a_compiled_package_whose_path_is_the_cache_finds_its_payload_modules(
    boot: types.ModuleType, native: types.ModuleType
) -> None:
    """mypyc sets the ``__path__`` of a package it compiled to the directory its extension was
    extracted to (``black``): the payload's plain modules are found there as in the payload."""
    where = f"{native.native_cache().site}/cy"

    finder = boot.path_hook(where)

    helpers = finder.find_spec("cy.helpers")
    assert helpers is not None and helpers.origin == f"{boot.ROOT}/cy/helpers.py"
    assert helpers.loader is not None and helpers.loader.prefix == boot.ROOT + "/"
    assert finder.find_spec("cy.missing") is None
    with pytest.raises(ImportError):
        boot.path_hook(f"{where}/../../x")


def test_a_bundled_library_is_found_by_its_file_name(native: types.ModuleType) -> None:
    """``ctypes.CDLL("libhelper.so")`` falls back to it (fixups): extracted with the closure of
    an extension that links it."""
    site = Path(native.native_cache().site)

    assert native.bundled_library("libhelper.so") == str(site / LIB)

    assert (site / LIB).read_bytes() == CONTENT[LIB]
    assert not (site / EXT).exists()  # the closure's members, not the extension itself


def test_an_extension_found_by_its_file_name_comes_with_its_closure(
    native: types.ModuleType,
) -> None:
    site = Path(native.native_cache().site)

    assert native.bundled_library(EXT.rpartition("/")[2]) == str(site / EXT)

    assert (site / EXT).read_bytes() == CONTENT[EXT] and (site / LIB).read_bytes() == CONTENT[LIB]


def test_of_several_of_a_name_the_one_in_the_callers_package_is_found(
    boot: types.ModuleType, native: types.ModuleType
) -> None:
    """PyTables loads libblosc2.so by bare name from tables/__init__.py: tables' copy, not
    blosc2's, as its lookup next to its __file__ finds in a venv."""
    site = Path(native.native_cache().site)

    assert native.bundled_library("libdup.so") is None  # which one is not to be guessed
    fast = f"{boot.ROOT}/fast/__init__.py"
    assert native.bundled_library("libdup.so", fast) == str(site / DUPS[0])
    assert native.bundled_library("libdup.so", f"{site}/cy/helpers.py") == str(site / DUPS[1])
    assert native.bundled_library("libdup.so", f"{boot.ROOT}/other/mod.py") is None
    assert native.bundled_library("libdup.so", f"{boot.ROOT}/top.py") is None  # at the root


def test_payload_paths_are_claimed_where_no_cache_directory_can_be_told(
    boot: types.ModuleType, native: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No HOME, no XDG_CACHE_HOME, no FSBPYR_CACHE_DIR: the cache cannot be located, and the
    path hook, which asks it for the directories mypyc names, still claims the payload's."""

    def nowhere() -> None:
        raise native.NativeCacheError("cannot locate a cache directory")

    monkeypatch.setattr(native, "native_cache", nowhere)

    assert boot.path_hook(boot.ROOT).find_spec("fast") is not None  # pure-Python code imports
    assert boot.path_hook(boot.ROOT + "/cy").find_spec("cy.helpers") is not None
    with pytest.raises(ImportError, match="not an fsbpyr payload path"):
        boot.path_hook("/elsewhere/fast")


def test_a_name_the_payload_bundles_no_library_of_is_none(native: types.ModuleType) -> None:
    assert native.bundled_library("libmissing.so") is None
    assert native.bundled_library("helper.so") is None  # the whole file name, not a part of it
    assert not Path(native.native_cache().site).exists()


def test_first_sync_extracts_the_closure_once(
    boot: types.ModuleType, native: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _speed_spec(boot)
    cache = native.native_cache()
    monkeypatch.setenv("FSBPYR_TRACE", "1")  # the paths written are kept for the trace

    assert cache.sync(_files(spec)) == 2
    assert cache.sync(_files(spec)) == 0

    site = Path(cache.site)
    assert (site / EXT).read_bytes() == CONTENT[EXT] and (site / LIB).read_bytes() == CONTENT[LIB]
    assert stat.S_IMODE((site / EXT).stat().st_mode) == 0o500
    assert stat.S_IMODE((site / "fast").stat().st_mode) == 0o700
    assert not (site / "cy").exists()  # only what was asked for
    assert sorted(native.EXTRACTED) == [LIB, EXT]
    assert not [p for p in site.rglob(".*")]  # no temporary files left behind


def test_paths_written_are_kept_only_for_the_trace(
    boot: types.ModuleType, native: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D38: a sync of any number of files needs no memory per file."""
    monkeypatch.delenv("FSBPYR_TRACE", raising=False)
    spec = _speed_spec(boot)

    assert native.native_cache().sync(_files(spec)) == 2
    assert native.EXTRACTED == []


def _damage(path: Path) -> None:
    path.chmod(0o700)
    path.write_bytes(b"\0" * path.stat().st_size)
    path.chmod(0o500)


def test_a_later_process_rehashes_and_restores_a_modified_file(
    boot: types.ModuleType, native: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = _speed_spec(boot)
    native.native_cache().sync(_files(spec))
    native._CACHE.clear()  # a new process
    cache = native.native_cache()
    assert cache.sync(_files(spec)) == 0
    _damage(Path(cache.site) / LIB)  # same size, other content
    native._CACHE.clear()
    cache = native.native_cache()

    assert cache.sync(_files(spec)) == 1
    assert (Path(cache.site) / LIB).read_bytes() == CONTENT[LIB]
    assert f"fsbpyr: {cache.site}/{LIB} was modified" in capsys.readouterr().err


class _FakeDlopen(ExtensionFileLoader):
    """Stands in for dlopen: fails unless every file of the closure is intact."""

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        assert spec.origin is not None
        site = Path(spec.origin[: -len(EXT)])
        if any((site / rel).read_bytes() != CONTENT[rel] for rel in (EXT, LIB)):
            raise ImportError("invalid ELF header", name=spec.name, path=spec.origin)
        return types.ModuleType(spec.name)


def _loader(native: types.ModuleType, spec: importlib.machinery.ModuleSpec) -> ExtensionFileLoader:
    class Loader(native.PayloadExtensionLoader, _FakeDlopen):  # type: ignore[misc,name-defined]
        pass

    factory: Any = Loader
    loader: ExtensionFileLoader = factory(spec.name, spec.origin, _files(spec))
    return loader


def test_an_unreadable_cached_file_is_replaced(
    boot: types.ModuleType, native: types.ModuleType
) -> None:
    spec = _speed_spec(boot)
    native.native_cache().sync(_files(spec))
    (Path(native.native_cache().site) / LIB).chmod(0o000)
    native._CACHE.clear()  # a new process

    # root reads it as it is: the file is intact, nothing to replace
    assert native.native_cache().sync(_files(spec)) == (0 if os.geteuid() == 0 else 1)
    assert (Path(native.native_cache().site) / LIB).read_bytes() == CONTENT[LIB]


def test_each_process_hashes_a_cached_file_once(
    boot: types.ModuleType, native: types.ModuleType
) -> None:
    """Extensions share libraries (scipy: one 25 MB OpenBLAS for 19 of them): a file this
    process checked is not checked again, so a change made since goes unnoticed until the next
    process starts."""
    spec = _speed_spec(boot)
    native.native_cache().sync(_files(spec))
    native._CACHE.clear()  # a new process
    cache = native.native_cache()
    assert cache.sync(_files(spec)) == 0
    _damage(Path(cache.site) / LIB)

    assert cache.sync(_files(spec)) == 0
    native._CACHE.clear()
    assert native.native_cache().sync(_files(spec)) == 1


def test_a_modified_file_is_restored_before_loading(
    boot: types.ModuleType, native: types.ModuleType
) -> None:
    spec = _speed_spec(boot)
    native.native_cache().sync(_files(spec))
    _damage(Path(native.native_cache().site) / EXT)
    native._CACHE.clear()  # a new process

    module = _loader(native, spec).create_module(spec)

    assert module is not None and module.__name__ == "fast._speed"
    assert (Path(native.native_cache().site) / EXT).read_bytes() == CONTENT[EXT]


def test_load_failure_with_intact_files_is_reported_unchanged(
    boot: types.ModuleType, native: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _speed_spec(boot)
    monkeypatch.setitem(CONTENT, LIB, b"never matches")  # the fake dlopen always fails now

    with pytest.raises(ImportError, match="invalid ELF header"):
        _loader(native, spec).create_module(spec)

    monkeypatch.setattr(native, "_noexec", lambda _path: True)
    with pytest.raises(native.NativeCacheError, match="noexec"):
        _loader(native, spec).create_module(spec)


def test_symlinks_inside_the_cache_are_refused(
    boot: types.ModuleType, native: types.ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "cache").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "cache" / "runtime").symlink_to(tmp_path / "elsewhere")
    spec = _speed_spec(boot)

    with pytest.raises(native.NativeCacheError, match="FSBPYR_CACHE_DIR"):
        _loader(native, spec).create_module(spec)
    assert not any((tmp_path / "elsewhere").iterdir())


def test_import_errors_raised_by_the_module_itself_do_not_trigger_a_rehash(
    boot: types.ModuleType, native: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _speed_spec(boot)

    class InitFails(ExtensionFileLoader):
        def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
            raise ModuleNotFoundError("No module named 'optional_dep'", name="optional_dep")

    class Loader(native.PayloadExtensionLoader, InitFails):  # type: ignore[misc,name-defined]
        pass

    synced: list[tuple[int, ...]] = []
    real_sync = native.NativeCache.sync

    def sync(self: Any, files: tuple[int, ...], **kwargs: bool) -> list[str]:
        synced.append(files)
        result: list[str] = real_sync(self, files, **kwargs)
        return result

    monkeypatch.setattr(native.NativeCache, "sync", sync)
    factory: Any = Loader

    with pytest.raises(ModuleNotFoundError, match="optional_dep"):
        factory(spec.name, spec.origin, _files(spec)).create_module(spec)
    assert synced == [_files(spec)]


def test_unwritable_cache_is_reported_with_a_hint(
    boot: types.ModuleType, native: types.ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache").chmod(0o500)
    spec = _speed_spec(boot)

    try:
        if os.geteuid() == 0:  # root writes into it
            assert _loader(native, spec).create_module(spec).__name__ == spec.name
        else:
            with pytest.raises(native.NativeCacheError, match="FSBPYR_CACHE_DIR"):
                _loader(native, spec).create_module(spec)
    finally:
        (tmp_path / "cache").chmod(0o700)


def test_unsafe_app_names_never_reach_the_filesystem(native: types.ModuleType) -> None:
    for app in ("", ".", "..", "a/b"):
        with pytest.raises(native.NativeCacheError, match="corrupt"):
            native.cache_directory(app, {"XDG_CACHE_HOME": "/x"})


def test_cache_hits_are_rehashed_before_loading(
    boot: types.ModuleType, native: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _speed_spec(boot)
    native.native_cache().sync(_files(spec))
    _damage(Path(native.native_cache().site) / LIB)
    native._CACHE.clear()  # a new process

    class Loads(ExtensionFileLoader):  # dlopen would not notice the damage
        def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
            return types.ModuleType(spec.name)

    class Loader(native.PayloadExtensionLoader, Loads):  # type: ignore[misc,name-defined]
        pass

    factory: Any = Loader
    factory(spec.name, spec.origin, _files(spec)).create_module(spec)

    assert (Path(native.native_cache().site) / LIB).read_bytes() == CONTENT[LIB]


@pytest.mark.parametrize(
    "message",
    [
        "/lib/x86_64-linux-gnu/libstdc++.so.6: version `GLIBCXX_3.4.32' not found",
        "libc.so.6: version `GLIBC_2.38' not found (required by x.so)",
        "libfoo.so.1: cannot open shared object file: No such file or directory",
    ],
)
def test_a_system_library_failure_carries_the_build_requirement(
    boot: types.ModuleType, native: types.ModuleType, message: str
) -> None:
    spec = _speed_spec(boot)

    class Dlopen(ExtensionFileLoader):
        def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
            raise ImportError(message, name=spec.name, path=spec.origin)

    class Loader(native.PayloadExtensionLoader, Dlopen):  # type: ignore[misc,name-defined]
        pass

    factory: Any = Loader
    loader = factory(spec.name, spec.origin, _files(spec))

    with pytest.raises(ImportError) as caught:
        loader.create_module(spec)

    text = str(caught.value)
    assert text.startswith(message) and "the build requires glibc" in text
    version = os.confstr("CS_GNU_LIBC_VERSION")
    assert version is not None and version in text
    assert caught.value.name == spec.name


def test_an_fs_package_comes_with_the_libraries_its_extensions_need(
    native: types.ModuleType,
) -> None:
    """Code that opens the package's extensions itself does not import them (torchvision)."""
    site = Path(native.native_cache().site)

    assert native.materialize_package("fast") == f"{site}/"

    assert (site / EXT).read_bytes() == CONTENT[EXT]
    assert (site / LIB).read_bytes() == CONTENT[LIB]  # outside the package's directory
    assert not (site / CY).exists()


def test_an_fs_package_comes_with_the_libraries_its_distribution_ships(
    native: types.ModuleType,
) -> None:
    """Libraries nothing links, which the package opens from a directory (openmm's plugins,
    in OpenMM.libs/lib/plugins); not those of other distributions."""
    site = Path(native.native_cache().site)

    native.materialize_package("fast")

    assert (site / PLUGIN).read_bytes() == CONTENT[PLUGIN]
    assert not (site / DUPS[1]).exists()  # cy's: of no distribution
    native.materialize_package("cy")
    assert (site / DUPS[1]).exists()  # below the package


class _Exited(Exception):
    """Stands in for the process ending: os._exit does not return."""


def _refusing_sync(monkeypatch: pytest.MonkeyPatch, native: types.ModuleType, message: str) -> None:
    def sync(*_: object) -> int:
        raise native.NativeCacheError(message)

    monkeypatch.setattr(native._fsbpyr, "sync", sync)

    def leave(status: int) -> None:
        raise _Exited(status)

    monkeypatch.setattr(native.os, "_exit", leave)


@pytest.mark.parametrize("why", ["other users may write to it", "owned by another user"])
def test_a_refused_cache_directory_is_a_refused_start(
    boot: types.ModuleType, native: types.ModuleType, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], why: str
) -> None:  # fmt: skip
    _refusing_sync(monkeypatch, native, f"refusing cache directory /shared: {why}")
    spec = _speed_spec(boot)

    with pytest.raises(_Exited) as ended:
        _loader(native, spec).create_module(spec)

    assert ended.value.args == (70,)
    executable = native._fsbpyr.info()["executable"]
    assert capfd.readouterr().err == (
        f"fsbpyr: cannot start {executable}: refusing cache directory /shared: {why}\n"
    )


def test_any_other_extraction_failure_stays_an_error(
    boot: types.ModuleType, native: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refusing_sync(monkeypatch, native, "corrupt executable payload: file 3")
    spec = _speed_spec(boot)

    with pytest.raises(native.NativeCacheError, match="corrupt executable payload"):
        _loader(native, spec).create_module(spec)


def test_a_modules_own_oserror_is_not_an_extraction_failure(
    boot: types.ModuleType, native: types.ModuleType
) -> None:
    """mypyc runs a compiled package's body inside create_module (blib2to3 reads Grammar.txt)."""
    spec = _speed_spec(boot)

    class ReadsAFile(ExtensionFileLoader):
        def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
            raise FileNotFoundError(2, "No such file or directory", f"{spec.origin}.txt")

    class Loader(native.PayloadExtensionLoader, ReadsAFile):  # type: ignore[misc,name-defined]
        pass

    factory: Any = Loader

    with pytest.raises(FileNotFoundError) as failed:
        factory(spec.name, spec.origin, _files(spec)).create_module(spec)
    assert not isinstance(failed.value, native.NativeCacheError)
    assert failed.value.filename == f"{spec.origin}.txt"


def test_a_missing_file_below_the_cache_site_names_the_package_to_extract(
    boot: types.ModuleType, native: types.ModuleType
) -> None:
    site = native.native_cache().site

    note = boot._inside_executable_note(
        FileNotFoundError(2, "No such file or directory", f"{site}/fast/Grammar.txt")
    )

    assert note is not None
    assert "fast reads files next to its __file__: rebuild with --fs-package fast\n" in note
    assert boot._inside_executable_note(
        FileNotFoundError(2, "No such file or directory", f"{site}/nowhere/Grammar.txt")
    ) is None  # fmt: skip
    assert boot._inside_executable_note(
        FileNotFoundError(2, "No such file or directory", "/elsewhere/fast/Grammar.txt")
    ) is None  # fmt: skip


def test_the_site_note_costs_nothing_before_an_extension_is_loaded(boot: types.ModuleType) -> None:
    assert boot._inside_executable_note(
        FileNotFoundError(2, "No such file or directory", "/c/runtime/g/site/fast/Grammar.txt")
    ) is None  # fmt: skip
