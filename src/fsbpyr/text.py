"""Small text helpers for messages."""


def plural(count: int, noun: str) -> str:
    """``count`` and ``noun``, the noun in the plural unless the count is one."""
    return f"{count} {noun}{'s' * (count != 1)}"


def shown(value: object) -> str:
    """``value`` as a line of a report: repr() when it is not printable, so that a name read from
    a file cannot rewrite the terminal."""
    text = str(value)
    return text if text.isprintable() else repr(text)


def tail_lines(text: str, limit: int) -> str:
    """The last ``limit`` characters of ``text``, from a line boundary, with a note of how many
    earlier lines were left out. Text longer than ``limit`` with no newline after the cut is cut
    as it is: there is no boundary to move to."""
    text = text.rstrip("\n")
    if len(text) <= limit:
        return text
    start = len(text) - limit
    if text[start - 1] != "\n":
        newline = text.find("\n", start)
        if newline == -1:
            return text[start:]
        start = newline + 1
    left_out = text.count("\n", 0, start)
    return f"({plural(left_out, 'earlier line')} left out)\n{text[start:]}"
