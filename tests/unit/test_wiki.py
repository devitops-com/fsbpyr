"""The wiki's pages (wiki/) and tools (tools/wiki/): the pages' links hold, the published form
of a page, the import graphs the build draws, and how publishing treats the wikis' history.
Building the PDFs needs pandoc and LuaLaTeX and is not tested here (make wiki-build)."""

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[2]
TOOLS = ROOT / "tools" / "wiki"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"wiki_{name}", TOOLS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint = _load("lint")
site = _load("site")
publish = _load("publish")
importgraphs = _load("importgraphs")


def test_the_wiki_pages_have_no_broken_link() -> None:
    """Every link of wiki/*.md has its target: a page and its heading, a file of the repository
    and its lines, a diagram the build draws (make wiki-lint)."""
    assert lint.check(ROOT) == []


def _repo(tmp_path: Path, pages: dict[str, str]) -> Path:
    (tmp_path / "wiki" / "diagrams").mkdir(parents=True)
    (tmp_path / "wiki" / "diagrams" / "flow.dot").write_text("digraph G {}\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("one\ntwo\nthree\n")
    (tmp_path / "docs.md").write_text("# Docs\n\n## A part\n")
    for name, text in pages.items():
        (tmp_path / "wiki" / f"{name}.md").write_text(text)
    return tmp_path


def test_the_lint_accepts_every_kind_of_link_the_pages_use(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "Home": "# Home\n\n## Start here\n\n[a](Other.md) [b](Other.md#its-part) [c](#start-here)"
                " [d](../src/a.py#L2-L3) [e](../src/) [f](../docs.md#a-part)"
                " [g](https://example.org) [h](fsbpyr-User-Guide.pdf)\n\n"
                "![flow](images/flow.svg) ![graph](images/arch-subsystems.svg)\n\n"
                "[ref]: ../src/a.py#L1\n",
        "Other": "# Other\n\n## Its part\n\n## Its part\n\n[x](#its-part-1)\n",
    })  # fmt: skip

    assert lint.check(root) == []


@pytest.mark.parametrize(("link", "problem"), [
    ("Nowhere.md", "no such page or target: Nowhere.md"),
    ("Other", "no such page or target: Other"),  # the wiki's form, not the repository's
    ("Other.md#nothing", "no such anchor: Other.md#nothing"),
    ("#nothing", "no such anchor: #nothing"),
    ("../src/b.py", "no such file or directory in the repository: src/b.py"),
    ("../src/a.py#L4", "line anchor past the end (3 lines): ../src/a.py#L4"),
    ("../docs.md#no-part", "no such heading: ../docs.md#no-part"),
    ("https://github.com/devitops-com/fsbpyr/blob/v0.27.0/src/a.py",
     "pinned to a tag, write it relative (../path): https://github.com/devitops-com/fsbpyr/blob"
     "/v0.27.0/src/a.py"),
    ("images/missing.svg", "no such image: images/missing.svg"),
])  # fmt: skip
def test_the_lint_names_a_broken_link(tmp_path: Path, link: str, problem: str) -> None:
    root = _repo(tmp_path, {"Home": f"# Home\n\n[x]({link})\n", "Other": "# Other\n"})

    assert lint.check(root) == [f"Home: {problem}"]


def test_the_lint_ignores_links_in_code_blocks(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Home": "# Home\n\n```md\n[x](Nowhere.md)\n```\n\n"
                                    "~~~\n[y](Nowhere.md)\n~~~\n"})  # fmt: skip

    assert lint.check(root) == []


