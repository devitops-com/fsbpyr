"""The target table and the host's detection (plan Phase 9, "More platforms")."""

import lzma
import os
import platform
import re
import sys
from pathlib import Path

import pytest

from fsbpyr import elf, natives, targets
from fsbpyr.elf import ElfError, dynamic_info
from fsbpyr.natives import glibc_libraries, system_libraries
from fsbpyr.payload import spec
from fsbpyr.payload.spec import _xz_filters
from fsbpyr.runtime import collect, pins, registry
from fsbpyr.targets import Target
from tests.support.elf import make_elf

X86_GNU = Target("x86_64-linux-gnu", "x86_64-unknown-linux-gnu", "pgo+lto-full", "x86-64",
                 62, "ld-linux-x86-64.so.2", "glibc", lzma.FILTER_X86,
                 "x86_64-linux-gnu", "x86_64-manylinux_2_34")  # fmt: skip
DEB_ARCH = {"x86-64": "amd64", "aarch64": "arm64"}  # Debian's names of the machines
EXPECTED = (
    X86_GNU,
    Target("aarch64-linux-gnu", "aarch64-unknown-linux-gnu", "pgo+lto-full", "aarch64",
           183, "ld-linux-aarch64.so.1", "glibc", None,
           "aarch64-linux-gnu", "aarch64-manylinux_2_34"),
    Target("x86_64-linux-musl", "x86_64-unknown-linux-musl", "lto-full", "x86-64",
           62, "ld-musl-x86_64.so.1", "musl", lzma.FILTER_X86,
           "x86_64-linux-musl", "x86_64-unknown-linux-musl"),
    Target("aarch64-linux-musl", "aarch64-unknown-linux-musl", "lto-full", "aarch64",
           183, "ld-musl-aarch64.so.1", "musl", None,
           "aarch64-linux-musl", "aarch64-unknown-linux-musl"),
    Target("aarch64-macos", "aarch64-apple-darwin", "pgo+lto-full", "arm64",
           None, None, "macos", None, None, "aarch64-apple-darwin"),
)  # fmt: skip


def test_the_table_holds_the_five_targets_with_every_field() -> None:
    assert targets.TARGETS == EXPECTED


@pytest.mark.parametrize("target", EXPECTED, ids=lambda t: t.name)
def test_a_target_is_found_by_name_and_by_triple(target: Target) -> None:
    assert targets.target_named(target.name) is targets.target_named(target.triple)


def test_an_unknown_target_name_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown target nope"):
        targets.target_named("nope")


@pytest.mark.parametrize(
    ("system", "machine", "libc", "name"),
    [
        ("linux", "x86_64", "glibc", "x86_64-linux-gnu"),
        ("linux", "aarch64", "glibc", "aarch64-linux-gnu"),
        ("linux", "arm64", "glibc", "aarch64-linux-gnu"),
        ("linux", "x86_64", "musl", "x86_64-linux-musl"),
        ("linux", "aarch64", "musl", "aarch64-linux-musl"),
        ("darwin", "arm64", None, "aarch64-macos"),
        ("darwin", "arm64", "glibc", "aarch64-macos"),
    ],
)
def test_the_host_target_comes_from_platform_machine_and_libc(
    system: str, machine: str, libc: str | None, name: str
) -> None:
    assert targets.detect(system, machine, libc).name == name


@pytest.mark.parametrize(
    ("system", "machine", "libc"),
    [
        ("linux", "riscv64", "glibc"),
        ("linux", "x86_64", None),
        ("darwin", "x86_64", None),
        ("win32", "AMD64", None),
        ("freebsd14", "amd64", None),
    ],
)
def test_an_unsupported_host_is_a_clear_error(system: str, machine: str, libc: str | None) -> None:
    with pytest.raises(targets.UnsupportedHost, match=f"unsupported host {system}/{machine}"):
        targets.detect(system, machine, libc)


def test_host_target_reads_the_running_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    monkeypatch.setattr(targets, "host_libc", lambda: None)

    assert targets.host_target().name == "aarch64-macos"
    assert registry.host_triple() == "aarch64-apple-darwin"


