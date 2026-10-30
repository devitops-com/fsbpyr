"""``fsbpyr self update``: replace the running executable with the latest GitHub release.

Settled with the owner on 2026-10-18 (plan, Phase 9) and 2026-10-22 (every asset names its
Python): the release's ``fsbpyr-3.X-<target>.xz`` of the Python the executable runs on (the
asset its build_info says it was released as), so an update never moves to another Python, is
unpacked in-process with the standard library's lzma into the executable's own directory, verified
in-process (``inspect --verify``, so no byte of it runs before it passes) and renamed over the
executable's real path, keeping its mode. A man page and completions found under the
executable's prefix are written again by the new executable and renamed into place after it.
Only released executables update themselves: a developer build (``untagged-<commit>``) is
refused with the installer command that installs the latest release for its Python.

Every download of fsbpyr's own files goes through ``signed_sums`` and ``fetch_checked``
(docs/signing.md §5.5): a release's sums file is checked against the keys this fsbpyr pins, and
an ``.xz`` against its line of that file, before lzma sees a byte of it. A release that is not
signed, or not by a pinned key, is refused, with no fallback.
"""

import contextlib
import http.client
import json
import lzma
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from . import signing
from .inspection import InspectError, read
from .targets import TARGETS, host_target
from .text import shown
from .vcs import own_version, release_asset, version_string

REPO = "devitops-com/fsbpyr"
SUMS = "SHA256SUMS"
API_VARIABLE = "FSBPYR_RELEASE_API"  # the release document's URL; tests serve their own
DEFAULT_API = f"https://api.github.com/repos/{REPO}/releases/latest"
INSTALLER = f"https://github.com/{REPO}/releases/latest/download/install.sh"
_TARGETS = "|".join(re.escape(t.name) for t in TARGETS)
_ASSET_NAME = re.compile(rf"fsbpyr-3\.\d+-({_TARGETS})")
_TIMEOUT_S = 60
_CHUNK = 1 << 20
_MAX_PACKED = 256 << 20  # an asset's .xz has a few MB; a larger one is no asset of a release
_MAX_SUMS = 1 << 20
_MAX_DOCUMENT = 4 << 20  # a release document has some 100 KB
_RELEASE = re.compile(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_ACCEPT = {"Accept": "application/vnd.github+json", "User-Agent": "fsbpyr-self-update"}

# What `make install` puts under the prefix (Makefile), and the command that writes each.
_EXTRAS = (
    ("share/man/man1/fsbpyr.1", ("man",)),
    ("share/bash-completion/completions/fsbpyr", ("completion", "bash")),
    ("share/zsh/site-functions/_fsbpyr", ("completion", "zsh")),
    ("share/fish/vendor_completions.d/fsbpyr.fish", ("completion", "fish")),
)


class UpdateError(RuntimeError):
    """The update cannot be done; the message says why and what to do instead."""


class NotFound(UpdateError):
    """The server has no such file (404): the release does not publish it."""


@dataclass(frozen=True, slots=True)
class Release:
    tag: str
    numbers: tuple[int, int, int]
    asset_url: str
    sums_url: str | None
    signature_url: str | None


def _running_python() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def curl_hint(*, default: bool = False, directory: Path | None = None) -> str:
    """The command that installs the latest release on this host: for the Python this runs on,
    or with ``default`` the installer's default Python; into ``directory`` when given."""
    install_dir = f"FSBPYR_INSTALL_DIR={shlex.quote(str(directory))} " if directory else ""
    python = "" if default else f" -s -- --python {_running_python()}"
    return f"curl -LsSf {INSTALLER} | {install_dir}sh{python}"


def running_executable() -> Path:
    """The real path of the running fsbpyr executable (it may be renamed or reached through a
    symlink); an error when fsbpyr runs from a checkout or a wheel."""
    try:
        import _fsbpyr  # noqa: F401, PLC0415  # only in executables
    except ImportError:
        raise UpdateError(
            "fsbpyr self update works on the fsbpyr executable only, not on a checkout or a "
            f"wheel; to get the latest release:\n{curl_hint()}"
        ) from None
    return Path(os.path.realpath("/proc/self/exe"))


def release_numbers(text: str) -> tuple[int, int, int] | None:
    match = _RELEASE.fullmatch(text)
    return None if match is None else (int(match[1]), int(match[2]), int(match[3]))


def own_release() -> tuple[int, int, int]:
    """The release number of this executable; a developer build is refused."""
    numbers = release_numbers(own_version().partition("-")[0])
    if numbers is None:
        raise UpdateError(
            f"this is a developer build ({own_version()}); only released executables update "
            f"themselves. To get the latest release:\n{curl_hint()}"
        )
    return numbers


def own_asset() -> str:
    """The asset (without ``.xz``) this executable was released as, from its build_info; when
    it records none of today's names, the asset of the Python it runs on for this host."""
    import _fsbpyr  # noqa: PLC0415  # only in executables

    asset = json.loads(_fsbpyr.info()["build_info"]).get("release_asset")
    if isinstance(asset, str) and _ASSET_NAME.fullmatch(asset):
        return asset
    return release_asset(_running_python(), host_target().name)


def _open(url: str) -> http.client.HTTPResponse:
    """``url``, which a release document or a mirror may have chosen: http or https alone. urllib
    takes file: and ftp: as well, and its timeout does not hold there: /dev/stdin blocks."""
    if not url.startswith(("https://", "http://")):
        raise ValueError(f"{shown(url)} is no http or https URL")
    request = urllib.request.Request(url, headers=_ACCEPT)  # noqa: S310  checked above
    response: http.client.HTTPResponse = urllib.request.urlopen(request, timeout=_TIMEOUT_S)  # noqa: S310
    return response


def latest_release(asset: str) -> Release:
    """The latest release and the URL of its ``asset``.xz. Nothing in this document is trusted:
    it says where to look, and what is found there is checked."""
    api = os.environ.get(API_VARIABLE) or DEFAULT_API
    try:
        with _open(api) as response:
            body = response.read(_MAX_DOCUMENT + 1)
        if len(body) > _MAX_DOCUMENT:
            raise ValueError(f"it is larger than {_MAX_DOCUMENT} bytes")
        document = json.loads(body)
        tag = document["tag_name"]
        assets = {a["name"]: a["browser_download_url"] for a in document["assets"]
                  if isinstance(a["browser_download_url"], str)}  # fmt: skip
    except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError,
            RecursionError) as exc:  # fmt: skip
        raise UpdateError(f"cannot read the latest release from {shown(api)}: {shown(exc)}"
                          ) from exc
    numbers = release_numbers(tag) if isinstance(tag, str) else None
    if numbers is None:
        raise UpdateError(f"the latest release has no vX.Y.Z tag ({tag!r})")
    name = f"{asset}.xz"
    if name not in assets:
        raise UpdateError(f"the latest release ({tag}) has no {name}; install its fsbpyr for "
                          "the default Python (or another with --python X.Y):\n"
                          f"{curl_hint(default=True)}")  # fmt: skip
    return Release(tag, numbers, assets[name], assets.get(SUMS), assets.get(f"{SUMS}.sig"))


