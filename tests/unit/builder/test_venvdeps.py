"""The venv rung of the dependency ladder (venvdeps.py): the distributions a script's venv holds
are read as files and become pins; nothing in the venv is run or copied."""

import json
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from fsbpyr.build import StrictError, infer_dependencies
from fsbpyr.closure import marker_environment
from fsbpyr.config import BuildConfig
from fsbpyr.loose import Package
from fsbpyr.manifest import inferred_lock
from fsbpyr.project import LockSource, Project, ProjectError
from fsbpyr.venvdeps import Inferred, find_venv, infer, read_venv

ENVIRONMENT = marker_environment("3.14.9")


def _venv(root: Path) -> Path:
    (root / "lib" / "python3.14" / "site-packages").mkdir(parents=True)
    (root / "pyvenv.cfg").write_text("home = /nowhere\n")
    return root


def _dist(
    venv: Path, name: str, version: str = "1.0", *, requires: Sequence[str] = (),
    files: Sequence[str] = (), top_level: str | None = None,
    direct_url: Mapping[str, object] | None = None,
) -> None:  # fmt: skip
    site = venv / "lib" / "python3.14" / "site-packages"
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir()
    lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    lines += [f"Requires-Dist: {line}" for line in requires]
    (info / "METADATA").write_text("\n".join(lines) + "\n\nlong description\n")
    (info / "RECORD").write_text("".join(f"{f},sha256=x,1\n" for f in files))
    if top_level is not None:
        (info / "top_level.txt").write_text(top_level)
    if direct_url is not None:
        (info / "direct_url.json").write_text(json.dumps(direct_url))


# --- finding the venv ------------------------------------------------------------------------


def test_a_venv_in_the_directory_is_found_in_the_order_dot_venv_venv_env(tmp_path: Path) -> None:
    _venv(tmp_path / "env")
    assert find_venv(tmp_path, {}) == tmp_path / "env"
    _venv(tmp_path / "venv")
    assert find_venv(tmp_path, {}) == tmp_path / "venv"
    _venv(tmp_path / ".venv")
    assert find_venv(tmp_path, {}) == tmp_path / ".venv"


def test_a_directory_without_pyvenv_cfg_is_no_venv_and_virtual_env_comes_last(
    tmp_path: Path,
) -> None:
    (tmp_path / ".venv").mkdir()
    active = _venv(tmp_path / "elsewhere")

    assert find_venv(tmp_path, {}) is None
    assert find_venv(tmp_path, {"VIRTUAL_ENV": str(active)}) == active
    _venv(tmp_path / "venv")
    assert find_venv(tmp_path, {"VIRTUAL_ENV": str(active)}) == tmp_path / "venv"
    assert find_venv(tmp_path, {"VIRTUAL_ENV": str(tmp_path / "gone")}) == tmp_path / "venv"


# --- mapping imports to distributions --------------------------------------------------------


def test_top_level_txt_and_record_say_which_distribution_provides_an_import(
    tmp_path: Path,
) -> None:
    venv = _venv(tmp_path)
    _dist(venv, "PyYAML", "6.0.2", top_level="yaml\n_yaml\n")
    _dist(venv, "attrs", "24.2.0", files=["attr/__init__.py", "attrs/__init__.py",
                                          "attrs-24.2.0.dist-info/METADATA", "../../../bin/x"])
    _dist(venv, "six", "1.16.0", files=["six.py", "six-1.16.0.dist-info/RECORD"])
    _dist(venv, "mod-with-ext", "1.0", files=["fast.cpython-314-x86_64-linux-gnu.so"])

    dists = read_venv(venv)

    assert dists["pyyaml"].imports == {"yaml", "_yaml"}
    assert dists["attrs"].imports == {"attr", "attrs"}
    assert dists["six"].imports == {"six"}
    assert dists["mod-with-ext"].imports == {"fast"}


def test_the_imports_that_a_distribution_provides_are_pinned_at_the_installed_version(
    tmp_path: Path,
) -> None:
    venv = _venv(tmp_path)
    _dist(venv, "Requests", "2.32.3", files=["requests/__init__.py"])
    _dist(venv, "idna", "3.10", files=["idna/__init__.py"])

    found = infer(venv, ["requests.adapters", "idna", "requests"], ENVIRONMENT)

    assert found == Inferred(venv, ("idna==3.10", "requests==2.32.3"))


def test_an_import_no_installed_distribution_lists_is_not_guessed_from_pypi(
    tmp_path: Path,
) -> None:
    venv = _venv(tmp_path)
    _dist(venv, "PyYAML", "6.0.2", files=["yaml/__init__.py"])
    _dist(venv, "scikit-learn", "1.5.0", files=["sklearn/__init__.py"])

    assert infer(venv, ["sklearn_nope", "bs4", "jwt"], ENVIRONMENT) is None
    assert infer(venv, [], ENVIRONMENT) is None


