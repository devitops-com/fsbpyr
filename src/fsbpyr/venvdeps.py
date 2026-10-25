"""The dependencies a script's own environment holds (research UC6, rung 3 of the dependency
ladder, owner decision 2026-10-25 reversing D5's "no venv is read" and decision 5.6(ii)).

A directory of scripts or a single script that declares no dependency and imports what nothing
in the executable provides may still run in a venv on its developer's machine: a ``.venv``,
``venv`` or ``env`` inside the directory (with a ``pyvenv.cfg``), else ``$VIRTUAL_ENV``. Its
``*.dist-info`` directories are read as files, never executed: METADATA (name, version,
Requires-Dist), ``top_level.txt`` and RECORD say which distribution provides which import name,
and ``direct_url.json`` says whether a distribution is the user's own tree. Nothing is copied:
the distributions that provide the unresolved imports, and the Requires-Dist closure that is
installed beside them, become ``name==version`` pins, which uv installs fresh for the target. A
PyPI name is never guessed from an import name; an import that no installed distribution lists
stays unresolved.
"""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from email.parser import HeaderParser
from pathlib import Path, PurePosixPath

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .project import ProjectError

VENV_NAMES = (".venv", "venv", "env")  # inside the directory, in this order
_NOT_IMPORTABLE = ("__pycache__",)


@dataclass(frozen=True, slots=True)
class VenvDist:
    name: str  # canonical
    version: str
    requires: tuple[str, ...]  # Requires-Dist
    imports: frozenset[str]  # top-level names it provides
    paths: frozenset[str]  # RECORD's paths below site-packages, posix
    local: str | None  # PEP 610 url of an editable or path install, else None

    def provides(self, parts: list[str]) -> bool:
        """Whether RECORD lists a package or module of the dotted name ``parts``."""
        prefix = "/".join(parts)
        return any(path.startswith((f"{prefix}/", f"{prefix}.")) for path in self.paths)


@dataclass(frozen=True, slots=True)
class Inferred:
    venv: Path
    pins: tuple[str, ...]  # "name==version", sorted


def find_venv(directory: Path, environ: Mapping[str, str] | None = None) -> Path | None:
    """The virtual environment of a project: ``.venv``, ``venv`` or ``env`` in ``directory``,
    else ``$VIRTUAL_ENV``; each needs a ``pyvenv.cfg``."""
    environ = os.environ if environ is None else environ
    candidates = [directory / name for name in VENV_NAMES]
    if active := environ.get("VIRTUAL_ENV"):
        candidates.append(Path(active))
    return next((path for path in candidates if (path / "pyvenv.cfg").is_file()), None)


def _record_paths(text: str) -> frozenset[str]:
    return frozenset(row[0] for row in csv.reader(text.splitlines()) if row and row[0])


def _imports(paths: Iterable[str], top_level: str) -> frozenset[str]:
    """The top-level import names of a distribution: ``top_level.txt`` and the first components
    of RECORD's paths that can be imported."""
    names = {line.strip() for line in top_level.splitlines() if line.strip()}
    for path in paths:
        parts = PurePosixPath(path).parts
        if not parts or ".." in parts or parts[0] in _NOT_IMPORTABLE:
            continue
        if len(parts) > 1:
            names.add(parts[0])
        elif path.endswith((".py", ".so", ".pyd")):
            names.add(parts[0].partition(".")[0])
    return frozenset(name for name in names if name.isidentifier())


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _local(info: Path, paths: Iterable[str]) -> str | None:
    """The PEP 610 url of an editable or path install; ``editable`` for one whose RECORD holds
    an editable finder or ``.pth`` file."""
    try:
        direct = json.loads(_read(info / "direct_url.json") or "{}")
    except json.JSONDecodeError:
        direct = {}
    url = direct.get("url") if isinstance(direct, dict) else None
    info_table = direct.get("dir_info") if isinstance(direct, dict) else None
    if isinstance(url, str) and (url.startswith("file:") or (
            isinstance(info_table, dict) and info_table.get("editable"))):
        return url
    return "editable" if any(p.startswith("__editable__") for p in paths) else None


