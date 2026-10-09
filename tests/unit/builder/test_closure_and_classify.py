"""Installed-dist index, dependency walk, entry points and file classification (§3.6 4-7)."""

import base64
import hashlib
import json
import marshal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from fsbpyr.classify import ClassifyError, _digested, classify, compile_module
from fsbpyr.closure import ClosureError, SiteIndex, resolve_entry
from fsbpyr.payload.format import (
    NATIVE_KINDS,
    Codec,
    FileFlag,
    FileKind,
    ModuleFlag,
    ModuleKind,
    tree_digest,
)
from fsbpyr.payload.spec import ZSTD_FAST_LEVEL, ZSTD_LEVEL, Blob

from ...support.apps import library
from ...support.elf import make_elf

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
            "hello_app/not.a.module.py": b"x = 1\n",
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
    assert files["hello_app/not.a.module.py"].kind is FileKind.RESOURCE
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


@pytest.mark.parametrize("level", [ZSTD_FAST_LEVEL, ZSTD_LEVEL])
def test_content_is_compressed_at_the_build_level(site: Path, level: int) -> None:
    text = "\n".join(f"VALUE_{i} = {i * i % 97}  # line {i}" for i in range(4000)).encode()
    native = make_elf() + text  # a library: natives follow the level too
    _dist(site, "big", "1.0", {"big/__init__.py": text, "big/table.txt": text,
                               "big/libbig.so.1": native})  # fmt: skip
    index = SiteIndex.scan(site)

    content = classify([index.dists["big"]], extension_suffixes=SUFFIXES, include_source=True,
                       level=level)  # fmt: skip

    assert Blob.zstd(text, level=ZSTD_FAST_LEVEL).stored != Blob.zstd(text, level=ZSTD_LEVEL).stored
    files = {f.path: f for f in content.files}
    assert files["big/table.txt"].blob.stored == Blob.zstd(text, level=level).stored
    assert files["big/libbig.so.1"].blob.stored == Blob.zstd(native, level=level).stored
    assert files["big/__init__.py"].blob.stored == Blob.zstd(text, level=level).stored  # source
    module = next(m for m in content.modules if m.name == "big")
    assert module.code == Blob.code(compile_module(text, "big/__init__.py"), level=level)


def test_pth_files_are_reported_not_packaged(site: Path) -> None:
    index = SiteIndex.scan(site)

    content = classify([index.dists["stray"]], extension_suffixes=(".so",), include_source=True)

    assert "stray.pth" not in {f.path for f in content.files}
    assert any("stray.pth" in w for w in content.warnings)


EXT = "fast/_speed.cpython-314-x86_64-linux-gnu.so"
SUFFIXES = (".cpython-314-x86_64-linux-gnu.so", ".abi3.so", ".so")


def _native_site(site: Path, ext_needs: tuple[str, ...] = ("libhelper-1.so", "libc.so.6")) -> None:
    _dist(site, "fast", "1.0", {
        "fast/__init__.py": b"",
        EXT: make_elf(needed=ext_needs, rpath="$ORIGIN/../fast.libs"),
        "fast.libs/libhelper-1.so": make_elf(needed=("libm.so.6",)),
        "fast/plugins.abi3.so": make_elf(),
        "fast/data.so.txt": b"not native",
    })  # fmt: skip


def test_native_extensions_become_extension_modules_with_closures(site: Path) -> None:
    _native_site(site)
    index = SiteIndex.scan(site)

    content = classify([index.dists["fast"]], extension_suffixes=SUFFIXES, include_source=True)

    modules = {m.name: m for m in content.modules}
    assert modules["fast._speed"].kind is ModuleKind.EXTENSION
    assert modules["fast._speed"].file == EXT and modules["fast._speed"].code is None
    assert modules["fast.plugins"].kind is ModuleKind.EXTENSION
    files = {f.path: f for f in content.files}
    ext = files[EXT]
    assert ext.kind is FileKind.NATIVE_EXT and ext.closure == ("fast.libs/libhelper-1.so",)
    assert ext.blob.sha256 == tree_digest(ext.blob.content()) and ext.mode & 0o500 == 0o500
    lib = files["fast.libs/libhelper-1.so"]
    assert lib.kind is FileKind.NATIVE_LIB and lib.blob.sha256 == tree_digest(lib.blob.content())
    assert files["fast/data.so.txt"].kind is FileKind.RESOURCE


