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
`make test` all of it but the corpus, and `make test-corpus` the corpus. The end-to-end tests
use a cached runtime or build one, which needs clang and lld 23 or newer: the `make` targets
put LLVM's clang and lld 23 that `tools/release/llvm23` pins first on PATH (fetched once, see
the README's "Building from source"); run directly, pytest skips them without a clang 23 on
PATH. `make test-ci` also fails when branch coverage falls below `fail_under` in
`pyproject.toml`. That value comes from a run with the end-to-end tests, so a run where they
skip can fail on coverage alone.

The tiers, from fastest to most complete:

- `make test-ci`: unit and integration tests and the core end-to-end tests.
- The full suite in CI (`.github/workflows/full-suite.yml`): every test and check there is, so
  that a release (`.github/workflows/release.yml`) only builds and publishes. The release build
  (`.github/workflows/build.yml`) with its smoke test and an independent rebuild compared byte
  for byte; everything but the portability tests and the corpus, as the user `tester` in a
  privileged Debian trixie container with rootless podman, so the root-only,
  supplementary-group and `podman unshare` tests run; the corpus, in a job of its own; and the
  portability tests. It runs on every push to main that changes more than documentation (a
  merge; pull requests start no CI), weekly and on request, once per target (x86-64 on
  `ubuntu-24.04`, aarch64 on `ubuntu-24.04-arm`) and Python, with
  `FSBPYR_TESTS_REQUIRE_ALL=full` (no skip is expected), and `make release` and the publish job
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
  list in `tests/conftest.py` does not name fails it. Its one expected skip is "needs podman",
  for the tests that map a second range of user ids with `podman unshare`: the stage has no
  podman. It copies the checkout (without `.venv` and `dist`) into the container, so run it
  from a checkout with its `.git` directory, not a linked worktree.
- Portability (`tests/e2e/test_portability.py`, `podman run` on 11 distributions, 10 on aarch64
  where `archlinux:latest` has no image): needs podman on the host and is not part of `make
  test-container`. CI runs it per target and Python after the suite job.

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
