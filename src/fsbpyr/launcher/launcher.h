/* Shared declarations between the launcher entry point and the _fsbpyr built-in module. */
#ifndef FSBPYR_LAUNCHER_H
#define FSBPYR_LAUNCHER_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "payload.h"

/* The payload of the running executable; filled by main() before Python starts. */
extern struct fsb_payload fsb_self;

PyMODINIT_FUNC PyInit__fsbpyr(void);

#endif /* FSBPYR_LAUNCHER_H */
