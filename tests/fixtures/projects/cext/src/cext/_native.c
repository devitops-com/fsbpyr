/* A thread started from C: glibc 2.34 moved pthread_create into libc as GLIBC_2.34, so a build
   against glibc 2.34 or newer needs that version, and one against glibc 2.28 takes GLIBC_2.2.5
   from libpthread. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <pthread.h>

static void *double_it(void *arg) {
    long *value = arg;
    *value *= 2;
    return NULL;
}

static PyObject *twice(PyObject *self, PyObject *arg) {
    long value = PyLong_AsLong(arg);
    pthread_t thread;
    if (value == -1 && PyErr_Occurred()) {
        return NULL;
    }
    if (pthread_create(&thread, NULL, double_it, &value) != 0 || pthread_join(thread, NULL) != 0) {
        return PyErr_Format(PyExc_OSError, "cannot run a thread");
    }
    return PyLong_FromLong(value);
}

static PyMethodDef methods[] = {
    {"twice", twice, METH_O, "The argument doubled in a new thread."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "cext._native", NULL, 0, methods};

PyMODINIT_FUNC PyInit__native(void) {
    return PyModule_Create(&module);
}
