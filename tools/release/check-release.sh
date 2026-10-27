#!/usr/bin/env bash
#
# check-release.sh — check a published release as its users get it, after `make release`.
#
#     bash tools/release/check-release.sh [X.Y.Z]        (make release-check [VERSION=X.Y.Z])
#
# Without X.Y.Z the latest release is checked. Everything happens in a scratch directory of its
# own under $TMPDIR, removed at the end; nothing is installed or changed outside it but uv's
# cache (the builds) and, for the other target, a podman image that is removed again unless it
# was there before. `make release-check` runs it in a directory under ~/tmp and in a cgroup with
# a task and memory limit, as the tests run. Checks, each reported as ok, FAIL or skip:
#
#   assets        every (Python, target) of the tag's build.yml has its executable, .xz, SBOM
#                 and licence notices; install.sh and SHA256SUMS; nothing else (the EL8 stubs
#                 are not assets of the release: releases are immutable, see below)
#   checksums     SHA256SUMS lists every other asset, and every one matches: what is downloaded,
#                 and each executable as its .xz unpacks; the executables themselves are not
#                 downloaded (the .xz has the same bytes in a third of the size), GitHub's
#                 digest of every asset must be the one SHA256SUMS lists
#   attestations  each executable's provenance and SBOM attestations, by release.yml from the
#                 tag (gh attestation verify, 8 at once, with one fetch of the trusted root)
#   el8 stubs     the companion release el8-<tag>, which el8.yml publishes after the release
#                 and which not every release has (skip: no EL8 stubs): published (no draft, no
#                 prerelease), immutable, not the latest release, its tag on the tag's commit,
#                 exactly SHA256SUMS-glibc2.28 and every (Python, target)'s stub .xz; each unpacks
#                 to the digest listed, with a provenance attestation by el8.yml from
#                 refs/tags/<tag> (el8.yml runs on the tag)
#   el8 build     with EL8 stubs: the default Python's executable builds the hello fixture with
#                 --glibc 2.28, fetching the stub from the companion release as a user's does
#                 (0.26.0's fsbpyr fetches from v0.26.0, which has none: its stub is placed
#                 beside the executable), and the app runs in rockylinux:8 (podman)
#   executables   on this machine's target: --version is X.Y.Z-<the tag's commit>, inspect
#                 --verify passes, the Python is the asset's
#   installer     install.sh, for every Python, into a scratch home with --no-modify-path:
#                 the release's executable, the man page and the completions, nothing else
#   dispatch      the default Python's executable builds a project pinned to another Python by
#                 fetching that Python's executable of this release
#   self-update   the previous release's (not a companion's) executables update to this one's, Python by Python
#                 (only for the latest release: self update installs the latest)
#   other-target  the other target's executables under podman (QEMU), when podman runs it
#   installed     the fsbpyr on PATH, or in ~/.local/bin: its version (information only)
#
# Exit 0 when nothing failed, 1 otherwise. Needs gh (authenticated), git, curl, uv, cmp,
# sha256sum and xz; podman for the other target and the EL8 app.
set -uo pipefail

REPO="devitops-com/fsbpyr"
SIGNER="$REPO/.github/workflows/release.yml"
EL8_SIGNER="$REPO/.github/workflows/el8.yml"
EL8_SUMS="SHA256SUMS-glibc2.28"
XYZ='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'

failed=0 skipped=0
ok()   { echo "  ok    $*"; }
fail() { echo "  FAIL  $*"; failed=$((failed + 1)); }
skip() { echo "  skip  $*"; skipped=$((skipped + 1)); }
step() { echo "==> $*"; }

