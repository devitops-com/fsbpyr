"""What the target must provide beyond glibc: version namespaces and floors (NAT-2)."""

import struct

import pytest

from fsbpyr.elf import VersionNeed, version_requirements
from fsbpyr.natives import SystemUse, closures
from fsbpyr.system import STUB, system_report, target_needs

from ...support.elf import make_elf

EXT = "p/_x.cpython-314-x86_64-linux-gnu.so"
CXX = ("libstdc++.so.6", ("GLIBCXX_3.4", "GLIBCXX_3.4.22", "CXXABI_1.3.9", "CXXABI_TM_1"))
STUB_2_28 = make_elf(
    needed=("libc.so.6", "libm.so.6"),
    versions=(("libc.so.6", ("GLIBC_2.2.5", "GLIBC_2.28")), ("libm.so.6", ("GLIBC_2.2.5",))),
)


def _reach(
    natives: dict[str, bytes], allowed: frozenset[str] = frozenset()
) -> tuple[SystemUse, list[str]]:
    use, warnings = SystemUse(), list[str]()
    closures(natives, {EXT}, allowed=allowed, warn=warnings.append, system=use)
    return use, warnings


def test_versions_are_grouped_by_library_and_namespace() -> None:
    data = make_elf(versions=(CXX, ("libgcc_s.so.1", ("GCC_3.0", "GCC_7.0.0")),
                              ("libc.so.6", ("GLIBC_2.17", "GLIBC_PRIVATE"))))  # fmt: skip

    found = version_requirements(data)

    assert found["libstdc++.so.6"] == {
        "CXXABI": VersionNeed((1, 3, 9), ("CXXABI_TM_1",)),
        "GLIBCXX": VersionNeed((3, 4, 22), ()),
    }
    assert found["libgcc_s.so.1"]["GCC"] == VersionNeed((7, 0, 0), ())
    assert found["libc.so.6"]["GLIBC"] == VersionNeed((2, 17), ("GLIBC_PRIVATE",))


def test_system_needs_name_the_files_and_the_versions() -> None:
    natives = {EXT: make_elf(needed=("libstdc++.so.6", "libc.so.6"), versions=(CXX,))}

    use, warnings = _reach(natives)

    assert use.needed_by == {"libstdc++.so.6": {EXT}, "libc.so.6": {EXT}}
    assert use.versions[EXT]["libstdc++.so.6"]["GLIBCXX"].highest == (3, 4, 22)
    assert warnings == []


def test_the_report_lists_libraries_sorted_with_the_stub_s_own_needs() -> None:
    natives = {EXT: make_elf(needed=("libstdc++.so.6", "libgcc_s.so.1"), versions=(CXX,))}
    use, _ = _reach(natives)

    libraries, warnings = system_report(STUB_2_28, use)

    assert list(libraries) == ["libc.so.6", "libgcc_s.so.1", "libm.so.6",
                                         "libstdc++.so.6"]  # fmt: skip
    assert libraries["libc.so.6"] == {"needed_by": [STUB],
                                                "versions": {"GLIBC": "2.28"}}  # fmt: skip
    assert libraries["libstdc++.so.6"] == {
        "needed_by": [EXT],
        "versions": {"CXXABI": "1.3.9", "GLIBCXX": "3.4.22"},
        "markers": ["CXXABI_TM_1"],
    }
    assert warnings == []


def test_a_stub_alone_lists_only_the_libraries_it_needs() -> None:
    libraries, warnings = system_report(STUB_2_28, SystemUse())

    assert list(libraries) == ["libc.so.6", "libm.so.6"]
    assert target_needs(libraries, "2.28") == "glibc >= 2.28" and warnings == []


