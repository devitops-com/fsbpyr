/* The launcher's one routine for parallel work: hashing the integrity digest (D31, D32) and
 * decoding the pieces of a large xz library (D33).
 */
#ifndef FSBPYR_PARALLEL_H
#define FSBPYR_PARALLEL_H

#include <stdint.h>

/* Run work(context, item) for every item in [0, count) and return when all are done: this
 * thread and up to 7 helpers, never more than the usable cores nor one per ``per_thread``
 * items, each taking the next item in turn. Fewer helpers when one cannot start. */
void fsb_parallel(uint64_t count, uint64_t per_thread, void (*work)(void *context, uint64_t item),
                  void *context);

/* CLOCK_MONOTONIC in nanoseconds. */
unsigned long long fsb_monotonic_ns(void);

#endif /* FSBPYR_PARALLEL_H */
