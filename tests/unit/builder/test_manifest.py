"""build_info's record of the lock and of each distribution (REP-3)."""

import dataclasses
import hashlib
import shutil
from pathlib import Path

import pytest

from fsbpyr.build import settings_record
from fsbpyr.closure import SiteIndex
from fsbpyr.config import BuildConfig
from fsbpyr.manifest import (
    BUILT_FROM_SDIST,
    LOCAL,
    LockError,
    built_here,
    dist_record,
    dist_records,
    lock_record,
    read_lock,
)
from fsbpyr.project import LockSource

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"
H1, H2, H3 = ("a" * 64, "b" * 64, "c" * 64)
ANY = "py3-none-any"
LINUX = "cp314-cp314-manylinux_2_28_x86_64"

UV_LOCK = f"""\
version = 1

[[package]]
name = "pure"
version = "1.0"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ url = "https://h/pure-1.0.tar.gz", hash = "sha256:{H1}" }}
wheels = [{{ url = "https://h/pure-1.0-{ANY}.whl", hash = "sha256:{H2}" }}]

[[package]]
name = "native"
version = "2.0"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ url = "https://h/native-2.0.tar.gz", hash = "sha256:{H1}" }}
wheels = [
    {{ url = "https://h/native-2.0-{LINUX}.whl", hash = "sha256:{H3}" }},
]

[[package]]
name = "only-sdist"
version = "3.0"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ url = "https://h/only_sdist-3.0.tar.gz", hash = "sha256:{H1}" }}

[[package]]
name = "from-git"
version = "4.0"
source = {{ git = "https://example.invalid/repo.git?rev=abc#abc" }}

[[package]]
name = "member"
version = "0.1"
source = {{ editable = "member" }}

[[package]]
name = "app"
version = "1.0"
source = {{ virtual = "." }}
"""


def _install(site: Path, name: str, version: str, *tags: str, generator: str = "x") -> None:
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n")
    wheel = [f"Generator: {generator}", "Root-Is-Purelib: true", *(f"Tag: {t}" for t in tags)]
    (info / "WHEEL").write_text("\n".join(wheel) + "\n")


@pytest.fixture
def site(tmp_path: Path) -> Path:
    return tmp_path / "site"


def _records(site: Path, lock: str = UV_LOCK, project: str = "app") -> dict[str, dict[str, object]]:
    packages = read_lock(LockSource.UV_LOCK, lock)
    index = SiteIndex.scan(site)
    found = dist_records(index.dists.values(), packages, project=project)
    return {str(r["name"]): r for r in found}


def test_a_dist_matching_a_wheel_of_the_lock_has_that_wheels_hash(site: Path) -> None:
    _install(site, "pure", "1.0", ANY)
    _install(site, "native", "2.0", LINUX, "cp314-cp314-manylinux2014_x86_64")

    found = _records(site)

    assert found["pure"] == {"name": "pure", "version": "1.0", "source": "registry",
                             "artifact_sha256": H2}  # fmt: skip
    assert found["native"]["artifact_sha256"] == H3  # not the sdist's, though the lock has it


def test_a_pure_python_dist_matching_no_wheel_was_built_from_its_sdist(site: Path) -> None:
    """The lock lists pure's py3-none-any wheel, and also wheels for other tags: an installed
    dist that shares none of them was built here, a pure-Python one too."""
    _install(site, "pure", "1.0", "py2-none-any", generator="setuptools (80.1.0)")
    _install(site, "native", "2.0", "cp314-cp314-linux_x86_64")

    found = _records(site)

    assert found["pure"] == {"name": "pure", "version": "1.0", "source": BUILT_FROM_SDIST,
                             "artifact_sha256": H1, "backend": "setuptools (80.1.0)"}  # fmt: skip
    assert found["native"]["source"] == BUILT_FROM_SDIST
    assert built_here(found.values()) == ["native", "pure"]


def test_a_package_the_lock_has_only_an_sdist_for_was_built_from_it(site: Path) -> None:
    _install(site, "only-sdist", "3.0", ANY)

    assert _records(site)["only-sdist"]["source"] == BUILT_FROM_SDIST


def test_local_dists_carry_no_hash(site: Path) -> None:
    _install(site, "app", "1.0", ANY)  # the project: its wheel is built at staging
    _install(site, "member", "0.1", ANY)  # an editable member of the workspace
    _install(site, "from-git", "4.0", "cp314-cp314-linux_x86_64")

    found = _records(site)

    assert found["app"] == {"name": "app", "version": "1.0", "source": LOCAL}
    assert found["member"] == {"name": "member", "version": "0.1", "source": LOCAL}
    assert found["from-git"] == {"name": "from-git", "version": "4.0", "source": "git"}


def test_the_project_is_local_whatever_the_lock_says(site: Path) -> None:
    _install(site, "pure", "1.0", ANY)

    assert _records(site, project="pure")["pure"] == {"name": "pure", "version": "1.0",
                                                      "source": LOCAL}  # fmt: skip


@pytest.mark.parametrize(
    ("direct_url", "source"),
    [
        (None, "registry"),
        ({"url": "file:///x", "dir_info": {}}, "path"),
        ({"url": "https://h/x.whl", "archive_info": {}}, "url"),
        ({"url": "https://h/x.git", "vcs_info": {"vcs": "git"}}, "git"),
    ],
)
def test_a_dist_the_lock_does_not_list_is_sorted_by_where_it_came_from(
    site: Path, direct_url: dict[str, object] | None, source: str
) -> None:
    _install(site, "other", "1.0", ANY)
    dist = SiteIndex.scan(site).dists["other"]
    dist = dataclasses.replace(dist, direct_url=direct_url)

    assert dist_record(dist, {}, project="app") == {"name": "other", "version": "1.0",
                                                    "source": source}  # fmt: skip


