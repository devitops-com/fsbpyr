"""The wheel of a project that has no build system (research UC3, D5, owner decision 2026-10-25).

``uv run main.py`` runs the Python files of a directory as they lie. fsbpyr writes the wheel of
them itself, with the standard library alone: no build backend runs, no project code runs and
nothing is written into the source tree. The wheel holds the project's ``*.py`` files and
nothing else (a ``.env`` or a key file is never in it) under the reserved root ``_fsbpyr_scripts/``,
which keeps their relative paths; the boot puts the script's directory on ``sys.path`` first, so
siblings import as they do under ``uv run``. The files come from ``git ls-files`` inside a git
work tree (an untracked Python file is left out, with a warning), else from a walk that skips
what is not the project's.

A directory with no pyproject.toml, setup.py or setup.cfg is packaged the same way (research
UC6): its Python files are the ``*.py`` files and the extensionless files whose first line is a
python or ``uv run`` shebang; every file with a main guard or such a shebang is a command, named
after the file. A file or directory whose name is no Python name (``check-disk``, ``sub-dir/``)
is stored under a mangled name in the same place (``check_disk.py``, ``sub_dir/``) and still
runs as ``__main__``; its ``__file__`` is that path inside the executable, and the boot's path
hook maps the entry ``<exe>/_fsbpyr_scripts/sub-dir`` to ``sub_dir`` by the same rule. Dependencies
come from requirements.txt or pylock.toml, else from the union of the commands' PEP 723 blocks.
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
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from packaging.markers import Marker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .entry import _SKIPPED_DIRECTORIES, has_main_guard
from .links import COMMAND_NAME
from .project import ProjectError, settings_only
from .vcs import work_tree_files

ROOT = "_fsbpyr_scripts"  # the wheel's top-level directory; a module is ``_fsbpyr_scripts.<path>``
MODULE_GROUP = "fsbpyr_scripts"  # entry_points.txt: commands that run a module as __main__
DEFAULT_VERSION = "0"  # a project without a static version
_SKIPPED = _SKIPPED_DIRECTORIES | {"site-packages"}
_TEST_DIRECTORIES = frozenset({"test", "tests"})  # not commands
_MAIN_STEMS = frozenset({"main", "__main__"})  # take the project's name as a command
_MODE = 0o100644 << 16  # a regular file, rw-r--r--
_EXTRAS_SUFFIX = re.compile(r"\s*\[[^\]]*\]\s*$")
_SHEBANG_PROGRAM = re.compile(r"python3?(?:\.\d+)?")  # not python2: Python 2 files do not run
_SHEBANG_HEAD = 512  # bytes read of an extensionless file to see whether it is a script
# PEP 723's reference expression for an inline metadata block
_BLOCK = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")
_MINORS = range(41)  # Python 3.0 to 3.40 and a few patch levels: a range of versions is empty
_PATCHES = (0, 1, 2, 5, 10, 20, 99)  # when none of them is in it (requires-python of blocks)


class NoPythonFiles(ProjectError):
    """A directory without pyproject.toml (or with settings only) holds no Python file."""


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
    # a directory without pyproject.toml: the project file of each file stored under another
    # name (payload path -> relative path), and what the commands' PEP 723 blocks say
    origins: dict[str, str] = field(default_factory=dict)
    requires_python: str | None = None
    dependencies: tuple[str, ...] = ()
    # a tool binary (tools.py): no file and no command of its own, the requirements are the tools
    tools: bool = False

    @classmethod
    def load(
        cls, directory: Path, *, declared: bool = False, script: Path | None = None
    ) -> Package:
        """The package of ``directory``. Without a pyproject.toml the dependencies are those of
        the commands' PEP 723 blocks, unless ``declared``: a requirements file or a lock
        declares them (the blocks' requires-python still counts). With ``script``, a file of
        ``directory``: that file and what it imports from there, and its own block."""
        directory = directory.resolve()
        if script is not None:
            return cls._from_script(directory, script, declared=declared)
        if directory in (Path("/"), Path.home().resolve()):
            raise ProjectError(f"{directory} is not a project directory: fsbpyr packages the "
                               "Python files below it, and that is all of them")  # fmt: skip
        if not (directory / "pyproject.toml").is_file():
            return cls._from_directory(directory, declared=declared)
        try:
            data = tomllib.loads((directory / "pyproject.toml").read_text())
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ProjectError(f"{directory / 'pyproject.toml'}: {exc}") from exc
        if settings_only(directory, data):
            return cls._from_directory(directory, declared=declared)
        table = data["project"]
        if not isinstance(table.get("name"), str):
            raise ProjectError(f"{directory / 'pyproject.toml'}: [project] needs a name")
        name = canonicalize_name(table["name"])
        sources, warnings = discover(directory)
        if not sources:
            raise ProjectError(f"{directory} has no Python files to package")
        return cls(directory, name, _version(table), _metadata(table), sources,
                   _commands(name, table, sources), warnings)  # fmt: skip

    @classmethod
    def _from_directory(cls, directory: Path, *, declared: bool) -> Package:
        name = directory_name(directory)
        sources, origins, warnings = discover_directory(directory)
        if not sources:
            what = ("a pyproject.toml of settings only" if (directory / "pyproject.toml").is_file()
                    else "no pyproject.toml or setup.py")  # fmt: skip
            raise NoPythonFiles(f"{directory} has {what} and no Python files to package")
        commands, files = _directory_commands(name, sources, origins, warnings)
        dependencies, requires_python = _inline_requirements(files)
        warnings += _ignored_settings(files)
        table: dict[str, Any] = {"name": name, "dependencies": [] if declared else dependencies}
        if requires_python is not None:
            table["requires-python"] = requires_python
        return cls(directory, name, _version(table), _metadata(table), sources, commands,
                   tuple(warnings), origins, requires_python, tuple(dependencies))  # fmt: skip

    @classmethod
    def tool_binary(cls, directory: Path, taken: Collection[str]) -> Package:
        """The empty project of a tool binary (research candidate 10): named after the directory,
        or ``<name>-tools`` when a requirement has that name (installing the project would
        replace that distribution). Its wheel holds metadata only; the tools' console scripts
        are its commands, found after the install (tools.py)."""
        name = directory_name(directory)
        name = f"{name}-tools" if name in taken else name
        table: dict[str, Any] = {"name": name}
        return cls(directory, name, _version(table), _metadata(table), {}, {}, (),
                   tools=True)  # fmt: skip

    @classmethod
    def _from_script(cls, directory: Path, script: Path, *, declared: bool) -> Package:
        """One script (research UC8): named and commanded after the file's stem, holding the
        script and its static local-import closure, with the dependencies and requires-python
        of its PEP 723 block. The rest of the directory is not read."""
        from .importcheck import local_closure  # noqa: PLC0415  importcheck imports this module

        data = script.read_bytes()
        command = script.stem
        if not COMMAND_NAME.fullmatch(command):
            raise ProjectError(f"{script.name}: {command!r} is not a command name (letters, "
                               "digits and _.+-, starting with a letter or digit)")  # fmt: skip
        payload = _module_stem(command) + ".py"
        if script.suffix != ".py" and command.isidentifier():  # python3 never imports it
            payload = f"_x_{payload}"
        sources = local_closure(directory, payload, data, script.name)
        origins = {path: script.name if path == payload else path for path in sources}
        dependencies, requires_python = _inline_requirements({script.name: data})
        name = clean_name(command)
        table: dict[str, Any] = {"name": name, "dependencies": [] if declared else dependencies}
        if requires_python is not None:
            table["requires-python"] = requires_python
        return cls(directory, name, _version(table), _metadata(table), sources,
                   {command: _module(payload)}, (), origins, requires_python,
                   tuple(dependencies))  # fmt: skip

    def with_dependencies(self, dependencies: Sequence[str]) -> Package:
        """This package with ``dependencies`` (requirement lines) as its Requires-Dist: the pins
        the venv rung found for a project that declares none."""
        table: dict[str, Any] = {"name": self.name, "version": self.version,
                                 "dependencies": list(dependencies)}  # fmt: skip
        if self.requires_python is not None:
            table["requires-python"] = self.requires_python
        return replace(self, metadata=_metadata(table))

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


def _project_files(
    directory: Path, *, scripts: bool, warnings: list[str]
) -> Iterator[tuple[str, Path, bool]]:
    """(relative path, real file, is a ``*.py`` file) of each regular file of the project that
    can be Python: the ``*.py`` files and, with ``scripts``, the extensionless ones (names
    starting with a dot are skipped then). A link to a file outside the tree is an error for a
    ``*.py`` file; an extensionless one is passed over unread. In a git work tree only the
    tracked files count; an untracked Python file that is not ignored is added to ``warnings``."""
    listed = work_tree_files(directory)
    venvs: dict[str, bool] = {}
    if listed is None:
        paths = _walk(directory)
    else:
        paths = listed[0]
        for rel in listed[1]:
            parts = rel.split("/")
            if _is_python_file(directory / rel, parts[-1], scripts=scripts) and not _pruned(
                directory, parts[:-1], venvs
            ):
                warnings.append(f"{rel} is not packaged: git does not track it; `git add` it "
                                "(or ignore it) so that the build knows whether it belongs "
                                "to the project")  # fmt: skip
    root = Path(os.path.realpath(directory))
    for rel in sorted(paths):
        parts = rel.split("/")
        python = rel.endswith(".py")
        if scripts and parts[-1].startswith("."):
            continue
        if not (python or (scripts and "." not in parts[-1])) or _pruned(
            directory, parts[:-1], venvs
        ):
            continue
        path = directory / rel
        if not path.is_file():  # a tracked file removed, a broken link, a link to a directory
            continue
        real = Path(os.path.realpath(path))
        if real.is_relative_to(root):
            target = real.relative_to(root).parts  # a link must not lead to what is skipped
            if target[-1].startswith(".") or _pruned(directory, list(target[:-1]), venvs):
                continue
            if not (target[-1].endswith(".py") or (scripts and "." not in target[-1])):
                continue
            yield rel, real, python
        elif python:
            raise ProjectError(f"{rel} is a link to {real}, outside the project directory; "
                               "fsbpyr packages the project's own files only")  # fmt: skip


def _is_python_file(path: Path, leaf: str, *, scripts: bool) -> bool:
    """Whether a file can be Python by its name (``*.py``) or, with ``scripts``, by the shebang
    of an extensionless one."""
    if leaf.endswith(".py"):
        return True
    if not scripts or "." in leaf:
        return False
    try:
        with path.open("rb") as handle:
            return python_shebang(handle.read(_SHEBANG_HEAD))
    except OSError:
        return False


def discover(directory: Path) -> tuple[dict[str, bytes], tuple[str, ...]]:
    """The project's Python files (relative posix path -> content, sorted) and warnings for the
    files that cannot be imported. A symlink to a file outside the tree is an error."""
    sources: dict[str, bytes] = {}
    warnings: list[str] = []
    for rel, real, _ in _project_files(directory, scripts=False, warnings=warnings):
        parts = rel.split("/")
        if not all(part.isidentifier() for part in (*parts[:-1], parts[-1].removesuffix(".py"))):
            warnings.append(f"{rel} is not packaged: its name or a directory name is not a valid "
                            "Python name, so no import reaches it")  # fmt: skip
            continue
        sources[rel] = real.read_bytes()
    return sources, tuple(warnings)


def python_shebang(data: bytes) -> bool:
    """Whether the first line of ``data`` is a shebang that starts Python 3 (``python``,
    ``python3``, ``python3.12``, directly or through ``env``) or ``uv run``; text only."""
    head = data[:_SHEBANG_HEAD]
    if not head.startswith(b"#!") or b"\0" in head:
        return False
    words = head[2:].split(b"\n", 1)[0].decode("utf-8", "replace").split()
    if words and os.path.basename(words[0]) == "env":  # options and VAR=value come first
        words = [word for word in words[1:] if not word.startswith("-") and "=" not in word]
    if not words:
        return False
    program = os.path.basename(words[0])
    if program == "uv":
        return words[1:2] == ["run"]
    return _SHEBANG_PROGRAM.fullmatch(program) is not None


def directory_name(directory: Path) -> str:
    """The project name of a directory without pyproject.toml: its name, canonical, with what a
    project name cannot hold replaced."""
    return clean_name(directory.name)


def clean_name(text: str) -> str:
    """``text`` as a project name: canonical, with what a project name cannot hold replaced."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._")
    return canonicalize_name(cleaned or "scripts")


