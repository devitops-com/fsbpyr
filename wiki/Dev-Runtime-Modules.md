# Runtime modules

The package [`src/fsbpyr/runtime/`](../src/fsbpyr/runtime) obtains runtimes at build time,
caches them and cleans the cache. A runtime is what every executable for one Python version,
target and glibc baseline has in common: the native stub (the [launcher](Dev-Launcher.md)
statically linked with CPython from python-build-standalone, PBS) and the compiled standard
library. `fsbpyr runtime build`, `fsbpyr runtime list` and `fsbpyr cache gc` are implemented here;
their command line is in `cli.py` ([Build modules](Dev-Build-Modules.md)).

## What a runtime is

`registry.Runtime` holds one runtime:

| Field | Content |
|---|---|
| `tag` | the PBS distribution's Python tag and the target triple, `<tag>-<triple>`; it seeds every app's native cache generation |
| `runtime_id` | `cp<XY>-<triple>-<12 hex digits>`, with `-sanitized` for the test variant |
| `python_version`, `hexversion`, `bytecode_magic` | of the PBS interpreter, from its `PYTHON.json` |
| `extension_suffixes` | the interpreter's extension module suffixes, asked of it |
| `stub` | the linked launcher's bytes |
| `modules`, `files` | the standard library as payload specs ([Payload modules](Dev-Payload-Modules.md)), with the licence notices as files |
| `toolchain` | the first `--version` line of the clang and lld that linked the stub (information only; empty for a release's glibc 2.28 stub) |
| `inputs` | the PBS release, name and SHA-256, the sysroot's identity and the layout version; apps record them as `runtime_inputs` |
| `components` | the OpenSSL, SQLite, zlib, expat and libmpdec versions the interpreter reports, and that of the zstd the launcher links (`pbs.zstd_version`; left out when not found); apps record them as `runtime_components` |
| `baseline` | the glibc baseline the stub is linked for: `2.34` (`pins.DEFAULT_BASELINE`, the Debian 12 sysroot) or `2.28` (the Rocky Linux 8.10 sysroot, [for EL8](#runtimes-for-glibc-228-el8)) |

The runtime id is the first 12 hex digits of a SHA-256 over the launcher digest
(`stub.launcher_digest`: the launcher's sources and its compiler and linker flags for the target),
the PBS archive's name and SHA-256, the sysroot's identity and `LAYOUT_VERSION` (10), so new
inputs never reuse an old runtime, and the two baselines have different ids. The clang and lld
versions are not part of it. The stub records its id as the string `fsbpyr-stub-identity=<id>`,
compiled in with `-DFSBPYR_RUNTIME_ID`, a define the launcher digest leaves out; LTO and stripping
keep it, nothing reads it at run time, and `stub.stub_identity` reads it back. Only a release's
glibc 2.28 stub, which this fsbpyr did not link, is checked against it
([below](#runtimes-for-glibc-228-el8)).

## Where its parts come from

![Building a runtime: pins name the PBS archive and, per glibc baseline, the Debian 12 or Rocky Linux 8.10 packages, which are downloaded, verified and extracted; clang and lld link the launcher sources against PBS's static libpython and the sysroot into the stub, with the runtime id compiled in; PBS's interpreter compiles the standard library; Runtime.save writes stub, stdlib.fsbp and runtime.json into runtimes/<id>](images/runtime-build.svg)

`registry.build_runtime(python, baseline=...)`, which
`fsbpyr runtime build --python X.Y [--glibc 2.34|2.28] [--clang PATH]` runs (`--glibc` defaults to
2.34), does the whole job, cached under the runtime id: `pins.pin_for` and `pins.sysroot_for` name
the inputs before anything is downloaded (a runtime whose `runtime.json` exists is held and loaded
instead); `pbs.fetch` gets the PBS archive; `toolchain.find_toolchain` finds clang and lld;
`sysroot.fetch_sysroot` gets the sysroot; `stub.build_stub` links the stub with the runtime id
compiled in; `stdlib.fragment` compiles the standard library with PBS's interpreter and
`notices.files` adds the licence notices; `Runtime.save` writes `stub`, `stdlib.fsbp` and
`runtime.json` into a staging directory beside the target, renames it into place and holds it.
[docs/performance.md, Building a runtime](../docs/performance.md#building-a-runtime) gives the
link's cost. Only the tests build the sanitized variant (`sanitize=True`: AddressSanitizer and
UndefinedBehaviorSanitizer).

## How it is cached

The tool's cache root is `$FSBPYR_CACHE_DIR`, else `${XDG_CACHE_HOME:-~/.cache}/fsbpyr`, by the
rule of the app caches ([Boot modules](Dev-Boot-Modules.md#native-code-and-the-cache)). With no
home directory, `registry.cache_home` raises a `RuntimeCacheError`,
`cannot find the home directory; set HOME, XDG_CACHE_HOME or FSBPYR_CACHE_DIR`, which
`fsbpyr build`, `runtime build`, `runtime list` and `cache gc` print as `fsbpyr: error: <message>`
before they exit 1. `registry.secure_directory` creates the root 0700, or checks that the user
owns it and removes group and other permissions, and checks the way to it by the app caches' rule
(`gc.way_problem`): a cached stub ends up inside every executable built from it.

| Under the cache root | Content | Complete when it holds |
|---|---|---|
| `runtimes/<runtime id>/` | `stub` (mode 0755), `stdlib.fsbp`, `runtime.json` | `runtime.json` |
| `pbs/<archive name>/` | the extracted PBS distribution | `python/PYTHON.json` |
| `sysroots/<identity>/` | the extracted sysroot packages | `.fsbpyr-sysroot` |
| `downloads/` | PBS archives, sysroot packages (`.deb`, `.rpm`), downloaded glibc 2.28 stubs (`<asset>-<runtime id>`, `stubs.py`) and fsbpyr variants for other Pythons (`dispatch.py`) | |
| `.fsbpyr-cache` | the marker that allows collection | |
| `.gc-tool`, `.trash-tool` | the collection's stamp and trash | |

`stdlib.fsbp` is a bare payload (no stub, no trailer) with block packing off, so every app packs
its zstd frames into its own blocks. `runtime.json` records the fields above, the fsbpyr version
that built the runtime, and the integrity digests of `stub` and `stdlib.fsbp`; `Runtime.load`
refuses a runtime whose files are missing or do not match them, or whose layout version differs,
and takes a `runtime.json` without `baseline` as 2.34. Each item is written into a staging
directory (a downloaded stub under a temporary name) and renamed into place, so it appears
complete or not at all; of two racing processes, the first complete item is kept.

## Held and collected

The tool's cache follows the rules of the app caches' collector,
[`_fsbpyr_rt.gc`](Dev-Boot-Modules.md#gc), which `hold`, `collect` and `registry` import from
`src/fsbpyr/boot/_fsbpyr_rt/gc.py`.

- **Held.** `hold.hold(directory)` holds an item's `.lock` shared for the life of the process,
  touching it once a day (or when in the future) to date the last use, and marks a root that
  holds nothing but fsbpyr's names (`gc.mark`). An item collected meanwhile is made again; one
  that cannot be locked (a read-only cache, no locks) is used unheld. Runtimes, PBS distributions
  and sysroots are held by those who fetch or load them; downloads are not.
- **Collected.** `collect.collect_tool(root)` runs `gc.run` with the stamp `.gc-tool` and the trash
  `.trash-tool` (the app collector's are `.gc` and `.trash`): the tool's cache shares its root
  with the app cache of the fsbpyr executable itself, so `gc.py` counts the tool's names as its
  own too (`gc.TOOL`). Nothing the running fsbpyr builds with is removed (`collect.needed`: every
  pin's runtime ids for each baseline, sanitized ones included, the PBS names, both baselines'
  sysroot identities and their downloads). Any other item with a name fsbpyr gives is trashed,
  under an exclusive lock, when nobody holds it and it was unused for 7 days or the file system
  has less than 10% free; another download is deleted by the same rule, and staging directories
  and partial downloads go once a day old. The downloaded glibc 2.28 stubs and fetched variants
  have names `collect.NAMES` does not list, so they stay (a test checks this for the stubs).
- **When.** `fsbpyr build`, `fsbpyr inspect`, `fsbpyr runtime build` and `fsbpyr runtime list` call
  `collect.collect_when_due` at their start: a day after the last run (an hour with less than 10%
  free, at once when the last stamp is in the future), never with `FSBPYR_GC` set. Its failure never
  stops the command (`cli._collect_when_due`); an `OSError` other than a missing home directory, a
  read-only file system or a cache it may not write is printed. `fsbpyr cache gc` collects the
  tool's cache and every app cache `collect.app_caches` finds, with a report for each, and exits 1
  if anything could not be removed, or if it finds neither `FSBPYR_CACHE_DIR` nor a home directory.

## Embedded and cached runtimes

A released fsbpyr is itself an executable built by fsbpyr, whose runtime it hands on to what it
builds ([Self-hosting](Dev-Self-Hosting.md)); `registry.running_executable` returns
`/proc/self/exe` when `_fsbpyr` is a built-in module, else `None`.

- **Embedded.** `Runtime.from_executable(path)` verifies the executable's whole-file digest,
  then takes the stub with its zero padding, the modules and files of no distribution, less the
  boot packages, and the identity, suffixes, inputs and components from `build_info`; its
  baseline is that of the pinned sysroot `runtime_inputs` names (the default for an unknown one).
  No compiler, no download, no cache.
- **Cached.** `find_runtime(python, baseline=...)` loads and holds `runtimes/<id>` for this host's
  pin and that baseline. It never builds: without one it fails naming
  `fsbpyr runtime build --python X.Y` (with `--glibc 2.28` for that baseline) and, for the default
  baseline, the release binary.
- **Choice.** `select_runtime(python, baseline=...)` takes the embedded runtime when its Python
  and baseline match; when only the Python matches, a released fsbpyr pairs its standard library
  with its release's stub for that baseline (`stubs.paired`), and a stub it cannot get is an
  error, not a fall-back to the cache. Otherwise, and in a developer build, it takes the cache.
  `fsbpyr build` passes the Python fsbpyr runs on, the build's `glibc` (`--glibc`, else
  `[tool.fsbpyr]`'s, else 2.34) and `--offline` (the stub must be placed or cached); a project
  that needs another Python goes to the matching fsbpyr release
  ([Build modules](Dev-Build-Modules.md)).
- **Listing.** `fsbpyr runtime list` (`list_runtimes`) prints the embedded runtime, if any, and
  every cached one as `<id>  Python <version>  glibc <baseline>`.

## Runtimes for glibc 2.28 (EL8)

The default stub links against Debian 12's glibc 2.36 and needs no symbol newer than 2.34 (the
docstring of `SysrootPin.baseline`); EL8 hosts have glibc 2.28, so their apps need a stub linked
against Rocky Linux 8.10's glibc. Only the stub differs: the standard library does not depend on
the sysroot (the docstring of `stubs.py`).
`fsbpyr runtime build --glibc 2.28` builds and caches a 2.28 runtime, which
`find_runtime(python, baseline="2.28")` finds; fsbpyr run from a source checkout and a developer
build (no release tag) use only this. A released fsbpyr carries one runtime, for 2.34; for 2.28,
`stubs.paired` keeps it (tag, Python, suffixes, standard library, components) and replaces the
stub with its release's glibc 2.28 stub, the runtime id and `inputs` with those of the 2.28
runtime of the same pin, and the toolchain record with an empty one.

`stubs.stub_for` finds the release's stub, the asset `vcs.release_stub_asset` names
(`fsbpyr-stub-<X.Y>-<target>-glibc2.28`), in this order:

1. **Placed.** A file of that name, or with `.xz` (then unpacked), in the directory of the path
   fsbpyr was run by (looked up on `PATH` for a bare name; links are kept, so a file beside a link
   is found) or of its real path (`stubs.own_paths`), for machines without GitHub. A variant for
   another Python (run from `downloads/`, `dispatch.variant`) looks first beside the fsbpyr that
   handed it the build, whose paths `dispatch.run` passes in `FSBPYR_DISPATCHED_BY` (joined with
   `:`, absolute ones only).
2. **Cached.** `downloads/<asset>-<runtime id>`, named by the runtime id the stub must carry, so an
   update that keeps the id reuses it; a cached file without that id is deleted and fetched again.
3. **Downloaded.** From the companion release `el8-vX.Y.Z` (`vcs.release_stub_tag`), since a
   published release takes no new assets, below `$FSBPYR_RELEASE_DOWNLOADS` (by default
   `https://github.com/devitops-com/fsbpyr/releases/download`). `SHA256SUMS-glibc2.28`
   (`vcs.release_stub_sums`), the SHA-256 of the unpacked stubs and their `.xz`, is checked with
   its `.sig` against the pinned keys; `<asset>.xz` is checked against it before lzma sees it,
   unpacked, checked again and for the runtime id, and only then kept. With `--offline` nothing
   is downloaded (`--offline: the <asset> is not placed or cached, and nothing is downloaded;
   ...`).

Whichever it is, the stub must carry the runtime id this fsbpyr computes from its own launcher
sources and flags, the PBS pin, the EL8 sysroot pin and the layout version
(`registry.runtime_id(pin, baseline="2.28")`), or it is refused (`<where> is not the stub this
fsbpyr (vX.Y.Z) needs: ...`, or `... it carries no runtime id: ...`). The id holds no fsbpyr
version, so releases with the same launcher, pins and layout version accept each other's stubs. A
placed or cached stub is accepted on that id alone, which does not detect a tampered file
([Security](Dev-Security.md)). A file over 256 MiB is no stub. A missing companion release, sums
file or asset stops the build with `release el8-vX.Y.Z (the glibc 2.28 stubs of vX.Y.Z) does not
exist or has none for this Python` and how to place the file; an unreachable server or a download
cut short (`http.client.IncompleteRead`) with `release el8-vX.Y.Z's <asset> could not be fetched:
...`. The place these messages name is the first of `own_paths`: for a variant, the fsbpyr that
handed it the build.

`fsbpyr self update` names the placed stubs the new release's `--glibc` would refuse
(`stubs.refused_after_update`): those whose SHA-256 the new release's `SHA256SUMS-glibc2.28` does
not list, or all of them when it cannot read that file. The stubs are built from a cached 2.28
runtime by [`.github/workflows/el8.yml`](../.github/workflows/el8.yml) with
[`tools/release/el8-stub.py`](../tools/release/el8-stub.py) into a draft of the companion
release, which `make release-sign` signs and publishes. The rest of a 2.28 build is on
[Build modules](Dev-Build-Modules.md#building-for-glibc-228).

## Modules

### `pins`

[`src/fsbpyr/runtime/pins.py`](../src/fsbpyr/runtime/pins.py)

The pinned inputs of every runtime. `PINS` lists one PBS archive per Python version and target:
CPython 3.12.16, 3.13.18, 3.14.10 and 3.15.2 for `x86_64-unknown-linux-gnu` and
`aarch64-unknown-linux-gnu`, release 20261026, flavour `pgo+lto-full`, each with its SHA-256 (a
test compares triples and flavours with `targets.py`). Two tables name the sysroot packages per
target, each with its SHA-256; `BASELINES` lists their baselines, newest first,
`DEFAULT_BASELINE` (`"2.34"`) and `"2.28"`:

| Table | Baseline | Packages | From |
|---|---|---|---|
| `SYSROOTS` | 2.34: glibc 2.36, `manylinux` 2.34, the newest symbol version the stub needs | `libc6`, `libc6-dev`, `libgcc-s1`, `libgcc-12-dev`, `linux-libc-dev` of Debian 12 | Debian's own pool, `deb.debian.org` |
| `EL8_SYSROOTS` | 2.28: glibc 2.28, `manylinux` 2.28 | `glibc`, `glibc-devel`, `glibc-headers`, `kernel-headers`, `libgcc` (BaseOS) and `gcc` (AppStream) of Rocky Linux 8.10 | `dl.rockylinux.org/pub/rocky/8.10` |

Debian 12 is the oldest Debian release in LTS, its main suite frozen; Rocky Linux 8.10, the final
EL8 minor release, keeps every build at a stable URL with its SHA-256; only its `gcc` package has
`crtbeginS.o`, `libgcc.a` and `libgcc_eh.a`.

Classes:

- `PbsPin(python, version, release, triple, flavor, sha256)`: one PBS archive; properties `name`
  (`cpython-<version>+<release>-<triple>-<flavor>`) and `url`.
- `SysrootPin(name, glibc, manylinux, packages, packaging="deb", layout="multiarch")`: one
  sysroot's packages. `packaging` (`"deb"` or `"rpm"`) chooses the extractor; `layout` is
  `"multiarch"` (Debian's `usr/lib/<multiarch>`) or `"redhat"` (`usr/lib64`, with `lib` and
  `lib64` links into `usr`), as the EL8 pins have. `identity` is the name
  (`debian12-glibc2.36-<arch>`, `rocky8-glibc2.28-<arch>`) plus 12 hex digits of a digest over the
  packages' SHA-256 values, so a changed pin never reuses an old sysroot or runtime; `baseline` is
  the apps' glibc (`manylinux`), as a tuple.

Public functions:

- `pin_for(python, triple)`: the pin of a Python version for a target; `LookupError` for a target
  without pins or a version without one (that message lists the supported ones).
- `sysroot_for(triple, baseline=DEFAULT_BASELINE)`: the target's sysroot pin for a baseline;
  `ValueError` for a baseline not in `BASELINES` (the message lists them) or a target without a
  pin.

Rules it enforces: only pinned versions, targets and baselines are built.

Imports: none inside fsbpyr.
Imported by: `fsbpyr.build`, `fsbpyr.config`, `fsbpyr.inspection`, `fsbpyr.natives`,
`fsbpyr.pyversion`, `fsbpyr.runtime.collect`, `fsbpyr.runtime.pbs`, `fsbpyr.runtime.registry`,
`fsbpyr.runtime.stubs`, `fsbpyr.runtime.sysroot`, `fsbpyr.system`.

### `pbs`

[`src/fsbpyr/runtime/pbs.py`](../src/fsbpyr/runtime/pbs.py)

PBS distributions: verified download, safe extraction, and what the link needs. An interpreter
that exits with an error gives a `PbsError` quoting the last 500 characters of its stderr
(`text.tail_lines`).

Classes:

- `PbsError`: a download, verification or layout problem.
- `LinkInputs(libpython, extra_objects, static_libs, system_libs)`: what to link besides the
  launcher's own sources.
- `PbsDist(root, info)`: an extracted distribution (the directory holding `PYTHON.json`), with
  `load(root)` and the properties `version`, `python`, `tag`, `triple`, `hexversion`,
  `bytecode_magic`, `llvm_major` (from `object_file_format`; objects that are not LLVM bitcode
  are a `PbsError`), `interpreter`, `stdlib` and `include`; `builtin_modules()` lists the
  inittab's modules compiled into libpython.

Public functions:

- `link_inputs(dist, archive_members)`: `libpython.a`, the object files of built-in extension
  modules that are not archive members, the static libraries they link, the system libraries,
  and PBS's `libzstd.a` when there is one (the launcher decodes zstd itself, and 3.12 and 3.13
  have no `_zstd` module that would name it).
- `component_versions(interpreter)`: OpenSSL, SQLite, zlib, expat and libmpdec versions, as the
  interpreter reports them (`-I -c`, 60 s timeout).
- `zstd_version(dist)`: the version of the `libzstd.a` the launcher links: the one `X.Y.Z`
  string among the C strings (`.rodata.str1.1`) of its member `zstd_common.o`, which holds what
  `ZSTD_versionString()` returns; `None` without exactly one.
- `extension_suffixes(interpreter)`: the interpreter's `importlib.machinery.EXTENSION_SUFFIXES`,
  in order; the docstring says why not `PYTHON.json`'s list.
- `sha256_of(path)`: a file's SHA-256, read 1 MiB at a time.
- `download(url, sha256, target)`: retries after a network failure (four attempts, pausing 2, 5
  and 10 seconds), never after an HTTP error; writes a `.part` file and renames it only once its
  SHA-256 matches. `sysroot` downloads with it too.
- `fetch(pin, cache)`: download the archive once into `downloads/` (a cached one is checked
  against its pin again), extract it (zstd-compressed tar, through `fsbpyr.compat`) into a staging
  directory renamed to `pbs/<name>`, require `python/PYTHON.json`, and hold it.

Rules it enforces: only `https://` URLs; a download or cached archive that does not match the
pinned SHA-256 is refused; extraction applies `tarfile`'s `data` filter.

Imports: `fsbpyr.elf`, `fsbpyr.compat`, `fsbpyr.text`, `fsbpyr.runtime.ar`,
`fsbpyr.runtime.hold`, `fsbpyr.runtime.pins` (all top).
Imported by: `fsbpyr.build`, `fsbpyr.cli`, `fsbpyr.runtime.notices`, `fsbpyr.runtime.registry`,
`fsbpyr.runtime.stdlib`, `fsbpyr.runtime.stub`, `fsbpyr.runtime.sysroot`.

### `toolchain`

[`src/fsbpyr/runtime/toolchain.py`](../src/fsbpyr/runtime/toolchain.py)

Finds a clang and lld able to link PBS's static libpython, whose objects are LLVM bitcode of
major N: `clang-N` to `clang-(N+9)`, then `clang`, on `PATH`, the first whose `--version` reports
a major of at least N and that has `ld.lld-<major>` or `ld.lld` on `PATH` or in its own
directory. A `--clang` is checked the same way. Both baselines use the same toolchain.

Classes:

- `ToolchainError`: no suitable compiler or linker; the message suggests installing LLVM N or
  passing `--clang`.
- `Toolchain(clang, lld, major, sysroot=None)`: the programs, and the sysroot to link against
  (filled in by `build_runtime`).

Public functions:

- `clang_major(version_text)`: the major version in `clang --version` output, or `None`.
- `find_toolchain(min_major, explicit=None, search_path=None)`: the search above.
- `describe(toolchain)`: the first `--version` line of clang and of lld, for `runtime.json`.

Imports: none inside fsbpyr.
Imported by: `fsbpyr.cli`, `fsbpyr.runtime.registry`, `fsbpyr.runtime.stub`.

### `sysroot`

[`src/fsbpyr/runtime/sysroot.py`](../src/fsbpyr/runtime/sysroot.py)

The glibc sysroot the stub links against, so that it depends on the pinned glibc, not on the build
host's (the module docstring), extracted without a container: the same pins give the same tree. Each
package is read once and checked against its SHA-256, so what is verified is what gets extracted;
absolute symlinks are made relative before the `data` filter is applied. An RPM's files come through
`rpm.payload_tar`, less the paths of `_RPM_SKIP`, which linking never reads (`etc/`, `var/`,
`usr/bin/`, `usr/sbin/`, `usr/libexec/`, `usr/share/`, `usr/lib/.build-id/` and the converters in
`usr/lib64/gconv/`; gcc's compiler proper alone is 70 MB). For the `redhat` layout the links `lib ->
usr/lib` and `lib64 -> usr/lib64` of EL8's `filesystem` package are added, since the linker scripts
name `/lib64/libc.so.6` and, on aarch64, a loader below `/lib`. App builds use the same sysroots to
rebuild dependencies built from sdists that need a newer glibc than the baseline
(`build.rebuild_on_baseline`).

Classes:

- `SysrootError`: the sysroot cannot be downloaded or extracted.

Public functions:

- `extract_deb(data, target, name)`: extract the `data.tar` member of a Debian package (an `ar`
  archive) into `target`.
- `extract_rpm(data, target, name)`: the same for an RPM package, less `_RPM_SKIP`.
- `fetch_sysroot(pin, cache)`: download (once), verify, extract each package with the extractor
  of `pin.packaging` into a staging directory, add the `redhat` links, write `.fsbpyr-sysroot`
  last, rename into `sysroots/<identity>`, and hold.
- `build_flags(root, env, layout="multiarch")`: `CFLAGS` and `LDFLAGS` that point gcc or clang at
  the sysroot, before the environment's own. For `multiarch`: `--sysroot`, `-isystem` for
  `usr/include/<multiarch>`, `-B` for `usr/lib/<multiarch>` and the gcc directory
  `usr/lib/gcc/<multiarch>/<version>` (the last in sorted order), and `-L` for
  `usr/lib/<multiarch>` and `lib/<multiarch>` (a host without a multiarch name is a
  `SysrootError`). For `redhat`: `--sysroot`, `-B` for `usr/lib64` and the gcc directory
  `usr/lib/gcc/<triple>/<version>` (the last in sorted order), and `-L` for `usr/lib64`. Not `CC`
  (build probes run it as a bare command) and not C++ (the sysroot has no libstdc++).

Rules it enforces: every package matches its pinned SHA-256; a Debian package without a data
archive, or an RPM that `rpm` refuses, is an error; symlinks cannot point outside the sysroot; a
sysroot without `.fsbpyr-sysroot` is incomplete.

Imports: `fsbpyr.targets`, `fsbpyr.runtime.ar`, `fsbpyr.runtime.rpm`, `fsbpyr.runtime.hold`,
`fsbpyr.runtime.pbs`, `fsbpyr.runtime.pins` (all top).
Imported by: `fsbpyr.build`, `fsbpyr.cli`, `fsbpyr.runtime.registry`.

### `ar`

[`src/fsbpyr/runtime/ar.py`](../src/fsbpyr/runtime/ar.py)

A reader of Unix `ar` archives in the GNU long-name variant that llvm-ar writes, for the members
of PBS's `libpython.a` (`stub`), a `.deb`'s `data.tar` (`sysroot`) and `zstd_common.o` of
`libzstd.a` (`pbs`). The symbol tables (`/`, `/SYM64/`) are skipped and the long-name table (`//`)
resolves names.

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

Reads the payload of an RPM package, standard library only, for the EL8 sysroot: a 96-byte lead,
the signature header (padded to 8 bytes), the main header and an xz- or lzma-compressed cpio
archive in the "newc" format (magic `070701`). Of the main header only the payload format (tag
1124) and the compressor (tag 1125) are read; the signatures are not (the module docstring), since
the pins' SHA-256 values are what is verified. The payload becomes a tar archive, which `sysroot`
extracts with `tarfile`'s safety filter. Member names are made relative and normalized; a name
below `bin`, `sbin`, `lib` or `lib64`, which EL8's `filesystem` package links into `usr`, gets
`usr/` in front (`libgcc` names `lib64/libgcc_s.so.1`). A set of hard links, whose data cpio
stores with the last name and tar needs with the first, is written data first.

Classes:

- `RpmError` (a `ValueError`): not an RPM fsbpyr can read.

Public functions:

- `payload_tar(data)`: the files of the RPM `data` as a tar archive (PAX format).

Rules it enforces: the RPM and header magics; header entry counts and sizes within rpm's own
limits and inside the file, string tags inside their header; payload format `cpio`; compressor
`xz` (or `lzma`); a decompressed payload of at most 512 MiB whose stream is complete (gcc's is
60 MB); untruncated cpio headers and entries; only regular files, directories and symlinks; no
name outside the package; no incomplete set of hard links.

Imports: none inside fsbpyr.
Imported by: `fsbpyr.runtime.sysroot`.

### `stub`

[`src/fsbpyr/runtime/stub.py`](../src/fsbpyr/runtime/stub.py)

Compiles the launcher and links it statically against a PBS distribution. The launcher's files
(`LAUNCHER_FILES`: the eleven C sources of `LAUNCHER_SOURCES`, their headers and the vendored zstd
headers) are copied through `importlib.resources`, since they may live inside an fsbpyr executable,
and compiled on relative paths; `-ffile-prefix-map` keeps local paths out of the binary. The fixed
flags (`COMPILE_FLAGS`, `LINK_FLAGS`) build with full LTO, stack protection and `_FORTIFY_SOURCE`,
warn about variable-length arrays (`-Wvla`) and stack frames over 8192 bytes, and link with a
non-executable stack, `-z relro -z now` and stripping; they export only the `Py*` and `_Py*` symbols
extension modules link against, link libgcc statically, and wrap `hypot` and `_dl_find_object`
(`glibc.c`, [Launcher](Dev-Launcher.md#glibcc)) to keep the stub within the baseline; the EL8
headers lack `struct dl_find_object`, so that wrapper is compiled out there. The bytecode magic
(`-DFSBPYR_BYTECODE_MAGIC`) and the runtime id are compiled in. On aarch64 compiler-rt's builtins
are linked for the outlined atomics PBS's objects call, and the link adds
`-Wl,--fix-cortex-a53-843419` (`AARCH64_LINK_FLAGS`): lld then patches the `adrp` sequences that
compute a wrong address on a Cortex-A53, which the stub, linked here from PBS's bitcode, needs
itself; the flag is part of the aarch64 runtime id.

Classes:

- `StubBuildError`: the compiler or linker failed, or a stack frame is over the limit.

Public functions:

- `compile_flags(sanitize=False)` and `link_flags(triple, sanitize=False)`: the fixed flags, with
  `AARCH64_LINK_FLAGS` for an `aarch64-` triple; the sanitized variant drops `-O2`, LTO,
  `_FORTIFY_SOURCE` and stripping, and adds `-O1`, ASan, UBSan (every undefined behaviour fatal),
  frame pointers and `-g`.
- `outlined_atomics(toolchain, triple)`: the compiler-rt builtins library on aarch64 (a
  `StubBuildError` naming `libclang-rt-<N>-dev` when clang has none), `None` on x86-64.
- `launcher_digest(triple, sanitize=False)`: SHA-256 over every launcher file and the flags for
  `triple`.
- `stub_identity(data)`: the runtime id a stub's bytes record (the text between
  `IDENTITY_MARKER`, `fsbpyr-stub-identity=`, and the next NUL), or `None`.
- `stub_command(dist, inputs, toolchain, sources, out, sanitize=False, atomics=None,
  identity=None)`: the clang command line; `identity` adds the `FSBPYR_RUNTIME_ID` define.
- `build_stub(dist, toolchain, out, sanitize=False, identity=None)`: link the stub into `out`,
  recording `identity` (else the launcher's default, `none`); returns every line the compiler and
  linker printed, which `build_runtime` shows.

Rules it enforces: a failed link, or any "stack frame size" warning (LTO sizes frames again at the
link, where only a warning reports it), is a `StubBuildError`; a failed link's message quotes the
last 4000 characters of the output from a line boundary (`text.tail_lines`).

Imports: `fsbpyr.text`, `fsbpyr.runtime.ar`, `fsbpyr.runtime.pbs`, `fsbpyr.runtime.toolchain`
(all top).
Imported by: `fsbpyr.cli`, `fsbpyr.runtime.registry`, `fsbpyr.runtime.stubs`.

### `stdlib`

[`src/fsbpyr/runtime/stdlib.py`](../src/fsbpyr/runtime/stdlib.py)

Everything under PBS's stdlib directory is taken except `config-*` and the directories in
`EXCLUDED_DIRS` (`test`, `idlelib`, `tkinter`, `turtledemo`, `ensurepip`, `__pycache__`,
`site-packages`, `lib-dynload`, `__phello__`); `.py` files with valid module names become
modules (other `.py` files are left out), other files resources. The startup modules, those
CPython imports during initialization (`-X importtime` of an empty run) that are neither
built-in nor frozen, get the STARTUP flag, which puts them in CPython's frozen-module table, and
a `<frozen NAME>` file name. PBS's own
interpreter (`-I -S`) compiles in up to `THREADS` worker processes, each given every
`THREADS`-th file; the code is canonicalized (`fsbpyr.bytecode`) and compressed. A file that does
not compile for this Python is kept as a resource, with a warning; a failed worker or interpreter
run is a `StdlibError` quoting the last 2000 characters of its stderr (`text.tail_lines`).

Classes:

- `StdlibError`: the standard library could not be compiled or analysed.
- `StdlibTree(modules, resources)`: `(name, relative path, is package)` of each module, and the
  relative paths of the other files.

Public functions:

- `collect(stdlib)`: the `StdlibTree` of a stdlib directory.
- `parse_importtime(stderr)`: the module names in `-X importtime` output, without its header line.
- `decode_compiled(stream, rels)`: the compile worker's output (a length word and marshal data per
  file, or a failure marker) as a mapping.
- `compile_files(interpreter, root, rels, filenames=None, warn=None)`: marshalled code of each
  file compiled by `interpreter`, in the order given; what the workers print goes to `warn`.
- `startup_names(interpreter)`: the startup modules.
- `fragment(dist, warn=...)`: the standard library as `Module`s (code with `Blob.code`, source
  with `Blob.zstd`) and `File`s (each module's source as a SOURCE file, other files as RESOURCE).

Rules it enforces: every startup module must be in the selection, else `StdlibError`; a worker
that fails, or output that is truncated or has trailing bytes, is an error.

Imports: `fsbpyr.bytecode`, `fsbpyr.compiler`, `fsbpyr.payload.format`, `fsbpyr.payload.spec`,
`fsbpyr.text`, `fsbpyr.runtime.pbs` (all top).
Imported by: `fsbpyr.cli`, `fsbpyr.runtime.registry`.

### `notices` and `licenses/`

[`src/fsbpyr/runtime/notices.py`](../src/fsbpyr/runtime/notices.py),
[`src/fsbpyr/runtime/licenses/`](../src/fsbpyr/runtime/licenses)

The third-party licence notices of what a runtime links: from PBS's `licenses/` directory, the
distribution's own licence and the `license_paths` of every built-in module's libraries in
`PYTHON.json`, and `LICENSE.zstd.txt` for every Python (PBS lists it only for the `_zstd` module,
which 3.12 and 3.13 lack). Where PBS ships no text, fsbpyr supplies zstd's
(`launcher/vendor/zstd/LICENSE`) and, when a built-in module's objects come from HACL\*, which has
no licence entry, HACL\*'s MIT licence (`licenses/LICENSE.hacl.txt`, the directory's one file).
`LICENSE.zlib-ng.txt` is left out (`NOT_LINKED`): `libz.a` is classic zlib. With fsbpyr's own
`LICENSE` (from the installed distribution, else the source checkout) the texts become RESOURCE
files under `_fsbpyr_licenses/` in every app, where `fsbpyr inspect --licenses` reads them
(`licensing.py`).

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

The runtime cache, as described above. `BOOT_PACKAGES` (`_fsbpyr_boot`, `_fsbpyr_rt`) names the
boot code that every build regenerates and that is never runtime content.

Classes:

- `RuntimeCacheError`: no usable runtime (missing, incomplete, of an old layout, corrupt, a cache
  directory that fails the checks, or no home directory to find the cache in); `stubs.StubError`
  is one too.
- `Runtime`: the dataclass above, with `save(directory)`, `load(directory)` and
  `from_executable(path)`.

Public functions:

- `check_owner(path, owner, euid)`: refuse a directory owned by another user.
- `secure_directory(path)`: create or verify a cache directory, as above (a symlink at `path`
  must be root's or the user's).
- `home_directory()`: `$HOME` if absolute, else the passwd entry's; `RuntimeCacheError` without one.
- `cache_home()`: `${XDG_CACHE_HOME:-~/.cache}`.
- `cache_root()`: `$FSBPYR_CACHE_DIR`, else `cache_home()/fsbpyr`.
- `host_triple()`: the host target's triple, or `RuntimeCacheError` on an unsupported host.
- `runtime_id(pin, sanitize=False, baseline=DEFAULT_BASELINE)`: the id, over the sysroot pin of
  `baseline`.
- `runtime_inputs(pin, baseline=DEFAULT_BASELINE)`: the `runtime_inputs` record.
- `running_executable()`: `/proc/self/exe` inside an fsbpyr executable, else `None`.
- `select_runtime(python, cache=None, baseline=DEFAULT_BASELINE, offline=False)`: the choice
  above.
- `find_runtime(python, cache=None, sanitize=False, baseline=DEFAULT_BASELINE)`: the cached
  runtime, held and loaded.
- `build_runtime(python, cache=None, clang=None, log=print, sanitize=False,
  baseline=DEFAULT_BASELINE)`: build and cache a runtime.
- `list_runtimes(cache=None)`: `(runtime id, Python version, glibc baseline)` of the embedded and
  the cached runtimes.

Rules it enforces: those above; an embedded runtime is taken only from an executable that has
the current format version and records its runtime.

Imports: `fsbpyr`, `fsbpyr.targets`, `fsbpyr.boot._fsbpyr_rt.gc`, `fsbpyr.payload.convert`,
`fsbpyr.payload.format`, `fsbpyr.payload.reader`, `fsbpyr.payload.spec`,
`fsbpyr.payload.writer`, `fsbpyr.runtime.notices`, `fsbpyr.runtime.pbs`, `fsbpyr.runtime.pins`,
`fsbpyr.runtime.stdlib`, `fsbpyr.runtime.stub`, `fsbpyr.runtime.sysroot`,
`fsbpyr.runtime.hold`, `fsbpyr.runtime.toolchain` (all top); `fsbpyr.runtime.stubs` (lazy, in
`select_runtime`, since `stubs` imports this module).
Imported by: `fsbpyr.assemble`, `fsbpyr.build`, `fsbpyr.cli`, `fsbpyr.dispatch`, `fsbpyr.signing`,
`fsbpyr.runtime.collect`, `fsbpyr.runtime.stubs`.

### `stubs`

[`src/fsbpyr/runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py)

[Runtimes for glibc 2.28 (EL8)](#runtimes-for-glibc-228-el8). The names come from `fsbpyr.vcs`:
`release_stub_asset(python, target, glibc)` the asset, `release_stub_sums(glibc)` the sums file
`SHA256SUMS-glibc<glibc>`, and `release_stub_tag(glibc, tag)` the companion release, `el8-<tag>`
for 2.28 (`ValueError` for a baseline without one). Downloads go through `selfupdate`'s
`signed_sums`, `listed_digest` and `fetch_checked`, whose `NotFound` tells a missing file from a
network failure.
`RELEASES` is the releases page the error messages name.

Classes:

- `StubError` (a `registry.RuntimeCacheError`): the release's stub for the baseline cannot be had,
  or is not the right one.

Public functions:

- `own_tag()`: this release's tag, `vX.Y.Z`, from the version its build recorded; `None` for a
  developer build.
- `own_paths(argv0=None, real=None)`: the places a stub is looked for, in order (above).
- `refused_after_update(tag, paths)`: the stubs placed beside `paths` that the fsbpyr of release
  `tag` would refuse, and whether that is known.
- `stub_for(python, baseline, tag, cache=None, offline=False)`: the stub of release `tag` for
  `python` and `baseline`, placed, cached or downloaded.
- `paired(own, baseline, cache=None, offline=False)`: the runtime `own` with that stub; `None` for
  a developer build.

Rules it enforces: a stub is used only if it carries the runtime id this fsbpyr computes for the
baseline; a download only if it matches the SHA-256 that the companion release's sums file lists,
and it is kept only after both checks; nothing over 256 MiB is read as a stub; neither a
developer build nor an `offline` one downloads a stub.

Imports: `fsbpyr.dispatch`, `fsbpyr.selfupdate`, `fsbpyr.signing`, `fsbpyr.targets`,
`fsbpyr.vcs`, `fsbpyr.runtime.pins`, `fsbpyr.runtime.registry`, `fsbpyr.runtime.stub` (all top).
Imported by: `fsbpyr.dispatch` (lazy, in `run`), `fsbpyr.runtime.registry` (lazy),
`fsbpyr.selfupdate` (lazy, for `self update`'s warning about placed stubs).

### `hold`

[`src/fsbpyr/runtime/hold.py`](../src/fsbpyr/runtime/hold.py)

[Held and collected](#held-and-collected). The locks stay in a module-level table until the
process ends; a second `hold` of the same item returns at once.

Public functions:

- `hold(directory)`: take `<root>/<kind>/<name>` for this process; `False` if it is gone or was
  collected while the lock was waited for.

Rules it enforces: the lock file is opened without following symlinks; an item renamed away while
the lock was waited for (the lock path no longer names the locked file) is not taken.

Imports: `fsbpyr.boot._fsbpyr_rt.gc` (top).
Imported by: `fsbpyr.runtime.pbs`, `fsbpyr.runtime.registry`, `fsbpyr.runtime.sysroot`.

### `collect`

[`src/fsbpyr/runtime/collect.py`](../src/fsbpyr/runtime/collect.py)

[Held and collected](#held-and-collected). `NAMES` holds, per subtree, a pattern of the names
fsbpyr gives (runtime ids, PBS names, sysroot identities, and in `downloads/` the PBS archives,
`.deb` and `.rpm` packages and partial downloads); nothing else is ever removed, since a directory
of the user's that holds only names like fsbpyr's may be marked.

Public functions:

- `needed()`: what the running fsbpyr builds with, by subtree.
- `collect_tool(root, now=None, pressure=None)`: one collection of the tool's cache; a
  `gc.Report`.
- `app_caches()`: the directories in `${XDG_CACHE_HOME:-~/.cache}` that have the marker or may be
  given it (`gc.mark`: a cache an app of an earlier version made), owned by the user and on a
  trusted way, and `$FSBPYR_CACHE_DIR` if it has the marker; without a home directory only that,
  and without either, `cache_home`'s `RuntimeCacheError`.
- `collect_when_due(root)`: the daily check at a command's start.

Rules it enforces: only marked roots, only names fsbpyr gives, only what the user owns, never what
`needed()` lists, never an item someone holds.

Imports: `fsbpyr.targets`, `fsbpyr.boot._fsbpyr_rt.gc`, `fsbpyr.runtime.pins`,
`fsbpyr.runtime.registry` (all top).
Imported by: `fsbpyr.cli`.

## Tests

The package's tests are in [`tests/unit/runtime/`](../tests/unit/runtime), among them
`test_rpm.py`, `test_sysroot.py` and `test_stubs.py` (a release's stub against a fake release
server, also for a variant and with `--offline`).
[`tests/e2e/test_el8.py`](../tests/e2e/test_el8.py) builds with real glibc 2.28 runtimes and with
a release fsbpyr pairing its standard library with the EL8 stub; the EL8 test mode
(`FSBPYR_TEST_GLIBC=2.28`, `make test-el8`) runs the stub's tests with the glibc 2.28 one.
[Testing](Dev-Testing.md) describes the suite.

## See also

- [Launcher](Dev-Launcher.md): the C program the stub is.
- [Security](Dev-Security.md): the trust a cached or placed stub gets.
