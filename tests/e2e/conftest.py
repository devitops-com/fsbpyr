"""End-to-end fixtures: a real runtime (built once, cached) and an app builder."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from fsbpyr import bytecode
from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import FileKind, ModuleKind
from fsbpyr.payload.spec import Blob, File, Module
from fsbpyr.runtime import registry
from fsbpyr.runtime.toolchain import ToolchainError

type BuildApp = Callable[[dict[str, str], str], Path]


@pytest.fixture(scope="session", autouse=True)
def _private_cache(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Executables run here (smoke tests included) never write the user's cache; tests that
    need a cache of their own still pass FSBPYR_CACHE_DIR explicitly."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("FSBPYR_CACHE_DIR", str(tmp_path_factory.mktemp("app-cache")))
        yield


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Tests marked ``sanitized`` run with both launchers: the release build and its ASan/UBSan
    variant (D38), which turns any memory error or undefined behaviour into a failed run."""
    if "runtime" in metafunc.fixturenames and metafunc.definition.get_closest_marker("sanitized"):
        metafunc.parametrize("runtime", ["release", "sanitized"], indirect=True, scope="session")


@pytest.fixture(scope="session")
def runtime(request: pytest.FixtureRequest) -> registry.Runtime:
    sanitize = getattr(request, "param", "release") == "sanitized"
    try:
        return registry.find_runtime("3.14", sanitize=sanitize)
    except registry.RuntimeCacheError:
        pass
    try:
        return registry.build_runtime("3.14", log=lambda _: None, sanitize=sanitize)
    except ToolchainError as exc:  # pragma: no cover - depends on the host
        pytest.skip(f"cannot build a runtime here: {exc}")


def app_content(sources: dict[str, str]) -> tuple[tuple[Module, ...], tuple[File, ...]]:
    """Modules (with sources) for a {relative path: source} tree of pure-Python files."""
    modules, files = [], []
    for rel, text in sorted(sources.items()):
        parts = rel.removesuffix(".py").split("/")
        is_package = parts[-1] == "__init__"
        name = ".".join(parts[:-1] if is_package else parts)
        source = Blob.zstd(text.encode())
        code = bytecode.dumps(compile(text, rel, "exec", dont_inherit=True))
        kind = ModuleKind.PACKAGE if is_package else ModuleKind.MODULE
        modules.append(Module(name, rel, kind, code=Blob.code(code), source=source))
        files.append(File(rel, FileKind.SOURCE, source))
    return tuple(modules), tuple(files)


@pytest.fixture
def build_app(runtime: registry.Runtime, tmp_path: Path) -> BuildApp:
    def build(sources: dict[str, str], entrypoint: str) -> Path:
        modules, files = app_content(sources)
        out = tmp_path / "app"
        build_executable(runtime, App("app", entrypoint, modules=modules, files=files), out)
        return out

    return build
