"""Runtime cache: build, save, load and locate runtimes (plan D3, §3.7)."""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import pwd
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__, targets
from ..boot._fsbpyr_rt import gc
from ..payload.convert import spec_from_payload
from ..payload.format import PayloadError, tree_digest
from ..payload.reader import Executable, Payload
from ..payload.spec import File, Module, PayloadSpec
from ..payload.writer import encode_payload
from . import notices, pbs, pins, stdlib, stub, sysroot
from .hold import hold
from .toolchain import describe, find_toolchain

BOOT_PACKAGES = ("_fsbpyr_boot", "_fsbpyr_rt")  # regenerated for every build, never runtime content
LAYOUT_VERSION = 10  # bump when the stdlib selection, its canonical bytecode, the layout or the
# launcher's link changes (7: a reproducible link; 8: runtime.json records its inputs and
# components, which build_info carries, REP-3; the stdlib fragment carries the licence notices
# of what the runtime links, REP-4; 9: zlib-ng's notice is left out, PBS ships it from 20261020;
# 10: zstd's notice is in every runtime, 3.12's and 3.13's too)
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
    """Create ``path`` (mode 0700) or verify it: owned by us, not writable by group or others
    (made so, through a descriptor), and the way to it from "/" trusted (gc.way_problem, D38:
    no other user can redirect it; a symlink at ``path`` must be root's or ours).

    Cached stubs end up inside every executable built from them, so a cache directory that
    another user could write to would let them plant code (security review, Phase 2).
    """
    _trusted_way(path.parent)
    for parent in reversed([p for p in path.parents if not p.exists()]):
        with contextlib.suppress(FileExistsError):
            parent.mkdir(mode=0o755)  # writable by no one else, whatever the umask
    with contextlib.suppress(FileExistsError):
        path.mkdir(mode=0o700)
    link = path.lstat()
    if stat.S_ISLNK(link.st_mode) and link.st_uid not in (0, os.geteuid()):
        raise RuntimeCacheError(f"refusing cache directory {path}: {gc.FOREIGN}")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        check_owner(path, owner=info.st_uid, euid=os.geteuid())
        if info.st_mode & 0o077:
            os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
    _trusted_way(path)  # the whole way now: a part someone else made meanwhile is caught
    return path


def _trusted_way(path: Path) -> None:
    if (problem := gc.way_problem(str(path))) is not None:
        raise RuntimeCacheError(problem.text())


def _digest(path: Path) -> str:
    return tree_digest(path.read_bytes()).hex()


def _home() -> Path:
    """$HOME if absolute, else (unset, empty or relative) the passwd entry's, as the boot code
    and the launcher have it (native.home_directory, due.c)."""
    home = os.environ.get("HOME", "")
    if os.path.isabs(home):
        return Path(home)
    try:
        entry = pwd.getpwuid(os.getuid()).pw_dir
    except KeyError:
        entry = ""
    if not os.path.isabs(entry):
        raise RuntimeCacheError(
            "cannot find the home directory; set HOME, XDG_CACHE_HOME or FSBPYR_CACHE_DIR"
        )
    return Path(entry)


def cache_home() -> Path:
    """${XDG_CACHE_HOME:-~/.cache}; a relative XDG_CACHE_HOME is ignored, as the XDG spec has
    it and as the boot code does (native.cache_directory)."""
    base = os.environ.get("XDG_CACHE_HOME", "")
    return Path(base) if os.path.isabs(base) else _home() / ".cache"


def cache_root() -> Path:
    if explicit := os.environ.get("FSBPYR_CACHE_DIR"):
        return Path(explicit)
    return cache_home() / "fsbpyr"


def host_triple() -> str:
    try:
        return targets.host_target().triple
    except targets.UnsupportedHost as exc:
        raise RuntimeCacheError(str(exc)) from exc


def runtime_id(
    pin: pins.PbsPin, *, sanitize: bool = False, baseline: str = pins.DEFAULT_BASELINE
) -> str:
    """``sanitize``: the ASan/UBSan test variant (D38), an identity of its own. ``baseline``:
    the glibc the stub is linked for; the sysroot's identity is in the digest, so runtimes of
    two baselines never share an id."""
    sysroot_pin = pins.sysroot_for(pin.triple, baseline)
    launcher = stub.launcher_digest(pin.triple, sanitize=sanitize)
    identity = "\0".join((launcher, pin.name, pin.sha256, sysroot_pin.identity,
                          str(LAYOUT_VERSION)))  # fmt: skip
    digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
    suffix = "-sanitized" if sanitize else ""
    return f"cp{pin.python.replace('.', '')}-{pin.triple}-{digest}{suffix}"


