/* S2 spike launcher: run CPython with startup modules served from an mmap'd payload.
 *
 * Executable layout:  [stub ELF][pad to 4 KiB][payload][trailer(64)]
 * Trailer:  "FSBS2\0\0T" | u32 version | u32 flags | u64 payload_offset | u64 payload_size | 32 x u8 zero
 * Payload:  "FSBS2\0\0H" | u32 n_frozen | u32 n_modules | u64 frozen_off | u64 modules_off
 *           entries (24 B): u64 name_off | u64 code_off | u32 code_size | u32 is_package
 * All offsets are payload-relative; names are NUL-terminated. Spike quality: bounds are checked,
 * but there is no digest verification.
 */
#include <Python.h>

#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#define TRAILER_SIZE 64

struct entry { uint64_t name_off, code_off; uint32_t code_size, is_package; };

static const uint8_t *g_payload;
static uint64_t g_payload_size;

static void die(const char *msg) {
    fprintf(stderr, "fsbpyr-spike: %s\n", msg);
    exit(70);
}

static int in_bounds(uint64_t off, uint64_t len) {
    return off <= g_payload_size && len <= g_payload_size - off;
}

static const char *name_at(uint64_t off) {
    if (!in_bounds(off, 1) || !memchr(g_payload + off, 0, g_payload_size - off)) die("bad name offset");
    return (const char *)(g_payload + off);
}

static void map_payload(void) {
    int fd = open("/proc/self/exe", O_RDONLY | O_CLOEXEC);
    struct stat st;
    if (fd < 0 || fstat(fd, &st) != 0 || st.st_size < TRAILER_SIZE) die("cannot open self");
    uint8_t trailer[TRAILER_SIZE];
    if (pread(fd, trailer, TRAILER_SIZE, st.st_size - TRAILER_SIZE) != TRAILER_SIZE) die("cannot read trailer");
    if (memcmp(trailer, "FSBS2\0\0T", 8) != 0) die("no payload trailer");
    uint64_t off, size;
    memcpy(&off, trailer + 16, 8);
    memcpy(&size, trailer + 24, 8);
    if (off % 4096 != 0 || off > (uint64_t)st.st_size || size > (uint64_t)st.st_size - off) die("bad trailer");
    void *map = mmap(NULL, size, PROT_READ, MAP_PRIVATE, fd, (off_t)off);
    if (map == MAP_FAILED) die("mmap failed");
    close(fd);
    g_payload = map;
    g_payload_size = size;
}

static struct _frozen *build_frozen_table(void) {
    if (!in_bounds(0, 32) || memcmp(g_payload, "FSBS2\0\0H", 8) != 0) die("bad payload header");
    uint32_t n;
    uint64_t table;
    memcpy(&n, g_payload + 8, 4);
    memcpy(&table, g_payload + 16, 8);
    if (n > 4096 || !in_bounds(table, (uint64_t)n * sizeof(struct entry))) die("bad frozen table");
    struct _frozen *frozen = calloc(n + 1, sizeof *frozen);
    if (!frozen) die("out of memory");
    for (uint32_t i = 0; i < n; i++) {
        struct entry e;
        memcpy(&e, g_payload + table + (uint64_t)i * sizeof e, sizeof e);
        if (!in_bounds(e.code_off, e.code_size) || e.code_size > INT32_MAX) die("bad frozen entry");
        frozen[i].name = name_at(e.name_off);
        frozen[i].code = g_payload + e.code_off;
        frozen[i].size = (int)e.code_size;
        frozen[i].is_package = (int)e.is_package;
    }
    return frozen; /* zero-filled sentinel terminates the table */
}

/* _spike.payload() -> read-only memoryview over the whole payload. */
static PyObject *spike_payload(PyObject *self, PyObject *noargs) {
    (void)self; (void)noargs;
    return PyMemoryView_FromMemory((char *)g_payload, (Py_ssize_t)g_payload_size, PyBUF_READ);
}

static PyMethodDef spike_methods[] = {
    {"payload", spike_payload, METH_NOARGS, NULL},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef spike_module = {
    PyModuleDef_HEAD_INIT, "_spike", NULL, -1, spike_methods, NULL, NULL, NULL, NULL,
};

static PyObject *PyInit__spike(void) { return PyModule_Create(&spike_module); }

static void check(PyStatus status) {
    if (PyStatus_Exception(status)) Py_ExitStatusException(status);
}

int main(int argc, char **argv) {
    map_payload();
    PyImport_FrozenModules = build_frozen_table();
    if (PyImport_AppendInittab("_spike", PyInit__spike) != 0) die("inittab");

    PyPreConfig pre;
    PyPreConfig_InitIsolatedConfig(&pre);
    pre.utf8_mode = 1;
    check(Py_PreInitialize(&pre));

    char exe[4096];
    ssize_t n = readlink("/proc/self/exe", exe, sizeof exe - 1);
    if (n <= 0) die("readlink self");
    exe[n] = '\0';

    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.parse_argv = 0;
    config.site_import = 0;
    config.safe_path = 1;
    config.write_bytecode = 0;
    config.pathconfig_warnings = 0;
    config.module_search_paths_set = 1; /* empty sys.path */
    config.import_time = getenv("FSBPYR_IMPORTTIME") != NULL;
    check(PyConfig_SetBytesArgv(&config, argc, argv));
    check(PyConfig_SetBytesString(&config, &config.executable, exe));
    if (getenv("S2_SET_PREFIX")) { /* variant: pin prefixes so getpath has nothing to search */
        char *slash = strrchr(exe, '/');
        *slash = '\0';
        check(PyConfig_SetBytesString(&config, &config.home, exe));
        check(PyConfig_SetBytesString(&config, &config.prefix, exe));
        check(PyConfig_SetBytesString(&config, &config.exec_prefix, exe));
        check(PyConfig_SetBytesString(&config, &config.base_prefix, exe));
        check(PyConfig_SetBytesString(&config, &config.base_exec_prefix, exe));
        check(PyConfig_SetBytesString(&config, &config.stdlib_dir, exe));
        *slash = '/';
    }
    check(PyConfig_SetString(&config, &config.run_command, L"import _spike_boot\n_spike_boot.run()\n"));
    check(Py_InitializeFromConfig(&config));
    PyConfig_Clear(&config);
    return Py_RunMain();
}
