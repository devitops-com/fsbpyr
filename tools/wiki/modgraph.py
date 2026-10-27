"""Extract fsbpyr's module graph: intra-package imports, classes and public functions.

Usage: python -I modgraph.py SRC_ROOT OUT_JSON
SRC_ROOT is the directory holding the `fsbpyr` package (the repository's `src`).
"""

import ast
import json
import sys
from pathlib import Path

PACKAGE = "fsbpyr"


def module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


BOOT_ALIAS = "_fsbpyr_rt"  # the boot code imports its runtime helpers under this top-level name


def absolute(name: str) -> str:
    if name == BOOT_ALIAS or name.startswith(BOOT_ALIAS + "."):
        return f"{PACKAGE}.boot.{name}"
    return name


def resolve(current: str, is_package: bool, node: ast.ImportFrom, known: set[str]) -> list[str]:
    if node.level == 0:
        base = absolute(node.module or "")
    else:
        pkg = current.split(".") if is_package else current.split(".")[:-1]
        pkg = pkg[: len(pkg) - (node.level - 1)]
        base = ".".join(pkg + ([node.module] if node.module else []))
    return [f"{base}.{a.name}" if f"{base}.{a.name}" in known else base for a in node.names]


def imports_of(tree: ast.Module, current: str, is_package: bool, known: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    top_level = {id(n) for n in tree.body}
    guarded = set()
    for n in tree.body:
        if isinstance(n, ast.If):
            guarded.update(id(c) for c in ast.walk(n))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [absolute(a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = resolve(current, is_package, node, known)
        else:
            continue
        kind = "top" if id(node) in top_level else ("typing" if id(node) in guarded else "lazy")
        for name in names:
            target = name
            while target and target not in known:
                target = target.rpartition(".")[0]
            if (target and target != current and target.startswith(PACKAGE)
                    and found.get(target) != "top"):
                found[target] = kind
    return found


def main() -> None:
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    files = sorted(p for p in (root / PACKAGE).rglob("*.py") if "__pycache__" not in p.parts)
    known = {module_name(root, p) for p in files}
    modules = {}
    for path in files:
        name = module_name(root, path)
        tree = ast.parse(path.read_text(), str(path))
        classes = [
            {"name": n.name, "doc": (ast.get_docstring(n) or "").split("\n\n")[0]}
            for n in tree.body if isinstance(n, ast.ClassDef)
        ]
        functions = [
            {"name": n.name, "doc": (ast.get_docstring(n) or "").split("\n\n")[0]}
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
        ]
        modules[name] = {
            "path": str(path.relative_to(root.parent)),
            "lines": len(path.read_text().splitlines()),
            "doc": (ast.get_docstring(tree) or "").split("\n\n")[0],
            "classes": classes,
            "functions": functions,
            "imports": imports_of(tree, name, path.name == "__init__.py", known),
        }
    for name, info in modules.items():
        info["imported_by"] = sorted(m for m, i in modules.items() if name in i["imports"])
    out.write_text(json.dumps(modules, indent=1))


if __name__ == "__main__":
    main()
