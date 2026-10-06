"""Assemble an S2 spike executable: STUB + pad + payload + trailer.

Must run under the same CPython version as the stub (bytecode is version-specific).
usage: mkpayload.py STUB STDLIB_DIR BOOT_PY OUT
"""

import marshal
import os
import struct
import sys
from pathlib import Path

EXCLUDED_DIRS = {"test", "idlelib", "tkinter", "turtledemo", "ensurepip", "__pycache__",
                 "site-packages", "lib-dynload", "__phello__"}
STARTUP = ["encodings", "encodings.aliases", "encodings.utf_8", "linecache", "_spike_boot", "stat"]
ENTRY = struct.Struct("<QQII")


def module_name(rel: Path) -> tuple[str, bool]:
    parts = list(rel.with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def collect(stdlib: Path, boot: Path) -> dict[str, tuple[bytes, bool]]:
    modules = {}
    for path in sorted(stdlib.rglob("*.py")):
        rel = path.relative_to(stdlib)
        if any(p in EXCLUDED_DIRS or p.startswith("config-") for p in rel.parts[:-1]):
            continue
        name, is_package = module_name(rel)
        source = path.read_bytes()
        if name == "stat":  # marker proving our frozen table wins over CPython's frozen stdlib
            source += b"\n__fsbpyr_frozen__ = True\n"
        try:
            code = compile(source, str(rel), "exec", dont_inherit=True)
        except SyntaxError as exc:  # e.g. lib2to3-era test data
            print(f"skip {rel}: {exc.msg}", file=sys.stderr)
            continue
        modules[name] = (marshal.dumps(code), is_package)
    boot_code = compile(boot.read_bytes(), "_spike_boot.py", "exec", dont_inherit=True)
    modules["_spike_boot"] = (marshal.dumps(boot_code), False)
    return modules


def build_payload(modules: dict[str, tuple[bytes, bool]]) -> bytes:
    names = sorted(modules)
    header_size = 32
    frozen_off = header_size
    modules_off = frozen_off + len(STARTUP) * ENTRY.size
    data_off = modules_off + len(names) * ENTRY.size
    blob = bytearray()
    name_at, code_at = {}, {}
    for name in names:
        name_at[name] = data_off + len(blob)
        blob += name.encode() + b"\0"
    for name in names:
        blob += b"\0" * (-(data_off + len(blob)) % 16)
        code_at[name] = data_off + len(blob)
        blob += modules[name][0]

    def entry(name: str) -> bytes:
        code, is_package = modules[name]
        return ENTRY.pack(name_at[name], code_at[name], len(code), int(is_package))

    header = b"FSBS2\0\0H" + struct.pack("<IIQQ", len(STARTUP), len(names), frozen_off, modules_off)
    return header + b"".join(map(entry, STARTUP)) + b"".join(map(entry, names)) + bytes(blob)


def main() -> None:
    stub, stdlib, boot, out = map(Path, sys.argv[1:5])
    payload = build_payload(collect(stdlib, boot))
    image = bytearray(stub.read_bytes())
    image += b"\0" * (-len(image) % 4096)
    offset = len(image)
    image += payload
    image += b"FSBS2\0\0T" + struct.pack("<IIQQ", 1, 0, offset, len(payload)) + bytes(32)
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(image)
    os.chmod(tmp, 0o755)
    tmp.rename(out)
    print(f"{out}: stub={offset} payload={len(payload)} modules={payload[12:16].hex()}")


if __name__ == "__main__":
    main()
