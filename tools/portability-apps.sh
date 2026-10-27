#!/usr/bin/env bash
#
# portability-apps.sh FSBPYR DIR PYTHON [GLIBC] — builds the four apps of
# tests/e2e/test_portability.py into DIR with the executable FSBPYR (a variant of PYTHON,
# "3.14": the fixtures allow any, so the build is told which), as that test's `apps` fixture
# does: hello, heavy, compat and heavy-pip (heavy kept with pip: a hash-pinned requirements.txt
# and no uv.lock). With GLIBC ("2.28") they are built with --glibc GLIBC, which needs that
# baseline's stub (beside FSBPYR, or the release's); the test then runs with FSBPYR_TEST_GLIBC
# set to it (the EL8 workflow's portability job).
# `make test-portability` and the portability job of full-suite.yml run it, then point
# FSBPYR_PORTABILITY_APPS at DIR. The apps are for the host's target (fsbpyr builds for the
# platform it runs on), so the job runs it on each target's runner and the test runs the
# images that have a variant for that architecture (tests/support/images.py). On a Python that
# onnxruntime has no wheel for (tests/support/no-wheels.txt) heavy and heavy-pip are not built,
# and the tests that run them are not generated.
set -euo pipefail

[ $# = 3 ] || [ $# = 4 ] || { echo "usage: $0 FSBPYR DIR PYTHON [GLIBC]" >&2; exit 2; }
fsbpyr="$(realpath "$1")"
dir="$2"
python="$3"
glibc=()
[ $# = 3 ] || glibc=(--glibc "$4")
fixtures="$(dirname "$0")/../tests/fixtures/projects"
no_wheels="$(awk -v python="$python" '$1 == python { $1 = ""; print }' \
    "$(dirname "$0")/../tests/support/no-wheels.txt")"
mkdir -p "$dir/src"
dir="$(realpath "$dir")"

for name in hello heavy compat; do
    [ "$name" != heavy ] || [[ " $no_wheels " != *" onnxruntime "* ]] || continue
    cp -a "$fixtures/$name" "$dir/src/$name"
    "$fsbpyr" build "$dir/src/$name" --python "$python" -o "$dir/$name" "${glibc[@]}"
done
[[ " $no_wheels " != *" onnxruntime "* ]] || exit 0
cp -a "$fixtures/heavy" "$dir/src/heavy-pip"
(cd "$dir/src/heavy-pip" \
    && uv export --quiet --frozen --no-dev --no-emit-project --format requirements-txt \
        -o requirements.txt \
    && rm uv.lock)
"$fsbpyr" build "$dir/src/heavy-pip" --python "$python" -o "$dir/heavy-pip" "${glibc[@]}"
