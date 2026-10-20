"""Smoke test and entry-point checks of the build orchestrator."""

import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

import pytest

from fsbpyr.build import (
    BuildError,
    StrictError,
    _provenance,
    best_compression_unused,
    build,
    builder_uv,
    built_here_above,
    check_strict,
    glibc_report,
    rebuild_on_baseline,
    smoke_run,
    smoke_test,
    uv_mismatch,
)
from fsbpyr.classify import Uncounted
from fsbpyr.cli import main
from fsbpyr.closure import SiteIndex
from fsbpyr.config import BuildConfig
from fsbpyr.manifest import SCHEMA
from fsbpyr.payload.format import FileKind
from fsbpyr.payload.spec import Blob, Dist, File
from fsbpyr.project import LockSource, Project
from fsbpyr.runtime import pins
from fsbpyr.runtime.sysroot import build_flags
from fsbpyr.staging import StagingError, StagingPlan

from ...support.elf import make_elf
from ..boot.conftest import FAKE_RUNTIME


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


def test_a_failed_smoke_test_names_the_output_not_the_new_file(tmp_path: Path) -> None:
    """The new file beside the output is gone by the time the error is read."""
    message = f"smoke test of the new {tmp_path / 'app'} failed (exit 3)"
    with pytest.raises(BuildError, match=re.escape(message)):
        smoke_test(_script(tmp_path / ".app.x.tmp", "exit 3"), "app", tmp_path / "app")


def test_smoke_run_passes_the_arguments_and_the_final_path_as_argv0() -> None:
    """The running file is the temporary one; the program sees the output's path."""
    check = "import sys; assert sys.orig_argv[0] == '/final/app' and sys.argv[1:] == ['x']"

    smoke_run(Path(sys.executable), Path("/final/app"), ("-c", check, "x"))


def test_a_failed_smoke_run_names_the_arguments_and_shows_stderr(tmp_path: Path) -> None:
    new = _script(tmp_path / ".app.x.tmp", "echo boom >&2; exit 2")

    with pytest.raises(BuildError, match=r"arguments '--version' failed \(exit 2\):\nboom"):
        smoke_run(new, tmp_path / "app", ("--version",))


def test_a_smoke_run_cannot_read_stdin(tmp_path: Path) -> None:
    new = _script(tmp_path / ".app.x.tmp", "cat >/dev/null")

    smoke_run(new, tmp_path / "app", ())


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


def _stand_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    """uv and the runtime: build() runs neither, and builds for the boot tests' stand-in."""
    monkeypatch.setattr("fsbpyr.build.find_uv", lambda: Path("uv"))
    monkeypatch.setattr("fsbpyr.build.uv_version", lambda uv: "0.13.1")
    monkeypatch.setattr("fsbpyr.build.select_runtime", lambda version: FAKE_RUNTIME)
    monkeypatch.setattr("fsbpyr.build.git_state", lambda directory, warn: None)


def test_an_error_carries_the_warnings_collected_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1"\n')
    _stand_ins(monkeypatch)

    def run_plan(plan: StagingPlan, project_dir: Path, **_: object) -> Path:
        raise StagingError("uv build failed")

    monkeypatch.setattr("fsbpyr.build.run_plan", run_plan)

    with pytest.raises(StagingError, match="uv build failed") as caught:
        build(tmp_path, BuildConfig())

    assert caught.value.__notes__ == ["unlocked build: dependencies resolved now, not reproducible"]


APP = {"app/__init__.py": "", "app/cli.py": "def main():\n    pass\n"}