[ $# -le 1 ] || { echo "usage: check-release.sh [X.Y.Z]" >&2; exit 2; }
for tool in gh git curl uv cmp sha256sum xz; do
    command -v "$tool" >/dev/null || { echo "ERROR: $tool is not installed" >&2; exit 2; }
done
if [ $# -eq 1 ]; then
    [[ "$1" =~ $XYZ ]] || { echo "ERROR: '$1' is not a version X.Y.Z" >&2; exit 2; }
    TAG="v$1"
else
    TAG="$(gh release view --repo "$REPO" --json tagName --jq .tagName)" \
        || { echo "ERROR: cannot read the latest release" >&2; exit 2; }
    # a companion release (el8-vX.Y.Z) must never be the latest: self update and install.sh read it
    [[ "$TAG" =~ ^v ]] || { echo "ERROR: the latest release is $TAG, not a version tag" >&2; exit 2; }
fi
VERSION="${TAG#v}"
COMPANION="el8-$TAG"  # the release of the EL8 stubs of $TAG (el8.yml)
LATEST="$(gh release view --repo "$REPO" --json tagName --jq .tagName 2>/dev/null || true)"

base="${TMPDIR:-$HOME/tmp}"  # a regular file system, not a tmpfs /tmp
mkdir -p "$base"
work="$(mktemp -d "$base/release-check.XXXXXX")" || { echo "ERROR: no scratch directory in $base" >&2; exit 2; }
pulled_image=""  # the podman image this run pulled: removed again however the run ends
cleanup() {
    [ -z "$pulled_image" ] || podman rmi "$pulled_image" >/dev/null 2>&1 || true
    chmod -R u+w "$work" 2>/dev/null
    rm -rf "${work:?}"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
mkdir -p "$work/assets" "$work/home" "$work/attest"
# Every run of a release executable: a home and a cache of its own, no collection of the cache.
isolated() {
    env -i PATH="$PATH" HOME="$work/home" FSBPYR_CACHE_DIR="$work/cache" FSBPYR_GC=0 "$@"
}
# A build: the user's HOME (uv's cache, its Pythons), nothing else of the caller's environment
# (UV_* or SOURCE_DATE_EPOCH would change the bytes), and no Python downloaded for it.
building() {
    env -i PATH="$PATH" HOME="$HOME" UV_PYTHON_DOWNLOADS=never FSBPYR_CACHE_DIR="$work/cache" \
        FSBPYR_GC=0 "$@"
}
# Whether Python $1 is installed with uv (a distribution's python3.X does not count).
managed() { UV_PYTHON_DOWNLOADS=never uv python find --managed-python --no-project "$1" >/dev/null 2>&1; }
# A field of `fsbpyr inspect`'s text: "python     3.14.9, glibc >= 2.34" -> 3.14.9.
field() { awk -v key="$2" '$1 == key { sub(/,$/, "", $2); print $2; exit }' <<<"$1"; }
# `gh attestation verify ARGS` in the background, 8 at once; its status goes to $work/attest/<n>,
# the nth call. Sigstore's trusted root is fetched once, through TUF as each verify would fetch it
# itself (seconds, with retries): a verify then costs two requests, about a second.
verifying=0 trusted=()
verify() {
    verifying=$((verifying + 1))
    if [ "$verifying" -eq 1 ] && gh attestation trusted-root >"$work/trusted_root.jsonl" 2>/dev/null; then
        trusted=(--custom-trusted-root "$work/trusted_root.jsonl")
    fi
    while [ "$(jobs -pr | wc -l)" -ge 8 ]; do wait -n; done
    { gh attestation verify "$@" --repo "$REPO" "${trusted[@]}" >/dev/null 2>&1
      echo $? >"$work/attest/$verifying"; } &
}
# Whether the nth verify passed; after `wait`.
verified() { [ "$(cat "$work/attest/$1" 2>/dev/null)" = 0 ]; }

echo "Checking fsbpyr $TAG in $work"

# --- The tag: its commit, Pythons and targets -------------------------------------------------
step "the tag's source"
if ! git clone -q --depth 1 --branch "$TAG" "https://github.com/$REPO.git" "$work/src" 2>/dev/null; then
    fail "cannot clone $TAG"
    exit 1
fi
COMMIT="$(git -C "$work/src" rev-parse HEAD)"
BUILD_YML="$work/src/.github/workflows/build.yml"
read -r -a PYTHONS <<<"$(sed -n 's/.*python: \[\(.*\)\].*one entry per Python.*/\1/p' "$BUILD_YML" \
    | head -1 | tr -d '" ' | tr ',' ' ')"
read -r -a TARGETS <<<"$(sed -n 's/.*target: \[\(.*\)\].*what the assets are named with.*/\1/p' \
    "$BUILD_YML" | head -1 | tr -d '" ' | tr ',' ' ')"
DEFAULT="$(tr -d '[:space:]' <"$work/src/.python-version" | cut -d. -f1,2)"  # 3.14.9 -> 3.14
case "$(uname -m)" in
    x86_64) HOST="x86_64-linux-gnu" OTHER="aarch64-linux-gnu" ;;
    aarch64) HOST="aarch64-linux-gnu" OTHER="x86_64-linux-gnu" ;;
    *) echo "ERROR: no release target for $(uname -m)" >&2; exit 2 ;;
