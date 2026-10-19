"""Doubles its argument in a thread the extension starts."""

import sys

from cext import _native


def main() -> int:
    print("cext", _native.twice(int(sys.argv[1]) if sys.argv[1:] else 21))
    return 0
