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
- `make test`: everything, on your machine. The tests that need what it lacks skip: root's
  own directories, a second user, a supplementary group, strace, groff, podman.
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
- Portability (`tests/e2e/test_portability.py`, `podman run` on 13 distributions): needs
  podman on the host and is not part of `make test-container`.
