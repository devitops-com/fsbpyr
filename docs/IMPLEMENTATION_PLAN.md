# fsbpyr — Implementation Plan

Status: v1.18, 2026-10-18 (fsbpyr 0.20.0). Phases 0–8, 6b and 6c are complete (Phase 7 reduced by D37; Phase 8 in six steps, 8a–8f) and Phase 10, the MVP's last, is done; Phase 9 (self-update first, then more Python versions with one fsbpyr per version, then Linux aarch64 and musl and macOS on Apple Silicon; no Windows) and signed releases come after the MVP (owner decision 2026-10-18); uv is required (D26); the CLI runs on click, which also gives the completions and the man page (D40); resources reached as paths are extracted on demand (D27); versions come from `vX.Y.Z` git tags (D28); Phase 0 findings are in `docs/spikes.md`. Requirements come from `docs/python_cli_runtime_manifest.pdf`
("the manifest") plus the owner requirements R1–R3 below. Items marked **[verified]** were
checked on the dev machine on 2026-10-06. Items marked **[assumption]** are not yet verified;
each one is covered by a Phase 0 spike or a test.

---

## 0. Requirements

| ID | Requirement | Source |
|----|-------------|--------|
| R1 | Any Python project maintained with uv or pip in a standard venv becomes a single executable with **one command**: `fsbpyr build` | owner |
| R2 | fsbpyr produces its own binary (self-hosting) | owner |
| R3 | Bootstrap: the first version runs as plain Python in a standard venv. From then on, the fsbpyr binary builds itself from its own Python sources | owner |
| M1 | One executable. Only the OS loader, glibc and explicitly allowed system libraries stay external | manifest §1–2 |
| M2 | Startup work scales with the executed import path, not with the size of the dependency set | manifest §3 |
| M3 | zstd frames that can each be addressed on their own. No whole-file compression. Resources that are never requested are never decompressed | manifest §4 |
| M4 | Pure-Python modules execute from memory and are never extracted | manifest §5 |
| M5 | Native extensions go into a content-addressed, immutable, self-healing cache on first import and are loaded by the normal OS loader | manifest §6–7 |
| M6 | Package data is decompressed in memory. Data that needs filesystem semantics is materialized lazily | manifest §8 |
| M7 | Security and integrity rules | manifest §11 |
| M8 | Benchmarks and success criteria | manifest §13–14 |

**[assumption → implemented, Phase 4] A1:** "single command" means `fsbpyr build [PROJECT_DIR]`,
run inside the project or pointed at it. Output goes to `dist/<console-script-name>` (`cli.py`:
PROJECT defaults to `.`; `build.py`: `dist/<app name>` unless `-o`).

---

## 1. Verified facts (probed 2026-10-06)

- **[verified]** python-build-standalone (PBS) release `20261003` provides CPython 3.14.8 as
  `x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst` (111 MB). The archive contains:
  - `python/install/lib/python3.14/config-3.14-x86_64-linux-gnu/libpython3.14.a` (72 MB)
  - static `libssl.a libcrypto.a libsqlite3.a libffi.a libz.a libzstd.a libbz2.a liblzma.a
    libmpdec.a libexpat.a libuuid.a libncursesw.a libedit.a …` in `python/build/lib/`
- **[verified]** Those objects are **LLVM IR bitcode**
  (`PYTHON.json: object_file_format = "llvm-bitcode:22.1.3"`). Linking them needs
  **clang + lld ≥ 22**. gcc cannot link them.
- **[verified]** `PYTHON.json` contains the following, which is enough to drive a static link
  the way PyOxidizer did:
  - `crt_features: glibc-max-symbol-version:2.17`
  - `python_symbol_visibility: global-default`
  - for each extension: its objects, links and `init_fn`
  - `inittab_object` and `inittab_source`
- **[verified]** uv-managed CPython 3.14.7 (PBS install_only) has 100 built-in extension modules,
  including `_zstd` (zstd 1.5.7), `_ssl`, `_hashlib`, `_sqlite3` and `_ctypes`. Only `_dbm` and
  `_tkinter` are shared objects.
- **[verified]** `python3.14 -I -S -X utf8 -c pass` imports only four modules that are neither
  built-in nor frozen: `encodings`, `encodings.aliases`, `encodings.utf_8`, `linecache`.
- **[verified]** These pieces of the CPython 3.14 public embedding API are present:
  - `struct _frozen {name, code, size, is_package}`
  - `PyImport_FrozenModules`
  - `PyImport_AppendInittab`
  - the `PyConfig` fields `use_frozen_modules`, `module_search_paths_set`, `safe_path`,
    `import_time`, `utf8_mode`, `parse_argv` and `site_import`
  - `Py_RunMain`

  `_Py_InitializeMain` is **not** in the 3.14 public headers, so two-phase init is not
  available. We inject a frozen-module table instead.
- **[verified, S2]** CPython consults a user-set `PyImport_FrozenModules` before its own frozen
  stdlib set.
- **[verified]** Baseline startup on the dev machine (Intel Core Ultra 9 285, warm cache, p50 of
  40 runs):

  | Command | p50 |
  |---------|-----|
  | `/bin/true` | 0.22 ms |
  | `python3.14 -I -S -c pass` | 4.83 ms |
  | `python3.14 -I -c pass` | 6.47 ms |
  | `python3.14 -I -c "import argparse"` | 10.87 ms |

- **[verified]** Host: Debian 13, glibc 2.41, gcc present, **clang absent**, podman present,
  uv 0.12.17, uv-managed CPython 3.11–3.14 installed, pip 25.1.1. Prerequisite for Phase 0:
  install `clang-22` and `lld-22` (or newer) from apt.llvm.org.
- **[verified]** uv 0.12.17 provides `uv sync --frozen --no-dev --no-editable`,
  `uv export --no-editable --no-emit-project`, and
  `uv pip install --target/--python-platform/--python-version/--require-hashes`, and it reads
  `pylock.toml`. pip 25.1.1 provides
  `install --target/--platform/--python-version/--abi/--only-binary/--require-hashes`.
- **[verified, Phase 4]** `UV_PROJECT_ENVIRONMENT` redirects `uv sync` to a staging venv. This is
  documented uv behaviour; `staging.py` stages every `uv.lock` build through it, self-hosting
  included.

---

## 2. Key decisions

