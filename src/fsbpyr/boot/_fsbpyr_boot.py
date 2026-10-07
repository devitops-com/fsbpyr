"""Bootstrap of every fsbpyr executable (plan §3.3 step 7, §3.4).

Stored uncompressed in the payload and handed to CPython as a frozen module; the launcher runs
``_fsbpyr_boot.run()``. Only built-in and frozen modules may be imported at module level, so
that startup never touches the filesystem and never decompresses anything it does not need.

The payload appears to Python as one virtual directory tree rooted at the executable path:
``sys.path == [<exe>]`` and module ``a.b`` has ``__file__ == <exe>/a/b.py``. A path hook claims
``<exe>`` and every ``<exe>/<dir>`` entry, so the stock PathFinder resolves packages and
subpackages exactly as it does for a site-packages directory.
"""

import marshal
import posix
import sys
from _frozen_importlib import ModuleSpec

import _fsbpyr

TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations are lazy (PEP 649): nothing below is imported at runtime
    from importlib.abc import Loader as _LoaderBase
    from types import CodeType, ModuleType, TracebackType
else:
    _LoaderBase = object

MODULE, PACKAGE, NAMESPACE, EXTENSION = 1, 2, 3, 4
NONE = _fsbpyr.NONE
INFO = _fsbpyr.info()
ROOT = INFO["executable"]
_PREFIX = ROOT + "/"

# (index, kind, flags, code, source, file, dist, path) as returned by _fsbpyr.find_module
type Record = tuple[int, int, int, int, int, int, int, str]


class PayloadLoader(_LoaderBase):
    """Loads pure-Python modules and packages from payload records (spec.loader_state)."""

    def create_module(self, spec: ModuleSpec) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        spec = module.__spec__
        if spec is None:
            raise ImportError(f"{module.__name__!r} has no spec", name=module.__name__)
        record: Record = spec.loader_state
        exec(marshal.loads(_fsbpyr.read(record[3])), module.__dict__)  # noqa: S102

    def _record(self, fullname: str) -> Record:
        record = _fsbpyr.find_module(fullname)
        if record is None or record[1] not in (MODULE, PACKAGE):
            raise ImportError(f"no module named {fullname!r} in the payload", name=fullname)
        return record

    def get_code(self, fullname: str) -> CodeType:
        code: CodeType = marshal.loads(_fsbpyr.read(self._record(fullname)[3]))
        return code

    def get_source(self, fullname: str) -> str | None:
        source = self._record(fullname)[4]
        if source == NONE:
            return None
        from _frozen_importlib_external import decode_source  # noqa: PLC0415  frozen, rarely used

        return decode_source(_fsbpyr.read(source))

    def is_package(self, fullname: str) -> bool:
        return self._record(fullname)[1] == PACKAGE

    def get_filename(self, fullname: str) -> str:
        return _PREFIX + self._record(fullname)[7]


LOADER = PayloadLoader()


class PayloadPathFinder:
    """Path-entry finder for one virtual directory of the payload ('' is the root)."""

    def __init__(self, directory: str) -> None:
        self.directory = directory

    def find_spec(self, fullname: str, target: object = None) -> ModuleSpec | None:
        if fullname.rpartition(".")[0].replace(".", "/") != self.directory:
            return None
        record = _fsbpyr.find_module(fullname)
        if record is None or record[1] not in (MODULE, PACKAGE):
            return None  # namespace packages and extensions arrive in later phases
        path = record[7]
        spec = ModuleSpec(
            fullname,
            LOADER,
            origin=_PREFIX + path,
            loader_state=record,
            is_package=record[1] == PACKAGE,
        )
        spec.has_location = True
        if record[1] == PACKAGE:
            spec.submodule_search_locations = [_PREFIX + path.rpartition("/")[0]]
        return spec

    def invalidate_caches(self) -> None:
        pass


def path_hook(entry: str) -> PayloadPathFinder:
    if entry == ROOT:
        return PayloadPathFinder("")
    if entry.startswith(_PREFIX):
        return PayloadPathFinder(entry[len(_PREFIX) :])
    raise ImportError("not an fsbpyr payload path", path=entry)


def _rehome_startup_modules() -> None:
    """Frozen STARTUP packages get ``__path__ == []``; point them into the payload tree."""
    for name, module in list(sys.modules.items()):
        record = _fsbpyr.find_module(name)
        if record is None or getattr(module, "__file__", None) is not None:
            continue
        module.__file__ = _PREFIX + record[7]
        if record[1] == PACKAGE and getattr(module, "__path__", None) == []:
            location = _PREFIX + record[7].rpartition("/")[0]
            module.__path__ = [location]
            if module.__spec__ is not None:
                module.__spec__.submodule_search_locations = [location]


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


def run() -> None:
    sys.path_hooks.insert(0, path_hook)
    sys.path[:] = [ROOT]
    sys.path_importer_cache.clear()
    _rehome_startup_modules()
    if sys.excepthook is sys.__excepthook__:
        sys.excepthook = _excepthook
    entrypoint = INFO["entrypoint"]
    if posix.environ.get(b"FSBPYR_SELFTEST"):
        _resolve(entrypoint.partition(":")[0])
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
