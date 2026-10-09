"""Bootstrap of every fsbpyr executable (plan §3.3 step 7, §3.4).

Stored as a STARTUP module and handed to CPython as a frozen module; the launcher runs
``_fsbpyr_boot.run()``. Only built-in and frozen modules may be imported at module level, so
that startup never touches the filesystem and never decompresses anything it does not need.
Support for importlib.resources and importlib.metadata lives in the payload package
``_fsbpyr_rt`` and is imported only when an application uses those APIs.

The payload appears to Python as one virtual directory tree rooted at the executable path:
``sys.path == [<exe>]`` and module ``a.b`` has ``__file__ == <exe>/a/b.py``. A path hook claims
``<exe>`` and every ``<exe>/<dir>`` entry, so the stock PathFinder resolves packages, namespace
packages and subpackages exactly as it does for a site-packages directory.
"""

import _imp
import marshal
import posix
import sys
from _frozen_importlib import FrozenImporter, ModuleSpec
from _frozen_importlib_external import NamespaceLoader, PathFinder

import _fsbpyr

TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations are lazy (PEP 649): nothing below is imported at runtime
    from collections.abc import Iterable, Iterator
    from importlib.abc import Loader as _LoaderBase
    from importlib.metadata import Distribution, DistributionFinder
    from importlib.resources.abc import TraversableResources
    from types import CodeType, ModuleType, TracebackType
else:
    _LoaderBase = object

MODULE, PACKAGE, NAMESPACE, EXTENSION = 1, 2, 3, 4
FS_REQUIRED_ROOT = 2  # module flag: the package's files are extracted on first import
NONE = _fsbpyr.NONE
INFO = _fsbpyr.info()
ROOT = INFO["executable"]
_PREFIX = ROOT + "/"
_INTERNAL = ("_fsbpyr_boot", "_fsbpyr_rt")  # boot code, hidden from module listings
# Stdlib modules adjusted right after they load (_fsbpyr_rt.fixups): CA store fallback and
# multiprocessing children started from this very binary.
_FIXUPS = frozenset({"ssl", "multiprocessing.spawn"})
# The native cache's site directory (with "/"), once an FS_REQUIRED package was extracted
# there: modules of such packages live below it, and the path hook claims it too.
_FS_PREFIXES: list[str] = []

# (index, kind, flags, code, source, file, dist, path) as returned by _fsbpyr.find_module
type Record = tuple[int, int, int, int, int, int, int, str]


def _directory(path: str) -> str:
    """Directory part of a site-relative path ('' for top-level entries)."""
    return path.rpartition("/")[0]


def _is_extension_package(name: str) -> bool:
    """A compiled ``__init__`` (Cython and the like) makes an extension module a package."""
    record = _fsbpyr.find_module(name)
    return record is not None and record[7].rpartition("/")[2].startswith("__init__.")


class PayloadLoader(_LoaderBase):
    """Loads pure-Python modules and packages from payload records (spec.loader_state).

    ``prefix`` is where the modules appear to live: the executable, or the native cache for
    FS_REQUIRED packages (whose files are on disk there; the code still comes from memory).
    """

    def __init__(self, prefix: str = _PREFIX) -> None:
        self.prefix = prefix

    def create_module(self, spec: ModuleSpec) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        spec = module.__spec__
        if spec is None:
            raise ImportError(f"{module.__name__!r} has no spec", name=module.__name__)
        record: Record = spec.loader_state
        exec(self._code(record), module.__dict__)  # noqa: S102
        if module.__name__ in _FIXUPS:
            from _fsbpyr_rt.fixups import apply  # type: ignore[import-not-found]  # noqa: PLC0415

            apply(module)

    def _record(self, fullname: str) -> Record:
        record = _fsbpyr.find_module(fullname)
        if record is None or record[1] not in (MODULE, PACKAGE):
            raise ImportError(f"no module named {fullname!r} in the payload", name=fullname)
        return record

    def _code(self, record: Record) -> CodeType:
        code: CodeType = marshal.loads(_fsbpyr.read(record[3]))
        # Absolute virtual path: linecache can never mistake a file in the cwd for our source.
        _imp._fix_co_filename(code, self.prefix + record[7])  # type: ignore[attr-defined]
        return code

    def get_code(self, fullname: str) -> CodeType:
        return self._code(self._record(fullname))

    def get_source(self, fullname: str) -> str | None:
        source = self._record(fullname)[4]
        if source == NONE:
            return None
        from _frozen_importlib_external import decode_source  # noqa: PLC0415  frozen, rarely used

        return decode_source(_fsbpyr.read(source))

    def is_package(self, fullname: str) -> bool:
        return self._record(fullname)[1] == PACKAGE

    def get_filename(self, fullname: str) -> str:
        return self.prefix + self._record(fullname)[7]

    def get_data(self, path: str) -> bytes:
        """``pkgutil.get_data`` and ``open(os.path.join(dirname(__file__), ...))`` emulation."""
        prefix = next((p for p in (_PREFIX, self.prefix) if path.startswith(p)), None)
        record = _fsbpyr.find_file(path[len(prefix) :]) if prefix is not None else None
        if record is None:
            raise FileNotFoundError(2, "No such file in the executable payload", path)
        return _fsbpyr.read(record[4])

    def get_resource_reader(self, fullname: str) -> TraversableResources:
        # _fsbpyr_rt exists as a top-level package only inside executables (and boot tests)
        from _fsbpyr_rt.resources import (  # type: ignore[import-not-found]  # noqa: PLC0415
            DirectoryReader,
            PayloadResourceReader,
        )

        directory = _directory(self._record(fullname)[7])
        reader: TraversableResources = (
            PayloadResourceReader(ROOT, directory)
            if self.prefix == _PREFIX
            else DirectoryReader(self.prefix + directory)
        )
        return reader


