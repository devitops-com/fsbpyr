/* The stub's _dl_find_object (launcher/glibc.c), for tests/e2e/test_find_object.py:
 *
 *   find_object     a line per address: its name and the address, then what glibc.c's
 *                   wrapper answers and what glibc's own _dl_find_object answers, each as
 *                   "RC START END MAP EH" (RC 0 or -1; the mapping, the link map and the
 *                   unwind data, in hex, 0 for none)
 *
 * The addresses: a function of this program, one of libc, one of a library it opens (libm), a
 * variable on the stack and NULL, the last two in no object. Built with
 * -Wl,--wrap=_dl_find_object and -Wl,--wrap=dlvsym: with $PROBE_OLD_GLIBC set, dlvsym finds no
 * _dl_find_object, as on glibc 2.34, and the wrapper answers from the program headers. A last
 * line says how many lookups dlvsym refused: "refused N".
 */
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void *__real_dlvsym(void *handle, const char *symbol, const char *version);
int __real__dl_find_object(void *pc, struct dl_find_object *result);

static int refused;

void *__wrap_dlvsym(void *handle, const char *symbol, const char *version) {
    if (getenv("PROBE_OLD_GLIBC") != NULL && strcmp(symbol, "_dl_find_object") == 0) {
        refused++;
        return NULL;
    }
    return __real_dlvsym(handle, symbol, version);
}

static void answer(int (*find)(void *, struct dl_find_object *), void *pc) {
    struct dl_find_object found;
    memset(&found, 0, sizeof found);
    int rc = find(pc, &found);
    printf(" %d %jx %jx %jx %jx", rc, (uintmax_t)(uintptr_t)found.dlfo_map_start,
           (uintmax_t)(uintptr_t)found.dlfo_map_end, (uintmax_t)(uintptr_t)found.dlfo_link_map,
           (uintmax_t)(uintptr_t)found.dlfo_eh_frame);
}

static void report(const char *name, void *pc) {
    printf("%s %jx", name, (uintmax_t)(uintptr_t)pc);
    answer(_dl_find_object, pc); /* the wrapper: --wrap sends this reference to glibc.c */
    answer(__real__dl_find_object, pc);
    printf("\n");
}

int main(void) {
    void *libm = dlopen("libm.so.6", RTLD_NOW);
    if (libm == NULL) {
        fprintf(stderr, "find_object: %s\n", dlerror());
        return 1;
    }
    int local = 0;
    report("program", (void *)(uintptr_t)main);
    report("libc", dlsym(RTLD_DEFAULT, "printf"));
    report("libm", dlsym(libm, "cos"));
    report("stack", &local);
    report("null", NULL);
    printf("refused %d\n", refused);
    return 0;
}
