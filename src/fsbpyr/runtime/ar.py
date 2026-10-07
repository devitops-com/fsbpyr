"""Member listing of Unix ``ar`` archives (GNU long-name variant, as written by llvm-ar)."""

from collections.abc import Iterator
from pathlib import Path

_MAGIC = b"!<arch>\n"
_HEADER_SIZE = 60


class ArError(ValueError):
    """Not a readable ar archive."""


def members(path: Path) -> list[str]:
    """Names of the regular members, in archive order (symbol and name tables skipped)."""
    data = path.read_bytes()
    return [name for name, _, _ in _entries(data, str(path))]


def read(path: Path) -> list[tuple[str, bytes]]:
    """(name, content) of the regular members, in archive order."""
    return parse(path.read_bytes(), str(path))


def parse(data: bytes, label: str) -> list[tuple[str, bytes]]:
    """(name, content) of the regular members of an archive held in memory."""
    return [(name, data[start:end]) for name, start, end in _entries(data, label)]


def _entries(data: bytes, label: str) -> Iterator[tuple[str, int, int]]:
    if not data.startswith(_MAGIC):
        raise ArError(f"{label} is not an ar archive")
    longnames = b""
    pos = len(_MAGIC)
    while pos < len(data):
        header = data[pos : pos + _HEADER_SIZE]
        if len(header) != _HEADER_SIZE or header[58:60] != b"`\n":
            raise ArError(f"{label}: corrupt member header at offset {pos}")
        raw_name = header[:16].rstrip(b" ")
        size_field = header[48:58].strip()
        if not size_field.isdigit():
            raise ArError(f"{label}: invalid member size {size_field!r} at offset {pos}")
        size = int(size_field)
        start = pos + _HEADER_SIZE
        if start + size > len(data):
            raise ArError(f"{label}: member at offset {pos} runs past the end of the archive")
        if raw_name == b"//":
            longnames = data[start : start + size]
        elif raw_name not in (b"/", b"/SYM64/"):
            yield _member_name(raw_name, longnames, label), start, start + size
        pos = start + size + (size % 2)


def _member_name(raw: bytes, longnames: bytes, label: str) -> str:
    if raw.startswith(b"/") and raw[1:].isdigit():
        offset = int(raw[1:])
        end = longnames.find(b"/\n", offset)
        if end < 0:
            raise ArError(f"{label}: bad long member name reference {raw!r}")
        name = longnames[offset:end]
    else:
        name = raw.removesuffix(b"/")
    try:
        return name.decode()
    except UnicodeDecodeError:
        raise ArError(f"{label}: member name {name!r} is not UTF-8") from None
