/* The cache root's way from "/" (launcher/way.c), for tests/e2e/test_way_targets.py (D38):
 *
 *   way ROOT    fsb_way_check of ROOT; prints its verdict, the errno and the path at fault:
 *               "TRUSTED 0 -" ("-": none), or "SYSTEM 36 /x/long", say
 *
 * Built with -Wl,--wrap=readlinkat: with $PROBE_LINK and $PROBE_LENGTH set, the target of the
 * symlink of that name, an absolute path, is given with slashes in front of it (the same path)
 * to that many bytes, or to all the bytes the walk asked for ("full"): a target longer than a
 * local file system makes, as a FUSE or NFS server may give one.
 */
#include "way.h"

#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

ssize_t __real_readlinkat(int fd, const char *path, char *buffer, size_t size);

ssize_t __wrap_readlinkat(int fd, const char *path, char *buffer, size_t size) {
    const char *name = getenv("PROBE_LINK"), *length = getenv("PROBE_LENGTH");
    ssize_t n = __real_readlinkat(fd, path, buffer, size);
    if (n <= 0 || name == NULL || length == NULL || strcmp(path, name) != 0) return n;
    size_t want = strcmp(length, "full") == 0 ? size : strtoul(length, NULL, 10);
    if (want > size || want < (size_t)n) { /* the test asked for what cannot be */
        errno = EINVAL;
        return -1;
    }
    memmove(buffer + (want - (size_t)n), buffer, (size_t)n);
    memset(buffer, '/', want - (size_t)n);
    return (ssize_t)want;
}

int main(int argc, char **argv) {
    static const char *const verdicts[] = {"TRUSTED", "PARTIAL", "FOREIGN", "EXPOSED", "SYSTEM",
                                           "MEMORY"};
    if (argc != 2) return 2;
    char where[PATH_MAX] = "-";
    int error = 0;
    enum fsb_way way = fsb_way_check(argv[1], where, sizeof where, &error);
    printf("%s %d %s\n", verdicts[way], error, where);
    return 0;
}
