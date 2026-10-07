"""ar member listing, clang discovery and the static link plan derived from PYTHON.json."""

import json
import os
from pathlib import Path

import pytest

from fsbpyr.runtime import ar
from fsbpyr.runtime.pbs import PbsDist, link_inputs
from fsbpyr.runtime.stub import stub_command
from fsbpyr.runtime.toolchain import Toolchain, ToolchainError, clang_major, find_toolchain


def _ar_archive(members: dict[str, bytes]) -> bytes:
    """GNU ar with a long-name table, as produced by llvm-ar."""
    longnames = b"".join(name.encode() + b"/\n" for name in members if len(name) > 15)
    out = bytearray(b"!<arch>\n")

    def header(name: bytes, size: int) -> bytes:
        return (
            name.ljust(16)
            + b"0".ljust(12)
            + b"0".ljust(6) * 2
            + b"644".ljust(8)
            + str(size).encode().ljust(10)
            + b"`\n"
        )

    out += header(b"/", 4) + b"\0\0\0\0"  # symbol table, skipped
    if longnames:
        out += header(b"//", len(longnames)) + longnames + (b"\n" if len(longnames) % 2 else b"")
    offset = 0
    for name, data in members.items():
        if len(name) > 15:
            entry = b"/" + str(offset).encode()
            offset += len(name) + 2
        else:
            entry = name.encode() + b"/"
        out += header(entry, len(data)) + data + (b"\n" if len(data) % 2 else b"")
    return bytes(out)


def test_ar_lists_short_and_long_member_names(tmp_path: Path) -> None:
    archive = tmp_path / "libx.a"
    archive.write_bytes(
        _ar_archive({"a.o": b"abc", "a_very_long_member_name.o": b"xy", "b.o": b""})
    )

    assert ar.members(archive) == ["a.o", "a_very_long_member_name.o", "b.o"]


