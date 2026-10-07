"""Member listing of Unix ``ar`` archives (GNU long-name variant, as written by llvm-ar)."""

from pathlib import Path

_MAGIC = b"!<arch>\n"
_HEADER_SIZE = 60


class ArError(ValueError):
    """Not a readable ar archive."""


def members(path: Path) -> list[str]:
    """Names of the regular members, in archive order (symbol and name tables skipped)."""
    data = path.read_bytes()
    if not data.startswith(_MAGIC):
        raise ArError(f"{path} is not an ar archive")
    names: list[str] = []
    longnames = b""
    pos = len(_MAGIC)
    while pos < len(data):
        header = data[pos : pos + _HEADER_SIZE]
        if len(header) != _HEADER_SIZE or header[58:60] != b"`\n":
            raise ArError(f"{path}: corrupt member header at offset {pos}")
        raw_name = header[:16].rstrip(b" ")
        size_field = header[48:58].strip()
        if not size_field.isdigit():
            raise ArError(f"{path}: invalid member size {size_field!r} at offset {pos}")
        size = int(size_field)
        if pos + _HEADER_SIZE + size > len(data):
            raise ArError(f"{path}: member at offset {pos} runs past the end of the archive")
        body = data[pos + _HEADER_SIZE : pos + _HEADER_SIZE + size]
        if raw_name == b"//":
            longnames = body
        elif raw_name not in (b"/", b"/SYM64/"):
            names.append(_member_name(raw_name, longnames, path))
        pos += _HEADER_SIZE + size + (size % 2)
    return names


def _member_name(raw: bytes, longnames: bytes, path: Path) -> str:
    if raw.startswith(b"/") and raw[1:].isdigit():
        offset = int(raw[1:])
        end = longnames.find(b"/\n", offset)
        if end < 0:
            raise ArError(f"{path}: bad long member name reference {raw!r}")
        return longnames[offset:end].decode()
    return raw.removesuffix(b"/").decode()
