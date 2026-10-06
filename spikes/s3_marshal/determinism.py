"""S3: is compile() + marshal.dumps() byte-deterministic across processes?

usage:
  determinism.py STDLIB_DIR PYTHON [PYTHON2]   # driver: runs workers, compares results
  determinism.py --worker STDLIB_DIR MODE OUT  # worker: MODE in {forward, reverse, noisy, batch}

Workers run in fresh processes with different PYTHONHASHSEED values and file orders.
"forward"/"reverse"/"noisy" dump right after each compile; "batch" keeps all code objects alive
before dumping (different refcounts -> exercises marshal's FLAG_REF decisions).
"""

import hashlib
import json
import marshal
import os
import subprocess
import sys
from pathlib import Path

EXCLUDED = {"test", "idlelib", "tkinter", "turtledemo", "__pycache__", "site-packages"}


def sources(stdlib: Path) -> list[Path]:
    return [p for p in sorted(stdlib.rglob("*.py"))
            if not any(part in EXCLUDED for part in p.relative_to(stdlib).parts[:-1])]


def compile_file(path: Path, stdlib: Path):
    try:
        return compile(path.read_bytes(), str(path.relative_to(stdlib)), "exec", dont_inherit=True)
    except SyntaxError:
        return None


def worker(stdlib: Path, mode: str, out: Path) -> None:
    dump = marshal.dumps
    if os.environ.get("S3_CANON"):  # apply the canonicalizer prototype to every dump
        sys.path.insert(0, str(Path(__file__).parent))
        from canonical import canonicalize

        def dump(code):
            return canonicalize(marshal.dumps(code))

    files = sources(stdlib)
    if mode == "reverse":
        files.reverse()
    if mode == "noisy":  # simulate a build tool process with many modules already loaded
        for name in ("argparse", "asyncio", "email.message", "logging", "tomllib", "zipfile"):
            __import__(name)
        files.reverse()
    digests = {}
    if mode == "batch":
        codes = [(f, compile_file(f, stdlib)) for f in files]
        for f, code in codes:
            if code is not None:
                digests[str(f.relative_to(stdlib))] = hashlib.sha256(dump(code)).hexdigest()
    else:
        for f in files:
            code = compile_file(f, stdlib)
            if code is not None:
                digests[str(f.relative_to(stdlib))] = hashlib.sha256(dump(code)).hexdigest()
    out.write_text(json.dumps(digests, sort_keys=True))


def run_worker(python: str, stdlib: Path, mode: str, seed: str, out: Path) -> dict[str, str]:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    subprocess.run([python, "-I", __file__, "--worker", str(stdlib), mode, str(out)], check=True, env=env)
    return json.loads(out.read_text())


def compare(label: str, a: dict[str, str], b: dict[str, str]) -> None:
    differing = sorted(k for k in a.keys() & b.keys() if a[k] != b[k])
    print(f"{label:<44} files={len(a.keys() & b.keys()):4d} differing={len(differing):4d} "
          f"{differing[:5]}")


def main() -> None:
    if sys.argv[1] == "--worker":
        worker(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]))
        return
    stdlib, python = Path(sys.argv[1]), sys.argv[2]
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"s3-{os.getpid()}"
    tmp.mkdir()
    fwd0 = run_worker(python, stdlib, "forward", "0", tmp / "a.json")
    fwd0b = run_worker(python, stdlib, "forward", "0", tmp / "b.json")
    rev = run_worker(python, stdlib, "reverse", "12345", tmp / "c.json")
    batch = run_worker(python, stdlib, "batch", "random", tmp / "d.json")
    noisy = run_worker(python, stdlib, "noisy", "random", tmp / "f.json")
    batch2 = run_worker(python, stdlib, "batch", "7", tmp / "g.json")
    compare("forward/seed0 vs forward/seed0 (rerun)", fwd0, fwd0b)
    compare("forward/seed0 vs reverse/seed12345", fwd0, rev)
    compare("forward/seed0 vs batch/random-seed", fwd0, batch)
    compare("forward/seed0 vs noisy-process/random-seed", fwd0, noisy)
    compare("batch/random-seed vs batch/seed7", batch, batch2)
    if len(sys.argv) > 3:
        other = run_worker(sys.argv[3], stdlib, "forward", "0", tmp / "e.json")
        compare("same stdlib, other interpreter build", fwd0, other)


if __name__ == "__main__":
    main()
