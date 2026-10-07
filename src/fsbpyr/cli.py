"""Command-line entry point: ``fsbpyr runtime build|list`` (``fsbpyr build`` follows in Phase 4)."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__

DEFAULT_PYTHON = "3.14"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fsbpyr", description="Package Python CLI projects as fast single-file executables."
    )
    parser.add_argument("--version", action="version", version=f"fsbpyr {__version__}")
    commands = parser.add_subparsers(dest="command")
    runtime = commands.add_parser("runtime", help="manage runtimes (native stub + stdlib)")
    actions = runtime.add_subparsers(dest="action", required=True)
    build = actions.add_parser("build", help="fetch CPython, link launcher, compile stdlib")
    build.add_argument(
        "--python", default=DEFAULT_PYTHON, help="major.minor (default: %(default)s)"
    )
    build.add_argument("--clang", type=Path, help="clang to use (default: search PATH)")
    actions.add_parser("list", help="list runtimes in the cache")
    return parser


def _runtime(args: argparse.Namespace) -> int:
    from .runtime import (  # noqa: PLC0415  keep --version fast
        pbs,
        registry,
        stdlib,
        stub,
        toolchain,
    )

    if args.action == "list":
        for runtime_id, version in registry.list_runtimes():
            print(f"{runtime_id}  Python {version}")
        return 0
    try:
        registry.build_runtime(args.python, clang=args.clang)
    except (
        LookupError,
        registry.RuntimeCacheError,
        toolchain.ToolchainError,
        pbs.PbsError,
        stub.StubBuildError,
        stdlib.StdlibError,
        OSError,
    ) as exc:
        print(f"fsbpyr: error: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "runtime":
        return _runtime(args)
    parser.print_usage(sys.stderr)
    return 2
