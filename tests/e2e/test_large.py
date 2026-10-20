"""Past every 32-bit size, within 1 GB of resident memory throughout (D38, Phase 8c exit): a
package with a 5 GiB file (sparse, of zeros) builds, compressed as a build compresses it or
stored raw (a 5 GiB executable, sparse as well), checks itself, streams the file and extracts it,
and `fsbpyr inspect --verify` reads it, none of them taking more than a fraction of a GB. On
demand, the same runs with 1 TB: FSBPYR_LARGE_SIZE=1099511627776 (docs/IMPLEMENTATION_PLAN.md,
Phase 8c)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fsbpyr.payload.format import Codec
from fsbpyr.runtime.registry import Runtime

pytestmark = pytest.mark.slow

SIZE = int(os.environ.get("FSBPYR_LARGE_SIZE", str(5 << 30)))
LIMIT = 1 << 30  # D38: fsbpyr's own work, whatever the input's size

# A process's own peak: VmHWM starts afresh at exec, where ru_maxrss keeps the peak of the
# process that exec'd (with vfork, pytest's own: past 1 GB when the suite built a runtime).
_PEAK = """\
def peak():
    with open("/proc/self/status") as status:
        return next(int(line.split()[1]) for line in status if line.startswith("VmHWM:")) << 10
"""

_APP = (
    _PEAK
    + """\
import json, os
from importlib.resources import as_file, files


def main():
    report = {"started": peak()}
    with files("big").joinpath("zeros.bin").open("rb") as file:
        size = zeros = 0
        while piece := file.read(16 << 20):
            size += len(piece)
            zeros += piece.count(0)
        report["streamed"] = [size, zeros]
    with as_file(files("big").joinpath("zeros.bin")) as path:
        report["extracted"] = [os.path.getsize(path), os.stat(path).st_blocks * 512]
    report["peak"] = peak()
    print(json.dumps(report))
"""
)

_BUILD = (
    _PEAK
    + """\
import json, sys
from pathlib import Path
from fsbpyr.assemble import App, build_executable
from fsbpyr.classify import classify
from fsbpyr.closure import SiteIndex
from fsbpyr.payload.spec import Blob
from fsbpyr.payload.spill import Spill
from fsbpyr.runtime import registry

site, out, raw = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] == "raw"
if raw:  # files too large to read whole are stored as they are, not compressed
    Spill.compress_files = lambda self, paths, **_: {rel: Blob.raw_file(path)
                                                    for rel, path in paths.items()}
runtime = registry.find_runtime("{}.{}".format(*sys.version_info[:2]))
index = SiteIndex.scan(site)
with Spill(out.parent) as spill:
    content = classify(list(index.dists.values()), include_source=True, spill=spill,
                       extension_suffixes=runtime.extension_suffixes)
    files = content.files
    app = App("big", "big:main", modules=content.modules, files=files, dists=content.dists)
    build_executable(runtime, app, out)
print(json.dumps({"peak": peak(),
                  "codec": int(next(f for f in files if f.path == "big/zeros.bin").blob.codec)}))
"""
)


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A site-packages with one distribution, ``big``: a package and its 5 GiB of zeros."""
    site = tmp_path_factory.mktemp("site")
    info = site / "big-1.0.dist-info"
    (site / "big").mkdir()
    (site / "big" / "__init__.py").write_text(_APP)
    with (site / "big" / "zeros.bin").open("wb") as file:
        file.truncate(SIZE)  # sparse: no disk, no memory
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.4\nName: big\nVersion: 1.0\n")
    records = ["big/__init__.py,,", "big/zeros.bin,,", f"{info.name}/METADATA,,",
               f"{info.name}/RECORD,,"]  # fmt: skip
    (info / "RECORD").write_text("\n".join(records) + "\n")
    return site


_INSPECT = (
    _PEAK
    + """\
import json, sys
from fsbpyr.cli import main
code = main(["inspect", "--verify", sys.argv[1]])
print(json.dumps({"code": code, "peak": peak()}))
"""
)


@pytest.mark.parametrize("stored", ["compressed", "raw"])
def test_a_5_gib_file_builds_runs_and_extracts_in_bounded_memory(
    runtime: Runtime, stored: str, site: Path, tmp_path: Path
) -> None:
    app = tmp_path / "big"
    built = subprocess.run([sys.executable, "-c", _BUILD, site, app, stored], capture_output=True,
                           text=True, check=False)  # fmt: skip
    assert built.returncode == 0, built.stderr
    build = json.loads(built.stdout)
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp_path / "cache")}

    ran = subprocess.run([app], capture_output=True, text=True, check=False, env=env)

    assert ran.returncode == 0, ran.stderr
    report = json.loads(ran.stdout)
    assert build["codec"] == (Codec.NONE if stored == "raw" else Codec.ZSTD_PIECES)  # D39
    assert report["streamed"] == [SIZE, SIZE]
    assert report["extracted"][0] == SIZE and report["extracted"][1] < (64 << 20)  # holes
    assert build["peak"] < LIMIT and report["peak"] < LIMIT, (build["peak"], report["peak"])
    if stored == "raw":  # a sparse executable past 5 GiB
        assert os.path.getsize(app) > SIZE and os.stat(app).st_blocks * 512 < (256 << 20)


def test_inspect_verifies_a_5_gib_executable_in_bounded_memory(
    runtime: Runtime, site: Path, tmp_path: Path
) -> None:
    app = tmp_path / "big"
    built = subprocess.run([sys.executable, "-c", _BUILD, site, app, "raw"], capture_output=True,
                           text=True, check=False)  # fmt: skip
    assert built.returncode == 0, built.stderr

    inspected = subprocess.run([sys.executable, "-c", _INSPECT, app], capture_output=True,
                               text=True, check=False)  # fmt: skip

    assert inspected.returncode == 0, inspected.stderr
    report = json.loads(inspected.stdout.splitlines()[-1])  # measured in the process itself
    assert report["code"] == 0 and 0 < report["peak"] < LIMIT, report
