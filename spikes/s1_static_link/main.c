/* S1: plain CPython main, statically linked against PBS libpython3.14.a. */
#include <Python.h>

int main(int argc, char **argv) { return Py_BytesMain(argc, argv); }
