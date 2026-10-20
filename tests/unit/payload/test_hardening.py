"""Regression tests for review findings: hostile sizes, file-level errors, spec rules on read."""

import struct
from pathlib import Path

import pytest

from fsbpyr.compat import zstd
from fsbpyr.payload.format import (
    BLOB,
    MAX_BLOB_SIZE,
    MODULE,
    NONE,
    TRAILER_SIZE,
    Codec,
    FileFlag,
    FileKind,
    ModuleFlag,
    ModuleKind,
    PayloadError,
    Section,
)
from fsbpyr.payload.reader import Executable, Payload
from fsbpyr.payload.spec import Blob, Dist, File, Module, stored_bytes
from fsbpyr.payload.writer import assemble, encode_payload

from .conftest import make_spec


def _patch(buf: bytes, offset: int, fmt: str, *values: object) -> bytes:
    out = bytearray(buf)
    struct.pack_into(fmt, out, offset, *values)
    return bytes(out)


def _section(raw: bytes, section: Section) -> tuple[int, int]:
    offset, count = struct.unpack_from("<QQ", raw, 80 + 16 * section)
    return int(offset), int(count)


def _replace_string(raw: bytes, old: str, new: str) -> bytes:
    assert len(old) == len(new)
    needle = old.encode() + b"\0"
    assert raw.count(needle) == 1
    return raw.replace(needle, new.encode() + b"\0")


def _two_lib_spec_raw() -> bytes:
    libs = ("x.libs/a.so", "x.libs/b.so")
    files = (
        File("x/_e.so", FileKind.NATIVE_EXT, Blob.raw(b"e", digest=True), closure=libs),
        *(File(p, FileKind.NATIVE_LIB, Blob.raw(p.encode(), digest=True)) for p in libs),
    )
    return encode_payload(make_spec(files=files, modules=(), dists=()))


# --- hostile sizes ----------------------------------------------------------------------------


def test_reader_rejects_blob_size_beyond_limit() -> None:
    raw = encode_payload(make_spec())
    base, _ = _section(raw, Section.BLOBS)
    payload = Payload(raw)
    zstd_blob = next(b for b in payload.blobs() if b.codec.name == "ZSTD")

    for size in (MAX_BLOB_SIZE + 1, 2**64 - 1):
        with pytest.raises(PayloadError, match="size"):
            Payload(_patch(raw, base + zstd_blob.index * BLOB.size + 16, "<Q", size))


def test_spec_rejects_blob_size_beyond_limit() -> None:
    with pytest.raises(PayloadError, match="size"):
        Blob(b"x", MAX_BLOB_SIZE + 1, Blob.zstd(b"a" * 100).codec)


# --- file-level errors surface as PayloadError ------------------------------------------------


@pytest.mark.parametrize("damage", ["empty", "short", "trailer", "header"])
def test_open_reports_corrupt_files_as_payload_error(tmp_path: Path, damage: str) -> None:
    image = bytearray(assemble(b"STUB", encode_payload(make_spec())))
    if damage == "empty":
        image = bytearray()
    elif damage == "short":
        image = image[: TRAILER_SIZE - 1]
    elif damage == "trailer":
        image[-TRAILER_SIZE] ^= 0xFF
    else:
        image[Executable.parse(bytes(image)).payload_offset] ^= 0xFF
    path = tmp_path / "app"
    path.write_bytes(bytes(image))

    with pytest.raises(PayloadError):
        Executable.open(path)
    path.unlink()  # nothing left mapped or open


def test_close_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "app"
    path.write_bytes(assemble(b"STUB", encode_payload(make_spec())))
    exe = Executable.open(path)

    exe.close()
    exe.close()


# --- spec rules enforced on read --------------------------------------------------------------


def test_reader_rejects_path_traversal() -> None:
    raw = encode_payload(
        make_spec(files=(File("aaa/b", FileKind.RESOURCE, Blob.raw(b"x")),), modules=(), dists=())
    )

    with pytest.raises(PayloadError, match="path"):
        Payload(_replace_string(raw, "aaa/b", "../xb"))


def test_reader_rejects_invalid_module_name() -> None:
    raw = encode_payload(make_spec())

    with pytest.raises(PayloadError, match="module name"):
        Payload(_replace_string(raw, "nspkg", "n pkg"))


def test_reader_rejects_non_canonical_dist_name() -> None:
    raw = encode_payload(make_spec())

    with pytest.raises(PayloadError, match="canonical"):
        Payload(_replace_string(raw, "demo", "Demo"))


def test_reader_rejects_misaligned_blob() -> None:
    raw = encode_payload(make_spec())
    base, _ = _section(raw, Section.BLOBS)
    offset = struct.unpack_from("<Q", raw, base)[0]

    with pytest.raises(PayloadError, match="aligned"):
        Payload(_patch(raw, base, "<Q", offset + 1))


