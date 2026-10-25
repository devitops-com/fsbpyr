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

The one exception is the scripts root (``_fsbpyr_scripts/``, the project's own files, loose.py):
a module of it appears next to the executable, ``__file__ == <dir of exe>/<path below the root>``,
as it does beside a script run by path, so that the files a script reads next to ``__file__``
(config, state, logs) are the host's, deployed beside the executable (owner decision 2026-10-25,
D24). The code is still the payload's: the path hook claims those directories too and code
objects keep the payload's ``co_filename``, so tracebacks show the payload's source.
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
_SCRIPTS = "_fsbpyr_scripts."  # the root of a project without a build system (loose.py)
_SCRIPTS_DIR = _SCRIPTS[:-1] + "/"
_BIN = ROOT.rpartition("/")[0] + "/"  # the executable's directory: where scripts appear to live
_INTERNAL = ("_fsbpyr_boot", "_fsbpyr_rt")  # boot code, hidden from module listings
# Modules adjusted right after they load (_fsbpyr_rt.fixups): CA store fallback, multiprocessing
# children started from this very binary, bundled libraries by bare name, parso's file times,
# cv2's loader.
_FIXUPS = frozenset({"ssl", "multiprocessing.spawn", "ctypes", "parso.file_io",
                     "cv2.load_config_py3"})  # fmt: skip
_SCRIPT_APP = _SCRIPTS in INFO["entrypoint"]  # some command is a script of the scripts root
if sys.version_info >= (3, 14) and _SCRIPT_APP:
    _FIXUPS |= {"argparse"}  # prog of a script started by the path its __file__ is
# The native cache's site directory (with "/"), once an FS_REQUIRED package was extracted
# there: modules of such packages live below it, and the path hook claims it too.
_FS_PREFIXES: list[str] = []
_FS_ROOTS: list[str] = []  # payload directories of the packages extracted there

# (index, kind, flags, code, source, file, dist, path) as returned by _fsbpyr.find_module
type Record = tuple[int, int, int, int, int, int, int, str]


def _directory(path: str) -> str:
    """Directory part of a site-relative path ('' for top-level entries)."""
    return path.rpartition("/")[0]


def _module_part(part: str) -> str:
    """A directory name below the scripts root as its payload name: what no identifier holds
    becomes ``_``, and a name that starts with a digit gets a ``_`` in front (``sub-dir`` ->
    ``sub_dir``, ``2024-tools`` -> ``_2024_tools``). The rule of ``_module_stem`` in loose.py,
    which names the directory in the payload."""
    if part.isidentifier():
        return part
    mangled = "".join(char if f"_{char}".isidentifier() else "_" for char in part)
    return mangled if mangled.isidentifier() else f"_{mangled}"


def _shown(prefix: str, path: str) -> str:
    """Where the module whose payload path is ``path`` appears to live: a module of the scripts
    root next to the executable, any other below ``prefix``."""
    if prefix == _PREFIX and path.startswith(_SCRIPTS_DIR):
        return _BIN + path[len(_SCRIPTS_DIR) :]
    return prefix + path


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
        return _shown(self.prefix, self._record(fullname)[7])

    def get_data(self, path: str) -> bytes:
        """``pkgutil.get_data`` and ``open(os.path.join(dirname(__file__), ...))`` emulation."""
        if self.prefix == _PREFIX and path.startswith(_BIN) and not path.startswith(_PREFIX):
            # a script's __file__-relative path
            path = _PREFIX + _SCRIPTS_DIR + path[len(_BIN) :]
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
    if directory not in _FS_ROOTS:
        _FS_ROOTS.append(directory)
    # A loaded module's path entry is in sys.path_importer_cache (PEP 302); code that
    # walks entries from __file__ (twisted.python.modules) warns if not.
    sys.path_importer_cache.setdefault(prefix[:-1], PayloadPathFinder("", prefix))
    return prefix


