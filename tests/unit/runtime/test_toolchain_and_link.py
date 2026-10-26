"""ar member listing, clang discovery and the static link plan derived from PYTHON.json."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from fsbpyr.runtime import ar, stub
from fsbpyr.runtime.pbs import PbsDist, link_inputs
from fsbpyr.runtime.stub import StubBuildError, stub_command
from fsbpyr.runtime.toolchain import (
    Toolchain,
    ToolchainError,
    clang_major,
    describe,
    find_toolchain,
)


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


def test_describe_gives_the_first_line_of_each_version(tmp_path: Path) -> None:
    clang = _fake_tool(tmp_path, "clang-22", "Debian clang version 22.1.8 (1~deb13u4)\\nTarget: x")
    lld = _fake_tool(tmp_path, "ld.lld-22", "Debian LLD 22.1.8 (compatible with GNU linkers)")

    assert describe(Toolchain(clang=clang, lld=lld, major=22)) == {
        "clang": "Debian clang version 22.1.8 (1~deb13u4)",
        "lld": "Debian LLD 22.1.8 (compatible with GNU linkers)",
    }
    gone = Toolchain(clang=tmp_path / "missing", lld=lld, major=22)
    assert describe(gone)["clang"] == "unknown"


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


def test_link_inputs_cover_builtin_modules_only(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)
    archive_members = {"_ssl.o", "sha2module.o", "mathmodule.o"}

    inputs = link_inputs(dist, archive_members)

    root = dist.root
    assert inputs.libpython == root / "install/lib/libpython3.14.a"
    assert inputs.extra_objects == (root / "build/Modules/_hacl/Hacl_Hash_SHA2.o",)
    assert inputs.static_libs == (root / "build/lib/libcrypto.a", root / "build/lib/libssl.a")
    assert inputs.system_libs == ("pthread", "m")


def _with_zstd_archive(dist: PbsDist) -> Path:
    archive = dist.root / "build" / "lib" / "libzstd.a"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"!<arch>\n")
    return archive


def _link_zstd_from(dist: PbsDist, module: str) -> PbsDist:
    """``dist`` with ``module`` linking libzstd.a, as 3.14's ``_zstd`` does."""
    info = json.loads((dist.root / "PYTHON.json").read_text())
    info["build_info"]["extensions"][module][0]["links"].append(
        {"name": "zstd", "path_static": "build/lib/libzstd.a"}
    )
    (dist.root / "PYTHON.json").write_text(json.dumps(info))
    return PbsDist.load(dist.root)


def test_link_inputs_add_libzstd_when_no_builtin_module_links_it(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)  # 3.12 and 3.13 have no _zstd; the launcher decodes zstd itself
    archive = _with_zstd_archive(dist)

    inputs = link_inputs(dist, {"_ssl.o", "sha2module.o", "mathmodule.o"})

    assert inputs.static_libs == (
        dist.root / "build/lib/libcrypto.a", dist.root / "build/lib/libssl.a", archive,
    )  # fmt: skip


def test_link_inputs_keep_libzstd_once_when_a_builtin_module_links_it(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)
    archive = _with_zstd_archive(dist)
    dist = _link_zstd_from(dist, "math")  # stands for 3.14's _zstd

    inputs = link_inputs(dist, {"_ssl.o", "sha2module.o", "mathmodule.o"})

    assert inputs.static_libs == (
        dist.root / "build/lib/libcrypto.a", dist.root / "build/lib/libssl.a", archive,
    )  # fmt: skip
    assert inputs.static_libs.count(archive) == 1


def test_link_inputs_leave_out_libzstd_when_the_archive_is_missing(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)

    inputs = link_inputs(dist, {"_ssl.o", "sha2module.o", "mathmodule.o"})

    assert all(lib.name != "libzstd.a" for lib in inputs.static_libs)


