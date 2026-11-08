"""`make mutate`: mutation testing of the launcher's C code (owner, 2026-11-08, finding Q-02).

    mutate-c.py SOURCE TEST... [--lines FIRST-LAST] [--timeout SECONDS] [--command CMD]
                [--root DIR]

SOURCE is a file of src/fsbpyr/launcher (way.c, sig.c, cache.c), each TEST a pytest file or node
whose probes compile it (tests/e2e/test_way_targets.py, ...). The checkout is copied once into a
temporary directory (without .git, .venv and dist); then, for each mutant (one operator changed
at one place outside comments, strings, character constants and preprocessor lines), SOURCE is
rewritten there and the tests run: `uv run --locked pytest -x -q -p no:cacheprovider --no-cov
TEST...`, or CMD in a shell. Tests that fail kill the mutant, and so does a run longer than the
timeout (default: four times the run without a mutant, which must pass); tests that pass let it
survive. Each survivor is printed with its line and its change: a test is missing there, or the
mutant does what the code did (an equivalent one). Exit 0 when none survived, 1 when one did, 2
on bad usage or when the tests fail without a mutant.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = Path("src/fsbpyr/launcher")
LEFT_OUT = (".git", ".venv", "dist", "__pycache__", ".mypy_cache", ".ruff_cache", "*.egg-info")
# (pattern in code, replacement): each match is one mutant. Longer operators are matched apart
# from the shorter ones they hold: `<` is not the `<` of `<=`, `<<` or `->`.
OPERATORS = (
    (r"==", "!="), (r"!=", "=="),
    (r"(?<![<>=!])<=", "<"), (r"(?<![<>=!])>=", ">"),
    (r"(?<![<\-])<(?![<=])", "<="), (r"(?<![>\-])>(?![>=])", ">="),
    (r"&&", "||"), (r"\|\|", "&&"),
    (r"\breturn true\b", "return false"), (r"\breturn false\b", "return true"),
    (r"\breturn 0;", "return 1;"), (r"\breturn -1;", "return 0;"),
    (r" \+ 1\b", " - 1"), (r" - 1\b", " + 1"),
)  # fmt: skip


@dataclass(frozen=True)
class Mutant:
    line: int
    offset: int  # in the source
    original: str
    replacement: str

    def apply(self, text: str) -> str:
        return text[: self.offset] + self.replacement + text[self.offset + len(self.original):]


def code_only(text: str) -> str:
    """``text`` with comments, string literals, character constants and preprocessor lines
    blanked to spaces (newlines kept), so that offsets and lines stay those of ``text``."""
    out = list(text)

    def blank(first: int, last: int) -> None:
        out[first:last] = ["\n" if ch == "\n" else " " for ch in text[first:last]]

    i, n, line_start = 0, len(text), True
    while i < n:
        c = text[i]
        if line_start and c in " \t":
            i += 1
            continue
        if line_start and c == "#":  # a preprocessor line, with its continuations
            end = text.find("\n", i)
            while end > 0 and text[end - 1] == "\\":
                end = text.find("\n", end + 1)
            end = n if end < 0 else end
        elif text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
        elif c in "\"'":
            end = i + 1
            while end < n and text[end] != c:
                end += 2 if text[end] == "\\" else 1
            end = min(end + 1, n)
        else:
            line_start = c == "\n"
            i += 1
            continue
        blank(i, end)
        line_start = False
        i = end
    return "".join(out)


def mutants(text: str, lines: range | None = None) -> list[Mutant]:
    """Every mutant of ``text``, in the order of the source; within ``lines`` if given."""
    code = code_only(text)
    found = []
    for pattern, replacement in OPERATORS:
        for match in re.finditer(pattern, code):
            line = text.count("\n", 0, match.start()) + 1
            if lines is None or line in lines:
                found.append(Mutant(line, match.start(), match.group(), replacement))
    return sorted(found, key=lambda m: (m.offset, m.replacement))


def run(command: list[str] | str, cwd: Path, timeout: float) -> tuple[bool, float]:
    """(whether it passed, seconds); a run past ``timeout`` did not pass."""
    start = time.monotonic()
    try:
        done = subprocess.run(command, cwd=cwd, shell=isinstance(command, str),  # noqa: S603
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=timeout, check=False)  # fmt: skip
    except subprocess.TimeoutExpired:
        return False, timeout
    return done.returncode == 0, time.monotonic() - start


def span(text: str) -> range:
    first, _, last = text.partition("-")
    if not (first.isdigit() and last.isdigit() and int(first) <= int(last)):
        raise argparse.ArgumentTypeError(f"{text}: FIRST-LAST, line numbers")
    return range(int(first), int(last) + 1)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="mutate-c.py", description=__doc__.split("\n")[0])
    parser.add_argument("source", help="a file of src/fsbpyr/launcher, such as way.c")
    parser.add_argument("tests", nargs="+", help="the pytest files or nodes that compile it")
    parser.add_argument("--lines", type=span, help="only mutants on these lines (FIRST-LAST)")
    parser.add_argument("--timeout", type=float, help="seconds a run may take (default: 4x)")
    parser.add_argument("--command", help="run this in a shell instead of pytest")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    original = (args.root / LAUNCHER / args.source).read_text() if (
        args.root / LAUNCHER / args.source).is_file() else None  # fmt: skip
    if original is None:
        print(f"mutate-c: no {LAUNCHER / args.source} in {args.root}", file=sys.stderr)
        return 2
    command: list[str] | str = args.command or [
        "uv", "run", "--locked", "pytest", "-x", "-q", "-p", "no:cacheprovider", "--no-cov",
        *args.tests]  # fmt: skip
    found = mutants(original, args.lines)
    with tempfile.TemporaryDirectory(prefix="fsbpyr-mutate.") as scratch:
        tree = Path(scratch) / "tree"
        shutil.copytree(args.root, tree, ignore=shutil.ignore_patterns(*LEFT_OUT), symlinks=True)
        target = tree / LAUNCHER / args.source
        passed, seconds = run(command, tree, args.timeout or 3600)
        if not passed:
            print("mutate-c: the tests fail without a mutant: fix them first", file=sys.stderr)
            return 2
        timeout = args.timeout or max(4 * seconds, 30)
        print(f"{len(found)} mutants of {args.source}; the tests took {seconds:.0f} s")
        survived = []
        for mutant in found:
            target.write_text(mutant.apply(original))
            passed, _ = run(command, tree, timeout)
            verdict = "SURVIVED" if passed else "killed"
            print(f"{verdict:<8} {args.source}:{mutant.line}  `{mutant.original}` -> "
                  f"`{mutant.replacement}`", flush=True)  # fmt: skip
            if passed:
                survived.append(mutant)
        target.write_text(original)
    killed = len(found) - len(survived)
    print(f"{killed} of {len(found)} killed" + (
        f", {len(survived)} survived:" if survived else ""))  # fmt: skip
    lines = original.splitlines()
    for mutant in survived:
        print(f"  {args.source}:{mutant.line}: `{mutant.original}` -> `{mutant.replacement}`  "
              f"{lines[mutant.line - 1].strip()}")  # fmt: skip
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
