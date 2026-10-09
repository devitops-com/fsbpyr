/* _fsbpyr: built-in module giving the boot code bounds-checked access to the payload.
 *
 *   find_module(name)  -> (index, kind, flags, code, source, file, dist, path) | None
 *   find_file(path)    -> (index, kind, flags, mode, blob, dist, closure) | None
 *   file_at(index)     -> (index, kind, flags, mode, blob, dist, closure, path)
 *   closure(word)      -> FILES indices of the closure starting at CLOSURES word ``word``
 *   blob_info(blob)    -> (size, integrity digest of the uncompressed content | None)
 *   list_dir(path)     -> sorted names directly below a directory ('' is the root)
 *   is_dir(path)       -> whether any file lives below a directory (O(log n))
 *   list_modules(pre)  -> [(name, kind)] direct children of "pkg." ('' is the top level)
 *   find_dist(name)    -> (index, name, dist_info, version) | None;  dists() -> all of them
 *   read(blob)         -> bytes (decompressed, size-checked)
 *   stream(blob)       -> Stream: the content a window at a time (readinto, seek, tell, size)
 *   info()             -> dict describing the executable
 *   stats()            -> read counters and integrity check time (FSBPYR_TRACE, benchmarks)
 *   files_below(path)  -> range of the FILES indices of every file below a directory ('' is all)
 *   sync(root, chain, files, checked, resources[, written]) -> number of files written (cache.h,
 *                         D35); their site-relative paths are appended to ``written``
 */
#include "launcher.h"

#include "cache.h"
#include "integrity.h"

#ifdef Py_GIL_DISABLED /* read() shares the process's zstd decoder: the GIL serializes it */
#error "the _fsbpyr module needs a CPython with the GIL"
#endif

static PyObject *PayloadError, *CacheError;
static unsigned long long reads;

static PyObject *corrupt(const char *what, unsigned long long index) {
    PyErr_Format(PayloadError, "corrupt executable payload: %s %llu", what, index);
    return NULL;
}

/* 0: key ready; 1: key cannot exist in the payload (not UTF-8 encodable); -1: error set */
static int key_arg(PyObject *arg, const char **key, Py_ssize_t *length) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return -1;
    }
    *key = PyUnicode_AsUTF8AndSize(arg, length);
    if (*key != NULL) return 0;
    if (!PyErr_ExceptionMatches(PyExc_UnicodeEncodeError)) return -1;
    PyErr_Clear();
    return 1;
}

static PyObject *find_module(PyObject *self, PyObject *arg) {
    (void)self;
    const char *key;
    Py_ssize_t length;
    int status = key_arg(arg, &key, &length);
    if (status < 0) return NULL;
    if (status > 0) Py_RETURN_NONE;
    uint32_t index = fsb_find_module(&fsb_self, key, (size_t)length);
    if (index == FSB_NONE) Py_RETURN_NONE;
    struct fsb_module m;
    const char *path;
    if (!fsb_get_module(&fsb_self, index, &m) || !fsb_str(&fsb_self, m.path, &path))
        return corrupt("module", index);
    return Py_BuildValue("(IIIIIIIs#)", index, (unsigned)m.kind, (unsigned)m.flags, m.code,
                         m.source, m.file, m.dist, path, (Py_ssize_t)m.path.length);
}

static PyObject *find_file(PyObject *self, PyObject *arg) {
    (void)self;
    const char *key;
    Py_ssize_t length;
    int status = key_arg(arg, &key, &length);
    if (status < 0) return NULL;
    if (status > 0) Py_RETURN_NONE;
    uint32_t index = fsb_find_file(&fsb_self, key, (size_t)length);
    if (index == FSB_NONE) Py_RETURN_NONE;
    struct fsb_file f;
    if (!fsb_get_file(&fsb_self, index, &f)) return corrupt("file", index);
    return Py_BuildValue("(IIIIIII)", index, (unsigned)f.kind, (unsigned)f.flags,
                         (unsigned)f.mode, f.blob, f.dist, f.closure);
}

/* Record index argument: 0 with *index set; -1 with an error set. */
static int index_arg(PyObject *arg, const char *what, uint32_t *index) {
    unsigned long value = PyLong_AsUnsignedLong(arg);
    if (value == (unsigned long)-1 && PyErr_Occurred()) return -1;
    if (value > UINT32_MAX) {
        corrupt(what, value);
        return -1;
    }
    *index = (uint32_t)value;
    return 0;
}

