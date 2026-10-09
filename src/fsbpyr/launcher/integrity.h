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

/* The digests of ``count`` buffers, all their leaves hashed in one parallel pass (many small
 * cached files share the threads; a large one spreads over them); false if out of memory.
 * Reentrant: no shared state. */
bool fsb_tree_digests(uint64_t count, const uint8_t *const data[], const uint64_t lengths[],
                      uint8_t (*digests)[FSB_DIGEST_SIZE]);

/* The digest of data[0, length): fsb_tree_digests of one buffer. */
bool fsb_tree_digest(const uint8_t *data, uint64_t length, uint8_t digest[FSB_DIGEST_SIZE]);

/* The digest of the first ``length`` bytes of the file open as ``fd``; 0 or an errno value. */
int fsb_file_digest(int fd, uint64_t length, uint8_t digest[FSB_DIGEST_SIZE]);

/* NULL if the file open as ``fd`` (``size`` bytes) ends with the digest of everything before
 * it, else a static description of the failure. */
const char *fsb_check_integrity(int fd, uint64_t size);

/* Nanoseconds fsb_check_integrity took (FSBPYR_TRACE). */
extern unsigned long long fsb_integrity_ns;

#endif /* FSBPYR_INTEGRITY_H */
