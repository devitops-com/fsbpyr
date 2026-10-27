# Developer overview

This page is the entry point to the Developer Guide. It describes what fsbpyr consists of, the
goals its design serves and how each one is met, the limits it keeps to, and every use case that
the test corpus and the end-to-end tests establish. The User Guide starts at [What fsbpyr is](User-Overview.md).

## The project in brief

fsbpyr is a Python program (`src/fsbpyr`, about 17,400 lines in 77 files) plus a C launcher
(`src/fsbpyr/launcher`, 19 `.c`/`.h` files, about 4,300 lines). Its only runtime dependencies are
click, packaging and, before Python 3.14, `backports.zstd`. It runs on CPython 3.12 to 3.15, and
it ships as an executable built by itself.

Three programs are involved in every app:

| Part | Language | Runs | Does |
|---|---|---|---|
| **fsbpyr** | Python | on the build machine | finds out what it was given (a project, scripts, a requirements file), stages it with uv, decides what the app needs, compiles it to bytecode and writes the executable and its command links |
| **launcher** | C, linked statically with CPython | first, in every executable | checks the file's integrity, finds the payload, sets up CPython and hands over to the boot code |
| **boot** (`_fsbpyr_boot`, `_fsbpyr_rt`) | Python | inside every executable | picks the command, imports modules from the payload, serves resources, extracts native code to the cache, handles helper processes and service policies, collects the cache |

The launcher and the compiled standard library together are a **runtime**: one per Python version
and target, built from a pinned python-build-standalone (PBS) CPython by `fsbpyr runtime build`,
cached, and embedded in every released fsbpyr executable. An app's executable is that runtime's
launcher stub followed by a **payload**: an indexed, zstd-compressed archive of the standard
library, the boot modules and the app with its locked dependencies, and a trailer with the
whole-file digest.

