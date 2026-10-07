/* _fsbpyr: built-in module giving the boot code bounds-checked access to the payload.
 *
 *   find_module(name)  -> (index, kind, flags, code, source, file, dist, path) | None
 *   find_file(path)    -> (index, kind, flags, mode, blob, dist, closure) | None
 *   read(blob)         -> bytes (decompressed, size-checked)
 *   info()             -> dict describing the executable
 *   stats()            -> dict of read counters (FSBPYR_TRACE, benchmarks)
 */
#include "launcher.h"

#define MAX_MEMORY_BLOB (UINT64_C(1) << 30) /* larger blobs are extracted, never read whole */

static PyObject *PayloadError;
static unsigned long long reads, stored_bytes, decompressed_bytes;

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

static PyObject *decompress(const struct fsb_blob *b, const uint8_t *src, unsigned long index) {
    if (!fsb_zstd_frame_ok(src, b->stored_size, b->size)) return corrupt("zstd frame of blob", index);
    PyObject *out = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)b->size);
    if (out == NULL) return NULL;
    if (!fsb_zstd_decompress(PyBytes_AS_STRING(out), b->size, src, b->stored_size)) {
        Py_DECREF(out);
        return corrupt("zstd data of blob", index);
    }
    return out;
}

static PyObject *read_blob(PyObject *self, PyObject *arg) {
    (void)self;
    unsigned long index = PyLong_AsUnsignedLong(arg);
    if (index == (unsigned long)-1 && PyErr_Occurred()) return NULL;
    struct fsb_blob b;
    if (index > UINT32_MAX || !fsb_get_blob(&fsb_self, (uint32_t)index, &b))
        return corrupt("blob", index);
    if (b.size > MAX_MEMORY_BLOB) return corrupt("blob too large to load into memory", index);
    const uint8_t *src = fsb_self.base + b.offset;
    PyObject *out = b.codec == FSB_CODEC_NONE
                        ? PyBytes_FromStringAndSize((const char *)src, (Py_ssize_t)b.size)
                        : decompress(&b, src, index);
    if (out != NULL) {
        reads++;
        stored_bytes += b.stored_size;
        decompressed_bytes += b.codec == FSB_CODEC_NONE ? 0 : b.size;
    }
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
    return Py_BuildValue("{s:K,s:K,s:K}", "reads", reads, "stored_bytes", stored_bytes,
                         "decompressed_bytes", decompressed_bytes);
}

static PyMethodDef methods[] = {
    {"find_module", find_module, METH_O, "Look up a module record by dotted name."},
    {"find_file", find_file, METH_O, "Look up a file record by site-relative path."},
    {"read", read_blob, METH_O, "Return the uncompressed content of a blob."},
    {"info", info, METH_NOARGS, "Describe the running executable."},
    {"stats", stats, METH_NOARGS, "Payload read counters."},
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