class PayloadPathFinder:
    """Path-entry finder for one virtual directory of the payload ('' is the root)."""

    def __init__(self, directory: str, location: str = _PREFIX) -> None:
        self.directory = directory
        self.location = location  # _PREFIX, or an FS_REQUIRED cache prefix
        self._loader = LOADER if location == _PREFIX else PayloadLoader(location)
        # Below the scripts root a directory that is no Python name (``sub-dir``) is stored under
        # its mangled name (loose.py): the entry is the on-disk name, the payload's is ``sub_dir``.
        parts = directory.split("/")
        names = [_module_part(part) for part in parts] if parts[0] == _SCRIPTS[:-1] else parts
        self._prefix = ".".join(names) + "." if directory else ""
        self._aliasable = bool(directory) and all(name.isidentifier() for name in names)

    def find_spec(self, fullname: str, target: object = None) -> ModuleSpec | None:
        if _directory(fullname.replace(".", "/")) == self.directory:
            return self._spec(fullname, fullname)
        if not self._aliasable:
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
            origin=_shown(location, path),
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


def _native_site() -> str | None:
    """The native cache's site directory (with "/") once natives are in use, else None (also
    where no cache directory can be told: no payload path may fail for want of one)."""
    native = sys.modules.get("_fsbpyr_rt.native")
    if native is None:
        return None
    try:
        return str(native.native_cache().site) + "/"
    except (ImportError, OSError):
        return None


def _locations() -> "Iterator[str]":
    """Where payload paths live; the native cache's site last, asked only for an entry no
    other location claims."""
    yield _PREFIX
    yield from _FS_PREFIXES
    if (site := _native_site()) is not None:
        yield site


def _in_fs_package(directory: str) -> bool:
    return any(directory == root or directory.startswith(root + "/") for root in _FS_ROOTS)


def _without_dotdot(parts: list[str]) -> list[str] | None:
    """``parts`` with each ``..`` removed together with the part before it, None if one has no
    part to remove (the entry names a directory above the root)."""
    kept: list[str] = []
    for part in parts:
        if part != "..":
            kept.append(part)
        elif kept:
            kept.pop()
        else:
            return None
    return kept


def _overlay_finder(entry: str) -> PayloadPathFinder | None:
    """The finder of a directory of the scripts root as it appears next to the executable
    (``<dir of exe>/<dir below the root>``, ``_shown``), or None for any other entry: the code
    is the payload's, so that a stale ``.py`` copy on the disk never shadows it. The host's
    other files in those directories are not looked at."""
    if entry != _BIN[:-1] and not entry.startswith(_BIN):
        return None
    parts = [part for part in entry[len(_BIN) :].split("/") if part not in ("", ".")]
    kept = _without_dotdot(parts) if ".." in parts else parts
    if kept is None:
        return None
    record = _fsbpyr.find_module(".".join([_SCRIPTS[:-1], *map(_module_part, kept)]))
    if record is None or record[1] not in (PACKAGE, NAMESPACE):
        return None
    return PayloadPathFinder("/".join([_SCRIPTS_DIR[:-1], *kept]))


