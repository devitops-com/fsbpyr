/* The cache collection's trigger (launcher/due.c), for tests/e2e/test_due.py (D38):
 *
 *   due APP     fsb_collect_when_due for the app APP, with a descriptor open that no exec
 *               closes (O_CLOEXEC unset); prints its number
 *
 * The process it starts to collect (FSBPYR_GC=background) runs this program again, which then
 * waits $PROBE_SLEEP seconds (an app that keeps a descriptor of its starter would make it wait as
 * long) and writes to $PROBE_OUT, a line each: the descriptors open, where 0, 1 and 2 lead, its
 * nice value, its I/O priority class, whether it shares its starter's session ($PROBE_SESSION),
 * its working directory, and $FSBPYR_CACHE_DIR. Built with -Wl,--wrap=statvfs: with $PROBE_FREE
 * set, the file system has that many percent free.
 */
#include "due.h"

#include <dirent.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/statvfs.h>
#include <sys/syscall.h>
#include <unistd.h>

int __real_statvfs(const char *path, struct statvfs *fs);

int __wrap_statvfs(const char *path, struct statvfs *fs) {
    int status = __real_statvfs(path, fs);
    const char *share = getenv("PROBE_FREE");
    if (status == 0 && share != NULL) {
        fs->f_blocks = 1000;
        fs->f_bavail = (fsblkcnt_t)(10 * atoi(share));
    }
    return status;
}

static int report(void) {
    char open_fds[1024] = "", target[PATH_MAX], cwd[PATH_MAX];
    DIR *listed = opendir("/proc/self/fd");
    if (listed == NULL) return 1;
    for (struct dirent *d; (d = readdir(listed)) != NULL;) {
        if (d->d_name[0] == '.' || atoi(d->d_name) == dirfd(listed)) continue;
        strncat(open_fds, " ", sizeof open_fds - strlen(open_fds) - 1);
        strncat(open_fds, d->d_name, sizeof open_fds - strlen(open_fds) - 1);
    }
    closedir(listed);
    unsigned seconds = (unsigned)atoi(getenv("PROBE_SLEEP") ? getenv("PROBE_SLEEP") : "0");
    sleep(seconds);
    FILE *out = fopen(getenv("PROBE_OUT"), "a"); /* one report a process started */
    if (out == NULL) return 1;
    fprintf(out, "fds%s\n", open_fds);
    for (int fd = 0; fd < 3; fd++) {
        char link[32];
        snprintf(link, sizeof link, "/proc/self/fd/%d", fd);
        ssize_t n = readlink(link, target, sizeof target - 1);
        target[n < 0 ? 0 : n] = '\0';
        fprintf(out, "%d %s\n", fd, target);
    }
    fprintf(out, "nice %d\n", getpriority(PRIO_PROCESS, 0));
    fprintf(out, "ioclass %ld\n", syscall(SYS_ioprio_get, 1, 0) >> 13);
    fprintf(out, "own session %d\n", (long)getsid(0) != atol(getenv("PROBE_SESSION")));
    fprintf(out, "cwd %s\n", getcwd(cwd, sizeof cwd) ? cwd : "?");
    fprintf(out, "root %s\n", getenv("FSBPYR_CACHE_DIR"));
    return fclose(out) != 0;
}

int main(int argc, char **argv) {
    const char *mode = getenv("FSBPYR_GC");
    if (mode != NULL && strcmp(mode, "background") == 0) return report();
    if (argc != 2) return 2;
    char session[32];
    snprintf(session, sizeof session, "%ld", (long)getsid(0));
    setenv("PROBE_SESSION", session, 1);
    int kept = open("/dev/null", O_RDONLY); /* no O_CLOEXEC: an exec keeps it */
    fsb_collect_when_due(argv[1], strlen(argv[1]), argv[0]);
    printf("%d\n", kept);
    return 0;
}
