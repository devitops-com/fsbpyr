"""Entry inference: main guards found with ast, candidates read from RECORD and the tree."""

from pathlib import Path

import pytest

from fsbpyr.entry import has_main_guard, installed_candidates, source_has_candidate


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("if __name__ == '__main__':\n    main()\n", True),
        ('if "__main__" == __name__:\n    main()\n', True),
        ("def f():\n    if __name__ == '__main__':\n        pass\n", False),
        ("# if __name__ == '__main__':\nx = 1\n", False),
        ("x = '__main__'\n", False),
        ("if __name__ != '__main__':\n    pass\n", False),
        ("print 'py2 __main__'\n", False),
    ],
)
def test_main_guard_is_found_in_the_module_body_only(source: str, expected: bool) -> None:
    assert has_main_guard(source) is expected


def test_installed_candidates_read_the_top_level_of_the_record(tmp_path: Path) -> None:
    (tmp_path / "tool.py").write_text("if __name__ == '__main__':\n    pass\n")
    (tmp_path / "lib.py").write_text("x = 1\n")
    record = (
        "pkg/__main__.py,sha256=x,1", "pkg/sub/__main__.py,,", "tool.py,,", "lib.py,,",
        "../../../bin/old,,", "pkg-1.dist-info/RECORD,,",
    )  # fmt: skip

    found = installed_candidates(tmp_path, record)

    assert (found.packages, found.modules) == (("pkg",), ("tool",))


def test_the_source_tree_has_a_candidate_only_for_a_main_file_or_a_guard(tmp_path: Path) -> None:
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "lib" / "util.py").write_text("x = 1\n")
    (tmp_path / "setup.py").write_text("if __name__ == '__main__':\n    pass\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "__main__.py").write_text("")
    assert not source_has_candidate(tmp_path)

    (tmp_path / "src" / "lib" / "__main__.py").write_text("")

    assert source_has_candidate(tmp_path)


def test_a_bom_and_a_coding_cookie_do_not_hide_the_guard() -> None:
    guard = 'if __name__ == "__main__":\n    pass\n'

    assert has_main_guard(b"\xef\xbb\xbf" + guard.encode())
    assert has_main_guard(b"# coding: latin-1\n# \xe9\n" + guard.encode())
