# Shared settings for Phase 0 spikes. Source this file; do not execute it.
set -euo pipefail
PBS_NAME="cpython-3.14.8+20261003-x86_64-unknown-linux-gnu-pgo+lto-full"
PBS="${PBS:-$HOME/.cache/fsbpyr/pbs/$PBS_NAME/python}"
PYINC="$PBS/install/include/python3.14"
LIBPY="$PBS/install/lib/python3.14/config-3.14-x86_64-linux-gnu/libpython3.14.a"
CC="${CC:-clang-22}"
LD_PATH="${LD_PATH:-/usr/bin/ld.lld-22}"
SPIKES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$SPIKES/.out"
mkdir -p "$OUT"
# Static deps of the built-in extension modules (from PYTHON.json build_info; tk/X11/db excluded).
STATIC_LIBS=(ssl crypto sqlite3 ffi z zstd bz2 lzma mpdec expat uuid edit panelw ncursesw)
link_stub() {  # link_stub OUTPUT PIE|NOPIE OBJ_OR_SRC...
  local out="$1" mode="$2"; shift 2
  local pie=(-fPIE -pie); [[ "$mode" == NOPIE ]] && pie=(-fno-pie -no-pie)
  local libs=(); for l in "${STATIC_LIBS[@]}"; do libs+=("$PBS/build/lib/lib$l.a"); done
  local extra; mapfile -t extra < <(python3 -I "$SPIKES/extra_objs.py" "$PBS" "$LIBPY" llvm-ar-22)
  "$CC" --target=x86_64-unknown-linux-gnu -O2 -flto=full "${pie[@]}" --ld-path="$LD_PATH" -I"$PYINC" -o "$out" "$@" "${extra[@]}" \
    -Wl,--export-dynamic -Wl,-z,noexecstack -Wl,-z,relro -Wl,-z,now \
    -Wl,--start-group "$LIBPY" "${libs[@]}" -Wl,--end-group \
    -lpthread -ldl -lutil -lm -lrt
}
