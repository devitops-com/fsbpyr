"""Licence notices of the libraries a runtime links (REP-4), from a made-up PBS distribution."""

import importlib.metadata
from pathlib import Path

import pytest

from fsbpyr.payload.format import FileKind
from fsbpyr.runtime import notices, pbs


def _dist(root: Path, shipped: set[str]) -> pbs.PbsDist:
    """_ssl and zlib built in (sources of LICENSE.openssl-3.txt, zstd and zlib-ng texts),
    _tkinter and _dbm not, and an inittab naming the first two."""
    (root / "licenses").mkdir(parents=True)
    for name in shipped:
        (root / "licenses" / name).write_text(f"text of {name}")
    (root / "inittab.c").write_text('{"_ssl", PyInit__ssl}, {"zlib", PyInit_zlib}, '
                                    '{"_hashlib", PyInit__hashlib}')  # fmt: skip
    extension = {"_ssl": [{"license_paths": ["licenses/LICENSE.openssl-3.txt"], "objs": [],
                           "links": []}],
                 "zlib": [{"license_paths": ["licenses/LICENSE.zlib-ng.txt",
                                             "licenses/LICENSE.zlib.txt",
                                             "licenses/LICENSE.zstd.txt"],
                           "objs": [], "links": []}],
                 "_hashlib": [{"objs": ["build/Modules/_hacl/Hacl_HMAC.o"], "links": []}],
                 "_dbm": [{"license_paths": ["licenses/LICENSE.bdb.txt"], "objs": [],
                           "links": []}]}  # fmt: skip
    return pbs.PbsDist(root, {
        "license_path": "licenses/LICENSE.cpython.txt",
        "build_info": {"inittab_source": "inittab.c", "extensions": extension},
    })  # fmt: skip


def test_the_texts_pbs_ships_for_what_is_built_in_are_collected(tmp_path: Path) -> None:
    shipped = {"LICENSE.cpython.txt", "LICENSE.openssl-3.txt", "LICENSE.zlib.txt",
               "LICENSE.bdb.txt"}  # fmt: skip

    result = notices.collect(_dist(tmp_path, shipped))

    assert result.texts["LICENSE.openssl-3.txt"] == b"text of LICENSE.openssl-3.txt"
    assert "LICENSE.bdb.txt" not in result.texts  # _dbm is not built in


def test_zstd_and_hacl_texts_are_supplied_where_pbs_ships_none(tmp_path: Path) -> None:
    result = notices.collect(_dist(tmp_path, {"LICENSE.cpython.txt", "LICENSE.openssl-3.txt",
                                              "LICENSE.zlib.txt"}))  # fmt: skip

    assert b"Zstandard" in result.texts["LICENSE.zstd.txt"]
    assert b"HACL* Contributors" in result.texts["LICENSE.hacl.txt"]
    assert result.unshipped == {"LICENSE.zstd.txt"}
    assert "LICENSE.zlib-ng.txt" not in result.texts  # libz.a is classic zlib


def test_zlib_ngs_text_is_left_out_also_when_pbs_ships_it(tmp_path: Path) -> None:
    # PBS ships it from 20261020 on, but libz.a is still classic zlib (LICENSE.zlib.txt)
    result = notices.collect(_dist(tmp_path, {"LICENSE.cpython.txt", "LICENSE.zlib.txt",
                                              "LICENSE.zlib-ng.txt"}))  # fmt: skip

    assert "LICENSE.zlib-ng.txt" not in result.texts
    assert result.texts["LICENSE.zlib.txt"] == b"text of LICENSE.zlib.txt"


def test_a_text_pbs_ships_is_not_replaced(tmp_path: Path) -> None:
    result = notices.collect(_dist(tmp_path, {"LICENSE.cpython.txt", "LICENSE.zstd.txt"}))

    assert result.texts["LICENSE.zstd.txt"] == b"text of LICENSE.zstd.txt"


def test_files_are_resources_under_the_notice_directory_with_fsbpyrs_license(
    tmp_path: Path,
) -> None:
    found = notices.files(_dist(tmp_path, {"LICENSE.cpython.txt"}))

    paths = [f.path for f in found]
    assert paths == sorted(paths)
    assert {f.kind for f in found} == {FileKind.RESOURCE}
    assert "_fsbpyr_licenses/LICENSE.fsbpyr.txt" in paths
    assert "_fsbpyr_licenses/LICENSE.hacl.txt" in paths


def test_fsbpyrs_license_is_the_repositorys() -> None:
    root = Path(__file__).resolve().parents[3]

    assert notices._own_license() == (root / "LICENSE").read_bytes()


def test_a_missing_license_is_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def absent(name: str) -> object:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", absent)
    monkeypatch.setattr(notices, "__file__", str(tmp_path / "a" / "b" / "c" / "notices.py"))

    with pytest.raises(pbs.PbsError, match="LICENSE"):
        notices._own_license()
