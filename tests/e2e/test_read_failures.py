"""The launcher's blob reads out of memory (launcher/payload.c), probed (tests/probes/reads.c,
the decoders' functions wrapped): fsb_read_blob fails with ENOMEM and a stream with
FSB_STREAM_MEMORY, which module.c raises as MemoryError, also when a helper thread, whose errno
its caller never sees, decoded the piece; corrupt data stays corrupt (errno 0, a PayloadError)
when dropping the payload's pages fails, as madvise does in an app that locked its memory. A
stub linked with zstd's context wrapped (tests/probes/no_context.c) shows that a start out of
memory says so (main.c), not that its startup module is corrupt."""

import dataclasses
import errno
import os
import random
import subprocess
from pathlib import Path
from typing import Any

import pytest

from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import XZ_PIECE, ZSTD_PIECE, Codec, FileKind
from fsbpyr.payload.reader import Executable
from fsbpyr.payload.spec import Blob, File
from fsbpyr.runtime import pbs, pins, registry, stub, sysroot
from fsbpyr.runtime.registry import Runtime
from fsbpyr.runtime.toolchain import find_toolchain

from ..unit.boot.conftest import FAKE_RUNTIME
from .conftest import LauncherBuild, app_content

pytestmark = pytest.mark.slow

PROBE = Path(__file__).parents[1] / "probes" / "reads.c"
NO_CONTEXT = Path(__file__).parents[1] / "probes" / "no_context.c"
CODECS = {"r/raw": Codec.NONE, "z/frame": Codec.ZSTD, "z/member": Codec.BLOCK,
          "z/pieces": Codec.ZSTD_PIECES, "lib/x.so": Codec.XZ,
          "lib/pieces.so": Codec.PIECES}  # fmt: skip
OOM, OK = [str(errno.ENOMEM), "memory"], ["ok", "end"]
# {function that has no memory: {file: how its read and its stream end}}; the others' are OK
FAILS = {
    "ZSTD_createDCtx": {"z/frame": OOM, "z/member": OOM},  # the decoder's context, a stream's
    "ZSTD_decompress": {"z/pieces": OOM},  # a piece's context, on any thread
    "ZSTD_decompressStream": {"z/frame": ["ok", "memory"]},  # a stream's window
    "lzma_stream_buffer_decode": {"lib/x.so": OOM, "lib/pieces.so": OOM},
}
WRAPPED = (*FAILS, "madvise")


