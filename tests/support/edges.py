"""Edge cases of the executable format (D38), one table for every reader of it: the Python reader
(tests/unit/payload/test_edges.py), the launcher's parsing code under libFuzzer
(tests/e2e/test_fuzz.py) and the launcher itself, release and sanitized
(tests/e2e/test_edges.py).

Each case corrupts one field of an intact executable of ``edge_app`` (every record kind, every
codec): an empty or truncated file, a trailer, header or section field, or a record field set
to 0, 1, a limit +-1, 2^32-1, 2^63 or 2^64-1, data that lies about its size, an index out of its
table. The digest is restamped, as a deliberate modification would, so that every check behind
the integrity check is reached. ``reader`` says whether the Python reader, with the stand-in's
cache sync, refuses the result (PayloadError when opened or when its records are used,
CacheError or OSError when extracted); ``launcher`` whether the launcher
refuses to start ("start", exit 70), refuses when the record is used ("access", PayloadError,
CacheError or OSError) or accepts it ("accept"); None where it cannot run (no stub left).
"""

import random
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import (
    BLOB,
    DIGEST_SIZE,
    FILE,
    HEADER_SIZE,
    MAX_BLOB_SIZE,
    MAX_BLOCK_SIZE,
    MODULE,
    NONE,
    TRAILER_SIZE,
    WORD,
    Codec,
    FileKind,
    ModuleKind,
    Section,
    tree_digest,
)
from fsbpyr.payload.reader import Executable
from fsbpyr.payload.spec import Blob, Dist, File, Module
from fsbpyr.runtime.registry import Runtime

from .apps import library

U32, U64 = 2**32 - 1, 2**64 - 1
SECTIONS = HEADER_SIZE - 16 * len(Section)  # the section table ends the header
REFUSALS = {"PayloadError", "CacheError", "OSError", "FileNotFoundError"}


def edge_files() -> tuple[File, ...]:
    """One file of each kind of blob; natives with digests, one with a closure."""
    noise = random.Random(7).randbytes(70 << 10)  # noqa: S311  # stored raw, over 64 KiB
    return (
        File("pkg/data.bin", FileKind.RESOURCE, Blob.zstd(b"data " * 30000), dist="edge"),
        File("pkg/raw.bin", FileKind.RESOURCE, Blob.zstd(noise), dist="edge"),
        File("lib/libx.so", FileKind.NATIVE_LIB, Blob.xz(b"\x7fELF x" * 20000, digest=True),
             mode=0o755),
        File("lib/libbig.so", FileKind.NATIVE_LIB, Blob.xz(library(1100), digest=True),
             mode=0o755),  # 4.5 MB: two pieces
        File("lib/_ext.so", FileKind.NATIVE_EXT, Blob.zstd(b"\x7fELF ext" * 999, digest=True),
             mode=0o755, closure=("lib/libx.so",)),
        File("edge-1.0.dist-info/METADATA", FileKind.METADATA,
             Blob.zstd(b"Metadata-Version: 2.4\nName: edge\nVersion: 1.0\n"), dist="edge"),
    )  # fmt: skip


def edge_app(modules: tuple[Module, ...], files: tuple[File, ...], entrypoint: str) -> App:
    pkg = (
        Module("pkg", "pkg/__init__.py", ModuleKind.PACKAGE, code=Blob.code(b"pkg code"),
               source=Blob.zstd(b"X = 1\n"), dist="edge"),
        Module("pkg.ns", "pkg/ns", ModuleKind.NAMESPACE, dist="edge"),
    )  # fmt: skip
    pkg_source = File("pkg/__init__.py", FileKind.SOURCE, Blob.zstd(b"X = 1\n"), dist="edge")
    return App("edge", entrypoint, modules=(*modules, *pkg),
               files=(*files, pkg_source, *edge_files()),
               dists=(Dist("edge", "edge-1.0.dist-info", "1.0"),))  # fmt: skip


def build(runtime: Runtime, out: Path, modules: tuple[Module, ...] = (),
          files: tuple[File, ...] = (), entrypoint: str = "pkg") -> Path:  # fmt: skip
    build_executable(runtime, edge_app(modules, files, entrypoint), out)
    return out


@dataclass(frozen=True)
class Layout:
    """Where the parts of an intact executable are, as absolute file offsets."""

    size: int
    payload: int  # payload offset
    sections: dict[Section, tuple[int, int]]  # absolute offset, count
    blob: dict[str, int]  # file path -> its blob index
    file: dict[str, int]  # file path -> FILES index
    module: dict[str, int]  # module name -> MODULES index
    pieces: int  # the first piece of lib/libbig.so
    member: int  # a block member: the blob of pkg/__init__.py
    block: int  # its block
    block_size: int

    def record(self, section: Section, index: int, size: int) -> int:
        return self.sections[section][0] + index * size


