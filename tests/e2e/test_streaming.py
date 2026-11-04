"""Extraction and reads stream at run time, with no limit of ours (D38, Phase 8b): files larger
than the old 1 GiB cap extract, a window at a time, in far less resident memory than their size;
runs of zeros become holes; resources opened as files stream and seek; _fsbpyr.read() returns
what the app asked for, whatever its size. A raw file's windows are read, and every file's
leaves written, on the threads that hash them (D31, D39): each leaf lands where it belongs."""

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import DIGEST_LEAF, XZ_PIECE, FileKind, is_hole
from fsbpyr.payload.reader import Executable
from fsbpyr.payload.spec import Blob, File
from fsbpyr.runtime.registry import Runtime

from .conftest import app_content

pytestmark = pytest.mark.slow

ZEROS = 1536 << 20  # past the old 1 GiB cap; a few KiB as zstd
RAW = 600 << 20  # stored raw: read through the mapping, past the 256 MiB sweep (payload.c)
PIECES = 20 * XZ_PIECE  # an xz library of 20 pieces: two windows of up to 16
HOLES = 67 * DIGEST_LEAF + 1000  # raw, over a window: zeros between data, a short last leaf
# The others are zeros, holes in the executable and the cache; 64 to 66 in the second window,
# where the first had data (a hole leaves zeros in the stream's buffer, not what was there).
DATA_LEAVES = (0, 1, 2, *range(60, 64), 67)
PEAK = 512 << 20  # what extraction, or a stream, may add to the resident peak

