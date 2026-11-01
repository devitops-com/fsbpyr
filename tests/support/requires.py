"""What the tests need from the machine: nothing in this suite skips. A test whose tool is
missing or whose environment is not as it needs fails with one line that names what to install or
change, and points at CONTRIBUTING.md. ``python -m tests.support.requires`` (`make test-prereqs`)
lists everything the full suite lacks at once.

A tool is asked for with the ``tools`` marker (a module's ``pytestmark``, or a test's decorator;
tests/conftest.py checks it before the test) or, where only a fixture or one branch needs it,
with ``tool`` at the point of use. An environment condition is a function below, called by the
test that needs it."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import NoReturn

import pytest

FIX = "see CONTRIBUTING.md, Running the tests"
MARKER = "requires_tools"
TMP_ROOT_OWNED = Path("/tmp/fsbpyr-test-root-owned")  # noqa: S108  tools/test-setup.sh makes it
FOREIGN_PARENTS = ("/var/lib", "/etc", "/var/spool", "/home")


@dataclass(frozen=True)
class Tool:
    commands: tuple[str, ...]  # candidates, the first one found wins
    package: str  # the Debian packages (space-separated), none where there is no package
    used_for: str
    install: str = ""  # how to get it where there is no package


TOOLS = {
    "uv": Tool(("uv",), "", "runs the tests and builds their fixture projects",
               "the uv that tools/release/uv.version names; it is not packaged"),
    "git": Tool(("git",), "git", "builds record the commit; tests read projects from repositories"),
    "make": Tool(("make",), "make", "a test runs the Makefile"),
    "cc": Tool(("cc", "gcc"), "gcc", "compiles the tests' C programs"),
    "clang": Tool(("clang",), "", "links the runtime, the launcher and its test programs",
                  "make puts fsbpyr's LLVM 23 first on PATH; run directly, put it there "
                  "(bash tools/bootstrap.sh --llvm23)"),
    "llvm-objdump": Tool(("llvm-objdump-22", "llvm-objdump"), "llvm-22",
                         "reads the executables' sections and symbols"),
    "readelf": Tool(("readelf",), "binutils", "reads the stub's ELF headers"),
    "objdump": Tool(("objdump",), "binutils", "disassembles the stub"),
    "unshare": Tool(("unshare",), "util-linux", "runs apps in namespaces and as PID 1"),
    "prlimit": Tool(("prlimit",), "util-linux", "caps the tasks of contained runs"),
    "podman": Tool(("podman",), "podman uidmap passt",
                   "runs the portability images and maps a second range of user ids"),
    "strace": Tool(("strace",), "strace", "traces the system calls of a start"),
    "groff": Tool(("groff",), "groff-base", "renders the man page"),
    "zstd": Tool(("zstd",), "zstd", "unpacks the LLVM tarball that bootstrap.sh fetches"),
    "tar": Tool(("tar",), "tar", "packs the LLVM tarball that bootstrap.sh's tests make"),
    "xz": Tool(("xz",), "xz-utils", "install.sh decompresses the release asset with it"),
    "curl": Tool(("curl",), "curl", "install.sh's tests point it at a local HTTPS server"),
    "wget": Tool(("wget",), "wget", "install.sh's tests fetch with it when curl is absent"),
    "busybox": Tool(("busybox",), "busybox", "install.sh's tests run it under busybox sh"),
    "dash": Tool(("dash",), "dash", "install.sh's tests run it under dash"),
    "bash": Tool(("bash",), "bash", "the completion and install.sh tests run it"),
    "openssl": Tool(("openssl",), "openssl", "install.sh's tests make their TLS certificate; "
                    "release-signing.sh's tests need its ML-DSA-65 (OpenSSL 3.5 or newer)"),
    "jq": Tool(("jq",), "jq", "previous-release.sh reads GitHub's release list with it"),
    "ls": Tool(("ls",), "coreutils", "a host binary whose glibc requirement a test reads"),
}


def _how_to_get(entry: Tool) -> str:
    if not entry.package:
        return entry.install
    return f"Debian package{'s' if ' ' in entry.package else ''}: {entry.package}"


def tool(name: str, why: str | None = None) -> str:
    """The path of the first of ``TOOLS[name]``'s commands on PATH; fails the test if none is."""
    entry = TOOLS[name]
    for command in entry.commands:
        if found := shutil.which(command):
            return found
    pytest.fail(f"missing tool: {name} ({_how_to_get(entry)}): {why or entry.used_for}; {FIX}",
                pytrace=False)


