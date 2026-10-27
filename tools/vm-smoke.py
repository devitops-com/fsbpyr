"""`make test-vm`: aarch64 apps run in a QEMU system VM whose kernel has 16 KiB or 64 KiB pages.

fsbpyr builds for the host it runs on and CI's aarch64 runners have 4 KiB pages, so the
page-size-dependent code (mapping and aligning the payload and the natives) is run here
instead: qemu-system-aarch64 under TCG (`-M virt -cpu max`, no KVM) boots a pinned kernel
(tools/vm-pins.toml: Raspberry Pi 5's, 16 KiB pages, or EL8's, 64 KiB) with an initramfs that
holds a pinned root file system, the apps and /init, which runs the commands and powers off.
The console is the serial port: the commands' output and exit codes are checked on the host.

    vm-smoke.py [--kernel 16k|64k]... [--userland debian|el8] [--app PATH]... [--run COMMAND]...
                [--timeout SECONDS]

Each --app (an executable built for aarch64) is copied to /apps, first on PATH. Each --run is
a shell command line run there as root in /tmp with FSBPYR_CACHE_DIR=/tmp/cache and a limit of
--timeout seconds; it must exit 0, and with ``COMMAND => TEXT`` its output must contain TEXT.
Without --run only the boot and the page size are checked. Exit status: 0 all passed, 1 a
check failed, 2 the command line, a pin or a missing tool (nothing is installed for you). The
files fetched are pinned by SHA-256 and checked on every use.
"""

import argparse
import io
import lzma
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from fsbpyr.runtime import ar, pbs

PINS = Path(__file__).with_name("vm-pins.toml")
QEMU = "qemu-system-aarch64"
BOOT_TIMEOUT_S = 300  # TCG boots in under a minute; the commands' limits come on top
COMMAND_TIMEOUT_S = 300
# Not `quiet`: a boot that hangs shows how far it got; earlycon from the first line on.
CMDLINE = "console=ttyAMA0 earlycon rdinit=/init panic=-1"
EXPECT_SEPARATOR = " => "
MARKER = "VMSMOKE"

# The guest's /init: the marker lines are what the host parses. /vm-commands holds one command
# line per line, /vm-timeout the limit of each. The kernel is as quiet as `quiet` from /init
# on, so its messages do not break into the marker lines.
INIT = f"""#!/bin/sh
export PATH=/apps:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root
export FSBPYR_CACHE_DIR=/tmp/cache
mount -t proc proc /proc
echo 4 > /proc/sys/kernel/printk
mount -t sysfs sys /sys
mount -t devtmpfs dev /dev
mount -t tmpfs tmpfs /tmp
echo "{MARKER}-KERNEL $(uname -r)"
echo "{MARKER}-PAGESIZE $(getconf PAGESIZE)"
cd /tmp
limit=$(cat /vm-timeout)
n=0
while IFS= read -r line; do
    n=$((n + 1))
    echo "{MARKER}-BEGIN $n"
    timeout "$limit" sh -c "$line" 2>&1
    echo "{MARKER}-END $n $?"
done < /vm-commands
echo "{MARKER}-DONE"
sync
echo 1 > /proc/sys/kernel/sysrq
echo o > /proc/sysrq-trigger
sleep 60
"""

_RPM_MAGIC, _HEADER_MAGIC, _LEAD = b"\xed\xab\xee\xdb", b"\x8e\xad\xe8\x01", 96
_CPIO_HEADER = 110
_CPIO_DEVICES = (("dev/console", 5, 1), ("dev/null", 1, 3))  # before /dev is mounted over


class VmSmokeError(Exception):
    """A pinned input is wrong or missing, or the command line is."""


class PinUnavailableError(VmSmokeError):
    """A pinned download failed: the mirror dropped the file or is down. The pin needs a
    re-pin (CONTRIBUTING.md); main() exits 3, which CI reports as a warning, not a failure."""


@dataclass(frozen=True)
class Result:
    """What one boot printed: the page size and, per command, its exit code and output."""

    kernel: str
    pagesize: int
    commands: tuple[tuple[int, str], ...]
    done: bool


# --- Pinned inputs ---


PIN_UNAVAILABLE = 3  # exit status: a pinned download failed (see PinUnavailableError)


def load_pins() -> dict[str, dict[str, dict[str, object]]]:
    with PINS.open("rb") as handle:
        return tomllib.load(handle)  # type: ignore[return-value]


def downloads_dir() -> Path:
    env = os.environ.get("FSBPYR_TEST_DOWNLOADS")
    if env:
        return Path(env)
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "fsbpyr-vm-smoke"


