"""python-build-standalone distributions: verified download, safe extraction, link metadata."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..compat import tarfile
from ..text import tail_lines
from .hold import hold
from .pins import PbsPin

_INITTAB_ENTRY = re.compile(r'\{"([A-Za-z0-9_.]+)",\s*PyInit_')
_LLVM_BITCODE = re.compile(r"llvm-bitcode:(\d+)\.")
_CHUNK = 1 << 20
_LIBZSTD = "build/lib/libzstd.a"
_ZSTD_VERSION_MEMBER = b"zstd_common.o"  # holds the string ZSTD_versionString() returns
_AR_MAGIC = b"!<arch>\n"
_AR_HEADER = 60
_ZSTD_VERSION = re.compile(rb"\0(\d+\.\d+\.\d+)\0")
_ERROR_TAIL = 500


class PbsError(RuntimeError):
    """Download, verification or layout problem with a PBS distribution."""


@dataclass(frozen=True, slots=True)
class LinkInputs:
    libpython: Path
    extra_objects: tuple[Path, ...]  # built-in extension objects missing from libpython.a
    static_libs: tuple[Path, ...]
    system_libs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PbsDist:
    """An extracted PBS "full" distribution (the directory containing PYTHON.json)."""

    root: Path
    info: dict[str, Any]

    @classmethod
    def load(cls, root: Path) -> PbsDist:
        try:
            info = json.loads((root / "PYTHON.json").read_text())
        except (OSError, ValueError) as exc:
            raise PbsError(f"{root} is not a PBS distribution: {exc}") from exc
        return cls(root, info)

    @property
    def version(self) -> str:
        return str(self.info["python_version"])

    @property
    def python(self) -> str:
        return str(self.info["python_major_minor_version"])

    @property
    def tag(self) -> str:
        return str(self.info["python_tag"])

    @property
    def triple(self) -> str:
        return str(self.info["target_triple"])

    @property
    def hexversion(self) -> int:
        return int(self.info["python_implementation_hex_version"])

    @property
    def bytecode_magic(self) -> bytes:
        return bytes.fromhex(self.info["python_bytecode_magic_number"])

    @property
    def llvm_major(self) -> int:
        match = _LLVM_BITCODE.match(self.info["build_info"]["object_file_format"])
        if match is None:
            raise PbsError("distribution objects are not LLVM bitcode; unsupported flavor")
        return int(match.group(1))

    @property
    def interpreter(self) -> Path:
        return self.root / str(self.info["python_exe"])

    @property
    def stdlib(self) -> Path:
        return self.root / str(self.info["python_paths"]["stdlib"])

    @property
    def include(self) -> Path:
        return self.root / str(self.info["python_paths"]["include"])

    def builtin_modules(self) -> list[str]:
        """Modules registered in the inittab that PBS compiled into libpython."""
        inittab = (self.root / self.info["build_info"]["inittab_source"]).read_text()
        known = self.info["build_info"]["extensions"]
        return sorted(set(_INITTAB_ENTRY.findall(inittab)) & known.keys())


def link_inputs(dist: PbsDist, archive_members: set[str]) -> LinkInputs:
    """What to link besides libpython.a so every built-in module resolves (S1)."""
    build = dist.info["build_info"]
    extra: list[Path] = []
    static: list[Path] = []
    system = [link["name"] for link in build["core"]["links"] if link.get("system")]
    for name in dist.builtin_modules():
        variant = build["extensions"][name][0]
        for obj in variant["objs"]:
            path = dist.root / obj
            if obj.rsplit("/", 1)[-1] not in archive_members and path not in extra:
                extra.append(path)
        for link in variant["links"]:
            if link.get("path_static") and dist.root / link["path_static"] not in static:
                static.append(dist.root / link["path_static"])
            elif link.get("system") and link["name"] not in system:
                system.append(link["name"])
    # The launcher decodes zstd itself. 3.14's and 3.15's _zstd link libzstd.a; 3.12 and 3.13
    # have no _zstd, so no entry names the archive that PBS still ships (Phase 9 spike).
    zstd = dist.root / _LIBZSTD
    if zstd.is_file() and zstd not in static:
        static.append(zstd)
    return LinkInputs(
        libpython=dist.root / build["core"]["static_lib"],
        extra_objects=tuple(extra),
        static_libs=tuple(static),
        system_libs=tuple(system),
    )


_ASK_TIMEOUT = 60
_COMPONENTS = """\
import decimal, json, pyexpat, sqlite3, ssl, zlib
print(json.dumps({"expat": pyexpat.EXPAT_VERSION, "libmpdec": decimal.__libmpdec_version__,
                  "openssl": ssl.OPENSSL_VERSION, "sqlite": sqlite3.sqlite_version,
                  "zlib": zlib.ZLIB_RUNTIME_VERSION}))
