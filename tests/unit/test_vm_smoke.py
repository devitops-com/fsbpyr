"""tools/vm-smoke.py: the pin table's shape (no network), the cpio archive and the RPM reader,
the console parsing and the checks. The VM itself runs in `make test-vm` and CI's vm-smoke job."""

import importlib.util
import io
import lzma
import re
import struct
import subprocess
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

TOOL = Path(__file__).parents[2] / "tools" / "vm-smoke.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vm_smoke", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vm = _load()


def _cpio(entries: list[tuple[str, int, bytes]]) -> bytes:
    out = io.BytesIO()
    archive = vm.Cpio(out)
    for name, mode, body in entries:
        archive.add(name, mode, body)
    archive.close()
    return out.getvalue()


def _parse_cpio(raw: bytes) -> dict[str, tuple[int, bytes]]:
    """{name: (mode, body)}, checking the alignment the kernel needs."""
    found: dict[str, tuple[int, bytes]] = {}
    offset = 0
    while True:
        assert raw[offset : offset + 6] == b"070701" and offset % 4 == 0
        fields = [int(raw[offset + 6 + 8 * i : offset + 14 + 8 * i], 16) for i in range(13)]
        mode, size, namesize = fields[1], fields[6], fields[11]
        name = raw[offset + 110 : offset + 110 + namesize - 1].decode()
        body = offset + 110 + namesize
        body += -body % 4
        if name == "TRAILER!!!":
            return found
        found[name] = (mode, raw[body : body + size])
        offset = body + size + (-size % 4)


def _rpm(payload: bytes) -> bytes:
    empty_header = b"\x8e\xad\xe8\x01" + bytes(4) + struct.pack(">II", 0, 0)
    return b"\xed\xab\xee\xdb" + bytes(92) + empty_header + empty_header + lzma.compress(payload)


# --- The pin table ---


def test_every_pin_has_an_https_url_and_a_sha256() -> None:
    pins = vm.load_pins()

    assert set(pins) == {"kernel", "userland"}
    for table in pins.values():
        for pin in table.values():
            assert pin["url"].startswith("https://")
            assert re.fullmatch(r"[0-9a-f]{64}", pin["sha256"])


def test_the_kernels_name_a_page_size_and_the_config_option_that_says_it() -> None:
    kernels = vm.load_pins()["kernel"]

    assert {key: kernel["pagesize"] for key, kernel in kernels.items()} == {
        "16k": 16384, "64k": 65536}  # fmt: skip
    for key, kernel in kernels.items():
        assert kernel["option"] == f"CONFIG_ARM64_{key.upper()}_PAGES"
        assert kernel["kind"] in ("deb", "rpm")
        assert kernel["image"].startswith("./") and kernel["config"].startswith("./")
        assert kernel["url"].endswith(".deb" if kernel["kind"] == "deb" else ".rpm")


def test_the_userlands_are_debian_and_el8_with_distinct_file_names() -> None:
    userlands = vm.load_pins()["userland"]

    assert set(userlands) == {"debian", "el8"}
    assert len({u["file"] for u in userlands.values()}) == 2


# --- The initramfs ---


def test_cpio_entries_are_aligned_and_parents_come_first() -> None:
    raw = _cpio([("usr/bin/tool", 0o100755, b"abcde"), ("link", 0o120777, b"usr/bin/tool")])

    assert list(_parse_cpio(raw)) == ["usr", "usr/bin", "usr/bin/tool", "link"]
    assert _parse_cpio(raw)["usr/bin/tool"] == (0o100755, b"abcde")
    assert _parse_cpio(raw)["link"] == (0o120777, b"usr/bin/tool")


def test_cpio_copies_a_file_object_of_the_given_size() -> None:
    out = io.BytesIO()
    archive = vm.Cpio(out)
    archive.add("f", 0o100644, io.BytesIO(b"123456789"), 9)
    archive.close()

    assert _parse_cpio(out.getvalue())["f"] == (0o100644, b"123456789")


def test_a_member_that_leaves_the_root_file_system_is_refused() -> None:
    assert vm._clean("./usr/bin/") == "usr/bin"
    assert vm._clean("./") is None
    with pytest.raises(vm.VmSmokeError, match="leaves the root"):
        vm._clean("../etc/passwd")


