"""tools/launcher-coverage.py, the gate on the launcher's line coverage (owner, 2026-11-08): 90%
of the lines of fsbpyr's own C code, and no file of it below 85%, from llvm-cov's summary."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

TOOL = Path(__file__).parents[2] / "tools" / "launcher-coverage.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("launcher_coverage", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["launcher_coverage"] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


gate = _load()
LAUNCHER = "/home/tester/src/src/fsbpyr/launcher"


def _summary(*files: tuple[str, int, int]) -> dict[str, object]:
    """llvm-cov export -summary-only's shape: (path, lines, covered) per file."""
    return {"data": [{"files": [
        {"filename": path, "summary": {"lines": {"count": lines, "covered": covered}}}
        for path, lines, covered in files
    ]}]}  # fmt: skip


def test_only_fsbpyrs_c_code_counts() -> None:
    """CPython's headers, the vendored zstd, the tests' sources and files without code do not."""
    summary = _summary(
        (f"{LAUNCHER}/cache.c", 744, 700),
        (f"{LAUNCHER}/payload.h", 3, 3),
        (f"{LAUNCHER}/format.h", 0, 0),
        (f"{LAUNCHER}/vendor/zstd/zstd.c", 9000, 10),
        ("/fsbpyr/python/install/include/python3.14/refcount.h", 157, 32),
        ("/home/tester/src/tests/probes/way.c", 50, 50),
    )

    assert [(f.name, f.lines, f.covered) for f in gate.ours(summary)] == [
        ("cache.c", 744, 700), ("payload.h", 3, 3)]  # fmt: skip


@pytest.mark.parametrize(("files", "failures"), [
    ([("a.c", 100, 95), ("b.c", 100, 86)], []),
    ([("a.c", 100, 99), ("b.c", 100, 84)], ["b.c: 84.00% of lines, below the floor of 85%"]),
    ([("a.c", 100, 89), ("b.c", 100, 90)], ["total: 89.50% of lines, below 90%"]),
    ([], ["no file of fsbpyr's C code in the summary"]),
])  # fmt: skip
def test_the_total_must_reach_90_and_each_file_85(
    files: list[tuple[str, int, int]], failures: list[str]
) -> None:
    assert gate.verdict([gate.File(*f) for f in files]) == failures


def test_it_prints_each_file_and_fails_when_a_rule_does(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps(_summary((f"{LAUNCHER}/due.c", 137, 93),
                                           (f"{LAUNCHER}/main.c", 102, 98))))  # fmt: skip

    assert gate.main([str(summary)]) == 1
    out, err = capsys.readouterr()
    assert out.splitlines() == ["due.c               93 of   137 lines   67.88%",
                                "main.c              98 of   102 lines   96.08%",
                                "total              191 of   239 lines   79.92%"]  # fmt: skip
    assert "due.c: 67.88% of lines, below the floor of 85%" in err
    assert "total: 79.92% of lines, below 90%" in err


def test_a_summary_that_cannot_be_read_is_bad_input(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("{")

    assert gate.main([str(tmp_path / "bad.json")]) == 2
    assert gate.main([str(tmp_path / "missing.json")]) == 2
    assert gate.main([]) == 2
