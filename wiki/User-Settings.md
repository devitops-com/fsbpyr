# Settings in `pyproject.toml`

Most build settings can live in the project's `pyproject.toml` under `[tool.fsbpyr]`, so that a
plain `fsbpyr build` always builds the app the same way. Options on the command line win over
the table, both ways for on/off settings. This page lists the keys, says where scripts keep their settings, then explains the three
settings that real apps need, with examples from the [test corpus](User-Examples.md).

## Every key

```toml
[tool.fsbpyr]
python = "3.13"                    # as --python
glibc = "2.28"                     # as --glibc: for EL8 hosts
entry = "mytool"                   # as --entry
output = "dist/mytool"             # as --output, relative to the project
requirements = "requirements.txt"  # as --requirements; pip projects only, overrides uv.lock
extras = ["fancy"]                 # as --extra
include-source = false             # as --no-source
fast-compression = true            # as --fast-compression
# best-compression = true          # as --best-compression (not both)
fs-packages = ["mypkg.templates"]  # as --fs-package
allow-system-libs = ["libcuda.so.1"]  # as --allow-system-lib
interpreter-reexec = true          # as --interpreter-reexec
smoke-args = ["--version"]         # as --smoke-args "--version"
strict = true                      # as --strict
```

| Key | Type | Command line |
|---|---|---|
| `python` | string `"X.Y"`, 3.12 to 3.15 | `--python` |
| `glibc` | string, `"2.34"` (default) or `"2.28"` ([Building for EL8](User-Building.md#building-for-el8-glibc-228)) | `--glibc` |
| `entry` | string | `--entry` |
| `output` | string, a path relative to the project | `-o`, `--output` |
| `requirements` | string | `--requirements` |
| `extras` | list of strings | `--extra` |
| `include-source` | boolean, default true | `--source` / `--no-source` |
| `fast-compression` | boolean | `--fast-compression` |
| `best-compression` | boolean | `--best-compression` |
| `fs-packages` | list of package names (dotted names allowed) | `--fs-package` |
| `allow-system-libs` | list of library file names (no `/`) | `--allow-system-lib` |
| `interpreter-reexec` | boolean, or unset | `--interpreter-reexec` / `--no-interpreter-reexec` |
| `smoke-args` | list of strings | `--smoke-args` |
| `strict` | boolean | `--strict` |

An unknown key stops the build and names the supported keys; a value of the wrong type stops it
and names the key and the value. `fast-compression` and `best-compression` exclude each other,
and a compression flag on the command line replaces the other one set in the table. `--offline`,
`--hints`, `--smoke` and `--verbose` exist on the command line only. What each setting does is
on [Building an app](User-Building.md#options).

## Settings without a project

Scripts have no `pyproject.toml` of a project to hold the table:

- **A directory of scripts** (and a tool built from a requirements file) reads `[tool.fsbpyr]`
  from a `pyproject.toml` beside the scripts that holds only tool settings: no `[project]`, no
  `[build-system]`, no Poetry tables. Such a file is not a project: no build backend ever sees
  it. A `[tool.fsbpyr]` table in a script's PEP 723 block is ignored in a directory build, with
  a warning that names the file.
- **A single script** reads the table from its PEP 723 block, as comment lines. A script
  `fetch.py` that starts with:

  ```python
  # /// script
  # requires-python = ">=3.12"
  # dependencies = ["requests"]
  #
  # [tool.fsbpyr]
  # include-source = false
  # ///
  ```

  `fsbpyr build fetch.py` with this block builds without the sources (`fsbpyr inspect` shows
  `sources    left out`) and warns that the build is unlocked, since nothing locks `requests`
  (a `fetch.py.lock` written by `uv lock --script fetch.py` would). Unknown keys are rejected as
  in `pyproject.toml`.

[Scripts and tools](User-Scripts.md) has the rest of what these builds do.

## Does my app need settings?

Many apps need none: of the 65 apps in the corpus, 35 build and run with no `[tool.fsbpyr]`
table at all. Most of the others name one or two packages in `fs-packages`. To find out for your
app:

1. **Look your packages up** in the corpus list, [Developer Guide: Compatibility](Dev-Compatibility.md#tested-on-real-applications),
   which gives the exact settings each tested app needs.
2. **Build without settings, then run the app's main commands.** The build's smoke test catches
   much on its own. Then follow what fsbpyr prints:

| What you see | Setting |
|---|---|
| `fsbpyr: <path> is inside the executable; <package> reads files next to its __file__: rebuild with --fs-package <package>` after an uncaught file error (usually `NotADirectoryError`) | [`fs-packages`](#fs-packages) |
| A template, data file or plugin that is not found, though the package ships it | [`fs-packages`](#fs-packages) |
| The build stops: `native files need libraries that are neither bundled nor standard system libraries` | [`allow-system-libs`](#allow-system-libs) |
| The build warns `these may start helper interpreters through sys.executable: ...`, or a child process exits 70 with `fsbpyr: refusing to run ... as Python` | [`interpreter-reexec`](#interpreter-reexec) |

The note in the first row is missing when the app catches the error itself or installs its own
`sys.excepthook` (typer does), and a package that only checks whether its files exist
sees `False` and may fail later in another way. `fsbpyr build --hints` lists packages that *may*
read files next to `__file__`; these are guesses, and they can miss a package (they did not
name `corpus_flask` in the example below).

## `fs-packages`

**What it is for.** Inside an executable, a module's `__file__` is a path below the executable
(`/usr/local/bin/mytool/mypkg/__init__.py`), and the executable is one file, not a directory.
Code that reads files next to its `__file__` with `open()` or `os.listdir()` therefore fails. The
files of a package in `fs-packages` are extracted into the cache on its first import, and its
modules' `__file__` then point there (their code still runs from memory), so such code works.
The package also gets the libraries its extensions need from outside it, and every shared
library its distribution ships.

Six packages are known to need it and are always on the list: babel, botocore, certifi,
litellm, matplotlib and torch (`fsbpyr inspect` shows them under `fs pkgs`).

**Example: Flask templates.** The corpus app
[`flask-jinja`](../tests/corpus/flask-jinja)
serves a Jinja template and a static file from its own package `corpus_flask`:

```
src/corpus_flask/__init__.py
src/corpus_flask/cli.py
src/corpus_flask/templates/hello.html
src/corpus_flask/static/note.txt
```

Built **without** a setting, the build succeeds (the smoke test only imports the app), but Flask
cannot find the template and answers 500:

```console
$ dist/no-setting
[2026-10-27 15:37:13,026] ERROR in app: Exception on /Ada [GET]
Traceback (most recent call last):
  ...
jinja2.exceptions.TemplateNotFound: hello.html
...
500 INTERNAL SERVER ERROR
```

With one line in `pyproject.toml`:

```toml
[tool.fsbpyr]
fs-packages = ["corpus_flask"]  # Flask reads templates/ and static/ next to its package
```

the same app works:

```console
$ dist/corpus-flask-jinja
<p>Hello Ada!</p>
static file
200 OK
```

Other corpus apps that need it, and why: `sklearn` (reads `estimator.css` next to a module),
`django` (Django's translation files, the app's templates and REST framework's static files, all
found next to their modules), `astropy` (data tables), `optuna` (alembic migrations next to its
modules), `black` (`blib2to3` reads `Grammar.txt`). The full list is on
[Compatibility](Dev-Compatibility.md#tested-on-real-applications).

## `allow-system-libs`

**What it is for.** fsbpyr bundles every shared library the wheels ship. An extension module,
or a bundled library it loads, that needs a library which no wheel bundles and which is not a
standard system library stops the build: the executable would fail on a target without it. The
standard ones are those the manylinux policy lets wheels take from the system: glibc's
libraries, libstdc++, libgcc_s, zlib, the dynamic loader, and a few X11, OpenGL and GLib
libraries. `allow-system-libs` names the libraries the target machine is expected to provide.
They appear in what the build prints as `target needs:` and in `fsbpyr inspect` as `needs`.

**Example: LightGBM and OpenMP.** The corpus app
[`boosting`](../tests/corpus/boosting)
uses xgboost and lightgbm. Built without the setting:

```console
$ fsbpyr build --python 3.14
...
fsbpyr: collecting 11 distributions
fsbpyr: error: native files need libraries that are neither bundled nor standard system libraries:
  lightgbm/lib/lib_lightgbm.so needs libgomp.so.1
bundle them (auditwheel repair), or allow the host's with --allow-system-lib NAME (repeat), or in [tool.fsbpyr]:
  allow-system-libs = ["libgomp.so.1"]
$ echo $?
1
```

The build prints the exact line to add. With it, the target must provide GNU OpenMP:

```console
$ fsbpyr build --python 3.14
...
fsbpyr: target needs: glibc >= 2.34; libgcc_s.so.1 (GCC_4.8.0); libgomp.so.1 (GOMP_4.5, OMP_1.0); libstdc++.so.6 (CXXABI_1.3.11, GLIBCXX_3.4.22); libz.so.1
```

To check a target machine before deploying, run the app there with
`FSBPYR_SELFTEST=natives` ([Running](User-Running.md#checking-a-target-machine)).

## `interpreter-reexec`

**What it is for.** In an executable, `sys.executable` is the app, not a Python interpreter. Some
libraries start helper processes as `sys.executable -m MODULE`, `sys.executable -c CODE` or
`sys.executable` with one of their own `.py` files: joblib's workers (`n_jobs`), Jupyter
kernels, py-cpuinfo, `subprocess.run([sys.executable, "-m", "pkg.tool"])`. (`multiprocessing` is
different: it works with every start method and needs no setting.) What such a child of the app
gets depends on the build:

| Build | The child |
|---|---|
| `interpreter-reexec = true` (`--interpreter-reexec`) | runs the module, code or script as Python would; interpreter flags such as `-u` or `-B` are accepted and ignored |
| not set (default) | is refused: exit 70 and a message naming what it asked for and the option |
| `interpreter-reexec = false` (`--no-interpreter-reexec`) | runs the app with these arguments, for an app whose own options are named `-m` or `-c` |

The default refuses because running the app in such a child would make every child start the
app again, and every copy start more. Only a child of the app itself is treated this way: the
same command line typed in a shell runs the app. One child is never refused: a script of the
app that starts another of the app's script commands as `[sys.executable, <its __file__>]` gets
that command, as `python sibling.py` would ([Scripts and tools](User-Scripts.md)). And a start under
the name of one of the app's commands (its link) runs that command; it is no helper
interpreter.

**Example: scikit-learn with joblib.** The corpus app
[`sklearn`](../tests/corpus/sklearn) runs
`cross_val_score(..., n_jobs=2)`. Built with only `fs-packages = ["sklearn"]`, the build already
warns:

```console
fsbpyr: warning: these may start helper interpreters through sys.executable: joblib (workers and a resource tracker for n_jobs); they are refused (exit 70) unless the app is built with --interpreter-reexec
```

and the run fails when it reaches the parallel part:

```console
$ dist/no-reexec
iris (150, 4) ['setosa', 'versicolor', 'virginica']
digits (1797, 64) (1797, 8, 8)
fsbpyr: refusing to run `-c 'from joblib.externals.loky.backend.resource_tracker import m'...` as Python: the app started itself as a helper interpreter (sys.executable is the app), and running the app in its place would start it again and again; rebuild with --interpreter-reexec to run such children as Python, or with --no-interpreter-reexec if the app's own options are named -m or -c
fsbpyr: refusing to run `-m joblib.externals.loky.backend.popen_loky_posix` as Python: the app started itself as a helper interpreter (sys.executable is the app), and running the app in its place would start it again and again; rebuild with --interpreter-reexec to run such children as Python, or with --no-interpreter-reexec if the app's own options are named -m or -c
...
joblib.externals.loky.process_executor.TerminatedWorkerError: A worker process managed by the executor was unexpectedly terminated. ...
The exit codes of the workers are {EXIT(70)}
```

With the setting the app's `pyproject.toml` has:

```toml
[tool.fsbpyr]
fs-packages = ["sklearn"]
interpreter-reexec = true
```

the run completes:

```console
$ dist/corpus-sklearn
iris (150, 4) ['setosa', 'versicolor', 'virginica']
digits (1797, 64) (1797, 8, 8)
cross_val_score [0.9667, 1.0, 0.9333, 0.9, 1.0]
forest score 0.9046
parallel [0, 1, 4, 9, 16, 25, 36, 49]
threadpool ['blas', 'openmp']
```

Other corpus apps with `interpreter-reexec = true`: `jupyter` (an ipykernel started as
`sys.executable -m ipykernel_launcher`) and `hdf5` (py-cpuinfo runs its `cpuinfo.py` as a
script).

However an app starts copies of itself, no more of them run at once than its instance limit
allows (`FSBPYR_MAX_INSTANCES`, [Running](User-Running.md#environment-variables)): a start beyond
the limit exits 70.
