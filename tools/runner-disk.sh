#!/usr/bin/env bash
#
# runner-disk.sh — room on the disk of a GitHub-hosted runner, for the suite and corpus jobs
# of full-suite.yml. Run as root in the job's container, with the runner's file system
# mounted at ROOT (never /: this deletes software).
#
# Runners of one label differ: most x86_64 ones have a 145 GB disk with 85 GB free, but in run
# 41603035547 two jobs got a 72 GB disk with 12 GB free, and the suite ran out of space. A job
# cannot ask for a disk, so this makes room on the one it got: it removes software the runner
# image installs and these containers never use (they run Debian's packages and their own;
# Docker and the runner's own files stay). Then it fails at once, with the figures, unless the
# file system of DIR has NEED GiB free beyond a tenth of its size. Below a tenth free, fsbpyr
# collects its caches hourly, and the gc tests require more (requires.enough_free_disk).
#
#   tools/runner-disk.sh ROOT DIR NEED
set -euo pipefail

# Paths below ROOT: the Android SDK, .NET, the tool cache of the actions/setup-* actions
# (Python, Node, Go, Ruby, PyPy, CodeQL), Haskell, Swift, Java, PowerShell, Chromium, the
# Google Cloud SDK, the Azure CLI, Edge, Chrome, Miniconda, global npm packages.
UNUSED=(
    usr/local/lib/android
    usr/share/dotnet
    opt/hostedtoolcache
    usr/local/.ghcup
    usr/share/swift
    usr/lib/jvm
    usr/local/share/powershell
    usr/local/share/chromium
    usr/lib/google-cloud-sdk
    opt/az
    opt/microsoft
    opt/google
    usr/share/miniconda
    usr/local/lib/node_modules
)

[ "$#" = 3 ] || { echo "usage: $0 ROOT DIR NEED" >&2; exit 2; }
root=$1 dir=$2 need=$3
if [ ! -d "$root" ] || [ "$root" -ef / ]; then
    echo "ERROR: $root: not the runner's file system mounted in this container" >&2
    exit 2
fi
case $need in
    '' | *[!0-9]*) echo "ERROR: NEED is a whole number of GiB, not '$need'" >&2; exit 2 ;;
esac

# What each removal frees is in the log. One that fails leaves the decision to the check below.
df -h "$dir"
for path in "${UNUSED[@]}"; do
    if [ -e "$root/$path" ] || [ -L "$root/$path" ]; then
        du -sh "$root/$path" || true
        rm -rf "${root:?}/$path" || echo "::warning::$path: not all of it removed"
    fi
done
df -h "$dir"

# In MiB: the file system's size and what an unprivileged user may still write.
out=$(df -B1M --output=size,avail "$dir")
read -r size avail <<< "${out##*$'\n'}"
want=$(( need * 1024 + size / 10 ))
if [ "$avail" -lt "$want" ]; then
    echo "::error::$dir: $avail MiB free of $size MiB; the job needs $need GiB beyond a tenth" \
        "of the disk free, $want MiB. Run the job again: another runner may have the room."
    exit 1
fi
echo "$dir: $avail MiB free of $size MiB; the job needs $want MiB ($need GiB beyond a tenth)"
