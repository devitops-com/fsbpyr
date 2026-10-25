"""The EL8 test mode and the corpus selection (tests/support/baseline.py, corpus.py): the same
tests run for glibc 2.34 or 2.28, and a few corpus apps can be run alone (el8.yml)."""

from pathlib import Path

import pytest

from fsbpyr import config
from fsbpyr.project import Project

from ..support import baseline, corpus
from ..support.images import EL8_IMAGES, IMAGES, NO_ARM64, images_for

HELLO = Path(__file__).parents[1] / "fixtures" / "projects" / "hello"


def test_a_build_in_a_test_is_for_the_baseline_the_run_is_in() -> None:
    """Both modes: 2.34 without FSBPYR_TEST_GLIBC, 2.28 with it (tests/conftest.py); `make
    test-el8-stub` runs this file in the mode."""
    assert config.load_config(Project.load(HELLO)).glibc == baseline.BASELINE
    flagged = config.load_config(Project.load(HELLO), glibc="2.34")
    assert flagged.glibc == "2.34"  # a flag still wins


def test_a_baseline_is_a_pair_of_numbers() -> None:
    assert baseline.as_tuple("2.28") == (2, 28)
    assert baseline.as_tuple("2.34") == (2, 34)


def test_the_el8_mode_adds_the_el8_images_to_the_matrix() -> None:
    assert images_for("x86_64", "2.28") == (*IMAGES, *EL8_IMAGES)
    assert images_for("aarch64", "2.28") == (
        *(image for image in IMAGES if image not in NO_ARM64), *EL8_IMAGES)
    assert images_for("x86_64", "2.34") == images_for("x86_64") == IMAGES


def test_without_a_selection_the_corpus_test_runs_every_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(corpus.SELECTION, raising=False)

    assert corpus.selected() == corpus.apps()


def test_a_selection_names_the_apps_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(corpus.SELECTION, " pillow, lxml ,lxml,")

    assert corpus.selected() == ["lxml", "pillow"]


def test_a_selection_of_an_app_that_does_not_exist_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(corpus.SELECTION, "lxml,nosuchapp")

    with pytest.raises(ValueError, match="names no corpus app: nosuchapp"):
        corpus.selected()
