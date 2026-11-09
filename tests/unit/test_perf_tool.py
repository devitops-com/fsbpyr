"""tools/perf.py, the per-release measurement of start, build and inspect (`make perf`, owner
2026-11-08): its figures, its table, its order of runs and what stops it. Measuring itself takes
two releases' fsbpyr and is done by hand before a release (docs/performance.md)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from ..support import requires

TOOL = Path(__file__).parents[2] / "tools" / "perf.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("perf_tool", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["perf_tool"] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


perf = _load()


@pytest.mark.parametrize(("old", "new", "said"), [
    (100.0, 101.0, "+1.0 %"), (200.0, 100.0, "-50.0 %"), (5.0, 5.0, "="), (3.0, 3.001, "+0.0 %"),
])  # fmt: skip
def test_a_change_is_new_against_old_in_percent(old: float, new: float, said: str) -> None:
    assert perf.change(old, new) == said


def test_the_90th_percentile_is_the_value_at_nine_tenths() -> None:
    assert perf.percentile([float(v) for v in range(11)], 0.9) == 9.0
    assert perf.percentile([5.0], 0.9) == 5.0


def test_the_table_gives_milliseconds_to_two_places_and_bytes_whole() -> None:
    rows = [("start, p50", 7.876, 7.871, "ms"), ("size", 29540196, 29545684, "bytes")]

    assert perf.table(rows, "0.30.0", "0.31.0").splitlines() == [
        "| Measure | 0.30.0 | 0.31.0 | Change |",
        "|---|---|---|---|",
        "| start, p50 | 7.88 ms | 7.87 ms | -0.1 % |",
        "| size | 29,540,196 bytes | 29,545,684 bytes | +0.0 % |",
    ]


def test_the_integrity_check_comes_from_the_trace_line() -> None:
    err = b'noise\nfsbpyr-trace {"integrity_ns": 1634000, "modules": 3}\n'

    assert perf.integrity_ns(err) == 1634000
    with pytest.raises(ValueError, match="FSBPYR_TRACE"):
        perf.integrity_ns(b"no trace here\n")


def test_variants_run_their_warm_up_then_take_turns_round_by_round(tmp_path: Path) -> None:
    """So that a drift of the CPU clock hits all of them alike."""
    log = tmp_path / "log"
    commands = {name: perf.Command(["/bin/sh", "-c", f"echo {name} >> {log}"])
                for name in ("a", "b")}  # fmt: skip

    times = perf.interleaved(commands, rounds=3, warmup=2)

    assert log.read_text().split() == ["a", "a", "b", "b", "a", "b", "a", "b", "a", "b"]
    assert [len(times[name]) for name in ("a", "b")] == [3, 3]
    assert all(t > 0 for values in times.values() for t in values)


def test_a_run_that_fails_stops_it_naming_the_command() -> None:
    with pytest.raises(SystemExit, match="/bin/false exited 1"):
        perf.interleaved({"x": perf.Command(["/bin/false"])}, rounds=1, warmup=0)


@requires.tools("time")
def test_the_peak_rss_is_gnu_times() -> None:
    """wait4 never reports less than the spawning Python's own resident set."""
    assert 100 < perf.peak_rss_kib(perf.Command(["/bin/true"])) < 10_000


def test_a_missing_fsbpyr_is_refused_with_nothing_measured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert perf.main([str(tmp_path / "old"), str(tmp_path / "new")]) == 2
    assert "is not an executable" in capsys.readouterr().err
