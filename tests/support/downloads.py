"""The sha256-pinned downloads of the end-to-end runtime (PBS and the sysroot's packages), kept
between sessions in FSBPYR_TEST_DOWNLOADS: each session's cache is a fresh directory, and
without this each one downloads them again. Only files that match their pin go in or out;
fsbpyr checks them again when it uses them."""

import shutil
from pathlib import Path

from fsbpyr.runtime import pbs, pins, registry


def pinned(python: str) -> dict[str, str]:
    """{file name in the cache's downloads directory: its pinned SHA-256} for ``python``."""
    pin = pins.pin_for(python, registry.host_triple())
    files = {f"{pin.name}.tar.zst": pin.sha256}
    for url, sha256 in pins.sysroot_for(pin.triple).packages:
        files[url.rpartition("/")[2]] = sha256
    return files


def _matches(path: Path, sha256: str) -> bool:
    return path.is_file() and pbs.sha256_of(path) == sha256


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    shutil.copyfile(source, partial)
    partial.replace(target)


def seed(files: dict[str, str], kept: Path | None, cache: Path) -> dict[str, str]:
    """Copy the pinned files ``kept`` holds into ``cache``; {name: "seeded" or "fetched"}, the
    latter for those the session will download."""
    status = {}
    for name, sha256 in files.items():
        if kept is not None and _matches(kept / name, sha256):
            _copy(kept / name, cache / "downloads" / name)
            status[name] = "seeded"
        else:
            status[name] = "fetched"
    return status


def keep(files: dict[str, str], kept: Path | None, cache: Path) -> None:
    """Copy the pinned files ``cache`` holds, and ``kept`` lacks, into ``kept``."""
    if kept is None:
        return
    for name, sha256 in files.items():
        source = cache / "downloads" / name
        if not _matches(kept / name, sha256) and _matches(source, sha256):
            _copy(source, kept / name)
