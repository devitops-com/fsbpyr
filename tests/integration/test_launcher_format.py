"""launcher/format.h and fsbpyr.payload.format must describe the same bytes."""

import importlib.resources
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from fsbpyr.payload import format as fmt

PROBE = r"""
#include <stdio.h>
#include "format.h"
#define FIELD(s, f) printf("field " #s "." #f " %zu %zu\n", \
    offsetof(struct s, f), sizeof(((struct s *)0)->f))
int main(void) {
    printf("sizeof trailer %zu\nsizeof header %zu\nsizeof blob %zu\n",
           sizeof(struct fsb_trailer), sizeof(struct fsb_header), sizeof(struct fsb_blob));
    printf("sizeof module %zu\nsizeof file %zu\nsizeof dist %zu\n",
           sizeof(struct fsb_module), sizeof(struct fsb_file), sizeof(struct fsb_dist));
    printf("const version %u\nconst page %u\nconst blob_align %u\nconst none %u\n",
           FSB_FORMAT_VERSION, FSB_PAGE_SIZE, FSB_BLOB_ALIGN, FSB_NONE);
    printf("const sections %d\nconst max_blob %llu\n", FSB_SECTION_COUNT,
           (unsigned long long)FSB_MAX_BLOB_SIZE);
    FIELD(fsb_header, python_hexversion); FIELD(fsb_header, bytecode_magic);
    FIELD(fsb_header, generation_id); FIELD(fsb_header, app_name); FIELD(fsb_header, sections);
    FIELD(fsb_blob, codec); FIELD(fsb_blob, digest);
    FIELD(fsb_module, kind); FIELD(fsb_module, flags); FIELD(fsb_module, code);
    FIELD(fsb_module, dist);
    FIELD(fsb_file, kind); FIELD(fsb_file, mode); FIELD(fsb_file, blob); FIELD(fsb_file, closure);
    return 0;
}
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[int, ...]]:
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("no C compiler")
    work = tmp_path_factory.mktemp("probe")
    (work / "probe.c").write_text(PROBE)
    header = importlib.resources.files("fsbpyr.launcher").joinpath("format.h").read_bytes()
    (work / "format.h").write_bytes(header)
    binary = work / "probe"
    subprocess.run([compiler, "-std=c11", "-o", binary, work / "probe.c"], check=True)
    output = subprocess.run([binary], check=True, capture_output=True, text=True).stdout
    return {
        " ".join(line.split()[:2]): tuple(int(v) for v in line.split()[2:])
        for line in output.splitlines()
    }


def test_record_sizes_match(probe: dict[str, tuple[int, ...]]) -> None:
    assert probe["sizeof trailer"] == (fmt.TRAILER.size,)
    assert probe["sizeof header"] == (fmt.HEADER.size,)
    assert probe["sizeof blob"] == (fmt.BLOB.size,)
    assert probe["sizeof module"] == (fmt.MODULE.size,)
    assert probe["sizeof file"] == (fmt.FILE.size,)
    assert probe["sizeof dist"] == (fmt.DIST.size,)


def test_constants_match(probe: dict[str, tuple[int, ...]]) -> None:
    assert probe["const version"] == (fmt.FORMAT_VERSION,)
    assert probe["const page"] == (fmt.PAGE_SIZE,)
    assert probe["const blob_align"] == (fmt.BLOB_ALIGN,)
    assert probe["const none"] == (fmt.NONE,)
    assert probe["const sections"] == (len(fmt.Section),)
    assert probe["const max_blob"] == (fmt.MAX_BLOB_SIZE,)


def _offset(layout: str, index: int) -> int:
    """Byte offset of field ``index`` in a little-endian struct format (no implicit padding)."""
    codes = re.findall(r"\d*[a-zA-Z?]", layout.lstrip("<"))
    return struct.calcsize("<" + "".join(codes[:index]))


@pytest.mark.parametrize(
    ("name", "layout", "index"),
    [
        ("fsb_header.python_hexversion", fmt.HEADER.format, 3),
        ("fsb_header.bytecode_magic", fmt.HEADER.format, 4),
        ("fsb_header.generation_id", fmt.HEADER.format, 5),
        ("fsb_header.app_name", fmt.HEADER.format, 6),
        ("fsb_header.sections", fmt.HEADER.format, 12),
        ("fsb_blob.codec", fmt.BLOB.format, 3),
        ("fsb_blob.digest", fmt.BLOB.format, 6),
        ("fsb_module.kind", fmt.MODULE.format, 4),
        ("fsb_module.flags", fmt.MODULE.format, 5),
        ("fsb_module.code", fmt.MODULE.format, 7),
        ("fsb_module.dist", fmt.MODULE.format, 10),
        ("fsb_file.kind", fmt.FILE.format, 2),
        ("fsb_file.mode", fmt.FILE.format, 4),
        ("fsb_file.blob", fmt.FILE.format, 5),
        ("fsb_file.closure", fmt.FILE.format, 7),
    ],
)
def test_field_offsets_match(
    probe: dict[str, tuple[int, ...]], name: str, layout: str, index: int
) -> None:
    assert probe[f"field {name}"][0] == _offset(layout, index)


def test_probe_header_matches_packaged_header() -> None:
    packaged = importlib.resources.files("fsbpyr.launcher").joinpath("format.h")
    assert Path(str(packaged)).read_bytes().startswith(b"/* fsbpyr executable format v1")
