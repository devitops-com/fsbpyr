"""Contract battery for the ``_fsbpyr`` API (stdlib only).

The same function runs inside a built executable against the C module and in-process against
the pure-Python stand-in; both must produce identical results (plan Phase 3).
"""

import errno
import hashlib
import io
import os
import stat
import sys
import tempfile
from typing import Any

NAMES = [
    "json", "json.decoder", "pkg", "pkg.cli", "pkg.data", "ns", "ns.a", "ns.b", "missing",
    "", "pkg.", ".pkg", "\ud800", "_fsbpyr_boot",
]  # fmt: skip
FILES = [
    "pkg/cli.py", "pkg/data/table.json", "pkg/data/sub/x.txt", "demo-1.0.dist-info/METADATA",
    "native.libs/_ext.so", "native.libs/libdep.so", "missing", "", "pkg", "pkg/data", "\ud800",
    # xz streams that do not match their record: both readers must refuse them (D33)
    "native.libs/libtrailing.so", "native.libs/liblonger.so", "native.libs/libtruncated.so",
    "native.libs/libpieces.so", "native.libs/libbadpiece.so",
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
NOT_STR: list[Any] = [None, 1, b"pkg"]
# The cache sync (D35), below a temporary cache root as below runtime/<generation>/site
CHAIN = "runtime/g/site"
EXT, DEP = "native.libs/_ext.so", "native.libs/libdep.so"
BROKEN = ["native.libs/libtrailing.so", "native.libs/libbadpiece.so",
          "native.libs/libwrongdigest.so", "pkg/cli.py"]  # fmt: skip
DAMAGES = ["same size", "missing", "longer", "empty", "symlink", "unreadable", "fifo",
           "directory"]  # fmt: skip


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _outcome(call: Any, *args: Any) -> Any:
    try:
        return ["ok", _plain(call(*args))]
    except Exception as exc:
        return ["raises", type(exc).__name__]


def _plain(value: Any) -> Any:
    if isinstance(value, range):
        return ["range", value.start, value.stop, value.step]
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
    out["below"] = {repr(d): _outcome(fsb.files_below, d) for d in DIRS}
    calls = (fsb.find_module, fsb.find_file, fsb.list_dir, fsb.is_dir, fsb.list_modules,
             fsb.find_dist, fsb.files_below)  # fmt: skip
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
    out["streams"] = _streams(fsb, sorted(blobs))
    out["sync"] = _syncs(fsb)
    return out


def _streams(fsb: Any, blobs: list[int]) -> dict[str, Any]:
    """Blobs read through _fsbpyr.stream (D38): in odd pieces, then around after seeks back,
    forward and to the end; a damaged blob refuses where it is read."""
    natives = [fsb.find_file(path)[4] for path in [*BROKEN[:3], "native.libs/libpieces.so"]]
    out = {str(blob): _stream_steps(fsb, blob) for blob in [*blobs, *natives]}
    out["bad"] = [_outcome(fsb.stream, blob) for blob in BAD_BLOBS]
    return out


def _stream_steps(fsb: Any, blob: int) -> list[Any]:
    steps: list[Any] = []
    try:
        stream = fsb.stream(blob)
    except Exception as exc:
        return [["raises", type(exc).__name__]]
    piece = bytearray(65537)
    try:
        size, data = stream.size, bytearray()
        while count := stream.readinto(piece):
            data += piece[:count]
        steps.append(["read", size, len(data), _digest(bytes(data))])
        for position in (0, size // 2, size, 1, size // 3):
            steps.append(["seek", stream.seek(position), stream.tell()])
            count = stream.readinto(piece)
            steps.append(["readinto", count, _digest(bytes(piece[:count])), stream.tell()])
        steps.append(_outcome(stream.seek, size + 1))
        steps.append(_outcome(stream.seek, 2**64))
        steps.append(_outcome(stream.readinto, b"read-only"))
    except Exception as exc:  # the contract compares where and how a stream refuses
        steps.append(["raises", type(exc).__name__])
        # a failure sticks: every call after fails alike (D38), wherever it would go
        steps.append([_outcome(stream.readinto, piece), _outcome(stream.seek, 0),
                      _outcome(stream.tell)])  # fmt: skip
    stream.close()
    steps.append([_outcome(stream.readinto, piece), _outcome(stream.seek, 0)])  # closed first
    return steps


def _syncs(fsb: Any) -> dict[str, Any]:
    """The cache sync (D35): what it writes, checks, repairs and refuses."""
    with tempfile.TemporaryDirectory() as tmp:
        descriptors = _descriptors()
        out: dict[str, Any] = {
            "arguments": _sync_arguments(fsb, tmp),
            "natives": _sync_natives(fsb, tmp),
            "damaged": _sync_damaged(fsb, tmp),
            "directories": _sync_directories(fsb, tmp),
            "payload": _sync_payload(fsb, tmp),
            "resources": _sync_resources(fsb, tmp),
            "digests": _sync_digests(fsb, tmp),
            "batches": _sync_batches(fsb, tmp),
            "threads": _sync_threads(fsb, tmp),
            "no stderr": _sync_without_stderr(fsb, tmp),
            "name too long": _sync(fsb, os.path.join(tmp, "toolong"), fsb.files_below("toolong")),
            "limited": _sync_limited(fsb, tmp),
            "chunks": _sync_chunks(fsb, tmp),
            "deep": _sync_deep(fsb, tmp),
        }
        out["descriptors left open"] = _descriptors() - descriptors
    return out


def _sync(fsb: Any, root: Any, files: Any, memo: Any = None, **options: Any) -> list[Any]:
    """Outcome of one sync (the number of files written and their paths) and what it reported
    on stderr, ``root`` written as <root>. Messages of cache errors count; of other errors only
    the class (and errno)."""
    memo = bytearray((fsb.info()["files"] + 7) // 8) if memo is None else memo
    written, chain = options.get("written", []), options.get("chain", CHAIN)
    args = (root, chain, files, memo, options.get("resources", False), written)
    saved, sys.stderr = sys.stderr, io.StringIO()
    try:
        outcome = ["ok", fsb.sync(*args), written]
    except OSError as exc:
        outcome = ["raises", type(exc).__name__, errno.errorcode.get(exc.errno or 0)]
    except ImportError as exc:  # CacheError
        outcome = ["raises", type(exc).__name__, str(exc)]
    except Exception as exc:
        outcome = ["raises", type(exc).__name__]
    finally:
        reported, sys.stderr = sys.stderr.getvalue(), saved
    if isinstance(root, str) and root:
        outcome = [v.replace(root, "<root>") if isinstance(v, str) else v for v in outcome]
        reported = reported.replace(root, "<root>")
    return [outcome, reported]


def _bits(memo: bytearray, count: int) -> list[int]:
    return [i for i in range(count) if memo[i // 8] >> i % 8 & 1]


def _sync_chunks(fsb: Any, tmp: str) -> list[Any]:
    """A sync of more files than a chunk (module.c's SYNC_CHUNK, 4096): the first chunk is
    written before a file of the second fails the sync (a name too long for any file system),
    and a sync again finds the done ones marked (D38)."""
    root, files, count = os.path.join(tmp, "chunks"), fsb.files_below("chunks"), fsb.info()["files"]
    memo, written = bytearray((count + 7) // 8), list[str]()
    first = _sync(fsb, root, files, memo, resources=True, written=written)
    marked = _bits(memo, count)
    again = _sync(fsb, root, files, memo, resources=True)
    return [first[0][:2], len(written), written[0], written[-1], len(files), len(marked),
            marked == list(files)[: len(marked)], again]  # fmt: skip


def _sync_deep(fsb: Any, tmp: str) -> list[Any]:
    """A file 40 directories deep, extracted with 24 descriptors to spare: at most OPEN_LEVELS
    of them stay open (cache.c), so depth costs no descriptors (D38)."""
    import resource  # noqa: PLC0415

    root, files = os.path.join(tmp, "deep"), fsb.files_below("deep")
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (_limit_leaving(24), hard))
    try:
        first = _sync(fsb, root, files, resources=True)
        again = _sync(fsb, root, files, resources=True)  # a new process: checked, not written
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    return [first, again, _tree(os.path.join(_site(root), "deep"))]


def _descriptors() -> int:
    return len(os.listdir("/proc/self/fd"))


def _closure(fsb: Any, path: str) -> tuple[int, ...]:
    record = fsb.find_file(path)
    return (record[0], *fsb.closure(record[6]))


def _site(root: str) -> str:
    return os.path.join(root, *CHAIN.split("/"))


def _tree(root: str) -> dict[str, list[str]]:
    """Everything below ``root``: its mode, and the digest of each readable file."""
    found: dict[str, list[str]] = {}
    for directory, names, files in os.walk(root):
        for name in sorted(names + files):
            path = os.path.join(directory, name)
            info = os.lstat(path)
            entry = [stat.filemode(info.st_mode)]
            if stat.S_ISREG(info.st_mode) and os.access(path, os.R_OK):
                with open(path, "rb") as file:
                    entry.append(_digest(file.read()))
            found[os.path.relpath(path, root)] = entry
    return found


def _rewrite(path: str, data: bytes) -> None:
    os.chmod(path, 0o700)
    with open(path, "wb") as file:
        file.write(data)
    os.chmod(path, 0o500)


def _damage(path: str, how: str) -> None:
    with open(path, "rb") as file:
        data = file.read()
    if how == "same size":
        _rewrite(path, bytes(len(data)))
    elif how == "longer":
        _rewrite(path, data + b"x")
    elif how == "empty":
        _rewrite(path, b"")
    elif how == "unreadable":
        os.chmod(path, 0)
    else:
        os.unlink(path)
        if how == "symlink":
            os.symlink(os.path.basename(path) + ".elsewhere", path)
        elif how == "fifo":
            os.mkfifo(path, 0o600)
        elif how == "directory":
            os.mkdir(path, 0o700)


def _sync_arguments(fsb: Any, tmp: str) -> dict[str, Any]:
    root, count = os.path.join(tmp, "arguments"), fsb.info()["files"]
    files = _closure(fsb, EXT)
    roots = [None, 1, "a\0b", ""]
    chains = [None, b"runtime", "", "../x", "a//b", "a/./b", "a/", "/a", "a\\b", "a\0b", "\ud800"]
    lists = [None, 1, [count], [-1], [2**64], ["0"], [1.0], {0: 0}, iter([0])]
    bits = (count + 7) // 8
    memos = [1, bytes(bits), bytearray(bits - 1), bytearray(bits + 1), bytearray(count)]
    return {
        "root": [_sync(fsb, bad, files) for bad in roots],
        "chain": [_sync(fsb, root, files, chain=bad) for bad in chains],
        "files": [_sync(fsb, root, bad) for bad in lists],
        "memo": [_sync(fsb, root, files, bad) for bad in memos],
        "written": [_sync(fsb, root, files, written=bad) for bad in ["x", (), {}]],
    }


def _sync_natives(fsb: Any, tmp: str) -> dict[str, Any]:
    parent = os.path.join(tmp, "natives")
    root = os.path.join(parent, "cache")  # neither exists: both are created
    count = fsb.info()["files"]
    files, memo = _closure(fsb, EXT), bytearray((count + 7) // 8)
    out: dict[str, Any] = {"first": _sync(fsb, root, files, memo), "tree": _tree(parent)}
    out["remembered"] = _bits(memo, count) == sorted(files)
    out["again"] = _sync(fsb, root, files, memo)
    _damage(os.path.join(_site(root), DEP), "same size")
    out["same process"] = _sync(fsb, root, files, memo)  # checked once per process
    out["new process"] = _sync(fsb, root, files)
    out["repaired"] = _tree(root)
    return out


def _sync_damaged(fsb: Any, tmp: str) -> dict[str, Any]:
    root, files = os.path.join(tmp, "damaged"), _closure(fsb, EXT)
    _sync(fsb, root, files)
    library = os.path.join(_site(root), DEP)
    out = {}
    for how in DAMAGES:
        _damage(library, how)
        out[how] = [*_sync(fsb, root, files), _tree(os.path.dirname(library))]
    return out


def _sync_directories(fsb: Any, tmp: str) -> dict[str, Any]:
    files, out = _closure(fsb, EXT), {}
    elsewhere, linked = os.path.join(tmp, "elsewhere"), os.path.join(tmp, "linked")
    os.mkdir(elsewhere)
    os.mkdir(linked)
    os.symlink(elsewhere, os.path.join(linked, "runtime"))  # never followed below the root
    out["symlink"] = [*_sync(fsb, linked, files), os.listdir(elsewhere)]
    blocked = os.path.join(tmp, "blocked")
    os.mkdir(blocked)
    open(os.path.join(blocked, "runtime"), "w").close()
    out["file"] = _sync(fsb, blocked, files)
    shared = os.path.join(tmp, "shared")  # the root's mode is the user's; below it, private
    os.makedirs(os.path.join(shared, "runtime", "g"))
    for path, mode in [("", 0o775), ("runtime", 0o755), ("runtime/g", 0o777)]:
        os.chmod(os.path.join(shared, path), mode)
    out["private"] = [*_sync(fsb, shared, files), stat.filemode(os.stat(shared).st_mode),
                      _tree(shared)]  # fmt: skip
    target, link = os.path.join(tmp, "target"), os.path.join(tmp, "link")
    os.mkdir(target)
    os.symlink(target, link)  # a root named through a symlink is followed
    out["root symlink"] = [*_sync(fsb, link, files), sorted(os.listdir(target))]
    out["foreign"] = _sync(fsb, _foreign(tmp), files)
    if os.geteuid() != 0:  # root writes anyway
        unwritable = os.path.join(tmp, "unwritable")
        os.mkdir(unwritable, 0o500)
        out["unwritable"] = _sync(fsb, unwritable, files)
        os.chmod(unwritable, 0o700)
    return out


def _foreign(tmp: str) -> str:
    """A directory owned by another user."""
    if os.geteuid() != 0:
        return "/proc"  # never created in: refused before
    foreign = os.path.join(tmp, "foreign")
    os.mkdir(foreign)
    os.chown(foreign, 65534, 65534)
    return foreign


def _sync_payload(fsb: Any, tmp: str) -> dict[str, Any]:
    """Records the payload's own checks catch: nothing is written for them."""
    out: dict[str, Any] = {}
    for path in BROKEN:
        root = os.path.join(tmp, "payload", os.path.basename(path))
        out[path] = [*_sync(fsb, root, [fsb.find_file(path)[0]]), _tree(root)]
    root, pieces = os.path.join(tmp, "pieces"), fsb.find_file("native.libs/libpieces.so")
    out["pieces"] = _sync(fsb, root, [pieces[0]])
    with open(os.path.join(_site(root), "native.libs/libpieces.so"), "rb") as file:
        out["pieces intact"] = _digest(file.read()) == _digest(fsb.read(pieces[4]))
    return out


def _sync_resources(fsb: Any, tmp: str) -> dict[str, Any]:
    """Files without a digest, extracted only as resources: compared with the payload's bytes."""
    root, data = os.path.join(tmp, "resources"), fsb.files_below("pkg/data")
    memo = bytearray((fsb.info()["files"] + 7) // 8)
    out: dict[str, Any] = {"refused": _sync(fsb, root, data)}
    out["first"] = _sync(fsb, root, data, memo, resources=True)
    _damage(os.path.join(_site(root), "pkg/data/table.json"), "same size")
    out["same process"] = _sync(fsb, root, data, memo, resources=True)
    out["new process"] = _sync(fsb, root, data, resources=True)
    out["tree"] = _tree(root)
    return out


def _sync_digests(fsb: Any, tmp: str) -> list[Any]:
    """Files around the digest's leaf boundaries, read or mapped, hashed on one to three threads:
    the launcher must find each one it wrote equal to the digest the builder recorded."""
    root, files = os.path.join(tmp, "digests"), fsb.files_below("sizes.libs")
    written = _sync(fsb, root, files)
    return [written, _sync(fsb, root, files), _tree(os.path.join(_site(root), "sizes.libs"))]


def _sync_batches(fsb: Any, tmp: str) -> list[Any]:
    """Every file of many passes is checked, and exactly the damaged ones are restored: on both
    sides of a pass's limits and of the read-or-map switch."""
    root, files = os.path.join(tmp, "batches"), fsb.files_below("batch")
    first = _sync(fsb, root, files, resources=True)
    directory = os.path.join(_site(root), "batch")
    names = sorted(os.listdir(directory))
    damaged = sorted({*names[::97], *(n for n in names if n.startswith("edge"))})
    for name in damaged:
        _damage(os.path.join(directory, name), "same size")
    again = _sync(fsb, root, files, resources=True)
    clean = _sync(fsb, root, files, resources=True)
    return [first[0][0], first[0][1], len(names), again,
            again[0] == ["ok", len(damaged), [f"batch/{name}" for name in damaged]],
            clean]  # fmt: skip


def _sync_threads(fsb: Any, tmp: str) -> list[Any]:
    """Syncs on threads of their own while other threads read blobs: they share no decoder."""
    import threading  # noqa: PLC0415

    files, count = fsb.files_below("batch"), fsb.info()["files"]
    blobs = sorted({fsb.file_at(i)[4] for i in files})
    expected = {blob: _digest(fsb.read(blob)) for blob in blobs}
    results: list[str] = []

    def syncing(n: int) -> None:
        try:
            root = os.path.join(tmp, f"thread{n}")
            memo = bytearray((count + 7) // 8)
            results.append(f"wrote {fsb.sync(root, CHAIN, files, memo, True)}")
        except Exception as exc:  # the contract compares it
            results.append(repr(exc))

    def reading() -> None:
        try:
            results.append(f"read {all(_digest(fsb.read(b)) == expected[b] for b in blobs)}")
        except Exception as exc:
            results.append(repr(exc))

    threads = [threading.Thread(target=syncing, args=(n,)) for n in range(3)]
    threads += [threading.Thread(target=reading) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    trees = [_tree(os.path.join(_site(os.path.join(tmp, f"thread{n}")), "batch")) for n in range(3)]
    return [sorted(results), trees[0] == trees[1] == trees[2]]


def _sync_without_stderr(fsb: Any, tmp: str) -> list[Any]:
    """With sys.stderr None a changed file is still restored (the report goes to the process's
    stderr)."""
    root, files = os.path.join(tmp, "quiet"), _closure(fsb, EXT)
    _sync(fsb, root, files)
    _damage(os.path.join(_site(root), DEP), "same size")
    saved, sys.stderr = sys.stderr, None
    try:
        written = fsb.sync(root, CHAIN, files, bytearray((fsb.info()["files"] + 7) // 8), False)
    finally:
        sys.stderr = saved
    return [written, _tree(os.path.join(_site(root), "native.libs"))]


def _limit_leaving(free: int) -> int:
    """A descriptor limit that leaves exactly ``free`` descriptors to open."""
    limit = 0
    while free:
        try:
            os.fstat(limit)
        except OSError:
            free -= 1
        limit += 1
    return limit


def _sync_limited(fsb: Any, tmp: str) -> list[Any]:
    """Directories are open along the path to one file at a time: a tree wider than the
    descriptor limit leaves room for is extracted. With no descriptor left for the file once its
    directories are open, the sync fails (EMFILE) and says so, never that the file changed. A
    check holds files over 64 KiB open until its pass is hashed: when the next file's directories
    find no descriptor left, the pass is checked first (D36)."""
    import resource  # noqa: PLC0415

    files, held = fsb.files_below("tree"), fsb.files_below("held")
    _sync(fsb, os.path.join(tmp, "held"), held, resources=True)  # extracted, to be checked
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    outcomes = []
    cases = [  # each in a cache of its own, but the checks of "held", extracted above
        ("limited", files, 10),
        ("exhausted", files, 6),  # root, runtime, g, site, tree, d00
        ("held", held, 47),  # the same six (held, a), 40 files held, then b and c: no d
        ("held", held, 30),  # 24 files held, then no descriptor for the 25th
    ]
    for name, chosen, free in cases:
        root = os.path.join(tmp, name)
        resource.setrlimit(resource.RLIMIT_NOFILE, (_limit_leaving(free), hard))
        try:
            outcomes.append(_sync(fsb, root, chosen, resources=True))
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    return [outcomes, len(files), _tree(os.path.join(_site(os.path.join(tmp, "limited")), "tree"))]
