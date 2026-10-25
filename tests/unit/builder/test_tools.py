"""The tool binary (research candidate 10): which distributions a lock source names, which of them
are the tools, and the commands they give. The staged site is built by hand."""

from pathlib import Path

import pytest
from packaging.markers import Marker

from fsbpyr.build import load_package
from fsbpyr.closure import ClosureError, InstalledDist, SiteIndex, resolve_entry
from fsbpyr.config import BuildConfig
from fsbpyr.loose import Package, directory_name
from fsbpyr.project import LockSource, Project, ProjectError
from fsbpyr.tools import Listed, listed_distributions, tool_scripts, with_commands

PYTHON = "3.14.0"


def _dist(
    name: str,
    requires: tuple[str, ...] = (),
    scripts: str = "",
    gui: str = "",
    *,
    code: bool = False,
) -> InstalledDist:
    entry_points = (f"[console_scripts]\n{scripts}\n" if scripts else "") + (
        f"[gui_scripts]\n{gui}\n" if gui else "")
    return InstalledDist(name=name, version="1.0", dist_info=f"{name}-1.0.dist-info",
                         site=Path("/site"), requires=requires,
                         record=(f"{name}/__init__.py,,",) if code else (),
                         entry_points=entry_points, direct_url=None)  # fmt: skip


def _index(*dists: InstalledDist) -> SiteIndex:
    return SiteIndex(Path("/site"), {dist.name: dist for dist in dists})


def _listed(tmp_path: Path, text: str | None = None, name: str = "requirements.txt"
            ) -> list[Listed]:  # fmt: skip
    if text is not None:
        (tmp_path / name).write_text(text)
    project = Project.load(tmp_path)
    source = LockSource.PYLOCK if name == "pylock.toml" else LockSource.REQUIREMENTS
    return listed_distributions(project, source, None if name == "pylock.toml" else name)


def _listed_by(names: list[str]) -> list[Listed]:
    return [Listed(name) for name in names]


def _names(listed: list[Listed]) -> list[str]:
    return [item.name for item in listed]


# --- the trigger -----------------------------------------------------------------------------


@pytest.mark.parametrize("lock", ["requirements.txt", "pylock.toml"])
def test_a_directory_with_no_python_file_and_a_lock_source_is_a_tool_binary(
    tmp_path: Path, lock: str
) -> None:
    (tmp_path / lock).write_text("")

    project = Project.load(tmp_path)

    assert (project.tools, project.loose, project.bare) == (True, True, True)
    assert project.name == directory_name(tmp_path)


def test_an_explicit_requirements_file_makes_a_tool_binary_of_a_directory_without_one(
    tmp_path: Path,
) -> None:
    (tmp_path / "tools.txt").write_text("")

    assert Project.load(tmp_path, "tools.txt").tools


def test_the_requirements_setting_of_a_pyproject_of_settings_makes_one(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.fsbpyr]\nrequirements = "tools.txt"\n')

    assert Project.load(tmp_path).tools


def test_without_a_lock_source_a_directory_without_python_files_is_still_an_error(
    tmp_path: Path,
) -> None:
    (tmp_path / "notes.txt").write_text("x")

    with pytest.raises(ProjectError, match="no Python files"):
        Project.load(tmp_path)


