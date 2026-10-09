/* Payload mapping, validation and lookups (see payload.h and docs/format.md). */
#include "payload.h"

#include "integrity.h"
#include "parallel.h"
#include "vendor/zstd/zstd.h"

#include <errno.h>
#include <fcntl.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static const uint64_t RECORD_SIZE[FSB_SECTION_COUNT] = {
    [FSB_SECTION_STRTAB] = 1,
    [FSB_SECTION_BLOBS] = sizeof(struct fsb_blob),
    [FSB_SECTION_MODULES] = sizeof(struct fsb_module),
    [FSB_SECTION_FILES] = sizeof(struct fsb_file),
    [FSB_SECTION_DISTS] = sizeof(struct fsb_dist),
    [FSB_SECTION_CLOSURES] = sizeof(uint32_t),
    [FSB_SECTION_DIGESTS] = FSB_DIGEST_SIZE,
    [FSB_SECTION_STARTUP] = sizeof(uint32_t),
};

/* off + len <= size without overflow */
static bool fits(uint64_t off, uint64_t len, uint64_t size) {
    return off <= size && len <= size - off;
}

static bool read_trailer(int fd, uint64_t file_size, struct fsb_trailer *trailer) {
    if (file_size < sizeof *trailer) return false;
    ssize_t got = pread(fd, trailer, sizeof *trailer, (off_t)(file_size - sizeof *trailer));
    return got == (ssize_t)sizeof *trailer;
}

static const char *check_trailer(const struct fsb_trailer *t, uint64_t file_size) {
    if (memcmp(t->magic, FSB_TRAILER_MAGIC, FSB_MAGIC_SIZE) != 0) return "no fsbpyr payload";
    if (t->format_version != FSB_FORMAT_VERSION) return "unsupported payload format version";
    if (t->flags != 0) return "unknown trailer flags";
    if (t->payload_offset % FSB_PAGE_SIZE != 0) return "payload offset not page aligned";
    if (!fits(t->payload_offset, t->payload_size, file_size - sizeof *t) ||
        t->payload_offset + t->payload_size + sizeof *t != file_size)
        return "payload size does not match file size";
    return NULL;
}

static const char *check_header(struct fsb_payload *p) {
    const struct fsb_header *h = &p->header;
    if (memcmp(h->magic, FSB_HEADER_MAGIC, FSB_MAGIC_SIZE) != 0) return "bad payload header";
    if (h->format_version != FSB_FORMAT_VERSION) return "unsupported payload format version";
    if (h->header_size != sizeof *h) return "bad payload header size";
    uint64_t data_start = sizeof *h;
    for (int s = 0; s < FSB_SECTION_COUNT; s++) {
        uint64_t off = h->sections[s].offset, count = h->sections[s].count;
        if (off < sizeof *h || off % FSB_TABLE_ALIGN != 0 || count > p->size / RECORD_SIZE[s] ||
            !fits(off, count * RECORD_SIZE[s], p->size))
            return "payload section out of bounds";
        uint64_t end = off + count * RECORD_SIZE[s];
        if (end > data_start) data_start = end;
    }
    p->strtab = p->base + h->sections[FSB_SECTION_STRTAB].offset;
    p->strtab_size = h->sections[FSB_SECTION_STRTAB].count;
    p->data_start = data_start;
    return NULL;
}

/* The kernel names an executable that was replaced or removed while it runs "path (deleted)".
 * Keep the path the program was started as: it names the payload's virtual tree, and a
 * multiprocessing parent passes its own sys.path (that tree) to children started from the same
 * binary through /proc/self/exe. A file really called "... (deleted)" is left alone. */
