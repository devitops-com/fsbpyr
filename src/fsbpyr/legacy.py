"""Legacy scripts of the project's wheel (research UC5 (d), owner decision 2026-10-25).

A wheel keeps the files of ``setup.py``'s ``scripts=`` under ``<dist>.data/scripts/``, and an
installer rewrites the first line ``#!python`` of each to its interpreter, so only the wheel
tells which are Python (a shell script is not packaged). Each one becomes a command that runs
as ``python script`` runs it: its source is stored below the scripts root of loose.py under a
module name, and the boot runs it as ``__main__`` with that directory first on ``sys.path``.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import replace
from pathlib import Path, PurePosixPath

from .closure import ClosureError, InstalledDist
from .loose import ROOT, script_stem

_SCRIPT = re.compile(r"[^/]+\.data/scripts/([^/]+)")
_SHEBANG = re.compile(rb"#!python(?:[ \t\r\n]|$)")  # the wheel spec's rule; not #!pythonw


def wheel_scripts(wheel: Path) -> dict[str, bytes]:
    """The files of the wheel's ``.data/scripts/`` that start with ``#!python``: name ->
    content, sorted."""
    found = {}
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            match = _SCRIPT.fullmatch(info.filename)
            if match is None:
                continue
            data = archive.read(info)
            if _SHEBANG.match(data):
                found[match.group(1)] = data
    return dict(sorted(found.items()))


def attach(dist: InstalledDist, wheel: Path, warnings: list[str]) -> InstalledDist:
    """``dist`` with the legacy scripts of ``wheel`` as commands: each source is written below
    the scripts root of its site and listed in its RECORD. A name that is also a console script,
    or two names that give one module, fail the build; a script that does not compile (Python 2)
    is skipped with a warning."""
    scripts = wheel_scripts(wheel)
    if not scripts:
        return dist
    taken = dist.commands()
    modules: dict[str, str] = {}  # payload path -> script name
    legacy: dict[str, str] = {}
    for name, data in scripts.items():
        try:
            compile(data, name, "exec")
        except (SyntaxError, ValueError) as error:
            warnings.append(f"{dist.name}: legacy script {name} is not packaged: it does not "
                            f"compile ({error})")  # fmt: skip
            continue
        if name in taken:
            raise ClosureError(
                f"{dist.name}: {name!r} is both a console script ({taken[name]}) and a legacy "
                f"script (.data/scripts/{name} of {wheel.name}); rename one"
            )
        stem = script_stem(name, python=name.endswith(".py"))
        payload = f"{ROOT}/{stem}.py"
        if payload in modules:
            raise ClosureError(f"{dist.name}: the legacy scripts {modules[payload]!r} and "
                               f"{name!r} are both {payload} in the executable (a name that is "
                               "no Python name is stored with '_' for each character that is "
                               "not one); rename one")  # fmt: skip
        modules[payload] = name
        legacy[name] = f"{ROOT}.{stem}"
        target = dist.site / payload
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
    if not legacy:
        return dist
    record = (*dist.record, *(f"{PurePosixPath(path)},," for path in modules))
    return replace(dist, record=record, legacy=legacy)