def test_a_namespace_shared_by_several_distributions_is_narrowed_by_record(
    tmp_path: Path,
) -> None:
    venv = _venv(tmp_path)
    _dist(venv, "google-auth", "2.0", files=["google/auth/__init__.py"], top_level="google\n")
    _dist(venv, "protobuf", "5.0", files=["google/protobuf/__init__.py"], top_level="google\n")

    found = infer(venv, ["google.protobuf.message"], ENVIRONMENT)

    assert found is not None and found.pins == ("protobuf==5.0",)
    both = infer(venv, ["google"], ENVIRONMENT)
    assert both is not None and both.pins == ("google-auth==2.0", "protobuf==5.0")


# --- the closure -----------------------------------------------------------------------------


def test_the_requires_dist_closure_as_installed_is_pinned_markers_for_the_target(
    tmp_path: Path,
) -> None:
    venv = _venv(tmp_path)
    _dist(venv, "app-lib", "1.0", files=["app_lib/__init__.py"], requires=[
        "dep-a>=1", 'dep-win; sys_platform == "win32"', "dep-opt; extra == 'fast'",
        'dep-new; python_version >= "3.10"', "not installed", "not a requirement !!"])
    _dist(venv, "dep-a", "2.5", files=["dep_a/__init__.py"], requires=["dep-b"])
    _dist(venv, "dep-b", "0.3", files=["dep_b/__init__.py"])
    _dist(venv, "dep-win", "9.9")
    _dist(venv, "dep-opt", "7.7")
    _dist(venv, "dep-new", "1.1")
    _dist(venv, "unrelated", "3.0", files=["unrelated/__init__.py"])

    found = infer(venv, ["app_lib"], ENVIRONMENT)

    assert found is not None
    assert found.pins == ("app-lib==1.0", "dep-a==2.5", "dep-b==0.3", "dep-new==1.1")


# --- the user's own trees --------------------------------------------------------------------


@pytest.mark.parametrize(
    "direct_url",
    [{"url": "file:///src/mine", "dir_info": {"editable": True}},
     {"url": "file:///src/mine", "dir_info": {}}],
)  # fmt: skip
def test_an_editable_or_path_install_that_is_needed_is_refused(
    tmp_path: Path, direct_url: dict[str, object]
) -> None:
    venv = _venv(tmp_path)
    _dist(venv, "mine", "0.1", direct_url=direct_url)

    with pytest.raises(ProjectError, match=r"mine 0\.1 in .* is an editable or path install"):
        infer(venv, ["mine"], ENVIRONMENT)


def test_an_editable_install_with_a_finder_file_is_refused_and_one_not_needed_is_not(
    tmp_path: Path,
) -> None:
    venv = _venv(tmp_path)
    _dist(venv, "mine", "0.1", files=["__editable__.mine-0.1.pth"])
    _dist(venv, "other", "1.0", files=["other/__init__.py"])
    _dist(venv, "needs-mine", "2.0", files=["needs_mine/__init__.py"], requires=["mine"])

    found = infer(venv, ["other"], ENVIRONMENT)
    assert found is not None and found.pins == ("other==1.0",)
    with pytest.raises(ProjectError, match="editable or path install"):
        infer(venv, ["needs_mine"], ENVIRONMENT)  # in the closure of what is needed


# --- the build step --------------------------------------------------------------------------


class _Runtime:
    python_version = "3.14.9"
    modules = ()


@pytest.fixture
def runtime_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsbpyr.build.runtime_module_names", lambda _: frozenset({"os", "sys"}))


YAML_TOOL = "import yaml\nif __name__ == '__main__':\n    print(yaml)\n"


def _script_dir(root: Path, text: str) -> Path:
    root.mkdir()
    (root / "tool.py").write_text(text)
    return root


def test_the_unresolved_imports_of_the_commands_pin_the_venv_versions_in_the_wheel(
    tmp_path: Path, runtime_modules: None
) -> None:
    directory = _script_dir(tmp_path / "ops", "import os\nimport yaml\nimport nope\n"
                            "if __name__ == '__main__':\n    print(yaml)\n")
    venv = _venv(directory / ".venv")
    _dist(venv, "PyYAML", "6.0.2", files=["yaml/__init__.py"])
    _dist(venv, "huge", "1.0", files=["huge/__init__.py"])
    warnings: list[str] = []

    package, inferred = infer_dependencies(Package.load(directory), Project.load(directory),
                                           BuildConfig(), _Runtime(), warnings)  # type: ignore[arg-type]  # fmt: skip

    assert inferred == Inferred(venv, ("pyyaml==6.0.2",))
    assert "Requires-Dist: pyyaml==6.0.2" in package.metadata
    assert "Requires-Dist: huge==1.0" not in package.metadata
    assert len(warnings) == 1
    assert ".venv were read as files (nothing is copied) and are installed fresh: pyyaml==6.0.2" \
        in warnings[0]
    assert "`uv pip freeze --python .venv/bin/python > requirements.txt` declares them" \
        in warnings[0]


