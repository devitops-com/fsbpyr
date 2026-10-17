# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Plain `fsbpyr` prints its help and exits 0.** With no command it printed the same help to
  stderr and exited 2, click's status for a usage error; it now prints it to stdout, as
  `fsbpyr --help` does, and still does nothing else.

### Fixed
- **`docs/format.md` gives the format version as 6**, the version executables have carried
  since 0.15.0; its trailer and header tables still said 5.

## [0.19.0] - 2026-10-14

### Added
- **Executables record the uv that built them.** uv builds the project's wheel (its built-in
  backend stamps uv's version into the wheel's metadata) and installs every distribution, so
  the uv version is part of what makes the bytes. The build records it in build_info, and
  `fsbpyr inspect` shows it below Python (`uv` in `--json`). An fsbpyr executable building
  with a uv other than the one that built it warns that the result can differ from one built
  with that uv, even from identical sources.
- **Releases also carry `fsbpyr.xz`**, the same executable compressed with xz (about 14 MB
  instead of 29 MB), attested like the executable itself.
- **`--best-compression` warns when it has nothing to compress.** It compresses native files
  only (extension modules and shared libraries), so a pure-Python app gains nothing from it;
  such a build now says so, and the option's help names what it compresses.
- **`fsbpyr inspect` shows how the content is compressed.** A `compressed` line below the
  size gives the zstd level and, if there are native files, theirs: `zstd 9, native files
  xz -6` after `--best-compression`. Both were already in `--json`'s build_info; executables
  older than 0.4.0 recorded neither and show `unknown`.

## [0.18.2] - 2026-10-14

### Fixed
- **The launcher links reproducibly, so executables do too.** The runtime build copied the
  launcher's sources into a randomly named temporary directory, and that name reached the
  launcher through LTO: every runtime build linked a different launcher, and the same commit
  built on two machines gave different executables. clang now runs inside that directory on
  relative paths, and the cache's own location is mapped out (`-ffile-prefix-map`), so the
  same inputs link the same bytes wherever the cache is. Runtimes are rebuilt once (layout 7).

## [0.18.1] - 2026-10-14

### Added
- **Releases on GitHub, with the executable attached.** A pushed `vX.Y.Z` tag makes GitHub
  Actions build fsbpyr with itself, smoke-test it, attest its build provenance and publish
  it as the release's `fsbpyr` asset, with this changelog's section as the notes
  (`gh attestation verify fsbpyr --repo devitops-com/fsbpyr` checks it). `make release`
  (`VERSION=X.Y.Z` to bump) promotes `[Unreleased]`, commits, tags and pushes.
- **A Makefile: `make bootstrap`, `make build`, `make install`.** `make bootstrap` takes a
  checkout to an installed fsbpyr: on Debian trixie it installs clang/lld 22 and the other
  OS packages with sudo and uv from astral.sh, then builds the runtime and fsbpyr in three
  stages (from source, then twice by the previous stage's executable; every stage must pass
  `inspect --verify` and stages 2 and 3 must be byte-identical); on any other Linux x86-64 the build runs in a Debian trixie podman container
  (`Containerfile`, IPv4-only). `make build` builds `dist/fsbpyr` with the installed fsbpyr
  (bootstrapping first if there is none), and `make install` installs it with its man page
  and bash, zsh and fish completions under `~/.local` (`PREFIX=` for elsewhere).
- **Python 3.14.8 pinned in `.python-version`**, the same CPython the runtime is built from.
- **`fsbpyr man` ends with examples.** The page gains an EXAMPLES section: a command line
  and what it does for every command (`build`, `inspect`, `runtime build`, `cache gc`,
  `completion`, `man`). A test runs each example through fsbpyr's real commands, so an
  example that names a removed option or command fails the suite. The section is set
  left-aligned and without hyphenation, so groff no longer splits `fsbpyr` ("fs-bpyr") in a
  command line or stretches the long ones.

