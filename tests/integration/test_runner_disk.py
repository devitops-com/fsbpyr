"""tools/runner-disk.sh, which makes room on a GitHub runner's disk for full-suite.yml's suite and
corpus jobs: it deletes the runner image's unused software below the runner's file system,
mounted at ROOT, and then fails unless NEED GiB beyond a tenth of the disk are free.

What matters most is what it must not do: delete anything but the listed paths below ROOT, run
on the file system it is itself on (ROOT /), or let a job start with less room than it needs.
`df` is a fake that reports the figures a test chooses; where a deletion must not happen at all,
`rm` and `du` are fakes too, so a broken refusal could not delete anything real."""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "tools" / "runner-disk.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "full-suite.yml"
UNUSED = re.search(r"^UNUSED=\(\n(.*?)^\)", SCRIPT.read_text(encoding="utf-8"), re.M | re.S)
PATHS = UNUSED[1].split() if UNUSED else []
MIB_PER_GIB = 1024
SIZE = 100 * MIB_PER_GIB  # the fake file system's size in MiB: a tenth of it is 10 GiB

FAKE_DF = """\
case "$*" in
  *--output=size,avail*) printf '1M-blocks Avail\\n%s %s\\n' "$FAKE_SIZE" "$FAKE_AVAIL" ;;
  *) echo "fake file system of $*" ;;
esac
"""


