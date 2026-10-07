"""_fsbpyr_rt.fixups: stdlib adjustments applied right after a module loads (Phase 6b)."""

import importlib.util
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


def test_multiprocessing_children_start_from_this_binary(fixups: ModuleType) -> None:
    spawn = ModuleType("multiprocessing.spawn")
    chosen: list[str] = []
    spawn.set_executable = chosen.append  # type: ignore[attr-defined]

    fixups.apply(spawn)

    assert chosen == ["/proc/self/exe"]
