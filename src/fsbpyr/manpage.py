"""The man page, rendered from the click commands that also give the help text and the shell
completions (D40). Help texts set code in backticks; the page sets it in bold."""

import re
from collections.abc import Iterable, Iterator, Sequence

import click

from . import __version__
from .config import CONFIG_KEYS

_CODE = re.compile(r"`([^`]+)`")
_NO_REWRAP = "\b"  # click: a paragraph whose first line is this keeps its lines


# (status, meaning): what `fsbpyr` and the executables it builds exit with.
EXIT_STATUS = (
    ("0", "Success."),
    ("1", "The command failed; the reason is on standard error."),
    ("2", "Usage error: an unknown command or option, or a value the command refuses."),
    ("130", "Interrupted (Ctrl-C)."),
    ("70", "An executable built by fsbpyr refused to start: its integrity check failed (the "
           "file was modified or damaged), it was started with privileges changed "
           "(set-uid or set-gid to another user or group, or file capabilities), it was "
           "started by itself as a Python interpreter unless it was built with "
           "`--interpreter-reexec` or `--no-interpreter-reexec`, the cache directory was "
           "refused (`refusing cache directory <path>`: owned by another user, or others "
           "may write to it), or as many copies of it run already as one user may run at "
           "once (FSBPYR_MAX_INSTANCES). "
           "Printed by the built app, not by `fsbpyr`."),
)

# (variable, meaning): the ones fsbpyr or an executable it built reads; a test keeps this
# list in step with the source.
ENVIRONMENT = (
    ("FSBPYR_CACHE_DIR", "The cache directory of fsbpyr and of built executables, in place of "
                         "the ones under FILES."),
    ("FSBPYR_GC", "Built executables only. `1`: collect the app's cache instead of running "
                  "the app. `0`: never collect it in the background."),
    ("FSBPYR_INSTALL_LINKS", "Built executables only. A directory: link the app's commands into "
                             "it (absolute symlinks to the executable, and `x.py` for each "
                             "script `x`), print what was done and exit instead of running the "
                             "app; exit 1 if a file or another link is in the way."),
    ("FSBPYR_TRACE", "Built executables only. Set: print what the run loaded and extracted, "
                     "and how long the integrity check took, as JSON on standard error."),
    ("FSBPYR_IMPORTTIME", "Built executables only. Set: print CPython's import-time table."),
    ("FSBPYR_MAX_INSTANCES", "Built executables only. How many copies of the app may run at "
                             "once for one user (at most 4096; `0`: no limit); by default 4 per "
                             "CPU and 16, at least 64 and at most 1024."),
    ("FSBPYR_SELFTEST", "Built executables only. `natives`: check that this machine provides "
                        "every library and symbol version the native extensions need, print "
                        "one line per missing one and exit 1 if any (glibc targets). Any other "
                        "value: import the entry point, check it, and exit without running "
                        "the app."),
    ("FSBPYR_SIGNING_KEY", "The `.key` file that signs: `sign` reads it when `--signing-key` is "
                           "not given, and with either a `build` signs its output."),
    ("FSBPYR_SIGNING_PASSPHRASE", "The passphrase of a signing key, for jobs without a terminal; "
                                  "otherwise it is asked for on the terminal, never read from "
                                  "standard input. Empty counts as unset. `build` removes both "
                                  "signing variables from its environment before it starts any "
                                  "process."),
    ("XDG_CONFIG_HOME", "The base of the keys `key trust` saves when it is an absolute path; "
                        "otherwise ~/.config."),
    ("XDG_CACHE_HOME", "The base of the cache directories when it is an absolute path; "
                       "otherwise `~/.cache` is used."),
)

# (file, meaning)
FILES = (
    ("pyproject.toml", "The project's metadata and the `[tool.fsbpyr]` table (CONFIGURATION)."),
    ("${XDG_CACHE_HOME:-~/.cache}/fsbpyr", "fsbpyr's own cache: runtimes, Python "
                                           "distributions, sysroots and downloads, among them "
                                           "the fsbpyr variants that a build for another "
                                           "Python fetches."),
    ("${XDG_CACHE_HOME:-~/.cache}/<app>", "What a built executable extracted on first use."),
    (".fsbpyr-cache", "The marker in a cache root: only a root that has it is collected."),
    ("${XDG_CONFIG_HOME:-~/.config}/fsbpyr/trusted/<project>.pub",
     "The key `key trust` saved for a project, which `verify` checks with."),
)


