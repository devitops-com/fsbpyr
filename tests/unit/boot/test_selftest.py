"""FSBPYR_SELFTEST=natives (_fsbpyr_rt.selftest, NAT-4): the dynamic loader's trace output of
every extension is parsed for missing libraries and versions. The loader is faked here, with
lines copied from glibc's."""

import importlib
import stat
import subprocess
import types
from pathlib import Path
from typing import Any

import pytest

from fsbpyr import bytecode
from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import FileKind, ModuleKind
from fsbpyr.payload.spec import Blob, File, Module

from .conftest import FAKE_RUNTIME
from .test_native import CONTENT, EXT, LIB, SUFFIX, _Exited, _refusing_sync

OTHER = f"fast/_other{SUFFIX}"
MISSING_LIBRARY = "\tlibfoo.so.1 => not found\n"
MISSING_VERSION = (
    "./x.so: /lib/x86_64-linux-gnu/libstdc++.so.6: version `GLIBCXX_3.4.32' not found "
    "(required by ./x.so)\n"
)
FOUND = ("\tlinux-vdso.so.1 (0x00007ffd0a5f1000)\n\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 "
         "(0x00007f1c3a000000)\n\t/lib64/ld-linux-x86-64.so.2 (0x00007f1c3a3e0000)\n")  # fmt: skip


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    natives = (
        File(EXT, FileKind.NATIVE_EXT, Blob.zstd(CONTENT[EXT], digest=True), mode=0o755,
             closure=(LIB,)),
        File(LIB, FileKind.NATIVE_LIB, Blob.zstd(CONTENT[LIB], digest=True), mode=0o755),
        File(OTHER, FileKind.NATIVE_EXT, Blob.zstd(b"\x7fELF other", digest=True), mode=0o755),
    )  # fmt: skip
    code = bytecode.dumps(compile("X = 1\n", "fast/__init__.py", "exec", dont_inherit=True))
    modules = (Module("fast", "fast/__init__.py", ModuleKind.PACKAGE, code=Blob.code(code)),)
    out = tmp_path_factory.mktemp("selftest") / "selftest-demo"
    build_executable(FAKE_RUNTIME, App("selftest-demo", "fast", modules, natives), out)
    return out


@pytest.fixture
def selftest(boot: types.ModuleType, tmp_path: Path) -> types.ModuleType:
    return importlib.import_module("_fsbpyr_rt.selftest")


def _loader(monkeypatch: pytest.MonkeyPatch, reports: dict[str, str]) -> str:
    """Replace the loader's run: it prints ``reports[<extension>]``, and fails unless it was
    started as ``<loader> <extension>`` in trace mode. (A script cannot stand in for the loader:
    trace mode would trace the script's interpreter.)"""

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        assert command[0] == "/fake/ld.so" and options["env"]["LD_TRACE_LOADED_OBJECTS"] == "1"
        rel = next(rel for rel in reports if command[1].endswith("/" + rel))
        return subprocess.CompletedProcess(command, 0, stdout=reports[rel], stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    return "/fake/ld.so"


def test_trace_output_yields_one_entry_per_missing_library_and_version(
    selftest: types.ModuleType,
) -> None:
    trace = (FOUND + MISSING_LIBRARY + MISSING_VERSION + MISSING_VERSION
             + "\tlibbar.so => not found\n")  # fmt: skip

    assert selftest.missing(trace) == [
        "libfoo.so.1",
        "version `GLIBCXX_3.4.32' of libstdc++.so.6",
        "libbar.so",
    ]


def test_trace_output_of_a_loadable_extension_has_nothing_missing(
    selftest: types.ModuleType,
) -> None:
    assert selftest.missing(FOUND) == []


def test_loader_is_the_interpreter_of_an_elf_header(selftest: types.ModuleType) -> None:
    header = Path("/bin/sh").resolve().read_bytes()[:4096]
    dynamic = selftest.interpreter(header)

    assert dynamic is not None and dynamic.startswith("/") and "ld" in dynamic
    assert selftest.interpreter(b"#!/bin/sh\n") is None
    assert selftest.interpreter(b"\x7fELF") is None  # truncated


def test_natives_pass_when_nothing_is_missing(
    selftest: types.ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    loader = _loader(monkeypatch, {EXT: FOUND, OTHER: FOUND})

    selftest.natives(loader)  # returns: no exit

    captured = capsys.readouterr()
    assert captured.out == "fsbpyr selftest: all natives load (2 extensions)\n"
    assert captured.err == ""


def test_natives_name_each_missing_item_with_its_extensions_and_exit_nonzero(
    selftest: types.ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    loader = _loader(monkeypatch, {EXT: MISSING_LIBRARY + MISSING_VERSION,
                                OTHER: MISSING_LIBRARY})  # fmt: skip

    with pytest.raises(SystemExit) as exit_:
        selftest.natives(loader)

    assert exit_.value.code == 1
    assert capsys.readouterr().err.splitlines() == [
        f"fsbpyr selftest: missing libfoo.so.1 (needed by {OTHER}, {EXT})",
        f"fsbpyr selftest: missing version `GLIBCXX_3.4.32' of libstdc++.so.6 (needed by {EXT})",
    ]


def test_natives_extract_every_extension_and_its_closure(
    selftest: types.ModuleType, boot: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    selftest.natives(_loader(monkeypatch, {EXT: FOUND, OTHER: FOUND}))

    native = importlib.import_module("_fsbpyr_rt.native")
    site = Path(native.native_cache().site)
    assert (site / LIB).read_bytes() == CONTENT[LIB] and (site / OTHER).is_file()
    assert stat.S_IMODE((site / EXT).stat().st_mode) == 0o500


def test_a_loader_that_cannot_run_fails_the_check(
    selftest: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_:
        selftest.natives("/no/such/loader")

    assert exit_.value.code == 1
    assert "cannot run" in capsys.readouterr().err


def test_a_refused_cache_directory_ends_the_check_as_it_ends_a_run(
    selftest: types.ModuleType, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    native = importlib.import_module("_fsbpyr_rt.native")
    refused = "refusing cache directory /shared: other users may write to it"
    _refusing_sync(monkeypatch, native, refused)
    loader = _loader(monkeypatch, {EXT: FOUND, OTHER: FOUND})

    with pytest.raises(_Exited) as ended:
        selftest.natives(loader)

    assert ended.value.args == (70,)
    executable = native._fsbpyr.info()["executable"]
    assert capfd.readouterr() == ("", f"fsbpyr: cannot start {executable}: {refused}\n")
