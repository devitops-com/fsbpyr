"""Start-up policies for services and containers (plan D14, D41; decided while
building it, 2026-10-25).

``JOURNAL_STREAM``: systemd sets ``<device>:<inode>`` (decimal) of the journal socket for a
service whose stdout or stderr go to the journal. A stream that is that file is line-buffered,
so each line reaches the journal as it is printed, unless it is unbuffered already
(``PYTHONUNBUFFERED``). A value that does not parse, or names another file, changes nothing.

PID 1: an app that is process 1 (a container without an init) never dies of SIGTERM, as the
kernel drops a signal that process 1 has no handler for, and `python app.py` is the same. Here
SIGTERM raises ``SystemExit(143)`` (128 + 15), when its disposition is still the default. The
app may install its own handler afterwards, which replaces this one.
"""

import io
import os
import signal
import sys
from types import FrameType
from typing import TextIO

EXIT_SIGTERM = 128 + signal.SIGTERM


def journal_identity(value: bytes) -> tuple[int, int] | None:
    """The (device, inode) a ``JOURNAL_STREAM`` value names, or None if it is no such pair."""
    device, colon, inode = value.partition(b":")
    if not colon or not device.isdigit() or not inode.isdigit():
        return None
    return int(device), int(inode)


def is_journal(stream: TextIO | None, identity: tuple[int, int]) -> bool:
    """Whether ``stream`` is the file ``identity`` names."""
    if stream is None:
        return False
    try:
        status = os.fstat(stream.fileno())
    except (OSError, ValueError):  # closed, or no file descriptor behind it
        return False
    return (status.st_dev, status.st_ino) == identity


def line_buffer_journal(value: bytes) -> None:
    """Makes stdout and stderr line-buffered where they are the journal named by ``value``."""
    if (identity := journal_identity(value)) is None:
        return
    for stream in (sys.stdout, sys.stderr):
        if (
            isinstance(stream, io.TextIOWrapper)
            and not stream.write_through
            and is_journal(stream, identity)
        ):
            stream.reconfigure(line_buffering=True)


def _terminate(signum: int, frame: FrameType | None) -> None:
    raise SystemExit(EXIT_SIGTERM)


def terminate_on_sigterm() -> None:
    """Installs the SystemExit(143) handler when SIGTERM still has its default disposition."""
    if signal.getsignal(signal.SIGTERM) == signal.SIG_DFL:
        signal.signal(signal.SIGTERM, _terminate)


def start(journal: bytes | None, pid: int) -> None:
    """The policies a start applies: ``journal`` is JOURNAL_STREAM's value, ``pid`` ours."""
    if journal is not None:
        line_buffer_journal(journal)
    if pid == 1:
        terminate_on_sigterm()
