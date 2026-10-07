"""Pure-Python stand-in for the ``_fsbpyr`` built-in module, over the validating reader.

Lets the boot code run in-process (with coverage) on any assembled executable. Behaviour must
match src/fsbpyr/launcher/module.c; tests/support/contract.py checks that it does.
"""

import os
import types
from pathlib import Path
from typing import Any

from fsbpyr.payload.format import NONE
from fsbpyr.payload.format import PayloadError as _ReaderError
from fsbpyr.payload.reader import Executable

MAX_MEMORY_BLOB = 1 << 30


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

    def read(self, blob: object) -> bytes:
        if not isinstance(blob, int):
            raise TypeError("expected int")
        if blob < 0 or blob >= 2**64:
            raise OverflowError("blob index out of range")
        try:
            record = self._payload.blob(blob)
            if record.size > MAX_MEMORY_BLOB:
                raise PayloadError(f"corrupt executable payload: blob too large {blob}")
            return self._payload.read_blob(blob)
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
        return {"reads": 0, "stored_bytes": 0, "decompressed_bytes": 0}
