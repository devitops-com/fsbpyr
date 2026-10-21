# fsbpyr improvement plan

2026-10-16, revised 2026-10-19 against v0.20.0

**Summary.** fsbpyr's design holds up under review; its weak points are evidence and process. A
release is gated by 1,042 of the suite's 1,424 items, and the 13-distribution portability matrix
and the natives, compat, sanitizer and fuzz tests run only on the maintainer's machine. CI runs
only on tags and pull requests while changes reach main as local merges, so a change is first
tested by its release tag. Green builds can fail at run time (built apps lack `exit()`, `quit()`
and `help`), the README's verification recipe returns 404, a failed smoke test overwrites the
previous tool at `-o`, and the runtime every app embeds is two PBS releases behind with nothing
watching upstream. The plan schedules 44 items in three stages. Stage 0 (13 items, all S, about
2-3 weeks) fixes what users and verifiers hit today and runs CI on every push to main. Stage 1
(17 items, about 8 weeks) gates releases on the whole suite, compares executables with their
venv, reports what the target must provide beyond glibc, rebuilds every tag on an independent
worker, and readies an LLVM 23 toolchain before a security fix needs it. Stage 2 (14 items,
about 4 weeks) adds a traceable build manifest, licence notices, app provenance and the
low-severity remainder. Three more items sit below a cut line until demand appears. Python
versions, platforms, self-update and the installer belong to Phase 9 of
docs/IMPLEMENTATION_PLAN.md, signed releases (D31) come after the MVP on their own, and the
comparison with other tools is done (Phase 10); this plan schedules none of them. Stage 0 comes
before Phase 9, and Stages 1-2 run alongside it (owner, 2026-10-19); Stage 0 was done the same
day. §5 records the owner's decisions: all 13 were taken by 2026-10-20. eval.md's hook API,
hidden-import discovery, module pruning and dashboard service are rejected as inapplicable to
this design or as over-engineering.

**How this was made.** Separate agents assessed fsbpyr independently in 8 areas (dependencies,
reproducibility, native libraries, tests, performance, developer experience, security,
maturity), and other agents verified each finding adversarially, 20 agents in all; this plan
uses the verified severities and drops refuted findings. eval.md was fact-checked claim by
claim, with the verifiers' disputes overriding the first verdicts. Three competing plans
(correctness first, trust first, adoption first) were merged, and three critics reviewed the
merge for feasibility, owner decisions and completeness. On 2026-10-19, 11 agents checked every
item, fact and triage row again against v0.20.0 and the owner's 2026-10-18 changes to
docs/IMPLEMENTATION_PLAN.md, and 11 more tried to refute each of their 158 findings (110
confirmed, 44 partly confirmed, 4 refuted). The owner's record overrules this plan: what
contradicts it was dropped, what was done since 2026-10-16 was removed, and the rest was
corrected. "Review notes" lists both rounds.
Baseline on the dev host (2026-10-19, HEAD): tests/unit + tests/integration with branch coverage
gave 978 passed in 23 s, 93% total (5,259 statements, 1,658 branches), lowest build.py 63%.
Without the pinned PBS in the cache, 6 integration tests skip and runtime/stdlib.py falls from
91% to 57%. e2e is not included, and the C launcher has no coverage measurement.

Gap ids are written `dimension/gap-id` (from the verified assessment), and eval.md lines are
written `eval:N`. Effort: S = under a day, M = 1-3 days, L = 1-2 weeks, XL = more than 2 weeks,
for one maintainer working with an AI assistant.

## Stage overview

| ID | Title | Effort | Stage |
|---|---|---|---|
| CI-8 | Run CI on every push to main, and tag only a green commit | S | 0 |
| REL-1 | Fix the release-verification recipe, make releases and tags immutable, and stop re-pushing tags | S | 0 |
| REL-2 | Freeze the release artifact before the tests run, and compare it with stage 3 | S | 0 |
| BLD-1 | Keep a failed build from replacing a working output | S | 0 |
| RT-1 | Restore exit(), quit() and help in built apps | S | 0 |
| NAT-1 | Actually rebuild the project's own extensions against the glibc 2.28 sysroot | S | 0 |
| DX-4 | Put a working quickstart and the prerequisites first in the README | S | 0 |
| DOC-3 | Correct the README's overclaims and stale numbers | S | 0 |
| SCOPE-1 | State the 3.14-only target, and cap fsbpyr's own requires-python | S | 0 |
| SEC-1 | Publish a security policy and the embedded-runtime CVE response | S | 0 |
| SEC-2 | Move the runtime to PBS 20261009 now, and watch upstream daily | S | 0 |
| CI-1 | Turn unexpected skips into failures in CI, and run the D37 contract test there | S | 0 |
| CI-2 | Measure and enforce coverage in CI | S | 0 |
| CI-4 | Put the full test environment in the repo | M | 1 |
| CI-3 | Run the whole suite on main and nightly as a normal user, and gate releases on it | L | 1 |
| CI-6 | Run the 13-distribution portability matrix on the runner host | M | 1 |
| TST-1 | Make the compatibility checks fail on stderr noise | S | 1 |
| TST-2 | Compare every fixture command with the same project run from its venv | M | 1 |
| BLD-2 | Let the smoke test run the app once (opt-in) | S | 1 |
| BLD-3 | Add --strict so CI can fail on warnings, including unhashed requirements | S | 1 |
| NAT-2 | Report what the target must provide beyond glibc, and check the GLIBCXX/CXXABI/GCC floors | M | 1 |
| RT-2 | Explain single-file-layout failures at run time (D24 amendment), and widen the build-time scan | M | 1 |
| RT-4 | Resolve sys.path entries that point inside the executable | M | 1 |
| DX-1 | Show what the build is doing | S | 1 |
| SEC-3 | Refuse privileged starts, check credentials in the D22/D23 parent check, and write the threat model | M | 1 |
| SEC-4 | Prepare the LLVM 23 toolchain before a security fix forces it | M | 1 |
| REP-1 | Pin and record the release toolchain | M | 1 |
| REP-2 | Prove reproducibility on an independent worker for every main push and tag | M | 1 |
| DOC-1 | Document how an executable differs from a venv, what the target must provide, and what is tested | M | 1 |
| TST-3 | Build a small real-world corpus checked against each app's venv | L | 1 |
| REP-3 | Make build_info a versioned manifest traceable to its inputs | M | 2 |
| REP-4 | Ship third-party licence notices with releases and apps | M | 2 |
| REP-6 | Give app builders a provenance recipe, and print the hash attestations use | S | 2 |
| NAT-3 | Check every native in the payload, not only the reached ones, and list system libraries loaded at run time | M | 2 |
| RT-3 | Keep helper executables executable (D27 amendment) | S | 2 |
| RT-5 | Map distutils to setuptools' copy, and warn about pkg_resources | S | 2 |
| RT-6 | Warn instead of silently dropping what the venv would have | S | 2 |
| PERF-1 | Guard the startup properties with counts in CI | S | 2 |
| PERF-2 | Measure cold starts and the integrity check's cost per GB | M | 2 |
| DX-2 | Fail early, completely and politely | S | 2 |
| DX-3 | Document exit codes, environment and configuration in the man page | S | 2 |
| DX-5 | Make inspect show the build options and where the bytes go | S | 2 |
| CI-5 | Add test timeouts and a hardening test of the built stub | S | 2 |
| DOC-2 | Bring the specs back in line with the code, and propose a stability policy | S | 2 |
| NAT-4 | Let users check a target machine before they deploy | M | below cut |
| REP-7 | Publish a CycloneDX SBOM for fsbpyr's release | M | below cut |
| CI-7 | Close the remaining low test gaps | M | below cut |

## Facts checked for this plan (2026-10-19, read-only)

- **PBS 20261009 is the last release whose bitcode clang 22 can link.**
  - Release 20261009 has `cpython-3.14.8+20261009-...-pgo+lto-full.tar.zst` and 3.15.0. Its
    notes say "CPython 3.15.0rc3 -> CPython 3.15.0, libffi 3.4.8 -> 3.8.0, expat 2.8.5 ->
    2.9.0". The pinned 20261003 already ships 3.14.8, so for 3.14 apps the move changes libffi
    and expat.
  - PYTHON.json's `object_file_format` is `llvm-bitcode:22.1.3` for 3.14.8+20261009 and
    `llvm-bitcode:23.1.3` for 3.14.8+20261013.
  - 20261013 is still the latest release. It adds 3.16.0a1 and ships the same 3.14.8 and 3.15.0,
    so a newer PBS release brings no newer CPython 3.14 than 20261009 does today.
  - Source: `gh release view 20261009|20261013 --repo astral-sh/python-build-standalone`; the object_file_format values from python/PYTHON.json in the downloaded 3.14.8 pgo+lto-full archives.
- **Debian has clang 23 only in sid.**
  - llvm-toolchain-23 1:23.1.3-1 is in sid only. trixie has llvm-toolchain-22
    1:22.1.8-1~deb13u4, and ~deb13u5 is in trixie-proposed-updates (sources.debian.org API).
  - The v0.19.0 and v0.20.0 stubs' `.comment` records "Debian clang version 22.1.8 (1~deb13u4)",
    so an unpinned from-source rebuild stops matching once u5 lands.
  - apt.llvm.org's llvm-toolchain-trixie-23 holds one rolling snapshot build
    (1:23.1.4~++20261018... on 2026-10-18, already replaced by ~++20261019... on 2026-10-19) that each rebuild replaces, so it cannot be pinned by version.
    snapshot.debian.org keeps sid's clang-23 1:23.1.3-1.
- **Repository settings today:**
  - `gh api repos/devitops-com/fsbpyr/immutable-releases` returns `{"enabled":false,...}`;
  - main has no protection (404), and there are no rulesets;
  - private vulnerability reporting is disabled;
  - the community profile's health is 37%.
- **CI runs only on tags and pull requests.** release.yml triggers on `push: tags: ["v*"]` and
  `pull_request`. No pull request has run since v0.19.0: changes reach main as local merges, so
  f5fa03a was first tested by v0.20.0's tag run (39081258610).
- **release.sh tells the maintainer to re-push tags.** tools/release/release.sh:298-299: "drop
  the tag ... then tag it again and push the tag".
- **Tags and releases.** Local and origin hold the same 22 `v*` tags. 0.15.2, 0.15.3, 0.16.0,
  0.17.0, 0.17.1 and 0.18.0 have no tag, locally or on origin (with no tag reflog, whether the last four ever had a local one cannot be shown); the release commits of the last four (80a1f1b,
  86b2645, 299355e, 21ac25d) are on main and contain no release.yml. The v0.18.1 and v0.18.2
  releases were deleted; v0.19.0 and v0.20.0 are published.
- **A release commit changes only version files.** c3850b0 (v0.19.0) and 7064cb7 (v0.20.0)
  touch only CHANGELOG.md, pyproject.toml, src/fsbpyr/__init__.py and uv.lock.
- **v0.20.0 embeds the same runtime as v0.19.0** (`cp314-x86_64-unknown-linux-gnu-a86b4c0360b8`;
  pins.py still has 20261003).
- **The build-system range excludes the uv that releases use.** pyproject.toml:15 requires
  `uv_build>=0.12.17,<0.13.0`; the v0.19.0 and v0.20.0 tag runs used uv 0.13.1 and logged that
  it is outside the range.
- **The only CI run of the full suite failed the portability tests.** Run 38624936426 (commit
  55641fc, a privileged debian:trixie container, as `tester`, with nested rootless podman 5.4.2)
  collected 1,405 items, showed `tests/e2e/test_portability.py FFFFFFFFFFFFF....` and was
  cancelled at 19:52:52, after about 18 minutes and at 29%. It printed no failure summary, so the
  cause was not captured.
- **The GitHub ubuntu-24.04 runner image lists Podman 4.9.3** (actions/runner-images
  Ubuntu2404-Readme.md, image 20261012).
- **The startup budget was raised to 1.5x by the owner.** docs/performance.md:37-40 records that
  the owner raised it twice on 2026-10-09, and tests/e2e/test_runtime_app.py:299 enforces 1.5x.
  D37, §3.3 and the Phase 7 regression guards in §8 of docs/IMPLEMENTATION_PLAN.md still say
  1.2x.
- **Suite size.** HEAD collects 1,424 items (446 in tests/e2e; 735 test functions, 133 in e2e).
  test-ci selects 1,042 of them; v0.20.0's run passed 1,014 and skipped 28. The skips: 17 "no C
  compiler", 2 groff and 1 strace (20 that installing gcc, groff and strace removes); 5 that need
  a non-root user; 3 for unshare and podman.
- **CI wall time.** The two passing pull-request runs with test-ci took 7m07s and 7m10s (38631560096, 38633140782). The v0.19.0 tag run took 5m46s and the
  v0.20.0 tag run 7m09s, with an unchanged workflow.
- **The launcher's C code and the fuzz harness are unchanged since v0.15.0**
  (`git diff --stat v0.15.0 v0.20.0 -- src/fsbpyr/launcher tests/fuzz` is empty).

---

## 1. Verdict

fsbpyr is technically strong for a project that went from its first commit on 2026-10-06 to
v0.20.0 on 2026-10-18 with one maintainer:
- Output is deterministic, and the build bootstraps itself in 3 stages; stage 2 and stage 3
  must be byte-identical (tools/bootstrap.sh:124).
- Every start checks the whole file, and the native cache is hardened.
- Multiprocessing and re-exec children are accepted only from a parent that runs the same
  executable (same device and inode; credentials are not compared, SEC-3).
- The ELF resolver follows ld.so's rules for `$ORIGIN` paths (NAT-3 covers the rest).
- There are 735 test functions (1,424 collected items), and the unit and integration tests
  reach 93% branch coverage.
- Releases are attested with Sigstore.
- Phase 10 measured it against PyInstaller onefile, pex and PyApp: fsbpyr matched the venv's
  output with no options where PyInstaller needed two, and its later runs take 1.03-1.21x the
  venv's time against PyInstaller onefile's 4.6-18x (docs/performance.md, "Other tools").

Installing the whole lock (D5/D26) also avoids PyInstaller's main failure mode, hidden imports.

Its weakness is evidence and process, not design:
- **What gates a release is a subset.** CI runs 1,042 of 1,424 items, 28 of them skipped; the
  skips are listed but do not fail the run. The natives, compat, 13-distro portability,
  sanitizer and fuzz tests run only on the maintainer's machine. The one attempt to run them in
  CI failed the portability matrix 13 of 13.
- **A change is first tested by its release tag.** CI runs on tags and pull requests, and
  changes reach main as local merges.
- **Green builds can still fail at run time.** The smoke test only imports the entry module.
  Built apps lack `exit()`/`quit()`/`help`, `__file__`-relative data fails with a bare
  NotADirectoryError, and several other venv-vs-executable differences behave the same way.
  Nothing compares an executable with the venv it came from.
- **Users and verifiers hit broken or untrue docs.**
  - The README's verification recipe returns 404, and it is circular for the launcher.
  - The README says the target needs nothing beyond glibc 2.28, which numpy and onnxruntime
    contradict (both need libstdc++ and libgcc_s).
  - A failed smoke test overwrites the previous tool at `-o`.
- **The release record is fragile.** Two releases were deleted, six versions have no
  tag, tags are mutable, and release.sh advises re-pushing tags.
