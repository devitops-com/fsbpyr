"""Mutated ELF bytes: elf.py reads them or raises ElfError, never anything else. A build
reads every native file of a lock, so a corrupt one must end in a message, not a traceback."""

import contextlib
from collections.abc import Callable

from hypothesis import given, settings
from hypothesis import strategies as st

from fsbpyr import elf
from fsbpyr.elf import ElfData, ElfError

from ..support.elf import make_elf

SEEDS = (
    make_elf(),
    make_elf(needed=("libm.so.6", "libc.so.6"), rpath="/a:/b", runpath="$ORIGIN", soname="x.so"),
    make_elf(
        versions=(
            ("libc.so.6", ("GLIBC_2.2.5", "GLIBC_2.34")),
            ("libstdc++.so.6", ("GLIBCXX_3.4.22",)),
        )
    ),
    make_elf(comment=b"GCC: (Debian 12.2.0-14) 12.2.0\0", executable=True, interp=True),
)
READERS: tuple[Callable[[ElfData], object], ...] = (
    elf.is_executable,
    lambda data: elf.section(data, ".comment"),
    elf.dynamic_info,
    elf.needed_versions,
    elf.glibc_requirement,
    elf.version_requirements,
)


@st.composite
def mutated_elf(draw: st.DrawFn) -> bytes:
    data = bytearray(draw(st.sampled_from(SEEDS)))
    for _ in range(draw(st.integers(1, 8))):
        # the headers and the tables sit in the first bytes: aim most mutations there
        position = draw(st.integers(0, min(len(data), 512) - 1))
        data[position] = draw(st.integers(0, 255))
    if draw(st.booleans()):
        del data[draw(st.integers(0, len(data))) :]
    return bytes(data)


@settings(max_examples=1000)
@given(mutated_elf())
def test_mutated_elf_bytes_raise_only_elf_error(data: bytes) -> None:
    for reader in READERS:
        with contextlib.suppress(ElfError):
            reader(data)
