"""Compile and statically link the launcher against a PBS distribution (plan §3.7 step 3)."""

import hashlib
import importlib.resources
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..text import tail_lines
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
    "slots.c",
    "way.c",
    "glibc.c",
)
LAUNCHER_FILES = (
    *LAUNCHER_SOURCES,
    "launcher.h",
    "payload.h",
    "integrity.h",
    "parallel.h",
    "cache.h",
    "due.h",
    "slots.h",
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
# libgcc is linked in: the launcher needs glibc's libraries only. Without it, the libcalls
# LTO adds on aarch64 (__extenddftf2, __trunctfdf2) bound to libgcc_s.so.1. --wrap=hypot and
# --wrap=_dl_find_object: glibc.c, so that the stub stays within the glibc 2.34 baseline.
LINK_FLAGS = (
    "-Wl,--export-dynamic-symbol=Py*", "-Wl,--export-dynamic-symbol=_Py*",
    "-Wl,-z,noexecstack", "-Wl,-z,relro", "-Wl,-z,now", "-s", "-static-libgcc",
    "-Wl,--wrap=hypot", "-Wl,--wrap=_dl_find_object",
)  # fmt: skip
# Cortex-A53 erratum 843419: an adrp in one of a 4 KiB page's last two instruction slots,
# followed by certain loads or stores, can compute a wrong address; lld patches each such
# sequence. PBS links its aarch64 builds so since 20261026, but the stub is linked here from
# PBS's bitcode, so the flag is the stub's own.
AARCH64_LINK_FLAGS = ("-Wl,--fix-cortex-a53-843419",)


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


def link_flags(triple: str, *, sanitize: bool = False) -> tuple[str, ...]:
    flags = tuple(f for f in LINK_FLAGS if f not in _NOT_SANITIZED) if sanitize else LINK_FLAGS
    return (*flags, *AARCH64_LINK_FLAGS) if triple.startswith("aarch64-") else flags


class StubBuildError(RuntimeError):
    """The compiler or linker failed."""


IDENTITY_MARKER = b"fsbpyr-stub-identity="  # main.c: followed by the runtime id, then a NUL


def stub_identity(data: bytes) -> str | None:
    """The runtime id the stub ``data`` was linked as (main.c); None for a file that carries
    none: not a stub of ours, or one linked before stubs recorded it."""
    start = data.find(IDENTITY_MARKER)
    if start < 0:
        return None
    start += len(IDENTITY_MARKER)
    end = data.find(b"\0", start)
    return None if end < 0 else data[start:end].decode("ascii", "replace")


def outlined_atomics(toolchain: Toolchain, triple: str) -> Path | None:
    """The library with the outlined atomics (``__aarch64_cas4_acq_rel`` and the like) that
    PBS's aarch64 objects call: clang's compiler-rt builtins, which the link takes them from
    (Debian 10's libgcc 8 predated them; Debian 12's libgcc 12 has them too, but later in the
    link); x86-64 needs nothing."""
    if not triple.startswith("aarch64-"):
        return None
    command = [os.fspath(toolchain.clang), f"--target={triple}", "-rtlib=compiler-rt",
               "--print-libgcc-file-name"]  # fmt: skip
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    library = Path(result.stdout.strip())
    if result.returncode != 0 or not library.is_file():
        raise StubBuildError(
            f"clang {toolchain.major} has no compiler-rt builtins for {triple} ({library}); "
            f"install libclang-rt-{toolchain.major}-dev"
        )
    return library


def launcher_digest(triple: str, *, sanitize: bool = False) -> str:
    """SHA-256 over the launcher sources and their flags for ``triple``: part of the runtime
    identity."""
    root = importlib.resources.files("fsbpyr.launcher")
    digest = hashlib.sha256()
    for name in LAUNCHER_FILES:
        digest.update(name.encode() + b"\0" + root.joinpath(name).read_bytes())
    flags = (*compile_flags(sanitize=sanitize), *link_flags(triple, sanitize=sanitize))
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
    atomics: Path | None = None,
    identity: str | None = None,
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
        # the stub's own id, out of the flags that the runtime id hashes
        *([f'-DFSBPYR_RUNTIME_ID="{identity}"'] if identity is not None else []),
        f"-I{dist.include}",
        f"-I{sources[0].parent}",
        "-o",
        os.fspath(out),
        *map(os.fspath, sources),
        *map(os.fspath, inputs.extra_objects),
        *link_flags(dist.triple, sanitize=sanitize),
        "-Wl,--start-group",
        os.fspath(inputs.libpython),
        *map(os.fspath, inputs.static_libs),
        "-Wl,--end-group",
        *(f"-l{name}" for name in inputs.system_libs),
        *([os.fspath(atomics)] if atomics is not None else []),
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
    dist: PbsDist, toolchain: Toolchain, out: Path, *, sanitize: bool = False,
    identity: str | None = None,
) -> tuple[str, ...]:
    """Link the launcher into ``out``, recording ``identity`` (the runtime id) in it; what the
    compiler and linker printed (their warnings), a line each. A stack frame over the limit
    fails it: LTO inlines across sources, and CPython's code into ours, and sizes the frames
    again at the link, where only a warning says so (D38; cache_sync took 8888 bytes,
    unnoticed, until 0.15.0)."""
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
        command = stub_command(dist, inputs, toolchain, sources, Path(_LINKED), sanitize=sanitize,
                               atomics=outlined_atomics(toolchain, dist.triple),
                               identity=identity)  # fmt: skip
        result = subprocess.run(command, cwd=work, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise StubBuildError(f"launcher link failed:\n{tail_lines(result.stderr, _ERROR_TAIL)}")
        shutil.copyfile(work / _LINKED, out)
    printed = tuple(line for line in result.stderr.splitlines() if line.strip())
    if over := [line for line in printed if "stack frame size" in line]:
        raise StubBuildError(
            "launcher link: a stack frame over the limit (D38):\n" + "\n".join(over)
        )
    return printed
