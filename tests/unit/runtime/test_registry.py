"""Payload -> spec conversion, runtime cache round trip and app assembly."""

import dataclasses
import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path

import pytest

from fsbpyr import bytecode
from fsbpyr.assemble import (
    BOOT_MODULE,
    App,
    AssemblyError,
    build_executable,
    generation_id,
    resources_id,
    stub_digest,
)
from fsbpyr.payload.convert import spec_from_payload
from fsbpyr.payload.format import (
    TRAILER,
    TRAILER_MAGIC,
    Codec,
    FileKind,
    ModuleFlag,
    ModuleKind,
    tree_digest,
)
from fsbpyr.payload.reader import Executable, Payload
from fsbpyr.payload.spec import ZSTD_FAST_LEVEL, ZSTD_LEVEL, Blob, Dist, File, Module, PayloadSpec
from fsbpyr.payload.writer import encode_payload
from fsbpyr.runtime import pins, registry
from fsbpyr.runtime.registry import Runtime, RuntimeCacheError, check_owner, secure_directory

from ..payload.conftest import make_spec

MAGIC = importlib.util.MAGIC_NUMBER


@pytest.fixture
def spec() -> PayloadSpec:
    return make_spec()


def _runtime(**overrides: object) -> Runtime:
    code = bytecode.dumps(compile("X = 1\n", "encodings/__init__.py", "exec"))
    fields: dict[str, object] = {
        "tag": "cp314-x86_64-linux-gnu",
        "runtime_id": "cp314-x86_64-linux-gnu-0123456789ab",
        "python_version": "3.14.8",
        "hexversion": 0x030E08F0,
        "bytecode_magic": MAGIC,
        "extension_suffixes": (".cpython-314-x86_64-linux-gnu.so", ".abi3.so", ".so"),
        "stub": b"\x7fELF-fake-stub",
        "modules": (
            Module(
                "encodings",
                "encodings/__init__.py",
                ModuleKind.PACKAGE,
                code=Blob.code(code),
                flags=ModuleFlag.STARTUP,
            ),
            Module(
                "json",
                "json/__init__.py",
                ModuleKind.PACKAGE,
                code=Blob.zstd(b"j" * 300),
                source=Blob.zstd(b"src" * 100),
            ),
        ),
        "files": (File("json/__init__.py", FileKind.SOURCE, Blob.zstd(b"src" * 100)),),
    }
    fields.update(overrides)
    return Runtime(**fields)  # type: ignore[arg-type]


def test_spec_from_payload_round_trips_exactly(spec: PayloadSpec) -> None:
    raw = encode_payload(spec)

    assert encode_payload(spec_from_payload(Payload(raw))) == raw


def test_spec_from_payload_keeps_stored_blobs_verbatim(spec: PayloadSpec) -> None:
    payload = Payload(encode_payload(spec))
    cli = next(m for m in spec_from_payload(payload).modules if m.name == "pkg.cli")

    assert cli.code is not None and cli.code.codec is Codec.ZSTD
    assert cli.code == next(m for m in spec.modules if m.name == "pkg.cli").code


def test_runtime_save_and_load(tmp_path: Path) -> None:
    runtime = _runtime()

    runtime.save(tmp_path / "rt")
    loaded = Runtime.load(tmp_path / "rt")

    assert loaded == runtime
    meta = json.loads((tmp_path / "rt" / "runtime.json").read_text())
    assert meta["python_version"] == "3.14.8"


def test_runtime_records_its_toolchain_for_information_only(tmp_path: Path) -> None:
    toolchain = {"clang": "Debian clang version 22.1.8 (1~deb13u4)", "lld": "Debian LLD 22.1.8"}
    plain, recorded = _runtime(), _runtime(toolchain=toolchain)

    recorded.save(tmp_path / "rt")

    meta = json.loads((tmp_path / "rt" / "runtime.json").read_text())
    assert meta["toolchain"] == toolchain
    assert Runtime.load(tmp_path / "rt").toolchain == toolchain
    assert plain.runtime_id == recorded.runtime_id  # not part of the identity (D3)


def test_runtime_without_recorded_toolchain_still_loads(tmp_path: Path) -> None:
    _runtime().save(tmp_path / "rt")
    meta_path = tmp_path / "rt" / "runtime.json"
    meta = json.loads(meta_path.read_text())
    del meta["toolchain"]  # a cache written before the toolchain was recorded
    meta_path.write_text(json.dumps(meta))

    assert Runtime.load(tmp_path / "rt").toolchain == {}


