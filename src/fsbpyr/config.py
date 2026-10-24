"""Build configuration: command-line options override ``[tool.fsbpyr]`` (plan §3.6 step 1, §9)."""

from collections.abc import Callable
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
    smoke_args: tuple[str, ...] = ()  # run the new executable once with these; none: do not
    allowed_system_libs: tuple[str, ...] = ()  # sonames extensions may load from the host
    fs_packages: tuple[str, ...] = ()  # packages whose files must exist on disk (FS_REQUIRED)
    hints: bool = False  # also print guesses (packages that may read files next to __file__)
    fast_compression: bool = False  # zstd level 3 instead of 9 for app content (D29)
    best_compression: bool = False  # native files as xz -6 (D33): smaller, slower to build
    # -m/-c/payload scripts from the app's own children: True runs them as Python (D23),
    # False runs the app in them, None (unset) refuses them (D44)
    interpreter_reexec: bool | None = None
    strict: bool = False  # a warning about the app, or an unlocked build, fails the build
    python: str | None = None  # "3.12": the Python to build for; None: the version rule decides

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


def _string(key: str, value: Any, project: Project) -> Any:
    return _expect(key, value, str)


def _flag(key: str, value: Any, project: Project) -> Any:
    return _expect(key, value, bool)


def _path(key: str, value: Any, project: Project) -> Any:
    return project.directory / _expect(key, value, str)


def _strings(key: str, value: Any, project: Project) -> Any:
    return tuple(_expect(key, item, str) for item in _expect(key, value, list))


def _sonames(key: str, value: Any, project: Project) -> Any:
    return tuple(_soname(key, lib) for lib in _expect(key, value, list))


def _packages(key: str, value: Any, project: Project) -> Any:
    return tuple(_package(key, package) for package in _expect(key, value, list))


@dataclass(frozen=True, slots=True)
class ConfigKey:
    """A ``[tool.fsbpyr]`` key: the BuildConfig field it sets, how its value is read and
    checked, and the text the man page's CONFIGURATION section shows for it."""

    name: str
    field: str
    convert: Callable[[str, Any, Project], Any]
    doc: str


# The one list of keys: _from_tool reads it, the unknown-key message names its keys and the
# man page documents them.
CONFIG_KEYS = (
    ConfigKey("entry", "entry", _string,
              "String. The console script name, `module` or `module:function`; as `--entry`."),
    ConfigKey("output", "output", _path,
              "String. Where to write the executable, relative to the project; as `--output`."),
    ConfigKey("requirements", "requirements", _string,
              "String. A pinned requirements file, for pip projects without `pylock.toml`; "
              "as `--requirements`."),
    ConfigKey("extras", "extras", _strings,
              "List of strings. Project extras to install; as `--extra`."),
    ConfigKey("include-source", "include_source", _flag,
              "Boolean. Keep the `.py` sources (default true); as `--source`."),
    ConfigKey("fast-compression", "fast_compression", _flag,
              "Boolean. zstd level 3 instead of 9; as `--fast-compression`."),
    ConfigKey("best-compression", "best_compression", _flag,
              "Boolean. Native files as xz instead of zstd; as `--best-compression`."),
    ConfigKey("allow-system-libs", "allowed_system_libs", _sonames,
              "List of library file names. Libraries native code may load from the host; "
              "as `--allow-system-lib`."),
    ConfigKey("fs-packages", "fs_packages", _packages,
              "List of package names. Packages whose files must exist on disk; "
              "as `--fs-package`."),
    ConfigKey("smoke-args", "smoke_args", _strings,
              "List of strings. Arguments to run the new executable with after the smoke "
              "test; it must exit 0. Off unless set; as `--smoke-args`."),
    ConfigKey("python", "python", _string,
              "String. The Python version to build for, `X.Y` (3.12 to 3.15), inside "
              "`requires-python`; as `--python`. Without it `.python-version` and "
              "`requires-python` decide."),
    ConfigKey("strict", "strict", _flag,
              "Boolean. Fail the build on warnings; as `--strict`."),
    ConfigKey("interpreter-reexec", "interpreter_reexec", _flag,
              "Boolean. true: children the app starts as Python (`-m`, `-c`, a script of the "
              "app) run as Python; false: they run the app; unset: they are refused (exit 70); "
              "as `--interpreter-reexec`."),
)
_KEYS_BY_NAME = {key.name: key for key in CONFIG_KEYS}


def _from_tool(project: Project) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, value in project.tool.items():
        key = _KEYS_BY_NAME.get(name)
        if key is None:
            supported = ", ".join(known.name for known in CONFIG_KEYS)
            raise ConfigError(f"unknown [tool.fsbpyr] key {name!r}; supported: {supported}")
        values[key.field] = key.convert(name, value, project)
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
