"""tests/support/downloads.py: the pinned downloads one e2e session keeps for the next."""

import hashlib
from pathlib import Path

from ..support import downloads

NAMES = {"a.tar.zst": b"archive", "b.deb": b"package"}
PINNED = {name: hashlib.sha256(data).hexdigest() for name, data in NAMES.items()}


def _directory(root: Path, **files: bytes) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (root / name.replace("_", ".")).write_bytes(data)
    return root


def test_a_session_is_seeded_with_the_pinned_files_the_directory_holds(tmp_path: Path) -> None:
    keep = _directory(tmp_path / "keep", **{"a_tar_zst": b"archive"})
    cache = tmp_path / "cache"

    status = downloads.seed(PINNED, keep, cache)

    assert status == {"a.tar.zst": "seeded", "b.deb": "fetched"}
    assert (cache / "downloads" / "a.tar.zst").read_bytes() == b"archive"
    assert not (cache / "downloads" / "b.deb").exists()


def test_a_file_that_does_not_match_its_pin_is_not_seeded(tmp_path: Path) -> None:
    keep = _directory(tmp_path / "keep", **{"a_tar_zst": b"tampered"})

    status = downloads.seed(PINNED, keep, tmp_path / "cache")

    assert status["a.tar.zst"] == "fetched"
    assert not (tmp_path / "cache" / "downloads" / "a.tar.zst").exists()


def test_without_a_directory_everything_is_fetched(tmp_path: Path) -> None:
    status = downloads.seed(PINNED, None, tmp_path / "cache")

    assert status == {"a.tar.zst": "fetched", "b.deb": "fetched"}


def test_a_session_keeps_the_verified_files_it_fetched(tmp_path: Path) -> None:
    _directory(tmp_path / "cache" / "downloads", **{"a_tar_zst": b"archive", "b_deb": b"corrupt"})
    keep = tmp_path / "keep"

    downloads.keep(PINNED, keep, tmp_path / "cache")

    assert (keep / "a.tar.zst").read_bytes() == b"archive"
    assert not (keep / "b.deb").exists()


def test_a_second_session_finds_what_the_first_kept(tmp_path: Path) -> None:
    _directory(tmp_path / "cache1" / "downloads", **{"a_tar_zst": b"archive", "b_deb": b"package"})
    keep = tmp_path / "keep"
    downloads.keep(PINNED, keep, tmp_path / "cache1")

    status = downloads.seed(PINNED, keep, tmp_path / "cache2")

    assert set(status.values()) == {"seeded"}
