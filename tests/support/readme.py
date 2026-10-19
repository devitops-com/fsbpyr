"""The README's quickstart: tests/e2e/test_build.py runs it, tests/unit/test_cli_docs.py parses
its fsbpyr commands."""

import re
from pathlib import Path

README = Path(__file__).parents[2] / "README.md"
START = "<!-- quickstart: tests/e2e/test_build.py runs these sh blocks -->\n"
END = "<!-- /quickstart -->\n"
_SHELL = re.compile(r"^```sh\n(.*?)^```$", re.MULTILINE | re.DOTALL)


def quickstart() -> str:
    """The shell lines between the markers: every ``sh`` block there, in order (the
    pyproject.toml shown between them is not run)."""
    text = README.read_text()
    block = text[text.index(START) + len(START) : text.index(END)]
    return "".join(_SHELL.findall(block))
