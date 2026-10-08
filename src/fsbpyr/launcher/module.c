/* _fsbpyr: built-in module giving the boot code bounds-checked access to the payload.
 *
 *   find_module(name)  -> (index, kind, flags, code, source, file, dist, path) | None
 *   find_file(path)    -> (index, kind, flags, mode, blob, dist, closure) | None
 *   file_at(index)     -> (index, kind, flags, mode, blob, dist, closure, path)
 *   closure(word)      -> FILES indices of the closure starting at CLOSURES word ``word``
 *   blob_info(blob)    -> (size, SHA-256 of the uncompressed content | None)
 *   list_dir(path)     -> sorted names directly below a directory ('' is the root)
 *   is_dir(path)       -> whether any file lives below a directory (O(log n))
 *   list_modules(pre)  -> [(name, kind)] direct children of "pkg." ('' is the top level)
 *   find_dist(name)    -> (index, name, dist_info, version) | None;  dists() -> all of them
 *   read(blob)         -> bytes (decompressed, size-checked)
 *   info()             -> dict describing the executable
 *   stats()            -> read counters and integrity check time (FSBPYR_TRACE, benchmarks)
 */
#include "launcher.h"

#include "integrity.h"

#define MAX_MEMORY_BLOB (UINT64_C(1) << 30) /* larger blobs are extracted, never read whole */

static PyObject *PayloadError;
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
    if (b.size > MAX_MEMORY_BLOB) return corrupt("blob too large to load into memory", index);
    if (b.codec == FSB_CODEC_ZSTD && !fsb_zstd_frame_ok(fsb_self.base + b.offset, b.stored_size,
                                                        b.size)) /* before allocating size */
        return corrupt("blob data", index);
    PyObject *out = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)b.size);
    if (out == NULL) return NULL;
    if (!fsb_read_blob(&fsb_self, &b, PyBytes_AS_STRING(out))) {
        Py_DECREF(out);
        return corrupt("blob data", index);
    }
    reads++;
    return out;
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
    return Py_BuildValue("{s:K,s:K,s:K,s:K}", "reads", reads, "stored_bytes", fsb_stored_bytes,
                         "decompressed_bytes", fsb_decompressed_bytes, "integrity_ns",
                         fsb_integrity_ns);
}

static PyMethodDef methods[] = {
    {"find_module", find_module, METH_O, "Look up a module record by dotted name."},
    {"find_file", find_file, METH_O, "Look up a file record by site-relative path."},
    {"file_at", file_at, METH_O, "File record by index, with its path."},
    {"closure", closure, METH_O, "FILES indices of a closure (by CLOSURES word index)."},
    {"blob_info", blob_info, METH_O, "(size, SHA-256 or None) of a blob's content."},
    {"read", read_blob, METH_O, "Return the uncompressed content of a blob."},
    {"list_dir", list_dir, METH_O, "Sorted names directly below a payload directory."},
    {"is_dir", is_dir, METH_O, "Whether any payload file lives below a directory."},
    {"list_modules", list_modules, METH_O, "(name, kind) of the direct children of a prefix."},
    {"find_dist", find_dist, METH_O, "Look up a distribution by canonical name."},
    {"dists", dists, METH_NOARGS, "All distributions, sorted by canonical name."},
    {"info", info, METH_NOARGS, "Describe the running executable."},
    {"stats", stats, METH_NOARGS, "Payload read counters and the integrity check's time."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_fsbpyr", "Access to the fsbpyr executable payload.", -1, methods,
    NULL, NULL, NULL, NULL,
};

PyMODINIT_FUNC PyInit__fsbpyr(void) {
    PyObject *m = PyModule_Create(&module);
    if (m == NULL) return NULL;
    PayloadError = PyErr_NewException("_fsbpyr.PayloadError", PyExc_ValueError, NULL);
    if (PayloadError == NULL || PyModule_AddObjectRef(m, "PayloadError", PayloadError) < 0 ||
        PyModule_AddIntConstant(m, "NONE", FSB_NONE) < 0) {
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
