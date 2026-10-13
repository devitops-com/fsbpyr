"""Helper interpreters: ``<exe> [flags] -m MODULE ...`` and ``-c CODE ...`` (plan D23).

Tools start helper interpreters through ``sys.executable``; in a single-file app that is the
app. With ``interpreter-reexec`` such a child runs the module or the code as CPython would,
interpreter flags accepted and ignored, but only when its parent process runs this very
executable (the D22 check): a shell or a sudo rule gets the app, with these arguments.
"""

import json
import runpy
import sys

from _fsbpyr_rt.mp import _parent_runs_this_executable  # type: ignore[import-not-found]

_TAKES_VALUE = ("-X", "-W")  # "-X dev" is two arguments, "-Xdev" one

# ("-m", module, arguments) or ("-c", code, arguments)
type Command = tuple[str, str, list[str]]


def child_command(argv: list[str]) -> Command | None:
    """The module or code ``argv`` asks for after interpreter flags, or None."""
    i = 1
    while i < len(argv) - 1:
        if argv[i] in ("-m", "-c"):
            return argv[i], argv[i + 1], argv[i + 2 :]
        if not argv[i].startswith("-") or argv[i] == "-":
            return None
        i += 2 if argv[i] in _TAKES_VALUE else 1
    return None


def run_child(argv: list[str], build_info: str) -> bool:
    """Run what ``argv`` asks for if the build allows it and the app started this process;
    False if the app runs as usual."""
    command = child_command(argv)
    if command is None or not json.loads(build_info).get("interpreter_reexec"):
        return False
    if not _parent_runs_this_executable():
        return False
    option, target, arguments = command
    sys.argv[:] = [option, *arguments]  # as CPython leaves it; runpy puts the module's path first
    if option == "-m":
        runpy._run_module_as_main(target, alter_argv=True)  # type: ignore[attr-defined]
    else:
        exec(compile(target, "<string>", "exec"), sys.modules["__main__"].__dict__)  # noqa: S102
    return True
