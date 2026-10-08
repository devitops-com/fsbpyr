"""Small blobs packed into shared zstd blocks (format v2, D30)."""

import dataclasses
import struct

import pytest

from fsbpyr.payload.convert import spec_from_payload
from fsbpyr.payload.format import (
    BLOB,
    BLOCK_MEMBER_MAX,
    BLOCK_SIZE,
    CODE_BLOCK_SIZE,
    MAX_BLOCK_SIZE,
    NONE,
    Codec,
    FileKind,
    ModuleFlag,
    ModuleKind,
    PayloadError,
    Section,
)
from fsbpyr.payload.reader import Payload
from fsbpyr.payload.spec import Blob, File, Module, PayloadSpec
from fsbpyr.payload.writer import encode_payload

from .conftest import GENERATION, MAGIC, PY_HEXVERSION

BODIES = {f"m{i:03}": bytes([i % 251]) * 20_000 + b"%d" % i for i in range(40)}
BIG = b"B" * (BLOCK_MEMBER_MAX + 1)


def _module(name: str, body: bytes, *, startup: bool = False) -> Module:
    flags = ModuleFlag.STARTUP if startup else ModuleFlag(0)
    return Module(name, f"{name}.py", ModuleKind.MODULE, code=Blob.code(body), flags=flags,
                  source=Blob.zstd(b"# " + body))  # fmt: skip


@pytest.fixture
def blocked(spec: PayloadSpec) -> tuple[PayloadSpec, bytes]:
    extra = (*(_module(n, b) for n, b in BODIES.items()), _module("big", BIG))
    full = dataclasses.replace(spec, modules=(*spec.modules, *extra), blocks=True)
    return full, encode_payload(full)


def _members(payload: Payload) -> list[int]:
    return [b.index for b in payload.blobs() if b.codec is Codec.BLOCK]


def test_small_blobs_share_blocks_and_read_back(blocked: tuple[PayloadSpec, bytes]) -> None:
    payload = Payload(blocked[1])

    members = _members(payload)
    blocks = {payload.blob(i).offset for i in members}
    assert len(members) > len(blocks) > 1  # several members per block
    for block in blocks:
        record = payload.blob(block)
        assert record.codec is Codec.ZSTD and record.size <= BLOCK_SIZE
    for name, body in BODIES.items():
        module = payload.find_module(name)
        assert module is not None and payload.blob(module.code).codec is Codec.BLOCK
        assert payload.read_blob(module.code) == body
        assert payload.read_blob(module.source) == b"# " + body
    big = payload.find_module("big")
    assert big is not None and payload.blob(big.code).codec is Codec.ZSTD  # too large for a block
    assert payload.read_blob(big.code) == BIG


def test_natives_and_startup_code_are_placed_apart(blocked: tuple[PayloadSpec, bytes]) -> None:
    payload = Payload(blocked[1])

    natives = [f for f in payload.files() if f.kind.name.startswith("NATIVE")]
    assert natives and all(payload.blob(f.blob).codec is not Codec.BLOCK for f in natives)
    boot = payload.find_module("_boot")
    assert boot is not None
    startup_block = payload.blob(payload.blob(boot.code).offset)
    others = {payload.blob(payload.blob(m.code).offset).offset for m in payload.modules()
              if m.code != 0xFFFFFFFF and m.name != "_boot"
              and payload.blob(m.code).codec is Codec.BLOCK}  # fmt: skip
    assert startup_block.offset < min(others)  # read first, on every start
    assert startup_block.offset not in others


def test_blocked_payloads_round_trip_byte_for_byte(blocked: tuple[PayloadSpec, bytes]) -> None:
    """Reusing a runtime's (or one's own) payload must reproduce it exactly (self-hosting)."""
    payload = Payload(blocked[1])

    assert encode_payload(spec_from_payload(payload)) == blocked[1]
    assert (
        encode_payload(dataclasses.replace(blocked[0], modules=blocked[0].modules[::-1]))
        == (blocked[1])
    )


def test_blocks_can_be_turned_off(blocked: tuple[PayloadSpec, bytes]) -> None:
    payload = Payload(encode_payload(dataclasses.replace(blocked[0], blocks=False)))

    assert not _members(payload)
    module = payload.find_module("m001")
    assert module is not None and payload.read_blob(module.code) == BODIES["m001"]


def _member_record(raw: bytes) -> tuple[int, int]:
    """(file offset of the first member record, its index)."""
    payload = Payload(raw)
    index = _members(payload)[0]
    return payload._sections[Section.BLOBS][0] + index * BLOB.size, index


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (0, 10**9, "block"),  # block index out of range
        (8, MAX_BLOCK_SIZE, "block"),  # position beyond any block
        (16, MAX_BLOCK_SIZE + 1, "block"),  # member larger than a block can be
    ],
)
def test_reader_rejects_bad_member_records(
    blocked: tuple[PayloadSpec, bytes], field: int, value: int, message: str
) -> None:
    raw = bytearray(blocked[1])
    offset, _ = _member_record(bytes(raw))
    struct.pack_into("<Q", raw, offset + field, value)

    with pytest.raises(PayloadError, match=message):
        Payload(bytes(raw))


