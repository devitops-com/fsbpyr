"""Signatures (docs/signing.md): the commands ``key new``, ``key trust``, ``sign`` and ``verify``,
the signing of a build's output and the checks of fsbpyr's own downloads.

The crypto is launcher C (sig.c), reached through ``crypto``; the keys fsbpyr pins are reached
through ``pinned_keys``. Nothing else here depends on a built fsbpyr, so tests replace those two.
No variable or option changes the pins. Python handles the arguments, the files, the statement,
the passphrase, the trust store and the messages.

A name read from a file is not to be trusted: a forged executable can name anything, and its D31
digest passes, since the forger writes it. Such a name is checked before it is used or printed,
is never normalized, and only a checked name is joined into a path.
"""

import contextlib
import errno
import importlib.resources
import json
import os
import re
import stat
import tempfile
import termios
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from packaging.utils import canonicalize_name

from .payload.format import PayloadError
from .payload.reader import Executable
from .runtime.registry import RuntimeCacheError, home_directory, secure_directory
from .text import shown

KEY_VARIABLE = "FSBPYR_SIGNING_KEY"
PASSPHRASE_VARIABLE = "FSBPYR_SIGNING_PASSPHRASE"  # noqa: S105  a variable's name
RELEASE_PROJECT = "fsbpyr"  # only the pinned keys sign it
SUMS_FILES = ("SHA256SUMS", "SHA256SUMS-glibc2.28")
SIGNATURE_SIZE = 3373
PUB_SIZE = 2839
MAX_PASSPHRASE = 1024
MAX_NAME = 64
_MAX_KEY_FILE = 16 << 10  # sig.c reads no more; a .key has 6088 bytes
_MAX_SUMS = 16 << 20
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_FINGERPRINT = re.compile(r"SHA256:([0-9a-fA-F]{64})")
_SHA256_HEX = 64
_PUBLIC_MODE = 0o644
_PRIVATE_MODE = 0o600
_NO_PASSPHRASE = f"no passphrase: set {PASSPHRASE_VARIABLE} or run this on a terminal"
_NO_NAME = "its build info names no valid project"
_BUILT_ONLY = "signing needs a built fsbpyr"
RELEASE_KEYS_DOCS = "https://github.com/devitops-com/fsbpyr/wiki/User-Installing#release-keys"
_WARNING_WIDTH = 62


class SigningError(RuntimeError):
    """A usage or setup error: exit 2. Never a verdict about a file."""


class NotVerified(RuntimeError):
    """The file does not pass: exit 1. The message follows `NOT VERIFIED: `."""


class Crypto(Protocol):
    """``_fsbpyr``'s four functions (sig.c)."""

    def _sha256(self, file: int | bytes, /) -> bytes: ...
    def _key_new(self, passphrase: bytes, /) -> tuple[bytes, bytes]: ...
    def _sign(self, key: bytes, passphrase: bytes, statement: bytes, /) -> tuple[bytes, bytes]: ...
    def _verify(self, pub: bytes, statement: bytes, signature: bytes, /) -> bool: ...


def crypto() -> Crypto:
    """The launcher's functions; they exist in executables only."""
    try:
        import _fsbpyr  # noqa: PLC0415  # only in executables
    except ImportError:
        raise SigningError(_BUILT_ONLY) from None
    return cast("Crypto", _fsbpyr)


def available() -> bool:
    """Whether this fsbpyr can check signatures: from source it cannot."""
    try:
        crypto()
    except SigningError:
        return False
    return True


def pinned_keys() -> tuple[bytes, ...]:
    """The .pub files packaged with this fsbpyr (src/fsbpyr/release-keys/); a signature by any of
    them passes as fsbpyr's. None in a build without them: then nothing does."""
    keys = importlib.resources.files("fsbpyr").joinpath("release-keys")
    if not keys.is_dir():
        return ()
    return tuple(entry.read_bytes() for entry in sorted(keys.iterdir(), key=lambda e: e.name)
                 if entry.name.endswith(".pub"))  # fmt: skip


def fingerprint(pub: bytes) -> str:
    return f"SHA256:{crypto()._sha256(pub).hex()}"  # private to fsbpyr


def valid_name(name: object) -> bool:
    """Whether ``name`` is a project name of a statement (§2.1), checked on the whole of it."""
    return isinstance(name, str) and len(name) <= MAX_NAME and bool(_NAME.fullmatch(name))


