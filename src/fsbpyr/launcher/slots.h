/* The app's instance limit (D45): every start of an executable holds one of its slots for as long
 * as it runs, an OFD lock on one byte of a per-user file, and a start that finds every slot taken
 * is refused. An app that starts itself again and again, however it does (sys.executable -m or
 * -c, a shell, a wrapper, a script), so stops at the limit, as deep or as wide as its tree grows.
 * The kernel frees a slot when its holder ends, by SIGKILL too, and a forked child shares its
 * parent's slot (it never runs main() again). The guard never refuses for its own sake: no file it
 * can trust, or no OFD locks, leaves the start unguarded.
 */
#ifndef FSBPYR_SLOTS_H
#define FSBPYR_SLOTS_H

#include <stddef.h>
#include <stdint.h>

/* $FSBPYR_MAX_INSTANCES when it is decimal digits only (at most 7; clamped to 4096; 0 turns the
 * guard off), else 4 per CPU this process may run on and 16, at least 64 and at most 1024. */
unsigned fsb_instance_limit(void);

/* A slot of the executable (dev, ino) in the file fsbpyr-<euid> of the first of ``dirs[0,
 * count)`` that holds one we can trust (a regular file of ours that nobody else may use; NULL
 * and relative entries are skipped), held until the process ends. When every slot is taken,
 * prints why and exits 70, or 0 in a background collection (FSBPYR_GC=background), which can
 * wait for another day. */
void fsb_hold_slot(uint64_t dev, uint64_t ino, const char *const dirs[], size_t count);

#endif /* FSBPYR_SLOTS_H */
