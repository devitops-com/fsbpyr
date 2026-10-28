# Self-hosting

fsbpyr builds its own executable. A released `fsbpyr-3.X-<target>` is an app that fsbpyr built
from this repository: the project `fsbpyr`, whose console script `fsbpyr` (`fsbpyr.cli:main`)
is the entry point, with click and packaging (and, on Python 3.12 and 3.13, backports.zstd) from
its `uv.lock`. That makes fsbpyr self-hosting the way a compiler written in its own language is.
Such a compiler is first built by another compiler and then builds itself, and the result counts
only when it reproduces itself: GCC's bootstrap builds the compiler three times and compares the
second and the third build ([Building GCC](https://gcc.gnu.org/install/build.html)). fsbpyr does
the same with whole executables. Stage 1 is built by fsbpyr run from source or by a released
fsbpyr, stage 2 by stage 1 and stage 3 by stage 2, and stages 2 and 3 must be the same bytes.
Self-hosting is R2 and R3 of the implementation plan
([§0](../docs/IMPLEMENTATION_PLAN.md#0-requirements)), and this check is its decision D15
([§2](../docs/IMPLEMENTATION_PLAN.md#2-key-decisions)).

There are two ways in. They differ in what builds stage 1, and so in the runtime that all three
stages carry:

- **From C and Python sources** (`make bootstrap`, [below](#from-c-and-python-sources-make-bootstrap)).
  The runtime, the launcher with CPython linked in and the compiled standard library, is linked
  from the launcher's C sources, the pinned CPython and the pinned glibc sysroot (or taken from
  fsbpyr's cache, where a runtime of the same id was linked before), and fsbpyr run from source
  in the checkout's venv builds stage 1. On Debian trixie this needs uv, the Python that
  `.python-version` pins, and curl and zstd, with which `make bootstrap` fetches clang and lld 23
  itself; on any other host it needs podman, whose container brings the rest.
- **From a released fsbpyr** ([below](#from-a-released-fsbpyr)). A released executable builds
  stage 1 with the runtime it carries, and every stage carries that runtime. Its builds need uv
  and git, no compiler and no runtime in the cache; fetching and checking the release needs curl
  and gh. It runs on a machine of the release asset's target, for a checkout that still has the
  release's payload format. No make target runs it; the commands are on this page.

![fsbpyr builds itself in three stages, from C and Python sources (make bootstrap) or from a released executable; in both, every build runs in the checkout, and stages 2 and 3 must be the same bytes](images/self-hosting-stages.svg)

This page explains both ways, shows which parts of the [layout](Dev-Executable-Format.md#layout)
change from one stage to the next, and why stages 2 and 3 must be equal. The runs it reports
were made on 2026-10-28 on x86-64 (Debian trixie) with uv 0.13.6 and CPython 3.14.10
(python-build-standalone 20261026), the default Python, from the release v0.28.0's
`fsbpyr-3.14-x86_64-linux-gnu` and from main at commit 5211d5d, whose `src/` is that of v0.28.0.
The layout comparison also used a scratch commit on top of 5211d5d
([below](#how-the-layout-changes-from-stage-to-stage)).

## What a build takes from where

A build of fsbpyr by fsbpyr takes each part of the new executable from one of three places, and
two tools of the build host work on it:

| From | What | Code |
|---|---|---|
| **the checkout**, the project being built | fsbpyr's own code and its dependencies as `uv.lock` pins them, which uv builds and installs into a temporary site; the commit and tag of HEAD, recorded as `git` in the build information, which become the new executable's `--version` (`X.Y.Z-<commit>` on a tag `vX.Y.Z`, else `untagged-<commit>`) | [`staging.py`](../src/fsbpyr/staging.py), [`vcs.version_string`](../src/fsbpyr/vcs.py#L101-L107) |
| **the runtime the builder uses** | the stub (the launcher, CPython linked in) with its zero padding, and the standard library with its startup modules; the runtime id, `stub_sha256` and the component versions in the build information | [`registry.select_runtime`](../src/fsbpyr/runtime/registry.py#L312-L332) |
| **the builder's own code** | the boot packages `_fsbpyr_boot` and `_fsbpyr_rt`, which a build makes from the `fsbpyr.boot` of the fsbpyr that runs it, not from the project's; how the build stages, analyses, compiles and writes; and its own version, recorded as `fsbpyr_version` (`built with` in `fsbpyr inspect`) | [`assemble.boot_modules`](../src/fsbpyr/assemble.py#L38-L54), [`vcs.own_version`](../src/fsbpyr/vcs.py#L110-L118) |
| **the build host's tools** | uv, which builds fsbpyr's wheel (stamping its version into the wheel's metadata), installs every distribution and is recorded as `uv`; the CPython that runs the builder, which compiles the bytecode and whose zstd library compresses, recorded as `zstd`: the runtime's in an executable, the venv's when fsbpyr runs from source | [`compat.py`](../src/fsbpyr/compat.py), [`compiler.py`](../src/fsbpyr/compiler.py) |

Which runtime a build uses is decided by `select_runtime`. An fsbpyr executable takes the
runtime embedded in itself when its Python and glibc baseline are the build's
([`Runtime.from_executable`](../src/fsbpyr/runtime/registry.py#L257-L294)): it verifies its own
whole-file digest, then hands on its stub prefix byte for byte and its standard library (zstd
frames copied verbatim, the members of blocks as content that the next build packs again),
leaving out the boot packages, which every build makes anew. fsbpyr run from source is
not inside an executable and takes the runtime from the cache (`find_runtime`), which never
builds one. Nothing compares the embedded runtime's id with the id the checkout's own launcher
sources and pins would give ([`runtime_id`](../src/fsbpyr/runtime/registry.py#L127-L139)), so an
executable hands on its runtime to everything it builds for its own Python and baseline, and
that runtime passes from stage to stage unchanged. This is what lets a released fsbpyr build
without a compiler; it is also why every stage built from a release carries the release's
launcher ([below](#what-the-release-path-checks)).
[Runtime modules](Dev-Runtime-Modules.md#embedded-and-cached-runtimes) describes both kinds of
runtime.

## From C and Python sources: make bootstrap

`make bootstrap` runs [`tools/bootstrap.sh`](../tools/bootstrap.sh), in the four steps its header
and [CONTRIBUTING.md](../CONTRIBUTING.md#building-from-source) number; `from_source`
([lines 165 to 198](../tools/bootstrap.sh#L165-L198)) is steps 1 to 3, which
`bash tools/bootstrap.sh --from-source` runs alone. On Debian trixie the bootstrap runs on the
host, unless `FSBPYR_BOOTSTRAP=podman`. Anywhere else steps 1 to 3 run in a Debian trixie podman
container, the `Containerfile`'s `build` stage, which installs the pinned Python when it starts
and keeps the venv in `/opt/venv` and the caches on the podman volume `fsbpyr-cache`; step 4
runs on the host.

1. **Prerequisites.** uv on `PATH`, and the Python that `.python-version` pins (3.14.10), which
   uv finds and, on the host, never downloads; curl, zstd, tar and sha256sum, which fetch LLVM's
   release of clang and lld 23 that [`tools/release/llvm23`](../tools/release/llvm23) pins by
   SHA-256, once, into `${XDG_CACHE_HOME:-~/.cache}/fsbpyr/toolchains` (`FSBPYR_LLVM23=0` uses a
   clang and lld 23 or newer on `PATH` instead). A missing tool stops the bootstrap with its
   name; nothing is installed on the host.
2. **The locked environment and the runtime.** `uv sync --locked` makes the checkout's `.venv`.
   Then `uv run --locked fsbpyr runtime build --clang <clang 23>`: fsbpyr, run from source,
   fetches the python-build-standalone archive and the Debian 12 sysroot packages that
   [`pins.py`](../src/fsbpyr/runtime/pins.py) pins by SHA-256, compiles the launcher's C sources
   ([`src/fsbpyr/launcher`](../src/fsbpyr/launcher)) and links them with CPython's prebuilt
   static libraries by full LTO into the stub, compiles the standard library with the archive's
   own CPython, and saves it all as `runtimes/<id>/` in fsbpyr's cache
   ([Runtime modules](Dev-Runtime-Modules.md), [Architecture: Building a runtime](Dev-Architecture.md#building-a-runtime)).
   The id is a digest of the launcher sources and their flags, the archive's pin, the sysroot
   and the layout version. A change to any of them gives a new runtime; with none, the cached
   runtime is used and nothing is fetched or linked.
3. **Three stages and the comparison.**
   - Stage 1: `uv run --locked fsbpyr build . -o dist/stage1/fsbpyr`. fsbpyr runs from source on
     the venv's CPython, with the runtime of step 2 from the cache.
   - Stage 2: `dist/stage1/fsbpyr build . -o dist/stage2/fsbpyr`, with the runtime that stage 1
     carries. Stage 3: `dist/stage2/fsbpyr build . -o dist/stage3/fsbpyr`, with stage 2's.
   - As a build does unless `--no-smoke` is given, each one runs the new executable once as a
     smoke test before it puts it in place, and after each stage `inspect --verify` must report
     its digest as verified.
   - `cmp` of stages 2 and 3. If they differ, the bootstrap stops and prints both SHA-256
     digests; otherwise stage 3 becomes `dist/fsbpyr`.
4. **`make install`** puts `dist/fsbpyr`, its man page and its shell completions under `PREFIX`.

`make bootstrap PYTHON=3.12` runs steps 1 to 3 for that Python's variant, in `.venv-3.12` and
into `dist/stage1-3.12` to `dist/stage3-3.12` and `dist/fsbpyr-3.12`, and installs nothing.

Measured: from an empty fsbpyr cache (clang and lld 23 already fetched, uv's cache warm), steps 1
to 3 took 77 seconds in all: fetching the archive and the sysroot, linking the launcher,
compiling the standard library and the three stages. The runtime was
`cp314-x86_64-unknown-linux-gnu-6fe9008f23af`, and all three stages were the same file, SHA-256
`79c81a8b4951f6e8067a765fa5f5f5d7a0ac16b9c93372e6e4d0b78b14a1f2db`. With the cache kept, the
scratch commit of [the layout comparison](#how-the-layout-changes-from-stage-to-stage), which
adds a comment line to `launcher/main.c` and changes a boot module's docstring, took 44 seconds:
its new runtime id (`…-8e450a0e49b6`) made `runtime build` link the launcher and compile the
standard library again, with the archive and the sysroot from the cache.

Stage 1 is the same file as stages 2 and 3 here, although its builder runs on the venv's CPython
with a cached runtime and theirs run inside an executable with an embedded one:

- **The same version.** `own_version` reads the checkout's git state when fsbpyr runs from source
  (`uv sync` installs the checkout editable), and the git state recorded in the executable
  otherwise, so both name the same commit.
- **The same boot packages**, the checkout's in every stage.
- **The same compiler and compressor.** The builder's CPython compiles fsbpyr's modules and the
  boot code, and its zstd library compresses blocks and frames and is recorded as `zstd`. In
  stage 1 that is the venv's CPython, the one `.python-version` pins, 3.14.10 like the runtime;
  from stage 2 on it is the runtime's CPython with its statically linked libzstd. Both gave zstd
  1.5.7. (On 3.12 and 3.13 the zstd library is `backports.zstd` from `uv.lock` in every stage.)
- **The same standard library bytes.** Its blobs that go into blocks pack to the same bytes
  whether they come from the cache as zstd frames or from an executable as raw content: the
  writer keys the members of a block by their content
  ([`_Blobs.key`](../src/fsbpyr/payload/writer.py#L227-L238)), as its comment says, to keep this
  fixed point. Its larger blobs are the same zstd frames, carried verbatim.

So stage 1 equals stage 2 only while the venv's CPython and its zstd agree with the runtime's.
`bootstrap.sh` requires only stages 2 and 3 to be equal
([§5](../docs/IMPLEMENTATION_PLAN.md#5-bootstrap-and-self-hosting-procedure));
`test_self_hosting_reaches_a_fixed_point` requires all three
([Testing](Dev-Testing.md#determinism-checks)).

## From a released fsbpyr

A released executable carries its runtime, so it can start the three stages without a
compiler. There is no make target for this; these are the commands, with the attestation check
of [Verifying a release](../CONTRIBUTING.md#verifying-a-release) first:

```sh
asset=fsbpyr-3.14-x86_64-linux-gnu                   # this machine's target, any Python
python="${asset#fsbpyr-}"; python="${python%%-*}"    # the asset's Python: 3.14, 3.12, ...
tag=v@VERSION@
curl -fLo "$asset" "https://github.com/devitops-com/fsbpyr/releases/download/$tag/$asset"
gh attestation verify "$asset" --repo devitops-com/fsbpyr \
  --signer-workflow devitops-com/fsbpyr/.github/workflows/release.yml --source-ref "refs/tags/$tag"
chmod +x "$asset"
git clone https://github.com/devitops-com/fsbpyr.git src   # or any other checkout
cd src
../"$asset" build . --python "$python" -o ../stage1/fsbpyr
../stage1/fsbpyr build . --python "$python" -o ../stage2/fsbpyr
../stage2/fsbpyr build . --python "$python" -o ../stage3/fsbpyr
for s in 1 2 3; do
  ../stage$s/fsbpyr inspect --verify ../stage$s/fsbpyr | grep -E '^(app|built with|digest|sha256)'
done
cmp ../stage2/fsbpyr ../stage3/fsbpyr && echo "stage 2 = stage 3"
```

`--python` must be the asset's Python: then every stage builds with the runtime it carries.
Without it the checkout's `.python-version` decides, and an executable of another Python hands
the build to that Python's executable of its release, which it fetches
([One fsbpyr per Python](User-Installing.md#one-fsbpyr-per-python)). Each build runs uv, which
builds and installs the checkout with a CPython of that minor version (uv downloads one unless
its Python downloads are turned off; `uv python install 3.14` installs it beforehand), and git,
from which the build records the commit and tag; without git the stages still build, but record
no commit and call themselves `untagged-unknown`. The rebuilds run only on a machine of the
asset's target, since fsbpyr builds for its host.

Run as above on 2026-10-28, with only uv and git on `PATH` and `FSBPYR_CACHE_DIR` naming a
directory that did not exist, each build took about a second with uv's cache warm, and that
directory was never created. The report, in which `digest` is the integrity digest in the
trailer and `sha256` the file's SHA-256:

```console
app        fsbpyr 0.28.0 (untagged-5211d5d9)
built with fsbpyr 0.28.0-b9b1144d
digest     36c17bc2af74d36f75ffbfc9b8d8fbaee34abce9266052c387cbac48288f4af9 verified
sha256     072276afed5831662b86900a11a49bd2ae6031cf963913ba191cbc2a777f6c7d
app        fsbpyr 0.28.0 (untagged-5211d5d9)
built with fsbpyr untagged-5211d5d9
digest     436b96eda29369ed51a325e3121634516ab546b5c73f5384bc2f018350c656fc verified
sha256     79c81a8b4951f6e8067a765fa5f5f5d7a0ac16b9c93372e6e4d0b78b14a1f2db
app        fsbpyr 0.28.0 (untagged-5211d5d9)
built with fsbpyr untagged-5211d5d9
digest     436b96eda29369ed51a325e3121634516ab546b5c73f5384bc2f018350c656fc verified
sha256     79c81a8b4951f6e8067a765fa5f5f5d7a0ac16b9c93372e6e4d0b78b14a1f2db
stage 2 = stage 3
```

Stage 1 differs from stage 2: it was built with 0.28.0, stage 2 with stage 1. Stages 2 and 3 are
the same file that `make bootstrap` built from the same commit. That commit's launcher sources
and pins are v0.28.0's, so the runtime `make bootstrap` linked has the release's id
(`cp314-x86_64-unknown-linux-gnu-6fe9008f23af`), and, linked with the same pinned clang and lld
23, the release's bytes: the stub and the standard library are the same part for part. Run in the
checkout of the release's own tag, with the uv that the release records, the first build gives
the release asset itself, byte for byte (checked for 0.28.0): that is check (a) of
[Verifying a release](../CONTRIBUTING.md#verifying-a-release).

The release and the checkout must share the payload format (version 7 at @VERSION@), and the
checkout's boot code must run on the release's launcher, which stages 2 and 3 carry. Stage 1 is
written by the release in its format and runs the checkout's code, which takes its runtime from
it only in the checkout's format (`spec_from_payload` in
[`payload/convert.py`](../src/fsbpyr/payload/convert.py#L24-L28)). After a format change, or a
change to the boot code that needs a newer launcher, stage 1 cannot build a working stage 2,
and `make bootstrap` is the way in.

### What the release path checks

Stage 2 = stage 3 shows that the checkout's fsbpyr, built by itself, builds itself again
unchanged ([below](#why-stages-2-and-3-must-be-the-same-bytes)). It shows it for fsbpyr's Python
code, its dependencies and its boot code. It does not check the runtime: every stage carries the
release's launcher, CPython and standard library, whatever the checkout's runtime sources say:
the launcher in `src/fsbpyr/launcher`, the pins in `pins.py`, and how `src/fsbpyr/runtime/`
links the stub and selects and compiles the standard library. A change to any of them since the
release is neither built nor checked by this path; `make bootstrap` builds and checks it. The
comparison of the next section shows both.

## How the layout changes from stage to stage

The diagram follows the parts of an executable as [Layout](Dev-Executable-Format.md#layout)
lists them, top to bottom, and gives for each stage of both ways in where the part's bytes come
from. It was measured in the general case, a checkout that differs from the release: a scratch
commit on top of 5211d5d that changed the docstring of one boot module (`_fsbpyr_rt/gc.py`) and
added a comment line to `launcher/main.c`. Each pair of stages was compared with fsbpyr's
payload reader ([`payload/reader.py`](../src/fsbpyr/payload/reader.py)): the stub, every field
of the header, every key of the build information, every table whole and record by record, the
decoded content of the standard library, the boot packages and each distribution, the blob data
and the trailer.

![Each part of the layout in stages 1, 2 and 3, from source and from a released fsbpyr: green the checkout's, purple the release's or what differs because of it, yellow the same content at other string offsets, white the same in the stages it spans](images/self-hosting-layout.svg)

From source, every part is the checkout's in every stage, and the three stages are one file
(`bd865477…`). From the release:

- **Stub and padding.** The release's in all three stages (runtime `…-6fe9008f23af`,
  `stub_sha256` `de9eebc3…`, the SHA-256 of the stub with its padding): the changed
  `launcher/main.c` is in none of them. From source they carry the stub linked from it (runtime
  `…-8e450a0e49b6`, `stub_sha256` `8246e164…`).
- **Header.** The fixed fields (the magic, version and header size, the Python version and
  bytecode magic, the native cache's `generation_id`, the app name and the entry point) are the
  same in every stage. The build information differs in one key: `fsbpyr_version` is the
  builder's version, the release's (`0.28.0-b9b1144d`) in stage 1 and the checkout's
  (`untagged-<commit>`, stage 1's own) in stages 2 and 3. (`fsbpyr`, the builder's
  `__version__`, would differ as well after a version change; both were 0.28.0 here.) Its `git`,
  `project`, `lock` and `dists` are the checkout's in every stage, and `resources`, a digest over
  the runtime's and the distributions' files, is the same in every stage; its `runtime` and
  `stub_sha256` are the release's. In stage 1 the header also gives the build information and
  STRTAB other lengths, BLOBS one record more, and the tables after BLOBS offsets 32 bytes on.
- **Tables.** STRTAB holds the build information, after the app name and the entry point, so it
  differs in stage 1. BLOBS has one record per distinct blob (the members of blocks keyed by
  content) and one per block; in stage 1 it has one more, from the boot packages below. MODULES
  and FILES name strings by their offset in STRTAB and blobs by their index in BLOBS, so in
  stage 1 the other length of the build information moves their strings and the extra blob
  moves the index of every later blob (324 MODULES and 324 FILES records point elsewhere, at the
  same content). DISTS names strings only, so only its string offsets differ. CLOSURES and
  DIGESTS are empty in every stage (the 3.14 fsbpyr carries no native files; on 3.12 and 3.13
  backports.zstd's extension module is one), and STARTUP lists the same five modules.
- **Blob data.** The standard library, with four of the five startup modules (`encodings`,
  `encodings.aliases`, `encodings.utf_8`, `linecache`), is the release's in every stage.
  fsbpyr, click and packaging, with their dist-info, are the checkout's in every stage: uv
  installs them from the checkout and `uv.lock` each time. The boot packages `_fsbpyr_boot` (the
  fifth startup module) and `_fsbpyr_rt` are the builder's: the release's in stage 1, the
  checkout's in stages 2 and 3. An executable of fsbpyr holds each boot module twice, as the boot
  package and inside the fsbpyr distribution (`fsbpyr/boot/`). Where the two sources are the same
  they share one blob; in stage 1 the changed module's two sources differ, which is the extra
  BLOBS record.
- **Trailer.** The payload size grows with the extra blob in stage 1, and the digest covers every
  byte before it, so it differs wherever anything does.

With the unchanged checkout of the earlier runs (5211d5d, whose code is v0.28.0's), stages 1 and
2 differ only in the builder's version and in what its length moves: `fsbpyr_version`
(`0.28.0-b9b1144d` against `untagged-5211d5d9`), STRTAB, the string offsets in MODULES, FILES and
DISTS, the lengths of the build information and STRTAB in the header, and the digest. Every blob
is the same in the release and in all three stages, and stages 2 and 3 are `make bootstrap`'s
file. The release itself differs from stage 1 not in `fsbpyr_version` but in `git`, its own tag
and commit, which makes its build information 5 bytes longer and moves the tables after STRTAB
8 bytes on.

## Why stages 2 and 3 must be the same bytes

In each way in, the columns of stages 2 and 3 in the diagram are the same, row for row:

- **The same inputs.** Both are built from the same checkout and `uv.lock`, by the same uv, for
  the same Python and target.
- **The same builder.** Stage 2 is built by stage 1 and stage 3 by stage 2. Both builders run
  fsbpyr's code from that checkout: the same `fsbpyr` package, whose `fsbpyr.boot` each one makes
  the boot packages it writes from, the same version (from the same commit), and the same
  runtime, handed on unchanged. Only the boot packages a builder itself starts with can differ:
  they come from the builder before it, so a stage 1 built by a release has the release's.
- **Deterministic builds.** The same inputs give the same bytes: the writer sorts every table and
  places blobs in a fixed order, bytecode is marshalled canonically, a FILES record holds no
  time ([Executable format](Dev-Executable-Format.md#files-record-24-bytes-keyed-by-site-relative-posix-path)),
  and the build information names no absolute path, build time or host; the paths in it, such
  as the lock's, are relative to the project
  ([Executable format: Build information](Dev-Executable-Format.md#build-information),
  [Architecture](Dev-Architecture.md#design-rules-that-cut-across)).

Write `B(x)` for "executable `x` builds the checkout". Then stage 2 is `B(stage 1)` and stage 3
is `B(stage 2)`, and stage 3 = stage 2 means `B(stage 2)` = stage 2: stage 2 is a fixed point,
which builds exactly itself again, and so does every executable it builds after it. If stage 3
is not stage 2, one of two things is wrong. Either the build depends on something that is not
one of its inputs (a time, a hash seed, the order of a directory listing, the name of a
temporary directory), or stage 1 and stage 2 build differently although both run the same
sources: then something of how stage 1 was made reached stage 1 and through it stage 2 (a
runtime handed on wrongly, a module stored wrongly, bytecode that is not canonical). Either way
fsbpyr does not build itself faithfully, and the fault would show in no single build, only in
the comparison. Where stage 1 is stage 2, as in `make bootstrap` here, stages 2 and 3 are built
by the same bytes, and the comparison is a determinism check: one program run twice on the same
inputs.

The comparison cannot see three things:

- **The runtime.** Stages 2 and 3 carry the runtime that stage 1 carries, its stub and its
  compiled standard library, so they cannot show whether building the runtime is reproducible.
  `test_the_launcher_links_reproducibly` links the launcher twice and compares, and
  `tests/integration/test_bytecode_determinism.py` checks the standard library's canonical
  bytecode ([Testing](Dev-Testing.md#determinism-checks)); an independent rebuild from source,
  check (b) of [Verifying a release](../CONTRIBUTING.md#verifying-a-release), compares the whole
  file.
- **The place.** Both stages are built in one run, on one host, from one checkout path, with the
  same caches, user, umask and CPUs, so the comparison cannot show that the build does not
  depend on them. `test_builds_do_not_depend_on_the_build_environment` builds again from another
  checkout path with other caches, umask 077 and one CPU core
  ([Testing](Dev-Testing.md#determinism-checks)); the independent rebuild also runs on another
  host, as another user.
- **A shared defect.** A fault that both builders share passes from stage 2 to stage 3
  unchanged. What an executable does is checked by the smoke test of every build,
  `inspect --verify` of every stage and the test suite.

Two builds are needed before the comparison means this, as in GCC's bootstrap. Stage 1 is not
built by an fsbpyr executable of the checkout. From a release, another fsbpyr builds it, which
records its own version and adds its own boot code; from source, the checkout's fsbpyr builds it
on the venv's CPython and zstd. Either way stage 1 may differ from stage 2 for reasons that say
nothing about whether the checkout builds itself faithfully. Stage 2 is the first stage built by
an fsbpyr executable of the checkout, and stage 3 checks it.

## Where the check runs

- `make bootstrap` (step 3 above), wherever it runs; CI and the release build run it too, as
  [Verifying a release](../CONTRIBUTING.md#verifying-a-release) describes.
- `test_self_hosting_reaches_a_fixed_point` in the test suite, which also requires stage 1 to
  equal stages 2 and 3 ([Testing: Determinism checks](Dev-Testing.md#determinism-checks)).
- By hand, from a release, as [above](#from-a-released-fsbpyr).