def typed_name(text: str) -> str:
    """A name the user typed: ASCII first (canonicalize_name turns the Kelvin sign into k), then
    in normal form; it must then be a valid one."""
    if text.isascii():
        name = canonicalize_name(text)
        if valid_name(name):
            return name
    raise SigningError(f"not a project name: {shown(text)}: give letters, digits and single "
                       f"hyphens, at most {MAX_NAME} characters")  # fmt: skip


def statement(project: str, digest: bytes) -> bytes:
    """What both signatures sign (§2.1). It is never stored: the signer and the checker each
    build it, so nothing in a file can change what is checked."""
    return f"fsbpyr-signature 1\nsubject {project}\nsha256 {digest.hex()}\n".encode("ascii")


def _regular(path: Path) -> int:
    """A descriptor of the regular file ``path``. Opened without blocking: a FIFO in a download
    must not hang a check. OSError for anything else."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise OSError(errno.EINVAL, "not a regular file")
    return descriptor


def _read_limited(path: Path, limit: int) -> bytes | None:
    """``path``'s content, or None if it has more than ``limit`` bytes."""
    descriptor = _regular(path)
    try:
        data = bytearray()
        while len(data) <= limit and (piece := os.read(descriptor, limit + 1 - len(data))):
            data += piece
    finally:
        os.close(descriptor)
    return bytes(data) if len(data) <= limit else None


def _digest(path: Path) -> bytes:
    """The SHA-256 of the regular file ``path``."""
    descriptor = _regular(path)
    try:
        return crypto()._sha256(descriptor)  # private to fsbpyr
    finally:
        os.close(descriptor)


def _no_build_info(path: Path) -> SigningError:
    return SigningError(f"{shown(path)} has no build info: name its project with --project")


def recorded_project(path: Path) -> tuple[bool, object]:
    """Whether ``path`` is an executable fsbpyr built, and the project its build info names, as
    it is there: anything, or nothing. A file that does not parse is not such an executable."""
    try:
        with Executable.open(path) as executable:
            text = executable.payload.build_info
    except (PayloadError, ValueError):  # ValueError: a mapping that cannot be made, a bad string
        return False, None
    try:
        info = json.loads(text)
    except (ValueError, RecursionError):  # bad JSON, numbers beyond the digit limit
        return True, None
    project = info.get("project") if isinstance(info, dict) else None
    return True, project.get("name") if isinstance(project, dict) else None


def _is_shown_as(path: Path, real: Path) -> Path:
    """The path to print: as given, unless a link was followed."""
    return path if os.path.abspath(path) == str(real) else real


@dataclass(frozen=True, slots=True)
class Verified:
    """A file that passed: as whom, by which key, and through which sums file."""

    path: Path
    project: str
    key: str  # the fingerprint
    sums: str | None = None
    listed: tuple[str, ...] = ()

    def line(self) -> str:
        through = (f"listed as {', '.join(map(shown, self.listed))} in {self.sums}, "
                   if self.sums else "")  # fmt: skip
        return (f"VERIFIED  {shown(self.path)}  (project {self.project}, {through}"
                f"Ed25519 + ML-DSA-65, key {self.key})")


def trust_store() -> Path:
    """Where ``key trust`` saves keys: ${XDG_CONFIG_HOME:-~/.config}/fsbpyr/trusted. A relative
    XDG_CONFIG_HOME is ignored, and so is a relative HOME, as for the cache: a download unpacked
    in the current directory must not bring its own trusted keys. Like the cache it is ours alone,
    and the way to it trusted, or another user could plant a key."""
    base = os.environ.get("XDG_CONFIG_HOME", "")
    try:
        root = Path(base) if os.path.isabs(base) else home_directory() / ".config"
        return secure_directory(root / "fsbpyr" / "trusted")
    except (RuntimeCacheError, OSError) as exc:
        raise SigningError(f"the trust store cannot be used: {exc}") from exc


def _no_trusted_key(project: str) -> SigningError:
    return SigningError(f"no trusted key for {project}: get the publisher's key and its "
                        f"fingerprint, then: fsbpyr key trust {project} KEY.pub "
                        "SHA256:…")  # fmt: skip


