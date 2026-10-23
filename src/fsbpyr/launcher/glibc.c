/* What keeps the stub within fsbpyr's glibc baseline (2.34) although it links against Debian
   12's glibc 2.36: glibc 2.35 gave hypot a new symbol version, which CPython's math module
   would bind to. The link's --wrap=hypot sends its calls here, to the version every glibc has
   (an entry point glibc keeps for compatibility: the same results, errno set the older way),
   looked up by dlvsym: a .symver directive breaks the aarch64 LTO link (LLVM's .lto_discard
   cannot name hypot@GLIBC_2.17). tests/e2e/test_portability.py checks that the stub needs
   nothing newer than the baseline. */
#include <dlfcn.h>
#include <stdatomic.h>
#include <stddef.h>

#if defined(__x86_64__)
#define OLD_HYPOT "GLIBC_2.2.5"
#elif defined(__aarch64__)
#define OLD_HYPOT "GLIBC_2.17"
#else
#error "no pre-2.35 hypot version known for this architecture"
#endif

typedef double (*hypot_fn)(double, double);

static _Atomic(hypot_fn) old_hypot;

double __wrap_hypot(double x, double y);

/* Looked up on the first call; two threads that race look it up twice and store the same
   pointer. Without the old version (a glibc built without it), the default one. */
double __wrap_hypot(double x, double y) {
    hypot_fn fn = atomic_load_explicit(&old_hypot, memory_order_acquire);
    if (fn == NULL) {
        fn = (hypot_fn)dlvsym(RTLD_DEFAULT, "hypot", OLD_HYPOT);
        if (fn == NULL) {
            fn = (hypot_fn)dlsym(RTLD_DEFAULT, "hypot");
        }
        atomic_store_explicit(&old_hypot, fn, memory_order_release);
    }
    return fn(x, y);
}
