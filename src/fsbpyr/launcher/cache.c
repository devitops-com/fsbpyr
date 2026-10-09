/* The native cache (see cache.h). */
#include "cache.h"

#include "integrity.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* Cached files checked in one parallel pass (D32): up to CHECK_BATCH files; those up to
 * SMALL_FILE bytes are read into a buffer of BATCH_BUFFER bytes, at most HELD_FILES larger ones
 * stay open and are read leaf by leaf on the hashing threads (D36). Reading a small file whole
 * costs less than mapping it: torch's 12,139 files that are no native library, 235 MB, took 34 ms
 * instead of 56 (D35); and a mapped file is resident while it is hashed: mapping torch's
 * libraries put up to 546 MiB in the process's RSS (D36). */
#define CHECK_BATCH 1024u
#define SMALL_FILE (UINT64_C(64) << 10)
#define BATCH_BUFFER (UINT64_C(16) << 20)
#define HELD_FILES 64u
/* Restored files are written in pieces of at most WRITE_PIECE: written whole, a 438 MB library
 * stayed in the page cache as 2 MiB folios, which the dynamic loader maps whole, and torch's
 * libtorch_cpu.so took 156 MB of RSS instead of 60 MB (XFS, Linux 6.12; D36). */
#define WRITE_PIECE (UINT64_C(1) << 20)
#define DECODER_BLOCKS 4u /* blocks a sync's own decoder keeps (D30): files restored in path order */
#define DIRECTORY (O_RDONLY | O_DIRECTORY | O_CLOEXEC)
#define TEMPORARY_ATTEMPTS 100u
#define PATH_SIZE 4096u            /* a directory path below the root */
#define MAX_DEPTH (PATH_SIZE / 2u) /* components of such a path */

/* A cached file, as load_cached() finds it; NO_ROOM: it cannot be opened for lack of descriptors
 * or memory, which the files held for the pass so far may be taking; FAILED: the failure is set. */
enum { PRESENT, MISSING, CHANGED, NO_ROOM, FAILED };

struct entry {        /* one file to check, its records checked */
    uint64_t item;    /* its place in job->files */
    uint32_t index;   /* FILES */
    const char *path; /* site-relative, NUL-terminated in the payload's string table */
    uint32_t length;
    uint16_t mode;
    struct fsb_blob blob;
    uint8_t digest[FSB_DIGEST_SIZE];
};

struct state {
    const struct fsb_payload *payload;
    struct fsb_decoder *decoder; /* this sync's own (no GIL here), once a blob needs one */
    struct fsb_cache_sync *job;
    size_t chain_length;
    uid_t uid;
    /* The directories open from the root down: fds[0] is the root, fds[i] the directory at the
     * end of the i-th component of ``path`` (ends[i - 1]); ``depth`` of them are open. */
    bool root_open;
    size_t depth;
    int fds[MAX_DEPTH + 1];
    size_t ends[MAX_DEPTH];
    char path[PATH_SIZE];
    /* Cached files read, or held open, waiting for their parallel pass. */
    unsigned batched;
    uint64_t buffered; /* bytes of ``buffer`` in use */
    uint8_t *buffer;   /* BATCH_BUFFER bytes, once a small file needs it */
    const struct entry *batch[CHECK_BATCH];
    const uint8_t *data[CHECK_BATCH];
    uint64_t lengths[CHECK_BATCH];
    int descriptors[CHECK_BATCH]; /* a file held open, or -1 */
    unsigned held;        /* how many */
    int no_room;          /* the errno of the last NO_ROOM */
    uint8_t digests[CHECK_BATCH][FSB_DIGEST_SIZE];
};

static atomic_ulong temporaries; /* names of temporary files: unique in this process */

bool fsb_cache_safe(const char *path, size_t length) {
    if (length == 0 || memchr(path, '\0', length) || memchr(path, '\\', length)) return false;
    for (size_t start = 0; start <= length;) {
        const char *slash = memchr(path + start, '/', length - start);
        size_t end = slash ? (size_t)(slash - path) : length, n = end - start;
        if (n == 0 || (n <= 2 && memcmp(path + start, "..", n) == 0)) return false;
        start = end + 1;
    }
    return true;
}

static bool corrupt(struct state *s, const char *reason, uint32_t index) {
    s->job->failure = FSB_CACHE_CORRUPT;
    s->job->reason = reason;
    s->job->file = index;
    return false;
}

static bool out_of_memory(struct state *s) {
    s->job->failure = FSB_CACHE_MEMORY;
    return false;
}

