#!/usr/bin/env bash
#
# green-run.sh — prints the URL of the successful run of a workflow, started on request on main,
# for a commit of main, or fails.
#
#     bash tools/release/green-run.sh WORKFLOW COMMIT
#
# A release needs a green full-suite.yml run (the release build and its checks, the whole
# suite) of the commit it is made from. release-assets.sh checks HEAD before it tags it; the draft
# job checks the tagged commit. A run still in
# progress is followed with follow-run.sh, as release-assets.sh follows the tag's run.
# full-suite.yml runs only on request (owner, 2026-11-13): the run that counts is one started on
# main (`gh workflow run full-suite.yml --ref main`) for that very commit. A run on another branch
# tests that branch, and pushes to main start none any more (those from before still count).
# A commit that changes only documentation needs no run of its own: the newest earlier commit of
# main that has one stands for it when nothing but documentation
# (*.md files, docs/, wiki/) changed since (CONTRIBUTING.md, "Documentation-only changes"); its
# run's URL is printed. Needs the history (git).
# Exit 1: no run for that commit, a failed one, one that fails while watched, or one with a job
# that did not succeed (skipped, or failed under continue-on-error). The reason goes to stderr,
# the URL to stdout. Needs gh, authenticated (GH_TOKEN in CI).
set -euo pipefail

[ $# -eq 2 ] || { echo "usage: green-run.sh WORKFLOW COMMIT" >&2; exit 2; }
workflow="$1" commit="$2"
BRANCH="main"
short="${commit:0:7}"

# The runs on main: those asked for, and the pushes' from before 2026-11-13.
OF_MAIN='select(.event == "workflow_dispatch" or .event == "push")'

# The newest run on main of commit $1, as "ID STATUS/CONCLUSION URL".
latest() {
    gh run list --workflow "$workflow" --branch "$BRANCH" --commit "$1" --limit 20 \
        --json databaseId,status,conclusion,url,event \
        --jq "[.[] | $OF_MAIN][0] // empty"' | "\(.databaseId) \(.status)/\(.conclusion) \(.url)"' \
        || true
}

# The newest commit of the recent runs that $commit descends from; none: status 1.
tested_ancestor() {
    local shas sha
    shas="$(gh run list --workflow "$workflow" --branch "$BRANCH" --limit 50 \
        --json headSha,event --jq ".[] | $OF_MAIN | .headSha")" || return 1
    for sha in $shas; do
        if git merge-base --is-ancestor "$sha" "$commit" 2>/dev/null; then
            echo "$sha"
            return 0
        fi
    done
    return 1
}

# The first file changed between $1 and $commit that is not documentation; none: empty.
first_not_docs() {
    local files file
    files="$(git diff --name-only "$1" "$commit")" || { echo "(git diff $1 failed)"; return 0; }
    while IFS= read -r file; do
        case "$file" in
            ''|*.md|docs/*|wiki/*) ;;
            *) echo "$file"; return 0 ;;
        esac
    done <<<"$files"
}

read -r id state url <<<"$(latest "$commit")"
if [ -z "${id:-}" ] && base="$(tested_ancestor)" && [ -n "$base" ]; then
    other="$(first_not_docs "$base")"
    if [ -z "$other" ]; then
        echo "==> $BRANCH at $short changes only documentation since ${base:0:7}:" \
             "$workflow's run for ${base:0:7} counts" >&2
        short="${base:0:7}"
        read -r id state url <<<"$(latest "$base")"
    else
        echo "==> not only documentation changed since ${base:0:7}, the newest commit" \
             "$workflow ran for: $other" >&2
    fi
fi
if [ -z "${id:-}" ]; then
    cat >&2 <<MSG
ERROR: $BRANCH at $short has no $workflow run: only a commit CI passed is released.
       Not started yet: wait a minute (gh run list --workflow $workflow), then run this again
       Never started:   start it on $BRANCH: gh workflow run $workflow --ref $BRANCH
MSG
    exit 1
fi

case "$state" in
    completed/success) ;;
    completed/*)
        echo "ERROR: $workflow's run for $BRANCH at $short did not succeed ($state): $url" >&2
        echo "       Fix $BRANCH, then run it again: gh workflow run $workflow --ref $BRANCH" >&2
        exit 1 ;;
    *)
        echo "==> $workflow is still running for $BRANCH at $short; following run $id" >&2
        bash "$(dirname "${BASH_SOURCE[0]}")/follow-run.sh" "$id" >&2 \
            || { echo "ERROR: $workflow's run for $BRANCH at $short failed: $url" >&2; exit 1; } ;;
esac
# Every job must have succeeded too: one skipped, or failed under continue-on-error, did not test
# what it tests, however the run concluded (owner, 2026-11-08).
others="$(gh run view "$id" --json jobs \
    --jq '.jobs[] | select(.conclusion != "success") | "\(.name): \(.conclusion)"')" \
    || { echo "ERROR: cannot read the jobs of $workflow's run $id: $url" >&2; exit 1; }
if [ -n "$others" ]; then
    echo "ERROR: $workflow's run for $BRANCH has jobs that did not succeed: $url" >&2
    sed 's/^/       /' <<<"$others" >&2
    exit 1
fi
echo "$url"
