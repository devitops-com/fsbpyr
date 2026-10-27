"""Every corpus app's uv.lock has a wheel for fsbpyr's glibc baseline on each Python and machine,
unless tests/support/no-wheels.txt lists the package there: the corpus never builds a package
from its sdist, and every gap that keeps an app from running is written down. Checked for
aarch64 too, where the corpus runs only in CI."""

from pathlib import Path

import pytest

from fsbpyr.bytecode import SUPPORTED_PYTHON

from ..support import corpus, locks

PYTHONS = [f"{major}.{minor}" for major, minor in sorted(SUPPORTED_PYTHON)]


@pytest.mark.parametrize("app", corpus.apps())
def test_a_corpus_lock_has_wheels_where_no_wheels_txt_says_none_are_missing(app: str) -> None:
    unlisted = {f"{python}/{machine}": gaps for python in PYTHONS for machine in locks.MACHINES
                if (gaps := corpus.unlisted_gaps(app, python, machine))}  # fmt: skip

    assert not unlisted, f"{app}: no wheel, and not in tests/support/no-wheels.txt: {unlisted}"


@pytest.mark.parametrize(
    ("wheel", "python", "machine", "expected"),
    [
        ("numpy-2.3.0-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
         "3.14", "x86_64", True),
        ("numpy-2.3.0-cp314-cp314-manylinux_2_28_x86_64.whl", "3.13", "x86_64", False),
        ("numpy-2.3.0-cp314-cp314-manylinux_2_28_x86_64.whl", "3.14", "aarch64", False),
        ("orjson-3.13.0-cp315-cp315-manylinux_2_39_x86_64.whl", "3.15", "x86_64", False),
        ("cryptography-46.0.0-cp311-abi3-manylinux2014_aarch64.whl", "3.15", "aarch64", True),
        ("click-8.3.0-py3-none-any.whl", "3.12", "x86_64", True),
        ("torch-2.14.1+cpu-cp314-cp314t-manylinux_2_28_x86_64.whl", "3.14", "x86_64", False),
        ("ruff-0.15.0-py3-none-manylinux_2_17_x86_64.whl", "3.13", "x86_64", True),
        ("lxml-6.1.0-cp314-cp314-musllinux_1_2_x86_64.whl", "3.14", "x86_64", False),
    ],
)  # fmt: skip
def test_a_wheel_is_usable_for_its_python_and_machine_on_manylinux_2_34(
    wheel: str, python: str, machine: str, expected: bool
) -> None:
    assert locks.usable(wheel, python, machine) is expected


def test_installed_follows_markers_and_extras(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text("""
version = 1

[[package]]
name = "app"
version = "1.0.0"
source = { editable = "." }
dependencies = [
    { name = "colorama", marker = "sys_platform == 'win32'" },
    { name = "b" },
    { name = "old", marker = "python_full_version < '3.13'" },
]

[[package]]
name = "b"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
dependencies = [{ name = "c", extra = ["x"] }]

[[package]]
name = "c"
version = "1.0"
source = { registry = "https://pypi.org/simple" }

[package.optional-dependencies]
x = [{ name = "d", marker = "platform_machine == 'aarch64'" }]

[[package]]
name = "d"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://example.org/d-1.0.tar.gz" }

[[package]]
name = "colorama"
version = "0.4.6"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "old"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
wheels = [{ url = "https://example.org/old-1.0-py3-none-any.whl" }]
""")

    def names(python: str, machine: str) -> list[str]:
        return sorted(package.name for package in locks.installed(lock, python, machine))

    assert names("3.14", "x86_64") == ["b", "c"]
    assert names("3.12", "aarch64") == ["b", "c", "d", "old"]
    assert locks.without_wheel(lock, "3.12", "aarch64") == ["b", "c", "d"]