static void strip_deleted(char *path, size_t *length, const struct stat *self) {
    static const char suffix[] = " (deleted)";
    size_t n = sizeof suffix - 1;
    struct stat named;
    if (*length <= n || memcmp(path + *length - n, suffix, n) != 0) return;
    if (path[*length - n - 1] == '/') return; /* "dir/ (deleted)" would leave a directory */
    if (stat(path, &named) == 0) {
        if (named.st_dev == self->st_dev && named.st_ino == self->st_ino) return;
    } else if (errno != ENOENT && errno != ENOTDIR) {
        return; /* cannot tell: keep what the kernel reported */
    }
    *length -= n;
    path[*length] = '\0';
}

bool fsb_payload_open(struct fsb_payload *p, const char **error) {
    memset(p, 0, sizeof *p);
    ssize_t n = readlink("/proc/self/exe", p->executable, sizeof p->executable - 1);
    if (n <= 0 || (size_t)n >= sizeof p->executable - 1) {
        *error = "cannot resolve own executable path";
        return false;
    }
    p->executable[n] = '\0';
    int fd = open("/proc/self/exe", O_RDONLY | O_CLOEXEC);
    struct stat st;
    if (fd < 0 || fstat(fd, &st) != 0) {
        *error = "cannot read own executable";
        if (fd >= 0) close(fd);
        return false;
    }
    size_t length = (size_t)n;
    strip_deleted(p->executable, &length, &st);
    *error = fsb_check_integrity(fd, (uint64_t)st.st_size); /* before any byte is believed */
    struct fsb_trailer trailer;
    if (*error == NULL && !read_trailer(fd, (uint64_t)st.st_size, &trailer))
        *error = "cannot read own executable";
    if (*error == NULL) *error = check_trailer(&trailer, (uint64_t)st.st_size);
    if (*error == NULL && trailer.payload_size < sizeof p->header) *error = "payload truncated";
    void *map = MAP_FAILED;
    if (*error == NULL) {
        map = mmap(NULL, trailer.payload_size, PROT_READ, MAP_PRIVATE, fd,
                   (off_t)trailer.payload_offset);
        if (map == MAP_FAILED) *error = "cannot map payload";
    }
    close(fd);
    if (*error != NULL) return false;
    p->base = map;
    p->size = trailer.payload_size;
    memcpy(&p->header, p->base, sizeof p->header);
    *error = check_header(p);
    return *error == NULL;
}

bool fsb_str(const struct fsb_payload *p, struct fsb_str ref, const char **text) {
    if (!fits(ref.offset, (uint64_t)ref.length + 1, p->strtab_size)) return false;
    if (p->strtab[ref.offset + ref.length] != '\0') return false;
    *text = (const char *)p->strtab + ref.offset;
    return true;
}

static bool get_record(const struct fsb_payload *p, enum fsb_section s, uint32_t i, void *out) {
    if (i >= fsb_count(p, s)) return false;
    memcpy(out, p->base + p->header.sections[s].offset + (uint64_t)i * RECORD_SIZE[s],
           RECORD_SIZE[s]);
    return true;
}

/* PIECES: offset is the first piece's index, stored_size their number; each piece is checked
 * as it is decoded. */
static bool pieces_ok(const struct fsb_payload *p, const struct fsb_blob *b) {
    uint64_t count = fsb_count(p, FSB_SECTION_BLOBS); /* no product of record fields: none overflows */
    return b->size > FSB_XZ_PIECE && b->size <= FSB_MAX_BLOB_SIZE &&
           b->stored_size == (b->size + FSB_XZ_PIECE - 1) / FSB_XZ_PIECE &&
           b->offset <= count && b->stored_size <= count - b->offset;
}

bool fsb_get_blob(const struct fsb_payload *p, uint32_t index, struct fsb_blob *b) {
    if (!get_record(p, FSB_SECTION_BLOBS, index, b)) return false;
    if (b->codec == FSB_CODEC_BLOCK) /* offset: the block's index; stored_size: the position */
        return b->offset < fsb_count(p, FSB_SECTION_BLOBS) && b->offset <= UINT32_MAX &&
               fits(b->stored_size, b->size, FSB_MAX_BLOCK_SIZE);
    if (b->codec == FSB_CODEC_PIECES) return pieces_ok(p, b);
    if (b->offset % FSB_BLOB_ALIGN != 0 || b->offset < p->data_start ||
        !fits(b->offset, b->stored_size, p->size) || b->size > FSB_MAX_BLOB_SIZE)
        return false;
    if (b->codec == FSB_CODEC_NONE) return b->stored_size == b->size;
    return b->codec == FSB_CODEC_ZSTD || b->codec == FSB_CODEC_XZ;
}

