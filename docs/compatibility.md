# Compatibility

What differs between an app built by fsbpyr and the same project in a virtual environment,
what the machine that runs it must provide, what is tested, and what to do when it fails. The
supported targets are in the [wiki](https://github.com/devitops-com/fsbpyr/wiki/User-Overview).

Statements marked *unverified* come from reading code or documentation, not from a run.

## Differs from a venv

Each row gives what you see, why, and what to do.

| Symptom | Cause | Remedy |
|---|---|---|
| `NotADirectoryError: [Errno 20] Not a directory: '<exe>/<pkg>/data.json'`, or an existence check on such a path returns `False` | A package reads files next to its `__file__`. Its `__file__` is below the executable, which is one file, not a directory. An uncaught `OSError` ends with the note `fsbpyr: <path> is inside the executable; <package> reads files next to its __file__: rebuild with --fs-package <package>`; the note is absent when the app catches the error or replaces `sys.excepthook` (typer and rich do) | Build with `--fs-package <package>` (or `fs-packages` in `[tool.fsbpyr]`); `fsbpyr build --hints` lists likely packages |
| A compiled package (mypyc's `black`) fails on import with `FileNotFoundError: ... <cache>/runtime/<id>/site/<pkg>/data.txt` | Its compiled modules live in the cache, where only compiled files are extracted, and they read files next to their `__file__`. An uncaught error ends with `fsbpyr: <path> is missing from the native cache, which holds the compiled files of a package only; <package> reads files next to its __file__: rebuild with --fs-package <package>` (absent when the app catches the error or replaces `sys.excepthook`) | Build with `--fs-package <package>` (or `fs-packages` in `[tool.fsbpyr]`) |
| In a project without a build system, a directory of scripts or a single script ([Scripts and tools](https://github.com/devitops-com/fsbpyr/wiki/User-Scripts) in the wiki) a script's `__file__` is `<directory of the executable>/<path>.py`, a path that exists only if the host has a file there; `open(__file__)` of the script's own source fails, and so does `importlib.util.spec_from_file_location` on it, or a plugin scan of its directory (`os.listdir`) for the project's own modules | A module of the scripts root (`_fsbpyr_scripts/`) says it lives next to the executable (the directory of the resolved executable, as a script run by path does; a link's own directory for a hard link), so the files a script reads next to `__file__` (config, state, logs) are the host's, deployed beside the executable, and are not in it; the executable holds the project's Python files only. The code is still the payload's: the path hook claims those directories (and `sys.path[0]` of a script is its own), so a stale `.py` copy beside the executable is never imported and `sys.path.append(os.path.dirname(__file__) + "/pylib")` serves the payload's `pylib`; code objects keep the `<exe>/_fsbpyr_scripts/<path>.py` file name, so tracebacks show the payload's source lines. The build warns for each read next to `__file__` it finds in the scripts (`reader.py:5: reads a file next to its own __file__ ...`: deploy the file beside the binary); a missing file ends with the note `fsbpyr: <dir>/x.conf is missing; the files a script reads next to its __file__ are read next to the executable, from <dir>/: deploy it there` (absent when the app catches the error or replaces `sys.excepthook`). `--fs-package _fsbpyr_scripts` would fail the build ("not a regular package") and is no advice. A start by the path `__file__` names (a `name.py` link) keeps `argparse`'s prog the file's name (3.14.9 and later would print `<exe> -m <module>`). A file or directory that is no Python name is deployed under its stored name (`sub_dir/`, below) | Deploy the files a script reads beside the executable (`install -m 644 config.ini "$DEST"`); read the script's own source from the payload with the loader's `get_source` or `importlib.resources`, not `open(__file__)` (`inspect.getsource` works, but linecache reads a copy beside the executable first and shows it instead); a module imported by its name is `_fsbpyr_scripts.<name>` for `module:function` commands and `__main__` for scripts |
| A top-level module (`mod.py`) reads `os.path.join(os.path.dirname(__file__), "x.conf")`, or a package without `__init__.py` (a namespace directory) reads a file next to its `__file__` | A top-level module's `__file__` is `<exe>/mod.py`, and the path of a file next to it is no module. `--fs-package` takes regular packages (with an `__init__.py`) only: it fails the build for a module or a namespace directory. The uncaught-error note says so, and for a namespace directory names the regular package below it that `--fs-package` takes, if there is one | Read the file from a path outside the executable; for a namespace directory add an `__init__.py` to the package that reads, and build with `--fs-package` for it |
| A file or a directory of a directory of scripts has a name that is no Python name (`check-disk.py`, `10-backup`, an extensionless `check-disk`; `sub-dir/`, `2024-tools/`): its `__file__` is `.../check_disk.py`, and the `__file__` and `sys.path[0]` of a script in such a directory name `.../sub_dir` (beside the executable, where the files a script reads next to `__file__` are deployed under that name) | The build stores the file or directory under a module name in the same place ([Scripts and tools](https://github.com/devitops-com/fsbpyr/wiki/User-Scripts#a-directory-of-scripts) in the wiki), the path hook maps an entry `<exe>/_fsbpyr_scripts/sub-dir` to it, and a script runs as `__main__`; `sys.argv[0]` is the name the command was started by. A `..` in a `sys.path` entry is resolved inside the executable, never above it: such an entry is not served from the payload | Do not compute a name from `__file__`; use `sys.argv[0]` |
| A crontab, unit or agent line runs `python3 /opt/tools/x.py` and fails with a `SyntaxError` about null bytes | `x.py` is now a link to the executable, which is an ELF file, not a Python file | Run `/opt/tools/x` (the command's name, without `.py`), or make `x.py` a link to the executable too (a start called `x.py` runs the command `x`): `FSBPYR_INSTALL_LINKS=/opt/tools app` makes both links |
| `FSBPYR_INSTALL_LINKS=DIR` is set and the app does not run: it prints `linked ...` lines and exits | The variable asks the executable to link its commands into DIR (absolute symlinks to its real path, and `x.py` for each script command `x`) and exit, 0 if all links are there, 1 if a file, a directory or a link to something else was in the way (kept; the others are still made) or DIR is missing or not writable. It is an environment variable, never an option, so a `sudo <binary> *` rule cannot be used to create links; sudo's `env_reset` (default on) drops it | Unset it; to install links run `FSBPYR_INSTALL_LINKS=/opt/tools/bin /opt/tools/app` |
| `sys.argv[0]` of a start by bare name on `PATH`, by `./app` or through a link is the absolute path of the start (`/usr/local/bin/cmd`); started with `exec -a other`, it is `other` | `sys.argv[0]` is the path `execve` was given (`AT_EXECFN`), made absolute against the working directory, when its basename is `argv[0]`'s basename (D14). A venv's script gets the same full path for a start by name or absolute path, and keeps `./app` for a relative start (the app gives `/cwd/app`); `exec -a` renames the process for an app and not for a script, which runs under its interpreter's path. argparse's `prog` and a multi-command start read only the basename | Compare `os.path.basename(sys.argv[0])` where the name matters |
| A hard link to the executable runs the app with `sys.executable`, `__file__` and `sys.path[0]` below the link's own path (for a script, next to it); a symlink gives the target's | `sys.executable` is the resolved path of the name the app was started by (D14): a symlink resolves to the file, a hard link is a name of its own. A rebuild replaces the file, so a hard link keeps the old build while the new path runs the new one. The D44 check applies to a hard-link start as to any other, also in a multi-command executable (a symlink named for a command does not meet it: see below) | Name commands with symlinks, or branch on `sys.argv[0]`; recreate hard links after a rebuild |
| `PYTHONPATH`, `PYTHONHOME`, `PYTHONDONTWRITEBYTECODE` and the other `PYTHON*` variables have no effect, except `PYTHONUNBUFFERED` (set and non-empty: stdout and stderr unbuffered) and `JOURNAL_STREAM` (systemd's `device:inode` of the journal: a stdout or stderr that is that file is line-buffered unless unbuffered already); `sys.flags.utf8_mode` is 1. As process 1 (a container without an init) SIGTERM ends the app with status 143 (a `SystemExit(143)` handler, installed before the app's code runs and only while SIGTERM has its default disposition; the app's own handler replaces it); `python app.py` as process 1 ignores SIGTERM | The interpreter runs isolated, with UTF-8 mode on and no site import (D14). | Use `FSBPYR_*` variables or the app's own options |
| A multi-command executable (several console scripts, [Building](https://github.com/devitops-com/fsbpyr/wiki/User-Building#several-commands-in-one-executable) in the wiki) runs the command its `argv[0]` names, or the one `argv[1]` names | Its entry point is a table of `name=target` lines. Started under another command's name, a child like `subprocess.run(["acme-db", "-m", "word"])` runs that command with those arguments: a start named for a command, and not for the executable, is never a helper interpreter (D44 stays for `sys.executable -m ...` and hard links). The dispatcher form, `acme acme-db -m word`, is no helper interpreter either: the first argument after the interpreter flags is the command's name, and `-m word` are that command's arguments. So a command that is interpreter-like (a console, a REPL, `ipython`) is reachable through `acme COMMAND` as well as through its link | A `sudo` or other rule that must allow one command names its link, not the executable, which runs every command |
| All the links of a multi-command executable share one instance budget (D45): monitoring plugins and cron fleets built from a directory of scripts exit 70 under load, which Nagios shows as CRITICAL | The budget counts copies by the executable's file, whatever name started them | If several commands legitimately run many copies at once, set `FSBPYR_MAX_INSTANCES` to a higher number; keep the binary outside directories an agent runs file by file |
| A tool binary (a directory of nothing but a requirements file, [Scripts and tools](https://github.com/devitops-com/fsbpyr/wiki/User-Scripts#a-tool-from-its-requirements-file) in the wiki) starts a command and the tool's helper exits 70 (`fsbpyr: refusing to run <what> as Python: the app started itself as a helper interpreter`): a worker pool, a plugin run through `sys.executable -m`, `-c` or a `.py` file below the executable. Ansible's modules on the implicit `localhost` run a file of the host, `sys.executable /path/AnsiballZ_x.py`, which is no helper interpreter (only `.py` files below the executable, or beside it for an app of scripts, are): the tool's output says `unknown command '/path/AnsiballZ_x.py'` | The tool starts helpers through `sys.executable` (D44), as any app does, and the build cannot know which tools do: it prints the helper-interpreter note only for the libraries it knows (joblib, loky, `jupyter_client`, ...) | Build with `--interpreter-reexec` ([D23](../docs/IMPLEMENTATION_PLAN.md)) and test the commands you rely on; for Ansible's `localhost`, set `ansible_python_interpreter` to a real interpreter |
| A tool binary lacks a command the tool has in a venv: a dependency's script (`jinja2`'s `jinja`), or a tool of a `pip freeze` file that another listed distribution requires | The commands are the `console_scripts` and `gui_scripts` of the distributions the file names (a meta-package without scripts or code, as `jupyter`, gives those of its requirements; a tool also has the commands of its direct requirements named after it, `ansible` those of `ansible-core`; a library without scripts gives none); in a file that lists every installed distribution (`pip freeze`, `pylock.toml`) only the ones no other listed distribution requires. The build prints which tools it took and which commands they gave | List the tools alone in the requirements file; to have a dependency's script, name its distribution there and keep the file short (not a freeze). A `sudo` rule names the link (`/opt/tools/ansible`), as for any multi-command executable; interpreter-like commands (`ipython`, `ansible-console`) are reachable through `tools ipython` too |
| `sys.prefix` is the executable's directory; files outside `site-packages` (`share/`, `etc/`, scripts a wheel installs elsewhere) are not in the app | The launcher pins every prefix to the directory of the executable; the build packages the modules and package data of the locked distributions | Ship such files next to the app, or read them through the package that owns them (`importlib.resources`). Legacy `setup.py` `scripts=` files that start with `#!python` in the wheel are commands ([Building](https://github.com/devitops-com/fsbpyr/wiki/User-Building#a-project-without-console-scripts) in the wiki); the others (shell scripts) are not packaged: the build warns, naming each; use `[project.scripts]` console scripts or `--entry` |
| `ModuleNotFoundError` for `tkinter`, `idlelib`, `ensurepip`, `turtledemo` or `test`; `_dbm` (so `dbm.ndbm`) is missing | The build leaves these out of the standard library; `_tkinter` and `_dbm` are the two shared objects of the interpreter build, and neither is bundled. GUI toolkits that ship as wheels (Qt, wx) are not blocked, but no test covers them | Do not import them; `dbm.dumb` is pure Python |
| A resource opened for writing fails: `ValueError: payload resources are read-only; invalid mode ...`; an extracted file has mode 0400 or 0500 | Resources are served from the payload, and files extracted to the cache are made read-only | Write to a path of your own, not next to the package's data |
| `sys.frozen` is missing, and `sys._MEIPASS` too | `sys.frozen` is PyInstaller's flag, and code that sees it looks for PyInstaller's layout (D21) | Do not test for it; test for the files you need |
| A child the app starts as a Python interpreter (`subprocess.run([sys.executable, "-m", "tool"])`, `-c CODE`, or a `.py` file of the app, as joblib's `n_jobs`, Jupyter kernels and py-cpuinfo do) exits 70, printing `fsbpyr: refusing to run <what> as Python: the app started itself as a helper interpreter`; joblib then fails with `TerminatedWorkerError` | `sys.executable` is the app, not an interpreter (D22), and running the app in such a child would start it again and again (D44). multiprocessing children are recognized. The exception is a script that is one of the app's own commands (`subprocess.run([sys.executable, <sibling's __file__>])` in a directory of scripts): it runs that command, as `__main__` with its directory first on `sys.path`, and so does a path that is a command's link (the restart idiom) | Build with `--interpreter-reexec` ([D23](../docs/IMPLEMENTATION_PLAN.md)); then such a child of the app itself runs as CPython would. An app whose own options are named `-m` or `-c` and which starts itself with them: `--no-interpreter-reexec` |
| An app exits 70 printing `fsbpyr: refusing to start: <N> copies of this app are running already, as many as this user may run at once` | N copies of the executable run already for this user, counted in `/dev/shm` (a container with a `/dev/shm` of its own counts apart); the limit is 4 per CPU and 16, at least 64 and at most 1024 (D45). It stops an app that starts itself again and again, however it starts it | If the app keeps starting itself, find the code that starts `sys.executable` (`ps -f`); if it needs more copies at once, set `FSBPYR_MAX_INSTANCES` to a higher number (at most 4096; 0 turns the limit off) |
| `ModuleNotFoundError: No module named 'pkg_resources'` | setuptools 84.0.0 ships no `pkg_resources`. With an older setuptools pin it finds no distributions (*unverified*) | Use `importlib.metadata` and `importlib.resources` |

## What the target must provide

The executable carries CPython, the standard library and the locked distributions. It needs
these from the machine:

- **glibc 2.34 or newer** on Linux x86-64 or aarch64 (RHEL 9, Amazon Linux 2023, Ubuntu
  22.04, Debian 12 and newer, SLES 15 SP6 and newer). `fsbpyr inspect` shows the glibc the
  executable actually needs. SLES 15 SP4 and SP5 (glibc 2.31) are not supported. The default
  build is tested on the distributions in the first table below, releases their vendors support.
  **EL8** (RHEL 8, Rocky Linux 8, AlmaLinux 8; glibc 2.28) is supported by building with
  `--glibc 2.28`, which needs the glibc 2.28 stub of the release, published in its companion release `el8-vX.Y.Z`;
  such an app needs glibc 2.28 or newer and is tested on the two EL8 images below. Debian 10 and
  11, Ubuntu 20.04 and SLES 15 SP4 and SP5 are not tested, with or without `--glibc 2.28`; CentOS
  7 and Amazon Linux 2 have a glibc below 2.28 and are out of reach.
- **The libraries native code links and the build did not bundle.** `fsbpyr build` prints
  `target needs: glibc >= X.Y; libstdc++.so.6 (GLIBCXX_..., CXXABI_...), libgcc_s.so.1`, and
  `fsbpyr inspect` shows it again. Install those libraries on the target. A build warns when a
  native file needs a `GLIBCXX`, `CXXABI`, `GCC` or `ZLIB` version newer than manylinux_2_34
  systems provide (manylinux_2_28's, with `--glibc 2.28`).
- **To check a target before deploying,** run the app there with `FSBPYR_SELFTEST=natives`. It
  extracts every native extension into the cache and runs the dynamic loader of the target the
  app was built for in trace mode on each, which resolves libraries and symbol versions
  without running the extension. It prints `fsbpyr selftest: missing libstdc++.so.6 (needed by
  <extension>, ...)` or `missing version `GLIBCXX_3.4.32' of libstdc++.so.6 (needed by ...)`
  for each, and exits 1 if any (0 and `all natives load` otherwise). It needs the cache
  directory as a run does, and checks only what extensions load at startup: libraries they
  open later by name (`dlopen`) are not seen. glibc targets only.
- **A mounted `/proc`.** The launcher reads its own executable through `/proc/self/exe`, and
  multiprocessing children are authenticated through `/proc/<ppid>/exe`.
- **A cache on a file system that allows execution.** Native code is extracted there and
  loaded from it. A `noexec` mount makes the import fail; the error then names the cache and
  suggests `FSBPYR_CACHE_DIR`. The directory must be yours, writable by nobody else
  ([Running](https://github.com/devitops-com/fsbpyr/wiki/User-Running#the-cache) in the wiki).
- **Time zone data, terminfo, the CA store and `openssl.cnf`** come from the system, as for
  any Python. An app that depends on the `tzdata` package or `certifi` carries its own. Which
  system paths each library reads has not been tested here.
- **The kernel:** no minimum is established. Containers share the host's kernel, so the
  distribution matrix below tests only the kernel of the machine that ran it. Where a feature
  is missing the launcher goes without: it falls back when `O_TMPFILE` is unavailable
  (`place_unnamed` in `src/fsbpyr/launcher/cache.c`), and `close_range` is compiled out when
  the headers lack it (`src/fsbpyr/launcher/due.c`). The instance limit needs OFD locks
  (Linux 3.15) and `/dev/shm` or `$XDG_RUNTIME_DIR`; without them a start runs unguarded
  (`src/fsbpyr/launcher/slots.c`). Page sizes: see Tested on; 16K is not supported for now.

## Tested on

The portability test builds the `hello`, `heavy` and `compat` fixtures once, copies them into
each of these distributions with podman and runs every check there. CI does so for each Python
(3.12 to 3.15) and each target (x86-64 and aarch64, on a runner of its architecture); on
aarch64 every image but `archlinux:latest` (no arm64 variant) runs. On 3.15 `heavy` is not
built (onnxruntime has no wheel) and the `compat` checks of packages without a usable wheel
are not run ([Building](https://github.com/devitops-com/fsbpyr/wiki/User-Building#which-python) in the wiki).

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

Apps built with `--glibc 2.28` (the pure-Python `hello` and an app with a manylinux_2_28
`cryptography` wheel; `tests/e2e/test_el8.py`) run on:

| Distribution | glibc |
|---|---|
| `docker.io/library/rockylinux:8` | 2.28 |
| `docker.io/library/almalinux:8` | 2.28 |

The EL8 workflow (`.github/workflows/el8.yml`, which publishes the glibc 2.28 stubs in the
companion release `el8-vX.Y.Z` and is no part of the release gate) links the stub of every Python and target twice and
compares them. On the default Python only, it runs the stub tests, the four portability apps
(built with `--glibc 2.28` by the tag's fsbpyr, built in the workflow) on the twelve distributions above and on
these two, and a small corpus subset against its venvs; on aarch64 it also runs a pure-Python
and a native app in the VM described below.

aarch64 kernels with 64K pages (EL8's) are smoke-tested in a QEMU system VM, not on hardware
(`make test-vm`; the `vm-smoke` job of `full-suite.yml`, and the EL8 workflow with the EL8
userland): a few built apps start and run. The container matrix above runs on 4K-page kernels.
Kernels with 16K pages (the Raspberry Pi 5's default kernel, Asahi Linux on Apple Silicon) are
not tested and not supported for now (owner decision 2026-10-27: too few users); a Raspberry
Pi 5 can boot its 4K kernel with `kernel=kernel8.img` in `config.txt`.

The `compat` app runs one small check per library or behaviour in each build:

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

Distributions and checks are compared with the tests in `tests/unit/test_cli_docs.py`, so this
list cannot drift from the code. It says nothing about packages that are not in it.

## Tested on real applications

Besides the fixtures, a corpus of small applications is built and run in the corpus job of
`full-suite.yml`: on every push to main, weekly and on request, for each Python (3.12 to 3.15)
and each target (x86-64 and aarch64); `make test-corpus` runs it locally. A release itself runs
no tests: it publishes a commit whose run passed. Each app is a project in
`tests/corpus/<name>` with its own `uv.lock`; the test runs its commands in a venv and in the
built executable, each run with an empty home directory of its own, and compares exit code,
stdout and stderr ([tests/corpus/README.md](../tests/corpus/README.md)). An app is not run on a
Python where a package of its lock has no wheel for manylinux_2_34 (`tests/support/no-wheels.txt`:
on 3.15, 23 apps; `tensorflow` also on 3.14, and it has no 3.14 or 3.15 wheels at all). An
app that fails is marked in its directory's `commands.toml` as a known failure: the test
expects the failure and goes red once it is fixed; no app is marked at present. The
`[tool.fsbpyr]` column is what the app needs beyond the defaults; each setting's reason is a
comment in its `pyproject.toml`.

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
| `jupyter` | nbclient running a notebook in an ipykernel started as `sys.executable -m ipykernel_launcher`, IPython in process, jedi (the standard library's sources come from the payload, `fixups.py`); the kernelspec search is cut to ipykernel's own (`XDG_DATA_DIRS`): a built app has no `share/`, and a host's `/usr/share/jupyter/kernels/python3` (Debian's python3-ipykernel) would start the system's Python as the kernel; on aarch64, debugpy's universal wheel carries an x86-64 library, kept as data | `fs-packages = ["debugpy", "IPython", "nbformat", "parso"]`, `interpreter-reexec = true` |
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

`tests/fixtures/projects/plugins` is a host and two plugin distributions with a custom
entry-point group and a PEP 420 namespace they share. The apps are run only for the commands in
their `commands.toml`. `import distutils` before `setuptools` resolves to setuptools' copy (the
`distutils` check above); the `setuptools-vendor` app imports `setuptools` first.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| An app exits 70 printing `fsbpyr: cannot start <executable>: refusing cache directory <path>: owned by another user`, or `... other users may write to it`, in a CI container (an app with native code, at its first extraction; it used to end as an `ImportError` with exit 1) | The way from `/` to the cache passes a directory that another user owns or others can write to. Mounted home directories (GitHub's `/github/home` in a job container) are the usual case | Put the cache under the user's own tree: set `FSBPYR_CACHE_DIR`, or `XDG_CACHE_HOME` as `.github/workflows/build.yml` does for its job container |
| `_fsbpyr.CacheError: ...; the native cache <path> is on a noexec mount, set FSBPYR_CACHE_DIR to another directory` (a subclass of `ImportError`) | The cache is on a file system mounted `noexec` | Point `FSBPYR_CACHE_DIR` at a directory on a file system that allows execution |
| `ImportError: ... version 'GLIBC_2.xx' not found`, followed by what the build required and what the system has | A native file needs a newer glibc, libstdc++ or libgcc_s than the target has | Build on the baseline (manylinux_2_34 wheels, or `fsbpyr build` against the Debian 12 sysroot), or run on a newer system |
| A C++ or Fortran dependency without a manylinux wheel is built from its sdist, and the build warns about the glibc it needs | The sysroot has no libstdc++, so such a build cannot be redone against Debian 12's glibc and keeps the build host's | Build the dependency as a `manylinux_2_34` wheel (for example with cibuildwheel) and point uv at it |
| The shell says the executable is "not found" although the file exists | The target has no glibc loader at the path the executable names (musl, such as Alpine) | Run it on a glibc system; musl is not a supported target |
