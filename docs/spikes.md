# Phase 0 — Spike findings

Date: 2026-10-06. Host: Debian 13 (glibc 2.41), Intel Core Ultra 9 285, clang/lld 22.1.8 (Debian).
Input: PBS release `20261003`, `cpython-3.14.8+20261003-x86_64-unknown-linux-gnu-pgo+lto-full`
(SHA-256 `43b8c1b02e3daefbbbf4b5714b656ea00a5cae24f55c4589fb87468bcac682ed`, verified against the
release `SHA256SUMS`), cached at `~/.cache/fsbpyr/pbs/<name>/python`.
Timings are p50 over 60–150 warm runs (`spikes/bench.py`).

Spike code lives in `spikes/`. Build outputs go to `spikes/.out/`, which is git-ignored.

| Spike | Result | Code |
|-------|--------|------|
| S1 static link | **PASS** | `spikes/common.sh`, `spikes/extra_objs.py`, `spikes/s1_static_link/` |
| S2 init path | **PASS** | `spikes/s2_init_path/`, `spikes/tools/ftrace.c` |
| S3 marshal determinism | **FAIL as-is → PASS with canonicalization** | `spikes/s3_marshal/` |

---

## S1 — Static link of PBS CPython

1. **What libpython.a contains.** `libpython3.14.a` (276 members) has the core, `config.o`
   (the inittab) and every built-in extension object **except the HACL\* hash objects**. The
   missing objects are found by taking the modules registered in `build/Modules/config.c` and
   listing their `PYTHON.json` objects that are not archive members. That yields 11 objects in
   `build/Modules/_hacl/`. See `spikes/extra_objs.py`.
2. **Link inputs:**
   - `libpython3.14.a` and the HACL objects
   - static `ssl crypto sqlite3 ffi z zstd bz2 lzma mpdec expat uuid edit panelw ncursesw`
   - `-lpthread -ldl -lutil -lm -lrt`

   tk, X11 and libdb are not needed, because `_tkinter` and `_dbm` are not built-in.
3. **Target triple.** clang needs `--target=x86_64-unknown-linux-gnu` to match the triple in
   the PBS bitcode. Without it, lld warns about mixing triples.
4. **Cost and size.** A full-LTO link takes about 51 s per stub. The stripped stub is 19.4 MB
   non-PIE or 20.9 MB PIE; unstripped it is about 67 MB, mostly debug info.
5. **Built-in modules.** All 100 are present and work:
   - ssl (OpenSSL 3.5.9)
   - sqlite3 3.53.1
   - `_zstd` 1.5.7
   - hashlib (HACL sha2/blake2/md5), hmac
   - ctypes, decimal, readline, curses
6. **Extensions resolve symbols from the executable.** `-Wl,--export-dynamic` lets them find
   `Py*` there. The manylinux wheels orjson 3.12.0 and numpy 2.5.3 import and run, and numpy
   finds `numpy.libs` through `RPATH $ORIGIN/../../numpy.libs`. This confirms D2 and D9.
7. **Startup** (`-I -S -c pass`, stdlib read from disk):

   | Binary | p50 |
   |--------|-----|
   | static non-PIE | 4.85 ms |
   | static PIE | 5.16 ms |
   | PBS shared libpython | 5.02 ms |
   | uv 3.14.7 | 4.88 ms |

   The PIE has 59,456 `R_X86_64_RELATIVE` relocations; non-PIE has none.
   **Decision: non-PIE stubs (D17).**
8. **glibc requirement.** A stub linked on this host needs **GLIBC_2.38** (`fmod@2.38`,
   `hypot@2.35`, and `pthread_*`/`sem_*`/`shm_*`/`__libc_start_main@2.34`), even though the PBS
   objects target 2.17. Symbol versions bind to the host glibc at link time. A portable baseline
   needs a link against an old-glibc sysroot (Phase 9). This confirms the risk listed in the plan.
9. **SSL defaults.** `openssl_cafile=/etc/ssl/cert.pem` does not exist on Debian;
   `openssl_capath=/etc/ssl/certs` does. Real HTTPS verification has not been tested yet; that
   happens in Phase 6.

## S2 — Init path with a frozen table mapped from the payload

1. **Frozen-table precedence.** `PyImport_FrozenModules`, set at run time to entries that point
   into the mmap'd payload, works. Our table wins over CPython's frozen stdlib: a frozen `stat`
   with a marker attribute was the one loaded. **[assumption → verified]**
2. **Startup set.** Init imports `encodings`, `encodings.aliases`, `encodings.utf_8` and
   `linecache` from our table, which matches the planned STARTUP set.
3. **`__path__` of frozen packages.** They get `__path__ == []`, so boot has to re-point them
   (planned in §3.3 step 7).