def test_the_initramfs_holds_the_rootfs_the_apps_the_commands_and_init(tmp_path: Path) -> None:
    rootfs = tmp_path / "rootfs.tar.gz"
    with tarfile.open(rootfs, "w:gz") as tar:
        info = tarfile.TarInfo("./bin/sh")
        info.size, info.mode = 3, 0o755
        tar.addfile(info, io.BytesIO(b"sh!"))
        link = tarfile.TarInfo("./bin/dash")
        link.type, link.linkname = tarfile.LNKTYPE, "./bin/sh"
        tar.addfile(link)
    app = tmp_path / "app"
    app.write_bytes(b"ELF")
    target = tmp_path / "initramfs.cpio"

    vm.build_initramfs(target, rootfs, [app], ["app --version"], 77)

    files = _parse_cpio(target.read_bytes())
    assert files["bin/sh"] == (0o100755, b"sh!") and files["bin/dash"] == (0o100755, b"sh!")
    assert files["apps/app"] == (0o100755, b"ELF")
    assert files["vm-commands"][1] == b"app --version\n" and files["vm-timeout"][1] == b"77\n"
    assert files["init"][1].startswith(b"#!/bin/sh\n") and files["init"][0] == 0o100755
    assert files["dev/console"][0] == 0o020666


# --- The kernel packages ---


def test_rpm_files_reads_the_named_files_of_an_xz_cpio_payload() -> None:
    payload = _cpio([("lib/modules/k/vmlinuz", 0o100755, b"IMAGE"), ("other", 0o100644, b"x")])

    found = vm.rpm_files(_rpm(payload), {"lib/modules/k/vmlinuz"})

    assert found == {"lib/modules/k/vmlinuz": b"IMAGE"}


def test_a_file_that_is_not_an_rpm_is_refused() -> None:
    with pytest.raises(vm.VmSmokeError, match="not an RPM"):
        vm.rpm_files(b"not an rpm", set())


def _kernel_pin(**changes: str) -> dict[str, object]:
    return {"kind": "rpm", "url": "https://example.test/k.rpm", "sha256": "0" * 64,
            "image": "vmlinuz", "config": "config", "option": "CONFIG_ARM64_64K_PAGES",
            "pagesize": 65536} | changes  # fmt: skip


def _fake_kernel_rpm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: bytes) -> None:
    package = tmp_path / "k.rpm"
    files = [("vmlinuz", 0o100755, b"IMAGE"), ("config", 0o100644, config)]
    package.write_bytes(_rpm(_cpio(files)))
    monkeypatch.setattr(vm, "fetch", lambda *_: package)


def test_the_kernel_image_is_written_when_its_config_says_the_page_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kernel_rpm(tmp_path, monkeypatch, b"# x\nCONFIG_ARM64_64K_PAGES=y\n")

    image = vm.kernel_image(_kernel_pin(), "64k", tmp_path)

    assert image.read_bytes() == b"IMAGE"


def test_a_kernel_whose_config_lacks_the_option_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kernel_rpm(tmp_path, monkeypatch, b"# CONFIG_ARM64_64K_PAGES is not set\n")

    with pytest.raises(vm.VmSmokeError, match="does not say CONFIG_ARM64_64K_PAGES=y"):
        vm.kernel_image(_kernel_pin(), "64k", tmp_path)


def test_a_kernel_package_without_the_image_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kernel_rpm(tmp_path, monkeypatch, b"CONFIG_ARM64_64K_PAGES=y\n")

    with pytest.raises(vm.VmSmokeError, match="lacks"):
        vm.kernel_image(_kernel_pin(image="missing"), "64k", tmp_path)


def test_a_download_that_does_not_match_its_pin_is_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FSBPYR_TEST_DOWNLOADS", str(tmp_path))
    (tmp_path / "k.bin").write_bytes(b"damaged")
    fetched: list[str] = []
    monkeypatch.setattr(vm.pbs, "download", lambda url, _sha, _target: fetched.append(url))

    vm.fetch("https://example.test/k.bin", "0" * 64, "k.bin")

    assert fetched == ["https://example.test/k.bin"]


def test_a_pinned_download_that_fails_exits_with_the_pin_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FSBPYR_TEST_DOWNLOADS", str(tmp_path))
    monkeypatch.setattr(vm.shutil, "which", lambda _name: "/usr/bin/qemu")

    def gone(_url: str, _sha: str, _target: Path) -> None:
        raise OSError("HTTP Error 404")

    monkeypatch.setattr(vm.pbs, "download", gone)

    assert vm.main(["--kernel", "16k"]) == vm.PIN_UNAVAILABLE
    assert "re-pin it in tools/vm-pins.toml" in capsys.readouterr().err


