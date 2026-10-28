"""Build the wiki from the repository, and publish it to the GitHub and the GitLab wiki.

Usage (from the repository's top directory; the Makefile's wiki targets run it):
  python -I tools/wiki/publish.py build
      the wiki of this checkout into dist/wiki/site, to look at; nothing is pushed
  python -I tools/wiki/publish.py publish [--version X.Y.Z] [--dry-run] [--force]
      the wiki of main, or with --version that of release vX.Y.Z, to both wikis

The pages live in wiki/ and are edited there, in the repository; the wikis are the published
copy and are never edited by hand. The wiki always describes a release's code, but publishing
it needs no release:
- Without --version, `publish` builds main as GitHub has it, when main ships the newest
  release's code: nothing of what goes into a release (RELEASED: src/, pyproject.toml,
  uv.lock, .python-version, tools/release/; Markdown files there excepted) changed since that
  tag. Otherwise it refuses, naming the first such file, as the wiki would describe code not
  released yet. Changes anywhere else (the documentation, the Makefile, the tests, CI, the
  wiki's tools) never need a release before the wiki is published. The pages' repository
  links point at the commit of main they come from. A tag's own pages are not published over
  pages of the same release from main without --force: that would undo documentation changes.
- With --version X.Y.Z it builds from the tag vX.Y.Z's own tree, with that tag's tools, so the
  wiki describes exactly that release (for a release whose code main has moved on from); it
  refuses a tag whose tree has no wiki/.
Both refuse a release tag GitHub does not have. Both build with the tree's own tools (git
archive). The same build goes to both wikis; only names differ where the wikis need them
(GitLab's front page and sidebar are home.md and _sidebar.md). GitLab is pushed first, then
GitHub; nothing is pushed until both are ready.

Every publish commit carries a `Published-From: vX.Y.Z <commit>` trailer: the release whose
code the wiki describes and the commit its pages come from. A wiki with a commit after the
last such commit was changed by hand: `publish` lists those commits and stops, unless
--force, which replaces them (and is needed once, for a wiki that was never published this
way). It also stops before publishing an older release over a newer one, unless --force. A
wiki that already has the build gets no commit. --dry-run stops before pushing and shows what
would change.

FSBPYR_WIKI_GITHUB, FSBPYR_WIKI_GITLAB and FSBPYR_WIKI_SOURCE override the wikis' and the
repository's addresses (for tests).
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "dist" / "wiki"
SOURCE = os.environ.get("FSBPYR_WIKI_SOURCE", "https://github.com/devitops-com/fsbpyr.git")
WIKIS = (  # (name, address, files to copy under another name, branch), pushed in this order
    ("GitLab", os.environ.get("FSBPYR_WIKI_GITLAB",
                              "git@gitlab.infrazop.hr:devitops1/fsbpyr.wiki.git"),
     {"Home.md": "home.md", "_Sidebar.md": "_sidebar.md"}, "main"),
    ("GitHub", os.environ.get("FSBPYR_WIKI_GITHUB", "git@github.com:devitops-com/fsbpyr.wiki.git"),
     {}, "master"),
)  # fmt: skip
TRAILER = "Published-From: "
RELEASE = re.compile(r"v(\d+)\.(\d+)\.(\d+)")  # a release tag; vX.Y.Z-rc1 and the like are not
# What goes into a release and what its users get: fsbpyr's code and its version, its locked
# dependencies, its default Python, and the release's installer, pins and SBOM. While none of
# it changed since the newest release tag, main's wiki describes that release (owner decision
# 2026-10-28: the documentation, Makefile, tests, CI and tools/wiki/ never need a release).
RELEASED = ("src/", "pyproject.toml", "uv.lock", ".python-version", "tools/release/")


class PublishError(RuntimeError):
    """The wiki cannot be published; the message says why."""


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603  git found on PATH, an argument list
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False  # noqa: S607
    )
    if check and result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip()
        raise PublishError(f"git {' '.join(args)}: {error}")
    return result.stdout.strip()


def version_of(tree: Path) -> str:
    text = (tree / "src" / "fsbpyr" / "__init__.py").read_text()
    match = re.search(r'__version__ = "([^"]+)"', text)
    if not match:
        raise PublishError(f"{tree}/src/fsbpyr/__init__.py names no version")
    return match.group(1)


def build(  # noqa: PLR0913  # build.sh's arguments
    tree: Path, out: Path, version: str, source: str, commit: str, *, ref: str | None = None
) -> None:
    """The published wiki of `tree`, built by the tree's own tools/wiki/build.sh, into out;
    its repository links point at ref (build.sh's default: the tag vVERSION)."""
    date = git("show", "-s", "--format=%cs", commit)
    epoch = git("show", "-s", "--format=%ct", commit)
    command = ["bash", str(tree / "tools" / "wiki" / "build.sh"), str(tree), str(out), version,
               source, date, epoch, *([ref] if ref else [])]  # fmt: skip
    result = subprocess.run(command, check=False)  # noqa: S603  bash, the tree's build.sh
    if result.returncode != 0:
        raise PublishError("the wiki did not build (see above); nothing was published")


def numbers(tag: str) -> tuple[int, ...]:
    match = RELEASE.fullmatch(tag)
    if not match:
        raise PublishError(f"{tag} is no release tag vX.Y.Z")
    return tuple(int(n) for n in match.groups())


def latest_tag() -> str:
    tags = [tag for tag in git("tag", "--list", "v*").splitlines() if RELEASE.fullmatch(tag)]
    if not tags:
        raise PublishError("no release tag vX.Y.Z here: git fetch --tags")
    return max(tags, key=numbers)


def newest_on_github(repo: Path = ROOT) -> str | None:
    """The newest release tag GitHub has, if any."""
    listed = git("ls-remote", "--tags", SOURCE, "refs/tags/v*", cwd=repo).splitlines()
    names = {line.split()[1].removeprefix("refs/tags/").removesuffix("^{}") for line in listed}
    tags = [name for name in names if RELEASE.fullmatch(name)]
    return max(tags, key=numbers) if tags else None


def released(tag: str, repo: Path = ROOT) -> str:
    """The commit of release `tag`, which must be here and on GitHub, the same tag object."""
    commit = git("rev-parse", "-q", "--verify", f"{tag}^{{commit}}", cwd=repo, check=False)
    if not commit:
        raise PublishError(f"no tag {tag} here: git fetch --tags")
    # The tag itself (an annotated tag's object, a lightweight tag's commit) must be GitHub's.
    local = git("rev-parse", f"refs/tags/{tag}", cwd=repo)
    remote = git("ls-remote", "--tags", SOURCE, f"refs/tags/{tag}", cwd=repo).split()
    if remote[:1] != [local]:
        raise PublishError(f"{SOURCE} does not have this tag {tag} ({local[:12]}): publish "
                           "released tags only")  # fmt: skip
    return commit


def extract(ref: str, tree: Path, repo: Path = ROOT) -> None:
    """The tree of `ref` (git archive) into the directory tree, made anew."""
    shutil.rmtree(tree, ignore_errors=True)
    tree.mkdir(parents=True)
    archive = subprocess.run(["git", "archive", ref], cwd=repo, capture_output=True,  # noqa: S603, S607
                             check=True).stdout  # fmt: skip
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)  # noqa: S603, S607


