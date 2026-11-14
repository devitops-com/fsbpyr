# Boot modules

The package [`src/fsbpyr/boot/`](../src/fsbpyr/boot) is the Python code that runs inside every
built executable: the boot module `_fsbpyr_boot.py` and the helper package `_fsbpyr_rt`.

## How the code gets into an executable

`assemble.boot_modules` ([Build modules](Dev-Build-Modules.md)) compiles the package with the
building interpreter, which must have the runtime's bytecode magic (`assemble.build_temporary`
checks it), under top-level names:

| In the source tree | In the executable | Stored as |
|---|---|---|
| `fsbpyr/boot/_fsbpyr_boot.py` | `_fsbpyr_boot` | a STARTUP module, file name `<frozen _fsbpyr_boot>` |
| `fsbpyr/boot/_fsbpyr_rt/__init__.py` | `_fsbpyr_rt` | a regular package of the payload |
| `fsbpyr/boot/_fsbpyr_rt/<name>.py` | `_fsbpyr_rt.<name>` | regular modules of the payload |

The tool itself imports `fsbpyr.boot._fsbpyr_rt.gc` and `.commands`. Every build regenerates the
boot code (a runtime taken from an executable leaves it out); it is hidden from
`pkgutil.iter_modules` at the top level and from the `FSBPYR_TRACE` report.

