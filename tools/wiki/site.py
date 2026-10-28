"""Turn the pages in wiki/ of a repository tree into the pages of the published wiki.

Usage: python -I site.py REPO_ROOT OUT_DIR VERSION [REF]
Writes every wiki/*.md into OUT_DIR, and copies wiki/images/ (hand-made images) to
OUT_DIR/images/. Outside fenced code blocks, links change from the repository's form (lint.py)
to the wiki's:
  Page.md#anchor        -> Page#anchor
  ../path#L10           -> https://github.com/devitops-com/fsbpyr/blob/REF/path#L10
  ../dir/, ../          -> https://github.com/devitops-com/fsbpyr/tree/REF/dir
REF is what the repository links point at: the tag vVERSION unless given (publish.py gives the
commit of main it publishes). @VERSION@ becomes VERSION everywhere. Images, the PDFs and other
URLs stay as they are.
"""

import re
import shutil
import sys
from pathlib import Path

REPO = "https://github.com/devitops-com/fsbpyr/"
INLINE = re.compile(r"(\]\()([^)\s]+)(\))")
REFDEF = re.compile(r"^(\[[^\]]+\]:\s+)(\S+)", re.M)
FENCE = re.compile(r"^(`{3,}|~{3,})[^\n]*\n.*?^\1[^\n]*$", re.M | re.S)


def wiki_target(target: str, root: Path, pages: set[str], ref: str) -> str:
    """The published form of one link target; links into the repository point at ref."""
    if target.startswith("../"):
        path, sep, frag = target.removeprefix("../").partition("#")
        path = path.rstrip("/")
        kind = "tree" if not path or (root / path).is_dir() else "blob"
        return f"{REPO}{kind}/{ref}" + (f"/{path}" if path else "") + sep + frag
    name, sep, frag = target.partition("#")
    if name.endswith(".md") and "/" not in name and name[:-3] in pages:
        return name[:-3] + sep + frag
    return target


def publish_text(
    text: str, root: Path, pages: set[str], version: str, ref: str | None = None
) -> str:
    """The published form of a page; repository links point at ref (default vVERSION)."""
    target_ref = ref or f"v{version}"

    def rewrite(match: re.Match[str]) -> str:
        groups = match.groups()
        new = wiki_target(groups[1], root, pages, target_ref)
        return groups[0] + new + (groups[2] if len(groups) > 2 else "")

    parts, start = [], 0
    for fence in FENCE.finditer(text):  # code blocks stay as they are
        parts += [REFDEF.sub(rewrite, INLINE.sub(rewrite, text[start:fence.start()])), fence[0]]
        start = fence.end()
    parts.append(REFDEF.sub(rewrite, INLINE.sub(rewrite, text[start:])))
    return "".join(parts).replace("@VERSION@", version)


def main() -> None:
    root, out, version = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    ref = sys.argv[4] if len(sys.argv) > 4 else None
    wiki = root / "wiki"
    pages = {p.stem for p in wiki.glob("*.md")}
    out.mkdir(parents=True, exist_ok=True)
    for page in sorted(wiki.glob("*.md")):
        (out / page.name).write_text(publish_text(page.read_text(), root, pages, version, ref))
    if (wiki / "images").is_dir():
        shutil.copytree(wiki / "images", out / "images", dirs_exist_ok=True)


if __name__ == "__main__":
    main()
