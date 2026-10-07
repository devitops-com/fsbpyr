"""``FSBPYR_TRACE=1``: at exit, one JSON line on stderr describing what the run took from the
payload: modules loaded per distribution ("" for modules without one), native files extracted
into the cache, and the payload read counters."""

import json
import sys

import _fsbpyr

_INTERNAL = ("_fsbpyr_boot", "_fsbpyr_rt")


def summary() -> dict[str, object]:
    names = {index: name for index, name, _, _ in _fsbpyr.dists()}
    modules: dict[str, list[str]] = {}
    for name in sorted(sys.modules):
        record = _fsbpyr.find_module(name)
        if record is not None and name.partition(".")[0] not in _INTERNAL:
            modules.setdefault(names.get(record[6], ""), []).append(name)
    native = sys.modules.get("_fsbpyr_rt.native")
    extracted = list(getattr(native, "EXTRACTED", ()))
    return {"modules": modules, "extracted": extracted, **_fsbpyr.stats()}


def report() -> None:
    print("fsbpyr-trace " + json.dumps(summary(), sort_keys=True), file=sys.stderr)
