"""The packages without a wheel for a Python fsbpyr supports (no-wheels.txt): what needs one is
not run on that Python. A parametrized test is not given the case (``missing``); any other is
marked ``needs``, and tests/conftest.py deselects it there."""

from pathlib import Path

import pytest
from packaging.utils import canonicalize_name


def _read(path: Path) -> dict[str, frozenset[str]]:
    table: dict[str, frozenset[str]] = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            python, *packages = line.split()  # several lines may name one Python
            table[python] = table.get(python, frozenset()) | set(map(canonicalize_name, packages))
    return table


NO_WHEELS = _read(Path(__file__).with_name("no-wheels.txt"))


def missing(package: str, python: str) -> bool:
    """Whether ``package`` (any spelling: charset_normalizer is charset-normalizer) has no wheel
    for ``python`` (X.Y)."""
    return canonicalize_name(package) in NO_WHEELS.get(python, frozenset())


def needs(package: str, python: str | None = None) -> pytest.MarkDecorator:
    """Marks a test as needing a wheel of ``package`` for ``python`` (X.Y; default the tests'
    Python): tests/conftest.py deselects it where ``missing`` says there is none."""
    return pytest.mark.needs_wheel(package, python=python)