static PyObject *file_at(PyObject *self, PyObject *arg) {
    (void)self;
    uint32_t index;
    struct fsb_file f;
    const char *path;
    if (index_arg(arg, "file", &index) < 0) return NULL;
    if (!fsb_get_file(&fsb_self, index, &f) || !fsb_str(&fsb_self, f.path, &path))
        return corrupt("file", index);
    return Py_BuildValue("(IIIIIIIs#)", index, (unsigned)f.kind, (unsigned)f.flags,
                         (unsigned)f.mode, f.blob, f.dist, f.closure, path,
                         (Py_ssize_t)f.path.length);
}

/* Members of one closure: a count n >= 1, then n strictly ascending FILES indices. */
static PyObject *closure(PyObject *self, PyObject *arg) {
    (void)self;
    uint32_t start, count, member, previous = 0;
    if (index_arg(arg, "closure", &start) < 0) return NULL;
    uint64_t files = fsb_count(&fsb_self, FSB_SECTION_FILES);
    if (!fsb_get_word(&fsb_self, start, &count) || count == 0 || count > files)
        return corrupt("closure", start);
    PyObject *members = PyTuple_New(count);
    for (uint32_t i = 0; members != NULL && i < count; i++) {
        uint64_t word = (uint64_t)start + 1 + i;
        if (word > UINT32_MAX || !fsb_get_word(&fsb_self, (uint32_t)word, &member) ||
            member >= files || (i > 0 && member <= previous)) {
            Py_DECREF(members);
            return corrupt("closure", start);
        }
        previous = member;
        PyObject *item = PyLong_FromUnsignedLong(member);
        if (item == NULL) Py_CLEAR(members);
        else PyTuple_SET_ITEM(members, i, item);
    }
    return members;
}

static PyObject *blob_info(PyObject *self, PyObject *arg) {
    (void)self;
    uint32_t index;
    struct fsb_blob b;
    uint8_t digest[FSB_DIGEST_SIZE];
    if (index_arg(arg, "blob", &index) < 0) return NULL;
    if (!fsb_get_blob(&fsb_self, index, &b)) return corrupt("blob", index);
    if (b.digest == FSB_NONE) return Py_BuildValue("(KO)", (unsigned long long)b.size, Py_None);
    if (!fsb_get_digest(&fsb_self, b.digest, digest)) return corrupt("digest of blob", index);
    return Py_BuildValue("(Ky#)", (unsigned long long)b.size, (const char *)digest,
                         (Py_ssize_t)FSB_DIGEST_SIZE);
}

/* Keys of ``section`` starting with key[0:length], followed by ``separator`` when the key is
 * non-empty and the separator is not NUL ("pkg" -> "pkg/"; "" matches every key). */
struct scan {
    enum fsb_section section;
    char *prefix;
    size_t prefix_length;
    uint64_t next;
};