def test_the_summary_line_names_what_is_not_glibc() -> None:
    libraries: dict[str, dict[str, object]] = {
        "libc.so.6": {"needed_by": [STUB], "versions": {"GLIBC": "2.28"}},
        "libgcc_s.so.1": {"needed_by": [EXT]},
        "libstdc++.so.6": {"needed_by": [EXT], "versions": {"CXXABI": "1.3.11",
                                                            "GLIBCXX": "3.4.22"}},
    }  # fmt: skip

    assert target_needs(libraries, "2.28") == (
        "glibc >= 2.28; libgcc_s.so.1; libstdc++.so.6 (CXXABI_1.3.11, GLIBCXX_3.4.22)"
    )
    assert target_needs({}, None) is None


def test_glibcxx_above_the_manylinux_2_28_floor_warns_naming_the_file() -> None:
    new = ("libstdc++.so.6", ("GLIBCXX_3.4.30", "CXXABI_1.3.13"))
    natives = {EXT: make_elf(needed=("libstdc++.so.6",), versions=(new,))}
    use, _ = _reach(natives)

    warnings = system_report(STUB_2_28, use)[1]

    assert len(warnings) == 2
    assert any(w.startswith(f"{EXT} needs GLIBCXX_3.4.30 from libstdc++.so.6, newer than "
                            "GLIBCXX_3.4.24") for w in warnings)  # fmt: skip
    assert any("CXXABI_1.3.13" in w and "CXXABI_1.3.11" in w for w in warnings)


def test_numpy_and_onnxruntime_needs_are_within_the_floors() -> None:
    new = ("libstdc++.so.6", ("GLIBCXX_3.4.22", "CXXABI_1.3.11", "CXXABI_TM_1"))
    use, _ = _reach({EXT: make_elf(needed=("libstdc++.so.6",), versions=(new,))})

    assert system_report(STUB_2_28, use)[1] == []


def test_glibc_private_warns() -> None:
    natives = {
        EXT: make_elf(
            needed=("libc.so.6",), versions=(("libc.so.6", ("GLIBC_2.17", "GLIBC_PRIVATE")),)
        )
    }
    use, _ = _reach(natives)

    warnings = system_report(STUB_2_28, use)[1]

    assert len(warnings) == 1 and warnings[0].startswith(f"{EXT} needs GLIBC_PRIVATE")


def test_other_files_over_the_floor_are_counted_in_one_warning() -> None:
    new = ("libstdc++.so.6", ("GLIBCXX_3.4.30",))
    natives = {EXT: make_elf(needed=("libstdc++.so.6",), versions=(new,)),
               "p/libb.so": make_elf(needed=("libstdc++.so.6",), versions=(new,))}  # fmt: skip
    use, _ = _reach(natives | {EXT: natives[EXT] + b"\0libb.so\0"})

    warnings = system_report(STUB_2_28, use)[1]

    assert len(warnings) == 1 and "and 1 more native file" in warnings[0]


def test_an_unreadable_version_table_warns_and_the_build_goes_on() -> None:
    data = bytearray(make_elf(needed=("libstdc++.so.6",), versions=(CXX,)))
    verneed = data.index(struct.pack("<HH", 1, len(CXX[1])))
    struct.pack_into("<I", data, verneed + 8, 0x7FFFFFF0)  # vn_aux far outside the file

    use, warnings = _reach({EXT: bytes(data)})

    assert use.needed_by == {"libstdc++.so.6": {EXT}} and EXT not in use.versions
    assert len(warnings) == 1 and warnings[0].startswith(f"{EXT}: symbol versions unreadable")
    assert system_report(STUB_2_28, use)[0]["libstdc++.so.6"] == {"needed_by": [EXT]}


def test_allowlist_entries_nothing_needs_get_a_warning() -> None:
    natives = {EXT: make_elf(needed=("libfoo.so.1",))}

    use, warnings = _reach(natives, frozenset({"libfoo.so.1", "libbar.so.2"}))

    assert use.allowed_used == {"libfoo.so.1"}
    assert len(warnings) == 1 and "libbar.so.2" in warnings[0]


@pytest.mark.parametrize("stub", [b"\x7fELF-fake", b""])
def test_an_unreadable_stub_adds_no_libraries(stub: bytes) -> None:
    assert system_report(stub, SystemUse()) == ({}, [])
