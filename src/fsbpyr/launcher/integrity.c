/* Whole-file integrity check (see integrity.h). */
#include "integrity.h"

#include "format.h"

#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>

#define MAX_THREADS 8u /* owner decision: at most 8, never more than the usable cores */
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

struct tree {
    const uint8_t *data;
    uint64_t length; /* bytes covered: the file without its digest */
    uint64_t leaves;
    uint8_t *digests; /* FSB_DIGEST_SIZE per leaf */
    atomic_uint_fast64_t next;
};

static void *hash_leaves(void *arg) {
    struct tree *t = arg;
    for (uint64_t i; (i = atomic_fetch_add(&t->next, 1)) < t->leaves;) {
        uint64_t start = i * FSB_DIGEST_LEAF;
        uint64_t rest = t->length - start;
        sha256(t->data + start, (size_t)(rest < FSB_DIGEST_LEAF ? rest : FSB_DIGEST_LEAF),
               t->digests + i * FSB_DIGEST_SIZE);
    }
    return NULL;
}

static unsigned usable_cores(void) {
    cpu_set_t set;
    if (sched_getaffinity(0, sizeof set, &set) != 0) return 1;
    int count = CPU_COUNT(&set);
    return count > 0 ? (unsigned)count : 1;
}

/* Hash every leaf: this thread and up to MAX_THREADS - 1 helpers take the next one in turn. */
static void hash_tree(struct tree *t) {
    uint64_t wanted = (t->leaves + LEAVES_PER_THREAD - 1) / LEAVES_PER_THREAD;
    unsigned threads = usable_cores();
    if (threads > MAX_THREADS) threads = MAX_THREADS;
    if (threads > wanted) threads = (unsigned)wanted;
    pthread_t helpers[MAX_THREADS - 1];
    unsigned started = 0;
    while (started + 1 < threads &&
           pthread_create(&helpers[started], NULL, hash_leaves, t) == 0)
        started++; /* fewer helpers if one cannot start: this thread hashes what is left */
    hash_leaves(t);
    for (unsigned i = 0; i < started; i++) pthread_join(helpers[i], NULL);
}

unsigned long long fsb_integrity_ns;

static unsigned long long monotonic_ns(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (unsigned long long)now.tv_sec * 1000000000ull + (unsigned long long)now.tv_nsec;
}

static const char *check(int fd, uint64_t size) {
    if (size <= FSB_DIGEST_SIZE || size > SIZE_MAX) return "integrity check failed: bad size";
    void *map = mmap(NULL, (size_t)size, PROT_READ, MAP_PRIVATE, fd, 0);
    if (map == MAP_FAILED) return "cannot map own executable";
    struct tree t = {.data = map, .length = size - FSB_DIGEST_SIZE};
    t.leaves = (t.length + FSB_DIGEST_LEAF - 1) / FSB_DIGEST_LEAF;
    atomic_init(&t.next, 0);
    t.digests = malloc((size_t)t.leaves * FSB_DIGEST_SIZE);
    const char *error = "out of memory";
    if (t.digests != NULL) {
        hash_tree(&t);
        uint8_t root[FSB_DIGEST_SIZE];
        sha256(t.digests, (size_t)t.leaves * FSB_DIGEST_SIZE, root);
        error = memcmp(root, t.data + t.length, FSB_DIGEST_SIZE) == 0
                    ? NULL
                    : "integrity check failed: the file was modified or damaged";
        free(t.digests);
    }
    munmap(map, (size_t)size);
    return error;
}

const char *fsb_check_integrity(int fd, uint64_t size) {
    unsigned long long start = monotonic_ns();
    const char *error = check(fd, size);
    fsb_integrity_ns = monotonic_ns() - start;
    return error;
}
