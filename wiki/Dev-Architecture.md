# Architecture

How fsbpyr's parts fit together.

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
  self-hosting); the launcher reads it in C (`launcher/format.h`, `payload.c`). Tests keep the
  record layouts of `payload/format.py` and `launcher/format.h` equal.
- **The `_fsbpyr` built-in module** that the launcher gives the boot code
  ([`launcher/module.c`](../src/fsbpyr/launcher/module.c),
  typed in [`stubs/_fsbpyr.pyi`](../stubs/_fsbpyr.pyi)):
  index lookups, blob reads, directory views, `info()` (with `AT_EXECFN`, the path `execve` was
  given, for `sys.argv[0]`), and `sync`, which writes files into the cache. The boot code never
  parses the payload itself; its unit tests run against a Python stand-in of `_fsbpyr` that a
  contract test checks against `module.c` ([Testing](Dev-Testing.md)).

The boot code imports nothing from the tool. The tool reuses two boot modules: the cache
collector `boot/_fsbpyr_rt/gc.py`, so every cache follows the same rules, and
`boot/_fsbpyr_rt/commands.py`, so the build, `inspect` and the start handle a command table with
the same code.

## Subsystems of the Python package

![How the subsystems of the Python package import each other. Edge labels count the module pairs that import across them](images/arch-subsystems.svg)

Each box is a group of [Build modules](Dev-Build-Modules.md) or a subpackage. An edge's label
counts the module pairs that import across it; the edges of the build driver and to the shared
helpers, which nearly every group has, are light grey. The graphs leave out `cli`'s imports
inside its command functions. `fsbpyr/__init__.py`, which only holds the version, is drawn with
the shared helpers.

- **Command line and dispatch** (`cli`, `dispatch`) parses the command and hands a build for
  another Python to the same release's fsbpyr for that Python.
- The **build driver** (`build`) calls the build's subsystems in turn: **project, settings,
  Python version** (`project`, `config`, `pyversion`, `poetry1`), **commands, scripts and tools**
  (`entry`, `links`, `loose`, `legacy`, `tools`), **locks of other tools, inferred dependencies**
  (`foreign`, `pipenv`, `venvdeps`), **static import check** (`importcheck`, `syspath`),
  **staging with uv** (`staging`), **what the app contains and needs** (`closure`, `classify`,
  `scan`, `natives`, `elf`, `system`), **compiling to bytecode** (`compiler`, `bytecode`),
  **recording provenance** (`manifest`, `vcs`) and **assembling** (`assemble`).