def test_a_single_script_is_told_to_list_the_dependencies_in_its_block(
    tmp_path: Path, runtime_modules: None
) -> None:
    directory = tmp_path / "ops"
    directory.mkdir()
    script = directory / "tool.py"
    script.write_text("import yaml\nif __name__ == '__main__':\n    print(yaml)\n")
    _dist(_venv(directory / "venv"), "PyYAML", "6.0.2", files=["yaml/__init__.py"])
    warnings: list[str] = []

    _, inferred = infer_dependencies(Package.load(directory, script=script), Project.load(script),
                                     BuildConfig(), _Runtime(), warnings)  # type: ignore[arg-type]  # fmt: skip

    assert inferred is not None
    assert "`uv pip freeze --python venv/bin/python` lists them for the '# /// script' block" \
        in warnings[0]
    assert "requirements.txt" not in warnings[0]


def test_strict_fails_before_anything_is_staged(tmp_path: Path, runtime_modules: None) -> None:
    directory = _script_dir(tmp_path / "ops", YAML_TOOL)
    _dist(_venv(directory / ".venv"), "PyYAML", "6.0.2", files=["yaml/__init__.py"])

    with pytest.raises(StrictError, match=r"installed fresh: pyyaml==6\.0\.2"):
        infer_dependencies(Package.load(directory), Project.load(directory),
                           BuildConfig(strict=True), _Runtime(), [])  # type: ignore[arg-type]  # fmt: skip


@pytest.mark.parametrize("case", ["no venv", "nothing unresolved", "not provided"])
def test_without_a_venv_or_a_provided_import_nothing_is_inferred(
    tmp_path: Path, runtime_modules: None, case: str
) -> None:
    imports = "import os\n" if case == "nothing unresolved" else "import os\nimport yaml\n"
    directory = _script_dir(tmp_path / "ops", imports + "if __name__ == '__main__':\n    pass\n")
    if case != "no venv":
        _dist(_venv(directory / ".venv"), "other", "1.0", files=["other/__init__.py"])
    warnings: list[str] = []

    package, inferred = infer_dependencies(Package.load(directory), Project.load(directory),
                                           BuildConfig(), _Runtime(), warnings)  # type: ignore[arg-type]  # fmt: skip

    assert inferred is None and warnings == []
    assert not any(line.startswith("Requires-Dist") for line in package.metadata)


def test_a_venv_python_is_never_started(tmp_path: Path, runtime_modules: None) -> None:
    marker = tmp_path / "marker"
    directory = _script_dir(tmp_path / "ops", YAML_TOOL)
    venv = _venv(directory / ".venv")
    _dist(venv, "PyYAML", "6.0.2", files=["yaml/__init__.py"])
    python = venv / "bin" / "python"
    python.parent.mkdir()
    python.write_text(f"#!/bin/sh\ntouch {marker}\n")
    python.chmod(python.stat().st_mode | stat.S_IXUSR)

    _, inferred = infer_dependencies(Package.load(directory), Project.load(directory),
                                     BuildConfig(), _Runtime(), [])  # type: ignore[arg-type]  # fmt: skip

    assert inferred is not None and not marker.exists()


# --- the record ------------------------------------------------------------------------------


def test_the_lock_record_names_the_venv_and_the_pins_and_has_no_file_to_hash(
    tmp_path: Path,
) -> None:
    inferred = Inferred(tmp_path / "ops" / ".venv", ("a==1", "b==2"))

    assert inferred_lock(tmp_path / "ops", inferred) == {
        "kind": "inferred", "path": ".venv", "sha256": None, "hashed": False,
        "pins": ["a==1", "b==2"]}  # fmt: skip
    assert LockSource.INFERRED.value == "inferred"


def test_a_distribution_with_an_invalid_version_or_a_foreign_name_is_not_pinned(
    tmp_path: Path,
) -> None:
    venv = _venv(tmp_path / ".venv")
    _dist(venv, "evil", "1.0 ; python_version<'3'", files=["evil/__init__.py"])
    _dist(venv, "other", "1.0", files=["liar/__init__.py"])
    site = venv / "lib" / "python3.14" / "site-packages"
    (site / "other-1.0.dist-info").rename(site / "different-1.0.dist-info")

    assert read_venv(venv).keys() == set()


def test_the_lock_record_names_only_a_venv_outside_the_directory(tmp_path: Path) -> None:
    directory = tmp_path / "app"
    directory.mkdir()
    outside = tmp_path / "elsewhere" / ".venv"

    record = inferred_lock(directory, Inferred(outside, ("a==1",)))

    assert record["path"] == ".venv"
    assert inferred_lock(directory, Inferred(directory / ".venv", ("a==1",)))["path"] == ".venv"
