# fsbpyr

Fast single-binary Python CLI runtime: `fsbpyr build` turns a Python project (uv or pip) into one
executable that carries CPython 3.14, the project and its locked dependencies. The executable
needs nothing on the target machine but Linux x86-64 with glibc 2.28 or newer (the baseline
every build targets and the tests run on; `fsbpyr inspect` shows the glibc an executable
actually needs, which can be older), plus the system libraries its native dependencies link
(often libstdc++ and libgcc_s; `fsbpyr inspect` lists them): no Python, no virtual
environment, no installation step.
Modules are imported straight from the file, so startup stays close to plain CPython.

## Quickstart

<!-- quickstart: tests/e2e/test_build.py runs these sh blocks -->
```sh
mkdir -p ~/.local/bin
curl -fLo ~/.local/bin/fsbpyr https://github.com/devitops-com/fsbpyr/releases/latest/download/fsbpyr
chmod +x ~/.local/bin/fsbpyr
```

Then, in a project whose `pyproject.toml` names a console script, such as this one for a
`main()` in `src/hello/cli.py`:

```toml
[project]
name = "hello"
version = "1.0.0"
requires-python = ">=3.14"

[project.scripts]
hello = "hello.cli:main"
```

```sh
~/.local/bin/fsbpyr build                 # writes dist/hello
dist/hello
~/.local/bin/fsbpyr inspect dist/hello
```
<!-- /quickstart -->

