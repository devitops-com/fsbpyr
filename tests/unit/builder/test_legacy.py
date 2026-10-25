"""Legacy scripts of the project's wheel (research UC5 (d)): which ones are Python, the module
each is stored under, and the clashes with console scripts."""

import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from fsbpyr.build import check_before_staging
from fsbpyr.classify import _legacy_script_warnings
from fsbpyr.closure import ClosureError, InstalledDist
from fsbpyr.config import BuildConfig
from fsbpyr.legacy import attach as attach_to
from fsbpyr.legacy import wheel_scripts
from fsbpyr.project import Project


def attach(dist: InstalledDist, wheel: Path) -> InstalledDist:
    return attach_to(dist, wheel, [])


def _wheel(tmp_path: Path, scripts: dict[str, bytes]) -> Path:
    wheel = tmp_path / "app-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("app/__init__.py", "")
        for name, data in scripts.items():
            archive.writestr(f"app-1.0.data/scripts/{name}", data)
        archive.writestr("app-1.0.data/data/share/x", b"#!python\n")
    return wheel


def _dist(tmp_path: Path, entry_points: str = "") -> InstalledDist:
    site = tmp_path / "site"
    site.mkdir()
    return InstalledDist("app", "1.0", "app-1.0.dist-info", site, (), ("app/__init__.py,,",),
                         entry_points, None)  # fmt: skip


def test_only_files_that_start_with_the_python_shebang_are_python_scripts(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, {
        "tool": b"#!python\nprint(1)\n",
        "args": b"#!python -u\r\nprint(2)\n",
        "gui": b"#!pythonw\nprint(3)\n",
        "env": b"#!/usr/bin/env python\nprint(4)\n",
        "shell": b"#!/bin/sh\necho 5\n",
        "bare": b"#!python",
    })  # fmt: skip

    assert list(wheel_scripts(wheel)) == ["args", "bare", "tool"]


def test_a_legacy_script_becomes_a_command_below_the_scripts_root(tmp_path: Path) -> None:
    dist = attach(_dist(tmp_path), _wheel(tmp_path, {"tool": b"#!python\nprint(1)\n"}))

    assert dist.commands() == {"tool": "_fsbpyr_scripts._x_tool"}
    assert (dist.site / "_fsbpyr_scripts" / "_x_tool.py").read_bytes() == b"#!python\nprint(1)\n"
    assert "_fsbpyr_scripts/_x_tool.py,," in dist.record


def test_a_name_that_is_no_module_name_is_mangled_and_an_identifier_gets_a_prefix(
    tmp_path: Path,
) -> None:
    scripts = {"check-disk": b"#!python\n", "json": b"#!python\n", "run.py": b"#!python\n"}

    dist = attach(_dist(tmp_path), _wheel(tmp_path, scripts))

    assert dist.legacy == {"check-disk": "_fsbpyr_scripts.check_disk",
                           "json": "_fsbpyr_scripts._x_json", "run.py": "_fsbpyr_scripts.run"}


def test_legacy_scripts_join_the_console_scripts(tmp_path: Path) -> None:
    entry_points = "[console_scripts]\napp = app:main\n"

    dist = attach(_dist(tmp_path, entry_points), _wheel(tmp_path, {"tool": b"#!python\n"}))

    assert dist.commands() == {"app": "app:main", "tool": "_fsbpyr_scripts._x_tool"}


def test_a_name_shared_with_a_console_script_fails_naming_both(tmp_path: Path) -> None:
    dist = _dist(tmp_path, "[console_scripts]\ntool = app:main\n")

    with pytest.raises(ClosureError, match=r"'tool' is both a console script \(app:main\) and "
                                           r"a legacy script \(\.data/scripts/tool of app-1\.0"):
        attach(dist, _wheel(tmp_path, {"tool": b"#!python\n"}))


def test_two_names_that_give_one_module_fail_naming_both(tmp_path: Path) -> None:
    with pytest.raises(ClosureError, match=r"'a-b' and 'a-b\.py' are both _fsbpyr_scripts/a_b\.py"):
        attach(_dist(tmp_path), _wheel(tmp_path, {"a-b": b"#!python\n", "a-b.py": b"#!python\n"}))


def test_a_wheel_without_python_scripts_leaves_the_dist_alone(tmp_path: Path) -> None:
    dist = _dist(tmp_path)

    assert attach(dist, _wheel(tmp_path, {"shell": b"#!/bin/sh\n"})) is dist
    assert not (dist.site / "_fsbpyr_scripts").exists()


def test_the_warning_for_a_dropped_script_skips_the_packaged_ones(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    dist = attach(dist, _wheel(tmp_path, {"tool": b"#!python\n"}))
    dist = replace(dist, record=(*dist.record, "../../../bin/tool,,", "../../../bin/shell,,"))

    warnings = _legacy_script_warnings(dist)

    assert len(warnings) == 1
    assert "legacy script shell is not packaged (only a legacy script that starts with #!python is"\
        in warnings[0]


def test_setup_py_defers_the_missing_entry_check_to_the_wheel(tmp_path: Path) -> None:
    (tmp_path / "setup.py").write_text("")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1"\n')
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "tool").write_text("#!/usr/bin/env python\n")

    check_before_staging(Project.load(tmp_path), BuildConfig())


def test_script_files_of_setuptools_defer_the_missing_entry_check_to_the_wheel(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "1"\n[tool.setuptools]\nscript-files = ["bin/tool"]\n'
    )

    check_before_staging(Project.load(tmp_path), BuildConfig())


def test_a_script_that_does_not_compile_is_skipped_with_a_warning(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    warnings: list[str] = []

    result = attach_to(dist, _wheel(tmp_path, {"old": b"#!python\nprint 'hi'\n"}), warnings)

    assert result is dist
    assert len(warnings) == 1
    assert "legacy script old is not packaged: it does not compile" in warnings[0]
    assert not (dist.site / "_fsbpyr_scripts").exists()
