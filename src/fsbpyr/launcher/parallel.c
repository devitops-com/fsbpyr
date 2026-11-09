/* Parallel work (see parallel.h). */
#include "parallel.h"

#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <time.h>

#define MAX_THREADS FSB_PARALLEL_MAX_THREADS
#define MOST_CPUS (1u << 20)
#define SPINS_PER_YIELD 1024u
#define SPIN_NS 2000000ull /* then sleep in pthread_join: a helper that cannot run (stopped by a
                            * debugger, stuck in a page fault) must not make the caller burn a core */

struct job {
    uint64_t count;
    atomic_uint_fast64_t next;
    void (*work)(void *context, uint64_t item, unsigned worker);
    void *context;
};

/* Set on a thread while it runs fsb_parallel's work: a call made from that work runs on its
 * thread alone, so that work nested in work (hashing what a cache writer writes, D39) never
 * makes more than MAX_THREADS threads. */
static _Thread_local bool working;

struct worker { /* one thread's view of the job */
    struct job *job;
    unsigned number;
};

static inline void spin_pause(void) {
#if defined(__x86_64__) || defined(__i386__)
    __builtin_ia32_pause();
#endif
}

unsigned long long fsb_monotonic_ns(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (unsigned long long)now.tv_sec * 1000000000ull + (unsigned long long)now.tv_nsec;
}

/* The CPUs this process may run on, asked with a mask of ``cpus`` bits; -1 (errno) if refused. */
static int cores_in(size_t cpus) {
    cpu_set_t *mask = CPU_ALLOC(cpus);
    if (mask == NULL) return -1;
    size_t size = CPU_ALLOC_SIZE(cpus);
    int count = sched_getaffinity(0, size, mask) == 0 ? CPU_COUNT_S(size, mask) : -1;
    int error = errno;
    CPU_FREE(mask);
    errno = error;
    return count;
}

/* A kernel with more CPUs than a cpu_set_t holds (1024) refuses its mask (EINVAL): then ask
 * again with one twice as large, up to MOST_CPUS, a bound far above any kernel's. */
static unsigned usable_cores(void) {
    cpu_set_t set;
    int count = sched_getaffinity(0, sizeof set, &set) == 0 ? CPU_COUNT(&set) : -1;
    for (size_t cpus = 2 * CPU_SETSIZE; count < 0 && errno == EINVAL && cpus <= MOST_CPUS;
         cpus *= 2)
        count = cores_in(cpus);
    return count > 0 ? (unsigned)count : 1;
}

static void *take_items(void *arg) {
    const struct worker *worker = arg;
    struct job *job = worker->job;
    working = true;
    for (uint64_t item; (item = atomic_fetch_add(&job->next, 1)) < job->count;)
        job->work(job->context, item, worker->number);
    return NULL;
}

/* After its own share this thread spins on non-blocking joins for up to SPIN_NS before it
 * sleeps in one: on a hybrid CPU a thread woken from a join may land on an efficiency core and
 * stay there while every core is busy (numpy, whose OpenBLAS starts one spinning thread per
 * core right after its libraries are checked: +38 ms with joins, +4 to 7 ms spinning; Core
 * Ultra 9 285). */
void fsb_parallel(uint64_t count, uint64_t per_thread,
                  void (*work)(void *context, uint64_t item, unsigned worker), void *context) {
    struct job job = {.count = count, .work = work, .context = context};
    atomic_init(&job.next, 0);
    uint64_t wanted = per_thread ? (count + per_thread - 1) / per_thread : count;
    unsigned threads = working ? 1 : usable_cores();
    if (threads > MAX_THREADS) threads = MAX_THREADS;
    if (threads > wanted) threads = (unsigned)wanted;
    pthread_t helpers[MAX_THREADS - 1];
    struct worker workers[MAX_THREADS];
    for (unsigned i = 0; i < MAX_THREADS; i++) workers[i] = (struct worker){&job, i};
    unsigned started = 0;
    while (started + 1 < threads &&
           pthread_create(&helpers[started], NULL, take_items, &workers[started + 1]) == 0)
        started++; /* fewer helpers if one cannot start: this thread does what is left */
    bool nested = working;
    take_items(&workers[0]); /* when it returns, every item is taken: what is left is in flight */
    working = nested;
    unsigned joined = 0;
    unsigned long long deadline = fsb_monotonic_ns() + SPIN_NS;
    for (unsigned spins = 1; joined < started; spins++) {
        if (pthread_tryjoin_np(helpers[joined], NULL) == 0)
            joined++;
        else if (spins % SPINS_PER_YIELD != 0)
            spin_pause();
        else if (fsb_monotonic_ns() < deadline)
            sched_yield(); /* a helper waiting for this core gets it; this thread stays put */
        else
            break;
    }
    for (; joined < started; joined++) pthread_join(helpers[joined], NULL);
}