def tools(*names: str) -> pytest.MarkDecorator:
    """Marks a test or module as needing these tools; tests/conftest.py fails it without them."""
    return pytest.mark.requires_tools(*names)


def unmet(condition: str, change: str) -> NoReturn:
    """Fails the test: the environment is not as it needs."""
    pytest.fail(f"unmet environment: {condition}: {change}; {FIX}", pytrace=False)


def _run(*command: str) -> bool:
    return subprocess.run(command, capture_output=True, check=False).returncode == 0


def non_root(why: str) -> None:
    if os.geteuid() == 0:
        unmet(f"running as root ({why})", "run the tests as a normal user")


def user_namespaces() -> None:
    if not _run(tool("unshare"), "-r", "true"):
        unmet("no user namespace for unprivileged users",
              "allow them: sysctl kernel.unprivileged_userns_clone=1, and "
              "kernel.apparmor_restrict_unprivileged_userns=0 on Ubuntu")  # fmt: skip


def rootless_podman() -> None:
    if not _run(tool("podman"), "unshare", "true"):
        unmet("podman cannot map a second range of user ids",
              "give your user ranges in /etc/subuid and /etc/subgid (podman's rootless tutorial)")


def supplementary_group() -> int:
    """A group of ours other than the effective one, which a file can be given."""
    others = [gid for gid in os.getgroups() if gid != os.getegid()]
    if not others:
        unmet("the user has no supplementary group",
              "run as a user with one: tools/test-setup.sh makes the group testers")
    return others[0]


def set_id_bits(path: Path) -> None:
    """Fails unless a set-id file in ``path``'s file system takes effect in this process."""
    if os.statvfs(path).f_flag & os.ST_NOSUID or "NoNewPrivs:\t1" in Path(
        "/proc/self/status"
    ).read_text():
        unmet("set-id bits do not apply here (a nosuid mount, or no_new_privs)",
              "point TMPDIR (make: TESTTMP) at a file system that allows them, "
              "and do not run with no_new_privs")  # fmt: skip


LONG_TARGET = "/" + "a/" * 499  # 999 bytes: test_cache_root's, as long as XFS (1024) allows


def long_symlink_targets(path: Path) -> None:
    """Fails unless ``path``'s file system takes a symlink target of 999 bytes, as test_cache_root
    makes (XFS takes 1024 at most; ext4 and a tmpfs take more)."""
    link = path / f".fsbpyr-long-target-{os.getpid()}"
    try:
        link.symlink_to(LONG_TARGET)
    except OSError as exc:
        unmet(f"{path} takes no 999-byte symlink target ({exc.strerror})",
              "point TMPDIR (make: TESTTMP) at a file system that does, such as ext4 or XFS")
    finally:
        link.unlink(missing_ok=True)


def foreign_directory() -> Path:
    """A directory of a user that is neither root nor us, which we may search."""
    for parent in map(Path, FOREIGN_PARENTS):
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
    return unmet("no searchable directory of another user besides root",
                 "make one, e.g. /var/lib/fsbpyr-other (tools/test-setup.sh does)")


def root_directory_in_tmp() -> Path:
    """A directory in /tmp that root owns."""
    tmp = Path("/tmp")  # noqa: S108  the case
    for path in sorted(tmp.iterdir()):
        if path.is_dir() and not path.is_symlink() and path.stat().st_uid == 0:
            return path
    return unmet("no directory owned by root in /tmp",
                 f"sudo mkdir -m 755 {TMP_ROOT_OWNED} (tools/test-setup.sh does)")


