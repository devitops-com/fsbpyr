# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions 0.15.2, 0.15.3, 0.16.0, 0.17.0, 0.17.1 and 0.18.0 have no `vX.Y.Z` tag; their release
commits are on main (d489698, 7c11a1c, 80a1f1b, 86b2645, 299355e and 21ac25d). The GitHub
releases of 0.18.1 and 0.18.2 were withdrawn; their tags remain.

## [Unreleased]

No code changes since 0.28.0: this release carries 0.28.0's code under a new version, so that its
wiki can be published from the repository (`make wiki-publish` publishes release tags, and
v0.28.0 predates the repository's `wiki/`).

### Fixed
- **The wiki explains how fsbpyr builds itself.** A new Developer Guide chapter, Self-hosting
  (`wiki/Dev-Self-Hosting.md`, chapter 9 of the PDF), describes the three stages from C and
  Python sources (`make bootstrap`) and from a released fsbpyr without a compiler, which parts of
  an executable change from stage to stage, and why stages 2 and 3 must be the same bytes.
- **The wiki no longer says that `make release-check` rebuilds a release.** It checks a
  published release as its users get it; a rebuild from the tag is check (a) of CONTRIBUTING.md's
  "Verifying a release".

## [0.28.0] - 2026-10-27

### Changed
- **The full suite runs each job's tests in 4 processes.** `full-suite.yml` ran them in one
  process, 20 to 27 minutes of a 26 to 35 minute job; it now runs `pytest -n 4 --dist loadfile`,
  one process per core of GitHub's runners for public repositories, as `make test` runs them in 8,
  with the kept downloads (`FSBPYR_TEST_DOWNLOADS`), so that the main process builds the suite's
  runtimes once before the workers start. Of the other Pythons' runtimes, the job builds only the
  one the dispatch test needs, not three. `make test-ci` runs in `JOBS` processes too.
- **The test suite has no skips.** A test whose tool is missing (a C compiler, `uv`, `unshare`,
  `podman`, `llvm-objdump`, `groff`, `git`, `strace`, `zstd`, `wget`, `busybox`, `make`) or whose
  environment is not as it needs (a normal user, a supplementary group, user namespaces,
  set-id bits, a directory of another user, 10% free disk) now fails with one line that names
  what to install or change and points at CONTRIBUTING.md; `make test-prereqs` lists everything
  a machine lacks at once. A case that does not apply to the Python, target or mode is not
  generated, deselected (`-m "not podman"` where there is no podman, packages without a wheel in
  `tests/support/no-wheels.txt`) or asserts its own behaviour. A skip or xfail by any means fails
  the run. `FSBPYR_TESTS_REQUIRE_ALL` and its allowlists are removed, from the tests, the
  Makefile and the workflows. `make test-container` runs with `--privileged`, the Pythons 3.12 to
  3.15 and a `/var/tmp` that allows set-id bits, so that it leaves out nothing but the tests
  that need podman.
- **The runtime is python-build-standalone 20261026's: CPython 3.12.16, 3.13.18, 3.14.10 and
  3.15.2**, on x86-64 and aarch64, still LLVM 23.1.3 bitcode linked with LLVM's clang and lld
  23.1.3. An executable gets the new CPython when it is rebuilt with this fsbpyr; a source
  checkout's Python is 3.14.10 (`.python-version`). Every runtime id changes (the pins, the
  launcher and runtime layout 10), so cached runtimes are built again, and this release's EL8
  stubs are new ones. PBS 20261026 still has SQLite 3.53.1, with CVE-2026-11822 and CVE-2026-11824
  (FTS5, fixed in SQLite 3.53.2), and zlib 1.3.2, with CVE-2026-85091: the next runtime update
  follows the first PBS release that fixes them. (#52)
- **The release is built with uv 0.13.6 (was 0.13.5).** `tools/release/uv.version` and the
  Containerfile name it, with the SHA-256 of uv's release tarball for x86-64 and aarch64; the
  Debian trixie image is unchanged. (#51)

### Fixed
- **The EL8 workflow can publish the companion release.** GitHub refuses a workflow's token a
  release or tag whose target is a commit that adds or changes a workflow file, as v0.27.0's
  merge commit did, so `el8.yml` could not create `el8-v0.27.0` (HTTP 403, also with the tag
  already pushed, since it named the commit as the target). `make release` now pushes the tag
  `el8-vX.Y.Z` with `vX.Y.Z`, on the same commit, and `el8.yml` creates the release on that tag
  (`--verify-tag`, no target), as `release.yml` creates its own; it fails early, with the
  command to push the tag, when it is missing. `el8-v0.27.0` was published by hand from the
  stubs el8.yml built and attested on the v0.27.0 tag. (#75)
- **aarch64 executables are linked with the Cortex-A53 erratum 843419 fix.** On a Cortex-A53
  (Raspberry Pi 3 and Zero 2, many other boards), an `adrp` in one of a 4 KiB page's last two
  instruction slots, followed by certain loads or stores, can compute a wrong address. PBS links
  its own aarch64 builds with lld's `--fix-cortex-a53-843419` since 20261026, but fsbpyr links the
  stub itself from PBS's bitcode, so the stub's link now passes the flag; it is part of the
  aarch64 runtime id.
- **A command script's reads next to its `__file__` inside `if __name__ == "__main__":` are
  reported.** For a project without a build system, a directory of scripts or a single script, the
  build warned about a script's reads next to its `__file__` at module level and in functions, but
  skipped its main guard, where a script does most of its work, so the app failed at run time with
  `FileNotFoundError` instead. The build now also warns about those reads in the main guard of
  each module the executable runs as `__main__`: its commands, or the module `--entry` names;
  `--strict` counts them, as it counts the others. The main guard of every other module never runs
  and is still skipped: a script's local-import siblings, the modules of a `[project]` that its
  `[project.scripts]` do not name, the commands that `--entry COMMAND` leaves out, a module whose
  command calls a function in it (`tool:main`), and packages; their other reads are reported as
  before. A script that starts a sibling command, `subprocess.run([sys.executable,
  os.path.join(here, "sibling.py")])` or an `os.exec*` of `sys.executable`, is not reported: the
  sibling runs from the payload, so the advice to deploy that file beside the binary was wrong.
  `--hints` no longer advises `--fs-package` for the packages among such a project's own files,
  which are read next to the executable. (#57)
- **`--strict` no longer counts the `--hints` file-read guess.** `these packages read files next
  to __file__ (a heuristic)` is a guess about the packages, like the other uncounted ones, so a
  `--hints --strict` build no longer fails on it. The `--strict` help and man page now say what is
  not counted: the build host's uv and git, the notes on `requirements/` files and `Pipfile.lock`
  indexes, and the guesses about packages (helper interpreters, files read next to `__file__`,
  shared objects, executables). (#60)
- **A `__main__.py` under a `site-packages` directory no longer delays the missing-entry error.**
  A packaged project with no console script, no `__main__.py` and no guarded module fails before
  uv stages anything. That check walked into `site-packages` directories (installed packages, not
  the project's), which the file walk of a project without a build system skips, so such a project
  was staged first and failed only afterwards. Both walks now skip the same directories. (#71)
- **The Python of a build comes from the lock source the build installs.** A `Pipfile.lock` beside
  a `requirements` setting in `[tool.fsbpyr]` chose the Python (its
  `_meta.requires.python_version`), although the build installed the requirements file the setting
  names. The Python is now chosen from the lock source `requirements` names, whether it comes from
  the settings or from `--requirements`, which already worked. (#58)
- **The `uv pip freeze` hint names the virtual environment the build reads.** When a command is
  skipped for an unresolved import, the hint used to appear only when there was a `.venv` beside
  the scripts, and it always named `.venv/bin/python`. It now names the environment the dependency
  inference reads: `.venv`, `venv` or `env` beside the scripts, else `$VIRTUAL_ENV`, by its
  absolute path. Each needs a `pyvenv.cfg`, so a `.venv` without one no longer gets the hint.
  (#68)
- **A build that goes from one command to several, or back, replaces its own earlier output.**
  Going to several, it stopped with `cannot link command 'a': .../dist/a exists and is not a
  symlink` when it found the executable that an earlier build of one command had written there (a
  script added to a directory of scripts, or a skipped command's dependency declared); the link
  now replaces a file that is an fsbpyr executable of that command whose build information names
  the same project, and any other file still stops the build. Going back to one command (a script
  removed or skipped, or `--entry` naming one of the project's scripts), it wrote its executable
  through the earlier build's link over `dist/<project>`, so the other commands' links ran it too;
  it now replaces its own link with the executable and leaves `dist/<project>` and the other links
  as they were (they run the earlier build until you remove them). A link of your own at the
  output path is still written through. (#74)
- **Executables for Python 3.12 and 3.13 carry zstd's licence text, and the SBOM lists zstd.** The
  launcher links zstd whatever the Python, but PBS lists `LICENSE.zstd.txt` only for the `_zstd`
  module, which 3.12 and 3.13 lack, so `fsbpyr inspect --licenses` showed no zstd text for their
  executables. Every runtime now carries it: PBS's own text, or fsbpyr's copy where PBS ships
  none. The runtime's recorded components (`runtime_components` in `build_info`) and so the SBOM
  also left zstd out; its version is now read from the archive itself (the string
  `ZSTD_versionString()` returns), `fsbpyr inspect` shows it among the components, and the SBOM
  gives it NVD's CPE, `cpe:2.3:a:facebook:zstandard:<version>`. If the archive holds no single
  version string, the component is left out rather than guessed. (#56)
- **A missing home directory is reported as an error.** When the cache location needs the home
  directory and there is none (`HOME` unset, empty or relative, and a passwd entry without an
  absolute home), `fsbpyr build`, `runtime build`, `runtime list` and `cache gc` print the one
  line `fsbpyr: error: cannot find the home directory; set HOME, XDG_CACHE_HOME or
  FSBPYR_CACHE_DIR` and exit 1. They used to print the internal-error traceback, which asked for a
  bug report. With `FSBPYR_CACHE_DIR` set, `cache gc` collects that cache and needs no home
  directory. (#59)
- **`FSBPYR_SELFTEST=natives` reports a refused cache directory as the app does.** With a cache
  directory that another user owns or others may write to, the native check of an app with native
  code now ends with the one line `fsbpyr: cannot start <executable>: refusing cache directory
  <path>: ...` and exit 70, as a run does since 0.27.0. It used to end with a traceback and exit
  1. (#79)
- **A downloaded EL8 stub is reused after an update when it has not changed.** The cache named the
  glibc 2.28 stub after the release tag, so the first `--glibc 2.28` build after every update
  downloaded it again, even when the stub was the same (0.26.0's and 0.27.0's are), and a build
  host without the network could not build for EL8 until someone placed the stub. The cache now
  names the stub after the runtime id it must carry, which decides whether it is accepted. An
  entry cached by an earlier version is not found and costs one more download. (#77)
- **`fsbpyr build --offline` downloads nothing.** It still downloaded the glibc 2.28 stub when
  none was placed or cached, and the fsbpyr of another Python for a project that needs one. With
  `--offline` a build now uses only a placed or cached stub and an fsbpyr of the other Python that
  is already cached. Otherwise it fails and says what to do: place the stub (its name and where),
  or run the build once without `--offline`. (#78)
- **`fsbpyr build --glibc 2.28` for a project on another Python uses the stub placed beside
  fsbpyr.** For such a project, fsbpyr runs the fsbpyr of that Python from its cache, and that
  fsbpyr looked for a placed `fsbpyr-stub-3.X-<target>-glibc2.28` only beside itself in the cache.
  A stub placed beside the fsbpyr that was run, where the docs and the error messages say to put
  it, was ignored: the build downloaded the stub, or failed without the network, with `--offline`
  or without a companion release, and named the cache directory as the place for the stub. It now
  looks first beside the fsbpyr that handed the build to it, and its messages name that place. A
  stub found there is accepted only if it carries the runtime id the build expects.
- **`fsbpyr self update` names only the placed EL8 stubs that `--glibc` refuses.** It used to name
  every `fsbpyr-stub-*-glibc*` file beside the executable as the old release's, including a stub
  that had not changed and that `--glibc` accepts. It also missed the stubs beside the path fsbpyr
  was run by (a symlink), which `--glibc` reads too. It now looks where `--glibc` looks and names
  only the stubs whose SHA-256 is not in the `SHA256SUMS-glibc2.28` of the new release's
  companion. When it cannot read that file (no companion yet, no network, a download cut short),
  it names them all and says that `--glibc` refuses them if the runtime id changed. (#76)
- **A download cut short is an error, not a traceback.** When a connection closed before the end
  of a body (`http.client.IncompleteRead`), `fsbpyr self update`, a build that fetches the fsbpyr
  of another Python and a `--glibc 2.28` build that fetches its stub ended in the internal-error
  traceback that asks for a bug report. They now report the failed download like any other.
- **`fsbpyr man` names clang and lld 23.** Its example for `fsbpyr runtime build` said clang and
  lld 22 or newer; the runtime is linked with 23 or newer. The Containerfile's image no longer
  installs Debian's clang-22, lld-22 and libclang-rt-22-dev, which nothing in it used; its `test`
  stage installs libc6-dev and libstdc++-14-dev itself, which the tests' C programs build against
  and which used to come in with clang-22. (#66)
- **An app at its instance limit says the limit is per user.** The refusal said the copies running
  were `as many as this machine allows at once`, but the limit counts one user's copies. It now
  says `as many as this user may run at once`, and docs/compatibility.md quotes the new text.
  (#67)
- **fsbpyr's messages and help name `uv run` shebangs.** Any `uv run` shebang, with or without
  `--script`, makes a file a script, as a python shebang does. The error for a file that is not a
  Python script, the error for a directory without a command, and the help of `fsbpyr build` (and
  the man page rendered from it) said `uv run --script` or a python shebang only. (#69)
- **The man page, `docs/compatibility.md` and `docs/performance.md` say what the code does.** The
  man page's exit 70 now also names a refused cache directory; it no longer says an app built with
  `--no-interpreter-reexec` is refused as a helper interpreter, and `FSBPYR_MAX_INSTANCES` counts
  the copies one user runs. In `docs/compatibility.md`, the dispatcher form `acme acme-db -m word`
  runs that command and is not refused; Ansible's modules on the implicit `localhost` end with
  `unknown command '.../AnsiballZ_x.py'`, not exit 70 (set `ansible_python_interpreter` to a real
  interpreter); and a cache on a `noexec` mount raises `_fsbpyr.CacheError`. In
  `docs/performance.md`, the start checks came with 0.26.0, not "0.25.0 (unreleased)", and
  spinning on non-blocking joins costs +4-7 ms; `spikes/bench.py` starts its runs with
  `os.posix_spawn`, and the rows that say so used `subprocess.run`. (#61, #62, #67, #70, #81)
- **The format, security and executable-parts docs match the code.** `docs/format.md` names
  `src/fsbpyr/payload/format.py` as the format's one reference, and says how the cache writes a
  file (a new one unnamed, `O_TMPFILE`, and linked into place; a replacement, or a file that
  cannot be linked, under a scratch name and renamed), which directory a script gets on
  `sys.path`, when a `Pipfile.lock` `file` entry gives an `artifact_sha256`, the whole blob order,
  the backslash in the path rule and which validation rules the launcher leaves to the integrity
  digest. The runtime id names the stub's inputs, which several releases may share, not a release:
  the launcher sources and their flags, the PBS pin, the sysroot and the layout version.
  `docs/security.md` limits the stub id check to the stubs a build is handed (the EL8 ones today),
  cites D5 for project code on the build host and names the `module:function` command whose module
  the smoke test imports, and no longer says the dispatcher form `exe B -m word` is refused: it
  runs command `B`. `docs/executable-parts.md` names the build information, not the header, as
  what differs in an app built with `--glibc 2.28`. (#61, #63, #64, #65, #80, #81)

## [0.27.0] - 2026-10-27

### Changed
- **16K-page kernels are not tested any more.** `make test-vm` and the `vm-smoke` job boot only
  EL8's 64K-page kernel. Raspberry Pi 5's 16K-page kernel hung before `/init` in three of four
  CI boots under the runner's QEMU, and 16K-page systems (the Pi 5's default kernel, Asahi
  Linux on Apple Silicon) are too few to matter for now: they are not supported. A Raspberry
  Pi 5 can boot its 4K kernel (`kernel=kernel8.img` in `config.txt`).
- **The test suite has no timing budgets.** The startup budget test
  (`test_startup_is_within_budget_of_bare_cpython`, 1.5x bare CPython) and the wall-clock bound
  of the canonicalizer's nested-set test are removed, the detached-collector test no longer
  measures how long the launcher took, and Hypothesis runs without its 200 ms per-example
  deadline locally, as it already did in CI. The startup budget failed on a loaded runner (1.8x on
  a 4-core GitHub runner against 1.21-1.24x locally), and a wall-clock bound fails a slow machine,
  not a wrong result. Startup is measured, not gated: `docs/performance.md` still records the
  measurements.

### Fixed
- **The EL8 stubs are published in a companion release.** `el8.yml` could not attach the glibc
  2.28 stubs to the release `v0.26.0`: releases are immutable and take no asset after
  publication (`HTTP 422: Cannot upload assets to an immutable release`). The stubs of `vX.Y.Z`
  are now the assets of the release `el8-vX.Y.Z`, whose tag is on the same commit. `el8.yml`
  creates it as a draft, uploads the eight `.xz` and `SHA256SUMS-glibc2.28`, and publishes it;
  it is not the latest release, so `releases/latest`, `fsbpyr self update` and the installer
  still point at `vX.Y.Z`. `fsbpyr build --glibc 2.28` downloads the stub from there, and says
  `release el8-vX.Y.Z (the glibc 2.28 stubs of vX.Y.Z) does not exist or has none for this
  Python` when there is none. `make release-check` checks the companion release. The attestations
  are still those of the run on the tag: `--source-ref refs/tags/vX.Y.Z`. For `v0.26.0` the stubs
  are in [`el8-v0.26.0`](https://github.com/devitops-com/fsbpyr/releases/tag/el8-v0.26.0)
  (nine assets, attested from `refs/tags/v0.26.0`), but fsbpyr 0.26.0 itself still looks in
  `v0.26.0` and stops with `this release (v0.26.0) has no glibc 2.28 stubs`: download the stub
  for your Python and target from `el8-v0.26.0`, check it against its `SHA256SUMS-glibc2.28` (the
  sums are of the unpacked stub: `xz -dc <asset>.xz | sha256sum`) and place it next to the fsbpyr
  executable. README, "Older systems: EL8 and glibc 2.28".
- **A refused cache directory is a refused start.** With `FSBPYR_CACHE_DIR` (or the default
  cache) in a directory that another user owns or others may write to, an app with native code
  ends with the one line `fsbpyr: cannot start <executable>: refusing cache directory <path>:
  other users may write to it` and exit 70, as the integrity check does, at the first extraction.
  It used to surface as the root cause of an `ImportError` (NumPy printed advice about a broken
  installation) with exit 1. Other extraction failures stay errors; apps without native code pay
  nothing. (#42)
- **A compiled package's missing data file names `--fs-package`.** A compiled module's own
  `OSError`, such as mypyc's `blib2to3` reading `Grammar.txt` while the package is imported, is no
  longer reported as "cannot extract native code ... set FSBPYR_CACHE_DIR". When the missing
  file is below the native cache's `site/` directory, the uncaught error ends with
  `fsbpyr: <path> is missing from the native cache, which holds the compiled files of a package
  only; <package> reads files next to its __file__: rebuild with --fs-package <package>`. (#43)
- **A failed smoke test shows whole lines.** The tail of the new executable's error output that
  the build prints starts at a line boundary, with a note of how many earlier lines were left out,
  instead of in the middle of a line. The same holds for the tails of `uv` and launcher link
  errors, and of a failing compile worker or interpreter query. (#41)
- **One is one.** The build says `collecting 1 distribution`, `1 command` and `the 1 distribution
  a lock source names`, not the plural. (#45)
- **The attestation example works, and is checked.** `.github/workflows/attest-example.yml`
  downloaded the release asset `fsbpyr`, which releases have not had since 0.22.0; it now takes
  the latest release's `fsbpyr-3.14-x86_64-linux-gnu`, runs `gh attestation verify` on the app it
  attested, as the README tells whoever downloads it, and keeps the app as the run's artifact.
  The README no longer says this is untested: `gh attestation verify` accepted the downloaded
  app, whose attested SHA-256 is the `sha256` that `fsbpyr inspect --verify` prints. (#46)
- **A VM boot that hangs shows how far it got.** `tools/vm-smoke.py` (`make test-vm`, the
  `vm-smoke` job) boots with kernel messages from the first line on (`earlycon`, no `quiet`), so
  a boot that never reaches `/init` leaves its last messages in the report instead of an empty
  console. `/init` turns them down to `quiet`'s level before its first marker line.

## [0.26.0] - 2026-10-26

### Added
- **Builds that ask no questions.** `fsbpyr build` now builds what it finds, in four more cases
  than a project with one console script. None writes anything into the source tree, and none
  runs project code during the build.
  - **Several console scripts build one executable with all of them.** With no `--entry` and two
    or more console scripts, the build no longer asks for a choice: it writes `dist/<project>`
    (or the `-o` path), whose entry point is a table of `name=target` lines (format version
    unchanged), and a relative symlink `dist/<command>` beside it per command, each made as a
    temporary link renamed into place. A regular file or directory in the way of a link fails the
    build before anything is written; an existing symlink is replaced. A start runs the command its
    `argv[0]` names (`.py` stripped), else the one `argv[1]` names, which is removed from
    `sys.argv`; with neither the executable lists its commands on stderr and exits 2. A start under
    a command's link is never a helper interpreter (D44): command A starting `B -m word` runs
    command B; `sys.executable -m ...` is still refused, and so are hard-link starts. The selftest
    checks every command, a forkserver preloads the parent's command, `fsbpyr inspect` (and its
    JSON, as `commands`) and the build summary list the commands, and `--smoke-args` starts with
    the command's name. Command names, of one console script as of several, must match
    `[A-Za-z0-9][A-Za-z0-9_.+-]*`. All links share
    one instance budget (D45). A one-command build is unchanged except that it carries the new
    `_fsbpyr_rt.commands` module, which only a table loads.
  - **A project with a `[project]` table and no `[build-system]` builds as `uv run main.py` runs
    it.** That is `uv init --no-package`, and Poetry `package-mode = false` and PDM
    `distribution = false` projects (a Poetry 1 project without a `[project]` table too: its name,
    version, scripts and `[tool.poetry.dependencies]`, with `^` and `~` converted, are read from
    `[tool.poetry]`; its `poetry.lock` locks the build); a uv workspace root keeps its build. These used to fall back
    to setuptools: two modules failed with "Multiple top-level modules", a `utils/` package was
    dropped, and `<name>.egg-info` was left in the tree. fsbpyr now writes the project's wheel
    itself with the standard library (no build backend, no project code, nothing written into the
    tree, the same bytes twice): the project's `*.py` files and nothing else, under the reserved
    root `_fsbpyr_scripts/`, from `git ls-files` (the tracked files; the build warns for each
    untracked Python file that is not ignored, which it leaves out) or a pruned walk without git;
    links out of the tree are refused, and a `.py` file below a directory that is no Python name
    is reported and left out. The dependencies come from `uv.lock` as before, else
    `[project.dependencies]` unlocked, or from `poetry.lock` / `pdm.lock` (see the entry below).
    The commands are `[project.scripts]`, else the modules with a main guard (one: the
    project's command; several: a multi-command executable, named after the files, `main.py` after
    the project, a collision fails naming both). A script runs as `__main__` with its directory
    first on `sys.path`, so `import utils` works; `__file__` is next to the executable (`<exe dir>/main.py`; see the Changed
    entry on `__file__`).
  - **A directory of scripts builds as one executable with a command per link.** `fsbpyr build`
    in a directory with no `setup.py` or `setup.cfg` (a `setup.cfg` alone is still refused) and
    no `pyproject.toml`, or one that holds only tool settings, packages its Python files: `*.py`
    and extensionless files whose first line is a python or `uv run` shebang, from `git ls-files`
    (tracked files only; an untracked Python file that is not ignored, such as a `secrets.py`, is
    left out with a warning naming it) or a pruned walk, and nothing else, so a `.env`, a key or a
    data file is never in the executable. A file with a module-level main guard or a python
    shebang is a command named after the file (hyphens kept, `main.py` and `__main__.py` after
    the directory), test files are never commands, and two files with one name fail the build,
    naming both. A file whose name is no Python name (`check-disk.py`, `10-backup`) is stored
    under a module name in its directory (`check_disk.py`, `_10_backup.py`) and runs as
    `__main__`; its `__file__` is that path. The dependencies are `requirements.txt` or
    `pylock.toml`, else the union of the commands' PEP 723 blocks (`requires-python` intersected,
    a conflict fails naming the files), else the venv the scripts run in (see the entry below), else
    none. A `pyproject.toml` that holds
    only tool settings (no `[project]`, `[build-system]`, Poetry tables, `setup.py` or `setup.cfg`)
    is the directory's settings file: `[tool.fsbpyr]` (`glibc`, `python`, `strict`, ...) is read
    from it, where setuptools' flat-layout discovery used to fail on several modules; a
    `[tool.fsbpyr]` table in a command's PEP 723 block is ignored in a directory build, with a
    warning. The static import check also searches a directory a file puts on `sys.path` with a
    path built from its `__file__` (the `pylib` hack), and treats the imports of a file whose
    `sys.path` change it cannot follow as optional. The output is `dist/<directory name>` and the
    links, and the build prints how to install them on a host. README and
    `docs/compatibility.md` cover monitoring plugins and cron fleets: the shared instance budget
    (`FSBPYR_MAX_INSTANCES`; a start beyond it exits 70), the per-start integrity check, and
    crontab lines that ran `python3 x.py`.
  - **One script builds as an executable: `fsbpyr build report.py`.** The argument may be a
    `*.py` file or an extensionless file whose first line is a python or `uv run --script`
    shebang; it was refused with "Directory 'report.py' is a file". The app is named after the
    file's stem (hyphens kept) and written to `dist/<name>` in the script's directory, or to `-o`.
    Only the file and its static local-import closure are packaged (the siblings and packages it
    imports from its directory, transitively, including a directory it adds to `sys.path` from
    its `__file__`): never the rest of the directory, so a script among 200 unrelated ones holds
    its own few modules, and the directory's `pyproject.toml`, `requirements.txt` and `uv.lock`
    are not read. It runs as in a directory of scripts. The PEP 723 block gives the dependencies
    (unlocked), the `requires-python` (which selects the Python and dispatches to another
    Python's fsbpyr as a project's does) and `[tool.fsbpyr]`, read as in `pyproject.toml`,
    unknown keys rejected. A `<script>.lock` that `uv lock --script` wrote beside the script
    locks the build: the staging environment is filled by `uv sync --script --active --locked`,
    so a lock the block has outgrown fails the build; the build records it as `script.lock`.
    fsbpyr never writes beside the script: no lock, no environment, no cache.
  - **Scripts are checked statically and never run by the build.** The smoke test finds a
    script and does not import it, so a script's top-level code never runs on the build host
    (a `--entry module` build still runs that module's top-level code, as it always did, and the
    README says so). A static import check follows each command's imports and skips a command
    that imports what the executable lacks, with `file:line` (`--strict` fails; none left
    fails), and names a standard library module removed in the build's Python (`cgi`, `imp`, ...)
    with the older `--python` that has it. The build warns about files a script reads next to its
    `__file__` (`file:line`), and a `.py` file below a directory that is no Python name is
    reported for the project's own files (a directory of scripts packages it, see below).
  - **Scripts find their siblings by relative paths, in directories with any name, and start
    each other.** The executable's path hook resolves a `..` in a `sys.path` entry inside the
    executable (`sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))`
    imports `lib/`), and an entry that would climb above the executable is not served from it;
    the static import check follows the same path. In a directory of scripts, a directory whose
    name is no Python name (`sub-dir/`, `2024-tools/`) is packaged, stored under the name made
    as a file's (`sub_dir/`, `_2024_tools/`; two directories that give one name fail the build):
    a script in it is a command with its directory first on `sys.path`, and
    `sys.path.append(os.path.join(os.path.dirname(__file__), "sub-dir"))` finds its modules; the
    build no longer warns that it leaves such files out. A script that starts one of the app's
    own commands as `subprocess.run([sys.executable, <sibling's __file__>, ...])` gets that
    command, run as `python sibling.py` would, instead of exit 70 (D44 stays for every other
    helper-interpreter child, and a path that is a command's link already ran the command).
- **A project with no console scripts builds from its `__main__` package or guarded module.**
  With no `--entry`, the build picks the project's only top-level package with a `__main__.py`
  (run as `python -m pkg`), else its only top-level module with an `if __name__ == "__main__":`
  guard (found with `ast`, never by importing it), and prints which one and why. Several
  candidates, or a package and a module, keep the "choose one with --entry" error and list them.
- **A directory of requirements builds the tools it names: `cd tools && fsbpyr build`.** A
  directory with no Python file (no `pyproject.toml`, or one that holds only tool settings) and a
  lock source (`requirements.txt`, `pylock.toml`, `--requirements FILE`) used to fail with "no
  Python files to package"; it now builds one executable whose commands are the console scripts
  of the distributions the file names (`ansible-core==2.17.*`, `httpie==3.2.4`), with the links,
  the deploy lines and the output name (`dist/<directory name>`, or `dist/<command>` for one) of
  a directory of scripts. The lock is installed as for any pip project (pins, hashes and
  `--strict` as before) with a wheel of metadata only for the directory, named after it, or
  `<name>-tools` when a requirement has that name; nothing of the directory is packaged. A
  requirements file names its distributions directly (`-r` includes are followed, `-c` files
  name nothing; a line that gives no name, such as a path, is refused). A file that lists every
  installed distribution (`pip freeze`, `uv pip freeze`, `pylock.toml`) has as tools only the
  listed distributions that no other listed distribution requires, and the build prints which it
  took and why. A dependency's scripts are not commands. A tool with no console or gui script of
  its own falls through to its direct requirements, under the runtime's markers and the extras
  asked for, when it is a meta-package (it installs no code of its own) or for the requirement
  named after it (a made-up `acme` requiring `acme-cli`), down to a distribution with scripts (a
  cycle or eight levels stop it), and the build names the meta-package and the tools taken
  through it; any other distribution with no script is a library, not a tool, and the build says
  so. A tool also has the commands of its **family**, decided while building it (not by the
  owner): its direct requirements whose name is the tool's name and a dash (`ansible` gives
  `ansible-core`'s `ansible`, `ansible-playbook`, ... beside its own `ansible-community`), under
  the runtime's markers and the extras asked for, and theirs in turn; the build names the members
  it took commands from; a script-name clash inside a family is the clash error. `gui_scripts` are commands like console scripts (pip and uv
  install them as ordinary launchers on Linux). Two tools with one script name fail the build
  naming both distributions (a console and a gui script of one name clash too); no script at all
  fails it, saying what the file named. A directory with
  a Python file is a directory of scripts as before. README ("A tool from its requirements
  file") and `docs/compatibility.md` cover tools that start helpers through `sys.executable`
  (exit 70 unless built with `--interpreter-reexec`), commands that are interpreters (reachable
  as `tools ipython`; a `sudo` rule names the link) and the licences of the tools, which are
  collected as for any app.
- **`fsbpyr build --glibc 2.28` builds an app for EL8** (RHEL 8, Rocky Linux 8, AlmaLinux 8), as
  does `glibc = "2.28"` in `[tool.fsbpyr]` (in a directory of scripts, in its settings-only
  `pyproject.toml`; in a single script, in its block); the command line wins, other values are
  rejected with the allowed ones, and `--glibc 2.34` is the default and builds as before. For 2.28
  uv picks `manylinux_2_28` wheels, a dependency without one is built from its sdist against the
  EL8 sysroot, the warnings about libstdc++, libgcc_s and libz use auditwheel's manylinux_2_28
  limits, and `fsbpyr inspect` shows `baseline   glibc 2.28` (`glibc_baseline` in `--json` and in
  the build information; builds for the default record nothing new). The launcher for 2.28 is a
  separate release asset, one per Python and target
  (`fsbpyr-stub-3.14-x86_64-linux-gnu-glibc2.28.xz`, with the SHA-256 of the stub in
  `SHA256SUMS-glibc2.28`; a file placed by hand may be plain or `.xz`): a release fsbpyr pairs its
  own standard library with the stub of its own release, taken from a file placed in the
  directory of the fsbpyr executable (for machines without GitHub access), else from its cache,
  else downloaded and verified. EL8 assets are published separately and not with every release:
  without them the build stops and says so, naming the asset to place. fsbpyr run from a checkout
  or a wheel uses the runtime that `fsbpyr runtime build --glibc 2.28` made, and never fetches.
  Every launcher now records the runtime id it was linked as, and a stub that is not the one of
  this fsbpyr's release, Python and target is refused; the default launcher's runtime id changed
  for that (its behaviour did not). See the README, "Older systems".
- **`fsbpyr runtime build --glibc 2.28` links the stub against an EL8 sysroot.** The sysroot is
  Rocky Linux 8.10's glibc 2.28, libgcc 8 and kernel 4.18 headers, read from the pinned RPMs by a
  reader of fsbpyr's own (no `rpm` tool is needed). A runtime built this way has an id of its
  own and never mixes with the default one (Debian 12's sysroot, glibc 2.34, which stays the
  default); `fsbpyr runtime list` now shows each runtime's glibc. The launcher compiles its
  `_dl_find_object` wrapper only where the headers have it (glibc 2.35); the default stub behaves
  as before, but its source changed, so its runtime id does.
- **A separate workflow attaches EL8 stubs to a release.** `.github/workflows/el8.yml` runs on
  the release tag itself: it starts on the push of a version tag, in parallel with `release.yml`,
  or by hand on the tag's ref (`gh workflow run el8.yml --ref vX.Y.Z`, for a release that has
  none or to retry), and is outside the release: `release.yml`, `full-suite.yml` and the release gate neither wait for it
  nor know it, and a release may have EL8 stubs or not. For every Python and target it links the EL8
  stub from the tag's sources twice in independent caches and requires the two to be byte for
  byte identical, with no GLIBC symbol above 2.28 and the runtime id its release's fsbpyr
  expects (`tools/release/el8-stub.py`); on the default Python only (CI minutes: the stubs differ
  per Python only in the linked libpython, which that check covers for every Python) per target,
  it runs the stub-dependent tests with the EL8 stub, the
  portability matrix with apps the tag's own fsbpyr (built in the workflow as `build.yml` builds the
  release) built with `--glibc 2.28` (the stub
  placed beside it) on the 12 images and `rockylinux:8` and `almalinux:8`, four native-heavy
  corpus apps (`pandas-csv`, `lxml`, `pillow`, `scipy`) built the same way and compared with
  their venvs, and, on aarch64, an app on EL8's 64 KiB-page kernel with the EL8 userland in a
  QEMU VM. Only when every job passed, and the release of the tag exists (it waits for it), does it write
  `SHA256SUMS-glibc2.28`, attest the stubs (from a run on the tag) and
  upload the eight `.xz` and then the sums to the release, touching no other asset; a release
  has EL8 stubs when it has `SHA256SUMS-glibc2.28`.
- **`make test-el8` runs the EL8 test mode and its portability matrix.** With
  `FSBPYR_TEST_GLIBC=2.28` the tests that run the stub use the glibc 2.28 runtime and every
  `fsbpyr build` of a test is a `--glibc 2.28` one (`make test-el8-stub`: the launcher, the
  `_fsbpyr` contract and native wheels at manylinux_2_28); `make test-el8-portability` runs the
  portability matrix on apps built for glibc 2.28, with `rockylinux:8` and `almalinux:8` added
  (they are in no other run). `FSBPYR_CORPUS_APPS` runs a subset of the corpus, and
  `tools/portability-apps.sh` takes the glibc as a fourth argument.
- **`make release-check` checks a release's EL8 stubs.** With `SHA256SUMS-glibc2.28` among the
  assets it verifies the sums, that each `.xz` unpacks to its listed digest, their attestations
  by `el8.yml` from the tag, and that the release's fsbpyr builds a pure-Python fixture with `--glibc 2.28`,
  fetching the stub as a user does, which runs in `rockylinux:8`; a release without reports
  `skip  no EL8 stubs`, not a failure, and the EL8 assets are no longer reported as unexpected.
- **`make test-vm` runs aarch64 apps on 16K- and 64K-page kernels.** `tools/vm-smoke.py` boots
  Raspberry Pi 5's kernel (16 KiB pages) and EL8's (64 KiB pages) in `qemu-system-aarch64`
  (TCG, no KVM) with an initramfs of a pinned Debian trixie (or Rocky Linux 8.10) root file
  system and the aarch64 apps it is given, runs their commands and checks the page size, the
  exit codes and the output on the serial console. Kernels and root file systems are pinned by
  URL and SHA-256 (`tools/vm-pins.toml`). `full-suite.yml` gains a `vm-smoke` job that runs the
  aarch64 executable and two fixture apps, one with native extensions, on both kernels. The
  README no longer calls 16K- and 64K-page kernels untested: they are smoke-tested in virtual
  machines, not on hardware.
- **SLES 15 SP6 and SP7 are supported and tested.** Their glibc is 2.38 and fsbpyr apps run on
  them; the 0.24.0 entry's statement that SLES/openSUSE Leap 15 are no longer supported holds for
  SLES 15 SP4 and SP5 (glibc 2.31) and openSUSE Leap 15 only. The portability matrix gains
  `registry.suse.com/bci/bci-base:15.7` (SLES 15 SP7, 12 images now, 11 on aarch64), and the
  README and `docs/compatibility.md` list SLES 15 SP6 and newer.
- **Legacy `scripts=` files that are Python are commands.** The files of the project's wheel
  under `<dist>.data/scripts/` that start with `#!python` (setuptools writes that for
  `scripts=['bin/tool']` and `script-files` from any python shebang; the build reads the wheel,
  since staging rewrites the line) join the console scripts as commands named after the file,
  hyphens kept: one makes the executable, several make the links of a multi-command
  executable, and a name that is also a console script fails the build, naming both. A project
  whose only commands they are builds without `--entry`. A script runs as `python script` runs
  it (`__main__`, `argv[0]` the path it was started by, its directory first on `sys.path`); its
  source is stored in the executable under a module name (`check-disk` as `check_disk.py`, `tool`
  as `_x_tool.py`), and, as for the scripts of a directory, its `__file__` is that name next to
  the executable. A file under `bin/` that is not a `#!python` script (a shell script), and a
  script that does not compile, is not packaged, and the build warns naming it (use
  `[project.scripts]` or `--entry`).
- **`poetry.lock` and `pdm.lock` lock a build.** They used to be left unread, with a warning
  and an unlocked build. fsbpyr converts the lock in its staging directory (nothing is written
  into the project) into a requirements file for uv: the main (Poetry) or default (PDM) group
  and the `--extra`s the lock lists, markers evaluated for the app's Python and platform, every
  hash of a package, and `--require-hashes` when every package has one. Poetry locks of any
  lock-version are read (1.0's `category` and `[metadata.files]`; 1.5 to 1.8 record no group, so
  the packages are those the pyproject's dependencies reach through the lock; 2.x `groups` and
  `markers`), and PDM locks of lock_version 4 with a group per package (`inherit_metadata`) or
  the default group only. A path dependency installs as a regular package, a VCS entry keeps its
  commit and a URL entry its hash; a package from another index, a VCS entry without a commit, a
  missing path and a package locked at two versions that both apply are refused, naming the
  entry. A lock whose content hash is not the pyproject's is a warning, and an error with
  `--strict`. They rank after `requirements.txt` as lock sources. `build_info.lock.kind` and
  `fsbpyr inspect` show `poetry.lock` or `pdm.lock`.
- **`Pipfile.lock` locks a build.** A project or a directory of scripts (a legacy Pipenv app is
  mostly that) with a `Pipfile.lock` is built from the lock's `default` section, never
  `develop`: the same conversion in the staging directory, each entry's pinned version, all
  its hashes and its markers for the app's Python and platform, with `--require-hashes`. A
  `git` entry keeps its commit (a `ref` that is no commit is refused), a `file` entry its hash,
  and a `path` entry (`editable` or not) installs as a regular package; `pipenv install -e .`
  of the project itself is left to fsbpyr's own wheel. A package from a source other than PyPI
  installs from the indexes uv is configured with, the lock's hashes deciding whether it found
  the same files, and the build says so (not counted by `--strict`). `_meta.hash` that is not
  the Pipfile's is a warning, an error with `--strict`; so is a lock with no Pipfile beside a
  `pyproject.toml` that declares dependencies, which the lock replaces. `_meta.requires` (`python_version` or
  `python_full_version`) chooses the Python as a `requires-python` of that minor version would,
  but `--python`, `python` in `[tool.fsbpyr]` and `.python-version` outrank it, and a version
  fsbpyr cannot build is a warning. It ranks last among the lock sources (a `requirements.txt`
  beside it wins), and unlike `poetry.lock` and `pdm.lock` a directory of scripts reads it.
  A directory with a `Pipfile.lock` and no Python file builds the tools in the Pipfile's
  `[packages]`, or, with no Pipfile, the packages of the lock that no other package of it
  needs ("A tool from its requirements file"). `build_info.lock.kind` and `fsbpyr inspect` show
  `Pipfile.lock`; its hashes carry no file names, so a distribution has no `artifact_sha256`.
- **The venv a script runs in gives the dependencies nothing declares.** A directory of scripts
  or a single script with no requirements file, `pylock.toml`, lock or PEP 723 dependencies, and
  an import nothing in the executable provides, used to skip the command with a hint. Now the
  build reads the venv beside the scripts (`.venv`, `venv` or `env` with a `pyvenv.cfg`, else
  `$VIRTUAL_ENV`) as files: its python is never started. The `*.dist-info` METADATA,
  `top_level.txt` and RECORD say which installed distribution provides each unresolved import
  (a PyPI name is never guessed from an import name), and those distributions and their
  installed `Requires-Dist` closure are pinned `name==version` and installed fresh by uv for the
  target (manylinux_2_34, or 2.28 with `--glibc 2.28`); nothing is copied from the venv. The
  build warns that the dependencies came from the venv and gives the
  `uv pip freeze --python <venv>/bin/python > requirements.txt` that declares them (for a single
  script, the command without the redirection, for its block's dependencies); `--strict` fails
  on it before anything is staged. A pin without a wheel for the target fails in uv, with uv's
  reason; an editable or path install that is needed is refused. An import the venv does not
  provide stays unresolved. `build_info.lock.kind` and `fsbpyr inspect` show `inferred`, with the
  venv and the pins. The README's "a `.venv` is never read" is gone with this.
- **Compile warnings of the project's own files are build warnings.** A `SyntaxWarning` or
  `DeprecationWarning` that compiling emits (`"\d"` in a normal string, say) used to show
  neither at build time nor at run time, while a venv run of the script prints it. The build now
  prints it with its file and line (`fsbpyr: warning: pkg/mod.py:12: SyntaxWarning: ...`) for the
  project's own modules; dependencies and the standard library stay silent, and `--strict`
  counts it like the other warnings. The bytecode is unchanged.
- **`FSBPYR_INSTALL_LINKS=DIR app` makes the command links on the target** (D14). The executable
  links each of its commands into DIR (absolute symlinks to its real path), and `x.py` besides `x`
  for a command that is a script, so a crontab, unit or NRPE line that runs `/opt/tools/x.py`
  keeps working; it prints what it did and exits without running the app. A second run changes
  nothing. A file, a directory or a link to something else in the way is never replaced: it is
  reported, the other links are still made and the exit status is 1; a missing or unwritable DIR
  is one error before any link. There is no option: `<binary> --install` would let a `sudo
  <binary> *` rule create root-owned links anywhere, and sudo's `env_reset` drops `FSBPYR_*`. A
  start pays one environ lookup; the code is in `_fsbpyr_rt/links.py`, imported only then. The
  build's deploy recipe mentions it.

### Changed
- **`sys.argv[0]` is the full path of the start, as in a venv** (D14). A start by `app` on `PATH`,
  `./app` or a link used to give the name as typed; the launcher now reports `AT_EXECFN` and the
  boot sets `sys.argv[0]` to that path, made absolute, when its basename is `argv[0]`'s. `exec -a
  other app` keeps `other`. A multi-command start and the restart idiom read the basename and are
  unchanged. A relative start gives `/cwd/app` where a venv script keeps `./app`.
- **`JOURNAL_STREAM` line-buffers the journal, and SIGTERM ends an app that is process 1** (D14).
  When systemd's `JOURNAL_STREAM` (`device:inode`) names the file stdout or stderr is, that stream
  is line-buffered, so each line reaches the journal as it is printed and survives a SIGTERM;
  `PYTHONUNBUFFERED` still wins. An app that runs as process 1 (a container without an init) used
  to ignore SIGTERM, as `python app.py` does, because the kernel drops a signal that process 1 has
  no handler for; it now exits with status 143, unless the app installed a SIGTERM handler of its
  own. Neither costs the common start more than an environment lookup and a pid check.
- **`PYTHONUNBUFFERED` is honoured** (D14). Set and non-empty, it makes an app's stdout and
  stderr unbuffered as CPython does, so a service's log lines reach a pipe as they are printed
  and survive a SIGTERM. It is the only `PYTHON*` variable the apps read.
- **`fsbpyr self update` refuses an executable that needs a newer glibc than the host has,** and
  warns about EL8 stubs beside the executable. The glibc its build_info records is compared with
  the host's before anything is replaced; the message names both versions. Stubs placed next to
  the executable for `--glibc 2.28` belong to the old release and a build would refuse them: the
  update names them and leaves them alone.
- **The uncaught-error note for a file below the executable says when `--fs-package` does not
  apply.** A top-level module that reads `<exe>/x.conf` (the path is no module) now gets a note, a
  namespace directory is told to get an `__init__.py` first (a regular package below it is named),
  and the note for a script says where the file is read (next to the executable, see below).
- **A script's `__file__` is next to the executable, and what it reads there is the host's file**
  (D24; the owner reversed decision 5.7(a) on 2026-10-25). For the scripts of a project without a
  build system, a directory of scripts and a single script, `__file__` and `spec.origin` are the
  executable's directory plus the file's path in the project (`/opt/tools/sub/x.py`), so `open(os.path.join(os.path.dirname(__file__),
  "config.ini"))` reads the `config.ini` deployed beside the binary, as beside a script run by
  path. The code is still the executable's: the path hook claims those directories too, so a
  stale `.py` copy beside the binary is never imported and `sys.path.append(dirname(__file__) +
  "/pylib")` serves the packaged `pylib`; code objects keep the payload file name, so a traceback
  shows the packaged source lines. Packaged projects are unchanged. A `name.py` link started by
  its own path keeps `argparse`'s prog the file's name on 3.14.9 and later, a sibling started as
  `[sys.executable, <its __file__>]` is still the app's own command, and the build's report of
  reads next to `__file__`, the uncaught-error note for a missing file there and README
  say to deploy the file beside the binary. The script's own source is no file there:
  `open(__file__)` fails unless a copy is deployed. A start pays a string check; the rest is
  imported only by scripts that use it.
- **A project without a build system whose `[project.scripts]` point into `src/`** must name
  `_fsbpyr_scripts.src.pkg.mod:main`; the build says so. It built through setuptools before.
- **The smoke test of a `module` or inferred entry runs that module's top-level code on the
  build host,** as it always did; README now says so.
- **`docs/compatibility.md` says what a hard link to an app does.** `sys.executable`, `__file__`
  and `sys.path[0]` of a start through a hard link are below the link's own path, a symlink gives
  the target's, and a rebuild leaves a hard link on the old build.

### Fixed
- **A forked child of an app that is process 1 gets the default SIGTERM back**, so a worker in
  a long C call dies on SIGTERM as under `python app.py`.
- **A command name cannot place the executable or a `FSBPYR_INSTALL_LINKS` link outside the
  output or link directory**: a tool's single script name is validated like several are, the
  links code also refuses a name with a directory part, and the background collection child
  makes no links.
- **A file next to the executable named like a script imports nothing before the parent check.**
- **A distribution name read from a venv's metadata must be a valid name** before it becomes a
  pin.
- **A tool binary from a `Pipfile.lock` with extras** keeps what an extra needs as installed,
  so a dependency does not become a command.
- **A Poetry 1 dependency whose `markers` has an `or`** is kept apart from its `python`
  constraint with parentheses.
- **`--requirements` help** mentions the tool binary.
- **A `--hash=` in a comment of a requirements file no longer makes the build expect hashes.**
  One test now decides it for the early VCS-line check, the install and the manifest.
- **An EL8 stub download stops at 256 MB unpacked** instead of filling the disk before the
  size check.
- **A static project with no console scripts, no `--entry` and no `__main__.py` or guarded
  module fails before uv stages anything**, with the same message, instead of after the full
  build and install.
- **`-e ./libs/common` lines in a requirements file build.** They are installed as regular
  packages (`uv pip install --no-editable`) instead of failing with "installed in editable
  mode"; the build warns that such a line is whatever the tree holds. Lines pinned by a commit
  SHA (`git+URL@<40 hex>`) or a `#sha256=` URL are no longer reported as unpinned. With no
  `requirements.txt` but `requirements/*.txt`, the build prints a hint naming `--requirements`.
- **`--strict`, and a requirements file with `--hash=` lines, refuse VCS requirement lines
  early**, naming the file and lines and saying that a commit SHA pins the source but is not a
  hash, rather than failing inside uv with a generic hash message (uv reads such a file in
  `--require-hashes` mode too).
- **A failed `uv build` shows why.** uv's build is no longer `--quiet`, so the error carries
  the backend's own reason (such as setuptools' "Multiple top-level modules discovered in a
  flat-layout"), and under `-v` "see its output above" is true.
- **`fsbpyr build` no longer leaves `*.egg-info` or `build/` in a setuptools project's tree**
  (in the project root and one directory below it, so `src/`). What the build created is
  removed; what existed before is never touched.
- **A directory with only `setup.cfg` is refused up front** with a clear message, not late
  inside uv.
- **A failed build no longer leaves an empty `dist/`** (or other output directory it created).
- **The launcher's background cache collection (D38) now runs with `HOME` unset or empty.** The
  launcher needed an absolute `$HOME` while the boot code fell back to the passwd home, so such an
  app's cache was never collected (and with `HOME=''` the two disagreed about the cache root). An
  unset, empty or relative `HOME` now means the passwd home in the launcher, the boot code and
  the runtime registry alike.
- **A script started through `sys.executable` finds the modules next to it** with
  `--interpreter-reexec`: `[sys.executable, <exe>/pkg/a.py]` now has `<exe>/pkg` first on
  `sys.path`, as `python /path/pkg/a.py` does, so `import common` of a sibling `pkg/common.py`
  works.
- **The restart idiom `os.execv(sys.executable, [sys.executable] + sys.argv)` gives the child
  the argv of the parent.** gunicorn's USR2 re-exec and Django's autoreloader start
  `[sys.executable, <interpreter flags>, sys.argv[0], *args]`; in a venv that is
  `python /venv/bin/app args`, but a built app ran with its own path as an extra first argument.
  A start whose first argument after the interpreter flags is this very executable (the same
  file, a link to it included) now drops the flags and that argument, keeping it as
  `sys.argv[0]`. Only interpreter flags (`-u`, `-v`, `-X x`, `-W x`, ...) may precede it; any
  other option means the app's own arguments, and nothing is touched.
- **CPython 3.15's misleading hint for a missing module is replaced.** It read "Site
  initialization is disabled, did you forget to add the site-packages directory to sys.path or
  to enable your virtual environment?", which is not what an fsbpyr user can do; an uncaught
  `ModuleNotFoundError` now ends with "fsbpyr: X is not in the executable: add it to the
  project's dependencies and rebuild". Only uncaught exceptions are changed, not those a program
  formats itself with `traceback` or `logging`.

## [0.25.0] - 2026-10-25

### Changed
- **An app that starts itself as a Python interpreter is refused rather than run again** (D44).
  A child the app starts with `sys.executable -m MODULE`, `-c CODE` or a `.py` file of the app
  used to run the app; libraries that start helper interpreters so made it start itself again
  and again: joblib's `n_jobs=2` three copies per copy (a corpus app grew to 1,550 processes in
  90 s and took its host down), Jupyter kernels and py-cpuinfo an endless chain. Such a child
  now exits 70 and says what it asked for, unless the build chose: `--interpreter-reexec` runs
  it as Python (D23), `--no-interpreter-reexec` runs the app as before, for apps whose own
  options are named `-m` or `-c`. A `-c` value that is a name or a constant (`-c config.toml`)
  stays the app's in every build.

### Added
- **Python 3.15.** A fourth fsbpyr runs on CPython 3.15.1 (`fsbpyr-3.15-<target>`, for both
  targets) and builds apps that run on it; the others build a 3.15 project by fetching it, as
  they do 3.12's. 3.14 stays the default (owner, 2026-10-23): `.python-version`, `make build`
  and the installer without `--python` keep to it, and the installer now takes its default
  from `.python-version` rather than from the newest Python it has. What 3.15 needed:
  - canonical bytecode takes marshal version 6 (frozendict, which compile() never emits);
  - extension suffixes come from the interpreter rather than PYTHON.json, which for 3.15.1 lacks
    `.abi3t.so` and the abi3 suffixes with the architecture: cryptography's 3.15 wheel
    (`_rust.abi3t.so`) did not import;
  - its JIT registers frames with libgcc's unwinder, which would raise the glibc baseline to
    2.35 (`_dl_find_object`); the launcher wraps `_dl_find_object` to use glibc's own where it
    exists and the loaded objects' program headers on 2.34 (D43);
  - a multiprocessing forkserver whose preload sets `on_error` (3.15) is recognized;
  - `fsbpyr inspect` no longer reads a RECORD digest with characters outside base64's alphabet
    as valid (3.15's decoder skips them).

  onnxruntime, PyYAML, orjson (whose 3.15 wheels need glibc 2.39) and gnureadline have no
  wheel for 3.15 on the glibc 2.34 baseline yet: onnxruntime cannot be installed on 3.15, the
  others build from their source distributions, which needs a compiler and is not
  reproducible elsewhere. The tests skip what needs them on 3.15
  (`tests/support/no-wheels.txt`). The README's "Python 3.15" says what to expect and
  recommends 3.14 until the wheels arrive. Runtime ids, old → new (the launcher changed):
  - x86-64: 3.12 `2802099b1e2f` → `9049e39d30fe`, 3.13 `db1aa8b7e5a1` → `f9457c914b09`,
    3.14 `e43c48d54bac` → `942f7ca276a0`, 3.15 `555b9ccd65f1`;
  - aarch64: 3.12 `c7dcb94ca154` → `55fca5b8b177`, 3.13 `fd4779d965e7` → `5748326d5108`,
    3.14 `63acc7e1d471` → `9edc7de433d5`, 3.15 `1fb41b74cc31`.
- **An app's copies running at once are limited** (D45). Every start holds one of the
  executable's slots while it runs (an OFD lock in `/dev/shm`, freed by the kernel when the
  process ends), and a start that finds them all taken exits 70. An app that starts itself
  again and again, by whatever route (`sys.executable -m`, `-c`, a shell, a script) and in
  whatever shape, stops at the limit: 4 per CPU and 16, at least 64 and at most 1024.
  `FSBPYR_MAX_INSTANCES` sets it (`0`: no limit). Where the guard cannot work (neither
  `/dev/shm` nor `$XDG_RUNTIME_DIR`, a file someone else made there, no OFD locks) the app
  starts unguarded.
- `--interpreter-reexec` also runs a `.py` file of the app as Python (`sys.executable
  <exe>/pkg/tool.py`), as py-cpuinfo and torch's inductor start theirs.
- The build names the packaged libraries known to start helper interpreters (joblib,
  jupyter_client, py-cpuinfo, multiprocess, torch's inductor) unless it was built with
  `--interpreter-reexec`; `--strict` does not count the note.
- The real-world corpus grows from 11 to 65 apps, covering AI research, applied mathematics,
  physics and services (docs/compatibility.md); it runs in full-suite.yml's new corpus job,
  and locally with `make test-corpus`. Every run of an app in the tests is contained: killed
  with all its processes when it starts too many, outlives its command or times out
  (tests/support/contain.py), and `make test` runs in a cgroup with a task and memory limit
  where systemd's user manager offers one.

### Fixed
- **An fs-package comes with its distribution's plugin libraries.** Every shared library the
  package's distribution ships is extracted with it, also those nothing links, which it opens
  from a directory: openmm's in `OpenMM.libs/lib/plugins`, so that it finds its CPU and OpenCL
  platforms (with `fs-packages = ["openmm"]`), not only `Reference`.
- **OpenCV imports** (with `fs-packages = ["cv2"]`). cv2's loader put its directory into
  `sys.path` behind the executable, found the cv2 package again instead of its binary, and
  stopped with "recursion is detected"; fsbpyr sets OpenCV's own switch that puts the
  directory first.
- **A wheel's ELF file for another machine no longer stops the build**: it is kept as data,
  with a warning, as no loader on the target can load it (another machine, 32-bit or
  big-endian). debugpy has no aarch64 wheel, and its universal wheel carries an x86-64
  library: apps with ipykernel or Jupyter could not be built on aarch64.
- **Libraries another distribution bundles are found at build time.** A native file's `DT_NEEDED`
  entry that no search path provides is taken from the payload's shared objects of another
  top-level directory by file name or SONAME (torchvision's extensions need torch's `libc10.so`
  and `libtorch*.so`, and the RPATH names PyTorch's build machine), when exactly one has it;
  with several, the build stops and names them. An fs-package is extracted with the libraries
  its extensions need from outside it (`torchvision.libs`). The corpus app torchvision now
  matches its venv (with `fs-packages = ["torchvision"]`).
- **An fs-package's cache directory is a path entry of `sys.path_importer_cache`.** Code that
  walks entries from a module's `__file__` (`twisted.python.modules`) found it missing, warned
  "not in path importer cache" and listed no plugins: the corpus app `twisted`, with
  `fs-packages = ["twisted"]`, now lists the same plugins as its venv.
- **Packages compiled with mypyc import their plain modules** (black). mypyc names the
  directory its shared runtime library was extracted to (the native cache) as the compiled
  package's `__path__`, where the payload's plain modules are not: `from black.output import
  err` found nothing. The path hook now serves a directory of the native cache that holds no
  extracted fs-package as the payload directory it mirrors.
- **A library a package loads by its bare file name is found among the libraries the app
  bundles** when the system has none of that name (`ctypes.CDLL("libblosc2.so")`, as
  PyTables does after looking in a site-packages directory a built app does not have). It is
  extracted with the libraries it needs; of several of that name, the one in the package of the
  module calling `CDLL`; a system copy still comes first. PyTables apps failed with `Blosc2
  library not found` on machines without a system blosc2.
- **jedi completes names of the standard library and of other modules of the payload**: parso
  asked the file of a payload module for its time, which fails below the executable with
  `NotADirectoryError` (it expects `FileNotFoundError`), so every completion that looked into
  a module of the payload raised. The time of a payload file is now the executable's.
- **An import-time warning names the importing module, with its source line**, as in a venv.
  A warning raised with `stacklevel=2` or more while a module was imported (starlette's,
  authlib's) named `<frozen _fsbpyr_boot>:83`, the payload loader, and showed no line: CPython's
  warnings skip only frames of importlib, and could not read a payload file's source without
  its module.
- **`from .__init__ import x` works**: `<pkg>.__init__` imports as a module of its own, running
  the package's `__init__` again, as a venv's finder does (statsmodels' `api`).
- **A library with a long RPATH or RUNPATH no longer stops the build**: these lists may be up to
  1 MiB; names stay limited to 4096 bytes. TensorFlow's RUNPATH is about 20 KB.
- **On 3.14 and 3.15 the forkserver imports the app's entry module before forking workers**, as
  a venv's imports the console script: torch's DataLoader workers no longer import torch each.
  The module comes from the executable, never from the command line (D22).
- **The README's rebuild of a release passes the asset's Python.** `fsbpyr-3.12-...` and
  `fsbpyr-3.13-...` built the checkout with the Python its `.python-version` names, 3.14, by
  handing the build to the release's 3.14 executable, so the comparison failed; `--python`
  makes each rebuild itself, as CI's variant check does.

## [0.24.0] - 2026-10-22

### Changed
- **The glibc baseline is 2.34 (manylinux_2_34), and the launcher links against Debian 12.**
  fsbpyr takes its sysroot from the oldest Debian release still in LTS, Debian 12 (owner,
  2026-10-22): glibc 2.36, libgcc 12 and Linux 6.1 headers from Debian's own repository
  (bookworm's main suite, unchanged since its last point release), no longer Debian 10's from
  archive.debian.org. What links against it needs glibc 2.34, the version glibc moved
  `pthread_create`, `dlopen` and `__libc_start_main` to; the launcher binds `hypot` to its
  pre-2.35 version to stay there (`--wrap=hypot`, looked up with `dlvsym`). uv picks
  manylinux_2_34 wheels, and the build warns about native files above auditwheel's
  manylinux_2_34 limits (per target: libgcc_s's GCC version differs). An executable needs glibc
  2.34 or newer: RHEL/Rocky/Alma 9 and 10, Amazon Linux 2023, Ubuntu 22.04 and newer, Debian 12
  and newer. **Debian 10 and 11, RHEL/Rocky 8, Ubuntu 20.04 and SLES/openSUSE Leap 15 are no
  longer supported.** The portability tests run on Rocky 9, Amazon Linux 2023, Ubuntu 22.04,
  24.04 and 26.04, Debian 12 and 13, AlmaLinux 10, openSUSE Leap 16.0, Fedora and Arch. Runtime
  ids, old → new:
  - x86-64: 3.12 `a9a5adbe3f13` → `2802099b1e2f`, 3.13 `4d0cc0e40f7d` → `db1aa8b7e5a1`,
    3.14 `97b0d36b1b68` → `e43c48d54bac`;
  - aarch64: 3.12 `a09051735e25` → `c7dcb94ca154`, 3.13 `2dda88f1c21f` → `fd4779d965e7`,
    3.14 `6d88769e0109` → `63acc7e1d471`.
- **Every test and check runs in the full suite; a release only builds and publishes.** The
  release build is one reusable workflow, `build.yml`. The full suite calls it with its checks on
  every push to main: the build is checked against stage 3, smoke-tested, its `.xz` unpacked,
  and an independent rebuild compared with it byte for byte, SBOM included. The full suite also
  takes the coverage gate of `payload/` and `elf.py`. `release.yml` runs for a pushed tag only and
  calls `build.yml` without the checks, then attests and publishes. Pull requests and pushes to
  main no longer start `release.yml`.
- **No build downloads from snapshot.debian.org.** The build installs git, curl, xz-utils, make
  and zstd from Debian's own repository, and nothing else: clang/lld 22 and the test tools are
  gone from it, since LLVM's pinned clang/lld 23 link. `tools/release/apt-snapshot` and its
  check in `tools/toolchain-watch.sh` are removed.
- **A release is two steps.** `make release-prep VERSION=X.Y.Z` ends a branch with the release
  commit (the version and the changelog), so the full suite tests exactly what is released once
  the branch is merged. `make release` then tags main's HEAD, once its full suite run has passed,
  and pushes the tag alone; it makes no commit, and a VERSION given to it must be HEAD's. A
  release now starts one workflow run. `make release` follows it with a plain line for each job
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
