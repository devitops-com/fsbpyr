"""Uses every record of a payload through the ``_fsbpyr`` API and lists what was refused (D38).

The edge-case tests run it inside executables (launcher, release and sanitized) and in-process
against the stand-in (the Python reader). A refusal is an exception from the API, a crash never
is. Standard library only: it is packaged into the executables it walks.
"""

import tempfile
from collections.abc import Callable
from typing import Any

MAX_READ = 16 << 20  # larger blobs are only extracted, not read into memory
MAX_RECORDS = 4096
NATIVE_KINDS = (3, 4)  # NATIVE_EXT, NATIVE_LIB


def run(fsb: Any) -> list[str]:
    """Sorted "call: exception class" of everything refused; empty if nothing was."""
    errors: set[str] = set()

    def attempt(name: str, call: Callable[..., Any], *args: Any) -> Any:
        try:
            return call(*args)
        except Exception as exc:  # the API's refusals, whatever their class: the test judges
            errors.add(f"{name}: {type(exc).__name__}")
            return None

    def read(blob: int) -> None:
        info = attempt("blob_info", fsb.blob_info, blob)
        if info is not None and info[0] <= MAX_READ:
            attempt("read", fsb.read, blob)

    info = attempt("info", fsb.info)
    count = info["files"] if info else 0
    natives = []  # extracted as an import extracts them: each must have a digest
    loads = []  # an extension and its closure, extracted together as an import does
    for index in range(min(count, MAX_RECORDS)):
        record = attempt("file_at", fsb.file_at, index)
        if record is None:
            continue
        if record[1] in NATIVE_KINDS:
            natives.append(index)
        attempt("find_file", fsb.find_file, record[7])
        read(record[4])
        if record[6] != fsb.NONE:
            members = attempt("closure", fsb.closure, record[6])
            if members is not None:
                loads.append([index, *members])
    prefixes = [""]
    while prefixes and len(prefixes) < MAX_RECORDS:  # every module, package by package
        prefix = prefixes.pop()
        for name, _ in attempt("list_modules", fsb.list_modules, prefix) or []:
            module = attempt("find_module", fsb.find_module, prefix + name)
            if module is not None:
                for blob in module[3:5]:
                    if blob != fsb.NONE:
                        read(blob)
                prefixes.append(prefix + name + ".")
    attempt("dists", fsb.dists)
    attempt("list_dir", fsb.list_dir, "")
    below = attempt("files_below", fsb.files_below, "") or ()
    with tempfile.TemporaryDirectory() as root:
        attempt("sync", fsb.sync, root, "runtime/g/site", below, bytearray(count), True)
        attempt("sync natives", fsb.sync, root, "runtime/n/site", natives, bytearray(count), False)
        for load in loads:
            attempt("sync closure", fsb.sync, root, "runtime/c/site", load, bytearray(count), False)
    return sorted(errors)
