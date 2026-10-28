#!/bin/bash
# Build the wiki as it is published, from a repository tree:
#   build.sh REPO_ROOT OUT_DIR VERSION SOURCE DATE SOURCE_DATE_EPOCH [REF]
# REPO_ROOT is the tree (a checkout, or a commit extracted with git archive); its own
# tools/wiki/ builds it. OUT_DIR is made anew and gets the published pages (site.py), the
# drawn diagrams in images/ (render.sh) and the two PDFs (Makefile). VERSION is the release the
# pages describe; SOURCE, DATE and SOURCE_DATE_EPOCH name the commit on the PDFs' title pages.
# REF is what the pages' repository links point at (site.py; default the tag vVERSION).
# The pages are checked first (lint.py): a page with a broken link is not built.
set -euo pipefail
root=$1 out=$2 version=$3 source=$4 date=$5 epoch=$6 ref=${7:-}
tools="$root/tools/wiki"
python3 -I "$tools/lint.py" "$root"
rm -rf "$out"
python3 -I "$tools/site.py" "$root" "$out" "$version" ${ref:+"$ref"}
sh "$tools/render.sh" "$root" "$out/images"
make -s -C "$out" -f "$tools/Makefile" pdf VERSION="$version" SOURCE="$source" DATE="$date" \
    SOURCE_DATE_EPOCH="$epoch"
rm -rf "$out/.pdf-build"
