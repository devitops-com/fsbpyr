/* libFuzzer harness (D38): the launcher's own payload parsing, record accessors, lookups, path
 * checks and blob decoding, on any bytes, under ASan and UBSan. Built and run by
 * tests/e2e/test_fuzz.py: once over every edge case (tests/support/edges.py), then exploring
 * from them; the same binary replays a single input given as a file.
 *
 * Whatever the input, the launcher must refuse it or read it, never crash: every record of
 * every table up to MAX_RECORDS is visited, every blob up to MAX_READ bytes decoded, whole and
 * through a stream (built with a small window, so that small inputs cross windows). */
#include "cache.h"
#include "integrity.h"
#include "payload.h"

#include <stdlib.h>

#define MAX_RECORDS 4096u
#define MAX_READ (16u << 20) /* larger blobs are left alone: the harness's memory is bounded */

static volatile uint8_t touched; /* what the windows held: their bytes are really read */

static uint64_t bounded(const struct fsb_payload *p, enum fsb_section section) {
    uint64_t count = fsb_count(p, section);
    return count < MAX_RECORDS ? count : MAX_RECORDS;
}

static void lookups(const struct fsb_payload *p, const char *key, size_t length) {
    (void)fsb_find_module(p, key, length);
    (void)fsb_find_file(p, key, length);
    (void)fsb_find_dist(p, key, length);
    for (int s = 0; s < FSB_SECTION_COUNT; s++) (void)fsb_lower_bound(p, s, key, length);
    (void)fsb_cache_safe(key, length);
}

static void read_blob(const struct fsb_payload *p, uint32_t index) {
    struct fsb_blob b;
    if (!fsb_get_blob(p, index, &b) || b.size > MAX_READ) return;
    uint8_t *content = malloc(b.size ? (size_t)b.size : 1);
    if (content == NULL) return;
    if (fsb_read_blob(p, NULL, &b, content)) {
        uint8_t digest[FSB_DIGEST_SIZE];
        (void)fsb_tree_digest(content, b.size, digest);
        if (b.digest != FSB_NONE) (void)fsb_get_digest(p, b.digest, digest);
    }
    free(content);
}

/* The blob through a stream, with a decoder of its own as a cache sync has: read to its end from
 * its start, from its middle and from its first byte on, each window's ends touched. */
static void stream_blob(const struct fsb_payload *p, struct fsb_decoder *d, uint32_t index) {
    struct fsb_blob b;
    if (!fsb_get_blob(p, index, &b) || b.size > MAX_READ) return;
    struct fsb_stream *s = fsb_stream_open(p, d, &b);
    if (s == NULL) return;
    uint64_t starts[] = {0, b.size / 2, b.size > 0};
    for (unsigned i = 0; i < sizeof starts / sizeof starts[0]; i++) {
        const uint8_t *data;
        uint64_t length;
        int status = fsb_stream_seek(s, starts[i]);
        while (status == FSB_STREAM_DATA &&
               (status = fsb_stream_next(s, &data, &length)) == FSB_STREAM_DATA)
            touched = data[0] ^ data[length - 1];
        if (status != FSB_STREAM_END) break; /* failed: it fails every call after */
    }
    fsb_stream_close(s);
}

static void walk(const struct fsb_payload *p) {
    const char *text;
    struct fsb_decoder *decoder = fsb_decoder_new(4);
    for (uint32_t i = 0; i < bounded(p, FSB_SECTION_BLOBS); i++) {
        read_blob(p, i);
        if (decoder != NULL) stream_blob(p, decoder, i);
    }
    fsb_decoder_free(decoder);
    for (uint32_t i = 0; i < bounded(p, FSB_SECTION_FILES); i++) {
        struct fsb_file f;
        if (!fsb_get_file(p, i, &f)) continue;
        if (fsb_str(p, f.path, &text)) lookups(p, text, f.path.length);
        uint32_t word;
        if (f.closure != FSB_NONE) (void)fsb_get_word(p, f.closure, &word);
    }
    for (uint32_t i = 0; i < bounded(p, FSB_SECTION_MODULES); i++) {
        struct fsb_module m;
        if (!fsb_get_module(p, i, &m)) continue;
        if (fsb_str(p, m.name, &text)) lookups(p, text, m.name.length);
        if (fsb_str(p, m.path, &text)) lookups(p, text, m.path.length);
    }
    for (uint32_t i = 0; i < bounded(p, FSB_SECTION_DISTS); i++) {
        struct fsb_dist d;
        if (!fsb_get_dist(p, i, &d)) continue;
        if (fsb_str(p, d.name, &text)) lookups(p, text, d.name.length);
        if (fsb_str(p, d.dist_info, &text)) lookups(p, text, d.dist_info.length);
        if (fsb_str(p, d.version, &text)) lookups(p, text, d.version.length);
    }
    uint32_t word;
    uint8_t digest[FSB_DIGEST_SIZE];
    for (uint32_t i = 0; i < bounded(p, FSB_SECTION_CLOSURES); i++) (void)fsb_get_word(p, i, &word);
    for (uint32_t i = 0; i < bounded(p, FSB_SECTION_DIGESTS); i++) (void)fsb_get_digest(p, i, digest);
    for (uint32_t i = 0; i < bounded(p, FSB_SECTION_STARTUP); i++) (void)fsb_get_startup(p, i, &word);
    for (uint64_t i = 0; i < bounded(p, FSB_SECTION_FILES); i++) /* every key, as the tables have it */
        if (fsb_record_key(p, FSB_SECTION_FILES, i, &text, &word)) lookups(p, text, word);
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    static struct fsb_payload payload; /* 4 KiB of path: not on the stack */
    if (fsb_payload_parse(&payload, data, size) == NULL) walk(&payload);
    uint8_t digest[FSB_DIGEST_SIZE]; /* the integrity digest of the input, whatever its size */
    (void)fsb_tree_digest(data, size, digest);
    return 0;
}
