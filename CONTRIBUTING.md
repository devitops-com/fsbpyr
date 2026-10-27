# Contributing

Bug reports and pull requests are welcome. A vulnerability goes to a private report, as
[`SECURITY.md`](SECURITY.md) describes, not to an issue.

## Packaging fixes

Every packaging fix (a module, file or library that a built executable lacks or gets wrong)
adds a fixture or corpus test that fails without the fix: a project under
`tests/fixtures/projects/` that the end-to-end tests build and run, or an app in the corpus
(`tests/corpus`, with the rules in its README). A bug found once stays tested.

## Building from source

A checkout needs `make` and [uv](https://docs.astral.sh/uv/). On Debian trixie it also needs
the Python that `.python-version` pins (`uv python install "$(cat .python-version)"`), `curl`
and `zstd`; on any other Linux x86-64 or aarch64 host, [podman](https://podman.io) instead. No
target installs or updates a tool: a missing one is named, and the target stops. `make help`
lists the targets. To install a release instead, see
[Installing](https://github.com/devitops-com/fsbpyr/wiki/User-Installing).

```
make bootstrap   # once: from source to an installed fsbpyr
make build       # dist/fsbpyr, built by the installed fsbpyr
make install     # dist/fsbpyr, its man page and completions under ~/.local
```

The runtime's launcher is linked with clang and lld 23 or newer: the pinned
python-build-standalone release is LLVM 23 bitcode, and trixie ships clang 22. `make bootstrap`
and the targets that build a runtime fetch, once and with `curl`, the LLVM release tarball for
the host's architecture that `tools/release/llvm23` pins (its version, then the SHA-256 of the
x86-64 and of the aarch64 tarball), check its SHA-256, and unpack what linking needs, about
600 MB, with `zstd` into `${XDG_CACHE_HOME:-~/.cache}/fsbpyr/toolchains`. With
`FSBPYR_LLVM23=0` they use a clang and lld 23 or newer on `PATH` instead.

**`make bootstrap`** (`tools/bootstrap.sh`) takes a checkout to an installed fsbpyr:

1. It checks for uv and the Python of `.python-version`; uv may not download that Python
   itself (`UV_PYTHON_DOWNLOADS=never`).
2. It installs the locked dependencies into the checkout's `.venv` and builds the runtime
   (`fsbpyr runtime build`): the pinned CPython from python-build-standalone and a Debian 12
   sysroot (glibc 2.36) are downloaded and checked against their SHA-256, the launcher is
   linked with clang and lld 23 (on aarch64 with compiler-rt's builtins, which have the
   outlined atomics that CPython's aarch64 objects call), and the standard library is
   compiled. This takes a minute or two, once: the runtime is cached in
   `${XDG_CACHE_HOME:-~/.cache}/fsbpyr`.
3. fsbpyr builds itself in three stages: fsbpyr run from source builds `dist/stage1/fsbpyr`,
   that executable builds `dist/stage2/fsbpyr`, and that one builds `dist/stage3/fsbpyr`. Each
   stage must pass `fsbpyr inspect --verify` (its digest is recomputed over the whole file),
   and stages 2 and 3 must be identical byte for byte: builds are reproducible, so a difference
   means fsbpyr does not build itself faithfully, and bootstrap stops there. Stage 3 is copied
   to `dist/fsbpyr`.
4. It runs `make install`.

On Debian trixie all four steps run on the host. On any other host steps 1 to 3 run in a
Debian trixie podman image of the host's architecture, the `Containerfile`'s `build` stage
(Debian's packages and the uv that `tools/release/uv.version` names, checked against its
SHA-256; that uv installs the Python of `.python-version` there), on the checkout mounted into
it; step 4 runs on the host. The container is IPv4-only (`--network pasta:-4`):
on podman's default network, TLS handshakes from Python with GitHub's download host time out.
The podman volume `fsbpyr-cache` keeps uv's cache, its Python, the runtime and clang and lld 23
between runs. `FSBPYR_BOOTSTRAP=podman make bootstrap` uses the container on trixie too.

**`make build`** builds the checkout with the installed fsbpyr (`~/.local/bin/fsbpyr`, else
`fsbpyr` on `PATH`) into `dist/fsbpyr`; like every `fsbpyr build`, it runs the new executable
once as a smoke test before it replaces the old one. The installed executable carries its own
runtime, so this needs no compiler, only uv. Without an installed fsbpyr it stops and says to
run `make bootstrap`. The new executable's `--version` is `X.Y.Z-<commit>` when HEAD carries
the tag `vX.Y.Z`, else `untagged-<commit>` (the commit's first 8 hex digits).

**`make install`** installs `dist/fsbpyr` (running `make build` first when it is missing), with
the man page and shell completions it prints:

| File | Installed as |
|---|---|
| the executable | `~/.local/bin/fsbpyr` |
| man page | `~/.local/share/man/man1/fsbpyr.1` (`man fsbpyr` finds it when `~/.local/bin` is on `PATH`) |
| bash completion | `~/.local/share/bash-completion/completions/fsbpyr` (loaded by bash-completion) |
| zsh completion | `~/.local/share/zsh/site-functions/_fsbpyr` (add the directory to `fpath` before `compinit`) |
| fish completion | `~/.local/share/fish/vendor_completions.d/fsbpyr.fish` |

`PREFIX=/usr/local` (as root) installs under another prefix; `BINDIR`, `MANDIR`,
`BASH_COMPLETION_DIR`, `ZSH_COMPLETION_DIR` and `FISH_COMPLETION_DIR` set one location each, and
`DESTDIR` is put before every path, for a staged install. `make uninstall` removes the same
files and takes the same variables; `make bootstrap` passes them on to its `make install`.

**Other Pythons.** `PYTHON=3.12` (3.12 to 3.15, on the command line only) makes `make build`
build that Python's runtime (cached after the first run), then `dist/fsbpyr-3.12` with fsbpyr
run from source in the locked environment `.venv-3.12`. `make bootstrap PYTHON=3.12`
(`tools/bootstrap.sh --python 3.12`) runs steps 1 to 3 for it, into `dist/stage1-3.12` to
`dist/stage3-3.12` and `dist/fsbpyr-3.12`. Neither installs it, and `make install` refuses
`PYTHON`. uv must find that Python and may not download it: install it first
(`uv python install 3.12`). The checks and the tests take `PYTHON` too
([Running the tests](#running-the-tests)).

**Runtimes.** `fsbpyr runtime list` lists the runtime embedded in the running fsbpyr, if any,
then the runtimes in the cache, each with its Python and the glibc its launcher is linked for:
2.34 by default (linked against Debian 12's glibc 2.36, the launcher needs no symbol newer than
2.34), or 2.28 (`fsbpyr runtime build --glibc 2.28`, against Rocky Linux 8.10's sysroot, for
EL8 hosts). fsbpyr run from a checkout or a wheel, not as its own executable, always needs a
runtime in the cache, and a new fsbpyr version may need a new one: a runtime's id covers the
launcher's sources and flags and the pinned downloads. The build then names the
`fsbpyr runtime build` to run. In a checkout,
`uv run --locked fsbpyr runtime build --clang "$(bash tools/bootstrap.sh --llvm23)"` builds the
default Python's runtime with the pinned clang.

## Running the tests

Nothing in the test suite skips: a test either runs or fails, naming the tool or the setting
it lacks. Install everything under "What the tests need" below before you run them, and run
`make test-prereqs` to see what this machine still lacks, all at once. `make check` runs the static checks (ruff and mypy), `make test-ci` the core of the suite,
`make test` all of it but the corpus, and `make test-corpus` the corpus. `make check-c` runs
clang's static analyzer over the launcher's C sources; it is informational (full-suite.yml runs
it with `continue-on-error`) and exits 1 when the analyzer has findings. Every target that
checks or tests but `make test-vm` takes `PYTHON=3.X` (3.12 to 3.15, on the command line only)
to run on that Python's variant. The end-to-end tests
use a cached runtime or build one, which needs clang and lld 23 or newer: the `make` targets
put LLVM's clang and lld 23 that `tools/release/llvm23` pins first on PATH (fetched once, see
[Building from source](#building-from-source)); run directly, they fail without a clang 23 on
PATH. `make test-ci` also fails when branch coverage falls below `fail_under` in
`pyproject.toml`.

The tiers, from fastest to most complete:

- `make test-ci`: `tests/unit`, `tests/integration` and the core end-to-end tests
  (`test_build`, `test_runtime_app`, `test_imports`, `test_stub_hardening`, `test_multicall`,
  `test_scripts`, `test_loose_directory`, `test_single_script`, `test_install_links`,
  `test_pid1`, `test_unbuffered`, `test_file_overlay` and `test_pipfile_lock`: real builds of
  projects, several commands, projects without a build system, directories of scripts, single
  scripts, install links, services as PID 1 with unbuffered output, the `__file__` overlay and
  Pipfile.lock projects), with the coverage check.
- The full suite in CI (`.github/workflows/full-suite.yml`): every test and check there is, so
  that a release (`.github/workflows/release.yml`) only builds and publishes. The release build
  (`.github/workflows/build.yml`) with its smoke test and an independent rebuild compared byte
  for byte; everything but the portability tests and the corpus, as the user `tester` in a
  privileged Debian trixie container with rootless podman, so the root-only,
  supplementary-group and `podman unshare` tests run; the corpus, in a job of its own; the
  portability tests; and the page-size smoke test (below). It runs on every push to main that
  changes more than documentation (a merge; pull requests start no CI), weekly and on request,
  once per target (x86-64 on `ubuntu-24.04`, aarch64 on `ubuntu-24.04-arm`) and Python, with
  every tool and setting the tests need present (nothing skips), and `make release` and the
  publish job require a green run, aarch64 jobs included, for the commit a release is made
  from. The fuzzer runs for 600 s (`FSBPYR_FUZZ_SECONDS`) when `src/fsbpyr/launcher` or
  `tests/fuzz` changed since the last tag and on the weekly run, else for 30 s; the corpus it finds
  (`FSBPYR_FUZZ_CORPUS`) is kept in the Actions cache. The launcher programs the tests build
  are instrumented for llvm-cov (`FSBPYR_LLVM_COV_DIR`), and the run's summary shows the
  report; it is informational. A run takes about 40 minutes (37 to 40 in October 2026); the
  job's limit is 60 minutes.
- `make test`: everything but the corpus, on your machine, in 8 processes (`JOBS=` to change
  it; about 2.5 minutes on 24 cores). A test whose tool or environment is missing fails,
  naming it: root's own directories, a second user, a supplementary group, strace, groff,
  podman and the rest of "What the tests need".
- `make test-corpus`: the corpus (`tests/corpus`: 65 applications, each built and compared with
  its venv; `PYTHON=3.X` for another Python), in 8 processes. It needs the network (gigabytes
  of wheels on a first run, kept in uv's cache) and up to a few gigabytes of temporary space
  per app, and runs every app contained; about 4 minutes per Python on 24 cores once the wheels
  are cached.
- `make test-container`: everything but `tests/e2e/test_portability.py` and the corpus in the
  Containerfile's `test` stage, a Debian trixie container where `tools/test-setup.sh` has added
  those things and the suite runs as the user `tester`. It needs only podman, make and uv on
  the host; uv's cache and Pythons and the runtime's pinned downloads (CPython and the
  sysroot's packages, `FSBPYR_TEST_DOWNLOADS`) stay in two podman volumes, so a second run does
  not fetch them again: the summary's "runtime downloads" section says `seeded` or `fetched`
  for each. The stage has no podman, so it runs with `-m "not podman"`, which leaves out the
  tests that map a second range of user ids with `podman unshare`; every other test runs.
  The container is `--privileged` (as in full-suite.yml, for `unshare --pid`; in a rootless
  podman that adds nothing on the host), has the Pythons 3.12 to 3.15 installed with uv and a
  `/var/tmp` tmpfs that allows exec and set-id bits. It copies the checkout (without `.venv` and
  `dist`) into the container, so run it from a checkout with its `.git` directory, not a
  linked worktree.
- Portability (`make test-portability`, `tests/e2e/test_portability.py`; `podman run` on 12
  distributions, 11 on aarch64 where `archlinux:latest` has no image): needs podman and
  llvm-objdump on the host and a `make build` first (it tests that executable's builds of the
  fixtures); not part of `make test-container`. CI runs it per target and Python after the
  suite job.
- `make test-vm`: aarch64 apps booted in a QEMU system VM (`tools/vm-smoke.py`): EL8's kernel
  (64 KiB pages), which the aarch64 runners' 4 KiB kernels cannot test. 16K-page kernels are
  not tested (owner decision 2026-10-27). It needs `qemu-system-aarch64` (TCG, no KVM; yours to install) and
  aarch64 executables, because fsbpyr builds only for its host: `make test-vm VM_ARGS="--app
  PATH... --run 'COMMAND => TEXT'..."` (without `VM_ARGS` it only boots the kernel) copies
  each app into an initramfs on a pinned Debian trixie
  (or `--userland el8`, Rocky Linux 8.10) root file system, runs each command as root over the
  serial console, and fails on the wrong page size, a non-zero exit code or output without
  the text after `=>`. The kernels and root file systems are pinned by URL and SHA-256 in
  `tools/vm-pins.toml` (`FSBPYR_TEST_DOWNLOADS` keeps the downloads, about 150 MB) and
  checked on every use. A mirror may drop a pinned file (Rocky's `pub/` replaces old kernel
  builds): the script then exits 3 naming the file, CI reports a warning, and the pin is
  renewed with the new URL and SHA-256 (from `Packages.gz` or the repository's `primary.xml`).
  A boot takes under a minute. CI runs it in the `vm-smoke` job after the
  suite job, with the aarch64 executable and two fixtures it builds (`hello`, and `native` with
  two native extensions). It is not part of `make test`: the VM is no pytest test, and
  `tests/unit/test_vm_smoke.py` covers the pin table and the script's own code.
- EL8 (glibc 2.28), outside the release gate: `make test-el8` runs the stub-dependent tests with
  the EL8 stub (`make test-el8-stub`: the launcher, the `_fsbpyr` contract and native wheels at
  manylinux_2_28, in the EL8 test mode `FSBPYR_TEST_GLIBC=2.28`, in which every `fsbpyr build`
  of a test is `--glibc 2.28` and `tests/e2e/test_el8.py` runs besides, with both runtimes) and
  the portability matrix with apps built for glibc 2.28, on the 12 images and `rockylinux:8` and
  `almalinux:8` (`make test-el8-portability`, podman; EL8 images are in no other run).
  `.github/workflows/el8.yml` runs them for a release, per target, on the default Python only
  (every Python's stub is still linked and checked, below), with a native-heavy corpus subset
  (`FSBPYR_CORPUS_APPS`, the mode's `pandas-csv`, `lxml`, `pillow` and `scipy`) and, on aarch64,
  the 64 KiB-page VM smoke test with the EL8 userland (`tools/vm-smoke.py --kernel 64k
  --userland el8`; a pinned file gone from its mirror fails this job, since nothing else tests
  the 64 KiB pages: renew the pin and retry). Only what differs from a default build is tested:
  the stub. (`tests/e2e/test_el8_use_cases.py`, EL8 builds of a directory of scripts, a single
  script and a project of several commands run on the host, builds the EL8 runtime itself and is
  in none of these lists: `make test` and the full suite run it.) The EL8 stub is linked twice
  from the tag's sources in independent caches (`tools/release/el8-stub.py` checks it needs no
  GLIBC symbol above 2.28 and carries the runtime id its release's fsbpyr expects) and the two
  must be byte for byte identical. **The workflow is not part of the release and does not gate
  it**: `release.yml` and `full-suite.yml` do not wait for it or know it, and a release may have
  EL8 stubs or not. It starts on the push of a version tag, in parallel with `release.yml`, and runs
  on the tag itself (a workflow that ran in the default branch's context and checked out a tag's
  code was flagged by CodeQL, D46); a `build` job builds the tag's executable as `build.yml`
  does (reproducible: the release asset's bytes) for the portability and VM jobs. Only the
  `publish` job, after every other job passed and the release of the tag exists (it waits up to
  an hour for `release.yml` to publish it), writes: it creates the companion release `el8-vX.Y.Z`
  as a draft on the tag's commit, uploads the eight `fsbpyr-stub-3.X-<target>-glibc2.28.xz` and
  `SHA256SUMS-glibc2.28` to it, attests them and publishes it (it becomes immutable) with
  `--latest=false`, so that `releases/latest` keeps pointing at `vX.Y.Z`. It does not touch the
  release `vX.Y.Z`: an immutable release accepts no later upload, which is why the stubs are not
  attached to it (REL-1). A rerun that finds `el8-vX.Y.Z` published with the same sums publishes
  nothing, one that finds other sums fails (a published release cannot be changed), and a draft
  left by a failed run is deleted and made again. A version has EL8 stubs when the release
  `el8-vX.Y.Z` exists; `make release-check` verifies it (and prints `skip: no EL8 stubs` when
  there is none). To publish them for a tag that has no companion release, or to retry a failed run, dispatch the workflow on the tag's ref: `gh workflow run
  el8.yml --ref vX.Y.Z` (or Actions, EL8, Run workflow, choosing the tag; it has no inputs, and
  dispatched on a branch the plan job finds no tag and nothing runs). Everything runs from the
  tag, workflow file included, so the tag must have the EL8 support (`fsbpyr runtime build
  --glibc`, `tools/release/el8-stub.py`, `tests/support/baseline.py`, el8.yml itself); an older
  one fails in the first job with a message. A run whose `publish` found no release in an hour
  says so: dispatch it again on the tag's ref once the release exists. On a 24-core machine `make test-el8-stub` takes about 3.5 minutes with 4 processes
  (the EL8 and default runtimes linked from cached downloads) and `make test-el8` about 3
  minutes once the runtimes and images are cached; how long the jobs take on a runner is not yet
  measured (each is meant to stay near 20 minutes).

### What the tests need

Install these before you run the whole suite; a subset needs only what its tests use, and a
test that lacks something fails with a line that names the Debian package or the setting.
`tools/test-setup.sh` installs most of it in the Containerfile's `test` stage and in the
full-suite container, and `make test-prereqs` lists what this machine lacks.

Tools (Debian package in parentheses; on Debian trixie `apt-get install git make gcc binutils
llvm-22 strace groff-base zstd tar xz-utils curl wget openssl dash bash busybox util-linux
coreutils podman uidmap passt` installs all of them but uv and LLVM 23):

- `git` (git) and `make` (make);
- `uv` at the version `tools/release/uv.version` names (not packaged), with the Pythons 3.12 to
  3.15 it runs the tests on: `uv python install 3.12 3.13 3.14 3.15` (the tests never let uv
  download one);
- clang and lld 23 (not packaged): the `make` targets put LLVM 23 first on PATH; run directly,
  put fsbpyr's own (`bash tools/bootstrap.sh --llvm23`) there;
- `cc` (gcc), `readelf` and `objdump` (binutils), `llvm-objdump` (llvm-22, or LLVM 23's own);
- `strace` (strace) and `groff` (groff-base);
- `zstd` (zstd), `tar` (tar), `xz` (xz-utils), `curl` (curl), `wget` (wget) and `openssl`
  (openssl);
- `dash` (dash), `bash` (bash) and `busybox` (busybox), which install.sh's tests run it under;
- `unshare` and `prlimit` (util-linux), and `ls` (coreutils);
- `podman` (podman, with uidmap and passt), rootless: your user needs ranges in `/etc/subuid`
  and `/etc/subgid`.

The environment:

- a normal user, not root: root ignores file modes and owns `/usr`, and the ownership tests
  need a user (a few tests assert root's own behaviour and run as either);
- at least one supplementary group (`tools/test-setup.sh` makes the group `testers`);
- user namespaces for unprivileged users: `unshare -r true` works (on Ubuntu,
  `kernel.apparmor_restrict_unprivileged_userns=0`);
- `no_new_privs` unset, and `TMPDIR` (`make`: `TESTTMP`, default `~/tmp`) on a file system that
  allows set-id bits (not `nosuid`; `/tmp` is often a `nosuid` tmpfs);
- a file system there that takes 999-byte symlink targets (a tmpfs or a disk; not a container's
  overlay), with at least 10% of it free;
- a directory in `/tmp` that root owns (`sudo mkdir -m 755 /tmp/fsbpyr-test-root-owned`), and
  a searchable directory of another user that is not root under `/var/lib`, `/etc`,
  `/var/spool` or `/home` (`/var/lib/fsbpyr-other`; `tools/test-setup.sh` makes both);
- a glibc host, and the network (python-build-standalone, the sysroot's packages and wheels
  are fetched once and kept).

A case that does not apply to the Python, the target or the mode is not part of the run: its
test is not generated or is left out (`-m "not podman"` in the stub-only environments, the
packages without a wheel in `tests/support/no-wheels.txt`), or it asserts the behaviour of that
case. The one place where tests fail by design is user-mode QEMU (arm64 checks on an x86-64
host): `/proc` shows the emulator, so
`tests/unit/boot/test_mp.py::test_children_of_any_other_program_are_refused` fails there. That is a limitation of the emulation, not a skip.

## Documentation-only changes

A pull request that changes only documentation (`*.md` files and `docs/`) starts no full
suite when it is merged: full-suite.yml leaves such pushes to main out (`paths-ignore`), its
corpus included. Never put a CI skip instruction (`[skip ci]` and the like) in a merge commit's
message instead: GitHub applies it to a tag pushed on that commit too, so release.yml would not
run, and `make release` refuses such a commit. Before merging, run the tests that read
documentation: `uv run pytest tests/unit/test_cli_docs.py`
(the README's quickstart and `docs/compatibility.md`'s tables against the code, and that the
commands under [Verifying a release](#verifying-a-release) name no version), and `uv run pytest
tests/e2e/test_build.py -k quickstart` when the README's quickstart changed. A release made
from such a commit stands on the run of the newest commit before it that full-suite.yml ran
for: `tools/release/green-run.sh`, which `make release` and the publish job use, accepts it
only when nothing but documentation changed since, and names that run. A change to code,
tests, configuration (`pyproject.toml`, `uv.lock`, the workflows, the Makefile) or the release
tools runs the full suite when it is merged.

## Verifying a release

A release's executables are built by GitHub Actions from its `vX.Y.Z` tag (`release.yml`, which
runs `build.yml`) and attested there. A pushed tag never moves, and every release from 0.19.0 on
stays published as it is: a version whose release fails is burned, and the fix comes as the
next version. The steps below take the latest release's `fsbpyr-3.14-x86_64-linux-gnu`; another
asset works the same. They need curl, git, gh, uv and python3, and the rebuilds run only on a
machine of the asset's architecture, since fsbpyr builds for its host.

The attestation comes first, before anything runs the file. It shows that this repository's
release workflow built the file from the tag:

```
asset=fsbpyr-3.14-x86_64-linux-gnu   # or another, such as fsbpyr-3.12-aarch64-linux-gnu
tag="$(gh release view --repo devitops-com/fsbpyr --json tagName --jq .tagName)"
curl -fLo "$asset" "https://github.com/devitops-com/fsbpyr/releases/download/$tag/$asset"
gh attestation verify "$asset" --repo devitops-com/fsbpyr \
  --signer-workflow devitops-com/fsbpyr/.github/workflows/release.yml --source-ref "refs/tags/$tag"
```

If it fails, stop there. `make release-check` makes the same check for every executable of a
release.

**(a) The executable rebuilds itself** from the tag, with the uv that built it. uv builds the
project's wheel, stamping its own version into the wheel's metadata, and installs every
distribution, so another uv can give other bytes from identical sources; an fsbpyr executable
that builds with another uv warns. The executable records its uv (`uv` in `fsbpyr inspect`,
`.uv` in `--json`), the one that the tag's `tools/release/uv.version` names. If yours differs,
`sh src/tools/release/install-uv.sh DIR` installs that uv into `DIR`, checked against its
SHA-256: put `DIR` first on `PATH` and run the rest again. uv also needs the asset's Python
(`uv python install 3.14`).

```
chmod +x "$asset"
git clone --branch "$tag" https://github.com/devitops-com/fsbpyr.git src
want="$(./"$asset" inspect "$asset" | awk '$1 == "uv" { print $2; exit }')"
[ "$(uv --version | cut -d' ' -f2)" = "$want" ] || echo "install uv $want first: this uv differs"
python="${asset#fsbpyr-}"; python="${python%%-*}"   # the asset's Python: 3.14, 3.12, ...
(cd src && ../"$asset" build . --python "$python" -o ../rebuilt)
cmp "$asset" rebuilt && echo identical
```

`--python` makes the executable rebuild itself with the runtime it carries. Without it the
checkout's `.python-version` decides, and an executable of another Python hands the build to
that Python's executable of the same release, which it fetches. This checks the payload:
fsbpyr's code and its dependencies are what the tag says. It cannot vouch for the launcher,
CPython and the standard library: an fsbpyr executable builds with the runtime it carries,
copying them from itself ([D4](docs/IMPLEMENTATION_PLAN.md#2-key-decisions)), so a changed
launcher would pass.

**(b) An independent rebuild from source** checks those too. It compiles the launcher from the
tag's sources and links it with the clang and lld that the tag pins (`tools/release/llvm23`);
CPython, which the launcher links in, and the standard library, which is compiled to bytecode,
come from the python-build-standalone archive that the tag pins by SHA-256. So (b) proves the
launcher, and that CPython and the standard library are that pinned build; it does not compile
CPython. `tools/bootstrap.sh --from-source` runs steps 1 to 3 of `make bootstrap`
([Building from source](#building-from-source)) and installs nothing. With `FSBPYR_UV_VERSION`
it refuses any other uv. A new `XDG_CACHE_HOME` makes it fetch everything again and link the
runtime in this run: a runtime already cached under the same id would be reused, and the id
does not cover the clang that linked it. uv must find, without downloading it, the Python of
the tag's `.python-version` for the default asset (`uv python install "$(cat src/.python-version)"`)
or any patch release of the asset's Python for another (`uv python install 3.12`); curl and
zstd fetch clang and lld.

```
if [ "$python" = "$(cut -d. -f1,2 src/.python-version)" ]; then variant=; else variant="-$python"; fi
XDG_CACHE_HOME="$PWD/cache" FSBPYR_UV_VERSION="$want" \
  bash src/tools/bootstrap.sh --from-source ${variant:+--python "$python"}
cmp "$asset" "src/dist/stage3$variant/fsbpyr" && echo identical
```

The full suite runs the same rebuild in `build.yml`'s `reproduce` job, in a Debian trixie
container, for every Python and both targets, as another user, from another path, with other
caches and umask 077; its `compare` job checks the result against the build byte for byte. A
release is made only from a commit whose full suite passed, and its build (`make bootstrap`
on the tag, then `make build` for the default Python) differs from the full suite's only in the
tag it records. The release build's log lists the image digest, the uv version, the LLVM pin
and the version of `xz-utils`; it installs nothing from Debian but ca-certificates, git, curl,
xz-utils, make and zstd. The launcher's bytes depend on the clang and lld that link it:
`fsbpyr inspect` lists the launcher's `.comment` lines, which name them, as `toolchain`.

An executable also records the version of the fsbpyr that built it (`built with` in
`fsbpyr inspect`), so an fsbpyr of another version, or of an untagged commit, gives a different
file from the same sources; such a build works the same, only the bytes differ. For the
releases from 0.19.0 to 0.27.0, the README.md of the tag describes how to verify that release.

Each executable also has an SBOM, `<asset>.cdx.json` (CycloneDX 1.6, made by
`tools/release/sbom.py` from `fsbpyr inspect --verify --json`). It lists the executable with its
SHA-256, fsbpyr, CPython, the libraries the runtime links statically (OpenSSL, SQLite, expat and
zlib with the CPEs that vulnerability scanners match them by, and libmpdec) and the
distributions from an index with their SHA-256. `release.yml` attests it to its executable:

```
curl -fLO "https://github.com/devitops-com/fsbpyr/releases/download/$tag/$asset.cdx.json"
gh attestation verify "$asset" --repo devitops-com/fsbpyr --predicate-type https://cyclonedx.org/bom \
  --signer-workflow devitops-com/fsbpyr/.github/workflows/release.yml --source-ref "refs/tags/$tag"
./"$asset" inspect --verify --json "$asset" | python3 src/tools/release/sbom.py - | cmp - "$asset.cdx.json"
```

The last line makes the SBOM again from the executable, with the tag's `sbom.py` (standard
library only), and compares it with the published file. `make release-check` makes the
attestation check for every executable, and the full suite's `compare` job checks that the
rebuild's SBOM, made the same way, is the build's byte for byte; nothing in the repository runs
the last line against a published release.
