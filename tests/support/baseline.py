"""The glibc baseline the tests build for: the EL8 test mode (D46).

FSBPYR_TEST_GLIBC=2.28 makes the tests that build and run executables use the glibc 2.28 stub:
the end-to-end ``runtime`` fixture is the 2.28 runtime, every `fsbpyr build` of a test is a
`--glibc 2.28` one (tests/conftest.py), the portability matrix adds the EL8 images and the
corpus looks for wheels at manylinux_2_28. Unset, the default baseline, 2.34. The EL8 workflow
(.github/workflows/el8.yml) and `make test-el8` run the stub-dependent tests this way."""

import os

from fsbpyr.runtime import pins

VARIABLE = "FSBPYR_TEST_GLIBC"
BASELINE = os.environ.get(VARIABLE) or pins.DEFAULT_BASELINE
EL8 = BASELINE != pins.DEFAULT_BASELINE


def as_tuple(baseline: str) -> tuple[int, int]:
    """``"2.28"`` as ``(2, 28)``."""
    major, minor = baseline.split(".")
    return int(major), int(minor)