PYLOCK = f"""\
lock-version = "1.0"

[[packages]]
name = "pure"
version = "1.0"
sdist = {{ name = "pure-1.0.tar.gz", hashes = {{ sha256 = "{H1}" }} }}
wheels = [{{ name = "pure-1.0-{ANY}.whl", hashes = {{ sha256 = "{H2}" }} }}]

[[packages]]
name = "from-url"
version = "2.0"
archive = {{ url = "https://h/x.whl", hashes = {{ sha256 = "{H3}" }} }}
"""


def test_pylock_wheels_are_matched_the_same_way(site: Path) -> None:
    _install(site, "pure", "1.0", ANY)
    packages = read_lock(LockSource.PYLOCK, PYLOCK)

    found = dist_records(SiteIndex.scan(site).dists.values(), packages, project="app")

    assert found[0]["artifact_sha256"] == H2 and packages["from-url"][0].kind == "url"


def _lock_files(directory: Path) -> None:
    shutil.copy(FIXTURES / "compat" / "uv.lock", directory / "uv.lock")


def test_the_lock_is_recorded_with_its_hash_and_a_path_relative_to_the_project(
    tmp_path: Path,
) -> None:
    _lock_files(tmp_path)

    record, packages = lock_record(tmp_path, LockSource.UV_LOCK, tmp_path / "uv.lock")

    assert record == {
        "kind": "uv.lock",
        "path": "uv.lock",
        "sha256": hashlib.sha256((tmp_path / "uv.lock").read_bytes()).hexdigest(),
        "hashed": True,
    }
    assert "click" in packages


def test_a_workspace_lock_is_named_relative_to_the_member(tmp_path: Path) -> None:
    member = tmp_path / "packages" / "app"
    member.mkdir(parents=True)
    _lock_files(tmp_path)

    record, _ = lock_record(member, LockSource.UV_LOCK, tmp_path / "uv.lock")

    assert record["path"] == "../../uv.lock"


def test_an_unlocked_build_records_no_lock_file(tmp_path: Path) -> None:
    assert lock_record(tmp_path, LockSource.UNLOCKED, None)[0] == {
        "kind": "unlocked", "path": None, "sha256": None, "hashed": False}  # fmt: skip


@pytest.mark.parametrize(("text", "hashed"), [("click==8.1.8 --hash=sha256:abc\n", True),
                                              ("click==8.1.8\n", False)])  # fmt: skip
def test_requirements_are_hashed_when_they_carry_hashes(
    tmp_path: Path, text: str, hashed: bool
) -> None:
    (tmp_path / "requirements.txt").write_text(text)

    record, packages = lock_record(tmp_path, LockSource.REQUIREMENTS,
                                   tmp_path / "requirements.txt")  # fmt: skip

    assert (record["hashed"], packages) == (hashed, {})


def test_an_unreadable_or_malformed_lock_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(LockError, match="cannot read"):
        lock_record(tmp_path, LockSource.UV_LOCK, tmp_path / "uv.lock")
    (tmp_path / "uv.lock").write_text("[[package\n")
    with pytest.raises(LockError, match=r"uv\.lock"):
        lock_record(tmp_path, LockSource.UV_LOCK, tmp_path / "uv.lock")


def test_identical_inputs_give_identical_records_from_two_directories(tmp_path: Path) -> None:
    records = []
    for name in ("first", "second"):
        project = tmp_path / name / "work" / "app"
        project.mkdir(parents=True)
        (project / "uv.lock").write_text(UV_LOCK)
        _install(tmp_path / name / "site", "pure", "1.0", ANY)
        _install(tmp_path / name / "site", "native", "2.0", "cp314-cp314-linux_x86_64")
        _install(tmp_path / name / "site", "app", "1.0", ANY)
        lock, packages = lock_record(project, LockSource.UV_LOCK, project / "uv.lock")
        index = SiteIndex.scan(tmp_path / name / "site")
        found = dist_records(index.dists.values(), packages, project="app")
        records.append((lock, found))

    assert records[0] == records[1]


def test_every_locked_dist_of_the_compat_fixture_traces_to_the_lock(site: Path) -> None:
    """With each package installed from its first wheel, every dist but the project has an
    artifact hash that appears in uv.lock."""
    text = (FIXTURES / "compat" / "uv.lock").read_text()
    packages = read_lock(LockSource.UV_LOCK, text)
    for name, [package] in packages.items():
        if package.wheels and package.version:
            wheel = sorted(package.wheels)[0]
            tag = wheel.removesuffix(".whl").split("-", 2)[2]
            _install(site, name, package.version, tag)

    found = dist_records(SiteIndex.scan(site).dists.values(), packages, project="compat")

    assert found
    assert all(r["source"] == "registry" and str(r["artifact_sha256"]) in text for r in found)


def test_settings_record_the_effective_configuration() -> None:
    config = BuildConfig(entry="tool", extras=("b", "a"), allowed_system_libs=("libz.so.1",),
                         fs_packages=("zeta", "babel"), include_source=False)  # fmt: skip

    settings = settings_record(config)

    assert settings["entry"] == "tool" and settings["extras"] == ["a", "b"]
    assert settings["include_source"] is False and settings["allow_system_libs"] == ["libz.so.1"]
    assert settings["fs_packages"] == sorted({"babel", "botocore", "certifi", "litellm",
                                              "matplotlib", "torch", "zeta"})  # fmt: skip
    assert settings_record(BuildConfig())["entry"] is None
