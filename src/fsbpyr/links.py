"""The command links of a multi-command executable: ``<dir>/<command>`` -> the binary's name."""

import contextlib
import os
import re
import secrets
import shlex
from collections.abc import Iterable
from pathlib import Path

COMMAND_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*")  # a file name and a command line word


class LinkError(RuntimeError):
    """A command's name is taken by a file that is not a link."""


def link_paths(binary: Path, commands: Iterable[str]) -> list[Path]:
    """Where the links go: beside ``binary``, one per command; a command named like the binary
    needs none, the binary itself serves it."""
    return [binary.parent / name for name in commands if name != binary.name]


def check_links(binary: Path, commands: Iterable[str]) -> None:
    """Fail naming the first link path held by a file or directory (a symlink is replaced)."""
    for path in link_paths(binary, commands):
        if os.path.lexists(path) and not path.is_symlink():
            raise LinkError(f"cannot link command {path.name!r}: {path} exists and is not a "
                            "symlink; remove it or build with another -o")  # fmt: skip


def make_links(binary: Path, commands: Iterable[str]) -> list[Path]:
    """Link each command to ``binary`` by its name alone, so the directory can move: a
    temporary link is renamed into place, which replaces a symlink and never a file."""
    check_links(binary, commands)
    made = []
    for path in link_paths(binary, commands):
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        try:
            os.symlink(binary.name, temporary)
            check_links(binary, [path.name])  # nothing but a symlink is renamed over
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                temporary.unlink()
            raise
        made.append(path)
    return made


def deploy_recipe(binary: Path, commands: Iterable[str]) -> list[str]:
    """The lines that say how to install a build of loose scripts on a host: the binary, one
    link per command, and what a caller that ran ``python3 x.py`` runs instead."""
    name = shlex.quote(binary.name)
    lines = ["to deploy, copy the file and link it by each command's name (DEST: a directory "
             "on the host):",
             f'  install -m 755 {shlex.quote(str(binary))} "$DEST"/{name}']  # fmt: skip
    if linked := [command for command in commands if command != binary.name]:
        lines.append(f"  for c in {' '.join(map(shlex.quote, linked))}; do "
                     f'ln -sf {name} "$DEST/$c"; done')  # fmt: skip
    lines.append(f'or let the copy make the links (and x.py for each script x) on the host: '
                 f'FSBPYR_INSTALL_LINKS="$DEST" "$DEST"/{name}')  # fmt: skip
    lines.append("a crontab, unit or agent line such as `python3 $DEST/x.py` must become "
                 "`$DEST/x` or `$DEST/x.py` run directly: the link is an executable, not a "
                 "Python file")  # fmt: skip
    return lines
