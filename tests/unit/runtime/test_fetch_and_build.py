"""PBS fetch (download, verification, extraction) and the runtime build orchestration."""

import dataclasses
import hashlib
import importlib.machinery
import io
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from email.message import Message
from pathlib import Path

import pytest

from fsbpyr import cli
from fsbpyr.cli import main
from fsbpyr.compat import tarfile
from fsbpyr.payload.format import FileKind, ModuleFlag, ModuleKind
from fsbpyr.payload.spec import Blob, File, Module
from fsbpyr.runtime import notices, pbs, pins, registry, stdlib, stub, sysroot
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


def _flaky(failures: list[BaseException]) -> Callable[..., object]:
    """urlopen that raises ``failures`` in turn, then serves GOOD."""
    calls: list[str] = []

    def urlopen(url: str, timeout: float) -> object:
        calls.append(url)
        if len(calls) <= len(failures):
            raise failures[len(calls) - 1]
        return _serve(GOOD)

    urlopen.calls = calls  # type: ignore[attr-defined]
    return urlopen


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("_ssl.c:1064: The handshake operation timed out"),
        urllib.error.URLError(TimeoutError("_ssl.c:1064: The handshake operation timed out")),
        ConnectionResetError(104, "Connection reset by peer"),
    ],
    ids=["timeout", "urlerror", "reset"],
)
def test_download_retries_a_network_failure(
    error: BaseException, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    urlopen = _flaky([error, error])
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    target = tmp_path / "downloads" / "x.tar.zst"

    pbs.download("https://example.invalid/x", hashlib.sha256(GOOD).hexdigest(), target)

    assert target.read_bytes() == GOOD
    assert len(urlopen.calls) == 3  # type: ignore[attr-defined]
    assert len(slept) == 2
    assert [p.name for p in target.parent.iterdir()] == ["x.tar.zst"]  # no .part left


def test_download_gives_up_after_its_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    urlopen = _flaky([TimeoutError("timed out")] * 10)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    target = tmp_path / "downloads" / "x.tar.zst"

    with pytest.raises(TimeoutError):
        pbs.download("https://example.invalid/x", hashlib.sha256(GOOD).hexdigest(), target)
    assert len(urlopen.calls) == pbs.DOWNLOAD_ATTEMPTS  # type: ignore[attr-defined]
    assert list(target.parent.iterdir()) == []


def test_download_does_not_retry_an_http_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 stays a 404: only the network is retried."""
    error = urllib.error.HTTPError("https://example.invalid/x", 404, "Not Found", Message(), None)
    urlopen = _flaky([error])
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(time, "sleep", lambda _: None)

    with pytest.raises(urllib.error.HTTPError):
        pbs.download("https://example.invalid/x", "0" * 64, tmp_path / "x")
    assert len(urlopen.calls) == 1  # type: ignore[attr-defined]


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


@pytest.mark.parametrize(
    ("triple", "python", "version", "sha256"),
    [
        ("x86_64", "3.12", "3.12.15",
         "5c992267780cef4c168bc07dba54f125ec1a2b9383a47946fc805f81a4712119"),
        ("x86_64", "3.13", "3.13.17",
         "344eec7e761e7b3dccce0ce2d91a3e70d40f8ff5d3ce7d907d979c23e8451fc7"),
        ("x86_64", "3.14", "3.14.9",
         "92f6abbce0283ed317873615396b5c43ccdcdf13ae9c7d68c9a07651df527528"),
        ("aarch64", "3.12", "3.12.15",
         "d40b4b72653d830884ac3ae432cf6b761a63b4c9b65d0d8f9241ddbd03c9c020"),
        ("aarch64", "3.13", "3.13.17",
         "6464bea3d1b41ebcb238ebdbb32332e9c01871d4ad14e7e66ca6aca18d5135fb"),
        ("aarch64", "3.14", "3.14.9",
         "f5726037fbea57a8d83ddb6bbe26242eeb96928fb396da2f6d9dbf3641aaff61"),
    ],
)  # fmt: skip
def test_every_version_is_pinned_to_release_20261021(
    triple: str, python: str, version: str, sha256: str
) -> None:
    pin = pin_for(python, f"{triple}-unknown-linux-gnu")

    assert (pin.version, pin.release, pin.flavor, pin.sha256) == (
        version, "20261021", "pgo+lto-full", sha256,
    )  # fmt: skip
    assert pin.url == (
        "https://github.com/astral-sh/python-build-standalone/releases/download/20261021/"
        f"cpython-{version}%2B20261021-{triple}-unknown-linux-gnu-pgo%2Blto-full.tar.zst"
    )


def test_runtime_ids_differ_by_version_and_name_it() -> None:
    ids = {p: registry.runtime_id(pin_for(p, "x86_64-unknown-linux-gnu"))
           for p in ("3.12", "3.13", "3.14")}  # fmt: skip

    assert len(set(ids.values())) == 3
    assert all(i.startswith(f"cp{p.replace('.', '')}-") for p, i in ids.items())


@pytest.mark.parametrize("baseline", pins.BASELINES)
def test_build_runtime_orchestrates_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, baseline: str
) -> None:
    fake = pbs.PbsDist(tmp_path / "python", {
        "python_version": "3.14.8", "python_tag": "cp314",
        "target_triple": registry.host_triple(),
        "python_implementation_hex_version": 0x030E08F0, "python_bytecode_magic_number": "2b0e0d0a",
        "python_suffixes": {"extension": [".so"]},
        "build_info": {"object_file_format": "llvm-bitcode:22.1.3"},
        "python_exe": "install/bin/python3.14",
    })  # fmt: skip
    boot = Module("encodings", "encodings/__init__.py", ModuleKind.PACKAGE, code=Blob.code(b"c"),
                  flags=ModuleFlag.STARTUP)  # fmt: skip
    calls: list[str] = []

    def fetch(pin: PbsPin, cache: Path) -> pbs.PbsDist:
        calls.append("fetch")
        return fake

    monkeypatch.setattr(pbs, "fetch", fetch)
    components = {"openssl": "OpenSSL 3.5.9 29 Sep 2026", "sqlite": "3.53.1"}
    monkeypatch.setattr(pbs, "component_versions", lambda interpreter: components)
    suffixes = (".cpython-314-x86_64-linux-gnu.so", ".abi3.so", ".so")
    monkeypatch.setattr(pbs, "extension_suffixes", lambda interpreter: suffixes)
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

    notice = File("_fsbpyr_licenses/LICENSE.fsbpyr.txt", FileKind.RESOURCE, Blob.zstd(b"MIT"))
    monkeypatch.setattr(stub, "build_stub", build_stub)
    monkeypatch.setattr(stdlib, "fragment", fragment)
    monkeypatch.setattr(notices, "files", lambda dist: (notice,))
    messages: list[str] = []

    runtime = registry.build_runtime("3.14", cache=tmp_path, log=messages.append,
                                     baseline=baseline)  # fmt: skip

    assert (runtime.stub, runtime.tag, runtime.modules) == (
        b"STUB",
        f"cp314-{registry.host_triple()}",
        (boot,),
    )
    assert runtime.files == (notice,)  # the licence notices travel with the runtime (REP-4)
    assert registry.find_runtime("3.14", cache=tmp_path, baseline=baseline) == runtime
    pin = pins.pin_for("3.14", registry.host_triple())
    sysroot_pin = pins.sysroot_for(pin.triple, baseline)
    assert runtime.baseline == baseline  # recorded in runtime.json, read back by load
    assert runtime.runtime_id == registry.runtime_id(pin, baseline=baseline)
    other = next(b for b in pins.BASELINES if b != baseline)  # never mixed with the other
    with pytest.raises(registry.RuntimeCacheError, match=r"no runtime for Python 3\.14"):
        registry.find_runtime("3.14", cache=tmp_path, baseline=other)
    assert runtime.components == components
    assert runtime.extension_suffixes == suffixes  # the interpreter's, not PYTHON.json's [".so"]
    assert runtime.inputs == {
        "pbs": {"release": pin.release, "name": pin.name, "sha256": pin.sha256},
        "sysroot": sysroot_pin.identity,
        "layout": registry.LAYOUT_VERSION,
    }
    assert (tmp_path / "runtimes" / runtime.runtime_id / ".lock").is_file()  # held (D38)
    assert registry.list_runtimes(tmp_path) == [(runtime.runtime_id, "3.14.8", baseline)]
    assert linked_against == [tmp_path / "sysroots" / sysroot_pin.name]  # stubbed
    # every warning of the build is shown, not only its failures
    assert {"ld.lld-22: warning: from the link", "x.py: SyntaxWarning: from the stdlib"} <= set(
        messages
    )

    again = registry.build_runtime("3.14", cache=tmp_path, log=messages.append, baseline=baseline)
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
    def fail(python: str, clang: Path | None = None, baseline: str = "2.34") -> None:
        raise sysroot.SysrootError("cannot download https://example.invalid/libc6.deb")

    monkeypatch.setattr(registry, "build_runtime", fail)

    assert main(["runtime", "build"]) == 1
    assert "fsbpyr: error: cannot download" in capsys.readouterr().err


def test_runtime_build_passes_the_glibc_baseline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[tuple[str, str]] = []

    def build(python: str, clang: Path | None = None, baseline: str = "2.34") -> None:
        asked.append((python, baseline))

    monkeypatch.setattr(registry, "build_runtime", build)

    assert main(["runtime", "build", "--python", "3.14"]) == 0
    assert main(["runtime", "build", "--python", "3.13", "--glibc", "2.28"]) == 0
    assert main(["runtime", "build", "--glibc", "2.27"]) == 2
    assert asked == [("3.14", "2.34"), ("3.13", "2.28")]
    assert "'2.27' is not one of '2.34', '2.28'" in capsys.readouterr().err
    option = next(p for p in cli.runtime_build.params if p.name == "baseline")
    assert tuple(option.type.choices) == pins.BASELINES  # type: ignore[attr-defined]


def test_the_sanitized_runtime_has_an_identity_of_its_own() -> None:
    pin = pin_for("3.14", "x86_64-unknown-linux-gnu")

    sanitized = registry.runtime_id(pin, sanitize=True)

    assert sanitized != registry.runtime_id(pin) and sanitized.endswith("-sanitized")
    assert stub.launcher_digest(sanitize=True) != stub.launcher_digest()


def test_component_versions_come_from_running_the_interpreter() -> None:
    found = pbs.component_versions(Path(sys.executable))

    assert set(found) == {"expat", "libmpdec", "openssl", "sqlite", "zlib"}
    assert found["openssl"].startswith("OpenSSL ") and all(found.values())


def test_component_versions_name_an_interpreter_that_fails(tmp_path: Path) -> None:
    with pytest.raises(pbs.PbsError, match="did not report"):
        pbs.component_versions(Path("/bin/false"))
    with pytest.raises(pbs.PbsError, match="cannot run"):
        pbs.component_versions(tmp_path / "missing")


def test_extension_suffixes_come_from_running_the_interpreter() -> None:
    """In the interpreter's order, which the import system tries them in; 3.15's PYTHON.json
    lacks .abi3t.so."""
    found = pbs.extension_suffixes(Path(sys.executable))

    assert found == tuple(importlib.machinery.EXTENSION_SUFFIXES)
    assert found[-1] == ".so" and ".abi3.so" in found


def test_extension_suffixes_name_an_interpreter_that_fails(tmp_path: Path) -> None:
    with pytest.raises(pbs.PbsError, match="did not report its extension suffixes"):
        pbs.extension_suffixes(Path("/bin/false"))
    with pytest.raises(pbs.PbsError, match="cannot run"):
        pbs.extension_suffixes(tmp_path / "missing")
