"""_fsbpyr_rt.links: a command name that is no plain file name never makes a link."""

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from .conftest import BOOT_DIR


@pytest.fixture
def links(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.syspath_prepend(str(BOOT_DIR))
    for name in ("_fsbpyr_rt.links", "_fsbpyr_rt.commands"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    yield importlib.import_module("_fsbpyr_rt.links")
    for name in ("_fsbpyr_rt.links", "_fsbpyr_rt.commands"):
        sys.modules.pop(name, None)


@pytest.mark.parametrize("name", ["../../evil", "a/b", "..", "."])
def test_a_name_with_a_directory_part_makes_no_link(
    links: ModuleType, tmp_path: Path, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "x" / "bin"
    target.mkdir(parents=True)
    executable = tmp_path / "app"
    executable.write_text("")

    status = links.install(str(target).encode(), str(executable), "m:f", name)

    assert status == 1
    assert "is no file name" in capsys.readouterr().err
    assert sorted(p for p in tmp_path.rglob("*") if p.is_symlink()) == []
