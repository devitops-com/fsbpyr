"""Poetry and PDM locks as the lock of a build (research UC3, D5).

The lock is read here and converted, in the staging work directory and never in the user's
tree, into a pip requirements file that the existing requirements plan installs (staging.py):
the packages of the main (Poetry) or default (PDM) group, plus the project's extras, with the
markers evaluated for the build's target and every hash the lock records. uv installs it, as it
installs everything else (D26). A Poetry lock that does not say which group a package belongs
to (lock-version 2.0, Poetry 1.5 to 1.8) is walked from the project's own dependencies. What
cannot be expressed safely (a package index, an unpinned VCS entry, a missing path, a version
the target cannot choose between) is refused with a message naming the entry.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from packaging.markers import InvalidMarker, Marker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name

from .project import LockSource, ProjectError

REGISTRY, INDEX, LOCAL, PATH, GIT, URL = "registry", "index", "local", "path", "git", "url"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_DIGEST = re.compile(r"[0-9A-Fa-f]{64}")
_PROJECT_ROOT = "${PROJECT_ROOT}"  # PDM's spelling of the project directory in a path
# Poetry hashes these parts of pyproject.toml into the lock's content-hash (poetry.packages.locker)
_POETRY_LEGACY = ("dependencies", "source", "extras", "dev-dependencies")
_POETRY_KEYS = (*_POETRY_LEGACY, "group")
_POETRY_PROJECT_KEYS = ("requires-python", "dependencies", "optional-dependencies")


class ForeignLockError(ProjectError):
    """A Poetry or PDM lock that fsbpyr cannot turn into a locked build."""


@dataclass(frozen=True, slots=True)
class Edge:
    """A dependency of a locked package (Poetry records them)."""

    name: str  # canonical
    specifier: str = ""
    marker: str | None = None
    optional: bool = False
    extras: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, eq=False)  # compared by identity: entries are set members
class Entry:
    """One ``[[package]]`` of a lock."""

    name: str  # canonical
    version: str
    kind: str = REGISTRY  # registry, index (another package index), local, path, git or url
    where: str = ""  # the path, the git URL or the URL as the lock writes it
    commit: str = ""
    subdirectory: str = ""
    files: Mapping[str, str] = field(default_factory=dict)  # file name -> sha256 (hex)
    groups: frozenset[str] = frozenset()
    marker: str | None = None
    optional: bool = False
    dependencies: tuple[Edge, ...] = ()
    extras: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # extra -> canonical names

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}"


@dataclass(frozen=True, slots=True)
class Export:
    text: str  # a pip requirements file
    hashed: bool  # every line carries a hash: uv can run with --require-hashes
    count: int


def _sha256(value: object) -> str | None:
    """``sha256:abc...`` as the bare digest; None for another algorithm."""
    algorithm, _, digest = str(value).partition(":")
    if algorithm != "sha256":
        return None
    if not _DIGEST.fullmatch(digest):
        raise ForeignLockError(f"a sha256 hash in the lock is not 64 hex digits: {str(value)!r}")
    return digest.lower()


def _name_of(text: str) -> str:
    """The canonical name that starts "appdirs (>=1.4.0)"."""
    match = _NAME.match(text.strip())
    return canonicalize_name(match.group()) if match else ""


def _file_name(url: str) -> str:
    return unquote(urlsplit(url).path.rpartition("/")[2])


def _files(files: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """File name -> sha256 of ``[{file or url, hash}]``."""
    table = {}
    for item in files:
        name = item.get("file") or _file_name(str(item.get("url", "")))
        if name and (digest := _sha256(item.get("hash"))):
            table[str(name)] = digest
    return table


# --- Poetry ---------------------------------------------------------------------------------


def _poetry_edges(table: Mapping[str, Any] | None) -> tuple[Edge, ...]:
    edges = []
    for name, spec in (table or {}).items():
        for item in spec if isinstance(spec, list) else [spec]:
            fields = item if isinstance(item, dict) else {"version": item}
            version = str(fields.get("version") or "")
            edges.append(Edge(
                canonicalize_name(name), "" if version == "*" else version, fields.get("markers"),
                bool(fields.get("optional")),
                tuple(canonicalize_name(extra) for extra in fields.get("extras", ())),
            ))  # fmt: skip
    return tuple(edges)


def _poetry_source(package: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """(kind, where, commit, subdirectory) of the ``[package.source]`` table."""
    source = package.get("source") or {}
    kind = {None: REGISTRY, "directory": LOCAL, "file": PATH, "git": GIT, "url": URL}.get(
        source.get("type"), INDEX)  # fmt: skip
    return (kind, str(source.get("url", "")), str(source.get("resolved_reference", "")),
            str(source.get("subdirectory", "")))  # fmt: skip


def _poetry_marker(package: Mapping[str, Any]) -> str | None:
    """The marker of a package: one string, or (lock-version 2.1) one per group."""
    marker = package.get("markers")
    return marker.get("main") if isinstance(marker, dict) else marker


def _poetry_entries(data: Mapping[str, Any]) -> list[Entry]:
    """The packages of a poetry.lock of any version: lock-version 1.x has ``category`` and keeps
    the hashes in ``[metadata.files]``, 2.0 has neither group nor marker, 2.1 has both."""
    old_files = {canonicalize_name(name): files for name, files in
                 ((data.get("metadata") or {}).get("files") or {}).items()}  # fmt: skip
    entries = []
    for package in data.get("package", []):
        name = canonicalize_name(str(package.get("name", "")))
        files = package["files"] if "files" in package else old_files.get(name, [])
        groups = package.get("groups") or [package["category"]] if "category" in package else (
            package.get("groups", []))  # fmt: skip
        kind, where, commit, subdirectory = _poetry_source(package)
        entries.append(Entry(
            name, str(package.get("version", "")), kind, where, commit, subdirectory,
            _files(files), frozenset(groups), _poetry_marker(package),
            bool(package.get("optional")), _poetry_edges(package.get("dependencies")),
            {canonicalize_name(extra): tuple(_name_of(item) for item in items)
             for extra, items in (package.get("extras") or {}).items()},
        ))  # fmt: skip
    return entries


def _poetry_roots(
    pyproject: Mapping[str, Any], environment: Mapping[str, str]
) -> list[tuple[str, tuple[str, ...]]]:
    """The names (and extras) the project depends on in the main group for the target, for a lock
    that does not say it: [tool.poetry.dependencies] and [project.dependencies]."""
    roots: list[tuple[str, tuple[str, ...]]] = []
    poetry = pyproject.get("tool", {}).get("poetry", {})
    for name, spec in (poetry.get("dependencies") or {}).items():
        for item in spec if isinstance(spec, list) else [spec]:
            fields = item if isinstance(item, dict) else {}
            if name != "python" and not fields.get("optional") and _root_holds(
                    name, fields, environment):  # fmt: skip
                roots.append((canonicalize_name(name),
                              tuple(canonicalize_name(x) for x in fields.get("extras", ()))))
    for line in pyproject.get("project", {}).get("dependencies", []):
        try:
            requirement = Requirement(line)
        except InvalidRequirement as exc:
            raise ForeignLockError(f"pyproject.toml: invalid dependency {line!r}: {exc}") from exc
        if requirement.marker is None or requirement.marker.evaluate({**environment, "extra": ""}):
            roots.append((canonicalize_name(requirement.name),
                          tuple(canonicalize_name(x) for x in requirement.extras)))  # fmt: skip
    return roots


def _root_holds(name: str, fields: Mapping[str, Any], environment: Mapping[str, str]) -> bool:
    """Whether a dependency of the project applies to the target: its ``markers``, and its
    ``python`` constraint when that is a PEP 440 specifier (Poetry's ``^`` and ``~`` are not)."""
    if marker := fields.get("markers"):
        try:
            if not Marker(marker).evaluate({**environment, "extra": ""}):
                return False
        except InvalidMarker as exc:
            raise ForeignLockError(f"pyproject.toml: {name}: invalid marker {marker!r}: "
                                   f"{exc}") from exc  # fmt: skip
    if python := fields.get("python"):
        try:
            return SpecifierSet(python).contains(environment["python_full_version"],
                                                 prereleases=True)  # fmt: skip
        except InvalidSpecifier as exc:
            raise ForeignLockError(f"pyproject.toml: {name} has the python constraint "
                                   f"{python!r}, which fsbpyr cannot evaluate; pass a "
                                   "requirements file with --requirements") from exc
    return True


def _lock_extras(data: Mapping[str, Any], wanted: Iterable[str]) -> list[str]:
    """The package names the lock's ``[extras]`` table lists for the extras asked for."""
    table = {canonicalize_name(extra): items for extra, items in (data.get("extras") or {}).items()}
    names = []
    for extra in wanted:
        if canonicalize_name(extra) not in table:
            raise ForeignLockError(f"poetry.lock has no extra {extra!r} (it has: "
                                   f"{', '.join(sorted(table)) or 'none'}); is the lock stale?")
        names += [_name_of(item) for item in table[canonicalize_name(extra)]]
    return names


def _poetry_selection(
    entries: list[Entry], data: Mapping[str, Any], directory: Path,
    environment: Mapping[str, str], extras: Iterable[str],
) -> list[Entry]:  # fmt: skip
    wanted = _lock_extras(data, extras)
    by_name = _by_name(entries)
    roots: list[tuple[Entry, tuple[str, ...]]] = []
    if any(entry.groups for entry in entries):
        roots += [(entry, ()) for entry in entries
                  if "main" in entry.groups and not entry.optional
                  and _applies(entry, environment)]  # fmt: skip
        named: list[tuple[str, tuple[str, ...]]] = [(name, ()) for name in wanted]
    else:
        named = _poetry_roots(_pyproject(directory), environment) + [(n, ()) for n in wanted]
    for name, root_extras in named:
        found = [entry for entry in by_name.get(name, ()) if _applies(entry, environment)]
        if not found:
            raise ForeignLockError(f"poetry.lock does not list {name}, which the project "
                                   "depends on; is the lock stale?")
        roots += [(entry, root_extras) for entry in found]
    return _closure(by_name, roots, environment)


def _poetry_hash(data: Mapping[str, Any]) -> str:
    """Poetry's content-hash of a pyproject.toml: the parts that decide the lock, as JSON."""
    project = data.get("project", {})
    poetry = data.get("tool", {}).get("poetry", {})
    relevant_project = {key: project[key] for key in _POETRY_PROJECT_KEYS if
                        project.get(key) is not None}  # fmt: skip
    relevant: dict[str, Any] = {}
    for key in _POETRY_KEYS:
        value = poetry.get(key)
        if value is None and (key not in _POETRY_LEGACY or relevant_project):
            continue
        relevant[key] = value
    content = {"project": relevant_project, "tool": {"poetry": relevant}} if (
        relevant_project) else relevant  # fmt: skip
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


# --- PDM ------------------------------------------------------------------------------------


def _pdm_entries(data: Mapping[str, Any]) -> list[Entry]:
    """The packages of a pdm.lock. An entry for a package with extras (``extras = [...]``) adds
    nothing: the packages the extras need are entries of their own. Lock 4.0 to 4.2 keep the
    hashes in ``[metadata.files]``, by "name version"."""
    old_files = (data.get("metadata") or {}).get("files") or {}
    entries = []
    for package in data.get("package", []):
        if package.get("extras"):
            continue
        name, version = str(package.get("name", "")), str(package.get("version", ""))
        files = package["files"] if "files" in package else old_files.get(f"{name} {version}", [])
        kind, where = (
            (LOCAL, str(package["path"])) if "path" in package
            else (GIT, str(package["git"])) if "git" in package
            else (URL, str(package["url"])) if "url" in package
            else (REGISTRY, ""))  # fmt: skip
        entries.append(Entry(
            canonicalize_name(name), version, kind, where, str(package.get("revision", "")),
            str(package.get("subdirectory", "")), _files(files),
            frozenset(canonicalize_name(g) for g in package.get("groups", ())),
            package.get("marker"),
        ))  # fmt: skip
    return entries


def _pdm_selection(
    entries: list[Entry], data: Mapping[str, Any], environment: Mapping[str, str],
    extras: Iterable[str],
) -> list[Entry]:  # fmt: skip
    """The packages of the default group and of the groups named like the extras asked for."""
    locked = {canonicalize_name(g) for g in (data.get("metadata") or {}).get("groups", [])}
    wanted = {"default", *(canonicalize_name(extra) for extra in extras)}
    if missing := sorted(wanted - locked - {"default"}):
        raise ForeignLockError(f"pdm.lock has no group for the extra {missing[0]!r} (it has: "
                               f"{', '.join(sorted(locked)) or 'none'}); is the lock stale?")
    if not any(entry.groups for entry in entries):
        if locked - {"default"}:
            raise ForeignLockError(
                f"pdm.lock holds the groups {', '.join(sorted(locked))} without saying which "
                "package belongs to which; lock again with `pdm lock --strategy "
                "inherit_metadata`, or pass a requirements file with --requirements")  # fmt: skip
        return [entry for entry in entries if _applies(entry, environment)]
    return [entry for entry in entries if entry.groups & wanted and _applies(entry, environment)]


def _pdm_hashes(data: Mapping[str, Any]) -> set[str]:
    """The content hashes PDM may have written for this pyproject.toml: the formula gained
    ``dependency-groups`` in some versions, so a lock stands if either matches."""
    tool = data.get("tool", {}).get("pdm", {})
    metadata = data.get("project", {})
    content = {
        "sources": tool.get("source", []),
        "dependencies": metadata.get("dependencies", []),
        "dev-dependencies": tool.get("dev-dependencies", {}),
        "optional-dependencies": metadata.get("optional-dependencies", {}),
        "requires-python": metadata.get("requires-python", ""),
        "resolution": tool.get("resolution", {}),
    }
    candidates = [content]
    if "dependency-groups" in data:
        candidates.append({**content, "dependency-groups": data["dependency-groups"]})
    return {hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest() for c in candidates}


# --- shared ---------------------------------------------------------------------------------


def _pyproject(directory: Path) -> dict[str, Any]:
    try:
        return tomllib.loads((directory / "pyproject.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ForeignLockError(f"cannot read {directory / 'pyproject.toml'}: {exc}") from exc


def _by_name(entries: Iterable[Entry]) -> dict[str, list[Entry]]:
    table: dict[str, list[Entry]] = {}
    for entry in entries:
        table.setdefault(entry.name, []).append(entry)
    return table


def _marker(text: str, entry: Entry) -> Marker:
    try:
        return Marker(text)
    except InvalidMarker as exc:
        raise ForeignLockError(f"{entry.label}: invalid marker {text!r}: {exc}") from exc


def _applies(entry: Entry, environment: Mapping[str, str]) -> bool:
    """Whether the package's own marker holds for the target."""
    return entry.marker is None or _marker(entry.marker, entry).evaluate({**environment,
                                                                          "extra": ""})


def _satisfies(entry: Entry, edge: Edge) -> bool:
    try:
        return not edge.specifier or SpecifierSet(edge.specifier).contains(
            entry.version, prereleases=True)
    except InvalidSpecifier:
        return True  # an old lock's spelling of a constraint: the marker and name decide


def _edge_active(
    entry: Entry, edge: Edge, active: set[str], environment: Mapping[str, str]
) -> bool:  # fmt: skip
    if edge.optional and edge.name not in {n for x in active for n in entry.extras.get(x, ())}:
        return False
    if edge.marker is None:
        return True
    marker = _marker(edge.marker, entry)
    return any(marker.evaluate({**environment, "extra": extra}) for extra in (active or {""}))


def _closure(
    by_name: Mapping[str, list[Entry]], roots: list[tuple[Entry, tuple[str, ...]]],
    environment: Mapping[str, str],
) -> list[Entry]:  # fmt: skip
    """The roots and what they need for the target, through the dependencies the lock records
    (an optional one only when an extra that lists it is active)."""
    active: dict[Entry, set[str]] = {}
    pending = [(entry, frozenset(extras)) for entry, extras in roots]
    while pending:
        entry, extras = pending.pop()
        if entry in active and extras <= active[entry]:
            continue
        on = active.setdefault(entry, set())
        on |= extras
        for edge in entry.dependencies:
            if not _edge_active(entry, edge, on, environment):
                continue
            pending += [(found, frozenset(edge.extras)) for found in by_name.get(edge.name, ())
                        if _applies(found, environment) and _satisfies(found, edge)]  # fmt: skip
    return list(active)


def _local_url(where: str, directory: Path, entry: Entry, lock: str) -> str:
    """The file URL of a path dependency: a lock writes the path relative to the project."""
    text = where.replace(_PROJECT_ROOT, str(directory))
    if text.startswith("file:"):  # `file:///${PROJECT_ROOT}/x` is file:////dir/x once filled in
        text = "/" + unquote(urlsplit(text).path).lstrip("/")
    path = Path(text)
    path = (path if path.is_absolute() else directory / path).resolve()
    if not path.exists():
        raise ForeignLockError(f"{lock}: {entry.label} is the path {where}, which does not "
                               f"exist ({path})")  # fmt: skip
    return path.as_uri()


def _hash_lines(digests: Iterable[str]) -> str:
    return "".join(f" \\\n    --hash=sha256:{digest}" for digest in sorted(set(digests)))


def _line(entry: Entry, directory: Path, lock: str) -> tuple[str, bool]:
    """The requirements line of an entry, and whether it carries a hash. A pin is exact: a
    registry entry by version and every hash the lock lists, VCS by its commit, a path as a
    regular (not editable) install, as UC2 does."""
    if not _NAME.fullmatch(entry.name) or any(re.search(r"\s", word) for word in (
            entry.name, entry.version, entry.where, entry.commit, entry.subdirectory)):
        raise ForeignLockError(f"{lock}: {entry.name!r} {entry.version!r} has a name, version or "
                               "source with a space or a line break, which a requirements line "
                               "cannot hold")
    if entry.kind == INDEX:
        raise ForeignLockError(
            f"{lock}: {entry.label} comes from the package index {entry.where}, which fsbpyr "
            "does not take from a lock (its credentials are not in the file); pass a "
            "requirements file with --requirements")  # fmt: skip
    if entry.kind == REGISTRY:
        return f"{entry.name}=={entry.version}{_hash_lines(entry.files.values())}", bool(
            entry.files)  # fmt: skip
    if entry.kind in (LOCAL, PATH):
        return f"{entry.name} @ {_local_url(entry.where, directory, entry, lock)}", False
    if entry.kind == GIT:
        if not entry.commit:
            raise ForeignLockError(f"{lock}: {entry.label} comes from {entry.where}, but the "
                                   "lock does not pin a commit")  # fmt: skip
        url = entry.where if entry.where.startswith("git+") else f"git+{entry.where}"
        sub = f"#subdirectory={entry.subdirectory}" if entry.subdirectory else ""
        return f"{entry.name} @ {url}@{entry.commit}{sub}", False
    digest = next((d for n, d in entry.files.items() if n == _file_name(entry.where)), None)
    fragment = f"#sha256={digest}" if digest else ""
    return f"{entry.name} @ {entry.where}{fragment}", digest is not None


def _one_per_name(chosen: Iterable[Entry], lock: str) -> list[Entry]:
    """The chosen entries, one per package: a version the lock keeps for another target is
    already out by its marker, and two that remain cannot be told apart here."""
    unique = []
    for name, found in sorted(_by_name(chosen).items()):
        if len({entry.version for entry in found}) > 1:
            raise ForeignLockError(
                f"{lock}: {name} is locked at {', '.join(sorted({e.version for e in found}))} "
                "for this target; pass a requirements file with --requirements")  # fmt: skip
        unique.append(found[0])
    return unique


def read_entries(source: LockSource, data: Mapping[str, Any]) -> list[Entry]:
    return _poetry_entries(data) if source is LockSource.POETRY_LOCK else _pdm_entries(data)


def export(
    source: LockSource, directory: Path, *, environment: Mapping[str, str],
    extras: Iterable[str] = (),
) -> Export:  # fmt: skip
    """The requirements file of the lock beside the project's pyproject.toml, for ``environment``
    (the target's markers) and the project's ``extras``."""
    lock = source.value
    try:
        data = tomllib.loads((directory / lock).read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ForeignLockError(f"cannot read {lock}: {exc}") from exc
    entries = read_entries(source, data)
    extras = tuple(extras)
    chosen = (_poetry_selection(entries, data, directory, environment, extras)
              if source is LockSource.POETRY_LOCK
              else _pdm_selection(entries, data, environment, extras))  # fmt: skip
    lines = [_line(entry, directory, lock) for entry in _one_per_name(chosen, lock)]
    header = f"# {lock} as fsbpyr reads it: the main packages, markers evaluated for the target\n"
    return Export(header + "".join(f"{line}\n" for line, _ in lines),
                  bool(lines) and all(hashed for _, hashed in lines), len(lines))  # fmt: skip


def stale(source: LockSource, directory: Path) -> str | None:
    """A message when the lock's content hash is not the pyproject.toml's: the dependencies
    changed after locking. None when it matches, or when there is nothing to compare."""
    lock = source.value
    try:
        data = tomllib.loads((directory / lock).read_text())
        pyproject = tomllib.loads((directory / "pyproject.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None  # reading the lock fails the build with its own message
    metadata = data.get("metadata") or {}
    if source is LockSource.POETRY_LOCK:
        recorded, current = metadata.get("content-hash"), {_poetry_hash(pyproject)}
        update = "poetry lock"
    else:
        recorded = str(metadata.get("content_hash", "")).partition(":")[2] or None
        current, update = _pdm_hashes(pyproject), "pdm lock"
    if recorded is None or recorded in current:
        return None
    return (f"{lock} is out of date: its content hash is not pyproject.toml's, so the "
            f"dependencies changed after locking (`{update}` updates it); the build installs "
            "the lock as it is")  # fmt: skip