| # | Decision | Rationale | Rejected alternatives |
|---|----------|-----------|-----------------------|
| D1 | The native launcher is written in **C** (about 800 LOC) | libpython is C and needs clang anyway (D2). This gives minimal native work at startup | Rust: adds cargo for code that mostly calls C APIs |
| D2 | **CPython is statically linked into the launcher** from PBS `pgo+lto-full` (clang/lld ≥ 22, `--target=x86_64-unknown-linux-gnu`, `-flto=full`; only `Py*`/`_Py*` are exported (`--export-dynamic-symbol`, Phase 5) so manylinux wheels resolve the C API from the executable but never bind to the stub's static zlib, OpenSSL or libffi). Link inputs: libpython.a, the HACL objects of modules registered in `config.c`, and the static libraries listed in `PYTHON.json` (S1) | Runtime is directly loadable (manifest Fig. 1). PGO+LTO speed. PBS already handles the static dependencies and the glibc 2.17 baseline | Shared libpython extracted to the cache (extracts the runtime on first run). memfd + dlopen (copies MBs on every start). Building CPython and its dependencies ourselves (large effort) |
| D3 | A **runtime** is: the *stub* (launcher + static CPython, an ELF) + a precompiled *stdlib payload fragment* + `runtime.json`. It is built once per (Python minor, platform, launcher version) and cached | App builds become concatenation: no compiler and no network needed | Compile per app |
| D4 | **Every fsbpyr binary carries its own runtime.** When `fsbpyr build` targets the same Python minor, it reuses its own stub bytes and the already-compressed stdlib frames from its own executable (`Runtime.from_self()`) | Makes R2/R3 work without a toolchain. Fast builds | — |
| D5 | **Inputs come from a fresh staging install driven by the project's lock** (§3.6). uv projects: `uv sync --frozen --no-dev --no-editable` (`--locked` since Phase 4) into a throwaway staging venv. pip projects (installed by uv, D26): `uv venv` plus `uv pip install` into a throwaway venv from `pylock.toml`, then a pinned requirements file, then `[project.dependencies]` (unlocked, with a warning), plus the project itself installed `--no-deps`. The developer's venv is never read or modified | Reproducible from the lock. No dev dependencies. The project is installed non-editable, so its package data is correct. uv does all resolving and building (D26) | Reading the developer's venv (not reproducible, editable installs, dev deps). Our own resolver |
| D6 | Imports hook in through **`sys.path_hooks` + one virtual `sys.path` entry** (as zipimport does), plus a `sys.meta_path` `DistributionFinder` for `importlib.metadata` | `pkgutil.iter_modules`, `importlib.util.find_spec` and PEP 420 namespace packages work through the stock PathFinder | Pure meta-path finder |
| D7 | **Index lookups happen in C** (built-in module `_fsbpyr`, binary search over sorted tables mapped with mmap). The index is never turned into Python dicts | Startup must not scale with the number of packaged files (M2) | Python dict built at boot (O(N) on every start) |
| D8 | **Startup-critical modules** (`encodings*`, `linecache`, the boot module) are listed in the STARTUP section. The launcher decompresses them once (checksum-verified zstd frames, one shared context, about 0.08 ms) and hands them to CPython through a `PyImport_FrozenModules` table built at run time | No filesystem access during init. No launcher rebuild when boot code changes. `marshal` never sees corrupt bytes: the Phase 2 review showed raw startup blobs crashed CPython on bit flips | Raw blobs referenced straight from the mmap (zero-copy but unverified). Freezing into the C source |
| D9 | The **native cache mirrors the site-packages layout** inside the generation directory (`<gen>/site/numpy/_core/*.so`, `<gen>/site/numpy.libs/*`) | auditwheel's `$ORIGIN`-relative RUNPATHs work unchanged, so no ELF patching is needed | Flat content-addressed files + patchelf |
| D10 | **Generation id** = SHA-256(runtime ABI ‖ platform ‖ sorted (path, sha256) of every extractable artifact), computed at build time and stored in the header | Manifest §7. Releases that change only Python code reuse the existing generation | Per-build generations |
| D11 | **Integrity:** SHA-256 for extractable artifacts, verified on extraction. zstd frame checksums for blobs kept in memory. Whole-payload SHA-256 in the trailer, checked only by `fsbpyr inspect --verify` or `FSBPYR_VERIFY=1` (superseded by D31: the whole file, on every start) | Meets manifest §11 without hashing on every import | — |
| D12 | **Sources are kept** by default as separate zstd frames, read only for tracebacks and `inspect`. `--no-source` drops them | Size may grow, startup may not. Gives full tracebacks | — |
| D13 | **fsbpyr itself** requires Python ≥ 3.14 (≥ 3.12 in Phase 9: one fsbpyr per Python version). Its only runtime dependency is `packaging` (and `click` since 0.18.0, D40). zstd comes from the stdlib `compression.zstd`, TOML from `tomllib`. Capped below the next minor (owner decision 2026-10-19): `requires-python = ">=3.14,<3.15"`, since canonical bytecode knows one marshal format (D16): `bytecode.dumps` raises on any other version, and a build reaches it only after staging. `uv pip install` and `uv tool install` ignore the cap, so `fsbpyr build` and `fsbpyr runtime build` also stop on any other Python before they do anything else. Phase 9's port to 3.12 makes it `>=3.12,<3.15`, and the cap rises whenever fsbpyr moves to a newer Python | Small self-hosting surface. fsbpyr is pure Python, so self-hosting does not depend on native-extension support | `zstandard` package (a native dependency) |
| D14 | **Interpreter config in built apps:** isolated (`PYTHON*` env vars ignored), `site_import=0`, `safe_path=1`, UTF-8 mode, `parse_argv=0`, `executable` = realpath of the binary. Debug knobs only through `FSBPYR_*` env vars | Deterministic startup. The app's argv stays untouched | — |
| D15 | **Deterministic builds:** sorted entries, fixed blob order, no timestamps, single-threaded zstd with fixed parameters, canonical bytecode (D16); output is byte-identical for a fixed libzstd version, which `build_info` records. Self-hosting fixed point: stage2 == stage3, byte for byte | The classic compiler-bootstrap check for R2/R3 | — |
| D16 | **Canonical bytecode:** every `marshal.dumps` result is rewritten by `fsbpyr/bytecode.py`. Strings are merged by value (interned if any occurrence was), FLAG_REF is set only for nodes that occur ≥ 2 times or are interned strings, refs are renumbered in pre-order, and set elements are sorted canonically | Raw marshal output depends on refcounts and on process-global interning (S3: 561/561 files differ in batch mode, 4 in a "noisy" build process). Canonical output: 0 differences, round-trip equality, same size and load time | Compiling in a clean worker process (still state-dependent). Accepting non-determinism |
| D18 | **Launcher C sources ship as package data** (`src/fsbpyr/launcher/`, with vendored zstd 1.5.7 headers) and are hashed into the runtime id | A self-hosted fsbpyr binary can build runtimes for new Python versions from its own resources; any launcher change yields a new runtime id automatically | Top-level `launcher/` outside the package |
| D17 | **PIE stub** (owner decision 2026-10-07, after the Phase 2 security review) | ASLR for the launcher and CPython image, the distro baseline. Costs about 0.3 ms per start (S1); measured no-op app 4.95 ms vs 4.84 ms bare CPython, inside the 1.15× budget. Also hardened with RELRO, BIND_NOW, NX stack, `-fstack-protector-strong`, `_FORTIFY_SOURCE=2`. A non-PIE stub may return as an opt-in for the Phase 7 `fast` profile (not done: D29 replaced the profiles, D37 ended the tuning; the stub is always PIE) | Non-PIE by default (0.3 ms faster, fixed image addresses) |
| D19 | **Code blobs are always checksummed zstd frames** (`Blob.code`); every reader requires the frame's content-checksum flag | `marshal.loads` is not safe against corrupt input. Fuzzing 900 bit flips: 67 SIGSEGV + 4 SIGABRT + a hang before, 0 signals after (clean exit 70 or `PayloadError`) | Raw storage of small code objects |
| D20 | **The cache is private:** cache directories must be owned by the user and are forced to 0700; `runtime.json` records SHA-256 of the stub and the stdlib fragment (the tree digest since D32), verified on load; a concurrently completed runtime wins | Cached stubs are copied into every executable built from them | Trusting any directory named by `FSBPYR_CACHE_DIR` / `XDG_CACHE_HOME` |
| D21 | **`sys.frozen` is not set** | It is PyInstaller's flag: code that sees it reaches for `sys._MEIPASS` or other PyInstaller layouts. multiprocessing would use it only to shorten the spawn command line, which D22 handles anyway | `sys.frozen = True` for compatibility with PyInstaller-aware code |
| D22 | **multiprocessing children are recognized and authenticated, never executed:** the three command lines CPython re-executes `sys.executable` with (spawn `-c ... --multiprocessing-fork`, forkserver `-c ... main(...)`, resource tracker `-c ... main(fd)`) are matched exactly and their arguments decoded with `ast.literal_eval`. The known function is called only if the parent process runs this same executable (same inode of `/proc/<ppid>/exe`), every fd argument is ≥ 3, and nothing an fsbpyr parent never sends is asked for (a forkserver `main_path`; its `sys_path` is dropped) | Every start method works, and an app that may run with untrusted arguments (a sudo rule) does not become a Python interpreter. Recognition alone was not enough: the security review ran code through `spawn_main(pipe_handle=0)` unpickling stdin and through forkserver's `sys_path`/`main_path` | `exec` of any `-c` command that starts with the right prefix (PyInstaller's approach) |
| D23 | **Interpreter re-exec is opt-in and authenticated** (owner decision 2026-10-07): with `interpreter-reexec = true`, `<exe> [flags] -m MODULE ...` and `-c CODE ...` run as CPython would, but only when the parent process runs this same executable (the D22 check); otherwise the app runs as usual | Tools that start helper interpreters through `sys.executable` work. Off by default, because an app's own CLI may use `-c`/`-m`; callers from a shell or sudo never get an interpreter | Always on; keeping it a non-goal |
| D24 | **A runtime hint for `__file__`-relative data** (owner decision 2026-10-07): an `open` audit hook turns failures on paths inside the executable into an error that names the package and `fs-packages`. It ships only if measured overhead is below 1%; build-time warnings ship regardless (Phase 6b: the hook cost +1.5%, so the hint was dropped; the warnings shipped) | Field failures become self-explanatory | Build-time warnings only |
| D25 | **The portable glibc baseline moves from Phase 9 to Phase 6c** (owner decision 2026-10-07) | glibc 2.38 excludes Debian 12, Ubuntu 22.04 and RHEL 8/9; benchmarks (Phase 7) should measure the stub that ships | Leaving it in Phase 9 |
| D26 | **uv is required on the build host** (owner decision 2026-10-07): it installs every project, including pip projects (requirements.txt, unlocked); a build without uv on PATH fails with installation instructions. The pip fallback and `--interpreter` are gone | Only uv selects wheels for the runtime's glibc baseline (`--python-platform`); measured: a pip build of `heavy` picked cryptography's manylinux_2_34 wheel and failed on Debian 10 and Rocky 8 | Bundling uv inside fsbpyr, or pinning and downloading one (owner decision 2026-10-14: users update uv without updating fsbpyr; uv's version reaches the executable through the wheel metadata its build backend writes, so builds record it in build_info, `fsbpyr inspect` shows it, and an fsbpyr executable building with a uv other than the one that built it warns that the bytes can differ); a wheels-only pip path; keeping pip with a warning |
| D27 | **Resources reached as paths are extracted on demand** (owner decision 2026-10-07): `importlib.resources.as_file` on a payload resource yields its copy in the app cache and never deletes it; `os.fspath()`/`str()` of a payload Traversable extract the file or directory and return that path. The copies live in `<cache>/resources/<resources id>/site/<path>`, keyed by every payload file (`build_info["resources"]`), not in the generation's tree, which releases share (D10): a release never sees another release's files. Each path is extracted once per process. The payload root is refused; an unusable cache raises from `as_file`/`os.fspath` (naming `FSBPYR_CACHE_DIR`) and makes `str()` warn and keep the virtual path. Extracted copies stay on disk until cache GC (Phase 8d, 0.13.0), like native files. Unlike the stdlib's temporary copy they are read-only (files 0400, directories 0700), shared by the app's processes, and under `--no-source` hold no `.py` sources; `str(files(pkg))` names the cache while `pkg.__file__` stays inside the executable | Code written for installed packages keeps `as_file` paths after the block (aiagent's skill discovery) or hands `str(files(pkg) / dir)` to `os` functions; with this, such code needs no configuration. Only what is asked for is extracted, with the same safe publishing as native files | Temporary copies (stdlib default: the path dies with the block); requiring `fs-packages` for every such package; one tree shared with natives (stale or removed files of other releases would show) |
| D28 | **Versions come from git tags** (owner decision 2026-10-07): releases are tagged `vX.Y.Z`, starting with v0.1.0 (a GitHub release system comes later; since 0.18.1 a pushed tag makes `.github/workflows/release.yml` build, attest and publish the release, `fsbpyr.xz` as well since 0.19.0). A fix to an existing feature bumps the patch level (0.1.1, 0.1.2, ...), a new feature the minor version (0.2.0, 0.3.0, ...), a major rewrite the major version; `[project] version` follows the tag. `fsbpyr build` records HEAD's commit and the highest `vX.Y.Z` tag pointing at it in build_info (`git`) for every project in HEAD's tree; `fsbpyr --version` prints `X.Y.Z-<8 hex>` for a tagged commit and `untagged-<8 hex>` otherwise, and `untagged-unknown` when nothing was recorded (no git, an sdist, a project copied into some other repository, a wheel installed outside a checkout). A source checkout asks git directly; a git failure such as "dubious ownership" is a build warning. Since v0.2.0 the build also records the project's name and version and the full version of the fsbpyr that built it, and `fsbpyr inspect BINARY` shows them: app name, version and commit; `built with fsbpyr X.Y.Z-<hash>`; Python and glibc; size and distribution count. Since v0.3.0 it also lists the bundled shared libraries and native extension modules (a plain `*.so` is told apart by `PyInit_<name>`, searched streaming, so a hostile file cannot make it allocate its declared size) and the top-level module names per distribution plus those of the standard library and fsbpyr (`--all-modules`: every module), each list in as many columns as the terminal fits; `--json` prints the same as JSON (`modules` per distribution, `runtime_modules`, `all_modules`), `--verify` checks the payload SHA-256 (D11; the whole-file digest since 0.6.0, D31), which detects damage, not deliberate tampering (the digest sits in the same file). Uncommitted changes are not marked | The executable has no git, and the project wheel is built from an sdist without `.git`, so the commit must be captured by the build. git stays optional: without it a build only lacks the commit | A VCS build-backend plugin (hatch-vcs, setuptools-scm): a second backend and PEP 440 versions (`0.1.1.dev3+g6457a95`) instead of the requested format |
| D29 | **zstd level 9 by default; `--fast-compression` keeps level 3** (owner decision 2026-10-08; replaces the `fast`/`balanced`/`compact` profiles). Applies to every blob, the runtime's stdlib frames included (LAYOUT_VERSION 4); `--fast-compression` and `build_info["zstd_level"]` concern the app's content and, since D30, every block (small stdlib files and boot modules included); the stdlib frames left outside blocks are always level 9 | Measured on aiagent's 405 MB of content, per-file: level 3 144.6 MB in 1 s, 9 134.4 MB in 4 s, 19 126.3 MB in 62 s, 22 126.1 MB in 77 s; decompression speed does not depend on the level. The whole aiagent build goes from 14.4 s (`--fast-compression`) to 18.0 s and from 167.1 MB to 157.4 MB (0.3.0, level 3 everywhere: 167.7 MB) | Levels 19/22 (15–20× the build time for 6% more); profiles. A two-stage stub (a small loader that unpacks the 20.9 MB launcher + CPython, 6.9 MB compressed) was evaluated and rejected: unpacking costs about 23 ms per start (hello starts in 5.7 ms) or, if cached, needs a writable exec-capable cache for every app and makes a user-writable file run on every start |
| D30 | **Small blobs are packed into shared zstd blocks** (format v2; owner decision 2026-10-08): every non-empty blob under 64 KiB except natives, keyed by content (raw or zstd, it is one member), in one stream per placement class; 64 KiB blocks for code, 256 KiB for resources, metadata and sources; readers keep the last 32 decompressed blocks. A member record (codec 2) names its block and its position | Measured on aiagent: 157.4 → 149.6 MB (−7.8 MB, −5%) for +5 ms on 130–170 ms import-heavy starts (+3–4%); hello unchanged. Variants measured: 32/256 KiB −6.4 MB +3 ms, 32/64 KiB −4.6 MB +2 ms, code unblocked −4.1 MB +2.5 ms. With 8 cached blocks, imports alternating between packages decompressed 36.5 MB instead of 16 MB. The runtime's stdlib fragment stays unblocked (apps pack it into their own blocks), and the resources id hashes content, not its representation | A zstd dictionary (−6.3 MB on small blobs, but a launcher change for less gain); profile-guided ordering of code by import order (not tried: a second build pass, and reproducibility would depend on a stable import order); 256 KiB code blocks (−10.2 MB, +17 ms) |
| D31 | **Every executable checks its whole file before it starts** (owner decision 2026-10-08; format v3; replaces D11's "whole-payload SHA-256 checked only on demand"). The trailer ends with a SHA-256 over the SHA-256 of each 1 MiB piece of everything before it; the launcher checks it before reading anything else, on min(8, usable cores) threads (one per 4 pieces) with the static OpenSSL's low-level SHA-256 (not the one-shot `SHA256()`, which since OpenSSL 3 sets up EVP and may read `openssl.cnf`), and exits 70 on a mismatch. Files extracted to the cache are hashed by every process, once, before it first uses them (`FSBPYR_VERIFY` is gone); a changed one is reported on stderr and restored. `fsbpyr build` checks its own file again before copying the runtime out of it. Detects damage and any change that leaves the digest alone; a change that also rewrites the digest or removes the check needs a signature checked with an outside key (owner decision: with the release system, later; not MVP, owner decision 2026-10-18). The startup budget (§3.3) now excludes the check | Measured in the launcher, file in the page cache (Core Ultra 9 285): 28 MB 2.0 ms (one core 6.9 ms, no SHA-NI 7.1 ms), 148 MB 8.9 ms (38 ms, 30 ms); through EVP the 28 MB check took 3.4 ms. The no-op app goes from 6.0 to 8.1 ms against 5.1–5.4 ms bare CPython; without the check it is at 1.11–1.17×. mmap beats pread (21 MB: 2.2 vs 3.1 ms); the piece size hardly matters (64 KiB to 1 MiB); 8 threads cost more than 5–6 on files under ~40 MB. Standard tools reproduce the digest (`split --filter`, `openssl dgst`, `sha256sum`) | Plain SHA-256 on one thread (31 ms per 150 MB with SHA-NI, 180 ms without); BLAKE3 (4.6 ms per 150 MB on 8 threads, but vendored C with hand-written threading and a native package for inspect); caching a verified state by inode and ctime (misses damage on disk, which changes neither); signing now (later, with releases) |
| D32 | **Files extracted to the cache carry the executable's digest construction** (owner decision 2026-10-09; format v4): DIGESTS holds the SHA-256 over the SHA-256 of each 1 MiB piece, so the per-process cache check runs on the same threads as the executable's. One routine per language: `fsb_tree_digest(s)` in the launcher (the executable check, and `_fsbpyr.tree_digests` for the cache) and `tree_digest` in `format.py` (builder, reader, the runtime cache's records: LAYOUT_VERSION 5); the `_fsbpyr` contract test holds them equal around piece boundaries. Plain SHA-256 stays only where an outside value is compared (downloads) and for identity hashes (runtime and generation ids). The calling thread spins on non-blocking joins (`pthread_tryjoin_np`) for up to 2 ms before it sleeps in a join: on a hybrid CPU a thread woken by the last helper may land on an efficiency core and stay there while every core is busy; the bound keeps a stopped helper from making it burn a core. `_fsbpyr.file_digest(fd, size)` hashes a duplicate of the descriptor and only a regular file of the expected size; its `fcntl` binds to `fcntl64@GLIBC_2.28`, so pure-Python apps now report glibc ≥ 2.28 (the D25 baseline) where they reported 2.25 (until 0.8.0, which took that call out of `module.c`: `Python.h`'s `_FILE_OFFSET_BITS 64` had mapped it to `fcntl64`; the launcher's other `fcntl` calls bind `fcntl@GLIBC_2.2.5`, so since then pure-Python apps report glibc ≥ 2.25 again, the launcher's `getentropy`; checked 2026-10-18) | Cache check against a size-only build, median of 61 runs (Core Ultra 9 285, P-cores / all 24 cores): numpy (38 MB of natives) +5 / +4–5 ms, onnx (69 MB) +8–9 / +14–18 ms (`docs/performance.md`); one thread (as 0.6.0): +18 / +18 and +26–30 / +25–26 ms. The routine alone hashes onnx's 69 MB in 5.1 ms; the rest is a fresh process mapping each library twice. With joins instead of spinning, numpy took +38 ms on all cores (its OpenBLAS starts one spinning thread per core right after the check) | Hashing different files in parallel (the largest file still sets the time: numpy 10 → 6 ms); joining the helper threads (above) |
| D33 | **`--best-compression`: native files as xz -6 with the x86 filter** (owner decisions 2026-10-08/09; format v5; zstd 9 stays the default; not with `--fast-compression`, and a compression flag on the command line replaces the other one from the project). A library of up to 4 MiB is one xz stream (codec 3); a larger one is a blob of pieces (codec 4) naming consecutive xz blobs of 4 MiB of content each, decoded in parallel — the reverse of D30 blocks. The build compresses all pieces on min(8, cores) threads, largest first. The launcher decodes xz with the statically linked liblzma (single-call `lzma_stream_buffer_decode`, exactly one stream of the recorded size, 64 MiB decoder memory, no check of its own: the integrity digest covers the file) in `_fsbpyr.read` without the GIL; the pieces of one library on `fsb_parallel`, the routine that also hashes the integrity digest. The runtime extracts files one by one, as with zstd, so memory stays at one library. `build_info["native_compression"]` records it | aiagent 149.7 → 140.0 MB (−6.5%), build 18.5 → 21.1 s; first run (empty cache) against zstd: numpy +71 ms, onnx (numpy included) +76 ms, cryptography +25 ms, aiagent `version` +45 ms; later runs unchanged. One stream per library instead: 139.0 MB but first runs +130 / +313 / +66 / +49 ms and builds of 24.4 s (`docs/performance.md`) | One stream per library, with libraries decoded on Python threads (memory up to workers + 2 libraries; the largest library still on one thread); native multi-block .xz (Python cannot write it; liblzma's multithreaded decoder brings its own thread pool, without the spin-wait fix); zstd (no parallel decoding of one frame in libzstd); xz -9 (37.8 against 38.0 MB of aiagent natives, slower) |
| D34 | **torch is a built-in fs-package; cached files are written without fsync and checked in batches** (owner decisions 2026-10-09). torch 2.14 runs `bin/torch_shm_manager` and loads `lib/libtorch_global_deps.so` by paths next to its `__file__` as it is imported; it found them because `str(distribution("torch").locate_file("torch"))` made the resources tree extract the whole package, a second copy of its libraries included. As a built-in fs-package its tree is extracted once into the native cache, next to its libraries. Every process still hashes every cached file once before it first uses it (D31), now up to 256 mapped files per parallel pass (`_fsbpyr.tree_digests` on `fsb_tree_digests`, the leaves of all files in one `fsb_parallel`); an fsync per file bought nothing, since a file a crash left incomplete fails its check and is restored | A CPU-only torch app (torch 2.14.1, 12,151 files, cache on XFS): first run 42.5 s → 1.34 s, cache 1.2 GB → 709 MB, later runs 895 → 620 ms (venv about 465-500 ms; the checks took about 120 ms one file at a time, about 60 ms in batches). Without the fsync alone: 2.3 s | `locate_file` returning a directory's path inside the executable (torch then fails: its `torch_shm_manager` is not on disk); checking only code each run and data when written (owner: every file, in parallel) |
| D35 | **The cache sync runs in the launcher** (owner decision 2026-10-09): `_fsbpyr.sync(root, chain, files, checked, resources)` (`cache.c`) checks every record first (an extractable kind, a path the payload reader would accept, a blob and a digest), then, without the GIL, opens and creates the directories below the root one component at a time (never through a symlink, ours, forced to 0700, only those along the current path kept open), checks each cached file (opened `O_NOFOLLOW\|O_NONBLOCK`, then a regular file of ours of the payload's size by `fstat`; up to 64 KiB read into a 16 MiB buffer, larger ones mapped; up to 1024 files per parallel pass), and restores a missing or different file (decoded, its digest checked, written under a temporary name, `fchmod`, renamed into place). Changed files are reported on stderr once it returns, in file order. `_fsbpyr.files_below` gives an fs-package's files as the one run of the sorted table they form. Python keeps where the cache is, the record of what this process checked (a bytearray per cache, one byte per file) and the messages. A sync decodes with a zstd context and a 4-block cache of its own (`fsb_decoder`; NULL names the process's, used with the GIL held), so nothing is shared and nothing locked: a lock held by a sync while another thread forks would stay held in the child (security review; a first lock-based version hung 39 of 40 forked children in a C test). A sync with nothing to check touches nothing; a restored file is always readable by its owner; running out of descriptors fails the sync instead of counting as a changed file, and a mapping that finds no address space left (`ulimit -v`) has the pass's files checked and unmapped first, then is tried again (follow-up review: 6 libraries of 64 MiB under a limit leaving 160 MiB failed before); a SIGINT is handled once the sync returns. `module.c` refuses to build for a free-threaded CPython (`read` shares the process's decoder under the GIL); an e2e test forks 40 times while a thread syncs, and a lock put back around decoding hangs the first child. The tests' stand-in mirrors `cache.c` in Python and the contract compares both: damaged, missing, replaced and unreadable files, symlinked, foreign and shared directories, corrupt records, descriptor limits, more files than a pass holds, syncs on threads while others read. `_fsbpyr.tree_digests` is gone (the cache check was its only user); the contract checks the launcher's digests at the leaf boundaries through `sync` instead. Blobs over 1 GiB are refused, as `_fsbpyr.read` refuses them (`FSB_MAX_MEMORY_BLOB`; a build never bundles one; cap removed in 8b, 0.11.0, D38) | torch app (12,151 cached files), later runs: torch's import 615 → 538 ms (venv 470), the whole run 1363 → 1279 ms (venv 1208); first runs 1967 → 1908 ms. The check of the 12,151 files: 80 ms with every file mapped (256 per pass), 53.5 ms as released (the 12 native libraries, 476 MB: 16 ms; the 12,139 other files, 235 MB: 65 → 38 ms). Standalone on those 12,139 files: all mapped 58 ms (55 ms with 1024 per pass); read up to 64 KiB 34 ms (1024 per pass, no `fstatat`; 36 ms with 256, 38 ms with an `fstatat` before each open); read up to 1 MiB 46 ms; one helper thread per 4 MiB of data instead of per 4 leaves, all mapped: 70 ms (`docs/performance.md`) | The walk in Python with one C call per file (the per-file Python calls were the cost); module code (`_code`) in C: of its 85 ms under cProfile for torch's 904 modules, 73 ms are `marshal.loads` and `_fsbpyr.read`, which are C already |
| D36 | **Checks read files, never map them; restored files are written in pieces of 1 MiB** (owner decision 2026-10-09; 0.9.1). The executable's check and the cache check hash a file through its descriptor: each of up to 8 threads reads its 1 MiB leaves through a 64 KiB buffer of its own (`fsb_parallel` passes the worker's number), so a file's size never counts in the process's resident memory, as it did while the file was mapped; a leaf that cannot be read whole gets a digest that cannot match, so a file that shrinks while it is hashed fails its check instead of ending the process with SIGBUS. The cache check holds at most 64 files over 64 KiB open per pass; out of descriptors or memory while it holds some (opening a file or its directories), the pass is checked first and the step tried once more (this replaces D35's address-space retry: nothing is mapped; the security review found the directories missing at first). A read error in the executable's own check says it cannot read the file, not that it was modified; a cached file that cannot be read is restored like a changed one. A restored file is written in pieces of at most 1 MiB: written whole, torch's 438 MB libtorch_cpu.so stayed in XFS's page cache as 2 MiB folios, which the dynamic loader maps whole | torch app (zstd 9, later runs, 21 runs of each interleaved; Core Ultra 9 285, Linux 6.12, cache on XFS): peak RSS 577 → 349 MB (venv 314); by phase 226 → 14 MB once the executable is checked, 576 → 241 MB during the import (the cache check), 450 → 348 MB in the training loop, where libtorch_cpu.so holds 60 MB instead of 156 MB, as in the venv; whole run 1067 → 1071 ms, import 467 → 473 ms. The executable's check: the no-op app's 27.6 MB 1.82 → 1.93 ms (201 runs), the torch app's 233 MB 14.8 → 13.9 ms. Whole 1 MiB leaves read into a buffer per thread cost the no-op start more and left glibc 8 MB more heap (code review): 64 KiB pieces instead. The library written in 4 MiB pieces stayed at 156 MB; in 1 MiB, 256 KiB or 64 KiB pieces it took 60 MB. The executable's own pages, 42 MB against `python3.14`'s 11 MB, remain (`docs/performance.md`) | Capping the bytes mapped per pass (the peak stays at the largest file, 438 MB here); dropping a written file from the page cache (`posix_fadvise` leaves dirty pages, and the next run would read it from disk) |
| D37 | **No more millisecond tuning; comparisons with other tools come last** (owner decision 2026-10-09). Phase 7 ends with what it has: the compression work (D29, D30, D33), the checks and the cache in the launcher (D31, D32, D35, D36), and `docs/performance.md` as the report (every release's measurements, how they were taken, where to look when a start gets slower). Regressions are caught by exact counts, not timings: the e2e tests read `FSBPYR_TRACE` (modules loaded per distribution, files extracted) and the contract the cache sync's outcomes; the one timing test stays the startup budget (1.2× bare CPython, the integrity check apart). Dropped: the benchmark harness, the 10% timing gate, the §8 speed targets, grouped frames for tiny modules, and an opt-in fast exit (it saves the libraries' teardown, about 215 ms for torch, but leaves unflushed files, killed threads and unrun finalizers behind). Comparisons with other tools (PyInstaller, a Rust and a Go CLI) move to Phase 10: there is no point in comparing unfinished work | The results are solid; a few milliseconds here or there are not worth the work, and a timing gate mostly catches noise | The §8 harness, targets and CI gate as planned |
| D38 | **Phase 8 scope** (owner decisions 2026-10-09). (1) No size limits of ours: files and executables of 1 TB and more build and run if the file system and OS allow them; the only structural limit is the format's 32-bit indices (about 4.29 billion files, modules or blobs: the OS fails first). (2) fsbpyr's own work never needs more than 1 GB of resident memory, whatever the input size, when building and when running: data is streamed in bounded pieces; what an app asks for itself (`read()`, `read_bytes()`) is the app's. (3) Cache GC: at most once a day, or when the cache's file system has less than 10% free (then at most once an hour); generations and resource trees unused for 7 days are removed (under disk pressure every one not in use); never what a running process uses (locks); never anything we did not create (a marker names our cache roots; `FSBPYR_CACHE_DIR` may be a user's own directory); explicit runs: `fsbpyr cache gc` (the tool's cache and every app cache it finds) and `FSBPYR_GC=1 app` (that app's cache, then exit). (4) Every item gets explicit tests in the normal test run; principle: normal cases fast, edge and extreme cases correct, never a crash | User does not want to deal with stale cache objects; a removed object is recreated when needed; 1 TB inputs must work | `rm -rf` of the whole cache; a GC run on every start; 64-bit indices (format v6) |
| D39 | **Up to 8 cores in every phase** (owner decision 2026-10-10: "all phases must use up to 8 cores"): building, running and checking use min(8, usable cores) wherever the work divides. (1) Format v6: codec 5, zstd in pieces: a blob of more than 8 MiB is stored as zstd frames of 8 MiB of content each (the last fewer), each with its content size and checksum, compressed on up to 8 threads and decoded on up to 8 threads (the launcher's extraction and streams, a window of 8 pieces at a time; the Python reader); codec 4 stays xz in pieces of 4 MiB. (2) A build compiles and canonicalizes modules in a pool of up to 8 processes (the GIL keeps threads from it) and registers the results in the original order, so the output is the same bytes whatever the number of cores; files, natives and blocks are compressed on up to 8 threads (zstd and hashlib release the GIL). The launcher writes the files a cache sync extracts on up to 8 threads: small ones in chunks of neighbours, large ones one at a time, their pieces decoded in parallel; work nested in that work runs on its own thread, so never more than 8. (3) What cannot divide stays one thread: a file under 8 MiB is one frame, decoded by one thread | Before (0.14.0, Core Ultra 9 285): the torch app's build used one core on average (22.2 s of CPU in 23.0 s); under cProfile 70% was compiling 4,959 modules (bytecode canonicalization 24 s, `compile()` 6 s of 44 s) and 9 s zstd; libtorch_cpu.so (418 MiB) compressed in 3.15 s and decoded in 566 ms on one thread; in 8 MiB pieces on 8 threads 0.60 s and 100 ms, 1.4% larger (4 MiB: +2.8%, 127 ms; 16 MiB: +0.6%, 219 ms, 26 pieces on 8 threads) | zstd's own multithreading (one frame: no parallel decoding); threads for compiling (the GIL); pieces of 4 MiB as for xz (twice the loss) |
| D40 | **The CLI runs on click; its commands are the one source of the help text, the shell completions and the man page** (owner decision 2026-10-13). `fsbpyr completion bash\|zsh\|fish` prints a script that asks fsbpyr for the words as the user types (click's `_FSBPYR_COMPLETE` protocol), so `--entry` and `--extra` offer the console scripts and extras of the project being built; `fsbpyr man` prints the roff page, rendered from the commands through click's public API (`manpage.py`); help texts set code in backticks, which the page sets in bold. Every on/off option of `build` has a `--no-` form, so the command line also switches off what `[tool.fsbpyr]` switches on. The README's option table is the `build` command's help records, checked by a test. Usage errors still exit 2, `main(argv)` still returns the exit code | The binary carries no install step, so it carries its completions and its man page; click's completion is dynamic, which is what lets it read the project. click costs the executable 4–5 ms (median of 31 starts: `--version` 66.7 → 70.6 ms, `build -h` 60.5 → 65.5 ms); a completion that reads the project takes about 85 ms | argparse plus an own declarative spec rendering all three (no dependency, more code of our own); argparse plus shtab and argparse-manpage (both walk argparse's private attributes; the page could only repeat `--help`); static completion scripts (could not offer the project's scripts or extras) |

---

## 3. Architecture

### 3.1 Components

Status (v0.19.0): the C module is `launcher/module.c` (lookups named `find_module`,
`find_file`, ...; `sync` and `files_below` since D35), next to `payload.c`, `integrity.c` (D31),
`parallel.c`, `cache.c` (D35), `due.c` (8d) and `way.c` (8e); the pure-Python stand-in is
`tests/support/standin.py` (Phase 3), not `boot/_fsbpyr_py.py`.

| Component | Language | Runs where | Responsibility |
|-----------|----------|------------|----------------|
| `launcher/main.c` | C | built binaries | Maps the payload, builds the frozen table, configures CPython, calls `Py_RunMain` |
| `launcher/fsbpyr_module.c` → `_fsbpyr` | C | built binaries | `lookup_module`, `lookup_file`, `list_dir`, `read(blob)`, `stream(blob)`, `info()`. Every offset is bounds-checked |
| `fsbpyr/boot/` | Python (stdlib only) | built binaries | Path hook, loaders, resources, `importlib.metadata`, native cache, entry-point dispatch |
| `fsbpyr/boot/_fsbpyr_py.py` | Python | tests only | Pure-Python implementation of the `_fsbpyr` API (mmap + struct + `compression.zstd`), so boot code can be unit-tested in a normal venv |
| `fsbpyr/` (build side) | Python | venv or fsbpyr binary | CLI, project discovery, staging install, dependency closure, compile, ELF analysis, payload writer, runtime management |

The boot code is part of fsbpyr's own source tree. When fsbpyr builds an app, it reads these
sources from its own payload (through `importlib.resources`), compiles them for the target
runtime, and embeds them as STARTUP entries. Self-hosting therefore exercises the resources path
too.

### 3.2 Executable layout (format v1)

Status (v0.19.0): format v6 since 0.15.0 (v2 blocks, 0.5.0; v3 whole-file digest, 0.6.0; v4
cache digests, 0.7.0; v5 xz, 0.8.0; v6 zstd pieces, 0.15.0); `docs/format.md` is authoritative.
Changed below: the trailer's last 32 bytes are the integrity digest of everything before them
(D31), not `payload_sha256`; blob codecs are 0–5 (none, zstd, block, xz, xz pieces, zstd
pieces); a STARTUP section lists the startup modules (Phase 2).

```
0                                     payload_offset (4 KiB aligned)                          EOF
| stub ELF (launcher + static CPython) | pad | HEADER | TABLES | STARTUP blobs | zstd frames… | TRAILER (64 B) |
```

- The stub is an unmodified, valid ELF and the payload is an appended overlay. The loader maps
  only `PT_LOAD` segments. Running `strip` on the output destroys the payload; this is
  documented as unsupported.
- **TRAILER** (64 B, little-endian): `magic "FSBPYR\0T"` (8) | `format_version u32` |
  `flags u32` | `payload_offset u64` | `payload_size u64` | `payload_sha256[32]`.
- **HEADER**: magic, version, `PY_VERSION_HEX`, bytecode magic, `generation_id[32]`, app name,
  entry point, table offsets and counts, and a reference to build-info JSON (parsed only by
  `inspect`).
- **TABLES** use fixed-size records sorted by key bytes. All keys live in one shared string
  table.
  - `modules`: dotted name → kind (`MODULE | PACKAGE | NAMESPACE | EXTENSION`), code blob,
    source blob, file id, flags (`STARTUP`, `FS_REQUIRED_ROOT`), dist id.
  - `files`: site-relative POSIX path → blob, kind (`RESOURCE | NATIVE_EXT | NATIVE_LIB |
    METADATA`), mode bits, dist id, closure reference. Because paths are sorted, listing a
    directory is a prefix range scan.
  - `dists`: normalized name → dist-info prefix, version.
  - `blobs`: payload-relative offset, compressed size, uncompressed size, codec
    (`NONE | ZSTD`), digest index.
  - `closures`: arrays of u32 file ids (native dependency closures).
  - `digests`: SHA-256 entries.
- **Blob order on disk:** STARTUP, then code grouped by package, then resources, then sources,
  then natives. Locality keeps page-ins low.
- The exact byte layout is frozen in `docs/format.md` (Phase 1). `launcher/format.h` mirrors
  `fsbpyr/payload/format.py`, and a test compiles a probe program that prints `sizeof` /
  `offsetof` and compares them with the Python `struct` definitions.

### 3.3 Startup sequence (built app)

Status (v0.19.0; `launcher/main.c` and `_fsbpyr_boot.run`): step 1 checks the whole file
against the trailer's digest before believing any byte (D31, `integrity.c`); between steps 2
and 3 the launcher starts a background cache collection when one is due (`due.c`, 8d); step 5
also installs `python`'s signal handlers (0.11.0); step 7 also runs `-m`/`-c` children under
D23 (0.16.0) and the collection for `FSBPYR_GC` (8d) instead of the app.

1. `open("/proc/self/exe")` and `pread` the trailer. Validate magic, version and bounds.
   `mmap` the payload with `PROT_READ, MAP_PRIVATE`.
2. Validate every header and table offset/count against the mapping size. Compare the bytecode
   magic in the header with the value the stub was compiled with (passed as `-D` at stub build
   time). A mismatch is a hard error with a clear message.
3. Build a `struct _frozen[]` from the STARTUP modules, pointing into the mmap (decompressed and
   checksum-verified instead, D8). Set
   `PyImport_FrozenModules`.
4. `PyImport_AppendInittab("_fsbpyr", PyInit__fsbpyr)`.
5. Configure the interpreter:
   - Isolated `PyPreConfig` with `utf8_mode=1`.
   - Isolated `PyConfig`: `executable`; `argv` with `parse_argv=0`; `site_import=0`;
     `safe_path=1`; `module_search_paths_set=1` with an empty list; `home`/`prefix` set so
     getpath does not probe the filesystem (S2: zero stdlib probes; two `pyvenv.cfg` ENOENT
     lookups remain, which affect only `sys.prefix`). `stdlib_dir` is the executable itself, so
     CPython's frozen modules get `__file__`/`__path__` inside the payload tree
     (`encodings.__path__ == [<exe>/encodings]`). `import_time` comes from `FSBPYR_IMPORTTIME`.
   - `run_command = "import _fsbpyr_boot; _fsbpyr_boot.run()"`.
6. `Py_InitializeFromConfig`. CPython imports `encodings` and `linecache` from our frozen table.
7. `Py_RunMain` → `_fsbpyr_boot.run()`:
   - re-points `__path__` of frozen packages (`encodings`) at the virtual path
   - installs the path hook and sets `sys.path = ["<exe>"]`
   - appends the DistributionFinder
   - runs multiprocessing children instead of the app when argv is one of the command lines
     CPython re-executes `sys.executable` with (D22, Phase 6)
   - runs the entry point: `module:attr` → `sys.exit(attr())`; `module` →
     `runpy._run_module_as_main`
8. CPython's own `Py_RunMain` handles exit codes, `SystemExit`, the SIGINT re-raise, stdio
   flushing and atexit.

**Startup budget (dev machine):** steps 1–5 take ≤ 0.2 ms. `hello --version` is ≤ 5.6 ms p50,
which is ≤ 1.15× bare `python3.14 -I -S -c pass`. Since D31 the whole-file integrity check
comes first and is budgeted apart: it costs what hashing the file costs (`integrity_ns` in
`FSBPYR_TRACE`, about 2 ms for a 28 MB app); everything else must stay within 1.2× (1.15×
until 0.7.0: the launcher sat at the edge, 1.11–1.18×; owner decision 2026-10-09). Measured
timings, release by release: `docs/performance.md`.

### 3.4 Pure-Python import path

- `sys.path = ["<exe>"]`. `sys.path_hooks[0]` accepts `<exe>` and `<exe>/<pkg path>`, plus
  cache-dir aliases for FS_REQUIRED packages, and returns `FsbpyrPathFinder(prefix)`.
- `find_spec(fullname)` does one C lookup in `modules`:
  - It returns a `ModuleSpec` with `FsbpyrLoader` and `origin="<exe>/pkg/mod.py"`. Packages
    also get `submodule_search_locations=["<exe>/pkg"]`.
  - For NAMESPACE it returns a spec with `loader=None` plus the portion path, and the stock
    PathFinder assembles the namespace (since Phase 3 a payload meta finder ahead of PathFinder
    serves them with a `NamespaceLoader` subclass, so `importlib.resources` works on them).
- `FsbpyrLoader` implements `InspectLoader` and `ResourceLoader`:
  - `get_code` → `marshal.loads(_fsbpyr.read(code_blob))`
  - `get_source` reads the lazy source frame
  - `get_data(path)` maps virtual paths back to `files`
  - `is_package`, `get_filename`
  - `get_resource_reader` → a `Traversable` over `files` (`iterdir` is a prefix scan)
- The path finder implements `iter_modules(prefix)`, so `pkgutil.iter_modules` and
  `walk_packages` work (plugin discovery).
- `co_filename` is the site-relative path (`pkg/mod.py`; made the absolute virtual path at load by
  `_imp._fix_co_filename`, Phase 3). Tracebacks get source through
  `__loader__.get_source` (linecache lazycache).
- `importlib.metadata`: `FsbpyrDistributionFinder.find_distributions(context)` binary-searches
  `dists` for name lookups. It enumerates every dist only when the caller asks for all of them
  (for example `entry_points()`).
- Startup-path boot code imports only built-in and frozen modules (`sys`, `_imp`, `marshal`,
  `_fsbpyr`, `_frozen_importlib_external`). Support for resources and metadata is imported
  lazily on first use.

### 3.5 Native extensions and the cache

Status (v0.19.0): the materialization below runs in the launcher (`_fsbpyr.sync`, `cache.c`,
D35); every process hashes each cached file once with the tree digest (D31, D32) instead of
step 2's size check; no fsync (D34); no self-heal retry after a failed `dlopen` (gone in 0.6.0);
new files are written unnamed and linked into place (`O_TMPFILE`, 0.15.0); the root's way from
`/` is checked (8e, 0.14.0); GC as built in 8d. `docs/format.md` (Integrity) describes the cache
check.

```
import native_mod → modules lookup (EXTENSION) → closure (bundled libs only) → cache check
   ├─ hit  ─────────────────────────────────────────────────────────────┐
   └─ miss → zstd stream → temp file → size+sha256 → fsync → rename ────┴→ ExtensionFileLoader (dlopen)
```

- **Build time.** `fsbpyr/elf.py` is a pure-Python ELF64 parser (`PT_DYNAMIC` → `DT_NEEDED`,
  `DT_RPATH`, `DT_RUNPATH`, `DT_SONAME`). Each NEEDED entry resolves in one of three ways:
  - (a) Inside the payload through RPATH/RUNPATH with `$ORIGIN`: **bundled**. An object
    without RUNPATH also searches the RPATHs of the objects that loaded it, as ld.so does
    (auditwheel leaves bundled libraries such as gnureadline's libncurses without one).
  - (b) In the system allowlist (auditwheel's manylinux policy for the target glibc): **system**.
  - (c) Otherwise the build fails and names the library. `--allow-system-lib NAME` overrides
    this.

  Extensions that `NEEDED libpython3.X.so` are rejected with a hint. The closure is stored per
  NATIVE_EXT. Libraries opened by name at run time (`dlopen`) have no NEEDED entry: a bundled
  library (not an extension, which has a `PyInit_<name>` symbol) in the object's own directory
  or on its `$ORIGIN` search path joins the closure when the object contains its file name as a
  C string. onnxruntime's provider bridge loads `libonnxruntime_providers_shared.so` this way
  (v0.1.1; before, every import printed "Init provider bridge failed"). Such a library whose own
  needs cannot be met (onnxruntime-gpu's CUDA provider without a bundled CUDA runtime) is left
  out with a build warning instead of failing the build. A library that nothing links to or
  names is reported only if the package's Python code names it (a likely `ctypes` load).
  Known onnxruntime quirk, left alone (owner decision 2026-10-08): its telemetry runs
  ``sh -c "echo `blkid; hostname`"`` at import, which prints "command not found" on minimal
  systems without those commands, exactly as a normal install does.
- **Runtime.** The extension's `find_spec` uses `PayloadExtensionLoader`. It has the launcher
  sync the closure (`_fsbpyr.sync`, D35), then delegates to
  `importlib.machinery.ExtensionFileLoader(name, <gen>/site/<path>)`, which uses the normal
  `dlopen`.
- **Cache root:** `$FSBPYR_CACHE_DIR`, or `${XDG_CACHE_HOME:-$HOME/.cache}/<app>`; the generation
  directory is `<root>/runtime/<first 32 hex digits of generation_id>/`.
- **Materialization steps:**
  1. Open the generation directory with `O_DIRECTORY|O_NOFOLLOW` and create subdirectories
     through `dir_fd` operations (mode 0700). Refuse symlinks. Every directory must be owned by
     the effective uid; those below the root are forced to 0700 (the root's mode is left
     alone, since `FSBPYR_CACHE_DIR` may name a directory of the user's).
  2. If the target exists as a regular file owned by the effective uid and its size matches,
     it is a cache hit.
  3. Otherwise create a temp file `.fsbpyr-<pid>-<rand>` with `O_CREAT|O_EXCL|O_NOFOLLOW`,
     mode 0600. Stream-decompress into it (`_fsbpyr.stream(blob)`) while hashing. Check size
     and SHA-256, `fchmod 0500`, `fsync`, then `rename` into place. Racing writers produce
     identical content, so the last rename wins harmlessly and no lock is needed.
  4. Self-heal: if `dlopen` of the extension fails (not an `ImportError` raised by its own
     initialization), re-hash the cached closure. Replace mismatches and retry once.
- **Garbage collection (Phase 8):** `.last-used` is touched at most once a day. Generations
  unused for more than 30 days are removed opportunistically under a non-blocking `flock`.
  (Built in 8d, 0.13.0, as D38 says instead: each tree's `.lock`, held shared by its users,
  dates its last use; 7 days, not 30.)
- **FS_REQUIRED packages** (`fs-packages` / `--fs-package`, plus the built-in rule for
  `certifi`; now also babel, botocore, litellm, matplotlib and torch:
  `classify.BUILTIN_FS_PACKAGES`): when the package is first imported, every file below its
  directory (data files
  and, unless `--no-source`, its `.py` sources) is materialized into `<gen>/site/<pkg>/` with
  the steps above, owner-readable and executable only if the wheel's file was. The package's
  modules get `__file__`/`__path__` there, the path hook also claims that directory, and
  `importlib.resources` returns real `pathlib.Path` objects; code is still loaded from memory.

### 3.6 Build pipeline (`fsbpyr build`)

1. **Config.** CLI flags override `[tool.fsbpyr]` in `pyproject.toml`, which overrides
   defaults.
2. **Runtime.** The target is Python 3.14 (MVP). Check that `requires-python` admits it, and warn
   if `.python-version` disagrees. Select the runtime: self if the minor version matches,
   otherwise the cache, otherwise fail with an error that suggests
   `fsbpyr runtime build --python X.Y`.
3. **Staging install.** Install into `build/fsbpyr/staging/<key>/`. `<key>` hashes the lock
   file and the extras, so an unchanged lock reuses the staging directory (since Phase 4 a fresh
   temporary directory per build instead, `build.py`; the uv cache keeps it fast). The
   lock source is chosen in this order:

   | Lock source | Command |
   |-------------|---------|
   | `uv.lock` | `UV_PROJECT_ENVIRONMENT=<staging> uv sync --frozen` (`--locked` since Phase 4) `--no-dev --no-editable --python 3.14 [--extra X…]`. Honours `[tool.uv.sources]`, indexes and workspaces. The project is installed non-editable |
   | `pylock.toml` (PEP 751) | `uv venv <staging> --python 3.14`, `uv pip install --python <staging>/bin/python -r pylock.toml`, then the project with `--no-deps` |
   | pinned requirements (`--requirements FILE`, default `requirements.txt`) | the same venv, `uv pip install ... -r FILE` (`--require-hashes` when hashes are present), then the project with `--no-deps` |
   | none | install the project with dependencies resolved fresh from `[project.dependencies]`. Prints the warning **"unlocked build — not reproducible"** |

   The installer is always uv (D26): without uv on PATH the build stops with installation
   instructions. Every install passes `--python-platform` for the runtime's glibc baseline
   (`x86_64-manylinux_2_28`, Phase 6c). `--offline` is passed through. Every source installs
   into a venv, never `--target`: the console scripts an installer writes carry a `#!` naming
   whichever Python uv picked (an active venv, the project's `.venv`), and in a venv they stay
   outside site-packages, so they never reach the executable; `UV_VENV_SEED=0` keeps a user
   setting from seeding pip into it. Files a wheel installs outside site-packages (`.data/data`,
   `.data/headers`) are skipped, as for uv.lock builds. The same fsbpyr binary builds
   byte-identical executables whichever Python uv picks (an active venv, the project's `.venv`;
   verified with aiagent, 2026-10-07).
4. **Entry point.** Read `[console_scripts]` from the project's installed `entry_points.txt`.
   If there is one, use it. If there are several, `--entry` or config is required. `module`
   and `module:func` are also accepted.
5. **Collect and validate.** Index every `*.dist-info` in the staging site directory. Walk the
   project's Requires-Dist (plus extras) with the runtime's marker environment as a consistency
   check. A missing dist is an error. Installed dists that are not reachable produce a warning
   and are still packaged, because the lock decided they belong. `.pth` files produce warnings
   (since Phase 6b their path lines are honoured; code lines warn).
6. **Classify** each dist's RECORD into files. Skip paths outside site-packages,
   `__pycache__/` and `*.pyc`. Files not listed in any RECORD (venv scaffolding such as
   `_virtualenv.py`) are not packaged. Every directory with an identifier name and no
   `__init__.py` gets a NAMESPACE record, matching CPython's implicit namespace packages, so
   that `importlib.resources.files("pkg.data")` works for data directories. A module file's
   stem may contain hyphens (`rich/_unicode_data/unicode17-0-0.py`) or start with a digit
   (mypyc's `81d243bd2c585b0f4821__mypyc`), since `importlib.import_module` reaches it; an
   extension's never contains a hyphen (CPython calls `PyInit_<stem>`), and package
   directories must be identifiers.
7. **Compile** in-process (on up to 8 worker processes since 0.15.0, `compiler.py`, D39) when the
   runtime is self; otherwise use the runtime's compile helper
   (Phase 9; since 2026-10-18 none: each Python version gets its own fsbpyr, which always
   compiles in-process). Every result is canonicalized (D16). A `SyntaxError` keeps the file as a resource
   and prints a warning.
8. **Natives.** Run the ELF analysis, compute closures, check the allowlist and compute the
   generation id.
9. **Compress** at the build's zstd level (D29). Stdlib frames from the runtime are reused verbatim.
10. **Assemble** stub ‖ pad ‖ payload ‖ trailer into a temp file, `chmod 0755`, and atomically
    rename it to `dist/<name>`.
11. **Smoke test.** Run `<out>` with `FSBPYR_SELFTEST=1`. Boot checks the tables and imports
    the entry module without calling it. `--no-smoke` skips this.

### 3.7 Runtime build (`fsbpyr runtime build --python 3.14`)

Status (v0.19.0): the stub links against the pinned glibc 2.28 sysroot (6c, `runtime/sysroot.py`)
as PIE (D17), with `-Wvla`, `-Wframe-larger-than=8192` and an ASan/UBSan variant (8a); the link
is reproducible (`-ffile-prefix-map`, 0.18.2, `LAYOUT_VERSION` 7); downloads retry network
failures (0.18.1); the stdlib compiles on up to 8 workers (0.15.0).

1. Download the pinned PBS full archive (URL and SHA-256 pinned in `fsbpyr/runtime/pins.py`),
   verify it, and extract it into `~/.cache/fsbpyr/downloads/`.
2. Parse `PYTHON.json` and require a clang/lld whose major version is at least the bitcode
   version. Search `FSBPYR_CLANG`, then `clang-NN`, then `clang`.
3. Compile `launcher/*.c` with `-flto -O2`. Link:
   - libpython
   - selected built-in extension objects and a generated inittab
   - the static libraries listed in `PYTHON.json`, excluding `_tkinter`, tcl/tk and X11
   - `-Wl,--export-dynamic-symbol=Py*` and `=_Py*` (Phase 5; was `--export-dynamic`)
   - `-lpthread -ldl -lutil -lm -lrt`

   Spike S1 measures PIE vs non-PIE startup (done: PIE, D17).
4. **Stdlib.** Take `lib/python3.14/` minus `test/`, `idlelib/`, `tkinter/`, `turtledemo/`,
   `ensurepip/`, `__pycache__/`, `site-packages/` and `config-*/`. Compile it with the PBS
   interpreter from the archive. Determine the STARTUP set by running that interpreter with
   `-X importtime -I -S -X utf8 -c pass`, plus the boot module.
5. **Output** goes to `~/.cache/fsbpyr/runtimes/cp314-x86_64-linux-gnu-<launcher_sha8>/` (now
   `cp314-x86_64-unknown-linux-gnu-<12 hex>`, hashing the launcher, the PBS and sysroot pins
   and `LAYOUT_VERSION`: `registry.runtime_id`) as
   `{stub, stdlib.fsbp, runtime.json}`. `runtime.json` records:
   - Python version and bytecode magic
   - ABI tag and extension suffixes
   - the marker environment
   - compatible wheel platform tags
   - the glibc baseline

---

## 4. Repository layout

Status (v0.19.0): also in the tree: `build.py` (§3.6), `scan.py` (6b hints), `vcs.py` (D28),
`payload/spec.py`, `payload/spill.py` (8c), `runtime/{sysroot,toolchain,ar,hold,collect}.py`,
`launcher/{integrity,parallel,cache,due,way}.c`, `boot/_fsbpyr_rt/gc.py` (8d), `tests/fuzz/`,
`tests/probes/` (8a), and at the top `Makefile`, `tools/bootstrap.sh`, `Containerfile`,
`tools/release/`, `.github/workflows/release.yml` (0.18.1), `CHANGELOG.md`, `.python-version`.

```
pyproject.toml              name=fsbpyr, requires-python>=3.14, deps=[click, packaging],
                            scripts: fsbpyr = "fsbpyr.cli:main", build backend: uv_build
stubs/_fsbpyr.pyi          type stub of the C module (for mypy)
src/fsbpyr/
  launcher/                 C sources, package data (D18)
    main.c                  startup sequence (§3.3)
    module.c                _fsbpyr built-in module (§3.1)
    payload.c, payload.h    O(1) validation, bounds-checked records, binary search
    format.h, launcher.h    C mirror of payload/format.py; shared declarations
    vendor/zstd/            zstd 1.5.7 public headers (BSD); libzstd.a comes from PBS
  assemble.py               runtime + boot + app content -> executable
  cli.py                    click: build | inspect | runtime | cache | completion | man (D40)
  manpage.py                roff man page from the click commands (D40)
  config.py                 [tool.fsbpyr] + CLI merge (tomllib)
  project.py                project discovery, pyproject/requires-python, lock-source selection
  staging.py                fresh install into staging (uv sync / uv venv + uv pip install)
  closure.py                staging dist index, Requires-Dist consistency walk, markers (packaging)
  classify.py               RECORD → typed entries
  compiler.py               bytecode compile, up to 8 worker processes (D39; planned: compile.py)
  bytecode.py               canonical marshal rewriter (D16), marshal v5 / CPython 3.14
  elf.py                    ELF64 dynamic-section parser
  natives.py                closures, allowlist, generation id
  payload/format.py         struct layouts and constants (single source of truth)
  payload/writer.py         deterministic assembly + zstd
  payload/reader.py         pure-Python reader (inspect, Runtime.from_self, tests)
  payload/convert.py        payload -> spec with stored blobs copied verbatim
  runtime/registry.py       self / cached / build resolution
  runtime/pbs.py            PBS download, verify, PYTHON.json
  runtime/stub.py           clang driver for the launcher
  runtime/stdlib.py         stdlib selection, compile, STARTUP detection
  runtime/pins.py           pinned PBS URLs + SHA-256
  boot/                     code embedded into every built app (stdlib-only)
    _fsbpyr_boot.py         STARTUP: path hook, loader, dist finder, entry dispatch
    _fsbpyr_rt/             lazily loaded: resources, metadata, native, mp, fixups, reexec, gc, trace
  inspection.py             fsbpyr inspect (v0.2.0; not inspect.py, which would shadow the stdlib)
tests/
  unit/ integration/ e2e/
  support/                  pure-Python _fsbpyr stand-in, contract battery, demo app, probe
  fixtures/projects/        hello, clicky, heavy, compat, datafiles, mp
docs/
  python_cli_runtime_manifest.pdf
  IMPLEMENTATION_PLAN.md    (this file)
  format.md                 (Phase 1)
  spikes.md                 (Phase 0 findings)
  performance.md            measurements per release: the Phase 7 report (D37)
```

---

## 5. Bootstrap and self-hosting procedure

Status (v0.19.0): since 0.18.1 `make bootstrap` (`tools/bootstrap.sh`) runs these stages: on
Debian trixie on the host (clang/lld 22 from Debian, uv from astral.sh), elsewhere in a Debian
trixie podman container (`Containerfile`). Stage 0 is `uv run --locked` on the Python of
`.python-version` (3.14.8); the stages land in `dist/stage1..3/`, each must pass
`inspect --verify`, stages 2 and 3 must be identical, and stage 3 becomes `dist/fsbpyr`. CI runs
it on every pull request and tag, and on every push to main after 0.20.0
(`.github/workflows/release.yml`).

```bash
# Stage 0: plain Python in a standard venv (R3)
uv venv --python 3.14 && uv sync          # or: python3.14 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/fsbpyr runtime build --python 3.14   # PBS download + clang>=22 link → runtime cache
.venv/bin/fsbpyr build .                       # → dist/fsbpyr            (stage1)

# Stage 1+: the binary builds itself from its own Python sources (R2)
dist/fsbpyr build . -o build/stage2/fsbpyr     # Runtime.from_self(): no download, no compiler
build/stage2/fsbpyr build . -o build/stage3/fsbpyr
cmp build/stage2/fsbpyr build/stage3/fsbpyr    # fixed point: must be identical
```

- In stage 0, bytecode is compiled by the PBS interpreter of the runtime. From stage 1 on, it is
  compiled in-process by the same CPython version. stage1 == stage2 is a goal; stage2 == stage3
  is a **hard gate**.
- fsbpyr is itself a uv project (`uv.lock`), so every self-build stages through
  `uv sync --frozen --no-dev --no-editable` (`--locked` since Phase 4). From stage 1 on, the
  only external tool is uv,
  which needs network access or a warm uv cache. Clang is needed only to build a new runtime
  (new Python minor or launcher change), and fsbpyr's Python code drives that step.

---

## 6. Phases

Every phase follows TDD: tests first (RED), implementation (GREEN), refactor, review. Exit
criteria are automated tests unless noted. Sizes are S, M or L.

### Phase 0 — Spikes (M) — **DONE 2026-10-06**, see `docs/spikes.md`

- **S1 static link.** Install clang-22/lld-22 from apt.llvm.org. Link a minimal launcher against PBS 3.14.8
  `libpython3.14.a`, then:
  - Find out whether libpython.a already contains the built-in extensions and the inittab, or
    whether `build/Modules/*.o` + `inittab_object` must be linked as well.
  - Load a manylinux extension (orjson) through `--export-dynamic`.
  - Record stub size and PIE vs non-PIE startup.
- **S2 init path.** Build the frozen table from an mmap'd file, use isolated config with empty
  search paths, then:
  - Run `strace -e trace=%file` and confirm there are **no** stdlib path probes during init.
  - Confirm that `Py_RunMain` + `run_command` with `parse_argv=0` keeps `sys.argv` intact and
    gives correct exit codes for `SystemExit`, exceptions and SIGINT.
  - Confirm the precedence of `PyImport_FrozenModules`.
- **S3 marshal determinism.** Compile the stdlib in two processes in different orders and
  compare the `marshal.dumps` bytes. If they differ, plan a deterministic re-serializer
  (about 200 LOC).
- **Exit:** decisions D1–D8 confirmed or revised. Fallback if S1 fails: shared libpython loaded
  through `memfd_create` + `dlopen` (measure its cost).

### Phase 1 — Skeleton and payload format (M) — **DONE 2026-10-06**

- `pyproject.toml`, package skeleton, pytest + coverage config, and `ruff` for formatting and
  linting.
- `payload/format.py`, `writer.py`, `reader.py`, `docs/format.md`.
- `bytecode.py` (D16), ported from `spikes/s3_marshal/canonical.py`. Tests: round-trip and
  idempotence over the full stdlib, plus the determinism matrix (clean, batch and noisy
  process).
- **Exit:**
  - writer→reader round-trip tests pass
  - identical input produces identical bytes
  - malformed and truncated payloads are rejected (property tests)
  - coverage of `payload/` is ≥ 90%

### Phase 2 — Launcher and runtime build (L) — **DONE 2026-10-07**

- `launcher/main.c`, `fsbpyr_module.c`, `format.h`, plus the struct-layout agreement test.
- `runtime/pbs.py`, `stub.py`, `stdlib.py`, `registry.py`, and the `fsbpyr runtime build`
  command.
- A minimal `_fsbpyr_boot` (stdlib only) and a built-in self-test entry point.
- **Exit:**
  - a binary built from the runtime alone runs `import json, argparse, ssl, sqlite3`
  - `strace` shows no stdlib file access
  - `hello` startup p50 is ≤ 1.15× `python3.14 -I -S -c pass`
- **Result (2026-10-07, dev machine):**
  - a no-op app starts in 4.57 ms p50, against 4.83 ms for `python3.14 -I -S -c pass`
    (0.94×, with verified startup modules; 4.49 ms unverified)
  - an app importing argparse takes 10.54 ms, against 10.75 ms for plain CPython
  - a run importing json, argparse, ssl, sqlite3, decimal and hashlib makes 16 file syscalls
    and accesses no stdlib file on disk
  - runtime build: 51 s (full-LTO link plus stdlib compile); app size 27 MB (19.4 MB stub)
  - the format gained a STARTUP section, so the launcher never scans the MODULES table

### Phase 3 — Import system (M) — **DONE 2026-10-07**

- Path hook, loader, namespace packages, `iter_modules`, resources `Traversable`,
  `get_data`, `importlib.metadata` finder, and source-backed tracebacks.
- Contract tests run against both `_fsbpyr_py` (in-process, with coverage) and the C module
  (inside built binaries).
- **Exit:**
  - the contract suite is green on both implementations
  - boot imports nothing outside the built-in and frozen set, asserted through
    `sys.modules` in a self-test
- **Result (2026-10-07):**
  - the C `_fsbpyr` and the pure-Python stand-in (`tests/support/standin.py`) give identical
    results, error types included, for the whole contract battery
  - at entry, `sys.modules` holds only built-in, frozen and STARTUP modules
  - inside a real binary, these work: `importlib.resources` (traversal, `as_file`, the
    functional API, module anchors), `pkgutil.iter_modules`/`walk_packages`/`get_data`,
    namespace packages, `importlib.metadata` (`version`, `metadata`, entry points, `files`,
    name normalization), `inspect.getsource` and source-backed tracebacks
  - code objects get absolute virtual `co_filename`s (`_imp._fix_co_filename`), and STARTUP
    modules use `<frozen NAME>`, so files in the current directory can never shadow payload
    sources in linecache
  - in-process boot coverage is 86–93%; overall coverage is 93%
  - startup is unchanged: 4.65 ms against 4.88 ms for bare CPython
  - namespace packages are served by the payload meta finder with a `NamespaceLoader`
    subclass, so `importlib.resources` works on them; CPython's NamespaceReader only
    understands real directories and zip files
  - review fixes: path-entry normalization, FileFinder parity in `iter_modules`, an O(log n)
    `is_dir`, Traversable equality and `__fspath__`, `Distribution._path`
  - known gaps, deferred to Phase 6:
    - `inspect.getsource` on CPython-frozen stdlib modules (`os`, `codecs`) raises OSError
      (closed in Phase 6b)
    - `__file__` paths do not exist on disk, so `open(dirname(__file__)/x)` fails;
      FS_REQUIRED packages address this (closed in Phase 6)

### Phase 4 — `fsbpyr build` for pure-Python projects, then self-hosting (L) — **DONE 2026-10-07**

- `project.py`, `staging.py`, `closure.py`, `classify.py`, `compile.py`, `config.py`,
  `cli.py`, `Runtime.from_self()`.
- **Exit:**
  - fixtures `hello`, `clicky` (click + rich) and `resources` build and run from each lock
    source: `uv.lock`, `pylock.toml`, pinned `requirements.txt` with hashes, and unlocked
    (the warning is asserted)
  - building from pip works both with and without uv on PATH (superseded by D26: uv is
    required)
  - the developer's `.venv` is untouched (mtime and file tree snapshot are asserted)
  - `dist/fsbpyr` is produced from the stage 0 venv install (stage1)
  - **stage2 == stage3**
  - after one warm-up run, the fsbpyr binary builds the fixtures with `--offline` and no clang
    on PATH
- **Result (2026-10-07):**
  - **self-hosting fixed point holds: stage1 == stage2 == stage3, byte for byte.** stage1 is
    built by the venv's CPython 3.14.7; stage2 and stage3 by fsbpyr binaries on the embedded
    3.14.8, with only uv on PATH and an empty cache. A self-build takes about 0.3 s
  - fixtures `hello` and `clicky` (click + rich + `files("clicky.data")`) build and run from
    `uv.lock`, `pylock.toml`, hashed `requirements.txt`, and unlocked (warning asserted); pip
    works without uv (superseded by D26: uv is required)
  - implementation notes that deviate from the text above:
    - `uv sync --locked` instead of `--frozen`: a stale lock is an error, not a silent build
    - the project's wheel is always built out of tree: `uv build` (sdist, then wheel), or
      `pip wheel` from a clean copy (removed by D26). It is installed `--no-deps` after the dependencies from
      the lock, so neither a stale setuptools `build/` directory nor uv's build cache (keyed on
      `pyproject.toml`, found by the self-hosting test) can ship old code, and the project tree
      is never written to. uv workspace members and path dependencies are rebuilt with
      `--reinstall-package`, using the names read from `uv.lock`
    - staging commands run in the project directory, so relative paths in requirements files
      resolve as expected; editable dependencies are rejected
    - uv workspace members use the workspace's `uv.lock` (`--package`); `--no-default-groups`
      keeps uv's default groups out; an explicit `--requirements` file wins over every lock
    - distributions installed from the lock but not required by the project are packaged,
      with a warning. Plugins listed only in the lock keep working (review suggestion to drop
      them declined)
    - bytecode is compiled with `optimize=0` whatever the build interpreter's `-O` setting;
      app modules may not shadow built-in or frozen modules: the project's own fail the build;
      a dependency's is packaged as a file with a warning, never imported, as in a venv
      (gnureadline's `readline.py`)
    - staging is a fresh temporary directory per build, not a reused `build/fsbpyr/staging`;
      the uv cache keeps it fast
    - installer-written dist-info files (`INSTALLER`, `REQUESTED`, `direct_url.json`,
      `uv_cache.json`) are not packaged; RECORD is rewritten to list only packaged files.
      Otherwise timestamps, staging shebangs and absolute paths would break reproducibility
      and leak build paths
    - native extensions are rejected with a clear error until Phase 5
    - CLI: `fsbpyr build [DIR] --entry -o --requirements --extra --interpreter --offline
      --no-source --no-smoke`; the other options arrive with their phases (`--interpreter`
      was removed by D26)

### Phase 5 — Native extensions and the cache (L) — **DONE 2026-10-07**

- `elf.py`, `natives.py`, `FsbpyrExtensionLoader`, materialization, self-heal, generation id.
- **Exit** (manifest §14), using fixture `heavy` (numpy, orjson, cryptography, pydantic):
  - `heavy --help` shows **0 bytes** decompressed from those dists and **0 extractions**
    (counters from `FSBPYR_TRACE`)
  - the first `heavy np` extracts only numpy's closure; the second run extracts nothing
  - 32 concurrent first runs all succeed
  - a corrupted cached `.so` gets repaired
  - a release that changes only Python code reuses the generation; a native change creates a
    new one
- **Result (2026-10-07):** all exit criteria hold (`tests/e2e/test_natives.py`):
  - `heavy --help` loads no module of numpy, orjson, cryptography, cffi, pydantic or
    pydantic-core, extracts nothing and does not even create the cache directory
  - the first `heavy np` extracts five files, all numpy's (`_multiarray_umath`,
    `_umath_linalg`, OpenBLAS, gfortran, quadmath); the second run extracts nothing
  - 32 concurrent first runs into an empty cache all succeed and leave no temporary files
  - a cached `_multiarray_umath` zeroed in place (same size) makes `dlopen` fail; it is
    re-hashed, re-extracted and the import succeeds
  - a Python-only rebuild keeps the generation id and extracts nothing; a native change gives
    a new id (unit test of `generation_id`)
  - measured on the dev machine: `heavy` is 57.5 MB and builds in about 2 s from a warm uv
    cache; `heavy --help` about 24 ms; `heavy np` about 60 ms warm, about 100 ms cold (about
    40 MB extracted)
  - implementation notes that deviate from the text above:
    - closures may also list NATIVE_EXT records (an extension may link against another
      extension's shared object); NATIVE_EXT/NATIVE_LIB blobs must carry a digest
    - blobs are decompressed in memory (`_fsbpyr.read`, at most 1 GiB) and written out, not
      streamed; `_fsbpyr.stream` is left for Phase 8 (done in 8b, 0.11.0)
    - the loader is `_fsbpyr_rt.native.PayloadExtensionLoader`, imported only when an
      extension is; the extension's `__file__` is its cache path
    - only shared objects an extension can reach are dependency-checked; others (for example
      libraries loaded with ctypes) are packaged unchecked. `.so` files that are not ELF are
      kept as data with a warning
    - compiled package `__init__` files (Cython) are EXTENSION records whose file is
      `__init__.<suffix>`; their `__path__` stays in the payload. Name clashes follow
      CPython's FileFinder order (package before module, extension suffixes before `.py`)
    - the stub exports only `Py*`/`_Py*` (D2): 1903 symbols instead of 3132, none from zlib,
      OpenSSL or libffi
    - `heavy model` (pydantic → zoneinfo → `sysconfig`) found that the runtime lacked
      `_sysconfigdata__linux_x86_64-linux-gnu`: module-name parts may now contain `-`, and
      `LAYOUT_VERSION` is 3
    - `--allow-system-lib` overrides `allow-system-libs`, like every other option; app names
      must be a single path component (they name the cache directory)
    - `FSBPYR_TRACE=1` prints one `fsbpyr-trace {json}` line on stderr at exit: payload
      modules per distribution, extracted paths and the read counters. There are no
      per-distribution byte counters, so "0 bytes from those dists" is checked as "no module
      of those dists loaded"
    - a failed `dlopen` from a `noexec` cache gets a hint to set `FSBPYR_CACHE_DIR`
    - native files over 1 GiB fail the build: without streaming they cannot be extracted
      (no cap since 8b, 0.11.0, and 8c, 0.12.0)
    - shared objects with importable names (`pkg/libfoo.so`) are extension modules, as in
      CPython; libraries an app loads with ctypes need FS_REQUIRED (Phase 6)
    - temp files of killed processes stay until generation GC (Phase 8; 8d, 0.13.0, removes
      them; since 0.15.0 new files are written unnamed, `O_TMPFILE`)
  - reviews (code and security; no CRITICAL or HIGH findings) led to: `$ORIGIN` expanded
    only as a whole token and correctly for top-level extensions (`_x.so` with
    `$ORIGIN/x.libs` was wrongly rejected); `DT_NEEDED` entries containing `/` rejected;
    payload paths and the app name re-checked before touching the cache; self-heal limited
    to `dlopen` failures; the user's cache root no longer chmod-ed; temp names that fit any
    file name
  - accepted risk (security review M1): the cache root's ancestors are trusted, like the
    variables that name them (D20). A root inside a directory other users can write lets
    them swap the tree between the checks and `dlopen`; the default `~/.cache/<app>` is not
    affected

### Phase 6 — Compatibility layer (M) — **DONE 2026-10-07**

- FS_REQUIRED packages (config + built-in rules).
- Multiprocessing re-exec patterns: spawn `--multiprocessing-fork`, forkserver and
  resource_tracker `-c` commands. Python 3.14 defaults to forkserver on Linux.
- Decide on `sys.frozen`.
- CA bundle fallback if PBS OpenSSL default paths miss the system store **[assumption — tested in
  Phase 6: they do not on Debian, Ubuntu or RHEL; the Nix layout's fallback is Phase 6b]**.
- `.pth` warnings.
- **Exit:** a compatibility matrix is green: click, typer, rich, httpx, requests + certifi,
  pydantic, jinja2, pyyaml, numpy, cryptography, orjson, zoneinfo/tzdata, sqlalchemy, and
  multiprocessing fixture `mp`.
- **Result (2026-10-07):** the matrix is green (`tests/e2e/test_compat.py`):
  - all 14 checks of fixture `compat` pass; the HTTP clients talk to a local server, TLS
    setup is checked by loading certifi's bundle
  - before any Phase 6 change, 13 of them already worked. Only `jinja2.PackageLoader` needed a
    real directory, which `fs-packages = ["compat"]` provides
  - fixture `mp`: spawn, forkserver and fork pools and `ProcessPoolExecutor`s, plus
    `shared_memory` (resource tracker), work for `module:function` and module entry points.
    Before the change, every re-executed child ran the app instead
  - `sys.frozen` stays unset (D21); multiprocessing argv is recognized and authenticated,
    never executed (D22; e2e tests pass `-c <code>` and replay forged child command lines)
  - CA bundle: no fallback needed. PBS's OpenSSL looks in `/etc/ssl` (`cert.pem`, `certs/`):
    Debian and Ubuntu verify through the hashed `/etc/ssl/certs` (real HTTPS request on
    Debian 13), and RHEL 9 links `/etc/ssl/cert.pem` to its bundle (layout checked in a Rocky
    9 container; the binary itself needs glibc 2.38 there, a Phase 9 item, moved to Phase 6c
    by D25 and done there)
  - `.pth` files are skipped with a warning (since Phase 4)
  - implementation notes that deviate from the text above:
    - FS_REQUIRED packages get every file extracted, `.py` sources included, so `__file__`
      exists and `inspect.getsource` works; only regular packages can be FS_REQUIRED
    - extraction happens when the package's spec is found, so its `__init__` can already read
      its data files; nested namespace packages also get their `__path__` in the cache
    - built-in rules: only `certifi`, which otherwise worked through a temporary copy of
      `cacert.pem` made again in every process
    - `--fs-package` overrides `fs-packages`, like every other option
    - known gaps, planned in Phase 6b: `inspect.getsource` on CPython-frozen stdlib modules
      (`os`, `codecs`) raises OSError; re-executing `sys.executable` for anything but
      multiprocessing (`-m`, `-c`) is not supported (opt-in support, D23; both closed in
      Phase 6b: `getsource` 2026-10-07, D23 in 0.16.0)
  - reviews: the security review found that recognizing the three command lines did not
    stop code execution through the functions they name (CRITICAL, reproduced against the
    `mp` binary: a pickle on stdin, a planted module through forkserver's `sys_path`, a file
    run through `main_path`); fixed by the parent-executable, fd and argument checks of D22,
    with an e2e test that replays the stdin and pipe variants. The code review found
    namespace packages two levels below an FS_REQUIRED package placed in the payload instead
    of the cache (fixed); the generation id now covers file modes, and `-X opt` flags are
    skipped when locating `-c`

### Phase 6b — Compatibility follow-ups (M) — **DONE 2026-10-07; interpreter re-exec (D23) 2026-10-13**

Closes the open issues that no later phase covered (review of open issues, 2026-10-07).

- **Data read relative to `__file__`.** A build-time scan (AST) of packaged modules outside
  FS_REQUIRED packages finds file-system calls whose path derives from `__file__` (`open`,
  `os.listdir`, `os.path` helpers, `pathlib.Path(__file__)`, `glob`) and prints one warning per
  package, naming module and line and suggesting `fs-packages`. The compatibility matrix gains
  packages known to read data this way (candidates: botocore, pytz, babel, matplotlib); a
  built-in rule is added only for a verified failure. Runtime hint per D24, measured first.
- **Shared objects no extension uses** (typically loaded with ctypes): a build warning when a
  NATIVE_LIB is in no closure and not inside an FS_REQUIRED package.
- **`.pth` files.** Path lines naming a directory inside site-packages make its modules
  importable at top level (module records rooted there, after site-packages in precedence, as
  CPython orders them). Code lines never run; they produce a warning, except recognized
  redundant ones (`*-nspkg.pth` namespace shims, which PEP 420 namespaces replace, and
  `distutils-precedence.pth`).
- **Sources of CPython-frozen stdlib modules.** The payload already holds them; the boot code
  lets `FrozenImporter.get_source` fall back to them, so `inspect.getsource(os)` works.
- **Post-import fixups.** A small table in the boot loader, consulted after a payload module
  executes, for the two items below.
- **CA store fallback** for distros whose `/etc/ssl` holds neither `cert.pem` nor a hashed
  `certs/` directory **[assumption — NixOS; verified in Phase 6b with the Nix CA layout in a
  `debian:trixie` container]**: when `ssl` is first
  imported, default contexts also load the first existing well-known system bundle. No
  environment variable is set, so child processes are unaffected.
- **Multiprocessing children start from the parent's own binary** (`/proc/self/exe`, set when
  `multiprocessing.spawn` is imported): after an in-place upgrade parent and children never mix
  code versions, and the D22 check keeps passing.
- **`FSBPYR_VERIFY=1`** also re-hashes cached files (natives and FS_REQUIRED files) on use and
  repairs mismatches; without it a cache hit stays a size check (superseded by D31, 0.6.0:
  every process hashes cached files; `FSBPYR_VERIFY` is gone).
- **Interpreter re-exec (D23):** `interpreter-reexec = true` / `--interpreter-reexec`. `-m`
  runs a payload module as `__main__` with `runpy`, `-c` runs the given code; interpreter flags
  before them are accepted and ignored, as for multiprocessing.
- Unchanged, documented as accepted: without `/proc` multiprocessing children are refused
  (fails closed; fork still works); forkserver does not receive app-added `sys.path` entries;
  finding an FS_REQUIRED package's spec extracts it.
- **Exit:**
  - the scan warns for a fixture package that opens data via `__file__`, not for one using
    `importlib.resources`; the extended matrix is green
  - the D24 hint is either measured below 1% and tested, or recorded as dropped
  - a `.pth` path fixture is importable; namespace shims produce no warning
  - `inspect.getsource(os)` works inside a built app
  - the CA fallback verifies TLS in a NixOS container, or the assumption is recorded as false
    and the item dropped
  - multiprocessing keeps working after the binary is replaced on disk mid-run
  - under `FSBPYR_VERIFY=1` a same-size corrupted cached data file is repaired
  - with `interpreter-reexec`, app code running `sys.executable -m <payload module>` and `-c`
    works; the same command lines started from a shell are refused; without the option
    nothing changes
- **Result (2026-10-07):** all items except D23 are done; 512 tests pass, coverage 93%.
  - build hints: one warning line lists the regular packages whose code reads files next to
    `__file__` (AST heuristic over modules that mention `__file__`, skipping `tests`
    directories and `__main__` blocks), and one per distribution names shared objects no
    extension loads (no `PyInit_<name>` symbol, in no closure). Known limits: reads through
    other modules, attributes or helper functions are missed (botocore), and some hits are
    build tools or have fallbacks (numpy's f2py, cffi's recompiler, pytz). Since v0.3.0
    (owner decision 2026-10-08) the `__file__` guess is shown only with `fsbpyr build
    --hints`: by default the build prints no false alarms
  - fixture `datafiles`: botocore, babel and matplotlib fail without their files on disk and
    are now built-in FS_REQUIRED packages; pytz works (it falls back). Extracted on first
    import: babel 33 MB, matplotlib 27 MB, botocore 25 MB
  - D24 runtime hint dropped: a Python `open` audit hook costs +1.5% on an import-heavy start
    (60 interleaved runs, 2834 audit events; a first sequential run showed +3.9%), above the 1%
    gate. A C hook in the launcher would likely be cheaper; not attempted (new scope)
  - `.pth` files: path lines make modules, packages and namespace packages importable by
    their names relative to that directory, after site-packages; code lines warn unless they
    are namespace shims or `distutils-precedence.pth`; dot-prefixed `.pth` files are skipped
    as `site.py` does. Package and namespace `__path__` is now derived from the module name.
    Not covered: FS_REQUIRED packages below a `.pth` directory (their `__path__` is not on disk)
  - frozen stdlib sources: `inspect.getsource(os)` works. Function-level lookups
    (`inspect.getsource(os.makedirs)`) and source lines of frozen frames in tracebacks do not:
    linecache reads `<frozen ...>` names only from `__file__` on disk
  - CA fallback: verified in a `debian:trixie` container with the Nix CA layout (bundle only,
    no `cert.pem`, no hashed links): HTTPS works with the fallback and fails without it. The
    `nixos/nix` image has that layout but no `/lib64` dynamic loader, so on NixOS fsbpyr
    binaries need nix-ld, like any foreign dynamically linked binary
  - multiprocessing children start from `/proc/self/exe`; an e2e test replaces the binary
    mid-run. This found that a replaced binary's children saw their executable as
    `path (deleted)`: the launcher now keeps the original path unless that name is still
    this file (runtime rebuilt). Self-hosting reads its own runtime from `/proc/self/exe`
  - `FSBPYR_VERIFY=1` re-hashes cached files (natives and FS_REQUIRED files) on use; same-dist
    duplicate-module warnings are kept only for two pure-Python modules
  - reviews (code and security; no CRITICAL or HIGH findings) led to: namespace packages and
    compiled packages below `.pth` directories, hint names that `fs-packages` accepts and
    less noise, the CA fallback re-checking `SSL_CERT_*` at use and never raising, robust
    `.pth` reading, self-hosting reading `/proc/self/exe`, and a stricter `(deleted)` rule
  - **open: interpreter re-exec (D23)** was not implemented in this phase (done in 0.16.0,
    below)
- **D23 done (0.16.0, 2026-10-13):** `interpreter-reexec = true` / `--interpreter-reexec` records
  `interpreter_reexec` in the build info. The boot module looks at it only when argv holds
  `-m` or `-c` (two scans of argv otherwise, as for multiprocessing); `_fsbpyr_rt.reexec`
  takes `-m MODULE` or `-c CODE` after interpreter flags (`-X` and `-W` take a value), and
  runs it when the parent runs this executable (D22's check): `-m` through
  `runpy._run_module_as_main` with `sys.argv` as CPython leaves it, `-c` in `__main__`.
  Any other caller gets the app, with these arguments. Asked for by tsim's shell, which runs
  its helpers as `sys.executable -m tsim....`
  - tests: `test_reexec.py` (command lines, the opt-in, the parent check, `-m` and `-c` as
    CPython runs them), the boot run with and without the option, and in `test_compat.py`
    the `mp` fixture starting `-m mp.helper` and `-c` children with and without the option
    and the same `-m` refused from the test process
  - the e2e tests were not run on the host that built this (no clang 22 for a runtime of
    their own; they never use the user's cache): the same scenarios ran by hand against the
    `mp` fixture built with the cached runtime, multiprocessing included

### Phase 6c — Portable glibc baseline (M) — moved from Phase 9 (D25) — **DONE 2026-10-07**

- Link the stub against an old glibc instead of the host's (S1 found `GLIBC_2.38`). A short
  spike picks between host clang with an extracted old-glibc sysroot and a podman build image
  with LLVM 22 installed; the baseline target is glibc 2.28 (manylinux_2_28: Debian 10+,
  Ubuntu 20.04+, RHEL 8+) unless the spike shows 2.17 is as cheap. The baseline is part of the
  runtime id.
- `fsbpyr build` reports the app's effective glibc requirement, max(stub, wheel platform
  tags), stores it in the build info, and warns when a wheel raises it above the stub's.
- **Exit:**
  - `hello`, `heavy` and `compat` binaries run in Debian 12, Ubuntu 22.04, Rocky 8 and Rocky 9
    containers
  - the reported requirement matches the highest versioned symbol found in the stub and the
    extracted natives
  - self-hosting fixed point and startup budget still hold
- **Result (2026-10-07):** all exit criteria hold; 551 tests pass, coverage 93%.
  - spike: host clang with a sysroot extracted from five pinned Debian 10 packages (glibc
    2.28, GCC 8 runtime objects, kernel headers); no container is needed. Debian's absolute
    symlinks (`libpthread.so -> /lib/...`) first resolved to the build host's glibc 2.41,
    whose `libpthread` is an empty stub: they are rewritten relative to the sysroot. glibc
    2.17 was not pursued: it would need RPM unpacking for RHEL 7, which is past end of life
  - the stub now needs **glibc 2.25** (GLIBC_2.38 before; 2.28 since 0.7.0, D32); the runtime
    id includes the
    sysroot pin; linking takes about 100 s instead of about 50 s
  - uv chose cryptography's manylinux_2_34 wheel on this host, so `heavy` needed glibc 2.34.
    Every uv install now passes `--python-platform x86_64-manylinux_2_28` (from the runtime's
    sysroot pin; uv also accepts the project's own locally built wheel with it). A dependency
    without such a wheel is built from its sdist on the build host, and the build warns when a
    native file needs more than the 2.28 baseline. The pip fallback was later removed (D26).
    Since 0.17.0 such a build is redone against the sysroot (below)
  - `fsbpyr build` reports the requirement (`glibc >= 2.25`), stores it in the build info,
    and reads it from DT_VERNEED of the stub and every bundled native file
  - `hello`, `heavy` and `compat` (every check except the two HTTP clients, which need the
    test's local server) run in 13 distributions, glibc 2.28 to 2.44: Debian 10, 11, 12 and
    13, Ubuntu 20.04, 22.04 and 24.04, Rocky 8 and 9, Amazon Linux 2023, openSUSE Leap 15.6,
    Fedora and Arch. The reported requirement equals `llvm-objdump`'s highest GLIBC version
    over the stub and the natives, and is at most 2.28 (`tests/e2e/test_portability.py`)
  - without uv (pip path, `heavy` from a hash-pinned requirements.txt) pip chose
    cryptography's manylinux_2_34 wheel: the build reported `glibc >= 2.34` with a warning, and
    on Debian 10 and Rocky 8 everything but `heavy crypto` worked (`GLIBC_2.30 not found`).
    Hence D26: uv is required; the same project built by uv reports `glibc >= 2.28` and runs
    on all 13 distributions (`heavy-pip` in the portability matrix)
  - startup: `hello` 1.11x bare `python3.14 -I -S`, inside the 1.15x budget
  - clang-22 takes `crt*.o` and `libgcc` from the sysroot's GCC 8 (checked with `-###`)
  - reviews (code and security; no CRITICAL or HIGH findings) led to: symbol-version parsing
    with one shared work budget, deduplicated names and a 4096-byte string limit, kept apart
    from the dependency checks; the sysroot directory and the runtime id keyed by a digest of
    the pinned packages, not just their name; the verified bytes are the extracted bytes;
    extraction failures reported as such; https-only downloads; `fsbpyr runtime build`
    reporting sysroot errors; the glibc warning measured against the 2.28 baseline (the
    manylinux_2_28 cryptography wheel no longer warns); `GLIBC_ABI_DT_RELR` counted as 2.36

### Phase 7 — Compression and measurements (M) — **DONE 2026-10-09** (reduced by D37)

- **Progress (2026-10-08):** D29 replaces the profiles: level 9 by default, `--fast-compression`
  (or `fast-compression = true`) for level 3. aiagent: 167.7 MB → 157.4 MB (140.0 MB with
  `--no-source`, now documented in `README.md`). D30 packs small blobs into blocks (format v2):
  aiagent 157.4 → 149.6 MB. Measured and set aside: a zstd dictionary (blocks gain more), and a
  two-stage stub (D29). xz -6 with the x86 filter for native files (aiagent's 162 MB: 48.7 →
  38.0 MB; decoding 1.0 s on one thread, 0.23 s on 8) becomes the opt-in `--best-compression`
  (0.8.0, D33); zstd 9 stays the default. D31 (0.6.0): every
  executable checks its whole file before it starts (format v3). D32 (0.7.0): extracted files
  use the same digest and routine (format v4). D35 (0.9.0): the launcher walks, checks and
  restores cached files itself (a torch app's later runs 615 → 538 ms). D36 (0.9.1): the checks read files instead of
  mapping them (the torch app's peak RSS 577 → 349 MB). Timings are recorded in
  `docs/performance.md`, the phase's report (D37).

- ~~`fast` / `balanced` / `compact` (manifest §10)~~ replaced by D29. ~~Grouped frames for tiny
  modules; a zstd dictionary~~: dropped (D37; the dictionary was measured and set aside).
- ~~Benchmark harness (§8) and CI regression gate~~: dropped (D37). Regressions are caught by
  exact counts: `tests/e2e/test_natives.py` reads `FSBPYR_TRACE` (`--help` loads no module of
  the heavy distributions and extracts nothing; a first run extracts only the closure it needs;
  a later run extracts nothing), `tests/e2e/test_imports.py` checks that startup imports only
  built-in, frozen and startup modules and compares the cache sync's outcomes; the timing test
  that stays is the startup budget (`test_startup_is_within_budget_of_bare_cpython`).
- The report is `docs/performance.md`.
- **Fast exit, evaluated (2026-10-09):** flushing stdout and stderr, running the `atexit`
  handlers, then `os._exit` (21 runs of each, interleaved; 201 for the no-op app): the torch app
  1070 → 857 ms, the same script in its venv 1004 → 783 ms, the no-op app 9.4 → 8.3 ms. What it
  saves is the libraries' own teardown, the same in a venv, not fsbpyr's overhead. What it
  skips: files the app opened and never closed lose their buffered data, non-daemon threads are
  killed, `__del__` methods do not run. **Dropped** (owner decision 2026-10-09): an exit that
  leaves debris for something else to clean up is not worth it.
- **Exit (D37):** the compression work is released; `docs/performance.md` records each
  release's measurements; the count-based tests and the startup budget pass; fast exit is
  evaluated and dropped. **DONE 2026-10-09.**

### Phase 8 — Hardening (L; D38) — **8a DONE 2026-10-09 (0.10.0), 8b DONE 2026-10-09 (0.11.0), 8c DONE 2026-10-09 (0.12.0), 8d DONE 2026-10-10 (0.13.0), 8e DONE 2026-10-10 (0.14.0), 8f DONE 2026-10-10 (0.15.0)**

Principle (owner): normal cases fast, edge and extreme cases correct, never a crash. Every
item below gets explicit tests in the normal test run. Steps, each released on its own:

**8a — Safety nets first** (they guard the rest of the phase):
- **Edge-case tests**, table-driven, against both the C launcher and the Python reader:
  - empty and tiny files: 0 bytes, shorter than the 64-byte trailer, exactly the trailer,
    `payload_size` 0 or exactly the header;
  - sizes and offsets at 0, 1, each limit ±1, 2^63 and 2^64−1; offset + size that wraps; a
    table count whose count × record size overflows; a string at the string table's last byte,
    without its NUL, or 2^32−1 long;
  - compressed data that lies: a zstd frame declaring another size than its record, a tiny xz
    stream claiming gigabytes, 0, 1 or 2^32 pieces, a block member at its block's last byte;
  - indices: NONE (0xFFFFFFFF), the table's count and count−1, a closure listing itself or
    longer than the table;
  - paths: empty, `.`, `..`, `a//b`, a trailing `/`, an embedded NUL, a component of 255/256
    bytes, very long and very deep paths.
- **ASan/UBSan stub variant**: a sanitized runtime the test run builds once; the e2e tests that
  feed crafted, damaged or extreme payloads and the cache tests run against it too.
- **Bounded stack by construction**: `-Wvla` and `-Wframe-larger-than` in the launcher build;
  no recursion over input. Module code is unmarshalled by CPython, whose nesting limit raises.
- **libFuzzer harness** on trailer, header, table and record parsing and blob decoding, seeded
  with the edge cases: a short run in the test run, 10 minutes clean before each release.
- Bounds checks across `_fsbpyr`, and declared-size checks before allocation.
- **Done (0.10.0):**
  - 65 cases and 14 paths in `tests/support/edges.py`, each with what the reader and the
    launcher do; read by the Python reader, by both launchers (an app that walks itself through
    `_fsbpyr`, `tests/support/walk.py`, syncing closures as imports do) and as the fuzzer's corpus
  - the sanitized runtime (its own id, `-sanitized`) runs the e2e tests marked `sanitized`; a
    finding exits 99, which no test can take for an expected failure (a wild read through
    `ctypes` is the control)
  - frames of at most 8192 bytes (the largest is 4.9 KB), checked per source without LTO, with
    controls that the check refuses a VLA and a large frame (the code review found it a no-op
    under a misspelt flag)
  - `tests/fuzz/payload_fuzz.c` (libFuzzer with ASan and UBSan): 30 s in the test run
    (`FSBPYR_FUZZ_SECONDS`), 600 s clean for 0.10.0
  - found: a cached file whose name the file system refuses (a 256-byte component) was reported
    as modified; now that fails the sync with the real error, and any other open error replaces
    the file (EINTR is retried)
  - test runs keep only failed tests' temporary directories: a run builds about 8 GB of
    executables, and `/tmp` is often a RAM disk
  - left for 8b, where the cases flip: directory paths over 4 KiB and over 2048 levels below
    the cache root are refused; the 1 GiB cap stays, so an xz stream claiming more is refused
    before it is decoded (flipped in 8b, 0.11.0)

**8b — Streaming at run time, no limits of ours** (D38):
- Extraction streams: stored bytes are read a window at a time (the window released after use),
  decoded in pieces (zstd and xz streaming; xz pieces in parallel, each written where it
  belongs), hashed as they are written, written in pieces of at most 1 MiB; runs of zeros
  become holes. The 1 GiB in-memory cap goes; blobs up to 2^62 bytes.
- The tree digest keeps a bounded window of leaf digests, whatever the file's size.
- The cache sync takes files in chunks (`files_below` gives a range), remembers checked files
  in a bitmap, and needs no per-file memory beyond a chunk; directory paths and depth have no
  limit of ours (only the OS's), and a deep path does not hold a descriptor per directory level
  (until 0.11.0 it did: 1,000 levels needed 1,000 descriptors, past a common soft limit of 1,024).
- Resources opened as files (`importlib.resources` `open()`) stream; `read_bytes()` returns
  what the app asked for.
- **Done (0.11.0):**
  - streams (`fsb_stream_*`): every codec a window of at most 64 MiB at a time; zstd frames
    decoded a window at a time, checksum checked at their end; a single xz stream holds at
    most a piece (more is in pieces), pieces decode 16 per window in parallel; a stream that
    failed fails every call after; a seek decodes nothing until the next read
  - payload pages read through the mapping counted in resident memory (a sequence of reads of
    10 GB would have kept 10 GB): every 256 MiB read, the data area's pages are dropped
  - extraction: decoded, hashed and written a window at a time under a temporary name, renamed
    once the digest is right; pieces of zeros are holes (a file is truncated to its size only
    when it ends in one: truncating every file cost the torch app's first run 0.4 s on XFS)
  - the digest keeps 4096 leaf digests (128 KiB) at a time, whatever the file's size
  - sync: `files_below` gives a range (two binary searches); 4096 files per job, a bitmap of
    the files checked; the number written is returned, their paths kept only for
    `FSBPYR_TRACE`; no limit on a path's length or depth; 16 directory levels stay open,
    deeper ones are opened again from the deepest open one
  - `_fsbpyr.read` has no cap; `_fsbpyr.stream`; resources over 16 MiB opened as files stream
    and seek
  - found by the reviews: the launcher let SIGPIPE and SIGXFSZ end the process (a write to a
    closed pipe or socket, a write past `ulimit -f`), where `python` raises BrokenPipeError or
    fails with EFBIG: signal handlers are now installed as `python` installs them; a stream of
    a block member pointed into the shared decoder's slot (copied out now); cached files are
    opened with O_NOCTTY
  - tests: windowed digests and streams of every codec at window boundaries (probes built
    with small windows, under ASan/UBSan/LSan), streams fuzzed, a sync across chunks and 40
    levels deep under a descriptor limit, 1.5 GiB + 600 MiB + 80 MiB extracted and streamed
    within 512 MiB of added resident memory, holes, signals as in `python`
  - measured (torch app, Core Ultra 9 285, XFS): first run 1696 → 1702 ms; later runs 1080 →
    1082 ms, peak RSS 349 → 346 MB (the check's 16 MiB buffer is mapped now: from malloc, the
    three 4096-file jobs of torch's fs-package left it in the heap, 364 MB); startup 7.94 →
    8.03 ms (Python's own signal setup)
  - left for 8c: the builder still holds content in memory (done in 8c, 0.12.0)

**8c — Streaming when building** (D38): blob contents come from files, not memory; compression
(zstd with the size pledged, xz in pieces), digests and ELF scanning stream; the writer lays
out the tables, streams the blob data after them and hashes the result in a second pass; zero
runs become holes. The reader, `inspect --verify` and the runtime's stdlib copy stream as well.
A 1 TB executable (a 1 TB file of zeros stored raw, so the file is sparse) builds, checks
itself, extracts and loads within 1 GB of RSS: run before each release (on demand only since
2026-10-18, owner decision: `FSBPYR_LARGE_SIZE=1099511627776`, 22–35 min); the normal test run
does the same with 5 GiB (past every 32-bit size).
- **Done (0.12.0):**
  - blobs hold their bytes in memory or as an Extent (a range of a file: a staged file's own
    bytes, or the build's spill); an Extent's identity is the tree digest of its bytes, and the
    writer checks the bytes against it as it copies them: a file that changed while the build
    read it fails the build (the security review found a shrunken file shipping zeros)
  - the spill: one temporary file next to the output, unlinked at once; every blob a build
    makes moves its bytes there; files over 16 MiB are compressed from disk a piece at a time
    (zstd with the size pledged, xz pieces in parallel, each read from its file), their digest
    computed as they are read; a module over 16 MiB is kept as data (compiling needs it whole)
  - natives are paths: mapped read-only for their ELF tables, searched a window at a time
  - the writer reserves the tables, streams the blob data after them (whole pieces of zeros
    left as holes), builds each block as it is placed, writes the tables, then hashes the file
    a leaf at a time (leaves in holes not read): `file_digest`, which `inspect --verify` uses
  - the reader feeds compressed data a piece at a time and drops the mapped pages it read
  - the launcher reads raw blobs of a leaf or more from the file, never through its mapping
    (on tmpfs a mapped hole takes a page of memory: a sparse executable ran out of tmpfs), with
    the descriptor it keeps (checked by device and inode: an app may close it), else
    /proc/self/exe
  - the runtime's stub and stdlib are read whole: they are the runtime's size, not the input's
  - the builder still holds a record of each file and blob (a few hundred bytes each): memory
    grows with the number of files, not with their size
  - measured: building the torch app (Core Ultra 9 285) took 1204 MB of peak RSS, 1869 MB with
    `--best-compression` (eight xz encoders); now 243 MB and 641 MB, in 22.4 s and 26.9 s as
    before (22.2 s, 27.3 s); the executable is 0.3 MB larger (zstd fed a piece at a time). 1 TiB
    (raw, sparse): built, checked, streamed and extracted in 614 s, `inspect --verify` in 32 s,
    each within 1 GB (asserted by tests/e2e/test_large.py with FSBPYR_LARGE_SIZE)

**8d — Cache GC** (D38):
- App caches: a marker at the cache root; each generation and resource tree has a lock file
  that every process using it holds shared (its mtime, touched at most once a day, says when it
  was last used); temporary files go to a scratch directory per tree and are locked while
  written. A GC run takes a GC lock, then removes, after locking each exclusively and renaming
  it to a trash name: trees unused for 7 days (under disk pressure every one not in use), stale
  trash and temporary files, trees of older fsbpyr layouts; never the running app's own
  generation, nothing locked, nothing outside our own subtrees and names.
- Triggers: the launcher checks at start, with one `stat` and one `statvfs`, whether a run is
  due (a day since the last, or under 10% free and an hour since the last); if so a detached
  background process collects (closing every inherited descriptor, at idle priority), so the
  app's start is not slowed. `FSBPYR_GC=1 app` collects in the foreground and exits;
  `fsbpyr cache gc` collects the tool's cache and every app cache it finds by its marker.
- The tool's cache: runtimes, Python distributions, sysroots and downloads that the running
  fsbpyr does not use and nothing used for 7 days, under the same rules; checked when `fsbpyr`
  runs, at most once a day.
- **Design** (before the code):
  - App cache root: `<root>/.fsbpyr-cache` (the marker, written when the launcher first uses
    the root); trees `<root>/runtime/<32 hex>/` and `<root>/resources/<32 hex>/`, each with
    `.lock` (held LOCK_SH by every process that syncs into the tree, for its whole life; its
    mtime is touched when older than a day) and `.scratch/` (temporary files, held LOCK_EX while
    written, renamed from there into `site/`); `<root>/.trash/` (trees being deleted);
    `<root>/.gc` (GC lock, held LOCK_EX by a run; its mtime is the last run's start).
  - Taking a tree (C, `_fsbpyr.hold`, before the process's first sync into it): open or create
    `.lock` through the checked directory walk, LOCK_SH (waiting for a GC that holds it),
    then check that it is still the tree's `.lock` (a GC renames the tree while it holds the
    lock): if not, take the tree again (it is created afresh).
  - A run (`_fsbpyr_rt.gc`, standard library only; the CLI uses the same module): only a root
    with the marker; LOCK_EX|LOCK_NB on `.gc` (busy: return); touch `.gc`; under 10% free is
    pressure. Each tree whose name is ours and that is a directory of ours: LOCK_EX|LOCK_NB on
    its `.lock` (failing: in use, kept, its stale temporary files removed: each one we can lock
    is not being written); a tree unused for 7 days, or any not in use under pressure, is
    renamed into `.trash/` while locked, then removed (`shutil.rmtree`, which never follows a
    symlink). A tree without `.lock` (written before 0.13.0) counts from its directory's mtime.
    Everything in `.trash/` goes. Nothing else in the root is touched.
  - Due (C, at the launcher's start, before Python; not when `FSBPYR_GC` is set): `stat` of
    `<root>/.gc`; missing with the marker present, or older than a day: due; else `statvfs` of
    the root: under 10% free and older than an hour: due. Due: touch `.gc` (no other start
    spawns one), then fork twice, setsid, nice 19 and the idle I/O class, every descriptor
    closed (0-2 on /dev/null), and exec the executable with `FSBPYR_GC=background`.
  - `FSBPYR_GC=1 app` (or `background`): the boot code runs the collection of that app's cache
    instead of the app, reports on stderr (`1`) and exits 0.
  - `fsbpyr cache gc`: the tool's cache, then every directory with the marker in
    `${XDG_CACHE_HOME:-~/.cache}` and `$FSBPYR_CACHE_DIR`. The tool's cache: runtimes, PBS
    distributions, sysroots and downloads each get a `.lock` held LOCK_SH while a command uses
    them (mtime: last use); what the running fsbpyr's pins name is kept; the rest goes by the
    same rules. `fsbpyr` checks it at its start, at most once a day (`.gc` there too).
  - The tool's cache and the app cache of fsbpyr itself (an app named `fsbpyr`) are the same
    root: the two collections touch only their own subtrees.
- **Done (0.13.0):**
  - as designed above, with what the code and security reviews changed (they replace the
    design where the two differ: no `_fsbpyr.hold`, no `shutil.rmtree`, not all of `.trash`):
    - the marker is written only into a root that holds nothing but our names and is new
      (empty) or holds a tree or item of ours (a cache of an earlier version): a directory of
      the user's that `FSBPYR_CACHE_DIR` names is never marked, so never collected (the README
      says how to opt one in: the marker made by hand); the tool's cache needs the marker as
      well (`hold()` writes it); a tree or item is ours only with its lock or what fsbpyr
      writes in it (`gc.PROOF`: an app tree's `site`, a runtime's `runtime.json`, a
      distribution's `python/PYTHON.json`, a sysroot's `.fsbpyr-sysroot`), and the tool's
      collector removes only names the tool gives (runtime ids, `cpython-*` distributions,
      sysroot identities, their downloads, its staging names)
    - the tree is taken in the sync, once the records are checked (nothing is written for a
      payload that is refused); `module.c` keeps the trees a process holds, and takes one again
      if the app closed its lock; taking a tree is best effort: a read-only cache, a file system
      without locks or a lock that is no regular file of ours leave it unheld, never failing
    - a tree from before 0.13.0 gets its lock (with the tree's mtime as its last use) and is
      locked before it is judged: a process taking it meanwhile waits and makes it afresh
    - the lock is let go once the tree is renamed into the trash; the trash is emptied at the
      end of the run, by a remover that holds three descriptors whatever the depth and makes
      a directory it may not write to writable; only names the run gives are removed there
    - what cannot be removed is reported and the run goes on; `fsbpyr cache gc` collects every
      root and exits 1 if anything failed
    - due: the stamp is claimed under its lock (one start of many starts a run; none while a
      run holds it) and must be touched (a read-only cache starts nothing); a stamp dated ahead
      is due; the free-space check cannot overflow; an app name the boot code refuses starts
      nothing; the process started runs in `/` with `FSBPYR_CACHE_DIR` set to the root
    - `FSBPYR_GC=1` or `background` collect (exit status 1 if anything could not be removed);
      any other value runs the app
    - locks are opened for writing where they may be (NFS emulates flock: an exclusive lock
      needs it); a lock that cannot be taken leaves its tree alone; a lock dated ahead (the
      clock was set back) is brought back to now
    - not from a set-uid start, nor as process 1 (no zombie of ours goes unreaped)
  - known limits: a process of a build older than 0.13.0 holds no lock, so its tree can go 7
    days after it was made while one runs (the next start extracts it again); a long-running
    process's lock is touched only when it is taken, so its tree can go soon after it ends;
    removing a tree costs time quadratic in its depth (0.9 s at 1000 levels)
  - the tool's cache (`runtime/collect.py`, `runtime/hold.py`): its own stamp and trash
    (`.gc-tool`, `.trash-tool`); staging directories and partial downloads go after a day,
    never sooner (a build or a fetch may be writing them, pressure or not); what the running
    fsbpyr's pins name stays; `XDG_CACHE_HOME` that is not absolute is ignored, as for apps
  - every test runs with `FSBPYR_GC=0` (tests/conftest.py) unless it asks for a collection;
    tests/e2e/test_large.py measures each process's own peak (VmHWM: ru_maxrss kept pytest's
    peak across the exec, past 1 GB once the suite had built a runtime)
  - tests: each rule of both collectors (marker, age, in use, pressure, kept, items from before
    their locks, foreign names, symlinks inside and as locks, FIFOs, stale and read-only
    temporary files, read-only directories, 300 levels with 16 descriptors, one run at a time,
    the trash, failures reported), a tree taken while a run removes it, marker consent, the
    launcher's names checked against the collector's, the tool's names, `fsbpyr cache gc`;
    due.c probed (tests/probes/due.c, statvfs wrapped): every due rule, the claim among 8
    starts at once, the root as the boot code finds it, and the process started (descriptors,
    session, priority, I/O class, directory, root); end to end: a held tree with its scratch,
    `FSBPYR_GC` values, the background run a day after the last and after none, its root
    found through XDG, a directory of the user's never marked

**8e — Cache-root ancestors and security tests**: walk the root's path from `/` (following
symlinks component by component) and require every directory on the way to be owned by root
or the user and not writable by others unless sticky; tests for path traversal in names,
symlinked cache directories, a foreign-owned cache, a cache root below a directory others can
write, a truncated binary.
- **Done (0.14.0):**
  - `launcher/way.c` (`fsb_way_check`, at every `open_root`, and again, whole, whenever the
    first walk ended at a missing part: made by us or by someone else meanwhile, it is caught)
    walks the root's path from `/`
    with `O_PATH` descriptors, one component at a time: a symlink, which must be root's or the
    user's (one planted in a sticky directory is refused), has its target spliced into what is
    left (40 at most, as the kernel); `..` goes up from the directory the walk is in; the first
    part that does not exist ends the walk, unless a `..` follows it (refused: as the kernel
    would, and it could lead out of what was checked); every directory on the way, the root's
    own included, must be owned by root or the user and writable by no other user unless
    sticky: not by others, nor by a group other than the user's primary one (a user-private
    group is the norm; a shared one such as `staff` is refused; known limit: a shared primary
    group, such as `users`, and ACL entries the group bits show are taken as the user's) (`FSB_CACHE_EXPOSED`: "refusing cache
    directory X: other users may write to it"); the root must also be the user's (as before)
  - in a user namespace other than the initial one (a sandbox), a directory owned outside it
    shows as the overflow uid (nobody): it counts as root's, the sandbox's own layout (else
    every cache there is refused at `/`), but only where no uid inside maps to the overflow uid
    (`podman unshare` maps it: a real nobody could own it) and only above any directory others
    may add to (below `/tmp`, nobody's could be anyone's); known limit: a directory of another
    host user bound into the sandbox, above such a directory, counts as root's too
  - `make_root` and the tool make missing parents 0755 (as far as the umask leaves it): a
    umask of 0 no longer makes them writable by others (and then refused)
  - `due.c` claims a collection only for a root whose way is trusted (the collector would
    refuse it: no spawn a day for nothing)
  - the same rule in Python (`gc.way_problem`, a `Problem`): the collector refuses a root whose
    way fails, and so does the tool's cache (`registry.secure_directory`, which still makes its
    own directory private rather than refusing it, through a descriptor, refuses a symlink
    there of another user's, and checks the whole way once the directory exists); the mirror
    uses it, and the contract compares C and mirror on each case
  - `fsbpyr cache gc` adopts the caches apps of earlier versions made (they mark nothing): a
    directory of ours in the cache home, its way trusted, holding only our names and a tree of
    ours (32 hex digits, ours, with its lock or its site) gets the marker
    (`gc.mark(..., new=False)`: an empty directory is some other program's, maybe)
  - tests: directories others or another group may write to (at the root, on the way, through
    a symlink, through `..`), sticky ones, our own group's, a directory and a symlink of
    another user, a real directory of another user on this system, symlinks with trusted
    targets, 40 and 41 symlinks, a loop, a file, a name too long, `missing/..`, relative roots
    (the launcher's working directory), a user namespace, a root of another user (`/usr`), the
    tool's cache and the collector refusing, parents made 0755 under umask 0; the launcher's
    cases under ASan/UBSan as well. Path traversal in names, symlinks inside the cache, a
    foreign-owned tool cache and truncated binaries were tested before
    (`test_launcher_never_extracts_a_file_whose_path_could_leave_the_cache`,
    `test_symlinks_inside_the_cache_are_refused`, `test_foreign_owner_is_refused`,
    `test_corrupt_executable_fails_cleanly`, `test_executable_rejects_every_truncation`)

**8f — Up to 8 cores in every phase** (D39):
- **Design** (before the code):
  - Format v6: `Codec.ZSTD_PIECES = 5`, `ZSTD_PIECE = 8 MiB`: the blob record names its first
    piece and their number, as codec 4 does; each piece is a zstd blob of `ZSTD_PIECE` bytes of
    content (the last 1 to `ZSTD_PIECE`), one frame with content size and checksum. Any file
    (native or data) or module source may be in zstd pieces; xz pieces stay natives only
  - Building: `Blob.zstd` and `Spill.compress_files` cut content over `ZSTD_PIECE` into pieces;
    the natives and the large files of a build are compressed together, their pieces on up to 8
    threads, largest first; a piece is read from its file, hashed and compressed on its thread;
    the in-memory files and the blocks on up to 8 threads too
  - Modules: the classifier collects the work (source, path, names) in its order, a pool of up
    to 8 processes compiles, canonicalizes and compresses (code and source), and the results are
    registered in that same order: the same warnings, the same winners, the same bytes; small
    builds (under 256 modules) compile in the process itself, with the same results
  - Running: the launcher decodes zstd pieces as it decodes xz ones (`decode_pieces`, a window
    of `FSB_STREAM_WINDOW / ZSTD_PIECE` pieces at a time on up to 8 threads; a piece checked by
    its frame's size and checksum); the Python reader decodes pieces on up to 8 threads
  - Extracting: a cache sync checks every file first, then writes the missing and changed ones
    on up to 8 threads: files of up to 8 MiB, raw, in one frame or in a block, in chunks of
    neighbours (32 files or 2 MiB, and the rest of the last one's block, decoded once); larger
    files, xz and pieces by the calling thread, one at a time, since their pieces are decoded in
    parallel already. Work that `fsb_parallel` runs calls it again on its own thread only
    (hashing what a writer writes): never more than 8 threads. The failure reported is the first
    in path order among the pool's files, else among the others; a pool that runs out of
    descriptors or memory leaves the rest to the calling thread
  - A new file is written unnamed in its directory (`O_TMPFILE`) and linked into place
    (`linkat` by descriptor, Linux 6.10+, else through /proc, the file linked then checked to
    be the one written, since a /proc that is no procfs could name another's; once no link can
    be made, the process stops trying): with temporary names in the one `.scratch` directory, its lock made 8 writers
    slower than one (torch: 357 against 302 ms; creating summed 868 ms against 50, renaming 2294
    against 51), and a killed process now leaves nothing behind. A file that replaces another
    is still renamed from `.scratch` (a link cannot replace). The descriptor table is grown
    before the writers start: grown while they run, each step waited for an RCU grace period
    (`expand_files`), with every writer that opened a file waiting too (200 against 176 ms)
  - Measured (Core Ultra 9 285, XFS, empty cache): torch's 12,139 small files take 140 ms
    instead of 302 (120 on the 8 performance cores, 296 on one core); threads that write in one
    large directory wait for one another there (`filename_create`) and on XFS's buffer locks.
    120,000 files of 19 kB on average (2.4 GB, as ten torches, 20 files a directory) take
    0.56 s on the 8 performance cores, 0.65 s on all 24, 3.05 s on one
  - Memory stays under D38's 1 GB: a window of 8 pieces (64 MiB), 8 compressors, the pool's 8
    processes (measured, and asserted by test_large for the build)

- **Done (0.15.0):**
  - format v6 (codec 5, zstd pieces of 8 MiB), written by `Blob.zstd` and
    `Spill.compress_files` (natives and large files together, largest first, pieces on up to
    8 threads), read by the launcher (`decode_piece`, a window of 8 pieces on up to 8 threads;
    modules read without the GIL) and by the Python reader (pieces on up to 8 threads)
  - modules compiled, canonicalized and compressed on up to 8 worker processes
    (`compiler.py`: forked when the build runs one thread, else from a fork server), the
    whole-read files compressed on the same workers, all registered in the classifier's order
    within bounded windows (32 jobs, 64 MiB); the canonical bytecode merges equal immutable
    values (`bytecode._merge`), since `compile()`'s constant sharing depends on what was
    interned before: a build on 8 cores is the same bytes as on 1 (it was not, by one
    frozenset reference); `LAYOUT_VERSION` 6
  - the stdlib fragment compiles on the workers too; the natives' scan finds names in one
    pass (`_contained_names`)
  - the launcher writes extracted files on up to 8 threads (see the design above): unnamed
    and linked, the descriptor table grown first, nested parallel work kept on its thread
  - measured (Core Ultra 9 285): the torch app builds in 6.3 to 8.3 s instead of 22.75 (best
    compression 16.0 instead of 27.2), the stdlib fragment in 0.31 s instead of 1.40, with all
    build processes at 595 MiB PSS at most (629 at best compression); libtorch_cpu.so decodes
    in about 100 ms instead of 566; torch's first run extracts its 12,139 small files in
    140 ms instead of 302, its libraries in 125 ms instead of 390 on one core
  - `fsbpyr runtime build` shows every warning of the launcher's link and of the stdlib's
    compile (and each stdlib module kept as a plain file), where it showed only failures; a
    stack frame over the limit fails it (D38): LTO inlines across sources, and CPython's code
    into ours, and sizes frames again at the link, where only a warning said so. It did, unseen:
    module.c's `cache_sync` took 8888 bytes, `fsb_cache_sync` inlined into it with `make_root`'s
    PATH_MAX buffer, now on the heap; the link prints nothing now
  - one thread still (known): the runtime's LTO link (51 s, 2.3 GB of RSS: over D38's 1 GB, in
    `fsbpyr runtime build` only; lld's `--lto-partitions=8` fails on CPython's static
    `compress` and `lzma_free`, which clash with zlib's and liblzma's once split, and took
    4.9 GB), the closure search and the classifier's
    bookkeeping (about 1 s of torch's build, the GIL), the in-order file writes of a build,
    a frame of 8 MiB or less, a block, the collector's removals
  - tests: zstd pieces (`test_zstd_pieces.py`: boundaries, validation, the same bytes on any
    number of threads), the worker pool (`test_compiler.py`: the same results as one process,
    jobs passed over, a 300-module build alike on 4 workers and on 1), the bytecode merge,
    the launcher's writers (`test_restore.py`: helpers write, large files and xz by the
    caller, never more than 8 threads, the first failure in path order, too few descriptors,
    unnamed files linked, changed ones renamed, no link possible, a /proc that links another
    file); the contract compares C and mirror on the new order
  - checks before the release: 1272 tests pass; the fuzzer ran 10 minutes clean twice (after
    the writers and after `make_root`); 1 TiB builds compressed (now in zstd pieces: the
    test still expected one frame, and its first run stopped there) and raw, runs, streams,
    extracts and verifies with each process under 1 GB
- **Fixed (0.15.1):** measuring 0.15.0 (docs/performance.md) showed a raw build of a sparse
  1 TiB file taking 65.7 s instead of 0.14.0's 26.1 (peak 318 MiB instead of 91):
  `Extent.leaves` sent each 1 MiB leaf to a thread, holes included, each holding 1 MiB of
  zeros. Holes are found on the calling thread now (one `lseek` each) and share one buffer;
  only leaves with data are read on the threads: 14.2 s, 106 MiB
  (`test_holes_are_never_sent_to_the_threads`). Known, not done: one `lseek` per leaf of a
  hole; skipping a hole's whole run would take fewer
- **Fixed (0.15.2):** a dependency's module that a runtime module shadows no longer fails the
  build. gnureadline ships `readline.py` for Pythons without a readline module; the runtime's
  built-in `readline` is always found first, so the file is never imported, in a venv either.
  The classifier keeps it as a file, with a warning; a clash in the project's own modules
  still fails the build in assemble
  (`test_a_dependency_module_the_runtime_shadows_is_kept_as_a_file`,
  `test_a_project_module_the_runtime_shadows_stays_to_fail_the_build`)
- **Fixed (0.15.3):** a library without RUNPATH also resolves its NEEDED entries through the
  RPATHs of the objects that loaded it, as ld.so does. gnureadline 8.3.3 bundles libncurses
  and libtinfo without an RPATH of their own: libncurses finds libtinfo through the
  extension's `$ORIGIN/gnureadline.libs`, and the build asked for
  `--allow-system-lib libtinfo-3a2cb85b.so.6.1`. The closure search passes those directories
  down each chain; an object with RUNPATH does not search them but passes them on
  (`test_libraries_without_runpath_inherit_the_rpath_of_their_loaders`,
  `test_a_runpath_ends_the_inherited_search_for_its_own_object_only`). Known, not done: ld.so
  also reuses an object already loaded under the same soname
- **0.17.0:** a dependency built from its sdist on the build host (wheel tag `linux_*`) whose
  native files need more than the 2.28 baseline is built again against the D25 sysroot. posix_ipc
  publishes no wheel for Python 3.14; built on a glibc 2.34 host it bound sem_*, shm_* and mq_*
  to GLIBC_2.34 (librt moved into libc in 2.34), and tsim's executable needed glibc 2.34.
  Against the sysroot it needs GLIBC_2.7 and librt.so.1, and the executable glibc 2.28.
  - the host's compiler (gcc or clang) gets the sysroot through CFLAGS and LDFLAGS
    (`--sysroot`, Debian's multiarch include and library directories, the sysroot's GCC 8
    crtbeginS.o and libgcc), not through CC: build probes run CC as a bare command
  - uv reuses a wheel it built, whatever the flags (`--refresh-package` only revalidates the
    index): `uv cache clean` drops its builds of those packages first, and the sysroot's builds
    take their place, so the next build finds them and rebuilds nothing
  - a failed rebuild (a C++ extension: the sysroot has no libstdc++) keeps the host's build,
    with a warning, as does an offline build; uv builds before it installs, so the staged site
    is unchanged
  - tests: the flags, the staging rebuild (cache cleaned, installs repeated, the venv not
    recreated), which dependencies need it (built here and above the baseline: not index
    wheels, not builds within it), a failed and an offline rebuild. tsim's shell built twice
    against a uv cache that held the host's posix_ipc build: the first rebuilt it (glibc 2.28),
    the second found the sysroot's build
- **Fixed (0.17.1):** a module file whose stem starts with a digit is a module. mypyc puts the
  code its extensions share in a top-level extension named after a hash, which they import by
  name: charset-normalizer's `cd` and `md` import `81d243bd2c585b0f4821__mypyc`. Not an
  identifier, it was packaged as a library, so `import charset_normalizer` failed and requests
  warned that it had no character detection. An extension's stem may start with a digit,
  since CPython calls `PyInit_<stem>`, but still never contains a hyphen (`libbar-2.so` stays
  a library); a `.py` stem may do both, so `0001_initial.py` migrations are modules too. The
  payload format allows such names; an older `fsbpyr inspect` refuses an executable holding
  one, the launcher never checks names
  (`test_module_files_starting_with_a_digit_are_modules`,
  `test_module_names_starting_with_a_digit_are_allowed`)
- **0.18.0 (D40):** the CLI runs on click; its commands give `--help`, the shell completions
  (`fsbpyr completion bash|zsh|fish`) and the man page (`fsbpyr man`, `manpage.py`). Every
  on/off option of `build` has a `--no-` form (`--source/--no-source` replaces `--no-source`
  alone, `--smoke/--no-smoke` `--no-smoke`), so the command line also switches off what the
  project switches on. `--entry` and `--extra` complete from the project's `pyproject.toml`
  (`Project.extras` is new). Changed with click: commands are listed alphabetically, options can
  no longer be abbreviated (`--off`: "Did you mean '--offline'?"), a usage error prints
  `Error: ...` (still exit 2), `fsbpyr` alone prints the help (exit 2), and a project path that
  is a file is a usage error. click costs the executable 4–5 ms of startup
  - tests: `test_cli_docs.py` (the subcommands and both forms of on/off options complete;
    `--entry` and `--extra` from the current or the named project, nothing and no message on
    stderr for a missing, broken or non-UTF-8 one; bash runs the script's function, which runs
    fsbpyr from `PATH`; the man page names every command and option, sets code in bold and
    passes `groff -ww` clean; the README's option table is the `build` command's help), and in
    `test_cli.py` the `--no-` forms over the project, `-h`, and which commands collect the cache
    (as before: `build`, `inspect` and `runtime build|list` when they run, never for help or a
    usage error). zsh ran by hand through a pty (`--entry`, `--extra`, `--no-o`, subcommands);
    fish was not available here, so its script is only checked for calling fsbpyr
- **0.18.1:** the man page ends with EXAMPLES: a command line and what it does, for every
  command. They sit in `cli.py` next to the commands (`EXAMPLES`), and a test runs each one
  through the commands with their callbacks replaced, so no example can name an option or a
  command that is gone. The section is set without hyphenation and left-aligned: groff split
  `fsbpyr` in a command line and stretched the long ones
  - tests: every example is a command line fsbpyr accepts and sits in EXAMPLES; no line of the
    rendered section ends in a hyphen (two do without `.nh`)

- ~~`FSBPYR_VERIFY=1` also checks the payload digest (D11)~~: D31 checks the whole file on
  every start and re-hashes cached files in every process; `FSBPYR_VERIFY` is gone.
- **Exit:**
  - the edge-case tests pass; the fuzzer (10 minutes) and the sanitizers are clean; the
    launcher builds with `-Wvla` and the frame-size limit
  - a 1 TB executable builds, runs and extracts, with RSS under 1 GB throughout
  - GC removes what D38 says and nothing else; tests cover every rule, concurrent use included
  - security tests pass: path traversal in names, symlinked cache dirs, foreign-owned cache,
    a cache root below a directory others can write, truncated binary
- **Exit met (0.14.0, 2026-10-10):** 1228 tests pass (none skipped here), the sanitized
  launcher's included; the fuzzer ran 10 minutes clean after each of 8c, 8d and 8e; the
  launcher builds with `-Wvla` and the frame limit; 1 TiB (sparse) builds compressed and raw,
  runs, streams, extracts and verifies with each process under 1 GB (34 min 51 s for the
  three runs; not run before 0.13.0, run again before 0.14.0); GC and security tests as listed
  under 8d and 8e

### Phase 9 — Self-update, more Python versions and portability (L, after the MVP; owner decision 2026-10-18)

- **Self-update first** (S; owner decision 2026-10-18, low-hanging fruit). An explicit command,
  never part of another (plain `fsbpyr` stays help only), asks GitHub for the latest release
  and, when its `vX.Y.Z` is newer than its own version, downloads its own asset of the release
  (`fsbpyr`, or `fsbpyr-3.X` once there are variants, see below) into the directory of the
  running executable under a temporary name, sets its execute bit and runs `inspect --verify`
  on it; when that exits 0 (it exits 1 for a damaged or truncated file,
  checked with 0.19.0), the file is renamed to the executable's name: one `rename(2)` in one
  directory, atomic, and a running fsbpyr keeps its old inode. Settled with the owner
  (2026-10-18):
  - the command is `fsbpyr self update` (as in uv and rustup; `fsbpyr update` would read as
    updating the project);
  - the running fsbpyr verifies the download (`inspect --verify NEW`, in-process), so no byte
    of the new file runs before it passes;
  - it downloads `fsbpyr.xz` (half the size) and unpacks it in-process with the stdlib `lzma`,
    which every fsbpyr runtime carries: no `xz` on the host, no command of its own (checked
    2026-10-18: an fsbpyr 0.19.0 app unpacked a release-style `xz --x86 --lzma2=preset=9e`
    file of 14.0 MB in 0.18 s, 50 MB peak RSS, byte for byte);
  - the target is the running executable's real path (it may be renamed or reached through a
    symlink), with its mode kept; a directory fsbpyr cannot write is an error saying how to
    update instead;
  - only released executables update themselves: releases are always tagged, so the version
    check compares two `vX.Y.Z`; a developer build (`untagged-<commit>`, from `make build` or
    `make bootstrap`) refuses with an error that gives the `curl` command downloading the
    latest release from GitHub (README, "Getting fsbpyr");
  - the man page and the shell completions are updated too: those found where
    `make install` puts them under the executable's prefix (`<prefix>/bin/fsbpyr` gives
    `<prefix>/share/man/man1/fsbpyr.1`, `share/bash-completion/completions/fsbpyr`,
    `share/zsh/site-functions/_fsbpyr`, `share/fish/vendor_completions.d/fsbpyr.fish`) are
    written again by the new executable (`man`, `completion SHELL`), each under a temporary
    name and renamed into place after the executable; ones installed elsewhere get a hint;
  - signing changes nothing here: once releases are signed (D31), `inspect --verify` checks the
    signature as well, and self-update gains tampering detection with it.
- **More Python versions: one fsbpyr per version** (L, second; owner decisions 2026-10-18). A
  project need not run on 3.14 to use fsbpyr. Supported are the CPython versions still
  maintained from 3.12 on: 3.12–3.14 now (owner decision 2026-10-18). 3.11 (end of life
  October 2027) only if projects need it: under the rule below a project allowing 3.12, such as
  `>=3.10` or `>=3.11`, gets 3.12, so only one excluding 3.12 and later would; its port would
  add the 14 uses of 3.12's type-parameter syntax in 9 files (checked 2026-10-18), its
  multiprocessing command lines are 3.12's. Each release ships
  one fsbpyr per version, built with and carrying that version's runtime only (29 MB, as now),
  so the builder and its apps always run on the same Python: modules compile in-process, the
  canonicalizer (D16) handles the running version's marshal format, and every variant builds
  itself. Rejected: one fsbpyr carrying every runtime (about 60 MB for 3.12–3.14, estimated from
  15 MB per compressed runtime: a 20.9 MB stub and a 7.3 MB stdlib), with the builder on 3.14
  only, a compile helper per runtime and the app-side tests repeated per runtime inside one
  suite.
  - **The runtime a build uses** follows `requires-python`: with a lower bound, the lowest
    supported minor version that satisfies it (`>=3.10` gives 3.12); with only an upper bound,
    the highest below it (`<3.14` gives 3.13); a pinned minor as pinned (`==3.12` gives 3.12);
    always that minor's newest patch release; no supported version is an error naming the
    supported ones (0.19.0 already stops on a `requires-python` excluding 3.14, checked
    2026-10-18). Settled with the owner (2026-10-18): the version comes from, strongest
    first, `--python X.Y` on the command line, then `python = "X.Y"` in `[tool.fsbpyr]` (both
    an error when unsupported or outside `requires-python`), then a `.python-version` pin when
    it names a supported version inside the range (the Python the project is tested with; a pin
    outside the range is an error, an unsupported one a warning before the rule applies), then
    the rule above. The tests force a version through `--python`.
  - **Dispatch:** `fsbpyr build` picks the version; when it is not its own, it fetches the
    matching variant of its own release the way self-update fetches (the `.xz`, unpacked with
    `lzma`, `inspect --verify`), keeps it in its cache and runs it with the same arguments, so
    users install one fsbpyr and download only the variants their projects need; once cached,
    builds work offline. Settled with the owner (2026-10-18):
    - assets: `fsbpyr` and `fsbpyr.xz` stay the newest version's (3.14 now), so the README's
      `curl` and self-update keep their names; the others are `fsbpyr-3.12` and `fsbpyr-3.13`,
      each with its `.xz`. A variant updates itself to the same variant of a newer release, so
      an executable records which asset it was released as; when 3.15 becomes the newest,
      `fsbpyr` moves to 3.15 and `fsbpyr-3.14` appears;
    - a developer build (`untagged-<commit>`) that needs another version stops with an error
      saying how to build that variant locally (`make build` with a Python version, to add);
      it never fetches a released variant, which would mix released code into a development
      build.
  - **Steps:**
    1. Spike, as Phase 0 did for 3.14: PBS `pgo+lto-full` for 3.12 and 3.13 (LLVM
       bitcode objects, `PYTHON.json` link inputs and inittab, the built-in extensions, which
       static libraries are missing, `libzstd.a` at least); the launcher's `PyConfig` fields
       (all expected since 3.11).
    2. Port fsbpyr's code to 3.12. Checked 2026-10-18: 4 of its 48 source files do not parse
       on 3.12 and 3.13 (3.14's unparenthesized `except A, B:`), `os.process_cpu_count()` is
       3.13's; the importlib internals the boot code uses exist in 3.12–3.14, and 3.12 and 3.13
       share marshal format 4 (3.14: 5); ruff and mypy target 3.12. zstd compression moves
       into `_fsbpyr` from a vendored libzstd, since the stdlib's `compression.zstd`, used by
       `assemble.py` and `payload/{writer,spill,spec,reader}.py`, exists only in 3.14; one
       libzstd for every variant keeps builds reproducible (D15). Per version: the
       canonicalizer's marshal layout, the stdlib selection and startup modules, and the
       multiprocessing command lines (D22, with their security tests; 3.12's forkserver
       command line differs from 3.13's and 3.14's, checked 2026-10-18). D13 becomes Python
       ≥ 3.12.
    3. Runtimes, CI and releases: `fsbpyr runtime build --python 3.X` for each version; CI
       runs the suite once per version (the development venv through `uv run --python 3.X`,
       then that variant's end-to-end tests and its three-stage bootstrap); each release
       builds, attests and publishes every variant, and each reproduces itself.
    4. The version rule (a table test from `requires-python` to runtime) and the dispatch.
  - **Exit:** every variant passes the full suite, builds itself byte-identically in three
    stages and builds and runs the fixture apps; a project whose `requires-python` excludes
    3.14 builds through the 3.14 fsbpyr's dispatch.
- (The portable glibc baseline moved to Phase 6c, D25.)
- **More platforms** (third; owner decisions 2026-10-18): Linux on aarch64 as well as x86-64,
  each with glibc (the 2.28 baseline, D25) and with musl; macOS on Apple Silicon only (arm64;
  the payload in a Mach-O segment so `codesign` works). No Windows: dropped entirely. Per
  target, to settle in a spike first:
  - a PBS runtime that D2's static link works with (for musl: whether PBS's builds can load
    extension modules, which needs a dynamically linked launcher), and a sysroot (aarch64
    glibc 2.28; musl);
  - the launcher's Linux-only calls (`/proc/self/exe`, `O_TMPFILE`, `flock`, `getauxval`,
    `ioprio`, `pthread_tryjoin_np`) on macOS;
  - the native analysis, ELF only today (`elf.py`, `natives.py`): Mach-O on macOS; musllinux
    wheels and musl's loader rules on musl;
  - CI runners (Linux arm64, macOS arm64), and a reproduction check per target.

  With one fsbpyr per Python version a release carries 3 versions × 5 targets = 15
  executables, each with its `.xz` and attestation. Settled with the owner (2026-10-18):
  - **asset names** carry the target, in uv's and PBS's triples: for the newest Python
    `fsbpyr-x86_64-linux-gnu`, `fsbpyr-aarch64-linux-gnu`, `fsbpyr-x86_64-linux-musl`,
    `fsbpyr-aarch64-linux-musl` and `fsbpyr-aarch64-macos`; for the others the version too
    (`fsbpyr-3.12-aarch64-linux-musl`); each with its `.xz`. `fsbpyr` and `fsbpyr.xz` stay,
    copies of `fsbpyr-x86_64-linux-gnu`, so the links in use keep working;
  - **an installer, `install.sh`, modelled on uv's** (`curl -LsSf
    https://github.com/devitops-com/fsbpyr/releases/latest/download/install.sh | sh`, or a
    tag's own `releases/download/vX.Y.Z/install.sh` for that version), built and attested by
    the release workflow with each release. From uv's installer (cargo-dist's, read
    2026-10-18) and aiagent's bootstrap:
    - POSIX `sh` (dash, busybox); the release's version and every asset's SHA-256 written
      into it when it is built, so a download is checked before it is used, as uv's does;
    - it detects the target (`uname -s`, `uname -m`, musl when `ldd --version` says so, as
      uv's), checks the glibc the release records (`inspect`: `glibc >= X.Y`) against the
      host's, and stops naming the targets a release has when it has none for this host;
    - downloads with `curl` (HTTPS only, `--proto '=https' --tlsv1.2`) or else `wget`, the
      `.xz` when `xz` is there, the uncompressed asset otherwise; then runs
      `inspect --verify` on the new file before installing it;
    - `FSBPYR_VERIFY=1` also checks the GitHub attestation with `gh attestation verify` and
      stops when it fails or `gh` is missing (fail closed, as aiagent's `AIAGENT_VERIFY`);
    - installs into the first of `$FSBPYR_INSTALL_DIR`, `$XDG_BIN_HOME`,
      `$XDG_DATA_HOME/../bin` and `$HOME/.local/bin` (uv's order), under a temporary name
      and renamed into place; the man page and the completions go where `make install` puts
      them under that directory's prefix, written by the new executable;
    - when the directory is not on `PATH`, writes an `env` script next to the executable
      (`env.fish` for fish) and sources it from `.profile`, `.bashrc`, `.zshrc` and fish's
      `conf.d`, the scheme uv shares with rustup (an `env` already there, uv's included, is
      reused); `--no-modify-path` or `FSBPYR_NO_MODIFY_PATH=1` leaves the shell files alone;
      in GitHub Actions it appends the directory to `$GITHUB_PATH`;
    - options as uv's: `-h/--help`, `-q/--quiet`, `-v/--verbose`, `--no-modify-path`
      (passed as `curl ... | sh -s -- --no-modify-path`); messages as uv's (`downloading
      fsbpyr X.Y.Z <target>`, `installing to <dir>`, `everything's installed!`);
    - the README's "Getting fsbpyr" leads with it; the plain `curl` of an asset stays as
      the manual way. The installer is not tied to the new targets: it can ship for today's
      single one first.

### Phase 10 — Comparisons with other tools (M, last; D37) — **DONE 2026-10-18**

Once the work is finished, compare the same apps built and run other ways: bare
`python3.14 -I -S -c pass`, the app from its venv, PyInstaller onefile, `pex --scie eager` and
PyApp with Python and the dependencies embedded (both added by owner decision 2026-10-18: the
maintained tools closest to fsbpyr), a Rust CLI (`rg --version`) and a Go CLI (`gh --version`).
Size, build time, first run, later runs, peak memory and the
cache on disk, measured as in `docs/performance.md` (medians of interleaved runs, warm page
cache, and cold where it matters). **Exit:** the comparison is committed in
`docs/performance.md`.

- **Done (2026-10-18, 0.19.0):** `docs/performance.md`, "Other tools": `hello`, `heavy` and
  aiagent, 8 commands. fsbpyr, pex and PyApp matched the venv's output with no extra options;
  PyInstaller needed `--collect-data heavy` and `--copy-metadata aiagent`; PyApp's
  `PYAPP_EXEC_SPEC` drops the entry function's exit status. Later runs: fsbpyr 1.03–1.21× the
  venv (the integrity and cache checks), pex and PyApp at the venv, PyInstaller onefile
  4.6–18× (it unpacks 35–179 MB on every start). First runs: fsbpyr 13–153 ms, PyApp
  0.28–1.17 s, pex 1.4–3.2 s. Disk after all runs: fsbpyr 96.5 MB, pex 814 MB, PyApp 1.52 GB.
  Not measured: a cold page cache (needs root), PyApp's default mode (it
  installs the dependencies from an index on the first run).

---

## 7. Test strategy

Status (v0.19.0): markers are `slow` and `sanitized` (no `network` marker); only `clicky`
commits all three lock sources, the other fixtures `uv.lock`; C probes are in `tests/probes/`,
the fuzzer in `tests/fuzz/` (8a); CI (`.github/workflows/release.yml`) runs `make check` and
`make test-ci` (unit, integration, `test_build.py`, `test_runtime_app.py`, and `test_imports.py`
after 0.20.0) on every pull request and tag since 0.19.0 and on every push to main after 0.20.0,
the rest locally (`make test`); tests that need the kernel to refuse access skip as root, and
after 0.20.0 a skip that the `ci` list in `tests/conftest.py` does not expect fails CI.

- **Unit (pytest):**
  - format and writer/reader
  - ELF parser, against real `.so` fixtures and crafted malformed ELFs
  - marker/closure walker, against synthetic site-packages trees
  - lock-source selection and staging-key hashing (subprocess calls
    mocked at the `staging.py` boundary)
  - classifier, config merge
  - cache materialization, including concurrency through multiprocessing and hostile
    filesystem layouts
- **Integration:**
  - `runtime build` (marked `slow`; needs clang ≥ 22)
  - project wheel acquisition through uv (D26)
  - boot contract suite against the stand-in (`tests/support/standin.py`; planned as `_fsbpyr_py`)
- **E2E:**
  - fixture projects under `tests/fixtures/projects/` carry committed `uv.lock`,
    `pylock.toml` and `requirements.txt` files; staging installs are marked `network` on
    first run and use the uv cache afterwards
  - binaries are built and executed, and their output, exit codes and `FSBPYR_TRACE`
    counters are asserted
- **Coverage:** ≥ 80% overall on `src/fsbpyr`, ≥ 90% on `payload/` and `elf.py`. Boot code
  is covered in-process through the stand-in.
- **C:** E2E with an ASan/UBSan stub variant, libFuzzer for parsers, and `-Wall -Wextra -Werror`
  (built: 8a, 0.10.0; no blanket `-Werror`: `fsbpyr runtime build` shows every warning and fails
  on a frame over the limit, 0.15.0, and `tests/e2e/test_launcher_build.py` compiles with
  `-Werror=vla -Werror=frame-larger-than`).

## 8. Benchmarks (manifest §13)

Reduced by D37 (owner decision 2026-10-09): no harness, timing gate or speed targets.

- **Measurements:** `docs/performance.md` records each release that changes the launcher, the
  payload format or the boot code: how runs are measured (medians of interleaved runs, warm page
  cache), startup against bare CPython, the integrity check, the cache check, apps with heavy
  native dependencies (numpy, onnxruntime, torch) against their venv, memory, and where to look
  when a start gets slower.
- **Regression guards:** exact counts from `FSBPYR_TRACE` in the e2e tests (modules loaded per
  distribution, files extracted), the cache sync's outcomes in the contract, and one timing
  test: everything but the integrity check within 1.2× bare CPython.
- **Comparisons with other tools** (PyInstaller onefile, `pex --scie eager`, PyApp, a Rust and a
  Go CLI): Phase 10, done 2026-10-18 (`docs/performance.md`, "Other tools").

## 9. CLI and config (v1)

Status (v0.19.0): the synopsis and the table below match `cli.py` and `config.py`.

```
fsbpyr build [PROJECT_DIR] [--entry NAME | module | module:func] [-o OUT]
             [--requirements FILE] [--[no-]offline]
             [--[no-]fast-compression | --[no-]best-compression] [--extra NAME]...
             [--[no-]source] [--fs-package PACKAGE]... [--allow-system-lib SONAME]...
             [--[no-]smoke] [--[no-]hints] [--[no-]interpreter-reexec]
fsbpyr inspect BINARY [--json] [--verify] [--all-modules]   # D28, v0.3.0
fsbpyr runtime list | build [--python X.Y] [--clang PATH]
fsbpyr cache gc                                             # D38
fsbpyr completion bash | zsh | fish                         # D40, v0.18.0
fsbpyr man                                                  # D40, v0.18.0
fsbpyr --version                 # 0.1.0-6457a95b at tag v0.1.0, else untagged-6457a95b (D28)
```

`fsbpyr build -h`, the completions and `fsbpyr man` all come from the click commands in
`cli.py` (D40).

```toml
[tool.fsbpyr]
entry = "mytool"              # console script name, "pkg.mod", or "pkg.mod:func"
output = "dist/mytool"
requirements = "requirements.txt"   # pip projects without pylock.toml; ignored when uv.lock exists
extras = []
include-source = true
fast-compression = false      # D29: zstd level 3 instead of 9
best-compression = false      # D33: native files as xz -6 (not with fast-compression)
fs-packages = []
allow-system-libs = []
interpreter-reexec = false    # Phase 6b, D23
```

Environment variables honored by built apps (never `PYTHON*`):
`FSBPYR_CACHE_DIR`, `FSBPYR_TRACE`, `FSBPYR_IMPORTTIME`, `FSBPYR_SELFTEST`, `FSBPYR_GC` (8d,
0.13.0) (`FSBPYR_VERIFY` until D31).
Whoever controls the environment can make an app import-only (`FSBPYR_SELFTEST`) or print
import timings; this was accepted in the Phase 2 review, since that party could equally set
`LD_PRELOAD`.

## 10. Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| No clang/lld ≥ 22 on the build host | Cannot build runtimes | apt.llvm.org packages; podman image with LLVM 22; fallback: build a PBS non-LTO variant with PBS's own build scripts. **Status (0.18.1):** Debian 13.7+ ships clang/lld 22, which `make bootstrap` installs on trixie; elsewhere it builds in a trixie podman image (`Containerfile`); executables carry their runtime, so only a new runtime needs clang (D4); the fallback was not needed |
| Host-linked stub binds to host glibc symbols (S1: needs GLIBC_2.38) | Binaries need a recent glibc | Phase 6c (D25): link against an old glibc sysroot; report the effective glibc requirement at build time. **Done** (Phase 6c, 2026-10-07): executables need at most glibc 2.28 and ran on 13 distributions; since 0.17.0 sdists built above 2.28 are rebuilt against the sysroot |
| `marshal` output not deterministic (confirmed in S3) | Fixed-point gate fails | D16 canonicalizer; required round-trip and determinism tests. **Done** (Phase 1); stage2 == stage3 since Phase 4, checked by every `make bootstrap` (0.18.1); the same bytes on 1 or 8 cores (0.15.0) |
| Canonicalizer tied to marshal v5 / 3.14 code layout | Breaks on new Python versions | Version check at build time; Phase 9 adds layouts for other versions. The check is in (`bytecode.SUPPORTED_PYTHON`); other layouts wait for Phase 9, where each fsbpyr handles its own version's layout |
| Packages that assume real files (`__file__` + `open`, `pkg_resources`) | Runtime failures | `get_data` mapping, FS_REQUIRED materialization, compatibility matrix. **Done** (Phases 3, 6, 6b): six built-in fs-packages, on-demand extraction of paths (D27), `tests/e2e/test_compat.py` |
| Re-exec of `sys.executable` (multiprocessing, subprocess `-m`) | Child runs the app instead of Python | Multiprocessing command lines are recognized and authenticated (D22); `-m`/`-c` are opt-in and authenticated (D23, Phase 6b). **Done**: D22 in Phase 6, D23 in 0.16.0 |
| Cache directory on a `noexec` mount | `dlopen` fails | Detect `ST_NOEXEC`, clear error, `FSBPYR_CACHE_DIR`. **Done** (Phase 5): a failed `dlopen` from a `noexec` cache names the mount and `FSBPYR_CACHE_DIR` (`_fsbpyr_rt/native.py`) |
| Static PIE relocation cost | Startup | Measure PIE vs non-PIE in S1. **Done**: about 0.3 ms per start (S1); PIE by default (D17) |
| Wheels not compliant with manylinux (local builds linking host libs) | Missing libs on targets | Fail the build with a named library; `--allow-system-lib`. **Done** (Phase 5, `natives.py`) |
| Staging install needs network or a warm cache | Builds fail offline | Staging is reused per lock hash; `--offline` passthrough; clear error naming the missing package. **Status:** staging is a fresh directory per build and uv's cache is what is reused (Phase 4); `--offline` is passed to uv (`staging.py`) |
| pip projects without any lock | Builds not reproducible | Loud warning; the build-info JSON records "unlocked" plus the resolved versions; `fsbpyr inspect` shows it. **Done**: build_info's `lock` and `dists` (Phase 4, `build.py`), shown by `inspect --json` (0.3.0); the text report does not show them |
| sdist-only dependencies | Build backends needed at staging time | Delegated to uv, as in any normal install; the error output is passed through verbatim. Since 0.17.0 one built above the glibc baseline is rebuilt against the sysroot |
| Memory safety bugs in `_fsbpyr` | Security | Bounds checks, fuzzing, sanitizers (Phase 8). **Done** (8a, 0.10.0): edge-case tables, an ASan/UBSan runtime, libFuzzer (`tests/fuzz/`) |

## 11. Resolved questions (owner, 2026-10-06 and 2026-10-18)

| Question | Answer | Effect on this plan |
|----------|--------|---------------------|
| Toolchain for runtime stubs | Host clang/lld ≥ 22 (apt.llvm.org) | D2, §3.7, S1. A portable glibc baseline through a container link is deferred to Phase 9 (moved to Phase 6c by D25 and done there: a pinned glibc 2.28 sysroot, no container; since 0.18.1 `make bootstrap` takes clang/lld 22 from Debian trixie, or a trixie podman image elsewhere) |
| Python versions in the MVP | 3.14 only | D13. 3.12/3.13 runtimes are deferred to Phase 9 (2026-10-18: 3.12–3.14 there, one fsbpyr per version) |
| Project scope | Installable projects (pyproject.toml or setup.py). Loose scripts are out of scope | §3.6 step 4. Entry points come from `console_scripts` or `--entry` |
| Dependency source | Fresh install from the lock into staging | D5, §3.6 step 3, Phase 4 exit criteria |
| MVP scope (2026-10-18) | Linux x86-64 and CPython 3.14. More platforms, more Python versions and self-update come after the MVP | Phase 9 after the MVP, self-update first; Phase 10 is the MVP's last phase |
| Signed releases (2026-10-18) | Not MVP | D31's signature checked with an outside key comes after the MVP |
| `fsbpyr` with no arguments (2026-10-18) | Prints the main help and nothing else: no build, no side effects for a new user | It prints the help as `--help` does, on stdout, and exits 0 (owner decision 2026-10-18; 0.19.0 printed it on stderr with click's usage status 2). Traced with 0.19.0 and an empty `HOME`: two `lstat`s of the cache root, nothing written, no process started |
| Platforms after the MVP (2026-10-18) | Linux x86-64 and aarch64, each with glibc and musl; macOS on Apple Silicon only; no Windows | Phase 9, third |
| Multi-command executables (2026-10-18) | The app's `main` dispatches on `sys.argv[0]`; symlinks or hard links name the commands. No launcher change | Checked with 0.19.0: the launcher passes `argv` unchanged (`main.c`, `parse_argv = 0`), so a link's name reaches `sys.argv[0]`; `sys.executable` stays the real file |

Still open: nothing.
