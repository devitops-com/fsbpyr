#!/usr/bin/env python3
"""sbom.py: the CycloneDX 1.6 SBOM of a release executable (REP-7).

    fsbpyr inspect --verify --json EXE | python3 tools/release/sbom.py - [-o FILE]
    python3 tools/release/sbom.py INSPECT.json [-o FILE]

A pure function of that JSON, so two byte-identical executables give byte-identical SBOMs
(build.yml's compare job checks it): no timestamp, no path, and a serial number derived from
the executable's SHA-256. It lists the executable (with that SHA-256), fsbpyr, CPython, the
libraries the runtime links statically (build_info's runtime_components) and the Python
distributions from an index. The C libraries carry a CPE, which is what vulnerability
scanners match them by; the vendor and product names are NVD's (checked against its CPE
dictionary on 2026-10-22, and zstd's, facebook:zstandard, on 2026-10-27). NVD has no mpdecimal
product, so libmpdec is listed without one.
Standard library only: the release jobs run it with whatever Python they have.
"""

import argparse
import json
import re
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SPEC_VERSION = "1.6"
REPOSITORY = "devitops-com/fsbpyr"
# build_info's runtime_components key -> (vendor, product) of its CPE
CPES = {
    "expat": ("libexpat_project", "libexpat"),
    "openssl": ("openssl", "openssl"),
    "sqlite": ("sqlite", "sqlite"),
    "zlib": ("zlib", "zlib"),
    "zstd": ("facebook", "zstandard"),
}
# dist sources that name a package on an index (a git or local one has no purl)
INDEXED = ("registry", "built-from-sdist")


class SbomError(RuntimeError):
    """The inspect output lacks what an SBOM needs."""


def _cpe(vendor: str, product: str, version: str) -> str:
    return f"cpe:2.3:a:{vendor}:{product}:{version}:*:*:*:*:*:*:*"


def _sha256(digest: str) -> list[dict[str, str]]:
    return [{"alg": "SHA-256", "content": digest}]


def _component(name: str, version: str, *, purl: str = "", cpe: str = "",
               kind: str = "library") -> dict[str, Any]:  # fmt: skip
    component: dict[str, Any] = {
        "type": kind, "bom-ref": purl or f"{name}@{version}", "name": name, "version": version,
    }
    if purl:
        component["purl"] = purl
    if cpe:
        component["cpe"] = cpe
    return component


def _version(name: str, value: str) -> str:
    """OpenSSL reports "OpenSSL 3.5.9 8 Apr 2026"; its version is the second word. expat
    reports "expat_2.9.0" (pyexpat.EXPAT_VERSION), and its CPE needs "2.9.0"."""
    if name == "expat":
        return value.removeprefix("expat_")
    match = re.fullmatch(r"OpenSSL (\S+)(?: .*)?", value) if name == "openssl" else None
    return match.group(1) if match else value


def _runtime_components(info: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    python = info.get("python")
    if isinstance(python, str):
        found.append(_component("cpython", python, kind="platform",
                                purl=f"pkg:github/python/cpython@v{python}",
                                cpe=_cpe("python", "python", python)))  # fmt: skip
    components = info.get("runtime_components")
    for name, value in (components if isinstance(components, dict) else {}).items():
        version = _version(name, str(value))
        vendor, product = CPES.get(name, ("", ""))
        cpe = _cpe(vendor, product, version) if vendor else ""
        found.append(_component(name, version, cpe=cpe))
    return found


def _distributions(info: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for dist in info.get("dists") or []:
        if not isinstance(dist, dict) or dist.get("source") not in INDEXED:
            continue
        name, version = str(dist["name"]), str(dist["version"])
        component = _component(name, version, purl=f"pkg:pypi/{name}@{version}")
        if isinstance(digest := dist.get("artifact_sha256"), str):
            component["hashes"] = _sha256(digest)
        found.append(component)
    return found


def render(inspect: dict[str, Any]) -> str:
    """The SBOM, as JSON text, of one ``inspect --verify --json`` document."""
    digest = inspect.get("sha256")
    info = inspect.get("build_info")
    if not isinstance(info, dict):
        raise SbomError("the inspect output has no build_info")
    if not isinstance(digest, str) or not digest:
        raise SbomError("the inspect output has no sha256 (was it made without --verify?)")
    app = inspect.get("app") or {}
    name = info.get("release_asset") or app.get("name")
    version = app.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise SbomError("the inspect output names neither a release asset nor an app version")
    fsbpyr = str(info.get("fsbpyr", version))
    components = [
        _component("fsbpyr", fsbpyr, purl=f"pkg:github/{REPOSITORY}@v{fsbpyr}"),
        *_runtime_components(info),
        *_distributions(info),
    ]
    components.sort(key=lambda c: (c["type"] != "platform", c["name"], c["version"]))
    executable = {**_component(name, version, kind="application"), "hashes": _sha256(digest)}
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'sha256:{digest}')}",
        "version": 1,
        "metadata": {"component": executable},
        "components": components,
        "dependencies": [{"ref": executable["bom-ref"],
                          "dependsOn": [c["bom-ref"] for c in components]}],
    }  # fmt: skip
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CycloneDX SBOM from `fsbpyr inspect --json`")
    parser.add_argument("inspect", help="the JSON file, or - for standard input")
    parser.add_argument("-o", "--output", type=Path, help="write here instead of to stdout")
    args = parser.parse_args(argv)
    try:
        text = sys.stdin.read() if args.inspect == "-" else Path(args.inspect).read_text()
        document = json.loads(text)
        if not isinstance(document, dict):
            raise SbomError("the inspect output is not a JSON object")
        sbom = render(document)
        if args.output is not None:
            args.output.write_text(sbom)
        else:
            sys.stdout.write(sbom)
    except (OSError, ValueError, SbomError) as exc:
        print(f"sbom.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
