"""Native files stored as xz (--best-compression, D33): extracted like any other, one by one.
The launcher decodes the pieces of a large library in parallel; the runtime adds no threads."""

import importlib
import threading
import types
from pathlib import Path
from typing import Any

import pytest

from fsbpyr import bytecode
from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import XZ_PIECE, Codec, FileKind, ModuleKind
from fsbpyr.payload.spec import Blob, File, Module

from ...support.apps import library
from .conftest import FAKE_RUNTIME

EXT = "fast/_speed.cpython-314-x86_64-linux-gnu.so"
SMALL, BIG, COPY = "fast.libs/libsmall.so", "fast.libs/libbig.so", "fast.libs/libcopy.so"
CONTENT = {
    EXT: b"\x7fELF speed" * 5000,
    SMALL: b"\x7fELF small" * 3000,
    BIG: library(1300),
}
CONTENT[COPY] = CONTENT[SMALL]  # the same bytes under a second name: one blob, two files
LIBS = (SMALL, BIG, COPY)


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    natives = tuple(
        File(rel, FileKind.NATIVE_EXT if rel == EXT else FileKind.NATIVE_LIB,
             Blob.xz(data, digest=True), mode=0o755, closure=LIBS if rel == EXT else ())
        for rel, data in CONTENT.items()
    )  # fmt: skip
    codecs = {f.path: f.blob.codec for f in natives}
    assert codecs[BIG] is Codec.PIECES and len(CONTENT[BIG]) > XZ_PIECE
    assert codecs[SMALL] is codecs[EXT] is Codec.XZ
    code = Blob.code(bytecode.dumps(compile("", "fast/__init__.py", "exec", dont_inherit=True)))
    modules = (
        Module("fast", "fast/__init__.py", ModuleKind.PACKAGE, code=code),
        Module("fast._speed", EXT, ModuleKind.EXTENSION, file=EXT),
    )
    out = tmp_path_factory.mktemp("native-xz") / "native-xz"
    build_executable(FAKE_RUNTIME, App("native-xz", "fast", modules, natives), out)
    return out


@pytest.fixture
def native(
    boot: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> types.ModuleType:
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path / "cache"))
    return importlib.import_module("_fsbpyr_rt.native")


def _closure(boot: types.ModuleType) -> tuple[int, ...]:
    spec = boot.path_hook(boot.ROOT + "/fast").find_spec("fast._speed")
    assert spec is not None
    loader: Any = spec.loader
    files: tuple[int, ...] = loader.files
    return files


def test_xz_natives_and_their_pieces_are_extracted_with_no_thread_of_the_runtime(
    boot: types.ModuleType, native: types.ModuleType
) -> None:
    threads = threading.active_count()

    written = native.native_cache().sync(_closure(boot))

    assert written == len(CONTENT)
    site = Path(native.native_cache().site)
    assert all((site / rel).read_bytes() == data for rel, data in CONTENT.items())
    assert threading.active_count() == threads
