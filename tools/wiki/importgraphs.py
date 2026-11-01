"""Draw fsbpyr's import graphs as Graphviz sources from modgraph.json.

Usage: python -I importgraphs.py MODGRAPH_JSON OUT_DIR
Writes arch-subsystems.dot, arch-modules-top.dot, arch-modules-payload.dot,
arch-modules-runtime.dot and arch-modules-boot.dot into OUT_DIR.
"""

import json
import sys
from collections import Counter
from pathlib import Path

HEADER = (
    "digraph G {\n"
    '  graph [fontname="Helvetica" fontsize=11 rankdir=LR bgcolor="white" pad=0.3 nodesep=0.35'
    " ranksep=0.5 compound=true newrank=true];\n"
    '  node [fontname="Helvetica" fontsize=10 shape=box style="rounded,filled"'
    ' fillcolor="#eef3fb" color="#4a6fa5"];\n'
    '  edge [fontname="Helvetica" fontsize=9 color="#555555" arrowsize=0.6];\n'
)
BUILD = ('fillcolor="#eef3fb" color="#4a6fa5"')
RUN = ('fillcolor="#fdf1e3" color="#c77d2a"')
OTHER = ('fillcolor="#f2f2f2" color="#999999" fontcolor="#555555"')

# Subsystems of the top-level modules, as the Developer Guide groups them.
GROUPS = {
    "Command line and dispatch": ["__main__", "cli", "dispatch"],
    "Build driver": ["build"],
    "Project, settings, Python version": ["project", "config", "pyversion", "poetry1"],
    "Commands, scripts and tools": ["entry", "links", "loose", "legacy", "tools"],
    "Locks of other tools, inferred dependencies": ["foreign", "pipenv", "venvdeps"],
    "Static import check": ["importcheck", "syspath"],
    "Staging with uv": ["staging"],
    "What the app contains and needs": ["closure", "classify", "scan", "natives", "elf", "system"],
    "Compiling to bytecode": ["compiler", "bytecode"],
    "Recording provenance": ["manifest", "vcs"],
    "Assembling": ["assemble"],
    "Reading executables": ["inspection", "sizes", "licensing"],
    "Tool upkeep": ["selfupdate", "manpage"],
    "Signatures": ["signing"],
    "Shared helpers": ["fsbpyr", "targets", "compat", "text"],
}
SUBPACKAGES = {"payload": "payload/", "runtime": "runtime/", "boot": "boot/ (in the executable)"}
FAINT = {"Build driver", "Shared helpers"}
# The graphs main() writes, by file name without .dot (lint.py's IMPORT_GRAPHS names them too).
OUTPUTS = ("arch-subsystems", "arch-modules-top", "arch-modules-payload", "arch-modules-runtime",
           "arch-modules-boot")


def short(name: str) -> str:
    return "fsbpyr" if name == "fsbpyr" else name.removeprefix("fsbpyr.")


def subsystem(name: str) -> str:
    s = short(name)
    head = s.split(".")[0]
    if head in SUBPACKAGES:
        return head
    for group, members in GROUPS.items():
        if s in members:
            return group
    raise KeyError(s)


def real(modules: dict) -> dict:
    """Modules with code (package __init__ files of one line carry none), without cli's lazy
    imports: cli imports each command's modules inside the command, and those edges would
    cross every other one."""
    kept = {k: dict(v) for k, v in modules.items() if not (k != "fsbpyr" and v["lines"] <= 1)}
    kept["fsbpyr.cli"]["imports"] = {
        t: kind for t, kind in kept["fsbpyr.cli"]["imports"].items() if kind != "lazy"
    }
    return kept


def node_id(name: str) -> str:
    return '"' + short(name) + '"'


def edge_style(kind: str) -> str:
    return ' [style=dashed color="#999999"]' if kind == "lazy" else ""