def test_a_pinned_download_with_the_wrong_hash_is_a_failure_not_an_unavailable_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FSBPYR_TEST_DOWNLOADS", str(tmp_path))
    monkeypatch.setattr(vm.shutil, "which", lambda _name: "/usr/bin/qemu")

    def tampered(url: str, _sha: str, _target: Path) -> None:
        raise vm.pbs.PbsError(f"SHA-256 mismatch for {url}")

    monkeypatch.setattr(vm.pbs, "download", tampered)

    assert vm.main(["--kernel", "16k"]) == 2
    assert "SHA-256 mismatch" in capsys.readouterr().err


# --- The console ---

CONSOLE = """[    0.1] kernel noise
VMSMOKE-KERNEL 6.18.50
VMSMOKE-PAGESIZE 16384
VMSMOKE-BEGIN 1
hello ['x']
VMSMOKE-END 1 0
VMSMOKE-BEGIN 2
VMSMOKE-END 2 3
VMSMOKE-DONE
"""


def test_the_console_gives_the_page_size_and_each_commands_code_and_output() -> None:
    result = vm.parse_console(CONSOLE, "16k")

    assert (result.pagesize, result.commands, result.done) == (
        16384, ((0, "hello ['x']\n"), (3, "")), True)  # fmt: skip


def test_a_console_without_markers_is_a_boot_that_failed() -> None:
    result = vm.parse_console("Kernel panic - not syncing\n", "64k")

    assert (result.pagesize, result.commands, result.done) == (0, (), False)


def test_an_expectation_follows_the_arrow() -> None:
    assert vm.split_expectation("app --version => 1.0") == ("app --version", "1.0")
    assert vm.split_expectation("app") == ("app", None)


def test_a_boot_passes_when_the_page_size_codes_and_expected_text_match() -> None:
    result = vm.parse_console(CONSOLE.replace("END 2 3", "END 2 0"), "16k")

    assert vm.check(result, 16384, ["app x => hello", "true"]) == []


@pytest.mark.parametrize(
    ("pagesize", "specs", "problem"),
    [
        (65536, ["a", "b"], "page size 16384, expected 65536"),
        (16384, ["a", "b"], "`b` exited 3"),
        (16384, ["a => nope", "b"], "`a` printed no 'nope'"),
        (16384, ["a"], "2 of 1 commands finished"),
    ],
)
def test_a_boot_fails_on_each_wrong_page_size_code_text_and_count(
    pagesize: int, specs: list[str], problem: str
) -> None:
    result = vm.parse_console(CONSOLE, "16k")

    assert problem in vm.check(result, pagesize, specs)


def test_a_guest_that_never_finished_init_fails() -> None:
    result = vm.parse_console(CONSOLE.replace("VMSMOKE-DONE\n", ""), "16k")

    assert "the guest did not reach the end of /init" in vm.check(result, 16384, ["a", "b"])


def test_the_qemu_command_has_no_network_card() -> None:
    command = vm.qemu_command(Path("kernel"), Path("initramfs"))

    assert command[command.index("-nic") + 1] == "none"


def test_the_boot_is_verbose_from_the_first_line_so_a_hung_boot_shows_where() -> None:
    cmdline = vm.qemu_command(Path("kernel"), Path("initramfs"))[-1].split()

    assert "earlycon" in cmdline
    assert "quiet" not in cmdline


def test_init_quietens_the_kernel_before_the_first_marker() -> None:
    lines = vm.INIT.splitlines()
    quieten = lines.index("echo 4 > /proc/sys/kernel/printk")

    assert lines.index("mount -t proc proc /proc") < quieten
    assert quieten < next(i for i, line in enumerate(lines) if vm.MARKER in line)


def test_a_qemu_that_exits_at_once_says_why_in_the_console_and_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = b"qemu-system-aarch64: failed to find romfile \"efi-virtio.rom\"\n"
    failed = subprocess.CompletedProcess([], 1, stdout=b"", stderr=message)
    monkeypatch.setattr(vm.subprocess, "run", lambda *_a, **_k: failed)
    log = tmp_path / "console.log"

    console = vm.run_qemu(Path("kernel"), Path("initramfs"), log)

    assert "failed to find romfile" in console
    assert log.read_text() == console
    assert not (vm.parse_console(console, "16k").done)


def test_a_successful_boot_with_no_stderr_leaves_the_console_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    booted = subprocess.CompletedProcess([], 0, stdout=CONSOLE.encode(), stderr=b"")
    monkeypatch.setattr(vm.subprocess, "run", lambda *_a, **_k: booted)

    console = vm.run_qemu(Path("kernel"), Path("initramfs"), tmp_path / "console.log")

    assert console == CONSOLE
