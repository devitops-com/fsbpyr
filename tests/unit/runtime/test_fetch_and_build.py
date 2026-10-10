"""PBS fetch (download, verification, extraction) and the runtime build orchestration."""

import dataclasses
import hashlib
import io
import json
import tarfile
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from fsbpyr.cli import main
from fsbpyr.payload.format import ModuleFlag, ModuleKind
from fsbpyr.payload.spec import Blob, File, Module
from fsbpyr.runtime import pbs, pins, registry, stdlib, stub, sysroot
from fsbpyr.runtime.pins import PbsPin, pin_for
from fsbpyr.runtime.toolchain import Toolchain


def _archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:zst") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


GOOD = _archive({"python/PYTHON.json": json.dumps({"python_version": "3.14.8"}).encode()})


def _pin(data: bytes) -> PbsPin:
    return PbsPin("3.14", "3.14.8", "20990101", "x86_64-unknown-linux-gnu", "pgo+lto-full",
                  hashlib.sha256(data).hexdigest())  # fmt: skip


@contextmanager
def _serve(data: bytes) -> Iterator[io.BytesIO]:
    yield io.BytesIO(data)


def test_fetch_extracts_a_cached_archive(tmp_path: Path) -> None:
    pin = _pin(GOOD)
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / f"{pin.name}.tar.zst").write_bytes(GOOD)

    dist = pbs.fetch(pin, tmp_path)

    assert dist.version == "3.14.8"
    assert dist.root == tmp_path / "pbs" / pin.name / "python"
    assert not list((tmp_path / "pbs").glob(".extract-*"))
    assert (tmp_path / "pbs" / pin.name / ".lock").is_file()  # held: never collected (D38)
    assert pbs.fetch(pin, tmp_path).root == dist.root  # second call reuses the extraction


def test_fetch_downloads_and_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pin = _pin(GOOD)
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: _serve(GOOD))

    assert pbs.fetch(pin, tmp_path).version == "3.14.8"
    assert (tmp_path / "downloads" / f"{pin.name}.tar.zst").read_bytes() == GOOD


def test_fetch_rejects_tampered_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pin = _pin(GOOD)
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: _serve(b"evil"))

    with pytest.raises(pbs.PbsError, match=r"SHA-256"):
        pbs.fetch(pin, tmp_path)
    assert list((tmp_path / "downloads").iterdir()) == []


def test_fetch_rejects_tampered_cached_archive(tmp_path: Path) -> None:
    pin = _pin(GOOD)
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / f"{pin.name}.tar.zst").write_bytes(b"evil")

    with pytest.raises(pbs.PbsError, match=r"SHA-256"):
        pbs.fetch(pin, tmp_path)


def test_fetch_rejects_archive_without_distribution(tmp_path: Path) -> None:
    bogus = _archive({"README": b"hi"})
    pin = _pin(bogus)
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / f"{pin.name}.tar.zst").write_bytes(bogus)

    with pytest.raises(pbs.PbsError, match=r"PYTHON\.json"):
        pbs.fetch(pin, tmp_path)


def test_fetch_refuses_path_traversal_in_archive(tmp_path: Path) -> None:
    hostile = _archive({"python/PYTHON.json": b"{}", "../escape.txt": b"x"})
    pin = _pin(hostile)
    (tmp_path / "cache" / "downloads").mkdir(parents=True)
    (tmp_path / "cache" / "downloads" / f"{pin.name}.tar.zst").write_bytes(hostile)

    with pytest.raises(tarfile.TarError):
        pbs.fetch(pin, tmp_path / "cache")
    assert not (tmp_path / "cache" / "escape.txt").exists()


def test_pin_lookup() -> None:
    assert pin_for("3.14", "x86_64-unknown-linux-gnu").version.startswith("3.14.")
    with pytest.raises(LookupError, match=r"3\.9"):
        pin_for("3.9", "x86_64-unknown-linux-gnu")


