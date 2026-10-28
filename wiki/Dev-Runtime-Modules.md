# Runtime modules

The package [`src/fsbpyr/runtime/`](../src/fsbpyr/runtime)
is the build-time side that obtains a runtime, caches it and cleans the cache. A runtime is
what every executable for one Python version, target and glibc baseline has in common: the
native stub (the [launcher](Dev-Launcher.md) statically linked with CPython from
python-build-standalone) and the compiled standard library. This page explains what a runtime
is, where its parts come from, how it is cached, held and collected, how the runtime embedded in
a released fsbpyr differs from a cached one, and how a build for glibc 2.28 (EL8) gets its
stub; then it describes each module. `fsbpyr runtime build`, `fsbpyr runtime list` and
`fsbpyr cache gc` are implemented here (their command-line side is in `cli.py`,
[Build modules](Dev-Build-Modules.md)).

## What a runtime is

`registry.Runtime` holds one runtime:

| Field | Content |
|---|---|
| `tag` | the PBS distribution's Python tag and the target triple, `<tag>-<triple>`; it seeds every app's native cache generation |
| `runtime_id` | `cp<XY>-<triple>-<12 hex digits>`, with `-sanitized` for the test variant (below) |
| `python_version`, `hexversion`, `bytecode_magic` | of the PBS interpreter, from its `PYTHON.json` |
| `extension_suffixes` | the suffixes the PBS interpreter imports extension modules by, asked of the interpreter itself |
| `stub` | the linked launcher's bytes |
| `modules`, `files` | the standard library as payload specs ([Payload modules](Dev-Payload-Modules.md)), with the licence notices as files |
| `toolchain` | the first `--version` line of the clang and lld that linked the stub (information only); empty for a release's glibc 2.28 stub, which was not linked here |
| `inputs` | the PBS release, name and SHA-256, the sysroot's identity and the layout version; every app records them in `build_info` as `runtime_inputs` |
| `components` | the versions of OpenSSL, SQLite, zlib, expat and libmpdec the interpreter reports, and that of the zstd the launcher links, read from PBS's `libzstd.a` (`pbs.zstd_version`; left out when the archive holds no single version string); every app records them as `runtime_components` |
| `baseline` | the glibc baseline the stub is linked for: `2.34` (`pins.DEFAULT_BASELINE`), from the Debian 12 sysroot, or `2.28`, from the Rocky Linux 8.10 sysroot, for EL8 hosts ([Runtimes for glibc 2.28 (EL8)](#runtimes-for-glibc-228-el8)) |

The runtime id is the first 12 hex digits of a SHA-256 over the launcher digest (the launcher's
sources and its compiler and linker flags for the target, `stub.launcher_digest`; aarch64's link
has one flag more, [`stub`](#stub)), the PBS archive's name and SHA-256, the sysroot's identity
and `LAYOUT_VERSION` (10; the comment beside it lists what each bump changed). A change to any
of them gives a new id, so an old runtime is never reused for new inputs. The two baselines have
different sysroots, so the runtimes of one Python for 2.34 and for 2.28 have different ids. The
clang and lld versions are not part of the id.

Every stub records the id it was linked as. `build_runtime` passes the id to `stub.build_stub`,
which compiles it in (`-DFSBPYR_RUNTIME_ID`, a define kept out of the flags the launcher digest
hashes, so the id does not depend on itself), and the launcher's `main.c` holds it as the string
`fsbpyr-stub-identity=<id>`, which LTO and stripping keep and nothing reads at run time.
`stub.stub_identity` reads it back out of a stub's bytes (`None` for a file without it). A build
that is handed a stub it did not link, the glibc 2.28 stub of a release, compares that id with
the one it computes from its own launcher sources, pins and sysroot, and refuses a stub whose id
differs ([Runtimes for glibc 2.28 (EL8)](#runtimes-for-glibc-228-el8)). A stub from a runtime of
the cache or from the running fsbpyr executable is not checked this way: the cache's runtime
directory is named by the id and its files are checked against `runtime.json`'s digests, and
`Runtime.from_executable` verifies the running executable's whole-file digest first.

## Where its parts come from

![Building a runtime: pins name the PBS archive and, per glibc baseline, the Debian 12 or Rocky Linux 8.10 packages, which are downloaded, verified and extracted; clang and lld link the launcher sources against PBS's static libpython and the sysroot into the stub, with the runtime id compiled in; PBS's interpreter compiles the standard library; Runtime.save writes stub, stdlib.fsbp and runtime.json into runtimes/<id>](images/runtime-build.svg)

`registry.build_runtime(python, baseline=...)` does the whole job, cached under the runtime id;
it is what `fsbpyr runtime build --python X.Y [--glibc 2.34|2.28] [--clang PATH]` runs
(`--glibc` defaults to 2.34):

1. **Pins.** `pins.pin_for(python, host triple)` names the python-build-standalone archive (URL
   and SHA-256); `pins.sysroot_for(triple, baseline)` names the packages of the glibc sysroot
   (Debian 12's for 2.34, Rocky Linux 8.10's for 2.28), before anything is downloaded. If
   `runtimes/<id>/runtime.json` exists already, the runtime is held and loaded instead.
2. **PBS.** `pbs.fetch` downloads the archive into `downloads/` (HTTPS only, checked against the
   pinned SHA-256 before it is renamed into place), extracts it with `tarfile`'s `data` filter
   into a staging directory, and renames that to `pbs/<name>`.
3. **Toolchain.** `toolchain.find_toolchain` finds a clang whose major version is at least the
   LLVM version of PBS's bitcode objects (read from `PYTHON.json`), with an `ld.lld` beside it,
   or checks the one given with `--clang`.
4. **Sysroot.** `sysroot.fetch_sysroot` downloads the pinned packages into `downloads/`, checks
   each against its SHA-256, extracts their files into `sysroots/<identity>` (a `.deb`'s
   `data.tar` member; an RPM's payload, which `rpm.payload_tar` turns into a tar archive, less
   what linking never reads), adds the `lib` and `lib64` links of Red Hat's layout for the EL8
   sysroot, and marks the tree complete with `.fsbpyr-sysroot`.
5. **Stub.** `stub.build_stub` copies the launcher's C sources out of the installed package and
   links them with clang (full LTO) and lld against PBS's `libpython.a`, the objects of the
   built-in extension modules that the archive lacks, PBS's static libraries and the sysroot,
   with the runtime id compiled in.
6. **Standard library.** `stdlib.fragment` compiles the standard library with PBS's own
   interpreter, finds the modules CPython imports at startup, and makes payload specs of
   modules, sources and data files; `notices.files` adds the licence notices.
7. **Save.** `Runtime.save` writes `stub`, `stdlib.fsbp` and `runtime.json` into a staging
   directory beside the target and renames it into place; then the new runtime is held.

[docs/performance.md, Building a runtime](../docs/performance.md#building-a-runtime)
gives the cost of step 5. A test variant built with AddressSanitizer and
UndefinedBehaviorSanitizer (`sanitize=True`) has its own id with the suffix `-sanitized`; only
the tests build it.

## How it is cached

The tool's cache root is `$FSBPYR_CACHE_DIR`, else `${XDG_CACHE_HOME:-~/.cache}/fsbpyr` (a
relative `XDG_CACHE_HOME` is ignored). The home directory `~` is `$HOME` when that is an
absolute path; when `HOME` is unset, empty or relative it is the home directory of the user's
passwd entry, so a service started without `HOME` still finds the cache. With no absolute path
there either, `registry.cache_home` fails with a `RuntimeCacheError`,
`cannot find the home directory; set HOME, XDG_CACHE_HOME or FSBPYR_CACHE_DIR`, which
`fsbpyr build`, `fsbpyr runtime build`, `fsbpyr runtime list` and `fsbpyr cache gc` print as
the one line `fsbpyr: error: <message>` before they exit 1, and the collection at a command's
start (below) skips the cache. The boot code (`native.home_directory`) and the launcher
(`due.c`) find an app's cache root by the same rule
([Boot modules](Dev-Boot-Modules.md#native-code-and-the-cache)).

`registry.secure_directory` creates the cache root with mode 0700, or checks that it is owned
by the user, removes group and other permissions from it, and checks the whole way to it from
`/` with the same rule the app caches use (`gc.way_problem`: every directory on the way owned
by root or the user and writable by no other user unless sticky, symlinks root's or the
user's). A cached stub ends up inside every executable built from it, so a cache another user
could write to would let them plant code.

| Under the cache root | Content | Complete when it holds |
|---|---|---|
| `runtimes/<runtime id>/` | `stub` (mode 0755), `stdlib.fsbp`, `runtime.json` | `runtime.json` |
| `pbs/<archive name>/` | the extracted PBS distribution | `python/PYTHON.json` |
| `sysroots/<identity>/` | the extracted sysroot packages | `.fsbpyr-sysroot` |
| `downloads/` | PBS archives, the sysroots' `.deb` and `.rpm` packages, the glibc 2.28 stubs a release fsbpyr downloaded (`<asset>-<runtime id>`, `stubs.py`) and the fsbpyr variants for other Pythons that `fsbpyr build` fetched (`dispatch.py`, [Build modules](Dev-Build-Modules.md)) | |
| `.fsbpyr-cache` | the marker that allows collection | |
| `.gc-tool`, `.trash-tool` | the collection's stamp and trash | |

`stdlib.fsbp` is a bare payload in the executable format (header, tables, blob data; no stub
and no trailer) with block packing off, so the zstd frames are kept as built and every app
build packs them into its own blocks. `runtime.json` records the fields above, the fsbpyr
version that built the runtime, and the integrity digests of `stub` and `stdlib.fsbp`.
`Runtime.load` refuses a runtime whose files are missing, whose layout version differs, or
whose files do not match those digests; a `runtime.json` without `baseline` counts as the
default, 2.34.

Each item (a runtime, a PBS distribution, a sysroot) is written into a staging directory and
renamed into place, so it appears complete or not at all; when two processes race, a complete
item that appeared first wins and is kept. A downloaded stub is written under a temporary name
in `downloads/` and renamed into place the same way.

## Held and collected

The tool's cache follows the rules of the app caches' collector,
[`_fsbpyr_rt.gc`](Dev-Boot-Modules.md#gc), which `hold`, `collect` and `registry` import from
`src/fsbpyr/boot/_fsbpyr_rt/gc.py`: that module uses the standard library only, so the tool can
run it outside an executable.

- **Held.** `hold.hold(directory)` takes an item for the life of the process: it opens the
  item's `.lock`, holds it with a shared `flock`, and touches its modification time when it is
  a day old (or dated in the future), so the lock's time is when the item was last used. It
  also writes the marker into a root that holds nothing but fsbpyr's names (`gc.mark`). An
  item that was collected while the lock was waited for is reported as gone, and the caller
  makes it again; an item that cannot be locked (a read-only cache, a file system without
  locks) is used unheld. `build_runtime` and `find_runtime` hold the runtime they use,
  `pbs.fetch` the PBS distribution and `sysroot.fetch_sysroot` the sysroot; downloads are not
  held.
- **Collected.** `collect.collect_tool(root)` runs `gc.run` with the stamp `.gc-tool` and the
  trash `.trash-tool`. The app collector's are `.gc` and `.trash`: the tool's cache shares its
  root with the app cache of the fsbpyr executable itself, whose app name is `fsbpyr`, so
  `gc.py` counts the tool's names as its own too (`gc.TOOL`). Only a root with the marker is
  collected. Nothing the running fsbpyr builds with is removed (`collect.needed`: the runtime
  ids every pin gives, for each glibc baseline, sanitized ones included, the PBS names, the
  sysroot identities of both baselines and their downloads). Every other item whose name is one
  fsbpyr gives is taken with an exclusive lock and renamed into the trash if nobody holds it and
  it was unused for 7 days, or, when the file system has less than 10% free, if nobody holds it
  at all; downloads go under the same rule, their modification time standing for their last
  use. Staging directories and partial downloads a day old are a killed process's and go too.
  The trash is emptied at the end. The downloaded glibc 2.28 stubs and the fetched variants
  have names that `collect.NAMES` does not list, so no collection removes them (a test checks
  this for the stubs).
- **When.** `fsbpyr build`, `fsbpyr inspect`, `fsbpyr runtime build` and `fsbpyr runtime list`
  call `collect.collect_when_due` at their start: it collects a day after the last run (an
  hour when the file system has less than 10% free, at once when the last run's stamp is in
  the future), and never when `FSBPYR_GC` is set. Its failure never stops the command
  (`cli._collect_when_due`): without a home directory, on a read-only file system or with a
  cache it may not write nothing is collected, and another `OSError` is printed.
  `fsbpyr cache gc` collects the tool's cache and then every app cache `collect.app_caches`
  finds, printing a report for each, and exits 1 if anything could not be removed. With
  `FSBPYR_CACHE_DIR` set it needs no home directory; without either it reports the missing home
  directory (above) and exits 1.

## Embedded and cached runtimes

A released fsbpyr is itself a single-file executable built by fsbpyr: its stub and its
standard library are a runtime, which it hands on to what it builds, a new fsbpyr included
([Self-hosting](Dev-Self-Hosting.md)). `registry.running_executable` returns `/proc/self/exe` when
`_fsbpyr` is a built-in module, that is, when the code runs inside such an executable, and
`None` otherwise.

- **Embedded.** `Runtime.from_executable(path)` opens the executable with the payload reader,
  verifies its whole-file digest first (what goes into every app it builds must be what was
  built), and takes from it the stub prefix (the stub with its zero padding), the modules and
  files that belong to no distribution, minus the boot packages `_fsbpyr_boot` and
  `_fsbpyr_rt` (regenerated for every build), and the runtime's identity, suffixes, inputs and
  components from its `build_info`; its baseline is that of the pinned sysroot whose identity
  `runtime_inputs` names (the default for an identity no pin has). Stored zstd frames are copied
  verbatim and block members come back as content that the next build packs again: no
  compiler, no download, no cache.
- **Cached.** `find_runtime(python, baseline=...)` looks in `runtimes/` under the runtime id of
  the pin for this host and that baseline, holds the directory and loads it. It never builds:
  without a cached runtime it fails with a message that names
  `fsbpyr runtime build --python X.Y` (with `--glibc 2.28` for that baseline) and, for the
  default baseline only, the release binary, which carries its own.
- **Choice.** `select_runtime(python, baseline=...)` takes the embedded runtime when its Python
  version and its glibc baseline match. When the Python matches and the baseline does not, a
  released fsbpyr pairs its embedded standard library with the stub of its release for that
  baseline (`stubs.paired`); a stub it cannot get is an error, not a fall-back to the cache.
  Otherwise, and in a developer build, it takes the cache.
  `fsbpyr build` calls it with the Python fsbpyr itself runs on, the build's `glibc`
  (`--glibc`, else `glibc` in `[tool.fsbpyr]`, else 2.34) and its `offline` (`--offline`: the
  stub must be placed or cached); a project that needs another Python is handed to the matching
  fsbpyr release ([Build modules](Dev-Build-Modules.md)).
- **Listing.** `list_runtimes` (`fsbpyr runtime list`) gives the embedded runtime first, if
  any, then every runtime in the cache that has a `runtime.json`, each as its id, its Python
  version and its glibc baseline; the command prints one line per runtime,
  `<id>  Python <version>  glibc <baseline>`.

## Runtimes for glibc 2.28 (EL8)

`pins.BASELINES` lists two glibc baselines, `2.34` (the default) and `2.28`. The default stub
links against Debian 12's glibc 2.36 and needs no symbol newer than 2.34 (the docstring of
`SysrootPin.baseline`); EL8 hosts have glibc 2.28, so an app for them needs a stub linked
against EL8's own glibc, from Rocky Linux 8.10's packages. Only the stub differs: the
docstring of `stubs.py` states that the standard library's frames and modules do not depend on
the sysroot. A runtime for 2.28 comes from one of two places:

- **Built.** `fsbpyr runtime build --glibc 2.28` builds and caches it as any runtime is built,
  against the EL8 sysroot, and `find_runtime(python, baseline="2.28")` finds it. fsbpyr run
  from a source checkout and a developer build (an executable without a release tag) use only
  this.
- **Paired.** A released fsbpyr carries one runtime, linked for 2.34. For 2.28,
  `stubs.paired` keeps its embedded runtime (tag, Python, suffixes, standard library,
  components) and replaces the stub with the release's glibc 2.28 stub, the runtime id and
  `inputs` with those of the 2.28 runtime for the same pin, and the toolchain record with an
  empty one.

`stubs.stub_for` finds the release's stub, named by `vcs.release_stub_asset`
(`fsbpyr-stub-<X.Y>-<target>-glibc2.28`, for example
`fsbpyr-stub-3.14-x86_64-linux-gnu-glibc2.28`), in this order:

1. **Placed.** A file of that name, or the same name with `.xz` (then unpacked), in the
   directory of the path fsbpyr was run by (looked up on `PATH` when it was run by a bare name;
   links are kept, so a file beside a link is found) or of its real path (`stubs.own_paths`).
   This serves machines without access to GitHub. A variant for another Python that
   `fsbpyr build` hands a project to runs from `downloads/` in the cache (`dispatch.variant`);
   `dispatch.run` passes it the paths of the fsbpyr that handed it the build in
   `FSBPYR_DISPATCHED_BY` (joined with `:`, absolute ones only), and it looks beside those
   first, where the user places stubs, then beside itself.
2. **Cached.** `downloads/<asset>-<runtime id>` in the tool's cache, named by the runtime id the
   stub must carry rather than by the release, so an update that leaves the id unchanged reuses
   the download (an entry that a release before 0.28.0 cached as `<asset>-<tag>` is not found
   and costs one more download). A cached file that does not carry the expected runtime id is
   deleted and fetched again.
3. **Downloaded.** From the companion release `el8-vX.Y.Z` (`vcs.release_stub_tag`), since a
   published release takes no new assets, below `$FSBPYR_RELEASE_DOWNLOADS`, by default
   `https://github.com/devitops-com/fsbpyr/releases/download`. `SHA256SUMS-glibc2.28`
   (`vcs.release_stub_sums`), in `sha256sum`'s format over the unpacked stubs, names the
   SHA-256 of the asset; `<asset>.xz` is unpacked as it arrives, checked against that SHA-256,
   checked for the runtime id, and only then kept in `downloads/`. With `--offline` nothing is
   downloaded: the build stops with `--offline: the <asset> is not placed or cached, and nothing
   is downloaded; place <asset> (or <asset>.xz) next to <path>, or build once without --offline
   to cache it`.

Whichever it is, the stub must carry the runtime id that this fsbpyr computes from its own
launcher sources and flags, the PBS pin, the EL8 sysroot pin and the layout version
(`registry.runtime_id(pin, baseline="2.28")`). A stub for another Python or target, or linked
from other launcher sources or pins, is refused: `<where> is not the stub this fsbpyr (vX.Y.Z)
needs: it is the stub of runtime <id>, and this fsbpyr expects <id>; use the <asset> of release
el8-vX.Y.Z` (or `it carries no runtime id: it is no stub of an fsbpyr release`). The id holds
no fsbpyr version: releases whose launcher, pins and layout version are the same compute the
same id (0.26.0 and 0.27.0 do), and each accepts the other's stub. 0.28.0 changed every runtime
id (new PBS pins, launcher sources and aarch64 link flags, layout version 10), so its stubs are
new ones. A placed or cached stub is accepted on that id alone, which does not detect a tampered
file ([Security](Dev-Security.md)). A file over 256 MiB is refused as no stub. When the
companion release, its sums file or the asset is missing, the build stops with
`release el8-vX.Y.Z (the glibc 2.28 stubs of vX.Y.Z) does not exist or has none for this
Python`, followed by how to place the file; a server that cannot be reached, or a download cut
short (`http.client.IncompleteRead`), gives
`release el8-vX.Y.Z's <asset> could not be fetched: ...; to build without the network, place
<asset> next to <path>`. `<path>` in these messages is the first of `own_paths`: for a variant,
the fsbpyr that handed it the build.

`fsbpyr self update` leaves the stubs placed beside the executable where they are and names
those that the new release's `--glibc` refuses (`stubs.refused_after_update`). It looks where
`--glibc` looks (`own_paths`, so beside the path fsbpyr was run by too) for files named like a
stub asset, plain or `.xz`. It cannot compute the new release's runtime id, so a stub counts as
current when its SHA-256 is listed in the `SHA256SUMS-glibc2.28` of the new release's companion;
for the others it warns that `--glibc` refuses them unless they carry its runtime id: place the
new release's stubs there, or remove them and let `--glibc` download. When it cannot read that
file (no companion yet, no network, a download cut short), it names them all and says that
`--glibc` refuses them if the runtime id changed. The stubs are built from a cached 2.28 runtime
and published by
[`.github/workflows/el8.yml`](../.github/workflows/el8.yml)
with [`tools/release/el8-stub.py`](../tools/release/el8-stub.py).

Outside this package a build for 2.28 stages wheels for `manylinux_2_28`, checks the system
library versions its native files need against that policy's limits
(`natives.MANYLINUX_LIMITS`), rebuilds natives built from sdists on the host against the EL8
sysroot (`sysroot.build_flags` with the `redhat` layout) and records `glibc_baseline` in
`build_info`, which `fsbpyr inspect` shows ([Build modules](Dev-Build-Modules.md)).

## Modules

### `pins`

[`src/fsbpyr/runtime/pins.py`](../src/fsbpyr/runtime/pins.py)

The pinned inputs of every runtime. `PINS` lists one python-build-standalone archive per
Python version and target: CPython 3.12.16, 3.13.18, 3.14.10 and 3.15.2 for
`x86_64-unknown-linux-gnu` and `aarch64-unknown-linux-gnu`, release 20261026, flavour
`pgo+lto-full`, each with its SHA-256. The triples and flavours are those of `targets.py`, and a
test compares them (the comment above `PINS`).

Two tables name the sysroot packages per target, each with its SHA-256; `BASELINES` lists
their baselines, newest first, `DEFAULT_BASELINE` (`"2.34"`) and `"2.28"`:

| Table | Baseline | Packages | From |
|---|---|---|---|
| `SYSROOTS` | 2.34: glibc 2.36, `manylinux` 2.34, the newest symbol version the stub needs | `libc6`, `libc6-dev`, `libgcc-s1`, `libgcc-12-dev`, `linux-libc-dev` of Debian 12 | Debian's own pool, `deb.debian.org` |
| `EL8_SYSROOTS` | 2.28: glibc 2.28, `manylinux` 2.28 | `glibc`, `glibc-devel`, `glibc-headers`, `kernel-headers`, `libgcc` (BaseOS) and `gcc` (AppStream) of Rocky Linux 8.10 | `dl.rockylinux.org/pub/rocky/8.10` |

The comments above the tables give the reasons: Debian 12 is the oldest Debian release still
in LTS and its main suite no longer changes; Rocky Linux 8.10 is the final EL8 minor release,
whose repository keeps every 8.10 build at a stable URL and publishes each one's SHA-256; only
its `gcc` package has `crtbeginS.o`, `libgcc.a` and `libgcc_eh.a`.

Classes:

- `PbsPin(python, version, release, triple, flavor, sha256)`: one PBS archive; properties
  `name` (`cpython-<version>+<release>-<triple>-<flavor>`) and `url`.
- `SysrootPin(name, glibc, manylinux, packages, packaging="deb", layout="multiarch")`: the
  packages of one sysroot. `packaging` is `"deb"` or `"rpm"` and chooses the extractor;
  `layout` is `"multiarch"` (Debian's `usr/lib/<multiarch>`) or `"redhat"` (`usr/lib64`, with
  `lib` and `lib64` links into `usr`), and the EL8 pins have `"rpm"` and `"redhat"`. `identity`
  is the name (`debian12-glibc2.36-<arch>`, `rocky8-glibc2.28-<arch>`) plus 12 hex digits of a
  digest over the packages' SHA-256 values, so a changed pin never reuses an old sysroot or
  runtime; `baseline` is the glibc the apps are built for (`manylinux`), as a tuple.

Public functions:

- `pin_for(python, triple)`: the pin of a Python version for a target; `LookupError` for a
  target without pins, or a version without one (the message lists the supported ones).
- `sysroot_for(triple, baseline=DEFAULT_BASELINE)`: the sysroot pin of a target for a glibc
  baseline, from `SYSROOTS` for 2.34 and `EL8_SYSROOTS` for 2.28; `ValueError` for a baseline
  not in `BASELINES` (the message lists them) or a target without a pin.

Rules it enforces: only pinned versions, targets and baselines are built.

Imports: none inside fsbpyr.
Imported by: `fsbpyr.build`, `fsbpyr.config`, `fsbpyr.inspection`, `fsbpyr.natives`,
`fsbpyr.pyversion`, `fsbpyr.runtime.collect`, `fsbpyr.runtime.pbs`, `fsbpyr.runtime.registry`,
`fsbpyr.runtime.stubs`, `fsbpyr.runtime.sysroot`, `fsbpyr.system`.

### `pbs`

[`src/fsbpyr/runtime/pbs.py`](../src/fsbpyr/runtime/pbs.py)

python-build-standalone distributions: verified download, safe extraction, and what the link
needs. `fetch` downloads an archive once into `downloads/`, checks a cached archive against its
pin again before it uses it, extracts it with `tarfile`'s `data` filter (zstd-compressed tar,
through `fsbpyr.compat`), and requires `python/PYTHON.json`. `download` retries after a network
failure (four attempts, pausing 2, 5 and 10 seconds), never after an HTTP error, writes a
`.part` file and renames it only once its SHA-256 matches; `sysroot` downloads its packages
with it too. `PbsDist` reads the distribution's `PYTHON.json`; `link_inputs` turns its build
information into what the stub's link needs, and the interpreter itself is asked for its
extension suffixes and library versions; zstd's version is read from the archive `libzstd.a`.
When the interpreter exits with an error, the `PbsError` quotes the last 500 characters of its
stderr from a line boundary, with a note of how many earlier lines were left out
(`text.tail_lines`).

Classes:

- `PbsError`: a download, verification or layout problem.
- `LinkInputs(libpython, extra_objects, static_libs, system_libs)`: what to link besides the
  launcher's own sources.
- `PbsDist(root, info)`: an extracted distribution (the directory holding `PYTHON.json`), with
  `load(root)` and the properties `version`, `python`, `tag`, `triple`, `hexversion`,
  `bytecode_magic`, `llvm_major` (from `object_file_format`; a flavour whose objects are not
  LLVM bitcode is a `PbsError`), `interpreter`, `stdlib` and `include`; `builtin_modules()`
  lists the modules of the inittab that PBS compiled into libpython.

Public functions:

- `link_inputs(dist, archive_members)`: `libpython.a`, the object files of built-in extension
  modules that are not members of the archive, the static libraries they link, the system
  libraries, and PBS's `libzstd.a` when the archive has it (the launcher decodes zstd itself,
  and 3.12 and 3.13 have no `_zstd` module that would name it).
- `component_versions(interpreter)`: OpenSSL, SQLite, zlib, expat and libmpdec versions, as the
  interpreter reports them (run with `-I -c`, 60 s timeout).
- `zstd_version(dist)`: the version of the `libzstd.a` the launcher links, for every Python
  (3.12 and 3.13 have no `_zstd` to ask): the one string of the form `X.Y.Z` among the C
  strings (`.rodata.str1.1`) of its member `zstd_common.o`, which holds what
  `ZSTD_versionString()` returns (the docstring says why not the whole object); `None` when
  there is no readable archive, no such member or not exactly one such string.
  `build_runtime` records it as the component `zstd`.
- `extension_suffixes(interpreter)`: `importlib.machinery.EXTENSION_SUFFIXES` of the
  interpreter, in its order; the docstring explains why `PYTHON.json`'s list is not used.
- `sha256_of(path)`: a file's SHA-256, read 1 MiB at a time.
- `download(url, sha256, target)`: the verified, atomic download above.
- `fetch(pin, cache)`: download (once), verify, extract and hold the pinned distribution.

Rules it enforces: only `https://` URLs are downloaded; a download or a cached archive that does
not match the pinned SHA-256 is refused; the archive must contain `python/PYTHON.json`;
extraction applies the standard library's `data` filter.

Imports: `fsbpyr.elf`, `fsbpyr.compat`, `fsbpyr.text`, `fsbpyr.runtime.ar`,
`fsbpyr.runtime.hold`, `fsbpyr.runtime.pins` (all top).
Imported by: `fsbpyr.build`, `fsbpyr.cli`, `fsbpyr.runtime.notices`, `fsbpyr.runtime.registry`,
`fsbpyr.runtime.stdlib`, `fsbpyr.runtime.stub`, `fsbpyr.runtime.sysroot`.

### `toolchain`

[`src/fsbpyr/runtime/toolchain.py`](../src/fsbpyr/runtime/toolchain.py)

Finds a clang and lld able to link PBS's static libpython, whose objects are LLVM bitcode. The
search tries `clang-N` to `clang-(N+9)` and then `clang` on `PATH`, where N is the LLVM major of
the PBS objects, and takes the first whose `--version` reports a major of at least N and that
has `ld.lld-<major>` or `ld.lld` on `PATH` or in its own directory. A clang given with
`--clang` is checked the same way. Both baselines use the same toolchain.

Classes:

- `ToolchainError`: no suitable compiler or linker; the message suggests installing LLVM N or
  passing `--clang`.
- `Toolchain(clang, lld, major, sysroot=None)`: the programs, and the sysroot to link against
  (filled in by `build_runtime`).

Public functions:

- `clang_major(version_text)`: the major version in `clang --version` output, or `None`.
- `find_toolchain(min_major, explicit=None, search_path=None)`: the search above.
- `describe(toolchain)`: the first `--version` line of clang and of lld, for `runtime.json`.

Rules it enforces: clang at least as new as the LLVM that produced the bitcode, with a matching
lld.

Imports: none inside fsbpyr.
Imported by: `fsbpyr.cli`, `fsbpyr.runtime.registry`, `fsbpyr.runtime.stub`.

### `sysroot`

[`src/fsbpyr/runtime/sysroot.py`](../src/fsbpyr/runtime/sysroot.py)

The glibc sysroot the stub links against, so that the stub depends on the pinned glibc rather
than the build host's (the module docstring: linking against the host's glibc ties every app to
it). The sysroot is extracted from the pinned packages, Debian 12's for the default baseline and
Rocky Linux 8.10's RPMs for `--glibc 2.28`; it needs no container, and the same pins always give
the same tree. Each package is read once and checked against its SHA-256, so what is verified
is what gets extracted. Absolute symlinks are rewritten relative to the sysroot before the
standard library's `data` filter is applied. An RPM's files come through `rpm.payload_tar` and
the same filter, less the paths of `_RPM_SKIP`, which linking never reads (`etc/`, `var/`,
`usr/bin/`, `usr/sbin/`, `usr/libexec/`, `usr/share/`, `usr/lib/.build-id/` and the
character-set converters in `usr/lib64/gconv/`; the comment: gcc's compiler proper alone is
70 MB). For the `redhat` layout `fetch_sysroot` adds the links `lib -> usr/lib` and
`lib64 -> usr/lib64` that EL8's `filesystem` package makes, since the linker scripts name
`/lib64/libc.so.6` and, on aarch64, a loader below `/lib`.

The app build uses the same sysroots when dependencies built from their sdists on the build host
need a newer glibc than the baseline: `build_flags` points the host's C compiler at the
baseline's sysroot to build them again (`build.rebuild_on_baseline`).

Classes:

- `SysrootError`: the sysroot cannot be downloaded or extracted.

Public functions:

- `extract_deb(data, target, name)`: extract the `data.tar` member of a Debian package (an `ar`
  archive) into `target`.
- `extract_rpm(data, target, name)`: extract the files of an RPM package into `target`, the way
  a Debian package's are, less `_RPM_SKIP`.
- `fetch_sysroot(pin, cache)`: download (once), verify, extract each package with the extractor
  of `pin.packaging` into a staging directory, add the `redhat` layout's links, write
  `.fsbpyr-sysroot` last, rename into `sysroots/<identity>`, and hold.
- `build_flags(root, env, layout="multiarch")`: `CFLAGS` and `LDFLAGS` that point gcc or clang
  at the sysroot, followed by the environment's own. For `multiarch`: `--sysroot`, `-isystem`
  for `usr/include/<multiarch>`, `-B` for `usr/lib/<multiarch>` and the gcc directory
  `usr/lib/gcc/<multiarch>/<version>` (the last in sorted order), and `-L` for
  `usr/lib/<multiarch>` and `lib/<multiarch>` (a host without a multiarch name is a
  `SysrootError`). For `redhat`: `--sysroot`, `-B` for `usr/lib64` and the gcc directory
  `usr/lib/gcc/<triple>/<version>` (the last in sorted order), and `-L` for `usr/lib64`. Not
  `CC` (build probes run it as a bare command) and not C++ (the sysroot has no libstdc++).

Rules it enforces: every package matches its pinned SHA-256; a Debian package without a data
archive, or an RPM that `rpm` refuses, is an error; symlinks cannot point outside the sysroot;
a sysroot without `.fsbpyr-sysroot` is incomplete.

Imports: `fsbpyr.targets`, `fsbpyr.runtime.ar`, `fsbpyr.runtime.rpm`, `fsbpyr.runtime.hold`,
`fsbpyr.runtime.pbs`, `fsbpyr.runtime.pins` (all top).
Imported by: `fsbpyr.build`, `fsbpyr.cli`, `fsbpyr.runtime.registry`.

### `ar`

[`src/fsbpyr/runtime/ar.py`](../src/fsbpyr/runtime/ar.py)

A reader of Unix `ar` archives in the GNU long-name variant that llvm-ar writes. `stub` uses it
to list the members of PBS's `libpython.a` (so that only the built-in modules' objects missing
from it are added to the link), `sysroot` to take the `data.tar` member out of a `.deb`, and
`pbs` to read `zstd_common.o` out of PBS's `libzstd.a` (`pbs.zstd_version`).
The symbol tables (`/`, `/SYM64/`) are skipped and the long-name table (`//`) resolves names.

Classes:

- `ArError` (a `ValueError`): not a readable ar archive.

Public functions:

- `members(path)`: the regular members' names, in archive order.
- `read(path)`: `(name, content)` of the regular members.
- `parse(data, label)`: the same for an archive held in memory.

Rules it enforces: the archive magic, well-formed member headers (the terminator, a decimal
size), members inside the archive, valid long-name references, UTF-8 names.

Imports: none inside fsbpyr.
Imported by: `fsbpyr.runtime.pbs`, `fsbpyr.runtime.stub`, `fsbpyr.runtime.sysroot`.

### `rpm`

[`src/fsbpyr/runtime/rpm.py`](../src/fsbpyr/runtime/rpm.py)

Reads the payload of an RPM package, standard library only, for the EL8 sysroot. An RPM is a
96-byte lead, the signature header (padded to 8 bytes), the main header and the payload, a
compressed cpio archive in the "newc" format (magic `070701`). Of the main header only the two
payload tags are read, the payload format (1124, which must be `cpio`) and the compressor (1125,
which must be `xz` or `lzma`, what EL8 uses). The payload is converted to a tar archive so that
`sysroot` extracts it with `tarfile` and its safety filter, as it does a Debian package's. Not
read (the module docstring): the signatures, because the pins' SHA-256 values are what is
verified.

Member names are made relative and normalized; one that leaves the package is refused. Since
EL8's `filesystem` package makes `bin`, `sbin`, `lib` and `lib64` links into `usr` (merged
`/usr`), a name below one of them gets `usr/` in front (`libgcc` still names
`lib64/libgcc_s.so.1`). cpio stores the data of a set of hard links with the last name of the
set, tar needs it with the first, so the names of a set wait for the entry with the data, which
is written first, the others as hard links to it.

Classes:

- `RpmError` (a `ValueError`): not an RPM fsbpyr can read.

Public functions:

- `payload_tar(data)`: the files of the RPM `data` as a tar archive (PAX format).

Rules it enforces: the RPM and header magics; header entry counts and sizes within rpm's own
limits and inside the file, string tags inside their header; payload format `cpio`; compressor
`xz` (or `lzma`); a decompressed payload of at most 512 MiB whose stream is complete (the
comment: gcc's is 60 MB); well-formed cpio headers and entries that are not truncated;
only regular files, directories and symlinks (a device or other special file is refused); no
name outside the package; no incomplete set of hard links.

Imports: none inside fsbpyr.
Imported by: `fsbpyr.runtime.sysroot`.

### `stub`

[`src/fsbpyr/runtime/stub.py`](../src/fsbpyr/runtime/stub.py)

Compiles the launcher and statically links it against a PBS distribution. The launcher's files
(`LAUNCHER_FILES`: the C sources listed in `LAUNCHER_SOURCES`, ten files, their headers and the
vendored zstd headers) are read through `importlib.resources`, because they may live inside an
fsbpyr executable, and written into a temporary directory; clang runs there on relative paths,
and `-ffile-prefix-map` maps the PBS and sysroot directories to fixed names, so that neither the
temporary directory nor the cache's location reaches the binary. The fixed flags
(`COMPILE_FLAGS`, `LINK_FLAGS`) build with full LTO, stack protection and `_FORTIFY_SOURCE`,
warn about variable-length arrays (`-Wvla`) and stack frames over 8192 bytes
(`-Wframe-larger-than=8192`), and link with a non-executable stack, `-z relro -z now` and
stripping; they export only the `Py*` and `_Py*` symbols that extension modules link against,
link libgcc statically, and wrap `hypot` and `_dl_find_object` (in `glibc.c`) to keep the stub
within the glibc baseline. Against the EL8 sysroot, whose headers lack `struct dl_find_object`,
the `_dl_find_object` wrapper is compiled out (the comment at the top of `glibc.c`). The
bytecode magic is compiled in (`-DFSBPYR_BYTECODE_MAGIC`), and so is the runtime id the stub is
linked as (`-DFSBPYR_RUNTIME_ID`, see [What a runtime is](#what-a-runtime-is)), which is not one
of the fixed flags and so not part of the launcher digest. On aarch64 clang's compiler-rt
builtins are linked for the outlined atomics PBS's objects call, and the link adds
`-Wl,--fix-cortex-a53-843419` (`AARCH64_LINK_FLAGS`): on a Cortex-A53, an `adrp` in one of a
4 KiB page's last two instruction slots, followed by certain loads or stores, can compute a
wrong address, and lld patches each such sequence. PBS links its own aarch64 builds so since
20261026, but the stub is linked here from PBS's bitcode, so the flag is the stub's own (the
comment above the constant). The launcher digest hashes the flags of the target, so the flag is
part of the aarch64 runtime id.

Classes:

- `StubBuildError`: the compiler or linker failed, or a stack frame is over the limit.

Public functions:

- `compile_flags(sanitize=False)` and `link_flags(triple, sanitize=False)`: the fixed flags,
  with `AARCH64_LINK_FLAGS` added to the link for an `aarch64-` triple; the sanitized variant
  drops `-O2`, LTO, `_FORTIFY_SOURCE` and stripping, and adds `-O1`, ASan, UBSan (every
  undefined behaviour fatal), frame pointers and `-g`.
- `outlined_atomics(toolchain, triple)`: the compiler-rt builtins library on aarch64 (a
  `StubBuildError` naming `libclang-rt-<N>-dev` when clang has none), `None` on x86-64.
- `launcher_digest(triple, sanitize=False)`: SHA-256 over every launcher file and the flags
  for `triple`; part of the runtime id.
- `stub_identity(data)`: the runtime id a stub's bytes record (the text between
  `IDENTITY_MARKER`, `fsbpyr-stub-identity=`, and the next NUL), or `None` for a file that
  records none.
- `stub_command(dist, inputs, toolchain, sources, out, sanitize=False, atomics=None,
  identity=None)`: the clang command line; `identity` adds the `FSBPYR_RUNTIME_ID` define.
- `build_stub(dist, toolchain, out, sanitize=False, identity=None)`: link the stub into `out`,
  recording `identity`; returns every line the compiler and linker printed, which
  `build_runtime` shows. Without `identity` the launcher's own default, `none`, is recorded.

Rules it enforces: a failed link, or any "stack frame size" warning (LTO sizes frames again at
the link, where only a warning reports it), is a `StubBuildError`; a failed link's message
quotes the last 4000 characters of what the compiler and linker printed, from a line boundary
(`text.tail_lines`).

Imports: `fsbpyr.text`, `fsbpyr.runtime.ar`, `fsbpyr.runtime.pbs`, `fsbpyr.runtime.toolchain`
(all top).
Imported by: `fsbpyr.cli`, `fsbpyr.runtime.registry`, `fsbpyr.runtime.stubs`.

### `stdlib`

[`src/fsbpyr/runtime/stdlib.py`](../src/fsbpyr/runtime/stdlib.py)

Selects the standard library, compiles it with the target interpreter and finds the startup
modules. Everything under PBS's stdlib directory is taken except the directories in
`EXCLUDED_DIRS` (`test`, `idlelib`, `tkinter`, `turtledemo`, `ensurepip`, `__pycache__`,
`site-packages`, `lib-dynload`, `__phello__`) and `config-*`; `.py` files with valid module
names become modules, other files resources. The startup modules are those CPython imports
during initialization (`-X importtime` of an empty run) that are neither built-in nor frozen;
they get the STARTUP flag, which makes the launcher put them in CPython's frozen-module table,
and a `<frozen NAME>` file name. Compilation runs PBS's own interpreter (`-I -S`) in up to
`THREADS` worker processes, each given every `THREADS`-th file, so the bytecode is that of the
exact target CPython; the marshalled code is then canonicalized (`fsbpyr.bytecode`) and
compressed, on a worker pool when there are many modules. A file that does not compile for
this Python version is kept as a plain resource, with a warning. A worker or interpreter run
that fails is a `StdlibError` that quotes the last 2000 characters of its stderr from a line
boundary (`text.tail_lines`).

Classes:

- `StdlibError`: the standard library could not be compiled or analysed.
- `StdlibTree(modules, resources)`: `(name, relative path, is package)` of each module, and the
  relative paths of the other files.

Public functions:

- `collect(stdlib)`: the `StdlibTree` of a stdlib directory.
- `parse_importtime(stderr)`: the module names in `-X importtime` output, without its header
  line.
- `decode_compiled(stream, rels)`: the compile worker's output (a length word and marshal data
  per file, or a failure marker) as a mapping.
- `compile_files(interpreter, root, rels, filenames=None, warn=None)`: marshalled code of each
  file compiled by `interpreter`, in the order given; what the workers print goes to `warn`.
- `startup_names(interpreter)`: the startup modules.
- `fragment(dist, warn=...)`: the standard library as `Module`s (code with `Blob.code`, source
  with `Blob.zstd`) and `File`s (each module's source as a SOURCE file, other files as
  RESOURCE).

Rules it enforces: every startup module must be in the selection, else `StdlibError`; a worker
that fails, or output that is truncated or has trailing bytes, is an error.

Imports: `fsbpyr.bytecode`, `fsbpyr.compiler`, `fsbpyr.payload.format`, `fsbpyr.payload.spec`,
`fsbpyr.text`, `fsbpyr.runtime.pbs` (all top).
Imported by: `fsbpyr.cli`, `fsbpyr.runtime.registry`.

### `notices` and `licenses/`

[`src/fsbpyr/runtime/notices.py`](../src/fsbpyr/runtime/notices.py),
[`src/fsbpyr/runtime/licenses/`](../src/fsbpyr/runtime/licenses)

The third-party licence notices of what a runtime links. The names come from `PYTHON.json`:
the distribution's own licence and the `license_paths` of every built-in module's libraries;
`LICENSE.zstd.txt` is added for every Python, since the launcher links zstd whatever the Python
and PBS lists its text only for the `_zstd` module, which 3.12 and 3.13 lack (the comment in
`collect`). Each is taken from PBS's `licenses/` directory; where PBS ships none, fsbpyr
supplies the text for zstd (from `launcher/vendor/zstd/LICENSE`) and HACL\* (from
`runtime/licenses/`). `LICENSE.zlib-ng.txt` is left out (`NOT_LINKED`): the comment explains
that `libz.a` is classic zlib. The HACL\* text is added whenever a built-in module's objects come from HACL\*, which
carries no licence entry of its own. With fsbpyr's own `LICENSE` (from the installed
distribution, else the source checkout) the texts become RESOURCE files under
`_fsbpyr_licenses/` in the standard-library fragment, and so in every app, where
`fsbpyr inspect --licenses` reads them (`licensing.py`).

The data directory `licenses/` holds one file, `LICENSE.hacl.txt`, the MIT licence of HACL\*.

Classes:

- `Notices(texts, unshipped)`: file name to text, and the names of the linked libraries' texts
  that PBS does not ship.

Public functions:

- `collect(dist)`: the `Notices` of a distribution.
- `files(dist)`: the notices and fsbpyr's `LICENSE.fsbpyr.txt` as `File`s under
  `_fsbpyr_licenses/`.

Rules it enforces: fsbpyr's own licence must be found, else `PbsError`.

Imports: `fsbpyr.payload.format`, `fsbpyr.payload.spec`, `fsbpyr.runtime.pbs` (all top).
Imported by: `fsbpyr.licensing`, `fsbpyr.runtime.registry`.

### `registry`

[`src/fsbpyr/runtime/registry.py`](../src/fsbpyr/runtime/registry.py)

The runtime cache: build, save, load, locate and list runtimes, and the tool's cache root with
its checks. Everything in [What a runtime is](#what-a-runtime-is),
[How it is cached](#how-it-is-cached) and [Embedded and cached runtimes](#embedded-and-cached-runtimes)
above is implemented here. `BOOT_PACKAGES` (`_fsbpyr_boot`, `_fsbpyr_rt`) names the boot code
that every build regenerates and that is never runtime content.

Classes:

- `RuntimeCacheError`: no usable runtime (missing, incomplete, of an old layout, corrupt, a
  cache directory that fails the checks, or no home directory to find the cache in);
  `stubs.StubError` is one too.
- `Runtime`: the dataclass above, with `save(directory)`, `load(directory)` and
  `from_executable(path)`.

Public functions:

- `check_owner(path, owner, euid)`: refuse a directory owned by another user.
- `secure_directory(path)`: create or verify a cache directory (mode 0700, owned by the user,
  no group or other permissions, the way from `/` trusted, a symlink at `path` root's or the
  user's).
- `cache_home()`: `${XDG_CACHE_HOME:-~/.cache}`, with `~` found as described in
  [How it is cached](#how-it-is-cached).
- `cache_root()`: `$FSBPYR_CACHE_DIR`, else `cache_home()/fsbpyr`.
- `host_triple()`: the host target's triple, or `RuntimeCacheError` on an unsupported host.
- `runtime_id(pin, sanitize=False, baseline=DEFAULT_BASELINE)`: the id above, over the sysroot
  pin of `baseline`.
- `runtime_inputs(pin, baseline=DEFAULT_BASELINE)`: the `runtime_inputs` record, with the
  sysroot identity of `baseline`.
- `running_executable()`: `/proc/self/exe` inside an fsbpyr executable, else `None`.
- `select_runtime(python, cache=None, baseline=DEFAULT_BASELINE, offline=False)`: the embedded
  runtime if its Python and baseline match; for its Python and another baseline, `stubs.paired`
  (with `offline`: a placed or cached stub only), when that gives a runtime; else
  `find_runtime`.
- `find_runtime(python, cache=None, sanitize=False, baseline=DEFAULT_BASELINE)`: the cached
  runtime of that baseline, held and loaded.
- `build_runtime(python, cache=None, clang=None, log=print, sanitize=False,
  baseline=DEFAULT_BASELINE)`: the steps above, against the sysroot of `baseline`; the
  components are the interpreter's and, when `pbs.zstd_version` finds one, zstd's.
- `list_runtimes(cache=None)`: `(runtime id, Python version, glibc baseline)` of the embedded
  and the cached runtimes.

Rules it enforces: the cache directory checks; a runtime's files must match the digests in its
`runtime.json` and its layout version must be the current one; an embedded runtime is taken
only from an executable that passes its integrity check, has the current format version and
records its runtime.

Imports: `fsbpyr`, `fsbpyr.targets`, `fsbpyr.boot._fsbpyr_rt.gc`, `fsbpyr.payload.convert`,
`fsbpyr.payload.format`, `fsbpyr.payload.reader`, `fsbpyr.payload.spec`,
`fsbpyr.payload.writer`, `fsbpyr.runtime.notices`, `fsbpyr.runtime.pbs`, `fsbpyr.runtime.pins`,
`fsbpyr.runtime.stdlib`, `fsbpyr.runtime.stub`, `fsbpyr.runtime.sysroot`,
`fsbpyr.runtime.hold`, `fsbpyr.runtime.toolchain` (all top); `fsbpyr.runtime.stubs` (lazy, in
`select_runtime`, since `stubs` imports this module).
Imported by: `fsbpyr.assemble`, `fsbpyr.build`, `fsbpyr.cli`, `fsbpyr.dispatch`,
`fsbpyr.runtime.collect`, `fsbpyr.runtime.stubs`.

### `stubs`

[`src/fsbpyr/runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py)

The stub of a released fsbpyr for another glibc baseline, as described in
[Runtimes for glibc 2.28 (EL8)](#runtimes-for-glibc-228-el8). The names come from `fsbpyr.vcs`:
`release_stub_asset(python, target, glibc)` the asset,
`release_stub_sums(glibc)` the sums file `SHA256SUMS-glibc<glibc>`, and
`release_stub_tag(glibc, tag)` the companion release, `el8-<tag>` for 2.28 (`ValueError` for a
baseline without one). Downloads go through `selfupdate`'s `download_text` and `unpack`, whose
`NotFound` (the server has no such file) tells a release without stubs from a network failure.
`RELEASES` is the releases page the error messages name.

Classes:

- `StubError` (a `registry.RuntimeCacheError`): the release's stub for the baseline cannot be
  had, or is not the right one.

Public functions:

- `own_tag()`: the tag of this release, `vX.Y.Z`, from the version its build recorded; `None`
  for a developer build.
- `own_paths(argv0=None, real=None)`: in a variant run by `dispatch.run`, first the paths of the
  fsbpyr that dispatched to it (`FSBPYR_DISPATCHED_BY`, absolute entries only); then the path
  this executable was run by (looked up on `PATH` when run by name) and, if it differs, its real
  path.
- `refused_after_update(tag, paths)`: the stubs placed beside `paths` that the fsbpyr of
  release `tag` would refuse (those whose SHA-256 the companion release's sums file does not
  list), and whether that is known; all of them, unknown, when the sums file cannot be read.
  `fsbpyr self update` names them (`selfupdate`).
- `stub_for(python, baseline, tag, cache=None, offline=False)`: the stub of release `tag` for
  `python` and `baseline` with this fsbpyr's runtime id: placed, else cached, else downloaded
  from the companion release and kept; `offline` downloads nothing.
- `paired(own, baseline, cache=None, offline=False)`: the runtime `own` with that stub; `None`
  for a developer build.

Rules it enforces: a stub is used only if it carries the runtime id this fsbpyr computes for the
baseline; a download only if it matches the SHA-256 that the companion release's sums file
lists, and it is kept only after both checks; nothing over 256 MiB is read as a stub; a
developer build never downloads a stub, nor does an `offline` one.

Imports: `fsbpyr.dispatch`, `fsbpyr.selfupdate`, `fsbpyr.targets`, `fsbpyr.vcs`,
`fsbpyr.runtime.pins`, `fsbpyr.runtime.registry`, `fsbpyr.runtime.stub` (all top).
Imported by: `fsbpyr.dispatch` (lazy, in `run`), `fsbpyr.runtime.registry` (lazy),
`fsbpyr.selfupdate` (lazy, for `self update`'s warning about placed stubs).

### `hold`

[`src/fsbpyr/runtime/hold.py`](../src/fsbpyr/runtime/hold.py)

Marks items of the tool's cache as in use, as described in [Held and collected](#held-and-collected).
The locks are kept in a module-level table until the process ends; a second `hold` of the same
item in the process returns at once.

Public functions:

- `hold(directory)`: take `<root>/<kind>/<name>` for this process; `False` if it is gone or was
  collected while the lock was waited for.

Rules it enforces: the lock file is opened without following symlinks; an item renamed away
while the lock was waited for (the lock path no longer names the locked file) is not taken.

Imports: `fsbpyr.boot._fsbpyr_rt.gc` (top).
Imported by: `fsbpyr.runtime.pbs`, `fsbpyr.runtime.registry`, `fsbpyr.runtime.sysroot`.

### `collect`

[`src/fsbpyr/runtime/collect.py`](../src/fsbpyr/runtime/collect.py)

Collects the tool's cache under the app collector's rules, finds the app caches for
`fsbpyr cache gc`, and decides when a collection is due, as described in
[Held and collected](#held-and-collected). `NAMES` holds, per subtree, a pattern of the names
fsbpyr gives (runtime ids, PBS names, sysroot identities, and in `downloads/` the PBS archives,
`.deb` and `.rpm` packages and partial downloads); nothing else there is ever removed, since a
directory of the user's may hold nothing but names like fsbpyr's and so be marked.

Public functions:

- `needed()`: what the running fsbpyr builds with, by subtree, for every pin and every glibc
  baseline; never collected.
- `collect_tool(root, now=None, pressure=None)`: one collection of the tool's cache; returns
  a `gc.Report`.
- `app_caches()`: directories in `${XDG_CACHE_HOME:-~/.cache}` that have the marker, or that
  may be given it (`gc.mark`: a cache an app of an earlier version made), owned by the user
  and on a trusted way, plus `$FSBPYR_CACHE_DIR` if it has the marker. When `cache_home` finds
  no home directory, only `$FSBPYR_CACHE_DIR` counts; when that is not set either, its
  `RuntimeCacheError` is raised.
- `collect_when_due(root)`: the daily check at a command's start.

Rules it enforces: only marked roots, only names fsbpyr gives, only directories and files the
user owns, never what `needed()` lists, never an item someone holds.

Imports: `fsbpyr.targets`, `fsbpyr.boot._fsbpyr_rt.gc`, `fsbpyr.runtime.pins`,
`fsbpyr.runtime.registry` (all top).
Imported by: `fsbpyr.cli`.

## Tests

The package's tests are in
[`tests/unit/runtime/`](../tests/unit/runtime),
among them `test_rpm.py`, `test_sysroot.py` (the Debian and the RPM extraction, the layouts'
links and flags) and `test_stubs.py` (where a release's stub comes from, against a fake
release server, also for a variant and with `--offline`, and how it is checked).
[`tests/e2e/test_el8.py`](../tests/e2e/test_el8.py)
builds with real runtimes for glibc 2.28 and with a release fsbpyr that pairs its standard
library with the EL8 stub, served by a fake release server or placed beside it; the EL8 test
mode (`FSBPYR_TEST_GLIBC=2.28`, `make test-el8`) runs the tests that depend on the stub with the
glibc 2.28 one. [Testing](Dev-Testing.md) describes the suite.

## See also

- [Launcher](Dev-Launcher.md): the C program the stub is.
- [Payload modules](Dev-Payload-Modules.md): the format of `stdlib.fsbp` and of the executables a
  runtime is read from.
- [Boot modules](Dev-Boot-Modules.md): the collector that `hold` and `collect` share with every
  app.
- [Build modules](Dev-Build-Modules.md): `--glibc` and the rest of a build for glibc 2.28.
- [Compatibility](Dev-Compatibility.md): what the glibc baseline means for the targets.
- [Security](Dev-Security.md): the trust a cached or placed stub gets.
