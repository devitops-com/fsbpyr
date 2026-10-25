"""Adjustments to modules in single-file executables, applied right after they load.

The boot loader calls ``apply`` for the modules named in ``FIXUPS`` (plan Phase 6b).
"""

import contextlib
import os
import sys

TYPE_CHECKING = False
if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import ModuleType

# OpenSSL's default locations in the runtime (PBS builds OpenSSL with OPENSSLDIR=/etc/ssl).
CA_FILE = "/etc/ssl/cert.pem"
CA_DIRECTORY = "/etc/ssl/certs"
# System CA bundles of distributions where those defaults find nothing, in order of preference.
SYSTEM_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/ssl/certs/ca-bundle.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",
)
OWN_EXECUTABLE = "/proc/self/exe"


def fallback_bundle(environ: "Mapping[str, str]") -> str | None:
    """A system CA bundle to load when OpenSSL's defaults find no trust store: neither
    ``cert.pem`` nor a hashed ``certs/`` directory, and no SSL_CERT_* override."""
    if "SSL_CERT_FILE" in environ or "SSL_CERT_DIR" in environ or os.path.exists(CA_FILE):
        return None
    try:
        with os.scandir(CA_DIRECTORY) as entries:
            if any(entry.name.endswith(".0") for entry in entries):
                return None  # hashed links: OpenSSL looks certificates up there
    except OSError:
        pass
    readable = (b for b in SYSTEM_BUNDLES if os.path.isfile(b) and os.access(b, os.R_OK))
    return next(readable, None)


def _ssl(module: "ModuleType") -> None:
    bundle = fallback_bundle(os.environ)
    if bundle is None:
        return
    context = module.SSLContext
    set_default_verify_paths = context.set_default_verify_paths

    def with_system_bundle(self: object) -> None:
        set_default_verify_paths(self)
        # OpenSSL reads SSL_CERT_* when the defaults are loaded: an app setting them later
        # chose its own trust store, which the fallback must not widen.
        if "SSL_CERT_FILE" not in os.environ and "SSL_CERT_DIR" not in os.environ:
            # OpenSSL's own defaults never raise here; neither may the fallback
            with contextlib.suppress(OSError):  # ssl.SSLError included
                self.load_verify_locations(cafile=bundle)  # type: ignore[attr-defined]

    context.set_default_verify_paths = with_system_bundle


def _multiprocessing_spawn(module: "ModuleType") -> None:
    # Children start from the parent's own binary even when the file was replaced on disk in
    # the meantime: parent and children never mix code versions, and the check that a child's
    # parent runs this executable (D22) keeps passing.
    if os.path.exists(OWN_EXECUTABLE):
        module.set_executable(OWN_EXECUTABLE)


def _ctypes(module: "ModuleType") -> None:
    # A package that loads its own library by bare file name (PyTables: libblosc2.so, after
    # looking for it in site-packages, which a built app has no directory for) finds the one the
    # app bundles, when the system has none of that name: a system copy still comes first.
    init = module.CDLL.__init__

    def with_bundled(self: object, name: object, *args: object, **kwargs: object) -> None:
        try:
            init(self, name, *args, **kwargs)
        except OSError:
            bare = os.fspath(name) if isinstance(name, (str, os.PathLike)) else None
            if not isinstance(bare, str) or not bare or "/" in bare:
                raise
            from _fsbpyr_rt.native import (  # type: ignore[import-not-found]  # noqa: PLC0415
                bundled_library,
            )

            caller = sys._getframe(1)  # the code that called CDLL(), past ctypes' own frames
            while caller.f_back is not None and caller.f_globals.get("__package__") == "ctypes":
                caller = caller.f_back
            where = caller.f_globals.get("__file__")
            try:
                path = bundled_library(bare, where if isinstance(where, str) else None)
            except (OSError, ImportError):  # the cache cannot be used: the original error stands
                path = None
            if path is None:
                raise
            init(self, path, *args, **kwargs)

    module.CDLL.__init__ = with_bundled


def _parso_file_io(module: "ModuleType") -> None:
    # jedi parses the sources a module's loader hands it (KnownContentFileIO) and parso asks the
    # file for its time only, to cache the parse: below a regular file (the executable) that
    # fails with NotADirectoryError where parso expects FileNotFoundError, and no completion
    # can look at a module of the payload (the standard library's included). The time of a
    # payload file is the executable's.
    last_modified = module.FileIO.get_last_modified

    def payload_last_modified(self: object) -> float | None:
        try:
            return last_modified(self)  # type: ignore[no-any-return]
        except NotADirectoryError:
            import _fsbpyr  # noqa: PLC0415  only a payload path, and only here, gets this far

            executable = _fsbpyr.info()["executable"]
            if not os.fspath(self.path).startswith(executable + "/"):  # type: ignore[attr-defined]
                raise
            return os.stat(executable).st_mtime

    module.FileIO.get_last_modified = payload_last_modified


def _cv2_load_config(module: "ModuleType") -> None:
    # cv2's loader (cv2/__init__.py) takes itself out of sys.modules, inserts its directory at
    # sys.path[1] and imports "cv2" again, to load its binary cv2.abi3.so. A venv's
    # site-packages comes after that position; a built app's sys.path[0] is the executable,
    # which finds the cv2 package again ("recursion is detected"). OpenCV's switch inserts the
    # directory at 0; the loader imports this module before it reads the switch, and restores
    # sys.path afterwards.
    sys.OpenCV_REPLACE_SYS_PATH_0 = True  # type: ignore[attr-defined]


FIXUPS = {
    "ssl": _ssl,
    "multiprocessing.spawn": _multiprocessing_spawn,
    "ctypes": _ctypes,
    "parso.file_io": _parso_file_io,
    "cv2.load_config_py3": _cv2_load_config,
}


def apply(module: "ModuleType") -> None:
    FIXUPS[module.__name__](module)
