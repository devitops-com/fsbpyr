"""``fsbpyr inspect --licenses``: the third-party licence notices an executable carries (REP-4).

The runtime's own (``_fsbpyr_licenses/``: the libraries linked into the stub, fsbpyr's) are
exact. The distributions' are best effort: what the packages ship under the names licences
usually have, which is not every notice a wheel's bundled libraries need. Texts come from a
file that may not be trusted, so control characters (terminal escape sequences) are shown
escaped.
"""

import email.parser
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .payload.format import FileKind
from .payload.reader import Executable, FileRecord, Payload
from .runtime.notices import NOTICE_DIR

_NAMES = re.compile(r"(licen[cs]e|copying|notice|thirdpartynotices)", re.IGNORECASE)
_KINDS = (FileKind.RESOURCE, FileKind.METADATA)


@dataclass(frozen=True, slots=True)
class Notice:
    owner: str  # "fsbpyr" or "<dist> <version>"
    path: str
    text: str


def _escaped(text: str) -> str:
    return "".join(c if c in "\n\t" or c.isprintable() else repr(c)[1:-1] for c in text)


def _text(payload: Payload, file: FileRecord) -> str:
    return _escaped(payload.read_blob(file.blob).decode("utf-8", errors="replace"))


def _named(path: str) -> bool:
    return _NAMES.match(PurePosixPath(path).name) is not None


def _declared(payload: Payload, info: str) -> set[str]:
    """The files METADATA names (``License-File``), where an installer put them."""
    metadata = payload.find_file(f"{info}/METADATA")
    if metadata is None:
        return set()
    text = payload.read_blob(metadata.blob).decode("utf-8", errors="replace")
    names = email.parser.Parser().parsestr(text, headersonly=True).get_all("License-File") or []
    stripped = [name.strip() for name in names]
    return {f"{info}/{name}" for name in stripped} | {f"{info}/licenses/{n}" for n in stripped}


def _is_notice(path: str, info: str, declared: set[str]) -> bool:
    if path in declared or path.startswith(f"{info}/licenses/"):
        return True
    directory = PurePosixPath(path).parent.as_posix()
    return _named(path) and (directory == info or not path.startswith(f"{info}/"))


def read(path: Path) -> list[Notice]:
    """The notices of the executable at ``path``: the runtime's, then each distribution's."""
    with Executable.open(path) as executable:
        payload = executable.payload
        files = sorted(payload.files(), key=lambda f: f.path)
        runtime = (f for f in files if f.path.startswith(f"{NOTICE_DIR}/"))
        found = [Notice("fsbpyr", f.path, _text(payload, f)) for f in runtime
                 if f.kind is FileKind.RESOURCE]  # fmt: skip
        for index, dist in enumerate(payload.dists()):
            declared = _declared(payload, dist.dist_info)
            owner = f"{dist.name} {dist.version}"
            found += [Notice(owner, f.path, _text(payload, f)) for f in files
                      if f.dist == index and f.kind in _KINDS
                      and _is_notice(f.path, dist.dist_info, declared)]  # fmt: skip
    return found


def render(notices: list[Notice]) -> str:
    """One block per notice, headed by its owner and path."""
    blocks = [
        f"==== {_escaped(n.owner)}: {_escaped(n.path)} ====\n{n.text.rstrip()}\n" for n in notices
    ]
    return "\n".join(blocks)
