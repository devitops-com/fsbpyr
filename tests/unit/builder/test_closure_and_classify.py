"""Installed-dist index, dependency walk, entry points and file classification (§3.6 4-7)."""

import base64
import hashlib
import json
import marshal
import subprocess
import sys
from pathlib import Path

import pytest

from fsbpyr.classify import ClassifyError, classify
from fsbpyr.closure import ClosureError, SiteIndex, resolve_entry
from fsbpyr.payload.format import FileKind, ModuleKind

PROJECT_DIR = Path("/work/hello")


def _record_line(rel: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{rel},sha256={digest},{len(data)}"


def _dist(
    site: Path,
    name: str,
    version: str,
    files: dict[str, bytes],
    *,
    requires: tuple[str, ...] = (),
    entry_points: str = "",
    direct_url: str | None = None,
) -> None:
    info = f"{name.replace('-', '_')}-{version}.dist-info"
    meta = ["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}"]
    meta += [f"Requires-Dist: {req}" for req in requires]
    contents = dict(files)
    contents[f"{info}/METADATA"] = ("\n".join(meta) + "\n").encode()
    contents[f"{info}/INSTALLER"] = b"uv"
    contents[f"{info}/REQUESTED"] = b""
    contents[f"{info}/uv_cache.json"] = b'{"timestamp": 1}'
    if entry_points:
        contents[f"{info}/entry_points.txt"] = entry_points.encode()
    if direct_url is not None:
        contents[f"{info}/direct_url.json"] = direct_url.encode()
    lines = []
    for rel, data in contents.items():
        path = site / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        lines.append(_record_line(rel, data))
    lines += [f"{info}/RECORD,,", "../../../bin/hello,sha256=x,1"]
    (site / info / "RECORD").write_text("\n".join(lines) + "\n")


@pytest.fixture
def site(tmp_path: Path) -> Path:
    site = tmp_path / "site"
    _dist(
        site,
        "hello-app",
        "1.0",
        {
            "hello_app/__init__.py": b"VERSION = '1.0'\n",
            "hello_app/cli.py": b"def main():\n    return 0\n",
            "hello_app/data/greeting.txt": b"hi",
            "hello_app/__pycache__/cli.cpython-314.pyc": b"stale",
            "hello_app/broken.py": b"def (:\n",
            "hello_app/not-a-module.py": b"x = 1\n",
        },
        requires=("click>=8", "colorama; sys_platform == 'win32'", "rich; extra == 'fancy'"),
        entry_points="[console_scripts]\nhello = hello_app.cli:main\n",
        direct_url=json.dumps({"url": PROJECT_DIR.as_uri(), "dir_info": {"editable": False}}),
    )
    _dist(site, "click", "8.1.8", {"click/__init__.py": b"__version__ = '8.1.8'\n"})
    _dist(site, "rich", "14.0", {"rich/__init__.py": b""}, requires=("pygments",))
    _dist(site, "pygments", "2.19", {"pygments/__init__.py": b""})
    _dist(site, "stray", "0.1", {"stray.py": b"", "stray.pth": b"import stray\n"})
    return site  # fmt: skip


def test_index_finds_project_by_direct_url(site: Path) -> None:
    index = SiteIndex.scan(site)

    assert sorted(index.dists) == ["click", "hello-app", "pygments", "rich", "stray"]
    assert index.project(name=None, directory=PROJECT_DIR).name == "hello-app"
    assert index.project(name="Hello_App", directory=Path("/elsewhere")).name == "hello-app"
    with pytest.raises(ClosureError, match="not installed"):
        index.project(name="other", directory=Path("/elsewhere"))


def test_dependency_walk_honours_markers_and_extras(site: Path) -> None:
    index = SiteIndex.scan(site)
    project = index.project(name="hello-app", directory=PROJECT_DIR)

    plain = index.walk(project, extras=(), python_version="3.14.8")
    fancy = index.walk(project, extras=("fancy",), python_version="3.14.8")

    assert plain.reachable == ("click", "hello-app")
    assert fancy.reachable == ("click", "hello-app", "pygments", "rich")
    assert plain.unreachable == ("pygments", "rich", "stray")


def test_missing_dependency_is_an_error(site: Path) -> None:
    (site / "click-8.1.8.dist-info" / "METADATA").unlink()
    index = SiteIndex.scan(site)

    with pytest.raises(ClosureError, match="click"):
        index.walk(index.project(name="hello-app", directory=PROJECT_DIR), extras=(),
                   python_version="3.14.8")  # fmt: skip


@pytest.mark.parametrize(
    ("entry", "expected"),
    [(None, ("hello", "hello_app.cli:main")), ("hello", ("hello", "hello_app.cli:main")),
     ("hello_app.cli:main", ("main", "hello_app.cli:main")),
     ("hello_app", ("hello_app", "hello_app"))],
)  # fmt: skip
def test_resolve_entry(site: Path, entry: str | None, expected: tuple[str, str]) -> None:
    index = SiteIndex.scan(site)
    project = index.project(name="hello-app", directory=PROJECT_DIR)

    assert resolve_entry(project, entry) == expected


def test_resolve_entry_needs_a_choice_or_a_script(site: Path) -> None:
    index = SiteIndex.scan(site)

    with pytest.raises(ClosureError, match="--entry"):
        resolve_entry(index.dists["click"], None)


def test_classify_builds_modules_files_and_namespaces(site: Path) -> None:
    index = SiteIndex.scan(site)
    dists = [index.dists[n] for n in ("click", "hello-app")]

    content = classify(dists, extension_suffixes=(".so",), include_source=True)

    modules = {m.name: m for m in content.modules}
    assert set(modules) == {"click", "hello_app", "hello_app.cli", "hello_app.data"}
    assert modules["hello_app"].kind is ModuleKind.PACKAGE
    assert modules["hello_app.data"].kind is ModuleKind.NAMESPACE
    assert all(m.dist for m in content.modules)
    code = modules["hello_app.cli"].code
    assert code is not None
    assert marshal.loads(_decompress(code)).co_filename == "hello_app/cli.py"
    files = {f.path: f for f in content.files}
    assert files["hello_app/data/greeting.txt"].kind is FileKind.RESOURCE
    assert files["hello_app/cli.py"].kind is FileKind.SOURCE
    assert files["hello_app/broken.py"].kind is FileKind.RESOURCE  # SyntaxError: kept as data
    assert files["hello_app/not-a-module.py"].kind is FileKind.RESOURCE
    assert not any("__pycache__" in p or p.startswith("..") for p in files)
    assert [w for w in content.warnings if "broken.py" in w]


def test_installer_files_are_dropped_and_record_rewritten(site: Path) -> None:
    index = SiteIndex.scan(site)

    content = classify([index.dists["hello-app"]], extension_suffixes=(".so",),
                       include_source=True)  # fmt: skip

    info = "hello_app-1.0.dist-info"
    paths = {f.path for f in content.files}
    assert {f"{info}/METADATA", f"{info}/RECORD", f"{info}/entry_points.txt"} <= paths
    assert not paths & {f"{info}/{n}" for n in ("INSTALLER", "REQUESTED", "uv_cache.json",
                                                 "direct_url.json")}  # fmt: skip
    record = next(f for f in content.files if f.path == f"{info}/RECORD")
    text = _decompress(record.blob).decode()
    assert "uv_cache.json" not in text and "INSTALLER" not in text
    assert "hello_app/cli.py,sha256=" in text
    # entries that are not packaged depend on the staging location or install time
    assert "../../../bin/hello" not in text and "__pycache__" not in text


def test_classify_without_sources(site: Path) -> None:
    index = SiteIndex.scan(site)

    content = classify([index.dists["hello-app"]], extension_suffixes=(".so",),
                       include_source=False)  # fmt: skip

    assert all(m.source is None for m in content.modules)
    assert not any(f.kind is FileKind.SOURCE for f in content.files)


def test_pth_files_are_reported_not_packaged(site: Path) -> None:
    index = SiteIndex.scan(site)

    content = classify([index.dists["stray"]], extension_suffixes=(".so",), include_source=True)

    assert "stray.pth" not in {f.path for f in content.files}
    assert any("stray.pth" in w for w in content.warnings)


def test_native_extensions_are_rejected_until_phase_5(site: Path) -> None:
    _dist(site, "fast", "1.0", {"fast/_speed.cpython-314-x86_64-linux-gnu.so": b"\x7fELF"})
    index = SiteIndex.scan(site)

    with pytest.raises(ClassifyError, match="_speed"):
        classify([index.dists["fast"]], extension_suffixes=(".cpython-314-x86_64-linux-gnu.so",),
                 include_source=True)  # fmt: skip


def _decompress(blob: object) -> bytes:
    from fsbpyr.payload.spec import Blob  # noqa: PLC0415

    assert isinstance(blob, Blob)
    if blob.codec.name == "NONE":
        return blob.stored
    from compression import zstd  # noqa: PLC0415

    return zstd.decompress(blob.stored)


def test_console_script_extras_are_not_part_of_the_entry_point(site: Path) -> None:
    _dist(site, "tool", "1.0", {"tool/__init__.py": b"def main():\n    pass\n"},
          entry_points="[console_scripts]\ntool = tool:main [fancy, other]\n")  # fmt: skip
    index = SiteIndex.scan(site)

    assert resolve_entry(index.dists["tool"], None) == ("tool", "tool:main")


def test_editable_installs_are_rejected(site: Path) -> None:
    editable = json.dumps({"url": "file:///src/ed", "dir_info": {"editable": True}})
    _dist(site, "ed", "1.0", {"ed.pth": b"/src/ed\n"}, direct_url=editable)
    index = SiteIndex.scan(site)

    with pytest.raises(ClosureError, match="editable"):
        index.check_no_editable()


def test_broken_package_init_does_not_become_a_namespace(site: Path) -> None:
    _dist(site, "py2", "1.0", {"oldpkg/__init__.py": b'print "py2"\n', "oldpkg/mod.py": b"x = 1\n",
                               "oldpkg/sub/data.txt": b"d"})  # fmt: skip
    index = SiteIndex.scan(site)

    content = classify([index.dists["py2"]], extension_suffixes=(".so",), include_source=True)

    names = {m.name for m in content.modules}
    assert "oldpkg" not in names and "oldpkg.sub" not in names  # CPython cannot import it either
    assert any("oldpkg/__init__.py" in w for w in content.warnings)


def test_so_detection_matches_shared_objects_only(site: Path) -> None:
    _dist(site, "notes", "1.0", {"notes/__init__.py": b"", "notes/readme.so.txt": b"text",
                                 "notes.libs/libz.so.1.2.13": b"\x7fELF"})  # fmt: skip
    index = SiteIndex.scan(site)

    with pytest.raises(ClassifyError) as raised:
        classify([index.dists["notes"]], extension_suffixes=(".so",), include_source=True)

    assert "libz.so.1.2.13" in str(raised.value) and "readme.so.txt" not in str(raised.value)


def test_package_wins_over_same_named_module_from_another_dist(site: Path) -> None:
    _dist(site, "a-mod", "1.0", {"clash/__init__.py": b"KIND = 'package'\n"})
    _dist(site, "b-mod", "1.0", {"clash.py": b"KIND = 'module'\n"})
    index = SiteIndex.scan(site)

    content = classify([index.dists["a-mod"], index.dists["b-mod"]], extension_suffixes=(".so",),
                       include_source=True)  # fmt: skip

    clash = next(m for m in content.modules if m.name == "clash")
    assert clash.kind is ModuleKind.PACKAGE
    assert any("clash" in w for w in content.warnings)


def test_record_paths_with_commas_are_parsed_as_csv(site: Path) -> None:
    info = site / "comma-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.4\nName: comma\nVersion: 1.0\n")
    (site / "comma").mkdir()
    (site / "comma" / "a,b.txt").write_text("x")
    (info / "RECORD").write_text('"comma/a,b.txt",sha256=x,1\ncomma-1.0.dist-info/RECORD,,\n')
    index = SiteIndex.scan(site)

    content = classify([index.dists["comma"]], extension_suffixes=(".so",), include_source=True)

    assert "comma/a,b.txt" in {f.path for f in content.files}


def test_bytecode_ignores_the_build_interpreters_optimization_level(tmp_path: Path) -> None:
    script = (
        "from fsbpyr.classify import compile_module\n"
        "import marshal, sys\n"
        'code = marshal.loads(compile_module(b\'"""doc"""\\nassert False\\n\', \'m.py\'))\n'
        "print(code.co_consts[0], 'assert' if 'AssertionError' in str(code.co_names) or "
        "any(isinstance(c, str) and c == 'doc' for c in code.co_consts) else 'stripped')\n"
    )
    result = subprocess.run([sys.executable, "-OO", "-c", script], capture_output=True, text=True,
                            check=True)  # fmt: skip

    assert result.stdout.split()[0] == "doc"
