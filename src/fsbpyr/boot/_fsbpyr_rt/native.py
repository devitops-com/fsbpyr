"""Native extensions: materialized on first import into the app's native cache (plan §3.5).

Layout (manifest §7, D9): ``<cache>/runtime/<generation>/site/<site-relative path>``. The
``site`` tree mirrors site-packages, so auditwheel's ``$ORIGIN`` RUNPATHs find the bundled
libraries unchanged. ``<cache>`` is ``$FSBPYR_CACHE_DIR`` or
``${XDG_CACHE_HOME:-~/.cache}/<app>``. A file is written under a temporary name, checked
against its SHA-256 and renamed into place, so racing processes only ever see complete files
and need no lock. Extensions are then loaded by CPython's ExtensionFileLoader (plain dlopen).
"""

import contextlib
import hashlib
import os
import stat
from _frozen_importlib import ModuleSpec
from _frozen_importlib_external import ExtensionFileLoader

import _fsbpyr

TYPE_CHECKING = False
if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import ModuleType

NATIVE_EXT, NATIVE_LIB = 3, 4
GENERATION_DIGITS = 32
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
_NEW_FILE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
FS_REQUIRED = 1  # file flag: extracted together with its FS_REQUIRED package
_MODE_MASK = 0o500  # owner read (and execute, where the payload says so); never writable
EXTRACTED: list[str] = []  # site-relative paths this process wrote (FSBPYR_TRACE)


class NativeCacheError(ImportError):
    """The native cache cannot be used, or the payload's native data is corrupt."""


def _corrupt(what: str) -> NativeCacheError:
    return NativeCacheError(f"corrupt executable payload: {what}")


def _unsafe(path: str) -> bool:
    return any(part in ("", ".", "..") for part in path.split("/"))


def cache_directory(app: str, environ: Mapping[str, str]) -> str:
    if _unsafe(app) or "/" in app:
        raise _corrupt(f"app name {app!r}")
    if explicit := environ.get("FSBPYR_CACHE_DIR"):
        return os.path.abspath(explicit)
    base = environ.get("XDG_CACHE_HOME", "")
    if not os.path.isabs(base):  # relative values are invalid per the XDG spec
        base = os.path.join(os.path.expanduser("~"), ".cache")
    if not os.path.isabs(base):
        raise NativeCacheError("cannot locate a cache directory; set FSBPYR_CACHE_DIR")
    return os.path.join(base, app)


def _checked(fd: int, path: str, *, private: bool) -> int:
    """``fd`` if the directory is ours; ``private`` ones (below the root) are forced to 0700."""
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid():
            raise NativeCacheError(f"refusing cache directory {path}: owned by another user")
        if private and stat.S_IMODE(info.st_mode) & 0o077:
            os.fchmod(fd, 0o700)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_root(path: str) -> int:
    """The cache root: it must be ours, but its mode is the user's business."""
    os.makedirs(path, 0o700, exist_ok=True)
    return _checked(os.open(path, _DIRECTORY), path, private=False)


def _open_child(parent: int, name: str, path: str) -> int:
    """Directory ``name`` below ``parent`` (created 0700); never follows a symlink."""
    with contextlib.suppress(FileExistsError):
        os.mkdir(name, 0o700, dir_fd=parent)
    return _checked(os.open(name, _DIRECTORY | os.O_NOFOLLOW, dir_fd=parent), path, private=True)


def _cached(directory: int, name: str, size: int, digest: bytes | None) -> bool:
    """A complete file of ours is in place (and, given ``digest``, has that content)."""
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_size != size:
        return False
    if digest is None:
        return True
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    with open(fd, "rb") as file:
        return hashlib.file_digest(file, "sha256").digest() == digest


def _publish(directory: int, name: str, data: bytes, mode: int) -> None:
    temporary = f".fsbpyr-{os.getpid()}-{os.urandom(6).hex()}"  # fits whatever ``name`` is
    fd = os.open(temporary, _NEW_FILE, 0o600, dir_fd=directory)
    try:
        with open(fd, "wb", closefd=False) as out:
            out.write(data)
        os.fchmod(fd, mode & _MODE_MASK)
        os.fsync(fd)
        os.rename(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary, dir_fd=directory)
        raise
    finally:
        os.close(fd)


