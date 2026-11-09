#!/usr/bin/env bash
#
# release-sign.sh — on the admin's machine, sign the drafts CI left for a release and publish them
# (docs/signing.md §5.3). K never leaves this machine: only the .sig goes to GitHub.
#
#     bash tools/release/release-sign.sh KEY [X.Y.Z]     (make release-sign KEY=PATH [VERSION=X.Y.Z])
#
# X.Y.Z defaults to the version HEAD declares. KEY is K, or R in a recovery: both pass or fail the
# same checks, against the keys this tag and the previous release pin. It runs again for the
# companion el8-vX.Y.Z when el8.yml has finished later than release.yml.
#
# Nothing is signed unless your HEAD, GitHub's main, GitHub's tag and the attestation of the sums
# file all name one commit, and every job of the run the attestation names ran on GitHub's own
# machines: the attestation is made after the build, and says nothing of where the build ran. That
# rests on GitHub itself being sound, and an account on it being what may be lost: an account cannot
# get GitHub to attest a file for a commit that did not build it. The attested sums then vouch for
# every other file. What that commit holds is the admin's to know, so this is only as good as what
# they pulled into main.
#
# The draft is read again before the signature is uploaded and before it is published: whoever
# holds the account has as long as signing takes.
#
# The attested files are then checked as this commit makes them (owner, 2026-11-08, finding R-01):
# each .xz unpacks to the file its sums list, install.sh is what make-install.sh makes of the draft
# here, with this checkout's own fsbpyr reading the executables (FSBPYR_RELEASE_INSPECTOR, an
# fsbpyr that runs here, if set), and the default Python's executables and every EL8 stub are the
# bytes the full suite built from the same commit (its el8-build and el8-stub artifacts). A file
# that is not burns the version: the tag cannot be taken back, so nothing of it is ever signed,
# and the fix is released as the next patch version. The burned releases are kept in
# FSBPYR_RELEASE_STATE (default $XDG_STATE_HOME/fsbpyr-release), in `burned`.
#
# The audit runs first, and again after publishing, when the record has the new release. Exit
# status: 0 done, 1 refused, 2 usage.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck source=version-files.sh
. tools/release/version-files.sh
# shellcheck source=passphrase.sh
. tools/release/passphrase.sh

REPOSITORY=${FSBPYR_RELEASE_REPO:-devitops-com/fsbpyr}
BRANCH=main
REMOTE=hub

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ $# -ge 1 ] && [ $# -le 2 ] && [ -n "$1" ] || { echo "usage: release-sign.sh KEY [X.Y.Z]" >&2; exit 2; }
KEY=$1
VERSION=${2:-$(current_version)}
[[ "$VERSION" =~ $XYZ ]] || die "$VERSION is no version X.Y.Z"
TAG=v$VERSION
EL8_TAG=el8-$TAG

