"""A cache root's way from "/" (D38, plan 8e; gc.way_problem, launcher/way.h's rule): every
directory on it, the root's own included, owned by root or by us and writable by no other user
unless sticky; symlinks, root's or ours, followed one at a time. The tool's cache and the
collector refuse an untrusted way; the launcher's (C) is compared with it in
tests/support/contract.py."""

import errno
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fsbpyr.boot._fsbpyr_rt import gc
from fsbpyr.boot._fsbpyr_rt.gc import EXPOSED, FOREIGN, Problem
from fsbpyr.runtime import registry

SRC = Path(__file__).parents[3] / "src"
UNSHARE, PODMAN = shutil.which("unshare"), shutil.which("podman")
OTHER_GROUPS = [g for g in os.getgroups() if g != os.getegid()]


@pytest.fixture
def exposed(tmp_path: Path) -> Path:
    """A directory any user may write to, not sticky."""
    directory = tmp_path / "open"
    directory.mkdir()
    directory.chmod(0o777)
    return directory


def test_a_way_through_our_own_directories_is_trusted(tmp_path: Path) -> None:
    (tmp_path / "a" / "cache").mkdir(parents=True)

    assert gc.way_problem(str(tmp_path / "a" / "cache")) is None
    assert gc.way_problem(str(tmp_path / "missing" / "cache")) is None  # made, then checked


def test_a_directory_others_may_write_to_is_refused(exposed: Path) -> None:
    (exposed / "cache").mkdir()

    assert gc.way_problem(str(exposed / "cache")) == Problem(str(exposed), reason=EXPOSED)
    assert gc.way_problem(str(exposed / "missing")) == Problem(str(exposed), reason=EXPOSED)
    assert gc.way_problem(str(exposed)) == Problem(str(exposed), reason=EXPOSED)  # the root too


def test_a_sticky_directory_is_trusted(exposed: Path) -> None:
    exposed.chmod(0o1777)  # as /tmp

    assert gc.way_problem(str(exposed / "cache")) is None


def test_a_directory_our_own_group_may_write_to_is_trusted(tmp_path: Path) -> None:
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared").chmod(0o775)  # a user-private group's

    assert gc.way_problem(str(tmp_path / "shared" / "cache")) is None


