"""Malformed specs are rejected at encode time; malformed bytes are rejected at read time."""

import dataclasses
import struct

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fsbpyr.payload.format import (
    BLOB,
    FILE,
    HEADER,
    HEADER_SIZE,
    MODULE,
    TRAILER,
    TRAILER_SIZE,
    FileFlag,
    FileKind,
    ModuleFlag,
    ModuleKind,
    PayloadError,
    Section,
)
from fsbpyr.payload.reader import Executable, Payload
from fsbpyr.payload.spec import Blob, Dist, File, Module, PayloadSpec
from fsbpyr.payload.writer import assemble, encode_payload

from .conftest import SO, make_spec

# --- encode-time validation ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["", "/abs", "a//b", "a/../b", "./a", "a/.", "a/", "a\0b", "a\\b", ".."]
)
def test_encode_rejects_unsafe_file_paths(path: str) -> None:
    files = (File(path, FileKind.RESOURCE, Blob.raw(b"x")),)

    with pytest.raises(PayloadError, match="path"):
        encode_payload(make_spec(files=files, modules=(), dists=()))


@pytest.mark.parametrize("name", ["", ".a", "a.", "a..b", "a/b", "a\0b", "a b"])
def test_encode_rejects_invalid_module_names(name: str) -> None:
    modules = (Module(name, "x.py", ModuleKind.MODULE, code=Blob.code(b"c")),)

    with pytest.raises(PayloadError, match="module name"):
        encode_payload(make_spec(modules=modules, files=(), dists=()))


def test_encode_rejects_duplicate_keys(spec: PayloadSpec) -> None:
    variants = (
        dataclasses.replace(spec, modules=(*spec.modules, spec.modules[0])),
        dataclasses.replace(spec, files=(*spec.files, spec.files[0])),
        dataclasses.replace(spec, dists=(*spec.dists, spec.dists[0])),
    )
    for variant in variants:
        with pytest.raises(PayloadError, match="duplicate"):
            encode_payload(variant)


@pytest.mark.parametrize(
    ("module", "message"),
    [
        (Module("m", "m.py", ModuleKind.MODULE), "requires code"),
        (Module("m", "m", ModuleKind.PACKAGE), "requires code"),
        (Module("m", "m", ModuleKind.NAMESPACE, code=Blob.code(b"c")), "namespace"),
        (Module("m", SO, ModuleKind.EXTENSION, code=Blob.code(b"c"), file=SO), "extension"),
        (Module("m", "m.so", ModuleKind.EXTENSION, file="pkg/certs/ca.pem"), "NATIVE_EXT"),
        (Module("m", "m.so", ModuleKind.EXTENSION, file="nope.so"), "unknown file"),
        (Module("m", "m.py", ModuleKind.MODULE, code=Blob.code(b"c"), dist="nope"), "unknown dist"),
        (Module("m", "m.py", ModuleKind.MODULE, code=Blob.raw(b"c")), "zstd"),
        (
            Module("m", "m.py", ModuleKind.MODULE, code=Blob.raw(b"c"), flags=ModuleFlag.STARTUP),
            "zstd",
        ),
    ],
)
def test_encode_rejects_inconsistent_modules(module: Module, message: str) -> None:
    spec = make_spec()

    with pytest.raises(PayloadError, match=message):
        encode_payload(dataclasses.replace(spec, modules=(*spec.modules, module)))


def test_encode_rejects_unknown_closure_member() -> None:
    blob = Blob.raw(b"x", digest=True)
    files = (File("a.so", FileKind.NATIVE_EXT, blob, closure=("missing.so",)),)

    with pytest.raises(PayloadError, match="closure"):
        encode_payload(make_spec(files=files, modules=(), dists=()))


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\0b"])
def test_app_name_must_be_one_path_component(name: str) -> None:
    with pytest.raises(PayloadError, match="app name"):  # it names the app's cache directory
        encode_payload(make_spec(app_name=name))


