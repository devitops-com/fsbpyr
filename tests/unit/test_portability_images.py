"""Which images the portability matrix runs on a host (tests/support/images.py)."""

import pytest

from ..support.images import IMAGES, NO_ARM64, images_for


def test_x86_64_runs_all_eleven_images_in_their_order() -> None:
    assert images_for("x86_64") == IMAGES
    assert len(IMAGES) == 11 and len(set(IMAGES)) == 11


@pytest.mark.parametrize("machine", ["aarch64", "arm64"])
def test_aarch64_runs_the_images_with_an_arm64_variant(machine: str) -> None:
    images = images_for(machine)

    assert {"docker.io/library/archlinux:latest"} == NO_ARM64
    assert images == tuple(image for image in IMAGES if image not in NO_ARM64)
    assert len(images) == 10
