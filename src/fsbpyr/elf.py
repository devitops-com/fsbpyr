"""Minimal ELF64 reader: the dynamic-section facts needed to bundle native code (plan §3.5)."""

import mmap
import re
import struct
from dataclasses import dataclass

from .targets import host_target

type ElfData = bytes | mmap.mmap  # a file's content, or its file mapped (D38)
_HEADER = struct.Struct("<HHIQQQIHHHHHH")  # e_type .. e_shstrndx, after e_ident[16]
_PHDR = struct.Struct("<IIQQQQQQ")  # type, flags, offset, vaddr, paddr, filesz, memsz, align
_DYN = struct.Struct("<qQ")
_SHDR = struct.Struct("<IIQQQQIIQQ")  # name, type, flags, addr, offset, size, link, info, ...
_VERNEED = struct.Struct("<HHIII")  # vn_version, vn_cnt, vn_file, vn_aux, vn_next
_VERNAUX = struct.Struct("<IHHII")  # vna_hash, vna_flags, vna_other, vna_name, vna_next
_GLIBC = re.compile(r"GLIBC_(\d+)\.(\d+)")
_NUMERIC = re.compile(r"([A-Z]+)_(\d+(?:\.\d+)*)")  # GLIBCXX_3.4.22, CXXABI_1.3.9, GCC_7.0.0
_GLIBC_ABI = {"GLIBC_ABI_DT_RELR": (2, 36)}  # version markers that are not release numbers
_MAX_NAME = 4096
PT_LOAD, PT_DYNAMIC, PT_INTERP = 1, 2, 3
ET_EXEC = 2
SHT_NOBITS = 8
DT_NULL, DT_NEEDED, DT_STRTAB, DT_STRSZ, DT_SONAME, DT_RPATH, DT_RUNPATH = 0, 1, 5, 10, 14, 15, 29
DT_VERNEED, DT_VERNEEDNUM = 0x6FFFFFFE, 0x6FFFFFFF
_MAX_ENTRIES = 1 << 16


class ElfError(ValueError):
    """Not a usable ELF64 object of the host's architecture."""


@dataclass(frozen=True, slots=True)
class DynamicInfo:
    needed: tuple[str, ...]
    rpath: tuple[str, ...]
    runpath: tuple[str, ...]
    soname: str | None


def is_elf(data: ElfData) -> bool:
    return data[:4] == b"\x7fELF"


def _unpack(layout: struct.Struct, data: ElfData, offset: int) -> tuple[int, ...]:
    if offset < 0 or offset + layout.size > len(data):
        raise ElfError("truncated ELF data")
    return layout.unpack_from(data, offset)


def _header(data: ElfData) -> tuple[int, ...]:
    if not is_elf(data):
        raise ElfError("not an ELF file")
    if len(data) < 64 or data[4] != 2 or data[5] != 1:
        raise ElfError("only 64-bit little-endian ELF objects are supported")
    header = _unpack(_HEADER, data, 16)
    host = host_target()
    if header[1] != host.elf_machine:
        raise ElfError(f"ELF object for machine {header[1]}, not {host.arch}")
    return header


def _segments(data: ElfData) -> list[tuple[int, ...]]:
    _, _, _, _, phoff, _, _, _, phentsize, phnum, _, _, _ = _header(data)
    if phentsize < _PHDR.size:
        raise ElfError("truncated ELF program headers")
    return [_unpack(_PHDR, data, phoff + i * phentsize) for i in range(phnum)]


def is_executable(data: ElfData) -> bool:
    """Whether an object is a program: ET_EXEC, or a position-independent executable, which is
    ET_DYN with a PT_INTERP (a shared library has none)."""
    if _header(data)[0] == ET_EXEC:
        return True
    return any(segment[0] == PT_INTERP for segment in _segments(data))


def section(data: ElfData, name: str) -> bytes | None:
    """The content of the section called ``name`` (``.comment``, ...), None if there is none."""
    _, _, _, _, _, shoff, _, _, _, _, shentsize, shnum, shstrndx = _header(data)
    if not shoff or not shnum:
        return None
    if shentsize < _SHDR.size or shstrndx >= shnum or shnum > _MAX_ENTRIES:
        raise ElfError("bad ELF section headers")

    def header(index: int) -> tuple[int, ...]:
        return _unpack(_SHDR, data, shoff + index * shentsize)

    def content(entry: tuple[int, ...]) -> bytes:
        offset, size = entry[4], entry[5]
        if entry[1] == SHT_NOBITS:
            return b""
        if offset + size > len(data):
            raise ElfError("ELF section beyond the end of the file")
        return bytes(data[offset : offset + size])

    names = content(header(shstrndx))
    wanted = name.encode() + b"\0"
    for index in range(shnum):
        entry = header(index)
        if names[entry[0] :].startswith(wanted):
            return content(entry)
    return None


