"""The cache sync writes files on up to 8 threads (D39, launcher/cache.c), probed
(tests/probes/restore.c): small files, in chunks of neighbours that share a block, on the
helpers too; files over a zstd piece, in pieces or in xz, by the calling thread alone, since
their pieces are decoded in parallel already; never more than 8 threads at once, the hashing of
what they write included. A new file is written unnamed in its directory and linked into place
(no directory that every thread locks; nothing left behind if the process is killed); a changed
one is replaced from the scratch directory. The failure reported is the first in path order
among the pooled files, whichever thread met it; and with too few descriptors for the helpers,
the calling thread writes what is left."""

import hashlib
import os
import random
import subprocess
from pathlib import Path

import pytest

from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import XZ_PIECE, ZSTD_PIECE, FileKind
from fsbpyr.payload.spec import Blob, File

from ..unit.boot.conftest import FAKE_RUNTIME
from .conftest import LauncherBuild, app_content

pytestmark = pytest.mark.slow

PROBE = Path(__file__).parents[1] / "probes" / "restore.c"
SMALL = 3000  # in 30 directories, of up to 20 kB: block members
FIRST_WRONG, SECOND_WRONG = 2000, 2900  # their digests do not match (the failing image)
ALONE = ("pieces.bin", "lib.so", "libpieces.so")  # zstd pieces, xz, xz pieces
CORES = min(8, len(os.sched_getaffinity(0)))


def _links_unnamed_files(directory: Path) -> bool:
    """Whether the file system of ``directory`` has O_TMPFILE and links (some overlay and network
    file systems have not: the launcher renames from its scratch directory there)."""
    try:
        fd = os.open(directory, os.O_TMPFILE | os.O_WRONLY, 0o600)
    except OSError:
        return False
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        # a dir_fd makes 3.12 and 3.13 call linkat(AT_SYMLINK_FOLLOW), as 3.14 always does;
        # without one they call link(), which links the /proc symlink itself (EXDEV)
        os.link(f"/proc/self/fd/{fd}", "probe", dst_dir_fd=dir_fd, follow_symlinks=True)
    except OSError:
        return False
    finally:
        os.close(fd)
        os.close(dir_fd)
    (directory / "probe").unlink()
    return True


