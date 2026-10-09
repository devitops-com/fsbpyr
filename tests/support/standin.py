"""Pure-Python stand-in for the ``_fsbpyr`` built-in module, over the validating reader.

Lets the boot code run in-process (with coverage) on any assembled executable. Behaviour must
match src/fsbpyr/launcher/module.c; tests/support/contract.py checks that it does.
"""

import errno
import os
import stat
import types
from pathlib import Path
from typing import Any

from fsbpyr.payload.format import MAX_MEMORY_BLOB, NONE, tree_digest
from fsbpyr.payload.format import PayloadError as _ReaderError
from fsbpyr.payload.reader import Executable


class PayloadError(ValueError):
    """Mirror of _fsbpyr.PayloadError."""


def _key(value: object) -> str | None:
    if not isinstance(value, str):
        raise TypeError("expected str")
    try:
        value.encode()
    except UnicodeEncodeError:
        return None
    return value


def _index(value: object, what: str) -> int:
    """Mirror of index_arg(): TypeError / OverflowError like PyLong_AsUnsignedLong."""
    if not isinstance(value, int):
        raise TypeError("expected int")
    if value < 0 or value >= 2**64:
        raise OverflowError(f"{what} index out of range")
    if value > 0xFFFFFFFF:
        raise PayloadError(f"corrupt executable payload: {what} {value}")
    return value


class StandIn(types.ModuleType):
    NONE = NONE
    PayloadError = PayloadError

    def __init__(self, executable: Path) -> None:
        super().__init__("_fsbpyr")
        self._path = os.fsdecode(executable)
        self._exe = Executable.open(executable)
        self._payload = self._exe.payload

    def close(self) -> None:
        self._exe.close()

    def find_module(self, name: object) -> tuple[Any, ...] | None:
        key = _key(name)
        record = None if key is None else self._payload.find_module(key)
        if record is None:
            return None
        return (record.index, int(record.kind), int(record.flags), record.code, record.source,
                record.file, record.dist, record.path)  # fmt: skip

    def find_file(self, path: object) -> tuple[int, ...] | None:
        key = _key(path)
        record = None if key is None else self._payload.find_file(key)
        if record is None:
            return None
        return (record.index, int(record.kind), int(record.flags), record.mode, record.blob,
                record.dist, record.closure)  # fmt: skip

    def file_at(self, index: object) -> tuple[Any, ...]:
        try:
            record = self._payload.files()[_index(index, "file")]
        except IndexError:
            raise PayloadError(f"corrupt executable payload: file {index}") from None
        return (record.index, int(record.kind), int(record.flags), record.mode, record.blob,
                record.dist, record.closure, record.path)  # fmt: skip

    def closure(self, word: object) -> tuple[int, ...]:
        try:
            return self._payload.closure(_index(word, "closure"))
        except _ReaderError:
            raise PayloadError(f"corrupt executable payload: closure {word}") from None

    def blob_info(self, blob: object) -> tuple[int, bytes | None]:
        try:
            record = self._payload.blob(_index(blob, "blob"))
            digest = None if record.digest == NONE else self._payload.digest(record.digest)
            return record.size, digest
        except _ReaderError:
            raise PayloadError(f"corrupt executable payload: blob {blob}") from None

    def read(self, blob: object) -> bytes:
        index = _index(blob, "blob")
        try:
            record = self._payload.blob(index)
            if record.size > MAX_MEMORY_BLOB:
                raise PayloadError(f"corrupt executable payload: blob too large {index}")
            return self._payload.read_blob(index)
        except _ReaderError as exc:
            raise PayloadError(f"corrupt executable payload: {exc}") from None

    def list_dir(self, path: object) -> list[str]:
        key = _key(path)
        return [] if key is None else list(self._payload.list_dir(key))

    def is_dir(self, path: object) -> bool:
        key = _key(path)
        return False if key is None else self._payload.is_dir(key)

    def list_modules(self, prefix: object) -> list[tuple[str, int]]:
        key = _key(prefix)
        if key is None:
            return []
        return [(name, int(kind)) for name, kind in self._payload.list_modules(key)]

    def find_dist(self, name: object) -> tuple[int, str, str, str] | None:
        key = _key(name)
        record = None if key is None else self._payload.find_dist(key)
        return None if record is None else (record.index, record.name, record.dist_info,
                                            record.version)  # fmt: skip

    def dists(self) -> list[tuple[int, str, str, str]]:
        return [(d.index, d.name, d.dist_info, d.version) for d in self._payload.dists()]

    def info(self) -> dict[str, Any]:
        payload = self._payload
        return {
            "executable": self._path,
            "app_name": payload.app_name,
            "entrypoint": payload.entrypoint,
            "build_info": payload.build_info,
            "generation_id": payload.generation_id,
            "python_hexversion": payload.python_hexversion,
            "payload_size": self._exe.payload_size,
            "modules": len(payload.modules()),
            "files": len(payload.files()),
        }

    def stats(self) -> dict[str, int]:
        return {"reads": 0, "stored_bytes": 0, "decompressed_bytes": 0, "integrity_ns": 0}

    def tree_digest(self, data: Any) -> bytes:
        return tree_digest(data)

    def file_digest(self, fd: Any, size: int) -> bytes | None:
        """Mirror of file_digest(): PyObject_AsFileDescriptor, fstat, regular files only, None
        unless the file has ``size`` bytes."""
        if not isinstance(fd, int) and not hasattr(fd, "fileno"):
            raise TypeError("argument must be an int, or have a fileno() method.")
        number = fd if isinstance(fd, int) else fd.fileno()
        if not isinstance(number, int):
            raise TypeError("fileno() returned a non-integer")
        if number < 0:
            raise ValueError(f"file descriptor cannot be a negative integer ({number})")
        info = os.fstat(number)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))
        if info.st_size != size:
            return None
        data = bytearray()
        while len(data) < info.st_size:  # by offset, like the C module's mmap
            chunk = os.pread(number, info.st_size - len(data), len(data))
            if not chunk:
                break
            data += chunk
        return tree_digest(data)