class NativeCache:
    def __init__(self, root: str, generation: str) -> None:
        self.root = root
        self.site = f"{root}/runtime/{generation}/site"
        self._chain = ("runtime", generation, "site")

    def _open_site(self) -> int:
        fd = _open_root(self.root)
        path = self.root
        for name in self._chain:
            path = f"{path}/{name}"
            try:
                child = _open_child(fd, name, path)
            finally:
                os.close(fd)
            fd = child
        return fd

    def sync(self, files: tuple[int, ...], *, verify: bool = False) -> list[str]:
        """Extract the files (FILES indices) that are missing, or with ``verify`` whose
        content does not match; returns the site-relative paths written."""
        opened: dict[str, int] = {}

        def directory(rel: str) -> int:
            if rel not in opened:
                if rel:
                    parent, _, name = rel.rpartition("/")
                    opened[rel] = _open_child(directory(parent), name, f"{self.site}/{rel}")
                else:
                    opened[rel] = self._open_site()
            return opened[rel]

        written = []
        try:
            for index in files:
                _, kind, flags, mode, blob, _, _, rel = _fsbpyr.file_at(index)
                size, digest = _fsbpyr.blob_info(blob)
                extractable = kind in (NATIVE_EXT, NATIVE_LIB) or flags & FS_REQUIRED
                if not extractable or digest is None or _unsafe(rel):
                    raise _corrupt(f"extracted file {rel!r}")
                parent, _, name = rel.rpartition("/")
                if _cached(directory(parent), name, size, digest if verify else None):
                    continue
                data = _fsbpyr.read(blob)
                if hashlib.sha256(data).digest() != digest:
                    raise _corrupt(f"{rel!r} digest")
                _publish(directory(parent), name, data, mode)
                written.append(rel)
        finally:
            for fd in opened.values():
                os.close(fd)
        EXTRACTED.extend(written)
        return written


_CACHE: list[NativeCache] = []


def native_cache() -> NativeCache:
    """This executable's cache; computed once, without touching the filesystem."""
    if not _CACHE:
        info = _fsbpyr.info()
        root = cache_directory(info["app_name"], os.environ)
        _CACHE.append(NativeCache(root, info["generation_id"].hex()[:GENERATION_DIGITS]))
    return _CACHE[0]


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
                if cache.sync(self.files, verify=True):  # self-heal a damaged cached file
                    return super().create_module(spec)
                if _noexec(cache.root):
                    raise NativeCacheError(
                        f"{exc}; the native cache {cache.root} is on a noexec mount, "
                        "set FSBPYR_CACHE_DIR to another directory",
                        name=spec.name,
                    ) from exc
                raise
        except OSError as exc:
            raise NativeCacheError(
                f"cannot extract native code for {spec.name} into {cache.root}: {exc}; "
                "set FSBPYR_CACHE_DIR to a writable directory",
                name=spec.name,
            ) from exc


def _files_below(directory: str) -> list[int]:
    """FILES indices of every payload file below ``directory``."""
    found = []
    for name in _fsbpyr.list_dir(directory):
        path = f"{directory}/{name}"
        record = _fsbpyr.find_file(path)
        found.extend(_files_below(path) if record is None else [record[0]])
    return found


def materialize_package(directory: str) -> str:
    """Extract every file of the FS_REQUIRED package at payload ``directory``; returns the
    prefix (with "/") that the package's modules live under."""
    cache = native_cache()
    try:
        cache.sync(tuple(_files_below(directory)))
    except OSError as exc:
        raise NativeCacheError(
            f"cannot extract {directory} into {cache.root}: {exc}; "
            "set FSBPYR_CACHE_DIR to a writable directory",
            name=directory.replace("/", "."),
        ) from exc
    return cache.site + "/"


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
        spec.submodule_search_locations = [prefix + rel.rpartition("/")[0]]
    return spec
