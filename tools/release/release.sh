#!/usr/bin/env bash
#
# release.sh — the whole release in one command, on the admin's machine, asking nothing
# (make release).
#
#     bash tools/release/release.sh [X.Y.Z]        (make release [KEY=PATH] [VERSION=X.Y.Z])
#
# Run it on main, at the release commit that `make release-prep` made on a branch and its pull
# request merged. Pull that yourself first, after looking at it: this never pulls, since what main
# holds is the admin's to know, and a pull here would sign whatever GitHub's main says.
#
# It checks the key, tags and builds (release-assets.sh: the audit, the tag, release.yml), waits for
# el8.yml, signs and publishes (release-sign.sh), checks the release as its users get it (make
# release-check), publishes the wiki (make wiki-publish), and GitLab follows (mirror.sh). Run it
# again after any stop: what is done is left out. The EL8 stubs are outside the release gate, so a
# failed el8.yml leaves the release to go out without them.
#
# The release key's file comes from KEY or FSBPYR_RELEASE_KEY_FILE, and its passphrase from
# FSBPYR_RELEASE_PASSPHRASE_FILE (passphrase.sh). Before anything is tagged, the key must open with
# it and be one that this tree pins.
#
# What the steps print goes to the terminal and to a report, in JSON, for reading later: under
# FSBPYR_RELEASE_STATE (default $XDG_STATE_HOME/fsbpyr-release), reports/TAG-TIME.json, and
# reports/latest.json leads to the newest. It is rewritten at each step, so it says how far a run got
# however that run ended: `result` is running, success, failed or interrupted, and `failed_step`
# and the steps' `output` say what went wrong. It holds no secret, and only the admin can read it.
#
# FSBPYR_RELEASE_WATCH_WAIT: how many seconds to wait for a workflow run to show up (default 60).
# Exit status: 0 done, 1 stopped, 2 usage, or that of the step that failed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck source=version-files.sh
. tools/release/version-files.sh
# shellcheck source=passphrase.sh
. tools/release/passphrase.sh

