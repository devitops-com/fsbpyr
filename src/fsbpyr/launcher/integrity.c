/* The integrity digest (see integrity.h). */
#include "integrity.h"

#include "parallel.h"

#include <assert.h>
#include <errno.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
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
    const uint8_t *const *data; /* a NULL entry is read from its descriptor */
    const int *fds;
    const uint64_t *lengths;
    const uint64_t *first; /* count + 1: the first leaf of each buffer, then the number of leaves */
    uint8_t *digests;      /* FSB_DIGEST_SIZE per leaf */
    uint8_t *buffers;      /* READ_PIECE bytes per worker, for leaves read from a descriptor */
    atomic_bool unreadable;
};

/* A leaf read from a descriptor is hashed READ_PIECE bytes at a time. With a whole 1 MiB leaf
 * per thread every start paid page faults on 8 MiB of fresh memory (the code review measured
 * the no-op app's check at 1.94 against 1.71 ms), and glibc kept the buffers once freed (the
 * torch app's heap: 127 against 119 MB; D36). */
#define READ_PIECE (64u << 10)

/* ``size`` bytes at ``offset`` of the file open as ``fd``; false if fewer are there. */
static bool read_piece(int fd, uint8_t *into, size_t size, uint64_t offset) {
    for (size_t got = 0; got < size;) {
        ssize_t n = pread(fd, into + got, size - got, (off_t)(offset + got));
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        got += (size_t)n;
    }
    return true;
}

static void hash_leaf(void *context, uint64_t item, unsigned worker) {
    struct trees *t = context;
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
    size_t size = (size_t)(rest < FSB_DIGEST_LEAF ? rest : FSB_DIGEST_LEAF);
    uint8_t *digest = t->digests + item * FSB_DIGEST_SIZE;
    if (t->data[low] != NULL) {
        sha256(t->data[low] + start, size, digest);
        return;
    }
    assert(worker < FSB_PARALLEL_MAX_THREADS);
    uint8_t *piece = t->buffers + (size_t)worker * READ_PIECE;
    sha256_context hashing;
    SHA256_Init(&hashing);
    for (size_t done = 0; done < size;) {
        size_t part = size - done < READ_PIECE ? size - done : READ_PIECE;
        if (!read_piece(t->fds[low], piece, part, start + done)) {
            /* unreadable, or shorter than it was: no digest over this one can match */
            memset(digest, 0, FSB_DIGEST_SIZE);
            atomic_store(&t->unreadable, true);
            return;
        }
        SHA256_Update(&hashing, piece, part);
        done += part;
    }
    SHA256_Final(digest, &hashing);
}

int fsb_tree_digests(uint64_t count, const uint8_t *const data[], const int fds[],
                     const uint64_t lengths[], uint8_t (*digests)[FSB_DIGEST_SIZE]) {
    uint64_t *first = malloc((size_t)(count + 1) * sizeof *first);
    if (first == NULL) return ENOMEM;
    first[0] = 0;
    bool reads = false;
    for (uint64_t i = 0; i < count; i++) {
        first[i + 1] = first[i] + (lengths[i] + FSB_DIGEST_LEAF - 1) / FSB_DIGEST_LEAF;
        reads = reads || (data[i] == NULL && lengths[i] > 0);
    }
    assert(fds != NULL || !reads);
    struct trees t = {.count = count, .data = data, .fds = fds, .lengths = lengths, .first = first};
    atomic_init(&t.unreadable, false);
    t.digests = malloc((size_t)first[count] * FSB_DIGEST_SIZE + 1); /* + 1: never malloc(0) */
    if (reads) t.buffers = malloc((size_t)FSB_PARALLEL_MAX_THREADS * READ_PIECE);
    bool ready = t.digests != NULL && (!reads || t.buffers != NULL);
    if (ready) {
        fsb_parallel(first[count], LEAVES_PER_THREAD, hash_leaf, &t); /* every leaf of every one */
        for (uint64_t i = 0; i < count; i++)
            sha256(t.digests + first[i] * FSB_DIGEST_SIZE,
                   (size_t)(first[i + 1] - first[i]) * FSB_DIGEST_SIZE, digests[i]);
    }
    free(t.buffers);
    free(t.digests);
    free(first);
    return !ready ? ENOMEM : atomic_load(&t.unreadable) ? EIO : 0;
}

bool fsb_tree_digest(const uint8_t *data, uint64_t length, uint8_t digest[FSB_DIGEST_SIZE]) {
    return fsb_tree_digests(1, &data, NULL, &length, (uint8_t (*)[FSB_DIGEST_SIZE])digest) == 0;
}

int fsb_file_digest(int fd, uint64_t length, uint8_t digest[FSB_DIGEST_SIZE]) {
    const uint8_t *read_from_fd = NULL;
    return fsb_tree_digests(1, &read_from_fd, &fd, &length, (uint8_t (*)[FSB_DIGEST_SIZE])digest);
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
