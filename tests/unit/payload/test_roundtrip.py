"""Writer -> reader round trips, lookups, determinism and layout."""

import dataclasses
import os
import stat
from pathlib import Path

import pytest

from fsbpyr.payload.format import (
    HEADER,
    NONE,
    PAGE_SIZE,
    TRAILER_SIZE,
    Codec,
    FileFlag,
    FileKind,
    ModuleFlag,
    ModuleKind,
)
from fsbpyr.payload.reader import Executable, Payload
from fsbpyr.payload.spec import Blob, PayloadSpec
from fsbpyr.payload.writer import assemble, encode_payload, write_executable

from .conftest import GENERATION, MAGIC, PY_HEXVERSION, SO, make_spec


def test_header_fields_round_trip(spec: PayloadSpec) -> None:
    payload = Payload(encode_payload(spec))

    assert payload.python_hexversion == PY_HEXVERSION
    assert payload.bytecode_magic == MAGIC
    assert payload.generation_id == GENERATION
    assert payload.app_name == "demo"
    assert payload.entrypoint == "pkg.cli:main"
    assert payload.build_info == '{"profile": "balanced"}'


def test_modules_round_trip_with_blob_contents(spec: PayloadSpec) -> None:
    payload = Payload(encode_payload(spec))

    cli = payload.find_module("pkg.cli")
    assert cli is not None
    assert (cli.path, cli.kind, cli.flags) == ("pkg/cli.py", ModuleKind.MODULE, ModuleFlag(0))
    assert payload.read_blob(cli.code) == b"code-cli " * 100
    assert payload.read_blob(cli.source) == b"def main(): pass\n" * 50
    assert payload.dists()[cli.dist].name == "demo"

    ext = payload.find_module("pkg._speed")
    assert ext is not None and ext.kind is ModuleKind.EXTENSION and ext.code == NONE
    assert payload.files()[ext.file].path == SO

    ns = payload.find_module("nspkg")
    assert ns is not None and ns.kind is ModuleKind.NAMESPACE
    assert (ns.code, ns.source, ns.file, ns.dist) == (NONE, NONE, NONE, NONE)


def test_files_round_trip_with_closure_and_digest(spec: PayloadSpec) -> None:
    payload = Payload(encode_payload(spec))

    ext = payload.find_file(SO)
    assert ext is not None
    assert (ext.kind, ext.mode) == (FileKind.NATIVE_EXT, 0o755)
    closure = payload.closure(ext.closure)
    assert [payload.files()[i].path for i in closure] == ["demo.libs/libfoo.so.1"]
    blob = payload.blob(ext.blob)
    assert blob.codec is Codec.ZSTD
    assert payload.digest(blob.digest) == Blob.zstd(b"\x7fELF ext" * 100, digest=True).sha256

    pem = payload.find_file("pkg/certs/ca.pem")
    assert pem is not None and pem.flags == FileFlag.FS_REQUIRED and pem.dist == NONE
    assert payload.read_blob(pem.blob) == b"PEM"


def test_dists_round_trip(spec: PayloadSpec) -> None:
    payload = Payload(encode_payload(spec))

    dist = payload.find_dist("demo")
    assert dist is not None
    assert (dist.dist_info, dist.version) == ("demo-1.0.dist-info", "1.0")


@pytest.mark.parametrize("name", ["", "pkg.missing", "pk", "pkg.cli.x", "zzz"])
def test_missing_lookups_return_none(spec: PayloadSpec, name: str) -> None:
    payload = Payload(encode_payload(spec))

    assert payload.find_module(name) is None
    assert payload.find_file(name) is None
    assert payload.find_dist(name) is None


def test_tables_are_sorted_by_utf8_bytes(spec: PayloadSpec) -> None:
    payload = Payload(encode_payload(spec))

    for keys in (
        [m.name for m in payload.modules()],
        [f.path for f in payload.files()],
        [d.name for d in payload.dists()],
    ):
        encoded = [k.encode() for k in keys]
        assert encoded == sorted(encoded)


def test_encoding_is_deterministic_under_input_permutation(spec: PayloadSpec) -> None:
    shuffled = dataclasses.replace(
        spec, modules=tuple(reversed(spec.modules)), files=tuple(reversed(spec.files))
    )

    assert encode_payload(spec) == encode_payload(shuffled) == encode_payload(make_spec())


