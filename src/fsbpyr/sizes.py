"""Where an executable's bytes go (DX-5), from the reader's records alone: no format change.

The file is the stub, zero padding to a 64 KiB boundary, the payload and the trailer
(``payload.writer.assemble``). The payload is the header, the tables, then the blob data, each
blob 16-byte aligned. The rows below cover everything but the trailer, so they add up to the
file's size minus ``TRAILER_SIZE``.

A blob has an owner, a distribution or the runtime, and a part: code, sources, resources or
natives. Blobs shared by several owners go to the first in path order. Small blobs live in
blocks, which mix owners and have a stored size of their own, so a block's stored size is split
by its members' raw sizes and the rounding remainder goes to its first member.
"""

from collections import defaultdict
from dataclasses import dataclass

from .payload.format import HEADER_SIZE, NATIVE_KINDS, NONE, PIECES_OF, Codec, FileKind
from .payload.reader import Executable, Payload

PARTS = ("code", "sources", "resources", "natives")
_FILE_PART = {FileKind.SOURCE: "sources", FileKind.RESOURCE: "resources",
              FileKind.METADATA: "resources"}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Sizes:
    """Stored bytes of each part of the file, less the trailer."""

    stub: int
    stub_padding: int  # zeros up to the 64 KiB boundary where the payload starts
    payload_header: int
    tables: int  # the index tables and the string table
    blob_padding: int  # BLOB_ALIGN gaps between the blobs
    runtime: int  # the standard library and fsbpyr's boot modules, whatever their kind
    distributions: dict[str, dict[str, int]]  # name -> part -> bytes
    unreferenced: int  # blob data no module or file uses

    @property
    def total(self) -> int:
        return (self.stub + self.stub_padding + self.payload_header + self.tables
                + self.blob_padding + self.runtime + self.unreferenced
                + sum(sum(parts.values()) for parts in self.distributions.values()))  # fmt: skip


def _stored_sizes(payload: Payload) -> tuple[dict[int, int], int]:
    """Stored bytes of each blob a module or file can name, and the stored bytes of all the
    blob data. Blocks and pieces are in the total, not named: their share goes to the blobs
    they hold."""
    blobs = payload.blobs()
    members: dict[int, list[tuple[int, int]]] = defaultdict(list)  # block -> (position, index)
    inner: set[int] = set()  # blocks and pieces: data that no record names directly
    stored = {}
    for blob in blobs:
        if blob.codec is Codec.BLOCK:
            members[blob.offset].append((blob.stored_size, blob.index))
        elif blob.codec in PIECES_OF:
            pieces = range(blob.offset, blob.offset + blob.stored_size)
            inner.update(pieces)
            stored[blob.index] = sum(blobs[i].stored_size for i in pieces)
        else:
            stored[blob.index] = blob.stored_size
    total = sum(b.stored_size for b in blobs if b.codec is not Codec.BLOCK
                and b.codec not in PIECES_OF)  # fmt: skip
    for block, parts in members.items():
        inner.add(block)
        whole = blobs[block]
        parts.sort()
        shares = [whole.stored_size * blobs[i].size // (whole.size or 1) for _, i in parts]
        shares[0] += whole.stored_size - sum(shares)
        stored.update(zip((i for _, i in parts), shares, strict=True))
    return {i: n for i, n in stored.items() if i not in inner}, total


def _owned(payload: Payload) -> list[tuple[bytes, int, int, str, int]]:
    """(path, order, dist index, part, blob index) for every use of a blob, in path order."""
    uses = []
    for module in payload.modules():
        path = module.path.encode()
        for order, (blob, part) in enumerate(((module.code, "code"), (module.source, "sources"))):
            if blob != NONE:
                uses.append((path, order, module.dist, part, blob))
    for file in payload.files():
        part = "natives" if file.kind in NATIVE_KINDS else _FILE_PART[file.kind]
        uses.append((file.path.encode(), 0, file.dist, part, file.blob))
    return sorted(uses)


def measure(executable: Executable) -> Sizes:
    payload = executable.payload
    stored, data = _stored_sizes(payload)
    stub = executable.stub_prefix()
    stub_size = len(stub.rstrip(b"\0"))  # a stub's own zero tail counts as padding
    names = [d.name for d in payload.dists()]
    parts: dict[str, dict[str, int]] = {name: dict.fromkeys(PARTS, 0) for name in names}
    runtime = 0
    claimed: set[int] = set()
    for _, _, dist, part, blob in _owned(payload):
        if blob in claimed or blob not in stored:
            continue
        claimed.add(blob)
        if dist == NONE:
            runtime += stored[blob]
        else:
            parts[names[dist]][part] += stored[blob]
    attributed = runtime + sum(sum(p.values()) for p in parts.values())
    tables = payload.data_start - HEADER_SIZE
    return Sizes(
        stub_size, len(stub) - stub_size, HEADER_SIZE, tables,
        max(executable.payload_size - payload.data_start - data, 0),
        runtime, parts, data - attributed,
    )  # fmt: skip
