"""Contract battery for the ``_fsbpyr`` API (stdlib only).

The same function runs inside a built executable against the C module and in-process against
the pure-Python stand-in; both must produce identical results (plan Phase 3).
"""

import hashlib
import os
import tempfile
from typing import Any

NAMES = [
    "json", "json.decoder", "pkg", "pkg.cli", "pkg.data", "ns", "ns.a", "ns.b", "missing",
    "", "pkg.", ".pkg", "\ud800", "_fsbpyr_boot",
]  # fmt: skip
FILES = [
    "pkg/cli.py", "pkg/data/table.json", "pkg/data/sub/x.txt", "demo-1.0.dist-info/METADATA",
    "native.libs/_ext.so", "native.libs/libdep.so", "missing", "", "pkg", "pkg/data", "\ud800",
]  # fmt: skip
DIRS = [
    "", "pkg", "pkg/data", "pkg/data/sub", "ns", "missing", "pkg/cli.py", "\ud800", "pkg/",
    "pkg//data", "/pkg", "pk",
]  # fmt: skip
PREFIXES = ["pkg.", "ns.", "json.", "missing.", "pkg.data.", "\ud800."]
DISTS = ["demo", "missing", ""]
BAD_BLOBS: list[Any] = [-1, 2**32, 2**64, 10**9, "1"]
LEAF = 1 << 20  # the integrity digest's piece size: around it, the C and Python routines agree
DIGEST_SIZES = [0, 1, LEAF - 1, LEAF, LEAF + 1, 5 * LEAF + 3, 9 * LEAF + 1]  # 1 to 3 threads
NOT_BUFFERS: list[Any] = [None, "text", 1]
BAD_FDS: list[Any] = [-1, 10**6, "3", None]
NOT_STR: list[Any] = [None, 1, b"pkg"]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _outcome(call: Any, *args: Any) -> Any:
    try:
        return ["ok", _plain(call(*args))]
    except Exception as exc:
        return ["raises", type(exc).__name__]


def _plain(value: Any) -> Any:
    if isinstance(value, tuple | list):
        return [type(value).__name__, *(_plain(v) for v in value)]
    if isinstance(value, bytes):
        return _digest(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    return value


def run(fsb: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["modules"] = {repr(n): _outcome(fsb.find_module, n) for n in NAMES}
    out["files"] = {repr(p): _outcome(fsb.find_file, p) for p in FILES}
    out["dirs"] = {repr(d): _outcome(fsb.list_dir, d) for d in DIRS}
    out["is_dir"] = {repr(d): _outcome(fsb.is_dir, d) for d in DIRS}
    calls = (fsb.find_module, fsb.find_file, fsb.list_dir, fsb.is_dir, fsb.list_modules,
             fsb.find_dist)  # fmt: skip
    out["not_str"] = [[_outcome(call, arg) for arg in NOT_STR] for call in calls]
    out["children"] = {repr(p): _outcome(fsb.list_modules, p) for p in PREFIXES}
    top = fsb.list_modules("")
    out["top_level"] = [len(top), _digest(repr(sorted(top)).encode())]
    out["dists"] = {repr(d): _outcome(fsb.find_dist, d) for d in DISTS}
    out["all_dists"] = _plain(fsb.dists())
    blobs: set[int] = set()
    for name in NAMES:
        record = _outcome(fsb.find_module, name)
        if record[0] == "ok" and record[1] is not None:
            blobs.update(b for b in record[1][4:6] if b != fsb.NONE)
    for path in FILES:
        record = _outcome(fsb.find_file, path)
        if record[0] == "ok" and record[1] is not None:
            blobs.add(record[1][5])
    out["reads"] = {str(b): _outcome(fsb.read, b) for b in sorted(blobs)}
    out["bad_reads"] = {repr(b): _outcome(fsb.read, b) for b in BAD_BLOBS}
    out["blob_info"] = {str(b): _outcome(fsb.blob_info, b) for b in [*sorted(blobs), *BAD_BLOBS]}
    count = fsb.info()["files"]
    out["file_at"] = [_outcome(fsb.file_at, i) for i in [*range(count), count, *BAD_BLOBS]]
    words = sorted({f[6] for f in (fsb.file_at(i) for i in range(count)) if f[6] != fsb.NONE})
    out["closures"] = [_outcome(fsb.closure, w) for w in [*words, *range(1, 4), *BAD_BLOBS]]
    info = dict(fsb.info())
    info["generation_id"] = info["generation_id"].hex()
    out["info"] = info
    out.update(_digests(fsb))
    return out


def _digests(fsb: Any) -> dict[str, Any]:
    samples = {size: (bytes(range(251)) * (size // 251 + 1))[:size] for size in DIGEST_SIZES}
    out: dict[str, Any] = {
        "tree_digest": {str(n): _outcome(fsb.tree_digest, data) for n, data in samples.items()},
        "tree_digest_bad": [_outcome(fsb.tree_digest, arg) for arg in NOT_BUFFERS],
        "file_digest_bad": [_outcome(fsb.file_digest, fd, 0) for fd in BAD_FDS],
    }
    with tempfile.TemporaryDirectory() as directory:
        files, other_sizes = {}, {}
        for size, data in samples.items():
            path = os.path.join(directory, str(size))
            with open(path, "wb") as file:
                file.write(data)
            with open(path, "rb") as file:
                file.seek(size // 2)  # the position does not matter
                files[str(size)] = _outcome(fsb.file_digest, file.fileno(), size)
                other_sizes[str(size)] = _outcome(fsb.file_digest, file.fileno(), size + 1)
        out["file_digest"] = files
        out["file_digest_other_size"] = other_sizes  # None: not the file that was expected
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            out["file_digest_dir"] = _outcome(fsb.file_digest, descriptor, 0)
        finally:
            os.close(descriptor)
    out["same_digests"] = out["tree_digest"] == out["file_digest"]
    return out
