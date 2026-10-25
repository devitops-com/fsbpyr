"""An executable with several commands (plan §11, owner decision 2026-10-25).

A build with several console scripts stores its entry point as lines ``name=target``, a target
being ``module`` or ``module:function`` as in a single-command build. A start runs the command
that ``basename(argv[0])`` names (a link, or the executable under that name), then the same
with ``.py`` stripped, then, as ``<exe> COMMAND args...``, the command ``argv[1]`` names
exactly; ``sys.argv`` then loses its first item, so the command sees its own name as through a
link. Otherwise the commands are listed on stderr and the exit status is 2. An argument is
never a path here: ``<exe> /path/to/command`` is the restart idiom or a file argument.
Only the entry point's text and ``argv`` are read.
"""

import sys

EXIT_USAGE = 2


def parse(table: str) -> dict[str, str]:
    """The command names with their targets, in the table's order."""
    return dict(line.partition("=")[::2] for line in table.split("\n"))


def named(commands: dict[str, str], argv0: str) -> str | None:
    """The command ``basename(argv0)`` names, as it is or without ``.py``; None if neither."""
    base = argv0.rpartition("/")[2]
    for name in (base, base.removesuffix(".py")):
        if name in commands:
            return name
    return None


def is_sibling(commands: dict[str, str], argv0: str, executable: str) -> bool:
    """Whether ``argv0`` names a command under another name than the executable's own: a link
    to the executable (D44 does not apply, owner decision 2026-10-25). A hard link keeps the
    check: there ``sys.executable`` is the link's own path."""
    return named(commands, argv0) is not None and (
        argv0.rpartition("/")[2] != executable.rpartition("/")[2])


def find(commands: dict[str, str], argv: list[str]) -> tuple[str, bool] | None:
    """(the target, whether ``argv`` loses its first item) for the command ``argv`` starts, or
    None if it starts none."""
    if (name := named(commands, argv[0] if argv else "")) is not None:
        return commands[name], False
    if len(argv) > 1 and argv[1] in commands:
        return commands[argv[1]], True
    return None


def choose(commands: dict[str, str], argv: list[str]) -> str:
    """The target of the command ``argv`` starts; ``argv`` is shifted for ``<exe> COMMAND``.
    Without a command the usage goes to stderr and this exits 2."""
    found = find(commands, argv)
    if found is not None:
        target, shift = found
        if shift:
            argv[:] = argv[1:]
        return target
    program = argv[0].rpartition("/")[2] if argv else "COMMAND"
    unknown = f"{program}: unknown command {argv[1]!r}\n" if len(argv) > 1 else ""
    listing = "\n".join(f"  {name}" for name in commands)
    sys.stderr.write(f"{unknown}usage: {program} COMMAND [ARGUMENT...]\ncommands:\n{listing}\n")
    raise SystemExit(EXIT_USAGE)
