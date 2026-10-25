"""_fsbpyr_rt.reexec: ``-m``, ``-c`` and payload scripts for children of the app itself: run
with interpreter-reexec (D23), refused without the setting (D44), the app with ``false``."""

import importlib
import json
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from .conftest import BOOT_DIR

EXE = "/opt/app/bin/tool"
PREFIX = EXE + "/"
ON = json.dumps({"interpreter_reexec": True})
OFF = json.dumps({"interpreter_reexec": False})
UNSET = json.dumps({"interpreter_reexec": None})
LOKY_TRACKER = "from joblib.externals.loky.backend.resource_tracker import main; main(6, False)"


def _runtime_modules() -> list[str]:
    return [n for n in sys.modules if n == "_fsbpyr_rt" or n.startswith("_fsbpyr_rt.")]


@pytest.fixture
def reexec(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.syspath_prepend(str(BOOT_DIR))
    for name in _runtime_modules():
        monkeypatch.delitem(sys.modules, name)
    yield importlib.import_module("_fsbpyr_rt.reexec")
    for name in _runtime_modules():
        del sys.modules[name]


@pytest.fixture
def child(reexec: ModuleType, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """A child the app started (pytest's parent is not pytest), with a __main__ of its own."""
    monkeypatch.setattr(reexec, "_parent_runs_this_executable", lambda: True)
    main = types.ModuleType("__main__")
    monkeypatch.setitem(sys.modules, "__main__", main)
    return main


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([EXE, "-u", "-B", "-m", "pkg.tool", "a", "-b"], ("-m", "pkg.tool", ["a", "-b"])),
        ([EXE, "-X", "dev", "-W", "ignore", "-c", "pass", "x"], ("-c", "pass", ["x"])),
        ([EXE, "-I", "-c", "pass"], ("-c", "pass", [])),
    ],
)
def test_module_or_code_after_interpreter_flags_is_recognized(
    reexec: ModuleType, argv: list[str], expected: tuple[str, str, list[str]]
) -> None:
    assert reexec.child_command(argv) == expected


@pytest.mark.parametrize(
    "argv",
    [[EXE], [EXE, "-m"], [EXE, "-q"], [EXE, "build", "-m", "x"], [EXE, "-", "-c", "pass"]],
)
def test_other_command_lines_belong_to_the_app(reexec: ModuleType, argv: list[str]) -> None:
    assert reexec.child_command(argv) is None


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([EXE, "-B", f"{PREFIX}pkg/tool.py", "x"], ("script", f"{PREFIX}pkg/tool.py", ["x"])),
        ([EXE, f"{PREFIX}pkg/__main__.py"], ("script", f"{PREFIX}pkg/__main__.py", [])),
        ([EXE, "/elsewhere/tool.py"], None),  # not the payload's
        ([EXE, f"{PREFIX}pkg/data.json"], None),
    ],
)
def test_a_script_of_the_payload_is_recognized(
    reexec: ModuleType, argv: list[str], expected: tuple[str, str, list[str]] | None
) -> None:
    assert reexec.child_command(argv, PREFIX) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (("-m", "joblib.externals.loky.backend.popen_loky_posix", []), True),
        (("-m", "ipykernel_launcher", ["-f", "k.json"]), True),
        (("-m", "two words", []), False),
        (("-c", LOKY_TRACKER, []), True),
        (("-c", "exit(4)", []), True),
        (("-c", "pass", []), True),
        (("-c", "config.toml", []), False),  # the app's own options
        (("-c", "production", []), False),
        (("-c", "3", []), False),
        (("-c", "a b", []), False),
        (("script", f"{PREFIX}cpuinfo/cpuinfo.py", ["--json"]), True),
    ],
)
def test_a_child_asks_for_python_with_a_module_name_code_or_a_payload_script(
    reexec: ModuleType, command: tuple[str, str, list[str]], expected: bool
) -> None:
    assert reexec.asks_for_python(command) is expected


