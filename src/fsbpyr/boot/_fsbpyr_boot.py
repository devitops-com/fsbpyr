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
import _sitebuiltins
import builtins
import marshal
import posix
import sys
from _frozen_importlib import FrozenImporter, ModuleSpec
from _frozen_importlib_external import NamespaceLoader, PathFinder

import _fsbpyr

TYPE_CHECKING = False
if TYPE_CHECKING:  # nothing below is imported at runtime: the annotations using it are quoted
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

    def exec_module(self, module: "ModuleType") -> None:
        spec = module.__spec__
        if spec is None:
            raise ImportError(f"{module.__name__!r} has no spec", name=module.__name__)
        record: Record = spec.loader_state
        exec(self._code(record), module.__dict__)  # noqa: S102
        if module.__name__ == "linecache":  # 3.12: no startup module, imported when needed
            _install_payload_linecache(module)
        elif module.__name__ in _FIXUPS:
            from _fsbpyr_rt.fixups import apply  # type: ignore[import-not-found]  # noqa: PLC0415

            apply(module)

    def _record(self, fullname: str) -> Record:
        record = _fsbpyr.find_module(fullname)
        if record is None or record[1] not in (MODULE, PACKAGE):
            raise ImportError(f"no module named {fullname!r} in the payload", name=fullname)
        return record

    def _code(self, record: Record) -> "CodeType":
        code: CodeType = marshal.loads(_fsbpyr.read(record[3]))
        # Absolute virtual path: linecache can never mistake a file in the cwd for our source.
        _imp._fix_co_filename(code, self.prefix + record[7])  # type: ignore[attr-defined]
        return code

    def get_code(self, fullname: str) -> "CodeType":
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

    def get_resource_reader(self, fullname: str) -> "TraversableResources":
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


class AliasLoader(PayloadLoader):
    """Loader of a module imported under a name other than its payload name: a sys.path
    entry below the root (``<exe>/pkg/_vendor``) makes ``vendored`` of ``pkg._vendor.vendored``."""

    def __init__(self, prefix: str, public: str, payload: str) -> None:
        super().__init__(prefix)
        self.public, self.payload = public, payload

    def _record(self, fullname: str) -> Record:
        return super()._record(self.payload if fullname == self.public else fullname)


class InitLoader(AliasLoader):
    """Loader of ``pkg.__init__`` imported as a module of its own (``from .__init__ import x``):
    it runs the package's ``__init__`` record again, and is not a package."""

    def is_package(self, fullname: str) -> bool:
        return False


