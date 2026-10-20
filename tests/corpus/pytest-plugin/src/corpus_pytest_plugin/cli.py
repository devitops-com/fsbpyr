"""Runs pytest with a plugin found through the pytest11 entry-point group."""

import sys

import pytest


def main() -> None:
    sys.exit(pytest.main(["-p", "no:cacheprovider", "-p", "no:terminal", *sys.argv[1:]]))