def _module_stem(stem: str) -> str:
    """``stem`` as a module name: what no identifier holds becomes ``_``, and a name that starts
    with a digit gets a ``_`` in front (``check-disk`` -> ``check_disk``, ``2fa`` -> ``_2fa``)."""
    if stem.isidentifier():
        return stem
    mangled = "".join(char if f"_{char}".isidentifier() else "_" for char in stem)
    return mangled if mangled.isidentifier() else f"_{mangled}"


def script_stem(leaf: str, *, python: bool) -> str:
    """The module name a script file is stored under: ``check-disk`` -> ``check_disk``, and
    ``_x_tool`` for an extensionless ``tool`` (python3 never imports an extensionless file, so
    no import may reach it, nor may it shadow a module of that name)."""
    stem = _module_stem(leaf.removesuffix(".py"))
    return stem if python or not leaf.isidentifier() else f"_x_{stem}"


def discover_directory(directory: Path) -> tuple[dict[str, bytes], dict[str, str], list[str]]:
    """The Python files of a directory without pyproject.toml: payload path -> content (sorted),
    payload path -> project file, and warnings. A file whose name is no Python name is stored as
    ``<mangled name>.py`` in its directory, a directory that is no Python name under its mangled
    name; two files with one payload path, or two directories with one mangled name, are an
    error."""
    sources: dict[str, bytes] = {}
    origins: dict[str, str] = {}
    warnings: list[str] = []
    folders_of: dict[str, str] = {}  # payload directory -> project directory
    for rel, real, python in _project_files(directory, scripts=True, warnings=warnings):
        if not python:
            with real.open("rb") as handle:
                if not python_shebang(handle.read(_SHEBANG_HEAD)):
                    continue
        *project_folders, leaf = rel.split("/")
        folders = _payload_folders(project_folders, folders_of)
        payload = "/".join([*folders, script_stem(leaf, python=python) + ".py"])
        if payload in origins:
            raise ProjectError(f"{origins[payload]} and {rel} are both {payload} in the "
                               "executable (a name that is no Python name is stored with '_' "
                               "for each character that is not one); rename one of them"
                               )  # fmt: skip
        sources[payload], origins[payload] = real.read_bytes(), rel
    for where, project in folders_of.items():
        if where != project and f"{where}.py" in sources:
            raise ProjectError(f"the directory {project} and {origins[where + '.py']} are both "
                               f"{where} in the executable; rename one of them")
    return dict(sorted(sources.items())), origins, warnings


