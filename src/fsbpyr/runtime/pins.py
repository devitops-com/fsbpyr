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


_DEBIAN = "https://archive.debian.org/debian/pool/main"


@dataclass(frozen=True, slots=True)
class SysrootPin:
    """Debian packages whose glibc the stub links against (plan Phase 6c, D25)."""

    name: str  # part of the runtime id
    glibc: str  # version of the headers and libraries
    packages: tuple[tuple[str, str], ...]  # (url, sha256)

    @property
    def identity(self) -> str:
        """The name plus a digest of the pinned packages: a changed pin never reuses an old
        sysroot or runtime."""
        digest = hashlib.sha256("\n".join(sha for _, sha in self.packages).encode()).hexdigest()
        return f"{self.name}-{digest[:12]}"

    @property
    def baseline(self) -> tuple[int, int]:
        major, minor = self.glibc.split(".")
        return int(major), int(minor)


SYSROOTS = {
    "x86_64-unknown-linux-gnu": SysrootPin(
        name="debian10-glibc2.28-x86_64",
        glibc="2.28",
        packages=tuple((f"{_DEBIAN}/{path}", sha256) for path, sha256 in (
            ("g/glibc/libc6_2.28-10+deb10u1_amd64.deb",
             "0ee0f0bfcd7b59ffda1dbc76ef58fa79512913204f2a336206467bfbf45bc69f"),
            ("g/glibc/libc6-dev_2.28-10+deb10u1_amd64.deb",
             "76b8413920d91a9a4289f99839933d0d30095e0ec12b05bb4f1a7f27e5ef4a55"),
            ("g/gcc-8/libgcc1_8.3.0-6_amd64.deb",
             "b1bb7611f3372732889d502cb1d09fe572b5fbb5288a4a8b1ed0363fecc3555a"),
            ("g/gcc-8/libgcc-8-dev_8.3.0-6_amd64.deb",
             "a52d5216858c7185bc245e52ad95a6ad515d087bc25e8556df06c5626b41a837"),
            ("l/linux/linux-libc-dev_4.19.249-2_amd64.deb",
             "c512f30f5abe413464b79ed23b54958df8cc104446c3ae93d83d28e23728a571"),
        )),
    ),
    "aarch64-unknown-linux-gnu": SysrootPin(
        name="debian10-glibc2.28-aarch64",
        glibc="2.28",
        packages=tuple((f"{_DEBIAN}/{path}", sha256) for path, sha256 in (
            ("g/glibc/libc6_2.28-10+deb10u1_arm64.deb",
             "09f5e92b1527cdc06b757499bb4d97ddf1b064fc3d163f978e5de18b131cd330"),
            ("g/glibc/libc6-dev_2.28-10+deb10u1_arm64.deb",
             "04097716018288cef8b82339faa1a9847838b89f39574b5b169f4657da8bf269"),
            ("g/gcc-8/libgcc1_8.3.0-6_arm64.deb",
             "2851ac25d12958586c035de5ec4f2fc17272dec48f776dd0dd24c62f62674fd9"),
            ("g/gcc-8/libgcc-8-dev_8.3.0-6_arm64.deb",
             "d8385c0106d4ac559ada75b162aecb9d507a67da3ac8742eb32258f501d07c7e"),
            ("l/linux/linux-libc-dev_4.19.249-2_arm64.deb",
             "e9a92d71d62655f673433c345a03fecaa4292f93c6572b28fd3d16dcfd1cc7f6"),
        )),
    ),
}  # fmt: skip


def sysroot_for(triple: str) -> SysrootPin:
    if triple not in SYSROOTS:
        raise ValueError(f"no glibc sysroot pinned for {triple}")
    return SYSROOTS[triple]