def test_a_python_file_keeps_the_directory_of_scripts(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("pyflakes==3.2.0\n")
    (tmp_path / "run.py").write_text("if __name__ == '__main__':\n    pass\n")

    project = Project.load(tmp_path)

    assert not project.tools and project.scripts == {"run": "_fsbpyr_scripts.run"}


def test_a_project_table_with_a_lock_and_no_python_file_is_not_a_tool_binary(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1"\n')
    (tmp_path / "poetry.lock").write_text("")

    project = Project.load(tmp_path)

    assert not project.tools
    assert project.lock_source() is LockSource.POETRY_LOCK
    with pytest.raises(ProjectError, match="has no Python files to package"):
        Package.load(tmp_path)


def test_a_venv_beside_the_requirements_of_a_tool_binary_changes_nothing(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("pyflakes==3.2.0\n")
    info = tmp_path / ".venv" / "lib" / "python3.14" / "site-packages" / "other-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: other\nVersion: 1.0\n")
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("")

    project = Project.load(tmp_path)

    assert project.tools and project.lock_source() is LockSource.REQUIREMENTS
    assert _names(listed_distributions(project, LockSource.REQUIREMENTS, None)) == ["pyflakes"]


def test_the_empty_project_is_named_after_the_directory_unless_a_tool_has_that_name(
    tmp_path: Path,
) -> None:
    (tmp_path / "requirements.txt").write_text("httpie==3.2.4\n")
    project = Project.load(tmp_path)

    listed = listed_distributions(project, LockSource.REQUIREMENTS, None)
    plain = load_package(project, BuildConfig(), [])
    name = directory_name(tmp_path)
    taken = Package.tool_binary(tmp_path, {name})

    assert _names(listed) == ["httpie"]
    assert plain is not None and plain.tools and plain.name == name
    assert taken.name == f"{name}-tools" and not taken.sources and not taken.commands


# --- the names a lock source gives -----------------------------------------------------------


def test_a_requirements_file_names_its_distributions_and_follows_includes(tmp_path: Path) -> None:
    (tmp_path / "base.txt").write_text("Jinja2==3.1.4  # a name\n")
    (tmp_path / "requirements.txt").write_text(
        "# tools\n--index-url https://example.invalid/simple\n-c constraints.txt\n"
        "-r base.txt\nansible_core==2.17.* \\\n    --hash=sha256:" + "a" * 64 + "\n"
        "httpie[socks]>=3 ; python_version >= '3.8'\n"
        "tool @ https://example.invalid/tool-1.0-py3-none-any.whl\n"
        "https://example.invalid/Other_Tool-2.0.tar.gz\n"
        "-e git+https://example.invalid/r.git@" + "a" * 40 + "#egg=Editable.Thing\n")

    listed = _listed(tmp_path)

    assert _names(listed) == ["jinja2", "ansible-core", "httpie", "tool", "other-tool",
                              "editable-thing"]  # fmt: skip
    assert listed[2].extras == {"socks"} and listed[2].marker is not None


def test_an_include_without_a_space_is_followed(tmp_path: Path) -> None:
    (tmp_path / "base.txt").write_text("jinja2==3.1.4\n")
    (tmp_path / "requirements.txt").write_text("-rbase.txt\n")

    assert _names(_listed(tmp_path)) == ["jinja2"]


def test_a_line_that_names_no_distribution_is_an_error_with_its_place(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match=r"requirements.txt:2: cannot tell which"):
        _listed(tmp_path, "click==8\n./local/dir\n")


def test_an_include_that_loops_or_is_missing_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="includes itself"):
        _listed(tmp_path, "-r requirements.txt\n")
    with pytest.raises(ProjectError, match="cannot read requirements file"):
        _listed(tmp_path, "-r nothing.txt\n")


def test_a_pylock_names_its_packages(tmp_path: Path) -> None:
    text = ('lock-version = "1.0"\n[[packages]]\nname = "Tool_A"\nversion = "1"\n'
            '[[packages]]\nname = "dep"\nmarker = "os_name == \'nt\'"\n')

    listed = _listed(tmp_path, text, "pylock.toml")

    assert _names(listed) == ["tool-a", "dep"] and listed[1].marker is not None


# --- the tools -------------------------------------------------------------------------------


def test_the_distributions_a_short_file_names_are_all_tools_and_a_dependency_is_not() -> None:
    scripts = "ansible = ansible.cli:main\nansible-doc = a.doc:main"
    index = _index(
        _dist("ansible-core", ("jinja2>=3",), scripts),
        _dist("jinja2", (), "jinja = jinja2.cli:main"),
        _dist("httpie", ("requests",), "http = httpie.core:main"),
        _dist("requests"),
    )
    listed = _listed_by(["ansible-core", "httpie"])

    tools = tool_scripts(index, listed, project="tools", source="requirements.txt",
                         python_version=PYTHON)  # fmt: skip

    assert tools.scripts == {"ansible": "ansible.cli:main", "ansible-doc": "a.doc:main",
                             "http": "httpie.core:main"}  # fmt: skip
    assert tools.owners["http"] == "httpie"
    assert tools.notes[0].startswith("the tools are the 2 distributions requirements.txt names")
    assert "commands: ansible, ansible-doc (ansible-core 1.0); http (httpie 1.0)" in tools.notes[-1]


def test_a_file_of_every_installed_distribution_has_only_those_no_other_requires_as_tools() -> None:
    index = _index(
        _dist("flake8", ("pycodestyle", "pyflakes<3.3", "mccabe"), "flake8 = flake8.main:main"),
        _dist("pycodestyle", (), "pycodestyle = pycodestyle:_main"),
        _dist("pyflakes", (), "pyflakes = pyflakes.api:main"),
        _dist("mccabe"),
        _dist("tools"),
    )
    listed = _listed_by(["mccabe", "pycodestyle", "flake8", "pyflakes"])

    tools = tool_scripts(index, listed, project="tools", source="requirements.txt",
                         python_version=PYTHON)  # fmt: skip

    assert tools.scripts == {"flake8": "flake8.main:main"}
    assert tools.notes[0] == ("requirements.txt lists all 4 installed distributions, so the "
                              "tools are the 1 that no other listed distribution requires: "
                              "flake8")  # fmt: skip


def test_a_freeze_that_leaves_out_pip_and_setuptools_still_has_only_the_tool() -> None:
    index = _index(
        _dist("httpie", ("pygments", "setuptools", "pip"), "http = httpie.__main__:main"),
        _dist("pygments", (), "pygmentize = pygments.cmdline:main"),
        _dist("setuptools"),
        _dist("pip"),
        _dist("tools"),
    )

    tools = tool_scripts(index, _listed_by(["pygments", "httpie"]), project="tools",
                         source="requirements.txt", python_version=PYTHON)  # fmt: skip

    assert tools.scripts == {"http": "httpie.__main__:main"}


def test_a_requirement_under_a_false_marker_or_an_unasked_extra_does_not_hide_a_tool() -> None:
    index = _index(
        _dist("a", ("b; python_version < '3.8'", "c; extra == 'more'"), "a = a:main"),
        _dist("b", (), "b = b:main"),
        _dist("c", (), "c = c:main"),
    )

    tools = tool_scripts(index, _listed_by(["a", "b", "c"]), project="p",
                         source="r.txt", python_version=PYTHON)  # fmt: skip

    assert sorted(tools.scripts) == ["a", "b", "c"]


def test_an_extra_asked_for_makes_its_dependency_a_dependency() -> None:
    index = _index(_dist("a", ("c; extra == 'more'",), "a = a:main"), _dist("c", (), "c = c:main"))

    tools = tool_scripts(index, [Listed("a", frozenset({"more"})), Listed("c")], project="p",
                         source="r.txt", python_version=PYTHON)  # fmt: skip

    assert sorted(tools.scripts) == ["a"]
    assert tools.requires == ("a[more]", "c")


def test_two_tools_with_one_script_name_are_an_error_naming_both() -> None:
    index = _index(_dist("one", (), "run = one:main"), _dist("two", (), "run = two:main"))

    with pytest.raises(ClosureError, match=r"one and two both have the script 'run'"):
        tool_scripts(index, _listed_by(["one", "two"]), project="p", source="r.txt",
                     python_version=PYTHON)  # fmt: skip


def test_no_console_script_is_an_error_saying_what_the_file_named() -> None:
    index = _index(_dist("lib-a"), _dist("lib-b"), _dist("dep"))

    with pytest.raises(ClosureError, match=r"requirements.txt names lib-a, lib-b, and none has"):
        tool_scripts(index, _listed_by(["lib-a", "lib-b"]), project="p",
                     source="requirements.txt", python_version=PYTHON)  # fmt: skip


def test_a_complete_listing_without_a_console_script_says_which_were_considered() -> None:
    index = _index(_dist("app", ("lib",)), _dist("lib"))

    with pytest.raises(ClosureError, match=r"lists every installed distribution.*\(app\)"):
        tool_scripts(index, _listed_by(["app", "lib"]), project="p", source="freeze.txt",
                     python_version=PYTHON)  # fmt: skip


def test_gui_scripts_are_commands_like_console_scripts() -> None:
    index = _index(_dist("t", (), "t = t:main", gui="t-gui = t:gui"),
                   _dist("viewer", (), gui="v = v:main"))  # fmt: skip

    tools = tool_scripts(index, _listed_by(["t", "viewer"]), project="p", source="r.txt",
                         python_version=PYTHON)  # fmt: skip

    assert tools.scripts == {"t": "t:main", "t-gui": "t:gui", "v": "v:main"}
    assert tools.owners["t-gui"] == "t" and tools.owners["v"] == "viewer"
    assert "commands: t, t-gui (t 1.0); v (viewer 1.0)" in tools.notes[-1]


def test_a_console_and_a_gui_script_of_one_name_are_a_clash_within_or_across_tools() -> None:
    within = _index(_dist("t", (), "run = t:main", gui="run = t:gui"))
    across = _index(_dist("a", (), "run = a:main"), _dist("b", (), gui="run = b:gui"))

    with pytest.raises(ClosureError, match=r"t and t both have the script 'run'"):
        tool_scripts(within, _listed_by(["t"]), project="p", source="r.txt",
                     python_version=PYTHON)  # fmt: skip
    with pytest.raises(ClosureError, match=r"a and b both have the script 'run'"):
        tool_scripts(across, _listed_by(["a", "b"]), project="p", source="r.txt",
                     python_version=PYTHON)  # fmt: skip


def test_a_meta_package_gives_its_requirements_as_tools() -> None:
    index = _index(
        _dist("ansible", ("ansible-core>=2", "jinja2"), ""),
        _dist("ansible-core", ("jinja2",), "ansible = ansible.cli:main"),
        _dist("jinja2", (), "jinja = jinja2.cli:main"),
    )

    tools = tool_scripts(index, _listed_by(["ansible"]), project="p", source="requirements.txt",
                         python_version=PYTHON)  # fmt: skip

    assert sorted(tools.scripts) == ["ansible", "jinja"]  # the direct requirements, no deeper
    assert tools.notes[1] == ("ansible has no console script of its own; the tools taken "
                              "through it: ansible-core, jinja2")  # fmt: skip


def test_a_meta_package_in_a_freeze_takes_the_tool_and_not_the_tools_dependencies() -> None:
    index = _index(
        _dist("ansible", ("ansible-core",)),
        _dist("ansible-core", ("jinja2",), "ansible = ansible.cli:main"),
        _dist("jinja2", (), "jinja = jinja2.cli:main"),
        _dist("p"),
    )

    tools = tool_scripts(index, _listed_by(["ansible", "ansible-core", "jinja2"]), project="p",
                         source="freeze.txt", python_version=PYTHON)  # fmt: skip

    assert tools.scripts == {"ansible": "ansible.cli:main"}
    assert "the tools taken through it: ansible-core" in tools.notes[1]


def test_a_meta_package_of_meta_packages_is_followed_to_the_tools() -> None:
    index = _index(
        _dist("top", ("mid", "tool-b; python_version < '3.8'", "extra-dep; extra == 'more'")),
        _dist("mid", ("tool-a",)),
        _dist("tool-a", (), "a = tool_a:main"),
        _dist("tool-b", (), "b = tool_b:main"),
        _dist("extra-dep", (), "c = extra_dep:main"),
    )

    plain = tool_scripts(index, _listed_by(["top"]), project="p", source="r.txt",
                         python_version=PYTHON)  # fmt: skip
    more = tool_scripts(index, [Listed("top", frozenset({"more"}))], project="p", source="r.txt",
                        python_version=PYTHON)  # fmt: skip

    assert plain.scripts == {"a": "tool_a:main"}  # tool-b: false marker
    assert plain.notes[1:3] == (
        "top has no console script of its own; the tools taken through it: tool-a",
        "mid has no console script of its own; the tools taken through it: tool-a")  # fmt: skip
    assert sorted(more.scripts) == ["a", "c"]  # the extra asked for is the meta-package's


def test_a_tool_with_scripts_next_to_a_meta_package_keeps_its_dependencies_out() -> None:
    index = _index(
        _dist("app", ("lib",), "app = app:main"),
        _dist("lib", (), "lib = lib:main"),
        _dist("meta", ("other",)),
        _dist("other", (), "other = other:main"),
    )

    tools = tool_scripts(index, _listed_by(["app", "meta"]), project="p", source="r.txt",
                         python_version=PYTHON)  # fmt: skip

    assert sorted(tools.scripts) == ["app", "other"]


def test_a_cycle_of_meta_packages_stops_and_the_other_way_out_is_taken() -> None:
    index = _index(
        _dist("one", ("two",)),
        _dist("two", ("one", "tool")),
        _dist("tool", (), "t = tool:main"),
    )

    tools = tool_scripts(index, _listed_by(["one"]), project="p", source="r.txt",
                         python_version=PYTHON)  # fmt: skip

    assert tools.scripts == {"t": "tool:main"}


def test_meta_packages_with_nothing_beyond_them_are_no_commands() -> None:
    index = _index(_dist("one", ("two",)), _dist("two", ("one",)))

    with pytest.raises(ClosureError, match=r"names one, and none has a console or gui script"):
        tool_scripts(index, _listed_by(["one"]), project="p", source="r.txt",
                     python_version=PYTHON)  # fmt: skip


def test_the_meta_package_chain_stops_at_the_depth_limit() -> None:
    def chain(length: int) -> SiteIndex:
        return _index(*[_dist(f"m{i}", (f"m{i + 1}",)) for i in range(length)],
                      _dist(f"m{length}", (), "end = end:main"))  # fmt: skip

    tools = tool_scripts(chain(8), _listed_by(["m0"]), project="p", source="r.txt",
                         python_version=PYTHON)  # fmt: skip
    assert tools.scripts == {"end": "end:main"}
    with pytest.raises(ClosureError, match="none has"):
        tool_scripts(chain(9), _listed_by(["m0"]), project="p", source="r.txt",
                     python_version=PYTHON)  # fmt: skip


def test_a_distribution_with_code_falls_through_only_to_a_requirement_named_after_it() -> None:
    index = _index(
        _dist("ansible", ("ansible-core>=2", "jinja2"), code=True),
        _dist("ansible-core", ("jinja2",), "ansible = ansible.cli:main"),
        _dist("jinja2", (), "jinja = jinja2.cli:main"),
    )

    tools = tool_scripts(index, _listed_by(["ansible"]), project="p", source="requirements.txt",
                         python_version=PYTHON)  # fmt: skip

    assert sorted(tools.scripts) == ["ansible"]
    assert tools.notes[1] == ("ansible has no console script of its own; the tools taken "
                              "through it: ansible-core")  # fmt: skip


def test_a_library_with_no_script_is_not_a_tool_and_gives_none_of_its_dependencys() -> None:
    index = _index(
        _dist("black", (), "black = black:main", code=True),
        _dist("requests", ("charset-normalizer",), code=True),
        _dist("charset-normalizer", (), "normalizer = cn:main", code=True),
    )

    tools = tool_scripts(index, _listed_by(["black", "requests"]), project="p",
                         source="r.txt", python_version=PYTHON)  # fmt: skip

    assert sorted(tools.scripts) == ["black"]
    assert "requests has no console script and is a library, not a tool" in tools.notes


def test_a_meta_package_reached_by_two_ways_is_noted_once() -> None:
    index = _index(
        _dist("top", ("left", "right")),
        _dist("left", ("shared",)),
        _dist("right", ("shared",)),
        _dist("shared", ("tool",)),
        _dist("tool", (), "t = tool:main"),
    )

    tools = tool_scripts(index, _listed_by(["top"]), project="p", source="r.txt",
                         python_version=PYTHON)  # fmt: skip

    assert tools.scripts == {"t": "tool:main"}
    assert sum(note.startswith("shared ") for note in tools.notes) == 1


def test_a_missing_distribution_is_an_error_unless_a_marker_excludes_it() -> None:
    index = _index(_dist("a", (), "a = a:main"))

    with pytest.raises(ClosureError, match="names missing, which is not installed"):
        tool_scripts(index, _listed_by(["a", "missing"]), project="p", source="r.txt",
                     python_version=PYTHON)  # fmt: skip
    tools = tool_scripts(index, [Listed("a"), Listed("win", marker=Marker("os_name == 'nt'"))],
                         project="p", source="r.txt", python_version=PYTHON)  # fmt: skip
    assert sorted(tools.scripts) == ["a"]


def test_the_project_takes_the_tools_scripts_and_requirements_as_its_own() -> None:
    index = _index(_dist("a", (), "a = a:main"), _dist("b", (), "b = b:main"), _dist("tools"))
    tools = tool_scripts(index, _listed_by(["a", "b"]), project="tools", source="r.txt",
                         python_version=PYTHON)  # fmt: skip

    view = with_commands(index.dists["tools"], tools)

    assert resolve_entry(view, None) == ("tools", "a=a:main\nb=b:main")
    assert resolve_entry(view, "b") == ("b", "b:main")
    assert view.requires == ("a", "b")
    walk = SiteIndex(index.site, {**index.dists, "tools": view}).walk(
        view, extras=(), python_version=PYTHON)  # fmt: skip
    assert walk.reachable == ("a", "b", "tools") and walk.unreachable == ()


def test_one_tool_with_one_script_is_a_plain_entry_named_after_the_script() -> None:
    index = _index(_dist("pyflakes", (), "pyflakes = pyflakes.api:main"), _dist("tools"))
    tools = tool_scripts(index, _listed_by(["pyflakes"]), project="tools", source="r.txt",
                         python_version=PYTHON)  # fmt: skip

    view = with_commands(index.dists["tools"], tools)

    assert resolve_entry(view, None) == ("pyflakes", "pyflakes.api:main")


def test_a_dependency_marked_with_an_extra_that_is_asked_elsewhere_is_still_installed() -> None:
    """A Pipfile.lock marks what an extra needs ``extra == 'x'``: it stays named."""
    index = _index(
        _dist("req", ("idna", "ssl; extra == 'security'"), "req = req:main"),
        _dist("idna"),
        _dist("ssl"),
    )
    listed = [Listed("req", frozenset({"security"})), Listed("idna"),
              Listed("ssl", marker=Marker("extra == 'security'"))]  # fmt: skip

    tools = tool_scripts(index, listed, project="p", source="Pipfile.lock",
                         python_version=PYTHON)  # fmt: skip

    assert sorted(tools.scripts) == ["req"]
