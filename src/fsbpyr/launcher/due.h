/* When the app's cache is due for a collection (D38), started from the launcher before Python:
 * a day since the last run (the mtime of <root>/.gc), or less than 10% free on the root's file
 * system and an hour since. A run due is claimed (the stamp touched under its lock, so that one
 * start of many begins it, and none while a run holds it) and started in the background: a
 * detached process at idle priority, in "/", with no descriptor of the app's, that executes the
 * app again with FSBPYR_GC=background and FSBPYR_CACHE_DIR set to the root (the boot code then
 * collects instead of running the app). Costs a start one stat, and a statvfs when the last run
 * was over an hour ago.
 */
#ifndef FSBPYR_DUE_H
#define FSBPYR_DUE_H

#include <stddef.h>

/* The app is ``app[0, length)``; argv0 is what the app was started as. Nothing if FSBPYR_GC is
 * set (0: never; 1 or background: this process collects), if the root is not known (no
 * HOME), or if it holds no cache of ours (no marker). */
void fsb_collect_when_due(const char *app, size_t length, const char *argv0);

#endif /* FSBPYR_DUE_H */
