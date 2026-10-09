"""Items of the tool's cache in use (D38): a runtime, a PBS distribution or a sysroot that a
command uses is held, so that a collection (``fsbpyr cache gc``, runtime/collect.py) leaves it
alone and knows when it was last used."""

import contextlib
import fcntl
import os
import time
from pathlib import Path

from ..boot._fsbpyr_rt import gc

LOCK = gc.LOCK
_FLAGS = os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOCTTY
_HELD: dict[Path, int] = {}  # this process's locks: held until it ends


def hold(directory: Path) -> bool:
    """Take ``directory`` (``<root>/<kind>/<name>``) for this process: the root marked (a root
    that holds nothing but our names: gc.mark), the item's .lock held LOCK_SH and touched once a
    day old. False if the item is gone, or was collected while the lock was waited for: the
    caller makes it again. An item that cannot be held here (a read-only cache, a file system
    without locks) is used unheld."""
    if directory in _HELD:
        return True
    with contextlib.suppress(OSError):  # a cache we cannot write is used, and never collected
        _mark(directory.parent.parent)
    try:
        fd = os.open(directory / LOCK, _FLAGS, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            held, now = os.fstat(fd), os.stat(directory / LOCK, follow_symlinks=False)
        except BaseException:
            os.close(fd)
            raise
    except FileNotFoundError:
        return False  # gone, or renamed away by a collection meanwhile
    except OSError:
        return directory.is_dir()  # it cannot be held here: used unheld
    if (now.st_dev, now.st_ino) != (held.st_dev, held.st_ino):
        os.close(fd)
        return False  # collected while the lock was waited for
    if not 0 <= time.time() - held.st_mtime < gc.DAY:  # a day old, or dated ahead
        with contextlib.suppress(OSError):
            os.utime(fd)
    if _HELD.setdefault(directory, fd) != fd:
        os.close(fd)  # another thread took it meanwhile
    return True


def _mark(root: Path) -> None:
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        gc.mark(fd)
    finally:
        os.close(fd)