def runtime_inputs(pin: pins.PbsPin, baseline: str = pins.DEFAULT_BASELINE) -> dict[str, object]:
    """What the runtime was made from, for build_info's ``runtime_inputs``."""
    return {
        "pbs": {"release": pin.release, "name": pin.name, "sha256": pin.sha256},
        "sysroot": pins.sysroot_for(pin.triple, baseline).identity,
        "layout": LAYOUT_VERSION,
    }


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
    toolchain: dict[str, str] = field(default_factory=dict)  # clang and lld versions, information
    inputs: dict[str, object] = field(default_factory=dict)  # build_info's runtime_inputs
    components: dict[str, str] = field(default_factory=dict)  # build_info's runtime_components
    baseline: str = pins.DEFAULT_BASELINE  # the glibc its stub is linked for (2.34 or 2.28)

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
                blocks=False,  # frames kept as built: every app build packs them into its blocks
            )
            (staging / _STDLIB).write_bytes(encode_payload(fragment))
            meta = {
                **self._metadata(),
                "digests": {name: _digest(staging / name) for name in (_STUB, _STDLIB)},
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
            "toolchain": dict(self.toolchain),  # information only: not in runtime_id (D3)
            "inputs": dict(self.inputs),  # recorded in every app (build_info)
            "components": dict(self.components),
            "baseline": self.baseline,
        }

    @classmethod
    def load(cls, directory: Path) -> Runtime:
        paths = [directory / name for name in (_STUB, _STDLIB, _META)]
        if not all(path.is_file() for path in paths):
            raise RuntimeCacheError(f"incomplete runtime at {directory}")
        meta = json.loads(paths[2].read_text())
        if meta.get("layout") != LAYOUT_VERSION:
            raise RuntimeCacheError(f"runtime at {directory} uses an old layout; rebuild it")
        expected = meta.get("digests", {})
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
            toolchain=dict(meta.get("toolchain", {})),
            inputs=dict(meta.get("inputs", {})),
            components=dict(meta.get("components", {})),
            baseline=meta.get("baseline", pins.DEFAULT_BASELINE),  # runtimes before --glibc
        )

    @classmethod
    def from_executable(cls, path: Path) -> Runtime:
        """The runtime embedded in an fsbpyr executable: its stub bytes and the stdlib blobs,
        zstd frames copied verbatim and block members as content that the next build packs
        again (no compiler, no download, no cache; plan D4)."""
        with Executable.open(path) as exe:
            try:
                exe.verify()  # what goes into every app it builds must be what was built
            except PayloadError as exc:
                raise RuntimeCacheError(f"{path} was modified or damaged ({exc})") from exc
            payload = exe.payload
            info = json.loads(payload.build_info)
            try:  # only from this format: its launcher reads nothing else
                fragment = spec_from_payload(payload)
            except PayloadError as exc:
                raise RuntimeCacheError(f"{path} cannot serve this fsbpyr's builds: {exc}") from exc
            stub_bytes = exe.stub_prefix()
            hexversion, magic = payload.python_hexversion, payload.bytecode_magic
        if "runtime_tag" not in info:
            raise RuntimeCacheError(f"{path} does not record its runtime")
        return cls(
            tag=info["runtime_tag"],
            runtime_id=info["runtime"],
            python_version=info["python"],
            hexversion=hexversion,
            bytecode_magic=magic,
            extension_suffixes=tuple(info["extension_suffixes"]),
            stub=stub_bytes,
            # app content always belongs to a dist; boot code is regenerated for each build
            modules=tuple(
                m for m in fragment.modules
                if m.dist is None and m.name.partition(".")[0] not in BOOT_PACKAGES
            ),
            files=tuple(f for f in fragment.files if f.dist is None),
            # what the building executable recorded: nothing here could find it out again
            inputs=dict(info.get("runtime_inputs", {})),
            components=dict(info.get("runtime_components", {})),
            baseline=_baseline_of(info.get("runtime_inputs", {}).get("sysroot", "")),
        )  # fmt: skip


def _baseline_of(sysroot_identity: str) -> str:
    """The glibc baseline whose sysroot has this identity (build_info's runtime_inputs); the
    default one for an identity no pin has any more."""
    for baseline, table in ((pins.DEFAULT_BASELINE, pins.SYSROOTS), ("2.28", pins.EL8_SYSROOTS)):
        if any(pin.identity == sysroot_identity for pin in table.values()):
            return baseline
    return pins.DEFAULT_BASELINE


def running_executable() -> Path | None:
    """The fsbpyr executable this process runs in, if any: the running inode, not
    ``sys.executable``, which names whatever file is at that path now (an upgrade, say)."""
    return Path("/proc/self/exe") if "_fsbpyr" in sys.builtin_module_names else None


