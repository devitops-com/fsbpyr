/* Linked into a stub for tests/e2e/test_read_failures.py, with -Wl,--wrap=ZSTD_createDCtx: with
 * $PROBE_NO_CONTEXT set, zstd cannot make a decompression context, as when memory ran out before
 * the first startup module is decompressed. */
#include "vendor/zstd/zstd.h"

#include <stdlib.h>

ZSTD_DCtx *__real_ZSTD_createDCtx(void);

ZSTD_DCtx *__wrap_ZSTD_createDCtx(void) {
    return getenv("PROBE_NO_CONTEXT") != NULL ? NULL : __real_ZSTD_createDCtx();
}