def _failed(url: str, exc: Exception) -> UpdateError:
    missing = isinstance(exc, urllib.error.HTTPError) and exc.code == 404
    return (NotFound if missing else UpdateError)(f"downloading {shown(url)} failed: {shown(exc)}")


def download_bytes(url: str, limit: int) -> bytes:
    """``url``'s body, of at most ``limit`` bytes (a checksums file or a signature). The response
    is closed when this returns: a failed check must not sit between urlopen() and read(), where
    OpenSSL's error queue would reach _ssl (docs/signing.md §4)."""
    try:
        with _open(url) as response:
            body: bytes = response.read(limit + 1)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise _failed(url, exc) from exc
    if len(body) > limit:
        raise UpdateError(f"{shown(url)} is larger than {limit} bytes")
    return body


def signed_sums(url: str, signature_url: str | None, tag: str) -> bytes:
    """The checksums file at ``url``, once a pinned key's signature of it passed. NotFound when
    the release has no such file. No line of it is used before this returns."""
    sums = download_bytes(url, _MAX_SUMS)
    try:
        if signature_url is None:
            raise NotFound(f"{url} has no signature")
        signature = download_bytes(signature_url, signing.SIGNATURE_SIZE + 1)
    except NotFound:
        raise UpdateError(f"release {tag} is not signed; nothing was changed") from None
    try:
        passed = signing.check_release_file(sums, signature)
    except signing.SigningError as exc:  # a pin this fsbpyr carries is damaged: nothing passes
        raise UpdateError(str(exc)) from exc
    if not passed:
        raise UpdateError(signing.not_pinned_message(tag))
    return sums


