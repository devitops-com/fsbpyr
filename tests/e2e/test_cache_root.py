"""The launcher's cache root (D38, plan 8e; launcher/way.h): every directory on its way from "/",
its own included, must be owned by root or the user (in a user namespace, also by an owner
outside it) and writable by no other user unless sticky, symlinks followed one at a time; the
root itself must be the user's. A root that fails is refused before anything is written. Each
case as the release launcher and its ASan/UBSan build run it; tests/support/contract.py
compares the C walk with gc.way_problem."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from fsbpyr.assemble import App, build_executable
from fsbpyr.payload.format import FileKind
from fsbpyr.payload.spec import Blob, File
from fsbpyr.runtime.registry import Runtime

from .conftest import app_content

pytestmark = [pytest.mark.slow, pytest.mark.sanitized]
UNSHARE, PODMAN = shutil.which("unshare"), shutil.which("podman")

LIBRARY = b"\x7fELF x" * 999
_SYNCS = """\
import os
from _fsbpyr_rt.native import native_cache, NativeCacheError
import _fsbpyr

def main():
    files = _fsbpyr.files_below("x.libs")
    if os.environ.get("GONE"):  # a working directory removed under the app
        import tempfile
        gone = tempfile.mkdtemp()
        os.chdir(gone)
        os.rmdir(gone)
    try:
        if root := os.environ.get("ROOT"):  # as given: relative, say (native_cache's is not)
            checked = bytearray((_fsbpyr.info()["files"] + 7) // 8)
            _fsbpyr.sync(root, "runtime/g/site", files, checked, False)
        else:
            native_cache().sync(files)
    except (NativeCacheError, OSError) as exc:
        print(exc)
    else:
        print("synced")
"""


@pytest.fixture(scope="module")
def app(runtime: Runtime, tmp_path_factory: pytest.TempPathFactory) -> Path:
    native = File("x.libs/libx.so", FileKind.NATIVE_LIB, Blob.zstd(LIBRARY, digest=True),
                  mode=0o755)  # fmt: skip
    modules, files = app_content({"x.py": _SYNCS})
    out = tmp_path_factory.mktemp("root") / "x"
    build_executable(runtime, App("x", "x:main", modules=modules, files=(*files, native)), out)
    return out


def _run(app: Path, cache: Path | str, *, root: str = "", cwd: Path | None = None,
         prefix: tuple[str, ...] = (), gone: bool = False) -> str:  # fmt: skip
    """What the app said; ``root``: synced as given, not as native_cache makes it; ``gone``: in
    a working directory removed meanwhile."""
    env = {**os.environ, "FSBPYR_CACHE_DIR": str(cache), "FSBPYR_GC": "0", "ROOT": root,
           "GONE": "1" if gone else ""}  # fmt: skip
    result = subprocess.run([*prefix, app], capture_output=True, text=True, check=False,
                            env=env, cwd=cwd, timeout=60)  # fmt: skip
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _foreign_directory() -> Path | None:
    """A directory of another user (not root) that we may search, if the system has one."""
    for parent in (Path("/var/lib"), Path("/etc"), Path("/var/spool"), Path("/home")):
        try:
            candidates = sorted(parent.iterdir())
        except OSError:
            continue
        for path in candidates:
            try:
                info = path.lstat()
            except OSError:
                continue
            if (path.is_dir() and not path.is_symlink() and info.st_uid not in (0, os.geteuid())
                    and os.access(path, os.X_OK)):  # fmt: skip
                return path
    return None


@pytest.fixture
def exposed(tmp_path: Path) -> Path:
    """A directory any user may write to, not sticky."""
    directory = tmp_path / "open"
    directory.mkdir()
    directory.chmod(0o777)
    return directory


def test_a_root_below_a_directory_others_may_write_to_is_refused(app: Path, exposed: Path) -> None:
    out = _run(app, exposed / "cache")

    assert out == f"refusing cache directory {exposed}: other users may write to it"
    assert not any(exposed.iterdir())  # nothing made below it


def test_a_symlinked_root_is_followed_and_its_targets_way_checked(
    app: Path, tmp_path: Path, exposed: Path
) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "good").symlink_to(tmp_path / "real")
    (exposed / "cache").mkdir()
    (tmp_path / "bad").symlink_to(exposed / "cache")

    assert _run(app, tmp_path / "good") == "synced"
    assert next((tmp_path / "real").rglob("libx.so")).read_bytes() == LIBRARY
    refused = f"refusing cache directory {exposed}: other users may write to it"
    assert _run(app, tmp_path / "bad") == refused
    assert not any((exposed / "cache").iterdir())


def test_a_sticky_directory_others_may_write_to_is_fine(app: Path, exposed: Path) -> None:
    exposed.chmod(0o1777)  # as /tmp: nobody may rename or remove what is ours there

    assert _run(app, exposed / "cache") == "synced"


@pytest.mark.skipif(os.geteuid() == 0, reason="root owns /usr")
def test_a_root_of_another_user_is_refused(app: Path) -> None:
    assert _run(app, "/usr") == "refusing cache directory /usr: owned by another user"


def test_a_root_reached_through_a_symlink_loop_is_refused(app: Path, tmp_path: Path) -> None:
    (tmp_path / "a").symlink_to(tmp_path / "b")
    (tmp_path / "b").symlink_to(tmp_path / "a")

    assert "Too many levels of symbolic links" in _run(app, tmp_path / "a" / "cache")


def test_a_relative_root_is_taken_from_the_working_directory(
    app: Path, tmp_path: Path, exposed: Path
) -> None:
    assert _run(app, tmp_path, root="rel", cwd=tmp_path) == "synced"
    assert (tmp_path / "rel" / "runtime" / "g" / "site" / "x.libs" / "libx.so").exists()
    refused = f"refusing cache directory {exposed}: other users may write to it"
    assert _run(app, tmp_path, root="rel", cwd=exposed) == refused


def test_a_root_through_dot_dot_is_checked_as_the_kernel_walks_it(
    app: Path, tmp_path: Path, exposed: Path
) -> None:
    (tmp_path / "real").mkdir()
    refused = f"refusing cache directory {exposed}: other users may write to it"

    assert _run(app, tmp_path, root=f"{exposed}/../real/c") == refused
    assert "No such file" in _run(app, tmp_path, root=f"{tmp_path}/real/no/../../open/c")
    assert not (tmp_path / "real" / "no").exists() and not (exposed / "c").exists()
    assert _run(app, tmp_path, root=f"{tmp_path}/real/../real/c") == "synced"


def test_a_directory_of_another_user_on_the_way_is_refused(app: Path, tmp_path: Path) -> None:
    foreign = _foreign_directory()
    if foreign is None or os.geteuid() == 0:
        pytest.skip("no directory of another user to walk through")

    out = _run(app, tmp_path, root=f"{foreign}/cache")

    assert out == f"refusing cache directory {foreign}: owned by another user"


@pytest.mark.skipif(UNSHARE is None, reason="needs unshare")
def test_a_cache_in_a_user_namespace_is_used(app: Path, tmp_path: Path) -> None:
    """There root's own directories ("/", "/tmp") show as nobody's: a sandbox's layout."""
    probe = subprocess.run([str(UNSHARE), "-r", "true"], capture_output=True, check=False)
    if probe.returncode != 0:
        pytest.skip("no user namespace here")

    assert _run(app, tmp_path / "cache", prefix=(str(UNSHARE), "-r")) == "synced"


@pytest.mark.skipif(UNSHARE is None, reason="needs unshare")
def test_in_a_user_namespace_below_tmp_nobodys_directory_is_refused(app: Path) -> None:
    """Anyone may add a directory to /tmp: below it, one shown as nobody's could be anyone's."""
    shown = next((p for p in sorted(Path("/tmp").iterdir())  # noqa: S108  the case
                  if p.is_dir() and not p.is_symlink() and p.stat().st_uid == 0), None)  # fmt: skip
    if shown is None or subprocess.run([str(UNSHARE), "-r", "true"], check=False).returncode:
        pytest.skip("no root's directory in /tmp, or no user namespace")

    out = _run(app, shown / "fsbpyr-test-cache", prefix=(str(UNSHARE), "-r"))

    assert out == f"refusing cache directory {shown}: owned by another user"
    assert not (shown / "fsbpyr-test-cache").exists()


@pytest.mark.skipif(PODMAN is None, reason="needs podman")
def test_a_symlink_of_another_user_is_refused(app: Path, tmp_path: Path) -> None:
    """One planted (in a sticky directory, say) by another user: its target is theirs to change.
    Made here by a subordinate uid of ours (podman unshare)."""
    (tmp_path / "real").mkdir()
    link = tmp_path / "theirs"
    script = 'ln -s "$1" "$2" && chown -h 1:1 "$2"'
    made = subprocess.run([str(PODMAN), "unshare", "sh", "-c", script, "sh",
                           str(tmp_path / "real"), str(link)], capture_output=True,
                          check=False)  # fmt: skip
    if made.returncode != 0:
        pytest.skip(f"cannot make a symlink of another user: {made.stderr!r}")

    refused = f"refusing cache directory {link}: owned by another user"
    assert _run(app, tmp_path, root=f"{link}/c") == refused
    assert not any((tmp_path / "real").iterdir())


def test_a_working_directory_removed_under_the_app_refuses_a_relative_root(
    app: Path, tmp_path: Path
) -> None:
    assert "No such file or directory" in _run(app, tmp_path, root="rel", gone=True)


def test_a_way_too_long_once_a_symlink_is_followed_is_refused(app: Path, tmp_path: Path) -> None:
    # a target of 999 bytes (XFS takes 1024 at most) spliced in front of the 7,200 left: more
    # than the walk's 2 * PATH_MAX + 2 bytes, which the root alone is not
    (tmp_path / "long").symlink_to("/" + "a/" * 499)
    root = f"{tmp_path}/long/" + "b/" * 3600

    assert "File name too long" in _run(app, tmp_path, root=root)
