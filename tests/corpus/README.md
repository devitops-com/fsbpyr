# The real-world corpus

Each directory here is a small application that uses real libraries the way real code does.
`tests/e2e/test_corpus.py` builds it with fsbpyr, runs each command of its `commands.toml` in
the app's venv and in the built executable, and compares exit code, stdout and stderr; one
output must also contain the app's `expect` string. `make test-corpus` runs the corpus
(`PYTHON=3.X` for another Python); full-suite.yml's corpus job runs it for every Python and
target. The apps and the risk each covers are listed in
[docs/compatibility.md](../../docs/compatibility.md#tested-on-real-applications).

## An app's files

```
tests/corpus/<app>/
  pyproject.toml   name "corpus-<app>", the script corpus-<app>, requires-python ">=3.12"
  uv.lock          `uv lock` in the directory
  commands.toml    what to run
  src/corpus_<package>/__init__.py, cli.py (main), ...
  work/            input files the commands name (optional)
```

`pyproject.toml`:

```toml
[project]
name = "corpus-<app>"
version = "1.0.0"
description = "fsbpyr corpus app: <the risk it covers>"
requires-python = ">=3.12"
dependencies = ["<package>>=<version>"]

[project.scripts]
corpus-<app> = "corpus_<package>.cli:main"

[build-system]
requires = ["uv_build>=0.12.17,<0.13.0"]
build-backend = "uv_build"
```

The test calls the console script by the directory's name: the script is `corpus-<app>`.
torch and torchvision come from PyTorch's CPU index (PyPI's x86-64 torch is the CUDA build,
gigabytes of NVIDIA libraries):

```toml
[tool.uv.sources]
torch = { index = "pytorch-cpu" }
torchvision = { index = "pytorch-cpu" }

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true
```

`commands.toml`:

```toml
expect = "<text one stdout contains>"   # so that two runs failing alike do not pass

[[command]]
argv = ["work/input.csv"]                # run from the project directory
env = { LANG = "de_DE.UTF-8" }           # optional: added to both runs' environment
stdin = "text\n"                         # optional: what both runs read on stdin
```

## Rules for an app

- **The same output on every run, in the venv and in the executable.** Seed every random
  generator. Round floating-point results (six significant digits is plenty) or print
  integers and shapes: results may vary in the last bits with the number of threads. Sort
  what has no order. No timestamps, durations, process ids, ports, addresses, host names,
  temporary paths or object reprs with `0x...`. No progress bars (tqdm, rich progress).
- **Every write goes into a temporary directory of the run.** Use `tempfile.TemporaryDirectory()`
  and point libraries' caches and configuration there before importing them
  (`MPLCONFIGDIR`, `HF_HOME`, `NUMBA_CACHE_DIR`, `KERAS_HOME`, ...). The venv's run and the
  executable's run share the project directory and the environment, and a cache the first
  run leaves (a font cache, a compiled kernel) changes what the second prints. Nothing is
  written into the project directory. The test gives each run an empty home directory of
  its own (`HOME` and the XDG base directories), so what a library keeps there by itself
  reaches neither the other run nor the user's home.
- **No network, except servers the app starts on 127.0.0.1, port 0,** and stops before it
  exits. No downloads: models are built from a configuration with random weights, datasets
  are made in memory or shipped in `work/`.
- **Quick and small:** each command finishes in well under a minute on one core.
- **Use the libraries as their users do**, through their public API, including what may
  break in a single-file executable (data files next to modules, plugins found through entry
  points, worker processes, compiled code loaded at run time). Nothing fsbpyr-specific, and
  no way around a library's own file access: finding where fsbpyr fails is the point.
- **One line per check,** starting with a short label (`solve [3.0, 1.0]`), so that a
  difference is easy to read and `expect` can name a line.
- **Warnings are output too.** A warning a library prints on every run is fine (both runs
  print it; the test normalizes the directory a module's file is named in). One that depends
  on the environment is not.

## Settings and failures

Build the app with no `[tool.fsbpyr]` table first, through the test (`make test-corpus
PYTEST_ADDOPTS="-k <app>"`), whose runs are contained (tests/support/contain.py): a built app
run by hand gets no such protection. When a run fails or differs:

- **The app is at fault** (output not deterministic, a write outside its temporary
  directory, a network access): fix the app.
- **fsbpyr needs a setting** (`fs-packages` for a package reading files next to its modules,
  `interpreter-reexec` for helper interpreters started as `sys.executable -m ...`, `-c ...` or
  with a `.py` file of the app, which a build without it refuses with exit 70, and the build
  names the libraries known to start them): add it under `[tool.fsbpyr]`, with a comment
  saying what failed without it. The session summary lists every app that needed a setting.
- **fsbpyr fails where no setting helps:** set `known-failure` in `commands.toml` to one line
  saying what fails, with a comment above it on the cause as far as it is known. The test
  then asserts that the app still fails and goes red once fsbpyr is fixed. When it fails
  on some Pythons only, make it a table: `known-failure = {reason = "...", pythons = ["3.14",
  "3.15"]}`.

A package without a wheel for fsbpyr's baseline (manylinux_2_34) on some Python goes into
`tests/support/no-wheels.txt` (a line `3.15 <package> ...`); the app is then not run there.
`tests/unit/test_corpus_locks.py` reads every app's lock for each Python and both machines,
and fails where it finds a package without a wheel and the list names none of the app's, so
no app is built from an sdist by surprise. A pure-Python package that publishes an sdist only
goes into the app's `commands.toml` instead, as `sdists = ["<package>"]`.