esac
if [ "${#PYTHONS[@]}" -eq 0 ] || [ "${#TARGETS[@]}" -eq 0 ]; then
    fail "cannot read the Pythons and targets from $TAG's build.yml"
    exit 1
fi
ok "$TAG is ${COMMIT:0:8}: Pythons ${PYTHONS[*]} (default $DEFAULT), targets ${TARGETS[*]}"

# --- Assets, checksums, attestations ----------------------------------------------------------
step "assets"
expected=(install.sh SHA256SUMS)
for py in "${PYTHONS[@]}"; do
    for target in "${TARGETS[@]}"; do
        expected+=("fsbpyr-$py-$target" "fsbpyr-$py-$target.xz" "fsbpyr-$py-$target.cdx.json"
                   "THIRD_PARTY_LICENSES-$py-$target.txt")
    done
done
published="$(gh release view "$TAG" --repo "$REPO" --json assets --jq '.assets[].name' | sort)"
missing="$(comm -23 <(printf '%s\n' "${expected[@]}" | sort) <(echo "$published"))"
extra="$(comm -13 <(printf '%s\n' "${expected[@]}" | sort) <(echo "$published"))"
[ -z "$missing" ] && ok "all ${#expected[@]} expected assets" || fail "missing: $(echo $missing)"
[ -z "$extra" ] && ok "no other asset" || fail "not expected: $(echo $extra)"

