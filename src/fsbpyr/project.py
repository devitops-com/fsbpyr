"""Project discovery: pyproject.toml / setup.py, Python constraints and lock source (§3.6)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name

DEFAULT_REQUIREMENTS = "requirements.txt"
# a commit-pinned VCS requirement: `name @ git+URL@<40 hex>` or `git+URL@<40 hex>#egg=name`
_SHA_PINNED_VCS = re.compile(r"git\+\S+@[0-9a-fA-F]{40}(?:[#&\s]|$)")
_HASHED_URL = re.compile(r"[#&]sha256=[0-9a-fA-F]{64}")
_VCS = re.compile(r"\b(?:git|hg|svn|bzr)\+[a-z]+://|\b(?:git|hg|svn|bzr)\+file:")
_EDITABLE = re.compile(r"(?:-e|--editable)(?:\s+|=)")


class ProjectError(RuntimeError):
    """The directory is not a buildable project, or its constraints exclude the runtime."""


class LockSource(Enum):
    UV_LOCK = "uv.lock"
    PYLOCK = "pylock.toml"
    REQUIREMENTS = "requirements"
    UNLOCKED = "unlocked"


@dataclass(frozen=True, slots=True)
class Project:
    directory: Path
    name: str | None  # canonical; None for setup.py-only projects (found after installing)
    requires_python: str | None = None
    scripts: dict[str, str] = field(default_factory=dict)
    extras: tuple[str, ...] = ()  # names in [project.optional-dependencies]
    tool: dict[str, Any] = field(default_factory=dict)  # the [tool.fsbpyr] table
    # [project] exists and does not list scripts / optional-dependencies as dynamic: then
    # `scripts` / `extras` are all there is, and a build can be checked before staging
    static_scripts: bool = False
    static_extras: bool = False
    static_entry_points: bool = False

    @classmethod
    def load(cls, directory: Path) -> Project:
        directory = directory.resolve()
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            try:
                data = tomllib.loads(pyproject.read_text())
            except tomllib.TOMLDecodeError as exc:
                raise ProjectError(f"{pyproject}: {exc}") from exc
        elif (directory / "setup.py").is_file():
            data = {}
        elif (directory / "setup.cfg").is_file():
            raise ProjectError(f"{directory} has a setup.cfg but no pyproject.toml or setup.py; "
                               "setup.cfg alone does not say how to build it")  # fmt: skip
        else:
            raise ProjectError(f"{directory} has no pyproject.toml or setup.py")
        table = data.get("project", {})
        dynamic = table.get("dynamic", [])
        declared = "project" in data and isinstance(dynamic, list)
        name = table.get("name")
        return cls(
            directory=directory,
            name=canonicalize_name(name) if isinstance(name, str) else None,
            requires_python=table.get("requires-python"),
            scripts=dict(table.get("scripts", {})),
            extras=tuple(table.get("optional-dependencies", {})),
            tool=dict(data.get("tool", {}).get("fsbpyr", {})),
            static_scripts=declared and "scripts" not in dynamic,
            static_extras=declared and "optional-dependencies" not in dynamic,
            static_entry_points=declared and "entry-points" not in dynamic,
        )

    @property
    def uv_root(self) -> Path | None:
        """Directory whose uv.lock governs this project: itself, or its uv workspace root."""
        if (self.directory / "uv.lock").is_file():
            return self.directory
        for parent in self.directory.parents:
            workspace = _workspace_table(parent / "pyproject.toml")
            if workspace is None:
                continue
            member = self.directory.relative_to(parent).as_posix()
            included = any(fnmatch(member, glob) for glob in workspace.get("members", []))
            excluded = any(fnmatch(member, glob) for glob in workspace.get("exclude", []))
            if included and not excluded and (parent / "uv.lock").is_file():
                return parent
            return None  # the nearest workspace does not claim this project
        return None

    def requirements_path(self, requirements: str | None) -> Path:
        return self.directory / (requirements or DEFAULT_REQUIREMENTS)

    def lock_source(self, requirements: str | None = None) -> LockSource:
        """An explicit requirements file, else uv.lock (own or workspace), pylock.toml,
        requirements.txt, else unlocked (plan §3.6 3)."""
        if requirements is not None:
            if not self.requirements_path(requirements).is_file():
                raise ProjectError(
                    f"requirements file {requirements} not found in {self.directory}"
                )
            return LockSource.REQUIREMENTS
        if self.uv_root is not None:
            return LockSource.UV_LOCK
        if (self.directory / "pylock.toml").is_file():
            return LockSource.PYLOCK
        if self.requirements_path(None).is_file():
            return LockSource.REQUIREMENTS
        return LockSource.UNLOCKED

    def lock_path(self, source: LockSource, requirements: str | None = None) -> Path | None:
        """The file ``source`` reads; None when unlocked."""
        if source is LockSource.UV_LOCK and self.uv_root is not None:
            return self.uv_root / LockSource.UV_LOCK.value
        if source is LockSource.PYLOCK:
            return self.directory / LockSource.PYLOCK.value
        if source is LockSource.REQUIREMENTS:
            return self.requirements_path(requirements)
        return None

    def python_pin(self) -> str | None:
        """What the first line of ``.python-version`` pins ("3.12", "3.12.4"; the part after
        ``@`` of a uv-style ``cpython@3.12``), if the file exists and names something."""
        pin_file = self.directory / ".python-version"
        if not pin_file.is_file():
            return None
        lines = pin_file.read_text().split()
        return lines[0].rpartition("@")[2] or None if lines else None

    def specifiers(self) -> SpecifierSet | None:
        """``requires-python`` as a specifier set; None when the project has none."""
        if self.requires_python is None:
            return None
        try:
            return SpecifierSet(self.requires_python)
        except InvalidSpecifier as exc:
            raise ProjectError(f"invalid requires-python {self.requires_python!r}") from exc

    def check_python(self, version: str) -> None:
        if (allowed := self.specifiers()) is not None and version not in allowed:
            raise ProjectError(
                f"requires-python {self.requires_python!r} excludes the runtime's Python {version}"
            )


def _requirement_lines(requirements: Path) -> list[tuple[int, str]]:
    """The (first line number, text) of each logical line of a requirements file; comments are
    dropped, and so is a trailing ``--hash`` on a requirement. A ``\\`` continues a line."""
    lines: list[tuple[int, str]] = []
    pending, start = "", 0
    for number, raw in enumerate(requirements.read_text().splitlines(), 1):
        start = start or number
        if raw.endswith("\\"):
            pending += raw[:-1] + " "
            continue
        text = (pending + raw).partition(" #")[0].strip()
        if text and not text.startswith("#"):
            lines.append((start, text.partition(" --")[0].strip() if text[0] != "-" else text))
        pending, start = "", 0
    if pending.strip():  # the file ends on a `\\`
        lines.append((start, pending.partition(" #")[0].strip()))
    return lines


def unpinned_requirements(requirements: Path) -> list[str]:
    """The requirements in a pip requirements file that are not pinned: no ``==``, and no
    commit SHA (``git+URL@<40 hex>``) or ``#sha256=`` on a URL. An ``-e`` path is listed too:
    it installs whatever the tree holds. Other options (``-r``, ``--hash``) are not
    requirements; a ``\\`` continues a line."""
    unpinned = []
    for _, text in _requirement_lines(requirements):
        editable = _EDITABLE.match(text)
        if text.startswith("-") and not editable:
            continue
        requirement = text[editable.end():].partition(" --")[0].strip() if editable else text
        if "==" in requirement.partition(";")[0]:
            continue
        if editable and "://" not in requirement and not requirement.startswith("git+"):
            unpinned.append(text)  # a local path: unversioned, whatever is in the tree
        elif not (_SHA_PINNED_VCS.search(requirement) or _HASHED_URL.search(requirement)):
            unpinned.append(requirement)
    return unpinned


def vcs_requirements(requirements: Path) -> list[str]:
    """The ``file:line: text`` of each requirement taken from version control (``git+https://``
    and the like): a commit SHA pins the source, but uv's --require-hashes wants a hash."""
    return [f"{requirements.name}:{number}: {text}" for number, text in _requirement_lines(
        requirements) if _VCS.search(text)]


def _workspace_table(pyproject: Path) -> dict[str, Any] | None:
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError:
        return None
    workspace = data.get("tool", {}).get("uv", {}).get("workspace")
    return workspace if isinstance(workspace, dict) else None