def render(group: click.Group, name: str, examples: Sequence[tuple[str, str]] = ()) -> str:
    """The page for man(7): the group's own options, every command below it, then the
    examples, each a command line and what it does."""
    ctx = click.Context(group, info_name=name)
    lines = [
        f'.TH {name.upper()} 1 "" "{name} {__version__}" "User Commands"',
        ".SH NAME",
        f"{name} \\- {_text(group.get_short_help_str(limit=200).rstrip('.'))}",
        ".SH SYNOPSIS",
        f"\\fB{name}\\fR {_text(' '.join(group.collect_usage_pieces(ctx)))}",
        ".SH DESCRIPTION",
        *_paragraphs(group.help),
        ".SH OPTIONS",
        *_options(ctx),
        ".SH COMMANDS",
    ]
    for sub in _commands(ctx):
        usage = " ".join(sub.command.collect_usage_pieces(sub))
        lines += [f'.SS "{_text(sub.command_path)} {_text(usage)}"', *_paragraphs(sub.command.help)]
        lines += _options(sub)
    lines += [".SH CONFIGURATION", *_paragraphs(
        "Options on the command line override the `[tool.fsbpyr]` table of `pyproject.toml`, "
        "which takes these keys:")]
    lines += _definitions((key.name, key.doc) for key in CONFIG_KEYS)
    lines += [".SH EXIT STATUS", *_definitions(EXIT_STATUS)]
    lines += [".SH ENVIRONMENT", *_definitions(ENVIRONMENT)]
    lines += [".SH FILES", *_definitions(FILES)]
    if examples:
        lines += [".SH EXAMPLES", ".nh", ".ad l"]  # command lines: no hyphens added, no gaps
    for command, text in examples:
        lines += [".TP", f"\\fB{_text(command)}\\fR", _text(text)]
    return "\n".join(lines) + "\n"


def _definitions(rows: Iterable[tuple[str, str]]) -> list[str]:
    lines: list[str] = []
    for term, text in rows:
        lines += [".TP", f"\\fB{_text(term)}\\fR", _text(text)]
    return lines


def _commands(ctx: click.Context) -> Iterator[click.Context]:
    group = ctx.command
    if not isinstance(group, click.Group):
        return
    for name in group.list_commands(ctx):
        command = group.get_command(ctx, name)
        if command is None or command.hidden:
            continue
        sub = click.Context(command, info_name=name, parent=ctx)
        yield sub
        yield from _commands(sub)


def _paragraphs(text: str | None) -> list[str]:
    lines: list[str] = []
    for paragraph in (text or "").split("\n\n"):
        rows = paragraph.splitlines()
        if rows[:1] == [_NO_REWRAP]:
            lines += [".PP", ".nf", *map(_text, rows[1:]), ".fi"]
        elif rows:
            lines += [".PP", _text(" ".join(paragraph.split()))]
    return lines


def _options(ctx: click.Context) -> list[str]:
    """Each option as --help shows it; -h/--help only once, for the group."""
    help_option = ctx.command.get_help_option(ctx)
    lines: list[str] = []
    for param in ctx.command.get_params(ctx):
        record = param.get_help_record(ctx)
        if record is None or (param is help_option and ctx.parent is not None):
            continue
        flags, text = record
        lines += [".TP", _flags(flags), _text(text)]
    return lines


def _flags(flags: str) -> str:
    """``-o, --output PATH``: option names in bold, values in italics."""
    words = []
    for word in flags.split(" "):
        name, comma = (word[:-1], ",") if word.endswith(",") else (word, "")
        if name == "/":
            words.append(name)
        else:
            font = "B" if name.startswith("-") else "I"
            words.append(f"\\f{font}{_text(name)}\\fR{comma}")
    return " ".join(words)


def _text(text: str) -> str:
    text = _CODE.sub(r"\\fB\1\\fR", text.replace("\\", "\\e").replace("-", "\\-"))
    return "\\&" + text if text.startswith((".", "'")) else text
