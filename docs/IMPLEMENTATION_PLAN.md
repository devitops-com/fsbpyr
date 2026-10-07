# fsbpyr — Implementation Plan

Status: v1.3, 2026-10-07. Phases 0–2 are complete; Phase 0 findings are in `docs/spikes.md`. Requirements come from `docs/python_cli_runtime_manifest.pdf`
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

**[assumption] A1:** "single command" means `fsbpyr build [PROJECT_DIR]`, run inside the project
or pointed at it. Output goes to `dist/<console-script-name>`.

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
- **[assumption]** `UV_PROJECT_ENVIRONMENT` redirects `uv sync` to a staging venv. This is
  documented uv behaviour but has not been tested here; the Phase 4 tests cover it.

---

## 2. Key decisions

| # | Decision | Rationale | Rejected alternatives |
|---|----------|-----------|-----------------------|
| D1 | The native launcher is written in **C** (about 800 LOC) | libpython is C and needs clang anyway (D2). This gives minimal native work at startup | Rust: adds cargo for code that mostly calls C APIs |
| D2 | **CPython is statically linked into the launcher** from PBS `pgo+lto-full` (clang/lld ≥ 22, `--target=x86_64-unknown-linux-gnu`, `-flto=full`, `-Wl,--export-dynamic` so manylinux wheels resolve `Py*` symbols from the executable). Link inputs: libpython.a, the HACL objects of modules registered in `config.c`, and the static libraries listed in `PYTHON.json` (S1) | Runtime is directly loadable (manifest Fig. 1). PGO+LTO speed. PBS already handles the static dependencies and the glibc 2.17 baseline | Shared libpython extracted to the cache (extracts the runtime on first run). memfd + dlopen (copies MBs on every start). Building CPython and its dependencies ourselves (large effort) |
| D3 | A **runtime** is: the *stub* (launcher + static CPython, an ELF) + a precompiled *stdlib payload fragment* + `runtime.json`. It is built once per (Python minor, platform, launcher version) and cached | App builds become concatenation: no compiler and no network needed | Compile per app |
| D4 | **Every fsbpyr binary carries its own runtime.** When `fsbpyr build` targets the same Python minor, it reuses its own stub bytes and the already-compressed stdlib frames from its own executable (`Runtime.from_self()`) | Makes R2/R3 work without a toolchain. Fast builds | — |
| D5 | **Inputs come from a fresh staging install driven by the project's lock** (§3.6). uv projects: `uv sync --frozen --no-dev --no-editable` into a throwaway staging venv. pip projects: `--target` install from `pylock.toml`, then a pinned requirements file, then `[project.dependencies]` (unlocked, with a warning), plus the project itself installed `--no-deps`. The developer's venv is never read or modified | Reproducible from the lock. No dev dependencies. The project is installed non-editable, so its package data is correct. uv and pip do all resolving and building | Reading the developer's venv (not reproducible, editable installs, dev deps). Our own resolver |
| D6 | Imports hook in through **`sys.path_hooks` + one virtual `sys.path` entry** (as zipimport does), plus a `sys.meta_path` `DistributionFinder` for `importlib.metadata` | `pkgutil.iter_modules`, `importlib.util.find_spec` and PEP 420 namespace packages work through the stock PathFinder | Pure meta-path finder |
| D7 | **Index lookups happen in C** (built-in module `_fsbpyr`, binary search over sorted tables mapped with mmap). The index is never turned into Python dicts | Startup must not scale with the number of packaged files (M2) | Python dict built at boot (O(N) on every start) |
| D8 | **Startup-critical modules** (`encodings*`, `linecache`, the boot module) are listed in the STARTUP section. The launcher decompresses them once (checksum-verified zstd frames, one shared context, about 0.08 ms) and hands them to CPython through a `PyImport_FrozenModules` table built at run time | No filesystem access during init. No launcher rebuild when boot code changes. `marshal` never sees corrupt bytes: the Phase 2 review showed raw startup blobs crashed CPython on bit flips | Raw blobs referenced straight from the mmap (zero-copy but unverified). Freezing into the C source |
| D9 | The **native cache mirrors the site-packages layout** inside the generation directory (`<gen>/site/numpy/_core/*.so`, `<gen>/site/numpy.libs/*`) | auditwheel's `$ORIGIN`-relative RUNPATHs work unchanged, so no ELF patching is needed | Flat content-addressed files + patchelf |
| D10 | **Generation id** = SHA-256(runtime ABI ‖ platform ‖ sorted (path, sha256) of every extractable artifact), computed at build time and stored in the header | Manifest §7. Releases that change only Python code reuse the existing generation | Per-build generations |
| D11 | **Integrity:** SHA-256 for extractable artifacts, verified on extraction. zstd frame checksums for blobs kept in memory. Whole-payload SHA-256 in the trailer, checked only by `fsbpyr inspect --verify` or `FSBPYR_VERIFY=1` | Meets manifest §11 without hashing on every import | — |
| D12 | **Sources are kept** by default as separate zstd frames, read only for tracebacks and `inspect`. `--no-source` drops them | Size may grow, startup may not. Gives full tracebacks | — |
| D13 | **fsbpyr itself** requires Python ≥ 3.14. Its only runtime dependency is `packaging`. zstd comes from the stdlib `compression.zstd`, TOML from `tomllib` | Small self-hosting surface. fsbpyr is pure Python, so self-hosting does not depend on native-extension support | `zstandard` package (a native dependency) |
| D14 | **Interpreter config in built apps:** isolated (`PYTHON*` env vars ignored), `site_import=0`, `safe_path=1`, UTF-8 mode, `parse_argv=0`, `executable` = realpath of the binary. Debug knobs only through `FSBPYR_*` env vars | Deterministic startup. The app's argv stays untouched | — |
| D15 | **Deterministic builds:** sorted entries, fixed blob order, no timestamps, single-threaded zstd with fixed parameters, canonical bytecode (D16); output is byte-identical for a fixed libzstd version, which `build_info` records. Self-hosting fixed point: stage2 == stage3, byte for byte | The classic compiler-bootstrap check for R2/R3 | — |
| D16 | **Canonical bytecode:** every `marshal.dumps` result is rewritten by `fsbpyr/bytecode.py`. Strings are merged by value (interned if any occurrence was), FLAG_REF is set only for nodes that occur ≥ 2 times or are interned strings, refs are renumbered in pre-order, and set elements are sorted canonically | Raw marshal output depends on refcounts and on process-global interning (S3: 561/561 files differ in batch mode, 4 in a "noisy" build process). Canonical output: 0 differences, round-trip equality, same size and load time | Compiling in a clean worker process (still state-dependent). Accepting non-determinism |
| D18 | **Launcher C sources ship as package data** (`src/fsbpyr/launcher/`, with vendored zstd 1.5.7 headers) and are hashed into the runtime id | A self-hosted fsbpyr binary can build runtimes for new Python versions from its own resources; any launcher change yields a new runtime id automatically | Top-level `launcher/` outside the package |
| D17 | **Non-PIE stub** — *trade-off flagged by the Phase 2 security review; owner decision pending* | 0.3 ms faster startup than PIE (4.85 vs 5.16 ms, S1); 59 k relative relocations avoided. ASLR still applies to heap, stack and shared libraries, but the launcher and CPython image sit at fixed addresses. Mitigations in place: RELRO, BIND_NOW, NX stack, `-fstack-protector-strong`, `_FORTIFY_SOURCE=2` | PIE |
| D19 | **Code blobs are always checksummed zstd frames** (`Blob.code`); every reader requires the frame's content-checksum flag | `marshal.loads` is not safe against corrupt input. Fuzzing 900 bit flips: 67 SIGSEGV + 4 SIGABRT + a hang before, 0 signals after (clean exit 70 or `PayloadError`) | Raw storage of small code objects |
| D20 | **The cache is private:** cache directories must be owned by the user and are forced to 0700; `runtime.json` records SHA-256 of the stub and the stdlib fragment, verified on load; a concurrently completed runtime wins | Cached stubs are copied into every executable built from them | Trusting any directory named by `FSBPYR_CACHE_DIR` / `XDG_CACHE_HOME` |

