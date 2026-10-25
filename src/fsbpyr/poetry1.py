"""A Poetry 1 project that is not packaged (``[tool.poetry] package-mode = false``, no
``[project]`` table): read as the ``[project]`` table of a project without a build system.

Poetry 1 keeps the name, the version, the dependencies and the scripts in ``[tool.poetry]``.
The name and version, the scripts (``module:function`` strings) and the dependencies become the
table loose.py reads; Poetry's version constraints (``^1.2``, ``~1.2``, a bare ``1.2.3``) become
PEP 440 specifiers. The dependencies are the main group's; ``optional`` ones belong to the extras
that ``[tool.poetry.extras]`` lists them in. A dependency from git, a path or a URL is refused
(write the project with a ``[project]`` table, or lock it with ``--requirements``). The lock, when
there is one, still decides what is installed (foreign.py).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name

from .project import ProjectError

_CARET_TILDE = re.compile(r"([\^~])\s*(\d+(?:\.\d+)*)$")
_BARE = re.compile(r"\d[\w.*+!-]*$")
_UNSUPPORTED = ("git", "path", "url", "develop", "source")


def _constraint(text: str, where: str) -> str:
    """A Poetry version constraint as a PEP 440 specifier set ("" for any version)."""
    if "||" in text:
        raise ProjectError(f"pyproject.toml: {where}: {text!r} has alternatives (||), which a "
                           "PEP 440 specifier cannot say")  # fmt: skip
    parts: list[str] = []
    for part in (p.strip() for p in text.split(",") if p.strip() and p.strip() != "*"):
        if match := _CARET_TILDE.match(part):
            numbers = [int(n) for n in match[2].split(".")]
            if match[1] == "^":
                index = next((i for i, n in enumerate(numbers) if n), len(numbers) - 1)
            else:
                index = min(1, len(numbers) - 1)
            upper = [*numbers[:index], numbers[index] + 1]
            parts += [f">={match[2]}", "<" + ".".join(map(str, upper))]
        elif _BARE.match(part):
            parts.append(f"=={part}")
        else:
            parts.append(part)
    try:
        return str(SpecifierSet(",".join(parts)))
    except InvalidSpecifier as exc:
        raise ProjectError(f"pyproject.toml: {where}: cannot read the constraint {text!r}: "
                           f"{exc}") from exc  # fmt: skip


def _requirements(name: str, spec: object) -> list[tuple[str, bool]]:
    """The requirement lines of a dependency ((line, optional) per alternative)."""
    lines = []
    for item in spec if isinstance(spec, list) else [spec]:
        fields: dict[str, Any] = item if isinstance(item, dict) else {"version": item}
        if unsupported := [key for key in _UNSUPPORTED if key in fields]:
            raise ProjectError(f"pyproject.toml: {name} is a {unsupported[0]} dependency, which "
                               "fsbpyr cannot read from a Poetry 1 project without a [project] "
                               "table; add one, or lock the build with --requirements")  # fmt: skip
        text = name
        if extras := fields.get("extras"):
            text += f"[{','.join(str(extra) for extra in extras)}]"
        text += _constraint(str(fields.get("version") or "*"), name)
        markers = [str(fields["markers"])] if fields.get("markers") else []
        if python := fields.get("python"):
            markers += [f'python_full_version {s.operator} "{s.version}"'
                        for s in SpecifierSet(_constraint(str(python), name))]
        if markers:
            text += "; " + " and ".join(markers)
        try:
            Requirement(text)
        except InvalidRequirement as exc:
            raise ProjectError(f"pyproject.toml: {name}: invalid dependency {text!r}: "
                               f"{exc}") from exc  # fmt: skip
        lines.append((text, bool(fields.get("optional"))))
    return lines


def project_table(data: Mapping[str, Any]) -> dict[str, Any]:
    """The ``[project]`` table of an unpackaged Poetry 1 pyproject.toml (``name`` may be absent:
    Poetry 1 did not need one without a package)."""
    poetry = data["tool"]["poetry"]
    table: dict[str, Any] = {}
    for key in ("name", "version"):
        if isinstance(poetry.get(key), str):
            table[key] = poetry[key]
    dependencies = dict(poetry.get("dependencies") or {})
    python = dependencies.pop("python", None)
    if isinstance(python, str) and (spec := _constraint(python, "python")):
        table["requires-python"] = spec
    main: list[str] = []
    optional: dict[str, str] = {}
    for name, spec in dependencies.items():
        for line, is_optional in _requirements(name, spec):
            if is_optional:
                optional[canonicalize_name(name)] = line
            else:
                main.append(line)
    table["dependencies"] = main
    extras = {extra: [optional[canonicalize_name(n)] for n in names
                      if canonicalize_name(n) in optional]
              for extra, names in (poetry.get("extras") or {}).items()}  # fmt: skip
    if extras:
        table["optional-dependencies"] = extras
    scripts = {k: v for k, v in (poetry.get("scripts") or {}).items() if isinstance(v, str)}
    if scripts:
        table["scripts"] = scripts
    return table
