/* The cache collection's trigger (launcher/due.c), for tests/e2e/test_due.py (D38):
 *
 *   due APP     fsb_collect_when_due for the app APP, with a descriptor open that no exec
 *               closes (O_CLOEXEC unset); prints its number
 *
 * The process it starts to collect (FSBPYR_GC=background) runs this program again, which then
 * waits for the file $PROBE_RELEASE to exist (an app that keeps a descriptor of its starter would
 * make its starter wait as long; a cap of 30 s leaves no orphan if the file never comes) and
 * writes to $PROBE_OUT, a line each: the descriptors open, where 0, 1 and 2 lead, its
 * nice value, its I/O priority class, whether it shares its starter's session ($PROBE_SESSION),
 * its working directory, $FSBPYR_CACHE_DIR, and whether a signing variable is in its environment
 * as the kernel shows it. Built with -Wl,--wrap=statvfs: with $PROBE_FREE
 * set, the file system has that many percent free. And with --wrap=getpwuid_r,--wrap=opendir,
 * --wrap=syscall: $PROBE_PASSWD_HOME is the home the user's passwd entry names ("-": the user has
 * none); before the collection runs (FSBPYR_GC unset; its report reads /proc), $PROBE_NO_CLOSE_RANGE
 * makes close_range fail as on a kernel before 5.9 (ENOSYS), and $PROBE_NO_PROC /proc/self/fd
 * unreadable, as without /proc.
 */
#include "due.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pwd.h>
#include <stdarg.h>
#include <sys/resource.h>
#include <sys/statvfs.h>
#include <sys/syscall.h>
#include <unistd.h>

int __real_statvfs(const char *path, struct statvfs *fs);
int __real_getpwuid_r(uid_t uid, struct passwd *entry, char *buffer, size_t size,
                      struct passwd **found);
DIR *__real_opendir(const char *name);
long __real_syscall(long number, ...);

int __wrap_getpwuid_r(uid_t uid, struct passwd *entry, char *buffer, size_t size,
                      struct passwd **found) {
    const char *home = getenv("PROBE_PASSWD_HOME");
    int status = __real_getpwuid_r(uid, entry, buffer, size, found);
    if (home != NULL && strcmp(home, "-") == 0) {
        *found = NULL;
        return 0;
    }
    if (home != NULL && status == 0 && *found != NULL) entry->pw_dir = (char *)home;
    return status;
}

/* Only the launcher's, before it runs the collection: not the report's. Set and not empty. */
static int failing(const char *variable) {
    const char *set = getenv(variable);
    return set != NULL && set[0] != '\0' && getenv("FSBPYR_GC") == NULL;
}

DIR *__wrap_opendir(const char *name) {
    if (failing("PROBE_NO_PROC") && strncmp(name, "/proc/", 6) == 0) {
        errno = ENOENT;
        return NULL;
    }
    return __real_opendir(name);
}

/* syscall's arguments as glibc's own syscall reads them: six longs, whatever was passed. */
long __wrap_syscall(long number, ...) {
    va_list args;
    long arg[6];
    va_start(args, number);
    for (int i = 0; i < 6; i++) arg[i] = va_arg(args, long);
    va_end(args);
#ifdef SYS_close_range
    if (number == SYS_close_range && failing("PROBE_NO_CLOSE_RANGE")) {
        errno = ENOSYS;
        return -1;
    }
#endif
    return __real_syscall(number, arg[0], arg[1], arg[2], arg[3], arg[4], arg[5]);
}

int __wrap_statvfs(const char *path, struct statvfs *fs) {
    int status = __real_statvfs(path, fs);
    const char *share = getenv("PROBE_FREE");
    if (status == 0 && share != NULL) {
        fs->f_blocks = 1000;
        fs->f_bavail = (fsblkcnt_t)(10 * atoi(share));
    }
    return status;
}

/* As /proc/self/environ has it: what a process of the same user can read, not getenv's view. */
static int holds_signing_variable(void) {
    static char environment[1 << 16];
    int fd = open("/proc/self/environ", O_RDONLY);
    ssize_t size = fd < 0 ? -1 : read(fd, environment, sizeof environment - 1);
    if (fd >= 0) close(fd);
    return size < 0 || memmem(environment, (size_t)size, "FSBPYR_SIGNING_", 15) != NULL;
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
    const char *release = getenv("PROBE_RELEASE");
    for (int polls = 0; release != NULL && access(release, F_OK) != 0 && polls < 600; polls++)
        usleep(50000);
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
    fprintf(out, "signing %d\n", holds_signing_variable());
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
