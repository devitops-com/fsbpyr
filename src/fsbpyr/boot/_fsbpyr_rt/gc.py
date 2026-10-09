"""The cache collector (D38): removes from an app's cache what no process uses any more.

The cache's root has a marker (``.fsbpyr-cache``), written (``mark``; the launcher's cache.c
alike) only into a root that holds nothing but our names, as a new root does: a directory of
the user's (``FSBPYR_CACHE_DIR`` may name one) is never marked, and a root without the marker
is never touched. Every generation and resource tree (``runtime/<32 hex>``,
``resources/<32 hex>``) has a ``.lock`` that each process using the tree holds shared for its
whole life (taken by the launcher's sync), and whose mtime, touched once a day, is when the tree
was last used. A run holds ``.gc`` exclusively (one run at a time; its mtime is when the last
one started), then for each tree of ours (one with its lock or its site): its lock taken
exclusively (failing: in use; made,
with the tree's mtime as its last use, for a tree written before 0.13.0), a tree unused for 7
days, or any not in use when the file system has less than 10% free, is renamed into ``.trash``
while locked; what the trash holds is removed at the end, without following a symlink. Files a
killed process left in a tree's ``.scratch`` go too: those nobody holds, an hour old. A tree
that cannot be removed is reported and the run goes on. Nothing else is touched. Standard
library only: the boot code runs it (``FSBPYR_GC``), and ``fsbpyr cache gc`` with the tool's
own cache (runtime/collect.py, the same rules).
"""

import contextlib
import errno
import fcntl
import functools
import itertools
import os
import re
import secrets
import stat
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

MARKER, LOCK, SCRATCH, TRASH, STAMP = ".fsbpyr-cache", ".lock", ".scratch", ".trash", ".gc"
MARKER_TEXT = b"fsbpyr's cache: collected by fsbpyr (D38)\n"  # cache.c's
KINDS = ("runtime", "resources")
# The tool's cache shares fsbpyr's own root (runtime/collect.py): its names are ours as well.
TOOL = (".gc-tool", ".trash-tool", "runtimes", "pbs", "sysroots", "downloads")
OURS = frozenset({MARKER, STAMP, TRASH, *KINDS, *TOOL})  # cache.c's OURS: a root to mark
# What each tree or item fsbpyr makes holds besides its lock (an app tree its site; the tool's
# items, runtime/collect.py, what marks them complete): a directory with neither is not ours.
PROOF = {"runtime": "site", "resources": "site", "runtimes": "runtime.json",
         "pbs": "python/PYTHON.json", "sysroots": ".fsbpyr-sysroot"}  # fmt: skip
TREE = re.compile(r"[0-9a-f]{32}")
TRASHED = re.compile(r"[a-z]+-[^/]+-[0-9]+-[0-9a-f]{8}")  # names Run.discard gives
DAY, HOUR = 86400, 3600
UNUSED = 7 * DAY  # owner decision (D38): a tree unused this long goes
STALE = HOUR  # a temporary file nobody holds and this old is a killed process's
PRESSURE = 0.10  # owner decision (D38): under this share free, every tree not in use goes
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_LOCK = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOCTTY
_LISTED = 4096  # directory entries read at a time while removing


@dataclass
class Report:
    root: str
    ours: bool = True  # the root has the marker
    busy: bool = False  # another run holds the lock
    pressure: bool = False
    removed: list[str] = field(default_factory=list)
    temporaries: int = 0  # temporary files removed
    kept: int = 0  # in use, or used lately
    errors: list[str] = field(default_factory=list)  # what could not be removed, and why

    def text(self) -> str:
        if not self.ours:
            return f"fsbpyr: {self.root} is no fsbpyr cache (no {MARKER}): left alone"
        if self.busy:
            return f"fsbpyr: {self.root} is being collected by another process"
        removed = ", ".join(self.removed) or "nothing"
        why = " (less than 10% free)" if self.pressure else ""
        failed = f"; failed: {'; '.join(self.errors)}" if self.errors else ""
        return (f"fsbpyr: collected {self.root}{why}: removed {removed}; "
                f"{self.temporaries} temporary files; {self.kept} kept{failed}")  # fmt: skip