static int scan_start(struct scan *scan, enum fsb_section section, const char *key,
                      size_t length, char separator) {
    scan->section = section;
    scan->prefix_length = length && separator ? length + 1 : length;
    scan->prefix = PyMem_Malloc(scan->prefix_length + 1);
    if (scan->prefix == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    memcpy(scan->prefix, key, length);
    if (scan->prefix_length > length) scan->prefix[length] = separator;
    scan->next = fsb_lower_bound(&fsb_self, section, scan->prefix, scan->prefix_length);
    return 0;
}

/* 1: next key's tail (after the prefix) in *tail; 0: done; -1: corrupt table (error set) */
static int scan_next(struct scan *scan, const char **tail, size_t *tail_length, uint32_t *index) {
    const char *text;
    uint32_t text_length;
    if (scan->next >= fsb_count(&fsb_self, scan->section)) return 0;
    if (!fsb_record_key(&fsb_self, scan->section, scan->next, &text, &text_length)) {
        corrupt("table key", scan->next);
        return -1;
    }
    if (text_length < scan->prefix_length || memcmp(text, scan->prefix, scan->prefix_length))
        return 0;
    *tail = text + scan->prefix_length;
    *tail_length = text_length - scan->prefix_length;
    *index = (uint32_t)scan->next++;
    return 1;
}

static PyObject *list_dir(PyObject *self, PyObject *arg) {
    (void)self;
    const char *key, *tail;
    Py_ssize_t length;
    size_t tail_length;
    uint32_t index;
    int status = key_arg(arg, &key, &length);
    if (status != 0) return status < 0 ? NULL : PyList_New(0);
    struct scan scan;
    PyObject *names = PySet_New(NULL);
    if (names == NULL || scan_start(&scan, FSB_SECTION_FILES, key, (size_t)length, '/') < 0) {
        Py_XDECREF(names);
        return NULL;
    }
    const char *previous = NULL;
    size_t previous_length = 0;
    while ((status = scan_next(&scan, &tail, &tail_length, &index)) > 0) {
        const char *slash = memchr(tail, '/', tail_length);
        size_t child = slash ? (size_t)(slash - tail) : tail_length;
        if (previous && child == previous_length && memcmp(tail, previous, child) == 0)
            continue; /* sorted keys: entries of one subdirectory are adjacent */
        previous = tail;
        previous_length = child;
        PyObject *name = PyUnicode_DecodeUTF8(tail, (Py_ssize_t)child, NULL);
        if (name == NULL || PySet_Add(names, name) < 0) status = -1;
        Py_XDECREF(name);
        if (status < 0) break;
    }
    PyMem_Free(scan.prefix);
    PyObject *result = status < 0 ? NULL : PySequence_List(names);
    Py_DECREF(names);
    if (result != NULL && PyList_Sort(result) < 0) Py_CLEAR(result);
    return result;
}

/* The files below a directory are one run of the sorted table: two binary searches find it,
 * from the first key starting with "path/" to the first after them ("path0": '0' follows '/'),
 * and a range holds it in constant memory, however many files it has (D38). */
static PyObject *files_below(PyObject *self, PyObject *arg) {
    (void)self;
    const char *key;
    Py_ssize_t length;
    uint64_t first = 0, end = 0;
    int status = key_arg(arg, &key, &length);
    if (status < 0) return NULL;
    if (status == 0 && length == 0) end = fsb_count(&fsb_self, FSB_SECTION_FILES);
    if (status == 0 && length > 0) {
        char *bound = PyMem_Malloc((size_t)length + 1);
        if (bound == NULL) return PyErr_NoMemory();
        memcpy(bound, key, (size_t)length);
        bound[length] = '/';
        first = fsb_lower_bound(&fsb_self, FSB_SECTION_FILES, bound, (size_t)length + 1);
        bound[length] = '/' + 1;
        end = fsb_lower_bound(&fsb_self, FSB_SECTION_FILES, bound, (size_t)length + 1);
        PyMem_Free(bound);
    }
    if (end <= first) first = end = 0; /* none (or an unsorted, corrupt table): range(0, 0) */
    return PyObject_CallFunction((PyObject *)&PyRange_Type, "KK", (unsigned long long)first,
                                 (unsigned long long)end);
}

/* O(log n): does any file live below ``path`` ('' is the root)? */
static PyObject *is_dir(PyObject *self, PyObject *arg) {
    (void)self;
    const char *key, *tail;
    Py_ssize_t length;
    size_t tail_length;
    uint32_t index;
    int status = key_arg(arg, &key, &length);
    if (status != 0) return status < 0 ? NULL : Py_NewRef(Py_False);
    struct scan scan;
    if (scan_start(&scan, FSB_SECTION_FILES, key, (size_t)length, '/') < 0) return NULL;
    status = scan_next(&scan, &tail, &tail_length, &index);
    PyMem_Free(scan.prefix);
    return status < 0 ? NULL : PyBool_FromLong(status > 0);
}

static PyObject *list_modules(PyObject *self, PyObject *arg) {
    (void)self;
    const char *key, *tail;
    Py_ssize_t length;
    size_t tail_length;
    uint32_t index;
    int status = key_arg(arg, &key, &length);
    if (status != 0) return status < 0 ? NULL : PyList_New(0);
    struct scan scan; /* the caller's prefix already ends with "." ("pkg.") or is "" */
    PyObject *children = PyList_New(0);
    if (children == NULL || scan_start(&scan, FSB_SECTION_MODULES, key, (size_t)length, 0) < 0) {
        Py_XDECREF(children);
        return NULL;
    }
    while ((status = scan_next(&scan, &tail, &tail_length, &index)) > 0) {
        struct fsb_module m;
        if (memchr(tail, '.', tail_length)) continue;
        if (!fsb_get_module(&fsb_self, index, &m)) {
            corrupt("module", index);
            status = -1;
            break;
        }
        PyObject *item = Py_BuildValue("(s#I)", tail, (Py_ssize_t)tail_length, (unsigned)m.kind);
        if (item == NULL || PyList_Append(children, item) < 0) status = -1;
        Py_XDECREF(item);
        if (status < 0) break;
    }
    PyMem_Free(scan.prefix);
    if (status < 0) Py_CLEAR(children);
    return children;
}

static PyObject *dist_tuple(uint32_t index) {
    struct fsb_dist d;
    const char *name, *dist_info, *version;
    if (!fsb_get_dist(&fsb_self, index, &d) || !fsb_str(&fsb_self, d.name, &name) ||
        !fsb_str(&fsb_self, d.dist_info, &dist_info) || !fsb_str(&fsb_self, d.version, &version))
        return corrupt("dist", index);
    return Py_BuildValue("(Is#s#s#)", index, name, (Py_ssize_t)d.name.length, dist_info,
                         (Py_ssize_t)d.dist_info.length, version, (Py_ssize_t)d.version.length);
}

static PyObject *find_dist(PyObject *self, PyObject *arg) {
    (void)self;
    const char *key;
    Py_ssize_t length;
    int status = key_arg(arg, &key, &length);
    if (status < 0) return NULL;
    if (status > 0) Py_RETURN_NONE;
    uint32_t index = fsb_find_dist(&fsb_self, key, (size_t)length);
    if (index == FSB_NONE) Py_RETURN_NONE;
    return dist_tuple(index);
}

static PyObject *dists(PyObject *self, PyObject *noargs) {
    (void)self;
    (void)noargs;
    uint64_t count = fsb_count(&fsb_self, FSB_SECTION_DISTS);
    PyObject *result = PyList_New(0);
    for (uint64_t i = 0; result != NULL && i < count && i < UINT32_MAX; i++) {
        PyObject *item = dist_tuple((uint32_t)i);
        if (item == NULL || PyList_Append(result, item) < 0) Py_CLEAR(result);
        Py_XDECREF(item);
    }
    return result;
}

static PyObject *read_blob(PyObject *self, PyObject *arg) {
    (void)self;
    unsigned long index = PyLong_AsUnsignedLong(arg);
    if (index == (unsigned long)-1 && PyErr_Occurred()) return NULL;
    struct fsb_blob b;
    if (index > UINT32_MAX || !fsb_get_blob(&fsb_self, (uint32_t)index, &b))
        return corrupt("blob", index);
    if (b.codec == FSB_CODEC_ZSTD && !fsb_zstd_frame_ok(fsb_self.base + b.offset, b.stored_size,
                                                        b.size)) /* before allocating size */
        return corrupt("blob data", index);
    if (b.size > PY_SSIZE_T_MAX) return PyErr_NoMemory(); /* what the app asked for: its memory */
    PyObject *out = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)b.size);
    if (out == NULL) return NULL;
    char *dst = PyBytes_AS_STRING(out);
    bool done;
    /* the GIL guards the process's decoder (NULL), which only blocks need; xz, pieces, raw
     * blobs read from the file and streamed ones (larger than a window: never a block) use
     * none, so they run without */
    if (b.codec == FSB_CODEC_XZ || b.codec == FSB_CODEC_PIECES || b.size > FSB_STREAM_WINDOW ||
        (b.codec == FSB_CODEC_NONE && b.size >= FSB_DIGEST_LEAF)) {
        Py_BEGIN_ALLOW_THREADS
        done = fsb_read_blob(&fsb_self, NULL, &b, dst);
        Py_END_ALLOW_THREADS
    } else {
        done = fsb_read_blob(&fsb_self, NULL, &b, dst);
    }
    int error = errno; /* fsb_read_blob's: 0 if the data is corrupt */
    if (!done) {
        Py_DECREF(out);
        if (error == ENOMEM) return PyErr_NoMemory();
        errno = error; /* as fsb_read_blob left it, whatever freeing ``out`` did */
        if (error != 0) return PyErr_SetFromErrno(PyExc_OSError);
        return corrupt("blob data", index);
    }
    reads++;
    return out;
}

