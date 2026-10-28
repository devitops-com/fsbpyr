# Compatibility

What differs between an app built by fsbpyr and the same project in a virtual environment,
what the machine that runs it must provide, which distributions, libraries and real
applications it is tested on, and what to do when it fails, with links to the code behind each
behaviour. It carries over
[docs/compatibility.md](../docs/compatibility.md)
as published with this wiki, checked against the code; the supported targets are in
[What the target must provide](#what-the-target-must-provide) below and on
[User-Running](User-Running.md#what-the-target-needs). For a user's view of the same failures see
[User-Troubleshooting](User-Troubleshooting.md).

Statements marked *unverified* come from reading code or documentation, not from a run.

## How an app differs from a venv

Each row gives what you see, why, and what to do. The cause column names the code that makes
the difference. "An app of scripts" is one built from a project without a build system, a
directory of scripts or a single script ([User-Scripts](User-Scripts.md):
[Projects without a build system](User-Scripts.md#projects-without-a-build-system),
[A directory of scripts](User-Scripts.md#a-directory-of-scripts),
[A single script](User-Scripts.md#a-single-script)).

| Symptom | Cause | Remedy |
|---|---|---|
| `NotADirectoryError: [Errno 20] Not a directory: '<exe>/<pkg>/data.json'`, or an existence check on such a path returns `False` | A package reads files next to its `__file__`. Its `__file__` is below the executable, which is one file, not a directory. An uncaught `OSError` ends with the note `fsbpyr: <path> is inside the executable; <package> reads files next to its __file__: rebuild with --fs-package <package>`, which `_inside_executable_note` and `_note_for` in [`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py) add from the boot code's `sys.excepthook`. The note is absent when the app catches the error or replaces `sys.excepthook` (typer does) | Build with `--fs-package <package>` (or `fs-packages` in `[tool.fsbpyr]`); `fsbpyr build --hints` lists likely packages. See [fs-packages](#fs-packages) below |
| A compiled package (mypyc's, as black's `blib2to3`) fails on import with `FileNotFoundError: ... <cache>/runtime/<generation>/site/<pkg>/Grammar.txt` | Its compiled modules are extension modules, extracted to the native cache's `site` directory (`native_cache` in [`_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py)), so their `__file__` is there. Of a package that is not an fs-package only the native files are extracted, so a file it reads next to its `__file__` is missing there. An uncaught `FileNotFoundError` below it ends with the note `fsbpyr: <path> is missing from the native cache, which holds the compiled files of a package only; <package> reads files next to its __file__: rebuild with --fs-package <package>`, naming the outermost regular package that holds the path (`_site_note` in `_fsbpyr_boot.py`). The note is absent when the app catches the error or replaces `sys.excepthook`. `PayloadExtensionLoader.create_module` words only the extraction's own `OSError` (`cannot extract native code for <module> into <cache>: ...`); an error the module raises while it is imported reaches the app as it is | Build with `--fs-package <package>` (or `fs-packages` in `[tool.fsbpyr]`): the `black` corpus app below has `fs-packages = ["blib2to3"]` |
| In an app of scripts, a script's `__file__` is `<directory of the executable>/<path>.py`, a path that exists only if the host has a file there; `open(__file__)` of the script's own source fails, and so does `importlib.util.spec_from_file_location` on it, or a plugin scan of its directory (`os.listdir`) for the project's own modules | A module of the scripts root (`_fsbpyr_scripts/`) says it lives next to the executable (`_shown` in `_fsbpyr_boot.py`), so the files a script reads next to `__file__` are the host's, deployed beside the executable; the executable holds the project's Python files only. The code is still the payload's (see [Scripts and the files beside the executable](#scripts-and-the-files-beside-the-executable)). The build warns for each read next to `__file__` it finds in the scripts, and a missing file ends with the note `fsbpyr: <dir>/x.conf is missing; the files a script reads next to its __file__ are read next to the executable, from <dir>/: deploy it there` (`_deploy_note`; absent when the app catches the error or replaces `sys.excepthook`). `--fs-package _fsbpyr_scripts` fails the build ("not a regular package") | Deploy the files a script reads beside the executable (`install -m 644 config.ini "$DEST"`); read the script's own source from the payload with the loader's `get_source` or `importlib.resources`, not `open(__file__)` (`inspect.getsource` works, but linecache reads a copy beside the executable first and shows it instead); a module imported by its name is `_fsbpyr_scripts.<name>` for `module:function` commands and `__main__` for scripts |
| A top-level module (`mod.py`) reads `os.path.join(os.path.dirname(__file__), "x.conf")`, or a package without `__init__.py` (a namespace directory) reads a file next to its `__file__` | A top-level module's `__file__` is `<exe>/mod.py`, and the path of a file next to it is no module. `--fs-package` takes regular packages only (`mark_fs_packages` in [`classify.py`](../src/fsbpyr/classify.py)): it fails the build for a module or a namespace directory. The uncaught-error note (`_note_for`) says so, and for a namespace directory names the regular package below it that `--fs-package` takes, if there is one | Read the file from a path outside the executable; for a namespace directory add an `__init__.py` to the package that reads, and build with `--fs-package` for it |
| A file or directory of a directory of scripts whose name is no Python name (`check-disk.py`, `10-backup`, an extensionless `check-disk`; `sub-dir/`, `2024-tools/`): its `__file__` is `.../check_disk.py`, and the `__file__` and `sys.path[0]` of a script in such a directory name `.../sub_dir` | The build stores the file or directory under a module name in the same place (`_module_stem` in [`loose.py`](../src/fsbpyr/loose.py)); the path hook maps an entry `<exe>/_fsbpyr_scripts/sub-dir` to it by the same rule (`_module_part` in `_fsbpyr_boot.py`), and a script runs as `__main__`; `sys.argv[0]` is the name the command was started by. A `..` in a `sys.path` entry is resolved inside the executable (`_without_dotdot`); an entry that climbs above it is not served from the payload | Do not compute a name from `__file__`; use `sys.argv[0]`. Deploy the files such a script reads under the stored name (`sub_dir/`) |
| A crontab, unit or agent line runs `python3 /opt/tools/x.py` and fails with a `SyntaxError` about null bytes | `x.py` is now a link to the executable, which is an ELF file, not a Python file | Run `/opt/tools/x` (the command's name, without `.py`), or make `x.py` a link to the executable too: a start called `x.py` runs the command `x` (`named` in [`_fsbpyr_rt/commands.py`](../src/fsbpyr/boot/_fsbpyr_rt/commands.py)). `FSBPYR_INSTALL_LINKS=/opt/tools app` makes both links |
| `FSBPYR_INSTALL_LINKS=DIR` is set and the app does not run: it prints `fsbpyr: linked ...` (or `fsbpyr: present ...`) lines and exits | The variable asks the executable to link its commands into DIR and exit (`install` in [`_fsbpyr_rt/links.py`](../src/fsbpyr/boot/_fsbpyr_rt/links.py)): absolute symlinks to its real path, and `x.py` for each script command `x`; exit 0 if all links are there, 1 if a file, a directory or a link to something else was in the way (kept; the others are still made) or DIR is missing or not writable. It is an environment variable, never an option, so a `sudo <binary> *` rule cannot be used to create links; sudo's `env_reset` (default on) drops it | Unset it; to install links run `FSBPYR_INSTALL_LINKS=/opt/tools/bin /opt/tools/app` |
| `sys.argv[0]` of a start by bare name on `PATH`, by `./app` or through a link is the absolute path of the start (`/usr/local/bin/cmd`); started with `exec -a other`, it is `other` | `sys.argv[0]` is the path `execve` was given (`AT_EXECFN`, which `info` in [`launcher/module.c`](../src/fsbpyr/launcher/module.c) reports as `execfn`), made absolute against the working directory, when its basename is `argv[0]`'s basename (`_execve_argv0` in `_fsbpyr_boot.py`). A venv's script gets the same full path for a start by name or absolute path, and keeps `./app` for a relative start (the app gives `/cwd/app`); `exec -a` renames the process for an app and not for a script, which runs under its interpreter's path. argparse's `prog` and a multi-command start read only the basename | Compare `os.path.basename(sys.argv[0])` where the name matters |
| A hard link to the executable runs the app with `sys.executable`, `__file__` and `sys.path[0]` below the link's own path (for a script, next to it); a symlink gives the target's | `sys.executable` is what `/proc/self/exe` resolves to (`fsb_payload_open` in [`launcher/payload.c`](../src/fsbpyr/launcher/payload.c)): a symlink resolves to the file, a hard link is a name of its own. A rebuild replaces the file, so a hard link keeps the old build while the new path runs the new one. The D44 check applies to a hard-link start as to any other, also in a multi-command executable (a symlink named for a command does not meet it: see below) | Name commands with symlinks, or branch on `sys.argv[0]`; recreate hard links after a rebuild |
| `PYTHONPATH`, `PYTHONHOME`, `PYTHONDONTWRITEBYTECODE` and the other `PYTHON*` variables have no effect, except `PYTHONUNBUFFERED` (set and non-empty: stdout and stderr unbuffered) and `JOURNAL_STREAM` (systemd's `device:inode` of the journal: a stdout or stderr that is that file is line-buffered unless unbuffered already); `sys.flags.utf8_mode` is 1. As process 1 (a container without an init) SIGTERM ends the app with status 143; `python app.py` as process 1 ignores SIGTERM | The launcher configures an isolated interpreter (`PyConfig_InitIsolatedConfig`) with UTF-8 mode on, no `site` import, `safe_path` and no bytecode writing, and honours `PYTHONUNBUFFERED` alone (`main` in [`launcher/main.c`](../src/fsbpyr/launcher/main.c)); the journal and process 1 are the boot code's [service policies](#services-cron-and-containers) | Use `FSBPYR_*` variables or the app's own options |
| A multi-command executable (several console scripts, [Several commands in one executable](User-Building.md#several-commands-in-one-executable)) runs the command its `argv[0]` names, or the one `argv[1]` names | Its entry point is a table of `name=target` lines (`parse` and `choose` in `_fsbpyr_rt/commands.py`). Started under another command's name, a child like `subprocess.run(["acme-db", "-m", "word"])` runs that command with those arguments: a start named for a command, and not for the executable, is never a helper interpreter (`is_sibling`; D44 stays for `sys.executable -m ...` and for hard links). The dispatcher form `acme acme-db -m word` from a child of the app runs `acme-db` with `-m word` too: the word after the executable's name is a command, not an interpreter option (`child_command` in `_fsbpyr_rt/reexec.py` looks only at that word). So a command that is interpreter-like (a console, a REPL, `ipython`) is reachable through `acme COMMAND` as well as through its link | A `sudo` or other rule that must allow one command names its link, not the executable, which runs every command |
| All the links of a multi-command executable share one instance budget: monitoring plugins and cron fleets built from a directory of scripts exit 70 under load | The budget counts copies by the executable's file (its device and inode, `fsb_hold_slot` in [`launcher/slots.c`](../src/fsbpyr/launcher/slots.c)), whatever name started them | If several commands legitimately run many copies at once, set `FSBPYR_MAX_INSTANCES` to a higher number; keep the binary outside directories an agent runs file by file |
| A tool binary (a directory with no Python file and a requirements file, [A tool from its requirements file](User-Scripts.md#a-tool-from-its-requirements-file)) starts a command and the tool's helper exits 70 (``fsbpyr: refusing to run `...` as Python: the app started itself as a helper interpreter ...``): a worker pool, a plugin run through `sys.executable -m`, `-c` or a `.py` file below the executable. Ansible's modules on the implicit `localhost` run a file of the host, `sys.executable /path/AnsiballZ_x.py`, which is no helper interpreter: the tool's output says `unknown command '/path/AnsiballZ_x.py'` | The tool starts helpers through `sys.executable`, as any app does, and the build cannot know which tools do: it prints the helper-interpreter note only for the libraries `HELPER_INTERPRETERS` in [`build.py`](../src/fsbpyr/build.py) lists (joblib, loky, `jupyter_client`, py-cpuinfo, multiprocess, torch's inductor). Only a `.py` file below the executable, or beside it for an app of scripts, asks for an interpreter (`child_command` in [`_fsbpyr_rt/reexec.py`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py)); any other path is an argument of the app, and for a tool of several commands one that names no command (`choose` in `_fsbpyr_rt/commands.py`) | Build with `--interpreter-reexec` and test the commands you rely on; for Ansible's `localhost`, set `ansible_python_interpreter` to a real interpreter |
| A tool binary lacks a command the tool has in a venv: a dependency's script (`jinja2`'s `jinja`), or a tool of a `pip freeze` file that another listed distribution requires | The commands are the `console_scripts` and `gui_scripts` of the distributions the file names ([`tools.py`](../src/fsbpyr/tools.py)): a meta-package without scripts or code, as `jupyter`, gives those of its requirements; a tool also has the commands of its direct requirements named after it (`ansible` those of `ansible-core`); a library without scripts gives none. In a file that lists every installed distribution (`pip freeze`, `pylock.toml`) only the ones no other listed distribution requires count. The build prints which tools it took and which commands they gave | List the tools alone in the requirements file; to have a dependency's script, name its distribution there and keep the file short (not a freeze). A `sudo` rule names the link (`/opt/tools/ansible`), as for any multi-command executable; interpreter-like commands (`ipython`, `ansible-console`) are reachable through `tools ipython` too |
| `sys.prefix` is the executable's directory; files outside `site-packages` (`share/`, `etc/`, scripts a wheel installs elsewhere) are not in the app | `pin_prefixes` in `launcher/main.c` sets every prefix to the directory of the executable, and the standard library directory to the executable itself; the build packages the modules and package data of the locked distributions. A project's legacy `setup.py` `scripts=` files that start with `#!python` in the wheel are commands ([`legacy.py`](../src/fsbpyr/legacy.py)); the others (shell scripts) are not packaged, and the build warns, naming each (`_legacy_script_warnings` in `classify.py`) | Ship such files next to the app, or read them through the package that owns them (`importlib.resources`); use `[project.scripts]` console scripts or `--entry` instead of shell scripts |
| `ModuleNotFoundError` for `tkinter`, `idlelib`, `ensurepip`, `turtledemo` or `test`; `_dbm` (so `dbm.ndbm`) is missing, and on 3.12 `_crypt` (so `crypt`) | `EXCLUDED_DIRS` in [`runtime/stdlib.py`](../src/fsbpyr/runtime/stdlib.py) leaves these directories out of the standard library, `lib-dynload` among them. `lib-dynload` holds the interpreter build's shared-object modules: `_tkinter`, `_dbm`, on 3.12 `_crypt`, and CPython's own test modules (`_testcapi` and the like); none is bundled. GUI toolkits that ship as wheels (Qt, wx) are not blocked, but no test covers them | Do not import them; `dbm.dumb` is pure Python |
| A resource opened for writing fails: `ValueError: payload resources are read-only; invalid mode ...`; an extracted file has mode 0400 or 0500 | Resources are served from the payload ([`_fsbpyr_rt/resources.py`](../src/fsbpyr/boot/_fsbpyr_rt/resources.py) raises the error), and files extracted to the cache are made readable by their owner, executable where the payload says so, never writable (`fill` in [`launcher/cache.c`](../src/fsbpyr/launcher/cache.c)) | Write to a path of your own, not next to the package's data |
| `sys.frozen` is missing, and `sys._MEIPASS` too | `sys.frozen` is PyInstaller's flag, and code that sees it looks for PyInstaller's layout; the boot code sets neither | Do not test for it; test for the files you need |
| A child the app starts as a Python interpreter (`subprocess.run([sys.executable, "-m", "tool"])`, `-c CODE`, or a `.py` file of the app, as joblib's `n_jobs`, Jupyter kernels and py-cpuinfo do) exits 70, printing ``fsbpyr: refusing to run `...` as Python: the app started itself as a helper interpreter ...``; joblib then fails with `TerminatedWorkerError` | `sys.executable` is the app, not an interpreter, and running the app in such a child would start it again and again. `run_child` in [`_fsbpyr_rt/reexec.py`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py) refuses it when the build chose neither way. multiprocessing children are recognized ([`_fsbpyr_rt/mp.py`](../src/fsbpyr/boot/_fsbpyr_rt/mp.py)). The exception is a script that is one of the app's own commands (`subprocess.run([sys.executable, <sibling's __file__>])` in a directory of scripts): it runs that command, as `__main__` with its directory first on `sys.path` (`_own_command`), and so does a path that is a command's link (the restart idiom, [`_fsbpyr_rt/restart.py`](../src/fsbpyr/boot/_fsbpyr_rt/restart.py)) | Build with `--interpreter-reexec`; then such a child of the app itself runs as CPython would. An app whose own options are named `-m` or `-c` and which starts itself with them: `--no-interpreter-reexec` |
| An app exits 70 printing `fsbpyr: refusing to start: <N> copies of this app are running already, as many as this user may run at once` | N copies of the executable run already for this user, counted in `/dev/shm` (a container with a `/dev/shm` of its own counts apart); the limit is 4 per CPU and 16, at least 64 and at most 1024 (`fsb_instance_limit` in [`launcher/slots.c`](../src/fsbpyr/launcher/slots.c)). It stops an app that starts itself again and again, however it starts it | If the app keeps starting itself, find the code that starts `sys.executable` (`ps -f`); if it needs more copies at once, set `FSBPYR_MAX_INSTANCES` to a higher number (at most 4096; 0 turns the limit off) |
| `ModuleNotFoundError: No module named 'pkg_resources'` | setuptools 84.0.0 ships no `pkg_resources`. With an older setuptools pin it finds no distributions (*unverified*); the build warns `setuptools: pkg_resources finds no distributions in executables; use importlib.metadata` when a setuptools with `pkg_resources` is packaged ([`classify.py`](../src/fsbpyr/classify.py)) | Use `importlib.metadata` and `importlib.resources` |

### fs-packages

A package listed in `fs-packages` (`--fs-package`) has every file below it extracted to the
cache when it is first imported, so that code reading files next to its `__file__` finds real
files. The pieces:

- `ConfigKey("fs-packages", ...)` in
  [`config.py`](../src/fsbpyr/config.py) reads
  the setting; `mark_fs_packages` in
  [`classify.py`](../src/fsbpyr/classify.py)
  flags the package's module `FS_REQUIRED_ROOT` and every file below it that is not native
  code `FS_REQUIRED` (native files are extracted in any case). A configured name that is not a
  regular package of the app fails the build (`fs-packages: 'x' is not a package of the
  application`, or `... not a regular package in the application` for a top-level module, a
  namespace directory or the scripts root). `BUILTIN_FS_PACKAGES` in the same file (babel,
  botocore, certifi, litellm, matplotlib and torch) are treated the same whatever the
  configuration says, when the app has them.
- At run time the boot code
  ([`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py))
  extracts such a package on its first import through `materialize_package` in
  [`_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py):
  the package's files, the libraries its extensions need from outside it (torchvision's
  `torchvision.libs`, torch's `libc10.so`), and every other shared library its distribution
  ships (openmm's plugins in `OpenMM.libs/lib/plugins`). Its modules' code still comes from the
  payload; `path_hook` claims the extracted directory as well as the payload's.
- `fsbpyr build --hints` runs `file_relative_reads` and `loaded_libraries` of
  [`scan.py`](../src/fsbpyr/scan.py) on each
  module as it is compiled
  ([`compiler.py`](../src/fsbpyr/compiler.py)).
  Reads relative to `__file__` become one warning, `these packages read files next to __file__
  (a heuristic); add one to fs-packages only if it fails at run time: ...` (`add_hints` in
  `classify.py`). Literal library names given to `CDLL`, cffi's `dlopen` or `find_library` are
  recorded in the build information as libraries the code may open by name
  (`system.may_load`, from `system_info` in `system.py`): not a need, and no warning. The
  modules of an app of scripts are scanned for reads next to `__file__` in every build, with or
  without `--hints`; those reads are warned about one by one, `file:line`, since the file is
  deployed beside the executable rather than extracted (`_hint_script_reads` in `classify.py`).
- `ctypes.CDLL` of a bare file name the system cannot load finds the app's bundled library of
  that name: `bundled_library` in
  [`_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py)
  (PyTables' `libblosc2.so`, see the `hdf5` app below).

### Scripts and the files beside the executable

In an app of scripts the project's Python files are stored below the payload root
`_fsbpyr_scripts/` ([`loose.py`](../src/fsbpyr/loose.py)),
and the boot code makes them behave as the same scripts run by path:

- **`__file__` is beside the executable.** `_shown` in
  [`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py)
  gives a module of the scripts root the origin `<directory of the executable>/<path below the
  root>` (the resolved executable's directory; a hard link's own). `_script_directory` puts the
  script's directory, that same place, first on `sys.path`, so its siblings import. Code
  objects keep the payload's file name, so tracebacks show the payload's source lines.
- **The code is the payload's.** `path_hook` hands the executable's directory, and each of its
  subdirectories that is a directory of the scripts root, to `_overlay_finder`, which serves
  them from the payload: a stale `.py` copy beside the executable is never imported, and
  `sys.path.append(os.path.dirname(__file__) + "/pylib")` serves the payload's `pylib`. The
  host's other files there are not looked at.
- **What a script reads there is the host's.** The build warns for each read next to `__file__`
  it finds in the scripts; an uncaught `FileNotFoundError` below the executable's directory
  ends with the deploy note (`_deploy_note`).
- **argparse.** On 3.14.9 and later, a script started by the path its `__file__` names (a
  `name.py` link beside the executable) would get the prog `<exe> -m <module>`; the `argparse`
  fixup in
  [`_fsbpyr_rt/fixups.py`](../src/fsbpyr/boot/_fsbpyr_rt/fixups.py)
  makes it the file's name. The boot code installs it on 3.14 and later, in apps with a script
  command only.
- **Siblings as helpers.** `[sys.executable, <a sibling's __file__>, args]` runs that command
  (`_own_command` in `_fsbpyr_rt/reexec.py`), whether the path names the script beside the
  executable or below it, once the parent check of
  [Dev-Security](Dev-Security.md#multiprocessing-children-and-helper-interpreters) has passed.

A project with a build system is not affected: its packages' `__file__` is below the
executable, and `--fs-package` applies.

### Commands, links and `sys.argv[0]`

An executable with several commands stores its entry point as `name=target` lines.
[`_fsbpyr_rt/commands.py`](../src/fsbpyr/boot/_fsbpyr_rt/commands.py)
picks the command `basename(argv[0])` names, then the same without `.py`, then, for
`<exe> COMMAND args`, the one `argv[1]` names, which is then removed from `sys.argv`; with none
it lists the commands on stderr and exits 2. A one-command build loads none of this: `run` in
`_fsbpyr_boot.py` only tests the entry point for a newline. Before the choice,
`_execve_argv0` sets `sys.argv[0]` to the path `execve` was given and `_restart` undoes the
restart idiom (`[sys.executable, <flags>, sys.argv[0], *args]`,
[`_fsbpyr_rt/restart.py`](../src/fsbpyr/boot/_fsbpyr_rt/restart.py)),
so that gunicorn's and Django's restarts get the argv of their parent. `FSBPYR_INSTALL_LINKS`
is handled first of all, before any of these.

### Services, cron and containers

- **Output.** A non-empty `PYTHONUNBUFFERED` makes stdout and stderr unbuffered
  (`config.buffered_stdio` in `main`,
  [`launcher/main.c`](../src/fsbpyr/launcher/main.c)).
  Under systemd, `JOURNAL_STREAM` names the journal's `device:inode`; a stdout or stderr that is
  that file is line-buffered, unless unbuffered already, so each line reaches the journal as it
  is printed (`line_buffer_journal` in
  [`_fsbpyr_rt/service.py`](../src/fsbpyr/boot/_fsbpyr_rt/service.py)).
- **Process 1.** In a container without an init, the kernel drops a signal that process 1 has
  no handler for, so `python app.py` ignores `docker stop`'s SIGTERM. A built app installs a
  handler that raises `SystemExit(143)`, before the app's code runs and only while SIGTERM has
  its default disposition (`terminate_on_sigterm`); the app's own handler replaces it, and a
  forked child gets the default back, as under `python app.py`. The `-m` and `-c` helper starts
  of an app that is process 1 get the same. Process 1 starts no background collection of the
  cache (`launcher/due.c`).
- **No `HOME`.** When `$HOME` is unset, empty or relative, the cache root is found from the
  passwd entry's home directory, the same in the boot code (`home_directory` in
  [`_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py)),
  the launcher (`home` in
  [`launcher/due.c`](../src/fsbpyr/launcher/due.c))
  and fsbpyr's own cache (`_home` in
  [`runtime/registry.py`](../src/fsbpyr/runtime/registry.py)),
  so a service without `HOME` still finds it.
- **Cron fleets and monitoring plugins.** Every link of an executable shares one instance
  budget, and every start checks the whole file, whose cost grows with its size (see
  [Dev-Performance](Dev-Performance.md#the-integrity-check)).
  [Monitoring plugins and cron fleets](User-Scripts.md#monitoring-plugins-and-cron-fleets) has the
  deployment advice.

`JOURNAL_STREAM` and process 1 cost a start an environment lookup and a `getpid`;
`_fsbpyr_rt.service` is imported only when one applies (`_service_policies` in
`_fsbpyr_boot.py`).

## What the target must provide

The executable carries CPython, the standard library and the locked distributions. It needs
these from the machine:

- **glibc 2.34 or newer** on Linux x86-64 or aarch64; the distributions tested are those of
  [Tested on](#tested-on) (from glibc 2.34, Rocky Linux 9 and Amazon Linux 2023, to 2.44, Arch
  Linux). A system with an older glibc, such as SLES 15 SP4 and SP5 (2.31), cannot run a
  default build. `fsbpyr inspect` shows the glibc the executable actually needs:
  the launcher's requirement, raised by the bundled native code (`glibc_report` in
  [`build.py`](../src/fsbpyr/build.py), from
  `glibc_requirement` in
  [`elf.py`](../src/fsbpyr/elf.py)); the
  build warns when a native file needs more than the 2.34 baseline. The
  launcher is linked against the glibc 2.36 of a Debian 12 sysroot
  ([`runtime/sysroot.py`](../src/fsbpyr/runtime/sysroot.py))
  and kept within 2.34 by two link-time wrappers in
  [`launcher/glibc.c`](../src/fsbpyr/launcher/glibc.c):
  `hypot`, which glibc 2.35 gave a new symbol version, and `_dl_find_object` (glibc 2.35),
  which the libgcc unwinder that 3.15's libpython links in calls. Only 2.34 and newer is
  tested: the distributions below, releases their vendors support. This is what a default
  build needs; for EL8 see the next item.
- **glibc 2.28 or newer for an app built with `--glibc 2.28`** (or `glibc = "2.28"` in
  `[tool.fsbpyr]`, also in a script's PEP 723 block), for EL8 hosts (RHEL 8, Rocky Linux 8,
  AlmaLinux 8; glibc 2.28). `--glibc` takes `2.34` (the default) or `2.28`
  (`BASELINES` in
  [`runtime/pins.py`](../src/fsbpyr/runtime/pins.py)).
  Such a build differs from a default one in these points:
  - **The stub.** The launcher is linked against Rocky Linux 8.10's glibc 2.28, from pinned RPMs
    (`EL8_SYSROOTS` in `runtime/pins.py`, extracted by `extract_rpm` in
    [`runtime/sysroot.py`](../src/fsbpyr/runtime/sysroot.py)
    through `payload_tar` in
    [`runtime/rpm.py`](../src/fsbpyr/runtime/rpm.py)).
    Those headers have no `_dl_find_object`, so `launcher/glibc.c` compiles that wrapper out
    (the EL8 libgcc finds frames with `dl_iterate_phdr`). A released
    fsbpyr does not carry that stub: `select_runtime` in
    [`runtime/registry.py`](../src/fsbpyr/runtime/registry.py)
    pairs its own standard library with the stub of its release for glibc 2.28, taken from a
    file placed beside the fsbpyr executable, from its cache, or from the release's companion
    release `el8-vX.Y.Z`
    ([`runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py);
    how each is checked is on [Dev-Security](Dev-Security.md#the-glibc-228-stub)). A developer
    build uses a runtime of its cache, made by `fsbpyr runtime build --glibc 2.28`.
  - **The wheels.** uv installs wheels for `manylinux_2_28` (`platform_for` in
    [`targets.py`](../src/fsbpyr/targets.py)
    gives `--python-platform` that tag). A dependency built from its sdist whose native files
    need a newer glibc than 2.28 is built again against the Rocky Linux 8.10 sysroot
    (`built_here_above` and `rebuild_on_baseline` in
    [`build.py`](../src/fsbpyr/build.py)); as
    for the default sysroot, C only, since the sysroot has no libstdc++.
  - **What the app records.** Its glibc requirement is never below 2.28, and a native file
    that needs more than 2.28 is warned about (`glibc_report` in `build.py`): building the
    pure-Python `hello` prints `glibc >= 2.28` and `target needs: glibc >= 2.28`. The build
    information records `glibc_baseline` `2.28` (`baseline_record`), which `fsbpyr inspect`
    shows as `baseline   glibc 2.28`; `inspect --json` gives `glibc_baseline` for every app,
    `2.34` for one that records none
    ([`inspection.py`](../src/fsbpyr/inspection.py)).

  Such an app is tested on the two EL8 images of [Tested on](#tested-on) and on the twelve
  distributions there. No other system with a glibc from 2.28 to 2.33 is tested, with or without
  `--glibc 2.28`; a system with an older glibc than 2.28 is not supported.
- **The libraries native code links and the build did not bundle.** At build time every
  `DT_NEEDED` entry of every extension module, and of every library it reaches through them,
  must resolve to another payload file or to a library manylinux wheels may take from the
  system (`_SYSTEM_LIBRARIES` and `closures` in
  [`natives.py`](../src/fsbpyr/natives.py)),
  or to one that `allow-system-libs` names; the build fails otherwise. A shared object no
  extension module reaches (one ctypes or cffi opens) only gets a warning
  (`_check_unreached`). `fsbpyr build` prints
  `fsbpyr: target needs: glibc >= X.Y; libstdc++.so.6 (GLIBCXX_..., CXXABI_...), libgcc_s.so.1`
  (`target_needs` in [`system.py`](../src/fsbpyr/system.py)),
  and `fsbpyr inspect` shows it again. Install those libraries on the target. A build warns
  when a native file needs a `GLIBCXX`, `CXXABI`, `GCC` or `ZLIB` version newer than the
  manylinux systems of its glibc baseline provide: manylinux_2_34 for a default build,
  manylinux_2_28 with `--glibc 2.28` (`_over_floor` in `system.py`, against the entry of
  `MANYLINUX_LIMITS` in `natives.py` for the baseline, taken from auditwheel's policy of each).
  The warning names the systems (`BASELINE_SYSTEMS`: RHEL 9 and Ubuntu 22.04 for 2.34, RHEL 8
  for 2.28):

  | Namespace | manylinux_2_34, x86-64 | manylinux_2_34, aarch64 | manylinux_2_28, x86-64 and aarch64 |
  |---|---|---|---|
  | `GLIBCXX` (libstdc++) | 3.4.29 | 3.4.29 | 3.4.24 |
  | `CXXABI` (libstdc++) | 1.3.13 | 1.3.13 | 1.3.11 |
  | `GCC` (libgcc_s) | 7.0.0 | 11.0 | 7.0.0 |
  | `ZLIB` (libz) | 1.2.9 | 1.2.9 | 1.2.9 |

  A file that needs `GLIBC_PRIVATE` is warned about too: glibc's internals have no
  compatibility promise.
- **To check a target before deploying,** run the app there with `FSBPYR_SELFTEST=natives`
  ([`_fsbpyr_rt/selftest.py`](../src/fsbpyr/boot/_fsbpyr_rt/selftest.py)).
  It extracts every native extension and its closure into the cache and runs, on each, the
  dynamic loader the executable's own `PT_INTERP` names (that of the target the app was built
  for) with `LD_TRACE_LOADED_OBJECTS=1`, which resolves libraries and symbol versions without
  running the extension. It prints `fsbpyr selftest: missing libstdc++.so.6 (needed by
  <extension>, ...)` or ``fsbpyr selftest: missing version `GLIBCXX_3.4.32' of libstdc++.so.6
  (needed by ...)`` on stderr for each, and exits 1 if any (0 and
  `fsbpyr selftest: all natives load (N extensions)` otherwise). It needs the cache
  directory as a run does: a refused one ends it as it ends a run, with the one line
  `fsbpyr: cannot start <executable>: refusing cache directory <path>: ...` and exit 70 (it
  extracts through `NativeCache.extract`, see [Troubleshooting](#troubleshooting)). It checks
  only what extensions load at startup: libraries they
  open later by name (`dlopen`) are not seen. glibc targets only.
- **A mounted `/proc`.** The launcher reads its own executable through `/proc/self/exe`
  (`fsb_payload_open` in
  [`launcher/payload.c`](../src/fsbpyr/launcher/payload.c):
  `cannot resolve own executable path (an executable needs /proc)` otherwise), and
  multiprocessing children are authenticated through `/proc/<ppid>/exe` and
  `/proc/<ppid>/status` (`_parent_runs_this_executable` in
  [`_fsbpyr_rt/mp.py`](../src/fsbpyr/boot/_fsbpyr_rt/mp.py)).
- **A cache on a file system that allows execution.** Native code is extracted there and
  loaded from it. A `noexec` mount makes the import fail; `PayloadExtensionLoader.create_module`
  in [`_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py)
  then checks the mount (`ST_NOEXEC`) and raises `_fsbpyr.CacheError` (an `ImportError`), which
  names the cache and suggests `FSBPYR_CACHE_DIR`. The
  directory must be yours, every directory on the way to it yours or root's, and none of them
  writable by other users (a sticky one such as `/tmp` aside); otherwise the app exits 70 at its
  first extraction, as does `FSBPYR_SELFTEST=natives` (see
  [Troubleshooting](#troubleshooting) and [Dev-Security](Dev-Security.md#the-cache)).
- **Time zone data, terminfo, the CA store and `openssl.cnf`** come from the system, as for
  any Python. An app that depends on the `tzdata` package or `certifi` carries its own. The
  runtime's OpenSSL looks in `/etc/ssl/cert.pem` and `/etc/ssl/certs` (python-build-standalone
  builds it with `OPENSSLDIR=/etc/ssl`); when `/etc/ssl/cert.pem` does not exist,
  `/etc/ssl/certs` has no hashed links (`*.0`) and no `SSL_CERT_FILE` or `SSL_CERT_DIR` is set,
  the `ssl` fixup in
  [`_fsbpyr_rt/fixups.py`](../src/fsbpyr/boot/_fsbpyr_rt/fixups.py)
  also loads the first readable of `/etc/ssl/certs/ca-certificates.crt`,
  `/etc/ssl/certs/ca-bundle.crt`, `/etc/pki/tls/certs/ca-bundle.crt` and
  `/etc/ssl/ca-bundle.pem` in `set_default_verify_paths`. Which system paths each library
  reads has not been tested.
- **The kernel:** no minimum is established. Containers share the host's kernel, so the
  distribution matrix below tests only the kernel of the machine that ran it. Where a feature
  is missing the launcher goes without: it falls back when `O_TMPFILE` is unavailable
  (`place_unnamed` in
  [`launcher/cache.c`](../src/fsbpyr/launcher/cache.c)
  then leaves the file to `place_renamed`), and `close_range` is compiled out when the headers
  lack it (`detach_descriptors` in
  [`launcher/due.c`](../src/fsbpyr/launcher/due.c)
  then closes what `/proc/self/fd` lists, else every descriptor up to the limit). The instance
  limit needs OFD locks (Linux 3.15) and `/dev/shm` or `$XDG_RUNTIME_DIR`; without them a
  start runs unguarded (`fsb_hold_slot` in
  [`launcher/slots.c`](../src/fsbpyr/launcher/slots.c)).
  Page sizes: 4 KiB and, on aarch64, 64 KiB are tested; kernels with 16 KiB pages are not
  supported (see [Tested on](#tested-on)).

## Tested on

The portability test
([`tests/e2e/test_portability.py`](../tests/e2e/test_portability.py))
builds the `hello`, `heavy` and `compat` fixtures once, copies them into each of these
distributions with podman and runs every check there. The images are `IMAGES` in
[`tests/support/images.py`](../tests/support/images.py),
12 of them.
[CI](../.github/workflows) runs it for each
Python (3.12 to 3.15) and each target (x86-64 and aarch64, on a machine of its architecture);
on aarch64 every image but `archlinux:latest` (no arm64 variant, `NO_ARM64`) runs. On 3.15
`heavy` is not built (onnxruntime has no wheel) and the `compat` checks of packages without a
usable wheel are not run (`HEAVY_APPS` in `test_portability.py`, `missing` in
[`tests/support/wheels.py`](../tests/support/wheels.py),
from [`tests/support/no-wheels.txt`](../tests/support/no-wheels.txt)).

| Distribution | glibc |
|---|---|
| `docker.io/library/rockylinux:9` | 2.34 |
| `docker.io/library/amazonlinux:2023` | 2.34 |
| `docker.io/library/ubuntu:22.04` | 2.35 |
| `docker.io/library/debian:12` | 2.36 |
| `registry.suse.com/bci/bci-base:15.7` | 2.38 (SLES 15 SP7) |
| `docker.io/library/ubuntu:24.04` | 2.39 |
| `docker.io/library/almalinux:10` | 2.39 |
| `docker.io/opensuse/leap:16.0` | 2.40 |
| `docker.io/library/debian:13` | 2.41 |
| `docker.io/library/ubuntu:26.04` | 2.43 |
| `docker.io/library/fedora:latest` | 2.43 |
| `docker.io/library/archlinux:latest` | 2.44 |

Apps built with `--glibc 2.28` run on the EL8 images, `EL8_IMAGES` in the same file (both have
an arm64 variant):

| Distribution | glibc |
|---|---|
| `docker.io/library/rockylinux:8` | 2.28 |
| `docker.io/library/almalinux:8` | 2.28 |

In a default run, `test_el8_apps_run_on_el8` in `test_portability.py` runs two apps built with
`--glibc 2.28` on these two images: `hello` and an app with a manylinux_2_28 `cryptography`
wheel (`native_project` in
[`tests/e2e/test_el8.py`](../tests/e2e/test_el8.py)),
built by the test with the EL8 runtime or taken from `FSBPYR_PORTABILITY_APPS`. In the EL8 test
mode (`FSBPYR_TEST_GLIBC=2.28`,
[`tests/support/baseline.py`](../tests/support/baseline.py))
every build of the tests is a `--glibc 2.28` one, and the whole matrix, `hello`, `heavy` and
`compat`, runs on the twelve images and these two (`images_for`): `make test-el8-portability`.
`make test-el8-stub` runs the tests that depend on the stub in that mode, then
`tests/e2e/test_el8.py`, which compares EL8 builds with default ones; `make test-el8` runs both
targets ([Makefile](../Makefile)).
[`tests/e2e/test_el8_use_cases.py`](../tests/e2e/test_el8_use_cases.py)
builds a directory of scripts, a single script and a project of several commands for EL8 and
runs them on the host. The EL8 workflow,
[`.github/workflows/el8.yml`](../.github/workflows/el8.yml),
which publishes the stubs, runs the EL8 test mode.

The container matrix runs on kernels with 4 KiB pages. aarch64 kernels with 64 KiB pages
(EL8's) are smoke-tested in a QEMU system VM, not on hardware: `make test-vm` runs
[`tools/vm-smoke.py`](../tools/vm-smoke.py),
which boots the kernel pinned in
[`tools/vm-pins.toml`](../tools/vm-pins.toml)
(Rocky Linux 8.10's `kernel-core` 4.18.0, whose config must have `CONFIG_ARM64_64K_PAGES=y`;
the guest must report 65536-byte pages) under `qemu-system-aarch64` (TCG, no KVM) with the apps
in an initramfs, on a Debian trixie root file system or, with `--userland el8`, Rocky Linux
8.10's container base image (glibc 2.28). It runs a few built apps there and checks their
output and exit codes on the host. Kernels with 16 KiB pages (Raspberry Pi 5's, Asahi
Linux's) are not tested and not supported: an owner decision of 2026-10-27, as too few systems
use them (`tools/vm-pins.toml`, `make test-vm` in the Makefile).

The `compat` app runs one small check per library or behaviour in each build; the checks are
`MATRIX` in
[`tests/e2e/test_compat.py`](../tests/e2e/test_compat.py),
18 of them:

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

The same file compares other behaviours of the `mp` fixture with its venv, among them
`sys.argv[0]` for a start by bare name, relative path, absolute path, symlink and `exec -a`,
the restart idiom, and a script started through `sys.executable` finding its sibling modules.

The same tables in docs/compatibility.md (distributions, checks and corpus) are compared with
the code by the tests under "docs/compatibility.md" in
[`tests/unit/test_cli_docs.py`](../tests/unit/test_cli_docs.py)
(`IMAGES` with `EL8_IMAGES`, `MATRIX` and the corpus directories), which also check that every
`fsbpyr: ` message that document quotes is one the sources can compose; the tables here were
checked against the same sources at v0.28.0. The lists say nothing about packages that are not in
them.

## Tested on real applications

Besides the fixtures, a corpus of small applications is built and run: by CI for each Python
(3.12 to 3.15) and each target, and locally by `make test-corpus` (`PYTHON=3.X` for another
Python). Each app is a project in
[`tests/corpus/<name>`](../tests/corpus) with
its own `uv.lock` and a `commands.toml`.
[`tests/e2e/test_corpus.py`](../tests/e2e/test_corpus.py)
runs each command in the app's venv and in the built executable, each run with an empty home
directory of its own (`HOME` and the XDG base directories), and compares exit code, stdout and
stderr; one output must also contain the app's `expect` string, so that two runs that fail
alike do not pass. How to write an app is in
[`tests/corpus/README.md`](../tests/corpus/README.md);
the helpers are in
[`tests/support/corpus.py`](../tests/support/corpus.py)
(`FSBPYR_CORPUS_APPS`, read by `selected`, runs a comma-separated subset).

At v0.28.0 the corpus has 65 apps (counted from the directories), 35 of which need no
`[tool.fsbpyr]` setting (counted from their `pyproject.toml` files). An app is not run on a
Python where a package its lock installs has no wheel for manylinux_2_34 there, unless the app
builds that package from its sdist (`gaps` and `runnable` in `tests/support/corpus.py`, from
the lock);
[`tests/support/no-wheels.txt`](../tests/support/no-wheels.txt)
lists the packages per Python. By that rule 23 apps are not run on 3.15 and one,
`tensorflow`, on 3.14, on both targets; every app runs on 3.12 and 3.13. An app that
fails is marked in its `commands.toml` as a known failure (`known-failure`, read by
`known_failure` in `tests/support/corpus.py`): the test expects the failure and goes red once
it is fixed. No app is marked at v0.28.0.

In the EL8 test mode the same rule looks for wheels for manylinux_2_28 instead of
manylinux_2_34 (`BASELINE` in
[`tests/support/locks.py`](../tests/support/locks.py)),
and every app is built with `--glibc 2.28`; the EL8 workflow runs a few apps with native code
that way (`FSBPYR_CORPUS_APPS`).

The `[tool.fsbpyr]` column is what the app needs beyond the defaults; each setting's reason is
a comment in the app's `pyproject.toml`.

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
| `vision` | cv2's loader putting its directory into `sys.path` to import its binary, scikit-image's lazy_loader stubs and data images, imageio-ffmpeg's bundled binary run as a subprocess | `fs-packages = ["cv2", "imageio_ffmpeg", "skimage"]` |
| `matplotlib` | matplotlib and seaborn: fonts and data of mpl-data, Agg rendering | none |
| `jupyter` | nbclient running a notebook in an ipykernel started as `sys.executable -m ipykernel_launcher`, IPython in process, jedi (the standard library's sources come from the payload, [`fixups.py`](../src/fsbpyr/boot/_fsbpyr_rt/fixups.py)); the kernelspec search is cut to ipykernel's own (`XDG_DATA_DIRS`): a built app has no `share/`, and a host's `/usr/share/jupyter/kernels/python3` (Debian's python3-ipykernel) would start the system's Python as the kernel; on aarch64, debugpy's universal wheel carries an x86-64 library, kept as data | `fs-packages = ["debugpy", "IPython", "nbformat", "parso"]`, `interpreter-reexec = true` |
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
| `openmm` | plugins loaded as shared libraries from a directory next to OpenMM's library (`OpenMM.libs/lib/plugins`, outside any package) | `fs-packages = ["openmm"]` |

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

[`tests/fixtures/projects/plugins`](../tests/fixtures/projects/plugins)
is a host and two plugin distributions with a custom entry-point group and a PEP 420 namespace
they share. The apps are run only for the commands in their `commands.toml`. `import
distutils` before `setuptools` resolves to setuptools' copy (the `distutils` check above,
`SetuptoolsDistutilsLoader` in
[`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py));
the `setuptools-vendor` app imports `setuptools` first.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| An app exits 70 printing `fsbpyr: cannot start <executable>: refusing cache directory <path>: owned by another user`, or `... other users may write to it`, in a CI container (also under `FSBPYR_SELFTEST=natives`) | The way from `/` to the cache passes a directory that another user owns or others can write to (`fsb_way_check` in [`launcher/way.c`](../src/fsbpyr/launcher/way.c); the messages come from `synced` in [`launcher/module.c`](../src/fsbpyr/launcher/module.c)). The check runs at the app's first extraction (a native extension, an fs-package, a resource asked for as a path, a bundled library opened by name); `NativeCache.extract` in [`_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py) turns that refusal into a refused start (`refuse`: the one line, then `os._exit(70)`, so neither an `except` of the app nor a thread carries on). Other extraction failures stay errors the app sees, and an app that extracts nothing never meets the check. Mounted home directories (GitHub's `/github/home` in a job container) are the usual case | Put the cache under the user's own tree: set `FSBPYR_CACHE_DIR`, or `XDG_CACHE_HOME` |
| `_fsbpyr.CacheError: ...; the native cache <path> is on a noexec mount, set FSBPYR_CACHE_DIR to another directory` (a subclass of `ImportError`) | The cache is on a file system mounted `noexec` (`PayloadExtensionLoader.create_module` in [`_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py)) | Point `FSBPYR_CACHE_DIR` at a directory on a file system that allows execution |
| ``ImportError: ... version `GLIBC_2.xx' not found``, followed by `; the build requires glibc ..., this system has ...` | A native file needs a newer glibc, libstdc++ or libgcc_s than the target has; `_system_library_hint` in [`_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py) appends the build's `glibc` and the system's `CS_GNU_LIBC_VERSION` | Build on the baseline (manylinux_2_34 wheels, or `fsbpyr build` against the Debian 12 sysroot), or run on a newer system |
| A C++ or Fortran dependency without a manylinux wheel is built from its sdist, and the build warns about the glibc it needs | A dependency built on the build host that needs a newer glibc than the baseline is built again against the Debian 12 sysroot (`built_here_above` and `rebuild_on_baseline` in [`build.py`](../src/fsbpyr/build.py), with the C flags of `build_flags` in [`runtime/sysroot.py`](../src/fsbpyr/runtime/sysroot.py)). The sysroot has no libstdc++, so such a build cannot be redone against Debian 12's glibc and keeps the build host's | Build the dependency as a `manylinux_2_34` wheel (for example with cibuildwheel) and point uv at it |
| A default build does not start on RHEL 8, Rocky Linux 8 or AlmaLinux 8 | Its launcher is built for glibc 2.34 (`fsbpyr inspect` shows `glibc >= 2.34`); EL8 has 2.28 | Build with `--glibc 2.28`, or `glibc = "2.28"` in `[tool.fsbpyr]` |
| `fsbpyr build --glibc 2.28` fails with `release el8-vX.Y.Z (the glibc 2.28 stubs of vX.Y.Z) does not exist or has none for this Python: ...` | The companion release of this fsbpyr's tag is missing, or it has no stub for this Python and target (`_no_stubs` in [`runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py)) | Place the stub, `fsbpyr-stub-<python>-<target>-glibc2.28` or the same with `.xz`, beside the fsbpyr executable (the message names both), or use a release that has the stubs |
| `fsbpyr build --glibc 2.28` fails with `release el8-vX.Y.Z's <asset> could not be fetched: ...`, or `... does not match the SHA-256 the release publishes (...); nothing was kept` | The download failed, or the unpacked stub is not the one `SHA256SUMS-glibc2.28` lists | Without a network, place the stub beside the fsbpyr executable; a mismatch is a damaged or changed download: try again, and check the file against the sums |
| `fsbpyr build --offline` fails with `--offline: the <asset> is not placed or cached, and nothing is downloaded; ...` (`--glibc 2.28`), or `this project builds with Python 3.X, which needs <asset> of release vX.Y.Z; it is not in the cache and --offline fetches nothing: ...` | `--offline` downloads nothing: neither the glibc 2.28 stub (`_offline` in [`runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py)) nor the fsbpyr of the Python the project needs (`variant` in [`dispatch.py`](../src/fsbpyr/dispatch.py)) | Place the stub beside the fsbpyr executable you run (the message names the file and the place), or run the build once without `--offline` to cache what it needs |
| `fsbpyr build --glibc 2.28` fails with `<path> is not the stub this fsbpyr (vX.Y.Z) needs: it is the stub of runtime <id>, and this fsbpyr expects <id>; use the <asset> of release el8-vX.Y.Z`, or `... it carries no runtime id: it is no stub of an fsbpyr release` | The file placed beside the fsbpyr executable is the stub of another Python or target, or of a release with another runtime id (an old release's, left there by `fsbpyr self update`), or no stub (`_checked` in `runtime/stubs.py`) | Place the stub of the companion release of this fsbpyr's tag, or remove the file and let the build download it |
| `fsbpyr build --glibc 2.28` of a developer build fails with `no runtime for Python 3.X and glibc 2.28; run fsbpyr runtime build --python 3.X --glibc 2.28` | A developer build has no release to fetch a stub from, and takes the runtime from its cache (`find_runtime` in [`runtime/registry.py`](../src/fsbpyr/runtime/registry.py)) | Run that command: it links the stub against the Rocky Linux 8.10 sysroot |
| The shell says the executable is "not found" although the file exists | The target has no glibc loader at the path the executable names (musl, such as Alpine) | Run it on a glibc system; musl is not a supported target |
| A cron job or monitoring check that ran `python3 /path/x.py` fails after the switch to a built executable, or exits 70 under load | See the `python3 x.py` and instance-budget rows of [the table above](#how-an-app-differs-from-a-venv) | Run the command's link; raise `FSBPYR_MAX_INSTANCES` if many copies run at once |

## See also

- [Dev-Security](Dev-Security.md): why the cache must be private, the files beside the
  executable, and the other refusals.
- [Dev-Performance](Dev-Performance.md): what extraction and the checks cost.
- [Dev-Testing](Dev-Testing.md): the test suites named here.
- [User-Building](User-Building.md#building-for-el8-glibc-228): building for EL8 from a user's
  side.
- [User-Troubleshooting](User-Troubleshooting.md): the same failures from a user's side.
