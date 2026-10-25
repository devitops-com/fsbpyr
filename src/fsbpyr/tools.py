"""A tool binary: the console scripts of the tools a requirements file names (research §3,
candidate 10, owner decision 2026-10-25).

A directory with no Python file and a lock source (requirements.txt, pylock.toml, Pipfile.lock,
a requirements file given with ``--requirements``) is an empty project whose requirements are
the tools: the build installs the lock as for any project and the console scripts of the
distributions the file names become the executable's commands. A requirements file names its
distributions directly (``-r`` includes are followed, constraints name nothing). A file that
lists every installed distribution (``pip freeze``, pylock.toml, a Pipfile.lock) names the
dependencies too, so there the tools are the listed distributions no other listed distribution
requires; a Pipfile beside its lock names the tools in ``[packages]``. A dependency's
scripts are never commands. A tool with no console or gui script of its own that is a
meta-package (no importable code of its own: ``jupyter``) has its direct requirements as the
tools in its place; with code of its own it falls through only to a requirement named after it
(``ansible`` requires ``ansible-core``). Any other such distribution is a library, not a tool.
``gui_scripts`` are commands as ``console_scripts`` are: pip and uv install them as ordinary
launchers on Linux.
"""

from __future__ import annotations

import dataclasses
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from packaging.markers import InvalidMarker, Marker, UndefinedComparison, UndefinedEnvironmentName
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)

from .closure import ClosureError, InstalledDist, SiteIndex, marker_environment, requirement_of
from .foreign import ForeignLockError
from .pipenv import tool_names
from .project import LockSource, Project, ProjectError, requirement_lines

_INCLUDE = re.compile(r"(?:-r\s*|--requirement(?:\s+|=))(\S.*)")
_EDITABLE = re.compile(r"(?:-e|--editable)(?:\s+|=)\s*")
_EGG = re.compile(r"[#&]egg=([A-Za-z0-9._-]+)")
_FREEZE_OMITS = ("pip", "setuptools", "wheel", "distribute")  # what pip freeze never lists
_SHOWN = 5  # names in a message
_META_DEPTH = 8  # meta-packages followed down to a distribution with scripts


@dataclass(frozen=True, slots=True)
class Listed:
    """A distribution a lock source names: canonical name, the extras asked for and the marker
    that makes uv install it (None: always)."""

    name: str
    extras: frozenset[str] = frozenset()
    marker: Marker | None = None


@dataclass(frozen=True, slots=True)
class Tools:
    """The commands of a tool binary: command -> entry point, with the distribution each comes
    from, and the progress lines that say how the tools were chosen."""

    scripts: dict[str, str]
    owners: dict[str, str]
    notes: tuple[str, ...]
    requires: tuple[str, ...]  # the listed distributions, as the project's Requires-Dist


def listed_distributions(project: Project, source: LockSource, requirements: str | None
                         ) -> list[Listed]:  # fmt: skip
    """The distributions the lock source of a tool binary names, in file order."""
    path = project.lock_path(source, requirements)
    assert path is not None  # noqa: S101  a tool binary has a lock source
    if source is LockSource.PYLOCK:
        return _pylock(path)
    if source is LockSource.PIPFILE_LOCK:
        return _pipfile(path.parent)
    return _requirements(path, set())


def _pipfile(directory: Path) -> list[Listed]:
    """The Pipfile's ``[packages]`` are the tools; a Pipfile.lock alone names its dependencies
    as well, so it is read like a freeze."""
    try:
        return [Listed(name, extras, marker) for name, extras, marker in tool_names(directory)]
    except ForeignLockError as exc:
        raise ProjectError(str(exc)) from exc


def _pylock(path: Path) -> list[Listed]:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProjectError(f"{path}: {exc}") from exc
    found = []
    for package in data.get("packages", []):
        if not isinstance(package, dict) or not isinstance(package.get("name"), str):
            raise ProjectError(f"{path}: a [[packages]] entry has no name")
        found.append(Listed(canonicalize_name(package["name"]), marker=_marker(package, path)))
    return found


def _marker(package: dict[str, object], path: Path) -> Marker | None:
    text = package.get("marker")
    if not isinstance(text, str):
        return None
    try:
        return Marker(text)
    except InvalidMarker as exc:
        raise ProjectError(f"{path}: invalid marker {text!r} of {package.get('name')}: {exc}"
                           ) from exc  # fmt: skip


