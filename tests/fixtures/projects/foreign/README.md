# Foreign locks (research UC3)

Fixtures for `poetry.lock`, `pdm.lock` and `Pipfile.lock` as the lock of a build
(`src/fsbpyr/foreign.py`, `src/fsbpyr/pipenv.py`). Each
project depends on click and rich (with markdown-it-py, mdurl and pygments), on colorama for
Windows only (a marker), on packaging in the extra `fancy`, and on iniconfig in a dev group.
Every hash is the SHA-256 of the file on PyPI (2026-10-25).

I could not run Poetry or PDM here, so the locks are written by hand following the formats of
real files, and the `content-hash` / `content_hash` of each is computed by the formula in
`foreign.py`:

| Directory | Lock | Follows |
|-----------|------|---------|
| `poetry2/` | `poetry.lock`, lock-version 2.1, `groups` and `markers` per package, `[project]` with `[tool.poetry] package-mode = false` | locks written by Poetry 2.0.0 and 2.0.1 in the atomic-agents repository (`atomic-examples/*/poetry.lock`) |
| `poetry1-unpackaged/` | `poetry.lock` of `poetry1/`, lock-version 2.0; `[tool.poetry] package-mode = false`, no `[project]`, no `[build-system]`, `main.py` | the same lock; the pyproject follows Poetry 1 projects that are not packages |
| `poetry1/` | `poetry.lock`, lock-version 2.0 (Poetry 1.5 to 1.8): no group, no marker, `[tool.poetry]` only, packaged with poetry-core | the lock written by Poetry 1.8.5 in atomic-agents (`atomic-forge/tools/tavily_search/poetry.lock`) |
| `poetry-legacy/` | `poetry.lock`, lock-version 1.0: `category`, hashes in `[metadata.files]` | the lock in the myia repository (`myia_utils/poetry.lock`) |
| `pdm/` | `pdm.lock`, lock_version 4.5, strategy `inherit_metadata`, `distribution = false` | the format described in PDM's documentation; no real `pdm.lock` was at hand |
| `pipenv/` | `Pipfile.lock` (pipfile-spec 6) beside its `Pipfile`, in a directory of scripts (`main.py`, no `pyproject.toml`); the extra `fancy` does not exist (a Pipfile.lock has none), `iniconfig` is in `develop` and packaging is not locked | the format of Pipenv's documentation (`_meta` with `hash`, `requires` and `sources`; `default` and `develop` entries with `hashes`, `index`, `markers` and `==` versions); no real `Pipfile.lock` was at hand |

The Poetry content-hash formula was checked against the 16 locks of those repositories: 15 match, and one (Poetry
1.8.3's in atomic-agents, `atomic-forge/tools/webpage_scraper`) does not, which I take for a lock
that is out of date in its repository. The PDM formula was not checked against a
real lock.

The `_meta.hash` of the Pipfile.lock is computed by `pipenv._pipfile_hash`, which follows plette's
`Pipfile.get_hash` as I remember it (sorted compact JSON of the sources, `requires`, `packages` and
`dev-packages`); I could not run Pipenv, so that formula is not checked against a lock Pipenv wrote.
