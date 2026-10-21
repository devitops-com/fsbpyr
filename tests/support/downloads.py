"""The sha256-pinned downloads of the end-to-end runtime (PBS and the sysroot's packages), kept
between sessions in FSBPYR_TEST_DOWNLOADS: each session's cache is a fresh directory, and
without this each one downloads them again. Only files that match their pin go in or out;
fsbpyr checks them again when it uses them.

The runtimes built from them are kept there too, in a cache of their own (``cache/``): built
once (before the workers of a parallel run start), and copied into each session's cache. A
runtime's id covers what it is built from, so a kept one is rebuilt only when that changes."""

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


def fill(python: str, kept: Path) -> None:
    """Download into ``kept`` the pinned files it lacks: once, before the workers of a parallel
    run start, each of which would otherwise fetch them for its own cache."""
    pin = pins.pin_for(python, registry.host_triple())
    sources = {f"{pin.name}.tar.zst": (pin.url, pin.sha256)}
    for url, sha256 in pins.sysroot_for(pin.triple).packages:
        sources[url.rpartition("/")[2]] = (url, sha256)
    for name, (url, sha256) in sources.items():
        if not _matches(kept / name, sha256):
            pbs.download(url, sha256, kept / name)


def build_kept_runtime(python: str, kept: Path, *, sanitize: bool) -> None:
    """``python``'s runtime built into ``kept``'s cache, from the kept downloads, unless it is
    there; that Python's older runtimes there go."""
    cache = registry.secure_directory(kept / "cache")
    files = pinned(python)
    seed(files, kept, cache)
    built = registry.build_runtime(python, cache=cache, log=lambda _: None, sanitize=sanitize)
    keep(files, kept, cache)
    for old in (cache / "runtimes").glob(f"{built.tag}-*"):
        if old.name != built.runtime_id and old.name.endswith("-sanitized") == sanitize:
            shutil.rmtree(old)


def seed_runtime(python: str, kept: Path, cache: Path, *, sanitize: bool) -> None:
    """Copy ``python``'s runtime from ``kept``'s cache into ``cache``, unless it is there."""
    name = registry.runtime_id(pins.pin_for(python, registry.host_triple()), sanitize=sanitize)
    target = registry.secure_directory(cache) / "runtimes" / name
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(name + ".part")
    shutil.copytree(kept / "cache" / "runtimes" / name, partial,
                    ignore=shutil.ignore_patterns(".lock"))  # fmt: skip
    partial.rename(target)


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
