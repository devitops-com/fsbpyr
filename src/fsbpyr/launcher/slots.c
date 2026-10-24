/* The app's instance limit (see slots.h). */
#include "slots.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define REFUSED 70           /* the launcher's refused start (D41) */
#define STRIDE 4096u         /* the slots an executable may have: its range of bytes in the file */
#define RANGES (1ull << 40)  /* ranges in the file: offsets stay below 2^52 */
#define LEAST 64             /* the default limit, 4 per CPU and 16, is at least LEAST */
#define MOST 1024            /* and at most MOST (D45) */
#define DIGITS 7             /* of $FSBPYR_MAX_INSTANCES, at most */
/* O_NONBLOCK: a FIFO in the file's place must not stop the start. */
#define SLOT_FLAGS (O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_NOCTTY | O_CLOEXEC)

unsigned fsb_instance_limit(void) {
    const char *set = getenv("FSBPYR_MAX_INSTANCES");
    size_t digits = set != NULL ? strspn(set, "0123456789") : 0;
    if (digits > 0 && digits <= DIGITS && set[digits] == '\0') {
        unsigned long limit = strtoul(set, NULL, 10);
        return limit < STRIDE ? (unsigned)limit : STRIDE;
    }
    cpu_set_t cpus;
    long count = sched_getaffinity(0, sizeof cpus, &cpus) == 0 ? CPU_COUNT(&cpus)
                                                              : sysconf(_SC_NPROCESSORS_ONLN);
    if (count < 1) count = 1;
    if (count > MOST) count = MOST; /* more CPUs than a cpu_set_t holds: sysconf's count */
    long limit = 4 * count + 16;
    return limit < LEAST ? LEAST : limit > MOST ? MOST : (unsigned)limit;
}

/* splitmix64's finalizer: the executable's range from its device and inode. */
static uint64_t mix(uint64_t x) {
    x += 0x9e3779b97f4a7c15u;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9u;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebu;
    return x ^ (x >> 31);
}

/* The slot file in ``dir``, if it is a regular file of ours that only we may use; else -1. In a
 * directory anyone may add to (/dev/shm), another user may have made the name first. Never one
 * of descriptors 0-2: a start with stdout closed would have Python write the app's output here. */
static int open_slots(const char *dir) {
    char path[PATH_MAX];
    if (dir == NULL || dir[0] != '/') return -1;
    int n = snprintf(path, sizeof path, "%s/fsbpyr-%lu", dir, (unsigned long)geteuid());
    if (n < 0 || (size_t)n >= sizeof path) return -1;
    int fd = open(path, SLOT_FLAGS, 0600);
    if (fd >= 0 && fd < 3) {
        int high = fcntl(fd, F_DUPFD_CLOEXEC, 3);
        close(fd);
        fd = high;
    }
    struct stat st;
    if (fd >= 0 && (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_uid != geteuid() ||
                    (st.st_mode & 077) != 0)) {
        close(fd);
        return -1;
    }
    return fd;
}

enum outcome { HELD, FULL, UNGUARDED };

/* One of the ``limit`` slots from ``base`` on, locked through ``fd``. Only EAGAIN and EACCES
 * say a slot is taken; any other error, that the file system has no OFD locks. */
static enum outcome take(int fd, uint64_t base, unsigned limit) {
    for (unsigned i = 0; i < limit; i++) {
        struct flock slot = {.l_type = F_WRLCK, .l_whence = SEEK_SET,
                             .l_start = (off_t)(base + i), .l_len = 1}; /* l_pid 0: OFD */
        int status;
        while ((status = fcntl(fd, F_OFD_SETLK, &slot)) != 0 && errno == EINTR) {}
        if (status == 0) return HELD;
        if (errno != EAGAIN && errno != EACCES) return UNGUARDED;
    }
    return FULL;
}

void fsb_hold_slot(uint64_t dev, uint64_t ino, const char *const dirs[], size_t count) {
    unsigned limit = fsb_instance_limit();
    int fd = -1;
    for (size_t i = 0; limit > 0 && fd < 0 && i < count; i++) fd = open_slots(dirs[i]);
    if (fd < 0) return; /* unguarded */
    enum outcome outcome = take(fd, mix(dev ^ mix(ino)) % RANGES * STRIDE, limit);
    if (outcome == HELD) return; /* fd stays open (O_CLOEXEC) while the process runs */
    close(fd);
    if (outcome == UNGUARDED) return;
    const char *collection = getenv("FSBPYR_GC");
    if (collection != NULL && strcmp(collection, "background") == 0) exit(0);
    fprintf(stderr,
            "fsbpyr: refusing to start: %u copies of this app are running already, as many as "
            "this machine allows at once (does the app keep starting itself?); if it needs more, "
            "set FSBPYR_MAX_INSTANCES to a higher number\n",
            limit);
    exit(REFUSED);
}