step "checksums"
# Everything but the executables, which their .xz give: the network is the slow part.
if gh release download "$TAG" --repo "$REPO" --dir "$work/assets" --pattern '*.xz' \
        --pattern '*.json' --pattern '*.txt' --pattern install.sh --pattern SHA256SUMS \
        >/dev/null 2>&1; then
    for py in "${PYTHONS[@]}"; do
        for target in "${TARGETS[@]}"; do
            xz --decompress --keep "$work/assets/fsbpyr-$py-$target.xz" 2>/dev/null
        done
    done
    listed="$(awk '{ sub(/^\*/, "", $2); print $2 }' "$work/assets/SHA256SUMS" | sort)"
    unlisted="$(comm -23 <(echo "$published" | grep -vx SHA256SUMS) <(echo "$listed"))"
    [ -z "$unlisted" ] && ok "SHA256SUMS lists every other asset" || fail "not in SHA256SUMS: $(echo $unlisted)"
    if (cd "$work/assets" && sha256sum --quiet -c SHA256SUMS >/dev/null 2>&1); then
        ok "$(echo "$listed" | wc -l) files match SHA256SUMS (the executables as their .xz unpack)"
    else
        fail "$(cd "$work/assets" && sha256sum -c SHA256SUMS 2>&1 | grep -v ': OK$' | head -3)"
    fi
    # what GitHub serves: its digest of every asset, the executables' too
    served="$(gh release view "$TAG" --repo "$REPO" --json assets \
        --jq '.assets[] | select(.name != "SHA256SUMS") | "\(.digest // "none" | ltrimstr("sha256:"))  \(.name)"' | sort -k2)"
    differ="$(comm -13 <(awk '{ sub(/^\*/, "", $2); print $1 "  " $2 }' "$work/assets/SHA256SUMS" | sort -k2) \
        <(echo "$served") | awk '{ print $2 }')"
    [ -z "$differ" ] && ok "GitHub's digest of every asset is the one SHA256SUMS lists" \
        || fail "GitHub's digest differs from SHA256SUMS (or is missing): $(echo $differ)"
else
    fail "cannot download $TAG's assets"
fi
chmod +x "$work"/assets/fsbpyr-*-linux-gnu 2>/dev/null

step "attestations"
first=$((verifying + 1))
for py in "${PYTHONS[@]}"; do
    for target in "${TARGETS[@]}"; do
        asset="$work/assets/fsbpyr-$py-$target"
        verify "$asset" --signer-workflow "$SIGNER" --source-ref "refs/tags/$TAG"
        verify "$asset" --signer-workflow "$SIGNER" --source-ref "refs/tags/$TAG" \
            --predicate-type https://cyclonedx.org/bom
    done
done
wait
n=$first
for py in "${PYTHONS[@]}"; do
    for target in "${TARGETS[@]}"; do
        asset="fsbpyr-$py-$target"
        verified $n && ok "$asset: provenance" \
            || fail "$asset: no provenance attestation by release.yml from refs/tags/$TAG"
        verified $((n + 1)) && ok "$asset: SBOM" \
            || fail "$asset: no SBOM attestation by release.yml from refs/tags/$TAG"
        n=$((n + 2))
    done
done

step "EL8 stubs ($COMPANION)"
# The stubs are no assets of the release (releases are immutable: it took none after its
# publication) but of the companion release, which el8.yml publishes, whole, on the same commit.
el8=()
for py in "${PYTHONS[@]}"; do
    for target in "${TARGETS[@]}"; do
        el8+=("fsbpyr-stub-$py-$target-glibc2.28.xz")
    done
done
has_el8=0
el8_dir="$work/el8-assets"
if ! flags="$(gh release view "$COMPANION" --repo "$REPO" --json isDraft,isPrerelease \
        --jq '"\(.isDraft) \(.isPrerelease)"' 2>"$work/el8-view.err")"; then
    if grep -q "release not found" "$work/el8-view.err"; then
        skip "no EL8 stubs: no companion release $COMPANION"
    else
        fail "cannot read $COMPANION: $(tail -1 "$work/el8-view.err")"
    fi
elif [ "$flags" != "false false" ]; then
    fail "$COMPANION is not a published release (draft, prerelease: $flags)"
else
    immutable="$(gh api "repos/$REPO/releases/tags/$COMPANION" --jq .immutable 2>/dev/null || true)"
    case "$immutable" in
        true) ok "$COMPANION is immutable" ;;
        false) fail "$COMPANION is not immutable: its stubs could still be replaced" ;;
        *) echo "  info  GitHub does not report whether $COMPANION is immutable" ;;
    esac
    [ "$LATEST" != "$COMPANION" ] && ok "$COMPANION is not the latest release ($LATEST)" \
        || fail "$COMPANION is the latest release: self update and install.sh would not find fsbpyr"
    if git -C "$work/src" fetch -q --depth 1 origin "refs/tags/$COMPANION:refs/tags/$COMPANION" 2>/dev/null \
            && [ "$(git -C "$work/src" rev-parse "$COMPANION^{commit}")" = "$COMMIT" ]; then
        ok "$COMPANION is on the commit of $TAG"
    else
        fail "the tag $COMPANION is not on the commit of $TAG (${COMMIT:0:8})"
    fi
    stubs_published="$(gh release view "$COMPANION" --repo "$REPO" --json assets --jq '.assets[].name' | sort)"
    wanted_assets="$(printf '%s\n' "$EL8_SUMS" "${el8[@]}" | sort)"
    missing="$(comm -23 <(echo "$wanted_assets") <(echo "$stubs_published"))"
    extra="$(comm -13 <(echo "$wanted_assets") <(echo "$stubs_published"))"
    [ -z "$missing" ] && ok "$COMPANION has all ${#el8[@]} stubs and $EL8_SUMS" || fail "$COMPANION misses: $(echo $missing)"
    [ -z "$extra" ] && ok "$COMPANION has no other asset" || fail "$COMPANION has not expected: $(echo $extra)"
    mkdir -p "$el8_dir"
    if [ -n "$missing" ]; then
        :  # reported above: with an asset missing the stubs are not checked, and nothing is downloaded
    elif gh release download "$COMPANION" --repo "$REPO" --dir "$el8_dir" >/dev/null 2>&1; then
        has_el8=1
    else
        fail "cannot download $COMPANION's assets"
    fi
