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
"""

import contextlib
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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .inspection import InspectError, read
from .targets import TARGETS, host_target
from .vcs import own_version, release_asset

REPO = "devitops-com/fsbpyr"
API_VARIABLE = "FSBPYR_RELEASE_API"  # the release document's URL; tests serve their own
DEFAULT_API = f"https://api.github.com/repos/{REPO}/releases/latest"
INSTALLER = f"https://github.com/{REPO}/releases/latest/download/install.sh"
_TARGETS = "|".join(re.escape(t.name) for t in TARGETS)
_ASSET_NAME = re.compile(rf"fsbpyr-3\.\d+-({_TARGETS})")
_TIMEOUT_S = 60
_CHUNK = 1 << 20
_RELEASE = re.compile(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_PLACED_STUBS = "fsbpyr-stub-*-glibc*"  # EL8 stubs a user placed beside the executable (D46)
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


def latest_release(asset: str) -> Release:
    """The latest release and the URL of its ``asset``.xz."""
    api = os.environ.get(API_VARIABLE) or DEFAULT_API
    try:
        request = urllib.request.Request(api, headers=_ACCEPT)  # noqa: S310  https, or a test's
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310
            document = json.load(response)
        tag = document["tag_name"]
        assets = {a["name"]: a["browser_download_url"] for a in document["assets"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UpdateError(f"cannot read the latest release from {api}: {exc}") from exc
    numbers = release_numbers(tag) if isinstance(tag, str) else None
    if numbers is None:
        raise UpdateError(f"the latest release has no vX.Y.Z tag ({tag!r})")
    name = f"{asset}.xz"
    if name not in assets:
        raise UpdateError(f"the latest release ({tag}) has no {name}; install its fsbpyr for "
                          "the default Python (or another with --python X.Y):\n"
                          f"{curl_hint(default=True)}")  # fmt: skip
    return Release(tag, numbers, assets[name])


def _failed(url: str, exc: Exception) -> UpdateError:
    missing = isinstance(exc, urllib.error.HTTPError) and exc.code == 404
    return (NotFound if missing else UpdateError)(f"downloading {url} failed: {exc}")


def unpack(url: str, target: Path) -> None:
    """``url`` (an xz file), unpacked as it arrives into ``target``."""
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
    try:
        request = urllib.request.Request(url, headers=_ACCEPT)  # noqa: S310  the release's URL
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response, \
                target.open("wb") as out:  # noqa: S310
            while chunk := response.read(_CHUNK):
                out.write(decoder.decompress(chunk))
    except (OSError, lzma.LZMAError, EOFError) as exc:
        raise _failed(url, exc) from exc
    if not decoder.eof:
        raise UpdateError(f"{url} ended before the end of its xz stream")


def download_text(url: str, limit: int = _CHUNK) -> str:
    """``url``'s body as text, of at most ``limit`` bytes (a checksums file)."""
    try:
        request = urllib.request.Request(url, headers=_ACCEPT)  # noqa: S310  the release's URL
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310
            body: bytes = response.read(limit + 1)
    except OSError as exc:
        raise _failed(url, exc) from exc
    if len(body) > limit:
        raise UpdateError(f"{url} is larger than {limit} bytes")
    return body.decode("utf-8", "replace")


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
    release = latest_release(own_asset())
    if release.numbers <= own:
        log(f"fsbpyr is up to date ({own_version()}; latest release {release.tag})")
        return False
    log(f"downloading fsbpyr {release.tag}")
    handle, name = tempfile.mkstemp(dir=directory, prefix=f".{executable.name}.update-")
    os.close(handle)
    new = Path(name)
    staged: list[tuple[Path, Path]] = []
    try:
        unpack(release.asset_url, new)
        new.chmod(stat.S_IMODE(executable.stat().st_mode))
        try:
            report = read(new, verify=True)
        except InspectError as exc:
            raise UpdateError(f"the download failed verification, nothing was changed: {exc}"
                              ) from exc
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
    if placed := sorted(path.name for path in directory.glob(_PLACED_STUBS)):
        warn(f"{', '.join(placed)} beside {executable.name} belong to the old release and "
             f"`--glibc` will refuse them; place the stubs of {release.tag} there, or remove "
             "these and let `--glibc` download them")  # fmt: skip
    log(f"updated {executable} to fsbpyr {release.tag}")
    return True