def fetch(url: str, sha256: str, name: str) -> Path:
    """The pinned file, downloaded unless the kept one matches; verified before it is returned."""
    target = downloads_dir() / name
    if not (target.is_file() and pbs.sha256_of(target) == sha256):
        print(f"vm-smoke: fetching {url}", file=sys.stderr)
        try:
            pbs.download(url, sha256, target)
        except pbs.PbsError as exc:  # a hash mismatch or a refused URL: a failure, not a gone pin
            raise VmSmokeError(f"the pinned {name} is not what the pin says: {exc}") from exc
        except OSError as exc:  # gone or unreachable
            raise PinUnavailableError(
                f"cannot fetch the pinned {name} ({exc}); re-pin it in tools/vm-pins.toml"
            ) from exc
    return target


def _rpm_header_end(data: bytes, offset: int) -> int:
    if data[offset : offset + 4] != _HEADER_MAGIC:
        raise VmSmokeError(f"no RPM header at offset {offset}")
    count, size = struct.unpack_from(">II", data, offset + 8)
    return offset + 16 + 16 * count + size


def rpm_files(data: bytes, wanted: set[str]) -> dict[str, bytes]:
    """The ``wanted`` regular files (names as the payload has them, "./usr/...") of an RPM whose
    payload is xz-compressed cpio (EL8's). The signature is not read: the pin's hash is."""
    if data[:4] != _RPM_MAGIC:
        raise VmSmokeError("not an RPM")
    end = _rpm_header_end(data, _LEAD)
    end = _rpm_header_end(data, end + (-end % 8))
    raw = lzma.LZMADecompressor().decompress(data[end:])
    found: dict[str, bytes] = {}
    offset = 0
    while raw[offset : offset + 6] == b"070701":
        head = raw[offset : offset + _CPIO_HEADER]
        size, namesize = (int(head[6 + 8 * i : 14 + 8 * i], 16) for i in (6, 11))
        name = raw[offset + _CPIO_HEADER : offset + _CPIO_HEADER + namesize - 1].decode()
        body = offset + _CPIO_HEADER + namesize
        body += -body % 4
        if name in wanted:
            found[name] = raw[body : body + size]
        offset = body + size + (-size % 4)
    return found


def deb_files(data_tar: bytes, wanted: set[str]) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data_tar), mode="r:*") as tar:
        for member in tar:
            if member.name in wanted and (handle := tar.extractfile(member)):
                found[member.name] = handle.read()
    return found


def kernel_image(pin: dict[str, object], key: str, directory: Path) -> Path:
    """The kernel of ``pin`` written into ``directory``, once its config is checked to say the
    page size the pin names."""
    package = fetch(str(pin["url"]), str(pin["sha256"]), str(pin["url"]).rpartition("/")[2])
    wanted = {str(pin["image"]), str(pin["config"])}
    if pin["kind"] == "rpm":
        files = rpm_files(package.read_bytes(), wanted)
    else:
        (payload,) = (body for name, body in ar.read(package) if name.startswith("data.tar"))
        files = deb_files(payload, wanted)
    if len(files) != len(wanted):
        raise VmSmokeError(f"kernel {key}: {package.name} lacks {sorted(wanted - set(files))}")
    option = f"{pin['option']}=y".encode()
    if option not in files[str(pin["config"])].splitlines():
        raise VmSmokeError(f"kernel {key}: its config does not say {option.decode()}")
    image = directory / f"Image-{key}"
    image.write_bytes(files[str(pin["image"])])
    return image


# --- The initramfs: a "newc" cpio archive ---


class Cpio:
    """Writes a cpio archive in "newc" format (the kernel's initramfs format) to ``out``."""

    def __init__(self, out: io.BufferedWriter) -> None:
        self._out = out
        self._inode = 0
        self._names: set[str] = set()

    def add(self, name: str, mode: int, body: bytes | io.IOBase = b"", size: int = 0,
            rdev: tuple[int, int] = (0, 0)) -> None:  # fmt: skip
        """One entry. ``body`` is bytes or a file object of ``size`` bytes; parents of ``name``
        that were not added are added as directories first (the kernel makes none)."""
        parent = name.rpartition("/")[0]
        if parent and parent not in self._names:
            self.add(parent, 0o040755)
        self._names.add(name)
        if isinstance(body, bytes):
            size = len(body)
        self._inode += 1
        nlink = 2 if mode & 0o170000 == 0o040000 else 1
        fields = (self._inode, mode, 0, 0, nlink, 0, size, 0, 0, *rdev, len(name) + 1, 0)
        encoded = name.encode() + b"\0"
        self._out.write(b"070701" + b"".join(f"{value:08X}".encode() for value in fields))
        self._out.write(encoded + b"\0" * (-(_CPIO_HEADER + len(encoded)) % 4))
        if isinstance(body, bytes):
            self._out.write(body)
        else:
            shutil.copyfileobj(body, self._out)  # type: ignore[arg-type]
        self._out.write(b"\0" * (-size % 4))

    def close(self) -> None:
        self.add("TRAILER!!!", 0)


