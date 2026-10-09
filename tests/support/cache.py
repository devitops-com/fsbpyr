"""Pure-Python mirror of the launcher's cache sync (src/fsbpyr/launcher/cache.c, D35).

Used by the ``_fsbpyr`` stand-in, so the boot code's native cache runs in-process. Every step
follows cache.c: the same validation before anything is touched, the same directories opened in
the same order, the same checks of cached files, batches and outcomes; tests/support/contract.py
checks that both behave alike.
"""

import contextlib
import errno
import os
import stat
import sys
from collections.abc import Sequence
from typing import Any

from fsbpyr.payload.format import MAX_MEMORY_BLOB, NATIVE_KINDS, NONE, FileFlag, tree_digest
from fsbpyr.payload.format import PayloadError as _ReaderError
from fsbpyr.payload.reader import FileRecord, Payload


class CacheError(ImportError):
    """Mirror of _fsbpyr.CacheError."""


CHANGED, DONE, WRITTEN = 1, 2, 4  # outcome bits of a file
CHECK_BATCH, SMALL_FILE, BATCH_BUFFER, HELD_FILES = 1024, 64 << 10, 16 << 20, 64
WRITE_PIECE = 1 << 20
_PATH_SIZE = 4096  # cache.c: a directory path below the root, in bytes, is shorter
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


def sync(
    payload: Payload, root: Any, chain: Any, files: Any, checked: Any, resources: Any
) -> list[str]:
    """Mirror of _fsbpyr.sync(): argument checks in module.c's order, then fsb_cache_sync()."""
    root_path = os.fsencode(root)
    if b"\0" in root_path:
        raise ValueError("embedded null byte")
    if not isinstance(chain, str):
        raise TypeError("expected str")
    resources = bool(resources)
    chain.encode()
    if not safe(chain):
        raise ValueError(f"unsafe cache chain {chain!r}")
    memo = memoryview(checked)
    if memo.readonly:
        raise BufferError("Object is not writable.")
    records = payload.files()
    if memo.nbytes != len(records):
        raise ValueError("checked must hold one byte per file")
    todo = [index for index in _indices(files, len(records)) if not memo[index]]
    outcome = [0] * len(todo)
    try:
        _Sync(payload, root_path, chain, [records[i] for i in todo], outcome, resources).run()
    finally:
        written = []
        for item, record in enumerate(records[i] for i in todo):
            if outcome[item] & CHANGED:
                _report(f"fsbpyr: {os.fsdecode(root_path)}/{chain}/{record.path} was modified "
                        "after extraction; restoring it\n")  # fmt: skip
            if outcome[item] & DONE:
                memo[record.index] = 1
            if outcome[item] & WRITTEN:
                written.append(record.path)
    return written


def _indices(files: Any, count: int) -> list[int]:
    try:
        items = list(files)
    except TypeError:
        raise TypeError("expected a sequence of FILES indices") from None
    for item in items:
        if not isinstance(item, int):
            raise TypeError("an integer is required")
        if item < 0 or item >= 2**64:
            raise OverflowError("FILES index out of range")
        if item >= count:
            raise CacheError(f"corrupt executable payload: file {item}")
    return items


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
        self.root_fd = -1
        self.opened: list[tuple[str, int]] = []  # the directories below the root, in order
        self.batch: list[tuple[_Entry, bytes | int]] = []  # small files' content, or descriptors
        self.buffered = 0  # bytes of small files read into the batch
        self.held = 0  # descriptors held by the batch

    def run(self) -> None:
        try:
            entries = [self._prepare(item, record) for item, record in enumerate(self.records)]
            if not entries:
                return  # nothing to check: nothing touched
            self.root_fd = self._open_root()
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
            for _, fd in self.opened:
                os.close(fd)
            if self.root_fd >= 0:
                os.close(self.root_fd)

    def _corrupt(self, what: str, record: FileRecord) -> CacheError:
        return CacheError(f"corrupt executable payload: {what} {record.index}")

    def _prepare(self, item: int, record: FileRecord) -> _Entry:
        extractable = (self.resources or record.kind in NATIVE_KINDS
                       or FileFlag.FS_REQUIRED in record.flags)  # fmt: skip
        if not extractable or not safe(record.path):
            raise self._corrupt("extracted file", record)
        blob = self.payload.blob(record.blob)
        if blob.size > MAX_MEMORY_BLOB:
            raise self._corrupt("blob of file", record)
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
        try:
            fd = os.open(self.root, _DIRECTORY)
        except FileNotFoundError:
            self._make_root()
            fd = os.open(self.root, _DIRECTORY)
        return self._owned(fd, self._path(""), private=False)

    def _make_root(self) -> None:
        """mkdir -p: parents as the umask leaves them, the root itself 0700."""
        for position in [i for i in range(1, len(self.root)) if self.root[i] == ord("/")]:
            with contextlib.suppress(FileExistsError):
                os.mkdir(self.root[:position], 0o777)
        with contextlib.suppress(FileExistsError):
            os.mkdir(self.root, 0o700)

    def _parent(self, entry: _Entry) -> int:
        parent = entry.record.path.rpartition("/")[0]
        target = f"{self.chain}/{parent}" if parent else self.chain
        if len(target.encode()) >= _PATH_SIZE:
            path = self._path(f"{self.chain}/{entry.record.path}")
            raise OSError(errno.ENAMETOOLONG, os.strerror(errno.ENAMETOOLONG), path)
        return self._directory(target)

    def _directory(self, target: str) -> int:
        """The directory ``target`` below the root: what is open already stays open, the rest
        of the path is opened (and created) one component at a time."""
        parts = target.split("/")
        common = 0
        while (common < len(self.opened) and common < len(parts)
               and self.opened[common][0] == parts[common]):  # fmt: skip
            common += 1
        while len(self.opened) > common:
            os.close(self.opened.pop()[1])
        for part in parts[common:]:
            parent = self.opened[-1][1] if self.opened else self.root_fd
            path = self._path("/".join(parts[: len(self.opened) + 1]))
            self.opened.append((part, self._open_private(parent, part, path)))
        return self.opened[-1][1]

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
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
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
        data = self._content(entry.record)
        if tree_digest(data) != entry.digest:
            raise self._corrupt("digest of file", entry.record)
        directory = self._parent(entry)
        _publish(directory, entry.record.path.rpartition("/")[2], data, entry.record.mode)
        self.outcome[entry.item] |= DONE | WRITTEN


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


def _publish(directory: int, name: str, data: bytes, mode: int) -> None:
    for _ in range(_TEMPORARY_ATTEMPTS):
        _temporaries[0] += 1
        temporary = f".fsbpyr-{os.getpid()}-{_temporaries[0] - 1}"
        try:
            fd = os.open(temporary, _NEW_FILE, 0o600, dir_fd=directory)
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError(17, "no temporary name left", name)
    try:
        view = memoryview(data)
        while view:  # in pieces, as cache.c writes them
            view = view[os.write(fd, view[:WRITE_PIECE]) :]
        os.fchmod(fd, 0o400 | (mode & 0o100))  # readable by its owner, never writable
        os.rename(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary, dir_fd=directory)
        raise
    finally:
        os.close(fd)
