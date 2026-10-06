"""Print objects of built-in extension modules that libpython.a does not already contain.

Built-in = modules registered in PBS's inittab source (build/Modules/config.c).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

pbs, libpy, ar = sys.argv[1:4]
members = set(subprocess.run([ar, "t", libpy], check=True, capture_output=True, text=True).stdout.split())
info = json.loads(Path(pbs, "PYTHON.json").read_text())["build_info"]
inittab = Path(pbs, info["inittab_source"]).read_text()
builtin = set(re.findall(r'\{"([A-Za-z0-9_.]+)",\s*PyInit_', inittab))
extra = []
for name in sorted(builtin & info["extensions"].keys()):
    for obj in info["extensions"][name][0]["objs"]:
        if obj.rsplit("/", 1)[-1] not in members and obj not in extra:
            extra.append(obj)
print("\n".join(f"{pbs}/{o}" for o in extra))