def test_stub_command_links_statically_as_pie(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)
    inputs = link_inputs(dist, {"_ssl.o", "sha2module.o", "mathmodule.o"})
    toolchain = Toolchain(clang=Path("/x/clang-22"), lld=Path("/x/ld.lld-22"), major=22,
                          sysroot=Path("/s/sysroot"))  # fmt: skip
    sources = [Path("/src/main.c"), Path("/src/payload.c")]

    cmd = stub_command(dist, inputs, toolchain, sources, tmp_path / "stub")

    assert cmd[0] == "/x/clang-22"
    for flag in (
        "--target=x86_64-unknown-linux-gnu",
        "-flto=full",
        "-pie",
        "-fPIE",
        "--ld-path=/x/ld.lld-22",
        "-Wl,--export-dynamic-symbol=Py*",
        "-Wl,--export-dynamic-symbol=_Py*",
        "-DFSBPYR_BYTECODE_MAGIC=0x0a0d0e2bu",
        "--sysroot=/s/sysroot",  # Debian 12's glibc, not the build host's (Phase 6c)
        "-s",
    ):
        assert flag in cmd
    # only the Python C API is exported: extensions must not bind to the stub's static zlib,
    # OpenSSL or libffi in place of the copies their wheels bundle
    assert "-Wl,--export-dynamic" not in cmd
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
    toolchain = Toolchain(clang=Path("/x/clang-22"), lld=Path("/x/ld.lld-22"), major=22,
                          sysroot=Path("/s/sysroot"))  # fmt: skip

    cmd = stub_command(dist, inputs, toolchain, [Path("/src/main.c")], tmp_path / "stub")

    assert {"-fstack-protector-strong", "-D_FORTIFY_SOURCE=2"} <= set(cmd)
    assert not {"-no-pie", "-fno-pie"} & set(cmd)  # PIE by default (D17): ASLR for the image


def test_sanitized_stub_command_checks_memory_and_undefined_behaviour(tmp_path: Path) -> None:
    """D38: the test variant of the launcher, built with ASan and UBSan; LTO and stripping would
    hide where a report points, and FORTIFY's own checks would overlap ASan's."""
    dist = _fake_dist(tmp_path)
    inputs = link_inputs(dist, set())
    toolchain = Toolchain(clang=Path("/x/clang-22"), lld=Path("/x/ld.lld-22"), major=22,
                          sysroot=Path("/s/sysroot"))  # fmt: skip

    cmd = stub_command(dist, inputs, toolchain, [Path("/src/main.c")], tmp_path / "stub",
                       sanitize=True)  # fmt: skip

    wanted = {"-fsanitize=address,undefined", "-fno-sanitize-recover=undefined",
              "-fno-omit-frame-pointer", "-g", "-Wvla", "--sysroot=/s/sysroot"}  # fmt: skip
    assert wanted <= set(cmd)
    assert not {"-flto=full", "-s", "-D_FORTIFY_SOURCE=2", "-O2"} & set(cmd)


def test_stub_command_ends_with_the_outlined_atomics_when_given_them(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)
    inputs = link_inputs(dist, set())
    toolchain = Toolchain(clang=Path("/x/clang-22"), lld=Path("/x/ld.lld-22"), major=22,
                          sysroot=Path("/s/sysroot"))  # fmt: skip
    plain = stub_command(dist, inputs, toolchain, [Path("main.c")], Path("stub"))

    withatomics = stub_command(dist, inputs, toolchain, [Path("main.c")], Path("stub"),
                               atomics=Path("/rt/libclang_rt.builtins-aarch64.a"))  # fmt: skip

    assert withatomics == [*plain, "/rt/libclang_rt.builtins-aarch64.a"]


def _clang_printing(tmp_path: Path, library: Path, status: int = 0) -> Toolchain:
    clang = tmp_path / "clang-22"
    clang.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/args"\necho {library}\nexit {status}\n')
    clang.chmod(0o755)
    return Toolchain(clang=clang, lld=tmp_path / "ld.lld-22", major=22)


def test_x86_64_links_no_outlined_atomics(tmp_path: Path) -> None:
    toolchain = _clang_printing(tmp_path, tmp_path / "x")

    assert stub.outlined_atomics(toolchain, "x86_64-unknown-linux-gnu") is None
    assert not (tmp_path / "args").exists()  # clang is not even asked


def test_aarch64_links_the_compiler_rt_builtins_clang_names(tmp_path: Path) -> None:
    library = tmp_path / "libclang_rt.builtins-aarch64.a"
    library.write_bytes(b"!<arch>\n")
    toolchain = _clang_printing(tmp_path, library)

    assert stub.outlined_atomics(toolchain, "aarch64-unknown-linux-gnu") == library
    assert (tmp_path / "args").read_text().split() == [
        "--target=aarch64-unknown-linux-gnu", "-rtlib=compiler-rt", "--print-libgcc-file-name",
    ]


@pytest.mark.parametrize("status", [0, 1])
def test_aarch64_without_the_builtins_names_the_package_to_install(
    tmp_path: Path, status: int
) -> None:
    toolchain = _clang_printing(tmp_path, tmp_path / "missing.a", status)

    with pytest.raises(StubBuildError, match="install libclang-rt-22-dev"):
        stub.outlined_atomics(toolchain, "aarch64-unknown-linux-gnu")


