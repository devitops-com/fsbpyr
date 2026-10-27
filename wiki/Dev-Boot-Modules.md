# Boot modules

The package [`src/fsbpyr/boot/`](../src/fsbpyr/boot)
is the Python code that runs inside every built executable: the boot module `_fsbpyr_boot.py`
and the helper package `_fsbpyr_rt`. This page describes how the [launcher](Dev-Launcher.md) hands
over to the boot module, the order of the start-up decisions, `sys.argv[0]` and the restart
idiom, how one executable serves several commands, how modules are imported straight from the
file and where the scripts of a project without a build system appear to live, the notes added
to uncaught errors, how native extension modules and shared libraries reach the cache, how
`importlib.resources` and `importlib.metadata` work, how `multiprocessing` children and other
`sys.executable` children are handled, the policies for services and containers, the fixups for
particular packages, the app cache's collector, the self-test and tracing; then each module in
turn.

## How the code gets into an executable

`assemble.boot_modules` ([Build modules](Dev-Build-Modules.md)) reads the package's files and
compiles them with the building interpreter, which must have the runtime's bytecode magic
(`assemble.build_temporary` checks it). They are stored under top-level names:

| In the source tree | In the executable | Stored as |
|---|---|---|
| `fsbpyr/boot/_fsbpyr_boot.py` | `_fsbpyr_boot` | a STARTUP module, file name `<frozen _fsbpyr_boot>` |
| `fsbpyr/boot/_fsbpyr_rt/__init__.py` | `_fsbpyr_rt` | a regular package of the payload |
| `fsbpyr/boot/_fsbpyr_rt/<name>.py` | `_fsbpyr_rt.<name>` | regular modules of the payload |

So inside an executable the helpers import as `_fsbpyr_rt.native`, `_fsbpyr_rt.gc` and so on,
while fsbpyr itself (the tool) imports the collector as `fsbpyr.boot._fsbpyr_rt.gc`
([Runtime modules](Dev-Runtime-Modules.md)) and the parser of the command table as
`fsbpyr.boot._fsbpyr_rt.commands` (`build`, `classify` and `inspection`,
[Build modules](Dev-Build-Modules.md)). Every build regenerates them: the runtime registry leaves
`_fsbpyr_boot` and `_fsbpyr_rt` out of a runtime taken from an executable. They are hidden from
`pkgutil.iter_modules` at the top level and from the `FSBPYR_TRACE` report.

