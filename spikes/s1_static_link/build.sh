#!/usr/bin/env bash
# Build S1 stubs (PIE and non-PIE) from PBS static libpython.
source "$(dirname "$0")/../common.sh"
for mode in PIE NOPIE; do
  out="$OUT/s1_python_${mode,,}"
  start=$SECONDS
  link_stub "$out" "$mode" "$SPIKES/s1_static_link/main.c"
  echo "$mode link: $((SECONDS - start))s"; ls -la "$out"
done
