# `fsbpyr` vs. similar GitHub projects

> **Note, 2026-10-23.** This evaluation was added on 2026-10-19 (commit c1aa348) and describes
> fsbpyr as it was then: 0.20.0 plus a few unreleased commits. It is kept as written, with its
> opinions, scores and descriptions of the other tools. The statements about fsbpyr below are not
> updated; bracketed "[since ...]" markers flag facts that are no longer true. Since then (up to
> 0.25.0; see CHANGELOG.md, README.md and docs/improvement-plan.md, which triages this
> document):
>
> - Targets: Linux aarch64 with glibc besides x86-64 (0.21.0). Windows and macOS are still not
>   supported; musl and macOS arm64 are planned and put aside.
> - Pythons: apps can run CPython 3.12 and 3.13 as well as 3.14 (0.21.0), and 3.15 (0.25.0);
>   3.14 stays the default. fsbpyr itself runs on 3.12 to 3.15.
> - glibc baseline: 2.34, not 2.28; the launcher links against a Debian 12 sysroot (0.24.0).
> - Getting it: `install.sh` (0.21.0) and `fsbpyr self update` (0.21.0). Each release has one
>   executable per Python and target, with its `.xz`, `SHA256SUMS`, licence notices and a
>   CycloneDX SBOM (0.22.0). The executables and SBOMs are attested by GitHub Actions.
> - Reproducibility: every build is rebuilt independently (another runner, path, user and umask)
>   and compared byte for byte, in the release workflow from 0.21.0 and in the full suite on
>   every push to main since 0.24.0, when a release came to build only a commit that passed.
> - Tests in CI: the full suite on every push to main and weekly, per target and Python, and the
>   portability matrix on clean distribution images (0.21.0); a release needs both to have passed.
>   A corpus of 11 real applications is built and compared with their venvs (0.21.0).
> - Build manifest and diagnostics: `build_info` records the lock, every distribution's hash and
>   the runtime's inputs; `inspect` shows the build options, where the bytes go and what the
>   target must provide beyond glibc, and `--licenses` prints the notices (0.21.0);
>   `FSBPYR_SELFTEST=natives` checks a target for missing libraries (0.22.0);
>   docs/compatibility.md lists how an executable differs from a venv (0.21.0).
> - Not done: a hook/plugin API, hidden-import discovery, module pruning and a compatibility
>   dashboard service were rejected in docs/improvement-plan.md.