`_fsbpyr_boot` imports only built-in and frozen modules at module level (`_imp`,
`_sitebuiltins`, `builtins`, `marshal`, `posix`, `sys`, `_frozen_importlib`,
`_frozen_importlib_external`, and the launcher's `_fsbpyr`), so that a start touches no file and
decompresses nothing it does not need; `_fsbpyr_rt` is imported when needed.

## The handover from the launcher

The [launcher](Dev-Launcher.md#from-execve-to-the-boot-code) configures CPython and runs
`_fsbpyr_boot.run()` through `Py_RunMain`. Its built-in module `_fsbpyr` is the boot code's
bounds-checked access to the payload; the functions the boot code uses (documented at the top of
[`module.c`](../src/fsbpyr/launcher/module.c)):

| Function | Returns |
|---|---|
| `find_module(name)` | `(index, kind, flags, code, source, file, dist, path)` or `None` |
| `find_file(path)` | `(index, kind, flags, mode, blob, dist, closure)` or `None` |
| `file_at(index)` | `(index, kind, flags, mode, blob, dist, closure, path)` |
| `closure(word)` | the FILES indices of a closure |
| `blob_info(blob)` | `(size, integrity digest or None)` |
| `read(blob)` | the blob's content, decompressed and size-checked |
| `stream(blob)` | a `Stream` over the content, a window at a time |
| `list_dir(path)`, `is_dir(path)`, `list_modules(prefix)`, `files_below(path)` | directory views of the payload |
| `find_dist(name)`, `dists()` | distribution records |
| `info()` | the executable's path, the path `execve` was given (`execfn`, from `AT_EXECFN`, or `None`), app name, entry point, `build_info`, `generation_id`, Python version, payload size, module and file counts |
| `stats()` | read counters and the integrity check's time |
| `sync(root, chain, files, checked, resources[, written])` | extracts files into the cache, or checks them there |

`_fsbpyr.CacheError` (an `ImportError`) is raised when the cache cannot be used or the payload's
data for it is corrupt, and its subclass `_fsbpyr.CacheRefused` when the cache's directory is
refused (another user's, or other users may write to it); `_fsbpyr.NONE` is the format's "no
reference" value.

## From `run()` to the app

![Boot startup: _fsbpyr_boot.run() installs the import machinery, makes the command links for FSBPYR_INSTALL_LINKS and exits, or sets sys.argv[0], undoes the restart idiom, parses a command table and applies the service policies; then it serves a multiprocessing or helper-interpreter child, FSBPYR_GC or a self-test, or chooses the command and runs the target; a refused cache directory ends the start with exit 70](images/boot-startup.svg)

`_fsbpyr_boot.run()`:

1. On 3.12, sets `sys._stdlib_dir` to the executable and gives the frozen standard-library
   modules a `__file__` (packages a `__path__`) inside it, as 3.13 does.
2. Puts `path_hook` first on `sys.path_hooks`, sets `sys.path` to `[<exe>]`, clears the path
   importer cache, and puts `PayloadMetaFinder` on `sys.meta_path` just before `PathFinder`.
3. Installs its `sys.excepthook` if the hook is still the default, lets `FrozenImporter` return
   payload sources for frozen modules, teaches `linecache` to read payload files, and defines
   `exit`, `quit` and `help` as `site` would.
4. With `FSBPYR_TRACE` set, registers `_fsbpyr_rt.trace.report` to run at exit.
5. With `FSBPYR_INSTALL_LINKS` set, makes the command links ([`links`](#links)) and exits; the
   launcher's collection child (`FSBPYR_GC=background`) inherits the variable and makes none.
6. Sets `sys.argv[0]` and undoes the restart idiom ([below](#sysargv0-and-the-restart-idiom)).
7. Parses an entry point that holds a newline as a table of commands (`commands.parse`); a single
   command costs this one test.
8. With `JOURNAL_STREAM` naming the file of stdout or stderr, or as process 1, applies the
   [service policies](#services-and-containers) (`service.start`), also for the helper
   interpreter of step 9.
9. Runs a multiprocessing or helper-interpreter child that the command line asks for and the
   checks allow ([Children through `sys.executable`](#children-through-sysexecutable)).
10. With `FSBPYR_GC` `1` or `background` (any other value runs the app), collects the app's
    cache instead.
11. With `FSBPYR_SELFTEST=natives`, runs the natives self-test ([`selftest`](#selftest)).
12. With any other non-empty `FSBPYR_SELFTEST`, imports the module of every command's target
    and looks its attribute up without calling it (`_check_entrypoint`; a package run as a module
    needs `<package>.__main__`), but only finds a script below `_fsbpyr_scripts`, whose top-level
    code must not run on the build host; then prints `fsbpyr selftest ok: <app>`, which the
    build's smoke test (`smoke_test` in [`build.py`](../src/fsbpyr/build.py)) requires.
13. With a table of commands, chooses the command `argv` names
    ([Several commands in one executable](#several-commands-in-one-executable)).
14. For a target below `_fsbpyr_scripts`, puts its module's directory, as `__file__` shows it
    (`<dir of exe>/sub` for `_fsbpyr_scripts.sub.tool`), first on `sys.path`, as
    `python dir/script.py` does.
15. Runs the target: `module` as `__main__` through `runpy`, with `sys.argv` as it is;
    `module:attr` is resolved, must be callable, and its return value goes to `sys.exit`.

## `sys.argv[0]` and the restart idiom

`argv[0]` is what the caller passed, the bare name for a start through `PATH`. `_execve_argv0`
replaces it with the path `execve` was given (`execfn` of `info()`), as a venv's script gets it,
made absolute: a leading `./` is dropped and a `..` kept, since after a link it names another
file. It leaves `argv[0]` alone when the two basenames differ (`exec -a`), for a start through
`/proc/self/exe` or `/dev/fd/N`, and when the working directory cannot be read. Choosing a
command and argparse's program name use only the basename.

gunicorn's re-execution on `USR2` and Django's autoreloader start
`[sys.executable, <interpreter flags>, sys.argv[0], *args]`, which would give a built app its own
path as an extra argument. When an argument holds a `/` or is the basename of `sys.argv[0]` or
`sys.executable`, `_restart` checks the first argument after the interpreter flags
(`restart.restart_argv`): if it names this very executable (the device and inode of
`/proc/self/exe`, so a command's link counts), the flags and the start's `argv[0]` are dropped and
it becomes `sys.argv[0]`, which then chooses the command. Any other leading option leaves the
arguments alone. The rewrite only removes an argument naming the executable, so it needs no
parent check.

## Several commands in one executable

A build with several commands ([Build modules](Dev-Build-Modules.md)) stores the entry point as
`name=target` lines sorted by name and makes a link per command beside the executable.
`_fsbpyr_rt.commands` reads only that text and `argv`:

- A start runs the command the basename of `argv[0]` names (a link, or the executable under that
  name), else that basename without `.py` (a `name.py` link an old crontab line runs).
- Else, as `<exe> COMMAND args...`, the command `argv[1]` names exactly, which then sees
  `[COMMAND, *args]`, as through its link. A command name cannot hold a `/`, so
  `<exe> /path/to/command` is the restart idiom or a file argument.
- Else it writes `usage: <name> COMMAND [ARGUMENT...]` and the commands to stderr (after
  `<name>: unknown command '<argument>'` when an argument was given) and exits 2.

A start under a command's name that is not the executable's (`commands.is_sibling`: a symlink
named for the command, since `sys.executable` is the resolved path) is never a helper interpreter:
a `-m`, `-c` or script among its arguments is the command's (D44). A hard link, whose own path is
`sys.executable`, keeps the check, as does every start under the executable's name. The check
reads only the word after the interpreter flags, so in `exe COMMAND -m word` the word is the
command, which gets `-m word`.

## Importing from the file

The payload is one read-only directory tree rooted at the executable's path: `sys.path` is
`[<exe>]`, and module `a.b` has `__file__ == <exe>/a/b.py`, except below the scripts root. The
stock `PathFinder` searches it; fsbpyr supplies the path-entry finder and the loaders.

- **`path_hook`** returns a `PayloadPathFinder` for `<exe>` and every `<exe>/<dir>` entry, the
  same below the native cache's site directory once in use, and the directories the scripts root
  shows next to the executable. `''` and `.` components and trailing slashes are dropped, a `..`
  is resolved inside the executable (`<exe>/pkg/../lib` is `<exe>/lib`), and an entry that climbs
  above `<exe>` is not claimed.
- **`PayloadPathFinder.find_spec`** looks the name up with `_fsbpyr.find_module`. A regular
  module or package gets a `ModuleSpec` with `PayloadLoader`, origin `<exe>/<path>` and the
  record as `loader_state`; a namespace record becomes a portion for `PathFinder`; an extension
  module goes to `_fsbpyr_rt.native`. A `sys.path` entry below the root, such as
  `<exe>/pkg/_vendor`, makes `vendored` find `pkg._vendor.vendored` (`AliasLoader`), and
  `pkg.__init__` imported as a module of its own runs the `__init__` record again (`InitLoader`).
- **`PayloadLoader.exec_module`** reads the code with `_fsbpyr.read`, so `marshal` sees only bytes
  that passed the zstd checksum, sets `co_filename` to the virtual path, executes the code and
  applies the module's [fixup](#fixups). The loader also serves `get_source`, `get_filename`,
  `is_package`, `get_data` (for `pkgutil.get_data`) and `get_resource_reader`.
- **`PayloadMetaFinder`**, ahead of `PathFinder`, finds namespace packages of the payload
  (CPython's namespace reader understands only real directories and zip files), merged with
  portions on `sys.path` as in a venv; `distutils` from `setuptools._distutils`, as setuptools'
  `_distutils_hack` provides it; and distributions for `importlib.metadata`.
- **Packages that need real files.** A package flagged FS_REQUIRED_ROOT (one in `fs-packages`, or
  one the build knows needs it) is extracted to the native cache on its first import, where its
  modules get `__file__` and `__path__`; their code still comes from the payload.

### The scripts root

A project without a build system, a directory of scripts and a single script are packaged below the
reserved top-level package `_fsbpyr_scripts`. Its modules appear next to the executable: `__file__`
and `spec.origin` are `<directory of the executable>/<path below the root>` (`_shown`), so the files
a script reads next to its `__file__` are the host's (D24). The code is still the payload's:
`_overlay_finder` serves the directories of the scripts root next to the executable from the
payload, so a stale `.py` copy there is never imported and the host's other files are not looked at;
`loader.get_data(__file__)` reads the packaged source; code objects keep the payload's file name
(`<exe>/_fsbpyr_scripts/<path>`), so tracebacks show the packaged source.

A name below the root that is no Python name is stored mangled (`check-disk` as
`check_disk.py`, `sub-dir/` as `sub_dir/`: each character an identifier cannot hold becomes `_`,
and a leading digit gets a `_` in front); `__file__` shows the stored name, and the finder of an
entry that uses the name on disk looks its modules up under the mangled one (`_module_part`, the
rule of `loose._module_stem`).

## Uncaught exceptions

The boot module's `sys.excepthook` hides the launcher's and its own frames at the top of the
traceback. For an `OSError` in the exception's chain, as the traceback prints it (`raise ...
from None` ends it), on a path below the executable it adds a note (D24;
[Compatibility](Dev-Compatibility.md)):

- below a regular package, naming the outermost one on the path: `fsbpyr: <path> is inside the
  executable; <package> reads files next to its __file__: rebuild with --fs-package <package>`;
- below namespace directories only, that `--fs-package` takes regular packages only;
- for a top-level module or file (`<exe>/x.conf`), that `--fs-package` does not apply;
- below the scripts root, that the file is read next to the executable, where it is to be
  deployed.

A `FileNotFoundError` below the native cache's `site` tree, once the process has its native
cache (`native.loaded_site()`), gets `fsbpyr: <path> is missing from the native cache, which
holds the compiled files of a package only; <package> reads files next to its __file__: rebuild
with --fs-package <package>`, naming the outermost regular package on the path, or no note
without one (`_site_note`; mypyc's `blib2to3` reading `Grammar.txt` is the case). One in a
directory the scripts root shows next to the executable gets `fsbpyr: <path> is missing; the
files a script reads next to its __file__ are read next to the executable, from <directory>/:
deploy it there`.

On Python 3.15 and later, for a `ModuleNotFoundError` in that chain of a module outside the
standard library, the hook drops CPython's hint "did you forget to enable your virtual
environment?" (it prints the traceback with `sys.flags.no_site` reading 0) and adds `fsbpyr:
<name> is not in the executable: add it to the project's dependencies and rebuild`. None of
this costs anything until an exception goes uncaught, nor happens when the app catches it or has
replaced `sys.excepthook`.

## Native code and the cache

Extension modules and the shared libraries they link cannot be loaded from inside a file, so
`_fsbpyr_rt.native` puts them into the app's cache on first import, and CPython's
`ExtensionFileLoader` loads them from there. The cache is `$FSBPYR_CACHE_DIR` (made absolute),
else `${XDG_CACHE_HOME:-~/.cache}/<app>` (a relative `XDG_CACHE_HOME` is ignored), `~` being
`$HOME` when absolute, else the passwd entry's home directory, as for the launcher's `due.c` and
the tool's cache. With no absolute path, the import fails with
`cannot locate a cache directory; set FSBPYR_CACHE_DIR`. Below the cache:

| Tree | Content | Named by |
|---|---|---|
| `runtime/<generation>/site/` | native files, FS_REQUIRED packages | the first 32 hex digits of the payload's `generation_id`: the runtime tag and every extracted file's path, content and mode |
| `resources/<id>/site/` | resources asked for as paths | the `resources` key of `build_info`: every payload file's path, content and mode |

The `site` trees mirror site-packages, so the `$ORIGIN` run paths of bundled libraries resolve
unchanged. The launcher's `_fsbpyr.sync` extracts and checks the files and holds the tree's lock
for the process's life ([`cache.c`](Dev-Launcher.md#cachec-and-cacheh)).

A failed extension load gets a hint when the cache is on a `noexec` mount, or, when the message
names a missing glibc or libstdc++ symbol version or a shared object that cannot be opened, the
glibc the build required and the one the system has. An error the extension's own initialization
raises is passed on as it is.

A refused cache directory, one that belongs to another user or on whose way other users may
write, is a refused start (D41): `NativeCache.extract` catches its `_fsbpyr.CacheRefused` and
writes `fsbpyr: cannot start <executable>: refusing cache directory <path>: other users may
write to it` (or `owned by another user`) to stderr, flushes stdout and stderr, and leaves with
exit 70 through `os._exit`, since the refusal may arise in a thread or under code that catches
`SystemExit`. Any other failure to extract is an error the app sees, saying to set
`FSBPYR_CACHE_DIR`: `_fsbpyr.CacheError` for an extension or an FS_REQUIRED package, an
`OSError` for a resource.

## Resources and metadata

`importlib.resources.files()` on a payload package returns a `PayloadTraversable`
(`_fsbpyr_rt.resources`), a read-only view of the payload tree. Opening a file reads it whole up
to 16 MiB and streams larger ones through `_fsbpyr.stream`. A resource asked for as a path
(`os.fspath`, `str`, `importlib.resources.as_file`) is extracted into the resources tree and
stays there, as an installed package's files do, so a path kept after `as_file` returns keeps
working. FS_REQUIRED packages get a `DirectoryReader` over their extracted directory.

`importlib.metadata` finds the payload's distributions through
`PayloadMetaFinder.find_distributions` (`_fsbpyr_rt.metadata`): a name lookup uses the sorted
DISTS table, and a `PayloadDistribution` reads its dist-info files from the payload.

## Children through `sys.executable`

In an executable, `sys.executable` is the app. Two kinds of children start it again:

- **multiprocessing** (`_fsbpyr_rt.mp`): spawn, forkserver and the resource tracker run
  `<exe> [flags] -c <code> [args]`. The boot module recognizes exactly these command lines,
  decodes their arguments with `ast.literal_eval` (the command line is never executed), and calls
  the function they name. A command line whose descriptors are standard input, output or
  error, or that asks for something an fsbpyr parent never sends (a `main_path`, unexpected
  keywords), is no such child; one whose parent does not run this very executable with the
  same real and effective user and group ids is refused with a message. A forkserver asked to preload
  `__main__` imports the module of a `module:attr` entry point instead (with several commands,
  of the command its parent runs, read from `/proc/<ppid>/cmdline`), as a venv's forkserver
  imports the console script.
- **helper interpreters** (`_fsbpyr_rt.reexec`): `<exe> [flags] -m MODULE`, `-c CODE`, or a `.py`
  file below `<exe>/` (in an app with scripts, also below the executable's directory), or below
  the native cache's site, where a module of an fs-package says it lives (torch's compile
  workers: `<cache>/runtime/<generation>/site/torch/_inductor/compile_worker/__main__.py`; the
  boot module looks for the end of that path, `_FS_SITE_TAIL`, before it imports anything). Only a
  child whose parent runs this executable counts; a shell, a script or a sudo rule gets the app.
  `interpreter_reexec` in `build_info` decides: `true` runs it as CPython would (interpreter flags
  accepted and ignored); `false` runs the app; not set refuses, with exit 70 and a message naming
  `--interpreter-reexec` and `--no-interpreter-reexec`, a child that looks like Python (a dotted
  name after `-m`, code after `-c` that is more than a name, attribute or constant, or a payload
  script), and runs the app otherwise. Unless the setting is `false`, a child that names the
  script of one of the app's own commands runs that command, as `python sibling.py` would. Any
  other path below the executable's directory is a file of the host, an argument of the app. Only
  the first argument after the interpreter flags is looked at.

Multiprocessing children start from `/proc/self/exe` (the `multiprocessing.spawn` fixup), so
parent and children never run different versions of the file. Users' view:
[`interpreter-reexec`](User-Settings.md#interpreter-reexec).

## Services and containers

- **`PYTHONUNBUFFERED`**, set and not empty, makes the launcher configure stdout and stderr
  unbuffered.
- **`JOURNAL_STREAM`**, systemd's `<device>:<inode>` (decimal) of the journal's stream, makes
  stdout and stderr line-buffered where they are that file and not unbuffered already, so each
  line reaches the journal when printed; a value that does not parse changes nothing.
- **Process 1.** The kernel drops a signal that process 1 has no handler for, so `python
  app.py` as process 1 (a container without an init) never dies of SIGTERM. A built app installs
  a handler that raises `SystemExit(143)` while SIGTERM has its default disposition; the app's own
  handler replaces it, and a forked child gets the default back (`os.register_at_fork`), so a
  worker in a long C call still dies of the signal.

The boot module looks up `JOURNAL_STREAM` and the process id at every start and imports
`_fsbpyr_rt.service` only when one of them applies. A parent whose output is the journal hands
the variable on to processes whose output is not (GNOME Shell's apps, a service's children on
pipes), so with it set the boot module first compares stdout's and stderr's device and inode
(`posix.fstat`) with it: a value of another file, or one that does not parse, imports nothing.

## Modules

Imports below use the names inside the executable (`_fsbpyr_rt.<name>`); the module graph lists
them as `fsbpyr.boot._fsbpyr_rt.<name>`. `_fsbpyr_boot`, `native`, `resources`, `metadata`,
`selftest` and `trace` also import the launcher's built-in `_fsbpyr` at module level, `fixups`
inside one function; `gc`, `mp`, `reexec`, `commands`, `links`, `restart` and `service` do not
use it.

### `_fsbpyr_boot`

[`src/fsbpyr/boot/_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py)

The bootstrap described above, with the executable's `info()` as `INFO` and its path as `ROOT`.
`PayloadLoader.exec_module` carries the file name `<frozen importlib._bootstrap_fsbpyr>`, so that
`warnings.warn` with a `stacklevel` skips it, as importlib's own frames. Whether the app has
scripts (an entry point naming `_fsbpyr_scripts.`) is decided once, at import.

Classes:

- `PayloadLoader(prefix=<exe>/)`: loads modules and packages; `prefix` is where they appear to
  live (the executable, or the cache for an FS_REQUIRED package).
- `AliasLoader(prefix, public, payload)`: a module imported under another name than its own.
- `InitLoader`: `pkg.__init__` imported as a module of its own; not a package.
- `PayloadPathFinder(directory, location=<exe>/)`: the path-entry finder of one virtual
  directory; also `iter_modules` for `pkgutil` (modules and regular packages, not namespace
  directories).
- `PayloadNamespaceLoader`: namespace packages of the payload, with a resource reader.
- `SetuptoolsDistutilsLoader`: `distutils` as the module setuptools ships.
- `PayloadMetaFinder`: the meta-path finder above.
- `_FlagsWithSite` (private): `sys.flags` with `no_site` reading 0, while a traceback prints on
  3.15.

Public functions:

- `path_hook(entry)`: the path hook; `ImportError` for any other entry.
- `run()`: the start-up sequence.

Rules it enforces: those above.

Imports: `_fsbpyr_rt.native`, `_fsbpyr_rt.mp`, `_fsbpyr_rt.restart`, `_fsbpyr_rt.reexec`,
`_fsbpyr_rt.resources`, `_fsbpyr_rt.metadata`, `_fsbpyr_rt.commands`, `_fsbpyr_rt.service`,
`_fsbpyr_rt.links`, `_fsbpyr_rt.trace`, `_fsbpyr_rt.selftest`, `_fsbpyr_rt.fixups` (all lazy).
Imported by: none in the package graph; the launcher imports it by name.

### `native`

[`src/fsbpyr/boot/_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py)

[Native code and the cache](#native-code-and-the-cache). Each process remembers the payload
files it has checked (a bit per file) and the resource paths it has extracted; the paths it wrote
only with `FSBPYR_TRACE`. `REFUSED` is 70.

Classes:

- `NativeCache(root, *chain)`: one tree of the cache (`root`, `site`, and `tree`, the
  `runtime/<generation>` or `resources/<id>` part); `sync(files, resources=False)` extracts or
  checks payload files and returns how many it wrote; `extract` is `sync` where a refused cache
  directory ends the start (`refuse`), as every extraction of the boot code uses it.
- `PayloadExtensionLoader(name, path, files)`: an `ExtensionFileLoader` that extracts the
  extension and its closure first, with the error hints above.

Public functions:

- `refuse(exc)`: the refused start: the line on stderr and exit 70; it does not return.
- `home_directory(environ)`: `$HOME` if absolute, else the passwd entry's home directory, `""`
  when there is none.
- `cache_directory(app, environ)`: the app's cache root; a corrupt app name, or no absolute base,
  is an error.
- `native_cache()`: this executable's `runtime/<generation>` tree, computed once without touching
  the file system.
- `loaded_site()`: that tree's `site` directory once computed in this process, else `None`.
- `resource_cache()`: the `resources/<id>` tree; the id must be 32 alphanumeric characters from
  `build_info`.
- `materialize_package(directory)`: extract an FS_REQUIRED package with the closures of its
  extensions (`torchvision.libs`) and every shared library its distributions ship (openmm's
  plugins); returns the prefix its modules live under. Once a process: a `find_spec` before the
  import (`importlib.util.find_spec("torch")`) has extracted it already.
- `materialize(path)`: extract a payload file or directory for `importlib.resources`; returns
  where it is on disk.
- `bundled_library(name, caller=None)`: the one bundled shared object of that file name, extracted
  with what it needs; of several, the one in the caller's top-level package; `None` if no single
  file qualifies.
- `extension_spec(fullname, record, prefix)`: an extension module's spec (a package when its file
  is a compiled `__init__`).
- `collect(quiet)`: `FSBPYR_GC`: collect this app's cache, never this build's own trees; exit
  status 1 if anything could not be removed.

Rules it enforces: an app name is a single path component; the payload root is never extracted as
a resource; an extension record points at a NATIVE_EXT file.

Imports: `_fsbpyr_rt.gc` (lazy).
Imported by: `_fsbpyr_boot`, `_fsbpyr_rt.fixups`, `_fsbpyr_rt.resources`,
`_fsbpyr_rt.selftest`.

### `resources`

[`src/fsbpyr/boot/_fsbpyr_rt/resources.py`](../src/fsbpyr/boot/_fsbpyr_rt/resources.py)

[Resources and metadata](#resources-and-metadata). Its own `as_file` for `PayloadTraversable`
yields the extracted path instead of a temporary copy.

Classes:

- `PayloadTraversable(root, path)`: a file or directory of the payload; `iterdir`, `joinpath`,
  `is_file`, `is_dir`, `read_bytes`, `read_text`, `open`, and `__fspath__`, which extracts. `str()`
  falls back to the virtual path, with a warning, when the cache cannot be used (not when it is
  refused).
- `_StreamedFile` (private): a large resource read as a seekable raw file over an
  `_fsbpyr.Stream`.
- `PayloadResourceReader(root, directory)`: the reader of a payload package.
- `DirectoryReader(directory)`: the reader of an extracted FS_REQUIRED package.

Public functions: none.

Rules it enforces: resources are read-only (`ValueError` for a write mode); paths that leave the
payload root resolve to nothing; extracting the whole executable is refused.

Imports: `_fsbpyr_rt.native` (top).
Imported by: `_fsbpyr_boot`, `_fsbpyr_rt.metadata`.

### `metadata`

[`src/fsbpyr/boot/_fsbpyr_rt/metadata.py`](../src/fsbpyr/boot/_fsbpyr_rt/metadata.py)

Distributions are found only when the executable is on the request's search path (by default
`sys.path`).

Classes:

- `PayloadDistribution(root, dist_info)`: `read_text` from the dist-info directory,
  `locate_file` as a `PayloadTraversable`.

Public functions:

- `find_distributions(root, context=None)`: every payload distribution, or the one whose
  normalized name matches.

Imports: `_fsbpyr_rt.resources` (top).
Imported by: `_fsbpyr_boot`.

### `commands`

[`src/fsbpyr/boot/_fsbpyr_rt/commands.py`](../src/fsbpyr/boot/_fsbpyr_rt/commands.py)

[Several commands in one executable](#several-commands-in-one-executable). `EXIT_USAGE` is 2.

Public functions:

- `parse(table)`: the command names with their targets, in the table's order.
- `named(commands, argv0)`: the command the basename of `argv0` names, as it is or without
  `.py`; `None` if neither.
- `is_sibling(commands, argv0, executable)`: whether `argv0` names a command under another name
  than the executable's own.
- `find(commands, argv)`: `(target, shift)` for the command `argv` starts, `shift` saying whether
  `argv` loses its first item; `None` if it starts none.
- `choose(commands, argv)`: the target of the command `argv` starts, shifting `argv` for
  `<exe> COMMAND`; without a command, the usage on stderr and exit 2.

Imports: none.
Imported by: `_fsbpyr_boot`, `_fsbpyr_rt.links`, `_fsbpyr_rt.mp`; in the tool, `fsbpyr.build`,
`fsbpyr.classify`, `fsbpyr.inspection`.

### `links`

[`src/fsbpyr/boot/_fsbpyr_rt/links.py`](../src/fsbpyr/boot/_fsbpyr_rt/links.py)

`FSBPYR_INSTALL_LINKS=DIR app` makes in `DIR` an absolute symlink to the executable's real path
per command (the app's name for a single command), and `<name>.py` too for a script target
without an attribute, so a line that runs `DIR/name.py` keeps working; it prints
`fsbpyr: linked <path> -> <real path>` (or `present`) for each and exits without running the
app. A link to this executable is left alone; anything else in the way is reported and never
replaced, the other links are still made, and the exit status is 1, as for a `DIR` that is not a
writable directory. There is no command-line form: under a `sudo <binary> *` rule a caller could
make root-owned links anywhere, and sudo's `env_reset`, a default, drops `FSBPYR_*`.

Public functions:

- `names(entrypoint, app_name)`: the link names.
- `install(directory, executable, entrypoint, app_name)`: make the links; the exit status.

Rules it enforces: a name with a directory part, `.` or `..` gets no link (status 1).

Imports: `_fsbpyr_rt.commands` (top).
Imported by: `_fsbpyr_boot`.

### `restart`

[`src/fsbpyr/boot/_fsbpyr_rt/restart.py`](../src/fsbpyr/boot/_fsbpyr_rt/restart.py)

[The restart idiom](#sysargv0-and-the-restart-idiom). It imports only the built-in `posix`.

Public functions:

- `after_flags(argv)`: the index of the first argument after interpreter flags (`-X` and `-W`
  take a value), or `None` if `-` or nothing follows them; `reexec` uses it too.
- `restart_argv(argv)`: `argv` from the argument that names this executable on, or `None` if the
  start is no restart.

Rules it enforces: only interpreter flags (`-B`, `-b`, `-bb`, `-d`, `-E`, `-I`, `-O`, `-OO`,
`-P`, `-q`, `-R`, `-s`, `-S`, `-u`, `-v`, `-vv`, and `-X` or `-W` with their value) may precede
the argument, which must be the same file as `/proc/self/exe`; nothing is imported, run or opened
from the arguments (D44).

Imports: none.
Imported by: `_fsbpyr_boot`, `_fsbpyr_rt.reexec`.

### `service`

[`src/fsbpyr/boot/_fsbpyr_rt/service.py`](../src/fsbpyr/boot/_fsbpyr_rt/service.py)

[Services and containers](#services-and-containers). `EXIT_SIGTERM` is 143.

Public functions:

- `journal_identity(value)`: the `(device, inode)` a `JOURNAL_STREAM` value names, or `None`.
- `is_journal(stream, identity)`: whether a stream is that file.
- `line_buffer_journal(value)`: make stdout and stderr line-buffered where they are the journal
  and not unbuffered already.
- `terminate_on_sigterm()`: install the `SystemExit(143)` handler while SIGTERM has its default
  disposition, and give forked children the default back.
- `start(journal, pid)`: apply what the value of `JOURNAL_STREAM` and the process id call for.

Imports: none.
Imported by: `_fsbpyr_boot`.

### `mp`

[`src/fsbpyr/boot/_fsbpyr_rt/mp.py`](../src/fsbpyr/boot/_fsbpyr_rt/mp.py)

[Multiprocessing children](#children-through-sysexecutable). It knows the forkserver command
line of 3.12 and of 3.13 and later, and the keywords each version may send (`authkey_r` from
3.14, `on_error` from 3.15); the forkserver's `sys_path` is dropped.

Public functions:

- `child_command(argv)`: the child `argv` asks for, as `(module, function, arguments, keywords,
  sys.argv)`, or `None` for a normal run.
- `preload_names(arguments)`: the module names a forkserver command preloads.
- `run_child(argv, entrypoint="")`: run the child; `False` if `argv` asks for none. `entrypoint`
  is the app's own, `module:attr`, `module` or a table of commands.

Rules it enforces: those above, with descriptors of at least 3, an `on_error` of `ignore`, `warn`
or `fail`, the parent identified by the device and inode of `/proc/<pid>/exe`, and any doubt
counting as no.

Imports: `_fsbpyr_rt.commands` (lazy).
Imported by: `_fsbpyr_boot`, `_fsbpyr_rt.reexec`.

### `reexec`

[`src/fsbpyr/boot/_fsbpyr_rt/reexec.py`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py)

[Helper interpreters](#children-through-sysexecutable). A payload script, named by its path
inside the executable or, for the scripts root, beside it, runs through `runpy` as the module
whose file it is (a package's `__main__` as the package), with its directory first on
`sys.path`; `-m` runs through `runpy` too, and `-c` executes the code in `__main__`. `sys.argv` is
left as CPython would leave it. `REFUSED` is 70.

Public functions:

- `child_command(argv, prefix=None, overlay=None)`: `("-m", module, args)`, `("-c", code, args)`
  or `("script", path, args)` after interpreter flags, or `None`; `overlay` (the executable's
  directory, in an app with scripts) also names scripts.
- `asks_for_python(command)`: whether the command looks like Python rather than the app's own
  options.
- `refusal(command, prefix)`: the refusal message.
- `run_child(argv, build_info, prefix, targets=(), overlay=None)`: run, refuse (exit 70), or
  return `False` for the app; `targets` are the targets of the app's commands.

Rules it enforces: those above; the parent is checked before anything of a script's packages is
imported, and a script must be a payload module file whose spec has that origin.

Imports: `_fsbpyr_rt.mp`, `_fsbpyr_rt.restart` (top).
Imported by: `_fsbpyr_boot`.

### `fixups`

[`src/fsbpyr/boot/_fsbpyr_rt/fixups.py`](../src/fsbpyr/boot/_fsbpyr_rt/fixups.py)

Adjustments to particular modules, applied by `PayloadLoader` right after the module's code has
run:

| Module | Adjustment |
|---|---|
| `ssl` | When OpenSSL's defaults (PBS builds it with `/etc/ssl`) find neither `/etc/ssl/cert.pem` nor hashed links in `/etc/ssl/certs`, and neither `SSL_CERT_FILE` nor `SSL_CERT_DIR` is set, `SSLContext.set_default_verify_paths` also loads the first readable bundle of `SYSTEM_BUNDLES` (`/etc/ssl/certs/ca-certificates.crt`, `/etc/ssl/certs/ca-bundle.crt`, `/etc/pki/tls/certs/ca-bundle.crt`, `/etc/ssl/ca-bundle.pem`). |
| `multiprocessing.spawn` | Children start from `/proc/self/exe`. |
| `ctypes` | `CDLL` of a bare file name the system cannot load falls back to the app's bundled library of that name (`native.bundled_library`). |
| `parso.file_io` | A payload file's modification time is the executable's, where parso would get `NotADirectoryError`. |
| `cv2.load_config_py3` | Sets `sys.OpenCV_REPLACE_SYS_PATH_0`, so that cv2's loader inserts its directory at `sys.path[0]` and does not find the cv2 package again. |
| `argparse` | In an app with scripts, on 3.14 and later: when `__main__` is a module of the scripts root whose origin is `sys.argv[0]` (a `name.py` link beside the executable), the default program name is the file's name, not `<exe> -m <module>` as on 3.14.9 and 3.15. |

Public functions:

- `fallback_bundle(environ)`: the system CA bundle to add, or `None`.
- `apply(module)`: run the fixup of a module named in `FIXUPS`.

Imports: `_fsbpyr_rt.native` (lazy).
Imported by: `_fsbpyr_boot`.

### `gc`

[`src/fsbpyr/boot/_fsbpyr_rt/gc.py`](../src/fsbpyr/boot/_fsbpyr_rt/gc.py)

The cache collector, standard library only: an app runs it with `FSBPYR_GC`, the tool for
`fsbpyr cache gc` and for its own cache (`runtime/collect.py`, same rules). Its docstring's rules:

- Only a root with the marker `.fsbpyr-cache` is collected; `mark`, as the launcher's `cache.c`,
  writes it only into a root that holds nothing but fsbpyr's names.
- Every tree (`runtime/<32 hex>`, `resources/<32 hex>`) has a `.lock` that each process using it
  holds shared for its life (the launcher's sync takes it); its modification time, touched once a
  day, is the tree's last use.
- A run holds `.gc` exclusively (one run at a time; its time is the last run's start) and takes
  each tree's lock exclusively, failing when the tree is in use; a tree without a lock (written
  before 0.13.0) gets one dated by the tree's own time. A tree unused for 7 days, or any tree not
  in use when the file system has less than 10% free, is renamed into `.trash` while locked; the
  trash is emptied at the end of the run, never following a symlink. Files a killed process left
  in a kept tree's `.scratch` (`.fsbpyr-*`, held by nobody, an hour old) go too.
- A tree that cannot be removed is reported and the run goes on; nothing else is touched.
- `way_problem` applies the launcher's rule for the way to a cache (`way.h`,
  [Dev-Security](Dev-Security.md#the-cache)).

Classes:

- `Report`: what a run did (removed, temporary files removed, kept, errors; not marked; busy;
  under pressure); `text()`.
- `Run(root, trash, now, report)`: one run over a root; `attempt`, `unused`, `take`, `discard`,
  `listed`, `empty_trash`.
- `Problem(where, error, reason)`: what makes a way untrustworthy, with `text()`.
- `_Way` (private): one walk of a path from `/`, following symlinks as the kernel does.

Public functions:

- `collect(root, keep=frozenset(), now=None, pressure=None)`: collect an app cache, never removing
  the trees in `keep`.
- `run(root, stamp, trash, work, now=None, pressure=None)`: every run's frame: way checked,
  marker required, stamp held, `work` called, trash emptied.
- `way_problem(path)`: the first problem on the way to `path`, or `None`.
- `short_of_space(where)`: whether the file system has less than 10% free.
- `ours(directory, name, kind)`: whether an entry is of a kind and owned by the user.
- `mark(root, new=True)`: write the marker if the root qualifies; whether it is marked.
- `exclusive(directory, name, create=False)`: a context manager holding a lock file
  exclusively, or yielding `None`.
- `remove(parent, name)`: remove a tree without following symlinks, holding at most three
  descriptors whatever its depth.

Imports: none.
Imported by: `_fsbpyr_rt.native` (lazy); in the tool, `fsbpyr.cli`, `fsbpyr.runtime.collect`,
`fsbpyr.runtime.hold`, `fsbpyr.runtime.registry`.

### `selftest`

[`src/fsbpyr/boot/_fsbpyr_rt/selftest.py`](../src/fsbpyr/boot/_fsbpyr_rt/selftest.py)

`FSBPYR_SELFTEST=natives`: each native extension and its closure are put into the cache, and
the executable's own program interpreter (`PT_INTERP`, the build target's) resolves their
libraries and symbol versions in trace mode (`LD_TRACE_LOADED_OBJECTS=1`, as `ldd` does) without
running them. Only glibc's trace output is understood.

Public functions:

- `interpreter(header)`: the program interpreter an ELF64 little-endian file names, or `None`.
- `missing(trace)`: the missing libraries and symbol versions in the loader's output.
- `natives(loader=None)`: a line on stderr per missing item, with the extensions that need it,
  and exit status 1, or a line on stdout that all load; a refused cache directory ends it as a run
  (`native.refuse`).

Imports: `_fsbpyr_rt.native` (top).
Imported by: `_fsbpyr_boot`.

### `trace`

[`src/fsbpyr/boot/_fsbpyr_rt/trace.py`](../src/fsbpyr/boot/_fsbpyr_rt/trace.py)

`FSBPYR_TRACE`: at exit, one line on stderr, `fsbpyr-trace ` and JSON: the modules loaded from
the payload by distribution (`""` for none, boot modules left out), the paths extracted into the
cache, and `_fsbpyr.stats()`, the payload read counters and the nanoseconds the launcher's
whole-file integrity check took.

Public functions:

- `summary()`: the report as a dictionary.
- `report()`: print it.

Imports: none.
Imported by: `_fsbpyr_boot`.

## Tests

The tests in [`tests/unit/boot/`](../tests/unit/boot) run the boot module in-process against a
pure-Python stand-in of `_fsbpyr` ([`tests/support/standin.py`](../tests/support/standin.py),
built on the payload reader); the contract battery
[`tests/support/contract.py`](../tests/support/contract.py) runs against the stand-in and,
inside a built executable, against the C module, which must agree.
[Testing](Dev-Testing.md) describes the suite.

## See also

- [Launcher](Dev-Launcher.md): what happens before `run()`, and `_fsbpyr.sync`.
- [Executable format](Dev-Executable-Format.md): the records the boot code reads.
- [Compatibility](Dev-Compatibility.md): what differs from a venv.
- [Security](Dev-Security.md): the cache's trust rules and the child checks.
