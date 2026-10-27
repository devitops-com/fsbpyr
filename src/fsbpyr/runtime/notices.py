"""Third-party licence notices of what a runtime links (REP-4): the texts PBS ships for the
built-in extensions' libraries, those fsbpyr supplies where PBS ships none, and fsbpyr's own."""

import importlib.metadata
import importlib.resources
from dataclasses import dataclass
from pathlib import Path

from ..payload.format import FileKind
from ..payload.spec import ZSTD_LEVEL, Blob, File
from .pbs import PbsDist, PbsError

NOTICE_DIR = "_fsbpyr_licenses"  # in the stdlib fragment, so in every app
FSBPYR_NOTICE = "LICENSE.fsbpyr.txt"
# Listed by PYTHON.json, shipped by no PBS archive before 20261020; the launcher's own zstd and
# the HACL* code in the hash modules are supplied (a text PBS ships is used instead). zlib-ng is
# listed for zlib and binascii, but libz.a is classic zlib (LICENSE.zlib.txt), so its text is
# left out, shipped or not.
_SUPPLIED = {"LICENSE.zstd.txt": ("fsbpyr.launcher", "vendor/zstd/LICENSE"),
             "LICENSE.hacl.txt": ("fsbpyr.runtime", "licenses/LICENSE.hacl.txt")}  # fmt: skip
NOT_LINKED = frozenset({"LICENSE.zlib-ng.txt"})
_HACL_OBJECTS = "/_hacl/"


@dataclass(frozen=True, slots=True)
class Notices:
    texts: dict[str, bytes]  # file name -> text, PBS's and supplied ones
    unshipped: frozenset[str]  # names of the linked libraries' texts that PBS does not ship


def _own_license() -> bytes:
    """fsbpyr's LICENSE: the installed distribution's, else the source checkout's."""
    try:
        text = importlib.metadata.distribution("fsbpyr").read_text("licenses/LICENSE")
    except importlib.metadata.PackageNotFoundError:
        text = None
    if text is not None:
        return text.encode()
    checkout = Path(__file__).resolve().parents[3] / "LICENSE"
    try:
        return checkout.read_bytes()
    except OSError as exc:
        raise PbsError(f"cannot find fsbpyr's LICENSE: {exc}") from exc


def _supplied(name: str) -> bytes:
    package, resource = _SUPPLIED[name]
    return (importlib.resources.files(package) / resource).read_bytes()


def _listed(dist: PbsDist) -> tuple[set[str], bool]:
    """The licence file names PBS lists for what the built-in modules link, and whether HACL*
    objects are among them (they carry no licence entry)."""
    build = dist.info["build_info"]
    names = {Path(str(dist.info["license_path"])).name}
    hacl = False
    for module in dist.builtin_modules():
        variant = build["extensions"][module][0]
        names.update(Path(path).name for path in variant.get("license_paths", ()))
        hacl = hacl or any(_HACL_OBJECTS in obj for obj in variant["objs"])
    return names, hacl


def collect(dist: PbsDist) -> Notices:
    listed, hacl = _listed(dist)
    texts: dict[str, bytes] = {}
    unshipped: set[str] = set()
    # the launcher links zstd whatever the Python; PBS lists its text only for _zstd, which 3.12
    # and 3.13 lack
    for name in sorted((listed | {"LICENSE.zstd.txt"}) - NOT_LINKED):
        path = dist.root / "licenses" / name
        if path.is_file():
            texts[name] = path.read_bytes()
        else:
            unshipped.add(name)
            if name in _SUPPLIED:
                texts[name] = _supplied(name)
    if hacl:
        texts["LICENSE.hacl.txt"] = _supplied("LICENSE.hacl.txt")
    return Notices(texts, frozenset(unshipped))


def files(dist: PbsDist) -> tuple[File, ...]:
    """The notices as resources of the stdlib fragment, fsbpyr's LICENSE with them."""
    texts = {**collect(dist).texts, FSBPYR_NOTICE: _own_license()}
    return tuple(File(f"{NOTICE_DIR}/{name}", FileKind.RESOURCE,
                      Blob.zstd(text, level=ZSTD_LEVEL))
                 for name, text in sorted(texts.items()))  # fmt: skip