def test_unresolved_native_dependency_fails_the_build(site: Path) -> None:
    _native_site(site, ext_needs=("libssl.so.3",))
    index = SiteIndex.scan(site)

    with pytest.raises(ClassifyError, match=r"libssl\.so\.3"):
        classify([index.dists["fast"]], extension_suffixes=SUFFIXES, include_source=True)

    content = classify([index.dists["fast"]], extension_suffixes=SUFFIXES, include_source=True,
                       allowed_system_libs=frozenset({"libssl.so.3"}))  # fmt: skip
    assert any(m.name == "fast._speed" for m in content.modules)


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


def test_hyphenated_module_files_are_modules(site: Path) -> None:
    _dist(site, "tables", "1.0", {"tables/__init__.py": b"", "tables/unicode17-0-0.py": b"W = 1\n",
                                  "tables/a-b.py": b"", "tables/a_b.py": b"",
                                  "tables/my-dir/__init__.py": b"",
                                  "tables/my-dir/x.py": b"y = 2\n"})  # fmt: skip
    index = SiteIndex.scan(site)

    content = classify([index.dists["tables"]], extension_suffixes=SUFFIXES, include_source=True)

    names = {m.name for m in content.modules}
    assert "tables.unicode17-0-0" in names  # import_module() reaches it (rich's width tables)
    assert {"tables.a-b", "tables.a_b"} <= names  # distinct modules, as in CPython
    assert not any("my-dir" in name for name in names)  # hyphenated directories stay data
    assert {f.path: f.kind for f in content.files}["tables/my-dir/x.py"] is FileKind.RESOURCE


def test_so_detection_matches_shared_objects_only(site: Path) -> None:
    _dist(site, "notes", "1.0", {"notes/__init__.py": b"", "notes/readme.so.txt": b"text",
                                 "notes.libs/libz.so.1.2.13": b"\x7fELF"})  # fmt: skip
    index = SiteIndex.scan(site)

    content = classify([index.dists["notes"]], extension_suffixes=(".so",), include_source=True)

    kinds = {f.path: f.kind for f in content.files}
    assert kinds["notes.libs/libz.so.1.2.13"] is FileKind.NATIVE_LIB
    assert kinds["notes/readme.so.txt"] is FileKind.RESOURCE


def test_extension_wins_over_same_named_source_module(site: Path) -> None:
    _dist(site, "dual", "1.0", {"dual/__init__.py": b"", "dual/_impl.py": b"PURE = True\n",
                                "dual/_impl.abi3.so": make_elf(),
                                f"dual/_impl{SUFFIXES[0]}": make_elf()})  # fmt: skip
    index = SiteIndex.scan(site)

    content = classify([index.dists["dual"]], extension_suffixes=SUFFIXES, include_source=True)

    assert not [w for w in content.warnings if "provided by" in w]  # one dist: a fallback
    impl = next(m for m in content.modules if m.name == "dual._impl")
    assert impl.kind is ModuleKind.EXTENSION  # CPython's FileFinder order, first suffix first
    assert impl.file == "dual/_impl.cpython-314-x86_64-linux-gnu.so"
    kinds = {f.path: f.kind for f in content.files}
    assert kinds["dual/_impl.abi3.so"] is FileKind.NATIVE_LIB  # shadowed, never imported
    assert kinds["dual/_impl.py"] is FileKind.RESOURCE


