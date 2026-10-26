"""The payload of an RPM package as a tar archive, stdlib only (plan Phase 6c, D25; EL8 sysroot).

An RPM is a 96-byte lead, the signature header (padded to 8 bytes), the main header and the
payload: a compressed cpio archive in "newc" format (magic 070701). Only the two payload tags of
the main header are read. The archive is converted to tar so that the sysroot's extraction
(``tarfile`` with its safety filter) handles it like a Debian package's. Not read: signatures
(the pins' SHA-256 are what is verified), devices and other special files (a sysroot has none:
refused), compressors but xz (what EL8 uses).
"""

import io
import lzma
import posixpath
import struct
import tarfile
from collections.abc import Iterator

LEAD = 96
_RPM_MAGIC = b"\xed\xab\xee\xdb"
_HEADER_MAGIC = b"\x8e\xad\xe8\x01"
_PAYLOADFORMAT, _PAYLOADCOMPRESSOR = 1124, 1125
_STRING = 6
_MAX_ENTRIES, _MAX_STORE = 0xFFFF, 0x0FFFFFFF  # rpm's own header limits
_MAX_PAYLOAD = 1 << 29  # decompressed: a sysroot package is far below (gcc: 60 MB)
_CPIO_MAGIC, _CPIO_HEADER = b"070701", 110
_KINDS = {0o040000: tarfile.DIRTYPE, 0o120000: tarfile.SYMTYPE, 0o100000: tarfile.REGTYPE}
# EL8's filesystem package makes these links into /usr (merged /usr); packages built there list
# the files where the links lead, but some still name the link (libgcc: lib64/libgcc_s.so.1)
_MERGED = frozenset({"bin", "sbin", "lib", "lib64"})


class RpmError(ValueError):
    """Not an RPM fsbpyr can read."""


def _header(data: bytes, offset: int) -> tuple[dict[int, str], int]:
    """The string tags of the header at ``offset`` and the offset after it."""
    if data[offset:offset + 4] != _HEADER_MAGIC:
        raise RpmError(f"no header at offset {offset}")
    try:
        count, size = struct.unpack_from(">II", data, offset + 8)
    except struct.error as exc:
        raise RpmError("truncated header") from exc
    index = offset + 16
    store = index + 16 * count
    if count > _MAX_ENTRIES or size > _MAX_STORE or store + size > len(data):
        raise RpmError("header sizes are impossible")
    tags: dict[int, str] = {}
    for entry in range(count):
        tag, kind, at, _ = struct.unpack_from(">IIII", data, index + 16 * entry)
        if kind != _STRING or tag not in (_PAYLOADFORMAT, _PAYLOADCOMPRESSOR):
            continue
        end = data.find(b"\0", store + at, store + size)
        if at >= size or end < 0:
            raise RpmError(f"tag {tag} lies outside the header")
        tags[tag] = data[store + at:end].decode("ascii", "replace")
    return tags, store + size


def _decompress(data: bytes, offset: int, compressor: str) -> bytes:
    if compressor not in ("xz", "lzma"):
        raise RpmError(f"payload compressor {compressor}: only xz is supported")
    unpacker = lzma.LZMADecompressor()
    try:
        raw = unpacker.decompress(data[offset:], _MAX_PAYLOAD + 1)
    except lzma.LZMAError as exc:
        raise RpmError(f"damaged payload: {exc}") from exc
    if len(raw) > _MAX_PAYLOAD or not unpacker.eof:
        raise RpmError("payload is too large or truncated")
    return raw


def _name(raw: str) -> str:
    """The member name inside the sysroot: relative, with /usr in front of the merged links."""
    path = posixpath.normpath(raw.removeprefix("./").lstrip("/") or ".")
    if path == ".." or path.startswith("../"):
        raise RpmError(f"{raw}: leaves the package")
    return "usr/" + path if path.partition("/")[0] in _MERGED else path


type _Entry = tuple[str, int, int, int, bytes, tuple[int, int, int]]  # name, mode, mtime,
# links, body, (device, inode)


def _cpio_entries(raw: bytes) -> Iterator[_Entry]:
    offset = 0
    while True:
        head = raw[offset:offset + _CPIO_HEADER]
        if len(head) < _CPIO_HEADER or head[:6] != _CPIO_MAGIC:
            raise RpmError(f"bad cpio header at {offset}")
        try:
            fields = [int(head[6 + 8 * i:14 + 8 * i], 16) for i in range(13)]
        except ValueError as exc:
            raise RpmError(f"bad cpio header at {offset}") from exc
        ino, mode, _, _, nlink, mtime, size, dmaj, dmin, _, _, namesize, _ = fields
        at = offset + _CPIO_HEADER
        name = raw[at:at + namesize - 1]
        body_at = at + namesize + (-(_CPIO_HEADER + namesize) % 4)
        body = raw[body_at:body_at + size]
        if namesize < 1 or len(name) != namesize - 1 or len(body) != size:
            raise RpmError(f"truncated cpio entry at {offset}")
        offset = body_at + size + (-size % 4)
        if name == b"TRAILER!!!":
            return
        yield name.decode("utf-8", "replace"), mode, mtime, nlink, body, (dmaj, dmin, ino)


def payload_tar(data: bytes) -> bytes:
    """The files of the RPM ``data`` as a tar archive (hard links as hard links: cpio stores
    the data with the last name of a set, tar needs it with the first)."""
    if data[:4] != _RPM_MAGIC:
        raise RpmError("not an RPM")
    _, end = _header(data, LEAD)  # the signature header
    tags, end = _header(data, end + (-end % 8))
    if (found := tags.get(_PAYLOADFORMAT, "cpio")) != "cpio":
        raise RpmError(f"payload format {found}")
    raw = _decompress(data, end, tags.get(_PAYLOADCOMPRESSOR, "gzip"))
    out = io.BytesIO()
    pending: dict[tuple[int, int, int], list[tarfile.TarInfo]] = {}
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for raw_name, mode, mtime, nlink, body, key in _cpio_entries(raw):
            kind = _KINDS.get(mode & 0o170000)
            if kind is None:
                raise RpmError(f"{raw_name}: neither a file, a directory nor a symlink")
            info = tarfile.TarInfo(_name(raw_name))
            info.mode, info.mtime, info.type = mode & 0o7777, mtime, kind
            if kind == tarfile.SYMTYPE:
                info.linkname = body.decode("utf-8", "replace")
            if kind != tarfile.REGTYPE:
                tar.addfile(info)
                continue
            names = pending.setdefault(key, []) if nlink > 1 else []
            if nlink > 1 and len(names) + 1 < nlink:
                names.append(info)
                continue
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
            for link in pending.pop(key, []):
                link.type, link.linkname = tarfile.LNKTYPE, info.name
                tar.addfile(link)
        if pending:
            raise RpmError("a set of hard links is incomplete")
    return out.getvalue()
