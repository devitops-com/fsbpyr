#!/usr/bin/env bash
#
# make-install.sh — fill in install.sh.in from a directory of release assets.
#
#     bash tools/release/make-install.sh VERSION ASSET_DIR OUTPUT [INSPECTOR]
#
# VERSION is the release's (`0.21.0`, no leading v). ASSET_DIR holds the release's files; the
# installer gets, for every target's executable `fsbpyr-<target>` and its `.xz`, the SHA-256,
# and for an executable on glibc the glibc it records (INSPECTOR inspect --json: "glibc"),
# which the installer checks against the host's. INSPECTOR is an fsbpyr that runs here, by
# default ASSET_DIR's fsbpyr-x86_64-linux-gnu (inspect only reads a file, so it can read the
# other targets' too). The installer's targets are those with an executable in ASSET_DIR.
#
# Used by the release workflow (.github/workflows/release.yml: the publish job), which attests
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
INSPECTOR="${4:-$ASSET_DIR/fsbpyr-x86_64-linux-gnu}"

recorded_glibc() { # executable
    "$INSPECTOR" inspect --json "$1" | sed -n 's/^  "glibc": "\([0-9][0-9.]*\)",\{0,1\}$/\1/p'
}

targets=()
assets=""
for target in $ALL_TARGETS; do
    exe="$ASSET_DIR/fsbpyr-$target"
    [ -f "$exe" ] || continue
    glibc=-
    case "$target" in
        *-gnu)
            glibc="$(recorded_glibc "$exe")"
            [ -n "$glibc" ] || { echo "ERROR: $exe records no glibc" >&2; exit 1; }
            ;;
    esac
    targets+=("$target")
    assets+="fsbpyr-$target $(sha256sum "$exe" | cut -d' ' -f1) $glibc"$'\n'
    if [ -f "$exe.xz" ]; then
        assets+="fsbpyr-$target.xz $(sha256sum "$exe.xz" | cut -d' ' -f1) -"$'\n'
    fi
done
[ "${#targets[@]}" -gt 0 ] \
    || echo "warning: no fsbpyr-<target> executable in $ASSET_DIR; the installer will have no target" >&2

# Written next to OUTPUT and renamed, so a failed run leaves no half-made installer.
partial="$OUTPUT.partial.$$"
trap 'rm -f "$partial"' EXIT
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        "APP_VERSION='@VERSION@'") printf "APP_VERSION='%s'\n" "$VERSION" ;;
        "TARGETS='@TARGETS@'") printf "TARGETS='%s'\n" "${targets[*]:-}" ;;
        "@ASSETS@") printf '%s' "$assets" ;;
        *) printf '%s\n' "$line" ;;
    esac
done < "$TEMPLATE" > "$partial"
# Every placeholder must be gone, or the template changed under this script.
if grep -q '@VERSION@\|@TARGETS@\|@ASSETS@' "$partial"; then
    echo "ERROR: $TEMPLATE has a placeholder this script does not fill" >&2
    exit 1
fi
chmod 755 "$partial"
mv -f "$partial" "$OUTPUT"
