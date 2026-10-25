# fsbpyr: use cases that need no questions, after verification

A research report, not a plan: decisions are recorded in docs/IMPLEMENTATION_PLAN.md, which wins where the two differ.

Date: 2026-10-25. Repository: HEAD 74deed8 (v0.25.0). The released fsbpyr 0.25.0-723e1966 has the same `src/` as HEAD, and the verifiers ran that release.

Inputs: the round-1 merge and six verifier lenses: code claims, experiments, prevalence, competitors, platform reach, and simplicity/security.

None of the proposed changes was built. Status labels in this report mean:

- **experiment-confirmed**: a verifier ran fsbpyr 0.25.0, uv 0.13.5 or a container and saw the result;
- **code-confirmed**: read in the source at HEAD;
- **unverified**: neither.

LOC figures are estimates from reading the code, not measurements.

---

## 1. Summary

- fsbpyr's verified edge is one plain ELF that runs pure-Python code without extracting anything, yet still carries native wheels. For hello: 13.1 ms and 0 bytes on the first run, 8.1 ms on later runs.
- It builds from uv.lock as it is, for a manylinux_2_34 floor, on any build host, with no C compiler. PyInstaller onefile writes 35-179 MB on every start (132.3 ms for hello). Its maintainers declined cached extraction on 2026-10-19.
- None of the eight tools checked finds the commands of a plain script directory without flags. fsbpyr cannot do it either: today it refuses such a directory.
- Several round-1 "only fsbpyr" claims fell: reading uv.lock, argv[0] multicall (Nuitka, pex), reproducible bytes, PYTHON* isolation, and tamper resistance. D31 detects damage only.
- Dispatch needs no C change. In runs of 0.25.0, argv[0] reached Python unchanged through symlinks, PATH and `exec -a`. A hand-written dispatcher that set sys.path[0] gave CPython's sibling imports.
- The changes that cover the most, each shippable alone:
  - two C fixes for services (about 10 LOC);
  - requirements `-e` lines (about 20);
  - entry inference (about 25);
  - a command table in the existing entrypoint string, with dispatch in the boot (about 90).
- These build on those: a hand-written wheel under a reserved `_fsbpyr_scripts/` root for projects without a build system (about 150-200), then loose directories (about 150-200) and single files (about 50). Nothing was built.
- The best-measured audience is unpackaged uv projects: 59% of 407 sampled uv projects, of which about 1 in 5 looks like a CLI or ops tool. The loose-scripts case cannot be sized from public GitHub, and round 1's "highest prevalence" is not supported.
- Main caveat: glibc 2.34 misses EL8 (about 42% of EPEL-reporting EL systems, maintained to 2029) and all older fleets. Section 9 shows that a supported EL8 sysroot (Rocky 8.10) links today's launcher and all four Pythons at GLIBC_2.25 after a 3-line guard. As a build-time option, the cheapest delivery is EL8 stub-only release assets. Inside that reach the wrong-Python problem is real: EL9 and AL2023 keep python3 at 3.9, and SLES 15 SP6/SP7 ship 3.6.
- Python 2 scripts, modules removed since 3.6-era Python, and OS bindings (dnf, rpm, apt) stay out whatever the glibc baseline.
- Two must-fix items come with the design:
  - the smoke test must find script commands, not import them (it already runs the unguarded top-level code of `--entry module` builds on the build host);
  - link-named starts must skip D44.
- Found on the way:
  - every setuptools-built project gets `<name>.egg-info` written into its tree, breaking a promise in the staging.py docstring;
  - PYTHONUNBUFFERED is ignored, so a service's block-buffered stdout is lost on SIGTERM.
- Twelve owner decisions gate the work (section 5). The first two reverse §11's "loose scripts out of scope" and move multi-command dispatch from the app into the boot.

---

## 2. What only fsbpyr offers, and where others are better

### 2.1 Claims that survived verification

| Claim | Status | Evidence |
|---|---|---|
| Pure-Python code runs from one plain ELF with nothing extracted, while native wheels are supported. Natives a run imports go to a persistent cache (0-69 MB per app). | experiment-confirmed (fsbpyr), measured | docs/performance.md:672-677: hello first run 13.1 ms (0 bytes), later 8.1 ms; heavy np first run 38.3 MB. HOME-less runs of pure apps wrote nothing (experiments e10). |
| PyInstaller onefile extracts on every start, and will keep doing so | code-confirmed (docs and issue) | performance.md:644-645, 681-685: 132.3 ms; 35 MB (hello) to 178.6 MB (aiagent) written and removed per start. PyInstaller #7907 closed not_planned 2026-10-19. |
| pex and PyApp unpack a full environment on the first run | measured in one configuration | performance.md:672-680: pex `--scie eager` 1.4-3.2 s and 265-677 MB; PyApp 0.28-1.17 s and 311-702 MB. pex with stripped PBS (`--scie-pbs-stripped`) and without `--venv` was not measured. |
| Locks used as they are, plus a manylinux_2_34 floor from any build host, plus no compiler | code-confirmed | uv picks the wheels (D26, D42). Nuitka compiles whatever the host environment has installed and tells users to build on the oldest OS (Nuitka README:1136-1150). PyInstaller says to build on the oldest glibc (usage.rst:664-680). |
| Commands detected from a plain directory with no flags | negative search over 8 tools; **not a feature of fsbpyr today** | PyInstaller, Nuitka 4.3.1, pex 2.105.1, cosmofy 0.2.1, pyfuze 2.7.2, PyCrucible 0.4.8, bundleup 0.2.1 and others: each needs one entry per command, or console scripts. |
| A whole-file check on every start (D31) | code-confirmed | No competitor checked hashes its whole file per start (Nuitka cached onefile CRC32s extracted files only). It detects damage, not tampering: docs/IMPLEMENTATION_PLAN.md:123 (D31), :120 (D28). |
| No writable home is needed for pure-Python apps | experiment-confirmed | e10 ran with HOME empty, unset and `/nonexistent`. Only the 0-byte D45 slot file in /dev/shm is used. |

### 2.2 Claims that did not survive

| Round-1 claim | Why it fell | Source |
|---|---|---|
| "No other single-binary tool reads uv.lock directly" | cosmofy runs `uv sync` (pure Python only). pyfuze has `--uv-lock` (bundle mode extracts to /tmp). PyCrucible runs `uv run` on the target (network on first run). bundleup reads uv.lock and pylock (a .pyz that needs Python; alpha). | github.com/metaist/cosmofy bundle.py:240-290; pypi.org/project/pyfuze; github.com/razorblade23/PyCrucible run.rs:81-90; pypi.org/project/bundleup |
| argv[0] multicall as an fsbpyr advantage | Nuitka 4.1+ `--project` turns [project.scripts] into a multidist binary that dispatches on basename(argv0). This was read from source, not run. pex `--scie-busybox @` selects all console scripts without naming them. scie-jump has SCIE_ARGV0. | Nuitka 4.3.1 ReformulationMultidist.py:38-46, extract_uv_config; pex scie/__init__.py:254-283; a-scie/jump docs/packaging.md:344-348, 410-412 |
| Reproducible bytes | pex is reproducible by default; PyInstaller documents a procedure | pex/bin/pex.py:395-415; PyInstaller advanced-topics.rst:853-880 |
| PYTHON* isolation | PyInstaller uses an isolated pre-config; PyApp has PYAPP_FULL_ISOLATION | PyInstaller bootloader pyi_pyconfig.c:348; PyApp docs |
| "A tampered binary refuses to run" | D31 detects damage only. A change that also rewrites the digest passes until signed releases exist. | IMPLEMENTATION_PLAN.md:123 |
| "No tool maps __file__ to the deployment directory" | It is true only for single-file outputs. Nuitka standalone does it by default, and PyInstaller onedir does it with `--contents-directory .`. | Nuitka README:1369-1376; PyInstaller runtime-information.rst:52-65, CHANGES 6.0.0 |
| An interpreter mode would be unique | PyApp ships `<exe> self python` (after a first-run unpack of 311 MB or more) | PyApp docs/runtime.md:140-156 |
| PEP 723 with the script's own uv lock is fsbpyr-only | cosmofy bundles PEP 723 scripts through `uv sync --script` (pure Python only) | cosmofy bundle.py:262-266 |

What remains unique, stated narrowly:

1. Commands detected from a plain directory or project with no flags. This is not built yet.
2. One plain ELF that never extracts pure-Python code yet carries native wheels.
3. uv.lock and PEP 723 locks used as they are, with a manylinux_2_34 floor from any build host and no compiler.
4. A whole-file damage check on every start.

### 2.3 Where competitors are better

| Area | Who is better | Evidence |
|---|---|---|
| Windows and macOS | PyInstaller, Nuitka, pex (macOS), APE tools | pypistats, PyInstaller downloads: last month (2026-09-25 to 10-24) 54.9% Linux, 36.2% Windows, 8.1% macOS. Over 2026-04-08 to 10-24: 62.0% Linux, 30.7% Windows, 6.5% macOS. CI dominates both counts. Only 1,988 of 30,400 GitHub workflow files using PyInstaller (6.5%) are Linux-only. |
| Old glibc | python-build-standalone based tools (pex scie, PyApp, uv) reach glibc 2.17. PyInstaller reaches it when built on an old host. Nuitka commercial offers CentOS 7 container builds. | python-build-standalone docs/running.rst:280-291; Nuitka README:1144-1150 |
| Size | PyInstaller onefile | hello 13.2 MB against 27.7 MB; aiagent 101.6 MB against 150.2 MB (performance.md:632-636) |
| Later-run time and memory | pex and PyApp match the venv | fsbpyr is +0.2 to +15 ms and +0.4 to +15.6 MB RSS over the venv. For aiagent, fsbpyr's RSS (54.5/62.6 MB) is above even PyInstaller onefile's (52.1/58.5 MB) (performance.md:644-665). |
| Package-specific fixes | pyinstaller-hooks-contrib, Nuitka package configs and anti-bloat | 29 of the 64 corpus apps need settings on 3.14 (README.md:55-58) |
| Foreign-platform builds from one host | pex `--platform`/`--scie-platform`, bundleup `--python-platform`, APE tools | competitors lens |
| Source obscured | Nuitka compiles to C | — |
| Maturity | PyInstaller 6.22.3, Nuitka 4.3.1 and pex 2.105.1 all release often | PyPI JSON, 2026-10 |
| Directory deployments (rsync, tar) | PyInstaller onedir with a shared COLLECT: N executables over one `_internal/`, no extraction, `__file__` next to the executable | spec-files.rst:644-648; runtime-information.rst:52-65. Start time was not measured here. |
| Multi-command packaged projects | Nuitka 4.x `--project` (read from source; it needs a compile step and `--zig` or a C compiler) | Nuitka has no numbers in docs/performance.md, so it is the competitor most worth measuring next |

Watch item: uv has no single-file output as of 0.13.5. Draft PR #21250 ("Portable Environments", 2026-08-20) makes relocatable directories, not single files. Re-check before any public positioning against uv.

---

## 3. Use cases, ranked by value for effort after verification

