# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions 0.15.2, 0.15.3, 0.16.0, 0.17.0, 0.17.1 and 0.18.0 have no `vX.Y.Z` tag; their release
commits are on main (d489698, 7c11a1c, 80a1f1b, 86b2645, 299355e and 21ac25d). The GitHub
releases of 0.18.1 and 0.18.2 were withdrawn; their tags remain.

## [Unreleased]

### Changed
- `make release` pushes the release tag alone, and the release workflow moves main to the
  release commit once it has published the release. A release now starts one workflow run, not
  three: pushing main as well ran `release.yml` and `full-suite.yml` again for a commit that
  changes only the version and the changelog. It follows the run with a plain line for each job
  that starts or ends, where `gh run watch` redrew the terminal.

## [0.23.0] - 2026-10-22

### Changed
- **The runtime is python-build-standalone 20261021's: CPython 3.13.17 and 3.14.9** (3.12.15
  is unchanged), linked with clang and lld 23.1.3. From 20261013 on PBS ships LLVM 23 bitcode,
  which Debian trixie's clang 22 cannot link, so the release uses LLVM's own release tarball,
  pinned by SHA-256 for x86-64 and aarch64 in `tools/release/llvm23`. An executable gets the
  new CPython when it is rebuilt with this fsbpyr. The licence notices take zstd's text from
  PBS, which ships it now, and still leave out zlib-ng's, which PBS ships too although its
  libz.a is classic zlib. Runtime ids, old → new:
  - x86-64: 3.12 `e6a982fd7d0c` → `a9a5adbe3f13`, 3.13 `344b4f521a8e` → `4d0cc0e40f7d`,
    3.14 `7c5b262ab766` → `97b0d36b1b68`;
  - aarch64: 3.12 `57b1c89666ae` → `a09051735e25`, 3.13 `8bc6d338fea0` → `2dda88f1c21f`,
    3.14 `cdd8f4741e5a` → `6d88769e0109`.

  PBS 20261021 still has SQLite 3.53.1, with CVE-2026-11822 and CVE-2026-11824 (FTS5, fixed in
  SQLite 3.53.2), and zlib 1.3.2, with CVE-2026-85091 (`gz_vacate`, no fix yet): no PBS release
  has those fixes yet, and the next runtime update follows the first one that does.
- **Building from source fetches clang and lld 23.** `make bootstrap` and the targets that build
  a runtime download LLVM's tarball for the host's architecture once (about 1.2 GB, checked
  against its SHA-256) and unpack what linking needs, about 600 MB, into
  `~/.cache/fsbpyr/toolchains`; they need `curl` and `zstd`. `FSBPYR_LLVM23=0` takes a clang and
  lld 23 or newer on `PATH` instead. A source checkout's Python is 3.14.9 (`.python-version`).
- **The release is built with uv 0.13.5 (was 0.13.1) on the current Debian trixie image**
  (`debian:trixie@sha256:4c252754…`). A release records its uv (`fsbpyr inspect` shows it),
  and rebuilding it as the README describes needs that uv.

### Fixed
- **The SBOMs name expat's version so that scanners match it.** 0.22.0's SBOMs recorded expat
  as `expat_2.9.0`, as Python reports it, so its CPE
  (`cpe:2.3:a:libexpat_project:libexpat:expat_2.9.0:…`) matched no vulnerability; it is now
  `2.9.0`. Checked with grype: an SBOM whose OpenSSL is set to 3.0.0 and expat to 2.4.0 lists
  their known vulnerabilities.
- **`make test` cleans up after itself.** `make test`, `make test-ci` and `make
  test-portability` give each run a directory of its own under `TESTTMP` (`~/tmp`) as
  `TMPDIR` and remove it when the run ends, also when it fails or is interrupted: uv's lock
  files and pytest's directories, which piled up in `~/tmp` (about 300 files a run), go with
  it. The runtime's pinned downloads stay in `~/tmp/fsbpyr-test-downloads`.

## [0.22.0] - 2026-10-22

### Changed
- **Breaking: every release asset names its Python.** 0.21.0 published the newest Python's
  builds without a version (`fsbpyr-x86_64-linux-gnu`) beside `fsbpyr-3.12-x86_64-linux-gnu`
  and `fsbpyr-3.13-x86_64-linux-gnu`, and copies of the x86-64 ones under the short names
  `fsbpyr`, `fsbpyr-3.12` and `fsbpyr-3.13`. Each executable is now `fsbpyr-3.X-<target>`
  (`fsbpyr-3.14-x86_64-linux-gnu`) with its `.xz`, `THIRD_PARTY_LICENSES-3.X-<target>.txt`
  and SBOM, and there are no other names. `fsbpyr self update` keeps to the Python the
  executable runs on: a 3.13 build updates to the latest 3.13 build, and a 3.14 build stays
  on 3.14 when a newer Python arrives. `install.sh` takes `--python X.Y` and installs the
  newest Python's build otherwise; the README's quickstart now uses it.
  **From 0.21.0:** its 3.14 builds look for `fsbpyr-<target>.xz`, which this release does
  not have, so `fsbpyr self update` fails on them; run the installer once instead
  (`curl -LsSf https://github.com/devitops-com/fsbpyr/releases/latest/download/install.sh | sh`).
  Its 3.12 and 3.13 builds update as before. Links to `releases/latest/download/fsbpyr` (the
  instructions of 0.20.0 and older) no longer work.

