"""fsbpyr inspect: what an executable was built from, and with which fsbpyr."""

import base64
import dataclasses
import hashlib
import json
import os
import struct
from pathlib import Path

import pytest

import fsbpyr
from fsbpyr.assemble import App, build_executable
from fsbpyr.cli import main
from fsbpyr.payload.convert import spec_from_payload
from fsbpyr.payload.format import (
    HEADER_SIZE,
    TRAILER,
    TRAILER_MAGIC,
    TRAILER_SIZE,
    FileKind,
    ModuleKind,
    PayloadError,
)
from fsbpyr.payload.reader import Executable, Payload
from fsbpyr.payload.spec import Blob, Dist, File, Module
from fsbpyr.payload.writer import write_executable
from fsbpyr.sizes import measure

from ..support.elf import make_elf
from .boot.conftest import FAKE_RUNTIME

SETTINGS = {"entry": "aiagent.cli:main", "fs_packages": ["certifi"],
            "allow_system_libs": ["libz.so.1"], "extras": ["fast"], "include_source": False}
COMMIT = "4f9771de" + "0" * 32
DISTS = (Dist("aiagent", "aiagent-0.8.1.dist-info", "0.8.1"),
         Dist("click", "click-8.2.1.dist-info", "8.2.1"),
         Dist("pyyaml", "pyyaml-6.0.2.dist-info", "6.0.2"))  # fmt: skip
EXT = "yaml/_yaml.cpython-314-x86_64-linux-gnu.so"
LIB = "pyyaml.libs/libyaml-0.so.2"
PLAIN = "yaml/libhelper.so"  # ends in .so, so it is importable by name, but has no PyInit_


def _module(name: str, dist: str, *, package: bool = False) -> Module:
    path = name.replace(".", "/") + ("/__init__.py" if package else ".py")
    kind = ModuleKind.PACKAGE if package else ModuleKind.MODULE
    return Module(name, path, kind, code=Blob.code(b"c"), dist=dist)


MODULES = (_module("aiagent", "aiagent", package=True), _module("aiagent.cli", "aiagent"),
           _module("click", "click", package=True), _module("yaml", "pyyaml", package=True),
           Module("yaml._yaml", EXT, ModuleKind.EXTENSION, file=EXT, dist="pyyaml"),
           Module("yaml.libhelper", PLAIN, ModuleKind.EXTENSION, file=PLAIN,
                  dist="pyyaml"))  # fmt: skip
FILES = (File(EXT, FileKind.NATIVE_EXT, Blob.zstd(b"\x7fELF\0PyInit__yaml\0" * 64, digest=True),
              mode=0o755, dist="pyyaml"),
         File(PLAIN, FileKind.NATIVE_EXT, Blob.zstd(b"\x7fELF-plain", digest=True), mode=0o755,
              dist="pyyaml"),
         File(LIB, FileKind.NATIVE_LIB, Blob.zstd(b"\x7fELF-lib", digest=True), mode=0o755,
              dist="pyyaml"))  # fmt: skip


def _executable(tmp_path: Path, *build_info: tuple[str, object], dists: int = 3) -> Path:
    out = tmp_path / "aiagent"
    app = App("aiagent", "aiagent.cli:main", dists=DISTS[:dists], build_info=build_info)
    if dists == len(DISTS):
        app = dataclasses.replace(app, modules=MODULES, files=FILES)
    build_executable(FAKE_RUNTIME, app, out)
    return out


def _with_build_info(executable: Path, text: str) -> Path:
    with Executable.open(executable) as original:
        spec, stub = spec_from_payload(original.payload), original.stub_prefix()
    out = executable.with_name("tampered")
    write_executable(out, stub, dataclasses.replace(spec, build_info=text))
    return out


def _inspect(path: Path, capsys: pytest.CaptureFixture[str]) -> list[str]:
    assert main(["inspect", str(path)]) == 0
    return capsys.readouterr().out.splitlines()


