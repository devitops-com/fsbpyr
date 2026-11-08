"""tools/mutate-c.py, mutation testing of the launcher's C code (`make mutate`, owner,
2026-11-08): which mutants a source has, and that the tests kill or let survive each one, on a
small tree with a shell command in place of pytest."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

TOOL = Path(__file__).parents[2] / "tools" / "mutate-c.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mutate_c", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["mutate_c"] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


mutate = _load()

SOURCE = """#include <stdio.h>
#define LIMIT (1 << 4) /* a < b in a comment */
// a <= b in a line comment
static int f(const int *p, int a, int b) {
    const char *s = "a < b in a string";
    char c = '<';
    if (a < b && p->x > 0) return 0;
    return a >> 1 == b << 1 ? -1 : a + 1;
}
"""


def test_mutants_are_operators_in_code_only() -> None:
    """Not in comments, strings, character constants or preprocessor lines; not the `<` of
    `<<` nor the `>` of `->` or `>>`."""
    found = [(m.line, m.original, m.replacement) for m in mutate.mutants(SOURCE)]

    assert found == [
        (7, "<", "<="), (7, "&&", "||"), (7, ">", ">="), (7, "return 0;", "return 1;"),
        (8, "==", "!="), (8, " + 1", " - 1"),
    ]  # fmt: skip


def test_a_mutant_changes_its_place_only() -> None:
    (mutant,) = [m for m in mutate.mutants(SOURCE) if m.original == "&&"]

    assert mutate.mutants(SOURCE, range(8, 9))[0].line == 8
    assert mutant.apply(SOURCE) == SOURCE.replace("a < b && p", "a < b || p")


def test_the_tests_kill_or_let_survive_each_mutant(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Here "the tests" check that `if (a < b` and `a + 1;` are as written: the four other mutants
    survive, and the source is left as it was."""
    launcher = tmp_path / "src" / "fsbpyr" / "launcher"
    launcher.mkdir(parents=True)
    (launcher / "x.c").write_text(SOURCE)
    x_c = "src/fsbpyr/launcher/x.c"
    check = f"grep -qF 'if (a < b' {x_c} && grep -qF 'a + 1;' {x_c}"

    status = mutate.main(["x.c", "unused", "--root", str(tmp_path), "--command", check])

    out = capsys.readouterr().out
    assert status == 1
    assert "6 mutants of x.c" in out and "2 of 6 killed, 4 survived:" in out
    assert "killed   x.c:7  `<` -> `<=`" in out and "killed   x.c:8  ` + 1` -> ` - 1`" in out
    assert "  x.c:7: `&&` -> `||`  if (a < b && p->x > 0) return 0;" in out
    assert (launcher / "x.c").read_text() == SOURCE


def test_tests_that_fail_without_a_mutant_stop_it(tmp_path: Path) -> None:
    (tmp_path / "src" / "fsbpyr" / "launcher").mkdir(parents=True)
    (tmp_path / "src" / "fsbpyr" / "launcher" / "x.c").write_text(SOURCE)

    assert mutate.main(["x.c", "t", "--root", str(tmp_path), "--command", "false"]) == 2
    assert mutate.main(["missing.c", "t", "--root", str(tmp_path), "--command", "true"]) == 2
