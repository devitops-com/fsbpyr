"""S2 boot module: frozen into the payload, imported by the launcher's run_command.

Serves the remaining stdlib from the payload through a meta-path finder, then runs the
scenario named by argv[1]. Only built-in and frozen modules may be imported at module level.
"""

import marshal
import sys
from _frozen_importlib import ModuleSpec

import _spike

_P = _spike.payload()
_INDEX = {}


def _u32(off):
    return int.from_bytes(_P[off : off + 4], "little")


def _u64(off):
    return int.from_bytes(_P[off : off + 8], "little")


def _load_index():
    count, table = _u32(12), _u64(24)
    for i in range(count):
        e = table + i * 24
        name_off = _u64(e)
        raw = bytes(_P[name_off : name_off + 256])
        _INDEX[raw[: raw.index(0)].decode()] = (_u64(e + 8), _u32(e + 16), _u32(e + 20))


class _Loader:
    @staticmethod
    def create_module(spec):
        return None

    @staticmethod
    def exec_module(module):
        off, size, _ = _INDEX[module.__spec__.name]
        exec(marshal.loads(_P[off : off + size]), module.__dict__)  # noqa: S102  module body from payload


class _Finder:
    @staticmethod
    def find_spec(fullname, path=None, target=None):
        hit = _INDEX.get(fullname)
        if hit is None:
            return None
        origin = "<fsbpyr>/" + fullname.replace(".", "/") + ".py"
        return ModuleSpec(fullname, _Loader, origin=origin, is_package=bool(hit[2]))


def _info():
    import encodings
    import stat

    print("argv", sys.argv)
    print("executable", sys.executable)
    print("path", sys.path, "| prefix", sys.prefix, "| flags isolated/utf8/safe_path",
          sys.flags.isolated, sys.flags.utf8_mode, sys.flags.safe_path)
    print("encodings loader", encodings.__spec__.loader.__name__, "| __path__", encodings.__path__)
    print("frozen-table precedence (stat)", getattr(stat, "__fsbpyr_frozen__", False))
    print("latin-1 codec", "é".encode("latin-1"), "| idna", "bücher".encode("idna"))
    import argparse  # stdlib served by _Finder
    import json
    print("json", json.dumps({"ok": True}), "| argparse", argparse.__spec__.origin,
          "| modules", len(sys.modules))


_SCENARIOS = {
    "noop": lambda: None,
    "noop_exit": lambda: __import__("posix")._exit(0),  # measures interpreter finalization cost
    "info": _info,
    "exit3": lambda: sys.exit(3),
    "raise": lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    "sigint": lambda: (_ for _ in ()).throw(KeyboardInterrupt),
}


def run():
    _load_index()
    sys.meta_path.append(_Finder)
    _SCENARIOS[sys.argv[1] if len(sys.argv) > 1 else "info"]()
