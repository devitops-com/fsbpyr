#!/bin/sh
# Draw the wiki's diagrams into images/ of the current directory, a checkout of the wiki
# (`make wiki-diagrams` runs it there):
#   render.sh SRC_TREE
# SRC_TREE is the extracted fsbpyr release the wiki describes (git archive vX.Y.Z | tar -x -C
# DIR), whose src/fsbpyr gives the import graphs (arch-subsystems, arch-modules-*: generated
# here each time, not kept). The other diagrams are the Graphviz sources in diagrams/ beside
# this script. Needs python3, dot (Graphviz) and rsvg-convert.
set -e
here=$(cd "$(dirname "$0")" && pwd)
src=${1:?usage: render.sh SRC_TREE}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
python3 -I "$here/modgraph.py" "$src/src" "$work/modgraph.json"
python3 -I "$here/importgraphs.py" "$work/modgraph.json" "$work"
for dot_file in "$here"/diagrams/*.dot "$work"/*.dot; do
    name=$(basename "$dot_file" .dot)
    dot -Tsvg "$dot_file" -o "images/$name.svg"
    rsvg-convert "images/$name.svg" > /dev/null
done