def test_reader_rejects_blob_data_inside_tables() -> None:
    raw = encode_payload(make_spec())
    base, _ = _section(raw, Section.BLOBS)
    modules, _ = _section(raw, Section.MODULES)

    with pytest.raises(PayloadError, match="blob range"):
        Payload(_patch(raw, base, "<Q", modules - modules % 16))


def test_reader_rejects_overlapping_sections() -> None:
    raw = encode_payload(make_spec())
    closures, _ = _section(raw, Section.CLOSURES)

    with pytest.raises(PayloadError, match="overlap"):
        Payload(_patch(raw, 80 + 16 * Section.DIGESTS, "<Q", closures))


@pytest.mark.parametrize(
    ("section", "offset", "fmt"),
    [
        (Section.BLOBS, 25, "<B"),
        (Section.BLOBS, 26, "<H"),
        (Section.MODULES, 18, "<H"),
        (Section.MODULES, 36, "<I"),
    ],
)
def test_reader_rejects_nonzero_reserved_fields(section: Section, offset: int, fmt: str) -> None:
    raw = encode_payload(make_spec())
    base, _ = _section(raw, section)
    assert MODULE.size == 40

    with pytest.raises(PayloadError, match="reserved"):
        Payload(_patch(raw, base + offset, fmt, 1))


def test_executable_rejects_nonzero_trailer_flags() -> None:
    image = assemble(b"STUB", encode_payload(make_spec()))

    with pytest.raises(PayloadError, match="flags"):
        Executable.parse(_patch(image, len(image) - TRAILER_SIZE + 12, "<I", 1))


@pytest.mark.parametrize(
    "words",
    [(2, 1, 0), (2, 0, 0), (2, 0, 2), (5, 0, 1)],
    ids=["unsorted", "duplicate", "self", "overrun"],
)
def test_reader_rejects_bad_closures(words: tuple[int, int, int]) -> None:
    raw = _two_lib_spec_raw()  # files: 0 x.libs/a.so, 1 x.libs/b.so, 2 x/_e.so -> [2, 0, 1]
    base, count = _section(raw, Section.CLOSURES)
    assert count == 3

    with pytest.raises(PayloadError, match="closure"):
        Payload(_patch(raw, base, "<III", *words))


# --- writer edge cases ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "closure",
    [("x.libs/a.so", "x.libs/a.so"), ("x/res",), ("x/_e.so",)],
    ids=["dup", "kind", "self"],
)
def test_writer_rejects_bad_closures(closure: tuple[str, ...]) -> None:
    files = (
        File("x/_e.so", FileKind.NATIVE_EXT, Blob.raw(b"e", digest=True), closure=closure),
        File("x.libs/a.so", FileKind.NATIVE_LIB, Blob.raw(b"a", digest=True)),
        File("x/res", FileKind.RESOURCE, Blob.raw(b"r")),
    )

    with pytest.raises(PayloadError, match="closure"):
        encode_payload(make_spec(files=files, modules=(), dists=()))


def test_writer_rejects_closure_on_non_extension() -> None:
    files = (
        File("x/res", FileKind.RESOURCE, Blob.raw(b"r"), closure=("x.libs/a.so",)),
        File("x.libs/a.so", FileKind.NATIVE_LIB, Blob.raw(b"a", digest=True)),
    )

    with pytest.raises(PayloadError, match="closure"):
        encode_payload(make_spec(files=files, modules=(), dists=()))


@pytest.mark.parametrize("bad", ["a\udcffb"])
def test_writer_rejects_unencodable_strings(bad: str) -> None:
    with pytest.raises(PayloadError):
        encode_payload(make_spec(app_name=bad))
    with pytest.raises(PayloadError):
        encode_payload(
            make_spec(files=(File(bad, FileKind.RESOURCE, Blob.raw(b"")),), modules=(), dists=())
        )
    with pytest.raises(PayloadError):
        encode_payload(
            make_spec(
                modules=(Module(bad, "m.py", ModuleKind.MODULE, code=Blob.code(b"c")),),
                files=(),
                dists=(),
            )
        )
    with pytest.raises(PayloadError):
        encode_payload(make_spec(dists=(Dist("d", bad, "1"),), modules=(), files=()))


def _with_startup(raw: bytes, *indices: int) -> bytes:
    """Point the STARTUP section at a fresh word list placed over the (16-aligned) digests."""
    digests, _ = _section(raw, Section.DIGESTS)
    patched = _patch(raw, digests, f"<{len(indices)}I", *indices)
    return _patch(patched, 80 + 16 * Section.STARTUP, "<QQ", digests, len(indices))


