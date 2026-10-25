"""The wheel of a project that has no build system (research UC3, D5, owner decision 2026-10-25).

``uv run main.py`` runs the Python files of a directory as they lie. fsbpyr writes the wheel of
them itself, with the standard library alone: no build backend runs, no project code runs and
nothing is written into the source tree. The wheel holds the project's ``*.py`` files and
nothing else (a ``.env`` or a key file is never in it) under the reserved root ``_fsbpyr_scripts/``,
which keeps their relative paths; the boot puts the script's directory on ``sys.path`` first, so
siblings import as they do under ``uv run``. The files come from ``git ls-files`` inside a git
work tree, else from a walk that skips what is not the project's.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import re
import time
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.markers import Marker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .entry import _SKIPPED_DIRECTORIES, has_main_guard
from .project import ProjectError
from .vcs import work_tree_files

ROOT = "_fsbpyr_scripts"  # the wheel's top-level directory; a module is ``_fsbpyr_scripts.<path>``
MODULE_GROUP = "fsbpyr_scripts"  # entry_points.txt: commands that run a module as __main__
DEFAULT_VERSION = "0"  # a project without a static version
_SKIPPED = _SKIPPED_DIRECTORIES | {"site-packages"}
_TEST_DIRECTORIES = frozenset({"test", "tests"})  # not commands
_MAIN_STEMS = frozenset({"main", "__main__"})  # take the project's name as a command
_MODE = 0o100644 << 16  # a regular file, rw-r--r--
_EXTRAS_SUFFIX = re.compile(r"\s*\[[^\]]*\]\s*$")


@dataclass(frozen=True, slots=True)
class Package:
    """A project's files and commands, read once: what the build checks is what it writes."""

    directory: Path
    name: str  # canonical
    version: str
    metadata: tuple[str, ...]  # METADATA's header lines
    sources: dict[str, bytes]  # relative posix path -> content, sorted
    # command -> target below ROOT: "pkg.mod" (run as __main__) or "pkg.mod:attr"
    commands: dict[str, str]
    warnings: tuple[str, ...]

    @classmethod
    def load(cls, directory: Path) -> Package:
        directory = directory.resolve()
        if directory in (Path("/"), Path.home().resolve()):
            raise ProjectError(f"{directory} is not a project directory: fsbpyr packages the "
                               "Python files below it, and that is all of them")  # fmt: skip
        try:
            data = tomllib.loads((directory / "pyproject.toml").read_text())
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ProjectError(f"{directory / 'pyproject.toml'}: {exc}") from exc
        table = data["project"]
        if not isinstance(table.get("name"), str):
            raise ProjectError(f"{directory / 'pyproject.toml'}: [project] needs a name")
        name = canonicalize_name(table["name"])
        sources, warnings = discover(directory)
        if not sources:
            raise ProjectError(f"{directory} has no Python files to package")
        return cls(directory, name, _version(table), _metadata(table), sources,
                   _commands(name, table, sources), warnings)  # fmt: skip

    def wheel_name(self) -> str:
        return f"{re.sub(r'[-_.]+', '_', self.name)}-{self.version}-py3-none-any.whl"

    def write_wheel(self, wheel_dir: Path, epoch: int) -> Path:
        """The wheel, in ``wheel_dir``: sorted entries stamped ``epoch``, a sorted RECORD, so
        two writes of the same files are the same bytes."""
        dist_info = f"{re.sub(r'[-_.]+', '_', self.name)}-{self.version}.dist-info"
        files = {f"{ROOT}/{path}": data for path, data in self.sources.items()}
        files[f"{dist_info}/METADATA"] = "\n".join(self.metadata).encode() + b"\n"
        files[f"{dist_info}/WHEEL"] = (b"Wheel-Version: 1.0\nGenerator: fsbpyr\n"
                                       b"Root-Is-Purelib: true\nTag: py3-none-any\n")  # fmt: skip
        if self.commands:
            files[f"{dist_info}/entry_points.txt"] = _entry_points(self.commands).encode()
        record = io.StringIO()
        rows = csv.writer(record, lineterminator="\n")
        for path, data in sorted(files.items()):
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
            rows.writerow([path, f"sha256={digest.decode()}", len(data)])
        files[f"{dist_info}/RECORD"] = (record.getvalue() + f"{dist_info}/RECORD,,\n").encode()
        stamp = time.gmtime(epoch)[:6]
        wheel_dir.mkdir(parents=True, exist_ok=True)
        wheel = wheel_dir / self.wheel_name()
        with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, data in sorted(files.items()):
                info = zipfile.ZipInfo(path, stamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr, info.create_system = _MODE, 3
                archive.writestr(info, data, compresslevel=9)
        return wheel


def _entry_points(commands: dict[str, str]) -> str:
    """entry_points.txt: ``module:function`` commands are console scripts; a module run as
    ``__main__`` has no function, which an installer refuses in console_scripts."""
    sections: dict[str, list[str]] = {"console_scripts": [], MODULE_GROUP: []}
    for name, target in sorted(commands.items()):
        group = "console_scripts" if ":" in target else MODULE_GROUP
        sections[group].append(f"{name} = {target}")
    return "".join(f"[{group}]\n" + "\n".join(lines) + "\n\n"
                   for group, lines in sections.items() if lines)  # fmt: skip


def _version(table: dict[str, Any]) -> str:
    version = table.get("version", DEFAULT_VERSION)
    try:
        return str(Version(version))
    except (InvalidVersion, TypeError) as exc:
        raise ProjectError(f"[project] version {version!r} is not a valid version") from exc


def _requirement(text: object, where: str) -> Requirement:
    try:
        return Requirement(str(text))
    except InvalidRequirement as exc:
        raise ProjectError(f"{where}: invalid requirement {text!r}: {exc}") from exc


def _metadata(table: dict[str, Any]) -> tuple[str, ...]:
    """METADATA's lines from [project]: name, version, requires-python, dependencies, extras."""
    lines = ["Metadata-Version: 2.1", f"Name: {table['name']}", f"Version: {_version(table)}"]
    if isinstance(table.get("requires-python"), str):
        lines.append(f"Requires-Python: {table['requires-python']}")
    lines += [f"Requires-Dist: {_requirement(d, 'dependencies')}"
              for d in table.get("dependencies", [])]  # fmt: skip
    for declared, requirements in sorted(table.get("optional-dependencies", {}).items()):
        extra = canonicalize_name(declared)
        lines.append(f"Provides-Extra: {extra}")
        for text in requirements:
            requirement = _requirement(text, f"optional-dependencies.{extra}")
            condition = f'extra == "{extra}"'
            requirement.marker = Marker(
                f"({requirement.marker}) and {condition}" if requirement.marker else condition
            )
            lines.append(f"Requires-Dist: {requirement}")
    return tuple(lines)


def discover(directory: Path) -> tuple[dict[str, bytes], tuple[str, ...]]:
    """The project's Python files (relative posix path -> content, sorted) and warnings for the
    files that cannot be imported. A symlink to a file outside the tree is an error."""
    listed = work_tree_files(directory)
    paths = listed or _walk(directory)
    root = Path(os.path.realpath(directory))
    venvs: dict[str, bool] = {}
    sources: dict[str, bytes] = {}
    warnings: list[str] = []
    for rel in sorted(paths):
        parts = rel.split("/")
        if not rel.endswith(".py") or _pruned(directory, parts[:-1], venvs):
            continue
        path = directory / rel
        if not path.is_file():  # a tracked file removed, a broken link, a link to a directory
            continue
        real = Path(os.path.realpath(path))
        if not real.is_relative_to(root):
            raise ProjectError(f"{rel} is a link to {real}, outside the project directory; "
                               "fsbpyr packages the project's own files only")  # fmt: skip
        if not all(part.isidentifier() for part in (*parts[:-1], parts[-1].removesuffix(".py"))):
            warnings.append(f"{rel} is not packaged: its name or a directory name is not a valid "
                            "Python name, so no import reaches it")  # fmt: skip
            continue
        sources[rel] = real.read_bytes()
    return sources, tuple(warnings)


def _pruned(directory: Path, parts: list[str], venvs: dict[str, bool]) -> bool:
    """Whether a file's directories (names, from the project) are not the project's: hidden,
    build output, dependencies or a virtual environment."""
    for index, part in enumerate(parts):
        if part.startswith(".") or part in _SKIPPED:
            return True
        prefix = "/".join(parts[: index + 1])
        if prefix not in venvs:
            venvs[prefix] = (directory / prefix / "pyvenv.cfg").exists()
        if venvs[prefix]:
            return True
    return False


def _walk(directory: Path) -> list[str]:
    """Every file below ``directory`` without following links to directories."""
    found = []
    for base, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIPPED]
        found += [Path(base, name).relative_to(directory).as_posix() for name in files]
    return found


