"""el8-stub.py PYTHON OUTDIR — the glibc 2.28 (EL8) stub of the runtime that `fsbpyr runtime build
--python PYTHON --glibc 2.28` put into the tool's cache (FSBPYR_CACHE_DIR, else the user's), checked
and written to OUTDIR under its release asset name (vcs.release_stub_asset), as el8.yml publishes
it. Run by the project's Python (`uv run python tools/release/el8-stub.py 3.14 dist`).

The checks, each of which fails the run: the stub needs no GLIBC_ symbol above 2.28, and it carries
the runtime id this checkout's fsbpyr computes for its launcher sources, pinned Python and EL8
sysroot (the id `fsbpyr build --glibc 2.28` of the same release expects of a stub, stubs.py).
Prints the asset name and the SHA-256 of the stub."""

import hashlib
import sys
from pathlib import Path

from fsbpyr.elf import glibc_requirement
from fsbpyr.runtime import pins, registry
from fsbpyr.runtime.stub import stub_identity
from fsbpyr.targets import host_target
from fsbpyr.vcs import release_stub_asset

BASELINE = "2.28"
BASELINE_TUPLE = (2, 28)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: el8-stub.py PYTHON OUTDIR", file=sys.stderr)
        return 2
    python, out = argv[0], Path(argv[1])
    try:
        stub = registry.find_runtime(python, baseline=BASELINE).stub
    except registry.RuntimeCacheError as exc:
        print(f"el8-stub.py: {exc}", file=sys.stderr)
        return 1
    pin = pins.pin_for(python, registry.host_triple())
    expected = registry.runtime_id(pin, baseline=BASELINE)
    needed = glibc_requirement(stub)
    if needed is None or needed > BASELINE_TUPLE:
        found = "no glibc version" if needed is None else "GLIBC_{}.{}".format(*needed)
        print(f"el8-stub.py: the stub needs {found}, not at most GLIBC_2.28", file=sys.stderr)
        return 1
    if (identity := stub_identity(stub)) != expected:
        print(f"el8-stub.py: the stub carries runtime id {identity}, this fsbpyr expects "
              f"{expected}", file=sys.stderr)
        return 1
    asset = release_stub_asset(python, host_target().name, BASELINE)
    out.mkdir(parents=True, exist_ok=True)
    (out / asset).write_bytes(stub)
    digest = hashlib.sha256(stub).hexdigest()
    print(f"{asset}  {digest}  glibc <= {needed[0]}.{needed[1]}  {expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