![fsbpyr from project to running app: inputs, the build on the build machine, the executable's layout, and the start on the target machine](images/arch-overview.svg)

Where to read on:

| Topic | Page |
|---|---|
| Components and how they depend on each other, with import graphs | [Architecture](Dev-Architecture.md) |
| Build-side Python modules (`cli`, `build`, analysis, assembly, inspection) | [Build modules](Dev-Build-Modules.md) |
| The payload library (`payload/`) | [Payload modules](Dev-Payload-Modules.md) |
| Runtimes: PBS, toolchain, stub, stdlib, cache (`runtime/`) | [Runtime modules](Dev-Runtime-Modules.md) |
| Code that runs inside the executable (`boot/`) | [Boot modules](Dev-Boot-Modules.md) |
| The C launcher | [Launcher](Dev-Launcher.md) |
| The byte layout of an executable | [Executable format](Dev-Executable-Format.md) |
| How an app differs from a venv, what is tested, every corpus app | [Compatibility](Dev-Compatibility.md) |
| Startup and build measurements | [Performance](Dev-Performance.md) |
| Threat model and defences | [Security](Dev-Security.md) |
| Tests and test logic | [Testing](Dev-Testing.md) |
| Plans, CI, release process, other documents | [Repository](Dev-Repository.md) |

## Goals

The project's requirements are listed in the implementation plan,
[§0 Requirements](../docs/IMPLEMENTATION_PLAN.md#0-requirements).
How the code meets each one in 0.27.0:

| Goal | How it is met | Where |
|---|---|---|
| **One command.** A project maintained with uv or pip becomes one executable with `fsbpyr build` | uv installs every project, pip projects too, from `uv.lock`, `pylock.toml` or a requirements file; Poetry, PDM and Pipenv locks are converted to requirements for uv; a released fsbpyr carries its runtime, so no compiler is needed | `build.py`, `staging.py`, `foreign.py`, `pipenv.py` ([Build modules](Dev-Build-Modules.md)) |
| **No questions.** What the build is given decides what it builds, without a choice to make | several console scripts become one executable with a command table and a link per command; a project with no script gets an inferred entry; a project without a build system, a directory of scripts and a single script are packaged from their Python files by a wheel fsbpyr writes itself; a directory of only a requirements file builds the tools it names; dependencies nothing declares come from the venv's metadata | `entry.py`, `links.py`, `loose.py`, `legacy.py`, `tools.py`, `venvdeps.py`, `importcheck.py`; `_fsbpyr_rt/commands.py` |
| **Self-hosting.** fsbpyr builds its own executable | `make bootstrap` runs fsbpyr from source to build stage 1, which builds stage 2, which builds stage 3; stages 2 and 3 must be byte-identical | `tools/bootstrap.sh` ([Repository](Dev-Repository.md)) |
| **One file.** Only the loader, glibc and explicitly allowed system libraries stay outside | every distribution's modules, data and native files go into the payload; native files that need other libraries stop the build unless `allow-system-libs` names them | `natives.py`, `classify.py` |
| **Startup scales with what runs.** Startup work follows the import path, not the size of the dependencies | an index of modules and files is searched by name, and only what is imported is decompressed | [Executable format](Dev-Executable-Format.md), [Boot modules](Dev-Boot-Modules.md) |
| **Independently addressable compression.** No whole-file compression; what is never asked for is never decompressed | zstd frames per blob, with small blobs packed into shared blocks; xz for native files only with `--best-compression` | `payload/writer.py`, `payload/spec.py` |
| **Pure Python runs from memory** | a path hook and loader serve modules from the payload; nothing is extracted for them | `boot/_fsbpyr_boot.py` |
| **Native code through an immutable, self-healing cache**, loaded by the system loader | extension modules and shared libraries are written into the app's cache under `runtime/<generation>/site/`, where the generation id comes from the payload, so two builds never share files; a file appears only when complete (linked or renamed into place), each process hashes it once before first use, and a changed file is restored from the payload | `launcher/cache.c`, `boot/_fsbpyr_rt/native.py` |
| **Package data from memory; a file only when needed** | `importlib.resources` reads from the payload; a resource asked for as a path, or a package in `fs-packages`, is extracted on demand | `boot/_fsbpyr_rt/resources.py` |
| **Security and integrity** | whole-file SHA-256 digest checked before anything runs; strict ownership and mode rules for the cache; refusal of set-uid starts; an instance limit | [Security](Dev-Security.md) |
| **Measured** | startup, cold start, integrity check, build time and size are measured per release | [Performance](Dev-Performance.md) |

Two further properties are kept by the test suite: **reproducible builds** (the same sources,
lock, fsbpyr and uv give the same bytes) and **the same behaviour as in a venv** for every corpus
app's commands.

## Scope and limits (0.27.0)

- **Targets:** Linux x86-64 and aarch64 with glibc 2.34 or newer, or glibc 2.28 (EL8) for an
  app built with `--glibc 2.28`, whose launcher is linked against Rocky Linux 8.10's sysroot.
  Each fsbpyr builds for the platform it runs on; there are no cross builds. musl, macOS and
  aarch64 kernels with 16K pages are not supported.
- **Python:** CPython 3.12.15, 3.13.17, 3.14.9 and 3.15.1, one fsbpyr executable per version;
  3.14 is the default. Free-threaded CPython is refused.
- **Apps:** anything with an entry point that uv can install; a project without a build system,
  a directory of scripts and a single script, packaged from their Python files; the console
  scripts of the tools a requirements file names. Some packages need [settings](User-Settings.md).
  A file whose shebang names `python2` is not taken for a script (`_SHEBANG_PROGRAM` in
  [`loose.py`](../src/fsbpyr/loose.py)).

## Use cases

Every use case below is one the tests establish. Two kinds of test do:

- **The corpus** ([`tests/corpus`](../tests/corpus)):
  65 packaged projects, one console script each. Each app's commands give the same exit code,
  stdout and stderr as in a venv, on every Python and target where its packages have wheels.
  The corpus is organised by field ([Compatibility](Dev-Compatibility.md#tested-on-real-applications)
  lists every app with the risk it covers and its settings); below, the same apps are also
  grouped by what an app *does*, which is what decides whether it needs a setting.
- **The end-to-end tests of the other ways a program is kept** (scripts, several commands, other
  locks, services): each builds a fixture or a directory it writes, and most compare the
  executable's output with what `python3 script.py` or uv prints for the same code. They are
  listed under [By how the code is kept](#by-how-the-code-is-kept). The research behind them is
  [`docs/use-case-research.md`](../docs/use-case-research.md),
  written before they were built; the code and these tests are what 0.27.0 does.

### By field

| Field | Apps | Need no settings |
|---|---|---|
| AI research and machine learning | 19 | 8 |
| Applied mathematics and data | 14 | 7 |
| Physics and chemistry | 9 | 3 |
| Services | 17 | 12 |
| Developer tools | 6 | 5 |
| **Total** | **65** | **35** |

### By behaviour

| An app that... | Corpus apps | What fsbpyr does / needs |
|---|---|---|
| imports pure-Python packages and compiled extensions | all; `pandas-csv`, `scipy`, `fitting`, `qutip` show nothing else | modules from memory, extensions through the cache; no settings |
| loads extensions with bundled shared libraries | `psycopg` (libpq, libssl), `messaging` (libzmq), `lxml` (libxml2, libxslt), `pillow`, `faiss` (OpenMP), `dataframes` (Rust and C++ extensions) | bundled libraries are extracted with the extension that needs them; no settings |
| uses an extension built with mypyc | `black`; `charset_normalizer` in the `compat` fixture | a mypyc package's `__path__` is its cache directory; data files next to it need `fs-packages` (`black`) |
| reads data files next to its `__file__` | `flask-jinja`, `django`, `sklearn`, `astropy`, `pint`, `statsmodels`, `optuna`, `materials`, `pyscf`, `geo`, `nlp`, `vision`, `transformers`, `lightning` | `fs-packages` |
| reads its data through `importlib.resources` | `validation` (jsonschema's meta-schemas) | served from the payload; no settings |
| uses packages fsbpyr already knows need their files on disk | `matplotlib` (fonts and mpl-data), `cloud` (botocore's service models), `torch`; also babel, certifi and litellm | built-in `fs-packages`: no settings |
| loads native libraries with ctypes, by path or by name | `boosting` (xgboost, lightgbm), `pyscf` (`numpy.ctypeslib`), `hdf5` (blosc2 by bare name), `geo` (rtree's libspatialindex), `casadi` and `openmm` (plugins from a directory) | `fs-packages` for path-based loading; a bare name finds the bundled library |
| needs system libraries no wheel bundles | `boosting` (libgomp), `numba` (libgomp, libtbb), `casadi` (commercial solver libraries its plugins link but never load) | `allow-system-libs`; the target must provide them |
| starts worker processes with `multiprocessing` or `fork` | `torch` (DataLoader workers from a forkserver), `hf-datasets` (multiprocess and dill), `xarray` (`dask.distributed` workers), `gunicorn` (forked workers) | works without settings; children are recognised |
| starts helper interpreters as `sys.executable -m/-c/<script>` | `sklearn` (joblib's loky), `jupyter` (ipykernel), `hdf5` (py-cpuinfo) | `interpreter-reexec = true` |
| runs executables shipped inside a package | `pulp` (CBC), `vision` (imageio-ffmpeg's ffmpeg) | `fs-packages` so the binary exists on disk |
| generates or compiles code at run time | `torch` (TorchScript reads the source, dynamo traces bytecode), `jax` (XLA), `numba` (JIT and `cache=True`), `sympy` (`lambdify`), `tensorflow` (`tf.function`), `grpc` (protoc with grpc_tools' include files) | sources kept in the payload (`--source`, the default); `fs-packages` where files are needed on disk |
| finds plugins through entry points or namespace packages | `pytest-plugin` (pluggy), `networkx` (backends), `hydra` (`hydra_plugins`), `observability` (OpenTelemetry), `qiskit` (transpiler plugins), `xarray` (backends), `twisted` (`twisted/plugins`), `pyomo`, `cloud` (Google namespace packages), `nlp` (tiktoken's plugin namespace) | `importlib.metadata` reads entry points from the dist-info directories in the payload; listing a plugin directory on disk needs `fs-packages` (`twisted`) |
| serves HTTP, WebSocket or gRPC, with TLS | `fastapi` (uvicorn, uvloop, httptools), `aiohttp`, `gunicorn`, `grpc`, `gevent` (pywsgi), `twisted`, `django`, `flask-jinja` | no settings, except templates and static files next to a package |
| uses databases, queues and storage formats | `psycopg`, `datastores` (async SQLAlchemy, aiosqlite, fakeredis), `alembic` (migrations loaded by path), `optuna` (SQLite), `celery` (in-process worker), `hdf5` (HDF5, NetCDF), `dataframes` (Arrow, Parquet), `xarray` (zarr) | mostly none; `fs-packages` where migrations or data live next to modules |
| does cryptography and authentication | `auth` (JWT, bcrypt, argon2, PyNaCl, paramiko over SSH) | none |
| trains, saves and loads models | `torch`, `lightning`, `keras`, `transformers` (safetensors), `onnx`, `boosting`, `sklearn` | as the rows above |
| runs notebooks | `jupyter` (nbclient running an ipykernel) | `interpreter-reexec`, `fs-packages` |
| emits warnings attributed to the importing module | `fastapi` (stacklevel 2), `auth` (stacklevel 3) | none: warnings name the right module |
| uses libraries that vendor others | `setuptools-vendor` | none |

### By how the code is kept

| Use case | What fsbpyr does | Tested by |
|---|---|---|
| **EL8 hosts** (RHEL 8, Rocky Linux 8, AlmaLinux 8; glibc 2.28) | `--glibc 2.28`: the launcher linked against Rocky Linux 8.10's glibc 2.28, `manylinux_2_28` wheels, the release's stub from its companion release `el8-vX.Y.Z` | `tests/e2e/test_el8.py`, `test_el8_use_cases.py` (they need the runtime linked for glibc 2.28); `tests/unit/runtime/test_stubs.py`, `test_rpm.py`, `test_sysroot.py` |
| **Services under systemd, cron and containers** (every app) | `PYTHONUNBUFFERED` honoured; stdout and stderr line-buffered when they are systemd's journal (`JOURNAL_STREAM`); SIGTERM ends an app that is process 1 with status 143; the cache root found from the passwd home without `HOME` | `tests/e2e/test_unbuffered.py`, `test_pid1.py`; `tests/unit/boot/test_service.py` |
| **Requirements files with `-e` local packages and VCS pins** | `-e` paths installed as regular packages with a warning; commit-pinned VCS lines and `#sha256=` URLs count as pinned; with `--strict` or `--hash` lines, VCS lines are refused early | `tests/e2e/test_build.py`; `tests/unit/builder/test_project_and_staging.py` |
| **pyproject apps without a build system** (`uv init --no-package`, Poetry `package-mode = false`, PDM `distribution = false`) | a wheel of the project's Python files under `_fsbpyr_scripts/`, written by fsbpyr; scripts run as `uv run main.py` runs them | `tests/e2e/test_scripts.py` (fixture `scripts`); `tests/unit/builder/test_loose.py`, `tests/unit/test_poetry1.py` |
| **Several console scripts, one executable** | a command table in the entrypoint, a relative symlink per command, dispatch on `argv[0]` or `argv[1]` | `tests/e2e/test_multicall.py` (fixture `multi`); `tests/unit/boot/test_commands.py`, `tests/unit/builder/test_multicall.py` |
| **`python -m pkg` packages and single guarded modules** | entry inferred from the only `__main__` package, else the only guarded module | `tests/unit/builder/test_entry.py`; `tests/e2e/test_build.py` |
| **Legacy `scripts=` files** | `#!python` scripts of the project's wheel become commands | `tests/e2e/test_build.py`; `tests/unit/builder/test_legacy.py` |
| **A directory of loose scripts** | one executable, a command per script, names that are no Python names stored under module names, a static import check, dependencies from requirements, PEP 723 blocks or the venv | `tests/e2e/test_loose_directory.py`; `tests/unit/builder/test_loose_directory.py`, `test_venvdeps.py` |
| **Monitoring plugins and cron fleets** (a directory of scripts called by agents) | links named as the agents call them; one shared instance limit; links made on the target with `FSBPYR_INSTALL_LINKS` | `tests/e2e/test_loose_directory.py`, `test_install_links.py` |
| **One script, with or without a PEP 723 block** | the script and its local-import closure; the block's dependencies, `requires-python` and `[tool.fsbpyr]`; a `<script>.lock` | `tests/e2e/test_single_script.py`; `tests/unit/builder/test_single_script.py` |
| **Files next to a script** | `__file__` beside the executable, code from the payload | `tests/e2e/test_file_overlay.py`; `tests/unit/boot/test_scripts_overlay.py` |
| **A tool from its requirements file** | the console and gui scripts of the named distributions, meta-packages and families followed | `tests/e2e/test_tool_binary.py`; `tests/unit/builder/test_tools.py`, `tests/unit/test_tool_metas.py` |
| **Poetry, PDM and Pipenv locks** | the lock converted into hashed requirements for uv | `tests/e2e/test_pipfile_lock.py` (fixture `foreign/pipenv`); `tests/unit/builder/test_foreign.py`, `test_pipenv.py` |

[Testing](Dev-Testing.md) describes each of these files.

## Terms

| Term | Meaning |
|---|---|
| **PBS** | python-build-standalone: the CPython builds (static libraries as LLVM bitcode) a runtime is linked from |
| **runtime** | a launcher stub linked with one CPython, plus that CPython's standard library compiled to bytecode; identified as `cpXY-<target>-<hash>` |
| **stub** | the ELF part of an executable: the launcher and CPython |
| **payload** | everything after the stub: header, index, blobs, trailer |
| **boot modules** | `_fsbpyr_boot` and `_fsbpyr_rt`, the Python code fsbpyr puts in every payload |
| **natives** | extension modules and shared libraries, extracted to the cache to be loaded |
| **variant** | the fsbpyr executable of the same release for another Python |
| **command table** | the entrypoint of an executable of several commands: `name=target` lines, read by the boot code |
| **scripts root** | `_fsbpyr_scripts`, the reserved top-level package under which the Python files of a project without a build system, a directory of scripts or a single script are stored |
| **tool binary** | an executable built from a directory that holds only a requirements file, whose commands are the named tools' scripts |
| **corpus** | the 65 real applications in `tests/corpus` |
| **D1, D2, ...** | numbered design decisions, cited in code comments and on some pages; defined in the implementation plan's [§2 Key decisions](../docs/IMPLEMENTATION_PLAN.md#2-key-decisions) |
