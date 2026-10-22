"""tools/release/sbom.py: the CycloneDX SBOM of a release executable (REP-7)."""

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from ..support.sbom import PATH, load

sbom = load()

SHA256 = "ab" * 32
WHEEL_SHA256 = "cd" * 32
SDIST_SHA256 = "ef" * 32
INSPECT: dict[str, Any] = {
    "schema": 1,
    "path": "/somewhere/dist/fsbpyr-3.14-x86_64-linux-gnu",  # differs between two builds
    "size": 31_000_000,
    "sha256": SHA256,
    "verified": True,
    "app": {"name": "fsbpyr", "version": "0.20.0", "revision": "0.20.0-4f9771de"},
    "python": "3.14.8",
    "build_info": {
        "schema": 1,
        "fsbpyr": "0.20.0",
        "release_asset": "fsbpyr-3.14-x86_64-linux-gnu",
        "python": "3.14.8",
        "runtime_components": {
            "expat": "expat_2.7.3",  # pyexpat.EXPAT_VERSION, as runtime/pbs.py records it
            "libmpdec": "4.0.1",
            "openssl": "OpenSSL 3.5.9 8 Apr 2026",
            "sqlite": "3.53.1",
            "zlib": "1.3.2",
        },
        "dists": [
            {"name": "fsbpyr", "version": "0.20.0", "source": "local"},
            {"name": "packaging", "version": "26.0", "source": "registry",
             "artifact_sha256": WHEEL_SHA256},
            {"name": "backports-zstd", "version": "1.3.0", "source": "built-from-sdist",
             "artifact_sha256": SDIST_SHA256, "backend": "Generator: setuptools"},
            {"name": "private", "version": "1.0", "source": "git"},
        ],
    },
}  # fmt: skip


def _bom(inspect: dict[str, Any] = INSPECT) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(sbom.render(inspect))
    return document


def _component(bom: dict[str, Any], name: str) -> dict[str, Any]:
    [found] = [c for c in bom["components"] if c["name"] == name]
    assert isinstance(found, dict)
    return found


def test_the_same_input_gives_the_same_bytes_whatever_the_path_and_order() -> None:
    other = copy.deepcopy(INSPECT)
    other["path"] = "/elsewhere/rebuilt"
    other["build_info"]["dists"].reverse()
    other["build_info"]["runtime_components"] = dict(
        reversed(other["build_info"]["runtime_components"].items()))

    assert sbom.render(INSPECT) == sbom.render(INSPECT) == sbom.render(other)
    assert sbom.render(INSPECT).endswith("\n")


def test_a_different_executable_gets_another_serial_number_and_the_same_one_keeps_it() -> None:
    other = copy.deepcopy(INSPECT)
    other["sha256"] = "12" * 32

    assert _bom()["serialNumber"] == _bom()["serialNumber"]
    assert _bom(other)["serialNumber"] != _bom()["serialNumber"]
    assert _bom()["serialNumber"].startswith("urn:uuid:")


def test_it_is_cyclonedx_1_6_without_a_timestamp() -> None:
    bom = _bom()

    assert (bom["bomFormat"], bom["specVersion"], bom["version"]) == ("CycloneDX", "1.6", 1)
    assert "timestamp" not in bom.get("metadata", {})


def test_the_executable_is_the_described_component_with_its_plain_sha256() -> None:
    executable = _bom()["metadata"]["component"]

    assert executable["type"] == "application"
    assert (executable["name"], executable["version"]) == ("fsbpyr-3.14-x86_64-linux-gnu", "0.20.0")
    assert executable["hashes"] == [{"alg": "SHA-256", "content": SHA256}]


def test_openssl_and_sqlite_carry_cpes_with_the_recorded_versions() -> None:
    bom = _bom()

    assert _component(bom, "openssl")["cpe"] == "cpe:2.3:a:openssl:openssl:3.5.9:*:*:*:*:*:*:*"
    assert _component(bom, "openssl")["version"] == "3.5.9"
    assert _component(bom, "sqlite")["cpe"] == "cpe:2.3:a:sqlite:sqlite:3.53.1:*:*:*:*:*:*:*"
    assert _component(bom, "zlib")["cpe"] == "cpe:2.3:a:zlib:zlib:1.3.2:*:*:*:*:*:*:*"
    assert _component(bom, "expat")["cpe"] == (
        "cpe:2.3:a:libexpat_project:libexpat:2.7.3:*:*:*:*:*:*:*")
    assert _component(bom, "expat")["version"] == "2.7.3"
    libmpdec = _component(bom, "libmpdec")  # NVD has no mpdecimal product
    assert libmpdec["version"] == "4.0.1" and "cpe" not in libmpdec


