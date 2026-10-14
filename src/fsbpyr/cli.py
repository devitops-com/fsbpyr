"""Command-line entry point: ``fsbpyr build``, ``inspect``, ``runtime build|list``, ``cache gc``,
``completion`` and ``man``. The click commands below are the one source of the help text, the
shell completions and the man page (D40)."""

import errno
import functools
import sys
from collections.abc import Sequence
from pathlib import Path

import click
from click.shell_completion import BashComplete, FishComplete, ShellComplete, ZshComplete

DEFAULT_PYTHON = "3.14"
PROG = "fsbpyr"
COMPLETE_VAR = "_FSBPYR_COMPLETE"  # set by the completion scripts when they call fsbpyr
COMPLETERS: dict[str, type[ShellComplete]] = {
    "bash": BashComplete,
    "zsh": ZshComplete,
    "fish": FishComplete,
}


def _show_version(ctx: click.Context, _: click.Parameter, value: bool) -> None:
    """--version: computed only when asked, since a source checkout runs git for it."""
    if not value or ctx.resilient_parsing:
        return
    from .vcs import own_version  # noqa: PLC0415  keep the other commands free of git

    print(own_version())
    ctx.exit()


def _project_values(ctx: click.Context, field: str, incomplete: str) -> list[str]:
    """Completions read from the project being built: its console scripts or extras. Click
    stops parsing at the option whose value is completed, so a project directory named before
    it is still in ``ctx.args``; in the ``--entry=VALUE`` form parsing ends and it is a param."""
    from .project import Project  # noqa: PLC0415  keep --version fast

    directory = Path(ctx.args[0]) if ctx.args else ctx.params.get("project") or Path(".")
    try:
        project = Project.load(directory)
    except Exception:  # whatever fails, a message would land on the shell prompt
        return []
    names = project.scripts if field == "scripts" else project.extras
    return [name for name in names if name.startswith(incomplete)]


def _complete_entry(ctx: click.Context, _: click.Parameter, incomplete: str) -> list[str]:
    return _project_values(ctx, "scripts", incomplete)


def _complete_extra(ctx: click.Context, _: click.Parameter, incomplete: str) -> list[str]:
    return _project_values(ctx, "extras", incomplete)


@click.group(context_settings={"help_option_names": ["-h", "--help"]},
             help="Package Python CLI projects as fast single-file executables.")  # fmt: skip
@click.option("--version", is_flag=True, expose_value=False, is_eager=True,
              callback=_show_version, help="show the version and exit")  # fmt: skip
def cli() -> None:
    pass


@cli.command(
    "build",
    help="build a single-file executable from a project\n\nPROJECT is the project's directory "
    "(default: the current one). Options given here win over `[tool.fsbpyr]` in its "
    "`pyproject.toml`.",
)
@click.argument("project", required=False, default=".",
                type=click.Path(file_okay=False, path_type=Path))  # fmt: skip
@click.option("--entry", metavar="NAME", shell_complete=_complete_entry,
              help="console script name, `module` or `module:function` "
                   "(default: the only script)")  # fmt: skip
@click.option("-o", "--output", type=click.Path(path_type=Path),
              help="where to write the executable (default: `dist/` and the console "
                   "script's name)")  # fmt: skip
@click.option("--requirements", metavar="FILE", type=click.Path(),
              help="pinned requirements file, for pip projects without `pylock.toml`")  # fmt: skip
@click.option("--extra", "extras", metavar="NAME", multiple=True, shell_complete=_complete_extra,
              help="install a project extra (repeat)")  # fmt: skip
@click.option("--fs-package", "fs_packages", metavar="PACKAGE", multiple=True,
              help="a package whose files must exist on disk (repeat)")  # fmt: skip
@click.option("--allow-system-lib", "allowed_system_libs", metavar="SONAME", multiple=True,
              help="let native code use this library from the host (repeat)")  # fmt: skip
@click.option("--offline/--no-offline", default=None,
              help="no network access during the build")  # fmt: skip
@click.option("--source/--no-source", "include_source", default=None,
              help="keep the `.py` sources (default) or leave them out: smaller, but tracebacks "
                   "show no source lines and `inspect.getsource()` fails")  # fmt: skip
@click.option("--fast-compression/--no-fast-compression", default=None,
              help="zstd level 3 instead of 9: a faster build, up to ~8% larger for large apps "
                   "(the standard library's files of 64 KiB and more stay level 9)")  # fmt: skip
