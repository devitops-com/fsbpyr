"""Compile and statically link the launcher against a PBS distribution (plan §3.7 step 3)."""

import hashlib
import importlib.resources
import os
import subprocess
import tempfile
from pathlib import Path

from . import ar
from .pbs import LinkInputs, PbsDist, link_inputs
from .toolchain import Toolchain

LAUNCHER_SOURCES = ("main.c", "payload.c", "module.c")
LAUNCHER_FILES = (
    *LAUNCHER_SOURCES,
    "launcher.h",
    "payload.h",
    "format.h",
    "vendor/zstd/zstd.h",
    "vendor/zstd/zstd_errors.h",
)
_ERROR_TAIL = 4000
# Fixed compiler/linker flags; hashed into the runtime id so a flag change yields a new runtime.
COMPILE_FLAGS = (
    "-std=c11", "-O2", "-flto=full", "-fPIE", "-pie", "-D_GNU_SOURCE",
    "-fstack-protector-strong", "-D_FORTIFY_SOURCE=2", "-Wall", "-Wextra",
)  # fmt: skip
LINK_FLAGS = ("-Wl,--export-dynamic", "-Wl,-z,noexecstack", "-Wl,-z,relro", "-Wl,-z,now", "-s")


class StubBuildError(RuntimeError):
    """The compiler or linker failed."""


def launcher_digest() -> str:
    """SHA-256 over the launcher sources: part of the runtime identity."""
    root = importlib.resources.files("fsbpyr.launcher")
    digest = hashlib.sha256()
    for name in LAUNCHER_FILES:
        digest.update(name.encode() + b"\0" + root.joinpath(name).read_bytes())
    digest.update("\0".join((*COMPILE_FLAGS, *LINK_FLAGS)).encode())
    return digest.hexdigest()


def stub_command(
    dist: PbsDist, inputs: LinkInputs, toolchain: Toolchain, sources: list[Path], out: Path
) -> list[str]:
    magic = int.from_bytes(dist.bytecode_magic, "little")
    return [
        os.fspath(toolchain.clang),
        f"--target={dist.triple}",
        *COMPILE_FLAGS,
        f"--ld-path={toolchain.lld}",
        f"-DFSBPYR_BYTECODE_MAGIC=0x{magic:08x}u",
        f"-I{dist.include}",
        f"-I{sources[0].parent}",
        "-o",
        os.fspath(out),
        *map(os.fspath, sources),
        *map(os.fspath, inputs.extra_objects),
        *LINK_FLAGS,
        "-Wl,--start-group",
        os.fspath(inputs.libpython),
        *map(os.fspath, inputs.static_libs),
        "-Wl,--end-group",
        *(f"-l{name}" for name in inputs.system_libs),
    ]


def _copy_launcher(target: Path) -> list[Path]:
    """Materialize the packaged sources (they may live inside an fsbpyr executable)."""
    root = importlib.resources.files("fsbpyr.launcher")
    for name in LAUNCHER_FILES:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(root.joinpath(name).read_bytes())
    return [target / name for name in LAUNCHER_SOURCES]


def build_stub(dist: PbsDist, toolchain: Toolchain, out: Path) -> None:
    inputs = link_inputs(
        dist, set(ar.members(dist.root / dist.info["build_info"]["core"]["static_lib"]))
    )
    with tempfile.TemporaryDirectory(prefix="fsbpyr-launcher-") as tmp:
        sources = _copy_launcher(Path(tmp))
        command = stub_command(dist, inputs, toolchain, sources, out)
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise StubBuildError(f"launcher link failed:\n{result.stderr[-_ERROR_TAIL:]}")
