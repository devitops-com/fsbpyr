"""Exercises the public import APIs inside a built executable; results are printed as JSON."""

import sys
from typing import Any


def _resources(out: dict[str, Any]) -> None:
    import importlib.resources as res  # noqa: PLC0415

    files = res.files("pkg")
    out["res_iterdir"] = sorted(p.name for p in files.iterdir())
    out["res_data"] = sorted(p.name for p in files.joinpath("data").iterdir())
    out["res_read"] = files.joinpath("data/table.json").read_text()
    out["res_read_bytes"] = (files / "data" / "sub" / "x.txt").read_bytes().decode()
    out["res_is"] = [
        files.joinpath("data").is_dir(),
        files.joinpath("data").is_file(),
        files.joinpath("data/table.json").is_file(),
        files.joinpath("nope").is_file(),
        files.joinpath("nope").is_dir(),
    ]
    with res.as_file(files / "data" / "table.json") as path, open(path) as handle:
        out["as_file"] = handle.read()
    out["read_text_api"] = res.read_text("pkg", "data", "table.json", encoding="utf-8")
    out["module_anchor"] = sorted(p.name for p in res.files("pkg.cli").iterdir())
    out["res_namespace"] = sorted(p.name for p in res.files("ns").iterdir())
    out["res_data_package"] = res.files("pkg.data").joinpath("table.json").read_text()


def _modules(out: dict[str, Any], root: str) -> None:
    import importlib.util  # noqa: PLC0415
    import pkgutil  # noqa: PLC0415

    import ns.a  # noqa: PLC0415
    import ns.b  # noqa: PLC0415
    import pkg  # noqa: PLC0415
    import pkg.cli  # noqa: PLC0415

    out["get_data"] = (pkgutil.get_data("pkg", "data/table.json") or b"").decode()
    out["iter_pkg"] = sorted([m.name, m.ispkg] for m in pkgutil.iter_modules(pkg.__path__, "pkg."))
    out["iter_ns"] = sorted(m.name for m in pkgutil.iter_modules(ns.__path__))
    top = {m.name for m in pkgutil.iter_modules()}
    out["iter_top"] = sorted(n for n in ("pkg", "ns", "json", "probe", "nope") if n in top)
    out["walk"] = sorted(m.name for m in pkgutil.walk_packages(pkg.__path__, "pkg."))
    spec = importlib.util.find_spec("pkg.cli")
    out["find_spec"] = [spec is not None and spec.origin == root + "/pkg/cli.py",
                        importlib.util.find_spec("nope") is None]  # fmt: skip
    out["ns"] = [list(ns.__path__) == [root + "/ns"], ns.a.VALUE, ns.b.VALUE]
    out["file"] = [
        pkg.__file__ == root + "/pkg/__init__.py",
        pkg.cli.__file__ == root + "/pkg/cli.py",
        pkg.__path__ == [root + "/pkg"],
    ]


def _metadata(out: dict[str, Any]) -> None:
    import importlib.metadata as md  # noqa: PLC0415

    out["version"] = [md.version("demo"), md.version("DEMO")]
    out["metadata_name"] = md.metadata("demo")["Name"]
    out["eps"] = sorted(f"{ep.name}={ep.value}" for ep in md.entry_points(group="console_scripts"))
    files = md.files("demo") or []
    out["dist_files"] = sorted(str(f) for f in files)
    out["dist_file_read"] = [f.read_text() for f in files if str(f) == "pkg/data/table.json"]
    try:
        md.version("nope")
        out["missing_dist"] = "found"
    except md.PackageNotFoundError:
        out["missing_dist"] = "missing"


def _sources(out: dict[str, Any], root: str) -> None:
    import encodings  # noqa: PLC0415
    import inspect  # noqa: PLC0415
    import linecache  # noqa: PLC0415

    import pkg.cli  # noqa: PLC0415

    code = pkg.cli.main.__code__
    out["getsource"] = inspect.getsource(pkg.cli.main)
    out["co_filename"] = code.co_filename == root + "/pkg/cli.py"
    out["linecache"] = linecache.getline(code.co_filename, 2, pkg.cli.__dict__).strip()
    out["startup_filename"] = encodings.search_function.__code__.co_filename


def run(boot_modules: list[str]) -> dict[str, Any]:
    import _imp  # noqa: PLC0415

    root = sys.executable
    out: dict[str, Any] = {
        "boot_modules": boot_modules,
        "builtin_or_frozen": sorted(
            {*sys.builtin_module_names, *_imp._frozen_module_names()}  # type: ignore[attr-defined]
        ),
    }
    _resources(out)
    _modules(out, root)
    _metadata(out)
    _sources(out, root)
    return out
