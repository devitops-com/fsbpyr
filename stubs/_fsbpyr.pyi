"""Type stub for the ``_fsbpyr`` built-in module implemented in launcher/module.c."""

from typing import TypedDict

NONE: int

class PayloadError(ValueError): ...

class Info(TypedDict):
    executable: str
    app_name: str
    entrypoint: str
    build_info: str
    generation_id: bytes
    python_hexversion: int
    payload_size: int
    modules: int
    files: int

class Stats(TypedDict):
    reads: int
    stored_bytes: int
    decompressed_bytes: int

def find_module(name: str, /) -> tuple[int, int, int, int, int, int, int, str] | None: ...
def find_file(path: str, /) -> tuple[int, int, int, int, int, int, int] | None: ...
def read(blob: int, /) -> bytes: ...
def info() -> Info: ...
def stats() -> Stats: ...