@pytest.mark.skipif(not OTHER_GROUPS, reason="needs a supplementary group")
def test_a_directory_another_group_may_write_to_is_refused(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    try:
        os.chown(shared, -1, OTHER_GROUPS[0])
    except OSError as exc:  # a group no one inside a namespace maps to, say
        pytest.skip(f"cannot give a directory to group {OTHER_GROUPS[0]}: {exc}")
    shared.chmod(0o775)  # as staff's /usr/local

    assert gc.way_problem(str(shared / "cache")) == Problem(str(shared), reason=EXPOSED)


@pytest.mark.skipif(os.geteuid() == 0 or Path("/").stat().st_uid != 0, reason="needs root's /")
def test_a_directory_of_another_user_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ways = [p for p in (tmp_path, *tmp_path.parents) if p.stat().st_uid != 0]
    monkeypatch.setattr(os, "geteuid", lambda: 4242)  # what was ours is another user's now

    assert gc.way_problem(str(tmp_path / "cache")) == Problem(str(ways[-1]), reason=FOREIGN)


def test_a_symlink_of_another_user_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One planted in a sticky directory: whose target is whatever its owner wants."""
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    lstat = os.lstat

    def planted(path: str | os.PathLike[str]) -> os.stat_result:
        info = lstat(path)
        if os.fspath(path) != str(tmp_path / "link"):
            return info
        return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink, 4242,
                               info.st_gid, info.st_size, 0, 0, 0))  # fmt: skip

    monkeypatch.setattr(os, "lstat", planted)

    assert gc.way_problem(str(tmp_path / "link" / "c")) == Problem(str(tmp_path / "link"),
                                                                  reason=FOREIGN)  # fmt: skip


def test_a_symlink_is_followed_and_its_targets_way_checked(tmp_path: Path, exposed: Path) -> None:
    (tmp_path / "trusted" / "cache").mkdir(parents=True)
    (exposed / "cache").mkdir()
    (tmp_path / "good").symlink_to(tmp_path / "trusted")
    (tmp_path / "relative").symlink_to("trusted")
    (tmp_path / "bad").symlink_to(exposed / "cache")

    assert gc.way_problem(str(tmp_path / "good" / "cache")) is None
    assert gc.way_problem(str(tmp_path / "relative" / "cache")) is None
    assert gc.way_problem(str(tmp_path / "bad")) == Problem(str(exposed), reason=EXPOSED)


def test_forty_symlinks_are_followed_and_forty_one_are_a_loop(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    for i in range(41):
        (tmp_path / f"l{i}").symlink_to(f"l{i - 1}" if i else "real")

    assert gc.way_problem(str(tmp_path / "l39" / "cache")) is None
    assert gc.way_problem(str(tmp_path / "l40" / "cache")) == Problem(str(tmp_path / "l0"),
                                                                     errno.ELOOP)  # fmt: skip


def test_a_loop_a_file_and_a_long_name_on_the_way_are_refused(tmp_path: Path) -> None:
    (tmp_path / "a").symlink_to(tmp_path / "b")
    (tmp_path / "b").symlink_to(tmp_path / "a")
    (tmp_path / "file").write_bytes(b"")
    long = str(tmp_path / ("x" * 256) / "cache")

    assert gc.way_problem(str(tmp_path / "a" / "cache")).error == errno.ELOOP  # type: ignore[union-attr]
    assert gc.way_problem(str(tmp_path / "file" / "c")) == Problem(str(tmp_path / "file"),
                                                                  errno.ENOTDIR)  # fmt: skip
    assert gc.way_problem(long) == Problem(long, errno.ENAMETOOLONG)


def test_dot_dot_goes_through_what_it_names(tmp_path: Path, exposed: Path) -> None:
    """As the kernel resolves it: "open/../cache" passes through "open", and "missing/.." does
    not exist (it could lead out of what was checked)."""
    assert gc.way_problem(str(exposed / ".." / "cache")) == Problem(str(exposed), reason=EXPOSED)
    assert gc.way_problem(str(tmp_path / "x" / ".." / "cache")) == Problem(
        str(tmp_path / "x"), errno.ENOENT
    )
    (tmp_path / "x").mkdir()
    assert gc.way_problem(str(tmp_path / "x" / ".." / "cache")) is None


def test_a_relative_root_is_taken_from_the_working_directory(
    exposed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(exposed)

    assert gc.way_problem("cache/inner") == Problem(str(exposed), reason=EXPOSED)


def _in_a_namespace(command: list[str], path: Path | str) -> str:
    """way_problem(``path``) as ``command`` (unshare -r, podman unshare) runs it, printed."""
    code = ("import sys; sys.path[:0] = [sys.argv[1]]\n"
            "from fsbpyr.boot._fsbpyr_rt.gc import way_problem\n"
            "print(way_problem(sys.argv[2]))")  # fmt: skip
    if subprocess.run([*command, "true"], capture_output=True, check=False).returncode != 0:
        pytest.skip(f"no such namespace here: {command}")
    result = subprocess.run([*command, sys.executable, "-I", "-c", code, SRC, str(path)],
                            capture_output=True, text=True, check=False)  # fmt: skip
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _roots_in_tmp() -> list[Path]:
    """Root's own directories in /tmp (sticky: anyone may add one): .ICE-unix, say."""
    return sorted(p for p in Path("/tmp").iterdir()  # noqa: S108  the case
                  if p.is_dir() and not p.is_symlink() and p.stat().st_uid == 0)  # fmt: skip


@pytest.mark.skipif(UNSHARE is None, reason="needs unshare")
def test_in_a_user_namespace_owners_outside_it_count_as_roots(tmp_path: Path) -> None:
    """There root's own directories ("/", "/tmp") show as nobody's: a sandbox's layout."""
    assert _in_a_namespace([str(UNSHARE), "-r"], tmp_path / "cache") == "None"


@pytest.mark.skipif(UNSHARE is None or not _roots_in_tmp(), reason="needs unshare, /tmp/.X")
def test_below_a_shared_directory_an_owner_outside_the_namespace_is_refused() -> None:
    """Anyone may add a directory to /tmp: below it, nobody's could be anyone's."""
    shown = _roots_in_tmp()[0]

    out = _in_a_namespace([str(UNSHARE), "-r"], shown / "cache")

    assert out == repr(Problem(str(shown), reason=FOREIGN))


@pytest.mark.skipif(PODMAN is None, reason="needs podman")
def test_where_the_overflow_uid_is_mapped_nobody_is_another_user(tmp_path: Path) -> None:
    """podman unshare maps 65536 uids, 65534 among them: a real nobody may own what shows as
    such, "/" included."""
    out = _in_a_namespace([str(PODMAN), "unshare"], tmp_path / "cache")

    assert out == repr(Problem("/", reason=FOREIGN))


def test_the_tools_cache_below_a_directory_others_may_write_to_is_refused(exposed: Path) -> None:
    with pytest.raises(registry.RuntimeCacheError, match="other users may write to it"):
        registry.secure_directory(exposed / "fsbpyr")
    assert not (exposed / "fsbpyr").exists()


def test_the_tools_cache_refuses_a_symlink_of_another_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One planted in a sticky directory, to a directory of ours it could re-point later."""
    (tmp_path / "ours").mkdir()
    link = tmp_path / "planted"
    link.symlink_to(tmp_path / "ours")
    lstat = os.lstat

    def planted(path: str | os.PathLike[str], **kwargs: object) -> os.stat_result:
        info = lstat(path, **kwargs)  # type: ignore[arg-type]
        if os.fspath(path) != str(link):
            return info
        return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink, 4242,
                               info.st_gid, info.st_size, 0, 0, 0))  # fmt: skip

    monkeypatch.setattr(os, "lstat", planted)

    with pytest.raises(registry.RuntimeCacheError, match="owned by another user"):
        registry.secure_directory(link)


def test_the_tools_cache_through_a_symlink_of_ours_is_its_targets(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir(mode=0o755)
    (tmp_path / "link").symlink_to(tmp_path / "real")

    assert registry.secure_directory(tmp_path / "link") == tmp_path / "link"

    assert (tmp_path / "real").stat().st_mode & 0o777 == 0o700  # made private, not the link


def test_the_tools_cache_itself_is_made_private(tmp_path: Path) -> None:
    """fsbpyr's own: made 0700 rather than refused; parents made 0755 whatever the umask."""
    mask = os.umask(0)
    try:
        registry.secure_directory(tmp_path / "made" / "fsbpyr")
    finally:
        os.umask(mask)

    assert (tmp_path / "made").stat().st_mode & 0o777 == 0o755
    assert (tmp_path / "made" / "fsbpyr").stat().st_mode & 0o777 == 0o700


def test_a_collection_below_a_directory_others_may_write_to_is_refused(exposed: Path) -> None:
    root = exposed / "cache"
    tree = root / "runtime" / ("a" * 32)
    (tree / "site").mkdir(parents=True)
    (root / gc.MARKER).write_bytes(gc.MARKER_TEXT)
    os.utime(tree, (0, 0))

    report = gc.collect(str(root), pressure=True)

    assert tree.exists() and report.errors == [Problem(str(exposed), reason=EXPOSED).text()]
