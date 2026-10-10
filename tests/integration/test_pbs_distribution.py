"""Facts about the pinned PBS distribution that the runtime build relies on (needs the cache)."""

import marshal
import shutil
import types
from pathlib import Path
from typing import cast

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


def test_compiled_files_come_in_their_order_whatever_the_number_of_cores(
    dist: pbs.PbsDist, monkeypatch: pytest.MonkeyPatch
) -> None:
    rels = ["os.py", "json/__init__.py", "abc.py", "ast.py", "base64.py"]
    monkeypatch.setattr(stdlib, "THREADS", 3)

    assert list(stdlib.compile_files(dist.interpreter, dist.stdlib, rels)) == rels


def test_the_compile_workers_warnings_are_given(dist: pbs.PbsDist, tmp_path: Path) -> None:
    (tmp_path / "escapes.py").write_text('PATTERN = "\\d+"\n')  # an invalid escape: warned
    warnings: list[str] = []

    compiled = stdlib.compile_files(
        dist.interpreter, tmp_path, ["escapes.py"], warn=warnings.append
    )

    assert compiled["escapes.py"] is not None
    assert len(warnings) == 1 and "escapes.py" in warnings[0] and "SyntaxWarning" in warnings[0]


def test_a_stdlib_module_that_does_not_compile_is_named(dist: pbs.PbsDist, tmp_path: Path) -> None:
    for name in ("encodings", "linecache.py"):  # what the interpreter imports at startup
        copy = shutil.copytree if name == "encodings" else shutil.copy
        copy(dist.stdlib / name, tmp_path / name)
    (tmp_path / "broken.py").write_text("def broken(:\n")
    fake = cast("pbs.PbsDist", types.SimpleNamespace(
        interpreter=dist.interpreter, stdlib=tmp_path, version=dist.version))  # fmt: skip
    warnings: list[str] = []

    _, files = stdlib.fragment(fake, warn=warnings.append)

    assert "broken.py" in {file.path for file in files}  # kept, as a plain file
    assert [w for w in warnings if "broken.py" in w] == [
        f"broken.py: not valid Python {dist.version}, kept as a plain file"
    ]


def test_link_plan_adds_only_hacl_objects(dist: pbs.PbsDist) -> None:
    libpython = dist.root / dist.info["build_info"]["core"]["static_lib"]
    inputs = pbs.link_inputs(dist, set(ar.members(libpython)))

    assert {Path(p).parent.name for p in inputs.extra_objects} == {"_hacl"}
    assert len(inputs.extra_objects) == 11
    assert "_tkinter" not in dist.builtin_modules()
    assert {"_ssl", "_sqlite3", "_zstd", "_ctypes"} <= set(dist.builtin_modules())
    assert {p.name for p in inputs.static_libs} >= {"libssl.a", "libcrypto.a", "libzstd.a"}