/* Files synced in one fsb_cache_sync: their indices and outcomes are all the memory a sync of
 * any number of files needs (D38). Each chunk's records are checked before its files are touched. */
#define SYNC_CHUNK 4096u

static bool marked(const uint8_t *memo, uint32_t index) {
    return memo[index / 8] & (1u << (index % 8));
}

/* Up to SYNC_CHUNK indices from items[*next] on that ``memo`` does not mark as checked yet, into
 * todo[]; their number, or -1 with an error set. */
static Py_ssize_t next_chunk(PyObject *items, Py_ssize_t *next, const uint8_t *memo,
                             uint64_t total, uint32_t *todo) {
    Py_ssize_t count = 0, size = PySequence_Size(items);
    while (size >= 0 && *next < size && count < (Py_ssize_t)SYNC_CHUNK) {
        PyObject *item = PySequence_GetItem(items, (*next)++);
        if (item == NULL) return -1;
        unsigned long index = PyLong_AsUnsignedLong(item);
        Py_DECREF(item);
        if (index == (unsigned long)-1 && PyErr_Occurred()) return -1;
        if (index >= total || index > UINT32_MAX) {
            PyErr_Format(CacheError, "corrupt executable payload: file %lu", index);
            return -1;
        }
        if (!marked(memo, (uint32_t)index)) todo[count++] = (uint32_t)index;
    }
    return size < 0 ? -1 : count;
}

