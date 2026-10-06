"""Canonical marshal rewriting (plan decision D16)."""

import marshal
import struct
import sys
import time
import uuid
from types import CodeType

import pytest

from fsbpyr.bytecode import MAX_DEPTH, BytecodeError, canonicalize, dumps

SAMPLE = '''
"""Module docstring."""
import os
BIG = 2 ** 100
NEG = -(2 ** 70)
VALUES = (1, 2.5, 3j, b"raw", "text", "ünïcödé", None, True, False, ..., frozenset({"a", "b"}))

class Outer:
    class Inner:
        def method(self, x, /, y=1, *, z=2):
            return x[1:2], y if y in {1, 2, 3} else z

def gen():
    yield from range(3)

async def coro():
    return [i async for i in agen()]
'''


def _code(source: str = SAMPLE) -> CodeType:
    return compile(source, "sample.py", "exec", dont_inherit=True)


def test_round_trip_preserves_code_objects() -> None:
    code = _code()

    assert marshal.loads(canonicalize(marshal.dumps(code))) == code


def test_dumps_is_canonicalize_of_marshal_dumps() -> None:
    code = _code()

    assert dumps(code) == canonicalize(marshal.dumps(code))


def test_canonicalize_is_idempotent() -> None:
    once = canonicalize(marshal.dumps(_code()))

    assert canonicalize(once) == once


def test_output_does_not_depend_on_refcounts() -> None:
    first = dumps(_code())
    code = _code()
    held = [code, *code.co_consts, *code.co_names]  # extra references change raw FLAG_REF bits

    assert dumps(code) == first
    assert held  # keep references alive until after the dump


def test_output_does_not_depend_on_previously_interned_strings() -> None:
    cls = f"C_{uuid.uuid4().hex}"
    source = f"class {cls}:\n    class D:\n        pass\nX = '{cls}.D'\n"
    clean = dumps(_code(source))
    held = sys.intern(f"{cls}.D")  # an equal string already interned elsewhere in the process

    noisy_raw = marshal.dumps(_code(source))

    assert canonicalize(noisy_raw) == clean
    assert held


def test_frozenset_elements_are_sorted_canonically() -> None:
    a, b = b"z\x01a", b"z\x01b"
    forward = b">" + struct.pack("<i", 2) + a + b
    backward = b">" + struct.pack("<i", 2) + b + a

    assert canonicalize(forward) == canonicalize(backward)
    assert marshal.loads(canonicalize(backward)) == frozenset({"a", "b"})


def test_equal_strings_are_merged_and_interned_if_any_occurrence_is() -> None:
    interned = b"Z\x03abc"
    plain = b"z\x03abc"
    stream = b")\x02" + plain + interned

    result = canonicalize(stream)

    assert result == b")\x02" + bytes([ord("Z") | 0x80]) + b"\x03abc" + b"r" + struct.pack("<i", 0)
    assert marshal.loads(result) == ("abc", "abc")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "truncated"),
        (b"z\x05ab", "truncated"),
        (b"{0", "unsupported"),
        (b"NN", "trailing"),
        (b"r" + struct.pack("<i", 0), "reference"),
        (b"l" + struct.pack("<i", 2) + b"\x01\x00", "truncated"),
        (b"(" + struct.pack("<i", -1), "length"),
    ],
)
def test_malformed_streams_raise(data: bytes, message: str) -> None:
    with pytest.raises(BytecodeError, match=message):
        canonicalize(data)


def test_recursive_reference_is_rejected() -> None:
    # tuple flagged as ref 0 that contains a reference to itself
    data = b"\xa9\x01r" + struct.pack("<i", 0)

    with pytest.raises(BytecodeError, match="recursive"):
        canonicalize(data)


def _nested_frozensets(depth: int) -> bytes:
    return b">" + struct.pack("<i", 1) + (_nested_frozensets(depth - 1) if depth > 1 else b"N")


def test_nested_sets_canonicalize_in_linear_time() -> None:
    start = time.perf_counter()

    result = canonicalize(_nested_frozensets(MAX_DEPTH))

    assert time.perf_counter() - start < 1.0
    assert canonicalize(result) == result


@pytest.mark.parametrize(
    "data",
    [
        b">" + struct.pack("<i", 1),  # prefix, completed below
        b")\x01",
    ],
    ids=["frozenset", "tuple"],
)
def test_nesting_beyond_limit_is_rejected(data: bytes) -> None:
    stream = data * (MAX_DEPTH + 2) + b"N"

    with pytest.raises(BytecodeError, match="nesting"):
        canonicalize(stream)


def test_nesting_limit_covers_compile_output() -> None:
    nested = "1"
    for _ in range(50):  # 50-deep tuple constant; the deepest stdlib module nests 11 levels
        nested = f"({nested},)"
    code = _code(f"x = {nested}\n")

    assert marshal.loads(canonicalize(marshal.dumps(code))) == code
