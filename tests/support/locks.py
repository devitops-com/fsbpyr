"""What a uv.lock installs on Linux for one Python and one machine, and which of those packages
have no wheel there for fsbpyr's glibc baseline (manylinux_2_34): staging builds such a package
from its sdist (staging.py), which a test must not depend on."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from packaging.markers import Marker
from packaging.utils import canonicalize_name, parse_wheel_filename

from fsbpyr.runtime.pins import PINS

from . import baseline

# manylinux_2_34, as the targets' uv_platform (targets.py); manylinux_2_28 in the EL8 test mode
BASELINE = baseline.as_tuple(baseline.BASELINE)
MACHINES = ("x86_64", "aarch64")
_MANYLINUX = re.compile(r"manylinux_(\d+)_(\d+)_(\w+)$")
_LEGACY = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}
_INTERPRETER = re.compile(r"(cp|py)3(\d*)")


@dataclass(frozen=True)
class Package:
    name: str
    version: str | None
    dependencies: tuple[dict[str, Any], ...]
    extras: dict[str, tuple[dict[str, Any], ...]]
    wheels: tuple[str, ...]  # file names
    local: bool  # the project itself (or another workspace path), not from an index


def _read(lock: Path) -> list[Package]:
    with lock.open("rb") as file:
        data = tomllib.load(file)
    return [
        Package(
            canonicalize_name(entry["name"]), entry.get("version"),
            tuple(entry.get("dependencies", ())),
            {extra: tuple(deps) for extra, deps in entry.get("optional-dependencies", {}).items()},
            tuple(unquote(wheel["url"].rpartition("/")[2]) for wheel in entry.get("wheels", ())),
            any(key in entry.get("source", {}) for key in ("editable", "directory", "virtual")),
        )
        for entry in data.get("package", [])
    ]  # fmt: skip


def _environment(python: str, machine: str) -> dict[str, str]:
    full = next(pin.version for pin in PINS if pin.python == python)
    return {
        "python_version": python, "python_full_version": full, "implementation_name": "cpython",
        "implementation_version": full, "platform_python_implementation": "CPython",
        "sys_platform": "linux", "platform_system": "Linux", "os_name": "posix",
        "platform_machine": machine, "platform_release": "", "platform_version": "", "extra": "",
    }  # fmt: skip


def installed(lock: Path, python: str, machine: str) -> list[Package]:
    """The packages ``uv sync --no-dev`` installs from ``lock`` (its projects, no extras of
    theirs) on CPython ``python`` (X.Y, at its pinned patch release) for Linux on ``machine``."""
    packages = _read(lock)
    by_name: dict[str, list[Package]] = {}
    for package in packages:
        by_name.setdefault(package.name, []).append(package)
    environment = _environment(python, machine)
    found: dict[tuple[str, str | None], Package] = {}
    done: set[tuple[str, str | None, str | None]] = set()  # (name, version, extra or None)
    pending: list[tuple[Package, str | None]] = [(p, None) for p in packages if p.local]
    while pending:
        package, extra = pending.pop()
        if (package.name, package.version, extra) in done:
            continue
        done.add((package.name, package.version, extra))
        found[package.name, package.version] = package
        for dependency in package.dependencies if extra is None else package.extras[extra]:
            marker = dependency.get("marker")
            if marker and not Marker(marker).evaluate(environment):
                continue
            candidates = by_name[canonicalize_name(dependency["name"])]
            if "version" in dependency:
                candidates = [c for c in candidates if c.version == dependency["version"]]
            pending += [(candidates[0], None)]
            pending += [(candidates[0], name) for name in dependency.get("extra", ())]
    return [package for package in found.values() if not package.local]


def _glibc(platform: str, machine: str) -> tuple[int, int] | None:
    """The glibc a manylinux platform tag for ``machine`` requires; None for another tag."""
    if (match := _MANYLINUX.match(platform)) and match.group(3) == machine:
        return int(match.group(1)), int(match.group(2))
    legacy, _, arch = platform.partition("_")  # manylinux2014_x86_64
    return _LEGACY.get(legacy) if arch == machine else None


def usable(wheel: str, python: str, machine: str) -> bool:
    """Whether uv may install the wheel file ``wheel`` on CPython ``python`` (X.Y) for
    ``machine``'s manylinux_2_34."""
    minor = int(python.split(".")[1])
    for tag in parse_wheel_filename(wheel)[3]:
        if tag.platform != "any" and ((glibc := _glibc(tag.platform, machine)) is None
                                      or glibc > BASELINE):  # fmt: skip
            continue
        if (interpreter := _INTERPRETER.fullmatch(tag.interpreter)) is None:
            continue
        kind, version = interpreter.group(1), int(interpreter.group(2) or 0)
        if tag.abi == "abi3" and kind == "cp" and version <= minor:
            return True
        if tag.abi == f"cp3{minor}" and version == minor:
            return True
        if tag.abi == "none" and (kind == "py" or version == minor):
            return True
    return False


def without_wheel(lock: Path, python: str, machine: str) -> list[str]:
    """The packages ``lock`` installs on ``python`` and ``machine`` that have no usable wheel
    there (only an sdist, or wheels for other Pythons, machines or a newer glibc), by name."""
    packages = installed(lock, python, machine)
    return sorted(p.name for p in packages if not any(usable(w, python, machine) for w in p.wheels))
