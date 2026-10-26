"""The glibc sysroot the stub links against (plan Phase 6c, D25)."""

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from fsbpyr import targets
from fsbpyr.runtime import ar
from fsbpyr.runtime.pbs import PbsError, download
from fsbpyr.runtime.pins import SysrootPin
from fsbpyr.runtime.sysroot import (
    SysrootError,
    build_flags,
    extract_deb,
    extract_rpm,
    fetch_sysroot,
)
from fsbpyr.targets import host_target

from ...support import rpms

MULTIARCH = host_target().multiarch  # Debian's layout of the pinned packages, per target


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
    lib = f"lib/{MULTIARCH}"
    deb.write_bytes(_deb([
        (f"./{lib}/libpthread.so.0", b"ELF"),
        _symlink(f"./usr/{lib}/libpthread.so", f"/{lib}/libpthread.so.0"),
        _symlink(f"./usr/{lib}/libm.so", "libm.so.6"),
    ]))  # fmt: skip
    root = tmp_path / "root"

    extract_deb(deb.read_bytes(), root, deb.name)

    link = root / f"usr/lib/{MULTIARCH}/libpthread.so"
    assert link.readlink() == Path(f"../../../lib/{MULTIARCH}/libpthread.so.0")
    assert link.read_bytes() == b"ELF"  # resolves inside the sysroot, never to the host's
    assert (root / f"usr/lib/{MULTIARCH}/libm.so").readlink() == Path("libm.so.6")


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
    pin = SysrootPin(name="test-sysroot", glibc="2.36", manylinux="2.34",
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
    pin = SysrootPin(name="test-sysroot", glibc="2.36", manylinux="2.34",
                     packages=(("https://example.invalid/libc6-dev.deb", "0" * 64),))  # fmt: skip

    with pytest.raises(SysrootError, match="SHA-256"):
        fetch_sysroot(pin, cache)


def test_pin_identity_covers_the_package_hashes() -> None:
    pin = SysrootPin(name="s", glibc="2.36", manylinux="2.34",
                     packages=(("https://example.invalid/a.deb", "1" * 64),))  # fmt: skip
    changed = SysrootPin(name="s", glibc="2.36", manylinux="2.34",
                         packages=(("https://example.invalid/a.deb", "2" * 64),))  # fmt: skip

    assert pin.identity.startswith("s-") and pin.identity != changed.identity
    assert pin.baseline == (2, 34)  # the apps', below the sysroot's own glibc


def test_downloads_must_use_https(tmp_path: Path) -> None:
    with pytest.raises(PbsError, match="https"):
        download("http://example.invalid/a.deb", "0" * 64, tmp_path / "a.deb")


def test_build_flags_point_the_host_compiler_at_the_sysroot(tmp_path: Path) -> None:
    root = tmp_path / "sysroot"
    (root / f"usr/lib/gcc/{MULTIARCH}/8").mkdir(parents=True)

    flags = build_flags(root, {"CFLAGS": "-O2", "CPPFLAGS": "-DX"})

    cflags, ldflags = flags["CFLAGS"].split(), flags["LDFLAGS"].split()
    assert cflags == [f"--sysroot={root}", "-isystem", f"{root}/usr/include/{MULTIARCH}",
                      "-O2"]  # the caller's own flags last  # fmt: skip
    assert ldflags == [f"--sysroot={root}", f"-B{root}/usr/lib/{MULTIARCH}",
                       f"-B{root}/usr/lib/gcc/{MULTIARCH}/8",  # crtbeginS.o, libgcc
                       f"-L{root}/usr/lib/{MULTIARCH}",
                       f"-L{root}/lib/{MULTIARCH}"]  # fmt: skip
    assert "CPPFLAGS" not in flags


def test_an_rpm_is_extracted_as_a_deb_is_less_what_linking_never_reads(tmp_path: Path) -> None:
    package = rpms.rpm([
        rpms.file("./usr/lib64/libc.so.6", b"ELF"),
        rpms.symlink("./usr/lib64/libpthread.so", "/usr/lib64/libpthread.so.0"),  # absolute
        rpms.file("./lib64/libgcc_s.so.1", b"GCC"),  # libgcc's RPM, before the merged /usr
        rpms.file("./usr/include/stdio.h", b"/* stdio */"),
        rpms.file("./usr/libexec/gcc/x86_64-redhat-linux/8/cc1", b"compiler"),
        rpms.file("./usr/bin/gcc", b"driver"),
        rpms.file("./usr/share/doc/README", b"x"),
        rpms.file("./usr/lib64/gconv/ISO8859-1.so", b"x"),
        rpms.file("./etc/ld.so.conf", b"x"),
    ])

    extract_rpm(package, tmp_path / "root", "glibc.rpm")

    root = tmp_path / "root"
    assert (root / "usr/lib64/libc.so.6").read_bytes() == b"ELF"
    assert (root / "usr/lib64/libpthread.so").readlink() == Path("libpthread.so.0")  # inside
    assert (root / "usr/lib64/libgcc_s.so.1").read_bytes() == b"GCC"
    assert (root / "usr/include/stdio.h").is_file()
    assert sorted(p.name for p in root.iterdir()) == ["usr"]
    assert sorted(p.name for p in (root / "usr").iterdir()) == ["include", "lib64"]
    assert not (root / "usr/lib64/gconv/ISO8859-1.so").exists()


@pytest.mark.parametrize(
    "entries",
    [
        [rpms.file("./../outside", b"x")],
        [rpms.symlink("./usr/lib64/escape", "../../../outside")],
    ],
    ids=["dotdot", "relative-symlink"],
)
def test_rpm_entries_leaving_the_sysroot_are_refused(
    tmp_path: Path, entries: list[rpms.Entry]
) -> None:
    with pytest.raises(SysrootError, match=r"evil\.rpm"):
        extract_rpm(rpms.rpm(entries), tmp_path / "root", "evil.rpm")
    assert not (tmp_path / "outside").exists()


def test_a_damaged_rpm_is_reported_by_name(tmp_path: Path) -> None:
    with pytest.raises(SysrootError, match=r"cannot extract bad\.rpm: not an RPM"):
        extract_rpm(b"x" * 200, tmp_path / "root", "bad.rpm")


def test_fetch_of_rpms_links_lib_and_lib64_into_usr(tmp_path: Path) -> None:
    package = rpms.rpm([rpms.file("./usr/lib64/crt1.o", b"o"),
                        rpms.symlink("./lib/ld-linux-aarch64.so.1", "../lib64/ld-2.28.so"),
                        rpms.file("./usr/lib64/ld-2.28.so", b"ld")])  # fmt: skip
    cache = tmp_path / "cache"
    (cache / "downloads").mkdir(parents=True)
    (cache / "downloads" / "glibc-devel.x86_64.rpm").write_bytes(package)
    pin = SysrootPin(name="rocky8-test", glibc="2.28", manylinux="2.28", packaging="rpm",
                     layout="redhat",
                     packages=(("https://example.invalid/glibc-devel.x86_64.rpm",
                                hashlib.sha256(package).hexdigest()),))  # fmt: skip

    root = fetch_sysroot(pin, cache)

    assert (root / "lib64").readlink() == Path("usr/lib64")  # the linker scripts name /lib64/...
    assert (root / "lib64/crt1.o").read_bytes() == b"o"
    assert (root / "lib").readlink() == Path("usr/lib")  # and /lib/ld-linux-aarch64.so.1
    assert (root / "lib/ld-linux-aarch64.so.1").read_bytes() == b"ld"
    assert pin.baseline == (2, 28)


def test_fetch_of_debs_makes_no_lib_links(tmp_path: Path) -> None:
    deb = _deb([("./usr/include/stdio.h", b"/* stdio */")])
    cache = tmp_path / "cache"
    (cache / "downloads").mkdir(parents=True)
    (cache / "downloads" / "libc6-dev.deb").write_bytes(deb)
    pin = SysrootPin(name="test-sysroot", glibc="2.36", manylinux="2.34",
                     packages=(("https://example.invalid/libc6-dev.deb",
                                hashlib.sha256(deb).hexdigest()),))  # fmt: skip

    root = fetch_sysroot(pin, cache)
    assert not (root / "lib64").exists() and not (root / "lib").exists()


def _host(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setattr(targets, "host_target", lambda: targets.target_named(name))


def test_build_flags_of_the_red_hat_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch, "x86_64-linux-gnu")
    root = tmp_path / "sysroot"
    (root / "usr/lib/gcc/x86_64-redhat-linux/8").mkdir(parents=True)

    flags = build_flags(root, {"CFLAGS": "-O2", "LDFLAGS": "-Wl,-z,now"}, "redhat")

    assert flags["CFLAGS"] == f"--sysroot={root} -O2"  # headers: where --sysroot looks
    assert flags["LDFLAGS"].split() == [
        f"--sysroot={root}", f"-B{root}/usr/lib64", f"-B{root}/usr/lib/gcc/x86_64-redhat-linux/8",
        f"-L{root}/usr/lib64", "-Wl,-z,now"]  # fmt: skip


def test_aarch64_red_hat_flags_add_no_outline_atomics_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rocky's libgcc.a (gcc 8.5) has the __aarch64_* helpers, and gcc older than 10 rejects
    -mno-outline-atomics: the flags are the x86-64 ones."""
    _host(monkeypatch, "aarch64-linux-gnu")
    root = tmp_path / "sysroot"
    (root / "usr/lib/gcc/aarch64-redhat-linux/8").mkdir(parents=True)

    assert build_flags(root, {}, "redhat")["CFLAGS"] == f"--sysroot={root}"
