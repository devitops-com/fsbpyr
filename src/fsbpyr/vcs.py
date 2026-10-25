"""The git commit and release tag a project is built from, and fsbpyr's version string.

Releases are tagged ``vX.Y.Z`` (D28). A build records HEAD's commit and the highest release tag
pointing at it in build_info (``git``); ``fsbpyr --version`` prints ``0.1.0-6457a95b`` for a
build of a ``v0.1.0`` tag and ``untagged-6457a95b`` otherwise. The executable has no git, so it
prints what its build recorded; a source checkout asks git.
"""

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

_RELEASE = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_HASH_DIGITS = 8
_TIMEOUT = 10
# Inherited from a git hook or script, these would point git at another repository.
_LOCATION = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                       "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                       "GIT_NAMESPACE", "GIT_PREFIX"})  # fmt: skip


def _git(git: str, directory: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    env = {k: v for k, v in os.environ.items() if k not in _LOCATION} | {"LC_ALL": "C"}
    try:
        return subprocess.run([git, "-C", str(directory), *args], capture_output=True,
                              text=True, env=env, timeout=_TIMEOUT, check=False)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None


def _release_tag(names: str) -> str | None:
    releases = [name for name in names.split() if _RELEASE.fullmatch(name)]
    return max(releases, key=lambda name: tuple(map(int, name[1:].split("."))), default=None)


def _head(git: str, directory: Path, warn: Callable[[str], None] = lambda _: None) -> str | None:
    """HEAD's commit for a directory in HEAD's tree; None outside a repository, and for a
    directory the commit does not contain."""
    head = _git(git, directory, "rev-parse", "--verify", "--quiet", "HEAD")
    if head is None or head.returncode != 0:
        if head is not None and head.stderr and "not a git repository" not in head.stderr:
            warn(f"git: {head.stderr.strip().splitlines()[0]}; no commit recorded")
        return None
    tree = _git(git, directory, "ls-tree", "--name-only", "HEAD")  # never runs fsmonitor
    if tree is None or tree.returncode != 0 or not tree.stdout.strip():
        return None
    return head.stdout.strip()


def git_state(
    directory: Path, *, warn: Callable[[str], None] = lambda _: None
) -> dict[str, str | None] | None:
    """HEAD's commit and the highest ``vX.Y.Z`` tag on it, for a directory in HEAD's tree;
    None without git, outside a repository, or for a directory the commit does not contain
    (a project copied into some other repository, fsbpyr installed in a venv there)."""
    git = shutil.which("git")
    if git is None:
        return None
    commit = _head(git, directory, warn)
    if commit is None:
        return None
    tags = _git(git, directory, "tag", "--points-at", "HEAD", "--list", "v[0-9]*")
    names = tags.stdout if tags is not None and tags.returncode == 0 else ""
    return {"commit": commit, "tag": _release_tag(names)}


def commit_time(directory: Path) -> int | None:
    """HEAD's commit time (seconds since the epoch) under the same conditions as
    ``git_state``; None otherwise."""
    git = shutil.which("git")
    if git is None or _head(git, directory) is None:
        return None
    shown = _git(git, directory, "show", "--no-patch", "--format=%ct", "HEAD")
    if shown is None or shown.returncode != 0:
        return None
    text = shown.stdout.strip()
    return int(text) if text.isdecimal() else None


def work_tree_files(directory: Path) -> list[str] | None:
    """The files below ``directory``, relative to it, that a git work tree holds: tracked, and
    untracked but not ignored (what a user expects ``uv run main.py`` to see). None without
    git or outside a work tree."""
    git = shutil.which("git")
    if git is None:
        return None
    listed = _git(git, directory, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if listed is None or listed.returncode != 0:
        return None
    return sorted(set(filter(None, listed.stdout.split("\0"))))


def version_string(state: Mapping[str, object] | None) -> str:
    commit = state.get("commit") if state else None
    if not isinstance(commit, str):
        return "untagged-unknown"  # built without git, from an sdist, or installed as a wheel
    tag = state.get("tag") if state else None
    release = tag.removeprefix("v") if isinstance(tag, str) else "untagged"
    return f"{release}-{commit[:_HASH_DIGITS]}"


def own_version() -> str:
    """fsbpyr's version: what the build of this executable recorded, else the checkout's."""
    try:
        import _fsbpyr  # noqa: PLC0415  # only in executables
    except ImportError:
        return version_string(git_state(Path(__file__).parent))
    import json  # noqa: PLC0415

    return version_string(json.loads(_fsbpyr.info()["build_info"]).get("git"))


def release_asset(python: str, target: str) -> str:
    """The release asset (without ``.xz``) of the fsbpyr that runs on ``python`` ("3.12") on
    ``target`` ("x86_64-linux-gnu", a targets.Target.name): ``fsbpyr-3.12-x86_64-linux-gnu``.
    Every asset names its Python, the newest's too (owner, 2026-10-22)."""
    return f"fsbpyr-{python}-{target}"


def release_stub_asset(python: str, target: str, glibc: str) -> str:
    """The release asset (without ``.xz``) of the stub linked for the glibc baseline ``glibc``
    ("2.28", EL8) that goes with the fsbpyr of ``python`` on ``target``: just the launcher,
    ``fsbpyr-stub-3.12-x86_64-linux-gnu-glibc2.28``. A release may carry none (D46)."""
    return f"fsbpyr-stub-{python}-{target}-glibc{glibc}"


def release_stub_sums(glibc: str) -> str:
    """The release asset listing the SHA-256 of each stub of the baseline ``glibc`` as
    ``sha256sum`` does, over the stubs themselves (the names without ``.xz``)."""
    return f"SHA256SUMS-glibc{glibc}"


def release_asset_record(project: str, python: str, target: str) -> tuple[tuple[str, str], ...]:
    """build_info's ``release_asset``, for fsbpyr's own executable only: the asset it is
    released as, which ``fsbpyr self update`` fetches the same variant of."""
    if project.lower() != "fsbpyr":
        return ()
    return (("release_asset", release_asset(python, target)),)
