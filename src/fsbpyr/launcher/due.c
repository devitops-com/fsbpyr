/* The cache collection's trigger (see due.h). */
#include "due.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/auxv.h>
#include <sys/file.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define HOUR 3600
#define DAY 86400
#define FREE_PART 10 /* less than 1/FREE_PART free: the file system is short of space (D38) */
#define IOPRIO_CLASS_IDLE 3
#define IOPRIO_WHO_PROCESS 1
#define IOPRIO_CLASS_SHIFT 13
/* Read and write: on NFS an exclusive lock needs a file open for writing. */
#define STAMP_FLAGS (O_RDWR | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK | O_NOCTTY)

/* Static: run once, before any thread, and frames stay small. */
static char root[PATH_MAX], stamp[PATH_MAX], marker[PATH_MAX], cwd[PATH_MAX];

/* ``path`` (absolute) without empty, "." and ".." components, as os.path.abspath leaves it. */
static void normalize(char *path) {
    size_t out = 0;
    for (const char *at = path; *at != '\0';) {
        const char *end = strchr(at, '/');
        size_t length = end != NULL ? (size_t)(end - at) : strlen(at);
        if (length == 2 && at[0] == '.' && at[1] == '.') {
            while (out > 0 && path[--out] != '/') {}
        } else if (length > 0 && !(length == 1 && at[0] == '.')) {
            path[out++] = '/';
            memmove(path + out, at, length);
            out += length;
        }
        at += length + (end != NULL);
    }
    if (out == 0) path[out++] = '/';
    path[out] = '\0';
}

/* The app's cache root as the boot code finds it (native.cache_directory): $FSBPYR_CACHE_DIR
 * (made absolute), else ${XDG_CACHE_HOME:-$HOME/.cache}/<app>, the app's name checked as there.
 * False if none can be told. */
static bool cache_root(const char *app, size_t length) {
    const char *explicit = getenv("FSBPYR_CACHE_DIR"), *base = getenv("XDG_CACHE_HOME");
    if (length == 0 || memchr(app, '/', length) != NULL || memchr(app, '\0', length) != NULL ||
        (length <= 2 && strncmp(app, "..", length) == 0))
        return false; /* "", ".", "..": the boot code refuses them */
    int n;
    if (explicit != NULL && explicit[0] != '\0') {
        if (explicit[0] == '/') {
            n = snprintf(root, sizeof root, "%s", explicit);
        } else if (getcwd(cwd, sizeof cwd) != NULL) {
            n = snprintf(root, sizeof root, "%s/%s", cwd, explicit);
        } else {
            return false;
        }
        if (n > 0 && (size_t)n < sizeof root) normalize(root);
    } else if (base != NULL && base[0] == '/') {
        n = snprintf(root, sizeof root, "%s/%.*s", base, (int)length, app);
    } else if ((base = getenv("HOME")) != NULL && base[0] == '/') {
        n = snprintf(root, sizeof root, "%s/.cache/%.*s", base, (int)length, app);
    } else {
        return false;
    }
    return n > 0 && (size_t)n < sizeof root &&
           snprintf(stamp, sizeof stamp, "%s/.gc", root) < (int)sizeof stamp &&
           snprintf(marker, sizeof marker, "%s/.fsbpyr-cache", root) < (int)sizeof marker;
}

/* Whether ``path`` is a regular file of ours. */
static bool ours(const char *path, struct stat *st) {
    return lstat(path, st) == 0 && S_ISREG(st->st_mode) && st->st_uid == geteuid();
}

/* Whether a run is due, in a root that is a cache of ours (it has the marker): no stamp
 * (``*missing``), or the stamp (its mtime: the last run's start, ``*seen``) a day old, or an hour
 * old with the file system short of space, or dated ahead (the clock was set back). */
static bool due(struct stat *seen, bool *missing) {
    struct stat st;
    struct statvfs fs;
    if (lstat(stamp, seen) != 0) {
        *missing = errno == ENOENT;
        return *missing && ours(marker, &st);
    }
    time_t now = time(NULL); /* no subtraction: a stamp's mtime may be anything */
    bool ahead = seen->st_mtime > now, day = seen->st_mtime <= now - DAY;
    if (!ahead && !day &&
        (seen->st_mtime > now - HOUR || statvfs(root, &fs) != 0 ||
         fs.f_bavail >= fs.f_blocks / FREE_PART))
        return false;
    return ours(marker, &st);
}

