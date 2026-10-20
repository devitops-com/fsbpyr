"""ELF dynamic-section parsing and native dependency closures (plan §3.5, build time)."""

import struct
from pathlib import Path

import pytest

from fsbpyr.elf import (
    DynamicInfo,
    ElfError,
    dynamic_info,
    glibc_requirement,
    needed_versions,
    section,
)
from fsbpyr.natives import NativeError, closures, is_extension_module

from ...support.elf import DT_NEEDED, make_elf

# --- elf ---------------------------------------------------------------------------------


def test_dynamic_info_reads_needed_paths_and_soname() -> None:
    data = make_elf(needed=("libfoo.so.1", "libc.so.6"), runpath="$ORIGIN:$ORIGIN/../lib",
                    soname="libext.so")  # fmt: skip

    assert dynamic_info(data) == DynamicInfo(
        needed=("libfoo.so.1", "libc.so.6"),
        rpath=(),
        runpath=("$ORIGIN", "$ORIGIN/../lib"),
        soname="libext.so",
    )


def test_section_reads_a_named_section_by_its_name() -> None:
    comment = b"\0Linker: Debian LLD 22.1.8\0Debian clang version 22.1.8 (1~deb13u4)\0"
    data = make_elf(needed=("libc.so.6",), comment=comment)

    assert section(data, ".comment") == comment
    assert section(data, ".shstrtab") == b"\0.comment\0.shstrtab\0"
    assert section(data, ".nope") is None
    assert section(data, ".comm") is None  # a prefix is not the name
    assert dynamic_info(data).needed == ("libc.so.6",)  # the sections change nothing else


def test_section_of_an_object_without_section_headers_is_none() -> None:
    assert section(make_elf(), ".comment") is None


def test_section_rejects_damaged_section_headers() -> None:
    data = bytearray(make_elf(comment=b"x\0"))
    with pytest.raises(ElfError, match="truncated"):
        section(bytes(data[:-100]), ".comment")  # the header table is cut off
    (shoff,) = struct.unpack_from("<Q", data, 40)
    bigger = bytearray(data)
    struct.pack_into("<Q", bigger, shoff + 64 + 32, 1 << 40)  # .comment claims a huge size
    with pytest.raises(ElfError, match="beyond the end"):
        section(bytes(bigger), ".comment")
    data[60:64] = struct.pack("<HH", 3, 9)  # e_shstrndx past e_shnum
    with pytest.raises(ElfError, match="bad ELF section headers"):
        section(bytes(data), ".comment")
    with pytest.raises(ElfError, match="not an ELF"):
        section(b"MZ", ".comment")


def test_objects_without_dynamic_section_have_no_dependencies() -> None:
    assert dynamic_info(make_elf(needed=("x",), dynamic=False)) == DynamicInfo((), (), (), None)


def test_real_host_library_parses() -> None:
    libc = Path("/lib/x86_64-linux-gnu/libc.so.6")
    if not libc.exists():
        pytest.skip("no glibc at the Debian path")

    info = dynamic_info(libc.read_bytes())

    assert info.soname == "libc.so.6" and "ld-linux-x86-64.so.2" in info.needed


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"MZ" + bytes(100), "not an ELF"),
        (b"\x7fELF" + bytes([1, 1]) + bytes(58), "64-bit"),
        (make_elf(machine=183), "x86-64"),
        (make_elf()[:70], "truncated"),
    ],
)
def test_malformed_or_foreign_elf_is_rejected(data: bytes, message: str) -> None:
    with pytest.raises(ElfError, match=message):
        dynamic_info(data)


def test_out_of_range_string_offsets_are_rejected() -> None:
    data = bytearray(make_elf(needed=("libfoo.so",)))
    dyn_start = data.rindex(struct.pack("<qQ", DT_NEEDED, 1))
    struct.pack_into("<qQ", data, dyn_start, DT_NEEDED, 10**6)

    with pytest.raises(ElfError, match="string"):
        dynamic_info(bytes(data))


# --- closures ------------------------------------------------------------------------------