def checking_keys(project: str) -> tuple[bytes, ...]:
    """The keys that may sign as ``project``, a checked name: the pinned ones for fsbpyr, and
    no file can add to those; otherwise the one ``key trust`` saved."""
    if project == RELEASE_PROJECT:
        keys = pinned_keys()
        if not keys:
            raise SigningError("this fsbpyr pins no release keys: it is not a release")
        return keys
    path = trust_store() / f"{project}.pub"
    try:
        pub = _read_limited(path, PUB_SIZE)
    except FileNotFoundError:
        raise _no_trusted_key(project) from None
    except OSError as exc:
        raise SigningError(f"cannot read {path}: {exc.strerror or exc}") from exc
    if pub is None:
        raise SigningError(f"{path} is not a public key file")
    return (pub,)


def _signed_by(keys: Iterable[bytes], message: bytes, signature: bytes) -> bytes | None:
    """The first of ``keys`` that signed ``message``; only a True from the C check passes."""
    for pub in keys:
        try:
            if crypto()._verify(pub, message, signature) is True:  # private to fsbpyr
                return pub
        except ValueError as exc:
            raise SigningError(f"a key to check with is not a public key file: {exc}") from exc
    return None


def _read_signature(path: Path, label: str) -> bytes | None:
    """A .sig's bytes, or None if there is none. One that cannot be a signature is NotVerified."""
    try:
        info = os.stat(path)
        data = _read_limited(path, SIGNATURE_SIZE) if stat.S_ISREG(info.st_mode) else None
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise NotVerified(f"{label}: cannot read {shown(path.name)}: {exc.strerror or exc}"
                          ) from exc
    if data is None or len(data) != SIGNATURE_SIZE:
        raise NotVerified(f"{label}: {shown(path.name)} is not a signature ({info.st_size} bytes)")
    return data


def sums_lines(text: str) -> Iterator[tuple[str, str]]:
    """(lowercase digest, name) of each line of a checksums file as `sha256sum` writes it."""
    for line in text.splitlines():
        digest, _, name = line.partition(" ")
        if len(digest) == _SHA256_HEX:
            yield digest.lower(), name.lstrip(" *")


def listed_names(text: str, digest: str) -> tuple[str, ...]:
    """What a sums file lists ``digest`` (lowercase hex) as, every name if there are several."""
    return tuple(name for found, name in sums_lines(text) if found == digest)


def _through_sums(
    directory: Path, project: str, keys: Sequence[bytes], digest: bytes
) -> tuple[str, bytes, tuple[str, ...]] | None:
    """The signed sums file in ``directory`` that lists ``digest``: its name, the key, and what
    it lists the digest as. A sums file that fails its signature lists nothing. It is read once,
    so what is checked is what is looked up in."""
    for name in SUMS_FILES:
        try:
            content = _read_limited(directory / name, _MAX_SUMS)
            signature = _read_limited(directory / f"{name}.sig", SIGNATURE_SIZE)
        except OSError:
            continue
        if content is None or signature is None or len(signature) != SIGNATURE_SIZE:
            continue
        own = crypto()._sha256(content)  # private to fsbpyr
        pub = _signed_by(keys, statement(project, own), signature)
        if pub is None:
            continue
        listed = listed_names(content.decode("utf-8", "replace"), digest.hex())
        if listed:
            return name, pub, listed
    return None


def _project_to_check(real: Path, shown_as: str, project: str | None) -> str:
    """The project ``real`` is checked as (§3): a name from its build info, checked, or the one
    typed. NotVerified for a build info without a valid name, or that names another project."""
    typed = None if project is None else typed_name(project)
    executable, recorded = recorded_project(real)
    if not executable:
        if typed is None:
            raise _no_build_info(real)
        return typed
    if not valid_name(recorded):
        raise NotVerified(f"{shown_as}: {_NO_NAME}")
    name = cast("str", recorded)
    if typed is not None and typed != name:
        raise NotVerified(f"{shown_as}: it is an executable of project {name}, not of {typed}")
    return name


