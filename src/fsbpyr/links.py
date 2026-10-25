"""The command links of a multi-command executable: ``<dir>/<command>`` -> the binary's name."""

import contextlib
import os
import secrets
from collections.abc import Iterable
from pathlib import Path


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