def test_inspect_shows_the_app_and_the_fsbpyr_it_was_built_with(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = _executable(
        tmp_path,
        ("project", {"name": "aiagent", "version": "0.8.1"}),
        ("git", {"commit": COMMIT, "tag": "v0.8.1"}),
        ("fsbpyr_version", "0.1.1-6d7ca726"),
        ("glibc", "2.28"),
        (
            "system",
            {
                "libraries": {
                    "libc.so.6": {"needed_by": ["(stub)"], "versions": {"GLIBC": "2.28"}},
                    "libstdc++.so.6": {
                        "needed_by": [EXT],
                        "versions": {"CXXABI": "1.3.9", "GLIBCXX": "3.4.22"},
                    },
                }
            },
        ),
        ("uv", "0.13.1"),
        ("zstd_level", 9),
        ("native_compression", "xz -6"),
    )

    lines = _inspect(executable, capsys)

    size = executable.stat().st_size / 1e6
    assert lines[:6] == [
        "app        aiagent 0.8.1 (0.8.1-4f9771de)",
        "built with fsbpyr 0.1.1-6d7ca726",
        "python     3.14.8, glibc >= 2.28",
        "uv         0.13.1",
        f"size       {size:.1f} MB, 3 distributions",
        "compressed zstd 9, native files xz -6",
    ]


def test_inspect_lists_the_entry_point_and_what_the_target_must_provide(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    system = {"libraries": {
        "libc.so.6": {"needed_by": ["(stub)"], "versions": {"GLIBC": "2.28"}},
        "libstdc++.so.6": {"needed_by": [EXT], "versions": {"CXXABI": "1.3.9",
                                                             "GLIBCXX": "3.4.22"}},
    }}  # fmt: skip
    executable = _executable(tmp_path, ("glibc", "2.28"), ("system", system))

    lines = _inspect(executable, capsys)

    assert "entry      aiagent.cli:main" in lines
    assert "needs      glibc >= 2.28; libstdc++.so.6 (CXXABI_1.3.9, GLIBCXX_3.4.22)" in lines
    assert "system libraries: 2" in lines


def test_builds_before_the_system_report_show_no_libraries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = _inspect(_executable(tmp_path), capsys)

    assert "system libraries: 0" in lines and not any(x.startswith("needs") for x in lines)


@pytest.mark.parametrize("system", [None, "x", {"libraries": []}, {"libraries": {"a": 1}},
                                    {"libraries": {"a": {"versions": 1}}}])  # fmt: skip
def test_unexpected_system_report_shapes_are_tolerated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], system: object
) -> None:
    text = json.dumps({"glibc": "2.28", "system": system})

    lines = _inspect(_with_build_info(_executable(tmp_path), text), capsys)

    assert lines[0] == "app        aiagent"


@pytest.mark.parametrize(
    ("dists", "build_info", "shown"),
    [
        (3, (("zstd_level", 9), ("native_compression", "zstd")), "zstd 9, native files zstd 9"),
        (3, (("zstd_level", 3),), "zstd 3, native files zstd 3"),  # 0.4.0 to 0.7: as the rest
        (1, (("zstd_level", 9), ("native_compression", "xz -6")), "zstd 9"),  # no native files
        (3, (), "unknown"),  # before 0.4.0
        (1, (("zstd_level", "9\x1b[2J"),), "'zstd 9\\x1b[2J'"),  # no terminal escapes
    ],
)
def test_inspect_shows_how_the_content_is_compressed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    dists: int,
    build_info: tuple[tuple[str, object], ...],
    shown: str,
) -> None:
    lines = _inspect(_executable(tmp_path, *build_info, dists=dists), capsys)

    assert lines[5] == f"compressed {shown}"


