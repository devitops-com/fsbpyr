# Test selection: a plan

Status: proposed, 2026-10-31. Nothing here is built, and none of it goes into 0.29.0. The costs were
measured for this plan, on `full-suite.yml` run 41227289551 and the probes in the test repository.
The savings are a replay: those costs applied to what each of the last 80 merges into main changed
(2026-10-20 to 2026-10-31). They are estimates until phase 5 measures the real thing. What is
assumed is marked **[A]**.

## 1. Summary

The owner asked for what `make` gives: when a target's dependencies did not change, do not build
it again. The measurements changed what that should mean here.

- **Selecting single tests does not pay.** 71 of src/fsbpyr's 78 modules are on the path of any
  build, so nearly every change reaches nearly every end-to-end test. A map from test files to what
  they import saves a quarter of `suite` at best, and for the median push nothing, and a map that
  is wrong misses bugs in silence.
- **The waste is work repeated, not tests selected badly.** The same fuzz run happens eight times
  on the same C code, each job builds the same runtime again, and a version bump makes everything
  look changed.
- **`make` at the level of jobs, not of tests, does pay.** Three measures together save 27% of the
  runner minutes. In the median push the longest job goes from 21.5 minutes to 16, and from 31 to
  16 on a push that fuzzes for ten minutes. The 30 pushes of 75 that change the runtime's inputs
  stay at 21. Three of 75 pushes would run nothing at all.

| Measure | Saved | Risk |
|---|---|---|
| A. Fuzz in a job of its own, once per target, once per launcher | 8% | low |
| B. Keep the built runtime between runs of the tests | 7% | low |
| C. Records of passed jobs, with the version ignored by the tests | 14% | medium: inputs must be declared |
| All three (they overlap) | **27%** | |

What remains is real work: 8 entries of `suite` and `corpus` on code that did change. Doing less of
that is a decision about coverage, not an optimization (§9, D4).

## 2. What costs what

One whole run on GitHub is 410 runner minutes:

| Job | Entries | Runner minutes | Longest |
|---|---|---|---|
| `suite` | 8 | 248 | 35 |
| `corpus` | 8 | 107 | 18 |
| `build` | 8 | 20 | 3 |
| `reproduce` | 8 | 19 | 3 |
| `portability` | 8 | 15 | 2 |
| `compare`, `vm-smoke` | 9 | 2 | 1 |

Inside one `suite` job (x86-64, Python 3.14, run 41227289551), whose step `The whole suite` took
24 minutes:

| Part | Minutes |
|---|---|
| Fetching downloads and building the runtimes, before the first test | 4.5 |
| Up to 97% of the tests | 11 |
| The last 3%, nearly all of it one fuzz test of exactly 600 s | 12.4 |

`make bootstrap` and "The other version's runtime" add 3 minutes before that step. The same tests
take 3 minutes on the 24-core development machine.

What pushes changed, in the 80 merges: 5 documentation only, which `paths-ignore` leaves out
already. Of the 75 that ran the suite, 9 had a version bump in them, and 29 ran the long fuzz.

## 3. What was ruled out

- **Selecting test files.** Replayed on the 75 pushes with a map built from every test file's
  imports, plus what a build goes through for `tests/e2e`: 75% of `suite`'s time stays, and the
  median push runs 100% of it. 56 pushes run over three quarters of it.
- **`pytest-testmon`.** It follows Python coverage. Most of this suite's cost runs in built
  executables and C, where coverage of the tests' process sees nothing.
- **Bazel, Pants.** They need every test's inputs declared, for the same result: everything
  depends on nearly everything.
- **`paths` on the trigger.** It takes or leaves a whole workflow, as `paths-ignore` does now.
- **Comparing with the commit before** (`dorny/paths-filter` and the like). A commit that breaks
  something, followed by an unrelated one, skips the job and shows green over broken code. A hash
  of the content, recorded only on success, cannot do that.
- **`actions/cache` for the records.** Measured in the test repository: a key saved on a branch is
  a miss on main. Caches are scoped to the branch, and main's are visible to branches, not the
  other way. It also says nothing of who wrote a key. It stays the right tool for the runtime (§5).
- **GitLab.** It could run this, and dynamic child pipelines are neat, but half the matrix needs
  arm64 hardware and the release gate reads GitHub's runs.
- **Skipping a tree that already passed.** It sounded like the clearest case. In the last 40 runs
  no tree ran twice. Records per job cover it anyway, when it happens.

## 4. Measure A: fuzz once per target, once per launcher

`suite` fuzzes in each of its 8 entries, for 30 s, or 600 s when `src/fsbpyr/launcher` or
`tests/fuzz` differ from the last tag. So from the first launcher change until the next release,
every push spends 80 runner minutes fuzzing the same C code, and adds ten minutes to every job.

