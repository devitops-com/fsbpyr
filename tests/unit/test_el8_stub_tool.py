"""tools/release/el8-stub.py: the EL8 stub of the cache's runtime, checked and named as el8.yml
publishes it."""

import hashlib
import importlib.util
import types
from pathlib import Path
from types import ModuleType

import pytest

from fsbpyr.runtime import pins, registry
from fsbpyr.runtime.stub import IDENTITY_MARKER
from fsbpyr.targets import host_target
from fsbpyr.vcs import release_stub_asset

from ..support.elf import make_elf

TOOL = Path(__file__).parents[2] / "tools" / "release" / "el8-stub.py"
PYTHON = "3.14"


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("el8_stub", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected_id() -> str:
    return registry.runtime_id(pins.pin_for(PYTHON, registry.host_triple()), baseline="2.28")


def stub_with(version: str, identity: str) -> bytes:
    """An ELF needing ``version`` of libc.so.6, that carries ``identity`` as main.c's string."""
    elf = make_elf(needed=("libc.so.6",), versions=(("libc.so.6", (version,)),))
    return elf + IDENTITY_MARKER + identity.encode() + b"\0"


def runtime_with(stub: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    def find(python: str, *, baseline: str) -> types.SimpleNamespace:
        assert (python, baseline) == (PYTHON, "2.28")
        return types.SimpleNamespace(stub=stub)

    monkeypatch.setattr(registry, "find_runtime", find)


def test_a_stub_that_needs_glibc_2_28_at_most_is_written_under_its_asset_name(
    tool: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stub = stub_with("GLIBC_2.25", expected_id())
    runtime_with(stub, monkeypatch)

    status = tool.main([PYTHON, str(tmp_path / "out")])

    asset = release_stub_asset(PYTHON, host_target().name, "2.28")
    assert status == 0
    assert (tmp_path / "out" / asset).read_bytes() == stub
    assert f"{asset}  {hashlib.sha256(stub).hexdigest()}" in capsys.readouterr().out


def test_a_stub_that_needs_more_than_glibc_2_28_is_refused(
    tool: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_with(stub_with("GLIBC_2.34", expected_id()), monkeypatch)

    assert tool.main([PYTHON, str(tmp_path / "out")]) == 1
    assert "needs GLIBC_2.34, not at most GLIBC_2.28" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_a_stub_of_another_runtime_is_refused(
    tool: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_with(stub_with("GLIBC_2.17", "cp314-x86_64-unknown-linux-gnu-000000000000"),
                 monkeypatch)  # fmt: skip

    assert tool.main([PYTHON, str(tmp_path / "out")]) == 1
    assert f"this fsbpyr expects {expected_id()}" in capsys.readouterr().err


def test_a_cache_without_the_runtime_is_an_error_and_a_bad_command_line_is_usage(
    tool: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path / "cache"))

    assert tool.main([PYTHON, str(tmp_path / "out")]) == 1
    assert "fsbpyr runtime build --python 3.14 --glibc 2.28" in capsys.readouterr().err
    assert tool.main([PYTHON]) == 2
