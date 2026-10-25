"""A tool binary: the console scripts of the tools a requirements file names (research §3,
candidate 10, owner decision 2026-10-25).

A directory with no Python file and a lock source (requirements.txt, pylock.toml, a requirements
file given with ``--requirements``) is an empty project whose requirements are the tools: the
build installs the lock as for any project and the console scripts of the distributions the
file names become the executable's commands. A requirements file names its distributions
directly (``-r`` includes are followed, constraints name nothing). A file that lists every
installed distribution (``pip freeze``, pylock.toml) names the dependencies too, so there the
tools are the listed distributions no other listed distribution requires. A dependency's
scripts are never commands. ``gui_scripts`` are not commands either: they open windows and need
a display.
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
from .project import LockSource, Project, ProjectError, requirement_lines

_INCLUDE = re.compile(r"(?:-r\s*|--requirement(?:\s+|=))(\S.*)")
_EDITABLE = re.compile(r"(?:-e|--editable)(?:\s+|=)\s*")
_EGG = re.compile(r"[#&]egg=([A-Za-z0-9._-]+)")
_FREEZE_OMITS = ("pip", "setuptools", "wheel", "distribute")  # what pip freeze never lists
_SHOWN = 5  # names in a message


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
    return _requirements(path, set())


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


def _required(
    named: dict[str, frozenset[str]], index: SiteIndex, environment: dict[str, str]
) -> set[str]:
    """The named distributions another named distribution requires (its dependencies under the
    runtime's markers, with the extras asked for)."""
    required: set[str] = set()
    for name, extras in named.items():
        dist = index.dists[name]
        for line in dist.requires:
            requirement = requirement_of(line, dist)
            dependency = canonicalize_name(requirement.name)
            if dependency == name or dependency not in named:
                continue
            if requirement.marker is None or any(
                requirement.marker.evaluate({**environment, "extra": extra})
                for extra in {"", *extras}
            ):
                required.add(dependency)
    return required


def _shown(names: list[str]) -> str:
    more = f" and {len(names) - _SHOWN} more" if len(names) > _SHOWN else ""
    return ", ".join(names[:_SHOWN]) + more


def tool_scripts(
    index: SiteIndex, listed: list[Listed], *, project: str, source: str, python_version: str
) -> Tools:
    """The commands of the tools ``listed`` names, after the install: their console scripts. A
    file that lists every installed distribution except the project has the listed
    distributions no other one requires as tools; else every listed distribution is one. Two
    tools with one script name, or none with a console script, are errors."""
    environment = marker_environment(python_version)
    named = _named(listed, index, environment, source)
    # a freeze: dependencies are listed too (but never the ones pip freeze leaves out)
    complete = set(index.dists) - {project, *_FREEZE_OMITS} <= set(named)
    required = _required(named, index, environment) if complete else set()
    tools = [name for name in named if name not in required]
    if required:
        notes = [f"{source} lists all {len(named)} installed distributions, so the tools are "
                 f"the {len(tools)} that no other listed distribution requires: "
                 f"{_shown(tools)}"]  # fmt: skip
    else:
        notes = [f"the tools are the {len(tools)} distributions {source} names: {_shown(tools)}"]
    scripts: dict[str, str] = {}
    owners: dict[str, str] = {}
    for name in tools:
        dist = index.dists[name]
        for command, target in dist.console_scripts().items():
            if command in owners:
                raise ClosureError(
                    f"{owners[command]} and {name} both have the console script {command!r}; "
                    "one executable cannot have two commands of one name: build them from "
                    "separate directories")
            scripts[command], owners[command] = target, name
        notes += _gui_note(dist)
    if not scripts:
        raise ClosureError(_no_commands(source, tools, named, complete=bool(required)))
    notes.append("commands: " + "; ".join(
        f"{', '.join(sorted(c for c, o in owners.items() if o == name))} ({name} "
        f"{index.dists[name].version})" for name in tools if name in owners.values()))  # fmt: skip
    requires = tuple(f"{n}[{','.join(sorted(e))}]" if e else n for n, e in named.items())
    return Tools(dict(sorted(scripts.items())), owners, tuple(notes), requires)


def _gui_note(dist: InstalledDist) -> list[str]:
    gui = sorted(dist.gui_scripts())
    return [f"{dist.name}: gui_scripts are not commands: {', '.join(gui)}"] if gui else []


def _no_commands(source: str, tools: list[str], named: dict[str, frozenset[str]],
                 *, complete: bool) -> str:  # fmt: skip
    if complete:
        return (f"{source} lists every installed distribution, so only the {len(tools)} that no "
                f"other one requires were taken as tools ({_shown(tools)}), and none has a "
                "console script: the executable would have no command; list the tools alone "
                "to choose them")  # fmt: skip
    return (f"{source} names {_shown(list(named))}, and none has a console script: the "
            "executable would have no command")  # fmt: skip


def with_commands(dist: InstalledDist, tools: Tools) -> InstalledDist:
    """The project's distribution as the tool binary's: requiring the listed
    distributions and holding the tools' console scripts as its own, which ``resolve_entry``
    turns into the entry point."""
    text = "[console_scripts]\n" + "".join(f"{c} = {t}\n" for c, t in tools.scripts.items())
    return dataclasses.replace(dist, entry_points=text, requires=tools.requires)

