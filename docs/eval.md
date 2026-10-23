# fsbpyr vs. similar projects

> Updated 2026-10-23 for fsbpyr 0.25.0. The first version (2026-10-19, commit c1aa348) was an
> outside evaluation of fsbpyr 0.20.0 plus a few commits, made from the repository's
> configuration and documentation; docs/improvement-plan.md §2 triaged it. This version keeps
> its criteria, its structure and its view of the other tools, re-checks their GitHub figures
> (2026-10-23), and revises everything it says about fsbpyr, judgements included where the
> evidence behind them changed. Each recommendation in the second part carries its status.
> Figures for fsbpyr come from this repository: README.md, CHANGELOG.md, docs/performance.md,
> docs/compatibility.md and the workflows.

[fsbpyr](https://github.com/devitops-com/fsbpyr) is a Python application bundler and
standalone runtime builder. It packages a Python project and its locked dependencies together
with CPython (3.12, 3.13, 3.14 or 3.15) into a single executable for Linux x86-64 or aarch64
with glibc 2.34 or newer, so the target machine needs no Python.

A fair comparison is with projects that solve the same deployment problem, not with tools that
audit Python applications or distribute Python packages. Three questions:

- **Features:** what can each tool package, run and deploy?
- **Test quality:** how thoroughly does the project verify correctness, edge cases and
  reproducible builds?
- **Maturity:** how much evidence is there of sustained maintenance, real-world adoption,
  release discipline and production reliability?

## 1. Executive verdict

**Most mature overall: PyInstaller.** The best-established general-purpose bundler, with broad
operating-system support, extensive package compatibility and a large test suite.
[pyinstaller/pyinstaller](https://github.com/pyinstaller/pyinstaller): 13.1k stars, about
10,200 commits, release 6.22.3 (2026-09-12).

**Best for Python environment packaging: PEX.** A mature choice for executable Python
environments, dependency locking and integration with build systems. Unlike fsbpyr, PEX
generally relies on a compatible Python on the target (its scie mode can carry one, which
docs/performance.md compares). [pex-tool/pex](https://github.com/pex-tool/pex): 4.2k stars,
about 2,100 commits, release 2.104.1 (2026-10-21).

**Most interesting for a self-contained Linux executable: fsbpyr.** Its differentiators: it
bundles CPython itself, so the target needs no Python; a glibc 2.34 baseline, tested on 11
distributions in CI; a check of the whole file's SHA-256 at every start; a self-hosting build
whose reproducibility an independent rebuild checks on every push to main; and attested
releases with an SBOM per executable. [devitops-com/fsbpyr](https://github.com/devitops-com/fsbpyr):
333 commits, 0 stars, repository created 2026-10-10.

Bottom line: PyInstaller wins on demonstrated maturity and breadth; PEX is a strong option for
Python-native deployment workflows; fsbpyr is a focused way to deliver one executable with its
own interpreter. These are different strengths, not a ranking of code quality. What changed
since the first version is the public evidence for fsbpyr's testing and release engineering,
not its maturity.

## 2. Feature comparison

A qualitative comparison of documented capabilities, not a claim that every dependency or
application has been tested.

| Capability | fsbpyr | PyInstaller | PEX | shiv | PyOxidizer |
| --- | --- | --- | --- | --- | --- |
| Single distributable artifact | Yes | Yes | Yes | Yes | Yes |
| Bundles Python runtime | Yes | Yes | Usually no | No | Yes |
| Runs without target Python installation | Yes | Yes | Generally no | No | Yes |
| Windows and macOS | No: Linux x86-64 and aarch64 with glibc (musl and macOS put aside) | Yes | Supported configurations | Yes, with caveats | Historically supported; verify current status |
| Linux glibc baseline explicitly targeted | Yes, 2.34, 11 distributions tested in CI | Platform-dependent | Platform-dependent | Platform-dependent | Configuration-dependent |
| Dependency locking / reproducible inputs | uv.lock, pylock.toml or a hash-pinned requirements file; the lock's and every distribution's hash recorded in the executable | Build/specification driven | Strong locking support | pip-based packaging | Configuration-driven |
| Native extensions | Supported; what the target must provide is reported, `FSBPYR_SELFTEST=natives` checks a target | Broad compatibility and hooks | Supported environments | Architecture-dependent | Supported, with integration work |
| Built-in executable integrity verification | Yes, SHA-256 of the whole file at every start | Not an equivalent core feature | Not an equivalent core feature | Not an equivalent core feature | Not an equivalent core feature |
| Self-hosting byte-reproducibility workflow | Yes: an independent rebuild compared byte for byte on every push to main | Not its defining feature | Reproducible-build tests exist | Not its defining feature | Not its defining feature |

Sources: [fsbpyr](https://github.com/devitops-com/fsbpyr),
[PyInstaller](https://github.com/pyinstaller/pyinstaller), [PEX](https://github.com/pex-tool/pex),
[shiv](https://github.com/linkedin/shiv), [PyOxidizer](https://github.com/indygreg/pyoxidizer).

Nuances:

- **fsbpyr:** a standalone executable carrying CPython (3.14 by default; 3.12, 3.13 or 3.15 by
  choice, one fsbpyr per Python version) and the application. It has controls for the entry
  point, extras, native libraries, source inclusion, compression, offline builds, the Python
  version and inspection, and an installer and `fsbpyr self update`. Packages that read files
  next to their `__file__` need `--fs-package` (`build --hints` lists likely ones). On 3.15 some
  widely used packages have no usable wheel yet (README, "Python 3.15").
- **PyInstaller:** much broader platform coverage and a mature ecosystem of hooks for packages
  such as NumPy and Qt. It collects an application and its dependencies with an interpreter,
  but a build generally runs on the target operating system.
- **PEX:** stronger when the goal is repeatable Python environments, dependency resolution, or
  packaging inside Pants and other build systems.
- **shiv:** a simple way to make a Python ZIP application; the destination needs a suitable
  Python, and native dependencies can limit portability. Latest release 1.0.8 (2024-11-01).
- **PyOxidizer:** architecturally similar in bundling Python into applications, but its latest
  release is 0.24.0 (2022-12-30) and its repository was last pushed in December 2024. Not a
  default for a new project without validating its maintenance and compatibility first.

## 3. Which has the best test suite?

The distinction that matters is between having tests and having evidence of reliability.

1. **PyInstaller: best evidence.** Dedicated unit and functional tests, extensive fixtures, a
   long history of compatibility work, tests of integrations with third-party libraries, and
   CI on several operating systems.
2. **PEX: strong.** A structured test runner, integration tests across Python versions, type
   checking, a named reproducible-build test, and a long commit and release history.
3. **fsbpyr: broad, and visible since 2026-10-20, but young.** The first version could verify
   only the configuration (pytest, Hypothesis, strict mypy, branch coverage) and a bootstrap
   whose successive stages match. Now:
   - about 2,140 tests per Python (2,139 passed on 3.14 for 0.25.0), unit, integration and
     end to end, most of the latter on real executables;
   - GitHub Actions runs all of them on every push to main, weekly and on request, for four
     Pythons on two architectures, and a release is built only from a commit whose run passed;
   - the executable against the original application: a corpus of 11 real applications
     (alembic, black, Flask with Jinja, gnureadline, lxml, pandas, Pillow, psycopg, a pytest
     plugin, scipy, setuptools' vendored libraries) and a compatibility app with 18 library
     checks, each run in the executable and in the project's venv and compared on exit code,
     stdout and stderr; a build that passes while the app fails counts as a failure;
   - clean machines: the built apps run on 11 distribution images without Python, from Rocky 9
     and Amazon Linux 2023 (glibc 2.34) to Arch (2.44);
   - adversarial: a fuzzer of the payload reader (30 s per run, 600 s weekly and whenever the
     launcher changed), the launcher under ASan and UBSan, damaged and truncated files,
     interrupted builds and concurrent first runs;
   - reproducibility: every build rebuilt on another runner, path, user and umask and compared
     byte for byte; the README's rebuild of a release gave 0.24.0's x86-64 executables byte for
     byte;
   - coverage gates (92.75% overall, 90% for the payload reader and the ELF code), strict mypy
     and Ruff.

   The record is short: 30 full-suite runs since 2026-10-20, of which 12 passed, 5 failed and
   13 were cancelled (2026-10-23).

What makes fsbpyr's approach interesting: property-based tests, branch coverage, a
self-hosting build checked by an independent rebuild, `fsbpyr inspect --verify`, static
analysis, and above all the differential tests against the venv and the clean-machine matrix,
which test the product (the executable) rather than the bundler's code.

None of this shows that fsbpyr has more tests, higher coverage, fewer bugs or better runtime
compatibility than its competitors; no comparable, independently measured coverage report
exists for all of them. PyInstaller keeps the strongest public evidence of long-running, broad
compatibility testing. fsbpyr's evidence is now broad and public, but days old.

## 4. Which project is the most mature?

Maturity means more than stars: release history, maintenance activity, compatibility with real
applications, contributors and release engineering.

| Maturity signal | Strongest candidate | Why |
| --- | --- | --- |
| Broad ecosystem and compatibility | PyInstaller | Large project, mature packaging workflow, many third-party package hooks |
| Dependency and build-system integration | PEX | Established tooling and integration with larger Python build systems |
| Minimal self-contained Linux deployment | fsbpyr | Focused architecture; reproducibility, portability and provenance checked in CI |
| Simple ZIP application distribution | shiv | Lightweight, established approach with a simpler scope |
| Embedding Python into applications | PyOxidizer historically | Ambitious architecture, but no release since 2022 |

PyInstaller has 13.1k stars and about 10,200 commits; PEX about 2,100 commits and a long release
history. fsbpyr has 333 commits and no stars; its repository is 13 days old, it has one
maintainer, 26 tags (v0.1.0 to v0.24.0) and GitHub releases from 0.21.0 on. These are snapshots,
not measures of correctness, but they show the difference in demonstrated community scale.

### A specific concern with fsbpyr: its support envelope

fsbpyr targets Linux x86-64 and aarch64 with glibc 2.34 or newer: RHEL 9 and its rebuilds,
Amazon Linux 2023, Ubuntu 22.04 and Debian 12 onwards. RHEL 8, Debian 11, Ubuntu 20.04 and older
systems are out since 0.24.0 (D42); there is no Windows, macOS or musl build. Apps run on
CPython 3.12 to 3.15, and fsbpyr itself runs on the same versions; on 3.15 the third-party wheels
are not complete yet. The README documents how native dependencies that cannot be built against
the baseline are handled.

That scope is not a weakness in itself: a small, focused project is easier to reason about than
a cross-platform bundler. Before relying on it in production, test the exact application and
dependency set to be shipped.

## 5. Recommendation by use case

- **Choose PyInstaller for the safest default:** desktop applications, Windows, macOS and Linux
  distribution, and projects with many third-party dependencies.
- **Choose PEX for reproducible Python environments:** backend services, CLI tools and build
  pipelines where environment management and integration with existing tooling matter more
  than embedding an interpreter.
- **Choose fsbpyr for controlled Linux infrastructure** (x86-64 or arm64, glibc 2.34 or newer):
  one executable, no system Python, a defined and tested glibc baseline, and verifiable build
  artifacts. Being smaller or newer is no reason to reject it.

## 6. What to require before adopting fsbpyr in production

A focused evaluation against PyInstaller and, where relevant, PEX. What fsbpyr now provides for
each point, and what stays the adopter's:

| Requirement | What fsbpyr provides | Still the adopter's |
| --- | --- | --- |
| Compatibility: C/C++ extensions, libraries loaded at run time, files reached through `__file__` or `importlib.resources` | The corpus and the compat app in CI; `build --hints` lists packages that may read next to `__file__`; a note at run time names the package and the fix | Building and running the real dependency set |
| Runtime independence on a clean Linux image | The 11 portability images have no Python and no build tools | The deployment image |
| Portability: the oldest supported glibc and the actual environment | Rocky 9 and Amazon Linux 2023 (glibc 2.34) are in the matrix; `FSBPYR_SELFTEST=natives` checks a target before deploying | Running the self-test on the targets |
| Reproducibility: two clean builds, differences explained | fsbpyr's own releases are rebuilt independently in CI; an app built from a wheel-only lock is byte-identical; distributions built from source are named as not reproducible elsewhere | Pinning the lock and the uv version |
| Integrity and provenance | The whole-file digest at every start; releases with provenance and SBOM attestations (`gh attestation verify`); the README shows how to attest an app | A signature with an outside key does not exist yet (D31) |
| Regression testing through the full functional suite | `--smoke-args` runs the executable once at build time | Running the app's own tests against the executable |
| Operational behaviour: start time, memory, size, cache, upgrades, rollback | docs/performance.md: a no-op start in 7.6 ms (bare CPython 5.4 ms), later runs 1.03 to 1.21× the venv's time (0.19.0), sizes, memory, the cache's collection; `fsbpyr self update`; a tag's own `install.sh` installs that version | Measuring the real app |
| Maintenance risk: issues, cadence, CI results, maintainer responsiveness | Public CI history since 2026-10-20; frequent releases | One maintainer, no community yet |

## Final scorecard

Qualitative assessments from the public evidence, not benchmark results.

| Category | Winner | Confidence |
| --- | --- | --- |
| Broadest feature coverage | PyInstaller | High |
| Most mature ecosystem | PyInstaller | High |
| Strong Python environment/build integration | PEX | High |
| Strongest visible compatibility-testing history | PyInstaller | High |
| Most interesting single-binary Linux approach | fsbpyr | High |
| Best overall production default | PyInstaller | High |
| Best choice for a specific application | Depends on the workload | Requires testing |

No winner changed since the first version. **Final verdict:** fsbpyr is a technically
thoughtful, specialized project whose release engineering (reproducible, attested builds) and
testing of the product itself (clean machines, differential tests against the venv) are now
public and run on every change. Its age and community still do not justify calling it more
mature than PyInstaller or PEX. PyInstaller remains the default for broad deployment; fsbpyr is
the one to evaluate when a self-contained Linux executable is a key requirement. The most
useful next step is a head-to-head test with a real application: which tool packages its
dependencies correctly, produces the smallest usable artifact, starts fastest and behaves
reliably in the target environment.

---

# How fsbpyr can compete with PyInstaller

Leaving age, community size and historical maturity aside, fsbpyr should not become a smaller
clone of PyInstaller, but the best tool for one job:

> Build a small, secure, reproducible, self-contained executable from a Python project, with
> minimal configuration and predictable behaviour on the target machine.

That positioning uses fsbpyr's architecture instead of reproducing every feature PyInstaller
has accumulated (one-folder and one-file bundling, dependency analysis, package hooks, Windows,
macOS and Linux). The status lines below are as of 0.25.0; the plan items they name are in
docs/improvement-plan.md.

## 1. The highest-value areas

### 1.1 Dependency completeness and correctness (P0)

The biggest competitive risk: a bundle that starts but fails when a rarely used code path
imports a plugin or loads a native library is not production-ready. PyInstaller's advantage is
its analysis engine plus package hooks that supply hidden imports, data files, binaries and
runtime initialization that static analysis cannot infer.

Recommended, with status:

- **Discover dependencies from project metadata and lockfiles.** Done: uv installs the lock into
  a fresh environment, and every installed file of every distribution is packaged.
- **Detect dynamic imports, plugin discovery, ctypes/cffi loading and native shared-library
  dependencies.** Done where it applies: because whole distributions are packaged, entry
  points, `pkgutil` and namespace packages work without analysis (a fixture tests plugins across
  distributions); the native closure is checked at build time, the system libraries the target
  must provide are reported, and `--hints` guesses libraries the code opens by name.
  Hidden-import discovery itself was rejected as not applicable to this design.
- **Actionable warnings for what cannot be resolved.** Done: the glibc and libstdc++ floors per
  native file, libraries neither bundled nor standard, distributions built from source,
  `--strict` to fail on warnings.
- **A versioned hook/plugin system.** Rejected (improvement plan §2): six built-in
  `fs-packages` and per-project settings cover what was needed.
- **Tested integrations for popular libraries.** Done: NumPy, pandas, Pydantic, cryptography,
  SQLAlchemy, psycopg, lxml, Pillow, scipy, click and typer, among others, in the compat app and
  the corpus.

Success criterion: a representative suite of real applications packages and runs without
workarounds, or explains what to configure. Now: of the 11 corpus apps, 9 match their venv with
no setting, Flask with Jinja needs `fs-packages`, and black fails (a mypyc-compiled package's
`__path__` is the cache directory; a known failure). In Phase 10 fsbpyr needed no options for
three apps where PyInstaller needed `--collect-data` and `--copy-metadata`.

### 1.2 Reproducible, verifiable builds (P0)

A potential differentiator, not just a checkbox: the same inputs, toolchain and declared
environment give the same artifact, with a documented policy for exceptions. PyInstaller also
documents reproducible-build considerations, so fsbpyr competes on completeness and usability.

- **A machine-readable build manifest with hashes.** Done: `build_info` records the lock and its
  hash, every distribution with its artifact's hash, the runtime's inputs and component versions,
  the uv that built it, and what the target must provide; fsbpyr's releases add a CycloneDX SBOM
  per executable.
- **No timestamps, archive-order or path dependence.** Done for fsbpyr's own bytes and wheel-only
  locks; distributions built from source are named as not reproducible elsewhere.
- **Offline builds from a verified cache.** Done: `--offline`; the cache is verified for hashed
  locks.
- **Provenance and signed attestations.** Done for fsbpyr's releases (provenance and SBOM
  attestations); the README shows how to attest an app. A signature with an outside key is
  planned after the MVP (D31).
- **Reproducibility across clean CI workers.** Done: an independent rebuild compared byte for
  byte on every push to main.

Success criterion: independent clean builds of the same pinned inputs yield identical artifacts.
Now: met for fsbpyr's own releases, on every push to main and for every Python and target.

### 1.3 Startup time, size and runtime efficiency (P0)

PyInstaller's one-file mode extracts its files to a temporary directory at every start; its
one-folder mode avoids that.

- **Direct startup without extraction.** Done: modules are imported from the file; only native
  extensions are extracted, once, into a cache shared by later runs.
- **No unused modules, test dependencies, documentation or development assets.** Development
  dependencies and groups are left out; pruning unused modules inside distributions was rejected,
  since it brings back the "starts but fails" risk. `fsbpyr inspect` shows where the bytes go,
  and `--no-source` drops the sources.
- **Optional compression, benchmarked.** Done: zstd by default, `--best-compression` (xz for
  native files) and `--fast-compression`, measured in docs/performance.md.
- **Share immutable runtime components.** Done for extracted native files, shared between runs
  of the same build.
- **Repeatable benchmarks for cold and warm start, memory, build time and size.** Done:
  docs/performance.md, including a cold page cache (0.20.0) and new start-time rows per release.

Success criterion: measurable advantages over PyInstaller `--onefile` and `--onedir`. Now: in
Phase 10 (0.19.0) fsbpyr's later runs took 1.03 to 1.21× the venv's time and PyInstaller
onefile's 4.6 to 18×; fsbpyr built fastest, while PyInstaller's files were smallest (13.2 MB
against 27.7 MB for a hello app). `--onedir` was not measured.

### 1.4 Native-library and filesystem compatibility (P1)

ELF dependencies, RPATH and RUNPATH, library search paths, libraries loaded at run time,
resources, certificates and package data, and which system libraries stay external.

Status: done. The build reports what the target must provide beyond glibc (with GLIBCXX, CXXABI,
GCC and ZLIB floors), `fsbpyr inspect` shows it again, `FSBPYR_SELFTEST=natives` checks a
target, and docs/compatibility.md lists what an executable needs from the machine (time zones,
terminfo, the CA store).

### 1.5 CI/CD and developer experience (P1)

The common path: `fsbpyr build`, `fsbpyr inspect dist/myapp`, and verification. Clear errors,
progress, JSON for CI, build caching, stable exit codes, an easy view of what went into the
executable.

Status: done, with differences: verification is `fsbpyr inspect --verify` (no separate `verify`
command), JSON is `inspect --json` (`build --json` deferred), `-v` shows each step, exit codes
are in the man page, and `--strict` fails a build on warnings.

## 2. The test suite as a competitive advantage

The key question is not "does fsbpyr's code work?" but "does the executable fsbpyr produces
behave like the original application?" The layers proposed, with status:

| Layer | Proposed | Status (0.25.0) |
| --- | --- | --- |
| 1. Unit and property-based | Archive parsing, dependency resolution, paths, ELF inspection, manifests, integrity, determinism | Done |
| 2. Package compatibility | Small apps with real dependencies, plugins, dynamic imports, native extensions | Done: compat app and corpus |
| 3. Clean-machine execution | Minimal containers without Python, compilers or headers | Done: 11 images, every Python and architecture, in CI |
| 4. Adversarial and failure | Corrupt and truncated files, path traversal, bad names, missing libraries, interrupted builds, concurrency, low disk space | Done in the main: fuzzer, sanitizers, edge cases, interrupted builds, concurrent first runs |
| 5. Differential and reproducibility | The same test cases against native Python, fsbpyr and PyInstaller | Done against native Python (the venv) and for reproducibility; no PyInstaller leg |

A compatibility dashboard per Python version, dependency versions, OS and architecture was
adapted rather than built: docs/compatibility.md, checked by tests against the code, lists what
is tested and what differs from a venv.

## 3. Features worth adding, and features not worth chasing yet

| Area | Priority | Recommendation | Status (0.25.0) |
| --- | --- | --- | --- |
| Automatic dependency discovery | Critical | Make it reliable for real applications | Done: the whole lock is packaged |
| Hook/plugin ecosystem | Critical | An extensible system with tested integrations | Rejected (improvement plan §2) |
| Reproducibility and provenance | Critical | An auditable workflow | Done |
| Native dependency handling | Critical | Report external system requirements | Done |
| Performance and artifact size | High | Benchmark against both PyInstaller modes | One-file measured (0.19.0); `--onedir` not |
| Diagnostics and inspection | High | Make packaging failures easy to understand | Done |
| Linux distribution compatibility | High | A measurable, dependable baseline | Done: glibc 2.34, 11 distributions in CI, a vendor-support policy (D42) |
| Cross-compilation | Medium | After the Linux experience is excellent | Not done: each fsbpyr builds for the platform it runs on; aarch64 builds on arm64 |
| Windows and macOS support | Strategic | Decide by intended customers | Windows dropped; macOS arm64 and musl put aside (owner, 2026-10-21) |
| GUI-specific features | Lower | When demand appears | Not pursued; tkinter is left out of the runtime |
| Every PyInstaller option | Low | Not without a use case | Not pursued |

On Windows and macOS, the two strategies were: own the Linux niche, or compete as a
general-purpose bundler. fsbpyr took the first: Linux x86-64 and aarch64 with glibc, with the
platforms it knows in one table (src/fsbpyr/targets.py), where a further target is added.

## 4. Product positioning

Not "PyInstaller, but newer" or "a tool that makes one executable", but outcomes users can
measure, each now backed by tests:

- **Predictable portability:** which Linux systems run the executable and which external
  libraries it needs, known before shipping (the build's report, the portability matrix).
- **Auditable artifacts:** every bundled distribution traced to a pinned input, the executable
  inspectable and its integrity verifiable (`build_info`, `inspect`, SBOMs, attestations).
- **Reproducible releases:** a rebuild from a clean environment gives the same bytes (the
  independent rebuild in CI, the README's rebuild).
- **No surprises at run time:** missing libraries and files read next to `__file__` reported
  at build time (the report, `--hints`, the corpus against the venv).

## 5. The 90-day roadmap, and where it stands

| Proposed | Status (0.25.0) |
| --- | --- |
| Days 1–30: a corpus of 20–30 representative applications | 11 apps chosen by risk class |
| Clean-environment execution tests | Done |
| Tests of dependency discovery, native libraries, dynamic imports, package resources | Done |
| A baseline of success rate, build time, start time and size against PyInstaller | Done in Phase 10 (0.19.0) |
| Days 31–60: better dependency detection and warnings | Done |
| A documented hook API | Rejected |
| An artifact manifest and detailed inspection | Done |
| Stronger deterministic-build and native-compatibility tests | Done |
| Days 61–90: repeatable performance and reproducibility benchmarks | Done (the Phase 10 driver is not committed) |
| Release provenance and artifact verification | Done |
| Supported package and OS combinations documented | Done: README, docs/compatibility.md |
| A compatibility matrix and known limitations | Done: docs/compatibility.md, the README's "Python 3.15" |

## 6. How to measure whether fsbpyr is winning

Measurable acceptance criteria rather than a weighted feature checklist:

| Metric | Proposed target | Now (0.25.0) |
| --- | --- | --- |
| Successful builds on the supported corpus | At least 95% at first, trending upward | All 11 build; 10 of 11 match their venv |
| False-success builds (build passes, application fails) | Zero in the supported regression suite | Zero: the corpus test fails on any |
| Reproducibility | Identical outputs for identical pinned inputs | Met for fsbpyr's releases; wheel-only app locks |
| External dependencies | Explicitly reported and validated | Reported at build time; checked on a target by the self-test |
| Packaging configuration | No manual step for common supported applications | 1 of 11 corpus apps needs a setting (`fs-packages`) |
| Regression testing | Every fixed packaging bug becomes a permanent test | CONTRIBUTING.md requires it |
| Performance | Measurements against PyInstaller `--onefile` and `--onedir` | `--onefile` (0.19.0); `--onedir` not |

A 95% success rate is not enough for critical production workloads; the threshold should be
higher for the supported application class. The most telling metric is the share of
applications that work on the first build without hooks or hidden-import declarations: 9 of 11
corpus apps.

## Final recommendation

The order of investment proposed, with status:

1. Dependency discovery and runtime correctness: done, by packaging the whole lock.
2. A real-world application compatibility suite: done (11 apps, the compat app), smaller than
   proposed.
3. Reproducibility, provenance and artifact verification: done.
4. Native-library portability and diagnostics: done.
5. Demonstrable start-time and size advantages: start time against PyInstaller onefile shown;
   size is not an advantage (PyInstaller's files are smaller); `--onedir` not measured.
6. Broader platform support once the core is compelling: aarch64 added; musl and macOS put
   aside, Windows dropped.

The aim stays the same: turn fsbpyr's focused architecture into measurable benefits rather than
reproducing PyInstaller's feature surface. Packaging the applications users care about,
explaining its output and demonstrating reproducible, portable artifacts is how it competes on
engineering quality before it has comparable maturity or community size.
