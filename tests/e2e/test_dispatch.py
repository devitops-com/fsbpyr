"""Phase 9, step 4 exit criterion: a project whose `requires-python` excludes the Python of the
running fsbpyr builds through that fsbpyr's dispatch to the variant of its own release.

The variant is a real one, fsbpyr built from this checkout on another Python (a few minutes,
once per run) and served by a fake release server on localhost, never GitHub. It needs that
Python installed with uv and its runtime in the tool's cache (`fsbpyr runtime build --python
X.Y`)."""

import lzma
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from fsbpyr import dispatch
from fsbpyr.build import TARGET_PYTHON
from fsbpyr.cli import main
from fsbpyr.pyversion import supported
from fsbpyr.runtime import registry
from fsbpyr.vcs import release_asset

from ..support.releases import FakeReleases

pytestmark = [pytest.mark.slow, pytest.mark.version_rule]

ROOT = Path(__file__).parents[2]
UV = shutil.which("uv")
OTHER = next(version for version in reversed(supported()) if version != TARGET_PYTHON)
PATH = f"/v1.2.0/{release_asset(OTHER)}.xz"
BUILD_TIMEOUT_S = 1800


@pytest.fixture(scope="module")
def variant(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """fsbpyr, built from this checkout by an fsbpyr running on the other Python."""
    if UV is None:
        pytest.skip("needs uv")
    found = subprocess.run([UV, "python", "find", "--no-python-downloads", OTHER],
                           capture_output=True, check=False)  # fmt: skip
    if found.returncode != 0:
        pytest.skip(f"needs Python {OTHER} installed with uv")
    # the real cache: the session's private one has no runtime of the other Python
    cache = registry.cache_home() / "fsbpyr"
    try:
        registry.find_runtime(OTHER, cache)
    except registry.RuntimeCacheError:
        pytest.skip(f"needs the Python {OTHER} runtime in the cache (fsbpyr runtime build)")
    work = tmp_path_factory.mktemp("variant")
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(work / "venv"),
           "FSBPYR_CACHE_DIR": str(cache), "UV_PYTHON_DOWNLOADS": "never"}  # fmt: skip
    out = work / "fsbpyr"
    command = [UV, "run", "--project", str(ROOT), "--python", OTHER, "--locked", "fsbpyr",
               "build", str(ROOT), "--python", OTHER, "-o", str(out)]  # fmt: skip
    done = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True,
                          timeout=BUILD_TIMEOUT_S, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr[-3000:]
    return out


@pytest.fixture
def releases(
    variant: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FakeReleases]:
    fake = FakeReleases()
    fake.files[PATH] = lzma.compress(variant.read_bytes())
    monkeypatch.setenv(dispatch.DOWNLOADS_VARIABLE, fake.base)
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(dispatch, "own_version", lambda: "1.2.0-abcdef12")
    yield fake
    fake.close()


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
    releases: FakeReleases, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"

    assert main(["build", str(project), "-o", str(first)]) == 0

    out = capsys.readouterr().out
    assert f"the project builds with Python {OTHER}" in out
    assert releases.requests == [PATH]
    assert _runs_on(first) == OTHER  # not the running fsbpyr's Python
    releases.close()  # offline from here on: the cached variant builds

    assert main(["build", str(project), "-o", str(second)]) == 0

    assert releases.requests == [PATH]
    assert _runs_on(second) == OTHER
