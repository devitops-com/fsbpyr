"""The one place fsbpyr's code differs between Python 3.12, 3.13 and 3.14 (Phase 9)."""

import importlib
import os
import pkgutil
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import fsbpyr
from fsbpyr.compat import process_cpu_count, zstd

# the libzstd of 3.14's compression.zstd; backports.zstd must bundle the same, so every
# Python version compresses alike and builds stay reproducible (D15)
LIBZSTD_VERSION = (1, 5, 7)


def test_every_module_of_the_package_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    # the boot modules run inside executables, where _fsbpyr is the launcher's own module
    monkeypatch.setitem(sys.modules, "_fsbpyr", MagicMock())
    monkeypatch.syspath_prepend(str(Path(fsbpyr.__path__[0]) / "boot"))
    names = [m.name for m in pkgutil.walk_packages(fsbpyr.__path__, "fsbpyr.")]

    assert "fsbpyr.compat" in names
    for name in names:
        importlib.import_module(name)


def test_the_zstd_module_has_the_libzstd_3_14_uses() -> None:
    assert zstd.zstd_version_info == LIBZSTD_VERSION


def test_the_cpu_count_is_the_usable_cores() -> None:
    assert process_cpu_count() == len(os.sched_getaffinity(0))
