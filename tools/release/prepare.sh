#!/usr/bin/env bash
#
# prepare.sh — make the release commit, as the last commit of a branch before its pull request.
#
#     bash tools/release/prepare.sh [X.Y.Z]        (make release-prep [VERSION=X.Y.Z])
#
# With X.Y.Z (digits, no leading zeros) the version is set to it in the three files that hold
# it (version-files.sh); it may equal the current version but not be lower (compared as
# numbers). Without X.Y.Z the current version is released. The CHANGELOG's [Unreleased]
# section becomes `## [X.Y.Z] - DATE`, under a fresh empty [Unreleased], and all of it is ONE
# commit "chore: release vX.Y.Z" on the current branch. Nothing is pushed.
#
# The pull request merges the branch; that push to main runs full-suite.yml, which builds and
# checks exactly this source, the release build included. `make release` (release.sh) then
# tags that commit, and the tag's release.yml run builds it again and publishes it.
#
# Guards (all must pass before anything changes): the three versions agree, X.Y.Z well-formed
# and not lower, on a branch other than main, a clean working tree (untracked files and
# assume-unchanged / skip-worktree entries included), the tag exists neither here nor on hub,
# and a non-empty [Unreleased] section.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck source=version-files.sh
. tools/release/version-files.sh

BRANCH="main"
CHANGELOG="CHANGELOG.md"

[ $# -le 1 ] || { echo "usage: prepare.sh [X.Y.Z]" >&2; exit 2; }

CURRENT_VERSION="$(current_version)"

# --- The version to release ---------------------------------------------------
# version_lt A B: A < B, both X.Y.Z, compared part by part as numbers (1.9.10 > 1.9.2).
version_lt() {
    local -a a b
    local i
    IFS=. read -r -a a <<<"$1"
    IFS=. read -r -a b <<<"$2"
    for i in 0 1 2; do
        [ "${a[i]}" -eq "${b[i]}" ] || { [ "${a[i]}" -lt "${b[i]}" ]; return; }
    done
    return 1
}
VERSION="$CURRENT_VERSION"
if [ $# -eq 1 ]; then
    [[ "$1" =~ $XYZ ]] \
        || { echo "ERROR: '$1' is not a version X.Y.Z (digits, no leading zeros)" >&2; exit 1; }
    [[ "$CURRENT_VERSION" =~ $XYZ ]] \
        || { echo "ERROR: the current version $CURRENT_VERSION is not X.Y.Z" >&2; exit 1; }
    if version_lt "$1" "$CURRENT_VERSION"; then
        echo "ERROR: version $1 is lower than the current version $CURRENT_VERSION" >&2; exit 1
    fi
    VERSION="$1"
fi
TAG="v$VERSION"

# --- cleanup / abort safety --------------------------------------------------
# Before the release commit lands, any failure should leave the tree pristine:
# revert the version bump and the CHANGELOG promotion, remove the awk-rewrite temp files.
PREPARED=()  # the files the release commit is to carry, once rewritten
COMMITTED=0
cleanup() {
    rm -f "$CHANGELOG.tmp" "${VERSION_FILES[@]/%/.tmp}"
    if [ "${#PREPARED[@]}" -gt 0 ] && [ "$COMMITTED" != "1" ]; then
        # Restore from HEAD (not the index) so the rewrites are reverted even
        # after they were `git add`ed; `git checkout HEAD --` also unstages them.
        git checkout HEAD -- "${PREPARED[@]}" 2>/dev/null || true
        echo "==> Reverted ${PREPARED[*]} (release commit aborted)" >&2
    fi
    return 0
}
trap cleanup EXIT

# --- 1. Guards ---------------------------------------------------------------
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
case "$CURRENT_BRANCH" in
    "$BRANCH")
        echo "ERROR: on '$BRANCH': make the release commit on the branch whose pull request" \
             "merges it" >&2
        exit 1 ;;
    HEAD) echo "ERROR: not on a branch (detached HEAD)" >&2; exit 1 ;;
esac

# --untracked-files=all overrides a status.showUntrackedFiles=no config: what the full suite
# tests must be exactly what the tag will point at (CI builds from the tag).
[ -z "$(git status --porcelain --untracked-files=all)" ] \
    || { echo "ERROR: working tree is not clean; commit or stash first" >&2; exit 1; }
# git status cannot see edits to files marked assume-unchanged (lower-case tag in
# `git ls-files -v`) or skip-worktree (S).
HIDDEN="$(git ls-files -v | grep -E '^([a-z]|S) ' || true)"
[ -z "$HIDDEN" ] || {
    echo "ERROR: files hidden from git status (assume-unchanged / skip-worktree):" >&2
    printf '%s\n' "$HIDDEN" | cut -c3- | sed 's/^/  /' >&2
    echo "       clear with: git update-index --no-assume-unchanged --no-skip-worktree FILE" >&2
    exit 1
}

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    echo "ERROR: tag $TAG already exists locally" >&2; exit 1
fi
if git ls-remote --exit-code --tags hub "$TAG" >/dev/null 2>&1; then
    echo "ERROR: tag $TAG already exists on hub" >&2; exit 1
fi

# The release workflow publishes the promoted section as the release notes.
bash tools/release/release-notes.sh Unreleased "$CHANGELOG" >/dev/null \
    || { echo "ERROR: nothing to release" >&2; exit 1; }

# --- 2. Bump the version, promote the CHANGELOG -------------------------------
# The version lines read above, only the first one of each file: sub() swaps the quoted
# value alone, so a line keeps its spacing and any comment.
if [ "$VERSION" != "$CURRENT_VERSION" ]; then
    for f in "${VERSION_FILES[@]}"; do
        awk -v ver="$VERSION" "$(version_selector "$f")"' hit && !done {
                sub(/"[^"]*"/, "\"" ver "\""); done = 1
            }
            { print }
        ' "$f" > "$f.tmp"
        mv "$f.tmp" "$f"
        PREPARED+=("$f")
    done
    echo "==> Bumped ${VERSION_FILES[*]}: $CURRENT_VERSION -> $VERSION"
fi

# Keep a fresh empty '## [Unreleased]', move its content under '## [VERSION] - DATE'.
RELEASE_DATE="$(date +%F)"
awk -v ver="$VERSION" -v date="$RELEASE_DATE" '
    !done && /^## \[Unreleased\]/ {
        print "## [Unreleased]"; print ""; print "## [" ver "] - " date
        done=1; next
    }
    { print }
' "$CHANGELOG" > "$CHANGELOG.tmp"
mv "$CHANGELOG.tmp" "$CHANGELOG"
PREPARED+=("$CHANGELOG")
echo "==> Promoted $CHANGELOG: [Unreleased] -> [$VERSION] - $RELEASE_DATE"

# --- 3. Commit ----------------------------------------------------------------
git add "${PREPARED[@]}"
git commit -m "chore: release $TAG" >/dev/null
COMMITTED=1
cat <<EOF
==> Committed "chore: release $TAG" on $CURRENT_BRANCH. Next: push $CURRENT_BRANCH and merge
    its pull request; once full-suite.yml has passed for that push to $BRANCH, make release
EOF
