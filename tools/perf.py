"""`make perf`: one release's start, build and inspect against the previous one's (D37).

    perf.py OLD NEW [--rounds N] [--build-rounds N] [--work DIR] [--json]

OLD and NEW are fsbpyr executables: the previous release's `fsbpyr-3.X-<target>`, and this
release's (or `dist/fsbpyr`, a build of the checkout). Each builds the fixtures `hello` (no
dependencies) and `native` (orjson, markupsafe) from tests/fixtures/projects with its own runtime,
and then, round by round and interleaved after warm-up runs, so that a drift of the CPU clock hits
both alike:

- start: `hello` and `native` (the median and the 90th percentile, and their peak RSS from
  /usr/bin/time, since wait4 reports at least the spawning Python's), the integrity check of
  `hello` (`integrity_ns`, FSBPYR_TRACE=1), and bare CPython (`python -I -S -c pass`) to show
  what the machine did that day;
- build: `fsbpyr build hello`, a new output each time, warm caches;
- inspect: each fsbpyr's `inspect --verify` of itself.

It prints a Markdown table, OLD against NEW (with --json, the numbers), for docs/performance.md:
every release records it (owner, 2026-11-08). Nothing is a limit: no figure fails anything (D37);
a run that exits non-zero does (exit 1). Each app has a cache of its own under the work directory
(default: a new one under TMPDIR, removed afterwards); builds use the caller's uv and fsbpyr
caches. Needs uv, and /usr/bin/time (GNU time); nothing is installed for you.
"""

import argparse
import functools
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "projects"
APPS = ("hello", "native")
GNU_TIME = "/usr/bin/time"
TRACE = re.compile(rb"^fsbpyr-trace (\{.*\})$", re.MULTILINE)
WARMUP = 5


@dataclass
class Command:
    """One thing timed: an argv and its environment."""

    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    before: Callable[[], None] | None = None  # run before each start (a fresh output)


def run_once(command: Command, *, stderr: bool = False) -> tuple[float, int, bytes]:
    """(wall ms, exit status, standard error if asked) of one run, spawned and reaped directly."""
    if command.before is not None:
        command.before()
    with tempfile.TemporaryFile() as err, open(os.devnull, "wb") as null:
        actions = [(os.POSIX_SPAWN_DUP2, null.fileno(), 1),
                   (os.POSIX_SPAWN_DUP2, (err if stderr else null).fileno(), 2)]  # fmt: skip
        start = time.perf_counter_ns()
        pid = os.posix_spawn(command.argv[0], command.argv, command.env, file_actions=actions)
        _, status, _ = os.wait4(pid, 0)
        elapsed = (time.perf_counter_ns() - start) / 1e6
        err.seek(0)
        return elapsed, os.waitstatus_to_exitcode(status), err.read() if stderr else b""


def peak_rss_kib(command: Command) -> int:
    """The peak resident set of one run, as GNU time reports it."""
    with tempfile.NamedTemporaryFile(suffix=".rss") as out:
        run_once(Command([GNU_TIME, "-f", "%M", "-o", out.name, *command.argv], command.env))
        return int(Path(out.name).read_text().split()[-1])


def integrity_ns(text: bytes) -> int:
    """`integrity_ns` of an fsbpyr-trace line on standard error."""
    found = TRACE.search(text)
    if found is None:
        raise ValueError("no fsbpyr-trace line: was FSBPYR_TRACE set?")
    return int(json.loads(found.group(1))["integrity_ns"])


def interleaved(commands: dict[str, Command], rounds: int, warmup: int = WARMUP
                ) -> dict[str, list[float]]:  # fmt: skip
    """Each command's wall times: ``warmup`` runs of each first, then ``rounds`` rounds in which
    every command runs once, in turn."""
    for command in commands.values():
        for _ in range(warmup):
            checked(command)
    times: dict[str, list[float]] = {label: [] for label in commands}
    for _ in range(rounds):
        for label, command in commands.items():
            times[label].append(checked(command))
    return times


def checked(command: Command) -> float:
    elapsed, status, _ = run_once(command)
    if status != 0:
        raise SystemExit(f"perf: {' '.join(command.argv)} exited {status}")
    return elapsed


