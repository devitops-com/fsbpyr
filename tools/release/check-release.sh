#!/usr/bin/env bash
#
# check-release.sh — check a published release as its users get it, after `make release`.
#
#     bash tools/release/check-release.sh [X.Y.Z]        (make release-check [VERSION=X.Y.Z])
#
# Without X.Y.Z the latest release is checked. Everything happens in a scratch directory of its
# own under $TMPDIR, removed at the end; nothing is installed or changed outside it but uv's
# cache (the rebuilds) and, for the other target, a podman image that is removed again unless it
# was there before. `make release-check` runs it in a directory under ~/tmp and in a cgroup with
# a task and memory limit, as the tests run. Checks, each reported as ok, FAIL or skip:
#
#   assets        every (Python, target) of the tag's build.yml has its executable, .xz, SBOM
#                 and licence notices; install.sh and SHA256SUMS; nothing else
#   checksums     SHA256SUMS lists every other asset, and every one matches
#   attestations  each executable's provenance and SBOM attestations, by release.yml from the
#                 tag (gh attestation verify)
#   executables   on this machine's target: --version is X.Y.Z-<the tag's commit>, inspect
#                 --verify passes, the Python is the asset's
#   rebuild       the README's: each of this target's executables rebuilds itself from the tag
#                 byte for byte, with the uv it records (skipped when this uv differs, or when
#                 the asset's Python is not installed with uv: nothing is installed for it)
#                 in an environment of only PATH and HOME
#   installer     install.sh, for every Python, into a scratch home with --no-modify-path:
#                 the release's executable, the man page and the completions, nothing else
#   dispatch      the default Python's executable builds a project pinned to another Python by
#                 fetching that Python's executable of this release
#   self-update   the previous release's executables update to this one's, Python by Python
#                 (only for the latest release: self update installs the latest)
#   other-target  the other target's executables under podman (QEMU), when podman runs it
#   installed     the fsbpyr on PATH, or in ~/.local/bin: its version (information only)
#
# Exit 0 when nothing failed, 1 otherwise. Needs gh (authenticated), git, curl, uv, cmp and
# sha256sum; podman for the other target.
set -uo pipefail

REPO="devitops-com/fsbpyr"
SIGNER="$REPO/.github/workflows/release.yml"
XYZ='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'

failed=0 skipped=0
ok()   { echo "  ok    $*"; }
fail() { echo "  FAIL  $*"; failed=$((failed + 1)); }
skip() { echo "  skip  $*"; skipped=$((skipped + 1)); }
step() { echo "==> $*"; }

[ $# -le 1 ] || { echo "usage: check-release.sh [X.Y.Z]" >&2; exit 2; }
for tool in gh git curl uv cmp sha256sum; do
    command -v "$tool" >/dev/null || { echo "ERROR: $tool is not installed" >&2; exit 2; }
done
if [ $# -eq 1 ]; then
    [[ "$1" =~ $XYZ ]] || { echo "ERROR: '$1' is not a version X.Y.Z" >&2; exit 2; }
    TAG="v$1"
else
    TAG="$(gh release view --repo "$REPO" --json tagName --jq .tagName)" \
        || { echo "ERROR: cannot read the latest release" >&2; exit 2; }
fi
VERSION="${TAG#v}"
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
mkdir -p "$work/assets" "$work/home"
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
if gh release download "$TAG" --repo "$REPO" --dir "$work/assets" >/dev/null 2>&1; then
    listed="$(awk '{ sub(/^\*/, "", $2); print $2 }' "$work/assets/SHA256SUMS" | sort)"
    unlisted="$(comm -23 <(echo "$published" | grep -vx SHA256SUMS) <(echo "$listed"))"
    [ -z "$unlisted" ] && ok "SHA256SUMS lists every other asset" || fail "not in SHA256SUMS: $(echo $unlisted)"
    if (cd "$work/assets" && sha256sum --quiet -c SHA256SUMS >/dev/null 2>&1); then
        ok "$(echo "$listed" | wc -l) files match SHA256SUMS"
    else
        fail "$(cd "$work/assets" && sha256sum -c SHA256SUMS 2>&1 | grep -v ': OK$' | head -3)"
    fi
else
    fail "cannot download $TAG's assets"
fi
chmod +x "$work"/assets/fsbpyr-*-linux-gnu 2>/dev/null

step "attestations"
for py in "${PYTHONS[@]}"; do
    for target in "${TARGETS[@]}"; do
        asset="fsbpyr-$py-$target"
        if gh attestation verify "$work/assets/$asset" --repo "$REPO" --signer-workflow "$SIGNER" \
            --source-ref "refs/tags/$TAG" >/dev/null 2>&1; then
            ok "$asset: provenance"
        else
            fail "$asset: no provenance attestation by release.yml from refs/tags/$TAG"
        fi
        if gh attestation verify "$work/assets/$asset" --repo "$REPO" --signer-workflow "$SIGNER" \
            --source-ref "refs/tags/$TAG" --predicate-type https://cyclonedx.org/bom >/dev/null 2>&1; then
            ok "$asset: SBOM"
        else
            fail "$asset: no SBOM attestation by release.yml from refs/tags/$TAG"
        fi
    done
done

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

step "rebuild (the README's)"
uv_here="$(uv --version | cut -d' ' -f2)"
for py in "${PYTHONS[@]}"; do
    exe="$work/assets/fsbpyr-$py-$HOST"
    uv_used="$(field "$(isolated "$exe" inspect "$exe" 2>/dev/null)" uv)"
    if [ -z "$uv_used" ]; then
        skip "$py: the executable records no uv (0.18.2 and older)"
        continue
    elif [ "$uv_used" != "$uv_here" ]; then
        skip "$py: built with uv $uv_used, this uv is $uv_here (install that uv to rebuild)"
        continue
    fi
    if ! managed "$py"; then
        skip "$py: Python $py is not installed with uv"
        continue
    fi
    if (cd "$work/src" && building "$exe" build . --python "$py" -o "$work/rebuilt-$py" \
            >"$work/rebuild-$py.log" 2>&1); then
        cmp -s "$exe" "$work/rebuilt-$py" && ok "$py: identical" || fail "$py: the rebuild differs"
    else
        fail "$py: the rebuild failed: $(tail -1 "$work/rebuild-$py.log")"
    fi
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
    --json tagName,createdAt --jq 'sort_by(.createdAt) | reverse | .[].tagName' \
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