def test_the_lint_checks_the_link_around_an_image_too(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Home": "# Home\n\n[![flow](images/flow.svg)](Nowhere.md)\n"})

    assert lint.check(root) == ["Home: no such page or target: Nowhere.md"]


def test_the_lint_refuses_a_link_with_a_title(tmp_path: Path) -> None:
    """Neither the check nor the published form would see its target."""
    root = _repo(tmp_path, {"Home": '# Home\n\n[x](Other.md "the other page")\n',
                            "Other": "# Other\n"})  # fmt: skip

    assert lint.check(root) == ["Home: a link with a title, write it without: Other.md"]


def test_a_hand_made_image_is_an_svg(tmp_path: Path) -> None:
    """Git ignores PNGs under wiki/: a PNG there would never reach the release tag."""
    root = _repo(tmp_path, {"Home": "# Home\n"})
    (root / "wiki" / "images").mkdir()
    (root / "wiki" / "images" / "photo.png").write_bytes(b"png")

    assert lint.check(root) == [
        "wiki/images/photo.png: not an SVG; images in wiki/images/ are SVGs"
    ]


def test_a_hand_made_image_may_not_take_the_name_of_a_drawn_one(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Home": "# Home\n"})
    (root / "wiki" / "images").mkdir()
    (root / "wiki" / "images" / "flow.svg").write_text("<svg/>")

    assert lint.check(root) == ["wiki/images/flow.svg: has the name of a drawn diagram"]


def test_the_published_page_links_as_the_wiki_does_and_pins_the_repository_to_the_tag(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path, {"Home": "", "Other": ""})
    text = ("See [o](Other.md#x), [h](#y), [f](../src/a.py#L2), [d](../src/), [r](../), "
            "[u](https://example.org), [p](fsbpyr-User-Guide.pdf), ![i](images/flow.svg)\n"
            "```\n[kept](Other.md) @VERSION@\n```\n~~~\n[kept](Other.md)\n~~~\n"
            "[![i](images/flow.svg)](Other.md)\n"
            "[ref]: ../src/a.py#L1\n\nfsbpyr @VERSION@\n")  # fmt: skip

    published = site.publish_text(text, root, {"Home", "Other"}, "1.2.3")

    repo = "https://github.com/devitops-com/fsbpyr"
    assert published == (
        f"See [o](Other#x), [h](#y), [f]({repo}/blob/v1.2.3/src/a.py#L2), "
        f"[d]({repo}/tree/v1.2.3/src), [r]({repo}/tree/v1.2.3), [u](https://example.org), "
        "[p](fsbpyr-User-Guide.pdf), ![i](images/flow.svg)\n"
        "```\n[kept](Other.md) 1.2.3\n```\n~~~\n[kept](Other.md)\n~~~\n"
        "[![i](images/flow.svg)](Other)\n"
        f"[ref]: {repo}/blob/v1.2.3/src/a.py#L1\n\nfsbpyr 1.2.3\n"
    )


def test_published_from_main_the_repository_links_point_at_its_commit(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Home": ""})
    text = "[f](../src/a.py#L2), [r](../), fsbpyr @VERSION@\n"

    published = site.publish_text(text, root, {"Home"}, "1.2.3", "0123abcd")

    repo = "https://github.com/devitops-com/fsbpyr"
    assert published == f"[f]({repo}/blob/0123abcd/src/a.py#L2), [r]({repo}/tree/0123abcd), " \
                        "fsbpyr 1.2.3\n"  # fmt: skip


def test_the_import_graphs_the_build_draws_are_the_ones_the_lint_knows(tmp_path: Path) -> None:
    graph = tmp_path / "modgraph.json"
    python = [sys.executable, "-I"]
    subprocess.run([*python, str(TOOLS / "modgraph.py"), str(ROOT / "src"), str(graph)],
                   check=True)  # fmt: skip
    subprocess.run([*python, str(TOOLS / "importgraphs.py"), str(graph), str(tmp_path / "out")],
                   check=True)  # fmt: skip

    drawn = sorted(p.stem for p in (tmp_path / "out").glob("*.dot"))
    assert drawn == sorted(importgraphs.OUTPUTS) == sorted(lint.IMPORT_GRAPHS)


def _git(repo: Path, *args: str) -> str:
    git = ["git", "-C", str(repo)]
    return subprocess.run([*git, *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for variable in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(variable, "tester")
    for variable in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(variable, "tester@example.org")
    repo = tmp_path / "wiki"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def _commit(repo: Path, name: str, message: str) -> None:
    (repo / name).write_text(message)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)


def test_a_wiki_never_published_from_the_repository_has_only_hand_commits(wiki: Path) -> None:
    assert publish.hand_commits(wiki) == []  # empty: nothing to lose
    _commit(wiki, "Home.md", "by hand")

    assert [line.split(" ", 3)[3] for line in publish.hand_commits(wiki)] == ["by hand"]


def test_the_commits_after_the_last_publish_are_the_hand_commits(wiki: Path) -> None:
    _commit(wiki, "Home.md", "by hand")
    _commit(wiki, "Home.md", f"docs: publish\n\n{publish.TRAILER}v1.2.3 abc")
    assert publish.hand_commits(wiki) == []

    _commit(wiki, "Other.md", "edited online")

    assert [line.split(" ", 3)[3] for line in publish.hand_commits(wiki)] == ["edited online"]


def test_staging_makes_the_wiki_the_site_with_the_copies_the_wiki_needs(
    wiki: Path, tmp_path: Path
) -> None:
    _commit(wiki, "Gone.md", "a page no longer published")
    built = tmp_path / "site"
    (built / "images").mkdir(parents=True)
    (built / "Home.md").write_text("# Home\n")
    (built / "_Sidebar.md").write_text("- [Home](Home)\n")
    (built / "images" / "a.svg").write_text("<svg/>")

    assert publish.stage(built, wiki, {"Home.md": "home.md", "_Sidebar.md": "_sidebar.md"})
    _git(wiki, "commit", "-q", "-m", "publish")

    assert _git(wiki, "ls-files").split() == ["Home.md", "_Sidebar.md", "_sidebar.md",
                                              "home.md", "images/a.svg"]  # fmt: skip
    assert (wiki / "home.md").read_text() == "# Home\n"
    assert not publish.stage(built, wiki, {"Home.md": "home.md", "_Sidebar.md": "_sidebar.md"})


def test_a_release_tag_is_vX_Y_Z_and_they_order_by_number() -> None:
    assert max(["v0.9.0", "v0.10.0", "v0.10.1"], key=publish.numbers) == "v0.10.1"
    with pytest.raises(publish.PublishError, match="no release tag"):
        publish.numbers("v0.29.0-rc1")


@pytest.fixture
def remote(wiki: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A wiki to publish to (bare, branch master) holding one commit made by hand, and the
    work directory publish.py clones into."""
    monkeypatch.setattr(publish, "WORK", tmp_path / "work")
    _commit(wiki, "Home.md", "by hand")
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(wiki), str(bare)], check=True)  # noqa: S607
    _git(bare, "symbolic-ref", "HEAD", "refs/heads/master")
    _git(wiki, "push", "-q", str(bare), "HEAD:refs/heads/master")
    return bare


def _site(tmp_path: Path, text: str) -> Path:
    built = tmp_path / f"site-{len(text)}"
    built.mkdir()
    (built / "Home.md").write_text(text)
    return built


def test_publishing_stops_at_hand_commits_and_force_replaces_them(
    remote: Path, tmp_path: Path
) -> None:
    target: tuple[str, str, dict[str, str], str] = ("Test", str(remote), {}, "master")
    built = _site(tmp_path, "# Home\n")

    with pytest.raises(publish.PublishError, match="commits not made by make wiki-publish"):
        publish.prepare(target, built, "v1.2.3", "abc", force=False)
    clone = publish.prepare(target, built, "v1.2.3", "abc", force=True)

    assert clone is not None
    assert f"{publish.TRAILER}v1.2.3 abc" in _git(clone, "log", "-1", "--format=%B")
    _git(clone, "push", "-q", "origin", "HEAD:refs/heads/master")
    assert publish.prepare(target, built, "v1.2.3", "abc", force=False) is None  # up to date


def test_publishing_an_older_release_over_a_newer_one_needs_force(
    remote: Path, tmp_path: Path
) -> None:
    target: tuple[str, str, dict[str, str], str] = ("Test", str(remote), {}, "master")
    clone = publish.prepare(target, _site(tmp_path, "# Home\n"), "v1.2.3", "abc", force=True)
    assert clone is not None
    _git(clone, "push", "-q", "origin", "HEAD:refs/heads/master")

    with pytest.raises(publish.PublishError, match=r"has the newer v1\.2\.3"):
        publish.prepare(target, _site(tmp_path, "# Old home\n"), "v1.1.9", "def", force=False)
    assert publish.prepare(target, _site(tmp_path, "# Old  home\n"), "v1.1.9", "def",
                           force=True) is not None  # fmt: skip


@pytest.mark.parametrize(("arguments", "environment", "flags"), [
    ([], {}, ""),
    (["FORCE=0", "DRY_RUN=0"], {}, ""),
    (["FORCE=1", "DRY_RUN=1", "VERSION=1.2.3"], {}, " --version '1.2.3' --force --dry-run"),
    ([], {"FORCE": "1", "DRY_RUN": "1", "VERSION": "1.2.3"}, ""),  # exported: another tool's
])  # fmt: skip
def test_only_force_and_dry_run_given_to_make_as_1_count(
    arguments: list[str], environment: dict[str, str], flags: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    make = ["make", "-s", "-n", "-C", str(ROOT), "wiki-publish", *arguments]

    printed = subprocess.run(make, capture_output=True, text=True, check=True).stdout

    assert printed.strip() == f"python3 -I tools/wiki/publish.py publish{flags}"


@pytest.fixture
def source(wiki: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout whose release v1.2.3 and main are on its GitHub (a bare repository here),
    and the work directory publish.py extracts into."""
    monkeypatch.setattr(publish, "WORK", tmp_path / "work")
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "src" / "fsbpyr").mkdir(parents=True)
    _commit(repo, "src/fsbpyr/__init__.py", '__version__ = "1.2.3"\n')
    (repo / "wiki").mkdir()
    _commit(repo, "wiki/Home.md", "# Home\n")
    _git(repo, "tag", "-a", "-m", "release", "v1.2.3")
    github = tmp_path / "github.git"
    subprocess.run(["git", "init", "-q", "--bare", str(github)], check=True)  # noqa: S607
    monkeypatch.setattr(publish, "SOURCE", str(github))
    _git(repo, "push", "-q", str(github), "main", "v1.2.3")
    return repo


def _change(repo: Path, name: str) -> str:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    _commit(repo, name, f"changed {name}\n")
    _git(repo, "push", "-q", publish.SOURCE, "main")
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("name", ["src/fsbpyr/build.py", "pyproject.toml", "uv.lock",
                                  ".python-version", "tools/release/install.sh.in"])  # fmt: skip
def test_what_goes_into_a_release_is_released_code(source: Path, name: str) -> None:
    for other in ("wiki/Home.md", "tools/wiki/site.py", "docs/notes.txt", "CHANGELOG.md",
                  "Makefile", "tests/unit/test_wiki.py", ".github/workflows/build.yml",
                  "tools/bootstrap.sh", "src/fsbpyr/README.md"):  # fmt: skip
        commit = _change(source, other)
    assert publish.released_changes("v1.2.3", commit, source) == []

    commit = _change(source, name)

    assert publish.released_changes("v1.2.3", commit, source) == [name]


def test_a_file_moved_out_of_what_goes_into_a_release_is_released_code(source: Path) -> None:
    (source / "tools").mkdir()
    _git(source, "mv", "src/fsbpyr/__init__.py", "tools/moved.py")
    _git(source, "commit", "-q", "-m", "moved")

    assert publish.released_changes("v1.2.3", "HEAD", source) == ["src/fsbpyr/__init__.py"]


def test_the_newest_release_is_looked_up_on_github(source: Path) -> None:
    _git(source, "tag", "v1.2.10")
    _git(source, "push", "-q", publish.SOURCE, "v1.2.10")

    assert publish.newest_on_github(source) == "v1.2.10"


def test_main_is_published_while_its_code_is_the_newest_releases(source: Path) -> None:
    commit = _change(source, "wiki/Home.md")
    assert publish.released("v1.2.3", source) == _git(source, "rev-parse", "v1.2.3^{commit}")

    tree, published = publish.main_tree("v1.2.3", source)

    assert published == commit
    assert (tree / "wiki" / "Home.md").read_text() == "changed wiki/Home.md\n"


def test_main_with_code_not_released_is_not_published(source: Path) -> None:
    _change(source, "wiki/Home.md")
    _change(source, "src/fsbpyr/build.py")

    with pytest.raises(publish.PublishError, match=r"changed src/fsbpyr/build\.py since v1\.2\.3"):
        publish.main_tree("v1.2.3", source)


def test_a_wiki_published_from_main_says_so_and_names_the_release(
    remote: Path, tmp_path: Path
) -> None:
    target: tuple[str, str, dict[str, str], str] = ("Test", str(remote), {}, "master")

    clone = publish.prepare(target, _site(tmp_path, "# Home\n"), "v1.2.3", "abc", force=True,
                            origin="main at abc (the code of v1.2.3)")  # fmt: skip

    assert clone is not None
    message = _git(clone, "log", "-1", "--format=%B")
    assert "from main at abc (the code of v1.2.3)" in message
    assert f"{publish.TRAILER}v1.2.3 abc" in message


def test_a_tags_pages_over_pages_of_the_same_release_from_main_need_force(
    remote: Path, tmp_path: Path
) -> None:
    target: tuple[str, str, dict[str, str], str] = ("Test", str(remote), {}, "master")
    clone = publish.prepare(target, _site(tmp_path, "# Home, fixed\n"), "v1.2.3", "main1",
                            force=True, origin="main at main1")  # fmt: skip
    assert clone is not None
    _git(clone, "push", "-q", "origin", "HEAD:refs/heads/master")

    with pytest.raises(publish.PublishError, match=r"published from main at main1"):
        publish.prepare(target, _site(tmp_path, "# Home\n"), "v1.2.3", "tag1", force=False)
    assert publish.prepare(target, _site(tmp_path, "# Home, newest\n"), "v1.2.3", "main2",
                           force=False, origin="main at main2") is not None  # fmt: skip
    assert publish.prepare(target, _site(tmp_path, "# The home\n"), "v1.2.3", "tag1",
                           force=True) is not None  # fmt: skip


def test_the_pinned_keys_are_the_ones_the_wiki_lists() -> None:
    """A test key, or a key change without its wiki entry, fails here: the pins are only as safe
    as main, and this is the check against mistakes (docs/signing.md §5.1)."""
    text = (ROOT / "wiki" / "User-Installing.md").read_text(encoding="utf-8")
    section = text.split("## Release keys\n", 1)[1].split("\n## ", 1)[0]
    listed = section.split("```text\n", 1)[1].split("```", 1)[0].split()
    pins = sorted((ROOT / "src" / "fsbpyr" / "release-keys").glob("*.pub"))

    assert sorted(listed) == sorted(f"SHA256:{hashlib.sha256(pin.read_bytes()).hexdigest()}"
                                    for pin in pins)  # fmt: skip