### Fixed
- **`fsbpyr runtime build` retries a download that fails on the network.** The pinned
  CPython archive and the sysroot's packages are tried up to four times, after 2, 5 and
  10 s, when a TLS handshake times out or a connection is reset; an HTTP error such as a
  404 still fails at once.

## [0.18.0] - 2026-10-13

### Added
- **Shell completions and a man page, from the same source as `--help`.** `fsbpyr
  completion bash|zsh|fish` prints a script that asks fsbpyr for the words as you type, and
  `fsbpyr man` prints the roff man page. `--entry` and `--extra` complete from the project
  being built (its console scripts and its extras); a project that cannot be read
  completes to nothing, silently.
- **Every on/off option of `build` has a `--no-` form** (`--source`/`--no-source`,
  `--smoke`/`--no-smoke`, `--offline`/`--no-offline`, ...), so the command line can also
  switch off what `[tool.fsbpyr]` switches on.

### Changed
- **The command line runs on click 8.5.** Commands are listed alphabetically, options can
  no longer be abbreviated, usage errors print `Error: ...` (still exit 2), bare `fsbpyr`
  prints the help (exit 2), and a project path that is a file is a usage error. The cache
  is still collected only when `build`, `inspect` or `runtime build|list` run, never for
  help or a usage error. click adds 4-5 ms to fsbpyr's own startup (`--version` 66.7 ->
  70.6 ms, median of 31).

## [0.17.1] - 2026-10-13

### Fixed
- **Modules whose names start with a digit are packaged as modules.** mypyc puts the code
  its extensions share in a top-level extension named after a hash
  (charset-normalizer imports `81d243bd2c585b0f4821__mypyc`); it was packaged as a
  library, so `import charset_normalizer` failed and requests warned that it had no
  character detection. An extension's name may now start with a digit (a name with a
  hyphen, such as `libbar-2.so`, stays a library), and so may a `.py` module, so
  migrations such as Django's or alembic's `0001_initial.py` are modules too. An older
  `fsbpyr inspect` refuses an executable holding such a name; the launcher never checks
  names, and executables built before read as they did.

## [0.17.0] - 2026-10-13

### Added
- **Dependencies built from sdists above the glibc baseline are rebuilt against the
  glibc 2.28 sysroot.** A dependency without a wheel for the baseline is built from its
  sdist with the build host's compiler and glibc, which used to raise the executable's
  glibc requirement with only a warning (posix_ipc built on a glibc 2.34 host made the
  executable need glibc 2.34). Such a dependency is now built again with the host's gcc or
  clang against the sysroot the launcher is linked with (downloaded once), and uv's cached
  build of it is replaced, so the next build rebuilds nothing. If the rebuild fails (a C++
  extension does: the sysroot has no libstdc++) or the build is offline, the host's build
  is packaged with a warning naming the glibc it needs.

## [0.16.0] - 2026-10-13

### Added
- **Helper interpreters for the app's own children: `--interpreter-reexec`.** In an
  executable, `sys.executable` is the app, so code that starts `sys.executable -m MODULE`
  or `-c CODE` got the app. Built with `--interpreter-reexec` (or `interpreter-reexec =
  true`), such a child runs the module or the code as Python would, interpreter flags
  before it accepted and ignored, but only when its parent process runs the same
  executable: the same command line from a shell, a script or sudo still runs the app with
  these arguments. Off by default, since an app with options of its own named `-m` or `-c`
  would run Python instead.

## [0.15.3] - 2026-10-12

