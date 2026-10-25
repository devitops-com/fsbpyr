"""What an executable was built from, for build_info (REP-3): the lock and each distribution.

Everything here is a pure function of the lock file's bytes and the staged site, so identical
inputs give identical records, whatever directory the project is in. A distribution is traced
to the artifact the lock names for it: with uv.lock or pylock.toml, the wheel whose tags the
installed WHEEL file shares; an installed wheel that shares none was built on the build host
from the package's sdist, so its bytes depend on that host's build backend and environment.
"""

import hashlib
import os
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from packaging.tags import Tag, parse_tag
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename

from .closure import InstalledDist
from .project import LockSource

SCHEMA = 1  # of build_info (docs/format.md)
BUILT_FROM_SDIST = "built-from-sdist"
LOCAL = "local"
_UV_SOURCES = {"registry": "registry", "git": "git", "url": "url", "path": "path",
               "editable": LOCAL, "directory": LOCAL, "virtual": LOCAL}  # fmt: skip


class LockError(RuntimeError):
    """The lock file cannot be read."""


@dataclass(frozen=True, slots=True)
class LockedPackage:
    kind: str  # registry, git, url, path or local
    version: str | None = None
    wheels: Mapping[str, str] = field(default_factory=dict)  # wheel file name -> sha256 (hex)
    sdist: str | None = None  # sha256 (hex)


def _hex(value: object) -> str | None:
    """``sha256:abc...`` (uv.lock) or ``abc...`` as the bare hex digest."""
    if not isinstance(value, str):
        return None
    algorithm, colon, digest = value.rpartition(":")
    return digest if digest and (not colon or algorithm == "sha256") else None


def _file_name(url: object) -> str:
    return unquote(urlsplit(str(url)).path.rpartition("/")[2])


def _uv_package(package: Mapping[str, Any]) -> LockedPackage:
    source = package.get("source") or {}
    kind = next((_UV_SOURCES[key] for key in source if key in _UV_SOURCES), "registry")
    wheels = {_file_name(w.get("url")): h for w in package.get("wheels", [])
              if (h := _hex(w.get("hash")))}  # fmt: skip
    sdist = package.get("sdist") or {}
    return LockedPackage(kind, package.get("version"), wheels, _hex(sdist.get("hash")))


def _pylock_file(entry: Mapping[str, Any]) -> tuple[str, str | None]:
    name = entry.get("name") or _file_name(entry.get("url") or entry.get("path"))
    return str(name), _hex((entry.get("hashes") or {}).get("sha256"))


def _pylock_package(package: Mapping[str, Any]) -> LockedPackage:
    kind = ("git" if "vcs" in package else LOCAL if "directory" in package
            else "url" if "archive" in package else "registry")  # fmt: skip
    wheels = dict(_pylock_file(w) for w in package.get("wheels", []))
    sdist = _pylock_file(package["sdist"])[1] if "sdist" in package else None
    return LockedPackage(kind, package.get("version"), {n: h for n, h in wheels.items() if h},
                         sdist)  # fmt: skip


def read_lock(source: LockSource, text: str) -> dict[str, list[LockedPackage]]:
    """The packages of a uv.lock or pylock.toml, by canonical name; empty for other sources."""
    if source not in (LockSource.UV_LOCK, LockSource.SCRIPT_LOCK, LockSource.PYLOCK):
        return {}
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise LockError(f"cannot read {source.value}: {exc}") from exc
    uv = source is not LockSource.PYLOCK
    packages: dict[str, list[LockedPackage]] = {}
    for package in data.get("package" if uv else "packages", []):
        parsed = _uv_package(package) if uv else _pylock_package(package)
        packages.setdefault(canonicalize_name(package.get("name", "")), []).append(parsed)
    return packages


def _hashed(source: LockSource, text: str, packages: Mapping[str, list[LockedPackage]]) -> bool:
    """Whether the lock pins artifact hashes: uv.lock always does; the others when they list
    some."""
    if source in (LockSource.UV_LOCK, LockSource.SCRIPT_LOCK):
        return True
    if source is LockSource.REQUIREMENTS:
        return "--hash=" in text
    return any(p.wheels or p.sdist for entries in packages.values() for p in entries)


