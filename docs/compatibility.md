# Compatibility

What differs between an app built by fsbpyr and the same project in a virtual environment,
what the machine that runs it must provide, what is tested, and what to do when it fails. The
supported targets are in the [README](../README.md#supported-targets).

Statements marked *unverified* come from reading code or documentation, not from a run.

## Differs from a venv

Each row gives what you see, why, and what to do.

| Symptom | Cause | Remedy |
|---|---|---|
| `NotADirectoryError: [Errno 20] Not a directory: '<exe>/<pkg>/data.json'`, or an existence check on such a path returns `False` | A package reads files next to its `__file__`. Its `__file__` is below the executable, which is one file, not a directory. An uncaught `OSError` ends with the note `fsbpyr: <path> is inside the executable; <package> reads files next to its __file__: rebuild with --fs-package <package>`; the note is absent when the app catches the error or replaces `sys.excepthook` (typer and rich do) | Build with `--fs-package <package>` (or `fs-packages` in `[tool.fsbpyr]`); `fsbpyr build --hints` lists likely packages |
| `PYTHONPATH`, `PYTHONHOME`, `PYTHONDONTWRITEBYTECODE` and the other `PYTHON*` variables have no effect; `sys.flags.utf8_mode` is 1 | The interpreter runs isolated, with UTF-8 mode on and no site import (D14). | Use `FSBPYR_*` variables or the app's own options |
| `sys.prefix` is the executable's directory; files outside `site-packages` (`share/`, `etc/`, scripts a wheel installs elsewhere) are not in the app | The launcher pins every prefix to the directory of the executable; the build packages the modules and package data of the locked distributions | Ship such files next to the app, or read them through the package that owns them (`importlib.resources`) |
| `ModuleNotFoundError` for `tkinter`, `idlelib`, `ensurepip`, `turtledemo` or `test`; `_dbm` (so `dbm.ndbm`) is missing | The build leaves these out of the standard library; `_tkinter` and `_dbm` are the two shared objects of the interpreter build, and neither is bundled. GUI toolkits that ship as wheels (Qt, wx) are not blocked, but no test covers them | Do not import them; `dbm.dumb` is pure Python |
| A resource opened for writing fails: `ValueError: payload resources are read-only; invalid mode ...`; an extracted file has mode 0400 or 0500 | Resources are served from the payload, and files extracted to the cache are made read-only | Write to a path of your own, not next to the package's data |
| `sys.frozen` is missing, and `sys._MEIPASS` too | `sys.frozen` is PyInstaller's flag, and code that sees it looks for PyInstaller's layout (D21) | Do not test for it; test for the files you need |
| A child the app starts as a Python interpreter (`subprocess.run([sys.executable, "-m", "tool"])`, `-c CODE`, or a `.py` file of the app, as joblib's `n_jobs`, Jupyter kernels and py-cpuinfo do) exits 70, printing `fsbpyr: refusing to run <what> as Python: the app started itself as a helper interpreter`; joblib then fails with `TerminatedWorkerError` | `sys.executable` is the app, not an interpreter (D22), and running the app in such a child would start it again and again (D44). multiprocessing children are recognized | Build with `--interpreter-reexec` ([D23](../docs/IMPLEMENTATION_PLAN.md)); then such a child of the app itself runs as CPython would. An app whose own options are named `-m` or `-c` and which starts itself with them: `--no-interpreter-reexec` |
| An app exits 70 printing `fsbpyr: refusing to start: <N> copies of this app are running already, as many as this machine allows at once` | N copies of the executable run already for this user, counted in `/dev/shm` (a container with a `/dev/shm` of its own counts apart); the limit is 4 per CPU and 16, at least 64 and at most 1024 (D45). It stops an app that starts itself again and again, however it starts it | If the app keeps starting itself, find the code that starts `sys.executable` (`ps -f`); if it needs more copies at once, set `FSBPYR_MAX_INSTANCES` to a higher number (at most 4096; 0 turns the limit off) |
| `ModuleNotFoundError: No module named 'pkg_resources'` | setuptools 84.0.0 ships no `pkg_resources`. With an older setuptools pin it finds no distributions (*unverified*) | Use `importlib.metadata` and `importlib.resources` |

## What the target must provide

The executable carries CPython, the standard library and the locked distributions. It needs
these from the machine:

- **glibc 2.34 or newer** on Linux x86-64 or aarch64 (RHEL 9, Amazon Linux 2023, Ubuntu
  22.04, Debian 12 and newer). `fsbpyr inspect` shows the glibc the executable actually needs.
  Only 2.34 and newer is tested: the distributions below, releases their vendors support.
- **The libraries native code links and the build did not bundle.** `fsbpyr build` prints
  `target needs: glibc >= X.Y; libstdc++.so.6 (GLIBCXX_..., CXXABI_...), libgcc_s.so.1`, and
  `fsbpyr inspect` shows it again. Install those libraries on the target. A build warns when a
  native file needs a `GLIBCXX`, `CXXABI`, `GCC` or `ZLIB` version newer than manylinux_2_34
  systems provide.
- **To check a target before deploying,** run the app there with `FSBPYR_SELFTEST=natives`. It
  extracts every native extension into the cache and runs the dynamic loader of the target the
  app was built for in trace mode on each, which resolves libraries and symbol versions
  without running the extension. It prints `fsbpyr selftest: missing libstdc++.so.6 (needed by
  <extension>, ...)` or `missing version `GLIBCXX_3.4.32' of libstdc++.so.6 (needed by ...)`
  for each, and exits 1 if any (0 and `all natives load` otherwise). It needs the cache
  directory as a run does, and checks only what extensions load at startup: libraries they
  open later by name (`dlopen`) are not seen. glibc targets only.
- **A mounted `/proc`.** The launcher reads its own executable through `/proc/self/exe`, and
  multiprocessing children are authenticated through `/proc/<ppid>/exe`.
- **A cache on a file system that allows execution.** Native code is extracted there and
  loaded from it. A `noexec` mount makes the import fail; the error then names the cache and
  suggests `FSBPYR_CACHE_DIR`. The directory must be yours, writable by nobody else
  (README, "At run time").
- **Time zone data, terminfo, the CA store and `openssl.cnf`** come from the system, as for
  any Python. An app that depends on the `tzdata` package or `certifi` carries its own. Which
  system paths each library reads has not been tested here.
- **The kernel:** no minimum is established. Containers share the host's kernel, so the
  distribution matrix below tests only the kernel of the machine that ran it. Where a feature
  is missing the launcher goes without: it falls back when `O_TMPFILE` is unavailable
  (`place_unnamed` in `src/fsbpyr/launcher/cache.c`), and `close_range` is compiled out when
  the headers lack it (`src/fsbpyr/launcher/due.c`). The instance limit needs OFD locks
  (Linux 3.15) and `/dev/shm` or `$XDG_RUNTIME_DIR`; without them a start runs unguarded
  (`src/fsbpyr/launcher/slots.c`).

## Tested on

The portability test builds the `hello`, `heavy` and `compat` fixtures once, copies them into
each of these distributions with podman and runs every check there. CI does so for each Python
(3.12 to 3.15) and each target (x86-64 and aarch64, on a runner of its architecture); on
aarch64 every image but `archlinux:latest` (no arm64 variant) runs. On 3.15 `heavy` is not
built (onnxruntime has no wheel) and the `compat` checks of packages without a usable wheel
are skipped (README, "Python 3.15").

| Distribution | glibc |
|---|---|
| `docker.io/library/rockylinux:9` | 2.34 |
| `docker.io/library/amazonlinux:2023` | 2.34 |
| `docker.io/library/ubuntu:22.04` | 2.35 |
| `docker.io/library/debian:12` | 2.36 |
| `docker.io/library/ubuntu:24.04` | 2.39 |
| `docker.io/library/almalinux:10` | 2.39 |
| `docker.io/opensuse/leap:16.0` | 2.40 |
| `docker.io/library/debian:13` | 2.41 |
| `docker.io/library/ubuntu:26.04` | 2.43 |
| `docker.io/library/fedora:latest` | 2.43 |
| `docker.io/library/archlinux:latest` | 2.44 |

The `compat` app runs one small check per library or behaviour in each build:

| Check | Covers |
|---|---|
| `click` | click's option parsing under its test runner |
| `typer` | a typer command under its test runner |
| `rich` | rich's table rendering and its Unicode width table |
| `certifi` | certifi's CA bundle, loaded into an `ssl` context |
| `pydantic` | pydantic model validation |
| `jinja2` | `PackageLoader` reading a template of the app |
| `pyyaml` | the libyaml-backed loader |
| `numpy` | numpy array arithmetic |
| `cryptography` | Fernet encryption round trip |
| `orjson` | orjson serialization |
| `zoneinfo` | `zoneinfo` served from the `tzdata` package, with no system database |
| `sqlalchemy` | an in-memory sqlite database, with the compiled extension |
| `paths` | `importlib.resources.as_file` and `open()` on a resource directory |
| `charset_normalizer` | a mypyc-compiled module loaded as an extension |
| `requests` | a GET with requests, against a local HTTP server |
| `httpx` | a GET with httpx against a local HTTP server; it loads certifi's bundle |
| `setuptools` | `import setuptools` and `distutils` mapped to setuptools' copy |
| `distutils` | `import distutils` before setuptools resolves to setuptools' copy |

Distributions and checks are compared with the tests in `tests/unit/test_cli_docs.py`, so this
list cannot drift from the code. It says nothing about packages that are not in it.

## Tested on real applications

Besides the fixtures, a corpus of small applications is built and run in the corpus job of
`full-suite.yml`: on every push to main, weekly and on request, for each Python (3.12 to 3.15)
and each target (x86-64 and aarch64); `make test-corpus` runs it locally. A release itself runs
no tests: it publishes a commit whose run passed. Each app is a project in
`tests/corpus/<name>` with its own `uv.lock`; the test runs its commands in a venv and in the
built executable, each run with an empty home directory of its own, and compares exit code,
stdout and stderr ([tests/corpus/README.md](../tests/corpus/README.md)). An app is skipped on a
Python where a package of its lock has no wheel for manylinux_2_34 (`tests/support/no-wheels.txt`:
on 3.15, 23 apps; `tensorflow` also on 3.14, and it has no 3.14 or 3.15 wheels at all). A
failing row is marked in its directory's `commands.toml` (**fails** below): the test expects
the failure and goes red once it is fixed. The `[tool.fsbpyr]` column is what the app needs
beyond the defaults; each setting's reason is a comment in its `pyproject.toml`.

### AI research and machine learning

| App | Risk it covers | `[tool.fsbpyr]` |
|---|---|---|
| `torch` | TorchScript compiling the source, dynamo tracing bytecode (not on 3.15, where `torch.compile` is not supported yet), DataLoader worker processes forked from a forkserver that preloads the app's entry module (3.14, 3.15), checkpoints | none |
| `torchvision` | custom operators registered in `torch.ops`, the image extension (opened by `torch.ops.load_library` through a `FileFinder` on the package directory) | `fs-packages = ["torchvision"]` |
| `lightning` | the Lightning trainer, checkpoints, torchmetrics | `fs-packages = ["lightning"]` |
| `transformers` | a tiny BERT and GPT-2 built offline, a tokenizer trained in process, safetensors, a pipeline | `fs-packages = ["transformers"]` |
| `hf-datasets` | `map(num_proc=2)` through multiprocess and dill, Arrow files | none |
| `jax` | jaxlib's extension, XLA compiling at run time, Flax and Optax | none |
| `keras` | Keras 3 on JAX, a model saved and loaded | none |
| `tensorflow` | TensorFlow's C++ runtime, `tf.function`, `tf.data`, SavedModel (3.12 and 3.13 only); a RUNPATH of about 20,000 bytes; op libraries loaded by a path built from `__file__` | `fs-packages = ["clang", "tensorflow"]` |
| `sklearn` | bundled datasets, joblib's loky worker processes started as `sys.executable -m` and `-c`, threadpoolctl | `fs-packages = ["sklearn"]`, `interpreter-reexec = true` |
| `boosting` | xgboost and lightgbm loading their shared libraries with ctypes from paths built from `__file__` | `allow-system-libs = ["libgomp.so.1"]`, `fs-packages = ["lightgbm", "sklearn", "xgboost"]` |
| `onnx` | onnx's reference evaluator and onnxruntime | none |
| `faiss` | faiss-cpu's SWIG module and OpenMP | none |
| `numba` | the JIT compiler, the parallel threading layer and `cache=True` | `allow-system-libs = ["libgomp.so.1.0.0", "libtbb.so.12"]`, `fs-packages = ["corpus_numba"]` |
| `nlp` | nltk, spaCy (language data imported by name), sentencepiece, tiktoken's plugin namespace | `fs-packages = ["spacy", "thinc"]` |
| `vision` | scikit-image's lazy_loader stubs, imageio-ffmpeg's bundled binary run as a subprocess; **fails**: cv2's loader puts its directory into `sys.path` behind the executable and finds the cv2 package again, not its binary | `fs-packages = ["cv2"]` |
| `matplotlib` | matplotlib and seaborn: fonts and data of mpl-data, Agg rendering | none |
| `jupyter` | nbclient running a notebook in an ipykernel started as `sys.executable -m ipykernel_launcher`, IPython in process, jedi (the standard library's sources come from the payload, `fixups.py`); the kernelspec search is cut to ipykernel's own (`XDG_DATA_DIRS`): a built app has no `share/`, and a host's `/usr/share/jupyter/kernels/python3` (Debian's python3-ipykernel) would start the system's Python as the kernel | `fs-packages = ["debugpy", "IPython", "nbformat", "parso"]`, `interpreter-reexec = true` |
| `hydra` | a config package, `_target_` instantiation, plugin discovery in the `hydra_plugins` namespace | none |
| `optuna` | a study in SQLite, whose storage runs alembic migrations kept next to optuna's modules | `fs-packages = ["optuna"]` |

### Applied mathematics

| App | Risk it covers | `[tool.fsbpyr]` |
|---|---|---|
| `scipy` | scipy's compiled libraries | none |
| `sympy` | lambdify executing generated source, mpmath, gmpy2 and python-flint ground types | none |
| `networkx` | backend dispatch through entry points, scipy-backed algorithms, GraphML | none |
| `cvxpy` | solver extensions (Clarabel, SCS, OSQP); cvxpy lists its own package directory | `fs-packages = ["cvxpy"]` |
| `pulp` | CBC, shipped as an executable file in the package and run as a subprocess; HiGHS in process | `fs-packages = ["pulp"]` |
| `ortools` | CP-SAT, routing and GLOP | none |
| `pyomo` | plugins loaded by name, the HiGHS interface | none |
| `casadi` | IPOPT, CVODES and qpOASES plugins `dlopen`ed by name from casadi's directory | `allow-system-libs` (12 libraries of commercial solvers and MATLAB that its plugins link and the app never loads), `fs-packages = ["casadi"]` |
| `statsmodels` | the formula API (patsy), bundled datasets (`statsmodels.api` imports `from .__init__ import test`: `pkg.__init__` is a module of its own, as in a venv) | `fs-packages = ["statsmodels"]` |
| `hdf5` | h5py, PyTables with blosc2 (loaded by bare name with ctypes, found among the bundled libraries) and netCDF4; py-cpuinfo running its `cpuinfo.py` as `sys.executable <script>` | `interpreter-reexec = true` |
| `xarray` | xarray backends and chunk managers through entry points, dask, zarr, `dask.distributed` worker processes | `fs-packages = ["dask", "distributed"]` |
| `geo` | shapely, pyproj's PROJ database, geopandas and pyogrio's GDAL data, rtree's libspatialindex (ctypes) | `fs-packages = ["pyproj", "pyogrio"]` |
| `dataframes` | polars, pyarrow and duckdb exchanging Arrow data, Parquet | none |
| `pandas-csv` | pandas with numpy and dateutil | none |

### Physics

| App | Risk it covers | `[tool.fsbpyr]` |
|---|---|---|
| `astropy` | units, IERS and leap-second tables with downloads off, coordinates, cosmology data, FITS and ECSV | `fs-packages = ["astropy", "astropy_iers_data"]` |
| `pint` | pint's definitions file, uncertainties, scipy's constants | `fs-packages = ["pint"]` |
| `fitting` | lmfit and emcee on numpy and scipy | none |
| `qutip` | quantum dynamics on scipy's sparse matrices | none |
| `hep` | ROOT files with uproot, awkward arrays, vector, particle's PDG tables, iminuit | none |
| `materials` | ase's data files and EMT calculator, spglib | `fs-packages = ["ase"]` |
| `pyscf` | C libraries loaded with `numpy.ctypeslib`, basis-set files | `fs-packages = ["pyscf"]` |
| `qiskit` | transpiler plugins through entry points, the Aer simulator | `fs-packages = ["qiskit", "qiskit_aer"]` |
| `openmm` | plugins loaded as shared libraries from a directory next to OpenMM's library; **fails**: the plugin libraries are not extracted (they are in `OpenMM.libs`, not in a package), so only the Reference platform is found | none |

### Services

| App | Risk it covers | `[tool.fsbpyr]` |
|---|---|---|
| `fastapi` | FastAPI and pydantic, uvicorn with uvloop, httptools and websockets serving from an import string; a warning raised during an import (stacklevel 2), attributed to the importing module | none |
| `django` | admin, auth, DRF, migrations found by listing packages, templates, translations, collectstatic | `fs-packages = ["django", "corpus_django", "rest_framework"]` |
| `flask-jinja` | templates and static files next to the package; without the setting the build succeeds and the app answers 500 | `fs-packages = ["corpus_flask"]` |
| `aiohttp` | a server and client over TLS with websockets | none |
| `gunicorn` | the arbiter and its sync and gthread workers forked from a WSGI app, SIGTERM | none |
| `grpc` | protoc run at run time with grpc_tools' include files, TLS, streaming | `fs-packages = ["grpc_tools"]` |
| `celery` | an in-process worker, transports resolved from aliases, chains and groups | none |
| `datastores` | async SQLAlchemy over aiosqlite (greenlet), fakeredis, bson | `fs-packages = ["fakeredis"]` |
| `alembic` | migration files named by digits, loaded by path | none |
| `psycopg` | a database driver whose wheel bundles libpq, libssl and libcrypto | none |
| `messaging` | pyzmq with its bundled libzmq, msgpack, fastavro, cbor2 | none |
| `auth` | JWT, bcrypt, argon2, PyNaCl, authlib, paramiko with an in-process SSH session; an import-time warning with stacklevel 3 | none |
| `cloud` | botocore's service models and endpoints, Azure SAS signing, Google namespace packages across distributions | none |
| `observability` | OpenTelemetry's context and propagators loaded through entry points, prometheus, structlog, loguru, sentry | none |
| `gevent` | monkey patching, pywsgi, a loop implementation chosen by name, gevent.subprocess | none |
| `twisted` | the reactor, twisted.web, plugins listed by `twisted.plugin.getPlugins` (twisted lists `twisted/plugins` on disk) | `fs-packages = ["twisted"]` |
| `validation` | jsonschema's meta-schemas through importlib.resources, marshmallow, Faker's locale providers, phonenumbers' lazy metadata | none |

### Tools

| App | Risk it covers | `[tool.fsbpyr]` |
|---|---|---|
| `black` | modules compiled with mypyc, whose `__path__` is the cache directory the extension was extracted to; its `blib2to3` reads `Grammar.txt` next to its `__file__` | `fs-packages = ["blib2to3"]` |
| `gnureadline` | an extension that links GNU readline statically | none |
| `lxml` | libxml2 and libxslt bundled in the wheel | none |
| `pillow` | Pillow's codecs and embedded font | none |
| `pytest-plugin` | a pluggy plugin found through an entry point | none |
| `setuptools-vendor` | the libraries setuptools vendors | none |

`tests/fixtures/projects/plugins` is a host and two plugin distributions with a custom
entry-point group and a PEP 420 namespace they share. The apps are run only for the commands in
their `commands.toml`. `import distutils` before `setuptools` resolves to setuptools' copy (the
`distutils` check above); the `setuptools-vendor` app imports `setuptools` first.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `refusing cache directory <path>: owned by another user`, or `... other users may write to it`, in a CI container | The way from `/` to the cache passes a directory that another user owns or others can write to. Mounted home directories (GitHub's `/github/home` in a job container) are the usual case | Put the cache under the user's own tree: set `FSBPYR_CACHE_DIR`, or `XDG_CACHE_HOME` as `.github/workflows/build.yml` does for its job container |
| `ImportError: ... noexec mount ... set FSBPYR_CACHE_DIR to another directory` | The cache is on a file system mounted `noexec` | Point `FSBPYR_CACHE_DIR` at a directory on a file system that allows execution |
| `ImportError: ... version 'GLIBC_2.xx' not found`, followed by what the build required and what the system has | A native file needs a newer glibc, libstdc++ or libgcc_s than the target has | Build on the baseline (manylinux_2_34 wheels, or `fsbpyr build` against the Debian 12 sysroot), or run on a newer system |
| A C++ or Fortran dependency without a manylinux wheel is built from its sdist, and the build warns about the glibc it needs | The sysroot has no libstdc++, so such a build cannot be redone against Debian 12's glibc and keeps the build host's | Build the dependency as a `manylinux_2_34` wheel (for example with cibuildwheel) and point uv at it |
| The shell says the executable is "not found" although the file exists | The target has no glibc loader at the path the executable names (musl, such as Alpine) | Run it on a glibc system; musl is not a supported target |
