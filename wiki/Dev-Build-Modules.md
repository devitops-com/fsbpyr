# Build modules

This page is the reference for the Python modules directly under
[`src/fsbpyr/`](../src/fsbpyr) in fsbpyr
v@VERSION@: the command line, the pipeline that turns a project, a directory of scripts or a single
script into an executable, and the commands that read executables or keep the tool up to date. It
starts with what `fsbpyr build` does, step by step, with the module and function behind each step
and the point where each kind of input takes its own path. Then it describes every module,
grouped by subsystem: what it does and why, its classes and public functions, the rules it
enforces, and where it sits in the import graph. The subpackages have their own pages:
[payload modules](Dev-Payload-Modules.md) (the executable format's reader and writer),
[runtime modules](Dev-Runtime-Modules.md) (building and caching the runtime),
[boot modules](Dev-Boot-Modules.md) (the code that runs inside a built executable), and the C
[launcher](Dev-Launcher.md).

Module names on this page drop the `fsbpyr.` prefix. "Imports" lists the fsbpyr modules a module
imports, and "Imported by" the ones that import it; "(lazy)" marks an import made inside a
function rather than at module level. Both lists come from the source's syntax tree.

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
- [Shared helpers](#shared-helpers): `fsbpyr`, `targets`, `compat`, `text`

## What `fsbpyr build` does, step by step

`fsbpyr build` starts in [`cli.py`](../src/fsbpyr/cli.py),
which reads what it is given, decides which kind of input that is and which Python it builds
with. If the Python is the one this fsbpyr runs on, the build runs in this process:
[`build.build`](../src/fsbpyr/build.py#L675)
calls [`build._build`](../src/fsbpyr/build.py#L735),
which goes from the input to a new executable written beside the output, and then smoke-tests
it, puts it in place and links its commands. A build for glibc 2.28 (`--glibc 2.28`, for EL8
hosts) runs the same steps; steps 8, 10, 12, 16 and 17 use its baseline instead of the default
2.34 ([below](#building-for-glibc-228)). The colours of the diagrams follow the wiki's
convention: blue for build-time code, green for files on disk, purple for external inputs.

### What a build starts from

`fsbpyr build PROJECT` takes a directory (the current one by default) or a file.
[`Project.load`](../src/fsbpyr/project.py#L73)
decides which of five kinds of input it is, and the kind decides how the project's wheel is made,
where the commands come from and which lock sources count. The letters A to E name the kinds in
the steps and in the second diagram.

![How Project.load decides the kind of input: a file that is a *.py file or has a python or uv run shebang is D, one script, any other file is refused; a directory with a pyproject.toml that holds tool settings only, or with neither pyproject.toml nor setup.py nor setup.cfg, is C, a directory of scripts, when it has Python files, else E, a tool binary, when it has a lock source, else NoPythonFiles; a setup.cfg alone is refused; a pyproject.toml that a build system packages, or a setup.py, is A, a project with a build system; a pyproject.toml with a project table that no build system packages is B, a project without a build system.](images/build-inputs.svg)

| | Input | The project's wheel | Commands | Dependencies, strongest first | Output by default |
|---|---|---|---|---|---|
| A | A project with a build system: a `pyproject.toml` that a build system packages, or a `setup.py` | `uv build`, out of tree | the console scripts and the wheel's `#!python` legacy scripts; with none, the only top-level `__main__` package or module with a main guard | `--requirements`, `uv.lock` (the project's or its uv workspace's), `pylock.toml`, `requirements.txt`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, else unlocked | `dist/<command>`; with several commands, `dist/<project name>` and a link per command |
| B | A project without a build system: `[project]` and no `[build-system]`, or Poetry's `package-mode = false` or PDM's `distribution = false` (a Poetry 1 `[tool.poetry]` with no `[project]` too); a uv workspace root is A | written by fsbpyr: the project's `*.py` files under `_fsbpyr_scripts/` | `[project.scripts]`, else the files with a main guard | as A | as A |
| C | A directory of scripts: no `pyproject.toml` (or one of tool settings only), no `setup.py` or `setup.cfg`, and Python files | written by fsbpyr: the `*.py` files and the extensionless files whose first line is a python or `uv run` shebang | each file with a main guard or such a shebang | `--requirements`, `pylock.toml`, `requirements.txt`, `Pipfile.lock`; else the commands' PEP 723 blocks (unlocked); else pins read from the scripts' venv | `dist/<command>`; with several, `dist/<directory name>` as a project name (`My_Tools` gives `my-tools`) and a link per command |
| D | One script: PROJECT is a `*.py` file, or an extensionless file whose first line is a python or `uv run` shebang | written by fsbpyr: the script and the files of its directory that it imports, transitively | the script, named after its stem | `--requirements`, `<script>.lock`; else its PEP 723 block (unlocked); else pins read from the venv | `dist/<stem>` in the script's directory |
| E | A tool binary: as C, but with no Python file and a lock source: `--requirements` (or `requirements` in `[tool.fsbpyr]`), `pylock.toml`, `requirements.txt` or `Pipfile.lock` | written by fsbpyr: metadata only | the console and gui scripts of the tools the lock names | the lock source that makes it E | as C, named after the project: the directory's name, or `<name>-tools` |

`--entry` builds one command or module of any kind. For B to E no build backend runs and no
project code runs during the build: fsbpyr writes the wheel with the standard library
([`loose`](#loose)), checks each command's imports without importing anything
([`importcheck`](#importcheck)), and the smoke test finds a script without importing it. The
user-facing side is on [Building](User-Building.md).

### The steps

The numbers below are the steps in the diagram; a step marked with some of the letters runs, or
takes that branch, only for those kinds. The italic lines in the diagram say what a
`--glibc 2.28` build does differently.

![The steps of fsbpyr build in five phases: command line (cli.build_command, Project.load deciding A to E and config.load_config, pyversion.resolve), checks (check_before_staging, loose.Package.load for B, C and D or Package.tool_binary for E, find_uv, python_warnings, select_runtime, which for glibc 2.28 pairs the executable's standard library with the EL8 stub of runtime.stubs, lock_source with infer_dependencies for C and D and lock_warnings), staging (staging_plan and run_plan with uv build for A or Package.write_wheel for B to E, with manylinux_2_28 wheels for glibc 2.28, SiteIndex.scan, rebuild_on_baseline against the EL8 sysroot for glibc 2.28, legacy.attach for A and tools.tool_scripts for E, walk with check_script_imports for B, C and D and resolve_entry), content (classify, glibc_report and system_report with the manylinux_2_28 limits for glibc 2.28, build_info with glibc_baseline for glibc 2.28 and App, entry check and check_strict) and writing (build_temporary, smoke test, commit and links.make_links for several commands). The project directory or script, uv, the runtime, the EL8 stub placed beside fsbpyr or cached, the companion release el8-vA.B.C, the scripts' venv, the lock files, the package index and the glibc sysroots feed the steps that read them; the output is the executable and a link per command.](images/build-pipeline.svg)

**Command line** ([`cli.py`](../src/fsbpyr/cli.py))

1. The `fsbpyr` console script is `fsbpyr.cli:main`, and `python -m fsbpyr` reaches the same
   [`main`](../src/fsbpyr/cli.py#L559). It makes
   SIGTERM raise `KeyboardInterrupt`, so that a terminated build unwinds like an interrupted one,
   and runs the click group with the command line kept as the click context's object.
   [`build_command`](../src/fsbpyr/cli.py#L155)
   stops with exit 1 on a Python other than 3.12 to 3.15, refuses `--fast-compression` together
   with `--best-compression` (a usage error, exit 2), collects the tool's cache when a collection
   is due, and splits `--smoke-args` as a shell would. `--glibc` takes `2.34` or `2.28`; click
   refuses any other value (exit 2).
2. [`Project.load`](../src/fsbpyr/project.py#L73)
   reads PROJECT, with `--requirements` if given, and decides its kind
   ([above](#what-a-build-starts-from)); when a `Pipfile.lock` is the lock source it also reads
   the Python the lock asks for. [`config.load_config`](../src/fsbpyr/config.py#L168)
   merges the `[tool.fsbpyr]` table (of `pyproject.toml`, of a `pyproject.toml` of settings, or
   of a script's PEP 723 block) with the options into a `BuildConfig`; options win. Its `glibc`
   is the baseline of the steps below: `--glibc`, else `glibc` in the table, else 2.34. When
   the table names `requirements` and the option does not, `build_command` loads the project
   again with that file, so that the lock source, and with it the Python a `Pipfile.lock` asks
   for, is the one the build installs.
3. [`pyversion.resolve`](../src/fsbpyr/pyversion.py#L89)
   picks the Python. When it is not this fsbpyr's, `dispatch.run` runs the matching fsbpyr with
   the same command line, `--glibc` included, and its exit status is this command's
   ([below](#building-for-another-python)). Otherwise `build.build` runs.

**Checks** (`build._build`)

4. `_build` loads the project again, and
   [`check_before_staging`](../src/fsbpyr/build.py#L432)
   refuses what `pyproject.toml` and the options already decide: a console script whose name is no
   command name when there are several, a project whose `[project]` lists no console script and
   that has no possible legacy script and no `__main__.py` or guarded module anywhere in its tree,
   an output path that is a directory, a version-control line in a requirements file that uv
   reads with `--require-hashes`, and an extra the project does not declare. Scripts and extras
   are checked here only when `[project]` lists them statically; otherwise after staging.
5. For B to E, [`load_package`](../src/fsbpyr/build.py#L452)
   reads the files into a `loose.Package`: for B, C and D the Python files, the commands and the
   PEP 723 dependencies ([`Package.load`](../src/fsbpyr/loose.py#L92)),
   with a warning for each file left out; for E an empty package named after the directory
   ([`Package.tool_binary`](../src/fsbpyr/loose.py#L143)).
   A package with no command fails the build here unless `--entry` names one. Step 10 writes the
   wheel from this package, so the files the build checks are the files it packages.
6. [`staging.find_uv`](../src/fsbpyr/staging.py#L82)
   requires uv on `PATH` before anything slow happens, and `uv_version` asks for its version, which
   build_info records.
7. [`python_warnings`](../src/fsbpyr/build.py#L718)
   runs the version rule again for its warnings (a `.python-version` pin it ignores, say). It
   raises `BuildError` for a project that builds with another Python, which only a direct call of
   `build.build` reaches: the command line has dispatched such a build already.
8. [`runtime.registry.select_runtime`](../src/fsbpyr/runtime/registry.py#L312)
   returns the runtime (the launcher stub and the standard library): the one embedded in the
   running fsbpyr executable when its Python and its glibc baseline match, else the one
   `fsbpyr runtime build` put in the cache. For glibc 2.28 a released executable pairs its own
   standard library with the stub of its release linked against the EL8 sysroot
   (`runtime.stubs.paired`, [below](#building-for-glibc-228)); with `--offline` that stub must
   be placed or cached. `Project.check_python` then tests
   `requires-python` against the runtime's full version, and `uv_mismatch` warns when uv is not
   the uv that built this fsbpyr executable.
9. [`Project.lock_source`](../src/fsbpyr/project.py#L212)
   chooses what the dependencies come from (the order is in the table above and under
   [`project`](#project)). For C and D with no lock source and no PEP 723 dependency,
   [`infer_dependencies`](../src/fsbpyr/build.py#L545)
   follows the commands' imports ([`importcheck.check_command`](../src/fsbpyr/importcheck.py#L198));
   when the runtime lacks some of them and a venv is found
   ([`venvdeps.find_venv`](../src/fsbpyr/venvdeps.py#L58)),
   [`venvdeps.infer`](../src/fsbpyr/venvdeps.py#L179)
   pins the distributions that provide them. The lock source is then `inferred`, the package's
   wheel requires the pins, and a warning names the venv; with `--strict` the build fails here.
   [`lock_warnings`](../src/fsbpyr/build.py#L353)
   warns about an unlocked build, requirement lines that are not pinned, and a Poetry, PDM or
   Pipenv lock that is older than its project file.

**Staging**, in a temporary directory `fsbpyr-stage-*` that lives until the executable is written

10. [`staging_plan`](../src/fsbpyr/staging.py#L183)
    turns the lock source into uv commands, and
    [`run_plan`](../src/fsbpyr/staging.py#L393)
    runs them in the project directory. The project's wheel is made out of tree: by `uv build` for
    A, by [`Package.write_wheel`](../src/fsbpyr/loose.py#L192)
    for B to E. Then the locked distributions and the wheel are installed into a throwaway
    environment, with wheels chosen for the glibc baseline (`--python-platform`:
    `x86_64-manylinux_2_34`, or `x86_64-manylinux_2_28` for glibc 2.28, and likewise on aarch64). A
    Poetry, PDM or Pipenv lock is first converted into a requirements file in the staging
    directory ([`foreign.export`](../src/fsbpyr/foreign.py#L472),
    [`pipenv.export`](../src/fsbpyr/pipenv.py#L157)),
    and a script's lock is installed by `uv sync --script`. The commands run with
    `SOURCE_DATE_EPOCH` set.
11. [`SiteIndex.scan`](../src/fsbpyr/closure.py#L99)
    reads every `*.dist-info` in the staged site-packages, and
    [`check_no_editable`](../src/fsbpyr/closure.py#L107)
    refuses editable installs.
12. [`built_here_above`](../src/fsbpyr/build.py#L193)
    finds the distributions uv built from sdists on this host (all their wheel tags are `linux_*`)
    whose native files need a newer glibc than the baseline, 2.34 or 2.28 (the `baseline` of the
    sysroot pin that `runtime.pins.sysroot_for` returns for the target and `glibc`).
    [`rebuild_on_baseline`](../src/fsbpyr/build.py#L209)
    builds them again against that pin's sysroot (`runtime.sysroot.fetch_sysroot` and
    `build_flags`, then `staging.rebuild_wheel` and `staging.rebuild`): Debian 12's packages
    (glibc 2.36) for 2.34, Rocky Linux 8.10's RPMs (glibc 2.28) for 2.28. The site is then scanned
    again. When that cannot be done (offline, no sysroot, a failed build) the host's builds stay
    and a warning says why.
13. `_installed_project` finds the project's own distribution in the site, by the name of the
    wheel built in step 10, and checks the extras that could not be checked in step 4.
    [`legacy.attach`](../src/fsbpyr/legacy.py#L39)
    makes the wheel's `#!python` legacy scripts commands of it (only a wheel that a build backend
    made has any, so A). For E,
    [`tool_binary`](../src/fsbpyr/build.py#L606)
    has [`tools.tool_scripts`](../src/fsbpyr/tools.py#L312)
    choose the tools the lock names and their scripts, prints how it chose them, and
    `tools.with_commands` makes those scripts the project's own.
14. [`SiteIndex.walk`](../src/fsbpyr/closure.py#L126)
    follows `Requires-Dist` under the runtime's environment markers. For B, C and D,
    [`check_script_imports`](../src/fsbpyr/build.py#L575)
    follows each command's imports and skips, with a warning naming `file:line`, a command that
    imports what neither the project, the staged site nor the runtime holds; `--strict` fails on
    that, and so does a build with no command left.
    [`resolve_entry`](../src/fsbpyr/closure.py#L232)
    gives the app's name and entry point: the `--entry`, the only command, a table of all the
    commands named after the project (`closure.command_table`, which checks every command name
    with `check_command_names`), or the inferred `__main__` package or guarded module. The
    output is `-o`, or `dist/<app name>` in the project directory (the script's directory for D).
    When the output is a symlink the executable is written through it, unless an earlier build
    of the project made that link for one of several commands: then the executable replaces the
    link (`_output_path`, `links.is_earlier_link`). For a table of commands,
    `links.check_links` refuses a regular file or directory where a command's link is to go,
    unless it is the executable an earlier build of the project wrote for that one command. The
    output's directory is created.

**Content**, with a `payload.spill.Spill` beside the output that holds compressed bytes

15. [`classify.classify`](../src/fsbpyr/classify.py#L750)
    turns every installed distribution's `RECORD` into payload modules, files and native libraries:
    modules are compiled to canonical bytecode by `compiler` and `bytecode`, files are compressed,
    and the native libraries' dependencies are resolved by `natives` and `elf`. For the project's
    own files it also reports the warnings compiling emits, and for the scripts (B, C, D and
    legacy scripts) the files they read next to their `__file__`, in the main guard too for the
    modules the executable runs as `__main__`.
16. The warnings are gathered: the walk's, the classifier's, `best_compression_unused` and
    `helper_interpreter_hints`.
    [`glibc_report`](../src/fsbpyr/build.py#L147)
    computes the oldest glibc the app runs on (for glibc 2.28 never below 2.28), and
    [`system.system_report`](../src/fsbpyr/system.py#L122)
    lists the system libraries the target must provide; both warn about needs above the
    baseline: glibc 2.34 and the manylinux_2_34 limits, or glibc 2.28 and the manylinux_2_28
    limits.
17. An `assemble.App` collects the content and the app's build_info:
    [`_provenance`](../src/fsbpyr/build.py#L256)
    gives `schema`, `lock`, `dists` and `settings` (through `manifest`), and `build` adds
    `project`, `fsbpyr_version`, `uv`, `glibc`, `glibc_baseline` (for glibc 2.28 only,
    `baseline_record`), `system`, `zstd_level`, `native_compression`, `interpreter_reexec`, `git`
    (`vcs.git_state`) and, for fsbpyr itself only, `release_asset`.
18. [`_check_entry_module`](../src/fsbpyr/build.py#L105)
    refuses an entry module that is neither in the app nor in the runtime, and a package named as
    the entry without a `__main__` module; for a table of commands it checks every target. With
    `--strict`, [`check_strict`](../src/fsbpyr/build.py#L404)
    raises `StrictError` for any warning that counts. Nothing has been written yet.

**Writing**

19. [`assemble.build_temporary`](../src/fsbpyr/assemble.py#L124)
    adds the boot modules and the runtime's build_info keys (for glibc 2.28, `runtime`,
    `runtime_inputs` and `stub_sha256` name the EL8 runtime id, sysroot and stub), and
    [`payload.writer.write_temporary`](../src/fsbpyr/payload/writer.py#L589)
    streams the executable into a new `.<name>.XXXX.tmp` beside the output. When `_build`
    returns, the spill is closed and the staging directory removed.
20. Unless `--no-smoke`,
    [`smoke_test`](../src/fsbpyr/build.py#L630)
    runs the new file with `FSBPYR_SELFTEST=1`, which checks every command's target; a script of
    B, C or D is found, not imported. With `--smoke-args`,
    [`smoke_run`](../src/fsbpyr/build.py#L653)
    also runs it as the app with those arguments.
21. [`payload.writer.commit`](../src/fsbpyr/payload/writer.py#L617)
    renames the new file over the output and syncs the directory. For a table of commands,
    [`links.make_links`](../src/fsbpyr/links.py#L67)
    then puts a relative symlink per command beside the output, in place of a symlink or of the
    executable an earlier build of the project wrote for that command. `build_command` prints the
    warnings, a line `fsbpyr: built PATH (SIZE MB, entry ENTRY, glibc >= X.Y)` (`N commands` in
    place of the entry for a table), the commands and each link, `fsbpyr: target needs: ...` when
    there is one, and for B to E how to deploy the executable and its links on a host
    (`links.deploy_recipe`).

When a step fails, the exception reaches `build_command`, which catches the build modules' error
types (and `OSError`), prints the warnings collected before the error (`build` attaches them to
the exception as notes, except to `StrictError`, whose message lists them), then
`fsbpyr: error: ...`, and exits 1. `build`'s `finally` deletes the temporary executable and
removes the output directories the build created while they are empty, so the output stays as it
was unless step 21 is reached; a link that cannot be made in step 21 fails the build with the
executable in place. An unexpected exception is reported by `main` as an internal error with its
traceback, and Ctrl-C as `fsbpyr: interrupted` with exit 130.

### Building for another Python

Each fsbpyr builds apps for the Python it runs on (`build.TARGET_PYTHON`). When step 3 chooses
another version, [`dispatch.run`](../src/fsbpyr/dispatch.py#L94)
runs the fsbpyr of the same release built for that Python, with the command line as it was
given, and its exit status becomes this command's. The variant then does the whole build itself,
from step 1. `dispatch.variant` takes it from the tool's cache, or fetches the release asset
first and refuses it when it needs a newer glibc than the host has; a developer build never
fetches one, and neither does `--offline`, which stops with a `DispatchError` when the variant
is not cached. The version comes from a project's `requires-python` as much as from a single
script's PEP 723 block or a `Pipfile.lock`, so a script can be handed on as a project can.
`--glibc` travels with the command line, and the variant reads `glibc` in `[tool.fsbpyr]` as
this fsbpyr does, so a glibc 2.28 build takes the stub that goes with the variant's Python.
`dispatch.run` tells the variant where this fsbpyr is (`FSBPYR_DISPATCHED_BY`), and the variant
looks for a placed stub beside it first, where the user put it.
`load_config` checks the table before the Python is chosen: an unknown `glibc` there stops the
build before any variant is fetched.

![How fsbpyr build chooses: config.load_config checks the glibc setting and pyversion.resolve chooses X.Y; if it is this fsbpyr's Python, build.build runs in this process; a developer build stops with DispatchError and the make command to build the variant; a release looks for downloads/fsbpyr-X.Y-TARGET-vA.B.C in the cache, stops with DispatchError when it is missing and the build is --offline, else fetches the release's fsbpyr-X.Y-TARGET.xz into it when it is missing, verifies it and checks its glibc against the host's, and dispatch.run runs it with the same arguments, --glibc included, and FSBPYR_DISPATCHED_BY naming this fsbpyr's paths; for glibc 2.28 the variant takes the EL8 stub for its own Python, looking beside this fsbpyr first.](images/build-dispatch.svg)

### Building for glibc 2.28

`--glibc 2.28`, or `glibc = "2.28"` in `[tool.fsbpyr]`, builds an app for EL8 hosts, whose glibc
is 2.28. The baselines are `runtime.pins.BASELINES`, the default `DEFAULT_BASELINE` (2.34) and
2.28 (D46). The steps are those above; the baseline changes what these use:

| Step | Default, glibc 2.34 | `--glibc 2.28` |
|---|---|---|
| 8, runtime | the runtime embedded in the fsbpyr executable, its stub linked against Debian 12's glibc 2.36 sysroot | the executable's standard library with its release's stub linked against Rocky Linux 8.10's glibc 2.28 sysroot (`runtime.stubs.paired`); a developer build or a checkout: the cache's runtime |
| 10, wheels | `--python-platform` `x86_64-manylinux_2_34` or `aarch64-manylinux_2_34` (`Target.platform_for`) | `x86_64-manylinux_2_28` or `aarch64-manylinux_2_28` |
| 12, rebuild | of distributions built here that need more than glibc 2.34, against the Debian 12 sysroot | more than glibc 2.28, against the Rocky Linux 8.10 sysroot, read from its RPMs |
| 16, reports | glibc above 2.34, and libstdc++, libgcc_s and libz above the manylinux_2_34 limits (named systems: RHEL 9, Ubuntu 22.04), warn | glibc above 2.28, and the manylinux_2_28 limits (RHEL 8); the app's `glibc` is never below 2.28 |
| 17, build_info | no `glibc_baseline`; `inspect` takes its absence as 2.34 | `glibc_baseline` `"2.28"` |

`select_runtime` takes the executable's embedded runtime only when its Python and its baseline
match; `registry` reads the embedded runtime's baseline from the sysroot identity in its
`runtime_inputs`, and finds a cached runtime by its runtime id, whose digest covers the
baseline's sysroot. A released fsbpyr carries one runtime, for 2.34. For 2.28,
[`runtime.stubs.paired`](../src/fsbpyr/runtime/stubs.py#L243)
gives it the stub of its own release for its Python and target, the asset
`fsbpyr-stub-X.Y-TARGET-glibc2.28` (`vcs.release_stub_asset`), which
[`stub_for`](../src/fsbpyr/runtime/stubs.py#L204)
takes from the first of these that has it:

1. A file of that name, or with `.xz` (unpacked in-process), placed beside the fsbpyr
   executable (`runtime.stubs.own_paths`): for a variant that `dispatch.run` started, first
   beside the fsbpyr that handed the build to it (`FSBPYR_DISPATCHED_BY`); then beside the path
   it was run by (looked up on `PATH` when it was run by name), then beside its real path. This
   is for machines without GitHub.
2. The tool's cache: `downloads/fsbpyr-stub-X.Y-TARGET-glibc2.28-<runtime id>`, named after the
   runtime id the stub must carry, so a stub that an update leaves unchanged is not downloaded
   again.
3. The companion release `el8-vA.B.C` of the release `vA.B.C` (`vcs.release_stub_tag`), under the
   download URL a variant is fetched from (`$FSBPYR_RELEASE_DOWNLOADS`, see
   [`dispatch`](#dispatch)): first its `SHA256SUMS-glibc2.28` (`vcs.release_stub_sums`, read with
   `selfupdate.download_text`), then the stub's `.xz`, unpacked with `selfupdate.unpack`. A stub
   whose SHA-256 is not the one listed is refused and not kept. With `--offline` nothing is
   downloaded: a stub that is neither placed nor cached is a `StubError` that says to place the
   asset beside the fsbpyr executable or to build once without `--offline`.

Wherever it comes from, the stub must carry the runtime id this fsbpyr computes for itself
(`registry.runtime_id`): `cp<XY>-<target triple>-` and 12 hex digits of a SHA-256 over the
launcher's sources and its compile and link flags for the target (`runtime.stub.launcher_digest`),
the pinned python-build-standalone archive of that Python and target, the EL8 sysroot's identity
and the runtime layout version. The launcher records its runtime id when it is linked, and
`runtime.stub.stub_identity` reads it. A stub with another runtime id (another Python or target,
or a release whose launcher, pins or sysroot differ), or a file that is no stub, is refused
(`StubError`, a `RuntimeCacheError`) with the asset and the release to take instead. The id names
no release: two releases with the same launcher, pins and sysroot compute the same id, so the
EL8 stub of an earlier release such as that passes the check, and is found in the cache under
the same name. A cached file that fails the check is removed and fetched again. A downloaded
stub that passes is written into the cache whole or not at all. When the companion release does
not exist or has no stub for this Python, the error says to place the stub beside the fsbpyr
executable (for a variant, the one that handed the build to it) or to use a release that has
one; any other failure to fetch it, a download cut short among them, says how to build without
the network. The paired runtime keeps the
executable's standard library, Python version and component versions, and takes the stub, the
EL8 runtime id and `runtime_inputs` naming the EL8 sysroot, which `assemble` writes into
build_info.

A developer build of fsbpyr, and fsbpyr run from a checkout, have no release to take a stub from:
they use the runtime `fsbpyr runtime build --glibc 2.28` put in the cache (the stub linked
against the EL8 sysroot, and the standard library), and stop with a `RuntimeCacheError` that
names that command when there is none. The runtime side is on
[runtime modules](Dev-Runtime-Modules.md); `fsbpyr inspect` shows the baseline
([`inspection`](#inspection)), and `fsbpyr self update` names the placed stubs that the new
release does not publish ([`selfupdate`](#selfupdate)).

## Command line and dispatch

These modules are what runs first. `cli` defines every command with click and imports the rest
of fsbpyr only inside the commands, so that `fsbpyr --version` stays fast.
`dispatch` hands a build to the fsbpyr for another Python.

### `__main__`

[`src/fsbpyr/__main__.py`](../src/fsbpyr/__main__.py)

`python -m fsbpyr`: it calls `cli.main` and exits with its status. The call is guarded by
`if __name__ == "__main__":`, so a worker process that imports the main module (the build's
compiler pool started by a fork server, see [`compiler`](#compiler)) does not start another
fsbpyr.

- Classes and functions: none.
- Imports: `cli`.
- Imported by: nothing.

### `cli`

[`src/fsbpyr/cli.py`](../src/fsbpyr/cli.py)

The command line: the click group `fsbpyr` and its commands `build`, `inspect`,
`runtime build`, `runtime list`, `cache gc`, `self update`, `completion` and `man`. The click
definitions are the one source of the help text, the shell completions and the man page (D40),
so an option is described once. Each command imports what it needs inside its function, and
`--version` is computed only when asked, because from a source checkout it runs git. `main` is
the entry point of the `fsbpyr` console script; it turns errors into exit statuses and messages
on standard error.

| Function | What it does |
|---|---|
| `cli` | The group; plain `fsbpyr` prints the help and exits 0. |
| `build_command` | `fsbpyr build [PROJECT]`: PROJECT is a directory or a script file. Loads the project and the configuration (and the project again when only the settings name the `requirements` file), resolves the Python, hands the build to `dispatch.run` or runs `build.build`, and prints the result: warnings, the built line, the commands and their links, what the target needs and, for a build without a build system, the deploy lines. |
| `inspect_command` | `fsbpyr inspect`: `inspection.read` and its text or JSON form; `--licenses` prints `licensing`'s notices instead. |
| `runtime_group` | The `fsbpyr runtime` group. |
| `runtime_build` | `fsbpyr runtime build`: `runtime.registry.build_runtime` for `--python` and `--glibc` (the sysroot the stub is linked against: Debian 12's for 2.34, the default, or Rocky Linux 8.10's for 2.28), with `--clang`. |
| `runtime_list` | `fsbpyr runtime list`: the embedded runtime and the cached ones, one per line, each with its Python and its glibc. |
| `cache_group` | The `fsbpyr cache` group. |
| `cache_gc` | `fsbpyr cache gc`: collects the tool's cache, then every app cache found; exit 1 if anything could not be removed, or when the caches cannot be found (no home directory). |
| `self_group` | The `fsbpyr self` group. |
| `self_update` | `fsbpyr self update`: `selfupdate.update`. |
| `completion` | Prints click's completion script for bash, zsh or fish; the scripts call fsbpyr back with `_FSBPYR_COMPLETE` set. |
| `man` | Prints the man page that `manpage.render` makes from these commands and `EXAMPLES`. |
| `main` | The console script's entry point: runs the group and returns the exit status. |

Module constants: `DEFAULT_PYTHON` is the X.Y of the Python fsbpyr runs on; `EXAMPLES` are the
man page's examples, each a command line and what it does (a test runs each one through the
commands, so no example names an option that is gone).

Rules it enforces:

- `build` and `runtime build` refuse to run on a Python outside 3.12 to 3.15
  (`bytecode.SUPPORTED_PYTHON`) with exit 1: `uv pip install` and `uv tool install` ignore the
  upper bound of fsbpyr's `requires-python`, and the build would otherwise fail only after
  staging.
- `--fast-compression` with `--best-compression`, a `--smoke-args` string that cannot be split,
  and `inspect --licenses` with another option are usage errors (exit 2).
- `--glibc` of `build` and of `runtime build` takes `2.34` or `2.28` (a click choice: any other
  value is a usage error). `runtime build` defaults to 2.34; `build` without it takes `glibc` in
  `[tool.fsbpyr]`, else 2.34.
- `build`, `inspect`, `runtime build` and `runtime list` first collect the tool's cache when a
  collection is due (`runtime.collect.collect_when_due`). No home directory, a cache it may not
  write and a read-only file system are ignored; other failures are printed and never stop the
  command.
- A cache location that needs the home directory when there is none (`HOME` unset, empty or
  relative, and no absolute home in the passwd entry) is a `RuntimeCacheError`: `build`,
  `runtime build`, `runtime list` and `cache gc` print `fsbpyr: error: cannot find the home
  directory; set HOME, XDG_CACHE_HOME or FSBPYR_CACHE_DIR` and exit 1. With `FSBPYR_CACHE_DIR`
  set, `cache gc` collects that cache without a home directory.
- `build_command` reports the build modules' errors and `OSError` as `fsbpyr: error: ...` with exit
  1, after the warnings carried as notes. `main` reports click usage errors with click's exit
  status, an interrupt (SIGINT, or SIGTERM, which it turns into `KeyboardInterrupt`) with exit 130,
  and any other exception as an internal error with its traceback and where to report it, exit 1.
- Completion of `--entry` and `--extra` reads the commands and extras of the project being built
  (`Project.load`); if anything fails it offers nothing, since a message would land on the shell
  prompt.

- Imports: `assemble` (lazy), `build` (lazy), `bytecode` (lazy), `classify` (lazy), `closure`
  (lazy), `config` (lazy), `dispatch` (lazy), `inspection` (lazy), `licensing` (lazy), `links`
  (lazy), `manpage` (lazy), `project` (lazy), `pyversion` (lazy), `selfupdate` (lazy), `staging`
  (lazy), `text` (lazy), `vcs` (lazy), `boot._fsbpyr_rt.gc` (lazy), `payload.format` (lazy),
  `runtime.collect` (lazy), `runtime.pbs` (lazy), `runtime.registry` (lazy), `runtime.stdlib`
  (lazy), `runtime.stub` (lazy), `runtime.sysroot` (lazy), `runtime.toolchain` (lazy).
- Imported by: `__main__`.

### `dispatch`

[`src/fsbpyr/dispatch.py`](../src/fsbpyr/dispatch.py)

`fsbpyr build` for a Python other than the running fsbpyr's. The matching variant of this
fsbpyr's own release, the asset `fsbpyr-X.Y-TARGET`, is fetched the way `fsbpyr self update`
fetches: the `.xz` is unpacked in-process with lzma (`selfupdate.unpack`) and verified in-process
(`inspection.read(verify=True)`) before anything runs it. It is kept in the tool's cache as
`downloads/fsbpyr-X.Y-TARGET-vA.B.C` and run with the same arguments, so once cached it needs no
network; with `--offline` it is never fetched. A developer build never fetches a released variant,
which would mix released code into a development build: it stops and says how to build the
variant from the checkout (`make build PYTHON=X.Y`). The variant gets the command line as it was
given, `--glibc` and `--offline` included, so a glibc 2.28 build for another Python pairs the
variant's standard library with the EL8 stub for that Python
([Building for glibc 2.28](#building-for-glibc-228)). The variant also gets
`FSBPYR_DISPATCHED_BY` (`DISPATCHED_BY_VARIABLE`): this fsbpyr's own paths
(`runtime.stubs.own_paths`), joined with `os.pathsep`, beside which the variant looks for a
placed stub before its own places. It only adds directories to look in: a stub found there must
still carry the runtime id. `runtime.stubs` takes `DOWNLOADS_VARIABLE`, `DEFAULT_DOWNLOADS` and
`DISPATCHED_BY_VARIABLE` from this module: the stubs' companion releases are under the same
download URL as the variants.

| Class | What it represents |
|---|---|
| `DispatchError` | The variant cannot be had; the message says why and what to do instead. |

| Function | What it does |
|---|---|
| `variant(python, log, *, offline)` | The path of this release's executable for `python`: from the cache, else fetched into it; with `offline`, never fetched. |
| `run(python, args, log, *, offline)` | Runs that executable with `args` and `FSBPYR_DISPATCHED_BY` set, and returns its exit status. |

Rules it enforces:

- The release comes from `vcs.own_version`; a version that is not a release number is a developer
  build and raises `DispatchError` (`_own_tag`).
- The download URL is `$FSBPYR_RELEASE_DOWNLOADS/<tag>/<asset>.xz`, by default under
  `https://github.com/devitops-com/fsbpyr/releases/download` (tests serve their own).
- The cache root goes through `runtime.registry.secure_directory` (created mode 0700, or checked to
  be the user's own and not writable by others); `downloads/` is created mode 0700.
- `_fetch` writes to a temporary file beside the final one, sets mode 0755, verifies it, refuses it
  when the glibc its build_info records is newer than the host's (`selfupdate.check_glibc`), and
  renames it, so the cached file appears whole or not at all. A failed download, verification or
  glibc check raises `DispatchError` naming the asset and the release; the temporary file is
  removed.
- With `--offline`, a variant that is not in the cache raises `DispatchError` naming the asset and
  the release and saying to run the build once without `--offline`; nothing is downloaded.

- Imports: `inspection`, `selfupdate`, `targets`, `vcs`, `runtime.registry`, `runtime.stubs`
  (lazy).
- Imported by: `cli` (lazy), `runtime.stubs`.

## Build driver

`build` runs the steps above. It calls into almost every other build module and adds the logic
that belongs to no single one: which input takes which branch, which warnings `--strict` counts,
the venv rung of the dependencies, the import check's warnings, what the glibc baseline decides,
the glibc report, the rebuild of host-built extensions against the baseline sysroot, the smoke
tests, the command links, and the app's half of build_info.

### `build`

[`src/fsbpyr/build.py`](../src/fsbpyr/build.py)

`fsbpyr build`'s pipeline: project, staging install, classification, executable. `build` runs
`_build`, which ends with the new executable written beside the output, then smoke-tests it,
renames it over the output and makes the command links. Along the way it collects warnings (some
of them `Uncounted`, which `--strict` ignores) and the build_info entries that record what the
executable was built from. `TARGET_PYTHON` is the X.Y of the Python fsbpyr runs on: the app runs
on the same Python as the fsbpyr that builds it.

| Class | What it represents |
|---|---|
| `BuildError` | The executable could not be produced, or failed its smoke test. |
| `StrictError` | A `BuildError` for `--strict`: the build has warnings that count; the message lists them. |
| `BuildResult` | What `build` returns: output path, app name, entry point, size, warnings, the glibc the app needs, the "target needs" line, the commands of a table, the links made, whether the project had no build system, and the project's name, by which the links tell an earlier output of the project. |

| Function | What it does |
|---|---|
| `glibc_report(stub, natives, *, baseline)` | The app's glibc requirement (the stub's, raised by the newest need of a bundled native file; for a baseline other than the default never below the baseline, since a launcher linked against glibc 2.28's headers may need only older symbols) and a warning when a native file needs more than both the baseline and the stub. |
| `baseline_record(glibc)` | build_info's `glibc_baseline`: `("glibc_baseline", "2.28")` for a glibc 2.28 build, nothing for the default 2.34, whose build_info has no such key. |
| `built_here_above(dists, baseline)` | Distributions built from sdists on this host (every wheel tag `linux_*`) with a native file that needs a newer glibc than `baseline`. |
| `rebuild_on_baseline(plan, project_dir, wheel, names, baseline, *, offline, log, verbose)` | Builds `names` again from their sdists against the baseline's sysroot, the project's own wheel too when it is among them; returns warnings when it cannot. |
| `settings_record(config)` | build_info's `settings`: entry, effective `fs_packages` (the built-in ones and the configured), `allow_system_libs`, extras, `include_source`. |
| `builder_uv()` | The uv version recorded in the running fsbpyr executable's build_info; None when fsbpyr runs from source. |
| `uv_mismatch(*, builder, current)` | An `Uncounted` warning when uv is not the one that built this fsbpyr: another uv can give other bytes from identical sources. |
| `best_compression_unused(*, best_compression, files)` | A warning when `--best-compression` has no native file to compress. |
| `helper_interpreter_hints(modules, reexec)` | An `Uncounted` note naming packaged libraries known to start helper interpreters through `sys.executable` (`HELPER_INTERPRETERS`), unless `interpreter-reexec` is true. |
| `unpinned_warning(unpinned)` | A warning naming up to three requirements not pinned by `==`, a commit SHA or a hash. |
| `lock_warnings(project, source, requirements)` | The lock source's warnings: an unlocked build (none for C or D when it declares no dependency), unpinned lines in a requirements file, a Poetry, PDM or Pipenv lock older than its project file, and for an unlocked build a `poetry.lock` or `pdm.lock` that a directory of scripts does not read and a `requirements/` directory that `--requirements` could use. |
| `check_hashed_requirements(project, config)` | Refuses version-control lines in a requirements file that uv reads with `--require-hashes` (`--strict`, or a `--hash` option in the file), naming the file and lines: a commit SHA pins the source but is not a hash. |
| `check_strict(warnings)` | Raises `StrictError` when a warning that is not `Uncounted` is among `warnings`. |
| `check_output(output)` | Raises `BuildError` when the output path is a directory. |
| `check_before_staging(project, config)` | The checks the project and options allow before staging: command names, a project with no possible entry, output, requirements lines, extras. |
| `load_package(project, config, warnings)` | The `loose.Package` of B to E (None for A); `ProjectError` when it has no command and `--entry` names none. |
| `infer_dependencies(package, project, config, runtime, warnings)` | The venv rung: for a package that declares no dependency, the pins `venvdeps.infer` reads for the imports the runtime lacks, and the warning that names the venv and the command that declares them. |
| `check_script_imports(package, project, config, site, runtime, *, warnings)` | The import check of each command (or of the one `--entry` names); returns the commands it skips. |
| `tool_binary(package, project, config, index, dist, *, python_version, log)` | For E, the project's distribution with the tools' scripts as its commands (`tools.tool_scripts`, `tools.with_commands`); None for any other build. |
| `smoke_test(executable, app_name, output)` | Runs the new executable in self-test mode and requires success. |
| `smoke_run(executable, output, args)` | Runs the new executable as the app with `args` and requires exit 0. |
| `build(directory, config, *, log, verbose)` | The whole build; returns a `BuildResult`. |
| `python_warnings(project, config)` | The version rule's warnings; `BuildError` when the project builds with another Python. |

Private helpers with logic of their own: `_build` (steps 4 to 19), `_provenance` (`lock`, `dists`
and `settings` through `manifest`, and the log line naming distributions built from sdists),
`_installed_project` (the project's distribution, and its extras when they are dynamic),
`_converted_lock_warnings`, `_unresolved_warning`, `_removed_hint` and `_freeze_command` (the text
of a skipped command), `_checked_commands`, `_output_path` (the path the executable is written
to, through a link or in its place), `_commands_to_link`, `_make_output_dir`,
`_check_entry_module` and `_glibc_needs`.

Rules it enforces:

- uv is required before anything slow happens (D26).
- `--strict` fails after every warning is collected and before anything is written; the venv
  rung's warning and a skipped command fail it as soon as they are found. Eight kinds of warning
  are `Uncounted` (the list is in `classify.Uncounted`'s docstring): the uv mismatch, git's error
  messages (`vcs.git_state`), the helper interpreter note, the hint that `requirements/` holds
  requirements files, the note that a `Pipfile.lock` takes packages from another index, and three
  of `classify`'s guesses (the `--hints` guess that packages read files next to `__file__`,
  shared libraries no extension loads that Python code names, and executables among the
  resources). A script's read next to its `__file__` counts.
- A command whose imports nothing resolves is skipped with a warning that shows up to three
  `file:line` places (in the project's own file names). It says where to declare the module,
  gives the `uv pip freeze --python <venv>/bin/python` command when there is a venv that
  dependency inference reads (`venvdeps.find_venv`: `.venv`, `venv` or `env` with a
  `pyvenv.cfg` in the project directory, else `$VIRTUAL_ENV`; named relative to the project
  directory when it lies inside it), and for a
  standard library module that the build's Python no longer has (`importcheck.REMOVED_IN`)
  names the newest Python fsbpyr builds for that still has it (`--python 3.12` for one removed
  in 3.13), or says that the script needs porting and, for `imp`, `distutils`, `asyncore`,
  `asynchat` and `smtpd`, what to use instead. A build with no command left fails.
- The venv rung runs only for C and D with no lock source and no PEP 723 dependency, and only
  when a venv is found and the runtime lacks an import that a distribution in it provides; it
  installs pins, never copies.
- The glibc baseline is `BuildConfig.glibc`, 2.34 or 2.28. It decides the runtime's stub, the
  wheels' platform tag, the sysroot of a rebuild, the glibc and library limits of the reports, and
  `glibc_baseline` in build_info ([Building for glibc 2.28](#building-for-glibc-228)).
- A distribution built on this host against a newer glibc than the baseline is rebuilt against the
  baseline's sysroot, except offline (warning). If the sysroot cannot be fetched or the rebuild
  fails, the host's build is packaged and a warning says why (for a failed rebuild, uv's last
  line).
- Distributions installed but not reached by the dependency walk are packaged all the same, with a
  warning that they come from the lock or requirements file; C, D and E with a lock source or
  inferred pins get no such warning, since their file declares every dependency.
- The entry module, or each target of a table, must be part of the app or the runtime; an entry
  without `:function` that names a package needs its `__main__`.
- The smoke test sets `FSBPYR_SELFTEST=1`, allows 120 seconds, and requires exit 0 and
  `fsbpyr selftest ok: <app>` on standard output. The smoke run passes the final output path as
  `argv[0]` while executing the new file, with standard input from `/dev/null` and the same time
  limit. A failure shows the end of standard error: its last 3000 characters, from the first line
  that starts in them, after a note of how many earlier lines were left out (`text.tail_lines`).
- The new executable replaces the output only after the smoke test (at once with `--no-smoke`); on
  any failure or interrupt it is deleted, and the output directories the build created are
  removed while empty. The links of a table are made after the commit.
- An output path that is a symlink is written through, unless it is a link an earlier build of
  the same project made for one of several commands (`links.is_earlier_link`): a build of one
  command replaces that link with its executable and leaves the earlier executable and its
  other links as they were. A table's link replaces a symlink, or the executable an earlier
  build of the same project wrote for that one command; any other file stops the build
  ([`links`](#links)).
- `release_asset` is recorded only when the project is fsbpyr itself.

- Imports: `assemble`, `bytecode`, `classify`, `closure`, `config`, `elf`, `entry`, `foreign`,
  `importcheck`, `legacy`, `links`, `loose`, `manifest`, `natives`, `pipenv`, `project`,
  `pyversion`, `staging`, `system`, `targets`, `text`, `tools`, `vcs`, `venvdeps`,
  `boot._fsbpyr_rt.commands`, `payload.format`, `payload.spec`, `payload.spill`,
  `payload.writer`, `runtime.pbs`, `runtime.pins`, `runtime.registry`, `runtime.sysroot`.
- Imported by: `cli` (lazy).

## Project, settings, Python version

What PROJECT says, merged with the command line: the kind of input and its metadata and lock
source (`project`), a Poetry 1 project read as a `[project]` table (`poetry1`), the build
settings (`config`) and the Python to build for (`pyversion`). The user-facing side is on
[Settings](User-Settings.md).

### `project`

[`src/fsbpyr/project.py`](../src/fsbpyr/project.py)

Reads PROJECT and decides its kind ([What a build starts from](#what-a-build-starts-from)):
`pyproject.toml`'s `[project]` table (name, `requires-python`, console scripts, optional
dependencies) and its `[tool.fsbpyr]` table; nothing for a `setup.py` project, whose name is
learnt after staging; the files of a directory of scripts or of one script, through
[`loose`](#loose). It knows whether scripts and extras are declared statically, so that the
build can check them before staging, finds the `uv.lock` that governs the project (its own or its
uv workspace's), and decides the lock source. The module also reads requirements files line by
line for the build's warnings and checks.

| Class | What it represents |
|---|---|
| `ProjectError` | The directory or file is not something fsbpyr builds, or its constraints exclude the runtime. |
| `LockSource` | Where the dependencies come from: `uv.lock`, `pylock.toml`, `requirements`, `script.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `inferred` or `unlocked`. |
| `Project` | A loaded project: its metadata and `[tool.fsbpyr]` table, whether scripts, extras and entry points are static, whether legacy scripts are possible, and the kind: `loose` (B to E), `bare` (C to E), `script` (D) and `tools` (E), with the commands' PEP 723 `dependencies` and the Python a `Pipfile.lock` asks for (`lock_python`). Methods and properties: `load`, `script_lock`, `uv_root`, `requirements_path`, `lock_source`, `lock_path`, `python_pin`, `specifiers`, `check_python`. |

| Function | What it does |
|---|---|
| `requirement_lines(requirements)` | The logical lines of a requirements file with their first line numbers: comments dropped, `\` continuations joined, a trailing `--hash` of a requirement cut off. |
| `unpinned_requirements(requirements)` | The requirements of a pip requirements file that are not pinned. |
| `vcs_requirements(requirements)` | `file:line: text` of each requirement taken from version control (`git+`, `hg+`, `svn+` or `bzr+` URLs). |
| `settings_only(directory, data)` | Whether a `pyproject.toml` holds tool settings and no project: only `[tool]` tables, no `[tool.poetry]`, and no `setup.py` or `setup.cfg` beside it. |
| `is_unpackaged(data)` | Whether a `pyproject.toml` is a Poetry 1 project with `package-mode = false` and no `[project]` table. |
| `has_hash_option(text)` | Whether a line of a requirements file that is not a comment has a `--hash` option: uv's `--require-hashes` mode. |

Rules it enforces:

- A file is D when it is a `*.py` file or an extensionless file whose first line is a python or
  `uv run` shebang (`loose.python_shebang`); any other file is refused. A script's `[tool.fsbpyr]`
  in its PEP 723 block must be a table.
- An unreadable `pyproject.toml` raises `ProjectError`. A `pyproject.toml` of settings only makes
  the directory C (or E); its `[tool.fsbpyr]` is the build's table.
- B is a `[project]` table with no `[build-system]`, or with Poetry's `package-mode = false` or
  PDM's `distribution = false`, or a Poetry 1 project that `is_unpackaged` (its table comes from
  `poetry1`, named after the directory when `[tool.poetry]` has no name); a `[tool.uv.workspace]`
  root is A. Any other `pyproject.toml`, and a `setup.py` without one, is A.
- A `setup.cfg` alone is refused. A directory with neither file is C when it holds Python files,
  else E when a lock source names tools (`--requirements`, `requirements` in its settings,
  `pylock.toml`, `requirements.txt` or `Pipfile.lock`), else `loose.NoPythonFiles`.
- The lock source, strongest first: `--requirements FILE` (which must exist); for D its
  `<script>.lock`, else nothing more; a `uv.lock` in the project or in the nearest uv workspace
  that claims it (its `members` globs include the project and its `exclude` globs do not; the
  nearest workspace that does not claim the project ends the search), which C and E never use;
  `pylock.toml`; `requirements.txt`; `poetry.lock` and `pdm.lock`, which C and E never use;
  `Pipfile.lock`; else unlocked. `build` turns an unlocked C or D into `inferred` when the venv
  rung finds pins.
- `python_pin` is the first word of `.python-version`, the part after `@` for a uv-style
  `cpython@3.12`; for D the file is read in the script's directory.
- An invalid `requires-python` raises `ProjectError`; so does `check_python` when it excludes the
  runtime's version.
- A requirement is unpinned when it has no `==` before any `;` marker, no commit SHA
  (`git+URL@<40 hex>`) and no `#sha256=` on its URL; an `-e` line with a local path is always
  unpinned, since it installs whatever the tree holds. Other option lines (`-r`, `--hash`) are no
  requirements, and a `--hash=` inside a comment does not count as a hash option.

- Imports: `loose` (lazy), `pipenv` (lazy), `poetry1` (lazy).
- Imported by: `build`, `cli` (lazy), `config`, `foreign`, `importcheck`, `loose`, `manifest`,
  `pipenv`, `poetry1`, `pyversion`, `staging`, `tools`, `venvdeps`.

### `config`

[`src/fsbpyr/config.py`](../src/fsbpyr/config.py)

The build settings. `BuildConfig` holds every setting of a build with its default; `load_config`
reads the project's `[tool.fsbpyr]` table and lets the command-line options win. The table is the
one in `pyproject.toml`, in a `pyproject.toml` of settings beside a directory of scripts, or in a
single script's PEP 723 block. `CONFIG_KEYS` is the one list of table keys: the reader uses it,
the unknown-key error names its keys, and the man page's CONFIGURATION section prints their
descriptions. It has 14 keys (counted in the source); the options `--offline`, `--hints`,
`--smoke` and `--verbose` have none. The `glibc` key and field, as `--glibc`, give the glibc the
app is built for: `2.34`, the default (`runtime.pins.DEFAULT_BASELINE`), or `2.28` for EL8 hosts
([Building for glibc 2.28](#building-for-glibc-228)).

| Class | What it represents |
|---|---|
| `ConfigError` | An invalid `[tool.fsbpyr]` table or option. |
| `BuildConfig` | The merged settings (entry, output, requirements, extras, sources, offline, smoke, smoke arguments, allowed system libraries, fs-packages, hints, compression, interpreter re-execution, strict, Python, glibc); `zstd_level` is 3 with fast compression, else 9. |
| `ConfigKey` | One table key: the `BuildConfig` field it sets, how its value is read and checked, and its man page text. |

| Function | What it does |
|---|---|
| `load_config(project, **options)` | The table's values, overridden by every option that is not None. |

Rules it enforces:

- An unknown key raises `ConfigError` listing the supported ones; a value of the wrong type
  raises `ConfigError`.
- `allow-system-libs` entries must be file names (not empty, no `/`); `fs-packages` entries must
  be dotted identifiers; `output` is relative to the project directory; `glibc` must be one of
  `runtime.pins.BASELINES` (2.34, 2.28), else the `ConfigError` names them.
- `fast-compression` and `best-compression` exclude each other (`ConfigError`). A compression flag
  given on the command line also drops the other one set in the table.

- Imports: `project`, `payload.spec`, `runtime.pins`.
- Imported by: `build`, `cli` (lazy), `manpage`.

### `pyversion`

[`src/fsbpyr/pyversion.py`](../src/fsbpyr/pyversion.py)

The Python a build uses, strongest first: `--python X.Y`, then `python` in `[tool.fsbpyr]`, then a
`.python-version` pin, then `requires-python`. `requires-python` gives the lowest supported
version that satisfies a lower bound, the highest below an upper-only bound, and a pinned minor
version as pinned; a project without a bound that picks one (none, or only `!=`) uses the Python
of the fsbpyr that builds it. A `Pipfile.lock` that is the lock source asks for a minor version
(`Project.lock_python`, from its `_meta.requires`) and counts as a `requires-python` of that
version. A version is tested against `requires-python` as the newest patch release the runtime
pins have for it (so `~=3.12.1` admits 3.12), or `X.Y.0` for one without pins. For D the
`requires-python` is the script block's, for C the intersection of the commands' blocks.
`cli` dispatches to another fsbpyr when the result is not its own Python.

| Class | What it represents |
|---|---|
| `Choice` | The chosen `major.minor` and the warnings the rule produced. |

| Function | What it does |
|---|---|
| `supported()` | The versions fsbpyr builds for, oldest first (from `bytecode.SUPPORTED_PYTHON`). |
| `resolve(project, requested, *, own)` | The `Choice` for `project`; `own` is the running fsbpyr's Python. |

Rules it enforces:

- `--python` and `python` must be `X.Y`, supported, and inside `requires-python`; otherwise
  `ProjectError`.
- A `.python-version` pin outside `requires-python` is a `ProjectError`; a pin that is not a
  CPython `X.Y` version fsbpyr builds for is ignored with a warning.
- A `requires-python` that allows none of the supported versions is a `ProjectError` naming them.
- The `Pipfile.lock`'s version is taken when it is among the versions the project allows;
  otherwise it is ignored with a warning. `--python`, `python` and `.python-version` outrank it
  without an error.

- Imports: `bytecode`, `project`, `runtime.pins`.
- Imported by: `build`, `cli` (lazy).

### `poetry1`

[`src/fsbpyr/poetry1.py`](../src/fsbpyr/poetry1.py)

A Poetry 1 project that is not packaged (`[tool.poetry] package-mode = false`, no `[project]`
table), read as the `[project]` table of a project without a build system (B). Poetry 1 keeps the
name, the version, the dependencies and the scripts in `[tool.poetry]`; this module turns them
into the table `loose` and `project` read. The lock, when there is one, still decides what is
installed ([`foreign`](#foreign)).

| Function | What it does |
|---|---|
| `project_table(data)` | The `[project]` table: `name` and `version` when they are strings, `requires-python` from the `python` dependency, the main dependencies, `optional-dependencies` from `[tool.poetry.extras]`, and the string `scripts`. |

Rules it enforces:

- Poetry's constraints become PEP 440 specifiers: `^1.2` is `>=1.2,<2`, `~1.2` is `>=1.2,<1.3`,
  a bare version is `==`, `*` is any version. An `||` alternative raises `ProjectError`.
- A dependency's `extras`, `markers` and `python` constraint go into its requirement line; a
  `markers` value is put in parentheses when there is a `python` constraint too, so an `or` in it
  keeps apart.
- An `optional` dependency belongs to the extras of `[tool.poetry.extras]` that list it.
- A `git`, `path`, `url`, `develop` or `source` dependency raises `ProjectError` (add a
  `[project]` table, or lock the build with `--requirements`), and so does a requirement that is
  not valid.

- Imports: `project`.
- Imported by: `loose`, `project` (lazy).

## Commands, scripts and tools

The modules that find an app's commands when the project does not list exactly one console
script: an entry inferred from the sources (`entry`), the wheel fsbpyr writes for a project
without a build system, a directory of scripts and a single script (`loose`), legacy scripts of a
project's wheel (`legacy`), the tools a lock names (`tools`), and the links that let one
executable serve several commands (`links`). The table of commands itself, the entry point of an
executable with several commands, is made by [`closure`](#closure) (`resolve_entry`,
`command_table`, `check_command_names`). What a start with several commands does is on
[boot modules](Dev-Boot-Modules.md).

### `entry`

[`src/fsbpyr/entry.py`](../src/fsbpyr/entry.py)

Entry inference for a project with no console scripts: a top-level package with `__main__.py`
(run as `python -m pkg`), or a top-level module with a main guard. The sources are parsed with
`ast`, never imported.

| Class | What it represents |
|---|---|
| `Candidates` | The top-level packages with `__main__.py` and the top-level modules with a main guard; `described` lists them for an error message. |

| Function | What it does |
|---|---|
| `has_main_guard(source)` | Whether the module body has `if __name__ == "__main__":`, in either operand order; bytes are parsed as Python does, so a BOM or a coding cookie counts. |
| `installed_candidates(site, record)` | The `Candidates` of an installed distribution, from its `RECORD`. |
| `source_has_candidate(directory)` | Whether the project tree holds any `__main__.py` or guarded module; a build with neither cannot infer an entry. |

Rules: only module-level `if` statements count, and a file that does not parse has no guard.
`source_has_candidate` looks through every directory, whatever the layout, except hidden ones,
those named in `SKIPPED_DIRECTORIES` (`build`, `dist`, `node_modules`, `__pycache__` and
`site-packages`, which the file walk of [`loose`](#loose) skips too) and virtual environments (a
`pyvenv.cfg`), and never counts `setup.py`. `installed_candidates` takes only names that are
identifiers and never the module `__main__`.

- Imports: nothing from fsbpyr.
- Imported by: `build`, `closure`, `loose`.

### `links`

[`src/fsbpyr/links.py`](../src/fsbpyr/links.py)

The command links of a multi-command executable: `<dir>/<command>` pointing at the executable's
name, and the lines the build prints on how to deploy them. `COMMAND_NAME` is the rule every
command name follows, `[A-Za-z0-9][A-Za-z0-9_.+-]*`: a file name and a word on a command line.
The table of commands the links serve, and the check of each name against `COMMAND_NAME`, are
made in [`closure`](#closure) (`command_table`, `check_command_names`); `build` takes the names
for the links from that table. A build that goes from one command to several, or back, replaces
its own earlier output: the module tells an earlier output of the project by reading its payload.

| Class | What it represents |
|---|---|
| `LinkError` | A command's name is taken by a file that is neither a link nor an earlier output of the project. |

| Function | What it does |
|---|---|
| `link_paths(binary, commands)` | Where the links go: beside `binary`, one per command; a command named like the binary needs none. |
| `is_earlier_link(path, project)` | Whether `path` is a link an earlier build of `project` made for one of several commands: a symlink to a bare name whose target is no link but an earlier output of the project. `build._output_path` replaces such a link rather than write through it. |
| `check_links(binary, commands, project)` | Fails naming the first link path that a file or a directory holds, unless it is an earlier output of `project` for that command. |
| `make_links(binary, commands, project)` | Links each command to the binary's name. |
| `deploy_recipe(binary, commands)` | The lines that say how to install the build on a host: `install -m 755`, a `ln -sf` per command, `FSBPYR_INSTALL_LINKS` as the alternative, and that a line running `python3 $DEST/x.py` must run the link instead. |

Rules: a link names the binary by its name alone, so the directory can move. Each link is made
as a temporary link `.<name>.<8 hex>.tmp` and renamed into place, which replaces a symlink or an
earlier output of the project and never another file; the check runs again just before the
rename. An earlier output (`_earlier_output`) is a regular file that is an fsbpyr executable (its
trailer and payload parse) whose app name is the file's name and whose build_info records the
same project name; a file that cannot be read as one is not.

- Imports: `payload.format`, `payload.reader`.
- Imported by: `build`, `cli` (lazy), `closure`, `loose`.

### `loose`

[`src/fsbpyr/loose.py`](../src/fsbpyr/loose.py)

The wheel of a project that has no build system (D5): B, C, D and the empty project of E.
`uv run main.py` runs the Python files of a directory as they lie, and fsbpyr packages them the
same way. It writes the wheel itself with the standard library: no build backend runs, no project
code runs and nothing is written into the source tree. The wheel holds the project's Python files
and nothing else (a `.env` or a key file is never in it) under the reserved root
`_fsbpyr_scripts/` (`ROOT`), which keeps their relative paths; at run time a script runs as
`__main__` with its directory first on `sys.path`, so its siblings import as under `uv run`.
Commands that run a module as `__main__` are listed under the entry point group `fsbpyr_scripts`
(`MODULE_GROUP`), `module:function` ones under `console_scripts`.

| Class | What it represents |
|---|---|
| `NoPythonFiles` | A directory without `pyproject.toml` (or with settings only) holds no Python file. A `ProjectError`. |
| `Package` | A project's files and commands: name, version, `METADATA` lines, sources by path, commands (`name` to a target below `ROOT`), warnings, and for C and D the project file of each stored path (`origins`), `requires-python` and the PEP 723 dependencies; `tools` for E. Constructors `load` and `tool_binary`; `with_dependencies`, `wheel_name`, `write_wheel`. |

| Function | What it does |
|---|---|
| `discover(directory)` | B's Python files (path to content, sorted) and warnings for the files that cannot be imported. |
| `python_shebang(data)` | Whether the first line is a shebang that starts Python 3 (`python`, `python3`, `python3.12`, directly or through `env`) or `uv run`. |
| `directory_name(directory)` | The project name of a directory: its name as `clean_name` makes it. |
| `clean_name(text)` | `text` as a canonical project name, with what a project name cannot hold replaced by `-` (`scripts` when nothing is left). |
| `script_stem(leaf, *, python)` | The module name a script file is stored under: `check-disk` becomes `check_disk`, an extensionless `tool` becomes `_x_tool`. |
| `discover_directory(directory)` | C's Python files (stored path to content, sorted), the project file of each stored path, and warnings. |
| `inline_metadata(source, where)` | The TOML of a file's `# /// script` block (PEP 723), empty without one. |

What it reads:

- **B**: the `*.py` files. A file whose name, or a directory above it, is no Python name is left
  out with a warning. `[project.scripts]` targets in the project are moved below `ROOT`
  (`main:run` runs `_fsbpyr_scripts.main:run`), a dependency's target stays as it is, and a
  target in `src/` must be written `_fsbpyr_scripts.src.pkg.mod:attr` (the build says so). Without
  `[project.scripts]` the commands are the files with a main guard and a top-level `__main__.py`:
  one is the project's command, named like the project; several are one command each, named after
  the file, `main.py` and `__main__.py` after the project.
- **C**: the `*.py` files and the extensionless files whose first 512 bytes start with a python
  or `uv run` shebang (`python_shebang`); names that start with a dot are skipped. A file or
  directory whose name is no Python name is stored under the name `script_stem` makes, in the
  same place (`check_disk.py`, `_10_backup.py`, `sub_dir/`). Each file with a main guard or such
  a shebang is a command named after the file (hyphens kept, `main` and `__main__` after the
  directory); a name that is no `COMMAND_NAME` is a warning, and the file stays a module. The
  dependencies are the union of the commands' PEP 723 `dependencies` (each once) unless a lock
  source declares them, and `requires-python` is the intersection of theirs; a `[tool.fsbpyr]`
  table in a block is ignored with a warning.
- **D**: the script, stored under its module name, and the files of its directory that it reaches
  by static imports (`importcheck.local_closure`); the rest of the directory is not read. The
  command is the file's stem, which must be a `COMMAND_NAME`; the dependencies and
  `requires-python` are its block's.
- **E**: nothing. The package is named after the directory, or `<name>-tools` when a requirement
  has that name, so that installing it does not replace that distribution.

Rules it enforces:

- `/` and the home directory are no project directories (`ProjectError`).
- In a git work tree the files are the tracked ones (`vcs.work_tree_files`); each untracked Python
  file that git does not ignore is left out with a warning to `git add` or ignore it. Without git a
  walk finds them. Either way hidden directories, `build`, `dist`, `node_modules`, `__pycache__`,
  `site-packages` and virtual environments (a `pyvenv.cfg`) are skipped, and so is a link that
  leads into one of them.
- A link to a `*.py` file outside the directory fails the build; a link to another file outside
  it is passed over; links to directories are not followed.
- Two files that are one stored path, two directories with one stored name, a directory and a
  file with one stored name, and two files that give one command fail the build, naming both.
- Never a command: `setup.py`, `__init__.py`, the test files (`conftest.py`, `test_*`,
  `*_test.py` and any file below a directory named `test` or `tests`), and a `__main__.py` below
  the top level.
- More than one `# /// script` block in a file, invalid TOML in a block, `dependencies` that are
  not a list, an invalid requirement or `requires-python`, and blocks whose `requires-python`
  leave no Python version raise `ProjectError`.
- The version is `[project] version`, or `0`, and must be a valid version. `METADATA` (version
  2.1) holds the name, version, `Requires-Python`, the dependencies and the extras with their
  `extra ==` markers.
- `write_wheel` sorts the entries and the `RECORD`, stamps every entry with the epoch it is given,
  and gives every file mode 0644, so two writes of the same files are the same bytes. The tag is
  `py3-none-any` and the `WHEEL` file names `fsbpyr` as generator.

- Imports: `entry`, `importcheck` (lazy), `links`, `poetry1`, `project`, `vcs`.
- Imported by: `build`, `classify`, `closure`, `compiler`, `importcheck`, `legacy`, `project`
  (lazy), `staging`, `syspath`.

### `legacy`

[`src/fsbpyr/legacy.py`](../src/fsbpyr/legacy.py)

Legacy scripts of the project's wheel: the files of `setup.py`'s `scripts=` (or setuptools'
`script-files`) under `<dist>.data/scripts/`. An installer rewrites their first line `#!python`
to its interpreter, so only the wheel tells which are Python; this module reads the wheel. Each
Python one becomes a command that runs as `python script` runs it: its source is stored below
`loose.ROOT` under a module name, and the boot runs it as `__main__` with that directory first on
`sys.path`.

| Function | What it does |
|---|---|
| `wheel_scripts(wheel)` | The files of the wheel's `.data/scripts/` whose first line is `#!python` (the wheel specification's rule; not `#!pythonw`), by name, sorted. |
| `attach(dist, wheel, warnings)` | `dist` with those scripts as commands: each source written below the scripts root of its site and listed in its `RECORD`. |

Rules: a script that does not compile (Python 2, say) is left out with a warning; a name that is
also a console script, and two names that give one module (`loose.script_stem`), raise
`ClosureError` naming both. A file under `bin/` that is not such a script, a shell script for
example, is not packaged, and `classify` warns naming it.

- Imports: `closure`, `loose`.
- Imported by: `build`.

### `tools`

[`src/fsbpyr/tools.py`](../src/fsbpyr/tools.py)

A tool binary (E): a directory with no Python file and a lock source is an empty project whose
requirements are the tools. The build installs the lock as for any project, and the console and
gui scripts of the distributions the file names become the executable's commands (pip and uv
install gui scripts as ordinary launchers on Linux). The user-facing side is on
[Building](User-Building.md).

| Class | What it represents |
|---|---|
| `Listed` | A distribution a lock source names: canonical name, the extras asked for and the marker that makes uv install it. |
| `Tools` | The commands (command to entry point), the distribution each comes from, the progress lines that say how the tools were chosen, and the listed distributions as the project's `Requires-Dist`. |

| Function | What it does |
|---|---|
| `listed_distributions(project, source, requirements)` | The distributions the lock source names, in file order. |
| `tool_scripts(index, listed, *, project, source, python_version)` | The commands of the tools, after the install. |
| `with_commands(dist, tools)` | The project's distribution requiring the listed distributions and holding the tools' scripts as its console scripts, which `closure.resolve_entry` turns into the entry point. |

Rules it enforces:

- A requirements file names its distributions directly: one per requirement line, `-r` files
  followed relative to the including file (a file that includes itself is an error), `-c` files
  and other options naming nothing. A line that is no requirement names the distribution of its
  `#egg=` fragment or of its wheel or sdist file name, else the build fails asking for
  `name @ URL`. A `pylock.toml` names its `[[packages]]`; a `Pipfile.lock` names the Pipfile's
  `[packages]`, or without a Pipfile the lock's `default` entries (`pipenv.tool_names`).
- A listed distribution whose marker is false is not a tool; one that is not installed and has no
  marker is a `ClosureError`. The extras that listed items ask for count as active when markers
  are evaluated.
- A file that lists every installed distribution (but the project, and `pip`, `setuptools`,
  `wheel` and `distribute`, which `pip freeze` leaves out) is a freeze: the tools are the listed
  distributions that no other listed one requires under the runtime's markers. Otherwise every
  listed distribution is a tool.
- A tool with no script of its own is replaced by its direct requirements when it installs no
  code of its own (a meta-package), and else only by the requirements named `<tool>-...`; this is
  followed down to distributions with scripts, a cycle or eight levels stop it, and one with code
  of its own that gives nothing is reported as a library, not a tool.
- A tool also gets the commands of its family: its direct requirements named `<tool>-...` that
  have scripts, and theirs in turn. A dependency's scripts are otherwise never commands.
- Two tools (or a tool and its family) with one script name, a console and a gui script among
  them, raise `ClosureError` naming both distributions; no script at all raises one saying what
  the file named.
- The build prints which tools it took and why, each meta-package and family taken through, and
  the commands with their distributions and versions.

- Imports: `closure`, `foreign`, `pipenv`, `project`, `text`.
- Imported by: `build`.

## Locks of other tools, inferred dependencies

uv installs everything (D26), so a lock that is not uv's or pip's is converted, in the staging
directory and never in the project, into a pip requirements file that the requirements plan of
[`staging`](#staging) installs: `foreign` for Poetry and PDM, `pipenv` for Pipenv. A directory of
scripts or one script that declares nothing gets its pins from the venv it runs in (`venvdeps`).
The user-facing side is on [Building](User-Building.md).

### `foreign`

[`src/fsbpyr/foreign.py`](../src/fsbpyr/foreign.py)

`poetry.lock` and `pdm.lock` as the lock of a build (D5). The packages of the main (Poetry) or
default (PDM) group, plus the project's extras, are written as requirement lines with the
markers evaluated for the build's target and every hash the lock records. A Poetry lock that does
not say which group a package belongs to (lock-version 2.0, Poetry 1.5 to 1.8) is walked from the
project's own dependencies. The lock entry types are shared with `pipenv`.

| Class | What it represents |
|---|---|
| `ForeignLockError` | A lock that fsbpyr cannot turn into a locked build. A `ProjectError`. |
| `Edge` | A dependency of a locked package, as Poetry records it: name, specifier, marker, optional, extras. |
| `Entry` | One locked package: name, version, kind (`registry`, `index`, `local`, `path`, `git` or `url`), where it comes from, commit and subdirectory, files with their SHA-256, groups, marker, optional, dependencies, extras, and hashes of files the lock does not name (`Pipfile.lock`). |
| `Export` | The requirements file, whether every line carries a hash, and the number of lines. |

| Function | What it does |
|---|---|
| `read_entries(source, data)` | The `Entry` list of a parsed Poetry or PDM lock. |
| `export(source, directory, *, environment, extras)` | The requirements file of the lock beside the project's `pyproject.toml`, for the target's markers and the project's extras. |
| `stale(source, directory)` | A message when the lock's content hash is not the one of `pyproject.toml`: the dependencies changed after locking; None when it matches or there is nothing to compare. |

Rules it enforces:

- Poetry locks of any lock-version: 1.x with `category` and the hashes in `[metadata.files]`,
  2.0 with neither group nor marker, 2.1 with both (a per-group marker is the `main` group's).
  With groups, the roots are the `main` packages that are not optional and whose marker holds;
  without, the roots are `[tool.poetry.dependencies]` (with their `markers` and a `python`
  constraint that must be a PEP 440 specifier) and `[project.dependencies]`. The extras asked for
  add the packages the lock's `[extras]` lists for them; an extra the lock lacks is an error.
  From the roots, the dependencies the lock records are followed under the target's markers, an
  optional one only when an active extra lists it.
- PDM locks: the `default` group and the groups named like the extras asked
  for; a lock that names no group per package is accepted only when it holds the default group
  alone (otherwise: lock again with `--strategy inherit_metadata`, or pass `--requirements`). An
  entry for a package with extras adds nothing, since the extras' packages are entries of their
  own; lock 4.0 to 4.2 keep the hashes in `[metadata.files]`.
- A line is exact: a registry package as `name==version` with every hash, a path (made absolute
  from the project; `${PROJECT_ROOT}` filled in) as a regular, not editable, `name @ file:...`
  that must exist, a VCS entry by its commit (`name @ git+URL@commit`, with its subdirectory),
  and a URL entry with `#sha256=` when the lock has its hash.
- Refused, naming the entry: a package from another package index (its credentials are not in the
  lock), a VCS entry with no commit, a path that does not exist, a name, version or source with
  white space, and a package locked at two versions that both apply to the target.
- `stale` compares Poetry's `content-hash` with a hash `_poetry_hash` computes from the parts of
  `pyproject.toml` Poetry hashes, and PDM's `content_hash` with the two formulas PDM has used
  (with and without `dependency-groups`).

- Imports: `project`.
- Imported by: `build`, `manifest`, `pipenv`, `staging`, `tools`.

### `pipenv`

[`src/fsbpyr/pipenv.py`](../src/fsbpyr/pipenv.py)

`Pipfile.lock` as the lock of a build (D5), for a project and for a directory of scripts. Pipenv's
lock is JSON; its `default` section (never `develop`) becomes the same hashed requirements file
as a Poetry or PDM lock, with each entry's pinned version and hashes and its markers evaluated
for the target. A `Pipfile.lock` is flat and complete, so there are no groups or extras to choose
from. `_meta.hash` is the SHA-256 of the Pipfile, which tells a lock that is out of date, and
`_meta.requires` names the Python the project asks for.

| Function | What it does |
|---|---|
| `read(directory)` | The parsed `Pipfile.lock`; `ForeignLockError` for one that cannot be read or has no `default` section. |
| `python_of(lock)` | The minor version `_meta.requires` asks for (`python_full_version` or `python_version`), if any. |
| `entries(data)` | The `default` packages as `foreign.Entry` objects. |
| `export(directory, *, environment)` | The requirements file of the lock, for the target's markers. |
| `stale(directory)` | A message when `_meta.hash` is not the Pipfile's hash; without a Pipfile, a message when the `pyproject.toml` beside the lock declares dependencies that the lock replaces. |
| `other_indexes(directory)` | A note on the packages the lock takes from an index other than PyPI. |
| `tool_names(directory)` | The tools of E: the Pipfile's `[packages]` with their extras and markers, or the lock's `default` entries when there is no Pipfile. |

Rules it enforces:

- A registry entry must be pinned with `==` (`===` is refused); a `git` entry needs a `ref` that is
  a commit (40 or 64 hex digits); `hg`, `svn` and `bzr` entries are refused; a `path` entry,
  `editable` or not, installs as a regular package, and the entry that is the project itself
  (`pipenv install -e .`) is left out, since fsbpyr installs the project's own wheel; a `file`
  entry keeps its hash when the lock gives exactly one.
- The markers are evaluated with the extras that entries ask of their dependencies counting as
  active, since Pipenv marks what an extra needs `extra == 'x'`.
- A package from an index other than PyPI is installed from the indexes uv is configured with;
  the lock's URL is never passed on (it may name credentials by variable), and the lock's hashes
  decide whether uv found the same files. The note is `Uncounted`.
- The Pipfile's hash follows `Pipfile.get_hash` of Pipenv's `plette` (`_pipfile_hash`): the
  SHA-256 of the sources, the `requires` and the package tables as sorted compact JSON. The
  `foreign` fixture's
  [README](../tests/fixtures/projects/foreign/README.md)
  notes that the formula has not been checked against a lock that Pipenv wrote.

- Imports: `foreign`, `project`.
- Imported by: `build`, `manifest`, `project` (lazy), `staging`, `tools`.

### `venvdeps`

[`src/fsbpyr/venvdeps.py`](../src/fsbpyr/venvdeps.py)

The dependencies a script's own environment holds: the venv rung, for C and D when nothing
declares a dependency and a command imports what the runtime lacks. The venv's `*.dist-info`
directories are read as files and never executed: `METADATA` (name, version, `Requires-Dist`),
`top_level.txt` and `RECORD` say which distribution provides which import name, and
`direct_url.json` says whether a distribution is the user's own tree. Nothing is copied: the
distributions that provide the imports, and their `Requires-Dist` closure as installed, become
`name==version` pins, which uv installs fresh for the target. A PyPI name is never guessed from
an import name; an import that no installed distribution lists stays unresolved.

| Class | What it represents |
|---|---|
| `VenvDist` | An installed distribution: name, version, `Requires-Dist`, the top-level import names it provides, its `RECORD` paths, and the PEP 610 URL of an editable or path install. |
| `Inferred` | The venv and the pins read from it. |

| Function | What it does |
|---|---|
| `find_venv(directory, environ)` | `.venv`, `venv` or `env` in the directory, else `$VIRTUAL_ENV`; each needs a `pyvenv.cfg`. |
| `read_venv(venv)` | The distributions of the venv's `lib*/python3*/site-packages`, by canonical name. |
| `infer(venv, modules, environment)` | The pins for the unresolved imports `modules`, or None when no installed distribution provides any of them. |

Rules it enforces:

- A distribution provides an import when `top_level.txt` or the first part of a `RECORD` path
  names it; when several do (a namespace), those whose `RECORD` holds the module itself win.
- The closure follows `Requires-Dist` lines whose marker holds for the target, extras not
  followed; a requirement the venv does not hold is left to uv.
- A distribution whose name or version is not valid, or whose name is not its directory's, is
  skipped.
- An editable or path install that is needed raises `ProjectError`: it is the user's own tree.
- `build` records the result as the lock source `inferred` (`manifest.inferred_lock`) and warns
  with the `uv pip freeze --python <venv>/bin/python` command that declares the pins.

- Imports: `project`.
- Imported by: `build`, `manifest`.

## Static import check

A script of B, C or D imports what its developer's environment had; the executable has what the
project declares. Before anything is packaged, the build follows each command's imports with
`ast`, never importing anything, and skips a command that imports what the executable will not
hold. `importcheck` follows the imports, `syspath` the directories a script adds to `sys.path`.

### `importcheck`

[`src/fsbpyr/importcheck.py`](../src/fsbpyr/importcheck.py)

The static import check of a project's script commands. For each command it follows the imports
of the script and of the project's own modules it reaches, and reports every required import that
resolves nowhere: the script's directory first (as at run time, where the boot puts it first on
`sys.path`), then the installed distributions and the runtime's modules, then the directories
files add to `sys.path`. It also gives D its files: the script's local-import closure.

| Class | What it represents |
|---|---|
| `Unresolved` | An import nothing resolves: the project file, the line, the module as written (a relative import with its dots), and why when it is not just a missing module; `describe` gives `file:line: ...`. |

The private classes `_Project` (the project's files below the scripts root), `_Search` (where one
command's imports are looked up) and `_Disk` (the files next to a script, looked up on disk) do
the lookups.

| Function | What it does |
|---|---|
| `top_level_names(site)` | The modules and packages a site directory provides: its files and directories, apart from metadata. |
| `check_command(target, sources, known)` | The required imports of the command `target` that nothing resolves; `known` is the top-level names of the installed distributions and the runtime. |
| `local_closure(directory, payload, source, origin)` | D's files: the script, stored as `payload`, and the sibling modules and packages it reaches by static imports, transitively, read from disk. |

`REMOVED_IN` maps the standard library modules removed in Python 3.12 (5) and 3.13 (20, counted
in the source) to the version that removed them; `build` uses it for its hint.

Rules it enforces:

- An import is optional under a `try` whose handlers catch `ImportError`, `ModuleNotFoundError`,
  `Exception`, `BaseException` or anything, in the handlers of such a `try`, under
  `if TYPE_CHECKING:`, and in both branches of an `if` that tests `sys.platform`, `os.name` or
  `sys.version_info`.
- A relative import is resolved from the file's package; one that climbs above the scripts root
  resolves nothing. `from pkg import name` follows `name` as a submodule when the project has it.
  A name that starts with `_fsbpyr_scripts` is looked up from the root.
- The directories a file adds to `sys.path` (`syspath.path_entries`) are searched after the
  others, in the order they were found; an import that was missing is tried again once a new
  directory is found, until nothing changes.
- When any file reached changes `sys.path` in a way that cannot be followed, the command's
  unresolved imports are not reported: the check cannot tell what the path holds. A file that does
  not parse is always reported.
- A command whose target is not below the scripts root (a dependency's script) has nothing to
  follow.
- `local_closure` touches only the paths an import names, so a script in a home directory walks
  nothing; it skips the directories `loose` skips, refuses a module that is a link to a file
  outside the directory, and refuses a sibling that would be stored under the script's own stored
  name.

- Imports: `loose`, `project`, `syspath`.
- Imported by: `build`, `loose` (lazy).

### `syspath`

[`src/fsbpyr/syspath.py`](../src/fsbpyr/syspath.py)

The directories a script puts on `sys.path` with a path built from its own `__file__` (the common
`sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pylib"))`), found with
`ast` for the import check. A path that cannot be followed is told apart from one the payload
cannot serve: the first may hold anything, the second holds nothing. The executable's path hook
normalizes `..` itself and never goes above the executable, so a written `..` is followed the
same way; a directory that is no Python name is searched under its stored name.

| Function | What it does |
|---|---|
| `path_entries(tree, file)` | The directories below the scripts root that the file adds to `sys.path`, and whether it changes `sys.path` in any other way. |

Rules: the calls are `sys.path.append`, `insert` and `extend` anywhere in the file (with a list or
tuple, each element). A path is followed through `__file__`, `os.path.dirname`, `os.path.join`
with string literals, `/` and `+` with string literals, `.parent`, `.resolve()`, `.absolute()`,
and `str`, `os.fspath`, `Path`, `pathlib.Path`, `PurePath`, `os.path.abspath`, `os.path.realpath`
and `os.path.normpath` of one argument. Anything else on such a call, and an assignment to
`sys.path`, counts as a change it cannot follow. A path that `..` takes above the scripts root is
neither found nor a change.

- Imports: `loose`.
- Imported by: `importcheck`.

## Staging with uv

fsbpyr never packages the developer's environment. It installs the project afresh, with uv, into
a temporary environment, and packages what is installed there.

### `staging`

[`src/fsbpyr/staging.py`](../src/fsbpyr/staging.py)

The staging install (D5), done entirely by uv (D26), pip projects included, so that every build
picks wheels for the runtime's glibc baseline. A project with a build system (A) is always built
into a wheel out of tree (uv builds an sdist, then a wheel from it), so stale in-tree build
directories and cached builds cannot leak into the executable; for B to E fsbpyr writes the wheel
itself (`loose.Package.write_wheel`). Dependencies are installed from the lock into a throwaway
environment, then the fresh wheel without dependencies. The developer's virtual environment is
never read or modified, and every command runs in the project directory, so relative paths in
requirements files resolve as the user expects.

| Lock source | uv commands after the wheel is made (`uv build --out-dir WORK/wheel --python X.Y PROJECT`, or `write_wheel`) |
|---|---|
| `uv.lock` | `uv sync --locked --no-dev --no-default-groups --no-editable --no-install-project` into the staging environment (`UV_PROJECT_ENVIRONMENT`), with `--package` for a workspace member, `--reinstall-package` for each local package of the lock and `--extra` for each extra; then `uv pip install --no-deps` the wheel |
| `script.lock` | `uv sync --script SCRIPT --active --locked` into the staging environment (`VIRTUAL_ENV`); then `uv pip install --no-deps` the wheel |
| `pylock.toml` | `uv venv`, `uv pip install -r pylock.toml`, `uv pip install --no-deps` the wheel |
| requirements file | as `pylock.toml`, with `--no-editable`, and `--require-hashes` under `--strict` or when the file has `--hash` options |
| `poetry.lock`, `pdm.lock`, `Pipfile.lock` | as a requirements file, from `WORK/<lock>.requirements.txt`, which `foreign.export` or `pipenv.export` writes; `--require-hashes` when every line carries a hash |
| unlocked, inferred | `uv venv`, then `uv pip install` the wheel with its extras, which resolves the dependencies now (for inferred, the pins the wheel requires) |

Every install command gets `--python-platform` with the target's uv platform for the glibc
baseline (for example `x86_64-manylinux_2_34`, or `x86_64-manylinux_2_28` for glibc 2.28),
`--offline` for an offline build, and `--quiet`; `uv build` is not quiet,
so that a failure carries the build backend's own reason. `uv venv` runs with `UV_VENV_SEED=0`,
since a seeded environment would ship pip.

| Class | What it represents |
|---|---|
| `StagingError` | The staging install could not be planned, or failed. |
| `StagingOptions` | The runtime's Python, extras, offline, requirements file, uv platform, strict, the `loose.Package` whose wheel fsbpyr writes, and the target's marker environment for converted locks. |
| `StagingPlan` | The site-packages path, the wheel build command (empty when fsbpyr writes the wheel), the install commands (with a placeholder for the wheel), the wheel directory, extra environment variables, the command that drops uv's cached builds, and the package. |

| Function | What it does |
|---|---|
| `find_uv()` | uv on `PATH`, or `StagingError` with how to install it. |
| `uv_version(uv)` | uv's version string, for build_info. |
| `local_packages(lock, exclude)` | Packages a `uv.lock` builds from local sources (editable or directory), reinstalled on every build because uv caches such builds by their `pyproject.toml`, not their sources. |
| `staging_plan(project, source, uv, work, options)` | The plan for the lock source, with `SOURCE_DATE_EPOCH` added. |
| `leave_tree_unchanged(project_dir)` | A context manager that removes the `*.egg-info` (in the project directory and one level below) and `build/` that a build created in the source tree; what existed before is never touched. |
| `rebuild_wheel(plan, project_dir, env, *, log, verbose)` | The project's wheel built again with `env` added, into a new directory. |
| `rebuild(plan, project_dir, wheel, names, env, *, log, verbose)` | Installs `names` again, built anew from their sdists with `env` added, after dropping uv's cached builds of them. |
| `run_plan(plan, project_dir, *, log, verbose)` | Makes the wheel, runs the installs, returns the wheel. |

Rules it enforces:

- `SOURCE_DATE_EPOCH` is the user's when set, else the project's last commit time
  (`vcs.commit_time`), never earlier than 315532800 (1980-01-01, the earliest time a zip entry can
  hold). The wheel fsbpyr writes is stamped with it, and a value that is not a number of seconds
  is a `StagingError`.
- A `uv.lock` project needs `[project] name` (`StagingError`); `uv build` must leave exactly one
  wheel, and runs inside `leave_tree_unchanged`.
- A failing uv command raises `StagingError` naming the step, with the end of uv's output: its
  last 3000 characters from the first line that starts in them (`text.tail_lines`); so does a
  `uv --version` that reports no version. With `--verbose` each command is printed and uv's
  standard error goes straight through.
- `rebuild` skips `uv venv` and adds `--reinstall-package` for each name; uv builds before it
  installs, so a failed rebuild leaves the staged site as it was.

- Imports: `closure`, `foreign`, `loose`, `pipenv`, `project`, `text`, `vcs`.
- Imported by: `build`, `cli` (lazy).

## What the app contains and needs

These modules read the staged site and decide what goes into the payload. `closure` indexes the
installed distributions and decides the entry point; `classify` turns their files into payload
modules and files; `scan` supplies the `--hints` guesses; `natives` and `elf` work out what each
native library loads and needs from the system; `system` sums that up for build_info and for the
warnings. The payload records they produce are described on [payload modules](Dev-Payload-Modules.md)
and [executable format](Dev-Executable-Format.md).

### `closure`

[`src/fsbpyr/closure.py`](../src/fsbpyr/closure.py)

The installed distributions of a staging site. `SiteIndex.scan` reads each `*.dist-info`
(`METADATA`, `RECORD`, `entry_points.txt`, `direct_url.json`). The index finds the project's own
distribution by name, or else by the PEP 610 URL of the project directory. Its
`walk` follows `Requires-Dist` from the project under the runtime's marker environment, as a
consistency check: it fails on a missing dependency, warns about a version that does not satisfy
its requirement, and lists the installed distributions nothing requires. The module also turns
`--entry`, the project's commands or the inferred entry into the app's name and entry point; with
several commands the entry point is a table of `name=target` lines, which the boot's
`_fsbpyr_rt.commands` reads.

| Class | What it represents |
|---|---|
| `ClosureError` | The staging site does not hold a consistent installation of the project. |
| `InstalledDist` | One installed distribution: name, version, dist-info directory, requirements, `RECORD` lines, entry points, PEP 610 data, extras, and the legacy scripts `legacy.attach` added. `console_scripts()` and `gui_scripts()` drop the `[extras]` suffix of each script; `commands()` is the console scripts, the `fsbpyr_scripts` group of `loose` and the legacy scripts. |
| `Walk` | The walk's result: reachable and unreachable distributions, and warnings. |
| `SiteIndex` | The site and its distributions by name: `scan`, `check_no_editable`, `project`, `walk`. |

| Function | What it does |
|---|---|
| `marker_environment(python_version)` | The app's marker environment: this host's, with the runtime's Python. |
| `requirement_of(line, dist)` | A `Requires-Dist` line parsed; `ClosureError` when it is invalid. |
| `several_scripts_error(name, scripts, candidates)` | The error for no `--entry` and no entry to take, listing the console scripts and the inferred candidates. |
| `check_command_names(names)` | `ClosureError` for a command name that is not a `links.COMMAND_NAME`. |
| `command_table(scripts)` | The entry point of several commands: `name=target` lines, sorted by name. |
| `unknown_extras_error(name, wanted, known)` | The message for requested extras the project does not have, else None. |
| `infer_entry(dist)` | (app name, entry point, why) for a distribution with no commands: its only top-level package with `__main__.py`, else its only top-level module with a main guard (`entry.installed_candidates`). |
| `resolve_entry(dist, entry, log, omit)` | (app name, entry point) from `--entry`, the only command, the command table (named after the distribution) or `infer_entry`, which it logs; the commands in `omit` are left out. |

Rules it enforces:

- An editable install cannot be packaged (`ClosureError`).
- A required distribution missing from the site, or an invalid `Requires-Dist`, raises
  `ClosureError`.
- Every command name, of one command as of several, must match `[A-Za-z0-9][A-Za-z0-9_.+-]*`.
- `--entry` must be a command name, or `module[:attribute]` made of identifiers. For a module
  entry the app's name is the last part of the attribute, or of the module.
- Several inferred candidates, or a package and a module, raise the "choose one with --entry"
  error, which lists them.

- Imports: `entry`, `links`, `loose`.
- Imported by: `build`, `classify`, `cli` (lazy), `legacy`, `manifest`, `staging`, `tools`.

### `classify`

[`src/fsbpyr/classify.py`](../src/fsbpyr/classify.py)

`RECORD` entries to payload modules, files and namespace packages. For every installed
distribution, in name order, each file its `RECORD` lists becomes a module (a `.py` with an
importable name, compiled by `compiler`), an extension module or shared library (an ELF object
for the host's machine), a resource, or metadata. Names follow CPython's search order: the
site-packages directory, then the directories that top-level `.pth` files add, a package before a
module, an extension suffix before `.py`. The native files go through `natives.closures`, which
records what each extension loads. Then implicit namespace packages are added, fs-packages are
marked, and the hints are made. The result is sorted by name and path, so it does not depend on
the order the work finished in.

| Class | What it represents |
|---|---|
| `ClassifyError` | The installed files cannot be packaged. |
| `Uncounted` | A `str` subclass for warnings that `--strict` does not count. |
| `Content` | The result: modules, files, distributions, warnings, the newest glibc need of a native file (and which file), and `natives.SystemUse`. |

The private class `_Collector` holds the classification's state and does the work described here.

| Function | What it does |
|---|---|
| `module_name(rel)` | Dotted name and is-package flag of a `.py` path, if importable; a module file's stem may hold hyphens or start with a digit, as `importlib.import_module` reaches it. |
| `extension_name(rel, suffixes)` | Dotted name, is-package flag and suffix rank of an extension module file; the stem may start with a digit but never holds a hyphen. |
| `classify(dists, *, extension_suffixes, include_source, ..., project, origins, entrypoint)` | The whole classification; with a spill, no file's bytes stay in memory. `project` is the project's distribution, `origins` the project file of each stored script path (C and D), `entrypoint` the app's entry point, which names the modules that run as `__main__`. |

Constants: `INSTALLER_FILES` are dist-info files that installers write (`INSTALLER`, `REQUESTED`,
`direct_url.json`, `uv_cache.json`, `RECORD.jws`, `RECORD.p7s`), left out because they embed paths,
times or the installer's name. `BUILTIN_FS_PACKAGES` (babel, botocore, certifi, litellm,
matplotlib, torch) always get their files on disk.

Rules it enforces:

- Left out, with a warning where it matters: files outside site-packages (counted by directory;
  `bin/` scripts silently, except a file of the project's own under `bin/` that is neither a
  console or gui script nor a `#!python` legacy script, which is named), `__pycache__` and `.pyc`
  files (bytecode without source gets a warning), and a path a second distribution installs
  again.
- The `RECORD` written into the payload lists only the packaged entries.
- A `.py` file that does not compile is kept as data with a warning, and a package whose
  `__init__.py` does not compile gets no namespace stand-in. A `.py` file over 16 MiB is kept as
  data ("too large to compile"), and any file over 16 MiB is compressed from its file a piece at a
  time rather than read whole (the build always passes a spill). A `.py` file of the project below
  a directory whose name is no Python name is stored as a file, with a warning that no import
  reaches it.
- The `SyntaxWarning`s and `DeprecationWarning`s that compiling the project's own modules emits are
  build warnings, `file:line: Category: message`, with a script's project file name;
  dependencies and the standard library stay silent.
- A non-ELF file, or an ELF object for another machine, with a shared-object name is kept as data
  with a warning.
- A module the runtime provides (standard library, built-in, frozen, boot) is packaged as a file,
  not a module, with a warning, unless the project's own distribution installs it; then
  `assemble` refuses the build.
- `.pth` files: path lines become extra module roots (a path outside site-packages is warned
  about); code lines are never run in executables, which is warned about unless the file only
  recreates namespace packages (`-nspkg.pth`, `distutils-precedence.pth`).
- Resources that were executable in the wheel get mode 0755, other files 0644, and native files
  0755, so the installer's umask does not reach the build.
- An `fs-packages` entry that is not a regular package of the app raises `ClassifyError`; every
  non-native file below a marked package is flagged `FS_REQUIRED`.
- A `NativeError` from `natives.closures` becomes `ClassifyError`.
- Hints: for the files under `_fsbpyr_scripts/` (the scripts of B, C and D, and legacy scripts),
  always, each read next to `__file__` (from `scan`), as a warning to deploy that file beside the
  executable, where the script's `__file__` points. A main guard counts only in the modules the
  executable runs as `__main__` (`_mains`: each target of the entry point that names a module,
  not `module:function`, below the scripts root; a package by its `__main__.py`), and a call that
  starts `sys.executable` is no read, since the sibling it runs comes from the payload. With
  `--hints`, as an `Uncounted` warning, packages that read files next to `__file__`, except in
  `tests`/`test` directories and below `_fsbpyr_scripts/`; always, as `Uncounted` warnings,
  shared libraries no extension loads that packaged Python code names, and ELF executables among
  the resources outside fs-packages.

- Imports: `closure`, `compiler`, `elf`, `loose`, `natives`, `boot._fsbpyr_rt.commands`,
  `payload.format`, `payload.spec`, `payload.spill`.
- Imported by: `build`, `cli` (lazy).

### `scan`

[`src/fsbpyr/scan.py`](../src/fsbpyr/scan.py)

The heuristics behind `--hints` and the build's notes on scripts, over a module's syntax tree. In
an executable a module's `__file__` points into the payload's virtual tree, so code that reads
files next to it fails unless its package is in fs-packages. `file_relative_reads` flags calls
that read the file system, run a program, or load a library or data file, and pathlib read
methods, whose argument or receiver mentions `__file__` or `__path__` (also another module's,
`X.__file__`), directly or through a name assigned from it. `loaded_libraries` lists the system
libraries a module opens by a literal name with ctypes or cffi.

| Function | What it does |
|---|---|
| `file_relative_reads(source, filename, *, script, main)` | Line numbers of reads relative to `__file__`. `main`: the module runs as `__main__`, so its main guard counts; `script`: a module of the scripts root, whose call that starts `sys.executable` reads nothing. |
| `loaded_libraries(source, filename)` | Library names opened by a literal: `CDLL("libmagic.so.1")` gives the name, `find_library("magic")` gives `libmagic`; a path is left out. |

Rules: a module without any of the marker strings is skipped without parsing; one that does not
parse gives no result; calls inside a top-level `if __name__ == "__main__":` block are ignored
unless `main`; with `script`, a `subprocess.run`, `Popen`, `call`, `check_call`, `check_output`
or `os.exec*` call whose program is `sys.executable` (its first argument or `args=`, or the first
element of a list or tuple there) is not a read, since it runs a sibling from the payload, while
the same call in a package is; names assigned from a tainted expression are followed for at most
5 passes; jinja2's `PackageLoader` is flagged by the call alone.

- Imports: nothing from fsbpyr.
- Imported by: `compiler`.

### `natives`

[`src/fsbpyr/natives.py`](../src/fsbpyr/natives.py)

Native dependency analysis at build time. Every `DT_NEEDED` entry of every shared object in the
payload must resolve to another payload file, through the object's RUNPATH (or RPATH) with
`$ORIGIN` expanded, or to a system library that manylinux wheels may rely on. An object without a
RUNPATH also searches the RPATHs of the objects that loaded it, as `ld.so` does. Bundled libraries
in the object's directory or on its search path whose file name it contains as a C string count as
loaded too (opened with `dlopen` at run time). A library nothing on the search path provides is
taken from another distribution's bundled libraries by SONAME or file name, when exactly one has
it. Because the native cache mirrors site-packages (D9), `$ORIGIN` paths keep working unchanged at
run time.

| Class | What it represents |
|---|---|
| `NativeError` | A native dependency cannot be satisfied inside the executable or by the system. |
| `MissingLibraries` | The libraries one native file needs that are neither bundled nor allowed; `closures` gathers them for all files and reports them together. |
| `SystemUse` | What the payload takes from the system: which files need each system library, the symbol versions they need, the allowlist entries used, unreadable version tables, and `--hints`' guesses (`may_load`). |

| Function | What it does |
|---|---|
| `system_libraries()` | The libraries native code may take from the system: the auditwheel manylinux list in `_SYSTEM_LIBRARIES` plus the host's dynamic loader. |
| `glibc_libraries()` | The libraries that are glibc itself, plus the loader. |
| `manylinux_tag(baseline)` | The wheel platform tag of a glibc baseline: `manylinux_2_34` for `2.34`, `manylinux_2_28` for `2.28`. |
| `manylinux_limits(baseline)` | The newest GLIBCXX, CXXABI, GCC and ZLIB versions that auditwheel's policy for the baseline's manylinux tag allows (2.34 by default), for the host's target (`MANYLINUX_LIMITS`). |
| `opened(native)` | A native file's content as given, or its file mapped read-only, never read whole (D38). |
| `init_symbol(path)` | The `PyInit_<name>` C string CPython looks up in an extension; a compiled package `__init__` takes the package's name. |
| `is_extension_module(path, data)` | Whether a shared object contains that symbol name. |
| `closures(natives, extensions, *, allowed, warn, system, executables)` | For each extension module, the bundled shared objects it loads, transitively. |

`MANYLINUX_LIMITS` holds the limits per baseline and target, taken from auditwheel's policy file
(the source says when it was read); `BASELINE_SYSTEMS` names the systems a baseline stands for in
`system`'s warnings, and `MANYLINUX_MARKERS` the non-numeric CXXABI versions that need no
warning (`CXXABI_TM_1`, `CXXABI_FLOAT128`, one set for every target):

| Baseline | Policy | GLIBCXX | CXXABI | GCC (libgcc_s) | ZLIB | Systems in warnings |
|---|---|---|---|---|---|---|
| 2.34, the default | manylinux_2_34 | 3.4.29 | 1.3.13 | 7.0.0 on x86-64, 11.0 on aarch64 | 1.2.9 | RHEL 9, Ubuntu 22.04 |
| 2.28 | manylinux_2_28 | 3.4.24 | 1.3.11 | 7.0.0 | 1.2.9 | RHEL 8 |

Rules it enforces:

- An extension linked against `libpython3*` is refused (it must take the symbols from the
  interpreter), and so is a `DT_NEEDED` entry with a `/`, which `ld.so` would open relative to the
  working directory.
- Libraries that are neither bundled, standard system libraries nor allowed with
  `allow-system-libs` fail the build with one message listing every file and what it needs, and
  the `[tool.fsbpyr]` line that would allow them.
- Two bundled libraries of the same name in other top-level directories fail the build: they would
  load as one, and the build cannot tell which the app gets.
- A library reached only by name whose own needs cannot be met is left out with a warning.
- Shared objects no extension reaches, and ELF executables among the resources, are checked as far
  as their needs go, with warnings instead of errors.
- RPATH and RUNPATH entries not based on `$ORIGIN` are warned about: `ld.so` searches them on the
  target. An `allow-system-libs` entry no native file needs, and an unreadable version table, are
  warned about too.
- Searches go through a file 64 MiB at a time, and a mapping's pages are dropped after each window.

- Imports: `elf`, `targets`, `runtime.pins`.
- Imported by: `build`, `classify`, `inspection`, `system`.

### `elf`

[`src/fsbpyr/elf.py`](../src/fsbpyr/elf.py)

A minimal ELF64 reader for what bundling native code needs: the dynamic section (`DT_NEEDED`,
`DT_RPATH`, `DT_RUNPATH`, `DT_SONAME`), the symbol versions an object requires (`DT_VERNEED`),
whether an object is a program, and a section's content by name. It works on bytes or on a
memory-mapped file, so a large library is never read whole.

| Class | What it represents |
|---|---|
| `ElfError` | Not a usable ELF64 object of the host's architecture. |
| `DynamicInfo` | `needed`, `rpath`, `runpath` and `soname` of an object. |
| `VersionNeed` | What an object needs from one version namespace of one library: the newest numeric version and the other markers. |

| Function | What it does |
|---|---|
| `is_elf(data)` | Whether the data starts with the ELF magic. |
| `is_foreign(head)` | Whether an ELF object is for another machine, or 32-bit, or big-endian; False when the header is too short to tell. |
| `is_executable(data)` | Whether an object is a program: `ET_EXEC`, or position-independent with a `PT_INTERP`. |
| `section(data, name)` | The content of the named section (`inspection` reads the stub's `.comment`, `runtime.pbs` the C strings of zstd's `zstd_common.o` for its version). |
| `dynamic_info(data)` | The `DynamicInfo` of an object. |
| `needed_versions(data)` | The (library, version) pairs of `DT_VERNEED`. |
| `glibc_requirement(data)` | The newest glibc an object needs; `GLIBC_2.2.5` counts as 2.2, and `GLIBC_ABI_DT_RELR` as 2.36. |
| `version_requirements(data)` | Per library and namespace (GLIBCXX, CXXABI, GCC, ...), the newest version and the markers. |

The private `_Dynamic` holds one object's parsed dynamic section.

Rules: only 64-bit little-endian objects for the host's `e_machine` are read; anything else, a
truncated table, an address not mapped from the file or a string out of range raises `ElfError`.
Entry counts, name lengths and search-path lengths have fixed limits (`_MAX_ENTRIES`, `_MAX_NAME`,
`_MAX_SEARCH_PATH`), and the version-need entries share one budget, so that overlapping chains
cannot multiply the work.

- Imports: `targets`.
- Imported by: `build`, `classify`, `inspection`, `natives`, `system`, `runtime.pbs`.

### `system`

[`src/fsbpyr/system.py`](../src/fsbpyr/system.py)

What the target must provide beyond what is bundled: the system libraries and their symbol
versions, checked against the floors of the manylinux policy of the glibc baseline the wheels
were chosen for (manylinux_2_34, or manylinux_2_28 for glibc 2.28; the limits are under
[`natives`](#natives)). glibc has its own report (`build.glibc_report`); here are the
libraries around it, libstdc++, libgcc_s, libz, X11 and the like, and the launcher stub's own
needs, which appear as `(stub)`.

| Function | What it does |
|---|---|
| `target_needs(libraries, glibc)` | The one-line summary: `glibc >= X.Y` and each non-glibc library with its versions. |
| `system_report(stub, use, baseline)` | build_info's library table (per library: the files that need it, the newest version per namespace, markers) and the warnings. |
| `system_info(libraries, use)` | build_info's `system`: the libraries and, with `--hints`, the libraries Python code may open by name. |

Rules: one warning per library and namespace whose need is above the baseline policy's limit,
naming the newest need, how many other files go over, and the systems that policy stands for
(RHEL 9 and Ubuntu 22.04 for manylinux_2_34, RHEL 8 for manylinux_2_28); a warning for any file
that needs `GLIBC_PRIVATE`, and for a non-numeric version in those namespaces that the manylinux
policy does not list (`MANYLINUX_MARKERS`). A stub that cannot be read adds nothing.

- Imports: `elf`, `natives`, `runtime.pins`.
- Imported by: `build`, `inspection`.

## Compiling to bytecode

Every module of the app is compiled to bytecode at build time and packaged that way. The bytes
must not depend on the state of the build's interpreter or on how the work is spread over
processes (D15, D16, D39).

### `compiler`

[`src/fsbpyr/compiler.py`](../src/fsbpyr/compiler.py)

Modules compiled ahead of the classifier (D39). A build's modules are compiled, canonicalized and
compressed on up to 8 worker processes (the GIL keeps threads from compiling), with a window of
them in flight, and handed to the classifier in the order it reaches them, so what it registers,
warns about and writes is the same whatever the number of cores. The files the classifier reads
whole are compressed on the same workers. A build of fewer than 256 modules (`POOL_MODULES`), or
one with a single usable CPU, compiles in its own process.

| Class | What it represents |
|---|---|
| `Compiled` | A module's code blob and source blob, their content digests, the lines that read next to `__file__`, the libraries it opens by name, the warnings compiling emitted; or the syntax error's message and line. |
| `Job` | One module to compile: its (distribution, path) key, file, relative path, whether its reads next to `__file__` are wanted, and whether it runs as `__main__`, so that its main guard runs. |
| `Compiler` | The jobs compiled ahead on workers; `get` returns them in order, `compress` compresses a file on a worker, `close` stops the pool. |

| Function | What it does |
|---|---|
| `compile_module(source, rel)` | Canonical bytecode: `compile(..., optimize=0, dont_inherit=True)` then `bytecode.dumps`, independent of the build interpreter's `-O` (D15). |
| `compile_warned(source, rel)` | `compile_module`'s bytecode and the `SyntaxWarning`s and `DeprecationWarning`s compiling emitted, each as (line, `Category: message`). |
| `compiled(data, rel, level, include_source, hints, main)` | A `Compiled` for one module: code blob, source blob unless sources are left out, the warnings, and the `scan` results when asked (`main` for a module that runs as `__main__`; a module below `loose.ROOT` is scanned as a script). |
| `worker_pool()` | Up to 8 worker processes: forked when the process runs one thread, else started by a fork server. |
| `compressed(data, level)` | A file's zstd blob and content digest. |

Rules: the filename passed to `compile` is a fresh copy of the string, because marshal writes an
interned string differently from the one a worker gets by pickle; warnings during compilation are
caught and never printed, and the ones returned never change the bytecode; at most 4 jobs per
worker and 64 MiB of their sources are in flight; a job the classifier passes over is cancelled,
and one it asks for that was not ahead is compiled in the build's own process. With a fork server
the main module is imported again, so a program that builds from several threads must keep its
main module free of side effects.

- Imports: `bytecode`, `loose`, `scan`, `payload.format`, `payload.spec`.
- Imported by: `classify`, `runtime.stdlib`.

### `bytecode`

[`src/fsbpyr/bytecode.py`](../src/fsbpyr/bytecode.py)

Canonical marshal encoding of code objects (D16). `marshal.dumps` output depends on the process
that runs it: `FLAG_REF` is set on any object whose reference count is above one, and whether a
string constant is interned depends on equal strings interned earlier. `canonicalize` parses a
marshal stream into an object graph and writes it again with a policy that depends only on the
graph:

- strings are merged by value, and a merged string is interned if any occurrence was;
- equal immutable values (numbers, bytes, strings, and tuples and frozensets of them) are one node;
- `FLAG_REF` is set exactly when a node occurs at least twice, or is an interned string;
- reference indices are assigned in pre-order, the order the reader uses;
- set and frozenset elements are sorted by their canonical standalone encoding.

`marshal.loads` of the canonical stream gives objects equal to the original. The supported formats
are marshal version 4 (CPython 3.12 and 3.13), 5 (3.14, which adds the slice type) and 6 (3.15);
each fsbpyr handles the version of the Python it runs on.

| Class | What it represents |
|---|---|
| `BytecodeError` | The marshal stream is malformed or uses an unsupported type. |

The private classes `_Node`, `_Reader` and `_Writer` are the object graph, its parser and its
writer.

| Function | What it does |
|---|---|
| `canonicalize(data)` | The canonical form of one marshalled object. |
| `dumps(code)` | `marshal.dumps` then `canonicalize`, for a supported Python only. |

Rules: an unknown marshal type (3.15's frozendict among them, which `compile` never puts among a
code object's constants), a truncated stream, trailing bytes, an invalid or recursive reference
and nesting deeper than 100 raise `BytecodeError`. `SUPPORTED_PYTHON` is the set of versions
fsbpyr runs on and builds for; `cli`, `pyversion` and `build` (for the hint on removed standard
library modules) read it.

- Imports: nothing from fsbpyr.
- Imported by: `assemble`, `build`, `cli` (lazy), `compiler`, `pyversion`, `runtime.stdlib`.

## Recording provenance

An executable records what it was built from in its build_info (keys on
[executable format](Dev-Executable-Format.md)). `manifest` describes the lock and every
distribution; `vcs` the project's git commit, and fsbpyr's own version and release asset names.

### `manifest`

[`src/fsbpyr/manifest.py`](../src/fsbpyr/manifest.py)

build_info's `lock` and `dists` (REP-3). Everything here is a pure function of the lock file's
bytes and the staged site, so identical inputs give identical records, whatever directory the
project is in. A distribution is traced to the artifact the lock names for it: the wheel whose
tags the installed `WHEEL` file shares. An installed wheel that shares none was built on the build
host from the package's sdist, so its bytes depend on that host.

| Class | What it represents |
|---|---|
| `LockError` | The lock file cannot be read. |
| `LockedPackage` | A package as the lock lists it: source kind, version, wheels with their SHA-256, the sdist's SHA-256, and whether it lists hashes of files it does not name (`Pipfile.lock`). |

| Function | What it does |
|---|---|
| `read_lock(source, text)` | The packages of a `uv.lock`, script lock, `pylock.toml`, `poetry.lock`, `pdm.lock` or `Pipfile.lock` by canonical name; empty for a requirements file and an unlocked build. |
| `lock_record(directory, source, path)` | build_info's `lock` (`kind`, `path` relative to the project, `sha256` of the file, `hashed`) and the lock's packages. |
| `inferred_lock(directory, inferred)` | build_info's `lock` for pins read from a venv: `kind` `inferred`, the venv's path (relative to the project, or its name alone when it lies outside), no `sha256`, `hashed` false, and the `pins`. |
| `installed_tags(dist)` | The wheel tags in an installed distribution's `WHEEL` file. |
| `dist_record(dist, packages, *, project)` | One `dists` entry: name, version, `source`, and `artifact_sha256` when the lock has one; `backend` for one built from an sdist. |
| `dist_records(dists, packages, *, project)` | All entries, sorted by name and version. |
| `built_here(records)` | The names of the distributions built from their sdists on this host. |

Rules: `source` is `local` for the project and for the lock's local packages (uv's editable,
directory and virtual ones, a `pylock.toml` directory, a Poetry directory, a PDM or Pipenv
path); the lock's source kind with the matching wheel's hash (Poetry, PDM and
Pipenv kinds map to `registry`, `git`, `url`, `path` or `local`, another index to `registry`);
`built-from-sdist` when the lock lists wheels and none matches, or lists only an sdist (except for
a git source); the lock's kind without a hash when it names no file (a `Pipfile.lock` entry); for
a distribution the lock does not list (requirements, unlocked, inferred), the PEP 610 data decides
(`git`, `url`, `path` or `registry`). `hashed` is always true for `uv.lock` and a script lock,
true for a requirements file with a `--hash` option, and true for the other locks when they list
hashes. An unreadable lock raises `LockError`, which `build` turns into `BuildError`.

- Imports: `closure`, `foreign`, `pipenv`, `project`, `venvdeps`.
- Imported by: `build`.

### `vcs`

[`src/fsbpyr/vcs.py`](../src/fsbpyr/vcs.py)

The git commit and release tag a project is built from, the files a git work tree tracks, and
fsbpyr's version string. Releases are tagged `vX.Y.Z` (D28). A build records HEAD's commit and the
highest release tag pointing at it in build_info's `git`. `fsbpyr --version` prints
`0.1.0-6457a95b` for a build of a `v0.1.0` tag and `untagged-6457a95b` otherwise; an executable has
no git, so it prints what its build recorded (read through the built-in `_fsbpyr` module), and a
source checkout asks git. The module also names fsbpyr's release assets, and the companion
release that holds a release's glibc 2.28 stubs with its assets: a published release takes no new
assets, so the stubs of `vA.B.C` are a release of their own, `el8-vA.B.C`, published by
[`el8.yml`](../.github/workflows/el8.yml), and a
version may have none (D46).

| Function | What it does |
|---|---|
| `git_state(directory, *, warn)` | `{commit, tag}` of HEAD; None without git, outside a repository, or for a directory HEAD's tree does not contain. |
| `commit_time(directory)` | HEAD's commit time in seconds, under the same conditions; staging's `SOURCE_DATE_EPOCH`. |
| `work_tree_files(directory)` | The files below `directory` that its git work tree tracks, and the untracked ones git does not ignore (`git ls-files`); None without git or outside a work tree. `loose` packages the first and warns about the second. |
| `version_string(state)` | `X.Y.Z-<8 hex>`, `untagged-<8 hex>`, or `untagged-unknown` without a commit. |
| `own_version()` | fsbpyr's version: what the running executable's build recorded, else the checkout's. |
| `release_asset(python, target)` | The asset name without `.xz`, `fsbpyr-3.12-x86_64-linux-gnu`. |
| `release_stub_asset(python, target, glibc)` | The companion release's asset (without `.xz`) of the stub linked for the baseline `glibc` that goes with the fsbpyr of `python` on `target`, the launcher alone: `fsbpyr-stub-3.12-x86_64-linux-gnu-glibc2.28`. |
| `release_stub_sums(glibc)` | The companion release's asset that lists the SHA-256 of each stub of the baseline as `sha256sum` does, over the unpacked stubs: `SHA256SUMS-glibc2.28`. |
| `release_stub_tag(glibc, tag)` | The tag of the companion release that holds the stubs of release `tag` for the baseline: `el8-v@VERSION@` for `v@VERSION@`; `ValueError` for a baseline without one (2.34). |
| `release_asset_record(project, python, target)` | build_info's `release_asset`, only when the project is fsbpyr. |

Rules: git runs with `LC_ALL=C`, a 10-second timeout, and without the `GIT_DIR`-style variables a
hook or script could have set, which would point it at another repository. A directory that HEAD's
tree does not contain (a project copied into another repository) records no commit. A git error
other than "not a git repository" is passed to `warn` (an `Uncounted` warning in `build`). Only
tags that are `vX.Y.Z` whole count as release tags, so the prefix of a companion release's tag
(`el8-` for 2.28, `_STUB_RELEASE_PREFIXES`) must not start with `v`: `el8-v1.2.0` on the same
commit as `v1.2.0` leaves the recorded tag `v1.2.0`.

- Imports: nothing from fsbpyr.
- Imported by: `build`, `cli` (lazy), `dispatch`, `inspection`, `loose`, `selfupdate`, `staging`,
  `runtime.stubs`.

## Assembling

The last build-side step puts the runtime, fsbpyr's boot code and the app's content into one
payload spec and hands it to the payload writer. The layout it produces is on
[executable format](Dev-Executable-Format.md); the writer is on [payload modules](Dev-Payload-Modules.md).

### `assemble`

[`src/fsbpyr/assemble.py`](../src/fsbpyr/assemble.py)

Composes runtime, boot code and application content into one executable. `build_temporary` checks
that the app fits the runtime, compiles the boot modules afresh, adds the runtime's build_info
keys to the app's (`runtime`, `runtime_tag`, `extension_suffixes`, `python`, `fsbpyr`, `zstd`,
`resources`, `runtime_inputs`, `runtime_components`, `stub_sha256`), serialises build_info as
JSON with sorted keys, and has `payload.writer.write_temporary` write the stub and the payload to a
temporary file beside the output for the caller to test and commit.

| Class | What it represents |
|---|---|
| `AssemblyError` | The pieces cannot be combined into a valid executable. |
| `App` | The application content placed on top of a runtime: name, entry point (one target, or a table of commands), modules, files, distributions, build_info entries, zstd level. |

| Function | What it does |
|---|---|
| `boot_modules()` | `_fsbpyr_boot` (flagged STARTUP, filename `<frozen _fsbpyr_boot>`) and every module of `_fsbpyr_rt`, compiled to canonical bytecode with their sources. |
| `generation_id(runtime_tag, files)` | The native cache generation: SHA-256 of the runtime tag and the path, content digest and mode of every extractable file (D10). |
| `resources_id(files)` | The same over every payload file, 32 hex digits: the name of the tree where resources extracted on demand live (D27). |
| `stub_digest(stub)` | SHA-256 of the stub with its zero padding to the payload alignment: the same in every executable that has the same stub. It identifies the stub, not a release: releases that link it from the same inputs (the launcher sources and flags, the PBS pin, the sysroot and the layout version) with the same toolchain share it. |
| `runtime_module_names(runtime)` | The modules found before the payload's: the runtime's, the boot modules, CPython's built-in and frozen modules, and `_fsbpyr`. `build` also gives their top-level names to the import check. |
| `build_executable(runtime, app, out)` | `build_temporary` and `commit` in one call (the end-to-end tests use it). |
| `build_temporary(runtime, app, out)` | The executable, written beside `out`; returns the temporary path. |

Rules: the running interpreter's bytecode magic must equal the runtime's; an app module with the
name of a module found before the payload, or an app file with a runtime file's path, raises
`AssemblyError` (at most ten names are listed). Files are hashed in the order of their paths'
bytes.

- Imports: `fsbpyr` (the package), `bytecode`, `compat`, `payload.format`, `payload.spec`,
  `payload.writer`, `runtime.registry`.
- Imported by: `build`, `cli` (lazy).

## Reading executables

`fsbpyr inspect` and its options read an executable that fsbpyr built: what it was built from,
where its bytes go and which licence notices it carries. They read only the payload, through
`payload.reader`, so they work on executables built by older fsbpyr versions too, and since the
file may not be trusted, text output never carries control characters. The user-facing side is on
[Inspecting](User-Inspecting.md).

### `inspection`

[`src/fsbpyr/inspection.py`](../src/fsbpyr/inspection.py)

`fsbpyr inspect`: what an executable was built from, and with which fsbpyr (D28). `read` opens the
file, optionally verifies its digest, and gathers the build_info, distributions, modules by
distribution, shared libraries and extension modules, the stub's toolchain (its `.comment`
section), the sizes (`sizes.measure`) and, with `--files`, every `RECORD` entry as packaged.
`as_text` and `as_json` present the report; lists are laid out in as many columns as the terminal
fits, as `ls` does. The JSON always has `glibc_baseline`: the one build_info records, else 2.34,
the default, which a build records nothing for (D46); the text adds a line
`baseline   glibc 2.28` only when build_info records one. An executable with several commands
shows each command and its target (`commands` in the JSON, read with the boot's
`_fsbpyr_rt.commands.parse`), and a build whose
dependencies came from a venv shows its lock as `inferred from <venv>` with the pins. `self update`
and `dispatch` use `read(verify=True)` to check a download before anything runs it.

| Class | What it represents |
|---|---|
| `InspectError` | The file cannot be read, or fsbpyr did not build it. |
| `FileEntry` | One `RECORD` line as packaged: distribution, path, kind, size, SHA-256. |
| `Report` | Everything `read` found: path, size, app, build_info, distributions, modules, libraries, verification, digests, toolchain, entry point, sizes, files. |

| Function | What it does |
|---|---|
| `read(path, *, verify, files)` | The `Report` of an executable. |
| `as_json(report, *, all_modules)` | The report as JSON (its own `schema` 1), with build_info in full. |
| `as_text(report, *, all_modules)` | The report as lines of text. |

Rules: a path that is not a regular file is refused before it is opened (a FIFO would block); a
file that is not an fsbpyr executable, or fails verification, raises `InspectError`; the plain
SHA-256 of the whole file is computed only with `--verify`. An extension-module file named with a
plain `.so` (no ABI tag) is listed as an extension only if it contains its `PyInit_` name.
Non-printable text is shown as its `repr`.

- Imports: `elf`, `natives`, `sizes`, `system`, `text`, `vcs`, `boot._fsbpyr_rt.commands`,
  `payload.format`, `payload.reader`, `runtime.pins`.
- Imported by: `cli` (lazy), `dispatch`, `selfupdate`.

### `sizes`

[`src/fsbpyr/sizes.py`](../src/fsbpyr/sizes.py)

Where an executable's bytes go (DX-5), from the reader's records alone. The file is the stub, zero
padding to a 64 KiB boundary, the payload and the trailer; the payload is the header, the tables
and the blob data. The rows cover everything but the trailer, so they add up to the file's size
minus the trailer. Each blob has an owner (a distribution, or the runtime) and a part: code,
sources, resources or natives.

| Class | What it represents |
|---|---|
| `Sizes` | Stored bytes of the stub, padding, payload header, tables, blob padding, runtime, each distribution's parts, and unreferenced blobs; `total` adds them up. |

| Function | What it does |
|---|---|
| `measure(executable)` | The `Sizes` of an opened executable. |

Rules: a blob shared by several owners counts for the first in path order; a block's stored size
is split among its members by their raw sizes, the rounding remainder going to the first; a
stub's own zero tail counts as padding.

- Imports: `payload.format`, `payload.reader`.
- Imported by: `inspection`.

### `licensing`

[`src/fsbpyr/licensing.py`](../src/fsbpyr/licensing.py)

`fsbpyr inspect --licenses`: the third-party licence notices an executable carries (REP-4). The
runtime's own, under `_fsbpyr_licenses/` (the libraries linked into the stub, and fsbpyr's), are
exact. The distributions' are best effort: the files a package ships under the names licences
usually have, which is not every notice a wheel's bundled libraries need.

| Class | What it represents |
|---|---|
| `Notice` | One notice: its owner (`fsbpyr`, or a distribution and version), path and text. |

| Function | What it does |
|---|---|
| `read(path)` | The runtime's notices, then each distribution's. |
| `render(notices)` | One block per notice, headed `==== owner: path ====`. |

Rules: a distribution's notice is a resource or metadata file that its `METADATA` names as
`License-File`, or one under `.dist-info/licenses/`, or a file whose name starts with
`licence`/`license`, `copying`, `notice` or `thirdpartynotices` directly in the dist-info directory
or anywhere outside it. Control characters in the texts are shown escaped.

- Imports: `payload.format`, `payload.reader`, `runtime.notices`.
- Imported by: `cli` (lazy).

## Tool upkeep

Modules that maintain the fsbpyr executable itself rather than build apps: replacing it with a new
release, and rendering its man page. The user-facing side is on
[Other commands](User-Other-Commands.md).

### `selfupdate`

[`src/fsbpyr/selfupdate.py`](../src/fsbpyr/selfupdate.py)

`fsbpyr self update`: replaces the running executable with the latest GitHub release. It fetches
the release's `fsbpyr-3.X-<target>.xz` for the Python the executable runs on (the asset its
build_info says it was released as), so an update never moves to another Python. The download is
unpacked in-process with lzma into the executable's own directory, verified in-process as
`inspect --verify` does, so no byte of it runs before it passes, checked against the host's glibc,
and renamed over the executable's real path, keeping its mode. A man page and completions found
under the executable's prefix are written again by the new executable and renamed into place
after it. Only released executables update themselves.

| Class | What it represents |
|---|---|
| `UpdateError` | The update cannot be done; the message says why and what to do instead. |
| `NotFound` | An `UpdateError` for a download the server does not have (HTTP 404). |
| `Release` | The latest release: tag, version numbers, the asset's download URL. |

| Function | What it does |
|---|---|
| `curl_hint(*, default, directory)` | The installer command line for this host's Python, or the default Python, into a directory. |
| `running_executable()` | The real path of the running fsbpyr executable; `UpdateError` from a checkout or a wheel. |
| `release_numbers(text)` | `(X, Y, Z)` of `vX.Y.Z` or `X.Y.Z`, else None. |
| `own_release()` | This executable's release numbers; a developer build is refused. |
| `own_asset()` | The asset this executable was released as, from its build_info, else the asset for its Python and the host. |
| `latest_release(asset)` | The latest release from the GitHub API and the URL of `asset.xz`. |
| `unpack(url, target, limit)` | Downloads an xz file and writes it unpacked as it arrives; `UpdateError` when the unpacked file would exceed `limit` bytes. |
| `download_text(url, limit)` | A small file's body as text, at most `limit` bytes (1 MiB by default): `runtime.stubs` reads a companion release's `SHA256SUMS-glibc2.28` with it. `NotFound` for HTTP 404, `UpdateError` for a larger body or another failure. |
| `check_glibc(baseline, host)` | Refuses a new executable whose recorded `glibc` is newer than the host's glibc (`os.confstr("CS_GNU_LIBC_VERSION")`, compared as major and minor numbers); a host that is not glibc, and a build that records none, pass. `dispatch` checks a variant with it too. |
| `update(log, warn)` | The update; False when already up to date. |

Rules it enforces:

- A developer build, a run from a checkout or a wheel, and an executable directory the user cannot
  write are refused with the installer command to use instead.
- The release document's URL is `FSBPYR_RELEASE_API` or the GitHub API's latest-release URL; a
  latest tag that is not `vX.Y.Z`, or a release without the asset, is an `UpdateError`.
- Nothing is replaced when the latest release is not newer, or when the download is cut short,
  fails verification or needs a newer glibc than the host has (the message names both versions);
  every temporary file is removed on failure.
- The man page and completions (`share/man/man1/fsbpyr.1` and the bash, zsh and fish completion
  files) are rewritten only if they exist under the prefix, and only when the executable is in a
  `bin` directory; otherwise a warning says how to write them.
- A download cut short (`http.client.IncompleteRead`, or any other `http.client.HTTPException`)
  is an `UpdateError` like any other failed download, in `latest_release`, `unpack` and
  `download_text`, and so in `dispatch` and `runtime.stubs`, which download through them.
- After an update, a warning names the glibc 2.28 stubs placed where `--glibc` looks for them
  (files `fsbpyr-stub-X.Y-TARGET-glibcA.B`, or with `.xz`, beside the path fsbpyr was run by and
  beside its real path) that the new release refuses: those whose SHA-256 is not in the
  `SHA256SUMS-glibc2.28` of its companion release (`runtime.stubs.refused_after_update`). It says
  to place the new release's stubs next to them, or to remove these and let `--glibc` download
  them. When that file cannot be read (no companion release yet, no network, a download cut
  short), it names every placed stub and says that `--glibc` refuses them if the new release's
  runtime id differs ([Building for glibc 2.28](#building-for-glibc-228)). The files themselves
  are left alone.

- Imports: `inspection`, `targets`, `vcs`, `runtime.stubs` (lazy).
- Imported by: `cli` (lazy), `dispatch`, `runtime.stubs`.

### `manpage`

[`src/fsbpyr/manpage.py`](../src/fsbpyr/manpage.py)

The man page, rendered from the click commands that also give the help text and the shell
completions (D40). Help texts set code in backticks; the page sets it in bold. Besides the
commands it has a CONFIGURATION section made from `config.CONFIG_KEYS`, EXIT STATUS, ENVIRONMENT
and FILES sections whose rows this module holds (`FSBPYR_INSTALL_LINKS`, which makes a built
executable link its commands into a directory, among the variables), and the examples `cli`
passes in. EXIT STATUS lists 0, 1, 2 (usage error), 130 (interrupted) and 70, which a built
executable, not `fsbpyr`, exits with when it refuses to start; that row names a failed integrity
check, a start with privileges changed (set-uid or set-gid to another user or group, or file
capabilities), a start of itself as a Python interpreter unless it was built with
`--interpreter-reexec` or `--no-interpreter-reexec`, a refused cache directory (owned by another
user, or others may write to it), and as many copies running as one user may run at once
(`FSBPYR_MAX_INSTANCES`).

| Function | What it does |
|---|---|
| `render(group, name, examples)` | The roff page: NAME, SYNOPSIS, DESCRIPTION, OPTIONS, every command below the group (hidden ones left out), CONFIGURATION, EXIT STATUS, ENVIRONMENT, FILES, EXAMPLES. |

Rules: backslashes and hyphens are escaped, a line starting with `.` or `'` is protected, and a
help paragraph whose first line is click's `\b` keeps its line breaks. The `ENVIRONMENT` table is
kept in step with the source by a test (as its comment says).

- Imports: `config`, `fsbpyr` (the package).
- Imported by: `cli` (lazy).

## Shared helpers

Small modules that many others use: the package itself, the platform table, the differences
between the Python versions fsbpyr runs on, and two text helpers for messages.

### `fsbpyr`

[`src/fsbpyr/__init__.py`](../src/fsbpyr/__init__.py)

The package. It holds only `__version__` (`"@VERSION@"`), the release number: `assemble` writes it
into build_info as `fsbpyr` and the man page puts it in its header. The version with the commit,
which `fsbpyr --version` prints, comes from `vcs.own_version`.

- Classes and functions: none.
- Imports: nothing from fsbpyr.
- Imported by: `assemble`, `manpage`, `runtime.registry`.

### `targets`

[`src/fsbpyr/targets.py`](../src/fsbpyr/targets.py)

The platforms fsbpyr knows and what differs between them. Each fsbpyr builds apps for the
platform it runs on, so the host's target is detected from `sys.platform`, the machine and the C
library. `TARGETS` lists five: `x86_64-linux-gnu`, `aarch64-linux-gnu`, `x86_64-linux-musl`,
`aarch64-linux-musl` and `aarch64-macos`. Knowing a target is not supporting it: only the glibc
ones have pins in `runtime.pins`. See [Compatibility](Dev-Compatibility.md).

| Class | What it represents |
|---|---|
| `UnsupportedHost` | The host is none of the targets. |
| `Target` | One platform: release-asset name, python-build-standalone triple and flavour, architecture name, ELF machine, dynamic loader, C library, xz filter for native files, multiarch directory, uv's `--python-platform` for the default glibc baseline. `platform_for(manylinux)` gives uv's platform for a baseline's manylinux tag (`x86_64-manylinux_2_34` for `2.34`, `x86_64-manylinux_2_28` for `2.28`); a musl or macOS target keeps its own. |

| Function | What it does |
|---|---|
| `target_named(name)` | The target by asset name or PBS triple; `ValueError` if unknown. |
| `host_libc()` | `"glibc"` when `confstr` reports a glibc version, `"musl"` when `/lib/ld-musl-*.so.1` exists, else None. |
| `detect(system, machine, libc)` | The target of a host (`arm64` counts as `aarch64`); `UnsupportedHost` otherwise. |
| `host_target()` | `detect` for this process. |

- Imports: nothing from fsbpyr.
- Imported by: `build`, `dispatch`, `elf`, `natives`, `selfupdate`, `payload.spec`,
  `runtime.collect`, `runtime.registry`, `runtime.stubs`, `runtime.sysroot`.

### `compat`

[`src/fsbpyr/compat.py`](../src/fsbpyr/compat.py)

What differs between the Python versions fsbpyr itself runs on (3.12 to 3.15). It exports `zstd`
and `tarfile`: the standard library's `compression.zstd` and `tarfile` on 3.14 and later, and the
`backports.zstd` package's (the same API, and a `tarfile` with `r:zst` and `w:zst`) before, which
fsbpyr depends on only below 3.14.

| Function | What it does |
|---|---|
| `process_cpu_count()` | `os.process_cpu_count()` on 3.13 and later; before, the size of the process's CPU affinity set. |

- Imports: nothing from fsbpyr.
- Imported by: `assemble`, `payload.format`, `payload.reader`, `payload.spec`, `payload.spill`,
  `payload.writer`, `runtime.pbs`.

### `text`

[`src/fsbpyr/text.py`](../src/fsbpyr/text.py)

Small text helpers for messages: a count with its noun in the right number, and the end of a
long output cut at a line, which is how an error shows what a failed program printed.

| Function | What it does |
|---|---|
| `plural(count, noun)` | `count` and `noun`, the noun with an `s` unless the count is one: `1 distribution`, `2 distributions`. |
| `tail_lines(text, limit)` | The last `limit` characters of `text` (trailing newlines dropped), from the first line that starts in them, after a line `(N earlier lines left out)`; text no longer than `limit` comes back whole. |

Rules: when no newline follows the cut (the last line alone is longer than `limit`), the text is
cut where it is, with no note. `build` (the smoke test and the smoke run) and `staging` (uv's
output) keep 3000 characters; the runtime modules use it for their own error output (see
[runtime modules](Dev-Runtime-Modules.md)). `build`, `cli`, `inspection` and `tools` count with
`plural`.

- Imports: nothing from fsbpyr.
- Imported by: `build`, `cli` (lazy), `inspection`, `staging`, `tools`, `runtime.pbs`,
  `runtime.stdlib`, `runtime.stub`.
