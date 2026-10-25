"""The static import check of a project's script commands (research UC3, decision 5.9(a)).

A script of a project without a build system imports what its developer's environment had; the
executable has what the project declares. For each command this follows the imports of the
script and of the project's own modules it reaches, with ast and no import, and reports every
required import that resolves nowhere in the payload: the script's directory first (as at run
time, the boot puts it first on ``sys.path``), then the installed distributions and the
runtime's modules. An import is optional under ``try`` with an ``except ImportError`` (or one
that catches anything), in the handlers of such a ``try`` (the fallback), and under
``if TYPE_CHECKING`` or a test on sys.platform, os.name or sys.version_info.
"""

from __future__ import annotations

import ast
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .loose import ROOT

# Standard library modules removed in a Python version (PEP 594 dead batteries, PEP 632
# distutils, and 3.12's imp and asynchat family), checked against 3.11 to 3.15 installations.
REMOVED_IN: dict[str, tuple[int, int]] = {
    **dict.fromkeys(("imp", "distutils", "asynchat", "asyncore", "smtpd"), (3, 12)),
    **dict.fromkeys((
        "cgi", "cgitb", "crypt", "telnetlib", "nntplib", "pipes", "sndhdr", "spwd", "sunau",
        "uu", "xdrlib", "msilib", "audioop", "aifc", "chunk", "imghdr", "mailcap", "nis",
        "ossaudiodev", "lib2to3",
    ), (3, 13)),
}  # fmt: skip
_ENVIRONMENT = {("sys", "platform"), ("os", "name"), ("sys", "version_info")}
_OPTIONAL_CATCHES = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})


@dataclass(frozen=True, slots=True)
class Unresolved:
    path: str  # the project file, relative
    line: int
    module: str  # as written, a relative import with its dots
    problem: str = ""  # why, when it is not simply a missing module

    def describe(self) -> str:
        return f"{self.path}:{self.line}: {self.problem or f'import {self.module}'}"


def top_level_names(site: Path) -> frozenset[str]:
    """The modules and packages a site directory provides: its files and directories, apart
    from metadata."""
    names = set()
    for entry in site.iterdir():
        name = entry.name
        if name.endswith((".dist-info", ".egg-info")) or name == "__pycache__":
            continue
        names.add(name.partition(".")[0] if entry.is_file() else name)
    return frozenset(names)


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    kind = handler.type
    kinds = kind.elts if isinstance(kind, ast.Tuple) else [kind]
    return any(
        k is None or (isinstance(k, ast.Name) and k.id in _OPTIONAL_CATCHES)
        or (isinstance(k, ast.Attribute) and k.attr in _OPTIONAL_CATCHES) for k in kinds
    )  # fmt: skip


def _type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _environment_test(test: ast.expr) -> bool:
    """``TYPE_CHECKING``, or a test on ``sys.platform``, ``os.name`` or ``sys.version_info``."""
    if _type_checking(test):
        return True
    return any(
        isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
        and (n.value.id, n.attr) in _ENVIRONMENT for n in ast.walk(test)
    )  # fmt: skip


def _visit(
    node: ast.AST, optional: bool, found: list[tuple[ast.Import | ast.ImportFrom, bool]]
) -> None:
    if isinstance(node, ast.Import | ast.ImportFrom):
        found.append((node, optional))
    elif isinstance(node, ast.Try | ast.TryStar):
        guarded = any(_catches_import_error(handler) for handler in node.handlers)
        for stmt in node.body:
            _visit(stmt, optional or guarded, found)
        for handler in node.handlers:  # the fallback of a guarded import is optional too
            _visit(handler, optional or guarded, found)
        for stmt in (*node.orelse, *node.finalbody):
            _visit(stmt, optional, found)
    elif isinstance(node, ast.If) and _environment_test(node.test):
        for part in node.body:
            _visit(part, True, found)
        for part in node.orelse:  # TYPE_CHECKING's else runs; a platform's may not
            _visit(part, optional or not _type_checking(node.test), found)
    else:
        for child in ast.iter_child_nodes(node):
            _visit(child, optional, found)