def test_ar_rejects_non_archives(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.a"
    bogus.write_bytes(b"not an archive")

    with pytest.raises(ar.ArError):
        ar.members(bogus)


@pytest.mark.parametrize(
    ("text", "major"),
    [
        ("Debian clang version 22.1.8 (1~deb13u4)\nTarget: x86_64-pc-linux-gnu", 22),
        ("clang version 23.0.0git (https://github.com/llvm/llvm-project abc)", 23),
        ("Apple clang version 17.0.0 (clang-1700.0.13.3)", 17),
        ("gcc (Debian 14.2.0) 14.2.0", None),
    ],
)
def test_clang_major(text: str, major: int | None) -> None:
    assert clang_major(text) == major


def _fake_tool(directory: Path, name: str, output: str) -> Path:
    tool = directory / name
    tool.write_text(f"#!/bin/sh\necho '{output}'\n")
    tool.chmod(0o755)
    return tool


def test_find_toolchain_picks_first_clang_with_required_major(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "clang", "clang version 19.1.7")
    good = _fake_tool(tmp_path, "clang-22", "clang version 22.1.8")
    lld = _fake_tool(tmp_path, "ld.lld-22", "LLD 22.1.8")

    toolchain = find_toolchain(22, search_path=str(tmp_path))

    assert toolchain == Toolchain(clang=good, lld=lld, major=22)


def test_find_toolchain_reports_what_is_missing(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "clang", "clang version 19.1.7")

    with pytest.raises(ToolchainError, match="clang >= 22"):
        find_toolchain(22, search_path=str(tmp_path))


def test_explicit_clang_must_satisfy_version(tmp_path: Path) -> None:
    old = _fake_tool(tmp_path, "clang-18", "clang version 18.1.0")

    with pytest.raises(ToolchainError, match="18"):
        find_toolchain(22, explicit=old, search_path=str(tmp_path))


def _fake_dist(tmp_path: Path) -> PbsDist:
    root = tmp_path / "python"
    (root / "build" / "Modules").mkdir(parents=True)
    (root / "build" / "Modules" / "config.c").write_text(
        'struct _inittab _PyImport_Inittab[] = {\n    {"_ssl", PyInit__ssl},\n'
        '    {"_sha2", PyInit__sha2},\n    {"math", PyInit_math},\n    {0, 0}\n};\n'
    )
    info = {
        "python_version": "3.14.8",
        "python_major_minor_version": "3.14",
        "python_implementation_hex_version": 0x030E08F0,
        "python_bytecode_magic_number": "2b0e0d0a",
        "python_tag": "cp314",
        "target_triple": "x86_64-unknown-linux-gnu",
        "python_exe": "install/bin/python3.14",
        "python_paths": {"stdlib": "install/lib/python3.14", "include": "install/include/py"},
        "python_suffixes": {"extension": [".cpython-314-x86_64-linux-gnu.so", ".abi3.so", ".so"]},
        "build_info": {
            "object_file_format": "llvm-bitcode:22.1.3",
            "inittab_source": "build/Modules/config.c",
            "core": {
                "static_lib": "install/lib/libpython3.14.a",
                "links": [{"name": "pthread", "system": True}],
            },
            "extensions": {
                "_ssl": [
                    {
                        "objs": ["build/Modules/_ssl.o"],
                        "links": [
                            {"name": "crypto", "path_static": "build/lib/libcrypto.a"},
                            {"name": "ssl", "path_static": "build/lib/libssl.a"},
                        ],
                    }
                ],
                "_sha2": [
                    {
                        "objs": [
                            "build/Modules/_hacl/Hacl_Hash_SHA2.o",
                            "build/Modules/sha2module.o",
                        ],
                        "links": [],
                    }
                ],
                "math": [
                    {
                        "objs": ["build/Modules/mathmodule.o"],
                        "links": [{"name": "m", "system": True}],
                    }
                ],
                "_tkinter": [
                    {
                        "objs": ["build/Modules/_tkinter.o"],
                        "links": [{"name": "X11", "path_static": "build/lib/libX11.a"}],
                    }
                ],
            },
        },
    }
    (root / "PYTHON.json").write_text(json.dumps(info))
    return PbsDist.load(root)


def test_dist_properties(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)

    assert (dist.version, dist.tag, dist.triple) == ("3.14.8", "cp314", "x86_64-unknown-linux-gnu")
    assert dist.hexversion == 0x030E08F0
    assert dist.bytecode_magic == b"+\x0e\r\n"
    assert dist.llvm_major == 22
    assert dist.interpreter == dist.root / "install/bin/python3.14"
    assert dist.extension_suffixes == (".cpython-314-x86_64-linux-gnu.so", ".abi3.so", ".so")


def test_link_inputs_cover_builtin_modules_only(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)
    archive_members = {"_ssl.o", "sha2module.o", "mathmodule.o"}

    inputs = link_inputs(dist, archive_members)

    root = dist.root
    assert inputs.libpython == root / "install/lib/libpython3.14.a"
    assert inputs.extra_objects == (root / "build/Modules/_hacl/Hacl_Hash_SHA2.o",)
    assert inputs.static_libs == (root / "build/lib/libcrypto.a", root / "build/lib/libssl.a")
    assert inputs.system_libs == ("pthread", "m")


def test_stub_command_links_statically_without_pie(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)
    inputs = link_inputs(dist, {"_ssl.o", "sha2module.o", "mathmodule.o"})
    toolchain = Toolchain(clang=Path("/x/clang-22"), lld=Path("/x/ld.lld-22"), major=22)
    sources = [Path("/src/main.c"), Path("/src/payload.c")]

    cmd = stub_command(dist, inputs, toolchain, sources, tmp_path / "stub")

    assert cmd[0] == "/x/clang-22"
    for flag in (
        "--target=x86_64-unknown-linux-gnu",
        "-flto=full",
        "-no-pie",
        "-fno-pie",
        "--ld-path=/x/ld.lld-22",
        "-Wl,--export-dynamic",
        "-DFSBPYR_BYTECODE_MAGIC=0x0a0d0e2bu",
        "-s",
    ):
        assert flag in cmd
    group = cmd[cmd.index("-Wl,--start-group") + 1 : cmd.index("-Wl,--end-group")]
    assert group[0] == str(inputs.libpython)
    assert cmd[-2:] == ["-lpthread", "-lm"]
    assert os.fspath(tmp_path / "stub") in cmd


@pytest.mark.parametrize("size", [b"-60", b"99999999", b"abc"])
def test_ar_rejects_bad_member_sizes(tmp_path: Path, size: bytes) -> None:
    header = b"a.o/".ljust(16) + b"0".ljust(12) + b"0".ljust(12) + b"644".ljust(8)
    archive = tmp_path / "bad.a"
    archive.write_bytes(b"!<arch>\n" + header + size.ljust(10) + b"`\n" + b"xx")

    with pytest.raises(ar.ArError):
        ar.members(archive)


def test_stub_command_hardens_launcher_code(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)
    inputs = link_inputs(dist, set())
    toolchain = Toolchain(clang=Path("/x/clang-22"), lld=Path("/x/ld.lld-22"), major=22)

    cmd = stub_command(dist, inputs, toolchain, [Path("/src/main.c")], tmp_path / "stub")

    assert {"-fstack-protector-strong", "-D_FORTIFY_SOURCE=2"} <= set(cmd)