def verify(path: Path, project: str | None = None) -> Verified:
    """Check ``path`` (§3): by its own ``.sig``, else through a signed sums file beside it. The
    file a link leads to is what is checked: its ``.sig``, its sums file and its name."""
    real = Path(os.path.realpath(path))
    printed = _is_shown_as(path, real)
    label = shown(printed)
    try:
        digest = _digest(real)
    except OSError as exc:
        raise SigningError(f"cannot read {label}: {exc.strerror or exc}") from exc
    name = _project_to_check(real, label, project)
    keys = checking_keys(name)
    signature = _read_signature(real.with_name(f"{real.name}.sig"), label)
    if signature is not None:
        pub = _signed_by(keys, statement(name, digest), signature)
        if pub is None:
            raise NotVerified(f"{label}: the signature does not match the trusted key of {name}")
        return Verified(printed, name, fingerprint(pub))
    found = _through_sums(real.parent, name, keys, digest)
    if found is None:
        raise NotVerified(f"{label}: no {shown(real.name)}.sig, and no signed {SUMS_FILES[0]} "
                          "beside it lists it")  # fmt: skip
    sums, pub, listed = found
    return Verified(printed, name, fingerprint(pub), sums, listed)


def check_release_file(content: bytes, signature: bytes) -> bool:
    """Whether a pinned key signed ``content`` as fsbpyr: the check of a release's sums file."""
    keys = pinned_keys()
    if len(signature) != SIGNATURE_SIZE:
        return False
    message = statement(RELEASE_PROJECT, crypto()._sha256(content))  # private to fsbpyr
    return _signed_by(keys, message, signature) is not None


def not_pinned_message(tag: str) -> str:
    """A release no pinned key signed: this fsbpyr cannot tell a key change from a forgery."""
    return (f"release {tag} is not signed by a key this fsbpyr pins: either the release keys "
            "changed in a release this fsbpyr did not install, or the download is not genuine. "
            f"Nothing was changed; see {RELEASE_KEYS_DOCS}")


def asset_digest(sums: bytes, asset: str) -> str | None:
    """The lowercase hex digest ``sums`` lists for the file named ``asset``."""
    for digest, name in sums_lines(sums.decode("utf-8", "replace")):
        if name == asset:
            return digest
    return None


def _tty_line(prompt: bytes) -> bytes | None:
    """A line typed on the terminal, with echo off, as the bytes typed; None at end of input.
    Standard input is never read: getpass would, when there is no terminal. Ctrl-C raises
    KeyboardInterrupt, and the terminal is left as it was."""
    try:
        descriptor = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY | os.O_CLOEXEC)
    except OSError:
        raise SigningError(_NO_PASSPHRASE) from None
    with os.fdopen(descriptor, "r+b", buffering=0) as tty:
        before = termios.tcgetattr(descriptor)
        quiet = termios.tcgetattr(descriptor)
        quiet[3] &= ~termios.ECHO
        termios.tcsetattr(descriptor, termios.TCSAFLUSH, quiet)
        try:
            tty.write(prompt)
            line = tty.readline()
        finally:
            termios.tcsetattr(descriptor, termios.TCSAFLUSH, before)
            tty.write(b"\n")
    return line.removesuffix(b"\n") if line.endswith(b"\n") else None


def _checked_passphrase(value: bytes) -> bytes:
    if not value:
        raise SigningError("the passphrase is empty")
    if len(value) > MAX_PASSPHRASE:
        raise SigningError(f"the passphrase is longer than {MAX_PASSPHRASE} bytes")
    return value


def passphrase(given: bytes | None = None, *, new: bool = False) -> bytes:
    """``given``, otherwise FSBPYR_SIGNING_PASSPHRASE, otherwise typed on the terminal (twice for
    a new key). An empty variable counts as unset. Bytes: a key may be made under any."""
    value = given or os.environb.get(PASSPHRASE_VARIABLE.encode())
    if value:
        return _checked_passphrase(value)
    typed = _tty_line(b"Passphrase: ")
    if typed is None:
        raise SigningError(_NO_PASSPHRASE)
    if new and _tty_line(b"Passphrase again: ") != typed:
        raise SigningError("the passphrases differ")
    return _checked_passphrase(typed)


def _place(content: bytes, final: Path, mode: int) -> None:
    """``content`` as ``final``, which must not exist, and appears whole. A hard link, as the
    script's `ln`: it follows no symlink at ``final`` and writes into no directory there."""
    temporary: Path | None = None
    try:
        handle, name = tempfile.mkstemp(dir=final.parent, prefix=f".{final.name}.")
        temporary = Path(name)
        with os.fdopen(handle, "wb") as out:
            out.write(content)
            os.fchmod(out.fileno(), mode)
        os.link(temporary, final)
    except FileExistsError:
        raise SigningError(f"{final} exists; nothing replaced") from None
    except OSError as exc:
        raise SigningError(f"cannot write {final}: {exc.strerror or exc}") from exc
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink()