fi
if [ "$has_el8" -eq 1 ]; then
    sums="$el8_dir/$EL8_SUMS"
    listed="$(awk '{ sub(/^\*/, "", $2); print $2 }' "$sums" | sort)"
    wanted="$(printf '%s\n' "${el8[@]%.xz}" | sort)"
    [ "$listed" = "$wanted" ] && ok "$EL8_SUMS lists the ${#el8[@]} stubs" \
        || fail "$EL8_SUMS lists '$(echo $listed)', not the stubs of every Python and target"
    first=$((verifying + 1))
    for asset in "${el8[@]}" "$EL8_SUMS"; do
        verify "$el8_dir/$asset" --signer-workflow "$EL8_SIGNER" --source-ref "refs/tags/$TAG"
    done
    wait
    n=$first
    for asset in "${el8[@]}"; do
        stub="${asset%.xz}"
        want="$(awk -v name="$stub" '{ file = $2; sub(/^\*/, "", file) } file == name { print $1 }' "$sums")"
        got="$(xz --decompress --stdout "$el8_dir/$asset" 2>/dev/null | sha256sum | cut -d' ' -f1)"
        [ -n "$want" ] && [ "$got" = "$want" ] && ok "$asset unpacks to the digest listed" \
            || fail "$asset does not unpack to the digest $EL8_SUMS lists"
        verified $n && ok "$asset: provenance" \
            || fail "$asset: no provenance attestation by el8.yml from refs/tags/$TAG"
        n=$((n + 1))
    done
    verified $n && ok "$EL8_SUMS: provenance" \
        || fail "$EL8_SUMS: no provenance attestation by el8.yml from refs/tags/$TAG"
fi

# --- This target's executables ----------------------------------------------------------------
step "executables ($HOST)"
for py in "${PYTHONS[@]}"; do
    exe="$work/assets/fsbpyr-$py-$HOST"
    shown="$(isolated "$exe" --version 2>&1)"
    [ "$shown" = "$VERSION-${COMMIT:0:8}" ] && ok "$py: --version $shown" \
        || fail "$py: --version says '$shown', not $VERSION-${COMMIT:0:8}"
    isolated "$exe" inspect --verify "$exe" >/dev/null 2>&1 && ok "$py: inspect --verify" \
        || fail "$py: inspect --verify fails"
    python="$(field "$(isolated "$exe" inspect "$exe" 2>/dev/null)" python)"
    [[ "$python" == "$py".* ]] && ok "$py: runs on Python $python" || fail "$py: runs on Python '$python'"
done

step "installer"
for py in "${PYTHONS[@]}"; do
    home="$work/install-$py"
    option=()
    [ "$py" = "$DEFAULT" ] || option=(--python "$py")
    if env -i PATH="$PATH" HOME="$home" FSBPYR_INSTALL_DIR="$home/prefix/bin" FSBPYR_GC=0 \
            FSBPYR_CACHE_DIR="$work/cache" sh "$work/assets/install.sh" --no-modify-path -q \
            "${option[@]}" >"$work/install-$py.log" 2>&1; then
        cmp -s "$home/prefix/bin/fsbpyr" "$work/assets/fsbpyr-$py-$HOST" \
            && ok "${option[*]:-default}: installs fsbpyr-$py-$HOST" \
            || fail "${option[*]:-default}: the installed file is not fsbpyr-$py-$HOST"
        for file in share/man/man1/fsbpyr.1 share/bash-completion/completions/fsbpyr \
                    share/zsh/site-functions/_fsbpyr share/fish/vendor_completions.d/fsbpyr.fish; do
            [ -s "$home/prefix/$file" ] || fail "${option[*]:-default}: no $file"
        done
        others="$(ls -A "$home" | grep -vx prefix)"
        [ -z "$others" ] || fail "${option[*]:-default}: wrote $(echo $others) into the home"
    else
        fail "${option[*]:-default}: install.sh failed: $(tail -1 "$work/install-$py.log")"
    fi
done

step "dispatch"
other_py=""
for py in "${PYTHONS[@]}"; do
    [ "$py" != "$DEFAULT" ] && managed "$py" && { other_py="$py"; break; }
done
if [ -z "$other_py" ]; then
    skip "no Python but $DEFAULT is installed with uv"
else
    project="$work/dispatch"
    mkdir -p "$project/src/hello"
    cat >"$project/pyproject.toml" <<EOF
[project]
name = "hello"
version = "1.0.0"
requires-python = "==$other_py.*"

[project.scripts]
hello = "hello:main"

