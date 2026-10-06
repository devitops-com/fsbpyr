"""Tiny startup benchmark: p50/p90 wall time and child CPU of repeated posix_spawn runs.

usage: bench.py [-n RUNS] LABEL=CMD [LABEL=CMD ...]   (CMD is split on whitespace)
"""
import os
import resource
import statistics
import sys
import time


def bench(argv: list[str], runs: int) -> tuple[float, float, float]:
    walls = []
    devnull = os.open(os.devnull, os.O_WRONLY)
    actions = [(os.POSIX_SPAWN_DUP2, devnull, 1)]
    for _ in range(5):  # warm-up
        os.waitpid(os.posix_spawn(argv[0], argv, os.environ, file_actions=actions), 0)
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    for _ in range(runs):
        t0 = time.perf_counter()
        _, status = os.waitpid(os.posix_spawn(argv[0], argv, os.environ, file_actions=actions), 0)
        walls.append(time.perf_counter() - t0)
        if status != 0:
            raise SystemExit(f"{argv} exited with status {status}")
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime) / runs
    walls.sort()
    return statistics.median(walls) * 1e3, walls[int(len(walls) * 0.9)] * 1e3, cpu * 1e3


def main() -> None:
    args = sys.argv[1:]
    runs = 60
    if args[:1] == ["-n"]:
        runs, args = int(args[1]), args[2:]
    for spec in args:
        label, cmd = spec.split("=", 1)
        p50, p90, cpu = bench(cmd.split(), runs)
        print(f"{label:<28} p50={p50:6.2f}ms  p90={p90:6.2f}ms  cpu={cpu:6.2f}ms")


if __name__ == "__main__":
    main()
