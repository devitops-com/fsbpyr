"""Smoke test and entry-point checks of the build orchestrator."""

import json
import sys
import types
from pathlib import Path

import pytest

from fsbpyr.build import (
    BuildError,
    build,
    builder_uv,
    built_here_above,
    glibc_report,
    rebuild_on_baseline,
    smoke_test,
    uv_mismatch,
)
from fsbpyr.cli import main
from fsbpyr.closure import SiteIndex
from fsbpyr.config import BuildConfig
from fsbpyr.runtime import pins
from fsbpyr.staging import StagingError, StagingPlan

from ...support.elf import make_elf


def _script(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def test_smoke_test_accepts_selftest_output(tmp_path: Path) -> None:
    executable = _script(
        tmp_path / "app", 'test "$FSBPYR_SELFTEST" = 1 && echo "fsbpyr selftest ok: app"'
    )

    smoke_test(executable, "app")


@pytest.mark.parametrize(
    "body", ["exit 3", "echo something else", "echo 'fsbpyr selftest ok: other'"]
)
def test_smoke_test_rejects_failures(tmp_path: Path, body: str) -> None:
    with pytest.raises(BuildError, match="smoke test"):
        smoke_test(_script(tmp_path / "app", body), "app")


def test_build_without_project_files_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["build", str(tmp_path)]) == 1
    assert "has no pyproject.toml or setup.py" in capsys.readouterr().err


def test_builds_without_uv_fail_before_looking_for_a_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1"\n')
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("fsbpyr.build.select_runtime", lambda version: pytest.fail("runtime"))

    with pytest.raises(StagingError, match="needs uv"):
        build(tmp_path, BuildConfig())


def test_output_must_not_be_a_directory(tmp_path: Path) -> None:
    from fsbpyr.build import check_output  # noqa: PLC0415

    with pytest.raises(BuildError, match="is a directory"):
        check_output(tmp_path)
    check_output(tmp_path / "new")


STUB_2_25 = make_elf(versions=(("libc.so.6", ("GLIBC_2.2.5", "GLIBC_2.25")),))


@pytest.mark.parametrize(
    ("natives", "expected", "warns"),
    [
        (None, "2.25", False),
        (((2, 17), "a/_x.so"), "2.25", False),
        (((2, 28), "a/_x.so"), "2.28", False),  # within the baseline the wheels were chosen for
        (((2, 34), "a/_new.so"), "2.34", True),
    ],
)
def test_glibc_requirement_is_the_highest_of_stub_and_natives(
    natives: tuple[tuple[int, int], str] | None, expected: str, warns: bool
) -> None:
    requirement, warnings = glibc_report(STUB_2_25, natives, baseline=(2, 28))

    assert requirement == expected
    assert bool(warnings) is warns
    assert not warnings or "a/_new.so needs glibc 2.34, more than the 2.28 baseline" in warnings[0]


def test_glibc_requirement_of_an_unreadable_stub_is_unknown() -> None:
    assert glibc_report(b"\x7fELF-fake", None, baseline=(2, 28)) == (None, [])


def _installed(site: Path, name: str, tag: str, files: dict[str, bytes]) -> None:
    info = site / f"{name}-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.4\nName: {name}\nVersion: 1.0\n")
    (info / "WHEEL").write_text(f"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: {tag}\n")
    for rel, data in files.items():
        (site / rel).write_bytes(data)
    lines = [f"{rel},," for rel in files] + [f"{info.name}/{f},," for f in ("METADATA", "WHEEL")]
    (info / "RECORD").write_text("\n".join([*lines, f"{info.name}/RECORD,,"]) + "\n")


def test_dependencies_built_here_that_need_more_than_the_baseline_are_found(tmp_path: Path) -> None:
    new = make_elf(versions=(("libc.so.6", ("GLIBC_2.2.5", "GLIBC_2.34")),))
    old = make_elf(versions=(("libc.so.6", ("GLIBC_2.17",)),))
    _installed(tmp_path, "native_src", "cp314-cp314-linux_x86_64", {"native_src.so": new})
    _installed(tmp_path, "native_wheel", "cp314-cp314-manylinux_2_34_x86_64",
               {"native_wheel.so": new})  # fmt: skip
    _installed(tmp_path, "old_src", "cp314-cp314-linux_x86_64", {"old_src.so": old})
    _installed(tmp_path, "pure_src", "py3-none-any", {"pure_src.py": b"x = 1\n"})

    found = built_here_above(SiteIndex.scan(tmp_path).dists.values(), (2, 28))

    assert found == ["native-src"]  # a wheel from the index got its glibc from its tag


BASELINE = pins.sysroot_for("x86_64-unknown-linux-gnu")


def _rebuild(tmp_path: Path, *, offline: bool = False) -> list[str]:
    plan = StagingPlan(site=tmp_path, build=[], install=[], wheel_dir=tmp_path)
    return rebuild_on_baseline(plan, tmp_path, tmp_path / "app.whl", ["cxx-ext"], BASELINE,
                               offline=offline)  # fmt: skip


def test_a_failed_rebuild_keeps_the_hosts_build_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failed(*_: object) -> None:
        raise StagingError("uv pip install ... failed:\n  error: command 'g++' failed\n\n")

    monkeypatch.setattr("fsbpyr.build.fetch_sysroot", lambda pin, cache: tmp_path)
    monkeypatch.setattr("fsbpyr.build.rebuild", failed)

    assert _rebuild(tmp_path) == [
        "cxx-ext: building from source against the glibc 2.28 sysroot failed, the build host's "
        "builds are packaged: error: command 'g++' failed"
    ]


def test_an_offline_build_rebuilds_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsbpyr.build.fetch_sysroot", lambda pin, cache: pytest.fail("fetch"))
    monkeypatch.setattr("fsbpyr.build.rebuild", lambda *_: pytest.fail("rebuild"))

    assert _rebuild(tmp_path, offline=True) == [
        "cxx-ext: not rebuilt against the glibc 2.28 sysroot: the build is offline"
    ]


def test_a_uv_other_than_the_one_that_built_fsbpyr_is_a_warning() -> None:
    [warning] = uv_mismatch(builder="0.13.1", current="0.14.0")

    assert "uv 0.14.0" in warning and "uv 0.13.1" in warning and "identical" in warning


@pytest.mark.parametrize("builder", ["0.13.1", None])  # None: from source, or an older build
def test_the_same_uv_or_an_unknown_one_is_no_warning(builder: str | None) -> None:
    assert uv_mismatch(builder=builder, current="0.13.1") == []


def test_an_executable_knows_the_uv_it_was_built_with(monkeypatch: pytest.MonkeyPatch) -> None:
    info = {"build_info": json.dumps({"uv": "0.13.1"})}
    monkeypatch.setitem(sys.modules, "_fsbpyr", types.SimpleNamespace(info=lambda: info))

    assert builder_uv() == "0.13.1"


def test_fsbpyr_run_from_source_knows_no_builder_uv() -> None:
    assert builder_uv() is None