class _Project:
    """The project's files as the import system sees them below the scripts root."""

    def __init__(self, sources: Mapping[str, bytes]) -> None:
        self.files = frozenset(sources)
        self.directories = frozenset(
            str(parent) for path in sources for parent in PurePosixPath(path).parents
        ) - {"."}  # fmt: skip

    def find(self, base: str, parts: list[str]) -> list[str] | None:
        """The files of ``a.b.c`` below directory ``base`` (``__init__`` of each package, then
        the module), or None when some part is missing."""
        found: list[str] = []
        path = PurePosixPath(base)
        for index, part in enumerate(parts):
            path /= part
            last = index == len(parts) - 1
            if (module := f"{path}.py") in self.files and last:
                return [*(name for name in found if name in self.files), module]
            if str(path) not in self.directories:
                return None
            found.append(f"{path}/__init__.py")
        return [name for name in found if name in self.files]


def check_command(
    target: str, sources: Mapping[str, bytes], known: Collection[str]
) -> list[Unresolved]:
    """The required imports of the command ``target`` (below ``ROOT``, ``"a.b"`` or
    ``"a.b:attr"``) that nothing resolves; ``known`` is the top-level names of the installed
    distributions and the runtime. Files the closure reaches that do not parse are reported."""
    project = _Project(sources)
    parts = target.partition(":")[0].split(".")[1:]
    start = project.find("", parts)
    if not start:
        return []  # a dependency's script: nothing of the project's to follow
    script_directory = "/".join(parts[:-1])
    main = "/".join([*parts, "__main__.py"])  # a package entry runs its __main__
    pending = [*start, *([main] if main in project.files else [])]
    seen: set[str] = set()
    problems: list[Unresolved] = []
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        try:
            tree = ast.parse(sources[path], filename=path)
        except (SyntaxError, ValueError) as exc:
            problems.append(Unresolved(path, getattr(exc, "lineno", None) or 1, "",
                                       f"cannot be parsed: {exc}"))  # fmt: skip
            continue
        imports: list[tuple[ast.Import | ast.ImportFrom, bool]] = []
        _visit(tree, False, imports)
        for node, optional in imports:
            for module, files in _resolve(node, path, script_directory, project, known):
                if files is None and not optional:
                    problems.append(Unresolved(path, node.lineno, module))
                pending.extend(files or [])
    return problems


def _resolve(
    node: ast.Import | ast.ImportFrom,
    path: str,
    script_directory: str,
    project: _Project,
    known: Collection[str],
) -> list[tuple[str, list[str] | None]]:
    """(the name as written, the project files it reaches or None when nothing resolves it)."""
    if isinstance(node, ast.Import):
        return [(alias.name, _absolute(alias.name, script_directory, project, known))
                for alias in node.names]  # fmt: skip
    if node.level:  # relative to the file's own package; one level up per dot
        directory = PurePosixPath(path).parent
        for _ in range(node.level - 1):
            directory = directory.parent
        where = str(directory).removeprefix(".")
        parts = (node.module or "").split(".") if node.module else []
        files = project.find(where, parts) if parts else []
        written = "." * node.level + (node.module or "")
        if node.level > len(PurePosixPath(path).parts):  # above the scripts root
            files = None
    else:
        written, parts = node.module or "", (node.module or "").split(".")
        files = _absolute(written, script_directory, project, known)
        where, parts = _base(script_directory, parts)
    reached = list(files or [])
    if files is not None:  # `from pkg import name`: name may be a module of the package
        for alias in node.names:
            reached += project.find(where, [*parts, alias.name]) or []
    return [(written, reached if files is not None else None)]


def _base(script_directory: str, parts: list[str]) -> tuple[str, list[str]]:
    """Where ``a.b`` is looked up: the script's directory, or the scripts root for a name that
    starts with it."""
    return ("", parts[1:]) if parts[0] == ROOT else (script_directory, parts)


def _absolute(
    name: str, script_directory: str, project: _Project, known: Collection[str]
) -> list[str] | None:
    """The project files of the absolute import ``name``, [] when the distributions or the
    runtime have it, None when nothing does."""
    directory, parts = _base(script_directory, name.split("."))
    if not parts:  # the scripts root itself
        return []
    if (files := project.find(directory, parts)) is not None:
        return files
    return [] if parts[0] in known and directory == script_directory else None
