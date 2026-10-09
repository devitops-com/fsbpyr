"""The launcher's blob streams at their window boundaries (D38): built here with windows of
2 MiB (tests/probes/streams.c), a blob of every codec around them reads back as it was given,
from its start and after seeks forward and back; windows from the start are whole digest leaves
but the last, so extraction can hash them as they come, and none is larger than the window but
a piece of a large xz library."""

import hashlib
import random
import subprocess
from pathlib import Path

import pytest

from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import DIGEST_LEAF, XZ_PIECE, Codec, FileKind
from fsbpyr.payload.spec import Blob, File

from ..unit.boot.conftest import FAKE_RUNTIME
from .conftest import LauncherBuild, app_content

pytestmark = pytest.mark.slow

PROBE = Path(__file__).parents[1] / "probes" / "streams.c"
WINDOW = 2 * DIGEST_LEAF
SIZES = (0, 1, WINDOW - 1, WINDOW, WINDOW + 1, 2 * WINDOW, 2 * WINDOW + 7)
LIBRARIES = (100 << 10, 3 * DIGEST_LEAF, XZ_PIECE, XZ_PIECE + 1, 2 * XZ_PIECE + 5)  # xz; pieces


def _content(size: int, seed: int) -> bytes:
    """Compressible, of this blob only: zstd and xz keep it compressed."""
    noise = random.Random(seed)  # noqa: S311  # test data
    words = noise.choices([b"alpha ", b"beta ", b"gamma ", b"\0" * 9], k=size // 5 + 1)
    return b"".join(words)[:size]


def _files() -> dict[str, tuple[Blob, bytes]]:
    files = {}
    for size in SIZES:
        data = _content(size, size)
        files[f"r/raw{size}"] = (Blob.raw(data), data)
        files[f"r/zstd{size}"] = (Blob.zstd(data), data)
    files["r/small"] = (Blob.zstd(b"a block member"), b"a block member")
    for size in LIBRARIES:
        data = _content(size, size + 1)
        files[f"lib/xz{size}.so"] = (Blob.xz(data, digest=True), data)
    return files


@pytest.fixture(scope="module")
def probe(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("probe") / "streams"
    flags = (f"-DFSB_STREAM_WINDOW={WINDOW}u", "-fsanitize=address,undefined",
             "-fno-sanitize-recover=undefined")  # fmt: skip
    return launcher_build.compile(out, [PROBE, "payload.c", "integrity.c", "parallel.c"], *flags)


@pytest.fixture(scope="module")
def streamed(probe: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[str]]:
    files = _files()
    modules, sources = app_content({"x.py": "def main():\n    pass\n"})
    kinds = {True: FileKind.NATIVE_LIB, False: FileKind.RESOURCE}
    records = tuple(File(path, kinds[path.startswith("lib/")], blob, mode=0o755)
                    for path, (blob, _) in files.items())  # fmt: skip
    app = tmp_path_factory.mktemp("image") / "app"
    build_executable(FAKE_RUNTIME, App("x", "x:main", modules=modules, files=(*sources, *records)),
                     app)  # fmt: skip
    result = subprocess.run([probe, app], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return {line.split()[0]: line.split()[1:] for line in result.stdout.splitlines()}


def test_every_codec_is_streamed(streamed: dict[str, list[str]]) -> None:
    codecs = {path: Codec(int(streamed[path][1])) for path in _files()}  # as the image has them

    assert {codecs[f"r/raw{s}"] for s in SIZES[2:]} == {Codec.NONE}  # small ones join blocks
    assert {codecs[f"r/zstd{s}"] for s in SIZES[2:]} == {Codec.ZSTD}
    assert codecs["r/small"] is Codec.BLOCK
    assert [codecs[f"lib/xz{s}.so"] for s in LIBRARIES] == [Codec.XZ] * 3 + [Codec.PIECES] * 2


@pytest.mark.parametrize("path", sorted(_files()))
def test_a_stream_reads_the_content_back_from_anywhere(
    streamed: dict[str, list[str]], path: str
) -> None:
    blob, data = _files()[path]
    size, _, *windows, whole = streamed[path][: streamed[path].index("|")]
    middle, after_first = streamed[path][streamed[path].index("|") + 1 :]

    assert int(size) == len(data)
    assert whole == hashlib.sha256(data).hexdigest()
    assert middle == hashlib.sha256(data[len(data) // 2 :]).hexdigest()
    assert after_first == hashlib.sha256(data[1:]).hexdigest()
    lengths = [int(w) for w in windows]
    assert sum(lengths) == len(data) and all(length > 0 for length in lengths)
    assert all(length % DIGEST_LEAF == 0 for length in lengths[:-1])  # whole leaves but the last
    largest = XZ_PIECE if blob.codec in (Codec.XZ, Codec.PIECES) else WINDOW
    assert all(length <= largest for length in lengths)