def listed_digest(sums: bytes, name: str, tag: str) -> str:
    """The SHA-256 a checked sums file lists for the file ``name``."""
    digest = signing.asset_digest(sums, name)
    if digest is None:
        raise NotFound(f"the checksums of release {tag} list no {name}")
    return digest


def _download(url: str, out: BinaryIO) -> None:
    """``url`` into the file ``out``, of at most _MAX_PACKED bytes."""
    try:
        with _open(url) as response:
            while chunk := response.read(_CHUNK):
                out.write(chunk)
                if out.tell() > _MAX_PACKED:
                    raise UpdateError(f"{shown(url)} is larger than {_MAX_PACKED} bytes")
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise _failed(url, exc) from exc


def _unpack(packed: BinaryIO, url: str, target: Path, limit: int | None) -> None:
    """The xz file ``packed`` into ``target``; UpdateError when it would exceed ``limit`` bytes."""
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
    try:
        with target.open("wb") as out:
            while chunk := packed.read(_CHUNK):
                out.write(decoder.decompress(chunk))
                if limit is not None and out.tell() > limit:
                    raise UpdateError(f"{shown(url)} unpacks to more than {limit} bytes")
    except (OSError, lzma.LZMAError, EOFError) as exc:
        raise UpdateError(f"unpacking {shown(url)} failed: {exc}") from exc
    if not decoder.eof:
        raise UpdateError(f"{shown(url)} ended before the end of its xz stream")


def fetch_checked(url: str, digest: str, target: Path, limit: int | None = None) -> None:
    """``url`` (an xz file) unpacked into ``target``, only after its SHA-256 equals ``digest``,
    the line of a checked sums file. One open file is hashed and read, so lzma sees what passed."""
    with tempfile.TemporaryFile(dir=target.parent) as packed:
        _download(url, packed)
        packed.flush()
        got = signing.crypto()._sha256(packed.fileno()).hex()
        if got != digest:
            raise UpdateError(f"{shown(url)} does not match the checksum of its release; nothing "
                              "was changed")  # fmt: skip
        packed.seek(0)
        _unpack(packed, url, target, limit)


def check_release_identity(info: Mapping[str, object], asset: str) -> tuple[int, int, int]:
    """The release number of a new executable, once it is the asset expected: a signed sums file
    lists every file of its release, under any name."""
    found = info.get("release_asset")
    if found != asset:
        raise UpdateError(f"the download is {shown(found)}, not {asset}; nothing was "
                          "changed")  # fmt: skip
    git = info.get("git")
    version = version_string(git if isinstance(git, dict) else None)
    numbers = release_numbers(version.partition("-")[0])
    if numbers is None:
        raise UpdateError("the download records no release; nothing was changed")
    return numbers


def _glibc_version(text: object) -> tuple[int, ...] | None:
    match = re.search(r"(\d+)\.(\d+)", text) if isinstance(text, str) else None
    return None if match is None else (int(match[1]), int(match[2]))


def check_glibc(baseline: object, host: str | None = None) -> None:
    """Refuse a new executable whose glibc ``baseline`` (the ``glibc`` its build_info records)
    is newer than the host's; musl and other hosts, and builds that record none, pass."""
    if host is None:
        try:
            host = os.confstr("CS_GNU_LIBC_VERSION")
        except (ValueError, OSError):  # not glibc
            return
    needed, have = _glibc_version(baseline), _glibc_version(host)
    if needed is None or have is None or needed <= have:
        return
    raise UpdateError(
        f"the new fsbpyr needs glibc {baseline}, this host has {host}; nothing was changed. "
        "Keep the current version, or install a build for this host's glibc"
    )


def _render(new: Path, command: tuple[str, ...]) -> bytes:
    """What the new, verified executable prints for ``command``."""
    try:
        done = subprocess.run([str(new), *command], capture_output=True,  # noqa: S603  verified
                              timeout=_TIMEOUT_S, check=True)  # fmt: skip
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError(f"the new fsbpyr failed to run `fsbpyr {' '.join(command)}`: {exc}"
                          ) from exc
    return done.stdout


