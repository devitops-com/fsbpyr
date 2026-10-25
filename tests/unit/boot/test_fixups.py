"""_fsbpyr_rt.fixups: stdlib adjustments applied right after a module loads (Phase 6b)."""

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

from .conftest import BOOT_DIR


@pytest.fixture
def fixups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The module with its /etc/ssl locations moved below ``tmp_path``."""
    spec = importlib.util.spec_from_file_location("_fixups", BOOT_DIR / "_fsbpyr_rt" / "fixups.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "CA_FILE", str(tmp_path / "cert.pem"))
    monkeypatch.setattr(module, "CA_DIRECTORY", str(tmp_path / "certs"))
    bundles = (str(tmp_path / "certs" / "ca-certificates.crt"), str(tmp_path / "bundle.pem"))
    monkeypatch.setattr(module, "SYSTEM_BUNDLES", bundles)
    (tmp_path / "certs").mkdir()
    return module


def test_no_fallback_when_openssl_finds_a_store(fixups: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "bundle.pem").write_text("B")

    (tmp_path / "certs" / "0a1b2c3d.0").write_text("hashed")  # Debian, SUSE: hashed directory
    assert fixups.fallback_bundle({}) is None
    (tmp_path / "certs" / "0a1b2c3d.0").unlink()
    (tmp_path / "cert.pem").write_text("C")  # RHEL, Alpine, Arch: a default file
    assert fixups.fallback_bundle({}) is None
    (tmp_path / "cert.pem").unlink()
    assert fixups.fallback_bundle({"SSL_CERT_FILE": "/x"}) is None  # the user decides


def test_fallback_picks_the_first_system_bundle(fixups: ModuleType, tmp_path: Path) -> None:
    assert fixups.fallback_bundle({}) is None  # nothing to fall back to

    (tmp_path / "bundle.pem").write_text("B")
    assert fixups.fallback_bundle({}) == str(tmp_path / "bundle.pem")
    (tmp_path / "certs" / "ca-certificates.crt").write_text("A")
    assert fixups.fallback_bundle({}) == str(tmp_path / "certs" / "ca-certificates.crt")


class _Context:
    loaded: list[str]

    def __init__(self) -> None:
        self.loaded = []

    def set_default_verify_paths(self) -> None:
        self.loaded.append("defaults")

    def load_verify_locations(self, cafile: str) -> None:
        self.loaded.append(cafile)


def test_ssl_default_contexts_also_load_the_fallback(
    fixups: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    (tmp_path / "bundle.pem").write_text("B")
    ssl = ModuleType("ssl")
    ssl.SSLContext = type("SSLContext", (_Context,), {})  # type: ignore[attr-defined]

    fixups.apply(ssl)

    context = ssl.SSLContext()
    context.set_default_verify_paths()
    assert context.loaded == ["defaults", str(tmp_path / "bundle.pem")]

    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "private-ca.pem"))  # set by the app later
    pinned = ssl.SSLContext()
    pinned.set_default_verify_paths()
    assert pinned.loaded == ["defaults"]  # the app's choice is not widened


def test_an_unusable_fallback_bundle_never_breaks_contexts(
    fixups: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    (tmp_path / "bundle.pem").write_text("not a certificate")

    class Rejects(_Context):
        def load_verify_locations(self, cafile: str) -> None:
            raise OSError("bad bundle")

    ssl = ModuleType("ssl")
    ssl.SSLContext = type("SSLContext", (Rejects,), {})  # type: ignore[attr-defined]
    ssl.SSLError = OSError  # type: ignore[attr-defined]
    fixups.apply(ssl)

    context = ssl.SSLContext()
    context.set_default_verify_paths()  # as OpenSSL's own defaults: never raises
    assert context.loaded == ["defaults"]


class _Cdll:
    """ctypes.CDLL as far as the fixup needs: only paths in ``LOADABLE`` load."""

    LOADABLE = frozenset({"/cache/site/lib/libbundled.so", "libsystem.so"})

    def __init__(self, name: object, mode: int = 0) -> None:
        if name not in self.LOADABLE:
            raise OSError(f"{name}: cannot open shared object file")
        self._name = name


def _ctypes(fixups: ModuleType, monkeypatch: pytest.MonkeyPatch, bundled: dict[str, str]) -> type:
    """A ctypes module with the fixup applied; ``bundled`` maps file names to cached paths."""
    native = ModuleType("_fsbpyr_rt.native")
    native.callers = []  # type: ignore[attr-defined]

    def bundled_library(name: str, caller: str | None = None) -> str | None:
        native.callers.append(caller)
        return bundled.get(name)

    native.bundled_library = bundled_library  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_fsbpyr_rt", ModuleType("_fsbpyr_rt"))
    monkeypatch.setitem(sys.modules, "_fsbpyr_rt.native", native)
    ctypes = ModuleType("ctypes")
    ctypes.CDLL = type("CDLL", (_Cdll,), {})  # type: ignore[attr-defined]
    fixups.apply(ctypes)
    cdll: type = ctypes.CDLL
    return cdll


def test_a_library_missing_by_name_is_loaded_from_the_payload(
    fixups: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    cdll = _ctypes(fixups, monkeypatch, {"libbundled.so": "/cache/site/lib/libbundled.so"})

    assert cdll("libbundled.so")._name == "/cache/site/lib/libbundled.so"
    assert cdll(Path("libbundled.so"))._name == "/cache/site/lib/libbundled.so"  # a PathLike
    assert sys.modules["_fsbpyr_rt.native"].callers == [__file__] * 2  # the module calling CDLL


def test_a_system_library_of_that_name_comes_first(
    fixups: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    cdll = _ctypes(fixups, monkeypatch, {"libsystem.so": "/cache/site/lib/libsystem.so"})

    assert cdll("libsystem.so")._name == "libsystem.so"


@pytest.mark.parametrize("name", ["libnone.so", "/opt/lib/libbundled.so", "", None, b"lib.so"])
def test_other_failures_raise_as_before(
    fixups: ModuleType, monkeypatch: pytest.MonkeyPatch, name: object
) -> None:
    """No bundled library of that name, a path, or no name to look up: the original error."""
    cdll = _ctypes(fixups, monkeypatch, {"libbundled.so": "/cache/site/lib/libbundled.so"})

    with pytest.raises(OSError, match="cannot open shared object file"):
        cdll(name)


def test_a_cache_that_cannot_be_used_leaves_the_original_error(
    fixups: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    cdll = _ctypes(fixups, monkeypatch, {})
    native = sys.modules["_fsbpyr_rt.native"]

    def unusable(name: str, caller: str | None = None) -> str:
        raise PermissionError(13, "cache not writable")

    monkeypatch.setattr(native, "bundled_library", unusable)

    with pytest.raises(OSError, match="cannot open shared object file"):
        cdll("libbundled.so")


def test_multiprocessing_children_start_from_this_binary(fixups: ModuleType) -> None:
    spawn = ModuleType("multiprocessing.spawn")
    chosen: list[str] = []
    spawn.set_executable = chosen.append  # type: ignore[attr-defined]

    fixups.apply(spawn)

    assert chosen == ["/proc/self/exe"]


class _FileIO:
    """parso.file_io.FileIO as far as the fixup needs: its time is that of a file on disk."""

    def __init__(self, path: str) -> None:
        self.path = path

    def get_last_modified(self) -> float | None:
        try:
            return os.path.getmtime(self.path)
        except FileNotFoundError:
            return None


def _parso(fixups: ModuleType, monkeypatch: pytest.MonkeyPatch, executable: Path) -> type:
    """A parso.file_io module with the fixup applied, in a process running ``executable``."""
    standin = ModuleType("_fsbpyr")
    standin.info = lambda: {"executable": str(executable)}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_fsbpyr", standin)
    file_io = ModuleType("parso.file_io")
    file_io.FileIO = type("FileIO", (_FileIO,), {})  # type: ignore[attr-defined]
    fixups.apply(file_io)
    parsed: type = file_io.FileIO
    return parsed


def test_the_time_of_a_payload_file_is_that_of_the_executable(
    fixups: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "app"
    executable.write_bytes(b"ELF")
    file_io = _parso(fixups, monkeypatch, executable)

    # below a regular file: NotADirectoryError, which parso takes for a failure
    payload_file = file_io(f"{executable}/json/__init__.py")
    assert payload_file.get_last_modified() == executable.stat().st_mtime


def test_other_times_are_as_before(
    fixups: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "app"
    executable.write_bytes(b"ELF")
    (tmp_path / "other").write_bytes(b"x")
    file_io = _parso(fixups, monkeypatch, executable)

    assert file_io(str(tmp_path / "missing.py")).get_last_modified() is None
    on_disk = tmp_path / "other"
    assert file_io(str(on_disk)).get_last_modified() == on_disk.stat().st_mtime
    with pytest.raises(NotADirectoryError):  # a regular file that is not the executable
        file_io(f"{tmp_path}/other/x.py").get_last_modified()
    (tmp_path / "app2").write_bytes(b"x")  # the executable's name is no prefix of its path
    with pytest.raises(NotADirectoryError):
        file_io(f"{tmp_path}/app2/x.py").get_last_modified()
