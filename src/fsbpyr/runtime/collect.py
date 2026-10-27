"""The tool's cache, collected (D38): runtimes, PBS distributions, sysroots and downloads that
the running fsbpyr does not build with, and nobody used for 7 days (when the file system has less
than 10% free: every one not in use), go, under the rules of the app caches' collector
(_fsbpyr_rt.gc): an item in use (held: runtime/hold.py) stays, the others are renamed into a
trash and removed. What a killed process left while it worked (an extraction or a runtime's
staging directory, a partial download) goes after a day. Only a root with the marker is
collected: hold() writes it into one that holds nothing but our names. ``fsbpyr cache gc`` runs
it, and every app cache it finds; ``fsbpyr build``, ``inspect``, ``runtime build`` and
``runtime list`` check it at their start, at most once a day (once an hour when the file system
has less than 10% free)."""

import functools
import os
import re
import stat
import time
from pathlib import Path

from .. import targets
from ..boot._fsbpyr_rt import gc
from . import pins, registry

STAMP, TRASH = ".gc-tool", ".trash-tool"  # the app collector's are .gc and .trash: same root
_SYSROOT_LIBCS = "|".join(sorted({t.libc for t in targets.TARGETS if t.multiarch}))
ITEMS = ("runtimes", "pbs", "sysroots")
DOWNLOADS = "downloads"
# The names the tool gives, by subtree: nothing else there is ever removed (a directory of the
# user's may hold nothing but names like ours, and so be marked).
NAMES = {
    "runtimes": re.compile(r"cp[0-9]+-[a-z0-9_.-]+-[0-9a-f]{12}(-sanitized)?"),
    "pbs": re.compile(r"cpython-[0-9.]+\+[0-9]+-[a-z0-9_.+-]+"),
    "sysroots": re.compile(rf"[a-z0-9]+-({_SYSROOT_LIBCS})[0-9.]+-[a-z0-9_]+(-[0-9a-f]{{12}})?"),
    DOWNLOADS: re.compile(
        r"cpython-[0-9.]+\+[0-9]+-[a-z0-9_.+-]+\.tar\.zst"
        r"|[a-z0-9.+-]+_[a-z0-9.+~:-]+_[a-z0-9]+\.deb|[a-z0-9._+-]+\.(x86_64|aarch64)\.rpm"
        r"|tmp[a-z0-9_]+\.part"
    ),
}
STAGING = re.compile(r"\.(extract-[a-z0-9_]+|cp[0-9]+-.+)")  # pbs and sysroot, runtime staging
PARTIAL = ".part"  # a download being written
LEFTOVER = gc.DAY  # staging and partial downloads this old are a killed process's
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW


def needed() -> dict[str, frozenset[str]]:
    """What the running fsbpyr builds with, by subtree: never collected."""
    found: dict[str, set[str]] = {kind: set() for kind in (*ITEMS, DOWNLOADS)}
    for pin in pins.PINS:
        found["pbs"].add(pin.name)
        found[DOWNLOADS].add(f"{pin.name}.tar.zst")
        for baseline in pins.BASELINES:
            sysroot = pins.sysroot_for(pin.triple, baseline)
            found["runtimes"] |= {registry.runtime_id(pin, baseline=baseline),
                                  registry.runtime_id(pin, sanitize=True, baseline=baseline)}
            found["sysroots"].add(sysroot.identity)
            found[DOWNLOADS] |= {url.rpartition("/")[2] for url, _ in sysroot.packages}
    return {kind: frozenset(names) for kind, names in found.items()}


def collect_tool(
    root: Path, *, now: float | None = None, pressure: bool | None = None
) -> gc.Report:
    """Collect the tool's cache at ``root``: only one with the marker (hold() writes it into a
    root that holds nothing but our names: ``FSBPYR_CACHE_DIR`` may name a directory of the
    user's, which is then never touched)."""
    keep = needed()

    def items(collection: gc.Run) -> None:
        for kind in ITEMS:
            _items(collection, kind, keep[kind])
        _downloads(collection, keep[DOWNLOADS])

    return gc.run(str(root), STAMP, TRASH, items, now=now, pressure=pressure)


