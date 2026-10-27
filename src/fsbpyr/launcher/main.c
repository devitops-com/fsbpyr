/* fsbpyr launcher: start the embedded CPython with startup modules served from the payload.
 *
 * Sequence (plan §3.3): check the whole file against its digest (D31), map and validate the
 * payload, check the bytecode/Python version, hold one of the app's instance slots (D45), build
 * CPython's frozen-module table from the STARTUP section (checksum-verified decompression),
 * register _fsbpyr, configure an isolated interpreter that never probes the filesystem for the
 * stdlib, and run `_fsbpyr_boot.run()` through Py_RunMain so exit codes, SystemExit and SIGINT
 * behave exactly as in CPython.
 */
#include "launcher.h"

#include "due.h"
#include "slots.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/auxv.h>

#ifndef FSBPYR_BYTECODE_MAGIC
#error "define FSBPYR_BYTECODE_MAGIC: importlib.util.MAGIC_NUMBER as a little-endian u32"
#endif

#ifndef FSBPYR_RUNTIME_ID
#define FSBPYR_RUNTIME_ID "none" /* test programs compiled from these sources */
#endif

/* The runtime id this stub was linked as (stub.py passes it). A build that is handed a stub it
 * did not link, an EL8 one, reads this string out of the file (stub.stub_identity) to prove it
 * was linked from the inputs of the id the build computes: the launcher sources and flags, the
 * Python pin, the sysroot and the layout version (several releases may share them). Kept by
 * `used` through LTO and stripping; nothing reads it at run time. */
__attribute__((used)) static const char identity[] = "fsbpyr-stub-identity=" FSBPYR_RUNTIME_ID;

#define EXIT_CORRUPT 70 /* EX_SOFTWARE */
#define MAX_STARTUP_MODULES 4096u
#define MAX_STARTUP_CODE (16u << 20)

struct fsb_payload fsb_self;

#if defined(__has_feature)
#if __has_feature(address_sanitizer)
/* The sanitized test variant (D38): CPython frees little at exit by design (no leak report), and
 * malloc returns NULL for a size no allocator can give, as it does without ASan: the launcher
 * reports that as out of memory. A finding exits 99, a status neither Python nor we use (1 is a
 * Python exception, 70 a refused start), so no test can take one for an expected failure. */
const char *__asan_default_options(void) {
    return "detect_leaks=0:allocator_may_return_null=1:exitcode=99";
}
const char *__ubsan_default_options(void) {
    return "print_stacktrace=1:exitcode=99";
}
#endif
#endif

static const wchar_t BOOT_COMMAND[] = L"import _fsbpyr_boot\n_fsbpyr_boot.run()\n";

_Noreturn static void fatal(const char *what) {
    fprintf(stderr, "fsbpyr: cannot start %s: %s\n",
            fsb_self.executable[0] ? fsb_self.executable : "executable", what);
    exit(EXIT_CORRUPT);
}

static void check_versions(void) {
    uint32_t magic;
    memcpy(&magic, fsb_self.header.bytecode_magic, sizeof magic);
    if (magic != (uint32_t)FSBPYR_BYTECODE_MAGIC)
        fatal("payload bytecode was compiled for a different Python version");
    if ((fsb_self.header.python_hexversion >> 16) != ((uint32_t)PY_VERSION_HEX >> 16))
        fatal("payload targets a different Python version");
}

/* Decompress (and so checksum-verify: a zstd frame, or the block it lies in) one startup module:
 * marshal never sees bytes that failed a checksum. A member's position and size, like every table
 * field, are not checksummed (docs/format.md). */
static const unsigned char *startup_code(const struct fsb_blob *b) {
    unsigned char *code = malloc((size_t)b->size);
    if (code == NULL) fatal("out of memory");
    if (!fsb_read_blob(&fsb_self, NULL, b, code)) fatal("corrupt startup module");
    return code;
}

static const struct _frozen *startup_table(void) {
    uint64_t count = fsb_count(&fsb_self, FSB_SECTION_STARTUP);
    if (count > MAX_STARTUP_MODULES) fatal("too many startup modules");
    struct _frozen *table = calloc(count + 1, sizeof *table); /* zeroed sentinel at the end */
    if (table == NULL) fatal("out of memory");
    for (uint32_t i = 0; i < count; i++) {
        uint32_t index;
        struct fsb_module m;
        struct fsb_blob b;
        const char *name;
        if (!fsb_get_startup(&fsb_self, i, &index) || !fsb_get_module(&fsb_self, index, &m) ||
            !(m.flags & FSB_MODULE_STARTUP) || !fsb_get_blob(&fsb_self, m.code, &b) ||
            (b.codec != FSB_CODEC_ZSTD && b.codec != FSB_CODEC_BLOCK) || /* checksummed */
            b.size == 0 || b.size > MAX_STARTUP_CODE ||
            !fsb_str(&fsb_self, m.name, &name))
            fatal("invalid startup module");
        table[i].name = name;
        table[i].code = startup_code(&b);
        table[i].size = (int)b.size;
        table[i].is_package = m.kind == FSB_PACKAGE;
    }
    return table;
}