/* After fsb_cache_sync, with the GIL again: report the changed files, mark those done in
 * ``memo``, count the files written (appending their paths to ``written`` unless it is None),
 * or raise the failure: 0 or -1. */
static int synced(const struct fsb_cache_sync *job, PyObject *root, uint8_t *memo,
                  PyObject *written, unsigned long long *count) {
    int status = 0;
    for (uint64_t i = 0; i < job->count; i++) {
        struct fsb_file f;
        const char *path;
        uint8_t outcome = job->outcome[i];
        uint32_t index = job->files[i];
        if (outcome == 0 || !fsb_get_file(&fsb_self, index, &f) ||
            !fsb_str(&fsb_self, f.path, &path))
            continue; /* not reached (the records of a file reached are checked) */
        if (outcome & FSB_CACHE_CHANGED) /* stderr, not a warning: apps that silence warnings
                                          * must not hide this, and it never raises */
            PySys_FormatStderr("fsbpyr: %U/%s/%s was modified after extraction; restoring it\n",
                               root, job->chain, path);
        if (outcome & FSB_CACHE_DONE) memo[index / 8] |= (uint8_t)(1u << (index % 8));
        if (!(outcome & FSB_CACHE_WRITTEN)) continue;
        ++*count;
        if (status == 0 && written != Py_None) {
            PyObject *name = PyUnicode_DecodeUTF8(path, (Py_ssize_t)f.path.length, NULL);
            if (name == NULL || PyList_Append(written, name) < 0) status = -1;
            Py_XDECREF(name);
        }
    }
    if (job->failure == FSB_CACHE_OK) return status;
    PyErr_Clear(); /* the failure says what went wrong first */
    PyObject *path = PyUnicode_DecodeFSDefault(job->path);
    if (path == NULL) return -1;
    switch (job->failure) {
    case FSB_CACHE_CORRUPT:
        PyErr_Format(CacheError, "corrupt executable payload: %s %u", job->reason,
                     (unsigned)job->file);
        break;
    case FSB_CACHE_FOREIGN:
        PyErr_Format(CacheError, "refusing cache directory %U: owned by another user", path);
        break;
    case FSB_CACHE_SYSTEM:
        errno = job->error;
        PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, path);
        break;
    default:
        PyErr_NoMemory();
    }
    Py_DECREF(path);
    return -1;
}

/* sync(root, chain, files, checked, resources[, written]): extract the files (FILES indices: a
 * range, as files_below gives, or a sequence) missing below <root>/<chain> and replace those that
 * differ from the payload (cache.h, D35), SYNC_CHUNK at a time. Each process checks a file once:
 * ``checked`` holds a bit per file (bit i % 8 of byte i / 8), set once the file is checked or
 * written. Returns the number of files written, their site-relative paths appended to the list
 * ``written`` if one is given; the GIL is released while it works. */
