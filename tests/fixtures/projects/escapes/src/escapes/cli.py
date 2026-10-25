"""Compiles with a warning: the pattern is a normal string with an invalid escape sequence."""

import re

PATTERN = "\d+"  # noqa: W605  the warning is the point


def main() -> int:
    print("escapes", re.findall(PATTERN, "a1b22"))
    return 0
