"""The scripts root appears next to the executable (owner decision 2026-10-25, D24): a module
of ``_fsbpyr_scripts/`` has ``__file__ == <dir of exe>/<path below the root>``, its code is still
the payload's, and the path hook claims those directories."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from fsbpyr import bytecode
from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import ModuleKind
from fsbpyr.payload.spec import Blob, Module

from .conftest import FAKE_RUNTIME

SOURCE = "VALUE = 'x'\n"
MODULES = {  # payload name -> (path, kind)
    "_fsbpyr_scripts": ("_fsbpyr_scripts", ModuleKind.NAMESPACE),
    "_fsbpyr_scripts.tool": ("_fsbpyr_scripts/tool.py", ModuleKind.MODULE),
    "_fsbpyr_scripts.pylib": ("_fsbpyr_scripts/pylib", ModuleKind.NAMESPACE),
    "_fsbpyr_scripts.pylib.plug": ("_fsbpyr_scripts/pylib/plug.py", ModuleKind.MODULE),
    "_fsbpyr_scripts.sub_dir": ("_fsbpyr_scripts/sub_dir", ModuleKind.NAMESPACE),
    "_fsbpyr_scripts.sub_dir.x": ("_fsbpyr_scripts/sub_dir/x.py", ModuleKind.MODULE),
    "pkg": ("pkg/__init__.py", ModuleKind.PACKAGE),
}


@pytest.fixture(scope="module")
def executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    code = Blob.code(bytecode.dumps(compile(SOURCE, "m.py", "exec", dont_inherit=True)))
    source = Blob.zstd(SOURCE.encode())
    modules = tuple(
        Module(name, path, kind, code=code, source=source)
        if kind != ModuleKind.NAMESPACE else Module(name, path, kind)
        for name, (path, kind) in MODULES.items()
    )  # fmt: skip
    out = tmp_path_factory.mktemp("overlay") / "app"
    build_executable(FAKE_RUNTIME, App("app", "_fsbpyr_scripts.tool", modules), out)
    return out


def _bin(boot: ModuleType) -> str:
    directory: str = boot.ROOT.rpartition("/")[0]
    return directory


def test_a_scripts_module_says_it_lives_next_to_the_executable(boot: ModuleType) -> None:
    directory = _bin(boot)
    spec = boot.path_hook(boot.ROOT + "/_fsbpyr_scripts").find_spec("_fsbpyr_scripts.tool")

    assert spec.origin == f"{directory}/tool.py"
    assert spec.loader.get_filename("_fsbpyr_scripts.tool") == f"{directory}/tool.py"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.__file__ == f"{directory}/tool.py" and module.VALUE == "x"
    assert spec.loader.get_code("_fsbpyr_scripts.tool").co_filename == (
        f"{boot.ROOT}/_fsbpyr_scripts/tool.py")  # tracebacks show the payload's source


def test_a_module_below_a_directory_of_the_root_keeps_the_directory(boot: ModuleType) -> None:
    spec = boot.path_hook(boot.ROOT + "/_fsbpyr_scripts/pylib").find_spec("plug")

    assert spec.origin == f"{_bin(boot)}/pylib/plug.py"


def test_a_packaged_module_is_where_it_always_was(boot: ModuleType) -> None:
    assert boot.path_hook(boot.ROOT).find_spec("pkg").origin == f"{boot.ROOT}/pkg/__init__.py"


def test_the_path_hook_claims_the_directories_next_to_the_executable(boot: ModuleType) -> None:
    directory = _bin(boot)

    assert boot.path_hook(directory).directory == "_fsbpyr_scripts"
    assert boot.path_hook(directory + "/").directory == "_fsbpyr_scripts"
    assert boot.path_hook(f"{directory}/pylib").directory == "_fsbpyr_scripts/pylib"
    assert boot.path_hook(f"{directory}/x/../pylib/.").directory == "_fsbpyr_scripts/pylib"
    sub = boot.path_hook(f"{directory}/sub-dir")  # the entry is the on-disk name, as in CPython
    assert sub.directory == "_fsbpyr_scripts/sub-dir" and sub._prefix == "_fsbpyr_scripts.sub_dir."
    assert boot.path_hook(f"{directory}/pylib").find_spec("plug").origin == (
        f"{directory}/pylib/plug.py")
    for foreign in (f"{directory}/other", f"{directory}/pylib/../..", f"{directory}/tool.py",
                    directory + "x"):  # fmt: skip
        with pytest.raises(ImportError):
            boot.path_hook(foreign)


def test_a_project_without_a_scripts_root_claims_nothing_beside_the_executable(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(boot._fsbpyr, "find_module", lambda name: None)

    with pytest.raises(ImportError):
        boot.path_hook(_bin(boot))


def test_the_directory_of_a_script_comes_first_on_sys_path_where_file_says(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", ["x"])

    boot._script_directory("_fsbpyr_scripts.tool")
    boot._script_directory("_fsbpyr_scripts.sub_dir.x")
    boot._script_directory("pkg.cli:main")

    assert sys.path == [f"{_bin(boot)}/sub_dir", _bin(boot), "x"]


def test_a_missing_file_next_to_the_executable_is_explained(boot: ModuleType) -> None:
    path = f"{_bin(boot)}/config.ini"

    note = boot._inside_executable_note(FileNotFoundError(2, "No such file", path))

    assert note is not None and f"{path} is missing" in note and "deploy it there" in note
    assert boot._inside_executable_note(FileNotFoundError(2, "No such file", "/etc/x")) is None
    assert boot._inside_executable_note(PermissionError(13, "denied", path)) is None
    other = FileNotFoundError(2, "No such file", f"{_bin(boot)}/elsewhere/x")
    assert boot._inside_executable_note(other) is None  # no directory of the scripts root


def test_get_data_looks_up_an_overlay_path_in_the_payload(
    boot: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    looked_up: list[str] = []
    monkeypatch.setattr(boot._fsbpyr, "find_file", looked_up.append)
    spec = boot.path_hook(boot.ROOT + "/_fsbpyr_scripts").find_spec("_fsbpyr_scripts.tool")

    for path in (f"{_bin(boot)}/sub/x.dat", f"{boot.ROOT}/_fsbpyr_scripts/sub/x.dat"):
        with pytest.raises(FileNotFoundError):
            spec.loader.get_data(path)

    assert looked_up == ["_fsbpyr_scripts/sub/x.dat"] * 2


def test_the_note_for_a_payload_path_of_the_scripts_root_says_where_it_is_read(
    boot: ModuleType,
) -> None:
    note = boot._note_for(f"{boot.ROOT}/_fsbpyr_scripts/sub/x.conf")

    assert f"from {_bin(boot)}/sub/x.conf" in note and "--fs-package" not in note
