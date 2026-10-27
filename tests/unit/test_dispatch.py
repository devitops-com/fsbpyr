"""fsbpyr build for another Python than the running fsbpyr's: the variant of its own release,
fetched from a fake release server on localhost (nothing reaches GitHub)."""

import lzma
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from fsbpyr import dispatch
from fsbpyr.assemble import App, build_executable
from fsbpyr.cli import DEFAULT_PYTHON, main
from fsbpyr.config import load_config
from fsbpyr.project import Project
from fsbpyr.pyversion import supported
from fsbpyr.runtime import collect
from fsbpyr.targets import TARGETS, host_target
from fsbpyr.vcs import release_asset

from ..support.releases import FakeReleases
from .boot.conftest import FAKE_RUNTIME

pytestmark = pytest.mark.version_rule

OTHER = next(version for version in reversed(supported()) if version != DEFAULT_PYTHON)
HOST = host_target().name
ASSET = release_asset(OTHER, HOST)
PATH = f"/v1.2.0/{ASSET}.xz"


class Ran:
    """subprocess.run in dispatch, recorded: the stand-in variants are not runnable."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.status = 0


@pytest.fixture
def releases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeReleases]:
    fake = FakeReleases()
    monkeypatch.setenv(dispatch.DOWNLOADS_VARIABLE, fake.base)
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(dispatch, "own_version", lambda: "1.2.0-abcdef12")
    yield fake
    fake.close()


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch) -> Ran:
    record = Ran()

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        record.commands.append(command)
        return subprocess.CompletedProcess(command, record.status)

    monkeypatch.setattr(subprocess, "run", run)
    return record


@pytest.fixture
def project(tmp_path: Path) -> Path:
    directory = tmp_path / "app"
    directory.mkdir()
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "app"\nversion = "1"\nrequires-python = "=={OTHER}.*"\n'
    )
    return directory


def _release(tmp_path: Path) -> bytes:
    out = tmp_path / "variant"
    build_executable(FAKE_RUNTIME, App("fsbpyr", "fsbpyr.cli:main", dists=()), out)
    return out.read_bytes()


def _cached(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "cache" / "downloads").iterdir())


def test_a_project_for_another_python_runs_the_variant_with_the_same_arguments(
    releases: FakeReleases, ran: Ran, project: Path, tmp_path: Path,
    capsys: pytest.CaptureFixture[str]
) -> None:
    binary = _release(tmp_path)
    releases.files[PATH] = lzma.compress(binary)
    arguments = ["build", str(project), "-o", str(tmp_path / "out"), "--no-smoke", "--strict"]

    assert main(arguments) == 0

    [command] = ran.commands
    cached = tmp_path / "cache" / "downloads" / f"{ASSET}-v1.2.0"
    assert command == [str(cached), *arguments]
    assert cached.read_bytes() == binary
    assert cached.stat().st_mode & 0o777 == 0o755
    assert _cached(tmp_path) == [cached.name]  # no temporary file left
    assert releases.requests == [PATH]
    assert f"running {cached}" in capsys.readouterr().out


def test_the_url_and_the_cache_name_carry_the_hosts_target(
    releases: FakeReleases, ran: Ran, project: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    other = next(t for t in TARGETS if t.name != HOST)
    monkeypatch.setattr(dispatch, "host_target", lambda: other)
    asset = release_asset(OTHER, other.name)
    assert asset.endswith(f"-{other.name}") and asset != ASSET
    releases.files[f"/v1.2.0/{asset}.xz"] = lzma.compress(_release(tmp_path))

    assert main(["build", str(project)]) == 0

    assert releases.requests == [f"/v1.2.0/{asset}.xz"]
    assert _cached(tmp_path) == [f"{asset}-v1.2.0"]  # not the host's


def test_the_variants_exit_status_is_the_builds(
    releases: FakeReleases, ran: Ran, project: Path, tmp_path: Path
) -> None:
    releases.files[PATH] = lzma.compress(_release(tmp_path))
    ran.status = 1

    assert main(["build", str(project)]) == 1


def test_a_cached_variant_is_not_fetched_again_and_works_offline(
    releases: FakeReleases, ran: Ran, project: Path, tmp_path: Path
) -> None:
    releases.files[PATH] = lzma.compress(_release(tmp_path))
    assert main(["build", str(project)]) == 0
    releases.close()  # no network from here on

    assert main(["build", str(project), "-o", str(tmp_path / "again")]) == 0

    assert len(ran.commands) == 2
    assert releases.requests == [PATH]


def test_offline_runs_a_cached_variant_without_the_network(
    releases: FakeReleases, ran: Ran, project: Path, tmp_path: Path
) -> None:
    releases.files[PATH] = lzma.compress(_release(tmp_path))
    assert main(["build", str(project)]) == 0
    releases.close()

    assert main(["build", str(project), "--offline"]) == 0

    assert ran.commands[1][1:] == ["build", str(project), "--offline"]
    assert releases.requests == [PATH]


def test_offline_does_not_fetch_a_variant_and_says_how_to_get_it(
    releases: FakeReleases, ran: Ran, project: Path, tmp_path: Path,
    capsys: pytest.CaptureFixture[str]
) -> None:
    releases.files[PATH] = lzma.compress(_release(tmp_path))

    assert main(["build", str(project), "--offline"]) == 1

    err = capsys.readouterr().err
    assert f"needs {ASSET} of release v1.2.0" in err and "--offline" in err
    assert "without --offline" in err
    assert releases.requests == [] and ran.commands == []
    assert not (tmp_path / "cache" / "downloads").exists() or _cached(tmp_path) == []


def test_glibc_reaches_the_variant_from_the_command_line_and_from_the_project(
    releases: FakeReleases, ran: Ran, project: Path, tmp_path: Path
) -> None:
    """The variant gets this command line as it is and reads the same pyproject, so both ways
    of choosing the glibc (D46) work for a project of another Python."""
    releases.files[PATH] = lzma.compress(_release(tmp_path))
    assert main(["build", str(project), "--glibc", "2.28"]) == 0
    assert ran.commands[0][1:] == ["build", str(project), "--glibc", "2.28"]

    with (project / "pyproject.toml").open("a") as file:
        file.write('[tool.fsbpyr]\nglibc = "2.28"\n')

    assert main(["build", str(project)]) == 0
    assert ran.commands[1][1:] == ["build", str(project)]
    assert load_config(Project.load(project)).glibc == "2.28"


def test_an_unknown_glibc_in_the_project_stops_before_the_variant_is_fetched(
    releases: FakeReleases, ran: Ran, project: Path, tmp_path: Path,
    capsys: pytest.CaptureFixture[str]
) -> None:
    releases.files[PATH] = lzma.compress(_release(tmp_path))
    with (project / "pyproject.toml").open("a") as file:
        file.write('[tool.fsbpyr]\nglibc = "2.17"\n')

    assert main(["build", str(project)]) == 1

    assert "supported: 2.34, 2.28" in capsys.readouterr().err
    assert releases.requests == [] and ran.commands == []


def test_python_on_the_command_line_picks_the_variant(
    releases: FakeReleases, ran: Ran, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1"\n')
    releases.files[PATH] = lzma.compress(_release(tmp_path))

    assert main(["build", str(tmp_path), "--python", OTHER]) == 0

    assert ran.commands[0][1:] == ["build", str(tmp_path), "--python", OTHER]


def test_the_configured_python_picks_the_variant(
    releases: FakeReleases, ran: Ran, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "app"\nversion = "1"\n[tool.fsbpyr]\npython = "{OTHER}"\n'
    )
    releases.files[PATH] = lzma.compress(_release(tmp_path))

    assert main(["build", str(tmp_path)]) == 0
    assert len(ran.commands) == 1


def test_the_running_fsbpyrs_own_python_is_not_dispatched(
    releases: FakeReleases, ran: Ran, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "pyproject.toml").write_text(
        (project / "pyproject.toml").read_text().replace(f"=={OTHER}.*", ">=3.12")
    )
    monkeypatch.setattr("fsbpyr.build.build", lambda *a, **k: pytest.fail("builds in-process"))

    with pytest.raises(pytest.fail.Exception, match="builds in-process"):
        main(["build", str(project), "--python", DEFAULT_PYTHON])

    assert ran.commands == [] and releases.requests == []


def test_a_variant_that_cannot_be_fetched_is_an_error_and_leaves_nothing(
    releases: FakeReleases, ran: Ran, project: Path, tmp_path: Path,
    capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["build", str(project)]) == 1  # the server has no such file: 404

    err = capsys.readouterr().err
    assert f"needs {ASSET} of release v1.2.0" in err
    assert "could not be fetched" in err
    assert ran.commands == [] and _cached(tmp_path) == []


def test_a_download_that_fails_verification_is_not_kept_or_run(
    releases: FakeReleases, ran: Ran, project: Path, tmp_path: Path,
    capsys: pytest.CaptureFixture[str]
) -> None:
    binary = _release(tmp_path)
    releases.files[PATH] = lzma.compress(binary[:-1000])  # truncated before it was compressed

    assert main(["build", str(project)]) == 1

    assert "could not be fetched" in capsys.readouterr().err
    assert ran.commands == [] and _cached(tmp_path) == []


def test_a_variant_needing_a_newer_glibc_than_the_host_is_not_kept_or_run(
    releases: FakeReleases, ran: Ran, project: Path, tmp_path: Path,
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "variant"
    build_executable(FAKE_RUNTIME, App("fsbpyr", "fsbpyr.cli:main", dists=(),
                                       build_info=(("glibc", "2.34"),)), out)  # fmt: skip
    releases.files[PATH] = lzma.compress(out.read_bytes())
    monkeypatch.setattr(os, "confstr", lambda name: "glibc 2.28")

    assert main(["build", str(project)]) == 1

    assert "could not be fetched" in capsys.readouterr().err
    assert ran.commands == [] and _cached(tmp_path) == []


def test_a_truncated_download_is_not_kept_or_run(
    releases: FakeReleases, ran: Ran, project: Path, tmp_path: Path
) -> None:
    releases.files[PATH] = lzma.compress(_release(tmp_path))[:-100]

    assert main(["build", str(project)]) == 1
    assert ran.commands == [] and _cached(tmp_path) == []


def test_a_developer_build_never_fetches_a_variant(
    releases: FakeReleases, ran: Ran, project: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    releases.files[PATH] = lzma.compress(_release(tmp_path))
    monkeypatch.setattr(dispatch, "own_version", lambda: "untagged-abcdef12")

    assert main(["build", str(project)]) == 1

    err = capsys.readouterr().err
    assert "developer build" in err
    assert f"make build PYTHON={OTHER}" in err
    assert f"dist/fsbpyr-{OTHER}" in err
    assert releases.requests == [] and ran.commands == []
    assert not (tmp_path / "cache").exists()


def test_a_variant_that_will_not_start_is_an_error(
    releases: FakeReleases, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    releases.files[PATH] = lzma.compress(_release(tmp_path))  # an ELF stand-in: not runnable

    assert main(["build", str(project)]) == 1

    assert "cannot run" in capsys.readouterr().err


def test_a_project_no_supported_python_fits_stops_before_any_fetch(
    releases: FakeReleases, ran: Ran, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "1"\nrequires-python = "<3.12"\n'
    )

    assert main(["build", str(project)]) == 1

    assert "allows none of the Python versions fsbpyr builds for (3.12, 3.13, 3.14 and 3.15)" in (
        capsys.readouterr().err)
    assert releases.requests == [] and ran.commands == []


def test_a_cached_variants_name_is_not_one_the_cache_collector_removes() -> None:
    for version in supported():
        for target in TARGETS:
            name = f"{release_asset(version, target.name)}-v1.2.0"
            assert not collect.NAMES[collect.DOWNLOADS].fullmatch(name)