def layout(image: bytes) -> Layout:
    with Executable(image) as exe:
        payload, base = exe.payload, exe.payload_offset
        raw = [struct.unpack_from("<QQ", image, base + SECTIONS + 16 * s) for s in Section]
        files = payload.files()
        blob = {f.path: f.blob for f in files}
        member = payload.blob(blob["pkg/__init__.py"])
        assert member.codec is Codec.BLOCK
        return Layout(
            size=len(image),
            payload=base,
            sections={s: (base + raw[s][0], raw[s][1]) for s in Section},
            blob=blob,
            file={f.path: f.index for f in files},
            module={m.name: m.index for m in payload.modules()},
            pieces=payload.blob(blob["lib/libbig.so"]).offset,
            member=member.index,
            block=member.offset,
            block_size=payload.blob(member.offset).size,
        )


def restamp(image: bytearray) -> bytes:
    if len(image) >= DIGEST_SIZE:  # a shorter file has no digest to restamp
        image[-DIGEST_SIZE:] = tree_digest(image[:-DIGEST_SIZE])
    return bytes(image)


def refused(errors: list[str]) -> bool:
    """Whether a walk (tests/support/walk.py) met a refusal; any other error fails the test."""
    assert {error.partition(": ")[2] for error in errors} <= REFUSALS, errors
    return bool(errors)


Mutation = Callable[[bytearray, Layout], bytes | None]


@dataclass(frozen=True)
class Case:
    id: str
    mutate: Mutation  # returns a whole new image, or None after changing ``image`` in place
    reader: bool  # the Python reader refuses it
    launcher: str | None  # "start", "access", "accept", or None: not an executable any more


def apply(case: Case, image: bytes) -> bytes:
    """The case's image, restamped."""
    work = bytearray(image)
    replaced = case.mutate(work, layout(image))
    return restamp(bytearray(replaced)) if replaced is not None else restamp(work)


def _put(fmt: str, where: Callable[[Layout], int], value: int) -> Mutation:
    def mutate(image: bytearray, at: Layout) -> None:
        struct.pack_into(fmt, image, where(at), value)

    return mutate


def _trailer(field: int, fmt: str, value: int) -> Mutation:
    return _put(fmt, lambda at: at.size - TRAILER_SIZE + field, value)


def _header(field: int, fmt: str, value: int) -> Mutation:
    return _put(fmt, lambda at: at.payload + field, value)


def _section(section: Section, field: int, value: int) -> Mutation:
    return _put("<Q", lambda at: at.payload + SECTIONS + 16 * section + field, value)


def _blob(which: Callable[[Layout], int], field: int, fmt: str, value: int) -> Mutation:
    return _put(fmt, lambda at: at.record(Section.BLOBS, which(at), BLOB.size) + field, value)


def _file(path: str, field: int, fmt: str, value: int) -> Mutation:
    return _put(fmt, lambda at: at.record(Section.FILES, at.file[path], FILE.size) + field, value)


def _module(name: str, field: int, fmt: str, value: int) -> Mutation:
    return _put(fmt, lambda at: at.record(Section.MODULES, at.module[name], MODULE.size) + field,
                value)  # fmt: skip


def _both(*mutations: Mutation) -> Mutation:
    def mutate(image: bytearray, at: Layout) -> None:
        for mutation in mutations:
            mutation(image, at)

    return mutate


def _field(image: bytearray, offset: int, fmt: str) -> int:
    value: int = struct.unpack_from(fmt, image, offset)[0]
    return value


def _adjusted(path: str, field: int, delta: int) -> Mutation:
    """A blob field of ``path``'s blob, ``delta`` away from what it is."""

    def mutate(image: bytearray, at: Layout) -> None:
        offset = at.record(Section.BLOBS, at.blob[path], BLOB.size) + field
        struct.pack_into("<Q", image, offset, _field(image, offset, "<Q") + delta)

    return mutate


def _closure_word(offset: int, value: int | Callable[[Layout], int]) -> Mutation:
    """The ``offset``-th word of lib/_ext.so's closure (0: its count)."""

    def mutate(image: bytearray, at: Layout) -> None:
        record = at.record(Section.FILES, at.file["lib/_ext.so"], FILE.size)
        start = _field(image, record + 20, "<I")
        word = value(at) if callable(value) else value
        struct.pack_into("<I", image, at.record(Section.CLOSURES, start + offset, WORD.size), word)

    return mutate


def _payload_of(size: int) -> Mutation:
    """The stub, the payload's first ``size`` bytes, and a trailer saying so."""

    def mutate(image: bytearray, at: Layout) -> bytes:
        trailer = bytearray(image[-TRAILER_SIZE:])
        struct.pack_into("<Q", trailer, 24, size)
        return bytes(image[: at.payload + size]) + bytes(trailer)

    return mutate


