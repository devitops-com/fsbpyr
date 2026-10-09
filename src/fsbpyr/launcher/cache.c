/* The native cache (see cache.h). */
#include "cache.h"

#include "integrity.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
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
 * libtorch_cpu.so took 156 MB of RSS instead of 60 MB (XFS, Linux 6.12; D36). A piece of zeros
 * is not written: runs of zeros become holes (D38). */
#define WRITE_PIECE (UINT64_C(1) << 20)
#define DECODER_BLOCKS 4u /* blocks a sync's own decoder keeps (D30): files restored in path order */
#define DIRECTORY (O_RDONLY | O_DIRECTORY | O_CLOEXEC)
#define TEMPORARY_ATTEMPTS 100u
/* Directories kept open from the root down: a deeper one is opened again from the deepest open
 * one when it is needed, so a path of any depth takes at most OPEN_LEVELS + 1 descriptors (D38).
 * Directories this deep are rare: walking down again costs little. */
#define OPEN_LEVELS 16u
/* The collector's names (D38, cache.h): the root's marker, a tree's lock file and the
 * directory files are written in before they are renamed into place. */
#define MARKER ".fsbpyr-cache"
#define MARKER_TEXT "fsbpyr's cache: collected by fsbpyr (D38)\n"
#define LOCK ".lock"
#define SCRATCH ".scratch"
#define DAY 86400 /* a tree's lock is touched once it is this old: its mtime is the last use */
#define HOLD_ATTEMPTS 8u /* a tree renamed away (collected) while taken: taken again */

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

struct level {  /* a directory below the root: path[0, end) */
    size_t end;
    int fd;     /* -1 if closed: deeper than OPEN_LEVELS, and not the deepest */
};