def test_builds_by_an_fsbpyr_without_git_show_its_release(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A wheel install of fsbpyr cannot see its commit: the version number still shows."""
    executable = _executable(tmp_path, ("fsbpyr_version", "untagged-unknown"), dists=1)

    lines = _inspect(executable, capsys)

    assert lines[1] == f"built with fsbpyr {fsbpyr.__version__}"
    assert lines[4].endswith(" 1 distribution")


def test_inspect_shows_what_older_builds_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Builds before 0.2.0 recorded neither the project nor fsbpyr's commit."""
    executable = _executable(
        tmp_path, ("fsbpyr", "0.1.0"), ("git", {"commit": COMMIT, "tag": None})
    )

    lines = _inspect(executable, capsys)

    assert lines[:4] == [
        "app        aiagent (untagged-4f9771de)",
        "built with fsbpyr 0.1.0",
        "python     3.14.8",
        "uv         unknown",
    ]


def test_libraries_and_modules_are_listed_in_columns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    executable = _executable(tmp_path)

    lines = _inspect(executable, capsys)

    text = "\n".join(lines)
    assert f"shared libraries: 2\n  {LIB}  {PLAIN}\n" in text
    assert f"extension modules: 1\n  {EXT}\n" in text
    modules = lines[lines.index("modules: 3 top-level names in 3 distributions") + 1]
    assert modules.split() == ["aiagent", "click", "pyyaml:", "yaml"]  # one row on a wide screen
    runtime = lines[lines.index("standard library and fsbpyr: 2 top-level names") + 1]
    assert runtime.split() == ["_fsbpyr_boot", "_fsbpyr_rt"]  # fsbpyr's boot modules (no stdlib)


def test_columns_follow_the_terminal_width(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "12")
    executable = _executable(tmp_path)

    lines = _inspect(executable, capsys)

    listed = lines[lines.index("modules: 3 top-level names in 3 distributions") + 1 :]
    assert listed[:3] == ["  aiagent", "  click", "  pyyaml: yaml"]  # one per row when narrow


def test_all_modules_lists_every_module_per_distribution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    executable = _executable(tmp_path)

    assert main(["inspect", "--all-modules", str(executable)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[lines.index("aiagent 0.8.1: 2 modules") + 1].split() == ["aiagent", "aiagent.cli"]
    assert lines[lines.index("pyyaml 6.0.2: 3 modules") + 1].split() == [
        "yaml", "yaml._yaml", "yaml.libhelper"]  # fmt: skip


def test_json_describes_everything(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    executable = _executable(
        tmp_path, ("project", {"name": "aiagent", "version": "0.8.1"}),
        ("git", {"commit": COMMIT, "tag": "v0.8.1"}), ("fsbpyr_version", "0.3.0-1a2b3c4d"),
        ("uv", "0.13.1"), ("glibc", "2.28"),
        ("settings", SETTINGS),
        ("system", {"libraries": {"libstdc++.so.6": {"needed_by": [EXT],
                                                      "versions": {"GLIBCXX": "3.4.22"}}}}),
    )  # fmt: skip

    assert main(["inspect", "--json", "--verify", str(executable)]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["app"] == {"name": "aiagent", "version": "0.8.1", "revision": "0.8.1-4f9771de"}
    assert report["built_with"] == "0.3.0-1a2b3c4d"
    assert report["python"] == "3.14.8" and report["verified"] is True
    assert report["uv"] == "0.13.1"
    assert report["entry"] == "aiagent.cli:main"
    assert report["system_libraries"] == report["build_info"]["system"]["libraries"]
    assert list(report["system_libraries"]) == ["libstdc++.so.6"]
    assert report["size"] == executable.stat().st_size
    assert report["distributions"][2] == {"name": "pyyaml", "version": "6.0.2"}
    assert report["shared_libraries"] == [LIB, PLAIN] and report["extension_modules"] == [EXT]
    assert report["modules"]["pyyaml"] == ["yaml"] and report["all_modules"] is False
    assert report["runtime_modules"] == ["_fsbpyr_boot", "_fsbpyr_rt"]
    assert report["build_info"]["git"]["tag"] == "v0.8.1"
    assert report["settings"] == SETTINGS
    sizes = report["sizes"]
    assert sizes["total"] == executable.stat().st_size - TRAILER_SIZE
    assert sizes["payload_header"] == HEADER_SIZE and sizes["stub_padding"] > 0
    assert sizes["unreferenced"] == 0 and sizes["runtime"] > 0
    assert sizes["distributions"]["pyyaml"]["natives"] > 0
    plain = ("stub", "stub_padding", "payload_header", "tables", "blob_padding", "runtime",
             "unreferenced")  # fmt: skip
    owned = sum(sum(parts.values()) for parts in sizes["distributions"].values())
    assert sum(sizes[key] for key in plain) + owned == sizes["total"]


def _lines(capsys: pytest.CaptureFixture[str], executable: Path) -> list[str]:
    assert main(["inspect", str(executable)]) == 0
    return capsys.readouterr().out.splitlines()


def test_verify_checks_the_file_digest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    executable = _executable(tmp_path)
    digest = executable.read_bytes()[-32:].hex()
    assert main(["inspect", str(executable)]) == 0
    assert f"digest     {digest}" in capsys.readouterr().out.splitlines()
    assert main(["inspect", "--verify", str(executable)]) == 0
    assert f"digest     {digest} verified" in capsys.readouterr().out.splitlines()
    assert main(["inspect", "--json", str(executable)]) == 0
    plain = json.loads(capsys.readouterr().out)
    assert plain["digest"] == digest and plain["sha256"] is None
    file_sha256 = hashlib.sha256(executable.read_bytes()).hexdigest()
    assert not any(line.startswith("sha256") for line in _lines(capsys, executable))
    assert main(["inspect", "--verify", str(executable)]) == 0
    assert f"sha256     {file_sha256}" in capsys.readouterr().out.splitlines()
    assert main(["inspect", "--json", "--verify", str(executable)]) == 0
    assert json.loads(capsys.readouterr().out)["sha256"] == file_sha256
    with Executable.open(executable) as reader:
        offset = reader.payload_offset - 1  # the stub's padding: outside the payload
    data = bytearray(executable.read_bytes())
    data[offset] ^= 0xFF
    executable.write_bytes(bytes(data))

    assert main(["inspect", "--verify", str(executable)]) == 1
    error = capsys.readouterr().err
    assert "is damaged or was changed after its build" in error and "digest mismatch" in error


def test_older_executables_show_a_digest_of_their_payload_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Format version 2 (fsbpyr 0.5.0): the trailer's digest covers the payload."""
    current = _executable(tmp_path)
    with Executable.open(current) as reader:
        offset, size = reader.payload_offset, reader.payload_size
    data = current.read_bytes()
    stub, payload = data[:offset], bytearray(data[offset : offset + size])
    struct.pack_into("<I", payload, 8, 2)
    digest = hashlib.sha256(payload).digest()
    older = tmp_path / "older"
    older.write_bytes(stub + payload + TRAILER.pack(TRAILER_MAGIC, 2, 0, offset, len(payload),
                                                    digest))  # fmt: skip

    assert main(["inspect", "--verify", str(older)]) == 0
    assert f"digest     {digest.hex()} (payload only) verified" in capsys.readouterr().out
    assert main(["inspect", "--json", str(older)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert (report["digest"], report["digest_covers"]) == (digest.hex(), "payload")


def test_distributions_named_like_the_runtime_or_without_modules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    dists = (*DISTS, Dist("data-only", "data_only-1.dist-info", "1"),
             Dist("runtime", "runtime-1.0.dist-info", "1.0"))  # fmt: skip
    app = App("aiagent", "aiagent.cli:main", modules=(*MODULES, _module("runtime", "runtime")),
              files=FILES, dists=dists)  # fmt: skip
    build_executable(FAKE_RUNTIME, app, tmp_path / "app")

    lines = _inspect(tmp_path / "app", capsys)
    assert main(["inspect", "--json", "--all-modules", str(tmp_path / "app")]) == 0
    report = json.loads(capsys.readouterr().out)

    modules = lines[lines.index("modules: 4 top-level names in 5 distributions") + 1]
    assert modules.split() == ["aiagent", "click", "data-only", "pyyaml:", "yaml", "runtime"]
    assert report["modules"]["runtime"] == ["runtime"] and report["modules"]["data-only"] == []
    assert report["runtime_modules"][:2] == ["_fsbpyr_boot", "_fsbpyr_rt"]
    assert report["all_modules"] is True


def test_damaged_or_oversized_libraries_fall_back_to_the_module_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def damaged(*args: object, **kwargs: object) -> bool:
        raise PayloadError("zstd frame invalid")

    monkeypatch.setattr(Payload, "contains", damaged)
    executable = _executable(tmp_path)

    assert main(["inspect", "--json", str(executable)]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["extension_modules"] == sorted([PLAIN, EXT])  # as recorded: importable


def test_blob_search_streams_and_finds_needles_across_chunks(tmp_path: Path) -> None:
    executable = _executable(tmp_path)

    with Executable.open(executable) as reader:
        payload = reader.payload
        blob = next(f.blob for f in payload.files() if f.path == EXT)
        content = payload.read_blob(blob)
        needle = content[3:14]
        for chunk in (1, 2, 5, 1 << 20):
            assert payload.contains(blob, needle, chunk=chunk)
            assert not payload.contains(blob, b"PyInit_absent\0", chunk=chunk)


def test_blob_search_in_stored_and_damaged_blobs(tmp_path: Path) -> None:
    raw = File("p/libraw.so", FileKind.NATIVE_LIB, Blob.raw(b"\x7fELF..PyInit_raw\0", digest=True),
               mode=0o755)  # fmt: skip
    app = App("x", "x:main", modules=MODULES, files=(raw, *FILES), dists=DISTS)
    build_executable(FAKE_RUNTIME, app, tmp_path / "x")
    with Executable.open(tmp_path / "x") as reader:
        payload = reader.payload
        stored = next(f.blob for f in payload.files() if f.path == "p/libraw.so")
        zstd_blob = next(f.blob for f in payload.files() if f.path == EXT)  # compressible
        assert payload.contains(stored, b"PyInit_raw\0", chunk=3)  # uncompressed, windowed
        assert not payload.contains(stored, b"PyInit_other\0", chunk=3)
        record = payload.blob(zstd_blob)
        offset = reader.payload_offset + record.offset + record.stored_size - 2
    data = bytearray((tmp_path / "x").read_bytes())
    data[offset] ^= 0xFF  # inside the zstd frame: its checksum no longer matches
    (tmp_path / "x").write_bytes(bytes(data))

    with Executable.open(tmp_path / "x") as reader, pytest.raises(PayloadError):
        reader.payload.contains(zstd_blob, b"\0never\0")


def test_text_from_the_file_cannot_drive_the_terminal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = _executable(
        tmp_path, ("project", {"name": "evil\x1b]0;title\x07", "version": "1\r2"}),
        ("fsbpyr_version", "\x1b[2J"),
    )  # fmt: skip

    lines = _inspect(executable, capsys)

    assert lines[0] == "app        'evil\\x1b]0;title\\x07' '1\\r2'"
    assert lines[1] == "built with fsbpyr '\\x1b[2J'"


def test_unexpected_build_information_shapes_are_tolerated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = _with_build_info(
        _executable(tmp_path), '{"git": "x", "project": ["y"], "glibc": 1}'
    )

    lines = _inspect(executable, capsys)

    assert lines[0] == "app        aiagent"


@pytest.mark.parametrize("text", ["[]", "null", "not json", "1" + "0" * 5000, "[" * 100_000])
def test_corrupt_build_information_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    executable = _with_build_info(_executable(tmp_path), text)

    assert main(["inspect", str(executable)]) == 1
    assert "has corrupt build information" in capsys.readouterr().err


def test_inspect_rejects_files_fsbpyr_did_not_build(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = tmp_path / "other"
    other.write_bytes(b"\x7fELF" + bytes(200))
    os.mkfifo(tmp_path / "fifo")  # opening it would wait for a writer forever

    assert main(["inspect", str(other)]) == 1
    assert main(["inspect", str(tmp_path / "missing")]) == 1
    assert main(["inspect", str(tmp_path / "fifo")]) == 1
    assert main(["inspect", str(tmp_path)]) == 1

    errors = capsys.readouterr().err.splitlines()
    assert errors[0].startswith(f"fsbpyr: error: {other} is not an executable fsbpyr built (")
    assert errors[1].startswith(f"fsbpyr: error: cannot read {tmp_path / 'missing'}")
    assert errors[2] == f"fsbpyr: error: {tmp_path / 'fifo'} is not a regular file"
    assert errors[3] == f"fsbpyr: error: {tmp_path} is not a regular file"


def test_inspect_shows_the_toolchain_that_linked_the_stub(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    comment = (b"\0GCC: (Debian 8.3.0-6) 8.3.0\0Linker: Debian LLD 22.1.8\0"
               b"Debian clang version 22.1.8 (1~deb13u4)\0Linker: Debian LLD 22.1.8\0")  # fmt: skip
    runtime = dataclasses.replace(FAKE_RUNTIME, stub=make_elf(comment=comment))
    out = tmp_path / "aiagent"
    build_executable(runtime, App("aiagent", "aiagent.cli:main", dists=DISTS[:1]), out)

    lines = _inspect(out, capsys)

    start = lines.index("toolchain  GCC: (Debian 8.3.0-6) 8.3.0")
    assert lines[start : start + 3] == [
        "toolchain  GCC: (Debian 8.3.0-6) 8.3.0",
        "           Linker: Debian LLD 22.1.8",  # a repeated line is shown once
        "           Debian clang version 22.1.8 (1~deb13u4)",
    ]
    assert main(["inspect", "--json", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["toolchain"] == [
        "GCC: (Debian 8.3.0-6) 8.3.0",
        "Linker: Debian LLD 22.1.8",
        "Debian clang version 22.1.8 (1~deb13u4)",
    ]


def test_inspect_shows_no_toolchain_for_a_stub_without_comments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = _inspect(_executable(tmp_path), capsys)  # the test double is not even an ELF file

    assert not any(line.startswith("toolchain") for line in lines)


def test_json_carries_its_schema_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["inspect", "--json", str(_executable(tmp_path))]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == 1
    assert "files" not in report  # only when asked for


def test_inspect_shows_the_libraries_the_runtime_carries_and_the_lock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = _executable(
        tmp_path,
        ("runtime_components", {"openssl": "OpenSSL 3.5.9 29 Sep 2026", "sqlite": "3.53.1"}),
        ("lock", {"kind": "uv.lock", "path": "uv.lock", "sha256": "ab" * 32, "hashed": True}),
    )

    lines = _inspect(executable, capsys)

    assert "components openssl OpenSSL 3.5.9 29 Sep 2026; sqlite 3.53.1" in lines
    assert f"lock       uv.lock sha256:{'ab' * 32}" in lines


@pytest.mark.parametrize("lock", ["uv.lock", None, {"path": None}, {"path": "uv.lock"}, []])
def test_older_or_unlocked_builds_show_no_lock_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], lock: object
) -> None:
    text = json.dumps({"lock": lock, "runtime_components": "x"})

    lines = _inspect(_with_build_info(_executable(tmp_path), text), capsys)

    assert not any(line.startswith(("lock ", "components")) for line in lines)


def _record(rel: str, data: bytes | None) -> str:
    if data is None:
        return f"{rel},,"
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{rel},sha256={digest},{len(data)}"


def _with_record(tmp_path: Path) -> Path:
    info = "aiagent-0.8.1.dist-info"
    lines = [_record("aiagent/__init__.py", b"c"), _record("aiagent/data/x.txt", b"hello"),
             _record(f"{info}/METADATA", b"Name: aiagent\n"), _record(f"{info}/RECORD", None),
             '"aiagent/odd,name.txt",sha256=bad!,7', "aiagent/gone.py,sha256=,"]  # fmt: skip
    files = (File("aiagent/data/x.txt", FileKind.RESOURCE, Blob.zstd(b"hello"), dist="aiagent"),
             File(f"{info}/RECORD", FileKind.METADATA, Blob.zstd("\n".join(lines).encode()),
                  dist="aiagent"))  # fmt: skip
    app = App("aiagent", "aiagent.cli:main", dists=DISTS[:1], modules=MODULES[:1], files=files)
    out = tmp_path / "recorded"
    build_executable(FAKE_RUNTIME, app, out)
    return out


def test_files_lists_the_record_lines_in_the_payload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = _with_record(tmp_path)

    assert main(["inspect", "--json", "--files", str(executable)]) == 0

    files = {f["path"]: f for f in json.loads(capsys.readouterr().out)["files"]}
    sha = hashlib.sha256
    assert files["aiagent/__init__.py"] == {"dist": "aiagent", "path": "aiagent/__init__.py",
                                            "kind": "package", "size": 1,
                                            "sha256": sha(b"c").hexdigest()}  # fmt: skip
    assert files["aiagent/data/x.txt"]["kind"] == "resource"
    assert files["aiagent/data/x.txt"]["sha256"] == sha(b"hello").hexdigest()
    assert files["aiagent-0.8.1.dist-info/RECORD"] | {"dist": 0} == {
        "dist": 0, "path": "aiagent-0.8.1.dist-info/RECORD", "kind": "metadata", "size": None,
        "sha256": None}  # fmt: skip
    assert files["aiagent/odd,name.txt"]["sha256"] is None  # not a digest
    assert (files["aiagent/gone.py"]["kind"], files["aiagent/gone.py"]["size"]) == ("other", None)


def test_files_in_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    executable = _with_record(tmp_path)

    assert main(["inspect", "--files", str(executable)]) == 0

    out = capsys.readouterr().out.splitlines()
    assert "files: 6" in out
    digest = hashlib.sha256(b"hello").hexdigest()
    assert f"  {digest}           5  resource   aiagent: aiagent/data/x.txt" in out
    assert any(line.startswith(f"  {'-' * 64}") for line in out)  # no digest listed


def _with_notices(tmp_path: Path) -> Path:
    info = "aiagent-0.8.1.dist-info"
    metadata = b"Name: aiagent\nLicense-File: AUTHORS.txt\nLicense-File: COPYING.extra\n\nbody"
    contents: dict[str, tuple[str | bytes, FileKind, str | None]] = {
        "_fsbpyr_licenses/LICENSE.openssl-3.txt": ("Apache License text", FileKind.RESOURCE, None),
        "_fsbpyr_licenses/LICENSE.fsbpyr.txt": ("MIT License text", FileKind.RESOURCE, None),
        f"{info}/METADATA": (metadata, FileKind.METADATA, "aiagent"),
        f"{info}/licenses/LICENSE.txt": ("dist-info licenses/", FileKind.METADATA, "aiagent"),
        f"{info}/AUTHORS.txt": ("declared by METADATA", FileKind.METADATA, "aiagent"),
        f"{info}/COPYING.extra": ("dist-info root", FileKind.METADATA, "aiagent"),
        f"{info}/entry_points.txt": ("not a notice", FileKind.METADATA, "aiagent"),
        "aiagent/ThirdPartyNotices.txt": ("in the package\x1b[2J", FileKind.RESOURCE, "aiagent"),
        "aiagent/data/x.txt": ("not a notice", FileKind.RESOURCE, "aiagent"),
        "other/LICENSE": ("another distribution's", FileKind.RESOURCE, "click"),
    }
    files = tuple(File(path, kind, Blob.zstd(text if isinstance(text, bytes) else text.encode()),
                       dist=dist) for path, (text, kind, dist) in contents.items())  # fmt: skip
    app = App("aiagent", "aiagent.cli:main", dists=DISTS[:2], modules=MODULES[:1], files=files)
    out = tmp_path / "noticed"
    build_executable(FAKE_RUNTIME, app, out)
    return out


def test_licenses_prints_the_runtimes_notices_then_each_distributions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = _with_notices(tmp_path)

    assert main(["inspect", "--licenses", str(executable)]) == 0

    out = capsys.readouterr().out
    headers = [line for line in out.splitlines() if line.startswith("====")]
    info = "aiagent-0.8.1.dist-info"
    assert headers == [
        "==== fsbpyr: _fsbpyr_licenses/LICENSE.fsbpyr.txt ====",
        "==== fsbpyr: _fsbpyr_licenses/LICENSE.openssl-3.txt ====",
        f"==== aiagent 0.8.1: {info}/AUTHORS.txt ====",
        f"==== aiagent 0.8.1: {info}/COPYING.extra ====",
        f"==== aiagent 0.8.1: {info}/licenses/LICENSE.txt ====",
        "==== aiagent 0.8.1: aiagent/ThirdPartyNotices.txt ====",
        "==== click 8.2.1: other/LICENSE ====",
    ]
    assert "Apache License text" in out
    assert "not a notice" not in out
    assert "\x1b" not in out and "in the package\\x1b[2J" in out  # no escape sequence reaches a tty


def test_licenses_of_an_executable_without_notices_prints_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["inspect", "--licenses", str(_executable(tmp_path))]) == 0

    assert capsys.readouterr().out.strip() == ""


def test_licenses_stands_alone_and_rejects_other_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = _executable(tmp_path)
    assert main(["inspect", "--licenses", "--json", str(executable)]) == 2

    (tmp_path / "plain").write_bytes(b"not an executable")
    assert main(["inspect", "--licenses", str(tmp_path / "plain")]) == 1
    assert "not an executable fsbpyr built" in capsys.readouterr().err
    assert main(["inspect", "--licenses", str(tmp_path / "missing")]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_text_shows_the_settings_and_the_sizes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = _executable(tmp_path, ("settings", SETTINGS))

    lines = _lines(capsys, executable)

    assert "extras     fast" in lines and "fs pkgs    certifi" in lines
    assert "allow libs libz.so.1" in lines and "sources    left out" in lines
    assert "stored bytes (without the trailer):" in lines
    total = executable.stat().st_size - TRAILER_SIZE
    assert any(line.strip().startswith("total") and f"{total:,}" in line for line in lines)


def test_older_builds_without_settings_show_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = _executable(tmp_path)

    assert main(["inspect", "--json", str(executable)]) == 0

    assert json.loads(capsys.readouterr().out)["settings"] is None
    assert not any(line.startswith("extras") for line in _lines(capsys, executable))


def test_sizes_add_up_for_blocks_and_shared_blobs(tmp_path: Path) -> None:
    same = Blob.code(b"x" * 300)  # one blob, owned by two modules: the first path takes it
    modules = (Module("a", "a.py", ModuleKind.MODULE, code=same, dist="aiagent"),
               Module("b", "b.py", ModuleKind.MODULE, code=same, dist="click"),
               Module("c", "c.py", ModuleKind.MODULE, code=Blob.code(b"y" * 1000),
                      dist="click"))  # fmt: skip
    app = App("aiagent", "a:main", dists=DISTS[:2], modules=modules)
    out = tmp_path / "aiagent"
    build_executable(FAKE_RUNTIME, app, out)

    with Executable.open(out) as executable:
        sizes = measure(executable)

    assert sizes.total == out.stat().st_size - TRAILER_SIZE
    assert sizes.unreferenced == 0
    assert sizes.distributions["aiagent"]["code"] > 0
