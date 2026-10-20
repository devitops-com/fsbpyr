"""``fsbpyr inspect``: what an executable was built from, and with which fsbpyr (D28).

Everything comes from the payload: its build information, distributions, modules and native
files, so it works for executables built by any fsbpyr version (builds before 0.2.0 recorded
neither the project nor fsbpyr's own commit). The values come from a file that may not be
trusted, so text output never carries control characters (terminal escape sequences); JSON
escapes them anyway. Lists are laid out in as many columns as the terminal fits, like ``ls``.
"""

import cmd
import hashlib
import io
import json
import re
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path

from packaging.utils import canonicalize_name

from .elf import ElfError, section
from .natives import init_symbol
from .payload.format import NATIVE_KINDS, NONE, FileKind, PayloadError
from .payload.reader import Executable, FileRecord, Payload
from .vcs import version_string

UNKNOWN = "unknown"
RUNTIME = "standard library and fsbpyr"  # modules of no distribution
_INDENT = "  "
_PLAIN_SO = re.compile(r"[^.]+\.so")  # no ABI tag: a library or an extension, the file can tell


class InspectError(RuntimeError):
    """The file cannot be read, or fsbpyr did not build it."""


@dataclass(frozen=True, slots=True)
class Report:
    path: Path
    size: int
    app: str
    info: dict[str, object]
    dists: tuple[tuple[str, str], ...]  # (name, version)
    modules: dict[str, list[str]]  # every distribution -> its module names, sorted
    runtime: list[str] = field(default_factory=list)  # modules of no distribution, sorted
    shared_libraries: list[str] = field(default_factory=list)
    extension_modules: list[str] = field(default_factory=list)
    verified: bool = False
    digest: str = ""  # hex: of the whole file (D31), of the payload in older executables
    covers_file: bool = True
    sha256: str = ""  # hex: plain SHA-256 of the whole file, as attestations name it; --verify only
    toolchain: tuple[str, ...] = ()  # the stub's .comment lines: compilers and linker that made it


def _shown(value: object) -> str:
    text = str(value)
    return text if text.isprintable() else repr(text)


def _build_info(path: Path, text: str) -> dict[str, object]:
    try:
        info = json.loads(text)
    except (ValueError, RecursionError) as exc:  # bad JSON, numbers beyond the digit limit
        raise InspectError(f"{path} has corrupt build information") from exc
    if not isinstance(info, dict):
        raise InspectError(f"{path} has corrupt build information")
    return info


def _is_extension(payload: Payload, file: FileRecord) -> bool:
    """A NATIVE_EXT backs a module record, but a library ending in a plain ``.so`` is
    importable by name too: what CPython would call (PyInit_) tells them apart. Only such
    names are looked into, streaming; a damaged or oversized blob keeps the record's word."""
    if file.kind is not FileKind.NATIVE_EXT:
        return False
    if not _PLAIN_SO.fullmatch(file.path.rpartition("/")[2]):
        return True  # an ABI-tagged name (.cpython-314-..., .abi3) is an extension
    try:
        return payload.contains(file.blob, init_symbol(file.path))
    except PayloadError:
        return True


def _file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _toolchain(stub: bytes) -> tuple[str, ...]:
    """The lines of the stub's ``.comment`` section (clang, lld and what they linked in); none
    when the stub has no such section or is not an ELF file (a test double)."""
    try:
        comment = section(stub, ".comment")
    except ElfError:
        return ()
    lines = (part.decode("utf-8", errors="replace") for part in (comment or b"").split(b"\0"))
    return tuple(dict.fromkeys(line for line in lines if line))


def read(path: Path, *, verify: bool = False) -> Report:
    try:
        status = path.stat()
        if not stat.S_ISREG(status.st_mode):  # a FIFO would block the open
            raise InspectError(f"{path} is not a regular file")
        with Executable.open(path) as executable:
            if verify:
                try:
                    executable.verify()
                except PayloadError as exc:
                    raise InspectError(f"{path} is damaged or was changed after its build "
                                       f"({exc})") from exc  # fmt: skip
            payload = executable.payload
            dists = payload.dists()
            modules: dict[str, list[str]] = {d.name: [] for d in dists}
            runtime: list[str] = []
            for module in payload.modules():
                owner = runtime if module.dist == NONE else modules[dists[module.dist].name]
                owner.append(module.name)
            libraries: list[str] = []
            extensions: list[str] = []
            for file in payload.files():
                if file.kind in NATIVE_KINDS:
                    (extensions if _is_extension(payload, file) else libraries).append(file.path)
            report = Report(
                path, status.st_size, payload.app_name, _build_info(path, payload.build_info),
                tuple((d.name, d.version) for d in dists),
                {name: sorted(names) for name, names in modules.items()}, sorted(runtime),
                sorted(libraries), sorted(extensions), verified=verify,
                digest=executable.digest.hex(), covers_file=executable.covers_file,
                sha256=_file_sha256(path) if verify else "",
                toolchain=_toolchain(executable.stub_prefix()),
            )  # fmt: skip
    except PayloadError as exc:
        raise InspectError(f"{path} is not an executable fsbpyr built ({exc})") from exc
    except OSError as exc:
        raise InspectError(f"cannot read {path}: {exc.strerror or exc}") from exc
    return report


def _project(report: Report) -> tuple[str, str | None, str | None]:
    """Name, version and git revision (``0.8.1-4f9771de``) of the app's project."""
    project = report.info.get("project")
    project = project if isinstance(project, dict) else {}
    name, version = project.get("name"), project.get("version")
    name = name if isinstance(name, str) else report.app
    version = version if isinstance(version, str) else None
    git = report.info.get("git")
    return name, version, version_string(git) if isinstance(git, dict) else None