# warnings.warn(stacklevel >= 2) skips the frames of importlib (file names with "importlib" and
# "_bootstrap"), so that an import-time warning names the importing module. exec_module is the one
# frame of ours between the importer and the module's code: it carries such a file name too.
# Not the exact "<frozen importlib._bootstrap...>" of CPython, whose frames tracebacks strip.
_LOADER_FRAME = "<frozen importlib._bootstrap_fsbpyr>"
PayloadLoader.exec_module.__code__ = PayloadLoader.exec_module.__code__.replace(
    co_filename=_LOADER_FRAME
)
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
        if _directory(fullname.replace(".", "/")) == self.directory:
            return self._spec(fullname, fullname)
        parts = self.directory.split("/")
        if not self.directory or not all(part.isidentifier() for part in parts):
            return None
        # An entry below the root (``sys.path.append(<exe>/pkg/_vendor)``): the module is
        # imported by its short name and lives in the payload as ``pkg._vendor.<name>``.
        return self._spec(fullname, f"{self._prefix}{fullname.rpartition('.')[2]}")

    def _spec(self, fullname: str, payload: str) -> ModuleSpec | None:
        """Spec of module ``fullname``, whose payload record is named ``payload``."""
        record = _fsbpyr.find_module(payload)
        init = payload.removesuffix(".__init__")
        if record is None and init != payload:  # a venv's FileFinder finds __init__.py by name
            record = _fsbpyr.find_module(init)
            if record is None or record[1] != PACKAGE:  # regular packages only
                return None
        else:
            init = payload
        if record is None:
            return None
        kind, path, location, loader = record[1], record[7], self.location, self._loader
        if init != payload:
            kind = MODULE
        where = location + payload.replace(".", "/")
        if kind == NAMESPACE:  # a portion: PathFinder assembles the namespace package
            spec = ModuleSpec(fullname, None, is_package=True)
            spec.submodule_search_locations = [where]
            return spec
        if kind == EXTENSION:
            from _fsbpyr_rt.native import extension_spec  # noqa: PLC0415

            native: ModuleSpec = extension_spec(fullname, record, location)
            if native.submodule_search_locations is not None:
                native.submodule_search_locations = [where]
            return native
        if record[2] & FS_REQUIRED_ROOT and location == _PREFIX:
            location = _fs_package_prefix(_directory(path))
            loader = PayloadLoader(location)
        if init != payload:
            loader = InitLoader(location, fullname, init)
        elif payload != fullname:
            loader = AliasLoader(location, fullname, payload)
        spec = ModuleSpec(
            fullname,
            loader,
            origin=location + path,
            loader_state=record,
            is_package=kind == PACKAGE,
        )
        spec.has_location = True
        if kind == PACKAGE:  # by name: a package below a .pth directory keeps its submodules
            spec.submodule_search_locations = [location + payload.replace(".", "/")]
        return spec

    def iter_modules(self, prefix: str = "") -> "Iterator[tuple[str, bool]]":
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
    ) -> "TraversableResources":
        from _fsbpyr_rt.resources import PayloadResourceReader  # noqa: PLC0415

        location: str = self._path[0]  # type: ignore[attr-defined]
        if not location.startswith(_PREFIX):  # an extracted FS_REQUIRED directory
            on_disk: TraversableResources = super().get_resource_reader(module)  # type: ignore[arg-type]
            return on_disk
        record = _fsbpyr.find_module(module)  # its directory, also below a .pth root
        directory = record[7] if record is not None else location[len(_PREFIX) :]
        reader: TraversableResources = PayloadResourceReader(ROOT, directory)
        return reader


class SetuptoolsDistutilsLoader(_LoaderBase):
    """``distutils`` as setuptools' ``_distutils_hack`` makes it: the module setuptools ships."""

    @staticmethod
    def create_module(spec: ModuleSpec) -> "ModuleType":
        module: ModuleType = __import__("setuptools._distutils", fromlist=["_"])
        return module

    @staticmethod
    def exec_module(module: "ModuleType") -> None:
        pass  # setuptools._distutils is already executed


def _distutils_spec(fullname: str) -> ModuleSpec | None:
    """RT-5: a packaged setuptools stands in for the missing stdlib ``distutils`` (3.12+), even
    when it is imported before setuptools. Submodules resolve through the shared ``__path__``."""
    if _fsbpyr.find_module("setuptools._distutils") is None:
        return None
    return ModuleSpec(fullname, SetuptoolsDistutilsLoader(), is_package=True)


