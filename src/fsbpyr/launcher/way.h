/* The cache root's way from "/" (D38): no other user may be able to replace or redirect any part
 * of it, since what the cache holds is loaded as code. Every directory on the way, the root's
 * own included, must be owned by root or by us (in a user namespace, also by an owner outside
 * it, which it shows as the overflow uid, nobody: the sandbox's own layout) and writable by no
 * other user unless sticky (/tmp): not by others, nor by a group other than our primary one (a
 * user-private group is the norm; a shared one is not ours alone). Symlinks, which must be
 * root's or ours, are followed one at a time, their targets' ways checked as well; ".." goes up
 * from where the walk is, as the kernel resolves it. Below a directory others may add entries
 * to (sticky, as /tmp), only root's and ours count: an owner outside the namespace could be
 * anyone there. The walk ends at the root, or at the first part that does not exist yet: the
 * caller makes the rest, or finds it made, and checks again (a ".." after a missing part is
 * refused, as the kernel would: it could lead out of what was checked). _fsbpyr_rt/gc.py's
 * way_problem is the same rule in Python.
 */
#ifndef FSBPYR_WAY_H
#define FSBPYR_WAY_H

#include <stddef.h>

enum fsb_way {
    FSB_WAY_TRUSTED, /* the whole way, the root included */
    FSB_WAY_PARTIAL, /* the way up to a part that does not exist: check again once it does */
    FSB_WAY_FOREIGN, /* the directory or symlink at ``where`` belongs to another user */
    FSB_WAY_EXPOSED, /* the directory at ``where`` is writable by other users */
    FSB_WAY_SYSTEM,  /* errno ``*error`` at ``where`` */
    FSB_WAY_MEMORY,
};

/* Check the way to ``root`` (a relative root is taken from the working directory); ``where``
 * (``size`` bytes, cut short if longer) gets the path at fault. Thread-safe. */
enum fsb_way fsb_way_check(const char *root, char *where, size_t size, int *error);

#endif /* FSBPYR_WAY_H */
