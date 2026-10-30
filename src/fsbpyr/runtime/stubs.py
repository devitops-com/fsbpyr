"""The stub of a released fsbpyr for another glibc baseline (EL8, ``fsbpyr build --glibc 2.28``;
research 9.5 option A2, D46).

A release fsbpyr carries one runtime, its stub linked against the default sysroot. An app for
glibc 2.28 needs the stub linked against the EL8 one, which the companion release of the tag
(el8-vX.Y.Z) publishes apart from the executables, since a published release takes no new
assets (not every release has one: the EL8 jobs run in a workflow of their own); the stdlib
frames and modules do not depend on the sysroot, so the executable's own are paired with it.
The stub comes from, in this order: a file the user placed beside the fsbpyr executable (for
machines without GitHub), the tool's cache, a download from the companion release, verified
against the SHA-256 it publishes and kept in the cache. Whichever it is, the stub must carry the
runtime id this fsbpyr computes (main.c, stub.py): a digest of the launcher sources and their
compile and link flags, the Python pin, the sysroot and the layout version. It is refused
otherwise, so a stub built from other inputs never goes into an app; several releases may share
the inputs, and their stubs are interchangeable. The cache names a stub by that id, not by the
release, so an update that leaves it unchanged reuses the download. A developer build has no
release to fetch from: it uses the cache's runtime (``fsbpyr runtime build --glibc 2.28``)."""

import contextlib
import dataclasses
import hashlib
import lzma
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from ..dispatch import DEFAULT_DOWNLOADS, DISPATCHED_BY_VARIABLE, DOWNLOADS_VARIABLE
from ..selfupdate import (
    REPO,
    NotFound,
    UpdateError,
    fetch_checked,
    listed_digest,
    release_numbers,
    signed_sums,
)
from ..signing import sums_lines
from ..targets import host_target
from ..vcs import own_version, release_stub_asset, release_stub_sums, release_stub_tag
from . import pins, registry
from .stub import stub_identity

RELEASES = f"https://github.com/{REPO}/releases"
_PLACED_STUBS = "fsbpyr-stub-*-glibc*"  # what a user places beside the executable
_PLACED_NAME = re.compile(r"fsbpyr-stub-\d+\.\d+-[\w-]+-glibc(\d+\.\d+)(\.xz)?")
_PLACED_SUFFIXES = ("", ".xz")  # the asset as it is, or as the release compresses it
_MAX_STUB = 256 * 1024 * 1024  # a stub is a few MB; a larger one is no stub (or a bomb)


class StubError(registry.RuntimeCacheError):
    """The release's stub for the baseline cannot be had, or is not the right one."""


def own_tag() -> str | None:
    """The tag of this release; None for a developer build."""
    numbers = release_numbers(own_version().partition("-")[0])
    return None if numbers is None else "v{}.{}.{}".format(*numbers)


def own_paths(argv0: str | None = None, real: str | None = None) -> list[Path]:
    """Where this executable is: the path it was run by (looked up on PATH when run by name;
    links kept, so a file placed beside a link is found) and, if it differs, its real path. A
    variant run by ``dispatch.run`` first has the paths of the fsbpyr that dispatched to it,
    where the user places stubs (absolute entries only)."""
    argv0 = sys.argv[0] if argv0 is None else argv0
    ran = argv0 if "/" in argv0 else shutil.which(argv0)
    found: list[Path] = []
    dispatched = os.environ.get(DISPATCHED_BY_VARIABLE, "").split(os.pathsep)
    for path in (*(entry for entry in dispatched if entry.startswith("/")), ran,
                 os.path.realpath("/proc/self/exe") if real is None else real):
        if path and (absolute := Path(os.path.abspath(path))) not in found:
            found.append(absolute)
    return found


def _no_stubs(tag: str, release: str, baseline: str, asset: str, beside: Path) -> StubError:
    return StubError(
        f"release {release} (the glibc {baseline} stubs of {tag}) does not exist or has none "
        f"for this Python: EL8 stubs are published separately and not for every release; "
        f"place {asset} (or {asset}.xz) next to {beside}, or use a release that has them: "
        f"{RELEASES}"
    )