def percentile(values: Sequence[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[round(share * (len(ordered) - 1))]


def change(old: float, new: float) -> str:
    """NEW against OLD, as a signed percentage ("+1.2 %"); "=" when they are equal."""
    if old == new:
        return "="
    return f"{(new - old) / old * 100:+.1f} %"


def shown(value: float, unit: str) -> str:
    """A figure as the table gives it: whole bytes and KiB, milliseconds to two places."""
    return f"{value:,.0f} {unit}" if unit in ("bytes", "KiB") else f"{value:.2f} {unit}"


def table(rows: list[tuple[str, float, float, str]], old: str, new: str) -> str:
    """The Markdown table of (measure, OLD, NEW, unit) rows."""
    lines = [f"| Measure | {old} | {new} | Change |", "|---|---|---|---|"]
    for measure, before, after, unit in rows:
        lines.append(f"| {measure} | {shown(before, unit)} | {shown(after, unit)} | "
                     f"{change(before, after)} |")  # fmt: skip
    return "\n".join(lines)


def version(fsbpyr: str) -> str:
    done = subprocess.run([fsbpyr, "--version"], capture_output=True, text=True,  # noqa: S603
                          check=True)  # fmt: skip
    return done.stdout.split()[-1]


def build(fsbpyr: str, project: str, out: Path, python: str) -> None:
    source = out.parent / f"src-{out.name}"
    shutil.copytree(FIXTURES / project, source)
    done = subprocess.run([fsbpyr, "build", str(source), "--python", python, "-o", str(out)],  # noqa: S603
                          capture_output=True, text=True, check=False)  # fmt: skip
    if done.returncode != 0:
        raise SystemExit(f"perf: {fsbpyr} could not build {project}:\n{done.stderr}")


@dataclass(frozen=True)
class Settings:
    rounds: int  # of the starts
    build_rounds: int  # of the builds and the inspections
    python: str  # the apps'


def measure(fsbpyrs: dict[str, str], work: Path, settings: Settings
            ) -> list[tuple[str, float, float, str]]:  # fmt: skip
    """The rows of the table: ``fsbpyrs`` has the "old" and the "new" one."""
    old, new = fsbpyrs["old"], fsbpyrs["new"]
    rounds, python = settings.rounds, settings.python
    for side, fsbpyr in fsbpyrs.items():
        for name in APPS:
            build(fsbpyr, name, work / f"{name}-{side}", python)
    base = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(work / "home")}
    (work / "home").mkdir(exist_ok=True)

    def started(name: str, side: str, **env: str) -> Command:
        cache = {"FSBPYR_CACHE_DIR": str(work / f"cache-{side}")}
        return Command([str(work / f"{name}-{side}")], {**base, **cache, **env})

    starts = {f"{name}-{side}": started(name, side) for name in APPS for side in fsbpyrs}
    starts["bare"] = Command([sys.executable, "-I", "-S", "-c", "pass"], base)
    times = interleaved(starts, rounds)
    rows = [(f"start of `{name}`, p50", statistics.median(times[f"{name}-old"]),
             statistics.median(times[f"{name}-new"]), "ms") for name in APPS]  # fmt: skip
    rows += [(f"start of `{name}`, p90", percentile(times[f"{name}-old"], 0.9),
              percentile(times[f"{name}-new"], 0.9), "ms") for name in APPS]  # fmt: skip
    traced: dict[str, list[int]] = {side: [] for side in fsbpyrs}
    for _ in range(rounds):
        for side in fsbpyrs:
            _, _, err = run_once(started("hello", side, FSBPYR_TRACE="1"), stderr=True)
            traced[side].append(integrity_ns(err))
    rows.append(("integrity check of `hello`, p50", statistics.median(traced["old"]) / 1e6,
                 statistics.median(traced["new"]) / 1e6, "ms"))  # fmt: skip
    for name in APPS:
        rss = {side: statistics.median(peak_rss_kib(started(name, side)) for _ in range(5))
               for side in fsbpyrs}  # fmt: skip
        rows.append((f"peak RSS of `{name}`", rss["old"], rss["new"], "KiB"))

    builds = {}
    for side, fsbpyr in fsbpyrs.items():
        out = work / f"built-{side}"
        source = work / f"src-hello-{side}"
        builds[side] = Command(
            [fsbpyr, "build", str(source), "--python", python, "-o", str(out)],
            dict(os.environ), before=functools.partial(out.unlink, missing_ok=True))
    built = interleaved(builds, settings.build_rounds, warmup=1)
    rows.append(("build of `hello`, p50", statistics.median(built["old"]),
                 statistics.median(built["new"]), "ms"))  # fmt: skip
    verify = {side: Command([fsbpyr, "inspect", "--verify", fsbpyr], dict(os.environ))
              for side, fsbpyr in fsbpyrs.items()}  # fmt: skip
    verified = interleaved(verify, settings.build_rounds, warmup=1)
    rows.append(("`inspect --verify` of itself, p50", statistics.median(verified["old"]),
                 statistics.median(verified["new"]), "ms"))  # fmt: skip
    rows.append(("size of fsbpyr", os.path.getsize(old), os.path.getsize(new), "bytes"))
    for name in APPS:
        rows.append((f"size of `{name}`", os.path.getsize(work / f"{name}-old"),
                     os.path.getsize(work / f"{name}-new"), "bytes"))  # fmt: skip
    bare = statistics.median(times["bare"])
    rows.append(("bare CPython (the day's machine), p50", bare, bare, "ms"))
    return rows


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="perf.py", description=__doc__.split("\n")[0])
    parser.add_argument("old", help="the previous release's fsbpyr")
    parser.add_argument("new", help="this release's fsbpyr (or dist/fsbpyr)")
    parser.add_argument("--rounds", type=int, default=201, help="start rounds (default 201)")
    parser.add_argument("--build-rounds", type=int, default=5, help="build rounds (default 5)")
    parser.add_argument("--python", default=".".join(map(str, sys.version_info[:2])))
    parser.add_argument("--work", type=Path, help="a directory to keep (default: a temporary)")
    parser.add_argument("--json", action="store_true", help="the numbers, not the table")
    args = parser.parse_args(argv)
    for tool in (args.old, args.new, GNU_TIME):
        if not (os.path.isfile(tool) and os.access(tool, os.X_OK)):
            print(f"perf: {tool} is not an executable (GNU time: apt install time); nothing is "
                  "installed for you", file=sys.stderr)  # fmt: skip
            return 2
    fsbpyrs = {"old": os.path.abspath(args.old), "new": os.path.abspath(args.new)}
    names = {side: version(fsbpyr) for side, fsbpyr in fsbpyrs.items()}
    settings = Settings(args.rounds, args.build_rounds, args.python)
    with tempfile.TemporaryDirectory(prefix="fsbpyr-perf.") as scratch:
        work = args.work or Path(scratch)
        work.mkdir(parents=True, exist_ok=True)
        rows = measure(fsbpyrs, work, settings)
    if args.json:
        print(json.dumps({"old": names["old"], "new": names["new"],
                          "rows": [{"measure": m, "old": o, "new": n, "unit": u}
                                   for m, o, n, u in rows]}, indent=1))  # fmt: skip
    else:
        print(table(rows, names["old"], names["new"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
