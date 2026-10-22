"""`make check-c`: clang's static analyzer over the launcher's C sources, compiled as the
launcher is (runtime/stub.py's flags and defines, the cached PBS distribution's headers).
Prints the analyzer's report; exits 1 when it has findings or a source fails to compile."""

import subprocess
import sys
from pathlib import Path

from fsbpyr.build import TARGET_PYTHON
from fsbpyr.runtime import pbs, pins, registry
from fsbpyr.runtime.stub import COMPILE_FLAGS, LAUNCHER_SOURCES
from fsbpyr.runtime.toolchain import find_toolchain

LAUNCHER = Path(__file__).parents[1] / "src" / "fsbpyr" / "launcher"
# the optimization and link-time flags mean nothing to the analyzer
_SKIPPED = ("-O2", "-flto=full", "-fPIE", "-pie")


def main() -> int:
    pin = pins.pin_for(TARGET_PYTHON, registry.host_triple())
    dist = pbs.fetch(pin, registry.cache_root())
    clang = find_toolchain(dist.llvm_major).clang
    magic = int.from_bytes(dist.bytecode_magic, "little")
    flags = [f for f in COMPILE_FLAGS if f not in _SKIPPED]
    findings = 0
    for name in LAUNCHER_SOURCES:
        command = [str(clang), "--analyze", "-Xclang", "-analyzer-output=text", *flags,
                   f"-DFSBPYR_BYTECODE_MAGIC=0x{magic:08x}u", f"-I{dist.include}",
                   f"-I{LAUNCHER}", "-o", "/dev/null", str(LAUNCHER / name)]  # fmt: skip
        result = subprocess.run(  # noqa: S603  the clang found for the runtime
            command, capture_output=True, text=True, check=False
        )
        report = result.stdout + result.stderr
        print(f"== {name}: {'clean' if not report.strip() else 'see below'}")
        print(report, end="")
        findings += result.returncode != 0 or "warning:" in report or "error:" in report
    print(f"check-c: {findings} of {len(LAUNCHER_SOURCES)} sources with findings")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
