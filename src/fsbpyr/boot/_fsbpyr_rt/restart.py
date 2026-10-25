"""The ``[sys.executable] + sys.argv`` restart idiom (plan D22, D44).

gunicorn's USR2 re-exec and Django's autoreloader start ``[sys.executable, <interpreter flags>,
sys.argv[0], *args]``. In a venv that is ``python /venv/bin/app args``: the script runs with
``sys.argv == [app, args]``. In a built app ``sys.executable`` is the app itself, so the child
would get the app's own path as an extra first argument. A start whose first argument after the
interpreter flags names this very executable (the same file as /proc/self/exe, so a symlink or
multicall name counts) is that restart: the flags and that argument are dropped, the argument
kept, as given, as ``sys.argv[0]``.

No D22 parent check: this removes an argument that names this very executable, which gives the
caller nothing it could not do with ``app args``; and ``app /path/to/app args`` typed in a shell
has no other sane meaning. Only real interpreter flags may precede it (-v, -X x, -W x, ...);
any other option means the app's own arguments and nothing is rewritten.
Nothing is imported, run or opened from the arguments (D44).
"""

import posix

_TAKES_VALUE = ("-X", "-W")  # "-X dev" is two arguments, "-Xdev" one


def after_flags(argv: list[str]) -> int | None:
    """The index of the first argument after interpreter flags, or None if ``-`` or nothing
    follows them."""
    i = 1
    while i < len(argv):
        if argv[i] in ("-m", "-c") or not argv[i].startswith("-"):
            return i
        if argv[i] == "-":
            return None
        i += 2 if argv[i] in _TAKES_VALUE else 1
    return None


# What subprocess._args_from_interpreter_flags and the autoreloaders pass; any other leading
# option (``app --output /path/to/app``) is the app's own and means this is no restart.
_PLAIN_FLAGS = frozenset(
    [
        *("-B", "-b", "-bb", "-d", "-E", "-I", "-O", "-OO"),
        *("-P", "-q", "-R", "-s", "-S", "-u", "-v", "-vv"),
    ]
)


def _only_interpreter_flags(argv: list[str], end: int) -> bool:
    i = 1
    while i < end:
        arg = argv[i]
        if arg in _TAKES_VALUE:
            i += 2
        elif arg in _PLAIN_FLAGS or arg[:2] in _TAKES_VALUE:
            i += 1
        else:
            return False
    return True


def restart_argv(argv: list[str]) -> list[str] | None:
    """``argv`` without the interpreter flags and without the executable's own name, or None
    if the first argument after the flags is not this executable."""
    i = after_flags(argv)
    if i is None or argv[i] in ("-m", "-c") or not _only_interpreter_flags(argv, i):
        return None
    try:
        named, own = posix.stat(argv[i]), posix.stat("/proc/self/exe")
    except (OSError, ValueError):  # not a file, or an argument that is no path
        return None
    if (named.st_dev, named.st_ino) != (own.st_dev, own.st_ino):
        return None
    return argv[i:]
