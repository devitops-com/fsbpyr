"""Synthetic RPM packages for the sysroot's tests: no network, no rpm tool."""

import lzma
import struct

S_DIR, S_LNK, S_REG, S_CHR = 0o040000, 0o120000, 0o100000, 0o020000

type Entry = tuple[str, int, bytes, int, int]  # name, type and permissions, body, links, inode


def file(
    name: str, body: bytes = b"", mode: int = 0o644, *, links: int = 1, inode: int = 0
) -> Entry:
    return name, S_REG | mode, body, links, inode


def directory(name: str) -> Entry:
    return name, S_DIR | 0o755, b"", 2, 0


def symlink(name: str, target: str) -> Entry:
    return name, S_LNK | 0o777, target.encode(), 1, 0


def cpio(entries: list[Entry]) -> bytes:
    """A newc archive. Inode numbers: ``inode`` when given, else one per entry."""
    out = bytearray()
    for number, (name, mode, body, links, inode) in enumerate(entries, 1):
        raw = name.encode() + b"\0"
        fields = (inode or number, mode, 0, 0, links, 0, len(body), 0, 0, 0, 0, len(raw), 0)
        out += b"070701" + "".join(f"{field:08x}" for field in fields).encode() + raw
        out += b"\0" * (-(110 + len(raw)) % 4) + body + b"\0" * (-len(body) % 4)
    trailer = b"TRAILER!!!\0"
    fields = (0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, len(trailer), 0)
    last = b"070701" + "".join(f"{f:08x}" for f in fields).encode() + trailer
    return bytes(out) + last + b"\0" * 3


def header(strings: dict[int, str]) -> bytes:
    """An RPM header with string tags."""
    index, store = b"", b""
    for tag, text in strings.items():
        index += struct.pack(">IIII", tag, 6, len(store), 1)
        store += text.encode() + b"\0"
    counts = struct.pack(">II", len(strings), len(store))
    return b"\x8e\xad\xe8\x01\0\0\0\0" + counts + index + store


def rpm(entries: list[Entry], *, compressor: str = "xz", payload: bytes | None = None) -> bytes:
    """An RPM whose payload holds ``entries`` (``payload``: these bytes instead of the
    compressed archive)."""
    lead = b"\xed\xab\xee\xdb\x03\x00\0\0" + b"\0" * 88
    signature = header({})
    main = header({1124: "cpio", 1125: compressor})
    body = lzma.compress(cpio(entries), format=lzma.FORMAT_XZ) if payload is None else payload
    return lead + signature + b"\0" * (-len(signature) % 8) + main + body
