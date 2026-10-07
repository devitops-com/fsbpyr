"""The glibc sysroot the stub links against: a portable baseline (plan Phase 6c, D25).

Linking against the build host's glibc ties every app to it (spike S1 found GLIBC_2.38).
The sysroot is extracted from pinned Debian 10 packages (glibc 2.28, the manylinux_2_28
baseline), so no container is needed and the same pins always give the same sysroot.
"""

import hashlib
import io
import posixpath
import shutil
import tarfile
import tempfile
from pathlib import Path

from . import ar
from .pbs import PbsError, download
from .pins import SysrootPin

_DONE = ".fsbpyr-sysroot"  # written last: a sysroot without it is incomplete


class SysrootError(RuntimeError):
    """The sysroot cannot be downloaded or extracted."""


def _into_sysroot(member: tarfile.TarInfo, destination: str) -> tarfile.TarInfo | None:
    """Debian's absolute symlinks (libpthread.so -> /lib/...) would resolve on the build host;
    rewrite them relative to the sysroot, then apply the stdlib's ``data`` safety filter."""
    if member.issym() and member.linkname.startswith("/"):
        directory = posixpath.dirname(posixpath.normpath("/" + member.name))
        member = member.replace(linkname=posixpath.relpath(member.linkname, directory), deep=False)
    return tarfile.data_filter(member, destination)


def extract_deb(data: bytes, target: Path, name: str) -> None:
    """Extract the data archive of the Debian package ``data`` into ``target``."""
    try:
        archive = next((body for member, body in ar.parse(data, name)
                        if member.startswith("data.tar")), None)  # fmt: skip
        if archive is None:
            raise SysrootError(f"{name}: no data archive")
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(target, filter=_into_sysroot)  # noqa: S202  ends in data_filter
    except (ar.ArError, tarfile.TarError, OSError, KeyError, ValueError) as exc:
        raise SysrootError(f"cannot extract {name}: {exc}") from exc


def _package(url: str, sha256: str, cache: Path) -> tuple[str, bytes]:
    """The package's bytes, read once: what is verified is what gets extracted."""
    deb = cache / "downloads" / url.rpartition("/")[2]
    try:
        if not deb.exists():
            download(url, sha256, deb)
        data = deb.read_bytes()
    except (OSError, PbsError) as exc:
        raise SysrootError(f"cannot download {url}: {exc}") from exc
    if hashlib.sha256(data).hexdigest() != sha256:
        raise SysrootError(f"{deb} does not match its pinned SHA-256; delete it")
    return deb.name, data


def fetch_sysroot(pin: SysrootPin, cache: Path) -> Path:
    """Download (once), verify and extract the pinned packages under ``cache``."""
    dest = cache / "sysroots" / pin.identity
    if (dest / _DONE).exists():
        return dest
    packages = [_package(url, sha256, cache) for url, sha256 in pin.packages]
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".extract-"))
    try:
        for name, data in packages:
            extract_deb(data, staging, name)
        (staging / _DONE).write_text(pin.identity + "\n")
        try:
            staging.rename(dest)
        except OSError as exc:
            if not (dest / _DONE).exists():  # not a concurrent winner
                raise SysrootError(f"{dest} is incomplete; delete it") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return dest
