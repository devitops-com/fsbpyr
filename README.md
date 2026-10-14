# fsbpyr

Fast single-binary Python CLI runtime: `fsbpyr build` turns a Python project (uv or pip) into one
executable that carries CPython 3.14, the project and its locked dependencies. The executable
needs nothing on the target machine but Linux x86-64 with glibc 2.28 or newer: no Python, no
virtual environment, no installation step. Modules are imported straight from the file, so
startup stays close to plain CPython.

## Getting fsbpyr

fsbpyr builds itself into a single executable, and that executable is the easiest way to run
it: it carries its own runtime (CPython 3.14 and the launcher), so building apps with it needs
no compiler. Each [release](https://github.com/devitops-com/fsbpyr/releases) carries it,
built and attested by GitHub Actions:

```
curl -fLo ~/.local/bin/fsbpyr https://github.com/devitops-com/fsbpyr/releases/latest/download/fsbpyr
chmod +x ~/.local/bin/fsbpyr
gh attestation verify ~/.local/bin/fsbpyr --repo devitops-com/fsbpyr   # optional
```

The same executable also comes as `fsbpyr.xz`, half the download (about 14 MB instead of 29 MB),
attested as well:

```
curl -fL https://github.com/devitops-com/fsbpyr/releases/latest/download/fsbpyr.xz \
  | xz -d > ~/.local/bin/fsbpyr
chmod +x ~/.local/bin/fsbpyr
```

### Building from source

A source checkout needs only `make`, `curl` and, on systems other than Debian trixie,
[podman](https://podman.io) (install it with your system's package manager). `make help` lists
all targets.

```
make bootstrap   # once: from source to an installed fsbpyr
make build       # dist/fsbpyr, built by the installed fsbpyr
make install     # dist/fsbpyr, its man page and completions under ~/.local
```

**`make bootstrap`** takes a checkout to an installed fsbpyr. On **Debian trixie** it runs on
the host:

1. installs the missing OS packages with `sudo apt-get` (`clang-22 lld-22 git make curl
   ca-certificates`; trixie ships clang and lld 22 since 13.7; they link the launcher);
2. installs [uv](https://docs.astral.sh/uv/) from astral.sh into `~/.local/bin` if it is not
   on `PATH`; uv installs the Python pinned in `.python-version` (3.14.8) and the locked
   dependencies;
3. builds the runtime (`fsbpyr runtime build`: the pinned CPython from python-build-standalone
   and a glibc 2.28 sysroot are downloaded, the launcher linked, the standard library
   compiled; a minute or two, cached in `~/.cache/fsbpyr`);
4. builds fsbpyr in three stages: fsbpyr run from source builds `dist/stage1/fsbpyr`, that
   executable builds `dist/stage2/fsbpyr`, and that one builds `dist/stage3/fsbpyr`. Each
   stage must pass `fsbpyr inspect --verify` (its digest is recomputed over the whole file),
   and stages 2 and 3 must be byte-identical: builds are reproducible, so a difference means
   fsbpyr does not build itself faithfully, and bootstrap stops there;
5. copies stage 3 to `dist/fsbpyr` and runs `make install`.

On **any other Linux x86-64** steps 1 to 4 run in a Debian trixie podman image
(`Containerfile`: the same OS packages and uv from astral.sh), on the checkout mounted into it,
and `make install` runs on the host. uv is installed on the host too, for `make build`. The
container is IPv4-only: on podman's default network, TLS handshakes from Python with GitHub's
download host time out. A podman volume, `fsbpyr-cache`, keeps uv's cache and the runtime
between runs. `FSBPYR_BOOTSTRAP=podman make bootstrap` uses the container on trixie too.

**`make build`** builds the checkout with the installed fsbpyr (`~/.local/bin/fsbpyr`, else
`fsbpyr` on `PATH`) into `dist/fsbpyr`, and runs the result once as a smoke test. The installed
executable carries its own runtime, so this needs no compiler, only uv. With no fsbpyr
installed it runs `make bootstrap` first. The new executable's `--version` names the `vX.Y.Z`
tag on HEAD (`untagged-<commit>` otherwise).

**`make install`** installs `dist/fsbpyr` (running `make build` first if it is missing), with
the man page and shell completions it prints:

| File | Installed as |
|---|---|
| the executable | `~/.local/bin/fsbpyr` |
| man page | `~/.local/share/man/man1/fsbpyr.1` (`man fsbpyr` finds it when `~/.local/bin` is on `PATH`) |
| bash completion | `~/.local/share/bash-completion/completions/fsbpyr` (loaded by bash-completion) |
| zsh completion | `~/.local/share/zsh/site-functions/_fsbpyr` (add the directory to `fpath` before `compinit`) |
| fish completion | `~/.local/share/fish/vendor_completions.d/fsbpyr.fish` |

`PREFIX=/usr/local` (as root) installs under another prefix, for all three targets;
`BINDIR`, `MANDIR`, `BASH_COMPLETION_DIR`, `ZSH_COMPLETION_DIR`, `FISH_COMPLETION_DIR` and
`DESTDIR` override single locations. `make uninstall` removes the same files.

`fsbpyr runtime list` shows the runtimes in the cache. Running fsbpyr from a checkout or a wheel
(rather than as its own executable) always needs such a runtime, and a new fsbpyr version may
need a new one: the build then says `no runtime for Python 3.14; run fsbpyr runtime build`.

## Building an app

```
fsbpyr build [PROJECT_DIR] [-o OUT] [options]
```

The build host needs [uv](https://docs.astral.sh/uv/) on `PATH`, and network access the first
time (uv fetches a Python 3.14 and the locked dependencies; later builds work from its cache, or
with `--offline`). git is optional: with it, the executable records the commit it was built
from. The output defaults to `dist/<console-script-name>`.

uv picks wheels that run on glibc 2.28. A dependency without one is built from its sdist with
the build host's C compiler, against the host's glibc. When such a build needs a newer glibc
than 2.28, fsbpyr builds it again against the glibc 2.28 sysroot it links the launcher with
(downloaded once), so the executable keeps the baseline; uv's cached build of it is replaced.
If that build fails, as a C++ extension does (the sysroot has no libstdc++), or the build is
offline, the host's build is packaged and the build warns which glibc it needs.

| Option | |
|---|---|
| `--entry NAME` | console script name, `module` or `module:function` (default: the only script) |
| `-o, --output PATH` | where to write the executable (default: `dist/` and the console script's name) |
| `--requirements FILE` | pinned requirements file, for pip projects without `pylock.toml` |
| `--extra NAME` | install a project extra (repeat) |
| `--fs-package PACKAGE` | a package whose files must exist on disk (repeat) |
| `--allow-system-lib SONAME` | let native code use this library from the host (repeat) |
| `--offline / --no-offline` | no network access during the build |
| `--source / --no-source` | keep the `.py` sources (default) or leave them out: smaller, but tracebacks show no source lines and `inspect.getsource()` fails |
| `--fast-compression / --no-fast-compression` | zstd level 3 instead of 9: a faster build, up to ~8% larger for large apps (the standard library's files of 64 KiB and more stay level 9) |
| `--best-compression / --no-best-compression` | native libraries as xz -6 instead of zstd 9: about 6.5% smaller for aiagent, a slower build, and a slower first run, which decodes them into the cache; not with `--fast-compression` |
| `--hints / --no-hints` | also list packages that may read files next to `__file__` (a guess) |
| `--interpreter-reexec / --no-interpreter-reexec` | let the app's own children started as `sys.executable -m MODULE` or `-c CODE` run as Python |
| `--smoke / --no-smoke` | run the new executable once as a smoke test (default) or skip it |

`fsbpyr build -h` shows the same list. [Leaving out sources](#leaving-out-sources) explains
`--no-source`, [At run time](#at-run-time) `--fs-package` and the cache that
`--best-compression` decodes into, and [Helper interpreters](#helper-interpreters)
`--interpreter-reexec`.

The same settings can live in the project's `pyproject.toml`; command-line options win, both
ways for on/off settings (`--source` keeps the sources of a project that sets
`include-source = false`), and a compression flag on the command line also replaces the other
one set in the project:

```toml
[tool.fsbpyr]
entry = "mytool"
output = "dist/mytool"
requirements = "requirements.txt"
extras = ["fancy"]
include-source = false       # same as --no-source
fast-compression = true      # same as --fast-compression
best-compression = true      # same as --best-compression (not both)
fs-packages = ["mypkg.templates"]
allow-system-libs = ["libGL.so.1"]
interpreter-reexec = true    # same as --interpreter-reexec
```

## Leaving out sources

By default an executable holds every module twice: as compiled bytecode, which is what runs, and
as its `.py` source, which is only read when something asks for it. `--no-source` (or
`include-source = false`) leaves the sources out of the app and its dependencies.

**What you gain.** Size. For aiagent (79 distributions) the executable shrinks from 157.4 MB to
140.0 MB. Startup does not change (measured): sources are never read on the normal path.

**What changes.** Everything that needs the text of a module:

- **Tracebacks** still name the file and line, but show no source line under it:

  ```
  Traceback (most recent call last):
    File "/usr/local/bin/mytool/mytool/cli.py", line 23, in main
  ValueError: invalid literal for int() with base 10: 'not a number'
  ```

  With sources the same traceback also shows the line `int("not a number")` under it.
- **`inspect.getsource()`** raises `OSError: could not get source code`. Libraries that read
  the source of your functions at run time break, `torch.jit.script` for example.
- **Reading a `.py` file as data** (`importlib.resources.files("pkg") / "x.py"`, or a package
  in `fs-packages` that expects its `.py` files on disk) fails with `FileNotFoundError`: the file
  is not in the executable.

The standard library keeps its sources either way: they come with the runtime, so tracebacks
through stdlib code still show source lines.

**When to use it.** For a release where size matters and the app does not read its own
source. Test the `--no-source` build before shipping it: run the app's main commands and a
failing path, since a library that calls `inspect.getsource()` only fails when that code runs.
Keep sources for builds you debug.

## Inspecting an executable

```
fsbpyr inspect BINARY [--json] [--verify] [--all-modules]
```

Shows the app and its version, the fsbpyr that built it (`fsbpyr --version` prints the same
form: `0.4.0-1a2b3c4d`), Python and glibc, the file's digest, the bundled shared libraries and
extension modules, and the top-level modules of each distribution, in as many columns as the
terminal fits. `--all-modules` lists every module, `--json` prints everything as JSON, and
`--verify` recomputes the digest.

## Shell completion and the man page

fsbpyr prints both. They come from the same definitions as `--help`, so all three always
agree:

```
fsbpyr completion bash > ~/.local/share/bash-completion/completions/fsbpyr
fsbpyr completion zsh > ~/.zfunc/_fsbpyr     # a directory in $fpath, before compinit
fsbpyr completion fish > ~/.config/fish/completions/fsbpyr.fish
fsbpyr man > ~/.local/share/man/man1/fsbpyr.1
```

The shell asks fsbpyr for the words as you type, so after `--entry` and `--extra` it offers
the console scripts and extras of the project being built: the directory named before them,
or the current one.

## Integrity

Every executable checks its whole file before it does anything else and refuses to start if a
single byte changed (damage to the launcher's own code can also make it crash before it gets
to say so):

```
fsbpyr: cannot start /usr/local/bin/app: integrity check failed: the file was modified or damaged
```

The check hashes the file with SHA-256 on up to 8 threads, never more than the cores it may
use. On a Core Ultra 9 285 with the file in the page cache it takes 2.0 ms for a 28 MB app and
8.9 ms for 148 MB (one core: 6.9 and 38 ms; without SHA instructions: 7.1 and 30 ms). The first
start after a reboot also reads the whole file from disk. `FSBPYR_TRACE=1` reports the time as
`integrity_ns`. Files extracted to the cache are hashed again by every process, once, before it
first uses them, with the same digest and the same multithreaded routine; a changed one is
reported and restored.

The digest can be published and checked with `fsbpyr inspect --verify BINARY` (plain `inspect`
shows the stored value) or with standard tools:

```sh
head -c -32 app | split -b 1M --filter='openssl dgst -sha256 -binary' | sha256sum
split -b 1M --filter='openssl dgst -sha256 -binary' < cached-file | sha256sum
```

Measured timings, release by release, are in `docs/performance.md`.

What the check cannot stop is someone who can write the file and rewrites the digest too, or
patches the check out: the code that checks is in the same file. Proving where an executable
came from needs a signature checked with a key from outside it; signed releases are planned.

## At run time

Pure-Python apps run straight from the executable. Native extensions, and the packages listed
in `fs-packages` (plus a few known ones such as certifi, botocore, litellm and torch), are extracted
on first use into `${XDG_CACHE_HOME:-~/.cache}/<app>`, or `$FSBPYR_CACHE_DIR` when set; resources
an app asks for as a path are extracted there too. With `--best-compression` that first use
takes longer, since xz decodes more slowly than zstd: the launcher decodes each library in
pieces of 4 MiB on up to 8 threads (numpy's first import: about +70 ms; onnxruntime's together
with numpy's: about +80 ms); later runs are as fast as with zstd. Since what is extracted there
is loaded as code, the cache directory must be yours, and it and every directory leading to it
owned by root or by you and writable by no other user (not by others, nor by a group other
than your primary one) unless sticky, as `/tmp` is; symlinks are followed, and must be root's
or yours. No other user can then replace or redirect any part of it. A cache that fails this
is refused, and the error names the directory at fault. (In a user namespace, a sandbox,
directories whose owner is outside it show as nobody's; they count as root's where nobody is
no user inside, and only above a directory anyone may add to, such as `/tmp`.) Environment variables the executable reads:

| Variable | |
|---|---|
| `FSBPYR_CACHE_DIR` | cache directory |
| `FSBPYR_GC=1` | collect the app's cache instead of running the app (see below); `0`: never collect it in the background |
| `FSBPYR_TRACE=1` | print what the run loaded and extracted, and how long the integrity check took, as JSON on stderr |

### Helper interpreters

In an executable, `sys.executable` is the app. multiprocessing works with every start method:
the app recognizes the children it re-executes. Other code that starts a helper interpreter
that way, `subprocess.run([sys.executable, "-m", "pkg.tool", ...])` or `-c CODE`, gets the
app instead, unless it was built with `--interpreter-reexec`. Then such a child runs the
module (from the executable) or the code as Python would, interpreter flags such as `-u` or
`-B` accepted and ignored. Only a child of the app itself does: the same command line from a
shell, a script or sudo runs the app, with these arguments, so a sudo rule for the app never
hands out an interpreter. The option is off by default because an app that starts itself with
options of its own named `-m` or `-c` would run Python instead.

### The cache cleans itself

What a running app extracted stays in use for the app's whole life; what no process has used
for 7 days is removed. A collection is due a day after the last; each start checks that with
one `stat`, and when one is due a detached process at idle priority collects, so the start is
not slowed. When the file system has less than 10% free, a collection is due an hour after the
last, and everything not in use goes. `fsbpyr` checks its own cache (runtimes, Python
distributions, sysroots and downloads) the same way when it runs, keeping what it builds with.
`fsbpyr cache gc` collects its own cache and every app cache now; `FSBPYR_GC=1 app` collects
that app's.

Only a cache root with the marker `.fsbpyr-cache` is ever collected, and only what fsbpyr
made there. The marker is written into a new (empty) root, or one that holds nothing but a
cache of an earlier version: a directory of yours that `FSBPYR_CACHE_DIR` names is never
marked. To have such a directory collected all the same (a volume of its own, with its
`lost+found`, say), create the marker yourself: `touch "$FSBPYR_CACHE_DIR/.fsbpyr-cache"`.

## Versions

Releases are tagged `vX.Y.Z`: a fix bumps the patch level, a new feature the minor version, a
rewrite the major version. `fsbpyr --version` prints `X.Y.Z-<commit>` for a tagged build and
`untagged-<commit>` otherwise.

The design, its decisions and the progress of each phase are in
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md); the executable format is in
[`docs/format.md`](docs/format.md).
