"""Validate canonical.py on a stdlib tree: round-trip equality, size and load-time impact.

usage: check_canonical.py STDLIB_DIR   (run with the interpreter whose bytecode is tested)
"""

import marshal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from canonical import canonicalize
from determinism import compile_file, sources


def main() -> None:
    stdlib = Path(sys.argv[1])
    originals, canons, failures = [], [], []
    t0 = time.perf_counter()
    for path in sources(stdlib):
        code = compile_file(path, stdlib)
        if code is None:
            continue
        original = marshal.dumps(code)
        canon = canonicalize(original)
        if marshal.loads(canon) != code or canonicalize(canon) != canon:
            failures.append(str(path.relative_to(stdlib)))
        originals.append(original)
        canons.append(canon)
    elapsed = time.perf_counter() - t0
    print(f"files={len(canons)} failures={len(failures)} {failures[:5]}")
    print(f"bytes original={sum(map(len, originals))} canonical={sum(map(len, canons))}")
    print(f"canonicalize+compile wall={elapsed:.2f}s")
    for label, blobs in (("original", originals), ("canonical", canons)):
        best = min(_load_all(blobs) for _ in range(5))
        print(f"marshal.loads all ({label:<9}) best={best * 1e3:.1f}ms")


def _load_all(blobs: list[bytes]) -> float:
    t0 = time.perf_counter()
    for blob in blobs:
        marshal.loads(blob)
    return time.perf_counter() - t0


if __name__ == "__main__":
    main()
