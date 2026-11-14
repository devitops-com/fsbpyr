# Launcher

The launcher is the native program at the start of every executable fsbpyr v@VERSION@ builds:
its start, how it is built, and each C source file in [`src/fsbpyr/launcher/`][dir]. The bytes it
reads are in [Executable format](Dev-Executable-Format.md), the Python code it starts in
[Boot modules](Dev-Boot-Modules.md).

## What the launcher is

The stub of every executable ([Layout](Dev-Executable-Format.md#layout)) is an ELF program
linked from the launcher's C sources, the static `libpython` of a python-build-standalone (PBS)
distribution and the static libraries of CPython's built-in modules (OpenSSL, liblzma, libzstd
and others).

`fsbpyr runtime build` (`build_runtime` in [`runtime/registry.py`][registry]) builds the stub
once per runtime: one Python on one target for one glibc baseline, 2.34 by default or 2.28 for
EL8 hosts (`--glibc 2.28`). Every app built for a runtime gets the same stub, so the same
`stub_sha256` (`stub_digest` in [`assemble.py`](../src/fsbpyr/assemble.py)); a released fsbpyr
gives the default baseline's apps its own stub (`Runtime.from_executable`;
[its size](Dev-Executable-Format.md#what-executables-share)). [`runtime/stub.py`][stub] reads the C
sources, package data of `fsbpyr.launcher`, through `importlib.resources`, so an fsbpyr
executable can build a runtime from its own payload.

## From `execve` to the boot code

![The launcher's startup: thirteen steps from opening /proc/self/exe to Py_RunMain, each with its failure exit](images/launcher-startup.svg)

`main()` in [`main.c`][main.c#L131] runs these steps in order; steps 1 to 4 are
`fsb_payload_open` in [`payload.c`][payload.c].

1. **Locate itself.** It reads the link `/proc/self/exe` for its path, opens `/proc/self/exe`
   read-only with `O_CLOEXEC`, and `fstat`s it. A path reported as `"<path> (deleted)"` (the
   file was replaced or removed while it runs) is cut back to `<path>`, unless a file of that
   name is this one (same device and inode).
2. **Check the integrity digest** of the whole file (`fsb_check_integrity`,
   [`integrity.c`][integrity.c]). No byte of the file is believed before this passes; the
   launcher leaves some validation rules to the digest
   ([who checks what](Dev-Executable-Format.md#who-checks-what)).
3. **Read the trailer**, the last 64 bytes, with `pread`, and check it: magic, format version 7,
   flags 0, `payload_offset` a multiple of 65536, `payload_offset + payload_size + 64` equal to
   the file size, and a payload at least as large as its header.
4. **Map the payload** (`mmap`, read-only, `MAP_PRIVATE`, at `payload_offset`) and check the
   header and section table: magic, version, `header_size` 208, every section inside the
   payload, after the header and 8-byte aligned. No table is scanned or copied at startup
   ([`payload.h`][payload.h]). The descriptor stays open (`O_CLOEXEC`), its device and inode
   recorded, for reading large raw blobs.
5. **Refuse a privileged start.** If the kernel set `AT_SECURE` (set-uid, set-gid or file
   capabilities), exit 70: the cache location would come from the caller's environment.
6. **Check the Python version.** The payload's bytecode magic must equal the one compiled in
   (`FSBPYR_BYTECODE_MAGIC`), and `python_hexversion` must have the major and minor version of
   the CPython compiled against (`PY_VERSION_HEX`).
7. **Hold an instance slot** (`fsb_hold_slot`, [`slots.c`][slots.c]): a start over the instance
   limit exits here, before CPython starts and before the collection's fork.
8. **Start a cache collection if one is due** (`fsb_collect_when_due`, [`due.c`][due.c]): a
   detached process executes the app again with `FSBPYR_GC=background`; the start goes on.
9. **Build the frozen-module table** from the STARTUP section: each listed module must be flagged
   STARTUP, its code a zstd frame or a block member (both checksummed) of 1 byte to 16 MiB,
   decompressed into memory, so `marshal` never sees bytes that failed a checksum. The table,
   at most 4096 modules, becomes `PyImport_FrozenModules`.
10. **Register `_fsbpyr`** ([`module.c`][module.c]) as a built-in module
    (`PyImport_AppendInittab`).
11. **Configure CPython** from its isolated pre-configuration and configuration
    (`PyPreConfig_InitIsolatedConfig`, `PyConfig_InitIsolatedConfig`), with the fields below.
12. **Initialise CPython** with `Py_InitializeFromConfig`.
13. **Run the boot code**, the STARTUP module `_fsbpyr_boot` (`run_command`), which runs the
    app ([Boot modules](Dev-Boot-Modules.md)); `main()` returns what `Py_RunMain()` returns, so
    exit codes, `SystemExit` and `SIGINT` behave as in CPython.

The fields step 11 sets ([`main.c`][main.c]):

| Field | Value | Why, where the code says |
|---|---|---|
| `preconfig.utf8_mode` | 1 | |
| `install_signal_handlers` | 1 | as `python` has them: SIGINT raises `KeyboardInterrupt`; SIGPIPE and SIGXFSZ are ignored, so writing to a closed pipe or socket raises `BrokenPipeError` and past the file-size limit fails with `EFBIG`, not ending the process |
| `parse_argv` | 0 | |
| `site_import` | 0 | |
| `safe_path` | 1 | |
| `write_bytecode` | 0 | |
| `pathconfig_warnings` | 0 | |
| `module_search_paths_set` | 1, with no paths | `sys.path` is empty until the boot module installs its hook |
| `import_time` | 1 if `FSBPYR_IMPORTTIME` is set | |
| `buffered_stdio` | 0 if `PYTHONUNBUFFERED` is set and not empty, else as the isolated configuration has it | "the one PYTHON* variable honoured, as CPython reads it: set and non-empty (D14)" |
| `argv` | the process's `argc` and `argv` (`PyConfig_SetBytesArgv`) | the boot code may replace `sys.argv[0]` with the path `execve` was given ([module.c](#modulec)) |
| `executable` | the path from step 1 | |
| `home`, `prefix`, `exec_prefix`, `base_prefix`, `base_exec_prefix` | the executable's directory | "so getpath has nothing to search" |
| `stdlib_dir` | the executable's path | the root of the payload's virtual tree, so frozen modules get `__file__` and `__path__` inside it (`encodings.__path__ = [<exe>/encodings]`) |
| `run_command` | `import _fsbpyr_boot\n_fsbpyr_boot.run()\n` | the boot code |

So the interpreter never searches the file system for the standard library
([what this means for apps](Dev-Compatibility.md#how-an-app-differs-from-a-venv)).

### Exit statuses

| Status | Message on stderr | When |
|---|---|---|
| 70 | `fsbpyr: cannot start <path>: <reason>` | any of steps 1 to 6 and 9 to 11 fails (`fatal()` in `main.c`); `<path>` is the executable's path, or `executable` if it could not be read |
| 70 | `fsbpyr: refusing to start: <N> copies of this app are running already, as many as this user may run at once (does the app keep starting itself?); if it needs more, set FSBPYR_MAX_INSTANCES to a higher number` | step 7: every slot is taken |
| 0 | none | step 7 in a background collection (`FSBPYR_GC=background`): every slot is taken, and the collection waits for another day |
| 70 | `fsbpyr: cannot start <path>: refusing cache directory <dir>: owned by another user`, or `...: other users may write to it` | after step 13: a directory on the way to the native cache, or in it, is another user's or writable by others ([module.c](#modulec)) |
| CPython's | CPython's | a `PyStatus` error from `Py_PreInitialize`, a `PyConfig_Set*` call or `Py_InitializeFromConfig` (`Py_ExitStatusException`) |
| 99 | the sanitizer's report | only in the sanitized test variant: an ASan or UBSan finding (`__asan_default_options`, `__ubsan_default_options` in `main.c`); neither Python (1) nor the launcher (70) uses it |
| any other | | what `Py_RunMain()` returns: the boot code's and the app's exit status |

The `<reason>` texts, in the order the steps can produce them:

| Step | `<reason>` |
|---|---|
| 1 | `cannot resolve own executable path (an executable needs /proc)`; `cannot read own executable (an executable needs /proc)` |
| 2 | `integrity check failed: bad size` (32 bytes or fewer); `out of memory`; `cannot read own executable`; `integrity check failed: the file was modified or damaged` |
| 3 | `cannot read own executable (an executable needs /proc)`; `no fsbpyr payload`; `unsupported payload format version`; `unknown trailer flags`; `payload offset not 64 KiB aligned`; `payload size does not match file size`; `payload truncated` |
| 4 | `cannot map payload`; `bad payload header`; `unsupported payload format version`; `bad payload header size`; `payload section out of bounds` |
| 5 | `refusing a privileged start (set-uid, set-gid or file capabilities): the cache location comes from the caller's environment` |
| 6 | `payload bytecode was compiled for a different Python version`; `payload targets a different Python version` |
| 9 | `too many startup modules`; `out of memory`; `invalid startup module`; `corrupt startup module` |
| 10 | `cannot register _fsbpyr` |
| 11 | `executable path is not absolute` |

A file that is not an fsbpyr executable, or one cut short, fails at step 2 with
`integrity check failed`, not with `no fsbpyr payload`.

## How it is compiled and linked

[`runtime/stub.py`][stub] compiles and links the launcher in one `clang` command, run by
`build_stub`, which `build_runtime` calls.

- **Toolchain.** `find_toolchain` ([`runtime/toolchain.py`][toolchain],
  [Runtime modules](Dev-Runtime-Modules.md#toolchain)) takes a `clang` at least as new as the
  LLVM of PBS's bitcode objects (`PbsDist.llvm_major`, from `PYTHON.json`), with its `ld.lld`;
  `--clang` names one.
- **Sysroot.** `--sysroot` is the pinned glibc sysroot of the target and baseline
  (`pins.sysroot_for`, [`runtime/pins.py`][pins]), which `fetch_sysroot`
  ([`runtime/sysroot.py`][sysroot]) downloads once, checks against its pinned SHA-256 and
  extracts into the cache's `sysroots/<identity>` ([pins](Dev-Runtime-Modules.md#pins),
  [sysroot](Dev-Runtime-Modules.md#sysroot), RPMs through [`runtime/rpm.py`][rpm]): Debian 12's
  glibc 2.36, gcc 12 and Linux 6.1 headers by default, kept within 2.34 by [`glibc.c`](#glibcc);
  Rocky Linux 8.10's glibc 2.28, gcc 8.5.0 and 4.18 kernel headers for `--glibc 2.28`
  (`EL8_SYSROOTS`). Only `--sysroot` and its `-ffile-prefix-map` differ between the two.
- **Sources.** `LAUNCHER_SOURCES` are the eleven `.c` files; `LAUNCHER_FILES` adds the ten headers
  and the two vendored zstd headers. They are compiled in a temporary directory on relative
  paths, so its random name reaches neither `__FILE__` nor LTO's local symbol names.
- **Compile flags** (`COMPILE_FLAGS`):
  `-std=c11 -O2 -flto=full -fPIE -pie -D_GNU_SOURCE -fstack-protector-strong`
  `-D_FORTIFY_SOURCE=2 -Wall -Wextra -Wvla -Wframe-larger-than=8192`, plus `--target=<triple>`,
  `--sysroot`, `-ffile-prefix-map` from the PBS directory to `/fsbpyr/python` and the sysroot to
  `/fsbpyr/sysroot` (the cache's location never reaches the binary), `--ld-path=<ld.lld>`,
  `-DFSBPYR_BYTECODE_MAGIC=0x<magic>u` (`importlib.util.MAGIC_NUMBER` as a little-endian u32;
  `main.c` refuses to compile without it), `-DFSBPYR_RUNTIME_ID="<runtime id>"` (below), and
  `-I` for the PBS includes and the sources.
- **Link inputs** (`link_inputs` in `runtime/pbs.py`): the objects of built-in extension modules
  the archive lacks; inside `-Wl,--start-group`/`--end-group`, PBS's static `libpython`, the
  static libraries those modules link (`path_static` in `PYTHON.json`) and `build/lib/libzstd.a`
  whenever it exists, since the launcher decodes zstd itself (3.12 and 3.13 have no `_zstd`
  module to name it), its `ZSTD_versionString()` read from `zstd_common.o` (`zstd_version`) into
  the runtime's components; PBS's system libraries as `-l` options; on aarch64, clang's
  compiler-rt builtins for the outlined atomics PBS's objects call (`outlined_atomics`).
- **Link flags** (`LINK_FLAGS`): `-Wl,--export-dynamic-symbol=Py*` and `=_Py*` (only the Python
  C API is exported, so libraries of wheels cannot bind to the static zlib, OpenSSL or libffi in
  the stub), `-Wl,-z,noexecstack -Wl,-z,relro -Wl,-z,now`, `-s`, `-static-libgcc`, and
  `-Wl,--wrap=hypot -Wl,--wrap=_dl_find_object` for `glibc.c`; on aarch64 also
  `-Wl,--fix-cortex-a53-843419` (`AARCH64_LINK_FLAGS`), lld's patch for an erratum of the
  Cortex-A53 that PBS's own links have but its bitcode does not carry
  ([Runtime modules](Dev-Runtime-Modules.md#stub)).
- **Failures.** A failed link raises `StubBuildError` with at most the output's last 4000
  characters, from a line boundary (`tail_lines` in [`text.py`][text]); any output line about a
  `stack frame size` (LTO sizes frames again at the link, where only a warning says so) raises it
  with those lines. Other warnings are printed.
- **Identity.** `launcher_digest` is the SHA-256 over every file of `LAUNCHER_FILES` (name and
  content) and the compile and link flags for the target triple; `runtime_id` in `registry.py`
  hashes it with the PBS pin, the sysroot (its pin's name and a digest of its packages'
  SHA-256) and the layout version into the runtime's id, `cp<python>-<triple>-<12 hex digits>`.
  A changed source or flag gives a new runtime, and the two baselines never share an id.
- **The recorded id.** `build_stub` compiles that id in as `-DFSBPYR_RUNTIME_ID="<id>"`, outside
  the hashed flags since the id is what it carries. `main.c` keeps it as
  `fsbpyr-stub-identity=<id>`, marked `used` so that LTO and stripping keep it (`none` without
  the define, as in test programs); nothing reads it at run time. `stub_identity` in `stub.py`
  reads it out of a file (up to the next NUL, or `None`), and a build refuses a stub it did not
  link unless it records the runtime id the build computes, which several releases may share.
- **The glibc 2.28 stub of a release.** A released fsbpyr carries the default runtime only. For
  `--glibc 2.28` it pairs its own standard library, which does not depend on the sysroot, with
  the asset `fsbpyr-stub-3.X-<target>-glibc2.28` (`paired` in [`runtime/stubs.py`][stubs]):
  placed beside the fsbpyr executable, kept in its cache, or downloaded from the companion
  release `el8-vX.Y.Z`, and refused unless it records this fsbpyr's runtime id (`_checked`;
  [The glibc 2.28 stub](Dev-Security.md#the-glibc-228-stub)).
- **Sanitized variant**, for tests: `sanitize=True` drops `-O2`, `-flto=full`,
  `-D_FORTIFY_SOURCE=2` and `-s`, and adds `-O1 -fsanitize=address,undefined
  -fno-sanitize-recover=undefined -fno-omit-frame-pointer -g`; its runtime id ends in
  `-sanitized`.
- **Static analysis.** `make check-c` ([`tools/check-c.py`][check-c]) runs `clang --analyze` over
  each source with `COMPILE_FLAGS` minus `-O2 -flto=full -fPIE -pie`, against the PBS
  distribution of the Python running it and the host's target, and exits 1 on any finding or
  failed compile, which fails `full-suite.yml`'s run.

Compile-time guards: `format.h` stops on a big-endian target, `module.c` on a free-threaded
CPython (`Py_GIL_DISABLED`: `read()` shares the process's zstd decoder, which the GIL
serializes), `main.c` without `FSBPYR_BYTECODE_MAGIC`, and [`glibc.c`](#glibcc) on an
architecture other than x86-64 and aarch64.

## Source files

![Which launcher source file calls which, and which headers it includes; purple boxes are the libraries linked in](images/launcher-files.svg)

Arrows follow the `#include` lines and `fsb_*` calls; `payload.c/.h` is a file and its header.

### main.c

[`main.c`][main.c]: `main()` runs the startup sequence; `fatal(what)` prints
`fsbpyr: cannot start <path>: <what>` and exits 70 (`EXIT_CORRUPT`, `EX_SOFTWARE`);
`check_versions()` is step 6; `startup_table()` and `startup_code()` are step 9 (a module whose
code cannot be read is `out of memory` when `fsb_read_blob` failed with `ENOMEM`, else
`corrupt startup module`; the table
`calloc`ed with a zeroed end entry, names pointing into the mapped string table, code into
`malloc`ed memory, `is_package` set for PACKAGE modules; limits `MAX_STARTUP_MODULES` and
`MAX_STARTUP_CODE`); `pin_prefixes()` sets the five prefixes to the executable's directory (`/`
for one in the root directory) and `stdlib_dir` to its path; `check(status)` hands a `PyStatus`
error to `Py_ExitStatusException`. Under AddressSanitizer, `__asan_default_options()` and
`__ubsan_default_options()` give `detect_leaks=0:allocator_may_return_null=1:exitcode=99` and
`print_stacktrace=1:exitcode=99`: CPython frees little at exit, and a failed allocation returns
`NULL`, reported as out of memory. It defines `fsb_self`, the running executable's
`struct fsb_payload`, and `identity` ([the recorded id](#how-it-is-compiled-and-linked)).

### payload.c and payload.h

[`payload.c`][payload.c] maps and validates the payload and gives read-only, bounds-checked
access to it. [`payload.h`][payload.h] declares the API and `struct fsb_payload`: the mapping
(`base`, `size`, and `mapped`, false for an image in memory in tests), the payload's offset, the
open descriptor with device and inode, a copy of the header, the string table's place,
`data_start` (after the last section), and the executable's path (4096 bytes).
`fsb_payload_open` ([line 108][payload.c#L108]) runs steps 1 to 4; `fsb_payload_parse` runs
steps 3 and 4 on an image in memory, for the fuzz harness and edge-case tests.

**Records.** `fsb_get_blob`, `_module`, `_file`, `_dist`, `_startup`, `fsb_get_word` (CLOSURES)
and `fsb_get_digest` copy record *i* out with `memcpy` after a bounds check, never casting
pointers into the mapping ([`format.h`][format.h]'s comment), so alignment does not matter.
They return false for an index out of range or a failed check: a module's `kind` 1 to 4; a
file's `kind` 1 to 5 and `mode` at most `0777`; codec 2: a block index below the BLOBS count
and within u32, `stored_size + size` at most 1 MiB; codecs 4 and 5: `size` above one piece
(4 MiB for xz, 8 MiB for zstd) and at most 2^62, `stored_size` the number of pieces `size`
needs, the pieces inside BLOBS; codecs 0, 1 and 3: 16-byte aligned, at or after `data_start`,
inside the payload, `size` at most 2^62, codec 0 with `stored_size == size`, codec 3 with `size`
at most 4 MiB; no other codec. `fsb_str` returns a string of the string table if it lies inside
it and ends in a NUL. `fsb_record_key`, `fsb_lower_bound` (a binary search in the format's key
order) and `fsb_find_module`, `_file` and `_dist` (an exact match's index or `FSB_NONE`) look
records up; nothing is scanned.

**Reading blobs.** `fsb_read_blob(payload, decoder, blob, dst)` writes a blob's content into
`dst[size]`:

| Codec | How |
|---|---|
| 0, raw | copied from the mapping; one of 1 MiB or more is read from the file instead (below) |
| 1, zstd | `fsb_zstd_frame_ok` first: exactly one frame, at least 6 bytes, with the zstd magic, the content-checksum flag and a declared content size of `size`; then decompressed with the decoder's context to exactly `size` bytes |
| 2, block | the block (a codec 1 blob of at most 1 MiB) is decompressed into one of the decoder's block slots, or found there, and the member's bytes are copied out |
| 3, xz | `lzma_stream_buffer_decode` from the static liblzma, with a 64 MiB memory limit: exactly one stream that consumes all `stored_size` bytes and gives exactly `size` |
| 4, 5, pieces | piece *i* is blob `offset + i`, of the pieces' codec (3 or 1) and the expected size, decoded to its place in parallel through `fsb_parallel` (one item per piece, up to 8 threads) |

A blob larger than the stream window (64 MiB), or a raw one of 1 MiB or more read from a file,
goes through a stream. On failure `errno` is 0 for corrupt data, `ENOMEM` when memory ran out, or
the read error. The decoders say which: a decoder, zstd context or block slot that cannot be
allocated, zstd's `memory_allocation` error and liblzma's `LZMA_MEM_ERROR` are out of memory,
every other failure is corrupt data. Pieces decoded on helper threads, whose `errno` never reaches the
caller, record a failure in their job: corrupt if any piece is, else out of memory if any piece
was.

**Decoders.** A `struct fsb_decoder` holds a zstd context, made on first use, and 1 MiB block
slots reused least recently used first, so a block a package's modules share is decompressed
once while it stays among the last few. `NULL` names the process's decoder (32 slots) for callers
that hold the GIL or run before Python; a cache sync, without the GIL, makes its own with 4.
Nothing is shared, so nothing is locked (`payload.h`: a lock taken while another thread forks
would stay taken in the child).

**Streams.** `fsb_stream_open`, `_next`, `_seek`, `_position`, `_size`, `_error` and `_close`
give a blob's content in windows of at most `FSB_STREAM_WINDOW` (64 MiB; smaller in tests), each
but the last whole 1 MiB leaves, to be hashed as they come. A zstd frame is checked to its end,
checksum included, before the stream ends, and a seek backwards restarts it; xz and block blobs are
decoded whole, pieces a window at a time in parallel, a large raw blob read with `pread` from
the stream's own descriptor, a window's leaves on up to 8 threads (`fsb_parallel`, 4 a thread). `fsb_stream_next` returns `FSB_STREAM_DATA`, `_END`, `_CORRUPT`,
`_MEMORY` (the decoders' failures as above, and a frame's window zstd cannot allocate) or
`_SYSTEM`; after a failure every later call fails the same way.

**Raw blobs and holes.** The writer leaves whole leaves of zeros of a raw blob unwritten, so one
of 1 MiB or more may hold holes. It is read from the file, never through the mapping, where a
hole on tmpfs would take a page of memory for every page read and could end the process with
`SIGBUS`, through a duplicate of the kept descriptor if that still is the executable (same device
and inode; the app may have closed it), else `/proc/self/exe` opened again and checked the same
way, else `ESTALE`. A leaf inside a hole of the executable (`fsb_in_hole`, `SEEK_DATA`) is not
read, since reading a hole fills page-cache pages with zeros: the buffer gets zeros, and
`fsb_hole_bytes` counts them.

**Memory.** Pages read through the mapping count as resident though they are page cache, so every
256 MiB read through it the data area's pages are dropped (`madvise(MADV_DONTNEED)`); a failed
`madvise` (it fails in an app that locked its memory) leaves `errno` as the read had it.
`fsb_stored_bytes`, `fsb_decompressed_bytes` and `fsb_hole_bytes`, for `_fsbpyr.stats()`, count
what was read, decompressed and found in holes.

### integrity.c and integrity.h

[`integrity.c`][integrity.c] computes the
[integrity digest](Dev-Executable-Format.md#integrity-digest), one routine for the launcher's
own file and for every file it extracts to the cache.

- `fsb_tree_digests(count, data, fds, lengths, digests)` hashes several buffers, in memory or
  open as descriptors (a `NULL` data entry): leaves in passes of up to 4096 (`FSB_PASS_LEAVES`:
  4 GiB of data, 128 KiB of leaf digests) on `fsb_parallel`, one thread per 4 leaves
  (`LEAVES_PER_THREAD`), folded into each root before the next pass, so memory does not grow
  with file size. A file is read with `pread` into a 64 KiB buffer per thread, never mapped, so
  its size never shows in resident memory; a leaf inside a hole is not read. A whole leaf of
  zeros, a hole's or one in memory, takes the digest of zeros (`ZERO_LEAF`) unhashed. A leaf that
  cannot be read whole (the file shrank or failed to read) gets a digest of zeros, which cannot
  match, and the call returns `EIO`; `ENOMEM` if out of memory; else 0.
- `fsb_in_hole(fd, start, size)` says whether a range lies inside a hole (`SEEK_DATA`); past the
  file's end there is none, so a file that shrank is read, and fails. `fsb_zeros` says whether
  bytes are all zeros.
- `fsb_tree_digest` and `fsb_file_digest` are the one-buffer and one-file forms;
  `fsb_tree_init`, `_add` and `_final` hash content that comes a window of whole leaves at a
  time, as a cache sync writes it; `fsb_tree_add_each` also hands each leaf, and whether it is
  zeros, to a function on the thread that hashed it; `fsb_sha256_file` and `fsb_sha256_bytes` give the plain
  SHA-256 for `sig.c`.
- `fsb_check_integrity(fd, size)` ([line 257][integrity.c#L257]) returns `NULL` if the last 32
  bytes are the digest of the bytes before them, else a static message (the step 2 reasons), and
  stores its time in `fsb_integrity_ns` (`_fsbpyr.stats()`, printed by `FSBPYR_TRACE`).

SHA-256 is OpenSSL's low-level `SHA256_Init`/`_Update`/`_Final` (SHA-NI where the CPU has it),
linked statically for `_hashlib` and declared in the file, as PBS ships no OpenSSL headers, with
a 256-byte union as context; the one-shot `SHA256()` goes through EVP since OpenSSL 3, which may
read `openssl.cnf` (`$OPENSSL_CONF`) before the check is done.

### parallel.c and parallel.h

[`parallel.c`][parallel.c] runs all parallel work: hashing, decoding pieces, writing cache files.
`fsb_parallel(count, per_thread, work, context)` calls `work(context, item, worker)` for every
item in `[0, count)` and returns when all are done. The caller, worker 0, starts up to 7 helpers
(`FSB_PARALLEL_MAX_THREADS` is 8), no more threads than the cores the process may run on
(`sched_getaffinity`; on a kernel of more than 1024 CPUs, which refuses a `cpu_set_t`'s mask,
asked again with a mask twice as large until one is) or than one per `per_thread` items;
threads take items from an atomic counter, and the running ones do the work of a helper that
cannot start. A call from inside such work runs on its own thread alone (a thread-local flag), so
nesting never makes more than 8 threads. After its share the caller spins on non-blocking joins
for up to 2 ms, yielding now and then, before it sleeps in `pthread_join`: on a hybrid CPU a
thread woken from a join may land on an efficiency core. `fsb_monotonic_ns` reads
`CLOCK_MONOTONIC`.

### slots.c and slots.h

[`slots.c`][slots.c] enforces the instance limit: every start holds a slot while it runs, so an
app that keeps starting itself, however it does so, stops at the limit.

- `fsb_instance_limit()` is `$FSBPYR_MAX_INSTANCES` if that is 1 to 7 decimal digits and nothing
  else (clamped to 4096; 0 turns the guard off), else 4 per CPU the process may run on plus 16,
  at least 64 and at most 1024.
- `fsb_hold_slot(dev, ino, dirs, count)` ([line 86][slots.c#L86]) opens `<dir>/fsbpyr-<euid>`
  in the first of `dirs` (`/dev/shm`, then `$XDG_RUNTIME_DIR`; a `NULL` or relative entry is
  skipped) whose file it can trust: opened with
  `O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_NOCTTY | O_CLOEXEC`, mode 0600, a regular file
  of the effective user with no group or other permission bits, moved above descriptor 2 so
  Python never writes the app's output there when stdout was closed. It takes an
  open-file-description write lock (`F_OFD_SETLK`) on the first free byte of the executable's
  range: `limit` bytes from an offset derived from its device and inode (a splitmix64
  finalizer, modulo 2^40, times 4096). The kernel frees the slot when the process ends, by
  `SIGKILL` too; a forked child shares its parent's slot. With every slot taken it prints the
  refusal ([exit statuses](#exit-statuses)) and exits 70, or 0 under `FSBPYR_GC=background`.
- It never refuses for its own sake: no file it can trust, or a lock error other than `EAGAIN`
  and `EACCES` (no OFD locks there), leaves the start unguarded. The limit is no security
  boundary (D45 in [`docs/security.md`][security-md]; [Known limits](Dev-Security.md#known-limits)).

### due.c and due.h

[`due.c`][due.c] decides before Python starts whether the app's cache is due for a collection
(Python code, [Boot modules](Dev-Boot-Modules.md)) and starts one in the background.
`fsb_collect_when_due(app, length, argv0)` ([line 211][due.c#L211]) does nothing under
`AT_SECURE`, for process 1 (in a container no zombie of its child would be reaped), or with
`FSBPYR_GC` set at all. Otherwise:

1. `cache_root` finds the root by the boot code's rule
   ([Native code and the cache](Dev-Boot-Modules.md#native-code-and-the-cache)):
   `$FSBPYR_CACHE_DIR` (made absolute and normalized, as `os.path.abspath` would), else an
   absolute `$XDG_CACHE_HOME/<app>`, else `<home>/.cache/<app>` with `home()` an absolute
   `$HOME` or else the passwd entry's (`getpwuid_r(getuid())`), else nothing. An app name that
   is empty, `.`, `..`, or holds `/` or a NUL is refused.
2. `due` makes one `lstat` of the stamp `<root>/.gc`, dated at the last collection's start: due
   if it is missing, a day old, dated ahead, or an hour old with less than 10% of the file
   system's blocks available (`statvfs`, made only then). A root without the marker
   `.fsbpyr-cache`, a regular file of the effective user, is never collected.
3. `trusted` requires `fsb_way_check` ([way.c](#wayc-and-wayh)) to find the root's way trusted.
4. `claim` opens the stamp (`O_RDWR | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK | O_NOCTTY`, created
   0600 with `O_EXCL` if missing), takes `flock(LOCK_EX | LOCK_NB)`, requires a regular file of
   the effective user still dated as `due` saw it, and touches it: one start of many claims a
   collection, none while one holds the stamp, and a stamp that cannot be touched (a read-only
   cache) starts nothing.
5. `start_collection` forks and waits for the child, which calls `setsid`, forks again and
   exits. The grandchild sets nice 19 and the idle I/O class, points descriptors 0 to 2 at
   `/dev/null`, closes the rest (`close_range`, else by `/proc/self/fd`, else one by one up to
   the limit), changes to `/`, sets `FSBPYR_CACHE_DIR` to the root and `FSBPYR_GC=background`,
   unsets `FSBPYR_SIGNING_KEY` and `FSBPYR_SIGNING_PASSPHRASE`, and executes `/proc/self/exe`
   with `argv0` alone: a normal start, whose boot code collects.

A start costs one `stat`, plus a `statvfs` when the last collection was over an hour ago
(`due.h`).

### way.c and way.h

[`way.c`][way.c] checks that no other user can replace or redirect any part of the way from `/`
to the cache root, whose files are loaded as code. The rule, stated in [`way.h`][way.h] and
in Python as `way_problem` in `_fsbpyr_rt/gc.py`:

- every directory on the way, the root included, must be owned by root or by the effective
  user, and writable by no other user unless sticky (as `/tmp`): not by others, nor by a group
  other than the effective group;
- in a user namespace an owner outside it shows as the overflow uid (nobody); such an owner
  counts as root's when no uid of the namespace maps to the overflow uid
  (`/proc/sys/kernel/overflowuid`, `/proc/self/uid_map`), but only above the first sticky
  directory: below one, only root's and the user's count;
- symlinks must be root's or the user's; they are followed one at a time (at most 40), their
  targets' ways checked too; `..` goes up from where the walk is, as the kernel resolves it;
- the walk ends at the root, or at the first part that does not exist yet; a `..` after a
  missing part is refused, as the kernel would.

`fsb_way_check(root, where, size, error)` ([line 174][way.c#L174]) walks with `O_PATH`
descriptors from `/` (a relative root from the working directory) and returns
`FSB_WAY_TRUSTED`, `FSB_WAY_PARTIAL` (trusted up to a part that does not exist: check again once
it does), `FSB_WAY_FOREIGN` (owned by another user), `FSB_WAY_EXPOSED` (writable by other users),
`FSB_WAY_SYSTEM` (an `errno` in `*error`) or `FSB_WAY_MEMORY`; `where` gets the path at fault.
A symlink's target is read into a buffer of `PATH_MAX` (4096) bytes; one that fills it may have
been cut short and is refused with `ENAMETOOLONG`: `symlink()` makes none so long, but a FUSE or
NFS server may give one. The walk's buffers are on the heap, so it is thread-safe. `due.c` and
`cache.c` call it.

### cache.c and cache.h

[`cache.c`][cache.c] extracts payload files to the native cache and checks those a process uses
before it first uses them. [`cache.h`][cache.h] describes the layout,
`<root>/<chain>/<site-relative path>` (`<root>/runtime/<generation>/site/x.libs/libx.so`): the
chain's parent (`runtime/<generation>`) is a tree, with its `.lock` and `.scratch`, that a
collector may remove once no process uses it; the root holds the marker `.fsbpyr-cache`.

`fsb_cache_sync(payload, job)` ([line 1028][cache.c#L1028]) takes a `struct fsb_cache_sync`: the
root, the chain, the FILES indices to sync (ascending, so directories are opened once), whether
resources may be extracted, an outcome byte per file, and whether to hold the tree. It sets
`failure` (`FSB_CACHE_OK`, `_CORRUPT` with a reason and index, `_FOREIGN`, `_SYSTEM` with
`errno` and path, `_EXPOSED`, `_MEMORY`) and per file `FSB_CACHE_CHANGED` (on disk, not as the
payload has it), `FSB_CACHE_DONE` (the payload's now) and `FSB_CACHE_WRITTEN`. It is
independent of Python and thread-safe (its caller releases the GIL), stops at the first failure,
and needs memory set by no file's size nor, in `module.c`'s jobs of at most 4096 files, by the
number of files (D38). In order:

1. **Check every record before touching the disk**: the FILES record and its path; the file must
   be extractable (a native extension or library, a file flagged FS_REQUIRED, or any file when
   `resources` is set); its path must pass `fsb_cache_safe` (no empty, `.` or `..` component, no
   NUL, no backslash: it cannot leave the directory it is opened from); its blob record; its
   digest from DIGESTS, which only `resources` may do without, the payload's content then hashed
   instead.
2. **Open the root.** `fsb_way_check` must find the way trusted. A missing part is created (the
   parents 0755 as far as the umask leaves it, the root 0700) and the whole way checked again,
   catching a part someone else made meanwhile. The root may be a symlink and must be the
   effective user's.
3. **Take the tree** (with `hold`). A root gets the marker only if it holds nothing but fsbpyr's
   names (`.fsbpyr-cache`, `.gc`, `.trash`, `runtime`, `resources`, `.gc-tool`, `.trash-tool`,
   `runtimes`, `pbs`, `sysroots`, `downloads`) and is empty or holds a tree of fsbpyr's: a
   directory of the user's is never marked. The tree's `.lock` (created 0600, a regular file of
   the user's) is held `LOCK_SH`, waiting for a collector that holds it; a tree renamed away
   meanwhile is taken again, up to 8 times. A lock a day old (or dated ahead) is touched, its
   modification time being the tree's last use. A tree that cannot be held is used unheld; the
   descriptor goes back in `job->lock`.
4. **Check what is on disk.** Each file's directory below the root is opened, created 0700 where
   missing, never followed if a symlink, must be the user's, and loses any group or other bits.
   The file is opened with `O_NOFOLLOW | O_NONBLOCK | O_NOCTTY`, so a symlink is replaced, a FIFO
   never waited for, a terminal never made the controlling one. A regular file of the user's with
   the payload's size is hashed (up to 64 KiB read into a 16 MiB buffer, larger ones kept open,
   at most 64, and read a leaf at a time; up to 1024 files per pass of `fsb_tree_digests`).
   Anything else, or a wrong digest, is queued and marked CHANGED; a missing file is queued. When
   a pass's files leave no descriptor or memory (`EMFILE`, `ENFILE`, `ENOMEM`) for the next file,
   the pass is checked first and the file tried once more, else the sync fails.
5. **Write the queued files**, in path order. Files of at most 8 MiB stored raw, as one zstd
   frame or in a block go to a pool on up to 8 threads (`fsb_parallel`), in chunks of 32 files
   or 2 MiB that never split a block, each writer with its own directories and decoder; larger
   files, xz and pieces follow one at a time, their pieces decoded in parallel and their leaves
   written by the threads that hash them. The
   descriptor table is grown once first (`make_room`). The failure reported is the first in path
   order; if the pool ran out of descriptors or memory, the calling thread writes what it left.

**Writing one file** (`restore`). The content comes from a stream, a window at a time; each 1 MiB
leaf is written by the thread that hashes it (`fsb_tree_add_each`), a leaf of zeros is left a
hole, the first failed write fails the file, and a file ending in a hole is truncated to its size. Only with the payload's digest is the file made `0400`, or `0500`
where the payload's mode is executable, and put in place. A new file is written unnamed
(`O_TMPFILE`) and linked into place (`linkat` with `AT_EMPTY_PATH`, else through
`/proc/self/fd/<n>`, after which the linked name must be the file written), so a killed process
leaves nothing. One that replaces another, or cannot be linked, is written as
`.fsbpyr-<pid>-<n>` in `.scratch`, locked `LOCK_EX` meanwhile (a collector removes only what it
can lock), and renamed into place. After a link through `/proc` gives the wrong file, or the
file system refuses links there, the process writes no more files unnamed. Nothing is synced to
disk: a file a crash left incomplete fails its digest and the next process replaces it.

### module.c

[`module.c`][module.c] is `_fsbpyr`, the built-in module through which the
[boot modules](Dev-Boot-Modules.md) reach the payload (`fsb_self`). `PyInit__fsbpyr`
([line 821][module.c#L821]) creates it with the type `Stream`, the exceptions `PayloadError`
(a `ValueError`), `CacheError` (an `ImportError`: the native cache cannot be used, or the
payload's data for it is corrupt) and `CacheRefused` (a `CacheError`: the cache's directory is
refused), the constant `NONE` (`0xFFFFFFFF`), and the private `_sha256`,
`_key_new`, `_sign` and `_verify` of [`sig.c`][sig.c], the crypto of
[Signatures](Dev-Security.md#signatures).

| Function | Returns |
|---|---|
| `find_module(name)` | `(index, kind, flags, code, source, file, dist, path)` of the MODULES record, or `None` |
| `find_file(path)` | `(index, kind, flags, mode, blob, dist, closure)` of the FILES record, or `None` |
| `file_at(index)` | `(index, kind, flags, mode, blob, dist, closure, path)` |
| `closure(word)` | the FILES indices of the closure at CLOSURES word `word`: a count of at least 1 and at most the FILES count, then that many indices in range and strictly ascending |
| `blob_info(blob)` | `(size, digest)`: the content's size, and its integrity digest or `None` |
| `read(blob)` | the content as `bytes` |
| `stream(blob)` | a `Stream`: the content a window at a time |
| `list_dir(path)` | the sorted names directly below a payload directory (`''` is the root) |
| `is_dir(path)` | whether any file lives below a directory: one binary search |
| `list_modules(prefix)` | `[(name, kind)]` of the direct children of `prefix` (`"pkg."`, or `''` for the top level) |
| `files_below(path)` | a `range` of the FILES indices below a directory, from two binary searches ([FILES](Dev-Executable-Format.md#files-record-24-bytes-keyed-by-site-relative-posix-path)); `''` is every file |
| `find_dist(name)`, `dists()` | `(index, name, dist_info, version)` of one distribution by canonical name, or of all |
| `info()` | a dict: `executable`, `execfn`, `app_name`, `entrypoint`, `build_info`, `generation_id`, `python_hexversion`, `payload_size`, `modules`, `files` |
| `stats()` | a dict: `reads`, `stored_bytes`, `decompressed_bytes`, `hole_bytes`, `integrity_ns` |
| `sync(root, chain, files, checked, resources[, written])` | the number of files written to the cache (below) |

A name or path that cannot be encoded as UTF-8 cannot be in the payload: lookups return `None`,
an empty list or `False`. A record that fails its checks raises `PayloadError`
(`corrupt executable payload: <what> <index>`). [`stubs/_fsbpyr.pyi`][pyi] declares the types for
mypy (`mypy_path = "stubs"` in `pyproject.toml`), the four private functions included, so that
mypy checks `signing.crypto()`'s `Crypto` protocol against them; `tests/e2e/test_runtime_app.py`
compares its names and the keys of `info()` and `stats()` with the module's.

**`info`**: `executable` is the path from step 1, `execfn` the path `execve` was given as the
kernel passed it (`getauxval(AT_EXECFN)`, possibly relative) or `None`, both decoded as
`sys.executable` is (`PyUnicode_DecodeFSDefault`); the boot code may make `execfn`
`sys.argv[0]` ([Boot modules](Dev-Boot-Modules.md#sysargv0-and-the-restart-idiom)).
`entrypoint` is the header's string, which only the boot code parses
([Executable format](Dev-Executable-Format.md#payload-header)).

**`read`** ([line 338][module.c#L338]) checks a zstd frame's header before it allocates the
result. It releases the GIL for xz, pieces, raw blobs of 1 MiB or more and blobs over the stream
window, which use no shared decoder, not for zstd frames and block members; it raises
`MemoryError`, `OSError` if the executable could not be read, or `PayloadError` for corrupt
data. **`Stream`** has `readinto(buffer)` (0 at the end), `seek(position)` (absolute; inside the
window only the offset moves, else decoding waits for the next read), `tell()`, `close()` and
`size`; windows but those of block members decode without the GIL. After a failed window every
call fails; use on two threads at once raises `RuntimeError`, use after `close` `ValueError`.

**`sync`** ([line 510][module.c#L510]) extracts the files (a `range` such as `files_below` gives,
or FILES indices) missing below `<root>/<chain>` and replaces those that differ; `chain` must
pass `fsb_cache_safe`. `checked`, one writable bit per FILES record (bit `i % 8` of byte
`i // 8`), marks files checked or written, so a process checks each file once; one changed while
it runs is found by the next process. Files go to `fsb_cache_sync` 4096 at a time, without the
GIL. After each chunk every CHANGED file is reported on stderr (not as a warning, which an app
could silence): `fsbpyr: <root>/<chain>/<path> was modified after extraction; restoring it`;
written paths are appended to `written` if it is a list; and a failure is raised: `CacheError`
for a corrupt record (`corrupt executable payload: <reason> <index>`), `CacheRefused` for a
refused directory (`refusing cache directory <path>: owned by another user`, or `...: other
users may write to it`), `OSError` with the path, or `MemoryError`. The first sync into a tree holds it, and the
lock's descriptor is kept for the process's life, taken again if the app closed it.

A refused cache directory ends the start (D41): `NativeCache.extract` in
[`_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py) catches `CacheRefused`, by its
type, and writes
`fsbpyr: cannot start <path>: <message>` (`<path>` the `executable` of `info()`, as `fatal()`
names it) to descriptor 2 and calls `os._exit(70)`, with no traceback; other `CacheError`s and
`OSError`s stay errors the app sees
([Native code and the cache](Dev-Boot-Modules.md#native-code-and-the-cache)).

### sig.c and sig.h

[`sig.c`][sig.c] is the crypto of [Signatures](Dev-Security.md#signatures)
([`docs/signing.md`](../docs/signing.md) sections 2 and 4): a signature is an Ed25519 and an
ML-DSA-65 signature of a short statement, made and checked with the OpenSSL the runtime links for
`_ssl` and `_hashlib`. Python reads the files, asks for the passphrase and prints; C does the
rest. [`sig.h`][sig.h] declares:

- `fsb_sig_key_new(passphrase, key, pub)`: a new pair, the `.key` (two `ENCRYPTED PRIVATE KEY`
  blocks, Ed25519 then ML-DSA-65, each PKCS#8 encrypted as `release-signing.sh`'s
  `openssl pkcs8 -topk8 -v2 aes-256-cbc -v2prf hmacWithSHA256 -iter 600000` does, with a random
  salt) and the `.pub` (the two public keys as OpenSSL writes them).
- `fsb_sig_sign(key, passphrase, statement, signature, pub)`: the signature, `FSB_SIG_SIZE`
  (3,373) bytes, 64 of Ed25519 then 3,309 of ML-DSA-65, and the key file's `.pub`. The signature
  is checked with the file's own keys before it is given, so a damaged key never yields one.
- `fsb_sig_verify(pub, statement, signature)`: 1 if both halves are good for the two keys of the
  `.pub`, 0 if not (for a signature of any other size too), -1 if `pub` is not a `.pub`, which is
  checked first. Only OpenSSL's 1 passes a half; its 0 and its errors do not.
- `fsb_sig_half(half, pem, message, signature)`: the check of one half (0 Ed25519, 1 ML-DSA-65)
  against one public key, for the independent test vectors, whose keys come one at a time.

Each status but `FSB_SIG_OK` says what is wrong: `FSB_SIG_BAD_KEY_FILE` (not two encrypted
blocks with nothing around them, or a block labelled encrypted that never asked for the
passphrase), `FSB_SIG_BAD_PASSPHRASE` (empty, over 1,024 bytes, `FSB_SIG_PASSPHRASE_MAX`, or a
block it does not open), `FSB_SIG_BAD_KEY_TYPE` (opened, but not Ed25519, then ML-DSA-65) and
`FSB_SIG_FAILED` (OpenSSL failed, or the new signature does not verify). Files are read only as
they are written, at most `FSB_SIG_PEM_MAX` (16 KiB) each: `key_blocks` takes a `.key`'s two
blocks with nothing before or between them and only LFs after them (the last one may be
missing), where OpenSSL's reader would take text around a block, a block that is not encrypted
and anything after it; `public_keys` reads each key of a `.pub` and writes it again, and the
file must be those bytes exactly. The passphrase reaches OpenSSL through a callback
(`give_passphrase`), never as `NULL`, without which OpenSSL would ask on the terminal; a `.pub`'s
reader gets one that refuses (`no_passphrase`).

`fsb_sig_key_new`, `fsb_sig_sign`, `fsb_sig_verify` and `fsb_sig_half` each make a library
context of their own (`OSSL_LIB_CTX_new`), load no configuration into it and free it, so
`openssl.cnf` and the default context change no result; each leaves the thread's error queue as
it found it (`ERR_set_mark`, `ERR_pop_to_mark`), since CPython's `_ssl` reads that queue; and each
takes its results from return values alone, so no OpenSSL text reaches Python. OpenSSL's
functions are declared in the file, as PBS ships no headers; `tests/e2e/test_signatures.py`
compiles it with Debian's headers included as well, so a declaration that does not match fails.

`fsb_sig_methods`, which `module.c` adds to `_fsbpyr`, are the private functions `_sha256(file)`
(the SHA-256 of bytes, or of a file descriptor's file read without the GIL: `fsb_sha256_bytes`
and `fsb_sha256_file` of [`integrity.c`](#integrityc-and-integrityh); `OSError` if it cannot be
read), `_key_new(passphrase)` (`(key, pub)`), `_sign(key, passphrase, statement)`
(`(signature, pub)`) and `_verify(pub, statement, signature)` (`True` or `False`). A failed status
raises `ValueError`: `not a key file`, `wrong passphrase?`, `it holds other keys than Ed25519,
then ML-DSA-65` or `the signature could not be made`, and `not a public key file` from `_verify`.
No key material is kept after a call; the new `.key`'s buffer is cleared (`OPENSSL_cleanse`)
before it is freed. Built with `-DFSB_SIG_NO_PYTHON`, as `tests/probes/sig.c` and
`tests/fuzz/sig_fuzz.c` build it, the file has no Python part. The tests from source build it
with that part: `tests/probes/sig_module.c` adds `fsb_sig_methods` to a module of its own,
`fsbpyr_sig`, which `sig_c` in `tests/support/signing.py` loads into the test process in
`_fsbpyr`'s place.

### glibc.c

[`glibc.c`][glibc.c] keeps the default stub within glibc 2.34 although it links against Debian
12's 2.36. The link's `--wrap` options send two symbols here, each looked up on the first call
and kept in an atomic pointer (racing threads store the same one):

- `hypot`: glibc 2.35 gave it a new symbol version, which CPython's `math` module would bind to.
  `__wrap_hypot` calls the version every glibc has (`GLIBC_2.2.5` on x86-64, `GLIBC_2.17` on
  aarch64), looked up with `dlvsym`, else the default one; a `.symver` directive would break the
  aarch64 LTO link (the file's comment).
- `_dl_find_object` (glibc 2.35): Debian 12's libgcc unwinder, which the 3.15 `libpython` links
  in, finds unwind data with it. `__wrap__dl_find_object` calls glibc's where there is one, else
  walks the loaded objects' program headers (`dl_iterate_phdr`, `PT_LOAD`, `PT_GNU_EH_FRAME`) as
  libgcc did before 2.35. It is compiled only where `<link.h>` defines `DLFO_EH_SEGMENT_TYPE`
  (glibc 2.35 and later headers). The glibc 2.28 stub, from the same file and flags, has none:
  the EL8 headers lack `struct dl_find_object`, and its libgcc (gcc 8) finds frames with
  `dl_iterate_phdr`, so `--wrap=_dl_find_object` finds nothing to wrap.

`tests/e2e/test_portability.py` and `tests/e2e/test_baseline.py` check that each stub needs no
glibc symbol above its baseline, `tests/e2e/test_find_object.py` runs the fallback
([Compatibility](Dev-Compatibility.md) covers the baselines).

### format.h

[`format.h`][format.h] is the C mirror of [`payload/format.py`][format.py]
([Executable format](Dev-Executable-Format.md)): the format version (7), the alignments,
`FSB_NONE`, the digest and piece sizes and limits, the two magics, the section, codec, module
kind, module flag, file kind and file flag enums, and the structs `fsb_trailer` (64 bytes),
`fsb_header` (208), `fsb_blob` (32), `fsb_module` (40), `fsb_file` (24), `fsb_dist` (24) and
`fsb_str`, each size held by a `_Static_assert`. It refuses a host that is not little-endian.

### launcher.h

[`launcher.h`][launcher.h] is shared by `main.c`, `module.c` and `sig.c`: it includes
`Python.h` (with `PY_SSIZE_T_CLEAN`) and `payload.h`, and declares `fsb_self` and
`PyInit__fsbpyr`.

### vendor/zstd

[`vendor/zstd/`][vendor] holds zstd's headers `zstd.h` and `zstd_errors.h` (version 1.5.7, by
their `ZSTD_VERSION_*` macros), which only `payload.c` includes, and zstd's BSD `LICENSE`, which
[`runtime/notices.py`][notices] (`collect`) puts into a runtime's licence notices when PBS ships
no zstd text: PBS lists `LICENSE.zstd.txt` only for the `_zstd` module, which 3.12 and 3.13 lack.

## Environment variables the C code reads

| Variable | Read by | Effect |
|---|---|---|
| `XDG_RUNTIME_DIR` | `main.c` | second directory for the instance-slot file |
| `FSBPYR_IMPORTTIME` | `main.c` | set: CPython's import-time table |
| `PYTHONUNBUFFERED` | `main.c` | set and not empty: stdout and stderr unbuffered (`buffered_stdio = 0`) |
| `FSBPYR_MAX_INSTANCES` | `slots.c` | the instance limit |
| `FSBPYR_GC` | `slots.c`, `due.c` | `background`: a full slot table exits 0; set at all: no background collection is started |
| `FSBPYR_CACHE_DIR`, `XDG_CACHE_HOME`, `HOME` | `due.c` | the cache root whose collection may be due; without an absolute `HOME`, the passwd entry's home directory |

The boot code reads the other `FSBPYR_*` variables, and `JOURNAL_STREAM`
([Boot modules](Dev-Boot-Modules.md)).

## Tests of the C code

[Testing](Dev-Testing.md) describes them: the format probe
`tests/integration/test_launcher_format.py`; the libFuzzer harnesses of `tests/e2e/test_fuzz.py`,
under ASan and UBSan: [`tests/fuzz/payload_fuzz.c`][fuzz], which feeds `fsb_payload_parse`, the
record accessors, lookups, path checks and blob decoding any bytes, and `sig_fuzz.c`, which feeds
`sig.c`'s readers of `.pub` and `.key` files; the C probes in [`tests/probes/`][probes];
`tests/unit/runtime/test_toolchain_and_link.py`; and end-to-end apps such as
`test_unbuffered.py`, `test_gc.py` and `test_cache_root.py`, run with both stubs
([EL8 test mode](Dev-Testing.md#the-el8-test-mode), `test_el8.py`).

[dir]: ../src/fsbpyr/launcher
[vendor]: ../src/fsbpyr/launcher/vendor/zstd
[main.c]: ../src/fsbpyr/launcher/main.c
[main.c#L131]: ../src/fsbpyr/launcher/main.c#L131
[payload.c]: ../src/fsbpyr/launcher/payload.c
[payload.c#L108]: ../src/fsbpyr/launcher/payload.c#L108
[payload.h]: ../src/fsbpyr/launcher/payload.h
[integrity.c]: ../src/fsbpyr/launcher/integrity.c
[integrity.c#L257]: ../src/fsbpyr/launcher/integrity.c#L257
[parallel.c]: ../src/fsbpyr/launcher/parallel.c
[slots.c]: ../src/fsbpyr/launcher/slots.c
[slots.c#L86]: ../src/fsbpyr/launcher/slots.c#L86
[due.c]: ../src/fsbpyr/launcher/due.c
[due.c#L211]: ../src/fsbpyr/launcher/due.c#L211
[way.c]: ../src/fsbpyr/launcher/way.c
[way.c#L174]: ../src/fsbpyr/launcher/way.c#L174
[way.h]: ../src/fsbpyr/launcher/way.h
[cache.c]: ../src/fsbpyr/launcher/cache.c
[cache.c#L1028]: ../src/fsbpyr/launcher/cache.c#L1028
[cache.h]: ../src/fsbpyr/launcher/cache.h
[module.c]: ../src/fsbpyr/launcher/module.c
[module.c#L338]: ../src/fsbpyr/launcher/module.c#L338
[module.c#L510]: ../src/fsbpyr/launcher/module.c#L510
[module.c#L821]: ../src/fsbpyr/launcher/module.c#L821
[sig.c]: ../src/fsbpyr/launcher/sig.c
[sig.h]: ../src/fsbpyr/launcher/sig.h
[glibc.c]: ../src/fsbpyr/launcher/glibc.c
[format.h]: ../src/fsbpyr/launcher/format.h
[launcher.h]: ../src/fsbpyr/launcher/launcher.h
[format.py]: ../src/fsbpyr/payload/format.py
[stub]: ../src/fsbpyr/runtime/stub.py
[toolchain]: ../src/fsbpyr/runtime/toolchain.py
[registry]: ../src/fsbpyr/runtime/registry.py
[stubs]: ../src/fsbpyr/runtime/stubs.py
[pins]: ../src/fsbpyr/runtime/pins.py
[sysroot]: ../src/fsbpyr/runtime/sysroot.py
[rpm]: ../src/fsbpyr/runtime/rpm.py
[text]: ../src/fsbpyr/text.py
[notices]: ../src/fsbpyr/runtime/notices.py
[check-c]: ../tools/check-c.py
[fuzz]: ../tests/fuzz/payload_fuzz.c
[probes]: ../tests/probes
[security-md]: ../docs/security.md
[pyi]: ../stubs/_fsbpyr.pyi