def _no_nul(image: bytearray, at: Layout) -> None:
    """lib/libx.so's path one byte longer: the byte after it is no NUL."""
    record = at.record(Section.FILES, at.file["lib/libx.so"], FILE.size)
    struct.pack_into("<I", image, record + 4, _field(image, record + 4, "<I") + 1)


def _nul_in_path(image: bytearray, at: Layout) -> None:
    """A NUL inside lib/libx.so's path: "lib/\\0ibx.so", its length unchanged."""
    record = at.record(Section.FILES, at.file["lib/libx.so"], FILE.size)
    image[at.sections[Section.STRTAB][0] + _field(image, record, "<I") + 4] = 0


def _last_strtab_byte(image: bytearray, at: Layout) -> None:
    record = at.record(Section.FILES, at.file["pkg/data.bin"], FILE.size)
    struct.pack_into("<II", image, record, at.sections[Section.STRTAB][1] - 1, 0)


B = Section.BLOBS
DATA, RAW, LIBX, BIG = "pkg/data.bin", "pkg/raw.bin", "lib/libx.so", "lib/libbig.so"


def _b(path: str) -> Callable[[Layout], int]:
    return lambda at: at.blob[path]


# fmt: off
CASES: tuple[Case, ...] = (
    # whole files: nothing a launcher could be started from any more
    Case("empty", lambda image, at: b"", True, None),
    Case("63 bytes", lambda image, at: bytes(image[-63:]), True, None),
    Case("the trailer alone", lambda image, at: bytes(image[-TRAILER_SIZE:]), True, None),
    # trailer
    Case("trailer magic", _trailer(0, "<B", 0), True, "start"),
    Case("trailer version 4", _trailer(8, "<I", 4), True, "start"),
    Case("trailer version 2^32-1", _trailer(8, "<I", U32), True, "start"),
    Case("trailer flags", _trailer(12, "<I", 1), True, "start"),
    Case("payload offset unaligned", _trailer(16, "<Q", 4097), True, "start"),
    Case("payload offset 2^63", _trailer(16, "<Q", 2**63), True, "start"),
    Case("payload offset 2^64-4096", _trailer(16, "<Q", U64 - 4095), True, "start"),
    Case("payload size 0", _trailer(24, "<Q", 0), True, "start"),
    Case("payload size 1", _trailer(24, "<Q", 1), True, "start"),
    Case("payload size 2^64-1", _trailer(24, "<Q", U64), True, "start"),
    Case("payload of 0 bytes, the trailer agreeing", _payload_of(0), True, "start"),
    Case("payload shorter than its header", _payload_of(HEADER_SIZE - 1), True, "start"),
    Case("payload, only its header", _payload_of(HEADER_SIZE), True, "start"),
    # header and section table
    Case("header magic", _header(0, "<B", 0), True, "start"),
    Case("header version 4", _header(8, "<I", 4), True, "start"),
    Case("header size + 1", _header(12, "<I", HEADER_SIZE + 1), True, "start"),
    Case("blobs at 2^64-8", _section(B, 0, U64 - 7), True, "start"),
    Case("blobs at 2^63", _section(B, 0, 2**63), True, "start"),
    Case("blobs misaligned", _section(Section.FILES, 0, 4), True, "start"),
    Case("blob count * 32 wraps", _section(B, 8, 2**59), True, "start"),
    Case("blob count 2^64-1", _section(B, 8, U64), True, "start"),
    Case("file count + 10^9", _section(Section.FILES, 8, 10**9), True, "start"),
    Case("no string table", _section(Section.STRTAB, 8, 0), True, "start"),
    # blob records (a resource's, a native's, an xz library in pieces, a block member)
    Case("blob offset 2^64-16", _blob(_b(DATA), 0, "<Q", U64 - 15), True, "access"),
    Case("blob offset + stored wraps", _both(_blob(_b(RAW), 0, "<Q", U64 - 15),
                                            _blob(_b(RAW), 8, "<Q", 32)), True, "access"),
    Case("blob stored 2^64-1", _blob(_b(DATA), 8, "<Q", U64), True, "access"),
    Case("blob size 2^64-1", _blob(_b(DATA), 16, "<Q", U64), True, "access"),
    Case("blob size limit + 1", _blob(_b(LIBX), 16, "<Q", MAX_BLOB_SIZE + 1), True, "access"),
    Case("raw blob one byte longer", _adjusted(RAW, 16, 1), True, "access"),
    Case("blob codec 255", _blob(_b(DATA), 24, "<B", 255), True, "access"),
    Case("native without a digest", _blob(_b(LIBX), 28, "<I", NONE), True, "access"),
    Case("digest index out of the table", _blob(_b(LIBX), 28, "<I", 10**6), True, "access"),
    Case("zstd frame of another size", _adjusted(DATA, 16, -1), True, "access"),
    Case("xz stream claiming 16 GiB", _blob(_b(LIBX), 16, "<Q", 16 << 30), True, "access"),
    Case("pieces: 0", _blob(_b(BIG), 8, "<Q", 0), True, "access"),
    Case("pieces: 1", _blob(_b(BIG), 8, "<Q", 1), True, "access"),
    Case("pieces: 2^32", _blob(_b(BIG), 8, "<Q", 2**32), True, "access"),
    Case("first piece out of the table", _blob(_b(BIG), 0, "<Q", 10**6), True, "access"),
    Case("piece codec zstd", _blob(lambda at: at.pieces, 24, "<B", Codec.ZSTD), True, "access"),
    Case("block member past its block", lambda image, at: _both(
         _blob(lambda a: a.member, 8, "<Q", at.block_size),
         _blob(lambda a: a.member, 16, "<Q", 1))(image, at), True, "access"),
    Case("block member of 0 bytes at its block's end", lambda image, at: _both(
         _blob(lambda a: a.member, 8, "<Q", at.block_size),
         _blob(lambda a: a.member, 16, "<Q", 0))(image, at), False, "accept"),
    Case("block member at 1 MiB + 1", _blob(lambda at: at.member, 8, "<Q", MAX_BLOCK_SIZE + 1),
         True, "access"),
    Case("block out of the table", _blob(lambda at: at.member, 0, "<Q", 10**6), True, "access"),
    # file records
    Case("path at the string table's last byte", _last_strtab_byte, True, "access"),
    Case("path without its NUL", _no_nul, True, "access"),
    Case("path with a NUL inside", _nul_in_path, True, "access"),
    Case("path 2^32-1 long", _file(DATA, 4, "<I", U32), True, "access"),
    Case("path offset 2^32-1", _file(DATA, 0, "<I", U32), True, "access"),
    Case("file kind 0", _file(DATA, 8, "<B", 0), True, "access"),
    Case("file kind 255", _file(DATA, 8, "<B", 255), True, "access"),
    Case("file mode 0o7777", _file(LIBX, 10, "<H", 0o7777), True, "access"),
    Case("file blob NONE", _file(DATA, 12, "<I", NONE), True, "access"),
    Case("file blob = blob count", lambda image, at: _file(DATA, 12, "<I",
         at.sections[B][1])(image, at), True, "access"),
    Case("closure count 0", _closure_word(0, 0), True, "access"),
    Case("closure count 2^32-1", _closure_word(0, U32), True, "access"),
    Case("closure member out of the table", _closure_word(1, 10**6), True, "access"),
    Case("closure out of its table", _file("lib/_ext.so", 20, "<I", 10**6), True, "access"),
    Case("closure lists its own file", _closure_word(1, lambda at: at.file["lib/_ext.so"]),
         True, "accept"),  # extracted twice: the same file
    Case("closure lists a resource", _closure_word(1, lambda at: at.file[DATA]), True, "access"),
    # module records
    Case("module kind 0", _module("pkg", 16, "<B", 0), True, "access"),
    Case("module kind 255", _module("pkg", 16, "<B", 255), True, "access"),
    Case("module name 2^32-1 long", _module("pkg", 4, "<I", U32), True, "access"),
)
# fmt: on


