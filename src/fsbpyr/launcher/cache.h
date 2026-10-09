/* The native cache (plan §3.5; D9, D31, D34, D35): payload files extracted to disk, and the check
 * each process makes of the extracted files it uses, before it first uses them.
 *
 * Layout: <root>/<chain>/<site-relative path>, such as <root>/runtime/<generation>/site/x.libs/
 * libx.so. The root must be ours; every directory below it is created private (0700), never
 * followed if it is a symlink, and must be ours. A cached file is used if it is a regular file
 * of ours with the payload's size and digest; anything else is reported and replaced (a name too
 * long for the file system fails the sync: no file can be there). A file is written under a
 * temporary name and renamed into place, so racing processes only ever see whole files and need
 * no lock. Nothing is synced to disk (D34): a file a crash left incomplete differs from its
 * digest, and the next process that uses it replaces it. Files are written in <the chain's
 * parent>/.scratch, locked while written, and renamed from there into place: a collector
 * removes what a killed process left there (D38).
 *
 * Collection (D38, _fsbpyr_rt/gc.py): the chain's parent ("runtime/<generation>") is a tree a
 * collector may remove once no process uses it. A sync asked to (``hold``) takes the tree for
 * the process's life once the records are checked: the root gets its marker (only a root that
 * holds nothing but our names: one of the user's is never collected), and the tree's .lock is
 * held LOCK_SH; the caller keeps the descriptor open.
 */
#ifndef FSBPYR_CACHE_H
#define FSBPYR_CACHE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "payload.h"

/* What became of one file (bits of fsb_cache_sync.outcome). */
enum {
    FSB_CACHE_CHANGED = 1, /* it was on disk, but not as the payload has it: report it */
    FSB_CACHE_DONE = 2,    /* the file on disk is the payload's now: checked, or written */
    FSB_CACHE_WRITTEN = 4, /* written from the payload */
};

enum fsb_cache_failure {
    FSB_CACHE_OK,
    FSB_CACHE_CORRUPT, /* a record or blob of the payload: ``reason`` of FILES index ``file`` */
    FSB_CACHE_FOREIGN, /* the directory ``path`` belongs to another user */
    FSB_CACHE_SYSTEM,  /* errno ``error`` at ``path`` */
    FSB_CACHE_MEMORY,
};

struct fsb_cache_sync {
    const char *root;      /* created if missing; followed if a symlink; must be ours */
    const char *chain;     /* the directories from the root down to the site: "a/b/c" */
    const uint32_t *files; /* FILES indices; in ascending order, directories are opened once */
    uint64_t count;
    bool resources;   /* any file may be extracted, and one without a digest is compared with
                         its content in the payload (importlib.resources asked for it on disk) */
    uint8_t *outcome; /* ``count`` FSB_CACHE_* bit sets, zeroed by the caller */
    bool hold;        /* take the tree (see above) once the records are checked */
    int lock;         /* ``hold``: the tree's lock, the caller's to keep; -1 if it cannot be held
                         (a read-only cache, no locks: the tree is used unheld) */
    enum fsb_cache_failure failure;
    const char *reason; /* CORRUPT */
    uint32_t file;      /* CORRUPT */
    int error;          /* SYSTEM */
    char path[4096];    /* FOREIGN and SYSTEM: the path (cut short if longer) */
};

/* Extract the files that are missing and replace the ones that differ from the payload; stops at
 * the first failure. Every record of the job is checked before anything on disk is touched
 * (module.c gives jobs of up to 4096 files, D38). Thread-safe and independent of Python: callers
 * release the GIL. */
void fsb_cache_sync(const struct fsb_payload *payload, struct fsb_cache_sync *job);

/* The payload reader's rule for paths (check_path): no empty, "." or ".." component, no NUL and
 * no backslash. A path that passes cannot leave the directory it is opened from. */
bool fsb_cache_safe(const char *path, size_t length);

#endif /* FSBPYR_CACHE_H */
