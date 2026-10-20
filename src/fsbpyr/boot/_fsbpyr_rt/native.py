"""Native extensions: materialized on first import into the app's native cache (plan §3.5).

Layout (manifest §7, D9): ``<cache>/runtime/<generation>/site/<site-relative path>``. The
``site`` tree mirrors site-packages, so auditwheel's ``$ORIGIN`` RUNPATHs find the bundled
libraries unchanged. ``<cache>`` is ``$FSBPYR_CACHE_DIR`` or
``${XDG_CACHE_HOME:-~/.cache}/<app>``. Extensions are loaded by CPython's ExtensionFileLoader
(plain dlopen) once the launcher's ``_fsbpyr.sync`` has put their files in place (D35): it
writes a missing file under a temporary name and renames it into place, so racing processes
only ever see complete files and need no lock. Cached files run outside the executable's own
integrity check, so each process hashes them once, before it first uses them, with the
launcher's own digest routine (D31, D32): a modified file is reported and restored from the
payload.
"""

import os
from _frozen_importlib import ModuleSpec
from _frozen_importlib_external import ExtensionFileLoader

import _fsbpyr

TYPE_CHECKING = False
if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from types import ModuleType

NATIVE_EXT = 3
GENERATION_DIGITS = 32
_SYSTEM_LIBRARY_ERRORS = ("version `GLIBC", "GLIBCXX_", "CXXABI_", "cannot open shared object file")
EXTRACTED: list[str] = []  # site-relative paths this process wrote, kept for FSBPYR_TRACE
# ImportError: the native cache cannot be used, or the payload's data for it is corrupt.
NativeCacheError = _fsbpyr.CacheError


def _corrupt(what: str) -> NativeCacheError:
    return NativeCacheError(f"corrupt executable payload: {what}")


def cache_directory(app: str, environ: Mapping[str, str]) -> str:
    if "/" in app or app in ("", ".", ".."):
        raise _corrupt(f"app name {app!r}")
    if explicit := environ.get("FSBPYR_CACHE_DIR"):
        return os.path.abspath(explicit)
    base = environ.get("XDG_CACHE_HOME", "")
    if not os.path.isabs(base):  # relative values are invalid per the XDG spec
        base = os.path.join(os.path.expanduser("~"), ".cache")
    if not os.path.isabs(base):
        raise NativeCacheError("cannot locate a cache directory; set FSBPYR_CACHE_DIR")
    return os.path.join(base, app)


