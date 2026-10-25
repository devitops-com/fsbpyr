/* What keeps the stub within fsbpyr's glibc baseline (2.34) although it links against Debian
   12's glibc 2.36. tests/e2e/test_portability.py checks that the stub needs nothing newer than
   the baseline; tests/e2e/test_find_object.py runs the _dl_find_object fallback.

   hypot: glibc 2.35 gave it a new symbol version, which CPython's math module would bind to.
   The link's --wrap=hypot sends its calls here, to the version every glibc has (an entry point
   glibc keeps for compatibility: the same results, errno set the older way), looked up by
   dlvsym: a .symver directive breaks the aarch64 LTO link (LLVM's .lto_discard cannot name
   hypot@GLIBC_2.17).

   _dl_find_object (glibc 2.35): Debian 12's libgcc unwinder (_Unwind_Find_FDE, libgcc_eh.a)
   finds a frame's unwind data with it, and 3.15's libpython links that unwinder in (its JIT
   registers the frames of the code it generates, __register_frame). The link's
   --wrap=_dl_find_object sends the lookup here: to glibc's own where glibc has it, else to the
   program headers of the loaded objects, the way libgcc looked before glibc 2.35. Where the
   headers lack the struct (the EL8 sysroot, glibc 2.28), the wrapper is compiled out: that
   libgcc (8) finds frames with dl_iterate_phdr and never asks, and the link's --wrap=
   finds nothing to wrap. */
#include <dlfcn.h>
#include <link.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>

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

#ifdef DLFO_EH_SEGMENT_TYPE /* link.h, glibc 2.35 and later */

typedef int (*find_object_fn)(void *, struct dl_find_object *);

struct phdr_search {
    uintptr_t pc;
    struct dl_find_object *result;
};

/* The object whose loaded segments hold the address: its mapping as the loader records it
   (from the first segment's page to the last segment's end), its unwind data
   (PT_GNU_EH_FRAME) and its link map. */
static int find_in_object(struct dl_phdr_info *info, size_t size, void *data) {
    (void)size;
    struct phdr_search *search = data;
    uintptr_t start = UINTPTR_MAX, end = 0, eh_frame = 0;
    int holds = 0;
    for (ElfW(Half) i = 0; i < info->dlpi_phnum; i++) {
        const ElfW(Phdr) *phdr = &info->dlpi_phdr[i];
        uintptr_t begin = info->dlpi_addr + phdr->p_vaddr;
        if (phdr->p_type == PT_GNU_EH_FRAME) {
            eh_frame = begin;
        }
        if (phdr->p_type != PT_LOAD) {
            continue;
        }
        start = begin < start ? begin : start;
        end = begin + phdr->p_memsz > end ? begin + phdr->p_memsz : end;
        holds |= search->pc >= begin && search->pc - begin < phdr->p_memsz;
    }
    if (!holds) {
        return 0;
    }
    Dl_info symbol;
    struct link_map *map = NULL;
    if (dladdr1((void *)search->pc, &symbol, (void **)&map, RTLD_DL_LINKMAP) == 0) {
        map = NULL;
    }
    memset(search->result, 0, sizeof *search->result);
    search->result->dlfo_map_start = (void *)(start & ~((uintptr_t)getpagesize() - 1));
    search->result->dlfo_map_end = (void *)end;
    search->result->dlfo_link_map = map;
    search->result->dlfo_eh_frame = (void *)eh_frame;
    return 1;
}

static int find_object_by_phdr(void *pc, struct dl_find_object *result) {
    struct phdr_search search = {(uintptr_t)pc, result};
    return dl_iterate_phdr(find_in_object, &search) ? 0 : -1;
}

static _Atomic(find_object_fn) find_object;

int __wrap__dl_find_object(void *pc, struct dl_find_object *result);

/* Looked up on the first call, as hypot is. */
int __wrap__dl_find_object(void *pc, struct dl_find_object *result) {
    find_object_fn fn = atomic_load_explicit(&find_object, memory_order_acquire);
    if (fn == NULL) {
        fn = (find_object_fn)dlvsym(RTLD_DEFAULT, "_dl_find_object", "GLIBC_2.35");
        if (fn == NULL) {
            fn = find_object_by_phdr;
        }
        atomic_store_explicit(&find_object, fn, memory_order_release);
    }
    return fn(pc, result);
}

#endif
