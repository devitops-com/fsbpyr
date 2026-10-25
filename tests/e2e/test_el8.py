"""`fsbpyr build --glibc 2.28` (EL8, D46) on real runtimes: the stub linked against Rocky Linux
8.10's sysroot, wheels of the manylinux_2_28 policy, the app run on the host and in EL8
containers; and a release fsbpyr (an executable of this checkout, tagged v1.2.0) that pairs its
own stdlib with the EL8 stub of its release, served by a fake release server or placed beside
the executable.

The EL8 containers are in tests/e2e/test_portability.py, which runs podman."""

import contextlib
import hashlib
import io
import json
import lzma
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from fsbpyr import dispatch
from fsbpyr.assemble import stub_digest
from fsbpyr.build import TARGET_PYTHON
from fsbpyr.cli import main
from fsbpyr.payload.reader import Executable
from fsbpyr.runtime import pins, registry, stubs
from fsbpyr.runtime.stub import stub_identity
from fsbpyr.targets import host_target
from fsbpyr.vcs import release_stub_asset, release_stub_sums

from ..support import contain
from ..support.releases import FakeReleases

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "projects"
BASELINE = "2.28"
ASSET = release_stub_asset(TARGET_PYTHON, host_target().name, BASELINE)
SUMS = release_stub_sums(BASELINE)
TAG = "v1.2.0"
CRYPTOGRAPHY = "50.0.2"
_MANYLINUX = re.compile(r"manylinux_(\d+)_(\d+)_")
_LEGACY = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}
BUILD_TIMEOUT_S = 900
NATIVE_PYPROJECT = f"""\
[project]
name = "el8native"
version = "1.0"
requires-python = ">=3.12"
dependencies = ["cryptography=={CRYPTOGRAPHY}"]

[project.scripts]
el8native = "el8native:main"

[build-system]
requires = ["uv_build>=0.12.17,<0.14.0"]
build-backend = "uv_build"
"""
NATIVE_SOURCE = """\
def main():
    import cryptography
    from cryptography.hazmat.primitives import hashes

    digest = hashes.Hash(hashes.SHA256())
    digest.update(b"el8")
    print(digest.finalize().hex()[:16], cryptography.__version__)
"""
NATIVE_OUTPUT = f"{hashlib.sha256(b'el8').hexdigest()[:16]} {CRYPTOGRAPHY}"
HELLO_OUTPUT = "hello ['5', 'x']"


def _build(project: Path, out: Path, *options: str) -> str:
    """`fsbpyr build` in-process, which must succeed; what it printed."""
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        status = main(["build", str(project), "-o", str(out), *options])
    assert status == 0, printed.getvalue()
    return printed.getvalue()


def _info(app: Path) -> dict[str, object]:
    with Executable.open(app) as executable:
        info: dict[str, object] = json.loads(executable.payload.build_info)
    return info


def assert_same_app(made: Path, expected: Path) -> None:
    """``made`` is ``expected``'s app: the same stub and the same build information but for the
    builder's version (the one is built by a tagged fsbpyr executable, the other by the tests)."""
    with Executable.open(made) as one, Executable.open(expected) as other:
        assert one.stub_prefix() == other.stub_prefix()
    info, wanted = _info(made), _info(expected)
    assert info.pop("fsbpyr_version") != wanted.pop("fsbpyr_version")
    assert info == wanted


def _hello(factory: pytest.TempPathFactory, name: str) -> Path:
    project = factory.mktemp(name) / "hello"
    shutil.copytree(FIXTURES / "hello", project)
    return project


def native_project(factory: pytest.TempPathFactory) -> Path:
    project = factory.mktemp("el8native-src") / "el8native"
    (project / "src" / "el8native").mkdir(parents=True)
    (project / "pyproject.toml").write_text(NATIVE_PYPROJECT)
    (project / "requirements.txt").write_text(f"cryptography=={CRYPTOGRAPHY}\n")
    (project / "src" / "el8native" / "__init__.py").write_text(NATIVE_SOURCE)
    return project


def wheel_tags(app: Path, dist: str) -> list[str]:
    """The platform tags of the wheel ``dist`` was installed from, read from its WHEEL file."""
    with Executable.open(app) as executable:
        payload = executable.payload
        record = next(f for f in payload.files()
                      if f.path.startswith(f"{dist}-") and f.path.endswith(".dist-info/WHEEL"))
        text = payload.read_blob(record.blob).decode()
    return [line.partition(":")[2].strip() for line in text.splitlines() if line.startswith("Tag:")]


