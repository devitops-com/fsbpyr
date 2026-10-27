"""Phase 6c exit criteria: executables run on older distributions (glibc 2.34 baseline, D25).

Needs podman with the images below (pulled on first use) and the cached runtime. When
FSBPYR_PORTABILITY_APPS names a directory with the four built apps (tools/portability-apps.sh,
`make test-portability`), they are used as they are and no runtime is needed. On a Python that
onnxruntime has no wheel for (tests/support/no-wheels.txt), heavy and heavy-pip are not built
and what runs them is not run; the compat checks of packages without a wheel are not run either.
The glibc 2.28 apps are tested where they can be: not in the EL8 test mode, whose matrix runs
every app on the EL8 images, and not when a prebuilt set has none.
"""

import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fsbpyr.build import TARGET_PYTHON
from fsbpyr.cli import main
from fsbpyr.natives import glibc_libraries
from fsbpyr.payload.format import NATIVE_KINDS
from fsbpyr.payload.reader import Executable
from fsbpyr.project import LockSource

from ..support import baseline, requires, wheels
from ..support.images import EL8_IMAGES, images_for
from ..support.stderr import NO_HOSTNAME, ONNX_HOST
from .test_compat import MATRIX
from .test_el8 import HELLO_OUTPUT, NATIVE_OUTPUT, native_project
from .test_natives import EXPECTED as HEAVY

pytestmark = [pytest.mark.slow, requires.tools("podman")]

FIXTURES = Path(__file__).parents[1] / "fixtures" / "projects"
# x86-64: all 12; aarch64: those with an arm64 variant; the EL8 test mode adds the EL8 images
IMAGES = images_for(platform.machine(), baseline.BASELINE)


def _apps_python() -> str:
    """The Python the apps are built for (X.Y): a prebuilt set's, which a variant built, else
    this run's."""
    if prebuilt := os.environ.get("FSBPYR_PORTABILITY_APPS"):
        with Executable.open(Path(prebuilt) / "hello") as executable:
            return str(json.loads(executable.payload.build_info)["python"]).rpartition(".")[0]
    return TARGET_PYTHON


APPS_PYTHON = _apps_python()
HEAVY_APPS = () if wheels.missing("onnxruntime", APPS_PYTHON) else ("heavy", "heavy-pip")
COMMANDS = {  # (binary, arguments) -> expected stdout; the HTTP checks need the host's network
    ("hello", "5 x"): "hello ['5', 'x']",
    # heavy-pip is heavy kept with pip (requirements.txt); a compat check is named after the
    # package it exercises
    **{(app, check): out for app in HEAVY_APPS for check, out in HEAVY.items()},
    **{
        ("compat", check): out
        for check, out in MATRIX.items()
        if check not in ("requests", "httpx") and not wheels.missing(check, APPS_PYTHON)
    },
}
_GLIBC = re.compile(r"GLIBC_(\d+)\.(\d+)")
_REQUIRED_FROM = re.compile(r"^\s+required from (\S+):$")
_VERSION_REF = re.compile(r"^\s+0x[0-9a-f]+ 0x[0-9a-f]+ \d+ ([A-Z]+)_(\d+(?:\.\d+)*)$")


@pytest.fixture(scope="module")
def apps(tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest) -> Path:
    if prebuilt := os.environ.get("FSBPYR_PORTABILITY_APPS"):
        return Path(prebuilt)
    request.getfixturevalue("runtime")
    directory = tmp_path_factory.mktemp("portable")
    for name in ("hello", "heavy", "compat") if HEAVY_APPS else ("hello", "compat"):
        project = directory / "src" / name
        shutil.copytree(FIXTURES / name, project)
        assert main(["build", str(project), "-o", str(directory / name)]) == 0
    if not HEAVY_APPS:
        return directory
    pip_project = directory / "src" / "heavy-pip"  # maintained with pip: hash-pinned, no uv.lock
    shutil.copytree(FIXTURES / "heavy", pip_project)
    subprocess.run([requires.tool("uv"), "export", "--quiet", "--frozen", "--no-dev",
                    "--no-emit-project", "--format", "requirements-txt", "-o", "requirements.txt"],
                   cwd=pip_project, check=True)  # fmt: skip
    (pip_project / "uv.lock").unlink()
    assert main(["build", str(pip_project), "-o", str(directory / "heavy-pip")]) == 0
    with Executable.open(directory / "heavy-pip") as executable:
        lock = json.loads(executable.payload.build_info)["lock"]
        assert lock["kind"] == LockSource.REQUIREMENTS.value
    return directory


