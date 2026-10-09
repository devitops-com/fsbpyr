"""The glibc sysroot the stub links against (plan Phase 6c, D25)."""

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from fsbpyr.runtime import ar
from fsbpyr.runtime.pbs import PbsError, download
from fsbpyr.runtime.pins import SysrootPin
from fsbpyr.runtime.sysroot import SysrootError, extract_deb, fetch_sysroot


def _ar(members: dict[str, bytes]) -> bytes:
    out = bytearray(b"!<arch>\n")
    for name, body in members.items():
        header = f"{name:<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(body):<10}`\n"
        out += header.encode() + body + (b"\n" if len(body) % 2 else b"")
    return bytes(out)


def _deb(entries: list[tarfile.TarInfo | tuple[str, bytes]]) -> bytes:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:xz") as tar:
        for entry in entries:
            if isinstance(entry, tarfile.TarInfo):
                tar.addfile(entry)
            else:
                info = tarfile.TarInfo(entry[0])
                info.size = len(entry[1])
                tar.addfile(info, io.BytesIO(entry[1]))
    return _ar({"debian-binary": b"2.0\n", "control.tar.xz": b"", "data.tar.xz": data.getvalue()})


def _symlink(name: str, target: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type, info.linkname = tarfile.SYMTYPE, target
    return info


def test_ar_read_returns_member_contents(tmp_path: Path) -> None:
    archive = tmp_path / "x.a"
    archive.write_bytes(_ar({"a.o": b"AAA", "b.o": b"BB"}))

    assert ar.read(archive) == [("a.o", b"AAA"), ("b.o", b"BB")]
    assert ar.members(archive) == ["a.o", "b.o"]


def test_absolute_symlinks_are_rewritten_into_the_sysroot(tmp_path: Path) -> None:
    deb = tmp_path / "libc6-dev.deb"
    lib = "lib/x86_64-linux-gnu"
    deb.write_bytes(_deb([
        (f"./{lib}/libpthread.so.0", b"ELF"),
        _symlink(f"./usr/{lib}/libpthread.so", f"/{lib}/libpthread.so.0"),
        _symlink(f"./usr/{lib}/libm.so", "libm.so.6"),
    ]))  # fmt: skip
    root = tmp_path / "root"

    extract_deb(deb.read_bytes(), root, deb.name)

    link = root / "usr/lib/x86_64-linux-gnu/libpthread.so"
    assert link.readlink() == Path("../../../lib/x86_64-linux-gnu/libpthread.so.0")
    assert link.read_bytes() == b"ELF"  # resolves inside the sysroot, never to the host's
    assert (root / "usr/lib/x86_64-linux-gnu/libm.so").readlink() == Path("libm.so.6")


def test_entries_leaving_the_sysroot_are_refused(tmp_path: Path) -> None:
    deb = tmp_path / "evil.deb"
    deb.write_bytes(_deb([("./../outside", b"x")]))

    with pytest.raises(SysrootError):
        extract_deb(deb.read_bytes(), tmp_path / "root", deb.name)
    assert not (tmp_path / "outside").exists()


def _hardlink(name: str, target: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type, info.linkname = tarfile.LNKTYPE, target
    return info


@pytest.mark.parametrize(
    "entries",
    [
        [_symlink("./usr/lib/escape", "../../../outside")],  # relative escape
        [_hardlink("./usr/lib/escape", "../../outside")],
        [_hardlink("./usr/lib/escape", "/etc/passwd")],
    ],
    ids=["relative-symlink", "hardlink-escape", "hardlink-absolute"],
)
def test_links_leaving_the_sysroot_are_refused(
    tmp_path: Path, entries: list[tarfile.TarInfo]
) -> None:
    with pytest.raises(SysrootError, match=r"evil\.deb"):
        extract_deb(_deb(list(entries)), tmp_path / "root", "evil.deb")
    assert not (tmp_path / "outside").exists()


def test_absolute_symlinks_with_dotdot_stay_inside(tmp_path: Path) -> None:
    root = tmp_path / "root"

    extract_deb(_deb([_symlink("./usr/lib/x", "/../../../etc/passwd")]), root, "x.deb")

    assert (root / "usr/lib/x").readlink() == Path("../../etc/passwd")  # <root>/etc/passwd


def test_a_package_without_data_is_reported(tmp_path: Path) -> None:
    with pytest.raises(SysrootError, match=r"empty\.deb: no data archive"):
        extract_deb(_ar({"debian-binary": b"2.0\n"}), tmp_path / "root", "empty.deb")


def test_fetch_verifies_extracts_once_and_reuses(tmp_path: Path) -> None:
    deb = _deb([("./usr/include/stdio.h", b"/* stdio */")])
    cache = tmp_path / "cache"
    (cache / "downloads").mkdir(parents=True)
    (cache / "downloads" / "libc6-dev.deb").write_bytes(deb)  # as if downloaded before
    pin = SysrootPin(name="test-sysroot", glibc="2.28", platform="x86_64-manylinux_2_28",
                     packages=(("https://example.invalid/libc6-dev.deb",
                                hashlib.sha256(deb).hexdigest()),))  # fmt: skip

    root = fetch_sysroot(pin, cache)

    assert root == cache / "sysroots" / pin.identity
    assert (root / "usr/include/stdio.h").read_bytes() == b"/* stdio */"
    assert (root / ".lock").is_file() and (cache / ".fsbpyr-cache").is_file()  # held (D38)
    (cache / "downloads" / "libc6-dev.deb").unlink()  # not needed any more
    assert fetch_sysroot(pin, cache) == root


def test_fetch_rejects_a_tampered_download(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    (cache / "downloads").mkdir(parents=True)
    (cache / "downloads" / "libc6-dev.deb").write_bytes(b"tampered")
    pin = SysrootPin(name="test-sysroot", glibc="2.28", platform="x86_64-manylinux_2_28",
                     packages=(("https://example.invalid/libc6-dev.deb", "0" * 64),))  # fmt: skip

    with pytest.raises(SysrootError, match="SHA-256"):
        fetch_sysroot(pin, cache)


def test_pin_identity_covers_the_package_hashes() -> None:
    pin = SysrootPin(name="s", glibc="2.28", platform="x86_64-manylinux_2_28",
                     packages=(("https://example.invalid/a.deb", "1" * 64),))  # fmt: skip
    changed = SysrootPin(name="s", glibc="2.28", platform="x86_64-manylinux_2_28",
                         packages=(("https://example.invalid/a.deb", "2" * 64),))  # fmt: skip

    assert pin.identity.startswith("s-") and pin.identity != changed.identity
    assert pin.baseline == (2, 28)


def test_downloads_must_use_https(tmp_path: Path) -> None:
    with pytest.raises(PbsError, match="https"):
        download("http://example.invalid/a.deb", "0" * 64, tmp_path / "a.deb")