def _module(path: str) -> str:
    """The module a path below ROOT is, ``sub/tool.py`` -> ``_fsbpyr_scripts.sub.tool``."""
    return ".".join((ROOT, *path.removesuffix(".py").split("/")))


def _exists(sources: dict[str, bytes], module: str) -> bool:
    """Whether ``a.b`` is a project file (``a/b.py``) or package (``a/b/...``)."""
    path = module.replace(".", "/")
    return f"{path}.py" in sources or any(name.startswith(f"{path}/") for name in sources)


def _commands(name: str, table: dict[str, Any], sources: dict[str, bytes]) -> dict[str, str]:
    """[project.scripts] with each project module moved below ROOT, else the modules with a main
    guard: one is the project's command, several are one command each (named after the file,
    ``main`` and ``__main__`` after the project)."""
    scripts = table.get("scripts", {})
    if scripts:
        return {script: _rewrite(str(target), sources) for script, target in scripts.items()}
    guarded = {path: _command_name(name, path) for path, data in sources.items()
               if _candidate(path, data)}  # fmt: skip
    if len(guarded) == 1:
        return {name: _module(next(iter(guarded)))}
    commands: dict[str, str] = {}
    owner: dict[str, str] = {}
    for path, command in guarded.items():
        if command in owner:
            raise ProjectError(f"{owner[command]} and {path} are both the command {command!r}; "
                               "rename one of them")  # fmt: skip
        commands[command], owner[command] = _module(path), path
    return commands