def test_identical_blobs_are_stored_once(spec: PayloadSpec) -> None:
    payload = Payload(encode_payload(spec))

    cli = payload.find_module("pkg.cli")
    src = payload.find_file("pkg/cli.py")
    assert cli is not None and src is not None
    assert cli.source == src.blob


def test_blob_data_is_aligned_and_ordered_by_class(spec: PayloadSpec) -> None:
    payload = Payload(encode_payload(spec))
    blobs = payload.blobs()

    assert all(b.offset % 16 == 0 for b in blobs)
    assert [b.offset for b in blobs] == sorted(b.offset for b in blobs)
    boot = payload.find_module("_boot")
    assert boot is not None and boot.code == 0  # STARTUP code comes first
    natives = {
        f.blob for f in payload.files() if f.kind in (FileKind.NATIVE_EXT, FileKind.NATIVE_LIB)
    }
    assert natives == set(range(len(blobs) - len(natives), len(blobs)))  # natives come last


def test_startup_code_is_uncompressed_and_directly_addressable(spec: PayloadSpec) -> None:
    raw = encode_payload(spec)
    payload = Payload(raw)
    boot = payload.find_module("_boot")
    assert boot is not None

    blob = payload.blob(boot.code)
    assert blob.codec is Codec.NONE
    assert raw[blob.offset : blob.offset + blob.size] == b"boot"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("", ("demo-1.0.dist-info", "demo.libs", "pkg")),
        ("pkg", ("_speed.cpython-314-x86_64-linux-gnu.so", "certs", "cli.py", "data")),
        ("pkg/data", ("table.json",)),
        ("pkg/data/table.json", ()),
        ("missing", ()),
        ("pk", ()),
    ],
)
def test_list_dir(spec: PayloadSpec, path: str, expected: tuple[str, ...]) -> None:
    assert Payload(encode_payload(spec)).list_dir(path) == expected


def test_zstd_falls_back_to_raw_when_not_smaller() -> None:
    blob = Blob.zstd(os.urandom(64))

    assert blob.codec is Codec.NONE
    assert blob.stored == blob.stored[: blob.size]


def test_header_is_followed_by_tables() -> None:
    raw = encode_payload(make_spec())

    assert HEADER.size == 192
    assert raw[:8] == b"FSBPYR\x00H"


def test_assemble_pads_stub_and_appends_trailer() -> None:
    stub = b"\x7fELF" + b"x" * 5000
    payload = encode_payload(make_spec())

    image = assemble(stub, payload)

    exe = Executable.parse(image)
    assert exe.payload_offset == 2 * PAGE_SIZE
    assert exe.stub_prefix().startswith(stub)
    assert len(image) == exe.payload_offset + len(payload) + TRAILER_SIZE
    assert exe.payload.app_name == "demo"
    exe.verify()


def test_stub_prefix_can_be_reused_for_a_new_payload() -> None:
    first = Executable.parse(assemble(b"STUB" * 100, encode_payload(make_spec())))
    second_payload = encode_payload(make_spec(app_name="other"))

    second = Executable.parse(assemble(first.stub_prefix(), second_payload))

    assert second.stub_prefix() == first.stub_prefix()
    assert second.payload.app_name == "other"


def test_write_and_open_executable(tmp_path: Path) -> None:
    out = tmp_path / "dist" / "demo"
    out.parent.mkdir()
    stub = b"\x7fELF" + bytes(100)

    write_executable(out, stub, encode_payload(make_spec()))

    assert stat.S_IMODE(out.stat().st_mode) == 0o755
    assert sorted(p.name for p in out.parent.iterdir()) == ["demo"]
    with Executable.open(out) as exe:
        assert exe.payload.find_module("pkg.cli") is not None
        assert exe.stub_prefix()[: len(stub)] == stub
        exe.verify()


def test_write_executable_replaces_existing_file(tmp_path: Path) -> None:
    out = tmp_path / "demo"
    out.write_bytes(b"old")

    write_executable(out, b"STUB", encode_payload(make_spec(app_name="new")))

    with Executable.open(out) as exe:
        assert exe.payload.app_name == "new"