bool fsb_get_module(const struct fsb_payload *p, uint32_t index, struct fsb_module *m) {
    return get_record(p, FSB_SECTION_MODULES, index, m) && m->kind >= FSB_MODULE &&
           m->kind <= FSB_EXTENSION;
}

bool fsb_get_file(const struct fsb_payload *p, uint32_t index, struct fsb_file *f) {
    return get_record(p, FSB_SECTION_FILES, index, f) && f->kind >= FSB_FILE_RESOURCE &&
           f->kind <= FSB_FILE_METADATA && f->mode <= FSB_MAX_MODE;
}

bool fsb_get_dist(const struct fsb_payload *p, uint32_t index, struct fsb_dist *d) {
    return get_record(p, FSB_SECTION_DISTS, index, d);
}

bool fsb_get_startup(const struct fsb_payload *p, uint32_t position, uint32_t *module) {
    return get_record(p, FSB_SECTION_STARTUP, position, module);
}

bool fsb_get_word(const struct fsb_payload *p, uint32_t index, uint32_t *word) {
    return get_record(p, FSB_SECTION_CLOSURES, index, word);
}

bool fsb_get_digest(const struct fsb_payload *p, uint32_t index, uint8_t digest[FSB_DIGEST_SIZE]) {
    return get_record(p, FSB_SECTION_DIGESTS, index, digest);
}

bool fsb_zstd_frame_ok(const uint8_t *src, uint64_t stored, uint64_t size) {
    static const uint8_t magic[4] = {0x28, 0xb5, 0x2f, 0xfd};
    if (stored < 6 || memcmp(src, magic, sizeof magic) != 0 || !(src[4] & 0x04)) return false;
    return ZSTD_findFrameCompressedSize(src, (size_t)stored) == stored &&
           ZSTD_getFrameContentSize(src, (size_t)stored) == size;
}

/* Recently decompressed blocks (D30): importing a package reads several of its modules from the
 * same block, so each block is decompressed once while it stays among the decoder's last few. */
#define BLOCK_CACHE 32 /* imports alternate between packages: 8 thrashed (aiagent --help) */

struct block_slot {
    uint32_t index;
    uint64_t size;
    uint64_t used; /* clock of the last use; 0: empty */
    uint8_t *data; /* FSB_MAX_BLOCK_SIZE bytes once allocated */
};

struct fsb_decoder {
    ZSTD_DCtx *dctx; /* created on first use */
    uint64_t clock;
    unsigned count;
    struct block_slot blocks[];
};

struct fsb_decoder *fsb_decoder_new(unsigned blocks) {
    if (blocks == 0) blocks = 1; /* block_content() takes a slot */
    struct fsb_decoder *d = calloc(1, sizeof *d + blocks * sizeof d->blocks[0]);
    if (d != NULL) d->count = blocks;
    return d;
}

void fsb_decoder_free(struct fsb_decoder *d) {
    if (d == NULL) return;
    for (unsigned i = 0; i < d->count; i++) free(d->blocks[i].data);
    ZSTD_freeDCtx(d->dctx);
    free(d);
}

/* ``d``, or the process's own decoder (NULL if it cannot be created). */
static struct fsb_decoder *decoder(struct fsb_decoder *d) {
    static struct fsb_decoder *process;
    if (d != NULL) return d;
    if (process == NULL) process = fsb_decoder_new(BLOCK_CACHE);
    return process;
}