### Added
- **A CycloneDX SBOM for every release executable** (REP-7). Vulnerability scanners cannot see
  the OpenSSL, SQLite, expat and zlib inside an fsbpyr executable. Each executable now has
  `<asset>.cdx.json` (CycloneDX 1.6) beside it, made by `tools/release/sbom.py` from
  `fsbpyr inspect --verify --json`: the executable with its plain SHA-256, fsbpyr
  (`pkg:github/devitops-com/fsbpyr@vX.Y.Z`), CPython and the runtime components with their
  NVD CPEs, and the Python distributions from an index as `pkg:pypi` purls with the lock's
  hashes. The file is a function of that output: no timestamp, and a serial number derived
  from the SHA-256. `build` and `reproduce` each make one, `compare` checks them byte for
  byte, and a new `sbom` job attests each to its executable with `actions/attest-sbom`
  (predicate `https://cyclonedx.org/bom`). See
  [Verifying a release](README.md#verifying-a-release).
- **`FSBPYR_SELFTEST=natives` checks a machine before you deploy.** Run an app with it set
  and fsbpyr extracts every native extension, runs the target's dynamic loader in trace mode
  on each (no initialization code runs), and prints one line per missing library or symbol
  version, such as `missing libstdc++.so.6` or `missing version `GLIBCXX_3.4.32' of
  libstdc++.so.6`. It exits 1 if anything is missing and 0 otherwise. Libraries an extension
  opens later by name are not seen. Documented in docs/compatibility.md, the README and the
  man page. Other values of `FSBPYR_SELFTEST` behave as before.

### Fixed
- **A build killed with SIGTERM cleans up like one interrupted with Ctrl-C.** `fsbpyr` handles
  SIGTERM as SIGINT: the build removes the executable it was writing and keeps an existing
  output, prints "interrupted" and exits 130, where it used to die at once and leave a
  `.NAME.XXXX.tmp` beside the output.

## [0.21.0] - 2026-10-21

### Security
- **A privileged start is refused, and the multiprocessing and re-exec parent check compares
  credentials.** An executable run set-uid, set-gid or with file capabilities (the kernel sets
  AT_SECURE) exits 70 with "refusing a privileged start", because the cache location would
  still come from the caller's environment. This stops set-gid deployments and uses such as
  `setcap cap_net_bind_service+ep app`, which ran before. The D22/D23 check of a
  multiprocessing or `-m`/`-c` child's parent now also needs the parent's real and effective
  user and group ids to equal the child's. The launcher changed, so the runtime id changes.
  The new [docs/security.md](docs/security.md) is the threat model.

### Added
- **Linux aarch64 with glibc** (Phase 9, platforms step 2). An fsbpyr on an aarch64 host builds
  its runtime and its apps for aarch64, as the x86-64 one does for x86-64 (no cross builds).
  `runtime/pins.py` pins the python-build-standalone 20261009 `pgo+lto-full` archives for
  3.12.15, 3.13.16 and 3.14.8 (SHA-256s from the release's checksums and the downloads) and a
  glibc 2.28 sysroot from the same Debian 10 package versions as x86-64's, for arm64. The
  aarch64 objects call outlined atomics that Debian 10's libgcc 8 lacks, so the launcher links
  clang's compiler-rt builtins too (`libclang-rt-22-dev`; the link stops and says so when
  they are missing). libgcc is linked in on every target (`-static-libgcc`), so the launcher
  needs glibc's libraries only: on aarch64 the libcalls LTO adds (`__extenddftf2`,
  `__trunctfdf2`) had bound to `libgcc_s.so.1`; the x86-64 launcher is byte-identical, under
  a new runtime id. The native analysis takes the aarch64
  ELF machine and loader (`ld-linux-aarch64.so.1`) from the target table and the same
  manylinux_2_28 limits; the payload's xz natives use no BCJ filter there. `tools/bootstrap.sh`
  accepts aarch64 hosts, and the `Containerfile` builds for amd64 and arm64
  (`podman build --arch arm64`) with the pinned uv of each architecture, checked against a
  SHA-256, instead of the newest from astral.sh. On GitHub's arm64 runners
  (`ubuntu-24.04-arm`) the full suite, with the three-stage bootstrap, and the 12 arm64
  portability images pass on Python 3.12, 3.13 and 3.14, and release CI builds, reproduces
  and compares the aarch64 executables; this is the first release that has them.
- **`install.sh`, an installer modelled on uv's** (Phase 9, platforms step 1). The release
  workflow builds it from `tools/release/install.sh.in` with `tools/release/make-install.sh`,
  which writes in the release's version and the SHA-256 and glibc of every executable, then
  attests it, lists it in `SHA256SUMS` and attaches it: `curl -LsSf
  https://github.com/devitops-com/fsbpyr/releases/latest/download/install.sh | sh`. POSIX `sh`;
  it detects the target (musl by `ldd --version`), stops naming the release's targets when there
  is none for the host or the host's glibc is too old, downloads with `curl` (HTTPS only) or
  `wget`, the `.xz` when `xz` is there, checks the SHA-256 and `fsbpyr inspect --verify` before
  installing, and with `FSBPYR_VERIFY=1` also `gh attestation verify`. It installs into
  `$FSBPYR_INSTALL_DIR`, `$XDG_BIN_HOME`, `$XDG_DATA_HOME/../bin` or `~/.local/bin` with the man
  page and completions, and puts the directory on `PATH` through an `env` script sourced from
  the shell files (`--no-modify-path`, `FSBPYR_NO_MODIFY_PATH=1`; `$GITHUB_PATH` in GitHub
  Actions). It looks for the per-target asset names (`fsbpyr-x86_64-linux-gnu`, ...), so it
  installs from a release only once the release has them. The README's "Getting fsbpyr" leads
  with it.
- **Release assets are named with the target** (Phase 9, step 0). Every executable is
  published as `fsbpyr-x86_64-linux-gnu` (the newest Python) or
  `fsbpyr-3.12-x86_64-linux-gnu` (the others), each with its `.xz`, attestation and
  `THIRD_PARTY_LICENSES-<...>.txt`. The old names `fsbpyr`, `fsbpyr-3.12`, `fsbpyr-3.13` and
  `THIRD_PARTY_LICENSES[-3.X].txt` stay as copies of the x86-64 glibc assets, so URLs,
  scripts and executables already released keep working. An fsbpyr built from now on records
  the long name as `release_asset` and `fsbpyr self update` follows it; one that recorded a
  short name or nothing keeps following the short name. A build for another Python fetches
  `fsbpyr-3.X-x86_64-linux-gnu.xz` and keeps it as
  `downloads/fsbpyr-3.X-x86_64-linux-gnu-vX.Y.Z` in the cache (it was
  `downloads/fsbpyr-3.X-vX.Y.Z`; the old file stays until deleted by hand).
- **Releases carry aarch64 assets, and CI builds and tests every target** (Phase 9, platforms
  step 2). `release.yml` builds, smoke-tests, reproduces and compares each (target, Python)
  entry, `x86_64-linux-gnu` on `ubuntu-24.04` and `aarch64-linux-gnu` on `ubuntu-24.04-arm`,
  in the same Debian trixie image (its multi-arch digest). The release adds
  `fsbpyr-aarch64-linux-gnu`, `fsbpyr-3.12-aarch64-linux-gnu` and `fsbpyr-3.13-aarch64-linux-gnu`
  with their `.xz` (compressed with `xz --arm64`, the x86-64 ones with `xz --x86`), attestations
  and licence notices, and `install.sh` lists both targets. The short names stay copies of the
  x86-64 glibc builds only. `full-suite.yml` runs the suite and the portability matrix per
  (target, Python) on the matching runner, on every trigger; the portability matrix on aarch64
  runs the 12 images that have an arm64 variant (`archlinux:latest` has none). `tools/release/
  uv.version` holds a checksum per architecture and `tools/release/install-uv.sh` installs the
  uv of the machine's. A release still needs every job of both workflows to have passed. The
  workflows passed on GitHub's arm64 runners (the full suite, and release CI on PR #21).
- **Format version 7: the payload starts at a multiple of 64 KiB, not 4 KiB** (Phase 9,
  step 0). One executable format is meant for kernels with 4K, 16K and 64K pages (aarch64
  Linux, macOS arm64): the payload's file offset is a multiple of each of those page sizes.
  It has run on 4K-page kernels only (x86-64, and aarch64 on GitHub's arm64 runners); no
  16K or 64K kernel has been tried. An executable is up to 64 KiB larger (the zero padding
  after the stub); `fsbpyr inspect` names it "zero padding to the 64 KiB boundary". The
  launcher accepts only version 7
  and refuses an offset that is not a multiple of 64 KiB; `inspect` still reads versions 1 to
  6. Each executable carries its own launcher, so executables built before this change still
  run; they are not rebuilt.
- **`fsbpyr build` chooses the Python of the app and hands the build to the fsbpyr of that
  Python** (Phase 9, step 4). The version comes from, strongest first, the new `--python X.Y`
  (or `python = "X.Y"` in `[tool.fsbpyr]`), a `.python-version` pin that names a supported
  version inside `requires-python`, then `requires-python`: the lowest of 3.12 to 3.14 that
  satisfies a lower bound (`>=3.10` gives 3.12), the highest below an upper-only bound
  (`<3.14` gives 3.13), a pinned minor as pinned. An unsupported version, or one outside
  `requires-python`, is an error naming the supported ones (a pin fsbpyr cannot build for is a
  warning). When the chosen version is not the running fsbpyr's, a released fsbpyr downloads
  the matching variant of its own release (`fsbpyr-3.X.xz`, unpacked in-process and verified
  as `fsbpyr self update` does), keeps it in `downloads/` of its cache and runs it with the
  same arguments, so cached variants build offline; a developer build stops and names `make
  build PYTHON=3.X`. A project without `requires-python` builds with the running fsbpyr's
  Python. A project that allows 3.12 and neither pins nor asks for another version
  (`requires-python = ">=3.12"`) is now built with 3.12, where the 3.14 fsbpyr built it with
  3.14 before: pass `--python 3.14` to keep that. The warning that `.python-version` pins
  another Python than the build's is gone, as the pin now decides.
- **fsbpyr builds on Python 3.12 and 3.13 as well as 3.14** (Phase 9, step 2, D13: fsbpyr
  requires Python 3.12 to 3.14 from now on). The CLI no longer stops on 3.12 and 3.13: each
  fsbpyr builds with, and its apps carry, the runtime of the Python it runs on (3.12.15,
  3.13.16 or 3.14.8). `make test`, `make test-ci`, `make check` and `make build` take
  `PYTHON=3.12` (or 3.13, 3.14) on the command line and run in the locked environment
  `.venv-3.12`; `make build PYTHON=3.12` writes `dist/fsbpyr-3.12`, and `tools/bootstrap.sh
  --python 3.12` (`make bootstrap PYTHON=3.12`) runs the three stages of that variant into
  `dist/stage1-3.12` to `dist/stage3-3.12`, each verified and stage 2 identical to stage 3,
  without installing it. Without a version nothing changes. Found by running the apps on 3.12: its frozen standard library modules had no
  `__file__` and `encodings.__path__` was empty (CPython 3.12 does not set `sys._stdlib_dir`
  from `PyConfig.stdlib_dir`), so `encodings.idna` and `inspect.getsource(os)` failed; the boot
  module now sets it. Uncaught exceptions print their source lines on 3.12 (CPython 3.12's C
  printer reads the files from disk), except a Ctrl-C traceback, which keeps the C printer so
  that the process still ends by SIGINT. Nothing changes on 3.14.
- **The multiprocessing child recognition knows each version's command lines** (Phase 9,
  step 2, D22): spawn and the resource tracker read the same on 3.12 to 3.14; the forkserver
  command line of 3.12 has no `sys_argv` and no `authkey_r`, 3.13's adds `sys_argv`, 3.14's
  adds `authkey_r`, and each runtime accepts only its own. The tests take the forkserver and
  resource tracker command lines from the running Python's own code. Nothing changes on 3.14.
- **`fsbpyr runtime build --python 3.12` and `--python 3.13` build runtimes for those
  versions** (Phase 9, step 3): CPython 3.12.15 and 3.13.16 from python-build-standalone
  20261009, pinned by SHA-256, linked against the same glibc 2.28 sysroot. The launcher also
  links `build/lib/libzstd.a` when no built-in module does (3.12 and 3.13 have no `_zstd`);
  3.14's link and runtime id are unchanged. `fsbpyr runtime list` shows each. Building apps
  for these versions follows with the port of fsbpyr itself.
- **Each release carries one fsbpyr per Python version, and `fsbpyr self update` updates a
  variant to the same variant** (Phase 9, step 3). `release.yml` builds, reproduces and
  compares, and `full-suite.yml` tests, for Python 3.12, 3.13 and 3.14 (each on its own
  environment `.venv-3.X`, with that variant's three-stage bootstrap). A release publishes
  `fsbpyr` and `fsbpyr.xz` (the newest version's, 3.14 now: the same names as before) and
  `fsbpyr-3.12`, `fsbpyr-3.13` with their `.xz`, each with its own
  `THIRD_PARTY_LICENSES[-3.X].txt`, all attested and listed in `SHA256SUMS`. The executable
  of fsbpyr itself records the asset it is released as (`release_asset` in the build
  information; `fsbpyr inspect --json` shows it under `build_info`), and self update downloads
  that asset's `.xz` of the latest release, so `fsbpyr-3.12` stays on 3.12. An executable
  released before the record updates to `fsbpyr.xz`. The README's "Getting fsbpyr" and
  "Verifying a release" cover the variants. Apps and other projects' executables record
  nothing new.
- **Natives no extension module reaches, ELF executables and run-time library paths are
  checked at build time.** A shared object that no extension links to (a ctypes or cffi load,
  a library in an fs-package) used to be packaged unchecked. Now one that needs a library
  nothing provides gives a warning, and its system needs count in the target's needs
  (`fsbpyr inspect`). An ELF executable kept as data (owner-executable, ET_EXEC or a PIE)
  gets the same check and a hint to add its package to fs-packages if the package runs it
  from a path built from `__file__` (paths from `importlib.resources` need none). RPATH and
  RUNPATH entries not based on `$ORIGIN` warn: ld.so searches them on the target, and relative
  ones relative to the working directory (nothing is patched). With `--hints`, literal names
  given to `ctypes.CDLL`, `cdll.LoadLibrary`, cffi's `dlopen` and `ctypes.util.find_library`
  (`find_library("magic")` gives `libmagic`) are listed in `fsbpyr inspect` under "may load
  from the system (heuristic)" and in the build information (`system.may_load`), with no
  warning. The build information gains the key only when something was found.
- **A corpus of real applications is built and compared with their venvs.** `tests/corpus`
  holds 11 small projects, one per risk class (black, pytest with a pluggy plugin, alembic,
  pandas, Pillow, lxml, scipy, Flask, setuptools' vendored packages, gnureadline, psycopg), and
  `tests/fixtures/projects/plugins` a host with two plugin distributions. `full-suite.yml` builds
  each and runs its commands in a venv and in the executable; the log ends with the false-success
  count and the first-build success rate. The corpus found one failure, listed in
  `docs/compatibility.md`: black's mypyc-compiled package cannot import its plain modules.
- **CI runs the whole test suite on every push to main, nightly and weekly, and a release
  needs it.** The new `full-suite.yml` workflow runs everything but the portability tests as an
  unprivileged user in a privileged Debian trixie container with rootless podman, so the
  root-only, group and user-namespace tests run, with `FSBPYR_TESTS_REQUIRE_ALL=full` (no skip
  is expected). The fuzzer runs for 600 s when the launcher or the fuzz harness changed since
  the last tag and on the weekly run (its corpus is kept in the Actions cache,
  `FSBPYR_FUZZ_CORPUS`), and the launcher programs the tests build report llvm-cov coverage
  (informational). `make release` refuses a commit without a green `release.yml` and
  `full-suite.yml` run on main, waiting for one in progress; the publish job checks the same for
  the release commit's parent and adds the suite run's URL to the release notes.
- **`FSBPYR_LLVM23=1 bash tools/bootstrap.sh` links the runtime with clang and lld 23.** Python
  build standalone releases from 20261013 on are LLVM 23 bitcode, which Debian trixie's clang 22
  cannot link. With the switch set, bootstrap downloads LLVM's release tarball named in
  `tools/release/llvm23` (version and sha256) into `~/.cache/fsbpyr/toolchains` once, fails if
  its sha256 differs, unpacks only clang, lld and clang's resource directory, and passes that
  clang to `fsbpyr runtime build --clang`. `bash tools/bootstrap.sh --llvm23` does only the
  fetch and prints the clang. Without the switch nothing changes: Debian's clang 22 links, and
  the pin stays on PBS 20261009. The release workflow and the Containerfile do not turn it on
  yet (SEC-4; the spike is in docs/spikes.md).
- **`fsbpyr build --smoke-args "ARGS"` (`smoke-args = [...]`) runs the app once.** After the
  smoke test, the new executable runs with these arguments, for example `--version`, and the
  build fails unless it exits 0, with the arguments and the app's stderr in the error. The
  output stays as it was. Off unless set: only you know which arguments are safe.
- **`fsbpyr inspect --licenses` prints the third-party licence notices of an executable.**
  Every runtime now carries the licence texts of what its launcher links, as resources under
  `_fsbpyr_licenses/`: the texts PBS ships for the built-in extensions (OpenSSL, libffi,
  libedit, ncurses, expat, mpdecimal, SQLite, zlib, liblzma, bzip2, libuuid and CPython's),
  zstd's and the HACL\* hash code's, which PBS does not ship, and fsbpyr's LICENSE
  (runtime layout 8). `--licenses` prints them, then each distribution's notices, best effort:
  the dist-info `licenses/` directory, METADATA's `License-File` names, licence files in the
  dist-info root, and `LICENSE*`, `COPYING*`, `NOTICE*` and `ThirdPartyNotices*` files in its
  packages. The release attaches the output for fsbpyr's executable as
  `THIRD_PARTY_LICENSES.txt` (also in `SHA256SUMS` and attested). The README says what to ship
  with an app.
- **The portability matrix runs in CI and with `make test-portability`.** A `portability` job
  in `full-suite.yml` (after the `suite` job, on the runner host, no container) builds the four
  test apps with the suite's executable (`tools/portability-apps.sh`) and runs
  `tests/e2e/test_portability.py`: 13 distributions from glibc 2.28 to 2.44 and the objdump
  check of the glibc floor, with `FSBPYR_TESTS_REQUIRE_ALL=portability` (no skip is expected).
  The job's result is part of the run that `make release` and the publish job require. The
  images now run with `--network=none`. `FSBPYR_PORTABILITY_APPS` makes the test use prebuilt
  apps and no runtime.
- **`fsbpyr inspect` shows the build options and where the bytes go.** The text output has
  `extras`, `fs pkgs`, `allow libs` and `sources` lines from the build information's
  `settings`, and a "stored bytes" table: the stub, the padding to the page boundary, the
  payload header, the index and string tables, the blob alignment padding, the standard library
  and boot modules, and per distribution its code, sources, resources and natives. The rows add
  up to the file's size less the trailer. `--json` has `settings` and `sizes`. docs/performance.md
  has the table for `hello`, `heavy` and fsbpyr.
- **`make test-container` runs the whole test suite but the portability tests in a container.**
  The Containerfile's new `test` stage (its first stage is now named `build`, which `make
  bootstrap` selects) adds what the suite needs beyond a build: the sanitizer runtimes,
  llvm-objdump, gcc, strace and groff, a user `tester` with one supplementary group, a
  directory of root's in /tmp and one of another user under /var/lib, all from
  `tools/test-setup.sh`. The suite runs as `tester` with `FSBPYR_TESTS_REQUIRE_ALL=container`,
  so a skip outside that list fails the run. The end-to-end runtime's pinned downloads (CPython
  and the sysroot's packages) are kept in a volume (`FSBPYR_TEST_DOWNLOADS`) and seed each
  later session, and the terminal summary says whether each was seeded or fetched.
  CONTRIBUTING.md describes the tiers and the expected skips.
- **docs/compatibility.md** lists how an executable differs from a venv (symptom, cause and
  remedy), what the target must provide beyond glibc, the distributions and checks the tests
  cover, and troubleshooting. The README links it from "Supported targets", documents
  `FSBPYR_SELFTEST` and `FSBPYR_IMPORTTIME`, and gains a note on offline and air-gapped builds.
  Tests keep the tables equal to the portability images and the compatibility matrix, and
  every environment variable the sources read in the README table.
- **`fsbpyr self update`** replaces the running executable with the latest GitHub release when
  that is newer. It downloads `fsbpyr.xz`, unpacks it with the standard library's lzma,
  verifies it in-process as `inspect --verify` does before anything runs it, and renames it
  over the executable's real path, keeping its mode. The man page and completions found under
  the executable's prefix are written again by the new executable; ones elsewhere get a hint.
  A developer build (`untagged-<commit>`) refuses and prints the `curl` command for the latest
  release, and an unwritable directory is an error saying how to update instead.
- **A run-time note for files read next to `__file__`.** When an app ends on an `OSError` for a
  path inside the executable and the first path component is a package in it, the traceback is
  followed by "fsbpyr: <path> is inside the executable; <pkg> reads files next to its
  `__file__`: rebuild with --fs-package <pkg>". Only an uncaught error prints it, and not when
  the app replaces `sys.excepthook`. An extension that fails to load with a missing
  `GLIBC`, `GLIBCXX_` or `CXXABI_` version or "cannot open shared object file" now also
  reports the glibc the build required and the one in this system.
- **`--hints` finds more.** It now also reports `X.__file__` and `__path__[0]`, subprocess and
  `os.exec*` calls, cffi `dlopen`, `configparser` `read`, `np.load` and `jinja2.PackageLoader`
  that use such a path.
- **`fsbpyr build --strict` (`strict = true`) fails on warnings.** Once all warnings are
  collected, and before anything is written, the build exits 1 with them in the message, so
  a file at `-o` stays as it was. An unlocked build fails too. The uv that differs from
  fsbpyr's, git errors and the ctypes guess do not count. Requirements files then get uv's
  `--require-hashes`. Without `--strict`, requirements lines without `==` now get a warning.
  The README shows `fsbpyr inspect --json app | jq -r .glibc` as a glibc gate.
- **The release workflow rebuilds every build independently.** A `reproduce` job runs beside
  `build` on a fresh runner (the same image, uv and, on tags, Debian snapshot), builds from a
  second checkout at another path as another user, with other cache directories and umask 077,
  and a `compare` job checks its executable against `build`'s byte for byte; on a difference it
  prints both digests and a diff of `inspect --json`. `publish` now needs `compare`. The
  build test also builds a copy of a project at another path with another umask, other caches
  and one core.
- **`fsbpyr build` shows what it is doing.** Each uv step prints a line before it runs ("uv:
  installing the locked distributions", "uv: building <project>'s wheel"), and the smoke test
  prints "smoke-testing <path>". A failed uv step names the step instead of a truncated command.
  `-v/--verbose` also prints each uv command on stderr and lets uv's error output through
  rather than capturing it.
- **`inspect --verify` prints the file's plain SHA-256** (`sha256` in the text output and in
  `--json`), the hash GitHub attestations and `sha256sum` use; plain `inspect` does not compute
  it. The README shows how to attest an app built with fsbpyr and verify it with `gh
  attestation verify`, with a manually dispatched example workflow, `attest-example.yml`.
- **`fsbpyr inspect` lists what the target must provide beyond glibc.** The build records, for
  every system library the launcher and the native files an extension module loads need, the
  files that need it and the newest symbol version per namespace (`GLIBCXX`, `CXXABI`, `GCC`,
  `ZLIB`, ...) in the build information (`system`). `inspect` shows them, a `needs` line and
  the entry point; `--json` has them as `system_libraries` and `entry`, and `fsbpyr build`
  prints `target needs: ...`. A build now warns, naming the file and the version, when a native
  file needs a `GLIBCXX`, `CXXABI`, `GCC` or `ZLIB` version above manylinux_2_28's, or
  `GLIBC_PRIVATE`, and when an `--allow-system-lib` entry is needed by no native file; a
  version table that cannot be read gives a warning and does not fail the build.
- **A test that the built stub keeps its hardening.** It runs `readelf` and `objdump` on the
  stub and asserts PIE, BIND_NOW, GNU_RELRO, a non-executable GNU_STACK and the stack
  protector (`__stack_chk_fail`); dropping `-Wl,-z,now` from the link flags now fails it. The
  end-to-end tests that run built executables also time out after 60 seconds instead of
  hanging a CI job.
- **`build_info` is a versioned manifest traceable to its inputs (`schema` 1).** It records
  the lock (`lock`: kind, path relative to the project, SHA-256, whether it carries hashes),
  every distribution as `{name, version, source, artifact_sha256}`, the runtime's inputs
  (`runtime_inputs`: python-build-standalone release, name and SHA-256, the sysroot, the
  runtime layout), the versions of the libraries the runtime carries (`runtime_components`:
  OpenSSL, SQLite, zlib, expat, libmpdec, read once at `fsbpyr runtime build`), the SHA-256 of
  the bytes before the payload (`stub_sha256`, the same in every build by the same fsbpyr
  release) and the effective settings (`settings`). `source` is registry, git, url, path,
  local (the project and workspace members, which have no hash) or built-from-sdist: with a
  uv.lock or pylock.toml, a distribution whose installed wheel shares no tag with the wheels
  the lock lists was built on the build host, pure-Python ones included; it carries the
  sdist's hash and the build backend (`backend`). `fsbpyr inspect` shows the runtime's library
  versions and the lock, `--json` has `"schema": 1`, and `inspect --files` lists every
  packaged file with its kind, size and SHA-256 (`files` in `--json`). docs/format.md
  describes the schema. Runtimes built by an earlier fsbpyr are built again (runtime layout 8).
- **A build prints the dists it built from sdists.** "built on this host from sdists, not
  reproducible elsewhere: X, Y"; it is information, not a warning. `uv` builds them and the
  project's wheel with `SOURCE_DATE_EPOCH` set to the project's last commit time (1980-01-01
  outside git), unless the environment already sets it.
- **A security policy, `SECURITY.md`.** Vulnerabilities are reported privately, as GitHub
  security advisories, and acknowledged within 7 days; only the latest release is supported.
  Every executable carries its own CPython and the libraries linked into it (OpenSSL, SQLite,
  expat, zlib, libffi, liblzma and others), so a security fix in them reaches an app only when
  the app is rebuilt. When python-build-standalone fixes such an issue, an fsbpyr patch
  release follows, with targets of 7 days for high or critical issues and 30 days otherwise;
  its `### Security` entry here names the components and the old and new runtime ids, and
  says to rebuild your executables.
- **Releases carry `SHA256SUMS`**, the SHA-256 of `fsbpyr` and `fsbpyr.xz` as `sha256sum`
  prints them, the digests the release's attestation signs: `sha256sum -c --ignore-missing
  SHA256SUMS` checks a download without gh.
- **A build warns about what the venv would have and the executable lacks.** Files a wheel
  installs outside site-packages (`share/`, `include/`, other data_files) are counted in one
  warning per distribution, "N files under share/ are not packaged; sys.prefix lookups will
  not find them" (scripts stay silent), and a `.pyc` outside `__pycache__` is reported as
  bytecode without source. What gets packaged does not change. The warning about native
  libraries that only the package's Python code loads now names an `fs-packages` value the
  config accepts (the regular package holding them), or says that none covers them; it named
  the distribution, which `foo-libs` is not a valid value for.
- **`spikes/bench.py --cold` and `--cache DIR`** measure starts with a cold page cache, with no
  root: before each sample the executable's pages, and with `--cache` those of the cache's
  files, are dropped. `docs/performance.md` has new "Cold start" and "Integrity check" tables
  (28 MB to 1.07 GB, one core and without SHA instructions), each with a "Reproduce:" command
  that runs; the README's "Integrity" figures are the new ones.
- **`fsbpyr inspect` shows the toolchain that linked the launcher**, the stub's `.comment`
  lines ("toolchain", `.toolchain` in `--json`), such as `Debian clang version 22.1.8
  (1~deb13u4)`. `fsbpyr runtime build` also records the clang and lld versions in the runtime's
  `runtime.json`, for information; they are not part of the runtime id.

### Changed
- **The tests keep their temporary files under `~/tmp`, not `/tmp`.** `make test`, `make
  test-ci` and `make test-portability` set `TMPDIR` to `TESTTMP` (default `~/tmp`, created if
  missing): `/tmp` is often a tmpfs mounted nosuid, where the set-gid start test skipped. The
  long-symlink test of the cache root uses a 999-byte target, which XFS takes.
- **The full suite runs weekly, no longer nightly.** `main` changes for a release only, so the
  nightly run tested the same commit again; it still runs on every push to main, weekly (with
  the longer fuzzing) and on request. `make test-container` takes `PYTHON=3.X` and runs in
  `JOBS` processes like `make test`.
- **`make test` runs the suite in 8 processes** (pytest-xdist, `JOBS=` to change it, each test
  file in one process): about 2.5 minutes instead of 10.5 on a 24-core host. Before the workers
  start, the runtime's pinned downloads are fetched and its release and sanitized runtimes
  built, once, into `FSBPYR_TEST_DOWNLOADS` (`make test*` default to `~/tmp/fsbpyr-test-downloads`);
  every session copies the runtime from there instead of building its own, and a kept runtime
  is rebuilt only when its id changes.
- **A test run removes the builds of local projects it made from uv's cache.** The tests build
  their fixture projects (and fsbpyr) from new temporary copies every time, and uv kept every
  build and its unpacked wheel: thousands of entries a run. At the end of a run, the entries
  uv made for local paths, editable installs and wheel files since it started go, with the
  wheels they link to; what uv downloaded from an index stays.
- **One table holds the platforms fsbpyr knows** (Phase 9, step 0): `src/fsbpyr/targets.py`
  has x86_64-linux-gnu, aarch64-linux-gnu, x86_64-linux-musl, aarch64-linux-musl and
  aarch64-macos, each with its python-build-standalone triple and flavour, ELF machine,
  dynamic loader, C library, xz filter for native files, sysroot directory and uv's
  `--python-platform`. The host's target comes from `sys.platform`, the machine and the C
  library; the ELF check, the system libraries, the payload's xz filter, the sysroot flags,
  the cache collector and staging take their values from it. A recognized host without pins
  (anything but x86-64 glibc) stops with "fsbpyr does not support the ... target yet" before
  any download; an unknown host keeps "unsupported host". Nothing changes on x86-64 glibc.
- **fsbpyr's code imports and runs on Python 3.12 and 3.13** (Phase 9, the base of the port):
  `requires-python` is now `>=3.12,<3.15`, with `backports.zstd` (the official backport of
  3.14's `compression.zstd`) as a dependency below 3.14. The CLI still refuses 3.12 and 3.13:
  building for them waits for the rest of Phase 9. Nothing changes on 3.14.
- **The README's "Versions" states what is stable before 1.0**: the `inspect --json` schema,
  the release asset names and URLs that self-update and installers use, and the trailer and
  digest layout that `inspect --verify` reads. Breaking changes are marked `**Breaking:**`
  in this file from now on.
- **LICENSE gains a runtime exception** for the launcher and the boot modules, so an app built
  with fsbpyr need not reproduce fsbpyr's MIT notice for them. The package's licence
  expression is now `LicenseRef-fsbpyr-MIT-with-runtime-exception`.
- **A build fails sooner and completely.** Two or more static `[project.scripts]` without
  `--entry`, and an explicit `-o` that is a directory, are reported before uv stages anything. An
  unknown `--extra` is reported before staging when the pyproject lists the extras statically,
  else after, against the installed project's `Provides-Extra`. All missing native libraries
  are reported in one error, with an `allow-system-libs = [...]` line to paste. An unexpected
  exception prints its traceback and a line saying where to report it, and exits 1. The
  `--extra` help says that requirements and pylock files must already contain the extra's
  dependencies.
- **`build_info["lock"]` and `["dists"]` changed shape** (see the manifest above): `lock` was
  the lock's kind as a string, now an object with `kind`; `dists` was a list of
  `name==version` strings, now a list of objects.
- **Executable data files stay executable.** A resource that was owner-executable in the wheel
  (a helper under `bin/`, say) is now packaged with mode 0755 and extracted as 0500 instead of
  0400, so `files(pkg) / "bin/tool"` runs in the executable as it does in the venv. Extracted
  files are still never writable. Rebuilt apps get new generation and resource ids once.
- **The release toolchain is pinned.** Release CI uses `debian:trixie` by digest, the uv named
  by `tools/release/uv.version` (version and the SHA-256 of its release tarball, checked on
  download) instead of the newest one, and, on tag runs, a snapshot of the Debian archive
  (`tools/release/apt-snapshot`) for clang-22, lld-22 and the rest, so a rebuild finds the
  clang that linked the launcher. The Containerfile pins the same image digest. The release log
  lists the image, snapshot, uv and package versions. With `FSBPYR_UV_VERSION` set,
  `tools/bootstrap.sh` accepts only that uv; without it any uv still works. The daily
  upstream-watch workflow also opens a `toolchain-update` issue when the uv, the image digest
  or the snapshot (older than 90 days) needs a new pin.
- **The man page documents configuration, exit status, environment and files.** New sections
  CONFIGURATION (every `[tool.fsbpyr]` key, from the same table that reads them and names them
  in the unknown-key error), EXIT STATUS (0, 1, 2, 130, and 70 for a built app that fails its
  integrity check), ENVIRONMENT (`FSBPYR_CACHE_DIR`, `FSBPYR_GC`, `FSBPYR_TRACE`,
  `FSBPYR_IMPORTTIME`, `FSBPYR_SELFTEST`, `XDG_CACHE_HOME`) and FILES. `fsbpyr runtime list` now
  shows the runtime embedded in the running executable first, and the "no runtime" error also
  points to the release binary.
- **fsbpyr runs on CPython 3.14 only, and stops at once on any other Python.** Its
  `requires-python` is now `>=3.14,<3.15`, so pip refuses to install it on 3.15.
  `uv pip install` and `uv tool install` ignore that upper bound, so `fsbpyr build` and
  `fsbpyr runtime build` now check the running Python before anything else and exit 1 with
  "fsbpyr runs on CPython 3.14 only", where a build failed only after staging the project. The
  README's new "Supported targets" section says what executables run on: CPython 3.14.8, Linux
  x86-64 with glibc, and the GIL build only.
- **The runtime is built from python-build-standalone 20261009** instead of 20261003. CPython
  stays 3.14.8; libffi goes from 3.4.8 to 3.8.0 and expat from 2.8.5 to 2.9.0 (the release
  notes name no CVE). Executables use a new runtime; fsbpyr run from a checkout or a wheel
  needs `fsbpyr runtime build` once. 20261009 is the newest release whose objects clang 22
  links: later ones are LLVM 23 bitcode. A daily workflow now opens an issue when a newer
  release is out.
- **The README starts with a quickstart and what a build needs.** Right after the introduction
  it downloads fsbpyr, builds a project, runs the executable and inspects it, then lists what
  the build host and the project need and the order in which a build picks its lock source.
  "Getting fsbpyr", with building from source and verifying a release, is now the last
  section. A test runs the quickstart as on a new account: an empty home, whose `~/.local/bin`
  is not on `PATH`.
- **A version whose release fails is burned.** When the release workflow failed for a reason
  that needs a code fix, `make release` said to delete the pushed tag and push it again at the
  fix; it now says to fix main and release the next patch version, with the burned version's
  section of this changelog merged back into [Unreleased], so that the next release's notes
  carry its changes too, and the burned version named in the note at the top (`gh run rerun`
  still retries a transient failure). A pushed tag never moves, and every release from 0.19.0
  on stays published as it is.
- **`make bootstrap` and `make build` no longer install or update tools on your machine.**
  They ran `sudo apt-get install` (clang and lld 22, git, make, curl), installed uv from
  astral.sh or ran `uv self update`, and installed the Python of `.python-version` with
  `uv python install`, which also re-pointed uv's minor-version links. Now they name a missing
  uv, Python or clang/lld 22 and stop, and uv may not download a Python on its own
  (`UV_PYTHON_DOWNLOADS=never`). `make build` without an installed fsbpyr says to run
  `make bootstrap` instead of running it. The podman build installs its tools inside the
  container, as before, and the README's release check no longer runs `uv self update`.

### Fixed
- **The build's compile pool forks only when the process runs one thread.** It counted the
  threads `threading` knows; one started by C code (or pytest-xdist's) did not count, and the
  pool forked a multi-threaded process, which may deadlock. It now counts `/proc/self/task`, as
  CPython's own warning does, and starts a fork server otherwise.
- **A bundled library whose version ends in a letter is a native library.** A file named like
  `libcrypto-02bae65c.so.1.1.1k` (OpenSSL 1.1) was kept as data, so the libraries that need it
  failed the build as "neither bundled nor standard system libraries"; psycopg-binary's
  aarch64 wheel bundles one. A version part after `.so` now starts with a digit and may go on
  with letters; the ELF check decides, as before.
- **Runtimes build and modules compile alike on Python 3.12 and 3.13.** `fsbpyr runtime build`
  unpacked the python-build-standalone archive with 3.14's `tarfile` mode `r:zst`; below 3.14
  it uses the one of `backports.zstd`. A module's `co_filename` is now a copy of its name:
  3.12's `pathlib` interns the parts of a path, so a module compiled in the build's process
  had its filename interned in the bytecode and one compiled on a worker did not. Nothing
  changes on 3.14.
- **`import distutils` works in an executable that packages setuptools, also before setuptools
  is imported.** Python 3.12+ has no stdlib `distutils`; a venv gets it from setuptools'
  `distutils-precedence.pth`, which the build treats as redundant. The import now resolves to
  `setuptools._distutils`, as setuptools' own hook does, and costs nothing unless it would
  have failed. The build warns when a packaged setuptools still contains `pkg_resources`,
  which finds no distributions in executables (use `importlib.metadata`).
- **A `sys.path` entry inside the executable now resolves its modules.** An app that
  runs `sys.path.insert(0, dirname(__file__) + "/_vendor")` found nothing in the executable
  although it works in the venv, and an app that packages setuptools could not
  `import setuptools`: it reaches `distutils` through `setuptools/_distutils` and `jaraco.text`
  through `setuptools/_vendor`. A path entry `<exe>/pkg/_vendor` now serves `pkg._vendor.x` as
  `x`, with the right `__file__`, source and resources. A namespace package that has a
  portion there and a packaged top-level portion (a vendored `jaraco` next to jaraco.classes)
  now merges them as it does in a venv.
- **`exit()`, `quit()` and `help` work in built apps.** Python's `site` module defines them,
  and built apps never import it, so each raised NameError there while working in the venv.
  The executable now defines them itself, as `site` does, also for the `-c` and `-m` children
  that `--interpreter-reexec` runs; `help` imports pydoc only when it is called.
- **The README's install lines create `~/.local/bin` first.** `curl -fLo ~/.local/bin/fsbpyr`
  failed on an account without that directory. The quickstart also calls fsbpyr by its path,
  since a new `~/.local/bin` reaches `PATH` only with the next login.
- **The README's `[tool.fsbpyr]` sample is one a build accepts.** It set both
  `fast-compression` and `best-compression`, which a build rejects together; the second is now
  commented out, and `requirements` is marked as for pip projects, overriding `uv.lock`.
- **A build that fails its smoke test leaves `-o` as it was.** The new executable replaced
  the output before the smoke test ran, so `fsbpyr build ~/src/mytool -o ~/.local/bin/mytool`
  could swap a working tool for a broken one. It is now written beside the output as
  `.<name>.XXXX.tmp` and smoke-tested there; only one that passed replaces the output, and a
  failed or interrupted build removes it.
- **An entry naming a package without `__main__` fails the build.** `--entry pkg` runs
  `pkg.__main__`, as `python -m pkg` does, but the smoke test only imported `pkg`: the build
  succeeded and the executable failed at every start. The build now requires `pkg.__main__`
  among the app's modules, and the smoke test finds it without running it.
- **A failed build prints the warnings it collected before the error**, such as "unlocked
  build", ahead of `fsbpyr: error:`; they were dropped.
- **The README and `fsbpyr build --help` claim only what was measured.** The introduction
  now says that an executable also needs the system libraries its native dependencies link
  (often libstdc++ and libgcc_s: numpy and onnxruntime link both). `--fast-compression` makes
  an executable up to ~9% larger, not ~8%. `--best-compression`'s saving and the `--no-source`
  sizes, measured on an app outside the repository, are re-measured with 0.20.0 on the `heavy`
  test app, and `docs/performance.md` has the row. Every measured figure in the help and the
  README names its release, and the README links its row. The README lists all six built-in
  fs-packages, and the `--allow-system-lib` examples allow `libcuda.so.1`: their `libGL.so.1`
  is allowed anyway, so the option changed nothing.
- **A project's own C extension is rebuilt against the glibc 2.28 sysroot too.** When the
  project's extension, built on the build host, needed a newer glibc than 2.28, the build said
  "rebuilding ... against the glibc 2.28 sysroot" but installed the project's first wheel
  again: the executable kept the host's build and needed its glibc (2.34 for `pthread_create`
  on Debian trixie), with a warning. The project's wheel is now built again with the
  sysroot's flags, and that wheel is packaged.
- **The README's release check works again, and checks more.** It downloaded v0.18.2, whose
  release was withdrawn (HTTP 404); it now asks GitHub for the latest release's tag, checks
  the attestation before anything runs the downloaded file, and sets uv to the version the
  release records. `gh attestation verify` now requires the signing workflow, `release.yml`
  (`--signer-workflow`), and in the release check also the release's tag (`--source-ref`);
  "Getting fsbpyr" calls the check recommended instead of optional. The README now says what
  the self-rebuild proves: fsbpyr's code and dependencies, not the launcher, CPython and the
  standard library, which an executable copies from itself. A second rebuild, given too,
  checks those: it links the launcher from the tag's sources against the CPython the tag pins,
  python-build-standalone's build (CPython is not compiled again), and takes the standard
  library from the same archive. It no longer says that `make bootstrap` and `make build`
  cannot give a release's bytes: with the release's uv and clang they do. The Integrity section
  names the attestation as what shows a release's origin until signed releases, planned after
  the MVP.

## [0.20.0] - 2026-10-18

### Changed
- **Plain `fsbpyr` prints its help and exits 0.** With no command it printed the same help to
  stderr and exited 2, click's status for a usage error; it now prints it to stdout, as
  `fsbpyr --help` does, and still does nothing else.

### Fixed
- **`docs/format.md` gives the format version as 6**, the version executables have carried
  since 0.15.0; its trailer and header tables still said 5.

## [0.19.0] - 2026-10-14

### Added
- **Executables record the uv that built them.** uv builds the project's wheel (its built-in
  backend stamps uv's version into the wheel's metadata) and installs every distribution, so
  the uv version is part of what makes the bytes. The build records it in build_info, and
  `fsbpyr inspect` shows it below Python (`uv` in `--json`). An fsbpyr executable building
  with a uv other than the one that built it warns that the result can differ from one built
  with that uv, even from identical sources.
- **Releases also carry `fsbpyr.xz`**, the same executable compressed with xz (about 14 MB
  instead of 29 MB), attested like the executable itself.
- **`--best-compression` warns when it has nothing to compress.** It compresses native files
  only (extension modules and shared libraries), so a pure-Python app gains nothing from it;
  such a build now says so, and the option's help names what it compresses.
- **`fsbpyr inspect` shows how the content is compressed.** A `compressed` line below the
  size gives the zstd level and, if there are native files, theirs: `zstd 9, native files
  xz -6` after `--best-compression`. Both were already in `--json`'s build_info; executables
  older than 0.4.0 recorded neither and show `unknown`.

## [0.18.2] - 2026-10-14

### Fixed
- **The launcher links reproducibly, so executables do too.** The runtime build copied the
  launcher's sources into a randomly named temporary directory, and that name reached the
  launcher through LTO: every runtime build linked a different launcher, and the same commit
  built on two machines gave different executables. clang now runs inside that directory on
  relative paths, and the cache's own location is mapped out (`-ffile-prefix-map`), so the
  same inputs link the same bytes wherever the cache is. Runtimes are rebuilt once (layout 7).

## [0.18.1] - 2026-10-14

### Added
- **Releases on GitHub, with the executable attached.** A pushed `vX.Y.Z` tag makes GitHub
  Actions build fsbpyr with itself, smoke-test it, attest its build provenance and publish
  it as the release's `fsbpyr` asset, with this changelog's section as the notes
  (`gh attestation verify fsbpyr --repo devitops-com/fsbpyr` checks it). `make release`
  (`VERSION=X.Y.Z` to bump) promotes `[Unreleased]`, commits, tags and pushes.
- **A Makefile: `make bootstrap`, `make build`, `make install`.** `make bootstrap` takes a
  checkout to an installed fsbpyr: on Debian trixie it installs clang/lld 22 and the other
  OS packages with sudo and uv from astral.sh, then builds the runtime and fsbpyr in three
  stages (from source, then twice by the previous stage's executable; every stage must pass
  `inspect --verify` and stages 2 and 3 must be byte-identical); on any other Linux x86-64 the build runs in a Debian trixie podman container
  (`Containerfile`, IPv4-only). `make build` builds `dist/fsbpyr` with the installed fsbpyr
  (bootstrapping first if there is none), and `make install` installs it with its man page
  and bash, zsh and fish completions under `~/.local` (`PREFIX=` for elsewhere).
- **Python 3.14.8 pinned in `.python-version`**, the same CPython the runtime is built from.
- **`fsbpyr man` ends with examples.** The page gains an EXAMPLES section: a command line
  and what it does for every command (`build`, `inspect`, `runtime build`, `cache gc`,
  `completion`, `man`). A test runs each example through fsbpyr's real commands, so an
  example that names a removed option or command fails the suite. The section is set
  left-aligned and without hyphenation, so groff no longer splits `fsbpyr` ("fs-bpyr") in a
  command line or stretches the long ones.

### Fixed
- **`fsbpyr runtime build` retries a download that fails on the network.** The pinned
  CPython archive and the sysroot's packages are tried up to four times, after 2, 5 and
  10 s, when a TLS handshake times out or a connection is reset; an HTTP error such as a
  404 still fails at once.

## [0.18.0] - 2026-10-13

### Added
- **Shell completions and a man page, from the same source as `--help`.** `fsbpyr
  completion bash|zsh|fish` prints a script that asks fsbpyr for the words as you type, and
  `fsbpyr man` prints the roff man page. `--entry` and `--extra` complete from the project
  being built (its console scripts and its extras); a project that cannot be read
  completes to nothing, silently.
- **Every on/off option of `build` has a `--no-` form** (`--source`/`--no-source`,
  `--smoke`/`--no-smoke`, `--offline`/`--no-offline`, ...), so the command line can also
  switch off what `[tool.fsbpyr]` switches on.

### Changed
- **The command line runs on click 8.5.** Commands are listed alphabetically, options can
  no longer be abbreviated, usage errors print `Error: ...` (still exit 2), bare `fsbpyr`
  prints the help (exit 2), and a project path that is a file is a usage error. The cache
  is still collected only when `build`, `inspect` or `runtime build|list` run, never for
  help or a usage error. click adds 4-5 ms to fsbpyr's own startup (`--version` 66.7 ->
  70.6 ms, median of 31).

## [0.17.1] - 2026-10-13

### Fixed
- **Modules whose names start with a digit are packaged as modules.** mypyc puts the code
  its extensions share in a top-level extension named after a hash
  (charset-normalizer imports `81d243bd2c585b0f4821__mypyc`); it was packaged as a
  library, so `import charset_normalizer` failed and requests warned that it had no
  character detection. An extension's name may now start with a digit (a name with a
  hyphen, such as `libbar-2.so`, stays a library), and so may a `.py` module, so
  migrations such as Django's or alembic's `0001_initial.py` are modules too. An older
  `fsbpyr inspect` refuses an executable holding such a name; the launcher never checks
  names, and executables built before read as they did.

## [0.17.0] - 2026-10-13

### Added
- **Dependencies built from sdists above the glibc baseline are rebuilt against the
  glibc 2.28 sysroot.** A dependency without a wheel for the baseline is built from its
  sdist with the build host's compiler and glibc, which used to raise the executable's
  glibc requirement with only a warning (posix_ipc built on a glibc 2.34 host made the
  executable need glibc 2.34). Such a dependency is now built again with the host's gcc or
  clang against the sysroot the launcher is linked with (downloaded once), and uv's cached
  build of it is replaced, so the next build rebuilds nothing. If the rebuild fails (a C++
  extension does: the sysroot has no libstdc++) or the build is offline, the host's build
  is packaged with a warning naming the glibc it needs.

## [0.16.0] - 2026-10-13

### Added
- **Helper interpreters for the app's own children: `--interpreter-reexec`.** In an
  executable, `sys.executable` is the app, so code that starts `sys.executable -m MODULE`
  or `-c CODE` got the app. Built with `--interpreter-reexec` (or `interpreter-reexec =
  true`), such a child runs the module or the code as Python would, interpreter flags
  before it accepted and ignored, but only when its parent process runs the same
  executable: the same command line from a shell, a script or sudo still runs the app with
  these arguments. Off by default, since an app with options of its own named `-m` or `-c`
  would run Python instead.

## [0.15.3] - 2026-10-12

### Fixed
- **Bundled libraries without RUNPATH are found through their loaders' RPATHs.**
  gnureadline 8.3.3 bundles libncurses and libtinfo without an RPATH of their own; the
  dynamic loader finds libtinfo through the RPATH of the extension that loaded libncurses,
  but the build searched only each object's own path and asked for `--allow-system-lib
  libtinfo-3a2cb85b.so.6.1`. The closure search now passes the loaders' RPATH directories
  down each chain as `ld.so` does: an object without RUNPATH searches them after its own
  RPATH; one with RUNPATH does not, but passes them on.

## [0.15.2] - 2026-10-12

### Fixed
- **A dependency's module that the runtime shadows no longer stops the build.**
  gnureadline ships `readline.py` for Pythons without a readline module; the runtime has
  readline built in, which is always found first, so the build failed with "application
  modules shadow the runtime: ['readline']". Such a module of a dependency is now packaged
  as a plain file, with a warning; one of the project's own still fails the build.

## [0.15.1] - 2026-10-10

### Fixed
- **Building with large sparse files is fast again.** 0.15.0 sent every 1 MiB piece of a
  file to a thread, holes included, so the raw build of a sparse 1 TiB file took 65.7 s
  instead of 0.14.0's 26.1 s (peak 318 MiB instead of 91). Holes are now found on the
  calling thread and only pieces with data are read on the threads: 14.2 s, 106 MiB.

## [0.15.0] - 2026-10-10

### Changed
- **Builds, decoding and extraction use up to 8 cores.** Builds compile, canonicalize and
  compress modules on up to 8 worker processes, and the output is the same bytes as on one
  core. Large zstd blobs are stored in pieces of 8 MiB (format v6, codec 5), compressed and
  decoded on up to 8 threads by the builder, the launcher and the Python reader. The
  launcher checks every file of a cache sync first, then writes on up to 8 threads. Measured
  on a torch app: the build takes 6.3-8.3 s instead of 22.75 s, `libtorch_cpu.so` decodes in
  about 100 ms instead of 566, and torch's 12,139 small files extract in 140 ms instead of
  302. Build processes stay at 595 MiB PSS at most. Executables use a new runtime.
- **The runtime build shows every warning** of the link and the stdlib compile, and fails
  on a stack frame over the launcher's limit.

## [0.14.0] - 2026-10-10

### Security
- **No other user can redirect the cache.** What the cache holds is loaded as code, so the
  launcher now walks the cache root's path from `/` a component at a time: every
  directory on it, the root's own included, must be owned by root or the user and writable
  by no other user (not by others, nor by a group other than the user's primary one)
  unless sticky, as `/tmp` is; symlinks must be root's or the user's and are followed one
  at a time (40 at most), their targets checked too. The way is checked again, whole, once
  the root exists. A cache that fails is refused, and the error names the directory at
  fault ("other users may write to it", "owned by another user"). In a user namespace, a
  directory owned outside it counts as root's only where nobody inside maps to the
  overflow uid, and only above any directory others may add to. The cache collector and
  fsbpyr's own cache apply the same rule; `fsbpyr cache gc` adopts the caches that apps of
  earlier versions made.

## [0.13.0] - 2026-10-10

### Added
- **Caches clean themselves.** App caches and fsbpyr's own cache are collected without
  the user: what no process has used for 7 days is removed, and when the file system has
  less than 10% free, everything not in use goes. A collection is due a day after the last
  (an hour when short of space); each start checks that with one `stat` and, when one is
  due, starts a detached process at idle priority, so the start is not slowed. Every
  process holds the trees it uses for its whole life, and stale temporary files go after an
  hour.
- **Only caches marked as fsbpyr's are collected.** A cache root gets the marker
  `.fsbpyr-cache` only when it is new or holds nothing but a cache of fsbpyr's; a
  directory of the user's named by `FSBPYR_CACHE_DIR` is never marked (create the marker
  yourself to have it collected). Only what fsbpyr made there is ever removed.
- **`fsbpyr cache gc` and `FSBPYR_GC`.** `fsbpyr cache gc` collects fsbpyr's cache and
  every app cache now; `FSBPYR_GC=1 app` collects that app's cache in the foreground
  instead of running the app, and `FSBPYR_GC=0` never collects in the background. fsbpyr
  checks its own cache (runtimes, Python distributions, sysroots, downloads) once a day,
  keeping what it builds with.

## [0.12.0] - 2026-10-09

### Changed
- **Builds stream: no file's bytes are held in memory, whatever their size.** Large files
  are compressed from disk a piece at a time into a temporary spill file next to the
  output, native libraries are read a window at a time, and the writer streams blob data
  and hashes the result on 8 threads. A file that changes while the build reads it fails
  the build instead of shipping other bytes. Building a torch app took 1204 MB of peak
  RSS (1869 MB with `--best-compression`); now 243 MB and 641 MB, in the same time. A raw
  sparse 1 TiB file was built, checked, streamed and extracted with each process under
  1 GB. A module over 16 MiB is kept as data.
- **The launcher reads large raw blobs from the file instead of its mapping**, so a sparse
  executable on tmpfs cannot exhaust memory and end with SIGBUS; read errors are reported
  as `OSError`, not as corrupt data.

## [0.11.0] - 2026-10-09

### Changed
- **Extraction and reads stream, with no size limits of fsbpyr's own.** Blob content is
  decoded at most 64 MiB at a time for every codec, and extraction decodes, hashes and
  writes a window at a time under a temporary name, renamed once the digest is right;
  pieces of zeros become holes. The 1 GiB cap on blobs goes (up to 2^62 bytes), and a
  path's length and depth have no limit of fsbpyr's. Resources over 16 MiB opened as files
  stream and seek. Extracting 1.5 GiB + 600 MiB + 80 MiB stays within 512 MiB of added
  resident memory.

### Fixed
- **SIGPIPE and SIGXFSZ no longer end the process.** The launcher now installs signal
  handlers as `python` does, so a write to a closed pipe or socket raises
  `BrokenPipeError` and a write past `ulimit -f` fails with `EFBIG`.

## [0.10.0] - 2026-10-09

### Changed
- **Hardened launcher build.** The launcher is built with `-Wvla` and
  `-Wframe-larger-than=8192`, its payload parsing and decoding are fuzzed with libFuzzer,
  and table-driven edge cases (65 mutations of an executable, 14 invalid paths) run against
  both the launcher and the Python reader, with an ASan/UBSan runtime for the tests.

### Fixed
- **A cached file whose name the file system refuses fails with its real error.** A
  256-byte path component used to be reported as modified after extraction. Any other
  error opening a cached file has the file replaced, and `EINTR` is retried.

## [0.9.1] - 2026-10-09

### Fixed
- **Much lower memory use when checking and restoring cached files.** The executable's own
  check mapped the whole file (226 MB resident for a 233 MB torch app), and the cache check
  mapped up to 546 MiB of cached files at once; both now read through a 64 KiB buffer per
  thread. Restored files are written in pieces of at most 1 MiB, so XFS no longer keeps
  them as 2 MiB folios that the dynamic loader maps whole. The torch app's peak RSS fell
  from 577 MB to 349 MB (its venv: 314 MB). A file that shrinks while it is hashed now
  fails its check instead of ending the process with SIGBUS.

## [0.9.0] - 2026-10-09

### Changed
- **The launcher walks, checks and restores the native cache in C.** Cached files are
  checked (a regular file of the user's, of the payload's size and digest) and missing or
  changed ones restored without the GIL, creating directories below the cache root one
  component at a time, never through a symlink, owned by the user and forced to 0700. On a
  torch app, later runs import in 538 ms instead of 615 (venv: 470), and the check of its
  12,151 cached files takes 53.5 ms instead of 80.

## [0.8.0] - 2026-10-09

### Added
- **`--best-compression` stores native libraries as xz.** With `--best-compression` (or
  `best-compression = true`) native files are compressed with xz -6 and the x86 filter,
  libraries over 4 MiB in 4 MiB pieces that the launcher decodes in parallel (format v5).
  zstd 9 stays the default, and the option excludes `--fast-compression` (a flag on the
  command line replaces the other one set in the project). aiagent: 149.7 -> 140.0 MB,
  build 18.5 -> 21.1 s, first runs +25 to +76 ms, later runs unchanged.
- **torch is a built-in fs-package**, since it runs `bin/torch_shm_manager` by a path next
  to its `__file__`.

### Changed
- **Cached files are written without fsync, and every process checks each cached file
  once.** A torch app's first run fell from 42.5 s to 1.34 s, its cache from 1.2 GB to
  709 MB, and later runs from 895 to 620 ms.

## [0.7.0] - 2026-10-09

### Changed
- **Files extracted to the cache are checked with the executable's digest.** They are
  checked with the same piece-wise SHA-256 as the executable (format v4), on the same
  threads. The startup budget, the integrity check aside, is 1.2x bare CPython. Cache check
  overhead against size-only checks: numpy +4-5 ms, cryptography +1.3 ms.

### Fixed
- **No long wake-up delay on hybrid CPUs.** A thread joining the check could wake on an
  efficiency core while numpy's OpenBLAS filled every core (+38 ms); the caller now spins
  for up to 2 ms before it sleeps (+4-5 ms).

## [0.6.0] - 2026-10-08

### Added
- **Every executable checks its whole file before it starts.** The trailer ends with a
  SHA-256 over the SHA-256 of each 1 MiB piece of everything before it (format v3). The
  launcher checks it on up to 8 threads before reading anything else, and exits 70 with
  "integrity check failed: the file was modified or damaged" on a mismatch. Cost with the
  file in the page cache: 2.0 ms for 28 MB, 8.9 ms for 148 MB; `FSBPYR_TRACE` reports it as
  `integrity_ns`. Standard tools (`split --filter`, `openssl dgst`, `sha256sum`) reproduce
  the digest. This detects damage and changes that leave the digest alone, not a change
  that also rewrites it.
- **`fsbpyr inspect` shows the digest** (JSON: `digest`, `digest_covers`), and `--verify`
  recomputes it.

### Changed
- **Cached files are hashed by every process, once, before first use**; a changed one is
  reported on stderr and restored. `FSBPYR_VERIFY` and the retry after a failed `dlopen`
  are gone. `fsbpyr build` verifies its own file before copying its runtime out of it, and
  refuses executables without a whole-file digest.

## [0.5.0] - 2026-10-08

### Changed
- **Small blobs are packed into shared zstd blocks (format v2).** Every non-empty blob under
  64 KiB except native files is a member of a block: 64 KiB blocks for code, 256 KiB for
  resources, metadata and sources; the launcher keeps the last 32 decompressed blocks.
  aiagent: 157.4 -> 149.6 MB for about +5 ms on import-heavy starts. `--fast-compression`
  also sets the level of every block. Readers accept format versions 1 and 2.

## [0.4.0] - 2026-10-08

### Changed
- **zstd level 9 by default; `--fast-compression` keeps level 3.** Every blob, the
  runtime's stdlib included, is compressed at level 9 (a new runtime);
  `--fast-compression` (or `fast-compression = true`) keeps level 3 for the app's content,
  and the build info records the level. aiagent: 167.7 -> 157.4 MB, build 14.4 -> 18.0 s;
  decompression speed does not depend on the level.

## [0.3.0] - 2026-10-08

### Added
- **`fsbpyr inspect` lists libraries and modules.** It lists bundled shared libraries and
  native extension modules, and the top-level module names per distribution, the standard
  library's and fsbpyr's, in as many columns as the terminal fits. `--all-modules` lists
  every module, `--json` prints the same as JSON, and `--verify` checks the payload's
  SHA-256.

### Changed
- **The "reads files next to `__file__`" guess is opt-in.** It was mostly false alarms;
  `fsbpyr build --hints` shows it.

## [0.2.0] - 2026-10-08

### Added
- **`fsbpyr inspect BINARY` shows what an executable was built with**: the app's name,
  version and commit, the fsbpyr that built it (as its `fsbpyr --version` printed), Python
  and glibc, size and distribution count. Builds now record the project's name and version
  and the fsbpyr version; older builds show what they recorded. Corrupt build information
  or a non-regular file is a clean error.

## [0.1.1] - 2026-10-08

### Fixed
- **Libraries that extensions open by name are extracted.** onnxruntime's extension opens
  `libonnxruntime_providers_shared.so` with `dlopen` and no DT_NEEDED entry, so it was never
  extracted and every import printed "Init provider bridge failed". A bundled library in an
  object's directory or on its `$ORIGIN` search path now joins the closure when the object
  names its file. One whose own needs cannot be met (onnxruntime-gpu's CUDA provider without
  a bundled CUDA runtime) is left out with a warning instead of failing the build, and
  "loaded by no extension module" is reported only when some packaged Python code names the
  library.

## [0.1.0] - 2026-10-07

### Added
- **`fsbpyr build` turns a Python CLI project into one executable.** The executable carries
  CPython 3.14, the project and its locked dependencies, installed fresh from `uv.lock`,
  `pylock.toml`, a requirements file or an unlocked project (pyproject or setup.py, uv
  workspaces), configured through `[tool.fsbpyr]` or options such as `--entry`, `--extra`,
  `--requirements`, `--offline` and `--no-source`. Builds are reproducible byte for byte,
  and a new executable runs once as a smoke test.
- **Modules are imported straight from the file.** A static, LTO-linked, PIE CPython
  launcher with an appended zstd payload validates it, imports the stdlib with no file
  system probes and serves the app's modules, resources, `pkgutil` and
  `importlib.metadata` from memory: a no-op app starts in about 4.95 ms against 4.84 ms for
  bare `python3.14 -I -S`.
- **`fsbpyr runtime build` and `runtime list`.** The runtime is built from a pinned,
  SHA-256-checked python-build-standalone release with clang and lld 22 or newer, into a
  private cache. fsbpyr builds itself into a single executable, which reuses its own
  runtime and needs no compiler, download or cache to build apps.
- **Native extensions and the native cache.** Extension modules and the shared libraries
  they need (through `$ORIGIN` paths, against the manylinux system-library allowlist plus
  `--allow-system-lib`) are packaged with digests and extracted on first import into
  `${XDG_CACHE_HOME:-~/.cache}/<app>` (or `$FSBPYR_CACHE_DIR`), safely and race-free; only
  the closure an import needs is extracted. `FSBPYR_TRACE=1` prints a JSON summary of what
  a run loaded and extracted.
- **A compatibility layer for code written for installed packages.** multiprocessing works
  with every start method; packages that need real files (`--fs-package`, with built-in
  rules for certifi, babel, botocore, matplotlib and litellm) are extracted to the cache;
  resources asked for as paths (`importlib.resources.as_file()`, `os.fspath()`) are
  extracted on demand; `.pth` path lines, `inspect.getsource` on frozen stdlib modules and
  a CA bundle fallback when OpenSSL finds no store are handled.
- **Portable to glibc 2.28.** The launcher is linked against a pinned glibc 2.28 sysroot
  and uv selects wheels for `manylinux_2_28`; the build reports the executable's glibc
  requirement and warns when a native file needs more. Executables run on 13 distributions
  from Debian 10 and Rocky 8 to Fedora and Arch.
- **uv is required on the build host.** uv installs every lock source with the runtime's
  platform; the pip fallback is gone, since pip-installed builds picked wheels for the
  build host's glibc.
- **Versions from `vX.Y.Z` git tags.** `fsbpyr --version` prints `X.Y.Z-<8 hex>` for a
  build of a tag and `untagged-<8 hex>` otherwise; `fsbpyr build` records the project's
  commit and tag when git is available, and a git failure is only a warning.
