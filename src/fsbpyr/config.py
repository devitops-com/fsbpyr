"""Build configuration: command-line options override ``[tool.fsbpyr]`` (plan §3.6 step 1, §9)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .payload.spec import ZSTD_FAST_LEVEL, ZSTD_LEVEL
from .project import Project


class ConfigError(ValueError):
    """Invalid [tool.fsbpyr] table or option."""


@dataclass(frozen=True, slots=True)
class BuildConfig:
    entry: str | None = None  # console script name, "module" or "module:attr"
    output: Path | None = None
    requirements: str | None = None
    extras: tuple[str, ...] = ()
    include_source: bool = True
    offline: bool = False
    smoke: bool = True
    allowed_system_libs: tuple[str, ...] = ()  # sonames extensions may load from the host
    fs_packages: tuple[str, ...] = ()  # packages whose files must exist on disk (FS_REQUIRED)
    hints: bool = False  # also print guesses (packages that may read files next to __file__)
    fast_compression: bool = False  # zstd level 3 instead of 9 for app content (D29)
    best_compression: bool = False  # native files as xz -6 (D33): smaller, slower to build
    interpreter_reexec: bool = False  # -m/-c from the app's own children run as Python (D23)

    def __post_init__(self) -> None:
        if self.fast_compression and self.best_compression:
            raise ConfigError("best-compression and fast-compression exclude each other")

    @property
    def zstd_level(self) -> int:
        """Level of the app's blobs and of every block (D30); the runtime's stdlib frames that
        stay outside blocks come prebuilt at level 9."""
        return ZSTD_FAST_LEVEL if self.fast_compression else ZSTD_LEVEL


def _expect(key: str, value: Any, kind: type | tuple[type, ...]) -> Any:
    if not isinstance(value, kind):
        raise ConfigError(f"[tool.fsbpyr] {key} has the wrong type: {value!r}")
    return value


def _soname(key: str, value: Any) -> str:
    name: str = _expect(key, value, str)
    if not name or "/" in name:
        raise ConfigError(f"[tool.fsbpyr] {key} entry {name!r} is not a library file name")
    return name


def _package(key: str, value: Any) -> str:
    name: str = _expect(key, value, str)
    if not name or not all(part.isidentifier() for part in name.split(".")):
        raise ConfigError(f"[tool.fsbpyr] {key} entry {name!r} is not a package name")
    return name


def _from_tool(project: Project) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in project.tool.items():
        if key in ("entry", "requirements"):
            values[key] = _expect(key, value, str)
        elif key == "output":
            values["output"] = project.directory / _expect(key, value, str)
        elif key == "extras":
            extras = _expect(key, value, list)
            values["extras"] = tuple(_expect(key, extra, str) for extra in extras)
        elif key == "include-source":
            values["include_source"] = _expect(key, value, bool)
        elif key == "fast-compression":
            values["fast_compression"] = _expect(key, value, bool)
        elif key == "best-compression":
            values["best_compression"] = _expect(key, value, bool)
        elif key == "interpreter-reexec":
            values["interpreter_reexec"] = _expect(key, value, bool)
        elif key == "allow-system-libs":
            libs = _expect(key, value, list)
            values["allowed_system_libs"] = tuple(_soname(key, lib) for lib in libs)
        elif key == "fs-packages":
            packages = _expect(key, value, list)
            values["fs_packages"] = tuple(_package(key, package) for package in packages)
        else:
            raise ConfigError(
                f"unknown [tool.fsbpyr] key {key!r}; supported: entry, output, requirements, "
                "extras, include-source, fast-compression, best-compression, "
                "allow-system-libs, fs-packages, interpreter-reexec"
            )
    return values


def load_config(project: Project, **options: Any) -> BuildConfig:
    """Merge: an option counts as given unless it is None (CLI flags default to None). A
    compression flag given on the command line also drops the other one from the project."""
    values = _from_tool(project)
    given = {key: value for key, value in options.items() if value is not None}
    for flag, other in (("fast_compression", "best_compression"),
                        ("best_compression", "fast_compression")):  # fmt: skip
        if given.get(flag):
            values.pop(other, None)
    values.update(given)
    return BuildConfig(**values)
