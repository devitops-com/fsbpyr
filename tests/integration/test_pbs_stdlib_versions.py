"""What differs in the stdlib selection and startup modules between the pinned CPython versions
(needs the cache). The rules in runtime/stdlib.py are the same for all; these tests pin what they
select from each version's PBS distribution (checked 2026-10-20 against PBS 20261009)."""

from collections.abc import Iterator

import pytest

from fsbpyr.payload.format import ModuleFlag
from fsbpyr.runtime import pbs, stdlib
from fsbpyr.runtime.pins import pin_for
from fsbpyr.runtime.registry import cache_home

pytestmark = pytest.mark.slow

TRIPLE = "x86_64-unknown-linux-gnu"
ENCODINGS = {"encodings", "encodings.aliases", "encodings.utf_8"}
# version -> (built-in extension modules, startup modules, selected modules, unselected ones)
EXPECTED = {
    "3.12": (92, ENCODINGS, {"lib2to3", "telnetlib", "collections.abc", "pathlib"},
             {"_pyrepl", "_colorize", "compression", "annotationlib"}),
    "3.13": (92, ENCODINGS | {"linecache"}, {"_pyrepl", "_colorize", "pathlib._local"},
             {"lib2to3", "telnetlib", "collections.abc", "compression", "annotationlib"}),
    "3.14": (96, ENCODINGS | {"linecache"}, {"compression.zstd", "annotationlib", "_py_warnings"},
             {"lib2to3", "telnetlib", "collections.abc", "pathlib._abc", "_compression"}),
}  # fmt: skip
BUILTINS_ONLY_IN_314 = {"_zstd", "_hmac", "_remote_debugging", "_types"}


def _dist(version: str) -> pbs.PbsDist:
    # the real cache: once tests/e2e has run, FSBPYR_CACHE_DIR names the session's private one
    root = cache_home() / "fsbpyr" / "pbs" / pin_for(version, TRIPLE).name / "python"
    if not (root / "PYTHON.json").exists():
        pytest.skip(f"PBS distribution for {version} is not in the cache (fsbpyr runtime build)")
    return pbs.PbsDist.load(root)


@pytest.fixture(scope="module", params=sorted(EXPECTED))
def dist(request: pytest.FixtureRequest) -> pbs.PbsDist:
    return _dist(request.param)


@pytest.fixture(scope="module")
def selections() -> dict[str, set[str]]:
    return {v: {m[0] for m in stdlib.collect(_dist(v).stdlib).modules} for v in EXPECTED}


def test_builtin_extension_modules_per_version(dist: pbs.PbsDist) -> None:
    builtin = set(dist.builtin_modules())

    assert len(builtin) == EXPECTED[dist.python][0]
    assert (builtin >= BUILTINS_ONLY_IN_314) == (dist.python == "3.14")


def test_startup_modules_per_version(dist: pbs.PbsDist) -> None:
    assert set(stdlib.startup_names(dist.interpreter)) == EXPECTED[dist.python][1]


def test_selection_per_version(dist: pbs.PbsDist, selections: dict[str, set[str]]) -> None:
    _, _, selected, unselected = EXPECTED[dist.python]
    mine = selections[dist.python]

    assert selected <= mine
    assert not unselected & mine
    assert {"os", "json", "importlib.metadata", "encodings.utf_8"} <= mine
    assert not any(part in stdlib.EXCLUDED_DIRS for name in mine for part in name.split("."))


@pytest.fixture
def plain_canonicalize(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The canonicalizer reads 3.14's marshal format only (per version: Phase 9 step 2); the
    selection does not depend on it."""
    monkeypatch.setattr(stdlib, "canonicalize", lambda raw: raw)
    yield


@pytest.mark.usefixtures("plain_canonicalize")
def test_fragment_selects_by_the_same_rules_on_every_version(dist: pbs.PbsDist) -> None:
    warnings: list[str] = []

    modules, files = stdlib.fragment(dist, warn=warnings.append)

    startup = {m.name for m in modules if m.flags & ModuleFlag.STARTUP}
    assert startup == EXPECTED[dist.python][1]
    assert not [w for w in warnings if "not valid Python" in w]
    assert {m.name for m in modules} == {m[0] for m in stdlib.collect(dist.stdlib).modules}
    assert "os.py" in {f.path for f in files}
