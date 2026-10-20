"""Synthetic ELF64 shared objects for native-dependency tests."""

import struct

DT_NULL, DT_NEEDED, DT_STRTAB, DT_STRSZ, DT_SONAME, DT_RPATH, DT_RUNPATH = 0, 1, 5, 10, 14, 15, 29
DT_VERNEED, DT_VERNEEDNUM = 0x6FFFFFFE, 0x6FFFFFFF


def make_elf(
    *,
    needed: tuple[str, ...] = (),
    rpath: str | None = None,
    runpath: str | None = None,
    soname: str | None = None,
    machine: int = 62,
    dynamic: bool = True,
    versions: tuple[tuple[str, tuple[str, ...]], ...] = (),
    comment: bytes | None = None,
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
    verneed = bytearray()  # Elf64_Verneed entries, each followed by its Elf64_Vernaux entries
    for i, (library, names) in enumerate(versions):
        last = i == len(versions) - 1
        verneed += struct.pack("<HHIII", 1, len(names), add(library), 16,
                               0 if last else 16 + 16 * len(names))  # fmt: skip
        for j, name in enumerate(names):
            verneed += struct.pack("<IHHII", 0, 0, 0, add(name), 0 if j == len(names) - 1 else 16)
    base = 0x1000
    phoff, phnum = 64, 2
    strtab_off = phoff + 56 * phnum
    verneed_off = strtab_off + len(strings) + (-len(strings) % 8)
    dyn_off = verneed_off + len(verneed)
    if versions:
        entries += [(DT_VERNEED, base + verneed_off), (DT_VERNEEDNUM, len(versions))]
    entries += [(DT_STRTAB, base + strtab_off), (DT_STRSZ, len(strings)), (DT_NULL, 0)]
    dyn = b"".join(struct.pack("<qQ", tag, value) for tag, value in entries)
    size = dyn_off + len(dyn)
    header = b"\x7fELF" + bytes([2, 1, 1]) + bytes(9)
    header += struct.pack("<HHIQQQIHHHHHH", 3, machine, 1, 0, phoff, 0, 0, 64, 56, phnum, 64, 0, 0)
    load = struct.pack("<IIQQQQQQ", 1, 5, 0, base, base, size, size, 0x1000)
    dyn_seg = struct.pack("<IIQQQQQQ", 2 if dynamic else 4, 6, dyn_off, base + dyn_off,
                          base + dyn_off, len(dyn), len(dyn), 8)  # fmt: skip
    image = bytearray(header + load + dyn_seg + strings)
    image.extend(bytes(verneed_off - len(image)))
    image += verneed
    image += dyn
    if comment is None:
        return bytes(image)
    # a .comment section, then .shstrtab and the section header table
    comment_at = len(image)
    image += comment
    shstrtab = b"\0.comment\0.shstrtab\0"
    shstrtab_at = len(image)
    image += shstrtab
    image += bytes(-len(image) % 8)
    shoff = len(image)
    image += bytes(64)  # the null section
    image += struct.pack("<IIQQQQIIQQ", 1, 1, 0, 0, comment_at, len(comment), 0, 0, 1, 0)
    image += struct.pack("<IIQQQQIIQQ", 10, 3, 0, 0, shstrtab_at, len(shstrtab), 0, 0, 1, 0)
    image[40:48] = struct.pack("<Q", shoff)  # e_shoff
    image[60:64] = struct.pack("<HH", 3, 2)  # e_shnum, e_shstrndx
    return bytes(image)