# --- 1. The key --------------------------------------------------------------
[ -f "$KEY" ] || die "no key file $KEY"
case "$(realpath -- "$KEY")" in
    "$ROOT"/*) die "$KEY is inside this checkout: a release key is never kept in the repository" ;;
esac
read_passphrase

# The commit a name has on GitHub, by the whole name: ls-remote's patterns match the tail of a ref,
# so that a tag refs/tags/x/refs/tags/vX.Y.Z would answer for vX.Y.Z.
remote_commit() { # full ref name
    git ls-remote "$REMOTE" | awk -v ref="$1" '$2 == ref { print $1 }'
}

# Your HEAD, the tag here, GitHub's main and GitHub's tag are one commit.
one_commit() {
    local main remote_tag
    [ "$(git rev-parse "refs/tags/$TAG^{commit}")" = "$COMMIT" ] || die "$TAG is not this clone's HEAD ($COMMIT)"
    main=$(remote_commit "refs/heads/$BRANCH")
    [ "$main" = "$COMMIT" ] || die "GitHub's $BRANCH is at ${main:-nothing}, not at your commit $COMMIT: refusing to sign"
    remote_tag=$(remote_commit "refs/tags/$TAG^{}")
    [ -n "$remote_tag" ] || remote_tag=$(remote_commit "refs/tags/$TAG")
    [ "$remote_tag" = "$COMMIT" ] || die "GitHub's $TAG is at ${remote_tag:-nothing}, not at your commit $COMMIT: refusing to sign"
}

# --- 2. One commit -----------------------------------------------------------
BURNED=${FSBPYR_RELEASE_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/fsbpyr-release}/burned
for release in "$TAG" "$EL8_TAG"; do
    reason=$(awk -v r="$release" '$1 == r { $1 = ""; print substr($0, 2); exit }' "$BURNED" 2>/dev/null) || true
    [ -z "$reason" ] || die "$release is burned ($reason): nothing of it is ever signed; release the next patch version"
done
INSPECTOR=${FSBPYR_RELEASE_INSPECTOR:-}
for tool in gh git jq sha256sum xz "${INSPECTOR:-uv}"; do
    command -v "$tool" >/dev/null || die "$tool is not installed"
done
gh auth status >/dev/null 2>&1 || die "gh is not authenticated (run: gh auth login)"
[ "$(git rev-parse --abbrev-ref HEAD)" = "$BRANCH" ] || die "not on $BRANCH"
[ -z "$(git status --porcelain --untracked-files=all)" ] || die "the working tree is not clean"
COMMIT=$(git rev-parse HEAD)
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || die "this clone has no tag $TAG"
one_commit

# --- 3. The audit ------------------------------------------------------------
bash tools/release/audit.sh || die "the audit did not pass: nothing was signed"

WORK=$(mktemp -d)
trap 'rm -rf -- "${WORK:?}"' EXIT

# --- 4. The drafts -----------------------------------------------------------
# State of a release: draft, published or none.
state_of() { # release
    local draft
    if ! draft=$(gh release view "$1" --repo "$REPOSITORY" --json isDraft --jq .isDraft 2>"$WORK/view.err"); then
        grep -q 'release not found' "$WORK/view.err" || { cat "$WORK/view.err" >&2; exit 1; }
        echo none
    else
        case "$draft" in
            true) echo draft ;;
            false) echo published ;;
            *) echo "ERROR: GitHub says isDraft is '$draft' for $1" >&2; exit 1 ;;
        esac
    fi
}

# Every file of the draft is listed in its sums with the digest it has, and a main release has
# every listed file. A companion publishes each stub's .xz only, and its sums list the stub too.
check_files() { # directory, sums, release
    local listed files
    listed=$(awk '{ print $2 }' "$1/$2" | LC_ALL=C sort)
    files=$(cd "$1" && find . -mindepth 1 -maxdepth 1 -printf '%P\n' | grep -vxF "$2" | LC_ALL=C sort)
    [ -z "$(LC_ALL=C comm -13 <(printf '%s\n' "$listed") <(printf '%s\n' "$files"))" ] \
        || die "$3 has files its attested $2 does not list: refusing to sign"
    if [ "$3" = "$TAG" ]; then
        [ -z "$(LC_ALL=C comm -23 <(printf '%s\n' "$listed") <(printf '%s\n' "$files"))" ] \
            || die "$3 lacks files its attested $2 lists: refusing to sign"
    fi
    (cd "$1" && sha256sum --quiet --strict --ignore-missing -c "$2") >/dev/null \
        || die "a file of $3 is not what its attested $2 lists: refusing to sign"
}

# Before anything reads the sums: it is GitHub's word that this commit's own workflow made them.
# --deny-self-hosted-runners speaks for the job that attested, which only hashed what the jobs
# before it left, so every job of the run must have run on GitHub's machines too: runner group 0,
# which an organization's groups, from 1 up, are not.
check_attestation() { # file, workflow, release
    local attestation uris uri foreign
    attestation=$(gh attestation verify "$1" --repo "$REPOSITORY" \
        --signer-workflow "$REPOSITORY/.github/workflows/$2" --source-ref "refs/tags/$TAG" \
        --source-digest "$COMMIT" --deny-self-hosted-runners --format json) \
        || die "the attestation of $3's ${1##*/} does not name your commit $COMMIT: refusing to sign"
    uris=$(jq -r '.[].verificationResult.signature.certificate.runInvocationURI' <<<"$attestation")
    while IFS= read -r uri; do
        [[ "$uri" =~ ^https://github\.com/$REPOSITORY/actions/runs/([0-9]+)/attempts/([0-9]+)$ ]] \
            || die "the attestation of $3 names the run '$uri': refusing to sign"
        foreign=$(gh api --paginate "repos/$REPOSITORY/actions/runs/${BASH_REMATCH[1]}/attempts/${BASH_REMATCH[2]}/jobs?per_page=100" \
            --jq '.jobs[] | select(.conclusion != "skipped" and .runner_group_id != 0) | .name')
        [ -z "$foreign" ] || die "run ${BASH_REMATCH[1]} of $3 has jobs on machines of someone else: $foreign: refusing to sign"
    done <<<"$uris"
}

# --- The bytes this commit makes ------------------------------------------------
# A release whose attested files are not what this commit makes, and every release of its version
# not yet published, are burned: recorded, and never signed (see the top).
burn() { # release, what differs
    local release=$1 reason=$2 burned=("$1") each
    [ "$release" != "$EL8_TAG" ] || [ "$main_state" != draft ] || burned+=("$TAG")
    [ "$release" != "$TAG" ] || [ "$el8_state" = published ] || burned+=("$EL8_TAG")
    mkdir -p "${BURNED%/*}"
    for each in "${burned[@]}"; do printf '%s %s\n' "$each" "$reason"; done >>"$BURNED"
    die "$reason: ${burned[*]} burned. Nothing was signed or published, and nothing of it ever will be; fix the cause and release the next patch version"
}

# The SHA-256 a sums file lists for a name; empty if none.
listed_digest() { # sums file, name
    awk -v name="$2" '$2 == name || $2 == "*" name { print $1; exit }' "$1"
}

# Every .xz unpacks to the file the sums list under its name without .xz.
check_xz() { # directory, sums, release
    local file name want got
    for file in "$1"/*.xz; do
        [ -e "$file" ] || continue
        name=${file##*/} && name=${name%.xz}
        want=$(listed_digest "$1/$2" "$name")
        [ -n "$want" ] || burn "$3" "$3's $2 lists ${name}.xz but not $name, which it unpacks to"
        got=$(xz --decompress --stdout --memlimit-decompress=512MiB -- "$file" | sha256sum | cut -d' ' -f1) \
            || burn "$3" "$3's ${name}.xz does not unpack"
        [ "$got" = "$want" ] || burn "$3" "$3's ${name}.xz unpacks to other bytes than its $2 lists for $name"
    done
}