/* Failure ``failure`` (errno ``error``) at <root>/below[0, length), or at the root itself. */
static bool failed_at(struct state *s, enum fsb_cache_failure failure, int error,
                      const char *below, size_t length) {
    struct fsb_cache_sync *job = s->job;
    job->failure = failure;
    job->error = error;
    if (length > PATH_SIZE) length = PATH_SIZE; /* the message only: cut short */
    if (below == NULL)
        snprintf(job->path, sizeof job->path, "%s", job->root);
    else
        snprintf(job->path, sizeof job->path, "%s/%.*s", job->root, (int)length, below);
    return false;
}

static bool file_failed(struct state *s, int error, const struct entry *e) {
    struct fsb_cache_sync *job = s->job;
    job->failure = FSB_CACHE_SYSTEM;
    job->error = error;
    snprintf(job->path, sizeof job->path, "%s/%s/%s", job->root, job->chain, e->path);
    return false;
}

/* The entry's content, decoded from the payload into a buffer the caller frees. */
static bool decode(struct state *s, const struct entry *e, uint8_t **content) {
    if (s->decoder == NULL && (s->decoder = fsb_decoder_new(DECODER_BLOCKS)) == NULL)
        return out_of_memory(s);
    if (e->blob.codec == FSB_CODEC_ZSTD && /* a frame of another size: corrupt, never allocated */
        !fsb_zstd_frame_ok(s->payload->base + e->blob.offset, e->blob.stored_size, e->blob.size))
        return corrupt(s, "blob data of file", e->index);
    *content = malloc(e->blob.size ? (size_t)e->blob.size : 1);
    if (*content == NULL) return out_of_memory(s);
    if (fsb_read_blob(s->payload, s->decoder, &e->blob, *content)) return true;
    free(*content);
    *content = NULL;
    return corrupt(s, "blob data of file", e->index);
}

static bool prepare(struct state *s, uint64_t item, struct entry *e) {
    const struct fsb_payload *p = s->payload;
    uint32_t index = s->job->files[item];
    struct fsb_file f;
    if (!fsb_get_file(p, index, &f) || !fsb_str(p, f.path, &e->path))
        return corrupt(s, "file", index);
    bool extractable = s->job->resources || f.kind == FSB_FILE_NATIVE_EXT ||
                       f.kind == FSB_FILE_NATIVE_LIB || (f.flags & FSB_FILE_FS_REQUIRED);
    if (!extractable || !fsb_cache_safe(e->path, f.path.length))
        return corrupt(s, "extracted file", index);
    if (!fsb_get_blob(p, f.blob, &e->blob) || e->blob.size > FSB_MAX_MEMORY_BLOB) /* read whole */
        return corrupt(s, "blob of file", index);
    e->item = item;
    e->index = index;
    e->length = f.path.length;
    e->mode = f.mode;
    if (e->blob.digest != FSB_NONE)
        return fsb_get_digest(p, e->blob.digest, e->digest) || corrupt(s, "digest of file", index);
    if (!s->job->resources) return corrupt(s, "extracted file", index);
    uint8_t *content; /* no digest: the cached file is compared with the payload's content */
    if (!decode(s, e, &content)) return false;
    bool hashed = fsb_tree_digest(content, e->blob.size, e->digest);
    free(content);
    return hashed || out_of_memory(s);
}

/* ``fd`` if the directory open as ``fd`` is ours (and, if ``private``, forced to 0700); else -1
 * with the failure set and ``fd`` closed. */
static int owned(struct state *s, int fd, bool private, const char *below, size_t length) {
    struct stat st;
    int error = 0;
    if (fstat(fd, &st) != 0) {
        error = errno;
    } else if (st.st_uid != s->uid) {
        close(fd);
        failed_at(s, FSB_CACHE_FOREIGN, 0, below, length);
        return -1;
    } else if (private && (st.st_mode & 077) && fchmod(fd, 0700) != 0) {
        error = errno;
    }
    if (error == 0) return fd;
    close(fd);
    failed_at(s, FSB_CACHE_SYSTEM, error, below, length);
    return -1;
}

