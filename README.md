# fsbpyr

Fast single-binary Python CLI runtime: `fsbpyr build` turns a Python project (uv or pip) into one
executable that carries CPython (3.12 to 3.15), the project and its locked dependencies. The executable
needs nothing on the target machine but Linux x86-64 or aarch64 with glibc 2.34 or newer (the
baseline every build targets and the tests run on: RHEL 9, Amazon Linux 2023, Ubuntu 22.04,
Debian 12 and newer, SLES 15 SP6 and newer; `fsbpyr inspect` shows the glibc an executable
actually needs; `--glibc 2.28` builds for RHEL 8 and its rebuilds, see [Older
systems](#older-systems-el8-and-glibc-228)), plus the system libraries its native dependencies link
(often libstdc++ and libgcc_s; `fsbpyr inspect` lists them): no Python, no virtual
environment, no installation step.
Modules are imported straight from the file, so startup stays close to plain CPython.
Some packages need a setting or two; [Does my app need settings?](#does-my-app-need-settings)
says which, for the 65 applications fsbpyr is tested on and for packages it has not seen.

## Quickstart

<!-- quickstart: tests/e2e/test_build.py runs these sh blocks -->
```sh
curl -LsSf https://github.com/devitops-com/fsbpyr/releases/latest/download/install.sh | sh
```

The installer picks the build for this machine and the default Python, 3.14 (see
[Getting fsbpyr](#getting-fsbpyr)) and puts it in `~/.local/bin` (unless `$XDG_BIN_HOME` says
otherwise).

Then, in a project whose `pyproject.toml` names a console script, such as this one for a
`main()` in `src/hello/cli.py`:

```toml
[project]
name = "hello"
version = "1.0.0"
requires-python = ">=3.14"

[project.scripts]
hello = "hello.cli:main"
```

```sh
~/.local/bin/fsbpyr build                 # writes dist/hello
dist/hello
~/.local/bin/fsbpyr inspect dist/hello
```
<!-- /quickstart -->

These lines call fsbpyr by its path: a new `~/.local/bin` is on `PATH` only from the next
login (Debian's and Ubuntu's `~/.profile` add it when it exists). The compressed download, the
attestation check and building fsbpyr from source are in [Getting fsbpyr](#getting-fsbpyr).

## Does my app need settings?

Many apps build and run with no settings. Some packages need a line in `[tool.fsbpyr]`: they
read their own files at paths built from `__file__` (`fs-packages`), link libraries that no
wheel bundles (`allow-system-libs`), or start helper Python processes (`interpreter-reexec`).
**Look your packages up in [docs/compatibility.md](docs/compatibility.md#tested-on-real-applications)
first:** it lists the 65 real applications fsbpyr is tested on, from AI research and science to
web services, what each one covers and the exact settings it needs. On Python 3.14, 35 of the
64 that run there need none; most of the others name one or two packages in `fs-packages`.

For a package that is not listed, build the app, run its main commands, and follow what fsbpyr
says:

- An uncaught file error on a path inside the executable (usually `NotADirectoryError`) ends
  with `fsbpyr: <path> is inside the executable; <package> reads files next to its __file__:
  rebuild with --fs-package <package>`. For a module that is no package, a namespace directory
  and the files of a script, the note says that `--fs-package` does not apply, and to read the
  file from a path outside the executable. The note is missing when the app catches the error or
  installs its own `sys.excepthook` (typer and rich do), and a package that only checks whether
  its files exist sees `False` and may fail later in another way. `fsbpyr build --hints` lists
  packages that may read files next to `__file__` (guesses).
- A build that stops on `native files need libraries that are neither bundled nor standard
  system libraries` prints the `allow-system-libs` line that lets the target provide them.
- A child process that exits 70 with `fsbpyr: refusing to run ... as Python` asks for
  `interpreter-reexec` ([Helper interpreters](#helper-interpreters)).

The table in [docs/compatibility.md](docs/compatibility.md#differs-from-a-venv) explains each
symptom and its remedy.

## Requirements

- **Build host:** Linux x86-64, or Linux aarch64 with glibc (see "Supported targets"), with [uv](https://docs.astral.sh/uv/) on `PATH`
  ([D26](docs/IMPLEMENTATION_PLAN.md#2-key-decisions)): uv installs every project, pip
  projects too.
- **Project:** a console script (`[project.scripts]`, or the build backend's own table such as
  setuptools' `entry_points`), or `--entry` with a script's name, a module or
  `module:function`. With no console script and no `--entry`, the build runs the project's only
  package with a `__main__.py` (as `python -m`), else its only top-level module with an
  `if __name__ == "__main__":` guard, and says which; with several candidates it lists them and
  asks for `--entry`. With several console scripts and no `--entry`, the build makes one
  executable with all of them (see "Several commands in one executable"). A project with a
  `[project]` table and no `[build-system]` builds from its Python files (see "Projects without a
  build system"), and so does a directory of scripts with no project file at all ("A directory of
  scripts"); `fsbpyr build FILE` builds one script ("A single script"). A directory with no Python
  file and a requirements file builds the tools the file names ("A tool from its requirements
  file"). Legacy `scripts=` files that start with `#!python` in the project's wheel are commands
  too ("Legacy scripts"); other legacy scripts are not packaged (the build warns). And a
  `requires-python`, if it sets one, that admits one of 3.12 to 3.15 (see "Which Python").
- **Lock sources,** the first one found: a requirements file that `--requirements` or
  `requirements` names, `uv.lock` (the project's or its uv workspace's), `pylock.toml`,
  `requirements.txt`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`. With none, the build resolves
  the dependencies itself and warns that it is not reproducible. From `uv.lock`, dependency
  groups (`dev` among them) are not packaged. See "Poetry and PDM locks" and "Pipfile.lock".

## Supported targets

- **Python:** apps run the CPython of the fsbpyr that built them: 3.12.15, 3.13.17, 3.14.9 or
  3.15.1, the version that fsbpyr embeds, which "Which Python" says how a build chooses. fsbpyr
  itself needs Python 3.12 to 3.15 (D13) and one checkout builds all four (`make build
  PYTHON=3.12`, see "Building from source"); 3.14 is the default (`.python-version`), which
  `make build` and the installer take, and the one to build apps with until the packages they
  need publish 3.15 wheels ("Python 3.15"). Each release carries all four, for every platform
  below.
- **Platform:** Linux x86-64 and Linux aarch64 (arm64), with glibc 2.34 or newer, or 2.28 or
  newer for apps built with `--glibc 2.28` ("Older systems"). Each fsbpyr builds apps
  for the platform it runs on; there are no cross builds. The aarch64 builds and tests run on
  GitHub's arm64 runners, which have 4K pages; kernels with 16K pages (Raspberry Pi 5's) and
  64K pages (EL8's) are smoke-tested in QEMU virtual machines (`make test-vm`), not on hardware.
  On a musl system such as Alpine the glibc loader the executable names does not exist, and the
  shell reports the executable as "not found" although the file is there.
- **Interpreter build:** CPython with the GIL only; the launcher refuses to build for a
  free-threaded CPython ([D35](docs/IMPLEMENTATION_PLAN.md#2-key-decisions)).
- **Planned** in [Phase 9](docs/IMPLEMENTATION_PLAN.md): musl and macOS arm64, put aside for
  now.

How an executable differs from a venv, what the target must provide, which distributions and
packages are tested, and what to do when an app fails: [docs/compatibility.md](docs/compatibility.md).

## Building an app

```
fsbpyr build [PROJECT_DIR | FILE] [-o OUT] [options]
```

The build host needs [uv](https://docs.astral.sh/uv/) on `PATH`, and network access the first
time (uv fetches the Python it builds with and the locked dependencies; later builds work from its cache, or
with `--offline`). git is optional: with it, the executable records the commit it was built
from. The output defaults to `dist/<console-script-name>` (`dist/<project name>` for several
commands, `dist/<directory name>` for a directory of scripts or a tool binary, `dist/<file stem>`
beside a single script). A directory of scripts or a single script is built with no project file at all; the sections
"Projects without a build system", "A directory of scripts" and "A single script" below say how.

uv picks wheels that run on glibc 2.34 (manylinux_2_34; manylinux_2_28 with `--glibc 2.28`). A dependency without one is built
from its sdist with the build host's C compiler, against the host's glibc. When such a build
needs a newer glibc than 2.34, fsbpyr builds it again against the Debian 12 sysroot (glibc 2.36)
it links the launcher with (downloaded once); uv's cached build of it is replaced. What that
build still needs above 2.34 (a function glibc 2.35 or 2.36 added) the build warns about.
If that build fails, as a C++ extension does (the sysroot has no libstdc++), or the build is
offline, the host's build is packaged and the build warns which glibc it needs.

Offline and air-gapped builds: warm uv's cache with a build that has network access, then build
with `--offline`. Build backends get their build constraints from `[tool.uv]
build-constraint-dependencies` (read by `uv sync`, for `uv.lock` projects) or from
`UV_BUILD_CONSTRAINT` (read by `uv pip install`, for requirements and `pylock.toml` sources; see
`uv pip install --help`). The build passes its environment through to uv.

| Option | |
|---|---|
| `--python X.Y` | the Python to build for, 3.12 to 3.15, inside `requires-python` (default: `python` in `[tool.fsbpyr]`, else `.python-version`, else `requires-python` decides) |
| `--glibc X.Y` | the glibc the app is built for: 2.34 (default), or 2.28 for EL8 hosts, which needs the glibc 2.28 stub of the fsbpyr release (default: `glibc` in `[tool.fsbpyr]`, else 2.34) |
| `--entry NAME` | console script name, `module` or `module:function` (default: the only script, else all of them as one multi-command executable, else the only `__main__` package or guarded module) |
| `-o, --output PATH` | where to write the executable (default: `dist/` and the entry's name, or the project's name for several commands) |
| `--requirements FILE` | pinned requirements file, for pip projects without `pylock.toml` |
| `--extra NAME` | install a project extra (repeat); requirements and pylock files must already contain its dependencies |
| `--fs-package PACKAGE` | a package whose files must exist on disk (repeat) |
| `--allow-system-lib SONAME` | let native code use this library from the host (repeat) |
| `--offline / --no-offline` | no network access during the build |
| `--source / --no-source` | keep the `.py` sources (default) or leave them out: smaller, but tracebacks show no source lines and `inspect.getsource()` fails |
| `--fast-compression / --no-fast-compression` | zstd level 3 instead of 9 (the standard library's files of 64 KiB and more stay level 9): a faster build, up to ~9% larger for large apps (measured with 0.14.0 and 0.15.0, docs/performance.md) |
| `--best-compression / --no-best-compression` | native files (extension modules and shared libraries, nothing else) as xz -6 instead of zstd 9: about 11% smaller for an app with numpy and onnxruntime (measured with 0.20.0, docs/performance.md), no change for pure-Python apps, a slower build, and a slower first run, which decodes them into the cache; not with `--fast-compression` |
| `--hints / --no-hints` | also list packages that may read files next to `__file__`, and system libraries the code may open by name (guesses) |
| `--interpreter-reexec / --no-interpreter-reexec` | let the app's own children started as `sys.executable -m MODULE`, `-c CODE` or with a script of the app run as Python, or (--no-...) run the app in them, for an app whose own options are named -m or -c; with neither, they are refused (exit 70) |
| `--smoke / --no-smoke` | run the new executable once as a smoke test (default) or skip it |
| `--smoke-args ARGS` | also run the new executable once with ARGS (one string, split like a shell would), for example "--version", and fail the build unless it exits 0; only you know which arguments have no side effects; with several commands the first word is the command ("acme-db --version") |
| `--strict / --no-strict` | fail the build, leaving the output as it was, on any warning about the app and on an unlocked build; requirements files must pin and hash every line |
| `-v, --verbose` | print each uv command and let uv's error output through |

`fsbpyr build -h` shows the same list. [Leaving out sources](#leaving-out-sources) explains
`--no-source`, [At run time](#at-run-time) `--fs-package` and the cache that
`--best-compression` decodes into, and [Helper interpreters](#helper-interpreters)
`--interpreter-reexec`. The sizes are measured: `--fast-compression`'s on a torch app
([docs/performance.md](docs/performance.md#the-same-app-with-0140-and-0150-d39)),
`--best-compression`'s on the [`heavy`](tests/fixtures/projects/heavy) test app
([docs/performance.md](docs/performance.md#build-options-0200)).

The same settings can live in the project's `pyproject.toml`; command-line options win, both
ways for on/off settings (`--source` keeps the sources of a project that sets
`include-source = false`), and a compression flag on the command line also replaces the other
one set in the project:

```toml
[tool.fsbpyr]
python = "3.13"              # same as --python
glibc = "2.28"               # same as --glibc (EL8; see "Older systems")
entry = "mytool"
output = "dist/mytool"
requirements = "requirements.txt"  # pip projects only; overrides uv.lock
extras = ["fancy"]
include-source = false       # same as --no-source
fast-compression = true      # same as --fast-compression
# best-compression = true    # same as --best-compression (not both)
fs-packages = ["mypkg.templates"]
allow-system-libs = ["libcuda.so.1"]
interpreter-reexec = true    # same as --interpreter-reexec
smoke-args = ["--version"]   # same as --smoke-args "--version"
strict = true                # same as --strict
```

`--smoke-args` is off unless given. The build then runs the new executable once more with
those arguments, as `-o` (so `argv[0]` is the final path), with stdin from `/dev/null` and
a 120 s limit, and fails unless it exits 0; the error names the arguments and shows the app's
stderr. Only you know which arguments are safe, such as `--version` or `--help`.

The smoke test imports the entry module. A `--entry module` build, an inferred guarded module
and a `module:function` entry therefore run that module's top-level code on the build host; the
scripts of a project without a build system are only found, never imported.

### Several commands in one executable

A project with several console scripts and no `--entry` builds **one** executable that holds all of
them, instead of asking for a choice. With `[project.scripts]` `acme-run = "acme.run:main"` and
`acme-db = "acme.db:main"`, `fsbpyr build` writes `dist/acme` (the project's name, or the `-o`
path) and, beside it, a relative symlink per command, `dist/acme-run -> acme` and
`dist/acme-db -> acme`. The build lists them. Names are letters, digits and `_.+-`, starting
with a letter or digit; another name fails the build, naming it. A command named like the
executable needs no link. `--entry acme-db` still builds that one command alone, as before. A
project with one command builds as before.

A start runs the command that its `argv[0]` names (the link, with `.py` stripped if it has it),
so copy or link the executable under each command's name where the commands belong, in `PATH`
for example (`ln -s acme /usr/local/bin/acme-db`, or copy the whole `dist/` directory). A start
under any other name takes the command from the first argument, `acme acme-db --flag`: the
command sees `sys.argv == ["acme-db", "--flag"]`, as through its link. Started with no command
or an unknown one, the executable lists its commands on stderr and exits 2. Only the exact
name counts: `acme /path/to/acme-db` is not a command.

Things to know:

- The build never replaces a file that is not a symlink: a regular file or a directory in the
  way of a link fails the build before anything is written; an existing symlink is replaced.
  With `-o`, the links go beside the output.
- Hard links and copies work as well, but a rebuild replaces the file and leaves them on the
  old build (see [docs/compatibility.md](docs/compatibility.md)). Prefer symlinks.
- `--smoke-args` runs the executable under its own name, so its first word is the command
  (`--smoke-args "acme-db --version"`); for a command named like the executable, all of the
  arguments are that command's.
- On the host the copy can make the links itself: `FSBPYR_INSTALL_LINKS=DIR executable` links each
  command into DIR and exits without running the app (see "Deploy" under "A directory of
  scripts").
- All links of an executable share one instance limit (`FSBPYR_MAX_INSTANCES`, "At run time").
- `fsbpyr inspect` shows the commands and what each runs.

### Legacy scripts

The files that a project's `setup.py` installs with `scripts=` (or setuptools' `script-files`)
are Python scripts when the wheel holds them with the first line `#!python`: setuptools writes
that for a file that starts with `#!/usr/bin/env python`, and an installer rewrites it to its
interpreter, so the build reads the wheel. Each is a command named after the file (hyphens
kept) beside the console scripts: with only legacy scripts the build makes the executable from
them, with several commands it makes the links of "Several commands in one executable", and a
name that is also a console script fails the build, naming both. A script runs as `python
script` runs it: as `__main__`, `argv[0]` the path it was started by, with its own directory first
on `sys.path`, and the project's package imports. The source is stored in the executable under a
module name (`check-disk` as `check_disk.py`, a name without `.py` that is an identifier, `tool`,
as `_x_tool.py`); as for the scripts of a directory, `__file__` is that name next to the
executable, and the files deployed beside the executable are the ones the script reads. A legacy script that does not start with `#!python` (a shell script)
is not packaged; the build warns, naming it.

### Poetry and PDM locks

A project with a `poetry.lock` (any lock-version) or a `pdm.lock` (lock_version 4) is a locked
build. fsbpyr reads the lock, never writes into the project, and gives uv a requirements
file made in its staging directory: the main (Poetry) or default (PDM) group, markers evaluated
for the Python and platform the app is built for, and every hash the lock lists for a package,
with `--require-hashes` when every package has one. Dev groups are not packaged. `--extra NAME`
adds the packages of the lock's extra (Poetry's `[extras]`, PDM's group of that name); an extra
the lock lacks is an error. They rank after `requirements.txt`: a requirements file beside
the lock, or `--requirements`, wins, and so does a `uv.lock`.

- **Poetry 1.5 to 1.8** (lock-version 2.0) record no group per package. fsbpyr starts from the
  pyproject's `[tool.poetry.dependencies]` (or `[project.dependencies]`) and follows the
  dependencies the lock records, so dev-only packages stay out. A `python =` constraint on a
  dependency must be a PEP 440 specifier (`<3.12`), not `^3.12`: the build says which dependency.
- **PDM** (lock_version 4) needs the group of each package: a lock made with the
  `inherit_metadata` strategy, or a lock that holds the default group only. Otherwise the build
  stops and says to lock again or to pass a requirements file.
- **Path, VCS and URL entries.** A path dependency is installed as a regular package, as for a
  requirements file, from the path the lock names (relative to the project). A VCS entry keeps
  its commit (`resolved_reference` or `revision`). A URL entry keeps its hash. These have no hash
  to require, so uv checks the hashes of the other packages and does not demand one of these.
- **Refused, naming the entry:** a package from another package index (the credentials are not
  in the lock; pass a requirements file with `--requirements`), a VCS entry that pins no commit, a
  path that does not exist, and a package locked at two versions that both apply to the target.
- **A lock older than the dependencies** (Poetry's `content-hash`, PDM's `content_hash` differ
  from the pyproject's) is a warning, and an error with `--strict`; the build still installs the
  lock as it is.

`fsbpyr inspect` shows the lock as `poetry.lock sha256:...` or `pdm.lock sha256:...`, and the JSON
`build_info` records the kind and, for each distribution, the hash of the locked wheel.

### Pipfile.lock

A `Pipfile.lock` is a locked build the same way, for a project and for a directory of scripts
(a legacy Pipenv app is usually a directory of code with no `pyproject.toml`; unlike
`poetry.lock` and `pdm.lock`, the lock is read there). fsbpyr installs the lock's `default`
section, never `develop`, with each entry's pinned version, every hash and its markers
evaluated for the app's Python and platform, and `--require-hashes` when every entry has
hashes. It ranks last of the lock sources (after `uv.lock`, `pylock.toml`, `requirements.txt`,
`poetry.lock` and `pdm.lock`): a requirements file beside it, or `--requirements`, wins. A lock
is complete, so `--extra` adds nothing.

- **The Python.** `_meta.requires` (`python_version = "3.12"`, or `python_full_version`) chooses
  the Python as a `requires-python` for that minor version would ("Which Python"). `--python`,
  `python` in `[tool.fsbpyr]` and a `.python-version` outrank it, with no error. A version
  fsbpyr cannot build for is a warning, and the rule goes on without it.
- **A lock older than the Pipfile** (`_meta.hash` is not the Pipfile's hash) is a warning, and
  an error with `--strict`; the build still installs the lock as it is. Without a Pipfile there
  is nothing to compare. The hash is computed as Pipenv's `plette` library does it; the formula
  was written from that code and has not been checked against a lock that Pipenv wrote.
- **Other indexes.** An entry whose `index` is a `[[source]]` that is not PyPI installs from the
  indexes uv is configured with (`UV_INDEX_URL`, `UV_EXTRA_INDEX_URL`, `uv.toml`), not from the
  lock's URL, which may name credentials by variable; the lock's hashes decide whether uv found
  the same files, and the build says which packages and hosts. `--strict` does not count the
  note.
- **Git, file and path entries.** A `git` entry keeps its `ref`, which must be a commit. A
  `file` entry keeps its hash. A `path` entry is installed as a regular package, `editable` or
  not, as for a requirements file; a path that does not exist, a `ref` that is no commit, a
  version that is not `==`, and `hg`, `svn` and `bzr` entries are refused, naming the entry. A
  `path` entry that is the project itself (`pipenv install -e .`) is not installed: fsbpyr
  installs the project's own wheel.
- **A tool binary.** A directory with a `Pipfile.lock` and no Python file builds the tools in
  the Pipfile's `[packages]` ("A tool from its requirements file"); without a Pipfile, a lock
  names its dependencies too, and the tools are the packages no other package of the lock
  needs.

`fsbpyr inspect` shows the lock as `Pipfile.lock sha256:...`. A Pipfile.lock's hashes have no file
names, so the JSON `build_info` has no `artifact_sha256` for its distributions.

### Projects without a build system

A project with a `[project]` table and no `[build-system]` is what `uv init --no-package` writes
(and plain `uv init` did from uv 0.4 to 0.11): `main.py` and its helpers, `pyproject.toml`,
`uv.lock`. It also covers Poetry's `package-mode = false` and PDM's `distribution = false` with a
`[project]` table; so does a Poetry 1 project with `package-mode = false` and no `[project]` table
(name, version, scripts and dependencies are read from `[tool.poetry]`). `fsbpyr build` builds it as `uv run main.py` runs it, and never uses
setuptools' legacy fallback. A uv workspace root (`[tool.uv.workspace]`) keeps its build.

- **What is packaged:** the project's Python files, and only those: `main.py`, `utils.py`, a
  `utils/` package, `src/` too (paths are kept, so a `src/` layout is not a package layout
  here). In a git work tree the files are the tracked ones, and the build warns for each
  untracked Python file that is not ignored, which it leaves out (`git add` it, or ignore it);
  elsewhere the files are those found by a walk that skips hidden directories, virtual environments,
  `__pycache__`, `build`, `dist`, `node_modules` and `site-packages`. A `.env`, a key, a data file
  or a template is never packaged, whatever git says. A link to a file outside the project
  directory fails the build; links to directories are not followed. A `.py` file below a directory
  whose name is no Python name (`sub-dir/c.py`) cannot be imported: it is not packaged and the
  build warns. The home directory and `/` are no project directories.
- **How it is built:** fsbpyr writes the project's wheel itself (the Python files under a
  reserved top-level directory `_fsbpyr_scripts/`), runs no project code and writes nothing into
  the source tree. Two builds of one tree are the same bytes. The dependencies come from
  `uv.lock` as for any project, or `poetry.lock` / `pdm.lock` / `Pipfile.lock` ("Poetry and PDM
  locks", "Pipfile.lock"); without a lock, `[project.dependencies]` (and the `--extra`s) are resolved now, with the warning for an
  unlocked build. Without `[project]`, a Poetry 1 project with `package-mode = false` reads
  `[tool.poetry.dependencies]` (the main group; `optional` ones go to the `[tool.poetry.extras]`
  that list them), with `^`, `~` and bare versions converted to PEP 440 specifiers; `||`
  alternatives and git, path and URL dependencies are refused, and its `poetry.lock` locks the
  build as for any project; a `.venv` is never read for
  undeclared dependencies (a directory of scripts and a single script do read one, below).
- **Commands:** `[project.scripts]` if present (`main:run` runs `_fsbpyr_scripts.main:run`),
  else the modules with an `if __name__ == "__main__":` guard (a `__main__.py` counts too). One
  is the command named like the project; several make a multi-command executable
  ("Several commands in one executable"), each named after its file, `main.py` and `__main__.py`
  after the project; test files (`tests/`, `test_*.py`, `conftest.py`) and nested `__main__.py`
  are not commands. Two files with one command name fail the build, naming both. A
  `[project.scripts]` target in `src/` reads `_fsbpyr_scripts.src.pkg.mod:main`. Without
  either the build asks for `--entry` (a command name or `_fsbpyr_scripts.module[:attr]`). A file
  with a name like `deploy-prod.py` is not packaged, and the build warns (a directory of scripts
  stores such a file under a module name instead).
- **At run time** a script runs as `__main__` with its directory first on `sys.path` and
  `sys.argv` as given, so `import utils` finds a sibling `utils.py` or `utils/`, as under
  `uv run`. `__file__` is the file's path below the executable's directory (`/opt/tools/main.py` for
  `main.py` of the project, with the executable in `/opt/tools`), so a file the script reads next
  to it is the host's, deployed beside the executable ("Files next to a script" below,
  [docs/compatibility.md](docs/compatibility.md)).
- **The smoke test only finds the scripts,** it does not import them: their top-level code does
  the work (deploy, delete, restart). For a `--entry module` build, an inferred guarded module
  and a `module:function` entry the smoke test imports the module, which runs its top-level code
  on the build host.
- **Imports are checked statically.** For each command the build follows the imports of the
  script and of the project's modules it reaches, and a required import that is not a project
  file, an installed distribution or in the standard library skips that command with a warning
  naming `file:line`, and `--strict` fails the build; with no command left the build fails. An
  import under `try`/`except ImportError` (or a bare `except`, or `except Exception`) and under
  `if TYPE_CHECKING:` or a test on `sys.platform`, `os.name` or `sys.version_info` is optional.
  With a `.venv` in the project the warning gives `uv pip freeze --python .venv/bin/python >
  requirements.txt`, which the build then reads. A standard
  library module removed in the build's Python (`cgi` in 3.13, `imp` in 3.12) is named as such,
  with the `--python` that still has it.

### A directory of scripts

`cd ops-scripts && fsbpyr build` also builds a directory with no project file: no `setup.py` or
`setup.cfg` (a `setup.cfg` alone is refused) and no `pyproject.toml`, or one that holds only tool
settings (see "Settings" below): a git repository of `.py` files and executable
scripts that Ansible copies to hosts with the wrong Python or none, a set of monitoring plugins,
a cron fleet. The result is one executable and a link per command, built as in "Projects
without a build system", named after the directory. It runs where "Supported targets" says:
RHEL, Oracle Linux, AlmaLinux and Rocky 9 and 10, Amazon Linux 2023, Ubuntu 22.04 and newer,
Debian 12 and newer, SLES 15 SP6 and newer and SLES 16, and EL8 (RHEL 8, Rocky Linux 8,
AlmaLinux 8) with `--glibc 2.28` ("Older systems: EL8 and glibc 2.28"). CentOS 7, Amazon Linux 2
and every musl system are out of reach, and Ubuntu 20.04, Debian 10 and 11 and SLES 15 SP4 and SP5
are not tested. Scripts for Python 2, scripts that import what Python removed (`cgi`, `imp`,
`telnetlib`; the build names the newest supported `--python` that still has the module, or says
the script needs porting when none does) and scripts that import
the system's `dnf`, `rpm`, `selinux` or `apt` bindings, which no executable carries, cannot be
bundled on any system.

- **What is packaged:** the Python files: `*.py`, and extensionless files whose first line is a
  python shebang (`#!/usr/bin/env python3`, `#!/usr/bin/python3`, `#!/usr/bin/env python`,
  `#!/usr/bin/env -S uv run --script`). A file with another shebang, a binary file, `.env`, a key,
  a template or a data file is never packaged: nothing but Python is taken, so no deny list is
  needed. In a git work tree the files are the tracked ones (`git ls-files`): an untracked
  Python file, such as a `secrets.py` nobody put in `.gitignore`, is **not** packaged, and the
  build warns for each, naming it, so that you `git add` it or ignore it; a work tree with
  nothing tracked has no files to package. Without git the files are those of a walk that skips
  hidden directories, virtual environments, `__pycache__`, `build`, `dist`, `node_modules` and
  `site-packages`, and then a file only a `.gitignore` would exclude is packaged: keep it out
  of the directory. A link to a `.py` file outside the directory fails
  the build and a link to any other file outside it is passed over; links to directories are
  not followed. The home directory and `/` are no project directories. A directory whose name
  is no Python name (`sub-dir/`, `2024-tools/`) is packaged too, under its mangled name (see
  "Names that are no Python names").
- **Commands:** a file with an `if __name__ == "__main__":` guard at module level or a python
  shebang is a command. The build reads the files and runs none of them. `conftest.py`,
  `test_*.py`, `*_test.py` and files below a top-level `tests/` or `test/` are packaged and are
  never commands. The command is the file's name without `.py`, hyphens kept (`check-disk.py` and
  an extensionless `check-disk` give `check-disk`); `main.py` and `__main__.py` take the
  directory's name. Two files with one name (`a.py` and `sub/a.py`, `tool` and `tool.py`) fail the
  build, naming both. A file whose name is no valid command name ("Several commands in one
  executable") stays a module, with a warning. One command makes `dist/<command>`; several make
  `dist/<directory name>` and a link per command.
- **Names that are no Python names:** `check-disk.py`, `10-backup` and an extensionless `check-disk`
  run as commands all the same. The build stores such a file in its own directory under the name
  that results from replacing each character no Python name can hold with `_`, and a `_` in front
  of a name that starts with a digit (`check_disk.py`, `_10_backup.py`); two files that give one
  name fail the build, naming both. An extensionless file with a Python name (`queue`) gets `_x_`
  in front (`_x_queue.py`), since `python3` never imports such a file and it must not shadow the
  standard library for its siblings. It runs as `__main__`, and its `__file__` is that path next to
  the executable. Files with Python names keep them, so `import util` finds the `util.py` next to
  the script. A directory whose name is no Python name (`sub-dir/`, `2024-tools/`) is stored under
  the name made the same way (`sub_dir/`, `_2024_tools/`), and so are the Python files below it:
  a script in it is a command and runs with that directory first on `sys.path`, so it imports its
  siblings, and `sys.path.append(os.path.join(os.path.dirname(__file__), "sub-dir"))` finds the
  modules in it, as for CPython. A script's `__file__` and `sys.path[0]` show the stored name
  (`.../sub_dir`). Two directories that give one name (`sub-dir` and `sub_dir`) fail the build,
  naming both.
- **Settings:** there is no project to hold them, so a `pyproject.toml` that holds tool settings
  and nothing else (no `[project]`, no `[build-system]`, no Poetry tables) beside the scripts is
  their settings file: `[tool.fsbpyr]` (`glibc = "2.28"`, `python`, `strict`, ...) is read as in any
  project, and the options of the command line win. It is not a project, so setuptools never
  sees it. A `[tool.fsbpyr]` table in a command's `# /// script` block is ignored in a directory
  build, with a warning that names the file: pass the option or use that `pyproject.toml`.
- **Dependencies:** only what the directory declares. `requirements.txt` (or `--requirements`) or
  `pylock.toml`, as for any pip project; else the union of the `# /// script` blocks (PEP 723) of
  the command files, resolved at build time, so the build warns that it is unlocked. The
  `requires-python` entries of the blocks are intersected, and a conflict between them fails the
  build, naming the files. A requirements file or `pylock.toml` replaces the blocks'
  dependencies. With none of them, the venv the scripts run in supplies pins for the imports
  nothing else provides ("The venv rung" below); without one the scripts get no packages.
- **Imports are checked** for each command as in "Projects without a build system": an import
  that nothing in the directory, the dependencies or the standard library resolves skips the
  command with a warning that names `file:line`, and with a `.venv` in the directory, the
  `uv pip freeze --python .venv/bin/python > requirements.txt` that records what it holds. `--strict` fails
  the build, and so does a build with no command left. An import under `try`/`except
  ImportError` gives no warning. A directory that a file adds to `sys.path` with a path built from
  its own `__file__` (`sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
  "pylib"))`) is searched; a `sys.path` change the check cannot follow makes the unresolved
  imports of the command optional. A `..` in such a path is followed as the executable follows it
  (`os.path.join(os.path.dirname(__file__), "..", "lib")` is the `lib/` beside the script's
  directory); a path that goes above the executable holds none of the directory's files.
- **The venv rung.** When nothing declares a dependency (no requirements file, `pylock.toml`
  or PEP 723 `dependencies`) and a command's import is unresolved, the build reads the venv the
  scripts run in: `.venv`, `venv` or `env` in the directory (with a `pyvenv.cfg`), else
  `$VIRTUAL_ENV`. It reads files only and never starts the venv's Python: each installed
  distribution's `METADATA`, `top_level.txt` and `RECORD` say which one provides the import (a
  PyPI name is never guessed from an import name). Those distributions, and the
  distributions their `Requires-Dist` need as installed, are pinned `name==version` and
  installed fresh by uv for the app's platform (`manylinux_2_34`, or 2.28 with `--glibc 2.28`);
  nothing is copied from the venv. The build warns that the dependencies came from the venv and
  prints the `uv pip freeze --python <venv>/bin/python > requirements.txt` that declares them;
  `--strict` fails on that warning. A pin that has no wheel for the target fails in uv, with
  uv's reason; an editable or path install that is needed is refused (it is your own tree); an
  import the venv does not provide stays unresolved, as above. `fsbpyr inspect` shows the lock
  as `inferred from <venv>` with the pins (`build_info.lock.kind` is `inferred`).
- **At run time** a link runs its script as `python3 script.py` would: as `__main__`, `argv` as
  given (`argv[0]` the full path of the start), the script's directory first on `sys.path`. A sibling named like a standard library
  module (`calendar.py`) wins over the library, as it does for CPython. The `pylib` hack above
  finds the modules of `pylib/`. A `..` in a `sys.path` entry that points into the executable is
  resolved inside it, and an entry that would climb above the executable is not served from it.
  A script that starts a sibling command as `subprocess.run([sys.executable, <sibling's
  __file__>])` gets that command (exit status and output as `python sibling.py` gives them, its
  directory first on `sys.path`) and is not refused as a helper interpreter ("Helper
  interpreters").
- **Files next to a script** are the host's, not the executable's. A script's `__file__` is where
  the script would be beside the executable (the executable's directory and the file's path in the
  project: `/opt/tools/sub/x.py` for `sub/x.py` with the executable in `/opt/tools`), so
  `open(os.path.join(os.path.dirname(__file__), "config.ini"))` reads the
  `config.ini` deployed beside the executable (config, state, logs), and the build tree's copy
  is never read. The code is still the executable's: a stale `check-disk.py` left beside the
  binary by an earlier copy deployment is never imported, 
  `sys.path.append(os.path.dirname(__file__) + "/pylib")` serves the executable's `pylib`, and a traceback shows the source lines of the
  build. A file or directory that is no Python name is deployed under its stored name
  (`sub_dir/`, `check_disk.py`). The executable holds the project's Python files only, so the data
  files must be put beside it: the build warns for each read next to `__file__` it finds in the
  scripts (`file:line`; a heuristic that knows `open`, `read_text`, `os.listdir` and the like, not
  every way to read), and an uncaught `FileNotFoundError` below the executable's directory says the
  file is read there. The script's own source is not a file there (`open(__file__)` fails unless a
  copy is deployed): use `importlib.resources` or the loader's `get_source`; `inspect.getsource`
  works too, but shows a copy deployed beside the executable instead of the payload's source. A
  package of the project (a project with a build system) is not affected: its `__file__` is below
  the executable, `--fs-package` applies.
- **Deploy:** the build prints how to install the result on a host, for example
  `install -m 755 dist/ops-scripts "$DEST"/ops-scripts` and `for c in check-disk check_mem; do ln -sf
  ops-scripts "$DEST/$c"; done`. A crontab, unit or agent line such as
  `python3 /opt/tools/check-disk.py` must become `/opt/tools/check-disk`: the link is an
  executable, not a Python file. Or let the copy link itself on the host: `FSBPYR_INSTALL_LINKS=DIR
  /opt/tools/ops-scripts` links each command into DIR, absolute to the executable's real path,
  and `check-disk.py` besides `check-disk` for each script command (also one that was no `.py` file), so those lines
  keep working (a start strips `.py`). It prints what it did and exits without running the app.
  A second run changes nothing; a file, a directory or a link to something else in the way is
  reported and kept, the other links are still made, and the exit status is 1; a DIR that is
  missing or not writable is an error before any link. There is no `--install` option:
  `sudo ops-scripts --install DIR` under a `<binary> *` sudo rule would let a caller create
  root-owned links anywhere. sudo's `env_reset` (on by default, see `sudoers(5)`) drops
  `FSBPYR_*`, so `sudo FSBPYR_INSTALL_LINKS=DIR` works only where `env_keep` or `SETENV` allows
  it.

#### Monitoring plugins and cron fleets

Nagios, Icinga, NRPE and Checkmk checks and cron jobs call a script by the name a command
definition holds, many times an hour. Give each link the name the definition uses: name the file
so, or link the command under that name. Keep the binary outside directories that an agent runs
file by file (`cron.daily`, a monitoring agent's directory of local checks): it would run the
binary itself as well. Put it in a directory of its own and link the commands where the agent
looks, with an absolute target (`ln -sf /opt/tools/ops-scripts /etc/cron.daily/check_disk`).

- All the links share **one instance budget** (D45), because it counts copies by the executable's
  file. The limit is 4 per CPU, at least 64 and at most 1024; a start beyond it exits 70, which
  Nagios shows as CRITICAL. `FSBPYR_MAX_INSTANCES` raises it (at most 4096), for a host that
  starts many checks at once.
- Every start checks the whole executable (D31, "Integrity"); the cost grows with the file's size,
  and one executable serves every command.

### A tool from its requirements file

`cd tools && fsbpyr build` in a directory with no Python file and a lock source builds the tools
the lock names: `requirements.txt` saying `ansible-core==2.17.*`, or `httpie==3.2.4`, or a
`pylock.toml`, or a `Pipfile.lock`, or a file given with `--requirements` (or `requirements` in `[tool.fsbpyr]` of a
`pyproject.toml` of settings). The directory is an empty project whose requirements are the tools;
the result is one executable, named after the directory, whose commands are the console scripts
of those tools, with a link per command and the deploy lines of "A directory of scripts". One
command makes `dist/<command>`. A directory that holds a Python file is a directory of scripts,
whatever its requirements say: its scripts are the commands, and the tools' scripts are not added.

- **Which tools:** the distributions the file names directly, one per requirement line (`name[extra]`,
  `name @ URL`, a URL with `#egg=name` or a wheel or sdist file name; `-r` files are followed,
  constraints (`-c`) and options name nothing, and a line that gives no name, such as a path, is
  refused: write `name @ URL`). A file that lists every installed distribution, as `pip freeze`,
  `uv pip freeze` and `pylock.toml` do, names the dependencies too, so there the tools are the
  listed distributions that no other listed distribution requires (by `Requires-Dist`, read from
  the staged site). A `Pipfile.lock` names its dependencies as well; with a `Pipfile` beside it,
  the tools are the Pipfile's `[packages]` instead ("Pipfile.lock"). The build says which it took and why, and the commands it found:
  `requirements.txt lists all 4 installed distributions, so the tools are the 1 that no other
  listed distribution requires: flake8`. In a file of tools alone every listed distribution is a
  tool, `requests` among them if you name it, though it has no command.
- **Commands:** the `console_scripts` and `gui_scripts` of the tools (pip and uv install both as
  ordinary launchers on Linux). A dependency's scripts are not commands
  (`ansible-core`'s are; its `jinja2` dependency's are not; name `jinja2` in a file of tools alone
  to have them). A tool with no script of its own falls through to its direct
  requirements (under the runtime's markers and the extras asked for) when it is a meta-package,
  a distribution that installs no code of its own (`jupyter`), or when the requirement is named
  after it: `ansible` requires `ansible-core`, so `ansible==10.*` gives the commands of
  `ansible-core`; and so on down to a distribution with scripts (the build names the
  meta-package and the tools taken through it). Any other distribution with no script is a
  library, not a tool (the build says so): `requests` listed beside `httpie` gives no command,
  and its dependency's `normalizer` is not one. Two tools with one script name, a
  console and a gui script among them, fail the build naming both distributions; no script at all
  fails it, saying what the file named. `--entry NAME` builds one of the commands alone. The smoke test
  checks every command as for any several-command executable: it imports each target's module and
  looks its function up (library code, imported as for any console script).
- **Build:** the lock is installed as for any pip project (a `requirements.txt` that is not pinned
  warns, `--strict` and `--hash` lines work as before), then the commands are tied to links.
  Nothing of the project is packaged: the executable holds the tools and their dependencies. The
  distribution that stands for the directory is named after it, or `<name>-tools` when a
  requirement has that name (`mkdir httpie; echo httpie==3.2.4 > httpie/requirements.txt`).
- **Helper interpreters:** a tool that starts helpers through `sys.executable` (Ansible's local
  connection, worker pools, `multiprocessing` outside the cases fsbpyr recognizes) exits 70 unless
  the binary is built with `--interpreter-reexec`. The build cannot tell which tools do; it prints
  the note it prints for any app when a library that starts helper interpreters is packaged, and
  you test the commands you need. See [Helper interpreters](#helper-interpreters).
- **Interpreter-like commands** (`ipython`, `ansible-console`) are reachable through the
  executable's name (`tools ipython`) as through their links; a `sudo` rule must name the link
  (`/opt/tools/ansible`), not the executable.
- **Licences:** you redistribute the tools. Their licence files are collected as for any app
  (`fsbpyr inspect --licenses`), which is the notices the distributions ship.

### A single script

`fsbpyr build report.py` builds one script: a `*.py` file, or an extensionless file whose first
line is a python or `uv run --script` shebang (anything else is refused). The app is named after
the file's stem, hyphens kept (`check-disk.py` gives `check-disk`), and written to `dist/<name>`
in the script's directory, or to `-o`. It runs where "A directory of scripts" does (EL8 with `--glibc 2.28`, or
`glibc = "2.28"` in its block), with the same checks, run-time behaviour (as `__main__`, `argv[0]` the full path of the start, the script's directory first on
`sys.path`, a name that is no Python name stored under a module name) and notes on files read
next to `__file__`.

- **What is packaged:** the file and its static local-import closure: the sibling modules and
  packages it imports from its own directory, transitively, and a directory it adds to `sys.path`
  from its `__file__`. The rest of the directory is never read, so a script among 200 unrelated
  ones holds its own few modules. A `pyproject.toml`, `requirements.txt`, `pylock.toml` or
  `uv.lock` beside the script is not read; `--requirements FILE` is.
- **Dependencies, `requires-python` and settings** come from the script's PEP 723 block
  (`# /// script` ... `# ///`): `dependencies` are resolved at build time (the build warns that it
  is unlocked), `requires-python` chooses the Python as a project's does, including the fetch of
  another Python's fsbpyr, and a `[tool.fsbpyr]` table (`# [tool.fsbpyr]`, `# strict = true`) is
  read as in `pyproject.toml`, with unknown keys rejected. Without a block the script has no
  dependencies unless a venv supplies pins (the "venv rung" below): the import check names an
  import nothing provides, with the freeze command when a `.venv` is next to it.
- **A script lock:** a `report.py.lock` beside the script (`uv lock --script report.py`) locks
  the build: the dependencies are installed from it, `--locked` fails the build when the block
  has outgrown it (run `uv lock --script` again), and `fsbpyr inspect` shows `script.lock`. fsbpyr
  itself never writes beside the script: it makes no lock, so a script with dependencies and no
  lock builds unlocked.

### Which Python

An app runs on the CPython of the fsbpyr that builds it, and there is one fsbpyr per Python
(3.12 to 3.15; "Getting fsbpyr"). `fsbpyr build` chooses the version, strongest first:

1. `--python X.Y` on the command line;
2. `python = "X.Y"` in `[tool.fsbpyr]`: both are an error when the version is not supported or
   is outside `requires-python`;
3. the Python pinned in `.python-version`, when it names a supported version inside
   `requires-python`: a pin outside `requires-python` is an error, and a pin fsbpyr cannot
   build for (3.11, say) a warning, after which the next rule applies;
4. `requires-python`: with a lower bound, the lowest supported version that satisfies it (`>=3.10`
   gives 3.12); with only an upper bound, the highest below it (`<3.14` gives 3.13); a pinned
   minor version as pinned (`==3.13.*` gives 3.13). If none is supported, the build stops and
   names the supported ones. A project without `requires-python` (or with only `!=` bounds)
   uses the Python of the fsbpyr that builds it.

The patch release is the one that variant embeds. When the chosen version is not the running
fsbpyr's, the build is handed to the matching one: a released fsbpyr downloads the variant of
its own release (the `.xz` of the release's `fsbpyr-3.X-TARGET` asset, unpacked and checked as
`fsbpyr self update` does before anything runs it), keeps it in its cache
(`~/.cache/fsbpyr/downloads/fsbpyr-3.X-TARGET-vX.Y.Z`, or under `$FSBPYR_CACHE_DIR`; `fsbpyr cache gc`
leaves it alone, delete it by hand to reclaim the space) and runs it with the same
arguments, so only the variants a project needs are downloaded, and a cached one builds
offline. A developer build (`fsbpyr --version` starts with `untagged`) never downloads a
variant, which would mix released code into a development build: it stops and says how to
build the variant from a checkout, `make build PYTHON=3.X`.

### Python 3.15

fsbpyr builds for 3.15 since 0.25.0, but 3.14 stays the default, and 3.14 is the one to use for
now: some widely used packages publish no wheel for 3.15 yet, or none that fsbpyr's glibc 2.34
baseline can use. Until the packages your project needs have them, build with 3.14:
`--python 3.14`, `python = "3.14"` in `[tool.fsbpyr]` or `3.14` in `.python-version`. The 3.15
fsbpyr builds such a project with its release's 3.14 fsbpyr (see above). What to expect on
3.15, as of 0.25.0 (2026-10-23):

- **No wheel and no source distribution:** onnxruntime (1.31.0). A project that needs it does
  not build for 3.15; uv stops with "doesn't have a source distribution or wheel for the
  current platform".
- **A source distribution only:** PyYAML (6.0.3) and gnureadline (8.3.3). uv builds them on the
  build host, which then needs a compiler and their headers (libyaml for PyYAML's C loader,
  readline for gnureadline), and the result is not reproducible on another host. PyYAML built
  this way may come without its C loader: in fsbpyr's tests `yaml.CSafeLoader` was missing.
- **Wheels above the baseline:** orjson's 3.15 wheels (3.13.0) need glibc 2.39, so uv takes its
  source distribution instead, which needs Rust.
- **Older releases in a lock:** a version locked before 3.15 came out may have no 3.15 wheel
  where a newer one does (pydantic-core 2.46.5 but 2.50.1, charset-normalizer 3.4.7 but
  3.5.2): `uv lock --upgrade-package NAME` takes the newer one.

The build prints `built on this host from sdists, not reproducible elsewhere:` and the names of
every distribution that had no usable wheel. It is information, not a warning: `--strict` does
not fail on it. fsbpyr's own tests pass on 3.15 except what needs these packages, which they
skip there (`tests/support/no-wheels.txt`).

## Older systems: EL8 and glibc 2.28

Apps built the usual way need glibc 2.34. `--glibc 2.28` (or `glibc = "2.28"` in
`[tool.fsbpyr]`; the command line wins) builds for glibc 2.28 instead: RHEL 8 and its rebuilds
(Rocky Linux 8, AlmaLinux 8), supported until 2029. `--glibc 2.34` is the default and builds
as it always did. It works for every kind of build: a project, projects without a build system,
a directory of scripts and a single script (`glibc = "2.28"` in the scripts' directory settings
or in a script's `# [tool.fsbpyr]` block). For 2.28 the build

- puts a launcher into the app that is linked against Rocky Linux 8.10's glibc 2.28 (the
  default one is linked against Debian 12's);
- has uv pick wheels for `manylinux_2_28` (`x86_64-manylinux_2_28`, `aarch64-manylinux_2_28`),
  builds a dependency without one from its sdist against the same sysroot, and warns about
  libstdc++, libgcc_s or libz versions above what auditwheel's manylinux_2_28 policy allows
  (RHEL 8's libstdc++ is GCC 8's);
- records the baseline in the executable: `fsbpyr inspect` shows `baseline   glibc 2.28`
  (`glibc_baseline` in `--json` and `build_info`), and the build summary says `glibc >= X.Y`
  for what the app needs; the build warns about a native file that needs more than 2.28.

Where the launcher for 2.28 comes from depends on how you run fsbpyr. The release fsbpyr
executable carries the standard library and the default launcher only; the launcher for 2.28 is
a separate asset of its release, one per Python and target,
`fsbpyr-stub-3.14-x86_64-linux-gnu-glibc2.28.xz` (the stub, compressed; a file you place may
be that or the unpacked stub), with the SHA-256 of each unpacked stub in
`SHA256SUMS-glibc2.28`. **EL8 assets are published
separately and not with every release.** A separate workflow (`.github/workflows/el8.yml`)
builds, tests and attaches them to a release after it is published, and only when all of its
jobs pass; the release itself never waits for it. **A release has EL8 stubs when its assets
include `SHA256SUMS-glibc2.28`** (it is uploaded last, after the eight `.xz`): look for it on the
[releases](https://github.com/devitops-com/fsbpyr/releases) page, or run `gh release view vX.Y.Z
--json assets --jq '.assets[].name' | grep glibc2.28`. Each asset has a build provenance
attestation: `gh attestation verify fsbpyr-stub-3.14-x86_64-linux-gnu-glibc2.28.xz --repo
devitops-com/fsbpyr --signer-workflow devitops-com/fsbpyr/.github/workflows/el8.yml`. A release
without them makes `--glibc 2.28` stop with `this release (vX.Y.Z) has no glibc 2.28 stubs`,
naming the asset to place and linking the releases; use a release that has them. fsbpyr
takes the launcher from, in this order:

1. a file you placed in the directory of the fsbpyr executable, named exactly as the asset,
   plain or `.xz`: the directory of the path you run (`~/.local/bin` for `fsbpyr`) and, if
   different, of the executable's real path. This is how a machine without access to GitHub
   builds for 2.28: download the asset on another machine and copy it there. For a project
   of another Python, whose fsbpyr is that Python's variant, place it next to the variant
   (in `~/.cache/fsbpyr/downloads/`);
2. fsbpyr's cache, where an earlier download is kept (under the release's tag);
3. a download from the release, checked against the SHA-256 in `SHA256SUMS-glibc2.28` and kept in the cache.

Whatever the source, the launcher must be the one built for this fsbpyr's release, Python and
target (every launcher records the runtime id it was linked as, and the build compares it with
the id it computes from its own launcher sources and pins); one of another release or Python is
refused, with the asset to use instead. That identity detects a stub of another release or
Python, not a tampered one: only the download is verified against the published SHA-256, so
check a file you place yourself against `SHA256SUMS-glibc2.28` first, and keep the directory of
the fsbpyr executable and the cache writable by you alone. `fsbpyr self update` replaces the
executable but not the files you placed beside it, which belong to the old release and would be
refused: the update names them in a warning; place the stubs of the new release there, or remove
them and let `--glibc 2.28` download them.

fsbpyr run from a checkout or a wheel has no release to fetch from: it uses the runtime that
`fsbpyr runtime build --python X.Y --glibc 2.28` put in the cache, and says to run that command
when there is none.

Kernels with 64K pages (EL8's on aarch64) are smoke-tested in a QEMU virtual machine, not on
hardware: `make test-vm` boots EL8's kernel, and the EL8 workflow runs an app built with
`--glibc 2.28` on that kernel with the EL8 userland before it publishes the aarch64 stubs.

## Failing a build on warnings

Without `--strict`, every warning leaves the exit status at 0. With it, `build` stops after
all warnings are collected and before it writes anything, so a file at `-o` stays as it was,
and exits 1 with the warnings in the message. This includes the "unlocked build" warning, so
a project without `uv.lock`, `pylock.toml` or `requirements.txt` fails. Three kinds of
warning do not count: the uv that differs from the one that built fsbpyr and git errors,
which describe the build host, and the ctypes guess about libraries no extension module
loads, which only `fs-packages` silences, and that changes how the app runs.

A `requirements.txt` counts as locked even when it is not pinned. Without `--strict`, a
line without `==` gets a warning. With it, uv also gets `--require-hashes`, so every line
needs `==` and a `--hash`. uv checks the hashes in a `pylock.toml` on its own,
`--strict` or not (a changed hash in a `pylock.toml` fails the build; verified with uv 0.13.1).
A line pinned by a commit SHA (`name @ git+URL@<40 hex>`) or a `#sha256=` URL is pinned for
the warning, but a SHA is not a hash: `--strict` refuses a requirements file with such lines
before uv runs, naming them. An `-e ./libs/common` line installs as a regular package, with a
warning that it is whatever the tree holds. With no `requirements.txt` but `requirements/*.txt`,
the build says to pick one with `--requirements`.

`--strict` does not check the glibc the executable needs. To fail unless it is 2.34 or
older (`jq -e` exits 1 on `false`):

```sh
fsbpyr build -o dist/mytool --strict
fsbpyr inspect --json dist/mytool | jq -e '.glibc | split(".") | map(tonumber) <= [2, 28]'
```

## Leaving out sources

By default an executable holds every module twice: as compiled bytecode, which is what runs, and
as its `.py` source, which is only read when something asks for it. `--no-source` (or
`include-source = false`) leaves the sources out of the app and its dependencies.

**What you gain.** Size. For the [`heavy`](tests/fixtures/projects/heavy) test app (15
distributions, most of their size native libraries) the executable shrinks from 77.1 MB to
73.7 MB, measured with 0.20.0 ([docs/performance.md](docs/performance.md#build-options-0200));
an app with more Python code saves more. Startup does not change (measured): sources are never
read on the normal path.

**What changes.** Everything that needs the text of a module:

- **Tracebacks** still name the file and line, but show no source line under it:

  ```
  Traceback (most recent call last):
    File "/usr/local/bin/mytool/mytool/cli.py", line 23, in main
  ValueError: invalid literal for int() with base 10: 'not a number'
  ```

  With sources the same traceback also shows the line `int("not a number")` under it.
- **`inspect.getsource()`** raises `OSError: could not get source code`. Libraries that read
  the source of your functions at run time break, `torch.jit.script` for example.
- **Reading a `.py` file as data** (`importlib.resources.files("pkg") / "x.py"`, or a package
  in `fs-packages` that expects its `.py` files on disk) fails with `FileNotFoundError`: the file
  is not in the executable.

The standard library keeps its sources either way: they come with the runtime, so tracebacks
through stdlib code still show source lines.

**When to use it.** For a release where size matters and the app does not read its own
source. Test the `--no-source` build before shipping it: run the app's main commands and a
failing path, since a library that calls `inspect.getsource()` only fails when that code runs.
Keep sources for builds you debug.

## Inspecting an executable

```
fsbpyr inspect BINARY [--json] [--verify] [--all-modules] [--files]
fsbpyr inspect BINARY --licenses
```

Shows the app and its version, the fsbpyr that built it (`fsbpyr --version` prints the same form:
`0.4.0-1a2b3c4d`), Python and glibc (and the baseline of an app built with `--glibc 2.28`), the entry point, the system libraries the executable
needs from the target with the newest symbol version it uses of each (libstdc++'s `GLIBCXX`
and `CXXABI`, libgcc_s's `GCC`, libz's `ZLIB`), the uv that built it, how its content is
compressed (the zstd level and, with native files, `xz -6` after `--best-compression`), the
file's digest, the bundled shared libraries and extension modules, and the top-level modules
of each distribution, in as many columns as the terminal fits. `--all-modules` lists every module, `--json` prints
everything as JSON, and `--verify` recomputes the digest and also prints the file's plain
SHA-256 (`sha256` in the text and in `--json`), the hash a GitHub attestation or `sha256sum`
names. Without `--verify` it is left out, so plain `inspect` stays fast. It also shows the
versions of the libraries the runtime carries (OpenSSL, SQLite, zlib, expat, libmpdec) and the
lock the app was built from with its SHA-256, and `--files` lists every packaged file with its
kind, size and SHA-256 (from the distributions' RECORD files). The build information `--json`
prints under `build_info` is described in [docs/format.md](docs/format.md#build-information).

### Licence notices

Every executable statically links third-party libraries (OpenSSL, libffi, libedit, ncurses,
expat, mpdecimal, SQLite, zlib, liblzma, zstd, bzip2, libuuid, and the HACL\* code in the hash
modules) and holds fsbpyr's launcher and boot modules. The licence texts for those travel in
the executable under `_fsbpyr_licenses/`, with CPython's and fsbpyr's own.
`fsbpyr inspect --licenses BINARY` prints them, then the notices of each distribution in the
app: the files of its dist-info `licenses/` directory, those METADATA names with
`License-File`, licence files in the dist-info root, and files named `LICENSE*`, `COPYING*`,
`NOTICE*` or `ThirdPartyNotices*` in its packages. The runtime's part is the exact list of
what fsbpyr links. The distributions' part is best effort: it finds what the packages ship
under those names, which need not be every notice their bundled libraries require.

fsbpyr's LICENSE has a runtime exception for the launcher and the boot modules, so an app need
not reproduce fsbpyr's MIT notice for them. To redistribute an app, ship the output of
`inspect --licenses` with it (or point to the executable), and check the distributions' part
against what you know your dependencies bundle. Each release also carries it for fsbpyr's own
executable as the asset `THIRD_PARTY_LICENSES-3.X-<target>.txt`, one per executable (such as
`THIRD_PARTY_LICENSES-3.14-x86_64-linux-gnu.txt`).

A build warns when a native file needs a `GLIBCXX`, `CXXABI`, `GCC` or `ZLIB` version newer
than the manylinux_2_34 policy allows (manylinux_2_28's with `--glibc 2.28`), since such a
library fails to import on a target with an older libstdc++ (RHEL 9, Ubuntu 22.04; RHEL 8);
the warning names the file and the version. The list
covers the native files an extension module loads, and the launcher.

The glibc shown (`glibc >= X.Y`) is the newest glibc symbol version the launcher and the
bundled native files use, so the oldest glibc this executable starts on, and never below the
glibc the build is for (2.34, or 2.28 with `--glibc 2.28`). The launcher needs
2.34 (glibc moved `pthread_create`, `dlopen` and `__libc_start_main` there), so an app without
native files shows `glibc >= 2.34`, fsbpyr's own executable included. Native files from
manylinux_2_34 wheels need at most 2.34; one built from source can need more, and the build
warns about it. Only 2.34 and newer is tested; with `--glibc 2.28`, 2.28 and newer.

## Shell completion and the man page

fsbpyr prints both. They come from the same definitions as `--help`, so all three always
agree:

```
fsbpyr completion bash > ~/.local/share/bash-completion/completions/fsbpyr
fsbpyr completion zsh > ~/.zfunc/_fsbpyr     # a directory in $fpath, before compinit
fsbpyr completion fish > ~/.config/fish/completions/fsbpyr.fish
fsbpyr man > ~/.local/share/man/man1/fsbpyr.1
```

The shell asks fsbpyr for the words as you type, so after `--entry` and `--extra` it offers
the console scripts and extras of the project being built: the directory named before them,
or the current one.

## Integrity

Every executable checks its whole file before it does anything else and refuses to start if a
single byte changed (damage to the launcher's own code can also make it crash before it gets
to say so):

```
fsbpyr: cannot start /usr/local/bin/app: integrity check failed: the file was modified or damaged
```

The check hashes the file with SHA-256 on up to 8 threads, never more than the cores it may
use. On a Core Ultra 9 285 with the file in the page cache it takes 1.7 ms for a 28 MB app,
11 ms for 244 MB and 47 ms for 1.07 GB (one core: 7.8, 84 and 311 ms; without SHA instructions
on one core: 46 ms, 0.4 s and 1.6 s;
[docs/performance.md](docs/performance.md#integrity-check-0200), measured at 0.20.0). The
first start after a reboot also reads the whole file from disk: 6 to 12 ms more for a 28 and a
77 MB app on an NVMe SSD ([cold start](docs/performance.md#cold-start-0200)).
`FSBPYR_TRACE=1` reports the time as `integrity_ns`. Files extracted to the cache are hashed
again by every process, once, before it first uses them, with the same digest and the same
multithreaded routine; a changed one is reported and restored.

The digest can be published and checked with `fsbpyr inspect --verify BINARY` (plain `inspect`
shows the stored value) or with standard tools:

```sh
head -c -32 app | split -b 1M --filter='openssl dgst -sha256 -binary' | sha256sum
split -b 1M --filter='openssl dgst -sha256 -binary' < cached-file | sha256sum
```

Measured timings, release by release, are in `docs/performance.md`.

What the check cannot stop is someone who can write the file and rewrites the digest too, or
patches the check out: the code that checks is in the same file. Proving where an executable
came from needs a signature checked with a key from outside it; signed releases are planned
after the MVP ([D31](docs/IMPLEMENTATION_PLAN.md#2-key-decisions)). Until then, a release's
origin is shown by its attestation: `gh attestation verify` checks that this repository's
release workflow built it from its tag ([Verifying a release](#verifying-a-release)).

### Attesting and verifying your app

A GitHub attestation records which workflow built a file from which commit. It is provenance
for your app and complements the outside-key signature planned after the MVP
([D31](docs/IMPLEMENTATION_PLAN.md#2-key-decisions)), which `inspect --verify` and
self-update are meant to check; it does not replace it. In a workflow whose job has
`id-token: write` and `attestations: write`, with fsbpyr and uv installed:

```yaml
- run: fsbpyr build . -o dist/myapp
- uses: actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8 # v4.2.2
  with:
    subject-path: dist/myapp
```

Whoever downloads the app checks it with

```
gh attestation verify myapp --repo ORG/REPO \
  --signer-workflow ORG/REPO/.github/workflows/WORKFLOW.yml
```

The attestation names the file's plain SHA-256, which `fsbpyr inspect --verify myapp` prints
as `sha256` (the `digest` line is fsbpyr's own, checked at every start). A complete workflow
is [.github/workflows/attest-example.yml](.github/workflows/attest-example.yml); it runs on
manual dispatch only. That the example's attestation is accepted by `gh attestation verify`
has not been tested yet.

## At run time

Pure-Python apps run straight from the executable. Native extensions, and the packages listed
in `fs-packages` (plus the known ones: babel, botocore, certifi, litellm, matplotlib and
torch), are extracted on first use into `${XDG_CACHE_HOME:-~/.cache}/<app>`, or
`$FSBPYR_CACHE_DIR` when set; resources an app asks for as a path are extracted there too.
A package in `fs-packages` comes with the libraries its extensions need from outside it and
with every shared library its distribution ships (openmm opens its plugins from a directory),
and `ctypes.CDLL` of a bare file name the system cannot load finds the app's bundled library
of that name (PyTables' `libblosc2.so`).
With `--best-compression` that first use takes longer, since xz decodes more slowly than zstd:
the launcher decodes each library in pieces of 4 MiB on up to 8 threads (numpy's first import:
about +70 ms; onnxruntime's together with numpy's: about +80 ms; measured at 0.8.0, not
re-measured: [docs/performance.md](docs/performance.md#--best-compression-080)); later runs
are as fast as with zstd. Since what is extracted there is loaded as code, the cache directory
must be yours, and it and every directory leading to it owned by root or by you and writable
by no other user (not by others, nor by a group other than your primary one) unless sticky, as
`/tmp` is; symlinks are followed, and must be root's or yours. No other user can then replace
or redirect any part of it. A cache that fails this is refused, and the error names the
directory at fault. (In a user namespace, a sandbox, directories whose owner is outside it
show as nobody's; they count as root's where nobody is no user inside, and only above a
directory anyone may add to, such as `/tmp`.) Environment variables the executable reads:

| Variable | |
|---|---|
| `FSBPYR_CACHE_DIR` | cache directory |
| `FSBPYR_GC=1` | collect the app's cache instead of running the app (see below); `0`: never collect it in the background |
| `FSBPYR_INSTALL_LINKS=DIR` | link the app's commands into DIR (absolute symlinks to the executable, and `x.py` for each script `x`), print what was done and exit instead of running the app; exit 1 if something was in the way (see "Deploy") |
| `FSBPYR_TRACE=1` | print what the run loaded and extracted, and how long the integrity check took, as JSON on stderr |
| `FSBPYR_SELFTEST` | `natives`: check that this machine provides every library and symbol version the native extensions need, one line per missing one, exit 1 if any (glibc targets); any other value: import the entry point and check it instead of running the app (what `--smoke` does) |
| `FSBPYR_IMPORTTIME` | when set, print CPython's import-time table |
| `FSBPYR_MAX_INSTANCES` | how many copies of the app may run at once (decimal, at most 4096; `0`: no limit); by default 4 per CPU and 16, at least 64 and at most 1024 |

An executable needs `/proc` (it finds itself through `/proc/self/exe`), and refuses to start
set-uid, set-gid or with file capabilities (exit 70): the cache location comes from the
caller's environment. A start also exits 70 when as many copies of the app run already as
the machine allows (`FSBPYR_MAX_INSTANCES` above): an app that keeps starting itself, by
whatever route, stops there. See [docs/security.md](docs/security.md).

### Helper interpreters

In an executable, `sys.executable` is the app. multiprocessing works with every start method:
the app recognizes the children it re-executes. Other code starts helper interpreters that way
too: joblib's workers (`n_jobs`), Jupyter kernels, py-cpuinfo,
`subprocess.run([sys.executable, "-m", "pkg.tool", ...])`. Such a child asks for Python with
`-m MODULE`, `-c CODE` or a script of the app (`sys.executable` and a `.py` file below the
executable), and what it gets depends on the build:

- built with `--interpreter-reexec` (`interpreter-reexec = true`): it runs the module, the
  code or the script (from the executable) as Python would, interpreter flags such as `-u` or
  `-B` accepted and ignored;
- built with neither option: it is refused, with exit 70 and a message naming what it asked
  for and the option, except for a script that is one of the app's own commands (a script of a
  directory of scripts starting its sibling as `[sys.executable, <exe>/_fsbpyr_scripts/x.py]`),
  which runs that command as `python x.py` would. Running the app in its place would make every such child start
  the app again, and every copy start more: joblib's `n_jobs=2` makes three copies per copy;
- built with `--no-interpreter-reexec` (`interpreter-reexec = false`): it runs the app, with
  these arguments, for an app whose own options are named `-m` or `-c` and which starts itself
  with them. A `-c` whose value is a name or a constant, such as `-c config.toml`, is the app's
  option in any build.

Only a child of the app itself is treated so: the same command line from a shell, a script or
sudo runs the app, with these arguments, so a sudo rule for the app never hands out an
interpreter. However an app starts copies of itself, through a shell or a script too, no
more of them run at once than its instance limit allows (`FSBPYR_MAX_INSTANCES` above): a
start beyond it exits 70. Code that starts `sys.executable` in another way so cannot start
the app without end, deep or wide. The build lists the libraries it finds that start helper
interpreters (joblib, jupyter_client, py-cpuinfo, multiprocess, torch's inductor).

### The cache cleans itself

What a running app extracted stays in use for the app's whole life; what no process has used
for 7 days is removed. A collection is due a day after the last; each start checks that with
one `stat`, and when one is due a detached process at idle priority collects, so the start is
not slowed. When the file system has less than 10% free, a collection is due an hour after the
last, and everything not in use goes. `fsbpyr` checks its own cache (runtimes, Python
distributions, sysroots and downloads) the same way when it runs, keeping what it builds with.
`fsbpyr cache gc` collects its own cache and every app cache now; `FSBPYR_GC=1 app` collects
that app's.

Only a cache root with the marker `.fsbpyr-cache` is ever collected, and only what fsbpyr
made there. The marker is written into a new (empty) root, or one that holds nothing but a
cache of an earlier version: a directory of yours that `FSBPYR_CACHE_DIR` names is never
marked. To have such a directory collected all the same (a volume of its own, with its
`lost+found`, say), create the marker yourself: `touch "$FSBPYR_CACHE_DIR/.fsbpyr-cache"`.

## Security

Report a vulnerability privately, as [`SECURITY.md`](SECURITY.md) describes. Every executable
carries its own CPython and the libraries linked into it (OpenSSL, SQLite, zlib and others),
so a security fix in them reaches an app only when the app is rebuilt with the fsbpyr release
that carries it; `SECURITY.md` says how soon such a release follows python-build-standalone's.

## Versions

Releases are tagged `vX.Y.Z`: a fix bumps the patch level, a new feature the minor version, a
rewrite the major version. `fsbpyr --version` prints `X.Y.Z-<commit>` for a tagged build and
`untagged-<commit>` otherwise.

Before 1.0 only the following is kept stable; everything else may change in any release:

- `fsbpyr inspect --json` carries a `schema` number (`docs/format.md`). A key that changes
  meaning increments it; a new key does not. CLI flags and `[tool.fsbpyr]` keys are not
  covered: they can change before 1.0, and a change that breaks them is marked **Breaking:**
  in the [changelog](CHANGELOG.md).
- What `fsbpyr self update` and an installer rely on across releases: the release assets
  `fsbpyr-3.X-<target>` (such as `fsbpyr-3.14-x86_64-linux-gnu` and
  `fsbpyr-3.12-aarch64-linux-gnu`) with their `.xz` and their `releases/latest/download/` URLs,
  `install.sh` and its `--python` option, and the `release_asset` key of `build_info` that
  records which asset an executable was released as, which self-update reads. A Python stays
  in the release while fsbpyr supports it; the assets of other targets come under the same
  promise when they ship. (0.21.0's unlabeled `fsbpyr-<target>` and short names such as
  `fsbpyr` went in 0.22.0: every asset names its Python since.)
- The trailer and the whole-file digest layout (`docs/format.md`) that `inspect --verify`
  reads: an older fsbpyr checks its download of a newer release in-process, so a later format
  version must keep that check working.

The design, its decisions and the progress of each phase are in
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md); the executable format is in
[`docs/format.md`](docs/format.md).

## Getting fsbpyr

fsbpyr builds itself into a single executable, and that executable is the easiest way to run
it: it carries its own runtime (CPython and the launcher), so building apps with it needs
no compiler. There is one fsbpyr per Python version, 3.12, 3.13, 3.14 and 3.15, each built with and
carrying that version's runtime only; the apps it builds run on that Python. Each
[release](https://github.com/devitops-com/fsbpyr/releases) carries all of them, built and
attested by GitHub Actions. An asset is named with its Python and its target, the platform it
runs on: `fsbpyr-3.14-x86_64-linux-gnu`, `fsbpyr-3.12-aarch64-linux-gnu` and so on. fsbpyr
releases for `x86_64-linux-gnu` and `aarch64-linux-gnu` (Linux with glibc on x86-64 and arm64)
so far. A release may also carry stub-only assets for `--glibc 2.28`, which are not executables
("Older systems").

You need one of them: whichever Python it runs on, an fsbpyr builds a project for any of the
four, downloading the matching one of its own release when the project needs another Python
(see "Which Python"). The installer takes the default Python's, 3.14's, unless told another.

### The installer

```
curl -LsSf https://github.com/devitops-com/fsbpyr/releases/latest/download/install.sh | sh
```

`install.sh` is a POSIX `sh` script (dash and busybox too) that each release builds, attests and
attaches, modelled on uv's: the release's version and the SHA-256 of every asset are written
into it, so a download is checked before it is used. A tag's own copy installs that version
(`.../releases/download/vX.Y.Z/install.sh`). It

- detects the target (`uname`, and musl when `ldd --version` says so) and stops, naming the
  targets the release has, when there is none for this host or the host's glibc is older than
  the `glibc >= X.Y` the release records for the executable;
- downloads with `curl` (HTTPS only, TLS 1.2 or newer) or else `wget`, the `.xz` when `xz` is
  installed, checks the SHA-256 and runs `fsbpyr inspect --verify` on the new file before it
  installs it; with `FSBPYR_VERIFY=1` it also runs `gh attestation verify` and stops when that
  fails or `gh` is missing;
- installs into the first of `$FSBPYR_INSTALL_DIR`, `$XDG_BIN_HOME`, `$XDG_DATA_HOME/../bin`
  and `~/.local/bin`, under a temporary name renamed into place, and writes the man page and
  completions where `make install` puts them under that prefix (only for a directory named
  `bin`);
- when the directory is not on `PATH`, writes an `env` script (`env.fish` for fish) next to the
  executable and sources it from `~/.profile` (created if missing), `~/.bashrc` and `~/.zshrc`
  (when they exist) and fish's `conf.d`; an `env` that is already there, uv's included, is
  reused. `--no-modify-path` or `FSBPYR_NO_MODIFY_PATH=1` leaves the shell files alone, and in
  GitHub Actions the directory goes to `$GITHUB_PATH` instead.

Options: `--python X.Y` installs the fsbpyr that runs on that Python instead of 3.14's
(it stops, naming the release's Pythons, when there is none), `-h/--help`, `-q/--quiet`,
`-v/--verbose`, `--no-modify-path`. Through the pipe:
`curl ... | sh -s -- --python 3.13`. The installer is tested against a local HTTPS server
under dash, bash and busybox sh. The commands below are the manual way.

### Manually

Pick the asset of your Python and target; the commands below fetch the Python 3.14 one for
x86-64 Linux:

```
mkdir -p ~/.local/bin
curl -fLo ~/.local/bin/fsbpyr https://github.com/devitops-com/fsbpyr/releases/latest/download/fsbpyr-3.14-x86_64-linux-gnu
chmod +x ~/.local/bin/fsbpyr
# recommended: checks that this repository's release workflow built it
gh attestation verify ~/.local/bin/fsbpyr --repo devitops-com/fsbpyr \
  --signer-workflow devitops-com/fsbpyr/.github/workflows/release.yml
```

The same executable also comes as `fsbpyr-3.14-x86_64-linux-gnu.xz`, half the download
(14.0 MB instead of 28.9 MB for 0.20.0), attested as well:

```
mkdir -p ~/.local/bin
curl -fL https://github.com/devitops-com/fsbpyr/releases/latest/download/fsbpyr-3.14-x86_64-linux-gnu.xz \
  | xz -d > ~/.local/bin/fsbpyr
chmod +x ~/.local/bin/fsbpyr
```

The others are at the same URLs under their names
(`.../releases/latest/download/fsbpyr-3.12-aarch64-linux-gnu`). Each executable also has its
licence notices, `THIRD_PARTY_LICENSES-3.X-<target>.txt`, and its SBOM, `<asset>.cdx.json`;
`SHA256SUMS` lists every file. One installed `fsbpyr` is enough: for a project that needs
another Python it downloads the matching one of its own release (see "Which Python").

### Updating

```
fsbpyr self update
```

asks GitHub for the latest release and, when it is newer than the running executable, downloads
the same Python's build of it (`fsbpyr-3.13-x86_64-linux-gnu.xz` for one that runs on 3.13: an
executable records which asset it was released as and updates to that one, so it never moves
to another Python; run the installer with `--python` for that), unpacks it in-process, checks it as
`fsbpyr inspect --verify` does before anything runs it, and renames it over the executable (its real path, mode kept; a running
fsbpyr keeps its old file). The man page and the completions that `make install` put under the
same prefix (`<prefix>/bin/fsbpyr` and `<prefix>/share/...`) are written again; ones installed
elsewhere get a hint. It needs write access to the executable's directory and says so when it
has none. A developer build (`make build`, `make bootstrap`; `fsbpyr --version` starts with
`untagged`) refuses: install the release with the installer above. The update does not
check the release's attestation; `gh attestation verify` still does.

### Building from source

A source checkout needs `make` and [uv](https://docs.astral.sh/uv/). On Debian trixie it also
needs the Python pinned in `.python-version` (`uv python install 3.14.9`), and `curl` and
`zstd`; on other systems, [podman](https://podman.io) instead (install it with your system's
package manager). The targets install none of these: when one is missing, they name it and
stop. `make help` lists all targets.

The runtime is linked with clang and lld 23 (the pinned python-build-standalone release is
LLVM 23 bitcode), which trixie does not ship: the targets that build a runtime fetch LLVM's
release tarball for the host's architecture that `tools/release/llvm23` pins (LLVM 23.1.3,
about 1.2 GB once, checked against its SHA-256) and unpack what linking needs, about 600 MB,
into `~/.cache/fsbpyr/toolchains`. With `FSBPYR_LLVM23=0` they use a clang and lld 23 or newer
on `PATH` instead.

```
make bootstrap   # once: from source to an installed fsbpyr
make build       # dist/fsbpyr, built by the installed fsbpyr
make install     # dist/fsbpyr, its man page and completions under ~/.local
```

**Other Python versions.** `PYTHON=3.12` (or `3.13`, `3.14`) on the `make` command line makes
`make test`, `make test-ci`, `make check` and `make build` run on that Python, in the locked
environment `.venv-3.12`; the Python must be installed with uv (`uv python install 3.12`).
`make build PYTHON=3.12` builds `dist/fsbpyr-3.12` from source with its own runtime, and
`make bootstrap PYTHON=3.12` does the three stages (`tools/bootstrap.sh --python 3.12`) into
`dist/stage1-3.12` to `dist/stage3-3.12` and `dist/fsbpyr-3.12`, without installing it.

**`make bootstrap`** takes a checkout to an installed fsbpyr. On **Debian trixie** it runs on
the host:

1. checks for uv and the Python pinned in `.python-version` (3.14.9), which uv may not
   download here (`UV_PYTHON_DOWNLOADS=never`), and stops naming what to install if either is
   missing;
2. installs the locked dependencies into the checkout's `.venv`;
3. builds the runtime (`fsbpyr runtime build`: the pinned CPython from python-build-standalone
   and a Debian 12 sysroot (glibc 2.36) are downloaded, the launcher linked with LLVM's clang and lld 23
   (above; on aarch64 with its compiler-rt builtins, which have the atomics that CPython's
   aarch64 objects call), the standard library compiled; a minute or two, cached in
   `~/.cache/fsbpyr`);
4. builds fsbpyr in three stages: fsbpyr run from source builds `dist/stage1/fsbpyr`, that
   executable builds `dist/stage2/fsbpyr`, and that one builds `dist/stage3/fsbpyr`. Each
   stage must pass `fsbpyr inspect --verify` (its digest is recomputed over the whole file),
   and stages 2 and 3 must be byte-identical: builds are reproducible, so a difference means
   fsbpyr does not build itself faithfully, and bootstrap stops there;
5. copies stage 3 to `dist/fsbpyr` and runs `make install`.

On **any other Linux x86-64 or aarch64** host steps 1 to 4 run in a Debian trixie podman image
of the host's architecture (`Containerfile`: Debian's packages, LLVM's clang and lld 23
fetched as above, and the pinned uv from its release tarball, checked against a SHA-256 per
architecture, which installs the pinned Python there), on the checkout mounted into it, and `make install` runs on the host. The
container is IPv4-only: on podman's default network, TLS handshakes from Python with GitHub's
download host time out. A podman volume, `fsbpyr-cache`, keeps uv's cache, its Python and the
runtime between runs. `FSBPYR_BOOTSTRAP=podman make bootstrap` uses the container on trixie too.

**`make build`** builds the checkout with the installed fsbpyr (`~/.local/bin/fsbpyr`, else
`fsbpyr` on `PATH`) into `dist/fsbpyr`, and runs the result once as a smoke test. The installed
executable carries its own runtime, so this needs no compiler, only uv. With no fsbpyr
installed it stops and says to run `make bootstrap`. The new executable's `--version` names
the `vX.Y.Z` tag on HEAD (`untagged-<commit>` otherwise).

**`make install`** installs `dist/fsbpyr` (running `make build` first if it is missing), with
the man page and shell completions it prints:

| File | Installed as |
|---|---|
| the executable | `~/.local/bin/fsbpyr` |
| man page | `~/.local/share/man/man1/fsbpyr.1` (`man fsbpyr` finds it when `~/.local/bin` is on `PATH`) |
| bash completion | `~/.local/share/bash-completion/completions/fsbpyr` (loaded by bash-completion) |
| zsh completion | `~/.local/share/zsh/site-functions/_fsbpyr` (add the directory to `fpath` before `compinit`) |
| fish completion | `~/.local/share/fish/vendor_completions.d/fsbpyr.fish` |

`PREFIX=/usr/local` (as root) installs under another prefix, for all three targets;
`BINDIR`, `MANDIR`, `BASH_COMPLETION_DIR`, `ZSH_COMPLETION_DIR`, `FISH_COMPLETION_DIR` and
`DESTDIR` override single locations. `make uninstall` removes the same files.

`fsbpyr runtime list` shows the runtime embedded in the executable you run, if any, then the
runtimes in the cache, each with the glibc its stub is linked for (`fsbpyr runtime build
--glibc 2.28` links against an EL8 sysroot instead of the default Debian 12 one, 2.34). Running
fsbpyr from a checkout or a wheel (rather than as its own executable) always needs a runtime in
the cache, and a new fsbpyr version may need a new one: the build then says
`no runtime for Python 3.14; run fsbpyr runtime build --python 3.14`, or use the release
binary, which carries its own.

### Verifying a release

Each release's executables (the steps below name `fsbpyr-3.14-x86_64-linux-gnu`; another
Python's differ where said) are built by GitHub Actions from its `vX.Y.Z` tag and attested
there.
Every release from 0.19.0 on stays published as it is: a version whose release fails is
burned, and the fix comes as the next version, never in its place.

The check needs curl, git, gh and uv, and uv must be the one that built the release: uv
builds the project's wheel, stamping its own version into the wheel's metadata, and installs
every distribution, so another uv can give other bytes from identical sources (a build with
any other uv warns). An executable records its uv (`fsbpyr inspect` shows it as "uv", `--json`
as `.uv`; 0.18.2 and older do not record it). The rebuild below compares it with yours: if they
differ, install that uv version and start the rebuild again.

The attestation comes first, before anything runs the file. It shows that this repository's
release workflow built the file from the latest release's tag:

```
asset=fsbpyr-3.14-x86_64-linux-gnu   # or another, such as fsbpyr-3.12-x86_64-linux-gnu
tag="$(gh release view --repo devitops-com/fsbpyr --json tagName --jq .tagName)"
curl -fLo "$asset" "https://github.com/devitops-com/fsbpyr/releases/download/$tag/$asset"
gh attestation verify "$asset" --repo devitops-com/fsbpyr \
  --signer-workflow devitops-com/fsbpyr/.github/workflows/release.yml --source-ref "refs/tags/$tag"
```

If it fails, stop there. Otherwise rebuild the file from that tag, with the uv it records:

```
chmod +x "$asset"
want="$(./"$asset" inspect "$asset" | awk '$1 == "uv" { print $2; exit }')"
[ "$(uv --version | cut -d' ' -f2)" = "$want" ] || echo "install uv $want first: this uv differs"
git clone --branch "$tag" https://github.com/devitops-com/fsbpyr.git src
python="${asset#fsbpyr-}"; python="${python%%-*}"   # the asset's Python: 3.14, 3.12, ...
(cd src && ../"$asset" build . --python "$python" -o ../rebuilt)
cmp "$asset" rebuilt && echo identical
```

`--python` makes each executable rebuild itself with the runtime it carries. Without it the
checkout's `.python-version` (3.14) decides, and `fsbpyr-3.12-x86_64-linux-gnu` hands the
build to the release's 3.14 executable, which builds itself. A variant's Python must be
installed with uv (`uv python install 3.12`).

A rebuild checks the bytes themselves, at two levels:

- **(a) The payload.** The executable rebuilds itself from its own tag byte for byte, so
  fsbpyr's code and its dependencies are what the tag says. This cannot vouch for the launcher
  stub, CPython and the standard library: an fsbpyr executable builds with the runtime it
  carries, copying them from itself ([D4](docs/IMPLEMENTATION_PLAN.md#2-key-decisions)), so a
  changed stub would pass this check.
- **(b) The runtime.** Only an independent rebuild checks the stub, CPython and the standard
  library. It compiles the launcher from the tag's sources and links it with the clang and lld
  that the tag pins; CPython, which the launcher links in, and the standard library, which is
  compiled to bytecode, come from the python-build-standalone archive that the tag pins by
  SHA-256. So (b) proves the stub, and that CPython and the standard library are that pinned
  build; it does not compile CPython. CI's release is `make build`'s output, so on a clean
  checkout of the tag `make bootstrap` and `make build` give the released bytes, with the
  release's uv and clang (`fsbpyr inspect` lists the stub's `.comment` lines as "toolchain";
  they name the clang that built the launcher). The launcher's bytes depend on that exact clang
  and lld. From 0.23.0 on they are LLVM's 23.1.3 from its release tarball, which
  `tools/release/llvm23` pins by SHA-256 and the build fetches itself ("Building from source").
  v0.19.0 to v0.22.0 were linked by Debian's `22.1.8-1~deb13u4`, which the snapshot
  `20261005T000000Z` of the Debian archive serves (a later clang may link other bytes): to
  rebuild one of those, point apt at the snapshot
  (`http://snapshot.debian.org/archive/debian/TIMESTAMP/` for trixie and trixie-updates,
  `.../debian-security/TIMESTAMP/` for trixie-security) before installing `clang-22 lld-22`.
  Since 0.23.0 the build installs nothing from Debian but git, curl, xz-utils, make and zstd,
  from Debian's own repository; a release's log lists the image digest, the uv version, the
  LLVM pin and the version of `xz-utils`. Use the uv named by
  `tools/release/uv.version` (release CI downloads that release's
  tarball for the machine's architecture and checks its SHA-256, `tools/release/install-uv.sh`; `FSBPYR_UV_VERSION=<version>` makes `tools/bootstrap.sh`
  refuse any other uv). `make bootstrap` also installs the result, though,
  so run fsbpyr from source directly in the checkout above: it builds the runtime, then `stage1`,
  and `stage1` builds the file to compare. A runtime already in the cache under the same id is
  reused, not built again; an empty cache (`XDG_CACHE_HOME` set to a new directory) builds it
  afresh. (b) is checked on every push to main, for each of the four Pythons and both
  targets, by the full suite's `reproduce` job (`.github/workflows/build.yml`), which rebuilds
  from a second checkout (another path and user, other caches, umask 077) and whose stage 3 the
  `compare` job checks against the build byte for byte; a release is made only from a commit
  whose full suite run passed, and its own build differs from that build only in the tag it
  records.

```
cd src
uv run --locked fsbpyr runtime build --clang "$(bash tools/bootstrap.sh --llvm23)"
uv run --locked fsbpyr build . -o ../stage1
../stage1 build . -o ../from-source
cd .. && cmp fsbpyr from-source && echo identical
```

For `fsbpyr-3.12-x86_64-linux-gnu` or `fsbpyr-3.13-x86_64-linux-gnu`, `bash tools/bootstrap.sh --from-source --python 3.12` in
the checkout runs the same stages in `.venv-3.12` (`dist/stage1-3.12` to `dist/stage3-3.12`,
the last being the file to compare); the Python must be installed with uv.

Each executable also has an SBOM, `<asset>.cdx.json` (CycloneDX 1.6), which lists the
statically linked OpenSSL, SQLite, expat and zlib with CPEs, CPython, and the Python
distributions with their lock hashes, so that a scanner such as grype can match them
(`grype sbom:fsbpyr-3.14-x86_64-linux-gnu.cdx.json`). The release workflow attests it to its
executable:

```
gh attestation verify "$asset" --repo devitops-com/fsbpyr \
  --predicate-type https://cyclonedx.org/bom
curl -fLO "https://github.com/devitops-com/fsbpyr/releases/download/$tag/$asset.cdx.json"
./"$asset" inspect --verify --json "$asset" | python3 tools/release/sbom.py - | cmp - "$asset.cdx.json"
```

The last command, run in a checkout of the tag, makes the SBOM again from the executable and
compares it with the published file. `tools/release/sbom.py` needs only Python's standard
library. These commands have not been run against a published release yet.

An executable also records the version of the fsbpyr that built it (`fsbpyr inspect` shows it
as "built with"), so an fsbpyr of another version, or of an untagged commit, gives a different
file from the same sources, even when the launcher and the payload are otherwise the same. Such
a build works the same; only the bytes differ. Releases before 0.18.2 do not reproduce
themselves: their launchers were not linked reproducibly.
