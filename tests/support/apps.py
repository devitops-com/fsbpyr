"""The demo application used by Phase 3 tests: packages, namespace package, resources, metadata."""

from pathlib import Path

from fsbpyr import bytecode
from fsbpyr.assemble import App
from fsbpyr.payload.format import FileKind, ModuleKind
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


def demo_app(entrypoint: str = "entry:main") -> App:
    built = [_module(rel, text) for rel, text in SOURCES.items()]
    modules = [m for m, _ in built] + [
        Module(name, name.replace(".", "/"), ModuleKind.NAMESPACE, dist="demo")
        for name in NAMESPACES
    ]
    files = [f for _, f in built]
    files += [File(p, FileKind.RESOURCE, Blob.zstd(d), dist="demo") for p, d in RESOURCES.items()]
    files += [File(p, FileKind.METADATA, Blob.zstd(d), dist="demo") for p, d in METADATA.items()]
    return App(
        "demo",
        entrypoint,
        modules=tuple(modules),
        files=tuple(files),
        dists=(Dist("demo", DIST_INFO, "1.0"),),
    )