class PayloadMetaFinder:
    """On sys.meta_path ahead of PathFinder: payload namespace packages (CPython's own
    NamespaceReader only understands real directories and zip files) and importlib.metadata."""

    @staticmethod
    def find_spec(
        fullname: str, path: "Iterable[str] | None" = None, target: object = None
    ) -> ModuleSpec | None:
        record = _fsbpyr.find_module(fullname)
        if record is None:
            return _distutils_spec(fullname) if fullname == "distutils" else None
        if record[1] != NAMESPACE:
            return None
        parent = next(iter(path), "") if path else ""  # a list, or a parent's _NamespacePath
        location = next((p for p in _FS_PREFIXES if parent.startswith(p)), _PREFIX)
        finder = PathFinder._get_spec  # type: ignore[attr-defined]  # namespace recalculation
        # Merge the portions as a venv does (a vendored `<exe>/pkg/_vendor/ns` joins a packaged
        # top-level `ns`); a real package ahead of them wins.
        found: ModuleSpec | None = finder(fullname, path or sys.path)
        if found is not None and found.loader is not None:
            return found
        if found is not None and found.submodule_search_locations:
            portions = found.submodule_search_locations
        else:
            portions = [location + fullname.replace(".", "/")]
        loader = PayloadNamespaceLoader(fullname, portions, finder)
        spec = ModuleSpec(fullname, loader, is_package=True)
        spec.submodule_search_locations = loader._path  # type: ignore[attr-defined]
        return spec

    @staticmethod
    def find_distributions(
        context: "DistributionFinder.Context | None" = None,
    ) -> "list[Distribution]":
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
    exc_type: type[BaseException], exc: BaseException, tb: "TracebackType | None"
) -> None:
    """Hide the launcher's own frames (run_command, this module) at the top of tracebacks."""
    hidden = ("<string>", _excepthook.__code__.co_filename, _LOADER_FRAME)
    head = tb
    while head is not None and head.tb_frame.f_code.co_filename in hidden:
        head = head.tb_next
    if head is not None:  # the default hook prints exc.__traceback__, not the tb argument
        exc = exc.with_traceback(head)
    # 3.12's C printer reads the source files from disk, the traceback module asks the loaders.
    # Not for Ctrl-C: importing it runs eval(), which on 3.12 forgets the unhandled
    # KeyboardInterrupt, and the process must still end by SIGINT.
    if sys.version_info < (3, 13) and not isinstance(exc, KeyboardInterrupt):
        import traceback  # noqa: PLC0415  only for a crash

        traceback.print_exception(exc_type, exc, head or tb)
    else:
        sys.__excepthook__(exc_type, exc, head or tb)
    if (note := _inside_executable_note(exc)) is not None:
        sys.stderr.write(note)