class NativeCache:
    def __init__(self, root: str, *chain: str) -> None:
        self.root = root
        self.site = "/".join((root, *chain))
        self._chain = "/".join(chain)
        self.tree = "/".join(chain[:-1])  # "runtime/<generation>": held from the first sync on
        files = _fsbpyr.info()["files"]
        self._checked = bytearray((files + 7) // 8)  # a bit per FILES index: checked here

    def sync(self, files: Sequence[int], *, resources: bool = False) -> int:
        """Extract the files (FILES indices, a range from files_below or a closure) that are
        missing or whose content does not match the payload's, reporting the changed ones;
        returns the number of files written. Each process hashes a file once (extensions share
        libraries). With ``resources`` any file may be extracted (importlib.resources asked for
        it on disk); one without a digest is compared with its bytes in the payload. Paths
        written are kept only for FSBPYR_TRACE: no memory per file otherwise (D38)."""
        written = EXTRACTED if os.environ.get("FSBPYR_TRACE") else None
        return _fsbpyr.sync(self.root, self._chain, files, self._checked, resources, written)


_CACHE: list[NativeCache] = []
_RESOURCES: list[NativeCache] = []
_MATERIALIZED: dict[str, str] = {}  # payload path -> path on disk, checked by this process


def native_cache() -> NativeCache:
    """This executable's cache; computed once, without touching the filesystem."""
    if not _CACHE:
        info = _fsbpyr.info()
        root = cache_directory(info["app_name"], os.environ)
        generation = info["generation_id"].hex()[:GENERATION_DIGITS]
        _CACHE.append(NativeCache(root, "runtime", generation, "site"))
    return _CACHE[0]


def resource_cache() -> NativeCache:
    """The tree of resources extracted on demand (D27): keyed by every payload file, unlike
    the generation, so a release never sees files another one extracted."""
    if not _RESOURCES:
        import json  # noqa: PLC0415  # only resources asked for as paths need it

        try:
            key = json.loads(_fsbpyr.info()["build_info"]).get("resources")
        except (ValueError, AttributeError) as exc:
            raise _corrupt("build info") from exc
        if not isinstance(key, str) or len(key) != GENERATION_DIGITS or not key.isalnum():
            raise _corrupt("resources id")
        _RESOURCES.append(NativeCache(native_cache().root, "resources", key, "site"))
    return _RESOURCES[0]


def _system_library_hint(message: str) -> str:
    """For a load failure that names a missing glibc/libstdc++ version or shared object: what
    the build required and what this system has (D24)."""
    if not any(marker in message for marker in _SYSTEM_LIBRARY_ERRORS):
        return ""
    import json  # noqa: PLC0415  # only a failed load needs it

    try:
        needed = json.loads(_fsbpyr.info()["build_info"]).get("glibc")
    except (ValueError, AttributeError):
        needed = None
    try:
        system = os.confstr("CS_GNU_LIBC_VERSION")
    except (ValueError, OSError):  # not glibc
        system = None
    return (
        f"; the build requires glibc {needed or 'unknown'}, this system has "
        f"{system or 'an unknown C library'}"
    )


def _noexec(path: str) -> bool:
    try:
        return bool(os.statvfs(path).f_flag & os.ST_NOEXEC)
    except OSError:
        return False


class PayloadExtensionLoader(ExtensionFileLoader):
    """ExtensionFileLoader for an extension that is materialized, together with the bundled
    libraries it needs, when it is first imported."""

    def __init__(self, name: str, path: str, files: tuple[int, ...]) -> None:
        super().__init__(name, path)
        self.files = files  # the extension's FILES index, then its closure

    def create_module(self, spec: ModuleSpec) -> ModuleType:
        cache = native_cache()
        try:
            cache.sync(self.files)
            try:
                return super().create_module(spec)
            except ImportError as exc:
                if exc.path != spec.origin:  # raised by the module's own initialization
                    raise
                if _noexec(cache.root):
                    raise NativeCacheError(
                        f"{exc}; the native cache {cache.root} is on a noexec mount, "
                        "set FSBPYR_CACHE_DIR to another directory",
                        name=spec.name,
                    ) from exc
                if hint := _system_library_hint(str(exc)):
                    raise ImportError(f"{exc}{hint}", name=exc.name, path=exc.path) from exc
                raise
        except OSError as exc:
            raise NativeCacheError(
                f"cannot extract native code for {spec.name} into {cache.root}: {exc}; "
                "set FSBPYR_CACHE_DIR to a writable directory",
                name=spec.name,
            ) from exc


def materialize_package(directory: str) -> str:
    """Extract every file of the FS_REQUIRED package at payload ``directory``; returns the
    prefix (with "/") that the package's modules live under."""
    cache = native_cache()
    try:
        cache.sync(_fsbpyr.files_below(directory))
    except OSError as exc:
        raise NativeCacheError(
            f"cannot extract {directory} into {cache.root}: {exc}; "
            "set FSBPYR_CACHE_DIR to a writable directory",
            name=directory.replace("/", "."),
        ) from exc
    return cache.site + "/"


def materialize(path: str) -> str:
    """Extract the payload file or directory at site-relative ``path`` (not the root) for
    importlib.resources (``as_file``, ``os.fspath``); returns where it is on disk. It stays
    there, as an installed package's files do, so code that keeps the path after ``as_file``
    returns keeps working. Raises OSError (FileNotFoundError when ``path`` is not in the
    payload). Each process checks the files on disk once, on its first request."""
    if path in _MATERIALIZED:
        return _MATERIALIZED[path]
    record = _fsbpyr.find_file(path) if path else None
    files = (record[0],) if record is not None else _fsbpyr.files_below(path) if path else ()
    if not files:
        raise FileNotFoundError(2, "No such file in the executable payload", path)
    try:
        cache = resource_cache()
        cache.sync(files, resources=True)
    except NativeCacheError as exc:  # its message says what to do
        raise OSError(f"cannot extract {path}: {exc}") from exc
    except OSError as exc:  # same class (PermissionError, ...), with the cache and the remedy
        root = _CACHE[0].root if _CACHE else "the cache"
        detail = f"cannot extract {path} into {root}: {exc.strerror or exc}"
        hint = "; set FSBPYR_CACHE_DIR to a writable directory"
        if exc.errno is None:
            raise OSError(detail + hint) from exc
        raise OSError(exc.errno, detail + hint, exc.filename) from exc
    _MATERIALIZED[path] = f"{cache.site}/{path}"
    return _MATERIALIZED[path]


def extension_spec(
    fullname: str, record: tuple[int, int, int, int, int, int, int, str], prefix: str
) -> ModuleSpec:
    """Spec of EXTENSION module record ``record``; ``prefix`` is the payload root plus '/'."""
    index, kind, _, _, _, _, closure, rel = _fsbpyr.file_at(record[5])
    if kind != NATIVE_EXT:
        raise _corrupt(f"extension {fullname!r}")
    members = _fsbpyr.closure(closure) if closure != _fsbpyr.NONE else ()
    origin = f"{native_cache().site}/{rel}"
    loader = PayloadExtensionLoader(fullname, origin, (index, *members))
    spec = ModuleSpec(fullname, loader, origin=origin)
    spec.has_location = True
    if loader.is_package(fullname):  # compiled __init__: submodules live in the payload
        spec.submodule_search_locations = [prefix + fullname.replace(".", "/")]
    return spec


def collect(*, quiet: bool) -> None:
    """``FSBPYR_GC=1`` (``background``: ``quiet``): collect this app's cache instead of running
    the app (D38, _fsbpyr_rt.gc), never removing this build's own trees; exit status 1 if
    anything could not be removed."""
    import contextlib  # noqa: PLC0415  # only a collection needs these
    import sys  # noqa: PLC0415

    from . import gc  # noqa: PLC0415

    keep = {native_cache().tree}
    with contextlib.suppress(NativeCacheError):  # corrupt build information: no resource tree
        keep.add(resource_cache().tree)
    report = gc.collect(native_cache().root, keep=frozenset(keep))
    if not quiet:
        print(report.text(), file=sys.stderr)
    if report.errors:
        raise SystemExit(1)
