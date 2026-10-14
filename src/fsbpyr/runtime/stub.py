"""Compile and statically link the launcher against a PBS distribution (plan §3.7 step 3)."""

import hashlib
import importlib.resources
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import ar
from .pbs import LinkInputs, PbsDist, link_inputs
from .toolchain import Toolchain

LAUNCHER_SOURCES = (
    "main.c",
    "payload.c",
    "module.c",
    "integrity.c",
    "parallel.c",
    "cache.c",
    "due.c",
    "way.c",
)
LAUNCHER_FILES = (
    *LAUNCHER_SOURCES,
    "launcher.h",
    "payload.h",
    "integrity.h",
    "parallel.h",
    "cache.h",
    "due.h",
    "way.h",
    "format.h",
    "vendor/zstd/zstd.h",
    "vendor/zstd/zstd_errors.h",
)
_ERROR_TAIL = 4000
_LINKED = "stub"  # the link's output, inside its temporary directory
# Fixed compiler/linker flags; hashed into the runtime id so a flag change yields a new runtime.
# -Wvla and the frame limit keep the stack bounded by construction (D38;
# tests/e2e/test_launcher_build.py compiles without LTO, where every frame is sized, and fails).
COMPILE_FLAGS = (
    "-std=c11", "-O2", "-flto=full", "-fPIE", "-pie", "-D_GNU_SOURCE",
    "-fstack-protector-strong", "-D_FORTIFY_SOURCE=2", "-Wall", "-Wextra",
    "-Wvla", "-Wframe-larger-than=8192",
)  # fmt: skip
# Export only the Python C API that extension modules link against: exporting everything would
# let wheel libraries bind to the static zlib, OpenSSL or libffi linked into the stub.
LINK_FLAGS = (
    "-Wl,--export-dynamic-symbol=Py*", "-Wl,--export-dynamic-symbol=_Py*",
    "-Wl,-z,noexecstack", "-Wl,-z,relro", "-Wl,-z,now", "-s",
)  # fmt: skip


# The sanitized test variant (D38): ASan and UBSan, every undefined behaviour fatal; built
# without LTO, stripping or FORTIFY (which would hide where a report points, or overlap ASan).
SANITIZE_FLAGS = (
    "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-fno-omit-frame-pointer",
    "-g",
)  # fmt: skip
_NOT_SANITIZED = ("-O2", "-flto=full", "-D_FORTIFY_SOURCE=2", "-s")


def compile_flags(*, sanitize: bool = False) -> tuple[str, ...]:
    if not sanitize:
        return COMPILE_FLAGS
    return (*(f for f in COMPILE_FLAGS if f not in _NOT_SANITIZED), "-O1", *SANITIZE_FLAGS)


def link_flags(*, sanitize: bool = False) -> tuple[str, ...]:
    return tuple(f for f in LINK_FLAGS if f not in _NOT_SANITIZED) if sanitize else LINK_FLAGS


class StubBuildError(RuntimeError):
    """The compiler or linker failed."""


def launcher_digest(*, sanitize: bool = False) -> str:
    """SHA-256 over the launcher sources and flags: part of the runtime identity."""
    root = importlib.resources.files("fsbpyr.launcher")
    digest = hashlib.sha256()
    for name in LAUNCHER_FILES:
        digest.update(name.encode() + b"\0" + root.joinpath(name).read_bytes())
    flags = (*compile_flags(sanitize=sanitize), *link_flags(sanitize=sanitize))
    digest.update("\0".join(flags).encode())
    return digest.hexdigest()


def stub_command(  # noqa: PLR0913  # the build's inputs, and which variant
    dist: PbsDist,
    inputs: LinkInputs,
    toolchain: Toolchain,
    sources: list[Path],
    out: Path,
    *,
    sanitize: bool = False,
) -> list[str]:
    magic = int.from_bytes(dist.bytecode_magic, "little")
    return [
        os.fspath(toolchain.clang),
        f"--target={dist.triple}",
        *compile_flags(sanitize=sanitize),
        # a glibc baseline instead of the build host's (D25)
        *([f"--sysroot={toolchain.sysroot}"] if toolchain.sysroot is not None else []),
        # reproducible: where the cache lives (whose ~/.cache) never reaches the launcher
        f"-ffile-prefix-map={dist.root}=/fsbpyr/python",
        *(
            [f"-ffile-prefix-map={toolchain.sysroot}=/fsbpyr/sysroot"]
            if toolchain.sysroot is not None
            else []
        ),
        f"--ld-path={toolchain.lld}",
        f"-DFSBPYR_BYTECODE_MAGIC=0x{magic:08x}u",
        f"-I{dist.include}",
        f"-I{sources[0].parent}",
        "-o",
        os.fspath(out),
        *map(os.fspath, sources),
        *map(os.fspath, inputs.extra_objects),
        *link_flags(sanitize=sanitize),
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


def build_stub(
    dist: PbsDist, toolchain: Toolchain, out: Path, *, sanitize: bool = False
) -> tuple[str, ...]:
    """Link the launcher into ``out``; what the compiler and linker printed (their warnings), a
    line each. A stack frame over the limit fails it: LTO inlines across sources, and CPython's
    code into ours, and sizes the frames again at the link, where only a warning says so (D38;
    cache_sync took 8888 bytes, unnoticed, until 0.15.0)."""
    inputs = link_inputs(
        dist, set(ar.members(dist.root / dist.info["build_info"]["core"]["static_lib"]))
    )
    # Reproducible: clang runs inside the temporary directory on relative paths, so its random
    # name reaches neither __FILE__ nor the names LTO gives local symbols (it did, and every
    # runtime build linked a different launcher).
    with tempfile.TemporaryDirectory(prefix="fsbpyr-launcher-") as tmp:
        work = Path(tmp)
        _copy_launcher(work)
        sources = [Path(name) for name in LAUNCHER_SOURCES]
        command = stub_command(dist, inputs, toolchain, sources, Path(_LINKED), sanitize=sanitize)
        result = subprocess.run(command, cwd=work, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise StubBuildError(f"launcher link failed:\n{result.stderr[-_ERROR_TAIL:]}")
        shutil.copyfile(work / _LINKED, out)
    printed = tuple(line for line in result.stderr.splitlines() if line.strip())
    if over := [line for line in printed if "stack frame size" in line]:
        raise StubBuildError(
            "launcher link: a stack frame over the limit (D38):\n" + "\n".join(over)
        )
    return printed
