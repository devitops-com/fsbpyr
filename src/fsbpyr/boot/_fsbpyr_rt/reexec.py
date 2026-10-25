"""Helper interpreters: ``<exe> [flags] -m MODULE ...``, ``-c CODE ...`` and ``<exe> [flags]
<exe>/pkg/script.py ...`` (plan D23, D44).

Tools start helper interpreters through ``sys.executable``; in a single-file app that is the
app. Only a child whose parent process runs this very executable (the D22 check) counts as one:
a shell, a script or a sudo rule gets the app, with these arguments. With ``interpreter-reexec``
such a child runs the module, the code or the script as CPython would, interpreter flags
accepted and ignored. Without the setting (the default) a child that asks for Python is refused
with exit 70, since running the app in it would start the app again and again (D44); with
``interpreter-reexec = false`` it gets the app, for apps whose own options are named -m or -c.
The one exception to the refusal is a script of the payload that is one of the app's own
commands (``[sys.executable, <exe>/_fsbpyr_scripts/x.py, args]``, a script that starts its
sibling as ``python x.py args`` would): it runs that command, which is no more than the app does
when it is started under that command's name (decided while building it, 2026-10-25).
"""

import ast
import importlib.util
import json
import runpy
import sys
from collections.abc import Collection

from _fsbpyr_rt.mp import _parent_runs_this_executable  # type: ignore[import-not-found]
from _fsbpyr_rt.restart import after_flags  # type: ignore[import-not-found]

REFUSED = 70  # the launcher's "refused to start" (D41)
_SCRIPT = "script"
SCRIPTS_ROOT = "_fsbpyr_scripts"  # the root of a project's own files (loose.py)

# ("-m", module, arguments), ("-c", code, arguments) or ("script", path, arguments)
type Command = tuple[str, str, list[str]]


def child_command(
    argv: list[str], prefix: str | None = None, overlay: str | None = None
) -> Command | None:
    """The module, code or (with ``prefix``, the executable's path and "/") script of the
    payload that ``argv`` asks for after interpreter flags, or None. ``overlay`` (the
    executable's directory and "/", for apps of scripts) also names the scripts: a path there
    counts only if a script of the payload is that file."""
    i = after_flags(argv)
    if i is None:
        return None
    if argv[i] in ("-m", "-c"):
        return (argv[i], argv[i + 1], argv[i + 2 :]) if i + 1 < len(argv) else None
    if prefix is not None and argv[i].startswith(prefix) and argv[i].endswith(".py"):
        return _SCRIPT, argv[i], argv[i + 1 :]
    named = overlay is not None and argv[i].startswith(overlay) and argv[i].endswith(".py")
    if named and _script_module(argv[i], prefix or "", overlay) is not None:
        return _SCRIPT, argv[i], argv[i + 1 :]
    return None


def asks_for_python(command: Command) -> bool:
    """Whether ``command`` looks like Python rather than the app's own options: a dotted name
    after -m, code after -c that is more than a name, an attribute or a constant
    (``-c config.toml`` is an option), or a script of the payload."""
    option, target, _ = command
    if option == "-m":
        return all(part.isidentifier() for part in target.split("."))
    if option == _SCRIPT:
        return True
    try:
        body = ast.parse(target).body
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return False
    alone = body[0] if len(body) == 1 else None
    return not (isinstance(alone, ast.Expr)
                and isinstance(alone.value, (ast.Name, ast.Attribute, ast.Constant)))  # fmt: skip


def _shown(command: Command, prefix: str) -> str:
    option, target, _ = command
    if option == _SCRIPT:
        return target.removeprefix(prefix)
    if option == "-c":
        return f"-c {target[:60]!r}{'...' if len(target) > 60 else ''}"
    return f"-m {target}"


def refusal(command: Command, prefix: str) -> str:
    return (
        f"fsbpyr: refusing to run `{_shown(command, prefix)}` as Python: the app started "
        "itself as a helper interpreter (sys.executable is the app), and running the app in "
        "its place would start it again and again; rebuild with --interpreter-reexec to run "
        "such children as Python, or with --no-interpreter-reexec if the app's own options "
        "are named -m or -c\n"
    )


def _script_module(path: str, prefix: str, overlay: str | None = None) -> str | None:
    """The module whose file in the payload ``path`` is (a package's ``__main__`` runs as the
    package), or None. A module of the scripts root says it lives next to the executable, below
    ``overlay``: that path names it too, as does its path inside the executable."""
    if path.startswith(prefix):
        below = path.removeprefix(prefix)
    elif overlay is not None and path.startswith(overlay):
        below = f"{SCRIPTS_ROOT}/{path.removeprefix(overlay)}"
    else:
        return None
    parts = below.removesuffix(".py").split("/")
    if not all(part.isidentifier() for part in parts) or parts[-1] == "__init__":
        return None
    name = ".".join(parts)
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):  # a parent package missing, or none of the payload's
        return None
    rest = below.removeprefix(f"{SCRIPTS_ROOT}/") if below.startswith(f"{SCRIPTS_ROOT}/") else None
    shown = f"{overlay}{rest}" if overlay is not None and rest is not None else prefix + below
    if spec is None or spec.origin != shown:
        return None
    return name.removesuffix(".__main__") if parts[-1] == "__main__" else name


def _own_command(
    command: Command, prefix: str, targets: Collection[str], overlay: str | None
) -> bool:
    """Whether ``command`` is a script of the payload that is the target of one of the app's own
    commands (a module run as ``__main__``, ``_fsbpyr_scripts.tools.x``)."""
    option, path, _ = command
    return option == _SCRIPT and _script_module(path, prefix, overlay) in targets


def _run(command: Command, prefix: str, overlay: str | None) -> bool:
    option, target, arguments = command
    if option == _SCRIPT:
        name = _script_module(target, prefix, overlay)
        if name is None:
            return False
        sys.argv[:] = [target, *arguments]  # runpy sets sys.argv[0] to the module's file
        sys.path.insert(0, target.rpartition("/")[0])  # the script's directory, as CPython does
        runpy._run_module_as_main(name, alter_argv=True)  # type: ignore[attr-defined]
        return True
    sys.argv[:] = [option, *arguments]  # as CPython leaves it; runpy puts the module's path first
    if option == "-m":
        runpy._run_module_as_main(target, alter_argv=True)  # type: ignore[attr-defined]
    else:
        exec(compile(target, "<string>", "exec"), sys.modules["__main__"].__dict__)  # noqa: S102
    return True


def run_child(
    argv: list[str],
    build_info: str,
    prefix: str,
    targets: Collection[str] = (),
    overlay: str | None = None,
) -> bool:
    """Run what ``argv`` asks for if the build allows it and the app started this process;
    exits 70 if the build did not choose and it asks for Python, unless it is a script that is
    the target of one of the app's own commands (``targets``); False if the app runs as usual.
    ``overlay`` is the executable's directory and "/" in an app of scripts (``child_command``)."""
    command = child_command(argv, prefix, overlay)
    if command is None:
        return False
    setting = json.loads(build_info).get("interpreter_reexec")
    if setting is False or not _parent_runs_this_executable():
        return False
    if setting is True or _own_command(command, prefix, targets, overlay):
        return _run(command, prefix, overlay)
    if not asks_for_python(command):
        return False
    sys.stderr.write(refusal(command, prefix))
    raise SystemExit(REFUSED)