---

## 3. Architecture

### 3.1 Components

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

1. `open("/proc/self/exe")` and `pread` the trailer. Validate magic, version and bounds.
   `mmap` the payload with `PROT_READ, MAP_PRIVATE`.
2. Validate every header and table offset/count against the mapping size. Compare the bytecode
   magic in the header with the value the stub was compiled with (passed as `-D` at stub build
   time). A mismatch is a hard error with a clear message.
3. Build a `struct _frozen[]` from the STARTUP modules, pointing into the mmap. Set
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
   - dispatches multiprocessing helper argv patterns (Phase 6)
   - runs the entry point: `module:attr` → `sys.exit(attr())`; `module` →
     `runpy._run_module_as_main`
8. CPython's own `Py_RunMain` handles exit codes, `SystemExit`, the SIGINT re-raise, stdio
   flushing and atexit.

**Startup budget (dev machine):** steps 1–5 take ≤ 0.2 ms. `hello --version` is ≤ 5.6 ms p50,
which is ≤ 1.15× bare `python3.14 -I -S -c pass`.

### 3.4 Pure-Python import path

- `sys.path = ["<exe>"]`. `sys.path_hooks[0]` accepts `<exe>` and `<exe>/<pkg path>`, plus
  cache-dir aliases for FS_REQUIRED packages, and returns `FsbpyrPathFinder(prefix)`.
