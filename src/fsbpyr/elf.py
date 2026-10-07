"""Minimal ELF64 reader: the dynamic-section facts needed to bundle native code (plan §3.5)."""

import re
import struct
from dataclasses import dataclass

_HEADER = struct.Struct("<HHIQQQIHHHHHH")  # e_type .. e_shstrndx, after e_ident[16]
_PHDR = struct.Struct("<IIQQQQQQ")  # type, flags, offset, vaddr, paddr, filesz, memsz, align
_DYN = struct.Struct("<qQ")
_VERNEED = struct.Struct("<HHIII")  # vn_version, vn_cnt, vn_file, vn_aux, vn_next
_VERNAUX = struct.Struct("<IHHII")  # vna_hash, vna_flags, vna_other, vna_name, vna_next
_GLIBC = re.compile(r"GLIBC_(\d+)\.(\d+)")
_GLIBC_ABI = {"GLIBC_ABI_DT_RELR": (2, 36)}  # version markers that are not release numbers
_MAX_NAME = 4096
PT_LOAD, PT_DYNAMIC = 1, 2
DT_NULL, DT_NEEDED, DT_STRTAB, DT_STRSZ, DT_SONAME, DT_RPATH, DT_RUNPATH = 0, 1, 5, 10, 14, 15, 29
DT_VERNEED, DT_VERNEEDNUM = 0x6FFFFFFE, 0x6FFFFFFF
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


@dataclass(frozen=True, slots=True)
class _Dynamic:
    """The parsed dynamic section of an object."""

    data: bytes
    loads: list[tuple[int, ...]]
    entries: list[tuple[int, int]]
    values: dict[int, int]
    strtab: int
    strsz: int

    def string(self, index: int) -> str:
        start = self.strtab + index
        if not 0 <= index < self.strsz or start >= len(self.data):
            raise ElfError(f"dynamic string offset {index} out of range")
        limit = min(len(self.data), self.strtab + self.strsz, start + _MAX_NAME + 1)
        end = self.data.find(b"\0", start, limit)
        if end < 0:
            raise ElfError(f"dynamic string at {index} is too long or not terminated")
        return self.data[start:end].decode("utf-8", errors="surrogateescape")


def _dynamic(data: bytes) -> _Dynamic | None:
    segments = _segments(data)
    loads = [s for s in segments if s[0] == PT_LOAD]
    dynamic = next((s for s in segments if s[0] == PT_DYNAMIC), None)
    if dynamic is None:
        return None
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
    return _Dynamic(data, loads, entries, values, strtab, values.get(DT_STRSZ, 0))


def dynamic_info(data: bytes) -> DynamicInfo:
    """DT_NEEDED, DT_RPATH, DT_RUNPATH and DT_SONAME of an ELF64 x86-64 object."""
    section = _dynamic(data)
    if section is None:
        return DynamicInfo((), (), (), None)
    values = section.values

    def paths(tag: int) -> tuple[str, ...]:
        if tag not in values:
            return ()
        return tuple(part for part in section.string(values[tag]).split(":") if part)

    return DynamicInfo(
        needed=tuple(section.string(value) for tag, value in section.entries if tag == DT_NEEDED),
        rpath=paths(DT_RPATH),
        runpath=paths(DT_RUNPATH),
        soname=section.string(values[DT_SONAME]) if DT_SONAME in values else None,
    )


def needed_versions(data: bytes) -> frozenset[str]:
    """Symbol versions an object requires from its libraries (DT_VERNEED): GLIBC_2.17, ..."""
    section = _dynamic(data)
    if section is None or DT_VERNEED not in section.values:
        return frozenset()
    versions: set[str] = set()
    budget = _MAX_ENTRIES  # shared by all entries: overlapping chains cannot multiply the work
    offset = _file_offset(section.loads, section.values[DT_VERNEED])
    for _ in range(min(section.values.get(DT_VERNEEDNUM, 0), _MAX_ENTRIES)):
        _, count, _, aux, following = _unpack(_VERNEED, data, offset)
        entry = offset + aux
        for _ in range(count):
            budget -= 1
            if budget < 0:
                raise ElfError("too many symbol version entries")
            _, _, _, name, next_entry = _unpack(_VERNAUX, data, entry)
            versions.add(section.string(name))
            if not next_entry:
                break
            entry += next_entry
        if not following:
            break
        offset += following
    return frozenset(versions)


def glibc_requirement(data: bytes) -> tuple[int, int] | None:
    """The newest glibc an object needs (GLIBC_2.2.5 counts as 2.2; ABI markers as the release
    that introduced them)."""
    versions = needed_versions(data)
    found = [(int(m[1]), int(m[2])) for v in versions if (m := _GLIBC.match(v))]
    found += [_GLIBC_ABI[v] for v in versions if v in _GLIBC_ABI]
    return max(found, default=None)
