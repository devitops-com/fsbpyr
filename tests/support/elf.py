"""Synthetic ELF64 shared objects for native-dependency tests."""

import struct

DT_NULL, DT_NEEDED, DT_STRTAB, DT_STRSZ, DT_SONAME, DT_RPATH, DT_RUNPATH = 0, 1, 5, 10, 14, 15, 29


def make_elf(
    *,
    needed: tuple[str, ...] = (),
    rpath: str | None = None,
    runpath: str | None = None,
    soname: str | None = None,
    machine: int = 62,
    dynamic: bool = True,
) -> bytes:
    """Minimal ELF64 shared object: one PT_LOAD over the file (vaddr == offset + 0x1000)."""
    strings = bytearray(b"\0")

    def add(text: str) -> int:
        offset = len(strings)
        strings.extend(text.encode() + b"\0")
        return offset

    entries = [(DT_NEEDED, add(n)) for n in needed]
    for tag, value in ((DT_RPATH, rpath), (DT_RUNPATH, runpath), (DT_SONAME, soname)):
        if value is not None:
            entries.append((tag, add(value)))
    base = 0x1000
    phoff, phnum = 64, 2
    strtab_off = phoff + 56 * phnum
    dyn_off = strtab_off + len(strings) + (-len(strings) % 8)
    entries += [(DT_STRTAB, base + strtab_off), (DT_STRSZ, len(strings)), (DT_NULL, 0)]
    dyn = b"".join(struct.pack("<qQ", tag, value) for tag, value in entries)
    size = dyn_off + len(dyn)
    header = b"\x7fELF" + bytes([2, 1, 1]) + bytes(9)
    header += struct.pack("<HHIQQQIHHHHHH", 3, machine, 1, 0, phoff, 0, 0, 64, 56, phnum, 64, 0, 0)
    load = struct.pack("<IIQQQQQQ", 1, 5, 0, base, base, size, size, 0x1000)
    dyn_seg = struct.pack("<IIQQQQQQ", 2 if dynamic else 4, 6, dyn_off, base + dyn_off,
                          base + dyn_off, len(dyn), len(dyn), 8)  # fmt: skip
    image = bytearray(header + load + dyn_seg + strings)
    image.extend(bytes(dyn_off - len(image)))
    return bytes(image + dyn)