def release_tree(tag: str) -> tuple[Path, str]:
    """The tree of `tag`, extracted under dist/wiki, and its commit."""
    commit = released(tag)
    tree = WORK / tag / "tree"
    extract(tag, tree)
    if not (tree / "wiki").is_dir():
        raise PublishError(f"{tag} has no wiki/: its wiki was not published from the repository")
    if version_of(tree) != tag.removeprefix("v"):
        raise PublishError(f"{tag}'s src/fsbpyr/__init__.py names version {version_of(tree)}")
    return tree, commit


def released_changes(tag: str, commit: str, repo: Path = ROOT) -> list[str]:
    """The files of what goes into a release (RELEASED) changed between tag and commit: what
    would make the wiki of commit describe code not released. Markdown files are none of it."""
    # --no-renames: a file moved out of RELEASED is its old path too; -z: paths unquoted.
    listed = git("diff", "--name-only", "--no-renames", "-z", tag, commit, cwd=repo)
    return [f for f in listed.split("\0") if f.startswith(RELEASED) and not f.endswith(".md")]


def main_tree(tag: str, repo: Path = ROOT) -> tuple[Path, str]:
    """Main as GitHub has it, extracted under dist/wiki, and its commit, if it ships the code of
    release `tag`: nothing of RELEASED changed since."""
    # The commit from GitHub itself, not from FETCH_HEAD, which another fetch may overwrite.
    heads = git("ls-remote", SOURCE, "refs/heads/main", cwd=repo).split()
    if not heads:
        raise PublishError(f"{SOURCE} has no main: nothing was published")
    commit = heads[0]
    git("fetch", "-q", "--no-tags", "--no-write-fetch-head", SOURCE, "refs/heads/main", cwd=repo)
    if not git("rev-parse", "-q", "--verify", f"{commit}^{{commit}}", cwd=repo, check=False):
        raise PublishError(f"main moved on GitHub while it was fetched ({commit[:12]} is not "
                           "here): run it again")  # fmt: skip
    contains = subprocess.run(["git", "merge-base", "--is-ancestor", tag, commit],  # noqa: S603, S607
                              cwd=repo, check=False).returncode == 0  # fmt: skip
    if not contains:
        raise PublishError(f"main ({commit[:12]}) does not contain {tag}: nothing was published")
    if changed := released_changes(tag, commit, repo):
        raise PublishError(
            f"main ({commit[:12]}) changed {changed[0]} since {tag}"
            + (f" and {len(changed) - 1} other files" if len(changed) > 1 else "")
            + f": its wiki may describe code not released yet; nothing was published. Publish "
            f"main's wiki after the next release, or {tag}'s with VERSION={tag[1:]}")
    tree = WORK / "main" / "tree"
    extract(commit, tree, repo)
    if version_of(tree) != tag.removeprefix("v"):
        raise PublishError(f"main's src/fsbpyr/__init__.py names version {version_of(tree)}, "
                           f"not {tag}'s")  # fmt: skip
    return tree, commit