# install.sh is what make-install.sh makes of the draft's files here, with an fsbpyr of this
# commit's own reading the executables (not one of the draft's).
check_installer() { # directory, release
    local inspector=$INSPECTOR
    if [ -z "$inspector" ]; then
        inspector=$WORK/inspector
        printf '#!/bin/sh\nexec uv run --locked --quiet --project %q fsbpyr "$@"\n' "$ROOT" >"$inspector"
        chmod +x "$inspector"
    fi
    bash tools/release/make-install.sh "$VERSION" "$1" "$WORK/install.sh" "$inspector" >/dev/null \
        || die "make-install.sh could not make $2's install.sh here: nothing was signed"
    cmp -s "$WORK/install.sh" "$1/install.sh" \
        || burn "$2" "$2's install.sh is not what make-install.sh makes of its files here"
}

# SUITE_FILE: a file of an artifact of the full suite's green push run of this commit, which built
# the release's bytes from it too; downloaded once. Dies, burning nothing, when there is none.
suite_file() { # artifact, file
    local dir=$WORK/suite/$1
    if [ -z "${SUITE_RUN:-}" ]; then
        # Not --status success: GitHub then lists no run of a commit (tried on v0.31.0's).
        SUITE_RUN=$(gh run list --repo "$REPOSITORY" --workflow full-suite.yml --branch "$BRANCH" \
            --event push --commit "$COMMIT" --limit 20 --json databaseId,conclusion \
            --jq '[.[] | select(.conclusion == "success")][0].databaseId // empty') \
            || die "cannot list full-suite.yml's runs: nothing was signed"
        [ -n "$SUITE_RUN" ] || die "full-suite.yml has no green run of $COMMIT to compare the drafts with: nothing was signed"
    fi
    if [ ! -d "$dir" ]; then
        gh run download "$SUITE_RUN" --repo "$REPOSITORY" --name "$1" --dir "$dir" >/dev/null 2>&1 \
            || die "the full suite's run $SUITE_RUN has no artifact $1 to compare with: nothing was signed"
    fi
    [ -f "$dir/$2" ] || die "the full suite's artifact $1 has no $2: nothing was signed"
    SUITE_FILE=$dir/$2
}

