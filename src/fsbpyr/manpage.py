"""The man page, rendered from the click commands that also give the help text and the shell
completions (D40). Help texts set code in backticks; the page sets it in bold."""

import re
from collections.abc import Iterator

import click

from . import __version__

_CODE = re.compile(r"`([^`]+)`")
_NO_REWRAP = "\b"  # click: a paragraph whose first line is this keeps its lines


def render(group: click.Group, name: str) -> str:
    """The page for man(7): the group's own options, then every command below it."""
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
    return "\n".join(lines) + "\n"


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
