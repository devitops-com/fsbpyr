#!/usr/bin/env bash
#
# make-install.sh — fill in install.sh.in from a directory of release assets.
#
#     bash tools/release/make-install.sh VERSION ASSET_DIR OUTPUT [INSPECTOR]
#
# VERSION is the release's (`0.21.0`, no leading v). ASSET_DIR holds the release's files; the
# installer gets, for every executable `fsbpyr-3.X-<target>` and its `.xz`, the SHA-256, and
# for an executable on glibc the glibc it records (INSPECTOR inspect --json: "glibc"), which
# the installer checks against the host's. INSPECTOR is an fsbpyr that runs here, by default
# ASSET_DIR's newest Python's fsbpyr-3.X-x86_64-linux-gnu (inspect only reads a file, so it can
# read the other targets' too). The installer's targets and Pythons are those with an
# executable in ASSET_DIR; it installs the Python of the checkout's .python-version unless told
# another (owner, 2026-10-23: 3.14 stays the default while 3.15 is new), which must be among
# them.
#
# Used by the release workflow (.github/workflows/release.yml: the prepare job), which attests
# OUTPUT and lists it in SHA256SUMS. Exit status: 0 with OUTPUT written, 1 if an asset is
# missing or unreadable, 2 on bad usage.
set -euo pipefail

usage() { echo "usage: make-install.sh VERSION ASSET_DIR OUTPUT [INSPECTOR]" >&2; exit 2; }
[ $# -ge 3 ] && [ $# -le 4 ] || usage

VERSION="$1"
ASSET_DIR="$2"
OUTPUT="$3"
TEMPLATE="$(dirname "${BASH_SOURCE[0]}")/install.sh.in"
# The targets of Phase 9 in the order the installer names them: Linux glibc, Linux musl, macOS.
ALL_TARGETS="x86_64-linux-gnu aarch64-linux-gnu x86_64-linux-musl aarch64-linux-musl aarch64-macos"

case "$VERSION" in
    "" | v* | *[!0-9A-Za-z.+-]*) echo "ERROR: VERSION is X.Y.Z, without a v: $VERSION" >&2; exit 2 ;;
esac
[ -d "$ASSET_DIR" ] || { echo "ERROR: $ASSET_DIR is not a directory" >&2; exit 1; }

# The Pythons with an executable for one of the targets, oldest first.
pythons="$(for exe in "$ASSET_DIR"/fsbpyr-3.*-*; do
    name="${exe##*/}"
    for target in $ALL_TARGETS; do
        if [[ "$name" =~ ^fsbpyr-(3\.[0-9]+)-$target$ ]]; then echo "${BASH_REMATCH[1]}"; fi
    done
done | sort -u -t. -k1,1n -k2,2n | tr '\n' ' ')"
pythons="${pythons% }"
default_python="$(cut -d. -f1,2 "$(dirname "${BASH_SOURCE[0]}")/../../.python-version")"
[ -z "$pythons" ] || [[ " $pythons " == *" $default_python "* ]] || {
    echo "ERROR: no fsbpyr-$default_python-<target> executable in $ASSET_DIR (.python-version's Python)" >&2
    exit 1
}
INSPECTOR="${4:-$ASSET_DIR/fsbpyr-${pythons##* }-x86_64-linux-gnu}"

recorded_glibc() { # executable
    "$INSPECTOR" inspect --json "$1" | sed -n 's/^  "glibc": "\([0-9][0-9.]*\)",\{0,1\}$/\1/p'
}

targets=()
assets=""
for target in $ALL_TARGETS; do
    for python in $pythons; do
        asset="fsbpyr-$python-$target"
        exe="$ASSET_DIR/$asset"
        [ -f "$exe" ] || continue
        glibc=-
        case "$target" in
            *-gnu)
                glibc="$(recorded_glibc "$exe")"
                [ -n "$glibc" ] || { echo "ERROR: $exe records no glibc" >&2; exit 1; }
                ;;
        esac
        [[ " ${targets[*]:-} " == *" $target "* ]] || targets+=("$target")
        assets+="$asset $(sha256sum "$exe" | cut -d' ' -f1) $glibc"$'\n'
        if [ -f "$exe.xz" ]; then
            assets+="$asset.xz $(sha256sum "$exe.xz" | cut -d' ' -f1) -"$'\n'
        fi
    done
done
[ "${#targets[@]}" -gt 0 ] \
    || echo "warning: no fsbpyr-3.X-<target> executable in $ASSET_DIR; the installer will have no target" >&2

# Written next to OUTPUT and renamed, so a failed run leaves no half-made installer.
partial="$OUTPUT.partial.$$"
trap 'rm -f "$partial"' EXIT
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        "APP_VERSION='@VERSION@'") printf "APP_VERSION='%s'\n" "$VERSION" ;;
        "TARGETS='@TARGETS@'") printf "TARGETS='%s'\n" "${targets[*]:-}" ;;
        "PYTHONS='@PYTHONS@'") printf "PYTHONS='%s'\n" "$pythons" ;;
        "DEFAULT_PYTHON='@DEFAULT_PYTHON@'") printf "DEFAULT_PYTHON='%s'\n" "$default_python" ;;
        "@ASSETS@") printf '%s' "$assets" ;;
        *) printf '%s\n' "$line" ;;
    esac
done < "$TEMPLATE" > "$partial"
# Every placeholder must be gone, or the template changed under this script.
if grep -q '@VERSION@\|@TARGETS@\|@PYTHONS@\|@DEFAULT_PYTHON@\|@ASSETS@' "$partial"; then
    echo "ERROR: $TEMPLATE has a placeholder this script does not fill" >&2
    exit 1
fi
chmod 755 "$partial"
mv -f "$partial" "$OUTPUT"
