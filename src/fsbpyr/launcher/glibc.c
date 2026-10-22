/* What keeps the stub within fsbpyr's glibc baseline (2.34) although it links against Debian
   12's glibc 2.36: glibc 2.35 gave hypot a new symbol version, which CPython's math module
   would bind to. The link's --wrap=hypot sends its calls here, to the version every glibc has
   (an entry point glibc keeps for compatibility: the same results, errno set the older way).
   tests/e2e/test_portability.py checks that the stub needs nothing newer than the baseline. */
#if defined(__x86_64__)
__asm__(".symver fsbpyr_old_hypot, hypot@GLIBC_2.2.5");
#elif defined(__aarch64__)
__asm__(".symver fsbpyr_old_hypot, hypot@GLIBC_2.17");
#else
#error "no pre-2.35 hypot version known for this architecture"
#endif

double fsbpyr_old_hypot(double x, double y);
double __wrap_hypot(double x, double y);

double __wrap_hypot(double x, double y) {
    return fsbpyr_old_hypot(x, y);
}