def _staged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sources: dict[str, str]) -> Path:
    """A project whose staging installs ``sources`` as the distribution "app"; its directory."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1"\n')
    _stand_ins(monkeypatch)

    def run_plan(plan: StagingPlan, project_dir: Path, **_: object) -> Path:
        for rel in sources:
            (plan.site / rel).parent.mkdir(parents=True, exist_ok=True)
        _installed(plan.site, "app", "py3-none-any",
                   {rel: text.encode() for rel, text in sources.items()})  # fmt: skip
        return plan.wheel_dir / "app-1-py3-none-any.whl"

    monkeypatch.setattr("fsbpyr.build.run_plan", run_plan)
    return project


def _existing(path: Path) -> Path:
    path.parent.mkdir()
    path.write_bytes(b"the working tool")
    return path


@pytest.mark.parametrize("package", ["app", "app.plugins"])  # app.plugins: a namespace
def test_a_package_entry_without_main_fails_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, package: str
) -> None:
    """Running a package runs its __main__ module, as `python -m app` does."""
    project = _staged(tmp_path, monkeypatch, {**APP, "app/plugins/one.py": ""})
    out = _existing(tmp_path / "bin" / "app")

    message = f"'{package}' is a package without '{package}.__main__'"
    with pytest.raises(BuildError, match=re.escape(message)):
        build(project, BuildConfig(entry=package, output=out))

    assert out.read_bytes() == b"the working tool"


def test_a_package_entry_with_main_builds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _staged(tmp_path, monkeypatch, {**APP, "app/__main__.py": "print('main')\n"})
    out = tmp_path / "bin" / "app"

    result = build(project, BuildConfig(entry="app", output=out, smoke=False))

    assert (result.output, result.entrypoint) == (out, "app")
    assert sorted(p.name for p in out.parent.iterdir()) == ["app"]


def test_the_output_is_replaced_once_the_smoke_test_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _staged(tmp_path, monkeypatch, APP)
    out = _existing(tmp_path / "bin" / "app")
    tested: list[str] = []
    monkeypatch.setattr("fsbpyr.build.smoke_test",
                        lambda executable, *_: tested.append(executable.name))  # fmt: skip

    result = build(project, BuildConfig(entry="app.cli:main", output=out))

    [name] = tested  # the new executable, beside the output
    assert name.startswith(".app.") and name.endswith(".tmp")
    assert out.read_bytes()[:4] == b"\x7fELF" and result.size == out.stat().st_size
    assert sorted(p.name for p in out.parent.iterdir()) == ["app"]


@pytest.mark.parametrize("failure", [BuildError("smoke test failed"), KeyboardInterrupt()],
                         ids=["smoke test", "interrupt"])  # fmt: skip
def test_a_failure_between_the_write_and_the_commit_keeps_the_old_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    project = _staged(tmp_path, monkeypatch, APP)
    out = _existing(tmp_path / "bin" / "app")
    written: list[str] = []

    def smoke_test(executable: Path, app_name: str, output: Path) -> None:
        written.extend(p.name for p in out.parent.iterdir() if p != out)
        raise failure

    monkeypatch.setattr("fsbpyr.build.smoke_test", smoke_test)

    with pytest.raises(type(failure)):
        build(project, BuildConfig(entry="app.cli:main", output=out))

    assert len(written) == 1  # the new executable was written
    assert out.read_bytes() == b"the working tool"
    assert sorted(p.name for p in out.parent.iterdir()) == ["app"]  # and removed


def test_an_interrupt_while_the_staging_is_removed_keeps_the_old_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staged files are removed after the write: that can take long, and be interrupted."""
    project = _staged(tmp_path, monkeypatch, APP)
    out = _existing(tmp_path / "bin" / "app")
    remove = tempfile.TemporaryDirectory.cleanup

    def cleanup(self: tempfile.TemporaryDirectory[str]) -> None:
        remove(self)
        if len(list(out.parent.iterdir())) > 1:  # the new executable was written
            raise KeyboardInterrupt

    monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", cleanup)

    with pytest.raises(KeyboardInterrupt):
        build(project, BuildConfig(entry="app.cli:main", output=out))

    assert out.read_bytes() == b"the working tool"
    assert sorted(p.name for p in out.parent.iterdir()) == ["app"]


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
    wheel = tmp_path / "app-1.0-py3-none-any.whl"
    return rebuild_on_baseline(plan, tmp_path, wheel, ["cxx-ext"], BASELINE, offline=offline)


def test_a_failed_rebuild_keeps_the_hosts_build_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failed(*_: object, **__: object) -> None:
        raise StagingError("uv pip install ... failed:\n  error: command 'g++' failed\n\n")

    monkeypatch.setattr("fsbpyr.build.fetch_sysroot", lambda pin, cache: tmp_path)
    monkeypatch.setattr("fsbpyr.build.rebuild", failed)

    assert _rebuild(tmp_path) == [
        "cxx-ext: building from source against the glibc 2.28 sysroot failed, the build host's "
        "builds are packaged: error: command 'g++' failed"
    ]


@pytest.mark.parametrize(("names", "rebuilt"), [(["cext", "posix-ipc"], True),
                                                (["posix-ipc"], False)])  # fmt: skip
def test_the_projects_own_extension_is_rebuilt_with_the_sysroot_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: list[str], rebuilt: bool
) -> None:
    """The project's wheel is built on the build host too: when it needs more than the baseline,
    it is built again with the sysroot's flags, and that wheel is installed."""
    first = tmp_path / "wheel" / "cext-1.0-cp314-cp314-linux_x86_64.whl"
    again = tmp_path / "wheel-again" / first.name
    built: list[tuple[Path, dict[str, str]]] = []
    installed: list[tuple[Path, list[str], dict[str, str]]] = []

    def rebuild_wheel(plan: StagingPlan, project_dir: Path, env: dict[str, str],
                      **_: object) -> Path:  # fmt: skip
        built.append((project_dir, env))
        return again

    def rebuild(plan: StagingPlan, project_dir: Path, wheel: Path, names: list[str],
                env: dict[str, str], **_: object) -> None:  # fmt: skip
        installed.append((wheel, names, env))

    monkeypatch.setattr("fsbpyr.build.fetch_sysroot", lambda pin, cache: tmp_path / "sysroot")
    monkeypatch.setattr("fsbpyr.build.rebuild_wheel", rebuild_wheel)
    monkeypatch.setattr("fsbpyr.build.rebuild", rebuild)
    plan = StagingPlan(site=tmp_path, build=[], install=[], wheel_dir=first.parent)

    assert rebuild_on_baseline(plan, tmp_path, first, names, BASELINE, offline=False) == []

    flags = build_flags(tmp_path / "sysroot", os.environ)
    assert built == ([(tmp_path, flags)] if rebuilt else [])
    assert installed == [(again if rebuilt else first, names, flags)]


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


