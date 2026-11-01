"""Keys and signatures for the tests of docs/signing.md.

The release script makes the real ones, once per session (0.25 s a key), so that no private key
is kept in the repository and fsbpyr's reader is checked against files that it did not write. Tests
that run from source have no ``_fsbpyr``: the openssl command does its crypto, on those same keys,
reached through ``fsbpyr.signing``'s two seams: never through ``sys.modules``, where vcs.py and
build.py would take it for a built fsbpyr.
"""

import fcntl
import functools
import hashlib
import os
import pty
import select
import signal
import struct
import subprocess
import tempfile
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
RELEASE_KEY = "release"
ALGORITHMS = ("ED25519", "ML-DSA-65")
END_KEY = b"-----END ENCRYPTED PRIVATE KEY-----\n"
END_PUB = b"-----END PUBLIC KEY-----\n"
TERMINAL_TIMEOUT = 300
TERMINAL_DRAIN = 0.2


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
    halves = []
    for index, block in enumerate(_blocks(key.read_bytes(), END_KEY)[:2]):
        part = directory / f"key.{index}"
        part.write_bytes(block)
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
    prompt: bytes = b"Passphrase",
) -> tuple[int, bytes, list[int]]:
    """``command`` on a terminal of its own: its exit status, what it printed there, and the
    terminal's local flags at the end. Each ``typed`` goes in when a ``prompt`` shows; Ctrl-C after
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
        if printed.count(prompt) > answered:
            answered += 1
            time.sleep(0.05)  # echo goes off before the read
            if pending:
                os.write(primary, pending.pop(0) + b"\n")
            elif interrupt and not interrupted:
                os.write(primary, b"\x03")
                interrupted = True
    # What it printed as it ended: the terminal hands it over a moment after the process is gone.
    while select.select([primary], [], [], TERMINAL_DRAIN)[0]:
        try:
            printed += os.read(primary, 4096)
        except OSError:
            break
    flags = termios.tcgetattr(secondary)[3]
    code = process.wait(timeout=30)
    os.close(primary)
    os.close(secondary)
    return code, printed, [flags]


def fingerprint(pub: bytes) -> str:
    return f"SHA256:{hashlib.sha256(pub).hexdigest()}"


def _blocks(pem: bytes, end: bytes) -> list[bytes]:
    return [block + end for block in pem.split(end)[:-1]]


def _public_blocks(pub: bytes) -> list[bytes]:
    """The two keys of a .pub, as sig.c's ``public_keys`` wants them: Ed25519, then ML-DSA-65, each
    as openssl writes it, and nothing else in the file."""
    blocks = _blocks(pub, END_PUB)
    if len(blocks) != len(ALGORITHMS) or b"".join(blocks) != pub:
        raise ValueError("not a public key file")
    for block, algorithm in zip(blocks, ALGORITHMS, strict=True):
        done = subprocess.run([requires.tool("openssl"), "pkey", "-pubin", "-pubout", "-text_pub"],
                              input=block, capture_output=True, check=False)  # fmt: skip
        if done.returncode != 0 or not done.stdout.startswith(block + algorithm.encode()):
            raise ValueError("not a public key file")
    return blocks


def _half_good(directory: Path, block: bytes, half: bytes) -> bool:
    key, signature = directory / "pub", directory / "sig"
    key.write_bytes(block)
    signature.write_bytes(half)
    return subprocess.run([requires.tool("openssl"), "pkeyutl", "-verify", "-rawin", "-pubin",
                           "-inkey", key, "-in", directory / "message", "-sigfile", signature],
                          capture_output=True, check=False).returncode == 0  # fmt: skip


class OpenSSL:
    """``_fsbpyr``'s four functions for tests that run from source, on the openssl command: real
    keys and real signatures, refused as sig.c refuses them. tests/e2e/test_signatures.py tests
    sig.c itself."""

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
        with tempfile.TemporaryDirectory() as directory:
            key = new_key(Path(directory) / "k", os.fsdecode(passphrase))
            return key.key.read_bytes(), key.pub.read_bytes()

    @staticmethod
    def _sign(key: bytes, passphrase: bytes, message: bytes) -> tuple[bytes, bytes]:
        if len(_blocks(key, END_KEY)) != len(ALGORITHMS):
            raise ValueError("not a key file")
        secret = os.fsdecode(passphrase)
        pubs = [subprocess.run([requires.tool("openssl"), "pkey", "-passin", f"pass:{secret}",
                                "-pubout"], input=block, capture_output=True, check=False)
                for block in _blocks(key, END_KEY)]  # fmt: skip
        if any(done.returncode != 0 for done in pubs):
            raise ValueError("wrong passphrase?")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "k.key"
            path.write_bytes(key)
            signature = sign_statement(path, message, Path(directory), secret)
        return signature, b"".join(done.stdout for done in pubs)

    @staticmethod
    def _verify(pub: bytes, message: bytes, signature: bytes) -> bool:
        blocks = _public_blocks(pub)
        if len(signature) != SIGNATURE_SIZE:
            return False
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "message").write_bytes(message)
            halves = signature[:ED25519_SIZE], signature[ED25519_SIZE:]
            return all(_half_good(directory, block, half)
                       for block, half in zip(blocks, halves, strict=True))  # fmt: skip


@functools.cache
def _key_directory() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="fsbpyr-test-keys-")


@functools.cache
def named_key(name: str) -> Key:
    """The key ``name``, of the script, made when this process first asks for it. Not while tests
    are collected: each xdist worker has its own keys, and test ids must not differ between them."""
    return new_key(Path(_key_directory().name) / name)


def pub_of(name: str) -> bytes:
    return named_key(name).pub.read_bytes()


@functools.cache
def signature_by(name: str, message: bytes) -> bytes:
    """The ``.sig`` of ``message`` by the key ``name``."""
    with tempfile.TemporaryDirectory() as directory:
        return sign_statement(named_key(name).key, message, Path(directory))


def use_openssl(monkeypatch: pytest.MonkeyPatch, *pins: bytes) -> None:
    """Run fsbpyr.signing on the openssl command, with these pins (by default ``RELEASE_KEY``'s)."""
    monkeypatch.setattr(signing, "crypto", lambda: OpenSSL)
    monkeypatch.setattr(signing, "pinned_keys", lambda: pins or (pub_of(RELEASE_KEY),))


def release_signature(sums: bytes, key: str = RELEASE_KEY) -> bytes:
    """A sums file's ``.sig``: by the key ``key``, as a release's, whose subject is fsbpyr."""
    return signature_by(key, statement("fsbpyr", sums))