@dataclass
class Run:
    """One run over a root (``run``): what it removes, and its report."""

    root: int
    trash: str
    now: float
    report: Report

    def attempt(self, what: str, action: Callable[[], object]) -> None:
        """``action``, its failure reported as ``what``'s: one item never stops a run."""
        try:
            action()
        except OSError as exc:
            self.report.errors.append(f"{what}: {exc.strerror or exc}")

    def unused(self, last: float) -> bool:
        return self.report.pressure or self.now - last >= UNUSED

    def take(self, parent: int, kind: str, name: str) -> bool:
        """The item ``name`` in ``parent`` (a directory of ours: with its .lock, or what PROOF
        names) renamed into the trash if nobody holds it and it is unused; whether it was. Its
        lock is taken
        exclusively first, and made for an item from before 0.13.0 (its directory's mtime then
        is the last use, and stays so): a process that takes the item meanwhile waits, then
        finds it gone and makes it afresh. The lock is let go once the item is renamed, before
        it is removed: nothing waits for a removal."""
        item = os.open(name, _DIRECTORY, dir_fd=parent)
        try:
            made = not _exists(item, LOCK)
            if made and not _exists(item, PROOF[kind]):
                return False  # neither lock nor content of ours: not ours (left alone)
            before = os.fstat(item).st_mtime  # making the lock changes the directory's mtime
            with exclusive(item, LOCK, create=True) as lock:
                if lock is None:
                    return False  # in use (or a lock that is not ours: left alone)
                last = before if made else os.fstat(lock).st_mtime
                if made or last > self.now:  # dated ahead: the clock was set back
                    last = min(last, self.now)
                    os.utime(lock, (last, last))
                if not self.unused(last):
                    return False
                self.discard(parent, item, kind, name)
                return True
        finally:
            os.close(item)

    def discard(self, parent: int, item: int, kind: str, name: str) -> None:
        """Rename ``name`` (in ``parent``, open as ``item``) into the root's trash, atomically: it
        is gone for every process; the trash is emptied at the end of the run."""
        now = os.stat(name, dir_fd=parent, follow_symlinks=False)
        held = os.fstat(item)
        if (now.st_dev, now.st_ino) != (held.st_dev, held.st_ino):
            raise OSError(errno.ESTALE, "replaced while collected", name)
        if held.st_mode & stat.S_IRWXU != stat.S_IRWXU:  # renaming a directory writes it
            os.fchmod(item, stat.S_IRWXU)
        with contextlib.suppress(FileExistsError):
            os.mkdir(self.trash, 0o700, dir_fd=self.root)
        if not ours(self.root, self.trash, stat.S_ISDIR):
            raise OSError(errno.ENOTDIR, "not a directory of ours", self.trash)
        trash = os.open(self.trash, _DIRECTORY, dir_fd=self.root)
        try:
            doomed = f"{kind}-{name}-{os.getpid()}-{secrets.token_hex(4)}"
            os.rename(name, doomed, src_dir_fd=parent, dst_dir_fd=trash)
        finally:
            os.close(trash)

    def listed(self, kind: str) -> tuple[int, list[str]]:
        """The directory ``kind`` of the root, open, and its names; a failure reported: -1."""
        fd = -1
        try:
            fd = os.open(kind, _DIRECTORY, dir_fd=self.root)
            return fd, sorted(os.listdir(fd))
        except OSError as exc:
            if fd >= 0:
                os.close(fd)
            self.report.errors.append(f"{kind}: {exc.strerror or exc}")
            return -1, []

    def empty_trash(self) -> None:
        """Everything renamed into the trash, by this run or an interrupted one (the names
        discard gives; nothing else there is touched)."""
        if not ours(self.root, self.trash, stat.S_ISDIR):
            return
        trash = os.open(self.trash, _DIRECTORY, dir_fd=self.root)
        try:
            names: list[str] = []
            self.attempt(self.trash, lambda: names.extend(sorted(os.listdir(trash))))
            for name in names:
                if TRASHED.fullmatch(name):
                    self.attempt(name, functools.partial(remove, trash, name))
        finally:
            os.close(trash)


