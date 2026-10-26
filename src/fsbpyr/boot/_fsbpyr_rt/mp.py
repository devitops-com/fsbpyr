"""Children that multiprocessing starts by re-executing ``sys.executable`` (plan §3.3, D22).

spawn, forkserver and the resource tracker run ``<exe> <flags> -c <code> [args]``. In a
single-file app ``sys.executable`` is the app, so those three command lines are recognized
here, exactly, and their arguments decoded with ``ast.literal_eval``; argv is never executed.

Recognizing the command is not enough: the functions it names unpickle data from the given
fd, import modules or run files. An app that may be started with untrusted arguments (a sudo
rule, say) must not become a Python interpreter, so a child runs only when its parent process
runs this very executable, its fds are not stdio, and it asks for nothing an fsbpyr parent
never sends (a ``main_path``, a foreign ``sys_path``).

A venv's forkserver imports the console script that started it (the parent's ``main_path``) once,
and its workers fork with the app's modules loaded. A built app has no script to send, so the
forkserver imports the module of the app's own entry point instead (``INFO["entrypoint"]``, never
the command line). An app run as a module (``python -m``) sends no ``main_path`` in a venv either.
"""

import ast
import importlib
import os
import re
import sys

_SPAWN = re.compile(r"from multiprocessing\.spawn import spawn_main; spawn_main\((.*)\)")
# The forkserver command line differs per version: 3.12 has no sys_argv and no authkey_r,
# 3.13 adds sys_argv, 3.14 adds authkey_r, 3.15 on_error, sent when set_forkserver_preload names
# one (multiprocessing/forkserver.py of each).
_FORKSERVER_3_12 = re.compile(
    r"from multiprocessing\.forkserver import main; "
    r"main\((\d+), (\d+), (\[[^\]]*\]), \*\*(\{.*\})\)"
)
_FORKSERVER_3_13 = re.compile(
    r"import sys; from multiprocessing\.forkserver import main; "
    r"main\((\d+), (\d+), (\[[^\]]*\]), sys_argv=sys\.argv\[1:\], \*\*(\{.*\})\)"
)
_FORKSERVER = _FORKSERVER_3_12 if sys.version_info < (3, 13) else _FORKSERVER_3_13
_FORKSERVER_KEYS = (
    {"sys_path", "main_path"}
    | ({"authkey_r"} if sys.version_info >= (3, 14) else set())
    | ({"on_error"} if sys.version_info >= (3, 15) else set())
)
_ON_ERROR = ("ignore", "warn", "fail")
_FORKSERVER_SYS_ARGV = sys.version_info >= (3, 13)
_TRACKER = re.compile(r"from multiprocessing\.resource_tracker import main;main\((\d+)\)")
_FIRST_INHERITED_FD = 3  # parents hand pipes over with pass_fds; stdio is the caller's
_INVALID = object()  # not a literal

# (module, function, positional arguments, keyword arguments, sys.argv for the child)
type Command = tuple[str, str, tuple[object, ...], dict[str, object], list[str]]


def _literal(text: str) -> object:
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
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
    if not keywords.keys() <= _FORKSERVER_KEYS:
        return None
    if (
        keywords.get("main_path") is not None
        or not _fd(keywords.get("authkey_r", 3))
        or keywords.get("on_error", "ignore") not in _ON_ERROR
    ):
        return None
    # sys_path is dropped: the forkserver keeps the payload-only sys.path set by the boot code
    # (its workers still receive the parent's sys.path through the authenticated pipe).
    passed = {name: keywords[name] for name in ("authkey_r", "on_error") if name in keywords}
    if not _FORKSERVER_SYS_ARGV:  # 3.12's command line has no arguments after the code
        return None if rest else _forkserver_command(listener, alive, preload, passed, [])
    return _forkserver_command(listener, alive, preload, {**passed, "sys_argv": rest}, rest)