- **Every app's security is tied to fsbpyr's runtime pin.** Each app statically embeds the
  CPython, OpenSSL and expat of the fsbpyr that built it. The pin is two PBS releases behind
  (v0.20.0 kept v0.19.0's runtime), and nobody watches upstream. The next upstream fix needs an
  LLVM 23 toolchain, which Debian ships only in sid.

eval.md gets the direction right: compatibility evidence, an auditable manifest, an
external-library report, diagnostics, and Linux first. But it was written from configuration
files, not code:
- About half of its "what to build" list already exists.
- Of its P0 #1, hidden-import discovery and a hook ecosystem do not apply to this design,
  because the whole lock is packaged; its native-library, warning and tested-integration parts
  are adopted (§2). Phase 10 bears this out: fsbpyr needed no options where PyInstaller needed
  `--collect-data` and `--copy-metadata`.
- Pruning unused modules (eval:303) would bring back the false-success risk it warns about
  (eval:266).
- It misses four things:
  - the 3.14-only limit for built apps (eval:164 frames it as a development-package detail);
  - the embedded-runtime CVE path;
  - the broken verification recipe;
  - the CI subset.

---

## 2. eval.md triage

| eval.md recommendation (lines) | Status in fsbpyr today | Decision | Reason / plan items |
|---|---|---|---|
| Discover dependencies from metadata and lockfiles (258, 381) | Done: uv installs the lock into a fresh venv and every RECORD file is packaged (build.py:248-249) | already done | none |
| Detect dynamic imports, plugin discovery, hidden imports (259, 447, 481 #1) | Not applicable by design: importlib entry points, pkgutil and namespaces work end to end in a real executable. What remains: sys.path subdirectories, `distutils`, legacy `pkg_resources` discovery, and the stdlib modules the runtime leaves out (runtime/stdlib.py:21-24), which fail only at run time | adapt | RT-4, RT-5, TST-3 (plugins fixture), DOC-1 (the left-out stdlib) |
| Versioned hook/plugin system and hook API (261, 382, 448) | 6 built-in fs-packages (classify.py:46-47) plus per-project `fs-packages` / `allow-system-libs` / `interpreter-reexec` | reject | Over-engineering for one 6-name table: in Phase 10 fsbpyr needed no options where PyInstaller needed two (performance.md:469-476). REP-3 records the rules a build applied; DOC-3 tests the README list |
| ctypes/cffi and native deps; actionable warnings; "precise explanation" (259-260, 264, 428-430) | The DT_NEEDED closure is strong. Unreached natives, system libraries opened at run time and GLIBCXX/CXXABI floors are unchecked; runtime failures are unexplained | adopt | NAT-2, NAT-3, RT-2, RT-6, BLD-3 |
| Tested integrations; a corpus of 20-30 apps (262, 357-359, 440) | 6 synthetic fixtures. No pandas; Pillow only through matplotlib's savefig; no plugins across distributions; no database driver (the compat fixture's SQLAlchemy runs on sqlite) | adapt | 8-11 apps chosen by risk class, one of them a database client with bundled native libraries, psycopg[binary] (TST-3) |
| A compatibility lab: does the executable behave like the original application? (343-347) | Real executables are tested, but against hard-coded outputs | adopt | TST-2, TST-3 |
| Machine-readable build manifest with hashes (280, 420-422, 449) | Partial. Per-file RECORD hashes are inside every executable. build_info records the project, fsbpyr, uv, the lock kind, dists as name==version, glibc and git (build.py:280-291), plus the runtime id, tag and Python (assemble.py:121-129), but no hash of the lock or of any dist | adapt | REP-3 |
| Reproducibility and provenance as an auditable workflow (383) | Partially done: one-job determinism, attested releases, no independent rebuild | adopt | REL-1, REP-1, REP-2, REP-3, REP-6 |
| Eliminate timestamps, archive ordering, environment paths (281) | Mostly done: fsbpyr's bytes and wheel-only locks (D15/D16). sdist-built natives are not normalized; nothing sets SOURCE_DATE_EPOCH | adopt the cheap part | REP-3 (SOURCE_DATE_EPOCH, backend recorded); DOC-1 (build constraints documented); prefix maps deferred (§4) |
| Offline build from a verified cache (282) | `--offline` exists (cli.py:90-91) and CI tests it end to end (tests/e2e/test_build.py:213). The cache is verified only for hashed locks | already done | Air-gap note in DOC-1 |
| Provenance and signed attestations (200, 283, 455) | fsbpyr releases carry a SLSA v1 attestation. Apps get nothing. The verify command pins neither workflow nor ref (README:20, :103) | adapt | REL-1, REP-6. A signature checked with an outside key stays D31's post-MVP owner item (§4) |
| Reproducibility across clean, independent workers (199, 284, 286, 424-426, 467) | stage2 == stage3 inside one job only; uv and the apt toolchain are not pinned | adopt | REP-1, REP-2 |
| Startup without extraction, sharing immutable parts, optional benchmarked compression, not size alone (302, 304-305, 310) | Done (M4, D7/D10, D29/D30/D33) | already done | none |
| Leave out test and development dependencies (303) | Done: staging's `uv sync` passes `--no-dev --no-default-groups` (staging.py:119; D5) | already done | none |
| Avoid bundling unused modules, and dependencies' own tests and docs (303) | The whole lock is packaged by design (Phase 4 owner decision) | reject pruning; measure | Pruning brings back "starts but fails". DX-5 shows where the bytes go, `heavy` included, which Phase 10 measured at 77.1 MB against PyInstaller onefile's 51.0 MB (performance.md:488) |
| Repeatable cold/warm/memory/build/size benchmarks (202, 306, 454) | docs/performance.md measures warm and first runs, peak RSS, size and build time, Phase 10 included. The Phase 10 runs have no committed script. Cold page-cache starts were never measured (performance.md:465-466) | adapt | PERF-2 (cold-start and integrity tables, each with a "Reproduce:" line); a committed Phase 10 driver is deferred (§4) |
| Beat PyInstaller --onefile and --onedir; a baseline in days 1-30 (308, 385, 443, 471, 485) | Phase 10 compared PyInstaller onefile, pex and PyApp: fsbpyr's later runs take 1.03-1.21× the venv's time, PyInstaller onefile's 4.6-18× (performance.md:543-548). No `--onedir`, no cold page cache | done: Phase 10 (owner, 2026-10-18) | Cold page cache: PERF-2. `--onedir` is not in this plan (§4) |
| ELF, RPATH and system libraries; report what is bundled, what stays external, what the target must provide (318-322, 384, 416-418, 468) | Checked at build time, never reported. README:4-7 says the executable needs nothing "but Linux x86-64 with glibc 2.28 or newer". L322 names database clients, scientific Python and apps that load plugins or external executables | adopt | DOC-3, NAT-2, NAT-3, RT-3, TST-3's database client (NAT-4 below the cut line) |
| `build` / `inspect` / `verify`, clear errors, build caching (330-337) | Done (`inspect --verify`, `--json`, one-line errors) | already done | No separate `verify` command (§4) |
| Progress, JSON output for CI, stable exit codes (337) | `build()` logs each step (build.py:220-295), but nothing shows progress while uv stages; no strict mode; exit codes undocumented | adapt | DX-1, BLD-3, DX-3; `build --json` deferred |
| A new user packages a typical project without opaque linker errors (339) | Mostly done for wheel-only locks. sdist compile/link errors reach the user as the last 3000 characters of uv's stderr (staging.py:29); a failed C++ rebuild keeps the host's build with a warning that quotes only the error's last line (build.py:144-147); "Building an app" starts at README:123 | adopt | DX-4, DX-1 (step-named errors, `-v`), NAT-2 (GLIBCXX warning), DOC-1 (manylinux_2_28 advice) |
| Excellent diagnostics and inspection (386) | Partially done | adopt | RT-2, DX-1, BLD-3, DX-5 |
| Layer 1: unit and property tests (353-355) | Strong unit layer; Hypothesis in 2 tests only (tests/unit/payload/test_validation.py:359, :380) | already done (minor gaps) | CI-7 (below the cut line) |
| Layer 3: clean-machine tests (197-198, 361-363, 387, 441) | Partially done: 13 images, glibc 2.28-2.44 (tests/e2e/test_portability.py:29-43), but CI never runs them, no release is gated on them, and the containers keep the host network | adopt | CI-3, CI-6 (`--network=none`) |
| Layer 4: adversarial tests (365-367) | Fuzzer, ASan/UBSan and about 80 edge cases exist, local only. Interrupted builds and full disks are untested | adapt | CI-3, BLD-1, CI-7 |
| Layer 5: differential tests vs native Python and PyInstaller (201, 369-371) | None; expected outputs are hard-coded. Phase 10 compared PyInstaller onefile's output with the venv's once, for three apps (performance.md:469-476) | adopt the native-Python half | TST-2, TST-3. No PyInstaller leg (§4) |
| Test dependency discovery, native libraries, dynamic imports and package resources (442) | Done locally: e2e tests cover each (test_imports.py, test_natives.py, test_compat.py), but CI runs only test_build.py and test_runtime_app.py of e2e (Makefile:79-81), and plugins across distributions are untested | done locally | CI-3 runs them in CI; TST-3 adds plugins across distributions |
| Strengthen deterministic-build and native-compatibility tests (450) | Partially done | adopt | REL-2, REP-2, NAT-1, NAT-2 |
| Compatibility dashboard; matrix and known limitations (373, 456-457) | Nothing user-facing | adapt | A test-checked docs/compatibility.md (DOC-1, TST-3), not a dashboard service |
| Cross-compilation (388) | One fixed target triple (registry.py:98-101); a real question once Phase 9 adds targets | defer to Phase 9's platform step (its third part) | Phase 9 plans arm64 CI runners; cross-building is a question for that step's spike (§4) |
| Windows/macOS; "own the Linux niche"; new targets without rewriting the packaging layers (389, 393-408) | Linux x86-64 only. The native analysis and the host triple are x86-64 ELF only (elf.py:52-53, natives.py:26-32, registry.py:98-101) | decided: Phase 9's platform step (owner, 2026-10-18) | Linux x86-64 and aarch64, each with glibc and musl, and macOS arm64 come after the MVP; Windows is dropped (IMPLEMENTATION_PLAN.md Phase 9, §11). eval:408's per-target layers are that step's spike (Mach-O, musl's loader rules) |
| GUI features (390); not every PyInstaller option (391) | tkinter/idlelib are left out of the runtime; Qt/wx wheels are untested but not blocked; small CLI surface | reject / already done | DOC-1 states the GUI position |
| Positioning statement (228-231, 410-432) | Partly true. Phase 10 published startup, size and build figures against other tools (performance.md:449) | adopt once backed | SCOPE-1 and DOC-1 state the scope. The four promises (416-430) rest on NAT-2, REP-3, REP-2 and TST-2/TST-3, in that order |
| Success metrics (463-475) | Partly measurable. Phase 10 measured PyInstaller onefile (471). Packaging fixes get regression tests (gnureadline and mypyc in tests/unit/builder/), as CLAUDE.md:53 asks, but no contributor document says so (470) | adopt | §6. SEC-1's CONTRIBUTING.md states eval:470's rule: each packaging fix adds a fixture or corpus test. `--onedir` (471): §4 |
| Checklist: compatibility (196) | Fixtures only | adopt | TST-2, TST-3, RT-2 |
| Checklist: runtime independence on a clean image (197) | 13 images, local only | adopt | CI-6 |
| Checklist: oldest glibc and the actual deployment environment (198) | Oldest glibc tested locally; nothing checks a deployment target | adopt | CI-6, NAT-1, NAT-2 (NAT-4 below the cut line) |
| Checklist: build twice from clean environments (199) | One job only | adopt | REP-1, REP-2 |
| Checklist: integrity and provenance (200) | Release attestation; the recipe downloads v0.18.2, which returns 404 (README:101) | adopt | REL-1, REP-6 |
| Checklist: full functional tests, not only startup (201) | Smoke test imports the entry only | adopt | TST-2, BLD-2 |
| Checklist: cold start, memory, size, cache, upgrades (202) | Warm and first runs, peak RSS, size and the cache on disk are measured (Phase 10 included); cold page cache, upgrades and rollback never | adapt | PERF-2 (upgrades deferred, §4) |
| Checklist: maintenance risk (203) | One maintainer, no SECURITY.md, community health 37%. CI runs on tags and pull requests only (release.yml:12-15), and main is pushed without pull requests | adopt | SEC-1, REL-1, CI-8, CI-3, DOC-2 |
| Priorities (478-486) | 1 runtime correctness → RT-1, RT-2, RT-4, BLD-1, BLD-2; 2 app corpus → TST-2, TST-3; 3 reproducibility and provenance → REL-1, REP-1, REP-2, REP-3, REP-6; 4 native portability and diagnostics → NAT-2, DX-1, CI-6; 5 startup and size advantages → Phase 10 (done) and PERF-2; 6 broader platforms → Phase 9 (owner, 2026-10-18) | adopt the order | §3 follows 1-4 loosely: release-trust fixes are pulled into Stage 0 because they are cheap, and REP-3 and REP-6 (priority 3) wait for Stage 2, after NAT-2, DX-1 and CI-6. Stage 0 comes before Phase 9, and Stages 1-2 run alongside it (§5 #13) |
| "Requires Python 3.14 for its own development package" (164) | Wrong framing: built apps are 3.14-only (build.py:39) | correct it | SCOPE-1 states the target; 3.12-3.14 come in Phase 9's second part, one fsbpyr per version (owner, 2026-10-18) |

---

## 3. The plan

**Ordering principle.** The plan runs in three stages, then a cut line:
1. **Stage 0, weeks 1-3: what users and verifiers hit today.** Broken docs, misleading claims,
   release-gate holes, CI that first runs on a release tag, a build that destroys a working
   file, and a stale runtime pin.
2. **Stage 1, weeks 4-11: stop false-success builds and make the evidence real.** CI gating,
   stderr and differential checks, the target-requirements report, the corpus, the independent
   rebuild and the LLVM 23 toolchain.
3. **Stage 2, weeks 12-15: provenance, reproducibility, and the low-severity remainder.**
4. **Below the cut line: not scheduled.** These items close medium or low gaps at a cost their
   evidence does not justify today. Each is specified, so it can start when a user asks or an
   incident shows the need.

Stages 0-2 come to about 15 weeks of work for one maintainer.

**Where the evidence changes the order:**
- **SEC-2 (PBS bump and upstream watch) is in Stage 0.** Its verified severity is high and it is
  cheap. 20261009 is the last PBS release that today's clang 22 can take.
- **CI-8 (CI on every push to main) is in Stage 0.** Without it, every other Stage 0 change is
  first tested by a release tag, and under REL-1's burned-version rule a failure there costs a
  version.
- **NAT-1 is in Stage 0.** The build log claims a sysroot rebuild that never happens.
- **REP-1, REP-2 and SEC-4 are in Stage 1, not Stage 2.** REP-2 and SEC-4 close high gaps
  (repro/no-independent-clean-rebuild, security/embedded-runtime-cve-latency part 2), and REP-2
  needs REP-1. REP-1 depends only on Stage 0 items, REP-2 on REP-1 and REL-2, and SEC-4 on
  SEC-2 and CI-4.
  SECURITY.md's response windows (SEC-1) mean little until SEC-4 is ready.
- **SEC-3 (AT_SECURE and parent credentials) is in Stage 1, not Stage 0.** No normal use hits it.

**Relation to Phase 9.** docs/IMPLEMENTATION_PLAN.md owns what comes after the MVP: Phase 9
(self-update first, then Python 3.12-3.14 with one fsbpyr per version, then Linux aarch64 and
x86-64 with glibc and musl, and macOS arm64) and signed releases (D31). This plan does not
schedule any of it; where an item touches it, the item follows the record:
- CI-3, CI-6 and REP-2 are written as matrices keyed by Python version, with one entry (3.14)
  today, so Phase 9's Python-version part adds entries instead of new jobs.
- REL-1's immutable releases keep each release's assets equal to the SHA-256s its install.sh
  carries (IMPLEMENTATION_PLAN.md:1558-1559), and keep the release that self-update fetches from
  `releases/latest` from changing under it. Self-update itself relies on `inspect --verify`,
  which detects damage only; it gains tampering detection with D31's signature, after the MVP
  (:1467-1468).
- DX-4's download lines give way to install.sh when it ships.
- install.sh's `FSBPYR_VERIFY=1` attestation check (IMPLEMENTATION_PLAN.md:1566-1567) uses
  REL-1's `--signer-workflow` and `--source-ref` pins, as the README's checks do.
- DOC-2's stability statement covers what self-update relies on (§5 #8).
- The order (owner, 2026-10-19; §5 #13): Stage 0 comes before Phase 9 starts. Stages 1-2 run
  alongside Phase 9, and CI-3, CI-6 and REP-2 land before Phase 9's Python-version part
  multiplies the executables. Stages 1-2 then share the maintainer's time with Phase 9, so
  their calendar time is longer than their weeks of work.

Facts this review verified that Phase 9's spikes can use (none of them is scheduled here):
- Porting to 3.12/3.13: `ast.parse` with `feature_version` (3, 12) or (3, 13) fails on 4 of
  src/fsbpyr's files, all unparenthesized `except A, B:` clauses (boot/_fsbpyr_rt/gc.py:324,
  boot/_fsbpyr_rt/mp.py:36, scan.py:80, vcs.py:30). build.py:39 `TARGET_PYTHON` is the literal
  "3.14", and registry.py `select_runtime` reuses the builder's own runtime only when the
  version matches.
- Platforms: the x86-64 assumptions sit in elf.py (`EM_X86_64`, :20 and :52-53), natives.py (the
  loader soname in `SYSTEM_LIBRARIES`, :26-27), runtime/sysroot.py (`_MULTIARCH`, :23),
  runtime/registry.py (`host_triple`, :99-101) and payload/spec.py (`_XZ_FILTERS`, :261-262);
  the spin pause in launcher/parallel.c:32-36 already compiles to nothing off x86. Python 3.14's
  `lzma` has no ARM64 BCJ filter (filter id 0x0A is rejected), so D33's x86 filter has no
  aarch64 counterpart in the build. Decoding is not affected: the same `lzma` reads `xz --arm64`
  files (checked 2026-10-19 with uv's PBS CPython 3.14.8), so self-update and the dispatch can
  still unpack an aarch64 release's `.xz` in-process (IMPLEMENTATION_PLAN.md:1450-1451,
  :1494-1496). PBS's optimized musl builds are `lto-full` and `lto+static-full`, never PGO, in
  20261003 and 20261009.
- macOS: besides the launcher calls Phase 9 lists (IMPLEMENTATION_PLAN.md:1540-1541), Python
  code reads /proc. D22/D23's parent check stats `/proc/<ppid>/exe`
  (boot/_fsbpyr_rt/mp.py:109-110; SEC-3 adds `/proc/<ppid>/status`), multiprocessing children
  start from `/proc/self/exe` (boot/_fsbpyr_rt/fixups.py:24), GC reads `/proc/self/uid_map`
  (boot/_fsbpyr_rt/gc.py:321), and self-hosting reads its runtime through `/proc/self/exe`
  (runtime/registry.py:247).
- Self-update runs `inspect --verify` on the new file in-process, and payload/reader.py rejects
  format versions outside `READABLE_VERSIONS` (format.py:14). A later format_version bump would
  make older releases refuse to update unless they can still check the new file (§5 #8).
- 3.15, for when `fsbpyr` moves to it (IMPLEMENTATION_PLAN.md:1502-1503): PBS 20261009's 3.15.0
  objects are LLVM 22.1.3 bitcode. In the 2026-10-19 review, bytecode.py with only its version
  guard changed round-tripped all 634 non-test stdlib modules under 3.15.0 (marshal version 6),
  the importlib names the boot code uses exist in 3.15, and `lazy import` parses. 3.15.x builds
  from later PBS releases need SEC-4's clang 23.
- Phase 9 does not say whether an x86-64 host may build apps for other targets. Doing so needs
  the target's runtime and has to skip or emulate the smoke test.

## 3.0 Stage 0 — weeks 1-3: fix what users and verifiers hit today

Done on 2026-10-19: all 13 items are merged into main (120afc2 to a47bdd8), and the repository
settings of §5 #2 are switched on.

### CI-8 Run CI on every push to main, and tag only a green commit
- Why: release.yml runs only for `v*` tags and pull requests (release.yml:12-15). The owner merges locally and pushes main; the last PR, #13, was merged on 2026-10-14. So a change is first tested by its release's tag run. f5fa03a (src/fsbpyr/cli.py, merged in 2bb6cdb) reached CI only in v0.20.0's tag run 39081258610, and no run happened between that run and v0.19.0's (38636574900). Under REL-1's burned-version rule, each failure there costs a version. eval:203.
- Change:
  - release.yml adds `push: branches: [main]`. `publish` stays tag-only through its existing `if:` (:129). A main push run builds, smoke-tests, runs `make check` and `make test-ci`, and uploads the executable, as a PR run does. The concurrency group (:19-21) already cancels a superseded main run.
  - tools/release/release.sh, in its guards after the origin/main check, refuses to continue unless `gh run list --workflow release.yml --branch main --event push --commit "$LOCAL_HEAD" --status success` finds a run. The message says to wait for that run (`gh run watch`) or to fix main.
  - tests/integration/test_release.py: when the fake gh knows no successful run, release.sh exits 1 with nothing committed or pushed.
  - The release commit's own main push run repeats its tag run's build; that costs runner minutes only.
  - docs/IMPLEMENTATION_PLAN.md's two statements that CI runs on every pull request and tag (:498, :1612) add pushes to main.
  - CI-3 later adds full-suite.yml to the check; the release.yml main push run stays required.
- Done when:
  - The push that adds the trigger starts a release.yml run on main, with `publish` skipped.
  - release.sh refuses a HEAD that has no green main push run, and the test covers it.
- Effort: S. Depends on: none. Owner decision: none.

### REL-1 Fix the release-verification recipe, make releases and tags immutable, and stop re-pushing tags
- Why: three problems. eval:199-200, 203.
  - README "Verifying a release" downloads v0.18.2 (README.md:101, 104), which returns HTTP 404 (the v0.18.1 and v0.18.2 releases were deleted).
  - Its self-rebuild cannot vouch for the stub and stdlib, which a self-hosted build copies from itself (registry.py `from_executable`, D4).
  - Releases, tags and main are unprotected, and release.sh:298-299 advises re-pushing a tag. install.sh (Phase 9) carries every asset's SHA-256 (IMPLEMENTATION_PLAN.md:1558-1559), and self-update fetches `releases/latest`, so a published release must never change.
  - Gaps: dx/readme-verify-release-404, maturity/readme-verify-recipe-404, repro/readme-verification-overstated, maturity/release-immutability-and-retention (missed) (medium); maturity/tag-release-hygiene, repro/attestation-verify-unpinned-repo-unprotected, tests/main-unprotected-no-schedule (protection part) (low).
- Change:
  - README "Verifying a release":
    - Name no version: download from `releases/latest/download/fsbpyr` and use `tag="v$(./fsbpyr --version | cut -d- -f1)"`.
    - Name the prerequisites (curl, git, gh, uv), and set uv with `uv self update <version>` to the version that `fsbpyr inspect --json fsbpyr` records (`.uv`).
    - Describe two levels:
      - (a) the self-rebuild proves that the payload (fsbpyr's code and dependencies) matches the tag;
      - (b) only an independent from-source rebuild proves the stub, CPython and the stdlib. Until REP-2 lands, the README says "(b) is not yet checked in CI".
    - Correct "Only that pairing" (README.md:109-115). CI's release is `make build`'s output, so on a clean checkout of the tag, `make bootstrap` and `make build` give the released bytes with the release's uv and clang. Both targets update uv to the newest (tools/bootstrap.sh `uv_present`), so until REP-1's `FSBPYR_UV_VERSION` exists the README tells readers to run `fsbpyr build` directly.
    - Add one sentence on retention: every release from 0.19.0 on stays published, and a failed version is burned, not replaced.
  - `gh attestation verify`:
    - in the verification block and the release.yml comment (:153), add `--signer-workflow devitops-com/fsbpyr/.github/workflows/release.yml --source-ref "refs/tags/$tag"`;
    - in "Getting fsbpyr" (README.md:20), which sets no tag, add `--signer-workflow` only;
    - call the check recommended, not "# optional".
  - The Integrity section (README.md:279-281) keeps "signed releases are planned", reworded to "planned after the MVP" (IMPLEMENTATION_PLAN.md:1726), and names the attestation check as what shows a release's origin until then.
  - The release.yml publish step also uploads `SHA256SUMS` (`sha256sum fsbpyr fsbpyr.xz`).
  - tools/release/release.sh:294-303:
    - replace "drop the tag ... tag it again" with "this version is burned: fix it on main and release X.Y.Z+1";
    - keep `gh run rerun` for transient failures;
    - update tests/integration/test_release.py::test_release_reports_a_failed_workflow_run_with_its_recovery.
  - Tags: local and origin hold the same 22 tags. CHANGELOG.md notes that 0.15.2, 0.15.3, 0.16.0, 0.17.0, 0.17.1 and 0.18.0 have no tag, and that the 0.18.1 and 0.18.2 releases were withdrawn.
  - Repository settings (the owner switches these on):
    - immutable releases;
    - a ruleset for `refs/tags/v*` that blocks updates and deletion;
    - a ruleset for main that blocks force pushes and deletion, with no required check. The owner pushes merges, and release.sh (:252) the release commit, straight to main as an admin, so a required check would be bypassed and gate nothing. CI-8's release.sh check gates tags instead.
  - Add a test in tests/unit/test_cli_docs.py: the verification block names no `vX.Y.Z`.
- Done when:
  - `grep -c 'v0\.18\.2' README.md` prints 0, and the docs test passes.
  - Level (a), run on a host with curl, git, gh and the recorded uv, prints `identical` against the latest release (v0.20.0 today).
  - `gh api repos/devitops-com/fsbpyr/rules/branches/main --jq '[.[].type] | sort'` lists `deletion` and `non_fast_forward`.
  - `gh api repos/devitops-com/fsbpyr/rulesets --jq length` prints at least 2.
  - `gh api repos/devitops-com/fsbpyr/immutable-releases --jq .enabled` prints true.
  - CHANGELOG.md lists the six untagged versions and the two withdrawn releases.
  - The next release lists a `SHA256SUMS` asset.
- Effort: S. Depends on: CI-8 (better done first: it keeps burned versions rare). Owner decision: decided (owner, 2026-10-19; no D-number): the repository settings, the burned-version rule and the never-tagged versions (§5 #2). D4, D28 and D31 are unchanged.

### REL-2 Freeze the release artifact before the tests run, and compare it with stage 3
- Why: release.yml compresses and uploads the executable (:112-124) only after `make check` and `make test-ci` (:104-108). Those steps run dev dependencies with write access to dist/, and nothing compares dist/fsbpyr with dist/stage3/fsbpyr. Gap: repro/release-ci-check-gaps (low). eval:450.
- Change: in .github/workflows/release.yml, keep the one `build` job, because 84feb09 put the tests there on purpose, on the environment `make bootstrap` leaves. Reorder its steps:
  1. make bootstrap;
  2. make build;
  3. a new step `cmp dist/stage3/fsbpyr dist/fsbpyr`;
  4. Smoke test;
  5. Compress;
  6. Upload;
  7. make check;
  8. make test-ci.

  No new job is added. The upload goes to the `publish` job, which `needs: build`, so failing tests still block the release.
- Done when:
  - A main push run's log (CI-8) shows the cmp step, and the upload before `make check`.
  - The new step adds under 30 s to the build job. Whole runs vary too much to bound: tag runs of the same workflow took 5m46s (v0.19.0) and 7m09s (v0.20.0).
- Effort: S. Depends on: CI-8 (for the main push run). Owner decision: none.

### BLD-1 Keep a failed build from replacing a working output
- Why: four related gaps. eval:337, 466.
  - build.py:296-298 replaces `-o` (assemble.py `build_executable`, then writer.py `os.replace`) before `smoke_test` runs. So the man page's own example, `fsbpyr build ~/src/mytool -o ~/.local/bin/mytool` (cli.py:294), can overwrite a working tool with a broken one: dx/smoke-failure-replaces-output (medium).
  - A module entry naming a package without `__main__` passes the smoke test, because selftest only imports it (_fsbpyr_boot.py:367-368): dx/smoke-package-entry-false-success (low).
  - Warnings collected before a failure are dropped, whatever the error (cli.py:160-162): dx/warnings-lost-on-failure (low).
  - A failure during the write leaves no temporary file (tests/unit/payload/test_streaming_writer.py `test_a_file_changed_since_it_was_classified_fails_the_write`); the window this item opens, between the write and the commit, has no test: tests/no-interrupted-build-or-kill-tests (write part, low).
- Change:
  - Split src/fsbpyr/payload/writer.py `write_executable` (:581-610) into two steps: write and fsync `.{name}.XXXX.tmp`, then commit (`os.replace`). assemble.py `build_executable` passes the temp path through.
  - In build.py `build()`, smoke-test the temp file and commit only on success; on failure, unlink it. The app cache is keyed by the payload's app_name (boot/_fsbpyr_rt/native.py:79), not argv[0], so the temp name changes nothing.
  - For an entry without ':':
    - selftest in _fsbpyr_boot.py keeps importing the module; when it is a package, it also requires `importlib.util.find_spec("<pkg>.__main__")`, without running it;
    - build.py `_check_entry_module` requires `<pkg>.__main__` among the app's modules in the same case.
  - `build()` attaches the warnings collected so far to every error that leaves it, for example as exception notes; cli.py prints them before `fsbpyr: error:`.
- Done when:
  - A new e2e test in tests/e2e/test_build.py passes: with a pre-existing `-o` file and `--entry hello.cli:missing`, the build exits 1, `cmp` shows the old bytes unchanged, and no `.<name>.*.tmp` file is left in its directory.
  - A unit test passes: a package entry without `__main__` fails the build, and the message names `pkg.__main__`.
  - A unit test passes: an exception between the write and the commit leaves the old output and no `.tmp` file.
  - A unit test of `build()` shows that a StagingError raised after the unlocked-build warning carries that warning, and tests/unit/test_cli.py asserts that `fsbpyr: warning:` comes before `fsbpyr: error:`.
- Effort: S. Depends on: none. Owner decision: none.

### RT-1 Restore exit(), quit() and help in built apps
- Why: built apps run with `site_import = 0` (src/fsbpyr/launcher/main.c:136, D14), so `exit()`, `quit()` and `help` raise NameError in every app, while they work in the venv; the smoke test cannot see it. In an app built with fsbpyr 0.20.0, `exit(3)` raises NameError. Gap: deps/site-builtins-exit-quit-missing (medium). eval:248-251.
- Change: in src/fsbpyr/boot/_fsbpyr_boot.py `run()`, after `_install_frozen_sources()` and before `_multiprocessing_child() or _interpreter_child()`, so that D23 `-c` and `-m` children get them too:
  - install `builtins.exit` and `builtins.quit` from `_sitebuiltins.Quitter`;
  - install `help` from `_sitebuiltins._Helper`, which imports pydoc only when called.

  `_sitebuiltins` is frozen in CPython 3.14, so the import reads nothing from the payload. No fallback code is added. `site_import = 0` stays.
- Done when:
  - An e2e case in tests/e2e/test_runtime_app.py passes: an entry that calls `exit(3)` exits 3 with empty stderr, `quit()` exits 0, and `callable(help)` is true.
  - In tests/e2e/test_compat.py, the mp fixture built with `--interpreter-reexec` starts a `-c 'exit(4)'` child, which exits 4.
  - A new unit test in tests/unit/boot/test_boot.py passes.
  - tests/e2e/test_imports.py::test_boot_imports_only_builtin_frozen_and_startup_modules and the startup-budget test stay green.
- Effort: S. Depends on: none. Owner decision: none (D14 is kept).

### NAT-1 Actually rebuild the project's own extensions against the glibc 2.28 sysroot
- Why: `rebuild_on_baseline` (build.py:121-147) passes the already built project wheel to `staging.rebuild` (staging.py:179; `--no-install-project` at :120). So a project's own C extension keeps the host's build, while the log says "rebuilding ... against the glibc 2.28 sysroot". Gap: native/project-own-extension-not-rebuilt (missed, medium). eval:198, 450.
- Change:
  - In `rebuild_on_baseline`, when the project is among the stale names, build the project wheel again into a fresh directory with runtime/sysroot.py `build_flags(root, os.environ)`, and reinstall that wheel.
  - Add the fixture tests/fixtures/projects/cext: a tiny setuptools C extension with no wheel that calls `pthread_create`. Its default version on trixie is GLIBC_2.34, so a host build needs more than 2.28, and `built_here_above` (build.py:105-118) selects it.
  - The C compiler for the fixture is the `gcc` that CI-1 installs. `build_flags` deliberately does not set CC (sysroot.py:89-93), so the build host needs a `cc`.
  - Add a unit test with staging mocked.
- Done when:
  - The unit test asserts that the project wheel is rebuilt with the sysroot flags.
  - An e2e test in tests/e2e/test_build.py (run by test-ci) builds cext on the trixie CI host. It asserts that the host-built extension needed more than GLIBC_2.28 before the rebuild, that `fsbpyr inspect --json` reports glibc ≤ 2.28 afterwards, and that no glibc warning is printed.
- Effort: S. Depends on: CI-1 (gcc in CI). Owner decision: none (applies D25).

### DX-4 Put a working quickstart and the prerequisites first in the README
- Why: "Building an app" starts at README.md:123, behind about 90 lines on building from source and verification. The install line `curl -fLo ~/.local/bin/fsbpyr` (README.md:18, :27-28) fails when ~/.local/bin does not exist, and on a fresh Debian or Ubuntu account a new ~/.local/bin reaches PATH only at the next login (/etc/skel/.profile). Nothing says what a project needs. Gaps: dx/readme-first-run-path (medium), dx/docs-examples-misleading-config (low). eval:330-339.
- Change: README.md in this order:
  1. The intro.
  2. A Quickstart of about 10 lines, fenced between comment markers: `mkdir -p ~/.local/bin`, curl, chmod, a minimal pyproject with `[project.scripts]`, then `~/.local/bin/fsbpyr build`, run, `~/.local/bin/fsbpyr inspect`. Once Phase 9's install.sh ships, it can replace the first three lines. The later lines must then call fsbpyr from the directory install.sh chose (IMPLEMENTATION_PLAN.md:1568-1569), since its PATH change reaches only shells started afterwards (:1572-1575).
  3. Requirements:
     - Build host: Linux x86-64 and uv on PATH (D26).
     - Project: a console script or `--entry`, and a requires-python that admits 3.14.
     - Lock sources, in order: an explicit `requirements` file, uv.lock, pylock.toml, requirements.txt, then unlocked with a warning (`Project.lock_source`). Dev groups are excluded.
  4. "Supported targets" (SCOPE-1), which links docs/compatibility.md (DOC-1).
  5. "Building an app" and the sections after it.
  6. "Getting fsbpyr", kept under that name (the curl and `fsbpyr.xz` downloads and the attestation check), with "Building from source" and "Verifying a release" under it, last. Phase 9 points to it by name: self-update's developer-build error quotes its curl command, and install.sh leads it once it ships (IMPLEMENTATION_PLAN.md:1457-1460, :1580-1582).

  The `[tool.fsbpyr]` sample marks `requirements` as "pip projects only; overrides uv.lock", and comments out one of the two compression keys, which config.py rejects together.
- Done when:
  - A new e2e test in tests/e2e/test_build.py (in test-ci) passes. It runs the marked block against a copy of the hello fixture, with HOME set to a temporary directory, PATH without `$HOME/.local/bin`, and a shim for the fsbpyr under test in place of the download. XDG_CACHE_HOME stays at the real cache, which holds the runtime (registry.py `cache_home`). The executable prints the fixture's line.
  - tests/unit/test_cli_docs.py parses every `fsbpyr` command in the block with the CLI, and loads the `[tool.fsbpyr]` sample with `load_config`.
- Effort: S. Depends on: none. Owner decision: none.

### DOC-3 Correct the README's overclaims and stale numbers
- Why: several claims are wrong or out of date. eval:318-320, 432.
  - README.md:4-7 says the executable "needs nothing on the target machine but Linux x86-64 with glibc 2.28 or newer", but numpy 2.5.3 and onnxruntime 1.30.0 need libstdc++.so.6 and libgcc_s.so.1: native/no-target-requirements-report (high, documentation part).
  - `--fast-compression` is called "up to ~8%" larger (README.md:151, cli.py:96), but the measured cost is +9.0-9.4% (docs/performance.md, "The same app with 0.14.0 and 0.15.0"): perf/stale-or-contradicted-claims.
  - README.md:286 names 4 of the 6 built-in fs-packages: deps/builtin-rules-hardcoded (README part).
  - The libGL `--allow-system-lib` example does nothing, because libGL.so.1 is already on the allowlist (natives.py:30): dx/docs-examples-misleading-config.
  - The internal benchmark name "aiagent" appears in the help (cli.py:100) and the README (:152, :187): dx/internal-jargon-and-polish.
  - The README's performance figures are from older releases. The `--no-source` sizes (README.md:187-188, 157.4 MB to 140.0 MB) predate 0.5.0's blocks (D30) and have no docs/performance.md row: perf/perf-doc-drift (README part).
  - All but the first are low.
- Change:
  - README.md:4-7 keeps its wording and adds "plus the system libraries its native dependencies link (often libstdc++ and libgcc_s)". NAT-2 adds "`fsbpyr inspect` lists them" once inspect does.
  - "up to ~9%" in README.md:151 and cli.py:96, together (test_readme_option_table_is_the_build_commands_help keeps them equal).
  - Every other number in the README and `--help` cites its docs/performance.md row and release, or says "measured at 0.8.0, not re-measured". This covers the 6.5% of `--best-compression` (performance.md:111) and the xz first-import figures at README.md:288-291 (performance.md, "`--best-compression` (0.8.0)"). The `--no-source` sizes are re-measured on the current release and get a performance.md row.
  - README.md:286 lists all of `BUILTIN_FS_PACKAGES`.
  - The allow-system-lib examples (README.md:177, cli.py:305) use a soname that is not in `SYSTEM_LIBRARIES`.
  - The help texts and the README no longer name "aiagent".
  - tests/unit/test_cli_docs.py checks two things: the README's fs-packages list equals `classify.BUILTIN_FS_PACKAGES`, and no allow-system-libs example names a `SYSTEM_LIBRARIES` entry.
- Done when:
  - both docs tests pass;
  - `grep -n '~8%' README.md src/fsbpyr/cli.py` and `grep -n aiagent README.md src/fsbpyr/cli.py` find nothing;
  - `grep -n libstdc README.md` finds the opening sentence, not only line 138.
- Effort: S. Depends on: none. Owner decision: none.

### SCOPE-1 State the 3.14-only target, and cap fsbpyr's own requires-python
- Why: built apps are CPython 3.14-only (build.py:39 `TARGET_PYTHON`; a single pin at runtime/pins.py:28-37), which eval:164 understates. fsbpyr's own `requires-python = ">=3.14"` (pyproject.toml:8) admits the released 3.15, where `dumps` in bytecode.py:283 raises mid-build. Gaps: maturity/python-314-only-for-built-apps (medium, documentation part); maturity/requires-python-no-upper-bound, maturity/free-threaded-refused, native/musl-undetected (low).
- Change:
  - `requires-python = ">=3.14,<3.15"`, then re-lock. pip and `uv run` then refuse 3.15, but `uv pip install` and `uv tool install` ignore upper bounds, and every user has uv (D26).
  - So `fsbpyr build` and `fsbpyr runtime build` first compare the running Python with `bytecode.SUPPORTED_PYTHON`, and exit 1 with "fsbpyr runs on CPython 3.14 only" before staging. The `# pragma: no cover` comment at bytecode.py:283 names this check instead of requires-python.
  - README "Supported targets":
    - apps run CPython 3.14.8, and their requires-python must admit 3.14;
    - Linux x86-64 with glibc only (on Alpine/musl the shell reports "not found" for a file that exists);
    - the GIL build only (D35);
    - planned in Phase 9, after the MVP: 3.12 and 3.13 (one fsbpyr per Python version), then Linux aarch64, musl and macOS arm64.
- Done when:
  - In a CPython 3.15 venv, `pip install .` is refused; after `uv pip install .`, `fsbpyr build` exits 1 with the new message and stages nothing. A unit test checks the message with a patched `sys.version_info`.
  - The README section exists, and a docs test checks that it names the pinned CPython version from runtime/pins.py.
- Effort: S. Depends on: none. Owner decision: decided (owner, 2026-10-19; D13): the cap `<3.15`, which becomes `>=3.12,<3.15` when Phase 9's port to 3.12 (step 2 of its Python-version part) lowers D13 to 3.12 (IMPLEMENTATION_PLAN.md:1522-1523), and rises to the next minor whenever `fsbpyr` moves to a newer Python (:1502-1503) (§5 #12).

### SEC-1 Publish a security policy and the embedded-runtime CVE response
- Why: the public repository has no SECURITY.md, and private vulnerability reporting is off. Nothing tells users that a CPython or OpenSSL fix means rebuilding their executables, although every app embeds the PBS runtime statically (D2, D4). Gaps: security/no-vulnerability-disclosure-process, repro/no-security-policy-or-cve-response, maturity/governance-files-missing (medium); maturity/runtime-security-update-policy (high; its policy part); dx/no-community-docs (low). eval:203.
- Change:
  - SECURITY.md:
    - Reporting goes through GitHub private advisories. Reports are acknowledged within 7 days.
    - Supported version: the latest release.
    - The embedded components: CPython, OpenSSL, SQLite, expat, zlib, libffi, liblzma, mpdecimal and others.
    - The rule: when a PBS release fixes a security issue in the pinned CPython minor or in a statically linked library, an fsbpyr patch release follows. The targets are 7 days for high or critical issues and 30 days otherwise.
    - Until SEC-4's opt-in clang-23 toolchain is merged, a fix that needs a PBS release after 20261009 first waits for it.
    - The patch release's CHANGELOG `### Security` entry names the component versions and the old and new runtime ids, and says "rebuild your executables". Under D4, a rebuild is the only fix for apps.
  - Enable private reporting (`gh api -X PUT repos/devitops-com/fsbpyr/private-vulnerability-reporting`), and link SECURITY.md from the README.
  - .github/dependabot.yml: weekly updates for the `github-actions` and `uv` ecosystems (uv.lock's click and packaging are embedded in every release). The `docker` ecosystem is added once REP-1 pins the image digest. Whether Dependabot updates a file named Containerfile, or a workflow's `container:` image, is unverified, so REP-1's watch step checks the digest either way.
  - .github/ISSUE_TEMPLATE/bug.yml asks for `fsbpyr --version`, `fsbpyr inspect --json <app>`, `uv --version` and the build output.
  - A short CONTRIBUTING.md stub. It states that every packaging fix adds a fixture or corpus test (eval:470, as CLAUDE.md:53 already asks), and CI-4 fills in "Running the tests".
- Done when:
  - `gh repo view devitops-com/fsbpyr --json isSecurityPolicyEnabled` shows true.
  - `gh api repos/devitops-com/fsbpyr/private-vulnerability-reporting --jq .enabled` prints true.
  - The Dependabot update jobs for github-actions and uv appear under the repository's Insights → Dependency graph → Dependabot.
- Effort: S. Depends on: none. Owner decision: decided (owner, 2026-10-19; no D-number): the response windows and the acknowledgement time (§5 #3); private reporting is part of the repository settings (§5 #2).

### SEC-2 Move the runtime to PBS 20261009 now, and watch upstream daily
- Why: the runtime every app embeds is pinned by hand to 3.14.8+20261003 (pins.py:28-37), two PBS releases behind, and nothing watches upstream. Release 20261009 updates libffi (3.4.8 to 3.8.0) and expat (2.8.5 to 2.9.0); whether these carry CVE fixes is unverified. It is also the last release whose bitcode today's clang 22 can link: PYTHON.json says llvm-bitcode 22.1.3 for 20261009 and 23.1.3 for 20261013. Gaps: security/embedded-runtime-cve-latency (high), maturity/runtime-security-update-policy (high; its automation part). Not in eval.md.
- Change:
  - pins.py: `3.14.8+20261009`, with the sha256 from PBS's SHA256SUMS. The runtime id changes by itself (registry.py:104-111). Run `make test` locally before tagging, because the full suite gates releases only once CI-3 lands.
  - .github/workflows/upstream-watch.yml runs on a daily schedule and on workflow_dispatch, with `permissions: issues: write`.
    - It finds the newest `cpython-3.14.*-x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst` with `gh release list/view --repo astral-sh/python-build-standalone` and compares it with pins.py.
    - On a difference, it opens or updates an issue labelled `runtime-update`. The issue quotes the release notes and any change of LLVM major.
    - REP-1 later adds uv, the apt snapshot and the image digest to the same workflow.
  - The CHANGELOG entry lists the component changes. It gets a `### Security` heading only where the upstream notes name a CVE.
- Done when:
  - `fsbpyr inspect --json dist/fsbpyr | jq -r .build_info.runtime` on the next release differs from v0.20.0's.
  - Its tag run shows stage2 == stage3.
  - A `workflow_dispatch` run of upstream-watch opens an issue that names the newest PBS release (20261013 today) and "LLVM 22.1.3 -> 23.1.3".
- Effort: S. Depends on: none. Owner decision: none (D2 is unchanged at LLVM 22).

### CI-1 Turn unexpected skips into failures in CI, and run the D37 contract test there
- Why: the v0.19.0 and v0.20.0 CI runs each skipped 28 items. pytest lists every reason (`-ra`) but exits 0 with any number of skips. Gaps: tests/silent-skips-in-ci-and-e2e, perf/ci-skips-count-guards (medium).
  - The skips:
    - 17 test_launcher_format items ("no C compiler": the test looks for `cc`, tests/integration/test_launcher_format.py:50, and CI installs only clang-22);
    - 1 strace: the check that a start never touches the stdlib on disk (test_runtime_app.py:247);
    - 2 groff;
    - 3 root file-mode and 2 test_way root/group;
    - 2 unshare/X and 1 podman.
  - On a host without clang 22 the whole e2e tier skips (tests/e2e/conftest.py:38-47).
  - D37's guards are not in test-ci: the cache-sync contract and boot-import check (tests/e2e/test_imports.py), and the FSBPYR_TRACE counts (tests/e2e/test_natives.py).
- Change:
  - The release.yml step "Install git, make and xz" (:45-48) adds `gcc strace groff-base`. gcc gives test_launcher_format and NAT-1's fixture a `cc`. bootstrap.sh's PACKAGES stay unchanged.
  - Makefile `test-ci` adds tests/e2e/test_imports.py, which needs no network. The trace counts gate releases through CI-3's full suite.
  - tests/conftest.py collects skip reasons in `pytest_runtest_logreport`. `FSBPYR_TESTS_REQUIRE_ALL` names an allowlist (`ci`, set by the test-ci step; CI-3 and CI-4 add theirs), and `pytest_sessionfinish` prints every skip the list does not match and sets `session.exitstatus = pytest.ExitCode.TESTS_FAILED`.
  - Entries match by nodeid or reason prefix, since some reasons carry runtime text (test_way.py:165). test-ci runs as root, so `ci` holds the 5 root-only skips and the 3 unshare and podman skips.
- Done when:
  - A main push run's log shows test_launcher_format with no skip, and no strace or groff skips.
  - test_imports runs in test-ci.
  - A probe commit that adds `pytest.skip("x")` turns CI red.
- Effort: S. Depends on: CI-8 (for the main push run). Owner decision: none.

### CI-2 Measure and enforce coverage in CI
- Why: branch coverage is configured (pyproject.toml:37-44), but no Makefile target, CI step or `fail_under` runs or gates it. So IMPLEMENTATION_PLAN.md §7's targets (80% overall, 90% for payload/ and elf.py) go unchecked. At HEAD the unit and integration run gives 93%, with payload/ plus elf.py at 96% and build.py at 63% (lines 211-299, the whole of `build()`); runtime/stdlib.py drops from 91% to 57% when the PBS distribution is not cached. The C launcher has no coverage at all. Gap: tests/coverage-not-measured-or-enforced (medium). eval:116, 133, 343.
- Change:
  - Makefile `test-ci` adds `--cov --cov-report=term --cov-report=xml`. test-ci includes test_build.py, which calls `fsbpyr.cli.main` in-process (test_build.py:17), so `build()` is covered.
  - `[tool.coverage.report] fail_under` is set to the first total measured in CI minus 1, and never below §7's 80. pytest-cov applies it to every `--cov` run, so it also gates CI-3's full-suite run, which runs every test-ci test as well.
  - A second step: `uv run --locked coverage report --include='src/fsbpyr/payload/*,src/fsbpyr/elf.py' --fail-under=90`.
  - release.yml uploads coverage.xml.
  - C coverage stays informational (CI-3).
- Done when: CI fails when the total drops below `fail_under`, or payload/ plus elf.py below 90%, and every run carries a coverage.xml artifact.
- Effort: S. Depends on: CI-1 (better done first). Owner decision: none.

## 3.1 Stage 1 — weeks 4-11: stop false-success builds and make the evidence real

### CI-4 Put the full test environment in the repo
- Why: only the deleted CI job (commit 55641fc) and a private image know what the full suite needs; Containerfile:8-10 installs only the build set. Every e2e session also downloads PBS and the sysroot again, because the e2e conftest's `_private_cache` gives each session a fresh cache. Gaps: tests/test-env-not-reproducible (medium), tests/e2e-rebuilds-runtime-from-network-every-session (missed, low). eval:324-339.
- Change:
  - tools/test-setup.sh, run as root by the Containerfile's `test` stage and by CI-3's job:
    - installs libclang-rt-22-dev, llvm-22, gcc, strace and groff-base beyond bootstrap's packages;
    - adds a uid-1000 user `tester` in one supplementary group (test_way.py:62; SEC-3's non-root variant needs it);
    - adds a root-owned directory in /tmp (test_way.py:184, test_cache_root.py:187) and a searchable directory of a second user under /var/lib (test_cache_root.py:164).
  - The Containerfile's first stage becomes `build`, which bootstrap.sh's `podman build` selects with `--target build`. The `test` stage runs the script and points UV_PROJECT_ENVIRONMENT and UV_PYTHON_INSTALL_DIR (root's paths, Containerfile:16-17) into tester's home.
  - The stage has no podman: rootless podman maps only 65,537 ids into a container, which leaves tester no 65,536-id range of its own for the `podman unshare` tests (test_way.py:194, test_cache_root.py:195). They skip there; CI-3 runs them.
  - Makefile `test-container` copies the checkout into tester's home and runs `uv run --locked pytest --ignore=tests/e2e/test_portability.py` as `tester`, with `FSBPYR_TESTS_REQUIRE_ALL=container` and bootstrap's `--network pasta:-4`. That module needs `podman run` inside podman; CI-6's `make test-portability` runs it on the host. The `container` allowlist names every skip left, with its reason.
  - tests/e2e/conftest.py's runtime fixture keeps only the sha256-pinned PBS and sysroot downloads, never the cache root, in `FSBPYR_TEST_DOWNLOADS` (a podman volume under `test-container`), and seeds each session from it; pbs.fetch checks them again. The terminal summary says whether each was seeded or fetched.
  - CONTRIBUTING.md "Running the tests": the tiers (unit, test-ci, full, portability), the prerequisites and the expected skips.
- Done when:
  - On a host with only podman, make and uv, `make test-container` exits 0 with no skip outside the `container` allowlist.
  - A second run's summary reports both downloads as seeded.
- Effort: M. Depends on: CI-1. Owner decision: none.

### CI-3 Run the whole suite on main and nightly as a normal user, and gate releases on it
- Why: CI and the release gate run part of the suite: v0.20.0's run selected 1,042 of 1,424 items (45 of 133 e2e functions) and executed 1,014. eval:192-204, 361-367.
  - Natives, compat, ASan/UBSan, the fuzzer, cache-root and GC tests never gate a release.
  - CI runs as root, so the ownership checks have nothing to test.
  - The owner removed the full-suite job in 84feb09 for latency. Its only CI attempt, run 38624936426, ran as `tester` in a privileged container; about 433 e2e items in, only test_portability.py's 13 `podman run` items had failed when it was cancelled after 18 minutes, with no summary. So this item runs everything except portability in a container, and CI-6 runs portability on the runner host.
  - Gaps: tests/ci-runs-only-core-subset, maturity/release-gate-partial-suite (high); security/adversarial-tests-not-in-ci (medium); tests/ci-runs-as-root, tests/fuzzing-not-continuous, tests/main-unprotected-no-schedule (schedule part) (low).
- Change:
  - New .github/workflows/full-suite.yml, triggered by push to main, a nightly and a weekly schedule, and workflow_dispatch. Its `suite` job is a matrix keyed by Python version with one entry (3.14), so Phase 9's Python-version part only adds entries (its step 3, "Runtimes, CI and releases", IMPLEMENTATION_PLAN.md:1524-1527). It runs in `container: docker.io/library/debian:trixie` with `options: --privileged` and `timeout-minutes: 60`, split as 55641fc's job was:
    - as root: bootstrap's packages, tools/test-setup.sh (CI-4), and 55641fc's rootless podman for tester (podman, uidmap, passt, catatonit, fuse-overlayfs; `tester:100000:65536` in /etc/subuid and /etc/subgid; vfs storage, cgroupfs); a copy of the checkout (`fetch-depth: 0`) in tester's home, owned by tester; `0` in /proc/sys/kernel/apparmor_restrict_unprivileged_userns, without which the ubuntu-24.04 host keeps unprivileged users out of user namespaces (runner-images#10443);
    - as tester (`runuser -u tester -- env -i HOME=/home/tester ...`), so uv, Python and the caches are its own: `make bootstrap` (the packages are present, so no sudo), `make check`, then `uv run --locked pytest --ignore=tests/e2e/test_portability.py --durations=30 --junitxml=full.xml --cov --cov-report=xml` with `FSBPYR_TESTS_REQUIRE_ALL=full`;
    - it uploads full.xml, coverage.xml and dist/fsbpyr for CI-6.
  - Fuzzing:
    - The fuzzer runs with `FSBPYR_FUZZ_SECONDS=600` when src/fsbpyr/launcher or tests/fuzz changed since the last tag (`git diff --quiet "$(git describe --tags --abbrev=0)" HEAD -- src/fsbpyr/launcher tests/fuzz`), and on the weekly schedule; otherwise for the 30 s default. So the run that gates a release fuzzes for 600 s whenever the launcher changed since the previous release.
    - tests/e2e/test_fuzz.py takes `FSBPYR_FUZZ_CORPUS` as its `found` directory, today a fresh tmp_path (:63). The corpus is kept in actions/cache, which is acceptable because this workflow publishes nothing; release.yml stays cache-free.
  - llvm-cov, informational: LauncherBuild.compile (tests/e2e/conftest.py) takes extra flags from the environment (`-fprofile-instr-generate -fcoverage-mapping`, with LLVM_PROFILE_FILE), and the report covers the launcher sources the probes and the fuzz harness exercise.
  - The release gate:
    - CI-8's check in release.sh is extended to full-suite.yml: HEAD needs a green release.yml main push run (the build, REL-2's cmp, the smoke test and, once it lands, REP-2's compare) and a green full-suite.yml run. It still refuses before the release commit is made, and follows a run in progress with `gh run watch --exit-status`, as it follows the tag's run.
    - In the release.yml `publish` job:
      - `actions: read` is added to its permissions, and its checkout gets `fetch-depth: 2`;
      - it runs the same check for `git rev-parse HEAD^`;
      - it checks that `git diff --name-only HEAD^ HEAD` touches only CHANGELOG.md, pyproject.toml, src/fsbpyr/__init__.py and uv.lock, which holds for c3850b0 (v0.19.0) and 7064cb7 (v0.20.0);
      - it appends "Full suite: <run URL>" to the release notes.
    - release.yml's pull-request and main runs stay as they are.
- Done when:
  - `gh run list --workflow full-suite.yml` shows 7 green nightly runs in a row.
  - release.sh and the publish job both refuse a tag whose parent has no green run, and tests/integration/test_release.py covers the release.sh refusal and its wait.
  - full.xml shows no skip outside the `full` allowlist, and shows that the 5 root-only tests skipped in v0.20.0's run executed, as did test_way.py:184 and test_cache_root.py:164, :187.
  - A launcher change on main produces a run whose log shows `FSBPYR_FUZZ_SECONDS=600` and a restored corpus.
  - The measured duration is recorded in CONTRIBUTING.md.
- Effort: L. The roughly 12 e2e items that run 38624936426 did not reach, and the unit and integration tiers, have not run as `tester` in CI. Depends on: CI-1, CI-4, CI-8. Owner decision: decided (owner, 2026-10-20; no D-number; partly reverses 84feb09): a release requires a green full-suite run of its parent commit (§5 #1).

### CI-6 Run the 13-distribution portability matrix on the runner host
- Why: tests/e2e/test_portability.py never runs in CI and gates no release. It covers 13 images with glibc 2.28-2.44, and its objdump cross-check compares the reported glibc floor with the binaries. In run 38624936426 (rootless podman as `tester` in a privileged container), its 13 `podman run` items failed and its 4 objdump checks passed; pytest never printed why. The containers also get outbound network access, since `podman run` (test_portability.py:86) sets no `--network`, although the HTTP checks are already left out of its COMMANDS. eval.md missed that. Gaps: native/portability-matrix-not-in-ci (medium), tests/ci-runs-only-core-subset (high, portability part). eval:197-198, 361-363, 387, 441.
- Change:
  - First, a workflow_dispatch spike runs test_portability for one image with `-rf --tb=short`, once on the runner host and once in CI-3's container, to record why the nested run failed.
  - A `portability` job in full-suite.yml (`needs: suite`, the same one-entry Python-version matrix) runs on the ubuntu-24.04 host, with no container; the runner image ships Podman 4.9.3. The job:
    1. downloads the suite job's dist/fsbpyr;
    2. installs uv (REP-1's pinned version once it exists) and `llvm` (for llvm-objdump);
    3. builds hello, heavy, heavy-pip and compat with a new tools/portability-apps.sh of about 10 lines. The script does what the `apps` fixture does: `uv export` for heavy-pip, then four `fsbpyr build` calls. The self-hosted executable needs no compiler (D4);
    4. runs `uv run --locked pytest tests/e2e/test_portability.py --junitxml=portability.xml` with `FSBPYR_PORTABILITY_APPS` pointing at those apps and `FSBPYR_TESTS_REQUIRE_ALL=portability` (CI-1's mechanism, with an empty allowlist), and uploads portability.xml.
  - The `apps` fixture (test_portability.py:59-75) returns `FSBPYR_PORTABILITY_APPS` when it is set, without requesting the runtime fixture.
  - `podman run` (test_portability.py:86) gets `--network=none`.
  - The job logs in to Docker Hub with a read-only token secret. Each run pulls 13 images, and the effect of unauthenticated rate limits is not measured. A failed pull fails the job; it is never turned into a skip.
  - `make test-portability` does the same on a developer host, using dist/fsbpyr.
- Done when:
  - The spike's log names the cause of the in-container failure.
  - The portability job is green on 7 nightly runs in a row, with 13 images and the objdump check passing.
  - `grep -n 'network=none' tests/e2e/test_portability.py` matches.
  - CI-3's gate covers the job, because it checks the conclusion of the whole workflow run.
- Effort: M. Depends on: CI-3. Owner decision: none (part of §5 #1).

### TST-1 Make the compatibility checks fail on stderr noise
- Why: the compat matrix (tests/e2e/test_compat.py:93-98) and the datafiles checks assert the exit status and stdout only; tests/e2e/test_natives.py checks stderr only for its trace line and one onnxruntime message. That is how 0.17.1's mispackaged mypyc charset_normalizer passed while requests warned on stderr. Gap: tests/compat-checks-ignore-stderr (medium). eval:466.
- Change:
  - Assert that stderr is empty, apart from test_natives' trace line and an explicit allowlist per check (reuse test_portability.py `_NO_HOSTNAME`).
  - Add a `charset_normalizer` check to tests/fixtures/projects/compat/src/compat/cli.py: `charset_normalizer.md.__file__` ends in an extension suffix. It also runs on every portability image under CI-6.
- Done when: on a scratch branch that reverts the 0.17.1 classify fix (digit-named modules) without the new check, the existing `requests` check (test_compat.py:39) fails on stderr alone in `make test`, which is otherwise green. test_compat is not in test-ci (Makefile:80-81).
- Effort: S. Depends on: none (it gates releases once CI-3 lands). Owner decision: none.

### TST-2 Compare every fixture command with the same project run from its venv
- Why: expected outputs are hard-coded literals (test_compat.py:25-41, test_natives.py:26-32), so nothing checks eval.md's central question: does the executable behave like the original application? Gaps: tests/no-differential-vs-native-python (low after verification, but it is how false-success builds get measured), deps/smoke-test-only-imports-entry (medium). eval:345, 369-371, 466.
- Change:
  - New tests/support/differential.py. For a fixture project and a list of argv, it runs two things with the same env and cwd, and compares returncode, stdout and stderr:
    - (a) the console script from a venv made with staging's sync flags (staging.py `_uv_sync_plan`, D5): `uv sync --locked --no-dev --no-default-groups --no-editable --python <runtime minor>` (3.14 today). So the venv holds the project's wheel, not an editable copy, and the runtime's Python, not whichever version from 3.12 on uv picks for the fixtures' `>=3.12`. It runs under `python -I -X utf8`, since built apps run in UTF-8 mode (D14);
    - (b) the built executable, named like the console script (test_compat.py `_build` names it `compat0`), without `FSBPYR_TRACE`, whose `fsbpyr-trace` line (trace.py `report`) would land on stderr.
  - Only documented normalizations are allowed:
    - executable paths vs venv paths;
    - sys.path, sys.prefix and sys.flags (D14);
    - the `__file__` prefix;
    - the missing `sys.frozen` (D21).
  - Use it in test_compat.py, test_natives.py and the datafiles checks. Keep literals only where the venv's output is not deterministic.
- Done when: every command in test_compat's MATRIX and in test_natives runs through the differential (the count is asserted), and removing RT-1's builtins makes a fixture command that calls `exit()` fail it.
- Effort: M. Depends on: TST-1, RT-1. Owner decision: none.

### BLD-2 Let the smoke test run the app once (opt-in)
- Why: `smoke_test` (build.py:185-203) only resolves the entry module, so lazily imported dependencies, `exit()` and `__file__` data failures ship in green builds. Gaps: deps/smoke-test-only-imports-entry, dx/shallow-smoke-test (medium). eval:201, 466.
- Change:
  - Add `[tool.fsbpyr] smoke-args = ["--version"]` and `--smoke-args "ARGS"`, one string split with `shlex.split` (src/fsbpyr/config.py, cli.py).
  - The build runs BLD-1's temp executable with these arguments and requires exit 0. On failure, it shows the app's stderr and names the arguments.
    - `subprocess.run([str(output), *args], executable=tmp)` keeps argv[0], from which click and argparse take the program name, at the final `-o` path; the launcher finds its payload through /proc/self/exe (launcher/payload.c:110-116). Unverified end to end.
    - stdin is /dev/null, so an app that reads it cannot block until `_SMOKE_TIMEOUT` (120 s).
  - It is opt-in, because only the user knows which arguments are safe and free of side effects; the default smoke test already runs user code (selftest imports the entry module).
  - Regenerate the README option table (test_cli_docs.py).
  - No import-every-module sweep (§4).
- Done when: e2e tests in tests/e2e/test_build.py pass:
  - clicky with `smoke-args = ["--help"]` builds;
  - a fixture whose `--version` path calls a missing function fails the build with exit 1 and leaves `-o` unchanged.
- Effort: S. Depends on: BLD-1. Owner decision: none.

### BLD-3 Add --strict so CI can fail on warnings, including unhashed requirements
- Why: every portability, packaging and lock warning still exits 0 (cli.py:163-168). A requirements.txt counts as "locked" even when it is unpinned or unhashed (project.py:80-95), and hashes are enforced only when a literal `--hash=` appears (staging.py:165). Gaps: repro/requirements-lock-weak (medium); dx/no-build-json-or-strict-mode, native/no-strict-portability-mode, deps/build-diagnostics-not-machine-readable (low). eval:62, 337, 466.
- Change:
  - Add `--strict/--no-strict` (cli.py) and `strict = true` (config.py).
  - Under strict, `build()` raises before `build_executable`, with the counted warnings in the error, so `-o` stays untouched; every warning is collected by then. Every warning counts except:
    - the uv mismatch (D26) and git errors (vcs.py `git_state`), which describe the build host, not the app;
    - the always-on ctypes guess (classify.py `_hint_unused_natives`): only fs-packages silences it, and that changes how the app runs.
  - Under strict, unlocked builds fail, and requirements sources get uv `--require-hashes`.
  - Without strict, requirements lines without `==` get a warning.
  - Document `fsbpyr inspect --json app | jq -r .glibc` as the glibc gate.
  - No `build --json` and no warning codes: both deferred (§4).
- Done when:
  - An e2e test in tests/e2e/test_build.py passes: with a pre-existing `-o`, an unlocked copy of hello exits 1 under `--strict` and `cmp` shows the old bytes unchanged; without `--strict` it builds.
  - In an e2e test, clicky with an unhashed requirements.txt fails under `--strict`.
  - A unit test passes: a uv other than the builder's, a git error and the ctypes guess do not fail a `--strict` build.
  - A tampered pylock.toml hash fails the build. Whether uv enforces pylock hashes is unverified; if it does not, strict refuses a pylock.toml without hashes, and the test documents that.
- Effort: S. Depends on: none. Owner decision: none (D5 still allows unlocked builds by default).

### NAT-2 Report what the target must provide beyond glibc, and check the GLIBCXX/CXXABI/GCC floors
- Why: two gaps in what fsbpyr reports and checks. eval:318-320, 384, 416-418, 468.
  - Sonames resolved to the 21-entry SYSTEM_LIBRARIES (natives.py:26-32) or through `--allow-system-lib` are forgotten once checked.
  - Only `glibc_requirement` (elf.py:154) is ever checked, and it filters to GLIBC_*. So onnxruntime 1.30.0's needs on libstdc++.so.6 and libgcc_s.so.1 (GLIBCXX_3.4.22, CXXABI_1.3.11) and numpy 2.5.3's (GLIBCXX_3.4.21, CXXABI_1.3.9) are neither reported nor compared with what Debian 10 and RHEL 8 ship.
  - Gaps: native/no-target-requirements-report (high); native/non-glibc-symbol-versions-unchecked (medium); deps/external-libs-not-reported, native/allow-system-lib-unvalidated (low).
- Change:
  - src/fsbpyr/elf.py:
    - keep `vn_file` from each VERNEED entry (unpacked and dropped at :137);
    - `needed_versions` (:128) returns (file, version) pairs; its one caller, `glibc_requirement`, and the direct calls in tests/unit/builder/test_natives.py adapt;
    - `version_requirements` gives, per namespace, the highest numeric version plus any non-numeric markers (CXXABI_TM_1, CXXABI_FLOAT128).
  - src/fsbpyr/natives.py `closures` records three things for every NEEDED that the system satisfies: which payload files need it, the versions per namespace, and which allowlist entries were used. Unused `--allow-system-lib` entries get a warning. An unreadable version table drops that file's version data with a warning and does not fail the build.
  - A constant next to SYSTEM_LIBRARIES holds auditwheel's manylinux_2_28 x86_64 limits, taken from its policy file and checked there: GLIBCXX_3.4.24, CXXABI_1.3.11 (plus the TM_1 and FLOAT128 markers), GCC_7.0.0, and ZLIB_1.2.9, because libz.so.1 is on the allowlist.
  - build.py `glibc_report` warns per namespace above the limit, naming the file and the version, and warns on GLIBC_PRIVATE. There is no rebuild trigger, because the sysroot has no libstdc++.
  - build_info["system"], sorted (D15-safe), plus one summary line, for example "target needs: glibc >= 2.28; libstdc++.so.6 (GLIBCXX_3.4.22, CXXABI_1.3.11), libgcc_s.so.1". It includes the stub's own NEEDED entries, read from the stub as `glibc_report` reads its glibc.
  - inspection.py adds this section and the entry point to the text output and `--json`. The parenthetical at README.md:5-7, on the glibc that `fsbpyr inspect` shows, extends to these libraries (DOC-3's follow-up).
  - Extend the objdump cross-check (tests/e2e/test_portability.py `test_reported_glibc_requirement_matches_the_binary`) to the new namespaces.
- Done when:
  - `fsbpyr inspect --json <heavy> | jq '.build_info.system.libraries|keys'` includes libstdc++.so.6 and libgcc_s.so.1, and hello lists only the glibc libraries its stub needs (libc, libm, libpthread, libdl, librt, libutil).
  - A unit test with tests/support/elf.py `make_elf` warns for GLIBCXX_3.4.30 and names the file. One with a malformed version table warns and the build goes on.
  - tests/unit/test_inspect.py::test_json_describes_everything is updated.
  - stage2 == stage3.
- Effort: M. Depends on: none. Owner decision: none.

### RT-2 Explain single-file-layout failures at run time (D24 amendment), and widen the build-time scan
- Why: four problems. eval:260, 264, 428-430.
  - `open(dirname(__file__) + "/x")` fails with an unclear NotADirectoryError under the executable's path, and existence checks silently return False.
  - D24's audit hook was dropped at +1.5%, above its 1% gate.
  - `--hints` catches 1 of 8 common patterns (see Done when).
  - A "GLIBC_2.xx not found" import error gets no hint.
  - Gaps: deps/file-relative-data-silent-and-undiagnosed (high), dx/runtime-path-hint-via-excepthook (missed, medium), native/glibc-errors-at-runtime-lack-hint (low).
- Change:
  - `_excepthook` (_fsbpyr_boot.py:277-287) walks the exception chain. For an OSError whose filename starts with `sys.executable + "/"`, it finds the owning top-level package in the payload index and prints "fsbpyr: <path> is inside the executable; <pkg> reads files next to its __file__: rebuild with --fs-package <pkg>". This costs nothing until an exception goes uncaught.
  - `PayloadExtensionLoader.create_module` (boot/_fsbpyr_rt/native.py): an ImportError that mentions "version `GLIBC", "GLIBCXX_", "CXXABI_" or "cannot open shared object file" gets the build's requirement and `os.confstr("CS_GNU_LIBC_VERSION")` appended.
  - src/fsbpyr/scan.py:
    - taints `X.__file__` (`_mentions` sees only bare names) and `__path__[0]`, and widens the `b"__file__"` early return in `file_relative_reads` to `__path__` and `PackageLoader`;
    - adds subprocess and os.exec*, cffi `dlopen` (ctypes is already covered), configparser.read, np.load, and `jinja2.PackageLoader`, flagged by the call since its arguments are plain strings;
    - reports existence checks as a separate, low-confidence result, so `file_relative_reads` and its "exists-only" negative case stay unchanged;
    - `--hints` stays opt-in (owner, v0.3.0).
  - Record the D24 amendment in docs/IMPLEMENTATION_PLAN.md.
- Done when:
  - tests/unit/boot/test_boot.py: an uncaught NotADirectoryError on `<exe>/pkg/x` prints a note that contains `fs-package` and `pkg`.
  - tests/unit/boot/test_native.py: a fake GLIBCXX dlopen error carries the hint.
  - tests/unit/builder/test_scan.py detects each of the eight patterns, one parametrized case each: `X.__file__`, `__path__[0]`, subprocess/os.exec*, ctypes/cffi dlopen, configparser.read, np.load, jinja2.PackageLoader and existence checks. The existing negative cases are unchanged.
  - The startup-budget test is unchanged.
- Effort: M. Depends on: none. Owner decision: decided (owner, 2026-10-20; D24). Three options (§5 #4):
  - (a) the zero-cost excepthook note. It meets D24's 1% gate but fires only for uncaught exceptions while fsbpyr's hook is installed. Apps that replace sys.excepthook (typer, rich) or catch the error get no note.
  - (b) measuring a C-level open hook in the launcher against D24's 1% gate.
  - (c) no runtime hint, only the wider scan and docs.

  The plan recommends (a) now, and (b) only if TST-3 finds failures that (a) misses.

### RT-4 Resolve sys.path entries that point inside the executable
- Why: `PayloadPathFinder.find_spec` returns None unless the module's directory equals the path entry (_fsbpyr_boot.py:164-166). So runtime vendoring such as `sys.path.insert(0, dirname(__file__) + "/_vendor")` works in the venv and fails in the executable. A built app cannot even `import setuptools` (reproduced with setuptools 84.0.0): it reaches `distutils.core` through `distutils.__path__ == [<exe>/setuptools/_distutils]` and `jaraco.text` through `setuptools/_vendor`. Gap: deps/syspath-subdir-entries (medium). eval:259.
- Change:
  - For a non-root directory entry D whose parts are identifiers, when the normal check fails, `find_spec` looks up the payload name `D.replace("/", ".") + "." + fullname.rpartition(".")[2]` with `_fsbpyr.find_module`.
    - Example: the finder for `setuptools/_vendor/jaraco`, asked for `jaraco.text`, looks up `setuptools._vendor.jaraco.text`.
  - It returns a spec named `fullname` with that record's origin. `submodule_search_locations` and namespace portions are built from the payload name (`<exe>/setuptools/_vendor/jaraco`).
  - `PayloadLoader.get_code`, `get_source` and `is_package` resolve records by the public name (_fsbpyr_boot.py:87-114). Such specs therefore get a small PayloadLoader subclass whose `_record` maps the public name to the payload name, so tracebacks, `inspect.getsource` and resource readers keep working.
  - `PayloadMetaFinder.find_spec` (_fsbpyr_boot.py:239-256), ahead of PathFinder, gives a payload namespace the single path `<exe>/<name>`, so a packaged top-level `jaraco` (jaraco.classes) hides the vendored portion, since setuptools appends `_vendor` before its first `import jaraco`. It builds the path with `PathFinder._get_spec(fullname, path or sys.path)` instead, which merges portions as a venv does, and keeps PayloadNamespaceLoader.
  - D6's path-hook design is kept.
- Done when:
  - Boot unit tests: after `sys.path.append(ROOT + "/pkg/_vendor")`, `import vendored`, `import vendored.sub` and, with a top-level namespace `ns` also in the payload, `import ns.mod` resolve to the files under pkg/_vendor, and `inspect.getsource(vendored.sub)` returns its source.
  - tests/e2e/test_compat.py: an app with setuptools and jaraco.classes runs `import setuptools`, and `distutils.core.__file__` contains `_distutils`.
  - tests/unit/boot/test_pth_roots.py stays green.
- Effort: M. Depends on: none. Owner decision: none (it extends D6's finder).

### DX-1 Show what the build is doing
- Why: every uv command runs with `--quiet` and captured output (staging.py:111-176). A cold build of a large app prints nothing for minutes. A failure shows a truncated command with temporary paths and the last 3000 characters of output, without naming the step. Gaps: dx/no-staging-progress-or-verbosity (medium), dx/uv-failure-context (low). eval:337, 339.
- Change:
  - staging.py `run_plan` and `rebuild` take `build()`'s `log` and log one line before each uv step ("uv: installing the locked distributions", "uv: building <project>'s wheel"), on stdout like the other build lines (cli.py:159). `StagingError` names the step.
  - `-v/--verbose` on `build` (cli.py) prints each shell-quoted uv command on stderr and lets uv's stderr through instead of capturing it ("uv failed, see its output above"). uv keeps `--quiet`, so that output is uv's errors and hints in full, not its progress.
  - build.py logs "smoke-testing <path>".
  - No `-q` until someone asks for it.
- Done when:
  - In tests/unit/builder/test_project_and_staging.py, with `-v`, the uv command line appears on stderr, and a failed step's error names the step.
  - The README option-table test passes.
- Effort: S. Depends on: none. Owner decision: none.

### SEC-3 Refuse privileged starts, check credentials in the D22/D23 parent check, and write the threat model
- Why: four gaps, none in eval.md. Gaps: security/at-secure-not-handled, security/mp-reexec-parent-credentials (medium); security/proc-required-undocumented, security/no-threat-model-document, security/way-check-acl-and-shared-group (low).
  - Under AT_SECURE (set-uid, set-gid or file capabilities), FSBPYR_*, XDG and HOME still pick the cache; the launcher reads AT_SECURE only for GC (src/fsbpyr/launcher/due.c:185).
    - Set-gid and file-capability starts keep the euid, so the invoking user owns the cache and can swap a native between its digest check and dlopen.
    - Set-uid starts accept only cache directories the euid owns (`fsb_way_check` in way.c, `owned` in cache.c), but the caller's environment still picks where a privileged process writes.
  - The D22/D23 parent check compares only the device and inode of /proc/<ppid>/exe (boot/_fsbpyr_rt/mp.py:105-113, reused by reexec.py).
  - Built apps need /proc and do not say so.
  - The security reasoning is spread across several decisions and phase notes.
- Change:
  - src/fsbpyr/launcher/main.c, right after `fsb_payload_open` (:117): if `getauxval(AT_SECURE)` is set, print "refusing a privileged start (set-uid, set-gid or file capabilities): the cache location comes from the caller's environment" and exit 70.
    - The launcher's error also names /proc when it is missing.
    - This changes the launcher, so the runtime id changes, and the `sanitized` and fuzz tests must pass.
  - mp.py `_parent_runs_this_executable` also requires the real and effective ids, the first two fields of the Uid: and Gid: lines of /proc/<ppid>/status, to equal `os.getresuid()[:2]` and `os.getresgid()[:2]`. Saved ids are not compared: execve sets the child's saved id to its effective id, so a parent that changed only its effective id would be refused. An unreadable status counts as no.
  - docs/security.md, at most 2 pages, linked from SECURITY.md (SEC-1) and README "At run time":
    - the assets, and the attackers in and out of scope;
    - what D17, D19, D20, D22, D23, D31 and this item stop;
    - the known limits: a rewritten digest is caught only by outside verification (D31's outside-key signature comes with signed releases, after the MVP); POSIX ACLs and shared primary groups on the cache root; /proc; embedded-runtime CVEs (SEC-1).
- Done when:
  - Unit tests in tests/unit/boot/test_mp.py and test_reexec.py refuse a parent with a mismatched uid in a fake status file.
  - An e2e test in tests/e2e/test_runtime_app.py (in test-ci, which CI runs as root) does `chgrp nogroup app && chmod g+s app` and asserts exit 70 and the message. The egid then differs from the real gid, so the kernel sets AT_SECURE. It skips with an allowlisted reason on a nosuid mount (such as /tmp on the dev host) and under `NoNewPrivs: 1`, which disables set-id bits.
  - CI-3's `tester` user (CI-4) runs the same test with its supplementary group.
  - The mp fixture's e2e tests still pass.
- Effort: M. Depends on: CI-3 and CI-4, for the `tester` variant only. Owner decision: decided (owner, 2026-10-20; D22, D23, and a new D-number for the AT_SECURE refusal) (§5 #5).
  - The credentials condition changes nothing that works today, because legitimate multiprocessing and re-exec children share their parent's credentials.
  - The refusal closes the escalation in about 5 lines of C. But it stops set-gid deployments and file-capability uses such as `setcap cap_net_bind_service+ep app`, which run today.
  - The alternative is an opt-in mode with a fixed root-owned cache that ignores FSBPYR_*, XDG and HOME. It keeps those uses working, at the cost of a second cache policy.

### SEC-4 Prepare the LLVM 23 toolchain before a security fix forces it
- Why: from PBS 20261013 on, the objects are LLVM 23.1.3 bitcode, and `fsbpyr runtime build` then requires clang/lld 23 or newer (pbs.py `llvm_major`; D2 allows any clang/lld ≥ 22). Debian ships llvm-toolchain-23 only in sid, so the first CPython or OpenSSL fix after 20261009 needs a toolchain change first. SECURITY.md's 7-day window (SEC-1) cannot be met unless that change is ready. Gap: security/embedded-runtime-cve-latency (high, part 2). Not in eval.md.
- Change:
  - A one-day spike, recorded in docs/spikes.md: get clang-23 and lld-23 from a durable source pinned by hash, build a runtime from the newest PBS (20261013 today), check stage2 == stage3 and tests/e2e/test_launcher_build.py::test_the_launcher_links_reproducibly, and settle how release.yml's trixie container uses the source. The candidates (§5 #11):
    - sid's clang-23/lld-23 1:23.1.3-1 from snapshot.debian.org at a fixed timestamp, in a sid image used only for `fsbpyr runtime build` (sid's packages pull sid's libc);
    - LLVM's release tarball LLVM-23.1.3-Linux-X64.tar.zst (about 1.2 GB), pinned by sha256.

    apt.llvm.org is not one: it keeps one rolling build per branch, so no version pin lasts and a stub linked with it cannot be rebuilt later.
  - tools/bootstrap.sh gains an opt-in switch that fetches that toolchain, checks its sha256s and passes it to `fsbpyr runtime build --clang`. The default stays Debian's clang-22, and pins.py stays on 20261009 (SEC-2). No long-lived branch.
  - Make the switch (pins.py to the newest PBS; release.yml and the Containerfile turn it on) when upstream-watch reports the first security-relevant PBS release after 20261009, or when trixie gets clang-23, whichever comes first.
- Done when:
  - docs/spikes.md records the commands, the source's hashes and both checks passing with clang-23 on the newest PBS.
  - With the switch on, `make test-container` (CI-4) passes; it leaves out portability, which CI-6 runs.
  - A bootstrap without the switch still links with clang-22, and the switch fails on a sha256 mismatch.
- Effort: M. Depends on: SEC-2, CI-4. Owner decision: none for D2, which already allows any clang ≥ 22. The toolchain source is a supply-chain choice for the owner (§5 #11).

### REP-1 Pin and record the release toolchain
- Why: the release inputs float or are unrecorded. Gaps: repro/release-toolchain-unpinned, security/release-build-unpinned-inputs (medium); repro/toolchain-not-recorded (low). eval:200, 280, 284.
  - Release CI installs the newest uv via curl|sh plus `uv self update` (tools/bootstrap.sh:31-32, 84-90), unversioned clang-22/lld-22 from apt, and an image pinned by tag only (release.yml:32, Containerfile:5).
  - The clang build string sits in the stub (v0.19.0 and v0.20.0: "Debian clang version 22.1.8 (1~deb13u4)"; u5 is in trixie-proposed-updates). So an unpinned from-source rebuild will stop matching.
  - pyproject.toml:15's `uv_build<0.13.0` excludes the uv 0.13.1 that releases use.
- Change:
  - release.yml and the Containerfile use `docker.io/library/debian:trixie@sha256:<digest>`.
  - tools/release/apt-snapshot holds a snapshot.debian.org timestamp. It serves later from-source verifiers (README level (b)); REP-2's compare builds both executables at the same time and needs none.
    - On tag runs only, in `build` and in REP-2's `reproduce`, release.yml sets `FSBPYR_APT_SNAPSHOT`. bootstrap.sh's `os_packages` then replaces the image's /etc/apt/sources.list.d/debian.sources (trixie, trixie-updates, trixie-security) with the snapshot archives (`[check-valid-until=no]`) and runs `apt-get update` again, with 3 retries; a snapshot line next to the live sources would pin nothing.
    - Main push and PR runs keep the live mirror, so a slow snapshot.debian.org delays only tags.
    - xz-utils, which decides fsbpyr.xz's bytes, still comes from the live mirror in the step before the checkout (release.yml:45-48); the log records its version.
    - The README records the timestamp that reproduces v0.19.0 and v0.20.0, one from before u5 reaches trixie.
  - tools/release/uv.version holds the uv version and its release tarball's sha256.
    - With `FSBPYR_UV_VERSION` set (by release CI, and by verifiers: README "Verifying a release" then names it in place of running `fsbpyr build` directly, REL-1), bootstrap.sh installs exactly that uv, fails on a sha256 mismatch, and skips `uv self update`.
    - Local bootstraps use the user's own uv, because D26 is about user builds. Since 2026-10-19 bootstrap.sh installs no tools (owner: tools never change the user's environment), so release.yml's own steps, not bootstrap.sh, set up the apt snapshot and the pinned uv above.
  - Widen pyproject.toml:15 so that it includes the pinned uv.
  - Record the toolchain:
    - registry.py `_metadata()` stores `clang --version` and `ld.lld --version` for information, not in runtime_id (D3);
    - elf.py gains a small `section(data, name)` reader (today it reads only program headers and the dynamic segment, `_segments` and `_dynamic`), so that inspection.py prints the stub's `.comment` toolchain lines;
    - the release log prints the image digest, the snapshot, uv's version and `dpkg-query -W clang-22 lld-22 xz-utils`.
  - upstream-watch.yml (SEC-2) gains three checks:
    - it compares tools/release/uv.version with `gh release view --repo astral-sh/uv`;
    - it compares the snapshot's date with today and flags one older than 90 days;
    - it compares the pinned image digest with the registry's current `debian:trixie` digest.

    It opens or updates a `toolchain-update` issue.
- Done when:
  - `grep -nE 'debian:trixie$' .github/workflows/release.yml Containerfile` finds nothing.
  - tests/integration/test_release.py asserts the `@sha256:` pin and that bootstrap.sh honours FSBPYR_UV_VERSION.
  - On a tag run, `fsbpyr inspect dist/fsbpyr` shows "Debian clang version 22.1.8 (1~deb13u4)", and `apt-cache policy clang-22` in the log shows only the snapshot origin.
  - The tag log lists every pinned version.
  - A `workflow_dispatch` run of upstream-watch on a branch with an older uv.version opens a `toolchain-update` issue.
- Effort: M. Depends on: SEC-2. Owner decision: none. D26 is untouched (users keep their uv), and so is D3 (the toolchain stays out of runtime_id).

### REP-2 Prove reproducibility on an independent worker for every main push and tag
- Why: stage2 == stage3 is checked inside one job, with one path, cache and user. Nothing rebuilds a release anywhere else, and the self-rebuild cannot cover the stub (D4). Gaps: repro/no-independent-clean-rebuild (high); maturity/no-independent-reproducibility-check (medium); repro/determinism-e2e-coverage (low). eval:199, 284, 286, 424-426, 450.
- Change:
  - A `reproduce` job in release.yml runs in parallel with `build` (no `needs`), on a fresh runner with REP-1's pinned image and uv, plus the snapshot on tags. It is a matrix keyed by Python version with one entry (3.14) today, so Phase 9's more Python versions add entries (IMPLEMENTATION_PLAN.md:1524-1527). It:
    1. installs git as build's first step does, runs `useradd -m builder`, and checks out with `fetch-depth: 0` at a path other than build's, owned by builder, so that dist/ is writable and git records the same commit and tag (release.yml:43-58);
    2. runs a new `bash tools/bootstrap.sh --packages` as root, which does only `os_packages`, with REP-1's snapshot on tags. `--from-source` skips `os_packages`, `as_root` needs root or sudo (tools/bootstrap.sh:45-52), and the image has no sudo;
    3. runs `runuser -u builder -- env HOME=/home/builder XDG_CACHE_HOME=/home/builder/c UV_CACHE_DIR=/home/builder/uv sh -c 'umask 077; bash tools/bootstrap.sh --from-source'`;
    4. uploads dist/stage3/fsbpyr.
  - A `compare` job (`needs: [build, reproduce]`, same matrix) downloads both executables into built/ and rebuilt/, runs `chmod +x` on both (artifacts do not keep file modes) and runs `cmp`. On a mismatch it prints both sizes and sha256s and `diff <(built/fsbpyr inspect --json built/fsbpyr | jq -S .) <(built/fsbpyr inspect --json rebuilt/fsbpyr | jq -S .)`, then fails. No new script.
  - `publish` needs `[build, compare]`.
  - Extend tests/e2e/test_build.py::test_builds_do_not_depend_on_the_build_environment: a second copy of clicky at another path, built with umask 077, other cache directories and one-core affinity, gives identical bytes.
  - The README's level (b) note (REL-1) becomes "checked on every tag by the reproduce job".
- Done when:
  - Every main push and tag run has `compare` green.
  - On a throwaway branch, writing `$HOME` into build_info keeps stage2 == stage3 in both jobs and makes `compare` fail with a diff that names that key.
  - The extended e2e test fails if a checkout path leaks into the output.
  - In the same run, `compare` ends within 1 minute of `build`, by job times, because `reproduce` runs alongside `build`.
- Effort: M. Depends on: REP-1, REL-2. Owner decision: none. This checks what D4's self-hosting cannot.

### DOC-1 Document how an executable differs from a venv, what the target must provide, and what is tested
- Why: users learn these differences only by hitting them, and the lists of tested distributions and packages exist only as test constants. Gaps: deps/runtime-differences-undocumented, dx/no-limitations-troubleshooting-docs, maturity/no-user-facing-scope-and-limitations (medium); deps/interpreter-config-differences-undocumented, native/stdlib-and-system-data-undocumented, native/noexec-hint-partial, security/env-var-docs, repro/offline-airgap-workflow (low); the documentation half of native/cxx-sdist-rebuild-unsupported (medium; the rest is deferred, §4). eval:264, 339, 390, 455-457.
- Change: a new docs/compatibility.md, linked from README "Supported targets" (SCOPE-1), with four parts.
  - "Differs from a venv": one row per difference, giving the symptom with its exact error text, the cause and the remedy:
    - NotADirectoryError under the executable's path (fs-packages; RT-2's note);
    - PYTHON* variables ignored and UTF-8 mode on (D14);
    - sys.prefix is the executable's directory, and files outside site-packages are not packaged;
    - no tkinter, idlelib, ensurepip, test or `_dbm`. GUI toolkits shipped as wheels (Qt, wx) are not blocked, but they are untested;
    - extracted resources are read-only;
    - no `sys.frozen` (D21);
    - `sys.executable` is the app (D22/D23, `interpreter-reexec`);
    - setuptools 84.0.0 ships no pkg_resources, so `import pkg_resources` fails; with older setuptools pins it finds no distributions (unverified). Use importlib.metadata.
  - "What the target must provide":
    - glibc ≥ 2.28 on x86-64;
    - NAT-2's report;
    - a mounted /proc;
    - a cache on a filesystem that allows exec (the noexec hint);
    - tzdata, terminfo, the CA store and openssl.cnf from the system.
    - The kernel: no minimum is established. Containers share the host kernel, so the distribution matrix tests only the CI host's kernel. The launcher falls back when O_TMPFILE is missing (`place_unnamed` in src/fsbpyr/launcher/cache.c), and close_range is compiled out (src/fsbpyr/launcher/due.c:140).
  - "Tested on": the IMAGES distributions and the MATRIX checks (packages, plus zoneinfo and paths); TST-3 adds the corpus results.
  - "Troubleshooting": symptom, cause and fix, including:
    - the cache that is refused inside CI containers (release.yml:36-41 shows the workaround);
    - C++ or Fortran dependencies built from sdists, which keep the host's build. Build them as manylinux_2_28 wheels (for example with cibuildwheel) and point uv at them.

  In the README:
  - complete the environment-variable table (FSBPYR_SELFTEST, FSBPYR_IMPORTTIME);
  - add an "Offline and air-gapped builds" note:
    - warm uv's cache, then use `--offline`;
    - build constraints for build backends come from `[tool.uv] build-constraint-dependencies` (uv.lock projects, read by `uv sync`) or `UV_BUILD_CONSTRAINT` (requirements and pylock sources, read by `uv pip install`, per `uv pip install --help` in uv 0.13.1);
    - staging passes the environment through (staging.py:172).
- Done when: tests in tests/unit/test_cli_docs.py assert that:
  - the distribution table equals `test_portability.IMAGES`;
  - the checks table equals the keys of `test_compat.MATRIX`;
  - every message that docs/compatibility.md quotes with an `fsbpyr:` prefix matches src/ by fragments, since messages are composed at run time (main.c:46 with integrity.c:189; cli.py:161): each quote is split at the prefix, paths and numbers, and every fragment of 10 or more characters occurs in a string in src/ (OS messages such as "[Errno 20] Not a directory" are not checked);
  - every variable read through `getenv("FSBPYR_`, `environ.get("FSBPYR_` or `environ.get(b"FSBPYR_` in src/fsbpyr appears in the README table (compile-time defines and header guards are not matched).
- Effort: M. Depends on: SCOPE-1, RT-1, RT-2, NAT-2 (for their final texts). Owner decision: none.

### TST-3 Build a small real-world corpus checked against each app's venv
- Why: there are 6 synthetic fixtures (340 lines of Python), and the real packaging bugs came from outside them: gnureadline in 0.15.2/0.15.3, mypyc in 0.17.1. pandas, direct use of Pillow, and entry-point plugins across distributions are untested. Gaps: tests/no-real-world-app-corpus (medium), deps/plugin-discovery-untested-across-dists (low). eval:262, 357-359, 373, 440, 457, 465, 475, 482.
- Change:
  - tests/corpus/<app>/ holds a pyproject.toml, a uv.lock and a commands.toml for each of 8-11 apps, one per risk class:
    - black (mypyc);
    - pytest with a pluggy plugin (entry points);
    - alembic (digit-named migrations);
    - a pandas CSV CLI;
    - a direct Pillow thumbnailer;
    - lxml;
    - scipy;
    - awscli, or a Flask/jinja2 PackageLoader app (data files, fs-packages);
    - an app that needs setuptools' vendoring (RT-4);
    - gnureadline;
    - a psycopg[binary] client (a database driver whose wheel bundles libpq, libssl and libcrypto in its own .libs directory).
  - tests/fixtures/projects/plugins: a host plus two plugin distributions, with a custom entry-point group and a PEP 420 namespace split across them.
  - tests/e2e/test_corpus.py builds each app, runs every command through TST-2's differential, and records whether the app needed any `[tool.fsbpyr]` setting. It runs only in full-suite.yml (CI-3).
  - The "Tested on" table in docs/compatibility.md, which DOC-1 creates, is edited by hand. DOC-1's docs tests and the one below keep it equal to MATRIX, IMAGES and tests/corpus. No generator script.
- Done when:
  - At least 8 corpus apps are green nightly through the differential.
  - The full-suite log prints the false-success count (target 0) and the first-build success rate.
  - A docs test checks that docs/compatibility.md lists exactly the tests/corpus directories.
- Effort: L. Depends on: TST-2, CI-3, RT-4, DOC-1. Owner decision: none.

## 3.2 Stage 2 — weeks 12-15: provenance, reproducibility, and the low-severity remainder

### REP-3 Make build_info a versioned manifest traceable to its inputs
- Why: build_info (build.py:280-291, assemble.py:121-130) records the lock kind, dists as name==version and an opaque 12-hex runtime id, but no hash of the lock or of any dist. It has no PBS release, no OpenSSL or SQLite versions, no settings and no schema version, so nobody can tell from an app what it contains or when to rebuild it. Dependencies built from sdists depend on the host's build backend and environment, and nothing records that. Gaps: repro/build-manifest-incomplete, security/no-component-inventory-or-sbom part (a), repro/build-deps-and-sdists-unlocked (medium); deps/builtin-rules-hardcoded, maturity/no-stability-policy (schema part) (low). eval:280-281, 383, 420-422, 449.
- Change: build.py and assemble.py add these keys, sorted and deterministic:
  - `schema: 1`.
  - `lock: {kind, path, sha256, hashed}`, where `path` is relative to the project directory.
  - `dists: [{name, version, source, artifact_sha256}]`.
    - `source` is registry, git, url, path, built-from-sdist or local. Where uv.lock or pylock lists a package's wheels, a dist whose installed WHEEL tags match none of them was built from its sdist, pure-Python ones included. The same tag match picks the hash among the package's wheels. The linux_* test (build.py `built_here_above`) stays only for the glibc rebuild.
    - The project and editable or directory members are `local`, with no `artifact_sha256`: the lock holds no hash for them, and uv builds their wheels at staging (the project's with `uv build`, staging.py `_uv_build`).
    - For built-from-sdist dists, `backend` holds the WHEEL file's `Generator:` line.
  - `runtime_inputs: {pbs: {release, name, sha256}, sysroot, layout}`.
  - `stub_sha256`, over the bytes before the payload: the stub padded to PAGE_SIZE (reader.py `stub_prefix`), with the raw cached stub padded the same way in a source build. It is identical across stages and links an app to the attested fsbpyr release. There is no builder digest, which would break D15.
  - `runtime_components`, captured once at `runtime build` by running the PBS interpreter (`ssl.OPENSSL_VERSION`, `sqlite3.sqlite_version`, `zlib.ZLIB_RUNTIME_VERSION`, `pyexpat.EXPAT_VERSION`, `decimal.__libmpdec_version__`).
    - It is stored in runtime.json through registry.py `_metadata`, with a LAYOUT_VERSION bump shared with REP-4.
    - Self-hosted builds copy it from the builder.
  - `settings: {entry, fs_packages (effective: built-in or configured), allow_system_libs, extras, include_source}`.

  Changes outside build_info:
  - inspection.py adds `"schema": 1` to `--json`, and `--files` lists path, kind, size and sha256 from the RECORD lines already in the payload.
  - Builds from sdists:
    - Staging sets SOURCE_DATE_EPOCH for the uv steps and for the sysroot rebuild, unless the user set it. The value is the project's last commit time (vcs.py already reads git), else 315532800 (1980-01-01, the earliest time a zip entry can hold).
    - The build prints one line that names the dists built here from sdists: "built on this host from sdists, not reproducible elsewhere: X, Y". It is not a warning, so `--strict` does not fail on it.
    - No build-constraints key of fsbpyr's own: uv's settings already pass through (DOC-1).
  - docs/format.md documents the schema.
- Done when:
  - A unit test shows identical build_info for identical inputs, including from two different project directories.
  - On the compat fixture, `lock.sha256` equals `sha256sum uv.lock`, and every dist but the `local` project has an `artifact_sha256` that appears in uv.lock.
  - A unit test labels built-from-sdist a py3-none-any dist that matches no lock wheel.
  - A unit test sees SOURCE_DATE_EPOCH in the staging and rebuild environments.
  - `stub_sha256` is equal in stage 1 and stage 2, and equals `head -c <payload offset> dist/fsbpyr | sha256sum`.
  - `fsbpyr inspect dist/fsbpyr` shows an OpenSSL 3.x version.
  - tests/unit/test_inspect.py covers the schema.
  - stage2 == stage3.
- Effort: M. Depends on: NAT-2, REP-1. Owner decision: none for these fields. Decided (owner, 2026-10-20; D28) only for a `git.dirty` flag, because D28 says uncommitted changes are not marked (§5 #7). If the flag is added, it counts tracked files only, with `git --no-optional-locks -c core.fsmonitor=false diff --quiet HEAD --`, so no fsmonitor hook runs, the index is not rewritten, and an untracked eval.md does not mark builds dirty.

### REP-4 Ship third-party licence notices with releases and apps
- Why: fsbpyr's release and every app statically link OpenSSL (Apache-2.0), libffi, libedit, ncurses, expat, mpdecimal, SQLite, zlib, liblzma, zstd, bzip2, libuuid, HACL* and fsbpyr's MIT launcher, and every app holds fsbpyr's MIT boot modules. Yet they ship none of those notices, only CPython's LICENSE and the dist-info licences. Gaps: repro/third-party-license-notices-missing, maturity/third-party-license-notices (missed, medium). Not in eval.md.
- Change:
  - The PBS full archive keeps the texts in python/licenses/, named by PYTHON.json's top-level `license_path` and per-extension `license_paths`. runtime/pbs.py `PbsDist.load` reads PYTHON.json but no licence field. The pinned 20261003 archive lists LICENSE.zstd.txt and LICENSE.zlib-ng.txt but ships neither, and has no HACL* text. Its libz.a is classic zlib 1.3.2 (by its members and version strings), which LICENSE.zlib.txt covers. fsbpyr supplies zstd's text (src/fsbpyr/launcher/vendor/zstd/LICENSE) and HACL*'s (from CPython's sources).
  - At runtime build (runtime/stdlib.py, registry.py), copy the texts for what runtime/pbs.py `link_inputs` links, and fsbpyr's LICENSE, into the stdlib fragment under `_fsbpyr_licenses/`. This is deterministic and shares REP-3's layout bump.
  - `fsbpyr inspect --licenses` prints them, plus each dist's notices, best effort: dist-info licenses/, files named by METADATA `License-File`, licence files in the dist-info root, and LICENSE*, COPYING*, NOTICE* and ThirdPartyNotices* in its package directories (onnxruntime 1.30.0 has no dist-info licenses/).
  - release.yml attaches THIRD_PARTY_LICENSES.txt.
  - The README says what to ship alongside an app, and that the dists' part is best effort.
- Done when:
  - `fsbpyr inspect --licenses dist/fsbpyr` prints `_fsbpyr_licenses/LICENSE.openssl-3.txt` with its Apache-2.0 text (packaging's LICENSE.APACHE alone already matches "Apache License").
  - `fsbpyr inspect --licenses <heavy>` includes onnxruntime/ThirdPartyNotices.txt.
  - An integration test in tests/integration/test_pbs_distribution.py, which runs in CI after bootstrap (`make test-ci`), finds a text, PBS's or fsbpyr's, for each static library `link_inputs` links, and names those PBS lists but does not ship.
  - The release carries the asset.
  - stage2 == stage3.
- Effort: M. Depends on: REP-3 (shared layout bump). Owner decision: decided (owner, 2026-10-20; no D-number): whether LICENSE gains a runtime-exception clause covering the launcher and the boot modules (assemble.py `boot_modules`; pyproject.toml's `license = "MIT"` then needs a LicenseRef), so that apps need not reproduce fsbpyr's MIT notice, one of about 15 they ship (§5 #10).

### REP-6 Give app builders a provenance recipe, and print the hash attestations use
- Why: D31 leaves the outside-key signature for after the MVP (IMPLEMENTATION_PLAN.md:123, :1726). fsbpyr's releases carry GitHub attestations since 0.18.1, but apps get no provenance option. `inspect` shows only the tree digest, which no attestation, checksum file or SBOM uses. Gaps: repro/no-app-provenance-or-signing, security/built-app-signing-and-provenance, maturity/d31-app-signing-untracked (medium); repro/digest-forms-and-integrity-docs (low). eval:200, 283, 455.
- Change:
  - README "Attesting and verifying your app": a GitHub Actions example of about 10 lines (`fsbpyr build`, then `actions/attest-build-provenance` pinned by SHA), and `gh attestation verify <app> --repo ORG/REPO --signer-workflow ...`. It presents the attestation as provenance that complements D31's signature, which comes after the MVP and which `inspect --verify` and self-update will check (IMPLEMENTATION_PLAN.md:1467-1468).
  - The same example lives as .github/workflows/attest-example.yml. It runs on workflow_dispatch only, builds the hello fixture, has its own `id-token: write` and `attestations: write`, and uses no cache. full-suite.yml stays read-only.
  - `inspect --verify` prints the plain whole-file `sha256` in the text output and in `--json`. It is computed only with `--verify`, so plain `inspect` stays fast.
- Done when:
  - tests/unit/test_inspect.py asserts that the printed sha256 equals `hashlib.sha256` of the file.
  - For the v0.19.0 asset, `inspect --verify` prints efb22caa…, the attestation's subject digest.
  - A dispatch run of attest-example.yml succeeds, and `gh attestation verify` accepts its output.
- Effort: S. Depends on: REL-1. Owner decision: none. D31 is unchanged: signed releases stay a post-MVP owner item.

### NAT-3 Check every native in the payload, not only the reached ones, and list system libraries loaded at run time
- Why: three gaps. eval:259, 318, 322.
  - `closures` walks only objects an extension reaches. Libraries that no extension links to (ctypes/cffi loads, fs-package libraries) and ELF executables shipped in wheels are packaged unchecked.
  - System libraries an app opens at run time (`ctypes.CDLL("libmagic.so.1")`, `ctypes.util.find_library`) go unreported.
  - RPATH/RUNPATH entries that are absolute or relative to the working directory are skipped at build time (natives.py `_search_path`), but ld.so searches them on the target.
  - Gaps: native/unreached-natives-and-system-dlopen-unchecked (medium); native/elf-executables-in-wheels, native/non-origin-rpath-silently-ignored (low).
- Change:
  - natives.py runs a tolerant `_direct_dependencies` pass over the unreached natives, those neither reached nor extension modules (classify.py `_hint_unused_natives`'s test, without its fs-package exclusion), and over ELF executables among the resources (PT_INTERP or ET_EXEC).
    - elf.py learns to read e_type (`_segments` discards it) and PT_INTERP (elf.py:17 defines only PT_LOAD and PT_DYNAMIC). tests/support/elf.py `make_elf`, fixed at ET_DYN without PT_INTERP, gains options for both.
    - An unresolved NEEDED gives a warning, and system needs feed NAT-2's report.
    - An ELF executable gives "<dist>: <path> is an executable; add <pkg> to fs-packages if the package runs it" (until RT-3 lands).
    - RPATH/RUNPATH entries not based on $ORIGIN give a warning, stronger for relative ones. No patching (D9).
  - With `--hints`, compiler.py's existing hint pass also collects literal sonames passed to `ctypes.CDLL`, `cdll.LoadLibrary` and cffi `dlopen`, and names passed to `ctypes.util.find_library`, recorded as `lib<name>` (`find_library("magic")` gives libmagic). NAT-2's system report lists them under "may load from the system (heuristic)", with no warning. It stays under `--hints` because the scan costs build time.
- Done when: unit tests with tests/support/elf.py `make_elf` pass:
  - an unreached library that needs libmissing.so.1 warns;
  - `runpath="lib:/opt/x"` warns;
  - in a fake dist, an ET_DYN file with PT_INTERP (a PIE) and an ET_EXEC file each warn;
  - a module calling `find_library("magic")`, built with `--hints`, lists libmagic in build_info.system;
  - heavy's report keeps its library keys and highest versions; its file lists gain onnxruntime/capi/libonnxruntime.so.1.30.0, which no extension loads (tests/e2e/test_natives.py:139).
- Effort: M. Depends on: NAT-2. Owner decision: none.

### RT-3 Keep helper executables executable (D27 amendment)
- Why: resources default to mode 0o644 (payload/spec.py:295) and are extracted as 0400 (src/fsbpyr/launcher/cache.c:545). Only fs-package files keep the wheel's exec bit (classify.py:445-449). So running `files(pkg) / "bin/tool"` works in the venv and fails in the executable. Gaps: deps/resource-exec-bit-dropped, native/elf-executables-in-wheels (mode part) (low). eval:322.
- Change:
  - classify.py gives resource files that had owner-exec in the wheel mode 0o755, in both places that build them: `_file_done` and `add_large` (files over 16 MiB). src/fsbpyr/launcher/cache.c already applies `S_IRUSR | (mode & S_IXUSR)`, so those copies become 0500 and are still never writable.
  - NAT-3's ELF-executable warning keeps its fs-packages advice only for paths built from `__file__`; importlib.resources paths need none (D27).
  - Generation and resource ids change once.
- Done when:
  - tests/unit/builder/test_closure_and_classify.py: an executable data file keeps 0o755, under and over 16 MiB.
  - In an e2e test, `os.access(str(files(pkg) / "bin/tool"), os.X_OK)` is true and the tool runs; a generated executable resource over 16 MiB extracts as 0500.
- Effort: S. Depends on: none (if it lands first, NAT-3 writes the narrowed text). Owner decision: decided (owner, 2026-10-20; D27): extracted files that were executable in the wheel would become 0500 (owner-exec, still read-only) instead of 0400 (§5 #6).

### RT-5 Map distutils to setuptools' copy, and warn about pkg_resources
- Why: `distutils-precedence.pth` is treated as redundant (classify.py:40 `_REDUNDANT_PTH`). So in executables that package setuptools, `import distutils` before `import setuptools` fails, while `_distutils_hack` makes it work in the venv. After RT-4, an import after setuptools works through sys.modules. setuptools 84.0.0 ships no pkg_resources; with older pins, pkg_resources finds no distributions (inferred from the code, not run). Gaps: deps/distutils-precedence-pth-not-redundant (missed, low), deps/pkg-resources-unsupported (low).
- Change:
  - `PayloadMetaFinder.find_spec` (_fsbpyr_boot.py:239-256): when `_fsbpyr.find_module("distutils")` is None and setuptools is packaged, `distutils` gets a spec whose loader imports `setuptools._distutils` and returns it from `create_module`, as `_distutils_hack` does. Submodules such as `distutils.version` then resolve through its `__path__` and RT-4's mapped path entry. This costs nothing unless the import would miss.
  - There is no `SETUPTOOLS_USE_DISTUTILS` handling. 3.14 has no stdlib distutils, and D14 keeps built apps' knobs to FSBPYR_*.
  - classify.py warns once, from one RECORD check, when a packaged setuptools contains pkg_resources: "pkg_resources finds no distributions in executables; use importlib.metadata".
  - No pkg_resources adapter unless a TST-3 app needs one.
- Done when:
  - A boot unit test with a fake `setuptools._distutils` resolves `import distutils.version` before setuptools is imported.
  - The classify warning has a unit test.
  - A fixture command that imports distutils before setuptools passes TST-2's differential.
- Effort: S. Depends on: RT-4, TST-2. Owner decision: none.

### RT-6 Warn instead of silently dropping what the venv would have
- Why: RECORD entries outside site-packages (data_files, headers) and sourceless .pyc modules are dropped without a word (classify.py:143-147, 243-244). The always-on hint for unused natives names the distribution (classify.py:493-497), which config.py's `_package` rejects unless it is a dotted identifier (not `foo-libs`). Gaps: deps/outside-site-packages-silently-dropped, deps/unimportable-sourceless-and-huge-modules, deps/fs-packages-remedy-gaps (low). eval:266, 428-430.
  - .py modules over 16 MiB are kept as data that cannot be imported, with a warning (classify.py:56, 268-271). The cut-off serves D38(2): compile() needs about 100-165 times the source size in memory (PBS CPython 3.14.8: 1.6-2.75 GB just under 16 MiB), so a module just under the cut-off already passes 1 GB in one compile worker. D38(1) rules out size limits of fsbpyr's own, and D38 does not list this one.
- Change: in classify.py:
  - `add_dist` warns once per distribution about non-script files outside site-packages ("<dist>: N files under share/ are not packaged; sys.prefix lookups will not find them"). Scripts stay silent.
  - Warn about a .pyc outside `__pycache__`.
  - The hint names a value that fs-packages accepts, or says that none exists.
  - Packaging itself is unchanged (D5).
- Done when: unit tests in tests/unit/builder/test_closure_and_classify.py pass:
  - a `../../../share/x` entry warns, and `../../../bin/x` does not;
  - a top-level legacy .pyc warns;
  - the foo-libs hint names a value that config.py accepts.
- Effort: S. Depends on: none. Owner decision: decided (owner, 2026-10-20; D38) for the 16 MiB compile cut-off (§5 #9): record it in D38 as a consequence of (2) that limits (1), with no code change. Compiling such modules from one whole read would break (2).

### PERF-1 Guard the startup properties with counts in CI
- Why: src/fsbpyr/launcher/module.c `stats()` reports the read counters (`reads`, `stored_bytes`, `decompressed_bytes`) through `FSBPYR_TRACE`, but no test asserts them. test_launcher_block_cache_rereads_evicted_blocks checks only the bytes read back, so a decoder block cache that thrashes passes every test. Size, deterministic under D15, has no guard. Gaps: perf/read-counters-never-asserted (missed), perf/no-size-regression-guard (missed, low). eval:201.
  - The integrity check's thread count and the per-run cache check (53.5 ms for torch's 12,151 files) stay invisible in released executables: no counter shows them. A new counter changes the launcher digest, and with it every app's runtime id (registry.py `runtime_id`), so it waits until D31 or D39 work needs it. perf/integrity-cost-unguarded (thread-count part) and perf/cache-check-not-observable (medium) stay open.
- Change: no launcher change. New tests read the existing counters:
  1. M2, in test-ci (tests/e2e/test_runtime_app.py): the no-op app with and without a large unused synthetic dist (tests/support/apps.py) reports the same `reads`, `stored_bytes` and `decompressed_bytes`.
  2. In test-ci: the no-op app's size is within ±3% of a recorded constant. It sees a change to the stdlib selection only with a freshly built runtime, because `runtime_id` does not hash the selection and the e2e `runtime` fixture (tests/e2e/conftest.py) reuses a cached runtime. CI builds the runtime fresh (release.yml: "No cache"); locally the change needs the LAYOUT_VERSION bump that registry.py asks for.
  3. In test_natives.py, which test-ci does not run (CI-3's full suite will): the `heavy np` run's `decompressed_bytes` is at most a recorded value. Whether `heavy np` thrashes at BLOCK_CACHE = 4 is unverified; if it does not, a compat command takes its place. That a second run extracts nothing is already asserted (`test_first_run_extracts_only_the_closure_it_needs`).
- Done when:
  - Test 3 fails when BLOCK_CACHE (src/fsbpyr/launcher/payload.c:235) is set to 4.
  - test-ci, with a freshly built runtime, fails when "test" is removed from EXCLUDED_DIRS in runtime/stdlib.py.
- Effort: S. Depends on: CI-1 (so the e2e guards cannot pass by skipping). Owner decision: none. This applies D37: counts, not timings.

### PERF-2 Measure cold starts and the integrity check's cost per GB
- Why: two measurements are missing. eval:202, 306.
  - D31 hashes the whole file at every start, yet starts with a cold page cache were never measured. Phase 10 left them out as needing root (performance.md:465-466, IMPLEMENTATION_PLAN.md:1602).
  - The check after D36 (files read, not mapped) is measured only on up to 8 threads with SHA-NI, from 28 to 234 MB: D36, and performance.md "Resident memory with 0.9.1 (D36)" and "Other tools" (0.19.0: 1.75 ms for `hello`, 7.4 ms for aiagent). One-core and no-SHA-NI figures exist only for the mmap-based 0.6.0/0.7.0 launcher (performance.md "The integrity check"), and no check above 234 MB was timed on its own ("Very large files" times whole runs). The README "Integrity" section (:262-263) still cites the 0.6.0/0.7.0 figures.
  - Gaps: perf/cold-start-unmeasured (medium), perf/integrity-cost-unguarded (per-GB part, medium), perf/no-reproducible-bench-kit (the committed-command part, medium).
- Change:
  - spikes/bench.py gets `--cold` and `--cache DIR`. In cold mode it skips its five warm-up runs, and before each sample it calls `posix_fadvise(POSIX_FADV_DONTNEED)` on the executable and, separately, on every file under DIR. No root is needed, but the files must be on a disk file system and synced first: DONTNEED leaves dirty pages, and on tmpfs it evicts nothing (as uid 1000, a file on XFS went to 0 resident pages; one on tmpfs stayed resident).
  - Measure warm starts against cold ones (executable evicted; cache evicted) for the no-op app and `heavy np`, at 0.20.0 or later.
  - Measure the integrity check (`integrity_ns` from FSBPYR_TRACE) for 28 MB, 234 MB and 1 GiB apps:
    - with up to 8 threads, and with 1 core via taskset;
    - both again without SHA-NI, forced with `OPENSSL_ia32cap=:~0x20000000` as for the 0.6.0/0.7.0 figures.
  - docs/performance.md gets "Cold start" and "Integrity check" tables, each with a "Reproduce:" line. The README "Integrity" section cites the integrity table, and Phase 10's notes that a cold page cache needs root (performance.md "Other tools", IMPLEMENTATION_PLAN.md Phase 10) point to the cold table.
  - No JSON output, RSS, page-fault, CPU-quota or upgrade campaigns (§4) unless the cold numbers raise a question about D31.
- Done when:
  - performance.md has both tables, measured at 0.20.0 or later, each with a "Reproduce:" line that runs.
  - The README "Integrity" section cites the new integrity table, and both Phase 10 notes point to the cold table.
- Effort: M. Depends on: none. Owner decision: none. D37 dropped "the benchmark harness"; two flags on the existing spike script are not that harness. Changing D31's scope, or capping threads by cgroup quota (D39), stays the owner's call if the data warrants it.

### DX-2 Fail early, completely and politely
- Why: several small failures. Gaps: dx/late-entry-and-output-validation, dx/first-missing-native-only, dx/unexpected-exception-traceback, dx/extra-flag-semantics-pip-sources, deps/extra-help-inaccurate-for-pip-locks (low). eval:337.
  - Several console scripts, or an `-o` that is a directory, are reported only after the whole uv staging (build.py:209 vs :228-243).
  - Missing native libraries are reported one per build.
  - An unexpected exception ends in a raw traceback with no context.
  - `--extra` installs nothing for pylock or requirements sources.
- Change:
  - build.py checks two things right after `Project.load`, before `find_uv`:
    - two or more static `[project.scripts]` and no `--entry` raise the existing several-scripts ClosureError. With none, as for dynamic scripts, setup.py and Poetry projects (project.py:45-56), `resolve_entry` still decides after staging;
    - an explicit `-o` that is a directory.
  - natives.py `closures` collects every missing soname and raises once, with a ready-to-paste `allow-system-libs = [...]` line.
  - cli.py `main` gets a last-resort handler. It prints the traceback, then "fsbpyr: internal error (fsbpyr X.Y.Z): ...; please report at <issues URL> with the output above and `fsbpyr inspect --json`", and exits 1. It adds no new environment variable. PayloadError and BytecodeError join the handled errors.
  - `--extra`:
    - its help says that requirements and pylock files must already contain the extra's dependencies;
    - extra names are checked against `project.extras` before staging only when `[project]` exists and its `dynamic`, which Project then loads, does not name `optional-dependencies`; otherwise after staging, against the installed project's `Provides-Extra`.
- Done when: unit tests pass for each case:
  - a two-script pyproject, with a uv stand-in that fails if called, gives the several-scripts error;
  - a pyproject with `dynamic = ["optional-dependencies", "scripts"]`, and a setup.py project, pass both pre-checks with `--extra fancy`;
  - two missing sonames appear in one error;
  - an unexpected exception prints the traceback and the report line, and exits 1.
- Effort: S. Depends on: none. Owner decision: none.

### DX-3 Document exit codes, environment and configuration in the man page
- Why: several things cannot be learned from the binary. Gaps: dx/exit-codes-undocumented, dx/manpage-missing-config-env-files, dx/missing-runtime-ux (low). eval:337.
  - manpage.py:19-38 renders only NAME to EXAMPLES.
  - The `[tool.fsbpyr]` keys live in a hand-maintained list (config.py:87-91).
  - Built apps' exit 70 is documented only in docs/format.md.
  - `runtime list` hides the embedded runtime (registry.py:321-327 reads only the cache), and the no-runtime message (registry.py:268-270) has drifted from the README (:92).
- Change:
  - One key table in config.py feeds `_from_tool`, the unknown-key message and a CONFIGURATION section.
  - manpage.py `render` adds three sections:
    - EXIT STATUS, documenting today's codes: 0; 1; 2 for usage (D40); 130; and 70 for built apps. No new code.
    - ENVIRONMENT.
    - FILES.
  - registry.py `list_runtimes` shows the embedded runtime first.
  - The no-runtime message (registry.py:268-270) points to the release binary.
- Done when:
  - `fsbpyr man | grep -c '^.SH'` is at least 10.
  - tests/unit/test_cli_docs.py checks that every key appears, and every variable DOC-1's getenv/environ.get rule finds (today FSBPYR_CACHE_DIR, FSBPYR_GC, FSBPYR_TRACE, FSBPYR_IMPORTTIME and FSBPYR_SELFTEST).
  - The `runtime list` test in tests/unit/test_cli.py shows the embedded runtime.
- Effort: S. Depends on: none. Owner decision: none; the commands stay the one source of the help text and the options (D40), and the new sections come from config.py and manpage.py, as EXAMPLES comes from cli.py.

### DX-5 Make inspect show the build options and where the bytes go
- Why: inspect omits the build options (extras, fs-packages, allowed system libraries, include-source). Nobody can see where an executable's bytes go, including its 27.6 MB floor. Gaps: dx/inspect-omits-entry-options-external-libs (options part; NAT-2 covers the entry and the external libraries), perf/size-floor-no-breakdown (medium). eval:303, 320, 337.
- Change:
  - inspection.py's text output and `--json` show REP-3's `settings`.
  - They also show a breakdown of the stored size with these rows: the stub; the zero padding to the page boundary; the payload header; the index and string tables; the BLOB_ALIGN padding; the stdlib and boot modules; and, per distribution, its code, sources, resources and natives. It is computed from the existing reader records (writer.py `assemble` shows the layout), with no format change.
    - Blocks mix owners, and a block member's record has no stored size (writer.py `_Blobs`). Each block's stored size is split by its members' raw sizes, and the rounding remainder goes to its first member.
    - A blob shared by several owners counts for the first of them in path order.
  - The stdlib's top-level module list stays as D28 specifies.
  - docs/performance.md gets a "Where the bytes go" table for hello, heavy and fsbpyr itself.
- Done when:
  - tests/unit/test_inspect.py::test_json_describes_everything asserts `sizes` and `settings`.
  - hello's rows sum to the file size minus TRAILER_SIZE.
  - performance.md has the table.
- Effort: S. Depends on: REP-3. Owner decision: none.

### CI-5 Add test timeouts and a hardening test of the built stub
- Why: some e2e runs of built apps have no timeout, so a hang blocks a CI job until its overall limit (75 minutes in release.yml); many others already pass one. Nothing checks that the built stub keeps its hardening flags. Gaps: tests/no-test-timeouts (missed, low), security/hardening-not-verified-on-binary (low). eval:365-367.
- Change:
  - Add `timeout=60` to tests/e2e/test_edges.py `_outcome`, test_runtime_app.py `_run`, test_build.py `_run`, and test_imports.py's four `subprocess.run` calls.
  - Add a test that runs binutils' `readelf -lW -dW` and `objdump -T` on the built stub. binutils is already in CI as a dependency of clang-22; elf.py reads no symbol table. It asserts five properties: PIE (FLAGS_1 PIE), BIND_NOW, GNU_RELRO, a non-executable GNU_STACK, and `__stack_chk_fail`. The v0.19.0 stub has all five.
- Done when:
  - Removing `-Wl,-z,now` from LINK_FLAGS in runtime/stub.py fails the hardening test.
  - The timeouts appear in the four places, and test-ci stays green.
- Effort: S. Depends on: none. Owner decision: none.

### DOC-2 Bring the specs back in line with the code, and propose a stability policy
- Why: parts of the specs disagree with the code, and nothing states what stays stable before 1.0. Gaps: maturity/plan-doc-drift, dx/plan-doc-drift, perf/perf-doc-drift (plan part), maturity/no-stability-policy, maturity/single-maintainer-unreviewed-velocity (low). eval:203.
  - D1 says "about 800 LOC" (IMPLEMENTATION_PLAN.md:93), but the launcher's .c and .h files outside vendor/ have 3,971 lines.
  - D37 (:129), §3.3 (:238) and §8 (:1653) say the startup budget is 1.2x. The owner raised it to 1.5x on 2026-10-09 (docs/performance.md:37-40), and tests/e2e/test_runtime_app.py:299 enforces 1.5x.
  - Phase 9's self-update and install.sh rely on the asset names and URLs (IMPLEMENTATION_PLAN.md:1499-1503, :1551-1555). The old fsbpyr verifies its download in-process (:1448) with a reader that rejects any format version outside `READABLE_VERSIONS` (payload/reader.py:153, :656). Once self-update ships, a format_version bump would stop every older release from updating.
- Change:
  - Append dated notes to D1 and D37 (1.5x, citing performance.md) rather than rewriting them, and update §3.3 and §8 to 1.5x.
  - A doc-lint test in tests/unit/test_cli_docs.py checks that every `src/...` path in docs/*.md exists, and that format.md's version equals FORMAT_VERSION. Both hold today; the test guards them.
  - Mark breaking changes with `**Breaking:**` in the CHANGELOG.
  - README "Versions" (README.md:335) states what is stable before 1.0, as the owner decides (§5 #8). It covers at least what self-update and install.sh rely on: the asset names and URLs (`fsbpyr` and `fsbpyr.xz`; later `fsbpyr-3.12` and `fsbpyr-3.13`, then the target-named assets such as `fsbpyr-x86_64-linux-gnu` and `fsbpyr-3.12-aarch64-linux-musl`, each with its `.xz`, of which `fsbpyr` and `fsbpyr.xz` stay copies; install.sh; and the record of which asset an executable was released as, which a variant's self-update reads; IMPLEMENTATION_PLAN.md:1499-1503, :1548-1555), and the trailer and digest layout an older fsbpyr's in-process `inspect --verify` reads, so a later format_version bump must keep that check working.
  - Proposed 1.0 criteria, for the owner rather than as a done-when: CI-3's gate green for 30 days, TST-3 with 0 false-success builds, and REP-2 green on every tag.
- Done when:
  - the doc-lint test passes;
  - D1 carries the dated note, and `grep -n '1\.2×' docs/IMPLEMENTATION_PLAN.md` shows 1.2× only as dated history;
  - README "Versions" matches the owner's choice in §5 #8.
- Effort: S. Depends on: REP-3 (the schema that the stability statement names). Owner decision: decided (owner, 2026-10-20; D28 extension): what is stable before 1.0, including what self-update and install.sh rely on, and the 1.0 criteria (§5 #8). The 1.5x note copies a recorded owner decision and needs no new one.

## 3.3 Below the cut line: specified, not scheduled

These items wait for a user request, an incident, or the end of Stage 2. Each one says why it
waits.

### NAT-4 Let users check a target machine before they deploy
- Why: nothing on the target says whether every extension will load, so a missing system library shows up only when a lazy code path first imports it. Gap: native/no-target-preflight (missed, medium). eval:197-198, 416-418. It is below the cut line because NAT-2's report already names every system library and version an app needs, so a user can compare it with the target by hand. Revisit when a user asks, or when TST-3 finds a lazy-import failure.
- Change:
  - `FSBPYR_SELFTEST=natives` (D14 allows FSBPYR_* knobs). Boot syncs every native closure into the cache and runs the loader in ldd's trace mode, `LD_TRACE_LOADED_OBJECTS=1 /lib64/ld-linux-x86-64.so.2 <file>`, on each extension, without running PyInit. `--list` does not fit: it stops at the first missing library with exit 127. On the host's glibc 2.41 no constructor ran in either mode; glibc 2.28 is untested.
  - It parses the "=> not found" and "version `X' not found" lines, because the loader exits 0 for both in trace mode. It prints one line per missing library or version and exits non-zero.
  - Documented in DOC-1's docs/compatibility.md.
- Done when:
  - A unit test with faked loader output, copied from those real lines, passes.
  - In CI-6, heavy exits 0 on debian:10. In a `--rm` debian:10 container with /usr/lib/x86_64-linux-gnu/libstdc++.so.6* removed, it exits non-zero and names libstdc++.so.6. (busybox:glibc lacks the stub's libdl, libutil and librt, so heavy fails in ld.so before boot.)
- Effort: M. Depends on: NAT-2, CI-6, DOC-1. Owner decision: none.
- Done 2026-10-21: `_fsbpyr_rt/selftest.py`, called from the boot code for `FSBPYR_SELFTEST=natives` (any other value keeps its meaning). The loader is the executable's own PT_INTERP, read from /proc/self/exe, so aarch64 and x86-64 need no table. Missing items are reported once each, with the extensions that need them. Unit tests with faked loader output: tests/unit/boot/test_selftest.py. End to end: tests/e2e/test_natives.py runs heavy on this host (exit 0). The debian:10 checks (heavy passes on every image; with libstdc++.so.6 removed it exits non-zero naming it) are in tests/e2e/test_portability.py and were NOT run (podman matrix). Not run either: glibc 2.28's loader in trace mode (untested, as noted above), and an aarch64 host.

### REP-7 Publish a CycloneDX SBOM for fsbpyr's release
- Why: vulnerability scanners cannot see the statically linked OpenSSL, SQLite, expat or libffi inside fsbpyr. Gaps: repro/no-sbom, security/no-component-inventory-or-sbom part (b) (medium); maturity/release-artifact-extras (low). eval.md asks for a manifest (eval:280), not an SBOM by name. It is below the cut line because REP-3's build_info, with `runtime_components`, already tells users what the release embeds and when to rebuild. This is the same reason §4 gives for deferring app SBOMs. Revisit when a user's scanner asks for an SBOM.
- Change:
  - tools/release/sbom.py reads `fsbpyr inspect --verify --json` and writes deterministic CycloneDX 1.6 JSON, as a pure function of that output: REP-3's build_info plus REP-6's whole-file sha256. It holds:
    - the executable with its plain sha256;
    - CPython and each runtime component, each with a CPE (e.g. `cpe:2.3:a:openssl:openssl:<version>`) and a purl where one exists. Without a CPE, scanners such as grype match nothing for these C libraries;
    - `pkg:pypi` purls with hashes;
    - fsbpyr as `pkg:github/devitops-com/fsbpyr@vX.Y.Z`.
  - `build` generates it from the frozen artifact, and `reproduce` generates it from its rebuild. REP-2's `compare` job compares the two, and `publish` attests build's copy with `actions/attest-sbom` (pinned by SHA) and uploads it as `fsbpyr.cdx.json`.
  - One format only, no SPDX. App SBOMs stay deferred (§4).
- Done when:
  - `gh attestation verify fsbpyr --repo devitops-com/fsbpyr --predicate-type https://cyclonedx.org/bom` succeeds.
  - `compare` shows byte-identical SBOMs.
  - `jq -r '.components[] | select(.name == "openssl") | .cpe' fsbpyr.cdx.json` prints a CPE with the embedded OpenSSL version, and the same holds for SQLite.
  - `grype sbom:` on a copy whose OpenSSL version is set to 3.0.0 lists OpenSSL vulnerabilities (a manual check, recorded in the commit).
- Effort: M. Depends on: REP-3, REP-2, REP-6 (the plain sha256). Owner decision: none.
- Done 2026-10-21 (not yet released): tools/release/sbom.py, tests/unit/test_sbom.py (a fixed inspect JSON: determinism, CPEs, purls, the fsbpyr component) and an SBOM of the hello executable in tests/e2e/test_build.py; release.yml makes the SBOM in `build` and `reproduce`, `compare` checks the two, and a new matrix job `sbom` attests each to its executable with actions/attest-sbom v4.1.0 (one SBOM per call, hence a job per executable and not a step in `publish`, which needs it); workflow-text tests in tests/integration/test_release.py. Since the plan: one SBOM per long-named executable (`<asset>.cdx.json`, six per release), none for the short names, whose digests the long names' attestations cover. CPE vendors (libexpat_project, mpdecimal_project) are NVD's names from memory, unchecked. Only a release run shows: `gh attestation verify ... --predicate-type https://cyclonedx.org/bom`, the published files, byte-identical SBOMs in `compare`, `jq` on the published file, and the grype check (OpenSSL set to 3.0.0). attest-sbom is deprecated upstream in favour of actions/attest, which v4.1.0 wraps.

### CI-7 Close the remaining low test gaps
- Why: five low-severity gaps: tests/thin-property-based-testing, security/fuzz-scope-and-corpus (build-time parsers), tests/no-interrupted-build-or-kill-tests (signal part), security/layer4-interrupted-build-and-low-disk, security/no-tsan-no-c-static-analysis (static-analysis part). eval:355-357, 365-367. It is below the cut line because all five are low. BLD-1 already covers a failed write, and CI-3 fuzzes continuously and keeps the corpus.
- Change:
  - Property tests:
    - a derandomized Hypothesis "ci" profile in tests/conftest.py;
    - a property that mutated ELF bytes make elf.py raise only ElfError.
  - cli.py handles SIGTERM like SIGINT, so `write_executable` cleans up.
  - An RLIMIT_FSIZE test of a build that runs out of disk.
  - `make check-c` (clang `--analyze` over src/fsbpyr/launcher/*.c) in full-suite.yml, informational at first.
- Done when:
  - Each test exists and runs: in test-ci where cheap, otherwise in full-suite.
  - The analyzer report appears in the nightly log.
- Effort: M. Depends on: CI-3. Owner decision: none.
- Done 2026-10-21:
  - tests/conftest.py registers the derandomized Hypothesis profile `ci`, loaded when CI is set. tests/unit/test_elf_mutation.py mutates the bytes of four small ELFs and requires every elf.py reader to return or raise ElfError (test-ci).
  - cli.py's `main` handles SIGTERM as SIGINT (exit 130), restoring the old handler on return. A unit test in test_cli.py (test-ci) and tests/e2e/test_interrupted_build.py, which kills a real `fsbpyr build` with SIGTERM while the new executable's smoke run waits (full-suite).
  - The same file runs a build under RLIMIT_FSIZE (1 MiB, SIGXFSZ ignored): exit 1 with "File too large", the old output kept, no temporary.
  - `make check-c` (tools/check-c.py) runs `clang --analyze` over the eight launcher sources with the launcher's flags and the cached PBS headers; full-suite.yml's suite job runs it informationally (continue-on-error), so its report goes to that job's log, not a nightly run's. First local run: four findings (cache.c:471 null argument, cache.c:915 fstatat on a negative descriptor, way.c:66 possible leak, way.c:141 dead store), not yet triaged.

---

## 4. Deferred or rejected

- **Hook/plugin system and a rules file** (eval:261, 382, 448; deps/builtin-rules-hardcoded): rejected. One table of 6 names plus per-project config needs no extension API, and Phase 10's three apps needed no fsbpyr options (performance.md:469-476). REP-3 records the rules a build applied, and DOC-3 tests the README list.
- **Hidden-import and dynamic-import discovery** (eval:259, 447): rejected as not applicable. The whole lock is installed and packaged. What an app can still miss is the stdlib the runtime leaves out (runtime/stdlib.py:21-24: test, idlelib, tkinter, turtledemo, ensurepip): such an import builds without a warning and fails at run time. DOC-1 documents them.
- **Pruning unused modules and dropping unreachable distributions** (eval:303): rejected. It brings false-success builds back and conflicts with the Phase 4 decision (IMPLEMENTATION_PLAN.md:649-651). Dev and test dependency groups are already left out (staging.py:119; D5).
  - Trimming the stdlib or sources would touch D2, D3, D12 and D29.
  - An opt-in exclude for dependencies' own tests touches no decision. It is deferred until DX-5's breakdown shows what it would save.
- **A build-time unresolved-import check, and an import-every-module smoke sweep** (deps/no-unresolved-import-check, low): deferred. TST-2's and TST-3's venv differential plus BLD-2's smoke-args catch the same failures, without false alarms from optional backends and test modules. Revisit if TST-3 finds import-time failures the differential misses.
- **A pkg_resources finder/provider adapter**: deferred. RT-5 adds a warning and DOC-1 documents it, unless a corpus app needs more.
- **`build --json` / `--report FILE` and stable warning codes**: deferred. `inspect --json` is already the machine-readable result, and BLD-3's `--strict` covers CI.
- **`-q/--quiet`, and a new exit status for a verify mismatch**: deferred until someone asks. DX-3 documents today's codes.
- **A separate `fsbpyr verify` command** (eval:334): rejected. It would duplicate `inspect --verify`, which Phase 9's self-update also runs.
- **A compatibility dashboard service** (eval:373): rejected. A test-checked docs/compatibility.md is enough.
- **Cross-compilation** (eval:388): deferred to Phase 9's platform step. Today there is one target triple (registry.py:98-101), buildable from any Linux x86-64 host. Phase 9 plans Linux arm64 and macOS arm64 CI runners and a reproduction check per target (IMPLEMENTATION_PLAN.md:1544); whether an x86-64 host may build apps for the other targets is a question for that step's spike.
- **Python versions, platforms and the comparison with other tools** (eval:308, 385, 389-408, 443, 471, 485-486; native/musl-undetected): decided by the owner on 2026-10-18 (IMPLEMENTATION_PLAN.md Phase 9, Phase 10 and §11), not by this plan. Phase 9, after the MVP: self-update, then 3.12-3.14 with one fsbpyr per version, then Linux x86-64 and aarch64, each with glibc and musl, and macOS arm64; no Windows. Facts for Phase 9's spikes are in §3. Phase 10 is done (PyInstaller onefile, pex, PyApp; no `--onedir`): this plan adds no PyInstaller run of its own, in benchmarks or in the differential tests, and PERF-2 measures the cold page cache. Until Phase 9 ships, SCOPE-1 documents the 3.14-only, glibc-only target.
- **A committed driver for Phase 10's comparison** (perf/no-reproducible-bench-kit, apps part): deferred. Phase 10 is done (IMPLEMENTATION_PLAN.md:1584-1603) and docs/performance.md records each tool's version and the method (:449-467), but no script, and aiagent is not public. PERF-2's "Reproduce:" lines cover only its own tables. Revisit if the owner re-runs Phase 10.
- **GUI features** (eval:390): rejected for now. tkinter and idlelib are left out of the runtime. Qt/wx wheels are not blocked but untested, and DOC-1 says so.
- **A free-threaded runtime** (maturity/free-threaded-refused): deferred (D35); documented in SCOPE-1.
- **C/C++ sdists rebuilt against the baseline** (native/cxx-sdist-rebuild-unsupported, medium): deferred, a former L-sized spike.
  - DOC-1 recommends manylinux_2_28 wheels for such dependencies, and NAT-2's GLIBCXX warning makes the failure visible at build time.
  - Revisit when a user or a TST-3 app needs a C++ sdist. A build-only libstdc++ pin must stay separate from the runtime's sysroot pin, because runtime_id hashes the sysroot pin's identity (registry.py:104-108; Phase 6c).
- **An end-to-end test of the system CA store** (native/system-ca-not-e2e-tested, low): deferred. A real TLS handshake needs a per-distribution CA install in each image, and the check that the merged plan proposed (`cert_store_stats()['x509_ca'] > 0`) fails on Debian and Ubuntu, where OpenSSL uses the hashed capath. Unit tests of `fixups.fallback_bundle` cover the selection logic. Revisit if a CA-store bug is reported.
- **A signature checked with an outside key** (D31): a post-MVP owner item, outside this plan (IMPLEMENTATION_PLAN.md D31 and §11). Once releases are signed, `inspect --verify` checks the signature and self-update gains tampering detection with it (Phase 9). REP-6's detached attestations supplement it; they do not replace it or close D31.
- **The builder's digest in build_info**: rejected. It breaks D15's stage2 == stage3 fixed point; REP-3's `stub_sha256` links an app to its release instead.
- **The clang/lld version in runtime_id**: rejected (D3). REP-1 records it for information only.
- **Pinning or bundling uv for user builds**: rejected (D26). REP-1 pins uv only in fsbpyr's own release CI.
- **A `[tool.fsbpyr] build-constraints` key**: rejected. uv's own `[tool.uv] build-constraint-dependencies` (uv.lock projects) and `UV_BUILD_CONSTRAINT` (the other lock sources) already reach staging (DOC-1).
- **App SBOMs (`inspect --sbom`), SPDX output, and an in-toto `build --provenance` statement**: deferred. REP-3's manifest and `runtime_components` already tell users when to rebuild, and the CI attestation actions produce provenance (REP-6).
- **A weekly workflow that re-verifies the latest release**: deferred. With REP-1's pins and REP-2's per-tag rebuild, from-source rebuilds no longer drift unnoticed.
- **Byte-identical natives built from sdists (prefix maps, a reproducibility gate on them)**: deferred as best effort. uv builds in randomly named directories. REP-3 sets SOURCE_DATE_EPOCH and records which dists were built from sdists, and with which backend.
- **POSIX ACL and shared-group checks on the cache root** (security/way-check-acl-and-shared-group): deferred. It is a known limit, documented in SEC-3's docs/security.md.
- **A spike on static OpenSSL with FIPS and RHEL crypto-policies** (native/static-openssl-host-config-unverified): deferred.
- **The close_range fast path** (native/close-range-compiled-out-by-sysroot-headers): deferred. The fallback is correct, the gain is unmeasured, and the change would alter the runtime id.
- **Broader benchmark campaigns**: deferred until PERF-2's cold numbers raise a question about D31. This covers page faults (perf/metric-breadth; Phase 10 measured peak RSS, performance.md:509-520), CPU-quota runs and a cgroup cap on thread counts (perf/thread-count-ignores-cgroup-quota, D39), cache growth across upgrades and rollbacks (perf/disk-across-upgrades-unmeasured), and an interleaved budget test (perf/budget-test-method).
- **ThreadSanitizer, and gating C launcher coverage**: deferred. CI-7's static analysis and CI-3's informational llvm-cov come first.
- **Size-floor reductions** (D2, D12, D29): deferred until DX-5's breakdown shows where the bytes go. Phase 10 measured `hello` at 27.7 MB against PyInstaller onefile's 13.2 MB (performance.md:486).
- **CODE_OF_CONDUCT, PR templates, CODEOWNERS and required reviews**: rejected for a one-maintainer project. The owner merges locally and pushes main as an admin, past any required check or review (§5 #2). CI-8 runs CI on every push to main and makes release.sh tag only a commit whose run passed. The community-profile percentage is therefore not a target (§6).
- **PyPI: a placeholder upload or a wheel carrying the executable**: rejected. install.sh is the owner's install channel (Phase 9), and a copy managed by uv or pip would conflict with self-update, which renames the new file over the running one. PEP 541 also lets PyPI reclaim placeholder projects.
- **FSBPYR_UNBUFFERED and other PYTHON* replacements**: rejected (YAGNI, D14). DOC-1 documents the difference.
- **A `setup-fsbpyr` GitHub Action**: rejected (YAGNI). The README's curl line, or Phase 9's install.sh, which appends its directory to `$GITHUB_PATH` in GitHub Actions (IMPLEMENTATION_PLAN.md:1576), is one line.

---

## 5. Owner decisions the plan needs

1. **Release gate (CI-3, CI-6).** No D-number; it partly reverses 84feb09.
   - (a) A nightly full suite that never blocks a release.
   - (b) release.sh and the publish job require a green full-suite run of the tag's parent.
     PRs get no slower. release.sh refuses to tag until that run is green, and can wait on a
     run in progress with `gh run watch`, as it already does for the tag run
     (release.sh:273-293).
   - (c) The tag job runs the full suite inline. Estimated at about 20-25 minutes, about 10 more when the 600 s fuzz applies: about 430
     of the 445 e2e items had run when run 38624936426 was cancelled at 17m53s, and the unit
     and integration tiers had not started.
   - **Decided (owner, 2026-10-20): (b).** Until CI-3 lands, CI-8's main push run is the gate.
2. **Repository settings and burned versions (REL-1, SEC-1).**
   - Immutable releases, a `v*` tag ruleset that blocks updates and deletion, and private
     vulnerability reporting.
   - main: (a) block force pushes and deletion only. A required status check would gate
     nothing, because every change reaches main as the owner's admin push. (b) Move merges to
     pull requests and require the CI check.
   - A failed release "burns" its version instead of having its tag re-pushed.
   - The six versions without a tag, 0.15.2-0.18.0: (i) a CHANGELOG note only, or (ii)
     annotated tags at their release commits (d489698, 7c11a1c, 80a1f1b, 86b2645, 299355e,
     21ac25d; none contains release.yml, so nothing would be published).
   - **Decided (owner, 2026-10-19): all settings, main (a), burned versions, and (i).** The
     settings were switched on the same day: immutable releases, the `release tags` ruleset
     (`refs/tags/v*`: updates and deletion blocked), the `main` ruleset (deletion and force
     pushes blocked, no bypass) and private vulnerability reporting.
3. **Security response windows (SEC-1).** **Decided (owner, 2026-10-19): 7-day acknowledgement
   of reports; fixes within 7 days for high and critical issues and 30 days otherwise, as
   targets.** SECURITY.md
   says that, until SEC-4's opt-in clang-23 toolchain is merged, a fix needing a PBS release
   after 20261009 first waits for it.
4. **D24 (RT-2).**
   - (a) A zero-cost excepthook note. It is narrow: it misses apps that replace
     sys.excepthook (typer, rich) and errors the app catches.
   - (b) A C-level open hook in the launcher, measured against D24's 1% gate and shipped only
     if it stays under it.
   - (c) No runtime hint; widen the scan and the docs only.
   - **Decided (owner, 2026-10-20): (a) now, and (b) only if TST-3 finds failures that (a) misses.**
5. **D22/D23 and a new D-number (SEC-3).**
   - The credentials condition on the parent check (real and effective uid and gid; saved ids
     are not compared).
     **Decided (owner, 2026-10-20);** nothing that works today breaks.
   - Privileged starts, three options:
     - (a) refuse every AT_SECURE start (about 5 lines of C; stops set-gid and `setcap`
       deployments such as `cap_net_bind_service`);
     - (b) an opt-in mode with a fixed root-owned cache that ignores FSBPYR_*, XDG and HOME
       (keeps those uses, adds a second cache policy);
     - (c) leave it as today.
   - **Decided (owner, 2026-10-20): (a), recording (b) as the rejected alternative.**
6. **D27 (RT-3).** Files that were executable in the wheel extract as 0500. The alternative is
   to keep 0400 everywhere and only warn. **Decided (owner, 2026-10-20): 0500.**
7. **D28 (REP-3).** A `git.dirty` flag: none, tracked files only, or tracked plus untracked.
   **Decided (owner, 2026-10-20): tracked files only,** read with `git --no-optional-locks -c core.fsmonitor=false
   diff --quiet HEAD --`.
8. **D28 extension (DOC-2): what is stable before 1.0.**
   - (a) State stability only for the `inspect --json` schema (versioned by REP-3), and leave
     CLI flags and `[tool.fsbpyr]` keys free until 1.0.
   - (b) Promise stability for CLI flags, `[tool.fsbpyr]` keys and the schema now.
   - Either way, state what self-update and install.sh rely on across versions: the release
     asset names and URLs (which Phase 9 already keeps), and that an older fsbpyr's in-process
     `inspect --verify` can still check a newer release, so a format_version bump must keep
     that check working.
   - **Decided (owner, 2026-10-20): (a) plus the self-update guarantees.** The 1.0 criteria in DOC-2 are a
     proposal for the owner to accept or change.
9. **D38 (RT-6).** The 16 MiB compile cut-off limits, but does not meet, D38(2): compiling takes
   about 100-165 times the source size in memory, so a module just under the cut-off already
   needs 1.6-2.75 GB in one compile worker (RT-6), and compiling larger modules from one whole
   read would need more. **Decided (owner, 2026-10-20): record the cut-off in D38** as a consequence of D38(2)
   and an exception to D38(1)'s "no size limits of ours".
10. **Launcher licence notice (REP-4; no D-number).** Either LICENSE gains a runtime-exception
    clause covering the launcher and the boot modules (the SPDX expression then needs a
    LicenseRef), or every app reproduces fsbpyr's MIT notice. Apps ship about 15 third-party
    notices anyway, so the exception saves one. **Decided (owner, 2026-10-20): the exception.**
11. **Source of the clang-23 toolchain (SEC-4; D2 itself is unchanged).**
    - (a) Stay on PBS 20261009 until the first security-relevant PBS release, then switch.
    - (b) sid's clang-23 from snapshot.debian.org at a fixed timestamp, in a sid image used
      only for `runtime build`.
    - (c) LLVM's release tarball, pinned by sha256.
    - apt.llvm.org, which §11 of docs/IMPLEMENTATION_PLAN.md first chose, can be pinned by
      signing key but not by version: it keeps one rolling build per branch.
    - **Decided (owner, 2026-10-20): (a), with (b) or (c) made ready by SEC-4's spike.**
12. **D13 (SCOPE-1).** Cap fsbpyr's own requires-python at `<3.15` now. pip then refuses
    3.15; uv installs anyway, so fsbpyr also stops early with a clear error on any Python but
    3.14. Phase 9's port to 3.12 widens it to `>=3.12,<3.15`, and the cap rises whenever `fsbpyr`
    moves to a newer Python. **Decided (owner, 2026-10-19).**
13. **Where Stages 0-2 sit relative to Phase 9 (no D-number). Decided (owner, 2026-10-19):
    (a).**
    - (a) Stage 0 before Phase 9 starts; Stages 1-2 alongside Phase 9, with CI-3, CI-6 and
      REP-2 done before Phase 9's Python-version part multiplies the executables.
    - Not taken: (b) Stages 0-2 first, then Phase 9; (c) Phase 9 first, then this plan.
    - Why: Stage 0 is 2-3 weeks of small fixes that users hit today, self-update fetches
      whatever the latest release holds and detects only damage until D31's signature, so
      REL-1's immutable releases matter to it, and the CI gates cost less to build for one
      executable than for 15.

No decision is needed now on D39 (thread counts under cgroup quotas). PERF-2 measures first, and
the owner decides if the data shows throttling. Signed releases (D31), Python versions,
platforms, self-update and the installer are decided in docs/IMPLEMENTATION_PLAN.md
(2026-10-18) and need nothing from this plan.

---

## 6. How to measure progress

| Metric | Today | Target | How it is measured |
|---|---|---|---|
| False-success builds: commands where the executable and the venv differ after the documented normalizations, across fixtures and corpus | unmeasured | 0; each one fixed or listed in docs/compatibility.md | The TST-2/TST-3 summary line in the full-suite log |
| Changes tested before their release tag | none: CI runs on tags and pull requests only, and changes reach main as local merges | every push to main runs CI, and no tag is made on a red commit | CI-8: `gh run list --workflow release.yml --event push --branch main`; release.sh's refusal test |
| Share of the suite that gated each release | 1,042 of 1,424 items at v0.20.0; 45 of 133 e2e functions; portability never | every collected item except allowlisted skips, on the release's parent, portability included | `gh run list --workflow full-suite.yml --commit "$(git rev-parse vX.Y.Z~1)" --status success` is non-empty for every tag; executed / collected over full.xml and the portability job's JUnit XML |
| Skips outside the allowlist | 28 in CI: 20 removed by installing gcc, strace and groff (CI-1), 5 by running as a normal user (CI-3), 3 for unshare and podman | 0 | `FSBPYR_TESTS_REQUIRE_ALL` with the job's allowlist (CI-1) fails the run; full.xml |
| Launcher fuzzing before a release | launcher and fuzz harness unchanged since 0.15.0; the last 600 s run was at 0.15 | every release whose launcher changed since the previous tag is gated by a 600 s run | The gating full-suite run's log; the release notes link it (CI-3) |
| CI wall time | pull-request runs about 7 min; tag runs 5m46s (v0.19.0) and 7m09s (v0.20.0) with the same workflow | the median of the last 5 main push runs within 2 minutes of the median of CI-8's main push runs before CI-1 lands; each new step's cost recorded in its commit | `gh run list --workflow release.yml --event push --branch main --json startedAt,updatedAt`; step times from `gh run view --json jobs` |
| Branch coverage | 93% on unit + integration with the PBS cache, not gated; build.py 63% | test-ci ≥ `fail_under` (first CI figure minus 1, at least 80); payload/ + elf.py ≥ 90% | CI-2's coverage steps |
| Independent rebuild matches | checked inside one job only | every main push and tag from REP-2 on | REP-2's `compare` job |
| Corpus apps that work on the first build with no fsbpyr config | unmeasured | at least 8 of the corpus apps; every other app needs one documented config line | TST-3 summary; docs/compatibility.md |
| Embedded-runtime lag | pin 20261003, two releases behind (v0.20.0 kept it), no tracking | within SECURITY.md's targets (7 days high/critical, 30 days otherwise) once SEC-4's switch is ready | open → close time of upstream-watch `runtime-update` issues, against release dates |
| Repository trust settings | 0 of 5: immutable releases off, main unprotected, no tag ruleset, private reporting off, no SECURITY.md | 5 of 5 as §5 #2 decides, plus CONTRIBUTING.md and a bug issue template | `gh api` on immutable-releases, rulesets, rules/branches/main and private-vulnerability-reporting; `gh repo view --json isSecurityPolicyEnabled` |
| README quickstart works on a clean machine | fails without ~/.local/bin; no quickstart | green on every main push | DX-4's e2e test in test-ci |
| Manifest completeness | lock kind and name==version only | 100% of the compat fixture's dists other than the `local` project carry `artifact_sha256` or `source = built-from-sdist`; inspect shows the OpenSSL version | REP-3's unit tests in test-ci; its compat-fixture check in the full suite (CI-3) |
| Startup and size regressions | one timing test (1.5x) | read and decompression counts and a ±3% size guard in test-ci; `heavy np`'s decompressed bytes in the full suite | PERF-1 and CI-1 tests |

---

## Review notes

### Revision of 2026-10-19

The plan was written on 2026-10-16 against v0.19.0. On 2026-10-18 the owner released v0.20.0
and changed docs/IMPLEMENTATION_PLAN.md: Phase 10 is done, Phase 9 is rewritten (self-update,
then Python 3.12-3.14 with one fsbpyr per version, then aarch64, musl and macOS arm64, no
Windows, and an installer), and signed releases come after the MVP. That record overrules this
plan. The review's 158 findings (110 confirmed, 44 partly confirmed, 4 refuted) were applied as
follows.

**Removed:**
- **PERF-4** (Phase 10 comparison): done on 2026-10-18 (docs/performance.md, "Other tools"). Its
  remaining cold page-cache figures come from PERF-2, which needs no root. Adding PyInstaller
  `--onedir` would reopen a phase the owner closed.
- **SCOPE-2** (CPython 3.15 first): Phase 9 settles Python versions. Its mechanism, one
  self-hosted fsbpyr per minor instead of a compile helper, is what the owner chose; 3.15
  follows when it becomes the newest version.
- **SCOPE-3** (aarch64 below the cut line): Phase 9's third step schedules it, with musl and
  macOS arm64. Its code points are listed under "Relation to Phase 9" in §3.
- **REL-3** (PyPI wheel): dropped by this revision; the findings asked to recast it as an optional channel beside install.sh. The owner's README leads with install.sh, and a uv-managed copy
  would collide with self-update, which replaces the running executable.
- §4's rejection of musl and its deferral of Windows, macOS and 3.12/3.13; §5's old #9 (detached
  attestations close D31), #10 (when Phase 10 runs), #15 (3.15 first), #16 (aarch64 order) and
  #17 (PyPI).
- **Done since 2026-10-16:** format.md's version 6, D32's glibc 2.25 note, D13's click note, the
  plan's status line, and the README's glibc paragraph (DOC-2 and DOC-3 shrank accordingly).
- REL-1's tag steps: local and origin tags already agree, and 0.15.2-0.18.0 (six versions) have
  no tag in either.

**Added:** CI-8 (CI on every push to main; tag only a green commit), §5 #13 (where Stages 0-2
sit against Phase 9; the owner chose (a) on 2026-10-19: Stage 0 first, Stages 1-2 alongside
Phase 9), and "Relation to Phase 9" in §3.

**Changed, among others:**
- SCOPE-1 keeps the `<3.15` cap but adds an early version check, because uv installs fsbpyr on
  3.15 despite the cap (reproduced with uv 0.13.1).
- SEC-4 becomes a one-day spike plus an opt-in switch, with a toolchain source that can be
  pinned: apt.llvm.org keeps one rolling build per branch.
- REP-6 keeps the app provenance recipe and the plain sha256, and no longer closes D31.
- REL-1 keeps saying signed releases are planned (after the MVP), and main protection follows
  §5 #2: a required check would not gate admin pushes.
- CI-3 (effort L) follows the job of 55641fc: tester is created in the job and bootstraps as
  itself.
- PERF-1 uses the existing counters, with no launcher change.
- Facts, counts and baselines were re-measured: 1,424 collected items, 1,042 in test-ci, 93%
  coverage with the PBS cache, CI runs of about 7 minutes, and PBS 20261009's bitcode read
  from PYTHON.json, no longer inferred.
- Line references were updated to HEAD.

### Review of 2026-10-16

Three critics reviewed the merged plan, covering feasibility, owner decisions and completeness.
Every must-fix and should-fix point was checked against the code. All were applied except the
ones below, which were rejected or changed.

**Rejected:**
- **PERF-2's 1.2x vs 1.5x as an owner decision** (decisions critic, must-fix). Not applied as an
  open decision: docs/performance.md:37-40 records that the owner raised the budget to 1.5x on
  2026-10-09. DOC-2 appends it to D37 as a dated note and updates §3.3 and §8 to 1.5x.
- **REP-3's `[tool.fsbpyr] build-constraints` key** (completeness critic). No new key: uv's own
  `[tool.uv] build-constraint-dependencies` and `UV_BUILD_CONSTRAINT` already reach staging,
  which passes the environment through (staging.py:172). DOC-1 documents them. SOURCE_DATE_EPOCH,
  the backend record and the sdist note were applied.

**Applied in another form:**
- **SEC-2's BITCODE_MAJOR.** The feasibility critic asked for a PYTHON.json check; the decisions
  critic asked to drop the constant. It is dropped: upstream-watch reports a change of LLVM major,
  and lld rejects mismatched bitcode at runtime build.
- **TST-1's `system-ca` check.** Dropped, as the feasibility critic showed it fails on Debian and
  Ubuntu. Its alternative, a real handshake, needs per-distribution CA installs, so
  native/system-ca-not-e2e-tested is deferred (§4) rather than planned.
- **CI-1's compiler lookup in test_launcher_format.py.** Not changed: installing gcc in CI gives
  it `cc`, which NAT-1's fixture also needs.
- **CI-3's portability leg.** It became its own item (CI-6) on the runner host, with apps built by
  the self-hosted executable, instead of a `container:` matrix. Both critics allowed either.
- **Pre-release fuzzing.** Instead of a separate 600 s check in the gate, full-suite fuzzes for
  600 s whenever the launcher changed since the last tag. The existing gate on the parent's run
  then enforces it.
- **REP-2's timing.** `reproduce` runs in parallel with `build`, and a small `compare` job checks
  the result.
- **REP-1, REP-2 and SEC-4.** All three moved into Stage 1, rather than staying in Stage 2 with a
  stated reason.
- **The cut line.** NAT-4, REP-7 and the low test gaps (CI-7) moved below a cut line with full
  specs, not into §4. NAT-3, PERF-2 and DX-5 stay above the line although the decisions critic's
  list omitted them, because each closes a medium gap at S-M cost. NAT-5 became §4's "C/C++ sdists rebuilt against the baseline" entry, and the ids REP-5 and
  PERF-3 were retired in the merge.
- **RT-6.** The 16 MiB cut-off became an owner decision (D38), with recording it in D38
  recommended over new code.
- **DOC-1's kernel line.** It says that no minimum is established (the launcher falls back
  without O_TMPFILE and close_range), not "≥ 4.19".
- **SEC-1's Dependabot `docker` ecosystem.** Kept, but marked unverified for a Containerfile and
  a workflow `container:` image. REP-1's watch step checks the digest regardless.
