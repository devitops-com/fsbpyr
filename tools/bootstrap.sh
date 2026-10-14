#!/usr/bin/env bash
#
# bootstrap.sh — from a source checkout to an installed fsbpyr (make bootstrap).
#
#     bash tools/bootstrap.sh               the whole bootstrap, on this host
#     bash tools/bootstrap.sh --from-source only steps 2-3 (what the podman container runs)
#     bash tools/bootstrap.sh --uv          only uv (what `make build` needs on the host)
#
# 1. Prerequisites. On Debian trixie: the OS packages (clang and lld 22, git, make, curl),
#    with sudo unless run as root, and uv from astral.sh (installed, or updated to the
#    newest). Anywhere else the same happens inside a Debian trixie podman image
#    (Containerfile); podman itself is the user's to install; uv is installed on the host
#    too, for `make build`.
# 2. The locked environment on the Python pinned in .python-version, then the runtime
#    (`fsbpyr runtime build`, which links with clang/lld 22).
# 3. fsbpyr built from source with that runtime -> dist/fsbpyr.
# 4. `make install`: dist/fsbpyr, its man page and completions under PREFIX (on the host,
#    also after a podman build).
#
# Environment: FSBPYR_BOOTSTRAP=podman builds in the container on Debian trixie too.
# PREFIX etc. pass through to `make install`. FSBPYR_BOOTSTRAP_IMAGE names the
# podman image (default fsbpyr-build); FSBPYR_BOOTSTRAP_VOLUME the volume that keeps uv's
# cache and the runtime between container runs (default fsbpyr-cache).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PACKAGES=(ca-certificates curl git make clang-22 lld-22)
UV_INSTALLER="https://astral.sh/uv/install.sh"
IMAGE="${FSBPYR_BOOTSTRAP_IMAGE:-fsbpyr-build}"
VOLUME="${FSBPYR_BOOTSTRAP_VOLUME:-fsbpyr-cache}"

say() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

is_trixie() {
    [ -r /etc/os-release ] || return 1
    # shellcheck disable=SC1091  # sourced in a subshell: only ID and VERSION_CODENAME are read
    (. /etc/os-release && [ "${ID:-}" = debian ] && [ "${VERSION_CODENAME:-}" = trixie ])
}

as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        command -v sudo >/dev/null || die "installing OS packages needs root or sudo"
        sudo "$@"
    fi
}

os_packages() {
    local missing=() p
    for p in "${PACKAGES[@]}"; do
        dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' \
            || missing+=("$p")
    done
    if [ "${#missing[@]}" -eq 0 ]; then
        say "OS packages present: ${PACKAGES[*]}"
        return
    fi
    say "Installing OS packages: ${missing[*]}"
    as_root apt-get update -q
    # clang/lld 22 came with Debian 13.7. A package index older than that (an update that
    # fetched the Release file but not the index lists itself as "Hit" from then on) has
    # neither: say so instead of apt's "Unable to locate package".
    for p in "${missing[@]}"; do
        apt-cache show "$p" >/dev/null 2>&1 || die "apt does not know $p. Debian 13.7 and newer ships it; if this is trixie, the package index is probably stale. Refresh it and run make bootstrap again:
    sudo rm /var/lib/apt/lists/deb.debian.org_debian_dists_trixie*
    sudo apt-get update"
    done
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
        "${missing[@]}"
}

uv_present() {
    # astral.sh's installer puts uv in ~/.local/bin, which may not be on PATH yet.
    export PATH="$HOME/.local/bin:$PATH"
    if command -v uv >/dev/null; then
        # The newest uv knows the newest CPython patch releases (.python-version pins one).
        # `self update` works for uv installed from astral.sh; another uv is kept as it is.
        uv self update -q 2>/dev/null \
            || echo "WARNING: cannot update $(command -v uv) (not installed from astral.sh?)" >&2
        say "uv present: $(uv --version)"
        return
    fi
    say "Installing uv from $UV_INSTALLER"
    curl -LsSf "$UV_INSTALLER" | env UV_NO_MODIFY_PATH=1 sh
    command -v uv >/dev/null || die "uv is not on PATH after installing it"
}

from_source() {
    uv_present
    say "Locked environment on Python $(cat .python-version)"
    uv python install "$(cat .python-version)"
    uv sync --locked
    say "Runtime (links with clang/lld 22; cached after the first run)"
    uv run --locked fsbpyr runtime build
    say "Building dist/fsbpyr from source"
    mkdir -p dist
    uv run --locked fsbpyr build . -o dist/fsbpyr
}

in_podman() {
    command -v podman >/dev/null \
        || die "this is not Debian trixie: bootstrap builds in a podman container, and podman is not installed (install it with your OS's package manager)"
    say "Building in podman image $IMAGE (Debian trixie)"
    # IPv4 only. On podman's default network the TLS handshakes of Python's OpenSSL (a
    # ClientHello with a post-quantum key share, larger than one segment) with GitHub's
    # download host time out; on pasta's IPv4-only network they complete.
    podman build -t "$IMAGE" -f Containerfile .
    podman run --rm --network pasta:-4 \
        -v "$ROOT":/src:Z -v "$VOLUME":/root/.cache "$IMAGE" \
        bash tools/bootstrap.sh --from-source
}

case "${1:-}" in
    --from-source) from_source; exit 0 ;;
    --uv) uv_present; exit 0 ;;
    "") ;;
    *) echo "usage: bootstrap.sh [--from-source | --uv]" >&2; exit 2 ;;
esac

[ "$(uname -m)" = x86_64 ] || die "fsbpyr builds for Linux x86-64 only (this is $(uname -m))"
if [ "${FSBPYR_BOOTSTRAP:-}" != podman ] && is_trixie; then
    say "Debian trixie: building on this host"
    os_packages
    from_source
else
    uv_present
    in_podman
fi
say "Installing"
make install
