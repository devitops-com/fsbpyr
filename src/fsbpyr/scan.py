"""Build-time hint: code that reads files located next to ``__file__`` (plan Phase 6b).

In an executable a module's ``__file__`` points into the payload's virtual tree, so such
reads fail unless the package is FS_REQUIRED. This is a heuristic over the AST: a call that
reads the file system, or a pathlib read method, whose argument or receiver mentions
``__file__`` directly or through a name assigned from it.
"""

import ast

_READ_CALLS = frozenset({
    "open", "io.open", "codecs.open", "os.listdir", "os.scandir", "os.walk", "glob.glob",
    "glob.iglob", "ctypes.CDLL", "CDLL", "ctypes.cdll.LoadLibrary", "cdll.LoadLibrary",
})  # fmt: skip
_READ_METHODS = frozenset({"open", "read_text", "read_bytes", "iterdir", "glob", "rglob"})
_MAX_PASSES = 5  # name propagation reaches a fixed point within a few passes in practice


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (base := _dotted(node.value)) is not None:
        return f"{base}.{node.attr}"
    return None


def _tainted_names(tree: ast.Module) -> set[str]:
    """``__file__`` plus every name assigned from an expression that mentions a tainted name."""
    tainted = {"__file__"}
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
    return any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node))


def _reads(call: ast.Call, tainted: set[str]) -> bool:
    if _dotted(call.func) in _READ_CALLS:
        arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
        return any(_mentions(argument, tainted) for argument in arguments)
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _READ_METHODS
        and _mentions(func.value, tainted)
    )


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


def file_relative_reads(source: bytes, filename: str) -> list[int]:
    """Line numbers of reads relative to ``__file__`` in one module (empty if it does not parse)."""
    if b"__file__" not in source:
        return []
    try:
        tree = ast.parse(source, filename)
    except SyntaxError, ValueError:
        return []
    tainted, guarded = _tainted_names(tree), _main_guarded(tree)
    calls = (node for node in ast.walk(tree) if isinstance(node, ast.Call))
    lines = {call.lineno for call in calls if _reads(call, tainted)}
    return sorted(line for line in lines if not any(line in block for block in guarded))
