"""The glibc sysroot the stub links against: a portable baseline (plan Phase 6c, D25).

Linking against the build host's glibc ties every app to it (spike S1 found GLIBC_2.38).
The sysroot is extracted from pinned packages of Debian 12, the oldest Debian release in LTS
(glibc 2.36; what links against it needs no glibc newer than 2.34, the manylinux_2_34
baseline), so no container is needed and the same pins always give the same sysroot. The EL8
option (``--glibc 2.28``) takes Rocky Linux 8.10's RPMs instead: glibc 2.28 is the baseline.
"""

import hashlib
import io
import posixpath
import shutil
import tarfile
import tempfile
from collections.abc import Mapping
from pathlib import Path

from .. import targets
from . import ar, rpm
from .hold import hold
from .pbs import PbsError, download
from .pins import SysrootPin

_DONE = ".fsbpyr-sysroot"  # written last: a sysroot without it is incomplete
# What linking never reads, left out of an RPM's files (gcc's compiler proper alone is 70 MB):
# programs, data, configuration, and the character-set converters of the run-time glibc.
_RPM_SKIP = ("etc/", "var/", "usr/bin/", "usr/sbin/", "usr/libexec/", "usr/share/",
             "usr/lib/.build-id/", "usr/lib64/gconv/")  # fmt: skip


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


def _lean(member: tarfile.TarInfo, destination: str) -> tarfile.TarInfo | None:
    """``_into_sysroot`` for an RPM's files, less what ``_RPM_SKIP`` names."""
    if member.name.startswith(_RPM_SKIP):
        return None
    return _into_sysroot(member, destination)


def extract_rpm(data: bytes, target: Path, name: str) -> None:
    """Extract the files of the RPM package ``data`` into ``target``, the way a deb's are."""
    try:
        with tarfile.open(fileobj=io.BytesIO(rpm.payload_tar(data))) as tar:
            tar.extractall(target, filter=_lean)  # noqa: S202  ends in data_filter
    except (rpm.RpmError, tarfile.TarError, OSError, KeyError, ValueError) as exc:
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
    if (dest / _DONE).exists() and hold(dest):  # held: never collected (D38)
        return dest
    packages = [_package(url, sha256, cache) for url, sha256 in pin.packages]
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".extract-"))
    try:
        extract = extract_rpm if pin.packaging == "rpm" else extract_deb
        for name, data in packages:
            extract(data, staging, name)
        if pin.layout == "redhat":  # EL8's filesystem package; the linker scripts name
            for name in ("lib", "lib64"):  # /lib64/libc.so.6 and, on aarch64, /lib/ld-linux...
                (staging / name).symlink_to(f"usr/{name}")
        (staging / _DONE).write_text(pin.identity + "\n")
        try:
            staging.rename(dest)
        except OSError as exc:
            if not (dest / _DONE).exists():  # not a concurrent winner
                raise SysrootError(f"{dest} is incomplete; delete it") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    hold(dest)
    return dest


def build_flags(root: Path, env: Mapping[str, str], layout: str = "multiarch") -> dict[str, str]:
    """CFLAGS and LDFLAGS that point the build host's C compiler (gcc or clang) at the sysroot
    ``root`` of the pin's ``layout``, for extensions built from source; ``env``'s own flags
    follow. Not CC itself: build probes run it as a bare command (posix_ipc's prober). Not C++:
    the sysroot has no libstdc++, so a C++ extension fails, and the caller keeps the host's
    build."""
    target = targets.host_target()
    cflags, ldflags = [f"--sysroot={root}"], [f"--sysroot={root}"]
    if layout == "redhat":  # Red Hat's: the headers are where --sysroot looks, the gcc directory
        lib = root / "usr" / "lib64"  # is <triple>-redhat-linux/<version>
        gcc = sorted((root / "usr" / "lib" / "gcc").glob("*/*"))[-1:]
        if target.triple.startswith("aarch64-"):  # a precaution (unverified on arm64): keeps a
            cflags.append("-mno-outline-atomics")  # gcc 10+ off libgcc 8's atomics helpers
        libs = [lib]
    else:
        if target.multiarch is None:
            raise SysrootError("this host has no sysroot")
        multiarch = target.multiarch  # Debian's layout of the pinned packages
        lib = root / "usr" / "lib" / multiarch
        gcc = sorted((root / "usr" / "lib" / "gcc" / multiarch).glob("*"))[-1:]
        cflags += ["-isystem", f"{root / 'usr' / 'include' / multiarch}"]
        libs = [lib, root / "lib" / multiarch]
    ldflags += [f"-B{lib}", *(f"-B{d}" for d in gcc), *(f"-L{d}" for d in libs)]
    return {
        "CFLAGS": " ".join([*cflags, env.get("CFLAGS", "")]).strip(),
        "LDFLAGS": " ".join([*ldflags, env.get("LDFLAGS", "")]).strip(),
    }
