"""Phase 9, step 4 exit criterion: a project whose `requires-python` excludes the Python of the
running fsbpyr builds through that fsbpyr's dispatch to the variant of its own release.

The variant is a real one, fsbpyr built from a tagged copy of this checkout on another Python (a
few minutes, once per run) and served by a fake release server on localhost, never GitHub, with
sums signed by a test key. The fsbpyr that dispatches is a built release of the same copy, run as a
subprocess: only a built fsbpyr fetches a variant, since only it can check a signature. It needs
that Python installed with uv (`uv python install X.Y`); its runtime is built into the tool's
cache when the cache has none."""

import lzma
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from fsbpyr import dispatch
from fsbpyr.build import TARGET_PYTHON
from fsbpyr.cli import main
from fsbpyr.pyversion import supported
from fsbpyr.runtime import registry
from fsbpyr.targets import host_target
from fsbpyr.vcs import release_asset

from ..support import contain, downloads, requires
from ..support.releases import SUMS, FakeReleases, sums_of
from ..support.signing import PASSPHRASE, fingerprint, new_key, signed_as_release
from .conftest import DOWNLOADS_VARIABLE, ReleaseKeys, tagged_copy

pytestmark = [pytest.mark.slow, pytest.mark.version_rule]

ROOT = Path(__file__).parents[2]
OTHER = next(version for version in reversed(supported()) if version != TARGET_PYTHON)
TAG = "v1.2.0"
PACKED = f"{release_asset(OTHER, host_target().name)}.xz"
PATH = f"/{TAG}/{PACKED}"
CHECKED = [f"/{TAG}/{SUMS}", f"/{TAG}/{SUMS}.sig"]  # before the asset is fetched
BUILD_TIMEOUT_S = 1800
TIMEOUT = 300


@pytest.fixture(scope="module")
def copy(tmp_path_factory: pytest.TempPathFactory, release_keys: ReleaseKeys) -> Path:
    return tagged_copy(tmp_path_factory.mktemp("release-src"), TAG, release_keys.k.pub,
                       release_keys.r.pub)  # fmt: skip