def _offline(asset: str, beside: Path) -> StubError:
    return StubError(
        f"--offline: the {asset} is not placed or cached, and nothing is downloaded; place "
        f"{asset} (or {asset}.xz) next to {beside}, or build once without --offline to cache it"
    )


def _checked(  # noqa: PLR0913  the stub, where it is from, what it must be, its releases
    data: bytes, where: object, expected: str, asset: str, tag: str, *, release: str
) -> bytes:
    """``data``, if it is the stub with the runtime id ``expected``."""
    found = stub_identity(data)
    if found == expected:
        return data
    what = (f"it is the stub of runtime {found}" if found
            else "it carries no runtime id: it is no stub of an fsbpyr release")  # fmt: skip
    raise StubError(
        f"{where} is not the stub this fsbpyr ({tag}) needs: {what}, and this fsbpyr "
        f"expects {expected}; use the {asset} of release {release}"
    )


def _placed(paths: list[Path], asset: str) -> tuple[Path, bytes] | None:
    """The first file named like the asset (plain, then ``.xz``) in a directory of the
    executable, with its stub bytes."""
    for directory in dict.fromkeys(path.parent for path in paths):
        for suffix in _PLACED_SUFFIXES:
            path = directory / f"{asset}{suffix}"
            if not path.is_file():
                continue
            try:
                with path.open("rb") as handle:
                    data = handle.read(_MAX_STUB + 1)
                if suffix:
                    data = lzma.LZMADecompressor().decompress(data, _MAX_STUB + 1)
            except (OSError, lzma.LZMAError, EOFError) as exc:
                raise StubError(f"cannot read {path}: {exc}") from exc
            if len(data) > _MAX_STUB:
                raise StubError(f"{path} is larger than {_MAX_STUB >> 20} MB: no stub")
            return path, data
    return None


def _digest(path: Path) -> str | None:
    """The SHA-256 of the stub in the placed file ``path`` (unpacked when ``.xz``); None when
    it cannot be read."""
    try:
        data = path.read_bytes()
        if path.suffix == ".xz":
            data = lzma.LZMADecompressor().decompress(data, _MAX_STUB + 1)
    except (OSError, lzma.LZMAError, EOFError):
        return None
    return hashlib.sha256(data).hexdigest()


def _companion_sums(base: str, release: str, baseline: str) -> bytes:
    """The checked sums file of companion release ``release``: nothing of it is used before."""
    sums = f"{base}/{release}/{release_stub_sums(baseline)}"
    return signed_sums(sums, f"{sums}.sig", release)


def refused_after_update(tag: str, paths: list[Path]) -> tuple[list[Path], bool]:
    """The stubs placed beside ``paths`` (as ``own_paths``) that the fsbpyr of release ``tag``
    would refuse, and whether that is known. The new fsbpyr's runtime id cannot be computed
    here, so a stub is current when the SHA-256 the companion release publishes lists it; with
    no companion, no network or a sums file that fails its check all placed stubs are returned,
    unknown."""
    directories = dict.fromkeys(path.parent for path in paths)
    placed = {found: match[1] for directory in directories
              for found in sorted(directory.glob(_PLACED_STUBS))
              if found.is_file() and (match := _PLACED_NAME.fullmatch(found.name))}  # fmt: skip
    base = os.environ.get(DOWNLOADS_VARIABLE) or DEFAULT_DOWNLOADS
    listed: dict[str, set[str]] = {}
    refused: list[Path] = []
    for path, baseline in placed.items():
        try:
            if baseline not in listed:
                sums = _companion_sums(base, release_stub_tag(baseline, tag), baseline)
                text = sums.decode("utf-8", "replace")
                listed[baseline] = {digest for digest, _ in sums_lines(text)}
        except (UpdateError, ValueError):  # unreadable: unknown
            return list(placed), False
        if _digest(path) not in listed[baseline]:
            refused.append(path)
    return refused, True