[build-system]
requires = ["uv_build>=0.12.17,<0.13.0"]
build-backend = "uv_build"
EOF
    printf 'import sys\n\n\ndef main():\n    print(sys.version.split()[0])\n' >"$project/src/hello/__init__.py"
    fetched="$work/cache/downloads/fsbpyr-$other_py-$HOST-$TAG"
    if (cd "$project" && building uv lock -q && building "$work/assets/fsbpyr-$DEFAULT-$HOST" \
            build . -o "$project/hello") >"$work/dispatch.log" 2>&1; then
        cmp -s "$fetched" "$work/assets/fsbpyr-$other_py-$HOST" \
            && ok "$DEFAULT fetched $TAG's fsbpyr-$other_py-$HOST" \
            || fail "the fetched fsbpyr-$other_py-$HOST is not the release's"
        ran="$(isolated "$project/hello" 2>&1)"
        [[ "$ran" == "$other_py".* ]] && ok "the app runs on Python $ran" || fail "the app printed '$ran'"
    else
        fail "the ==$other_py.* build failed: $(tail -1 "$work/dispatch.log")"
    fi
fi

step "self update"
previous="$(gh release list --repo "$REPO" --exclude-drafts --exclude-pre-releases --limit 50 \
    --json tagName,createdAt \
    --jq 'map(select(.tagName | test("^v[0-9]+\\.[0-9]+\\.[0-9]+$"))) | sort_by(.createdAt) | reverse | .[].tagName' \
    | awk -v tag="$TAG" 'found { print; exit } $0 == tag { found = 1 }')"
if [ "$TAG" != "$LATEST" ]; then
    skip "$TAG is not the latest release ($LATEST): self update would install that one"
elif [ -z "$previous" ]; then
    skip "no release before $TAG"
else
    for py in "${PYTHONS[@]}"; do
        old="$work/update-$py/fsbpyr"
        mkdir -p "${old%/*}"
        if ! gh release download "$previous" --repo "$REPO" --pattern "fsbpyr-$py-$HOST" \
                --output "$old" >/dev/null 2>&1; then
            skip "$py: $previous has no fsbpyr-$py-$HOST"
            continue
        fi
        chmod +x "$old"
        if isolated "$old" self update >"$work/update-$py.log" 2>&1 \
                && cmp -s "$old" "$work/assets/fsbpyr-$py-$HOST"; then
            ok "$py: $previous updates to $TAG's fsbpyr-$py-$HOST"
        else
            fail "$py: $previous did not update to $TAG's: $(tail -1 "$work/update-$py.log")"
        fi
    done
fi

step "other target ($OTHER, podman)"
# An image of that architecture only: `--arch arm64 debian:trixie` would retag the host's image.
case "$OTHER" in
    aarch64-linux-gnu) image="docker.io/arm64v8/debian:trixie" arch=arm64 qemu=qemu-aarch64 ;;
    x86_64-linux-gnu) image="docker.io/amd64/debian:trixie" arch=amd64 qemu=qemu-x86_64 ;;
esac
if ! command -v podman >/dev/null; then
    skip "podman is not installed"
elif [ ! -e "/proc/sys/fs/binfmt_misc/$qemu" ]; then
    skip "no QEMU for $OTHER is registered (binfmt_misc $qemu)"
else
    ready=1
    if ! podman image exists "$image" 2>/dev/null; then
        pulled_image="$image"
        podman pull --quiet --arch "$arch" "$image" >/dev/null 2>&1 || { fail "cannot pull $image"; ready=0; }
    fi
    [ "$ready" -eq 1 ] && for py in "${PYTHONS[@]}"; do
        exe="/assets/fsbpyr-$py-$OTHER"
        out="$(podman run --rm --arch "$arch" --pids-limit 256 --memory 4g -v "$work/assets:/assets:ro" \
            -e FSBPYR_CACHE_DIR=/tmp/cache -e FSBPYR_GC=0 "$image" \
            sh -c "$exe --version && $exe inspect --verify $exe >/dev/null && $exe inspect $exe" \
            2>"$work/podman-$py.err")"
        version="$(head -1 <<<"$out")"
        python="$(field "$out" python)"
        if [ "$version" = "$VERSION-${COMMIT:0:8}" ] && [[ "$python" == "$py".* ]]; then
            ok "$py: $version, inspect --verify, Python $python"
        else
            fail "$py: '$version', Python '$python': $(tail -1 "$work/podman-$py.err")"
        fi
    done
    [ -z "$pulled_image" ] || podman rmi "$pulled_image" >/dev/null 2>&1 || true
    pulled_image=""