def _built_with(info: dict[str, object]) -> str:
    """What `fsbpyr --version` of the building fsbpyr printed; its release number when it
    could not see its commit (a wheel install), and for builds before 0.2.0."""
    version = info.get("fsbpyr_version")
    if not isinstance(version, str) or version == version_string(None):
        version = info.get("fsbpyr", UNKNOWN)
    return str(version)


def _compression(report: Report) -> str:
    """The zstd level of the content (recorded since 0.4.0, D29) and, if there are native
    files, theirs (since 0.8.0, D33; before, zstd at the same level)."""
    level = report.info.get("zstd_level")
    if level is None:
        return UNKNOWN
    content = f"zstd {level}"
    if not (report.shared_libraries or report.extension_modules):
        return content
    natives = report.info.get("native_compression", "zstd")
    return f"{content}, native files {content if natives == 'zstd' else natives}"


def _tops(names: list[str]) -> list[str]:
    return sorted({name.partition(".")[0] for name in names})


def _top_level(report: Report) -> dict[str, list[str]]:
    return {dist: _tops(names) for dist, names in report.modules.items()}


def as_json(report: Report, *, all_modules: bool = False) -> str:
    name, version, revision = _project(report)
    document = {
        "path": str(report.path),
        "size": report.size,
        "app": {"name": name, "version": version, "revision": revision},
        "built_with": _built_with(report.info),
        "python": report.info.get("python"),
        "uv": report.info.get("uv"),
        "glibc": report.info.get("glibc"),
        "toolchain": list(report.toolchain),
        "digest": report.digest,
        "digest_covers": "file" if report.covers_file else "payload",
        "sha256": report.sha256 or None,
        "verified": report.verified or None,
        "distributions": [{"name": n, "version": v} for n, v in report.dists],
        "shared_libraries": report.shared_libraries,
        "extension_modules": report.extension_modules,
        "all_modules": all_modules,  # modules lists every module, else top-level names
        "modules": report.modules if all_modules else _top_level(report),
        "runtime_modules": report.runtime if all_modules else _tops(report.runtime),
        "build_info": report.info,
    }
    return json.dumps(document, indent=2)


def _columns(items: list[str]) -> list[str]:
    """``items`` in as many columns as the terminal fits (column-major, like ``ls``)."""
    if not items:
        return [f"{_INDENT}(none)"]
    out = io.StringIO()
    width = shutil.get_terminal_size().columns - len(_INDENT)
    cmd.Cmd(stdout=out).columnize([_shown(item) for item in items], max(width, 1))
    return [_INDENT + line.rstrip() for line in out.getvalue().splitlines()]


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'s' * (count != 1)}"


def _modules_by_distribution(report: Report) -> list[str]:
    tops = _top_level(report)
    entries = []
    for dist, _ in report.dists:
        names = tops[dist]
        same = [canonicalize_name(n) for n in names] == [dist]  # "numpy" provides numpy
        entries.append(f"{dist}: {' '.join(names)}" if names and not same else dist)
    count = len({name for names in tops.values() for name in names})  # namespaces count once
    runtime = _tops(report.runtime)
    dists = _plural(len(report.dists), "distribution")
    return [
        f"modules: {_plural(count, 'top-level name')} in {dists}",
        *_columns(entries),
        f"{RUNTIME}: {_plural(len(runtime), 'top-level name')}",
        *_columns(runtime),
    ]


def _every_module(report: Report) -> list[str]:
    lines = []
    for dist, version in report.dists:
        names = report.modules[dist]
        lines += [f"{_shown(f'{dist} {version}')}: {_plural(len(names), 'module')}",
                  *_columns(names)]  # fmt: skip
    return [*lines, f"{RUNTIME}: {_plural(len(report.runtime), 'module')}",
            *_columns(report.runtime)]  # fmt: skip


def as_text(report: Report, *, all_modules: bool = False) -> list[str]:
    name, version, revision = _project(report)
    app_line = " ".join(_shown(part) for part in (name, version) if part)
    if revision:
        app_line += f" ({_shown(revision)})"
    glibc = report.info.get("glibc")
    glibc_text = f", glibc >= {_shown(glibc)}" if glibc else ""
    lines = [
        f"app        {app_line}",
        f"built with fsbpyr {_shown(_built_with(report.info))}",
        f"python     {_shown(report.info.get('python', UNKNOWN))}{glibc_text}",
        f"uv         {_shown(report.info.get('uv', UNKNOWN))}",
        f"size       {report.size / 1e6:.1f} MB, {_plural(len(report.dists), 'distribution')}",
        f"compressed {_shown(_compression(report))}",
        f"digest     {report.digest}{'' if report.covers_file else ' (payload only)'}"
        + (" verified" if report.verified else ""),
    ]
    if report.sha256:
        lines.append(f"sha256     {report.sha256}")
    if report.toolchain:
        first, *rest = (_shown(line) for line in report.toolchain)
        lines += [f"toolchain  {first}", *(f"           {line}" for line in rest)]
    lines += [
        f"shared libraries: {len(report.shared_libraries)}",
        *_columns(report.shared_libraries),
        f"extension modules: {len(report.extension_modules)}",
        *_columns(report.extension_modules),
    ]
    return lines + (_every_module(report) if all_modules else _modules_by_distribution(report))
