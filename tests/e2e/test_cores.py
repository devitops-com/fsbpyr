"""How many threads the launcher's parallel work runs on (launcher/parallel.c), probed
(tests/probes/cores.c) on a pretend kernel: the CPUs the process may use, at most 8, also past
the 1024 CPUs a cpu_set_t holds, where the kernel refuses that mask (EINVAL)."""

import subprocess
from pathlib import Path

import pytest

from .conftest import LauncherBuild

pytestmark = pytest.mark.slow

PROBE = Path(__file__).parents[1] / "probes" / "cores.c"


@pytest.fixture(scope="module")
def probe(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("probe") / "cores"
    flags = ("-Wl,--wrap=sched_getaffinity,--wrap=pthread_create",
             "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined")  # fmt: skip
    return launcher_build.compile(out, [PROBE, "parallel.c"], *flags)


@pytest.mark.parametrize(("kernel", "allowed", "threads"), [
    (64, 3, 3),
    (1024, 1024, 8),
    (1025, 3, 3),
    (4096, 3, 3),
    (4096, 4096, 8),
    (8192, 1, 1),
])  # fmt: skip
def test_the_threads_are_the_usable_cpus_up_to_8(
    probe: Path, kernel: int, allowed: int, threads: int
) -> None:
    result = subprocess.run([probe, str(kernel), str(allowed)], capture_output=True, text=True,
                            check=False)  # fmt: skip

    assert (result.returncode, result.stderr) == (0, "")
    assert int(result.stdout) == threads
