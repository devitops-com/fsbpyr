"""``importlib.resources`` over payload directories (plan §3.4).

Imported only when an application calls ``importlib.resources.files()`` on a payload package, so
it may use regular stdlib modules.

A resource reached as a path (``as_file``, ``os.fspath``, ``str``) is extracted to the app's
cache and stays there (D27): code written for installed packages hands such paths to ``open``
and ``os`` functions, or keeps them after ``as_file`` returns.
"""

import contextlib
import io
import os
import pathlib
import posixpath
import warnings
from collections.abc import Iterator
from importlib.resources import as_file
from importlib.resources.abc import Traversable, TraversableResources
from typing import IO, Any

import _fsbpyr

from .native import materialize

_READ_MODES = frozenset({"r", "rt", "tr", "rb", "br"})


def _normalize(path: str) -> str | None:
    """Site-relative normalized path; None if it leaves the payload root."""
    normalized = posixpath.normpath(path) if path else "."
    if normalized == ".":
        return ""
    if normalized.startswith(("/", "../")) or normalized == "..":
        return None
    return normalized


class PayloadTraversable(Traversable):
    """A file or directory of the payload tree (read-only)."""

    def __init__(self, root: str, path: str | None) -> None:
        self._root = root
        self._path = path  # site-relative; '' is the root; None means "outside the payload"

    def virtual(self) -> str:
        """The path inside the executable (it does not exist on disk)."""
        return self._root + ("/" + self._path if self._path else "")

    def __fspath__(self) -> str:
        """The path on disk, extracting the file or directory first (OSError if the cache is
        unusable). The payload root (it would extract everything) and missing paths keep the
        virtual path."""
        if self._path and self.exists():
            return materialize(self._path)
        return self.virtual()

    def __str__(self) -> str:
        """As ``os.fspath``, but an unusable cache warns and keeps the virtual path: str() also
        formats messages, which must not fail."""
        try:
            return self.__fspath__()
        except OSError as exc:
            warnings.warn(f"{exc}; using the path inside the executable", RuntimeWarning, 2)
            return self.virtual()

    def __repr__(self) -> str:
        return f"PayloadTraversable({self.virtual()!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PayloadTraversable):
            return NotImplemented
        return (self._root, self._path) == (other._root, other._path)

    def __hash__(self) -> int:
        return hash((self._root, self._path))

    @property
    def name(self) -> str:
        return posixpath.basename(self._path or "")

    @property
    def parent(self) -> PayloadTraversable:
        return PayloadTraversable(self._root, posixpath.dirname(self._path or ""))

    def _record(self) -> tuple[int, ...] | None:
        return _fsbpyr.find_file(self._path) if self._path else None

    def is_file(self) -> bool:
        return self._record() is not None

    def is_dir(self) -> bool:
        return self._path is not None and (self._path == "" or _fsbpyr.is_dir(self._path))

    def exists(self) -> bool:
        return self.is_file() or self.is_dir()

    def iterdir(self) -> Iterator[PayloadTraversable]:
        names = _fsbpyr.list_dir(self._path) if self._path is not None else []
        if not names and self._path != "":
            raise (
                NotADirectoryError(self.virtual())
                if self.is_file()
                else FileNotFoundError(self.virtual())
            )
        for name in names:
            yield self.joinpath(name)

    def joinpath(self, *descendants: str | os.PathLike[str]) -> PayloadTraversable:
        if self._path is None:
            return self
        parts = [os.fspath(part) for part in descendants]
        return PayloadTraversable(self._root, _normalize(posixpath.join(self._path, *parts)))

    def read_bytes(self) -> bytes:
        record = self._record()
        if record is None:
            raise FileNotFoundError(2, "No such file in the executable payload", self.virtual())
        return _fsbpyr.read(record[4])

    def read_text(self, encoding: str | None = None, errors: str | None = None) -> str:
        """Like ``pathlib.Path.read_text``; the functional importlib.resources API passes errors."""
        return self.read_bytes().decode(encoding or "utf-8", errors or "strict")

    def open(self, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        if mode not in _READ_MODES:
            raise ValueError(f"payload resources are read-only; invalid mode {mode!r}")
        data = io.BytesIO(self.read_bytes())
        return data if "b" in mode else io.TextIOWrapper(data, *args, **kwargs)


@contextlib.contextmanager
def _as_file(path: PayloadTraversable) -> Iterator[pathlib.Path]:
    """The extracted file or directory, not a temporary copy: it outlives the ``with`` block."""
    if path._path == "":
        raise PermissionError(f"refusing to extract the whole executable ({path.virtual()})")
    if path._path is None or not path.exists():
        raise FileNotFoundError(2, "No such file in the executable payload", path.virtual())
    yield pathlib.Path(materialize(path._path))


as_file.register(PayloadTraversable, _as_file)  # type: ignore[attr-defined]  # singledispatch


class PayloadResourceReader(TraversableResources):
    def __init__(self, root: str, directory: str) -> None:
        self._root = root
        self._directory = directory

    def files(self) -> PayloadTraversable:
        return PayloadTraversable(self._root, self._directory)


class DirectoryReader(TraversableResources):
    """FS_REQUIRED packages: their files were extracted, so resources are real paths (and
    ``importlib.resources.as_file`` needs no temporary copy)."""

    def __init__(self, directory: str) -> None:
        self._directory = directory

    def files(self) -> pathlib.Path:
        return pathlib.Path(self._directory)