### Fixed
- **Bundled libraries without RUNPATH are found through their loaders' RPATHs.**
  gnureadline 8.3.3 bundles libncurses and libtinfo without an RPATH of their own; the
  dynamic loader finds libtinfo through the RPATH of the extension that loaded libncurses,
  but the build searched only each object's own path and asked for `--allow-system-lib
  libtinfo-3a2cb85b.so.6.1`. The closure search now passes the loaders' RPATH directories
  down each chain as `ld.so` does: an object without RUNPATH searches them after its own
  RPATH; one with RUNPATH does not, but passes them on.

## [0.15.2] - 2026-10-12

### Fixed
- **A dependency's module that the runtime shadows no longer stops the build.**
  gnureadline ships `readline.py` for Pythons without a readline module; the runtime has
  readline built in, which is always found first, so the build failed with "application
  modules shadow the runtime: ['readline']". Such a module of a dependency is now packaged
  as a plain file, with a warning; one of the project's own still fails the build.

## [0.15.1] - 2026-10-10

### Fixed
- **Building with large sparse files is fast again.** 0.15.0 sent every 1 MiB piece of a
  file to a thread, holes included, so the raw build of a sparse 1 TiB file took 65.7 s
  instead of 0.14.0's 26.1 s (peak 318 MiB instead of 91). Holes are now found on the
  calling thread and only pieces with data are read on the threads: 14.2 s, 106 MiB.

## [0.15.0] - 2026-10-10

### Changed
- **Builds, decoding and extraction use up to 8 cores.** Builds compile, canonicalize and
  compress modules on up to 8 worker processes, and the output is the same bytes as on one
  core. Large zstd blobs are stored in pieces of 8 MiB (format v6, codec 5), compressed and
  decoded on up to 8 threads by the builder, the launcher and the Python reader. The
  launcher checks every file of a cache sync first, then writes on up to 8 threads. Measured
  on a torch app: the build takes 6.3-8.3 s instead of 22.75 s, `libtorch_cpu.so` decodes in
  about 100 ms instead of 566, and torch's 12,139 small files extract in 140 ms instead of
  302. Build processes stay at 595 MiB PSS at most. Executables use a new runtime.
- **The runtime build shows every warning** of the link and the stdlib compile, and fails
  on a stack frame over the launcher's limit.

## [0.14.0] - 2026-10-10

### Security
- **No other user can redirect the cache.** What the cache holds is loaded as code, so the
  launcher now walks the cache root's path from `/` a component at a time: every
  directory on it, the root's own included, must be owned by root or the user and writable
  by no other user (not by others, nor by a group other than the user's primary one)
  unless sticky, as `/tmp` is; symlinks must be root's or the user's and are followed one
  at a time (40 at most), their targets checked too. The way is checked again, whole, once
  the root exists. A cache that fails is refused, and the error names the directory at
  fault ("other users may write to it", "owned by another user"). In a user namespace, a
  directory owned outside it counts as root's only where nobody inside maps to the
  overflow uid, and only above any directory others may add to. The cache collector and
  fsbpyr's own cache apply the same rule; `fsbpyr cache gc` adopts the caches that apps of
  earlier versions made.

## [0.13.0] - 2026-10-10

### Added
- **Caches clean themselves.** App caches and fsbpyr's own cache are collected without
  the user: what no process has used for 7 days is removed, and when the file system has
  less than 10% free, everything not in use goes. A collection is due a day after the last
  (an hour when short of space); each start checks that with one `stat` and, when one is
  due, starts a detached process at idle priority, so the start is not slowed. Every
  process holds the trees it uses for its whole life, and stale temporary files go after an
  hour.
- **Only caches marked as fsbpyr's are collected.** A cache root gets the marker
  `.fsbpyr-cache` only when it is new or holds nothing but a cache of fsbpyr's; a
  directory of the user's named by `FSBPYR_CACHE_DIR` is never marked (create the marker
  yourself to have it collected). Only what fsbpyr made there is ever removed.
- **`fsbpyr cache gc` and `FSBPYR_GC`.** `fsbpyr cache gc` collects fsbpyr's cache and
  every app cache now; `FSBPYR_GC=1 app` collects that app's cache in the foreground
  instead of running the app, and `FSBPYR_GC=0` never collects in the background. fsbpyr
  checks its own cache (runtimes, Python distributions, sysroots, downloads) once a day,
  keeping what it builds with.

## [0.12.0] - 2026-10-09

### Changed
- **Builds stream: no file's bytes are held in memory, whatever their size.** Large files
  are compressed from disk a piece at a time into a temporary spill file next to the
  output, native libraries are read a window at a time, and the writer streams blob data
  and hashes the result on 8 threads. A file that changes while the build reads it fails
  the build instead of shipping other bytes. Building a torch app took 1204 MB of peak
  RSS (1869 MB with `--best-compression`); now 243 MB and 641 MB, in the same time. A raw
  sparse 1 TiB file was built, checked, streamed and extracted with each process under
  1 GB. A module over 16 MiB is kept as data.
- **The launcher reads large raw blobs from the file instead of its mapping**, so a sparse
  executable on tmpfs cannot exhaust memory and end with SIGBUS; read errors are reported
  as `OSError`, not as corrupt data.

## [0.11.0] - 2026-10-09

### Changed
- **Extraction and reads stream, with no size limits of fsbpyr's own.** Blob content is
  decoded at most 64 MiB at a time for every codec, and extraction decodes, hashes and
  writes a window at a time under a temporary name, renamed once the digest is right;
  pieces of zeros become holes. The 1 GiB cap on blobs goes (up to 2^62 bytes), and a
  path's length and depth have no limit of fsbpyr's. Resources over 16 MiB opened as files
  stream and seek. Extracting 1.5 GiB + 600 MiB + 80 MiB stays within 512 MiB of added
  resident memory.

### Fixed
- **SIGPIPE and SIGXFSZ no longer end the process.** The launcher now installs signal
  handlers as `python` does, so a write to a closed pipe or socket raises
  `BrokenPipeError` and a write past `ulimit -f` fails with `EFBIG`.

## [0.10.0] - 2026-10-09

### Changed
- **Hardened launcher build.** The launcher is built with `-Wvla` and
  `-Wframe-larger-than=8192`, its payload parsing and decoding are fuzzed with libFuzzer,
  and table-driven edge cases (65 mutations of an executable, 14 invalid paths) run against
  both the launcher and the Python reader, with an ASan/UBSan runtime for the tests.

### Fixed
- **A cached file whose name the file system refuses fails with its real error.** A
  256-byte path component used to be reported as modified after extraction. Any other
  error opening a cached file has the file replaced, and `EINTR` is retried.

## [0.9.1] - 2026-10-09

### Fixed
- **Much lower memory use when checking and restoring cached files.** The executable's own
  check mapped the whole file (226 MB resident for a 233 MB torch app), and the cache check
  mapped up to 546 MiB of cached files at once; both now read through a 64 KiB buffer per
  thread. Restored files are written in pieces of at most 1 MiB, so XFS no longer keeps
  them as 2 MiB folios that the dynamic loader maps whole. The torch app's peak RSS fell
  from 577 MB to 349 MB (its venv: 314 MB). A file that shrinks while it is hashed now
  fails its check instead of ending the process with SIGBUS.

## [0.9.0] - 2026-10-09

### Changed
- **The launcher walks, checks and restores the native cache in C.** Cached files are
  checked (a regular file of the user's, of the payload's size and digest) and missing or
  changed ones restored without the GIL, creating directories below the cache root one
  component at a time, never through a symlink, owned by the user and forced to 0700. On a
  torch app, later runs import in 538 ms instead of 615 (venv: 470), and the check of its
  12,151 cached files takes 53.5 ms instead of 80.

## [0.8.0] - 2026-10-09

### Added
- **`--best-compression` stores native libraries as xz.** With `--best-compression` (or
  `best-compression = true`) native files are compressed with xz -6 and the x86 filter,
  libraries over 4 MiB in 4 MiB pieces that the launcher decodes in parallel (format v5).
  zstd 9 stays the default, and the option excludes `--fast-compression` (a flag on the
  command line replaces the other one set in the project). aiagent: 149.7 -> 140.0 MB,
  build 18.5 -> 21.1 s, first runs +25 to +76 ms, later runs unchanged.
- **torch is a built-in fs-package**, since it runs `bin/torch_shm_manager` by a path next
  to its `__file__`.

### Changed
- **Cached files are written without fsync, and every process checks each cached file
  once.** A torch app's first run fell from 42.5 s to 1.34 s, its cache from 1.2 GB to
  709 MB, and later runs from 895 to 620 ms.

## [0.7.0] - 2026-10-09

### Changed
- **Files extracted to the cache are checked with the executable's digest.** They are
  checked with the same piece-wise SHA-256 as the executable (format v4), on the same
  threads. The startup budget, the integrity check aside, is 1.2x bare CPython. Cache check
  overhead against size-only checks: numpy +4-5 ms, cryptography +1.3 ms.

### Fixed
- **No long wake-up delay on hybrid CPUs.** A thread joining the check could wake on an
  efficiency core while numpy's OpenBLAS filled every core (+38 ms); the caller now spins
  for up to 2 ms before it sleeps (+4-5 ms).

## [0.6.0] - 2026-10-08

### Added
- **Every executable checks its whole file before it starts.** The trailer ends with a
  SHA-256 over the SHA-256 of each 1 MiB piece of everything before it (format v3). The
  launcher checks it on up to 8 threads before reading anything else, and exits 70 with
  "integrity check failed: the file was modified or damaged" on a mismatch. Cost with the
  file in the page cache: 2.0 ms for 28 MB, 8.9 ms for 148 MB; `FSBPYR_TRACE` reports it as
  `integrity_ns`. Standard tools (`split --filter`, `openssl dgst`, `sha256sum`) reproduce
  the digest. This detects damage and changes that leave the digest alone, not a change
  that also rewrites it.
- **`fsbpyr inspect` shows the digest** (JSON: `digest`, `digest_covers`), and `--verify`
  recomputes it.

### Changed
- **Cached files are hashed by every process, once, before first use**; a changed one is
  reported on stderr and restored. `FSBPYR_VERIFY` and the retry after a failed `dlopen`
  are gone. `fsbpyr build` verifies its own file before copying its runtime out of it, and
  refuses executables without a whole-file digest.

## [0.5.0] - 2026-10-08

### Changed
- **Small blobs are packed into shared zstd blocks (format v2).** Every non-empty blob under
  64 KiB except native files is a member of a block: 64 KiB blocks for code, 256 KiB for
  resources, metadata and sources; the launcher keeps the last 32 decompressed blocks.
  aiagent: 157.4 -> 149.6 MB for about +5 ms on import-heavy starts. `--fast-compression`
  also sets the level of every block. Readers accept format versions 1 and 2.

## [0.4.0] - 2026-10-08

### Changed
- **zstd level 9 by default; `--fast-compression` keeps level 3.** Every blob, the
  runtime's stdlib included, is compressed at level 9 (a new runtime);
  `--fast-compression` (or `fast-compression = true`) keeps level 3 for the app's content,
  and the build info records the level. aiagent: 167.7 -> 157.4 MB, build 14.4 -> 18.0 s;
  decompression speed does not depend on the level.

## [0.3.0] - 2026-10-08

### Added
- **`fsbpyr inspect` lists libraries and modules.** It lists bundled shared libraries and
  native extension modules, and the top-level module names per distribution, the standard
  library's and fsbpyr's, in as many columns as the terminal fits. `--all-modules` lists
  every module, `--json` prints the same as JSON, and `--verify` checks the payload's
  SHA-256.

### Changed
- **The "reads files next to `__file__`" guess is opt-in.** It was mostly false alarms;
  `fsbpyr build --hints` shows it.

## [0.2.0] - 2026-10-08

### Added
- **`fsbpyr inspect BINARY` shows what an executable was built with**: the app's name,
  version and commit, the fsbpyr that built it (as its `fsbpyr --version` printed), Python
  and glibc, size and distribution count. Builds now record the project's name and version
  and the fsbpyr version; older builds show what they recorded. Corrupt build information
  or a non-regular file is a clean error.

## [0.1.1] - 2026-10-08

### Fixed
- **Libraries that extensions open by name are extracted.** onnxruntime's extension opens
  `libonnxruntime_providers_shared.so` with `dlopen` and no DT_NEEDED entry, so it was never
  extracted and every import printed "Init provider bridge failed". A bundled library in an
  object's directory or on its `$ORIGIN` search path now joins the closure when the object
  names its file. One whose own needs cannot be met (onnxruntime-gpu's CUDA provider without
  a bundled CUDA runtime) is left out with a warning instead of failing the build, and
  "loaded by no extension module" is reported only when some packaged Python code names the
  library.

## [0.1.0] - 2026-10-07

### Added
- **`fsbpyr build` turns a Python CLI project into one executable.** The executable carries
  CPython 3.14, the project and its locked dependencies, installed fresh from `uv.lock`,
  `pylock.toml`, a requirements file or an unlocked project (pyproject or setup.py, uv
  workspaces), configured through `[tool.fsbpyr]` or options such as `--entry`, `--extra`,
  `--requirements`, `--offline` and `--no-source`. Builds are reproducible byte for byte,
  and a new executable runs once as a smoke test.
- **Modules are imported straight from the file.** A static, LTO-linked, PIE CPython
  launcher with an appended zstd payload validates it, imports the stdlib with no file
  system probes and serves the app's modules, resources, `pkgutil` and
  `importlib.metadata` from memory: a no-op app starts in about 4.95 ms against 4.84 ms for
  bare `python3.14 -I -S`.
- **`fsbpyr runtime build` and `runtime list`.** The runtime is built from a pinned,
  SHA-256-checked python-build-standalone release with clang and lld 22 or newer, into a
  private cache. fsbpyr builds itself into a single executable, which reuses its own
  runtime and needs no compiler, download or cache to build apps.
- **Native extensions and the native cache.** Extension modules and the shared libraries
  they need (through `$ORIGIN` paths, against the manylinux system-library allowlist plus
  `--allow-system-lib`) are packaged with digests and extracted on first import into
  `${XDG_CACHE_HOME:-~/.cache}/<app>` (or `$FSBPYR_CACHE_DIR`), safely and race-free; only
  the closure an import needs is extracted. `FSBPYR_TRACE=1` prints a JSON summary of what
  a run loaded and extracted.
- **A compatibility layer for code written for installed packages.** multiprocessing works
  with every start method; packages that need real files (`--fs-package`, with built-in
  rules for certifi, babel, botocore, matplotlib and litellm) are extracted to the cache;
  resources asked for as paths (`importlib.resources.as_file()`, `os.fspath()`) are
  extracted on demand; `.pth` path lines, `inspect.getsource` on frozen stdlib modules and
  a CA bundle fallback when OpenSSL finds no store are handled.
- **Portable to glibc 2.28.** The launcher is linked against a pinned glibc 2.28 sysroot
  and uv selects wheels for `manylinux_2_28`; the build reports the executable's glibc
  requirement and warns when a native file needs more. Executables run on 13 distributions
  from Debian 10 and Rocky 8 to Fedora and Arch.
- **uv is required on the build host.** uv installs every lock source with the runtime's
  platform; the pip fallback is gone, since pip-installed builds picked wheels for the
  build host's glibc.
- **Versions from `vX.Y.Z` git tags.** `fsbpyr --version` prints `X.Y.Z-<8 hex>` for a
  build of a tag and `untagged-<8 hex>` otherwise; `fsbpyr build` records the project's
  commit and tag when git is available, and a git failure is only a warning.