def glibc_of(tag: str) -> tuple[int, int] | None:
    """The glibc a wheel tag asks for: the highest manylinux among its dotted platforms."""
    found = []
    for platform in tag.rpartition("-")[2].split("."):
        if match := _MANYLINUX.match(platform):
            found.append((int(match[1]), int(match[2])))
        elif legacy := _LEGACY.get(platform.partition("_")[0]):
            found.append(legacy)
    return max(found, default=None)


@pytest.fixture(scope="module")
def built(
    tmp_path_factory: pytest.TempPathFactory, runtime: registry.Runtime,
    el8_runtime: registry.Runtime
) -> dict[str, Path]:
    """The fixtures built for glibc 2.28 and, to compare, for the default."""
    out = tmp_path_factory.mktemp("apps")
    native = native_project(tmp_path_factory)
    apps = {name: out / name for name in ("hello", "hello-default", "native", "native-default")}
    apps["summary"] = out / "summary.txt"
    summary = _build(_hello(tmp_path_factory, "hello-el8"), apps["hello"], "--glibc", BASELINE)
    apps["summary"].write_text(summary)
    _build(_hello(tmp_path_factory, "hello-default"), apps["hello-default"])
    _build(native, apps["native"], "--glibc", BASELINE)
    _build(native, apps["native-default"])
    return apps