NUMPY_LIKE = {
    "np/_core/_umath.cpython-314-x86_64-linux-gnu.so": make_elf(
        needed=("libblas-1.so", "libm.so.6", "libc.so.6"), rpath="$ORIGIN/../../np.libs"),
    "np/fft/_fft.cpython-314-x86_64-linux-gnu.so": make_elf(needed=("libstdc++.so.6",)),
    "np.libs/libblas-1.so": make_elf(needed=("libgfortran-2.so.5", "libpthread.so.0"),
                                     rpath="$ORIGIN"),
    "np.libs/libgfortran-2.so.5": make_elf(needed=("libquadmath-3.so.0", "libz.so.1"),
                                           rpath="${ORIGIN}"),
    "np.libs/libquadmath-3.so.0": make_elf(needed=("libm.so.6",)),
}  # fmt: skip
EXTENSIONS = {"np/_core/_umath.cpython-314-x86_64-linux-gnu.so",
              "np/fft/_fft.cpython-314-x86_64-linux-gnu.so"}  # fmt: skip


def test_closures_follow_rpath_transitively() -> None:
    result = closures(NUMPY_LIKE, EXTENSIONS, allowed=frozenset())

    assert result == {
        "np/_core/_umath.cpython-314-x86_64-linux-gnu.so": (
            "np.libs/libblas-1.so", "np.libs/libgfortran-2.so.5", "np.libs/libquadmath-3.so.0"),
        "np/fft/_fft.cpython-314-x86_64-linux-gnu.so": (),
    }  # fmt: skip


def test_libraries_an_extension_names_are_loaded_with_it() -> None:
    """dlopen("libx_shared.so") by name, as onnxruntime's provider bridge does: no DT_NEEDED."""
    natives = {
        "p/capi/_x.so": make_elf(runpath="$ORIGIN/../../p.libs", soname="PyInit__x")
        + b"\0libx_shared.so\0libr.so\0",
        "p/capi/libx_shared.so": make_elf(needed=("libdep.so",), runpath="$ORIGIN"),
        "p/capi/libdep.so": make_elf(),
        "p/capi/libx.so.1": make_elf(),  # never named
        "p.libs/libr.so": make_elf(),  # found through the RUNPATH, as dlopen would
        "q/libx_shared.so": make_elf(),  # same name where neither dlopen nor dladdr looks
        "p/capi/_y.so": make_elf(soname="PyInit__y") + b"\0libx_shared.so.1\0xlibdep.so\0_x.so\0",
    }

    result = closures(natives, {"p/capi/_x.so", "p/capi/_y.so"}, allowed=frozenset())

    assert result == {
        "p/capi/_x.so": ("p.libs/libr.so", "p/capi/libdep.so", "p/capi/libx_shared.so"),
        "p/capi/_y.so": (),  # other names, and extensions are imported, not opened by name
    }


def test_names_are_matched_as_whole_strings_or_path_ends() -> None:
    natives = {
        "_top.so": make_elf(soname="PyInit__top") + b"\0xlibb.so\0/opt/x/libb.so\0",
        "p/_x.so": make_elf(runpath="$ORIGIN/..", soname="PyInit__x") + b"\0liba.so\0",
        "liba.so": make_elf() + b"\0libb.so\0",  # a library naming another one
        "libb.so": make_elf() + b"\0liba.so\0libb.so\0",  # a cycle, and itself
    }

    result = closures(natives, {"_top.so", "p/_x.so"}, allowed=frozenset())

    assert result == {"_top.so": ("libb.so", "liba.so")[::-1], "p/_x.so": ("liba.so", "libb.so")}


def test_libraries_opened_by_name_that_cannot_load_are_left_out() -> None:
    """onnxruntime-gpu's CUDA provider needs libcudart, which no wheel of the app bundles."""
    natives = {
        "p/_x.so": make_elf(soname="PyInit__x") + b"\0libcuda_provider.so\0libok.so\0",
        "p/libcuda_provider.so": make_elf(needed=("libcudart.so.12",)),
        "p/libok.so": make_elf(),
    }
    warnings: list[str] = []

    result = closures(natives, {"p/_x.so"}, allowed=frozenset(), warn=warnings.append)

    assert result == {"p/_x.so": ("p/libok.so",)}
    assert len(warnings) == 1
    assert "p/libcuda_provider.so" in warnings[0] and "libcudart.so.12" in warnings[0]
    assert "p/_x.so" in warnings[0]  # where it came from
    with pytest.raises(NativeError, match="libcudart"):  # needed by the extension itself
        closures({**natives, "p/_y.so": make_elf(needed=("libcudart.so.12",))}, {"p/_y.so"},
                 allowed=frozenset())  # fmt: skip


