"""Installed distributions of a staging site: index, project lookup, dependency walk (§3.6 4-5)."""

from __future__ import annotations

import configparser
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from email.parser import HeaderParser
from pathlib import Path
from typing import Any

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from .entry import installed_candidates

_EXTRAS_SUFFIX = re.compile(r"\s*\[[^\]]*\]\s*$")


class ClosureError(RuntimeError):
    """The staging site does not contain a consistent installation of the project."""


@dataclass(frozen=True, slots=True)
class InstalledDist:
    name: str  # canonical
    version: str
    dist_info: str  # directory name, relative to the site
    site: Path
    requires: tuple[str, ...]
    record: tuple[str, ...]  # RECORD lines
    entry_points: str
    direct_url: dict[str, Any] | None
    provides_extra: tuple[str, ...] = ()  # canonical names

    def console_scripts(self) -> dict[str, str]:
        return self._entries("console_scripts")

    def gui_scripts(self) -> dict[str, str]:
        return self._entries("gui_scripts")

    def _entries(self, section: str) -> dict[str, str]:
        parser = configparser.ConfigParser(delimiters=("=",), interpolation=None)
        parser.optionxform = str  # type: ignore[assignment,method-assign]
        parser.read_string(self.entry_points)
        if not parser.has_section(section):
            return {}
        # "module:attr [extra1, extra2]": the extras only mattered to the installer
        return {name: _EXTRAS_SUFFIX.sub("", value) for name, value in parser[section].items()}


@dataclass(frozen=True, slots=True)
class Walk:
    reachable: tuple[str, ...]
    unreachable: tuple[str, ...]
    warnings: tuple[str, ...]


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _load_dist(site: Path, info: Path) -> InstalledDist | None:
    metadata = _read(info / "METADATA")
    if metadata is None:
        return None
    headers = HeaderParser().parsestr(metadata)
    direct_url = _read(info / "direct_url.json")
    return InstalledDist(
        name=canonicalize_name(headers["Name"]),
        version=headers["Version"],
        dist_info=info.name,
        site=site,
        requires=tuple(headers.get_all("Requires-Dist") or ()),
        record=tuple((_read(info / "RECORD") or "").splitlines()),
        entry_points=_read(info / "entry_points.txt") or "",
        direct_url=json.loads(direct_url) if direct_url else None,
        provides_extra=tuple(canonicalize_name(e) for e in headers.get_all("Provides-Extra") or ()),
    )


@dataclass(frozen=True, slots=True)
class SiteIndex:
    site: Path
    dists: dict[str, InstalledDist]

    @classmethod
    def scan(cls, site: Path) -> SiteIndex:
        dists = {}
        for info in sorted(site.glob("*.dist-info")):
            dist = _load_dist(site, info)
            if dist is not None:
                dists[dist.name] = dist
        return cls(site, dists)

    def check_no_editable(self) -> None:
        """Editable installs point at source trees outside the site and cannot be packaged."""
        for dist in self.dists.values():
            if (dist.direct_url or {}).get("dir_info", {}).get("editable"):
                url = (dist.direct_url or {}).get("url", "?")
                raise ClosureError(
                    f"{dist.name} is installed in editable mode ({url}); editable requirements "
                    "(-e) cannot be packaged, list them as regular requirements"
                )

    def project(self, *, name: str | None, directory: Path) -> InstalledDist:
        """By canonical name, else the dist installed from ``directory`` (PEP 610 direct_url)."""
        if name is not None and canonicalize_name(name) in self.dists:
            return self.dists[canonicalize_name(name)]
        for dist in self.dists.values():
            if (dist.direct_url or {}).get("url") == directory.resolve().as_uri():
                return dist
        raise ClosureError(f"project {name or directory} is not installed in the staging site")

    def walk(self, root: InstalledDist, *, extras: tuple[str, ...], python_version: str) -> Walk:
        """Requires-Dist closure under the runtime's marker environment (consistency check)."""
        environment = {key: str(value) for key, value in default_environment().items()}
        environment["python_full_version"] = python_version
        environment["python_version"] = ".".join(python_version.split(".")[:2])
        seen: dict[str, set[str]] = {}
        warnings: list[str] = []
        pending = [(root, set(extras))]
        while pending:
            dist, active = pending.pop()
            if dist.name in seen and active <= seen[dist.name]:
                continue
            seen.setdefault(dist.name, set()).update(active)
            for line in dist.requires:
                requirement = _requirement(line, dist)
                if requirement.marker is not None and not any(
                    requirement.marker.evaluate({**environment, "extra": extra})
                    for extra in (active or {""})
                ):
                    continue
                dependency = self.dists.get(canonicalize_name(requirement.name))
                if dependency is None:
                    raise ClosureError(
                        f"{dist.name} requires {line!r}, which is not installed in the staging site"
                    )
                if not requirement.specifier.contains(dependency.version, prereleases=True):
                    warnings.append(
                        f"{dependency.name} {dependency.version} does not satisfy {line!r}"
                    )
                pending.append((dependency, set(requirement.extras)))
        reachable = tuple(sorted(seen))
        unreachable = tuple(sorted(set(self.dists) - set(seen)))
        return Walk(reachable, unreachable, tuple(warnings))


