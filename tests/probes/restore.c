/* The cache sync's writers (launcher/cache.c, D39), for tests/e2e/test_restore.py:
 *
 *   restore IMAGE ROOT   fsb_cache_sync of every file of the image's payload, as resources,
 *                        below ROOT/runtime/g/site; prints a line per file put in place: its
 *                        name, the thread that put it there ("caller" or "helper") and how
 *                        ("link": an unnamed file linked; "rename": from the scratch directory);
 *                        then
 *                        "threads N" (the most threads running at once, this one included),
 *                        "written N" and "failure F PATH ERRNO" (the job's; PATH "-" if none)
 *
 * With $PROBE_FREE set, the descriptor limit leaves that many descriptors to open; with
 * $PROBE_NO_LINK set, no link can be made (as on a kernel before 6.10 without /proc), and a line
 * "links N" says how many were tried; with $PROBE_DECOY set, a link by descriptor fails and one
 * through /proc links that file instead (a /proc that is not procfs). Built with
 * -Wl,--wrap=renameat,--wrap=linkat,--wrap=pthread_create.
 */
#include "cache.h"

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>

int __real_renameat(int from, const char *old, int to, const char *new);
int __real_linkat(int from, const char *old, int to, const char *new, int flags);
int __real_pthread_create(pthread_t *thread, const pthread_attr_t *attr,
                          void *(*routine)(void *), void *arg);

static pthread_t caller;
static atomic_uint running = 1, most = 1, links;
static pthread_mutex_t lines = PTHREAD_MUTEX_INITIALIZER;

static void placed(const char *name, const char *how) {
    pthread_mutex_lock(&lines);
    printf("%s %s %s\n", name, pthread_equal(pthread_self(), caller) ? "caller" : "helper", how);
    pthread_mutex_unlock(&lines);
}

int __wrap_renameat(int from, const char *old, int to, const char *new) {
    int status = __real_renameat(from, old, to, new);
    if (status == 0) placed(new, "rename");
    return status;
}

int __wrap_linkat(int from, const char *old, int to, const char *new, int flags) {
    atomic_fetch_add(&links, 1);
    if (getenv("PROBE_NO_LINK") != NULL || (getenv("PROBE_DECOY") != NULL && *old == '\0')) {
        errno = EPERM;
        return -1;
    }
    if (getenv("PROBE_DECOY") != NULL && strncmp(old, "/proc/", 6) == 0)
        old = getenv("PROBE_DECOY");
    int status = __real_linkat(from, old, to, new, flags);
    if (status == 0) placed(new, "link");
    return status;
}

struct start {
    void *(*routine)(void *);
    void *arg;
};

static void *counted(void *arg) {
    struct start start = *(struct start *)arg;
    free(arg);
    unsigned now = atomic_fetch_add(&running, 1) + 1, seen = atomic_load(&most);
    while (now > seen && !atomic_compare_exchange_weak(&most, &seen, now)) {}
    void *result = start.routine(start.arg);
    atomic_fetch_sub(&running, 1);
    return result;
}

int __wrap_pthread_create(pthread_t *thread, const pthread_attr_t *attr,
                          void *(*routine)(void *), void *arg) {
    struct start *start = malloc(sizeof *start);
    if (start == NULL) return EAGAIN;
    *start = (struct start){routine, arg};
    int status = __real_pthread_create(thread, attr, counted, start);
    if (status != 0) free(start);
    return status;
}

/* The descriptor limit that leaves ``free`` descriptors to open. */
static void leave_free(int free) {
    int limit = 0;
    for (; free > 0; limit++)
        if (fcntl(limit, F_GETFD) < 0) free--;
    struct rlimit now;
    if (getrlimit(RLIMIT_NOFILE, &now) == 0) {
        now.rlim_cur = (rlim_t)limit;
        setrlimit(RLIMIT_NOFILE, &now);
    }
}

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    FILE *file = fopen(argv[1], "rb");
    if (file == NULL || fseek(file, 0, SEEK_END) != 0) return 3;
    long size = ftell(file);
    uint8_t *image = malloc((size_t)size);
    if (image == NULL || fseek(file, 0, SEEK_SET) != 0 ||
        fread(image, 1, (size_t)size, file) != (size_t)size)
        return 3;
    fclose(file);
    static struct fsb_payload payload;
    const char *error = fsb_payload_parse(&payload, image, (uint64_t)size);
    if (error != NULL) {
        fprintf(stderr, "%s\n", error);
        return 1;
    }
    uint64_t count = fsb_count(&payload, FSB_SECTION_FILES);
    uint32_t *files = malloc(count * sizeof *files);
    uint8_t *outcome = calloc(count, 1);
    struct fsb_cache_sync *job = calloc(1, sizeof *job);
    if (files == NULL || outcome == NULL || job == NULL) return 3;
    for (uint32_t i = 0; i < count; i++) files[i] = i;
    *job = (struct fsb_cache_sync){.root = argv[2], .chain = "runtime/g/site", .files = files,
                                   .count = count, .resources = true, .outcome = outcome};
    if (getenv("PROBE_FREE") != NULL) leave_free(atoi(getenv("PROBE_FREE")));
    caller = pthread_self();
    fsb_cache_sync(&payload, job);
    unsigned written = 0;
    for (uint64_t i = 0; i < count; i++) written += (outcome[i] & FSB_CACHE_WRITTEN) != 0;
    struct fsb_file failed;
    const char *path = "-";
    if (job->failure == FSB_CACHE_CORRUPT && fsb_get_file(&payload, job->file, &failed))
        fsb_str(&payload, failed.path, &path);
    printf("threads %u\nwritten %u\nfailure %d %s %d\n", atomic_load(&most), written,
           (int)job->failure, path, job->error);
    if (getenv("PROBE_NO_LINK") != NULL) printf("links %u\n", atomic_load(&links));
    free(job);
    free(outcome);
    free(files);
    free(image);
    return 0;
}