LOADER = PayloadLoader()


def _fs_package_prefix(directory: str) -> str:
    """Extract an FS_REQUIRED package's files; the prefix its modules live under from now on."""
    from _fsbpyr_rt.native import (  # type: ignore[import-not-found]  # noqa: PLC0415
        materialize_package,
    )

    prefix: str = materialize_package(directory)
    if prefix not in _FS_PREFIXES:
        _FS_PREFIXES.append(prefix)
    return prefix


class PayloadPathFinder:
    """Path-entry finder for one virtual directory of the payload ('' is the root)."""

    def __init__(self, directory: str, location: str = _PREFIX) -> None:
        self.directory = directory
        self.location = location  # _PREFIX, or an FS_REQUIRED cache prefix
        self._loader = LOADER if location == _PREFIX else PayloadLoader(location)
        self._prefix = directory.replace("/", ".") + "." if directory else ""

    def find_spec(self, fullname: str, target: object = None) -> ModuleSpec | None:
        if _directory(fullname.replace(".", "/")) != self.directory:
            return None
        record = _fsbpyr.find_module(fullname)
        if record is None:
            return None
        kind, path, location, loader = record[1], record[7], self.location, self._loader
        if kind == NAMESPACE:  # a portion: PathFinder assembles the namespace package
            spec = ModuleSpec(fullname, None, is_package=True)
            spec.submodule_search_locations = [location + fullname.replace(".", "/")]
            return spec
        if kind == EXTENSION:
            from _fsbpyr_rt.native import extension_spec  # noqa: PLC0415

            native: ModuleSpec = extension_spec(fullname, record, location)
            return native
        if record[2] & FS_REQUIRED_ROOT and location == _PREFIX:
            location = _fs_package_prefix(_directory(path))
            loader = PayloadLoader(location)
        spec = ModuleSpec(
            fullname,
            loader,
            origin=location + path,
            loader_state=record,
            is_package=kind == PACKAGE,
        )
        spec.has_location = True
        if kind == PACKAGE:  # by name: a package below a .pth directory keeps its submodules
            spec.submodule_search_locations = [location + fullname.replace(".", "/")]
        return spec

    def iter_modules(self, prefix: str = "") -> Iterator[tuple[str, bool]]:
        """For ``pkgutil.iter_modules`` / ``walk_packages``: like FileFinder, list modules and
        regular packages, not namespace directories."""
        for name, kind in _fsbpyr.list_modules(self._prefix):
            if kind != NAMESPACE and not (self.directory == "" and name in _INTERNAL):
                yield (
                    prefix + name,
                    kind == PACKAGE
                    or (kind == EXTENSION and _is_extension_package(self._prefix + name)),
                )

    def invalidate_caches(self) -> None:
        pass


def path_hook(entry: str) -> PayloadPathFinder:
    """Claim ``<exe>`` and ``<exe>/<dir>`` (normalizing '', '.' and trailing components), and
    the same below the cache directory of extracted FS_REQUIRED packages."""
    for location in (_PREFIX, *_FS_PREFIXES):
        if entry == location[:-1] or entry.startswith(location):
            parts = [part for part in entry[len(location) :].split("/") if part not in ("", ".")]
            if ".." not in parts:
                return PayloadPathFinder("/".join(parts), location)
    raise ImportError("not an fsbpyr payload path", path=entry)


class PayloadNamespaceLoader(NamespaceLoader):
    """Namespace packages of the payload, with importlib.resources support."""

    def get_resource_reader(  # type: ignore[override]  # importlib passes the module name
        self, module: str
    ) -> TraversableResources:
        from _fsbpyr_rt.resources import PayloadResourceReader  # noqa: PLC0415

        location: str = self._path[0]  # type: ignore[attr-defined]
        if not location.startswith(_PREFIX):  # an extracted FS_REQUIRED directory
            on_disk: TraversableResources = super().get_resource_reader(module)  # type: ignore[arg-type]
            return on_disk
        record = _fsbpyr.find_module(module)  # its directory, also below a .pth root
        directory = record[7] if record is not None else location[len(_PREFIX) :]
        reader: TraversableResources = PayloadResourceReader(ROOT, directory)
        return reader