def subsystems_graph(modules: dict) -> str:
    counts: Counter = Counter()
    for name, info in modules.items():
        for target in info["imports"]:
            a, b = subsystem(name), subsystem(target)
            if a != b and target in modules:
                counts[(a, b)] += 1
    out = [HEADER, '  graph [rankdir=TB ranksep=0.7 nodesep=0.5];\n']
    for group in [*GROUPS, *SUBPACKAGES]:
        label = SUBPACKAGES.get(group, group)
        style = RUN if group == "boot" else BUILD
        out.append(f'  "{group}" [label="{label}" {style}];\n')
    for (a, b), n in sorted(counts.items()):
        # the build driver imports nearly every group, and every group the shared helpers:
        # drawn faint, so that the edges between the other groups can be followed
        faint = ' color="#c8c8c8" fontcolor="#999999"' if FAINT & {a, b} else ""
        width = min(0.8 + n * 0.25, 3)
        out.append(f'  "{a}" -> "{b}" [label="{n}" penwidth={width:.2f}{faint}];\n')
    out.append("}\n")
    return "".join(out)


def top_graph(modules: dict) -> str:
    # left to right, without newrank: with it, dot lets the clusters of this graph overlap
    out = [HEADER.replace(" newrank=true", ""), '  graph [rankdir=LR ranksep=0.8];\n']
    for i, (group, members) in enumerate(GROUPS.items()):
        out.append(f'  subgraph cluster_{i} {{ label="{group}"; style="rounded";'
                   ' color="#bbbbbb"; fontsize=10;\n')
        for m in members:
            out.append(f'    "{m}";\n')
        out.append("  }\n")
    seen = set()
    for name, info in modules.items():
        s = short(name)
        if s.split(".")[0] in SUBPACKAGES:
            continue
        for target, kind in sorted(info["imports"].items()):
            if target not in modules:
                continue
            t = short(target)
            t = t.split(".")[0] if t.split(".")[0] in SUBPACKAGES else t
            if (s, t) in seen or s == t:
                continue
            seen.add((s, t))
            style = edge_style(kind) or (' [color="#c8c8c8"]' if s == "build" else "")
            out.append(f'  "{s}" -> "{t}"{style};\n')
    for sub, label in SUBPACKAGES.items():
        if any(t == sub for _, t in seen):
            style = RUN if sub == "boot" else BUILD
            out.append(f'  "{sub}" [label="{label}" shape=folder {style}];\n')
    out.append("}\n")
    return "".join(out)


def package_graph(modules: dict, package: str, run_time: bool) -> str:
    prefix = f"fsbpyr.{package}."
    inside = {k: v for k, v in modules.items() if k.startswith(prefix)}
    out = [HEADER]
    style = RUN if run_time else BUILD
    for name in inside:
        label = short(name).removeprefix(package + ".")
        out.append(f'  {node_id(name)} [label="{label}" {style}];\n')
    outside = set()
    lines = []
    for name, info in inside.items():
        for target, kind in sorted(info["imports"].items()):
            if target not in modules:
                continue
            if target.startswith(prefix):
                lines.append(f'  {node_id(name)} -> {node_id(target)}{edge_style(kind)};\n')
            else:
                outside.add(target)
                lines.append(f'  {node_id(name)} -> {node_id(target)}{edge_style(kind)};\n')
    users = set()
    for name, info in modules.items():
        if name.startswith(prefix):
            continue
        for target, kind in info["imports"].items():
            if target.startswith(prefix):
                users.add(name)
                lines.append(f'  {node_id(name)} -> {node_id(target)}{edge_style(kind)};\n')
    for name in sorted(outside | users):
        out.append(f'  {node_id(name)} [{OTHER}];\n')
    out.extend(sorted(set(lines)))
    out.append("}\n")
    return "".join(out)


def main() -> None:
    modules = real(json.loads(Path(sys.argv[1]).read_text()))
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    (out / "arch-subsystems.dot").write_text(subsystems_graph(modules))
    (out / "arch-modules-top.dot").write_text(top_graph(modules))
    (out / "arch-modules-payload.dot").write_text(package_graph(modules, "payload", False))
    (out / "arch-modules-runtime.dot").write_text(package_graph(modules, "runtime", False))
    (out / "arch-modules-boot.dot").write_text(package_graph(modules, "boot", True))


if __name__ == "__main__":
    main()