REPOSITORY=${FSBPYR_RELEASE_REPO:-devitops-com/fsbpyr}
REMOTE=hub
WATCH_WAIT=${FSBPYR_RELEASE_WATCH_WAIT:-60}
WATCH_POLL=3
REPORTS=${FSBPYR_RELEASE_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/fsbpyr-release}/reports
KEY_FILE=${FSBPYR_RELEASE_KEY_FILE:-}
KEY_FILE=${KEY_FILE/#\~/$HOME}
STEPS=7

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
now() { date -u +%FT%TZ; }

[ $# -le 1 ] || { echo "usage: release.sh [X.Y.Z]" >&2; exit 2; }
VERSION=${1:-$(current_version)}
[[ "$VERSION" =~ $XYZ ]] || die "$VERSION is no version X.Y.Z"
TAG=v$VERSION
EL8_TAG=el8-$TAG
command -v jq >/dev/null || die "jq is not installed: the report needs it"

# --- The report ---------------------------------------------------------------

(umask 077 && mkdir -p "$REPORTS")
STARTED=$(now)
COMMIT=$(git rev-parse HEAD)
REPORT=$REPORTS/$TAG-$(date -u +%Y%m%dT%H%M%SZ).json
WORK=$(mktemp -d "$REPORTS/.run.XXXXXXXX")
: >"$WORK/steps"
: >"$WORK/warnings"
NUMBER=0
RUNNING=
FAILED=
INTERRUPTED=0

# write_report RESULT EXIT_STATUS: the whole report, in place at once, or never half written.
write_report() {
    local finished=
    [ "$1" = running ] || finished=$(now)
    (umask 077 && jq -n --arg version "$VERSION" --arg tag "$TAG" --arg repository "$REPOSITORY" \
        --arg commit "$COMMIT" --arg started "$STARTED" --arg finished "$finished" \
        --arg result "$1" --argjson exit_code "$2" --arg running "$RUNNING" --arg failed "$FAILED" \
        --rawfile warnings "$WORK/warnings" --slurpfile steps "$WORK/steps" '
        def text: if . == "" then null else . end;
        {schema: 1, version: $version, tag: $tag, repository: $repository, commit: $commit,
         result: $result, exit_code: $exit_code, started: $started, finished: ($finished | text),
         running_step: ($running | text), failed_step: ($failed | text),
         warnings: ($warnings | split("\n") | map(select(. != ""))), steps: $steps}
        ' >"$REPORT.new")
    mv -f "$REPORT.new" "$REPORT"
}

# record_step RESULT EXIT_STATUS: the step that ran, with what it printed.
record_step() {
    jq -n -c --argjson number "$NUMBER" --arg name "$RUNNING" --arg title "$TITLE" \
        --arg started "$STEP_STARTED" --arg finished "$(now)" \
        --argjson seconds "$(($(date +%s) - STEP_FROM))" --arg result "$1" \
        --argjson exit_code "$2" --rawfile output "$WORK/log" '
        {number: $number, name: $name, title: $title, started: $started, finished: $finished,
         seconds: $seconds, result: $result, exit_code: $exit_code,
         output: ($output | rtrimstr("\n") | split("\n"))}
        ' >>"$WORK/steps"
}

finish() {
    local status=$? result=failed
    trap - EXIT INT TERM HUP
    [ "$status" -ne 0 ] || result=success
    [ "$INTERRUPTED" = 0 ] || result=interrupted
    if [ -n "$RUNNING" ]; then
        FAILED=$RUNNING
        record_step "$result" "$status"
        RUNNING=
    fi
    write_report "$result" "$status"
    rm -rf -- "${WORK:?}"
    if [ "$status" -eq 0 ]; then
        printf 'Report: %s\n' "$REPORT"
    else
        printf 'Report: %s\n' "$REPORT" >&2
    fi
    exit "$status"
}

write_report running null
ln -sfn "${REPORT##*/}" "$REPORTS/latest.json"
trap finish EXIT
trap 'INTERRUPTED=1; exit 130' INT
trap 'INTERRUPTED=1; exit 143' TERM
trap 'INTERRUPTED=1; exit 129' HUP

# run_step NAME TITLE COMMAND...: a subshell with -e of its own, since `set +e` around the pipe must
# not reach the command, and neither may the status of tee.
run_step() {
    RUNNING=$1 TITLE=$2
    shift 2
    NUMBER=$((NUMBER + 1))
    STEP_STARTED=$(now) STEP_FROM=$(date +%s)
    printf '\n=== %s/%s %s\n' "$NUMBER" "$STEPS" "$TITLE"
    write_report running null
    local status
    set +e
    (set -e; "$@") 2>&1 | tee "$WORK/log"
    status=${PIPESTATUS[0]}
    set -e
    [ "$status" -eq 0 ] || exit "$status"
    record_step success 0
    RUNNING=
    write_report running null
}

# --- The steps ----------------------------------------------------------------

# The commit a tag has on GitHub, by its whole name; nothing if there is no such tag.
tag_on_github() { git ls-remote "$REMOTE" | awk -v ref="refs/tags/$1" '$2 == ref { print $1 }'; }

# The latest run of a workflow for this tag. GitHub shows a run a moment after the push.
run_of() { # workflow file
    local run waited=0
    while :; do
        run=$(gh run list --repo "$REPOSITORY" --workflow "$1" --branch "$TAG" --limit 1 \
            --json databaseId --jq '.[0].databaseId // empty' 2>/dev/null || true)
        case "$run" in
            ''|*[!0-9]*) ;;
            *) echo "$run"; return 0 ;;
        esac
        [ "$waited" -lt "$WATCH_WAIT" ] || return 1
        sleep "$WATCH_POLL"
        waited=$((waited + WATCH_POLL))
    done
}

# Whether a draft of this release is left to sign.
draft_left() {
    local release
    for release in "$TAG" "$EL8_TAG"; do
        [ "$(gh release view "$release" --repo "$REPOSITORY" --json isDraft --jq .isDraft 2>/dev/null)" != true ] \
            || return 0
    done
    return 1
}

# Before the tag, which cannot be taken back: a build takes a while, to find out then.
check_the_key() {
    if [ -n "$(tag_on_github "$TAG")" ] && ! draft_left; then
        echo "nothing is left to sign"
        return 0
    fi
    [ -n "$KEY_FILE" ] || die "no release key: give KEY=PATH or set FSBPYR_RELEASE_KEY_FILE"
    read_passphrase
    FSBPYR_SIGNING_PASSPHRASE=$passphrase bash tools/release/release-signing.sh check-key \
        "$KEY_FILE" src/fsbpyr/release-keys/*.pub
}

tag_and_build() {
    local run
    if [ -n "$(tag_on_github "$TAG")" ]; then
        echo "$TAG is on GitHub: going on from there"
        run=$(run_of release.yml) || die "$TAG has no release.yml run"
        bash tools/release/follow-run.sh "$run" \
            || die "release.yml failed for $TAG: see gh run view $run --log-failed"
    else
        bash tools/release/release-assets.sh "$VERSION"
    fi
}

wait_for_el8() {
    local run warning="el8.yml did not pass for $TAG: the release goes out without EL8 stubs for now."
    if run=$(run_of el8.yml) && bash tools/release/follow-run.sh "$run"; then
        echo "el8.yml passed"
    else
        echo "$warning" >>"$WORK/warnings"
        echo "warning: $warning" >&2
        echo "         Once a run has passed: make release-sign KEY=PATH" >&2
    fi
}

sign_and_publish() {
    if draft_left; then
        bash tools/release/release-sign.sh "$KEY_FILE" "$VERSION"
    else
        echo "nothing is left to sign"
    fi
}

run_step key "Check the release key" check_the_key
run_step tag-and-build "Tag and build $TAG" tag_and_build
run_step el8-stubs "Wait for the EL8 stubs" wait_for_el8
run_step sign-and-publish "Sign and publish" sign_and_publish
run_step check "Check it as users get it" make --no-print-directory release-check VERSION="$VERSION"
run_step wiki "Publish the wiki" make --no-print-directory wiki-publish
run_step mirror "GitLab follows" bash tools/release/mirror.sh

printf '\nReleased fsbpyr %s\n' "$TAG"
gh release view "$TAG" --repo "$REPOSITORY" --json url --jq .url 2>/dev/null || true
