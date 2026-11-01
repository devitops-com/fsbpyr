#!/usr/bin/env bash
#
# mirror.sh — GitLab follows GitHub, and merged branches go (make mirror; make release ends with it).
#
#     bash tools/release/mirror.sh
#
# Run it on main, after looking at what you pulled. What reaches `origin` is this checkout's main,
# once GitHub is seen to hold the same, so that a change made behind the admin's back on one remote
# reaches no second one. GitLab's main only moves forward, and release tags (v* and el8-v*, the
# patterns of the rulesets) are added and never moved. Whatever disagrees stops it before the first
# push, and main and the tags go in one atomic push.
#
# Then a branch goes from either remote when main holds every commit of it, as it does after a
# merge. A branch with a commit main lacks stays, and so does one of commits this checkout has
# never seen. Running it again changes nothing. Exit status: 0 done, 1 stopped, 2 usage.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

GITHUB=hub
GITLAB=origin
MAIN=main

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ $# -eq 0 ] || { echo "usage: mirror.sh" >&2; exit 2; }

[ "$(git symbolic-ref --quiet --short HEAD || true)" = "$MAIN" ] || die "run this on $MAIN"
head=$(git rev-parse HEAD)

# Read before anything else: in a pipe, a remote that cannot be read would look like an empty one.
github=$(git ls-remote --heads --tags "$GITHUB")
gitlab=$(git ls-remote --heads --tags "$GITLAB")

# The object a ref names in a listing, by its whole name; nothing if there is no such ref.
named() { awk -v ref="$2" '$2 == ref { print $1 }' <<<"$1"; }

# Whether this main holds a commit: one this checkout has never seen, it does not.
on_main() { git merge-base --is-ancestor "$1" "$head" 2>/dev/null; }

changed=0
pushes=()
names=()

github_main=$(named "$github" "refs/heads/$MAIN")
[ "$github_main" = "$head" ] || die "GitHub's main is ${github_main:0:7}, this checkout's ${head:0:7}:" \
    "look at it, pull it and run this again; nothing was pushed"

gitlab_main=$(named "$gitlab" "refs/heads/$MAIN")
if [ "$gitlab_main" != "$head" ]; then
    [ -z "$gitlab_main" ] || on_main "$gitlab_main" \
        || die "GitLab's main ${gitlab_main:0:7} has commits this one lacks; nothing was pushed"
    pushes+=("$head:refs/heads/$MAIN")
    names+=("$MAIN")
fi

while read -r object ref; do
    case "$ref" in
        *'^{}') continue ;;
        refs/tags/v* | refs/tags/el8-v*) ;;
        *) continue ;;
    esac
    tag=${ref#refs/tags/}
    theirs=$(named "$gitlab" "$ref")
    [ "$theirs" != "$object" ] || continue
    [ -z "$theirs" ] || die "GitLab has $tag at ${theirs:0:7}, GitHub at ${object:0:7}:" \
        "a tag never moves; nothing was pushed"
    mine=$(git rev-parse --quiet --verify "$ref" || true)
    [ -z "$mine" ] || [ "$mine" = "$object" ] || die "this checkout has $tag at ${mine:0:7}," \
        "GitHub at ${object:0:7}: a tag never moves; nothing was pushed"
    git fetch --quiet --no-tags "$GITHUB" "$ref" </dev/null  # ssh would read the listing below
    on_main "$object" || die "$tag is not on $MAIN; nothing was pushed"
    pushes+=("$object:$ref")
    names+=("$tag")
done <<<"$github"

if [ ${#pushes[@]} -gt 0 ]; then
    git push --quiet --atomic "$GITLAB" "${pushes[@]}" </dev/null
    echo "GitLab got: ${names[*]}"
    changed=1
fi

# delete_merged REMOTE LISTING: the branches in LISTING that main holds go from REMOTE.
delete_merged() {
    local remote=$1 object ref merged=()
    while read -r object ref; do
        case "$ref" in
            "refs/heads/$MAIN") continue ;;
            refs/heads/*) ;;
            *) continue ;;
        esac
        if on_main "$object"; then merged+=("${ref#refs/heads/}"); fi
    done <<<"$2"
    [ ${#merged[@]} -gt 0 ] || return 0
    git push --quiet "$remote" --delete "${merged[@]}" </dev/null
    echo "deleted from $remote: ${merged[*]}"
    changed=1
}

delete_merged "$GITHUB" "$github"
delete_merged "$GITLAB" "$gitlab"

[ "$changed" = 1 ] || echo "mirror: nothing to do"
