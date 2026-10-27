"""tests/support/downloads.py: the pinned downloads one e2e session keeps for the next."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fsbpyr.runtime import pbs, pins, registry

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


def _runtime_directory(cache: Path, name: str, baseline: str | None) -> Path:
    directory = cache / "runtimes" / name
    directory.mkdir(parents=True)
    meta = {} if baseline is None else {"baseline": baseline}
    (directory / "runtime.json").write_text(json.dumps(meta))
    return directory


def test_pruning_a_runtime_spares_the_other_glibc_baselines(tmp_path: Path) -> None:
    triple = "x86_64-unknown-linux-gnu"
    default = _runtime_directory(tmp_path, f"cp314-{triple}-aaaaaaaaaaaa", "2.34")
    old = _runtime_directory(tmp_path, f"cp314-{triple}-bbbbbbbbbbbb", "2.34")
    unrecorded = _runtime_directory(tmp_path, f"cp314-{triple}-cccccccccccc", None)  # the default's
    el8 = _runtime_directory(tmp_path, f"cp314-{triple}-dddddddddddd", "2.28")
    other_python = _runtime_directory(tmp_path, f"cp313-{triple}-eeeeeeeeeeee", "2.34")

    downloads.prune_runtimes(tmp_path, default.name)

    assert not old.exists() and not unrecorded.exists()  # replaced by the current one
    assert default.exists() and el8.exists() and other_python.exists()

    downloads.prune_runtimes(tmp_path, f"cp314-{triple}-ffffffffffff", "2.28")

    assert not el8.exists() and default.exists()


def test_a_pbs_distribution_comes_from_the_kept_cache_and_its_archive_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = SimpleNamespace(name="cpython-x", sha256=PINNED["a.tar.zst"])
    fetched: list[Path] = []

    def fake_fetch(given: object, cache: Path) -> str:
        assert given is pin
        (cache / "downloads").mkdir(parents=True, exist_ok=True)
        (cache / "downloads" / f"{pin.name}.tar.zst").write_bytes(b"archive")
        fetched.append(cache)
        return "distribution"

    monkeypatch.setattr(pins, "pin_for", lambda python, triple: pin)
    monkeypatch.setattr(registry, "host_triple", lambda: "x86_64-unknown-linux-gnu")
    monkeypatch.setattr(pbs, "fetch", fake_fetch)
    monkeypatch.setenv("FSBPYR_TEST_DOWNLOADS", str(tmp_path / "keep"))

    distribution: object = downloads.pbs_distribution("3.14")

    assert distribution == "distribution"
    assert fetched == [tmp_path / "keep" / "cache"]
    assert (tmp_path / "keep" / "cpython-x.tar.zst").read_bytes() == b"archive"
