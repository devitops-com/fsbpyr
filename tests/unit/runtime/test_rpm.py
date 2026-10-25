"""The RPM reader of the EL8 sysroot (runtime/rpm.py): synthetic packages, no network."""

import io
import lzma
import tarfile

import pytest

from fsbpyr.runtime import rpm

from ...support import rpms


def _members(data: bytes) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(rpm.payload_tar(data))) as tar:
        return {member.name: member for member in tar.getmembers()}


def _contents(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(rpm.payload_tar(data))) as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isreg()}  # type: ignore[union-attr]


def test_files_directories_and_symlinks_come_out_as_tar_members() -> None:
    package = rpms.rpm([
        rpms.directory("./usr/lib64"),
        rpms.file("./usr/lib64/libc-2.28.so", b"ELF", 0o755),
        rpms.symlink("./usr/lib64/libc.so.6", "libc-2.28.so"),
        rpms.file("./usr/lib64/odd name.txt", b""),
    ])

    members = _members(package)

    assert members["usr/lib64"].isdir()
    lib, link = members["usr/lib64/libc-2.28.so"], members["usr/lib64/libc.so.6"]
    assert (lib.mode, lib.size) == (0o755, 3)
    assert link.issym() and link.linkname == "libc-2.28.so"
    assert _contents(package)["usr/lib64/libc-2.28.so"] == b"ELF"
    assert _contents(package)["usr/lib64/odd name.txt"] == b""


def test_hard_links_follow_the_file_that_carries_the_data() -> None:
    """cpio stores the data of a set of hard links with its last name; tar needs it first."""
    package = rpms.rpm([
        rpms.file("./usr/libexec/a", b"", links=3, inode=7),
        rpms.file("./usr/libexec/b", b"", links=3, inode=7),
        rpms.file("./usr/libexec/c", b"data", links=3, inode=7),
        rpms.file("./usr/libexec/other", b"x"),
    ])

    members = _members(package)

    assert list(members)[:3] == ["usr/libexec/c", "usr/libexec/a", "usr/libexec/b"]
    assert members["usr/libexec/c"].isreg() and members["usr/libexec/c"].size == 4
    for name in ("usr/libexec/a", "usr/libexec/b"):
        assert members[name].islnk() and members[name].linkname == "usr/libexec/c"


def test_links_to_empty_files_are_links_too() -> None:
    package = rpms.rpm([rpms.file("./a", links=2, inode=5), rpms.file("./b", links=2, inode=5)])

    members = _members(package)

    assert members["b"].isreg() and members["a"].islnk() and members["a"].linkname == "b"


def test_what_the_merged_usr_links_cover_is_named_under_usr() -> None:
    """EL8's filesystem package links /lib64 to usr/lib64; libgcc's RPM still lists lib64/..."""
    package = rpms.rpm([
        rpms.file("./lib64/libgcc_s-8.so.1", b"x"),
        rpms.symlink("./lib64/libgcc_s.so.1", "libgcc_s-8.so.1"),
        rpms.file("./sbin/ldconfig", b"x"),
        rpms.file("./.hidden", b"x"),  # a leading dot is part of the name
        rpms.file("./library/x", b"x"),  # only whole path components
    ])

    assert set(_members(package)) == {
        "usr/lib64/libgcc_s-8.so.1", "usr/lib64/libgcc_s.so.1", "usr/sbin/ldconfig", ".hidden",
        "library/x",
    }  # fmt: skip


def test_absolute_names_are_made_relative() -> None:
    assert set(_members(rpms.rpm([rpms.file("/usr/include/stdio.h", b"x")]))) == {
        "usr/include/stdio.h"}  # fmt: skip


@pytest.mark.parametrize(
    ("package", "message"),
    [
        (b"not an rpm at all" * 20, "not an RPM"),
        (rpms.rpm([], compressor="gzip"), "only xz is supported"),
        (rpms.rpm([], compressor="zstd"), "only xz is supported"),
        (rpms.rpm([], payload=b"not xz"), "damaged payload"),
        (rpms.rpm([rpms.file("a", b"x")])[:-30], "damaged payload|too large or truncated"),
        (rpms.rpm([], payload=rpms.cpio([]).replace(b"070701", b"070702", 1)), "damaged payload"),
        (rpms.rpm([rpms.file("../escape", b"x")]), "leaves the package"),
        (rpms.rpm([rpms.file("a/../../escape", b"x")]), "leaves the package"),
        (rpms.rpm([("./dev/null", rpms.S_CHR | 0o666, b"", 1, 0)]), "neither a file"),
        (rpms.rpm([rpms.file("./a", b"", links=2, inode=3)]), "hard links is incomplete"),
    ],
    ids=["not-rpm", "gzip", "zstd", "not-xz", "truncated", "bad-xz", "dotdot", "dotdot-inside",
         "device", "hard-link-set"],
)  # fmt: skip
def test_what_cannot_be_read_safely_is_refused(package: bytes, message: str) -> None:
    with pytest.raises(rpm.RpmError, match=message):
        rpm.payload_tar(package)


def test_a_cpio_archive_that_is_not_one_is_refused() -> None:
    package = rpms.rpm([], payload=lzma.compress(b"070701" + b"zz" * 60))

    with pytest.raises(rpm.RpmError, match="bad cpio header"):
        rpm.payload_tar(package)


def test_a_payload_over_the_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rpm, "_MAX_PAYLOAD", 100)

    with pytest.raises(rpm.RpmError, match="too large"):
        rpm.payload_tar(rpms.rpm([rpms.file("a", b"x" * 1000)]))


@pytest.mark.parametrize("cut", [8, 20, 100, 120])
def test_a_truncated_package_is_refused_not_misread(cut: int) -> None:
    with pytest.raises(rpm.RpmError):
        rpm.payload_tar(rpms.rpm([rpms.file("a", b"x")])[:rpm.LEAD + cut])


def test_header_sizes_that_do_not_fit_are_refused() -> None:
    package = bytearray(rpms.rpm([]))
    package[rpm.LEAD + 8:rpm.LEAD + 12] = (10**6).to_bytes(4, "big")  # the signature's entries

    with pytest.raises(rpm.RpmError, match="impossible"):
        rpm.payload_tar(bytes(package))
