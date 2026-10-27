"""Check wiki pages: wiki links, images, repository links at the tag, anchors, plan mentions.

Usage: python -I lint.py WIKI_DIR TAG_TREE
WIKI_DIR holds the pages (*.md) and images/; TAG_TREE is the extracted release tree, whose
src/fsbpyr/__init__.py gives the tag (vX.Y.Z) that repository links must name.
"""

import re
import sys
from pathlib import Path

REPO = "https://github.com/devitops-com/fsbpyr/"
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
PLAN = re.compile(
    r"\b(planned|Phase \d+|D\d{1,2}\b|IMPLEMENTATION_PLAN|improvement-plan|will be added)"
)


def slug(heading: str) -> str:
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def anchors(page: Path) -> set[str]:
    found = set()
    in_code = False
    for line in page.read_text().splitlines():
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code and line.startswith("#"):
            found.add(slug(line.lstrip("#")))
    return found


def release_tag(tree: Path) -> str:
    match = re.search(r'__version__ = "([^"]+)"', (tree / "src/fsbpyr/__init__.py").read_text())
    return "v" + match.group(1)


def check_repo(target: str, tree: Path, tag: str) -> str | None:  # noqa: PLR0911  one per problem
    rest = target.removeprefix(REPO)
    if rest == f"tree/{tag}":
        return None
    if re.match(r"(blob|tree)/v\d", rest) and not rest.startswith((f"blob/{tag}/", f"tree/{tag}/")):
        return f"not at {tag}: {target}"
    for kind in (f"blob/{tag}/", f"tree/{tag}/"):
        if rest.startswith(kind):
            path, _, frag = rest.removeprefix(kind).partition("#")
            local = tree / path
            if not local.exists():
                return f"missing in tag: {path}"
            if frag.startswith("L") and local.is_file():
                lines = len(local.read_text(errors="replace").splitlines())
                numbers = [int(n) for n in re.findall(r"\d+", frag)]
                if any(n > lines for n in numbers):
                    return f"line anchor past end ({lines}): {path}#{frag}"
            return None
    if "/blob/main/" in target or "/tree/main/" in target:
        return "points at main"
    return None


def main() -> None:  # noqa: PLR0912  one branch per kind of link
    wiki, tree = Path(sys.argv[1]), Path(sys.argv[2])
    tag = release_tag(tree)
    pages = {p.stem: p for p in wiki.glob("*.md")}
    page_anchors = {name: anchors(p) for name, p in pages.items()}
    problems = 0
    for name, page in sorted(pages.items()):
        text = page.read_text()
        for target in LINK.findall(text):
            if target.startswith(("http://", "https://")):
                issue = check_repo(target, tree, tag) if target.startswith(REPO) else None
            elif target.startswith("#"):
                issue = None if target[1:] in page_anchors[name] else f"anchor not found: {target}"
            elif target.startswith("mailto:"):
                issue = None
            else:
                base, _, frag = target.partition("#")
                if base in pages:
                    issue = (f"anchor not found: {base}#{frag}"
                             if frag and frag not in page_anchors[base] else None)
                elif (wiki / base).is_file():  # a file of the wiki, such as a guide's PDF
                    issue = None
                else:
                    issue = f"no such page or file: {base}"
            if issue:
                problems += 1
                print(f"{name}: {issue}")
        for target in IMAGE.findall(text):
            if not (wiki / target).exists():
                problems += 1
                print(f"{name}: missing image {target}")
        for match in sorted(set(PLAN.findall(text))):
            print(f"{name}: mentions {match!r}")
    print(f"{len(pages)} pages, {problems} problems")


if __name__ == "__main__":
    main()
