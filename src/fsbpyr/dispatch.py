"""``fsbpyr build`` for another Python than the running fsbpyr's (plan, Phase 9, settled with the
owner on 2026-10-18): the matching variant of this fsbpyr's own release is fetched the way
``fsbpyr self update`` fetches (the release's signed sums, the ``.xz`` checked against them,
unpacked in-process with lzma, verified in-process), kept in the tool's cache and run with the
same arguments. Once cached, it needs no network. A developer build never fetches a released
variant, which would mix released code into a development build: it stops and says how to build
the variant locally. Nor does fsbpyr running from source, which cannot check a signature."""

import contextlib
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from .inspection import InspectError, read
from .runtime.registry import cache_root, secure_directory
from .selfupdate import (
    REPO,
    SUMS,
    UpdateError,
    check_glibc,
    check_release_identity,
    fetch_checked,
    listed_digest,
    release_numbers,
    signed_sums,
)
from .signing import BuildSigning, available
from .targets import host_target
from .vcs import own_version, release_asset

DOWNLOADS_VARIABLE = "FSBPYR_RELEASE_DOWNLOADS"  # the releases' download URL; tests serve their own
# Set by `run` for the variant: the dispatching fsbpyr's own paths (``stubs.own_paths``), joined
# with os.pathsep. The variant's stubs are looked for beside them too, since the user places
# them beside the fsbpyr they ran, not in the cache. It only adds directories to look in: a
# stub found there still has to carry the runtime id (``stubs._checked``).
DISPATCHED_BY_VARIABLE = "FSBPYR_DISPATCHED_BY"
DEFAULT_DOWNLOADS = f"https://github.com/{REPO}/releases/download"
_MODE = 0o755


class DispatchError(RuntimeError):
    """The variant cannot be had; the message says why and what to do instead."""


def _developer_build(python: str) -> DispatchError:
    return DispatchError(
        f"this project builds with Python {python}, and this fsbpyr ({own_version()}, a developer "
        f"build) runs on another. A developer build never fetches a released variant; build "
        f"the variant from the checkout with `make build PYTHON={python}` (it writes "
        f"dist/fsbpyr-{python}) and run that, or pass --python for this fsbpyr's own Python"
    )


def _own_tag(python: str) -> str:
    """The tag of this release; a developer build is refused, with the way to build the
    variant it needs."""
    numbers = release_numbers(own_version().partition("-")[0])
    if numbers is None:
        raise _developer_build(python)
    return "v{}.{}.{}".format(*numbers)


def _fetch(python: str, tag: str, final: Path) -> None:
    """The release's asset for ``python`` into ``final``, checked against the release's signed
    sums and then as ``self update`` checks: the asset it should be, of this very version;
    ``final`` appears whole or not at all."""
    asset = release_asset(python, host_target().name)
    base = f"{os.environ.get(DOWNLOADS_VARIABLE) or DEFAULT_DOWNLOADS}/{tag}"
    handle, name = tempfile.mkstemp(dir=final.parent, prefix=f".{final.name}.")
    os.close(handle)
    new = Path(name)
    try:
        sums = signed_sums(f"{base}/{SUMS}", f"{base}/{SUMS}.sig", tag)
        fetch_checked(f"{base}/{asset}.xz", listed_digest(sums, f"{asset}.xz", tag), new)
        new.chmod(_MODE)
        report = read(new, verify=True)
        if check_release_identity(report.info, asset) != release_numbers(tag):
            raise UpdateError(f"the download is not fsbpyr {tag}")
        check_glibc(report.info.get("glibc"))
        new.rename(final)
    except (UpdateError, InspectError) as exc:
        raise DispatchError(
            f"this project builds with Python {python}, which needs {asset} of release {tag}; "
            f"it is not in the cache and could not be fetched: {exc}"
        ) from exc
    finally:
        with contextlib.suppress(OSError):
            new.unlink()


def variant(python: str, log: Callable[[str], None] = print, *, offline: bool = False) -> Path:
    """The executable of this release that runs on ``python``: from the cache, else fetched
    (``offline``: never fetched)."""
    tag = _own_tag(python)
    root = secure_directory(cache_root())
    directory = root / "downloads"
    directory.mkdir(mode=0o700, exist_ok=True)
    asset = release_asset(python, host_target().name)
    final = directory / f"{asset}-{tag}"
    if not final.is_file():
        if not available():
            raise _developer_build(python)  # from source, even at a tag: nothing can be checked
        if offline:
            raise DispatchError(
                f"this project builds with Python {python}, which needs {asset} of release "
                f"{tag}; it is not in the cache and --offline fetches nothing: run the build "
                "once without --offline"
            )
        log(f"fetching {asset} of release {tag}")
        _fetch(python, tag, final)
    return final


def run(
    python: str, args: Sequence[str], log: Callable[[str], None] = print, *, offline: bool = False,
    signing: BuildSigning | None = None,
) -> int:
    """Run the variant for ``python`` with ``args`` (this command line, as given); its exit
    status. The variant builds, signs and warns: it alone gets the signing variables this
    fsbpyr took out of its environment (``signing``), so none of its other processes has them."""
    executable = variant(python, log, offline=offline)
    log(f"the project builds with Python {python}: running {executable}")
    from .runtime import stubs  # noqa: PLC0415  stubs.py needs this module

    env = {**os.environ, DISPATCHED_BY_VARIABLE: os.pathsep.join(map(str, stubs.own_paths())),
           **(signing.environment() if signing is not None else {})}  # fmt: skip
    try:
        return subprocess.run([str(executable), *args], check=False, env=env).returncode  # noqa: S603
    except OSError as exc:
        raise DispatchError(f"cannot run {executable}: {exc}") from exc