- `find_spec(fullname)` does one C lookup in `modules`:
  - It returns a `ModuleSpec` with `FsbpyrLoader` and `origin="<exe>/pkg/mod.py"`. Packages
    also get `submodule_search_locations=["<exe>/pkg"]`.
  - For NAMESPACE it returns a spec with `loader=None` plus the portion path, and the stock
    PathFinder assembles the namespace.
- `FsbpyrLoader` implements `InspectLoader` and `ResourceLoader`:
  - `get_code` → `marshal.loads(_fsbpyr.read(code_blob))`
  - `get_source` reads the lazy source frame
  - `get_data(path)` maps virtual paths back to `files`
  - `is_package`, `get_filename`
  - `get_resource_reader` → a `Traversable` over `files` (`iterdir` is a prefix scan)
- The path finder implements `iter_modules(prefix)`, so `pkgutil.iter_modules` and
  `walk_packages` work (plugin discovery).
- `co_filename` is the site-relative path (`pkg/mod.py`). Tracebacks get source through
  `__loader__.get_source` (linecache lazycache).
- `importlib.metadata`: `FsbpyrDistributionFinder.find_distributions(context)` binary-searches
  `dists` for name lookups. It enumerates every dist only when the caller asks for all of them
  (for example `entry_points()`).
- Startup-path boot code imports only built-in and frozen modules (`sys`, `_imp`, `marshal`,
  `_fsbpyr`, `_frozen_importlib_external`). Support for resources and metadata is imported
  lazily on first use.

### 3.5 Native extensions and the cache

```
import native_mod → modules lookup (EXTENSION) → closure (bundled libs only) → cache check
   ├─ hit  ─────────────────────────────────────────────────────────────┐
   └─ miss → zstd stream → temp file → size+sha256 → fsync → rename ────┴→ ExtensionFileLoader (dlopen)
```

