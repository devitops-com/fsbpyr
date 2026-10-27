"""Build-time hint: code that reads files located next to ``__file__`` (plan Phase 6b).

In an executable a module's ``__file__`` points into the payload's virtual tree, so such
reads fail unless the package is FS_REQUIRED. This is a heuristic over the AST: a call that
reads the file system, runs a program, loads a library or data file, or a pathlib read
method, whose argument or receiver mentions ``__file__`` or ``__path__`` (also of another
module, ``X.__file__``) directly or through a name assigned from it.
"""

import ast

# Calls that start a program; with ``sys.executable`` as the program, a script's sibling is run
# from the payload (D44), not read from beside the binary.
_SPAWN_CALLS = frozenset({
    "subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "os.execv", "os.execve", "os.execvp", "os.execvpe", "os.execl",
    "os.execle", "os.execlp", "os.execlpe",
})  # fmt: skip
_READ_CALLS = _SPAWN_CALLS | frozenset({
    "open", "io.open", "codecs.open", "os.listdir", "os.scandir", "os.walk", "glob.glob",
    "glob.iglob", "ctypes.CDLL", "CDLL", "ctypes.cdll.LoadLibrary", "cdll.LoadLibrary",
    "np.load", "numpy.load",
})  # fmt: skip
_READ_METHODS = frozenset({"open", "read_text", "read_bytes", "iterdir", "glob", "rglob"})
# Methods of any receiver that read a path given as an argument: cffi's ffi.dlopen(path),
# configparser's parser.read(path).
_PATH_ARGUMENT_METHODS = frozenset({"dlopen", "read"})
# jinja2's loader is flagged by the call: its arguments are plain strings (package and folder).
_LOADER_CALLS = frozenset({"PackageLoader", "jinja2.PackageLoader"})
# Calls that open a library by name (the first argument, or ``name=``): ctypes, cffi's dlopen.
_LOAD_CALLS = frozenset({
    "ctypes.CDLL", "CDLL", "ctypes.PyDLL", "PyDLL", "ctypes.cdll.LoadLibrary", "cdll.LoadLibrary",
})  # fmt: skip
_FIND_CALLS = frozenset({"ctypes.util.find_library", "util.find_library", "find_library"})
_LIBRARY_MARKERS = (b"CDLL", b"LoadLibrary", b"dlopen", b"find_library")
_FILE_ATTRIBUTES = frozenset({"__file__", "__path__"})
_MARKERS = (b"__file__", b"__path__", b"PackageLoader")  # a module without them is skipped
_MAX_PASSES = 5  # name propagation reaches a fixed point within a few passes in practice


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (base := _dotted(node.value)) is not None:
        return f"{base}.{node.attr}"
    return None


def _tainted_names(tree: ast.Module) -> set[str]:
    """``__file__`` plus every name assigned from an expression that mentions a tainted name."""
    tainted = set(_FILE_ATTRIBUTES)
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign | ast.AnnAssign)]
    for _ in range(_MAX_PASSES):
        before = len(tainted)
        for node in assignments:
            if node.value is not None and _mentions(node.value, tainted):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                tainted.update(t.id for target in targets for t in ast.walk(target)
                               if isinstance(t, ast.Name))  # fmt: skip
        if len(tainted) == before:
            break
    return tainted


def _mentions(node: ast.AST, names: set[str]) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id in names)
        or (isinstance(n, ast.Attribute) and n.attr in _FILE_ATTRIBUTES)  # X.__file__
        for n in ast.walk(node)
    )


def _arguments_mention(call: ast.Call, tainted: set[str]) -> bool:
    arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
    return any(_mentions(argument, tainted) for argument in arguments)


def _reads(call: ast.Call, tainted: set[str]) -> bool:
    name, func = _dotted(call.func), call.func
    if name in _LOADER_CALLS:
        return True
    if name in _READ_CALLS:
        return _arguments_mention(call, tainted)
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in _PATH_ARGUMENT_METHODS and _arguments_mention(call, tainted):
        return True
    return func.attr in _READ_METHODS and _mentions(func.value, tainted)


def _runs_own_interpreter(call: ast.Call) -> bool:
    """Whether a spawn call runs ``sys.executable``: the first element of the argument list
    (a list or tuple literal), or for ``os.exec*`` the program argument."""
    if _dotted(call.func) not in _SPAWN_CALLS:
        return False
    first = call.args[0] if call.args else next((k.value for k in call.keywords
                                                 if k.arg == "args"), None)  # fmt: skip
    if isinstance(first, ast.List | ast.Tuple):
        first = first.elts[0] if first.elts else None
    return first is not None and _dotted(first) == "sys.executable"


def _main_guarded(tree: ast.Module) -> list[range]:
    """Lines of top-level ``if __name__ == "__main__":`` blocks, which never run on import."""
    return [
        range(node.lineno, (node.end_lineno or node.lineno) + 1)
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and any(
            isinstance(c, ast.Constant) and c.value == "__main__" for c in node.test.comparators
        )
    ]


def file_relative_reads(source: bytes, filename: str, *, script: bool = False) -> list[int]:
    """Line numbers of reads relative to ``__file__`` in one module (empty if it does not parse).
    A package's main guard never runs on import, so it has none there; a script runs it, and
    its ``sys.executable`` call of a sibling runs the sibling from the payload, no read."""
    if not any(marker in source for marker in _MARKERS):
        return []
    try:
        tree = ast.parse(source, filename)
    except (SyntaxError, ValueError):
        return []
    tainted = _tainted_names(tree)
    guarded = [] if script else _main_guarded(tree)
    calls = (node for node in ast.walk(tree) if isinstance(node, ast.Call))
    lines = {
        call.lineno for call in calls
        if _reads(call, tainted) and not (script and _runs_own_interpreter(call))
    }
    return sorted(line for line in lines if not any(line in block for block in guarded))


def _literal_argument(call: ast.Call) -> str | None:
    """The call's first argument (or ``name=``) when it is a string literal."""
    argument = (
        call.args[0]
        if call.args
        else next((k.value for k in call.keywords if k.arg == "name"), None)
    )
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    return None


def loaded_libraries(source: bytes, filename: str) -> list[str]:
    """System libraries a module opens by a literal name, sorted: ``CDLL("libmagic.so.1")``
    and cffi's ``dlopen`` give the name, ``find_library("magic")`` gives ``libmagic``. A path
    names a file, not a system library, and is left out. A heuristic: names built at run time
    are not seen (empty if the module does not parse)."""
    if not any(marker in source for marker in _LIBRARY_MARKERS):
        return []
    try:
        tree = ast.parse(source, filename)
    except (SyntaxError, ValueError):
        return []
    found = set()
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        name = _dotted(call.func)
        is_dlopen = isinstance(call.func, ast.Attribute) and call.func.attr == "dlopen"
        opens = name in _LOAD_CALLS or name in _FIND_CALLS or is_dlopen
        literal = _literal_argument(call) if opens else None
        if not literal or "/" in literal:
            continue
        found.add(f"lib{literal}" if name in _FIND_CALLS else literal)
    return sorted(found)