- `test_fuzz.py` leaves `suite` for a job `fuzz`, with one entry per target, on the default Python.
- The weekly run keeps fuzzing all 8 entries for 600 s (D3).
- With records (§6), its inputs are the launcher, `tests/fuzz`, `tests/probes` and `test_fuzz.py`:
  each state of the launcher is fuzzed once, instead of on every push until a release. The rule
  about the last tag goes.
- [A] The harnesses find nothing per Python that they would not find per target. They exercise
  launcher C, and the weekly run stays on all 8 as the check on this.

Before any of the rest, this also shortens every `suite` job by ten minutes on the pushes that
fuzz long.

## 5. Measure B: keep the runtime

Each entry builds its runtimes from pinned inputs, for about 5 minutes of its time.

- A runtime is a function of the launcher's sources, the pins, the toolchain and the Python. It
  goes into `actions/cache` for the jobs that run tests, under the hash of exactly those inputs,
  per Python and target.
- Only runs of main save, so that branches and main share them. A run on a branch that changes the
  launcher builds its own, as now.
- **The release build keeps taking no cache**, as `build.yml:143` says it must: this is for tests
  only. A test that needs a clean build, such as `test_the_launcher_links_reproducibly`, keeps
  building its own.
- [A] The runtime tests read it from where the cache restores it without change. The suite has an
  own cache directory for downloads (`FSBPYR_TEST_DOWNLOADS`), and this is the same idea.

## 6. Measure C: records of passed jobs

### 6.1 The key

Every entry of a job has a key: the SHA-256 of its name, Python and target, and of
`git ls-tree -r <commit> -- <inputs>`, the mode, path and blob of every input. That needs no
checkout and works for any commit, which §6.4 relies on.

The inputs are declared in `tools/ci/inputs.toml`, coarse on purpose:

| Job | Inputs, beyond the common ones |
|---|---|
| common | `src/`, `pyproject.toml`, `uv.lock`, `.python-version`, `Makefile`, `tools/bootstrap.sh`, `tools/test-setup.sh`, `tools/release/{llvm23,uv.version,install-uv.sh}`, `tools/ci/`, `.github/workflows/full-suite.yml` |
| `fuzz` | only: `src/fsbpyr/launcher/`, `tests/fuzz/`, `tests/probes/`, `tests/e2e/test_fuzz.py`, `tools/ci/`, the workflow |
| `suite` | `tests/`, `tools/` |
| `corpus` | `tests/support/`, `tests/fixtures/`, `tests/corpus/`, the conftests, `tests/e2e/test_corpus.py` |
| `build` | `.github/workflows/build.yml`, `tools/release/sbom.py` |
| `portability` | as `corpus`, with `tests/e2e/test_portability.py` and `tools/portability-apps.sh` in place of `tests/e2e/test_corpus.py` |
| `vm-smoke` | `tools/vm-smoke.py`, `tools/vm-pins.toml` |

- **The version does not count, except for `build`.** `pyproject.toml`, `uv.lock` and
  `src/fsbpyr/__init__.py` go into the tests' keys with fsbpyr's own version line removed. Otherwise
  `make release-prep` makes everything stale when no code changed, and a test that reads the
  version sees a stale value at worst. `build` keeps the whole files: the version is in the bytes
  it makes, and release.yml leaves `reproduce` and `compare` out (`checks: false`) because the full
  suite ran them "on this very commit". That has to stay true of a release commit.
- **Every tracked file is accounted for.** A test requires each file `git ls-files` lists to be an
  input of some job, or to be in the file's `ignored` list (documentation, `spikes/`). A new
  directory cannot fall through in silence.
- **The selection is an input of itself.** `tools/ci/` is in every key, so a changed table or a
  changed key script runs everything once, instead of trusting records made under the old rules.

### 6.2 The record

The last step of an entry, which runs only if every step before it passed, uploads an artifact of
one small file, named `passed-<entry>-<key>`, with `retention-days: 8`.

Measured on GitHub: an artifact uploaded on a branch is found from main, by exact name, in 0.3 to
0.6 s, with its run, commit and branch. So a record made anywhere counts, as `make` counts a file
however it was made.

### 6.3 The plan

A new first job `plan`, with `contents: read` and `actions: read`, computes every entry's key, looks
each record up, and outputs one matrix per job with the entries that are stale. The other jobs take
those as dynamic matrices.

Measured on GitHub: stale entries alone exist as jobs. With every matrix empty, the run took 12 s,
and its conclusion was success, which is what `tools/release/green-run.sh` reads, so `make release`
needs no change. A job that `needs` a skipped job is skipped too, unless it says
`if: ${{ !cancelled() && ... }}`.

`plan` writes the run's summary: for each entry, the run that tested it. The release notes' "Full
suite" link lands there.

