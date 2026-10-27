#!/bin/sh
# Draw the wiki's diagrams as SVG:
#   render.sh REPO_ROOT OUT_DIR
# REPO_ROOT is the repository tree the wiki is built from. Its wiki/diagrams/*.dot are the
# hand-written Graphviz sources; its src/fsbpyr gives the import graphs (arch-subsystems,
# arch-modules-*), drawn here each time. Writes OUT_DIR/<name>.svg for each; nothing drawn
# is kept in the repository. Needs python3, dot (Graphviz) and rsvg-convert.
set -e
here=$(cd "$(dirname "$0")" && pwd)
root=${1:?usage: render.sh REPO_ROOT OUT_DIR}
out=${2:?usage: render.sh REPO_ROOT OUT_DIR}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$out"
python3 -I "$here/modgraph.py" "$root/src" "$work/modgraph.json"
python3 -I "$here/importgraphs.py" "$work/modgraph.json" "$work"
for dot_file in "$root"/wiki/diagrams/*.dot "$work"/*.dot; do
    name=$(basename "$dot_file" .dot)
    dot -Tsvg "$dot_file" -o "$out/$name.svg"
    rsvg-convert "$out/$name.svg" > /dev/null
done