def test_needed_chains_stay_strict_after_a_by_name_visit() -> None:
    """libl is first reached through a library opened by name, then through NEEDED only."""
    origin = "$ORIGIN"
    natives = {
        "p/_x.so": make_elf(needed=("libc1.so", "libc2.so"), runpath=origin, soname="PyInit__x"),
        "p/libc1.so": make_elf(needed=("libl.so",), runpath=origin),
        "p/libc2.so": make_elf(runpath=origin) + b"\0libb2.so\0",
        "p/libb2.so": make_elf(needed=("libl.so",), runpath=origin),
        "p/libl.so": make_elf(needed=("libm.so",), runpath=origin),
        "p/libm.so": make_elf(needed=("libcudart.so.12",)),
    }

    with pytest.raises(NativeError, match="libcudart"):  # _x -> libc1 -> libl -> libm: all NEEDED
        closures(natives, {"p/_x.so"}, allowed=frozenset())


def test_runpath_takes_precedence_over_rpath() -> None:
    natives = {
        "p/_x.so": make_elf(needed=("liba.so",), rpath="$ORIGIN/old", runpath="$ORIGIN/new"),
        "p/old/liba.so": make_elf(),
        "p/new/liba.so": make_elf(),
    }

    assert closures(natives, {"p/_x.so"}, allowed=frozenset())["p/_x.so"] == ("p/new/liba.so",)


def test_libraries_without_runpath_inherit_the_rpath_of_their_loaders() -> None:
    """gnureadline 8.3.3's bundled libncurses has no RPATH: ld.so finds its libtinfo through
    the RPATH of the extension that loaded it."""
    natives = {
        "_r.so": make_elf(needed=("libncurses-1.so.6",), rpath="$ORIGIN/r.libs",
                          soname="PyInit__r"),
        "r.libs/libncurses-1.so.6": make_elf(needed=("libtinfo-2.so.6",)),
        "r.libs/libtinfo-2.so.6": make_elf(),
    }  # fmt: skip

    assert closures(natives, {"_r.so"}, allowed=frozenset()) == {
        "_r.so": ("r.libs/libncurses-1.so.6", "r.libs/libtinfo-2.so.6")
    }


def test_a_runpath_ends_the_inherited_search_for_its_own_object_only() -> None:
    """ld.so ignores the loaders' RPATHs for an object with a RUNPATH, but passes them on to
    what that object loads."""
    natives = {
        "p/_x.so": make_elf(needed=("liba.so",), rpath="$ORIGIN/x.libs", soname="PyInit__x"),
        "p/x.libs/liba.so": make_elf(needed=("libb.so",), runpath="$ORIGIN/a"),
        "p/x.libs/a/libb.so": make_elf(needed=("libd.so",)),
        "p/x.libs/libd.so": make_elf(),
    }

    assert closures(natives, {"p/_x.so"}, allowed=frozenset())["p/_x.so"] == (
        "p/x.libs/a/libb.so", "p/x.libs/liba.so", "p/x.libs/libd.so")  # fmt: skip
    natives["p/x.libs/liba.so"] = make_elf(needed=("libd.so",), runpath="$ORIGIN/a")
    with pytest.raises(NativeError, match="libd"):
        closures(natives, {"p/_x.so"}, allowed=frozenset())


def test_unresolved_library_is_an_error_unless_allowed() -> None:
    natives = {"p/_x.so": make_elf(needed=("libssl.so.3",))}

    with pytest.raises(NativeError, match=r"libssl\.so\.3.*--allow-system-lib"):
        closures(natives, {"p/_x.so"}, allowed=frozenset())
    assert closures(natives, {"p/_x.so"}, allowed=frozenset({"libssl.so.3"})) == {"p/_x.so": ()}


def test_libpython_dependencies_are_rejected() -> None:
    natives = {"p/_x.so": make_elf(needed=("libpython3.14.so.1.0",))}

    with pytest.raises(NativeError, match="libpython"):
        closures(natives, {"p/_x.so"}, allowed=frozenset({"libpython3.14.so.1.0"}))


def test_absolute_rpath_entries_never_resolve_into_the_payload() -> None:
    natives = {"p/_x.so": make_elf(needed=("liba.so",), rpath="/usr/lib"), "usr/lib/liba.so": b""}

    with pytest.raises(NativeError, match="liba"):
        closures(natives, {"p/_x.so"}, allowed=frozenset())


def test_top_level_extensions_resolve_origin_relative_paths() -> None:
    natives = {
        "_top.cpython-314-x86_64-linux-gnu.so": make_elf(needed=("liba.so", "libb.so"),
                                                         runpath="$ORIGIN/top.libs:$ORIGIN"),
        "top.libs/liba.so": make_elf(),
        "libb.so": make_elf(),
    }  # fmt: skip

    result = closures(natives, {"_top.cpython-314-x86_64-linux-gnu.so"}, allowed=frozenset())

    assert result["_top.cpython-314-x86_64-linux-gnu.so"] == ("libb.so", "top.libs/liba.so")