### 6.4 A record is checked before it counts

Whoever can write to the repository can upload an artifact of any name. So a record counts only if:
1. it has not expired, and comes from this repository;
2. its run's `path` is `.github/workflows/full-suite.yml`;
3. in the attempt that uploaded it, the job of that entry concluded success;
4. **the key computed at the run's `head_sha` equals the key in the name.**

The last one is what makes a forgery pointless. The workflow is an input, so a changed workflow
gives another key at its own commit, and it cannot claim the key of an unchanged one. A commit with
the same key has the same workflow and the same inputs, so its job really ran them. This rests on
GitHub reporting a run's commit truly: GitHub the platform being compromised is out of scope here,
as it is for signing.

None of this touches a release's safety: that rests on the signature, and tests are no gate
against a stolen account.

**A lookup that fails makes the entry stale.** Here that is the safe direction: it runs the job.

### 6.5 Jobs that feed jobs

`portability` and `vm-smoke` take the executable that `suite` uploads, so a skipped `suite` leaves
them nothing. They move to the `built-*` artifacts of `build`, which `compare` already shows to be
the same bytes. `build` then stays stale whenever a job that consumes it is, as `make` builds a
prerequisite only if something needs it and it is missing.

`build.yml` takes its matrix as an input for this, with release.yml's call leaving it out and
getting all of it, as it must.

### 6.6 What a key cannot see

The runner's image, apt's packages, `archlinux:latest` and the like are not inputs. So:
- **The weekly run ignores all records and writes new ones**, and so does a dispatch with
  `full: true`.
- Records last 8 days, the week between two weekly runs and a day, so no pass is older than that.

A flaky test that passed once stays passed until then. That is a cost of the method.

### 6.7 A release

A release commit changes the version and the changelog. `build` runs on it with `reproduce` and
`compare`, since its key sees the version (§6.1), so what release.yml leaves out stays checked on
that very commit. The test jobs reuse the records of the commit before it, if nothing else changed
(D1). `release.yml` builds from the tag as it does now, with no cache and no record, and
`make release-check` tests what was published.

## 7. Failure modes

| What goes wrong | What happens | What limits it |
|---|---|---|
| An input is missing from the table | A job skips that should run | Coarse inputs, the test of §6.1, shadow mode (§8), the weekly run |
| A record is forged | It does not count | §6.4 |
| GitHub's API fails in `plan` | The entry runs | §6.4 |
| An artifact expires | The entry runs | Nothing is needed |
| The world changes under an unchanged key | Found by the next weekly run | §6.6 |
| A flaky test passed once | It is not run again until then | §6.6 |
| `plan` itself has a bug | Anything | Tests below, shadow mode, and `full: true` as a way out |

## 8. How to get there

Each phase can stop there and keep what it gained. Each is a pull request with its own full run.

1. **Fuzz and the runtime (A, B).** No records yet.
   Done when: a full run takes 10 minutes less per `suite` job on a long fuzz, and the same tests
   pass; a planted crash in the launcher is still found by the `fuzz` job.
2. **Shadow mode.** `tools/ci/inputs.toml`, the keys, the records and `plan`, which only reports
   what it would skip, while everything runs.
   Done when: 20 pushes to main have passed with no entry that `plan` would have skipped and that
   then failed. One such entry means a missing input: add it, and start counting again.
3. **Turn it on for `fuzz`, `corpus`, `build`, `portability` and `vm-smoke`.** `suite` stays
   unconditional, as the check on the rest.
   Done when: two weekly runs show nothing the skipped jobs missed.
4. **Turn it on for `suite`.**
5. **Measure again,** on the same replay, and decide whether anything finer is worth it.

The tools get the treatment the release scripts got:
- unit tests of the keys: stable, changed by each input, unchanged by a version bump for every job
  but `build`, and changed by it for `build`;
- the test that every tracked file is accounted for;
- `plan` against a fake `gh`: each way a record can be forged or missing;
- mutation testing of `plan`, since a check that fails open is the bug that matters here;
- a rehearsal in the test repository, where the probes of this plan ran.

## 9. Decisions for the owner

- **D1. May a release commit reuse the records of the tests?** Proposed: yes, and never those of
  `build` (§6.7). Only the version differs, and `make release-check` tests the result. Otherwise
  every release is a full run for a change of one line.
- **D2. How old may a pass be?** Proposed: 8 days, one weekly run and a day.
- **D3. Fuzz on one Python per target on a push?** Proposed: yes, with all 8 in the weekly run.
- **D4. Should `corpus` run all 8 entries on every push?** Not in this plan, which changes no
  coverage. At 107 minutes it is the largest thing left, and one entry per target on a push, with
  all 8 weekly, would save about 80 minutes a run, at the price of finding a corpus failure on
  another Python up to a week later.