def last_trailer(clone: Path) -> tuple[str, str] | None:
    """The release the wiki last published describes and the commit its pages came from (the
    trailer's two fields), if it was ever published this way."""
    body = git("log", "-1", "--format=%B", f"--grep=^{TRAILER}", cwd=clone, check=False)
    match = re.search(rf"^{TRAILER}(\S+)(?: (\S+))?", body, re.M)
    return (match.group(1), match.group(2) or "") if match else None


def last_published(clone: Path) -> str | None:
    """The release the wiki last published describes (the trailer's tag), if any."""
    last = last_trailer(clone)
    return last[0] if last else None


def hand_commits(clone: Path) -> list[str]:
    """The wiki's commits after the last one this script made (all of them if none)."""
    if not git("rev-parse", "-q", "--verify", "HEAD", cwd=clone, check=False):
        return []  # an empty wiki
    last = git("log", "-1", "--format=%H", f"--grep=^{TRAILER}", cwd=clone)
    span = f"{last}..HEAD" if last else "HEAD"
    return git("log", "--format=%h %an %as %s", span, cwd=clone).splitlines()


def stage(site: Path, clone: Path, copies: dict[str, str]) -> bool:
    """Make the clone's files the site's (plus the copies under other names), staged; True if
    that changes anything."""
    git("rm", "-rq", "--ignore-unmatch", ".", cwd=clone)
    for path in clone.iterdir():
        if path.is_dir() and path.name != ".git":
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()
    shutil.copytree(site, clone, dirs_exist_ok=True)
    for name, other in copies.items():
        shutil.copyfile(clone / name, clone / other)
    git("add", "-A", ".", cwd=clone)
    return bool(git("status", "--porcelain", cwd=clone))