def _file_offset(loads: list[tuple[int, ...]], address: int) -> int:
    for _, _, offset, vaddr, _, filesz, _, _ in loads:
        if vaddr <= address < vaddr + filesz:
            return offset + address - vaddr
    raise ElfError(f"address {address:#x} is not mapped from the file")


@dataclass(frozen=True, slots=True)
class _Dynamic:
    """The parsed dynamic section of an object."""

    data: ElfData
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


def _dynamic(data: ElfData) -> _Dynamic | None:
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


def dynamic_info(data: ElfData) -> DynamicInfo:
    """DT_NEEDED, DT_RPATH, DT_RUNPATH and DT_SONAME of an ELF64 object."""
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


@dataclass(frozen=True, slots=True)
class VersionNeed:
    """What an object needs from one version namespace (GLIBCXX, CXXABI, ...) of one library."""

    highest: tuple[int, ...] | None  # the newest numeric version, GLIBCXX_3.4.22 as (3, 4, 22)
    markers: tuple[str, ...]  # the others, sorted: CXXABI_TM_1, GLIBC_PRIVATE


def needed_versions(data: ElfData) -> frozenset[tuple[str, str]]:
    """Symbol versions an object requires from its libraries (DT_VERNEED), each with the
    library: ("libc.so.6", "GLIBC_2.17"), ..."""
    section = _dynamic(data)
    if section is None or DT_VERNEED not in section.values:
        return frozenset()
    versions: set[tuple[str, str]] = set()
    budget = _MAX_ENTRIES  # shared by all entries: overlapping chains cannot multiply the work
    offset = _file_offset(section.loads, section.values[DT_VERNEED])
    for _ in range(min(section.values.get(DT_VERNEEDNUM, 0), _MAX_ENTRIES)):
        _, count, file, aux, following = _unpack(_VERNEED, data, offset)
        library = section.string(file)
        entry = offset + aux
        for _ in range(count):
            budget -= 1
            if budget < 0:
                raise ElfError("too many symbol version entries")
            _, _, _, name, next_entry = _unpack(_VERNAUX, data, entry)
            versions.add((library, section.string(name)))
            if not next_entry:
                break
            entry += next_entry
        if not following:
            break
        offset += following
    return frozenset(versions)


def glibc_requirement(data: ElfData) -> tuple[int, int] | None:
    """The newest glibc an object needs (GLIBC_2.2.5 counts as 2.2; ABI markers as the release
    that introduced them)."""
    versions = [version for _, version in needed_versions(data)]
    found = [(int(m[1]), int(m[2])) for v in versions if (m := _GLIBC.match(v))]
    found += [_GLIBC_ABI[v] for v in versions if v in _GLIBC_ABI]
    return max(found, default=None)


def version_requirements(data: ElfData) -> dict[str, dict[str, VersionNeed]]:
    """What an object needs per library and version namespace: libstdc++.so.6 ->
    GLIBCXX (3, 4, 22), CXXABI (1, 3, 11), ... A name that is not NAME_1.2.3 is a marker."""
    numeric: dict[str, dict[str, list[tuple[int, ...]]]] = {}
    markers: dict[str, dict[str, set[str]]] = {}
    for library, version in needed_versions(data):
        found = _NUMERIC.fullmatch(version)
        namespace = found[1] if found else version.partition("_")[0]
        numeric.setdefault(library, {}).setdefault(namespace, [])
        markers.setdefault(library, {}).setdefault(namespace, set())
        if found:
            numeric[library][namespace].append(tuple(int(part) for part in found[2].split(".")))
        else:
            markers[library][namespace].add(version)
    return {
        library: {
            namespace: VersionNeed(
                max(found, default=None), tuple(sorted(markers[library][namespace]))
            )
            for namespace, found in sorted(namespaces.items())
        }
        for library, namespaces in sorted(numeric.items())
    }
