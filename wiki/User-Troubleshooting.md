# Troubleshooting

What you may see when building or running an app, why, and what to do. Most rows come from
fsbpyr's [compatibility notes](Dev-Compatibility.md); the rest from running the
[corpus examples](User-Examples.md).

## At build time

| Symptom | Cause | Fix |
|---|---|---|
| `native files need libraries that are neither bundled nor standard system libraries: ... needs libX.so.N` | A native file links a library no wheel bundles | Add the line the build prints, `allow-system-libs = [...]`, if the target provides it; or bundle it (`auditwheel repair`) ([Settings](User-Settings.md#allow-system-libs)) |
| `fsbpyr: <path> is missing from the native cache, which holds the compiled files of a package only; <package> reads files next to its __file__: rebuild with --fs-package <package>` (often at the end of a failed smoke test) | A package compiled to native code (mypyc, as black's) reads a data file next to its `__file__`, which is in the cache, where only its compiled files are | `--fs-package <package>` ([example](User-Examples.md#6-a-developer-tool-compiled-with-mypyc-black)) |
| `smoke test of the new ... failed` with `NotADirectoryError` on a path below the executable | A package reads files next to its `__file__` at import time | `--fs-package <package>` |
| `warning: these may start helper interpreters through sys.executable: ...` | A dependency may start Python helper processes | If the app uses that feature, build with `--interpreter-reexec` ([Settings](User-Settings.md#interpreter-reexec)) |
| `this project builds with Python X.Y, and this fsbpyr (..., a developer build) ...` | A source-built fsbpyr never downloads a variant for another Python | Build that variant from the checkout (`make build PYTHON=X.Y`), or use a released fsbpyr |
| `fsbpyr: error: cannot find the home directory; set HOME, XDG_CACHE_HOME or FSBPYR_CACHE_DIR` | fsbpyr's cache is under the home directory, and there is none: `HOME` is unset, empty or relative, and the passwd entry has no absolute home (a service, a container) | Set one of the three variables |
| `...: uv failed (exit ...)`, and uv's output says a dependency `doesn't have a source distribution or wheel for the current platform` (often on 3.15) | A dependency publishes nothing for this Python and glibc 2.34 | Build for 3.14 (`--python 3.14`) ([Python 3.15](User-Building.md#which-python)) |
| `built on this host from sdists, not reproducible elsewhere: ...` | Those distributions had no usable wheel, so uv built them here | Information only: the build machine needed their compilers and headers. `--strict` does not fail on it |
| A warning that a native file needs a newer `GLIBC`, `GLIBCXX`, `CXXABI`, `GCC` or `ZLIB` than `manylinux_2_34` (`manylinux_2_28` for a `--glibc 2.28` build) | The file needs more than those systems provide: usually a dependency built from source on this machine | Use a wheel of it for that baseline (for example built with cibuildwheel), or deploy only to newer systems |
| `unknown [tool.fsbpyr] key ...; supported: ...` | A typo or an option that exists only on the command line (`offline`, `hints`, `smoke`) | Use a key from [the list](User-Settings.md#every-key) |
| `best-compression and fast-compression exclude each other` | Both set | Keep one |
| A build with `--strict` exits 1 listing warnings | `--strict` turns warnings, and an unlocked build, into errors | Fix the warnings, lock the project (`uv lock`), or build without `--strict` |
| `command X skipped: not in the executable: x.py:3: import requests. declare it in requirements.txt, or in a '# /// script' block of the script ...` | A script imports something that neither the directory, its dependencies nor the standard library provides | Declare the dependency (`requirements.txt`, a PEP 723 block); with a venv beside the scripts, the warning gives the `uv pip freeze` line that declares what it holds ([Scripts and tools](User-Scripts.md#imports-are-checked)) |
| `release el8-vX.Y.Z (the glibc 2.28 stubs of vX.Y.Z) does not exist or has none for this Python: ...` | A `--glibc 2.28` build with no placed or cached stub, and the release has no companion release of EL8 stubs (yet), or its `SHA256SUMS-glibc2.28` lists no stub for this Python and target | Wait for it, or place the stub where the message says (next to the fsbpyr executable), or build with a release that has one ([Building for EL8](User-Building.md#building-for-el8-glibc-228)) |
| `<file> is not the stub this fsbpyr (vX.Y.Z) needs: it is the stub of runtime ..., and this fsbpyr expects ...; use the <asset> of release el8-vX.Y.Z` (or `it carries no runtime id: it is no stub of an fsbpyr release`) | A stub placed next to fsbpyr (or downloaded) is for another Python or target, or from a release with other launcher sources or pins, or is no stub at all; after `fsbpyr self update`, the old stubs are left in place | Place the stub of this release, Python and target, or remove the old one and let the build download it |
| `the <asset> downloaded from release el8-vX.Y.Z does not match the SHA-256 the release publishes (...); nothing was kept` | The download was damaged or replaced | Build again; if it persists, report it |
| `release el8-vX.Y.Z's <asset> could not be fetched: ...; to build without the network, place <asset> next to ...` | No network, or GitHub unreachable, during a `--glibc 2.28` build without a placed or cached stub | Place the stub where the message says (next to the fsbpyr executable) |
| `--offline: the <asset> is not placed or cached, and nothing is downloaded; place <asset> (or <asset>.xz) next to ..., or build once without --offline to cache it` | A `--glibc 2.28` build with `--offline` and no placed or cached stub | Place the stub where the message says, or build once with network access |
| `this project builds with Python X.Y, which needs fsbpyr-X.Y-<target> of release vX.Y.Z; it is not in the cache and --offline fetches nothing: run the build once without --offline` | A build with `--offline` for another Python, whose fsbpyr is not in the cache yet | Build once with network access; later builds use the cached fsbpyr ([Which Python](User-Building.md#which-python)) |
| `<name>: no command can run in the executable, each imports what it does not hold` | Every script command was skipped by the import check (for a single script, its one command) | Declare the dependencies, as in the row above |
| `smoke test of the new ... failed`, ending in `ModuleNotFoundError: No module named 'x'` | The project imports a package it does not declare in its dependencies | Add it to `[project.dependencies]` and lock again (`uv lock`) |
| `... import cgi. cgi was removed in Python 3.13; build with --python 3.12` | The script uses a standard library module that the build's Python removed | Build with the `--python` it names, or port the script |
| `x.py is not packaged: git does not track it; ...` | In a git work tree only tracked Python files are packaged | `git add` the file, or ignore it if it must stay out (a `secrets.py`) |
| `x.py:4: reads a file next to its own __file__, which is read next to the executable ...` | A script reads a data file beside itself; the executable holds only Python files | Deploy the file beside the executable ([Scripts and tools](User-Scripts.md#files-next-to-a-script)) |
| `cannot link command 'x': .../dist/x exists and is not a symlink; remove it or build with another -o` | A regular file or directory is where a command's link must go, and it is not the executable an earlier build of this project wrote for that command | Move it away, or build to another `-o`; the build replaces only symlinks and its own earlier output |
| `poetry.lock is out of date: its content hash is not pyproject.toml's ...` (or `pdm.lock`), or `Pipfile.lock is out of date: its _meta hash is not the Pipfile's ...` | The dependencies changed after locking | Lock again (`poetry lock`, `pdm lock`, `pipenv lock`); the build installs the lock as it is, and fails with `--strict` |
| `... has a setup.cfg but no pyproject.toml or setup.py; setup.cfg alone does not say how to build it` | A directory with only `setup.cfg` | Add a `pyproject.toml` with a `[build-system]` |
| `no requirements.txt, but requirements/ holds base.txt, prod.txt; ...` | No lock and no `requirements.txt`, only files in `requirements/`, so the build is unlocked | Name one with `--requirements requirements/<file>` |
| `<project> has console scripts: none; choose one with --entry (a script name, 'module' or 'module:function'); found: ...` | No console script, and no single `__main__` package or guarded module to choose | Pass `--entry` ([Building](User-Building.md#a-project-without-console-scripts)) |

## At run time

| Symptom | Cause | Fix |
|---|---|---|
| `NotADirectoryError: [Errno 20] Not a directory: '<exe>/<pkg>/data.json'`, often with `fsbpyr: <path> is inside the executable; <package> reads files next to its __file__: rebuild with --fs-package <package>` | A package reads files next to its `__file__` | `--fs-package <package>` ([Settings](User-Settings.md#fs-packages)) |
| A template, data file or plugin is not found (`TemplateNotFound`, an empty plugin list, an HTTP 500) | Same cause, but the app or library catches the error | Same fix; `fsbpyr build --hints` lists likely packages (guesses) |
| `fsbpyr: refusing to run <what> as Python: the app started itself as a helper interpreter`, exit 70; joblib's `TerminatedWorkerError` | A child started as `sys.executable -m/-c/<script>` | `--interpreter-reexec` ([Settings](User-Settings.md#interpreter-reexec)) |
| `fsbpyr: refusing to start: <N> copies of this app are running already, as many as this user may run at once`, exit 70 | The instance limit was reached | Find what keeps starting the app (`ps -f`); or raise `FSBPYR_MAX_INSTANCES` |
| `fsbpyr: cannot start ...: integrity check failed: the file was modified or damaged` | The file changed after the build | Copy or download it again; check it with `fsbpyr inspect --verify` |
| `fsbpyr: cannot start <executable>: refusing cache directory <path>: other users may write to it` (or `owned by another user`), exit 70 (common in CI containers) | A directory on the way to the cache belongs to another user or is writable by others; the app stops when it first extracts a file into the cache | Set `FSBPYR_CACHE_DIR` (or `XDG_CACHE_HOME`) to a directory in the user's own tree |
| `_fsbpyr.CacheError: ...; the native cache <path> is on a noexec mount, set FSBPYR_CACHE_DIR to another directory` | The cache is on a `noexec` file system | Point `FSBPYR_CACHE_DIR` at a file system that allows execution |
| ``ImportError: ... version `GLIBC_2.xx' not found ...; the build requires glibc ..., this system has ...`` | A native file needs a newer glibc, libstdc++ or libgcc_s than the target has | Build with wheels for the build's baseline (`manylinux_2_34`, or `manylinux_2_28` with `--glibc 2.28`), or run on a newer system; check targets first with `FSBPYR_SELFTEST=natives` |
| On RHEL 8, Rocky Linux 8 or AlmaLinux 8, the executable stops at once with lines such as ``/lib64/libc.so.6: version `GLIBC_2.34' not found (required by ...)`` | The app was built the default way, for glibc 2.34; EL8 has 2.28 | Build it with `--glibc 2.28` ([Building for EL8](User-Building.md#building-for-el8-glibc-228)) |
| The shell says the executable is "not found", though the file exists | No glibc loader at the path the executable names (musl, such as Alpine) | Run it on a glibc system |
| `ModuleNotFoundError` for `tkinter`, `idlelib`, `ensurepip`, `turtledemo` or `test`; `dbm.ndbm` and `dbm.gnu` missing | Left out of the standard library | Do not import them; `dbm.sqlite3` and `dbm.dumb` work |
| `ValueError: payload resources are read-only; invalid mode ...` | Resources are served from the file and cannot be written | Write to a path of your own |
| `PYTHONPATH` and similar variables have no effect | The interpreter runs isolated; only `PYTHONUNBUFFERED` is read | Use the app's own options or `FSBPYR_*` variables ([Running](User-Running.md#environment-variables)) |
| `usage: app COMMAND [ARGUMENT...]` and a list of commands, exit 2 | An executable of several commands was started under its own name with no command, or an unknown one | Start it through a command's link, or as `app COMMAND` ([Building](User-Building.md#several-commands-in-one-executable)) |
| A crontab, unit or monitoring line `python3 /opt/tools/x.py` fails with a `SyntaxError` about null bytes | `x.py` is now a link to the executable, which is no Python file | Run `/opt/tools/x` (or `/opt/tools/x.py` directly); `FSBPYR_INSTALL_LINKS=/opt/tools app` makes both links |
| `fsbpyr: .../settings.json is missing; the files a script reads next to its __file__ are read next to the executable, from ...: deploy it there` | A script's data file is not beside the executable | Deploy it there ([Scripts and tools](User-Scripts.md#files-next-to-a-script)) |
| The app does not run but prints `linked ...` or `present ...` lines | `FSBPYR_INSTALL_LINKS` is set | Unset it ([Running](User-Running.md#making-the-command-links-on-the-target)) |
| Monitoring checks or cron jobs exit 70 under load | All the links of an executable share one instance limit | Raise `FSBPYR_MAX_INSTANCES` ([Scripts and tools](User-Scripts.md#monitoring-plugins-and-cron-fleets)) |
| Ansible on `localhost` fails with `unknown command` | Ansible runs a module file of the host through `sys.executable`, which the executable does not run as Python | Give Ansible a real interpreter: `-e ansible_python_interpreter=/usr/bin/python3` |
| On Python 3.15: `fsbpyr: X is not in the executable: add it to the project's dependencies and rebuild` | An import of something that was not packaged | Add the distribution that provides it to the project's dependencies |
| Exit 70 at start for a set-uid, set-gid or file-capability copy | Refused: the cache location comes from the caller's environment | Do not make built apps set-uid or set-gid, or give them file capabilities |

## Reporting a problem

Open an issue at <https://github.com/devitops-com/fsbpyr/issues> with the build output, the
output of `fsbpyr inspect BINARY`, and the failing command. A vulnerability goes to a private
report as [SECURITY.md](../SECURITY.md)
describes, not to an issue.