def test_with_interpreter_reexec_false_the_app_runs(reexec: ModuleType, child: ModuleType) -> None:
    argv = [EXE, "-c", "ran = True"]

    assert reexec.run_child(argv, OFF, PREFIX) is False
    assert not hasattr(child, "ran")


@pytest.mark.parametrize(
    ("argv", "shown"),
    [
        ([EXE, "-B", "-S", "-I", "-c", LOKY_TRACKER], f"-c {LOKY_TRACKER[:60]!r}..."),
        ([EXE, "-m", "joblib.externals.loky.backend.popen_loky_posix", "--pipe", "17"],
         "-m joblib.externals.loky.backend.popen_loky_posix"),
        ([EXE, f"{PREFIX}cpuinfo/cpuinfo.py", "--json"], "cpuinfo/cpuinfo.py"),
    ],
)  # fmt: skip
def test_without_the_setting_a_child_asking_for_python_is_refused(
    reexec: ModuleType, child: ModuleType, capsys: pytest.CaptureFixture[str],
    argv: list[str], shown: str,
) -> None:
    with pytest.raises(SystemExit) as refused:
        reexec.run_child(argv, UNSET, PREFIX)

    assert refused.value.code == 70
    message = capsys.readouterr().err
    assert message.startswith(f"fsbpyr: refusing to run `{shown}` as Python")
    assert "--interpreter-reexec" in message and "--no-interpreter-reexec" in message


