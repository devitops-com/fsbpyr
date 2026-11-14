/* sig.c's functions (_sha256, _key_new, _sign and _verify) as a Python module of their own,
 * fsbpyr_sig, for the tests that run fsbpyr from source: tests/support/signing.py's sig_c builds
 * it with sig.c, integrity.c and parallel.c and the pinned distribution's libcrypto.a, loads it
 * into the test process and makes fsbpyr.signing's crypto() return it. Its name is not _fsbpyr:
 * vcs.py and selfupdate.py import _fsbpyr to tell whether this is a built fsbpyr.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "sig.h"

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "fsbpyr_sig", "sig.c's functions, for the tests from source.", -1, NULL,
    NULL, NULL, NULL, NULL,
};

PyMODINIT_FUNC PyInit_fsbpyr_sig(void) {
    PyObject *m = PyModule_Create(&module);
    if (m == NULL) return NULL;
    if (PyModule_AddFunctions(m, fsb_sig_methods) < 0) {
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
