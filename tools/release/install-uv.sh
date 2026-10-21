#!/bin/sh
#
# install-uv.sh — install the uv that tools/release/uv.version names, for this machine's
# architecture, into a directory.
#
#     sh tools/release/install-uv.sh BINDIR
#
# uv.version is one line: the uv release, then the SHA-256 of its x86_64 and of its aarch64
# Linux glibc tarball. The tarball comes from uv's GitHub release and is checked against the
# digest recorded here before anything is unpacked; BINDIR gets uv and uvx. Prints the uv
# version on stdout, which is what release CI sets FSBPYR_UV_VERSION to. Runs under dash (the
# workflows' Debian trixie containers). Exit status: 0 installed, 1 failed, 2 bad usage.
set -eu

[ $# -eq 1 ] || { echo "usage: install-uv.sh BINDIR" >&2; exit 2; }
bindir="$1"
here="$(dirname "$0")"

read -r version sha_x86_64 sha_aarch64 < "$here/uv.version"
arch="$(uname -m)"
case "$arch" in
    x86_64) sha256="$sha_x86_64" ;;
    aarch64) sha256="$sha_aarch64" ;;
    *) echo "ERROR: no uv is pinned for $arch" >&2; exit 1 ;;
esac
[ -n "$sha256" ] || { echo "ERROR: uv.version has no digest for $arch" >&2; exit 1; }

name="uv-$arch-unknown-linux-gnu"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
curl -fsSL -o "$scratch/uv.tar.gz" \
    "https://github.com/astral-sh/uv/releases/download/$version/$name.tar.gz" >&2
echo "$sha256  $scratch/uv.tar.gz" | sha256sum -c - >&2
mkdir -p "$bindir"
tar -xzf "$scratch/uv.tar.gz" --strip-components=1 -C "$bindir" "$name/uv" "$name/uvx"
echo "$version"
