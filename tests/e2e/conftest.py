"""End-to-end fixtures: a real runtime (built once, cached) and an app builder."""

import os
import shutil
import subprocess
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from fsbpyr import bytecode
from fsbpyr.assemble import App, build_executable
from fsbpyr.build import TARGET_PYTHON
from fsbpyr.payload.format import FileKind, ModuleKind
from fsbpyr.payload.spec import Blob, File, Module
from fsbpyr.runtime import pbs, pins, registry, stub
from fsbpyr.runtime.toolchain import ToolchainError, find_toolchain

from ..support import corpus, downloads

type BuildApp = Callable[[dict[str, str], str], Path]

# The pinned downloads kept between sessions (tests/support/downloads.py): a directory named by
# FSBPYR_TEST_DOWNLOADS (`make test-container` keeps one in a volume), none without it.
DOWNLOADS_VARIABLE = "FSBPYR_TEST_DOWNLOADS"
# A directory named by FSBPYR_LLVM_COV_DIR (full-suite.yml sets one) makes LauncherBuild.compile
# build with llvm-cov's instrumentation and keep each program there, for the launcher coverage
# report; LLVM_PROFILE_FILE says where the runs write their profiles.
COVERAGE_VARIABLE = "FSBPYR_LLVM_COV_DIR"
COVERAGE_FLAGS = ("-fprofile-instr-generate", "-fcoverage-mapping")
_download_status: dict[str, str] = {}  # file name -> "seeded" or "fetched", for the summary


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
        return registry.find_runtime(TARGET_PYTHON, sanitize=sanitize)
    except registry.RuntimeCacheError:
        pass
    pinned = downloads.pinned(TARGET_PYTHON)
    kept = Path(os.environ[DOWNLOADS_VARIABLE]) if os.environ.get(DOWNLOADS_VARIABLE) else None
    seeded = downloads.seed(pinned, kept, registry.cache_root())
    for name, how in seeded.items():
        _download_status.setdefault(name, how)
    try:
        if kept is None:
            return registry.build_runtime(TARGET_PYTHON, log=lambda _: None, sanitize=sanitize)
        downloads.build_kept_runtime(TARGET_PYTHON, kept, sanitize=sanitize)  # built already?
    except ToolchainError as exc:  # pragma: no cover - depends on the host
        pytest.skip(f"cannot build a runtime here: {exc}")
    downloads.seed_runtime(TARGET_PYTHON, kept, registry.cache_root(), sanitize=sanitize)
    return registry.find_runtime(TARGET_PYTHON, sanitize=sanitize)


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Whether the runtime's pinned downloads came from FSBPYR_TEST_DOWNLOADS or the network,
    and how the real-world corpus fared (TST-3)."""
    if lines := corpus.summary():
        terminalreporter.section("corpus")
        for line in lines:
            terminalreporter.line(line)
    if _download_status:
        terminalreporter.section("runtime downloads")
        for name, how in _download_status.items():
            terminalreporter.line(f"{how}: {name}")


@dataclass(frozen=True)
class LauncherBuild:
    """The runtime build's compiler, a copy of the launcher's sources and the runtime's own
    zstd, liblzma and OpenSSL, for test programs built from launcher code (D38)."""

    clang: Path
    sources: Path
    libs: tuple[Path, ...]

    def compile(self, out: Path, files: Sequence[Path | str], *flags: str) -> Path:
        """``files``: test sources, and launcher sources by name ("integrity.c")."""
        inputs = [os.fspath(f if isinstance(f, Path) else self.sources / f) for f in files]
        coverage = os.environ.get(COVERAGE_VARIABLE)
        command = [os.fspath(self.clang), "-std=c11", "-D_GNU_SOURCE", "-g", "-O1", *flags,
                   *(COVERAGE_FLAGS if coverage else ()),
                   f"-I{self.sources}", "-o", os.fspath(out), *inputs,
                   *map(os.fspath, self.libs), "-lpthread", "-ldl"]  # fmt: skip
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        if coverage:
            kept = Path(coverage)
            kept.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out, kept / f"{out.parent.name}-{out.name}")
        return out


@pytest.fixture(scope="session")
def launcher_build(
    runtime: registry.Runtime, tmp_path_factory: pytest.TempPathFactory
) -> LauncherBuild:
    pin = pins.pin_for(runtime.python_version.rpartition(".")[0], registry.host_triple())
    dist = pbs.fetch(pin, registry.cache_root())
    sources = tmp_path_factory.mktemp("launcher-sources")
    stub._copy_launcher(sources)
    libs = tuple(dist.root / "build" / "lib" / name
                 for name in ("libzstd.a", "liblzma.a", "libcrypto.a"))  # fmt: skip
    return LauncherBuild(find_toolchain(dist.llvm_major).clang, sources, libs)


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