def test_package_directory_wins_over_same_named_extension(site: Path) -> None:
    _dist(site, "both", "1.0", {"both/__init__.py": b"", f"both{SUFFIXES[0]}": make_elf(),
                                "both/libbar-2.so": make_elf()})  # fmt: skip
    index = SiteIndex.scan(site)

    content = classify([index.dists["both"]], extension_suffixes=SUFFIXES, include_source=True)

    assert {m.name: m.kind for m in content.modules} == {"both": ModuleKind.PACKAGE}
    kinds = {f.path: f.kind for f in content.files}
    assert kinds[f"both{SUFFIXES[0]}"] is FileKind.NATIVE_LIB  # shadowed by the package
    assert kinds["both/libbar-2.so"] is FileKind.NATIVE_LIB  # not an importable name


def test_native_files_too_large_to_load_fail_the_build(
    site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _native_site(site)
    monkeypatch.setattr("fsbpyr.classify.MAX_MEMORY_BLOB", 100)
    index = SiteIndex.scan(site)

    with pytest.raises(ClassifyError, match="too large"):
        classify([index.dists["fast"]], extension_suffixes=SUFFIXES, include_source=True)


def test_same_dist_duplicates_warn_unless_extension_and_fallback(site: Path) -> None:
    _dist(site, "twice", "1.0", {"twice.py": b"", "twice/__init__.py": b""})
    index = SiteIndex.scan(site)

    content = classify([index.dists["twice"]], extension_suffixes=SUFFIXES, include_source=True)

    assert any("twice is provided by twice and twice" in w for w in content.warnings)


def test_compiled_package_init_is_an_extension_package(site: Path) -> None:
    _dist(site, "cy", "1.0", {f"cy/__init__{SUFFIXES[0]}": make_elf(), "cy/__init__.py": b"",
                              "cy/helpers.py": b"X = 1\n"})  # fmt: skip
    index = SiteIndex.scan(site)

    content = classify([index.dists["cy"]], extension_suffixes=SUFFIXES, include_source=True)

    modules = {m.name: m for m in content.modules}
    assert modules["cy"].kind is ModuleKind.EXTENSION  # beats __init__.py, as in FileFinder
    assert modules["cy"].file == f"cy/__init__{SUFFIXES[0]}"
    assert modules["cy.helpers"].kind is ModuleKind.MODULE


def test_non_elf_shared_object_is_kept_as_data(site: Path) -> None:
    _dist(site, "odd", "1.0", {"odd/__init__.py": b"", "odd/_stub.so": b"INPUT(-lfoo)\n"})
    index = SiteIndex.scan(site)

    content = classify([index.dists["odd"]], extension_suffixes=SUFFIXES, include_source=True)

    assert {f.path: f.kind for f in content.files}["odd/_stub.so"] is FileKind.RESOURCE
    assert all(m.name != "odd._stub" for m in content.modules)
    assert any("not an ELF" in w for w in content.warnings)


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


# --- FS_REQUIRED packages ------------------------------------------------------------------


def test_fs_packages_mark_every_file_below_them(site: Path) -> None:
    _dist(site, "tpl", "1.0", {"tpl/__init__.py": b"", "tpl/templates/a.txt": b"A",
                               "tpl/tool.sh": b"#!/bin/sh\n", "tpl/sub/__init__.py": b"",
                               "other/x.txt": b"x"})  # fmt: skip
    (site / "tpl" / "tool.sh").chmod(0o775)
    index = SiteIndex.scan(site)

    content = classify([index.dists["tpl"]], extension_suffixes=SUFFIXES, include_source=True,
                       fs_packages=frozenset({"tpl"}))  # fmt: skip

    modules = {m.name: m for m in content.modules}
    assert ModuleFlag.FS_REQUIRED_ROOT in modules["tpl"].flags
    assert ModuleFlag.FS_REQUIRED_ROOT not in modules["tpl.sub"].flags
    files = {f.path: f for f in content.files}
    for rel in ("tpl/__init__.py", "tpl/templates/a.txt", "tpl/tool.sh", "tpl/sub/__init__.py"):
        blob = files[rel].blob
        assert FileFlag.FS_REQUIRED in files[rel].flags and blob.sha256 == tree_digest(
            blob.content()
        )
    assert (files["tpl/tool.sh"].mode, files["tpl/templates/a.txt"].mode) == (0o755, 0o644)
    assert files["other/x.txt"].flags == FileFlag(0)
    assert modules["tpl"].source is files["tpl/__init__.py"].blob


@pytest.mark.parametrize(("name", "message"), [("missing", "not a package"),
                                               ("nsdir", "not a regular package")])  # fmt: skip
def test_fs_packages_must_be_regular_packages_of_the_app(
    site: Path, name: str, message: str
) -> None:
    _dist(site, "app", "1.0", {"app/__init__.py": b"", "nsdir/data.txt": b"d"})
    index = SiteIndex.scan(site)

    with pytest.raises(ClassifyError, match=f"fs-packages: {name!r} is {message}"):
        classify([index.dists["app"]], extension_suffixes=SUFFIXES, include_source=True,
                 fs_packages=frozenset({name}))  # fmt: skip


def test_fs_packages_nested_with_natives_and_without_sources(site: Path) -> None:
    _dist(site, "tpl", "1.0", {"tpl/__init__.py": b"", "tpl/sub/__init__.py": b"",
                               "tpl/sub/x.txt": b"x", "tpl/_c.abi3.so": make_elf()})  # fmt: skip
    index = SiteIndex.scan(site)

    content = classify([index.dists["tpl"]], extension_suffixes=SUFFIXES, include_source=False,
                       fs_packages=frozenset({"tpl", "tpl.sub"}))  # fmt: skip

    roots = {m.name for m in content.modules if ModuleFlag.FS_REQUIRED_ROOT in m.flags}
    assert roots == {"tpl", "tpl.sub"}
    files = {f.path: f for f in content.files}
    assert not any(f.kind is FileKind.SOURCE for f in files.values())  # --no-source
    assert FileFlag.FS_REQUIRED in files["tpl/sub/x.txt"].flags
    native = files["tpl/_c.abi3.so"]  # extracted with the package as a native file anyway
    assert native.kind is FileKind.NATIVE_EXT and native.blob.sha256 is not None


def test_certifi_is_fs_required_without_configuration(site: Path) -> None:
    _dist(site, "certifi", "2026.1.1", {"certifi/__init__.py": b"", "certifi/cacert.pem": b"P"})
    _dist(site, "app", "1.0", {"app/__init__.py": b""})
    index = SiteIndex.scan(site)

    with_certifi = classify(list(index.dists.values()), extension_suffixes=SUFFIXES,
                            include_source=False)  # fmt: skip
    without = classify([index.dists["app"]], extension_suffixes=SUFFIXES, include_source=False)

    certifi = next(m for m in with_certifi.modules if m.name == "certifi")
    assert ModuleFlag.FS_REQUIRED_ROOT in certifi.flags
    pem = next(f for f in with_certifi.files if f.path == "certifi/cacert.pem")
    assert FileFlag.FS_REQUIRED in pem.flags
    assert all(m.flags == ModuleFlag(0) for m in without.modules)


def test_litellm_is_fs_required_without_configuration(site: Path) -> None:
    _dist(site, "litellm", "1.100.1", {"litellm/__init__.py": b"",
                                       "litellm/containers/endpoints.json": b"{}"})  # fmt: skip
    index = SiteIndex.scan(site)

    content = classify([index.dists["litellm"]], extension_suffixes=SUFFIXES, include_source=True)

    litellm = next(m for m in content.modules if m.name == "litellm")
    assert ModuleFlag.FS_REQUIRED_ROOT in litellm.flags  # opens files next to its modules
    endpoints = next(f for f in content.files if f.path == "litellm/containers/endpoints.json")
    assert FileFlag.FS_REQUIRED in endpoints.flags


def test_torch_is_fs_required_without_configuration(site: Path) -> None:
    """torch runs bin/torch_shm_manager and loads lib/libtorch_global_deps.so by paths next to
    its __file__ as it is imported (torch 2.14)."""
    _dist(site, "torch", "2.14.1", {"torch/__init__.py": b"", "torch/bin/torch_shm_manager": b"x"})
    index = SiteIndex.scan(site)

    content = classify([index.dists["torch"]], extension_suffixes=SUFFIXES, include_source=False)

    torch = next(m for m in content.modules if m.name == "torch")
    assert ModuleFlag.FS_REQUIRED_ROOT in torch.flags
    manager = next(f for f in content.files if f.path == "torch/bin/torch_shm_manager")
    assert FileFlag.FS_REQUIRED in manager.flags


# --- .pth files ------------------------------------------------------------------------------


def test_pth_path_lines_make_modules_importable_by_relative_name(site: Path) -> None:
    _dist(site, "vendored", "1.0", {
        "vendored.pth": b"# comment\n\nvendored_libs\n../outside\n",
        "vendored_libs/helper.py": b"X = 1\n",
        "vendored_libs/bar/__init__.py": b"",
        "vendored_libs/bar/baz.py": b"",
        "vendored_libs/clash.py": b"WHERE = 'pth'\n",
        "vendored_libs/bar/plug/m.py": b"",
        "clash.py": b"WHERE = 'site'\n",
        ".hidden.pth": b"hidden_libs\n",
        "hidden_libs/secret.py": b"",
    })  # fmt: skip
    index = SiteIndex.scan(site)

    content = classify([index.dists["vendored"]], extension_suffixes=SUFFIXES, include_source=True)

    paths = {m.name: m.path for m in content.modules}
    assert paths["helper"] == "vendored_libs/helper.py"
    assert paths["bar"] == "vendored_libs/bar/__init__.py"
    assert paths["bar.baz"] == "vendored_libs/bar/baz.py"
    assert paths["clash"] == "clash.py"  # site-packages comes first on sys.path
    kinds = {m.name: m.kind for m in content.modules}
    assert (
        kinds["bar.plug"] is ModuleKind.NAMESPACE and paths["bar.plug"] == "vendored_libs/bar/plug"
    )
    assert paths["bar.plug.m"] == "vendored_libs/bar/plug/m.py"
    assert "secret" not in paths  # site.py skips .pth files whose name starts with "."
    assert "vendored.pth" not in {f.path for f in content.files}
    assert any("../outside" in w and "outside site-packages" in w for w in content.warnings)


def test_pth_code_lines_warn_unless_known_to_be_redundant(site: Path) -> None:
    _dist(site, "hooks", "1.0", {"hooks.pth": b"import hooks; hooks.install()\n",
                                 "hooks.py": b"", "zope.interface-5-nspkg.pth": b"import sys\n",
                                 "distutils-precedence.pth": b"import os\n"})  # fmt: skip
    index = SiteIndex.scan(site)

    content = classify([index.dists["hooks"]], extension_suffixes=SUFFIXES, include_source=True)

    pth_warnings = [w for w in content.warnings if ".pth" in w]
    assert len(pth_warnings) == 1 and "hooks.pth" in pth_warnings[0]
    assert "not run" in pth_warnings[0]


# --- build hints -----------------------------------------------------------------------------

READS_DATA = b"import os\nDATA = open(os.path.join(os.path.dirname(__file__), 'd.txt')).read()\n"


def test_reads_next_to_file_are_reported_unless_fs_required(site: Path) -> None:
    _dist(site, "datapkg", "1.0", {"datapkg/__init__.py": READS_DATA, "datapkg/d.txt": b"d",
                                   "datapkg/util.py": READS_DATA,
                                   "datapkg/tests/test_x.py": READS_DATA})  # fmt: skip
    index = SiteIndex.scan(site)

    quiet = classify([index.dists["datapkg"]], extension_suffixes=SUFFIXES, include_source=True)
    plain = classify([index.dists["datapkg"]], extension_suffixes=SUFFIXES, include_source=True,
                     hints=True)  # fmt: skip
    fs = classify([index.dists["datapkg"]], extension_suffixes=SUFFIXES, include_source=True,
                  fs_packages=frozenset({"datapkg"}), hints=True)  # fmt: skip

    assert not [w for w in quiet.warnings if "__file__" in w]  # a guess: only with --hints
    hints = [w for w in plain.warnings if "__file__" in w]
    assert len(hints) == 1  # one line for the whole build
    assert "datapkg (datapkg/__init__.py:2)" in hints[0] and "fs-packages" in hints[0]
    assert not [w for w in fs.warnings if "__file__" in w]


def test_file_read_hints_name_a_package_fs_packages_accepts(site: Path) -> None:
    _dist(site, "google-cloud-x", "1.0", {"google/cloud/x/__init__.py": b"",
                                          "google/cloud/x/res.py": READS_DATA})  # fmt: skip
    index = SiteIndex.scan(site)

    content = classify([index.dists["google-cloud-x"]], extension_suffixes=SUFFIXES,
                       include_source=True, hints=True)  # fmt: skip

    (hint,) = [w for w in content.warnings if "__file__" in w]
    assert "google.cloud.x (google/cloud/x/res.py:2)" in hint  # not the namespace 'google'
    classify([index.dists["google-cloud-x"]], extension_suffixes=SUFFIXES, include_source=True,
             fs_packages=frozenset({"google.cloud.x"}))  # fmt: skip  # the advice works


def test_shared_objects_python_code_names_are_reported(site: Path) -> None:
    extension = make_elf(soname="PyInit__ext")  # the symbol name CPython looks up
    loader = b"import ctypes, os\nctypes.CDLL(os.path.join(HERE, 'libplugin.so.2'))\n"
    _dist(site, "tool", "1.0", {"tool/__init__.py": loader, "tool/libplugin.so.2": make_elf(),
                                "tool/libcapi.so.1": make_elf(),  # nothing names it
                                "tool/libplugin_cuda.so.1": make_elf(),  # only a longer name
                                f"tool/_ext{SUFFIXES[0]}": extension})  # fmt: skip
    index = SiteIndex.scan(site)

    plain = classify([index.dists["tool"]], extension_suffixes=SUFFIXES, include_source=True)
    fs = classify([index.dists["tool"]], extension_suffixes=SUFFIXES, include_source=True,
                  fs_packages=frozenset({"tool"}))  # fmt: skip

    hints = [w for w in plain.warnings if "no extension" in w]
    assert len(hints) == 1 and "fs-packages" in hints[0]
    assert "tool/libplugin.so.2" in hints[0]
    assert "libcapi" not in hints[0] and "libplugin_cuda" not in hints[0]
    assert not [w for w in fs.warnings if "no extension" in w]


def test_shared_objects_an_extension_names_are_extracted_with_it(site: Path) -> None:
    extension = make_elf(soname="PyInit__ext") + b"\0libplugin.so\0"  # dlopen'ed by name
    _dist(site, "tool", "1.0", {"tool/__init__.py": b"", "tool/libplugin.so": make_elf(),
                                f"tool/_ext{SUFFIXES[0]}": extension})  # fmt: skip
    index = SiteIndex.scan(site)

    content = classify([index.dists["tool"]], extension_suffixes=SUFFIXES, include_source=True)

    files = {f.path: f for f in content.files}
    assert files[f"tool/_ext{SUFFIXES[0]}"].closure == ("tool/libplugin.so",)
    assert not [w for w in content.warnings if "no extension" in w]


def test_libraries_another_distribution_loads_are_reported(site: Path) -> None:
    _dist(site, "foo-libs", "1.0", {"foo_libs/libfoo.so.2": make_elf()})  # no Python code
    _dist(site, "foo", "1.0", {"foo/__init__.py": b"ctypes.CDLL(LIBS / 'libfoo.so.2')\n"})
    index = SiteIndex.scan(site)

    content = classify([index.dists["foo-libs"], index.dists["foo"]],
                       extension_suffixes=SUFFIXES, include_source=True)  # fmt: skip

    hints = [w for w in content.warnings if "no extension" in w]
    assert len(hints) == 1 and "foo_libs/libfoo.so.2" in hints[0]


def test_pth_files_with_the_same_name_or_missing_do_not_break_the_build(site: Path) -> None:
    _dist(site, "one", "1.0", {"shared.pth": b"one_libs\n", "one_libs/a.py": b""})
    _dist(site, "two", "1.0", {"shared.pth": b"two_libs\n", "two_libs/b.py": b"",
                               "gone.pth": b""})  # fmt: skip
    (site / "gone.pth").unlink()
    index = SiteIndex.scan(site)

    content = classify(list(index.dists.values()), extension_suffixes=SUFFIXES,
                       include_source=True)  # fmt: skip

    assert any("gone.pth" in w and "missing" in w for w in content.warnings)


def test_the_highest_glibc_requirement_of_native_files_is_recorded(site: Path) -> None:
    _dist(site, "fast", "1.0", {"fast/__init__.py": b"",
                                EXT: make_elf(versions=(("libc.so.6", ("GLIBC_2.17",)),)),
                                "fast/_new.abi3.so": make_elf(
                                    versions=(("libc.so.6", ("GLIBC_2.34",)),))})  # fmt: skip
    index = SiteIndex.scan(site)

    content = classify([index.dists["fast"]], extension_suffixes=SUFFIXES, include_source=True)

    assert content.glibc == ((2, 34), "fast/_new.abi3.so")


def test_digests_of_files_needed_on_disk_come_from_their_content() -> None:
    """Whatever digest a blob arrives with, the one recorded is computed from its bytes."""
    stale = replace(Blob.zstd(b"data", digest=True), sha256=bytes(32))

    assert _digested(stale).sha256 == tree_digest(b"data")


def test_best_compression_stores_native_files_as_xz(site: Path) -> None:
    """D33: natives only, with their digests; everything else stays zstd."""
    big = "big/_big.cpython-314-x86_64-linux-gnu.so"
    _dist(site, "big", "1.0", {"big/__init__.py": b"", big: make_elf() + bytes(200_000),
                               "big/data.txt": b"x" * 5000})  # fmt: skip
    _native_site(site)
    index = SiteIndex.scan(site)
    dists = [index.dists["big"], index.dists["fast"]]

    files = {f.path: f for f in classify(dists, extension_suffixes=SUFFIXES,
                                         include_source=True, xz_natives=True).files}  # fmt: skip

    natives = {path: f for path, f in files.items() if f.kind in NATIVE_KINDS}
    assert len(natives) == 4 and files[big].blob.codec is Codec.XZ
    assert all(f.blob.codec in (Codec.XZ, Codec.NONE) for f in natives.values())
    assert all(f.blob.sha256 == tree_digest(f.blob.content()) for f in natives.values())
    assert files["big/data.txt"].blob.codec is Codec.ZSTD


def test_best_compression_uses_at_most_the_usable_cores(
    site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    workers: list[int] = []

    def recorded(max_workers: int) -> ThreadPoolExecutor:
        workers.append(max_workers)
        return ThreadPoolExecutor(max_workers)

    monkeypatch.setattr("fsbpyr.classify.ThreadPoolExecutor", recorded)
    _native_site(site)
    dists = [SiteIndex.scan(site).dists["fast"]]
    for cores in (3, 64):
        monkeypatch.setattr("os.process_cpu_count", lambda cores=cores: cores)
        classify(dists, extension_suffixes=SUFFIXES, include_source=True, xz_natives=True)

    assert workers == [3, 8]


def test_best_compression_stores_large_natives_as_pieces(site: Path) -> None:
    """The pieces of every file go through one pool; the result is what Blob.xz gives."""
    big = "big/_big.cpython-314-x86_64-linux-gnu.so"
    data = make_elf() + library(2300)  # over two pieces
    _dist(site, "big", "1.0", {"big/__init__.py": b"", big: data})
    dists = [SiteIndex.scan(site).dists["big"]]

    files = {f.path: f for f in classify(dists, extension_suffixes=SUFFIXES,
                                         include_source=True, xz_natives=True).files}  # fmt: skip

    blob = files[big].blob
    assert blob.codec is Codec.PIECES and len(blob.pieces) == 3
    assert blob == Blob.xz(data, digest=True) and blob.content() == data
