"""Pure-Python stand-in for the ``_fsbpyr`` built-in module, over the validating reader.

Lets the boot code run in-process (with coverage) on any assembled executable. Behaviour must
match src/fsbpyr/launcher/module.c; tests/support/contract.py checks that it does.
"""

from __future__ import annotations

import os
import types
from pathlib import Path
from typing import Any

from fsbpyr.payload.format import NONE
from fsbpyr.payload.format import PayloadError as _ReaderError
from fsbpyr.payload.reader import Executable

from . import cache


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
    CacheError = cache.CacheError

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
            return self._payload.read_blob(index)
        except _ReaderError as exc:
            raise PayloadError(f"corrupt executable payload: {exc}") from None

    def stream(self, blob: object) -> Stream:
        index = _index(blob, "blob")
        try:
            size = self._payload.blob(index).size
        except _ReaderError:
            raise PayloadError(f"corrupt executable payload: blob {index}") from None
        return Stream(self, index, size)

    def list_dir(self, path: object) -> list[str]:
        key = _key(path)
        return [] if key is None else list(self._payload.list_dir(key))

    def files_below(self, path: object) -> range:
        key = _key(path)
        if key is None:
            return range(0)
        prefix = f"{key}/" if key else ""
        below = [f.index for f in self._payload.files() if f.path.startswith(prefix)]
        return range(below[0], below[-1] + 1) if below else range(0)

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
            "execfn": None,
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

    def sync(self, root: Any, chain: Any, files: Any, checked: Any, resources: Any,
             written: Any = None) -> int:  # fmt: skip
        return cache.sync(self._payload, root, chain, files, checked, resources, written)


class Stream:
    """Mirror of _fsbpyr.Stream. The content is read whole here, at the first read: a damaged
    blob is refused there, where the launcher refuses it at the window that holds the damage
    (a zstd checksum: at the end). A failure is sticky, as the launcher's."""

    def __init__(self, module: StandIn, blob: int, size: int) -> None:
        self._module, self._blob, self.size = module, blob, size
        self._content: bytes | None = None
        self._position = 0
        self._closed = self._failed = False

    def _error(self) -> PayloadError:
        return PayloadError(f"corrupt executable payload: blob data {self._blob}")

    def _open(self) -> None:
        """module.c's stream_take(): closed, or failed before."""
        if self._closed:
            raise ValueError("I/O operation on closed stream")
        if self._failed:
            raise self._error()

    def readinto(self, buffer: Any) -> int:
        view = memoryview(buffer).cast("B")  # first, as PyObject_GetBuffer is
        if view.readonly:
            raise BufferError("Object is not writable.")
        self._open()
        if self._content is None and len(view) and self._position < self.size:
            try:
                self._content = self._module._payload.read_blob(self._blob)
            except _ReaderError:
                self._failed = True
                raise self._error() from None
        data = (self._content or b"")[self._position : self._position + len(view)]
        view[: len(data)] = data
        self._position += len(data)
        return len(data)

    def seek(self, position: int) -> int:
        if not isinstance(position, int):
            raise TypeError("expected int")
        if not 0 <= position < 2**64:  # PyLong_AsUnsignedLongLong
            raise OverflowError("int out of range of unsigned long long")
        self._open()
        if position > self.size:
            raise ValueError("position beyond the end of the stream")
        self._position = position
        return position

    def tell(self) -> int:
        return self._position

    def close(self) -> None:
        self._closed = True
