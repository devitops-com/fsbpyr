"""Runtime cache: build, save, load and locate runtimes (plan D3, §3.7)."""

import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import __version__
from ..payload.convert import spec_from_payload
from ..payload.reader import Payload
from ..payload.spec import File, Module, PayloadSpec
from ..payload.writer import encode_payload
from . import pbs, pins, stdlib, stub
from .toolchain import find_toolchain

LAYOUT_VERSION = 2  # bump when the stdlib selection or the runtime directory layout changes
_STUB = "stub"
_STDLIB = "stdlib.fsbp"
_META = "runtime.json"


class RuntimeCacheError(RuntimeError):
    """No usable runtime is available."""


def check_owner(path: Path, *, owner: int, euid: int) -> None:
    if owner != euid:
        raise RuntimeCacheError(
            f"refusing cache directory {path}: owned by uid {owner}, not {euid}"
        )


def secure_directory(path: Path) -> Path:
    """Create ``path`` (mode 0700) or verify it: owned by us, not writable by group or others.

    Cached stubs end up inside every executable built from them, so a cache directory that
    another user could write to would let them plant code (security review, Phase 2).
    """
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    check_owner(path, owner=info.st_uid, euid=os.geteuid())
    if info.st_mode & 0o077:
        path.chmod(0o700)
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cache_root() -> Path:
    if explicit := os.environ.get("FSBPYR_CACHE_DIR"):
        return Path(explicit)
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "fsbpyr"


def host_triple() -> str:
    if sys.platform == "linux" and platform.machine() == "x86_64":
        return "x86_64-unknown-linux-gnu"
    raise RuntimeCacheError(f"unsupported host {sys.platform}/{platform.machine()}")


def runtime_id(pin: pins.PbsPin) -> str:
    identity = f"{stub.launcher_digest()}\0{pin.name}\0{pin.sha256}\0{LAYOUT_VERSION}"
    digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
    return f"cp{pin.python.replace('.', '')}-{pin.triple}-{digest}"


@dataclass(frozen=True, slots=True)
class Runtime:
    tag: str
    runtime_id: str
    python_version: str
    hexversion: int
    bytecode_magic: bytes
    extension_suffixes: tuple[str, ...]
    stub: bytes
    modules: tuple[Module, ...]
    files: tuple[File, ...]

    def save(self, directory: Path) -> None:
        """Write atomically: a complete runtime directory appears, or nothing does.

        A complete runtime that appears concurrently (another builder) wins and is kept.
        """
        secure_directory(directory.parent)
        staging = Path(tempfile.mkdtemp(dir=directory.parent, prefix=f".{directory.name}."))
        try:
            (staging / _STUB).write_bytes(self.stub)
            (staging / _STUB).chmod(0o755)
            fragment = PayloadSpec(
                self.hexversion,
                self.bytecode_magic,
                bytes(32),
                "stdlib",
                "",
                modules=self.modules,
                files=self.files,
            )
            (staging / _STDLIB).write_bytes(encode_payload(fragment))
            meta = {
                **self._metadata(),
                "sha256": {name: _digest(staging / name) for name in (_STUB, _STDLIB)},
            }
            (staging / _META).write_text(json.dumps(meta, indent=2, sort_keys=True))
            self._publish(staging, directory)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _publish(staging: Path, directory: Path) -> None:
        if (directory / _META).exists():
            return  # a complete runtime is already there (same id, same content)
        if directory.exists():  # leftover without metadata: move it aside, then replace it
            stale = Path(tempfile.mkdtemp(dir=directory.parent, prefix=f".{directory.name}.old."))
            directory.rename(stale / "x")
            shutil.rmtree(stale, ignore_errors=True)
        try:
            staging.rename(directory)
        except OSError:
            if not (directory / _META).exists():  # not a concurrent winner
                raise

    def _metadata(self) -> dict[str, object]:
        return {
            "layout": LAYOUT_VERSION,
            "tag": self.tag,
            "runtime_id": self.runtime_id,
            "python_version": self.python_version,
            "hexversion": self.hexversion,
            "bytecode_magic": self.bytecode_magic.hex(),
            "extension_suffixes": list(self.extension_suffixes),
            "fsbpyr": __version__,
        }

    @classmethod
    def load(cls, directory: Path) -> Runtime:
        paths = [directory / name for name in (_STUB, _STDLIB, _META)]
        if not all(path.is_file() for path in paths):
            raise RuntimeCacheError(f"incomplete runtime at {directory}")
        meta = json.loads(paths[2].read_text())
        if meta.get("layout") != LAYOUT_VERSION:
            raise RuntimeCacheError(f"runtime at {directory} uses an old layout; rebuild it")
        expected = meta.get("sha256", {})
        for path in paths[:2]:
            if expected.get(path.name) != _digest(path):
                raise RuntimeCacheError(f"corrupt runtime file {path}; delete {directory}")
        fragment = spec_from_payload(Payload(paths[1].read_bytes()))
        return cls(
            tag=meta["tag"],
            runtime_id=meta["runtime_id"],
            python_version=meta["python_version"],
            hexversion=meta["hexversion"],
            bytecode_magic=bytes.fromhex(meta["bytecode_magic"]),
            extension_suffixes=tuple(meta["extension_suffixes"]),
            stub=paths[0].read_bytes(),
            modules=fragment.modules,
            files=fragment.files,
        )


def find_runtime(python: str, cache: Path | None = None) -> Runtime:
    pin = pins.pin_for(python, host_triple())
    directory = secure_directory(cache or cache_root()) / "runtimes" / runtime_id(pin)
    if not directory.exists():
        raise RuntimeCacheError(
            f"no runtime for Python {python}; run `fsbpyr runtime build --python {python}`"
        )
    return Runtime.load(directory)


def build_runtime(
    python: str,
    *,
    cache: Path | None = None,
    clang: Path | None = None,
    log: Callable[[str], None] = print,
) -> Runtime:
    """Fetch PBS, link the stub and compile the stdlib; cached under the runtime id."""
    cache = secure_directory(cache or cache_root())
    pin = pins.pin_for(python, host_triple())
    directory = cache / "runtimes" / runtime_id(pin)
    if (directory / _META).exists():
        log(f"runtime {directory.name} is already built")
        return Runtime.load(directory)
    log(f"fetching {pin.name}")
    dist = pbs.fetch(pin, cache)
    toolchain = find_toolchain(dist.llvm_major, explicit=clang)
    log(f"linking launcher with {toolchain.clang} (full LTO, this takes about a minute)")
    with tempfile.TemporaryDirectory(prefix="fsbpyr-stub-") as tmp:
        stub_path = Path(tmp) / _STUB
        stub.build_stub(dist, toolchain, stub_path)
        stub_bytes = stub_path.read_bytes()
    log("compiling the standard library")
    modules, files = stdlib.fragment(dist)
    runtime = Runtime(
        tag=f"{dist.tag}-{dist.triple}",
        runtime_id=directory.name,
        python_version=dist.version,
        hexversion=dist.hexversion,
        bytecode_magic=dist.bytecode_magic,
        extension_suffixes=dist.extension_suffixes,
        stub=stub_bytes,
        modules=modules,
        files=files,
    )
    runtime.save(directory)
    log(f"runtime {directory.name} ready")
    return runtime


def list_runtimes(cache: Path | None = None) -> list[tuple[str, str]]:
    """(runtime id, Python version) of every complete runtime in the cache."""
    found = []
    for meta in sorted(((cache or cache_root()) / "runtimes").glob(f"*/{_META}")):
        data = json.loads(meta.read_text())
        found.append((data["runtime_id"], data["python_version"]))
    return found