def test_fs_required_files_need_digests() -> None:
    flagged = File("pkg/data.txt", FileKind.RESOURCE, Blob.raw(b"d"), flags=FileFlag.FS_REQUIRED)

    with pytest.raises(PayloadError, match="digest"):
        encode_payload(make_spec(files=(flagged,), modules=(), dists=()))

    digested = dataclasses.replace(flagged, blob=Blob.raw(b"d", digest=True))
    payload = Payload(encode_payload(make_spec(files=(digested,), modules=(), dists=())))
    assert payload.find_file("pkg/data.txt") is not None


def test_fs_required_root_marks_regular_packages_only() -> None:
    namespace = Module("ns", "ns", ModuleKind.NAMESPACE, flags=ModuleFlag.FS_REQUIRED_ROOT)

    with pytest.raises(PayloadError, match="FS_REQUIRED_ROOT"):
        encode_payload(make_spec(modules=(namespace,), files=(), dists=()))


def test_encode_rejects_bad_header_values() -> None:
    with pytest.raises(PayloadError, match="generation_id"):
        encode_payload(make_spec(generation_id=b"short"))
    with pytest.raises(PayloadError, match="bytecode_magic"):
        encode_payload(make_spec(bytecode_magic=b"12345"))
    with pytest.raises(PayloadError, match="mode"):
        encode_payload(
            make_spec(
                files=(File("a", FileKind.RESOURCE, Blob.raw(b""), mode=0o4755),),
                modules=(),
                dists=(),
            )
        )


def test_blob_rejects_inconsistent_fields() -> None:
    with pytest.raises(PayloadError):
        Blob(b"abc", 4, Blob.raw(b"").codec)
    with pytest.raises(PayloadError):
        Blob(b"abc", 3, Blob.raw(b"").codec, sha256=b"short")
    with pytest.raises(PayloadError):
        Blob.zstd(b"x", level=99)


def test_dist_names_must_be_canonical() -> None:
    with pytest.raises(PayloadError, match="canonical"):
        encode_payload(
            make_spec(dists=(Dist("Demo_Pkg", "Demo_Pkg-1.dist-info", "1"),), modules=(), files=())
        )


def test_hyphenated_stdlib_module_names_are_allowed() -> None:
    # sysconfig imports _sysconfigdata_<abiflags>_<platform>_<multiarch> with import_module
    name = "_sysconfigdata__linux_x86_64-linux-gnu"
    modules = (Module(name, f"{name}.py", ModuleKind.MODULE, code=Blob.code(b"c")),)

    assert Payload(encode_payload(make_spec(modules=modules, files=(), dists=()))).find_module(name)


def test_module_names_starting_with_a_digit_are_allowed() -> None:
    # mypyc's shared module, which the extensions it compiled import by name
    name = "81d243bd2c585b0f4821__mypyc"
    modules = (Module(name, f"{name}.py", ModuleKind.MODULE, code=Blob.code(b"c")),)

    assert Payload(encode_payload(make_spec(modules=modules, files=(), dists=()))).find_module(name)


# --- read-time validation -----------------------------------------------------------------


def _patch(buf: bytes, offset: int, fmt: str, *values: object) -> bytes:
    out = bytearray(buf)
    struct.pack_into(fmt, out, offset, *values)
    return bytes(out)


def _section(raw: bytes, section: Section) -> tuple[int, int]:
    fields = HEADER.unpack_from(raw)
    base = len(fields) - 2 * len(Section)
    return fields[base + 2 * section], fields[base + 2 * section + 1]


@pytest.fixture
def raw() -> bytes:
    return encode_payload(make_spec())


@pytest.fixture(params=[False, True], ids=["frames", "blocks"])
def any_raw(request: pytest.FixtureRequest) -> bytes:
    return encode_payload(make_spec(blocks=request.param))


@pytest.mark.parametrize(
    ("offset", "fmt", "value", "message"),
    [
        (0, "<8s", b"NOTMAGIC", "magic"),
        (8, "<I", 7, "version"),
        (12, "<I", 191, "header size"),
    ],
)
def test_reader_rejects_bad_header(
    raw: bytes, offset: int, fmt: str, value: object, message: str
) -> None:
    with pytest.raises(PayloadError, match=message):
        Payload(_patch(raw, offset, fmt, value))


