# Contributing

Bug reports and pull requests are welcome. A vulnerability goes to a private report, as
[`SECURITY.md`](SECURITY.md) describes, not to an issue.

## Packaging fixes

Every packaging fix (a module, file or library that a built executable lacks or gets wrong)
adds a fixture or corpus test that fails without the fix: a project under
`tests/fixtures/projects/` that the end-to-end tests build and run, or an app in the corpus
(`tests/corpus`, with the rules in its README). A bug found once stays tested.

## Running the tests

`make check` runs the static checks (ruff and mypy), `make test-ci` the core of the suite,
`make test` all of it but the corpus, and `make test-corpus` the corpus. `make check-c` runs
clang's static analyzer over the launcher's C sources; it is informational (full-suite.yml runs
it with `continue-on-error`) and exits 1 when the analyzer has findings. Every target that
checks or tests but `make test-vm` takes `PYTHON=3.X` (3.12 to 3.15, on the command line only)
to run on that Python's variant. The end-to-end tests
use a cached runtime or build one, which needs clang and lld 23 or newer: the `make` targets
put LLVM's clang and lld 23 that `tools/release/llvm23` pins first on PATH (fetched once, see
the README's "Building from source"); run directly, pytest skips them without a clang 23 on
PATH. `make test-ci` also fails when branch coverage falls below `fail_under` in
`pyproject.toml`. That value comes from a run with the end-to-end tests, so a run where they
skip can fail on coverage alone.

The tiers, from fastest to most complete:

- `make test-ci`: `tests/unit`, `tests/integration` and the core end-to-end tests
  (`test_build`, `test_runtime_app`, `test_imports`, `test_stub_hardening`, `test_multicall`,
  `test_scripts`, `test_loose_directory`, `test_single_script`, `test_install_links`,
  `test_pid1`, `test_unbuffered`, `test_file_overlay` and `test_pipfile_lock`: real builds of
  projects, several commands, projects without a build system, directories of scripts, single
  scripts, install links, services as PID 1 with unbuffered output, the `__file__` overlay and
  Pipfile.lock projects), with the coverage check.
  `FSBPYR_TESTS_REQUIRE_ALL` names one of four lists in `tests/conftest.py`: `container`, `full`
  (full-suite.yml's suite and corpus jobs), `el8` (el8.yml's tests job: `full`'s skips and "needs
  podman") and `portability` (the portability runs: only "no wheel for Python " and, outside the
  EL8 mode, "no glibc 2.28 apps"). A skip the list does not name fails the run.
- The full suite in CI (`.github/workflows/full-suite.yml`): every test and check there is, so
  that a release (`.github/workflows/release.yml`) only builds and publishes. The release build
  (`.github/workflows/build.yml`) with its smoke test and an independent rebuild compared byte
  for byte; everything but the portability tests and the corpus, as the user `tester` in a
  privileged Debian trixie container with rootless podman, so the root-only,
  supplementary-group and `podman unshare` tests run; the corpus, in a job of its own; the
  portability tests; and the page-size smoke test (below). It runs on every push to main that
  changes more than documentation (a merge; pull requests start no CI), weekly and on request,
  once per target (x86-64 on `ubuntu-24.04`, aarch64 on `ubuntu-24.04-arm`) and Python, with
  `FSBPYR_TESTS_REQUIRE_ALL=full` (only the `full` list's skips: the test that 3.12 skips by
  version and an app with no wheel for the Python), and `make release` and the publish job
  require a green run, aarch64 jobs included, for the commit a release is made from. The fuzzer
  runs for 600 s (`FSBPYR_FUZZ_SECONDS`) when `src/fsbpyr/launcher` or `tests/fuzz` changed
  since the last tag and on the weekly run, else for 30 s; the corpus it finds
  (`FSBPYR_FUZZ_CORPUS`) is kept in the Actions cache. The launcher programs the tests build
  are instrumented for llvm-cov (`FSBPYR_LLVM_COV_DIR`), and the run's summary shows the
  report; it is informational. A run takes about 40 minutes (37 to 40 in October 2026); the
  job's limit is 60 minutes.
