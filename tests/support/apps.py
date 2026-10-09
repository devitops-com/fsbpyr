"""The demo application used by Phase 3 tests: packages, namespace package, resources, metadata."""

from pathlib import Path

from fsbpyr import bytecode
from fsbpyr.assemble import App
from fsbpyr.payload.format import XZ_PIECE, Codec, FileKind, ModuleKind
from fsbpyr.payload.spec import Blob, Dist, File, Module

SUPPORT = Path(__file__).parent

SOURCES = {
    "pkg/__init__.py": "VALUE = 'pkg'\n",
    "pkg/cli.py": "def main():\n    return 'cli'\n",
    "pkg/sub/__init__.py": "",
    "pkg/sub/deep.py": "DEPTH = 2\n",
    "ns/a.py": "VALUE = 'a'\n",
    "ns/b.py": "VALUE = 'b'\n",
    "entry.py": """\
import sys

BOOT_MODULES = sorted(sys.modules)


def main():
    import json

    if sys.argv[1:] == ["contract"]:
        import _fsbpyr
        import contract

        print(json.dumps(contract.run(_fsbpyr)))
    else:
        import probe

        print(json.dumps(probe.run(BOOT_MODULES)))
""",
    "contract.py": (SUPPORT / "contract.py").read_text(),
    "probe.py": (SUPPORT / "probe.py").read_text(),
}
NAMESPACES = ("ns", "pkg.data", "pkg.data.sub")  # dirs without __init__.py
NATIVE_EXT, NATIVE_LIB = "native.libs/_ext.so", "native.libs/libdep.so"
RESOURCES = {
    "pkg/data/table.json": b'{"k": 1}',
    "pkg/data/sub/x.txt": b"x",
}
DIST_INFO = "demo-1.0.dist-info"
METADATA = {
    f"{DIST_INFO}/METADATA": b"Metadata-Version: 2.4\nName: Demo\nVersion: 1.0\n",
    f"{DIST_INFO}/entry_points.txt": b"[console_scripts]\ndemo = pkg.cli:main\n",
    f"{DIST_INFO}/top_level.txt": b"ns\npkg\n",
    f"{DIST_INFO}/RECORD": b"pkg/__init__.py,,\npkg/cli.py,,\npkg/data/table.json,,\n",
}


def _module(rel: str, text: str) -> tuple[Module, File]:
    parts = rel.removesuffix(".py").split("/")
    is_package = parts[-1] == "__init__"
    name = ".".join(parts[:-1] if is_package else parts)
    source = Blob.zstd(text.encode())
    code = Blob.code(bytecode.dumps(compile(text, rel, "exec", dont_inherit=True)))
    kind = ModuleKind.PACKAGE if is_package else ModuleKind.MODULE
    dist = "demo" if parts[0] in ("pkg", "ns") else None
    return (
        Module(name, rel, kind, code=code, source=source, dist=dist),
        File(rel, FileKind.SOURCE, source, dist=dist),
    )


def library(blocks: int) -> bytes:
    """A compressible stand-in for a native library of ``blocks`` 4 KiB blocks, none alike."""
    return b"".join(b"\x7fELF " + bytes([i % 251]) * 4093 + i.to_bytes(3, "little")
                    for i in range(blocks))  # fmt: skip


PIECED = library(2200)  # about 9 MB: three pieces of xz (D33)


def _bad_xz() -> list[File]:
    """xz natives whose stream does not match the record (never valid, always refused), and a
    library in pieces, whole and with one piece broken."""
    good = Blob.xz(b"\x7fELF bad" * 2000)
    size = good.size
    broken = {"libtrailing.so": (good.stored + b"junk", size),
              "liblonger.so": (good.stored, size + 1),
              "libtruncated.so": (good.stored[:-40], size)}  # fmt: skip
    files = [File(f"native.libs/{name}", FileKind.NATIVE_LIB,
                  Blob(stored, length, Codec.XZ, bytes(32)), mode=0o755)
             for name, (stored, length) in broken.items()]  # fmt: skip
    pieces = Blob.xz(PIECED, digest=True)  # three xz streams, decoded in parallel by the launcher
    bad = (*pieces.pieces[:1], Blob(pieces.pieces[1].stored + b"junk", XZ_PIECE, Codec.XZ),
           *pieces.pieces[2:])  # fmt: skip
    files += [
        File("native.libs/libpieces.so", FileKind.NATIVE_LIB, pieces, mode=0o755),
        File("native.libs/libbadpiece.so", FileKind.NATIVE_LIB,
             Blob(b"", pieces.size, Codec.PIECES, bytes(32), bad), mode=0o755),
    ]  # fmt: skip
    return files


def demo_app(entrypoint: str = "entry:main") -> App:
    built = [_module(rel, text) for rel, text in SOURCES.items()]
    modules = [m for m, _ in built] + [
        Module(name, name.replace(".", "/"), ModuleKind.NAMESPACE, dist="demo")
        for name in NAMESPACES
    ]
    files = [f for _, f in built]
    files += [File(p, FileKind.RESOURCE, Blob.zstd(d), dist="demo") for p, d in RESOURCES.items()]
    files += [File(p, FileKind.METADATA, Blob.zstd(d), dist="demo") for p, d in METADATA.items()]
    files += [  # never loaded: they exercise file_at / closure / blob_info
        File(NATIVE_EXT, FileKind.NATIVE_EXT, Blob.zstd(b"\x7fELF ext", digest=True),
             mode=0o755, closure=(NATIVE_LIB,)),
        File(NATIVE_LIB, FileKind.NATIVE_LIB, Blob.xz(b"\x7fELF lib" * 2000, digest=True),
             mode=0o755),  # xz (D33): the launcher decodes it as the stand-in does
        *_bad_xz(),
    ]  # fmt: skip
    return App(
        "demo",
        entrypoint,
        modules=tuple(modules),
        files=tuple(files),
        dists=(Dist("demo", DIST_INFO, "1.0"),),
    )