def _requirements(path: Path, including: set[Path]) -> list[Listed]:
    """The distributions of a requirements file and of the files it includes with ``-r``
    (relative to the including file); options, constraints (``-c``) and index settings name
    none."""
    path = path.resolve()
    if path in including:
        raise ProjectError(f"{path} includes itself with -r")
    try:
        lines = requirement_lines(path)
    except OSError as exc:
        raise ProjectError(f"cannot read requirements file {path}: {exc}") from exc
    found: list[Listed] = []
    for number, text in lines:
        if include := _INCLUDE.match(text):
            found += _requirements(path.parent / include[1].strip(), {*including, path})
            continue
        editable = _EDITABLE.match(text)
        if text.startswith("-") and not editable:
            continue
        found.append(_listed(text[editable.end():] if editable else text, f"{path.name}:{number}"))
    return found


def _listed(text: str, where: str) -> Listed:
    try:
        requirement = Requirement(text)
    except InvalidRequirement:
        return Listed(_url_name(text, where))
    return Listed(canonicalize_name(requirement.name), frozenset(requirement.extras),
                  requirement.marker)  # fmt: skip


def _url_name(text: str, where: str) -> str:
    """The distribution a bare URL names: its ``#egg=`` fragment, else the name in the file
    name of a wheel or an sdist."""
    if egg := _EGG.search(text):
        return canonicalize_name(egg[1])
    leaf = unquote(urlsplit(text.partition(" ")[0]).path).rpartition("/")[2]
    try:
        if leaf.endswith(".whl"):
            return parse_wheel_filename(leaf)[0]
        return parse_sdist_filename(leaf)[0]
    except (InvalidWheelFilename, InvalidSdistFilename):
        raise ProjectError(
            f"{where}: cannot tell which distribution {text!r} names; a tool binary needs the "
            "names: write it as `name @ URL`"
        ) from None


def _holds(marker: Marker | None, environment: dict[str, str]) -> bool:
    try:
        return marker is None or marker.evaluate({**environment, "extra": ""})
    except (UndefinedComparison, UndefinedEnvironmentName):
        return True


def _named(
    listed: list[Listed], index: SiteIndex, environment: dict[str, str], source: str
) -> dict[str, frozenset[str]]:
    """The listed distributions that are installed, with the extras asked for. One whose marker
    is false is not installed; one that is missing without a marker is an error."""
    named: dict[str, frozenset[str]] = {}
    for item in listed:
        if item.name in index.dists:
            if _holds(item.marker, environment):
                named[item.name] = named.get(item.name, frozenset()) | item.extras
        elif item.marker is None:
            raise ClosureError(f"{source} names {item.name}, which is not installed in the "
                               "staging site")  # fmt: skip
    return named


def _dependencies(
    name: str, extras: frozenset[str], index: SiteIndex, environment: dict[str, str]
) -> dict[str, frozenset[str]]:
    """The direct requirements of an installed distribution under the runtime's markers and the
    extras asked for, with the extras each asks for."""
    dist = index.dists[name]
    found: dict[str, frozenset[str]] = {}
    for line in dist.requires:
        requirement = requirement_of(line, dist)
        dependency = canonicalize_name(requirement.name)
        if dependency == name:
            continue
        if requirement.marker is None or any(
            requirement.marker.evaluate({**environment, "extra": extra})
            for extra in {"", *extras}
        ):
            found[dependency] = found.get(dependency, frozenset()) | frozenset(
                canonicalize_name(extra) for extra in requirement.extras)
    return found


def _required(
    named: dict[str, frozenset[str]], index: SiteIndex, environment: dict[str, str]
) -> set[str]:
    """The named distributions another named distribution requires (its dependencies under the
    runtime's markers, with the extras asked for)."""
    return {
        dependency
        for name, extras in named.items()
        for dependency in _dependencies(name, extras, index, environment)
        if dependency in named
    }


def _scripts_of(dist: InstalledDist) -> list[tuple[str, str]]:
    """The commands a tool gives: its console scripts and its gui scripts."""
    return [*dist.console_scripts().items(), *dist.gui_scripts().items()]


