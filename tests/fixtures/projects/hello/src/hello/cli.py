"""Prints its arguments; exits with the first one if it is a number."""

import sys


def main() -> int:
    print("hello", sys.argv[1:])
    return int(sys.argv[1]) if sys.argv[1:] and sys.argv[1].isdigit() else 0
