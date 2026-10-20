#!/usr/bin/env bash
#
# green-run.sh — prints the URL of the successful run of a workflow for a commit's push to
# main, or fails.
#
#     bash tools/release/green-run.sh WORKFLOW COMMIT
#
# A release needs a green release.yml run (build, smoke test, checks, core tests) and a green
# full-suite.yml run (the whole suite) of the commit it is made from. release.sh checks HEAD
# before it commits; the publish job checks the release commit's parent. A run still in
# progress is followed with `gh run watch --exit-status`, as release.sh follows the tag's run.
# Exit 1: no run for that commit, a failed one, or one that fails while watched. The reason
# goes to stderr, the URL to stdout. Needs gh, authenticated (GH_TOKEN in CI).
set -euo pipefail

[ $# -eq 2 ] || { echo "usage: green-run.sh WORKFLOW COMMIT" >&2; exit 2; }
workflow="$1" commit="$2"
BRANCH="main"
short="${commit:0:7}"

# The newest run of that push, as "ID STATUS/CONCLUSION URL".
latest() {
    gh run list --workflow "$workflow" --branch "$BRANCH" --event push --commit "$commit" \
        --limit 1 --json databaseId,status,conclusion,url \
        --jq '.[0] // empty | "\(.databaseId) \(.status)/\(.conclusion) \(.url)"' || true
}

read -r id state url <<<"$(latest)"
if [ -z "${id:-}" ]; then
    cat >&2 <<MSG
ERROR: $BRANCH at $short has no $workflow run: only a commit CI passed is released.
       Not started yet: wait a minute (gh run list --workflow $workflow), then run this again
       Never started:   push $BRANCH, or start the workflow (workflow_dispatch) on it
MSG
    exit 1
fi

case "$state" in
    completed/success) ;;
    completed/*)
        echo "ERROR: $workflow's run for $BRANCH at $short did not succeed ($state): $url" >&2
        echo "       Fix $BRANCH and push; release once that push's runs are green" >&2
        exit 1 ;;
    *)
        echo "==> $workflow is still running for $BRANCH at $short; watching run $id" >&2
        gh run watch "$id" --exit-status >&2 \
            || { echo "ERROR: $workflow's run for $BRANCH at $short failed: $url" >&2; exit 1; } ;;
esac
echo "$url"
