/* Read-only access to the payload appended to the running executable.
 *
 * Startup validation is O(1): the trailer, header and section table are checked once, every
 * record access is bounds-checked, and lookups binary-search the sorted tables in place. No
 * table is ever scanned or copied, so startup cost does not grow with the number of packaged
 * files (manifest §3).
 */
#ifndef FSBPYR_PAYLOAD_H
#define FSBPYR_PAYLOAD_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "format.h"

struct fsb_payload {
    const uint8_t *base; /* start of the mapped payload */
    uint64_t size;
    bool mapped; /* base is a mapping of the file (not a test's image in memory) */
    struct fsb_header header;
    const uint8_t *strtab;
    uint64_t strtab_size;
    uint64_t data_start; /* first byte after the last section: start of the blob data area */
    char executable[4096];
};

/* Maps the payload of /proc/self/exe and validates trailer, header and sections.
 * On failure returns false and stores a static description in *error. */
bool fsb_payload_open(struct fsb_payload *payload, const char **error);

/* The same checks on a whole executable image[0, size) in memory, without the integrity check
 * (the fuzz harness and edge-case tests, D38); NULL, or a static description of the failure. */
const char *fsb_payload_parse(struct fsb_payload *payload, const uint8_t *image, uint64_t size);

/* Validated view of one string; false if the Str is out of bounds or not NUL-terminated. */
bool fsb_str(const struct fsb_payload *payload, struct fsb_str ref, const char **text);

/* Record accessors: false if the index is out of range or the record fails its checks. */
bool fsb_get_blob(const struct fsb_payload *payload, uint32_t index, struct fsb_blob *out);
bool fsb_get_module(const struct fsb_payload *payload, uint32_t index, struct fsb_module *out);
bool fsb_get_file(const struct fsb_payload *payload, uint32_t index, struct fsb_file *out);
bool fsb_get_dist(const struct fsb_payload *payload, uint32_t index, struct fsb_dist *out);
bool fsb_get_startup(const struct fsb_payload *payload, uint32_t position, uint32_t *module);
bool fsb_get_word(const struct fsb_payload *payload, uint32_t index, uint32_t *word); /* CLOSURES */
bool fsb_get_digest(const struct fsb_payload *payload, uint32_t index,
                    uint8_t digest[FSB_DIGEST_SIZE]);

/* Key (name or path) of record ``index`` of a sorted table; false if out of range or corrupt. */
bool fsb_record_key(const struct fsb_payload *payload, enum fsb_section section, uint64_t index,
                    const char **text, uint32_t *length);

/* First index whose key is >= ``key`` (the table's count if none; also on a corrupt table). */
uint64_t fsb_lower_bound(const struct fsb_payload *payload, enum fsb_section section,
                         const char *key, size_t length);

/* True if [src, src+stored) is exactly one zstd frame that declares content size ``size`` and
 * carries a content checksum (so decompression verifies integrity). */
bool fsb_zstd_frame_ok(const uint8_t *src, uint64_t stored, uint64_t size);

/* What decompressing zstd takes: a context, and the last ``blocks`` blocks decompressed. A thread
 * uses its own: NULL in fsb_read_blob names the process's (32 blocks), for callers that hold the
 * GIL or run before Python starts; a cache sync, which runs without the GIL, brings one (D35).
 * Nothing is shared, so nothing is locked: a lock taken while another thread forks would stay
 * taken in the child. */
struct fsb_decoder;
struct fsb_decoder *fsb_decoder_new(unsigned blocks); /* NULL if out of memory */
void fsb_decoder_free(struct fsb_decoder *decoder);

/* Read blob ``b`` (from fsb_get_blob) into dst[b->size] with ``decoder``: copied, decompressed
 * (zstd checksum verified; xz bounded to exactly one stream of ``size`` bytes; pieces decoded in
 * parallel), or sliced out of its block, which the decoder decompresses once and keeps since a
 * package's modules share blocks; a blob larger than a stream's window is streamed into ``dst``.
 * False on any corruption, or if out of memory for a stream. xz and pieces touch only the
 * atomic counters (D33). */
bool fsb_read_blob(const struct fsb_payload *payload, struct fsb_decoder *decoder,
                   const struct fsb_blob *b, void *dst);

/* The content of one blob a window at a time, whatever its size (D38): a window holds at most
 * FSB_STREAM_WINDOW bytes (an xz piece at least), and every window from the start of the
 * content but the last is whole digest leaves (FSB_DIGEST_LEAF), so it can be hashed as it
 * comes. Streams use ``decoder`` as fsb_read_blob does (only BLOCK needs it, and its member is
 * copied out); each has its own state otherwise. A stream that failed fails every call after.
 * Tests build the window smaller (a multiple of FSB_DIGEST_LEAF). */
#ifndef FSB_STREAM_WINDOW
#define FSB_STREAM_WINDOW (UINT64_C(64) << 20)
#endif
enum { FSB_STREAM_END, FSB_STREAM_DATA, FSB_STREAM_CORRUPT, FSB_STREAM_MEMORY };
struct fsb_stream;
/* NULL if out of memory. ``b`` is from fsb_get_blob. */
struct fsb_stream *fsb_stream_open(const struct fsb_payload *payload, struct fsb_decoder *decoder,
                                   const struct fsb_blob *b);
/* FSB_STREAM_DATA with the content from the stream's position on in data[0, *length), valid
 * until the next call; FSB_STREAM_END after the last byte (a zstd frame also checked to its
 * end); FSB_STREAM_CORRUPT or FSB_STREAM_MEMORY. */
int fsb_stream_next(struct fsb_stream *stream, const uint8_t **data, uint64_t *length);
/* Move the stream to content offset ``position`` (at most the blob's size; FSB_STREAM_CORRUPT
 * otherwise): what fsb_stream_next gives next starts there. Nothing is decoded here: zstd
 * decodes up to the position when the next window is asked for, from the frame's start if it is
 * behind. */
int fsb_stream_seek(struct fsb_stream *stream, uint64_t position);
uint64_t fsb_stream_position(const struct fsb_stream *stream);
uint64_t fsb_stream_size(const struct fsb_stream *stream);
void fsb_stream_close(struct fsb_stream *stream);

/* Payload bytes read and bytes decompressed so far, blocks included (FSBPYR_TRACE). */
extern _Atomic unsigned long long fsb_stored_bytes, fsb_decompressed_bytes; /* any thread */

/* Binary search by key bytes; returns the record index or FSB_NONE. */
uint32_t fsb_find_module(const struct fsb_payload *payload, const char *name, size_t length);
uint32_t fsb_find_file(const struct fsb_payload *payload, const char *path, size_t length);
uint32_t fsb_find_dist(const struct fsb_payload *payload, const char *name, size_t length);

static inline uint64_t fsb_count(const struct fsb_payload *payload, enum fsb_section section) {
    return payload->header.sections[section].count;
}

#endif /* FSBPYR_PAYLOAD_H */
