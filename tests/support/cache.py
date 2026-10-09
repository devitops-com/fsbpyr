"""Pure-Python mirror of the launcher's cache sync (src/fsbpyr/launcher/cache.c, D35).

Used by the ``_fsbpyr`` stand-in, so the boot code's native cache runs in-process. Every step
follows cache.c: the same validation before anything is touched, the same directories opened in
the same order, the same checks of cached files, batches and outcomes; tests/support/contract.py
checks that both behave alike.
"""

import contextlib
import errno
import fcntl
import os
import stat
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

from fsbpyr.boot._fsbpyr_rt.gc import MARKER, MARKER_TEXT, OURS, way_problem
from fsbpyr.payload.format import NATIVE_KINDS, NONE, FileFlag, tree_digest
from fsbpyr.payload.format import PayloadError as _ReaderError
from fsbpyr.payload.reader import FileRecord, Payload


class CacheError(ImportError):
    """Mirror of _fsbpyr.CacheError."""


CHANGED, DONE, WRITTEN = 1, 2, 4  # outcome bits of a file
CHECK_BATCH, SMALL_FILE, BATCH_BUFFER, HELD_FILES = 1024, 64 << 10, 16 << 20, 64
WRITE_PIECE = 1 << 20
SYNC_CHUNK = 4096  # module.c: files synced at once
OPEN_LEVELS = 16  # cache.c: directories kept open from the root down
LOCK, SCRATCH, DAY, HOLD_ATTEMPTS = ".lock", ".scratch", 86400, 8  # cache.c
_LOCK_FLAGS = os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOCTTY
_HELD: dict[bytes, tuple[int, int, int]] = {}  # module.c's held_trees: key -> fd, device, inode
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
_NEW_FILE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_PRESENT, _MISSING, _CHANGED = range(3)
_TEMPORARY_ATTEMPTS = 100
_temporaries = [0]


def safe(path: str) -> bool:
    """fsb_cache_safe(): the payload reader's check_path rule."""
    return (
        bool(path)
        and "\0" not in path
        and "\\" not in path
        and all(part not in ("", ".", "..") for part in path.split("/"))
    )