@pytest.fixture(scope="module")
def own(tmp_path_factory: pytest.TempPathFactory, runtime: registry.Runtime, copy: Path) -> Path:
    """The fsbpyr that dispatches: a release of the copy, on the tests' Python."""
    out = tmp_path_factory.mktemp("own") / "fsbpyr"
    assert main(["build", str(copy), "-o", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def variant(tmp_path_factory: pytest.TempPathFactory, copy: Path) -> Path:
    """fsbpyr, built from the same copy by an fsbpyr running on the other Python."""
    uv = requires.tool("uv")
    requires.uv_python(OTHER)
    # the session's private cache has no runtime of the other Python: the kept one has, built
    # there once by that Python's fsbpyr (tests/support/downloads.py), else the real cache,
    # where one that is missing is built now
    kept = os.environ.get(DOWNLOADS_VARIABLE)
    cache = Path(kept) / "cache" if kept else registry.cache_home() / "fsbpyr"
    work = tmp_path_factory.mktemp("variant")
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(work / "venv"),
           "FSBPYR_CACHE_DIR": str(cache), "UV_PYTHON_DOWNLOADS": "never"}  # fmt: skip
    try:
        registry.find_runtime(OTHER, cache)
    except registry.RuntimeCacheError:
        _build_runtime(uv, env, cache)
    out = work / "fsbpyr"
    command = [uv, "run", "--project", str(ROOT), "--python", OTHER, "--locked", "fsbpyr",
               "build", str(copy), "--python", OTHER, "-o", str(out)]  # fmt: skip
    done = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True,
                          timeout=BUILD_TIMEOUT_S, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr[-3000:]
    return out


def _build_runtime(uv: str, env: dict[str, str], cache: Path) -> None:
    """The other Python's runtime, built into ``cache`` by fsbpyr running on that Python (one
    fsbpyr builds its own Python's runtime, as CI does); the runtime it replaces goes."""
    command = [uv, "run", "--project", str(ROOT), "--python", OTHER, "--locked", "fsbpyr",
               "runtime", "build", "--python", OTHER]  # fmt: skip
    done = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True,
                          timeout=BUILD_TIMEOUT_S, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr[-3000:]
    downloads.prune_runtimes(cache, registry.find_runtime(OTHER, cache).runtime_id)


@pytest.fixture(scope="module")
def signed_files(
    variant: Path, release_keys: ReleaseKeys, tmp_path_factory: pytest.TempPathFactory
) -> dict[str, bytes]:
    packed = lzma.compress(variant.read_bytes(), preset=0)
    sums = sums_of({PACKED: packed})
    signature = signed_as_release(release_keys.k.key, SUMS, sums, tmp_path_factory.mktemp("sums"))
    return {PATH: packed, f"/{TAG}/{SUMS}": sums, f"/{TAG}/{SUMS}.sig": signature}


@pytest.fixture
def releases(signed_files: dict[str, bytes]) -> Iterator[FakeReleases]:
    fake = FakeReleases()
    fake.files.update(signed_files)
    yield fake
    fake.close()


def _build(own: Path, source: Path, out: Path, base: str, cache: Path, *more: str | Path,
           **variables: str) -> subprocess.CompletedProcess[str]:  # fmt: skip
    """The released fsbpyr builds ``source``, which needs the other Python."""
    env = {**os.environ, dispatch.DOWNLOADS_VARIABLE: base, "FSBPYR_CACHE_DIR": str(cache),
           "FSBPYR_GC": "0", **variables}  # fmt: skip
    return contain.run([own, "build", source, "-o", out, *more], env=env, timeout=TIMEOUT)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "app"
    (project / "src" / "app").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        f'[project]\nname = "app"\nversion = "1.0"\nrequires-python = "=={OTHER}.*"\n'
        'dependencies = []\n[project.scripts]\napp = "app:main"\n'
        '[build-system]\nrequires = ["uv_build>=0.12.17,<0.13.0"]\nbuild-backend = "uv_build"\n'
    )
    (project / "src" / "app" / "__init__.py").write_text(
        "import sys\n\ndef main():\n    print('{}.{}'.format(*sys.version_info[:2]))\n"
    )
    return project


def _runs_on(executable: Path) -> str:
    done = subprocess.run([executable], capture_output=True, text=True, timeout=60, check=True)
    return done.stdout.strip()


