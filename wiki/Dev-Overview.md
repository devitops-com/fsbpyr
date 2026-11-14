# Developer overview

The User Guide starts at [What fsbpyr is](User-Overview.md).

## The project in brief

fsbpyr is a Python program (`src/fsbpyr`, about 18,800 lines in 78 files) plus a C launcher
(`src/fsbpyr/launcher`, 21 `.c`/`.h` files, about 4,800 lines). Its only runtime dependencies are
click, packaging and, before Python 3.14, `backports.zstd`. It runs on CPython 3.12 to 3.15 and
builds its own executable.

Every app involves three programs ([Architecture](Dev-Architecture.md)):

| Part | Language | Runs | Does |
|---|---|---|---|
| **fsbpyr** | Python | on the build machine | stages the input with uv, decides what the app needs, compiles it, writes the executable and its links |
| **launcher** | C, linked statically with CPython | first, in every executable | checks the file's integrity, finds the payload, starts CPython and the boot code |
| **boot** (`_fsbpyr_boot`, `_fsbpyr_rt`) | Python | inside every executable | picks the command, imports from the payload, serves resources, extracts native code, handles helper processes and service policies, collects the cache |

An app's executable is a [runtime](#terms)'s launcher stub followed by a payload: an indexed,
zstd-compressed archive of the standard library, the boot modules and the app with its locked
dependencies, and a trailer with the whole-file digest.

