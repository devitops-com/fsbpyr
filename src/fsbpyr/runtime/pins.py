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


PINS = (
    PbsPin(
        python="3.14",
        version="3.14.8",
        release="20261003",
        triple="x86_64-unknown-linux-gnu",
        flavor="pgo+lto-full",
        sha256="43b8c1b02e3daefbbbf4b5714b656ea00a5cae24f55c4589fb87468bcac682ed",
    ),
)


def pin_for(python: str, triple: str) -> PbsPin:
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
    platform: str  # wheels compatible with it, in uv's --python-platform spelling
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
        platform="x86_64-manylinux_2_28",
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
}  # fmt: skip


def sysroot_for(triple: str) -> SysrootPin:
    if triple not in SYSROOTS:
        raise ValueError(f"no glibc sysroot pinned for {triple}")
    return SYSROOTS[triple]