- `make test`: everything but the corpus, on your machine, in 8 processes (`JOBS=` to change
  it; about 2.5 minutes on 24 cores). The tests that need what it lacks skip: root's own
  directories, a second user, a supplementary group, strace, groff, podman.
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
  for each. It runs with `FSBPYR_TESTS_REQUIRE_ALL=container`, so a skip that the `container`
  list in `tests/conftest.py` does not name fails it. Its expected skips are that list's: "needs
  podman", for the tests that map a second range of user ids with `podman unshare` (the stage
  has no podman), and the few that need another Python, a mount that allows set-id bits or a
  tool the stage lacks. It copies the checkout (without `.venv` and `dist`) into the container,
  so run it from a checkout with its `.git` directory, not a linked worktree.
- Portability (`make test-portability`, `tests/e2e/test_portability.py`; `podman run` on 12
  distributions, 11 on aarch64 where `archlinux:latest` has no image): needs podman and
  llvm-objdump on the host and a `make build` first (it tests that executable's builds of the
  fixtures); not part of `make test-container`. CI runs it per target and Python after the
  suite job.
- `make test-vm`: aarch64 apps booted in QEMU system VMs (`tools/vm-smoke.py`): the kernel of a
  Raspberry Pi 5 (16 KiB pages) and EL8's (64 KiB pages), which the aarch64 runners' 4 KiB
  kernels cannot test. It needs `qemu-system-aarch64` (TCG, no KVM; yours to install) and
  aarch64 executables, because fsbpyr builds only for its host: `make test-vm VM_ARGS="--app
  PATH... --run 'COMMAND => TEXT'..."` (without `VM_ARGS` it only boots the kernels) copies
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
  EL8 stubs or not. It starts when `release.yml` completes successfully for a version tag
  (`workflow_run`) and only the `publish` job, after every other job passed, writes: it uploads
  the eight `fsbpyr-stub-3.X-<target>-glibc2.28.xz` and then `SHA256SUMS-glibc2.28` to the tag's
  release, attests them, and touches no other asset (a rerun that finds the same sums uploads
  nothing). A release has EL8 stubs when it has `SHA256SUMS-glibc2.28`; `make release-check`
  verifies them when it does (and prints `skip: no EL8 stubs` when not). To attach them to a
  release that has none, or to retry a failed run, start the workflow by hand: `gh workflow run
  el8.yml -f tag=vX.Y.Z` (or Actions, EL8, Run workflow, from the default branch: the plan job
  refuses another). Everything runs from the tag, so the tag must have the EL8 support (`fsbpyr
  runtime build --glibc`, `tools/release/el8-stub.py`, `tests/support/baseline.py`); an older
  one fails in the first job with a message. Like all workflows, the file used is the default
  branch's. On a 24-core machine `make test-el8-stub` takes about 3.5 minutes with 4 processes
  (the EL8 and default runtimes linked from cached downloads) and `make test-el8` about 3
  minutes once the runtimes and images are cached; how long the jobs take on a runner is not yet
  measured (each is meant to stay near 20 minutes).

## Documentation-only changes

A pull request that changes only documentation (`*.md` files and `docs/`) starts no full
suite when it is merged: full-suite.yml leaves such pushes to main out (`paths-ignore`), its
corpus included. Never put a CI skip instruction (`[skip ci]` and the like) in a merge commit's
message instead: GitHub applies it to a tag pushed on that commit too, so release.yml would not
run, and `make release` refuses such a commit. Before merging, run the tests that read
documentation: `uv run pytest tests/unit/test_cli_docs.py`
(the README's and `docs/compatibility.md`'s tables against the code), and `uv run pytest
tests/e2e/test_build.py -k quickstart` when the README's quickstart changed. A release made
from such a commit stands on the run of the newest commit before it that full-suite.yml ran
for: `tools/release/green-run.sh`, which `make release` and the publish job use, accepts it
only when nothing but documentation changed since, and names that run. A change to code,
tests, configuration (`pyproject.toml`, `uv.lock`, the workflows, the Makefile) or the release
tools runs the full suite when it is merged.
