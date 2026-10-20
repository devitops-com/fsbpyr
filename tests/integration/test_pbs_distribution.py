"""Facts about the pinned PBS distribution that the runtime build relies on (needs the cache)."""

import marshal
import shutil
import types
from pathlib import Path
from typing import cast

import pytest

from fsbpyr.build import TARGET_PYTHON
from fsbpyr.runtime import ar, notices, pbs, stdlib
from fsbpyr.runtime.pins import pin_for
from fsbpyr.runtime.registry import cache_root

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def dist() -> pbs.PbsDist:
    pin = pin_for(TARGET_PYTHON, "x86_64-unknown-linux-gnu")
    root = cache_root() / "pbs" / pin.name / "python"
    if not (root / "PYTHON.json").exists():
        pytest.skip("pinned PBS distribution is not in the cache (run fsbpyr runtime build)")
    return pbs.PbsDist.load(root)


def test_startup_set_matches_spike_s2(dist: pbs.PbsDist) -> None:
    names = stdlib.startup_names(dist.interpreter)

    expected = {"encodings", "encodings.aliases", "encodings.utf_8"}
    assert set(names) == expected | ({"linecache"} if TARGET_PYTHON != "3.12" else set())  # 3.13+


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


HACL_OBJECTS = {"3.12": 1, "3.13": 1, "3.14": 11}  # the objects of HACL*'s hashes PBS leaves out


def test_link_plan_adds_only_hacl_objects(dist: pbs.PbsDist) -> None:
    libpython = dist.root / dist.info["build_info"]["core"]["static_lib"]
    inputs = pbs.link_inputs(dist, set(ar.members(libpython)))

    assert {Path(p).parent.name for p in inputs.extra_objects} == {"_hacl"}
    assert len(inputs.extra_objects) == HACL_OBJECTS[TARGET_PYTHON]
    assert "_tkinter" not in dist.builtin_modules()
    assert {"_ssl", "_sqlite3", "_ctypes"} <= set(dist.builtin_modules())
    assert ("_zstd" in dist.builtin_modules()) == (TARGET_PYTHON == "3.14")
    assert {p.name for p in inputs.static_libs} >= {"libssl.a", "libcrypto.a", "libzstd.a"}


def test_every_linked_static_library_has_a_licence_text(dist: pbs.PbsDist) -> None:
    libpython = dist.root / dist.info["build_info"]["core"]["static_lib"]
    inputs = pbs.link_inputs(dist, set(ar.members(libpython)))
    found = notices.collect(dist)
    extensions = dist.info["build_info"]["extensions"]
    wanted: dict[str, set[str]] = {}  # static library -> the texts PBS lists for it
    for module in dist.builtin_modules():
        variant = extensions[module][0]
        for link in variant["links"]:
            if link.get("path_static"):
                wanted.setdefault(link["path_static"], set()).update(
                    Path(path).name for path in variant.get("license_paths", ())
                )

    # no module of 3.12 and 3.13 links libzstd.a: the link plan adds it for the launcher
    added = {"libzstd.a"} if "_zstd" not in dist.builtin_modules() else set()
    assert {dist.root / lib for lib in wanted} == {
        lib for lib in inputs.static_libs if lib.name not in added
    }
    for lib, names in wanted.items():
        assert names - notices.NOT_LINKED, f"PBS lists no licence text for {lib}"
        assert (names - notices.NOT_LINKED) <= found.texts.keys(), lib
    assert b"Apache License" in found.texts["LICENSE.openssl-3.txt"]
    assert "LICENSE.hacl.txt" in found.texts  # the hash modules' objects are HACL*'s


def test_the_texts_pbs_lists_but_does_not_ship_are_known(dist: pbs.PbsDist) -> None:
    # a newer PBS that ships zstd's or HACL*'s text, or drops zlib-ng's, needs a look at notices.py
    expected = {"LICENSE.zlib-ng.txt"}
    if TARGET_PYTHON == "3.14":
        expected.add("LICENSE.zstd.txt")
    assert notices.collect(dist).unshipped == expected