def test_registry_reports_an_unsupported_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")

    with pytest.raises(registry.RuntimeCacheError, match="unsupported host win32/AMD64"):
        registry.host_triple()


def test_host_libc_is_glibc_where_confstr_knows_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "confstr", lambda name: "glibc 2.41")
    assert targets.host_libc() == "glibc"


def test_host_libc_is_musl_with_musl_s_loader_and_no_confstr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse(name: str) -> str:
        raise ValueError(name)

    monkeypatch.setattr(os, "confstr", refuse)
    monkeypatch.setattr(targets, "Path", lambda _: tmp_path)
    assert targets.host_libc() is None
    (tmp_path / "ld-musl-x86_64.so.1").touch()
    assert targets.host_libc() == "musl"


def test_the_pins_agree_with_the_table() -> None:
    for pin in pins.PINS:
        target = targets.target_named(pin.triple)
        assert pin.flavor == target.flavor
    assert set(pins.SYSROOTS) <= {t.triple for t in targets.TARGETS}


PINNED = [t for t in EXPECTED if t.libc == "glibc"]
UNPINNED = [t for t in EXPECTED if t.libc != "glibc"]


@pytest.mark.parametrize("target", PINNED, ids=lambda t: t.name)
def test_a_glibc_target_has_pins_for_every_python_and_a_sysroot(target: Target) -> None:
    for python in ("3.12", "3.13", "3.14"):
        pin = pins.pin_for(python, target.triple)
        assert (pin.triple, pin.flavor, pin.release) == (target.triple, target.flavor, "20261021")
        assert pin.version.startswith(f"{python}.")
        assert re.fullmatch(r"[0-9a-f]{64}", pin.sha256)
        assert pin.url.endswith(f"-{target.triple}-{target.flavor.replace('+', '%2B')}.tar.zst")
    sysroot = pins.sysroot_for(target.triple)
    assert (sysroot.glibc, sysroot.baseline) == ("2.36", (2, 34))
    assert target.uv_platform == f"{target.triple.partition('-')[0]}-manylinux_2_34"
    assert all(url.endswith(f"_{DEB_ARCH[target.arch]}.deb") for url, _ in sysroot.packages)
    # Debian's own repository: no snapshot, no archive of a release out of support
    assert all(url.startswith("https://deb.debian.org/debian/pool/main/")
               for url, _ in sysroot.packages)  # fmt: skip


def test_the_aarch64_sysroot_is_the_x86_64_ones_packages_for_arm64() -> None:
    def versions(pin: pins.SysrootPin) -> list[str]:
        return [url.rpartition("/")[2].rpartition("_")[0] for url, _ in pin.packages]

    x86, arm = (pins.sysroot_for(t.triple) for t in PINNED)
    assert versions(x86) == versions(arm)
    assert x86.identity != arm.identity
    assert not {sha for _, sha in arm.packages} & {sha for _, sha in x86.packages}


EL8_PACKAGES = ("glibc", "glibc-devel", "glibc-headers", "kernel-headers", "libgcc", "gcc")


@pytest.mark.parametrize("target", PINNED, ids=lambda t: t.name)
def test_the_el8_sysroot_is_rocky_linux_8_10s_packages(target: Target) -> None:
    sysroot = pins.sysroot_for(target.triple, "2.28")

    assert (sysroot.glibc, sysroot.baseline) == ("2.28", (2, 28))
    assert (sysroot.packaging, sysroot.layout) == ("rpm", "redhat")
    names = [url.rpartition("/")[2] for url, _ in sysroot.packages]
    assert [re.match(r"(.+?)-\d", name)[1] for name in names] == list(EL8_PACKAGES)  # type: ignore[index]
    assert all(name.endswith(f".{target.triple.partition('-')[0]}.rpm") for name in names)
    arch = target.triple.partition("-")[0]
    base = f"https://dl.rockylinux.org/pub/rocky/8.10/%s/{arch}/os/Packages/"
    for url, sha256 in sysroot.packages:  # only gcc has crtbeginS.o and libgcc.a: AppStream
        repo = "AppStream" if "/gcc-8" in url else "BaseOS"
        assert url.startswith(base % repo) and re.fullmatch(r"[0-9a-f]{64}", sha256), url
    assert sysroot.name == f"rocky8-glibc2.28-{arch}"
    assert re.fullmatch(r"rocky8-glibc2\.28-\w+-[0-9a-f]{12}", sysroot.identity)