def _payload_folders(folders: list[str], taken: dict[str, str]) -> list[str]:
    """The payload names of a file's directories (each one that is no Python name mangled);
    two project directories with one payload path are an error. ``taken`` maps the payload
    paths seen to their project paths."""
    payload: list[str] = []
    for depth, part in enumerate(folders, 1):
        payload.append(_module_stem(part))
        where, project = "/".join(payload), "/".join(folders[:depth])
        if taken.setdefault(where, project) != project:
            raise ProjectError(f"the directories {taken[where]} and {project} are both {where} "
                               "in the executable (a name that is no Python name is stored with "
                               "'_' for each character that is not one); rename one of them")
    return payload


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


def _candidate(path: str, data: bytes, *, shebang: bool = False) -> bool:
    """Whether a file can be a command: a top-level ``__main__.py`` or any file with a main
    guard (or, with ``shebang``, a python shebang), except setup.py, packages'
    ``__init__``/``__main__`` and test code."""
    parts = path.split("/")
    leaf = parts[-1]
    if (path == "setup.py" or leaf in {"__init__.py", "conftest.py"}
            or leaf.startswith("test_") or leaf.endswith("_test.py")
            or any(part in _TEST_DIRECTORIES for part in parts[:-1])):  # fmt: skip
        return False
    if leaf == "__main__.py":
        return len(parts) == 1
    return has_main_guard(data) or (shebang and python_shebang(data))