def path_hook(entry: str) -> PayloadPathFinder:
    """Claim ``<exe>`` and ``<exe>/<dir>`` (normalizing '', '.', '..' and trailing components; an
    entry that climbs above ``<exe>`` is not the payload's), and the same below the native
    cache's site directory: the files of an extracted FS_REQUIRED package are there, any other
    directory stands for the payload's (mypyc names the directory its extension was extracted
    to as the compiled package's ``__path__``, whatever the spec says). A directory next to the
    executable that is a directory of the scripts root is claimed too (``_overlay_finder``)."""
    for location in _locations():
        if entry == location[:-1] or entry.startswith(location):
            parts = [part for part in entry[len(location) :].split("/") if part not in ("", ".")]
            kept = _without_dotdot(parts) if ".." in parts else parts
            if kept is None:
                continue  # it climbs above the executable: not the payload's
            directory = "/".join(kept)
            served = location if location == _PREFIX or _in_fs_package(directory) else _PREFIX
            return PayloadPathFinder(directory, served)
    if (finder := _overlay_finder(entry)) is not None:
        return finder
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
    missing = _missing_module(exc) if sys.version_info >= (3, 15) else None
    flags = sys.flags
    if missing is not None:
        sys.flags = _FlagsWithSite(flags)  # type: ignore[assignment]
    try:
        if sys.version_info < (3, 13) and not isinstance(exc, KeyboardInterrupt):
            import traceback  # noqa: PLC0415  only for a crash

            traceback.print_exception(exc_type, exc, head or tb)
        else:
            sys.__excepthook__(exc_type, exc, head or tb)
    finally:
        sys.flags = flags
    if missing is not None:
        sys.stderr.write(
            f"fsbpyr: {missing} is not in the executable: add it to the project's dependencies "
            "and rebuild\n"
        )
    if (note := _inside_executable_note(exc)) is not None:
        sys.stderr.write(note)


class _FlagsWithSite:
    """``sys.flags`` with ``no_site`` clear while a traceback prints: built apps never import
    site (D14), and 3.15's ModuleNotFoundError hint for ``no_site`` ("did you forget to enable
    your virtual environment?") misleads here."""

    def __init__(self, flags: object) -> None:
        self._flags = flags

    def __getattr__(self, name: str) -> object:
        return 0 if name == "no_site" else getattr(self._flags, name)


def _missing_module(exc: BaseException | None) -> str | None:
    """The name of a ModuleNotFoundError in the chain of ``exc`` that is not a standard library
    module (3.15 words those itself), or None. Costs nothing until an exception goes uncaught."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        name = exc.name if isinstance(exc, ModuleNotFoundError) else None
        if name is not None and name.partition(".")[0] not in sys.stdlib_module_names:
            return name
        exc = exc.__cause__ or exc.__context__
    return None


def _inside_executable_note(exc: BaseException | None) -> str | None:
    """D24: the hint for an OSError (anywhere in the chain) on a path below the executable,
    which code reading files next to its ``__file__`` provokes. Costs nothing until an
    exception goes uncaught."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        filename = exc.filename if isinstance(exc, OSError) else None
        if isinstance(filename, str) and filename.startswith(_PREFIX):
            return _note_for(filename)
        if (
            isinstance(exc, FileNotFoundError)
            and isinstance(filename, str)
            and filename.startswith(_BIN)
            and _overlay_finder(filename.rpartition("/")[0]) is not None
        ):
            return _deploy_note(filename)
        exc = exc.__cause__ or exc.__context__
    return None


def _deploy_note(filename: str) -> str:
    """The D24 note for a file that is not there, below the executable's directory, in an app of
    scripts: a script reads the files next to its ``__file__`` from there (``_shown``)."""
    return (
        f"fsbpyr: {filename} is missing; the files a script reads next to its __file__ are read "
        f"next to the executable, from {_BIN}: deploy it there\n"
    )


def _note_for(filename: str) -> str:
    """The D24 note for ``filename``, a path below the executable. ``--fs-package`` takes a
    regular package (one with an ``__init__.py``) and nothing else: not a namespace directory,
    not a flat module, not the scripts root."""
    parts = filename[len(_PREFIX) :].split("/")
    if parts[0] == _SCRIPTS[:-1]:  # a script's __file__ is its path next to the executable
        return (
            f"fsbpyr: {filename} is inside the executable, which holds the project's Python "
            "files only; the files a script reads next to its __file__ are read next to the "
            f"executable, from {_shown(_PREFIX, '/'.join(parts))}: deploy the file there\n"
        )
    record = _fsbpyr.find_module(parts[0])
    if record is None or record[1] not in (PACKAGE, NAMESPACE):  # <exe>/x.conf, a flat module's
        return (
            f"fsbpyr: {filename} is inside the executable; a top-level module's __file__ is a "
            "path in it and the files next to the module are not (--fs-package takes packages "
            "only): read the file from a path outside the executable\n"
        )
    for depth in range(1, len(parts) + 1):  # the outermost regular package holds the others
        name = ".".join(parts[:depth])
        found = _fsbpyr.find_module(name)
        if found is None or found[1] not in (PACKAGE, NAMESPACE):
            break
        if found[1] == PACKAGE:
            return (
                f"fsbpyr: {filename} is inside the executable; {name} reads files next to its "
                f"__file__: rebuild with --fs-package {name}\n"
            )
    return (
        f"fsbpyr: {filename} is inside the executable; {parts[0]} is a directory without an "
        "__init__.py (a namespace package), which --fs-package does not take: add an "
        "__init__.py to the package that reads the file and rebuild with --fs-package, or read "
        "the file from a path outside the executable\n"
    )


