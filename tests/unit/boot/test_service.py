"""_fsbpyr_rt.service: JOURNAL_STREAM matching and the PID-1 SIGTERM handler (plan D14, D41)."""

import importlib
import io
import os
import signal
import sys
from collections.abc import Iterator
from types import ModuleType

import pytest

from .conftest import BOOT_DIR


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.syspath_prepend(str(BOOT_DIR))
    monkeypatch.delitem(sys.modules, "_fsbpyr_rt.service", raising=False)
    yield importlib.import_module("_fsbpyr_rt.service")
    sys.modules.pop("_fsbpyr_rt.service", None)


@pytest.fixture
def restored_sigterm() -> Iterator[None]:
    before = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, before)


def _pipe_text(buffer_size: int = -1) -> tuple[io.TextIOWrapper, io.TextIOWrapper, bytes]:
    """A write stream on a pipe, a read stream, and the JOURNAL_STREAM value of the pipe."""
    read, write = os.pipe()
    status = os.fstat(write)
    value = f"{status.st_dev}:{status.st_ino}".encode()
    return (
        open(write, "w", buffering=buffer_size),
        open(read),
        value,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (b"8:12345", (8, 12345)),
        (b"0:0", (0, 0)),
        (b"", None),
        (b"8", None),
        (b"8:", None),
        (b":8", None),
        (b"a:b", None),
        (b"8:-1", None),
        (b"8:1:2", None),
    ],
)
def test_journal_identity_parses_a_decimal_pair_only(
    service: ModuleType, value: bytes, expected: tuple[int, int] | None
) -> None:
    assert service.journal_identity(value) == expected


def test_a_matching_stream_becomes_line_buffered(
    service: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, reader, value = _pipe_text()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", None)
    with out, reader:
        service.line_buffer_journal(value)
        out.write("line\n")

        assert out.line_buffering
        os.set_blocking(reader.fileno(), False)
        assert os.read(reader.fileno(), 100) == b"line\n"


def test_another_stream_stays_block_buffered(
    service: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, reader, _ = _pipe_text()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", None)
    with out, reader:
        service.line_buffer_journal(b"1:2")
        service.line_buffer_journal(b"garbage")

        assert not out.line_buffering


def test_an_unbuffered_stream_is_left_alone(
    service: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    read, write = os.pipe()
    value = f"{os.fstat(write).st_dev}:{os.fstat(write).st_ino}".encode()
    out = io.TextIOWrapper(io.FileIO(write, "w"), write_through=True)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", None)
    with out:
        service.line_buffer_journal(value)

        assert out.write_through and not out.line_buffering
    os.close(read)


def test_a_closed_stream_does_not_match(service: ModuleType) -> None:
    out, reader, _ = _pipe_text()
    out.close()
    reader.close()

    assert not service.is_journal(out, (1, 2))
    assert not service.is_journal(None, (1, 2))


@pytest.mark.usefixtures("restored_sigterm")
def test_the_default_sigterm_disposition_gets_the_exit_143_handler(service: ModuleType) -> None:
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    service.terminate_on_sigterm()

    handler = signal.getsignal(signal.SIGTERM)
    assert callable(handler)
    with pytest.raises(SystemExit) as raised:
        handler(signal.SIGTERM, None)
    assert raised.value.code == 143


@pytest.mark.usefixtures("restored_sigterm")
@pytest.mark.parametrize("own", [signal.SIG_IGN, lambda signum, frame: None])
def test_an_existing_sigterm_disposition_is_kept(service: ModuleType, own: object) -> None:
    signal.signal(signal.SIGTERM, own)  # type: ignore[arg-type]

    service.terminate_on_sigterm()

    assert signal.getsignal(signal.SIGTERM) is own


@pytest.mark.usefixtures("restored_sigterm")
@pytest.mark.parametrize(("pid", "installed"), [(1, True), (2, False)])
def test_only_process_1_gets_the_handler(
    service: ModuleType, pid: int, installed: bool
) -> None:
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    service.start(None, pid)

    assert (signal.getsignal(signal.SIGTERM) != signal.SIG_DFL) is installed
