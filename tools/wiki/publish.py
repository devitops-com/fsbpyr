"""Build the wiki from the repository, and publish it to the GitHub and the GitLab wiki.

Usage (from the repository's top directory; the Makefile's wiki targets run it):
  python -I tools/wiki/publish.py build
      the wiki of this checkout into dist/wiki/site, to look at; nothing is pushed
  python -I tools/wiki/publish.py publish [--version X.Y.Z] [--dry-run] [--force]
      the wiki of release vX.Y.Z (default: the newest release tag) to both wikis

The pages live in wiki/ and are edited there, in the repository; the wikis are the published
copy and are never edited by hand. `publish` builds from the release tag's own tree (git
archive), so the wiki describes exactly that release, with that tag's tools; it refuses a tag
GitHub does not have, and a tag whose tree has no wiki/. The same build goes to both wikis;
only names differ where the wikis need them (GitLab's front page and sidebar are home.md and
_sidebar.md). GitLab is pushed first, then GitHub; nothing is pushed until both are ready.

Every publish commit carries a `Published-From: vX.Y.Z <commit>` trailer. A wiki with a
commit after the last such commit was changed by hand: `publish` lists those commits and
stops, unless --force, which replaces them (and is needed once, for a wiki that was never
published this way). It also stops before publishing an older release over a newer one, unless
--force. A wiki that already has the build gets no commit. --dry-run stops before pushing and
shows what would change.

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


def build(tree: Path, out: Path, version: str, source: str, commit: str) -> None:
    """The published wiki of `tree`, built by the tree's own tools/wiki/build.sh, into out."""
    date = git("show", "-s", "--format=%cs", commit)
    epoch = git("show", "-s", "--format=%ct", commit)
    result = subprocess.run(["bash", str(tree / "tools" / "wiki" / "build.sh"), str(tree),  # noqa: S603, S607
                             str(out), version, source, date, epoch], check=False)  # fmt: skip
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


def release_tree(tag: str) -> tuple[Path, str]:
    """The tree of `tag`, extracted under dist/wiki, and its commit."""
    commit = git("rev-parse", "-q", "--verify", f"{tag}^{{commit}}", check=False)
    if not commit:
        raise PublishError(f"no tag {tag} here: git fetch --tags")
    # The tag itself (an annotated tag's object, a lightweight tag's commit) must be GitHub's.
    local = git("rev-parse", f"refs/tags/{tag}")
    remote = git("ls-remote", "--tags", SOURCE, f"refs/tags/{tag}").split()
    if remote[:1] != [local]:
        raise PublishError(f"{SOURCE} does not have this tag {tag} ({local[:12]}): publish "
                           "released tags only")  # fmt: skip
    tree = WORK / tag / "tree"
    shutil.rmtree(tree, ignore_errors=True)
    tree.mkdir(parents=True)
    archive = subprocess.run(["git", "archive", tag], cwd=ROOT, capture_output=True,  # noqa: S603, S607
                             check=True).stdout  # fmt: skip
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)  # noqa: S603, S607
    if not (tree / "wiki").is_dir():
        raise PublishError(f"{tag} has no wiki/: its wiki was not published from the repository")
    if version_of(tree) != tag.removeprefix("v"):
        raise PublishError(f"{tag}'s src/fsbpyr/__init__.py names version {version_of(tree)}")
    return tree, commit


def last_published(clone: Path) -> str | None:
    """The tag the wiki was last published from, if it ever was."""
    body = git("log", "-1", "--format=%B", f"--grep=^{TRAILER}", cwd=clone, check=False)
    match = re.search(rf"^{TRAILER}(\S+)", body, re.M)
    return match.group(1) if match else None


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


def prepare(wiki: tuple[str, str, dict[str, str], str], site: Path, tag: str, commit: str, *,
            force: bool) -> Path | None:  # fmt: skip
    """A clone of the wiki (name, address, copies, branch) with the publish commit made on the
    branch; None if the wiki is up to date."""
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
    message = (f"docs: publish the wiki of fsbpyr {tag}\n\nMade by make wiki-publish from tag "
               f"{tag} of the fsbpyr repository; edit the pages in its wiki/ directory, not "
               f"here.\n\n{TRAILER}{tag} {commit}\n")  # fmt: skip
    git("commit", "-q", "-m", message, cwd=clone)
    return clone


def publish(version: str | None, dry_run: bool, force: bool) -> None:
    tag = f"v{version}" if version else latest_tag()
    numbers(tag)
    tree, commit = release_tree(tag)
    site = WORK / tag / "site"
    build(tree, site, tag.removeprefix("v"), f"tag {tag}, commit {commit[:12]}", commit)
    ready = [(wiki, prepare(wiki, site, tag, commit, force=force)) for wiki in WIKIS]
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