def _items(collection: gc.Run, kind: str, keep: frozenset[str]) -> None:
    if not gc.ours(collection.root, kind, stat.S_ISDIR):
        return
    parent, names = collection.listed(kind)
    try:
        for name in names:
            named = NAMES[kind].fullmatch(name) or STAGING.fullmatch(name)
            if named and gc.ours(parent, name, stat.S_ISDIR):
                item = functools.partial(_item, collection, parent, kind, name, keep)
                collection.attempt(f"{kind}/{name}", item)
    finally:
        if parent >= 0:
            os.close(parent)


def _item(collection: gc.Run, parent: int, kind: str, name: str, keep: frozenset[str]) -> None:
    """One item: a staging directory a day old goes (one younger may be being written, pressure
    or not); what the running fsbpyr builds with stays; the rest goes by the app trees' rules."""
    label = f"{kind}/{name}"
    if STAGING.fullmatch(name):
        item = os.open(name, _DIRECTORY, dir_fd=parent)
        try:
            if collection.now - os.fstat(item).st_mtime < LEFTOVER:
                return
            collection.discard(parent, item, kind, name)
        finally:
            os.close(item)
        collection.report.removed.append(label)
    elif name not in keep and collection.take(parent, kind, name):
        collection.report.removed.append(label)
    else:
        collection.report.kept += 1


def _downloads(collection: gc.Run, keep: frozenset[str]) -> None:
    if not gc.ours(collection.root, DOWNLOADS, stat.S_ISDIR):
        return
    parent, names = collection.listed(DOWNLOADS)
    try:
        for name in names:
            named = NAMES[DOWNLOADS].fullmatch(name) and name not in keep
            if named and gc.ours(parent, name, stat.S_ISREG):
                download = functools.partial(_download, collection, parent, name)
                collection.attempt(f"{DOWNLOADS}/{name}", download)
    finally:
        if parent >= 0:
            os.close(parent)


def _download(collection: gc.Run, parent: int, name: str) -> None:
    """A partial download a day old goes; another download, unused for 7 days or when the file
    system is short (what the running fsbpyr builds with is kept by the caller)."""
    age = collection.now - os.stat(name, dir_fd=parent, follow_symlinks=False).st_mtime
    if age >= LEFTOVER if name.endswith(PARTIAL) else collection.unused(collection.now - age):
        os.unlink(name, dir_fd=parent)
        collection.report.removed.append(f"{DOWNLOADS}/{name}")
    else:
        collection.report.kept += 1


def app_caches() -> list[Path]:
    """The app caches to collect: directories in ${XDG_CACHE_HOME:-~/.cache} with the marker, or
    that may have it (gc.mark: a cache an app of an earlier version made, which marks nothing),
    and $FSBPYR_CACHE_DIR if it has one. Without a home directory there is no ~/.cache, but
    $FSBPYR_CACHE_DIR still is one."""
    explicit = os.environ.get("FSBPYR_CACHE_DIR")
    try:
        home: Path | None = registry.cache_home()
    except registry.RuntimeCacheError:
        if not explicit:
            raise
        home = None
    found = sorted(p for p in home.glob("*/") if _marked(p)) if home and home.is_dir() else []
    if explicit:
        root = Path(os.path.abspath(explicit))  # as the boot code makes it (native.py)
        if (root / gc.MARKER).is_file() and root not in found:
            found.append(root)
    return found


def _marked(directory: Path) -> bool:
    """Whether ``directory`` has the marker, or may have it (made so): a directory of ours whose
    way is trusted, holding a tree of ours (an empty one may be some other program's)."""
    if gc.way_problem(str(directory)) is not None:
        return False
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)  # may be a symlink
    except OSError:
        return False  # not ours to read
    try:
        return os.fstat(fd).st_uid == os.geteuid() and gc.mark(fd, new=False)
    except OSError:
        return False
    finally:
        os.close(fd)


def collect_when_due(root: Path) -> gc.Report | None:
    """At fsbpyr's start: the tool's cache, a day after the last run (an hour, when the file
    system has less than 10% free; at once when the last run seems to be in the future: the
    clock was set back). None when not due, or when FSBPYR_GC is set (0: never, as for apps)."""
    if "FSBPYR_GC" in os.environ:
        return None
    try:
        age = time.time() - os.stat(root / STAMP).st_mtime
    except FileNotFoundError:
        age = gc.DAY  # never collected here (a root without the marker is left alone)
    if 0 <= age < gc.DAY and (age < gc.HOUR or not gc.short_of_space(str(root))):
        return None
    return collect_tool(root)
