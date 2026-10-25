"""The glibc baselines of the stub (D25, EL8 option): linked against Debian 12's glibc 2.36 it
needs nothing newer than 2.34 (the default), against Rocky Linux 8.10's glibc 2.28 nothing newer
than 2.28. glibc.c compiles against both header sets (the _dl_find_object wrapper is compiled
out where they lack `struct dl_find_object`, glibc 2.35), and `build_flags` of each layout
compile and link a C program against its sysroot."""

import dataclasses
import os
import subprocess
from pathlib import Path

import pytest

from fsbpyr.build import TARGET_PYTHON
from fsbpyr.elf import glibc_requirement
from fsbpyr.launcher import __file__ as launcher_init
from fsbpyr.runtime import pbs, pins, registry, stub, sysroot
from fsbpyr.runtime.toolchain import find_toolchain

from ..support import downloads
from .conftest import DOWNLOADS_VARIABLE

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module", params=pins.BASELINES)
def fetched(
    request: pytest.FixtureRequest, runtime: registry.Runtime
) -> tuple[str, pins.SysrootPin, Path]:
    """The baseline, its sysroot pin for the host and the sysroot fetched into the cache."""
    baseline: str = request.param
    cache = registry.cache_root()
    kept = Path(os.environ[DOWNLOADS_VARIABLE]) if os.environ.get(DOWNLOADS_VARIABLE) else None
    files = downloads.pinned(TARGET_PYTHON, baseline)
    downloads.seed(files, kept, cache)
    sysroot_pin = pins.sysroot_for(registry.host_triple(), baseline)
    root = sysroot.fetch_sysroot(sysroot_pin, cache)
    downloads.keep(files, kept, cache)
    return baseline, sysroot_pin, root


@pytest.fixture(scope="module")
def linked(
    fetched: tuple[str, pins.SysrootPin, Path],
    runtime: registry.Runtime,
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[str, bytes]:
    """The baseline and the stub linked for it: the default runtime's own, or one linked here
    (the stub alone, without the stdlib)."""
    baseline, _, root = fetched
    if baseline == pins.DEFAULT_BASELINE:
        return baseline, runtime.stub
    pin = pins.pin_for(TARGET_PYTHON, registry.host_triple())
    dist = pbs.fetch(pin, registry.cache_root())
    toolchain = dataclasses.replace(find_toolchain(dist.llvm_major), sysroot=root)
    out = tmp_path_factory.mktemp("el8-stub") / "stub"
    stub.build_stub(dist, toolchain, out)
    return baseline, out.read_bytes()


def test_the_stub_needs_no_glibc_symbol_above_its_baseline(linked: tuple[str, bytes]) -> None:
    baseline, data = linked
    major, minor = (int(part) for part in baseline.split("."))

    required = glibc_requirement(data)

    assert required is not None and required <= (major, minor)


def test_glibc_c_and_extension_flags_work_against_the_sysroot(
    fetched: tuple[str, pins.SysrootPin, Path], tmp_path: Path
) -> None:
    baseline, sysroot_pin, root = fetched
    clang = find_toolchain(22).clang
    source = tmp_path / "hello.c"
    source.write_text('#include <stdio.h>\n#include <math.h>\nint main(void)'
                      '{printf("%f\\n", hypot(3, 4)); return 0;}\n')  # fmt: skip
    flags = sysroot.build_flags(root, {}, sysroot_pin.layout)
    glibc_c = Path(launcher_init).parent / "glibc.c"

    guard = subprocess.run(  # the _dl_find_object wrapper compiles out or in as the headers say
        [clang, f"--sysroot={root}", "-fsyntax-only", "-D_GNU_SOURCE", glibc_c],
        capture_output=True, text=True, check=False, cwd=tmp_path)  # fmt: skip
    link = subprocess.run(
        [clang, *flags["CFLAGS"].split(), source, *flags["LDFLAGS"].split(), "-lm",
         "-fuse-ld=lld", "-o", tmp_path / "hello"],
        capture_output=True, text=True, check=False, cwd=tmp_path)  # fmt: skip

    assert guard.returncode == 0, (baseline, guard.stderr)
    assert link.returncode == 0, (baseline, link.stderr)