def collect(root: str, *, keep: frozenset[str] = frozenset(), now: float | None = None,
            pressure: bool | None = None) -> Report:  # fmt: skip
    """Collect the app cache at ``root``; ``keep``: trees never removed ("runtime/<id>": the
    running app's own). ``now`` and ``pressure`` are found out unless given (tests)."""

    def trees(collection: Run) -> None:
        for kind in KINDS:
            for name, kind_fd in _entries(collection, kind):
                label = f"{kind}/{name}"
                tree = functools.partial(_tree, collection, kind_fd, label, keep)
                collection.attempt(label, tree)

    return run(root, STAMP, TRASH, trees, now=now, pressure=pressure)


def run(root: str, stamp: str, trash: str, work: Callable[[Run], None], *,  # noqa: PLR0913
        now: float | None = None, pressure: bool | None = None) -> Report:  # fmt: skip
    """A run over ``root``: only a root with the marker, one run at a time (``stamp`` held
    LOCK_EX and touched), then ``work``; what it put into ``trash`` is removed at the end, with
    what an interrupted run left there."""
    report = Report(root)
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except FileNotFoundError:
        report.ours = False
        return report
    try:
        if not ours(root_fd, MARKER, stat.S_ISREG):
            report.ours = False
            return report
        with exclusive(root_fd, stamp, create=True) as held:
            if held is None:
                report.busy = True
                return report
            os.utime(held)
            report.pressure = short_of_space(root_fd) if pressure is None else pressure
            collection = Run(root_fd, trash, time.time() if now is None else now, report)
            work(collection)
            collection.empty_trash()
    finally:
        os.close(root_fd)
    return report


def short_of_space(where: int | str) -> bool:
    """Whether the file system of ``where`` (a path, or a descriptor) has less than 10% free."""
    usage = os.statvfs(where)
    return usage.f_blocks > 0 and usage.f_bavail < PRESSURE * usage.f_blocks


def ours(directory: int, name: str, kind: Callable[[int], bool]) -> bool:
    """Whether ``name`` in ``directory`` is of the given kind (S_ISREG, S_ISDIR) and ours."""
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return kind(info.st_mode) and info.st_uid == os.geteuid()


def mark(root: int) -> bool:
    """The root's marker, written if it is missing and the root holds nothing but our names and
    is new (empty) or holds a tree or item of ours (PROOF); whether the root is marked. A
    directory of the user's is never marked, even one that holds nothing but names like ours."""
    if ours(root, MARKER, stat.S_ISREG):
        return True
    with os.scandir(root) as entries:
        names = [entry.name for entry in itertools.islice(entries, len(OURS) + 1)]
    if any(name not in OURS for name in names) or (names and not _proven(root)):
        return False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOCTTY
    try:
        fd = os.open(MARKER, flags, 0o600, dir_fd=root)
    except FileExistsError:  # another process marked it meanwhile
        return ours(root, MARKER, stat.S_ISREG)
    try:
        os.write(fd, MARKER_TEXT)
    finally:
        os.close(fd)
    return True


def _proven(root: int) -> bool:
    """Whether the root holds a tree or item of ours: one with its lock, or what PROOF names."""
    for kind, proof in PROOF.items():
        if not ours(root, kind, stat.S_ISDIR):
            continue
        kind_fd = os.open(kind, _DIRECTORY, dir_fd=root)
        try:
            with os.scandir(kind_fd) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False) and any(
                        _exists(kind_fd, f"{entry.name}/{name}") for name in (LOCK, proof)
                    ):  # fmt: skip
                        return True
        finally:
            os.close(kind_fd)
    return False


@contextlib.contextmanager
def exclusive(directory: int, name: str, *, create: bool = False) -> Iterator[int | None]:
    """The file ``name`` held LOCK_EX; None if another process holds it, or it is missing, or
    no regular file of ours (a symlink, a FIFO: left alone), or it cannot be locked here."""
    fd = _open_lock(directory, name, create=create)
    if fd < 0:
        yield None
        return
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            yield None
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # held, or no locks on this file system: left alone
            yield None
            return
        yield fd
    finally:
        os.close(fd)


