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
| `sys.executable` runs the app, not an interpreter: `subprocess.run([sys.executable, "-m", "tool"])` starts the app again | The executable is the app (D22). multiprocessing children are recognized | Build with `--interpreter-reexec` ([D23](../docs/IMPLEMENTATION_PLAN.md)); then `-m` and `-c` run as CPython would, for a child of the app itself |
| `ModuleNotFoundError: No module named 'pkg_resources'` | setuptools 84.0.0 ships no `pkg_resources`. With an older setuptools pin it finds no distributions (*unverified*) | Use `importlib.metadata` and `importlib.resources` |

## What the target must provide

The executable carries CPython, the standard library and the locked distributions. It needs
these from the machine:

- **glibc 2.28 or newer** on Linux x86-64. `fsbpyr inspect` shows the glibc the executable
  actually needs, which can be older. Only 2.28 and newer is tested.
- **The libraries native code links and the build did not bundle.** `fsbpyr build` prints
  `target needs: glibc >= X.Y; libstdc++.so.6 (GLIBCXX_..., CXXABI_...), libgcc_s.so.1`, and
  `fsbpyr inspect` shows it again. Install those libraries on the target. A build warns when a
  native file needs a `GLIBCXX`, `CXXABI`, `GCC` or `ZLIB` version newer than manylinux_2_28
  systems provide.
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
  the headers lack it (`src/fsbpyr/launcher/due.c`).

## Tested on

The portability test builds the `hello`, `heavy` and `compat` fixtures once, copies them into
each of these distributions with podman and runs every check there.

| Distribution | glibc |
|---|---|
| `docker.io/library/debian:10` | 2.28 |
| `docker.io/library/rockylinux:8` | 2.28 |
| `docker.io/library/debian:11` | 2.31 |
| `docker.io/library/ubuntu:20.04` | 2.31 |
| `docker.io/library/rockylinux:9` | 2.34 |
| `docker.io/library/amazonlinux:2023` | 2.34 |
| `docker.io/library/ubuntu:22.04` | 2.35 |
| `docker.io/library/debian:12` | 2.36 |
| `docker.io/opensuse/leap:15.6` | 2.38 |
| `docker.io/library/ubuntu:24.04` | 2.39 |
| `docker.io/library/debian:13` | 2.41 |
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

Besides the fixtures, a corpus of small applications is built and run once a night, on every
push to main and before a release (`full-suite.yml`). Each is a project in `tests/corpus/<name>`
with its own `uv.lock`; the test runs its commands in a venv and in the built executable and
compares exit code, stdout and stderr. A failing row is marked in its directory's
`commands.toml`.

| App | Risk it covers | `[tool.fsbpyr]` |
|---|---|---|
| `alembic` | migration files named by digits, loaded by path | none |
| `black` | modules compiled with mypyc; **fails**: `black.output` is not found, because the compiled package's `__path__` is the cache directory and `--fs-package` refuses a package of native code | none |
| `flask-jinja` | templates and static files next to the package; without the setting the build succeeds and the app answers 500 | `fs-packages = ["corpus_flask"]` |
| `gnureadline` | an extension that links GNU readline statically | none |
| `lxml` | libxml2 and libxslt bundled in the wheel | none |
| `pandas-csv` | pandas with numpy and dateutil | none |
| `pillow` | Pillow's codecs and embedded font | none |
| `psycopg` | a database driver whose wheel bundles libpq, libssl and libcrypto | none |
| `pytest-plugin` | a pluggy plugin found through an entry point | none |
| `scipy` | scipy's compiled libraries | none |
| `setuptools-vendor` | the libraries setuptools vendors | none |

`tests/fixtures/projects/plugins` is a host and two plugin distributions with a custom
entry-point group and a PEP 420 namespace they share. The apps were only run on Linux x86-64
with the Python of `.python-version`, and only for the commands in their `commands.toml`.
`distutils` imported before `setuptools` is not found in the executable; the
`setuptools-vendor` app imports `setuptools` first, as the `compat` app does (*not
investigated further*).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `refusing cache directory <path>: owned by another user`, or `... other users may write to it`, in a CI container | The way from `/` to the cache passes a directory that another user owns or others can write to. Mounted home directories (GitHub's `/github/home` in a job container) are the usual case | Put the cache under the user's own tree: set `FSBPYR_CACHE_DIR`, or `XDG_CACHE_HOME` as `.github/workflows/release.yml` does for its job container |
| `ImportError: ... noexec mount ... set FSBPYR_CACHE_DIR to another directory` | The cache is on a file system mounted `noexec` | Point `FSBPYR_CACHE_DIR` at a directory on a file system that allows execution |
| `ImportError: ... version 'GLIBC_2.xx' not found`, followed by what the build required and what the system has | A native file needs a newer glibc, libstdc++ or libgcc_s than the target has | Build on the baseline (wheels, or `fsbpyr build` against the 2.28 sysroot), or run on a newer system |
| A C++ or Fortran dependency without a manylinux wheel is built from its sdist, and the build warns about the glibc it needs | The sysroot has no libstdc++, so such a build cannot be redone against glibc 2.28 and keeps the build host's | Build the dependency as a `manylinux_2_28` wheel (for example with cibuildwheel) and point uv at it |
| The shell says the executable is "not found" although the file exists | The target has no glibc loader at the path the executable names (musl, such as Alpine) | Run it on a glibc system; musl is not a supported target |