| Rank | Use case | Effort (code) | Population evidence | fsbpyr edge | Status of today's failure |
|---|---|---|---|---|---|
| 1 | UC1 Python services under systemd, cron and containers (every app) | XS: about 10 LOC C | medium | parity fix, not unique | experiment-confirmed |
| 2 | UC2 Requirements files with `-e` local packages and VCS pins | XS: about 20 LOC | unmeasured | not unique | experiment-confirmed |
| 3 | UC3 pyproject apps without a build system | M: about 150-200 LOC | high for the layout, medium for the target | unique combination | experiment-confirmed |
| 4 | UC4 Several console scripts become one multicall binary | S-M: about 90 LOC | medium-low | moderate (Nuitka and pex do it with flags) | experiment-confirmed |
| 5 | UC5 `python -m pkg` packages and single guarded modules | XS: about 25 LOC | low-medium | low (PyApp infers the same) | experiment-confirmed |
| 6 | UC6 The owner's case: a directory of loose scripts | M after 3-4 (about 150-200 LOC) | unknown | clearest | experiment-confirmed (refused) |
| 7 | UC7 Monitoring plugins and cron fleets (a profile of UC6) | 0 beyond UC6, plus docs | low-medium, declining | clearest against PyInstaller onefile | as UC6 |
| 8 | UC8 One script, with or without a PEP 723 block | S after UC6 (about 50 LOC) | medium | partial (cosmofy covers pure-Python scripts) | experiment-confirmed (refused) |

UC1 and UC2 open no new layouts. They rank first because they are tiny and fix measured failures. UC3 is the first change that opens a layout, and it has the only measured audience.

### UC1. Python services under systemd, cron and containers (all apps)

- **Situation.** A converted app runs as a service or in a container. Units and Dockerfiles routinely set `PYTHONUNBUFFERED=1`: GitHub code-search estimates find about 20.7k unit files and 316k Dockerfiles that do. systemd sets HOME only for units with `User=` (systemd.exec). By code-search estimates, about 13% of 89k python unit files have no `User=`; the NOT semantics of that query are unverified.
- **Today (experiment-confirmed).**
  - PYTHONUNBUFFERED has no effect (D14), and stdout to a pipe is block-buffered. The first line arrived at +2.06 s, against +0.01 s in a venv. After SIGTERM at 1.5 s the fsbpyr app had delivered 0 lines, CPython 2.
  - stderr is line-buffered, so logging to stderr is unaffected.
  - With HOME unset, a native app extracts into the passwd home, but the launcher's background collection (D38) never runs: there was no `.gc` stamp in 3 runs, and it appeared on the next run once HOME was set. The effect is a growing cache, not a failed start.
- **With the change, asking nothing.**
  - A non-empty PYTHONUNBUFFERED disables stdio buffering, as in CPython.
  - The launcher finds the cache root by the same rule as the boot.
- **Mechanism:** runtime-env-policy, reduced to these two items. JOURNAL_STREAM sniffing and a PID-1 SIGTERM handler are dropped; `python app.py` as PID 1 behaves the same.
- **Size:** about 10 LOC C and 2 tests.
- **Evidence strength:** high for the defects, medium for prevalence (code-search estimates).
- **Verification:** the defects are experiment-confirmed; the fix is unverified.
- **Risks.**
  - `getpwuid_r` in the launcher may load NSS modules (sssd, LDAP) when HOME is unset; the cost is unverified.
  - It is a named exception to D14.
  - With HOME set to an empty string, Python gives `/.cache/<app>` and C gives nothing. One rule is needed for both.

### UC2. Requirements files with `-e` local packages and VCS pins

- **Situation.** A monorepo app has a requirements.txt with `-e ./libs/common`, or `mylib @ git+https://...@<40-hex>`.
- **Today.**
  - An `-e` line fails: "common is installed in editable mode ...; editable requirements (-e) cannot be packaged" (experiment-confirmed, e9; closure.py:93-101).
  - A SHA-pinned VCS line, or a URL with `#sha256=`, is reported as unpinned, because `unpinned_requirements` checks only `==` (project.py:142-153; code-confirmed by calling it).
- **With the change.**
  - `uv pip install --no-editable -r FILE` installs `-e` paths as regular packages. `check_no_editable` stays as the safety net.
  - SHA-pinned VCS lines and `#sha256=` URLs are not warned about.
  - When only `requirements/*.txt` exists, a one-line hint names `--requirements` instead of a guess.
- **Mechanism:** requirements-quirks. The split-file guessing is cut, because base.txt can leave out production-only packages.
- **Size:** about 20 LOC and 4 tests. The e2e test `test_editable_dependencies_are_rejected` inverts.
- **Evidence strength:** low for prevalence; nothing measured the share of `-e` lines.
- **Verification.**
  - uv's `--no-editable` was experiment-confirmed outside fsbpyr: it installed common==0.1.0 with `editable: false`.
  - It was not tested through fsbpyr's pipeline.
- **Corrections from verification.**
  - Counting SHA pins as pinned does **not** make `--strict` pass. `--strict` adds `--require-hashes` (staging.py:189), and uv 0.13.5 then rejects VCS lines that have no hash. The same happens to any file that has `--hash=` anywhere. See decision 5.11.
  - `-e` lines are never reported as unpinned (project.py:148 skips lines starting with `-`). With `--no-editable` they would install an unversioned local tree without a word, so the build must record or warn about them.
- **fsbpyr edge:** none unique.

### UC3. pyproject apps without a build system

- **Situation.**
  - `uv init --no-package` today, and plain `uv init` from uv 0.4.0 (2024-08-28) to 0.11.x; uv 0.12.0 (2026-07-28) made it packaged by default.
  - Poetry `package-mode = false`, and PDM `distribution = false`.
  - The project has [project] with dependencies, uv.lock, main.py (hello.py in uv 0.4-0.5) plus helpers, no [build-system], and usually no [project.scripts].
- **Today (experiment-confirmed, uv 0.13.5).** `uv build` falls back to setuptools' legacy backend.
  - Two top-level modules fail with "Multiple top-level modules discovered in a flat-layout". fsbpyr shows only "uv failed (exit 2)", because it passes `--quiet` (section 7).
  - A `utils/` package is dropped silently, and the smoke test then fails with ModuleNotFoundError.
  - A single main.py builds, then stops with "has console scripts: none".
  - A single module with [project.scripts] already builds and runs today.
  - Every such build writes `<name>.egg-info` into the user's tree (section 7).
- **With the change, asking nothing.**
  - fsbpyr writes the project's wheel itself: stdlib zipfile, fixed timestamps, sorted RECORD. The project's Python files go under `_fsbpyr_scripts/`. The wheel is installed through the existing `--no-deps WHEEL` step, and uv.lock is used unchanged through `uv sync --locked --no-install-project`.
  - Entry: [project.scripts] if present, rewritten to the reserved root. Otherwise the modules with a module-level main guard: one gives one command, several give a command table (UC4).
  - At run time, sys.path[0] is the script's payload directory, so `import utils` works as under `uv run main.py`.
  - The selftest finds script targets instead of importing them, and a static import check replaces the import check.
- **Mechanisms:** synthesized-project, script-mode-run, entry-inference, multicall-table (when there are several commands), build-report-scan (the import check only).
- **Size:** about 150-200 LOC and about 200 LOC of tests.
- **Evidence strength.**
  - High for the layout: 242 of 407 sampled uv projects (59%) are unpackaged.
  - Medium for the target: 45 of the 238 without scripts (19%, about 11% of all sampled uv projects) look like CLI or ops tools. Most are web services (36%), ML/data, notebooks or templates.
  - The sample is ranked code-search output, not random.
  - New projects of this shape have become fewer since uv 0.12.0; `--no-package` users and two years of older projects remain.
- **Verification.**
  - The failure is experiment-confirmed.
  - Sibling imports through sys.path[0] under a non-top-level root are experiment-confirmed with a hand-written dispatcher (e13).
  - The wheel writer is unverified.
- **Competitors.**
  - cosmofy runs `uv sync` but is pure Python only.
  - pyfuze has `--uv-lock`, but its bundle mode extracts.
  - PyCrucible needs the network on the target.
  - bundleup makes a .pyz that needs Python.
  - PyInstaller is used through `uv run pyinstaller main.py`.
- **Risks.**
  - Projects that build today (one module plus [project.scripts]) would change their payload layout, so a regression test must show the same behaviour.
  - A src/ layout without a build system.
  - uv workspace roots must keep today's behaviour.
  - Poetry and PDM locks are not read: the build is unlocked, with a warning. Poetry 1's `[tool.poetry.dependencies]` is not read either; the import check names what is missing.
  - uv init takes requires-python from the newest interpreter it finds (3.15 on the test host). fsbpyr then fetches its 3.15 variant (31 MB, experiment-confirmed) and builds for 3.15, where wheels are scarcer.

### UC4. Several console scripts become one multicall binary

- **Situation.** An internal toolkit (acme-deploy, acme-db), httpie (http, https, httpie) or ansible-core (10 scripts).
- **Today (experiment-confirmed).** "e2 has console scripts: e2-alpha, e2-beta; choose one with --entry", raised before staging (build.py:327-328). The user builds N binaries, each with the whole runtime and its own cache root.
- **With the change.**
  - With one command, nothing changes, and the build is byte-identical to today's.
  - With several, the build writes `dist/<project>` and relative symlinks `dist/<script>`.
  - Dispatch order: basename(argv[0]); then the same with `.py` stripped; then argv[1] as an exact command name, with argv shifted; otherwise the commands are listed on stderr and the exit status is 2.
- **Mechanisms:** multicall-table, link-install (build-time links only), entry-inference (a).
- **Size:** about 90 LOC and about 150 LOC of tests.
- **Evidence strength:** medium. 18 of the 95 sampled uv projects with console scripts (19%) declare two or more, about 4% of uv projects. Demand for a single multicall file is anecdotal: pex #3077, one user, closed, 0 reactions. All 65 corpus apps have exactly one script.
- **Verification.**
  - argv passing is experiment-confirmed: by absolute path, symlink, relative path, `exec -a`, bare PATH name, a `tool.py` symlink and a hard link.
  - D45's shared pool across links is experiment-confirmed.
  - The table and dispatch are unverified.
- **Required companions (all must ship with the step).**
  - (a) The selftest loops over the commands. The smoke test runs the temporary file `.{name}.<8 chars>.tmp` (writer.py:593), whose name is no command.
  - (b) The forkserver preload skips tables (mp.py:200). Unchanged, it would silently preload nothing.
  - (c) `_interpreter_child` is skipped when basename(argv[0]) names a command and differs from basename(sys.executable) (decision 5.4).
- **Risks.**
  - Interpreter-like scripts (ansible-console, ipython) become reachable through the dispatcher form, so sudo rules must name links.
  - ansible inside fsbpyr is untested.
  - Hard links: sys.executable is the hard link's own path (experiment-confirmed), so hard-link-named starts keep D44's check, and hard links go stale on rebuild (experiment-confirmed).
  - The output name changes, but only for projects that fail today.

### UC5. `python -m pkg` packages and single guarded modules

- **Situation.** A packaged project with no [project.scripts] that is run as `python -m tool`, or a single py_module with a main guard.
- **Today (experiment-confirmed, e3).** "has console scripts: none; choose one with --entry", raised only after the full uv build and install. `--entry e3` builds and runs.
- **With the change.** With no `--entry` and zero console scripts, the build picks exactly one top-level package of the project's dist that has `__main__.py`. Otherwise it picks exactly one top-level module with a main guard. An ambiguous case keeps today's error and lists the candidates. One info line says what was chosen.
- **Mechanism:** entry-inference (b) and (c). Part (d), legacy `.data/scripts`, is deferred.
- **Size:** about 25 LOC and 3 fixtures.
- **Evidence strength:** low-medium.
  - 12 of 74 sampled packaged no-script uv projects have any `__main__.py`, and several of those are noise, leaving about 2% of uv projects.
  - `scripts=[` appears in 2.5% of setup.py files.
- **Verification:** the failure and the `--entry pkg` workaround are experiment-confirmed.
- **Risks:** a package whose `__main__` is a developer helper gets built as the app. That is what `python -m pkg` does too, and the build says so.
- **fsbpyr edge:** low. PyApp infers the module by default.

### UC6. The owner's case: a directory of loose scripts