The first important distinction: [`devitops-com/fsbpyr`](<https://github.com/devitops-com/fsbpyr?utm_source=chatgpt.com>) is a Python application bundler and standalone runtime builder, not an AWS security scanner. It packages a Python project and its locked dependencies together with CPython into a single executable for Linux x86-64 [since 0.21.0: also aarch64], so the target machine doesn't need Python installed.

devitops-com/fsbpyr · GitHub

For a fair comparison, I'd evaluate it against projects that solve the same deployment problem, rather than tools that merely audit Python applications or distribute Python packages.

My comparison focuses on three questions:

- Features: What can each tool package, run, and deploy?
- Test quality: How thoroughly does the project verify correctness, edge cases, and reproducible builds?
- Maturity: How much evidence is there of sustained maintenance, real-world adoption, release discipline, and production reliability?

## 1. Executive verdict

GitHub - pyinstaller/pyinstaller: Freeze (package) Python programs into stand-alone executables · GitHub

Most mature overall: PyInstaller

Best-established general-purpose bundler, with broad operating-system support, extensive package compatibility, and a large test suite.

[GitHub repository](<https://github.com/pyinstaller/pyinstaller?utm_source=chatgpt.com>) · 13k+ stars

GitHub

PEX with included Python interpreter - Pex Docs (v2.98.4)

Best for Python environment packaging: PEX

A mature choice for executable Python environments, dependency locking, and integration with build systems. Unlike fsbpyr, PEX generally relies on a compatible Python interpreter.

[GitHub repository](<https://github.com/pex-tool/pex?utm_source=chatgpt.com>) · 4k+ stars

GitHub

GitHub - Nuitka/Nuitka-standalone-backend: Enhanced CPython for usage in standalone deployments

Most interesting for a self-contained Linux executable: `fsbpyr`

Its differentiators are bundling CPython itself, avoiding a target-side Python installation, a glibc 2.28 [since 0.24.0: 2.34] compatibility target, executable integrity verification, and a reproducible self-hosting build process.

[GitHub repository](<https://github.com/devitops-com/fsbpyr?utm_source=chatgpt.com>) · 82 commits and 0 stars shown on the repository page at review time.

devitops-com/fsbpyr · GitHub

+1

My bottom line: PyInstaller wins on demonstrated maturity and breadth; PEX is a strong option for Python-native deployment workflows; fsbpyr has a more focused approach to delivering a single executable with its own interpreter. Those are different strengths, not a universal ranking of code quality.

## 2. Feature comparison

The following is a qualitative comparison of the documented capabilities, not a claim that every possible dependency or application has been tested.

| Capability | fsbpyr | PyInstaller | PEX | shiv | PyOxidizer |
| --- | --- | --- | --- | --- | --- |
| Single distributable artifact | Yes | Yes | Yes | Yes | Yes |
| Bundles Python runtime | Yes | Yes | Usually no | No | Yes |
| Runs without target Python installation | Yes | Yes | Generally no | No | Yes |
| Windows and macOS | No, Linux x86-64 target [since 0.21.0: also aarch64] | Yes | Supported configurations | Yes, with caveats | Historically supported; verify current status |
| Linux glibc baseline explicitly targeted | Yes, 2.28 [since 0.24.0: 2.34] | Platform-dependent | Platform-dependent | Platform-dependent | Configuration-dependent |
| Dependency locking / reproducible inputs | Locked project dependencies | Build/specification driven | Strong locking support | pip-based packaging | Configuration-driven |
| Native extensions | Supported, with constraints | Broad compatibility and hooks | Supported environments | Architecture-dependent | Supported, with integration work |
| Built-in executable integrity verification | Yes, SHA-256 | Not an equivalent core feature | Not an equivalent core feature | Not an equivalent core feature | Not an equivalent core feature |
| Self-hosting byte-reproducibility workflow | Yes, documented | Not its defining feature | Reproducible-build tests exist | Not its defining feature | Not its defining feature |

Sources: [fsbpyr documentation](<https://github.com/devitops-com/fsbpyr?utm_source=chatgpt.com>), [PyInstaller](<https://github.com/pyinstaller/pyinstaller?utm_source=chatgpt.com>), [PEX](<https://github.com/pex-tool/pex?utm_source=chatgpt.com>), [shiv](<https://github.com/linkedin/shiv?utm_source=chatgpt.com>), and [PyOxidizer](<https://github.com/indygreg/pyoxidizer?utm_source=chatgpt.com>).

devitops-com/fsbpyr · GitHub

+5

A few important nuances:

- fsbpyr: The most distinctive deployment model here is a standalone executable carrying CPython 3.14 [since 0.21.0: or 3.12, 3.13; since 0.25.0: 3.15] and the application. It has controls for entry points, extras, native libraries, source inclusion, compression, offline builds, and inspection. Native extensions and packages that expect files on disk require special attention.
  devitops-com/fsbpyr · GitHub
  +1
- PyInstaller: Much broader platform coverage and a mature ecosystem of hooks for packages such as NumPy and Qt. It collects an application and its dependencies with an interpreter, but the build generally needs to be performed on the target operating system.
  GitHub
- PEX: Stronger when the objective is to create repeatable Python environments, manage dependency resolution, or integrate packaging into Pants and other build systems.
  GitHub
- shiv: A relatively straightforward way to create a Python ZIP application. Its trade-off is that the destination needs a suitable Python interpreter, and native dependencies can constrain portability.
  GitHub
- PyOxidizer: Architecturally similar in its ambition to bundle Python into applications, but its latest listed PyOxidizer release is 0.24.0 from December 2022. I would not select it as the default for a new project without first validating its current maintenance and compatibility.
  GitHub
  +1

## 3\. Which has the best test suite?

This is the most important distinction between having tests and having evidence of reliability.

1\. PyInstaller

Best evidence

Its repository has dedicated unit and functional tests, extensive test fixtures, and a long history of compatibility work. Its documented test strategy includes testing integrations with third-party libraries, and its CI targets multiple operating systems.

GitHub

+1

2\. PEX

Strong

PEX documents a structured test-running system, integration tests across Python versions, type checking, and a specifically named reproducible-build test. Its repository also has a substantial history of commits and releases.

GitHub

+1

3\. fsbpyr

Promising design

The repository includes `pytest`, `pytest-cov`, Hypothesis property-based testing, strict mypy checking, branch-coverage configuration, and dedicated test fixtures. Its self-hosting build also checks executable integrity and verifies that successive build stages produce byte-identical artifacts.

GitHub

+1

4\. shiv

It has a test directory and a documented `tox` workflow. However, the available evidence doesn't establish that its tests are as broad as PyInstaller's or PEX's for the particular risks involved in packaging runtimes and native dependencies.

GitHub

### Why fsbpyr's testing approach is interesting

The project has some good engineering choices that deserve credit:

- Property-based testing: Hypothesis can explore edge cases that conventional hand-written examples might miss.
- Branch coverage: The project configures branch-aware coverage measurement, which is more informative than line coverage alone.
- Reproducible self-hosting: Its bootstrap process builds the tool through multiple stages and checks that the final stages match byte for byte.
- Artifact verification: `fsbpyr inspect --verify` recomputes the executable's digest.
- Static analysis: Strict mypy and Ruff checks provide additional feedback before release.
  GitHub
  +1

These are meaningful quality signals. But they do not prove that fsbpyr has a higher test count, greater coverage percentage, fewer bugs, or better runtime compatibility than its competitors.

I could verify the repository's testing configuration and documented build checks, but not a comparable, independently measured coverage report or successful CI history for every project. I therefore wouldn't claim an objectively measured winner for test quality beyond PyInstaller having the strongest publicly visible evidence of long-running, broad compatibility testing.

## 4\. Which project is the most mature?

For maturity, I weigh more than GitHub stars. I look at release history, maintenance activity, compatibility with real applications, contributor ecosystem, and the quality of release engineering.

| Maturity signal | Strongest candidate | Why |
| --- | --- | --- |
| Broad ecosystem and compatibility | PyInstaller | Large project, mature packaging workflow, many third-party package hooks |
| Dependency and build-system integration | PEX | Established tooling and integration with larger Python build systems |
| Minimal self-contained Linux deployment | fsbpyr | Focused architecture and unusually explicit reproducibility checks |
| Simple ZIP application distribution | shiv | Lightweight, established approach with a simpler scope |
| Embedding Python into applications | PyOxidizer historically | Ambitious architecture, but older release history is a concern |

PyInstaller reports more than 13,000 stars and roughly 10,000 commits on its repository page. PEX reports over 2,000 commits and a substantial release history. fsbpyr reports 82 commits and no stars on the page I reviewed. These numbers are snapshots, not measures of correctness, but they do illustrate the difference in demonstrated community scale.

GitHub

+3

### A specific concern with fsbpyr: its support envelope

The project targets Linux x86-64 [since 0.21.0: and aarch64] with glibc 2.28 [since 0.24.0: 2.34] or newer and requires Python 3.14 [since 0.21.0: 3.12 to 3.14; since 0.25.0: to 3.15] for its own development package in the current `pyproject.toml`. Its README documents how native dependencies that cannot be built against its intended baseline may require special handling.

devitops-com/fsbpyr · GitHub

+1

That narrow scope is not inherently a weakness. A small, focused project can be easier to reason about than a cross-platform bundler. However, before relying on it in production, I'd test the exact application and dependency set that you intend to ship.

## 5\. My recommendation by use case

Python exe 파일 만들기: PyInstaller 사용법 완벽 정리 (배포까지 한 번에)

Choose PyInstaller if you want the safest default.

Especially appropriate for desktop applications, Windows/macOS/Linux distribution, and Python projects with many third-party dependencies. Its ecosystem and compatibility history make it the best general-purpose choice.

GitHub - pex-tool/pex: A tool for generating .pex (Python EXecutable) files, lock files and venvs.

Choose PEX if you care about reproducible Python environments.

Best suited to backend services, CLI tools, and build pipelines where Python environment management and integration with existing tooling matter more than embedding an interpreter into a native executable.

5 Tools to Enhance Your AppImage Experience on Linux

Choose fsbpyr if your target is controlled Linux infrastructure.

It is worth a serious proof of concept when you want one executable, no system Python dependency, a defined glibc compatibility baseline, and verifiable build artifacts. I would not reject it simply because it is smaller or newer.

## 6\. What I'd require before adopting fsbpyr in production

I would run a focused evaluation against PyInstaller and, where relevant, PEX.

- [ ]Compatibility: Test your real dependencies, especially C/C++ extensions, libraries that load shared objects dynamically, and packages that access files through `__file__` or `importlib.resources`.
- [ ]Runtime independence: Run on a clean Linux image with no system Python or build tools.
- [ ]Portability: Test on the oldest supported glibc distribution and on the actual deployment environment.
- [ ]Reproducibility: Build twice from clean environments and investigate any byte differences.
- [ ]Integrity and provenance: Verify release attestations, executable digests, and the build supply chain. A self-contained executable's embedded digest alone is not proof of who produced it.
- [ ]Regression testing: Run the packaged application through its full functional test suite, not merely a successful startup.
- [ ]Operational behavior: Measure cold-start time, memory usage, executable size, cache extraction, upgrades, and rollback behavior.
- [ ]Maintenance risk: Review open issues, release cadence, CI results, and the responsiveness of maintainers.

## Final scorecard

These are my qualitative assessments from the public repository evidence, not benchmark results.

| Category | Winner | Confidence |
| --- | --- | --- |
| Broadest feature coverage | PyInstaller | High |
| Most mature ecosystem | PyInstaller | High |
| Strong Python environment/build integration | PEX | High |
| Strongest visible compatibility-testing history | PyInstaller | High |
| Most interesting single-binary Linux approach | fsbpyr | High |
| Best overall production default | PyInstaller | High |
| Best choice for your specific application | Depends on workload | Requires testing |

Final verdict: `fsbpyr` looks like a technically thoughtful, specialized project with interesting release-engineering and integrity features. However, the public evidence doesn't yet justify calling it more mature or better tested than PyInstaller or PEX. PyInstaller is my default recommendation for broad deployment; fsbpyr is the one I'd evaluate if its self-contained Linux executable model is a key requirement.

The most useful next step would be a head-to-head test using one of your actual Python applications. That would tell you much more than stars or repository size: which tool packages your dependencies correctly, produces the smallest usable artifact, starts fastest, and behaves reliably in your target environment.


# How fsbpyr can compete with PyInstaller

If we remove age, community size, and historical maturity from the evaluation, I would not try to make fsbpyr a smaller clone of PyInstaller.

I'd make it the best tool for a specific, increasingly important job:

> Build a small, secure, reproducible, self-contained executable from a Python project, with minimal configuration and predictable behavior on the target machine.

That positioning takes advantage of fsbpyr's architecture rather than trying to reproduce every feature PyInstaller has accumulated. PyInstaller already offers one-folder and one-file bundling, automatic dependency analysis, package-specific hooks, and support for Windows, macOS, and Linux.

PyInstaller 6.22.3 documentation

+2

Here is how I'd prioritize the work.

## 1. The highest-value areas

## 1. Dependency completeness and correctness

P0

The biggest competitive risk

A bundle that starts successfully but fails when a rarely used code path imports a plugin or loads a native library is not production-ready.

PyInstaller's major advantage is its analysis engine plus a broad ecosystem of package-specific hooks. Hooks explicitly supply hidden imports, data files, binaries, and runtime initialization that static analysis cannot reliably infer.

PyInstaller 6.22.3 documentation

+2

What fsbpyr should do:

- Automatically discover dependencies from project metadata and lockfiles.
- Detect dynamic imports, plugin discovery, `ctypes`/`cffi` library loading, and native shared-library dependencies where possible.
- Produce actionable warnings for dependencies it cannot resolve confidently.
- Support a first-class, versioned hook/plugin system.
- Ship tested integrations for popular libraries: NumPy, pandas, Pydantic, cryptography, SQL drivers, and common CLI frameworks.

Success criterion: a representative suite of real-world applications packages and runs without manual workarounds, or gives a precise explanation of what must be configured.

This is the most important area because users will forgive a slightly larger binary; they won't forgive a bundle that silently omits a dependency.

## 2. Reproducible, verifiable builds

P0

This is a potential differentiator, not just a checkbox.

Build reproducibility should mean that the same inputs, toolchain, and declared environment produce the same artifact, with a documented policy for any exceptions. PyInstaller itself documents reproducible-build considerations, so fsbpyr should compete on the completeness and usability of its implementation rather than claiming reproducibility as a feature unique to it.

PyInstaller 6.22.3 documentation

What fsbpyr should do:

- Produce a machine-readable build manifest with hashes of source files, dependencies, interpreter, native libraries, and build tools.
- Eliminate timestamps, nondeterministic archive ordering, and environment-dependent paths from output.
- Offer an offline build mode from a verified dependency cache.
- Publish provenance and optional signed attestations for release artifacts.
- Test reproducibility across clean CI workers, not merely successive stages of one build.

Success criterion: independent clean builds of the same pinned inputs yield identical artifacts, or a machine-readable report explains every difference.

This is particularly attractive for security-conscious organizations, regulated deployments, and software supply-chain controls.

## 3. Startup time, size, and runtime efficiency

P0

This is where a single-executable design can deliver tangible value.

PyInstaller's one-file mode extracts bundled files to a temporary directory before launching the application, which adds startup overhead. Its one-folder mode avoids that extraction step.

PyInstaller 6.22.3 documentation

What fsbpyr should optimize:

- Direct startup without unnecessary extraction, when its architecture permits it.
- Avoid bundling unused modules, test dependencies, documentation, and development assets.
- Make compression optional and benchmark the trade-off between size and decompression time.
- Cache or share immutable runtime components where this is safe and worthwhile.
- Publish repeatable benchmarks for cold start, warm start, memory, build time, and final artifact size.

Success criterion: measurable advantages over both PyInstaller `--onefile` and `--onedir` on representative workloads, with the methodology and trade-offs documented.

Don't optimize for the smallest executable alone. A 20% smaller artifact that starts three times slower is not necessarily a win.

## 4. Native-library and filesystem compatibility

P1

Python packaging often fails at the boundary between Python and the operating system.

fsbpyr should systematically handle ELF dependencies, `RPATH`/`RUNPATH`, shared-library search paths, dynamically loaded libraries, resource files, certificates, and package data. It should also document which system libraries intentionally remain external.

Success criterion: a dependency report that tells users exactly which libraries are bundled, which remain external, and what the target system must provide.

This matters especially for database clients, scientific Python, cryptography, and applications that load plugins or external executables.

## 5. CI/CD and developer experience

P1

Make the common path extremely simple:

bash
```
fsbpyr build
fsbpyr inspect dist/myapp
fsbpyr verify dist/myapp
```

Provide clear errors, progress reporting, JSON output for CI, build caching, stable exit codes, and an easy way to inspect what went into the executable.

Success criterion: a new user can package a typical supported project from a clean checkout without reading implementation details or debugging opaque linker errors.

## 2. The test suite should become a competitive advantage

I would invest in a packaging compatibility lab, not merely increasing unit-test coverage.

The key question is not "does fsbpyr's code work?" It is "does the executable produced by fsbpyr behave like the original application?"

PyInstaller's hook ecosystem illustrates why this distinction matters: packages have special requirements that must be tested through actual frozen applications, not just by unit-testing the bundler itself.

GitHub

### The test architecture I'd build

Layer 1 — Unit and property-based tests

Archive parsing, dependency resolution, path normalization, ELF inspection, manifest generation, integrity verification, and deterministic output.

Layer 2 — Package compatibility tests

Small applications exercising real dependencies: NumPy, pandas, Pillow, cryptography, SQLAlchemy, Pydantic, plugin discovery, dynamic imports, and native extensions.

Layer 3 — Clean-machine execution tests

Run produced artifacts in minimal containers or virtual machines without Python, compilers, development headers, or the build environment.

Layer 4 — Adversarial and failure tests

Corrupted artifacts, truncated archives, path traversal, malicious filenames, missing libraries, interrupted builds, concurrent execution, and low disk space.

Layer 5 — Differential and reproducibility tests

Run identical application test cases against native Python, fsbpyr, and PyInstaller. Compare observable behavior, exit codes, output, resource access, and failure modes.

I'd publish the results as a compatibility dashboard, with every test identifying its Python version, dependency versions, target OS, architecture, and outcome.

That would give users evidence of compatibility rather than relying on marketing claims or a single coverage percentage.

## 3. Features worth adding — and features not worth chasing yet

| Area | Priority | Recommendation |
| --- | --- | --- |
| Automatic dependency discovery | Critical | Make it reliable for real applications |
| Hook/plugin ecosystem | Critical | Build an extensible system with tested integrations |
| Reproducibility and provenance | Critical | Turn build integrity into an auditable workflow |
| Native dependency handling | Critical | Explicitly report external system requirements |
| Performance and artifact size | High | Benchmark against both PyInstaller modes |
| Excellent diagnostics and inspection | High | Make packaging failures easy to understand |
| Linux distribution compatibility | High | Make the supported baseline measurable and dependable |
| Cross-compilation | Medium | Investigate after the Linux experience is excellent |
| Windows and macOS support | Strategic | Decide based on the intended customer base |
| GUI-specific features | Lower initially | Add when there is evidence of demand |
| Every PyInstaller command-line option | Low | Don't replicate features without a compelling use case |

### Should fsbpyr support Windows and macOS?

This is the biggest strategic decision.

PyInstaller's cross-platform coverage is a major advantage, although it requires builds on the corresponding target operating system rather than acting as a universal cross-compiler.

PyInstaller 6.22.3 documentation

+1

fsbpyr has two possible strategies:

- Own the Linux niche: deliver exceptionally portable, reproducible, secure Linux executables with outstanding native-library handling. This is the more achievable near-term strategy.
- Compete as a general-purpose bundler: add Windows and macOS backends, equivalent runtime integration, platform-specific dependency handling, signing, and extensive compatibility testing. This expands the addressable market but also expands the engineering burden considerably.

I would not start with three operating systems. I'd first make the Linux implementation demonstrably excellent, then design the architecture so additional targets do not require rewriting the dependency-resolution and packaging layers.

## 4. The product positioning I would use

I would avoid positioning fsbpyr as "PyInstaller, but newer" or simply "a tool that makes one executable."

Instead, emphasize outcomes that users can measure:

Predictable portability

Know which Linux systems can run the executable and which external libraries it needs before shipping it.

Auditable artifacts

Trace every bundled dependency back to a pinned input, inspect the final executable, and verify its integrity.

Reproducible releases

Rebuild from a clean environment and independently verify that the artifact is identical.

No surprises at runtime

Detect missing imports, shared libraries, and resources during the build, rather than after deployment.

These are product promises. They become competitive advantages only when backed by tests, benchmarks, and clear diagnostics.

## 5\. A practical 90-day roadmap

Assuming a small team or a primary maintainer, this is the order I'd follow.

Days 1–30### Establish correctness

- Create a benchmark corpus of 20–30 representative Python applications.
- Build clean-environment execution tests.
- Test dependency discovery, native libraries, dynamic imports, and package resources.
- Establish baseline success rate, build time, startup time, and artifact size against PyInstaller.

Days 31–60### Make failures diagnosable

- Improve automatic dependency detection and actionable warnings.
- Add a documented hook API and prioritize the most common unsupported packages.
- Publish an artifact manifest and detailed inspection output.
- Strengthen deterministic-build tests and native-library compatibility checks.

Days 61–90### Demonstrate superiority

- Publish repeatable performance and reproducibility benchmarks.
- Add release provenance and artifact verification.
- Document supported package and operating-system combinations.
- Release a compatibility matrix and clearly state known limitations.

## 6\. How I would measure whether fsbpyr is winning

The most useful comparison is not a weighted score made from subjective feature checklists. It is a set of measurable acceptance criteria.

| Metric | Proposed target |
| --- | --- |
| Successful builds on the supported compatibility corpus | At least 95% initially, trending upward |
| False-success builds (build passes, application fails) | Zero in the supported regression suite |
| Reproducibility | Identical outputs for identical pinned inputs |
| External dependencies | Explicitly reported and validated |
| Packaging configuration | No manual intervention for common supported applications |
| Regression testing | Every fixed packaging bug becomes a permanent test |
| Performance | Publish measurements against PyInstaller `--onefile` and `--onedir` |

These are proposed engineering targets, not measurements of the current project. A 95% success rate is also not sufficient for critical production workloads; the required threshold should be higher for the specific supported application class.

One especially important metric: the percentage of applications that work correctly on the first build without manual hooks or hidden-import declarations. It measures how much work the tool saves users, which is one of the clearest ways to compete with PyInstaller.

## My final recommendation

If I were prioritizing fsbpyr's development, I would invest in this order:

1. Dependency discovery and runtime correctness.
2. A real-world application compatibility test suite.
3. Reproducibility, provenance, and artifact verification.
4. Native-library portability and excellent diagnostics.
5. Demonstrable startup-time and artifact-size advantages.
6. Broader platform support, once the core product is compelling.

The key is to turn fsbpyr's focused architecture into measurable benefits, rather than spending its early development effort reproducing PyInstaller's entire feature surface.

If it can reliably package the applications that users care about, explain its output, and demonstrate reproducible and portable artifacts, it can be competitive on engineering quality well before it has comparable maturity or community size.

