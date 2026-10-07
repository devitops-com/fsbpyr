"""Pinned python-build-standalone distributions (URL and SHA-256 per Python and target)."""

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