- **Situation.** A directory, often a git repo, holds .py files and extensionless files with python shebangs. Some are commands and some are helpers. It has no pyproject.toml or setup.py, and may have a requirements.txt or PEP 723 blocks.
  - It is deployed by a copy (Ansible copy or synchronize, `install src/*`, rsync) to hosts with the wrong Python or no pip.
  - RHEL 9, OL 9 and AL2023 keep python3 at 3.9 for life, and Debian 12 refuses system pip (PEP 668).
- **Today (experiment-confirmed).** "has no pyproject.toml or setup.py" (project.py:53-54).
- **With the change (`cd ops-scripts && fsbpyr build`).**
  - Discovery: `git ls-files` inside a work tree; otherwise a walk that skips dot-directories, venvs, `__pycache__`, build/, dist/, node_modules and site-packages.
    - Python files only, which needs no deny list.
    - No directory symlinks are followed, and files that resolve outside the tree are refused.
    - `$HOME` and `/` are refused as inputs.
  - Commands: a module-level `if __name__ == '__main__':` or a python or `uv run` shebang (AST and first line; no user code runs).
    - Names come from the stem, and hyphens are kept. `main.py` and `__main__.py` take the directory's name.
    - Any other collision is an error that names both files.
    - Only identifier directories become modules in v1; the others are reported.
  - Dependencies come from declared sources only: requirements.txt or pylock as today; otherwise the union of PEP 723 blocks, unlocked.
    - Otherwise none, and the import check skips each command with an unresolved required import (file:line).
    - When a `.venv` exists, the message gives the exact `uv pip freeze --python .venv/bin/python > requirements.txt` command.
  - Output: `dist/<directory name>`, `dist/<cmd>` symlinks, and a printed deploy recipe.
  - At run time a link runs its script as `python3 script.py` would. It runs as `__main__`, with argv untouched, and sys.path[0] is the script's payload directory.
- **Mechanisms:** synthesized-project, script-mode-run, multicall-table, dependency-ladder (declared rungs only), build-report-scan (import check, removed-module hint, `__file__`-read report), user-file-policy (v1: the payload path), link-install (build time only).
- **Size:** about 150-200 LOC and about 300 LOC of tests, after UC3 and UC4.
- **Evidence strength:** low for size; the population is plausible but unmeasurable.
  - Public GitHub drops repos inactive for a year and cannot see private ops fleets.
  - Of 425 sampled requirements.txt files, 88% have no packaging file beside them, but those directories are mostly web (47%) or ML (23%). Only 37 hold 6 or more .py files.
  - JetBrains 2023: 26% mainly use Python for DevOps or automation (multi-select); only 6-7% name it as their main use.
- **Verification.**
  - The refusal is experiment-confirmed.
  - The start-up side is experiment-confirmed with a hand-written dispatcher (e13): sibling imports work, a stdlib-named sibling shadows the stdlib as under CPython, and argparse prog is basename(argv[0]) on 3.14.9 and 3.15.1.
  - The build side is unverified.
- **Known limits (experiment-confirmed).**
  - Path entries containing `..` fail (path_hook, _fsbpyr_boot.py:308-313).
  - Hyphenated directories become resources without a warning.
  - A script that starts a sibling as `[sys.executable, <exe>/.../other.py]` exits 70 (D44). With `--interpreter-reexec` it runs, but without its directory on sys.path, so its imports fail (reexec.py:105-113).
- **Risks.**
  - Command detection errs both ways.
  - Crontab or unit lines like `python3 /opt/tools/x.py` break once x.py is a link to an ELF file (inferred).
  - Ansible copy may copy the binary once per link (local_follow unverified).
  - Python 2 files (9% of survey-29 files) drop out.
  - One bad dependency affects every command.
  - The platform caveat is in section 6.
  - Imported Python files that hold secrets (settings_local.py) are bundled and readable.

Merged into UC6:

- **syspath-hacks-shared-lib.** `sys.path.append(<tree>/pylib)` already works through the existing alias lookup for identifier directories (experiment-confirmed). Normalizing `..` is deferred.
- **files-next-to-the-script.** In v1, `__file__` stays the payload path. The build reports each name a script reads next to itself, and the D24 note gets a branch for the scripts root. The overlay is deferred (decision 5.7).
- **old-scripts-on-new-python.** It is reduced to a hint table in the import check, for example "cgi was removed in 3.13; build with `--python 3.12`". The claim that SyntaxWarnings show at build time is refuted: compiler.py:34-36 silences them.

### UC7. Monitoring plugins and cron fleets

- **Situation.** Dozens to hundreds of short checks run every 1-5 minutes per host: Nagios, Icinga, NRPE, Checkmk local checks, textfile collectors.
- **With the change:** the UC6 build. Links carry the names the command definitions already use, and the binary goes outside any directory that agents execute wholesale.
- **What changes for the user:** nothing beyond UC6, and no D45 change (decision 5.5). It needs documentation that all links share one instance budget and that FSBPYR_MAX_INSTANCES raises it.
- **Evidence strength:** low-medium and declining. 963 Python "nagios plugin" repos exist, but only 97 were pushed in the past year. prometheus-client's download count is not evidence.
- **Verification.** The advantage over PyInstaller onefile is measured: 8.1 against 132.3 ms per start, 0 against 35 MB written per start. The D45 pool is experiment-confirmed: 64 starts under one-CPU affinity, then exit 70.
- **Risks.**
  - Exit 70 under load shows as CRITICAL in Nagios.
  - The integrity check costs up to 7.4 ms per start for a 150 MB binary.
  - Many monitoring estates still run glibc below 2.34.

### UC8. One script, with or without a PEP 723 block

- **Situation.** `report.py`, possibly with a `# /// script` block and a `report.py.lock`, or an extensionless `#!/usr/bin/env -S uv run --script` file.
- **Today (experiment-confirmed).** "Directory 'script.py' is a file" (cli.py:83-84).
- **With the change.** `fsbpyr build report.py` packages the file's static local-import closure. The block's dependencies become Requires-Dist (unlocked); requires-python selects the runtime, and `[tool.fsbpyr]` supplies settings through `config.load_config` unchanged (config.py:144-155). Using the script's `.lock` is a small follow-up.
- **Mechanisms:** synthesized-project, script-mode-run, dependency-ladder (PEP 723 rung).
- **Size:** about 40-50 LOC and 80 LOC of tests, after UC6.
- **Evidence strength:** medium.
  - About 157k files carry a real PEP 723 block: an index estimate, with 300 of 300 sampled hits precise.
  - About 21% of the sample is AI-agent tooling, and 30% sits in `scripts/` helper directories.
  - uv #5802 is generic, cross-platform demand: only 3 of its 70 comments concern scripts.
- **Verification.** uv 0.13.5 `uv lock --script`, `uv export --script --locked` and `uv sync --script --active --locked` are experiment-confirmed, and so is `[tool.fsbpyr]` inside a block. The build path is unverified.
- **Competitors.** cosmofy covers pure-Python scripts with uv's script lock. pex `--exe` reads PEP 723, and its sibling-pylock support (2.52.0) applies to `pex3 run` only.

### Deferred and dropped candidates

