"""Entry inference for a project with no console scripts: a package with ``__main__.py``, or a
module with a main guard (research UC5). Source is parsed with ast, never imported."""

from __future__ import annotations

import ast
import csv
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_SKIPPED_DIRECTORIES = frozenset({"build", "dist", "node_modules", "__pycache__"})


@dataclass(frozen=True, slots=True)
class Candidates:
    packages: tuple[str, ...] = ()  # top-level packages with a __main__.py
    modules: tuple[str, ...] = ()  # top-level modules with a main guard

    def __bool__(self) -> bool:
        return bool(self.packages or self.modules)

    def described(self) -> str:
        return ", ".join(
            [f"{name} (package with __main__.py)" for name in self.packages]
            + [f"{name} (module with a main guard)" for name in self.modules]
        )


def has_main_guard(source: str | bytes) -> bool:
    """Whether the module body has ``if __name__ == "__main__":`` (either operand order).
    Bytes are parsed as Python does, so a BOM or a coding cookie is honoured."""
    marker: str | bytes = b"__main__" if isinstance(source, bytes) else "__main__"
    if marker not in source:  # type: ignore[operator]
        return False
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    return any(isinstance(node, ast.If) and _is_main_test(node.test) for node in tree.body)


def _is_main_test(test: ast.expr) -> bool:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    operands = [test.left, *test.comparators]
    names = [o.id for o in operands if isinstance(o, ast.Name)]
    values = [o.value for o in operands if isinstance(o, ast.Constant)]
    return names == ["__name__"] and values == ["__main__"]


def _guarded(path: Path) -> bool:
    try:
        return has_main_guard(path.read_bytes())
    except OSError:
        return False


def installed_candidates(site: Path, record: tuple[str, ...]) -> Candidates:
    """The top-level packages and modules of an installed distribution, from its RECORD."""
    packages, modules = set(), set()
    for line in record:
        rel = next(csv.reader([line]), [""])[0]
        parts = PurePosixPath(rel).parts
        if ".." in parts or rel.startswith("/") or not parts:
            continue
        if len(parts) == 2 and parts[1] == "__main__.py" and parts[0].isidentifier():
            packages.add(parts[0])
        elif len(parts) == 1 and rel.endswith(".py"):
            stem = rel.removesuffix(".py")
            if stem.isidentifier() and stem != "__main__" and _guarded(site / rel):
                modules.add(stem)
    return Candidates(tuple(sorted(packages)), tuple(sorted(modules)))


def source_has_candidate(directory: Path) -> bool:
    """Whether the project tree holds any ``__main__.py`` or guarded module: a build that has
    none cannot infer an entry. Every layout counts (package-dir may put sources anywhere)."""
    for root, dirs, files in os.walk(directory):
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d not in _SKIPPED_DIRECTORIES
            and not (Path(root, d) / "pyvenv.cfg").exists()
        ]  # fmt: skip
        if "__main__.py" in files:
            return True
        if any(f.endswith(".py") and f != "setup.py" and _guarded(Path(root, f)) for f in files):
            return True
    return False
