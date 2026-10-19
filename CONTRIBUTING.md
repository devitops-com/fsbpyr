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
