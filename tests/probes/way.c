/* The cache root's way from "/" (launcher/way.c), for tests/e2e/test_way_targets.py and
 * tests/e2e/test_way.py (D38):
 *
 *   way ROOT    fsb_way_check of ROOT; prints its verdict, the errno and the path at fault:
 *               "TRUSTED 0 -" ("-": none), or "SYSTEM 36 /x/long", say; with $PROBE_FDS set,
 *               then " fds N", the descriptors open after the check less those before, and with
 *               $PROBE_CLOSE_STDIN set, descriptor 0 is closed first, so the walk may open it
 *
 * Built with -Wl,--wrap=readlinkat: with $PROBE_LINK and $PROBE_LENGTH set, the target of the
 * symlink of that name, an absolute path, is given with slashes in front of it (the same path)
 * to that many bytes, or to all the bytes the walk asked for ("full"), or as nothing ("0"): a
 * target longer than a local file system makes, as a FUSE or NFS server may give one; with
 * $PROBE_LINK and $PROBE_LINK_ERRNO, reading it fails with that errno.
 * With --wrap=fopen too: $PROBE_OVERFLOWUID and $PROBE_UID_MAP name the files read instead of
 * /proc/sys/kernel/overflowuid and /proc/self/uid_map ("-": it cannot be opened).
 * With --wrap=fstat,--wrap=fstatat: $PROBE_OWNED (a real path) is owned by $PROBE_OWNER (a uid),
 * one no test could make. With --wrap=malloc: $PROBE_FAIL_MALLOC=N fails the walk's Nth malloc.
 */
#include "way.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

ssize_t __real_readlinkat(int fd, const char *path, char *buffer, size_t size);
FILE *__real_fopen(const char *path, const char *mode);
int __real_fstat(int fd, struct stat *st);
int __real_fstatat(int fd, const char *path, struct stat *st, int flags);
void *__real_malloc(size_t size);

ssize_t __wrap_readlinkat(int fd, const char *path, char *buffer, size_t size) {
    const char *name = getenv("PROBE_LINK"), *length = getenv("PROBE_LENGTH");
    const char *error = getenv("PROBE_LINK_ERRNO");
    if (name != NULL && error != NULL && strcmp(path, name) == 0) {
        errno = atoi(error);
        return -1;
    }
    ssize_t n = __real_readlinkat(fd, path, buffer, size);
    if (n <= 0 || name == NULL || length == NULL || strcmp(path, name) != 0) return n;
    if (strcmp(length, "0") == 0) return 0;
    size_t want = strcmp(length, "full") == 0 ? size : strtoul(length, NULL, 10);
    if (want > size || want < (size_t)n) { /* the test asked for what cannot be */
        errno = EINVAL;
        return -1;
    }
    memmove(buffer + (want - (size_t)n), buffer, (size_t)n);
    memset(buffer, '/', want - (size_t)n);
    return (ssize_t)want;
}

FILE *__wrap_fopen(const char *path, const char *mode) {
    const char *instead = NULL;
    if (strcmp(path, "/proc/sys/kernel/overflowuid") == 0) instead = getenv("PROBE_OVERFLOWUID");
    if (strcmp(path, "/proc/self/uid_map") == 0) instead = getenv("PROBE_UID_MAP");
    if (instead != NULL && strcmp(instead, "-") == 0) {
        errno = ENOENT;
        return NULL;
    }
    return __real_fopen(instead != NULL ? instead : path, mode);
}

/* The owner $PROBE_OWNER for $PROBE_OWNED: ``path`` is where ``st`` was taken. */
static void owned(const char *path, struct stat *st) {
    const char *which = getenv("PROBE_OWNED"), *owner = getenv("PROBE_OWNER");
    if (which != NULL && owner != NULL && strcmp(path, which) == 0)
        st->st_uid = (uid_t)strtoul(owner, NULL, 10);
}

/* The path of the directory ``fd`` (O_PATH too) into ``path``; "" if it cannot be told. */
static void path_of(int fd, char *path, size_t size) {
    char link[64];
    snprintf(link, sizeof link, "/proc/self/fd/%d", fd);
    ssize_t n = readlink(link, path, size - 1);
    path[n < 0 ? 0 : n] = '\0';
}

int __wrap_fstat(int fd, struct stat *st) {
    int status = __real_fstat(fd, st);
    if (status == 0) {
        char path[PATH_MAX];
        path_of(fd, path, sizeof path);
        owned(path, st);
    }
    return status;
}

int __wrap_fstatat(int fd, const char *name, struct stat *st, int flags) {
    int status = __real_fstatat(fd, name, st, flags);
    if (status == 0) {
        char path[PATH_MAX + NAME_MAX + 2];
        path_of(fd, path, PATH_MAX);
        size_t end = strlen(path);
        snprintf(path + end, sizeof path - end, "%s%s", end > 1 ? "/" : "", name);
        owned(path, st);
    }
    return status;
}

static int mallocs;

void *__wrap_malloc(size_t size) {
    const char *fail = getenv("PROBE_FAIL_MALLOC");
    if (fail != NULL && ++mallocs == atoi(fail)) {
        errno = ENOMEM;
        return NULL;
    }
    return __real_malloc(size);
}

static int open_fds(void) {
    int count = 0;
    DIR *listed = opendir("/proc/self/fd");
    if (listed == NULL) return -1;
    for (struct dirent *d; (d = readdir(listed)) != NULL;)
        count += d->d_name[0] != '.';
    closedir(listed);
    return count - 1; /* the listing's own */
}

int main(int argc, char **argv) {
    static const char *const verdicts[] = {"TRUSTED", "PARTIAL", "FOREIGN", "EXPOSED", "SYSTEM",
                                           "MEMORY"};
    if (argc != 2) return 2;
    if (getenv("PROBE_CLOSE_STDIN") != NULL) close(0);
    char where[PATH_MAX] = "-";
    int error = 0, before = open_fds();
    enum fsb_way way = fsb_way_check(argv[1], where, sizeof where, &error);
    int after = open_fds();
    printf("%s %d %s", verdicts[way], error, where);
    if (getenv("PROBE_FDS") != NULL) printf(" fds %d", after - before);
    printf("\n");
    return 0;
}
