/* Whole-file integrity check (D31, docs/format.md "Integrity").
 *
 * The last FSB_DIGEST_SIZE bytes of an executable are the SHA-256 over the SHA-256 of each
 * FSB_DIGEST_LEAF piece of everything before them. The launcher checks it before it believes
 * any byte of its own file, hashing the pieces on up to 8 threads.
 */
#ifndef FSBPYR_INTEGRITY_H
#define FSBPYR_INTEGRITY_H

#include <stdint.h>

/* NULL if the file open as ``fd`` (``size`` bytes) matches its digest, else a static
 * description of the failure. */
const char *fsb_check_integrity(int fd, uint64_t size);

/* Nanoseconds the check took (FSBPYR_TRACE). */
extern unsigned long long fsb_integrity_ns;

#endif /* FSBPYR_INTEGRITY_H */
