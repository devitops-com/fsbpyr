"""Modules compiled ahead on worker processes (D39, compiler.py): the same results as compiling
in the build's own process, given in the classifier's order; jobs it passes over are dropped,
modules it asks for that were not ahead are compiled on the spot."""

import _thread
import multiprocessing
from pathlib import Path

import pytest

from fsbpyr import compiler
from fsbpyr.classify import Content, classify
from fsbpyr.closure import SiteIndex
from fsbpyr.compiler import Compiler, Job, compiled


@pytest.fixture
def sources(tmp_path: Path) -> list[Job]:
    jobs = []
    for i in range(12):
        path = tmp_path / f"m{i}.py"
        path.write_text(f"X = {i}\n" if i != 5 else "def broken(:\n")
        jobs.append(Job(("dist", path.name), path, path.name, hints=False))
    return jobs


@pytest.fixture
def pooled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pool even for a few modules, of 4 workers."""
    monkeypatch.setattr(compiler, "POOL_MODULES", 2)
    monkeypatch.setattr(compiler, "THREADS", 4)


def _here(job: Job) -> compiler.Compiled:
    return compiled(job.path.read_bytes(), job.rel, 9, True, False)


@pytest.mark.usefixtures("pooled")
def test_workers_compile_what_the_process_would(sources: list[Job]) -> None:
    pool = Compiler(sources, level=9, include_source=True)
    try:
        got = [pool.get(job.key, job.path, job.rel, False) for job in sources]
    finally:
        pool.close()

    assert pool._pool is not None  # the workers did it
    assert got == [_here(job) for job in sources]
    assert got[5].code is None and got[5].error is not None and got[5].error[1] == 1


@pytest.mark.usefixtures("pooled")
def test_jobs_passed_over_are_dropped_and_others_compiled_on_the_spot(
    sources: list[Job], tmp_path: Path
) -> None:
    extra = tmp_path / "extra.py"
    extra.write_text("Y = 1\n")
    pool = Compiler(sources, level=9, include_source=True)
    try:
        third = pool.get(sources[3].key, sources[3].path, sources[3].rel, False)  # 0-2 skipped
        other = pool.get(("dist", "extra.py"), extra, "extra.py", False)
        last = pool.get(sources[11].key, sources[11].path, sources[11].rel, False)
    finally:
        pool.close()

    assert third == _here(sources[3]) and last == _here(sources[11])
    assert other == compiled(extra.read_bytes(), "extra.py", 9, True, False)


def test_a_few_modules_compile_in_the_process(sources: list[Job]) -> None:
    pool = Compiler(sources, level=9, include_source=False)

    assert pool._pool is None
    assert pool.get(sources[0].key, sources[0].path, "m0.py", False).source is None
    pool.close()


def test_one_core_compiles_in_the_process(
    sources: list[Job], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compiler, "POOL_MODULES", 2)
    monkeypatch.setattr(compiler, "THREADS", 1)

    assert Compiler(sources, include_source=True)._pool is None


def _site(root: Path) -> Path:
    """A site-packages with one distribution of 300 modules, one that does not compile and one
    that reads next to its __file__ (a hint)."""
    package = root / "many"
    package.mkdir(parents=True)
    names = ["__init__.py", *(f"m{i}.py" for i in range(298)), "data_reader.py"]
    for name in names:
        (package / name).write_text(f'"""{name}"""\nVALUE = {len(name)}\n')
    (package / "m7.py").write_text("def broken(:\n")
    (package / "data_reader.py").write_text(
        "import os\nDATA = open(os.path.join(os.path.dirname(__file__), 'x.txt')).read()\n"
    )
    info = root / "many-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.4\nName: many\nVersion: 1.0\n")
    rows = [f"many/{name},," for name in names] + [
        f"{info.name}/METADATA,,",
        f"{info.name}/RECORD,,",
    ]
    (info / "RECORD").write_text("\n".join(rows) + "\n")
    return root


def test_a_build_classifies_alike_on_one_core_and_on_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    dists = list(SiteIndex.scan(_site(tmp_path)).dists.values())
    suffixes = (".cpython-314-x86_64-linux-gnu.so", ".abi3.so", ".so")

    def classified() -> Content:
        return classify(dists, extension_suffixes=suffixes, include_source=True, hints=True)

    monkeypatch.setattr(compiler, "THREADS", 4)
    pooled = classified()
    monkeypatch.setattr(compiler, "THREADS", 1)

    assert classified() == pooled
    assert any("m7.py kept as data" in warning for warning in pooled.warnings)
    assert any("data_reader.py" in warning for warning in pooled.warnings)


def test_a_thread_threading_does_not_know_keeps_the_pool_from_forking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    methods: list[str] = []
    get_context = multiprocessing.get_context

    def recorded(method: str) -> multiprocessing.context.BaseContext:
        methods.append(method)
        return get_context(method)

    monkeypatch.setattr(multiprocessing, "get_context", recorded)
    release = _thread.allocate_lock()
    release.acquire()
    _thread.start_new_thread(release.acquire, ())  # not in threading.enumerate()
    try:
        compiler.worker_pool().shutdown()
    finally:
        release.release()

    assert methods == ["forkserver"]


def test_compile_warnings_come_with_their_line_and_leave_the_bytecode_alone() -> None:
    plain = compiler.compile_module(b"X = 1\n", "m.py")
    code, emitted = compiler.compile_warned(b"X = 1\nY = '\\d'\n", "m.py")

    assert [(line, text.partition(":")[0]) for line, text in emitted] in (
        [(2, "SyntaxWarning")], [(2, "DeprecationWarning")])  # by Python version  # fmt: skip
    assert "invalid escape sequence" in emitted[0][1]
    assert compiler.compile_module(b"X = 1\nY = '\\d'\n", "m.py") == code != plain
    assert compiler.compile_warned(b"X = 1\n", "m.py")[1] == ()


@pytest.mark.usefixtures("pooled")
def test_workers_carry_the_warnings_back(tmp_path: Path) -> None:
    jobs = []
    for i in range(4):
        path = tmp_path / f"w{i}.py"
        path.write_text("R = '\\d'\n")
        jobs.append(Job(("dist", path.name), path, path.name, hints=False))
    pool = Compiler(jobs, level=9, include_source=True)
    try:
        got = [pool.get(job.key, job.path, job.rel, False) for job in jobs]
    finally:
        pool.close()

    assert pool._pool is not None
    assert all(r.warnings and r.warnings[0][0] == 1 for r in got)


GUARDED = b"if __name__ == '__main__':\n    open(__file__).read()\n"


def test_only_a_command_of_the_scripts_root_reports_its_main_guard_reads() -> None:
    rel = "_fsbpyr_scripts/m.py"

    assert compiled(GUARDED, rel, 9, True, True, True).reads == (2,)
    assert compiled(GUARDED, rel, 9, True, True, False).reads == ()  # a sibling, only imported
    assert compiled(GUARDED, "pkg/m.py", 9, True, True, False).reads == ()


@pytest.mark.usefixtures("pooled")
def test_workers_scan_the_main_guard_of_the_commands_only(tmp_path: Path) -> None:
    root = tmp_path / "site"
    package = root / "_fsbpyr_scripts"
    package.mkdir(parents=True)
    for name in ("tool", "helper", "other"):
        (package / f"{name}.py").write_bytes(GUARDED)
    info = root / "ops-0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.4\nName: ops\nVersion: 0\n")
    rows = [f"_fsbpyr_scripts/{name}.py,," for name in ("tool", "helper", "other")]
    (info / "RECORD").write_text("\n".join([*rows, f"{info.name}/METADATA,,"]) + "\n")
    dists = list(SiteIndex.scan(root).dists.values())

    content = classify(dists, extension_suffixes=(".so",), include_source=True, project="ops",
                       entrypoint="_fsbpyr_scripts.tool")  # fmt: skip

    reads = [w.partition(": ")[0] for w in content.warnings if "reads a file next" in w]
    assert reads == ["tool.py:2"]
