#!/usr/bin/env bash
#
# bootstrap.sh — from a source checkout to an installed fsbpyr (make bootstrap).
#
#     bash tools/bootstrap.sh               the whole bootstrap, on this host
#     bash tools/bootstrap.sh --from-source only steps 1-3 (what the podman container runs)
#     bash tools/bootstrap.sh --uv          only the check for uv (what `make build` needs)
#
# Nothing here installs or updates a tool on the host: a missing one is named, and installing
# it is the user's.
#
# 1. Prerequisites: uv on PATH and the Python pinned in .python-version, which uv finds but
#    never downloads; clang and lld 22, which `fsbpyr runtime build` looks for and names if
#    they are missing. This runs on the host on Debian trixie; anywhere else steps 1-3 run
#    inside a Debian trixie podman image (Containerfile) that has them all, and podman itself
#    is the user's to install.
# 2. The locked environment in the checkout's .venv, then the runtime (`fsbpyr runtime build`,
#    which links with clang/lld 22).
# 3. Three stages: fsbpyr from source builds dist/stage1/fsbpyr, which builds
#    dist/stage2/fsbpyr, which builds dist/stage3/fsbpyr. `inspect --verify` must confirm each
#    one's digest, and stages 2 and 3 must be identical; stage 3 becomes dist/fsbpyr.
# 4. `make install`: dist/fsbpyr, its man page and completions under PREFIX (on the host,
#    also after a podman build).
#
# Environment: FSBPYR_UV_VERSION=X.Y.Z (release CI sets it from tools/release/uv.version;
# a verifier sets the uv a release records) makes the check for uv fail unless uv on PATH is
# exactly that version. Without it any uv is accepted: user builds keep the user's own uv (D26).
# FSBPYR_BOOTSTRAP=podman builds in the container on Debian trixie too.
# PREFIX etc. pass through to `make install`. FSBPYR_BOOTSTRAP_IMAGE names the
# podman image (default fsbpyr-build); FSBPYR_BOOTSTRAP_VOLUME the volume that keeps uv's
# cache, its Python and the runtime between container runs (default fsbpyr-cache).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

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
    version="$(cat .python-version)"
    uv python find "$version" >/dev/null 2>&1 \
        || die "Python $version (.python-version) is not installed; install it (uv python install $version) and run this again"
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

from_source() {
    need_uv
    need_python
    say "Locked environment on Python $(cat .python-version)"
    uv sync --locked
    say "Runtime (links with clang/lld 22; cached after the first run)"
    uv run --locked fsbpyr runtime build
    rm -rf dist/stage1 dist/stage2 dist/stage3
    mkdir -p dist/stage1 dist/stage2 dist/stage3
    say "Stage 1: fsbpyr from source builds dist/stage1/fsbpyr"
    uv run --locked fsbpyr build . -o dist/stage1/fsbpyr
    verify dist/stage1/fsbpyr
    say "Stage 2: dist/stage1/fsbpyr builds dist/stage2/fsbpyr"
    dist/stage1/fsbpyr build . -o dist/stage2/fsbpyr
    verify dist/stage2/fsbpyr
    say "Stage 3: dist/stage2/fsbpyr builds dist/stage3/fsbpyr"
    dist/stage2/fsbpyr build . -o dist/stage3/fsbpyr
    verify dist/stage3/fsbpyr
    # Builds are reproducible: two executables built from the same checkout by builders of the
    # same version are the same bytes. Stage 2 and 3 differ only if building changes fsbpyr.
    cmp -s dist/stage2/fsbpyr dist/stage3/fsbpyr \
        || die "stage 2 and stage 3 differ:
$(sha256sum dist/stage2/fsbpyr dist/stage3/fsbpyr)"
    say "Stage 2 and stage 3 are identical: $(sha256sum dist/stage3/fsbpyr | cut -d' ' -f1)"
    cp dist/stage3/fsbpyr dist/fsbpyr.tmp && mv dist/fsbpyr.tmp dist/fsbpyr
}

in_podman() {
    command -v podman >/dev/null \
        || die "this is not Debian trixie: bootstrap builds in a podman container, and podman is not installed (install it with your OS's package manager)"
    say "Building in podman image $IMAGE (Debian trixie)"
    # IPv4 only. On podman's default network the TLS handshakes of Python's OpenSSL (a
    # ClientHello with a post-quantum key share, larger than one segment) with GitHub's
    # download host time out; on pasta's IPv4-only network they complete.
    podman build --target build -t "$IMAGE" -f Containerfile .
    # The container is its own environment: uv installs the pinned Python there, on the volume.
    podman run --rm --network pasta:-4 \
        -v "$ROOT":/src:Z -v "$VOLUME":/root/.cache "$IMAGE" \
        bash -c 'uv python install "$(cat .python-version)" && bash tools/bootstrap.sh --from-source'
}

case "${1:-}" in
    --from-source) from_source; exit 0 ;;
    --uv) need_uv; exit 0 ;;
    "") ;;
    *) echo "usage: bootstrap.sh [--from-source | --uv]" >&2; exit 2 ;;
esac

[ "$(uname -m)" = x86_64 ] || die "fsbpyr builds for Linux x86-64 only (this is $(uname -m))"
if [ "${FSBPYR_BOOTSTRAP:-}" != podman ] && is_trixie; then
    say "Debian trixie: building on this host"
    from_source
else
    in_podman
fi
say "Installing"
make install