/* mkdir -p of the root: its parents as the umask leaves them, the root itself 0700. */
static bool make_root(struct state *s) {
    char path[PATH_MAX];
    size_t length = strlen(s->job->root);
    if (length >= sizeof path) return failed_at(s, FSB_CACHE_SYSTEM, ENAMETOOLONG, NULL, 0);
    memcpy(path, s->job->root, length + 1);
    for (size_t i = 1; i < length; i++) {
        if (path[i] != '/') continue;
        path[i] = '\0';
        int made = mkdir(path, 0777), error = errno;
        if (made != 0 && error != EEXIST) {
            s->job->failure = FSB_CACHE_SYSTEM;
            s->job->error = error;
            snprintf(s->job->path, sizeof s->job->path, "%s", path);
            return false;
        }
        path[i] = '/';
    }
    if (mkdir(path, 0700) != 0 && errno != EEXIST)
        return failed_at(s, FSB_CACHE_SYSTEM, errno, NULL, 0);
    return true;
}

/* The root, created if it is missing. It may be a symlink (FSBPYR_CACHE_DIR naming one), and its
 * mode is the user's business; it must be ours. */
static bool open_root(struct state *s) {
    int fd = open(s->job->root, DIRECTORY);
    if (fd < 0 && errno == ENOENT) {
        if (!make_root(s)) return false;
        fd = open(s->job->root, DIRECTORY);
    }
    if (fd < 0) return failed_at(s, FSB_CACHE_SYSTEM, errno, NULL, 0);
    s->fds[0] = owned(s, fd, false, NULL, 0);
    s->root_open = s->fds[0] >= 0;
    return s->root_open;
}

/* Directory ``name`` of the one open as ``parent``, created 0700 if missing; never a symlink. */
static int open_private(struct state *s, int parent, const char *name, const char *below,
                        size_t length) {
    int fd = openat(parent, name, DIRECTORY | O_NOFOLLOW);
    if (fd < 0 && errno == ENOENT) {
        if (mkdirat(parent, name, 0700) != 0 && errno != EEXIST) {
            failed_at(s, FSB_CACHE_SYSTEM, errno, below, length);
            return -1;
        }
        fd = openat(parent, name, DIRECTORY | O_NOFOLLOW);
    }
    if (fd < 0) {
        failed_at(s, FSB_CACHE_SYSTEM, errno, below, length);
        return -1;
    }
    return owned(s, fd, true, below, length);
}

/* The directory target[0, length) below the root: the directories open along the way stay
 * open, the others are closed (a tree of any width needs few descriptors), and the rest of the
 * path is opened, and created, one component at a time. -1 with the failure set. */
static int open_directory(struct state *s, const char *target, size_t length) {
    size_t common = 0;
    while (common < s->depth) {
        size_t start = common ? s->ends[common - 1] + 1 : 0, end = s->ends[common];
        if (end > length || (end < length && target[end] != '/') ||
            memcmp(s->path + start, target + start, end - start) != 0)
            break;
        common++;
    }
    while (s->depth > common) close(s->fds[s->depth--]);
    memcpy(s->path, target, length);
    for (size_t start = common ? s->ends[common - 1] + 1 : 0; start < length;) {
        const char *slash = memchr(target + start, '/', length - start);
        size_t end = slash ? (size_t)(slash - target) : length;
        char name[NAME_MAX + 1];
        if (end - start > NAME_MAX) {
            failed_at(s, FSB_CACHE_SYSTEM, ENAMETOOLONG, target, end);
            return -1;
        }
        memcpy(name, target + start, end - start);
        name[end - start] = '\0';
        int fd = open_private(s, s->fds[s->depth], name, target, end);
        if (fd < 0) return -1;
        s->fds[++s->depth] = fd;
        s->ends[s->depth - 1] = end;
        start = end + 1;
    }
    return s->fds[s->depth];
}

/* The directory the entry's file belongs in: <chain>/<its site directory>. */
static int open_parent(struct state *s, const struct entry *e) {
    const char *slash = memrchr(e->path, '/', e->length);
    size_t directory = slash ? (size_t)(slash - e->path) : 0;
    size_t length = s->chain_length + (slash ? 1 + directory : 0);
    char target[PATH_SIZE];
    if (length >= sizeof target) {
        file_failed(s, ENAMETOOLONG, e);
        return -1;
    }
    memcpy(target, s->job->chain, s->chain_length);
    if (slash) {
        target[s->chain_length] = '/';
        memcpy(target + s->chain_length + 1, e->path, directory);
    }
    return open_directory(s, target, length);
}

static const char *base_name(const struct entry *e) {
    const char *slash = memrchr(e->path, '/', e->length);
    return slash ? slash + 1 : e->path;
}

/* Read the whole file open as ``fd`` (``size`` bytes, at most SMALL_FILE) into the batch's
 * buffer; false if fewer bytes are there. */