def _file(kind: FileKind) -> File:
    return File(f"pkg/{kind.name.lower()}", kind, Blob.zstd(b"x"))


def test_best_compression_with_no_native_files_is_a_warning() -> None:
    """It compresses native files only: a pure-Python app gains nothing from it."""
    files = [_file(FileKind.RESOURCE), _file(FileKind.SOURCE), _file(FileKind.METADATA)]

    [warning] = best_compression_unused(best_compression=True, files=files)

    assert warning.startswith("best-compression has nothing to compress")


@pytest.mark.parametrize(("best", "kind"), [(True, FileKind.NATIVE_EXT),
                                            (True, FileKind.NATIVE_LIB),
                                            (False, FileKind.RESOURCE)])  # fmt: skip
def test_best_compression_of_native_files_or_none_asked_is_no_warning(
    best: bool, kind: FileKind
) -> None:
    files = [_file(FileKind.RESOURCE), _file(kind)]

    assert best_compression_unused(best_compression=best, files=files) == []


def _locked(project: Path) -> None:
    (project / "requirements.txt").write_text("dep==1 --hash=sha256:00\n")


def test_strict_refuses_an_unlocked_build_and_leaves_the_output_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _staged(tmp_path, monkeypatch, APP)
    out = _existing(tmp_path / "bin" / "app")

    with pytest.raises(StrictError, match=r"has 1 warning; .*\n  unlocked build") as caught:
        build(project, BuildConfig(entry="app.cli:main", output=out, strict=True))

    assert not getattr(caught.value, "__notes__", [])  # the message lists them
    assert out.read_bytes() == b"the working tool"
    assert sorted(p.name for p in out.parent.iterdir()) == ["app"]
    build(project, BuildConfig(entry="app.cli:main", output=out, smoke=False))  # not strict
    assert out.read_bytes()[:4] == b"\x7fELF"


def test_strict_does_not_count_the_build_hosts_uv_or_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _staged(tmp_path, monkeypatch, APP)
    _locked(project)
    monkeypatch.setattr("fsbpyr.build.builder_uv", lambda: "0.12.0")  # staged uv is 0.13.1
    monkeypatch.setattr("fsbpyr.build.git_state",
                        lambda directory, warn: warn("git: boom; no commit recorded"))  # fmt: skip

    result = build(project, BuildConfig(entry="app.cli:main", output=tmp_path / "app",
                                        strict=True, smoke=False))  # fmt: skip

    assert any(w.startswith("uv 0.13.1 is not uv 0.12.0") for w in result.warnings)
    assert any(w.startswith("git: boom") for w in result.warnings)


def test_strict_counts_every_other_warning() -> None:
    host = [Uncounted("git: boom"), Uncounted("libx loaded by no extension")]

    check_strict(host)  # nothing counts
    with pytest.raises(StrictError, match=r"has 2 warnings; [^\n]*\n  a\n  b$"):
        check_strict(["a", host[0], "b"])


def test_requirements_without_pins_are_a_warning_even_when_not_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _staged(tmp_path, monkeypatch, APP)
    (project / "requirements.txt").write_text("dep>=1\n")

    result = build(project, BuildConfig(entry="app.cli:main", output=tmp_path / "app",
                                        smoke=False))  # fmt: skip

    assert any("not pinned with ==" in w and "dep>=1" in w for w in result.warnings)

def test_dists_built_here_from_sdists_are_named_in_one_line_that_is_no_warning(
    tmp_path: Path,
) -> None:
    lock = (
        'version = 1\n[[package]]\nname = "pure"\nversion = "1.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        'sdist = { url = "https://h/pure-1.0.tar.gz", hash = "sha256:' + "a" * 64 + '" }\n'
        'wheels = [{ url = "https://h/pure-1.0-py3-none-any.whl", hash = "sha256:'
        + "b" * 64
        + '" }]\n'
    )
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1"\n')
    (tmp_path / "uv.lock").write_text(lock)
    site = tmp_path / "site"
    info = site / "pure-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Name: pure\nVersion: 1.0\n")
    (info / "WHEEL").write_text("Generator: hatchling 1.0\nTag: py2-none-any\n")
    project = Project.load(tmp_path)
    lines: list[str] = []

    entries = dict(_provenance(project, LockSource.UV_LOCK, BuildConfig(), SiteIndex.scan(site),
                               [Dist("pure", info.name, "1.0")], project_dist="app",
                               log=lines.append))  # fmt: skip

    assert lines == ["built on this host from sdists, not reproducible elsewhere: pure"]
    assert entries["schema"] == SCHEMA == 1
    assert entries["dists"][0]["backend"] == "hatchling 1.0"  # type: ignore[index]
    assert set(entries) == {"schema", "lock", "dists", "settings"}