def test_reader_rejects_members_of_non_blocks(blocked: tuple[PayloadSpec, bytes]) -> None:
    raw = bytearray(blocked[1])
    payload = Payload(bytes(raw))
    offset, index = _member_record(bytes(raw))
    other_member = next(i for i in _members(payload) if i != index)
    raw_lib = next(f.blob for f in payload.files() if f.path == "demo.libs/libfoo.so.1")
    assert payload.blob(raw_lib).codec is Codec.NONE
    for target in (other_member, raw_lib):  # a member, and a blob that is not a zstd frame
        struct.pack_into("<Q", raw, offset, target)
        with pytest.raises(PayloadError, match="block"):
            Payload(bytes(raw))


def test_the_spec_must_keep_code_checksummed() -> None:
    """Raw code is only allowed when it will be packed into a (checksummed) block."""
    spec = make_small_spec(code=Blob.raw(b"x" * (BLOCK_MEMBER_MAX + 1)))

    with pytest.raises(PayloadError, match="checksummed"):
        encode_payload(spec)
    with pytest.raises(PayloadError, match="checksummed"):
        encode_payload(dataclasses.replace(make_small_spec(code=Blob.raw(b"x")), blocks=False))
    encode_payload(make_small_spec(code=Blob.raw(b"x")))  # packed into a block: fine


def make_small_spec(*, code: Blob) -> PayloadSpec:
    return PayloadSpec(PY_HEXVERSION, MAGIC, GENERATION, "demo", "m:main",
                       modules=(Module("m", "m.py", ModuleKind.MODULE, code=code),))  # fmt: skip


# --- packing rules (D30) ----------------------------------------------------------------------


def _owners(payload: Payload) -> dict[int, set[str]]:
    """Block index -> classes of its members (startup code, code, source, data)."""
    classes: dict[int, set[str]] = {}

    def note(blob: int, kind: str) -> None:
        if blob != NONE and payload.blob(blob).codec is Codec.BLOCK:
            classes.setdefault(payload.blob(blob).offset, set()).add(kind)

    for module in payload.modules():
        startup = ModuleFlag.STARTUP in module.flags
        note(module.code, "startup" if startup else "code")
        note(module.source, "source")
    for file in payload.files():
        note(file.blob, "source" if file.kind is FileKind.SOURCE else "data")
    return classes


def _rich_spec() -> PayloadSpec:
    modules = tuple(_module(f"c{i:03}", bytes([i % 251]) * 9000 + b"%d" % i) for i in range(300))
    files = tuple(File(f"d/f{i:03}.txt", FileKind.RESOURCE,
                       Blob.raw(bytes([i]) * 9000 + b"f%d" % i))  # unlike any module's code
                  for i in range(60))  # fmt: skip
    return dataclasses.replace(make_small_spec(code=Blob.code(b"x")), modules=modules, files=files)


def test_each_block_holds_one_class_and_code_blocks_are_small() -> None:
    payload = Payload(encode_payload(_rich_spec()))

    classes = _owners(payload)
    assert all(len(kinds) == 1 for kinds in classes.values())  # an import never reads sources
    sizes = {kind: [payload.blob(b).size for b, k in classes.items() if kind in k]
             for kind in ("code", "source", "data")}  # fmt: skip
    assert max(sizes["code"]) <= CODE_BLOCK_SIZE < max(sizes["data"]) <= BLOCK_SIZE
    assert max(sizes["source"]) > CODE_BLOCK_SIZE


@pytest.mark.parametrize(
    ("size", "packed"), [(BLOCK_MEMBER_MAX - 1, True), (BLOCK_MEMBER_MAX, False)]
)
def test_member_size_limit(size: int, packed: bool) -> None:
    spec = dataclasses.replace(
        make_small_spec(code=Blob.code(b"x")),
        files=(File("d/x.bin", FileKind.RESOURCE, Blob.raw(b"z" * size)),),
    )
    payload = Payload(encode_payload(spec))

    record = payload.find_file("d/x.bin")
    assert record is not None and (payload.blob(record.blob).codec is Codec.BLOCK) is packed
    assert payload.read_blob(record.blob) == b"z" * size


def test_natives_stay_out_of_blocks_even_when_a_resource_has_their_bytes() -> None:
    lib = b"\x7fELF" + bytes(range(256)) * 4
    files = (File("a/lib.so", FileKind.NATIVE_LIB, Blob.raw(lib, digest=True)),
             File("b/r.txt", FileKind.RESOURCE, Blob.zstd(lib)))  # fmt: skip
    payload = Payload(encode_payload(dataclasses.replace(make_small_spec(code=Blob.code(b"x")),
                                                         files=files)))  # fmt: skip

    native, resource = payload.find_file("a/lib.so"), payload.find_file("b/r.txt")
    assert native is not None and resource is not None
    assert payload.blob(native.blob).codec is Codec.NONE
    assert payload.blob(resource.blob).codec is Codec.BLOCK
    assert payload.read_blob(native.blob) == payload.read_blob(resource.blob) == lib


