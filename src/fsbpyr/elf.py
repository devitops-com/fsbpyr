"""Minimal ELF64 reader: the dynamic-section facts needed to bundle native code (plan §3.5)."""

import struct
from dataclasses import dataclass

_HEADER = struct.Struct("<HHIQQQIHHHHHH")  # e_type .. e_shstrndx, after e_ident[16]
_PHDR = struct.Struct("<IIQQQQQQ")  # type, flags, offset, vaddr, paddr, filesz, memsz, align
_DYN = struct.Struct("<qQ")
PT_LOAD, PT_DYNAMIC = 1, 2
DT_NULL, DT_NEEDED, DT_STRTAB, DT_STRSZ, DT_SONAME, DT_RPATH, DT_RUNPATH = 0, 1, 5, 10, 14, 15, 29
EM_X86_64 = 62
_MAX_ENTRIES = 1 << 16


class ElfError(ValueError):
    """Not a usable ELF64 x86-64 object."""


@dataclass(frozen=True, slots=True)
class DynamicInfo:
    needed: tuple[str, ...]
    rpath: tuple[str, ...]
    runpath: tuple[str, ...]
    soname: str | None


def is_elf(data: bytes) -> bool:
    return data[:4] == b"\x7fELF"


def _unpack(layout: struct.Struct, data: bytes, offset: int) -> tuple[int, ...]:
    if offset < 0 or offset + layout.size > len(data):
        raise ElfError("truncated ELF data")
    return layout.unpack_from(data, offset)


def _segments(data: bytes) -> list[tuple[int, ...]]:
    if not is_elf(data):
        raise ElfError("not an ELF file")
    if len(data) < 64 or data[4] != 2 or data[5] != 1:
        raise ElfError("only 64-bit little-endian ELF objects are supported")
    _, machine, _, _, phoff, _, _, _, phentsize, phnum, _, _, _ = _unpack(_HEADER, data, 16)
    if machine != EM_X86_64:
        raise ElfError(f"ELF object for machine {machine}, not x86-64")
    if phentsize < _PHDR.size:
        raise ElfError("truncated ELF program headers")
    return [_unpack(_PHDR, data, phoff + i * phentsize) for i in range(phnum)]


def _file_offset(loads: list[tuple[int, ...]], address: int) -> int:
    for _, _, offset, vaddr, _, filesz, _, _ in loads:
        if vaddr <= address < vaddr + filesz:
            return offset + address - vaddr
    raise ElfError(f"address {address:#x} is not mapped from the file")


def dynamic_info(data: bytes) -> DynamicInfo:
    """DT_NEEDED, DT_RPATH, DT_RUNPATH and DT_SONAME of an ELF64 x86-64 object."""
    segments = _segments(data)
    loads = [s for s in segments if s[0] == PT_LOAD]
    dynamic = next((s for s in segments if s[0] == PT_DYNAMIC), None)
    if dynamic is None:
        return DynamicInfo((), (), (), None)
    entries: list[tuple[int, int]] = []
    offset, size = dynamic[2], dynamic[5]
    for position in range(offset, offset + min(size, _MAX_ENTRIES * _DYN.size), _DYN.size):
        tag, value = _unpack(_DYN, data, position)
        if tag == DT_NULL:
            break
        entries.append((tag, value))
    values = dict(entries)
    if DT_STRTAB not in values:
        raise ElfError("dynamic section without a string table")
    strtab = _file_offset(loads, values[DT_STRTAB])
    strsz = values.get(DT_STRSZ, 0)

    def string(index: int) -> str:
        if not 0 <= index < strsz or strtab + index >= len(data):
            raise ElfError(f"dynamic string offset {index} out of range")
        end = data.find(b"\0", strtab + index, min(len(data), strtab + strsz))
        if end < 0:
            raise ElfError(f"dynamic string at {index} is not terminated")
        return data[strtab + index : end].decode("utf-8", errors="surrogateescape")

    def paths(tag: int) -> tuple[str, ...]:
        if tag not in values:
            return ()
        return tuple(part for part in string(values[tag]).split(":") if part)

    return DynamicInfo(
        needed=tuple(string(value) for tag, value in entries if tag == DT_NEEDED),
        rpath=paths(DT_RPATH),
        runpath=paths(DT_RUNPATH),
        soname=string(values[DT_SONAME]) if DT_SONAME in values else None,
    )
