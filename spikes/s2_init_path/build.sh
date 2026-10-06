#!/usr/bin/env bash
# Build the S2 launcher stub, the spike app (stub + payload) and the ftrace helper.
source "$(dirname "$0")/../common.sh"
here="$SPIKES/s2_init_path"
cc -O2 -Wall -Wextra -o "$OUT/ftrace" "$SPIKES/tools/ftrace.c"
if [[ ! -x "$OUT/s2_stub" || "$here/launcher.c" -nt "$OUT/s2_stub" ]]; then
  link_stub "$OUT/s2_stub" NOPIE "$here/launcher.c" 2>&1 | grep -v 'different target triples' | grep -v '^$' || true
  llvm-strip-22 "$OUT/s2_stub"
fi
"$PBS/install/bin/python3.14" -I "$here/mkpayload.py" "$OUT/s2_stub" "$PBS/install/lib/python3.14" "$here/_spike_boot.py" "$OUT/s2_app"