@pytest.mark.parametrize("section", list(Section))
def test_reader_rejects_section_out_of_bounds(raw: bytes, section: Section) -> None:
    header_offset = 80 + 16 * section
    with pytest.raises(PayloadError, match="section"):
        Payload(_patch(raw, header_offset, "<QQ", len(raw) - 4, 1 << 20))
    with pytest.raises(PayloadError, match="section"):
        Payload(_patch(raw, header_offset, "<Q", HEADER_SIZE - 8))


def test_reader_rejects_string_out_of_bounds(raw: bytes) -> None:
    with pytest.raises(PayloadError, match="string"):
        Payload(_patch(raw, 56, "<II", 0, 1 << 30))


def _app_name_offset(raw: bytes) -> int:
    """Payload offset of the app_name string ("demo"); Str offsets are strtab-relative."""
    strtab, _ = _section(raw, Section.STRTAB)
    return strtab + int(struct.unpack_from("<II", raw, 56)[0])


def test_reader_rejects_missing_nul_terminator(raw: bytes) -> None:
    with pytest.raises(PayloadError, match="string"):
        Payload(_patch(raw, _app_name_offset(raw) + 4, "<B", ord("x")))


def test_reader_rejects_invalid_utf8(raw: bytes) -> None:
    with pytest.raises(PayloadError, match="UTF-8"):
        Payload(_patch(raw, _app_name_offset(raw), "<B", 0xFF))


def test_reader_rejects_unsorted_table(raw: bytes) -> None:
    base, count = _section(raw, Section.MODULES)
    first = raw[base : base + MODULE.size]
    last = raw[base + (count - 1) * MODULE.size : base + count * MODULE.size]
    swapped = bytearray(raw)
    swapped[base : base + MODULE.size] = last
    swapped[base + (count - 1) * MODULE.size : base + count * MODULE.size] = first

    with pytest.raises(PayloadError, match="sorted"):
        Payload(bytes(swapped))


@pytest.mark.parametrize(
    ("section", "record_offset", "fmt", "value", "message"),
    [
        (Section.MODULES, 16, "<B", 9, "kind"),
        (Section.MODULES, 20, "<I", 999, "blob index"),
        (Section.MODULES, 32, "<I", 999, "dist index"),
        (Section.FILES, 8, "<B", 0, "kind"),
        (Section.FILES, 10, "<H", 0o4000, "mode"),
        (Section.FILES, 12, "<I", 999, "blob index"),
        (Section.FILES, 20, "<I", 999, "closure"),
        (Section.BLOBS, 24, "<B", 7, "codec"),
        (Section.BLOBS, 0, "<Q", 1 << 40, "blob range"),
        (Section.BLOBS, 28, "<I", 999, "digest index"),
    ],
)
def test_reader_rejects_bad_record_fields(
    raw: bytes, section: Section, record_offset: int, fmt: str, value: int, message: str
) -> None:
    base, _ = _section(raw, section)

    with pytest.raises(PayloadError, match=message):
        Payload(_patch(raw, base + record_offset, fmt, value))


def test_reader_rejects_size_mismatch_on_read(raw: bytes) -> None:
    payload = Payload(raw)
    cli = payload.find_module("pkg.cli")
    assert cli is not None
    base, _ = _section(raw, Section.BLOBS)
    patched = _patch(raw, base + cli.code * BLOB.size + 16, "<Q", payload.blob(cli.code).size - 1)

    with pytest.raises(PayloadError, match="size"):
        Payload(patched).read_blob(cli.code)


def test_reader_rejects_corrupted_zstd_frame(raw: bytes) -> None:
    payload = Payload(raw)
    cli = payload.find_module("pkg.cli")
    assert cli is not None
    blob = payload.blob(cli.code)
    corrupted = bytearray(raw)
    corrupted[blob.offset + blob.stored_size - 1] ^= 0xFF  # inside the content checksum

    with pytest.raises(PayloadError, match="zstd"):
        Payload(bytes(corrupted)).read_blob(cli.code)


