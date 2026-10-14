"""D38: the launcher's stack is bounded by construction: no variable-length array and no frame
over FRAME_LIMIT bytes (and, by review, no recursion over input). Each source is compiled as the
runtime build compiles it, but without LTO, so the compiler sizes every function's frame."""

import dataclasses
import os
import subprocess
from pathlib import Path

import pytest

from fsbpyr.assemble import App, build_executable
from fsbpyr.runtime import pbs, pins, registry, stub, sysroot
from fsbpyr.runtime.registry import Runtime
from fsbpyr.runtime.toolchain import find_toolchain

from .conftest import app_content

pytestmark = pytest.mark.slow

FRAME_LIMIT = 8192  # bytes; the largest frame was 4.9 KB (0.9.1)


@pytest.fixture(scope="module")
def compile_command(runtime: Runtime, tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    """The runtime build's compiler, flags and include paths, for one source at a time."""
    pin = pins.pin_for(runtime.python_version.rpartition(".")[0], registry.host_triple())
    cache = registry.cache_root()
    dist = pbs.fetch(pin, cache)
    root = sysroot.fetch_sysroot(pins.sysroot_for(pin.triple), cache)
    toolchain = find_toolchain(dist.llvm_major)
    sources = tmp_path_factory.mktemp("launcher")
    stub._copy_launcher(sources)
    magic = int.from_bytes(dist.bytecode_magic, "little")
    flags = [flag for flag in stub.COMPILE_FLAGS if flag not in ("-flto=full", "-pie")]
    # -Werror=frame-larger-than turns the flags' -Wframe-larger-than=FRAME_LIMIT into an error
    return [os.fspath(toolchain.clang), f"--target={dist.triple}", *flags, f"--sysroot={root}",
            f"-DFSBPYR_BYTECODE_MAGIC=0x{magic:08x}u", f"-I{dist.include}", f"-I{sources}",
            "-Werror=vla", "-Werror=frame-larger-than", "-c", "-o", os.devnull,
            os.fspath(sources)]  # fmt: skip


@pytest.mark.parametrize("source", stub.LAUNCHER_SOURCES)
def test_launcher_sources_have_no_vla_and_no_large_frame(
    compile_command: list[str], source: str
) -> None:
    *command, directory = compile_command
    result = subprocess.run(
        [*command, os.fspath(Path(directory) / source)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("probe", "warning"),
    [("void use(char *); void f(int n) { char a[n]; use(a); }", "-Wvla"),
     (f"void use(char *); void f(void) {{ char a[{2 * FRAME_LIMIT}]; use(a); }}",
      "-Wframe-larger-than")],
    ids=["a VLA", "a large frame"],
)  # fmt: skip
def test_the_check_refuses_what_it_is_for(
    compile_command: list[str], probe: str, warning: str, tmp_path: Path
) -> None:
    *command, _ = compile_command
    (tmp_path / "probe.c").write_text(probe + "\n")

    result = subprocess.run(
        [*command, os.fspath(tmp_path / "probe.c")], capture_output=True, text=True, check=False
    )

    assert result.returncode != 0 and f"[-Werror,{warning}]" in result.stderr, result.stderr


def test_the_runtime_build_warns_about_both() -> None:
    assert "-Wvla" in stub.COMPILE_FLAGS
    assert f"-Wframe-larger-than={FRAME_LIMIT}" in stub.COMPILE_FLAGS


@pytest.mark.parametrize("runtime", ["sanitized"], indirect=True)
def test_the_sanitized_launcher_runs_under_asan_and_ubsan(runtime: Runtime, tmp_path: Path) -> None:
    """The variant the tests marked ``sanitized`` also run with (D38): a run under it passing
    means something only if the sanitizers are really in it. A wild read in the app's process is
    reported by ASan and exits 99 (main.c), never 1 like a Python exception."""
    source = (
        "import ctypes, sys\n\ndef main():\n    print('ran')\n"
        "    if sys.argv[1:]:\n        ctypes.string_at(1)\n"
    )
    modules, files = app_content({"x.py": source})
    app = tmp_path / "app"
    build_executable(runtime, App("x", "x:main", modules=modules, files=files), app)

    ran = subprocess.run([app], capture_output=True, text=True, check=False)
    read = subprocess.run([app, "wild"], capture_output=True, text=True, check=False)

    assert ran.returncode == 0 and ran.stdout == "ran\n", ran.stderr
    assert read.returncode == 99 and "ERROR: AddressSanitizer: SEGV" in read.stderr, read.stderr
    assert b"__ubsan_handle_" in app.read_bytes()  # UBSan runs inside ASan's runtime


def test_the_launcher_links_reproducibly(runtime: Runtime, tmp_path: Path) -> None:
    """Two links of the same inputs are the same bytes, so executables built from one commit
    are the same wherever their runtime was built (the temporary directory's name reached the
    launcher through LTO until 0.18.1)."""
    pin = pins.pin_for(runtime.python_version.rpartition(".")[0], registry.host_triple())
    cache = registry.cache_root()
    dist = pbs.fetch(pin, cache)
    root = sysroot.fetch_sysroot(pins.sysroot_for(pin.triple), cache)
    toolchain = dataclasses.replace(find_toolchain(dist.llvm_major), sysroot=root)

    for name in ("a", "b"):
        stub.build_stub(dist, toolchain, tmp_path / name)

    assert (tmp_path / "a").read_bytes() == (tmp_path / "b").read_bytes()