static bool read_small(struct state *s, int fd, uint64_t size, const uint8_t **data) {
    uint8_t *into = s->buffer + s->buffered;
    for (uint64_t got = 0; got < size;) {
        ssize_t n = read(fd, into + got, (size_t)(size - got));
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        got += (uint64_t)n;
    }
    *data = into;
    s->buffered += size;
    return true;
}

/* PRESENT: the file is ours, of the payload's size, and in the pass: read into *data, or, if
 * larger than SMALL_FILE, open as *fd (until the pass is hashed); MISSING: nothing is there;
 * CHANGED: something else is (a symlink, a device, not a regular file of ours, another size,
 * unreadable, stale on NFS), so it is replaced; NO_ROOM: no descriptor or memory left to open it
 * (s->no_room); FAILED: the name is too long for the file system: no file can be there, so
 * nothing to report or restore. A small file's descriptor is closed at once. */
static int load_cached(struct state *s, int directory, const struct entry *e,
                       const uint8_t **data, int *fd) {
    uint64_t size = e->blob.size;
    *data = NULL;
    do /* O_NOFOLLOW: a symlink is replaced; O_NONBLOCK: a FIFO is replaced, never waited for */
        *fd = openat(directory, base_name(e), O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    while (*fd < 0 && errno == EINTR);
    if (*fd < 0) {
        int error = errno;
        if (error == ENOENT) return MISSING;
        if (error == EMFILE || error == ENFILE || error == ENOMEM) {
            s->no_room = error;
            return NO_ROOM;
        }
        if (error != ENAMETOOLONG) return CHANGED;
        file_failed(s, error, e);
        return FAILED;
    }
    struct stat st;
    int found = CHANGED;
    if (fstat(*fd, &st) == 0 && S_ISREG(st.st_mode) && st.st_uid == s->uid &&
        (uint64_t)st.st_size == size) {
        if (size > SMALL_FILE) return PRESENT; /* read leaf by leaf as the pass is hashed */
        found = size == 0 || read_small(s, *fd, size, data) ? PRESENT : CHANGED;
    }
    close(*fd);
    *fd = -1;
    return found;
}

static int write_all(int fd, const uint8_t *data, uint64_t size) {
    while (size > 0) {
        ssize_t written = write(fd, data, (size_t)(size < WRITE_PIECE ? size : WRITE_PIECE));
        if (written < 0 && errno == EINTR) continue;
        if (written < 0) return errno;
        if (written == 0) return EIO;
        data += written;
        size -= (uint64_t)written;
    }
    return 0;
}

/* Write the file under a temporary name, then rename it into place: readers see a whole file or
 * none. Not synced to disk (D34; fsync made torch's first run 42.5 s instead of 2.3 s). */
static bool publish(struct state *s, int directory, const struct entry *e,
                    const uint8_t *content) {
    char temporary[64];
    int fd = -1;
    for (unsigned attempt = 0; fd < 0 && attempt < TEMPORARY_ATTEMPTS; attempt++) {
        snprintf(temporary, sizeof temporary, ".fsbpyr-%ld-%lu", (long)getpid(),
                 atomic_fetch_add(&temporaries, 1));
        fd = openat(directory, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,
                    0600);
        if (fd < 0 && errno != EEXIST) break;
    }
    if (fd < 0) return file_failed(s, errno, e);
    int error = write_all(fd, content, e->blob.size);
    /* readable by its owner, executable where the payload says so, never writable */
    if (error == 0 && fchmod(fd, S_IRUSR | (e->mode & S_IXUSR)) != 0) error = errno;
    if (error == 0 && renameat(directory, temporary, directory, base_name(e)) != 0) error = errno;
    if (error != 0) unlinkat(directory, temporary, 0);
    close(fd);
    return error == 0 || file_failed(s, error, e);
}

/* Write the entry's file from the payload, its content checked against the digest first. */
static bool restore(struct state *s, const struct entry *e) {
    uint8_t *content, digest[FSB_DIGEST_SIZE];
    if (!decode(s, e, &content)) return false;
    bool done = false;
    if (!fsb_tree_digest(content, e->blob.size, digest)) {
        out_of_memory(s);
    } else if (memcmp(digest, e->digest, sizeof digest) != 0) {
        corrupt(s, "digest of file", e->index);
    } else {
        int directory = open_parent(s, e);
        done = directory >= 0 && publish(s, directory, e, content);
    }
    free(content);
    if (done) s->job->outcome[e->item] |= FSB_CACHE_DONE | FSB_CACHE_WRITTEN;
    return done;
}

static void close_batch(struct state *s, unsigned count) {
    for (unsigned i = 0; i < count; i++)
        if (s->descriptors[i] >= 0) close(s->descriptors[i]);
    s->held = 0;
}

/* Hash the files of the batch in one parallel pass; restore those that differ. */
static bool check_batch(struct state *s) {
    unsigned count = s->batched;
    s->batched = 0;
    s->buffered = 0;
    /* EIO: a file that could not be read whole has a digest that cannot match: it is restored */
    int status =
        count ? fsb_tree_digests(count, s->data, s->descriptors, s->lengths, s->digests) : 0;
    close_batch(s, count);
    if (status == ENOMEM) return out_of_memory(s);
    for (unsigned i = 0; i < count; i++) {
        const struct entry *e = s->batch[i];
        if (memcmp(s->digests[i], e->digest, FSB_DIGEST_SIZE) == 0) {
            s->job->outcome[e->item] |= FSB_CACHE_DONE;
            continue;
        }
        s->job->outcome[e->item] |= FSB_CACHE_CHANGED;
        if (!restore(s, e)) return false;
    }
    return true;
}

/* After a failure for want of descriptors or memory while the pass holds files: check the pass,
 * which closes them, and say whether to try once more (the failure is cleared then). */
static bool room_made(struct state *s) {
    struct fsb_cache_sync *job = s->job;
    if (s->batched == 0 || job->failure != FSB_CACHE_SYSTEM ||
        (job->error != EMFILE && job->error != ENFILE && job->error != ENOMEM))
        return false;
    job->failure = FSB_CACHE_OK;
    return check_batch(s);
}

static bool sync_file(struct state *s, const struct entry *e) {
    uint64_t size = e->blob.size;
    if (size <= SMALL_FILE) { /* room in the buffer, before any directory is opened for it */
        if (s->buffered + size > BATCH_BUFFER && !check_batch(s)) return false;
        if (s->buffer == NULL && (s->buffer = malloc(BATCH_BUFFER)) == NULL)
            return out_of_memory(s);
    }
    int directory = open_parent(s, e);
    if (directory < 0 && room_made(s)) directory = open_parent(s, e);
    if (directory < 0) return false;
    unsigned at = s->batched;
    int found = load_cached(s, directory, e, &s->data[at], &s->descriptors[at]);
    if (found == NO_ROOM) {
        file_failed(s, s->no_room, e);
        if (!room_made(s) || (directory = open_parent(s, e)) < 0) return false;
        at = s->batched;
        found = load_cached(s, directory, e, &s->data[at], &s->descriptors[at]);
        if (found == NO_ROOM) return file_failed(s, s->no_room, e);
    }
    if (found == FAILED) return false;
    if (found == PRESENT) {
        s->batch[at] = e;
        s->lengths[at] = size;
        if (s->descriptors[at] >= 0) s->held++;
        return (++s->batched < CHECK_BATCH && s->held < HELD_FILES) || check_batch(s);
    }
    if (found == CHANGED) s->job->outcome[e->item] |= FSB_CACHE_CHANGED;
    return restore(s, e);
}

void fsb_cache_sync(const struct fsb_payload *payload, struct fsb_cache_sync *job) {
    job->failure = FSB_CACHE_OK;
    struct state *s = calloc(1, sizeof *s);
    struct entry *entries = malloc((size_t)job->count * sizeof *entries + 1);
    if (s == NULL || entries == NULL) {
        job->failure = FSB_CACHE_MEMORY;
        free(s);
        free(entries);
        return;
    }
    s->payload = payload;
    s->job = job;
    s->chain_length = strlen(job->chain);
    s->uid = geteuid();
    bool ok = true; /* every record checked before anything on disk is touched */
    for (uint64_t i = 0; ok && i < job->count; i++) ok = prepare(s, i, &entries[i]);
    ok = ok && job->count > 0 && open_root(s); /* nothing to check: nothing touched */
    for (uint64_t i = 0; ok && i < job->count; i++) ok = sync_file(s, &entries[i]);
    if (ok) check_batch(s);
    close_batch(s, s->batched); /* a failure left them open */
    while (s->depth > 0) close(s->fds[s->depth--]);
    if (s->root_open) close(s->fds[0]);
    free(s->buffer);
    fsb_decoder_free(s->decoder);
    free(entries);
    free(s);
}