fi

step "el8 build (--glibc 2.28)"
# The release's own fsbpyr, as a user runs it: its stub comes from the companion release, over the
# network. Not 0.26.0's: it fetches from v0.26.0, which has no stubs (they were published in
# el8-v0.26.0 by hand, after the fact), so the stub is placed beside the executable, as the
# README of v0.26.0 tells its users.
el8_image="docker.io/library/rockylinux:8"
if [ "$has_el8" -eq 0 ]; then
    skip "no EL8 stubs"
elif ! managed "$DEFAULT"; then
    skip "Python $DEFAULT is not installed with uv"
else
    # a copy alone in its directory: the EL8 assets downloaded beside the release's executable
    # would be found there as placed files, and the stub would not be fetched
    mkdir -p "$work/el8/apps" "$work/el8/bin"
    exe="$work/el8/bin/fsbpyr-$DEFAULT-$HOST"
    stub="fsbpyr-stub-$DEFAULT-$HOST-glibc2.28"
    cp "$work/assets/fsbpyr-$DEFAULT-$HOST" "$exe"
    placed=0
    if [ "$VERSION" = "0.26.0" ]; then
        placed=1
        cp "$el8_dir/$stub.xz" "$work/el8/bin/"
        echo "  info  $TAG fetches its stub from $TAG, which has none: $stub.xz is placed beside the executable"
    fi
    cp -a "$work/src/tests/fixtures/projects/hello" "$work/el8/hello"
    if (cd "$work/el8/hello" && building "$exe" build . --python "$DEFAULT" --glibc 2.28 \
            -o "$work/el8/apps/hello") >"$work/el8.log" 2>&1; then
        ok "$DEFAULT: builds hello with --glibc 2.28"
        if [ "$placed" -eq 0 ]; then
            xz --decompress --stdout "$el8_dir/$stub.xz" \
                | cmp -s - "$work/cache/downloads/$stub-$TAG" \
                && ok "the stub it fetched is $COMPANION's $stub" \
                || fail "the stub it fetched is not $COMPANION's $stub"
        fi
        if ! command -v podman >/dev/null; then
            skip "podman is not installed: the app is not run in rockylinux:8"
        else
            ready=1
            if ! podman image exists "$el8_image" 2>/dev/null; then
                pulled_image="$el8_image"
                podman pull --quiet "$el8_image" >/dev/null 2>&1 || { fail "cannot pull $el8_image"; ready=0; }
            fi
            if [ "$ready" -eq 1 ]; then
                out="$(podman run --rm --pids-limit 256 --memory 4g -v "$work/el8/apps:/apps:ro,Z" \
                    -e FSBPYR_CACHE_DIR=/tmp/cache -e FSBPYR_GC=0 "$el8_image" \
                    sh -c '/apps/hello 5 x; echo "rc=$?"' 2>"$work/el8-podman.err")"
                [ "$out" = "hello ['5', 'x']"$'\n'"rc=5" ] && ok "the app runs in rockylinux:8" \
                    || fail "the app in rockylinux:8 printed '$out': $(tail -1 "$work/el8-podman.err")"
            fi
            [ -z "$pulled_image" ] || podman rmi "$pulled_image" >/dev/null 2>&1 || true
            pulled_image=""
        fi
    else
        fail "the --glibc 2.28 build failed: $(tail -1 "$work/el8.log")"
    fi
fi

step "installed"
installed="$(command -v fsbpyr 2>/dev/null || true)"
[ -n "$installed" ] || { [ -x "$HOME/.local/bin/fsbpyr" ] && installed="$HOME/.local/bin/fsbpyr"; }
if [ -z "$installed" ]; then
    echo "  info  no fsbpyr on PATH or in ~/.local/bin"
else
    shown="$(isolated "$installed" --version 2>&1)"
    if [ "${shown%%-*}" = "${LATEST#v}" ]; then
        echo "  info  $installed is $shown, the latest release"
    else
        echo "  info  $installed is $shown: \`$installed self update\` installs the latest, $LATEST"
    fi
fi

echo
if [ "$failed" -eq 0 ]; then
    echo "==> $TAG: every check passed ($skipped skipped)"
else
    echo "==> $TAG: $failed check(s) FAILED ($skipped skipped)"
    exit 1
fi