class PathCase(NamedTuple):
    """A native file's path a valid writer never writes (built with its checks off), whether
    the Python reader refuses it, and what the launcher does (as for ``Case``)."""

    id: str
    path: str
    reader: bool
    launcher: str


# fmt: off
PATH_CASES: tuple[PathCase, ...] = (
    PathCase("empty path", "", True, "access"),
    PathCase("path '.'", ".", True, "access"),
    PathCase("path '..'", "..", True, "access"),
    PathCase("path 'lib/../x.so'", "lib/../x.so", True, "access"),
    PathCase("path 'lib//x.so'", "lib//x.so", True, "access"),
    PathCase("path 'lib/'", "lib/", True, "access"),
    PathCase("path '/x.so'", "/x.so", True, "access"),
    PathCase("path with a backslash", "lib/x\\.so", True, "access"),
    PathCase("a 255-byte component", "lib/" + "c" * 255, False, "accept"),
    PathCase("a 256-byte component", "lib/" + "c" * 256, True, "access"),  # the OS refuses it
    PathCase("a 256-byte directory", "lib/" + "d" * 256 + "/x.so", True, "access"),
    PathCase("500 directories deep", "d/" * 500 + "x.so", False, "accept"),  # 1 KiB descriptors
    # past the cache's 4 KiB directory path: refused until 8b leaves the limit to the OS
    PathCase("an 8 KiB path", ("d" * 250 + "/") * 32 + "x.so", True, "access"),
    PathCase("2100 directories deep", "d/" * 2100 + "x.so", True, "access"),
)
# fmt: on
