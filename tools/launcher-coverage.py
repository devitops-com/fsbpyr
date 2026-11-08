"""The launcher's line coverage, gated (owner, 2026-11-08): at least 90% of the lines of
fsbpyr's own C code, and no file of it below 85%.

    launcher-coverage.py SUMMARY.json

SUMMARY.json is `llvm-cov export -summary-only` of the probes, the fuzz harnesses and the
instrumented sanitized stub (full-suite.yml, "Launcher coverage"). fsbpyr's code is every file
under src/fsbpyr/launcher/ but the vendored zstd; CPython's headers (whose inline functions the
launcher compiles), the tests' own sources and files without a line of code do not count.
Prints a row per file and the total; exit 1 when a rule fails, 2 on bad input.
"""

import json
import sys
from dataclasses import dataclass

TOTAL_MIN = 90.0  # % of the lines of fsbpyr's C code
FILE_MIN = 85.0  # % of each file's lines: a coarse floor, below the total
OURS = "/src/fsbpyr/launcher/"
NOT_OURS = ("/vendor/",)


@dataclass(frozen=True)
class File:
    name: str  # below src/fsbpyr/launcher/
    lines: int
    covered: int

    @property
    def percent(self) -> float:
        return 100.0 * self.covered / self.lines


def ours(summary: dict[str, object]) -> list[File]:
    """fsbpyr's C files with code in an llvm-cov summary, by name."""
    found = []
    for data in summary["data"]:  # type: ignore[attr-defined]
        for entry in data["files"]:
            path, lines = entry["filename"], entry["summary"]["lines"]
            if OURS in path and not any(part in path for part in NOT_OURS) and lines["count"]:
                found.append(File(path.partition(OURS)[2], lines["count"], lines["covered"]))
    return sorted(found, key=lambda f: f.name)


def verdict(files: list[File]) -> list[str]:
    """What fails the gate: nothing when the total and every file pass."""
    if not files:
        return ["no file of fsbpyr's C code in the summary"]
    failures = [f"{f.name}: {f.percent:.2f}% of lines, below the floor of {FILE_MIN:.0f}%"
                for f in files if f.percent < FILE_MIN]  # fmt: skip
    total = 100.0 * sum(f.covered for f in files) / sum(f.lines for f in files)
    if total < TOTAL_MIN:
        failures.append(f"total: {total:.2f}% of lines, below {TOTAL_MIN:.0f}%")
    return failures


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: launcher-coverage.py SUMMARY.json", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as handle:
            files = ours(json.load(handle))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"launcher-coverage: cannot read {argv[0]}: {exc}", file=sys.stderr)
        return 2
    for f in files:
        print(f"{f.name:<16} {f.covered:>5} of {f.lines:>5} lines  {f.percent:6.2f}%")
    if files:
        lines, covered = sum(f.lines for f in files), sum(f.covered for f in files)
        print(f"{'total':<16} {covered:>5} of {lines:>5} lines  {100.0 * covered / lines:6.2f}%")
    failures = verdict(files)
    for failure in failures:
        print(f"launcher-coverage: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
