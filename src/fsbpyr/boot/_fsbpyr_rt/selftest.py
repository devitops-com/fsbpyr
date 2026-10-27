"""``FSBPYR_SELFTEST=natives``: before deploying, check that this machine provides what every
native extension needs (NAT-4). The extensions and their closures are put in the cache, then
the dynamic loader runs in trace mode (what ldd does) on each: it resolves the libraries and
symbol versions without running the extension's initialization. It exits 0 even when it finds
something missing, so its output is parsed. The loader is the executable's own ``PT_INTERP``,
the one of the target the app was built for. glibc's trace output only (the musl targets have
no pins yet)."""

import os
import re
import subprocess
import sys

import _fsbpyr

from .native import NATIVE_EXT, native_cache

PT_INTERP = 3
ELF_HEADER_SIZE = 4096  # the program headers and the interpreter's name are within it
_NOT_FOUND = re.compile(r"^\s*(\S+) => not found$", re.MULTILINE)
_NO_VERSION = re.compile(r"(\S+): version `([^']+)' not found")


def _integer(data: bytes, offset: int, size: int) -> int:
    return int.from_bytes(data[offset : offset + size], "little")


def interpreter(header: bytes) -> str | None:
    """The program interpreter an ELF64 little-endian file asks for; None if it names none, or
    ``header`` (the file's first bytes) is not such a file or is cut short."""
    if header[:4] != b"\x7fELF" or header[4:6] != b"\x02\x01":
        return None
    table = _integer(header, 0x20, 8)  # e_phoff, e_phentsize, e_phnum
    entry, count = _integer(header, 0x36, 2), _integer(header, 0x38, 2)
    for index in range(count):
        at = table + index * entry
        if _integer(header, at, 4) == PT_INTERP:
            start, size = _integer(header, at + 8, 8), _integer(header, at + 32, 8)
            name = header[start : start + size].partition(b"\0")[0]
            return os.fsdecode(name) or None
    return None


def missing(trace: str) -> list[str]:
    """What the loader's trace output says is missing: ``libfoo.so.1`` for a library, and
    ``version `X' of libfoo.so.1`` for a symbol version the library on this machine lacks."""
    found = [(m.start(), m[1]) for m in _NOT_FOUND.finditer(trace)]
    found += [(m.start(), f"version `{m[2]}' of {os.path.basename(m[1])}")
              for m in _NO_VERSION.finditer(trace)]  # fmt: skip
    return list(dict.fromkeys(item for _, item in sorted(found)))


def _extensions() -> list[tuple[str, tuple[int, ...]]]:
    """Every extension's site-relative path, with its FILES index and its closure's."""
    found = []
    for index in range(_fsbpyr.info()["files"]):
        _, kind, _, _, _, _, closure, rel = _fsbpyr.file_at(index)
        if kind == NATIVE_EXT:
            members = _fsbpyr.closure(closure) if closure != _fsbpyr.NONE else ()
            found.append((rel, (index, *members)))
    return found


def natives(loader: str | None = None) -> None:
    """Report on stderr one line per missing library or version, and raise SystemExit(1) if
    there is any (or the check could not run); otherwise say so on stdout. A refused cache
    directory ends it as it ends a run (native.refuse: exit 70). ``loader``: the dynamic loader,
    in place of this executable's."""
    if loader is None:
        with open("/proc/self/exe", "rb") as exe:
            loader = interpreter(exe.read(ELF_HEADER_SIZE))
    if loader is None:
        print("fsbpyr selftest: cannot find this executable's dynamic loader", file=sys.stderr)
        raise SystemExit(1)
    cache = native_cache()
    extensions = _extensions()
    needed_by: dict[str, list[str]] = {}
    for rel, files in extensions:
        cache.extract(files)
        try:
            traced = subprocess.run(  # noqa: S603  the executable's own loader, our cache file
                [loader, f"{cache.site}/{rel}"], capture_output=True, text=True,
                errors="replace", check=False,
                env={**os.environ, "LD_TRACE_LOADED_OBJECTS": "1"},
            )  # fmt: skip
        except OSError as exc:
            print(f"fsbpyr selftest: cannot run {loader}: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        for item in missing(traced.stdout + traced.stderr):
            needed_by.setdefault(item, []).append(rel)
    for item, rels in needed_by.items():
        users = ", ".join(sorted(rels))
        print(f"fsbpyr selftest: missing {item} (needed by {users})", file=sys.stderr)
    if needed_by:
        raise SystemExit(1)
    print(f"fsbpyr selftest: all natives load ({len(extensions)} extensions)")