![fsbpyr from project to running app: inputs, the build on the build machine, the executable's layout, and the start on the target machine](images/arch-overview.svg)

[Home](Home.md) lists the other pages.

## Goals

How @VERSION@ meets the implementation plan's
[§0 Requirements](../docs/IMPLEMENTATION_PLAN.md#0-requirements):

| Goal | How it is met | Where |
|---|---|---|
| **One command.** A uv or pip project becomes one executable with `fsbpyr build` | uv installs from `uv.lock`, `pylock.toml`, a requirements file or a converted Poetry, PDM or Pipenv lock; a released fsbpyr carries its runtime, needing no compiler | `build.py`, `staging.py`, `foreign.py`, `pipenv.py` |
| **No questions.** The input decides what is built | commands and dependencies follow from the kind of input ([below](#by-how-the-code-is-kept)) | `entry.py`, `links.py`, `loose.py`, `legacy.py`, `tools.py`, `venvdeps.py`, `importcheck.py`; `_fsbpyr_rt/commands.py` |
| **Self-hosting.** fsbpyr builds its own executable | `make bootstrap`: stages 2 and 3 must be byte-identical | `tools/bootstrap.sh` ([Self-hosting](Dev-Self-Hosting.md)) |
| **One file.** Only the loader, glibc and explicitly allowed system libraries stay outside | everything else goes into the payload; a native file needing a library neither bundled nor on auditwheel's manylinux list stops the build unless `allow-system-libs` names it | `natives.py`, `classify.py` |
| **Startup scales with what runs** | an index is searched by name; only what is imported is decompressed | [Executable format](Dev-Executable-Format.md), [Boot modules](Dev-Boot-Modules.md) |
| **Independently addressable compression** | zstd frames per blob, small blobs in shared blocks; xz for native files only with `--best-compression` | `payload/writer.py`, `payload/spec.py` |
| **Pure Python runs from memory** | a path hook and loader serve it from the payload | `boot/_fsbpyr_boot.py` |
| **Native code through an immutable, self-healing cache**, loaded by the system loader | written under `runtime/<generation>/site/` in the app's cache, one generation per build; a file appears only when complete, is hashed once per process before first use, and is restored from the payload when changed | `launcher/cache.c`, `boot/_fsbpyr_rt/native.py` |
| **Package data from memory; a file only when needed** | `importlib.resources` reads the payload; a resource asked for as a path, or an `fs-packages` package, is extracted on demand | `boot/_fsbpyr_rt/resources.py` |
| **Security and integrity** | whole-file SHA-256 tree digest checked before anything runs; strict cache ownership and modes; set-uid starts refused; an instance limit; releases and, on request, apps signed with Ed25519 and ML-DSA-65 | [Security](Dev-Security.md) |
| **Measured** | startup, cold start, integrity check, build time and size, per release | [Performance](Dev-Performance.md) |

The test suite also keeps builds **reproducible** and every corpus app's commands behaving **as
in a venv**.

## Scope and limits (@VERSION@)

- **Targets:** Linux x86-64 and aarch64 with glibc 2.34 or newer, or glibc 2.28 (EL8) for an
  app built with `--glibc 2.28`. Each fsbpyr builds for its own platform; no cross builds.
  musl, macOS and aarch64 kernels with 16K pages are not supported.
- **Python:** CPython 3.12.16, 3.13.18, 3.14.10 and 3.15.2 (python-build-standalone 20261110),
  one fsbpyr executable per version; 3.14 is the default. Free-threaded CPython is refused.
- **Apps:** anything uv can install that has an entry point, and the inputs
  [below](#by-how-the-code-is-kept). Some packages need [settings](User-Settings.md). A file
  whose shebang names `python2` is no script (`_SHEBANG_PROGRAM` in
  [`loose.py`](../src/fsbpyr/loose.py)).

## Use cases

Tests establish every use case below: the corpus ([`tests/corpus`](../tests/corpus)) is 65
projects, one console script each, whose commands give the same exit code, stdout and stderr as
in a venv on every Python and target where their packages have wheels;
[Compatibility](Dev-Compatibility.md#tested-on-real-applications) lists each app's risk and
settings. End-to-end tests cover the other ways code is kept, most comparing the executable's
output with what `python3 script.py` or uv prints
([research](../docs/use-case-research.md)).

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

Rows that name no setting need none.

| An app that... | Corpus apps | What fsbpyr does / needs |
|---|---|---|
| imports pure-Python packages and compiled extensions | all; `pandas-csv`, `scipy`, `fitting`, `qutip` nothing else | modules from memory, extensions from the cache |
| loads extensions with bundled shared libraries | `psycopg`, `messaging`, `lxml`, `pillow`, `faiss`, `dataframes` | extracts them with the extension that needs them |
| uses an extension built with mypyc | `black`; `charset_normalizer` in the `compat` fixture | a mypyc package's `__path__` is its cache directory; data files next to it need `fs-packages` (`black`) |
| reads data files next to its `__file__` | `flask-jinja`, `django`, `sklearn`, `astropy`, `pint`, `statsmodels`, `optuna`, `materials`, `pyscf`, `geo`, `nlp`, `vision`, `transformers`, `lightning` | `fs-packages` |
| reads its data through `importlib.resources` | `validation` | served from the payload |
| uses packages known to need their files on disk | `matplotlib`, `cloud`, `torch`; also babel, certifi, litellm | built-in `fs-packages` |
| loads native libraries with ctypes, by path or by name | `boosting`, `pyscf`, `hdf5` (by bare name), `geo`, `casadi`, `openmm` | `fs-packages` for loading by path; a bare name finds the bundled library |
| needs system libraries no wheel bundles | `boosting`, `numba`, `casadi` | `allow-system-libs`; the target must provide them |
| starts worker processes with `multiprocessing` or `fork` | `torch`, `hf-datasets`, `xarray`, `gunicorn` | recognises the children |
| starts helper interpreters as `sys.executable -m/-c/<script>` | `sklearn`, `jupyter`, `hdf5` | `interpreter-reexec = true` |
| runs executables shipped inside a package | `pulp`, `vision` | `fs-packages`, so the binary exists on disk |
| generates or compiles code at run time | `torch`, `jax`, `numba`, `sympy`, `tensorflow`, `grpc` | sources kept in the payload (`--source`, the default); `fs-packages` where files must be on disk |
| finds plugins through entry points or namespace packages | `pytest-plugin`, `networkx`, `hydra`, `observability`, `qiskit`, `xarray`, `twisted`, `pyomo`, `cloud`, `nlp` | `importlib.metadata` reads entry points from the payload; listing a plugin directory needs `fs-packages` (`twisted`) |
| serves HTTP, WebSocket or gRPC, with TLS | `fastapi`, `aiohttp`, `gunicorn`, `grpc`, `gevent`, `twisted`, `django`, `flask-jinja` | `fs-packages` only for templates and static files next to a package |
| uses databases, queues and storage formats | `psycopg`, `datastores`, `alembic`, `optuna`, `celery`, `hdf5`, `dataframes`, `xarray` | `fs-packages` where migrations or data live next to modules |
| does cryptography and authentication | `auth` | none |
| trains, saves and loads models | `torch`, `lightning`, `keras`, `transformers`, `onnx`, `boosting`, `sklearn` | as the rows above |
| runs notebooks | `jupyter` | `interpreter-reexec`, `fs-packages` |
| emits warnings attributed to the importing module | `fastapi`, `auth` | warnings name the right module |
| uses libraries that vendor others | `setuptools-vendor` | none |

### By how the code is kept

| Use case | What fsbpyr does | Tested by |
|---|---|---|
| **EL8 hosts** (RHEL 8, Rocky Linux 8, AlmaLinux 8; glibc 2.28) | `--glibc 2.28`: `manylinux_2_28` wheels and the release's stub, linked against Rocky Linux 8.10's glibc 2.28, from its companion release `el8-vX.Y.Z` | `tests/e2e/test_el8.py`, `test_el8_use_cases.py`; `tests/unit/runtime/test_stubs.py`, `test_rpm.py`, `test_sysroot.py` |
| **Services under systemd, cron and containers** (every app) | `PYTHONUNBUFFERED` honoured; line-buffered output to systemd's journal (`JOURNAL_STREAM`); SIGTERM ends process 1 with status 143; the cache root found without `HOME` from the passwd home | `tests/e2e/test_unbuffered.py`, `test_pid1.py`; `tests/unit/boot/test_service.py` |
| **Requirements files with `-e` local packages and VCS pins** | `-e` paths installed as regular packages, with a warning; commit-pinned VCS lines and `#sha256=` URLs are pinned; `--strict` or `--hash` lines refuse VCS lines early | `tests/e2e/test_build.py`; `tests/unit/builder/test_project_and_staging.py` |
| **pyproject apps without a build system** (`uv init --no-package`, Poetry `package-mode = false`, PDM `distribution = false`) | fsbpyr writes a wheel of the Python files under `_fsbpyr_scripts/`; scripts run as under `uv run` | `tests/e2e/test_scripts.py` (fixture `scripts`); `tests/unit/builder/test_loose.py`, `tests/unit/test_poetry1.py` |
| **Several console scripts, one executable** | a command table, a relative symlink per command, dispatch on `argv[0]` or `argv[1]` | `tests/e2e/test_multicall.py` (fixture `multi`); `tests/unit/boot/test_commands.py`, `tests/unit/builder/test_multicall.py` |
| **`python -m pkg` packages and single guarded modules** | entry inferred from the only `__main__` package, else the only guarded module | `tests/unit/builder/test_entry.py`; `tests/e2e/test_build.py` |
| **Legacy `scripts=` files** | the wheel's `#!python` scripts become commands | `tests/e2e/test_build.py`; `tests/unit/builder/test_legacy.py` |
| **A directory of loose scripts** | a command per script, a static import check, dependencies from requirements, PEP 723 blocks or the venv | `tests/e2e/test_loose_directory.py`; `tests/unit/builder/test_loose_directory.py`, `test_venvdeps.py` |
| **Monitoring plugins and cron fleets** (scripts called by agents) | links named as the agents call them, or made on the target with `FSBPYR_INSTALL_LINKS`; one shared instance limit | `tests/e2e/test_loose_directory.py`, `test_install_links.py` |
| **One script, with or without a PEP 723 block** | the script and its local imports; the block's dependencies, `requires-python` and `[tool.fsbpyr]`; a `<script>.lock` | `tests/e2e/test_single_script.py`; `tests/unit/builder/test_single_script.py` |
| **Files next to a script** | `__file__` beside the executable, code from the payload | `tests/e2e/test_file_overlay.py`; `tests/unit/boot/test_scripts_overlay.py` |
| **A tool from its requirements file** | the named distributions' console and gui scripts, through meta-packages and families | `tests/e2e/test_tool_binary.py`; `tests/unit/builder/test_tools.py`, `tests/unit/test_tool_metas.py` |
| **Poetry, PDM and Pipenv locks** | converted into hashed requirements for uv | `tests/e2e/test_pipfile_lock.py` (fixture `foreign/pipenv`); `tests/unit/builder/test_foreign.py`, `test_pipenv.py` |

[Testing](Dev-Testing.md) describes these files.

## Terms

| Term | Meaning |
|---|---|
| **PBS** | python-build-standalone: the CPython builds (static libraries as LLVM bitcode) a runtime is linked from |
| **runtime** | a launcher stub linked with one pinned PBS CPython, plus its standard library as bytecode, one per Python, target and glibc baseline (`cpXY-<triple>-<hash>`); built by `fsbpyr runtime build`, embedded in every released fsbpyr |
| **stub** | the ELF part of an executable: the launcher and CPython |
| **payload** | everything after the stub: header, index, blobs, trailer |
| **boot modules** | `_fsbpyr_boot` and `_fsbpyr_rt`, in every payload |
| **natives** | extension modules and shared libraries, loaded from the cache |
| **variant** | the fsbpyr executable of the same release for another Python |
| **command table** | the entrypoint of an executable of several commands: `name=target` lines |
| **scripts root** | `_fsbpyr_scripts`, the reserved package for the files of a project without a build system, a directory of scripts or a single script |
| **tool binary** | an executable built from a directory holding only a requirements file |
| **corpus** | the 65 applications in `tests/corpus` |
| **D1, D2, ...** | design decisions of the implementation plan's [§2 Key decisions](../docs/IMPLEMENTATION_PLAN.md#2-key-decisions) |
