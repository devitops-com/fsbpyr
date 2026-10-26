"""Which distributions without a script a tool binary falls through to their requirements: a
meta-package (no code of its own) or a requirement named after it; a library is not a tool."""

from pathlib import Path

import pytest

from fsbpyr.closure import ClosureError, SiteIndex
from fsbpyr.tools import Listed, tool_scripts


def _dist(
    site: Path, name: str, *, requires: tuple[str, ...] = (), console: str = "", code: bool = True
) -> None:
    info = site / f"{name.replace('-', '_')}-1.0.dist-info"
    info.mkdir(parents=True)
    lines = "".join(f"Requires-Dist: {r}\n" for r in requires)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n{lines}")
    record = [f"{info.name}/METADATA,,"]
    if code:
        record.append(f"{name.replace('-', '_')}/__init__.py,,")
    (info / "RECORD").write_text("\n".join(record) + "\n")
    if console:
        (info / "entry_points.txt").write_text(f"[console_scripts]\n{console} = x:main\n")


def _tools(site: Path, *names: str) -> tuple[dict[str, str], tuple[str, ...]]:
    result = tool_scripts(
        SiteIndex.scan(site), [Listed(n) for n in names], project="app", source="requirements.txt",
        python_version="3.12.0")  # fmt: skip
    return result.owners, result.notes


def test_a_library_listed_beside_a_tool_does_not_turn_its_dependencys_script_into_a_command(
    tmp_path: Path
) -> None:
    _dist(tmp_path, "httpie", console="http", requires=("requests",))
    _dist(tmp_path, "requests", requires=("charset-normalizer",))
    _dist(tmp_path, "charset-normalizer", console="normalizer")

    owners, notes = _tools(tmp_path, "httpie", "requests")

    assert owners == {"http": "httpie"}
    assert "requests has no console script and is a library, not a tool" in notes


def test_a_pure_meta_package_gives_the_commands_of_every_requirement(tmp_path: Path) -> None:
    _dist(tmp_path, "jupyter", requires=("notebook", "jupyterlab"), code=False)
    _dist(tmp_path, "notebook", console="jupyter-notebook")
    _dist(tmp_path, "jupyterlab", console="jupyter-lab")

    owners, notes = _tools(tmp_path, "jupyter")

    assert owners == {"jupyter-lab": "jupyterlab", "jupyter-notebook": "notebook"}
    assert any(n.startswith("jupyter has no console script of its own") for n in notes)


def test_a_distribution_with_code_falls_through_only_to_a_requirement_named_after_it(
    tmp_path: Path
) -> None:
    _dist(tmp_path, "ansible", requires=("ansible-core", "jinja2"))
    _dist(tmp_path, "ansible-core", console="ansible-playbook")
    _dist(tmp_path, "jinja2", console="jinja")

    owners, _ = _tools(tmp_path, "ansible")

    assert owners == {"ansible-playbook": "ansible-core"}


def test_a_library_alone_is_an_error(tmp_path: Path) -> None:
    _dist(tmp_path, "requests", requires=("charset-normalizer",))
    _dist(tmp_path, "charset-normalizer", console="normalizer")

    with pytest.raises(ClosureError, match="none has a console or gui script"):
        _tools(tmp_path, "requests")