def test_runtime_load_rejects_incomplete_directory(tmp_path: Path) -> None:
    _runtime().save(tmp_path / "rt")
    (tmp_path / "rt" / "stub").unlink()

    with pytest.raises(RuntimeCacheError, match="incomplete"):
        Runtime.load(tmp_path / "rt")


def test_build_executable_composes_runtime_boot_and_app(tmp_path: Path) -> None:
    out = tmp_path / "hello"
    app = Module(
        "hello",
        "hello.py",
        ModuleKind.MODULE,
        code=Blob.code(bytecode.dumps(compile("def main(): return 0\n", "hello.py", "exec"))),
    )

    build_executable(_runtime(), App("hello", "hello:main", modules=(app,)), out)

    with Executable.open(out) as exe:
        payload = exe.payload
        assert exe.stub_prefix().startswith(b"\x7fELF-fake-stub")
        assert (payload.app_name, payload.entrypoint) == ("hello", "hello:main")
        assert [m.name for m in payload.startup_modules()] == [BOOT_MODULE, "encodings"]
        assert {"json", "hello"} <= {m.name for m in payload.modules()}
        assert payload.generation_id == generation_id("cp314-x86_64-linux-gnu", ())
        assert json.loads(payload.build_info)["resources"] == resources_id(
            tuple(spec_from_payload(payload).files)
        )
        assert json.loads(payload.build_info)["runtime"] == "cp314-x86_64-linux-gnu-0123456789ab"


def test_build_executable_rejects_bytecode_magic_mismatch(tmp_path: Path) -> None:
    other_magic = b"\x00\x00\r\n"

    with pytest.raises(AssemblyError, match="bytecode"):
        build_executable(_runtime(bytecode_magic=other_magic), App("x", "x:main"), tmp_path / "x")


def test_build_executable_rejects_app_module_clashing_with_runtime(tmp_path: Path) -> None:
    clash = Module("json", "json/__init__.py", ModuleKind.PACKAGE, code=Blob.code(b"c"))

    with pytest.raises(AssemblyError, match="json"):
        build_executable(_runtime(), App("x", "x:main", modules=(clash,)), tmp_path / "x")


def test_generation_id_depends_on_runtime_and_extractable_files() -> None:
    lib = File("a.libs/l.so", FileKind.NATIVE_LIB, Blob.raw(b"l", digest=True))
    res = File("a/r.txt", FileKind.RESOURCE, Blob.raw(b"r"))

    base = generation_id("cp314-x", ())
    assert generation_id("cp314-x", (res,)) == base  # in-memory resources do not count
    assert generation_id("cp314-x", (lib,)) != base
    executable = dataclasses.replace(lib, mode=0o755)
    # cached files keep the mode of their first extraction, so a mode change needs a new one
    assert generation_id("cp314-x", (executable,)) != generation_id("cp314-x", (lib,))
    assert generation_id("cp314-y", ()) != base
    assert len(base) == 32


def test_resources_id_covers_every_file_unlike_the_generation() -> None:
    res = File("a/r.txt", FileKind.RESOURCE, Blob.raw(b"r"))
    changed = dataclasses.replace(res, blob=Blob.raw(b"R"))
    lib = File("a.libs/l.so", FileKind.NATIVE_LIB, Blob.raw(b"l", digest=True))

    assert resources_id((res,)) != resources_id((changed,))  # a new release, a new tree (D27)
    assert generation_id("cp314-x", (res,)) == generation_id("cp314-x", (changed,))  # D10
    assert resources_id((res, lib)) != resources_id((res,))
    assert resources_id((dataclasses.replace(res, mode=0o755),)) != resources_id((res,))
    assert len(resources_id(())) == 32 and int(resources_id(()), 16) >= 0


@pytest.mark.parametrize("level", [ZSTD_FAST_LEVEL, ZSTD_LEVEL])
def test_blocks_are_compressed_at_the_apps_level(tmp_path: Path, level: int) -> None:
    body = b"".join(b"line %d of a module\n" % i for i in range(2000))
    app = Module("hello", "hello.py", ModuleKind.MODULE, code=Blob.code(body), dist="hello")
    out = tmp_path / "app"

    build_executable(_runtime(), App("hello", "hello:main", modules=(app,), zstd_level=level,
                                     dists=(Dist("hello", "h", "1"),)), out)  # fmt: skip

    with Executable.open(out) as executable:
        payload = executable.payload
        record = payload.find_module("hello")
        assert record is not None and payload.blob(record.code).codec is Codec.BLOCK
        block = payload.blob(payload.blob(record.code).offset)
        content = payload.read_blob(block.index)
        assert (
            payload.stored(block.index) == Blob.zstd(content, level=level, allow_raw=False).stored
        )