/* Decompress one verified frame (fsb_zstd_frame_ok) into dst[size]. */
static bool zstd_decompress(struct fsb_decoder *d, void *dst, uint64_t size, const uint8_t *src,
                            uint64_t stored) {
    if (d->dctx == NULL && (d->dctx = ZSTD_createDCtx()) == NULL) return false;
    size_t got = ZSTD_decompressDCtx(d->dctx, dst, (size_t)size, src, (size_t)stored);
    if (ZSTD_isError(got) || got != size) return false;
    fsb_decompressed_bytes += size;
    return true;
}

_Atomic unsigned long long fsb_stored_bytes, fsb_decompressed_bytes;

/* liblzma, linked statically for CPython's _lzma (the distribution ships no headers). */
typedef struct lzma_allocator lzma_allocator;
int lzma_stream_buffer_decode(uint64_t *memlimit, uint32_t flags, const lzma_allocator *allocator,
                              const uint8_t *in, size_t *in_pos, size_t in_size, uint8_t *out,
                              size_t *out_pos, size_t out_size);
#define LZMA_OK 0

/* Exactly one xz stream of exactly ``size`` bytes: a longer one fails as the output fills. */
static bool xz_decode(void *dst, uint64_t size, const uint8_t *src, uint64_t stored) {
    uint64_t memlimit = FSB_XZ_MEMLIMIT;
    size_t in_pos = 0, out_pos = 0;
    if (lzma_stream_buffer_decode(&memlimit, 0, NULL, src, &in_pos, (size_t)stored, dst, &out_pos,
                                  (size_t)size) != LZMA_OK)
        return false;
    if (in_pos != stored || out_pos != size) return false;
    fsb_decompressed_bytes += size;
    return true;
}

static const uint8_t *block_content(const struct fsb_payload *p, struct fsb_decoder *d,
                                    uint32_t index, uint64_t *size) {
    struct block_slot *blocks = d->blocks, *slot = &blocks[0];
    for (unsigned i = 0; i < d->count; i++) {
        if (blocks[i].used != 0 && blocks[i].index == index) {
            blocks[i].used = ++d->clock;
            *size = blocks[i].size;
            return blocks[i].data;
        }
        if (blocks[i].used < slot->used) slot = &blocks[i]; /* least recently used, or empty */
    }
    struct fsb_blob block;
    if (!fsb_get_blob(p, index, &block) || block.codec != FSB_CODEC_ZSTD ||
        block.size > FSB_MAX_BLOCK_SIZE)
        return NULL;
    const uint8_t *src = p->base + block.offset;
    if (!fsb_zstd_frame_ok(src, block.stored_size, block.size)) return NULL;
    fsb_stored_bytes += block.stored_size;
    if (slot->data == NULL && (slot->data = malloc(FSB_MAX_BLOCK_SIZE)) == NULL) return NULL;
    slot->used = 0; /* invalid until the content is in place */
    if (!zstd_decompress(d, slot->data, block.size, src, block.stored_size)) return NULL;
    slot->index = index;
    slot->size = block.size;
    slot->used = ++d->clock;
    *size = block.size;
    return slot->data;
}

struct pieces {
    const struct fsb_payload *payload;
    const struct fsb_blob *parent;
    uint8_t *dst;
    atomic_bool failed;
};

static void decode_piece(void *context, uint64_t number, unsigned worker) {
    (void)worker;
    struct pieces *job = context;
    const struct fsb_blob *parent = job->parent;
    uint64_t start = number * FSB_XZ_PIECE;
    uint64_t size = number + 1 < parent->stored_size ? FSB_XZ_PIECE : parent->size - start;
    struct fsb_blob piece;
    if (!fsb_get_blob(job->payload, (uint32_t)(parent->offset + number), &piece) ||
        piece.codec != FSB_CODEC_XZ || piece.size != size) {
        atomic_store(&job->failed, true);
        return;
    }
    fsb_stored_bytes += piece.stored_size;
    if (!xz_decode(job->dst + start, size, job->payload->base + piece.offset, piece.stored_size))
        atomic_store(&job->failed, true);
}