- **Reading executables** (`inspection`, `sizes`, `licensing`), **tool upkeep** (`selfupdate`,
  `manpage`) and **signatures** (`signing`: `key`, `sign`, `verify`, the signing of a build's
  output, the checks of fsbpyr's own downloads) serve the other commands.
- **`runtime/`** obtains and caches runtimes, using `payload/` for a runtime's standard library
  and the boot collector for its cache. Its `stubs` module, which gets the release's glibc 2.28
  (EL8) stub, imports `dispatch`, `selfupdate`, `signing`, `targets` and `vcs` to download the
  way they do;
  `dispatch` and `selfupdate` import `stubs` inside functions, to tell a variant where the fsbpyr
  that handed it the build is and to name the placed stubs a new release refuses. `pbs` imports
  `elf` to read zstd's version out of the archive.
- **`payload/`** is the format library every writer and reader of an executable uses.
- **Shared helpers** (`targets`, `compat`, `text`) know the supported platforms, the differences
  between Python versions and how messages show text.

### Every module

![The import graph of the modules directly under src/fsbpyr, grouped by subsystem; the subpackages are drawn as single nodes; dashed edges are imports inside functions, light grey ones those of build](images/arch-modules-top.svg)

### Inside the subpackages

Grey nodes are modules outside the subpackage.

**`payload/`**: `format` holds the constants and record layouts; `spec` describes a payload to
write; `writer` encodes it; `reader` opens and validates one; `spill` keeps large blobs on disk
during a build; `convert` turns a payload back into a spec.

![Import graph of the payload subpackage](images/arch-modules-payload.svg)

**`runtime/`**: `registry` is the entry point and uses `pins` (the pinned CPython and sysroot),
`pbs` (fetching and unpacking CPython), `toolchain` (finding clang and lld), `sysroot`, `stub`
(linking the launcher), `stdlib` (compiling the standard library), `notices` (licence texts),
`hold` (keeping the cache) and `stubs` (a released fsbpyr's glibc 2.28 stub); `ar` and `rpm`
(reading the EL8 sysroot's RPM packages) serve `pbs`, `stub` and `sysroot`, and `collect`
(collecting the cache) uses `registry`.

![Import graph of the runtime subpackage](images/arch-modules-runtime.svg)

**`boot/`**: `_fsbpyr_boot` imports a helper only when needed: `commands` for a command table,
`links` when `FSBPYR_INSTALL_LINKS` is set, `restart` for the restart idiom, `service` for a
service policy.

![Import graph of the boot subpackage](images/arch-modules-boot.svg)

## Three processes

### Building an app

[Build modules: what `fsbpyr build` does, step by step](Dev-Build-Modules.md#what-fsbpyr-build-does-step-by-step)
names the module and function behind each stage.

![The stages of fsbpyr build, with the modules and functions that carry them out and the inputs each reads](images/build-pipeline.svg)

When the project needs another Python, `dispatch` hands the build to the release's executable
for that Python:

![How fsbpyr build picks the fsbpyr variant for another Python](images/build-dispatch.svg)

### Building a runtime

A runtime is a launcher stub linked with one CPython plus that CPython's compiled standard
library. A released fsbpyr executable carries its own. From source, `fsbpyr runtime build` makes
one from pinned inputs and caches it under an id that digests those inputs
([Runtime modules](Dev-Runtime-Modules.md#what-a-runtime-is)).

![The steps of building a runtime: pinned PBS archive, sysroot and toolchain in, a cached runtime out](images/runtime-build.svg)

### Starting an app

The launcher checks the file's digest, opens the payload, refuses unsafe starts, sets up an
isolated CPython with the startup modules frozen, and runs `_fsbpyr_boot.run()`
([Launcher](Dev-Launcher.md#from-execve-to-the-boot-code)). The boot code installs the import
machinery, recognises helper processes, picks the command and runs it
([Boot modules](Dev-Boot-Modules.md#from-run-to-the-app)).

![The launcher's start-up sequence and its failure exits](images/launcher-startup.svg)

## Caches

| Cache | Where | Holds | Collected by |
|---|---|---|---|
| **an app's cache** | `$FSBPYR_CACHE_DIR`, or `${XDG_CACHE_HOME:-~/.cache}/<app>` (`~` the passwd home when `$HOME` is unset, empty or relative) | `runtime/<generation>/site/` (native files, `fs-packages`), `resources/<id>/site/` (resources asked for as paths); `.fsbpyr-cache`, `.gc`, `.trash` | the app itself in the background, `FSBPYR_GC=1 app`, `fsbpyr cache gc` |
| **the tool's cache** | `$FSBPYR_CACHE_DIR`, or `${XDG_CACHE_HOME:-~/.cache}/fsbpyr` | `runtimes/<id>/` (stub, `stdlib.fsbp`, `runtime.json`), `pbs/` (unpacked CPython), `sysroots/`, `downloads/` (archives, variants of other Pythons, EL8 stubs); `.gc-tool`, `.trash-tool` | fsbpyr when due, `fsbpyr cache gc` |

The tool's cache shares its root with fsbpyr's own app cache, hence its own stamp and trash.
`toolchains/` (the LLVM that `make` fetches) lives there too, uncollected. Both collectors run
`boot/_fsbpyr_rt/gc.py`: only a root marked `.fsbpyr-cache` is collected, an item in use is held
by a lock and never removed, and an item unused for 7 days goes (any unused item when less than
10% of the file system is free). Every directory on the way to a cache must be safe from other
users ([Security](Dev-Security.md)).

## Design rules that cut across

- **Determinism.** The same inputs give the same bytes: the writer sorts every table and
  places blobs in a fixed order, bytecode is marshalled canonically whatever the build
  interpreter's flags or hash seed, the wheel fsbpyr writes has fixed timestamps and a sorted
  record, and the launcher links reproducibly. The zstd library version is an input, recorded
  in the build information. Tests require identical bytes across paths, user masks,
  caches, CPU counts and bootstrap stages
  ([Testing: determinism checks](Dev-Testing.md#determinism-checks)).
- **Self-hosting.** fsbpyr builds its own executable; a released fsbpyr needs no compiler for
  its own Python, taking the stub and standard library from itself (`registry`,
  [Self-hosting](Dev-Self-Hosting.md)).
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
  another user could change is refused, by name, when the app first needs it
  ([Security](Dev-Security.md)).

Design decisions cited by number (D9, D38, ...) are defined in the implementation plan's
[§2 Key decisions](../docs/IMPLEMENTATION_PLAN.md#2-key-decisions).
