"""_fsbpyr_rt.native in-process: cache location, materialization, self-heal (plan §3.5)."""

import importlib
import os
import stat
import types
from importlib.machinery import ExtensionFileLoader
from pathlib import Path
from typing import Any

import pytest

from fsbpyr import bytecode
from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import FileKind, ModuleKind
from fsbpyr.payload.spec import Blob, File, Module

from .conftest import FAKE_RUNTIME

EXT = "fast/_speed.cpython-314-x86_64-linux-gnu.so"
LIB = "fast.libs/libhelper.so"
CY = "cy/__init__.cpython-314-x86_64-linux-gnu.so"
RCY = "vendored/rcy/__init__.cpython-314-x86_64-linux-gnu.so"  # below a .pth root, named rcy
CONTENT = {EXT: b"\x7fELF speed" * 50, LIB: b"\x7fELF helper" * 50, CY: b"\x7fELF cy",
           RCY: b"\x7fELF rcy"}  # fmt: skip


def _code(rel: str) -> Blob:
    return Blob.code(bytecode.dumps(compile("X = 1\n", rel, "exec", dont_inherit=True)))


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    natives = tuple(
        File(rel, FileKind.NATIVE_LIB if rel == LIB else FileKind.NATIVE_EXT,
             Blob.zstd(data, digest=True), mode=0o755, closure=(LIB,) if rel == EXT else ())
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
    build_executable(FAKE_RUNTIME, App("native-demo", "fast", modules, natives), out)
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
    monkeypatch.setenv("HOME", "/home/u")
    where = native.cache_directory

    assert where("app", {"FSBPYR_CACHE_DIR": "/c", "XDG_CACHE_HOME": "/x"}) == "/c"
    assert where("app", {"XDG_CACHE_HOME": "/x"}) == "/x/app"
    assert where("app", {"XDG_CACHE_HOME": "relative"}) == "/home/u/.cache/app"
    assert where("app", {"FSBPYR_CACHE_DIR": "rel"}) == os.path.abspath("rel")


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


def test_first_sync_extracts_the_closure_once(
    boot: types.ModuleType, native: types.ModuleType, tmp_path: Path
) -> None:
    spec = _speed_spec(boot)
    cache = native.native_cache()

    assert sorted(cache.sync(_files(spec))) == [LIB, EXT]
    assert cache.sync(_files(spec)) == []

    site = Path(cache.site)
    assert (site / EXT).read_bytes() == CONTENT[EXT] and (site / LIB).read_bytes() == CONTENT[LIB]
    assert stat.S_IMODE((site / EXT).stat().st_mode) == 0o500
    assert stat.S_IMODE((site / "fast").stat().st_mode) == 0o700
    assert not (site / "cy").exists()  # only what was asked for
    assert sorted(native.EXTRACTED) == [LIB, EXT]
    assert not [p for p in site.rglob(".*")]  # no temporary files left behind


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
    assert cache.sync(_files(spec)) == []
    _damage(Path(cache.site) / LIB)  # same size, other content
    native._CACHE.clear()
    cache = native.native_cache()

    assert cache.sync(_files(spec)) == [LIB]
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

    assert native.native_cache().sync(_files(spec)) == [LIB]
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
    assert cache.sync(_files(spec)) == []
    _damage(Path(cache.site) / LIB)

    assert cache.sync(_files(spec)) == []
    native._CACHE.clear()
    assert native.native_cache().sync(_files(spec)) == [LIB]


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

    def sync(self: Any, files: tuple[int, ...]) -> list[str]:
        synced.append(files)
        result: list[str] = real_sync(self, files)
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
