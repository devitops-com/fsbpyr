"""Doubles its argument, parsed and doubled in a thread by the extension."""

import sys

from cext import _native


def main() -> int:
    print("cext", _native.twice(sys.argv[1] if sys.argv[1:] else "21"))
    return 0
