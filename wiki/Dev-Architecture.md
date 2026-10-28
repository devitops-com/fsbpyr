# Architecture

How fsbpyr's parts fit together: the three programs involved in every app, the subsystems of the
Python package and how they depend on each other, the three processes (building an app, building
a runtime, starting an app), the caches, and the design rules that cut across all of them. The
pages on each part go into detail; this page is the map.

![fsbpyr from project to running app: inputs, the build on the build machine, the executable's layout, and the start on the target machine](images/arch-overview.svg)

## Three programs

| Program | Source | Runs | Details |
|---|---|---|---|
| **fsbpyr**, the tool | [`src/fsbpyr/`](../src/fsbpyr) without `boot/` and `launcher/` | on the build machine, from source or as its own executable | [Build modules](Dev-Build-Modules.md), [Payload modules](Dev-Payload-Modules.md), [Runtime modules](Dev-Runtime-Modules.md) |
| **the launcher** | [`src/fsbpyr/launcher/`](../src/fsbpyr/launcher), C, linked statically with CPython | first, in every executable | [Launcher](Dev-Launcher.md) |
| **the boot code** | [`src/fsbpyr/boot/`](../src/fsbpyr/boot), Python | inside every executable, after the launcher | [Boot modules](Dev-Boot-Modules.md) |

They meet at two interfaces:

- **The executable format** ([Executable format](Dev-Executable-Format.md)). The tool writes it
  (`payload/writer.py`) and reads it (`payload/reader.py`, for `inspect`, self-update and
  self-hosting); the launcher reads it in C (`launcher/format.h`, `payload.c`). The record layouts
  are defined twice, in `payload/format.py` and `launcher/format.h`, and kept equal by tests.
- **The `_fsbpyr` built-in module** that the launcher gives the boot code
  ([`launcher/module.c`](../src/fsbpyr/launcher/module.c),
  typed in [`stubs/_fsbpyr.pyi`](../stubs/_fsbpyr.pyi)):
  lookups in the index, reads of blobs, directory views, `info()` (which includes the path
  `execve` was given, `AT_EXECFN`, for `sys.argv[0]`), and `sync`, which writes files into the
  cache. The boot code never parses the payload itself. Its unit tests run against a Python
  stand-in of `_fsbpyr` that a contract test checks against `module.c` ([Testing](Dev-Testing.md)).

The boot code is plain Python and imports nothing from the tool. The tool, in turn, reuses two
boot modules: the cache collector `boot/_fsbpyr_rt/gc.py`, so that the tool's cache and every
app's cache follow the same rules, and `boot/_fsbpyr_rt/commands.py`, so that the build writes,
and `inspect` reads, the command table of an executable of several commands with the same code
that reads it at start.

## Subsystems of the Python package

![How the subsystems of the Python package import each other. Edge labels count module-level imports between them](images/arch-subsystems.svg)

Each box is a group of modules as [Build modules](Dev-Build-Modules.md) describes them, or one of
the three subpackages. An edge means that modules in one group import modules in the other; its
label counts the module pairs. The build driver imports nearly every group and nearly every
group imports the shared helpers, so those edges are drawn in light grey. `cli` imports each command's modules inside the command
function; those lazy imports are left out of the graphs.
`fsbpyr/__init__.py`, which only holds the version, is drawn with the shared helpers.

The groups:

- **Command line and dispatch** (`cli`, `dispatch`) parses the command and, when a project needs
  another Python, hands the whole command to the matching fsbpyr executable of the same release.
- The **build driver** (`build`) runs the build: it calls each subsystem in turn, from reading the
  project to the smoke test and the command links.
- **Project, settings, Python version** (`project`, `config`, `pyversion`, `poetry1`) finds out
  what the build was given (a project with or without a build system, a directory of scripts, a
  single script, a directory of only a requirements file) and reads its settings.
- **Commands, scripts and tools** (`entry`, `links`, `loose`, `legacy`, `tools`) decides the
  commands: inferred entries, `#!python` legacy scripts, the wheel fsbpyr writes of a project's
  Python files, the tools a requirements file names, and the links of an executable of several
  commands.
- **Locks of other tools, inferred dependencies** (`foreign`, `pipenv`, `venvdeps`) turns
  `poetry.lock`, `pdm.lock` and `Pipfile.lock` into requirements for uv, and reads the pins of
  undeclared dependencies from a venv's metadata.
- **Static import check** (`importcheck`, `syspath`) follows the imports of script commands
  without running them.
- **Staging with uv** (`staging`), **what the app contains and needs** (`closure`, `classify`,
  `scan`, `natives`, `elf`, `system`), **compiling to bytecode** (`compiler`, `bytecode`),
  **recording provenance** (`manifest`, `vcs`) and **assembling** (`assemble`) are the build's
  other stages.
- **Reading executables** (`inspection`, `sizes`, `licensing`) and **tool upkeep** (`selfupdate`,
  `manpage`) serve the other commands.
- **`runtime/`** obtains and caches runtimes; it uses `payload/` to store a runtime's standard
  library and the boot collector to manage its cache. Its `stubs` module, which gets the
  release's glibc 2.28 (EL8) stub for a released fsbpyr, imports `dispatch`, `selfupdate`,
  `targets` and `vcs`: it downloads from the companion release as a variant or an update is
  downloaded. `dispatch` and `selfupdate` import `stubs` in turn, inside functions: a variant is
  told where the fsbpyr that handed it the build is, and an update names the placed stubs the
  new release refuses. `pbs` imports `elf` to read zstd's version out of the archive.
- **`payload/`** is the format library that everything writing or reading an executable uses.
- **Shared helpers** (`targets`, `compat`) know the supported platforms and paper over
  differences between Python versions.

### Every module

![The import graph of the modules directly under src/fsbpyr, grouped by subsystem; the subpackages are drawn as single nodes; dashed edges are imports inside functions, light grey ones those of build](images/arch-modules-top.svg)

The module `build` imports most of the others; its edges are drawn in light grey.

### Inside the subpackages

Grey nodes are modules outside the subpackage.

**`payload/`**: `format` holds the constants and record layouts; `spec` describes a payload to
write; `writer` encodes it; `reader` opens and validates one; `spill` keeps large blobs on disk
while a build runs; `convert` turns a payload back into a spec.

![Import graph of the payload subpackage](images/arch-modules-payload.svg)

**`runtime/`**: `registry` is the entry point and uses every other module: `pins` (the pinned
CPython and sysroot), `pbs` (fetching and unpacking CPython), `toolchain` (finding clang and
lld), `sysroot`, `ar`, `stub` (linking the launcher), `stdlib` (compiling the standard library),
`notices` (licence texts), `hold` and `collect` (keeping and collecting the cache), `rpm`
(reading the RPM packages of the EL8 sysroot) and `stubs` (a released fsbpyr's stub for glibc
2.28: placed beside the executable, cached, or downloaded from the companion release `el8-vX.Y.Z`
and checked).

![Import graph of the runtime subpackage](images/arch-modules-runtime.svg)

**`boot/`**: `_fsbpyr_boot` imports its helpers only when an app needs them, so a start loads
nothing it does not use: `commands` only for a command table, `links` only when
`FSBPYR_INSTALL_LINKS` is set, `restart` only for the restart idiom, `service` only when a
service policy applies.

![Import graph of the boot subpackage](images/arch-modules-boot.svg)

## Three processes

### Building an app

`fsbpyr build` finds out what it was given and reads its settings, chooses the Python, decides
the commands (writing the wheel of a project's Python files itself where there is no build
system), stages it with uv into a temporary site-packages from its lock (converting a Poetry,
PDM or Pipenv lock first), checks the imports of script commands statically, works out the
import closure and classifies every file (module, resource, extension, shared library), checks
native files against the glibc baseline and the allowed system libraries, compiles every module
to bytecode, records what the build was made from, writes the payload behind the runtime's
launcher stub, runs the result once, and links each command beside it.
[Build modules: what `fsbpyr build` does, step by step](Dev-Build-Modules.md#what-fsbpyr-build-does-step-by-step)
names the module and function behind each step.

![The stages of fsbpyr build, with the modules and functions that carry them out and the inputs each reads](images/build-pipeline.svg)

When the project needs another Python, `dispatch` hands the build to the release's executable
for that Python:

![How fsbpyr build picks the fsbpyr variant for another Python](images/build-dispatch.svg)

### Building a runtime

A runtime is a launcher stub linked with one CPython plus that CPython's compiled standard
library. A released fsbpyr executable carries its own and builds apps with it; from source,
`fsbpyr runtime build` makes one from pinned inputs and caches it under an id that is a digest
of the launcher's sources and the flags it is built with for the target, the PBS release, the
sysroot and the cache layout's version. The stub records that id in its read-only data
(`fsbpyr-stub-identity=<id>`). See [Runtime modules](Dev-Runtime-Modules.md#what-a-runtime-is).

![The steps of building a runtime: pinned PBS archive, sysroot and toolchain in, a cached runtime out](images/runtime-build.svg)

### Starting an app

The launcher checks the whole file's digest, opens the payload, refuses unsafe starts, takes an
instance slot, may start a background collection of the cache, hands CPython the startup modules
as frozen modules, configures an isolated interpreter (unbuffered stdio when `PYTHONUNBUFFERED`
is set) and runs `_fsbpyr_boot.run()` ([Launcher](Dev-Launcher.md#from-execve-to-the-boot-code)).
The boot code installs the import machinery, applies the service policies, makes the command
links when `FSBPYR_INSTALL_LINKS` asks for them, recognises multiprocessing and helper-interpreter
children and the restart idiom, picks the command of a command table, and runs the entry point,
or a script as `__main__` ([Boot modules](Dev-Boot-Modules.md#from-run-to-the-app)).

![The launcher's start-up sequence and its failure exits](images/launcher-startup.svg)

## Caches

Two kinds of cache, one set of rules:

| Cache | Where | Holds | Collected by |
|---|---|---|---|
| **an app's cache** | `$FSBPYR_CACHE_DIR`, or `${XDG_CACHE_HOME:-~/.cache}/<app>` (`~` the passwd home when `$HOME` is unset, empty or relative) | `runtime/<generation>/site/` (native files, `fs-packages`), `resources/<id>/site/` (resources asked for as paths); `.fsbpyr-cache`, `.gc`, `.trash` | the app itself in the background, `FSBPYR_GC=1 app`, `fsbpyr cache gc` |
| **the tool's cache** | `$FSBPYR_CACHE_DIR`, or `${XDG_CACHE_HOME:-~/.cache}/fsbpyr` | `runtimes/<id>/` (stub, `stdlib.fsbp`, `runtime.json`), `pbs/` (unpacked CPython), `sysroots/`, `downloads/` (archives, variants of other Pythons, EL8 stubs); `.gc-tool`, `.trash-tool` | fsbpyr when due, `fsbpyr cache gc` |

The tool's cache shares its root with fsbpyr's own app cache (fsbpyr is an app named `fsbpyr`),
which is why the tool's collector keeps its own stamp and trash. `toolchains/` (the LLVM that
`make` fetches) lives there too but is not collected. Both collectors run
`boot/_fsbpyr_rt/gc.py`: only a root with the marker `.fsbpyr-cache` is collected, an item in use
is held by a lock and never removed, and an item no one used for 7 days goes (any unused item
when less than 10% of the file system is free). Every directory on the way to a cache must be
safe from other users ([Security](Dev-Security.md)).

## Design rules that cut across

- **Determinism.** The same inputs give the same bytes: the writer sorts every table and
  places blobs in a fixed order, bytecode is marshalled canonically whatever the build
  interpreter's flags or hash seed, the wheel fsbpyr writes for a project without a build system
  has fixed timestamps and a sorted record, and the launcher links reproducibly. Tests build the same
  project from another path, user mask, cache and CPU count and require identical bytes, and
  fsbpyr's bootstrap stages must be byte-identical: stages 2 and 3 in `make bootstrap`, all
  three in the test suite ([Testing: determinism checks](Dev-Testing.md#determinism-checks)).
  The zstd library version is part of the input and is recorded in the build information.
- **Self-hosting.** fsbpyr's own executable is built by fsbpyr. A released fsbpyr takes the stub
  and standard library from itself when it builds for its own Python (`registry`), so it needs no
  compiler. [Self-hosting](Dev-Self-Hosting.md) describes the three stages, from source and from
  a release.
- **One definition, several outputs.** The click command definitions produce `--help`, the shell
  completions and the man page; the list of `[tool.fsbpyr]` keys in `config.py` drives parsing,
  error messages and the man page's configuration section.
- **Two implementations of the format, one set of rules.** The Python reader and the C launcher
  read the same records; the edge cases are one table run against both
  ([Testing](Dev-Testing.md#how-the-layers-work)).
- **Read, never run, what is only to be packaged.** No build writes into the source tree.
  Scripts, locks, PEP 723 blocks and a venv's metadata are read as data (`ast`, TOML, METADATA,
  RECORD); only a project with a build system runs its build backend, and the smoke test imports
  only an entry module, never a script ([Security](Dev-Security.md)).
- **Fail closed.** A digest mismatch, a privileged start, a helper interpreter the build did not
  allow, or too many running copies stop the app with exit 70 and a message; a cache directory
  that another user could change is refused, naming the directory, when the app first needs it.
  Nothing runs in a state the checks could not vouch for ([Security](Dev-Security.md)).

Comments in the code, and some of these pages, cite design decisions by number (D9, D38, ...).
They are defined in the implementation plan,
[§2 Key decisions](../docs/IMPLEMENTATION_PLAN.md#2-key-decisions).
