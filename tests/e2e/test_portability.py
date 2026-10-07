"""Phase 6c exit criteria: executables run on older distributions (glibc 2.28 baseline, D25).

Needs podman with the images below (pulled on first use) and the cached runtime.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fsbpyr.cli import main
from fsbpyr.payload.format import NATIVE_KINDS
from fsbpyr.payload.reader import Executable
from fsbpyr.runtime.registry import Runtime

from .test_compat import MATRIX
from .test_natives import EXPECTED as HEAVY

PODMAN = shutil.which("podman")
OBJDUMP = shutil.which("llvm-objdump-22") or shutil.which("llvm-objdump")
pytestmark = [pytest.mark.slow, pytest.mark.skipif(PODMAN is None, reason="needs podman")]

FIXTURES = Path(__file__).parents[1] / "fixtures" / "projects"
IMAGES = (  # glibc 2.28 (the baseline) up to the newest releases
    "docker.io/library/debian:10",  # 2.28
    "docker.io/library/rockylinux:8",  # 2.28
    "docker.io/library/debian:11",  # 2.31
    "docker.io/library/ubuntu:20.04",  # 2.31
    "docker.io/library/rockylinux:9",  # 2.34
    "docker.io/library/amazonlinux:2023",  # 2.34
    "docker.io/library/ubuntu:22.04",  # 2.35
    "docker.io/library/debian:12",  # 2.36
    "docker.io/opensuse/leap:15.6",  # 2.38
    "docker.io/library/ubuntu:24.04",  # 2.39
    "docker.io/library/debian:13",  # 2.41
    "docker.io/library/fedora:latest",  # 2.43
    "docker.io/library/archlinux:latest",  # 2.44
)
COMMANDS = {  # (binary, arguments) -> expected stdout; the HTTP checks need the host's network
    ("hello", "5 x"): "hello ['5', 'x']",
    **{("heavy", check): out for check, out in HEAVY.items()},
    **{
        ("compat", check): out
        for check, out in MATRIX.items()
        if check not in ("requests", "httpx")
    },
}
_GLIBC = re.compile(r"GLIBC_(\d+)\.(\d+)")


@pytest.fixture(scope="module")
def apps(tmp_path_factory: pytest.TempPathFactory, runtime: Runtime) -> Path:
    directory = tmp_path_factory.mktemp("portable")
    for name in ("hello", "heavy", "compat"):
        project = directory / "src" / name
        shutil.copytree(FIXTURES / name, project)
        assert main(["build", str(project), "-o", str(directory / name)]) == 0
    return directory


@pytest.mark.parametrize("image", IMAGES)
def test_apps_run_on_older_distributions(apps: Path, image: str) -> None:
    script = "".join(
        f"echo '== {binary} {args}'; FSBPYR_CACHE_DIR=/tmp/cache MPLCONFIGDIR=/tmp/mpl "
        f'/apps/{binary} {args} 2>&1; echo "rc=$?"\n'
        for binary, args in COMMANDS
    )
    result = subprocess.run(
        [PODMAN or "podman", "run", "--rm", "-v", f"{apps}:/apps:ro,Z",
         image, "sh", "-c", script],
        capture_output=True, text=True, timeout=900, check=False,
    )  # fmt: skip

    sections = dict(
        (head.strip(), body.strip())
        for head, _, body in (part.partition("\n") for part in result.stdout.split("== ")[1:])
    )
    for (binary, args), expected in COMMANDS.items():
        output = sections.get(f"{binary} {args}", "(missing)")
        rc = 5 if binary == "hello" else 0
        assert output == f"{expected}\nrc={rc}", f"{image}: {binary} {args}"


def _objdump_glibc(data: bytes, scratch: Path) -> tuple[int, int] | None:
    scratch.write_bytes(data)
    listing = subprocess.run([OBJDUMP or "llvm-objdump", "-T", str(scratch)],
                             capture_output=True, text=True, check=True).stdout  # fmt: skip
    return max(((int(a), int(b)) for a, b in _GLIBC.findall(listing)), default=None)


@pytest.mark.skipif(OBJDUMP is None, reason="needs llvm-objdump")
@pytest.mark.parametrize("name", ["hello", "heavy", "compat"])
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
    assert highest is not None and reported == f"{highest[0]}.{highest[1]}"
    assert highest <= (2, 28)  # the baseline these distributions provide