"""
_EXTENSION_SUFFIXES = """\
import importlib.machinery, json
print(json.dumps(importlib.machinery.EXTENSION_SUFFIXES))
"""


def _ask(interpreter: Path, script: str, what: str) -> object:
    """What ``script``, run by ``interpreter``, prints as JSON."""
    try:
        result = subprocess.run([interpreter, "-I", "-c", script], capture_output=True,
                                text=True, timeout=_ASK_TIMEOUT, check=False)  # fmt: skip
        answer = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise PbsError(f"cannot run {interpreter} to read its {what}: {exc}") from exc
    if answer is None:
        raise PbsError(f"{interpreter} did not report its {what}: "
                       f"{tail_lines(result.stderr.strip(), _ERROR_TAIL)}")  # fmt: skip
    return answer


def component_versions(interpreter: Path) -> dict[str, str]:
    """The versions of the libraries ``interpreter`` carries, as it reports them: the ones a
    security fix concerns, recorded in the runtime and in every app (REP-3)."""
    versions = _ask(interpreter, _COMPONENTS, "library versions")
    if not isinstance(versions, dict):
        raise PbsError(f"{interpreter} did not report its library versions: {versions!r}")
    return {key: str(value) for key, value in versions.items()}


def zstd_version(dist: PbsDist) -> str | None:
    """The version of the ``libzstd.a`` the launcher links (every Python's, 3.12 and 3.13 too,
    which have no ``_zstd`` to ask), read from the one string its ``zstd_common.o`` holds; None
    when there is no archive, no such member or not exactly one version in it."""
    try:
        data = (dist.root / _LIBZSTD).read_bytes()
    except OSError:
        return None
    position = len(_AR_MAGIC)
    while data.startswith(_AR_MAGIC) and position + _AR_HEADER <= len(data):
        name = data[position:position + 16].rstrip(b" ").removesuffix(b"/")
        try:
            size = int(data[position + 48:position + 58])
        except ValueError:
            return None
        start = position + _AR_HEADER
        if name == _ZSTD_VERSION_MEMBER:
            found = set(_ZSTD_VERSION.findall(data[start:start + size]))
            return found.pop().decode() if len(found) == 1 else None
        position = start + size + size % 2
    return None


def extension_suffixes(interpreter: Path) -> tuple[str, ...]:
    """The file name suffixes ``interpreter`` imports extension modules by, in its order.
    PYTHON.json lists them too, but its list lags: 3.15.1's names neither ``.abi3t.so``
    nor the abi3 suffixes with the architecture, which wheels for 3.15 use."""
    suffixes = _ask(interpreter, _EXTENSION_SUFFIXES, "extension suffixes")
    if not isinstance(suffixes, list) or not all(isinstance(s, str) for s in suffixes):
        raise PbsError(f"{interpreter} did not report its extension suffixes: {suffixes!r}")
    return tuple(suffixes)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


# Downloads are retried after a network failure (a TLS handshake that times out, a reset
# connection), never after an HTTP error: each attempt starts over, after these pauses.
DOWNLOAD_ATTEMPTS = 4
_RETRY_PAUSES_S = (2.0, 5.0, 10.0)


def download(url: str, sha256: str, target: Path) -> None:
    """Download ``url`` to ``target`` (atomically), checking its pinned SHA-256."""
    if not url.startswith("https://"):
        raise PbsError(f"refusing to download {url}: only https URLs are pinned")
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            partial = _download_once(url, target.parent)
            break
        except OSError as exc:  # URLError, TimeoutError and ConnectionError are OSErrors
            if attempt == DOWNLOAD_ATTEMPTS or isinstance(exc, urllib.error.HTTPError):
                raise
            pause = _RETRY_PAUSES_S[attempt - 1]
            print(f"fsbpyr: {url}: {exc}; retrying in {pause:.0f} s", file=sys.stderr)
            time.sleep(pause)
    if sha256_of(partial) != sha256:
        partial.unlink()
        raise PbsError(f"SHA-256 mismatch for {url}")
    partial.replace(target)


def _download_once(url: str, directory: Path) -> Path:
    """``url`` into a new ``.part`` file in ``directory``; nothing is left behind on failure."""
    with tempfile.NamedTemporaryFile(dir=directory, delete=False, suffix=".part") as out:
        partial = Path(out.name)
        try:
            with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 pinned https
                shutil.copyfileobj(response, out, _CHUNK)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    return partial


def fetch(pin: PbsPin, cache: Path) -> PbsDist:
    """Download (once), verify and extract the pinned distribution under ``cache``."""
    archive = cache / "downloads" / f"{pin.name}.tar.zst"
    dest = cache / "pbs" / pin.name
    if (dest / "python" / "PYTHON.json").exists() and hold(dest):  # held: never collected (D38)
        return PbsDist.load(dest / "python")
    if not archive.exists():
        download(pin.url, pin.sha256, archive)
    elif sha256_of(archive) != pin.sha256:
        raise PbsError(f"cached {archive} does not match its pinned SHA-256; delete it")
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".extract-"))
    try:
        with tarfile.open(archive, "r:zst") as tar:
            tar.extractall(staging, filter="data")
        if not (staging / "python" / "PYTHON.json").exists():
            raise PbsError(f"{archive} does not contain python/PYTHON.json")
        try:
            staging.rename(dest)
        except OSError:
            if not (dest / "python" / "PYTHON.json").exists():  # not a concurrent winner
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    hold(dest)
    return PbsDist.load(dest / "python")
