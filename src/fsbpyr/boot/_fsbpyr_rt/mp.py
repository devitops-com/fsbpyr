"""Children that multiprocessing starts by re-executing ``sys.executable`` (plan §3.3, D22).

spawn, forkserver and the resource tracker run ``<exe> <flags> -c <code> [args]``. In a
single-file app ``sys.executable`` is the app, so those three command lines are recognized
here, exactly, and their arguments decoded with ``ast.literal_eval``; argv is never executed.

Recognizing the command is not enough: the functions it names unpickle data from the given
fd, import modules or run files. An app that may be started with untrusted arguments (a sudo
rule, say) must not become a Python interpreter, so a child runs only when its parent process
runs this very executable, its fds are not stdio, and it asks for nothing an fsbpyr parent
never sends (a ``main_path``, a foreign ``sys_path``).
"""

import ast
import importlib
import os
import re
import sys

_SPAWN = re.compile(r"from multiprocessing\.spawn import spawn_main; spawn_main\((.*)\)")
_FORKSERVER = re.compile(
    r"import sys; from multiprocessing\.forkserver import main; "
    r"main\((\d+), (\d+), (\[[^\]]*\]), sys_argv=sys\.argv\[1:\], \*\*(\{.*\})\)"
)
_TRACKER = re.compile(r"from multiprocessing\.resource_tracker import main;main\((\d+)\)")
_FIRST_INHERITED_FD = 3  # parents hand pipes over with pass_fds; stdio is the caller's
_INVALID = object()  # not a literal

# (module, function, positional arguments, keyword arguments, sys.argv for the child)
type Command = tuple[str, str, tuple[object, ...], dict[str, object], list[str]]


def _literal(text: str) -> object:
    try:
        return ast.literal_eval(text)
    except ValueError, SyntaxError, MemoryError, RecursionError:
        return _INVALID


def _fd(value: object) -> bool:
    return type(value) is int and value >= _FIRST_INHERITED_FD


def _spawn(arguments: str, rest: list[str]) -> Command | None:
    if rest != ["--multiprocessing-fork"]:
        return None
    keywords: dict[str, object] = {}
    for item in arguments.split(", ") if arguments else ():
        name, _, text = item.partition("=")
        value = _literal(text)
        valid = _fd(value) if name in ("pipe_handle", "tracker_fd") else name == "parent_pid"
        if not valid or not (value is None or type(value) is int):
            return None
        keywords[name] = value
    if "pipe_handle" not in keywords:
        return None
    return "multiprocessing.spawn", "spawn_main", (), keywords, ["-c", *rest]


def _forkserver(match: re.Match[str], rest: list[str]) -> Command | None:
    listener, alive = int(match[1]), int(match[2])
    preload, keywords = _literal(match[3]), _literal(match[4])
    if not (_fd(listener) and _fd(alive)) or not isinstance(keywords, dict):
        return None
    if not isinstance(preload, list) or not all(isinstance(name, str) for name in preload):
        return None
    if not keywords.keys() <= {"sys_path", "main_path", "authkey_r"}:
        return None
    if keywords.get("main_path") is not None or not _fd(keywords.get("authkey_r", 3)):
        return None
    # sys_path is dropped: the forkserver keeps the payload-only sys.path set by the boot code
    # (its workers still receive the parent's sys.path through the authenticated pipe).
    authkey = {"authkey_r": keywords["authkey_r"]} if "authkey_r" in keywords else {}
    return ("multiprocessing.forkserver", "main", (listener, alive, preload),
            {**authkey, "sys_argv": rest}, ["-c", *rest])  # fmt: skip


def _code_position(argv: list[str]) -> int | None:
    """Index of the code after ``<flags> -c``, or None if argv has another shape."""
    i = 1
    while i < len(argv) - 1:
        if argv[i] == "-c":
            return i + 1
        if not argv[i].startswith("-"):
            return None
        i += 2 if argv[i] == "-X" else 1  # "-X dev" is two arguments, "-Wdefault" one
    return None


def child_command(argv: list[str]) -> Command | None:
    """The multiprocessing child that ``argv`` asks for, or None for a normal run."""
    position = _code_position(argv)
    if position is None:
        return None
    code, rest = argv[position], argv[position + 1 :]
    if (match := _TRACKER.fullmatch(code)) and not rest and _fd(int(match[1])):
        return "multiprocessing.resource_tracker", "main", (int(match[1]),), {}, ["-c"]
    if match := _SPAWN.fullmatch(code):
        return _spawn(match[1], rest)
    if match := _FORKSERVER.fullmatch(code):
        return _forkserver(match, rest)
    return None


def _parent_runs_this_executable() -> bool:
    """multiprocessing starts its children from the app itself; a shell or sudo is a caller
    pretending to be multiprocessing. Any doubt counts as no."""
    try:
        parent = os.stat(f"/proc/{os.getppid()}/exe")
        own = os.stat("/proc/self/exe")
    except OSError:
        return False
    return (parent.st_dev, parent.st_ino) == (own.st_dev, own.st_ino)


def run_child(argv: list[str]) -> bool:
    """Run the multiprocessing child ``argv`` asks for; False if it asks for none."""
    command = child_command(argv)
    if command is None:
        return False
    if not _parent_runs_this_executable():
        raise SystemExit("refusing to run as a multiprocessing child: not started by this program")
    module, function, arguments, keywords, child_argv = command
    sys.argv[:] = child_argv  # what CPython's -c would have left in sys.argv
    getattr(importlib.import_module(module), function)(*arguments, **keywords)
    return True
