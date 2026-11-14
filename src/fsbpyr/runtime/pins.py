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
        version="3.12.16",
        release="20261110",
        triple="x86_64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="a1b98760371cfd145a1533ea75c89437cdaf538e84c5e84c469eda9065a251af",
    ),
    PbsPin(
        python="3.13",
        version="3.13.18",
        release="20261110",
        triple="x86_64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="dec4f6d23031233beddfe91b99751977ef96e90642109836b1f9ad80b0019a68",
    ),
    PbsPin(
        python="3.14",
        version="3.14.10",
        release="20261110",
        triple="x86_64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="4730c0dc79e7eecbe54a323d587fdeaab621094fe09b5827167d69045115fcbc",
    ),
    PbsPin(
        python="3.15",
        version="3.15.2",
        release="20261110",
        triple="x86_64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="a61124f187ea3b379d6b78806ac06edd43db524eef411925eca05fb9357589d5",
    ),
    PbsPin(
        python="3.12",
        version="3.12.16",
        release="20261110",
        triple="aarch64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="d035e9c629feb9f9f4458b54da1aa1332b1c786e0d09e3394c05cea0b92adde8",
    ),
    PbsPin(
        python="3.13",
        version="3.13.18",
        release="20261110",
        triple="aarch64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="160b0ca941266cd8f0e0dda7fd355ce1089df50d15853ea8cd99f4599067db39",
    ),
    PbsPin(
        python="3.14",
        version="3.14.10",
        release="20261110",
        triple="aarch64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="7e6b0d554a93c15186993ee0254c54a590a1dcc8aaf0564f3ada3d76a1d331da",
    ),
    PbsPin(
        python="3.15",
        version="3.15.2",
        release="20261110",
        triple="aarch64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="56246cc19968bbfc32a92a2c7887fa2fd4ba1917976dccb2d0ee3e43468b1b3d",
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


# Rocky Linux 8.10, the final EL8 minor release (supported to 2029-05-31, owner 2026-10-25):
# its repository keeps every 8.10 build at a stable URL and publishes each one's SHA-256 in
# repodata/primary.xml (signed: repomd.xml.asc); the NVRs are Red Hat's. Only the gcc package
# has crtbeginS.o, libgcc.a and libgcc_eh.a.
_ROCKY = "https://dl.rockylinux.org/pub/rocky/8.10"
DEFAULT_BASELINE = "2.34"  # the baselines apps are built for, newest first: see sysroot_for
BASELINES = (DEFAULT_BASELINE, "2.28")


@dataclass(frozen=True, slots=True)
class SysrootPin:
    """Packages whose glibc the stub links against (plan Phase 6c, D25)."""

    name: str  # part of the runtime id
    glibc: str  # version of the headers and libraries
    manylinux: str  # the glibc the apps are built for: manylinux_2_X wheels, the stub's need
    packages: tuple[tuple[str, str], ...]  # (url, sha256)
    packaging: str = "deb"  # "deb" or "rpm"
    layout: str = "multiarch"  # Debian's usr/lib/<multiarch>, or "redhat": usr/lib64 and links

    @property
    def identity(self) -> str:
        """The name plus a digest of the pinned packages: a changed pin never reuses an old
        sysroot or runtime."""
        digest = hashlib.sha256("\n".join(sha for _, sha in self.packages).encode()).hexdigest()
        return f"{self.name}-{digest[:12]}"

    @property
    def baseline(self) -> tuple[int, int]:
        """The glibc the apps are built for, below the sysroot's own where Debian's: linked
        against glibc 2.36, the stub needs no symbol newer than 2.34 (pthread_create, dlopen and
        __libc_start_main moved there), which RHEL 9 and Ubuntu 22.04 provide. EL8's own glibc
        2.28 is the baseline of its sysroot."""
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


EL8_SYSROOTS = {
    "x86_64-unknown-linux-gnu": SysrootPin(
        name="rocky8-glibc2.28-x86_64",
        glibc="2.28",
        manylinux="2.28",
        packaging="rpm",
        layout="redhat",
        packages=tuple((f"{_ROCKY}/{path}", sha256) for path, sha256 in (
            ("BaseOS/x86_64/os/Packages/g/glibc-2.28-251.el8_10.48.x86_64.rpm",
             "ac6b703086afead9a4cfeb5cf50d0974056e8c810f059d17dba0e050cf2ed477"),
            ("BaseOS/x86_64/os/Packages/g/glibc-devel-2.28-251.el8_10.48.x86_64.rpm",
             "ba67a4c21c184e5a5fd31bfe6cdd879196b98b8745d24e7d951d4f23281f3f31"),
            ("BaseOS/x86_64/os/Packages/g/glibc-headers-2.28-251.el8_10.48.x86_64.rpm",
             "8a2496f1385a372db6a66c48b7f71678f97b3815ce0553711e81118d5b9a990c"),
            ("BaseOS/x86_64/os/Packages/k/kernel-headers-4.18.0-553.177.1.el8_10.x86_64.rpm",
             "9ea6371b6f8526fcfb098ed2c1421f94b6ef164e66b8183cb6f778f279cdd5a8"),
            ("BaseOS/x86_64/os/Packages/l/libgcc-8.5.0-29.el8_10.x86_64.rpm",
             "f5f66f766f8d9e9ca3e31462a08cf5ff8c8824b5efa1ba06ef404c3e9763b3e2"),
            ("AppStream/x86_64/os/Packages/g/gcc-8.5.0-29.el8_10.x86_64.rpm",
             "04601bcd450d79df4f8aa250f240ec98b89b1eb36388f8b4ee6737b0b4fec3f6"),
        )),
    ),
    "aarch64-unknown-linux-gnu": SysrootPin(
        name="rocky8-glibc2.28-aarch64",
        glibc="2.28",
        manylinux="2.28",
        packaging="rpm",
        layout="redhat",
        packages=tuple((f"{_ROCKY}/{path}", sha256) for path, sha256 in (
            ("BaseOS/aarch64/os/Packages/g/glibc-2.28-251.el8_10.48.aarch64.rpm",
             "ff7bff4fab47c038ae308c7f38080c9e56285451ff02c7d48d860abcf9994308"),
            ("BaseOS/aarch64/os/Packages/g/glibc-devel-2.28-251.el8_10.48.aarch64.rpm",
             "23f2fc103fb3323700869414ca63ee8477eb85f2bff749f469f9dbb57a6c402d"),
            ("BaseOS/aarch64/os/Packages/g/glibc-headers-2.28-251.el8_10.48.aarch64.rpm",
             "2c771d2314c88c38181001b38fdbd299e6cd45437d17516c933c5086c7729901"),
            ("BaseOS/aarch64/os/Packages/k/kernel-headers-4.18.0-553.177.1.el8_10.aarch64.rpm",
             "70d869a10c01a17b4fbe3888e6ccfe7ec5b5d3c6f30cb389085933fd60a5e742"),
            ("BaseOS/aarch64/os/Packages/l/libgcc-8.5.0-29.el8_10.aarch64.rpm",
             "c5c21f4698674c79de72ac90bcfdc0b12021df658f49287173af202f9a1f7632"),
            ("AppStream/aarch64/os/Packages/g/gcc-8.5.0-29.el8_10.aarch64.rpm",
             "03d0f8e3cef460958b79059d47a8ad292ee62eebe03b45da30274c2a0df323a0"),
        )),
    ),
}  # fmt: skip


def sysroot_for(triple: str, baseline: str = DEFAULT_BASELINE) -> SysrootPin:
    """The sysroot for ``triple`` and the glibc baseline of the apps: Debian 12's (2.34, the
    default) or EL8's (2.28)."""
    tables = {DEFAULT_BASELINE: SYSROOTS, "2.28": EL8_SYSROOTS}
    if baseline not in tables:
        raise ValueError(f"no glibc {baseline} baseline; supported: {', '.join(BASELINES)}")
    if triple not in tables[baseline]:
        raise ValueError(f"no glibc sysroot pinned for {triple}")
    return tables[baseline][triple]
