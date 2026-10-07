"""Shared builders for payload tests."""

import pytest

from fsbpyr.payload.format import FileFlag, FileKind, ModuleFlag, ModuleKind
from fsbpyr.payload.spec import Blob, Dist, File, Module, PayloadSpec

PY_HEXVERSION = 0x030E08F0
MAGIC = b"+\x0e\r\n"
GENERATION = bytes(range(32))
SO = "pkg/_speed.cpython-314-x86_64-linux-gnu.so"


def make_spec(**overrides: object) -> PayloadSpec:
    """A small but complete spec exercising every table and reference type."""
    code = Blob.code(b"code-pkg")
    fields: dict[str, object] = {
        "python_hexversion": PY_HEXVERSION,
        "bytecode_magic": MAGIC,
        "generation_id": GENERATION,
        "app_name": "demo",
        "entrypoint": "pkg.cli:main",
        "build_info": '{"profile": "balanced"}',
        "modules": (
            Module("pkg", "pkg/__init__.py", ModuleKind.PACKAGE, code=code, dist="demo"),
            Module(
                "pkg.cli",
                "pkg/cli.py",
                ModuleKind.MODULE,
                code=Blob.code(b"code-cli " * 100),
                source=Blob.zstd(b"def main(): pass\n" * 50),
                dist="demo",
            ),
            Module("pkg._speed", SO, ModuleKind.EXTENSION, file=SO, dist="demo"),
            Module("nspkg", "nspkg", ModuleKind.NAMESPACE),
            Module(
                "_boot",
                "_boot.py",
                ModuleKind.MODULE,
                code=Blob.code(b"boot"),
                flags=ModuleFlag.STARTUP,
            ),
        ),
        "files": (
            File(
                "pkg/data/table.json", FileKind.RESOURCE, Blob.zstd(b'{"k": 1}' * 64), dist="demo"
            ),
            File("pkg/cli.py", FileKind.SOURCE, Blob.zstd(b"def main(): pass\n" * 50), dist="demo"),
            File(
                SO,
                FileKind.NATIVE_EXT,
                Blob.zstd(b"\x7fELF ext" * 100, digest=True),
                mode=0o755,
                dist="demo",
                closure=("demo.libs/libfoo.so.1",),
            ),
            File(
                "demo.libs/libfoo.so.1",
                FileKind.NATIVE_LIB,
                Blob.raw(b"\x7fELF lib", digest=True),
                mode=0o755,
                dist="demo",
            ),
            File(
                "pkg/certs/ca.pem", FileKind.RESOURCE, Blob.raw(b"PEM"), flags=FileFlag.FS_REQUIRED
            ),
            File(
                "demo-1.0.dist-info/METADATA",
                FileKind.METADATA,
                Blob.raw(b"Name: demo\nVersion: 1.0\n"),
                dist="demo",
            ),
        ),
        "dists": (Dist("demo", "demo-1.0.dist-info", "1.0"),),
    }
    fields.update(overrides)
    return PayloadSpec(**fields)  # type: ignore[arg-type]


@pytest.fixture
def spec() -> PayloadSpec:
    return make_spec()
