"""The stub's _dl_find_object (launcher/glibc.c), probed (tests/probes/find_object.c): libgcc's
unwinder, which 3.15's libpython links in, looks frames up with it. It is glibc's own where
glibc has one (2.35 and newer); on 2.34 the program headers answer, and must name the object
glibc would: the same link map and unwind data, a mapping that holds the address."""

import os
import subprocess
from pathlib import Path

import pytest

from .conftest import LauncherBuild

pytestmark = pytest.mark.slow

PROBE = Path(__file__).parents[1] / "probes" / "find_object.c"
NAMES = ("program", "libc", "libm", "stack", "null")


@pytest.fixture(scope="module")
def probe(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("probe") / "find_object"
    flags = ("-Wl,--wrap=_dl_find_object", "-Wl,--wrap=dlvsym")
    return launcher_build.compile(out, [PROBE, "glibc.c"], *flags)


Answer = tuple[int, int, int, int, int]  # rc, mapping start and end, link map, unwind data


def _answer(fields: list[str]) -> Answer:
    rc, start, end, link_map, eh_frame = fields
    return int(rc), int(start, 16), int(end, 16), int(link_map, 16), int(eh_frame, 16)


def _answers(probe: Path, *, old_glibc: bool) -> dict[str, tuple[int, Answer, Answer]]:
    """Per address: the address, the wrapper's answer and glibc's own."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PROBE_")}
    if old_glibc:
        env["PROBE_OLD_GLIBC"] = "1"
    result = subprocess.run([probe], capture_output=True, text=True, check=True, timeout=10,
                            env=env)  # fmt: skip
    *lines, refused = result.stdout.splitlines()
    assert refused == f"refused {int(old_glibc)}"  # the wrapper looks glibc's up once
    answers = {}
    for line in lines:
        name, address, *fields = line.split()
        answers[name] = (int(address, 16), _answer(fields[:5]), _answer(fields[5:]))
    assert tuple(answers) == NAMES
    return answers


def test_where_glibc_has_one_the_wrapper_is_glibcs_own(probe: Path) -> None:
    answers = _answers(probe, old_glibc=False)

    assert all(wrapper == own for _, wrapper, own in answers.values())
    assert [own[0] for _, _, own in answers.values()] == [0, 0, 0, -1, -1]


def test_on_glibc_2_34_the_program_headers_name_the_same_object(probe: Path) -> None:
    for name, (address, wrapper, own) in _answers(probe, old_glibc=True).items():
        rc, start, end, link_map, eh_frame = wrapper
        assert (rc, link_map, eh_frame) == (own[0], own[3], own[4]), name
        if rc == 0:
            assert start <= address < end and eh_frame != 0, name
