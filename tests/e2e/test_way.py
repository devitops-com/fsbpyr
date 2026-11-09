"""The cache root's way (D38, launcher/way.c), probed case by case (tests/probes/way.c): the
owner outside the user namespace (the overflow uid, read from faked /proc files, for "/", which
every walk passes, so that a sticky temporary directory above the tests changes nothing), the
symlink limit, empty and failed link reads, the buffer limits at their very bounds, "." and "..",
names of NAME_MAX bytes, relative roots and a working directory that is gone, failed allocations,
and a walk whose directory is descriptor 0. These are what `make mutate FILE=way.c` found no test
for (#128)."""

import errno
import os
import subprocess
from pathlib import Path

import pytest

from .conftest import LauncherBuild

pytestmark = pytest.mark.slow

PROBE = Path(__file__).parents[1] / "probes" / "way.c"
PROBE_FLAGS = ("-Wl,--wrap=readlinkat,--wrap=fopen,--wrap=fstat,--wrap=fstatat,--wrap=malloc",
               "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined")  # fmt: skip
PATH_MAX, NAME_MAX, SYMLINKS = 4096, 255, 40  # <linux/limits.h>, way.c's limit
OVERFLOW = 65534  # what the tests' /proc/sys/kernel/overflowuid says


@pytest.fixture(scope="module")
def probe(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("probe") / "way"
    return launcher_build.compile(out, [PROBE, "way.c"], *PROBE_FLAGS)


@pytest.fixture
def base(tmp_path: Path) -> Path:
    """A directory of ours, mode 0700, by its real path."""
    base = Path(os.path.realpath(tmp_path))
    base.chmod(0o700)
    return base


def _walk(probe: Path, root: str | Path, cwd: Path | None = None,
          faked: dict[str, str] | None = None, **env: str) -> str:  # fmt: skip
    clean = {k: v for k, v in os.environ.items() if not k.startswith("PROBE_")}
    result = subprocess.run([probe, os.fspath(root)], capture_output=True, text=True, cwd=cwd,
                            env={**clean, **(faked or {}), **env}, check=False,
                            timeout=60)  # fmt: skip
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _exposed(path: Path) -> Path:
    path.mkdir()
    path.chmod(0o777)
    return path


# --- an owner outside the user namespace ------------------------------------------------------


def _outside(base: Path, overflow: str, uid_map: str) -> dict[str, str]:
    """The environment that makes "/" the overflow uid's, with these /proc files."""
    files = {}
    for name, content in (("overflowuid", overflow), ("uid_map", uid_map)):
        if content == "-":
            files[name] = "-"
        else:
            (base / name).write_text(content)
            files[name] = str(base / name)
    return {"PROBE_OWNED": "/", "PROBE_OWNER": str(OVERFLOW),
            "PROBE_OVERFLOWUID": files["overflowuid"], "PROBE_UID_MAP": files["uid_map"]}


@pytest.mark.parametrize(("overflow", "uid_map", "trusted"), [
    (f"{OVERFLOW}\n", "0 1000 1\n", True),  # a rootless namespace: the overflow uid maps to none
    (f"{OVERFLOW}\n", "0 0 4294967295\n", False),  # the initial namespace: every uid is mapped
    (f"{OVERFLOW}\n", "0 1000 1\n1 100000 65536\n", False),  # the second range maps it
    (f"{OVERFLOW}\n", f"{OVERFLOW} 0 1\n", False),  # the range's first uid
    (f"{OVERFLOW}\n", f"{OVERFLOW - 1} 0 1\n", True),  # the uid after a range
    (f"{OVERFLOW}\n", "0 0 100\n", True),  # a range below it
    (f"{OVERFLOW}\n", f"{OVERFLOW + 1} 0 10\n", True),  # a range above it
    (f"{OVERFLOW}\n", "not a range\n0 0 4294967295\n", True),  # reading stops at a bad line
    (f"{OVERFLOW - 1}\n", "0 1000 1\n", False),  # another uid than the overflow one
    ("nonsense\n", "0 1000 1\n", False),
    ("-", "0 1000 1\n", False),  # no overflowuid to read
    (f"{OVERFLOW}\n", "-", False),  # no uid_map to read
], ids=["unmapped", "initial namespace", "second range", "first of a range", "after a range",
        "below", "above", "bad line", "another uid", "unreadable overflowuid",
        "no overflowuid", "no uid_map"])  # fmt: skip
def test_the_overflow_uid_unmapped_owns_like_root(
    probe: Path, base: Path, overflow: str, uid_map: str, trusted: bool
) -> None:
    """"/" shown as the overflow uid with no uid of the namespace mapping to it belongs to an
    owner outside it (the sandbox's own layout); anything else is another user's."""
    verdict = _walk(probe, base, faked=_outside(base, overflow, uid_map))

    assert verdict == ("TRUSTED 0 -" if trusted else "FOREIGN 0 /")


def test_an_outside_owner_does_not_count_below_a_sticky_directory(
    probe: Path, base: Path
) -> None:
    """Others may add entries there: an owner outside the namespace could be anyone."""
    sticky = base / "sticky"
    sticky.mkdir()
    sticky.chmod(0o1777)
    (sticky / "sub").mkdir()
    env = {**_outside(base, f"{OVERFLOW}\n", "0 1000 1\n"), "PROBE_OWNED": str(sticky / "sub")}

    assert _walk(probe, sticky / "sub", faked=env) == f"FOREIGN 0 {sticky}/sub"


def test_an_outside_owner_counts_above_any_sticky_directory_not_at_slash_only(
    probe: Path, base: Path
) -> None:
    """The base's first directory below "/" (/tmp or /home, say) shown as nobody's, not sticky."""
    env = {**_outside(base, f"{OVERFLOW}\n", "0 1000 1\n"), "PROBE_OWNED": f"/{base.parts[1]}",
           "PROBE_OWNED_MODE": "755"}  # fmt: skip

    assert _walk(probe, base, faked=env) == "TRUSTED 0 -"


@pytest.mark.parametrize(("way", "owned", "mode", "trusted"), [
    ("s/link", "/", None, True),  # the link's absolute target: from "/" again
    ("s/sub/..", "/", None, True),  # on up to "/"
    ("/..", "/", "1777", True),  # "/" itself the sticky one: back at it, as at the start
    ("s/sub/../inside", "s/inside", None, False),  # a ".." that stays below it
    ("s/link/../s/inside", "s/inside", None, False),  # the target's way below it again
], ids=["absolute target", "dot dot above", "dot dot to it", "dot dot below",
        "below again"])  # fmt: skip
def test_a_walk_that_leaves_a_sticky_directory_is_no_longer_below_it(
    probe: Path, base: Path, way: str, owned: str, mode: str | None, trusted: bool
) -> None:
    """In a rootless namespace "/" is nobody's: neither a symlink's absolute target below /tmp nor
    a ".." above it is refused there, nor /tmp itself when a ".." comes back to it; a directory of
    nobody's below it still is."""
    sticky = base / "s"
    sticky.mkdir()
    sticky.chmod(0o1777)
    for directory in (sticky / "sub", sticky / "inside", base / "target"):
        directory.mkdir()
    (sticky / "link").symlink_to(base / "target")
    root = way if way.startswith("/") else f"{base}/{way}"
    if way == "s/sub/..":  # on up to "/": a ".." for each part of the base
        root += "/.." * len(base.parts)
    env = {**_outside(base, f"{OVERFLOW}\n", "0 1000 1\n"),
           "PROBE_OWNED": "/" if owned == "/" else str(base / owned)}  # fmt: skip
    if mode is not None:
        env["PROBE_OWNED_MODE"] = mode

    assert _walk(probe, root, faked=env) == ("TRUSTED 0 -" if trusted else
                                             f"FOREIGN 0 {base / owned}")  # fmt: skip


# --- symlinks ----------------------------------------------------------------------------------


def _chain(base: Path, links: int) -> Path:
    """``links`` symlinks in a row, each to the next, the last to a directory; the first."""
    (base / "end").mkdir()
    for i in range(links, 0, -1):
        (base / f"l{i}").symlink_to(base / ("end" if i == links else f"l{i + 1}"))
    return base / "l1"


def test_forty_symlinks_on_the_way_are_followed(probe: Path, base: Path) -> None:
    assert _walk(probe, _chain(base, SYMLINKS)) == "TRUSTED 0 -"


def test_the_forty_first_symlink_is_a_loop(probe: Path, base: Path) -> None:
    assert _walk(probe, _chain(base, SYMLINKS + 1)) == (
        f"SYSTEM {errno.ELOOP} {base}/l{SYMLINKS + 1}")


def test_a_symlink_read_as_empty_is_no_way(probe: Path, base: Path) -> None:
    (base / "link").symlink_to(_exposed(base / "open"))

    assert _walk(probe, base / "link", PROBE_LINK="link", PROBE_LENGTH="0") == (
        f"SYSTEM {errno.ENOENT} {base}/link")


def test_a_symlink_that_cannot_be_read_gives_its_errno(probe: Path, base: Path) -> None:
    (base / "link").symlink_to(base)

    assert _walk(probe, base / "link", PROBE_LINK="link", PROBE_LINK_ERRNO=str(errno.EIO)) == (
        f"SYSTEM {errno.EIO} {base}/link")


def test_what_follows_a_symlink_is_walked_from_its_target(probe: Path, base: Path) -> None:
    (base / "dir").mkdir()
    _exposed(base / "dir" / "e")
    (base / "link").symlink_to(base / "dir")

    assert _walk(probe, base / "link" / "e") == f"EXPOSED 0 {base}/dir/e"


@pytest.mark.parametrize(("extra", "followed"), [(0, True), (1, False)],
                         ids=["the buffer's size", "a byte more"])  # fmt: skip
def test_a_target_and_the_rest_fill_the_buffer_to_its_size_not_beyond(
    probe: Path, base: Path, extra: int, followed: bool
) -> None:
    """The target (4095 bytes, slashes in front), "/" and what is left after the link (with its
    NUL) must fit the walk's 2 * PATH_MAX + 2 bytes."""
    (base / "long").symlink_to(_exposed(base / "open"))
    rest = 2 * PATH_MAX + 2 - 2 - (PATH_MAX - 1) + extra  # what is left after "long", its "/"
    tail = "".join("/" + "a" * (NAME_MAX - 1) for _ in range(rest // NAME_MAX))
    tail += "/" + "b" * (rest - len(tail) - 1)
    assert len(tail) == rest

    verdict = _walk(probe, f"{base}/long{tail}", PROBE_LINK="long", PROBE_LENGTH=str(PATH_MAX - 1))

    assert verdict == (f"EXPOSED 0 {base}/open" if followed
                       else f"SYSTEM {errno.ENAMETOOLONG} {base}/long")  # fmt: skip


# --- parts of the way --------------------------------------------------------------------------


def test_a_part_of_one_byte_is_a_part_and_dot_is_none(probe: Path, base: Path) -> None:
    _exposed(base / "x")

    assert _walk(probe, f"{base}/x") == f"EXPOSED 0 {base}/x"
    assert _walk(probe, f"{base}/./x") == f"EXPOSED 0 {base}/x"
    assert _walk(probe, f"{base}/.") == "TRUSTED 0 -"


def test_dot_dot_goes_up_and_at_the_top_stays_there(probe: Path, base: Path) -> None:
    (base / "a").mkdir()
    _exposed(base / "e")

    assert _walk(probe, f"{base}/a/../e") == f"EXPOSED 0 {base}/e"
    assert _walk(probe, f"/..{base}/e") == f"EXPOSED 0 {base}/e"  # "/..": "/", as the kernel


def test_a_name_of_name_max_bytes_is_walked(probe: Path, base: Path) -> None:
    longest = _exposed(base / ("n" * NAME_MAX))

    assert _walk(probe, longest) == f"EXPOSED 0 {longest}"
    assert _walk(probe, f"{base}/missing/{'n' * NAME_MAX}") == "PARTIAL 0 -"


@pytest.mark.parametrize("length", [NAME_MAX + 1, 300])
def test_a_longer_name_is_refused(probe: Path, base: Path, length: int) -> None:
    root = f"{base}/{'n' * length}"

    assert _walk(probe, root) == f"SYSTEM {errno.ENAMETOOLONG} {root}"


def test_a_missing_part_ends_the_walk_unless_dot_dot_follows_it(probe: Path, base: Path) -> None:
    assert _walk(probe, f"{base}/missing/y") == "PARTIAL 0 -"
    assert _walk(probe, f"{base}/missing/../y") == f"SYSTEM {errno.ENOENT} {base}/missing"


@pytest.mark.parametrize(("extra", "refused"), [(-1, False), (0, True)],
                         ids=["a byte short", "at the bound"])  # fmt: skip
def test_the_path_walked_stops_at_its_buffer(
    probe: Path, base: Path, extra: int, refused: bool
) -> None:
    """The real path walked, "/" and the next part may take PATH_MAX + NAME_MAX + 1 bytes: a tree
    deeper than PATH_MAX, made a directory at a time."""
    bound = PATH_MAX + NAME_MAX + 2
    depth = (bound - 4 - len(str(base))) // (NAME_MAX - 3)  # the last part 2 to 253 bytes long
    names = ["d" * (NAME_MAX - 4)] * depth
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in names:
            os.mkdir(name, dir_fd=fd)
            inner = os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = inner
    finally:
        os.close(fd)
    walked = len(str(base)) + sum(1 + len(n) for n in names)
    last = "z" * (bound - walked - 1 + extra)
    root = "/".join((str(base), *names, last))

    verdict = _walk(probe, root)

    assert verdict == (f"SYSTEM {errno.ENAMETOOLONG} {root[:PATH_MAX - 1]}" if refused
                       else "PARTIAL 0 -")  # fmt: skip


@pytest.mark.parametrize(("extra", "refused"), [(0, False), (1, True)],
                         ids=["the buffer's size", "a byte more"])  # fmt: skip
def test_a_root_fills_the_buffer_to_its_size_not_beyond(
    probe: Path, base: Path, extra: int, refused: bool
) -> None:
    """"/", the root and its NUL in 2 * PATH_MAX + 2 bytes."""
    root = f"{base}/missing"
    root += "/a" * ((2 * PATH_MAX - len(root)) // 2)
    root += "b" * (2 * PATH_MAX - len(root) + extra)

    verdict = _walk(probe, root)

    assert verdict == (f"SYSTEM {errno.ENAMETOOLONG} {root[:PATH_MAX - 1]}" if refused
                       else "PARTIAL 0 -")  # fmt: skip


def test_a_relative_root_is_walked_from_the_working_directory(probe: Path, base: Path) -> None:
    _exposed(base / "open")

    assert _walk(probe, "open", cwd=base) == f"EXPOSED 0 {base}/open"


def test_a_relative_root_without_a_working_directory_is_refused(
    probe: Path, base: Path
) -> None:
    gone = base / "gone"
    gone.mkdir()
    clean = {k: v for k, v in os.environ.items() if not k.startswith("PROBE_")}

    result = subprocess.run(["/bin/sh", "-c", 'cd "$1" && rmdir "$1" && exec "$2" open', "_",
                             gone, probe], capture_output=True, text=True, env=clean,
                            check=True, timeout=60)  # fmt: skip

    assert result.stdout.strip() == f"SYSTEM {errno.ENOENT} ."


@pytest.mark.parametrize("which", ["1", "2", "3"])
def test_a_failed_allocation_is_memory_and_nothing_else(
    probe: Path, base: Path, which: str
) -> None:
    assert _walk(probe, base, PROBE_FAIL_MALLOC=which) == "MEMORY 0 -"


@pytest.mark.parametrize("last", [True, False])
def test_a_walk_in_descriptor_0_closes_it_as_any_other(
    probe: Path, base: Path, last: bool
) -> None:
    """With standard input closed, the walk's first directory, "/", is descriptor 0: it is used,
    and closed when the walk moves on, or at the end when "/" is the root."""
    root = "/" if last else base
    assert _walk(probe, root, PROBE_CLOSE_STDIN="1", PROBE_FDS="1") == "TRUSTED 0 - fds 0"
