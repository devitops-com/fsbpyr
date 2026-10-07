"""Command-line entry point: ``fsbpyr build`` and ``fsbpyr runtime build|list``."""

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
    _add_build(commands)
    runtime = commands.add_parser("runtime", help="manage runtimes (native stub + stdlib)")
    actions = runtime.add_subparsers(dest="action", required=True)
    build = actions.add_parser("build", help="fetch CPython, link launcher, compile stdlib")
    build.add_argument(
        "--python", default=DEFAULT_PYTHON, help="major.minor (default: %(default)s)"
    )
    build.add_argument("--clang", type=Path, help="clang to use (default: search PATH)")
    actions.add_parser("list", help="list runtimes in the cache")
    return parser


def _add_build(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    build = commands.add_parser("build", help="build a single-file executable from a project")
    build.add_argument("project", nargs="?", type=Path, default=Path("."), help="project directory")
    build.add_argument("--entry", help="console script name, 'module' or 'module:function'")
    build.add_argument("-o", "--output", type=Path, help="executable path (default: dist/NAME)")
    build.add_argument("--requirements", help="pinned requirements file (pip projects)")
    build.add_argument("--extra", dest="extras", action="append", help="project extra (repeat)")
    build.add_argument("--allow-system-lib", dest="allowed_system_libs", action="append",
                       metavar="SONAME", help="allow this host library (repeat)")  # fmt: skip
    build.add_argument("--interpreter", type=Path, help="Python 3.14 that runs pip (without uv)")
    build.add_argument("--offline", action="store_true", default=None, help="no network access")
    build.add_argument("--no-source", dest="include_source", action="store_false", default=None,
                       help="do not keep Python sources in the executable")  # fmt: skip
    build.add_argument("--no-smoke", dest="smoke", action="store_false", default=None,
                       help="skip running the executable once in selftest mode")  # fmt: skip


def _build(args: argparse.Namespace) -> int:
    from .assemble import AssemblyError  # noqa: PLC0415  keep --version fast
    from .build import BuildError, build  # noqa: PLC0415
    from .classify import ClassifyError  # noqa: PLC0415
    from .closure import ClosureError  # noqa: PLC0415
    from .config import ConfigError, load_config  # noqa: PLC0415
    from .project import Project, ProjectError  # noqa: PLC0415
    from .runtime.registry import RuntimeCacheError  # noqa: PLC0415
    from .staging import StagingError  # noqa: PLC0415

    errors = (AssemblyError, BuildError, ClassifyError, ClosureError, ConfigError, ProjectError,
              RuntimeCacheError, StagingError, OSError)  # fmt: skip
    try:
        config = load_config(
            Project.load(args.project),
            entry=args.entry,
            output=args.output.resolve() if args.output else None,
            requirements=args.requirements,
            extras=tuple(args.extras) if args.extras else None,
            allowed_system_libs=(
                tuple(args.allowed_system_libs) if args.allowed_system_libs else None
            ),
            interpreter=args.interpreter,
            offline=args.offline,
            include_source=args.include_source,
            smoke=args.smoke,
        )
        result = build(args.project, config, log=lambda message: print(f"fsbpyr: {message}"))
    except errors as exc:
        print(f"fsbpyr: error: {exc}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"fsbpyr: warning: {warning}", file=sys.stderr)
    print(f"fsbpyr: built {result.output} ({result.size / 1e6:.1f} MB, entry {result.entrypoint})")
    return 0


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
    if args.command == "build":
        return _build(args)
    if args.command == "runtime":
        return _runtime(args)
    parser.print_usage(sys.stderr)
    return 2
