"""Adjustments to stdlib modules in single-file executables, applied right after they load.

The boot loader calls ``apply`` for the modules named in ``FIXUPS`` (plan Phase 6b).
"""

import contextlib
import os

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

            try:
                path = bundled_library(bare)
            except (OSError, ImportError):  # the cache cannot be used: the original error stands
                path = None
            if path is None:
                raise
            init(self, path, *args, **kwargs)

    module.CDLL.__init__ = with_bundled


FIXUPS = {"ssl": _ssl, "multiprocessing.spawn": _multiprocessing_spawn, "ctypes": _ctypes}


def apply(module: "ModuleType") -> None:
    FIXUPS[module.__name__](module)
