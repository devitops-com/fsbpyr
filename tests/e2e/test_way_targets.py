"""The walk of the cache root's way (D38, launcher/way.c) on symlink targets no local file system
makes, probed (tests/probes/way.c, readlinkat wrapped): a target as long as the walk's whole
buffer (PATH_MAX) may have been cut short, and is refused; one byte shorter, the longest that
symlink() makes (tmpfs takes it; ~/tmp's XFS stops at 1024), it is followed to its end. A FUSE or
NFS server on a kernel with pages over 4 KiB can give a longer one."""

import errno
import os
import subprocess
from pathlib import Path

import pytest

from .conftest import LauncherBuild
from .test_way import PROBE_FLAGS

pytestmark = pytest.mark.slow

PROBE = Path(__file__).parents[1] / "probes" / "way.c"
PATH_MAX = 4096  # <linux/limits.h>: the size of the walk's buffer for a target


@pytest.fixture(scope="module")
def probe(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("probe") / "way"
    return launcher_build.compile(out, [PROBE, "way.c"], *PROBE_FLAGS)


def _walk(probe: Path, root: str, length: str) -> str:
    """The verdict on ``root``'s way, the symlink ``long`` on it given as ``length`` bytes."""
    env = {**os.environ, "PROBE_LINK": "long", "PROBE_LENGTH": length}
    result = subprocess.run([probe, root], capture_output=True, text=True, check=False, env=env,
                            timeout=60)  # fmt: skip
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def base(tmp_path: Path) -> Path:
    """A directory of ours, with ``open`` (any user may write to it, not sticky) and ``long``, a
    symlink to it."""
    base = Path(os.path.realpath(tmp_path))
    (base / "open").mkdir()
    (base / "open").chmod(0o777)
    (base / "long").symlink_to(base / "open")
    return base


def test_a_target_as_long_as_the_buffer_is_refused(probe: Path, base: Path) -> None:
    assert _walk(probe, f"{base}/long/cache", "full") == (
        f"SYSTEM {errno.ENAMETOOLONG} {base}/long"
    )


def test_a_target_one_byte_shorter_is_followed_to_its_end(probe: Path, base: Path) -> None:
    """Its end names the directory; the slashes in front of it change nothing."""
    assert _walk(probe, f"{base}/long/cache", str(PATH_MAX - 1)) == f"EXPOSED 0 {base}/open"