/* The stamp claimed for a run: held LOCK_EX, still as due() saw it (no other start claimed it
 * meanwhile, and no run holds it), and touched (made, if it was missing). False otherwise, or
 * if it cannot be touched (a read-only cache): no run is started. */
static bool claim(const struct stat *seen, bool missing) {
    int fd = open(stamp, STAMP_FLAGS | (missing ? O_CREAT | O_EXCL : 0), 0600);
    if (fd < 0) return false;
    struct stat st;
    bool claimed = flock(fd, LOCK_EX | LOCK_NB) == 0 && fstat(fd, &st) == 0 &&
                   S_ISREG(st.st_mode) && st.st_uid == geteuid() &&
                   (missing || (st.st_mtim.tv_sec == seen->st_mtim.tv_sec &&
                                st.st_mtim.tv_nsec == seen->st_mtim.tv_nsec)) &&
                   futimens(fd, NULL) == 0;
    close(fd);
    return claimed;
}

/* 0-2 on /dev/null and every other descriptor closed (close_range, else those /proc/self/fd
 * lists, else one at a time up to the limit); false if /dev/null cannot be opened. */
static bool detach_descriptors(void) {
    int null = open("/dev/null", O_RDWR);
    if (null < 0) return false;
    for (int fd = 0; fd < 3; fd++)
        if (fd != null && dup2(null, fd) < 0) return false;
    if (null > 2) close(null);
#ifdef SYS_close_range
    if (syscall(SYS_close_range, 3u, ~0u, 0u) == 0) return true;
#endif
    DIR *open_fds = opendir("/proc/self/fd"); /* one thread: malloc is safe after fork */
    if (open_fds != NULL) {
        for (struct dirent *d; (d = readdir(open_fds)) != NULL;) {
            int fd = atoi(d->d_name);
            if (fd > 2 && fd != dirfd(open_fds)) close(fd);
        }
        closedir(open_fds);
        return true;
    }
    struct rlimit limit;
    long last = getrlimit(RLIMIT_NOFILE, &limit) == 0 && limit.rlim_cur != RLIM_INFINITY
                    ? (long)limit.rlim_cur
                    : 65536;
    for (long fd = 3; fd < last; fd++) close((int)fd);
    return true;
}

/* Fork twice (the app reaps the first child at once; the second, detached, is init's), then
 * execute the app again at idle priority to collect the root, from "/" (no mount kept busy). */
static void start_collection(const char *argv0) {
    pid_t child = fork();
    if (child < 0) return;
    if (child > 0) {
        while (waitpid(child, NULL, 0) < 0 && errno == EINTR) {}
        return;
    }
    if (setsid() < 0 || fork() != 0) _exit(0);
    setpriority(PRIO_PROCESS, 0, 19);
    syscall(SYS_ioprio_set, IOPRIO_WHO_PROCESS, 0, IOPRIO_CLASS_IDLE << IOPRIO_CLASS_SHIFT);
    if (detach_descriptors() && chdir("/") == 0 && setenv("FSBPYR_CACHE_DIR", root, 1) == 0 &&
        setenv("FSBPYR_GC", "background", 1) == 0) {
        char *const argv[] = {(char *)argv0, NULL};
        execv("/proc/self/exe", argv);
    }
    _exit(0);
}

void fsb_collect_when_due(const char *app, size_t length, const char *argv0) {
    struct stat seen;
    bool missing = false;
    /* Not for a set-uid start (its environment is not to be trusted), nor as process 1 (in a
     * container: the process started would be its child, and no zombie of ours is reaped). */
    if (getauxval(AT_SECURE) || getpid() == 1 || getenv("FSBPYR_GC") != NULL ||
        !cache_root(app, length) || !due(&seen, &missing) || !claim(&seen, missing))
        return;
    start_collection(argv0);
}
