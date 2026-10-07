"""ELF dynamic-section parsing and native dependency closures (plan §3.5, build time)."""

import struct
from pathlib import Path

import pytest

from fsbpyr.elf import DynamicInfo, ElfError, dynamic_info
from fsbpyr.natives import NativeError, closures

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


def test_runpath_takes_precedence_over_rpath() -> None:
    natives = {
        "p/_x.so": make_elf(needed=("liba.so",), rpath="$ORIGIN/old", runpath="$ORIGIN/new"),
        "p/old/liba.so": make_elf(),
        "p/new/liba.so": make_elf(),
    }

    assert closures(natives, {"p/_x.so"}, allowed=frozenset())["p/_x.so"] == ("p/new/liba.so",)


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
