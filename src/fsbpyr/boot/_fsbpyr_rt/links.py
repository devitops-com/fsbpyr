"""Links to the executable made on its target (plan, Phase 12, owner decision 2026-10-25).

``FSBPYR_INSTALL_LINKS=DIR app`` makes in DIR a symlink per command, naming the executable's
real path (absolute, so DIR can be anywhere), prints what it did and exits without running the
app. A command that is a script file of the project (its target is ``_fsbpyr_scripts.name``
without an attribute) also gets ``name.py``, so a crontab, unit or NRPE line that runs
``DIR/name.py`` keeps working: a start strips ``.py`` (commands.py). Idempotent: a link that
names this executable, or this executable itself, is left as it is. Anything else in the way (a
file, a directory, a link to something else) is never replaced: it is reported, the other links
are still made, and the exit status is 1. A DIR that is missing or cannot be written is one
error before any link, status 1. No argument form exists: under a ``sudo <binary> *`` rule a
caller could make root-owned links anywhere; sudo's ``env_reset`` (a default) drops
``FSBPYR_*``. Imported only when the variable is set.
"""

import os
import sys

from _fsbpyr_rt.commands import parse  # type: ignore[import-not-found]

SCRIPTS = "_fsbpyr_scripts."  # the root of a project's own files (loose.py)
FAILURE = 1


def names(entrypoint: str, app_name: str) -> list[str]:
    """The link names: each command's, and ``<command>.py`` for a command that is a script."""
    commands = parse(entrypoint) if "\n" in entrypoint else {app_name: entrypoint}
    found = []
    for name, target in commands.items():
        found.append(name)
        if target.startswith(SCRIPTS) and ":" not in target:
            found.append(f"{name}.py")
    return found


def _states(path: str, real: str) -> bool:
    """Whether ``path`` already is a link to ``real``, or ``real`` itself."""
    if os.path.islink(path):
        return os.path.realpath(path) == real
    return os.path.exists(path) and os.path.samefile(path, real)


def _link(path: str, real: str) -> str | None:
    """Make the link; None if made or present, else why not."""
    if os.path.lexists(path):
        if _states(path, real):
            return None
        return f"{path} exists and is not a link to {real}; it is kept"
    try:
        os.symlink(real, path)
    except FileExistsError:
        return None if _states(path, real) else f"{path} appeared meanwhile; it is kept"
    except OSError as exc:
        return f"cannot link {path}: {exc.strerror}"
    return None


def install(directory: bytes, executable: str, entrypoint: str, app_name: str) -> int:
    """Link the commands into ``directory``; the exit status."""
    folder = os.path.abspath(os.fsdecode(directory))
    real = os.path.realpath(executable)
    if not os.path.isdir(folder):
        sys.stderr.write(f"fsbpyr: error: FSBPYR_INSTALL_LINKS: {folder} is not a directory\n")
        return FAILURE
    if not os.access(folder, os.W_OK | os.X_OK):
        sys.stderr.write(f"fsbpyr: error: FSBPYR_INSTALL_LINKS: cannot write to {folder}\n")
        return FAILURE
    status = 0
    for name in names(entrypoint, app_name):
        path = os.path.join(folder, name)
        present = os.path.lexists(path) and _states(path, real)
        if (problem := _link(path, real)) is not None:
            sys.stderr.write(f"fsbpyr: error: {problem}\n")
            status = FAILURE
        else:
            print(f"fsbpyr: {'present' if present else 'linked'} {path} -> {real}")
    return status