# The default Python's executables are the full suite's el8-build ones, built from the tag too.
check_suite_executables() { # directory, sums, release
    local python file name compared=0
    python=$(cut -d. -f1,2 .python-version)
    for file in "$1/fsbpyr-$python-"*; do
        name=${file##*/}
        [[ "$name" =~ ^fsbpyr-${python//./\\.}-([a-z0-9_]+-[a-z]+-[a-z]+)$ ]] || continue
        suite_file "el8-fsbpyr-$python-${BASH_REMATCH[1]}" "$name"
        [ "$(listed_digest "$1/$2" "$name")" = "$(sha256sum "$SUITE_FILE" | cut -d' ' -f1)" ] \
            || burn "$3" "$3's $name is not the full suite's build of the same commit (run $SUITE_RUN)"
        compared=$((compared + 1))
    done
    [ "$compared" -gt 0 ] || die "$3 has no fsbpyr-$python-<target> to compare with the full suite: nothing was signed"
}

# Every stub the companion's sums list is the full suite's el8-stub one.
check_suite_stubs() { # directory, sums, release
    local digest name
    while read -r digest name; do
        name=${name#\*}
        [[ "$name" =~ ^fsbpyr-stub-(3\.[0-9]+)-([a-z0-9_]+-[a-z]+-[a-z]+)-glibc2\.28$ ]] || continue
        suite_file "el8-stub-${BASH_REMATCH[1]}-${BASH_REMATCH[2]}" "$name"
        [ "$digest" = "$(sha256sum "$SUITE_FILE" | cut -d' ' -f1)" ] \
            || burn "$3" "$3's $name is not the full suite's stub of the same commit (run $SUITE_RUN)"
    done <"$1/$2"
}

# The files of a release's one draft on GitHub now, as `name sha256:digest`, sorted.
draft_files() { # release, or the draft's id on stdout with `id` as second argument
    local listed
    listed=$(gh api --paginate "repos/$REPOSITORY/releases?per_page=100" \
        --jq ".[] | select(.tag_name == \"$1\" and .draft)")
    [ "$(grep -c '^{' <<<"$listed" || true)" = 1 ] || die "$1 is not exactly one draft: refusing to sign"
    [ "$(jq -r .prerelease <<<"$listed")" = false ] || die "the draft $1 was made a prerelease: refusing to sign"
    if [ "${2-}" = id ]; then
        jq -r .id <<<"$listed"
    else
        jq -r '.assets[] | "\(.name) \(.digest)"' <<<"$listed" | LC_ALL=C sort
    fi
}

# The files of the directory that was checked, as draft_files lists them.
checked_files() { # directory
    (cd "$1" && find . -mindepth 1 -maxdepth 1 -printf '%P\0' | xargs -0 sha256sum) |
        awk '{ print $2, "sha256:" $1 }' | LC_ALL=C sort
}

# What was checked is still what is there, and the commit still the commit.
unchanged() { # directory, release
    one_commit
    [ "$(draft_files "$2")" = "$(checked_files "$1")" ] \
        || die "the draft $2 changed while it was being signed: nothing was published, see what changed before anything else"
}

check_release() { # release, sums file, workflow
    local release=$1 sums=$2 workflow=$3 dir=$WORK/$1
    echo "==> Checking the draft $release"
    gh release download "$release" --repo "$REPOSITORY" --dir "$dir"
    [ -f "$dir/$sums" ] || die "the draft $release has no $sums"
    [ ! -e "$dir/$sums.sig" ] || die "the draft $release already has a signature. If a run of this command stopped after uploading it, remove it (gh release delete-asset $release $sums.sig); if not, someone else put it there"
    check_attestation "$dir/$sums" "$workflow" "$release"
    check_files "$dir" "$sums" "$release"
    check_xz "$dir" "$sums" "$release"
    if [ "$release" = "$TAG" ]; then
        check_installer "$dir" "$release"
        check_suite_executables "$dir" "$sums" "$release"
    else
        check_suite_stubs "$dir" "$sums" "$release"
    fi
}

summary_of() { # release, sums file
    local dir=$WORK/$1
    printf '  release : %s  (%s files)\n' "$1" "$(find "$dir" -mindepth 1 -maxdepth 1 | wc -l)"
    printf '  sums    : %s  %s\n' "$2" "$(sha256sum "$dir/$2" | cut -d' ' -f1)"
}

# Once, for all the signing of this run. From no environment: it goes to the signing alone.

sign_sums() { # release, sums file
    local file=$WORK/$1/$2 previous
    FSBPYR_SIGNING_PASSPHRASE=$passphrase bash tools/release/release-signing.sh sign "$KEY" "$file"
    previous=$(bash tools/release/previous-release.sh "$TAG")
    bash tools/release/check-signature.sh . "$file" "$TAG" "$previous"
}

publish_release() { # release, sums file, make_latest value
    local release=$1 sums=$2 latest=$3 dir=$WORK/$1
    mv "$dir/$sums.sig" "$WORK/$release.sig"
    unchanged "$dir" "$release"
    mv "$WORK/$release.sig" "$dir/$sums.sig"
    gh release upload "$release" "$dir/$sums.sig" --repo "$REPOSITORY"
    unchanged "$dir" "$release"
    # By id: a tag can have more than one draft by now, and only this one was checked.
    gh api -X PATCH "repos/$REPOSITORY/releases/$(draft_files "$release" id)" \
        -F draft=false -f "make_latest=$latest" >/dev/null
    bash tools/release/audit.sh --record "$release"
    echo "==> Published $release"
}

main_state=$(state_of "$TAG")
el8_state=$(state_of "$EL8_TAG")
case "$main_state" in
    none) die "$TAG has no draft: release.yml has not finished" ;;
    published) echo "$TAG is published" ;;
esac
case "$el8_state" in
    none) echo "$EL8_TAG has no draft yet: run this again when el8.yml has finished, or leave it, as a release may have no EL8 stubs" ;;
    published) echo "$EL8_TAG is published" ;;
esac
[ "$main_state" = draft ] || [ "$el8_state" = draft ] || exit 0

# Both drafts are checked, and both signed, before either is published: a wrong passphrase, or a
# companion that does not pass, leaves nothing half done.
[ "$main_state" != draft ] || check_release "$TAG" SHA256SUMS release.yml
[ "$el8_state" != draft ] || check_release "$EL8_TAG" SHA256SUMS-glibc2.28 el8.yml
printf '\nSigning and publishing, on %s:\n' "$REPOSITORY"
[ "$main_state" != draft ] || summary_of "$TAG" SHA256SUMS
[ "$el8_state" != draft ] || summary_of "$EL8_TAG" SHA256SUMS-glibc2.28
cat <<SUMMARY
  commit  : $COMMIT
            your HEAD, GitHub's $BRANCH, GitHub's $TAG and the attestation all name it,
            and every job of its run ran on GitHub's machines
  key     : $KEY
SUMMARY
[ "$main_state" != draft ] || sign_sums "$TAG" SHA256SUMS
[ "$el8_state" != draft ] || sign_sums "$EL8_TAG" SHA256SUMS-glibc2.28
[ "$main_state" != draft ] || publish_release "$TAG" SHA256SUMS true
[ "$el8_state" != draft ] || publish_release "$EL8_TAG" SHA256SUMS-glibc2.28 false

# --- 5. As GitHub now serves it ----------------------------------------------
bash tools/release/audit.sh || die "the audit fails after publishing: see above"