def test_runtime_files_are_recorded_with_the_integrity_digest(tmp_path: Path) -> None:
    """D32: one digest construction for everything fsbpyr checks against its own record."""
    _runtime().save(tmp_path / "rt")
    meta = json.loads((tmp_path / "rt" / "runtime.json").read_text())

    assert meta["digests"] == {name: tree_digest((tmp_path / "rt" / name).read_bytes()).hex()
                               for name in ("stub", "stdlib.fsbp")}  # fmt: skip


def test_runtime_load_detects_corrupted_files(tmp_path: Path) -> None:
    _runtime().save(tmp_path / "rt")
    stub = tmp_path / "rt" / "stub"
    stub.write_bytes(stub.read_bytes() + b"!")

    with pytest.raises(RuntimeCacheError, match="corrupt"):
        Runtime.load(tmp_path / "rt")


def test_runtimes_of_an_older_layout_are_refused(tmp_path: Path) -> None:
    """A layout change (D29: stdlib frames at level 9) must never reuse an old runtime."""
    _runtime().save(tmp_path / "rt")
    meta_path = tmp_path / "rt" / "runtime.json"
    meta = json.loads(meta_path.read_text())
    assert meta["layout"] == registry.LAYOUT_VERSION == 9
    meta_path.write_text(json.dumps({**meta, "layout": 6}))

    with pytest.raises(RuntimeCacheError, match="old layout"):
        Runtime.load(tmp_path / "rt")


def test_blobs_default_to_the_build_level() -> None:
    text = "\n".join(f"row {i} {i * 7 % 13}" for i in range(5000)).encode()

    assert Blob.zstd(text).stored == Blob.zstd(text, level=ZSTD_LEVEL).stored
    assert Blob.zstd(text).stored != Blob.zstd(text, level=ZSTD_FAST_LEVEL).stored
    assert Blob.code(text) == Blob.code(text, level=ZSTD_LEVEL)


def test_save_keeps_a_complete_runtime_written_concurrently(tmp_path: Path) -> None:
    winner = _runtime(stub=b"\x7fELF-winner")
    winner.save(tmp_path / "rt")

    _runtime().save(tmp_path / "rt")  # a second builder finishing later must not clobber it

    assert Runtime.load(tmp_path / "rt").stub == b"\x7fELF-winner"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rt"]


def test_save_replaces_an_incomplete_directory(tmp_path: Path) -> None:
    (tmp_path / "rt").mkdir()
    (tmp_path / "rt" / "junk").write_text("x")

    _runtime().save(tmp_path / "rt")

    assert Runtime.load(tmp_path / "rt") == _runtime()