def select_runtime(
    python: str,
    cache: Path | None = None,
    baseline: str = pins.DEFAULT_BASELINE,
    *,
    offline: bool = False,
) -> Runtime:
    """Our own embedded runtime when it matches ``python`` and the glibc ``baseline``
    (self-hosting); a released executable's own stdlib with its release's stub of another
    baseline (stubs.py; ``offline``: placed or cached, never downloaded); else the cache."""
    own = running_executable()
    if own is not None:
        runtime = Runtime.from_executable(own)
        if runtime.python_version.startswith(python + "."):
            if runtime.baseline == baseline:
                return runtime
            from . import stubs  # noqa: PLC0415  stubs.py needs this module

            if (paired := stubs.paired(runtime, baseline, cache, offline=offline)) is not None:
                return paired
    return find_runtime(python, cache, baseline=baseline)


def find_runtime(
    python: str,
    cache: Path | None = None,
    *,
    sanitize: bool = False,
    baseline: str = pins.DEFAULT_BASELINE,
) -> Runtime:
    pin = pins.pin_for(python, host_triple())
    name = runtime_id(pin, sanitize=sanitize, baseline=baseline)
    directory = secure_directory(cache or cache_root()) / "runtimes" / name
    if not directory.exists() or not hold(directory):  # held: no collection removes it (D38)
        if sanitize:  # a test variant (D38): the test run builds it
            raise RuntimeCacheError(f"no sanitized runtime for Python {python}")
        other = baseline != pins.DEFAULT_BASELINE
        raise RuntimeCacheError(
            f"no runtime for Python {python}{f' and glibc {baseline}' if other else ''}; run "
            f"`fsbpyr runtime build --python {python}{f' --glibc {baseline}' if other else ''}`"
            + ("" if other else ", or use the release binary, which carries its own")
        )
    return Runtime.load(directory)


def build_runtime(  # noqa: PLR0913  # what to build, where, how to say so, which variant
    python: str,
    *,
    cache: Path | None = None,
    clang: Path | None = None,
    log: Callable[[str], None] = print,
    sanitize: bool = False,
    baseline: str = pins.DEFAULT_BASELINE,
) -> Runtime:
    """Fetch PBS, link the stub (against the sysroot of the glibc ``baseline``) and compile the
    stdlib; cached under the runtime id."""
    cache = secure_directory(cache or cache_root())
    pin = pins.pin_for(python, host_triple())
    sysroot_pin = pins.sysroot_for(pin.triple, baseline)  # before any download
    directory = cache / "runtimes" / runtime_id(pin, sanitize=sanitize, baseline=baseline)
    if (directory / _META).exists() and hold(directory):
        log(f"runtime {directory.name} is already built")
        return Runtime.load(directory)
    log(f"fetching {pin.name}")
    dist = pbs.fetch(pin, cache)
    toolchain = find_toolchain(dist.llvm_major, explicit=clang)
    log(f"fetching glibc {sysroot_pin.glibc} sysroot {sysroot_pin.name}")
    toolchain = dataclasses.replace(toolchain, sysroot=sysroot.fetch_sysroot(sysroot_pin, cache))
    how = "ASan and UBSan" if sanitize else "full LTO, this takes a minute or two"
    log(f"linking launcher with {toolchain.clang} ({how})")
    with tempfile.TemporaryDirectory(prefix="fsbpyr-stub-") as tmp:
        stub_path = Path(tmp) / _STUB
        for line in stub.build_stub(dist, toolchain, stub_path, sanitize=sanitize,
                                    identity=directory.name):
            log(line)  # every warning shown, not only failures
        stub_bytes = stub_path.read_bytes()
    components = pbs.component_versions(dist.interpreter)
    if (zstd := pbs.zstd_version(dist)) is not None:
        components["zstd"] = zstd  # linked by the launcher, whatever the Python
    log("compiling the standard library")
    modules, files = stdlib.fragment(dist, warn=log)
    files = (*files, *notices.files(dist))
    runtime = Runtime(
        tag=f"{dist.tag}-{dist.triple}",
        runtime_id=directory.name,
        python_version=dist.version,
        hexversion=dist.hexversion,
        bytecode_magic=dist.bytecode_magic,
        extension_suffixes=pbs.extension_suffixes(dist.interpreter),
        stub=stub_bytes,
        modules=modules,
        files=files,
        toolchain=describe(toolchain),
        inputs=runtime_inputs(pin, baseline),
        components=components,
        baseline=baseline,
    )
    runtime.save(directory)
    hold(directory)
    log(f"runtime {directory.name} ready")
    return runtime


def list_runtimes(cache: Path | None = None) -> list[tuple[str, str, str]]:
    """(runtime id, Python version, glibc baseline) of the runtime embedded in the running
    executable, if any, then of every complete runtime in the cache."""
    found = []
    own = running_executable()
    if own is not None:
        embedded = Runtime.from_executable(own)
        found.append((embedded.runtime_id, embedded.python_version, embedded.baseline))
    for meta in sorted(((cache or cache_root()) / "runtimes").glob(f"*/{_META}")):
        data = json.loads(meta.read_text())
        found.append((data["runtime_id"], data["python_version"],
                      data.get("baseline", pins.DEFAULT_BASELINE)))  # fmt: skip
    return found
