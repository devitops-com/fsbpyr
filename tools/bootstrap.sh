#!/usr/bin/env bash
#
# bootstrap.sh — from a source checkout to an installed fsbpyr (make bootstrap).
#
#     bash tools/bootstrap.sh               the whole bootstrap, on this host
#     bash tools/bootstrap.sh --from-source only steps 1-3 (what the podman container runs)
#     bash tools/bootstrap.sh --uv          only the check for uv (what `make build` needs)
#     bash tools/bootstrap.sh --llvm23      only the fetch of clang/lld 23, printing its clang
#     bash tools/bootstrap.sh --python 3.12 the variant for another Python (3.12 to 3.15):
#                                           steps 1-3 only, in .venv-3.12, into dist/stage1-3.12
#                                           ... and dist/fsbpyr-3.12; no install (Phase 9)
#
# Nothing here installs or updates a tool on the host: a missing one is named, and installing
# it is the user's.
#
# 1. Prerequisites: uv on PATH and the Python pinned in .python-version, which uv finds but
#    never downloads; curl and zstd, which fetch clang/lld 23 (below). This runs on the host
#    on Debian trixie; anywhere else steps 1-3 run inside a Debian trixie podman image
#    (Containerfile) that has them all, and podman itself is the user's to install.
# 2. The locked environment in the checkout's .venv, then the runtime (`fsbpyr runtime build`,
#    which links with clang/lld 23).
# 3. Three stages: fsbpyr from source builds dist/stage1/fsbpyr, which builds
#    dist/stage2/fsbpyr, which builds dist/stage3/fsbpyr. `inspect --verify` must confirm each
#    one's digest, and stages 2 and 3 must be identical; stage 3 becomes dist/fsbpyr.
# 4. `make install`: dist/fsbpyr, its man page and completions under PREFIX (on the host,
#    also after a podman build).
#
# Environment: FSBPYR_UV_VERSION=X.Y.Z (release CI sets it from tools/release/uv.version;
# a verifier sets the uv a release records) makes the check for uv fail unless uv on PATH is
# exactly that version. Without it any uv is accepted: user builds keep the user's own uv (D26).
# The runtime is linked with clang/lld 23: the pinned python-build-standalone release
# (src/fsbpyr/runtime/pins.py, 20261026) has LLVM 23 bitcode objects, and Debian trixie ships
# clang/lld 22 only (SEC-4). The toolchain is LLVM's release tarball for the host's
# architecture that tools/release/llvm23 names (version, then the sha256 of the x86-64 and of
# the aarch64 tarball); it is downloaded once into fsbpyr's cache,
# ${XDG_CACHE_HOME:-~/.cache}/fsbpyr/toolchains, and the bootstrap fails if its sha256
# differs. FSBPYR_LLVM23=0 links with a clang/lld 23 or newer on PATH instead.
# FSBPYR_LLVM23_URL replaces the download URL (tests).
# FSBPYR_BOOTSTRAP=podman builds in the container on Debian trixie too. Linux x86-64 and
# aarch64 hosts build for themselves (no cross builds).
# PREFIX etc. pass through to `make install`. FSBPYR_BOOTSTRAP_IMAGE names the
# podman image (default fsbpyr-build); FSBPYR_BOOTSTRAP_VOLUME the volume that keeps uv's
# cache, its Python and the runtime between container runs (default fsbpyr-cache).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --python X.Y bootstraps that Python's variant: its own locked environment, runtime and stage
# directories, so it never disturbs the default's .venv and dist/stage*. Without it nothing
# below changes.
PYTHON_VARIANT=""
SUFFIX=""
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --python)
            [ $# -ge 2 ] && [[ "$2" =~ ^3\.[0-9]+$ ]] \
                || { echo "usage: bootstrap.sh --python X.Y (3.12 to 3.15)" >&2; exit 2; }
            PYTHON_VARIANT="$2"
            SUFFIX="-$2"
            shift 2 ;;
        *) ARGS+=("$1"); shift ;;
    esac
done
set -- ${ARGS[@]+"${ARGS[@]}"}
if [ -n "$PYTHON_VARIANT" ]; then
    export UV_PROJECT_ENVIRONMENT=".venv$SUFFIX"
fi

IMAGE="${FSBPYR_BOOTSTRAP_IMAGE:-fsbpyr-build}"
VOLUME="${FSBPYR_BOOTSTRAP_VOLUME:-fsbpyr-cache}"

# uv must not install a Python on its own: the one .python-version pins is the user's to install.
export UV_PYTHON_DOWNLOADS=never

say() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

is_trixie() {
    [ -r /etc/os-release ] || return 1
    # shellcheck disable=SC1091  # sourced in a subshell: only ID and VERSION_CODENAME are read
    (. /etc/os-release && [ "${ID:-}" = debian ] && [ "${VERSION_CODENAME:-}" = trixie ])
}