static PyObject *cache_sync(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *root, *chain_arg, *files, *checked, *written = Py_None, *items = NULL;
    PyObject *root_text = NULL, *result = NULL;
    int resources;
    if (!PyArg_ParseTuple(args, "O&UOOp|O:sync", PyUnicode_FSConverter, &root, &chain_arg,
                          &files, &checked, &resources, &written))
        return NULL;
    Py_buffer memo = {0};
    uint32_t *todo = NULL;
    uint64_t total = fsb_count(&fsb_self, FSB_SECTION_FILES);
    unsigned long long count = 0;
    uint8_t *outcome = NULL;
    struct fsb_cache_sync *job = NULL;
    Py_ssize_t chain_length, next = 0, chunk;
    const char *chain = PyUnicode_AsUTF8AndSize(chain_arg, &chain_length);
    if (chain == NULL) goto done;
    if (!fsb_cache_safe(chain, (size_t)chain_length)) {
        PyErr_Format(PyExc_ValueError, "unsafe cache chain %R", chain_arg);
        goto done;
    }
    if (written != Py_None && !PyList_Check(written)) {
        PyErr_SetString(PyExc_TypeError, "written must be a list or None");
        goto done;
    }
    if (PyObject_GetBuffer(checked, &memo, PyBUF_WRITABLE) != 0) goto done;
    if ((uint64_t)memo.len != (total + 7) / 8) {
        PyErr_SetString(PyExc_ValueError, "checked must hold one bit per file");
        goto done;
    }
    /* a range or another sequence is read an item at a time; anything else is made a list */
    items = PySequence_Check(files) ? Py_NewRef(files)
                                    : PySequence_Fast(files, "expected a sequence of FILES indices");
    todo = PyMem_Malloc(SYNC_CHUNK * sizeof *todo);
    outcome = PyMem_Malloc(SYNC_CHUNK);
    job = PyMem_Malloc(sizeof *job);
    if (items == NULL) goto done;
    if (todo == NULL || outcome == NULL || job == NULL) {
        PyErr_NoMemory();
        goto done;
    }
    if ((root_text = PyUnicode_DecodeFSDefault(PyBytes_AS_STRING(root))) == NULL) goto done;
    while ((chunk = next_chunk(items, &next, memo.buf, total, todo)) != 0) {
        if (chunk < 0) goto done;
        memset(outcome, 0, (size_t)chunk);
        *job = (struct fsb_cache_sync){.root = PyBytes_AS_STRING(root), .chain = chain,
                                       .files = todo, .count = (uint64_t)chunk,
                                       .resources = resources, .outcome = outcome};
        Py_BEGIN_ALLOW_THREADS
        fsb_cache_sync(&fsb_self, job);
        Py_END_ALLOW_THREADS
        if (synced(job, root_text, memo.buf, written, &count) < 0) goto done;
    }
    result = PyLong_FromUnsignedLongLong(count);
done:
    if (memo.obj != NULL) PyBuffer_Release(&memo);
    PyMem_Free(todo);
    PyMem_Free(outcome);
    PyMem_Free(job);
    Py_XDECREF(items);
    Py_XDECREF(root_text);
    Py_DECREF(root);
    return result;
}

/* Stream: one blob's content a window at a time (D38), for resources opened as files: a window
 * (fsb_stream_next) is copied out as asked, and the next one decoded when it is used up. Only
 * blocks share the process's decoder, which the GIL guards; other codecs decode without it. */
typedef struct {
    PyObject_HEAD
    struct fsb_stream *stream;
    uint32_t blob;
    int failed; /* the status of a failed window: every call after fails with it */
    bool block, busy;
    const uint8_t *window; /* content [start, start + length), read up to ``offset`` */
    uint64_t start, length, offset;
} StreamObject;

static PyTypeObject *StreamType;

/* The error for a failed window: the window is dropped, and the stream fails every call after
 * (fsb_stream_next is sticky), so nothing of a damaged blob is read past the damage. */
