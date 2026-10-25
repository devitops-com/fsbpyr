"""The build side of a multi-command executable: the table, the links, and the requirements
check that belongs to the same change."""

import os
from pathlib import Path

import pytest

from fsbpyr.boot._fsbpyr_rt.commands import parse
from fsbpyr.build import check_before_staging
from fsbpyr.closure import ClosureError, InstalledDist, command_table, resolve_entry
from fsbpyr.config import BuildConfig
from fsbpyr.links import LinkError, check_links, link_paths, make_links
from fsbpyr.project import Project, ProjectError

SHA = "a" * 40


def _dist(scripts: str) -> InstalledDist:
    return InstalledDist(name="app", version="1", dist_info="app-1.dist-info", site=Path("/site"),
                         requires=(), record=(), entry_points=f"[console_scripts]\n{scripts}",
                         direct_url=None)  # fmt: skip


def test_one_script_stays_the_plain_target() -> None:
    assert resolve_entry(_dist("tool = app.cli:main [extra]\n"), None) == ("tool", "app.cli:main")


def test_several_scripts_become_sorted_name_target_lines_under_the_project_name() -> None:
    dist = _dist("b = app.b:main\na = app.a [x]\nc = app.c\n")

    name, entrypoint = resolve_entry(dist, None)

    assert (name, entrypoint) == ("app", "a=app.a\nb=app.b:main\nc=app.c")  # no extras
    assert parse(entrypoint) == {"a": "app.a", "b": "app.b:main", "c": "app.c"}


def test_an_entry_names_one_script_of_several() -> None:
    assert resolve_entry(_dist("a = app.a:main\nb = app.b:main\n"), "b") == ("b", "app.b:main")


@pytest.mark.parametrize("name", ["-a", ".a", "a b", "a/b", "a=b", "ä", "+x"])
def test_a_name_that_is_no_command_fails_naming_it(name: str) -> None:
    with pytest.raises(ClosureError, match="not valid for a command"):
        command_table({"ok": "m:f", name: "m:g"})


def test_plain_names_are_commands() -> None:
    assert command_table({"a1": "m:f", "A_b.c+d-e": "m:g"}) == "A_b.c+d-e=m:g\na1=m:f"


# --- links ---------------------------------------------------------------------------------


def test_links_are_relative_and_the_binary_needs_none_for_its_own_name(tmp_path: Path) -> None:
    binary = tmp_path / "dist" / "app"
    binary.parent.mkdir()
    binary.write_bytes(b"x")

    made = make_links(binary, ["app", "a", "b"])

    assert made == [tmp_path / "dist" / "a", tmp_path / "dist" / "b"]
    assert [os.readlink(path) for path in made] == ["app", "app"]
    assert sorted(path.name for path in binary.parent.iterdir()) == ["a", "app", "b"]  # no .tmp


def test_an_existing_regular_file_is_not_replaced(tmp_path: Path) -> None:
    binary = tmp_path / "app"
    binary.write_bytes(b"x")
    (tmp_path / "b").write_text("mine")

    with pytest.raises(LinkError, match=r"'b'.*is not a symlink"):
        make_links(binary, ["a", "b"])

    assert (tmp_path / "b").read_text() == "mine"
    assert not (tmp_path / "a").exists()  # nothing is linked once one name is taken


def test_a_directory_is_not_replaced(tmp_path: Path) -> None:
    binary = tmp_path / "app"
    (tmp_path / "a").mkdir()

    with pytest.raises(LinkError, match="is not a symlink"):
        check_links(binary, ["a"])


def test_an_existing_symlink_is_replaced_even_when_dangling(tmp_path: Path) -> None:
    binary = tmp_path / "app"
    binary.write_bytes(b"x")
    (tmp_path / "a").symlink_to("nowhere")
    (tmp_path / "other").write_text("theirs")
    (tmp_path / "b").symlink_to("other")

    make_links(binary, ["a", "b"])

    assert os.readlink(tmp_path / "a") == "app" and os.readlink(tmp_path / "b") == "app"
    assert (tmp_path / "other").read_text() == "theirs"  # the old target is untouched


def test_links_follow_the_output_name_not_the_project_name(tmp_path: Path) -> None:
    assert link_paths(tmp_path / "tool", ["tool", "x"]) == [tmp_path / "x"]


# --- the hash check ------------------------------------------------------------------------


def _project(tmp_path: Path, requirements: str) -> Project:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "1"\n[project.scripts]\na = "app:a"\n'
    )
    (tmp_path / "requirements.txt").write_text(requirements)
    return Project.load(tmp_path)


def test_a_hash_anywhere_makes_vcs_lines_fail_early_without_strict(tmp_path: Path) -> None:
    project = _project(tmp_path, f"a==1 \\\n  --hash=sha256:ab\nlib @ git+https://h/r.git@{SHA}\n")

    with pytest.raises(ProjectError, match=r"requirements.txt has --hash lines.*not a hash") as e:
        check_before_staging(project, BuildConfig())

    assert f"requirements.txt:3: lib @ git+https://h/r.git@{SHA}" in str(e.value)


def test_vcs_lines_pass_the_early_check_without_hashes_unless_strict(tmp_path: Path) -> None:
    project = _project(tmp_path, f"a==1\nlib @ git+https://h/r.git@{SHA}\n")

    check_before_staging(project, BuildConfig())
    with pytest.raises(ProjectError, match="--strict needs every requirement"):
        check_before_staging(project, BuildConfig(strict=True))


def test_hashes_without_vcs_lines_pass(tmp_path: Path) -> None:
    project = _project(tmp_path, "a==1 --hash=sha256:ab\n")

    check_before_staging(project, BuildConfig())


def test_a_commented_hash_does_not_count_and_a_spaced_one_does(tmp_path: Path) -> None:
    vcs = f"lib @ git+https://h/r.git@{SHA}\n"
    check_before_staging(_project(tmp_path, f"# --hash=x\na==1  # --hash=y\n{vcs}"), BuildConfig())
    with pytest.raises(ProjectError, match="has --hash lines"):
        check_before_staging(_project(tmp_path, f"a==1 --hash sha256:ab\n{vcs}"), BuildConfig())
