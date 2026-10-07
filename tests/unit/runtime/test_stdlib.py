"""Stdlib selection, startup-module detection and compile-worker framing."""

from pathlib import Path

import pytest

from fsbpyr.runtime.stdlib import StdlibTree, collect, decode_compiled, parse_importtime

IMPORTTIME = """\
import time: self [us] | cumulative | imported package
import time:       122 |        122 |   _io
import time:       373 |        680 | _frozen_importlib_external
import time:        24 |         24 |     _codecs
import time:       198 |        198 |   encodings.aliases
import time:       241 |        776 | encodings
import time:        92 |         92 | encodings.utf_8
import time:        86 |         86 | linecache
"""


def _tree(root: Path, files: list[str]) -> None:
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# x\n")


def test_collect_classifies_modules_and_resources(tmp_path: Path) -> None:
    _tree(
        tmp_path,
        [
            "os.py",
            "json/__init__.py",
            "json/decoder.py",
            "email/architecture.rst",
            "venv/scripts/common/activate",
            "test/test_os.py",
            "idlelib/run.py",
            "__pycache__/os.cpython-314.pyc",
            "config-3.14-x86_64-linux-gnu/Makefile",
            "lib-dynload/_dbm.so",
            "site-packages/README.txt",
            "tkinter/__init__.py",
            "ensurepip/__init__.py",
            "turtledemo/x.py",
            "__phello__/spam.py",
            "dir.with.dots/x.py",
        ],
    )

    tree = collect(tmp_path)

    assert tree == StdlibTree(
        modules=(
            ("json", "json/__init__.py", True),
            ("json.decoder", "json/decoder.py", False),
            ("os", "os.py", False),
        ),
        resources=("email/architecture.rst", "venv/scripts/common/activate"),
    )


def test_parse_importtime_lists_modules_in_import_order() -> None:
    assert parse_importtime(IMPORTTIME) == [
        "_io",
        "_frozen_importlib_external",
        "_codecs",
        "encodings.aliases",
        "encodings",
        "encodings.utf_8",
        "linecache",
    ]


def test_decode_compiled_frames() -> None:
    stream = (3).to_bytes(4, "little") + b"abc" + (0xFFFFFFFF).to_bytes(4, "little")

    assert decode_compiled(stream, ["a.py", "bad.py"]) == {"a.py": b"abc", "bad.py": None}


@pytest.mark.parametrize("stream", [b"\x05\x00\x00\x00ab", b"\x01\x00", b""])
def test_decode_compiled_rejects_truncated_streams(stream: bytes) -> None:
    with pytest.raises(ValueError, match="compile worker"):
        decode_compiled(stream, ["a.py"])