def _clean(name: str) -> str | None:
    """A tar member's name as a cpio one: relative, without "." and ".."; None for the root."""
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise VmSmokeError(f"{name}: leaves the root file system")
    return "/".join(parts) or None


def _tar_entries(rootfs: Path) -> Iterator[tuple[str, int, bytes | io.IOBase, int]]:
    """(name, cpio mode, body, size) of the files, directories and links of a root file system
    archive; hard links become copies, devices and pipes are left out."""
    modes: dict[str, int] = {}
    with tarfile.open(rootfs, mode="r:*") as tar:
        for member in tar:
            name = _clean(member.name)
            if name is None:
                continue
            mode = member.mode
            if member.islnk():  # a hard link has its target's mode
                mode = modes.get(_clean(member.linkname) or "", mode)
            mode = modes[name] = mode & 0o7777
            if member.isdir():
                yield name, 0o040000 | mode, b"", 0
            elif member.issym():
                yield name, 0o120777, member.linkname.encode(), 0
            elif member.isreg() or member.islnk():
                handle = tar.extractfile(member)  # a hard link's, its target's content
                if handle is not None:
                    size = handle.seek(0, os.SEEK_END)
                    handle.seek(0)
                    yield name, 0o100000 | mode, handle, size


def build_initramfs(path: Path, rootfs: Path, apps: list[Path], commands: list[str],
                    timeout: int) -> None:  # fmt: skip
    """``rootfs``'s files, /apps with the ``apps``, the command list and /init in a cpio file."""
    with path.open("wb") as out:
        cpio = Cpio(out)
        for name, mode, body, size in _tar_entries(rootfs):
            cpio.add(name, mode, body, size)
        for name, major, minor in _CPIO_DEVICES:
            cpio.add(name, 0o020666, rdev=(major, minor))
        cpio.add("apps", 0o040755)
        for app in apps:
            with app.open("rb") as handle:
                cpio.add(f"apps/{app.name}", 0o100755, handle, app.stat().st_size)
        cpio.add("vm-commands", 0o100644, "".join(f"{c}\n" for c in commands).encode())
        cpio.add("vm-timeout", 0o100644, f"{timeout}\n".encode())
        cpio.add("init", 0o100755, INIT.encode())
        cpio.close()


# --- Running and checking ---


def qemu_command(kernel: Path, initramfs: Path) -> list[str]:
    """The QEMU command line; `-nic none` because the guest needs no network (and the default
    virtio NIC needs an option ROM that some distributions package separately)."""
    return [QEMU, "-M", "virt", "-cpu", "max", "-m", "2G", "-smp", "2", "-nographic",
            "-nic", "none", "-no-reboot", "-kernel", str(kernel), "-initrd", str(initramfs),
            "-append", CMDLINE]  # fmt: skip