def _program(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    path.chmod(0o755)


def _run(tmp_path: Path, root: str, need: str, avail: int = SIZE,
         fakes: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:  # fmt: skip
    """The script as a job runs it, with a fake df (and each of `fakes`, which only log)."""
    bin_dir = tmp_path / "bin"
    _program(bin_dir / "df", FAKE_DF)
    for name in fakes:
        _program(bin_dir / name, f'echo "{name} $*" >> "{tmp_path}/calls"\n')
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_SIZE": str(SIZE), "FAKE_AVAIL": str(avail)}
    return subprocess.run(["bash", str(SCRIPT), root, str(tmp_path), need], env=env,  # noqa: S607
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
                          timeout=60)  # fmt: skip


def _runner(tmp_path: Path) -> Path:
    """A runner's file system: every listed path, one of them a link, and what must stay."""
    runner = tmp_path / "runner"
    for index, path in enumerate(PATHS):
        target = runner / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if index == 1:
            target.symlink_to(tmp_path / "outside")  # a link removed, not followed
        else:
            (target / "sub").mkdir(parents=True)
            (target / "sub" / "file").write_text("x", encoding="utf-8")
            (target / "out").symlink_to(tmp_path / "outside")
    for kept in ("usr/bin/docker", "home/runner/work/x", f"{PATHS[0]}-other/file", "usr/x"):
        (runner / kept).parent.mkdir(parents=True, exist_ok=True)
        (runner / kept).write_text("kept", encoding="utf-8")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "file").write_text("kept", encoding="utf-8")
    return runner


def test_the_list_names_the_big_unused_software_as_relative_paths() -> None:
    assert len(PATHS) >= 10
    assert "usr/local/lib/android" in PATHS and "opt/hostedtoolcache" in PATHS
    assert all(not path.startswith("/") and ".." not in path.split("/") for path in PATHS)
    assert all(re.fullmatch(r"[a-z0-9._/-]+", path) for path in PATHS)  # no glob, no space


def test_it_removes_each_listed_path_below_root_and_nothing_else(tmp_path: Path) -> None:
    runner = _runner(tmp_path)

    result = _run(tmp_path, str(runner), "0")

    assert result.returncode == 0, result.stderr
    assert not [path for path in PATHS if os.path.lexists(runner / path)]
    for kept in ("usr/bin/docker", "home/runner/work/x", f"{PATHS[0]}-other/file", "usr/x"):
        assert (runner / kept).read_text(encoding="utf-8") == "kept"
    assert (tmp_path / "outside" / "file").read_text(encoding="utf-8") == "kept"
    assert f"{tmp_path}: {SIZE} MiB free of {SIZE} MiB; the job needs" in result.stdout


def test_a_path_it_cannot_remove_leaves_the_decision_to_the_check(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    locked = runner / PATHS[0] / "sub"
    locked.chmod(0o555)  # its file cannot be removed by a user
    try:
        result = _run(tmp_path, str(runner), "0")
    finally:
        locked.chmod(0o755)

    assert result.returncode == 0, result.stderr
    assert f"::warning::{PATHS[0]}: not all of it removed" in result.stdout
    assert not [path for path in PATHS[1:] if os.path.lexists(runner / path)]


@pytest.mark.parametrize(("need", "avail", "status"), [
    (20, 20 * MIB_PER_GIB + SIZE // 10, 0),  # exactly NEED GiB beyond a tenth
    (20, 20 * MIB_PER_GIB + SIZE // 10 - 1, 1),  # 1 MiB less
    (0, SIZE // 10, 0),
    (0, SIZE // 10 - 1, 1),  # below a tenth free, whatever the job needs
])  # fmt: skip
def test_it_fails_unless_need_gib_beyond_a_tenth_of_the_disk_are_free(
    tmp_path: Path, need: int, avail: int, status: int
) -> None:
    result = _run(tmp_path, str(_runner(tmp_path)), str(need), avail)

    assert result.returncode == status, result.stdout + result.stderr
    want = need * MIB_PER_GIB + SIZE // 10
    if status:
        assert f"::error::{tmp_path}: {avail} MiB free of {SIZE} MiB" in result.stdout
        assert f"{want} MiB. Run the job again" in result.stdout
    else:
        assert f"the job needs {want} MiB ({need} GiB beyond a tenth)" in result.stdout


@pytest.mark.parametrize("root", ["/", "link-to-root", "missing", "file"])
def test_it_refuses_this_systems_own_root_and_what_is_no_directory(
    tmp_path: Path, root: str
) -> None:
    """ROOT / would delete this system's software: refused before anything is looked at (rm
    and du are fakes here, so a refusal that broke could not delete anything)."""
    (tmp_path / "link-to-root").symlink_to("/")
    (tmp_path / "file").write_text("", encoding="utf-8")
    argument = root if root == "/" else str(tmp_path / root)

    result = _run(tmp_path, argument, "0", fakes=("rm", "du"))

    assert result.returncode == 2
    assert f"ERROR: {argument}: not the runner's file system mounted in this container" in \
        result.stderr  # fmt: skip
    assert not (tmp_path / "calls").exists()
    assert result.stdout == ""


@pytest.mark.parametrize("need", ["", "2.5", "-1", "20G", " 20"])
def test_need_is_a_whole_number_of_gib(tmp_path: Path, need: str) -> None:
    runner = _runner(tmp_path)

    result = _run(tmp_path, str(runner), need, fakes=("rm", "du"))

    assert result.returncode == 2
    assert f"ERROR: NEED is a whole number of GiB, not '{need}'" in result.stderr
    assert not (tmp_path / "calls").exists()


def test_it_needs_its_three_arguments(tmp_path: Path) -> None:
    result = subprocess.run(["bash", str(SCRIPT), str(tmp_path), str(tmp_path)],  # noqa: S607
                            capture_output=True, text=True, check=False, timeout=60)  # fmt: skip

    assert result.returncode == 2
    assert "usage: " in result.stderr and "ROOT DIR NEED" in result.stderr


def _job(name: str) -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    found = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z-]+:\n|\Z)", workflow, re.M | re.S)
    assert found, name
    return found[1]


@pytest.mark.parametrize(("name", "directory", "test_step"), [
    ("suite", "/var/tmp", "      - name: The whole suite\n"),  # noqa: S108 (the job's)
    ("corpus", "/tmp", "      - name: make test-corpus\n"),  # noqa: S108
])  # fmt: skip
def test_the_suite_and_corpus_jobs_make_room_first_and_report_what_they_took(
    name: str, directory: str, test_step: str
) -> None:
    job = _job(name)
    room = "      - name: Room on the runner's disk\n"

    assert "      volumes:\n" in job and "        - /:/runner-host\n" in job
    assert f"bash tools/runner-disk.sh /runner-host {directory} 20\n" in job
    # after the checkout it runs from, before what is big lands on the disk
    assert job.index("      - name: Trust the checkout\n") < job.index(room)
    assert job.index(room) < job.index("      - name: Install uv\n") < job.index(test_step)
    step = job.split(test_step, 1)[1].split("\n      - ", 1)[0]
    assert f"while df -B1M --output=avail {directory} | tail -n 1; do sleep 1; done" in step
    assert ">> /root/disk-free.txt 2> /dev/null < /dev/null &" in step
    assert "trap 'kill \"$sampler\"' EXIT" in step
    assert "the job took" in job.split(test_step, 1)[1]


def test_no_other_job_deletes_on_its_runner() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert workflow.count("/:/runner-host") == workflow.count("bash tools/runner-disk.sh") == 2
