import pytest

from fsbpyr.text import plural, tail_lines


@pytest.mark.parametrize(
    ("count", "text"), [(0, "0 files"), (1, "1 file"), (2, "2 files")]
)
def test_plural_adds_the_s_unless_the_count_is_one(count: int, text: str) -> None:
    assert plural(count, "file") == text


def test_output_within_the_limit_is_unchanged() -> None:
    assert tail_lines("a\nb\n", 100) == "a\nb"


def test_a_cut_in_the_middle_of_a_line_starts_at_the_next_line() -> None:
    text = "aaaa\nbbbb\ncccc\ndddd"

    assert tail_lines(text, 12) == "(2 earlier lines left out)\ncccc\ndddd"


def test_one_line_left_out_is_one_line() -> None:
    assert tail_lines("aaaa\nbbbb\ncccc", 9) == "(1 earlier line left out)\nbbbb\ncccc"


def test_a_cut_that_lands_on_a_line_start_keeps_that_line() -> None:
    assert tail_lines("aa\nbbbb\ncccc", 9) == "(1 earlier line left out)\nbbbb\ncccc"


def test_long_output_without_a_newline_after_the_cut_is_cut_as_it_is() -> None:
    assert tail_lines("a" * 50, 10) == "a" * 10
    assert tail_lines("first\n" + "b" * 50, 10) == "b" * 10
