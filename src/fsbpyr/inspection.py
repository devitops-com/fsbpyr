"""``fsbpyr inspect``: what an executable was built from, and with which fsbpyr (D28).

Everything comes from the payload's build information, so it works for executables built by any
fsbpyr version; builds before 0.2.0 recorded neither the project nor fsbpyr's own commit. The
values come from a file that may not be trusted, so they are never printed with control
characters (terminal escape sequences) in them.
"""

import json
import stat
from pathlib import Path

from .payload.format import PayloadError
from .payload.reader import Executable
from .vcs import version_string

UNKNOWN = "unknown"


class InspectError(RuntimeError):
    """The file cannot be read, or fsbpyr did not build it."""


def _shown(value: object) -> str:
    text = str(value)
    return text if text.isprintable() else repr(text)


def _read(path: Path) -> tuple[str, dict[str, object], int, int]:
    """App name, build information, distribution count and file size."""
    try:
        status = path.stat()
        if not stat.S_ISREG(status.st_mode):  # a FIFO would block the open
            raise InspectError(f"{path} is not a regular file")
        with Executable.open(path) as executable:
            payload = executable.payload
            app, text, dists = payload.app_name, payload.build_info, len(payload.dists())
    except PayloadError as exc:
        raise InspectError(f"{path} is not an executable fsbpyr built ({exc})") from exc
    except OSError as exc:
        raise InspectError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        info = json.loads(text)
    except (ValueError, RecursionError) as exc:  # bad JSON, numbers beyond the digit limit
        raise InspectError(f"{path} has corrupt build information") from exc
    if not isinstance(info, dict):
        raise InspectError(f"{path} has corrupt build information")
    return app, info, dists, status.st_size


def _built_with(info: dict[str, object]) -> str:
    """What `fsbpyr --version` of the building fsbpyr printed; its release number when it
    could not see its commit (a wheel install), and for builds before 0.2.0."""
    version = info.get("fsbpyr_version")
    if not isinstance(version, str) or version == version_string(None):
        version = info.get("fsbpyr", UNKNOWN)
    return _shown(version)


def describe(path: Path) -> list[str]:
    app, info, dists, size = _read(path)
    project = info.get("project")
    project = project if isinstance(project, dict) else {}
    name = project.get("name") if isinstance(project.get("name"), str) else app
    app_line = " ".join(_shown(part) for part in (name, project.get("version")) if part)
    git = info.get("git")
    if isinstance(git, dict):
        app_line += f" ({_shown(version_string(git))})"
    glibc = f", glibc >= {_shown(info['glibc'])}" if info.get("glibc") else ""
    return [
        f"app        {app_line}",
        f"built with fsbpyr {_built_with(info)}",
        f"python     {_shown(info.get('python', UNKNOWN))}{glibc}",
        f"size       {size / 1e6:.1f} MB, {dists} distribution{'s' * (dists != 1)}",
    ]
