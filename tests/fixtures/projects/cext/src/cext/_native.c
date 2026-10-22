/* A number parsed with strtol: glibc 2.38 redirects strtol to __isoc23_strtol (GLIBC_2.38)
   under _GNU_SOURCE, which Python.h defines, so a build against glibc 2.38 or newer needs that
   version, and one against Debian 12's glibc 2.36 takes strtol's GLIBC_2.2.5 (GLIBC_2.17 on
   aarch64). The doubling runs in a thread the extension starts. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <pthread.h>
#include <stdlib.h>

static void *double_it(void *arg) {
    long *value = arg;
    *value *= 2;
    return NULL;
}

static PyObject *twice(PyObject *self, PyObject *arg) {
    const char *text = PyUnicode_AsUTF8(arg);
    char *end;
    long value;
    pthread_t thread;
    if (text == NULL) {
        return NULL;
    }
    value = strtol(text, &end, 10);
    if (*text == '\0' || *end != '\0') {
        return PyErr_Format(PyExc_ValueError, "not a number: %s", text);
    }
    if (pthread_create(&thread, NULL, double_it, &value) != 0 || pthread_join(thread, NULL) != 0) {
        return PyErr_Format(PyExc_OSError, "cannot run a thread");
    }
    return PyLong_FromLong(value);
}

static PyMethodDef methods[] = {
    {"twice", twice, METH_O, "The number in the argument doubled in a new thread."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "cext._native", NULL, 0, methods};

PyMODINIT_FUNC PyInit__native(void) {
    return PyModule_Create(&module);
}
