"""Runs black itself: its mypyc-compiled modules (0.17.1 broke on one)."""

import black


def main() -> None:
    black.patched_main()