def _through_metas(
    tops: dict[str, frozenset[str]], index: SiteIndex, environment: dict[str, str]
) -> tuple[list[str], list[str]]:
    """The tools in place of ``tops`` (name -> extras asked for), and a note per meta-package
    taken through. A top with no script of its own is replaced by its direct requirements,
    recursively, if it is a meta-package (``_has_code``) and else only by those named after it;
    a cycle or ``_META_DEPTH`` levels stop it. A library that gives no tool is noted as one."""
    notes: list[str] = []
    taken_by: dict[tuple[str, frozenset[str]], list[str]] = {}  # each walked once, noted once

    def take(name: str, extras: frozenset[str], path: tuple[str, ...]) -> list[str]:
        if name in path or len(path) > _META_DEPTH or name not in index.dists:
            return []
        if _scripts_of(index.dists[name]):
            return [name]
        if (name, extras) in taken_by:
            return taken_by[name, extras]
        slot = len(notes)
        notes.append("")  # the meta-package's note goes before those of the ones below it
        taken: list[str] = []
        code = _has_code(index.dists[name])
        for dependency, asked in _dependencies(name, extras, index, environment).items():
            if code and not dependency.startswith(f"{name}-"):
                continue
            taken += [t for t in take(dependency, asked, (*path, name)) if t not in taken]
        if taken:
            notes[slot] = _meta_note(name, taken)
        elif code:
            notes[slot] = f"{name} has no console script and is a library, not a tool"
        taken_by[name, extras] = taken
        return taken

    tools: list[str] = []
    for name, extras in tops.items():
        tools += [t for t in take(name, extras, ()) if t not in tools]
    return tools, [note for note in notes if note]


def _has_code(dist: InstalledDist) -> bool:
    """Whether the distribution installs files of its own besides its .dist-info and .data
    (a meta-package installs none) and its scripts outside the site."""
    for line in dist.record:
        top = line.partition(",")[0].partition("/")[0]
        if top and top != ".." and not top.endswith((".dist-info", ".data")):
            return True
    return False


def _meta_note(name: str, taken: list[str]) -> str:
    return f"{name} has no console script of its own; the tools taken through it: {_shown(taken)}"


def _shown(names: list[str]) -> str:
    more = f" and {len(names) - _SHOWN} more" if len(names) > _SHOWN else ""
    return ", ".join(names[:_SHOWN]) + more


def tool_scripts(
    index: SiteIndex, listed: list[Listed], *, project: str, source: str, python_version: str
) -> Tools:
    """The commands of the tools ``listed`` names, after the install: their console and gui
    scripts. A file that lists every installed distribution except the project has the listed
    distributions no other one requires as tools; else every listed distribution is one. A tool
    with no script is replaced by its direct requirements (a meta-package). Two tools with one
    script name, or none with a script, are errors."""
    environment = marker_environment(python_version)
    named = _named(listed, index, environment, source)
    # a freeze: dependencies are listed too (but never the ones pip freeze leaves out)
    complete = set(index.dists) - {project, *_FREEZE_OMITS} <= set(named)
    required = _required(named, index, environment) if complete else set()
    tops = [name for name in named if name not in required]
    tools, meta_notes = _through_metas({n: named[n] for n in tops}, index, environment)
    if required:
        notes = [f"{source} lists all {len(named)} installed distributions, so the tools are "
                 f"the {len(tops)} that no other listed distribution requires: "
                 f"{_shown(tops)}"]  # fmt: skip
    else:
        notes = [f"the tools are the {len(tops)} distributions {source} names: {_shown(tops)}"]
    notes += meta_notes
    scripts: dict[str, str] = {}
    owners: dict[str, str] = {}
    for name in tools:
        for command, target in _scripts_of(index.dists[name]):
            if command in owners:
                raise ClosureError(
                    f"{owners[command]} and {name} both have the script {command!r}; "
                    "one executable cannot have two commands of one name: build them from "
                    "separate directories")
            scripts[command], owners[command] = target, name
    if not scripts:
        raise ClosureError(_no_commands(source, tops, named, complete=bool(required)))
    notes.append("commands: " + "; ".join(
        f"{', '.join(sorted(c for c, o in owners.items() if o == name))} ({name} "
        f"{index.dists[name].version})" for name in tools if name in owners.values()))  # fmt: skip
    requires = tuple(f"{n}[{','.join(sorted(e))}]" if e else n for n, e in named.items())
    return Tools(dict(sorted(scripts.items())), owners, tuple(notes), requires)


def _no_commands(source: str, tools: list[str], named: dict[str, frozenset[str]],
                 *, complete: bool) -> str:  # fmt: skip
    if complete:
        return (f"{source} lists every installed distribution, so only the {len(tools)} that no "
                f"other one requires were taken as tools ({_shown(tools)}), and none has a "
                "console or gui script: the executable would have no command; list the tools alone "
                "to choose them")  # fmt: skip
    return (f"{source} names {_shown(list(named))}, and none has a console or gui script: the "
            "executable would have no command")  # fmt: skip


def with_commands(dist: InstalledDist, tools: Tools) -> InstalledDist:
    """The project's distribution as the tool binary's: requiring the listed
    distributions and holding the tools' console scripts as its own, which ``resolve_entry``
    turns into the entry point."""
    text = "[console_scripts]\n" + "".join(f"{c} = {t}\n" for c, t in tools.scripts.items())
    return dataclasses.replace(dist, entry_points=text, requires=tools.requires)