static PyObject *stream_failed(StreamObject *self, int status) {
    self->failed = status;
    self->start += self->offset;
    self->length = self->offset = 0;
    if (status == FSB_STREAM_MEMORY) return PyErr_NoMemory();
    if (status == FSB_STREAM_SYSTEM) {
        errno = fsb_stream_error(self->stream);
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    return corrupt("blob data", self->blob);
}

/* 0, or -1 with an error set: closed, in use on another thread, or failed before. */
static int stream_take(StreamObject *self) {
    if (self->stream == NULL) {
        PyErr_SetString(PyExc_ValueError, "I/O operation on closed stream");
        return -1;
    }
    if (self->busy) {
        PyErr_SetString(PyExc_RuntimeError, "stream in use on another thread");
        return -1;
    }
    if (self->failed) {
        stream_failed(self, self->failed);
        return -1;
    }
    self->busy = true;
    return 0;
}

static PyObject *stream_readinto(StreamObject *self, PyObject *arg) {
    Py_buffer into;
    if (PyObject_GetBuffer(arg, &into, PyBUF_WRITABLE) != 0) return NULL;
    if (stream_take(self) < 0) {
        PyBuffer_Release(&into);
        return NULL;
    }
    int status = FSB_STREAM_DATA;
    if (self->offset == self->length && into.len > 0) { /* the next window */
        self->start += self->length;
        self->length = self->offset = 0;
        if (self->block) { /* the process's decoder: the GIL guards it */
            status = fsb_stream_next(self->stream, &self->window, &self->length);
        } else {
            Py_BEGIN_ALLOW_THREADS
            status = fsb_stream_next(self->stream, &self->window, &self->length);
            Py_END_ALLOW_THREADS
        }
    }
    self->busy = false;
    uint64_t size = 0;
    if (status == FSB_STREAM_DATA) {
        size = self->length - self->offset;
        if (size > (uint64_t)into.len) size = (uint64_t)into.len;
        memcpy(into.buf, self->window + self->offset, (size_t)size);
        self->offset += size;
    }
    PyBuffer_Release(&into);
    if (status != FSB_STREAM_DATA && status != FSB_STREAM_END) return stream_failed(self, status);
    return PyLong_FromUnsignedLongLong(size);
}

/* Inside the window, only the offset moves; else the stream does, decoding nothing yet. */
static PyObject *stream_seek(StreamObject *self, PyObject *arg) {
    unsigned long long position = PyLong_AsUnsignedLongLong(arg);
    if (position == (unsigned long long)-1 && PyErr_Occurred()) return NULL;
    if (stream_take(self) < 0) return NULL;
    self->busy = false;
    if (position > fsb_stream_size(self->stream)) {
        PyErr_SetString(PyExc_ValueError, "position beyond the end of the stream");
        return NULL;
    }
    if (position >= self->start && position <= self->start + self->length) {
        self->offset = position - self->start;
        return PyLong_FromUnsignedLongLong(position);
    }
    int status = fsb_stream_seek(self->stream, position);
    if (status != FSB_STREAM_DATA) return stream_failed(self, status);
    self->start = position;
    self->length = self->offset = 0;
    return PyLong_FromUnsignedLongLong(position);
}

static PyObject *stream_tell(StreamObject *self, PyObject *noargs) {
    (void)noargs;
    return PyLong_FromUnsignedLongLong(self->start + self->offset);
}

static PyObject *stream_close(StreamObject *self, PyObject *noargs) {
    (void)noargs;
    if (self->busy) {
        PyErr_SetString(PyExc_RuntimeError, "stream in use on another thread");
        return NULL;
    }
    fsb_stream_close(self->stream);
    self->stream = NULL;
    Py_RETURN_NONE;
}

static PyObject *stream_size(StreamObject *self, void *closure) {
    (void)closure;
    struct fsb_blob b;
    if (!fsb_get_blob(&fsb_self, self->blob, &b)) return corrupt("blob", self->blob);
    return PyLong_FromUnsignedLongLong(b.size);
}

static void stream_dealloc(StreamObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    fsb_stream_close(self->stream);
    type->tp_free(self);
    Py_DECREF(type);
}

static PyMethodDef stream_methods[] = {
    {"readinto", (PyCFunction)stream_readinto, METH_O, "Read into a buffer; 0 at the end."},
    {"seek", (PyCFunction)stream_seek, METH_O, "Move to an absolute position of the content."},
    {"tell", (PyCFunction)stream_tell, METH_NOARGS, "The position in the content."},
    {"close", (PyCFunction)stream_close, METH_NOARGS, "Release the stream's memory."},
    {NULL, NULL, 0, NULL},
};

static PyGetSetDef stream_getset[] = {
    {"size", (getter)stream_size, NULL, "The content's size.", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

static PyType_Slot stream_slots[] = {
    {Py_tp_dealloc, stream_dealloc},
    {Py_tp_methods, stream_methods},
    {Py_tp_getset, stream_getset},
    {Py_tp_doc, "The content of a payload blob, a window at a time (_fsbpyr.stream)."},
    {0, NULL},
};

static PyType_Spec stream_spec = {"_fsbpyr.Stream", sizeof(StreamObject), 0, Py_TPFLAGS_DEFAULT,
                                  stream_slots};

static PyObject *open_stream(PyObject *self, PyObject *arg) {
    (void)self;
    uint32_t index;
    struct fsb_blob b;
    if (index_arg(arg, "blob", &index) < 0) return NULL;
    if (!fsb_get_blob(&fsb_self, index, &b)) return corrupt("blob", index);
    StreamObject *stream = PyObject_New(StreamObject, StreamType);
    if (stream == NULL) return NULL;
    stream->stream = fsb_stream_open(&fsb_self, NULL, &b);
    stream->blob = index;
    stream->failed = 0;
    stream->block = b.codec == FSB_CODEC_BLOCK;
    stream->busy = false;
    stream->window = NULL;
    stream->start = stream->length = stream->offset = 0;
    if (stream->stream == NULL) {
        Py_DECREF(stream);
        return PyErr_NoMemory();
    }
    return (PyObject *)stream;
}

static PyObject *info(PyObject *self, PyObject *noargs) {
    (void)self;
    (void)noargs;
    const struct fsb_header *h = &fsb_self.header;
    const char *app, *entry, *build;
    if (!fsb_str(&fsb_self, h->app_name, &app) || !fsb_str(&fsb_self, h->entrypoint, &entry) ||
        !fsb_str(&fsb_self, h->build_info, &build))
        return corrupt("header string", 0);
    return Py_BuildValue(
        "{s:N,s:s#,s:s#,s:s#,s:y#,s:I,s:K,s:K,s:K}", "executable",
        PyUnicode_DecodeFSDefault(fsb_self.executable), /* surrogateescape, like sys.executable */
        "app_name", app, (Py_ssize_t)h->app_name.length, "entrypoint", entry,
        (Py_ssize_t)h->entrypoint.length, "build_info", build, (Py_ssize_t)h->build_info.length,
        "generation_id", (const char *)h->generation_id, (Py_ssize_t)sizeof h->generation_id,
        "python_hexversion", h->python_hexversion, "payload_size",
        (unsigned long long)fsb_self.size, "modules",
        (unsigned long long)fsb_count(&fsb_self, FSB_SECTION_MODULES), "files",
        (unsigned long long)fsb_count(&fsb_self, FSB_SECTION_FILES));
}

static PyObject *stats(PyObject *self, PyObject *noargs) {
    (void)self;
    (void)noargs;
    unsigned long long stored = fsb_stored_bytes, decompressed = fsb_decompressed_bytes;
    return Py_BuildValue("{s:K,s:K,s:K,s:K}", "reads", reads, "stored_bytes", stored,
                         "decompressed_bytes", decompressed, "integrity_ns", fsb_integrity_ns);
}

static PyMethodDef methods[] = {
    {"find_module", find_module, METH_O, "Look up a module record by dotted name."},
    {"find_file", find_file, METH_O, "Look up a file record by site-relative path."},
    {"file_at", file_at, METH_O, "File record by index, with its path."},
    {"closure", closure, METH_O, "FILES indices of a closure (by CLOSURES word index)."},
    {"blob_info", blob_info, METH_O, "(size, integrity digest or None) of a blob's content."},
    {"read", read_blob, METH_O, "Return the uncompressed content of a blob."},
    {"stream", open_stream, METH_O, "The content of a blob, a window at a time (a Stream)."},
    {"list_dir", list_dir, METH_O, "Sorted names directly below a payload directory."},
    {"is_dir", is_dir, METH_O, "Whether any payload file lives below a directory."},
    {"list_modules", list_modules, METH_O, "(name, kind) of the direct children of a prefix."},
    {"find_dist", find_dist, METH_O, "Look up a distribution by canonical name."},
    {"dists", dists, METH_NOARGS, "All distributions, sorted by canonical name."},
    {"info", info, METH_NOARGS, "Describe the running executable."},
    {"stats", stats, METH_NOARGS, "Payload read counters and the integrity check's time."},
    {"files_below", files_below, METH_O, "FILES indices of every file below a directory."},
    {"sync", cache_sync, METH_VARARGS, "Extract files to the cache, or check them there (D35)."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_fsbpyr", "Access to the fsbpyr executable payload.", -1, methods,
    NULL, NULL, NULL, NULL,
};

PyMODINIT_FUNC PyInit__fsbpyr(void) {
    PyObject *m = PyModule_Create(&module);
    if (m == NULL) return NULL;
    StreamType = (PyTypeObject *)PyType_FromSpec(&stream_spec);
    if (StreamType == NULL || PyModule_AddObjectRef(m, "Stream", (PyObject *)StreamType) < 0) {
        Py_DECREF(m);
        return NULL;
    }
    PayloadError = PyErr_NewException("_fsbpyr.PayloadError", PyExc_ValueError, NULL);
    CacheError = PyErr_NewExceptionWithDoc(
        "_fsbpyr.CacheError",
        "The native cache cannot be used, or the payload's data for it is corrupt.",
        PyExc_ImportError, NULL);
    if (PayloadError == NULL || PyModule_AddObjectRef(m, "PayloadError", PayloadError) < 0 ||
        CacheError == NULL || PyModule_AddObjectRef(m, "CacheError", CacheError) < 0 ||
        PyModule_AddIntConstant(m, "NONE", FSB_NONE) < 0) {
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