def test_stub_command_maps_the_cache_paths_out_of_the_launcher(tmp_path: Path) -> None:
    """Where the cache lives (~/.cache of whoever builds) must not reach the launcher's bytes."""
    dist = _fake_dist(tmp_path)
    inputs = link_inputs(dist, set())
    toolchain = Toolchain(clang=Path("/x/clang-22"), lld=Path("/x/ld.lld-22"), major=22,
                          sysroot=Path("/s/sysroot"))  # fmt: skip

    cmd = stub_command(dist, inputs, toolchain, [Path("main.c")], Path("stub"))

    assert f"-ffile-prefix-map={dist.root}=/fsbpyr/python" in cmd
    assert "-ffile-prefix-map=/s/sysroot=/fsbpyr/sysroot" in cmd
    assert "-I." in cmd


def test_the_link_runs_in_its_directory_with_relative_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The temporary directory's random name must not reach the launcher (it did, through
    LTO): clang runs inside it on relative paths, and the result is copied to ``out``."""
    dist = _fake_dist(tmp_path)
    toolchain = Toolchain(clang=Path("/x/clang-22"), lld=Path("/x/ld.lld-22"), major=22)
    monkeypatch.setattr(ar, "members", lambda path: [])
    seen: dict[str, object] = {}

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        work = Path(str(kwargs["cwd"]))
        seen.update(command=command, cwd=work, sources=(work / "main.c").exists())
        (work / command[command.index("-o") + 1]).write_bytes(b"linked")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)

    stub.build_stub(dist, toolchain, tmp_path / "stub")

    command = seen["command"]
    assert isinstance(command, list)
    assert seen["sources"]
    assert not [arg for arg in command if str(seen["cwd"]) in arg]
    assert "main.c" in command
    assert (tmp_path / "stub").read_bytes() == b"linked"


def _link_printing(stderr: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[str, ...]:
    """build_stub with a linker that succeeds and prints ``stderr``."""
    dist = _fake_dist(tmp_path)
    toolchain = Toolchain(clang=Path("/x/clang-22"), lld=Path("/x/ld.lld-22"), major=22)
    monkeypatch.setattr(ar, "members", lambda path: [])

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        (Path(str(kwargs["cwd"])) / command[command.index("-o") + 1]).write_bytes(b"linked")
        return subprocess.CompletedProcess(command, 0, "", stderr)

    monkeypatch.setattr(subprocess, "run", run)
    return stub.build_stub(dist, toolchain, tmp_path / "stub")


def test_the_link_gives_its_warnings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    printed = "ld.lld-22: warning: one\n\nclang-22: warning: two\n"

    assert _link_printing(printed, monkeypatch, tmp_path) == (
        "ld.lld-22: warning: one",
        "clang-22: warning: two",
    )


def test_a_frame_over_the_limit_fails_the_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D38: -Wframe-larger-than sizes each source's frames; LTO inlines across sources (and
    CPython's code into ours) and sizes them again at the link, where only a warning says so."""
    printed = ("ld.lld-22: warning: <unknown>:0:0: stack frame size (8888) exceeds limit (8192) "
               "in function 'cache_sync'\n")  # fmt: skip

    with pytest.raises(StubBuildError, match=r"stack frame size \(8888\).*cache_sync"):
        _link_printing(printed, monkeypatch, tmp_path)


def test_stub_command_passes_the_runtime_id_the_stub_records(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)
    inputs = link_inputs(dist, set())
    toolchain = Toolchain(clang=Path("/x/clang-22"), lld=Path("/x/ld.lld-22"), major=22)

    plain = stub_command(dist, inputs, toolchain, [Path("main.c")], Path("stub"))
    named = stub_command(dist, inputs, toolchain, [Path("main.c")], Path("stub"),
                         identity="cp314-x86_64-unknown-linux-gnu-0123456789ab")  # fmt: skip

    assert not any(flag.startswith("-DFSBPYR_RUNTIME_ID") for flag in plain)
    assert '-DFSBPYR_RUNTIME_ID="cp314-x86_64-unknown-linux-gnu-0123456789ab"' in named
    assert [flag for flag in named if not flag.startswith("-DFSBPYR_RUNTIME_ID")] == plain


def test_the_identity_flag_is_not_part_of_the_runtime_id() -> None:
    """The id is what the flag carries: it cannot depend on it."""
    assert not any("RUNTIME_ID" in flag for flag in (*stub.COMPILE_FLAGS, *stub.LINK_FLAGS))
