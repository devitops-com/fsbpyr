"""The Python a build uses (plan, Phase 9, settled with the owner on 2026-10-18).

Strongest first: ``--python X.Y``, then ``python = "X.Y"`` in ``[tool.fsbpyr]`` (both an error
when unsupported or outside ``requires-python``), then a ``.python-version`` pin that names a
supported version inside the range (outside the range an error, an unsupported one a warning
before the rule applies), then ``requires-python``: the lowest supported minor version that
satisfies a lower bound, the highest below an upper-only bound, a pinned minor as pinned. A
project without a bound that picks one (none, or only ``!=``) uses the Python of the fsbpyr
that builds it. ``cli.py`` dispatches to another fsbpyr when the result is not its own.
"""

import re
from dataclasses import dataclass

from packaging.specifiers import SpecifierSet

from .bytecode import SUPPORTED_PYTHON
from .project import Project, ProjectError
from .runtime import pins

_MINOR = re.compile(r"[0-9]+\.[0-9]+")
_PIN = re.compile(r"([0-9]+\.[0-9]+)(\.[0-9A-Za-z.+-]+)?")
_LOWER_BOUNDS = frozenset({">=", ">", "~=", "==", "==="})
_UPPER_BOUNDS = frozenset({"<", "<="})


@dataclass(frozen=True, slots=True)
class Choice:
    version: str  # major.minor
    warnings: tuple[str, ...] = ()


def supported() -> list[str]:
    """The Python versions fsbpyr builds for, oldest first."""
    return [f"{major}.{minor}" for major, minor in sorted(SUPPORTED_PYTHON)]


def _sample(minor: str) -> str:
    """The version ``requires-python`` is tested with for ``minor``: the newest patch release
    a build uses (``~=3.12.1`` then admits 3.12), 0 for a version fsbpyr has no runtime for."""
    return next((pin.version for pin in pins.PINS if pin.python == minor), f"{minor}.0")


def _fits(allowed: SpecifierSet | None, minor: str) -> bool:
    return allowed is None or allowed.contains(_sample(minor), prereleases=True)


def _listed(versions: list[str]) -> str:
    return ", ".join(versions[:-1]) + f" and {versions[-1]}"


def _requested(project: Project, allowed: SpecifierSet | None, minor: str) -> Choice:
    names = supported()
    if not _MINOR.fullmatch(minor):
        raise ProjectError(f"Python {minor!r} is not a version X.Y (--python, python in "
                           "[tool.fsbpyr])")  # fmt: skip
    if minor not in names:
        raise ProjectError(f"Python {minor} is not supported; fsbpyr builds for "
                           f"{_listed(names)}")  # fmt: skip
    if not _fits(allowed, minor):
        raise ProjectError(f"Python {minor} is outside requires-python "
                           f"{project.requires_python!r}")  # fmt: skip
    return Choice(minor)


def _pinned(
    project: Project, allowed: SpecifierSet | None, pin: str
) -> tuple[str | None, tuple[str, ...]]:
    """The version the ``.python-version`` pin names, if it is usable; an error when it is
    outside the range, a warning when fsbpyr cannot build for it."""
    names = supported()
    match = _PIN.fullmatch(pin)
    if match is None:
        return None, (f".python-version pins {pin}, which is not a CPython X.Y version fsbpyr "
                      f"builds for ({_listed(names)}); ignored",)  # fmt: skip
    minor = match[1]
    if not _fits(allowed, minor):
        raise ProjectError(f".python-version pins {pin}, outside requires-python "
                           f"{project.requires_python!r}")  # fmt: skip
    if minor not in names:
        return None, (f".python-version pins {pin}, which fsbpyr does not build for "
                      f"({_listed(names)}); ignored",)  # fmt: skip
    return minor, ()


def resolve(project: Project, requested: str | None, *, own: str) -> Choice:
    """The Python ``project`` builds with: see the module's rule. ``own`` is the Python of
    the running fsbpyr, for a project that does not say."""
    allowed = project.specifiers()
    if requested is not None:
        return _requested(project, allowed, requested)
    warnings: tuple[str, ...] = ()
    if (pin := project.python_pin()) is not None:
        minor, warnings = _pinned(project, allowed, pin)
        if minor is not None:
            return Choice(minor)
    names = supported()
    fitting = [minor for minor in names if _fits(allowed, minor)]
    if not fitting:
        raise ProjectError(f"requires-python {project.requires_python!r} allows none of the "
                           f"Python versions fsbpyr builds for ({_listed(names)})")  # fmt: skip
    operators = {spec.operator for spec in allowed or ()}
    if operators & _LOWER_BOUNDS:
        return Choice(fitting[0], warnings)
    if operators & _UPPER_BOUNDS:
        return Choice(fitting[-1], warnings)
    return Choice(own if own in fitting else fitting[-1], warnings)