bool fsb_read_blob(const struct fsb_payload *p, struct fsb_decoder *with, const struct fsb_blob *b,
                   void *dst) {
    struct fsb_decoder *d;
    switch (b->codec) { /* offset is a data offset for NONE, ZSTD and XZ; a record index else */
    case FSB_CODEC_NONE:
        memcpy(dst, p->base + b->offset, (size_t)b->size);
        fsb_stored_bytes += b->size;
        return true;
    case FSB_CODEC_ZSTD:
        if (!fsb_zstd_frame_ok(p->base + b->offset, b->stored_size, b->size)) return false;
        if ((d = decoder(with)) == NULL) return false;
        fsb_stored_bytes += b->stored_size;
        return zstd_decompress(d, dst, b->size, p->base + b->offset, b->stored_size);
    case FSB_CODEC_BLOCK: {
        uint64_t size;
        if ((d = decoder(with)) == NULL) return false;
        const uint8_t *block = block_content(p, d, (uint32_t)b->offset, &size);
        if (block == NULL || !fits(b->stored_size, b->size, size)) return false;
        memcpy(dst, block + b->stored_size, (size_t)b->size);
        return true;
    }
    case FSB_CODEC_XZ:
        fsb_stored_bytes += b->stored_size;
        return xz_decode(dst, b->size, p->base + b->offset, b->stored_size);
    case FSB_CODEC_PIECES: { /* one stream per piece: decoded in parallel (D33) */
        struct pieces job = {.payload = p, .parent = b, .dst = dst};
        atomic_init(&job.failed, false);
        fsb_parallel(b->stored_size, 1, decode_piece, &job);
        return !atomic_load(&job.failed);
    }
    default:
        return false;
    }
}

/* memcmp order with the shorter string first on a common prefix (Python bytes ordering) */
static int compare_key(const char *a, size_t la, const char *b, size_t lb) {
    int c = memcmp(a, b, la < lb ? la : lb);
    if (c != 0) return c;
    return (la > lb) - (la < lb);
}

bool fsb_record_key(const struct fsb_payload *p, enum fsb_section s, uint64_t index,
                    const char **text, uint32_t *length) {
    if (index >= fsb_count(p, s)) return false;
    struct fsb_str key; /* every sorted table starts its records with the key */
    memcpy(&key, p->base + p->header.sections[s].offset + index * RECORD_SIZE[s], sizeof key);
    if (!fsb_str(p, key, text)) return false;
    *length = key.length;
    return true;
}

uint64_t fsb_lower_bound(const struct fsb_payload *p, enum fsb_section s, const char *key,
                         size_t length) {
    uint64_t lo = 0, hi = fsb_count(p, s);
    while (lo < hi) {
        uint64_t mid = lo + (hi - lo) / 2;
        const char *text;
        uint32_t text_length;
        if (!fsb_record_key(p, s, mid, &text, &text_length)) return fsb_count(p, s);
        if (compare_key(text, text_length, key, length) < 0)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo;
}

static uint32_t search(const struct fsb_payload *p, enum fsb_section s, const char *key,
                       size_t length) {
    uint64_t index = fsb_lower_bound(p, s, key, length);
    const char *text;
    uint32_t text_length;
    if (index >= UINT32_MAX || !fsb_record_key(p, s, index, &text, &text_length) ||
        compare_key(text, text_length, key, length) != 0)
        return FSB_NONE;
    return (uint32_t)index;
}

uint32_t fsb_find_module(const struct fsb_payload *p, const char *name, size_t length) {
    return search(p, FSB_SECTION_MODULES, name, length);
}

uint32_t fsb_find_file(const struct fsb_payload *p, const char *path, size_t length) {
    return search(p, FSB_SECTION_FILES, path, length);
}

uint32_t fsb_find_dist(const struct fsb_payload *p, const char *name, size_t length) {
    return search(p, FSB_SECTION_DISTS, name, length);
}
