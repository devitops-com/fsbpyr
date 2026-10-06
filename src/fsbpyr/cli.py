"""Command-line entry point. Phase 1 provides only ``--version``; subcommands follow."""

import argparse
from collections.abc import Sequence

from . import __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fsbpyr", description="Package Python CLI projects as fast single-file executables."
    )
    parser.add_argument("--version", action="version", version=f"fsbpyr {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    parser.parse_args(argv)
    parser.print_usage(__import__("sys").stderr)
    return 2
