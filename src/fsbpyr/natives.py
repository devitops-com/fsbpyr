"""Native dependency analysis at build time (plan §3.5).

Every ``DT_NEEDED`` entry of every shared object in the payload must resolve either to another
payload file, through the object's RUNPATH (or RPATH) with ``$ORIGIN`` expanded, or to a system
library that manylinux wheels may rely on. Because the native cache mirrors the site-packages
layout (D9), the auditwheel-style ``$ORIGIN`` paths keep working unchanged at run time.
"""

import posixpath
import re

from .elf import ElfError, dynamic_info

# Libraries manylinux wheels may take from the system (auditwheel policy, manylinux_2_17 and
# later for x86-64, plus the dynamic loader and libz which current policies also allow).
SYSTEM_LIBRARIES = frozenset({
    "ld-linux-x86-64.so.2", "libc.so.6", "libm.so.6", "libdl.so.2", "librt.so.1",
    "libpthread.so.0", "libutil.so.1", "libnsl.so.1", "libresolv.so.2", "libgcc_s.so.1",
    "libstdc++.so.6", "libz.so.1", "libX11.so.6", "libXext.so.6", "libXrender.so.1",
    "libICE.so.6", "libSM.so.6", "libGL.so.1", "libgobject-2.0.so.0", "libgthread-2.0.so.0",
    "libglib-2.0.so.0",
})  # fmt: skip
_LIBPYTHON = re.compile(r"^libpython3")
_ORIGIN = re.compile(r"\$(ORIGIN(?![A-Za-z0-9_])|\{ORIGIN\})")  # as ld.so tokenizes it


class NativeError(ValueError):
    """A native dependency cannot be satisfied inside the executable or by the system."""


def _search_path(path: str, rpath: tuple[str, ...], runpath: tuple[str, ...]) -> list[str]:
    origin = posixpath.dirname(path) or "."  # top-level files: site-packages itself
    directories = []
    for entry in runpath or rpath:  # the loader ignores RPATH when RUNPATH is present
        if not _ORIGIN.match(entry):
            continue  # absolute or relative-to-cwd entries never point into the payload
        directories.append(posixpath.normpath(_ORIGIN.sub(lambda _: origin, entry)))
    return directories


def _direct_dependencies(
    path: str, data: bytes, payload: set[str], allowed: frozenset[str]
) -> list[str]:
    try:
        info = dynamic_info(data)
    except ElfError as exc:
        raise NativeError(f"{path}: {exc}") from exc
    dependencies = []
    for needed in info.needed:
        if _LIBPYTHON.match(needed):
            raise NativeError(
                f"{path} links against {needed}; extensions must take libpython symbols from "
                "the interpreter (rebuild it as a manylinux wheel)"
            )
        if "/" in needed:  # ld.so would open it relative to the working directory
            raise NativeError(f"{path} needs {needed} by path, which cannot be bundled")
        directories = _search_path(path, info.rpath, info.runpath)
        candidates = (posixpath.normpath(posixpath.join(d, needed)) for d in directories)
        found = next((c for c in candidates if c in payload), None)
        if found is not None:
            dependencies.append(found)
        elif needed not in SYSTEM_LIBRARIES and needed not in allowed:
            raise NativeError(
                f"{path} needs {needed}, which is neither bundled nor a standard system library; "
                f"bundle it (auditwheel repair) or pass --allow-system-lib {needed}"
            )
    return dependencies


def closures(
    natives: dict[str, bytes], extensions: set[str], *, allowed: frozenset[str]
) -> dict[str, tuple[str, ...]]:
    """Bundled shared objects each extension needs, transitively (excluding itself)."""
    payload = set(natives)
    direct: dict[str, list[str]] = {}

    def dependencies(path: str) -> list[str]:  # only objects an extension can reach are checked
        if path not in direct:
            direct[path] = _direct_dependencies(path, natives[path], payload, allowed)
        return direct[path]

    result = {}
    for extension in sorted(extensions):
        seen: set[str] = set()
        pending = list(dependencies(extension))
        while pending:
            dependency = pending.pop()
            if dependency not in seen and dependency != extension:
                seen.add(dependency)
                pending.extend(dependencies(dependency))
        result[extension] = tuple(sorted(seen))
    return result
