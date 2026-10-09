/* The cache root's way from "/" (see way.h). */
#include "way.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define SYMLINKS 40u /* followed on the way, as the kernel's own limit */
#define DIRECTORY (O_PATH | O_DIRECTORY | O_CLOEXEC)

struct walk {
    char *pending;  /* what is left to walk: "a/b/c", a symlink's target spliced in front */
    size_t capacity;
    char *walked;   /* the real path of ``fd``: "" for "/" */
    char *target;   /* a symlink's, PATH_MAX bytes */
    int fd;         /* the directory the walk is in (O_PATH) */
    unsigned links; /* symlinks followed */
    bool shared;    /* past a directory others may add to (sticky): only root's or ours below */
    uid_t uid;
    gid_t gid;
    char *where; /* the caller's: the path at fault */
    size_t size;
    int *error;
};

static enum fsb_way fail(struct walk *w, enum fsb_way way, int error, const char *path) {
    *w->error = error;
    snprintf(w->where, w->size, "%s", path[0] != '\0' ? path : "/");
    return way;
}

/* Whether ``uid`` is the overflow uid and no uid of this process's user namespace maps to it
 * (in the initial namespace every uid does): the owner is outside the namespace (root, mostly:
 * the sandbox's own layout). */
static bool outside_owner(uid_t uid) {
    char line[128];
    FILE *f = fopen("/proc/sys/kernel/overflowuid", "re");
    unsigned long overflow = 0, inside, outside, count;
    bool read = f != NULL && fscanf(f, "%lu", &overflow) == 1;
    if (f != NULL) fclose(f);
    if (!read || uid != (uid_t)overflow || (f = fopen("/proc/self/uid_map", "re")) == NULL)
        return false;
    bool mapped = false; /* in the initial namespace every uid is */
    while (fgets(line, sizeof line, f) != NULL &&
           sscanf(line, "%lu %lu %lu", &inside, &outside, &count) == 3)
        mapped |= overflow >= inside && overflow - inside < count;
    fclose(f);
    return !mapped;
}

/* Whether ``uid`` may own a directory or symlink on the way: root, we, or (above any directory
 * others may add to) an owner outside our user namespace. */
static bool our_owner(const struct walk *w, uid_t uid) {
    return uid == 0 || uid == w->uid || (!w->shared && outside_owner(uid));
}

/* The directory the walk is in (at ``walked``) checked: see way.h. */
static enum fsb_way trusted(struct walk *w) {
    struct stat st;
    if (fstat(w->fd, &st) != 0) return fail(w, FSB_WAY_SYSTEM, errno, w->walked);
    if (!our_owner(w, st.st_uid)) return fail(w, FSB_WAY_FOREIGN, 0, w->walked);
    bool others = (st.st_mode & S_IWOTH) || ((st.st_mode & S_IWGRP) && st.st_gid != w->gid);
    if (others && !(st.st_mode & S_ISVTX)) return fail(w, FSB_WAY_EXPOSED, 0, w->walked);
    w->shared |= others; /* sticky: others may add entries, so only root's or ours count */
    return FSB_WAY_TRUSTED;
}

/* The walk moved to the directory ``fd`` (opened from where it was), then checked. */
static enum fsb_way moved(struct walk *w, int fd, int error) {
    close(w->fd);
    w->fd = fd;
    return fd < 0 ? fail(w, FSB_WAY_SYSTEM, error, w->walked) : trusted(w);
}

static enum fsb_way from_slash(struct walk *w) {
    w->walked[0] = '\0';
    int fd = open("/", DIRECTORY);
    return moved(w, fd, errno);
}

static enum fsb_way up(struct walk *w) {
    char *slash = strrchr(w->walked, '/');
    if (slash != NULL) *slash = '\0';
    int fd = openat(w->fd, "..", DIRECTORY);
    return moved(w, fd, errno);
}

/* The symlink ``name`` (``st``) followed: its target spliced in front of what is left (``*at``).
 * The link itself is at ``walked``/``name`` for messages. */
