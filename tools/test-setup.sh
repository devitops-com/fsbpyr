#!/usr/bin/env bash
#
# test-setup.sh — what the full test suite needs beyond a bootstrap build. Run as root: by
# the Containerfile's `test` stage, and by the full-suite workflow on its Debian trixie
# container. The packages of bootstrap (git, make, curl, clang-22, lld-22) are already there.
#
#   - libclang-rt-22-dev and llvm-22: Debian's compiler-rt and llvm-objdump (the runtime, the
#     ASan/UBSan launcher and llvm-cov use LLVM 23's own, tools/release/llvm23);
#   - gcc, strace, groff-base, zstd, xz-utils, wget, busybox and jq: tests that skip without them;
#   - a user `tester` (uid 1000) in one supplementary group (testers), the user the suite runs
#     as: the ownership tests need a user that is not root and has a group besides its own;
#   - a directory of root's in /tmp and a searchable directory of another user under /var/lib,
#     which the cache-directory tests walk through and must refuse.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "ERROR: test-setup.sh must run as root" >&2; exit 1; }

apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
    libclang-rt-22-dev llvm-22 gcc strace groff-base zstd xz-utils wget busybox openssl jq
rm -rf /var/lib/apt/lists/*

groupadd --gid 2000 testers
useradd --create-home --uid 1000 --groups testers tester

mkdir -m 755 /tmp/fsbpyr-test-root-owned
useradd --system --no-create-home --shell /usr/sbin/nologin fsbpyr-other
mkdir -m 755 /var/lib/fsbpyr-other
chown fsbpyr-other: /var/lib/fsbpyr-other
