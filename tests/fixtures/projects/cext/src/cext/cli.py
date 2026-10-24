"""Doubles its argument, parsed and doubled in a thread by the extension."""

import ctypes
import importlib.machinery
import os
import sys

from cext import _native


def main() -> int:
    if sys.argv[1:] == ["--ctypes"]:  # its own library by bare file name, as PyTables loads one
        name = "_native" + importlib.machinery.EXTENSION_SUFFIXES[0]
        print("ctypes", os.path.basename(ctypes.CDLL(name)._name) == name)
        return 0
    print("cext", _native.twice(sys.argv[1] if sys.argv[1:] else "21"))
    return 0