def lock_record(
    directory: Path, source: LockSource, path: Path | None
) -> tuple[dict[str, object], dict[str, list[LockedPackage]]]:
    """build_info's ``lock``, and the lock's packages. ``path`` is relative to ``directory``,
    the project's."""
    if path is None:
        return {"kind": source.value, "path": None, "sha256": None, "hashed": False}, {}
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise LockError(f"cannot read {path}: {exc.strerror or exc}") from exc
    text = data.decode("utf-8", errors="replace")
    packages = read_lock(source, text)
    record: dict[str, object] = {
        "kind": source.value,
        "path": Path(os.path.relpath(path, directory)).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "hashed": _hashed(source, text, packages),
    }
    return record, packages


def _wheel_file(dist: InstalledDist) -> list[str]:
    wheel = dist.site / dist.dist_info / "WHEEL"
    return wheel.read_text(errors="replace").splitlines() if wheel.is_file() else []


def installed_tags(dist: InstalledDist) -> frozenset[Tag]:
    tags: set[Tag] = set()
    for line in _wheel_file(dist):
        key, _, value = line.partition(":")
        if key == "Tag" and value.strip():
            tags |= parse_tag(value.strip())
    return frozenset(tags)


def _backend(dist: InstalledDist) -> str | None:
    for line in _wheel_file(dist):
        key, _, value = line.partition(":")
        if key == "Generator":
            return value.strip()
    return None


def _wheel_tags(name: str) -> frozenset[Tag]:
    try:
        return parse_wheel_filename(name)[3]
    except InvalidWheelFilename:
        return frozenset()


def _direct_source(dist: InstalledDist) -> str:
    """The source of a distribution the lock does not list (requirements, unlocked), by PEP 610."""
    direct = dist.direct_url or {}
    if "vcs_info" in direct:
        return "git"
    if "archive_info" in direct:
        return "url"
    return "path" if "dir_info" in direct else "registry"


def _locked(
    dist: InstalledDist, packages: Mapping[str, list[LockedPackage]]
) -> LockedPackage | None:
    entries = packages.get(dist.name, [])
    return next((p for p in entries if p.version == dist.version), None) or (
        entries[0] if len(entries) == 1 and entries[0].version is None else None)  # fmt: skip


def dist_record(
    dist: InstalledDist, packages: Mapping[str, list[LockedPackage]], *, project: str
) -> dict[str, object]:
    """One entry of build_info's ``dists``: name, version, source and the hash of the
    artifact the lock names for it (none for ``local`` ones, which the lock has no hash for).
    ``project`` is the app's own distribution, built by `uv build` at staging."""
    record: dict[str, object] = {"name": dist.name, "version": dist.version}
    locked = _locked(dist, packages)
    if dist.name == project or (locked is not None and locked.kind == LOCAL):
        return {**record, "source": LOCAL}
    if locked is None:
        return {**record, "source": _direct_source(dist)}
    tags = installed_tags(dist)
    for name, digest in sorted(locked.wheels.items()):
        if tags & _wheel_tags(name):
            return {**record, "source": locked.kind, "artifact_sha256": digest}
    # none of the lock's wheels, or only an sdist: built here
    if locked.wheels or (locked.sdist and locked.kind != "git"):
        built = {**record, "source": BUILT_FROM_SDIST}
        if locked.sdist:
            built["artifact_sha256"] = locked.sdist
        if backend := _backend(dist):
            built["backend"] = backend
        return built
    return {**record, "source": locked.kind}


def dist_records(
    dists: Iterable[InstalledDist], packages: Mapping[str, list[LockedPackage]], *, project: str
) -> list[dict[str, object]]:
    records = [dist_record(dist, packages, project=project) for dist in dists]
    return sorted(records, key=lambda r: (str(r["name"]), str(r["version"])))


def built_here(records: Iterable[Mapping[str, object]]) -> list[str]:
    """Names of the distributions built from their sdists on this host."""
    return sorted(str(r["name"]) for r in records if r["source"] == BUILT_FROM_SDIST)
