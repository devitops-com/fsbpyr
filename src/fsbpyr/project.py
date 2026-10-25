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
    SCRIPT_LOCK = "script.lock"  # uv's `<script>.lock` (uv lock --script), a uv.lock for one file
    POETRY_LOCK = "poetry.lock"  # converted to requirements at staging (foreign.py)
    PDM_LOCK = "pdm.lock"
    # no declared source, the versions found in the scripts' venv (venvdeps.py); staged like
    # an unlocked build, from pins in the project's wheel
    INFERRED = "inferred"
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
    # setup.py or setuptools' script-files may install legacy scripts, which are commands too
    # (legacy.py); only the wheel tells which
    may_have_legacy_scripts: bool = False
    # no build system to run: fsbpyr writes the wheel of the project's Python files (loose.py)
    loose: bool = False
    # a directory with no pyproject.toml, setup.py or setup.cfg: loose, named after the directory,
    # with the dependencies its commands' PEP 723 blocks declare
    bare: bool = False
    dependencies: tuple[str, ...] = ()
    # one script (research UC8): `directory` is its directory, which nothing else of is read
    script: Path | None = None

    @classmethod
    def load(cls, directory: Path) -> Project:
        directory = directory.resolve()
        if directory.is_file():
            return cls._script(directory)
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            try:
                data = tomllib.loads(pyproject.read_text())
            except tomllib.TOMLDecodeError as exc:
                raise ProjectError(f"{pyproject}: {exc}") from exc
            if settings_only(directory, data):
                return cls._bare(directory, tool=dict(data.get("tool", {}).get("fsbpyr", {})))
        elif (directory / "setup.py").is_file():
            data = {}
        elif (directory / "setup.cfg").is_file():
            raise ProjectError(f"{directory} has a setup.cfg but no pyproject.toml or setup.py; "
                               "setup.cfg alone does not say how to build it")  # fmt: skip
        else:
            return cls._bare(directory)
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
            may_have_legacy_scripts=(directory / "setup.py").is_file() or bool(
                data.get("tool", {}).get("setuptools", {}).get("script-files")),
            loose=_without_build_system(data),
        )

    @classmethod
    def _bare(cls, directory: Path, tool: dict[str, Any] | None = None) -> Project:
        """A directory of Python files (research UC6); its commands, and what their PEP 723
        blocks declare, come from reading the files. ``tool``: the ``[tool.fsbpyr]`` table of a
        pyproject.toml that holds settings and nothing else."""
        from .loose import Package  # noqa: PLC0415  loose imports this module's error

        package = Package.load(directory)
        return cls(directory=directory, name=package.name, requires_python=package.requires_python,
                   scripts=dict(package.commands), tool=tool or {}, loose=True, bare=True,
                   dependencies=package.dependencies)  # fmt: skip

    @classmethod
    def _script(cls, script: Path) -> Project:
        """One script, ``*.py`` or extensionless with a python or `uv run` shebang: its
        directory's pyproject.toml is not read. Its PEP 723 block gives the dependencies,
        requires-python and ``[tool.fsbpyr]``."""
        from .loose import Package, inline_metadata, python_shebang  # noqa: PLC0415  see _bare

        data = script.read_bytes()
        if script.suffix != ".py" and (script.suffix or not python_shebang(data)):
            raise ProjectError(f"{script} is a file, but not a Python script: fsbpyr builds a "
                               "`*.py` file, an extensionless file whose first line is a python "
                               "or `uv run --script` shebang, or a project directory")  # fmt: skip
        tools = inline_metadata(data, script.name).get("tool", {})
        tool = tools.get("fsbpyr", {}) if isinstance(tools, dict) else None
        if not isinstance(tool, dict):
            raise ProjectError(f"{script.name}: [tool.fsbpyr] in the '# /// script' block "
                               "must be a table")  # fmt: skip
        package = Package.load(script.parent, script=script)
        return cls(directory=script.parent, name=package.name,
                   requires_python=package.requires_python, scripts=dict(package.commands),
                   tool=dict(tool), loose=True, bare=True, dependencies=package.dependencies,
                   script=script)  # fmt: skip

    @property
    def script_lock(self) -> Path | None:
        """uv's lock of the script, ``<script>.lock`` beside it, if it exists."""
        if self.script is None:
            return None
        lock = self.script.with_name(self.script.name + ".lock")
        return lock if lock.is_file() else None

    @property
    def uv_root(self) -> Path | None:
        """Directory whose uv.lock governs this project: itself, or its uv workspace root."""
        if self.bare:  # no pyproject.toml: uv has no project to sync
            return None
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
        requirements.txt, poetry.lock, pdm.lock, else unlocked (plan §3.6 3)."""
        if requirements is not None:
            if not self.requirements_path(requirements).is_file():
                raise ProjectError(
                    f"requirements file {requirements} not found in {self.directory}"
                )
            return LockSource.REQUIREMENTS
        if self.script is not None:  # a lock beside it, else nothing: the directory's files
            return LockSource.SCRIPT_LOCK if self.script_lock else LockSource.UNLOCKED
        if self.uv_root is not None:
            return LockSource.UV_LOCK
        if (self.directory / "pylock.toml").is_file():
            return LockSource.PYLOCK
        if self.requirements_path(None).is_file():
            return LockSource.REQUIREMENTS
        return self._foreign_lock() or LockSource.UNLOCKED

    def _foreign_lock(self) -> LockSource | None:
        """The Poetry or PDM lock beside the pyproject.toml of a project (not of a directory of
        scripts, whose dependencies are the scripts')."""
        for source in (LockSource.POETRY_LOCK, LockSource.PDM_LOCK):
            if not self.bare and (self.directory / source.value).is_file():
                return source
        return None

    def lock_path(self, source: LockSource, requirements: str | None = None) -> Path | None:
        """The file ``source`` reads; None when unlocked."""
        if source is LockSource.UV_LOCK and self.uv_root is not None:
            return self.uv_root / LockSource.UV_LOCK.value
        if source is LockSource.SCRIPT_LOCK:
            return self.script_lock
        if source in (LockSource.PYLOCK, LockSource.POETRY_LOCK, LockSource.PDM_LOCK):
            return self.directory / str(source.value)
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


def settings_only(directory: Path, data: dict[str, Any]) -> bool:
    """A pyproject.toml that holds tool settings (``[tool.fsbpyr]``, ``[tool.ruff]``) and no
    project: no [project], no [build-system], no Poetry, and no setup.py or setup.cfg beside
    it. Next to a directory of scripts it is their settings file, not a project for a build
    backend (whose flat-layout discovery fails on several top-level modules)."""
    return (set(data) <= {"tool"} and "poetry" not in data.get("tool", {})
            and not (directory / "setup.py").is_file()
            and not (directory / "setup.cfg").is_file())  # fmt: skip


def _without_build_system(data: dict[str, Any]) -> bool:
    """A [project] that no build system packages (D5, owner decision 2026-10-25): no
    [build-system] table (never setuptools' legacy fallback), or Poetry ``package-mode = false``
    / PDM ``distribution = false``. A uv workspace root keeps its build."""
    tool = data.get("tool", {})
    if "project" not in data or "workspace" in tool.get("uv", {}):
        return False
    return ("build-system" not in data
            or tool.get("poetry", {}).get("package-mode") is False
            or tool.get("pdm", {}).get("distribution") is False)  # fmt: skip


def _workspace_table(pyproject: Path) -> dict[str, Any] | None:
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError:
        return None
    workspace = data.get("tool", {}).get("uv", {}).get("workspace")
    return workspace if isinstance(workspace, dict) else None


def has_hash_option(text: str) -> bool:
    """Whether a non-comment line of requirements ``text`` has a ``--hash`` option (the test
    for uv's --require-hashes mode)."""
    return any(re.search(r"(?:^|\s)--hash[=\s]", raw.partition(" #")[0])
               for raw in text.splitlines() if not raw.lstrip().startswith("#"))
