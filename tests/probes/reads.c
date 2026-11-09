/* The launcher's blob reads when memory runs out (launcher/payload.c), for
 * tests/e2e/test_read_failures.py:
 *
 *   reads IMAGE        for every file record, one line: its path, its codec, how fsb_read_blob
 *                      ended ("ok", else its errno: 0 for corrupt data) and how a stream of it
 *                      ended ("end", else "corrupt", "memory" or "system")
 *   reads IMAGE PATH   fsb_read_blob of file PATH's blob, read as if the image were the mapped
 *                      executable, again and again until its reads made the payload's pages be
 *                      dropped once (madvise, which fails here as in an app that locked its
 *                      memory: EINVAL); prints "sweeps N" and the errno of every failed read,
 *                      once each, in ascending order
 *
 * Both read with the process's decoder, as module.c and main.c do. Built with
 * -Wl,--wrap=ZSTD_createDCtx,--wrap=ZSTD_decompress,--wrap=ZSTD_decompressStream,
 * --wrap=lzma_stream_buffer_decode,--wrap=madvise: with $PROBE_NO_MEMORY set to one of the first
 * four's names, that function fails as it does when it cannot allocate (no context, zstd's
 * memory_allocation error, LZMA_MEM_ERROR), leaving errno alone, as decoding on a helper thread
 * leaves its caller's.
 */
#include "payload.h"
#include "vendor/zstd/zstd.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

typedef struct lzma_allocator lzma_allocator;
int __real_lzma_stream_buffer_decode(uint64_t *memlimit, uint32_t flags,
                                     const lzma_allocator *allocator, const uint8_t *in,
                                     size_t *in_pos, size_t in_size, uint8_t *out,
                                     size_t *out_pos, size_t out_size);
ZSTD_DCtx *__real_ZSTD_createDCtx(void);
size_t __real_ZSTD_decompress(void *dst, size_t capacity, const void *src, size_t size);
size_t __real_ZSTD_decompressStream(ZSTD_DStream *stream, ZSTD_outBuffer *out, ZSTD_inBuffer *in);
int __real_madvise(void *address, size_t length, int advice);

static bool no_memory(const char *what) {
    const char *failing = getenv("PROBE_NO_MEMORY");
    return failing != NULL && strcmp(failing, what) == 0;
}

#define NO_MEMORY ((size_t)0 - (size_t)ZSTD_error_memory_allocation) /* as zstd's ERROR() */

ZSTD_DCtx *__wrap_ZSTD_createDCtx(void) {
    return no_memory("ZSTD_createDCtx") ? NULL : __real_ZSTD_createDCtx();
}

size_t __wrap_ZSTD_decompress(void *dst, size_t capacity, const void *src, size_t size) {
    if (no_memory("ZSTD_decompress")) return NO_MEMORY;
    return __real_ZSTD_decompress(dst, capacity, src, size);
}

size_t __wrap_ZSTD_decompressStream(ZSTD_DStream *stream, ZSTD_outBuffer *out, ZSTD_inBuffer *in) {
    if (no_memory("ZSTD_decompressStream")) return NO_MEMORY;
    return __real_ZSTD_decompressStream(stream, out, in);
}

int __wrap_lzma_stream_buffer_decode(uint64_t *memlimit, uint32_t flags,
                                     const lzma_allocator *allocator, const uint8_t *in,
                                     size_t *in_pos, size_t in_size, uint8_t *out,
                                     size_t *out_pos, size_t out_size) {
    if (no_memory("lzma_stream_buffer_decode")) return 5; /* LZMA_MEM_ERROR */
    return __real_lzma_stream_buffer_decode(memlimit, flags, allocator, in, in_pos, in_size, out,
                                            out_pos, out_size);
}

static bool locked; /* the second command: every madvise fails */
static unsigned sweeps;

int __wrap_madvise(void *address, size_t length, int advice) {
    if (!locked) return __real_madvise(address, length, advice);
    sweeps++;
    errno = EINVAL;
    return -1;
}

static const char *streamed(const struct fsb_payload *p, const struct fsb_blob *b) {
    static const char *const ends[] = {[FSB_STREAM_END] = "end", [FSB_STREAM_CORRUPT] = "corrupt",
                                       [FSB_STREAM_MEMORY] = "memory",
                                       [FSB_STREAM_SYSTEM] = "system"};
    struct fsb_stream *s = fsb_stream_open(p, NULL, b);
    if (s == NULL) return "memory";
    const uint8_t *data;
    uint64_t length;
    int status;
    while ((status = fsb_stream_next(s, &data, &length)) == FSB_STREAM_DATA) {}
    fsb_stream_close(s);
    return ends[status];
}

/* The second command: the errnos of failed reads, a bit each below 64. */
static int sweep(const struct fsb_payload *p, const struct fsb_blob *b) {
    uint8_t *dst = malloc(b->size + 1);
    if (dst == NULL) return 3;
    uint64_t seen = 0;
    locked = true;
    for (unsigned reads = 0; sweeps == 0 && reads < 4096; reads++)
        if (!fsb_read_blob(p, NULL, b, dst)) seen |= UINT64_C(1) << (errno < 64 ? errno : 63);
    locked = false;
    printf("sweeps %u\n", sweeps);
    for (int error = 0; error < 64; error++)
        if (seen & (UINT64_C(1) << error)) printf("errno %d\n", error);
    free(dst);
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2 && argc != 3) return 2;
    FILE *file = fopen(argv[1], "rb");
    if (file == NULL || fseek(file, 0, SEEK_END) != 0) return 3;
    long size = ftell(file);
    uint8_t *image = malloc((size_t)size);
    if (image == NULL || fseek(file, 0, SEEK_SET) != 0 ||
        fread(image, 1, (size_t)size, file) != (size_t)size)
        return 3;
    fclose(file);
    static struct fsb_payload payload;
    const char *error = fsb_payload_parse(&payload, image, (uint64_t)size);
    if (error != NULL) {
        fprintf(stderr, "%s\n", error);
        return 1;
    }
    int status = 0;
    for (uint32_t i = 0; i < fsb_count(&payload, FSB_SECTION_FILES); i++) {
        struct fsb_file f;
        struct fsb_blob b;
        const char *path;
        if (!fsb_get_file(&payload, i, &f) || !fsb_str(&payload, f.path, &path) ||
            !fsb_get_blob(&payload, f.blob, &b))
            return 1;
        if (argc == 3) {
            if (strcmp(path, argv[2]) != 0) continue;
            payload.mapped = true; /* only what count_read does with it: no blob is read raw */
            status = sweep(&payload, &b);
            break;
        }
        uint8_t *dst = malloc(b.size + 1);
        if (dst == NULL) return 3;
        bool read = fsb_read_blob(&payload, NULL, &b, dst);
        int read_errno = errno;
        char ended[16] = "ok";
        if (!read) snprintf(ended, sizeof ended, "%d", read_errno);
        printf("%s %u %s %s\n", path, (unsigned)b.codec, ended, streamed(&payload, &b));
        free(dst);
    }
    free(image);
    return status;
}