def _open_lock(directory: int, name: str, *, create: bool) -> int:
    """``name`` open for writing (on NFS an exclusive lock needs it), else for reading (a file
    of ours without write permission: a temporary file once its mode is set); -1 if missing or
    a symlink."""
    flags = _LOCK | (os.O_CREAT if create else 0)
    for mode in (os.O_RDWR, os.O_RDONLY):
        try:
            return os.open(name, flags | mode, 0o600, dir_fd=directory)
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ELOOP):
                return -1
            if exc.errno not in (errno.EACCES, errno.EROFS) or mode == os.O_RDONLY:
                raise
    return -1


def remove(parent: int, name: str) -> None:
    """``name`` in ``parent`` removed with everything below it, never following a symlink and
    holding three descriptors at most, whatever the depth (shutil.rmtree holds one a level: a
    deep tree runs out of them); a directory of ours we may not open or write to is made so."""
    if not stat.S_ISDIR(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode):
        os.unlink(name, dir_fd=parent)
        return
    _usable(parent, name)
    path = [name]
    while path:
        directory = _open_path(parent, path)
        try:
            below = _empty(directory)
            if below is not None:
                _usable(directory, below)
        finally:
            os.close(directory)
        if below is not None:
            path.append(below)
            continue
        last = path.pop()
        above = _open_path(parent, path)
        try:
            os.rmdir(last, dir_fd=above)
        finally:
            os.close(above)


def _usable(parent: int, name: str) -> None:
    """The directory ``name`` in ``parent``, if ours, made readable, writable and searchable
    for us (through a descriptor: never a symlink's target)."""
    fd = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if info.st_uid == os.geteuid() and info.st_mode & stat.S_IRWXU != stat.S_IRWXU:
            os.chmod(f"/proc/self/fd/{fd}", stat.S_IRWXU)
    finally:
        os.close(fd)


def _exists(directory: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _open_path(parent: int, path: list[str]) -> int:
    """The directory ``parent``/``path``, opened a level at a time (one descriptor kept)."""
    fd = os.dup(parent)
    for name in path:
        try:
            following = os.open(name, _DIRECTORY, dir_fd=fd)
        finally:
            os.close(fd)
        fd = following
    return fd


def _empty(directory: int) -> str | None:
    """Every entry of ``directory`` but directories removed; a directory left, if any."""
    while True:
        with os.scandir(directory) as entries:
            listed = list(itertools.islice(entries, _LISTED))
        for entry in listed:
            if entry.is_dir(follow_symlinks=False):
                return entry.name
            os.unlink(entry.name, dir_fd=directory)
        if len(listed) < _LISTED:
            return None


def _entries(collection: Run, kind: str) -> Iterator[tuple[str, int]]:
    """The trees of a kind: names of ours (32 hex digits), directories of ours."""
    if not ours(collection.root, kind, stat.S_ISDIR):
        return
    kind_fd, names = collection.listed(kind)
    try:
        for name in names:
            if TREE.fullmatch(name) and ours(kind_fd, name, stat.S_ISDIR):
                yield name, kind_fd
    finally:
        if kind_fd >= 0:
            os.close(kind_fd)


def _tree(collection: Run, kind_fd: int, label: str, keep: frozenset[str]) -> None:
    """One tree of a run (``label``: "runtime/<id>"): removed, or kept and its stale temporary
    files removed."""
    kind, name = label.split("/")
    if label not in keep and collection.take(kind_fd, kind, name):
        collection.report.removed.append(label)
        return
    collection.report.kept += 1
    tree = os.open(name, _DIRECTORY, dir_fd=kind_fd)
    try:
        if ours(tree, SCRATCH, stat.S_ISDIR):
            scratch = os.open(SCRATCH, _DIRECTORY, dir_fd=tree)
            try:
                collection.report.temporaries += _clean_scratch(scratch, collection.now)
            finally:
                os.close(scratch)
    finally:
        os.close(tree)


def _clean_scratch(scratch: int, now: float) -> int:
    """Temporary files of a kept tree that nobody writes (none holds them) and an hour old."""
    removed = 0
    for entry in os.listdir(scratch):
        if not (entry.startswith(".fsbpyr-") and ours(scratch, entry, stat.S_ISREG)):
            continue
        with exclusive(scratch, entry) as held:
            if held is not None and now - os.fstat(held).st_mtime >= STALE:
                os.unlink(entry, dir_fd=scratch)
                removed += 1
    return removed
