"""What the target must provide beyond what is bundled: system libraries and their symbol
versions, checked against the manylinux_2_28 floors the baseline wheels were built for (NAT-2).

glibc has its own report and warning (``build.glibc_report``); here are the libraries around
it: libstdc++ (GLIBCXX, CXXABI), libgcc_s (GCC), libz (ZLIB), X11 and the like, and the
launcher's own needs.
"""

from collections.abc import Mapping

from .elf import ElfError, VersionNeed, dynamic_info, version_requirements
from .natives import (
    GLIBC_LIBRARIES,
    MANYLINUX_2_28_LIMITS,
    MANYLINUX_2_28_MARKERS,
    SystemUse,
)

STUB = "(stub)"  # the launcher, in "needed by"
_PRIVATE = "GLIBC_PRIVATE"  # glibc's internals: no promise from one release to the next


def _dotted(version: tuple[int, ...]) -> str:
    return ".".join(map(str, version))


def _stub_use(stub: bytes) -> tuple[list[str], dict[str, dict[str, VersionNeed]]]:
    """The libraries the launcher needs and its versions of them; nothing if it cannot be read."""
    try:
        return list(dynamic_info(stub).needed), version_requirements(stub)
    except ElfError:
        return [], {}


def _merged(use: SystemUse, stub: bytes) -> dict[str, dict[str, object]]:
    """Per library: the files that need it, and the newest version per namespace."""
    needed_by = {library: set(files) for library, files in use.needed_by.items()}
    versions = [use.versions[path] for path in sorted(use.versions)]
    stub_needed, stub_versions = _stub_use(stub)
    for library in stub_needed:
        needed_by.setdefault(library, set()).add(STUB)
    versions.append({lib: v for lib, v in stub_versions.items() if lib in stub_needed})
    libraries: dict[str, dict[str, object]] = {}
    for library in sorted(needed_by):
        highest: dict[str, tuple[int, ...]] = {}
        markers: set[str] = set()
        for table in versions:
            for namespace, need in table.get(library, {}).items():
                if need.highest is not None:
                    highest[namespace] = max(highest.get(namespace, need.highest), need.highest)
                markers.update(need.markers)
        entry: dict[str, object] = {"needed_by": sorted(needed_by[library])}
        if highest:
            entry["versions"] = {ns: _dotted(highest[ns]) for ns in sorted(highest)}
        if markers:
            entry["markers"] = sorted(markers)
        libraries[library] = entry
    return libraries


def target_needs(libraries: Mapping[str, Mapping[str, object]], glibc: str | None) -> str | None:
    """The glibc and the other system libraries (with versions) the target must have, in a line."""
    parts = [f"glibc >= {glibc}"] if glibc else []
    for library, entry in libraries.items():
        if library in GLIBC_LIBRARIES:
            continue
        versions = entry.get("versions")
        shown = (
            ", ".join(f"{ns}_{v}" for ns, v in versions.items())
            if isinstance(versions, Mapping)
            else ""
        )
        parts.append(f"{library} ({shown})" if shown else library)
    return "; ".join(parts) or None


def _over_floor(use: SystemUse) -> list[str]:
    """Warnings for the files that need a newer libstdc++, libgcc_s or libz than the
    manylinux_2_28 systems the wheels were chosen for provide, one per namespace: the
    newest need, with how many other files go over too."""
    over: dict[tuple[str, str], list[tuple[tuple[int, ...], str]]] = {}
    warnings = []
    for path in sorted(use.versions):
        for library, namespaces in use.versions[path].items():
            for namespace, need in namespaces.items():
                limit = MANYLINUX_2_28_LIMITS.get(namespace)
                if need.highest is not None and limit is not None and need.highest > limit:
                    over.setdefault((library, namespace), []).append((need.highest, path))
                for marker in need.markers:
                    if marker == _PRIVATE:
                        warnings.append(
                            f"{path} needs {_PRIVATE} from {library}, which has no "
                            "compatibility promise; it may fail on other glibc releases"
                        )
                    elif (
                        namespace in MANYLINUX_2_28_LIMITS and marker not in MANYLINUX_2_28_MARKERS
                    ):
                        warnings.append(
                            f"{path} needs {marker} from {library}, which "
                            "manylinux_2_28 systems may not provide"
                        )
    for (library, namespace), found in sorted(over.items()):
        version, path = max(found)
        others = len(found) - 1
        more = (
            f" (and {others} more native file{'s' * (others != 1)} over the limit)"
            if others
            else ""
        )
        warnings.append(
            f"{path} needs {namespace}_{_dotted(version)} from {library}, newer than "
            f"{namespace}_{_dotted(MANYLINUX_2_28_LIMITS[namespace])} that manylinux_2_28 "
            f"systems (Debian 10, RHEL 8) provide{more}; on older systems it fails when imported"
        )
    return warnings


def system_report(stub: bytes, use: SystemUse) -> tuple[dict[str, dict[str, object]], list[str]]:
    """The libraries of the build information (sorted), and the warnings for needs above the
    manylinux_2_28 floors."""
    return _merged(use, stub), _over_floor(use)