def test_secure_directory_tightens_own_permissive_directory(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir(mode=0o775)
    cache.chmod(0o777)

    secure_directory(cache)

    assert cache.stat().st_mode & 0o777 == 0o700


def test_secure_directory_creates_private_directories(tmp_path: Path) -> None:
    secure_directory(tmp_path / "a" / "b")

    assert (tmp_path / "a" / "b").stat().st_mode & 0o777 == 0o700


def test_foreign_owner_is_refused() -> None:
    with pytest.raises(RuntimeCacheError, match="owned by uid 0"):
        check_owner(Path("/somewhere"), owner=0, euid=1000)


def test_runtime_from_executable_recovers_the_embedded_runtime(tmp_path: Path) -> None:
    runtime = _runtime()
    out = tmp_path / "app"
    app = Module("hello", "hello.py", ModuleKind.MODULE, code=Blob.code(b"c"), dist="hello")
    build_executable(
        runtime, App("hello", "hello:main", modules=(app,), dists=(Dist("hello", "h", "1"),)), out
    )

    embedded = Runtime.from_executable(out)

    def content(modules: tuple[Module, ...]) -> list[tuple[str, bytes | None]]:
        """Blobs packed into blocks come back raw: compare what they hold."""
        return [(m.name, m.code.content() if m.code else None) for m in modules]

    assert content(embedded.modules) == content(runtime.modules)  # no app or boot modules
    assert [f.blob.content() for f in embedded.files] == [f.blob.content() for f in runtime.files]
    assert (embedded.tag, embedded.runtime_id, embedded.python_version) == (
        runtime.tag, runtime.runtime_id, runtime.python_version)  # fmt: skip
    assert embedded.extension_suffixes == runtime.extension_suffixes
    assert embedded.stub.startswith(runtime.stub) and len(embedded.stub) % 4096 == 0


@pytest.mark.parametrize("baseline", pins.BASELINES)
def test_the_baseline_survives_the_trip_through_an_executable(
    tmp_path: Path, baseline: str
) -> None:
    """build_info records the sysroot's identity; the executable's runtime reads its baseline
    back from it (the runtime's own metadata is not in the executable)."""
    sysroot = pins.sysroot_for("x86_64-unknown-linux-gnu", baseline)
    runtime = _runtime(inputs={"sysroot": sysroot.identity}, baseline=baseline)
    out = tmp_path / "app"
    build_executable(runtime, App("hello", "hello:main"), out)

    assert Runtime.from_executable(out).baseline == baseline


def test_an_executable_of_an_unknown_sysroot_has_the_default_baseline(tmp_path: Path) -> None:
    out = tmp_path / "app"
    build_executable(_runtime(inputs={"sysroot": "debian11-glibc2.31-x86_64-0123456789ab"}),
                     App("hello", "hello:main"), out)  # fmt: skip

    assert Runtime.from_executable(out).baseline == pins.DEFAULT_BASELINE


def test_a_runtime_saved_before_baselines_loads_as_the_default(tmp_path: Path) -> None:
    _runtime().save(tmp_path / "rt")
    meta = json.loads((tmp_path / "rt" / "runtime.json").read_text())
    del meta["baseline"]
    (tmp_path / "rt" / "runtime.json").write_text(json.dumps(meta))

    assert Runtime.load(tmp_path / "rt").baseline == "2.34"


def test_select_runtime_takes_the_embedded_runtime_only_for_its_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedded = _runtime(baseline="2.28")
    monkeypatch.setattr(registry, "running_executable", lambda: Path("/proc/self/exe"))
    monkeypatch.setattr(Runtime, "from_executable", classmethod(lambda cls, path: embedded))
    asked: list[str] = []

    def find(python: str, cache: Path | None = None, baseline: str = "2.34") -> Runtime:
        asked.append(baseline)
        return _runtime()

    monkeypatch.setattr(registry, "find_runtime", find)

    assert registry.select_runtime("3.14", baseline="2.28") is embedded
    assert registry.select_runtime("3.14") == _runtime() and asked == ["2.34"]


def test_find_runtime_names_the_glibc_to_build_for(tmp_path: Path) -> None:
    with pytest.raises(RuntimeCacheError, match=r"Python 3\.14 and glibc 2\.28; run "
                       r"`fsbpyr runtime build --python 3\.14 --glibc 2\.28`"):
        registry.find_runtime("3.14", cache=tmp_path, baseline="2.28")


def test_select_runtime_uses_the_cache_outside_executables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _runtime().save(tmp_path / "rt")
    monkeypatch.setattr(registry, "find_runtime",
                        lambda python, cache=None, baseline="2.34": Runtime.load(tmp_path / "rt"))

    assert registry.running_executable() is None  # pytest is not an fsbpyr executable
    assert registry.select_runtime("3.14") == _runtime()


def test_own_runtime_is_read_from_the_running_inode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "builtin_module_names", (*sys.builtin_module_names, "_fsbpyr"))

    # not sys.executable: after an in-place upgrade that name is another file
    assert registry.running_executable() == Path("/proc/self/exe")


@pytest.mark.parametrize("name", ["grp", "posix", "os", "_fsbpyr"])
def test_app_modules_may_not_shadow_builtin_or_frozen_modules(tmp_path: Path, name: str) -> None:
    clash = Module(name, f"{name}.py", ModuleKind.MODULE, code=Blob.code(b"c"), dist="x")

    with pytest.raises(AssemblyError, match=name):
        build_executable(_runtime(), App("x", "x:main", modules=(clash,)), tmp_path / "x")


def test_runtime_from_an_executable_of_another_format_version_is_refused(tmp_path: Path) -> None:
    """Its launcher could not read what this fsbpyr writes (and before version 3 the stub
    would be copied unchecked)."""
    out = tmp_path / "app"
    app = Module("hello", "hello.py", ModuleKind.MODULE, code=Blob.code(b"c"), dist="hello")
    build_executable(
        _runtime(),
        App("hello", "hello:main", modules=(app,), dists=(Dist("hello", "h", "1"),)),
        out,
    )
    with Executable.open(out) as exe:
        offset, size = exe.payload_offset, exe.payload_size
    data = out.read_bytes()
    payload = bytearray(data[offset : offset + size])
    struct.pack_into("<I", payload, 8, 2)
    digest = hashlib.sha256(payload).digest()
    out.write_bytes(data[:offset] + payload + TRAILER.pack(TRAILER_MAGIC, 2, 0, offset, size,
                                                           digest))  # fmt: skip

    with pytest.raises(RuntimeCacheError, match="format version 2"):
        Runtime.from_executable(out)


