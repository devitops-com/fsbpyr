"""Canonical bytecode over the real stdlib, and across differently-shaped build processes."""

import json
import marshal
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from fsbpyr.bytecode import canonicalize

STDLIB = Path(sysconfig.get_path("stdlib"))
EXCLUDED = {"test", "idlelib", "tkinter", "turtledemo", "__pycache__", "site-packages"}
SENSITIVE = ["argparse.py", "inspect.py", "weakref.py", "asyncio/events.py"]

WORKER = r"""
import hashlib, json, marshal, sys
from pathlib import Path
from fsbpyr.bytecode import canonicalize
mode, stdlib, names = sys.argv[1], Path(sys.argv[2]), sys.argv[3:]
if mode == "noisy":
    import argparse, asyncio, inspect, weakref  # noqa: F401
compile_one = lambda n: compile((stdlib / n).read_bytes(), n, "exec", dont_inherit=True)
codes = [(n, compile_one(n)) for n in names] if mode == "batch" else None
out = {}
for n in names:
    code = dict(codes)[n] if codes else compile_one(n)
    raw = marshal.dumps(code)
    out[n] = [hashlib.sha256(raw).hexdigest(), hashlib.sha256(canonicalize(raw)).hexdigest()]
print(json.dumps(out))
"""


def _stdlib_sources() -> list[Path]:
    return [
        p
        for p in sorted(STDLIB.rglob("*.py"))
        if not EXCLUDED.intersection(p.relative_to(STDLIB).parts[:-1])
    ]


def _worker(mode: str, names: list[str], seed: str) -> dict[str, list[str]]:
    result = subprocess.run(
        [sys.executable, "-I", "-c", WORKER, mode, str(STDLIB), *names],
        check=True,
        capture_output=True,
        text=True,
        env={"PYTHONHASHSEED": seed, "PYTHONPATH": str(Path(__file__).parents[2] / "src")},
    )
    parsed: dict[str, list[str]] = json.loads(result.stdout)
    return parsed


@pytest.mark.slow
def test_whole_stdlib_round_trips() -> None:
    failures = []
    for path in _stdlib_sources():
        try:
            code = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
        except SyntaxError:
            continue
        canon = canonicalize(marshal.dumps(code))
        if marshal.loads(canon) != code or canonicalize(canon) != canon:
            failures.append(path)
    assert failures == []


@pytest.mark.slow
def test_canonical_output_is_identical_across_build_process_shapes() -> None:
    names = [*SENSITIVE, "json/decoder.py", "email/message.py", "dataclasses.py"]

    clean = _worker("clean", names, "0")
    batch = _worker("batch", names, "123")
    noisy = _worker("noisy", list(reversed(names)), "random")

    # sanity: modules loaded in the build process perturb raw marshal output of these files
    assert all(clean[n][0] != noisy[n][0] for n in SENSITIVE)
    for name in names:
        assert clean[name][1] == batch[name][1] == noisy[name][1], name
