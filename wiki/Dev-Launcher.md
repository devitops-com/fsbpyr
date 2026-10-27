# Launcher

The launcher is the native program at the start of every executable fsbpyr builds. This page
describes it as of fsbpyr v0.27.0: what it is, what it does from `execve` until it hands control
to the Python boot code, what it never does, how it is compiled and linked, and then each C
source file in [`src/fsbpyr/launcher/`][dir] in turn: its purpose, its main functions, the data it
reads and writes, the rules it enforces, and how it works with the other files. The bytes it
reads are described in [Executable format](Dev-Executable-Format.md); the Python code it starts is
described in [Boot modules](Dev-Boot-Modules.md).

## What the launcher is

Every fsbpyr executable is a stub, zero padding, a payload and a trailer
([Executable format](Dev-Executable-Format.md)). The stub is an ELF program linked from the
launcher's C sources and the static `libpython` of a python-build-standalone (PBS) distribution,
together with the static libraries of CPython's built-in modules (OpenSSL, liblzma, libzstd and
others). The loader maps only the stub's `PT_LOAD` segments, so the appended payload is inert
until the launcher reads it.

The stub is built once per runtime, by `fsbpyr runtime build` (`build_runtime` in
[`runtime/registry.py`][registry]). A runtime is one Python on one target for one glibc
baseline: 2.34, the default, or 2.28 for EL8 hosts (`fsbpyr runtime build --glibc 2.28`, which
links the stub against Rocky Linux 8.10's glibc; see the sysroot in
[How it is compiled and linked](#how-it-is-compiled-and-linked)). Every app built for a runtime
gets the same stub: `build_info`'s `stub_sha256` (`stub_digest` in
[`assemble.py`](../src/fsbpyr/assemble.py)) is
the same in every app built by the same fsbpyr release for the same Python, target and glibc
baseline. A released fsbpyr builds an app for the default baseline with its own stub, the bytes
before its own payload (`Runtime.from_executable` in [`runtime/registry.py`][registry]), and an
app for glibc 2.28 with the glibc 2.28 stub of its companion release `el8-vX.Y.Z`
([`runtime/stubs.py`][stubs], see the recorded id below). The 0.27.0 release's executable for
CPython 3.14.9 on x86-64 (`fsbpyr-3.14-x86_64-linux-gnu`) has a stub of 20,939,049 bytes
(19.97 MiB), counted from its trailer's `payload_offset` with the stub's zero tail counted as
padding, as `fsbpyr inspect --json`'s `sizes` counts it (2026-10-27); the apps it builds for
the default baseline get the same bytes before their payload, so the same `stub_sha256`. The
stub records the runtime id it was linked as (see
[How it is compiled and linked](#how-it-is-compiled-and-linked)). The C sources are
package data of `fsbpyr.launcher`: [`runtime/stub.py`][stub] reads them through
`importlib.resources`, so an fsbpyr executable can build a runtime from the copy inside its own
payload.

## From `execve` to the boot code

![The launcher's startup: thirteen steps from opening /proc/self/exe to Py_RunMain, each with its failure exit](images/launcher-startup.svg)

`main()` in [`main.c`][main.c#L128] runs these steps in this order. Steps 1 to 4 are
`fsb_payload_open` in [`payload.c`][payload.c].

1. **Locate itself.** It reads the link `/proc/self/exe` for its path, opens `/proc/self/exe`
   read-only with `O_CLOEXEC`, and `fstat`s it. A path that the kernel reports as
   `"<path> (deleted)"` (the file was replaced or removed while it runs) is cut back to `<path>`,
   unless a file of that name really is this one (same device and inode).
2. **Check the integrity digest** of the whole file (`fsb_check_integrity`,
   [`integrity.c`][integrity.c]): the last 32 bytes must be the integrity digest of everything
   before them. Nothing else of the file is believed before this passes.
3. **Read the trailer**, the last 64 bytes, with `pread`, and check it: magic, format version 7,
   flags 0, `payload_offset` a multiple of 65536, `payload_offset + payload_size + 64` equal to
   the file size, and a payload at least as large as its header.
4. **Map the payload** (`mmap`, read-only, `MAP_PRIVATE`, at `payload_offset`) and check the
   header and section table: magic, version, `header_size` of 208, and every section inside the
   payload, after the header and 8-byte aligned. The descriptor stays open (`O_CLOEXEC`) with its
   device and inode recorded; large raw blobs are read through a duplicate of it later.
5. **Refuse a privileged start.** If the kernel set `AT_SECURE` (set-uid, set-gid or file
   capabilities), it exits with 70: the cache location would come from the caller's
   environment.
6. **Check the Python version.** The payload's bytecode magic must equal the one the launcher was
   compiled with (`FSBPYR_BYTECODE_MAGIC`), and its `python_hexversion` must have the same major
   and minor version as the CPython it was compiled against (`PY_VERSION_HEX`).
7. **Hold an instance slot** (`fsb_hold_slot`, [`slots.c`][slots.c]) in `/dev/shm`, or in
   `$XDG_RUNTIME_DIR`: a start over the app's instance limit exits here, before CPython starts
   and before the collection's fork.
8. **Start a cache collection if one is due** (`fsb_collect_when_due`, [`due.c`][due.c]): a
   detached process executes the app again with `FSBPYR_GC=background`. The start itself goes on.
9. **Build the frozen-module table** from the STARTUP section: each listed module must be flagged
   STARTUP, and its code must be a zstd frame or a block member (both checksummed) of 1 byte to
   16 MiB. The code is decompressed into memory, so `marshal` never sees bytes that failed a
   checksum. The table becomes `PyImport_FrozenModules`; at most 4096 modules.
10. **Register `_fsbpyr`** ([`module.c`][module.c]) as a built-in module with
    `PyImport_AppendInittab`.
11. **Configure CPython.** The pre-configuration and configuration start from CPython's isolated
    ones (`PyPreConfig_InitIsolatedConfig`, `PyConfig_InitIsolatedConfig`), with UTF-8 mode on;
    the table below gives every field it sets.
12. **Initialise CPython** with `Py_InitializeFromConfig`.
13. **Run the boot code**: the configuration's `run_command` is
    `import _fsbpyr_boot` / `_fsbpyr_boot.run()`, and `main()` returns what `Py_RunMain()`
    returns. `_fsbpyr_boot` is one of the STARTUP modules; from here on the
    [boot modules](Dev-Boot-Modules.md) run the app. `main.c`'s own comment gives the reason for
    `Py_RunMain`: exit codes, `SystemExit` and `SIGINT` behave as in CPython.

The configuration fields that step 11 sets ([`main.c`][main.c]):

| Field | Value | Why, where the code says |
|---|---|---|
| `preconfig.utf8_mode` | 1 | |
| `install_signal_handlers` | 1 | as `python` has them: SIGINT raises `KeyboardInterrupt`; SIGPIPE and SIGXFSZ are ignored, so a write to a closed pipe or socket raises `BrokenPipeError` and one past the file-size limit fails with `EFBIG`, where both would end the process |
| `parse_argv` | 0 | |
| `site_import` | 0 | |
| `safe_path` | 1 | |
| `write_bytecode` | 0 | |
| `pathconfig_warnings` | 0 | |
| `module_search_paths_set` | 1, with no paths | `sys.path` is empty until the boot module installs its hook |
| `import_time` | 1 if `FSBPYR_IMPORTTIME` is set | |
| `buffered_stdio` | 0 if `PYTHONUNBUFFERED` is set and not empty, else as the isolated configuration has it | "the one PYTHON* variable honoured, as CPython reads it: set and non-empty (D14)": stdout and stderr are unbuffered |
| `argv` | the process's `argc` and `argv` (`PyConfig_SetBytesArgv`) | the boot code may then replace `sys.argv[0]` with the path `execve` was given (`execfn` of `_fsbpyr.info()`, see [module.c](#modulec)) |
| `executable` | the path from step 1 | |
| `home`, `prefix`, `exec_prefix`, `base_prefix`, `base_exec_prefix` | the executable's directory | "so getpath has nothing to search" |
| `stdlib_dir` | the executable's path | the root of the payload's virtual tree, so CPython's frozen modules get `__file__` and `__path__` inside it (`encodings.__path__ = [<exe>/encodings]`) |
| `run_command` | `import _fsbpyr_boot\n_fsbpyr_boot.run()\n` | the boot code |

The effect for users ([Compatibility](Dev-Compatibility.md#how-an-app-differs-from-a-venv)): the
interpreter runs isolated, with UTF-8 mode on and no `site` import, and `PYTHONPATH`,
`PYTHONHOME`, `PYTHONDONTWRITEBYTECODE` and the other `PYTHON*` variables have no effect, except
`PYTHONUNBUFFERED`, which the launcher reads itself and sets as `buffered_stdio`; and in an
executable `sys.executable` is the app (the `executable` row above).

### Exit statuses

| Status | Message on stderr | When |
|---|---|---|
| 70 | `fsbpyr: cannot start <path>: <reason>` | any of steps 1 to 6 and 9 to 11 fails (`fatal()` in `main.c`); `<path>` is the executable's path, or `executable` if it could not be read |
| 70 | `fsbpyr: refusing to start: <N> copies of this app are running already, as many as this machine allows at once (does the app keep starting itself?); if it needs more, set FSBPYR_MAX_INSTANCES to a higher number` | step 7: every slot is taken |
| 0 | none | step 7 in a background collection (`FSBPYR_GC=background`): every slot is taken, and the collection waits for another day |
| 70 | `fsbpyr: cannot start <path>: refusing cache directory <dir>: owned by another user`, or `...: other users may write to it` | after step 13: the boot code extracts files to the native cache (an extension it imports, an fs-package, a resource asked for as a path, a bundled library), and the C code finds a directory `<dir>` on the way to the cache root, or in the cache, that another user owns or may write to; the boot code ends the start ([module.c](#modulec)) |
| CPython's | CPython's | a `PyStatus` error from `Py_PreInitialize`, a `PyConfig_Set*` call or `Py_InitializeFromConfig`: `Py_ExitStatusException` ends the process |
| 99 | the sanitizer's report | only in the sanitized test variant: an AddressSanitizer or UndefinedBehaviorSanitizer finding (`__asan_default_options`, `__ubsan_default_options` in `main.c`); neither Python (1) nor the launcher (70) uses it |
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

Because the integrity check comes first, a file that is not an fsbpyr executable at all, or one
cut short, fails at step 2 with `integrity check failed`, not with `no fsbpyr payload`.

## What the launcher never does

- **It never believes a byte of its file before the whole-file digest matches** (step 2). Every
  later check relies on that: the launcher leaves some validation rules to the digest, which
  only the writer's output passes (see [who checks what](Dev-Executable-Format.md#who-checks-what)).
- **It never starts privileged** (step 5).
- **It never searches the file system for the standard library.** All prefixes are the
  executable's directory, `stdlib_dir` is the executable itself, and `sys.path` starts empty;
  modules come from the payload through the frozen table and the boot code's import hook.
- **It never lets CPython import `site`, write bytecode or parse the command line**
  (`site_import`, `write_bytecode` and `parse_argv` are 0), and it starts from CPython's
  isolated configuration.
- **It never hands unchecked bytes to `marshal`.** Startup code must be a checksummed zstd frame
  or a member of one (step 9); `_fsbpyr.read` checks a zstd frame's header before it allocates
  its size.
- **It never reads a table by casting pointers into the mapping**: records are copied out with
  `memcpy` after a bounds check ([`format.h`][format.h]'s comment), so the alignment of the
  mapped data does not matter. Startup validation reads only the trailer, the header and the
  section table; no table is scanned or copied ([`payload.h`][payload.h]).
- **It never reads a large raw blob through its mapping**: raw blobs of 1 MiB or more may hold
  holes and are read with `pread` (see [payload.c](#payloadc-and-payloadh)).
- **It never writes a cached file before every record of the job is checked**, never puts a file
  in place before its content has the payload's digest, and never calls `fsync`
  (see [cache.c](#cachec-and-cacheh)).

## How it is compiled and linked

[`runtime/stub.py`][stub] compiles and links the launcher in one `clang` command; `build_stub`
runs it, and `build_runtime` in [`runtime/registry.py`][registry] calls `build_stub` when it
builds a runtime.

- **Toolchain.** `find_toolchain` ([`runtime/toolchain.py`][toolchain]) takes the first `clang`
  whose major version is at least the LLVM major of the PBS distribution's objects (PBS's static
  libraries are LLVM bitcode; `PbsDist.llvm_major` reads it from `PYTHON.json`), trying
  `clang-N` for ten versions from that major up and then plain `clang`, each with an `ld.lld-N`
  or `ld.lld` on the search path or next to it. `--clang` names one explicitly.
- **Sysroot.** The link uses a pinned glibc sysroot through `--sysroot`, the one
  `pins.sysroot_for(triple, baseline)` gives for the target triple and the runtime's glibc
  baseline ([`runtime/pins.py`][pins]); `fetch_sysroot` ([`runtime/sysroot.py`][sysroot])
  downloads its packages once, checks each against its pinned SHA-256 and extracts them into the
  cache's `sysroots/<identity>`.
  - For the default baseline, 2.34: Debian 12's `libc6`, `libc6-dev` (glibc 2.36),
    `libgcc-s1`, `libgcc-12-dev` (gcc 12) and `linux-libc-dev` (Linux 6.1).
    [`glibc.c`](#glibcc) keeps the stub within glibc 2.34 all the same.
  - For glibc 2.28 (`fsbpyr runtime build --glibc 2.28`, `EL8_SYSROOTS`): Rocky Linux 8.10's
    `glibc`, `glibc-devel`, `glibc-headers` (2.28), `kernel-headers` (4.18), `libgcc` and
    `gcc` (8.5.0), RPMs pinned by URL and SHA-256; pins.py's comment says that only the `gcc`
    package has `crtbeginS.o`, `libgcc.a` and `libgcc_eh.a`. [`runtime/rpm.py`][rpm] turns an
    RPM's payload (an xz-compressed cpio archive) into a tar archive, which `sysroot.py`
    extracts as it extracts a Debian package, leaving out programs, data, configuration and
    glibc's character-set converters; the links `lib` and `lib64` to `usr/lib` and `usr/lib64`
    that EL8's filesystem package makes are added, since the linker scripts name
    `/lib64/libc.so.6` (and on aarch64 the loader below `/lib`). The stub links against glibc
    2.28 and gcc 8's libgcc, and needs no glibc symbol newer than 2.28
    (`tests/e2e/test_baseline.py` checks it).

  The compile and link flags below are the same for both baselines: `stub_command` takes the
  sysroot from the toolchain, so only `--sysroot` and the sysroot's `-ffile-prefix-map` name
  another directory. [Runtime modules](Dev-Runtime-Modules.md#sysroot) describes the extraction.
- **Sources.** `LAUNCHER_SOURCES` are the ten `.c` files (`main.c`, `payload.c`, `module.c`,
  `integrity.c`, `parallel.c`, `cache.c`, `due.c`, `slots.c`, `way.c`, `glibc.c`);
  `LAUNCHER_FILES` adds the nine headers (`launcher.h`, `payload.h`, `integrity.h`,
  `parallel.h`, `cache.h`, `due.h`, `slots.h`, `way.h`, `format.h`) and the two vendored zstd
  headers. They are
  copied into a temporary directory, and `clang` runs there on relative paths, so the directory's
  random name reaches neither `__FILE__` nor LTO's local symbol names (stub.py's comment: it did,
  and every runtime build linked a different launcher).
- **Compile flags** (`COMPILE_FLAGS`):
  `-std=c11 -O2 -flto=full -fPIE -pie -D_GNU_SOURCE -fstack-protector-strong`
  `-D_FORTIFY_SOURCE=2 -Wall -Wextra -Wvla -Wframe-larger-than=8192`.
  Further arguments: `--target=<triple>`, `--sysroot`, `-ffile-prefix-map` from the PBS
  directory to `/fsbpyr/python` and from the sysroot to `/fsbpyr/sysroot` (so the cache's
  location never reaches the binary), `--ld-path=<ld.lld>`,
  `-DFSBPYR_BYTECODE_MAGIC=0x<magic>u` (the distribution's `importlib.util.MAGIC_NUMBER` as a
  little-endian u32; `main.c` refuses to compile without it),
  `-DFSBPYR_RUNTIME_ID="<runtime id>"` when an `identity` is given (below), and `-I` for the
  PBS include directory and the sources.
- **Link inputs** (`link_inputs` in `runtime/pbs.py`): PBS's `libpython` static library; the
  objects of the built-in extension modules that the archive does not contain; the static
  libraries those modules link (`path_static` in `PYTHON.json`); PBS's `build/lib/libzstd.a`
  whenever it exists, since the launcher decodes zstd itself (3.12 and 3.13 have no `_zstd`
  module that would name it); and the system libraries PBS lists, as `-l` options. `libpython`
  and the static libraries are wrapped in `-Wl,--start-group` / `-Wl,--end-group`. On aarch64
  clang's compiler-rt builtins library is added for the outlined atomics PBS's objects call
  (`outlined_atomics`).
- **Link flags** (`LINK_FLAGS`): `-Wl,--export-dynamic-symbol=Py*` and `=_Py*` (only the Python
  C API is exported, so libraries of wheels cannot bind to the static zlib, OpenSSL or libffi in
  the stub), `-Wl,-z,noexecstack -Wl,-z,relro -Wl,-z,now`, `-s` (stripped), `-static-libgcc`,
  and `-Wl,--wrap=hypot -Wl,--wrap=_dl_find_object` for `glibc.c`.
- **Stack frames.** Any line of the compiler's or linker's output that mentions a
  `stack frame size` fails the build (`StubBuildError`): with LTO the frames are sized again at
  the link, where only a warning says so. Every other warning is passed back and printed.
- **A failed link** raises `StubBuildError` with the end of the output: at most its last 4000
  characters, starting at a line boundary, after a note of how many earlier lines were left out
  (`tail_lines` in [`text.py`][text]).
- **Identity.** `launcher_digest` is the SHA-256 over every file of `LAUNCHER_FILES` (name and
  content) and the flags; `runtime_id` in `registry.py` hashes it with the PBS pin, the sysroot
  and the layout version into the runtime's id, `cp<python>-<triple>-<12 hex digits>`. A change
  to a source or a flag gives a new runtime. The sysroot's identity is its pin's name and a
  digest of its packages' SHA-256, so the stubs of the two baselines never share an id.
- **The recorded id.** `build_runtime` passes that id to `build_stub` as `identity`, which
  `stub_command` compiles in as `-DFSBPYR_RUNTIME_ID="<id>"`. The define is not one of the
  hashed flags, since the id is what it carries (a unit test,
  `test_the_identity_flag_is_not_part_of_the_runtime_id`, holds that). `main.c` keeps it as
  the string `fsbpyr-stub-identity=<id>`, marked `used` so that LTO and stripping keep it;
  sources compiled without the define (test programs) record `none`. Nothing reads it at run
  time. `stub_identity` in `stub.py` reads it back out of a file: the text after
  `fsbpyr-stub-identity=` up to the next NUL, or `None` for a file that has none. A build that
  is handed a stub it did not link uses it to prove the stub is the one of its own launcher
  sources, Python, target and sysroot, and refuses a stub whose recorded id is not the runtime
  id it computes.
- **The glibc 2.28 stub of a release.** A released fsbpyr carries the runtime of the default
  baseline only. For `fsbpyr build --glibc 2.28` it pairs its own standard library, which does
  not depend on the sysroot, with a glibc 2.28 stub (`paired` in [`runtime/stubs.py`][stubs]),
  and takes that stub, the asset `fsbpyr-stub-3.X-<target>-glibc2.28`, from the first of:
  1. a file of that name, or of that name with `.xz`, beside the fsbpyr executable (in the
     directory of the path it was run by, or of its real path), for machines that cannot reach
     GitHub;
  2. the copy kept in the cache, `downloads/<asset>-<tag>`;
  3. a download of `<asset>.xz` from the companion release `el8-vX.Y.Z` of its own tag
     (`release_stub_tag` in `vcs.py`; under `$FSBPYR_RELEASE_DOWNLOADS` if set), whose
     SHA-256 must be the one that release's `SHA256SUMS-glibc2.28` lists for the asset; it is
     then kept in the cache.

  Whichever it is, its recorded id must be the runtime id this fsbpyr computes for its launcher
  sources, Python, target and the EL8 sysroot (`_checked`): a placed or downloaded stub that
  records another id (another Python, the default baseline, launcher sources or pins of another
  release) or none is refused with a message that names the asset and the release to take it
  from, and a cached copy whose id differs is downloaded again. A file larger than 256 MiB is
  no stub. A tag without a companion release, or one that lists no stub for this Python, fails
  the build with a message that says to place the asset beside the executable. A developer
  build has no release to fetch from: it uses a cached runtime that
  `fsbpyr runtime build --glibc 2.28` built. A cached runtime is checked by its digests
  instead, and the runtime embedded in a released fsbpyr by that executable's own integrity
  check. After `fsbpyr self update`, stubs placed beside the executable are named in a warning,
  since they belong to the old release.
- **Sanitized variant.** With `sanitize=True` the compile drops `-O2`, `-flto=full` and
  `-D_FORTIFY_SOURCE=2`, adds `-O1 -fsanitize=address,undefined` and
  `-fno-sanitize-recover=undefined -fno-omit-frame-pointer -g`, and the link drops `-s`. Its
  runtime id ends in `-sanitized`. It is a test variant; [Testing](Dev-Testing.md) covers it.
- **Static analysis.** `make check-c` runs [`tools/check-c.py`][check-c]: `clang --analyze` over
  each of `LAUNCHER_SOURCES` with `COMPILE_FLAGS` minus `-O2 -flto=full -fPIE -pie`, the same
  `FSBPYR_BYTECODE_MAGIC` define and include directories, against the PBS distribution for the
  Python version that runs the tool and the host's target (downloaded into fsbpyr's cache once).
  It prints the analyzer's report per file and exits 1 if any file has findings or fails to
  compile; the Makefile calls it informational.

Compile-time guards in the sources: `format.h` stops on a big-endian target, `module.c` on a
free-threaded CPython (`Py_GIL_DISABLED`: `read()` shares the process's zstd decoder, which the
GIL serializes), `main.c` without `FSBPYR_BYTECODE_MAGIC`, and `glibc.c` on an architecture other
than x86-64 and aarch64. `glibc.c` compiles its `_dl_find_object` wrapper only where the
sysroot's `<link.h>` declares it: in the Debian 12 sysroot, not in the EL8 one
([below](#glibcc)).

## Source files

![Which launcher source file calls which, and which headers it includes; purple boxes are the libraries linked in](images/launcher-files.svg)

The arrows follow the `#include` lines and the `fsb_*` calls of the sources. A box such as
`payload.c/.h` stands for a source file and its header. `main.c` and `module.c` share
`launcher.h`; `payload.h` and `integrity.h` include `format.h`; `cache.h` includes `payload.h`.

### main.c

[`main.c`][main.c] is the entry point. Its functions:

- `main()` runs the startup sequence above.
- `fatal(what)` prints `fsbpyr: cannot start <path>: <what>` and exits 70 (`EXIT_CORRUPT`,
  `EX_SOFTWARE`).
- `check_versions()` compares the bytecode magic and the major and minor Python version (step 6).
- `startup_table()` and `startup_code()` build the frozen-module table (step 9). The table is
  `calloc`ed with one zeroed entry more as its end; each entry's name points into the mapped
  string table, its code into memory `malloc`ed for it, and `is_package` is set for PACKAGE
  modules. Limits: `MAX_STARTUP_MODULES` (4096), `MAX_STARTUP_CODE` (16 MiB per module).
- `pin_prefixes()` sets the five prefixes to the executable's directory (`/` for an executable
  in the root directory) and `stdlib_dir` to the executable's path.
- `check(status)` passes a `PyStatus` error to `Py_ExitStatusException`.
- `__asan_default_options()` and `__ubsan_default_options()`, compiled only under
  AddressSanitizer: `detect_leaks=0:allocator_may_return_null=1:exitcode=99` and
  `print_stacktrace=1:exitcode=99`. CPython frees little at exit, so leaks are not reported;
  allocation failure returns `NULL` as without ASan, which the launcher reports as out of memory.

It defines `fsb_self`, the `struct fsb_payload` of the running executable that `module.c` reads,
and `identity`, the string `fsbpyr-stub-identity=` followed by `FSBPYR_RUNTIME_ID` (`none` when
the define is missing; see [the recorded id](#how-it-is-compiled-and-linked)). It reads the
environment variables `XDG_RUNTIME_DIR` (the second slot directory), `FSBPYR_IMPORTTIME` and
`PYTHONUNBUFFERED`.

### payload.c and payload.h

[`payload.c`][payload.c] maps and validates the payload and gives read-only, bounds-checked
access to it; [`payload.h`][payload.h] declares `struct fsb_payload` and the API.

`struct fsb_payload` holds the mapping (`base`, `size`), whether it is a mapping of the file
(`mapped`, false for an image in memory in tests), the payload's offset in the file, the open
descriptor with the file's device and inode, a copy of the header, the string table's position
and size, `data_start` (the first byte after the last section: where blob data may start), and
the executable's path (4096 bytes).

**Opening.** `fsb_payload_open` ([line 108][payload.c#L108]) runs steps 1 to 4 above.
`fsb_payload_parse` runs the same trailer, header and section checks on a whole executable image
in memory, without the integrity check; the fuzz harness and edge-case tests use it.

**Records.** `fsb_get_blob`, `fsb_get_module`, `fsb_get_file`, `fsb_get_dist`,
`fsb_get_startup`, `fsb_get_word` (CLOSURES) and `fsb_get_digest` copy record *i* out of its
section; they return false for an index out of range or a record that fails its checks:

- a module's `kind` must be 1 to 4; a file's `kind` 1 to 5 and its `mode` at most `0777`;
- a blob of codec 2 (in a block) must name a block index below the BLOBS count and within u32,
  with `stored_size + size` at most 1 MiB;
- a blob of codec 4 or 5 (in pieces) must have `size` above one piece (4 MiB for xz, 8 MiB for
  zstd) and at most 2^62, `stored_size` equal to the number of pieces `size` needs, and its
  pieces inside the BLOBS table;
- any other blob must be 16-byte aligned, at or after `data_start`, inside the payload, with
  `size` at most 2^62; codec 0 needs `stored_size == size`, codec 3 `size` of at most 4 MiB,
  and only codecs 0, 1 and 3 are accepted here.

`fsb_str` returns a string of the string table if it lies inside it and is followed by a NUL.

**Lookups.** MODULES, FILES and DISTS are sorted by their key (the first field of each record).
`fsb_record_key` returns record *i*'s key; `fsb_lower_bound` binary-searches for the first key
not below a given one, comparing as Python compares bytes (`memcmp` order, the shorter first on
a common prefix); `fsb_find_module`, `fsb_find_file` and `fsb_find_dist` return the index of an
exact match or `FSB_NONE`. Nothing is scanned.

**Reading blobs.** `fsb_read_blob(payload, decoder, blob, dst)` writes a blob's content into
`dst[size]`:

| Codec | How |
|---|---|
| 0, raw | copied from the mapping; one of 1 MiB or more is read from the file instead (below) |
| 1, zstd | `fsb_zstd_frame_ok` first: the bytes are exactly one frame with the zstd magic, the content-checksum flag and the declared content size equal to `size`; then decompressed with the decoder's context to exactly `size` bytes |
| 2, block | the block (a codec 1 blob of at most 1 MiB) is decompressed into one of the decoder's block slots, or found there, and the member's bytes are copied out |
| 3, xz | `lzma_stream_buffer_decode` from the static liblzma, with a 64 MiB memory limit: exactly one stream that consumes all `stored_size` bytes and gives exactly `size` |
| 4, 5, pieces | the pieces decoded in parallel through `fsb_parallel` (one item per piece, up to 8 threads); each piece must be a blob of the pieces' codec (3 or 1) with the expected size |

A blob larger than the stream window (64 MiB), or a raw one of 1 MiB or more read from a file,
goes through a stream instead. On failure `errno` is 0 for corrupt data, `ENOMEM`, or the error
of reading the executable.

**Decoders.** A `struct fsb_decoder` holds a zstd context, created on first use, and a number
of block slots of 1 MiB each, reused least recently used first: a package's modules share
blocks, so a block is decompressed once while it stays among the last few. `NULL` names the
process's own decoder with 32 slots, for callers that hold the GIL or run before Python starts;
a cache sync, which runs without the GIL, creates its own with 4. Nothing is shared between
decoders, so nothing is locked (`payload.h`: a lock taken while another thread forks would stay
taken in the child).

**Streams.** `fsb_stream_open`, `fsb_stream_next`, `fsb_stream_seek`, `fsb_stream_position`,
`fsb_stream_size`, `fsb_stream_error` and `fsb_stream_close` give a blob's content a window at a
time, of at most `FSB_STREAM_WINDOW` (64 MiB; tests build it smaller). Every window but the last
holds whole 1 MiB digest leaves, so a window can be hashed as it comes. A zstd blob is decoded
with a streaming context of its own, and its frame is checked to its end, checksum included, at
the last window; a seek backwards restarts the frame. xz and block blobs are decoded whole into
the stream's buffer (they are at most one piece or one block). Pieces are decoded a window's
worth at a time in parallel. A large raw blob is read with `pread` from a descriptor of the
stream's own. `fsb_stream_next` returns `FSB_STREAM_DATA`, `FSB_STREAM_END`,
`FSB_STREAM_CORRUPT`, `FSB_STREAM_MEMORY` or `FSB_STREAM_SYSTEM`; after a failure every later
call fails the same way.

**Raw blobs and holes.** The writer leaves whole leaves of zeros of a raw blob unwritten, so a
raw blob of 1 MiB or more may hold holes; it is read from the file, never through the mapping,
where a hole on tmpfs would take a page of memory for every page read and could end the process
with `SIGBUS`. The descriptor is a duplicate of the one kept open if that one still is the
executable (same device and inode; the app may have closed it), else `/proc/self/exe` opened
again and checked the same way, else `ESTALE`.

**Memory.** Pages read through the mapping stay in the process's resident memory, though they
are only page cache: after every 256 MiB read through the mapping, the data area's pages are
dropped with `madvise(MADV_DONTNEED)`; the mapping is read-only, so a page used again is read
again. `fsb_stored_bytes` and `fsb_decompressed_bytes` count what was read and decompressed;
`_fsbpyr.stats()` reports them.

### integrity.c and integrity.h

[`integrity.c`][integrity.c] computes the integrity digest: the SHA-256 over the concatenated
SHA-256 digests of the data's 1 MiB pieces (`FSB_DIGEST_LEAF`), the last piece shorter. One
routine serves both checks the launcher makes: its own file before it starts, and every file it
extracts to the cache. The format is described in
[Executable format](Dev-Executable-Format.md#integrity-digest).

- `fsb_tree_digests(count, data, fds, lengths, digests)` computes the digests of several buffers
  at once: buffers in memory, or files open as descriptors (a `NULL` data entry) read a leaf at a
  time. Leaves are hashed in passes of up to 4096 (`FSB_PASS_LEAVES`: 4 GiB of data, 128 KiB of
  leaf digests), each pass on `fsb_parallel` with one thread per 4 leaves (`LEAVES_PER_THREAD`),
  and folded into each buffer's root before the next pass, so the memory needed does not grow
  with the files' sizes. A file is read with `pread` into a 64 KiB buffer per thread, never
  mapped. A leaf that cannot be read whole gets a digest of zeros, which cannot match, and the
  call returns `EIO`; `ENOMEM` if out of memory; else 0.
- `fsb_tree_digest` and `fsb_file_digest` are the one-buffer and one-file forms;
  `fsb_tree_init`, `fsb_tree_add` and `fsb_tree_final` hash content that comes a window at a
  time (every window but the last whole leaves), as a cache sync writes it.
- `fsb_check_integrity(fd, size)` ([line 192][integrity.c#L192]) returns `NULL` if the file's
  last 32 bytes are the digest of the bytes before them, else a static message (see the step 2
  reasons above), and stores the time it took in `fsb_integrity_ns` (`_fsbpyr.stats()`, which
  `FSBPYR_TRACE` prints).

SHA-256 is OpenSSL's low-level `SHA256_Init`/`SHA256_Update`/`SHA256_Final`, statically linked
for CPython's `_hashlib` (SHA-NI where the CPU has it). The file declares them itself, because
the distribution ships no OpenSSL headers, and gives their context a 256-byte union. It does not
use the one-shot `SHA256()`: since OpenSSL 3 that goes through EVP, which may read `openssl.cnf`
(`$OPENSSL_CONF`) before the check is done.

### parallel.c and parallel.h

[`parallel.c`][parallel.c] is the launcher's one routine for parallel work: hashing the
integrity digest, decoding the pieces of a large blob, and writing files to the cache.

`fsb_parallel(count, per_thread, work, context)` calls `work(context, item, worker)` for every
item in `[0, count)` and returns when all are done. The calling thread is worker 0; it starts up
to 7 helpers (`FSB_PARALLEL_MAX_THREADS` is 8), never more threads than the cores this process
may run on (`sched_getaffinity`) nor than one per `per_thread` items. Each thread takes the next
item from an atomic counter. If a helper cannot be started, the threads already running do the
rest. A call made from work that `fsb_parallel` runs runs on its own thread alone (a
thread-local flag), so nested work (hashing what a cache writer writes) never makes more than 8
threads. After its own share the caller spins on non-blocking joins for up to 2 ms, yielding
the core now and then, before it sleeps in `pthread_join`. The comment gives the measurement
behind it: on a hybrid CPU a thread woken from a join may land on an efficiency core.

`fsb_monotonic_ns` reads `CLOCK_MONOTONIC` in nanoseconds.

### slots.c and slots.h

[`slots.c`][slots.c] enforces the app's instance limit: every start holds one slot for as long
as it runs, and a start that finds every slot taken is refused. An app that keeps starting
itself, however it does so, stops at the limit.

- `fsb_instance_limit()` returns `$FSBPYR_MAX_INSTANCES` if it is 1 to 7 decimal digits and
  nothing else (clamped to 4096; 0 turns the guard off), else 4 per CPU this process may run on
  plus 16, at least 64 and at most 1024.
- `fsb_hold_slot(dev, ino, dirs, count)` ([line 86][slots.c#L86]) opens `<dir>/fsbpyr-<euid>`
  in the first directory of `dirs` (main.c passes `/dev/shm` and `$XDG_RUNTIME_DIR`; a `NULL` or
  relative entry is skipped) whose file it can trust: opened with
  `O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_NOCTTY | O_CLOEXEC` and mode 0600, it must be
  a regular file owned by the effective user with no group or other permission bits. A
  descriptor 0 to 2 is moved higher, so that a start with stdout closed never has Python write
  the app's output there. It then
  takes a one-byte open-file-description write lock (`F_OFD_SETLK`) on the first free byte of
  the executable's range: `limit` bytes from an offset derived from the executable's device and
  inode (a splitmix64 finalizer, modulo 2^40, times 4096). The descriptor stays open while the
  process runs; the kernel frees the slot when the process ends, by `SIGKILL` too, and a forked
  child shares its parent's slot.
- When every slot is taken it prints the refusal (see [exit statuses](#exit-statuses)) and exits
  70, or exits 0 under `FSBPYR_GC=background`.
- The guard never refuses for its own sake: no file it can trust, or a lock error other than
  `EAGAIN` and `EACCES` (no OFD locks on that file system), leaves the start unguarded.

The limit is no security boundary (D45 in [`docs/security.md`][security-md]): the environment
that starts the app can turn it off (`FSBPYR_MAX_INSTANCES=0`), and a start that finds no file
it can trust runs unguarded.

### due.c and due.h

[`due.c`][due.c] decides, before Python starts, whether the app's cache is due for a collection,
and if so starts one in the background. The collection itself is Python code
([Boot modules](Dev-Boot-Modules.md)).

`fsb_collect_when_due(app, length, argv0)` ([line 193][due.c#L193]) does nothing when
`AT_SECURE` is set, when the process is process 1 (in a container no zombie of its child would
be reaped), or when `FSBPYR_GC` is set at all. Otherwise:

1. `cache_root` finds the app's cache root as the boot code does: `$FSBPYR_CACHE_DIR` (made
   absolute against the working directory and normalized, as `os.path.abspath` would), else
   `$XDG_CACHE_HOME/<app>` if `XDG_CACHE_HOME` is absolute, else `<home>/.cache/<app>`;
   otherwise nothing is done. `<home>` (`home()`) is `$HOME` if it is absolute, else (unset,
   empty or relative) the home directory of the passwd entry of the process's user
   (`getpwuid_r(getuid())`) if that is absolute; `home_directory` in the boot code's
   `_fsbpyr_rt/native.py` has the same rule. An app name that is empty, `.`, `..`, or contains
   `/` or a NUL is refused, as the boot code refuses it.
2. `due` decides with one `lstat` of the stamp `<root>/.gc`, whose modification time is the last
   collection's start: a collection is due if the stamp is missing, a day old, dated ahead of
   the clock, or an hour old with less than 10% of the file system's blocks available
   (`statvfs`, made only then). In every case the root must hold the marker `.fsbpyr-cache` as a
   regular file of the effective user: a root without it is never collected.
3. `trusted` requires `fsb_way_check` ([way.c](#wayc-and-wayh)) to find the root's way trusted.
4. `claim` opens the stamp (`O_RDWR | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK | O_NOCTTY`, created
   0600 with `O_EXCL` if it was missing), takes `flock(LOCK_EX | LOCK_NB)`, requires a regular
   file of the effective user whose modification time is still the one `due` saw, and touches
   it. So one start of many claims a collection, and none while a collection holds the stamp.
   A stamp that cannot be touched (a read-only cache) starts nothing.
5. `start_collection` forks; the launcher waits for that child, which calls `setsid` and forks
   again and exits. The grandchild sets nice 19 and the idle I/O priority class, points
   descriptors 0 to 2 at `/dev/null` and closes every other one (`close_range`, else the entries
   of `/proc/self/fd`, else one by one up to the descriptor limit), changes to `/`, sets
   `FSBPYR_CACHE_DIR` to the root and `FSBPYR_GC=background`, and executes `/proc/self/exe` with
   `argv0` alone. The new process is a normal start of the app, so it too checks its integrity
   and holds a slot; the boot code then collects instead of running the app.

`due.h` states the cost: one `stat` per start, and a `statvfs` when the last collection was
over an hour ago.

### way.c and way.h

[`way.c`][way.c] checks that no other user can replace or redirect any part of the way from `/`
to the cache root, since what the cache holds is loaded as code. [`way.h`][way.h] states the
rule, and `way_problem` in the boot code's `_fsbpyr_rt/gc.py` is the same rule in Python:

- every directory on the way, the root included, must be owned by root or by the effective
  user, and writable by no other user unless it is sticky (as `/tmp`): not writable by others,
  nor by a group other than the effective group;
- in a user namespace an owner outside it shows as the overflow uid (nobody); such a directory
  counts as root's when no uid of the namespace maps to the overflow uid
  (`/proc/sys/kernel/overflowuid`, `/proc/self/uid_map`), but only above the first sticky
  directory: below one, only root's and the user's count;
- symlinks must be root's or the user's; they are followed one at a time (at most 40), their
  targets' ways checked as well; `..` goes up from where the walk is, as the kernel resolves it;
- the walk ends at the root, or at the first part that does not exist yet; a `..` after a
  missing part is refused, as the kernel would.

`fsb_way_check(root, where, size, error)` ([line 171][way.c#L171]) walks with `O_PATH`
descriptors from `/` (a relative root is taken from the working directory) and returns
`FSB_WAY_TRUSTED`, `FSB_WAY_PARTIAL` (trusted up to a part that does not exist: check again once
it does), `FSB_WAY_FOREIGN` (owned by another user), `FSB_WAY_EXPOSED` (writable by other users),
`FSB_WAY_SYSTEM` (an `errno` in `*error`) or `FSB_WAY_MEMORY`; `where` gets the path at fault.
Its buffers are on the heap, so it is thread-safe. `due.c` and `cache.c` call it.

### cache.c and cache.h

[`cache.c`][cache.c] extracts payload files to the native cache and checks the extracted files a
process uses before it first uses them. [`cache.h`][cache.h] describes the layout and the rules.

**Layout.** `<root>/<chain>/<site-relative path>`, such as
`<root>/runtime/<generation>/site/x.libs/libx.so`. The chain's parent (`runtime/<generation>`)
is a tree a collector may remove once no process uses it; it holds the tree's `.lock` and the
`.scratch` directory. The root holds the marker `.fsbpyr-cache`.

**The job.** `fsb_cache_sync(payload, job)` ([line 1009][cache.c#L1009]) takes a
`struct fsb_cache_sync`: the root, the chain, the FILES indices to sync (in ascending order, so
directories are opened once), whether resources may be extracted, an outcome byte per file, and
whether to hold the tree. It sets `failure` (`FSB_CACHE_OK`, `_CORRUPT` with a reason and the
file's index, `_FOREIGN`, `_SYSTEM` with `errno` and path, `_EXPOSED`, `_MEMORY`) and the
outcome bits `FSB_CACHE_CHANGED` (on disk, but not as the payload has it), `FSB_CACHE_DONE`
(the file on disk is the payload's now) and `FSB_CACHE_WRITTEN`. It is independent of Python and
thread-safe; its caller in `module.c` releases the GIL. It stops at the first failure.

**What it does, in order:**

1. **Check every record before touching the disk.** For each file: the FILES record and its
   path; the file must be extractable (a native extension or library, a file flagged
   FS_REQUIRED, or any file when `resources` is set); its path must pass `fsb_cache_safe` (no
   empty, `.` or `..` component, no NUL, no backslash: it cannot leave the directory it is
   opened from); its blob record; and its digest from DIGESTS. A file without a digest is
   allowed only with `resources`, and then the payload's content is hashed instead.
2. **Open the root.** `fsb_way_check` must find the way trusted. If a part is missing, the root
   is created (its parents with mode 0755, as far as the umask leaves it, the root itself 0700)
   and the whole way is checked again, so a part someone else made meanwhile is caught. The root
   may be a symlink and must be owned by the effective user.
3. **Take the tree** (when `hold` is set). The root gets its marker if it has none and holds
   nothing but fsbpyr's names (`.fsbpyr-cache`, `.gc`, `.trash`, `runtime`, `resources`,
   `.gc-tool`, `.trash-tool`, `runtimes`, `pbs`, `sysroots`, `downloads`) and is empty or already
   holds a tree of fsbpyr's: a directory of the user's is never marked. Then the tree's `.lock`
   is opened (created 0600 if missing), must be a regular file of the user's, and is held
   `LOCK_SH` (waiting for a collector that holds it); if a collector renamed the tree away
   meanwhile, it is taken again, up to 8 times. A lock a day old (or dated ahead) is touched: its
   modification time is the tree's last use. Best effort: a tree that cannot be held is used
   unheld. The descriptor goes back to the caller in `job->lock`.
4. **Check what is on disk.** For each file, its directory below the root is opened, and created
   with mode 0700 where missing; no directory is followed if it is a symlink, each must be the
   user's, and one with group or other permission bits is set to 0700. The file is opened with
   `O_NOFOLLOW | O_NONBLOCK | O_NOCTTY` (a symlink is replaced, a FIFO never waited for, a
   terminal never becomes the controlling one). A regular file of the user's with the payload's
   size is hashed: files up to 64 KiB are read into a 16 MiB buffer, larger ones stay open (at
   most 64) and are read a leaf at a time by the hashing threads; up to 1024 files per parallel
   pass of `fsb_tree_digests`. Anything else, or a digest that differs, is queued to be
   replaced and marked CHANGED; a missing file is queued. When the files a pass holds leave no
   descriptor or memory (`EMFILE`, `ENFILE`, `ENOMEM`) for the next file or its directories, the
   pass is checked first and the file tried once more.
5. **Write the queued files**, in path order. Files of at most 8 MiB stored raw, as one zstd
   frame or in a block go to a pool on up to 8 threads (`fsb_parallel`), in chunks of 32 files
   or 2 MiB that never split a block, so a block is decoded once; each writer has its own
   directories and decoder. Larger files, xz and pieces are then written by the calling thread
   one at a time, since their pieces are decoded in parallel already. The descriptor table is
   grown once before the pool starts (`make_room`). The failure reported is the first in path
   order; if the pool failed for want of descriptors or memory, the calling thread writes what
   it left.

**Writing one file** (`restore`). The content comes from a stream, a window at a time, hashed as
it is written, in pieces of at most 1 MiB; a piece of zeros is not written (a hole), and the
file is truncated to its size if it ends in one. Only if the digest is the payload's is the file
made readable by its owner, executable where the payload's mode says so (`0400` or `0500`), never
writable, and put in place. A new file is written unnamed in its own directory (`O_TMPFILE`) and
linked into place (`linkat` with `AT_EMPTY_PATH`, else through `/proc/self/fd/<n>`, after which
the linked name must be the file written); a killed process leaves nothing behind. A file that
replaces another, or that cannot be linked, is written as `.fsbpyr-<pid>-<n>` in `.scratch`,
locked `LOCK_EX` while it is written (a collector removes only what it can lock), and renamed
into place. Once a link through `/proc` gives the wrong file, or the file system says no link
can be made there, no further file of the process is written unnamed. Nothing is synced to disk:
a file a crash left incomplete differs from its digest, and the next process that uses it
replaces it.

### module.c

[`module.c`][module.c] is `_fsbpyr`, the built-in module through which the
[boot modules](Dev-Boot-Modules.md) reach the payload. `PyInit__fsbpyr`
([line 817][module.c#L817]) creates it with the type `Stream`, the exceptions `PayloadError`
(a `ValueError`) and `CacheError` (an `ImportError`: the native cache cannot be used, or the
payload's data for it is corrupt), and the constant `NONE` (`0xFFFFFFFF`). Every function reads
`fsb_self`.

| Function | Returns |
|---|---|
| `find_module(name)` | `(index, kind, flags, code, source, file, dist, path)` of the MODULES record, or `None` |
| `find_file(path)` | `(index, kind, flags, mode, blob, dist, closure)` of the FILES record, or `None` |
| `file_at(index)` | `(index, kind, flags, mode, blob, dist, closure, path)` |
| `closure(word)` | the FILES indices of the closure that starts at CLOSURES word `word`: a count of at least 1 and at most the FILES count, then that many indices in range and strictly ascending |
| `blob_info(blob)` | `(size, digest)`: the content's size, and its integrity digest or `None` |
| `read(blob)` | the content as `bytes` |
| `stream(blob)` | a `Stream`: the content a window at a time |
| `list_dir(path)` | the sorted names directly below a payload directory (`''` is the root) |
| `is_dir(path)` | whether any file lives below a directory: one binary search |
| `list_modules(prefix)` | `[(name, kind)]` of the direct children of `prefix` (`"pkg."`, or `''` for the top level) |
| `files_below(path)` | a `range` of the FILES indices below a directory: two binary searches, from the first key starting with `path/` to the first starting with `path0` (`'0'` follows `'/'`); `''` is every file |
| `find_dist(name)`, `dists()` | `(index, name, dist_info, version)` of one distribution by canonical name, or of all |
| `info()` | a dict: `executable`, `execfn`, `app_name`, `entrypoint`, `build_info`, `generation_id`, `python_hexversion`, `payload_size`, `modules`, `files` |
| `stats()` | a dict: `reads`, `stored_bytes`, `decompressed_bytes`, `integrity_ns` |
| `sync(root, chain, files, checked, resources[, written])` | the number of files written to the cache (below) |

A name or path that cannot be encoded as UTF-8 cannot be in the payload: the lookups return
`None`, an empty list or `False` for it. A record that fails its checks raises `PayloadError`
(`corrupt executable payload: <what> <index>`). [`stubs/_fsbpyr.pyi`][pyi] declares the
module's types for mypy (`mypy_path = "stubs"` in `pyproject.toml`).

**`info`** reads the header's three strings and the counts. `executable` is the path from step 1
and `execfn` the path `execve` was given, as the kernel passed it (`getauxval(AT_EXECFN)`,
possibly relative), or `None` if there is none; both are decoded as `sys.executable` is
(`PyUnicode_DecodeFSDefault`). The boot code reads `execfn` to set `sys.argv[0]` to the path the
app was started by, made absolute, when its basename is that of `argv[0]` and it is neither
`/proc/self/exe` (as the background collection is started) nor `/dev/fd/N`
([Boot modules](Dev-Boot-Modules.md)). `entrypoint` is the header's string as it is: `module:attr`
or `module`, or for several commands a table of `name=target` lines, which only the boot code
parses ([Executable format](Dev-Executable-Format.md#payload-header)).

**`read`** ([line 336][module.c#L336]) checks a zstd frame's header before it allocates the
result. It releases the GIL for xz, pieces, raw blobs of 1 MiB or more and blobs larger than the
stream window, which use no shared decoder; zstd frames and block members use the process's
decoder and keep the GIL. It raises `MemoryError` for `ENOMEM`, `OSError` if the executable
could not be read, and `PayloadError` for corrupt data.

**`Stream`** has `readinto(buffer)` (0 at the end), `seek(position)` (absolute; inside the
current window only the offset moves, else nothing is decoded until the next read), `tell()`,
`close()` and the attribute `size`. A window is decoded with the GIL released, except for block
members. After a window fails, every later call fails; a stream used on two threads at once
raises `RuntimeError`, a closed one `ValueError`.

**`sync`** ([line 508][module.c#L508]) extracts the files (a `range` such as `files_below` gives,
or a sequence of FILES indices) missing below `<root>/<chain>` and replaces those that differ.
`chain` must pass `fsb_cache_safe`. `checked` is a writable buffer of one bit per FILES record
(bit `i % 8` of byte `i // 8`), set once a file is checked or written, so a process checks each
file once; files already marked are skipped. The files go to `fsb_cache_sync` 4096 at a time,
with the GIL released. After each chunk, every CHANGED file is reported on stderr (not as a
warning, which an app could silence) as
`fsbpyr: <root>/<chain>/<path> was modified after extraction; restoring it`; written paths are
appended to `written` if it is a list; and a failure is raised: `CacheError` for a corrupt
record (`corrupt executable payload: <reason> <index>`), a directory of another user
(`refusing cache directory <path>: owned by another user`) or one others may write to
(`refusing cache directory <path>: other users may write to it`), `OSError` with the path for a
system error, `MemoryError` for memory. The first sync into a tree asks to hold it; the lock's
descriptor is kept in a list for the process's life, and taken again if the app closed it.

**A refused cache directory ends the start.** The C code finds the refusal and words it:
`fsb_cache_sync` refuses a way to the cache root that another user owns or may write to
(`fsb_way_check`), and a directory of the cache that is not the user's. The boot code decides
what it means. `NativeCache.extract` in the boot code's
[`_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py),
through which the import of an extension, an fs-package, a resource asked for as a path and a
bundled library extract their files, calls `sync` and takes a `CacheError` whose message starts
with `refusing cache directory ` as a refused start (D41): it flushes `sys.stdout` and
`sys.stderr`, writes `fsbpyr: cannot start <path>: <message>` (the `executable` of `info()`, as
`fatal()` names it) straight to descriptor 2 and ends the process with `os._exit(70)`, since the
refusal may come in a thread or under code that catches `SystemExit`. No more of the app runs,
and no traceback is printed. Every other `CacheError` and `OSError` stays an error the import or
the resource reports.

### glibc.c

[`glibc.c`][glibc.c] keeps the default stub within fsbpyr's default glibc baseline, 2.34,
although it links against Debian 12's glibc 2.36. The link's `--wrap` options send two symbols
here:

- `hypot`: glibc 2.35 gave it a new symbol version, which CPython's `math` module would bind to.
  `__wrap_hypot` calls the version every glibc has (`GLIBC_2.2.5` on x86-64, `GLIBC_2.17` on
  aarch64), looked up with `dlvsym` on the first call, or the default one if that is missing.
  The file's comment explains why not a `.symver` directive: it breaks the aarch64 LTO link.
- `_dl_find_object` (glibc 2.35): Debian 12's libgcc unwinder finds a frame's unwind data with
  it, and the 3.15 `libpython` links that unwinder in. `__wrap__dl_find_object` calls glibc's
  own where glibc has it, else walks the loaded objects' program headers
  (`dl_iterate_phdr`, `PT_LOAD` and `PT_GNU_EH_FRAME`), the way libgcc looked before 2.35.
  The wrapper is compiled only where the sysroot's `<link.h>` defines `DLFO_EH_SEGMENT_TYPE`
  (glibc 2.35 and later headers, as Debian 12's are).

Both look the function up on the first call and keep it in an atomic pointer (two threads that
race look it up twice and store the same pointer).

The glibc 2.28 stub is linked from the same file with the same flags, against the EL8
sysroot's glibc 2.28 itself. `__wrap_hypot` is compiled as for the default stub. The EL8
headers lack `struct dl_find_object`, so the `_dl_find_object` wrapper is compiled out: the file's
comment says that the sysroot's libgcc (gcc 8) finds frames with `dl_iterate_phdr` and never
asks, and the link's `--wrap=_dl_find_object` finds nothing to wrap.

The file names its tests: `tests/e2e/test_portability.py` checks that the stub needs nothing
newer than the baseline, and `tests/e2e/test_find_object.py` runs the fallback.
`tests/e2e/test_baseline.py` checks both baselines: it compiles `glibc.c` against each
sysroot's headers, links a stub against the EL8 sysroot, and checks that the default stub needs
no glibc symbol above 2.34 and the EL8 one none above 2.28.
[Compatibility](Dev-Compatibility.md) covers the glibc baselines.

### format.h

[`format.h`][format.h] is the C mirror of [`payload/format.py`][format.py]: the format version
(7), the alignments, `FSB_NONE`, the digest and piece sizes and limits, the two magics, the
section, codec, module kind, module flag, file kind and file flag enums, and the structs
`fsb_trailer` (64 bytes), `fsb_header` (208), `fsb_blob` (32), `fsb_module` (40), `fsb_file`
(24), `fsb_dist` (24) and `fsb_str`, each size held by a `_Static_assert`. It refuses to compile
on a host that is not little-endian. `tests/integration/test_launcher_format.py` compiles a probe
against it and compares every size, offset and constant with the Python definitions.
[Executable format](Dev-Executable-Format.md) lists the fields.

### launcher.h

[`launcher.h`][launcher.h] is shared by `main.c` and `module.c`: it includes `Python.h` (with
`PY_SSIZE_T_CLEAN`) and `payload.h`, and declares `fsb_self` and `PyInit__fsbpyr`.

### vendor/zstd

[`vendor/zstd/`][vendor] holds zstd's public headers `zstd.h` and `zstd_errors.h` (version 1.5.7,
by their `ZSTD_VERSION_*` macros) and zstd's BSD `LICENSE`. Only `payload.c` includes them. The
library itself is not vendored: it is PBS's static `libzstd.a`, linked in by `link_inputs`. The
`LICENSE` is also the zstd licence text that [`runtime/notices.py`][notices] puts into a
runtime's licence notices when PBS ships none.

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

[Testing](Dev-Testing.md) describes them. Among them: the format probe of
`tests/integration/test_launcher_format.py`; the libFuzzer harness
[`tests/fuzz/payload_fuzz.c`][fuzz] (run by `tests/e2e/test_fuzz.py` under ASan and UBSan), which
feeds `fsb_payload_parse`, the record accessors, lookups, path checks and blob decoding any
bytes; the C probes in [`tests/probes/`][probes]; and the glibc tests named above. Apps built
by the end-to-end tests run the rest: `tests/e2e/test_unbuffered.py` reads an app's output
through a pipe with and without `PYTHONUNBUFFERED`; a test of `tests/e2e/test_gc.py` starts an
app with `HOME` unset, empty and relative and expects the background collection in the passwd
home's cache; and a test of `tests/e2e/test_cache_root.py` starts an app that imports a native
extension, with a cache directory that others may write to, and expects the refusal line alone
on stderr, nothing on stdout, and status 70. `tests/unit/runtime/test_toolchain_and_link.py`
checks that `stub_command` passes the runtime id, that the define is outside the hashed flags,
and that the message of a failed link starts the output it shows at a line boundary.

In the EL8 test mode (`FSBPYR_TEST_GLIBC=2.28`, `make test-el8-stub`) the end-to-end tests that
run the stub, the launcher's among them (`test_edges.py`, `test_due.py`, `test_slots.py`,
`test_gc.py`, `test_restore.py`, `test_find_object.py`, `test_cache_root.py`), run with the
glibc 2.28 stub; `tests/e2e/test_el8.py` checks that the default and the EL8 stub each record
the runtime id they were linked as and that the two ids differ, and that a release build
refuses the default stub placed where the EL8 one belongs.

[dir]: ../src/fsbpyr/launcher
[vendor]: ../src/fsbpyr/launcher/vendor/zstd
[main.c]: ../src/fsbpyr/launcher/main.c
[main.c#L128]: ../src/fsbpyr/launcher/main.c#L129
[payload.c]: ../src/fsbpyr/launcher/payload.c
[payload.c#L108]: ../src/fsbpyr/launcher/payload.c#L108
[payload.h]: ../src/fsbpyr/launcher/payload.h
[integrity.c]: ../src/fsbpyr/launcher/integrity.c
[integrity.c#L192]: ../src/fsbpyr/launcher/integrity.c#L192
[parallel.c]: ../src/fsbpyr/launcher/parallel.c
[slots.c]: ../src/fsbpyr/launcher/slots.c
[slots.c#L86]: ../src/fsbpyr/launcher/slots.c#L86
[due.c]: ../src/fsbpyr/launcher/due.c
[due.c#L193]: ../src/fsbpyr/launcher/due.c#L193
[way.c]: ../src/fsbpyr/launcher/way.c
[way.c#L171]: ../src/fsbpyr/launcher/way.c#L171
[way.h]: ../src/fsbpyr/launcher/way.h
[cache.c]: ../src/fsbpyr/launcher/cache.c
[cache.c#L1009]: ../src/fsbpyr/launcher/cache.c#L1009
[cache.h]: ../src/fsbpyr/launcher/cache.h
[module.c]: ../src/fsbpyr/launcher/module.c
[module.c#L336]: ../src/fsbpyr/launcher/module.c#L336
[module.c#L508]: ../src/fsbpyr/launcher/module.c#L508
[module.c#L817]: ../src/fsbpyr/launcher/module.c#L817
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