class PayloadMetaFinder:
    """On sys.meta_path ahead of PathFinder: payload namespace packages (CPython's own
    NamespaceReader only understands real directories and zip files) and importlib.metadata."""

    @staticmethod
    def find_spec(
        fullname: str, path: Iterable[str] | None = None, target: object = None
    ) -> ModuleSpec | None:
        record = _fsbpyr.find_module(fullname)
        if record is None or record[1] != NAMESPACE:
            return None
        parent = next(iter(path), "") if path else ""  # a list, or a parent's _NamespacePath
        location = next((p for p in _FS_PREFIXES if parent.startswith(p)), _PREFIX)
        finder = PathFinder._get_spec  # type: ignore[attr-defined]  # namespace recalculation
        loader = PayloadNamespaceLoader(fullname, [location + fullname.replace(".", "/")], finder)
        spec = ModuleSpec(fullname, loader, is_package=True)
        spec.submodule_search_locations = loader._path  # type: ignore[attr-defined]
        return spec

    @staticmethod
    def find_distributions(
        context: DistributionFinder.Context | None = None,
    ) -> list[Distribution]:
        from _fsbpyr_rt.metadata import (  # type: ignore[import-not-found]  # noqa: PLC0415
            find_distributions,
        )

        found: list[Distribution] = find_distributions(ROOT, context)
        return found

    @staticmethod
    def invalidate_caches() -> None:
        pass


META_FINDER = PayloadMetaFinder()


def _excepthook(
    exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
) -> None:
    """Hide the launcher's own frames (run_command, this module) at the top of tracebacks."""
    hidden = ("<string>", _excepthook.__code__.co_filename)
    head = tb
    while head is not None and head.tb_frame.f_code.co_filename in hidden:
        head = head.tb_next
    if head is not None:  # the default hook prints exc.__traceback__, not the tb argument
        exc = exc.with_traceback(head)
    sys.__excepthook__(exc_type, exc, head or tb)


def _resolve(entrypoint: str) -> object:
    module_name, _, attribute = entrypoint.partition(":")
    target: object = __import__(module_name, fromlist=["__name__"])
    for part in attribute.split(".") if attribute else ():
        target = getattr(target, part)
    return target


def _frozen_source(fullname: str) -> str | None:
    record = _fsbpyr.find_module(fullname)
    if record is None or record[4] == NONE:
        return None
    from _frozen_importlib_external import decode_source  # noqa: PLC0415  frozen, rarely used

    return decode_source(_fsbpyr.read(record[4]))


def _install_frozen_sources() -> None:
    """CPython keeps no source for its frozen stdlib modules (os, codecs, ...); the payload
    does, so inspect.getsource() works on them as it does on a normal installation."""
    frozen_source = FrozenImporter.get_source

    def get_source(cls: type, fullname: str) -> str | None:
        return frozen_source(fullname) or _frozen_source(fullname)

    FrozenImporter.get_source = classmethod(get_source)  # type: ignore[method-assign,assignment]


def _multiprocessing_child() -> bool:
    """multiprocessing re-executes ``sys.executable`` (this app) with ``-c <code>``."""
    argv = sys.argv
    if "-c" not in argv[1:-1] or "multiprocessing" not in argv[argv.index("-c") + 1]:
        return False  # the common case costs two scans of argv
    from _fsbpyr_rt.mp import run_child  # type: ignore[import-not-found]  # noqa: PLC0415

    handled: bool = run_child(argv)
    return handled


def run() -> None:
    sys.path_hooks.insert(0, path_hook)
    sys.path[:] = [ROOT]
    sys.path_importer_cache.clear()
    position = (
        sys.meta_path.index(PathFinder) if PathFinder in sys.meta_path else len(sys.meta_path)
    )
    sys.meta_path.insert(position, META_FINDER)
    if sys.excepthook is sys.__excepthook__:
        sys.excepthook = _excepthook
    _install_frozen_sources()
    if posix.environ.get(b"FSBPYR_TRACE"):
        import atexit  # noqa: PLC0415

        from _fsbpyr_rt.trace import report  # type: ignore[import-not-found]  # noqa: PLC0415

        atexit.register(report)
    if _multiprocessing_child():
        return
    collection = posix.environ.get(b"FSBPYR_GC")  # D38: any other value runs the app
    if collection in (b"1", b"background"):
        from _fsbpyr_rt.native import collect  # noqa: PLC0415

        collect(quiet=collection == b"background")
        return
    entrypoint = INFO["entrypoint"]
    if posix.environ.get(b"FSBPYR_SELFTEST"):
        _resolve(entrypoint)  # import the module and look the attribute up, without calling
        print(f"fsbpyr selftest ok: {INFO['app_name']}")
        return
    if ":" not in entrypoint:
        import runpy  # noqa: PLC0415

        runpy._run_module_as_main(entrypoint, alter_argv=False)  # type: ignore[attr-defined]
        return
    main = _resolve(entrypoint)
    if not callable(main):
        raise TypeError(f"entry point {entrypoint!r} is not callable")
    sys.exit(main())
