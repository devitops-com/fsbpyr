"""The packages without a wheel for a Python fsbpyr supports (no-wheels.txt): what needs one is
skipped on that Python, with a reason tests/conftest.py expects."""

from pathlib import Path

import pytest
from packaging.utils import canonicalize_name

REASON = "no wheel for Python"


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


def skip_without(package: str, python: str) -> None:
    """Skips the test when ``package`` has no wheel for ``python`` (X.Y)."""
    if missing(package, python):
        pytest.skip(f"{REASON} {python}: {package}")
