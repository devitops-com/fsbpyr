"""The launcher coverage report of full-suite.yml: with FSBPYR_LLVM_COV_DIR set,
LauncherBuild.compile instruments the test programs, and llvm-cov reads their profiles together.
Each session, and each runtime variant, compiles a copy of the launcher sources of its own:
named by their place in the repository, every copy's code counts as that one file, and the tests'
own sources, which the report leaves out, are no row of it."""

import dataclasses
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from fsbpyr.runtime import stub

from ..support import requires
from .conftest import LauncherBuild, keep_stub

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parents[2]
PROBE = ROOT / "tests" / "probes" / "find_object.c"
WORKFLOW = ROOT / ".github" / "workflows" / "full-suite.yml"


def test_the_sanitized_stub_is_kept_where_nothing_was_kept_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """full-suite.yml names a directory that does not exist yet, and the `runtime` fixture can keep
    the sanitized stub before any LauncherBuild.compile (whose own keeping the next test runs): in
    the run of 2026-11-14 it did, on every job, and the write failed."""
    named = tmp_path / "llvm-cov" / "bin"
    runtime_id = "cp314-x86_64-unknown-linux-gnu-0123456789ab-sanitized"
    monkeypatch.setenv("FSBPYR_LLVM_COV_DIR", os.fspath(named))

    keep_stub(runtime_id, b"a stub")

    assert (named / f"stub-{runtime_id}").read_bytes() == b"a stub"
    monkeypatch.delenv("FSBPYR_LLVM_COV_DIR")
    keep_stub("another", b"another stub")
    assert sorted(path.name for path in named.iterdir()) == [f"stub-{runtime_id}"]


def test_the_report_has_one_row_per_launcher_source_and_none_of_the_tests(
    launcher_build: LauncherBuild, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llvm = launcher_build.clang.parent  # the instrumenting clang's tools read its profiles
    if not all((llvm / name).is_file() for name in ("llvm-profdata", "llvm-cov")):
        requires.unmet(f"no llvm-profdata and llvm-cov beside {launcher_build.clang}",
                       "use LLVM 23's (bash tools/bootstrap.sh --llvm23)")  # fmt: skip
    monkeypatch.setenv("FSBPYR_LLVM_COV_DIR", os.fspath(tmp_path / "kept"))
    monkeypatch.setenv("LLVM_PROFILE_FILE", f"{tmp_path}/%p.profraw")
    wraps = ("-Wl,--wrap=_dl_find_object", "-Wl,--wrap=dlvsym")
    programs = []
    for copy in ("one", "other"):  # as two sessions' copies of the sources
        build = dataclasses.replace(launcher_build, sources=tmp_path / copy / "launcher")
        stub._copy_launcher(build.sources)
        programs.append(build.compile(tmp_path / copy / "find_object", [PROBE, "glibc.c"], *wraps))
        subprocess.run([programs[-1]], capture_output=True, check=True)
    profile = tmp_path / "all.profdata"
    subprocess.run([llvm / "llvm-profdata", "merge", "-sparse", *tmp_path.glob("*.profraw"),
                    "-o", profile], check=True)  # fmt: skip
    (ignored,) = set(re.findall(r'ignored="-ignore-filename-regex=([^"]+)"', WORKFLOW.read_text()))

    export = subprocess.run([llvm / "llvm-cov", "export", "-summary-only", programs[0],
                             "-object", programs[1], f"-instr-profile={profile}",
                             f"-ignore-filename-regex={ignored}"],
                            capture_output=True, text=True, check=True).stdout  # fmt: skip

    files = [file["filename"] for file in json.loads(export)["data"][0]["files"]]
    assert files == [os.fspath(ROOT / "src" / "fsbpyr" / "launcher" / "glibc.c")]