def _dist(info: Path) -> VenvDist | None:
    headers = HeaderParser().parsestr(_read(info / "METADATA"), headersonly=True)
    name, version = headers.get("Name"), headers.get("Version")
    if not name or not version:
        return None
    try:  # the pin goes into the wheel and the warning: an invalid version is skipped
        Version(version)
    except InvalidVersion:
        return None
    if canonicalize_name(name) != canonicalize_name(info.name.partition("-")[0]):
        return None
    paths = _record_paths(_read(info / "RECORD"))
    local = _local(info, paths)
    imports = _imports(paths, _read(info / "top_level.txt"))
    if local is not None:  # an editable install lists no files: its name is the best there is
        imports |= {canonicalize_name(name).replace("-", "_")}
    return VenvDist(canonicalize_name(name), version, tuple(headers.get_all("Requires-Dist") or ()),
                    imports, paths, local)  # fmt: skip


def read_venv(venv: Path) -> dict[str, VenvDist]:
    """The distributions installed in the venv's site-packages, by canonical name; the files
    are read, no interpreter is started."""
    dists: dict[str, VenvDist] = {}
    for site in sorted(venv.glob("lib*/python3*/site-packages")):
        for info in sorted(site.glob("*.dist-info")):
            if info.is_dir() and (dist := _dist(info)) is not None:
                dists.setdefault(dist.name, dist)
    return dists


def _providers(dists: Mapping[str, VenvDist], module: str) -> list[VenvDist]:
    """The distributions that provide the dotted import ``module``: those listing its top-level
    name, narrowed to those whose RECORD holds the module itself when several (a namespace)."""
    parts = module.split(".")
    named = [dist for dist in dists.values() if parts[0] in dist.imports]
    for length in range(len(parts), 0, -1):  # the longest prefix of the name some RECORD lists
        if narrowed := [dist for dist in named if dist.provides(parts[:length])]:
            return narrowed
    return named


def _closure(
    dists: Mapping[str, VenvDist], wanted: Iterable[VenvDist], environment: Mapping[str, str]
) -> dict[str, VenvDist]:
    """``wanted`` and the installed distributions their Requires-Dist lines need on the target
    (markers evaluated for its environment, extras not followed); a requirement the venv does
    not hold is left to uv."""
    found = {dist.name: dist for dist in wanted}
    pending = list(found.values())
    while pending:
        for line in pending.pop().requires:
            try:
                requirement = Requirement(line)
            except InvalidRequirement:
                continue
            if requirement.marker is not None and not requirement.marker.evaluate(
                {**environment, "extra": ""}
            ):
                continue
            name = canonicalize_name(requirement.name)
            if name in dists and name not in found:
                found[name] = dists[name]
                pending.append(dists[name])
    return found


def infer(venv: Path, modules: Iterable[str], environment: Mapping[str, str]) -> Inferred | None:
    """The pins for the unresolved imports ``modules`` (dotted names) from the venv, or None
    when no installed distribution provides any of them. An editable or path install that is
    needed is an error: it is the user's own tree."""
    dists = read_venv(venv)
    wanted = {dist.name: dist for module in sorted(set(modules))
              for dist in _providers(dists, module)}  # fmt: skip
    if not wanted:
        return None
    needed = _closure(dists, wanted.values(), environment)
    for dist in needed.values():
        if dist.local is not None:
            raise ProjectError(f"{dist.name} {dist.version} in {venv} is an editable or path "
                               f"install ({dist.local}), the tree of your own project: fsbpyr "
                               "copies nothing from a venv; declare it by its path in "
                               "requirements.txt or build that project itself")  # fmt: skip
    return Inferred(venv, tuple(f"{d.name}=={d.version}" for _, d in sorted(needed.items())))