def key_new(name: Path, given: bytes | None = None) -> str:
    """NAME.key and NAME.pub (§2.3); the fingerprint. Nothing that exists is replaced."""
    crypto()
    key, pub = name.with_name(f"{name.name}.key"), name.with_name(f"{name.name}.pub")
    for path in (key, pub):
        if os.path.lexists(path):
            raise SigningError(f"{path} exists; nothing replaced")
    secret = passphrase(given, new=True)
    key_bytes, pub_bytes = crypto()._key_new(secret)  # private to fsbpyr
    _place(key_bytes, key, _PRIVATE_MODE)
    try:
        _place(pub_bytes, pub, _PUBLIC_MODE)
    except SigningError:
        key.unlink(missing_ok=True)  # ours: the pair is made whole or not at all
        raise
    return fingerprint(pub_bytes)


def _project_to_sign(real: Path, project: str | None) -> str:
    """The project ``real`` is signed as (§3): as for a check, but every refusal is exit 2."""
    typed = None if project is None else typed_name(project)
    executable, recorded = recorded_project(real)
    if executable:
        if not valid_name(recorded):
            raise SigningError(f"{shown(real)}: {_NO_NAME}")
        name = cast("str", recorded)
        if typed is not None and typed != name:
            raise SigningError(f"{shown(real)} is an executable of project {name}, not of {typed}")
    elif typed is None:
        raise _no_build_info(real)
    else:
        name = typed
    if name == RELEASE_PROJECT:
        raise SigningError(f"the subject {RELEASE_PROJECT} is signed only with its release keys, "
                           "by tools/release/release-signing.sh")  # fmt: skip
    return name


def signing_key_path(option: Path | None) -> Path:
    """--signing-key, else FSBPYR_SIGNING_KEY: a path. It has no [tool.fsbpyr] key: the path
    belongs to the machine that builds."""
    value = option or os.environ.get(KEY_VARIABLE)
    if not value:
        raise SigningError(f"no signing key: give --signing-key KEY or set {KEY_VARIABLE}")
    return Path(value)


def sign(
    path: Path, *, project: str | None = None, key: Path, given: bytes | None = None
) -> tuple[Path, str, str]:
    """``path``.sig, beside the file a link leads to, after the C code checked the new signature
    against the key's own public keys; the signature's path, the project and the fingerprint."""
    real = Path(os.path.realpath(path))
    try:
        digest = _digest(real)
    except OSError as exc:
        raise SigningError(f"cannot read {shown(path)}: {exc.strerror or exc}") from exc
    name = _project_to_sign(real, project)
    target = real.with_name(f"{real.name}.sig")
    if os.path.lexists(target):
        raise SigningError(f"{target} exists; nothing replaced")
    try:
        key_bytes = _read_limited(key, _MAX_KEY_FILE)
    except OSError as exc:
        raise SigningError(f"cannot open {key}: {exc.strerror or exc}") from exc
    if key_bytes is None:
        raise SigningError(f"cannot open {key}: not a key file")
    secret = passphrase(given)
    try:
        signature, pub = crypto()._sign(key_bytes, secret, statement(name, digest))
    except ValueError as exc:
        raise SigningError(f"cannot open {key}: {exc}") from exc
    _place(signature, target, _PUBLIC_MODE)
    return target, name, fingerprint(pub)


@dataclass(frozen=True, slots=True)
class Trusted:
    """What ``key trust`` did: the fingerprint saved, and the one replaced."""

    project: str
    key: str
    replaced: str | None = None
    unchanged: bool = False


def _trust_copy(public_key: Path, store: Path) -> tuple[Path, bytes]:
    """PUBKEY read once into a new file in the store: the checks run on this copy, and it is what
    is saved. Hashing PUBKEY by its path and then copying it saves a file swapped in between."""
    try:
        content = _read_limited(public_key, PUB_SIZE + 1)
    except OSError as exc:
        raise SigningError(f"cannot read {public_key}: {exc.strerror or exc}") from exc
    if content is None or len(content) != PUB_SIZE:
        raise SigningError(f"{public_key} is not a public key file: it has not {PUB_SIZE} bytes")
    handle, name = tempfile.mkstemp(dir=store, prefix=".trust-")
    copy = Path(name)
    with os.fdopen(handle, "wb") as out:
        out.write(content)
        os.fchmod(out.fileno(), _PUBLIC_MODE)
    return copy, content