def _content(size: int, seed: int) -> bytes:
    """Compressible, of this file only."""
    noise = random.Random(seed)  # noqa: S311  # test data
    words = noise.choices([b"alpha ", b"beta ", b"gamma ", b"\0" * 9], k=size // 5 + 1)
    return b"".join(words)[:size]


def _small(wrong: bool) -> dict[str, tuple[File, bytes]]:
    files = {}
    for i in range(SMALL):
        data = _content(1 + i * 7919 % 20000, i)
        blob = Blob.zstd(data, digest=True)
        if wrong and i in (FIRST_WRONG, SECOND_WRONG):
            blob = Blob(blob.stored, blob.size, blob.codec, bytes(32))
        path = f"many/d{i // 100:02}/f{i:04}.txt"
        files[path] = (File(path, FileKind.RESOURCE, blob), data)
    return files


def _others() -> dict[str, tuple[File, bytes]]:
    files = {}
    for path, size, make, kind in [
        ("mid/m5.bin", 5 << 20, Blob.zstd, FileKind.RESOURCE),  # two threads' hashing alone
        ("mid/m8.bin", ZSTD_PIECE, Blob.zstd, FileKind.RESOURCE),  # the largest in one frame
        ("raw/r.bin", 200 << 10, Blob.raw, FileKind.RESOURCE),
        ("big/pieces.bin", ZSTD_PIECE + 1, Blob.zstd, FileKind.RESOURCE),
        ("big/lib.so", 100 << 10, Blob.xz, FileKind.NATIVE_LIB),
        ("big/libpieces.so", XZ_PIECE + 1, Blob.xz, FileKind.NATIVE_LIB),
    ]:
        data = _content(size, size)
        files[path] = (File(path, kind, make(data, digest=True), mode=0o755), data)
    return files


def _image(directory: Path, files: dict[str, tuple[File, bytes]]) -> Path:
    modules, sources = app_content({"x.py": "def main():\n    pass\n"})
    app = directory / "app"
    records = tuple(file for file, _ in files.values())
    build_executable(FAKE_RUNTIME, App("x", "x:main", modules=modules, files=(*sources, *records)),
                     app)  # fmt: skip
    return app


@pytest.fixture(scope="module")
def probe(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("probe") / "restore"
    flags = ("-Wl,--wrap=renameat,--wrap=linkat,--wrap=pthread_create",
             "-fsanitize=address,undefined",
             "-fno-sanitize-recover=undefined")  # fmt: skip
    names = ("cache.c", "way.c", "payload.c", "integrity.c", "parallel.c")
    return launcher_build.compile(out, [PROBE, *names], *flags)


@pytest.fixture(scope="module")
def files() -> dict[str, tuple[File, bytes]]:
    return {**_small(wrong=False), **_others()}


@pytest.fixture(scope="module")
def image(files: dict[str, tuple[File, bytes]], tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _image(tmp_path_factory.mktemp("image"), files)


def _restore(probe: Path, image: Path, root: Path, **env: str) -> dict[str, str]:
    result = subprocess.run([probe, image, root], capture_output=True, text=True, check=False,
                            env={**os.environ, **env}, timeout=300)  # fmt: skip
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr
    return dict(line.split(" ", 1) for line in result.stdout.splitlines())


def _site(root: Path) -> Path:
    return root / "runtime" / "g" / "site"


def _whole(root: Path, files: dict[str, tuple[File, bytes]]) -> list[str]:
    """The files not on disk as the payload has them."""
    return [path for path, (_, data) in files.items()
            if hashlib.sha256((_site(root) / path).read_bytes()).digest()
            != hashlib.sha256(data).digest()]  # fmt: skip


@pytest.fixture(scope="module")
def restored(probe: Path, image: Path, tmp_path_factory: pytest.TempPathFactory
             ) -> tuple[Path, dict[str, str]]:  # fmt: skip
    root = tmp_path_factory.mktemp("cache") / "root"
    return root, _restore(probe, image, root)


def test_every_file_is_written_whole(
    restored: tuple[Path, dict[str, str]], files: dict[str, tuple[File, bytes]]
) -> None:
    root, lines = restored

    assert lines["failure"] == "0 - 0"
    assert int(lines["written"]) == len(files) + 1  # and x.py's source
    assert _whole(root, files) == []


@pytest.mark.skipif(CORES < 2, reason="one usable core: no helper")
def test_small_files_are_written_on_the_helpers_too(restored: tuple[Path, dict[str, str]]) -> None:
    _, lines = restored
    writers = {lines[f"f{i:04}.txt"].split()[0] for i in range(SMALL)}

    assert writers == {"caller", "helper"}


def test_files_over_a_piece_or_in_xz_are_written_by_the_caller(
    restored: tuple[Path, dict[str, str]],
) -> None:
    _, lines = restored

    assert [lines[name].split()[0] for name in ALONE] == ["caller"] * len(ALONE)


def test_new_files_are_linked_into_place_and_nothing_is_left_behind(
    restored: tuple[Path, dict[str, str]], files: dict[str, tuple[File, bytes]]
) -> None:
    root, lines = restored
    if not _links_unnamed_files(root):
        pytest.skip("no O_TMPFILE or links on this file system")

    assert {lines[path.rpartition("/")[2]].split()[1] for path in files} == {"link"}
    assert os.listdir(root / "runtime" / "g" / ".scratch") == []


def test_changed_files_are_replaced_from_the_scratch_directory(
    probe: Path, image: Path, files: dict[str, tuple[File, bytes]], tmp_path: Path
) -> None:
    root = tmp_path / "root"
    _restore(probe, image, root)
    changed = ["many/d03/f0300.txt", "mid/m5.bin", "big/pieces.bin"]
    for path in changed:
        (_site(root) / path).chmod(0o600)
        (_site(root) / path).write_bytes(b"x" * len(files[path][1]))

    lines = _restore(probe, image, root)

    assert int(lines["written"]) == len(changed)  # renamed over: a link cannot replace
    assert [lines[path.rpartition("/")[2]].split()[1] for path in changed] == ["rename"] * 3
    assert _whole(root, files) == []


def test_where_no_link_can_be_made_files_are_renamed_into_place_written_once(
    probe: Path, image: Path, files: dict[str, tuple[File, bytes]], tmp_path: Path
) -> None:
    lines = _restore(probe, image, tmp_path / "root", PROBE_NO_LINK="1")

    assert lines["failure"] == "0 - 0"
    assert {lines[path.rpartition("/")[2]].split()[1] for path in files} == {"rename"}
    assert int(lines["links"]) <= 2 * 8  # two ways tried, by each writer at most, then none
    assert _whole(tmp_path / "root", files) == []


def test_a_link_through_proc_that_is_not_the_file_written_is_undone(
    probe: Path, image: Path, files: dict[str, tuple[File, bytes]], tmp_path: Path
) -> None:
    """Before Linux 6.10 an unnamed file is linked through /proc/self/fd, which a /proc that is
    no procfs (a chroot's, another user's) could point at a file of theirs."""
    decoy = tmp_path / "decoy"
    decoy.write_bytes(b"not the payload's")
    root = tmp_path / "root"

    lines = _restore(probe, image, root, PROBE_DECOY=str(decoy))

    assert lines["failure"] == "0 - 0"
    assert _whole(root, files) == []
    assert os.stat(decoy).st_nlink == 1  # linked nowhere in the cache
    assert {lines[path.rpartition("/")[2]].split()[1] for path in files} == {"rename"}


def test_never_more_than_8_threads_run_at_once(restored: tuple[Path, dict[str, str]]) -> None:
    _, lines = restored

    assert 1 <= int(lines["threads"]) <= CORES


def test_the_first_failure_in_path_order_is_reported(
    probe: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    files = _small(wrong=True)
    image = _image(tmp_path_factory.mktemp("wrong"), files)
    first = f"many/d{FIRST_WRONG // 100:02}/f{FIRST_WRONG:04}.txt"
    before = dict(list(files.items())[:FIRST_WRONG])

    for attempt in range(3):  # whichever thread meets which failure first
        root = tmp_path_factory.mktemp(f"wrong{attempt}") / "root"
        lines = _restore(probe, image, root)

        assert lines["failure"] == f"1 {first} 0"
        assert _whole(root, before) == []
        assert not (_site(root) / first).exists()


def test_with_too_few_descriptors_for_the_helpers_the_caller_writes_the_rest(
    probe: Path, image: Path, files: dict[str, tuple[File, bytes]], tmp_path: Path
) -> None:
    lines = _restore(probe, image, tmp_path / "root", PROBE_FREE="8")

    assert lines["failure"] == "0 - 0"
    assert _whole(tmp_path / "root", files) == []