def test_reader_rejects_invalid_closure_member(raw: bytes) -> None:
    base, count = _section(raw, Section.CLOSURES)
    assert count == 2
    with pytest.raises(PayloadError, match="closure"):
        Payload(_patch(raw, base + 4, "<I", 999))
    with pytest.raises(PayloadError, match="closure"):
        Payload(_patch(raw, base, "<I", 50))


def test_lookup_index_errors_raise_payload_error(raw: bytes) -> None:
    payload = Payload(raw)
    for call in (payload.blob, payload.read_blob, payload.closure, payload.digest):
        with pytest.raises(PayloadError):
            call(10_000)


# --- executable-level validation -------------------------------------------------------------


@pytest.fixture
def image() -> bytes:
    return assemble(b"STUB" * 300, encode_payload(make_spec()))


@pytest.mark.parametrize(
    ("offset", "fmt", "value", "message"),
    [
        (0, "<8s", b"XXXXXXXX", "trailer magic"),
        (8, "<I", 7, "version"),
        (16, "<Q", 4097, "aligned"),
        (24, "<Q", 5, "size"),
    ],
)
def test_executable_rejects_bad_trailer(
    image: bytes, offset: int, fmt: str, value: object, message: str
) -> None:
    trailer_at = len(image) - TRAILER_SIZE
    with pytest.raises(PayloadError, match=message):
        Executable.parse(_patch(image, trailer_at + offset, fmt, value))


def test_executable_verify_detects_tampering(image: bytes) -> None:
    exe = Executable.parse(image)
    boot = exe.payload.find_module("_boot")
    assert boot is not None
    tampered = bytearray(image)
    tampered[exe.payload_offset + exe.payload.blob(boot.code).offset + 8] ^= 1  # frame body

    with pytest.raises(PayloadError, match="digest"):
        Executable.parse(bytes(tampered)).verify()


def test_executable_rejects_every_truncation(image: bytes) -> None:
    for size in [*range(TRAILER_SIZE + 1), *range(len(image) - 300, len(image))]:
        with pytest.raises(PayloadError):
            Executable.parse(image[:size])


def test_trailer_struct_is_64_bytes() -> None:
    assert TRAILER.size == TRAILER_SIZE == 64
    assert (BLOB.size, MODULE.size, FILE.size) == (32, 40, 24)


@settings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    flips=st.lists(st.tuples(st.integers(min_value=0), st.integers(1, 255)), min_size=1, max_size=4)
)
def test_random_corruption_only_raises_payload_error(
    any_raw: bytes, flips: list[tuple[int, int]]
) -> None:
    corrupted = bytearray(any_raw)
    for position, mask in flips:
        corrupted[position % len(corrupted)] ^= mask
    try:
        payload = Payload(bytes(corrupted))
        for index in range(len(payload.blobs())):
            payload.read_blob(index)
        for name in ("pkg", "pkg.cli", "zzz"):
            payload.find_module(name)
        payload.list_dir("pkg")
    except PayloadError:
        pass


@settings(max_examples=500, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    writes=st.lists(
        st.tuples(st.integers(min_value=0), st.integers(min_value=0, max_value=2**64 - 1)),
        min_size=1,
        max_size=3,
    )
)
def test_random_u64_overwrites_only_raise_payload_error(
    any_raw: bytes, writes: list[tuple[int, int]]
) -> None:
    corrupted = bytearray(any_raw)
    tables_end = len(any_raw) // 2  # header and tables live in the first part of this payload
    for position, value in writes:
        offset = (position % (tables_end - 8)) & ~7
        struct.pack_into("<Q", corrupted, offset, value)
    try:
        payload = Payload(bytes(corrupted))
        for index in range(len(payload.blobs())):
            payload.read_blob(index)
        for file in payload.files():
            if file.closure != 0xFFFFFFFF:
                payload.closure(file.closure)
        payload.list_dir("")
    except PayloadError:
        pass