def _inside_executable_note(exc: BaseException | None) -> str | None:
    """D24: the hint for an OSError (anywhere in the chain) on a path below the executable,
    which a package reading files next to its ``__file__`` provokes. Costs nothing until an
    exception goes uncaught."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        filename = exc.filename if isinstance(exc, OSError) else None
        if isinstance(filename, str) and filename.startswith(_PREFIX):
            package = filename[len(_PREFIX) :].partition("/")[0]
            if _fsbpyr.find_module(package) is not None:
                return (
                    f"fsbpyr: {filename} is inside the executable; {package} reads files next "
                    f"to its __file__: rebuild with --fs-package {package}\n"
                )
        exc = exc.__cause__ or exc.__context__
    return None


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


def _locate_frozen_modules() -> None:
    """3.12 leaves ``sys._stdlib_dir`` unset whatever ``PyConfig.stdlib_dir`` says, so the
    frozen stdlib modules imported at startup (encodings, os, codecs, ...) have no ``__file__``
    and ``encodings.__path__`` is empty. Set it as 3.13 does and place those modules in the
    payload's tree (the executable is its root, see the launcher's pin_prefixes)."""
    if getattr(sys, "_stdlib_dir", None) is not None:
        return
    sys._stdlib_dir = ROOT  # type: ignore[attr-defined]  # private, and typeshed has no entry
    for name, module in list(sys.modules.items()):
        spec = getattr(module, "__spec__", None)
        state = getattr(spec, "loader_state", None)
        if spec is None or spec.loader is not FrozenImporter or state is None or state.filename:
            continue
        if state.origname is None or name != state.origname:  # an alias (__hello_alias__)
            continue
        relative = _PREFIX + name.replace(".", "/")
        if spec.submodule_search_locations is not None:
            spec.submodule_search_locations.insert(0, relative)
            module.__path__ = spec.submodule_search_locations
            relative += "/__init__"
        module.__file__ = state.filename = relative + ".py"


def _install_frozen_sources() -> None:
    """CPython keeps no source for its frozen stdlib modules (os, codecs, ...); the payload
    does, so inspect.getsource() works on them as it does on a normal installation."""
    frozen_source = FrozenImporter.get_source

    def get_source(cls: type, fullname: str) -> str | None:
        return frozen_source(fullname) or _frozen_source(fullname)

    FrozenImporter.get_source = classmethod(get_source)  # type: ignore[method-assign,assignment]


def _install_payload_linecache(linecache: "ModuleType") -> None:
    """warnings formats the source line with ``linecache.getline(file, line)`` and no module
    globals, so no loader is asked for it: let linecache read payload files from the payload."""
    updatecache = linecache.updatecache

    def payload_updatecache(
        filename: str, module_globals: "dict[str, object] | None" = None
    ) -> list[str]:
        lines: list[str] = updatecache(filename, module_globals)
        if lines or not (filename.startswith(_PREFIX) and filename.endswith(".py")):
            return lines
        record = _fsbpyr.find_file(filename[len(_PREFIX) :])
        if record is None:
            return lines
        from _frozen_importlib_external import decode_source  # noqa: PLC0415

        text = decode_source(_fsbpyr.read(record[4]))
        split = text.split("\n")  # at newlines only, as the file's readlines() would
        lines = [line + "\n" for line in split[:-1]] + ([split[-1]] if split[-1] else [])
        linecache.cache[filename] = (len(text), None, lines, filename)  # no mtime: never stale
        return lines

    linecache.updatecache = payload_updatecache  # type: ignore[attr-defined]


def _install_site_builtins() -> None:
    """exit(), quit() and help as site defines them; built apps never import site (D14).
    help imports pydoc only when it is called."""
    builtins.exit = _sitebuiltins.Quitter("exit", "Ctrl-D (i.e. EOF)")
    builtins.quit = _sitebuiltins.Quitter("quit", "Ctrl-D (i.e. EOF)")
    builtins.help = _sitebuiltins._Helper()


def _multiprocessing_child() -> bool:
    """multiprocessing re-executes ``sys.executable`` (this app) with ``-c <code>``."""
    argv = sys.argv
    if "-c" not in argv[1:-1] or "multiprocessing" not in argv[argv.index("-c") + 1]:
        return False  # the common case costs two scans of argv
    from _fsbpyr_rt.mp import run_child  # type: ignore[import-not-found]  # noqa: PLC0415

    handled: bool = run_child(argv, INFO["entrypoint"])
    return handled


def _interpreter_child() -> bool:
    """The app re-executed through ``sys.executable`` with ``-m MODULE``, ``-c CODE`` or a
    script of the payload runs that instead with interpreter-reexec (D23), and is refused
    without the setting (D44)."""
    argv = sys.argv
    flagged = "-m" in argv[1:-1] or "-c" in argv[1:-1]
    if not flagged and not any(arg.startswith(_PREFIX) for arg in argv[1:]):
        return False  # the common case costs three scans of argv
    from _fsbpyr_rt.reexec import run_child  # type: ignore[import-not-found]  # noqa: PLC0415

    handled: bool = run_child(argv, INFO["build_info"], _PREFIX)
    return handled


def run() -> None:
    _locate_frozen_modules()
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
    if (linecache := sys.modules.get("linecache")) is not None:  # a startup module (3.13+)
        _install_payload_linecache(linecache)
    _install_site_builtins()  # for -c and -m children too
    if posix.environ.get(b"FSBPYR_TRACE"):
        import atexit  # noqa: PLC0415

        from _fsbpyr_rt.trace import report  # type: ignore[import-not-found]  # noqa: PLC0415

        atexit.register(report)
    if _multiprocessing_child() or _interpreter_child():
        return
    collection = posix.environ.get(b"FSBPYR_GC")  # D38: any other value runs the app
    if collection in (b"1", b"background"):
        from _fsbpyr_rt.native import collect  # noqa: PLC0415

        collect(quiet=collection == b"background")
        return
    if posix.environ.get(b"FSBPYR_SELFTEST") == b"natives":  # NAT-4: before anything imports
        from _fsbpyr_rt.selftest import natives  # type: ignore[import-not-found]  # noqa: PLC0415

        natives()
        return
    entrypoint = INFO["entrypoint"]
    if posix.environ.get(b"FSBPYR_SELFTEST"):
        _resolve(entrypoint)  # import the module and look the attribute up, without calling
        if ":" not in entrypoint and hasattr(sys.modules[entrypoint], "__path__"):
            from importlib.util import find_spec  # noqa: PLC0415

            if find_spec(f"{entrypoint}.__main__") is None:  # runpy would run it: found, not run
                raise ModuleNotFoundError(
                    f"No module named {entrypoint}.__main__; {entrypoint!r} is a package and "
                    "cannot be directly executed"
                )
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
