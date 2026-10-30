# Building an app

```
fsbpyr build [OPTIONS] [PROJECT_DIR | FILE]
```

`PROJECT_DIR` is the directory to build (default: the current one); `FILE` is a single script.
This page explains what fsbpyr can build, what a project needs, what a build does and prints,
how the Python version is chosen, how several commands become one executable, and every
option, with examples from fsbpyr's [test corpus](User-Examples.md) and test fixtures. The same
settings can live in [`[tool.fsbpyr]`](User-Settings.md); command-line options win. Scripts
without a project and tools named in a requirements file have a page of their own:
[Scripts and tools](User-Scripts.md).

## What fsbpyr builds

`fsbpyr build` looks at what it is given and builds it without asking questions:

| You have | fsbpyr builds | Output (default) |
|---|---|---|
| A project with a build system and one console script | that command | `dist/<script>` |
| A project with several console scripts | one executable with all of them, and a link per command ([below](#several-commands-in-one-executable)) | `dist/<project>` and `dist/<command>` links |
| A project with no console script | its one package with a `__main__.py` or module with an `if __name__ == "__main__":` guard ([below](#a-project-without-console-scripts)) | `dist/<name>` |
| A project with a `[project]` table and no `[build-system]` (`uv init --no-package`, Poetry `package-mode = false`, PDM `distribution = false`) | its Python files, as `uv run main.py` runs them | [Scripts and tools](User-Scripts.md#projects-without-a-build-system) |
| A directory of scripts, with no project file | one executable with a command per script | [Scripts and tools](User-Scripts.md#a-directory-of-scripts) |
| One script: `fsbpyr build report.py` | the script and the local modules it imports | [Scripts and tools](User-Scripts.md#a-single-script) |
| A directory with no Python file and a `requirements.txt`, `pylock.toml` or `Pipfile.lock` | the tools the file names, a command per console script | [Scripts and tools](User-Scripts.md#a-tool-from-its-requirements-file) |

No build writes into the source tree, apart from the executable itself (in `dist/` by default).
What runs on the build machine differs: a project with a build system has its build backend
run, and the smoke test imports the module of its entry point (of each tool's command for a tool
binary), which runs that module's top-level code. The scripts
of a project without a build system, of a directory and a single script are read, never run: a
script runs as `__main__`, and the smoke test only checks that it is there. (A
`module:function` command that a project without a build system declares in
`[project.scripts]` is imported like any console script.) Dependencies built from source
distributions run their own build code in every case.

## What a project needs

- **A command.** A console script (`[project.scripts]`, or the build backend's own table such as
  setuptools' `entry_points`), or `--entry` naming a script, a module or `module:function`.
  Without either, the build infers one ([below](#a-project-without-console-scripts)).
- **A `requires-python`**, if it has one, that admits one of 3.12 to 3.15.
- **Its dependencies, declared, and ideally locked.** The first lock found is used:
  1. a requirements file that `--requirements` (or `requirements`) names;
  2. `uv.lock` (the project's, or its uv workspace's);
  3. `pylock.toml`;
  4. `requirements.txt`;
  5. `poetry.lock`;
  6. `pdm.lock`;
  7. `Pipfile.lock`.

  Dependency groups in `uv.lock` (`dev` among them) are not packaged. With no lock, the
  dependencies the project declares (`[project.dependencies]`) are resolved at build time, and
  the build warns `unlocked build: dependencies resolved now, not reproducible`.

  A dependency declared nowhere is never guessed from an import, and the build fails: the smoke
  test imports the entry module, which stops at the missing import (`ModuleNotFoundError: No
  module named 'requests'`). Only what the entry module imports when it loads is checked then;
  an import that runs later fails when the app runs. Scripts are checked differently, by a
  static import check that skips the commands with a missing import, and a directory of
  scripts or a single script that declares nothing can take its dependencies from the venv it
  runs in ([Scripts and tools](User-Scripts.md#imports-are-checked)).

A minimal project, `src/hello/cli.py` with a `main()`:

```toml
[project]
name = "hello"
version = "1.0.0"
requires-python = ">=3.14"

[project.scripts]
hello = "hello.cli:main"

[build-system]
requires = ["uv_build>=0.12.17,<0.13.0"]
build-backend = "uv_build"
```

Without `[build-system]` it is a project without a build system, whose `src/` stays in the module
path, and the build refuses `hello.cli:main`.

## A first build

The corpus app [`pandas-csv`](../tests/corpus/pandas-csv)
sums a CSV file with pandas. Its `pyproject.toml` has `requires-python = ">=3.12"`, a console
script `corpus-pandas-csv` and a `uv.lock`. Built with fsbpyr 0.28.0 for Python 3.14:

```console
$ fsbpyr build --python 3.14
fsbpyr: staging corpus-pandas-csv from uv.lock
fsbpyr: uv: building pandas-csv's wheel
fsbpyr: uv: installing the locked distributions
fsbpyr: uv: installing the project's wheel
fsbpyr: collecting 5 distributions
fsbpyr: writing .../pandas-csv/dist/corpus-pandas-csv
fsbpyr: smoke-testing .../pandas-csv/dist/.corpus-pandas-csv.2z6d6aoz.tmp
fsbpyr: built .../pandas-csv/dist/corpus-pandas-csv (65.1 MB, entry corpus_pandas_csv.cli:main, glibc >= 2.34)
fsbpyr: target needs: glibc >= 2.34; libgcc_s.so.1 (GCC_4.3.0); libstdc++.so.6 (CXXABI_1.3.9, GLIBCXX_3.4.21); libz.so.1
$ dist/corpus-pandas-csv work/sales.csv
         sum  mean  count
region
east     1.5  1.50      1
north   17.5  8.75      2
south   16.0  8.00      2
day
1    2
2    3
```

What happened:

1. uv built the project's wheel and installed the locked distributions into a staging
   directory.
2. fsbpyr collected the five distributions (the app, pandas, numpy, python-dateutil, six),
   compiled their modules to bytecode and wrote the executable next to its final name.
3. The **smoke test** ran the new file once (it imports the entry point and checks it) before
   renaming it into place. A build whose smoke test fails leaves the old file alone.
4. The last line lists what the **target machine must provide**: the glibc version, and the
   system libraries the native code links that no wheel bundles. Here those are libstdc++,
   libgcc_s and zlib, which nearly every Linux system has.

The uv on this build machine is the one that built the fsbpyr release (0.13.6 for 0.28.0). With
another uv the build also warns, for example `fsbpyr: warning: uv 0.13.5 is not uv 0.13.6,
which built this fsbpyr: the executable can differ from one built with uv 0.13.6, even from
identical sources`: uv builds the project's wheel and installs every distribution, so another
uv can give other bytes from the same sources. The warning does not stop the build, and
`--strict` does not count it.

## Which Python

An app runs on the CPython of the fsbpyr that builds it, and there is one fsbpyr per Python. The
build chooses the version, strongest first:

1. `--python X.Y` on the command line;
2. `python = "X.Y"` in `[tool.fsbpyr]` (both are an error when the version is unsupported or
   outside `requires-python`);
3. the Python pinned in `.python-version`, when it is supported and inside `requires-python`
   (outside `requires-python`: an error; inside it but unsupported, such as 3.11: a warning, and
   the next rule applies);
4. `requires-python`: with a lower bound, the lowest supported version that satisfies it
   (`>=3.10` gives 3.12); with only an upper bound, the highest below it (`<3.14` gives 3.13); a
   pinned minor version as pinned (`==3.13.*` gives 3.13). A project without `requires-python`
   uses the Python of the fsbpyr that builds it.

A single script's `requires-python` comes from its PEP 723 block. A `Pipfile.lock` that is the
lock source names a version in `_meta.requires` (`"python_version": "3.12"`): it comes after
`.python-version` and before the `requires-python` rule, and one the project cannot build with
(unsupported, or outside `requires-python`) is a warning, and the rule decides.

The patch release is the one that fsbpyr variant embeds: 3.12.16, 3.13.18, 3.14.10 or 3.15.2 in
0.28.0. When the chosen version is not the running fsbpyr's, the build is handed over. The same
`pandas-csv` project built without `--python`: `requires-python = ">=3.12"` picks 3.12, so the
3.14 fsbpyr fetches its release's 3.12 variant once and runs it:

```console
$ fsbpyr build
fsbpyr: fetching fsbpyr-3.12-x86_64-linux-gnu of release v0.28.0
fsbpyr: the project builds with Python 3.12: running ~/.cache/fsbpyr/downloads/fsbpyr-3.12-x86_64-linux-gnu-v0.28.0
fsbpyr: staging corpus-pandas-csv from uv.lock
...
fsbpyr: built .../pandas-csv/dist/corpus-pandas-csv (64.3 MB, entry corpus_pandas_csv.cli:main, glibc >= 2.34)
```

The variant is checked as `fsbpyr self update` checks a download before anything runs it, and
kept under `~/.cache/fsbpyr/downloads/` (`$FSBPYR_CACHE_DIR/downloads/` when that is set), so a
later build uses it without a download, offline too. With `--offline`, a build that needs a
variant not yet in the cache stops instead of fetching it: `this project builds with Python
3.12, which needs fsbpyr-3.12-x86_64-linux-gnu of release v0.28.0; it is not in the cache and
--offline fetches nothing: run the build once without --offline`. `fsbpyr cache gc` leaves the
variant alone; delete it by hand to reclaim the space. A developer build never downloads a
variant: it says how to build one from a checkout.

**Python 3.15.** fsbpyr builds for 3.15, but the installer's default is 3.14, and 3.14 is the one
to use for now: some widely used packages publish no 3.15 wheel yet, or none for the glibc 2.34
baseline. The release's tests do not run what needs one of them on 3.15: onnxruntime, PyYAML,
orjson, gnureadline, h5py and spaCy among others
([`tests/support/no-wheels.txt`](../tests/support/no-wheels.txt)
keeps the list). The build prints `built on this host from sdists, not reproducible elsewhere:`
and the names of every distribution it had to build from source.

## Locks from Poetry, PDM and Pipenv

A `poetry.lock` (any lock-version), a `pdm.lock` or a `Pipfile.lock` locks a
build like `uv.lock` does. fsbpyr reads the lock, converts it in its staging directory into a
requirements file for uv (nothing is written into the project), and installs:

- from `poetry.lock`, the main group; from `pdm.lock`, the default group; from `Pipfile.lock`,
  the `default` section, never `develop`;
- with markers evaluated for the app's Python and platform, every hash the lock lists for a
  package, and `--require-hashes` when every package has one;
- `--extra NAME` adds a Poetry `[extras]` entry or a PDM group of that name (an extra the lock
  lacks is an error); a `Pipfile.lock` is complete, so `--extra` adds nothing to it.

A path dependency installs as a regular package, a VCS entry keeps its commit and a URL entry its
hash. The build refuses, naming the entry: a package from another package index in a Poetry
lock (its credentials are not in the lock: pass a requirements file with `--requirements`), a
VCS entry without a commit, a missing path, and a package locked at two versions that both apply.
A `Pipfile.lock` entry from another index installs from the indexes uv is configured with, the
lock's hashes deciding whether uv found the same files; the build says so. A lock that is out of
date, whose hash (Poetry's `content-hash`, PDM's `content_hash`, Pipenv's `_meta.hash`) does not
match its project file, is a warning, and an error with `--strict`. Poetry 1.5 to 1.8 locks
record no groups: fsbpyr starts from the project's dependencies and follows the lock from there,
so dev-only packages stay out. A PDM lock needs a group per package (the `inherit_metadata`
strategy) or only the default group.

The project fixture [`foreign/poetry2`](../tests/fixtures/projects/foreign/poetry2)
(Poetry 2, `package-mode = false`, a `main.py` that prints which distributions are installed)
built from its `poetry.lock`:

```console
$ fsbpyr build --python 3.14
fsbpyr: staging foreign-poetry from poetry.lock
fsbpyr: collecting 6 distributions
...
fsbpyr: built .../poetry2/dist/foreign-poetry (32.1 MB, entry _fsbpyr_scripts.main, glibc >= 2.34)
$ dist/foreign-poetry
click 8.5.0
rich 15.0.0
markdown-it-py 4.2.0
mdurl 0.1.2
pygments 2.21.0
colorama absent
packaging absent
iniconfig absent
```

colorama is locked for Windows only, packaging only in the extra `fancy`, and iniconfig only in
the dev group, so none of them is installed. `fsbpyr inspect` shows the lock as
`poetry.lock sha256:...`, `pdm.lock sha256:...` or `Pipfile.lock sha256:...`. The
[`foreign/pipenv`](../tests/fixtures/projects/foreign/pipenv)
fixture, the same program with a `Pipfile` and `Pipfile.lock` and no `pyproject.toml`, builds
with Python 3.12 because its lock says `"python_version": "3.12"`.

## Several commands in one executable

A project with several console scripts and no `--entry` builds **one** executable that holds all
of them. The test fixture [`multi`](../tests/fixtures/projects/multi)
declares two:

```toml
[project.scripts]
multi-alpha = "multi.cli:alpha"
multi-beta = "multi.cli:beta"
```

```console
$ fsbpyr build --python 3.14
fsbpyr: staging multi from uv.lock
...
fsbpyr: built .../multi/dist/multi (27.9 MB, 2 commands, glibc >= 2.34)
fsbpyr: commands: multi-alpha, multi-beta; start one through its link, or as multi COMMAND
fsbpyr: linked .../multi/dist/multi-alpha -> multi
fsbpyr: linked .../multi/dist/multi-beta -> multi
fsbpyr: target needs: glibc >= 2.34
$ ls -l dist
-rwxr-xr-x  multi
lrwxrwxrwx  multi-alpha -> multi
lrwxrwxrwx  multi-beta -> multi
```

The executable is named after the project (or the `-o` path), and each command gets a relative
symlink beside it. A start runs the command its name says; under any other name, the first
argument names the command, and with none the executable lists its commands and exits 2
(each command prints its name and `sys.argv`):

```console
$ dist/multi-alpha x --y
alpha ['multi-alpha', 'x', '--y']
$ dist/multi multi-beta --flag
beta ['multi-beta', '--flag']
$ dist/multi
usage: multi COMMAND [ARGUMENT...]
commands:
  multi-alpha
  multi-beta
$ echo $?
2
```

- Install it by copying the executable and linking it under each command's name where the
  commands belong (`ln -s multi /usr/local/bin/multi-beta`), or by copying the whole `dist/`
  directory. On the target the copy can make the links itself:
  `FSBPYR_INSTALL_LINKS=DIR executable` ([Running](User-Running.md#making-the-command-links-on-the-target)).
- A command name is letters, digits and `_.+-`, starting with a letter or digit; another name
  fails the build. A start under `name.py` runs the command `name`, so a link that keeps a
  script's old file name works.
- Only the exact name counts as the first argument: `multi /path/to/multi-beta` is not a
  command.
- A link replaces a symlink, and the executable an earlier build of the same project wrote there
  for that one command (an fsbpyr executable of that name whose build information names the
  project), so a project that goes from one command to several replaces its own earlier output.
  Any other file, or a directory, where a link goes fails the build before anything is written.
- A project that goes back to one command (a script removed or skipped, or `--entry` naming one
  of them) replaces its own link at the output path with the executable, and leaves
  `dist/<project>` and the other links as they are: they run the earlier build until you remove
  them. A link of your own at the output path is written through.
- Hard links and copies work too, but a rebuild leaves them on the old build. Prefer symlinks.
- `--entry multi-beta` builds that one command alone, as a plain executable.
- `fsbpyr inspect` shows the commands under `commands`, one per line, instead of `entry`.
- All links of one executable share one instance limit (`FSBPYR_MAX_INSTANCES`,
  [Running](User-Running.md#environment-variables)).

## A project without console scripts

With no console script and no `--entry`, the build runs the project's one candidate: a
top-level package that has a `__main__.py` (as `python -m pkg` would), or a top-level module with
an `if __name__ == "__main__":` guard (found by reading the source, not by importing it), and
says which one it chose and why. With several candidates, of either kind, it lists them and asks
for `--entry`.

**Legacy scripts.** Files a `setup.py` installs with `scripts=` (or setuptools' `script-files`)
are commands too when the wheel holds them with the first line `#!python` (setuptools writes that
for a file that starts with a python shebang). Each is a command named after the file, hyphens
kept, beside the console scripts. Such a script runs as `python script` runs it: as `__main__`,
with its own directory first on `sys.path`; its `__file__` is next to the executable, as for the
scripts of a directory ([Scripts and tools](User-Scripts.md#files-next-to-a-script)). A legacy
script that is not Python (a shell script) is not packaged, and the build warns, naming it.

## Options

| Option | `[tool.fsbpyr]` key | What it does |
|---|---|---|
| `--signing-key KEY` | — | sign the output with this `.key` file after the smoke test, writing `OUTPUT.sig` (default: `FSBPYR_SIGNING_KEY`, else unsigned); a key that cannot be used gives a warning and an unsigned build ([Signing](User-Signing.md)) |
| `--python X.Y` | `python` | the Python to build for, 3.12 to 3.15, inside `requires-python` ([above](#which-python)) |
| `--glibc X.Y` | `glibc` | the glibc the app is built for: `2.34` (the default) or `2.28`, for EL8 hosts ([below](#building-for-el8-glibc-228)) |
| `--entry NAME` | `entry` | a console script name, `module` or `module:function` (default: the only script, else all of them as one executable, else the inferred `__main__` package or guarded module) |
| `-o, --output PATH` | `output` | where to write the executable (default: `dist/` and the entry's name, or the project's name for several commands) |
| `--requirements FILE` | `requirements` | a pinned requirements file, for pip projects without `pylock.toml`; overrides `uv.lock`; in a directory with no Python file it names the tools to build ([Scripts and tools](User-Scripts.md#a-tool-from-its-requirements-file)) |
| `--extra NAME` (repeat) | `extras` | install a project extra; requirements and pylock files must already contain its dependencies |
| `--fs-package PACKAGE` (repeat) | `fs-packages` | a package whose files must exist on disk ([Settings](User-Settings.md#fs-packages)) |
| `--allow-system-lib SONAME` (repeat) | `allow-system-libs` | let native code use this library from the target machine ([Settings](User-Settings.md#allow-system-libs)) |
| `--offline / --no-offline` | — | no network access during the build, for uv and for fsbpyr's own downloads ([below](#--offline)) |
| `--source / --no-source` | `include-source` | keep the `.py` sources (default) or leave them out ([below](#leaving-out-sources)) |
| `--fast-compression / --no-fast-compression` | `fast-compression` | zstd level 3 instead of 9: a faster build, up to about 9% larger for large apps |
| `--best-compression / --no-best-compression` | `best-compression` | native files as xz -6 instead of zstd 9: smaller (about 11% for an app with numpy and onnxruntime), slower build and slower first run; not with `--fast-compression` |
| `--hints / --no-hints` | — | also list packages that may read files next to `__file__`, and system libraries the code may open by name (guesses) |
| `--interpreter-reexec / --no-interpreter-reexec` | `interpreter-reexec` | how the app's own children started as Python (`sys.executable -m ...`) are treated ([Settings](User-Settings.md#interpreter-reexec)) |
| `--smoke / --no-smoke` | — | run the new executable once as a smoke test (default) or skip it |
| `--smoke-args ARGS` | `smoke-args` | also run the new executable with ARGS and fail the build unless it exits 0; with several commands the first word is the command |
| `--strict / --no-strict` | `strict` | fail the build on any warning about the app and on an unlocked build |
| `-v, --verbose` | — | print each uv command and let uv's error output through |

The size figures come from the project's measurements in [Performance](Dev-Performance.md)
(`--fast-compression` with 0.14.0 and 0.15.0 on a PyTorch app, `--best-compression` with
0.20.0).

### `--entry`

Picks the entry point: a script name, a module, or `module:function`. With several console
scripts, it builds the one it names alone.

```console
$ fsbpyr build --python 3.14 --entry corpus_pandas_csv.cli:main -o dist/by-entry
...
fsbpyr: built .../pandas-csv/dist/by-entry (65.1 MB, entry corpus_pandas_csv.cli:main, glibc >= 2.34)
```

Shell completion offers the project's console scripts after `--entry` and its extras after
`--extra`.

### `--smoke-args`

The default smoke test only imports the entry point and checks it. That import runs the entry
module's top-level code on the build machine, for a console script, a `module` or
`module:function` entry and an inferred guarded module alike; a script that runs as `__main__`
(of a project without a build system, a directory or a single script) is only found, never
imported. `--smoke-args`
also runs the app once, as it will be run (with `argv[0]` the final path, stdin from
`/dev/null`, a 120 s limit), and fails the build unless it exits 0. Only you know which arguments
have no side effects, such as `--version`. For an executable of several commands the first word
is the command: `--smoke-args "multi-beta --version"`. With an argument the app rejects, the build
fails and the file at `-o` stays as it was:

```console
$ fsbpyr build --python 3.14 --smoke-args no-such-file.csv -o dist/pandas-csv-smoke2
...
fsbpyr: running the new executable with no-such-file.csv
fsbpyr: error: smoke run of the new .../dist/pandas-csv-smoke2 with arguments 'no-such-file.csv' failed (exit 1):
Traceback (most recent call last):
  ...
FileNotFoundError: [Errno 2] No such file or directory: 'no-such-file.csv'
$ echo $?
1
```

### `--strict`

Without it, warnings leave the exit status at 0. With it, a warning that counts stops the build
before it writes the executable (a file at `-o` stays as it was), and the build exits 1 with the
warnings that count in the message. The "unlocked build" warning counts, so a project without a
lock fails. An out-of-date Poetry, PDM or Pipenv lock counts, and so do the commands the static
import check of scripts skips ([Scripts and tools](User-Scripts.md#imports-are-checked)).

With `--strict` a requirements file must pin (`==`) and hash (`--hash`) every line. A line pinned
by a commit (`name @ git+URL@<40 hex>`) or a `#sha256=` URL counts as pinned for the warning, but
a commit is no hash: `--strict`, or a requirements file that has `--hash=` lines, refuses such a
VCS line before uv runs, naming the file and the line.

Eight kinds of warning do not count. Three describe the build machine: a uv version different
from the one that built fsbpyr, git errors, and a `Pipfile.lock` package that uv installs from
its own configured indexes. Four are guesses about the app: that a dependency may start helper
interpreters, that a package may read files next to its `__file__` (the `--hints` list), that a
package's Python code names a library no extension module loads, and that a package ships an
executable file. One is advice: a project with `requirements/*.txt` and no `requirements.txt` is
told to pick a file with `--requirements`. The reads next to a script's `__file__` that the
build finds in its own code count ([Scripts and tools](User-Scripts.md#files-next-to-a-script)).

A native file that needs a newer glibc than 2.34 is a warning that counts
([below](#native-code-and-glibc)), so `--strict` fails on it. The glibc an executable needs is also
in `fsbpyr inspect --json`, to check it after the build (`jq -e` exits 1 on `false`):

```sh
fsbpyr build -o dist/mytool --strict
fsbpyr inspect --json dist/mytool | jq -e '.glibc | split(".") | map(tonumber) <= [2, 34]'
```

### Leaving out sources

By default an executable holds every module twice: as compiled bytecode, which is what runs,
and as its `.py` source, which is only read when something asks for it. `--no-source` leaves the
sources of the app and its dependencies out. For `pandas-csv` the file shrinks from 65.1 MB to
59.3 MB (measured here with 0.28.0). What changes:

- tracebacks name the file and line but show no source line;
- `inspect.getsource()` raises `OSError: could not get source code`, which breaks libraries that
  read the source of your functions at run time (`torch.jit.script`, for example);
- reading a `.py` file as data (through `importlib.resources`, or a package in `fs-packages`
  that expects its `.py` files on disk) fails with `FileNotFoundError`.

The standard library keeps its sources either way. Test a `--no-source` build before shipping
it, including a failing path.

### `--offline`

Warm uv's cache with a build that has network access, then build with `--offline`: every uv
command the build runs gets `--offline`. The build passes its environment through to uv, so
uv's own `UV_*` settings apply. fsbpyr's own downloads are off too: the fsbpyr of another Python
must be in the cache already, and the stub of a `--glibc 2.28` build placed next to fsbpyr or in
the cache. When one is missing, the build stops and says what to do: place the stub (naming it
and where), or run the build once without `--offline`.

## Requirements files

A requirements file that does not pin every line with `==`, a commit or a `#sha256=` URL gets a
warning that the build is not reproducible, naming up to three such lines. An `-e ./libs/common`
line installs as a regular package, and the same warning names it, since it installs whatever the
tree holds. A build with no lock at all, but `requirements/*.txt`, says to pick one with
`--requirements`.

## Compile warnings

A `SyntaxWarning` or `DeprecationWarning` that compiling the project's own modules emits (an
invalid escape such as `"\d"` in a normal string, say) is a build warning, with its file and
line: `fsbpyr: warning: pkg/mod.py:12: SyntaxWarning: ...`. Dependencies and the standard library
stay silent. `--strict` counts it. The bytecode is the same either way.

## Native code and glibc

uv picks wheels for glibc 2.34 (`manylinux_2_34`). A dependency without one is built from its
source distribution with the build machine's C compiler. When that result needs a newer glibc
than 2.34, fsbpyr builds it again against the Debian 12 sysroot (glibc 2.36) it links the
launcher with, and warns about what still needs more than 2.34. A C++ extension cannot be
rebuilt that way (the sysroot has no libstdc++), and neither can anything in an offline build:
then the build machine's result is packaged and the build warns which glibc it needs.

A build also warns when a native file needs a `GLIBCXX`, `CXXABI`, `GCC` or `ZLIB` version newer
than `manylinux_2_34` systems provide, since it would fail to import on RHEL 9 or Ubuntu 22.04.

An extension module, or a bundled library it loads, that needs a library which is neither
bundled nor a standard system library stops the build, which prints the setting that allows it;
for a library no extension module loads, it is a warning. See
[`allow-system-libs`](User-Settings.md#allow-system-libs).

## Building for EL8 (glibc 2.28)

An app built the usual way needs glibc 2.34 or newer. `--glibc 2.28` (or `glibc = "2.28"` in
`[tool.fsbpyr]`; the command line wins) builds it for glibc 2.28 instead: RHEL 8 and its rebuilds,
Rocky Linux 8 and AlmaLinux 8. Any other value stops the build and names the two it takes. It
works for every kind of build: a project, a project without a build system, a directory of
scripts (`glibc` in its settings-only `pyproject.toml`) and a single script (in its PEP 723
block). For 2.28 the build differs in four places:

- **The launcher.** The app's stub is linked against Rocky Linux 8.10's glibc 2.28, libgcc 8.5
  and kernel 4.18 headers instead of Debian 12's sysroot. The standard library and the boot code
  are the same bytes as in a default build.
- **The wheels.** uv picks wheels for `manylinux_2_28`. A dependency without one is built from
  its source distribution, and native code that then needs more than glibc 2.28 is built again
  against the EL8 sysroot, with the limits [above](#native-code-and-glibc): no C++ extension,
  nothing in an offline build.
- **The checks.** The warnings about `GLIBCXX`, `CXXABI`, `GCC` and `ZLIB` versions use the limits
  of `manylinux_2_28` (libstdc++ 8), and name RHEL 8.
- **The record.** The build information records `glibc_baseline = "2.28"`, and `fsbpyr inspect`
  shows it ([Inspecting](User-Inspecting.md#the-summary)).

**Where the stub comes from.** A released fsbpyr carries its standard library, but the stub for
glibc 2.28 is published apart, in the companion release `el8-vX.Y.Z` of its own release
(`el8-v0.28.0` for 0.28.0): one `fsbpyr-stub-3.X-<target>-glibc2.28.xz` per Python and target,
and `SHA256SUMS-glibc2.28`. The build takes the stub, in this order:

1. from a file placed next to the fsbpyr executable (next to the path it was run by, or next to
   the file a link to it leads to), named like the asset with or without `.xz`
   (`fsbpyr-stub-3.14-x86_64-linux-gnu-glibc2.28`), for a machine without access to GitHub;
2. from its cache: `downloads/` in fsbpyr's cache directory (`$FSBPYR_CACHE_DIR`, else
   `${XDG_CACHE_HOME:-~/.cache}/fsbpyr`), where a download is kept under the asset's name and
   the runtime id it carries (`<asset>-<runtime id>`);
3. from the companion release: downloaded, unpacked, checked against the SHA-256 that
   `SHA256SUMS-glibc2.28` lists for it, and kept in the cache, whole or not at all. With
   `--offline` nothing is downloaded: the build stops and names the stub to place and where.

A stub records the runtime id it was linked as. The id is computed from the launcher's sources
and compiler flags, the pinned CPython build, the target, the sysroot and the runtime layout.
The build refuses a placed or downloaded stub whose id is not the one this fsbpyr computes for
its own, and downloads again in place of a cached one with another id: a stub for another
Python or target, or linked from other launcher sources, never goes into an app. The id does
not name the release, so the stub of an earlier release whose launcher and pins are the same is
accepted (0.26.0's and 0.27.0's are), and an update that leaves the stub unchanged reuses the
cached one. 0.28.0 changed the launcher, the pinned CPython builds and the runtime layout, so
its stubs are new. A placed file is accepted on that id, not on its digest, so check it against
`SHA256SUMS-glibc2.28` before you place it (`xz -dc <asset>.xz | sha256sum`: the sums are of the
unpacked stub).

A build for another Python is handed to that Python's fsbpyr, which takes the stub for its own
Python. That fsbpyr runs from the cache's `downloads/`, but looks for a placed stub first next
to the fsbpyr that handed the build to it, so the stubs of every Python go next to the fsbpyr
you run, and its messages name that place. A developer build (an fsbpyr run from a checkout or a
wheel, or an executable built from an untagged commit) never downloads a stub: it uses the
runtime that `fsbpyr runtime build --glibc 2.28` made
([Other commands](User-Other-Commands.md#fsbpyr-runtime-list-and-fsbpyr-runtime-build)).

Not every release has a companion release. Without one the build stops before staging; with
fsbpyr 0.27.0, before `el8-v0.27.0` was published:

```console
$ fsbpyr build --glibc 2.28
fsbpyr: error: release el8-v0.27.0 (the glibc 2.28 stubs of v0.27.0) does not exist or has none for this Python: EL8 stubs are published separately and not for every release; place fsbpyr-stub-3.14-x86_64-linux-gnu-glibc2.28 (or fsbpyr-stub-3.14-x86_64-linux-gnu-glibc2.28.xz) next to .../fsbpyr, or use a release that has them: https://github.com/devitops-com/fsbpyr/releases
```

**An example.** The minimal `hello` project of [What a project needs](#what-a-project-needs),
built for EL8 by the released fsbpyr 0.28.0, which fetches the stub on its own; the build output
says nothing about it, and the stub is then kept in the cache:

```console
$ fsbpyr build --glibc 2.28
...
fsbpyr: built .../hello/dist/hello (27.9 MB, entry hello.cli:main, glibc >= 2.28)
fsbpyr: target needs: glibc >= 2.28
$ ls ~/.cache/fsbpyr/downloads/
fsbpyr-stub-3.14-x86_64-linux-gnu-glibc2.28-cp314-x86_64-unknown-linux-gnu-c63bc5403081  ...
$ sha256sum ~/.cache/fsbpyr/downloads/fsbpyr-stub-3.14-x86_64-linux-gnu-glibc2.28-*
1575eff341a86bc8ba8278ddd74ed7686500a613ebeff5f883e8f4fbc63e28bb  ...
```

That is the sum `SHA256SUMS-glibc2.28` of `el8-v0.28.0` lists for the stub. Without network
access the same stub can be placed next to fsbpyr; `pandas-csv` built that way:

```console
$ ls .../fsbpyr-el8
fsbpyr  fsbpyr-stub-3.14-x86_64-linux-gnu-glibc2.28.xz
$ .../fsbpyr-el8/fsbpyr build --python 3.14 --glibc 2.28
fsbpyr: staging corpus-pandas-csv from uv.lock
fsbpyr: collecting 5 distributions
...
fsbpyr: built .../pandas-csv/dist/corpus-pandas-csv (65.1 MB, entry corpus_pandas_csv.cli:main, glibc >= 2.28)
fsbpyr: target needs: glibc >= 2.28; libgcc_s.so.1 (GCC_4.3.0); libstdc++.so.6 (CXXABI_1.3.9, GLIBCXX_3.4.21); libz.so.1
$ .../fsbpyr-el8/fsbpyr inspect dist/corpus-pandas-csv | sed -n 3,4p
python     3.14.10, glibc >= 2.28
baseline   glibc 2.28
```

Run in a `rockylinux:8` container (`ldd --version` says glibc 2.28), it gives the same output as
on the build machine; the same app built the default way does not start there:

```console
$ dist/corpus-pandas-csv work/sales.csv
         sum  mean  count
region
east     1.5  1.50      1
...
$ dist/default work/sales.csv
dist/default: /lib64/libm.so.6: version `GLIBC_2.29' not found (required by dist/default)
dist/default: /lib64/libc.so.6: version `GLIBC_2.32' not found (required by dist/default)
dist/default: /lib64/libc.so.6: version `GLIBC_2.33' not found (required by dist/default)
dist/default: /lib64/libc.so.6: version `GLIBC_2.34' not found (required by dist/default)
```

The same held in `almalinux:8`, and for the `hello` app built above.

A file placed under the right name but for another Python is refused before the build starts:

```console
$ fsbpyr build --glibc 2.28
fsbpyr: error: .../fsbpyr-stub-3.14-x86_64-linux-gnu-glibc2.28.xz is not the stub this fsbpyr (v0.28.0) needs: it is the stub of runtime cp312-x86_64-unknown-linux-gnu-a722edaf960d, and this fsbpyr expects cp314-x86_64-unknown-linux-gnu-c63bc5403081; use the fsbpyr-stub-3.14-x86_64-linux-gnu-glibc2.28 of release el8-v0.28.0
```