def test_build_runtime_orchestrates_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = pbs.PbsDist(tmp_path / "python", {
        "python_version": "3.14.8", "python_tag": "cp314",
        "target_triple": "x86_64-unknown-linux-gnu",
        "python_implementation_hex_version": 0x030E08F0, "python_bytecode_magic_number": "2b0e0d0a",
        "python_suffixes": {"extension": [".so"]},
        "build_info": {"object_file_format": "llvm-bitcode:22.1.3"},
    })  # fmt: skip
    boot = Module("encodings", "encodings/__init__.py", ModuleKind.PACKAGE, code=Blob.code(b"c"),
                  flags=ModuleFlag.STARTUP)  # fmt: skip
    calls: list[str] = []

    def fetch(pin: PbsPin, cache: Path) -> pbs.PbsDist:
        calls.append("fetch")
        return fake

    monkeypatch.setattr(pbs, "fetch", fetch)
    monkeypatch.setattr(registry, "find_toolchain", lambda major, explicit: Toolchain(
        Path("/usr/bin/clang-22"), Path("/usr/bin/ld.lld-22"), major))  # fmt: skip
    linked_against: list[Path] = []
    monkeypatch.setattr(sysroot, "fetch_sysroot", lambda pin, cache: cache / "sysroots" / pin.name)

    def build_stub(
        dist: pbs.PbsDist, toolchain: Toolchain, out: Path, *, sanitize: bool = False
    ) -> tuple[str, ...]:
        assert toolchain.sysroot is not None and not sanitize
        linked_against.append(toolchain.sysroot)
        out.write_bytes(b"STUB")
        return ("ld.lld-22: warning: from the link",)

    def fragment(
        dist: pbs.PbsDist, *, warn: Callable[[str], None]
    ) -> tuple[tuple[Module, ...], tuple[File, ...]]:
        warn("x.py: SyntaxWarning: from the stdlib")
        return (boot,), ()

    monkeypatch.setattr(stub, "build_stub", build_stub)
    monkeypatch.setattr(stdlib, "fragment", fragment)
    messages: list[str] = []

    runtime = registry.build_runtime("3.14", cache=tmp_path, log=messages.append)

    assert (runtime.stub, runtime.tag, runtime.modules) == (
        b"STUB",
        "cp314-x86_64-unknown-linux-gnu",
        (boot,),
    )
    assert registry.find_runtime("3.14", cache=tmp_path) == runtime
    assert (tmp_path / "runtimes" / runtime.runtime_id / ".lock").is_file()  # held (D38)
    assert registry.list_runtimes(tmp_path) == [(runtime.runtime_id, "3.14.8")]
    assert linked_against == [tmp_path / "sysroots" / "debian10-glibc2.28-x86_64"]  # stubbed
    # every warning of the build is shown, not only its failures
    assert {"ld.lld-22: warning: from the link", "x.py: SyntaxWarning: from the stdlib"} <= set(
        messages
    )

    again = registry.build_runtime("3.14", cache=tmp_path, log=messages.append)
    assert again == runtime and calls == ["fetch"]
    assert messages[-1].endswith("is already built")


def test_runtime_id_depends_on_the_glibc_sysroot(monkeypatch: pytest.MonkeyPatch) -> None:
    pin = pin_for("3.14", "x86_64-unknown-linux-gnu")
    before = registry.runtime_id(pin)
    other = dataclasses.replace(pins.sysroot_for(pin.triple), name="debian11-glibc2.31")
    monkeypatch.setitem(pins.SYSROOTS, pin.triple, other)

    assert registry.runtime_id(pin) != before


def test_find_runtime_explains_how_to_build(tmp_path: Path) -> None:
    with pytest.raises(registry.RuntimeCacheError, match=r"fsbpyr runtime build --python 3\.14"):
        registry.find_runtime("3.14", cache=tmp_path)


def test_cache_root_honours_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path / "x"))
    assert registry.cache_root() == tmp_path / "x"
    monkeypatch.delenv("FSBPYR_CACHE_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert registry.cache_root() == tmp_path / "xdg" / "fsbpyr"
    monkeypatch.setenv("XDG_CACHE_HOME", "relative")  # ignored, as the XDG spec has it
    assert registry.cache_root() == Path.home() / ".cache" / "fsbpyr"


def test_runtime_id_depends_on_the_pinned_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    pin = pin_for("3.14", "x86_64-unknown-linux-gnu")
    before = registry.runtime_id(pin)
    current = pins.sysroot_for(pin.triple)
    other = dataclasses.replace(current, packages=current.packages[:-1])  # same name
    monkeypatch.setitem(pins.SYSROOTS, pin.triple, other)

    assert registry.runtime_id(pin) != before


def test_runtime_build_reports_sysroot_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(python: str, clang: Path | None = None) -> None:
        raise sysroot.SysrootError("cannot download https://example.invalid/libc6.deb")

    monkeypatch.setattr(registry, "build_runtime", fail)

    assert main(["runtime", "build"]) == 1
    assert "fsbpyr: error: cannot download" in capsys.readouterr().err


def test_the_sanitized_runtime_has_an_identity_of_its_own() -> None:
    pin = pin_for("3.14", "x86_64-unknown-linux-gnu")

    sanitized = registry.runtime_id(pin, sanitize=True)

    assert sanitized != registry.runtime_id(pin) and sanitized.endswith("-sanitized")
    assert stub.launcher_digest(sanitize=True) != stub.launcher_digest()
