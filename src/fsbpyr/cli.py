"""Command-line entry point: ``fsbpyr build``, ``inspect``, ``runtime build|list`` and
``cache gc``."""

import argparse
import errno
import functools
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

DEFAULT_PYTHON = "3.14"


class _Version(argparse.Action):
    """--version: computed only when asked, since a source checkout runs git for it."""

    def __init__(self, option_strings: Sequence[str], dest: str, **_: Any) -> None:
        super().__init__(option_strings, dest, nargs=0, help="show the version and exit")

    def __call__(self, parser: argparse.ArgumentParser, *_: Any) -> None:
        from .vcs import own_version  # noqa: PLC0415  keep the other commands free of git

        print(own_version())
        parser.exit()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fsbpyr", description="Package Python CLI projects as fast single-file executables."
    )
    parser.add_argument("--version", action=_Version)
    commands = parser.add_subparsers(dest="command")
    _add_build(commands)
    inspect = commands.add_parser("inspect", help="show what an executable was built from")
    inspect.add_argument("binary", type=Path, help="executable built by fsbpyr")
    inspect.add_argument("--json", action="store_true", help="machine-readable output")
    inspect.add_argument("--verify", action="store_true", help="recompute the file's digest")
    inspect.add_argument("--all-modules", action="store_true",
                         help="list every module, not only top-level names")  # fmt: skip
    runtime = commands.add_parser("runtime", help="manage runtimes (native stub + stdlib)")
    actions = runtime.add_subparsers(dest="action", required=True)
    build = actions.add_parser("build", help="fetch CPython, link launcher, compile stdlib")
    build.add_argument(
        "--python", default=DEFAULT_PYTHON, help="major.minor (default: %(default)s)"
    )
    build.add_argument("--clang", type=Path, help="clang to use (default: search PATH)")
    actions.add_parser("list", help="list runtimes in the cache")
    cache = commands.add_parser("cache", help="manage fsbpyr's caches")
    cache_actions = cache.add_subparsers(dest="action", required=True)
    cache_actions.add_parser("gc", help="remove what nothing uses any more: from the tool's cache "
                             "and every app cache (D38)")  # fmt: skip
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
    build.add_argument("--fs-package", dest="fs_packages", action="append", metavar="PACKAGE",
                       help="package whose files must exist on disk (repeat)")  # fmt: skip
    build.add_argument("--offline", action="store_true", default=None, help="no network access")
    build.add_argument("--no-source", dest="include_source", action="store_false", default=None,
                       help="leave out .py sources (bytecode only): smaller, but tracebacks show "
                            "no source lines and inspect.getsource() fails")  # fmt: skip
    compression = build.add_mutually_exclusive_group()
    compression.add_argument("--fast-compression", action="store_true", default=None,
                             help="zstd level 3, not 9: faster, larger")  # fmt: skip
    compression.add_argument("--best-compression", action="store_true", default=None,
                             help="native libraries as xz -6: smaller; slower to build and to "
                                  "extract on first use")  # fmt: skip
    build.add_argument("--hints", action="store_true", default=None,
                       help="also list packages that may read files next to __file__")  # fmt: skip
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
            fs_packages=tuple(args.fs_packages) if args.fs_packages else None,
            offline=args.offline,
            include_source=args.include_source,
            smoke=args.smoke,
            hints=args.hints,
            fast_compression=args.fast_compression,
            best_compression=args.best_compression,
        )
        result = build(args.project, config, log=lambda message: print(f"fsbpyr: {message}"))
    except errors as exc:
        print(f"fsbpyr: error: {exc}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"fsbpyr: warning: {warning}", file=sys.stderr)
    glibc = f", glibc >= {result.glibc}" if result.glibc else ""
    print(f"fsbpyr: built {result.output} ({result.size / 1e6:.1f} MB, entry "
          f"{result.entrypoint}{glibc})")  # fmt: skip
    return 0


def _inspect(args: argparse.Namespace) -> int:
    from .inspection import InspectError, as_json, as_text, read  # noqa: PLC0415  fast --version

    try:
        report = read(args.binary, verify=args.verify)
    except InspectError as exc:
        print(f"fsbpyr: error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(as_json(report, all_modules=args.all_modules))
    else:
        print("\n".join(as_text(report, all_modules=args.all_modules)))
    return 0


def _runtime(args: argparse.Namespace) -> int:
    from .runtime import (  # noqa: PLC0415  keep --version fast
        pbs,
        registry,
        stdlib,
        stub,
        sysroot,
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
        sysroot.SysrootError,
        OSError,
    ) as exc:
        print(f"fsbpyr: error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cache(args: argparse.Namespace) -> int:
    """``fsbpyr cache gc``: the tool's cache, then every app cache found (D38); 1 if anything
    could not be removed."""
    from .boot._fsbpyr_rt import gc  # noqa: PLC0415  keep --version fast
    from .runtime import collect, registry  # noqa: PLC0415

    del args  # one action
    tool = registry.cache_root()
    runs = [(tool, functools.partial(collect.collect_tool, tool))]
    runs += [(root, functools.partial(gc.collect, str(root))) for root in collect.app_caches()]
    failed = False
    for root, run in runs:
        try:
            report = run()
        except OSError as exc:  # the root itself: the next one is collected all the same
            print(f"fsbpyr: collecting {root} failed: {exc}", file=sys.stderr)
            failed = True
            continue
        print(report.text())
        failed |= bool(report.errors)
    return 1 if failed else 0


def _collect_when_due() -> None:
    """The tool's cache, collected at a command's start once a day (D38); a failure is reported
    and never stops the command."""
    from .runtime import collect, registry  # noqa: PLC0415  keep --version fast

    try:
        collect.collect_when_due(registry.cache_root())
    except RuntimeError:
        pass  # no home directory: no cache to collect
    except PermissionError:
        pass  # a cache we may not write (read-only, or not ours): not ours to collect
    except OSError as exc:
        if exc.errno != errno.EROFS:
            print(f"fsbpyr: collecting the cache failed: {exc}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command in ("build", "inspect", "runtime"):
        _collect_when_due()
    if args.command == "cache":
        return _cache(args)
    if args.command == "build":
        return _build(args)
    if args.command == "inspect":
        return _inspect(args)
    if args.command == "runtime":
        return _runtime(args)
    parser.print_usage(sys.stderr)
    return 2
