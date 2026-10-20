"""Tiny startup benchmark: p50/p90 wall time and child CPU of repeated posix_spawn runs.

usage: bench.py [-n RUNS] [--cold] [--cache DIR] LABEL=CMD [LABEL=CMD ...]
(CMD is split on whitespace)

--cache DIR  run the commands with FSBPYR_CACHE_DIR=DIR.
--cold       no warm-up runs; before each sample, drop the executable's pages from the page cache
             and, with --cache, those of every file under DIR (posix_fadvise DONTNEED, no root
             needed). The files must be on a disk file system: on tmpfs nothing is evicted. The
             cache must be filled by an earlier run, or the first sample extracts into it.
"""
import os
import resource
import statistics
import sys
import time


def evict(path: str) -> None:
    """Drop one file's pages from the page cache (dirty pages stay, so write them out first)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


def evict_tree(directory: str) -> None:
    for root, _, names in os.walk(directory):
        for name in names:
            path = os.path.join(root, name)
            if os.path.isfile(path) and not os.path.islink(path):
                evict(path)


def bench(
    argv: list[str], runs: int, cold: bool = False, cache: str | None = None
) -> tuple[float, float, float]:
    walls = []
    env = dict(os.environ)
    if cache is not None:
        env["FSBPYR_CACHE_DIR"] = cache
    devnull = os.open(os.devnull, os.O_WRONLY)
    actions = [(os.POSIX_SPAWN_DUP2, devnull, 1)]
    for _ in range(0 if cold else 5):  # warm-up
        os.waitpid(os.posix_spawn(argv[0], argv, env, file_actions=actions), 0)
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    for _ in range(runs):
        if cold:
            evict(argv[0])
            if cache is not None:
                evict_tree(cache)
        t0 = time.perf_counter()
        _, status = os.waitpid(os.posix_spawn(argv[0], argv, env, file_actions=actions), 0)
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
    cold = False
    cache = None
    while args[:1] in (["-n"], ["--cold"], ["--cache"]):
        if args[0] == "-n":
            runs, args = int(args[1]), args[2:]
        elif args[0] == "--cache":
            cache, args = args[1], args[2:]
        else:
            cold, args = True, args[1:]
    for spec in args:
        label, cmd = spec.split("=", 1)
        p50, p90, cpu = bench(cmd.split(), runs, cold, cache)
        print(f"{label:<28} p50={p50:6.2f}ms  p90={p90:6.2f}ms  cpu={cpu:6.2f}ms")


if __name__ == "__main__":
    main()
