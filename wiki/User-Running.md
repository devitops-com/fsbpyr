# Running a built app

A built app is an ordinary executable: copy it to the target machine and run it. This page
covers what happens when it starts, where it keeps its cache, the environment variables it
reads, how it behaves as a service, how to make the command links on a target, how to check a
target machine, and how it differs from running the same project in a virtual environment.

## What a start does

1. **Integrity check.** The executable hashes its whole file and refuses to start if a single
   byte changed:
   ```
   fsbpyr: cannot start /usr/local/bin/app: integrity check failed: the file was modified or damaged
   ```
   Before Python starts it also refuses a privileged start ([below](#refusals-exit-70)), takes
   one of the app's instance slots (`FSBPYR_MAX_INSTANCES`), and starts a cache collection in
   the background when one is due ([The cache](#the-cache)).
2. **Python starts** from the executable: the embedded CPython, isolated from the machine's
   Python (the `PYTHON*` variables are ignored except `PYTHONUNBUFFERED`, there is no `site`
   import, UTF-8 mode is on).
3. **The command is chosen.** An executable of several commands runs the one its name says,
   else the one its first argument names ([Building](User-Building.md#several-commands-in-one-executable)).
4. **Modules are imported straight from the file.** Pure-Python modules run from memory; they
   are not extracted.
5. **Native code is extracted on first use** into the app's cache and loaded from there by the
   system's loader. Later starts reuse it.
6. The entry point runs: a console script's function, or a script as `__main__`.

`sys.argv[0]` is the full path the app was started by: `app` found on `PATH` gives
`/usr/local/bin/app`, and `./app` gives `./app` made absolute against the working directory
(where a venv's script keeps `./app`). A start through `exec -a other app` keeps `other`.

The `pandas-csv` app from [Building an app](User-Building.md), traced with `FSBPYR_TRACE=1` on an
empty cache and then again (the columns are the trace's keys):

| Run | `extracted` | `decompressed_bytes` | `stored_bytes` | `integrity_ns` |
|---|---|---|---|---|
| first | 57 files | 77,021,875 | 22,465,859 | 6,125,008 (6.1 ms) |
| second | 0 files | 18,458,502 | 6,959,611 | 5,136,828 (5.1 ms) |

(One run each on a 24-core x86-64 machine with the file in the page cache, fsbpyr 0.27.0, while other builds ran. The
project's own measurements, release by release, are in [Performance](Dev-Performance.md).)

## Integrity

The check uses SHA-256 on up to 8 threads, never more than the cores the process may use. Files
extracted to the cache are hashed again by every process, once, before it first uses them, with
the same digest; a changed one is reported and restored.

The digest can be checked from outside with `fsbpyr inspect --verify BINARY`, or with standard
tools:

```sh
head -c -32 app | split -b 1M --filter='openssl dgst -sha256 -binary' | sha256sum
```

What the check cannot stop is someone who can write the file and rewrites the digest too, or
patches the check out: the code that checks is in the same file. To prove where an app came
from, attest it when you build it (for example with GitHub's `attest-build-provenance` action)
and verify the attestation where it runs.
[`.github/workflows/attest-example.yml`](../.github/workflows/attest-example.yml)
is a workflow that builds an app and attests it.

## The cache

Native extensions and shared libraries, the packages in `fs-packages`, and resources an app asks
for as a file path are extracted on first use into

- `$FSBPYR_CACHE_DIR`, when set, or else
- `${XDG_CACHE_HOME:-~/.cache}/<app>`. When `$HOME` is unset, empty or relative (a service
  without `User=`, say), `~` is the home directory of the user's passwd entry.

Since what is extracted there is loaded as code, the cache must be safe from other users: the
directory must be yours, and it and every directory leading to it must be owned by root or by
you and writable by no other user (not by others, nor by a group other than your primary one)
unless sticky, as `/tmp` is. Symlinks on the way must be root's or yours. A cache that fails
this is refused: when the app first needs the cache, the start ends with exit 70 and a line that
names the directory at fault, here a cache below a directory with mode 777:

```console
$ FSBPYR_CACHE_DIR=$PWD/shared/cache dist/corpus-pandas-csv work/sales.csv
fsbpyr: cannot start .../dist/corpus-pandas-csv: refusing cache directory .../shared: other users may write to it
$ echo $?
70
```

The check runs when the app first extracts something into the cache; an app that extracts nothing is not affected. The cache must also be on a file
system that allows execution: on a `noexec` mount the import fails and the error suggests
`FSBPYR_CACHE_DIR`.

**The cache cleans itself.** What a running app extracted stays for the app's whole life; what no
process has used for 7 days is removed. A collection is due a day after the last; each start
(except as process 1) checks that with a `stat` of a stamp file in the cache, and a `statvfs`
once the last collection is an hour old. When one is due a detached process at idle priority
collects, so the start is not slowed. When the file system has less than 10% free, a collection
is due an hour after the last, and everything not in use goes. `FSBPYR_GC=1 app` collects the
app's cache now instead of running the app; `fsbpyr cache gc` collects fsbpyr's own cache and
every app cache it finds ([Other commands](User-Other-Commands.md#fsbpyr-cache-gc)).

Only a cache root with the marker file `.fsbpyr-cache` is ever collected, and only what fsbpyr
made there. The marker is written into a new (empty) root, or one that holds only a cache of an
earlier version, so a directory of yours that `FSBPYR_CACHE_DIR` names is never marked. To have
such a directory collected anyway: `touch "$FSBPYR_CACHE_DIR/.fsbpyr-cache"`.

## Environment variables

| Variable | Effect |
|---|---|
| `FSBPYR_CACHE_DIR` | the cache directory |
| `FSBPYR_GC=1` | collect the app's cache instead of running the app; `0` (or any other value): run the app and never collect its cache in the background |
| `FSBPYR_INSTALL_LINKS=DIR` | link the app's commands into DIR and exit instead of running the app ([below](#making-the-command-links-on-the-target)) |
| `FSBPYR_TRACE=1` | at exit, print what the run loaded and extracted, and how long the integrity check took, as one JSON line on stderr |
| `FSBPYR_SELFTEST=natives` | check that this machine provides every library and symbol version the native extensions need ([below](#checking-a-target-machine)) |
| `FSBPYR_SELFTEST=<anything else>` | import the entry point and check it instead of running the app (what the build's smoke test does) |
| `FSBPYR_IMPORTTIME` | when set, print CPython's import-time table |
| `FSBPYR_MAX_INSTANCES` | how many copies of the app one user may run at once (at most 4096; `0`: no limit); by default 4 per CPU plus 16, at least 64 and at most 1024; all the links of an executable share them |
| `PYTHONUNBUFFERED` | set and non-empty: stdout and stderr unbuffered, as in CPython; the only `PYTHON*` variable an app reads |
| `JOURNAL_STREAM` | set by systemd: a stdout or stderr that is the journal is line-buffered ([below](#under-systemd-in-a-container-from-cron)) |

`FSBPYR_TRACE=1` writes a line that starts with `fsbpyr-trace ` followed by a JSON object with
the keys `decompressed_bytes`, `extracted` (the files extracted in this run), `integrity_ns`,
`modules`, `reads` and `stored_bytes`:

```console
$ FSBPYR_TRACE=1 dist/pandas-csv work/sales.csv 2>trace.txt >/dev/null
$ cut -c1-120 trace.txt
fsbpyr-trace {"decompressed_bytes": 77067848, "extracted": ["numpy/_core/_multiarray_umath.cpython-314-x86_64-linux-gnu
```

## Under systemd, in a container, from cron

- **Output reaches its reader line by line.** A non-empty `PYTHONUNBUFFERED`, which units and
  Dockerfiles often set, makes stdout and stderr unbuffered. When systemd sends them to the
  journal (`JOURNAL_STREAM` names the file), they are line-buffered, so each line reaches the
  journal as it is printed and survives a SIGTERM.
- **SIGTERM ends an app that runs as process 1** (a container without an init) with exit status
  143, so `docker stop` does not wait for SIGKILL; `python app.py` as process 1 would ignore it.
  The app's own SIGTERM handler replaces this one, and a forked child gets the default behaviour
  back.
- **The cache is found without `HOME`:** when it is unset, empty or relative, the passwd entry's
  home is used, by the app and its background cache collection alike.

## Making the command links on the target

`FSBPYR_INSTALL_LINKS=DIR app` links each of the app's commands into `DIR`, as absolute symlinks
to the executable's real path, and `x.py` beside `x` for each command that is a script, so a
crontab or unit line that runs `/opt/tools/x.py` keeps working. It prints what it did and exits
without running the app; a second run changes nothing. A file, a directory or a link to
something else in the way is reported and kept, the other links are still made, and the exit
status is 1. It is an environment variable, never an option, so that a sudo rule for the app
cannot be used to create root-owned links. An example is on
[Scripts and tools](User-Scripts.md#deploying).

## Checking a target machine

Before deploying an app with native code, run it on the target with `FSBPYR_SELFTEST=natives`.
It extracts every native extension into the cache and runs the dynamic loader on each in trace
mode, which resolves libraries and symbol versions without running the extension:

```console
$ FSBPYR_SELFTEST=natives dist/corpus-boosting
fsbpyr selftest: all natives load (196 extensions)
$ echo $?
0
```

For each library that is missing it prints a line such as
`fsbpyr selftest: missing libstdc++.so.6 (needed by <extension>, ...)` or
``missing version `GLIBCXX_3.4.32' of libstdc++.so.6 (needed by ...)``, and exits 1 if there
are any. It checks only what extensions load at startup: libraries they open later by name
(`dlopen`) are not seen.

`FSBPYR_SELFTEST=1` checks only that the entry point imports (and, for an executable of
several commands, every command; a script is only found, not imported):

```console
$ FSBPYR_SELFTEST=1 dist/pandas-csv
fsbpyr selftest ok: corpus-pandas-csv
```

## What the target needs

- Linux x86-64 or aarch64 with the glibc and the libraries the build listed as `target needs:`
  (`fsbpyr inspect` shows them again as `needs`): glibc 2.34 or newer, or 2.28 or newer for an
  app built with `--glibc 2.28` ([Building for EL8](User-Building.md#building-for-el8-glibc-228)),
  newer still when bundled native code needs it.
- On aarch64, a kernel with 4K or 64K pages. Kernels with 16K pages (the Raspberry Pi 5's
  default kernel, Asahi Linux on Apple Silicon) are not tested and not supported.
- A mounted `/proc`: the executable finds itself through `/proc/self/exe`.
- A cache directory as described above, on a file system that allows execution.
- Time zone data, terminfo, the CA store and `openssl.cnf` come from the system, as for any
  Python, unless the app depends on `tzdata` or `certifi`, which carry their own.

On a musl system such as Alpine the glibc loader the executable names does not exist, and the
shell reports the executable as "not found" although the file is there.

## Refusals (exit 70)

An executable exits 70 without running the app when:

- its integrity check fails, or its payload is otherwise damaged
  ([above](#what-a-start-does));
- it is set-uid, set-gid or has file capabilities: the cache location comes from the caller's
  environment, so such a start would not be safe;
- as many copies of the app run already as the instance limit allows (`FSBPYR_MAX_INSTANCES`,
  [above](#environment-variables)): an app that keeps starting itself, by whatever route, stops
  there;
- the cache directory is refused ([above](#the-cache)), when the app first extracts a file into it;
- the app started a copy of itself as a Python interpreter (`sys.executable -m ...`) and the
  build left `interpreter-reexec` unset, the default ([Settings](User-Settings.md#interpreter-reexec));
  with `interpreter-reexec = false` the copy runs the app instead. A script that starts one of
  the app's own script commands as `[sys.executable, <its __file__>]` is the exception: it gets
  that command, as `python sibling.py` would.

An executable of several commands started with no command, or an unknown one, lists its
commands on stderr and exits 2.

## How it differs from a virtual environment

The main differences:

- `sys.executable` is the app, not a Python interpreter. `multiprocessing` works with every start
  method; other helper interpreters need [`interpreter-reexec`](User-Settings.md#interpreter-reexec).
- A package module's `__file__` is below the executable, which is a file, not a directory.
  Packages that read files next to their `__file__` need [`fs-packages`](User-Settings.md#fs-packages).
  A script's `__file__` is beside the executable instead, where the files it reads must be
  deployed ([Scripts and tools](User-Scripts.md#files-next-to-a-script)).
- `sys.argv[0]` is the absolute path of the start, also for `./app`, where a venv's script keeps
  `./app`.
- A hard link to the executable is a name of its own: `sys.executable` and `__file__` are below
  the link's path, and a rebuild leaves the hard link on the old build. A symlink resolves to the
  executable.
- Resources are read-only, and extracted files have mode 0400 or 0500. Write to a path of your
  own.
- `sys.prefix` is the executable's directory; files a wheel installs outside `site-packages`
  (`share/`, `etc/`, scripts) are not in the app.
- `tkinter`, `idlelib`, `ensurepip`, `turtledemo` and `test` are left out of the standard
  library, and so are `_dbm` and `_gdbm`, so `dbm.ndbm` and `dbm.gnu` are missing; `dbm.sqlite3`
  and `dbm.dumb` work.
- `sys.frozen` is not set (it is PyInstaller's flag, and code that sees it looks for PyInstaller's
  layout).
- On Python 3.15, an uncaught `ModuleNotFoundError` for a module outside the standard library
  ends with `fsbpyr: X is not in the executable: add it to the project's dependencies and
  rebuild`, instead of CPython 3.15's hint about site-packages and virtual environments.

The full table, with each symptom and its remedy, is in
[Developer Guide: Compatibility](Dev-Compatibility.md#how-an-app-differs-from-a-venv).