def run_qemu(kernel: Path, initramfs: Path, log: Path) -> str:
    """The console of one boot: QEMU's output, which is also kept in ``log``.

    QEMU's own stderr follows the console after a marker line, so a QEMU that cannot start says why.
    """
    try:
        done = subprocess.run(  # noqa: S603  QEMU found on PATH, an argument list
            qemu_command(kernel, initramfs),
            capture_output=True,
            timeout=BOOT_TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        output, errors, tail = done.stdout, done.stderr, ""
    except subprocess.TimeoutExpired as exc:
        output, errors = exc.stdout or b"", exc.stderr or b""
        tail = f"\n{QEMU} killed after {BOOT_TIMEOUT_S} s\n"
    console = output.decode(errors="replace").replace("\r\n", "\n") + tail
    if errors.strip():
        console += f"\n--- {QEMU} stderr:\n{errors.decode(errors='replace')}"
    log.write_text(console)
    return console


def parse_console(console: str, kernel: str) -> Result:
    """The markers /init printed; a command's output is what lies between its BEGIN and END."""
    pagesize = re.search(rf"^{MARKER}-PAGESIZE (\d+)$", console, re.MULTILINE)
    commands: list[tuple[int, str]] = []
    pattern = rf"^{MARKER}-BEGIN (\d+)\n(.*?)^{MARKER}-END \1 (\d+)$"
    for match in re.finditer(pattern, console, re.MULTILINE | re.DOTALL):
        commands.append((int(match.group(3)), match.group(2)))
    done = re.search(rf"^{MARKER}-DONE$", console, re.MULTILINE) is not None
    return Result(kernel, int(pagesize.group(1)) if pagesize else 0, tuple(commands), done)


def split_expectation(spec: str) -> tuple[str, str | None]:
    command, separator, expected = spec.partition(EXPECT_SEPARATOR)
    return command, expected if separator else None


def check(result: Result, pagesize: int, specs: list[str]) -> list[str]:
    """The failures of one boot, empty when it passed."""
    problems = []
    if result.pagesize != pagesize:
        problems.append(f"page size {result.pagesize}, expected {pagesize}")
    if len(result.commands) != len(specs):
        problems.append(f"{len(result.commands)} of {len(specs)} commands finished")
    for spec, (code, output) in zip(specs, result.commands, strict=False):
        command, expected = split_expectation(spec)
        if code != 0:
            problems.append(f"`{command}` exited {code}")
        elif expected is not None and expected not in output:
            problems.append(f"`{command}` printed no {expected!r}")
    if not result.done:
        problems.append("the guest did not reach the end of /init")
    return problems


def report(result: Result, specs: list[str], problems: list[str]) -> None:
    print(f"== kernel {result.kernel}: page size {result.pagesize}")
    for spec, (code, output) in zip(specs, result.commands, strict=False):
        print(f"$ {split_expectation(spec)[0]}  (exit {code})")
        print("".join(f"    {line}\n" for line in output.splitlines()), end="")
    for problem in problems:
        print(f"FAILED: {problem}")


def smoke(kernel: str, args: argparse.Namespace, work: Path) -> bool:
    pins = load_pins()
    pin, userland = pins["kernel"][kernel], pins["userland"][args.userland]
    image = kernel_image(pin, kernel, work)
    rootfs = fetch(str(userland["url"]), str(userland["sha256"]), str(userland["file"]))
    specs: list[str] = args.run
    initramfs = work / f"initramfs-{kernel}.cpio"
    build_initramfs(initramfs, rootfs, args.app, [split_expectation(s)[0] for s in specs],
                    args.timeout)  # fmt: skip
    log = work / f"console-{kernel}.log"
    console = run_qemu(image, initramfs, log)
    result = parse_console(console, kernel)
    problems = check(result, int(str(pin["pagesize"])), specs)
    report(result, specs, problems)
    if not (result.done and result.pagesize):  # no boot to speak of: the console says why
        print(f"--- console of the boot (last lines):\n{console[-3000:]}")
    return not problems


def parse_args(argv: list[str]) -> argparse.Namespace:
    pins = load_pins()
    parser = argparse.ArgumentParser(prog="vm-smoke.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--kernel", action="append", choices=sorted(pins["kernel"]),
                        help="page size to boot (repeatable; default: all)")  # fmt: skip
    parser.add_argument("--userland", choices=sorted(pins["userland"]), default="debian")
    parser.add_argument("--app", action="append", default=[], type=Path, metavar="PATH",
                        help="an aarch64 executable to copy to /apps (repeatable)")  # fmt: skip
    parser.add_argument("--run", action="append", default=[], metavar="COMMAND",
                        help="a command line to run in the guest; 'CMD => TEXT' also needs TEXT"
                        " in its output (repeatable)")  # fmt: skip
    parser.add_argument("--timeout", type=int, default=COMMAND_TIMEOUT_S, metavar="SECONDS",
                        help="limit of each command in the guest")  # fmt: skip
    args = parser.parse_args(argv)
    args.kernel = args.kernel or sorted(pins["kernel"])
    names = [app.name for app in args.app]
    if len(set(names)) != len(names):
        parser.error("two --app files have the same name")
    for app in args.app:
        if not app.is_file():
            parser.error(f"{app}: no such file")
    if any("\n" in spec for spec in args.run):
        parser.error("a --run command is one line")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if shutil.which(QEMU) is None:
        print(f"vm-smoke: {QEMU} is not on PATH (Debian: apt install qemu-system-arm); "
              "nothing is installed for you", file=sys.stderr)  # fmt: skip
        return 2
    try:
        with tempfile.TemporaryDirectory(prefix="fsbpyr-vm-smoke.") as tmp:
            passed = [smoke(kernel, args, Path(tmp)) for kernel in args.kernel]
    except PinUnavailableError as exc:
        print(f"vm-smoke: {exc}", file=sys.stderr)
        return PIN_UNAVAILABLE
    except (VmSmokeError, pbs.PbsError, ar.ArError, OSError) as exc:
        print(f"vm-smoke: {exc}", file=sys.stderr)
        return 2
    print(f"vm-smoke: {sum(passed)} of {len(passed)} boots passed")
    return 0 if all(passed) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