@pytest.mark.parametrize("image", IMAGES)
def test_apps_run_on_older_distributions(apps: Path, image: str) -> None:
    script = "".join(
        f"echo '== {binary} {args}'; FSBPYR_CACHE_DIR=/tmp/cache MPLCONFIGDIR=/tmp/mpl "
        f'/apps/{binary} {args} 2>&1; echo "rc=$?"\n'
        for binary, args in COMMANDS
    )
    result = subprocess.run(
        [requires.tool("podman"), "run", "--rm", "--network=none", "-v", f"{apps}:/apps:ro,Z",
         image, "sh", "-c", script],
        capture_output=True, text=True, timeout=900, check=False,
    )  # fmt: skip

    combined = ONNX_HOST.sub("", NO_HOSTNAME.sub("", result.stdout))
    sections = dict(
        (head.strip(), body.strip())
        for head, _, body in (part.partition("\n") for part in combined.split("== ")[1:])
    )
    for (binary, args), expected in COMMANDS.items():
        output = sections.get(f"{binary} {args}", "(missing)")
        rc = 5 if binary == "hello" else 0
        assert output == f"{expected}\nrc={rc}", f"{image}: {binary} {args}"


def _podman(apps: Path, image: str, script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [requires.tool("podman"), "run", "--rm", "--network=none", "-v", f"{apps}:/apps:ro,Z",
         image, "sh", "-c", script],
        capture_output=True, text=True, timeout=900, check=False,
    )  # fmt: skip


@wheels.needs("onnxruntime", python=APPS_PYTHON)
@pytest.mark.parametrize("image", IMAGES)
def test_selftest_natives_passes_on_older_distributions(apps: Path, image: str) -> None:
    """NAT-4: every extension of heavy finds its libraries and versions on each image."""
    result = _podman(apps, image, "FSBPYR_CACHE_DIR=/tmp/cache FSBPYR_SELFTEST=natives /apps/heavy")

    assert result.returncode == 0, f"{image}: {result.stdout}{result.stderr}"
    assert "all natives load" in result.stdout


@wheels.needs("onnxruntime", python=APPS_PYTHON)
def test_selftest_natives_names_a_library_the_machine_lacks(apps: Path) -> None:
    script = ("rm -f /usr/lib/*-linux-gnu/libstdc++.so.6* /lib/*-linux-gnu/libstdc++.so.6*; "
              "FSBPYR_CACHE_DIR=/tmp/cache FSBPYR_SELFTEST=natives /apps/heavy")  # fmt: skip
    result = _podman(apps, "docker.io/library/debian:12", script)  # the layout the rm names

    assert result.returncode != 0
    assert "fsbpyr selftest: missing libstdc++.so.6 (needed by " in result.stderr


def _objdump_glibc(data: bytes, scratch: Path) -> tuple[int, int] | None:
    scratch.write_bytes(data)
    listing = subprocess.run([requires.tool("llvm-objdump"), "-T", str(scratch)],
                             capture_output=True, text=True, check=True).stdout  # fmt: skip
    return max(((int(a), int(b)) for a, b in _GLIBC.findall(listing)), default=None)


def _objdump_versions(data: bytes, scratch: Path) -> dict[str, dict[str, str]]:
    """The newest numeric symbol version per library and namespace, as objdump lists them."""
    scratch.write_bytes(data)
    listing = subprocess.run([requires.tool("llvm-objdump"), "-p", str(scratch)],
                             capture_output=True, text=True, check=True).stdout  # fmt: skip
    found: dict[str, dict[str, tuple[int, ...]]] = {}
    library = ""
    for line in listing.splitlines():
        if match := _REQUIRED_FROM.match(line):
            library = match[1]
        elif (match := _VERSION_REF.match(line)) and library:
            version = tuple(int(part) for part in match[2].split("."))
            namespaces = found.setdefault(library, {})
            namespaces[match[1]] = max(namespaces.get(match[1], version), version)
    return {lib: {ns: ".".join(map(str, v)) for ns, v in sorted(table.items())}
            for lib, table in sorted(found.items())}  # fmt: skip