def _forkserver_command(
    listener: int, alive: int, preload: list[str], keywords: dict[str, object], rest: list[str]
) -> Command:
    return "multiprocessing.forkserver", "main", (listener, alive, preload), keywords, ["-c", *rest]


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


def _read_parent_status() -> str:
    with open(f"/proc/{os.getppid()}/status", encoding="ascii", errors="replace") as status:
        return status.read()


def _first_two_ids(status: str, field: str) -> tuple[int, int] | None:
    """The real and effective ids of the ``Uid:`` or ``Gid:`` line of a /proc status file."""
    for line in status.splitlines():
        if line.startswith(field + ":"):
            ids = line.split()[1:3]
            return (int(ids[0]), int(ids[1])) if len(ids) == 2 else None
    return None


def _parent_runs_this_executable() -> bool:
    """multiprocessing starts its children from the app itself; a shell or sudo is a caller
    pretending to be multiprocessing. The parent must run this executable with the real and
    effective ids this process has (the saved ids differ: execve sets the child's to its
    effective id). Any doubt counts as no."""
    try:
        parent = os.stat(f"/proc/{os.getppid()}/exe")
        own = os.stat("/proc/self/exe")
        status = _read_parent_status()
        uids = _first_two_ids(status, "Uid")
        gids = _first_two_ids(status, "Gid")
    except (OSError, ValueError):
        return False
    if (parent.st_dev, parent.st_ino) != (own.st_dev, own.st_ino):
        return False
    return uids == os.getresuid()[:2] and gids == os.getresgid()[:2]


def _preload_entry_module(entrypoint: str) -> None:
    """What a venv's forkserver does with the console script's ``main_path``: import the module
    of ``pkg.cli:main`` as the preloaded main. A failure is left to the workers, which import it
    again and report it (the forkserver's own ``on_error`` does the same for ``main_path``)."""
    from multiprocessing import process  # noqa: PLC0415

    process.current_process()._inheriting = True  # type: ignore[attr-defined]  # as forkserver does
    try:
        importlib.import_module(entrypoint.partition(":")[0])
    except Exception:  # noqa: S110
        pass
    finally:
        del process.current_process()._inheriting  # type: ignore[attr-defined]


def _parent_target(table: str) -> str:
    """The target of the command the parent runs, from the parent's own command line (the same
    choice the start made, see ``commands``); empty if the parent runs none or cannot be read.
    A forkserver of a multi-command executable preloads that command's module."""
    from _fsbpyr_rt.commands import find, parse  # type: ignore[import-not-found]  # noqa: PLC0415

    try:
        with open(f"/proc/{os.getppid()}/cmdline", "rb") as cmdline:
            argv = os.fsdecode(cmdline.read()).split("\0")[:-1]
    except OSError:
        return ""
    found = find(parse(table), argv)
    return found[0] if found is not None else ""


def preload_names(arguments: tuple[object, ...]) -> list[str]:
    """The module names a forkserver command preloads (``_forkserver`` checked they are strings)."""
    names = arguments[2]
    return names if isinstance(names, list) else []


def run_child(argv: list[str], entrypoint: str = "") -> bool:
    """Run the multiprocessing child ``argv`` asks for; False if it asks for none.
    ``entrypoint`` is the app's own (``pkg.cli:main`` or a module name, or a table of commands)."""
    command = child_command(argv)
    if command is None:
        return False
    if not _parent_runs_this_executable():
        raise SystemExit("refusing to run as a multiprocessing child: not started by this program")
    module, function, arguments, keywords, child_argv = command
    sys.argv[:] = child_argv  # what CPython's -c would have left in sys.argv
    forkserver = module == "multiprocessing.forkserver"
    if forkserver and "__main__" in preload_names(arguments):
        if "\n" in entrypoint:
            entrypoint = _parent_target(entrypoint)
        if ":" in entrypoint:
            _preload_entry_module(entrypoint)
    getattr(importlib.import_module(module), function)(*arguments, **keywords)
    return True
