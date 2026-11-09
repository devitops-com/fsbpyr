# Compatibility

How a built app differs from the same project in a virtual environment, carried over from
[docs/compatibility.md](../docs/compatibility.md) and checked against the code. The supported
targets are also on [User-Running](User-Running.md#what-the-target-needs), the user's view of
the failures on [User-Troubleshooting](User-Troubleshooting.md).

Statements marked *unverified* come from reading code or documentation, not from a run.

## How an app differs from a venv

"An app of scripts" is one built from a project without a build system, a directory of scripts or a
single script ([User-Scripts](User-Scripts.md#projects-without-a-build-system)). The `fsbpyr:` notes
below are added by the boot code's `sys.excepthook` in
[`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py) ([Uncaught
exceptions](Dev-Boot-Modules.md#uncaught-exceptions)); they are absent when the app catches the
error or replaces `sys.excepthook` (typer does).

| Symptom | Cause | Remedy |
|---|---|---|
| `NotADirectoryError: [Errno 20] Not a directory: '<exe>/<pkg>/data.json'`, or an existence check on such a path returns `False` | The package reads files next to its `__file__`, which is below the executable, a file. The note: `fsbpyr: <path> is inside the executable; <package> reads files next to its __file__: rebuild with --fs-package <package>` (`_inside_executable_note`, `_note_for`) | `--fs-package <package>` (`fs-packages` in `[tool.fsbpyr]`); `fsbpyr build --hints` lists likely packages ([fs-packages](#fs-packages)) |
| A compiled package (mypyc's, as black's `blib2to3`) fails on import with `FileNotFoundError: ... <cache>/runtime/<generation>/site/<pkg>/Grammar.txt` | Its compiled modules are extensions in the native cache's `site` directory (`native_cache` in [`_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py)), where their `__file__` is, and a package that is not an fs-package has only its native files there. The note `fsbpyr: <path> is missing from the native cache, ...` names the outermost regular package (`_site_note`); only a failed extraction gets fsbpyr's own wording (`cannot extract native code for <module> into <cache>: ...`) | `--fs-package <package>`, as in the `black` corpus app |
| In an app of scripts, `open(__file__)`, `importlib.util.spec_from_file_location` on it, or `os.listdir` of its directory fail: a script's `__file__` is `<directory of the executable>/<path>.py` | The executable holds the project's Python files only, and they appear next to it ([Scripts and the files beside the executable](#scripts-and-the-files-beside-the-executable)); a missing file ends with the deploy note (`_deploy_note`). `--fs-package _fsbpyr_scripts` fails the build | Deploy the files a script reads beside the executable; read the script's own source with the loader's `get_source` or `importlib.resources` (`inspect.getsource` works, but linecache prefers a copy beside the executable); a module imported by name is `_fsbpyr_scripts.<name>`, or `__main__` for a script |
| A top-level module (`mod.py`) or a namespace directory (a package without `__init__.py`) reads a file next to its `__file__` | `--fs-package` takes regular packages only (`mark_fs_packages` in [`classify.py`](../src/fsbpyr/classify.py)) and fails the build for either; the note (`_note_for`) says so and, for a namespace directory, names the regular package below it, if any | Read the file from a path outside the executable; for a namespace directory add an `__init__.py` to the package that reads, and build with `--fs-package` for it |
| A script file or directory whose name is no Python name (`check-disk.py`, `10-backup`, `check-disk`; `sub-dir/`, `2024-tools/`) shows its stored name in `__file__` and `sys.path[0]` (`check_disk.py`, `sub_dir`) | The build stores it under a module name (`_module_stem` in [`loose.py`](../src/fsbpyr/loose.py)), which the path hook applies to `sys.path` entries too (`_module_part`); a `..` in an entry is resolved inside the executable (`_without_dotdot`), and an entry that climbs above it is not served from the payload | Use `sys.argv[0]`, the name the command was started by; deploy the files such a script reads under the stored name (`sub_dir/`) |
| A crontab, unit or agent line runs `python3 /opt/tools/x.py` and fails with a `SyntaxError` about null bytes | `x.py` is now a link to the executable, an ELF file | Run `/opt/tools/x`, or make `x.py` a link to the executable too: a start called `x.py` runs the command `x` (`named` in [`_fsbpyr_rt/commands.py`](../src/fsbpyr/boot/_fsbpyr_rt/commands.py)). `FSBPYR_INSTALL_LINKS=/opt/tools app` makes both links |
| `FSBPYR_INSTALL_LINKS=DIR` is set, and the app prints `fsbpyr: linked ...` (or `present`) lines and exits | The variable makes the executable link its commands into DIR and exit, with status 1 if something is in the way or DIR is unusable ([links](Dev-Boot-Modules.md#links)); it is no option, so a `sudo <binary> *` rule cannot use it, and sudo's `env_reset` (default on) drops it | Unset it; to install links run `FSBPYR_INSTALL_LINKS=/opt/tools/bin /opt/tools/app` |
| `sys.argv[0]` of a start by bare name on `PATH`, by `./app` or through a link is the absolute path of the start; with `exec -a other` it is `other` | `_execve_argv0` takes the path `execve` was given (`AT_EXECFN`), made absolute, when its basename is `argv[0]`'s ([Boot modules](Dev-Boot-Modules.md#sysargv0-and-the-restart-idiom)). A venv's script does the same, except that it keeps `./app` for a relative start and ignores `exec -a` | Compare `os.path.basename(sys.argv[0])`, as argparse's `prog` and the choice of a command do |
| A hard link to the executable runs the app with `sys.executable`, `__file__` and `sys.path[0]` below the link's own path (for a script, next to it); a symlink gives the target's | `sys.executable` is what `/proc/self/exe` resolves to (`fsb_payload_open` in [`launcher/payload.c`](../src/fsbpyr/launcher/payload.c)): a hard link is a name of its own, keeps the old build after a rebuild, and keeps the helper-interpreter refusal (D44) that a symlink named for a command lifts | Name commands with symlinks, or branch on `sys.argv[0]`; recreate hard links after a rebuild |
| `PYTHONPATH`, `PYTHONHOME`, `PYTHONDONTWRITEBYTECODE` and the other `PYTHON*` variables have no effect but `PYTHONUNBUFFERED`; `sys.flags.utf8_mode` is 1; output to the journal is line-buffered; as process 1, SIGTERM ends the app with status 143 | The launcher configures an isolated interpreter (`PyConfig_InitIsolatedConfig`) with UTF-8 mode, no `site` import, `safe_path` and no bytecode writing (`main` in [`launcher/main.c`](../src/fsbpyr/launcher/main.c)); the rest are [service policies](#services-cron-and-containers) | Use `FSBPYR_*` variables or the app's own options |
| A multi-command executable ([Several commands in one executable](User-Building.md#several-commands-in-one-executable)) runs the command its `argv[0]` names, or the one `argv[1]` names | Its entry point is a table of `name=target` lines ([Boot modules](Dev-Boot-Modules.md#several-commands-in-one-executable)). A start named for a command (`is_sibling`), and the word after the executable in `acme acme-db -m word` (`child_command`), are commands, never helper interpreters: `acme-db` gets `-m word`, and `ipython` or a REPL is reachable through `acme COMMAND` too, unless a command is named `acme`: every start as `acme` runs that one; D44 stays for `sys.executable -m ...` and hard links | A `sudo` or other rule that must allow one command names its link, not the executable, which runs every command |
| All the links of a multi-command executable share one instance budget: monitoring plugins and cron fleets built from a directory of scripts exit 70 under load | The budget counts copies by the executable's file (device and inode, `fsb_hold_slot` in [`launcher/slots.c`](../src/fsbpyr/launcher/slots.c)), whatever name started them | Raise `FSBPYR_MAX_INSTANCES` if many copies legitimately run at once; keep the binary outside directories an agent runs file by file |
| A helper of a tool binary ([A tool from its requirements file](User-Scripts.md#a-tool-from-its-requirements-file)) exits 70: a worker pool, a plugin run through `sys.executable -m`, `-c` or a `.py` file below the executable; Ansible on the implicit `localhost` gets `unknown command '/path/AnsiballZ_x.py'` | The build names helper interpreters only for the libraries of `HELPER_INTERPRETERS` in [`build.py`](../src/fsbpyr/build.py) (joblib, loky, `jupyter_client`, py-cpuinfo, multiprocess, torch's inductor). Only a `.py` file below the executable, or beside it for an app of scripts, or below the native cache's site for a module of an fs-package, asks for one (`child_command` in [`_fsbpyr_rt/reexec.py`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py)); Ansible's `AnsiballZ_x.py` is a file of the host, an argument, which a tool reads as a command name | `--interpreter-reexec`, testing the commands you rely on; for Ansible's `localhost`, set `ansible_python_interpreter` to a real interpreter |
| A tool binary lacks a command the tool has in a venv: a dependency's script (`jinja2`'s `jinja`), or a tool of a `pip freeze` file that another listed distribution requires | The commands are the `console_scripts` and `gui_scripts` of the distributions the file names ([`tools.py`](../src/fsbpyr/tools.py)), with those of a meta-package's requirements (`jupyter`) and of a tool's direct requirements named after it (`ansible`: `ansible-core`'s); of a full list (`pip freeze`, `pylock.toml`) only distributions no other listed one requires count. The build prints what it took | List the tools alone, and a dependency whose script you want, in a short file (not a freeze). A `sudo` rule names the link (`/opt/tools/ansible`); `tools ipython` reaches an interpreter-like command (`ipython`, `ansible-console`) |
| `sys.prefix` is the executable's directory; files outside `site-packages` (`share/`, `etc/`, scripts a wheel installs elsewhere) are not in the app | `pin_prefixes` in `launcher/main.c` sets every prefix to the executable's directory and the standard library directory to the executable. Legacy `setup.py` `scripts=` files that start with `#!python` are commands ([`legacy.py`](../src/fsbpyr/legacy.py)); the build warns about each other one (`_legacy_script_warnings`) | Ship such files next to the app or read them through their package (`importlib.resources`); use `[project.scripts]` or `--entry`, not shell scripts |
| `ModuleNotFoundError` for `tkinter`, `idlelib`, `ensurepip`, `turtledemo` or `test`; no `_dbm` or `_gdbm` (so `dbm.ndbm`, `dbm.gnu`), and on 3.12 no `_crypt` (so `crypt`) | `EXCLUDED_DIRS` in [`runtime/stdlib.py`](../src/fsbpyr/runtime/stdlib.py) leaves these directories out, and `lib-dynload`, the interpreter build's shared-object modules (`_tkinter`, `_dbm`, `_crypt`, test modules such as `_testcapi`); PBS builds no `_gdbm`. GUI toolkits that ship as wheels (Qt, wx) are not blocked, but no test covers them | Do not import them; `dbm.dumb` and, from 3.13, `dbm.sqlite3` (built-in `_sqlite3`) work |
| A resource opened for writing fails: `ValueError: payload resources are read-only; invalid mode ...`; an extracted file has mode 0400 or 0500 | Resources are served from the payload ([`_fsbpyr_rt/resources.py`](../src/fsbpyr/boot/_fsbpyr_rt/resources.py)); files extracted to the cache are never writable (`fill` in [`launcher/cache.c`](../src/fsbpyr/launcher/cache.c)) | Write to a path of your own |
| `sys.frozen` and `sys._MEIPASS` are missing | They are PyInstaller's, and code that sees them looks for PyInstaller's layout | Test for the files you need |
| A child the app starts as Python (`[sys.executable, "-m", "tool"]`, `-c CODE`, a `.py` file of the app; joblib's `n_jobs`, Jupyter kernels, py-cpuinfo) exits 70 with ``fsbpyr: refusing to run `...` as Python: the app started itself as a helper interpreter ...``, and joblib raises `TerminatedWorkerError` | `sys.executable` is the app, which such a child would start again and again; `run_child` in [`_fsbpyr_rt/reexec.py`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py) refuses it unless the build chose a way ([Children through `sys.executable`](Dev-Boot-Modules.md#children-through-sysexecutable)). multiprocessing children ([`_fsbpyr_rt/mp.py`](../src/fsbpyr/boot/_fsbpyr_rt/mp.py)), a script that is one of the app's own commands, and a command's link (the restart idiom, [`_fsbpyr_rt/restart.py`](../src/fsbpyr/boot/_fsbpyr_rt/restart.py)) run (`_own_command`) | `--interpreter-reexec`: such a child runs as CPython would. An app whose own options are `-m` or `-c` and which starts itself with them: `--no-interpreter-reexec` |
| Exit 70 with `fsbpyr: refusing to start: <N> copies of this app are running already, as many as this user may run at once` | N copies run for this user, counted in `/dev/shm`, else `$XDG_RUNTIME_DIR` (a container with its own counts apart; with neither, no limit), against a limit of 4 per CPU and 16, at least 64, at most 1024 (`fsb_instance_limit`): it stops an app that starts itself again and again | If the app keeps starting itself, find the code that starts `sys.executable` (`ps -f`); if it needs more copies, raise `FSBPYR_MAX_INSTANCES` (at most 4096; 0 turns the limit off) |
| `ModuleNotFoundError: No module named 'pkg_resources'` | setuptools 84.0.0 ships no `pkg_resources`; with an older setuptools pin it finds no distributions (*unverified*). The build warns `setuptools: pkg_resources finds no distributions in executables; use importlib.metadata` when it packages a setuptools with `pkg_resources` | Use `importlib.metadata` and `importlib.resources` |

### fs-packages

A package in `fs-packages` (`--fs-package`) has every file below it extracted to the cache on
its first import, so that code reading files next to its `__file__` finds real files.

- `ConfigKey("fs-packages", ...)` in [`config.py`](../src/fsbpyr/config.py) reads the setting;
  `mark_fs_packages` in `classify.py` flags the package's module `FS_REQUIRED_ROOT` and every
  non-native file below it `FS_REQUIRED` (native files are extracted anyway). A name that is no
  regular package of the app fails the build (`fs-packages: 'x' is not a package of the
  application`, or `... not a regular package in the application`). `BUILTIN_FS_PACKAGES` there
  (babel, botocore, certifi, litellm, matplotlib, torch) count as listed when the app has them.
- `materialize_package` in `_fsbpyr_rt/native.py` extracts the package's files, the libraries
  its extensions need from outside it (torchvision's `torchvision.libs`, torch's `libc10.so`) and
  every other shared library its distribution ships (openmm's `OpenMM.libs/lib/plugins`). Its
  modules' code still comes from the payload.
- `fsbpyr build --hints` runs `file_relative_reads` and `loaded_libraries` of
  [`scan.py`](../src/fsbpyr/scan.py) on each module as [`compiler.py`](../src/fsbpyr/compiler.py)
  compiles it. Reads relative to `__file__` become one warning, `these packages read files next
  to __file__ (a heuristic); add one to fs-packages only if it fails at run time: ...`
  (`add_hints`); library names given to `CDLL`, cffi's `dlopen` or `find_library` are recorded in
  the build information (`system.may_load`), with no warning. The scripts of an app of scripts
  are scanned in every build, and each read is warned about, `file:line` (`_hint_script_reads`).
- `ctypes.CDLL` of a bare file name the system cannot load finds the app's bundled library of
  that name (`bundled_library`; PyTables' `libblosc2.so` in the `hdf5` app below).

### Scripts and the files beside the executable

In an app of scripts the project's Python files are stored below the payload root
`_fsbpyr_scripts/` ([`loose.py`](../src/fsbpyr/loose.py)) and behave as the same scripts run by
path ([The scripts root](Dev-Boot-Modules.md#the-scripts-root)): `__file__` is
`<directory of the executable>/<path below the root>` (`_shown`; for a hard link, its own
directory), that directory is first on `sys.path` (`_script_directory`), and the code is the
payload's, never a `.py` copy beside the executable. What a script reads there is the host's;
the build warns for each read next to `__file__`. On 3.14.9 and later the `argparse` fixup in
[`_fsbpyr_rt/fixups.py`](../src/fsbpyr/boot/_fsbpyr_rt/fixups.py) gives a script started
through a `name.py` link the file's name as prog, not `<exe> -m <module>`.
`[sys.executable, <a sibling's __file__>, args]` runs that command once the parent check of
[Dev-Security](Dev-Security.md#multiprocessing-children-and-helper-interpreters) has passed. A
project with a build system is not affected.

### Services, cron and containers

The boot code's policies ([Services and containers](Dev-Boot-Modules.md#services-and-containers)):
a stdout or stderr that is systemd's journal (`JOURNAL_STREAM`) is line-buffered; as process 1,
where `python app.py` ignores `docker stop`'s SIGTERM, a built app and its `-m` and `-c` helper
starts raise `SystemExit(143)` until the app installs its own handler, and no background
collection of the cache starts (`launcher/due.c`). When `$HOME` is unset, empty or relative, the
boot code, the launcher and fsbpyr's own cache take the cache root from the passwd entry's home
directory. Every start checks the whole file, at a cost that grows with its size
([Dev-Performance](Dev-Performance.md#the-integrity-check));
[Monitoring plugins and cron fleets](User-Scripts.md#monitoring-plugins-and-cron-fleets) has the
deployment advice.

## What the target must provide

The executable carries CPython, the standard library and the locked distributions. It needs
from the machine:

- **glibc 2.34 or newer** on Linux x86-64 or aarch64 (the distributions of
  [Tested on](#tested-on)); an older one, such as SLES 15 SP4 and SP5 (2.31), cannot run a
  default build. `fsbpyr inspect` shows the glibc the executable needs, the launcher's raised by
  the bundled native code (`glibc_report` in [`build.py`](../src/fsbpyr/build.py),
  `glibc_requirement` in [`elf.py`](../src/fsbpyr/elf.py)), and the build warns when a native
  file needs more than 2.34. The launcher, linked against a Debian 12 sysroot's glibc 2.36
  ([`runtime/sysroot.py`](../src/fsbpyr/runtime/sysroot.py)), is kept within 2.34 by the
  link-time wrappers of [`launcher/glibc.c`](../src/fsbpyr/launcher/glibc.c)
  ([Launcher](Dev-Launcher.md#glibcc)). Only 2.34 and newer is tested.
- **glibc 2.28 or newer for an app built with `--glibc 2.28`** (or `glibc = "2.28"` in
  `[tool.fsbpyr]`, also in a script's PEP 723 block), for EL8 (RHEL 8, Rocky Linux 8,
  AlmaLinux 8): such a build takes a stub linked against Rocky Linux 8.10's glibc 2.28 and
  `manylinux_2_28` wheels ([Building for glibc 2.28](Dev-Build-Modules.md#building-for-glibc-228)).
  Its glibc requirement is never below 2.28; `fsbpyr inspect` shows `baseline   glibc 2.28`, and
  `inspect --json` gives `glibc_baseline` for every app, `2.34` for one that records none
  ([`inspection.py`](../src/fsbpyr/inspection.py)). It is tested on all 14 images of
  [Tested on](#tested-on); no other glibc from 2.28 to 2.33 is tested, and one older than 2.28 is
  not supported.
- **The libraries native code links and the build did not bundle.** Every `DT_NEEDED` entry of
  every extension module, and of every library it reaches, must resolve to a payload file, to a
  library manylinux wheels may take from the system (`_SYSTEM_LIBRARIES` and `closures` in
  [`natives.py`](../src/fsbpyr/natives.py)), or to one `allow-system-libs` names, or the build
  fails; one no extension reaches (one ctypes or cffi opens) only gets a warning
  (`_check_unreached`). `fsbpyr build` prints
  `fsbpyr: target needs: glibc >= X.Y; libstdc++.so.6 (GLIBCXX_..., CXXABI_...), libgcc_s.so.1`,
  `fsbpyr inspect` the same as `needs` (`target_needs` in
  [`system.py`](../src/fsbpyr/system.py)): install those on the target. The
  build warns when a native file needs `GLIBC_PRIVATE`, whose internals have no compatibility
  promise, or a `GLIBCXX`, `CXXABI`, `GCC` or `ZLIB` version newer than the manylinux systems of
  its baseline provide (`_over_floor`, `MANYLINUX_LIMITS`, from auditwheel's policies), naming
  them (`BASELINE_SYSTEMS`: RHEL 9 and Ubuntu 22.04 for 2.34, RHEL 8 for 2.28):

  | Namespace | manylinux_2_34, x86-64 | manylinux_2_34, aarch64 | manylinux_2_28, x86-64 and aarch64 |
  |---|---|---|---|
  | `GLIBCXX` (libstdc++) | 3.4.29 | 3.4.29 | 3.4.24 |
  | `CXXABI` (libstdc++) | 1.3.13 | 1.3.13 | 1.3.11 |
  | `GCC` (libgcc_s) | 7.0.0 | 11.0 | 7.0.0 |
  | `ZLIB` (libz) | 1.2.9 | 1.2.9 | 1.2.9 |

- **To check a target before deploying,** run the app there with `FSBPYR_SELFTEST=natives`
  ([selftest](Dev-Boot-Modules.md#selftest)): it resolves every native extension's libraries and
  symbol versions with the loader of the executable's own `PT_INTERP`, without running them, prints
  `fsbpyr selftest: missing libstdc++.so.6 (needed by <extension>, ...)` or ``fsbpyr selftest:
  missing version `GLIBCXX_3.4.32' of libstdc++.so.6 (needed by ...)`` for each, and exits 1 (0 and
  `fsbpyr selftest: all natives load (N extensions)` otherwise). A refused cache directory ends it
  with exit 70. Libraries opened later by name are not seen; glibc targets only.
- **A mounted `/proc`**, for the launcher's `/proc/self/exe` (`cannot resolve own executable
  path (an executable needs /proc)` otherwise) and the parent check of multiprocessing children
  (`/proc/<ppid>/exe`, `/proc/<ppid>/status`).
- **A cache on a file system that allows execution,** owned by the user, every directory on the
  way to it the user's or root's and none writable by other users (a sticky one such as `/tmp`
  aside), or the app exits 70 at its first extraction ([Troubleshooting](#troubleshooting),
  [Dev-Security](Dev-Security.md#the-cache)).
- **Time zone data, terminfo, the CA store and `openssl.cnf`** come from the system, as for any
  Python; an app that depends on `tzdata` or `certifi` carries its own. When OpenSSL's `/etc/ssl`
  holds no trust store, the `ssl` fixup loads a system bundle
  ([fixups](Dev-Boot-Modules.md#fixups)). Which system paths each library reads has not been
  tested.
- **The kernel:** no minimum is established; the container matrix tests only the kernel of the
  machine that ran it. The launcher works without `O_TMPFILE`
  (`place_unnamed` in [`launcher/cache.c`](../src/fsbpyr/launcher/cache.c) leaves the file to
  `place_renamed`) and without `close_range` (`detach_descriptors` in
  [`launcher/due.c`](../src/fsbpyr/launcher/due.c) closes what `/proc/self/fd` lists, else every
  descriptor up to the limit). The instance limit needs OFD locks (Linux 3.15) and `/dev/shm` or
  `$XDG_RUNTIME_DIR`, or a start runs unguarded (`fsb_hold_slot` in
  [`launcher/slots.c`](../src/fsbpyr/launcher/slots.c)). Pages of 4 KiB and, on aarch64, 64 KiB
  are tested; 16 KiB pages are not supported.

## Tested on

The portability test ([`tests/e2e/test_portability.py`](../tests/e2e/test_portability.py))
builds the `hello`, `heavy` and `compat` fixtures once, mounts them read-only into a podman
container of each of the 12 images of `IMAGES` in
[`tests/support/images.py`](../tests/support/images.py) and runs every
check there. [CI](../.github/workflows) runs it for each Python (3.12 to 3.15) and target (x86-64
and aarch64, each on a machine of its architecture); on aarch64 `archlinux:latest` has no arm64
variant (`NO_ARM64`). On 3.15 `heavy` is not built (onnxruntime has no wheel) and the `compat`
checks of packages without a usable wheel are not run (`HEAVY_APPS` there, `missing` in
[`tests/support/wheels.py`](../tests/support/wheels.py), from
[`tests/support/no-wheels.txt`](../tests/support/no-wheels.txt)).

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

A default run takes `hello` and an app with a manylinux_2_28 `cryptography` wheel, built with
`--glibc 2.28`, to these two images (`test_el8_apps_run_on_el8`); the EL8 test mode builds every
app of the tests that way and runs the whole matrix on all 14 images
(`make test-el8-portability`). The EL8 jobs of
[`.github/workflows/full-suite.yml`](../.github/workflows/full-suite.yml) run that mode and gate
the release; [`.github/workflows/el8.yml`](../.github/workflows/el8.yml) only links the stubs
from a release's tag into a draft of its companion release
([Builds for glibc 2.28](Dev-Testing.md#builds-for-glibc-228-el8)).

The container matrix runs on kernels with 4 KiB pages. aarch64 kernels with 64 KiB pages
(EL8's) are smoke-tested in a QEMU system VM, not on hardware: `make test-vm` runs
[`tools/vm-smoke.py`](../tools/vm-smoke.py) on the kernel pinned in
[`tools/vm-pins.toml`](../tools/vm-pins.toml), Rocky Linux 8.10's, whose guest must report
65536-byte pages ([The page-size smoke test](Dev-Testing.md#the-page-size-smoke-test)). Kernels
with 16 KiB pages (Raspberry Pi 5's, Asahi Linux's) are not tested and not supported, as too few
systems use them.

The `compat` app runs the 18 checks of `MATRIX` in
[`tests/e2e/test_compat.py`](../tests/e2e/test_compat.py), one per library or behaviour:

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

The same file compares the `mp` fixture's `sys.argv[0]` (for a start by bare name, relative
path, absolute path, symlink and `exec -a`), restart idiom and sibling imports with its venv.
[`tests/unit/test_cli_docs.py`](../tests/unit/test_cli_docs.py) checks docs/compatibility.md's
tables, and every `fsbpyr: ` message it quotes, against the code. The lists say nothing about
packages not in them.

## Tested on real applications

A corpus of small applications is built and run by CI for each Python (3.12 to 3.15) and
target, and locally by `make test-corpus` (`PYTHON=3.X` for another Python;
[The corpus](Dev-Testing.md#the-corpus)). Each app is a project in
[`tests/corpus/<name>`](../tests/corpus) with its own `uv.lock` and `commands.toml`
([`tests/corpus/README.md`](../tests/corpus/README.md)).
[`tests/e2e/test_corpus.py`](../tests/e2e/test_corpus.py) runs each command in the app's venv and
in the built executable, each with an empty home directory of its own, and compares exit code,
stdout and stderr; one output must contain the app's `expect` string, so that two runs that fail
alike do not pass. `FSBPYR_CORPUS_APPS` runs a
comma-separated subset.

The corpus has 65 apps, 35 of which need no `[tool.fsbpyr]` setting. An app whose lock installs a
package without a manylinux_2_34 wheel for a Python, and does not build it from its sdist, is not
run there ([Wheels, sdists and known failures](Dev-Testing.md#wheels-sdists-and-known-failures)): 23
apps on 3.15 and `tensorflow` on 3.14, on both targets. No app is marked as a known failure. In the
EL8 test mode the rule looks for manylinux_2_28 wheels
([`tests/support/locks.py`](../tests/support/locks.py)) and every app is built with `--glibc 2.28`;
the full suite's `el8-corpus` job runs a few apps with native code that way.

The `[tool.fsbpyr]` column is what the app needs beyond the defaults, each setting explained in
a comment of its `pyproject.toml`.

### AI research and machine learning

| App | Risk it covers | `[tool.fsbpyr]` |
|---|---|---|
| `torch` | TorchScript compiling the source, dynamo tracing bytecode (no `torch.compile` on 3.15 yet), DataLoader workers forked from a forkserver that preloads the app's entry module (3.14, 3.15), checkpoints | none |
| `torchvision` | custom operators registered in `torch.ops`, the image extension (opened by `torch.ops.load_library` through a `FileFinder` on the package directory) | `fs-packages = ["torchvision"]` |
| `lightning` | the Lightning trainer, checkpoints, torchmetrics | `fs-packages = ["lightning"]` |
| `transformers` | a tiny BERT and GPT-2 built offline, a tokenizer trained in process, safetensors, a pipeline | `fs-packages = ["transformers"]` |
| `hf-datasets` | `map(num_proc=2)` through multiprocess and dill, Arrow files | none |
| `jax` | jaxlib's extension, XLA compiling at run time, Flax and Optax | none |
| `keras` | Keras 3 on JAX, a model saved and loaded | none |
| `tensorflow` | TensorFlow's C++ runtime, `tf.function`, `tf.data`, SavedModel (3.12 and 3.13 only); a RUNPATH of about 20,000 bytes; op libraries loaded by a path built from `__file__` | `fs-packages = ["clang", "tensorflow"]` |
| `sklearn` | bundled datasets, joblib's loky workers started as `sys.executable -m` and `-c`, threadpoolctl | `fs-packages = ["sklearn"]`, `interpreter-reexec = true` |
| `boosting` | xgboost and lightgbm loading their shared libraries with ctypes from paths built from `__file__` | `allow-system-libs = ["libgomp.so.1"]`, `fs-packages = ["lightgbm", "sklearn", "xgboost"]` |
| `onnx` | onnx's reference evaluator and onnxruntime | none |
| `faiss` | faiss-cpu's SWIG module and OpenMP | none |
| `numba` | the JIT compiler, the parallel threading layer and `cache=True` | `allow-system-libs = ["libgomp.so.1.0.0", "libtbb.so.12"]`, `fs-packages = ["corpus_numba"]` |
| `nlp` | nltk, spaCy (language data imported by name), sentencepiece, tiktoken's plugin namespace | `fs-packages = ["spacy", "thinc"]` |
| `vision` | cv2's loader putting its directory into `sys.path` to import its binary, scikit-image's lazy_loader stubs and data images, imageio-ffmpeg's bundled binary run as a subprocess | `fs-packages = ["cv2", "imageio_ffmpeg", "skimage"]` |
| `matplotlib` | matplotlib and seaborn: fonts and data of mpl-data, Agg rendering | none |
| `jupyter` | nbclient running a notebook in an ipykernel started as `sys.executable -m ipykernel_launcher`, IPython in process, jedi (standard library sources from the payload, [`fixups.py`](../src/fsbpyr/boot/_fsbpyr_rt/fixups.py)); the kernelspec search cut to ipykernel's own (`XDG_DATA_DIRS`), since a built app has no `share/` and a host's `/usr/share/jupyter/kernels/python3` (Debian's python3-ipykernel) would start the system's Python; on aarch64, debugpy's universal wheel carries an x86-64 library, kept as data | `fs-packages = ["debugpy", "IPython", "nbformat", "parso"]`, `interpreter-reexec = true` |
| `hydra` | a config package, `_target_` instantiation, plugin discovery in the `hydra_plugins` namespace | none |
| `optuna` | a study in SQLite, whose storage runs alembic migrations kept next to optuna's modules | `fs-packages = ["optuna"]` |

### Applied mathematics

| App | Risk it covers | `[tool.fsbpyr]` |
|---|---|---|
| `scipy` | scipy's compiled libraries | none |
| `sympy` | lambdify executing generated source, mpmath, gmpy2 and python-flint ground types | none |
| `networkx` | backend dispatch through entry points, scipy-backed algorithms, GraphML | none |
| `cvxpy` | solver extensions (Clarabel, SCS, OSQP); cvxpy lists its own package directory | `fs-packages = ["cvxpy"]` |
| `pulp` | CBC, an executable file in the package run as a subprocess; HiGHS in process | `fs-packages = ["pulp"]` |
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

[`tests/fixtures/projects/plugins`](../tests/fixtures/projects/plugins) is a host and two plugin
distributions with a custom entry-point group and a PEP 420 namespace they share. The apps are
run only for the commands in their `commands.toml`. The `setuptools-vendor` app imports
`setuptools` first; the other order is the `distutils` check above (`SetuptoolsDistutilsLoader`
in `_fsbpyr_boot.py`).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Exit 70 with `fsbpyr: cannot start <executable>: refusing cache directory <path>: owned by another user` (or `other users may write to it`), in a CI container or under `FSBPYR_SELFTEST=natives` | A directory on the way from `/` to the cache belongs to another user or is writable by others (`fsb_way_check` in [`launcher/way.c`](../src/fsbpyr/launcher/way.c), messages from `synced` in [`launcher/module.c`](../src/fsbpyr/launcher/module.c)), met at the first extraction ([Native code and the cache](Dev-Boot-Modules.md#native-code-and-the-cache)); usually a mounted home directory, as GitHub's `/github/home` in a job container | Set `FSBPYR_CACHE_DIR` or `XDG_CACHE_HOME` to a directory in the user's own tree |
| `_fsbpyr.CacheError: ...; the native cache <path> is on a noexec mount, ...` | The cache is on a `noexec` mount (`ST_NOEXEC`, checked by `PayloadExtensionLoader.create_module` when an import fails) | Point `FSBPYR_CACHE_DIR` at a file system that allows execution |
| ``ImportError: ... version `GLIBC_2.xx' not found``, followed by `; the build requires glibc ..., this system has ...` | A native file needs a newer glibc, libstdc++ or libgcc_s than the target has; `_system_library_hint` appends the build's `glibc` and the system's `CS_GNU_LIBC_VERSION` | Build on the baseline (manylinux_2_34 wheels, or `fsbpyr build` against the Debian 12 sysroot), or run on a newer system |
| A C++ or Fortran dependency built from its sdist gets a warning about the glibc it needs | A dependency built here that needs more than the baseline is built again against the baseline's sysroot (`built_here_above`, `rebuild_on_baseline`, `build_flags` in `runtime/sysroot.py`), C only: the sysroot has no libstdc++ | Build it as a `manylinux_2_34` wheel (for example with cibuildwheel) and point uv at it |
| A default build does not start on RHEL 8, Rocky Linux 8 or AlmaLinux 8 | Its launcher needs glibc 2.34; EL8 has 2.28 | Build with `--glibc 2.28` |
| `--glibc 2.28` fails with `release el8-vX.Y.Z (the glibc 2.28 stubs of vX.Y.Z) does not exist or has none for this Python: ...` | The companion release of this fsbpyr's tag is missing or has no stub for this Python and target (`_no_stubs` in [`runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py)) | Place the stub, `fsbpyr-stub-<python>-<target>-glibc2.28` (or `.xz`), beside the fsbpyr executable, or use a release that has the stubs |
| `--glibc 2.28` fails with `release el8-vX.Y.Z's <asset> could not be fetched: ...`, or `... does not match the SHA-256 the release publishes (...); nothing was kept` | The download failed, or the unpacked stub is not the one `SHA256SUMS-glibc2.28` lists | Place the stub beside the fsbpyr executable; after a mismatch, try again |
| `--offline` fails with `--offline: the <asset> is not placed or cached, ...` (`--glibc 2.28`), or `this project builds with Python 3.X, which needs <asset> of release vX.Y.Z; ...` | `--offline` fetches neither the glibc 2.28 stub (`_offline`) nor the fsbpyr of the project's Python (`variant` in [`dispatch.py`](../src/fsbpyr/dispatch.py)) | Place the stub where the message says, or build once without `--offline` |
| `--glibc 2.28` fails with `<path> is not the stub this fsbpyr (vX.Y.Z) needs: ...`, or `... it carries no runtime id: ...` | The file beside the fsbpyr executable is the stub of another Python, target or runtime id (an old release's, left by `fsbpyr self update`), or no stub (`_checked`) | Place the stub of the companion release of this fsbpyr's tag, or remove the file and let the build download it |
| A developer build fails `--glibc 2.28` with `no runtime for Python 3.X and glibc 2.28; run fsbpyr runtime build --python 3.X --glibc 2.28` | It has no release to fetch a stub from and takes the runtime from its cache (`find_runtime` in [`runtime/registry.py`](../src/fsbpyr/runtime/registry.py)) | Run that command: it links the stub against the Rocky Linux 8.10 sysroot |
| The shell says the executable is "not found" although the file exists | The target has no glibc loader at the path the executable names (musl, such as Alpine) | Use a glibc system; musl is not supported |
| A cron job or monitoring check that ran `python3 /path/x.py` fails, or exits 70 under load | See the `python3 x.py` and instance-budget rows of [the table above](#how-an-app-differs-from-a-venv) | Run the command's link; raise `FSBPYR_MAX_INSTANCES` |

## See also

- [Dev-Security](Dev-Security.md): the cache's rules and the other refusals.
- [Dev-Performance](Dev-Performance.md): what extraction and the checks cost.
- [User-Building](User-Building.md#building-for-el8-glibc-228): building for EL8 from a user's
  side.