_APP = """\
import hashlib, json, os, sys
import _fsbpyr
from importlib.resources import files
from _fsbpyr_rt.native import resource_cache

def peak():  # kB; reset by the caller
    with open("/proc/self/status") as status:
        return next(int(l.split()[1]) for l in status if l.startswith("VmHWM:")) << 10

def reset():
    with open("/proc/self/clear_refs", "w") as refs:
        refs.write("5")

def digest(read):
    hashed, size = hashlib.sha256(), 0
    while piece := read(1 << 20):
        hashed.update(piece)
        size += len(piece)
    return size, hashed.hexdigest()

def main():
    out, cache = {}, resource_cache()
    names = ("big/zeros.bin", "big/raw.bin", "big/libpieces.so", "big/holes.bin")
    indices = [_fsbpyr.find_file(name)[0] for name in names]
    reset()
    base = peak()
    out["written"] = cache.sync(indices, resources=True)
    out["extract peak"] = peak() - base
    out["hole bytes"] = _fsbpyr.stats()["hole_bytes"]
    for name in names:
        path = os.path.join(cache.site, name)
        with open(path, "rb") as file:
            out[name] = [*digest(file.read), os.stat(path).st_blocks * 512]
    reset()
    base = peak()
    with files("big").joinpath("zeros.bin").open("rb") as file:
        out["streamed"] = list(digest(file.read))
        file.seek(-5, os.SEEK_END)
        tail = file.read()
        file.seek(0)
        head = file.read(3)
        file.seek(10, os.SEEK_END)
        out["seeks"] = [tail.hex(), head.hex(), file.tell(), file.read().hex()]
    out["stream peak"] = peak() - base
    data = _fsbpyr.read(_fsbpyr.find_file("big/zeros.bin")[4])
    out["read"] = [len(data), data.count(0) == len(data)]
    print(json.dumps(out))
"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _holes() -> bytes:
    data, pattern = bytearray(HOLES), bytes(range(1, 256)) * (DIGEST_LEAF // 255 + 1)
    for leaf in DATA_LEAVES:
        start = leaf * DIGEST_LEAF
        end = min(start + DIGEST_LEAF, HOLES)
        data[start:end] = pattern[: end - start]
    return bytes(data)


@pytest.fixture(scope="module")
def big_app(runtime: Runtime, tmp_path_factory: pytest.TempPathFactory) -> Path:
    zeros, raw = bytes(ZEROS), (bytes(range(1, 256)) * (RAW // 255 + 1))[:RAW]
    library = b"".join(b"%08d " % i for i in range(PIECES // 9 + 1))[:PIECES]
    modules, files = app_content({"x.py": _APP, "big/__init__.py": ""})
    big = (File("big/zeros.bin", FileKind.RESOURCE, Blob.zstd(zeros)),
           File("big/raw.bin", FileKind.RESOURCE, Blob.raw(raw)),
           File("big/libpieces.so", FileKind.NATIVE_LIB, Blob.xz(library, digest=True),
                mode=0o755),
           File("big/holes.bin", FileKind.RESOURCE, Blob.raw(_holes(), digest=True)))  # fmt: skip
    assert big[2].blob.pieces and len(big[2].blob.pieces) == PIECES // XZ_PIECE
    app = tmp_path_factory.mktemp("big") / "app"
    built = app.with_name("built")
    build_executable(runtime, App("x", "x:main", modules=modules, files=(*files, *big)), built)
    _sparse_copy(built, app)
    return app


def _sparse_copy(source: Path, target: Path) -> None:
    """``source`` as ``target``, a hole wherever it has 64 KiB of zeros, written back. Every write
    is inside the file's end, so no file system preallocates around it (XFS did as the writer
    wrote the executable, and where it did, SEEK_DATA's answer depends on the page cache): the
    holes are the same whenever they are looked at (test_holes_in_the_executable_are_not_read)."""
    piece = 64 << 10
    with source.open("rb") as reader, target.open("wb") as writer:
        writer.truncate(source.stat().st_size)
        for offset in range(0, source.stat().st_size, piece):
            data = reader.read(piece)
            if data.count(0) != len(data):
                writer.seek(offset)
                writer.write(data)
        writer.flush()
        os.fsync(writer.fileno())
    target.chmod(0o755)


@pytest.fixture(scope="module")
def report(big_app: Path) -> dict[str, Any]:
    cache = big_app.parent / "cache"
    result = subprocess.run([big_app], capture_output=True, text=True, check=False,
                            env={**os.environ, "FSBPYR_CACHE_DIR": os.fspath(cache)})  # fmt: skip
    assert result.returncode == 0, result.stderr
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def test_files_past_the_old_cap_extract_in_bounded_memory(report: dict[str, Any]) -> None:
    raw = (bytes(range(1, 256)) * (RAW // 255 + 1))[:RAW]
    library = b"".join(b"%08d " % i for i in range(PIECES // 9 + 1))[:PIECES]

    assert report["written"] == 4
    assert report["big/zeros.bin"][:2] == [ZEROS, _sha256(bytes(ZEROS))]
    assert report["big/raw.bin"][:2] == [RAW, _sha256(raw)]
    assert report["big/libpieces.so"][:2] == [PIECES, _sha256(library)]
    assert report["extract peak"] < PEAK, report["extract peak"]


def test_runs_of_zeros_become_holes(report: dict[str, Any]) -> None:
    allocated = report["big/zeros.bin"][2]

    assert allocated < (1 << 20), f"{allocated} bytes allocated for {ZEROS} bytes of zeros"
    assert report["big/raw.bin"][2] >= RAW


def test_a_raw_file_read_and_written_on_many_threads_is_whole_with_its_holes(
    report: dict[str, Any],
) -> None:
    size, digest, allocated = report["big/holes.bin"]

    assert [size, digest] == [HOLES, _sha256(_holes())]
    assert allocated < 2 * len(DATA_LEAVES) * DIGEST_LEAF, f"{allocated} bytes allocated"


def _hole_bytes(app: Path, path: str) -> int:
    """The bytes of the raw blob of ``path`` whose leaves lie inside holes of ``app``."""
    with Executable.open(app) as executable:
        file = executable.payload.find_file(path)
        assert file is not None
        blob = executable.payload.blobs()[file.blob]
        start, size = executable.payload_offset + blob.offset, blob.size
    fd = os.open(app, os.O_RDONLY)
    try:
        return sum(min(DIGEST_LEAF, size - leaf) for leaf in range(0, size, DIGEST_LEAF)
                   if is_hole(fd, start + leaf, min(DIGEST_LEAF, size - leaf)))  # fmt: skip
    finally:
        os.close(fd)


def test_holes_in_the_executable_are_not_read(big_app: Path, report: dict[str, Any]) -> None:
    """holes.bin's leaves 3 to 59 and 64 to 66 are zeros, holes in the executable
    (_sparse_copy) but where a leaf shares 64 KiB with data (the blob is 16-byte aligned): those
    in holes are not read, and they are the only raw leaves in holes."""
    holes = _hole_bytes(big_app, "big/holes.bin")

    assert holes >= 56 * DIGEST_LEAF, holes  # 4 to 58 and 65 at least
    assert report["hole bytes"] == holes


def test_resources_opened_as_files_stream_and_seek(report: dict[str, Any]) -> None:
    assert report["streamed"] == [ZEROS, _sha256(bytes(ZEROS))]
    assert report["seeks"] == ["00" * 5, "00" * 3, ZEROS + 10, ""]
    assert report["stream peak"] < PEAK, report["stream peak"]


def test_read_gives_the_app_what_it_asked_for(report: dict[str, Any]) -> None:
    assert report["read"] == [ZEROS, True]