def _stage_extras(
    executable: Path, new: Path, warn: Callable[[str], None]
) -> list[tuple[Path, Path]]:
    """(temporary, final) for each man page or completion found under the executable's prefix,
    written by the new executable beside the final file."""
    prefix = executable.parent.parent
    if executable.parent.name != "bin":
        warn(f"not under a prefix with a bin directory: the man page and the completions are "
             f"left alone; write them with `{executable.name} man` and `{executable.name} "
             "completion SHELL`")  # fmt: skip
        return []
    staged: list[tuple[Path, Path]] = []
    missing = False
    for relative, command in _EXTRAS:
        final = prefix / relative
        if not final.is_file():
            missing = True
            continue
        content = _render(new, command)
        try:
            handle, name = tempfile.mkstemp(dir=final.parent, prefix=f".{final.name}.")
            temporary = Path(name)
            with os.fdopen(handle, "wb") as out:
                out.write(content)
            temporary.chmod(stat.S_IMODE(final.stat().st_mode))
        except OSError as exc:
            warn(f"{final} was not updated: {exc.strerror or exc}; write it with "
                 f"`fsbpyr {' '.join(command)}`")  # fmt: skip
            continue
        staged.append((temporary, final))
    if missing:
        warn(f"the man page or completions not found under {prefix} were left alone; write "
             "them with `fsbpyr man` and `fsbpyr completion SHELL`")  # fmt: skip
    return staged


def _warn_about_placed_stubs(executable: Path, tag: str, warn: Callable[[str], None]) -> None:
    """Name the EL8 stubs placed beside the executable that ``--glibc`` of release ``tag``
    refuses (D46); all of them, as maybe, when that cannot be told."""
    from .runtime import stubs  # noqa: PLC0415  stubs.py needs this module

    refused, known = stubs.refused_after_update(tag, stubs.own_paths(real=str(executable)))
    if not refused:
        return
    names = ", ".join(str(path) for path in refused)
    if known:
        warn(f"{names} are not the published stubs of {tag}: `--glibc` refuses them unless "
             f"they carry its runtime id; place the stubs of {tag} next to them, or remove "
             "these and let `--glibc` download them")  # fmt: skip
    else:
        warn(f"{names} could not be checked against the stubs of {tag}: if the runtime id of "
             f"{tag} differs, `--glibc` refuses them and names the stub to place")  # fmt: skip


def update(
    log: Callable[[str], None] = print, warn: Callable[[str], None] = print
) -> bool:
    """Update the running executable to the latest release; False when it is up to date."""
    own = own_release()
    executable = running_executable()
    directory = executable.parent
    if not os.access(directory, os.W_OK | os.X_OK):
        raise UpdateError(f"cannot write to {directory}; update with the owner's rights, or "
                          f"install the latest release there:\n"
                          f"{curl_hint(directory=directory)}")  # fmt: skip
    asset = own_asset()
    release = latest_release(asset)
    if release.numbers <= own:
        log(f"fsbpyr is up to date ({own_version()}; latest release {release.tag})")
        return False
    log(f"downloading fsbpyr {release.tag}")
    if release.sums_url is None:
        raise UpdateError(f"release {release.tag} is not signed; nothing was changed")
    digest = listed_digest(signed_sums(release.sums_url, release.signature_url, release.tag),
                           f"{asset}.xz", release.tag)  # fmt: skip
    handle, name = tempfile.mkstemp(dir=directory, prefix=f".{executable.name}.update-")
    os.close(handle)
    new = Path(name)
    staged: list[tuple[Path, Path]] = []
    try:
        fetch_checked(release.asset_url, digest, new)
        new.chmod(stat.S_IMODE(executable.stat().st_mode))
        try:
            report = read(new, verify=True)
        except InspectError as exc:
            raise UpdateError(f"the download failed verification, nothing was changed: {exc}"
                              ) from exc
        numbers = check_release_identity(report.info, asset)
        if numbers <= own:
            raise UpdateError(f"the download is not newer than this fsbpyr ({own_version()}); "
                              "nothing was changed")  # fmt: skip
        if numbers != release.numbers:
            raise UpdateError(f"the download is not fsbpyr {release.tag}; nothing was changed")
        check_glibc(report.info.get("glibc"))
        staged = _stage_extras(executable, new, warn)
        new.rename(executable)
    except BaseException:
        with contextlib.suppress(OSError):
            new.unlink()
        for temporary, _ in staged:
            with contextlib.suppress(OSError):
                temporary.unlink()
        raise
    for temporary, final in staged:
        try:
            temporary.rename(final)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            warn(f"{final} was not updated: {exc.strerror or exc}")
    _warn_about_placed_stubs(executable, release.tag, warn)
    log(f"updated {executable} to fsbpyr {release.tag}")
    return True


