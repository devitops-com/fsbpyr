"""Keys and signatures for the tests of docs/signing.md.

The release script makes the real ones, once per session (0.25 s a key), so that no private key
is kept in the repository and fsbpyr's reader is checked against files that it did not write. The
stand-in for ``_fsbpyr`` does no signing crypto, and is reached through ``fsbpyr.signing``'s two
seams: never through ``sys.modules``, where vcs.py and build.py would take it for a built fsbpyr.
"""

import fcntl
import hashlib
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import time
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import pytest

from fsbpyr import signing

from . import requires

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "tools" / "release" / "release-signing.sh"
PASSPHRASE = "the tests' passphrase"  # noqa: S105  of keys made and thrown away in a session
SIGNATURE_SIZE = 3373
ED25519_SIZE = 64
PUB_SIZE = 2839
KEY_SIZE = 6088
TERMINAL_TIMEOUT = 300


class Key(NamedTuple):
    key: Path
    pub: Path


def statement(project: str, data: bytes) -> bytes:
    """The statement, written out here apart from fsbpyr.signing's: a test of that one compares."""
    digest = hashlib.sha256(data).hexdigest()
    return f"fsbpyr-signature 1\nsubject {project}\nsha256 {digest}\n".encode()


def script(
    *args: str | Path, passphrase: str | None = PASSPHRASE, cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """release-signing.sh with its arguments, on no terminal."""
    requires.openssl_with_ml_dsa()
    environment = {**os.environ, **(env or {})}
    environment.pop("FSBPYR_SIGNING_PASSPHRASE", None)
    if passphrase is not None:
        environment["FSBPYR_SIGNING_PASSPHRASE"] = passphrase
    return subprocess.run(["bash", SCRIPT, *map(str, args)], cwd=cwd, env=environment,  # noqa: S607
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          start_new_session=True, check=False)  # fmt: skip


def new_key(name: Path, passphrase: str = PASSPHRASE) -> Key:
    """``name``.key and ``name``.pub, from the script."""
    done = script("new-key", name, passphrase=passphrase)
    assert done.returncode == 0, done.stderr
    return Key(name.with_name(f"{name.name}.key"), name.with_name(f"{name.name}.pub"))


def sign(key: Path, file: Path, passphrase: str = PASSPHRASE) -> Path:
    """``file``.sig, from the script: signed as fsbpyr, the only subject it signs."""
    done = script("sign", key, file, passphrase=passphrase)
    assert done.returncode == 0, done.stderr
    return file.with_name(f"{file.name}.sig")


def signed_as_release(key: Path, name: str, content: bytes, directory: Path) -> bytes:
    """The ``.sig`` of a release's sums file ``name``, as the sign job makes it."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.unlink(missing_ok=True)
    path.with_name(f"{name}.sig").unlink(missing_ok=True)
    path.write_bytes(content)
    return sign(key, path).read_bytes()


def sign_statement(
    key: Path, message: bytes, directory: Path, passphrase: str = PASSPHRASE
) -> bytes:
    """Both halves over ``message``, with `openssl pkeyutl`: any subject, unlike the script."""
    requires.openssl_with_ml_dsa()
    path = directory / "message"
    path.write_bytes(message)
    blocks = key.read_text().split("-----END ENCRYPTED PRIVATE KEY-----\n")[:2]
    halves = []
    for index, block in enumerate(blocks):
        part = directory / f"key.{index}"
        part.write_text(f"{block}-----END ENCRYPTED PRIVATE KEY-----\n")
        out = directory / f"half.{index}"
        run_openssl("pkeyutl", "-sign", "-rawin", "-inkey", part, "-passin", f"pass:{passphrase}",
                    "-in", path, "-out", out)  # fmt: skip
        halves.append(out.read_bytes())
    return b"".join(halves)


def run_openssl(*args: str | Path, stdin: bytes | None = None) -> bytes:
    """The openssl command's standard output; it must succeed."""
    done = subprocess.run([requires.tool("openssl"), *map(str, args)], input=stdin,
                          capture_output=True, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    return done.stdout


def on_a_terminal(
    command: Sequence[str | Path], env: dict[str, str], *typed: bytes, interrupt: bool = False,
) -> tuple[int, bytes, list[int]]:
    """``command`` on a terminal of its own: its exit status, what it printed there, and the
    terminal's local flags at the end. Each ``typed`` goes in when a prompt shows; Ctrl-C after
    them with ``interrupt``."""
    primary, secondary = pty.openpty()
    fcntl.ioctl(secondary, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

    def own_terminal() -> None:
        os.setsid()
        fcntl.ioctl(secondary, termios.TIOCSCTTY, 0)
        # a run started in the background has SIGINT ignored, and a child inherits that
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    process = subprocess.Popen([str(part) for part in command], stdin=secondary, stdout=secondary,
                               stderr=secondary, env=env, preexec_fn=own_terminal)  # noqa: PLW1509
    printed, pending, interrupted, answered = b"", list(typed), False, 0
    deadline = time.monotonic() + TERMINAL_TIMEOUT
    while process.poll() is None and time.monotonic() < deadline:
        ready, _, _ = select.select([primary], [], [], 0.1)
        if ready:
            try:
                printed += os.read(primary, 4096)
            except OSError:
                break
        if printed.count(b"Passphrase") > answered:
            answered += 1
            time.sleep(0.05)  # echo goes off before the read
            if pending:
                os.write(primary, pending.pop(0) + b"\n")
            elif interrupt and not interrupted:
                os.write(primary, b"\x03")
                interrupted = True
    flags = termios.tcgetattr(secondary)[3]
    code = process.wait(timeout=30)
    os.close(primary)
    os.close(secondary)
    return code, printed, [flags]


def fingerprint(pub: bytes) -> str:
    return f"SHA256:{hashlib.sha256(pub).hexdigest()}"


def fake_signature(pub: bytes, message: bytes) -> bytes:
    """What the stand-in's ``_verify`` passes: bound to the key and the statement."""
    return hashlib.shake_256(b"\0".join((pub, message))).digest(SIGNATURE_SIZE)


class StandIn:
    """``_fsbpyr``'s four functions for tests that run from source: no crypto, but each refuses
    what the real one refuses when a test depends on it. A key is ``b"key:" + passphrase``."""

    @staticmethod
    def _sha256(file: object) -> bytes:
        if isinstance(file, bytes):
            return hashlib.sha256(file).digest()
        assert isinstance(file, int)
        digest = hashlib.sha256()
        offset = 0
        while chunk := os.pread(file, 1 << 16, offset):
            digest.update(chunk)
            offset += len(chunk)
        return digest.digest()

    @staticmethod
    def _key_new(passphrase: bytes) -> tuple[bytes, bytes]:
        return b"key:" + passphrase, fake_pub(hashlib.sha256(passphrase).hexdigest())

    @staticmethod
    def _sign(key: bytes, passphrase: bytes, message: bytes) -> tuple[bytes, bytes]:
        if not key.startswith(b"key:"):
            raise ValueError("not a key file")
        if key != b"key:" + passphrase:
            raise ValueError("wrong passphrase?")
        pub = fake_pub(hashlib.sha256(passphrase).hexdigest())
        return fake_signature(pub, message), pub

    @staticmethod
    def _verify(pub: bytes, message: bytes, signature: bytes) -> bool:
        if not pub.startswith(_FAKE_PUB) or len(pub) != PUB_SIZE:
            raise ValueError("not a public key file")
        return signature == fake_signature(pub, message)


_FAKE_PUB = b"fake public key "


def fake_pub(seed: str) -> bytes:
    """A .pub of the stand-in: the size of a real one, different for each ``seed``."""
    return (_FAKE_PUB + seed.encode()).ljust(PUB_SIZE, b".")


RELEASE_PUB = fake_pub("the release key")


def use_stand_in(monkeypatch: pytest.MonkeyPatch, *pins: bytes) -> None:
    """Run fsbpyr.signing on the stand-in, with these pins (by default ``RELEASE_PUB``)."""
    monkeypatch.setattr(signing, "crypto", lambda: StandIn)
    monkeypatch.setattr(signing, "pinned_keys", lambda: pins or (RELEASE_PUB,))


def release_signature(sums: bytes, pub: bytes = RELEASE_PUB) -> bytes:
    """A sums file's ``.sig`` under the stand-in: a pinned key's, as fsbpyr's own paths check."""
    return fake_signature(pub, statement("fsbpyr", sums))
