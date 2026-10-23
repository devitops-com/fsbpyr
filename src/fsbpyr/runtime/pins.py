"""Pinned python-build-standalone distributions (URL and SHA-256 per Python and target)."""

import hashlib
from dataclasses import dataclass
from urllib.parse import quote

_RELEASES = "https://github.com/astral-sh/python-build-standalone/releases/download"


@dataclass(frozen=True, slots=True)
class PbsPin:
    python: str  # major.minor
    version: str
    release: str
    triple: str
    flavor: str
    sha256: str

    @property
    def name(self) -> str:
        return f"cpython-{self.version}+{self.release}-{self.triple}-{self.flavor}"

    @property
    def url(self) -> str:
        return f"{_RELEASES}/{self.release}/{quote(self.name)}.tar.zst"


# The triples and flavours below are those of targets.py (a test compares them); they stay
# literal here because tools/upstream-watch.sh reads them from this file.
PINS = (
    PbsPin(
        python="3.12",
        version="3.12.15",
        release="20261021",
        triple="x86_64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="5c992267780cef4c168bc07dba54f125ec1a2b9383a47946fc805f81a4712119",
    ),
    PbsPin(
        python="3.13",
        version="3.13.17",
        release="20261021",
        triple="x86_64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="344eec7e761e7b3dccce0ce2d91a3e70d40f8ff5d3ce7d907d979c23e8451fc7",
    ),
    PbsPin(
        python="3.14",
        version="3.14.9",
        release="20261021",
        triple="x86_64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="92f6abbce0283ed317873615396b5c43ccdcdf13ae9c7d68c9a07651df527528",
    ),
    PbsPin(
        python="3.12",
        version="3.12.15",
        release="20261021",
        triple="aarch64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="d40b4b72653d830884ac3ae432cf6b761a63b4c9b65d0d8f9241ddbd03c9c020",
    ),
    PbsPin(
        python="3.13",
        version="3.13.17",
        release="20261021",
        triple="aarch64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="6464bea3d1b41ebcb238ebdbb32332e9c01871d4ad14e7e66ca6aca18d5135fb",
    ),
    PbsPin(
        python="3.14",
        version="3.14.9",
        release="20261021",
        triple="aarch64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="f5726037fbea57a8d83ddb6bbe26242eeb96928fb396da2f6d9dbf3641aaff61",
    ),
)


def pin_for(python: str, triple: str) -> PbsPin:
    if all(pin.triple != triple for pin in PINS):  # a target of targets.py, not supported yet
        raise LookupError(f"fsbpyr does not support the {triple} target yet")
    for pin in PINS:
        if (pin.python, pin.triple) == (python, triple):
            return pin
    supported = ", ".join(f"{p.python} ({p.triple})" for p in PINS)
    raise LookupError(f"no pinned CPython {python} for {triple}; supported: {supported}")


# Debian 12 (bookworm), the oldest Debian release still in LTS (owner, 2026-10-22), from
# Debian's own repository: bookworm's main suite has not changed since its last point release
# (12.15, 2026-07-11); LTS updates go to bookworm-security, so these files stay where they are.
_DEBIAN = "https://deb.debian.org/debian/pool/main"


@dataclass(frozen=True, slots=True)
class SysrootPin:
    """Debian packages whose glibc the stub links against (plan Phase 6c, D25)."""

    name: str  # part of the runtime id
    glibc: str  # version of the headers and libraries
    manylinux: str  # the glibc the apps are built for: manylinux_2_X wheels, the stub's need
    packages: tuple[tuple[str, str], ...]  # (url, sha256)

    @property
    def identity(self) -> str:
        """The name plus a digest of the pinned packages: a changed pin never reuses an old
        sysroot or runtime."""
        digest = hashlib.sha256("\n".join(sha for _, sha in self.packages).encode()).hexdigest()
        return f"{self.name}-{digest[:12]}"

    @property
    def baseline(self) -> tuple[int, int]:
        """The glibc the apps are built for, below the sysroot's own: linked against glibc
        2.36, the stub needs no symbol newer than 2.34 (pthread_create, dlopen and
        __libc_start_main moved there), which RHEL 9 and Ubuntu 22.04 provide."""
        major, minor = self.manylinux.split(".")
        return int(major), int(minor)


SYSROOTS = {
    "x86_64-unknown-linux-gnu": SysrootPin(
        name="debian12-glibc2.36-x86_64",
        glibc="2.36",
        manylinux="2.34",
        packages=tuple((f"{_DEBIAN}/{path}", sha256) for path, sha256 in (
            ("g/glibc/libc6_2.36-9+deb12u14_amd64.deb",
             "ba4f88f73dbc3ae9055f3c20f4523bfdbaf1ad13ff95e258924f77d20b4fbedf"),
            ("g/glibc/libc6-dev_2.36-9+deb12u14_amd64.deb",
             "0218fc2befcd784c1b0c6292c0a137ce89fad054efaa579ad083bee0f2c01aae"),
            ("g/gcc-12/libgcc-s1_12.2.0-14+deb12u1_amd64.deb",
             "3016e62cb4b7cd8038822870601f5ed131befe942774d0f745622cc77d8a88f7"),
            ("g/gcc-12/libgcc-12-dev_12.2.0-14+deb12u1_amd64.deb",
             "d720259380a84f2ffc6fe516eff5cbe9c8a005138e8d6a5748747ba4b3404a82"),
            ("l/linux/linux-libc-dev_6.1.176-1_amd64.deb",
             "8bb258735b9dffbb111da778ebdd024750878e435ffd9dfcadcb6762ede6b4cf"),
        )),
    ),
    "aarch64-unknown-linux-gnu": SysrootPin(
        name="debian12-glibc2.36-aarch64",
        glibc="2.36",
        manylinux="2.34",
        packages=tuple((f"{_DEBIAN}/{path}", sha256) for path, sha256 in (
            ("g/glibc/libc6_2.36-9+deb12u14_arm64.deb",
             "01f4330719fd4f65580e16ea5a0527f372fca750e8f588d26deaf09f2d3b1cf4"),
            ("g/glibc/libc6-dev_2.36-9+deb12u14_arm64.deb",
             "8bc395bf2ee9939f1a6ae395f974cffdcf541d98f99adfe89ce7e4bef36d159e"),
            ("g/gcc-12/libgcc-s1_12.2.0-14+deb12u1_arm64.deb",
             "576926b283613db80168ddf76380a3bd877602778cf0d226caa7bfbfa71eacf3"),
            ("g/gcc-12/libgcc-12-dev_12.2.0-14+deb12u1_arm64.deb",
             "c83d28b7c3397c25f15ae17783fce78716a3d7cd415940940cc2af8dc4db8d6d"),
            ("l/linux/linux-libc-dev_6.1.176-1_arm64.deb",
             "eeca5ec13bed7206f8171fab9a48e84119dcc20cc79557f104628447a524ad10"),
        )),
    ),
}  # fmt: skip


def sysroot_for(triple: str) -> SysrootPin:
    if triple not in SYSROOTS:
        raise ValueError(f"no glibc sysroot pinned for {triple}")
    return SYSROOTS[triple]
