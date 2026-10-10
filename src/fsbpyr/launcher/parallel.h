/* The launcher's one routine for parallel work: hashing the integrity digest (D31, D32),
 * decoding the pieces of a large library (D33, D39) and writing files to the cache (D39).
 */
#ifndef FSBPYR_PARALLEL_H
#define FSBPYR_PARALLEL_H

#include <stdint.h>

#define FSB_PARALLEL_MAX_THREADS 8u /* owner decision: at most 8, never more than the cores */

/* Run work(context, item, worker) for every item in [0, count) and return when all are done:
 * this thread (worker 0) and up to 7 helpers (workers 1 to 7), never more than the usable cores
 * nor one per ``per_thread`` items, each taking the next item in turn. Fewer helpers when one
 * cannot start. ``worker`` is the thread's own number, for state of its own (a buffer). A call
 * from work that fsb_parallel runs runs on its thread alone (D39). */
void fsb_parallel(uint64_t count, uint64_t per_thread,
                  void (*work)(void *context, uint64_t item, unsigned worker), void *context);

/* CLOCK_MONOTONIC in nanoseconds. */
unsigned long long fsb_monotonic_ns(void);

#endif /* FSBPYR_PARALLEL_H */