need_uv() {
    command -v uv >/dev/null \
        || die "uv is not on PATH; install it (https://docs.astral.sh/uv/getting-started/installation/) and run this again"
    # Release CI and verifiers name the uv that built a release; its bytes depend on it.
    if [ -n "${FSBPYR_UV_VERSION:-}" ]; then
        local have
        have="$(uv --version | cut -d' ' -f2)"
        [ "$have" = "$FSBPYR_UV_VERSION" ] \
            || die "FSBPYR_UV_VERSION is $FSBPYR_UV_VERSION, but uv on PATH is $have; install uv $FSBPYR_UV_VERSION and run this again"
    fi
}

need_python() {
    local version
    version="${PYTHON_VARIANT:-$(cat .python-version)}"
    uv python find "$version" >/dev/null 2>&1 \
        || die "Python $version (${PYTHON_VARIANT:+--python}${PYTHON_VARIANT:-.python-version}) is not installed; install it (uv python install $version) and run this again"
}

# verify EXECUTABLE: `inspect --verify` recomputes its digest over the whole file; the report
# must say so (exit status 0 and "digest <hex> verified").
verify() {
    local report
    report="$("$1" inspect --verify "$1")" || die "$1 does not verify"
    grep -Eq '^digest +[0-9a-f]{64} verified$' <<<"$report" \
        || die "inspect --verify did not confirm the digest of $1"
    say "$1: $(grep -E '^digest' <<<"$report" | tr -s ' ')"
}