def _content(size: int, seed: int) -> bytes:
    """Compressible, of this blob only: zstd and xz keep it compressed."""
    noise = random.Random(seed)  # noqa: S311  # test data
    words = noise.choices([b"alpha ", b"beta ", b"gamma ", b"\0" * 9], k=size // 5 + 1)
    return b"".join(words)[:size]


@pytest.fixture(scope="module")
def probe(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("probe") / "reads"
    flags = ("-Wl," + ",".join(f"--wrap={name}" for name in WRAPPED),
             "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined")  # fmt: skip
    return launcher_build.compile(out, [PROBE, "payload.c", "integrity.c", "parallel.c"], *flags)


def _image(directory: Path, files: tuple[File, ...]) -> Path:
    modules, sources = app_content({"x.py": "def main():\n    pass\n"})
    out = directory / "app"
    build_executable(FAKE_RUNTIME, App("x", "x:main", modules=modules, files=(*sources, *files)),
                     out)  # fmt: skip
    return out


@pytest.fixture(scope="module")
def image(tmp_path_factory: pytest.TempPathFactory) -> Path:
    blobs = {"r/raw": Blob.raw(_content(100 << 10, 6)), "z/frame": Blob.zstd(_content(2 << 20, 1)),
             "z/member": Blob.zstd(b"a block member"),
             "z/pieces": Blob.zstd(_content(ZSTD_PIECE + 1, 2)),
             "lib/x.so": Blob.xz(_content(100 << 10, 3), digest=True),
             "lib/pieces.so": Blob.xz(_content(XZ_PIECE + 1, 4), digest=True)}  # fmt: skip
    kinds = {True: FileKind.NATIVE_LIB, False: FileKind.RESOURCE}
    files = tuple(File(path, kinds[path.startswith("lib/")], blob, mode=0o755)
                  for path, blob in blobs.items())  # fmt: skip
    return _image(tmp_path_factory.mktemp("image"), files)


def _reads(probe: Path, image: Path, failing: str = "") -> dict[str, list[str]]:
    """{path: [codec, how the read ended, how the stream ended]} for the payload's files."""
    env = {k: v for k, v in os.environ.items() if k != "PROBE_NO_MEMORY"}
    if failing:
        env["PROBE_NO_MEMORY"] = failing
    result = subprocess.run([probe, image], capture_output=True, text=True, check=False,
                            env=env, timeout=120)  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    return {line.split()[0]: line.split()[1:] for line in result.stdout.splitlines()}


def test_the_image_has_every_codec(probe: Path, image: Path) -> None:
    read = _reads(probe, image)

    assert {path: Codec(int(read[path][0])) for path in CODECS} == CODECS
    assert all(read[path][1:] == OK for path in CODECS)


@pytest.mark.parametrize("failing", FAILS)
def test_a_decoder_out_of_memory_fails_with_enomem_not_as_corrupt_data(
    probe: Path, image: Path, failing: str
) -> None:
    read = _reads(probe, image, failing)

    assert {path: read[path][1:] for path in CODECS} == {
        path: FAILS[failing].get(path, OK) for path in CODECS
    }


def test_corrupt_data_stays_corrupt_when_dropping_the_pages_fails(
    probe: Path, tmp_path: Path
) -> None:
    """A frame of 1 MiB whose checksum fails, read until the reads drop the payload's pages."""
    data = random.Random(5).randbytes(1 << 20)  # noqa: S311  # test data
    blob = Blob.code(data)  # a zstd frame, never raw
    image = _image(tmp_path, (File("z/corrupt", FileKind.RESOURCE, blob),))
    with Executable.open(image) as exe:
        record = exe.payload.find_file("z/corrupt")
        assert record is not None
        stored = exe.payload.blob(record.blob)
        assert stored.codec is Codec.ZSTD
        position = exe.payload_offset + stored.offset + stored.stored_size // 2
    damaged = bytearray(image.read_bytes())
    damaged[position] ^= 0x5A
    image.write_bytes(bytes(damaged))

    result = subprocess.run([probe, image, "z/corrupt"], capture_output=True, text=True,
                            check=False, timeout=120)  # fmt: skip

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["sweeps 1", "errno 0"]


@pytest.fixture(scope="module")
def no_context(runtime: Runtime, tmp_path_factory: pytest.TempPathFactory) -> Runtime:
    """The runtime with its stub linked again with tests/probes/no_context.c, which makes zstd
    fail to make a decompression context when $PROBE_NO_CONTEXT is set."""
    pin = pins.pin_for(runtime.python_version.rpartition(".")[0], registry.host_triple())
    cache = registry.cache_root()
    dist = pbs.fetch(pin, cache)
    root = sysroot.fetch_sysroot(pins.sysroot_for(pin.triple, runtime.baseline), cache)
    toolchain = dataclasses.replace(find_toolchain(dist.llvm_major), sysroot=root)
    linked = stub.stub_command

    def command(*args: Any, **kwargs: Any) -> list[str]:
        return [*linked(*args, **kwargs), os.fspath(NO_CONTEXT), "-Wl,--wrap=ZSTD_createDCtx"]

    out = tmp_path_factory.mktemp("no-context") / "stub"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(stub, "stub_command", command)
        stub.build_stub(dist, toolchain, out)
    return dataclasses.replace(runtime, stub=out.read_bytes())


def test_a_start_out_of_memory_says_so(no_context: Runtime, tmp_path: Path) -> None:
    """main.c reads the startup modules before Python starts; their code is in blocks, whose
    decompression takes the process's zstd context, made on first use."""
    modules, files = app_content({"x.py": "def main():\n    print('ran')\n"})
    app = tmp_path / "app"
    build_executable(no_context, App("x", "x:main", modules=modules, files=files), app)

    ran = subprocess.run([app], capture_output=True, text=True, check=False, timeout=60)
    failed = subprocess.run([app], capture_output=True, text=True, check=False, timeout=60,
                            env={**os.environ, "PROBE_NO_CONTEXT": "1"})  # fmt: skip

    assert (ran.returncode, ran.stdout) == (0, "ran\n"), ran.stderr
    assert failed.returncode == 70
    assert failed.stderr == f"fsbpyr: cannot start {app}: out of memory\n"
