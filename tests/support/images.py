"""The container images of the portability matrix (tests/e2e/test_portability.py)."""

IMAGES = (  # glibc 2.34 (the baseline) up to the newest releases: what their vendors support
    "docker.io/library/rockylinux:9",  # 2.34
    "docker.io/library/amazonlinux:2023",  # 2.34
    "docker.io/library/ubuntu:22.04",  # 2.35
    "docker.io/library/debian:12",  # 2.36
    "docker.io/library/ubuntu:24.04",  # 2.39
    "docker.io/library/almalinux:10",  # 2.39
    "docker.io/opensuse/leap:16.0",  # 2.40
    "docker.io/library/debian:13",  # 2.41
    "docker.io/library/ubuntu:26.04",  # 2.43
    "docker.io/library/fedora:latest",  # 2.43
    "docker.io/library/archlinux:latest",  # 2.44
)
# Images with no arm64 variant (`podman manifest inspect`, checked 2026-10-22: the other ten
# have one); Arch Linux ARM is a different project's.
NO_ARM64 = frozenset({"docker.io/library/archlinux:latest"})


def images_for(machine: str) -> tuple[str, ...]:
    """The images that run on a host whose ``platform.machine()`` is ``machine``: all of them
    on x86-64, those with an arm64 variant on aarch64."""
    if machine in ("aarch64", "arm64"):
        return tuple(image for image in IMAGES if image not in NO_ARM64)
    return IMAGES
