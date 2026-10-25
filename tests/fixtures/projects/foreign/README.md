# Foreign locks (research UC3)

Fixtures for `poetry.lock` and `pdm.lock` as the lock of a build (`src/fsbpyr/foreign.py`). Each
project depends on click and rich (with markdown-it-py, mdurl and pygments), on colorama for
Windows only (a marker), on packaging in the extra `fancy`, and on iniconfig in a dev group.
Every hash is the SHA-256 of the file on PyPI (2026-10-25).

I could not run Poetry or PDM here, so the locks are written by hand following the formats of
real files, and the `content-hash` / `content_hash` of each is computed by the formula in
`foreign.py`:

| Directory | Lock | Follows |
|-----------|------|---------|
| `poetry2/` | `poetry.lock`, lock-version 2.1, `groups` and `markers` per package, `[project]` with `[tool.poetry] package-mode = false` | locks written by Poetry 2.0.0 and 2.0.1 in the atomic-agents repository (`atomic-examples/*/poetry.lock`) |
| `poetry1/` | `poetry.lock`, lock-version 2.0 (Poetry 1.5 to 1.8): no group, no marker, `[tool.poetry]` only, packaged with poetry-core | the lock written by Poetry 1.8.5 in atomic-agents (`atomic-forge/tools/tavily_search/poetry.lock`) |
| `poetry-legacy/` | `poetry.lock`, lock-version 1.0: `category`, hashes in `[metadata.files]` | the lock in the myia repository (`myia_utils/poetry.lock`) |
| `pdm/` | `pdm.lock`, lock_version 4.5, strategy `inherit_metadata`, `distribution = false` | the format described in PDM's documentation; no real `pdm.lock` was at hand |

The Poetry content-hash formula was checked against the 16 locks of those repositories: 15 match, and one (Poetry
1.8.3's in atomic-agents, `atomic-forge/tools/webpage_scraper`) does not, which I take for a lock
that is out of date in its repository. The PDM formula was not checked against a
real lock.