def test_identical_content_keeps_the_digest_either_copy_carries() -> None:
    data = b"same bytes " * 100
    files = (File("a.txt", FileKind.RESOURCE, Blob.raw(data)),
             File("b.txt", FileKind.RESOURCE, Blob.zstd(data, digest=True)))  # fmt: skip
    payload = Payload(encode_payload(dataclasses.replace(make_small_spec(code=Blob.code(b"x")),
                                                         files=files)))  # fmt: skip

    records = [payload.find_file(p) for p in ("a.txt", "b.txt")]
    assert records[0] is not None and records[1] is not None
    assert records[0].blob == records[1].blob
    assert payload.blob(records[0].blob).digest != NONE


def test_writer_reports_an_invalid_frame_as_a_payload_error() -> None:
    files = (File("r.txt", FileKind.RESOURCE, Blob(b"not a zstd frame", 50, Codec.ZSTD)),)

    with pytest.raises(PayloadError, match="zstd"):
        encode_payload(dataclasses.replace(make_small_spec(code=Blob.code(b"x")), files=files))


def test_empty_blobs_keep_records_of_their_own() -> None:
    spec = dataclasses.replace(
        make_small_spec(code=Blob.code(b"x")),
        files=(File("d/py.typed", FileKind.RESOURCE, Blob.raw(b"")),),
    )

    payload = Payload(encode_payload(spec))

    record = payload.find_file("d/py.typed")
    assert record is not None and payload.blob(record.blob).codec is Codec.NONE
    assert payload.read_blob(record.blob) == b""
    with pytest.raises(PayloadError, match="checksummed"):
        encode_payload(make_small_spec(code=Blob.raw(b"")))  # empty raw code: never in a block


def test_identical_content_is_packed_once_whatever_its_representation() -> None:
    """A runtime blob comes back raw from an executable, the same content as an app's zstd
    frame: both must pack identically (self-hosting fixed point) and only once."""
    text = b"same content in two representations\n" * 40
    files = (File("a/one.txt", FileKind.RESOURCE, Blob.raw(text)),
             File("b/two.txt", FileKind.RESOURCE, Blob.zstd(text, level=3)))  # fmt: skip
    spec = dataclasses.replace(make_small_spec(code=Blob.code(b"x")), files=files)

    raw = encode_payload(spec)
    payload = Payload(raw)

    one, two = payload.find_file("a/one.txt"), payload.find_file("b/two.txt")
    assert one is not None and two is not None and one.blob == two.blob
    assert encode_payload(spec_from_payload(payload)) == raw


def test_reader_cache_evicts_and_rereads_correctly() -> None:
    spec = _rich_spec()
    payload = Payload(encode_payload(spec))
    blobs = [f.blob for f in payload.files()] + [m.code for m in payload.modules()]
    assert len({payload.blob(b).offset for b in blobs if payload.blob(b).codec is Codec.BLOCK}) > 32

    first = [payload.read_blob(b) for b in blobs]
    again = [payload.read_blob(b) for b in reversed(blobs)][::-1]

    assert first == again and len(payload._block_cache) <= 32
    member = next(b for b in blobs if payload.blob(b).codec is Codec.BLOCK)
    assert payload.contains(member, payload.read_blob(member)[3:20])


def test_reader_rejects_members_reaching_past_their_block(
    blocked: tuple[PayloadSpec, bytes],
) -> None:
    raw = bytearray(blocked[1])
    payload = Payload(bytes(raw))
    offset, index = _member_record(bytes(raw))
    block = payload.blob(payload.blob(index).offset)
    struct.pack_into("<Q", raw, offset + 8, block.size - payload.blob(index).size + 1)

    with pytest.raises(PayloadError, match="past the end of its block"):
        Payload(bytes(raw))
    block_offset = payload._sections[Section.BLOBS][0] + block.index * BLOB.size
    raw = bytearray(blocked[1])
    struct.pack_into("<Q", raw, block_offset + 16, MAX_BLOCK_SIZE + 1)  # a block too large

    with pytest.raises(PayloadError, match="block"):
        Payload(bytes(raw))


def test_version_1_payloads_are_read_and_may_not_use_blocks(
    blocked: tuple[PayloadSpec, bytes],
) -> None:
    classic = bytearray(encode_payload(dataclasses.replace(blocked[0], blocks=False)))
    struct.pack_into("<I", classic, 8, 1)  # format version 1: what 0.4.0 and earlier wrote
    Payload(bytes(classic))
    with_blocks = bytearray(blocked[1])
    struct.pack_into("<I", with_blocks, 8, 1)

    with pytest.raises(PayloadError, match="version 1"):
        Payload(bytes(with_blocks))
