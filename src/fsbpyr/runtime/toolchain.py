"""Discovery of a clang/lld able to link PBS's LLVM-bitcode static libpython."""

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_CLANG_VERSION = re.compile(r"clang version (\d+)\.")
_CANDIDATE_SPAN = 10  # clang-N, clang-(N+1), ... are tried before plain "clang"


class ToolchainError(RuntimeError):
    """No suitable compiler/linker was found."""


@dataclass(frozen=True, slots=True)
class Toolchain:
    clang: Path
    lld: Path
    major: int


def clang_major(version_text: str) -> int | None:
    match = _CLANG_VERSION.search(version_text)
    return int(match.group(1)) if match else None


def _probe(clang: Path) -> int | None:
    try:
        result = subprocess.run(
            [clang, "--version"], capture_output=True, text=True, check=False, timeout=30
        )
    except OSError:
        return None
    return clang_major(result.stdout)


def _which(name: str, search_path: str | None) -> Path | None:
    found = shutil.which(name, path=search_path)
    return Path(found) if found else None


def _with_lld(clang: Path, major: int, search_path: str | None) -> Toolchain | None:
    for name in (f"ld.lld-{major}", "ld.lld"):
        lld = _which(name, search_path) or _which(name, str(clang.parent))
        if lld is not None:
            return Toolchain(clang=clang, lld=lld, major=major)
    return None


def find_toolchain(
    min_major: int, *, explicit: Path | None = None, search_path: str | None = None
) -> Toolchain:
    """First clang with major >= ``min_major`` that has a matching ld.lld next to it."""
    if explicit is not None:
        major = _probe(explicit)
        if major is None or major < min_major:
            raise ToolchainError(f"{explicit} is clang {major}; clang >= {min_major} is required")
        toolchain = _with_lld(explicit, major, search_path)
        if toolchain is None:
            raise ToolchainError(f"no ld.lld found for {explicit}")
        return toolchain
    names = [f"clang-{n}" for n in range(min_major, min_major + _CANDIDATE_SPAN)] + ["clang"]
    for name in names:
        clang = _which(name, search_path)
        major = _probe(clang) if clang else None
        if clang is not None and major is not None and major >= min_major:
            toolchain = _with_lld(clang, major, search_path)
            if toolchain is not None:
                return toolchain
    raise ToolchainError(
        f"clang >= {min_major} with ld.lld not found; install LLVM {min_major} "
        "(e.g. apt.llvm.org) or pass --clang"
    )