def test_a_vulnerable_openssl_version_reaches_the_cpe() -> None:
    old = copy.deepcopy(INSPECT)
    old["build_info"]["runtime_components"]["openssl"] = "OpenSSL 3.0.0 7 Sep 2021"

    assert _component(_bom(old), "openssl")["cpe"].split(":")[5] == "3.0.0"


def test_cpython_has_a_cpe_and_a_purl() -> None:
    cpython = _component(_bom(), "cpython")

    assert cpython["version"] == "3.14.8"
    assert cpython["cpe"] == "cpe:2.3:a:python:python:3.14.8:*:*:*:*:*:*:*"
    assert cpython["purl"] == "pkg:github/python/cpython@v3.14.8"


def test_registry_distributions_are_pypi_purls_with_their_hashes() -> None:
    bom = _bom()

    packaging = _component(bom, "packaging")
    assert packaging["purl"] == "pkg:pypi/packaging@26.0"
    assert packaging["hashes"] == [{"alg": "SHA-256", "content": WHEEL_SHA256}]
    zstd = _component(bom, "backports-zstd")
    assert zstd["purl"] == "pkg:pypi/backports-zstd@1.3.0"
    assert zstd["hashes"] == [{"alg": "SHA-256", "content": SDIST_SHA256}]


def test_distributions_that_are_not_on_an_index_are_left_out() -> None:
    names = [c["name"] for c in _bom()["components"]]

    assert "private" not in names
    assert names.count("fsbpyr") == 1  # the github component, not the local dist


def test_fsbpyr_is_a_github_component_at_its_tag() -> None:
    fsbpyr = _component(_bom(), "fsbpyr")

    assert fsbpyr["purl"] == "pkg:github/devitops-com/fsbpyr@v0.20.0"
    assert fsbpyr["version"] == "0.20.0"


def test_the_executable_depends_on_every_component_once() -> None:
    bom = _bom()
    refs = [c["bom-ref"] for c in bom["components"]]

    assert len(refs) == len(set(refs))
    assert bom["dependencies"] == [
        {"ref": bom["metadata"]["component"]["bom-ref"], "dependsOn": refs}]


def test_an_unknown_runtime_component_is_listed_without_a_cpe() -> None:
    other = copy.deepcopy(INSPECT)
    other["build_info"]["runtime_components"]["libffi"] = "3.4.8"

    libffi = _component(_bom(other), "libffi")
    assert libffi["version"] == "3.4.8" and "cpe" not in libffi


def test_an_executable_without_a_release_asset_is_named_after_its_app() -> None:
    app = copy.deepcopy(INSPECT)
    del app["build_info"]["release_asset"]
    app["app"] = {"name": "hello", "version": "1.0.0", "revision": None}

    executable = _bom(app)["metadata"]["component"]
    assert (executable["name"], executable["version"]) == ("hello", "1.0.0")


@pytest.mark.parametrize("drop", ["sha256", "build_info"])
def test_it_refuses_an_inspect_without_what_it_needs(drop: str) -> None:
    broken = copy.deepcopy(INSPECT)
    del broken[drop]

    with pytest.raises(sbom.SbomError, match=drop):
        sbom.render(broken)


def test_it_refuses_an_unverified_inspect() -> None:
    unverified = copy.deepcopy(INSPECT)
    unverified["sha256"] = None

    with pytest.raises(sbom.SbomError, match="--verify"):
        sbom.render(unverified)


def test_the_command_line_reads_a_file_or_stdin_and_writes_the_output_file(
    tmp_path: Path,
) -> None:
    source, out = tmp_path / "inspect.json", tmp_path / "out.cdx.json"
    source.write_text(json.dumps(INSPECT))
    run = [sys.executable, "-I", str(PATH)]

    from_file = subprocess.run([*run, str(source), "-o", str(out)], check=False,
                               capture_output=True, text=True)  # fmt: skip
    from_stdin = subprocess.run([*run, "-"], input=json.dumps(INSPECT), check=False,
                                capture_output=True, text=True)  # fmt: skip

    assert (from_file.returncode, from_stdin.returncode) == (0, 0)
    assert out.read_text() == from_stdin.stdout == sbom.render(INSPECT)


def test_the_command_line_reports_bad_input_and_exits_1(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json")

    result = subprocess.run([sys.executable, "-I", str(PATH), str(bad)], check=False,
                            capture_output=True, text=True)  # fmt: skip

    assert result.returncode == 1 and "sbom.py:" in result.stderr and result.stdout == ""