struct state {
    const struct fsb_payload *payload;
    struct fsb_decoder *decoder; /* this sync's own (no GIL here), once a blob needs one */
    struct fsb_cache_sync *job;
    size_t chain_length;
    uid_t uid;
    int root;    /* -1 until open */
    int scratch; /* <the chain's parent>/.scratch, once a file is written; -1 */
    /* The directories from the root down to the last one used: ``depth`` levels of ``path``. */
    struct level *levels;
    size_t depth, levels_capacity;
    char *path, *target; /* the levels' path; open_parent's */
    size_t path_capacity, target_capacity;
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
    if (length > sizeof job->path) length = sizeof job->path; /* the message only: cut short */
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

static bool zeros(const uint8_t *data, size_t size) {
    return size == 0 || (data[0] == 0 && memcmp(data, data + 1, size - 1) == 0);
}

/* data[0, length) at ``offset`` of the file open as ``fd``, in pieces of at most WRITE_PIECE;
 * a piece of zeros is skipped (a hole). *end: where the last piece written ends. 0 or an errno. */
static int write_window(int fd, uint64_t offset, const uint8_t *data, uint64_t length,
                        uint64_t *end) {
    for (uint64_t at = 0; at < length;) {
        size_t size = (size_t)(length - at < WRITE_PIECE ? length - at : WRITE_PIECE);
        if (!zeros(data + at, size)) {
            for (size_t done = 0; done < size;) {
                ssize_t n = pwrite(fd, data + at + done, size - done, (off_t)(offset + at + done));
                if (n < 0 && errno == EINTR) continue;
                if (n < 0) return errno;
                if (n == 0) return EIO;
                done += (size_t)n;
            }
            *end = offset + at + size;
        }
        at += size;
    }
    return 0;
}

/* The entry's content from the payload a window at a time (D38): its digest into ``digest``
 * and, if ``fd`` >= 0, the content written to the file open as ``fd``. False with the failure
 * set. */
static bool stream_content(struct state *s, const struct entry *e, int fd,
                           uint8_t digest[FSB_DIGEST_SIZE]) {
    if (s->decoder == NULL && (s->decoder = fsb_decoder_new(DECODER_BLOCKS)) == NULL)
        return out_of_memory(s);
    struct fsb_stream *stream = fsb_stream_open(s->payload, s->decoder, &e->blob);
    if (stream == NULL) return out_of_memory(s);
    struct fsb_tree tree;
    fsb_tree_init(&tree);
    const uint8_t *data;
    uint64_t length, offset = 0, end = 0;
    int status, error = 0;
    while ((status = fsb_stream_next(stream, &data, &length)) == FSB_STREAM_DATA) {
        if (fsb_tree_add(&tree, data, length) != 0) {
            status = FSB_STREAM_MEMORY;
            break;
        }
        if (fd >= 0 && (error = write_window(fd, offset, data, length, &end)) != 0) break;
        offset += length;
    }
    if (status == FSB_STREAM_SYSTEM) error = fsb_stream_error(stream); /* reading the executable */
    fsb_stream_close(stream);
    if (error != 0) return file_failed(s, error, e);
    if (status == FSB_STREAM_MEMORY) return out_of_memory(s);
    if (status != FSB_STREAM_END) return corrupt(s, "blob data of file", e->index);
    /* a hole at the end: the file gets its size (a truncate costs XFS a transaction: only then) */
    if (fd >= 0 && end < e->blob.size && ftruncate(fd, (off_t)e->blob.size) != 0)
        return file_failed(s, errno, e);
    fsb_tree_final(&tree, digest);
    return true;
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
    if (!fsb_get_blob(p, f.blob, &e->blob)) return corrupt(s, "blob of file", index);
    e->item = item;
    e->index = index;
    e->length = f.path.length;
    e->mode = f.mode;
    if (e->blob.digest != FSB_NONE)
        return fsb_get_digest(p, e->blob.digest, e->digest) || corrupt(s, "digest of file", index);
    if (!s->job->resources) return corrupt(s, "extracted file", index);
    return stream_content(s, e, -1, e->digest); /* no digest: the payload's content is hashed */
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
    s->root = owned(s, fd, false, NULL, 0);
    return s->root >= 0;
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

/* *buffer holding at least ``needed`` items of ``size`` bytes; false if out of memory. */
static bool reserve(void **buffer, size_t *capacity, size_t needed, size_t size) {
    if (needed <= *capacity) return true;
    size_t grown = *capacity * 2 > needed ? *capacity * 2 : needed;
    void *more = realloc(*buffer, grown * size);
    if (more == NULL) return false;
    *buffer = more;
    *capacity = grown;
    return true;
}

/* Level ``level`` of ``path``, opened (and created) in the directory open as ``parent``. */
static int open_level(struct state *s, int parent, size_t level) {
    size_t start = level ? s->levels[level - 1].end + 1 : 0, end = s->levels[level].end;
    char name[NAME_MAX + 1];
    if (end - start > NAME_MAX) {
        failed_at(s, FSB_CACHE_SYSTEM, ENAMETOOLONG, s->path, end);
        return -1;
    }
    memcpy(name, s->path + start, end - start);
    name[end - start] = '\0';
    return open_private(s, parent, name, s->path, end);
}

/* The descriptor of level ``level``: open, or opened again down from the deepest open level
 * above it; levels deeper than OPEN_LEVELS opened on the way are closed again. */
static int level_fd(struct state *s, size_t level) {
    size_t from = level + 1; /* levels [from, level] are closed */
    while (from > 0 && s->levels[from - 1].fd < 0) from--;
    int parent = from > 0 ? s->levels[from - 1].fd : s->root;
    for (size_t i = from; i <= level; i++) {
        int fd = open_level(s, parent, i);
        if (i > from && i - 1 >= OPEN_LEVELS) { /* opened on the way: not kept */
            close(parent);
            s->levels[i - 1].fd = -1;
        }
        if (fd < 0) return -1;
        s->levels[i].fd = parent = fd;
    }
    return s->levels[level].fd;
}

/* The directory target[0, length) below the root: the levels it shares with the last one stay,
 * the others are closed (a tree of any width needs few descriptors), and the rest of the path
 * is opened, and created, one component at a time. -1 with the failure set. */
static int open_directory(struct state *s, const char *target, size_t length) {
    size_t common = 0;
    while (common < s->depth) {
        size_t start = common ? s->levels[common - 1].end + 1 : 0, end = s->levels[common].end;
        if (end > length || (end < length && target[end] != '/') ||
            memcmp(s->path + start, target + start, end - start) != 0)
            break;
        common++;
    }
    while (s->depth > common)
        if (s->levels[--s->depth].fd >= 0) close(s->levels[s->depth].fd);
    if (!reserve((void **)&s->path, &s->path_capacity, length + 1, 1)) {
        out_of_memory(s);
        return -1;
    }
    memcpy(s->path, target, length);
    int parent = common ? level_fd(s, common - 1) : s->root;
    for (size_t start = common ? s->levels[common - 1].end + 1 : 0; parent >= 0 && start < length;) {
        const char *slash = memchr(target + start, '/', length - start);
        size_t end = slash ? (size_t)(slash - target) : length;
        if (!reserve((void **)&s->levels, &s->levels_capacity, s->depth + 1, sizeof *s->levels)) {
            out_of_memory(s);
            return -1;
        }
        s->levels[s->depth] = (struct level){.end = end, .fd = -1};
        int fd = open_level(s, parent, s->depth);
        if (fd < 0) return -1;
        if (s->depth > OPEN_LEVELS && s->levels[s->depth - 1].fd >= 0) { /* only the deepest */
            close(s->levels[s->depth - 1].fd);
            s->levels[s->depth - 1].fd = -1;
        }
        s->levels[s->depth++].fd = parent = fd;
        start = end + 1;
    }
    return parent;
}

/* The directory path[0, length) below the root, opened (and created) one component at a time
 * as open_directory does, but apart from the levels: a descriptor of the caller's (the root's
 * duplicate if ``length`` is 0), or -1 with the failure set. */
static int walk(struct state *s, const char *path, size_t length) {
    int fd = s->root;
    for (size_t start = 0; start < length;) {
        const char *slash = memchr(path + start, '/', length - start);
        size_t end = slash ? (size_t)(slash - path) : length;
        char name[NAME_MAX + 1];
        int next = -1;
        if (end - start > NAME_MAX) {
            failed_at(s, FSB_CACHE_SYSTEM, ENAMETOOLONG, path, end);
        } else {
            memcpy(name, path + start, end - start);
            name[end - start] = '\0';
            next = open_private(s, fd, name, path, end);
        }
        if (fd != s->root) close(fd);
        if (next < 0) return -1;
        fd = next;
        start = end + 1;
    }
    if (fd != s->root) return fd;
    if ((fd = fcntl(s->root, F_DUPFD_CLOEXEC, 0)) < 0)
        failed_at(s, FSB_CACHE_SYSTEM, errno, NULL, 0);
    return fd;
}

/* Where files are written before they are renamed into place: <the chain's parent>/.scratch,
 * so that a collector finds what a killed process left (D38); opened once a sync writes. */
static int open_scratch(struct state *s) {
    if (s->scratch >= 0) return s->scratch;
    const char *chain = s->job->chain, *slash = memrchr(chain, '/', s->chain_length);
    size_t parent = slash ? (size_t)(slash - chain) + 1 : 0, length = parent + strlen(SCRATCH);
    if (!reserve((void **)&s->target, &s->target_capacity, length + 1, 1)) {
        out_of_memory(s);
        return -1;
    }
    memcpy(s->target, chain, parent);
    memcpy(s->target + parent, SCRATCH, strlen(SCRATCH));
    s->scratch = walk(s, s->target, length);
    return s->scratch;
}

/* The directory the entry's file belongs in: <chain>/<its site directory>. */
static int open_parent(struct state *s, const struct entry *e) {
    const char *slash = memrchr(e->path, '/', e->length);
    size_t directory = slash ? (size_t)(slash - e->path) : 0;
    size_t length = s->chain_length + (slash ? 1 + directory : 0);
    if (!reserve((void **)&s->target, &s->target_capacity, length + 1, 1)) {
        out_of_memory(s);
        return -1;
    }
    memcpy(s->target, s->job->chain, s->chain_length);
    if (slash) {
        s->target[s->chain_length] = '/';
        memcpy(s->target + s->chain_length + 1, e->path, directory);
    }
    return open_directory(s, s->target, length);
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
    do /* O_NOFOLLOW: a symlink is replaced; O_NONBLOCK: a FIFO is replaced, never waited for;
        * O_NOCTTY: a terminal device put there never becomes the process's terminal */
        *fd = openat(directory, base_name(e),
                     O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK | O_NOCTTY);
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

/* Write the entry's file from the payload under a temporary name in the scratch directory,
 * locked while it is written (a collector removes only what it can lock), a window at a time,
 * hashing what it writes, then rename it into place if it has the payload's digest: readers see
 * a whole file or none. Not synced to disk (D34; fsync made torch's first run 42.5 s instead of
 * 2.3 s). */
static bool restore(struct state *s, const struct entry *e) {
    int directory = open_parent(s, e);
    int scratch = directory < 0 ? -1 : open_scratch(s);
    if (scratch < 0) return false;
    char temporary[64];
    int fd = -1;
    for (unsigned attempt = 0; fd < 0 && attempt < TEMPORARY_ATTEMPTS; attempt++) {
        snprintf(temporary, sizeof temporary, ".fsbpyr-%ld-%lu", (long)getpid(),
                 atomic_fetch_add(&temporaries, 1));
        fd = openat(scratch, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,
                    0600);
        if (fd < 0 && errno != EEXIST) break;
    }
    if (fd < 0) return file_failed(s, errno, e);
    flock(fd, LOCK_EX | LOCK_NB); /* a new file: nobody else holds it */
    uint8_t digest[FSB_DIGEST_SIZE];
    bool done = stream_content(s, e, fd, digest);
    if (done && memcmp(digest, e->digest, sizeof digest) != 0)
        done = corrupt(s, "digest of file", e->index);
    /* readable by its owner, executable where the payload says so, never writable */
    if (done && fchmod(fd, S_IRUSR | (e->mode & S_IXUSR)) != 0) done = file_failed(s, errno, e);
    if (done && renameat(scratch, temporary, directory, base_name(e)) != 0)
        done = file_failed(s, errno, e);
    if (!done) unlinkat(scratch, temporary, 0);
    close(fd);
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
        if (s->buffer == NULL) { /* mapped: unmapped at the end, never left in malloc's heap */
            void *buffer = mmap(NULL, BATCH_BUFFER, PROT_READ | PROT_WRITE,
                                MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
            if (buffer == MAP_FAILED) return out_of_memory(s);
            s->buffer = buffer;
        }
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

/* The names a cache root may hold (the collector's gc.OURS): the app cache's and the tool's,
 * whose root fsbpyr's own app cache shares. */
static const char *const OURS[] = {
    MARKER, ".gc", ".trash", "runtime", "resources", ".gc-tool", ".trash-tool", "runtimes", "pbs",
    "sysroots", "downloads",
};

/* Whether ``kind`` (a directory of the root) holds a tree of ours: one with its lock or its site
 * (the collector's PROOF). */
static bool holds_a_tree(struct state *s, const char *kind) {
    int fd = openat(s->root, kind, DIRECTORY | O_NOFOLLOW);
    DIR *directory = fd < 0 ? NULL : fdopendir(fd);
    if (directory == NULL) {
        if (fd >= 0) close(fd);
        return false;
    }
    bool found = false;
    for (struct dirent *d; !found && (d = readdir(directory)) != NULL;) {
        char path[NAME_MAX + sizeof "/site"];
        struct stat st;
        for (int i = 0; !found && d->d_name[0] != '.' && i < 2; i++)
            found = snprintf(path, sizeof path, "%s/%s", d->d_name, i ? "site" : LOCK) <
                        (int)sizeof path &&
                    fstatat(dirfd(directory), path, &st, AT_SYMLINK_NOFOLLOW) == 0;
    }
    closedir(directory);
    return found;
}

/* Whether the root may be marked: it holds nothing but our names, and is new (empty) or holds a
 * tree of ours (a cache of an earlier version). A directory of the user's never may, even one
 * that holds nothing but names like ours. */
static bool markable(struct state *s) {
    int fd = openat(s->root, ".", DIRECTORY);
    DIR *directory = fd < 0 ? NULL : fdopendir(fd);
    if (directory == NULL) {
        if (fd >= 0) close(fd);
        return false;
    }
    bool only = true, empty = true;
    for (struct dirent *d; only && (d = readdir(directory)) != NULL;) {
        if (strcmp(d->d_name, ".") == 0 || strcmp(d->d_name, "..") == 0) continue;
        only = empty = false;
        for (size_t i = 0; !only && i < sizeof OURS / sizeof *OURS; i++)
            only = strcmp(d->d_name, OURS[i]) == 0;
    }
    closedir(directory);
    return only && (empty || holds_a_tree(s, "runtime") || holds_a_tree(s, "resources"));
}

/* The marker at the root, the collector's leave to act on it (D38): written if it is missing and
 * the root may be marked (markable). Best effort: a root that cannot be marked is used
 * unmarked. */
static void mark(struct state *s) {
    struct stat st;
    if (fstatat(s->root, MARKER, &st, AT_SYMLINK_NOFOLLOW) == 0 || errno != ENOENT ||
        !markable(s))
        return;
    int fd = openat(s->root, MARKER, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return; /* marked meanwhile, or not ours to mark */
    (void)write_window(fd, 0, (const uint8_t *)MARKER_TEXT, strlen(MARKER_TEXT), &(uint64_t){0});
    close(fd);
}

/* Whether ``fd`` (a tree's lock, held) is still the lock at <root>/<chain[0, tree)>/.lock: a
 * collector renames a tree away while it holds the lock exclusively. */
static bool still_the_lock(struct state *s, int fd, size_t tree) {
    int directory = walk(s, s->job->chain, tree);
    struct stat held, now;
    bool same = directory >= 0 && fstat(fd, &held) == 0 &&
                fstatat(directory, LOCK, &now, AT_SYMLINK_NOFOLLOW) == 0 &&
                held.st_dev == now.st_dev && held.st_ino == now.st_ino;
    if (directory >= 0) close(directory);
    return same;
}

/* Take the tree, the chain's parent ("runtime/<id>"), for the process's life (D38), once the
 * records are checked and the root is open: the root marked, then the tree's .lock, opened
 * through the checked walk, held LOCK_SH (waiting for a collector that holds it) and taken
 * again if a collector renamed the tree away meanwhile; touched once a day old: its mtime is
 * when the tree was last used. Best effort: a tree that cannot be held (a read-only cache, a
 * file system without locks, a lock that is no regular file of ours, a tree collected each time
 * it was taken) is used unheld, and a failure on the way is the sync's to meet. The lock's
 * descriptor, or -1. */
static int take(struct state *s) {
    const char *chain = s->job->chain, *slash = memrchr(chain, '/', s->chain_length);
    size_t tree = slash ? (size_t)(slash - chain) : 0;
    int held = -1;
    mark(s);
    for (unsigned attempt = 0; held < 0 && attempt < HOLD_ATTEMPTS; attempt++) {
        int directory = walk(s, chain, tree);
        if (directory < 0) break;
        int fd = openat(directory, LOCK,
                        O_RDONLY | O_CREAT | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK | O_NOCTTY, 0600);
        int error = errno;
        close(directory);
        if (fd < 0 && error == ENOENT) continue; /* the tree removed meanwhile: made afresh */
        struct stat st;
        int locked = -1;
        if (fd >= 0 && fstat(fd, &st) == 0 && S_ISREG(st.st_mode) && st.st_uid == s->uid)
            while ((locked = flock(fd, LOCK_SH)) != 0 && errno == EINTR) {}
        if (locked == 0 && still_the_lock(s, fd, tree)) {
            held = fd;
        } else if (fd >= 0) {
            close(fd);
        }
        if (locked != 0) break;
    }
    s->job->failure = FSB_CACHE_OK; /* the walk's failure, if any: the sync meets it again */
    struct stat st;
    time_t now = time(NULL);
    if (held >= 0 && fstat(held, &st) == 0 && (st.st_mtime <= now - DAY || st.st_mtime > now))
        (void)futimens(held, NULL); /* its mtime: the tree's last use, a day at most behind */
    return held;
}

void fsb_cache_sync(const struct fsb_payload *payload, struct fsb_cache_sync *job) {
    job->failure = FSB_CACHE_OK;
    job->lock = -1;
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
    s->root = s->scratch = -1;
    bool ok = true; /* every record checked before anything on disk is touched */
    for (uint64_t i = 0; ok && i < job->count; i++) ok = prepare(s, i, &entries[i]);
    ok = ok && job->count > 0 && open_root(s); /* nothing to check: nothing touched */
    if (ok && job->hold) job->lock = take(s);
    for (uint64_t i = 0; ok && i < job->count; i++) ok = sync_file(s, &entries[i]);
    if (ok) check_batch(s);
    close_batch(s, s->batched); /* a failure left them open */
    while (s->depth > 0)
        if (s->levels[--s->depth].fd >= 0) close(s->levels[s->depth].fd);
    if (s->root >= 0) close(s->root);
    if (s->scratch >= 0) close(s->scratch);
    free(s->levels);
    free(s->path);
    free(s->target);
    if (s->buffer != NULL) munmap(s->buffer, BATCH_BUFFER);
    fsb_decoder_free(s->decoder);
    free(entries);
    free(s);
}
