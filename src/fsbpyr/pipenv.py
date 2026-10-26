"""Pipfile.lock as the lock of a build (research UC3, §4.3 foreign locks; D5).

Pipenv's lock is JSON. Its ``default`` section (``develop`` never) is converted, in the staging
work directory and never in the user's tree, into the same hashed pip requirements file as a
Poetry or PDM lock (foreign.py): each entry's pinned version and hashes, its markers evaluated
for the target. A Pipfile.lock is flat and complete, so there are no groups or extras to choose
from. ``_meta.hash`` is the SHA-256 of the Pipfile, which tells a lock that is out of date, and
``_meta.requires`` names the Python the project asks for. The Pipfile's ``[packages]`` name the
tools of a tool binary (tools.py): the lock names the dependencies as well. What cannot be
expressed safely (an unpinned VCS entry, a missing path, an unpinned version) is refused with a
message naming the entry.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from packaging.markers import InvalidMarker, Marker
from packaging.utils import canonicalize_name

from .foreign import (
    GIT,
    LOCAL,
    PATH,
    REGISTRY,
    URL,
    Entry,
    Export,
    ForeignLockError,
    _file_name,
    _line,
    _marker,
    _one_per_name,
    _sha256,
)
from .project import LockSource

LOCK = LockSource.PIPFILE_LOCK.value
PIPFILE = "Pipfile"
_COMMIT = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
_MINOR = re.compile(r"([0-9]+\.[0-9]+)(?:\.[0-9A-Za-z.+-]+)?")
_PYPI_HOSTS = ("pypi.org", "pypi.python.org", "files.pythonhosted.org")
# the sections of a Pipfile that Pipenv keeps out of the hash besides the ones it names itself
_UNHASHED = frozenset({"source", "packages", "dev-packages", "requires", "scripts", "pipenv"})
_VCS_KEYS = ("hg", "svn", "bzr")
_SHOWN = 5  # names in a message


def read(directory: Path) -> dict[str, Any]:
    """The parsed Pipfile.lock beside ``directory``'s project."""
    try:
        data = json.loads((directory / LOCK).read_text())
    except (OSError, ValueError) as exc:
        raise ForeignLockError(f"cannot read {LOCK}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("default"), dict):
        raise ForeignLockError(f"{LOCK} is not a Pipenv lock: it has no `default` section")
    return data


def python_of(lock: Path) -> str | None:
    """The minor version (``3.9``) that ``_meta.requires`` asks for, if the file says one;
    None for an unreadable file (the build reports it) or a requirement of "3" alone."""
    try:
        requires = json.loads(lock.read_text())["_meta"]["requires"]
        text = str(requires.get("python_full_version") or requires.get("python_version") or "")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    match = _MINOR.fullmatch(text)
    return match[1] if match else None


def _sources(data: Mapping[str, Any]) -> dict[str, str]:
    meta = data.get("_meta")
    listed = meta.get("sources") if isinstance(meta, dict) else None
    return {str(s.get("name")): str(s.get("url", "")) for s in listed or []
            if isinstance(s, dict)}  # fmt: skip


def _on_pypi(url: str) -> bool:
    return (urlsplit(url).hostname or "") in _PYPI_HOSTS


def _version(name: str, spec: Mapping[str, Any]) -> str:
    text = str(spec.get("version", ""))
    if not text.startswith("==") or text.startswith("==="):
        raise ForeignLockError(f"{LOCK}: {name} is not pinned with `==` ({text or 'no version'}"
                               "); lock again with `pipenv lock`")  # fmt: skip
    return text[2:].strip()


def _digests(name: str, spec: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(digest for value in spec.get("hashes") or ()
                 if (digest := _sha256(value)))  # fmt: skip


def _entry(name: str, spec: Mapping[str, Any], sources: Mapping[str, str]) -> Entry:
    """One ``default`` entry: a registry pin, a git, file or path dependency."""
    canonical = canonicalize_name(name)
    marker = spec.get("markers") or None
    if (vcs := next((key for key in _VCS_KEYS if key in spec), None)) is not None:
        raise ForeignLockError(f"{LOCK}: {name} comes from {vcs}, which fsbpyr does not "
                               "install from a lock; pass a requirements file with "
                               "--requirements")  # fmt: skip
    if "git" in spec:
        ref = str(spec.get("ref", ""))
        if not _COMMIT.fullmatch(ref):
            raise ForeignLockError(f"{LOCK}: {name} comes from {spec['git']}, but its ref "
                                   f"{ref or 'is missing'}{' is not a commit' * bool(ref)}; "
                                   "a build installs a pinned commit")  # fmt: skip
        return Entry(canonical, "", GIT, str(spec["git"]), ref,
                     str(spec.get("subdirectory", "")), marker=marker)  # fmt: skip
    if "path" in spec:
        return Entry(canonical, "", LOCAL, str(spec["path"]), marker=marker)
    if "file" in spec:
        where = str(spec["file"])
        digests = _digests(name, spec)
        files = {_file_name(where): digests[0]} if len(digests) == 1 else {}
        return Entry(canonical, "", PATH if where.startswith("file:") else URL, where,
                     files=files, marker=marker)  # fmt: skip
    url = sources.get(str(spec.get("index", "")), "")
    return Entry(canonical, _version(name, spec), REGISTRY, "" if _on_pypi(url) else url,
                 marker=marker, extras={e: () for e in map(canonicalize_name,
                                                           spec.get("extras") or ())},
                 digests=_digests(name, spec))  # fmt: skip


def entries(data: Mapping[str, Any]) -> list[Entry]:
    """The packages of the ``default`` section."""
    if not isinstance(data.get("default"), dict):
        raise ForeignLockError(f"{LOCK} is not a Pipenv lock: it has no `default` section")
    sources = _sources(data)
    return [_entry(name, spec, sources) for name, spec in data["default"].items()
            if isinstance(spec, dict)]  # fmt: skip


def _applies(entry: Entry, environment: Mapping[str, str], extras: frozenset[str]) -> bool:
    """Whether the entry's marker holds for the target, with the extras that entries ask of
    their dependencies counting as active: Pipenv marks what an extra needs ``extra == 'x'``."""
    if entry.marker is None:
        return True
    marker = _marker(entry.marker, entry)
    return any(marker.evaluate({**environment, "extra": extra}) for extra in ("", *extras))


def _own_directory(entry: Entry, directory: Path) -> bool:
    """``pipenv install -e .`` locks the project itself, which fsbpyr installs on its own."""
    return entry.kind == LOCAL and (directory / entry.where).resolve() == directory.resolve()


def export(directory: Path, *, environment: Mapping[str, str]) -> Export:
    """The requirements file of the Pipfile.lock in ``directory``, for ``environment`` (the
    target's markers)."""
    found = entries(read(directory))
    extras = frozenset(canonicalize_name(x) for entry in found for x in entry.extras)
    chosen = [entry for entry in found if _applies(entry, environment, extras)
              and not _own_directory(entry, directory)]  # fmt: skip
    lines = [_line(entry, directory, LOCK) for entry in _one_per_name(chosen, LOCK)]
    header = f"# {LOCK} as fsbpyr reads it: the default section, markers evaluated for the target\n"

    return Export(header + "".join(f"{line}\n" for line, _ in lines),
                  bool(lines) and all(hashed for _, hashed in lines), len(lines))  # fmt: skip


def _pipfile_hash(pipfile: Mapping[str, Any]) -> str:
    """Pipenv's hash of a Pipfile (plette's ``Pipfile.get_hash``): the sources, the ``requires``
    and the package tables, as sorted compact JSON; a table of another name counts too."""
    data = {
        "_meta": {"sources": pipfile.get("source", []), "requires": pipfile.get("requires", {})},
        "default": pipfile.get("packages", {}),
        "develop": pipfile.get("dev-packages", {}),
    }
    data.update({k: v for k, v in pipfile.items() if k not in _UNHASHED | {"default", "develop"}})
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
                          ).hexdigest()  # fmt: skip


def _replaces_pyproject(directory: Path) -> str | None:
    try:
        declared = tomllib.loads((directory / "pyproject.toml").read_text()
                                 ).get("project", {}).get("dependencies")
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return None
    if not declared:
        return None
    return (f"{LOCK} is the lock source, with no Pipfile beside it: the build installs the "
            "lock's `default` section, not the dependencies of pyproject.toml (remove a "
            f"leftover {LOCK}, or lock them in another format)")  # fmt: skip


def stale(directory: Path) -> str | None:
    """A message when the lock's ``_meta.hash`` is not the Pipfile's: the dependencies changed
    after locking. None when it matches or there is no hash to compare. Without a Pipfile, a
    message when the pyproject.toml beside the lock declares dependencies the lock replaces."""
    if not (directory / PIPFILE).is_file():
        return _replaces_pyproject(directory)
    try:
        recorded = read(directory)["_meta"]["hash"]["sha256"]
        current = _pipfile_hash(tomllib.loads((directory / PIPFILE).read_text()))
    except (OSError, ForeignLockError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return None  # reading the lock fails the build with its own message
    if recorded == current:
        return None
    return (f"{LOCK} is out of date: its _meta hash is not the Pipfile's, so the dependencies "
            "changed after locking (`pipenv lock` updates it); the build installs the lock as "
            "it is")  # fmt: skip


def other_indexes(directory: Path) -> str | None:
    """A note on the packages that the lock takes from a package index other than PyPI: uv
    installs them from the indexes it is configured with, and the lock's hashes check what it
    finds. The index URL of the lock is not passed on (it may name credentials by variable)."""
    try:
        found = [entry for entry in entries(read(directory)) if entry.where
                 and entry.kind == REGISTRY]  # fmt: skip
    except ForeignLockError:
        return None
    if not found:
        return None
    hosts = sorted({urlsplit(entry.where).hostname or entry.where for entry in found})
    names = ", ".join(entry.name for entry in found[:_SHOWN]) + (
        " ..." if len(found) > _SHOWN else "")  # fmt: skip
    return (f"{LOCK} takes {names} from {', '.join(hosts)}, not PyPI: uv installs them from "
            "the indexes it is configured with (UV_INDEX_URL, UV_EXTRA_INDEX_URL, uv.toml), "
            "not from the lock's URL, and the lock's hashes decide whether it found the same "
            "files")  # fmt: skip


def tool_names(directory: Path) -> list[tuple[str, frozenset[str], Marker | None]]:
    """The packages a Pipfile names under ``[packages]`` with their extras and markers; the
    lock's ``default`` entries when there is no Pipfile (a lock names the dependencies too)."""
    pipfile = directory / PIPFILE
    if not pipfile.is_file():
        return [(entry.name, frozenset(entry.extras),
                 _marker(entry.marker, entry) if entry.marker else None)
                for entry in entries(read(directory))]  # fmt: skip
    try:
        table = tomllib.loads(pipfile.read_text()).get("packages", {})
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ForeignLockError(f"cannot read {PIPFILE}: {exc}") from exc
    found: list[tuple[str, frozenset[str], Marker | None]] = []
    for name, spec in table.items():
        fields = spec if isinstance(spec, dict) else {}
        text = fields.get("markers")
        try:
            marker = Marker(text) if text else None
        except InvalidMarker as exc:
            raise ForeignLockError(f"{PIPFILE}: {name}: invalid marker {text!r}: {exc}") from exc
        found.append((canonicalize_name(name),
                      frozenset(canonicalize_name(x) for x in fields.get("extras", ())), marker))
    return found

