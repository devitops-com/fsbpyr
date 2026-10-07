"""Project discovery: pyproject.toml / setup.py, Python constraints and lock source (§3.6)."""

import tomllib
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name

DEFAULT_REQUIREMENTS = "requirements.txt"


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
    tool: dict[str, Any] = field(default_factory=dict)  # the [tool.fsbpyr] table

    @classmethod
    def load(cls, directory: Path) -> Project:
        directory = directory.resolve()
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            try:
                data = tomllib.loads(pyproject.read_text())
            except tomllib.TOMLDecodeError as exc:
                raise ProjectError(f"{pyproject}: {exc}") from exc
        elif (directory / "setup.py").is_file() or (directory / "setup.cfg").is_file():
            data = {}
        else:
            raise ProjectError(f"{directory} has no pyproject.toml or setup.py")
        table = data.get("project", {})
        name = table.get("name")
        return cls(
            directory=directory,
            name=canonicalize_name(name) if isinstance(name, str) else None,
            requires_python=table.get("requires-python"),
            scripts=dict(table.get("scripts", {})),
            tool=dict(data.get("tool", {}).get("fsbpyr", {})),
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

    def python_version_mismatch(self, python: str) -> str | None:
        """The .python-version pin if it names a different major.minor than ``python``."""
        pin_file = self.directory / ".python-version"
        if not pin_file.is_file():
            return None
        lines = pin_file.read_text().split()
        pin = lines[0].rpartition("@")[2] if lines else ""
        return None if pin in ("", python) or pin.startswith(python + ".") else pin

    def check_python(self, version: str) -> None:
        if self.requires_python is None:
            return
        try:
            allowed = SpecifierSet(self.requires_python)
        except InvalidSpecifier as exc:
            raise ProjectError(f"invalid requires-python {self.requires_python!r}") from exc
        if version not in allowed:
            raise ProjectError(
                f"requires-python {self.requires_python!r} excludes the runtime's Python {version}"
            )


def _workspace_table(pyproject: Path) -> dict[str, Any] | None:
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError:
        return None
    workspace = data.get("tool", {}).get("uv", {}).get("workspace")
    return workspace if isinstance(workspace, dict) else None