def uv_python(version: str) -> None:
    """Fails unless uv has Python ``version`` (X.Y) installed; the tests never let it download."""
    if not _run(tool("uv"), "python", "find", "--no-python-downloads", version):
        unmet(f"Python {version} is not installed with uv", f"uv python install {version}")


def openssl_with_ml_dsa() -> None:
    """Fails unless the openssl command signs with ML-DSA-65, which takes OpenSSL 3.5 or newer:
    a tool's presence does not say so."""
    listed = subprocess.run([tool("openssl"), "list", "-signature-algorithms"],
                            capture_output=True, text=True, check=False)  # fmt: skip
    if "ML-DSA-65" not in listed.stdout:
        unmet("the openssl command has no ML-DSA-65",
              "install OpenSSL 3.5 or newer (Debian 13 has 3.5.7), or run make test-container")


def openssl_headers() -> None:
    """Fails without OpenSSL 3.5's headers, which sig.c's declarations are compared with."""
    if not Path("/usr/include/openssl/evp.h").is_file():
        unmet("OpenSSL's headers are not installed",
              "install libssl-dev 3.5 or newer (Debian 13 has 3.5.7), or run make test-container")


def glibc_host() -> None:
    from fsbpyr.targets import host_target  # noqa: PLC0415

    if host_target().libc != "glibc":
        unmet("the host's libc is not glibc", "run the tests on a glibc distribution")


def enough_free_disk(path: Path) -> None:
    """Fails with under 10% free on ``path``'s file system, where a collection is due hourly."""
    from fsbpyr.boot._fsbpyr_rt import gc  # noqa: PLC0415

    if gc.short_of_space(str(path)):
        unmet("under 10% free disk where the tests run",
              "free space on the file system of TMPDIR (make: TESTTMP)")


def _missing() -> list[str]:
    """The tools not on PATH; clang is judged by its version (``_clang_problem``)."""
    return [name for name, entry in TOOLS.items()
            if name != "clang" and not any(map(shutil.which, entry.commands))]  # fmt: skip


def _unmet_conditions() -> list[str]:
    """Each environment condition the full suite needs that does not hold, as a line."""
    tmp = Path(os.environ.get("TMPDIR", "/tmp"))  # noqa: S108
    checks: list[Callable[[], object]] = [
        partial(non_root, "the ownership tests need a user"), user_namespaces, rootless_podman,
        supplementary_group, foreign_directory, root_directory_in_tmp, glibc_host,
        partial(set_id_bits, tmp), partial(long_symlink_targets, tmp),
        partial(enough_free_disk, tmp), openssl_with_ml_dsa, openssl_headers,
        *(partial(uv_python, version) for version in ("3.12", "3.13", "3.14", "3.15")),
    ]
    found = []
    for check in checks:
        try:
            check()
        except (pytest.fail.Exception, OSError) as error:
            if not str(error).startswith("missing tool:"):  # listed with the tools
                found.append(str(error).removesuffix(f"; {FIX}"))
    return found


def _clang_problem() -> str | None:
    from fsbpyr.runtime.toolchain import ToolchainError, find_toolchain  # noqa: PLC0415

    try:
        find_toolchain(23)
    except ToolchainError as exc:
        return f"{exc}; make puts fsbpyr's LLVM 23 first on PATH"
    return None


def main() -> int:
    """Prints what the full suite lacks: the tools (the Debian ones as one apt-get line) and the
    environment conditions. 1 if anything, else 0."""
    missing = _missing()
    packages = sorted({p for name in missing for p in TOOLS[name].package.split()})
    others = [f"{name}: {TOOLS[name].install}" for name in missing if not TOOLS[name].package]
    if clang := _clang_problem():
        others.append(f"clang/lld 23: {clang}")
    lines = []
    if packages:
        lines.append("missing tools, Debian packages: apt-get install " + " ".join(packages))
    lines += [f"missing tool: {line}" for line in others]
    lines += _unmet_conditions()
    sys.stdout.write("".join(f"{line}\n" for line in lines))
    if lines:
        sys.stdout.write(f"{FIX}\n")
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