def prepare(  # noqa: PLR0913  # the wiki, what to publish, and how
    wiki: tuple[str, str, dict[str, str], str], site: Path, tag: str, commit: str, *,
    force: bool, origin: str | None = None,
) -> Path | None:  # fmt: skip
    """A clone of the wiki (name, address, copies, branch) with the publish commit made on the
    branch; None if the wiki is up to date. origin says where the pages come from (default:
    the tag)."""
    name, url, copies, branch = wiki
    clone = WORK / tag / name
    shutil.rmtree(clone, ignore_errors=True)
    git("clone", "-q", url, str(clone))
    if not git("rev-parse", "-q", "--verify", "HEAD", cwd=clone, check=False):
        git("symbolic-ref", "HEAD", f"refs/heads/{branch}", cwd=clone)  # an empty wiki
    elif (current := git("symbolic-ref", "--short", "HEAD", cwd=clone)) != branch:
        raise PublishError(f"the {name} wiki's branch is {current}, not {branch}; nothing was "
                           "published")  # fmt: skip
    previous = last_published(clone)
    if previous and RELEASE.fullmatch(previous) and numbers(previous) > numbers(tag):
        if not force:
            raise PublishError(f"the {name} wiki has the newer {previous}; nothing was "
                               f"published (FORCE=1 publishes {tag} over it)")  # fmt: skip
        print(f"{name}: replacing {previous} with the older {tag}")
    # The tag's own pages over pages of the same release published from main would undo the
    # documentation changes made since the tag.
    last = last_trailer(clone)
    if origin is None and last and last[0] == tag and last[1] != commit:
        if not force:
            raise PublishError(f"the {name} wiki has pages of {tag} published from main at "
                               f"{last[1][:12]}, later than the tag's; nothing was published "
                               f"(FORCE=1 publishes the tag's over them)")  # fmt: skip
        print(f"{name}: replacing the pages of {tag} from {last[1][:12]} with the tag's")
    if foreign := hand_commits(clone):
        listed = "\n  ".join(foreign)
        if not force:
            raise PublishError(f"the {name} wiki has commits not made by make wiki-publish; "
                               f"nothing was published:\n  {listed}\nmake those changes in "
                               "wiki/ of the repository, or publish with FORCE=1 to replace "
                               "them")  # fmt: skip
        print(f"{name}: replacing commits not made by make wiki-publish:\n  {listed}")
    if not stage(site, clone, copies):
        print(f"{name}: up to date with {tag}")
        return None
    print(f"{name}: {git('diff', '--cached', '--shortstat', cwd=clone)}")
    message = (f"docs: publish the wiki of fsbpyr {tag}\n\nMade by make wiki-publish from "
               f"{origin or 'tag ' + tag} of the fsbpyr repository; edit the pages in its "
               f"wiki/ directory, not here.\n\n{TRAILER}{tag} {commit}\n")  # fmt: skip
    git("commit", "-q", "-m", message, cwd=clone)
    return clone


def publish(version: str | None, dry_run: bool, force: bool) -> None:
    if version:
        tag = f"v{version}"
        numbers(tag)
        tree, commit = release_tree(tag)
        source, ref, origin = f"tag {tag}, commit {commit[:12]}", None, None
    else:
        tag = latest_tag()
        released(tag)
        if (newest := newest_on_github()) and numbers(newest) > numbers(tag):
            raise PublishError(f"GitHub has the newer release {newest}, this checkout's newest "
                               f"tag is {tag}: git fetch --tags")  # fmt: skip
        tree, commit = main_tree(tag)
        source = f"main, commit {commit[:12]}, the code of {tag}"
        ref, origin = commit, f"main at {commit[:12]} (the code of {tag})"
    site = WORK / tag / "site"
    build(tree, site, tag.removeprefix("v"), source, commit, ref=ref)
    ready = [(wiki, prepare(wiki, site, tag, commit, force=force, origin=origin))
             for wiki in WIKIS]  # fmt: skip
    for (name, _, _, branch), clone in ready:
        if clone is None:
            continue
        if dry_run:
            print(f"{name}: not pushed (DRY_RUN); the commit is in {clone}")
            continue
        git("push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=clone)
        print(f"{name}: published {tag} as {git('rev-parse', '--short', 'HEAD', cwd=clone)}")


def preview() -> None:
    commit = git("rev-parse", "HEAD")
    modified = git("status", "--porcelain", "--", "wiki", "tools/wiki", "src")
    source = f"commit {commit[:12]}" + ("-modified" if modified else "")
    build(ROOT, WORK / "site", version_of(ROOT), source, commit)
    print(f"built {WORK / 'site'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    publishing = sub.add_parser("publish")
    publishing.add_argument("--version")
    publishing.add_argument("--dry-run", action="store_true")
    publishing.add_argument("--force", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]  # in step with stderr
    try:
        if args.command == "build":
            preview()
        else:
            publish(args.version, args.dry_run, args.force)
    except PublishError as exc:
        print(f"wiki-publish: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