def _candidate(path: str, data: bytes) -> bool:
    """Whether a file can be a command: a top-level ``__main__.py`` or any file with a main
    guard, except setup.py, packages' ``__init__``/``__main__`` and test code."""
    parts = path.split("/")
    leaf = parts[-1]
    if (path == "setup.py" or leaf in {"__init__.py", "conftest.py"}
            or leaf.startswith("test_") or leaf.endswith("_test.py")
            or any(part in _TEST_DIRECTORIES for part in parts[:-1])):  # fmt: skip
        return False
    if leaf == "__main__.py":
        return len(parts) == 1
    return has_main_guard(data)


def _command_name(project: str, path: str) -> str:
    stem = path.rpartition("/")[2].removesuffix(".py")
    return project if stem in _MAIN_STEMS else stem


def _rewrite(target: str, sources: dict[str, bytes]) -> str:
    """``mod:attr`` of a project module as ``_fsbpyr_scripts.mod:attr``; another module (a
    dependency's) stays as it is."""
    module, colon, attribute = _EXTRAS_SUFFIX.sub("", target).strip().partition(":")
    if not _exists(sources, module.strip()):
        if _exists(sources, f"src.{module.strip()}"):
            raise ProjectError(f"[project.scripts] {target!r}: the project has no build system, "
                               "so src/ is kept in the path; write "
                               f"'{ROOT}.src.{module.strip()}{colon}{attribute.strip()}'"
                               )  # fmt: skip
        return f"{module.strip()}{colon}{attribute.strip()}"
    return f"{ROOT}.{module.strip()}{colon}{attribute.strip()}"