def _requirement(line: str, dist: InstalledDist) -> Requirement:
    try:
        return Requirement(line)
    except InvalidRequirement as exc:
        raise ClosureError(f"{dist.name}: invalid Requires-Dist {line!r}: {exc}") from exc


def several_scripts_error(
    name: str, scripts: Iterable[str], candidates: str = ""
) -> ClosureError:
    """No --entry, and the project's console scripts are not exactly one; ``candidates``
    describes the packages and modules that entry inference found but could not choose from."""
    choices = ", ".join(sorted(scripts)) or "none"
    found = f"; found: {candidates}" if candidates else ""
    return ClosureError(
        f"{name} has console scripts: {choices}; choose one with --entry "
        f"(a script name, 'module' or 'module:function'){found}"
    )


def unknown_extras_error(name: str, wanted: Iterable[str], known: Iterable[str]) -> str | None:
    """A message for the extras in ``wanted`` that ``known`` lacks, else None."""
    have = {canonicalize_name(extra) for extra in known}
    unknown = sorted(extra for extra in wanted if canonicalize_name(extra) not in have)
    if not unknown:
        return None
    return (
        f"{name} has no extra {', '.join(map(repr, unknown))} (--extra); its extras: "
        f"{', '.join(sorted(have)) or 'none'}"
    )


def infer_entry(dist: InstalledDist) -> tuple[str, str, str]:
    """(application name, entry point, why) for a project with no console scripts: its only
    package with __main__.py, else its only module with a main guard (run as ``python -m`` /
    as the script, UC5)."""
    found = installed_candidates(dist.site, dist.record)
    if len(found.packages) == 1 and not found.modules:
        name = found.packages[0]
        why = f"package {name} is the only one with __main__.py; run as python -m {name}"
        return name, name, why
    if len(found.modules) == 1 and not found.packages:
        name = found.modules[0]
        return name, name, f"module {name} is the only one with an if __name__ == '__main__' guard"
    raise several_scripts_error(dist.name, (), found.described())


def resolve_entry(
    dist: InstalledDist, entry: str | None, log: Callable[[str], None] = lambda _: None
) -> tuple[str, str]:
    """(application name, entry point) from --entry or the project's console scripts, else the
    inferred package or module (``log`` says which and why)."""
    scripts = dist.console_scripts()
    if entry is None:
        if len(scripts) == 1:
            return next(iter(scripts.items()))
        if scripts:
            raise several_scripts_error(dist.name, scripts)
        name, entrypoint, why = infer_entry(dist)
        log(f"{dist.name} has no console scripts: {why}")
        return name, entrypoint
    if entry in scripts:
        return entry, scripts[entry]
    module, _, attribute = entry.partition(":")
    if not all(part.isidentifier() for part in module.split(".")) or (
        attribute and not all(part.isidentifier() for part in attribute.split("."))
    ):
        raise ClosureError(f"--entry {entry!r} is neither a console script nor module[:function]")
    return (attribute or module).rsplit(".", 1)[-1], entry