def _command_name(project: str, path: str) -> str:
    stem = path.rpartition("/")[2].removesuffix(".py")
    return project if stem in _MAIN_STEMS else stem


def _directory_commands(
    name: str, sources: dict[str, bytes], origins: dict[str, str], warnings: list[str]
) -> tuple[dict[str, str], dict[str, bytes]]:
    """The commands of a directory without pyproject.toml, one per file with a main guard or a
    python shebang (the file's name, ``main`` and ``__main__`` after the directory), and the
    contents of those files by project path. Two files with one name are an error; a name that
    is no command name is a warning, and the file stays a module."""
    commands: dict[str, str] = {}
    owner: dict[str, str] = {}
    files: dict[str, bytes] = {}
    for payload, data in sources.items():
        origin = origins[payload]
        if not _candidate(origin, data, shebang=True):
            continue
        command = _command_name(name, origin)
        if not COMMAND_NAME.fullmatch(command):
            warnings.append(f"{origin} is not a command: {command!r} is not a command name "
                            "(letters, digits and _.+-, starting with a letter or digit)"
                            )  # fmt: skip
            continue
        if command in owner:
            raise ProjectError(f"{owner[command]} and {origin} are both the command "
                               f"{command!r}; rename one of them")  # fmt: skip
        commands[command], owner[command], files[origin] = _module(payload), origin, data
    return commands, files