@requires.tools("llvm-objdump")
@pytest.mark.parametrize("name", ["hello", *HEAVY_APPS, "compat"])
def test_reported_system_libraries_match_the_binary(apps: Path, tmp_path: Path, name: str) -> None:
    """Every library and version namespace the build reports is one the objects need, with the
    newest version among them; hello needs nothing beyond glibc's libraries."""
    with Executable.open(apps / name) as executable:
        payload = executable.payload
        reported = json.loads(payload.build_info)["system"]["libraries"]
        objects = [executable.stub_prefix()] + [
            payload.read_blob(f.blob) for f in payload.files() if f.kind in NATIVE_KINDS
        ]

    seen: dict[str, dict[str, str]] = {}
    for data in objects:
        for library, table in _objdump_versions(data, tmp_path / "object").items():
            merged = seen.setdefault(library, {})
            for namespace, version in table.items():
                old = tuple(map(int, merged.get(namespace, version).split(".")))
                merged[namespace] = ".".join(
                    map(str, max(old, tuple(map(int, version.split(".")))))
                )
    for library, entry in reported.items():
        for namespace, version in entry.get("versions", {}).items():
            assert seen[library][namespace] == version, f"{library} {namespace}"
    if name == "hello":
        assert set(reported) <= glibc_libraries()
    if name.startswith("heavy"):
        assert {"libstdc++.so.6", "libgcc_s.so.1"} <= set(reported)


@requires.tools("llvm-objdump")
@pytest.mark.parametrize("name", ["hello", *HEAVY_APPS, "compat"])
def test_reported_glibc_requirement_matches_the_binary(
    apps: Path, tmp_path: Path, name: str
) -> None:
    with Executable.open(apps / name) as executable:
        payload = executable.payload
        reported = json.loads(payload.build_info)["glibc"]
        objects = [executable.stub_prefix()] + [
            payload.read_blob(f.blob) for f in payload.files() if f.kind in NATIVE_KINDS
        ]

    found = [_objdump_glibc(data, tmp_path / "object") for data in objects]
    highest = max((v for v in found if v is not None), default=None)
    floor = baseline.as_tuple(baseline.BASELINE) if baseline.EL8 else (0, 0)  # glibc_report
    assert highest is not None and reported == "{}.{}".format(*max(highest, floor))
    assert highest <= baseline.as_tuple(baseline.BASELINE)  # what these distributions provide


def _el8_apps_wanted() -> bool:
    """Whether the glibc 2.28 apps are tested: not in the EL8 test mode, whose matrix runs all the
    apps on the EL8 images, and not when a prebuilt set has none (a release's glibc 2.28 stubs
    are assets of their own, so the set of a run on a release executable may lack them)."""
    prebuilt = os.environ.get("FSBPYR_PORTABILITY_APPS")
    return not baseline.EL8 and (not prebuilt or (Path(prebuilt) / "hello-el8").is_file())


if _el8_apps_wanted():
    @pytest.fixture(scope="module")
    def el8_apps(tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest) -> Path:
        """`hello` and an app with a manylinux_2_28 wheel, built with --glibc 2.28 (D46): from a
        prebuilt set that has them, else here, with the EL8 runtime."""
        if prebuilt := os.environ.get("FSBPYR_PORTABILITY_APPS"):
            return Path(prebuilt)
        request.getfixturevalue("el8_runtime")
        directory = tmp_path_factory.mktemp("el8-apps")
        hello = directory / "src" / "hello"
        shutil.copytree(FIXTURES / "hello", hello)
        options = ["--glibc", "2.28"]
        assert main(["build", str(hello), "-o", str(directory / "hello-el8"), *options]) == 0
        project = native_project(tmp_path_factory)
        assert main(["build", str(project), "-o", str(directory / "native-el8"), *options]) == 0
        return directory

    @pytest.mark.parametrize("image", EL8_IMAGES)
    def test_el8_apps_run_on_el8(el8_apps: Path, image: str) -> None:
        script = ("export FSBPYR_CACHE_DIR=/tmp/cache; /apps/hello-el8 5 x 2>&1; echo \"rc=$?\"; "
                  "/apps/native-el8 2>&1; echo \"rc=$?\"")  # fmt: skip

        result = subprocess.run(
            [requires.tool("podman"), "run", "--rm", "--network=none", "--pids-limit", "512",
             "--memory", "4g", "-v", f"{el8_apps}:/apps:ro,Z", image, "sh", "-c", script],
            capture_output=True, text=True, timeout=900, check=False,
        )  # fmt: skip

        assert result.stdout.splitlines() == [HELLO_OUTPUT, "rc=5", NATIVE_OUTPUT, "rc=0"], (
            image, result.stdout, result.stderr)