@click.option("--best-compression/--no-best-compression", default=None,
              help="native files (extension modules and shared libraries, nothing else) as xz "
                   "-6 instead of zstd 9: about 6.5% smaller for aiagent, no change for "
                   "pure-Python apps, a slower build, and a slower first run, which decodes "
                   "them into the cache; not with `--fast-compression`")  # fmt: skip
@click.option("--hints/--no-hints", default=None,
              help="also list packages that may read files next to `__file__` "
                   "(a guess)")  # fmt: skip
@click.option("--interpreter-reexec/--no-interpreter-reexec", default=None,
              help="let the app's own children started as `sys.executable -m MODULE` or "
                   "`-c CODE` run as Python")  # fmt: skip
@click.option("--smoke/--no-smoke", default=None,
              help="run the new executable once as a smoke test (default) or skip it")  # fmt: skip
def build_command(  # noqa: PLR0913  click passes every option by name
    *,
    project: Path,
    entry: str | None,
    output: Path | None,
    requirements: str | None,
    extras: tuple[str, ...],
    fs_packages: tuple[str, ...],
    allowed_system_libs: tuple[str, ...],
    offline: bool | None,
    include_source: bool | None,
    fast_compression: bool | None,
    best_compression: bool | None,
    hints: bool | None,
    interpreter_reexec: bool | None,
    smoke: bool | None,
) -> int:
    if fast_compression and best_compression:
        raise click.UsageError("--fast-compression and --best-compression exclude each other")
    _collect_when_due()
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
            Project.load(project),
            entry=entry,
            output=output.resolve() if output else None,
            requirements=requirements,
            extras=extras or None,
            allowed_system_libs=allowed_system_libs or None,
            fs_packages=fs_packages or None,
            offline=offline,
            include_source=include_source,
            smoke=smoke,
            hints=hints,
            fast_compression=fast_compression,
            best_compression=best_compression,
            interpreter_reexec=interpreter_reexec,
        )
        result = build(project, config, log=lambda message: print(f"fsbpyr: {message}"))
    except errors as exc:
        print(f"fsbpyr: error: {exc}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"fsbpyr: warning: {warning}", file=sys.stderr)
    glibc = f", glibc >= {result.glibc}" if result.glibc else ""
    print(f"fsbpyr: built {result.output} ({result.size / 1e6:.1f} MB, entry "
          f"{result.entrypoint}{glibc})")  # fmt: skip
    return 0


@cli.command("inspect", short_help="show what an executable was built from",
             help="show what BINARY, an executable built by fsbpyr, was built from")  # fmt: skip
@click.argument("binary", type=click.Path(path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="machine-readable output")
@click.option("--verify", is_flag=True, help="recompute the file's digest")
@click.option("--all-modules", is_flag=True, help="list every module, not only top-level names")
def inspect_command(*, binary: Path, as_json: bool, verify: bool, all_modules: bool) -> int:
    _collect_when_due()
    from .inspection import InspectError, as_text, read  # noqa: PLC0415  fast --version
    from .inspection import as_json as json_text  # noqa: PLC0415

    try:
        report = read(binary, verify=verify)
    except InspectError as exc:
        print(f"fsbpyr: error: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json_text(report, all_modules=all_modules))
    else:
        print("\n".join(as_text(report, all_modules=all_modules)))
    return 0


@cli.group("runtime", help="manage runtimes (native stub + stdlib)")
def runtime_group() -> None:
    pass


@runtime_group.command("build", help="fetch CPython, link launcher, compile stdlib")
@click.option("--python", metavar="X.Y", default=DEFAULT_PYTHON, show_default=True,
              help="the Python version, major.minor")  # fmt: skip
@click.option("--clang", type=click.Path(path_type=Path),
              help="clang to use (default: search PATH)")  # fmt: skip
def runtime_build(*, python: str, clang: Path | None) -> int:
    _collect_when_due()
    from .runtime import (  # noqa: PLC0415  keep --version fast
        pbs,
        registry,
        stdlib,
        stub,
        sysroot,
        toolchain,
    )

    try:
        registry.build_runtime(python, clang=clang)
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


@runtime_group.command("list", help="list runtimes in the cache")
def runtime_list() -> int:
    _collect_when_due()
    from .runtime import registry  # noqa: PLC0415  keep --version fast

    for runtime_id, version in registry.list_runtimes():
        print(f"{runtime_id}  Python {version}")
    return 0


@cli.group("cache", help="manage fsbpyr's caches")
def cache_group() -> None:
    pass


@cache_group.command("gc", help="remove what nothing uses any more: from the tool's cache and "
                     "every app cache")  # fmt: skip
def cache_gc() -> int:
    """``fsbpyr cache gc``: the tool's cache, then every app cache found (D38); 1 if anything
    could not be removed."""
    from .boot._fsbpyr_rt import gc  # noqa: PLC0415  keep --version fast
    from .runtime import collect, registry  # noqa: PLC0415

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


@cli.command(
    "completion",
    short_help="print the shell completion script for SHELL",
    help="print the shell completion script for SHELL (bash, zsh or fish); the shell then asks "
    "fsbpyr for the words, so `--entry` and `--extra` offer the project's console scripts and "
    "extras\n\n"
    "\b\n"
    "fsbpyr completion bash > ~/.local/share/bash-completion/completions/fsbpyr\n"
    "fsbpyr completion zsh > ~/.zfunc/_fsbpyr  # a directory in $fpath, before compinit\n"
    "fsbpyr completion fish > ~/.config/fish/completions/fsbpyr.fish",
)
@click.argument("shell", metavar="SHELL", type=click.Choice(list(COMPLETERS)))
def completion(*, shell: str) -> int:
    print(COMPLETERS[shell](cli, {}, PROG, COMPLETE_VAR).source())
    return 0


# The man page's EXAMPLES: a command line, then what it does. A test runs each one through the
# commands above, so no example can name an option or a command that is gone.
EXAMPLES = (
    ("fsbpyr build",
     "Build the project in the current directory into `dist/`, named after its console "
     "script."),
    ("fsbpyr build ~/src/mytool -o ~/.local/bin/mytool",
     "Build another project, straight into a directory on `PATH`."),
    ("fsbpyr build --entry mytool --extra fancy",
     "Pick one of the project's console scripts, and install the project's extra `fancy` "
     "with it."),
    ("fsbpyr build --no-source --best-compression",
     "The smallest executable: bytecode without the `.py` sources, native libraries as xz. "
     "Tracebacks show no source lines, and the first run takes longer."),
    ("fsbpyr build --source",
     "Keep the sources, for a build to debug, of a project whose `pyproject.toml` sets "
     "`include-source = false`."),
    ("fsbpyr build --fs-package mypkg.templates --allow-system-lib libGL.so.1",
     "Put the files of `mypkg.templates` on disk for code that opens them by path, and let "
     "native code load `libGL.so.1` from the host."),
    ("fsbpyr inspect --verify ~/.local/bin/mytool",
     "Show what an executable was built from, and recompute its digest."),
    ("fsbpyr inspect --json --all-modules dist/mytool",
     "The same report as JSON, with every module listed."),
    ("fsbpyr runtime build",
     "Build the runtime that fsbpyr needs when it runs from a checkout or a wheel: once, with "
     "clang and lld 22 or newer."),
    ("fsbpyr cache gc",
     "Collect fsbpyr's own cache and every app cache now, rather than when the next "
     "collection is due."),
    ("fsbpyr completion bash > ~/.local/share/bash-completion/completions/fsbpyr",
     "Install the completion for bash; new shells pick it up."),
    ("fsbpyr man > ~/.local/share/man/man1/fsbpyr.1",
     "Install this page."),
)  # fmt: skip


@cli.command("man", help="print the man page (roff)\n\n\b\n"
             "fsbpyr man > ~/.local/share/man/man1/fsbpyr.1")  # fmt: skip
def man() -> int:
    from .manpage import render  # noqa: PLC0415  keep --version fast

    print(render(cli, PROG, EXAMPLES), end="")
    return 0


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
    try:
        code: int = cli.main(args=argv, prog_name=PROG, complete_var=COMPLETE_VAR,
                             standalone_mode=False)  # fmt: skip
    except click.ClickException as exc:  # usage errors: 2, as before
        exc.show()
        return exc.exit_code
    except click.Abort:  # click's form of KeyboardInterrupt
        print("fsbpyr: interrupted", file=sys.stderr)
        return 130
    return code