def _resolve(entrypoint: str) -> object:
    module_name, _, attribute = entrypoint.partition(":")
    target: object = __import__(module_name, fromlist=["__name__"])
    for part in attribute.split(".") if attribute else ():
        target = getattr(target, part)
    return target


def _script_directory(entrypoint: str) -> None:
    """A target below the scripts root runs as `python dir/script.py` does: the directory of
    its module comes first on sys.path, so its siblings import. That directory is where
    ``__file__`` says (``_shown``): next to the executable."""
    if entrypoint.startswith(_SCRIPTS):
        module = entrypoint.partition(":")[0]
        below = module.rpartition(".")[0].removeprefix(_SCRIPTS[:-1]).replace(".", "/")
        sys.path.insert(0, _BIN + below[1:] if below else _BIN[:-1] or "/")


def _check_entrypoint(entrypoint: str) -> None:
    """The selftest of one target: import the module and look the attribute up, without calling.
    A script of a project without a build system is only found: its top-level code does the
    work (deploy, delete, restart) and must not run on the build host."""
    _script_directory(entrypoint)
    if ":" not in entrypoint and entrypoint.startswith(_SCRIPTS):
        record = _fsbpyr.find_module(entrypoint)
        if record is None:
            raise ModuleNotFoundError(f"No module named {entrypoint}", name=entrypoint)
        if record[1] == MODULE:
            return
    _resolve(entrypoint)
    if ":" not in entrypoint and hasattr(sys.modules[entrypoint], "__path__"):
        from importlib.util import find_spec  # noqa: PLC0415

        if find_spec(f"{entrypoint}.__main__") is None:  # runpy would run it: found, not run
            raise ModuleNotFoundError(
                f"No module named {entrypoint}.__main__; {entrypoint!r} is a package and "
                "cannot be directly executed"
            )


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


def _execve_argv0() -> None:
    """``sys.argv[0]`` as a venv's script gets it, the path ``execve`` was given made absolute
    (D14). The caller's own name stays when its basename is not that path's (``exec -a``), and
    so does the launcher's re-execution (``/proc/self/exe``, ``/dev/fd/N``). The common start
    costs two string splits."""
    execfn, argv = INFO["execfn"], sys.argv
    if execfn is None or not argv or (execfn == argv[0] and execfn.startswith("/")):
        return
    if execfn.rpartition("/")[2] != argv[0].rpartition("/")[2] or execfn.startswith("/dev/fd/"):
        return
    if execfn == "/proc/self/exe":
        return
    if not execfn.startswith("/"):
        try:
            directory = posix.getcwd()
        except OSError:  # the start's directory is gone: the name stays
            return
        while execfn.startswith("./"):  # not collapsed: ".." after a link names another file
            execfn = execfn[2:].lstrip("/")
        execfn = f"{directory.rstrip('/')}/{execfn}"
    argv[0] = execfn