def test_runtime_from_a_modified_executable_is_refused(tmp_path: Path) -> None:
    """fsbpyr copies its own stub and stdlib into every app it builds: only as built."""
    out = tmp_path / "app"
    app = Module("hello", "hello.py", ModuleKind.MODULE, code=Blob.code(b"c"), dist="hello")
    build_executable(
        _runtime(),
        App("hello", "hello:main", modules=(app,), dists=(Dist("hello", "h", "1"),)),
        out,
    )
    data = bytearray(out.read_bytes())
    data[1] ^= 0x01
    out.write_bytes(bytes(data))

    with pytest.raises(RuntimeCacheError, match="modified or damaged"):
        Runtime.from_executable(out)


INPUTS: dict[str, object] = {
    "pbs": {"release": "20261009", "name": "cpython-3.14.8+20261009-x", "sha256": "ab" * 32},
    "sysroot": "debian12-glibc2.36-x86_64-0123456789ab",
    "layout": registry.LAYOUT_VERSION,
}
COMPONENTS = {"openssl": "OpenSSL 3.5.9 29 Sep 2026", "sqlite": "3.53.1"}


def test_runtime_keeps_its_inputs_and_components_in_runtime_json(tmp_path: Path) -> None:
    runtime = _runtime(inputs=INPUTS, components=COMPONENTS)

    runtime.save(tmp_path / "rt")

    meta = json.loads((tmp_path / "rt" / "runtime.json").read_text())
    assert (meta["inputs"], meta["components"]) == (INPUTS, COMPONENTS)
    assert Runtime.load(tmp_path / "rt") == runtime


def test_the_inputs_and_components_reach_build_info_and_the_next_runtime(tmp_path: Path) -> None:
    out = tmp_path / "app"
    build_executable(_runtime(inputs=INPUTS, components=COMPONENTS), App("a", "a:main"), out)

    with Executable.open(out) as exe:
        info = json.loads(exe.payload.build_info)
    embedded = Runtime.from_executable(out)  # self-hosted builds copy them from the builder

    assert (info["runtime_inputs"], info["runtime_components"]) == (INPUTS, COMPONENTS)
    assert (embedded.inputs, embedded.components) == (INPUTS, COMPONENTS)


def test_stub_sha256_is_over_the_stub_and_its_padding(tmp_path: Path) -> None:
    """The same value from the raw stub (a source build) and from the padded prefix a
    self-hosted build passes on, and it is what `head -c <payload offset>` hashes."""
    first, second = tmp_path / "first", tmp_path / "second"
    build_executable(_runtime(), App("a", "a:main"), first)
    build_executable(Runtime.from_executable(first), App("b", "b:main"), second)

    infos = []
    for path in (first, second):
        with Executable.open(path) as exe:
            infos.append(json.loads(exe.payload.build_info))
            prefix = exe.stub_prefix()
        assert infos[-1]["stub_sha256"] == hashlib.sha256(prefix).hexdigest()
    assert infos[0]["stub_sha256"] == infos[1]["stub_sha256"] == stub_digest(_runtime().stub)


def test_runtimes_cached_before_inputs_were_recorded_load_without_them(tmp_path: Path) -> None:
    _runtime().save(tmp_path / "rt")
    meta_path = tmp_path / "rt" / "runtime.json"
    meta = json.loads(meta_path.read_text())
    del meta["inputs"], meta["components"]
    meta_path.write_text(json.dumps(meta))

    assert (Runtime.load(tmp_path / "rt").inputs, Runtime.load(tmp_path / "rt").components) == (
        {}, {})  # fmt: skip


@pytest.mark.parametrize("home", [None, "", "relative"])
def test_an_unusable_home_without_a_passwd_entry_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, home: str | None
) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False) if home is None else monkeypatch.setenv("HOME", home)

    def no_entry(uid: int) -> None:
        raise KeyError(uid)

    monkeypatch.setattr("pwd.getpwuid", no_entry)

    with pytest.raises(RuntimeError, match="set HOME, XDG_CACHE_HOME or FSBPYR_CACHE_DIR"):
        registry.cache_home()