def sync(payload: Payload, root: Any, chain: Any, files: Any, checked: Any, resources: Any,
         written: Any = None) -> int:  # fmt: skip
    """Mirror of _fsbpyr.sync(): argument checks in module.c's order, then fsb_cache_sync() a
    chunk of SYNC_CHUNK files at a time; the number of files written."""
    root_path, resources, memo, items = _arguments(payload, root, chain, files, checked,
                                                   resources, written)  # fmt: skip
    records = payload.files()
    count, at = 0, 0
    key = root_path + b"\0" + chain.rpartition("/")[0].encode()  # module.c's held_trees
    while chunk := _chunk(items, at, memo, len(records)):
        todo, at = chunk
        outcome = [0] * len(todo)
        job = _Sync(payload, root_path, chain, [records[i] for i in todo], outcome, resources)
        job.hold = not _holds(key)
        try:
            job.run()
        finally:
            if job.lock >= 0:
                _keep_held(key, job.lock)
            for item, record in enumerate(records[i] for i in todo):
                if outcome[item] & CHANGED:
                    _report(f"fsbpyr: {os.fsdecode(root_path)}/{chain}/{record.path} was "
                            "modified after extraction; restoring it\n")  # fmt: skip
                if outcome[item] & DONE:
                    memo[record.index // 8] |= 1 << record.index % 8
                if outcome[item] & WRITTEN:
                    count += 1
                    if written is not None:
                        written.append(record.path)
    return count


def _arguments(payload: Payload, root: Any, chain: Any, files: Any, checked: Any, resources: Any,
               written: Any) -> tuple[bytes, bool, memoryview, Any]:  # fmt: skip
    """module.c's checks of sync()'s arguments, in its order."""
    root_path = os.fsencode(root)
    if b"\0" in root_path:
        raise ValueError("embedded null byte")
    if not isinstance(chain, str):
        raise TypeError("expected str")
    resources = bool(resources)
    chain.encode()
    if not safe(chain):
        raise ValueError(f"unsafe cache chain {chain!r}")
    if written is not None and not isinstance(written, list):
        raise TypeError("written must be a list or None")
    memo = memoryview(checked)
    if memo.readonly:
        raise BufferError("Object is not writable.")
    if memo.nbytes != (len(payload.files()) + 7) // 8:
        raise ValueError("checked must hold one bit per file")
    return root_path, resources, memo, _sequence(files)


def _sequence(files: Any) -> Any:
    """PySequence_Check(): read an item at a time; anything else is made a list."""
    if not isinstance(files, dict) and hasattr(type(files), "__getitem__"):
        return files
    try:
        return list(files)
    except TypeError:
        raise TypeError("expected a sequence of FILES indices") from None


def _chunk(items: Any, at: int, memo: memoryview, count: int) -> tuple[list[int], int] | None:
    """module.c's next_chunk(): up to SYNC_CHUNK indices from items[at] on that ``memo`` does
    not mark, and where the next chunk starts; None when the items are used up."""
    todo: list[int] = []
    while at < len(items) and len(todo) < SYNC_CHUNK:
        item = items[at]
        at += 1
        if not isinstance(item, int):
            raise TypeError("an integer is required")
        if item < 0 or item >= 2**64:
            raise OverflowError("FILES index out of range")
        if item >= count:
            raise CacheError(f"corrupt executable payload: file {item}")
        if not memo[item // 8] >> item % 8 & 1:
            todo.append(item)
    return (todo, at) if todo else None


def _report(text: str) -> None:
    """Mirror of PySys_FormatStderr(): sys.stderr, else the process's own stderr."""
    try:
        sys.stderr.write(text)
    except Exception:  # None, closed, ...: never stops the repair
        with contextlib.suppress(OSError):
            os.write(2, text.encode())


class _Entry:
    def __init__(self, item: int, record: FileRecord, size: int, digest: bytes) -> None:
        self.item, self.record, self.size, self.digest = item, record, size, digest


class _Sync:
    def __init__(self, payload: Payload, root: bytes, chain: str, records: Sequence[FileRecord],
                 outcome: list[int], resources: bool) -> None:  # fmt: skip
        self.payload, self.root, self.chain = payload, root, chain
        self.records, self.outcome, self.resources = records, outcome, resources
        self.uid = os.geteuid()
        self.root_fd = self.scratch_fd = -1
        self.hold, self.lock = False, -1  # take the tree once the records are checked (D38)
        self.levels: list[list[Any]] = []  # [name, descriptor or -1] from the root down
        self.batch: list[tuple[_Entry, bytes | int]] = []  # small files' content, or descriptors
        self.buffered = 0  # bytes of small files read into the batch
        self.held = 0  # descriptors held by the batch

    def run(self) -> None:
        try:
            entries = [self._prepare(item, record) for item, record in enumerate(self.records)]
            if not entries:
                return  # nothing to check: nothing touched
            self.root_fd = self._open_root()
            self.lock = self._take() if self.hold else -1
            for entry in entries:
                if entry.size <= SMALL_FILE and self.buffered + entry.size > BATCH_BUFFER:
                    self._check()
                state, content = self._loaded(entry)
                if state == _PRESENT:
                    assert content is not None
                    self.batch.append((entry, content))
                    self.held += isinstance(content, int)
                    if len(self.batch) == CHECK_BATCH or self.held == HELD_FILES:
                        self._check()
                else:
                    if state == _CHANGED:
                        self.outcome[entry.item] |= CHANGED
                    self._restore(entry)
            self._check()
        finally:
            _close(self.batch)
            for _, fd in self.levels:
                if fd >= 0:
                    os.close(fd)
            if self.root_fd >= 0:
                os.close(self.root_fd)
            if self.scratch_fd >= 0:
                os.close(self.scratch_fd)

    def _corrupt(self, what: str, record: FileRecord) -> CacheError:
        return CacheError(f"corrupt executable payload: {what} {record.index}")

    def _prepare(self, item: int, record: FileRecord) -> _Entry:
        extractable = (self.resources or record.kind in NATIVE_KINDS
                       or FileFlag.FS_REQUIRED in record.flags)  # fmt: skip
        if not extractable or not safe(record.path):
            raise self._corrupt("extracted file", record)
        blob = self.payload.blob(record.blob)
        if blob.digest != NONE:
            return _Entry(item, record, blob.size, self.payload.digest(blob.digest))
        if not self.resources:
            raise self._corrupt("extracted file", record)
        return _Entry(item, record, blob.size, tree_digest(self._content(record)))

    def _content(self, record: FileRecord) -> bytes:
        try:
            return self.payload.read_blob(record.blob)
        except _ReaderError:
            raise self._corrupt("blob data of file", record) from None

    def _path(self, relative: str) -> str:
        return f"{os.fsdecode(self.root)}/{relative}" if relative else os.fsdecode(self.root)

    def _owned(self, fd: int, path: str, *, private: bool) -> int:
        try:
            info = os.fstat(fd)
            if info.st_uid != self.uid:
                raise CacheError(f"refusing cache directory {path}: owned by another user")
            if private and stat.S_IMODE(info.st_mode) & 0o077:
                os.fchmod(fd, 0o700)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _open_root(self) -> int:
        """cache.c's open_root(): the root's way from "/" trusted (gc.way_problem is
        trusted_way's rule), then the root opened, created if missing, and ours."""
        self._trusted_way()
        try:
            fd = os.open(self.root, _DIRECTORY)
        except FileNotFoundError:
            self._make_root()
            self._trusted_way()  # again: a part someone else made meanwhile is caught
            fd = os.open(self.root, _DIRECTORY)
        return self._owned(fd, self._path(""), private=False)

    def _trusted_way(self) -> None:
        if (problem := way_problem(os.fsdecode(self.root))) is not None:
            if problem.error:
                raise OSError(problem.error, os.strerror(problem.error), problem.where)
            raise CacheError(problem.text())

    def _make_root(self) -> None:
        """mkdir -p: parents 0755 as far as the umask leaves it, the root itself 0700."""
        for position in [i for i in range(1, len(self.root)) if self.root[i] == ord("/")]:
            with contextlib.suppress(FileExistsError):
                os.mkdir(self.root[:position], 0o755)
        with contextlib.suppress(FileExistsError):
            os.mkdir(self.root, 0o700)

    def _walk(self, target: str) -> int:
        """cache.c's walk(): ``target`` opened one component at a time apart from the levels;
        a descriptor of the caller's."""
        fd = self.root_fd
        for depth, part in enumerate(target.split("/") if target else []):
            path = self._path("/".join(target.split("/")[: depth + 1]))
            try:
                following = self._open_private(fd, part, path)
            finally:
                if fd != self.root_fd:
                    os.close(fd)
            fd = following
        return os.dup(fd) if fd == self.root_fd else fd

    def _scratch(self) -> int:
        """cache.c's open_scratch(): <the chain's parent>/.scratch, once a file is written."""
        if self.scratch_fd < 0:
            parent = self.chain.rpartition("/")[0]
            self.scratch_fd = self._walk(f"{parent}/{SCRATCH}" if parent else SCRATCH)
        return self.scratch_fd

    def _take(self) -> int:
        """cache.c's take(): the root marked, then the tree's .lock held LOCK_SH, taken again if a
        collector renamed the tree away meanwhile, touched once a day old; best effort: -1 if
        it cannot be held (the sync meets any failure itself)."""
        _mark(self.root_fd)
        tree = self.chain.rpartition("/")[0]
        for _ in range(HOLD_ATTEMPTS):
            try:
                directory = self._walk(tree)
            except OSError, CacheError:
                return -1
            try:
                fd = os.open(LOCK, _LOCK_FLAGS, 0o600, dir_fd=directory)
            except FileNotFoundError:
                continue  # the tree removed meanwhile: made afresh
            except OSError:
                return -1
            finally:
                os.close(directory)
            held = os.fstat(fd)
            if not (stat.S_ISREG(held.st_mode) and held.st_uid == self.uid):
                os.close(fd)
                return -1
            try:
                fcntl.flock(fd, fcntl.LOCK_SH)
            except OSError:
                os.close(fd)
                return -1
            if self._still_the_lock(fd, tree):
                if time.time() - held.st_mtime >= DAY:
                    with contextlib.suppress(OSError):
                        os.utime(fd)
                return fd
            os.close(fd)
        return -1

    def _still_the_lock(self, fd: int, tree: str) -> bool:
        try:
            directory = self._walk(tree)
        except OSError, CacheError:
            return False
        try:
            now = os.stat(LOCK, dir_fd=directory, follow_symlinks=False)
        except OSError:
            return False
        finally:
            os.close(directory)
        held = os.fstat(fd)
        return (now.st_dev, now.st_ino) == (held.st_dev, held.st_ino)

    def _parent(self, entry: _Entry) -> int:
        parent = entry.record.path.rpartition("/")[0]
        return self._directory(f"{self.chain}/{parent}" if parent else self.chain)

    def _level_path(self, level: int) -> str:
        return self._path("/".join(name for name, _ in self.levels[: level + 1]))

    def _level_fd(self, level: int) -> int:
        """cache.c's level_fd(): open, or opened again down from the deepest open level above;
        levels deeper than OPEN_LEVELS opened on the way are closed again."""
        start = level + 1
        while start > 0 and self.levels[start - 1][1] < 0:
            start -= 1
        parent = self.levels[start - 1][1] if start else self.root_fd
        for i in range(start, level + 1):
            try:
                fd = self._open_private(parent, self.levels[i][0], self._level_path(i))
            finally:
                if i > start and i - 1 >= OPEN_LEVELS:
                    os.close(parent)
                    self.levels[i - 1][1] = -1
            self.levels[i][1] = parent = fd
        return int(self.levels[level][1])

    def _directory(self, target: str) -> int:
        """cache.c's open_directory(): the levels shared with the last directory stay, the rest
        of the path is opened (and created) one component at a time; only OPEN_LEVELS levels
        and the deepest stay open."""
        parts = target.split("/")
        common = 0
        while (common < len(self.levels) and common < len(parts)
               and self.levels[common][0] == parts[common]):  # fmt: skip
            common += 1
        while len(self.levels) > common:
            fd = self.levels.pop()[1]
            if fd >= 0:
                os.close(fd)
        parent = self._level_fd(common - 1) if common else self.root_fd
        for part in parts[common:]:
            depth = len(self.levels)
            fd = self._open_private(parent, part, self._path("/".join(parts[: depth + 1])))
            if depth > OPEN_LEVELS and self.levels[depth - 1][1] >= 0:  # only the deepest
                os.close(self.levels[depth - 1][1])
                self.levels[depth - 1][1] = -1
            self.levels.append([part, fd])
            parent = fd
        return parent

    def _open_private(self, parent: int, name: str, path: str) -> int:
        flags = _DIRECTORY | os.O_NOFOLLOW
        try:
            fd = os.open(name, flags, dir_fd=parent)
        except FileNotFoundError:
            with contextlib.suppress(FileExistsError):
                os.mkdir(name, 0o700, dir_fd=parent)
            fd = os.open(name, flags, dir_fd=parent)
        return self._owned(fd, path, private=True)

    def _load(self, directory: int, name: str, size: int) -> tuple[int, bytes | int | None]:
        """cache.c's load_cached(): a small file's content, or a larger one's descriptor."""
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOCTTY
        try:
            fd = os.open(name, flags, dir_fd=directory)
        except FileNotFoundError:
            return _MISSING, None
        except OSError as exc:  # os.open() retries EINTR itself, as cache.c does
            if exc.errno in (errno.EMFILE, errno.ENFILE, errno.ENOMEM):
                raise _NoRoom(exc.errno) from exc
            if exc.errno == errno.ENAMETOOLONG:
                raise
            return _CHANGED, None
        try:
            info = os.fstat(fd)
            ours = stat.S_ISREG(info.st_mode) and info.st_uid == self.uid and info.st_size == size
        except OSError:
            ours = False
        if ours and size > SMALL_FILE:
            return _PRESENT, fd  # read as the pass is hashed
        try:
            data = self._read(fd, size) if ours else None
        finally:
            os.close(fd)
        return (_CHANGED, None) if data is None else (_PRESENT, data)

    def _room_made(self, exc: OSError) -> bool:
        """cache.c's room_made(): out of descriptors or memory while the batch holds files, the
        batch is checked (its files closed) and the step tried once more."""
        if exc.errno not in (errno.EMFILE, errno.ENFILE, errno.ENOMEM) or not self.batch:
            return False
        self._check()
        return True

    def _loaded(self, entry: _Entry) -> tuple[int, bytes | int | None]:
        """_load() in the file's directory, both tried once more after _room_made()."""
        name = entry.record.path.rpartition("/")[2]
        try:
            directory = self._parent(entry)
        except OSError as exc:
            if not self._room_made(exc):
                raise
            directory = self._parent(entry)
        try:
            return self._load(directory, name, entry.size)
        except _NoRoom as exc:
            error = OSError(exc.errno, os.strerror(exc.errno), name)
        if not self._room_made(error):
            raise error
        try:
            return self._load(self._parent(entry), name, entry.size)
        except _NoRoom as exc:
            raise OSError(exc.errno, os.strerror(exc.errno), name) from None

    def _read(self, fd: int, size: int) -> bytes | None:
        """A small file's content, into the batch's buffer."""
        data = b""
        try:
            while len(data) < size and (more := os.read(fd, size - len(data))):
                data += more
        except OSError:
            return None
        if len(data) != size:
            return None
        self.buffered += size
        return data

    def _check(self) -> None:
        batch, self.batch, self.buffered, self.held = self.batch, [], 0, 0
        try:
            digests = [tree_digest(c) if isinstance(c, bytes) else _file_digest(c, entry.size)
                       for entry, c in batch]  # fmt: skip
        finally:
            _close(batch)
        for (entry, _), digest in zip(batch, digests, strict=True):
            if digest == entry.digest:
                self.outcome[entry.item] |= DONE
            else:
                self.outcome[entry.item] |= CHANGED
                self._restore(entry)

    def _restore(self, entry: _Entry) -> None:
        """cache.c's restore(): the directory, a temporary file, the content written and hashed,
        then the digest checked before the file is renamed into place."""
        directory = self._parent(entry)
        _publish(directory, self._scratch(), entry.record.path.rpartition("/")[2],
                 entry.record.mode, lambda: self._checked_content(entry))  # fmt: skip
        self.outcome[entry.item] |= DONE | WRITTEN

    def _checked_content(self, entry: _Entry) -> bytes:
        data = self._content(entry.record)
        if tree_digest(data) != entry.digest:
            raise self._corrupt("digest of file", entry.record)
        return data


class _NoRoom(Exception):
    """No descriptor or memory left to open a file (cache.c's NO_ROOM)."""

    def __init__(self, error: int) -> None:
        super().__init__(error)
        self.errno = error


def _file_digest(fd: int, size: int) -> bytes | None:
    """The digest of the file open as ``fd``; None if it cannot be read whole (cache.c gives a
    digest that cannot match)."""
    data = bytearray()
    try:
        while len(data) < size and (more := os.pread(fd, size - len(data), len(data))):
            data += more
    except OSError:
        return None
    return tree_digest(data) if len(data) == size else None


def _close(batch: list[tuple[_Entry, bytes | int]]) -> None:
    for _, content in batch:
        if isinstance(content, int):
            os.close(content)


def _publish(directory: int, scratch: int, name: str, mode: int,
             content: Callable[[], bytes]) -> None:  # fmt: skip
    for _ in range(_TEMPORARY_ATTEMPTS):
        _temporaries[0] += 1
        temporary = f".fsbpyr-{os.getpid()}-{_temporaries[0] - 1}"
        try:
            fd = os.open(temporary, _NEW_FILE, 0o600, dir_fd=scratch)
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError(17, "no temporary name left", name)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # a new file: nobody else holds it
    try:
        view = memoryview(content())
        while view:  # in pieces, as cache.c writes them (it leaves pieces of zeros as holes)
            view = view[os.write(fd, view[:WRITE_PIECE]) :]
        os.fchmod(fd, 0o400 | (mode & 0o100))  # readable by its owner, never writable
        os.rename(temporary, name, src_dir_fd=scratch, dst_dir_fd=directory)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary, dir_fd=scratch)
        raise
    finally:
        os.close(fd)


def _holds(key: bytes) -> bool:
    """module.c's holds(): an entry whose lock the app closed is dropped."""
    if key not in _HELD:
        return False
    fd, device, inode = _HELD[key]
    try:
        info = os.fstat(fd)
    except OSError:
        info = None
    if info is not None and (info.st_dev, info.st_ino) == (device, inode):
        return True
    del _HELD[key]
    return False


def _keep_held(key: bytes, fd: int) -> None:
    if _holds(key):
        os.close(fd)
        return
    info = os.fstat(fd)
    _HELD[key] = (fd, info.st_dev, info.st_ino)


def take(root: bytes, chain: str) -> int:
    """cache.c's take() for the tree <root>/<the chain's parent>, without a sync (tests)."""
    sync = _Sync(None, root, chain, [], [], False)  # type: ignore[arg-type]  # no payload used
    sync.root_fd = sync._open_root()
    try:
        return sync._take()
    finally:
        os.close(sync.root_fd)


def _mark(root: int) -> None:
    """cache.c's mark(): the marker written if it is missing and the root holds nothing but our
    names and is new (empty) or holds a tree of ours (its lock or its site); best effort."""
    try:
        os.stat(MARKER, dir_fd=root, follow_symlinks=False)
        return
    except FileNotFoundError:
        pass
    except OSError:
        return
    names = os.listdir(root)
    if any(name not in OURS for name in names):
        return
    if names and not any(_holds_a_tree(root, kind) for kind in ("runtime", "resources")):
        return
    try:
        fd = os.open(MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=root)  # fmt: skip
    except OSError:
        return
    try:
        with contextlib.suppress(OSError):
            os.write(fd, MARKER_TEXT)
    finally:
        os.close(fd)


def _holds_a_tree(root: int, kind: str) -> bool:
    """cache.c's holds_a_tree(): an entry of ``kind`` with its lock or its site."""
    try:
        fd = os.open(kind, _DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
    except OSError:
        return False
    try:
        for name in os.listdir(fd):
            for inside in (LOCK, "site"):
                with contextlib.suppress(OSError):
                    if not name.startswith("."):
                        os.stat(f"{name}/{inside}", dir_fd=fd, follow_symlinks=False)
                        return True
        return False
    finally:
        os.close(fd)