def _restart() -> None:
    """``[sys.executable, <flags>, sys.argv[0], *args]``, the restart idiom of gunicorn and
    Django, runs the app with ``sys.argv == [sys.argv[0], *args]``, as in a venv. The common
    start costs a scan of argv: only a path or the executable's name may be the restart."""
    argv = sys.argv
    names = (argv[0].rpartition("/")[2], sys.executable.rpartition("/")[2])
    if not any("/" in arg or arg in names for arg in argv[1:]):
        return
    from _fsbpyr_rt.restart import restart_argv  # type: ignore[import-not-found]  # noqa: PLC0415

    if (rewritten := restart_argv(argv)) is not None:
        argv[:] = rewritten


def _interpreter_child(commands: "dict[str, str] | None" = None) -> bool:
    """The app re-executed through ``sys.executable`` with ``-m MODULE``, ``-c CODE`` or a
    script of the payload runs that instead with interpreter-reexec (D23), and is refused
    without the setting (D44), unless the script is one of the app's own commands. A start named
    for one of the ``commands`` of a multi-command executable, under another name than the
    executable's, is that command's own (owner decision 2026-10-25)."""
    argv = sys.argv
    flagged = "-m" in argv[1:-1] or "-c" in argv[1:-1]
    roots = (_PREFIX, _BIN) if _SCRIPT_APP else _PREFIX  # a script is named by its __file__
    if not flagged and not any(arg.startswith(roots) for arg in argv[1:]):
        return False  # the common case costs three scans of argv
    if commands is not None:
        from _fsbpyr_rt.commands import is_sibling  # type: ignore[import-not-found]  # noqa: PLC0415

        if is_sibling(commands, argv[0], sys.executable):
            return False
    from _fsbpyr_rt.reexec import run_child  # type: ignore[import-not-found]  # noqa: PLC0415

    targets = commands.values() if commands is not None else (INFO["entrypoint"],)
    overlay = _BIN if _SCRIPT_APP else None
    handled: bool = run_child(argv, INFO["build_info"], _PREFIX, tuple(targets), overlay)
    return handled


def _service_policies() -> None:
    """JOURNAL_STREAM and SIGTERM as process 1 (D14). The common start costs an environ lookup
    and a pid check; the rest is imported only when either applies."""
    journal = posix.environ.get(b"JOURNAL_STREAM")
    pid = posix.getpid()
    if journal is not None or pid == 1:
        from _fsbpyr_rt.service import start  # type: ignore[import-not-found]  # noqa: PLC0415

        start(journal, pid)


def _install_links() -> None:
    """FSBPYR_INSTALL_LINKS=DIR links the commands into DIR and exits (D14). The common start
    costs an environ lookup."""
    if directory := posix.environ.get(b"FSBPYR_INSTALL_LINKS"):
        from _fsbpyr_rt.links import install  # type: ignore[import-not-found]  # noqa: PLC0415

        sys.exit(install(directory, ROOT, INFO["entrypoint"], INFO["app_name"]))


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
    _install_links()
    _execve_argv0()
    _restart()
    entrypoint = INFO["entrypoint"]
    commands = None
    if "\n" in entrypoint:  # a table of commands: the only cost of the common single command
        from _fsbpyr_rt.commands import parse  # noqa: PLC0415

        commands = parse(entrypoint)
    _service_policies()  # also for the -m/-c helper starts of an app that is process 1
    if _multiprocessing_child() or _interpreter_child(commands):
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
    if posix.environ.get(b"FSBPYR_SELFTEST"):  # every command's target, whatever argv[0] is
        for target in commands.values() if commands is not None else (entrypoint,):
            _check_entrypoint(target)
        print(f"fsbpyr selftest ok: {INFO['app_name']}")
        return
    if commands is not None:
        from _fsbpyr_rt.commands import choose  # noqa: PLC0415

        entrypoint = choose(commands, sys.argv)
    _script_directory(entrypoint)
    if ":" not in entrypoint:
        import runpy  # noqa: PLC0415

        runpy._run_module_as_main(entrypoint, alter_argv=False)  # type: ignore[attr-defined]
        return
    main = _resolve(entrypoint)
    if not callable(main):
        raise TypeError(f"entry point {entrypoint!r} is not callable")
    sys.exit(main())
