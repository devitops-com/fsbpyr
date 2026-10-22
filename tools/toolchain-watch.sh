#!/usr/bin/env bash
#
# toolchain-watch.sh — report a release toolchain pin that is no longer the newest or is old.
#
#     bash tools/toolchain-watch.sh
#
# Run once a day by .github/workflows/upstream-watch.yml. A release is built with pins that
# only move when someone moves them (REP-1 in docs/improvement-plan.md):
#   - the uv of tools/release/uv.version, against astral-sh/uv's newest release;
#   - the Debian snapshot of tools/release/apt-snapshot, flagged when older than 90 days;
#   - the debian:trixie image digest of the Containerfile and build.yml, against the digest
#     the registry serves for the tag now.
# Any of them opens an issue labelled toolchain-update, or updates the open one; it stays
# open until the pins move (closed earlier, it is opened again on the next run).
#
# Needs gh, authenticated for a repository whose issues it may write (GH_TOKEN and GH_REPO in
# the workflow), and curl. Exit status: 0 when all pins are current or once the issue is
# written, 1 on an error.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV_REPO="astral-sh/uv"
LABEL="toolchain-update"
MAX_SNAPSHOT_AGE_DAYS=90
IMAGE_RE='debian:trixie@sha256:[0-9a-f]{64}'
REGISTRY="https://registry-1.docker.io/v2/library/debian/manifests/trixie"
TOKEN_URL="https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/debian:pull"
MANIFESTS="application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json"

say() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

problems=()

# uv
read -r pinned_uv _ < "$ROOT/tools/release/uv.version" || die "tools/release/uv.version is empty"
latest_uv="$(gh release view --repo "$UV_REPO" --json tagName --jq .tagName)" \
    || die "cannot read the newest $UV_REPO release"
say "uv: pinned $pinned_uv, newest $latest_uv"
if [ "$pinned_uv" != "$latest_uv" ]; then
    problems+=("**uv** $pinned_uv (tools/release/uv.version) is not the newest release, $latest_uv.")
fi

# snapshot
snapshot="$(tr -d '[:space:]' < "$ROOT/tools/release/apt-snapshot")"
[[ "$snapshot" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || die "tools/release/apt-snapshot is not a timestamp: $snapshot"
age=$(( ( $(date -u +%s) - $(date -u -d "${snapshot:0:8}" +%s) ) / 86400 ))
say "snapshot: $snapshot, $age days old"
if [ "$age" -gt "$MAX_SNAPSHOT_AGE_DAYS" ]; then
    problems+=("**Debian snapshot** $snapshot (tools/release/apt-snapshot) is $age days old; the limit is $MAX_SNAPSHOT_AGE_DAYS.")
fi

# image
pinned_image="$(grep -Eho "$IMAGE_RE" "$ROOT/Containerfile" | head -n1 | cut -d@ -f2)"
[ -n "$pinned_image" ] || die "no debian:trixie@sha256 pin in the Containerfile"
token="$(curl -fsS "$TOKEN_URL" | sed -n 's/.*"token": *"\([^"]*\)".*/\1/p')"
[ -n "$token" ] || die "no registry token"
current_image="$(curl -fsSI -H "Authorization: Bearer $token" -H "Accept: $MANIFESTS" "$REGISTRY" \
    | tr -d '\r' | awk 'tolower($1) == "docker-content-digest:" { print $2 }')"
[ -n "$current_image" ] || die "the registry gave no digest for debian:trixie"
say "image: pinned $pinned_image, registry $current_image"
if [ "$pinned_image" != "$current_image" ]; then
    problems+=("**debian:trixie** is pinned to \`$pinned_image\` (Containerfile, build.yml); the registry serves \`$current_image\`.")
fi

if [ "${#problems[@]}" -eq 0 ]; then
    say "Up to date"
    exit 0
fi

title="Toolchain update: ${#problems[@]} release pin(s) to move"
body="The release toolchain is pinned (REP-1). To move the pins, edit tools/release/uv.version
and the Containerfile's \`UV_VERSION\` and \`UV_SHA256_*\`, tools/release/apt-snapshot, and the
\`debian:trixie@sha256\` digest in the Containerfile, .github/workflows/build.yml and
.github/workflows/full-suite.yml, then rebuild and compare:
"
for problem in "${problems[@]}"; do
    body+="
- $problem"
done

number="$(gh issue list --label "$LABEL" --state open --limit 1 --json number \
              --jq '.[0].number // empty')"
if [ -n "$number" ]; then
    gh issue edit "$number" --title "$title" --body "$body" >/dev/null
    say "Updated issue #$number: $title"
else
    gh label create "$LABEL" --force --color fbca04 \
        --description "A release toolchain pin that is old or no longer the newest" >/dev/null
    gh issue create --title "$title" --body "$body" --label "$LABEL"
    say "Opened: $title"
fi
