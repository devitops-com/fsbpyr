"""``fsbpyr build`` for another Python than the running fsbpyr's (plan, Phase 9, settled with the
owner on 2026-10-18): the matching variant of this fsbpyr's own release is fetched the way
``fsbpyr self update`` fetches (the ``.xz``, unpacked in-process with lzma, verified in-process),
kept in the tool's cache and run with the same arguments. Once cached, it needs no network. A
developer build never fetches a released variant, which would mix released code into a
development build: it stops and says how to build the variant locally."""

import contextlib
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from .inspection import InspectError, read
from .runtime.registry import cache_root, secure_directory
from .selfupdate import REPO, UpdateError, release_numbers, unpack
from .vcs import own_version, release_asset

DOWNLOADS_VARIABLE = "FSBPYR_RELEASE_DOWNLOADS"  # the releases' download URL; tests serve their own
DEFAULT_DOWNLOADS = f"https://github.com/{REPO}/releases/download"
_MODE = 0o755


class DispatchError(RuntimeError):
    """The variant cannot be had; the message says why and what to do instead."""


def _own_tag(python: str) -> str:
    """The tag of this release; a developer build is refused, with the way to build the
    variant it needs."""
    version = own_version()
    numbers = release_numbers(version.partition("-")[0])
    if numbers is None:
        raise DispatchError(
            f"this project builds with Python {python}, and this fsbpyr ({version}, a developer "
            f"build) runs on another. A developer build never fetches a released variant; build "
            f"the variant from the checkout with `make build PYTHON={python}` (it writes "
            f"dist/fsbpyr-{python}) and run that, or pass --python for this fsbpyr's own Python"
        )
    return "v{}.{}.{}".format(*numbers)


def _fetch(python: str, tag: str, final: Path) -> None:
    """The release's asset for ``python`` into ``final``, verified; ``final`` appears whole or
    not at all."""
    asset = release_asset(python)
    url = f"{os.environ.get(DOWNLOADS_VARIABLE) or DEFAULT_DOWNLOADS}/{tag}/{asset}.xz"
    handle, name = tempfile.mkstemp(dir=final.parent, prefix=f".{final.name}.")
    os.close(handle)
    new = Path(name)
    try:
        unpack(url, new)
        new.chmod(_MODE)
        read(new, verify=True)
        new.rename(final)
    except (UpdateError, InspectError) as exc:
        raise DispatchError(
            f"this project builds with Python {python}, which needs {asset} of release {tag}; "
            f"it is not in the cache and could not be fetched: {exc}"
        ) from exc
    finally:
        with contextlib.suppress(OSError):
            new.unlink()


def variant(python: str, log: Callable[[str], None] = print) -> Path:
    """The executable of this release that runs on ``python``: from the cache, else fetched."""
    tag = _own_tag(python)
    root = secure_directory(cache_root())
    directory = root / "downloads"
    directory.mkdir(mode=0o700, exist_ok=True)
    final = directory / f"{release_asset(python)}-{tag}"
    if not final.is_file():
        log(f"fetching {release_asset(python)} of release {tag}")
        _fetch(python, tag, final)
    return final


def run(python: str, args: Sequence[str], log: Callable[[str], None] = print) -> int:
    """Run the variant for ``python`` with ``args`` (this command line, as given); its exit
    status."""
    executable = variant(python, log)
    log(f"the project builds with Python {python}: running {executable}")
    try:
        return subprocess.run([str(executable), *args], check=False).returncode  # noqa: S603
    except OSError as exc:
        raise DispatchError(f"cannot run {executable}: {exc}") from exc