def test_reader_rejects_startup_section_naming_non_startup_module() -> None:
    raw = encode_payload(
        make_spec(
            files=(),
            dists=(),
            modules=(
                Module(
                    "_boot",
                    "_boot.py",
                    ModuleKind.MODULE,
                    code=Blob.code(b"b"),
                    flags=ModuleFlag.STARTUP,
                ),
                Module("other", "other.py", ModuleKind.MODULE, code=Blob.code(b"o")),
            ),
        )
    )

    with pytest.raises(PayloadError, match="startup"):
        Payload(_with_startup(raw, 0, 1))
    with pytest.raises(PayloadError, match="startup"):
        Payload(_with_startup(raw))  # STARTUP module missing from the section
    with pytest.raises(PayloadError, match="startup"):
        Payload(_with_startup(raw, 7))


def test_reader_rejects_zstd_frame_without_checksum() -> None:
    data = b"resource data " * 50
    frame = zstd.compress(data, options={zstd.CompressionParameter.checksum_flag: 0})
    unchecked = Blob(frame, len(data), Codec.ZSTD)
    files = (File("r.txt", FileKind.RESOURCE, unchecked),)
    payload = Payload(encode_payload(make_spec(files=files, modules=(), dists=())))
    record = payload.find_file("r.txt")
    assert record is not None

    with pytest.raises(PayloadError, match="checksum"):
        payload.read_blob(record.blob)


def test_closure_may_name_another_extension() -> None:
    files = (
        File("x/_a.so", FileKind.NATIVE_EXT, Blob.raw(b"a", digest=True), closure=("x/_b.so",)),
        File("x/_b.so", FileKind.NATIVE_EXT, Blob.raw(b"b", digest=True)),
    )

    payload = Payload(encode_payload(make_spec(files=files, modules=(), dists=())))

    owner = payload.find_file("x/_a.so")
    assert owner is not None
    assert [payload.files()[i].path for i in payload.closure(owner.closure)] == ["x/_b.so"]


def test_reader_rejects_fs_required_files_without_digest() -> None:
    files = (File("pkg/d.txt", FileKind.RESOURCE, Blob.raw(b"d", digest=True),
                  flags=FileFlag.FS_REQUIRED),)  # fmt: skip
    raw = encode_payload(make_spec(files=files, modules=(), dists=()))
    base, _ = _section(raw, Section.BLOBS)

    with pytest.raises(PayloadError, match="digest"):
        Payload(_patch(raw, base + BLOB.size - 4, "<I", NONE))  # the only blob's digest field


def test_reader_rejects_fs_required_root_on_a_namespace() -> None:
    raw = encode_payload(make_spec())
    record = Payload(raw).find_module("nspkg")
    assert record is not None
    base, _ = _section(raw, Section.MODULES)

    with pytest.raises(PayloadError, match="FS_REQUIRED_ROOT"):
        Payload(_patch(raw, base + record.index * MODULE.size + 17, "<B",
                       int(ModuleFlag.FS_REQUIRED_ROOT)))  # fmt: skip


def test_executable_rejects_trailer_and_header_of_different_versions() -> None:
    image = assemble(b"STUB", encode_payload(make_spec()))

    with pytest.raises(PayloadError, match="version"):
        Executable.parse(_patch(image, len(image) - TRAILER_SIZE + 8, "<I", 1))


@pytest.mark.parametrize("read", ["read_blob", "contains"])
def test_reader_rejects_bytes_after_the_zstd_frame(read: str) -> None:
    """The launcher refuses them (fsb_zstd_frame_ok): inspect must not call the file healthy."""
    data = b"resource data " * 50
    padded = Blob(stored_bytes(Blob.zstd(data).stored) + b"JUNKJUNK", len(data), Codec.ZSTD)
    files = (File("r.txt", FileKind.RESOURCE, padded),)
    payload = Payload(encode_payload(make_spec(files=files, modules=(), dists=())))
    record = payload.find_file("r.txt")
    assert record is not None

    with pytest.raises(PayloadError, match="frame"):
        getattr(payload, read)(record.blob, *([b"data"] if read == "contains" else []))


@pytest.mark.parametrize("read", ["read_blob", "contains"])
def test_reader_rejects_zstd_frame_without_content_size(read: str) -> None:
    """The launcher refuses such a frame: inspect must not call the file healthy."""
    data = b"resource data " * 50
    options: dict[int, int] = {zstd.CompressionParameter.checksum_flag: 1,
                               zstd.CompressionParameter.content_size_flag: 0}  # fmt: skip
    unsized = Blob(zstd.compress(data, options=options), len(data), Codec.ZSTD)
    files = (File("r.txt", FileKind.RESOURCE, unsized),)
    payload = Payload(encode_payload(make_spec(files=files, modules=(), dists=())))
    record = payload.find_file("r.txt")
    assert record is not None

    with pytest.raises(PayloadError, match="content size"):
        getattr(payload, read)(record.blob, *([b"data"] if read == "contains" else []))
