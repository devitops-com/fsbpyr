/* The integrity digest (see integrity.h). */
#include "integrity.h"

#include "parallel.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

/* Leaves per thread: waking more threads costs more than they save on small files (21 MB:
 * 2.1 ms on 6 threads, 2.4 ms on 8; 150 MB: 6.6 ms on 8; Core Ultra 9 285, page cache warm). */
#define LEAVES_PER_THREAD 4u

/* OpenSSL's low-level SHA-256, linked statically for _hashlib (SHA-NI where the CPU has it).
 * Not the one-shot SHA256(): since OpenSSL 3 it goes through EVP, which sets up the library
 * context and may read openssl.cnf ($OPENSSL_CONF) before the check is done. Declared here
 * because the distribution ships no OpenSSL headers; the context is opaque to us. */
int SHA256_Init(void *context);
int SHA256_Update(void *context, const void *data, size_t length);
int SHA256_Final(unsigned char *digest, void *context);

typedef union {
    unsigned char bytes[256]; /* OpenSSL's SHA256_CTX is 112 bytes */
    uint64_t align;
} sha256_context;

static void sha256(const uint8_t *data, size_t length, uint8_t digest[FSB_DIGEST_SIZE]) {
    sha256_context context;
    SHA256_Init(&context);
    SHA256_Update(&context, data, length);
    SHA256_Final(digest, &context);
}

struct trees {
    uint64_t count;
    const uint8_t *const *data;
    const uint64_t *lengths;
    const uint64_t *first; /* count + 1: the first leaf of each buffer, then the number of leaves */
    uint8_t *digests;      /* FSB_DIGEST_SIZE per leaf */
};

static void hash_leaf(void *context, uint64_t item) {
    const struct trees *t = context;
    uint64_t low = 0, high = t->count; /* the last buffer whose first leaf is at most ``item`` */
    while (high - low > 1) {
        uint64_t middle = low + (high - low) / 2;
        if (t->first[middle] <= item)
            low = middle;
        else
            high = middle;
    }
    uint64_t start = (item - t->first[low]) * FSB_DIGEST_LEAF;
    uint64_t rest = t->lengths[low] - start;
    sha256(t->data[low] + start, (size_t)(rest < FSB_DIGEST_LEAF ? rest : FSB_DIGEST_LEAF),
           t->digests + item * FSB_DIGEST_SIZE);
}

bool fsb_tree_digests(uint64_t count, const uint8_t *const data[], const uint64_t lengths[],
                      uint8_t (*digests)[FSB_DIGEST_SIZE]) {
    uint64_t *first = malloc((size_t)(count + 1) * sizeof *first);
    if (first == NULL) return false;
    first[0] = 0;
    for (uint64_t i = 0; i < count; i++)
        first[i + 1] = first[i] + (lengths[i] + FSB_DIGEST_LEAF - 1) / FSB_DIGEST_LEAF;
    struct trees t = {.count = count, .data = data, .lengths = lengths, .first = first};
    t.digests = malloc((size_t)first[count] * FSB_DIGEST_SIZE + 1); /* + 1: never malloc(0) */
    if (t.digests == NULL) {
        free(first);
        return false;
    }
    fsb_parallel(first[count], LEAVES_PER_THREAD, hash_leaf, &t); /* all leaves of all buffers */
    for (uint64_t i = 0; i < count; i++)
        sha256(t.digests + first[i] * FSB_DIGEST_SIZE,
               (size_t)(first[i + 1] - first[i]) * FSB_DIGEST_SIZE, digests[i]);
    free(t.digests);
    free(first);
    return true;
}

bool fsb_tree_digest(const uint8_t *data, uint64_t length, uint8_t digest[FSB_DIGEST_SIZE]) {
    return fsb_tree_digests(1, &data, &length, (uint8_t (*)[FSB_DIGEST_SIZE])digest);
}

int fsb_file_digest(int fd, uint64_t length, uint8_t digest[FSB_DIGEST_SIZE]) {
    if (length > SIZE_MAX) return EFBIG;
    void *map = NULL; /* an empty file cannot be mapped, and needs no bytes */
    if (length > 0) {
        map = mmap(NULL, (size_t)length, PROT_READ, MAP_PRIVATE, fd, 0);
        if (map == MAP_FAILED) return errno;
    }
    bool done = fsb_tree_digest(map, length, digest);
    if (length > 0) munmap(map, (size_t)length);
    return done ? 0 : ENOMEM;
}

unsigned long long fsb_integrity_ns;

static const char *check(int fd, uint64_t size) {
    if (size <= FSB_DIGEST_SIZE) return "integrity check failed: bad size";
    uint8_t actual[FSB_DIGEST_SIZE], stored[FSB_DIGEST_SIZE];
    int error = fsb_file_digest(fd, size - FSB_DIGEST_SIZE, actual);
    if (error == ENOMEM) return "out of memory";
    if (error != 0 ||
        pread(fd, stored, sizeof stored, (off_t)(size - FSB_DIGEST_SIZE)) != (ssize_t)sizeof stored)
        return "cannot read own executable";
    return memcmp(actual, stored, sizeof stored) == 0
               ? NULL
               : "integrity check failed: the file was modified or damaged";
}

const char *fsb_check_integrity(int fd, uint64_t size) {
    unsigned long long start = fsb_monotonic_ns();
    const char *error = check(fd, size);
    fsb_integrity_ns = fsb_monotonic_ns() - start;
    return error;
}
