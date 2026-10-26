"""The platforms fsbpyr knows (plan Phase 9, "More platforms"): what differs between them.

Each fsbpyr builds apps for the platform it runs on, so the host's target is detected from
sys.platform, the machine and the C library. Knowing a target is not supporting it: only
the glibc ones (x86_64-linux-gnu, aarch64-linux-gnu) have pins (``runtime.pins``) so far.
"""

import lzma
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

EM_X86_64, EM_AARCH64 = 62, 183  # ELF e_machine


class UnsupportedHost(ValueError):
    """The host is none of the targets."""


@dataclass(frozen=True, slots=True)
class Target:
    name: str  # as release assets spell it
    triple: str  # python-build-standalone's
    flavor: str  # of PBS's "full" archive
    arch: str  # as error messages name it
    elf_machine: int | None  # e_machine; None: not ELF
    loader: str | None  # the dynamic loader's file name
    libc: str  # "glibc", "musl" or "macos"
    xz_filter: int | None  # xz's BCJ filter for the payload's natives (lzma has no ARM64's)
    multiarch: str | None  # of the sysroot's directories
    uv_platform: str  # uv's --python-platform, for the default glibc baseline

    def platform_for(self, manylinux: str) -> str:
        """uv's --python-platform for wheels of the glibc baseline ``manylinux`` ("2.28"): the
        default's with its manylinux tag replaced; musl and macOS have no such tag."""
        if self.libc != "glibc":
            return self.uv_platform
        arch = self.uv_platform.partition("-manylinux")[0]
        return f"{arch}-manylinux_{manylinux.replace('.', '_')}"


TARGETS = (
    Target("x86_64-linux-gnu", "x86_64-unknown-linux-gnu", "pgo+lto-full", "x86-64",
           EM_X86_64, "ld-linux-x86-64.so.2", "glibc", lzma.FILTER_X86,
           "x86_64-linux-gnu", "x86_64-manylinux_2_34"),
    Target("aarch64-linux-gnu", "aarch64-unknown-linux-gnu", "pgo+lto-full", "aarch64",
           EM_AARCH64, "ld-linux-aarch64.so.1", "glibc", None,
           "aarch64-linux-gnu", "aarch64-manylinux_2_34"),
    Target("x86_64-linux-musl", "x86_64-unknown-linux-musl", "lto-full", "x86-64",
           EM_X86_64, "ld-musl-x86_64.so.1", "musl", lzma.FILTER_X86,
           "x86_64-linux-musl", "x86_64-unknown-linux-musl"),
    Target("aarch64-linux-musl", "aarch64-unknown-linux-musl", "lto-full", "aarch64",
           EM_AARCH64, "ld-musl-aarch64.so.1", "musl", None,
           "aarch64-linux-musl", "aarch64-unknown-linux-musl"),
    Target("aarch64-macos", "aarch64-apple-darwin", "pgo+lto-full", "arm64",
           None, None, "macos", None, None, "aarch64-apple-darwin"),
)  # fmt: skip
_BY_KEY = {t.name: t for t in TARGETS} | {t.triple: t for t in TARGETS}


def target_named(name: str) -> Target:
    """The target called ``name`` (asset style) or by its PBS triple."""
    try:
        return _BY_KEY[name]
    except KeyError:
        raise ValueError(f"unknown target {name}") from None


def host_libc() -> str | None:
    """"glibc" or "musl" for the C library this process runs on, None if neither."""
    try:
        os.confstr("CS_GNU_LIBC_VERSION")  # glibc only
        return "glibc"
    except (ValueError, OSError):
        pass
    return "musl" if any(Path("/lib").glob("ld-musl-*.so.1")) else None


def detect(system: str, machine: str, libc: str | None) -> Target:
    """The target of a host: ``system`` is sys.platform, ``machine`` platform.machine() (arm64
    counts as aarch64), ``libc`` what host_libc() says."""
    arch = "aarch64" if machine == "arm64" else machine
    abi = {"glibc": "gnu", "musl": "musl"}.get(libc or "")
    name = {"linux": f"{arch}-linux-{abi}", "darwin": f"{arch}-macos"}.get(system)
    if name in _BY_KEY:
        return _BY_KEY[name]
    raise UnsupportedHost(f"unsupported host {system}/{machine}")


def host_target() -> Target:
    return detect(sys.platform, platform.machine(), host_libc())
