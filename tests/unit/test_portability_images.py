"""Which images the portability matrix runs on a host (tests/support/images.py)."""

import pytest

from ..support.images import EL8_IMAGES, IMAGES, NO_ARM64, images_for


def test_x86_64_runs_all_twelve_images_in_their_order() -> None:
    assert images_for("x86_64") == IMAGES
    assert len(IMAGES) == 12 and len(set(IMAGES)) == 12


@pytest.mark.parametrize("machine", ["aarch64", "arm64"])
def test_aarch64_runs_the_images_with_an_arm64_variant(machine: str) -> None:
    images = images_for(machine)

    assert {"docker.io/library/archlinux:latest"} == NO_ARM64
    assert images == tuple(image for image in IMAGES if image not in NO_ARM64)
    assert len(images) == 11


def test_the_el8_images_are_glibc_2_28_ones_apart_from_the_matrix() -> None:
    assert set(EL8_IMAGES).isdisjoint(IMAGES) and not set(EL8_IMAGES) & NO_ARM64
    assert all(":8" in image for image in EL8_IMAGES)