def _download(directory: Path, base: str, release: str, baseline: str, asset: str) -> bytes:
    """The companion release's stub, its ``.xz`` checked against the signed sums before lzma sees
    it, and unpacked, against its own line."""
    sums = _companion_sums(base, release, baseline)
    published = listed_digest(sums, asset, release)
    packed = listed_digest(sums, f"{asset}.xz", release)
    handle, name = tempfile.mkstemp(dir=directory, prefix=f".{asset}.")
    os.close(handle)
    new = Path(name)
    try:
        fetch_checked(f"{base}/{release}/{asset}.xz", packed, new, _MAX_STUB)
        data = new.read_bytes()
    finally:
        with contextlib.suppress(OSError):
            new.unlink()
    if hashlib.sha256(data).hexdigest() != published:
        raise StubError(f"the {asset} downloaded from release {release} does not match the SHA-256 "
                        f"the release publishes ({published}); nothing was kept")  # fmt: skip
    return data


def _keep(data: bytes, final: Path) -> None:
    """``data`` into ``final``, whole or not at all."""
    handle, name = tempfile.mkstemp(dir=final.parent, prefix=f".{final.name}.")
    new = Path(name)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        new.rename(final)
    finally:
        with contextlib.suppress(OSError):
            new.unlink()


def stub_for(
    python: str, baseline: str, tag: str, cache: Path | None = None, *, offline: bool = False
) -> bytes:
    """The stub of release ``tag`` for ``python`` and ``baseline``, with this fsbpyr's runtime
    id: placed beside the executable, else kept in the cache, else downloaded (and kept) from
    the companion release of the tag; ``offline`` downloads nothing."""
    pin = pins.pin_for(python, registry.host_triple())
    expected = registry.runtime_id(pin, baseline=baseline)
    asset = release_stub_asset(python, host_target().name, baseline)
    release = release_stub_tag(baseline, tag)
    paths = own_paths()
    beside = paths[0] if paths else Path("fsbpyr")
    placed = _placed(paths, asset)
    if placed is not None:
        return _checked(placed[1], placed[0], expected, asset, tag, release=release)
    directory = registry.secure_directory(cache or registry.cache_root()) / "downloads"
    directory.mkdir(mode=0o700, exist_ok=True)
    final = directory / f"{asset}-{expected}"  # the id decides: an unchanged stub is reused
    if final.is_file() and stub_identity(final.read_bytes()) == expected:
        return final.read_bytes()
    final.unlink(missing_ok=True)  # not the stub (edited, damaged): again
    if offline:
        raise _offline(asset, beside)
    base = os.environ.get(DOWNLOADS_VARIABLE) or DEFAULT_DOWNLOADS
    try:
        data = _download(directory, base, release, baseline, asset)
    except NotFound as exc:
        raise _no_stubs(tag, release, baseline, asset, beside) from exc
    except UpdateError as exc:
        raise StubError(
            f"release {release}'s {asset} could not be fetched: {exc}; to build without the "
            f"network, place {asset} next to {beside}"
        ) from exc
    _checked(data, f"the {asset} downloaded from release {release}", expected, asset, tag,
             release=release)
    _keep(data, final)
    return data


def paired(
    own: registry.Runtime, baseline: str, cache: Path | None = None, *, offline: bool = False
) -> registry.Runtime | None:
    """``own``'s stdlib with the stub of its release for ``baseline``; None for a developer
    build, which has the cache's runtimes only."""
    tag = own_tag()
    if tag is None:
        return None
    python = own.python_version.rpartition(".")[0]
    pin = pins.pin_for(python, registry.host_triple())
    stub = stub_for(python, baseline, tag, cache, offline=offline)
    return dataclasses.replace(
        own, runtime_id=registry.runtime_id(pin, baseline=baseline), stub=stub, toolchain={},
        inputs=registry.runtime_inputs(pin, baseline), baseline=baseline,
    )