def inline_metadata(source: bytes, where: str) -> dict[str, Any]:
    """The TOML of the ``# /// script`` block of a file (PEP 723), empty without one."""
    text = source.decode("utf-8", "replace")
    blocks = [block for block in _BLOCK.finditer(text) if block["type"] == "script"]
    if len(blocks) > 1:
        raise ProjectError(f"{where} has more than one '# /// script' block")
    if not blocks:
        return {}
    lines = blocks[0]["content"].splitlines(keepends=True)
    toml = "".join(line[2:] if line.startswith("# ") else line[1:] for line in lines)
    try:
        return tomllib.loads(toml)
    except tomllib.TOMLDecodeError as exc:
        line = text.count("\n", 0, blocks[0].start()) + 1
        raise ProjectError(f"{where}:{line}: invalid '# /// script' block: {exc}") from exc


def _ignored_settings(files: dict[str, bytes]) -> list[str]:
    """A warning for each file whose PEP 723 block has a ``[tool.fsbpyr]`` table: a directory
    build takes its settings from the command line and the directory's pyproject.toml."""
    message = ("{}: [tool.fsbpyr] in a '# /// script' block is ignored in a directory build; "
               "pass the option on the command line or put it in [tool.fsbpyr] of the "
               "directory's pyproject.toml")  # fmt: skip
    return [message.format(where) for where, data in sorted(files.items())
            if "fsbpyr" in (inline_metadata(data, where).get("tool") or {})]


def _inline_requirements(files: dict[str, bytes]) -> tuple[list[str], str | None]:
    """The union of the dependencies of the files' PEP 723 blocks (each requirement once, in
    file order) and the intersection of their requires-python; an error naming the files when
    that leaves no Python version."""
    dependencies: dict[str, None] = {}
    specifiers: dict[str, SpecifierSet] = {}
    for where, data in sorted(files.items()):
        block = inline_metadata(data, where)
        listed = block.get("dependencies", [])
        if not isinstance(listed, list):
            raise ProjectError(f"{where}: PEP 723 dependencies must be a list")
        for text in listed:
            dependencies[str(_requirement(text, f"{where}: PEP 723 dependencies"))] = None
        if (declared := block.get("requires-python")) is not None:
            try:
                specifiers[where] = SpecifierSet(str(declared))
            except InvalidSpecifier as exc:
                raise ProjectError(f"{where}: invalid requires-python {declared!r}") from exc
    if not specifiers:
        return list(dependencies), None
    combined = SpecifierSet(",".join(map(str, specifiers.values())))
    if not any(f"3.{minor}.{patch}" in combined for minor in _MINORS for patch in _PATCHES):
        raise ProjectError("the requires-python of the PEP 723 blocks leave no Python version: "
                           + ", ".join(f"{where} {spec}" for where, spec in specifiers.items()))
    return list(dependencies), str(combined)


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
