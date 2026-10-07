"""Facts about the pinned PBS distribution that the runtime build relies on (needs the cache)."""

import marshal
from pathlib import Path

import pytest

from fsbpyr.runtime import ar, pbs, stdlib
from fsbpyr.runtime.pins import pin_for
from fsbpyr.runtime.registry import cache_root

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def dist() -> pbs.PbsDist:
    pin = pin_for("3.14", "x86_64-unknown-linux-gnu")
    root = cache_root() / "pbs" / pin.name / "python"
    if not (root / "PYTHON.json").exists():
        pytest.skip("pinned PBS distribution is not in the cache (run fsbpyr runtime build)")
    return pbs.PbsDist.load(root)


def test_startup_set_matches_spike_s2(dist: pbs.PbsDist) -> None:
    names = stdlib.startup_names(dist.interpreter)

    assert set(names) == {"encodings", "encodings.aliases", "encodings.utf_8", "linecache"}


def test_compile_worker_uses_the_target_interpreter(dist: pbs.PbsDist) -> None:
    compiled = stdlib.compile_files(dist.interpreter, dist.stdlib, ["json/__init__.py", "os.py"])

    assert marshal.loads(compiled["json/__init__.py"] or b"").co_filename == "json/__init__.py"
    assert compiled["os.py"] is not None


def test_link_plan_adds_only_hacl_objects(dist: pbs.PbsDist) -> None:
    libpython = dist.root / dist.info["build_info"]["core"]["static_lib"]
    inputs = pbs.link_inputs(dist, set(ar.members(libpython)))

    assert {Path(p).parent.name for p in inputs.extra_objects} == {"_hacl"}
    assert len(inputs.extra_objects) == 11
    assert "_tkinter" not in dist.builtin_modules()
    assert {"_ssl", "_sqlite3", "_zstd", "_ctypes"} <= set(dist.builtin_modules())
    assert {p.name for p in inputs.static_libs} >= {"libssl.a", "libcrypto.a", "libzstd.a"}