| Candidate | Verdict | Reason |
|---|---|---|
| undeclared-deps-from-host-env (read the venv's metadata) | deferred | It reverses D5's rejected alternative (IMPLEMENTATION_PLAN.md:97). Pins from old venvs often have no cp312+ or manylinux_2_34 wheels. v1 errors with file:line and a freeze hint. JetBrains 2024: 12% store no dependency information. |
| requirements-only-tool-binary | deferred | Low prevalence. yt-dlp ships Linux binaries, and borg ships per-glibc builds. The only request is pex #3077 (closed, 0 reactions). ansible inside fsbpyr is untested. |
| foreign-lock-files (Poetry, PDM, Pipenv) | deferred | Packaged Poetry and PDM projects build today, unlocked. It needs three parsers tested per tool version. |
| argv0-and-self-reexec-parity | deferred | Prevalence is unmeasured. The `[sys.executable] + sys.argv` restart gives the child an extra positional argument (experiment-confirmed, e7c; section 7). |
| tool-per-subdirectory-collections | deferred | It needs several outputs per build, and the evidence is mostly learner repos. |
| portable-interpreter (interpreter command) | dropped | Limiting it to argv[0] is no boundary: anyone can `ln -s` or `exec -a`. It reverses D22, D23 and D44. |
| legacy `.data/scripts` (entry-inference d) | deferred | It needs script mode. `#!python` exists only in the wheel, because staging rewrites the shebang, so detection must read the wheel. The evidence is thin (awscli v1). |

---

## 4. The changes

### 4.1 Mechanisms after verification

| Mechanism | Verdict | What remains |
|---|---|---|
| multicall-table | simplify | Newline-separated `name=target` lines in the existing entrypoint Str. Dispatch on argv[0], `.py` stripped, then argv[1] exact. The argv[1] path-basename form is cut: it collides with the reexec script form (reexec.py:50) and with positional file arguments. |
| synthesized-project | simplify | A hand-written wheel (about 40 LOC) under `_fsbpyr_scripts/`, holding Python files only. It replaces a generated pyproject plus uv_build: uv_build's fast path needs a matching bundled version, otherwise uv downloads it, which breaks `--offline`. |
| script-mode-run | simplify | `sys.path.insert(0, <exe>/_fsbpyr_scripts/<dir>)` plus today's full-name runpy call; the selftest uses find_spec. Deferred: normalizing `..`, non-identifier directories, legacy scripts, the D44 sibling-script exception. |
| dependency-ladder | simplify | Declared sources only (locks, requirements, [project.dependencies], PEP 723). The venv rung is deferred. |
| build-report-scan | simplify | An import check for the commands' static closure, a removed-module hint table, and the `__file__`-read report (already collected behind `--hints`; always on for the scripts root). The OS-binding tables, shadowing warnings and the implicit Python bound are cut. |
| user-file-policy | defer | v1 keeps the payload `__file__` and rewords the D24 note. The overlay comes later if users need it. |
| link-install | simplify | Build-time relative symlinks: a temporary link renamed into place, never replacing a regular file. On-target install is dropped (see 4.3). |
| entry-inference | simplify | Parts (b) and (c) only. |
| runtime-env-policy | simplify | The cache-root fallback in due.c and PYTHONUNBUFFERED. |
| requirements-quirks | simplify | `--no-editable`, the SHA-pin warning, a hint. The `--strict` interplay goes to decision 5.11. |
| slot-keying | defer | Document only. |
| argv0-parity | defer | — |
| foreign-lock-export | defer | — |
| interpreter-command | drop | — |

### 4.2 Ordered plan

Each step can ship alone unless a prerequisite is named. Sizes are estimates. In total, about 500-600 LOC of code and about 800 LOC of tests.

**Step 1. Service start-up fixes (UC1).**

- Change:
  - in `cache_root()`, fall back to the passwd home of the user when `$HOME` is unset, as `native.cache_directory` does, and define one rule for an empty HOME;
  - set `config.buffered_stdio = 0` when `PYTHONUNBUFFERED` is non-empty.
- Files: `src/fsbpyr/launcher/due.c` (55-83), `src/fsbpyr/launcher/main.c` (around 137-155), `docs/compatibility.md:16`, and the D14 row.
- Size: about 10 LOC C and 2 e2e tests.
- Tests:
  - a native app started with `env -u HOME` gets its `.gc` stamp on a due run;
  - with `PYTHONUNBUFFERED=1`, a pipe reader sees a line before the app exits and keeps it after SIGTERM; without it, the old behaviour.
- Prerequisite: decision 5.3(ii).

**Step 2. Requirements-file fixes (UC2).**

- Change:
  - add `--no-editable` to the `-r` install;
  - do not warn about SHA-pinned VCS lines or `#sha256=` URLs;
  - warn about `-e` local paths, since they are unversioned;
  - print a hint when only `requirements/*.txt` exists;
  - handle `--strict` with VCS lines per decision 5.11.
- Files: `src/fsbpyr/staging.py:186-190`, `src/fsbpyr/project.py:142-153`.
- Size: about 20 LOC and 4 tests.
- Tests:
  - unit: `unpinned_requirements` with a `git+...@<sha40>` line and a `#sha256=` URL;
  - e2e: a project with `-e ./libs/common` builds and runs;
  - `--strict` with a VCS line gives fsbpyr's own clear message.

**Step 3. Entry inference for zero console scripts (UC5).**

- Change:
  - choose a single `__main__` package, else a single guarded top-level module;
  - an ambiguous case keeps today's error with the candidates listed;
  - static projects with zero scripts fail before staging, not after.
- Files: `src/fsbpyr/closure.py:154-189`, `src/fsbpyr/build.py:323-334`.
- Size: about 25 LOC and 3 fixtures.
- Tests: a `__main__` package builds and runs as `python -m`; a guarded single py_module builds; both present gives an error listing them.

**Step 4. Multicall for several console scripts (UC4).**

- Change:
  - the build side writes the table;
  - the boot dispatches;
  - the three companions: the selftest loop, the preload skip, and the `_interpreter_child` skip;
  - `dist/<cmd>` symlinks;
  - inspect and cli list the commands;
  - a docs/format.md:99 note;
  - command names are validated as `^[A-Za-z0-9][A-Za-z0-9_.+-]*$`.
- Files: `src/fsbpyr/boot/_fsbpyr_boot.py` (run() 555-610, `_interpreter_child` 541-552), `src/fsbpyr/boot/_fsbpyr_rt/mp.py:200`, `src/fsbpyr/closure.py:175-181`, `src/fsbpyr/build.py:83-85, 327-328`, `src/fsbpyr/inspection.py`, `src/fsbpyr/cli.py`, `docs/format.md`.
- Size: about 90 LOC and about 150 LOC of tests.
- Tests:
  - dispatch unit tests for an exact argv[0], `.py` stripped, the binary name plus argv[1] (argv shifted), and no command (exit 2);
  - e2e with 2 console scripts through links, the dispatcher form and a renamed copy;
  - the smoke test passes under the `.tmp` name;
  - command A starting `B -m word` runs instead of exiting 70;
  - with `interpreter-reexec = true`, `B -c CODE` is **not** executed as Python;
  - `sys.executable -m x` from a command is still refused;
  - forkserver and spawn work from a link-named command;
  - single-command builds are byte-identical to before.
- Prerequisites: decisions 5.2, 5.3(i) and 5.4.

**Step 5. Projects without a build system (UC3).**

- Change:
  - detect the trigger;
  - write the wheel;
  - skip `uv build` when fsbpyr writes the wheel;
  - choose the entry ([project.scripts] rewritten, or the guarded modules);
  - the boot's sys.path[0] rule for `_fsbpyr_scripts.` targets;
  - the find_spec selftest;
  - the static import check.
- Files: `src/fsbpyr/project.py`, `src/fsbpyr/staging.py` (`_build_wheel`, 117-119), `src/fsbpyr/closure.py`, `src/fsbpyr/classify.py`, `src/fsbpyr/build.py`, `src/fsbpyr/boot/_fsbpyr_boot.py`, and a new module `fsbpyr/loose.py` (planned).
- Size: about 150-200 LOC and about 200 LOC of tests.
- Tests:
  - a `uv init --no-package` layout (main.py, utils.py, uv.lock) builds and runs like `uv run main.py`;
  - a `utils/` package is kept;
  - a single module with [project.scripts] behaves as before;
  - nothing is written into the source tree;
  - a script whose unguarded body writes a marker file does not create it during the build;
  - two builds are byte-identical.
- Prerequisites: decisions 5.1, 5.6(i), 5.8 and 5.10. Step 4 is needed only when there are several commands.

**Step 6. Loose directories (UC6, UC7).**

- Change:
  - Project.load accepts a directory without packaging files;
  - discovery (git or a pruned walk) with symlink containment;
  - AST command detection, naming and collisions;
  - dependencies from requirements.txt, pylock or the PEP 723 union;
  - the import-check policy;
  - the removed-module hints;
  - the `__file__`-read report;
  - the D24 note branch;
  - links and a deploy recipe.
- Files: `src/fsbpyr/project.py:43-63`, `fsbpyr/loose.py` (planned), `src/fsbpyr/scan.py`, `src/fsbpyr/build.py`, `src/fsbpyr/boot/_fsbpyr_boot.py:427-443`.
- Size: about 150-200 LOC and about 300 LOC of tests.
- Tests:
  - a flat ops directory with 3 guarded scripts, 1 shebang-only script, a helper and a stdlib-named sibling, plus requirements.txt: one binary and links;
  - a subdirectory tool that imports a sibling;
  - `sys.path.append(dirname(abspath(__file__)) + '/pylib')`;
  - an undeclared import skips that command with file:line;
  - `try/except ImportError` gives no error;
  - cgi on 3.14 gives the removed-module hint;
  - `.env`, a key, an untracked `secrets.py` (with git) and a symlink to /etc/passwd are never packaged;
  - collisions and main.py naming;
  - no user code runs during the build.
- Prerequisites: step 5, step 4, and decisions 5.1, 5.6(ii), 5.7 and 5.9.

**Step 7. Single-file builds (UC8).**

- Change:
  - the CLI accepts a file;
  - the file's static local-import closure is packaged;
  - a PEP 723 block gives the dependencies, requires-python and `[tool.fsbpyr]`.
- Files: `src/fsbpyr/cli.py:83-84`, `fsbpyr/loose.py` (planned), `src/fsbpyr/project.py`.
- Size: about 40-50 LOC and about 80 LOC of tests.
- Tests:
  - a block with a sibling import builds;
  - requires-python selects the runtime;
  - unknown `[tool.fsbpyr]` keys are rejected;
  - a script in a directory of 200 unrelated scripts packages only its closure;
  - an extensionless `uv run --script` file builds;
  - uv never writes beside the script.
- Prerequisite: step 6 (or step 5 for the wheel and boot parts).

### 4.3 Not in the plan

- **Deferred:**
  - the venv metadata rung;
  - the `__file__` overlay;
  - normalizing `..` and accepting non-identifier directories;
  - legacy `.data/scripts`;
  - the D44 exception for `[sys.executable, <exe>/_fsbpyr_scripts/x.py]`;
  - on-target link install;
  - D45 changes;
  - argv0 parity;
  - foreign locks;
  - the requirements-only tool binary;
  - per-subdirectory groups;
  - JOURNAL_STREAM;
  - a PID-1 SIGTERM handler.
- **Dropped:**
  - the interpreter command;
  - `<binary> --install DIR` in the dispatcher form. Under a `<binary> *` sudo rule it would let callers create root-owned symlinks anywhere, for example in /etc/sudoers.d;
  - the argv[1] path-basename form;
  - the `-`/`_` equivalence;
  - the secret deny list (packaging Python files only replaces it).

---

## 5. Decisions the owner must make

**5.1 §11 Project scope, 2026-10-06: "Loose scripts are out of scope"** (docs/IMPLEMENTATION_PLAN.md:2163)

- Options:
  - (a) reverse it only for pyprojects without a build system (UC3);
  - (b) reverse it for those, for loose directories and for single files (UC3, UC6, UC8);
  - (c) keep it.
- Recommendation: (b), shipped in plan order with UC3 first.
- Why:
  - One mechanism serves all three: the hand-written wheel plus script commands.
  - UC3 has the only measured audience and keeps today's lock path.
  - UC6's value depends on accepting the platform reach in section 6.

**5.2 §11 Multi-command executables, 2026-10-18: "The app's main dispatches on sys.argv[0] ... No launcher change"** (:2169)

- Options:
  - (a) the boot dispatches from a `name=target` table in the existing entrypoint Str;
  - (b) the table goes in the build_info JSON, which adds a JSON parse per start;
  - (c) keep app-level dispatch, so projects with several scripts keep failing.
- Recommendation: (a). The dispatcher form is argv[1] as an exact command name. Without a command, the binary lists the commands and exits 2.
- Why:
  - The "no launcher change" half of the decision stays true.
  - argv[0] reaches Python unchanged (experiment-confirmed).
  - C treats the entrypoint as an opaque str (module.c:760-778), and the writer rejects only NUL (format.py:278-286).
  - The reader ships in the same file, so no format_version bump is needed; bumping it remains the owner's choice.

**5.3 D14: isolated interpreter, "the app's argv stays untouched", knobs only through FSBPYR_\*** (:106)

- (i) Should the dispatcher form shift sys.argv? Recommendation: yes, in the dispatcher form only. Link-named starts keep argv untouched.
- (ii) Should PYTHONUNBUFFERED be honoured as a named exception? Recommendation: yes, only that variable. Why: the defect is measured (UC1), and it carries no security weight.
- (iii) Should argv[0] become the AT_EXECFN path, as a venv gives it? Recommendation: defer. Prevalence is unmeasured. Note that `exec -a` renames argv[0] for an fsbpyr app but not for a venv script, so parity fails the other way too.

**5.4 D23/D44: same-executable children** (:115, :136)

- Options:
  - (a) a start whose basename(argv[0]) names a command and differs from basename(sys.executable) is always that command, so `_interpreter_child` is skipped;
  - (b) keep the check for every start.
- Recommendation: (a).
- Why:
  - Sibling commands pass D22's same-executable parent check (mp.py:150-165), being the same inode.
  - Without (a), command A starting `B -m word` exits 70; this was experiment-confirmed with today's single-command build.
  - With `interpreter-reexec = true`, A passing caller input as `B -c CODE` would run it as Python, which separate binaries never allowed.
  - Helpers started through sys.executable (the real path) keep D44, so joblib and loky stay refused.
- Limit: hard-link-named starts keep the check, because there sys.executable is the link's own path.
- Deferred: the D44 exception for scripts that start siblings by payload path.

**5.5 D45: instance limit** (:137)

- Options:
  - (a) no change, with documentation that every link shares one budget and that FSBPYR_MAX_INSTANCES (slots.c:24-29) raises it;
  - (b) a higher floor for binaries with a table;
  - (c) count online CPUs instead of the affinity mask.
- Recommendation: (a).
- Why:
  - The shared pool is experiment-confirmed: 64 under one-CPU affinity, 112 on 24 CPUs.
  - There is no evidence of more than 64 concurrent commands per user.
  - The guard was added after a host went down on 2026-10-24.

**5.6 D5: "uv does all resolving and building; the developer's venv is never read"** (:97)

- (i) May fsbpyr write the project's wheel itself when there is no build system?
  - Options: a hand-written deterministic wheel; a generated pyproject plus uv_build in a staging copy; files copied into the site with a hand-written dist-info.
  - Recommendation: the hand-written wheel.
  - Why:
    - uv_build's fast path needs a matching bundled version; otherwise uv downloads it, which breaks `--offline`, and fsbpyr enforces no minimum uv (D26).
    - uv_build also expects `__init__.py` files and one top-level module.
    - A dist-info copy bypasses the uniform install path.
    - Writing the wheel runs no project code and writes nothing into the tree.
- (ii) Should undeclared dependencies be read from the venv?
  - Recommendation: no, not in v1.
  - Why: D5's rejected alternative stands, and pins from old venvs are often unbuildable. Instead, the build errors with file:line and the freeze command.

**5.7 D24: `__file__` for the user's own scripts** (:116)

- Options:
  - (a) the payload path (today), plus a build-time report and a reworded note;
  - (b) a binary-directory overlay;
  - (c) the invoked link's directory (AT_EXECFN).
- Recommendation: (a) in v1.
- Why:
  - sys.path hacks into the tree (22% of survey-29 files, mostly two repos) work with payload paths, as experiment-confirmed.
  - The overlay helps only data reads (2.6% of files), sends those hacks to the disk, and can bring back argparse's `<exe> -m x` prog when a `name.py` link equals the origin.
- Required in v1: the note must not advise `--fs-package _fsbpyr_scripts`, which fails with "not a regular package" (classify.py:506-511). It must also cover a flat module that reads `<exe>/x.conf`, which gets no note today.

**5.8 Smoke-test semantics**

- Options:
  - (a) find_spec for script targets, and today's import for `module:attr` and package entries;
  - (b) import everything, as today.
- Recommendation: (a). Also say in the docs that a `--entry module` build runs that module's top-level code on the build host.
- Why:
  - The selftest `__import__`s the entry (_fsbpyr_boot.py:446-451, 590-591). This is known (docs/improvement-plan.md:724), and it already ran an unguarded body during the build (experiment-confirmed: e12 and code-claims).
  - Scripts' top-level code does real work (deploy, delete, restart).

**5.9 A script command whose required import nothing resolves**

- Options:
  - (a) skip that command with a warning (file:line); `--strict` fails; if no command remains, the build fails;
  - (b) fail the build.
- Recommendation: (a).
- Why: one `apt_pkg` import in a 100-script directory should not block a zero-question build, and v1 has no per-script exclude option.

**5.10 A pyproject without [build-system]: always write the wheel, or try setuptools' legacy backend first?**

- Recommendation: always write it.
- Why:
  - It is predictable.
  - It runs no project code, writes no egg-info, and works for multi-module layouts.
  - Cost: single-module projects that build today change their payload layout, so a regression test is required.

**5.11 `--strict` with VCS requirement lines**

- Options:
  - (a) refuse them early with fsbpyr's own message;
  - (b) drop `--require-hashes` when every unhashed line is a SHA-pinned VCS URL;
  - (c) leave uv's message.
- Recommendation: (a) now, and (b) only on demand.
- Why: uv 0.13.5 rejects them under `--require-hashes` (experiment-confirmed). A SHA pins the source but is not a hash.

**5.12 D42: the glibc 2.34 baseline** (:134)

Superseded by section 9, the EL8 sysroot option. The verifiers' original options here (a "glibc 2.28" flavour from the Debian 10 pins on archive.debian.org, or a second embedded stub) are void: Debian 10 is out of vendor support, and the owner rejects unsupported baselines (2026-10-25). The owner's frame is that EL8 can only ever be a build-time option, with the Debian LTS sysroot staying the default, and the main objection is release CI cost. Section 9.7 lists the decisions.

---

## 6. Platform reach

### 6.1 Targets

These rows come from container runs of fsbpyr 0.25.0's own binary on 2026-10-25, unless noted.

| OS | glibc | System python3 | Vendor status, Oct 2026 | fsbpyr runs |
|---|---|---|---|---|
| RHEL / Rocky / Alma 8 | 2.28 | platform-python 3.6.8, no python3 by default | maintenance to 2029-05-31 | **no**: GLIBC_2.29/2.32/2.33 not found |
| Oracle Linux 8 | 2.28 | 3.6.8 | premier to 2029-07-31 | **no** |
| RHEL / Rocky / Alma / OL 9 | 2.34 | 3.9 for the whole lifecycle; 3.11 and 3.12 available | maintenance to 2032-05-31 | yes (rockylinux:9 is in the matrix) |
| RHEL / Alma / Rocky 10 | 2.39 | 3.12 | full support to 2030-05-31 | yes |
| CentOS 7 / RHEL 7 ELS | 2.17 | 2.7.5 | EOL 2024-06-30; paid ELS to 2029 | **no** |
| Amazon Linux 2 | 2.26 | 2.7.18; python3 3.7.16 | end of support 2026-06-30 | **no** |
| Amazon Linux 2023 | 2.34 | 3.9 for life; 3.11-3.14 available | standard to 2027-06-30, security to 2029-06-30 | yes (in the matrix) |
| Ubuntu 20.04 | 2.31 | 3.8 | ESM only, to 2030 | **no** |
| Ubuntu 22.04 | 2.35 | 3.10 (upstream EOL Oct 2026) | standard to 2027-06 | yes |
| Ubuntu 24.04 / 26.04 | 2.39 / 2.43 | 3.12 / 3.14 | supported | yes |
| Debian 11 | 2.31 | 3.9 | paid ELTS only | **no** |
| Debian 12 | 2.36 | 3.11, system pip refused (PEP 668) | LTS to 2028-06-30 | yes |
| Debian 13 | 2.41 | 3.13 | stable | yes |
| SLES 15 SP4 / SP5 | 2.31 | 3.6.15 | LTSS only | **no** |
| SLES 15 SP6 / SP7 | 2.38 | 3.6.15 (python311 and python313 available) | SP7 general support to 2031-07-31 | **yes**. This corrects round 1 and CHANGELOG.md:142. It is not in the matrix. |
| SLES 16 / Leap 16.0 | 2.40 | 3.13 | supported | yes |
| Raspberry Pi OS 64-bit (Trixie / Bookworm) | 2.41 / 2.36 | 3.13 / 3.11 | current / legacy | **unverified**: the Pi 5's default kernel uses 16K pages, which are untested |
| ubi8 / ubi8-minimal | 2.28 | 3.6.8 / none | RHEL 8 lifecycle | **no** |
| ubi9 / ubi9-minimal / ubi10 | 2.34 / 2.34 / 2.39 | 3.9 / none / 3.12 | supported | yes (none in the matrix) |
| distroless base / cc (Debian 12, 13) | 2.36 / 2.41 | none | supported | yes for pure Python. `base` lacks libgcc_s and libstdc++, so native apps need `cc`. Native apps were not tested. |
| distroless static, scratch | none | none | — | **no**: there is no loader |
| Alpine 3.22 (musl) | none | 3.12 | supported | **no**. With gcompat: `dladdr1` and `posix_fallocate64` not found. |
| Wolfi / Chainguard | glibc (version not checked) | none | rolling | yes |

### 6.2 How much of the field is below 2.34

No source counts the server fleet by glibc. These are proxies.

| Source | What it counts | Below 2.34 | Notes |
|---|---|---|---|
| EPEL countme, week of 2026-10-05 | 7.18M EL systems checking in to EPEL | EL8: 41.8% (systems older than 24 weeks: 46.6%; RHEL proper: 48.4%) | EL8's share was 68.0% in Oct 2024 and 54.8% in Oct 2025; its absolute count stays flat at about 3.0-3.2M. EL7 is invisible (EPEL 7 is archived). |
| Debian popcon, 2026-10-24 | 294k opt-in, mostly desktop installs | about 16% (bullseye 7.6%, buster 4.0%, older 4.4%) | — |
| PyPI pip downloads of manylinux wheels (manylinux-timeline) | downloads, CI-dominated | 6.0% (22.6% a year earlier, 54.5% two years earlier) | It heavily under-counts long-lived hosts: EL8 is about 0.5% of downloads but 42% of EPEL systems. |
| Lansweeper, June 2024 | scanned Linux machines | CentOS 7 about 26% | No split by version; data from 2024 |

Estimated reach of the glibc 2.34 baseline:

- about 55-60% of the RHEL family by the EPEL proxy;
- about 84% of Debian popcon installs.

Both are rising every year. They are proxies, not a fleet census.

### 6.3 What it means

- Inside the reach, the owner's "wrong Python" problem is real and long-lived:
  - EL9, OL9 and AL2023 keep python3 at 3.9;
  - SLES 15 SP6/SP7 keep 3.6.15;
  - Ubuntu 22.04's 3.10 reaches upstream EOL this month;
  - Debian 12 refuses system pip.
- The distributions soften part of it. AL2023 packages 3.11-3.14, and RHEL 9 has 3.11 and 3.12 streams. fsbpyr's edge there is no per-host module install and no PEP 668 friction, not the absence of a newer interpreter.
- The largest supported pocket of the oldest Python, EL8 with platform-python 3.6 maintained to 2029, is out of reach. So are CentOS 7, AL2, Ubuntu 20.04, Debian 11, SLES 15 SP4/SP5 and every musl image.
- The glibc baseline is the biggest platform limit for loose scripts, but not the only one:
  - fsbpyr's Python 3.12 floor (D13) cannot run Python 2 scripts, or 3.6-era scripts that use `imp`, `asyncore`, or (on 3.13+) `cgi`, `telnetlib` and `crypt`;
  - ops scripts that import `dnf`, `rpm`, `selinux` or `apt` bindings cannot be bundled on any baseline.
- Positioning should name the reach plainly: RHEL/OL/Alma/Rocky 9-10, AL2023, Ubuntu 22.04+, Debian 12+, SLES 15 SP6+, SLES 16, with EL8 named as the gap.
- Not verified: FIPS-mode behaviour of the static OpenSSL, and use of the system CA store on RHEL-family hosts (docs/compatibility.md also lists these as untested).

---

## 7. Pre-existing bugs and gaps

These are independent of the new features.

1. **`fsbpyr build` writes `<name>.egg-info` into the project tree for setuptools-built projects.**
   - Status: experiment-confirmed with the legacy fallback (e1b, e1c) and with explicit setuptools projects (e4, e6). It was also reproduced with `uv build` alone, on a project without a build system. uv_build projects stayed clean.
   - Contradicts: the staging.py docstring ("the developer's tree is never written to", lines 5-7) and docs/IMPLEMENTATION_PLAN.md:678.
   - Cause: setuptools' sdist step runs in the source directory under `uv build` (staging.py:117-119). No test checks for it.
   - Fix options (unverified): run `uv build` on a temporary copy of the tree, or remove an egg-info that the build created. For projects without a build system, it disappears with decision 5.10.
2. **Cache-root mismatch when HOME is unset or empty.**
   - Status: experiment-confirmed (code-claims: no `.gc` in 3 runs).
   - Cause: `native.py:39-49` falls back to the passwd home, while `due.c:55-83` needs `$HOME` starting with `/`. With `HOME=''`, Python gives `/.cache/<app>` and C gives nothing.
   - Effect: D38 auto-collection never runs, so the cache grows. `FSBPYR_GC=1` and `fsbpyr cache gc` still work.
   - Fix: plan step 1.
3. **PYTHONUNBUFFERED has no effect.**
   - Status: experiment-confirmed.
   - Effect: stdout to a pipe is block-buffered, and output is lost on SIGTERM (0 lines against 2). stderr is unaffected.
   - Cause: D14 by design (main.c:137-142); no `buffered_stdio` handling exists.
   - Fix: plan step 1.
4. **The smoke test runs the entry module's top-level code on the build host.**
   - Status: experiment-confirmed (e12 marker: argv `.side.<8 chars>.tmp`, FSBPYR_SELFTEST=1).
   - Cause: _fsbpyr_boot.py:446-451, 590-591; build.py:337-356.
   - It is known and stated in docs/improvement-plan.md:724. It matters for `--entry module` builds of script-like modules. It must not apply to script commands (decision 5.8).
5. **`uv build --quiet` hides the cause of a failure.**
   - Status: experiment-confirmed.
   - Cause: staging.py:119 passes `--quiet`, so users see only "Call to setuptools.build_meta:__legacy__... failed". The real reason ("Multiple top-level modules discovered in a flat-layout") is hidden.
   - Under `-v`, fsbpyr says "see its output above", but the output is not there.
6. **A static project with zero console scripts fails only after the full staging.**
   - Status: experiment-confirmed (0.14 s for two scripts against a full uv build and install for zero).
   - Cause: build.py:327 checks only `> 1`.
7. **Compile warnings are silenced everywhere.**
   - Status: experiment-confirmed with plain, `-v` and `--strict` builds.
   - Cause: compiler.py:34-36 uses `simplefilter("ignore")`, so SyntaxWarnings (for example `"\d"`) show neither at build nor at run time.
   - This is a diagnostic gap, not a crash. A venv run prints them.
8. **Legacy `setup.py scripts=` files are dropped without a build message.**
   - Status: experiment-confirmed (e4).
   - Cause: classify.py:271-280 exempts `bin/`.
   - It is documented only generically (docs/compatibility.md:17).
9. **A `.py` file under a non-identifier directory becomes a resource without a warning.**
   - Status: experiment-confirmed (e13 `sub-dir/c.py`).
   - Cause: classify.module_name, classify.py:120.
10. **The D24 note has gaps.**
    - Status: code-confirmed.
    - There is no note for a flat top-level module that reads `<exe>/x.conf`, because the path component is not a module.
    - For a namespace directory, the advised `--fs-package` fails with "not a regular package" (_fsbpyr_boot.py:427-443; classify.py:506-511).
11. **A setup.cfg-only directory is refused late.**
    - Status: experiment-confirmed.
    - It passes Project.load (project.py:51), then uv fails with "neither `pyproject.toml` nor `setup.py` are present".
12. **Requirements handling.**
    - Status: code-confirmed and experiment-confirmed.
    - `-e` lines are never reported as unpinned (project.py:148).
    - `--strict` with SHA-pinned VCS lines fails inside uv with a generic hash message (staging.py:189).
13. **`--interpreter-reexec` script children lack their directory on sys.path.**
    - Status: experiment-confirmed.
    - `[sys.executable, <exe>/pkg/a.py]` runs as module `pkg.a`, and `import common` from the same directory fails, unlike CPython (reexec.py:105-113).
14. **The `[sys.executable] + sys.argv` restart idiom differs from a venv.**
    - Status: experiment-confirmed (e7c).
    - The child gets the binary's path as an extra positional argument and runs the app with it. gunicorn's USR2 and Django's autoreloader use this idiom.
15. **Documentation is stale on SLES 15 SP6/SP7.**
    - Status: experiment-confirmed, container runs.
    - CHANGELOG.md:142 says "SLES/openSUSE Leap 15 are no longer supported". SP6/SP7 have glibc 2.38 and run fsbpyr apps; SP7 is supported to 2031.
    - README.md:5-7 and docs/compatibility.md:30-32 omit them, and the matrix (tests/support/images.py) has no SLES 15 image.
16. **A workflow comment is stale.**
    - Status: code-confirmed.
    - .github/workflows/full-suite.yml:374 says "13 distributions (glibc 2.28 to 2.44; the 12 with an arm64 variant)". tests/support/images.py lists 11 images, glibc 2.34-2.44, 10 of them with arm64.
17. **Two small UX items.**
    - Status: experiment-confirmed.
    - CPython 3.15's ModuleNotFoundError hint in fsbpyr apps reads "Site initialization is disabled, did you forget to add the site-packages directory to sys.path or to enable your virtual environment?", which misleads fsbpyr users.
    - A failed build leaves an empty `dist/` in the tree.
18. **Hard links: sys.executable is the link's own path.**
    - Status: experiment-confirmed.
    - `__file__` and sys.path[0] therefore differ per hard-link name. For symlinks they are the resolved target. This is not documented as far as checked.
19. **Self-update does not check the host's glibc.**
    - Status: code-confirmed by the EL8 skeptic (no glibc check in selfupdate.py or dispatch.py; selfupdate.py:39, :112); not run.
    - Effect: an install of 0.21.0-0.23.0 (glibc 2.28 baseline) on an EL8 host that runs `fsbpyr self update` would replace itself with a 2.34 executable that cannot start there.
    - Fix: a host-glibc guard against the asset's recorded glibc, about 10-20 lines. It is independent of the EL8 option.

Item 1 (egg-info) was reproduced once more with uv 0.13.5's `uv build` alone, on a src-layout setuptools project: `demo.egg-info` appeared under `src/` in the source tree.

---

## 8. Corrections to round 1

| Round-1 claim | Verdict | Correct statement | Source |
|---|---|---|---|
| main.c:145 parse_argv, :152 SetBytesArgv, :153 executable | corrected | main.c:147, :154, :155 | code-claims |
| build_info JSON is parsed only in reexec.py:131 | corrected | reexec.py:132, and lazily in native.py:94 and :111 | code-claims |
| PayloadMetaFinder serves only namespace packages | partly | It also serves the distutils shim and importlib.metadata distributions. For regular modules, sys.path order does decide. | _fsbpyr_boot.py:347-394 |
| `uv build` runs after `uv sync` | corrected | `uv build` runs first, for every lock source | staging.py:139, 174, 294-306 |
| Legacy scripts are detected by `#!python` in the staged site | corrected | Staging rewrites the shebang, so `#!python` exists only in the wheel | code-claims (installed RECORD) |
| Temporary smoke name `.{name}.XXXX.tmp` | corrected | 8 random characters. `--smoke-args` runs with argv[0] set to the final output path. | writer.py:593; build.py:364-366 |
| pins.py:49,57 | corrected | 3.15.1 is on :58 | pins.py |
| Child refusal at reexec.py:53-55 | corrected | asks_for_python is at reexec.py:55-70. The refusal fires only when `-m`/`-c` is the first non-flag argument and not the last, or for `<exe>/...py` arguments. | reexec.py:29-39, 55-70 |
| Forkserver preload "must use the parent's command or skip" | refined | Unchanged, a table would silently preload nothing (the error is swallowed at mp.py:177), not crash | mp.py:176-177, 200 |
| Compile-time SyntaxWarnings appear at build time | **refuted** | They are suppressed at build time and never seen at run time | compiler.py:34-36 |
| Counting SHA-pinned VCS lines as pinned fixes `--strict` | **refuted** | `--strict` adds `--require-hashes`, and uv rejects such lines | staging.py:189; uv 0.13.5 run |
| A D45 change is needed | refined | FSBPYR_MAX_INSTANCES already overrides the limit (up to 4096) | slots.c:24-29 |
| "No other single-binary tool reads uv.lock directly" | **refuted** | cosmofy, pyfuze, PyCrucible and bundleup consume it | competitors lens |
| PyInstaller MERGE is "effectively broken" | **refuted** | It was restored in 5.7.0, with fixes in 6.3 and 6.10 (untested). It still gives N executables. | PyInstaller CHANGES.rst |
| Nuitka multidist: experimental, needs one --main per program | corrected | The 4.3.1 README has no experimental label. `--project` derives entries from [project.scripts]. `--zig` downloads a compiler. (Read from source, not run.) | Nuitka 4.3.1 sdist |
| pex busybox needs commands named by hand | corrected | `@` and `@<project>` select all console scripts | pex scie/__init__.py:254-283 |
| pex honours a sibling pylock for PEP 723 (2.52.0) | corrected | That applies to `pex3 run`, not to building a PEX | pex CHANGES.md |
| The script's own uv lock honoured is fsbpyr-only | **refuted** | cosmofy does it for pure-Python scripts | cosmofy bundle.py:262-266 |
| No tool maps `__file__` to the deployment directory | corrected | True only for single files. Nuitka standalone and PyInstaller onedir do it for directories. | Nuitka README; PyInstaller docs |
| A tampered binary refuses to run (D31) | **refuted** | It detects damage only | IMPLEMENTATION_PLAN.md:123 |
| Reproducible bytes and PYTHON* isolation are fsbpyr-only | **refuted** | pex and PyInstaller have both, to different degrees | competitors lens |
| Pure-Python zero extraction is fsbpyr's alone | partly | True among the tools measured; cosmofy and pyfuze portable also do it (pure Python, APE) | competitors lens |
| PyInstaller downloads 36.2% Windows, 8.1% macOS | partly | Right for the last month; 30.7% and 6.5% over Apr-Oct 2026. CI dominates both, and only 6.5% of PyInstaller workflows are Linux-only. | pypistats; GitHub code search |
| Loose scripts have "the highest prevalence of all candidates" | **refuted** | Unknown, plausibly medium. Requirements-only directories are mostly web or ML. | prevalence lens |
| Stack Overflow 51455765 shows loose-script demand | **refuted** | It is a PyQt GUI app with 5-6 modules | api.stackexchange.com |
| JetBrains: 26% use Python for DevOps | partly | The question is multi-select; 6-7% name it as their main use | JetBrains 2023, 2024 |
| JetBrains: 59% requirements.txt vs 36% pyproject.toml | partly | Multi-select; it does not mean 59% lack a pyproject | JetBrains 2024 |
| survey-29 percentages describe script fleets | partly | They are dominated by learner and hacktoberfest repos; use them qualitatively only | prevalence lens |
| The "# /// script" count is noise (loose-scripts lens) | **refuted** | 300 of 300 sampled hits are real PEP 723 blocks | prevalence lens |
| Code-search counts include forks | corrected | Forks are excluded unless they have more stars than the parent. Archived and inactive repos are excluded. Large totals are extrapolated (about ±10%). | docs.github.com searching-code |
| uv #5802 is documented demand for bundling PEP 723 scripts | partly | It is generic and cross-platform; 3 of 70 comments concern scripts. The cited comment proposes a bootstrap that downloads uv. | gh api |
| prometheus-client's 135.5M downloads support the monitoring use case | **refuted** | Its use as a library dependency drives the count | prevalence lens |
| uv's template writes requires-python >= 3.15 | corrected | uv writes the minor version of the newest interpreter it discovers | `uv help init` |
| uv's template file is main.py | corrected | hello.py in uv 0.4.x-0.5.x, main.py from 0.6.0 | uv release notes |
| Non-package pyprojects fail in every case | corrected | A single module with [project.scripts] builds today through setuptools legacy | experiments e1b |
| SLES 15 is excluded ("SP glibc unverified") | corrected | SP6/SP7 have glibc 2.38 and run; SP4/SP5 (2.31) do not | platform-reach lens |
| The D44 refusal of `[sys.executable, dirname(__file__)+'/other.py']` holds generally | refined | Only while `__file__` stays below `<exe>/`; with an overlay the child is not flagged | code-claims |
| The cache-root mismatch stops background collection | confirmed and extended | It also covers `HOME=''`. The effect is cache growth, not failure. | code-claims |
| BorgBackup ships glibc239/243 binaries | partly | True for the 2.0 betas; 1.4.5 ships glibc231 and glibc235 | gh api releases |

---

## 9. EL8 sysroot option (glibc 2.28)

Added after the owner's questions on 2026-10-25. The owner's frame:

- the sysroot must come from a distribution its vendor still supports, so fixes in glibc and friends stay possible;
- EL8 would only ever be a `fsbpyr build` option, and today's Debian LTS sysroot stays the default;
- the main objection is release cost: the full suite would grow from about 40 to about 80 minutes.

Research: a code-cost agent (scratch links against Rocky 8.10; nothing in the repository changed, and no built executable was run), an external-facts agent and a skeptic. The raw results are not in the repository.

### 9.1 Does EL8 meet the owner's rule?

Yes. It meets the rule at parity with Debian 12, and it stays valid longer.

| Distribution (glibc 2.28) | Security support ends | Note |
|---|---|---|
| RHEL 8 | 2029-05-31 (Maintenance); ELS to 2033-05-31 | 8.10 is the final minor release |
| Rocky Linux 8 | 2029-05-31 | package versions (NVRs) identical to RHEL's |
| AlmaLinux 8 | 2029-05-31 | gcc and libgcc are its own `.alma.1` rebuilds |
| Oracle Linux 8 | 2029-07-31; extended to 2032-07-31 | carries its own patches; lags Red Hat |
| *Debian 12, today's sysroot* | *LTS to 2028-06-30* | EL8 outlasts it by about 11 months |

- **Fixes keep coming.** There were 9 glibc security advisories (RHSAs) for RHEL 8 between 2025-04 and 2026-10, six of them in 2026.
  - AlmaLinux published each within 0-1 day and Rocky within 0-4 days. Oracle took 0-14 days and still lacks the last two (.43 and .48).
  - In practice Red Hat fixes Moderate glibc CVEs even below CVSS 7, more than its written policy requires.
  - Two CVSS 7.0 CVEs are still open ("Affected"); a few lower ones are marked "Fix deferred".
  - Sources: Red Hat security data API; AlmaLinux errata feed; repository listings.
- **What the sysroot actually puts into an app.** The stub links `-static-libgcc` (`stub.py:56-60`). The only sysroot code inside an app is:
  - `crt1`/`Scrt1`, `crti`, `crtn`;
  - `libc_nonshared.a` and `libpthread_nonshared.a`;
  - `crtbeginS`/`crtendS`, and `libgcc.a`/`libgcc_eh.a`.

  glibc itself is always the target host's, at run time. Today's Debian pins come from bookworm's point-release main suite and leave out `bookworm-security` by design (`pins.py:108-111`). So an EL8 sysroot is at parity, and no new pin-watching tooling is needed.
- **Package source.** Rocky Linux 8.10 (`dl.rockylinux.org/pub/rocky/8.10/`) is the recommended source:
  - it keeps every 8.10 build at a stable URL: 17 glibc-devel builds, and every gcc 8.5 build on both architectures;
  - its repodata is signed (`repomd.xml.asc`) and lists each build's SHA-256;
  - its NVRs match RHEL's.

  AlmaLinux is equivalent apart from its own gcc rebuilds. The two rejected sources:
  - **UBI 8:** only the newest build is listed, the repodata is unsigned, and EULA §2 is unclear for statically linked parts.
  - **Oracle Linux 8:** it lags Red Hat, carries its own patches and has unsigned repodata.

  When 8.10 reaches end of life it will probably move to the vault, as 8.9 did; this is inferred, not verified. That only matters for rebuilding old releases after 2029.
- **Licence:** the same as Debian's: the GCC Runtime Library Exception and glibc's linking exceptions (a reading, not legal advice).

### 9.2 Does glibc 2.28 work with today's launcher and Pythons?

These results come from links on the build host. Nothing was run on an EL8 host.

- **Packages.** Six RPMs per target: glibc, glibc-devel, glibc-headers, kernel-headers and libgcc from BaseOS, and gcc 8.5 from AppStream. On EL8 only the gcc package has `crtbeginS.o`, `libgcc.a` and `libgcc_eh.a`.
  - The download is about 40 MB on x86_64 and 35 MB on aarch64, against 9.5 MB for today's Debian set. Extracted, the sysroot is 26 MB against 49 MB.
  - A stdlib-only RPM reader of about 100 lines (xz-compressed newc cpio, including hard links) parsed all six. Its output goes through the existing `_into_sysroot`/`data_filter` path.
- **Layout.** The sysroot needs a `lib64 -> usr/lib64` link, because the linker scripts name `/lib64/...`. `build_flags` must use the Red Hat layout instead of Debian multiarch; the layout belongs on `SysrootPin`. clang 23 then finds GCC 8 by itself.
- **Launcher.** `glibc.c` does not compile against 2.28 headers: `struct dl_find_object` only exists from 2.35. A 3-line `#ifdef DLFO_EH_SEGMENT_TYPE` guard fixes it, and GCC 8's unwinder uses `dl_iterate_phdr`, so the wrapper is not needed there. The other launcher sources compile cleanly.
- **The four Pythons.** With that guard, fsbpyr's own `stub_command` linked 3.12.15, 3.13.17, 3.14.9 and 3.15.1 against Rocky 8.10 on x86_64, about 44 s each. The ASan/UBSan variant links too.
  - The highest symbol version is GLIBC_2.25, a weak `getentropy` from OpenSSL; no strong reference is above 2.17.
  - NEEDED grows from 2 to 6 libraries. 0.15.0 had the same six and measured 1.10x bare CPython against a 1.5x budget (`docs/performance.md:37-55`).
- **aarch64.** Not linked in this research. Strong precedent: 0.21.0-0.23.0 shipped 3.12-3.14 on aarch64 against Debian 10's glibc 2.28, and `rockylinux:8` arm64 was in the portability matrix.
- **Kernel 4.18.** The launcher needs nothing newer. `close_range` is compiled in, because EL8's headers define it, and has a `/proc/self/fd` fallback (`due.c:140-151`). That fallback has never run in CI, because the containers use the runners' 6.x kernels.
- **Open: EL8 aarch64 runs 64 KiB-page kernels.** AlmaLinux's kernel config has `CONFIG_ARM64_64K_PAGES=y`, and the README calls 64K pages untested. The payload is 64 KiB-aligned and the code uses `sysconf(_SC_PAGESIZE)`, so it is expected to work but is unverified. Neither containers nor GitHub's arm runners (4K pages) test it; only a QEMU-system VM would. aarch64 is 2.7% of EL8 hosts in EPEL's counts.
- **Inferred defect (untested).** On an aarch64 build host using gcc, an sdist C extension that uses atomics, rebuilt against GCC 8's `libgcc.a`, would link with undefined `__aarch64_*` outline-atomics symbols. The fix is `-mno-outline-atomics` in the rebuild flags plus a fixture: about 20-40 lines. The same latent issue existed in 0.21-0.23.

### 9.3 Wheels at manylinux_2_28

Only builds that choose 2.28 are affected.

- **Corpus** (73 lock files, 3.12-3.15, both architectures):
  - openmm loses its wheel on 3.12-3.14; it is manylinux_2_34 only, and its C++ sdist cannot be rebuilt against the sysroot.
  - debugpy on x86_64 falls back to its pure-Python wheel, the shape aarch64 already installs (jupyter).
- **PyPI top 1000:** 210 projects ship Linux wheels. Only 3 of them have nothing at 2_28 or older for 3.12-3.14:
  - caio and debugpy, which have pure-Python fallbacks;
  - semgrep, whose sdist needs OCaml.

  Outside the top 1000, PyQt6 and PySide6 (x86_64) are lost.
- **Trend:** projects settled at 2_27/2_28 in 2025-2026. cibuildwheel still defaults to manylinux_2_28, and pypa/manylinux labels its 2_34 image ALPHA.

### 9.4 Who it reaches

- **EPEL countme, week of 2026-10-12:**

  | Release | Machines | Share |
  |---|---|---|
  | EL8 | 3.10 M | 42.7% |
  | EL9 | 3.64 M | 50.2% |
  | EL10 | 0.51 M | 7% |

  The EL8 machines are Rocky 1.21 M, AlmaLinux 0.71 M, RHEL 0.64 M and Oracle 0.24 M (undercounted). The EL8 count is down about 9% year on year. These are only machines that enable EPEL, not a market share.
- **pip downloads:** only 0.55% of manylinux wheel downloads come from glibc 2.28-2.30. Many EL8 hosts exist, but few install modern wheels. That fits the copy-deploy scenario, where the target has no modern Python (an inference).
- **Not quite Python-less:** RHEL 8.10 AppStream ships python3.11 and python3.12. The default python3, however, is 3.6.

### 9.5 Delivering it as a build-time option

| Option | What it is | Code (estimate) | Release and CI |
|---|---|---|---|
| **A1: full flavour** (researched) | `--glibc 2.28` / `[tool.fsbpyr] glibc = "2.28"` fetches the same release's complete 2.28 fsbpyr flavour and reuses its runtime (`Runtime.from_executable`, no second process); another Python dispatches to that flavour | L+: about +1250-1650 lines, about 2 weeks. It includes installer, self-update and dispatch flavour logic and the selfupdate regex | Assets go from 8 to 16 executables (each with .xz, licences, SBOM). CI: about +60 job-minutes with a reduced matrix, about +400 with a full one; the full suite today is about 400 job-minutes |
| **A2: EL8 stub only** (proposed in this report; unverified) | A runtime is stub bytes plus a stdlib fragment from PBS, and the fragment does not depend on the sysroot (`registry.py:230-260, 308-338`). A release adds one EL8 stub per (Python, architecture): 21.0 MB, 6.5 MB as xz, measured on 0.25.0's stub. `fsbpyr build --glibc 2.28` fetches the stub of its own release and pairs it with its own stdlib frames, cached like `dispatch.py`. Builds for EL8 run on a modern host, which is the copy-deploy scenario | M-L: the sysroot work (about +300: RPM reader and tests, pins, layout, glibc.c guard, aarch64 atomics flag) plus option plumbing (about +200-300: config and CLI, runtime selection by (Python, baseline), stub fetch, `natives.py` limits and uv platform taken from the pin). About 1 week | 8 small extra assets; no installer or self-update change. CI below |
| B: 2.28 as the only default | Section 9.6 | — | — |

A2 does not cover running fsbpyr itself on an EL8 build host. Its trust model is dispatch's today: HTTPS plus a digest. A digest table of the stubs embedded in fsbpyr would be stronger; it is possible because the links are reproducible (open).

**CI for A2: test only what differs.** An EL8 build differs from a Debian build only in the stub and in uv's platform tag. Boot code, payload format, classification and build pipeline are the same bytes. Per (Python, architecture), the EL8 matrix is:

1. link and reproduce the EL8 stub, about 2 minutes;
2. portability: apps built with the EL8 stub on the 11 current images plus `rockylinux:8` and `almalinux:8`, about 2 minutes;
3. the stub-dependent tests: C launcher, `_fsbpyr` contract, and the e2e tests that run built executables. Their share of the 25-30 minute suite is unmeasured; pytest durations from a CI log would show it;
4. a native-heavy corpus subset in an EL8 container (manylinux_2_28 wheels).

Measured on run 40177685276 (b32fbe6, 2026-10-25):

- the run was 48 jobs and 39.2 minutes of wall time;
- its 8 suite jobs all start at minute 0 and take 28-37 minutes, so the wall time is the slowest suite job;
- corpus jobs took 7-15 minutes, portability 1.5-2, build and reproduce about 2 each.

Short EL8 jobs started next to the suite jobs need not lengthen the run. Whether they queue depends on the account's concurrent-job limit (unverified). The alternative is to decouple: the main full suite gates the release as today, and a separate EL8 workflow attaches the stub assets when it goes green. `--glibc 2.28` then says plainly when a release has no EL8 stubs yet.

### 9.6 Stated once, for completeness: 2.28 as the only baseline (B)

The skeptic recommended B: replace Debian 12 by Rocky 8.10 for both targets until EL8 ends in 2029.

- Size: M, 3-5 days, about +500-650 / -150-400 lines.
- Release cost: no new assets, and CI grows only by two portability images (minutes).
- Wheel cost: as in 9.3, but for every build.
- It reverses the owner's 2026-10-25 decision that Debian stays the default, and it needs another sysroot move in 2029. The x86-64-v2 floor of an EL9 sysroot must be checked before naming EL9 as the successor.

It is cheapest on the release-cost axis the owner raised. The owner's decision stands unless the owner reopens it.

### 9.7 Decisions for the owner

1. **Vendor:** accept Rocky Linux 8.10, or AlmaLinux 8.10, as the "supported vendor" under the D42 rule? UBI and Oracle are not recommended (9.1).
2. **Delivery:** A2 (stub-only assets; recommended within the owner's frame), A1 (full flavours), or not at all.
3. **Release coupling:** EL8 jobs in the main full suite, or a decoupled EL8 workflow that attaches stub assets after the release.
4. **Surface:** a CLI-only `--glibc 2.28`, or also `[tool.fsbpyr] glibc`? Each new setting works against the Phase 11 adoption concern. A CLI flag fits the "build on a workstation for EL8 hosts" case.
5. **EL8 aarch64 (64K pages, kernel 4.18):** add a QEMU-system VM smoke job, or document EL8 aarch64 as untested?
6. **Wrappers:** keep `hypot` and `_dl_find_object`. The Debian default still needs them; only the guard is new.
7. **Independent of EL8:** add a host-glibc guard to self-update (section 7, item 19).

### 9.8 Sources (EL8)

- Life cycles:
  - https://endoflife.date/rhel
  - https://endoflife.date/rocky-linux
  - https://endoflife.date/almalinux
  - https://endoflife.date/oracle-linux
  - https://endoflife.date/debian
  - https://access.redhat.com/support/policy/updates/errata
  - https://access.redhat.com/product-life-cycles/api/v1/products?name=Red%20Hat%20Enterprise%20Linux
- Repositories:
  - https://dl.rockylinux.org/pub/rocky/8.10/BaseOS/x86_64/os/Packages/g/
  - https://dl.rockylinux.org/vault/rocky/8.9/
  - https://repo.almalinux.org/almalinux/8.10/BaseOS/x86_64/os/Packages/
  - https://vault.almalinux.org/8.9/
  - https://cdn-ubi.redhat.com/content/public/ubi/dist/ubi8/8/x86_64/baseos/os/
  - https://yum.oracle.com/repo/OracleLinux/OL8/baseos/latest/x86_64/index.html
  - UBI EULA: https://www.redhat.com/licenses/EULA_Red_Hat_Universal_Base_Image_English_20190422.pdf
- Security data:
  - https://access.redhat.com/hydra/rest/securitydata/cve.json?package=glibc&after=2024-06-01
  - https://errata.almalinux.org/8/errata.full.json
- Wheels:
  - https://pypi.org/pypi/<name>/json
  - https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json
  - https://raw.githubusercontent.com/pypa/manylinux/main/README.rst
  - https://cibuildwheel.pypa.io/en/stable/options/
- Fleet:
  - https://data-analysis.fedoraproject.org/csv-reports/countme/totals.csv
  - https://mayeut.github.io/manylinux-timeline/consumer-data.json
- 64K pages:
  - kernel-core-4.18.0-553.177.1.el8_10.aarch64 config (repo.almalinux.org)
  - README.md:100-102
- Repository:
  - `git show e0dbd6c e420ad1 5db7c31 3e68842 f408bce`
  - `src/fsbpyr/runtime/{sysroot,pins,registry,stub}.py`
  - `src/fsbpyr/launcher/{glibc,due}.c`
  - `src/fsbpyr/targets.py`
  - `src/fsbpyr/natives.py:63-80`
  - `src/fsbpyr/dispatch.py`
  - `src/fsbpyr/selfupdate.py:39,112`
  - `tools/release/install.sh.in:133-165`
  - CHANGELOG.md [0.21.0]-[0.23.0]
- CI: `gh run view 40177685276 --repo devitops-com/fsbpyr --json jobs`

---

## 10. Sources

### Repository (HEAD 74deed8)

- Launcher:
  - `src/fsbpyr/launcher/main.c:120-155` (D41 refusal, isolated config, argv, executable);
  - `module.c:760-778`;
  - `slots.c:23-37, 91, 102`;
  - `due.c:55-83`;
  - `payload.c:110`.
- Boot:
  - `src/fsbpyr/boot/_fsbpyr_boot.py:103, 147-156, 204-212, 300-317, 347-401, 427-451, 541-552, 555-610`;
  - `_fsbpyr_rt/reexec.py:29-70, 105-140`;
  - `_fsbpyr_rt/mp.py:150-177, 200`;
  - `_fsbpyr_rt/native.py:39-49, 77-111`.
- Build:
  - `src/fsbpyr/project.py:43-63, 90-105, 142-153`;
  - `staging.py:1-13, 79-91, 117-144, 170-191, 294-306`;
  - `closure.py:85-101, 114-116, 154-189`;
  - `build.py:83-85, 228-236, 286-287, 323-370`;
  - `classify.py:111-124, 271-280, 342-349, 396-410, 506-511`;
  - `assemble.py:95-103, 133`;
  - `compiler.py:34-36, 56-61`;
  - `scan.py:119-134`;
  - `config.py:28, 76-77, 144-165`;
  - `cli.py:83-84`;
  - `payload/writer.py:589-629`;
  - `payload/format.py:19, 278-301`;
  - `runtime/pins.py:49, 58, 111-177`;
  - `manifest.py:79-92`.
- Docs:
  - `docs/IMPLEMENTATION_PLAN.md` (D5 :97, D14 :106, D23 :115, D24 :116, D28 :120, D31 :123, D42 :134, D44 :136, D45 :137, :678, Phase 11 :1992ff, §11 :2163, :2169);
  - `docs/format.md:99`;
  - `docs/compatibility.md:15-19, 30-32`;
  - `docs/performance.md:596-702`;
  - `docs/improvement-plan.md:724`;
  - `README.md:1-12, 55-58`;
  - `CHANGELOG.md:138-146`.
- Tests and CI:
  - `tests/support/images.py`;
  - `tests/unit/builder/test_closure_and_classify.py:774`;
  - `.github/workflows/full-suite.yml:372-375`.
- Process note: while the verifiers ran, another session had uncommitted edits in the working tree (Makefile, docs/IMPLEMENTATION_PLAN.md, tests/integration/test_release.py, tools/release/check-release.sh). It has since committed 45f28c4 on another branch. All plan line numbers here are from HEAD 74deed8.

### Research data

The raw research data is not in the repository. It comprises the agents' JSON results, the experiment log (experiments e1-e13, run with fsbpyr 0.25.0-723e1966 and uv 0.13.5, each build and run in a systemd user scope) and the sampling and EL8 scripts.

### uv

- Releases:
  - https://github.com/astral-sh/uv/releases/tag/0.4.0 (no [build-system] by default);
  - https://github.com/astral-sh/uv/releases/tag/0.6.0 (main.py);
  - https://github.com/astral-sh/uv/releases/tag/0.12.0 (packaged by default).
- Issues and PRs:
  - https://github.com/astral-sh/uv/issues/5802;
  - https://github.com/astral-sh/uv/issues/5802#issuecomment-2395199353;
  - https://github.com/astral-sh/uv/issues/2799;
  - https://github.com/astral-sh/uv/pull/21250;
  - https://github.com/astral-sh/uv/issues/11746;
  - https://github.com/astral-sh/uv/issues/7828.
- Docs:
  - https://docs.astral.sh/uv/concepts/build-backend/;
  - https://docs.astral.sh/uv/concepts/projects/config/.
- https://pydevtools.com/blog/uv-0-12-packaged-by-default/

### Competitors

- PyInstaller:
  - https://pyinstaller.org/en/stable/usage.html;
  - https://pyinstaller.org/en/stable/operating-mode.html;
  - https://pyinstaller.org/en/stable/spec-files.html;
  - https://pyinstaller.org/en/stable/runtime-information.html;
  - doc/CHANGES.rst (develop, through 6.22.3);
  - https://github.com/pyinstaller/pyinstaller/issues/7907;
  - https://github.com/pyinstaller/pyinstaller/pull/7236.
- Nuitka 4.3.1 sdist, https://pypi.org/project/Nuitka/: options/OptionParsing.py, tree/ReformulationMultidist.py, tools/general/extract_uv_config, build/static_src/OnefileBootstrap.c, README.rst.
- pex 2.105.1:
  - https://github.com/pex-tool/pex/blob/main/CHANGES.md;
  - pex/scie/__init__.py;
  - pex/bin/pex.py;
  - https://docs.pex-tool.org/scie.html;
  - https://github.com/pex-tool/pex/issues/3077.
- scie-jump: https://github.com/a-scie/jump (docs/packaging.md).
- PyApp: https://github.com/ofek/pyapp (docs/config/project.md, docs/runtime.md).
- Single-file and APE tools:
  - cosmofy: https://github.com/metaist/cosmofy, https://docs.metaist.com/cosmofy/bundling/;
  - https://pypi.org/project/pyfuze/;
  - https://github.com/razorblade23/PyCrucible;
  - https://pypi.org/project/bundleup/;
  - https://pypi.org/project/uvbox/;
  - https://github.com/jart/cosmopolitan (Platform Notes).
- shiv: https://github.com/linkedin/shiv/issues/266
- python-build-standalone: https://github.com/astral-sh/python-build-standalone (docs/running.rst).
- Upstream binaries: yt-dlp releases (https://github.com/yt-dlp/yt-dlp/releases) and borg releases (https://github.com/borgbackup/borg/releases).

### Prevalence

- GitHub code search: https://docs.github.com/en/search-github/searching-on-github/searching-code (index limits); queries were run through `gh api search/code` on 2026-10-25.
- JetBrains:
  - https://lp.jetbrains.com/python-developers-survey-2024/;
  - https://lp.jetbrains.com/python-developers-survey-2023/.
- Stack Overflow:
  - https://stackoverflow.com/questions/404744;
  - https://stackoverflow.com/questions/7674790;
  - https://stackoverflow.com/questions/12059509;
  - https://stackoverflow.com/questions/51455765.
- pypistats:
  - https://pypistats.org/packages/pyinstaller;
  - https://pypistats.org/packages/pipreqs;
  - https://pypistats.org/packages/nagiosplugin.
- https://tweag.io/blog/2024-09-24-fawltydeps-pypi-analysis/
- https://safedep.io/pep-723-inline-metadata-security

### Platforms

- Lifecycles:
  - https://endoflife.date/rhel;
  - https://endoflife.date/rocky-linux;
  - https://endoflife.date/oracle-linux;
  - https://endoflife.date/amazon-linux;
  - https://endoflife.date/ubuntu;
  - https://endoflife.date/debian;
  - https://endoflife.date/opensuse;
  - https://www.suse.com/lifecycle/.
- Vendor Python docs:
  - https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/installing_and_using_dynamic_programming_languages/assembly_introduction-to-python_installing-and-using-dynamic-programming-languages;
  - https://developers.redhat.com/blog/2019/05/07/what-no-python-in-red-hat-enterprise-linux-8;
  - https://docs.aws.amazon.com/linux/al2023/ug/python.html;
  - https://docs.aws.amazon.com/linux/al2/ug/python.html;
  - https://aws.amazon.com/amazon-linux-2/faqs/.
- Package versions:
  - https://launchpad.net/ubuntu/+source/python3-defaults;
  - https://packages.debian.org/search?keywords=python3&exact=1.
- Fleet proxies:
  - EPEL countme, https://data-analysis.fedoraproject.org/csv-reports/countme/totals-countme.db.gz;
  - https://popcon.debian.org/;
  - mayeut manylinux-timeline (consumer-data.json);
  - https://www.theregister.com/2024/06/06/lansweeper_centos/;
  - https://fossforce.com/2025/04/survey-says-more-organizations-are-using-dead-centos-7-than-rhel/.
- Raspberry Pi:
  - https://www.raspberrypi.com/documentation/computers/config_txt.html;
  - https://www.raspberrypi.com/news/trixie-the-new-version-of-raspberry-pi-os/.

### Services and security

- https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html (HOME only with `User=`).
- STIG:
  - https://www.stigviewer.com/stigs/red_hat_enterprise_linux_9/2026-05-20/finding/V-257867 (noexec /tmp);
  - https://www.stigviewer.com/stigs/red_hat_enterprise_linux_9_v2/2025-05-14/finding/V-257864 (/dev/shm).
- sudo: plugins/sudoers/match_command.c on main, https://github.com/sudo-project/sudo. A command matches by basename, canonical directory and dev/ino.
- https://github.com/NagiosEnterprises/nagioscore/blob/master/base/checks.c (exit codes above 3 are CRITICAL).