`_fsbpyr_boot` imports only built-in and frozen modules at module level (`_imp`,
`_sitebuiltins`, `builtins`, `marshal`, `posix`, `sys`, `_frozen_importlib`,
`_frozen_importlib_external`, and the launcher's `_fsbpyr`), so that a start touches no file
and decompresses nothing it does not need. Everything else is in `_fsbpyr_rt`, imported only
when an app uses it or a start needs it, where the regular standard library may be used.

## The handover from the launcher

The launcher ([`main.c`](../src/fsbpyr/launcher/main.c),
described on [Launcher](Dev-Launcher.md)) checks the file's integrity digest, opens the payload,
checks the Python version, takes an instance slot, and may start a background collection of
the app's cache. Then it prepares CPython:

- it decompresses the code of every STARTUP module (the startup modules of the standard
  library and `_fsbpyr_boot`) and hands it to CPython as its frozen-module table;
- it registers its built-in module `_fsbpyr`, the boot code's bounds-checked access to the
  payload;
- it configures an isolated interpreter with UTF-8 mode, no `site` import, no bytecode
  writing, CPython's own signal handlers, the arguments as given, `sys.executable` set to the
  executable's path, every prefix set to its directory, the standard library directory set to
  the executable itself, and an empty `sys.path`; stdout and stderr are unbuffered when
  `PYTHONUNBUFFERED` is set and not empty (the one `PYTHON*` variable it reads), and CPython's
  import-time table is printed when `FSBPYR_IMPORTTIME` is set;
- it runs `import _fsbpyr_boot` and `_fsbpyr_boot.run()` as the interpreter's command through
  `Py_RunMain`, so exit statuses, `SystemExit` and `SIGINT` behave as in CPython.

The functions of `_fsbpyr` that the boot code uses (documented at the top of
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
| `info()` | the executable's path, the path `execve` was given (`execfn`, from `AT_EXECFN`, or `None`), app name, entry point, `build_info`, `generation_id`, Python version, payload size, and the module and file counts |
| `stats()` | read counters and the integrity check's time |
| `sync(root, chain, files, checked, resources[, written])` | extracts files into the cache, or checks them there |

`_fsbpyr.CacheError` (an `ImportError`) is raised when the cache cannot be used or the payload's
data for it is corrupt; `_fsbpyr.NONE` is the format's "no reference" value.

## From `run()` to the app

![Boot startup: the launcher runs _fsbpyr_boot.run(), which installs the import machinery; FSBPYR_INSTALL_LINKS makes the command links and exits; otherwise sys.argv[0] is set from AT_EXECFN, the restart idiom is undone, a command table is parsed and the service policies applied; then it checks in turn for a multiprocessing child, a helper-interpreter child, FSBPYR_GC, FSBPYR_SELFTEST=natives (which ends with exit 70 when the cache directory is refused) and FSBPYR_SELFTEST, chooses the command, puts a script's directory first on sys.path, and runs the target as a module or calls it; while the app runs, the first extraction into the cache ends the start with exit 70 when the cache directory is refused](images/boot-startup.svg)

`_fsbpyr_boot.run()`:

1. On Python 3.12, which leaves `sys._stdlib_dir` unset, sets it to the executable and gives
   the frozen standard-library modules a `__file__` (and packages a `__path__`) inside the
   executable's tree, as 3.13 does.
2. Puts `path_hook` first on `sys.path_hooks`, sets `sys.path` to `[<exe>]`, clears the path
   importer cache, and puts `PayloadMetaFinder` on `sys.meta_path` just before `PathFinder`.
3. Installs its `sys.excepthook` if the hook is still the default, lets `FrozenImporter`
   return payload sources for frozen modules, teaches `linecache` to read payload files (now
   if `linecache` is a startup module, else when it is loaded), and defines `exit`, `quit` and
   `help` as `site` would (built apps never import `site`).
4. With `FSBPYR_TRACE` set, registers `_fsbpyr_rt.trace.report` to run at exit.
5. With `FSBPYR_INSTALL_LINKS` set to a directory, links the app's commands into it
   (`links.install`) and exits with the status that returns; the app does not run. The
   launcher's detached collection child, started with `FSBPYR_GC=background`, inherits the
   variable and makes no links.
6. Sets `sys.argv[0]` to the path the start was made by, and undoes the restart idiom
   ([below](#sysargv0-and-the-restart-idiom)).
7. When the entry point holds a newline, parses it as a table of commands (`commands.parse`);
   a single command costs this one test.
8. With `JOURNAL_STREAM` set, or as process 1, applies the service policies (`service.start`,
   [Services and containers](#services-and-containers)), before the children below, so that a
   helper interpreter of an app that is process 1 gets them too.
9. Runs a multiprocessing child, or a helper-interpreter child, if the command line asks for
   one and the checks allow it ([Children through `sys.executable`](#children-through-sysexecutable)),
   and returns. In an executable with several commands, a start under the name of one of the
   commands, where that is not the executable's own name, is never taken for a helper
   interpreter.
10. With `FSBPYR_GC` set to `1` or `background`, collects the app's cache instead of running the
    app; any other value runs the app.
11. With `FSBPYR_SELFTEST=natives`, runs the natives self-test; a refused cache directory ends
    it as it ends a run, with exit 70 ([Native code and the cache](#native-code-and-the-cache)).
12. With any other non-empty `FSBPYR_SELFTEST`, checks the target of every command, whatever
    `argv[0]` is (`_check_entrypoint`): it imports the target's module and looks its attribute
    up without calling it, and for a package run as a module it also requires
    `<package>.__main__`. A script of a project without a build system (a target below
    `_fsbpyr_scripts` without `:attr` whose record is a module) is only found, never imported:
    its top-level code does the work and must not run on the build host. Then it prints
    `fsbpyr selftest ok: <app>` and returns. The build's smoke test runs the new executable with
    `FSBPYR_SELFTEST=1` and requires exit 0 and that line (`smoke_test` in
    [`build.py`](../src/fsbpyr/build.py)).
13. With a table of commands, chooses the command `argv` names (`commands.choose`,
    [Several commands in one executable](#several-commands-in-one-executable)); a start that
    names none lists the commands and exits 2.
14. For a target below `_fsbpyr_scripts`, puts the directory of its module, as `__file__`
    shows it, first on `sys.path`: the executable's directory plus the module's path below the
    root without its last part (`<dir of exe>/sub` for `_fsbpyr_scripts.sub.tool`), not a
    directory inside the executable ([The scripts root](#the-scripts-root)). That is what
    `python dir/script.py` does, so its siblings import.
15. Runs the target: `module` runs as `__main__` through `runpy`, with `sys.argv` as it is;
    `module:attr` is resolved, must be callable, and its return value goes to `sys.exit`.

## `sys.argv[0]` and the restart idiom

The launcher hands CPython the arguments as the process got them, so `argv[0]` is what the
caller passed: the bare name for a start through `PATH`. `_execve_argv0` sets it to the path
`execve` was given (`AT_EXECFN`, which `info()` reports as `execfn`), as a venv's script gets
it, made absolute against the working directory: a leading `./` is dropped and a `..` is kept,
since after a link it names another file (for a relative start a venv's script keeps `./app`,
where the app gives `/cwd/app`). It leaves `argv[0]` as it is when the two basenames differ (a
caller that chose another name, `exec -a`), for a start through `/proc/self/exe` or
`/dev/fd/N`, and when the working directory cannot be read. The choice of a command and
argparse's program name use only the basename.

gunicorn's re-execution on `USR2` and Django's autoreloader start
`[sys.executable, <interpreter flags>, sys.argv[0], *args]`. In a venv that runs the script with
`sys.argv == [sys.argv[0], *args]`; in a built app `sys.executable` is the app, which would get
its own path as an extra first argument. `_restart` looks closer only when an argument holds a
`/` or is the basename of `sys.argv[0]` or of `sys.executable`; then `restart.restart_argv`
takes the first argument after the interpreter flags. When that argument names this very
executable (the same device and inode as `/proc/self/exe`, so a symlink or a command's link
counts) and only interpreter flags precede it, the flags and the start's own `argv[0]` are
dropped and the argument, as given, becomes `sys.argv[0]`. Any other leading option means the
arguments are the app's own, and nothing is rewritten. There is no parent check: the rewrite
only removes an argument that names the executable itself. In an executable with several
commands the new `argv[0]` then chooses the command, so a restart through a command's link
runs that command.

## Several commands in one executable

A build with several commands (several console scripts, a directory of scripts, the tools of a
requirements file; [Build modules](Dev-Build-Modules.md)) stores the entry point as lines
`name=target`, sorted by name ([Payload modules](Dev-Payload-Modules.md)), and makes a link per
command beside the executable. `_fsbpyr_rt.commands` reads the table; only the entry point's
text and `argv` are read.

- A start runs the command the basename of `argv[0]` names (a link, or the executable under
  that name), else the command that basename names without `.py` (a `name.py` link that an old
  crontab line runs).
- Else, as `<exe> COMMAND args...`, it runs the command `argv[1]` names exactly; `sys.argv`
  then loses its first item, so the command sees `[COMMAND, *args]`, as through its link. A
  command name cannot hold a `/`, so a path is never a command: `<exe> /path/to/command` is the
  restart idiom or a file argument.
- Else it writes `usage: <name> COMMAND [ARGUMENT...]` and the list of commands to stderr
  (after `<name>: unknown command '<argument>'` when an argument was given) and exits 2.

A start under a command's name that is not the executable's own (`commands.is_sibling`: a
symlink named for the command, since `sys.executable` is the resolved path) is that command's
own: a `-m`, `-c` or script among its arguments is the command's, never a helper interpreter
(D44). A hard link keeps the check, since there `sys.executable` is the link's own path, and so
does every start under the executable's name. The check reads only the word after the
interpreter flags, so the dispatcher form `exe COMMAND -m word` is no helper interpreter: the
word is the command's name, and `COMMAND` runs with `-m word` as its arguments.

The self-test checks every command's target. A forkserver that preloads `__main__` takes the
command its parent runs ([Children through `sys.executable`](#children-through-sysexecutable)).
`FSBPYR_INSTALL_LINKS=DIR` makes the links on the target ([`links`](#links)).

## Importing from the file

The payload appears to Python as one read-only directory tree rooted at the executable's path:
`sys.path` is `[<exe>]`, and module `a.b` has `__file__ == <exe>/a/b.py`. The stock
`PathFinder` does the searching, as for a site-packages directory; fsbpyr supplies the
path-entry finder and the loaders. The one exception to where modules appear to live is the
scripts root (below).

- **`path_hook`** claims `<exe>` and every `<exe>/<dir>` entry, and the same below the native
  cache's site directory once that is in use. `''` and `.` components and trailing slashes are
  dropped, and a `..` is resolved inside the executable together with the component before it
  (`<exe>/pkg/../lib` is `<exe>/lib`); an entry that climbs above `<exe>` is not the payload's
  and is not claimed. It returns a `PayloadPathFinder` for that virtual directory, and claims
  the directories that the scripts root shows next to the executable as well.
- **`PayloadPathFinder.find_spec`** looks the dotted name up with `_fsbpyr.find_module`. A
  regular module or package gets a `ModuleSpec` whose loader is `PayloadLoader`, whose origin is
  `<exe>/<path>`, and whose `loader_state` is the module record; a package's `__path__` is built
  from its name. A namespace record becomes a portion for `PathFinder` to assemble; an
  extension module goes to `_fsbpyr_rt.native`. A `sys.path` entry below the root, such as
  `<exe>/pkg/_vendor`, makes `vendored` find `pkg._vendor.vendored` (`AliasLoader`), and
  `pkg.__init__` imported as a module of its own runs the package's `__init__` record again
  (`InitLoader`).
- **`PayloadLoader.exec_module`** reads the code blob with `_fsbpyr.read`, which decompresses
  it, so the bytes `marshal` sees have passed the zstd checksum; it sets `co_filename` to the
  absolute virtual path and executes the code. It applies the fixups after `ssl`,
  `multiprocessing.spawn`, `ctypes`, `parso.file_io`, `cv2.load_config_py3` and, in an app with
  scripts on 3.14 and later, `argparse` have run. The loader also serves `get_source`,
  `get_filename`, `is_package`, `get_data` (the content of a payload file named by its path
  below the executable, which `pkgutil.get_data` uses) and `get_resource_reader`.
- **`PayloadMetaFinder`**, ahead of `PathFinder`, handles what `PathFinder` cannot: namespace
  packages of the payload (CPython's own namespace reader only understands real directories
  and zip files), merged with portions found on `sys.path` as a venv would, with
  `PayloadNamespaceLoader` for `importlib.resources`; `distutils` as setuptools'
  `_distutils_hack` provides it, when the payload has `setuptools._distutils`; and
  `find_distributions` for `importlib.metadata`.
- **Packages that need real files.** A package flagged FS_REQUIRED_ROOT (one in `fs-packages`,
  or one the build knows needs it) is extracted on its first import: every file below its
  directory goes to the native cache, and its modules get `__file__` and `__path__` there,
  while their code still comes from the payload. The path hook then also claims that
  directory.

### The scripts root

A project without a build system, a directory of scripts and a single script are packaged
below the reserved top-level package `_fsbpyr_scripts` ([Build modules](Dev-Build-Modules.md)). A
module of it appears next to the executable, not inside it: `__file__` and `spec.origin` are
`<directory of the executable>/<path below the root>` (`_shown`), as for a script run by path,
so the files a script reads next to its `__file__` (configuration, state, logs) are the host's,
deployed beside the executable (D24). The code is still the payload's:

- the path hook also claims a directory next to the executable whose path below it is a package
  or namespace directory of the scripts root (`_overlay_finder`, with `.` and `..` resolved as
  above), and serves it from the payload, so a stale `.py` copy beside the executable never
  shadows the packaged module; the host's other files there are not looked at;
- `PayloadLoader.get_data` maps a path below the executable's directory back into the scripts
  root, so `loader.get_data(__file__)` reads the packaged source;
- code objects keep the payload's file name (`<exe>/_fsbpyr_scripts/<path>`), so a traceback
  shows the packaged source lines.

A file or directory below the root whose name is no Python name is stored under a mangled name
(`check-disk` as `check_disk.py`, `sub-dir/` as `sub_dir/`: each character an identifier cannot
hold becomes `_`, and a name that starts with a digit gets a `_` in front); `__file__` shows the
stored name, and the finder of an entry that uses the name on disk
(`<exe>/_fsbpyr_scripts/sub-dir`, or `sub-dir` next to the executable) looks its modules up
under the mangled one (`_module_part`, the rule of `loose._module_stem`). A script target runs
with its directory, as `__file__` shows it, first on `sys.path`. The modules of a packaged
project are not affected: their `__file__` stays below the executable.

## Uncaught exceptions

When an uncaught exception ends the app, the boot module's `sys.excepthook` hides the
launcher's and its own frames at the top of the traceback. For an `OSError` anywhere in the
exception's chain on a path below the executable, it adds a note that says what applies to that
path (D24; see [Compatibility](Dev-Compatibility.md)):

- a path below a regular package of the payload names the outermost regular package (one with
  an `__init__.py`) on it: `fsbpyr: <path> is inside the executable; <package> reads files next
  to its __file__: rebuild with --fs-package <package>`;
- a path below namespace directories with no regular package on it says that `--fs-package`
  takes regular packages only, and to add an `__init__.py` to the package that reads the file
  or to read it from a path outside the executable;
- the path of a top-level module or file (`<exe>/x.conf`) says that a top-level module's
  `__file__` is a path in the executable and `--fs-package` does not apply: read the file from
  a path outside the executable;
- a path below the scripts root says that the executable holds the project's Python files only
  and that the file is read next to the executable, at the path it names, where it is to be
  deployed.

For a `FileNotFoundError` on a path below the native cache's `site` tree, once the process has
its native cache (`native.loaded_site()`, as after an extension's import), the note names the
outermost regular package on the path: `fsbpyr: <path> is missing from the native cache, which
holds the compiled files of a package only; <package> reads files next to its __file__: rebuild
with --fs-package <package>`. The case the docstring of `_site_note` names is a compiled
package that reads files next to its `__file__`, such as mypyc's `blib2to3`, which reads
`Grammar.txt`. A path with no regular package on it gets no note.

For a `FileNotFoundError` on a path in a directory that the scripts root shows next to the
executable, the note is `fsbpyr: <path> is missing; the files a script reads next to its
__file__ are read next to the executable, from <directory>/: deploy it there`.

On Python 3.15 and later, when the chain holds a `ModuleNotFoundError` for a module outside the
standard library, the hook prints the traceback with `sys.flags.no_site` reading 0, so that
CPython's hint for `no_site` ("did you forget to enable your virtual environment?"), which
misleads in an app that never imports `site`, is left out, and then adds `fsbpyr: <name> is not
in the executable: add it to the project's dependencies and rebuild`. A missing standard library
module is left to CPython's own wording. None of this costs anything until an exception goes
uncaught, and none of it happens when the app catches the error or has replaced
`sys.excepthook`.

## Native code and the cache

Extension modules and the shared libraries they link cannot be loaded from inside a file, so
`_fsbpyr_rt.native` puts them into the app's cache on first import and lets CPython's
`ExtensionFileLoader` load them from there with `dlopen`. The cache is `$FSBPYR_CACHE_DIR`
(made absolute), else `${XDG_CACHE_HOME:-~/.cache}/<app>` (a relative `XDG_CACHE_HOME` is
ignored). `~` is `$HOME` when that is an absolute path; when `HOME` is unset, empty or relative
it is the home directory of the user's passwd entry (`native.home_directory`), so a service
started without `HOME` finds its cache. The launcher's `due.c` and the tool's cache
([Runtime modules](Dev-Runtime-Modules.md)) use the same rule. With no absolute path either way,
the import fails with `cannot locate a cache directory; set FSBPYR_CACHE_DIR`. Below the
cache:

| Tree | Content | Named by |
|---|---|---|
| `runtime/<generation>/site/` | native files, FS_REQUIRED packages | the first 32 hex digits of the payload's `generation_id`: the runtime tag and every extracted file's path, content and mode |
| `resources/<id>/site/` | resources asked for as paths | the `resources` key of `build_info`: every payload file's path, content and mode |

The `site` trees mirror site-packages, so the `$ORIGIN` run paths of bundled libraries resolve
unchanged. The extraction itself is the launcher's `_fsbpyr.sync`: a new file is written
unnamed (`O_TMPFILE`) and linked into place once complete, and a replacement (or a file on a
file system without `O_TMPFILE`) is written under a temporary name in `.scratch` and renamed,
so racing processes see only complete files and need no lock ([Launcher](Dev-Launcher.md)). Each
process hashes every cached file once, before it first uses it, against the payload's digest,
restoring a changed one. The first sync into a tree holds that tree's lock for the
process's life, which keeps the collector away from it.

An extension's spec has its origin in the cache and a `PayloadExtensionLoader`, which extracts
the extension's file and its closure (the bundled libraries it needs) before loading it. A failed
load gets a hint when the cache is on a `noexec` mount, or, when the message names a missing
glibc or libstdc++ symbol version or a shared object that cannot be opened, the glibc the build
required and the one the system has. An error that the extension's own initialization raises,
such as the `OSError` of a compiled package that reads a file, is passed on as it is.

A refused cache directory is a refused start, as the launcher reports one (D41). `_fsbpyr.sync`
refuses a cache directory that belongs to another user, or one on the way to it that other
users may write to. Wherever the boot code extracts (an extension, an FS_REQUIRED package, a
bundled library for `ctypes`, a resource asked for as a path, the extensions the natives
self-test checks), `NativeCache.extract` then writes `fsbpyr: cannot start <executable>:
refusing cache directory <path>: other users may write to it` (or `owned by another user`) to
stderr and ends the process with exit 70. It flushes stdout and stderr first and leaves
through `os._exit`, since the refusal may arise in a thread or under code that catches
`SystemExit`; `atexit` handlers do not run. A start that extracts nothing is
not affected. Any other failure to extract stays an error the app sees: a cache that cannot be
written makes the import of an extension or an FS_REQUIRED package fail with an `ImportError`
(`_fsbpyr.CacheError`) that says to set `FSBPYR_CACHE_DIR`, and a resource asked for as a path
with an `OSError` that says the same.

## Resources and metadata

`importlib.resources.files()` on a payload package returns a `PayloadTraversable`
(`_fsbpyr_rt.resources`): a read-only view of the payload tree. Reading a file returns its
content from the payload; opening one reads it whole up to 16 MiB and streams larger ones
through `_fsbpyr.stream`. A resource asked for as a path (`os.fspath`, `str`,
`importlib.resources.as_file`) is extracted into the resources tree and stays there, as an
installed package's files do, so code that keeps the path after `as_file` returns keeps
working. FS_REQUIRED packages get a `DirectoryReader` over their extracted directory instead.

`importlib.metadata` finds the payload's distributions through
`PayloadMetaFinder.find_distributions` (`_fsbpyr_rt.metadata`): a name lookup uses the sorted
DISTS table, and a `PayloadDistribution` reads its dist-info files from the payload.

## Children through `sys.executable`

In an executable, `sys.executable` is the app. Two kinds of children start it again:

- **multiprocessing** (`_fsbpyr_rt.mp`): spawn, forkserver and the resource tracker run
  `<exe> [flags] -c <code> [args]`. The boot module recognizes exactly these command lines
  (forkserver's per Python version), decodes their arguments with `ast.literal_eval` (the
  command line is never executed), and calls the function they name. The child runs only when
  its parent process runs this very executable with the same real and effective user and group
  ids, its descriptors are not standard input, output or error, and it asks for nothing an
  fsbpyr parent never sends (a `main_path`, unexpected keywords). A command line that does not
  match exactly is not taken for a multiprocessing child; a matching one whose parent is not
  this executable is refused with a message. A forkserver asked to preload `__main__` imports
  the module of the app's `module:attr` entry point instead, as a venv's forkserver imports the
  console script; in an executable with several commands it takes the command its parent runs,
  read from the parent's command line (`/proc/<ppid>/cmdline`) by the rule of `commands`.
- **helper interpreters** (`_fsbpyr_rt.reexec`): `<exe> [flags] -m MODULE`, `-c CODE`, or a
  `.py` file below `<exe>/`; in an app with scripts, also a `.py` file below the executable's
  directory, the path a script's `__file__` gives. Only a child whose parent runs this
  executable counts; a shell, a script or a sudo rule gets the app with these arguments. What
  the child gets depends on `interpreter_reexec` in `build_info`: `true` runs the module, code
  or script as CPython would (interpreter flags accepted and ignored); `false` runs the app;
  not set refuses, with exit 70 and a message naming `--interpreter-reexec` and
  `--no-interpreter-reexec`, a child that looks like Python (a dotted name after `-m`, code
  after `-c` that is more than a name, attribute or constant, or a payload script), and runs
  the app otherwise. Unless the setting is `false`, such a child that names a script which is
  the target of one of the app's own commands runs that command: a script that starts a
  sibling as `subprocess.run([sys.executable, <sibling's __file__>, ...])` gets it, as
  `python sibling.py` would. A script runs with `sys.argv` `[path, *args]` and its directory
  first on `sys.path`. A path below the executable's directory that is no script of the payload
  is a file of the host, an argument of the app, which runs. Only the first argument after the
  interpreter flags is looked at, and a start through a symlink named for a command never gets
  here ([Several commands in one executable](#several-commands-in-one-executable)).

The `multiprocessing.spawn` fixup sets the executable children start from to `/proc/self/exe`,
so parent and children never run different versions of the file, and the parent check keeps
passing after the file was replaced. The User Guide describes helper interpreters for users
under [`interpreter-reexec`](User-Settings.md#interpreter-reexec).

## Services and containers

Three policies serve apps run by systemd, in containers or from cron:

- **`PYTHONUNBUFFERED`.** Set and not empty, it makes the launcher configure stdout and stderr
  unbuffered, as CPython does, so a service's lines reach a pipe as they are printed.
- **`JOURNAL_STREAM`.** systemd sets it to the `<device>:<inode>` (decimal) of the journal's
  stream for a service whose stdout or stderr go to the journal. Each of stdout and stderr that
  is that file is made line-buffered (`service.line_buffer_journal`), so each line reaches the
  journal when it is printed, unless the stream is unbuffered already. A value that does not
  parse, or names another file, changes nothing.
- **Process 1.** The kernel drops a signal that process 1 has no handler for, so an app that is
  process 1 (a container without an init) would never die of SIGTERM, as `python app.py` does
  not. While SIGTERM still has its default disposition, `service.terminate_on_sigterm`
  installs a handler that raises `SystemExit(143)` (128 + 15). A handler the app installs
  afterwards replaces it, and a forked child gets the default disposition back
  (`os.register_at_fork`), so a worker in a long C call still dies of the signal.

The boot module looks up `JOURNAL_STREAM` and the process id at every start and imports
`_fsbpyr_rt.service` only when one of them applies.

## Modules

Imports below use the names inside the executable (`_fsbpyr_rt.<name>`); the module graph lists
them as `fsbpyr.boot._fsbpyr_rt.<name>`. Besides them, `_fsbpyr_boot`, `native`, `resources`,
`metadata`, `selftest` and `trace` import the launcher's built-in `_fsbpyr` at module level and
`fixups` inside one function; `gc`, `mp`, `reexec`, `commands`, `links`, `restart` and `service`
do not use it.

### `_fsbpyr_boot`

[`src/fsbpyr/boot/_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py)

The bootstrap of every executable: the import machinery, the start-up decisions and the
handling of uncaught exceptions described above. It holds the executable's `info()` as `INFO`
and its path as `ROOT`. `PayloadLoader.exec_module` carries the file name
`<frozen importlib._bootstrap_fsbpyr>`, so that `warnings.warn` with a `stacklevel` skips it as
it skips importlib's own frames and names the importing module. Whether the app has scripts
(an entry point that names `_fsbpyr_scripts.`) is decided once, at import: only then does the
helper-interpreter check look below the executable's directory, and only then, on 3.14 and
later, is `argparse` adjusted.

Classes:

- `PayloadLoader(prefix=<exe>/)`: loads modules and packages from their payload records;
  `prefix` is where they appear to live (the executable, or the cache for an FS_REQUIRED
  package; modules of the scripts root appear next to the executable).
- `AliasLoader(prefix, public, payload)`: a module imported under a name other than its payload
  name (a `sys.path` entry below the root).
- `InitLoader`: `pkg.__init__` imported as a module of its own; not a package.
- `PayloadPathFinder(directory, location=<exe>/)`: the path-entry finder of one virtual
  directory (below the scripts root, a directory named as on disk is looked up under its
  mangled name); also `iter_modules` for `pkgutil` (modules and regular packages, not
  namespace directories).
- `PayloadNamespaceLoader`: namespace packages of the payload, with a resource reader.
- `SetuptoolsDistutilsLoader`: `distutils` as the module setuptools ships.
- `PayloadMetaFinder`: the meta-path finder for namespace packages, `distutils` and
  distributions.
- `_FlagsWithSite` (private): `sys.flags` with `no_site` reading 0, while a traceback prints on
  3.15.

Public functions:

- `path_hook(entry)`: the path hook described above; `ImportError` for any other entry.
- `run()`: the start-up sequence.

Rules it enforces: module-level imports only of built-in and frozen modules; an entry point
`module:attr` must be callable; a `sys.path` entry that climbs above the executable is not
claimed; the self-test never imports a script of the scripts root; the links are never made by
the collection child.

Imports: `_fsbpyr_rt.native`, `_fsbpyr_rt.mp`, `_fsbpyr_rt.restart`, `_fsbpyr_rt.reexec`,
`_fsbpyr_rt.resources`, `_fsbpyr_rt.metadata`, `_fsbpyr_rt.commands`, `_fsbpyr_rt.service`,
`_fsbpyr_rt.links`, `_fsbpyr_rt.trace`, `_fsbpyr_rt.selftest`, `_fsbpyr_rt.fixups` (all lazy).
Imported by: none in the package graph; the launcher imports it by name.

### `native`

[`src/fsbpyr/boot/_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py)

Native extensions, FS_REQUIRED packages and resources on disk: the cache's location and trees,
the extension loader, and the extraction requests that `_fsbpyr.sync` carries out, as
described in [Native code and the cache](#native-code-and-the-cache). Each process remembers
which payload files it has checked (a bit per file) and which resource paths it has extracted,
and keeps the list of paths it wrote only when `FSBPYR_TRACE` is set. `REFUSED` is 70, the
launcher's exit status for a refused start.

Classes:

- `NativeCache(root, *chain)`: one tree of the cache (`root`, `site`, and `tree`, the
  `runtime/<generation>` or `resources/<id>` part); `sync(files, resources=False)` extracts or
  checks the given payload files and returns how many it wrote; `extract(files,
  resources=False)` is `sync` for the import machinery, the resources API and the natives
  self-test, where a refused cache directory ends the start (`refuse`). The extension loader,
  `materialize_package`, `materialize`, `bundled_library` and `selftest.natives` use
  `extract`.
- `PayloadExtensionLoader(name, path, files)`: an `ExtensionFileLoader` that extracts the
  extension and its closure before it loads it, with the error hints above.

Public functions:

- `refuse(exc)`: the refused start: the line on stderr and exit 70; it does not return.
- `home_directory(environ)`: `$HOME` if it is absolute, else the passwd entry's home
  directory, `""` when there is none.
- `cache_directory(app, environ)`: the app's cache root; a corrupt app name, or no absolute
  base, is an error.
- `native_cache()`: this executable's `runtime/<generation>` tree, computed once without
  touching the file system.
- `loaded_site()`: the `site` directory of that tree once it has been computed in this process,
  else `None`; the uncaught-exception note for a missing file below it uses it.
- `resource_cache()`: the `resources/<id>` tree; the id must be 32 alphanumeric characters
  from `build_info`.
- `materialize_package(directory)`: extract an FS_REQUIRED package: every file below its
  directory, the closures of its extensions (libraries outside the directory, such as
  `torchvision.libs`), and every shared library its distributions ship (plugins opened from a
  directory, such as openmm's); returns the prefix its modules live under.
- `materialize(path)`: extract a payload file or directory for `importlib.resources`; returns
  where it is on disk.
- `bundled_library(name, caller=None)`: where the one bundled shared object of that file name
  is on disk, extracted with what it needs; of several, the one in the caller's top-level
  package; `None` if no single file qualifies.
- `extension_spec(fullname, record, prefix)`: the spec of an extension module (a package when
  its file is a compiled `__init__`).
- `collect(quiet)`: `FSBPYR_GC`: collect this app's cache, never removing this build's own
  trees; exit status 1 if anything could not be removed.

Rules it enforces: an app name must be a single path component; the payload root is never
extracted as a resource; an extension record must point at a NATIVE_EXT file; a refused cache
directory ends the start with exit 70, other extraction failures are errors.

Imports: `_fsbpyr_rt.gc` (lazy).
Imported by: `_fsbpyr_boot`, `_fsbpyr_rt.fixups`, `_fsbpyr_rt.resources`,
`_fsbpyr_rt.selftest`.

### `resources`

[`src/fsbpyr/boot/_fsbpyr_rt/resources.py`](../src/fsbpyr/boot/_fsbpyr_rt/resources.py)

`importlib.resources` over payload directories, as described in
[Resources and metadata](#resources-and-metadata). It registers its own `as_file` for
`PayloadTraversable`, which yields the extracted path instead of a temporary copy.

Classes:

- `PayloadTraversable(root, path)`: a file or directory of the payload tree; `iterdir`,
  `joinpath`, `is_file`, `is_dir`, `read_bytes`, `read_text`, `open`, and `__fspath__`, which
  extracts. `str()` falls back to the virtual path, with a warning, when the cache cannot be
  used; a refused cache directory ends the start there too.
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

`importlib.metadata` over the dist-info directories stored in the payload. Distributions are
found only when the executable is on the search path of the request (by default `sys.path`).

Classes:

- `PayloadDistribution(root, dist_info)`: `read_text` from the dist-info directory in the
  payload, `locate_file` as a `PayloadTraversable`.

Public functions:

- `find_distributions(root, context=None)`: every payload distribution, or the one whose
  normalized name matches.

Imports: `_fsbpyr_rt.resources` (top).
Imported by: `_fsbpyr_boot`.

### `commands`

[`src/fsbpyr/boot/_fsbpyr_rt/commands.py`](../src/fsbpyr/boot/_fsbpyr_rt/commands.py)

The table of commands of an executable with several commands, as described in
[Several commands in one executable](#several-commands-in-one-executable). `EXIT_USAGE` is 2.

Public functions:

- `parse(table)`: the command names with their targets, in the table's order.
- `named(commands, argv0)`: the command the basename of `argv0` names, as it is or without
  `.py`; `None` if neither.
- `is_sibling(commands, argv0, executable)`: whether `argv0` names a command under another
  name than the executable's own.
- `find(commands, argv)`: `(target, shift)` for the command `argv` starts, `shift` saying
  whether `argv` loses its first item; `None` if it starts none.
- `choose(commands, argv)`: the target of the command `argv` starts, shifting `argv` for
  `<exe> COMMAND`; without a command, the usage on stderr and exit 2.

Rules it enforces: a command is named exactly, by `argv[0]`'s basename (with or without `.py`)
or by `argv[1]`.

Imports: none.
Imported by: `_fsbpyr_boot`, `_fsbpyr_rt.links`, `_fsbpyr_rt.mp`; in the tool, `fsbpyr.build`,
`fsbpyr.classify`, `fsbpyr.inspection`.

### `links`

[`src/fsbpyr/boot/_fsbpyr_rt/links.py`](../src/fsbpyr/boot/_fsbpyr_rt/links.py)

`FSBPYR_INSTALL_LINKS=DIR app` makes in `DIR` a symlink per command that names the executable's
real path (absolute, so `DIR` can be anywhere), prints `fsbpyr: linked <path> -> <real path>`
(or `present` for one that is there already) for each, and exits without running the app. The
commands are those of the table, or, for a single command, the app's name. A command whose
target is a script of the scripts root without an attribute also gets `<name>.py`, so a
crontab, unit or monitoring line that runs `DIR/name.py` keeps working (a start strips `.py`).
Running it again changes nothing: a link that names this executable, or the executable itself,
is left as it is. Anything else in the way (a file, a directory, a link to something else) is
never replaced: it is reported, the other links are still made, and the exit status is 1. A
`DIR` that is not a directory or cannot be written is one error before any link, status 1.
There is no command-line form: under a `sudo <binary> *` rule a caller could make root-owned
links anywhere, and sudo's `env_reset`, a default, drops `FSBPYR_*`.

Public functions:

- `names(entrypoint, app_name)`: the link names.
- `install(directory, executable, entrypoint, app_name)`: make the links; the exit status.

Rules it enforces: a name with a directory part, `.` or `..` gets no link (status 1); nothing in
the way is replaced.

Imports: `_fsbpyr_rt.commands` (top).
Imported by: `_fsbpyr_boot`.

### `restart`

[`src/fsbpyr/boot/_fsbpyr_rt/restart.py`](../src/fsbpyr/boot/_fsbpyr_rt/restart.py)

The `[sys.executable] + sys.argv` restart idiom, as described in
[`sys.argv[0]` and the restart idiom](#sysargv0-and-the-restart-idiom). It imports only the
built-in `posix`.

Public functions:

- `after_flags(argv)`: the index of the first argument after interpreter flags (`-X` and `-W`
  take a value), or `None` if `-` or nothing follows them; `reexec` uses it too.
- `restart_argv(argv)`: `argv` from the argument that names this executable on, or `None` if
  the start is no restart.

Rules it enforces: only interpreter flags (`-B`, `-b`, `-bb`, `-d`, `-E`, `-I`, `-O`, `-OO`,
`-P`, `-q`, `-R`, `-s`, `-S`, `-u`, `-v`, `-vv`, and `-X` or `-W` with their value) may precede
the argument, which must be the same file as `/proc/self/exe`; nothing is imported, run or
opened from the arguments (D44).

Imports: none.
Imported by: `_fsbpyr_boot`, `_fsbpyr_rt.reexec`.

### `service`

[`src/fsbpyr/boot/_fsbpyr_rt/service.py`](../src/fsbpyr/boot/_fsbpyr_rt/service.py)

The start-up policies for `JOURNAL_STREAM` and process 1, as described in
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

The multiprocessing children, as described in
[Children through `sys.executable`](#children-through-sysexecutable). It knows the forkserver
command line of 3.12 and of 3.13 and later, and the keywords each version may send
(`authkey_r` from 3.14, `on_error` from 3.15). The forkserver's `sys_path` is dropped: it keeps
the payload-only `sys.path`.

Public functions:

- `child_command(argv)`: the child `argv` asks for, as `(module, function, arguments,
  keywords, sys.argv)`, or `None` for a normal run.
- `preload_names(arguments)`: the module names a forkserver command preloads.
- `run_child(argv, entrypoint="")`: run the child; `False` if `argv` asks for none.
  `entrypoint` is the app's own, `module:attr`, `module` or a table of commands.

Rules it enforces: an exact match of the command line; descriptors of at least 3; known keywords
only, no `main_path`, an `on_error` of `ignore`, `warn` or `fail`; arguments that are literals;
the parent process running the same executable (same device and inode of `/proc/<pid>/exe`)
with the same real and effective ids, any doubt counting as no.

Imports: `_fsbpyr_rt.commands` (lazy).
Imported by: `_fsbpyr_boot`, `_fsbpyr_rt.reexec`.

### `reexec`

[`src/fsbpyr/boot/_fsbpyr_rt/reexec.py`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py)

Helper interpreters, as described in
[Children through `sys.executable`](#children-through-sysexecutable). A payload script runs as
the module whose file it is (a package's `__main__` as the package), through `runpy`, with its
directory first on `sys.path`; it is named by its path inside the executable or, for the
scripts root, by the path its `__file__` shows next to the executable. `-m` runs through
`runpy` too, and `-c` executes the code in `__main__`. `sys.argv` is left as CPython would leave
it. `REFUSED` is 70.

Public functions:

- `child_command(argv, prefix=None, overlay=None)`: `("-m", module, args)`, `("-c", code, args)`
  or `("script", path, args)` after interpreter flags, or `None`; `overlay` (the executable's
  directory, in an app with scripts) also names scripts.
- `asks_for_python(command)`: whether the command looks like Python rather than the app's own
  options.
- `refusal(command, prefix)`: the refusal message.
- `run_child(argv, build_info, prefix, targets=(), overlay=None)`: run, refuse (exit 70), or
  return `False` for the app; `targets` are the targets of the app's commands.

Rules it enforces: only a child of this very executable is ever run as Python, and the parent is
checked before anything of a script's packages is imported; without the setting such a child is
refused with exit 70, unless it is a script that is one of the app's own commands; a script must
be a module file of the payload whose spec has that path as its origin.

Imports: `_fsbpyr_rt.mp`, `_fsbpyr_rt.restart` (top).
Imported by: `_fsbpyr_boot`.

### `fixups`

[`src/fsbpyr/boot/_fsbpyr_rt/fixups.py`](../src/fsbpyr/boot/_fsbpyr_rt/fixups.py)

Adjustments to particular modules, applied by `PayloadLoader` right after the module's code has
run:

| Module | Adjustment |
|---|---|
| `ssl` | When OpenSSL's defaults (PBS builds it with `/etc/ssl`) find no trust store, neither `/etc/ssl/cert.pem` nor hashed links in `/etc/ssl/certs`, and no `SSL_CERT_FILE` or `SSL_CERT_DIR` is set, `SSLContext.set_default_verify_paths` also loads the first readable system bundle of `SYSTEM_BUNDLES` (`/etc/ssl/certs/ca-certificates.crt`, `/etc/ssl/certs/ca-bundle.crt`, `/etc/pki/tls/certs/ca-bundle.crt`, `/etc/ssl/ca-bundle.pem`), unless the app has set those variables meanwhile. |
| `multiprocessing.spawn` | Children start from `/proc/self/exe`. |
| `ctypes` | `CDLL` of a bare file name that the system cannot load falls back to the app's bundled library of that name (`native.bundled_library`), the one in the calling module's top-level package when there are several. |
| `parso.file_io` | The modification time of a payload file is the executable's, where parso would get `NotADirectoryError`. |
| `cv2.load_config_py3` | Sets `sys.OpenCV_REPLACE_SYS_PATH_0`, so that cv2's loader inserts its directory at `sys.path[0]` and does not find the cv2 package again. |
| `argparse` | Only in an app with scripts, on 3.14 and later: when `__main__` is a module of the scripts root whose origin is `sys.argv[0]` (a script started by the path its `__file__` names, such as a `name.py` link beside the executable), the default program name is the file's name, where 3.14.9 and 3.15 would print `<exe> -m <module>` (the comment in the source). |

Public functions:

- `fallback_bundle(environ)`: the system CA bundle to add, or `None`.
- `apply(module)`: run the fixup of a module named in `FIXUPS`.

Imports: `_fsbpyr_rt.native` (lazy).
Imported by: `_fsbpyr_boot`.

### `gc`

[`src/fsbpyr/boot/_fsbpyr_rt/gc.py`](../src/fsbpyr/boot/_fsbpyr_rt/gc.py)

The cache collector, standard library only: an app runs it with `FSBPYR_GC`, and the tool runs
it for `fsbpyr cache gc` and for its own cache (`runtime/collect.py`, same rules). Its module
docstring states the rules:

- Only a root with the marker `.fsbpyr-cache` is collected. The marker is written (`mark`, and
  the launcher's `cache.c` alike) only into a root that holds nothing but fsbpyr's names, as a
  new root does; a directory of the user's is never marked.
- Every tree (`runtime/<32 hex>`, `resources/<32 hex>`) has a `.lock` that each process using
  it holds shared for its whole life (taken by the launcher's sync), and whose modification
  time, touched once a day, is the tree's last use.
- A run holds `.gc` exclusively (one run at a time; its time is when the last run started). For
  each tree it takes the lock exclusively, failing when the tree is in use; a tree written
  before 0.13.0, which has no lock, gets one dated by the tree's own time. A tree unused for 7
  days, or any tree not in use when the file system has less than 10% free, is renamed into
  `.trash` while locked; the trash is emptied at the end of the run, never following a
  symlink. Files a killed process left in a kept tree's `.scratch` (named `.fsbpyr-*`, held by
  nobody, an hour old) go too.
- A tree that cannot be removed is reported and the run goes on; nothing else is touched.
- `way_problem` applies the launcher's rule for the way to a cache (`way.h`): every directory
  from `/` owned by root or the user and writable by no other user unless sticky, symlinks
  root's or the user's, with the exception for owners outside the user namespace.

Classes:

- `Report`: what a run did (removed, temporary files removed, kept, errors; not marked; busy;
  under pressure), with `text()`.
- `Run(root, trash, now, report)`: one run over a root; `attempt`, `unused`, `take`, `discard`,
  `listed`, `empty_trash`.
- `Problem(where, error, reason)`: what makes a way untrustworthy, with `text()`.
- `_Way` (private): one walk of a path from `/`, following symlinks as the kernel does.

Public functions:

- `collect(root, keep=frozenset(), now=None, pressure=None)`: collect an app cache, never
  removing the trees in `keep`.
- `run(root, stamp, trash, work, now=None, pressure=None)`: the frame of every run: the way
  checked, the marker required, the stamp held, `work` called, the trash emptied.
- `way_problem(path)`: the first problem on the way to `path`, or `None`.
- `short_of_space(where)`: whether the file system has less than 10% free.
- `ours(directory, name, kind)`: whether an entry is of a kind and owned by the user.
- `mark(root, new=True)`: write the marker if the root qualifies; whether it is marked.
- `exclusive(directory, name, create=False)`: a context manager holding a lock file
  exclusively, or yielding `None`.
- `remove(parent, name)`: remove a tree without following symlinks, holding at most three
  descriptors whatever its depth.

Rules it enforces: those listed above.

Imports: none.
Imported by: `_fsbpyr_rt.native` (lazy); in the tool, `fsbpyr.cli`, `fsbpyr.runtime.collect`,
`fsbpyr.runtime.hold`, `fsbpyr.runtime.registry`.

### `selftest`

[`src/fsbpyr/boot/_fsbpyr_rt/selftest.py`](../src/fsbpyr/boot/_fsbpyr_rt/selftest.py)

`FSBPYR_SELFTEST=natives`: checks, before deploying, that the machine provides what every
native extension needs. Each extension and its closure are put into the cache, then the dynamic
loader runs in trace mode on it (`LD_TRACE_LOADED_OBJECTS=1`, what `ldd` does), resolving
libraries and symbol versions without running the extension's initialization. The loader is
the executable's own program interpreter (`PT_INTERP`), the one of the target the app was built
for. Only glibc's trace output is understood.

Public functions:

- `interpreter(header)`: the program interpreter an ELF64 little-endian file names, or `None`.
- `missing(trace)`: the missing libraries and symbol versions in the loader's output.
- `natives(loader=None)`: one line on stderr per missing item with the extensions that need
  it and exit status 1, or a line on stdout saying all extensions load; a refused cache
  directory ends it as it ends a run (`native.refuse`: the line on stderr and exit 70).

Imports: `_fsbpyr_rt.native` (top).
Imported by: `_fsbpyr_boot`.

### `trace`

[`src/fsbpyr/boot/_fsbpyr_rt/trace.py`](../src/fsbpyr/boot/_fsbpyr_rt/trace.py)

`FSBPYR_TRACE`: at exit, one line on stderr, `fsbpyr-trace ` followed by JSON: the modules
loaded from the payload, grouped by distribution (`""` for those without one, boot modules
left out), the paths extracted into the cache, and `_fsbpyr.stats()`: the payload read counters
and the nanoseconds the launcher's whole-file integrity check took.

Public functions:

- `summary()`: the report as a dictionary.
- `report()`: print it.

Imports: none.
Imported by: `_fsbpyr_boot`.

## Tests

The tests in [`tests/unit/boot/`](../tests/unit/boot)
run the boot module in-process against a pure-Python stand-in of `_fsbpyr`
([`tests/support/standin.py`](../tests/support/standin.py),
built on the payload reader); the same contract battery,
[`tests/support/contract.py`](../tests/support/contract.py),
runs against the stand-in and, inside a built executable, against the C module, and both must
give the same results. [Testing](Dev-Testing.md) describes the suite.

## See also

- [Launcher](Dev-Launcher.md): what happens before `run()`, and `_fsbpyr.sync`.
- [Payload modules](Dev-Payload-Modules.md) and [Executable format](Dev-Executable-Format.md): the
  records the boot code reads, and the entry point's table of commands.
- [Build modules](Dev-Build-Modules.md): the builds that make several commands and the scripts
  root.
- [Runtime modules](Dev-Runtime-Modules.md): the tool's use of `gc`.
- [Compatibility](Dev-Compatibility.md): what differs from a venv.
- [Security](Dev-Security.md): the cache's trust rules and the child checks.
