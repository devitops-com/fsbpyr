/* The integrity digest (D31, D32; docs/format.md "Integrity").
 *
 * SHA-256 over the SHA-256 of each FSB_DIGEST_LEAF piece of the data, the pieces hashed on up to
 * 8 threads. One routine for everything the launcher checks: its own file before it starts
 * (the file's last FSB_DIGEST_SIZE bytes are the digest of everything before them) and every
 * file it extracts to the cache (the payload's DIGESTS).
 */
#ifndef FSBPYR_INTEGRITY_H
#define FSBPYR_INTEGRITY_H

#include <stdbool.h>
#include <stdint.h>

#include "format.h"

/* Room for OpenSSL's SHA256_CTX (112 bytes), which the distribution declares nowhere. */
typedef union {
    unsigned char bytes[256];
    uint64_t align;
} fsb_sha256_context;

/* The digests of ``count`` buffers, their leaves hashed in parallel passes (many small cached
 * files share the threads; a large one spreads over them). A pass hashes a bounded number of
 * leaves and folds their digests into each buffer's root before the next, so the memory needed
 * does not grow with the buffers' sizes (D38). A buffer whose data[i] is NULL is the file open
 * as fds[i] (``fds`` may be NULL if there is none): each thread reads its leaves through a
 * small buffer of its own, so a large file never takes its size of resident memory, as it would
 * mapped (D36). A leaf that cannot be read whole gets a digest that cannot match. 0; EIO if some
 * leaf could not be read (its digest is set all the same); ENOMEM if out of memory (no digest
 * set). Reentrant: no shared state. */
int fsb_tree_digests(uint64_t count, const uint8_t *const data[], const int fds[],
                     const uint64_t lengths[], uint8_t (*digests)[FSB_DIGEST_SIZE]);

/* The digest of content that comes a piece at a time (extraction, D38): each piece is hashed
 * in parallel as it comes and only its root is kept. Every piece but the last is whole leaves. */
struct fsb_tree {
    fsb_sha256_context root;
    bool ended; /* a piece that was not whole leaves came: no other may follow */
};
void fsb_tree_init(struct fsb_tree *tree);
int fsb_tree_add(struct fsb_tree *tree, const uint8_t *data, uint64_t length); /* 0 or ENOMEM */
void fsb_tree_final(struct fsb_tree *tree, uint8_t digest[FSB_DIGEST_SIZE]);

/* The digest of data[0, length): fsb_tree_digests of one buffer. */
bool fsb_tree_digest(const uint8_t *data, uint64_t length, uint8_t digest[FSB_DIGEST_SIZE]);

/* The digest of the first ``length`` bytes of the file open as ``fd``, read in leaves; 0, EIO
 * or ENOMEM as fsb_tree_digests. */
int fsb_file_digest(int fd, uint64_t length, uint8_t digest[FSB_DIGEST_SIZE]);

/* NULL if the file open as ``fd`` (``size`` bytes) ends with the digest of everything before
 * it, else a static description of the failure. */
const char *fsb_check_integrity(int fd, uint64_t size);

/* Nanoseconds fsb_check_integrity took (FSBPYR_TRACE). */
extern unsigned long long fsb_integrity_ns;

#endif /* FSBPYR_INTEGRITY_H */