- **Build time.** `fsbpyr/elf.py` is a pure-Python ELF64 parser (`PT_DYNAMIC` → `DT_NEEDED`,
  `DT_RPATH`, `DT_RUNPATH`, `DT_SONAME`). Each NEEDED entry resolves in one of three ways:
  - (a) Inside the payload through RPATH/RUNPATH with `$ORIGIN`: **bundled**.
  - (b) In the system allowlist (auditwheel's manylinux policy for the target glibc): **system**.
  - (c) Otherwise the build fails and names the library. `--allow-system-lib NAME` overrides
    this.

  Extensions that `NEEDED libpython3.X.so` are rejected with a hint. The closure is stored per
  NATIVE_EXT.
- **Runtime.** The extension's `find_spec` uses `FsbpyrExtensionLoader`. It materializes the
  closure, then delegates to `importlib.machinery.ExtensionFileLoader(name, <gen>/site/<path>)`,
  which uses the normal `dlopen`.
- **Cache root:** `$FSBPYR_CACHE_DIR`, or `${XDG_CACHE_HOME:-$HOME/.cache}/<app>/runtime/<generation_id>/`.
- **Materialization steps:**
  1. Open the generation directory with `O_DIRECTORY|O_NOFOLLOW` and create subdirectories
     through `dir_fd` operations (mode 0700). Refuse symlinks. The root must be owned by the
     effective uid and must not be group- or other-writable.
  2. If the target exists as a regular file owned by the effective uid and its size matches,
     it is a cache hit.
  3. Otherwise create a temp file `.<name>.<pid>.<rand>` with `O_CREAT|O_EXCL|O_NOFOLLOW`,
     mode 0600. Stream-decompress into it (`_fsbpyr.stream(blob)`) while hashing. Check size
     and SHA-256, `fchmod 0500`, `fsync`, then `rename` into place. Racing writers produce
     identical content, so the last rename wins harmlessly and no lock is needed.
  4. Self-heal: if `dlopen` fails, re-hash the cached file. On a mismatch, unlink it,
     re-extract once and retry.
- **Garbage collection (Phase 8):** `.last-used` is touched at most once a day. Generations
  unused for more than 30 days are removed opportunistically under a non-blocking `flock`.
- **FS_REQUIRED packages** (from config or built-in rules): on first import, the package's
  non-code files are materialized into `<gen>/site/<pkg>/`. The package's `__file__` and
  `__path__` point there, while code is still loaded from memory.

### 3.6 Build pipeline (`fsbpyr build`)

1. **Config.** CLI flags override `[tool.fsbpyr]` in `pyproject.toml`, which overrides
   defaults.
2. **Runtime.** The target is Python 3.14 (MVP). Check that `requires-python` admits it, and warn
   if `.python-version` disagrees. Select the runtime: self if the minor version matches,
   otherwise the cache, otherwise fail with an error that suggests
   `fsbpyr runtime build --python X.Y`.
3. **Staging install.** Install into `build/fsbpyr/staging/<key>/`. `<key>` hashes the lock
   file, the profile and the extras, so an unchanged lock reuses the staging directory. The
   lock source is chosen in this order:

   | Lock source | Command |
   |-------------|---------|
   | `uv.lock` | `UV_PROJECT_ENVIRONMENT=<staging> uv sync --frozen --no-dev --no-editable --python 3.14 [--extra X…]`. Honours `[tool.uv.sources]`, indexes and workspaces. The project is installed non-editable |
   | `pylock.toml` (PEP 751) | `uv pip install --target <staging> --python 3.14 -r pylock.toml`, then the project with `--no-deps` |
   | pinned requirements (`--requirements FILE`, default `requirements.txt`) | `uv pip install` / `pip install --target <staging> -r FILE` (`--require-hashes` when hashes are present), then the project with `--no-deps` |
   | none | install the project with dependencies resolved fresh from `[project.dependencies]`. Prints the warning **"unlocked build — not reproducible"** |

   The installer is uv when it is on PATH. Otherwise it is pip run by a Python 3.14
   interpreter: `--interpreter`, then `$VIRTUAL_ENV/bin/python`, then `.venv/bin/python`, then
   `python3.14`. The venv only supplies pip; its packages are never read. `--offline` is passed
   through. Cross-platform flags (`--python-platform` / `--platform`) are Phase 9.
4. **Entry point.** Read `[console_scripts]` from the project's installed `entry_points.txt`.
   If there is one, use it. If there are several, `--entry` or config is required. `module`
   and `module:func` are also accepted.
5. **Collect and validate.** Index every `*.dist-info` in the staging site directory. Walk the
   project's Requires-Dist (plus extras) with the runtime's marker environment as a consistency
   check. A missing dist is an error. Installed dists that are not reachable produce a warning
   and are still packaged, because the lock decided they belong. `.pth` files produce warnings.
6. **Classify** each dist's RECORD into files. Skip paths outside site-packages,
   `__pycache__/` and `*.pyc`. Files not listed in any RECORD (venv scaffolding such as
   `_virtualenv.py`) are not packaged.
7. **Compile** in-process when the runtime is self; otherwise use the runtime's compile helper
   (Phase 9). Every result is canonicalized (D16). A `SyntaxError` keeps the file as a resource
   and prints a warning.
8. **Natives.** Run the ELF analysis, compute closures, check the allowlist and compute the
   generation id.
9. **Compress** according to the profile. Stdlib frames from the runtime are reused verbatim.
10. **Assemble** stub ‖ pad ‖ payload ‖ trailer into a temp file, `chmod 0755`, and atomically
    rename it to `dist/<name>`.
11. **Smoke test.** Run `<out>` with `FSBPYR_SELFTEST=1`. Boot checks the tables and imports
    the entry module without calling it. `--no-smoke` skips this.

### 3.7 Runtime build (`fsbpyr runtime build --python 3.14`)

1. Download the pinned PBS full archive (URL and SHA-256 pinned in `fsbpyr/runtime/pins.py`),
   verify it, and extract it into `~/.cache/fsbpyr/downloads/`.
2. Parse `PYTHON.json` and require a clang/lld whose major version is at least the bitcode
   version. Search `FSBPYR_CLANG`, then `clang-NN`, then `clang`.
3. Compile `launcher/*.c` with `-flto -O2`. Link:
   - libpython
   - selected built-in extension objects and a generated inittab
   - the static libraries listed in `PYTHON.json`, excluding `_tkinter`, tcl/tk and X11
   - `-Wl,--export-dynamic`
   - `-lpthread -ldl -lutil -lm -lrt`

   Spike S1 measures PIE vs non-PIE startup.
4. **Stdlib.** Take `lib/python3.14/` minus `test/`, `idlelib/`, `tkinter/`, `turtledemo/`,
   `ensurepip/`, `__pycache__/`, `site-packages/` and `config-*/`. Compile it with the PBS
   interpreter from the archive. Determine the STARTUP set by running that interpreter with
   `-X importtime -I -S -X utf8 -c pass`, plus the boot module.
5. **Output** goes to `~/.cache/fsbpyr/runtimes/cp314-x86_64-linux-gnu-<launcher_sha8>/` as
   `{stub, stdlib.fsbp, runtime.json}`. `runtime.json` records:
   - Python version and bytecode magic
   - ABI tag and extension suffixes
   - the marker environment
   - compatible wheel platform tags
   - the glibc baseline

---

## 4. Repository layout

```
pyproject.toml              name=fsbpyr, requires-python>=3.14, deps=[packaging],
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
  cli.py                    argparse: build | inspect | runtime
  config.py                 [tool.fsbpyr] + CLI merge (tomllib)
  project.py                project discovery, pyproject/requires-python, lock-source selection
  staging.py                fresh install into staging (uv sync / uv pip / pip --target)
  closure.py                staging dist index, Requires-Dist consistency walk, markers (packaging)
  classify.py               RECORD → typed entries
  compile.py                bytecode compile (in-process / compile helper)
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
    _fsbpyr_boot.py         STARTUP: path hook, loader, entry dispatch
    _fsbpyr_rt/             lazily loaded: resources, metadata, cache, compat
    _fsbpyr_py.py           pure-Python _fsbpyr for unit tests
  inspect.py
tests/
  unit/ integration/ e2e/
  fixtures/projects/        hello, clicky, heavy, resources, nspkg, mp
benchmarks/                 harness + baselines JSON
docs/
  python_cli_runtime_manifest.pdf
  IMPLEMENTATION_PLAN.md    (this file)
  format.md                 (Phase 1)
  spikes.md                 (Phase 0 findings)
```

---

## 5. Bootstrap and self-hosting procedure

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
  `uv sync --frozen --no-dev --no-editable`. From stage 1 on, the only external tool is uv,
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

### Phase 3 — Import system (M)

- Path hook, loader, namespace packages, `iter_modules`, resources `Traversable`,
  `get_data`, `importlib.metadata` finder, and source-backed tracebacks.
- Contract tests run against both `_fsbpyr_py` (in-process, with coverage) and the C module
  (inside built binaries).
- **Exit:**
  - the contract suite is green on both implementations
  - boot imports nothing outside the built-in and frozen set, asserted through
    `sys.modules` in a self-test

### Phase 4 — `fsbpyr build` for pure-Python projects, then self-hosting (L)

- `project.py`, `staging.py`, `closure.py`, `classify.py`, `compile.py`, `config.py`,
  `cli.py`, `Runtime.from_self()`.
- **Exit:**
  - fixtures `hello`, `clicky` (click + rich) and `resources` build and run from each lock
    source: `uv.lock`, `pylock.toml`, pinned `requirements.txt` with hashes, and unlocked
    (the warning is asserted)
  - building from pip works both with and without uv on PATH
  - the developer's `.venv` is untouched (mtime and file tree snapshot are asserted)
  - `dist/fsbpyr` is produced from the stage 0 venv install (stage1)
  - **stage2 == stage3**
  - after one warm-up run, the fsbpyr binary builds the fixtures with `--offline` and no clang
    on PATH

### Phase 5 — Native extensions and the cache (L)

- `elf.py`, `natives.py`, `FsbpyrExtensionLoader`, materialization, self-heal, generation id.
- **Exit** (manifest §14), using fixture `heavy` (numpy, orjson, cryptography, pydantic):
  - `heavy --help` shows **0 bytes** decompressed from those dists and **0 extractions**
    (counters from `FSBPYR_TRACE`)
  - the first `heavy np` extracts only numpy's closure; the second run extracts nothing
  - 32 concurrent first runs all succeed
  - a corrupted cached `.so` gets repaired
  - a release that changes only Python code reuses the generation; a native change creates a
    new one

### Phase 6 — Compatibility layer (M)

- FS_REQUIRED packages (config + built-in rules).
- Multiprocessing re-exec patterns: spawn `--multiprocessing-fork`, forkserver and
  resource_tracker `-c` commands. Python 3.14 defaults to forkserver on Linux.
- Decide on `sys.frozen`.
- CA bundle fallback if PBS OpenSSL default paths miss the system store **[assumption — to test]**.
- `.pth` warnings.
- **Exit:** a compatibility matrix is green: click, typer, rich, httpx, requests + certifi,
  pydantic, jinja2, pyyaml, numpy, cryptography, orjson, zoneinfo/tzdata, sqlalchemy, and
  multiprocessing fixture `mp`.

### Phase 7 — Compression profiles and benchmarks (M)

- `fast` / `balanced` / `compact` (manifest §10). Grouped frames for tiny modules if
  measurement shows a gain. Optional zstd dictionary, kept only if measured as a gain.
- Benchmark harness (§8) and CI regression gate.
- Evaluate an opt-in "fast exit" (flush stdio, run atexit, then `_exit`). S2 measured
  interpreter finalization at about 0.83 ms of a 4.8 ms run.
- **Exit:** a report is committed in `benchmarks/README.md` and the targets in §8 are met.

### Phase 8 — Hardening (M)

- Bounds checks across `_fsbpyr`, and declared-size checks before allocation.
- A libFuzzer harness on trailer/header/table parsing, run clean for 1 h.
- An ASan/UBSan stub variant for the E2E suite.
- Cache ownership and permission checks, `noexec` mount detection (`statvfs ST_NOEXEC`) with a
  clear message, generation GC.
- **Exit:**
  - the fuzzer and sanitizers are clean
  - security tests pass: path traversal in names, symlinked cache dirs, foreign-owned cache,
    truncated binary

### Phase 9 — More Python versions and portability (L, later)

- Runtimes for 3.12/3.13 (vendored zstd decompressor where PBS has no `libzstd.a`) and a
  per-runtime **compile-helper binary** (stub + stdlib + compile entry), so builds never depend
  on the venv interpreter.
- Containerized stub builds (podman, old glibc sysroot) for a portable glibc baseline. Report
  the effective glibc requirement of each app as max(stub, wheel tags).
- Then Linux aarch64, macOS (payload in a Mach-O segment so codesign works) and Windows
  (PE resource or overlay, explicit DLL search directories).

---

## 7. Test strategy

- **Unit (pytest):**
  - format and writer/reader
  - ELF parser, against real `.so` fixtures and crafted malformed ELFs
  - marker/closure walker, against synthetic site-packages trees
  - lock-source selection, installer selection and staging-key hashing (subprocess calls
    mocked at the `staging.py` boundary)
  - classifier, config merge
  - cache materialization, including concurrency through multiprocessing and hostile
    filesystem layouts
- **Integration:**
  - `runtime build` (marked `slow`; needs clang ≥ 22)
  - project wheel acquisition through uv and through pip
  - boot contract suite against `_fsbpyr_py`
- **E2E:**
  - fixture projects under `tests/fixtures/projects/` carry committed `uv.lock`,
    `pylock.toml` and `requirements.txt` files; staging installs are marked `network` on
    first run and use the uv cache afterwards
  - binaries are built and executed, and their output, exit codes and `FSBPYR_TRACE`
    counters are asserted
- **Coverage:** ≥ 80% overall on `src/fsbpyr`, ≥ 90% on `payload/` and `elf.py`. Boot code
  is covered in-process through `_fsbpyr_py`.
- **C:** E2E with an ASan/UBSan stub variant, libFuzzer for parsers, and `-Wall -Wextra -Werror`.

## 8. Benchmarks (manifest §13)

- **Harness:** `benchmarks/run.py` launches through `posix_spawn` and measures wall time,
  user+sys CPU, max RSS and minor/major faults (`getrusage(RUSAGE_CHILDREN)`), plus the
  `FSBPYR_TRACE` counters (bytes read and decompressed, frames, natives loaded). Cold-cache runs
  use `posix_fadvise(DONTNEED)` on the binary and the cache files, which needs no root.
- **Comparisons:**
  - `python3.14 -I -S -c pass`
  - the same app run from its venv
  - PyInstaller onefile
  - a Rust CLI (`rg --version`) and a Go CLI
- **Targets on the dev machine, p50 warm:**

  | Scenario | Target |
  |----------|--------|
  | `hello --version` | ≤ 5.6 ms |
  | `heavy --help` vs. the same app without the optional heavy deps | Δ ≤ 0.3 ms CPU, 0 bytes decompressed from the unused dists |
  | first native import | dominated by decompress + write; reported, no target |
  | subsequent native import | within 10% of the same import from a venv |

- **CI gate:** fail when p50 regresses by more than 10% against the committed baseline JSON.

## 9. CLI and config (v1)

```
fsbpyr build [PROJECT_DIR] [--entry NAME | module | module:func] [-o OUT]
             [--requirements FILE] [--interpreter PYTHON] [--offline]
             [--profile fast|balanced|compact] [--extra NAME]... [--no-source]
             [--fs-package NAME]... [--allow-system-lib SONAME]... [--no-smoke]
fsbpyr inspect BINARY [--json] [--verify]
fsbpyr runtime list | build --python X.Y | path
fsbpyr --version
```

```toml
[tool.fsbpyr]
entry = "mytool"              # console script name, "pkg.mod", or "pkg.mod:func"
output = "dist/mytool"
requirements = "requirements.txt"   # pip projects without pylock.toml; ignored when uv.lock exists
profile = "balanced"
extras = []
include-source = true
fs-packages = []
allow-system-libs = []
```

Environment variables honored by built apps (never `PYTHON*`):
`FSBPYR_CACHE_DIR`, `FSBPYR_TRACE`, `FSBPYR_IMPORTTIME`, `FSBPYR_VERIFY`, `FSBPYR_SELFTEST`.
Whoever controls the environment can make an app import-only (`FSBPYR_SELFTEST`) or print
import timings; this was accepted in the Phase 2 review, since that party could equally set
`LD_PRELOAD`.

## 10. Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| No clang/lld ≥ 22 on the build host | Cannot build runtimes | apt.llvm.org packages; podman image with LLVM 22; fallback: build a PBS non-LTO variant with PBS's own build scripts |
| Host-linked stub binds to host glibc symbols (S1: needs GLIBC_2.38) | Binaries need a recent glibc | Phase 9 container link against an old sysroot; report the effective glibc requirement at build time |
| `marshal` output not deterministic (confirmed in S3) | Fixed-point gate fails | D16 canonicalizer; required round-trip and determinism tests |
| Canonicalizer tied to marshal v5 / 3.14 code layout | Breaks on new Python versions | Version check at build time; Phase 9 adds layouts for other versions |
| Packages that assume real files (`__file__` + `open`, `pkg_resources`) | Runtime failures | `get_data` mapping, FS_REQUIRED materialization, compatibility matrix |
| Re-exec of `sys.executable` (multiprocessing, subprocess `-m`) | Child runs the app instead of Python | Narrow argv dispatch for multiprocessing. General `-m` is a documented non-goal for v1 |
| Cache directory on a `noexec` mount | `dlopen` fails | Detect `ST_NOEXEC`, clear error, `FSBPYR_CACHE_DIR` |
| Static PIE relocation cost | Startup | Measure PIE vs non-PIE in S1 |
| Wheels not compliant with manylinux (local builds linking host libs) | Missing libs on targets | Fail the build with a named library; `--allow-system-lib` |
| Staging install needs network or a warm cache | Builds fail offline | Staging is reused per lock hash; `--offline` passthrough; clear error naming the missing package |
| pip projects without any lock | Builds not reproducible | Loud warning; the build-info JSON records "unlocked" plus the resolved versions; `fsbpyr inspect` shows it |
| sdist-only dependencies | Build backends needed at staging time | Delegated to uv/pip, as in any normal install; the error output is passed through verbatim |
| Memory safety bugs in `_fsbpyr` | Security | Bounds checks, fuzzing, sanitizers (Phase 8) |

## 11. Resolved questions (owner, 2026-10-06)

| Question | Answer | Effect on this plan |
|----------|--------|---------------------|
| Toolchain for runtime stubs | Host clang/lld ≥ 22 (apt.llvm.org) | D2, §3.7, S1. A portable glibc baseline through a container link is deferred to Phase 9 |
| Python versions in the MVP | 3.14 only | D13. 3.12/3.13 runtimes are deferred to Phase 9 |
| Project scope | Installable projects (pyproject.toml or setup.py). Loose scripts are out of scope | §3.6 step 4. Entry points come from `console_scripts` or `--entry` |
| Dependency source | Fresh install from the lock into staging | D5, §3.6 step 3, Phase 4 exit criteria |

Still open (no decision needed before Phase 0):

- Whether `fsbpyr` with no arguments inside a project directory should mean `fsbpyr build`
  (A1 currently assumes an explicit `build` subcommand).