static enum fsb_way follow(struct walk *w, const char *name, const struct stat *st, char **at) {
    size_t end = strlen(w->walked);
    snprintf(w->walked + end, PATH_MAX + NAME_MAX + 2 - end, "/%s", name);
    if (!our_owner(w, st->st_uid)) return fail(w, FSB_WAY_FOREIGN, 0, w->walked); /* in /tmp */
    if (++w->links > SYMLINKS) return fail(w, FSB_WAY_SYSTEM, ELOOP, w->walked);
    ssize_t n = readlinkat(w->fd, name, w->target, PATH_MAX - 1);
    if (n <= 0) return fail(w, FSB_WAY_SYSTEM, n < 0 ? errno : ENOENT, w->walked);
    size_t rest = strlen(*at);
    if ((size_t)n + 2 + rest > w->capacity)
        return fail(w, FSB_WAY_SYSTEM, ENAMETOOLONG, w->walked);
    w->walked[end] = '\0';
    memmove(w->pending + n + 1, *at, rest + 1);
    memcpy(w->pending, w->target, (size_t)n);
    w->pending[n] = '/';
    *at = w->pending;
    return w->target[0] == '/' ? from_slash(w) : FSB_WAY_TRUSTED;
}

static enum fsb_way down(struct walk *w, const char *name) {
    size_t end = strlen(w->walked);
    snprintf(w->walked + end, PATH_MAX + NAME_MAX + 2 - end, "/%s", name);
    int fd = openat(w->fd, name, DIRECTORY | O_NOFOLLOW);
    return moved(w, fd, errno);
}

/* The next part of ``*at`` into ``name`` (NAME_MAX + 1 bytes): its length, 0 at the end;
 * empty and "." parts skipped. */
static size_t next_part(char **at, char *name) {
    for (;;) {
        *at += strspn(*at, "/");
        size_t length = strcspn(*at, "/");
        if (length == 0) return 0;
        bool dot = length == 1 && (*at)[0] == '.';
        if (!dot && length <= NAME_MAX) memcpy(name, *at, length);
        *at += length;
        if (!dot) {
            name[length <= NAME_MAX ? length : 0] = '\0';
            return length;
        }
    }
}

/* Whether a ".." is ahead in ``at``. */
static bool up_ahead(char *at) {
    char name[NAME_MAX + 1];
    for (size_t length; (length = next_part(&at, name)) != 0;)
        if (strcmp(name, "..") == 0) return true;
    return false;
}

static enum fsb_way walk(struct walk *w, const char *root) {
    char name[NAME_MAX + 1];
    char *at = w->pending;
    enum fsb_way way = from_slash(w);
    for (size_t length; way == FSB_WAY_TRUSTED && (length = next_part(&at, name)) != 0;) {
        struct stat st;
        if (length > NAME_MAX || strlen(w->walked) + 1 + length >= PATH_MAX + NAME_MAX + 2)
            return fail(w, FSB_WAY_SYSTEM, ENAMETOOLONG, root);
        if (strcmp(name, "..") == 0) {
            way = up(w);
        } else if (fstatat(w->fd, name, &st, AT_SYMLINK_NOFOLLOW) != 0) {
            if (errno != ENOENT) return fail(w, FSB_WAY_SYSTEM, errno, w->walked);
            if (!up_ahead(at)) return FSB_WAY_PARTIAL; /* the rest does not exist yet */
            size_t end = strlen(w->walked);
            snprintf(w->walked + end, PATH_MAX + NAME_MAX + 2 - end, "/%s", name);
            return fail(w, FSB_WAY_SYSTEM, ENOENT, w->walked); /* "missing/..": as the kernel */
        } else if (S_ISLNK(st.st_mode)) {
            way = follow(w, name, &st, &at);
        } else {
            way = down(w, name);
        }
    }
    return way;
}

enum fsb_way fsb_way_check(const char *root, char *where, size_t size, int *error) {
    struct walk w = {.capacity = 2 * PATH_MAX + 2, .fd = -1, .uid = geteuid(), .gid = getegid(),
                     .where = where, .size = size, .error = error};
    w.pending = malloc(w.capacity);
    w.walked = malloc(PATH_MAX + NAME_MAX + 2);
    w.target = malloc(PATH_MAX);
    enum fsb_way way = FSB_WAY_MEMORY;
    if (w.pending != NULL && w.walked != NULL && w.target != NULL) {
        size_t cwd = 0;
        if (root[0] != '/') {
            if (getcwd(w.pending, PATH_MAX) == NULL) {
                way = fail(&w, FSB_WAY_SYSTEM, errno, ".");
                goto done;
            }
            cwd = strlen(w.pending);
        }
        if (cwd + 2 + strlen(root) > w.capacity) {
            way = fail(&w, FSB_WAY_SYSTEM, ENAMETOOLONG, root);
            goto done;
        }
        snprintf(w.pending + cwd, w.capacity - cwd, "/%s", root);
        way = walk(&w, root);
    }
done:
    if (w.fd >= 0) close(w.fd);
    free(w.pending);
    free(w.walked);
    free(w.target);
    return way;
}
