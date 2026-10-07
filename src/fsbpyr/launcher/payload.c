/* Payload mapping, validation and lookups (see payload.h and docs/format.md). */
#include "payload.h"

#include "vendor/zstd/zstd.h"

#include <fcntl.h>
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
    struct fsb_trailer trailer;
    if (fd < 0 || fstat(fd, &st) != 0 || !read_trailer(fd, (uint64_t)st.st_size, &trailer)) {
        *error = "cannot read own executable";
        if (fd >= 0) close(fd);
        return false;
    }
    *error = check_trailer(&trailer, (uint64_t)st.st_size);
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

bool fsb_get_blob(const struct fsb_payload *p, uint32_t index, struct fsb_blob *b) {
    if (!get_record(p, FSB_SECTION_BLOBS, index, b)) return false;
    if (b->offset % FSB_BLOB_ALIGN != 0 || b->offset < p->data_start ||
        !fits(b->offset, b->stored_size, p->size) || b->size > FSB_MAX_BLOB_SIZE)
        return false;
    if (b->codec == FSB_CODEC_NONE) return b->stored_size == b->size;
    return b->codec == FSB_CODEC_ZSTD;
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

bool fsb_zstd_decompress(void *dst, uint64_t size, const uint8_t *src, uint64_t stored) {
    static ZSTD_DCtx *dctx;
    if (dctx == NULL && (dctx = ZSTD_createDCtx()) == NULL) return false;
    size_t got = ZSTD_decompressDCtx(dctx, dst, (size_t)size, src, (size_t)stored);
    return !ZSTD_isError(got) && got == size;
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
