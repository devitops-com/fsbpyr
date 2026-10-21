# Contributing

Bug reports and pull requests are welcome. A vulnerability goes to a private report, as
[`SECURITY.md`](SECURITY.md) describes, not to an issue.

## Packaging fixes

Every packaging fix (a module, file or library that a built executable lacks or gets wrong)
adds a fixture or corpus test that fails without the fix: a project under
`tests/fixtures/projects/` that the end-to-end tests build and run, or an app in the corpus
once it exists. A bug found once stays tested.

## Running the tests

`make check` runs the static checks (ruff and mypy), `make test-ci` the part of the suite that
CI runs, and `make test` all of it. The end-to-end tests use a cached runtime or build one,
which needs clang and lld 22 or newer; with neither, they skip. `make test-ci` also fails when
branch coverage falls below `fail_under` in `pyproject.toml`. That value comes from a run with
the end-to-end tests, so a run where they skip can fail on coverage alone.

The tiers, from fastest to most complete:

- `make test-ci`: unit and integration tests and the core end-to-end tests, as CI runs them
  before a release.
- The full suite in CI (`.github/workflows/full-suite.yml`): everything but the portability
  tests, as the user `tester` in a privileged Debian trixie container with rootless podman, so
  the root-only, supplementary-group and `podman unshare` tests run. It runs on every push to
  main (which is pushed to GitHub for a release only), weekly and on request, once per target
  (x86-64 on `ubuntu-24.04`, aarch64 on `ubuntu-24.04-arm`) and Python, with
  `FSBPYR_TESTS_REQUIRE_ALL=full` (no skip is expected), and `make release` and the publish
  job require a green run, aarch64 jobs included, for the commit a release is made from. The
  fuzzer runs for 600 s (`FSBPYR_FUZZ_SECONDS`) when `src/fsbpyr/launcher` or
  `tests/fuzz` changed since the last tag and on the weekly run, else for 30 s; the corpus it
  finds (`FSBPYR_FUZZ_CORPUS`) is kept in the Actions cache. The launcher programs the tests
  build are instrumented for llvm-cov (`FSBPYR_LLVM_COV_DIR`), and the run's summary shows the
  report; it is informational. A run's duration has not been measured yet; the job's limit is
  60 minutes.
- `make test`: everything, on your machine, in 8 processes (`JOBS=` to change it; about 2.5
  minutes on 24 cores). The tests that need what it lacks skip: root's own directories, a
  second user, a supplementary group, strace, groff, podman.
- `make test-container`: everything but `tests/e2e/test_portability.py` in the Containerfile's
  `test` stage, a Debian trixie container where `tools/test-setup.sh` has added those things
  and the suite runs as the user `tester`. It needs only podman, make and uv on the host;
  uv's cache and Pythons and the runtime's pinned downloads (CPython and the sysroot's
  packages, `FSBPYR_TEST_DOWNLOADS`) stay in two podman volumes, so a second run does not
  fetch them again: the summary's "runtime downloads" section says `seeded` or `fetched` for
  each. It runs with `FSBPYR_TESTS_REQUIRE_ALL=container`, so a skip that the `container` list
  in `tests/conftest.py` does not name fails it. Its one expected skip is "needs podman",
  for the tests that map a second range of user ids with `podman unshare`: the stage has no
  podman. It copies the checkout (without `.venv` and `dist`) into the container, so run it
  from a checkout with its `.git` directory, not a linked worktree.
- Portability (`tests/e2e/test_portability.py`, `podman run` on 13 distributions, 12 on aarch64
  where `archlinux:latest` has no image): needs podman on the host and is not part of `make
  test-container`. CI runs it per target and Python after the suite job.