def _checked_fingerprint(content: bytes, public_key: Path, wanted: str) -> str:
    try:
        crypto()._verify(content, b"", b"")  # refuses a .pub that §2.3 refuses
    except ValueError:
        raise SigningError(f"{public_key} is not a public key file") from None
    got = fingerprint(content)
    if got != wanted:
        raise SigningError(f"the fingerprint of {public_key} is {got}, not {wanted}")
    return got


def key_trust(project: str, public_key: Path, expected: str, *, replace: bool = False) -> Trusted:
    """Save PUBKEY as ``project``'s key (§3): the whole fingerprint must match the file saved."""
    crypto()
    name = typed_name(project)
    if name == RELEASE_PROJECT:
        raise SigningError("the keys of fsbpyr are pinned in fsbpyr itself")
    match = _FINGERPRINT.fullmatch(expected)
    if match is None:
        raise SigningError("not a fingerprint: give SHA256: and all 64 hex digits")
    wanted = f"SHA256:{match[1].lower()}"
    final = trust_store() / f"{name}.pub"
    copy: Path | None = None
    try:
        copy, content = _trust_copy(public_key, final.parent)
        got = _checked_fingerprint(content, public_key, wanted)
        present = os.path.lexists(final)
        saved = _read_limited(final, PUB_SIZE) if final.exists() else None
        if saved == content:
            return Trusted(name, got, unchanged=True)
        old = None if saved is None else fingerprint(saved)
        if present and not replace:
            raise SigningError(f"{name} has another trusted key, {old or 'that is damaged'}; "
                               "give --replace to replace it")  # fmt: skip
        copy.rename(final)
    except OSError as exc:
        raise SigningError(f"cannot write {final}: {exc.strerror or exc}") from exc
    finally:
        if copy is not None:
            with contextlib.suppress(OSError):
                copy.unlink()
    return Trusted(name, got, replaced=old)


@dataclass(frozen=True, slots=True)
class BuildSigning:
    """The signing variables a build took out of its environment, and --signing-key."""

    key: Path | None = None
    key_variable: bytes | None = None
    passphrase: bytes | None = None

    @property
    def wanted(self) -> bool:
        return self.key is not None or bool(self.key_variable)

    def environment(self) -> dict[str, str]:
        """The variables as the variant of a dispatched build gets them, and no other process."""
        variables = {KEY_VARIABLE: self.key_variable, PASSPHRASE_VARIABLE: self.passphrase}
        return {name: os.fsdecode(value) for name, value in variables.items()
                if value is not None}  # fmt: skip


def take_build_variables(key: Path | None) -> BuildSigning:
    """Read both signing variables once and remove them from this process's environment, before
    the build starts any process: uv, build backends and the new app run third-party code, whose
    logs or dumps of their environments then show neither. It keeps the passphrase from code that
    does not look for it, not from code that does (§3.1): /proc/PID/environ still shows what this
    process started with, so a build of untrusted code signs in a separate job."""
    return BuildSigning(key, os.environb.pop(KEY_VARIABLE.encode(), None),
                        os.environb.pop(PASSPHRASE_VARIABLE.encode(), None))  # fmt: skip


def sign_output(output: Path, request: BuildSigning) -> tuple[str, str | None]:
    """Sign a build's output, as `fsbpyr sign` does: ``(line, None)``, or ``("", why it is not
    signed)``. Signing is optional for apps: no failure here stops a build."""
    key = request.key or Path(os.fsdecode(request.key_variable or b""))
    try:
        signature, _, print_key = sign(output, key=key, given=request.passphrase)
    except SigningError as exc:
        return "", str(exc)
    return f"signed {output} with key {print_key}: {signature}", None


def warning_lines(output: Path, why: str) -> list[str]:
    """The warning of a build that was to be signed and is not, boxed: it must not be missed."""
    ended = why if why.endswith(("?", ".")) else f"{why}."
    text = (f"{output} is NOT SIGNED: {ended} Unset {KEY_VARIABLE} to build unsigned without "
            "this warning.")
    lines, line = [], ""
    for word in text.split(" "):
        if line and len(line) + 1 + len(word) > _WARNING_WIDTH:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    rule = "*" * (_WARNING_WIDTH + 2)
    return [rule, *(f" {part}" for part in (*lines, line)), rule]
