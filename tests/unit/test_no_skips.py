"""Nothing in tests/ skips or expects a failure: a test runs, or fails naming what is missing
(tests/support/requires.py). The corpus apps' own test files (tests/corpus) are theirs."""

import ast
import warnings
from pathlib import Path

TESTS = Path(__file__).parents[1]
FORBIDDEN = {"pytest.skip", "pytest.xfail", "pytest.importorskip", "pytest.mark.skip",
             "pytest.mark.skipif", "pytest.mark.xfail", "unittest.skip", "unittest.skipIf",
             "unittest.skipUnless", "unittest.expectedFailure"}  # fmt: skip
FORBIDDEN_NAMES = {name.rpartition(".")[2] for name in FORBIDDEN}


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return node.id if isinstance(node, ast.Name) else ""


def _skips(source: str) -> list[str]:
    """Each use of a skip or xfail in ``source``, as "line: what", in line order."""
    found = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)  # an invalid escape in a fixture's source
        tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and (
            _dotted(node) in FORBIDDEN or node.attr == "skipTest"
        ):
            found.append((node.lineno, _dotted(node)))
        if isinstance(node, ast.ImportFrom) and node.module in {"pytest", "unittest"}:
            found += [(node.lineno, f"from {node.module} import {alias.name}")
                      for alias in node.names if alias.name in FORBIDDEN_NAMES]  # fmt: skip
    return [f"{line}: {what}" for line, what in sorted(found)]


def test_no_test_skips_or_expects_a_failure() -> None:
    found = [f"{path.relative_to(TESTS.parent)}:{skip}"
             for path in sorted(TESTS.rglob("*.py")) if path.relative_to(TESTS).parts[0] != "corpus"
             for skip in _skips(path.read_text())]  # fmt: skip

    assert found == []


def test_the_scan_finds_each_way_to_skip() -> None:
    source = """\
import pytest, unittest
from pytest import skip
@pytest.mark.skipif(True, reason='x')
def test_a():
    pytest.importorskip('x')
@pytest.mark.xfail
def test_b(): self.skipTest('x')
@unittest.skip('x')
def test_c(): pytest.xfail('x')
"""

    assert _skips(source) == [
        "2: from pytest import skip", "3: pytest.mark.skipif", "5: pytest.importorskip",
        "6: pytest.mark.xfail", "7: self.skipTest", "8: unittest.skip", "9: pytest.xfail",
    ]