def test_the_el8_sysroots_are_the_same_builds_for_both_targets() -> None:
    def versions(pin: pins.SysrootPin) -> list[str]:
        return [url.rpartition("/")[2].rsplit(".", 2)[0] for url, _ in pin.packages]

    x86, arm = (pins.sysroot_for(t.triple, "2.28") for t in PINNED)
    assert versions(x86) == versions(arm)
    assert not {sha for _, sha in arm.packages} & {sha for _, sha in x86.packages}


def test_the_default_sysroot_stays_debian_12s_and_other_baselines_are_refused() -> None:
    for target in PINNED:
        default = pins.sysroot_for(target.triple)
        assert default is pins.sysroot_for(target.triple, "2.34")
        assert (default.packaging, default.layout) == ("deb", "multiarch")
        assert default.identity != pins.sysroot_for(target.triple, "2.28").identity
    with pytest.raises(ValueError, match=r"no glibc 2\.27 baseline; supported: 2\.34, 2\.28"):
        pins.sysroot_for("x86_64-unknown-linux-gnu", "2.27")
    with pytest.raises(ValueError, match="no glibc sysroot pinned for"):
        pins.sysroot_for("riscv64-unknown-linux-gnu", "2.28")
    assert set(pins.EL8_SYSROOTS) == set(pins.SYSROOTS)


def test_runtime_ids_differ_by_baseline_and_the_default_is_unchanged() -> None:
    for pin in pins.PINS:
        default = registry.runtime_id(pin)
        el8 = registry.runtime_id(pin, baseline="2.28")
        assert default == registry.runtime_id(pin, baseline="2.34") and default != el8
        assert registry.runtime_id(pin, sanitize=True, baseline="2.28") not in (el8, default)
    ids = {registry.runtime_id(p, baseline=b) for p in pins.PINS for b in pins.BASELINES}
    assert len(ids) == 2 * len(pins.PINS)


def test_the_pins_of_each_python_differ_per_target() -> None:
    for python in ("3.12", "3.13", "3.14"):
        first, second = (pins.pin_for(python, t.triple) for t in PINNED)
        assert first.version == second.version
        assert first.sha256 != second.sha256


@pytest.mark.parametrize("target", UNPINNED, ids=lambda t: t.name)
def test_a_target_without_pins_stops_before_any_download(
    target: Target, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(registry, "host_triple", lambda: target.triple)

    with pytest.raises(LookupError, match=f"does not support the {target.triple} target yet"):
        registry.find_runtime("3.14", tmp_path)
    with pytest.raises(LookupError, match="does not support"):
        registry.build_runtime("3.14", cache=tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("target", EXPECTED, ids=lambda t: t.name)
def test_each_target_picks_its_loader_filter_and_machine(
    target: Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    for module in (elf, natives, spec):
        monkeypatch.setattr(module, "host_target", lambda: target)

    assert (target.loader in system_libraries()) == (target.loader is not None)
    assert (target.loader in glibc_libraries()) == (target.loader is not None)
    assert [f["id"] for f in _xz_filters()] == [
        *([target.xz_filter] if target.xz_filter else []),
        lzma.FILTER_LZMA2,
    ]
    if target.elf_machine is None:
        with pytest.raises(ElfError, match=f"not {target.arch}"):
            dynamic_info(make_elf())
    else:
        assert dynamic_info(make_elf(machine=target.elf_machine)).needed == ()
        with pytest.raises(ElfError, match=f"not {target.arch}"):
            dynamic_info(make_elf(machine=3))


def test_sysroot_names_of_every_libc_are_ours_to_collect() -> None:
    names = collect.NAMES["sysroots"]
    assert names.fullmatch("debian12-glibc2.36-x86_64-0123456789ab")
    assert names.fullmatch("debian10-glibc2.28-aarch64")  # a sysroot of an older fsbpyr
    assert names.fullmatch("alpine314-musl1.2.2-aarch64")
    assert not names.fullmatch("debian10-bionic2.28-x86_64")
