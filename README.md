# fsbpyr

Fast single-binary Python CLI runtime: `fsbpyr build` turns a Python project (uv or pip) into one
executable that carries CPython 3.14, the project and its locked dependencies. The executable
needs nothing on the target machine but Linux x86-64 with glibc 2.28 or newer: no Python, no
virtual environment, no installation step. Modules are imported straight from the file, so
startup stays close to plain CPython.

## Getting fsbpyr

fsbpyr builds itself into a single executable, and that executable is the easiest way to run
it: it carries its own runtime (CPython 3.14 and the launcher), so building apps with it needs
no compiler. From a source checkout:

```
uv run fsbpyr runtime build      # once: needs clang and lld 22 or newer, and network access
uv run fsbpyr build . -o ~/.local/bin/fsbpyr
```

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

| Option | |
|---|---|
| `--entry NAME` | console script name, `module` or `module:function` (default: the only script) |
| `-o, --output PATH` | where to write the executable |
| `--requirements FILE` | pinned requirements file, for pip projects without `pylock.toml` |
| `--extra NAME` | install a project extra (repeat) |
| `--fs-package NAME` | a package whose files must exist on disk (repeat; see below) |
| `--allow-system-lib SONAME` | let native code use this library from the host (repeat) |
| `--offline` | no network access during the build |
| `--no-source` | leave out `.py` sources; see [Leaving out sources](#leaving-out-sources) |
| `--fast-compression` | zstd level 3 instead of 9: a faster build, up to ~8% larger for large apps (the standard library's files of 64 KiB and more stay level 9) |
| `--best-compression` | native libraries as xz -6 instead of zstd 9: about 6.5% smaller for aiagent, a slower build, and a slower first run, which decodes them into the cache (see below); not with `--fast-compression` |
| `--hints` | also list packages that may read files next to `__file__` (a guess) |
| `--no-smoke` | skip the smoke test that runs the new executable once |

The same settings can live in the project's `pyproject.toml`; command-line options win (a
compression flag on the command line also replaces the other one set in the project). The
boolean settings below can only be switched on from the command line, so a project that sets
them in `pyproject.toml` changes them there:

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
with numpy's: about +80 ms); later runs are as fast as with zstd. Environment variables the
executable reads:

| Variable | |
|---|---|
| `FSBPYR_CACHE_DIR` | cache directory |
| `FSBPYR_TRACE=1` | print what the run loaded and extracted, and how long the integrity check took, as JSON on stderr |

## Versions

Releases are tagged `vX.Y.Z`: a fix bumps the patch level, a new feature the minor version, a
rewrite the major version. `fsbpyr --version` prints `X.Y.Z-<commit>` for a tagged build and
`untagged-<commit>` otherwise.

The design, its decisions and the progress of each phase are in
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md); the executable format is in
[`docs/format.md`](docs/format.md).