# llvm23: sets CLANG23 to the clang of LLVM's pinned release tarball, fetching it into fsbpyr's
# cache on the first use. Only what linking the launcher needs is unpacked (about 600 MB of
# 12 GB): clang, lld and clang's resource directory (compiler-rt's builtins, sanitizer and
# profile runtimes), and llvm-profdata and llvm-cov, which read the profiles of what this clang
# instrumented (full-suite.yml). The tarball is zstd with a 1 GiB window.
llvm23() {
    local version sha_x86_64 sha_aarch64 sha256 url name cache dir tmp
    read -r version sha_x86_64 sha_aarch64 _ < tools/release/llvm23 \
        || die "tools/release/llvm23 is empty"
    case "$(uname -m)" in
        x86_64) name="LLVM-$version-Linux-X64" sha256="$sha_x86_64" ;;
        aarch64) name="LLVM-$version-Linux-ARM64" sha256="$sha_aarch64" ;;
        *) die "tools/release/llvm23 pins no LLVM tarball for $(uname -m)" ;;
    esac
    [[ "$sha256" =~ ^[0-9a-f]{64}$ ]] || die "tools/release/llvm23 has no sha256 for $(uname -m)"
    url="${FSBPYR_LLVM23_URL:-https://github.com/llvm/llvm-project/releases/download/llvmorg-$version/$name.tar.zst}"
    case "${XDG_CACHE_HOME:-}" in
        /*) cache="$XDG_CACHE_HOME/fsbpyr/toolchains" ;;
        *) cache="$HOME/.cache/fsbpyr/toolchains" ;;
    esac
    dir="$cache/llvm-$version-${sha256:0:12}"
    # A tree without llvm-cov is the opt-in switch's of 0.21.0 and 0.22.0: unpacked again.
    if [ ! -x "$dir/bin/clang" ] || [ ! -x "$dir/bin/llvm-cov" ]; then
        local tool
        for tool in curl zstd tar sha256sum; do
            command -v "$tool" >/dev/null \
                || die "$tool is not on PATH; install it and run this again (clang/lld 23 needs it)"
        done
        mkdir -p "$cache"
        tmp="$(mktemp -d "$cache/.fetch.XXXXXX")"
        say "Fetching clang/lld $version ($url)" >&2  # stdout is the clang (--llvm23)
        curl -fsSL --retry 3 -o "$tmp/llvm.tar.zst" "$url" \
            || { rm -rf "$tmp"; die "cannot download $url"; }
        echo "$sha256  $tmp/llvm.tar.zst" | sha256sum -c --status - \
            || { rm -rf "$tmp"; die "sha256 of $url is not the $sha256 of tools/release/llvm23"; }
        mkdir "$tmp/tree"
        zstd -dc --long=30 "$tmp/llvm.tar.zst" \
            | tar -xf - -C "$tmp/tree" --strip-components=1 --wildcards \
                "$name/bin/clang" "$name/bin/clang-[0-9]*" "$name/bin/lld" "$name/bin/ld.lld" \
                "$name/bin/llvm-profdata" "$name/bin/llvm-cov" "$name/lib/clang" \
            || { rm -rf "$tmp"; die "cannot unpack $url"; }
        [ -x "$tmp/tree/bin/clang" ] && [ -x "$tmp/tree/bin/ld.lld" ] \
            || { rm -rf "$tmp"; die "$url holds no bin/clang and bin/ld.lld"; }
        rm -rf "$dir"
        mv "$tmp/tree" "$dir"
        rm -rf "$tmp"
    fi
    CLANG23="$dir/bin/clang"
}

from_source() {
    need_uv
    need_python
    local clang_args=() linker="clang/lld 23 or newer on PATH"
    if [ "${FSBPYR_LLVM23:-1}" != 0 ]; then
        llvm23
        clang_args=(--clang "$CLANG23")
        linker="LLVM's clang/lld $(cut -d' ' -f1 tools/release/llvm23)"
    fi
    local py=() s1="dist/stage1$SUFFIX" s2="dist/stage2$SUFFIX" s3="dist/stage3$SUFFIX"
    [ -z "$PYTHON_VARIANT" ] || py=(--python "$PYTHON_VARIANT")
    say "Locked environment on Python ${PYTHON_VARIANT:-$(cat .python-version)}"
    uv sync "${py[@]}" --locked
    say "Runtime (links with $linker; cached after the first run)"
    uv run "${py[@]}" --locked fsbpyr runtime build "${py[@]}" "${clang_args[@]}"
    rm -rf "$s1" "$s2" "$s3"
    mkdir -p "$s1" "$s2" "$s3"
    say "Stage 1: fsbpyr from source builds $s1/fsbpyr"
    uv run "${py[@]}" --locked fsbpyr build . "${py[@]}" -o "$s1/fsbpyr"
    verify "$s1/fsbpyr"
    say "Stage 2: $s1/fsbpyr builds $s2/fsbpyr"
    "$s1/fsbpyr" build . "${py[@]}" -o "$s2/fsbpyr"
    verify "$s2/fsbpyr"
    say "Stage 3: $s2/fsbpyr builds $s3/fsbpyr"
    "$s2/fsbpyr" build . "${py[@]}" -o "$s3/fsbpyr"
    verify "$s3/fsbpyr"
    # Builds are reproducible: two executables built from the same checkout by builders of the
    # same version are the same bytes. Stage 2 and 3 differ only if building changes fsbpyr.
    cmp -s "$s2/fsbpyr" "$s3/fsbpyr" \
        || die "stage 2 and stage 3 differ:
$(sha256sum "$s2/fsbpyr" "$s3/fsbpyr")"
    say "Stage 2 and stage 3 are identical: $(sha256sum "$s3/fsbpyr" | cut -d' ' -f1)"
    cp "$s3/fsbpyr" "dist/fsbpyr$SUFFIX.tmp" && mv "dist/fsbpyr$SUFFIX.tmp" "dist/fsbpyr$SUFFIX"
}

in_podman() {
    command -v podman >/dev/null \
        || die "this is not Debian trixie: bootstrap builds in a podman container, and podman is not installed (install it with your OS's package manager)"
    # The image is built for the host's architecture (amd64 or arm64), the Containerfile's.
    say "Building in podman image $IMAGE (Debian trixie)"
    # IPv4 only. On podman's default network the TLS handshakes of Python's OpenSSL (a
    # ClientHello with a post-quantum key share, larger than one segment) with GitHub's
    # download host time out; on pasta's IPv4-only network they complete.
    podman build --target build -t "$IMAGE" -f Containerfile .
    # The container is its own environment: uv installs the pinned Python there, on the volume.
    podman run --rm --network pasta:-4 -e FSBPYR_LLVM23 \
        -v "$ROOT":/src:Z -v "$VOLUME":/root/.cache "$IMAGE" \
        bash -c 'uv python install "$1" && shift && bash tools/bootstrap.sh "$@"' \
        bash "${PYTHON_VARIANT:-$(cat .python-version)}" --from-source \
        ${PYTHON_VARIANT:+--python "$PYTHON_VARIANT"}
}

case "${1:-}" in
    --from-source) from_source; exit 0 ;;
    --uv) need_uv; exit 0 ;;
    --llvm23) llvm23; echo "$CLANG23"; exit 0 ;;
    "") ;;
    *) echo "usage: bootstrap.sh [--python X.Y] [--from-source | --uv | --llvm23]" >&2; exit 2 ;;
esac

case "$(uname -m)" in
    x86_64 | aarch64) ;;
    *) die "fsbpyr builds for Linux x86-64 and aarch64 only (this is $(uname -m))" ;;
esac
if [ "${FSBPYR_BOOTSTRAP:-}" != podman ] && is_trixie; then
    say "Debian trixie: building on this host"
    from_source
else
    in_podman
fi
if [ -n "$PYTHON_VARIANT" ]; then
    say "Built dist/fsbpyr$SUFFIX; a variant is not installed"
    exit 0
fi
say "Installing"
make install
