#!/usr/bin/env bash
#
# upstream-watch.sh — report a python-build-standalone release newer than the pinned one.
#
#     bash tools/upstream-watch.sh
#
# Run once a day by .github/workflows/upstream-watch.yml. The runtime is built from the
# CPython 3.14 x86-64 pgo+lto-full archive that src/fsbpyr/runtime/pins.py pins. When a
# newer PBS release has a cpython-3.14.*-x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst, this
# opens an issue labelled runtime-update, or updates the open one. The issue names the newest
# such archive, quotes the notes of every release since the pinned one and compares the LLVM
# version of both archives' objects (PYTHON.json's object_file_format): from a new LLVM major
# on, `fsbpyr runtime build` needs clang and lld of that major or newer (pbs.py llvm_major).
#
# Needs gh, authenticated for a repository whose issues it may write (GH_TOKEN and GH_REPO
# in the workflow), and tar with zstd. When the archives differ it reads both (about 110 MB
# each). Exit status: 0 when the pin is the newest or once the issue is written, 1 on an error.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PINS="$ROOT/src/fsbpyr/runtime/pins.py"
PBS="astral-sh/python-build-standalone"
TRIPLE="x86_64-unknown-linux-gnu"
ARCHIVE="^cpython-3\.14\.[0-9]+\+[0-9]+-$TRIPLE-pgo\+lto-full\.tar\.zst\$"
LABEL="runtime-update"
RELEASES=30  # the newest releases searched; PBS publishes a few a month

say() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

# llvm TAG ASSET: the LLVM version of the archive's bitcode ("22.1.3" for llvm-bitcode:22.1.3).
llvm() {
    local format
    format="$(gh release download "$1" --repo "$PBS" --pattern "$2" --output - \
        | tar --zstd -xOf - python/PYTHON.json \
        | sed -n 's/.*"object_file_format": *"\([^"]*\)".*/\1/p')" \
        || die "cannot read python/PYTHON.json from $1's $2"
    case "$format" in
        llvm-bitcode:?*) echo "${format#llvm-bitcode:}" ;;
        *) die "$1's $2 gives object_file_format '$format', not LLVM bitcode" ;;
    esac
}

# The pin: version and release of pins.py's PbsPin(...) entry for CPython 3.14 on x86-64.
read -r version release < <(awk -F'"' -v triple="$TRIPLE" '
    /^ *PbsPin\($/ { split("", f); pin = 1 }
    pin && NF == 3 { key = $1; gsub(/[ =]/, "", key); f[key] = $2 }
    pin && /^ *\),?$/ {
        pin = 0
        if (f["python"] == "3.14" && f["triple"] == triple && f["flavor"] == "pgo+lto-full")
            print f["version"], f["release"]
    }
' "$PINS") || true
[ -n "${release:-}" ] || die "no CPython 3.14 $TRIPLE pgo+lto-full pin in $PINS"
pinned="cpython-$version+$release-$TRIPLE-pgo+lto-full.tar.zst"
say "Pinned: $pinned"

# The releases newer than the pinned one, newest first, from the newest with such an archive.
tags="$(gh release list --repo "$PBS" --exclude-drafts --exclude-pre-releases \
            --limit "$RELEASES" --json tagName --jq '.[].tagName')"
newest="" newest_tag="" newer=() reached=""
for tag in $tags; do
    if [ "$tag" = "$release" ]; then reached=1; break; fi
    if [ -z "$newest" ]; then
        assets="$(gh release view "$tag" --repo "$PBS" --json assets --jq '.assets[].name')"
        newest="$(grep -E -m1 "$ARCHIVE" <<<"$assets" || true)"
        [ -n "$newest" ] || continue
        newest_tag="$tag"
    fi
    newer+=("$tag")
done
if [ -z "$newest" ]; then
    [ -n "$reached" ] || die "none of the newest $RELEASES releases of $PBS has a" \
        "cpython-3.14 $TRIPLE pgo+lto-full archive, and $release is not among them"
    say "Up to date: $pinned is the newest"
    exit 0
fi
say "Newer: $newest"

old="$(llvm "$release" "$pinned")"
new="$(llvm "$newest_tag" "$newest")"
title="Runtime update: python-build-standalone $newest_tag"
if [ "${old%%.*}" != "${new%%.*}" ]; then
    title+=", LLVM $old -> $new"
    change="**LLVM $old -> $new.** A new LLVM major: \`fsbpyr runtime build\` links these"
    change+=" objects only with clang and lld ${new%%.*} or newer."
elif [ "$old" != "$new" ]; then
    change="LLVM $old -> $new, the same major."
else
    change="LLVM $new, as before."
fi

url="https://github.com/$PBS/releases/tag"
body="python-build-standalone [$newest_tag]($url/$newest_tag) has \`$newest\`."
body+=" The runtime is built from \`$pinned\` (src/fsbpyr/runtime/pins.py).

$change

Release notes since $release:"
for tag in "${newer[@]}"; do
    notes="$(gh release view "$tag" --repo "$PBS" --json body --jq '.body')"
    body+="

### [$tag]($url/$tag)

$(printf '%s\n' "$notes" | tr -d '\r' | sed 's/^/> /')"
done

number="$(gh issue list --label "$LABEL" --state open --limit 1 --json number \
              --jq '.[0].number // empty')"
if [ -n "$number" ]; then
    gh issue edit "$number" --title "$title" --body "$body" >/dev/null
    say "Updated issue #$number: $title"
else
    gh label create "$LABEL" --force --color fbca04 \
        --description "A python-build-standalone release newer than the pinned one" >/dev/null
    gh issue create --title "$title" --body "$body" --label "$LABEL"
    say "Opened: $title"
fi
