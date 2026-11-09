/* The threads of fsb_parallel (launcher/parallel.c), for tests/e2e/test_cores.py:
 *
 *   cores KERNEL ALLOWED   runs fsb_parallel over 64 items, one per thread at most, and prints
 *                          the number of threads it ran them on
 *
 * Built with -Wl,--wrap=sched_getaffinity,--wrap=pthread_create: the kernel has KERNEL CPUs and
 * refuses a mask of fewer bits with EINVAL, as Linux refuses one shorter than nr_cpu_ids, and
 * the process may run on the first ALLOWED of them; each thread started is counted.
 */
#include "parallel.h"

#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int __real_pthread_create(pthread_t *thread, const pthread_attr_t *attr,
                          void *(*routine)(void *), void *arg);

static size_t kernel, allowed;
static atomic_uint started;

int __wrap_sched_getaffinity(pid_t pid, size_t size, cpu_set_t *mask) {
    (void)pid;
    if (size * 8 < kernel) {
        errno = EINVAL;
        return -1;
    }
    memset(mask, 0, size);
    for (size_t cpu = 0; cpu < allowed; cpu++) CPU_SET_S(cpu, size, mask);
    return 0;
}

int __wrap_pthread_create(pthread_t *thread, const pthread_attr_t *attr,
                          void *(*routine)(void *), void *arg) {
    int status = __real_pthread_create(thread, attr, routine, arg);
    if (status == 0) atomic_fetch_add(&started, 1);
    return status;
}

static void nothing(void *context, uint64_t item, unsigned worker) {
    (void)context, (void)item, (void)worker;
}

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    kernel = strtoul(argv[1], NULL, 10);
    allowed = strtoul(argv[2], NULL, 10);
    fsb_parallel(64, 1, nothing, NULL);
    printf("%u\n", atomic_load(&started) + 1);
    return 0;
}
