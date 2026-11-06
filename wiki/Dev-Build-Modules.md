# Build modules

The Python modules directly under [`src/fsbpyr/`](../src/fsbpyr) in fsbpyr v@VERSION@: the
command line, the build pipeline, and the commands that read executables or keep the tool up to
date. First what `fsbpyr build` does step by step, then every module by subsystem. The
subpackages have their own pages: [payload modules](Dev-Payload-Modules.md),
[runtime modules](Dev-Runtime-Modules.md), [boot modules](Dev-Boot-Modules.md) and the C
[launcher](Dev-Launcher.md).

Module names drop the `fsbpyr.` prefix. "Imports" and "Imported by" list fsbpyr modules, taken
from the source's syntax tree; "(lazy)" marks an import inside a function.

## Contents

- [What `fsbpyr build` does, step by step](#what-fsbpyr-build-does-step-by-step)
  - [What a build starts from](#what-a-build-starts-from)
  - [The steps](#the-steps)
  - [Building for another Python](#building-for-another-python)
  - [Building for glibc 2.28](#building-for-glibc-228)
- [Command line and dispatch](#command-line-and-dispatch): `__main__`, `cli`, `dispatch`
- [Build driver](#build-driver): `build`
- [Project, settings, Python version](#project-settings-python-version): `project`, `config`, `pyversion`, `poetry1`
- [Commands, scripts and tools](#commands-scripts-and-tools): `entry`, `links`, `loose`, `legacy`, `tools`
- [Locks of other tools, inferred dependencies](#locks-of-other-tools-inferred-dependencies): `foreign`, `pipenv`, `venvdeps`
- [Static import check](#static-import-check): `importcheck`, `syspath`
- [Staging with uv](#staging-with-uv): `staging`
- [What the app contains and needs](#what-the-app-contains-and-needs): `closure`, `classify`, `scan`, `natives`, `elf`, `system`
- [Compiling to bytecode](#compiling-to-bytecode): `compiler`, `bytecode`
- [Recording provenance](#recording-provenance): `manifest`, `vcs`
- [Assembling](#assembling): `assemble`
- [Reading executables](#reading-executables): `inspection`, `sizes`, `licensing`
- [Tool upkeep](#tool-upkeep): `selfupdate`, `manpage`
- [Signatures](#signatures): `signing`
- [Shared helpers](#shared-helpers): `fsbpyr`, `targets`, `compat`, `text`

## What `fsbpyr build` does, step by step

[`cli.py`](../src/fsbpyr/cli.py) decides the kind of input and the Python. For the Python
this fsbpyr runs on, [`build.build`](../src/fsbpyr/build.py#L683)
calls [`build._build`](../src/fsbpyr/build.py#L744),
which writes a new executable beside the output; `build` then smoke-tests it, puts it in place
and links its commands. A `--glibc 2.28` build changes steps 8, 10, 12, 16 and 17
([below](#building-for-glibc-228)). In the diagrams, blue is build-time code, green files on
disk, purple external inputs.

### What a build starts from

`fsbpyr build PROJECT` takes a directory (the current one by default) or a file.
[`Project.load`](../src/fsbpyr/project.py#L73)
decides which of five kinds of input, A to E, it is; the kind decides how the project's wheel is
made, where the commands come from and which lock sources count.

![How Project.load decides the kind of input, A to E, from the file it is given or the files of the directory](images/build-inputs.svg)

| | Input | The project's wheel | Commands | Dependencies, strongest first | Output by default |
|---|---|---|---|---|---|
| A | A project with a build system: a `pyproject.toml` that a build system packages, or a `setup.py` | `uv build`, out of tree | the console scripts and the wheel's `#!python` legacy scripts; with none, the only top-level `__main__` package or guarded module | `--requirements`, `uv.lock` (the project's or its uv workspace's), `pylock.toml`, `requirements.txt`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, else unlocked | `dist/<command>`; with several commands, `dist/<project name>` and a link per command |
| B | A project without a build system: `[project]` and no `[build-system]`, Poetry's `package-mode = false` or PDM's `distribution = false` (a Poetry 1 `[tool.poetry]` without `[project]` too); a uv workspace root is A | written by fsbpyr: the project's `*.py` files under `_fsbpyr_scripts/` | `[project.scripts]`, else the files with a main guard | as A | as A |
| C | A directory of scripts: Python files, and no `pyproject.toml` (or one of tool settings only), `setup.py` or `setup.cfg` | written by fsbpyr: the `*.py` files and the extensionless files whose first line is a python or `uv run` shebang | each file with a main guard or such a shebang | `--requirements`, `pylock.toml`, `requirements.txt`, `Pipfile.lock`; else the commands' PEP 723 blocks (unlocked); else pins read from the scripts' venv | `dist/<command>`; with several, `dist/<directory name>` as a project name (`My_Tools` gives `my-tools`) and a link per command |
| D | One script: a `*.py` file, or an extensionless file whose first line is a python or `uv run` shebang | written by fsbpyr: the script and the files of its directory it imports, transitively | the script, named after its stem | `--requirements`, `<script>.lock`; else its PEP 723 block (unlocked); else pins read from the venv | `dist/<stem>` in the script's directory |
| E | A tool binary: as C, with no Python file and a lock source: `--requirements` (or `requirements` in `[tool.fsbpyr]`), `pylock.toml`, `requirements.txt` or `Pipfile.lock` | written by fsbpyr: metadata only | the console and gui scripts of the tools the lock names | the lock source that makes it E | as C, named after the directory, or `<name>-tools` |

`--entry` builds one command or module of any kind. For B to E no build backend and no project
code runs during the build: fsbpyr writes the wheel itself ([`loose`](#loose)), checks imports
without importing ([`importcheck`](#importcheck)), and the smoke test finds a script without
importing it. The user-facing side is on [Building](User-Building.md).

### The steps

A step marked with some of the letters runs, or takes that branch, only for those kinds.

![The steps of fsbpyr build in five phases, command line, checks, staging, content and writing, with the functions behind them and the inputs each reads; italic lines say what a glibc 2.28 build does differently](images/build-pipeline.svg)

**Command line** ([`cli.py`](../src/fsbpyr/cli.py))

1. The console script `fsbpyr.cli:main`, also reached by `python -m fsbpyr`, is
   [`main`](../src/fsbpyr/cli.py#L748): it runs the click group with the command line as the
   context's object.
   [`build_command`](../src/fsbpyr/cli.py#L164)
   takes both signing variables out of its environment (`signing.take_build_variables`), checks
   the Python and the options ([`cli`](#cli)), collects the tool's cache when due, and splits
   `--smoke-args` as a shell would.
2. [`Project.load`](../src/fsbpyr/project.py#L73)
   reads PROJECT, with `--requirements` if given, decides its kind, and reads the Python a
   `Pipfile.lock` lock source asks for.
   [`config.load_config`](../src/fsbpyr/config.py#L168)
   merges `[tool.fsbpyr]` with the options, which win, into a `BuildConfig`; its `glibc` is the
   baseline of the steps below. When only the table names `requirements`, `build_command` loads
   the project again with that file, so the lock source is the one the build installs.
3. [`pyversion.resolve`](../src/fsbpyr/pyversion.py#L89)
   picks the Python. For another Python than this fsbpyr's, `dispatch.run` runs the matching
   fsbpyr with the same command line and returns its exit status
   ([below](#building-for-another-python)); otherwise `build.build` runs.

**Checks** (`build._build`)

4. `_build` loads the project again, and
   [`check_before_staging`](../src/fsbpyr/build.py#L440)
   refuses what `pyproject.toml` and the options already decide: a console script whose name is
   no command name when there are several, a `[project]` with no console script, no possible
   legacy script and no `__main__.py` or guarded module in its tree, an output path that is a
   directory, a version-control line in a requirements file uv reads with `--require-hashes`, and
   an undeclared extra. Scripts and extras are checked here only when `[project]` lists them
   statically, otherwise after staging.
5. For B to E, [`load_package`](../src/fsbpyr/build.py#L460)
   reads the files into a `loose.Package` ([`Package.load`](../src/fsbpyr/loose.py#L92) for B,
   C and D, warning about each file left out;
   [`Package.tool_binary`](../src/fsbpyr/loose.py#L143) for E). A package with no command fails
   the build unless `--entry` names one. Step 10 writes the wheel from this package, so the build
   checks the files it packages.
6. [`staging.find_uv`](../src/fsbpyr/staging.py#L82)
   requires uv on `PATH` before anything slow (D26), and `uv_version` gets its version for
   build_info.
7. [`python_warnings`](../src/fsbpyr/build.py#L727)
   gives the version rule's warnings (a `.python-version` pin it ignores, say).
8. [`runtime.registry.select_runtime`](../src/fsbpyr/runtime/registry.py#L312)
   returns the runtime: the one embedded in the running fsbpyr when its Python and glibc baseline
   match, else the one `fsbpyr runtime build` cached. `Project.check_python` then tests
   `requires-python` against the runtime's full version, and `uv_mismatch` warns when uv is not
   the one that built this fsbpyr.
9. [`Project.lock_source`](../src/fsbpyr/project.py#L212)
   chooses what the dependencies come from. For C and D with no lock source and no PEP 723
   dependency, [`infer_dependencies`](../src/fsbpyr/build.py#L553)
   follows the commands' imports ([`importcheck.check_command`](../src/fsbpyr/importcheck.py#L198));
   when the runtime lacks some and a venv is found
   ([`venvdeps.find_venv`](../src/fsbpyr/venvdeps.py#L58)),
   [`venvdeps.infer`](../src/fsbpyr/venvdeps.py#L179)
   pins the distributions that provide them; the lock source is then `inferred`, and a warning
   names the venv (`--strict` fails here).
   [`lock_warnings`](../src/fsbpyr/build.py#L361)
   warns about an unlocked build, unpinned requirement lines, and a stale Poetry, PDM or Pipenv
   lock.

**Staging**, in a temporary directory `fsbpyr-stage-*` that lives until the executable is written

10. [`staging_plan`](../src/fsbpyr/staging.py#L183)
    turns the lock source into uv commands, which
    [`run_plan`](../src/fsbpyr/staging.py#L393)
    runs: the project's wheel is made by `uv build` for A, by
    [`Package.write_wheel`](../src/fsbpyr/loose.py#L192)
    for B to E, then installed with the locked distributions for the glibc baseline
    ([`staging`](#staging)). A Poetry, PDM or Pipenv lock is first converted
    ([`foreign.export`](../src/fsbpyr/foreign.py#L472),
    [`pipenv.export`](../src/fsbpyr/pipenv.py#L157)).
11. [`SiteIndex.scan`](../src/fsbpyr/closure.py#L99)
    reads the staged site's `*.dist-info`, and
    [`check_no_editable`](../src/fsbpyr/closure.py#L107)
    refuses editable installs.
12. [`built_here_above`](../src/fsbpyr/build.py#L201)
    finds distributions uv built from sdists on this host whose native files need a newer glibc
    than the baseline, and
    [`rebuild_on_baseline`](../src/fsbpyr/build.py#L217)
    builds them again against the sysroot of the baseline's pin (`runtime.pins.sysroot_for`,
    `runtime.sysroot.fetch_sysroot` and `build_flags`, then `staging.rebuild_wheel` and
    `staging.rebuild`): Debian 12's packages (glibc 2.36) for 2.34, Rocky Linux 8.10's RPMs for
    2.28. The site is scanned again.
13. `_installed_project` finds the project's distribution by the name of the wheel from step 10,
    and checks the extras step 4 could not.
    [`legacy.attach`](../src/fsbpyr/legacy.py#L39)
    makes the wheel's `#!python` legacy scripts commands (A only). For E,
    [`tool_binary`](../src/fsbpyr/build.py#L614)
    has [`tools.tool_scripts`](../src/fsbpyr/tools.py#L312)
    choose the tools and their scripts, and `tools.with_commands` makes them the project's own.
14. [`SiteIndex.walk`](../src/fsbpyr/closure.py#L126)
    follows `Requires-Dist` under the runtime's markers. For B, C and D,
    [`check_script_imports`](../src/fsbpyr/build.py#L583)
    skips, with a warning naming `file:line`, a command that imports what neither the project,
    the staged site nor the runtime holds; `--strict` fails on that, as does a build with no
    command left.
    [`resolve_entry`](../src/fsbpyr/closure.py#L232)
    gives the app's name and entry point. `_output_path` gives the output (`-o`, or
    `dist/<app name>`), `_commands_to_link` checks a table's link paths (`links.check_links`,
    [`links`](#links)) and refuses a command named `<output>.sig`, and the output's directory is
    created.

**Content**, with a `payload.spill.Spill` beside the output that holds compressed bytes

15. [`classify.classify`](../src/fsbpyr/classify.py#L750)
    turns every installed distribution's `RECORD` into payload modules, files and native
    libraries ([`classify`](#classify)).
16. The warnings are gathered: the walk's, the classifier's, `best_compression_unused` and
    `helper_interpreter_hints`.
    [`glibc_report`](../src/fsbpyr/build.py#L155)
    computes the oldest glibc the app runs on, and
    [`system.system_report`](../src/fsbpyr/system.py#L122)
    the system libraries the target must provide; both warn about needs above the baseline.
17. An `assemble.App` collects the content and the app's build_info:
    [`_provenance`](../src/fsbpyr/build.py#L264)
    gives `schema`, `lock`, `dists` and `settings` (through `manifest`), and `build` adds
    `project`, `fsbpyr_version`, `uv`, `glibc`, `glibc_baseline` (2.28 only), `system`,
    `zstd_level`, `native_compression`, `interpreter_reexec`, `git` (`vcs.git_state`) and, for
    fsbpyr itself only, `release_asset`.
18. [`_check_entry_module`](../src/fsbpyr/build.py#L105)
    refuses an entry module that is neither in the app nor in the runtime, and a package entry
    without a `__main__` module; for a table it checks every target. With `--strict`,
    [`check_strict`](../src/fsbpyr/build.py#L412)
    raises `StrictError` for any warning that counts. Nothing has been written yet.

**Writing**

19. [`assemble.build_temporary`](../src/fsbpyr/assemble.py#L124)
    adds the boot modules and the runtime's build_info keys, and
    [`payload.writer.write_temporary`](../src/fsbpyr/payload/writer.py#L589)
    streams the executable into a new `.<name>.XXXX.tmp` beside the output. When `_build`
    returns, the spill is closed and the staging directory removed.
20. Unless `--no-smoke`,
    [`smoke_test`](../src/fsbpyr/build.py#L638)
    runs the new file with `FSBPYR_SELFTEST=1`, which checks every command's target; with
    `--smoke-args`,
    [`smoke_run`](../src/fsbpyr/build.py#L661)
    also runs it as the app with those arguments.
21. [`payload.writer.commit`](../src/fsbpyr/payload/writer.py#L617)
    renames the new file over the output, once `build` has removed an `<output>.sig`, and syncs
    the directory; for a table
    [`links.make_links`](../src/fsbpyr/links.py#L67)
    puts a relative symlink per command beside it. `build_command` prints the warnings,
    `fsbpyr: built PATH (SIZE MB, entry ENTRY, glibc >= X.Y)` (`N commands` for a table), the
    commands and links, `fsbpyr: target needs: ...`, and for B to E how to deploy
    (`links.deploy_recipe`). With `--signing-key` or `FSBPYR_SIGNING_KEY` it then signs the
    output into `<output>.sig` (`signing.sign_output`); a failure only warns. In a dispatched
    build the variant signs, with the variables `dispatch.run` hands it alone.

When a step fails, `build_command` prints the warnings collected so far (attached to the error
as notes, or listed in a `StrictError`'s message), then `fsbpyr: error: ...`, and exits 1.
`build`'s `finally` deletes the temporary executable and the empty output directories the build
created, so the output stays as it was unless step 21 is reached; a link that cannot be made
there fails the build with the executable in place.

### Building for another Python

Each fsbpyr builds apps for the Python it runs on (`build.TARGET_PYTHON`). For another,
[`dispatch.run`](../src/fsbpyr/dispatch.py#L116)
runs the same release's fsbpyr for it ([`dispatch`](#dispatch)), which does the whole build from
step 1; a script is handed on as a project is. `load_config` checks the table first, so an
unknown `glibc` stops the build before any variant is fetched.

![How fsbpyr build chooses between building in this process and running the cached or downloaded variant for another Python](images/build-dispatch.svg)

### Building for glibc 2.28

`--glibc 2.28`, or `glibc = "2.28"` in `[tool.fsbpyr]`, builds an app for EL8 hosts. The
baselines are `runtime.pins.BASELINES`: `DEFAULT_BASELINE` (2.34) and 2.28 (D46). The baseline
changes these steps:

| Step | Default, glibc 2.34 | `--glibc 2.28` |
|---|---|---|
| 8, runtime | the runtime embedded in the fsbpyr executable, its stub linked against Debian 12's glibc 2.36 sysroot | the executable's standard library with its release's stub linked against Rocky Linux 8.10's glibc 2.28 sysroot (`runtime.stubs.paired`); a developer build or a checkout: the cache's runtime, else a `RuntimeCacheError` naming `fsbpyr runtime build --glibc 2.28` |
| 10, wheels | `--python-platform` `x86_64-manylinux_2_34` or `aarch64-manylinux_2_34` (`Target.platform_for`) | `x86_64-manylinux_2_28` or `aarch64-manylinux_2_28` |
| 12, rebuild | of distributions built here that need more than glibc 2.34, against the Debian 12 sysroot | more than glibc 2.28, against the Rocky Linux 8.10 sysroot, read from its RPMs |
| 16, reports | glibc above 2.34, and libstdc++, libgcc_s and libz above the manylinux_2_34 limits ([`natives`](#natives)), warn | glibc above 2.28, and the manylinux_2_28 limits; the app's `glibc` is never below 2.28 |
| 17, build_info | no `glibc_baseline`; `inspect` takes its absence as 2.34 | `glibc_baseline` `"2.28"` (`build.baseline_record`) |

[`runtime.stubs.paired`](../src/fsbpyr/runtime/stubs.py#L248) takes the release's stub, the asset
`fsbpyr-stub-X.Y-TARGET-glibc2.28`, which
[`stub_for`](../src/fsbpyr/runtime/stubs.py#L209)
finds beside the fsbpyr executable, in the tool's cache, or in the companion release
`el8-vA.B.C`, and refuses (`StubError`) unless it carries the runtime id this fsbpyr computes
([Runtime modules](Dev-Runtime-Modules.md#runtimes-for-glibc-228-el8)).

## Command line and dispatch

`cli` defines every command with click and imports the rest of fsbpyr only inside the commands,
so that `fsbpyr --version` stays fast.

### `__main__`

[`src/fsbpyr/__main__.py`](../src/fsbpyr/__main__.py)

`python -m fsbpyr`: calls `cli.main` and exits with its status, under
`if __name__ == "__main__":`, so a worker process that imports the main module (the compiler
pool's fork server, see [`compiler`](#compiler)) does not start another fsbpyr.

- Classes and functions: none.
- Imports: `cli`.
- Imported by: nothing.

### `cli`

[`src/fsbpyr/cli.py`](../src/fsbpyr/cli.py)

The click definitions are the one source of the help text, the shell completions and the man
page (D40). `--version` is computed only when asked,
because from a source checkout it runs git.

| Function | What it does |
|---|---|
| `cli` | The group; plain `fsbpyr` prints the help and exits 0. |
| `build_command` | `fsbpyr build [PROJECT]`: steps 1 to 3 and the output of step 21. |
| `inspect_command` | `fsbpyr inspect`: `inspection.read` as text or JSON; `--licenses` prints `licensing`'s notices instead. |
| `key_group`, `key_new`, `key_trust`, `sign_command`, `verify_command` | `fsbpyr key new`, `key trust`, `sign` and `verify`, through [`signing`](#signing); `_signing_error` exits 2. |
| `runtime_group` | The `fsbpyr runtime` group. |
| `runtime_build` | `fsbpyr runtime build`: `runtime.registry.build_runtime` for `--python`, `--glibc` and `--clang`. |
| `runtime_list` | `fsbpyr runtime list`: the embedded and cached runtimes, with Python and glibc. |
| `cache_group` | The `fsbpyr cache` group. |
| `cache_gc` | `fsbpyr cache gc`: collects the tool's cache, then every app cache found; exit 1 if anything could not be removed or the caches cannot be found. |
| `self_group` | The `fsbpyr self` group. |
| `self_update` | `fsbpyr self update`: `selfupdate.update`. |
| `completion` | Prints click's completion script for bash, zsh or fish; it calls fsbpyr with `_FSBPYR_COMPLETE` set. |
| `man` | Prints the man page `manpage.render` makes from these commands and `EXAMPLES`. |
| `main` | The console script's entry point: runs the group and returns the exit status. |

`DEFAULT_PYTHON` is the X.Y of the Python fsbpyr runs on; `EXAMPLES` are the man page's
examples, each a command line and what it does, which a test runs.

Rules it enforces:

- `build` and `runtime build` refuse a Python outside 3.12 to 3.15
  (`bytecode.SUPPORTED_PYTHON`) with exit 1: `uv pip install` and `uv tool install` ignore the
  upper bound of fsbpyr's `requires-python`, and the build would otherwise fail after staging.
- `--fast-compression` with `--best-compression`, a `--smoke-args` string that cannot be split,
  and `inspect --licenses` with another option are usage errors (exit 2).
- `--glibc` is a click choice of `2.34` or `2.28`; `runtime build` defaults to 2.34.
- `build`, `inspect`, `runtime build` and `runtime list` first collect the tool's cache when due
  (`runtime.collect.collect_when_due`); a failure never stops the command, and is printed unless
  it is a missing home directory, an unwritable cache or a read-only file system.
- A cache location that needs a home directory when there is none (`HOME` unset, empty or
  relative, and no absolute home in the passwd entry) makes `build`, `runtime build`,
  `runtime list` and `cache gc` exit 1 with `fsbpyr: error: cannot find the home directory; set
  HOME, XDG_CACHE_HOME or FSBPYR_CACHE_DIR` (a `RuntimeCacheError`).
- A host that is none of the targets (a released fsbpyr under `setarch i686`, say) makes `build`
  exit 1 with `fsbpyr: error: unsupported host linux/i686` (`targets.UnsupportedHost`), not an
  internal error.
- `main` makes SIGTERM raise `KeyboardInterrupt`, so a terminated build unwinds like an
  interrupted one. It reports click usage errors with click's exit status, an interrupt as
  `fsbpyr: interrupted` with exit 130, and any other exception as an internal error with its
  traceback and where to report it, exit 1.
- Completion of `--entry` and `--extra` reads the project's commands and extras; if anything
  fails it offers nothing, since a message would land on the shell prompt.

- Imports, all lazy: `assemble`, `build`, `bytecode`, `classify`, `closure`, `config`,
  `dispatch`, `inspection`, `licensing`, `links`, `manpage`, `project`, `pyversion`,
  `selfupdate`, `signing`, `staging`, `targets`, `text`, `vcs`, `boot._fsbpyr_rt.gc`,
  `payload.format`, `runtime.collect`, `runtime.pbs`, `runtime.registry`, `runtime.stdlib`,
  `runtime.stub`, `runtime.sysroot`, `runtime.toolchain`.
- Imported by: `__main__`.

### `dispatch`

[`src/fsbpyr/dispatch.py`](../src/fsbpyr/dispatch.py)

The variant, the asset `fsbpyr-X.Y-TARGET` of this fsbpyr's release, is fetched and checked as
`self update` fetches ([`selfupdate`](#selfupdate)) before anything runs it, and cached as
`downloads/fsbpyr-X.Y-TARGET-vA.B.C`. It gets the command line as given, the signing variables
`build_command` took out of the environment, and `FSBPYR_DISPATCHED_BY`
(`DISPATCHED_BY_VARIABLE`): this fsbpyr's paths (`runtime.stubs.own_paths`, joined with
`os.pathsep`), beside which it looks first for a placed glibc 2.28 stub.

| Class | What it represents |
|---|---|
| `DispatchError` | The variant cannot be had; the message says why and what to do instead. |

| Function | What it does |
|---|---|
| `variant(python, log, *, offline)` | This release's executable for `python`: from the cache, else fetched into it unless `offline`. |
| `run(python, args, log, *, offline, signing)` | Runs it with `args`, `FSBPYR_DISPATCHED_BY` and the signing variables; returns its exit status. |

Rules it enforces:

- The release comes from `vcs.own_version`. A developer build (a version that is no release
  number, `_own_tag`) never fetches a variant, which would mix released code into it, nor does
  fsbpyr run from source, which cannot check a signature (`signing.available`): the
  `DispatchError` says to build it from the checkout (`make build PYTHON=X.Y`).
- The download URLs are `$FSBPYR_RELEASE_DOWNLOADS/<tag>/<asset>.xz`, `SHA256SUMS` and
  `SHA256SUMS.sig`, by default under
  `https://github.com/devitops-com/fsbpyr/releases/download` (tests serve their own).
- The cache root goes through `runtime.registry.secure_directory` (created mode 0700, or checked to
  be the user's own and not writable by others); `downloads/` is created mode 0700.
- `_fetch` writes a temporary file beside the final one, sets mode 0755, verifies it, refuses it
  when it records another asset or release (`selfupdate.check_release_identity`) or a glibc
  newer than the host's (`selfupdate.check_glibc`), and renames it, so
  the cached file appears whole or not at all. A failure raises `DispatchError` naming the asset
  and the release, and removes the temporary file.
- With `--offline`, a variant not in the cache raises `DispatchError` saying to build once
  without `--offline`.

- Imports: `inspection`, `selfupdate`, `signing`, `targets`, `vcs`, `runtime.registry`,
  `runtime.stubs` (lazy).
- Imported by: `cli` (lazy), `selfupdate` (lazy), `runtime.stubs`.

## Build driver

### `build`

[`src/fsbpyr/build.py`](../src/fsbpyr/build.py)

The steps above, and the logic that belongs to no single module. `TARGET_PYTHON` is the X.Y of
the Python fsbpyr runs on, which is the app's.

| Class | What it represents |
|---|---|
| `BuildError` | The executable could not be produced, or failed its smoke test. |
| `StrictError` | A `BuildError` for `--strict`: warnings that count; the message lists them. |
| `BuildResult` | What `build` returns: output path, app name, entry point, size, warnings, the glibc needed, the "target needs" line, a table's commands, the links made, whether there was no build system, and the project's name. |

| Function | What it does |
|---|---|
| `glibc_report(stub, natives, *, baseline)` | The app's glibc: the stub's, raised by bundled native files, never below a non-default baseline (a launcher linked against glibc 2.28 may need only older symbols); a warning when a native file needs more than both. |
| `baseline_record(glibc)` | build_info's `glibc_baseline` entry, for 2.28 only. |
| `signature_of(output)` | Where a detached signature of `output` goes: `<output>.sig`. |
| `built_here_above(dists, baseline)` | Distributions built from sdists on this host (every wheel tag `linux_*`) with a native file that needs more than `baseline`. |
| `rebuild_on_baseline(plan, project_dir, wheel, names, baseline, *, offline, log, verbose)` | Step 12, the project's own wheel too when among `names`; returns warnings when it cannot. |
| `settings_record(config)` | build_info's `settings`. |
| `builder_uv()` | The uv version in the running fsbpyr's build_info; None from source. |
| `uv_mismatch(*, builder, current)` | An `Uncounted` warning when uv is not the one that built this fsbpyr: another uv can give other bytes from identical sources. |
| `best_compression_unused(*, best_compression, files)` | A warning when `--best-compression` has no native file to compress. |
| `helper_interpreter_hints(modules, reexec)` | An `Uncounted` note naming packaged libraries known to start helper interpreters (`HELPER_INTERPRETERS`), unless `interpreter-reexec` is true. |
| `unpinned_warning(unpinned)` | A warning naming up to three requirements not pinned by `==`, a commit SHA or a hash. |
| `lock_warnings(project, source, requirements)` | Step 9's warnings (none for an unlocked C or D without dependencies); for an unlocked build also a `poetry.lock` or `pdm.lock` a directory of scripts ignores, or a `requirements/` directory. |
| `check_hashed_requirements(project, config)` | Refuses version-control lines, by file and line, in a requirements file uv reads with `--require-hashes` (`--strict`, or a `--hash` option): a commit SHA is not a hash. |
| `check_strict(warnings)` | Raises `StrictError` when a warning that is not `Uncounted` is among `warnings`. |
| `check_output(output)` | Raises `BuildError` when the output path is a directory. |
| `check_before_staging(project, config)` | Step 4. |
| `load_package(project, config, warnings)` | Step 5 (None for A). |
| `infer_dependencies(package, project, config, runtime, warnings)` | Step 9's venv rung, with the warning naming the venv and the command that declares the pins. |
| `check_script_imports(package, project, config, site, runtime, *, warnings)` | Step 14's import check of each command (or the one `--entry` names); returns the skipped commands. |
| `tool_binary(package, project, config, index, dist, *, python_version, log)` | Step 13 for E; None for any other build. |
| `smoke_test(executable, app_name, output)` | Runs the new executable in self-test mode and requires success. |
| `smoke_run(executable, output, args)` | Runs it as the app with `args` and requires exit 0. |
| `build(directory, config, *, log, verbose)` | The whole build; returns a `BuildResult`. |
| `python_warnings(project, config)` | The version rule's warnings; `BuildError` for a project that builds with another Python, which only a direct call of `build` reaches. |

Private helpers with logic of their own: `_build` (steps 4 to 19), `_provenance`,
`_installed_project`, `_converted_lock_warnings`, `_unresolved_warning`, `_removed_hint`,
`_freeze_command`, `_checked_commands`, `_output_path`, `_commands_to_link`, `_make_output_dir`,
`_check_entry_module` and `_glibc_needs`.

Rules it enforces:

- Eight kinds of warning are `Uncounted` (listed in `classify.Uncounted`'s docstring): the uv
  mismatch, git's errors, the helper interpreter note, the `requirements/` hint, the note on a
  `Pipfile.lock`'s other index, and three of `classify`'s guesses (the `--hints` reads next to
  `__file__`, shared libraries no extension loads that Python code names, executables among the
  resources). A script's read next to its `__file__` counts.
- A skipped command's warning shows up to three `file:line` places and says where to declare
  the module; with a venv it gives the `uv pip freeze --python <venv>/bin/python` command. For a
  standard library module the build's Python lacks (`importcheck.REMOVED_IN`) it names the newest
  Python that has it (`--python 3.12` for one removed in 3.13), or says the script needs porting
  and, for `imp`, `distutils`, `asyncore`, `asynchat` and `smtpd`, what to use instead.
- When a rebuild against the baseline's sysroot cannot be done (offline, a sysroot that cannot be
  fetched, a failed rebuild with uv's last line), the host's build is packaged and a warning
  says why.
- Distributions installed but not reached by the dependency walk are packaged all the same, with a
  warning that they come from the lock or requirements file, except for C, D and E with a lock
  source or inferred pins.
- The smoke test allows 120 seconds and requires exit 0 and `fsbpyr selftest ok: <app>` on
  standard output. The smoke run passes the final output path as `argv[0]`, with standard input
  from `/dev/null` and the same time limit. A failure shows the end of standard error
  (`text.tail_lines`, 3000 characters).
- A one-command build replaces a link an earlier build of the project made for one of several
  commands (`links.is_earlier_link`), leaving that build's other files alone ([`links`](#links)).

- Imports: `assemble`, `bytecode`, `classify`, `closure`, `config`, `elf`, `entry`, `foreign`,
  `importcheck`, `legacy`, `links`, `loose`, `manifest`, `natives`, `pipenv`, `project`,
  `pyversion`, `staging`, `system`, `targets`, `text`, `tools`, `vcs`, `venvdeps`,
  `boot._fsbpyr_rt.commands`, `payload.format`, `payload.spec`, `payload.spill`,
  `payload.writer`, `runtime.pbs`, `runtime.pins`, `runtime.registry`, `runtime.sysroot`.
- Imported by: `cli` (lazy).

## Project, settings, Python version

The user-facing side is on [Settings](User-Settings.md).

### `project`

[`src/fsbpyr/project.py`](../src/fsbpyr/project.py)

Reads PROJECT and decides its kind ([What a build starts from](#what-a-build-starts-from)), from
`pyproject.toml`'s `[project]` and `[tool.fsbpyr]` tables (nothing for a `setup.py` project,
whose name is learnt after staging) or, for C to E, through [`loose`](#loose). It also reads
requirements files line by line for the build's checks.

| Class | What it represents |
|---|---|
| `ProjectError` | The directory or file is not something fsbpyr builds, or its constraints exclude the runtime. |
| `LockSource` | `uv.lock`, `pylock.toml`, `requirements`, `script.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `inferred` or `unlocked`. |
| `Project` | A loaded project: metadata, `[tool.fsbpyr]`, whether scripts, extras and entry points are static and legacy scripts possible, the kind (`loose` B to E, `bare` C to E, `script` D, `tools` E), PEP 723 `dependencies`, `lock_python`. Members: `load`, `script_lock`, `uv_root`, `requirements_path`, `lock_source`, `lock_path`, `python_pin`, `specifiers`, `check_python`. |

| Function | What it does |
|---|---|
| `requirement_lines(requirements)` | The logical lines of a requirements file with their first line numbers: comments dropped, `\` continuations joined, a trailing `--hash` cut off. |
| `unpinned_requirements(requirements)` | The unpinned requirements of a pip requirements file. |
| `vcs_requirements(requirements)` | `file:line: text` of each requirement from version control (`git+`, `hg+`, `svn+`, `bzr+`). |
| `settings_only(directory, data)` | Whether a `pyproject.toml` holds only `[tool]` tables, no `[tool.poetry]`, and has no `setup.py` or `setup.cfg` beside it. |
| `is_unpackaged(data)` | Whether a `pyproject.toml` is a Poetry 1 project with `package-mode = false` and no `[project]`. |
| `has_hash_option(text)` | Whether a non-comment line has a `--hash` option: uv's `--require-hashes` mode. |

Rules it enforces:

- Refused: a file that is neither `*.py` nor starts with a python or `uv run` shebang
  (`loose.python_shebang`), a `setup.cfg` alone, an unreadable `pyproject.toml`, and a script's
  `[tool.fsbpyr]` that is no table. A directory of C or E without Python file or lock source is
  `loose.NoPythonFiles`.
- The `uv.lock` is the project's or the nearest uv workspace's that claims it (`members`
  include the project, `exclude` does not); a nearer workspace that does not claim it ends the
  search. `--requirements FILE` must exist.
- `python_pin` is the first word of `.python-version`, the part after `@` for a uv-style
  `cpython@3.12`; for D the file is read in the script's directory.
- An invalid `requires-python` raises `ProjectError`; so does `check_python` when it excludes the
  runtime's version.
- A requirement is unpinned without `==` before any `;` marker, a commit SHA (`git+URL@<40 hex>`)
  or a `#sha256=` on its URL; an `-e` line with a local path is always unpinned, since it
  installs whatever the tree holds. Other option lines (`-r`, `--hash`) are no requirements, and
  a `--hash=` in a comment is no hash option.

- Imports: `loose` (lazy), `pipenv` (lazy), `poetry1` (lazy).
- Imported by: `build`, `cli` (lazy), `config`, `foreign`, `importcheck`, `loose`, `manifest`,
  `pipenv`, `poetry1`, `pyversion`, `staging`, `tools`, `venvdeps`.

### `config`

[`src/fsbpyr/config.py`](../src/fsbpyr/config.py)

`load_config` reads `[tool.fsbpyr]` (of `pyproject.toml`, of a `pyproject.toml` of settings, or of
a script's PEP 723 block); the options win. `CONFIG_KEYS`, 14 keys (counted in the source), is
the one list the reader, the unknown-key error and the man page use; `--offline`, `--hints`,
`--smoke`, `--signing-key` and `--verbose` have no key.

| Class | What it represents |
|---|---|
| `ConfigError` | An invalid `[tool.fsbpyr]` table or option. |
| `BuildConfig` | Every setting with its default (entry, output, requirements, extras, sources, offline, smoke, smoke arguments, allowed system libraries, fs-packages, hints, compression, interpreter re-execution, strict, Python, glibc); `zstd_level` is 3 with fast compression, else 9. |
| `ConfigKey` | One table key: the `BuildConfig` field it sets, how its value is read and checked, and its man page text. |

| Function | What it does |
|---|---|
| `load_config(project, **options)` | The table's values, overridden by every option that is not None. |

Rules it enforces:

- An unknown key, or a value of the wrong type, raises `ConfigError`; for a key it lists the
  supported ones.
- `allow-system-libs` entries must be file names (not empty, no `/`); `fs-packages` entries must
  be dotted identifiers; `output` is relative to the project directory; `glibc` must be one of
  `runtime.pins.BASELINES`.
- `fast-compression` and `best-compression` exclude each other; a compression flag on the
  command line drops the other one set in the table.

- Imports: `project`, `payload.spec`, `runtime.pins`.
- Imported by: `build`, `cli` (lazy), `manpage`.

### `pyversion`

[`src/fsbpyr/pyversion.py`](../src/fsbpyr/pyversion.py)

The Python a build uses, strongest first: `--python X.Y`, `python` in `[tool.fsbpyr]`, a
`.python-version` pin, `requires-python`: the lowest supported version above a lower bound, the
highest below an upper-only bound, a pinned minor version as pinned, else the building fsbpyr's
Python. A lock-source `Pipfile.lock`'s `_meta.requires` (`Project.lock_python`) counts as a
`requires-python`. A version is tested as the newest patch release the runtime pins have (so
`~=3.12.1` admits 3.12), or `X.Y.0`. For D `requires-python` is the script block's, for C the
intersection of the commands' blocks.

| Class | What it represents |
|---|---|
| `Choice` | The chosen `major.minor` and the rule's warnings. |

| Function | What it does |
|---|---|
| `supported()` | The versions fsbpyr builds for, oldest first (`bytecode.SUPPORTED_PYTHON`). |
| `resolve(project, requested, *, own)` | The `Choice` for `project`; `own` is the running fsbpyr's Python. |

Rules it enforces:

- `--python` and `python` must be `X.Y`, supported, and inside `requires-python`; otherwise
  `ProjectError`.
- A `.python-version` pin outside `requires-python` is a `ProjectError`; one that is not a
  supported CPython `X.Y` is ignored with a warning.
- A `requires-python` that allows no supported version is a `ProjectError` naming them.
- The `Pipfile.lock`'s version is ignored with a warning when the project does not allow it;
  `--python`, `python` and `.python-version` outrank it silently.

- Imports: `bytecode`, `project`, `runtime.pins`.
- Imported by: `build`, `cli` (lazy).

### `poetry1`

[`src/fsbpyr/poetry1.py`](../src/fsbpyr/poetry1.py)

A Poetry 1 project that is not packaged (`[tool.poetry] package-mode = false`, no `[project]`
table) keeps its name, version, dependencies and scripts in `[tool.poetry]`; this module turns
them into the `[project]` table of B. The lock still decides what is installed
([`foreign`](#foreign)).

| Function | What it does |
|---|---|
| `project_table(data)` | The `[project]` table: string `name` and `version`, `requires-python` from the `python` dependency, the main dependencies, `optional-dependencies` from `[tool.poetry.extras]`, and the string `scripts`. |

Rules it enforces:

- Poetry's constraints become PEP 440 specifiers: `^1.2` is `>=1.2,<2`, `~1.2` is `>=1.2,<1.3`,
  a bare version is `==`, `*` is any version. A caret or tilde takes a pre-, post- or
  dev-release or an epoch too, its bounds from the release numbers (`^1.2.0a1` is
  `>=1.2.0a1,<2`); a local label (`^1.2+local`) is refused, as a `>=` specifier cannot hold one. AND constraints are split on
  a comma or a space, as poetry-core splits them (`>=1.2 <2.0`). An `||` alternative raises
  `ProjectError`.
- A dependency's `extras`, `markers` and `python` constraint go into its requirement line, the
  `markers` parenthesised next to a `python` constraint so an `or` in it keeps apart.
- An `optional` dependency, with every alternative of one written as a list of tables, belongs
  to the extras of `[tool.poetry.extras]` that list it.
- A `git`, `path`, `url`, `develop` or `source` dependency, and an invalid requirement, raise
  `ProjectError` (add a `[project]` table, or lock the build with `--requirements`).

- Imports: `project`.
- Imported by: `loose`, `project` (lazy).

## Commands, scripts and tools

[`closure`](#closure) makes the table of commands; a start with several commands is on
[boot modules](Dev-Boot-Modules.md).

### `entry`

[`src/fsbpyr/entry.py`](../src/fsbpyr/entry.py)

Entry inference for a project with no console scripts: a top-level package with `__main__.py`
(run as `python -m pkg`), or a top-level module with a main guard, found with `ast`, never
imported.

| Class | What it represents |
|---|---|
| `Candidates` | The top-level packages with `__main__.py` and modules with a main guard; `described` lists them for an error message. |

| Function | What it does |
|---|---|
| `has_main_guard(source)` | Whether the module body has `if __name__ == "__main__":`, in either operand order; bytes are parsed as Python does, so a BOM or a coding cookie counts. |
| `installed_candidates(site, record)` | The `Candidates` of an installed distribution, from its `RECORD`. |
| `source_has_candidate(directory)` | Whether the project tree holds any `__main__.py` or guarded module. |

Rules: only module-level `if` statements count, and a file that does not parse has no guard.
`source_has_candidate` skips hidden directories, those in `SKIPPED_DIRECTORIES` (`build`,
`dist`, `node_modules`, `__pycache__`, `site-packages`, which [`loose`](#loose) skips too) and
virtual environments (a `pyvenv.cfg`), and never counts `setup.py`. `installed_candidates` takes
only identifiers and never the module `__main__`.

- Imports: nothing from fsbpyr.
- Imported by: `build`, `closure`, `loose`.

### `links`

[`src/fsbpyr/links.py`](../src/fsbpyr/links.py)

The links of a multi-command executable, `<dir>/<command>` pointing at the executable's name.
`COMMAND_NAME`, `[A-Za-z0-9][A-Za-z0-9_.+-]*`, is the rule every command name follows: a file
name and a word on a command line. A build that goes from one command to several, or back,
replaces its own earlier output, recognised by reading its payload.

| Class | What it represents |
|---|---|
| `LinkError` | A command's name is taken by a file that is neither a link nor an earlier output of the project. |

| Function | What it does |
|---|---|
| `link_paths(binary, commands)` | One link per command beside `binary`; a command named like the binary needs none. |
| `is_earlier_link(path, project)` | Whether `path` is a symlink to a bare name whose target is no link but an earlier output of `project`. |
| `check_links(binary, commands, project)` | Fails naming the first link path that a file or directory holds, unless it is an earlier output of `project` for that command. |
| `make_links(binary, commands, project)` | Links each command to the binary's name. |
| `deploy_recipe(binary, commands)` | How to install the build on a host: `install -m 755`, a `ln -sf` per command, `FSBPYR_INSTALL_LINKS` as the alternative, and that a line running `python3 $DEST/x.py` must run the link instead. |

Rules: a link names the binary by its name alone, so the directory can move. Each link is made
as `.<name>.<8 hex>.tmp` and renamed into place after a last check. An earlier output
(`_earlier_output`) is a regular file whose trailer and payload parse, whose app name is the
file's name, and whose build_info names the same project.

- Imports: `payload.format`, `payload.reader`.
- Imported by: `build`, `cli` (lazy), `closure`, `loose`.

### `loose`

[`src/fsbpyr/loose.py`](../src/fsbpyr/loose.py)

The wheel of B, C, D and the empty project of E (D5), written with the standard library: only the
project's Python files (never a `.env` or a key file), with their relative paths, under the
reserved root `_fsbpyr_scripts/` (`ROOT`). A script runs as `__main__` with its directory first
on `sys.path`, as under `uv run`. Commands that run a module as `__main__` are in the entry point
group `fsbpyr_scripts` (`MODULE_GROUP`), `module:function` ones in `console_scripts`.

| Class | What it represents |
|---|---|
| `NoPythonFiles` | A `ProjectError`: a directory without `pyproject.toml` (or with settings only) holds no Python file. |
| `Package` | A project's name, version, `METADATA` lines, sources by path, commands (`name` to a target below `ROOT`) and warnings; for C and D also each stored path's project file (`origins`), `requires-python` and the PEP 723 dependencies; `tools` for E. Constructors `load` and `tool_binary`; `with_dependencies`, `wheel_name`, `write_wheel`. |

| Function | What it does |
|---|---|
| `discover(directory)` | B's Python files (path to content, sorted) and warnings for files that cannot be imported. |
| `python_shebang(data)` | Whether the first line is a shebang that starts Python 3 (`python`, `python3`, `python3.12`, directly or through `env`) or `uv run`. |
| `directory_name(directory)` | A directory's project name, as `clean_name` makes it. |
| `clean_name(text)` | `text` as a canonical project name, what a name cannot hold replaced by `-` (`scripts` when nothing is left). |
| `script_stem(leaf, *, python)` | The module name a script file is stored under: `check-disk` becomes `check_disk`, an extensionless `tool` becomes `_x_tool`. |
| `discover_directory(directory)` | C's Python files (stored path to content, sorted), each stored path's project file, and warnings. |
| `inline_metadata(source, where)` | The TOML of a file's `# /// script` block (PEP 723), empty without one. |

What it reads:

- **B**: the `*.py` files; one whose name, or a directory above it, is no Python name is left
  out with a warning. `[project.scripts]` targets in the project move below `ROOT` (`main:run`
  runs `_fsbpyr_scripts.main:run`; a dependency's target stays as it is; a target in `src/`
  must be written `_fsbpyr_scripts.src.pkg.mod:attr`). Without them, one file with a main guard
  or a top-level `__main__.py` is a command named like the project; several are named after the
  file, `main.py` and `__main__.py` after the project.
- **C**: an extensionless file counts when its first 512 bytes start with a python or `uv run`
  shebang; names starting with a dot are skipped. A file or directory that is no Python name is
  stored under the name `script_stem` makes (`check_disk.py`, `_10_backup.py`, `sub_dir/`).
  Commands are named after the file (hyphens kept, `main` and `__main__` after the directory); a
  name that is no `COMMAND_NAME` stays a module, with a warning. The PEP 723 blocks'
  `dependencies` are joined; a `[tool.fsbpyr]` table in a block is ignored with a warning.
- **D**: the script under its module name and its static-import closure
  (`importcheck.local_closure`); nothing else of the directory is read. The stem must be a
  `COMMAND_NAME`.
- **E**: nothing; the name is `<name>-tools` when a requirement has the directory's name, so
  installing the package does not replace that distribution.

Rules it enforces:

- `/` and the home directory are no project directories (`ProjectError`).
- In a git work tree the files are the tracked ones (`vcs.work_tree_files`), and each untracked
  Python file git does not ignore is left out with a warning to `git add` or ignore it; without
  git a walk finds them. Hidden directories, `entry.SKIPPED_DIRECTORIES`, virtual environments
  (a `pyvenv.cfg`) and links into them are skipped.
- A link to a `*.py` file outside the directory fails the build; a link to another file outside
  it is passed over; links to directories are not followed.
- Two files or directories with one stored name, and two files that give one command, fail the
  build, naming both.
- Never a command: `setup.py`, `__init__.py`, test files (`conftest.py`, `test_*`, `*_test.py`,
  anything below a `test` or `tests` directory), and a `__main__.py` below the top level.
- Several `# /// script` blocks in a file, invalid TOML, `dependencies` that are not a list, an
  invalid requirement or `requires-python`, and blocks whose `requires-python` leave no Python
  version raise `ProjectError`.
- The version is `[project] version`, or `0`, and must be valid. `METADATA` (version 2.1) holds
  the name, version, `Requires-Python`, the dependencies and the extras with their `extra ==`
  markers.
- `write_wheel` sorts the entries and the `RECORD`, stamps every entry with the epoch it is given
  and gives every file mode 0644, so two writes of the same files are the same bytes. The tag is
  `py3-none-any`; the `WHEEL` file names `fsbpyr` as generator.

- Imports: `entry`, `importcheck` (lazy), `links`, `poetry1`, `project`, `vcs`.
- Imported by: `build`, `classify`, `closure`, `compiler`, `importcheck`, `legacy`, `project`
  (lazy), `staging`, `syspath`.

### `legacy`

[`src/fsbpyr/legacy.py`](../src/fsbpyr/legacy.py)

The files of `setup.py`'s `scripts=` (or setuptools' `script-files`) under
`<dist>.data/scripts/`. An installer rewrites their `#!python` line, so only the wheel tells
which are Python. Each Python one becomes a command that runs as `python script` would: stored
below `loose.ROOT` under a module name, run as `__main__` with that directory first on
`sys.path`.

| Function | What it does |
|---|---|
| `wheel_scripts(wheel)` | The files of the wheel's `.data/scripts/` whose first line is `#!python` (the wheel specification's rule; not `#!pythonw`), sorted. |
| `attach(dist, wheel, warnings)` | `dist` with those scripts as commands, each written below its site's scripts root and listed in its `RECORD`. |

Rules: a script that does not compile (Python 2, say) is left out with a warning; a name that is
also a console script, and two names that give one module (`loose.script_stem`), raise
`ClosureError` naming both. Another file under `bin/`, such as a shell script, is not packaged,
and `classify` warns naming it.

- Imports: `closure`, `loose`.
- Imported by: `build`.

### `tools`

[`src/fsbpyr/tools.py`](../src/fsbpyr/tools.py)

E: the lock is installed as for any project, and the console and gui scripts of the
distributions it names become the commands (pip and uv install gui scripts as ordinary launchers
on Linux).

| Class | What it represents |
|---|---|
| `Listed` | A distribution a lock source names: canonical name, extras asked for, and the marker under which uv installs it. |
| `Tools` | The commands (command to entry point), each one's distribution, the progress lines that say how the tools were chosen, and the listed distributions as the project's `Requires-Dist`. |

| Function | What it does |
|---|---|
| `listed_distributions(project, source, requirements)` | The distributions the lock source names, in file order. |
| `tool_scripts(index, listed, *, project, source, python_version)` | The tools' commands, after the install. |
| `with_commands(dist, tools)` | The project's distribution requiring the listed ones, with the tools' scripts as its console scripts. |

Rules it enforces:

- A requirements file names one distribution per requirement line, following `-r` files relative
  to the including file (one that includes itself is an error); `-c` files and other options name
  nothing. A URL line names the distribution of its `#egg=` fragment or wheel or sdist file name,
  else the build asks for `name @ URL`. A `pylock.toml` names its `[[packages]]`; a
  `Pipfile.lock` the Pipfile's `[packages]`, or its `default` entries (`pipenv.tool_names`).
- A listed distribution whose marker is false is no tool; one not installed and without a marker
  is a `ClosureError`. The extras listed items ask for count as active in markers. A listed
  item's marker that cannot be evaluated counts as true; a `Requires-Dist` marker that cannot
  be evaluated is a `ClosureError` naming the distribution.
- A file that lists every installed distribution (but the project, and `pip`, `setuptools`,
  `wheel` and `distribute`, which `pip freeze` leaves out) is a freeze: the tools are the listed
  distributions no other listed one requires under the runtime's markers. Otherwise every listed
  distribution is a tool.
- A tool without a script is replaced by its direct requirements when it installs no code (a
  meta-package), else by those named `<tool>-...`, down to distributions with scripts, stopping at
  a cycle or eight levels; one with code that gives nothing is reported as a library.
- A tool also gets the commands of its family, its requirements named `<tool>-...`, recursively.
  A dependency's scripts are otherwise never commands.
- Two tools (or a tool and its family) with one script name, console or gui, raise `ClosureError`
  naming both distributions; no script at all raises one saying what the file named.
- The build prints the tools it took and why, and the commands with their distributions and
  versions.

- Imports: `closure`, `foreign`, `pipenv`, `project`, `text`.
- Imported by: `build`.

## Locks of other tools, inferred dependencies

uv installs everything (D26), so a Poetry, PDM or Pipenv lock is converted, in the staging
directory, into a pip requirements file. The user-facing side is on
[Building](User-Building.md).

### `foreign`

[`src/fsbpyr/foreign.py`](../src/fsbpyr/foreign.py)

`poetry.lock` and `pdm.lock` (D5). The main (Poetry) or default (PDM) group, plus the project's
extras, become requirement lines with the markers evaluated for the target and every hash the
lock records. A Poetry lock without groups (lock-version 2.0, Poetry 1.5 to 1.8) is walked from
the project's own dependencies.

| Class | What it represents |
|---|---|
| `ForeignLockError` | A `ProjectError`: a lock fsbpyr cannot turn into a locked build. |
| `Edge` | A dependency of a locked package, as Poetry records it: name, specifier, marker, optional, extras. |
| `Entry` | One locked package, also for `pipenv`: name, version, kind (`registry`, `index`, `local`, `path`, `git`, `url`), origin, commit, subdirectory, files with SHA-256, groups, marker, optional, dependencies, extras, unnamed files' hashes (`Pipfile.lock`). |
| `Export` | The requirements file, whether every line carries a hash, and the line count. |

| Function | What it does |
|---|---|
| `read_entries(source, data)` | The `Entry` list of a parsed Poetry or PDM lock. |
| `export(source, directory, *, environment, extras)` | The lock's requirements file for the target's markers and the project's extras. |
| `stale(source, directory)` | A message when the lock's content hash is not `pyproject.toml`'s; None when it matches or there is nothing to compare. |

Rules it enforces:

- Poetry lock-versions 1.x (`category`, hashes in `[metadata.files]`), 2.0 (no groups or
  markers) and 2.1 (a per-group marker is the `main` group's). The roots are the `main` packages
  that are not optional and whose marker holds, or without groups `[tool.poetry.dependencies]`
  (whose `python` constraint must be a PEP 440 specifier) and `[project.dependencies]`, plus what
  the lock's `[extras]` lists for the extras asked for (one it lacks is an error). The recorded
  dependencies are followed under the target's markers, an optional one only for an active
  extra.
- PDM: the `default` group and the groups named like the extras asked for; a lock without groups
  per package must hold the default group alone (else: lock with `--strategy inherit_metadata`,
  or pass `--requirements`). An entry for a package with extras adds nothing; locks 4.0 to 4.2
  keep the hashes in `[metadata.files]`.
- A line is exact: `name==version` with every hash, a path (absolute; `${PROJECT_ROOT}` filled
  in) as a regular `name @ file:...` that must exist, a VCS entry by its commit
  (`name @ git+URL@commit`, with its subdirectory), a URL with `#sha256=` when the lock has it.
- Refused, naming the entry: a package from another index (its credentials are not in the lock),
  a VCS entry without a commit, a missing path, a name, version or source with white space, a
  marker that cannot be evaluated (of a package, a dependency or a dependency of the project),
  and a package locked at two versions that both apply to the target.
- `stale` compares Poetry's `content-hash` with what `_poetry_hash` computes from the parts of
  `pyproject.toml` Poetry hashes, and PDM's `content_hash` with both formulas PDM has used (with
  and without `dependency-groups`).

- Imports: `closure`, `project`.
- Imported by: `build`, `manifest`, `pipenv`, `staging`, `tools`.

### `pipenv`

[`src/fsbpyr/pipenv.py`](../src/fsbpyr/pipenv.py)

`Pipfile.lock` (D5), for a project or a directory of scripts. Its `default` section (never
`develop`) becomes the same hashed requirements file as a Poetry or PDM lock; the lock is flat
and complete, so there are no groups or extras to choose.

| Function | What it does |
|---|---|
| `read(directory)` | The parsed `Pipfile.lock`; `ForeignLockError` for one that cannot be read or has no `default` section. |
| `python_of(lock)` | The minor version `_meta.requires` asks for (`python_full_version` or `python_version`), if any. |
| `entries(data)` | The `default` packages as `foreign.Entry` objects. |
| `export(directory, *, environment)` | The lock's requirements file, for the target's markers. |
| `stale(directory)` | A message when `_meta.hash` is not the Pipfile's hash, or, without a Pipfile, when the `pyproject.toml` beside the lock declares dependencies the lock replaces. |
| `other_indexes(directory)` | A note on the packages the lock takes from an index other than PyPI. |
| `tool_names(directory)` | The tools of E: the Pipfile's `[packages]` with their extras and markers, else the lock's `default` entries. |

Rules it enforces:

- A registry entry must be pinned with `==` (not `===`); a `git` entry needs a commit `ref` (40 or
  64 hex digits); `hg`, `svn` and `bzr` are refused; a `path` entry installs as a regular package,
  except the project itself (`pipenv install -e .`); a `file` entry keeps its hash when the lock
  gives exactly one.
- Extras that the entries the target installs ask of their dependencies count as active in
  markers (Pipenv marks what an extra needs `extra == 'x'`), until they bring in no other entry;
  an entry whose own marker is false asks for none. A marker that cannot be evaluated is
  refused naming the entry.
- A package from an index other than PyPI is installed from the indexes uv is configured with;
  the lock's URL is never passed on (it may name credentials by variable), and the lock's hashes
  decide whether uv found the same files. The note is `Uncounted`.
- The Pipfile's hash follows `Pipfile.get_hash` of Pipenv's `plette` (`_pipfile_hash`): the
  SHA-256 of the sources, the `requires` and the package tables as sorted compact JSON. The
  `foreign` fixture's
  [README](../tests/fixtures/projects/foreign/README.md)
  notes that the formula has not been checked against a lock that Pipenv wrote.

- Imports: `closure`, `foreign`, `project`.
- Imported by: `build`, `manifest`, `project` (lazy), `staging`, `tools`.

### `venvdeps`

[`src/fsbpyr/venvdeps.py`](../src/fsbpyr/venvdeps.py)

The venv rung of step 9. The venv's `*.dist-info` files are read, never executed: `METADATA`,
`top_level.txt` and `RECORD` say which distribution provides which import, `direct_url.json`
whether it is the user's own tree. The providing distributions and their installed
`Requires-Dist` closure become `name==version` pins; nothing is copied, and no PyPI name is
guessed from an import name.

| Class | What it represents |
|---|---|
| `VenvDist` | An installed distribution: name, version, `Requires-Dist`, the top-level import names it provides, its `RECORD` paths, and the PEP 610 URL of an editable or path install. |
| `Inferred` | The venv and the pins read from it. |

| Function | What it does |
|---|---|
| `find_venv(directory, environ)` | `.venv`, `venv` or `env` in the directory, else `$VIRTUAL_ENV`; each needs a `pyvenv.cfg`. |
| `read_venv(venv)` | The distributions of the venv's `lib*/python3*/site-packages`, by canonical name. |
| `infer(venv, modules, environment)` | The pins for the unresolved imports `modules`, or None when no installed distribution provides any. |

Rules it enforces:

- A distribution provides an import when `top_level.txt` or the first part of a `RECORD` path
  names it; when several do (a namespace), those whose `RECORD` holds the module itself win.
- The closure follows `Requires-Dist` lines whose marker holds for the target, not extras; a
  requirement the venv lacks is left to uv, and a marker that cannot be evaluated raises
  `ProjectError` naming the distribution.
- A distribution with an invalid name or version, or a name not its directory's, is skipped; a
  needed editable or path install raises `ProjectError`: it is the user's own tree.

- Imports: `closure`, `project`.
- Imported by: `build`, `manifest`.

## Static import check

A script of B, C or D imports what its developer's environment had; the executable has what the
project declares.

### `importcheck`

[`src/fsbpyr/importcheck.py`](../src/fsbpyr/importcheck.py)

For each command, follows with `ast` the imports of the script and the project modules it
reaches, and reports every required import found neither in the script's directory (first), the
installed distributions, the runtime, nor the directories files add to `sys.path`.

| Class | What it represents |
|---|---|
| `Unresolved` | An import nothing resolves: project file, line, the module as written (a relative import with its dots), and why when it is not just missing; `describe` gives `file:line: ...`. |

The private classes `_Project` (the project's files below the scripts root), `_Search` (where one
command's imports are looked up) and `_Disk` (the files next to a script, on disk) do the
lookups.

| Function | What it does |
|---|---|
| `top_level_names(site)` | The modules and packages a site directory provides, metadata apart. |
| `check_command(target, sources, known)` | The required imports of the command `target` that nothing resolves; `known` is the top-level names of the installed distributions and the runtime. |
| `local_closure(directory, payload, source, origin)` | D's files: the script, stored as `payload`, and the sibling modules and packages it reaches by static imports, transitively, read from disk. |

`REMOVED_IN` maps the standard library modules removed in Python 3.12 (5) and 3.13 (20, counted
in the source) to the version that removed them, for `build`'s hint.

Rules it enforces:

- An import is optional under a `try` whose handlers catch `ImportError`, `ModuleNotFoundError`,
  `Exception`, `BaseException` or anything, in such handlers, under `if TYPE_CHECKING:`, and in
  both branches of an `if` that tests `sys.platform`, `os.name` or `sys.version_info`.
- A relative import resolves from the file's package, and nothing above the scripts root.
  `from pkg import name` follows `name` as a submodule when the project has it. A name starting
  with `_fsbpyr_scripts` is looked up from the root.
- The directories files add to `sys.path` (`syspath.path_entries`) are searched last, in the
  order found; a missing import is tried again whenever a new one is found, until nothing
  changes.
- When a file reached changes `sys.path` in a way that cannot be followed, the command's
  unresolved imports are not reported; a file that does not parse always is. A dependency's
  script has nothing to follow.
- `local_closure` touches only the paths an import names, so a script in a home directory walks
  nothing; it skips what `loose` skips, and refuses a module that links to a file outside the
  directory and a sibling that would be stored under the script's own stored name.

- Imports: `loose`, `project`, `syspath`.
- Imported by: `build`, `loose` (lazy).

### `syspath`

[`src/fsbpyr/syspath.py`](../src/fsbpyr/syspath.py)

The directories a script puts on `sys.path` with a path built from its own `__file__` (the common
`sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pylib"))`). A path
that cannot be followed may hold anything; one the payload cannot serve holds nothing. `..` is
followed as the executable's path hook follows it, never above the executable; a directory that
is no Python name is searched under its stored name.

| Function | What it does |
|---|---|
| `path_entries(tree, file)` | The directories below the scripts root that the file adds to `sys.path`, and whether it changes `sys.path` in any other way. |

Rules: the calls are `sys.path.append`, `insert` and `extend` anywhere in the file (with a list or
tuple, each element). A path is followed through `__file__`, `os.path.dirname`, `os.path.join`
with string literals, `/` and `+` with string literals, `.parent`, `.resolve()`, `.absolute()`,
and `str`, `os.fspath`, `Path`, `pathlib.Path`, `PurePath`, `os.path.abspath`, `os.path.realpath`
and `os.path.normpath` of one argument. Anything else on such a call, and an assignment to
`sys.path`, is a change it cannot follow. A path that `..` takes above the scripts root is
neither found nor a change.

- Imports: `loose`.
- Imported by: `importcheck`.

## Staging with uv

### `staging`

[`src/fsbpyr/staging.py`](../src/fsbpyr/staging.py)

The staging install (D5): the project installed afresh by uv (D26), pip projects too, so every
build picks wheels for the glibc baseline; the developer's environment is never read or
modified. A is built out of tree (an sdist, then a wheel), so stale in-tree build directories
and cached builds cannot leak in. Commands run in the project directory, so relative paths in
requirements files resolve as the user expects.

| Lock source | uv commands after the wheel is made (`uv build --out-dir WORK/wheel --python X.Y PROJECT`, or `write_wheel`) |
|---|---|
| `uv.lock` | `uv sync --locked --no-dev --no-default-groups --no-editable --no-install-project` into the staging environment (`UV_PROJECT_ENVIRONMENT`), with `--package` for a workspace member, `--reinstall-package` for each local package of the lock and `--extra` for each extra; then `uv pip install --no-deps` the wheel |
| `script.lock` | `uv sync --script SCRIPT --active --locked` into the staging environment (`VIRTUAL_ENV`); then `uv pip install --no-deps` the wheel |
| `pylock.toml` | `uv venv`, `uv pip install -r pylock.toml`, `uv pip install --no-deps` the wheel |
| requirements file | as `pylock.toml`, with `--no-editable`, and `--require-hashes` under `--strict` or when the file has `--hash` options |
| `poetry.lock`, `pdm.lock`, `Pipfile.lock` | as a requirements file, from `WORK/<lock>.requirements.txt` written by `foreign.export` or `pipenv.export`; `--require-hashes` when every line carries a hash |
| unlocked, inferred | `uv venv`, then `uv pip install` the wheel with its extras, resolving the dependencies now (for inferred, the pins the wheel requires) |

Every install command gets `--python-platform` with the target's uv platform for the baseline
(`x86_64-manylinux_2_34`, or `x86_64-manylinux_2_28` for glibc 2.28, and likewise on aarch64),
`--offline` for an offline build, and `--quiet`; `uv build` is not quiet, so a failure carries
the build backend's reason. `uv venv` runs with `UV_VENV_SEED=0`, since a seeded environment
would ship pip.

| Class | What it represents |
|---|---|
| `StagingError` | The staging install could not be planned, or failed. |
| `StagingOptions` | The runtime's Python, extras, offline, requirements file, uv platform, strict, the `loose.Package`, and the target's markers for converted locks. |
| `StagingPlan` | The site-packages path, the wheel build command (empty when fsbpyr writes the wheel), the install commands, the wheel directory, extra environment, the command that drops uv's cached builds, and the package. |

| Function | What it does |
|---|---|
| `find_uv()` | uv on `PATH`, or `StagingError` with how to install it. |
| `uv_version(uv)` | uv's version string. |
| `local_packages(lock, exclude)` | Packages a `uv.lock` builds from local sources, reinstalled every build because uv caches such builds by their `pyproject.toml`, not their sources. |
| `staging_plan(project, source, uv, work, options)` | The plan for the lock source, with `SOURCE_DATE_EPOCH` added. |
| `leave_tree_unchanged(project_dir)` | A context manager that removes the `*.egg-info` (in the project directory and one level below) and `build/` a build created in the source tree, never what existed before. |
| `rebuild_wheel(plan, project_dir, env, *, log, verbose)` | The project's wheel built again with `env` added, into a new directory. |
| `rebuild(plan, project_dir, wheel, names, env, *, log, verbose)` | Installs `names` again, built anew from their sdists with `env` added, after dropping uv's cached builds of them. |
| `run_plan(plan, project_dir, *, log, verbose)` | Makes the wheel, runs the installs, returns the wheel. |

Rules it enforces:

- `SOURCE_DATE_EPOCH` is the user's when set, else the project's last commit time
  (`vcs.commit_time`), never before 315532800 (1980-01-01, the earliest a zip entry can hold);
  a value that is not a number of seconds is a `StagingError`.
- A `uv.lock` project needs `[project] name`; `uv build` must leave exactly one wheel, and runs
  inside `leave_tree_unchanged`.
- A failing uv command, or a `uv --version` without a version, raises `StagingError` naming the
  step, with the end of uv's output (3000 characters). `--verbose` prints each command and passes
  uv's standard error through.
- `rebuild` skips `uv venv` and adds `--reinstall-package` for each name; uv builds before it
  installs, so a failed rebuild leaves the staged site as it was.

- Imports: `closure`, `foreign`, `loose`, `pipenv`, `project`, `text`, `vcs`.
- Imported by: `build`, `cli` (lazy).

## What the app contains and needs

The records these modules produce are on [payload modules](Dev-Payload-Modules.md) and
[executable format](Dev-Executable-Format.md).

### `closure`

[`src/fsbpyr/closure.py`](../src/fsbpyr/closure.py)

`SiteIndex.scan` reads each `*.dist-info` (`METADATA`, `RECORD`, `entry_points.txt`,
`direct_url.json`) and finds the project by name, else by the PEP 610 URL of its directory.
`walk` is a consistency check: it fails on a missing dependency, warns about a version that does
not satisfy its requirement, and lists the distributions nothing requires.

| Class | What it represents |
|---|---|
| `ClosureError` | The staging site does not hold a consistent installation of the project. |
| `InstalledDist` | One distribution: name, version, dist-info directory, requirements, `RECORD` lines, entry points, PEP 610 data, extras, legacy scripts. `console_scripts()` and `gui_scripts()` drop each `[extras]` suffix; `commands()` adds `loose`'s `fsbpyr_scripts` group and the legacy scripts to the console scripts. |
| `Walk` | Reachable and unreachable distributions, and warnings. |
| `SiteIndex` | The site and its distributions by name: `scan`, `check_no_editable`, `project`, `walk`. |

| Function | What it does |
|---|---|
| `marker_environment(python_version)` | This host's marker environment with the runtime's Python. |
| `marker_holds(marker, environment, extras, error, where)` | Whether a marker holds with one of the extras; one that `packaging` parses but cannot evaluate (`~=` on a string, `extras`) raises the caller's `error`, naming `where`. |
| `requirement_of(line, dist)` | A `Requires-Dist` line parsed; `ClosureError` when invalid. |
| `several_scripts_error(name, scripts, candidates)` | The error for no `--entry` and no entry to take, listing the console scripts and inferred candidates. |
| `check_command_names(names)` | `ClosureError` for a name that is not a `links.COMMAND_NAME`, of one command as of several. |
| `command_table(scripts)` | The entry point of several commands: `name=target` lines, sorted by name. |
| `unknown_extras_error(name, wanted, known)` | The message for requested extras the project lacks, else None. |
| `infer_entry(dist)` | (app name, entry point, why) from the only top-level `__main__` package, else the only top-level guarded module (`entry.installed_candidates`). |
| `resolve_entry(dist, entry, log, omit)` | (app name, entry point) from `--entry`, the only command, the command table (named after the distribution) or `infer_entry`, which it logs; the commands in `omit` are left out. |

Rules it enforces:

- An editable install, a required distribution missing from the site, an invalid
  `Requires-Dist` and one whose marker cannot be evaluated raise `ClosureError`.
- `--entry` must be a command name, or `module[:attribute]` made of identifiers, whose last part
  names the app.
- Several inferred candidates, or a package and a module, raise the "choose one with --entry"
  error, which lists them.

- Imports: `entry`, `links`, `loose`.
- Imported by: `build`, `classify`, `cli` (lazy), `foreign`, `legacy`, `manifest`, `pipenv`,
  `staging`, `tools`, `venvdeps`.

### `classify`

[`src/fsbpyr/classify.py`](../src/fsbpyr/classify.py)

Each `RECORD` file becomes a module (an importable `.py`, compiled by `compiler`), an extension
module or shared library (an ELF object for the host's machine), a resource, or metadata, in
CPython's search order: site-packages, then the directories top-level `.pth` files add, a
package before a module, an extension suffix before `.py`. Implicit namespace packages are
added. The result is sorted by name and path.

| Class | What it represents |
|---|---|
| `ClassifyError` | The installed files cannot be packaged. |
| `Uncounted` | A `str` subclass for warnings that `--strict` does not count. |
| `Content` | Modules, files, distributions, warnings, the newest glibc need of a native file (and which), and `natives.SystemUse`. |

The private class `_Collector` holds the state and does the work.

| Function | What it does |
|---|---|
| `module_name(rel)` | Dotted name and is-package flag of an importable `.py` path; the stem may hold hyphens or start with a digit, as `importlib.import_module` reaches it. |
| `extension_name(rel, suffixes)` | Dotted name, is-package flag and suffix rank of an extension module; the stem may start with a digit but never holds a hyphen. |
| `classify(dists, *, extension_suffixes, include_source, ..., project, origins, entrypoint)` | The whole classification; with a spill, no file's bytes stay in memory. `origins` maps stored script paths to project files (C and D); `entrypoint` names the modules that run as `__main__`. |

`INSTALLER_FILES` (`INSTALLER`, `REQUESTED`, `direct_url.json`, `uv_cache.json`, `RECORD.jws`,
`RECORD.p7s`) are left out because they embed paths, times or the installer's name.
`BUILTIN_FS_PACKAGES` (babel, botocore, certifi, litellm, matplotlib, torch) always get their
files on disk.

Rules it enforces:

- Left out: files outside site-packages (a warning counts them by directory; `bin/` scripts
  silently, except a project file under `bin/` that is neither a console or gui script nor a
  `#!python` legacy script, which is named), `__pycache__` and `.pyc` files (bytecode without
  source gets a warning), and a path a second distribution installs again.
- The payload's `RECORD` lists only the packaged entries.
- A `.py` file that does not compile, or is over 16 MiB, is kept as data with a warning; a
  package whose `__init__.py` does not compile gets no namespace stand-in. Any file over 16 MiB is
  compressed a piece at a time. A project `.py` file below a non-Python directory name is stored
  as a file, with a warning that no import reaches it.
- `SyntaxWarning`s and `DeprecationWarning`s from compiling the project's own modules are build
  warnings, `file:line: Category: message`, with a script's project file name.
- A non-ELF file, or an ELF object for another machine, with a shared-object name is kept as data
  with a warning.
- A module the runtime provides (standard library, built-in, frozen, boot) is packaged as a file
  with a warning, unless the project's own distribution installs it; then `assemble` refuses the
  build.
- `.pth` path lines become module roots (one outside site-packages is warned about); code lines
  never run, with a warning unless the file only recreates namespace packages (`-nspkg.pth`,
  `distutils-precedence.pth`).
- Resources executable in the wheel and native files get mode 0755, other files 0644, whatever
  the installer's umask.
- An `fs-packages` entry that is no regular package of the app raises `ClassifyError`; every
  non-native file below a marked package is flagged `FS_REQUIRED`. A `NativeError` from
  `natives.closures` becomes `ClassifyError`.
- Hints: each read next to `__file__` in a file under `_fsbpyr_scripts/` is a warning to deploy
  that file beside the executable; a main guard counts only in the modules run as `__main__`
  (`_mains`: entry point targets below the scripts root that name a module). `Uncounted`: with
  `--hints`, packages that read files next to `__file__` (not in `tests`/`test`); always, shared
  libraries no extension loads that Python code names, and ELF executables among the resources
  outside fs-packages.

- Imports: `closure`, `compiler`, `elf`, `loose`, `natives`, `boot._fsbpyr_rt.commands`,
  `payload.format`, `payload.spec`, `payload.spill`.
- Imported by: `build`, `cli` (lazy).

### `scan`

[`src/fsbpyr/scan.py`](../src/fsbpyr/scan.py)

A module's `__file__` points into the payload, so code that reads files next to it fails unless
its package is in fs-packages. `file_relative_reads` flags calls that read files, run a program
or load a library, and pathlib reads, whose argument or receiver mentions `__file__` or
`__path__` (also `X.__file__`), directly or through a name assigned from it.

| Function | What it does |
|---|---|
| `file_relative_reads(source, filename, *, script, main)` | Line numbers of reads relative to `__file__`; `main` counts the main guard, `script` (a module of the scripts root) makes a call that starts `sys.executable` no read. |
| `loaded_libraries(source, filename)` | Library names opened by a literal with ctypes or cffi: `CDLL("libmagic.so.1")` gives the name, `find_library("magic")` gives `libmagic`; a path is left out. |

Rules: a module without the marker strings is not parsed, and one that does not parse gives
nothing; calls in a top-level main guard are ignored unless `main`: the body of
`if __name__ == "__main__":` (not its `else` or `elif`) and the `else` of
`if __name__ != "__main__":`, which never run on import; with `script`, a
`subprocess.run`, `Popen`, `call`, `check_call`, `check_output` or `os.exec*` call whose program
is `sys.executable` (its first argument or `args=`, or the first element of a list or tuple
there) is no read, since it runs a sibling from the payload; names assigned from a tainted
expression are followed for at most 5 passes; jinja2's `PackageLoader` is flagged by the call
alone.

- Imports: nothing from fsbpyr.
- Imported by: `compiler`.

### `natives`

[`src/fsbpyr/natives.py`](../src/fsbpyr/natives.py)

Every `DT_NEEDED` entry of every shared object in the payload must resolve to a payload file,
through RUNPATH (or RPATH; without a RUNPATH also the loaders' RPATHs, as in `ld.so`) with
`$ORIGIN` expanded, or to a system library manylinux wheels may rely on. Bundled libraries in
its directory or on its search path whose file name it contains as a C string count as loaded
(`dlopen`). A library
not on the search path is taken by SONAME or file name from the one other distribution that
bundles it. The native cache mirrors site-packages (D9), so `$ORIGIN` paths keep working.

| Class | What it represents |
|---|---|
| `NativeError` | A native dependency cannot be satisfied inside the executable or by the system. |
| `MissingLibraries` | The libraries one native file needs that are neither bundled nor allowed; `closures` reports them for all files together. |
| `SystemUse` | Which files need each system library, the symbol versions they need, the allowlist entries used, unreadable version tables, and `--hints`' guesses (`may_load`). |

| Function | What it does |
|---|---|
| `system_libraries()` | The auditwheel manylinux list in `_SYSTEM_LIBRARIES` plus the host's dynamic loader. |
| `glibc_libraries()` | The libraries that are glibc itself, plus the loader. |
| `manylinux_tag(baseline)` | `manylinux_2_34` for `2.34`, `manylinux_2_28` for `2.28`. |
| `manylinux_limits(baseline)` | The newest GLIBCXX, CXXABI, GCC and ZLIB versions auditwheel's policy for the baseline allows on the host's target (`MANYLINUX_LIMITS`). |
| `opened(native)` | A native file's content as given, or its file mapped read-only, never read whole (D38). |
| `init_symbol(path)` | The `PyInit_<name>` C string CPython looks up; a compiled package `__init__` takes the package's name. |
| `is_extension_module(path, data)` | Whether a shared object contains that symbol name. |
| `closures(natives, extensions, *, allowed, warn, system, executables)` | For each extension module, the bundled shared objects it loads, transitively. |

`MANYLINUX_LIMITS` comes from auditwheel's policy file;
`BASELINE_SYSTEMS` names the systems in `system`'s warnings; `MANYLINUX_MARKERS` are the
non-numeric CXXABI versions that need no warning (`CXXABI_TM_1`, `CXXABI_FLOAT128`):

| Baseline | Policy | GLIBCXX | CXXABI | GCC (libgcc_s) | ZLIB | Systems in warnings |
|---|---|---|---|---|---|---|
| 2.34, the default | manylinux_2_34 | 3.4.29 | 1.3.13 | 7.0.0 on x86-64, 11.0 on aarch64 | 1.2.9 | RHEL 9, Ubuntu 22.04 |
| 2.28 | manylinux_2_28 | 3.4.24 | 1.3.11 | 7.0.0 | 1.2.9 | RHEL 8 |

Rules it enforces:

- An extension linked against `libpython3*` is refused (it must take the symbols from the
  interpreter), and so is a `DT_NEEDED` entry with a `/`, which `ld.so` would open relative to the
  working directory.
- `MissingLibraries` fail the build with one message listing every file, what it needs, and the
  `[tool.fsbpyr]` line that would allow them.
- Two bundled libraries of one name in different top-level directories fail the build: they would
  load as one.
- A library reached only by name whose own needs cannot be met is left out with a warning.
- Shared objects no extension reaches, and ELF executables among the resources, get warnings
  instead of errors. RPATH and RUNPATH entries not based on `$ORIGIN` (`ld.so` searches them on
  the target), an `allow-system-libs` entry nothing needs and an unreadable version table are
  warned about.
- Searches go through a file 64 MiB at a time, dropping a mapping's pages after each window.

- Imports: `elf`, `targets`, `runtime.pins`.
- Imported by: `build`, `classify`, `inspection`, `system`.

### `elf`

[`src/fsbpyr/elf.py`](../src/fsbpyr/elf.py)

A minimal ELF64 reader, on bytes or a memory-mapped file, so a large library is never read whole.

| Class | What it represents |
|---|---|
| `ElfError` | Not a usable ELF64 object of the host's architecture. |
| `DynamicInfo` | `needed`, `rpath`, `runpath` and `soname` of an object. |
| `VersionNeed` | What an object needs from one version namespace of one library: the newest numeric version and the other markers. |

| Function | What it does |
|---|---|
| `is_elf(data)` | Whether the data starts with the ELF magic. |
| `is_foreign(head)` | Whether an ELF object is for another machine, 32-bit or big-endian; False when the header is too short to tell. |
| `is_executable(data)` | Whether an object is a program: `ET_EXEC`, or position-independent with a `PT_INTERP`. |
| `section(data, name)` | A section's content (the stub's `.comment` for `inspection`, `zstd_common.o`'s strings for `runtime.pbs`). |
| `dynamic_info(data)` | The `DynamicInfo` of an object (`DT_NEEDED`, `DT_RPATH`, `DT_RUNPATH`, `DT_SONAME`). |
| `needed_versions(data)` | The (library, version) pairs of `DT_VERNEED`. |
| `glibc_requirement(data)` | The newest glibc an object needs; `GLIBC_2.2.5` counts as 2.2, and `GLIBC_ABI_DT_RELR` as 2.36. |
| `version_requirements(data)` | Per library and namespace (GLIBCXX, CXXABI, GCC, ...), the newest version and the markers. |

The private `_Dynamic` holds one object's parsed dynamic section.

Rules: only 64-bit little-endian objects for the host's `e_machine` are read; anything else, a
truncated table, an address not mapped from the file or a string out of range raises `ElfError`.
Entry counts, name lengths and search-path lengths have fixed limits (`_MAX_ENTRIES`,
`_MAX_NAME`, `_MAX_SEARCH_PATH`), and the version-need entries share one budget, so overlapping
chains cannot multiply the work.

- Imports: `targets`.
- Imported by: `build`, `classify`, `inspection`, `natives`, `system`, `runtime.pbs`.

### `system`

[`src/fsbpyr/system.py`](../src/fsbpyr/system.py)

The libraries the target must provide beyond glibc (which `build.glibc_report` covers) and what
is bundled: libstdc++, libgcc_s, libz, X11 and the like, and the stub's own needs, shown as
`(stub)`, checked against the limits under [`natives`](#natives).

| Function | What it does |
|---|---|
| `target_needs(libraries, glibc)` | The one-line summary: `glibc >= X.Y` and each non-glibc library with its versions. |
| `system_report(stub, use, baseline)` | build_info's library table (per library: the files that need it, the newest version per namespace, markers) and the warnings. |
| `system_info(libraries, use)` | build_info's `system`: the libraries and, with `--hints`, those Python code may open by name. |

Rules: one warning per library and namespace above the limit, naming the newest need, how many
other files go over, and the policy's systems; one for any file that needs `GLIBC_PRIVATE` or a
non-numeric version the policy does not list. A stub that cannot be read adds nothing.

- Imports: `elf`, `natives`, `runtime.pins`.
- Imported by: `build`, `inspection`.

## Compiling to bytecode

The bytes must not depend on the build interpreter's state or on how the work is spread over
processes (D15, D16, D39).

### `compiler`

[`src/fsbpyr/compiler.py`](../src/fsbpyr/compiler.py)

Modules are compiled, canonicalized and compressed ahead of the classifier (D39) on up to 8
worker processes (the GIL keeps threads from compiling), as are the files it reads whole, and
handed over in the order it asks, so its output is the same on any number of cores. Fewer than
256 modules (`POOL_MODULES`), or one usable CPU, compile in the build's own process.

| Class | What it represents |
|---|---|
| `Compiled` | A module's code and source blobs and digests, its reads next to `__file__`, the libraries it opens by name, the compile warnings; or the syntax error. |
| `Job` | One module: (distribution, path) key, file, relative path, whether its reads next to `__file__` are wanted, whether it runs as `__main__`. |
| `Compiler` | The jobs compiled ahead; `get` returns them in order, `compress` compresses a file on a worker, `close` stops the pool. |

| Function | What it does |
|---|---|
| `compile_module(source, rel)` | Canonical bytecode: `compile(..., optimize=0, dont_inherit=True)` then `bytecode.dumps`, whatever the build interpreter's `-O` (D15). |
| `compile_warned(source, rel)` | That bytecode and the `SyntaxWarning`s and `DeprecationWarning`s compiling emitted, as (line, `Category: message`). |
| `compiled(data, rel, level, include_source, hints, main)` | A `Compiled` for one module, with the `scan` results when asked; a module below `loose.ROOT` is scanned as a script. |
| `worker_pool()` | Up to 8 workers: forked when the process runs one thread, else started by a fork server. |
| `compressed(data, level)` | A file's zstd blob and content digest. |

Rules: `compile` gets a fresh copy of the filename, since marshal writes an interned string
differently; compile warnings never change the bytecode; at most 4 jobs per worker and 64 MiB of
sources are in flight; a skipped job is cancelled, one not ahead compiled in the build's
process. A fork server imports the main module again, so a program that builds from several
threads must keep its main module free of side effects.

- Imports: `bytecode`, `loose`, `scan`, `payload.format`, `payload.spec`.
- Imported by: `classify`, `runtime.stdlib`.

### `bytecode`

[`src/fsbpyr/bytecode.py`](../src/fsbpyr/bytecode.py)

Canonical marshal encoding of code objects (D16). `marshal.dumps` depends on the process:
`FLAG_REF` is set on any object whose reference count is above one, and whether a string constant
is interned depends on equal strings interned earlier. `canonicalize` parses a marshal stream into
an object graph and writes it again with a policy that depends only on the graph:

- strings are merged by value, and a merged string is interned if any occurrence was;
- equal immutable values (numbers, bytes, strings, and tuples and frozensets of them) are one node;
- `FLAG_REF` is set exactly when a node occurs at least twice, or is an interned string;
- reference indices are assigned in pre-order, the order the reader uses;
- set and frozenset elements are sorted by their canonical standalone encoding.

`marshal.loads` of the result equals the original. Marshal versions 4 (3.12, 3.13), 5 (3.14,
adding the slice type) and 6 (3.15) are supported, each fsbpyr its own Python's.

| Class | What it represents |
|---|---|
| `BytecodeError` | The marshal stream is malformed or uses an unsupported type. |

The private classes `_Node`, `_Reader` and `_Writer` are the graph, its parser and its writer.

| Function | What it does |
|---|---|
| `canonicalize(data)` | The canonical form of one marshalled object. |
| `dumps(code)` | `marshal.dumps` then `canonicalize`, for a supported Python only. |

Rules: an unknown marshal type (3.15's frozendict among them, which `compile` never puts among a
code object's constants), a truncated stream, trailing bytes, an invalid or recursive reference
and nesting deeper than 100 raise `BytecodeError`. `SUPPORTED_PYTHON` is the set of versions
fsbpyr runs on and builds for.

- Imports: nothing from fsbpyr.
- Imported by: `assemble`, `build`, `cli` (lazy), `compiler`, `pyversion`, `runtime.stdlib`.

## Recording provenance

What an executable was built from goes into its build_info, whose keys are on
[executable format](Dev-Executable-Format.md#build-information).

### `manifest`

[`src/fsbpyr/manifest.py`](../src/fsbpyr/manifest.py)

build_info's `lock` and `dists` (REP-3), pure functions of the lock file's bytes and the staged
site, so identical inputs give identical records from any directory. A distribution is traced to
the wheel the lock names whose tags the installed `WHEEL` file shares; one that shares none was
built on the build host from its sdist.

| Class | What it represents |
|---|---|
| `LockError` | The lock file cannot be read. |
| `LockedPackage` | A package as the lock lists it: source kind, version, wheels with their SHA-256, the sdist's SHA-256, and whether it lists hashes of files it does not name (`Pipfile.lock`). |

| Function | What it does |
|---|---|
| `read_lock(source, text)` | The packages of a `uv.lock`, script lock, `pylock.toml`, `poetry.lock`, `pdm.lock` or `Pipfile.lock` by canonical name; empty for a requirements file or an unlocked build. |
| `lock_record(directory, source, path)` | build_info's `lock` and the lock's packages. |
| `inferred_lock(directory, inferred)` | build_info's `lock` for pins read from a venv. |
| `installed_tags(dist)` | The wheel tags in an installed distribution's `WHEEL` file. |
| `dist_record(dist, packages, *, project)` | One `dists` entry. |
| `dist_records(dists, packages, *, project)` | All entries, sorted by name and version. |
| `built_here(records)` | The distributions built from their sdists on this host. |

Rules: the `source` values are on
[executable format](Dev-Executable-Format.md#build-information); `local` is the project and the
lock's local packages (uv's editable, directory and virtual ones, a `pylock.toml` directory, a
Poetry directory, a PDM or Pipenv path); Poetry, PDM and Pipenv kinds map to `registry` (another
index too), `git`, `url`, `path` or `local`, and a lock that lists only an sdist gives
`built-from-sdist` except for git. `hashed` is true for `uv.lock` and a script lock,
for a requirements file with a `--hash` option, and for the other locks when they list hashes.
`build` turns a `LockError` into a `BuildError`.

- Imports: `closure`, `foreign`, `pipenv`, `project`, `venvdeps`.
- Imported by: `build`.

### `vcs`

[`src/fsbpyr/vcs.py`](../src/fsbpyr/vcs.py)

Releases are tagged `vX.Y.Z` (D28); a build records HEAD's commit and the highest release tag on
it. `fsbpyr --version` prints `0.1.0-6457a95b` for a build of a `v0.1.0` tag, else
`untagged-6457a95b`, from the executable's build_info (through `_fsbpyr`) or, in a checkout, from
git. A release's glibc 2.28 stubs are a companion release `el8-vA.B.C`, since a published
release takes no new assets; [`el8.yml`](../.github/workflows/el8.yml) leaves it as a draft,
`make release-sign` signs and publishes it, and a version may have none (D46).

| Function | What it does |
|---|---|
| `git_state(directory, *, warn)` | `{commit, tag}` of HEAD; None without git, outside a repository, or for a directory HEAD's tree does not contain. |
| `commit_time(directory)` | HEAD's commit time in seconds, under the same conditions. |
| `work_tree_files(directory)` | The tracked files below `directory`, and the untracked ones git does not ignore (`git ls-files`); None without git or outside a work tree. |
| `version_string(state)` | `X.Y.Z-<8 hex>`, `untagged-<8 hex>`, or `untagged-unknown` without a commit. |
| `own_version()` | fsbpyr's version: what the running executable recorded, else the checkout's. |
| `release_asset(python, target)` | `fsbpyr-3.12-x86_64-linux-gnu` (without `.xz`). |
| `release_stub_asset(python, target, glibc)` | The stub asset for that fsbpyr: `fsbpyr-stub-3.12-x86_64-linux-gnu-glibc2.28`. |
| `release_stub_sums(glibc)` | The `sha256sum` list of the unpacked stubs: `SHA256SUMS-glibc2.28`. |
| `release_stub_tag(glibc, tag)` | `el8-v@VERSION@` for `v@VERSION@`; `ValueError` for a baseline without one (2.34). |
| `release_asset_record(project, python, target)` | build_info's `release_asset`, only for fsbpyr. |

Rules: git runs with `LC_ALL=C`, a 10-second timeout, and without the `GIT_DIR`-style variables
a hook could have set. A project copied into another repository records no commit. A git error
other than "not a git repository" goes to `warn` (`Uncounted` in `build`). Only whole `vX.Y.Z`
tags are release tags, so a companion release's prefix (`el8-`, `_STUB_RELEASE_PREFIXES`) must
not start with `v`.

- Imports: nothing from fsbpyr.
- Imported by: `build`, `cli` (lazy), `dispatch`, `inspection`, `loose`, `selfupdate`, `staging`,
  `runtime.stubs`.

## Assembling

### `assemble`

[`src/fsbpyr/assemble.py`](../src/fsbpyr/assemble.py)

`build_temporary` checks that the app fits the runtime, compiles the boot modules afresh, adds
the runtime's build_info keys to the app's (`runtime`, `runtime_tag`, `extension_suffixes`,
`python`, `fsbpyr`, `zstd`, `resources`, `runtime_inputs`, `runtime_components`,
`stub_sha256`), serialises build_info as JSON with sorted keys, and has
`payload.writer.write_temporary` write stub and payload to a temporary file beside the output
([payload modules](Dev-Payload-Modules.md), [executable format](Dev-Executable-Format.md)).

| Class | What it represents |
|---|---|
| `AssemblyError` | The pieces cannot be combined into a valid executable. |
| `App` | The application content: name, entry point (one target, or a table of commands), modules, files, distributions, build_info entries, zstd level. |

| Function | What it does |
|---|---|
| `boot_modules()` | `_fsbpyr_boot` (STARTUP, filename `<frozen _fsbpyr_boot>`) and every `_fsbpyr_rt` module, as canonical bytecode with sources. |
| `generation_id(runtime_tag, files)` | The native cache generation: SHA-256 of the runtime tag and the path, content digest and mode of every extractable file (D10). |
| `resources_id(files)` | The same over every payload file, 32 hex digits: where resources extracted on demand live (D27). |
| `stub_digest(stub)` | SHA-256 of the stub with its zero padding to the payload alignment. It identifies the stub, not a release: releases that link it from the same inputs with the same toolchain share it. |
| `runtime_module_names(runtime)` | The modules found before the payload's: the runtime's, the boot modules, CPython's built-in and frozen modules, `_fsbpyr`. |
| `build_executable(runtime, app, out)` | `build_temporary` and `commit` in one call (the end-to-end tests use it). |
| `build_temporary(runtime, app, out)` | The executable, written beside `out`; returns the temporary path. |

Rules: the running interpreter's bytecode magic must equal the runtime's; an app module named
like a module found before the payload, or an app file with a runtime file's path, raises
`AssemblyError` (at most ten names listed). Files are hashed in the order of their paths' bytes.

- Imports: `fsbpyr` (the package), `bytecode`, `compat`, `payload.format`, `payload.spec`,
  `payload.writer`, `runtime.registry`.
- Imported by: `build`, `cli` (lazy).

## Reading executables

`fsbpyr inspect` reads only the payload, through `payload.reader`, so it reads older fsbpyr
versions' executables too; text output never carries control characters, since the file may not
be trusted. The user-facing side is on [Inspecting](User-Inspecting.md).

### `inspection`

[`src/fsbpyr/inspection.py`](../src/fsbpyr/inspection.py)

`read` gathers the build_info, distributions, modules by distribution, shared libraries and
extension modules, the stub's toolchain (its `.comment`), the sizes (`sizes.measure`) and, with
`--files`, every packaged `RECORD` entry (D28). The JSON always has `glibc_baseline` (2.34 when
none is recorded, D46), the text only a recorded one. Several commands are shown with their
targets (`_fsbpyr_rt.commands.parse`); a lock from a venv as `inferred from <venv>` with the
pins.

| Class | What it represents |
|---|---|
| `InspectError` | The file cannot be read, or fsbpyr did not build it. |
| `FileEntry` | One packaged `RECORD` line: distribution, path, kind, size, SHA-256. |
| `Report` | Everything `read` found: path, size, app, build_info, distributions, modules, libraries, verification, digests, toolchain, entry point, sizes, files. |

| Function | What it does |
|---|---|
| `read(path, *, verify, files)` | The `Report` of an executable. |
| `as_json(report, *, all_modules)` | The report as JSON (its own `schema` 1), with build_info in full. |
| `as_text(report, *, all_modules)` | The report as lines of text. |

Rules: a path that is not a regular file is refused unopened (a FIFO would block); a file that is
no fsbpyr executable, or fails verification, raises `InspectError`; the whole file's SHA-256 is
computed only with `--verify`. A plain `.so` (no ABI tag) is an extension only if it contains its
`PyInit_` name.

- Imports: `elf`, `natives`, `sizes`, `system`, `text`, `vcs`, `boot._fsbpyr_rt.commands`,
  `payload.format`, `payload.reader`, `runtime.pins`.
- Imported by: `cli` (lazy), `dispatch`, `selfupdate`.

### `sizes`

[`src/fsbpyr/sizes.py`](../src/fsbpyr/sizes.py)

Where an executable's bytes go (DX-5), from the reader's records: the stub, padding to 64 KiB,
and the payload's header, tables and blob data, all but the trailer. Each blob has an owner (a
distribution, or the runtime) and a part: code, sources, resources or natives.

| Class | What it represents |
|---|---|
| `Sizes` | Stored bytes of the stub, padding, payload header, tables, blob padding, runtime, each distribution's parts, and unreferenced blobs; `total` adds them up. |

| Function | What it does |
|---|---|
| `measure(executable)` | The `Sizes` of an opened executable. |

Rules: a blob shared by several owners counts for the first in path order; a block's stored size
is split among its members by raw size, the rounding remainder going to the first; a stub's own
zero tail counts as padding.

- Imports: `payload.format`, `payload.reader`.
- Imported by: `inspection`.

### `licensing`

[`src/fsbpyr/licensing.py`](../src/fsbpyr/licensing.py)

`fsbpyr inspect --licenses` (REP-4). The runtime's notices, under `_fsbpyr_licenses/` (the
libraries linked into the stub, and fsbpyr's), are exact. The distributions' are best effort:
files under the usual licence names, not every notice a wheel's bundled libraries need.

| Class | What it represents |
|---|---|
| `Notice` | One notice: its owner (`fsbpyr`, or a distribution and version), path and text. |

| Function | What it does |
|---|---|
| `read(path)` | The runtime's notices, then each distribution's. |
| `render(notices)` | One block per notice, headed `==== owner: path ====`. |

Rules: a distribution's notice is a file its `METADATA` names as `License-File`, one under
`.dist-info/licenses/`, or one whose name starts with `licence`/`license`, `copying`, `notice` or
`thirdpartynotices`, directly in the dist-info directory or anywhere outside it. Control
characters are shown escaped.

- Imports: `payload.format`, `payload.reader`, `runtime.notices`.
- Imported by: `cli` (lazy).

## Tool upkeep

The user-facing side is on [Other commands](User-Other-Commands.md).

### `selfupdate`

[`src/fsbpyr/selfupdate.py`](../src/fsbpyr/selfupdate.py)

`fsbpyr self update` fetches the latest GitHub release's `fsbpyr-3.X-<target>.xz` for its own
Python (`own_asset`). The release's `SHA256SUMS` must pass a signature check against the keys
this fsbpyr pins (`signing.check_release_file`), and the `.xz` must match its line before lzma
reads it; there is no fallback. It is unpacked in-process into the executable's directory,
verified as `inspect --verify` does before any byte of it runs, and renamed over the
executable's real path, keeping its mode; the new executable then rewrites the man page and
completions. `dispatch` and
`runtime.stubs` download through `signed_sums` and `fetch_checked` too, `verify` through
`release_files`.

| Class | What it represents |
|---|---|
| `UpdateError` | The update cannot be done; the message says why and what to do instead. |
| `NotFound` | An `UpdateError`: HTTP 404, or a sums file that lists no such file. |
| `TooLarge` | An `UpdateError`: the server sent more than a limit allows. |
| `UnsignedRelease` | An `UpdateError`: no signature by a pinned key covers what the release served. |
| `Release` | The latest release: tag, version numbers, the URLs of the asset's `.xz`, `SHA256SUMS` and `SHA256SUMS.sig`. |

| Function | What it does |
|---|---|
| `curl_hint(*, default, directory)` | The installer command line for this host's Python, or the default Python, into a directory. |
| `running_executable()` | The running fsbpyr executable's real path; `UpdateError` from a checkout or a wheel. |
| `release_numbers(text)` | `(X, Y, Z)` of `vX.Y.Z` or `X.Y.Z`, else None. |
| `own_release()` | This executable's release numbers; a developer build is refused. |
| `own_asset()` | The asset this executable was released as, from its build_info, else the asset for its Python and the host. |
| `latest_release(asset)` | The latest release from the GitHub API, as a `Release`. |
| `download_bytes(url, limit)` | A small file's body, at most `limit` bytes (else `TooLarge`); `NotFound` for HTTP 404, `UpdateError` otherwise. |
| `signed_sums(url, signature_url, tag)` | A sums file, once a pinned key's signature of it passes; `UnsignedRelease` otherwise. |
| `release_files(missing, log)` | For `verify` of a file of fsbpyr with nothing beside it: the sums file and signature of the `vX.Y.Z` release it records, unchecked. |
| `listed_digest(sums, name, tag)` | The SHA-256 a checked sums file lists for `name`. |
| `fetch_checked(url, digest, target, limit)` | Downloads an `.xz` (256 MiB at most), checks its SHA-256 against `digest`, then unpacks it into `target`, at most `limit` bytes when a limit is given: `runtime.stubs` gives 256 MiB, self update and `dispatch` none (each `.xz` has matched the digest of a signed sums file by then). |
| `check_release_identity(info, asset)` | A new executable's release numbers, once its build_info's `release_asset` is `asset`. |
| `check_glibc(baseline, host)` | Refuses an executable whose recorded `glibc` is newer than the host's (`os.confstr("CS_GNU_LIBC_VERSION")`, major and minor); a non-glibc host and a build that records none pass. |
| `update(log, warn)` | The update; False when already up to date. |

Rules it enforces:

- A developer build, a run from a checkout or a wheel, and an executable directory the user cannot
  write are refused with the installer command to use instead.
- The release document is at `FSBPYR_RELEASE_API` or the GitHub API's latest-release URL; a
  latest tag that is not `vX.Y.Z`, or a release without the asset, is an `UpdateError`. Nothing
  in it is trusted, and only `http` and `https` URLs are opened.
- Nothing is replaced when the latest release is not newer, or the download has no valid
  signature, does not match its line, is cut short, fails verification, is another asset or
  version, or needs a newer glibc than the host's (the message names both versions); temporary
  files are removed.
- The man page and completions (`share/man/man1/fsbpyr.1` and the bash, zsh and fish completion
  files) are rewritten only if they exist under the prefix and the executable is in a `bin`
  directory; otherwise a warning says how to write them.
- After an update, a warning names the placed glibc 2.28 stubs the new release refuses
  (`runtime.stubs.refused_after_update`,
  [Runtime modules](Dev-Runtime-Modules.md#runtimes-for-glibc-228-el8)); the files stay.

- Imports: `inspection`, `signing`, `targets`, `text`, `vcs`, `dispatch` (lazy),
  `runtime.stubs` (lazy).
- Imported by: `cli` (lazy), `dispatch`, `runtime.stubs`.

### `manpage`

[`src/fsbpyr/manpage.py`](../src/fsbpyr/manpage.py)

The man page, from the click commands (D40); code in backticks is set in bold. Besides the
commands it has a CONFIGURATION section from `config.CONFIG_KEYS`, EXIT STATUS, ENVIRONMENT,
FILES and SECURITY sections whose text this module holds, and the examples `cli` passes in.
EXIT STATUS lists 0, 1, 2 (usage error), 130 (interrupted) and 70, with which a built executable
refuses to start ([Launcher](Dev-Launcher.md#exit-statuses)).

| Function | What it does |
|---|---|
| `render(group, name, examples)` | The roff page: NAME, SYNOPSIS, DESCRIPTION, OPTIONS, COMMANDS (every command below the group, hidden ones left out), CONFIGURATION, EXIT STATUS, ENVIRONMENT, FILES, SECURITY, EXAMPLES. |

Rules: backslashes and hyphens are escaped, a line starting with `.` or `'` is protected, and a
help paragraph whose first line is click's `\b` keeps its line breaks. A test keeps the
`ENVIRONMENT` table in step with the source.

- Imports: `config`, `fsbpyr` (the package).
- Imported by: `cli` (lazy).

## Signatures

### `signing`

[`src/fsbpyr/signing.py`](../src/fsbpyr/signing.py)

The commands `key new`, `key trust`, `sign` and `verify`, the signing of a build's output, and
the checks of fsbpyr's own downloads. The crypto is the launcher's (`_fsbpyr`,
[`launcher/sig.c`](../src/fsbpyr/launcher/sig.c)), so fsbpyr run from source can neither sign
nor check. The design is on [Security](Dev-Security.md#signatures) and in
[`docs/signing.md`](../docs/signing.md).

| Class | What it represents |
|---|---|
| `SigningError` | An error of use or setup: exit 2, never a verdict about a file. |
| `NotVerified` | The file does not pass: exit 1. |
| `NothingToCheck` | A `NotVerified`: nothing was given and nothing lies beside the file. |
| `Crypto` | The four `_fsbpyr` functions used: SHA-256, new key, sign, verify. |
| `Verified` | A file that passed: project, key fingerprint, the sums file and the names it lists. |
| `Target` | A file to check: its path, project, that project's keys and SHA-256. |
| `Trusted` | What `key trust` did: the fingerprint saved, and any it replaced. |
| `BuildSigning` | `--signing-key` and the two signing variables a build took. |

| Function | What it does |
|---|---|
| `crypto()` | The launcher's functions, which mypy checks against `Crypto` through `stubs/_fsbpyr.pyi`; `SigningError` from source. |
| `available()` | Whether they exist. |
| `pinned_keys()` | The `.pub` files of `src/fsbpyr/release-keys/`. |
| `fingerprint(pub)` | `SHA256:<hex>` of a public key file. |
| `valid_name(name)` | Whether a project name may go into a statement. |
| `typed_name(text)` | A project name the user typed, canonical and checked. |
| `statement(project, digest)` | What both signatures sign: the project and the SHA-256. |
| `recorded_project(path)` | Whether `path` is an fsbpyr executable, and the project its build info names. |
| `recorded_tag(path)` | The tag its build info names, unchecked. |
| `trust_store()` | `${XDG_CONFIG_HOME:-~/.config}/fsbpyr/trusted`, through `registry.secure_directory`. |
| `checking_keys(project)` | The pinned keys for fsbpyr, else the key `key trust` saved. |
| `sums_lines(text)` | (digest, name) of each line of a `sha256sum` file. |
| `listed_names(text, digest)` | The names a sums file lists a digest as. |
| `check_listed(target, sums, signature, name)` | A file through a signed sums file given as bytes. |
| `verify(path, project, *, checksums, signature)` | `fsbpyr verify`: what was named, else the `.sig` or a signed sums file beside the file. |
| `check_release_file(content, signature)` | Whether a pinned key signed a release's sums file. |
| `asset_digest(sums, asset)` | The digest a sums file lists for `asset`. |
| `passphrase(given, *, new)` | `given`, else `FSBPYR_SIGNING_PASSPHRASE`, else typed on the terminal. |
| `key_new(name, given)` | `NAME.key` (mode 0600) and `NAME.pub`; the fingerprint. |
| `signing_key_path(option)` | `--signing-key`, else `FSBPYR_SIGNING_KEY`. |
| `sign(path, *, project, key, given)` | `FILE.sig` beside the file a link leads to; nothing is replaced. |
| `key_trust(project, public_key, expected, *, replace)` | Saves a project's key once the whole fingerprint matches. |
| `take_build_variables(key)` | Both signing variables, removed from the environment before the build starts a process. |
| `sign_output(output, request)` | Signs a build's output; the reason when it does not. |
| `warning_lines(output, why)` | The boxed warning of a build that is not signed. |
| `release_check_lines(tag)` | What a check against downloaded release files can and cannot show. |

- Imports: `text`, `payload.format`, `payload.reader`, `runtime.registry`.
- Imported by: `cli` (lazy), `dispatch`, `selfupdate`, `runtime.stubs`.

## Shared helpers

### `fsbpyr`

[`src/fsbpyr/__init__.py`](../src/fsbpyr/__init__.py)

The package holds only `__version__` (`"@VERSION@"`), which `assemble` writes into build_info as
`fsbpyr` and the man page puts in its header. The version with the commit comes from
`vcs.own_version`.

- Classes and functions: none.
- Imports: nothing from fsbpyr.
- Imported by: `assemble`, `manpage`, `runtime.registry`.

### `targets`

[`src/fsbpyr/targets.py`](../src/fsbpyr/targets.py)

The host's target is detected from `sys.platform`, the machine and the C library. `TARGETS`
lists `x86_64-linux-gnu`, `aarch64-linux-gnu`, `x86_64-linux-musl`, `aarch64-linux-musl` and
`aarch64-macos`; only the glibc ones have pins in `runtime.pins`
([Compatibility](Dev-Compatibility.md)).

| Class | What it represents |
|---|---|
| `UnsupportedHost` | The host is none of the targets. |
| `Target` | One platform: asset name, PBS triple and flavour, architecture, ELF machine, dynamic loader, C library, xz filter for native files, multiarch directory, uv's `--python-platform`; `platform_for(manylinux)` gives it for a baseline's manylinux tag (a musl or macOS target keeps its own). |

| Function | What it does |
|---|---|
| `target_named(name)` | The target by asset name or PBS triple; `ValueError` if unknown. |
| `host_libc()` | `"glibc"` when `confstr` reports a glibc version, `"musl"` when `/lib/ld-musl-*.so.1` exists, else None. |
| `detect(system, machine, libc)` | A host's target (`arm64` counts as `aarch64`); `UnsupportedHost` otherwise. |
| `host_target()` | `detect` for this process. |

- Imports: nothing from fsbpyr.
- Imported by: `build`, `cli` (lazy), `dispatch`, `elf`, `natives`, `selfupdate`,
  `payload.spec`, `runtime.collect`, `runtime.registry`, `runtime.stubs`, `runtime.sysroot`.

### `compat`

[`src/fsbpyr/compat.py`](../src/fsbpyr/compat.py)

Exports `zstd` and `tarfile`: the standard library's `compression.zstd` and `tarfile` on 3.14
and later; before, those of `backports.zstd` (the same API, and a `tarfile` with `r:zst` and
`w:zst`), which fsbpyr depends on only below 3.14.

| Function | What it does |
|---|---|
| `process_cpu_count()` | `os.process_cpu_count()` on 3.13 and later; before, the size of the process's CPU affinity set. |

- Imports: nothing from fsbpyr.
- Imported by: `assemble`, `payload.format`, `payload.reader`, `payload.spec`, `payload.spill`,
  `payload.writer`, `runtime.pbs`.

### `text`

[`src/fsbpyr/text.py`](../src/fsbpyr/text.py)

| Function | What it does |
|---|---|
| `plural(count, noun)` | `count` and `noun`, with an `s` unless the count is one: `1 distribution`, `2 distributions`. |
| `tail_lines(text, limit)` | The last `limit` characters of `text` (trailing newlines dropped), from the first line that starts in them, after a line `(N earlier lines left out)`; text no longer than `limit` comes back whole. |
| `shown(value)` | `value` as a line of a report, its `repr` when it is not printable, so a name read from a file cannot rewrite the terminal. |

Rules: when no newline follows the cut (the last line alone is longer than `limit`), the text is
cut where it is, with no note.

- Imports: nothing from fsbpyr.
- Imported by: `build`, `cli` (lazy), `inspection`, `selfupdate`, `signing`, `staging`, `tools`,
  `runtime.pbs`, `runtime.stdlib`, `runtime.stub`.