def test_origin_is_only_expanded_as_a_whole_token() -> None:
    natives = {"p/_x.so": make_elf(needed=("liba.so",), rpath="$ORIGINAL"), "p/liba.so": b""}

    with pytest.raises(NativeError, match="liba"):  # ld.so does not expand $ORIGINAL
        closures(natives, {"p/_x.so"}, allowed=frozenset())


def test_needed_entries_with_a_slash_are_rejected() -> None:
    natives = {"p/_x.so": make_elf(needed=("sub/liba.so",), rpath="$ORIGIN"), "p/sub/liba.so": b""}

    with pytest.raises(NativeError, match="path"):  # ld.so would resolve it against the cwd
        closures(natives, {"p/_x.so"}, allowed=frozenset({"sub/liba.so"}))


# --- symbol versions (glibc requirement, Phase 6c) ------------------------------------------


def test_required_symbol_versions_are_read() -> None:
    data = make_elf(needed=("libc.so.6", "libm.so.6"),
                    versions=(("libc.so.6", ("GLIBC_2.2.5", "GLIBC_2.17")),
                              ("libm.so.6", ("GLIBC_2.29",))))  # fmt: skip

    assert needed_versions(data) == {
        ("libc.so.6", "GLIBC_2.2.5"),
        ("libc.so.6", "GLIBC_2.17"),
        ("libm.so.6", "GLIBC_2.29"),
    }
    assert glibc_requirement(data) == (2, 29)
    assert glibc_requirement(make_elf()) is None


def test_host_binaries_report_their_glibc_requirement() -> None:
    ls = Path("/bin/ls")
    if not ls.exists():
        pytest.skip("no /bin/ls")

    requirement = glibc_requirement(ls.read_bytes())

    assert requirement is not None and requirement >= (2, 2)


def test_version_tables_are_parsed_with_bounded_work() -> None:
    entries = tuple(("libc.so.6", ("GLIBC_2.17",) * 300) for _ in range(300))  # 90000 entries

    with pytest.raises(ElfError, match="version"):
        needed_versions(make_elf(versions=entries))


def test_glibc_abi_markers_count_as_their_release() -> None:
    data = make_elf(versions=(("libc.so.6", ("GLIBC_2.17", "GLIBC_ABI_DT_RELR")),))

    assert glibc_requirement(data) == (2, 36)  # DT_RELR support arrived in glibc 2.36


def test_malformed_version_tables_do_not_affect_dependency_checks() -> None:
    data = bytearray(make_elf(needed=("libc.so.6",), versions=(("libc.so.6", ("GLIBC_2.17",)),)))
    verneed = data.index(struct.pack("<HH", 1, 1))
    struct.pack_into("<I", data, verneed + 8, 0x7FFFFFF0)  # vn_aux far outside the file

    assert dynamic_info(bytes(data)).needed == ("libc.so.6",)
    with pytest.raises(ElfError):
        glibc_requirement(bytes(data))


def test_overlong_strings_are_rejected() -> None:
    with pytest.raises(ElfError, match="string"):
        dynamic_info(make_elf(needed=("x" * 5000,)))


def test_compiled_package_inits_are_extension_modules() -> None:
    init = "cy/__init__.cpython-314-x86_64-linux-gnu.so"  # Cython: CPython calls PyInit_cy

    assert is_extension_module(init, b"\0PyInit_cy\0")
    assert not is_extension_module(init, b"\0PyInit___init__\0")
    assert is_extension_module("_top.so", b"\0PyInit__top\0")


def test_names_are_found_in_one_pass_as_whole_strings_or_path_ends() -> None:
    from fsbpyr.natives import _contained_names, _contains_name  # noqa: PLC0415

    data = (b"libfirst.so\0junk/opt/lib/libpath.so.1\0xlibpart.so\0libnoext\0"
            + b"z" * 100 + b"\0libsuffix.so.2.3\0libtail.so")  # fmt: skip
    names = [b"libfirst.so", b"libpath.so.1", b"libpart.so", b"libnoext", b"libsuffix.so.2.3",
             b"libtail.so", b"libabsent.so", b"libsuffix.so"]  # fmt: skip

    found = _contained_names(data, names)

    assert found == {b"libfirst.so", b"libpath.so.1", b"libnoext", b"libsuffix.so.2.3"}
    assert found == {name for name in names if _contains_name(data, name)}
