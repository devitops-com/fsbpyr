"""The directories a script puts on ``sys.path`` with a path built from its own ``__file__``
(the common ``sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
"pylib"))``), found with ast for the import check (importcheck.py). A path that cannot be
followed is told apart from one the payload cannot serve: the first may hold anything, the second
holds nothing. The path hook normalizes ``..`` itself (never above the executable), so a written
``..`` is followed as the hook does; a directory that is no Python name is searched under its
payload name (loose.py).
"""

from __future__ import annotations

import ast
import posixpath

from .loose import _module_stem

_SAME_PATH = frozenset({"str", "os.fspath", "Path", "pathlib.Path", "PurePath", "os.path.abspath",
                        "os.path.realpath", "os.path.normpath"})  # fmt: skip
_SAME_METHODS = frozenset({"resolve", "absolute"})
_PATH_CALLS = frozenset({"append", "insert", "extend"})

type _Found = tuple[str, ...]  # parts below the scripts root


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _literal(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _parent(inner: _Found | None) -> _Found | None:
    return inner[:-1] if inner else None


def _location(node: ast.expr, file: tuple[str, ...]) -> _Found | None:
    """The path ``node`` builds from ``__file__`` as the parts below the scripts root (``file``
    is the script's own path); None when it is built from anything else. Written ``..`` parts
    stay in the parts."""
    if isinstance(node, ast.Name):
        return file if node.id == "__file__" else None
    if isinstance(node, ast.Call):
        return _call_location(node, file)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        return _parent(_location(node.value, file))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div | ast.Add):
        return _join([node.left, node.right], file, glue=isinstance(node.op, ast.Add))
    return None


def _call_location(node: ast.Call, file: tuple[str, ...]) -> _Found | None:
    name = _dotted(node.func)
    if name == "os.path.dirname" and len(node.args) == 1:
        return _parent(_location(node.args[0], file))
    if name == "os.path.join" and node.args:
        return _join(node.args, file)
    if name in _SAME_PATH and len(node.args) == 1:
        return _location(node.args[0], file)
    if isinstance(node.func, ast.Attribute) and node.func.attr in _SAME_METHODS:
        return _location(node.func.value, file)
    return None


def _join(nodes: list[ast.expr], file: tuple[str, ...], *, glue: bool = False) -> _Found | None:
    """``os.path.join(base, "a", "b")``, ``base / "a"`` or (``glue``) ``base + "/a"``."""
    parts = _location(nodes[0], file)
    if parts is None:
        return None
    for node in nodes[1:]:
        text = _literal(node)
        if text is None or text.startswith("/") != glue:  # a path is joined, a string glued
            return None
        parts = (*parts, *(part for part in text.split("/") if part))
    return parts


def _directory(parts: _Found) -> str | None:
    """The scripts-root-relative directory a ``sys.path`` entry names, in its payload name (a
    directory that is no Python name is stored mangled); None when ``..`` takes it above the
    scripts root, where the project has no files."""
    if ".." in parts or "." in parts:
        path = posixpath.normpath("/".join(parts)) if parts else "."
        if path == ".." or path.startswith("../"):
            return None
        parts = tuple(part for part in path.split("/") if part != ".")
    return "/".join(_module_stem(part) for part in parts)


def path_entries(tree: ast.Module, file: str) -> tuple[list[str], bool]:
    """The directories (below the scripts root) the file adds to ``sys.path`` and whether it
    changes ``sys.path`` in any other way. A directory built from ``__file__`` that the payload
    cannot serve is neither."""
    where = tuple(file.split("/"))
    found: list[str] = []
    dynamic = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr not in _PATH_CALLS or _dotted(node.func.value) != "sys.path":
                continue
            argument = node.args[-1] if node.args else None
            entries = argument.elts if isinstance(argument, ast.List | ast.Tuple) else [argument]
            for entry in entries:
                location = None if entry is None else _location(entry, where)
                if location is None:
                    dynamic = True
                elif (directory := _directory(location)) is not None:
                    found.append(directory)
        elif isinstance(node, ast.Assign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            dynamic = dynamic or any(_dotted(target) == "sys.path" for target in targets)
    return found, dynamic
