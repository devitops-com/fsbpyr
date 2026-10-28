"""Check the wiki pages in wiki/ of a repository tree: every link and image has a target.

Usage: python -I lint.py REPO_ROOT [--mentions]
Prints one line per problem and exits 1 if there is any. --mentions also lists the pages'
mentions of plans and decisions (informational).

The pages link the way files of the repository do; site.py turns the links into wiki links
when the wiki is built:
  Page.md, Page.md#anchor, #anchor   another page or a heading; the anchor is GitHub's
  ../path, ../path#L10-L20           a file or directory of the repository, lines in range
  ../doc.md#heading                  a heading of a Markdown file of the repository
  images/name.svg                    a diagram drawn from wiki/diagrams/name.dot, an import
                                     graph, or an SVG in wiki/images/
  fsbpyr-User-Guide.pdf, ...         the PDFs the build makes
A link into the repository pinned to a tag (blob/vX.Y.Z/...) is a problem: the build pins
every relative link to the tag or commit it publishes.
"""

import re
import sys
from pathlib import Path

REPO = "https://github.com/devitops-com/fsbpyr/"
# Every link target, the image's of an image and the outer one of a linked image alike (as
# site.py rewrites them), and every reference definition.
LINK = re.compile(r"\]\(([^)\s]+)\)|^\[[^\]]+\]:\s+(\S+)", re.M)
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
TITLED = re.compile(r"\]\(([^)\s]+)\s+[\"'(]")  # a link with a title: neither checked nor rewritten
FENCE = re.compile(r"^(`{3,}|~{3,})[^\n]*\n.*?^\1[^\n]*$", re.M | re.S)
PLAN = re.compile(
    r"\b(planned|Phase \d+|D\d{1,2}\b|IMPLEMENTATION_PLAN|improvement-plan|will be added)"
)
# The import graphs render.sh draws from the code: importgraphs.OUTPUTS, which a test compares.
IMPORT_GRAPHS = ("arch-subsystems", "arch-modules-top", "arch-modules-payload",
                 "arch-modules-runtime", "arch-modules-boot")  # fmt: skip
# The PDFs tools/wiki/Makefile builds.
PDFS = ("fsbpyr-User-Guide.pdf", "fsbpyr-Developer-Guide.pdf")


def slug(heading: str) -> str:
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def anchors(text: str) -> set[str]:
    found: set[str] = set()
    for line in FENCE.sub("", text).splitlines():
        if line.startswith("#"):
            base = slug(line.lstrip("#"))
            anchor, n = base, 0
            while anchor in found:  # GitHub numbers a repeated heading: x, x-1, x-2
                n += 1
                anchor = f"{base}-{n}"
            found.add(anchor)
    return found


def drawn_names(wiki: Path) -> set[str]:
    """The diagrams the build draws, as image names."""
    names = {f"{p.stem}.svg" for p in (wiki / "diagrams").glob("*.dot")}
    return names | {f"{name}.svg" for name in IMPORT_GRAPHS}


def check_repo_path(root: Path, target: str) -> str | None:
    path, _, frag = target.removeprefix("../").partition("#")
    local = root / path
    if not local.exists():
        return f"no such file or directory in the repository: {path}"
    if frag and local.suffix == ".md" and not frag.startswith("L"):
        heading = frag in anchors(local.read_text(errors="replace"))
        return None if heading else f"no such heading: {target}"
    if frag:
        if not (re.fullmatch(r"L\d+(-L\d+)?", frag) and local.is_file()):
            return f"not a line anchor of a file: {target}"
        lines = len(local.read_text(errors="replace").splitlines())
        if any(int(n) > lines for n in re.findall(r"\d+", frag)):
            return f"line anchor past the end ({lines} lines): {target}"
    return None


def check_link(  # noqa: PLR0911  one return per kind of target
    root: Path, page: str, target: str, page_anchors: dict[str, set[str]], images: set[str]
) -> str | None:
    if target.startswith(REPO) and re.match(r"(blob|tree)/v\d", target.removeprefix(REPO)):
        return f"pinned to a tag, write it relative (../path): {target}"
    if target.startswith(("http://", "https://", "mailto:")):
        return None
    if target.startswith("#"):
        return None if target[1:] in page_anchors[page] else f"no such anchor: {target}"
    if target.startswith("../"):
        return check_repo_path(root, target)
    if target.startswith("images/"):
        return None if target.removeprefix("images/") in images else f"no such image: {target}"
    if target in PDFS:
        return None
    name, _, frag = target.partition("#")
    if name.endswith(".md") and name[:-3] in page_anchors:
        if frag and frag not in page_anchors[name[:-3]]:
            return f"no such anchor: {target}"
        return None
    return f"no such page or target: {target}"


def check(root: Path) -> list[str]:
    """Every problem of the pages in root/wiki, as 'Page: problem'."""
    wiki = root / "wiki"
    texts = {p.stem: p.read_text() for p in sorted(wiki.glob("*.md"))}
    page_anchors = {name: anchors(text) for name, text in texts.items()}
    drawn = drawn_names(wiki)
    made = {p.name for p in (wiki / "images").glob("*") if p.is_file()}
    problems = [f"wiki/images/{name}: has the name of a drawn diagram"
                for name in sorted(made & drawn)]  # fmt: skip
    # Git ignores PNGs and PDFs under wiki/ (generated files are never kept): only SVGs are.
    problems += [f"wiki/images/{name}: not an SVG; images in wiki/images/ are SVGs"
                 for name in sorted(made) if not name.endswith(".svg")]  # fmt: skip
    images = drawn | made
    for name, text in texts.items():
        prose = FENCE.sub("", text)
        for match in LINK.finditer(prose):
            target = match.group(1) or match.group(2)
            if issue := check_link(root, name, target, page_anchors, images):
                problems.append(f"{name}: {issue}")
        for target in TITLED.findall(prose):
            problems.append(f"{name}: a link with a title, write it without: {target}")
        for target in IMAGE.findall(prose):
            if target.removeprefix("images/") not in images or not target.startswith("images/"):
                problems.append(f"{name}: no such image: {target}")
    return problems


def main() -> None:
    root = Path(sys.argv[1])
    problems = check(root)
    for problem in problems:
        print(problem)
    if "--mentions" in sys.argv[2:]:
        for page in sorted((root / "wiki").glob("*.md")):
            for match in sorted(set(PLAN.findall(page.read_text()))):
                print(f"{page.stem}: mentions {match!r}")
    pages = len(list((root / "wiki").glob("*.md")))
    print(f"wiki-lint: {pages} pages, {len(problems)} problems")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
