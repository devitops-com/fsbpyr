# Self-hosting

A released `fsbpyr-3.X-<target>` is an app fsbpyr built from this repository: the project
`fsbpyr`, entry point `fsbpyr.cli:main`, with click and packaging (and, on Python 3.12 and 3.13,
backports.zstd) from its `uv.lock`. As in GCC's bootstrap, which builds the compiler three times
and compares the last two builds ([Building GCC](https://gcc.gnu.org/install/build.html)), fsbpyr
must reproduce itself: stage 1 is built by fsbpyr run from source or by a released fsbpyr, stage
2 by stage 1 and stage 3 by stage 2, and stages 2 and 3 must be the same bytes. This is R2, R3
and D15 of the implementation plan ([§0](../docs/IMPLEMENTATION_PLAN.md#0-requirements),
[§2](../docs/IMPLEMENTATION_PLAN.md#2-key-decisions)).

The two ways in differ in what builds stage 1, and so in the runtime every stage carries:

- **From C and Python sources** (`make bootstrap`, [below](#from-c-and-python-sources-make-bootstrap)):
  fsbpyr run from source builds stage 1, with a runtime linked from the launcher's C sources and
  the pinned CPython and glibc sysroot, or found in fsbpyr's cache.
- **From a released fsbpyr** ([below](#from-a-released-fsbpyr)): with the runtime it carries, so
  no compiler is needed.

![fsbpyr builds itself in three stages, from C and Python sources (make bootstrap) or from a released executable; in both, every build runs in the checkout, and stages 2 and 3 must be the same bytes](images/self-hosting-stages.svg)

The runs this page reports were made on 2026-11-03 on x86-64 (Debian trixie) with uv 0.13.6 and
CPython 3.14.10 (python-build-standalone 20261026), the default Python, from the release v0.30.0's
`fsbpyr-3.14-x86_64-linux-gnu` and from commit 976dd4f, whose `src/` is that of v0.30.0.
The layout comparison also used a scratch commit on top of 976dd4f
([below](#how-the-layout-changes-from-stage-to-stage)).

## What a build takes from where

Each part of the new executable comes from one of three places, and two tools of the build host
work on it:

| From | What | Code |
|---|---|---|
| **the checkout**, the project being built | fsbpyr's code and dependencies as `uv.lock` pins them, built and installed by uv into a temporary site; HEAD's commit and tag, recorded as `git`, giving the `--version` (`X.Y.Z-<commit>` on a tag `vX.Y.Z`, else `untagged-<commit>`) | [`staging.py`](../src/fsbpyr/staging.py), [`vcs.version_string`](../src/fsbpyr/vcs.py#L101-L107) |
| **the runtime the builder uses** | the stub (the launcher, CPython linked in) with its zero padding; the standard library with its startup modules; the runtime id, `stub_sha256` and component versions in the build information | [`registry.select_runtime`](../src/fsbpyr/runtime/registry.py#L312-L332) |
| **the builder's own code** | the boot packages `_fsbpyr_boot` and `_fsbpyr_rt`, from the running fsbpyr's `fsbpyr.boot`, not the project's; how the build stages, analyses, compiles and writes; its version, recorded as `fsbpyr_version` (`built with` in `inspect`) | [`assemble.boot_modules`](../src/fsbpyr/assemble.py#L38-L54), [`vcs.own_version`](../src/fsbpyr/vcs.py#L110-L118) |
| **the build host's tools** | uv, which builds fsbpyr's wheel (stamping its version in) and installs every distribution, recorded as `uv`; the builder's CPython, which compiles the bytecode, and its zstd library, which compresses, recorded as `zstd`: the runtime's in an executable, the venv's from source | [`compat.py`](../src/fsbpyr/compat.py), [`compiler.py`](../src/fsbpyr/compiler.py) |

An fsbpyr executable builds with its embedded runtime when its Python and glibc baseline are the
build's ([`Runtime.from_executable`](../src/fsbpyr/runtime/registry.py#L257-L294)): it verifies
its whole-file digest, then hands on its stub prefix byte for byte and its standard library,
leaving out the boot packages, which every build makes anew. From source, fsbpyr takes it from
the cache (`find_runtime`), which never builds one. Nothing compares the embedded runtime's id
with the one the checkout's launcher sources and pins would give
([`runtime_id`](../src/fsbpyr/runtime/registry.py#L127-L139)), so a runtime passes unchanged from
stage to stage ([below](#what-the-release-path-checks)). Both kinds of runtime:
[Runtime modules](Dev-Runtime-Modules.md#embedded-and-cached-runtimes).

## From C and Python sources: make bootstrap

`make bootstrap`, which CI and the release build run too
([Verifying a release](../CONTRIBUTING.md#verifying-a-release)), runs the four steps of
[`tools/bootstrap.sh`](../tools/bootstrap.sh), numbered in its header and
[CONTRIBUTING.md](../CONTRIBUTING.md#building-from-source); `from_source`
([lines 165 to 198](../tools/bootstrap.sh#L165-L198)) is steps 1 to 3, which
`bash tools/bootstrap.sh --from-source` runs alone. On Debian trixie it runs on the host unless
`FSBPYR_BOOTSTRAP=podman`; elsewhere steps 1 to 3 run in the `Containerfile`'s `build` stage, a
Debian trixie podman container that installs the pinned Python at start, with the venv in
`/opt/venv` and the caches on the volume `fsbpyr-cache`.

1. **Prerequisites.** uv on `PATH`; the Python `.python-version` pins (3.14.10), which uv finds
   and, on the host, never downloads; curl, zstd, tar and sha256sum to fetch LLVM's clang and lld
   23, pinned by SHA-256 in [`tools/release/llvm23`](../tools/release/llvm23), once into
   `${XDG_CACHE_HOME:-~/.cache}/fsbpyr/toolchains` (`FSBPYR_LLVM23=0`: a clang and lld 23 or
   newer on `PATH` instead). A missing tool stops the bootstrap with its name; nothing is
   installed on the host.
2. **The locked environment and the runtime.** `uv sync --locked` makes `.venv`;
   `uv run --locked fsbpyr runtime build --clang <clang 23>` fetches the python-build-standalone
   archive and the Debian 12 sysroot packages pinned by SHA-256 in
   [`pins.py`](../src/fsbpyr/runtime/pins.py), links the launcher's C sources
   ([`src/fsbpyr/launcher`](../src/fsbpyr/launcher)) with CPython's prebuilt static libraries by
   full LTO into the stub, compiles the standard library with the archive's CPython, and saves it
   all as `runtimes/<id>/` in fsbpyr's cache
   ([Architecture: Building a runtime](Dev-Architecture.md#building-a-runtime)). The id digests
   the launcher sources and flags, the archive's pin, the sysroot and the layout version; while
   they stay the same, the cached runtime is used and nothing is fetched or linked.
3. **Three stages and the comparison.** Stage 1: `uv run --locked fsbpyr build . -o
   dist/stage1/fsbpyr`, with step 2's runtime; stage 2: `dist/stage1/fsbpyr build . -o
   dist/stage2/fsbpyr`; stage 3: `dist/stage2/fsbpyr build . -o dist/stage3/fsbpyr`. Each build
   smoke-tests the new executable before putting it in place, and `inspect --verify` must verify
   each stage. If `cmp` finds stages 2 and 3 different, the bootstrap stops with both SHA-256
   digests; otherwise stage 3 becomes `dist/fsbpyr`.
4. **`make install`** puts `dist/fsbpyr`, its man page and shell completions under `PREFIX`.

`make bootstrap PYTHON=3.12` runs steps 1 to 3 for that Python's variant, in `.venv-3.12` and
into `dist/stage1-3.12` to `dist/stage3-3.12` and `dist/fsbpyr-3.12`, and installs nothing.

Measured: from an empty fsbpyr cache (clang and lld 23 already fetched, uv's cache warm), steps 1
to 3 took 98 seconds: fetching the archive and the sysroot, linking the launcher,
compiling the standard library and the three stages. The runtime was
`cp314-x86_64-unknown-linux-gnu-ce4c77296a2f`, and all three stages were the same file, SHA-256
`c75df22f6ae7b291675becd5f750aa2614151f056055241eea9ecaa6be910c27`. With the cache kept, the
scratch commit of [the layout comparison](#how-the-layout-changes-from-stage-to-stage) took 44
seconds: its new runtime id (`…-2faa763df869`) made `runtime build` link the launcher and compile
the standard library again, with the archive and the sysroot from the cache.

Stage 1 equals stages 2 and 3 here, though built on the venv's CPython with a cached runtime:
`own_version` reads the same commit from the checkout's git state (`uv sync` installs it
editable) as an executable from its record; the boot packages are the checkout's throughout; the
venv's CPython is the pinned 3.14.10, like the runtime's, and both zstd libraries were 1.5.7 (on
3.12 and 3.13 every stage uses `backports.zstd` from `uv.lock`); and the writer keys block
members by content ([`_Blobs.key`](../src/fsbpyr/payload/writer.py#L227-L238)), so they pack the
same from the cache's zstd frames or an executable's raw content, and larger blobs are the same
frames, carried verbatim. That holds only while the venv's CPython and zstd match the runtime's.
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

Run as above on 2026-11-03, with only uv and git on `PATH` and `FSBPYR_CACHE_DIR` naming a
directory that did not exist, each build took about a second with uv's cache warm, and that
directory was never created. The report, in which `digest` is the integrity digest in the
trailer and `sha256` the file's SHA-256:

```console
app        fsbpyr 0.30.0 (untagged-976dd4fe)
built with fsbpyr 0.30.0-8d35b93c
digest     3ae9ba1fcb0e80af0b30d9e79e318c2e3be95d7f51ed7e4febdf9b28f6e36fad verified
sha256     ece5495b6b70b46ef59be492fbe4bafd164cd1e52e87e526da13df1b4d57afb7
app        fsbpyr 0.30.0 (untagged-976dd4fe)
built with fsbpyr untagged-976dd4fe
digest     969f8fc72ec0beac526cf7daee330d10addff02852fb7cd1f72d76551f7fa7aa verified
sha256     c75df22f6ae7b291675becd5f750aa2614151f056055241eea9ecaa6be910c27
app        fsbpyr 0.30.0 (untagged-976dd4fe)
built with fsbpyr untagged-976dd4fe
digest     969f8fc72ec0beac526cf7daee330d10addff02852fb7cd1f72d76551f7fa7aa verified
sha256     c75df22f6ae7b291675becd5f750aa2614151f056055241eea9ecaa6be910c27
stage 2 = stage 3
```

Stage 1 differs from stage 2: it was built with 0.30.0, stage 2 with stage 1. Stages 2 and 3 are
the same file that `make bootstrap` built from the same commit. That commit's launcher sources
and pins are v0.30.0's, so the runtime `make bootstrap` linked has the release's id
(`cp314-x86_64-unknown-linux-gnu-ce4c77296a2f`), and, linked with the same pinned clang and lld
23, the release's bytes: the stub and the standard library are the same part for part. Run in the
checkout of the release's own tag, with the uv that the release records, the first build gives
the release asset itself, byte for byte (checked for 0.30.0): that is check (a) of
[Verifying a release](../CONTRIBUTING.md#verifying-a-release).

The release and the checkout must share the payload format (version 7 at @VERSION@): stage 1,
written by the release, runs the checkout's code, which takes its runtime only from the
checkout's format (`spec_from_payload` in
[`payload/convert.py`](../src/fsbpyr/payload/convert.py#L24-L28)), and the checkout's boot code
must run on the release's launcher, which stages 2 and 3 carry. After a format change, or a boot
code change that needs a newer launcher, use `make bootstrap`.

### What the release path checks

Stage 2 = stage 3 checks fsbpyr's Python code, dependencies and boot code, not the runtime:
every stage carries the release's launcher, CPython and standard library, whatever the
checkout's `src/fsbpyr/launcher`, `pins.py` and `src/fsbpyr/runtime/` say; `make bootstrap`
checks those.

## How the layout changes from stage to stage

The diagram gives, for each part of the [layout](Dev-Executable-Format.md#layout) and each stage
of both ways in, where its bytes come from. The checkout differed from the release by a scratch
commit on top of 976dd4f that changed one boot module's docstring (`_fsbpyr_rt/gc.py`) and added
a comment line to `launcher/main.c`; fsbpyr's payload reader
([`payload/reader.py`](../src/fsbpyr/payload/reader.py)) compared each pair of stages field by
field, record by record and blob by blob.

![Each part of the layout in stages 1, 2 and 3, from source and from a released fsbpyr: green the checkout's, purple the release's or what differs because of it, yellow the same content at other string offsets, white the same in the stages it spans](images/self-hosting-layout.svg)

From source, every part is the checkout's in every stage, and the three stages are one file
(`27f2242b…`). From the release:

- **Stub and padding.** The release's in all three stages (runtime `…-ce4c77296a2f`,
  `stub_sha256` `ac95ab71…`), without the changed `launcher/main.c`; from source, the stub linked
  from it (`…-2faa763df869`, `7cf14ba0…`).
- **Header.** The fixed fields (magic, version, header size, Python version, bytecode magic, the
  native cache's `generation_id`, app name, entry point) never differ. Of the build information
  only `fsbpyr_version` does: the release's (`0.30.0-8d35b93c`) in stage 1, the checkout's
  (`untagged-<commit>`) in stages 2 and 3; `fsbpyr`, the builder's `__version__`, would too after
  a version change. `git`, `project`, `lock` and `dists` are the checkout's, `resources` (a
  digest over the runtime's and distributions' files) is the same, and `runtime` and
  `stub_sha256` are the release's. In stage 1 the build information and STRTAB have other
  lengths, BLOBS has two records more, and the tables after it sit 64 bytes on.
- **Tables.** STRTAB holds the build information, and BLOBS one record per distinct blob and one
  per block. As MODULES and FILES name strings by STRTAB offset and blobs by BLOBS index, 689
  of the 690 MODULES records and all 745 FILES records point elsewhere in stage 1, at the same
  content; DISTS names strings only.
  CLOSURES and DIGESTS are empty (the 3.14 fsbpyr carries no native files; on 3.12 and 3.13
  backports.zstd's extension module is one), and STARTUP lists the same five modules.
- **Blob data.** The standard library, with four of the five startup modules (`encodings`,
  `encodings.aliases`, `encodings.utf_8`, `linecache`), is the release's; fsbpyr, click and
  packaging with their dist-info are the checkout's, installed by uv from it and `uv.lock`. The
  boot packages `_fsbpyr_boot` (the fifth startup module) and `_fsbpyr_rt` are the builder's: the
  release's in stage 1, the checkout's after. Each boot module is stored as the boot package and
  in the fsbpyr distribution (`fsbpyr/boot/`), one blob while both sources match; in stage 1 the
  changed module's do not: the two extra BLOBS records.
- **Trailer.** The payload size grows with that blob, and the digest, over every byte before it,
  differs wherever anything does.

With the unchanged checkout (976dd4f, v0.30.0's code), stages 1 and 2 differ only in
`fsbpyr_version` (`0.30.0-8d35b93c` against `untagged-976dd4fe`) and what its length moves:
STRTAB, the string offsets in MODULES, FILES and DISTS, the lengths of the build information and
STRTAB in the header, and the digest. Every blob is the same in the release and all three stages,
and stages 2 and 3 are `make bootstrap`'s file. The release differs from stage 1 in `git`
instead, its own tag and commit, which makes its build information 5 bytes longer and moves the
tables after STRTAB 8 bytes on.

## Why stages 2 and 3 must be the same bytes

In both ways in, stages 2 and 3 match row for row in the diagram:

- **The same inputs**: checkout, `uv.lock`, uv, Python and target.
- **The same builder.** Stages 1 and 2 both run the checkout's `fsbpyr` package, whose
  `fsbpyr.boot` gives the boot packages they write, at the same version, with the same runtime
  handed on. Only the boot packages a builder itself starts with come from the builder before
  it.
- **Deterministic builds.** The writer sorts every table and places blobs in a fixed order,
  bytecode is marshalled canonically, a FILES record holds no time
  ([Executable format](Dev-Executable-Format.md#files-record-24-bytes-keyed-by-site-relative-posix-path)),
  and the build information names no absolute path, build time or host
  ([Executable format: Build information](Dev-Executable-Format.md#build-information),
  [Architecture](Dev-Architecture.md#design-rules-that-cut-across)).

Write `B(x)` for "executable `x` builds the checkout". Stage 3 = stage 2 means `B(stage 2)` =
stage 2: stage 2 is a fixed point, which builds exactly itself, as does everything it builds. If
stage 3 differs, either the build depends on something that is not an input (a time, a hash seed,
a directory listing's order, a temporary directory's name), or something of how stage 1 was made
reached stage 2 (a runtime handed on wrongly, a module stored wrongly, bytecode that is not
canonical): fsbpyr does not build itself faithfully, and no single build shows it. Where stage 1
is stage 2, as in `make bootstrap` here, the comparison is a determinism check: one program run
twice on the same inputs.

The comparison cannot see:

- **The runtime**, taken from stage 1. `test_the_launcher_links_reproducibly` links the
  launcher twice and compares, `tests/integration/test_bytecode_determinism.py` checks the
  standard library's canonical bytecode ([Testing](Dev-Testing.md#determinism-checks)), and an
  independent rebuild from source, check (b) of
  [Verifying a release](../CONTRIBUTING.md#verifying-a-release), compares the whole file.
- **The place**: one run, host, checkout path, caches, user, umask and CPUs.
  `test_builds_do_not_depend_on_the_build_environment` builds again from another checkout path
  with other caches, umask 077 and one CPU core; the independent rebuild runs on another host as
  another user too.
- **A shared defect**, which passes on unchanged. The smoke test of every build, `inspect
  --verify` of every stage and the test suite check what an executable does.

Stage 1 is not built by an fsbpyr executable of the checkout (a release adds its own version and
boot code; from source, the venv's CPython and zstd build it), so stage 2 is the first that
counts.
