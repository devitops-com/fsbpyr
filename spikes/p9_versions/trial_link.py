"""Trial link of the launcher against a PBS 3.12 or 3.13 distribution (Phase 9 spike).

usage: uv run python -I spikes/p9_versions/trial_link.py PBS_ROOT OUTPUT [EXTRA_STATIC_LIB...]

PBS_ROOT is the extracted ``python/`` directory (the one holding PYTHON.json); EXTRA_STATIC_LIBs
are paths relative to it, e.g. ``build/lib/libzstd.a``. Uses the build's own link inputs and
flags, so it needs a clang/lld of the distribution's LLVM major on the host.
"""

import dataclasses
import sys
import time
from pathlib import Path

from fsbpyr.runtime import ar, stub
from fsbpyr.runtime.pbs import PbsDist, link_inputs
from fsbpyr.runtime.toolchain import find_toolchain


def main() -> int:
    root, out, extra = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
    dist = PbsDist.load(root)
    toolchain = find_toolchain(dist.llvm_major)
    members = set(ar.members(dist.root / dist.info["build_info"]["core"]["static_lib"]))
    inputs = link_inputs(dist, members)
    print(dist.python, dist.version, "llvm", dist.llvm_major)
    print("extra objects:", [p.name for p in inputs.extra_objects])
    print("static libs:", [p.name for p in inputs.static_libs])
    print("system libs:", inputs.system_libs)
    inputs = dataclasses.replace(inputs, static_libs=(*inputs.static_libs, *(root / e for e in extra)))
    stub.link_inputs = lambda _dist, _members: inputs  # the build's inputs plus the extras
    start = time.monotonic()
    try:
        stub.build_stub(dist, toolchain, out)
    except stub.StubBuildError as exc:
        print(f"FAIL after {time.monotonic() - start:.0f} s\n{exc}")
        return 1
    print(f"OK {out.stat().st_size} bytes in {time.monotonic() - start:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