4. **Interpreter behaviour.** With isolated `PyConfig`, `parse_argv=0` and `run_command`:
   - `sys.argv` is passed through verbatim, `sys.executable` is the realpath, `sys.path == []`,
     and the isolated, utf8 and safe_path flags are set
   - `sys.exit(3)` → exit status 3
   - an uncaught exception → exit status 1 with a traceback
   - `KeyboardInterrupt` → the process dies by SIGINT (status −2), as CPython does

   The traceback includes a `File "<string>"` frame from `run_command`. This is cosmetic and
   gets fixed in Phase 2.
5. **File syscalls during init**, traced with `spikes/tools/ftrace.c`:
   - **Default config:** about 45 getpath probes (`python314.zip`, `lib/python3.14/os.py`,
     `lib-dynload`, `._pth`, `pybuilddir.txt`).
   - **With `home`, `prefix`, `exec_prefix`, `base_prefix`, `base_exec_prefix` and
     `stdlib_dir` pinned:** no stdlib probes. What remains is 2 `openat(pyvenv.cfg)` returning
     ENOENT (next to and above the executable) and `readlink(exe)`. Skipping those would
     require the deprecated `Py_SetPath` (getpath.py 3.14.8, lines 347–362). They only affect
     `sys.prefix`; imports are unaffected because `module_search_paths_set=1`.
   - **Inherent to CPython:** `/dev/urandom` (hash seed), `/proc/sys/vm/overcommit_memory`,
     `/proc/self/maps` ×2 (stack bounds), `/etc/localtime`.
6. **Startup p50:** the spike app (`noop`) takes **4.76 ms**, against 4.85 ms for
   `python3.14 -I -S -c pass`. That holds even though the spike boot builds a 556-entry Python
   dict index. `encodings` costs 483 µs cumulative from the frozen payload, against 776 µs from
   disk.
7. **Interpreter finalization costs about 0.83 ms** (`noop` that ends with `posix._exit`:
   3.93 ms). This makes an opt-in "fast exit" (flush stdio, run atexit, then `_exit`) a Phase 7
   candidate. It is not a safe default, because buffered files that were never closed would lose
   data.
8. **zipimport.** CPython always imports the frozen `zipimport` during init (about 0.18 ms).
   There is no public way to skip it.

## S3 — Bytecode determinism

1. **When raw `marshal.dumps` is stable.** It is byte-identical across processes when each code
   object is dumped right after compiling and the process state matches. File order and
   `PYTHONHASHSEED` don't matter, and 3.14.7 vs 3.14.8 produce identical output for all 561
   stdlib files.
2. **When it is not.** Keeping all code objects alive before dumping changes **561/561** files.
   A build process that already has some stdlib modules loaded changes **4** files: `argparse`,
   `asyncio/events`, `inspect`, `weakref`.
3. **Two independent causes:**
   - (a) `w_ref` in `Python/marshal.c` sets FLAG_REF whenever refcount > 1. Frozenset element
     order is sorted by the elements' marshal bytes, which carry those flags, so it is affected
     too.
   - (b) Whether a string constant is interned depends on whether an equal string was already
     interned in the process. Example: `"HelpFormatter._Section"` is written as `Z` in one
     process and `z` in another.
4. **Canonicalizer prototype** (`spikes/s3_marshal/canonical.py`, about 170 lines). It parses
   the stream into a graph and then:
   - merges strings by value; a merged string is interned if any occurrence was
   - sets FLAG_REF only if a node occurs ≥ 2 times or is an interned string
   - renumbers refs in pre-order
   - sorts set elements by their canonical encoding

   Results:
   - **0/561 files differ in every scenario**
   - `marshal.loads(canon) == code` holds for all 561 files
   - it is idempotent
   - total size shrinks by 198 bytes
   - `marshal.loads` time is unchanged (16.1 ms for the whole stdlib)

   **Decision D16:** all bytecode goes through the canonicalizer (`fsbpyr/bytecode.py`). It is
   tied to marshal version 5 and the CPython 3.14 code-object layout. A round-trip test over the
   full stdlib plus fixture dependencies is a required test.

## Decisions

| Plan item | Status |
|-----------|--------|
| D1 C launcher | confirmed |
| D2 static CPython | confirmed. Additions: `--target=x86_64-unknown-linux-gnu`, HACL objects found via `config.c` |
| D7 C index lookups | still to build (the spike used a Python dict) |
| D8 frozen table from payload | confirmed |
| D9 cache mirrors site layout | confirmed by numpy's `$ORIGIN` RPATH |
| D15 deterministic builds | depends on D16 |
| **D16 (new)** canonical bytecode | adopted |
| **D17 (new)** non-PIE stub | adopted |
| Fallback (memfd libpython) | not needed |
