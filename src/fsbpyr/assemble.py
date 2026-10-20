"""Compose runtime, boot code and application content into one executable (plan §3.6 9-10)."""

import _imp
import hashlib
import importlib.resources
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __version__, bytecode
from .compat import zstd
from .payload.format import PAGE_SIZE, ModuleFlag, ModuleKind, align, extractable
from .payload.spec import ZSTD_LEVEL, Blob, Dist, File, Module, PayloadSpec
from .payload.writer import commit, write_temporary
from .runtime.registry import BOOT_PACKAGES, Runtime

BOOT_MODULE, RUNTIME_PACKAGE = BOOT_PACKAGES


class AssemblyError(RuntimeError):
    """The pieces cannot be combined into a valid executable."""


def _boot_module(name: str, rel: str, source: bytes, *, filename: str, startup: bool) -> Module:
    code = bytecode.dumps(compile(source, filename, "exec", dont_inherit=True, optimize=0))
    return Module(
        name,
        rel,
        ModuleKind.PACKAGE if rel.endswith("/__init__.py") else ModuleKind.MODULE,
        code=Blob.code(code),
        source=Blob.zstd(source),
        flags=ModuleFlag.STARTUP if startup else ModuleFlag(0),
    )


def boot_modules() -> tuple[Module, ...]:
    """The boot module (STARTUP, frozen-style filename) and the lazily imported _fsbpyr_rt."""
    root = importlib.resources.files("fsbpyr.boot")
    boot_rel = f"{BOOT_MODULE}.py"
    modules = [
        _boot_module(BOOT_MODULE, boot_rel, root.joinpath(boot_rel).read_bytes(),
                     filename=f"<frozen {BOOT_MODULE}>", startup=True)
    ]  # fmt: skip
    package = root.joinpath(RUNTIME_PACKAGE)
    for entry in sorted(package.iterdir(), key=lambda e: e.name):
        if not entry.name.endswith(".py"):
            continue
        rel = f"{RUNTIME_PACKAGE}/{entry.name}"
        stem = entry.name.removesuffix(".py")
        name = RUNTIME_PACKAGE if stem == "__init__" else f"{RUNTIME_PACKAGE}.{stem}"
        modules.append(_boot_module(name, rel, entry.read_bytes(), filename=rel, startup=False))
    return tuple(modules)


def _identity(file: File) -> bytes:
    content = file.blob.content_digest()
    mode = file.mode.to_bytes(2, "little")  # cached files keep their first mode
    return file.path.encode() + b"\0" + content + mode


def generation_id(runtime_tag: str, files: tuple[File, ...]) -> bytes:
    """Native cache generation: runtime ABI plus identity of every extractable artifact (D10)."""
    digest = hashlib.sha256(runtime_tag.encode() + b"\0")
    for file in sorted(files, key=lambda f: f.path.encode()):
        if extractable(file.kind, file.flags):
            digest.update(_identity(file))
    return digest.digest()


def resources_id(files: tuple[File, ...]) -> str:
    """Identity of every payload file: resources extracted on demand (D27) live in a tree of
    this build's own, so no release ever sees another release's files."""
    digest = hashlib.sha256()
    for file in sorted(files, key=lambda f: f.path.encode()):
        digest.update(_identity(file))
    return digest.hexdigest()[:32]


def stub_digest(stub: bytes) -> str:
    """SHA-256 of the bytes before the payload: the stub and its zero padding. Identical in
    every executable that has the same stub, however it was handed on (the raw stub of a
    source build, the padded prefix of a self-hosted one), so it links an app to the fsbpyr
    release that built it."""
    padding = bytes(align(len(stub), PAGE_SIZE) - len(stub))
    return hashlib.sha256(stub + padding).hexdigest()


def _builtin_or_frozen() -> set[str]:
    """Modules CPython resolves before the payload (the stub embeds the same CPython build)."""
    return {*sys.builtin_module_names, *_imp._frozen_module_names(), "_fsbpyr"}  # type: ignore[attr-defined]


def runtime_module_names(runtime: Runtime) -> frozenset[str]:
    """Modules found before the payload's: built-in and frozen ones, and the runtime's own."""
    return frozenset({m.name for m in (*runtime.modules, *boot_modules())} | _builtin_or_frozen())


def _check_clashes(kind: str, runtime: set[str] | frozenset[str], app: list[str]) -> None:
    clashes = sorted(runtime.intersection(app))
    if clashes:
        raise AssemblyError(f"application {kind} shadow the runtime: {clashes[:10]}")


@dataclass(frozen=True, slots=True)
class App:
    """Application content to place on top of a runtime."""

    name: str
    entrypoint: str  # "module:attr" (console-script semantics) or "module" (run as __main__)
    modules: tuple[Module, ...] = ()
    files: tuple[File, ...] = ()
    dists: tuple[Dist, ...] = ()
    build_info: tuple[tuple[str, object], ...] = ()
    zstd_level: int = ZSTD_LEVEL  # of the blocks (D29, D30)


def build_executable(runtime: Runtime, app: App, out: Path) -> None:
    commit(build_temporary(runtime, app, out), out)


def build_temporary(runtime: Runtime, app: App, out: Path) -> Path:
    """The executable, written beside ``out`` (``write_temporary``) for the caller to test and
    then ``commit``."""
    if runtime.bytecode_magic != importlib.util.MAGIC_NUMBER:
        raise AssemblyError(
            f"bytecode magic of this interpreter ({importlib.util.MAGIC_NUMBER.hex()}) differs "
            f"from runtime {runtime.runtime_id} ({runtime.bytecode_magic.hex()})"
        )
    boot = boot_modules()
    # built-in and frozen modules are found before the payload, so app modules could never load
    _check_clashes("modules", runtime_module_names(runtime), [m.name for m in app.modules])
    _check_clashes("files", {f.path for f in runtime.files}, [f.path for f in app.files])
    all_files = (*runtime.files, *app.files)
    info: dict[str, object] = {
        "runtime": runtime.runtime_id,
        "runtime_tag": runtime.tag,
        "extension_suffixes": list(runtime.extension_suffixes),
        "python": runtime.python_version,
        "fsbpyr": __version__,
        "zstd": zstd.zstd_version,
        "resources": resources_id(all_files),
        "runtime_inputs": dict(runtime.inputs),
        "runtime_components": dict(runtime.components),
        "stub_sha256": stub_digest(runtime.stub),
        **dict(app.build_info),
    }
    spec = PayloadSpec(
        python_hexversion=runtime.hexversion,
        bytecode_magic=runtime.bytecode_magic,
        generation_id=generation_id(runtime.tag, all_files),
        app_name=app.name,
        entrypoint=app.entrypoint,
        build_info=json.dumps(info, sort_keys=True),
        modules=(*runtime.modules, *boot, *app.modules),
        files=all_files,
        dists=app.dists,
        zstd_level=app.zstd_level,
    )
    return write_temporary(out, runtime.stub, spec)