def test_a_project_excluding_the_running_python_builds_through_the_variant(
    own: Path, releases: FakeReleases, tmp_path: Path
) -> None:
    project = _project(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    cache = tmp_path / "cache"

    done = _build(own, project, first, releases.base, cache)

    assert done.returncode == 0, done.stderr
    assert f"the project builds with Python {OTHER}" in done.stdout
    assert releases.requests == [*CHECKED, PATH]
    assert _runs_on(first) == OTHER  # not the running fsbpyr's Python
    releases.close()  # offline from here on: the cached variant builds

    done = _build(own, project, second, releases.base, cache)

    assert done.returncode == 0, done.stderr
    assert _runs_on(second) == OTHER


def test_a_script_whose_block_excludes_the_running_python_builds_through_the_variant(
    own: Path, releases: FakeReleases, tmp_path: Path
) -> None:
    script = tmp_path / "ops" / "which.py"
    script.parent.mkdir()
    script.write_text(f'# /// script\n# requires-python = "=={OTHER}.*"\n# ///\n'
                      "import sys\nprint('{}.{}'.format(*sys.version_info[:2]))\n")
    out = tmp_path / "out"

    done = _build(own, script, out, releases.base, tmp_path / "cache")

    assert done.returncode == 0, done.stderr
    assert f"the project builds with Python {OTHER}" in done.stdout
    assert _runs_on(out) == OTHER


def test_a_variant_signed_by_no_pinned_key_is_not_run(
    own: Path, releases: FakeReleases, tmp_path: Path, release_keys: ReleaseKeys
) -> None:
    sums = releases.files[f"/{TAG}/{SUMS}"]
    releases.files[f"/{TAG}/{SUMS}.sig"] = signed_as_release(release_keys.stray.key, SUMS, sums,
                                                             tmp_path / "sums")  # fmt: skip

    done = _build(own, _project(tmp_path), tmp_path / "out", releases.base, tmp_path / "cache")

    assert done.returncode == 1
    assert f"release {TAG} has no valid release signature" in done.stderr
    assert PATH not in releases.requests
    assert not (tmp_path / "out").exists()


def test_a_dispatched_build_is_signed_by_the_variant(
    own: Path, releases: FakeReleases, tmp_path: Path
) -> None:
    key, pub = new_key(tmp_path / "k")
    project = _project(tmp_path)
    out = tmp_path / "out"
    cache = tmp_path / "cache"

    done = _build(own, project, out, releases.base, cache, FSBPYR_SIGNING_KEY=str(key),
                  FSBPYR_SIGNING_PASSPHRASE=PASSPHRASE)  # fmt: skip

    assert done.returncode == 0, done.stderr
    assert done.stderr.count("is NOT SIGNED") == 0
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "config"), "FSBPYR_GC": "0"}
    saved = contain.run([own, "key", "trust", "app", pub, fingerprint(pub.read_bytes())], env=env,
                        timeout=TIMEOUT)  # fmt: skip
    assert saved.returncode == 0, saved.stderr
    checked = contain.run([own, "verify", "--project", "app", out], env=env, timeout=TIMEOUT)
    assert checked.returncode == 0, checked.stderr


def test_a_wrong_passphrase_gives_one_warning_and_a_build(
    own: Path, releases: FakeReleases, tmp_path: Path
) -> None:
    key, _ = new_key(tmp_path / "k")
    out = tmp_path / "out"

    variables = {"FSBPYR_SIGNING_KEY": str(key), "FSBPYR_SIGNING_PASSPHRASE": "wrong"}
    done = _build(own, _project(tmp_path), out, releases.base, tmp_path / "cache", **variables)

    assert done.returncode == 0, done.stderr
    assert done.stderr.count("is NOT SIGNED") == 1  # the variant's: the dispatcher opens no key
    assert out.exists() and not out.with_name("out.sig").exists()


def test_the_dispatchers_own_processes_get_no_signing_variable(
    own: Path, releases: FakeReleases, tmp_path: Path
) -> None:
    """For a directory of Python files it runs `git ls-files`, which runs the project's
    core.fsmonitor command, before the variant takes over."""
    requires.tool("git")
    project = tmp_path / "tools"
    project.mkdir()
    (project / "tool.py").write_text(
        f'# /// script\n# requires-python = "=={OTHER}.*"\n# ///\n'
        "if __name__ == '__main__':\n    print('tool')\n")
    seen = tmp_path / "seen"
    monitor = tmp_path / "monitor.sh"
    monitor.write_text(f'#!/bin/sh\nenv >> "{seen}"\n')
    monitor.chmod(0o755)
    git = ["git", "-C", str(project), "-c", "user.name=t", "-c", "user.email=t@example.org"]
    for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "x"],
                    ["config", "core.fsmonitor", str(monitor)]):  # fmt: skip
        subprocess.run([*git, *command], check=True, capture_output=True)
    key, _ = new_key(tmp_path / "k")

    variables = {"FSBPYR_SIGNING_KEY": str(key), "FSBPYR_SIGNING_PASSPHRASE": PASSPHRASE}
    done = _build(own, project, tmp_path / "out", releases.base, tmp_path / "cache", **variables)

    assert done.returncode == 0, done.stderr
    assert seen.is_file()  # the command ran
    assert "FSBPYR_SIGNING" not in seen.read_text() and PASSPHRASE not in seen.read_text()
