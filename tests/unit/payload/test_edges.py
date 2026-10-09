"""The Python reader on every edge case of the format (D38, tests/support/edges.py): it refuses
what it should with PayloadError (CacheError or OSError where the cache refuses), reads the rest,
and never fails any other way."""

from pathlib import Path

import pytest

from fsbpyr.payload import writer
from fsbpyr.payload.format import FileKind
from fsbpyr.payload.format import PayloadError as ReaderError
from fsbpyr.payload.spec import Blob, File

from ...support import edges, walk
from ...support.standin import StandIn
from ..boot.conftest import FAKE_RUNTIME


@pytest.fixture(scope="module")
def intact(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    return edges.build(FAKE_RUNTIME, tmp_path_factory.mktemp("edges") / "edge").read_bytes()


def _refused(path: Path) -> bool:
    try:
        standin = StandIn(path)  # the reader checks every table when it opens the file
    except ReaderError:
        return True
    try:
        return edges.refused(walk.run(standin))
    finally:
        standin.close()


def test_the_intact_executable_is_read_without_a_refusal(intact: bytes, tmp_path: Path) -> None:
    (tmp_path / "edge").write_bytes(intact)

    assert not _refused(tmp_path / "edge")


@pytest.mark.parametrize("case", edges.CASES, ids=lambda case: case.id)
def test_the_reader_refuses_or_reads_each_edge_case(
    intact: bytes, case: edges.Case, tmp_path: Path
) -> None:
    (tmp_path / "edge").write_bytes(edges.apply(case, intact))

    assert _refused(tmp_path / "edge") is case.reader


@pytest.mark.parametrize("case", edges.PATH_CASES, ids=lambda case: case.id)
def test_the_reader_refuses_paths_a_writer_never_writes(
    case: edges.PathCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(writer, "check_path", lambda path: None)  # build what must be refused
    native = File(case.path, FileKind.NATIVE_LIB, Blob.zstd(b"\x7fELF p" * 99, digest=True),
                  mode=0o755)  # fmt: skip
    edges.build(FAKE_RUNTIME, tmp_path / "edge", files=(native,))

    assert _refused(tmp_path / "edge") is case.reader
