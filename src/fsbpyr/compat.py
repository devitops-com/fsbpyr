"""What differs between the Python versions fsbpyr itself runs on (3.12 to 3.14)."""

import os
import sys

if sys.version_info >= (3, 14):
    from compression import zstd
else:
    import backports.zstd as zstd  # noqa: PLR0402  the official backport, same API

__all__ = ["process_cpu_count", "zstd"]


def process_cpu_count() -> int | None:
    """`os.process_cpu_count()` (3.13+); before it, the Linux affinity set it counts."""
    if sys.version_info >= (3, 13):
        return os.process_cpu_count()
    return len(os.sched_getaffinity(0))