def _scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``_fsbpyr_scripts`` package with two scripts, ``tools/probe.py`` and ``other.py``."""
    root = tmp_path / "_fsbpyr_scripts"
    (root / "tools").mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "tools" / "__init__.py").write_text("")
    for script in (root / "tools" / "probe.py", root / "other.py"):
        script.write_text("import sys\nseen = (__name__, sys.argv[:])\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in [n for n in sys.modules if n.split(".")[0] == "_fsbpyr_scripts"]:
        monkeypatch.delitem(sys.modules, name)  # another test's package of that name
    return root


def test_without_the_setting_a_script_that_is_an_own_command_runs_as_python_would(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _scripts(tmp_path, monkeypatch)
    script = str(root / "tools" / "probe.py")
    monkeypatch.setattr(sys, "path", list(sys.path))
    argv = [EXE, "-u", script, "a"]
    targets = ("_fsbpyr_scripts.other", "_fsbpyr_scripts.tools.probe", "pkg.cli:main")

    assert reexec.run_child(argv, UNSET, f"{tmp_path}/", targets) is True
    assert child.seen == ("__main__", [script, "a"])
    assert sys.path[0] == str(root / "tools")


def test_without_the_setting_a_script_that_is_no_own_command_is_still_refused(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    root = _scripts(tmp_path, monkeypatch)

    for argv, targets in (([EXE, str(root / "other.py")], ("_fsbpyr_scripts.tools.probe",)),
                          ([EXE, str(root / "other.py")], ()),
                          ([EXE, "-m", "_fsbpyr_scripts.other"], ("_fsbpyr_scripts.other",))):
        with pytest.raises(SystemExit) as refused:
            reexec.run_child(argv, UNSET, f"{tmp_path}/", targets)
        assert refused.value.code == 70
    assert not hasattr(child, "seen") and "refusing to run" in capsys.readouterr().err


def test_a_script_that_is_an_own_command_is_not_run_by_a_caller_other_than_the_app(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _scripts(tmp_path, monkeypatch)
    monkeypatch.setattr(reexec, "_parent_runs_this_executable", lambda: False)
    argv = [EXE, str(root / "other.py")]

    assert reexec.run_child(argv, UNSET, f"{tmp_path}/", ("_fsbpyr_scripts.other",)) is False
    assert not hasattr(child, "seen")


def test_without_the_setting_the_apps_own_options_run_the_app(
    reexec: ModuleType, child: ModuleType
) -> None:
    assert reexec.run_child([EXE, "-c", "config.toml"], UNSET, PREFIX) is False
    assert reexec.run_child([EXE, "-m", "two words"], UNSET, PREFIX) is False


def test_without_the_setting_a_caller_other_than_the_app_gets_the_app(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reexec, "_parent_runs_this_executable", lambda: False)

    assert reexec.run_child([EXE, "-c", "ran = True"], UNSET, PREFIX) is False  # no refusal
    assert not hasattr(child, "ran")


def test_a_caller_other_than_the_app_gets_the_app(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reexec, "_parent_runs_this_executable", lambda: False)
    argv = [EXE, "-c", "ran = True"]
    monkeypatch.setattr(sys, "argv", list(argv))

    assert reexec.run_child(argv, ON, PREFIX) is False
    assert not hasattr(child, "ran") and sys.argv == argv


def test_a_parent_with_another_uid_gets_the_app(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    mp = sys.modules["_fsbpyr_rt.mp"]
    same = types.SimpleNamespace(st_dev=1, st_ino=2)  # the same executable, other credentials
    monkeypatch.setattr(mp.os, "stat", lambda path: same)
    uid, gid = mp.os.getresuid()[:2], mp.os.getresgid()[:2]
    fake = f"Uid:\t{uid[0] + 1}\t{uid[1]}\t{uid[1]}\t{uid[1]}\nGid:\t{gid[0]}\t{gid[1]}\n"
    monkeypatch.setattr(mp, "_read_parent_status", lambda: fake)
    monkeypatch.setattr(reexec, "_parent_runs_this_executable", mp._parent_runs_this_executable)
    argv = [EXE, "-c", "ran = True"]

    assert reexec.run_child(argv, ON, PREFIX) is False
    assert not hasattr(child, "ran")


def test_code_runs_in_main_with_the_argv_cpython_leaves(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", [EXE, "-B", "-c", "import sys; seen = sys.argv[:]", "x"])

    assert reexec.run_child(sys.argv, ON, PREFIX) is True
    assert child.seen == ["-c", "x"]


def test_a_module_runs_as_main_with_its_path_first(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "helper_mod.py").write_text("import sys\nseen = (__name__, sys.argv[:])\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "argv", [EXE, "-u", "-B", "-m", "helper_mod", "a"])

    assert reexec.run_child(sys.argv, ON, PREFIX) is True
    assert child.seen == ("__main__", [str(tmp_path / "helper_mod.py"), "a"])


def test_a_payload_script_runs_as_its_module_with_its_path_first(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = tmp_path / "helper_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "tool.py").write_text("import sys\nseen = (__name__, sys.argv[:])\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "argv", [EXE, "-u", str(package / "tool.py"), "a"])

    assert reexec.run_child(sys.argv, ON, f"{tmp_path}/") is True
    assert child.seen == ("__main__", [str(package / "tool.py"), "a"])


def test_a_payload_script_has_its_directory_first_on_the_path(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """As CPython puts the script's directory at sys.path[0]: ``import common`` finds the
    sibling."""
    package = tmp_path / "sibling_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "common.py").write_text("WORD = 'sibling'\n")
    (package / "tool.py").write_text("import sys, common\nseen = (sys.path[0], common.WORD)\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "path", list(sys.path))  # the child's insert is undone
    monkeypatch.setattr(sys, "argv", [EXE, str(package / "tool.py")])

    assert reexec.run_child(sys.argv, ON, f"{tmp_path}/") is True
    assert child.seen == (str(package), "sibling")


def test_a_packages_main_script_runs_the_package(
    reexec: ModuleType, child: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = tmp_path / "helper_main"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text("import sys\nseen = (__name__, sys.argv[:])\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "argv", [EXE, str(package / "__main__.py"), "b"])

    assert reexec.run_child(sys.argv, ON, f"{tmp_path}/") is True
    assert child.seen == ("__main__", [str(package / "__main__.py"), "b"])


def test_a_payload_path_that_is_no_module_runs_the_app(
    reexec: ModuleType, child: ModuleType, tmp_path: Path
) -> None:
    argv = [EXE, f"{tmp_path}/nowhere/tool.py"]

    assert reexec.run_child(argv, ON, f"{tmp_path}/") is False
