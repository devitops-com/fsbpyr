"""The demo application used by Phase 3 tests: packages, namespace package, resources, metadata."""

from pathlib import Path

from fsbpyr import bytecode
from fsbpyr.assemble import App
from fsbpyr.payload.format import XZ_PIECE, Codec, FileKind, ModuleKind
from fsbpyr.payload.spec import Blob, Dist, File, Module, stored_bytes

from .contract import DIGEST_SIZES

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
    elif sys.argv[1:] == ["signatures"]:
        import _fsbpyr
        import signatures

        print(json.dumps(signatures.run(_fsbpyr)))
    else:
        import probe

        print(json.dumps(probe.run(BOOT_MODULES)))
""",
    "contract.py": (SUPPORT / "contract.py").read_text(),
    "signatures.py": (SUPPORT / "signatures.py").read_text(),
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
# Resources the cache sync (D35) extracts on request: more directories than a low descriptor limit
# leaves room for if each stayed open, and a name near the file system's limit.
TREE = {f"tree/d{i:02}/f.txt": b"%d" % i for i in range(40)} | {"tree/" + "n" * 250: b"long"}
SIZES = {f"sizes.libs/lib{size}.so": (bytes(range(251)) * (size // 251 + 1))[:size]
         for size in DIGEST_SIZES}  # fmt: skip


def _filler(name: str, size: int) -> bytes:
    """``size`` bytes, compressible and of this file only."""
    line = name.encode() + b"\n"
    return (line * (size // len(line) + 1))[:size]


# More files than one parallel pass checks (1024), more bytes of small files than its buffer holds
# (16 MiB), and sizes around the read-or-map switch (64 KiB): the cache sync's batches (D35).
BATCH = (
    {f"batch/t{i:04}.txt": _filler(f"t{i}", 20 + i % 50) for i in range(1100)}
    | {f"batch/m{i:03}.bin": _filler(f"m{i}", 60 << 10) for i in range(300)}
    | {f"batch/edge{n}.bin": _filler(f"edge{n}", n) for n in (65535, 65536, 65537)}
)
# Files over 64 KiB that a check holds open, then a file whose directories need descriptors (D36).
HELD = {f"held/a/f{i:02}.bin": _filler(f"f{i}", 70 << 10) for i in range(40)} | {
    "held/b/c/d/e/f/x.txt": b"x"
}
# more files than a sync takes at once (module.c's SYNC_CHUNK, 4096), the last one with a name
# no file system takes; one file 40 directories deep (D38)
CHUNKS = {f"chunks/f{i:04}.txt": b"%d" % i for i in range(4100)} | {"chunks/zz/" + "z" * 256: b"z"}
DEEP = {"deep/" + "d/" * 40 + "x.txt": b"deep"}


def _bad_xz() -> list[File]:
    """xz natives whose stream does not match the record (never valid, always refused), and a
    library in pieces, whole and with one piece broken."""
    good = Blob.xz(b"\x7fELF bad" * 2000)
    size = good.size
    stream = stored_bytes(good.stored)
    broken = {"libtrailing.so": (stream + b"junk", size),
              "liblonger.so": (stream, size + 1),
              "libtruncated.so": (stream[:-40], size)}  # fmt: skip
    files = [File(f"native.libs/{name}", FileKind.NATIVE_LIB,
                  Blob(stored, length, Codec.XZ, bytes(32)), mode=0o755)
             for name, (stored, length) in broken.items()]  # fmt: skip
    pieces = Blob.xz(PIECED, digest=True)  # three xz streams, decoded in parallel by the launcher
    bad = (*pieces.pieces[:1],
           Blob(stored_bytes(pieces.pieces[1].stored) + b"junk", XZ_PIECE, Codec.XZ),
           *pieces.pieces[2:])  # fmt: skip
    wrong = Blob.zstd(b"\x7fELF wrong" * 500)  # intact content, another digest
    files += [
        File("native.libs/libpieces.so", FileKind.NATIVE_LIB, pieces, mode=0o755),
        File("native.libs/libbadpiece.so", FileKind.NATIVE_LIB,
             Blob(b"", pieces.size, Codec.PIECES, bytes(32), bad), mode=0o755),
        File("native.libs/libwrongdigest.so", FileKind.NATIVE_LIB,
             Blob(wrong.stored, wrong.size, Codec.ZSTD, bytes(32)), mode=0o755),
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
    files += [File(p, FileKind.RESOURCE, Blob.zstd(d)) for p, d in TREE.items()]
    files += [File(p, FileKind.RESOURCE, Blob.zstd(d)) for p, d in BATCH.items()]
    files += [File(p, FileKind.RESOURCE, Blob.zstd(d)) for p, d in HELD.items()]
    files += [File(p, FileKind.RESOURCE, Blob.zstd(d)) for p, d in (CHUNKS | DEEP).items()]
    files += [  # never loaded: they exercise file_at / closure / blob_info / sync
        File(NATIVE_EXT, FileKind.NATIVE_EXT, Blob.zstd(b"\x7fELF ext", digest=True),
             mode=0o755, closure=(NATIVE_LIB,)),
        File(NATIVE_LIB, FileKind.NATIVE_LIB, Blob.xz(b"\x7fELF lib" * 2000, digest=True),
             mode=0o755),  # xz (D33): the launcher decodes it as the stand-in does
        *_bad_xz(),
        *(File(path, FileKind.NATIVE_LIB, Blob.zstd(data, digest=True), mode=0o755)
          for path, data in SIZES.items()),
        File("toolong/" + "l" * 256, FileKind.NATIVE_LIB, Blob.zstd(b"\x7fELF long" * 99,
             digest=True), mode=0o755),  # a name no file system takes (NAME_MAX 255)
    ]  # fmt: skip
    return App(
        "demo",
        entrypoint,
        modules=tuple(modules),
        files=tuple(files),
        dists=(Dist("demo", DIST_INFO, "1.0"),),
    )
