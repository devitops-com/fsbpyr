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
   needs a link against an old-glibc sysroot (moved to Phase 6c, D25; done: the stub now needs
   GLIBC_2.25). This confirms the risk listed in the plan.
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

---

# Phase 9 — Python 3.12 and 3.13 spike

Date: 2026-10-20. Host: Debian 13 (glibc 2.41), clang/lld 22.1.8 (Debian), x86_64.
Input: the PBS release fsbpyr pins for 3.14 (`20261009`), `pgo+lto-full`, x86_64-unknown-linux-gnu:

| Python | Archive | SHA-256 (from the release's `SHA256SUMS`, checked) | Size |
|--------|---------|-----------------------------------------------------|------|
| 3.12.15 | `cpython-3.12.15+20261009-x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst` | `36a23ba5776ede9db3dfbb1b1f84c74b18939331c75e1e70760f12ad61aa96d5` | 98.4 MB |
| 3.13.16 | `cpython-3.13.16+20261009-x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst` | `b4fd36646765e6b8387ace2fd5a152d10028a84deb0d1f173f7299429129d202` | 109.5 MB |

Downloaded with curl and unpacked with `zstd -dc | tar -x` into a scratch directory; no Python
was installed with uv. The trial link is `spikes/p9_versions/trial_link.py`, which calls the
build's own `link_inputs` and `build_stub`.

Result: **PASS with one addition.** The existing launcher links against both distributions
and starts, provided `build/lib/libzstd.a` is added to the link inputs. Nothing else in the
link differs from 3.14.

1. **Object format.** `PYTHON.json` `build_info.object_file_format` is `llvm-bitcode:22.1.3`
   for both, so `PbsDist.llvm_major` gives 22 and the clang/lld 22 already needed for 3.14
   links them. (`llvm-bcanalyzer` on `config.o`: `LLVM22.1.3`.) `crt_features` is
   `glibc-dynamic`, `glibc-max-symbol-version:2.17`, as for 3.14. Bytecode magic: 3.12
   `cb0d0d0a`, 3.13 `f30d0d0a` (3.14: `2b0e0d0a`).
2. **Link inputs** (from `PYTHON.json`, via `link_inputs`):
   - `libpython3.12.a` (64.1 MB, 127 core objects) and `libpython3.13.a` (70.0 MB, 149),
     `core.static_lib` under `install/lib/python3.X/config-3.X-x86_64-linux-gnu/`
   - static `bz2 ffi ncursesw panelw mpdec crypto lzma sqlite3 ssl uuid z expat edit` (13
     libraries, the 3.14 list without `zstd`)
   - system `pthread dl util m rt`
   - tk, X11 and libdb are not needed, as for 3.14
3. **Inittab.** `build/Modules/config.c` (object `config.o`, in `libpython`), cflags
   `-std=c99 -DNDEBUG -DPy_BUILD_CORE`; the same layout as 3.14, so `builtin_modules()`
   works unchanged.
4. **Built-in extensions.** 92 in each (3.14: 96). Against 3.14:
   - 3.13 lacks `_hmac`, `_remote_debugging`, `_types` and `_zstd` (3.14 additions)
   - 3.12 lacks the same and has, besides, the modules 3.13 removed: it has `audioop`,
     `ossaudiodev`, `spwd`, `_xxinterpchannels` and `_xxsubinterpreters`; it lacks
     `_interpchannels`, `_interpqueues`, `_interpreters`, `_suggestions` and `_sysconfig`
   - no built-in module of either links `zstd`, because `_zstd` is 3.14's
5. **Objects missing from `libpython.a`.** Only `build/Modules/_hacl/Hacl_Hash_SHA2.o` (for
   `_sha2`); 3.14 needs 11 HACL\* objects. `_hacl/` holds `Hacl_Hash_MD5/SHA1/SHA2/SHA3.o`,
   of which MD5, SHA1 and SHA3 are archive members; `_blake2` has its own sources in 3.12 and
   3.13. `link_inputs` finds it by itself.
6. **Missing static libraries.** `libzstd.a` is the one the launcher needs and no built-in
   module asks for: the launcher decodes zstd (`payload.c`, `vendor/zstd/zstd.h`, 1.5.7).
   The file exists, `build/lib/libzstd.a` (34 members, plain ELF objects, `1.5.7`, the same
   version as the vendored header), but no `links` entry names it, so `link_inputs` omits
   it. Linking the launcher as is fails with `undefined symbol: ZSTD_isError`,
   `ZSTD_DCtx_reset`, `ZSTD_decompress` and `ZSTD_decompressStream` (both versions).
   Adding `build/lib/libzstd.a` to the static libraries fixes it. All the other libraries
   `link_inputs` returns are present. Phase 9 step 2 also moves zstd *compression* into
   `_fsbpyr`; the archive defines `ZSTD_compress` and `ZSTD_compressStream`, so it
   would serve, which this spike did not test (the plan's vendored libzstd source is the alternative).
7. **Trial link** (host glibc, no sysroot, current flags: LTO, PIE, `-s`):

   | Python | Result | Stub size | Link time | Notes |
   |--------|--------|-----------|-----------|-------|
   | 3.12.15 | OK with `libzstd.a`, FAIL without | 20.99 MB | 66 s | no warnings; needs GLIBC_2.38 |
   | 3.13.16 | OK with `libzstd.a`, FAIL without | 20.25 MB | 80 s | no warnings; needs GLIBC_2.38 |

   (Times with two links running in parallel.) The stack-frame check (D38) printed nothing.
   Each stub starts and stops at its integrity check ("the file was modified or damaged",
   exit 70), as a stub without a payload does. **Not tested**: running an app on either
   (the boot module and the build run on 3.14 only), the glibc 2.28 sysroot link (the
   sysroot is Python-independent), `numpy` or `orjson` on 3.12/3.13.
8. **`PyConfig` fields.** Every field `src/fsbpyr/launcher/main.c` sets exists in
   `cpython/initconfig.h` of both: `install_signal_handlers`, `parse_argv`, `site_import`,
   `safe_path`, `write_bytecode`, `pathconfig_warnings`, `module_search_paths_set`,
   `import_time`, `executable`, `run_command`, `home`, `prefix`, `exec_prefix`,
   `base_prefix`, `base_exec_prefix` and `stdlib_dir`, as do `PyConfig_InitIsolatedConfig`,
   `PyConfig_SetBytesString` and `PyConfig_SetBytesArgv`. The launcher compiled unchanged
   against both sets of headers. That the *runtime behaviour* matches 3.14 (frozen-table
   precedence, `__path__` of frozen packages, the getpath probes of S2) is not tested here.

Consequence for the plan: step 3's runtime build needs `libzstd.a` added to the link inputs
for 3.12 and 3.13 (for 3.14 `_zstd` already pulls it in); the rest of the link, the HACL
objects included, follows from `PYTHON.json` unchanged.

---

## SEC-4 — clang/lld 23 for PBS 20261013 and later

Date: 2026-10-20. Host: Debian 13, x86-64. Input: PBS `20261013`,
`cpython-3.14.8+20261013-x86_64-unknown-linux-gnu-pgo+lto-full` (SHA-256
`6ea47477c59736c51914214a1608cc4fceb37a238e5b0ba6bb351bb665d6aca6`, from the release's
`SHA256SUMS`). `src/fsbpyr/runtime/pins.py` was edited to this release for the spike only and
restored; it stays on 20261009 (SEC-2).

1. **Source tried: LLVM's release tarball (candidate (c)).**
   `LLVM-23.1.3-Linux-X64.tar.zst`, 1,188,398,549 bytes, SHA-256
   `14d2f701eb68fb799001bdea6231048f3990690fa2f406d563555ff8f744daba`. The value matches the
   digest GitHub lists for the asset and the `sha256sum` of the file downloaded at
   `https://github.com/llvm/llvm-project/releases/download/llvmorg-23.1.3/LLVM-23.1.3-Linux-X64.tar.zst`
   (about 7 minutes here). Candidate (b), sid's clang-23 from snapshot.debian.org in a sid image,
   was **not tried**.
2. **Unpacking.**
   - `tar --zstd -xf` fails: "Window size larger than maximum: 1073741824 > 134217728. Use
     --long=30". `zstd -dc --long=30 FILE | tar -xf -` works (6 s).
   - The whole tree is 12 GB. Linking needs only `bin/clang`, `bin/clang-23`, `bin/lld`,
     `bin/ld.lld` and `lib/clang` (clang's resource directory): 575 MB. The binaries have no
     missing shared libraries on trixie (`ldd bin/clang`).
   - `clang --version`: `clang version 23.1.3 (... 0d261d1ca552c95a8f007e061c787ac7132fbcbc)`;
     `ld.lld --version`: `LLD 23.1.3`.
3. **Runtime build.** `fsbpyr runtime build --clang <unpacked>/bin/clang` on PBS 20261013:
   "runtime cp314-x86_64-unknown-linux-gnu-817e3649db9b ready", 1 min 33 s, with an empty
   cache (PBS and sysroot fetched).
4. **Three stages.** With the pin on 20261013, `FSBPYR_LLVM23=1 bash tools/bootstrap.sh
   --from-source` (the new switch, see below) built stages 1-3; each `inspect --verify`
   passed, and **stage 2 == stage 3**, sha256
   `33c220671b14524ad9998e1926886259fd0e8e35ea19eb23847086fe159dac8e`. `strings` on
   `dist/fsbpyr` shows `clang version 23.1.3` and `Linker: LLD 23.1.3` in the stub's
   `.comment`. 1 min 35 s in all with the runtime uncached.
5. **Launcher reproducibility.** With the unpacked `bin/` first on `PATH` (so that
   `find_toolchain(23)` takes it) and the pin on 20261013,
   `pytest tests/e2e/test_launcher_build.py::test_the_launcher_links_reproducibly` passed
   (213 s).
6. **What is in the repository.**
   - `tools/release/llvm23`: the version and the tarball's sha256.
   - `tools/bootstrap.sh`: `FSBPYR_LLVM23=1` makes `--from-source` fetch the tarball (into
     `${XDG_CACHE_HOME:-~/.cache}/fsbpyr/toolchains/llvm-<version>-<sha12>`, once), fail on a
     sha256 mismatch, unpack what linking needs and pass its clang to `fsbpyr runtime build
     --clang`. `--llvm23` does only the fetch and prints the clang. Without the switch the
     runtime is linked with the clang on `PATH`, as before (clang 22 on trixie). Needs `curl`
     and `zstd`, which the Containerfile's `build` stage and the CI test setup now install.
   - Tests in `tests/integration/test_release.py` use a small tarball laid out like LLVM's:
     only what linking needs is unpacked, the second run fetches nothing, a wrong sha256 fails
     and keeps nothing, and the call to `runtime build` has `--clang` only with the switch.
7. **How the release container uses it (to be done at the switch; not in release.yml yet).**
   - `make bootstrap` step: `env: FSBPYR_LLVM23: 1`.
   - Test steps: `echo "$(dirname "$(bash tools/bootstrap.sh --llvm23)")" >> "$GITHUB_PATH"`, so
     that the tests' own `fsbpyr runtime build` finds clang 23 first.
   - The same two lines in the Containerfile's `test` stage run (`make test-container`).
   - Together with moving `pins.py` to the newest PBS, when upstream-watch reports the first
     security-relevant release after 20261009 or trixie gets clang-23.
8. **Not checked here.** `make test-container` with the switch on and the pin moved (the run
   in step 4-5 used the host, not the container); a release.yml run; candidate (b).