static void check(PyStatus status) {
    if (PyStatus_Exception(status)) Py_ExitStatusException(status);
}

/* Pin every prefix to the executable's directory so getpath has nothing to search (S2). The
 * stdlib directory is the executable itself: the root of the payload's virtual tree, so CPython's
 * frozen modules get __file__/__path__ inside that tree (e.g. encodings.__path__ = [<exe>/encodings]).
 */
static void pin_prefixes(PyConfig *config) {
    char dir[sizeof fsb_self.executable];
    memcpy(dir, fsb_self.executable, sizeof dir);
    char *slash = strrchr(dir, '/');
    if (slash == NULL) fatal("executable path is not absolute");
    *(slash == dir ? slash + 1 : slash) = '\0';
    wchar_t **fields[] = {&config->home, &config->prefix, &config->exec_prefix,
                          &config->base_prefix, &config->base_exec_prefix};
    for (size_t i = 0; i < sizeof fields / sizeof fields[0]; i++)
        check(PyConfig_SetBytesString(config, fields[i], dir));
    check(PyConfig_SetBytesString(config, &config->stdlib_dir, fsb_self.executable));
}

int main(int argc, char **argv) {
    const char *error, *app;
    if (!fsb_payload_open(&fsb_self, &error)) fatal(error);
    /* set-uid, set-gid or file capabilities: the cache would still come from the caller's
     * environment (FSBPYR_CACHE_DIR, XDG, HOME), and a set-gid or capable start keeps a cache the
     * invoking user owns */
    if (getauxval(AT_SECURE))
        fatal("refusing a privileged start (set-uid, set-gid or file capabilities): the cache "
              "location comes from the caller's environment");
    check_versions();
    /* before Python and the collection's fork: a start over the app's limit exits here (D45) */
    const char *slot_dirs[] = {"/dev/shm", getenv("XDG_RUNTIME_DIR")};
    fsb_hold_slot(fsb_self.dev, fsb_self.ino, slot_dirs, sizeof slot_dirs / sizeof slot_dirs[0]);
    if (fsb_str(&fsb_self, fsb_self.header.app_name, &app)) /* before Python: no threads yet */
        fsb_collect_when_due(app, fsb_self.header.app_name.length, argc > 0 ? argv[0] : app);
    PyImport_FrozenModules = startup_table();
    if (PyImport_AppendInittab("_fsbpyr", PyInit__fsbpyr) != 0) fatal("cannot register _fsbpyr");

    PyPreConfig preconfig;
    PyPreConfig_InitIsolatedConfig(&preconfig);
    preconfig.utf8_mode = 1;
    check(Py_PreInitialize(&preconfig));

    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    /* as `python` has them: SIGINT raises KeyboardInterrupt; SIGPIPE and SIGXFSZ are ignored, so
     * a write to a closed pipe or socket raises BrokenPipeError and one past the file-size limit
     * fails with EFBIG, where both would end the process (D38, security review) */
    config.install_signal_handlers = 1;
    config.parse_argv = 0;
    config.site_import = 0;
    config.safe_path = 1;
    config.write_bytecode = 0;
    config.pathconfig_warnings = 0;
    config.module_search_paths_set = 1; /* empty sys.path until the boot module installs its hook */
    config.import_time = getenv("FSBPYR_IMPORTTIME") != NULL;
    /* the one PYTHON* variable honoured, as CPython reads it: set and non-empty (D14) */
    const char *unbuffered = getenv("PYTHONUNBUFFERED");
    if (unbuffered != NULL && unbuffered[0] != '\0') config.buffered_stdio = 0;
    check(PyConfig_SetBytesArgv(&config, argc, argv));
    check(PyConfig_SetBytesString(&config, &config.executable, fsb_self.executable));
    pin_prefixes(&config);
    check(PyConfig_SetString(&config, &config.run_command, BOOT_COMMAND));
    PyStatus status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    check(status);
    return Py_RunMain();
}