These lines call fsbpyr by its path: a new `~/.local/bin` is on `PATH` only from the next
login (Debian's and Ubuntu's `~/.profile` add it when it exists). The compressed download, the
attestation check and building fsbpyr from source are in [Getting fsbpyr](#getting-fsbpyr).

## Requirements

- **Build host:** Linux x86-64, with [uv](https://docs.astral.sh/uv/) on `PATH`
  ([D26](docs/IMPLEMENTATION_PLAN.md#2-key-decisions)): uv installs every project, pip
  projects too.
- **Project:** a console script (`[project.scripts]`, or the build backend's own table such as
  setuptools' `entry_points`), or `--entry` with a script's name, a module or
  `module:function`; and a `requires-python`, if it sets one, that admits 3.14.
- **Lock sources,** the first one found: a requirements file that `--requirements` or
  `requirements` names, `uv.lock` (the project's or its uv workspace's), `pylock.toml`,
  `requirements.txt`. With none, the build resolves the dependencies itself and warns that it
  is not reproducible. From `uv.lock`, dependency groups (`dev` among them) are not packaged.

## Supported targets

- **Python:** apps run CPython 3.14.8, the version every executable embeds, so a project's
  `requires-python` must admit 3.14.
- **Platform:** Linux x86-64 with glibc only. On a musl system such as Alpine the glibc loader
  the executable names does not exist, and the shell reports the executable as "not found"
  although the file is there.
- **Interpreter build:** CPython with the GIL only; the launcher refuses to build for a
  free-threaded CPython ([D35](docs/IMPLEMENTATION_PLAN.md#2-key-decisions)).
- **Planned** in [Phase 9](docs/IMPLEMENTATION_PLAN.md), after the MVP: Python 3.12 and 3.13
  (one fsbpyr per Python version), then Linux aarch64, musl and macOS arm64.

How an executable differs from a venv, what the target must provide, which distributions and
packages are tested, and what to do when an app fails: [docs/compatibility.md](docs/compatibility.md).

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

Offline and air-gapped builds: warm uv's cache with a build that has network access, then build
with `--offline`. Build backends get their build constraints from `[tool.uv]
build-constraint-dependencies` (read by `uv sync`, for `uv.lock` projects) or from
`UV_BUILD_CONSTRAINT` (read by `uv pip install`, for requirements and `pylock.toml` sources; see
`uv pip install --help`). The build passes its environment through to uv.

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
| `--fast-compression / --no-fast-compression` | zstd level 3 instead of 9 (the standard library's files of 64 KiB and more stay level 9): a faster build, up to ~9% larger for large apps (measured with 0.14.0 and 0.15.0, docs/performance.md) |
| `--best-compression / --no-best-compression` | native files (extension modules and shared libraries, nothing else) as xz -6 instead of zstd 9: about 11% smaller for an app with numpy and onnxruntime (measured with 0.20.0, docs/performance.md), no change for pure-Python apps, a slower build, and a slower first run, which decodes them into the cache; not with `--fast-compression` |
| `--hints / --no-hints` | also list packages that may read files next to `__file__` (a guess) |
| `--interpreter-reexec / --no-interpreter-reexec` | let the app's own children started as `sys.executable -m MODULE` or `-c CODE` run as Python |
| `--smoke / --no-smoke` | run the new executable once as a smoke test (default) or skip it |
| `--smoke-args ARGS` | also run the new executable once with ARGS (one string, split like a shell would), for example "--version", and fail the build unless it exits 0; only you know which arguments have no side effects |
| `--strict / --no-strict` | fail the build, leaving the output as it was, on any warning about the app and on an unlocked build; requirements files must pin and hash every line |
| `-v, --verbose` | print each uv command and let uv's error output through |

`fsbpyr build -h` shows the same list. [Leaving out sources](#leaving-out-sources) explains
`--no-source`, [At run time](#at-run-time) `--fs-package` and the cache that
`--best-compression` decodes into, and [Helper interpreters](#helper-interpreters)
`--interpreter-reexec`. The sizes are measured: `--fast-compression`'s on a torch app
([docs/performance.md](docs/performance.md#the-same-app-with-0140-and-0150-d39)),
`--best-compression`'s on the [`heavy`](tests/fixtures/projects/heavy) test app
([docs/performance.md](docs/performance.md#build-options-0200)).

The same settings can live in the project's `pyproject.toml`; command-line options win, both
ways for on/off settings (`--source` keeps the sources of a project that sets
`include-source = false`), and a compression flag on the command line also replaces the other
one set in the project:

```toml
[tool.fsbpyr]
entry = "mytool"
output = "dist/mytool"
requirements = "requirements.txt"  # pip projects only; overrides uv.lock
extras = ["fancy"]
include-source = false       # same as --no-source
fast-compression = true      # same as --fast-compression
# best-compression = true    # same as --best-compression (not both)
fs-packages = ["mypkg.templates"]
allow-system-libs = ["libcuda.so.1"]
interpreter-reexec = true    # same as --interpreter-reexec
smoke-args = ["--version"]   # same as --smoke-args "--version"
strict = true                # same as --strict
```

`--smoke-args` is off unless given. The build then runs the new executable once more with
those arguments, as `-o` (so `argv[0]` is the final path), with stdin from `/dev/null` and
a 120 s limit, and fails unless it exits 0; the error names the arguments and shows the app's
stderr. Only you know which arguments are safe, such as `--version` or `--help`.

## Failing a build on warnings

Without `--strict`, every warning leaves the exit status at 0. With it, `build` stops after
all warnings are collected and before it writes anything, so a file at `-o` stays as it was,
and exits 1 with the warnings in the message. This includes the "unlocked build" warning, so
a project without `uv.lock`, `pylock.toml` or `requirements.txt` fails. Three kinds of
warning do not count: the uv that differs from the one that built fsbpyr and git errors,
which describe the build host, and the ctypes guess about libraries no extension module
loads, which only `fs-packages` silences, and that changes how the app runs.

A `requirements.txt` counts as locked even when it is not pinned. Without `--strict`, a
line without `==` gets a warning. With it, uv also gets `--require-hashes`, so every line
needs `==` and a `--hash`. uv checks the hashes in a `pylock.toml` on its own,
`--strict` or not (a changed hash in a `pylock.toml` fails the build; verified with uv 0.13.1).

`--strict` does not check the glibc the executable needs. To fail unless it is 2.28 or
older (`jq -e` exits 1 on `false`):

```sh
fsbpyr build -o dist/mytool --strict
fsbpyr inspect --json dist/mytool | jq -e '.glibc | split(".") | map(tonumber) <= [2, 28]'
```

## Leaving out sources

By default an executable holds every module twice: as compiled bytecode, which is what runs, and
as its `.py` source, which is only read when something asks for it. `--no-source` (or
`include-source = false`) leaves the sources out of the app and its dependencies.

**What you gain.** Size. For the [`heavy`](tests/fixtures/projects/heavy) test app (15
distributions, most of their size native libraries) the executable shrinks from 77.1 MB to
73.7 MB, measured with 0.20.0 ([docs/performance.md](docs/performance.md#build-options-0200));
an app with more Python code saves more. Startup does not change (measured): sources are never
read on the normal path.

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
fsbpyr inspect BINARY [--json] [--verify] [--all-modules] [--files]
```

Shows the app and its version, the fsbpyr that built it (`fsbpyr --version` prints the same form:
`0.4.0-1a2b3c4d`), Python and glibc, the entry point, the system libraries the executable
needs from the target with the newest symbol version it uses of each (libstdc++'s `GLIBCXX`
and `CXXABI`, libgcc_s's `GCC`, libz's `ZLIB`), the uv that built it, how its content is
compressed (the zstd level and, with native files, `xz -6` after `--best-compression`), the
file's digest, the bundled shared libraries and extension modules, and the top-level modules
of each distribution, in as many columns as the terminal fits. `--all-modules` lists every module, `--json` prints
everything as JSON, and `--verify` recomputes the digest and also prints the file's plain
SHA-256 (`sha256` in the text and in `--json`), the hash a GitHub attestation or `sha256sum`
names. Without `--verify` it is left out, so plain `inspect` stays fast. It also shows the
versions of the libraries the runtime carries (OpenSSL, SQLite, zlib, expat, libmpdec) and the
lock the app was built from with its SHA-256, and `--files` lists every packaged file with its
kind, size and SHA-256 (from the distributions' RECORD files). The build information `--json`
prints under `build_info` is described in [docs/format.md](docs/format.md#build-information).

A build warns when a native file needs a `GLIBCXX`, `CXXABI`, `GCC` or `ZLIB` version newer
than the manylinux_2_28 policy allows, since such a library fails to import on a target with
an older libstdc++ (Debian 10, RHEL 8); the warning names the file and the version. The list
covers the native files an extension module loads, and the launcher.

The glibc shown (`glibc >= X.Y`) is the newest glibc symbol version the launcher and the
bundled native files use, so the oldest glibc this executable starts on. It can be older
than the 2.28 baseline: the launcher itself needs 2.25 (`getentropy`), so an app without
native files shows `glibc >= 2.25`, fsbpyr's own executable included. Native files from
wheels or from the glibc 2.28 sysroot need at most 2.28; one built against the host's glibc
can need more, and the build warns about it. Only 2.28 and newer is tested.

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
use. On a Core Ultra 9 285 with the file in the page cache it takes 1.7 ms for a 28 MB app,
11 ms for 244 MB and 47 ms for 1.07 GB (one core: 7.8, 84 and 311 ms; without SHA instructions
on one core: 46 ms, 0.4 s and 1.6 s;
[docs/performance.md](docs/performance.md#integrity-check-0200), measured at 0.20.0). The
first start after a reboot also reads the whole file from disk: 6 to 12 ms more for a 28 and a
77 MB app on an NVMe SSD ([cold start](docs/performance.md#cold-start-0200)).
`FSBPYR_TRACE=1` reports the time as `integrity_ns`. Files extracted to the cache are hashed
again by every process, once, before it first uses them, with the same digest and the same
multithreaded routine; a changed one is reported and restored.

The digest can be published and checked with `fsbpyr inspect --verify BINARY` (plain `inspect`
shows the stored value) or with standard tools:

```sh
head -c -32 app | split -b 1M --filter='openssl dgst -sha256 -binary' | sha256sum
split -b 1M --filter='openssl dgst -sha256 -binary' < cached-file | sha256sum
```

Measured timings, release by release, are in `docs/performance.md`.

What the check cannot stop is someone who can write the file and rewrites the digest too, or
patches the check out: the code that checks is in the same file. Proving where an executable
came from needs a signature checked with a key from outside it; signed releases are planned
after the MVP ([D31](docs/IMPLEMENTATION_PLAN.md#2-key-decisions)). Until then, a release's
origin is shown by its attestation: `gh attestation verify` checks that this repository's
release workflow built it from its tag ([Verifying a release](#verifying-a-release)).

### Attesting and verifying your app

A GitHub attestation records which workflow built a file from which commit. It is provenance
for your app and complements the outside-key signature planned after the MVP
([D31](docs/IMPLEMENTATION_PLAN.md#2-key-decisions)), which `inspect --verify` and
self-update are meant to check; it does not replace it. In a workflow whose job has
`id-token: write` and `attestations: write`, with fsbpyr and uv installed:

```yaml
- run: fsbpyr build . -o dist/myapp
- uses: actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8 # v4.2.2
  with:
    subject-path: dist/myapp
```

Whoever downloads the app checks it with

```
gh attestation verify myapp --repo ORG/REPO \
  --signer-workflow ORG/REPO/.github/workflows/WORKFLOW.yml
```

The attestation names the file's plain SHA-256, which `fsbpyr inspect --verify myapp` prints
as `sha256` (the `digest` line is fsbpyr's own, checked at every start). A complete workflow
is [.github/workflows/attest-example.yml](.github/workflows/attest-example.yml); it runs on
manual dispatch only. That the example's attestation is accepted by `gh attestation verify`
has not been tested yet.

## At run time

Pure-Python apps run straight from the executable. Native extensions, and the packages listed
in `fs-packages` (plus the known ones: babel, botocore, certifi, litellm, matplotlib and
torch), are extracted on first use into `${XDG_CACHE_HOME:-~/.cache}/<app>`, or
`$FSBPYR_CACHE_DIR` when set; resources an app asks for as a path are extracted there too.
With `--best-compression` that first use takes longer, since xz decodes more slowly than zstd:
the launcher decodes each library in pieces of 4 MiB on up to 8 threads (numpy's first import:
about +70 ms; onnxruntime's together with numpy's: about +80 ms; measured at 0.8.0, not
re-measured: [docs/performance.md](docs/performance.md#--best-compression-080)); later runs
are as fast as with zstd. Since what is extracted there is loaded as code, the cache directory
must be yours, and it and every directory leading to it owned by root or by you and writable
by no other user (not by others, nor by a group other than your primary one) unless sticky, as
`/tmp` is; symlinks are followed, and must be root's or yours. No other user can then replace
or redirect any part of it. A cache that fails this is refused, and the error names the
directory at fault. (In a user namespace, a sandbox, directories whose owner is outside it
show as nobody's; they count as root's where nobody is no user inside, and only above a
directory anyone may add to, such as `/tmp`.) Environment variables the executable reads:

| Variable | |
|---|---|
| `FSBPYR_CACHE_DIR` | cache directory |
| `FSBPYR_GC=1` | collect the app's cache instead of running the app (see below); `0`: never collect it in the background |
| `FSBPYR_TRACE=1` | print what the run loaded and extracted, and how long the integrity check took, as JSON on stderr |
| `FSBPYR_SELFTEST` | when set, import the entry point and check it instead of running the app (what `--smoke` does) |
| `FSBPYR_IMPORTTIME` | when set, print CPython's import-time table |

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

## Security

Report a vulnerability privately, as [`SECURITY.md`](SECURITY.md) describes. Every executable
carries its own CPython and the libraries linked into it (OpenSSL, SQLite, zlib and others),
so a security fix in them reaches an app only when the app is rebuilt with the fsbpyr release
that carries it; `SECURITY.md` says how soon such a release follows python-build-standalone's.

## Versions

Releases are tagged `vX.Y.Z`: a fix bumps the patch level, a new feature the minor version, a
rewrite the major version. `fsbpyr --version` prints `X.Y.Z-<commit>` for a tagged build and
`untagged-<commit>` otherwise.

Before 1.0 only the following is kept stable; everything else may change in any release:

- `fsbpyr inspect --json` carries a `schema` number (`docs/format.md`). A key that changes
  meaning increments it; a new key does not. CLI flags and `[tool.fsbpyr]` keys are not
  covered: they can change before 1.0, and a change that breaks them is marked **Breaking:**
  in the [changelog](CHANGELOG.md).
- What `fsbpyr self update` and an installer rely on across releases: the release assets
  `fsbpyr` and `fsbpyr.xz` and their `releases/latest/download/` URLs. The planned target- and
  version-named assets (`fsbpyr-3.12`, `fsbpyr-x86_64-linux-gnu`, `fsbpyr-3.12-aarch64-linux-musl`,
  each with its `.xz`), `install.sh` and the record of which asset an executable was released
  as come under the same promise when they ship, and `fsbpyr` and `fsbpyr.xz` then stay copies
  of the default executable.
- The trailer and the whole-file digest layout (`docs/format.md`) that `inspect --verify`
  reads: an older fsbpyr checks its download of a newer release in-process, so a later format
  version must keep that check working.

The design, its decisions and the progress of each phase are in
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md); the executable format is in
[`docs/format.md`](docs/format.md).

## Getting fsbpyr

fsbpyr builds itself into a single executable, and that executable is the easiest way to run
it: it carries its own runtime (CPython 3.14 and the launcher), so building apps with it needs
no compiler. Each [release](https://github.com/devitops-com/fsbpyr/releases) carries it,
built and attested by GitHub Actions:

```
mkdir -p ~/.local/bin
curl -fLo ~/.local/bin/fsbpyr https://github.com/devitops-com/fsbpyr/releases/latest/download/fsbpyr
chmod +x ~/.local/bin/fsbpyr
# recommended: checks that this repository's release workflow built it
gh attestation verify ~/.local/bin/fsbpyr --repo devitops-com/fsbpyr \
  --signer-workflow devitops-com/fsbpyr/.github/workflows/release.yml
```

The same executable also comes as `fsbpyr.xz`, half the download (14.0 MB instead of 28.9 MB
for 0.20.0), attested as well:

```
mkdir -p ~/.local/bin
curl -fL https://github.com/devitops-com/fsbpyr/releases/latest/download/fsbpyr.xz \
  | xz -d > ~/.local/bin/fsbpyr
chmod +x ~/.local/bin/fsbpyr
```

### Updating

```
fsbpyr self update
```

asks GitHub for the latest release and, when it is newer than the running executable, downloads
its `fsbpyr.xz`, unpacks it in-process, checks it as `fsbpyr inspect --verify` does before
anything runs it, and renames it over the executable (its real path, mode kept; a running
fsbpyr keeps its old file). The man page and the completions that `make install` put under the
same prefix (`<prefix>/bin/fsbpyr` and `<prefix>/share/...`) are written again; ones installed
elsewhere get a hint. It needs write access to the executable's directory and says so when it
has none. A developer build (`make build`, `make bootstrap`; `fsbpyr --version` starts with
`untagged`) refuses: download the release with the `curl` commands above. The update does not
check the release's attestation; `gh attestation verify` still does.

### Building from source

A source checkout needs `make` and [uv](https://docs.astral.sh/uv/). On Debian trixie it also
needs the Python pinned in `.python-version` (`uv python install 3.14.8`) and clang and lld 22
(`sudo apt-get install clang-22 lld-22`; trixie ships them since 13.7); on other systems,
[podman](https://podman.io) instead (install it with your system's package manager). The
targets install none of these: when one is missing, they name it and stop. `make help` lists
all targets.

```
make bootstrap   # once: from source to an installed fsbpyr
make build       # dist/fsbpyr, built by the installed fsbpyr
make install     # dist/fsbpyr, its man page and completions under ~/.local
```

**`make bootstrap`** takes a checkout to an installed fsbpyr. On **Debian trixie** it runs on
the host:

1. checks for uv and the Python pinned in `.python-version` (3.14.8), which uv may not
   download here (`UV_PYTHON_DOWNLOADS=never`), and stops naming what to install if either is
   missing;
2. installs the locked dependencies into the checkout's `.venv`;
3. builds the runtime (`fsbpyr runtime build`: the pinned CPython from python-build-standalone
   and a glibc 2.28 sysroot are downloaded, the launcher linked with clang and lld 22, the
   standard library compiled; a minute or two, cached in `~/.cache/fsbpyr`). Without clang and
   lld 22 it stops and says so;
4. builds fsbpyr in three stages: fsbpyr run from source builds `dist/stage1/fsbpyr`, that
   executable builds `dist/stage2/fsbpyr`, and that one builds `dist/stage3/fsbpyr`. Each
   stage must pass `fsbpyr inspect --verify` (its digest is recomputed over the whole file),
   and stages 2 and 3 must be byte-identical: builds are reproducible, so a difference means
   fsbpyr does not build itself faithfully, and bootstrap stops there;
5. copies stage 3 to `dist/fsbpyr` and runs `make install`.

On **any other Linux x86-64** steps 1 to 4 run in a Debian trixie podman image
(`Containerfile`: clang and lld 22 from Debian and uv from astral.sh, which installs the
pinned Python there), on the checkout mounted into it, and `make install` runs on the host. The
container is IPv4-only: on podman's default network, TLS handshakes from Python with GitHub's
download host time out. A podman volume, `fsbpyr-cache`, keeps uv's cache, its Python and the
runtime between runs. `FSBPYR_BOOTSTRAP=podman make bootstrap` uses the container on trixie too.

**`make build`** builds the checkout with the installed fsbpyr (`~/.local/bin/fsbpyr`, else
`fsbpyr` on `PATH`) into `dist/fsbpyr`, and runs the result once as a smoke test. The installed
executable carries its own runtime, so this needs no compiler, only uv. With no fsbpyr
installed it stops and says to run `make bootstrap`. The new executable's `--version` names
the `vX.Y.Z` tag on HEAD (`untagged-<commit>` otherwise).

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

`fsbpyr runtime list` shows the runtime embedded in the executable you run, if any, then the
runtimes in the cache. Running fsbpyr from a checkout or a wheel (rather than as its own
executable) always needs a runtime in the cache, and a new fsbpyr version may need a new one: the
build then says `no runtime for Python 3.14; run fsbpyr runtime build --python 3.14`, or use the
release binary, which carries its own.

### Verifying a release

Each release's `fsbpyr` is built by GitHub Actions from its `vX.Y.Z` tag and attested there.
Every release from 0.19.0 on stays published as it is: a version whose release fails is
burned, and the fix comes as the next version, never in its place.

The check needs curl, git, gh and uv, and uv must be the one that built the release: uv
builds the project's wheel, stamping its own version into the wheel's metadata, and installs
every distribution, so another uv can give other bytes from identical sources (a build with
any other uv warns). An executable records its uv (`fsbpyr inspect` shows it as "uv", `--json`
as `.uv`; 0.18.2 and older do not record it). The rebuild below compares it with yours: if they
differ, install that uv version and start the rebuild again.

The attestation comes first, before anything runs the file. It shows that this repository's
release workflow built the file from the latest release's tag:

```
tag="$(gh release view --repo devitops-com/fsbpyr --json tagName --jq .tagName)"
curl -fLo fsbpyr "https://github.com/devitops-com/fsbpyr/releases/download/$tag/fsbpyr"
gh attestation verify fsbpyr --repo devitops-com/fsbpyr \
  --signer-workflow devitops-com/fsbpyr/.github/workflows/release.yml --source-ref "refs/tags/$tag"
```

If it fails, stop there. Otherwise rebuild the file from that tag, with the uv it records:

```
chmod +x fsbpyr
want="$(./fsbpyr inspect fsbpyr | awk '$1 == "uv" { print $2; exit }')"
[ "$(uv --version | cut -d' ' -f2)" = "$want" ] || echo "install uv $want first: this uv differs"
git clone --branch "$tag" https://github.com/devitops-com/fsbpyr.git src
(cd src && ../fsbpyr build . -o ../rebuilt)
cmp fsbpyr rebuilt && echo identical
```

A rebuild checks the bytes themselves, at two levels:

- **(a) The payload.** The executable rebuilds itself from its own tag byte for byte, so
  fsbpyr's code and its dependencies are what the tag says. This cannot vouch for the launcher
  stub, CPython and the standard library: an fsbpyr executable builds with the runtime it
  carries, copying them from itself ([D4](docs/IMPLEMENTATION_PLAN.md#2-key-decisions)), so a
  changed stub would pass this check.
- **(b) The runtime.** Only an independent rebuild checks the stub, CPython and the standard
  library. It compiles the launcher from the tag's sources and links it with clang and lld 22
  (on Debian trixie, from the archive snapshot below); CPython, which the launcher links in, and
  the standard library, which is compiled to bytecode, come from the python-build-standalone
  archive that the tag pins by SHA-256. So (b) proves the stub, and that CPython and the
  standard library are that pinned build; it does not compile CPython. CI's release is
  `make build`'s output, so on a clean checkout of the tag `make bootstrap` and `make build`
  give the released bytes, with the release's uv and clang (`fsbpyr inspect` lists the stub's
  `.comment` lines as "toolchain"; they name the Debian clang that built the launcher). The
  launcher's bytes depend on that exact clang and lld: v0.19.0 and v0.20.0 were linked by
  Debian's `22.1.8-1~deb13u4`, which the snapshot `20261005T000000Z` of the Debian archive
  serves (a later clang may link other bytes). A release tag's CI run takes its
  packages from the snapshot in `tools/release/apt-snapshot`, and its log lists the image digest,
  the snapshot, the uv version and the versions of `clang-22`, `lld-22` and `xz-utils`. To
  install the same packages, point apt at the snapshot
  (`http://snapshot.debian.org/archive/debian/TIMESTAMP/` for trixie and trixie-updates,
  `.../debian-security/TIMESTAMP/` for trixie-security) before installing `clang-22 lld-22`,
  and use the uv named by `tools/release/uv.version` (release CI downloads that release's
  tarball and checks its SHA-256; `FSBPYR_UV_VERSION=<version>` makes `tools/bootstrap.sh`
  refuse any other uv). `make bootstrap` also installs the result, though,
  so run fsbpyr from source directly in the checkout above: it builds the runtime, then `stage1`,
  and `stage1` builds the file to compare. A runtime already in the cache under the same id is
  reused, not built again; an empty cache (`XDG_CACHE_HOME` set to a new directory) builds it
  afresh. (b) is checked on every tag by the `reproduce` job of the release workflow, which
  rebuilds from a second checkout (another path and user, other caches, umask 077) and whose
  stage 3 the `compare` job checks against the release build byte for byte; the release is
  published only if they are identical. The same two jobs run on every push to main and pull
  request, with the live mirror instead of the snapshot.

```
cd src
uv run --locked fsbpyr runtime build
uv run --locked fsbpyr build . -o ../stage1
../stage1 build . -o ../from-source
cd .. && cmp fsbpyr from-source && echo identical
```

An executable also records the version of the fsbpyr that built it (`fsbpyr inspect` shows it
as "built with"), so an fsbpyr of another version, or of an untagged commit, gives a different
file from the same sources, even when the launcher and the payload are otherwise the same. Such
a build works the same; only the bytes differ. Releases before 0.18.2 do not reproduce
themselves: their launchers were not linked reproducibly.