def _run(app: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return contain.run([app, *args], env={**os.environ, "FSBPYR_CACHE_DIR": str(cache)},
                       timeout=120)  # fmt: skip


# --- the stubs --------------------------------------------------------------------------------


def test_stubs_carry_the_runtime_id_they_were_linked_as(
    runtime: registry.Runtime, el8_runtime: registry.Runtime
) -> None:
    pin = pins.pin_for(TARGET_PYTHON, registry.host_triple())

    assert stub_identity(runtime.stub) == runtime.runtime_id == registry.runtime_id(pin)
    assert stub_identity(el8_runtime.stub) == el8_runtime.runtime_id == registry.runtime_id(
        pin, baseline=BASELINE)
    assert runtime.runtime_id != el8_runtime.runtime_id


# --- the apps ---------------------------------------------------------------------------------


def test_the_build_summary_and_inspect_say_the_glibc_the_app_needs(
    built: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    summary = built["summary"].read_text()

    assert main(["inspect", str(built["hello"])]) == 0
    text = capsys.readouterr().out

    assert "glibc >= 2.28" in summary and "target needs: glibc >= 2.28" in summary
    assert "glibc >= 2.28" in text and "baseline   glibc 2.28" in text


def test_the_app_records_the_baseline_and_the_el8_runtime_it_was_built_with(
    built: dict[str, Path], el8_runtime: registry.Runtime
) -> None:
    info = _info(built["hello"])

    assert info["glibc_baseline"] == BASELINE
    assert info["runtime"] == el8_runtime.runtime_id
    assert str(info["runtime_inputs"]["sysroot"]).startswith("rocky8-glibc2.28-")  # type: ignore[index]
    assert info["stub_sha256"] == stub_digest(el8_runtime.stub)
    assert info["glibc"] == "2.28"


def test_a_default_build_is_what_it_was(
    built: dict[str, Path], runtime: registry.Runtime
) -> None:
    info = _info(built["hello-default"])

    assert "glibc_baseline" not in info
    assert info["runtime"] == runtime.runtime_id
    assert str(info["runtime_inputs"]["sysroot"]).startswith("debian12-")  # type: ignore[index]
    assert info["stub_sha256"] == stub_digest(runtime.stub)
    assert info["glibc"] == "2.34"


def test_a_default_build_is_reproducible(
    built: dict[str, Path], tmp_path_factory: pytest.TempPathFactory
) -> None:
    again = tmp_path_factory.mktemp("again") / "hello-default"

    _build(_hello(tmp_path_factory, "hello-default"), again)

    assert again.read_bytes() == built["hello-default"].read_bytes()


def test_the_native_wheel_is_chosen_for_manylinux_2_28(built: dict[str, Path]) -> None:
    el8 = [glibc_of(tag) for tag in wheel_tags(built["native"], "cryptography")]
    default = [glibc_of(tag) for tag in wheel_tags(built["native-default"], "cryptography")]

    assert el8 and all(need is not None and need <= (2, 28) for need in el8), el8
    assert any(need == (2, 34) for need in default), default  # the default takes the newer wheel
    assert (2, 28) in el8
    assert _info(built["native"])["glibc"] == "2.28"  # the wheels need no more


@pytest.mark.parametrize("name", ["hello", "native"])
def test_an_el8_app_runs_on_this_host(built: dict[str, Path], tmp_path: Path, name: str) -> None:
    result = _run(built[name], tmp_path / "cache", *(["5", "x"] if name == "hello" else []))

    assert result.stdout.strip() == (HELLO_OUTPUT if name == "hello" else NATIVE_OUTPUT)
    assert result.returncode == (5 if name == "hello" else 0), result.stderr


def test_the_projects_own_extension_is_rebuilt_against_the_el8_sysroot(
    tmp_path: Path, el8_runtime: registry.Runtime, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    """cext's extension binds __isoc23_strtol (GLIBC_2.38) when the host builds it; with
    --glibc 2.28 it is built again against Rocky Linux 8.10's glibc, and needs no more than 2.28.
    The build gets a uv cache of its own, as test_build's version of this test does."""
    if shutil.which("cc") is None:
        pytest.skip("no C compiler")
    project = tmp_path / "cext"
    shutil.copytree(FIXTURES / "cext", project)
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    executable = tmp_path / "bin" / "cext"

    assert main(["build", str(project), "-o", str(executable), "--glibc", BASELINE]) == 0

    out, err = capsys.readouterr()
    assert "rebuilding cext against the glibc 2.28 sysroot" in out
    assert "glibc" not in err
    assert _info(executable)["glibc"] == "2.28"
    cache = {"FSBPYR_CACHE_DIR": str(tmp_path / "cache")}
    result = contain.run([executable, "21"], env={**os.environ, **cache}, timeout=120)
    assert result.stdout == "cext 42\n", result.stderr
    result = contain.run([executable, "--ctypes"], env={**os.environ, **cache}, timeout=120)
    assert result.stdout == "ctypes True\n"


# --- a release fsbpyr -------------------------------------------------------------------------


@pytest.fixture
def releases(el8_runtime: registry.Runtime) -> Iterator[FakeReleases]:
    """A release that publishes the EL8 stub of the tests' Python, as part 3's workflow will."""
    fake = FakeReleases()
    fake.files[f"/{TAG}/{ASSET}.xz"] = lzma.compress(el8_runtime.stub)
    fake.files[f"/{TAG}/{SUMS}"] = (
        f"{hashlib.sha256(el8_runtime.stub).hexdigest()}  {ASSET}\n".encode())
    yield fake
    fake.close()


def test_a_release_builds_for_el8_with_its_stdlib_and_the_downloaded_stub(
    releases: FakeReleases, built: dict[str, Path], runtime: registry.Runtime,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The embedded runtime stands in for the executable's (monkeypatched, so this builds no
    executable): the stdlib of the default runtime and the stub of the EL8 asset make the very
    app the cache's EL8 runtime does, whose stdlib is the same."""
    monkeypatch.setenv("FSBPYR_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv(dispatch.DOWNLOADS_VARIABLE, releases.base)
    monkeypatch.setattr(stubs, "own_version", lambda: f"{TAG[1:]}-abcdef12")
    monkeypatch.setattr(registry, "running_executable", lambda: Path("/proc/self/exe"))
    monkeypatch.setattr(registry.Runtime, "from_executable", classmethod(lambda cls, path: runtime))
    out = tmp_path / "hello"

    _build(_hello(tmp_path_factory, "hello-release"), out, "--glibc", BASELINE)

    assert releases.requests == [f"/{TAG}/{SUMS}", f"/{TAG}/{ASSET}.xz"]
    assert out.read_bytes() == built["hello"].read_bytes()
    assert (tmp_path / "cache" / "downloads" / f"{ASSET}-{TAG}").is_file()


def _tagged_copy(directory: Path) -> Path:
    """This checkout's sources as a git repository with a commit tagged v1.2.0: built, it is a
    release fsbpyr."""
    copy = directory / "fsbpyr"
    copy.mkdir()
    for name in ("pyproject.toml", "uv.lock", "LICENSE", "README.md", ".python-version"):
        shutil.copy2(ROOT / name, copy / name)
    shutil.copytree(ROOT / "src", copy / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))  # fmt: skip
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-C", str(copy)]
    for command in (["init", "-q", "-b", "main"], ["add", "-A"],
                    ["commit", "-q", "-m", "release"], ["tag", TAG]):  # fmt: skip
        subprocess.run([*git, *command], check=True, capture_output=True)
    return copy


@pytest.fixture(scope="module")
def release_fsbpyr(
    tmp_path_factory: pytest.TempPathFactory, runtime: registry.Runtime
) -> Path:
    """fsbpyr built from the tagged copy: an executable that reports itself as release 1.2.0."""
    if shutil.which("git") is None:
        pytest.skip("needs git")
    copy = _tagged_copy(tmp_path_factory.mktemp("release-src"))
    out = tmp_path_factory.mktemp("release-bin") / "fsbpyr"
    _build(copy, out)
    assert _info(out)["git"]["tag"] == TAG  # type: ignore[index]  # its own_version says 1.2.0
    return out


def _release_build(
    fsbpyr: Path, project: Path, out: Path, cache: Path, base: str
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(cache), dispatch.DOWNLOADS_VARIABLE: base,
           "FSBPYR_GC": "0"}  # fmt: skip
    command: list[str | Path] = [fsbpyr, "build", project, "-o", out, "--python", TARGET_PYTHON,
                                 "--glibc", BASELINE]
    return contain.run(command, env=env, timeout=BUILD_TIMEOUT_S)


def test_a_real_release_downloads_the_el8_stub_and_builds_the_same_app(
    release_fsbpyr: Path, releases: FakeReleases, built: dict[str, Path], tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory
) -> None:
    out = tmp_path / "hello"
    project = _hello(tmp_path_factory, "hello-real")

    result = _release_build(release_fsbpyr, project, out, tmp_path / "cache", releases.base)

    assert result.returncode == 0, result.stderr
    assert releases.requests == [f"/{TAG}/{SUMS}", f"/{TAG}/{ASSET}.xz"]
    assert_same_app(out, built["hello"])


def test_a_real_release_uses_the_stub_placed_beside_it_without_the_network(
    release_fsbpyr: Path, el8_runtime: registry.Runtime, built: dict[str, Path],
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    beside = tmp_path / "bin"
    beside.mkdir()
    tool = shutil.copy2(release_fsbpyr, beside / "fsbpyr")
    (beside / f"{ASSET}.xz").write_bytes(lzma.compress(el8_runtime.stub))
    out = tmp_path / "hello"
    project = _hello(tmp_path_factory, "hello-placed")

    result = _release_build(Path(tool), project, out, tmp_path / "cache", "http://127.0.0.1:9")

    assert result.returncode == 0, result.stderr
    assert_same_app(out, built["hello"])
    assert not (tmp_path / "cache" / "downloads").exists()  # nothing was fetched or kept


def test_a_real_release_without_el8_stubs_says_so_plainly(
    release_fsbpyr: Path, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    empty = FakeReleases()
    try:
        result = _release_build(release_fsbpyr, _hello(tmp_path_factory, "hello-none"),
                                tmp_path / "hello", tmp_path / "cache", empty.base)
    finally:
        empty.close()

    assert result.returncode == 1
    assert f"this release ({TAG}) has no glibc 2.28 stubs" in result.stderr
    assert f"place {ASSET} (or {ASSET}.xz) next to {release_fsbpyr}" in result.stderr
    assert "Traceback" not in result.stderr and not (tmp_path / "hello").exists()


def test_a_real_release_refuses_the_default_stub_in_place_of_the_el8_one(
    release_fsbpyr: Path, runtime: registry.Runtime, tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory
) -> None:
    beside = tmp_path / "bin"
    beside.mkdir()
    tool = shutil.copy2(release_fsbpyr, beside / "fsbpyr")
    (beside / ASSET).write_bytes(runtime.stub)  # the glibc 2.34 stub under the EL8 name

    result = _release_build(Path(tool), _hello(tmp_path_factory, "hello-wrong"),
                            tmp_path / "hello", tmp_path / "cache", "http://127.0.0.1:9")

    assert result.returncode == 1
    assert f"is not the stub this fsbpyr ({TAG}) needs" in result.stderr
    assert f"use the {ASSET} of release {TAG}" in result.stderr
    assert not (tmp_path / "hello").exists()
