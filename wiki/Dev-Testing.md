# Testing

fsbpyr's test suite is in [`tests/`](../tests). The modules it tests have their own pages:
[Dev-Build-Modules](Dev-Build-Modules.md), [Dev-Payload-Modules](Dev-Payload-Modules.md),
[Dev-Runtime-Modules](Dev-Runtime-Modules.md), [Dev-Boot-Modules](Dev-Boot-Modules.md) and
[Dev-Launcher](Dev-Launcher.md).

## Layout

| Directory | What it holds |
|---|---|
| [`tests/unit`](../tests/unit) | fsbpyr's modules, mostly in the test process; at the top level the CLI and its documentation, inspection, bytecode, the version rule, dispatch, self update, signing, targets, git state, text helpers, Poetry 1, tool binaries, the SBOM generator, `tools/release/el8-stub.py`, `tools/vm-smoke.py`, the wiki, the test modes, the no-skip rule and some support code. |
| [`tests/unit/boot`](../tests/unit/boot) | The boot code (`_fsbpyr_boot`, `_fsbpyr_rt`) in-process, mostly against a pure-Python stand-in for `_fsbpyr`. |
| [`tests/unit/builder`](../tests/unit/builder) | The build pipeline, from project discovery and other tools' locks to staging, classification, native closures, manifest, compiler pool and orchestrator. |
| [`tests/unit/payload`](../tests/unit/payload) | Payload writer and reader. |
| [`tests/unit/runtime`](../tests/unit/runtime) | The runtime build: PBS fetch, registry, sysroots, toolchain and link plan, stdlib selection, licence notices, a release's glibc 2.28 stub, the tool's cache collector. |
| [`tests/integration`](../tests/integration) | Real tools or data but no built executable: the real stdlib, a C compiler, the PBS distribution, the installer under three shells, git repositories, release scripts and the EL8 workflow against a fake `gh`. |
| [`tests/e2e`](../tests/e2e) | Real executables built from a real runtime and run as subprocesses; C probes and the fuzzer; the corpus and the portability matrix. |
| [`tests/probes`](../tests/probes) | Small C programs linked with launcher sources, each driving one part of the launcher for one end-to-end test. |
| [`tests/fuzz`](../tests/fuzz) | `payload_fuzz.c` and `sig_fuzz.c`, the libFuzzer harnesses for the launcher's payload parsing and its readers of `.pub` and `.key` files (`sig.c`). |
| [`tests/corpus`](../tests/corpus) | The real-world corpus: one small application per risk class, compared with its venv. |
| [`tests/fixtures/projects`](../tests/fixtures/projects) | Projects the tests build ([Fixture projects](#fixture-projects)). |
| [`tests/support`](../tests/support) | Shared code ([Support modules](#support-modules)). |

`tests`, `tests/unit` and its subdirectories, `tests/integration`, `tests/e2e` and
`tests/support` are packages, so tests import shared code relatively
(`from ..support import corpus`). [`tests/conftest.py`](../tests/conftest.py) keeps pytest out of
the corpus apps' own tests (`collect_ignore_glob = ["corpus/*"]`).

![The test layers and the make targets that run them: make test-ci runs tests/unit, tests/integration and fifteen end-to-end files; make test and make test-container run unit, integration, e2e, probes and fuzzer, make test also the portability tests; make test-corpus the corpus; make test-portability the portability tests; make test-el8-stub sixteen end-to-end files and test_test_modes.py in the EL8 test mode, then test_el8.py; make test-el8-portability the portability tests in that mode; make test-vm tools/vm-smoke.py, aarch64 apps in a QEMU VM with 64 KiB pages. Unit tests run in-process, the boot code against the _fsbpyr stand-in; e2e tests run executables with the release, ASan/UBSan and glibc 2.28 launchers; the corpus compares real apps with their venv.](images/testing-layers.svg)

## How the layers work

**Unit tests** run fsbpyr's Python modules in the test process. Most that need a whole
executable build one with `build_executable` and `FAKE_RUNTIME`, a runtime record from
[`tests/unit/boot/conftest.py`](../tests/unit/boot/conftest.py) whose stub is a few bytes
(`b"\x7fELF-fake"`): a valid payload the Python reader reads, behind a stub that cannot run. The
boot code runs against [`StandIn`](../tests/support/standin.py), a pure-Python `_fsbpyr` over the
payload reader, whose cache sync, [`tests/support/cache.py`](../tests/support/cache.py), mirrors
`launcher/cache.c` step by step. Payload tests use `make_spec` from
[`tests/unit/payload/conftest.py`](../tests/unit/payload/conftest.py), a small spec with every
table and reference type.

**The C launcher and its Python mirror are held to one contract.**
[`tests/support/contract.py`](../tests/support/contract.py), a battery of `_fsbpyr` calls with
damaged, missing and refused inputs, runs against the C module inside a built executable and
against the stand-in, and `tests/e2e/test_imports.py` requires equal JSON results. One table of
format edge cases ([`tests/support/edges.py`](../tests/support/edges.py)) likewise runs against
the Python reader (`tests/unit/payload/test_edges.py`), the launcher (`tests/e2e/test_edges.py`,
release and sanitized) and its parsing code under libFuzzer (`tests/e2e/test_fuzz.py`).

**Integration tests** need something real but no executable: the standard library, `cc` for a
probe of `launcher/format.h`, the pinned PBS distributions (from `FSBPYR_TEST_DOWNLOADS`, else
downloaded), `dash`, `bash` and `busybox sh` for the installer, `openssl` for its server's
certificate, `git` for the release scripts. Servers are local: HTTPS on 127.0.0.1 for the
installer, a fake `gh` that logs its calls for the release and watch scripts and the EL8
workflow's draft job. Nothing reaches fsbpyr's GitHub repository.

**End-to-end tests** build real executables from a real runtime and run them. The `runtime`
fixture in [`tests/e2e/conftest.py`](../tests/e2e/conftest.py) gives the runtime for the tests'
Python (`TARGET_PYTHON`) once per session, and once more for the sanitized launcher: the session
cache's, else the one kept in `FSBPYR_TEST_DOWNLOADS` (built there from the kept downloads if
missing), else one built from downloads. Building needs clang and lld 23 or newer. Tests marked
`sanitized` run with the release launcher and its ASan/UBSan build. `el8_runtime` gives the same
Python's runtime linked for glibc 2.28 in the same way (release launcher only), whose stub is the
one `fsbpyr build --glibc 2.28` puts into apps; in the EL8 test mode `runtime` is that runtime
too.

`launcher_build` gives the runtime build's clang, the launcher's sources and the runtime's
`libzstd.a`, `liblzma.a` and `libcrypto.a`; `LauncherBuild.compile` links a probe from
[`tests/probes`](../tests/probes) or the fuzz harness with the launcher sources it names, as
`clang -std=c11 -D_GNU_SOURCE -g -O1` plus the test's flags. `app_content` turns
`{path: source}` pure-Python files into an app, which `build_app` builds. Most files that build
projects, directories of scripts or single scripts call `fsbpyr build` in-process
(`fsbpyr.cli.main`) on a copy in `tmp_path` and run the result through `contain.run`
([Containment](#containment-of-app-runs)).

**The corpus** builds real applications with their dependencies from PyPI and compares each
command's output with the app's venv ([The corpus](#the-corpus)).

## Configuration and conftests

`[tool.pytest.ini_options]` in [`pyproject.toml`](../pyproject.toml):

- `testpaths = ["tests"]`; `addopts = ["--strict-markers", "--strict-config", "-ra"]`: unknown
  markers and keys are errors, and the summary lists every test that did not pass.
- `tmp_path_retention_policy = "failed"`: a run builds gigabytes of executables, so only failed
  tests' temporary directories are kept.
- `empty_parameter_set_mark = "fail_at_collect"`: an empty parametrization is an error, not a
  hidden skip.
- Six markers:
  - `slow`: every end-to-end file, the PBS integration tests and the two bytecode-determinism
    tests. No `make` target selects by it; `-m "not slow"` leaves them out.
  - `sanitized`: also run with the ASan/UBSan launcher; the end-to-end conftest's
    `pytest_generate_tests` parametrizes `runtime` with `release` and `sanitized` (in the EL8
    test mode `release` only, the sanitized launcher being the default sysroot's). Used by
    `test_cache_root.py`, `test_edges.py`, `test_imports.py`, `test_runtime_app.py` and
    `test_signatures.py`.
  - `version_rule`: the Python version rule runs as it is: `test_pyversion.py`, the unit
    and end-to-end `test_dispatch.py`, two tests of `test_pipenv.py` and two of `test_cli.py`.
  - `requires_tools`: fails before running without these tools on `PATH`
    (`requires.tools(...)`).
  - `needs_wheel`: deselected where `no-wheels.txt` lists no wheel of the package
    (`wheels.needs(package)`).
  - `podman`: set by `tests/conftest.py` on every test whose `requires_tools` names podman, so
    `-m "not podman"` leaves those out.

[`tests/conftest.py`](../tests/conftest.py) applies to every test:

- `_no_collection` (session, autouse) sets `FSBPYR_GC=0`, so that no cache collection acts on the
  tests' caches unless a test asks for one.
- `_no_signing_setup` (session, autouse) unsets `FSBPYR_SIGNING_KEY` and
  `FSBPYR_SIGNING_PASSPHRASE` and points `XDG_CONFIG_HOME` at a temporary directory, so that no
  developer's key signs the tests' builds and no test reads or writes their trust store.
- `_build_for_own_python` (session, autouse) wraps `pyversion.resolve` so a build is for the
  tests' Python, as `--python` would say (the fixtures' `requires-python = ">=3.12"` would pick
  3.12); `_version_rule` undoes it for `version_rule` tests.
- `_build_for_baseline` (session, autouse), in the EL8 test mode only, wraps
  `config.load_config` so a build is `--glibc 2.28` unless it passes a glibc itself.
- Hypothesis has no deadline (`deadline=None`); with `CI` set, a profile `ci`
  (`derandomize=True`) runs the same examples every time.
- `pytest_configure` refuses an `FSBPYR_TEST_GLIBC` that is no glibc baseline. Under pytest-xdist
  with `FSBPYR_TEST_DOWNLOADS` set, the controller downloads the pinned files (both baselines'
  sysroots) and builds the runtimes there before the workers start: release, sanitized and
  glibc 2.28, or in the EL8 test mode glibc 2.28 only. Without a compiler it stops with
  `missing tool: ...`.
- `pytest_runtest_setup` fails a `requires_tools` test before its fixtures run when a tool is
  missing.
- `pytest_collection_modifyitems` marks the tests that require podman `podman` and deselects
  `needs_wheel` tests without a wheel for their Python.
- `pytest_runtest_logreport` records every skip and xfail; `pytest_sessionfinish` prints them and
  fails the run ([A skip fails the run](#a-skip-fails-the-run)).
- At the end, `pytest_sessionfinish` removes from uv's cache the builds of local projects made
  during the run (path and editable sources, wheels installed by URL) and the unpacked wheels
  they link to, since the tests build from new temporary copies every time; what uv downloaded
  from an index stays.

[`tests/e2e/conftest.py`](../tests/e2e/conftest.py) adds `_private_cache` (session, autouse),
which points `FSBPYR_CACHE_DIR` at a temporary directory so executables never write the user's
cache; `runtime`, `el8_runtime`, `launcher_build`, `release_keys`, `app_content` and
`build_app`; and the corpus results (`pytest_runtest_logreport`, also in pytest-xdist's
controller), printed by `pytest_terminal_summary` with a `runtime downloads` section saying
`seeded` or `fetched` for each pinned file.

[`tests/unit/boot/conftest.py`](../tests/unit/boot/conftest.py) defines `FAKE_RUNTIME`,
`BOOT_DIR` and the `boot` fixture: `FSBPYR_CACHE_DIR` below `tmp_path`, a `StandIn` for the
module's `executable` as `sys.modules["_fsbpyr"]`, `src/fsbpyr/boot` first on `sys.path`,
`_fsbpyr_boot.py` loaded from the source tree, and the `_fsbpyr_rt` modules removed afterwards.
[`tests/unit/payload/conftest.py`](../tests/unit/payload/conftest.py) defines `make_spec` and the
`spec` fixture.

## Environment variables

| Variable | Read by | Effect |
|---|---|---|
| `FSBPYR_TEST_DOWNLOADS` | `tests/conftest.py`, `tests/e2e/conftest.py`, `tests/e2e/test_dispatch.py`, `tests/e2e/test_baseline.py`, `tests/support/downloads.py`, `tools/vm-smoke.py` | Keeps the runtime's pinned downloads and the runtimes built from them between sessions ([`downloads.py`](../tests/support/downloads.py)), and the [page-size smoke test](#the-page-size-smoke-test)'s kernels and root file systems. The `make` targets default it to `$(TESTTMP)/fsbpyr-test-downloads`. |
| `FSBPYR_CORPUS_APPS` | `tests/support/corpus.py` | Comma-separated corpus apps for `test_corpus.py` (an unknown name is an error); unset or empty: all. |
| `FSBPYR_LLVM_COV_DIR` | `tests/e2e/conftest.py` | `LauncherBuild.compile` instruments its programs for llvm-cov (`-fprofile-instr-generate -fcoverage-mapping`) and copies them there; `LLVM_PROFILE_FILE` says where profiles go. |
| `FSBPYR_FUZZ_SECONDS` | `tests/e2e/test_fuzz.py` | How long the payload harness's fuzzing run explores; 30 seconds when unset. |
| `FSBPYR_FUZZ_CORPUS` | `tests/e2e/test_fuzz.py` | Keeps the inputs a fuzzing run finds for later runs; a temporary directory when unset. |
| `FSBPYR_LARGE_SIZE` | `tests/e2e/test_large.py` | Size of the large file; 5 GiB when unset (the docstring names `1099511627776`, 1 TB, for a run on demand). |
| `FSBPYR_PORTABILITY_APPS` | `tests/e2e/test_portability.py` | The four apps already built (by `tools/portability-apps.sh` in `make test-portability`), used as they are, with no runtime. |
| `FSBPYR_TEST_GLIBC` | `tests/conftest.py`, `tests/support/baseline.py` | `2.28` selects [the EL8 test mode](#the-el8-test-mode); unset or `2.34` the default; anything else is refused before any test runs. |
| `CI` | `tests/conftest.py` | Loads the derandomized Hypothesis profile. |

The tests set `FSBPYR_CACHE_DIR` (a private cache for almost every executable they start),
`FSBPYR_GC`, `FSBPYR_SELFTEST`, `FSBPYR_TRACE`, `FSBPYR_MAX_INSTANCES`, `FSBPYR_INSTALL_LINKS`,
`PYTHONUNBUFFERED`, `JOURNAL_STREAM` and others for the code they run, and
`FSBPYR_RELEASE_DOWNLOADS` to point downloads of release assets at a `FakeReleases` server.
`contain.run` tags its processes with `FSBPYR_TEST_CONTAINED=<token>`. The C probes read
`PROBE_*` switches (`PROBE_FREE`, `PROBE_RELEASE`, `PROBE_OUT`, `PROBE_SESSION`,
`PROBE_OLD_GLIBC`, `PROBE_NO_LINK`, `PROBE_DECOY`, `PROBE_EUID`, `PROBE_VERIFY_RESULT`,
`PROBE_NO_SPACE`, `PROBE_LONGER`),
described in each probe's header comment.

`make` variables ([`Makefile`](../Makefile)): `JOBS` (pytest-xdist processes, default 8),
`TESTTMP` (where each run's temporary directory goes, default `~/tmp`, since `/tmp` is often a
`nosuid` tmpfs where a set-gid start cannot be tested), `TEST_TASKS` and `TEST_MEMORY` (cgroup
limits, [Containment](#containment-of-app-runs)), `PYTHON` (another Python's `.venv-3.X`, for
every test target but `test-vm`), `FSBPYR_LLVM23=0` (leave `PATH` alone instead of putting the
pinned LLVM 23 first) and `VM_ARGS` (`tools/vm-smoke.py`'s arguments). `PYTEST_ADDOPTS` passes
pytest options through, such as `-k <app>`.

## When the machine lacks something

No test skips. A test whose tool is missing, or whose environment is not as it needs, fails with
one line that names what to install or change and points at `CONTRIBUTING.md`
([`tests/support/requires.py`](../tests/support/requires.py)):

```
missing tool: strace (Debian package: strace): traces the system calls of a start; see CONTRIBUTING.md, Running the tests
unmet environment: <condition>: <change>; see CONTRIBUTING.md, Running the tests
```

A test asks for a tool with the `requires_tools` marker (`requires.tools(...)`), checked before
its fixtures build anything, or with `requires.tool(name)` where only a fixture or a branch needs
it; the first of the tool's commands on `PATH` is used. Without clang and lld 23 or newer the
`runtime` fixture fails with `missing tool:` and the `ToolchainError`'s message. The `make`
targets put LLVM 23 (fetched once by `tools/bootstrap.sh --llvm23`) first on `PATH`; pytest run
directly needs a clang 23 there. The tools, as `TOOLS` in `requires.py` lists them:

| Tool | Debian package | What needs it |
|---|---|---|
| `uv` | none: the uv that `tools/release/uv.version` names | runs the tests and builds their fixture projects |
| `git` | git | builds record the commit; tests read projects from repositories |
| `make` | make | a test runs the Makefile |
| `cc` (or `gcc`) | gcc | compiles the tests' C programs |
| `clang` (and lld) 23 | none: `make` puts fsbpyr's LLVM 23 first on `PATH` | links the runtime, the launcher and its test programs |
| `llvm-objdump` (`llvm-objdump-22` first) | llvm-22 | reads executables' sections and symbols |
| `readelf`, `objdump` | binutils | read and disassemble the stub |
| `unshare`, `prlimit` | util-linux | run apps in namespaces and as PID 1; cap contained runs' tasks |
| `podman` | podman, uidmap, passt | runs the portability images; maps a second range of user ids |
| `strace` | strace | traces the system calls of a start |
| `groff` | groff-base | renders the man page |
| `zstd`, `tar` | zstd, tar | unpack the LLVM tarball `bootstrap.sh` fetches; pack the one its tests make |
| `xz`, `curl`, `wget`, `openssl` | xz-utils, curl, wget, openssl | `install.sh` and its tests: the release asset, downloads, the TLS certificate; `release-signing.sh`'s tests: ML-DSA-65 (OpenSSL 3.5 or newer) |
| `jq` | jq | `previous-release.sh` reads GitHub's release list |
| `dash`, `bash`, `busybox` | dash, bash, busybox | the shells `install.sh`'s tests use; completion and release-script tests run `bash` or `dash` too |
| `ls` | coreutils | a host binary whose glibc requirement a test reads |

An environment condition is a function of `requires.py` that the test calls:

| Function | Fails unless | Called by |
|---|---|---|
| `non_root` | the tests run as a normal user | `e2e/test_cache_root.py`, `unit/boot/test_way.py` |
| `user_namespaces` | `unshare -r true` works | `e2e/test_cache_root.py`, `unit/boot/test_way.py` |
| `rootless_podman` | `podman unshare true` works (ranges in `/etc/subuid` and `/etc/subgid`) | `e2e/test_cache_root.py`, `unit/boot/test_way.py` |
| `supplementary_group` | the user has a group besides its effective one | `e2e/test_runtime_app.py`, `unit/boot/test_way.py` |
| `set_id_bits` | set-id bits apply: no `nosuid` mount, no `no_new_privs` | `e2e/test_runtime_app.py` |
| `long_symlink_targets` | the file system takes a symlink target of 999 bytes | `e2e/test_cache_root.py` |
| `foreign_directory` | a searchable directory of a user who is neither root nor the tester is under `/var/lib`, `/etc`, `/var/spool` or `/home` | `e2e/test_cache_root.py` |
| `root_directory_in_tmp` | a directory in `/tmp` is root's | `e2e/test_cache_root.py`, `unit/boot/test_way.py` |
| `uv_python` | uv has that Python installed (the tests never let it download one) | `e2e/test_dispatch.py` |
| `openssl_with_ml_dsa` | the `openssl` command signs with ML-DSA-65 | `support/signing.py`, for every key or signature it makes |
| `openssl_headers` | OpenSSL's headers are installed | `e2e/test_signatures.py` |
| `glibc_host` | the host's libc is glibc | `unit/builder/test_natives.py` |
| `enough_free_disk` | 10% of the file system is free, else a collection is due hourly | `e2e/test_gc.py` |

`e2e/test_pid1.py` (neither `unshare --user --map-root-user --pid` nor podman with `debian:13`
works), `unit/test_cli_docs.py` (no fsbpyr console script beside the interpreter) and
`unit/boot/test_way.py` (a directory that cannot be given to the group, a `/` that is not root's)
call `requires.unmet` themselves. `tools/test-setup.sh` makes the user, group and directories in
the Containerfile's `test` stage.

`make test-prereqs` (`python -m tests.support.requires`) lists at once all the full suite lacks
on this machine: the missing tools' Debian packages as one `apt-get install` line, uv and
clang/lld 23 (by the toolchain search for 23) on lines of their own, then every unmet condition,
with the Pythons 3.12 to 3.15 installed with uv and the set-id, symlink and free-space checks made
for `TMPDIR`. It exits 1 when it lists anything.

### Cases that do not apply

A case that does not apply to the Python, target or mode is not part of the run:

- A package without a wheel for the Python ([`no-wheels.txt`](../tests/support/no-wheels.txt)):
  parametrized tests do not generate the case (corpus apps through `corpus.runnable`,
  `test_compat.py`'s matrix checks, `test_portability.py`'s `heavy` apps and compat checks);
  others are marked `needs_wheel` and deselected (`test_natives.py`, `test_portability.py`'s two
  `FSBPYR_SELFTEST=natives` tests).
- Without podman, `-m "not podman"` leaves out `test_portability.py`, `test_cache_root.py`'s
  symlink of another user made with `podman unshare` and `test_way.py`'s mapped overflow uid;
  `make test-container` and `make test-el8-stub` use it.
- `test_portability.py`'s `test_el8_apps_run_on_el8` exists only outside the EL8 test mode, and,
  with a prebuilt set of apps, only when the set has `hello-el8`.
- A case that behaves otherwise asserts that behaviour: 3.12's functional `importlib.resources`
  API, which takes one resource name (`test_boot.py`); root, who ignores file modes
  (`test_native.py`, `test_collect.py`, `test_selfupdate.py`, `test_install_links.py`); one
  usable core, with no helper thread, and a file system that cannot link unnamed files, where
  files are renamed into place (`test_restore.py`).

One test fails by design, under user-mode QEMU (aarch64 checks on an x86-64 host):
`/proc/<pid>/exe` names the emulator for every process, so `test_mp.py`'s
`test_children_of_any_other_program_are_refused` fails there (`CONTRIBUTING.md`).

### A skip fails the run

Whatever makes a test skip or xfail, `pytest_runtest_logreport` in
[`tests/conftest.py`](../tests/conftest.py) records it, and `pytest_sessionfinish` prints each
under "skipped tests: skipping is not allowed in this suite (CONTRIBUTING.md)" and sets the exit
status to "tests failed".

`tests/unit/test_no_skips.py` reads every Python file under `tests/` but the corpus with `ast`
and fails on `pytest.skip`, `pytest.xfail`, `pytest.importorskip`, the `skip`, `skipif` and
`xfail` marks, `unittest`'s skip decorators and `expectedFailure`, `skipTest`, or an import of one
of them. `tests/unit/test_requires.py` tests the rest with pytester, on a suite of its own: the
one-line failures, a marked test failing before its fixtures, the `podman` mark and
`-m "not podman"`, `needs_wheel` deselection, a skip or xfail failing the run, an empty parameter
set; it also requires `CONTRIBUTING.md` to name every tool and package, and
`tools/test-setup.sh` and the Containerfile to install every package but podman's and those that
Debian or gcc bring along.

## Builds for glibc 2.28 (EL8)

`fsbpyr build --glibc 2.28`, or `glibc = "2.28"` in `[tool.fsbpyr]`, gives the app a stub linked
against a sysroot from pinned Rocky Linux 8.10 RPMs and chooses wheels for manylinux_2_28. A
runtime's id covers its glibc baseline, and a stub records the id it was linked as. A released
fsbpyr pairs its own stdlib with its release's glibc 2.28 stub, placed beside it, cached, or
downloaded from the companion release `el8-vX.Y.Z`; the stub must carry the runtime id fsbpyr
expects ([Dev-Runtime-Modules](Dev-Runtime-Modules.md), [Dev-Build-Modules](Dev-Build-Modules.md)).

### The EL8 tests

Outside the EL8 test mode these files test EL8 builds beside default ones; end-to-end tests that
need the glibc 2.28 runtime take `el8_runtime`. What each checks is under
[Test files](#test-files).

- Unit: `runtime/test_rpm.py`, `runtime/test_stubs.py`, `test_el8_stub_tool.py`, and the EL8
  cases of `test_targets.py`, `runtime/test_sysroot.py`, `runtime/test_fetch_and_build.py`,
  `runtime/test_registry.py`, `builder/test_project_and_staging.py`,
  `builder/test_system_report.py`, `builder/test_build.py`, `builder/test_loose_directory.py`,
  `test_cli.py`, `test_dispatch.py`, `test_inspect.py`, `test_selfupdate.py`, `test_vcs.py`,
  `test_downloads.py`, `test_portability_images.py` and `test_vm_smoke.py`.
- Integration: `test_el8_workflow.py`, `test_check_release_el8.py`, and the tests of the
  companion release and its tag in `test_release.py`, `test_release_sign.py` and
  `test_make_release.py`.
- End-to-end: `test_el8.py`, `test_el8_use_cases.py`, `test_baseline.py`,
  `test_portability.py`'s `test_el8_apps_run_on_el8`, and `test_selfupdate.py`'s EL8 sums.

### The EL8 test mode

`FSBPYR_TEST_GLIBC=2.28` ([`tests/support/baseline.py`](../tests/support/baseline.py)) runs tests
with the glibc 2.28 stub in place of the default one, the stub being what differs between an EL8
build and a default one. In the mode:

- `runtime` is the glibc 2.28 runtime, and `sanitized` tests run with the release launcher only;
- every `fsbpyr build` of a test is `--glibc 2.28` unless it passes another `--glibc`;
- the portability matrix adds `rockylinux:8` and `almalinux:8`, its apps are glibc 2.28 builds
  (unless `FSBPYR_PORTABILITY_APPS` names prebuilt ones) that must report glibc 2.28 with no
  object needing more, and `test_el8_apps_run_on_el8` is not generated;
- the corpus counts only wheels usable at manylinux_2_28 (`tests/support/locks.py`), and
  `corpus.runnable` leaves out apps whose lock has a package without one;
- under pytest-xdist with `FSBPYR_TEST_DOWNLOADS` set, only the glibc 2.28 runtime is built up
  front.

`make test-el8-stub` runs in the mode, in `JOBS` processes with `-m "not podman"`,
`test_runtime_app.py`, `test_imports.py`, `test_stub_hardening.py`, `test_edges.py`,
`test_cache_root.py`, `test_gc.py`, `test_slots.py`, `test_due.py`, `test_restore.py`,
`test_find_object.py`, `test_streaming.py`, `test_stream_windows.py`, `test_unbuffered.py`,
`test_digests.py`, `test_compat.py` and `test_natives.py` of `tests/e2e` (the launcher, the
`_fsbpyr` contract, and apps with natives from manylinux_2_28 wheels) and
`tests/unit/test_test_modes.py`, then `tests/e2e/test_el8.py` outside the mode, as it compares an
EL8 build with a default one. `make test-el8-portability` runs `test_portability.py` in the mode;
`make test-el8` runs both. `test_el8_use_cases.py` is in neither; `make test` runs it.
[`full-suite.yml`](../.github/workflows/full-suite.yml)'s `el8-tests`, `el8-portability` and
`el8-corpus` jobs run the mode, the last with a few corpus apps (`FSBPYR_CORPUS_APPS`);
[`el8.yml`](../.github/workflows/el8.yml) tests nothing.

## The corpus

[`tests/corpus`](../tests/corpus) holds 65 applications, each a small project that
uses real libraries the way real code does, one per risk class;
[Dev-Compatibility](Dev-Compatibility.md) lists them.

### An app's files

An app is a directory `tests/corpus/<app>/` with:

- `pyproject.toml`: name and console script `corpus-<app>` (the test calls it by the directory's
  name), `requires-python = ">=3.12"`, and a description naming the risk it covers. torch and
  torchvision come from PyTorch's CPU index.
- `uv.lock`, made with `uv lock` in the directory.
- `src/corpus_<package>/`: `__init__.py`, `cli.py` with `main`, and the rest of the app.
- `commands.toml`: what to run.
- `work/` (optional): input files the commands name.

### The rules

[`tests/corpus/README.md`](../tests/corpus/README.md) sets them, in short:

- The same output on every run, in the venv and in the executable: seeded random generators,
  rounded floats, sorted output; no timestamps, process ids, ports, addresses, temporary paths,
  `0x...` reprs or progress bars.
- Every write goes into the run's temporary directory, library caches and configuration pointed
  there before import; the test gives each run an empty home.
- No network but servers on 127.0.0.1, port 0, stopped before exit; no downloads.
- Each command well under a minute on one core.
- Libraries used through their public API, with nothing fsbpyr-specific: finding where fsbpyr
  fails is the point.
- One line per check, with a short label. Warnings are output too; none may depend on the
  environment.

When a run fails or differs: fix the app if it is at fault; add a `[tool.fsbpyr]` setting
(`fs-packages`, `interpreter-reexec`, [User-Settings](User-Settings.md)) with a comment saying
what failed without it if fsbpyr needs one; record a known failure if no setting helps.

### commands.toml

[`tests/support/corpus.py`](../tests/support/corpus.py) reads it with `tomllib`:

| Key | Read by | Meaning |
|---|---|---|
| `expect` | `expected()` | Text one command's stdout must contain, so two runs failing alike do not pass. |
| `[[command]]` `argv` | `commands()` | The arguments, run from the project directory. |
| `[[command]]` `env` | `commands()` | Optional variables added to both runs. |
| `[[command]]` `stdin` | `commands()` | Optional text both runs read on stdin; else no input. |
| `known-failure` | `known_failure()` | One line saying what fails, which the test then expects; `{reason = "...", pythons = ["3.14", ...]}` limits it to those Pythons. |
| `sdists` | `sdists()` | Pure-Python packages published only as sdists, which the app builds (the hydra app lists `antlr4-python3-runtime`). |

`settings()` reads the app's `[tool.fsbpyr]`. `test_every_corpus_app_says_what_its_output_contains`
requires a non-empty `expect` in every app (and at least 8 apps),
`tests/unit/test_corpus_locks.py` checks every lock (below), and `tests/unit/test_cli_docs.py`
requires `docs/compatibility.md`'s corpus table to name exactly the corpus directories.

### How an app is built and run

`test_corpus_app_matches_its_venv` in [`tests/e2e/test_corpus.py`](../tests/e2e/test_corpus.py)
runs for each corpus directory, or each that `FSBPYR_CORPUS_APPS` names (`corpus.selected()`),
whose lock installs nothing but its `sdists` without a usable wheel for this Python and machine
(`corpus.runnable`). For one app it:

1. Reads `known-failure`: when it names this Python, the steps below are expected to fail.
2. Makes a temporary directory with a build cache whose `runtimes` links to the session's, as
   `FSBPYR_CACHE_DIR` for the build, so what the smoke test extracts goes with the rest.
3. Copies the project, makes its venv with `make_venv` (`uv sync --locked --no-dev
   --no-default-groups --no-editable --python X.Y`) and builds it in-process with
   `fsbpyr build <project> -o <tmp>/bin/corpus-<app>`.
4. Runs every command through `run_both` ([Differential testing](#differential-testing)), from
   the project directory, with `FSBPYR_CACHE_DIR` below the app's directory, the command's `env`
   and `stdin`, and an empty home per run. A difference where the executable exits 0 is a false
   success; any other is a failure.
5. Requires a build, no false success, no failure and the `expect` text, and records the result
   whatever the outcome (`corpus.record`, a dict pytest-xdist can send to its controller). With a
   known failure the test passes when anything in steps 3 to 5 fails, and otherwise fails, saying
   to remove the entry. A passing test removes the app's venv, executable and caches.

The same file runs `test_plugins_across_distributions_match_their_venv` on the `plugins` fixture
(entry points across two distributions and a PEP 420 namespace split between them). The session's
`corpus` section gives the number of apps, false successes (target 0) and how many of them are in
known failures, the first-build success rate, and the apps with `[tool.fsbpyr]` settings.

### Wheels, sdists and known failures

[`tests/support/no-wheels.txt`](../tests/support/no-wheels.txt) lists, per Python
(`3.15 <package> ...`), packages with no wheel for that Python or for fsbpyr's manylinux_2_34
baseline. [`tests/unit/test_corpus_locks.py`](../tests/unit/test_corpus_locks.py) reads every
app's `uv.lock` for each supported Python and both machines with
[`locks.py`](../tests/support/locks.py), and fails where an app's packages lack a usable wheel and
`no-wheels.txt` lists none of them: no app is built from an sdist by surprise, and every gap that
keeps an app from running is written down. Packages in the app's `sdists` are no gaps.

A `known-failure` makes the test assert that the app still fails, so the entry goes once fsbpyr
is fixed; there is no xfail. No app has one.

### Containment of app runs

A built app whose library restarts `sys.executable` re-runs the app, and can multiply until the
host goes down ([`contain.py`](../tests/support/contain.py)'s docstring has the case). Two layers
hold such runs:

- `contain.run` runs every command of the corpus and differential tests, and the app runs of
  `test_build.py`, `test_compat.py`, `test_multicall.py`, `test_scripts.py`,
  `test_loose_directory.py`, `test_single_script.py`, `test_tool_binary.py`,
  `test_install_links.py`, `test_file_overlay.py`, `test_pipfile_lock.py`, `test_pid1.py`,
  `test_el8.py`, `test_el8_use_cases.py`, `test_dispatch.py`, `test_selfupdate.py`,
  `test_signing.py` and `test_verify.py`, in a session of its own, its environment tagged with
  a token (`FSBPYR_TEST_CONTAINED`) that every descendant inherits. It polls `/proc` every 0.2
  seconds and kills all tagged processes with SIGKILL, failing the test, when there are more than
  64 (`Runaway`), when the command times out (`subprocess.TimeoutExpired`), or when any outlive
  it by more than 2 seconds (`Leftover`). In case polling falls behind, `prlimit --nproc` caps
  the command at the machine's task count plus 1,024.
- `make test`, `make test-ci`, `make test-corpus`, `make test-el8-stub`,
  `make test-el8-portability` and `make test-vm` run in a systemd user scope where a user manager
  exists: at most `TEST_TASKS` tasks (default 8,192) and `TEST_MEMORY` bytes (default 70% of the
  machine's memory), no swap, `OOMPolicy=continue` (only the process that runs out of memory is
  killed), and what is left is killed when the run ends.

Apps should be tried through the test (`make test-corpus PYTEST_ADDOPTS="-k <app>"`), not by
hand, which has no such protection.

## Differential testing

[`tests/support/differential.py`](../tests/support/differential.py): `make_venv` makes the
project's staging environment with uv (its wheel, not an editable copy, and its locked
dependencies without dev groups, on the runtime's Python). `run_both` runs one command with the
same environment and working directory as the venv's console script under `python -I -X utf8`, as
built apps run, and as the executable, and returns both outcomes (return code, stdout, stderr);
`assert_same` fails unless they are equal.

Only documented differences are normalized: the venv's and executable's own paths (`<venv>`,
`<executable>`), and the directory a module's file is named in (warnings, tracebacks), `<site>`
for the venv's site-packages, the executable's tree and the cache's extracted site alike. With
`home`, each run gets an empty home (`HOME` and the XDG base directories), written `<home>`.
onnxruntime's timestamped host warning is removed. Both runs get two threads per BLAS and OpenMP
pool unless the environment says otherwise, and lose `FSBPYR_TRACE`, `VIRTUAL_ENV` and the run's
`PYTEST_*` variables. A command has 120 seconds, the venv's sync 300. `test_compat.py`,
`test_natives.py` and the corpus use it.

## Determinism checks

Tests that require the same bytes from the same inputs:

- `test_builds_do_not_depend_on_the_build_environment` (`tests/e2e/test_build.py`): `clicky`
  from each lock source, with an active `VIRTUAL_ENV` and `UV_VENV_SEED=1`, and from another
  checkout path with umask 077, other cache directories and one CPU core.
- `test_self_hosting_reaches_a_fixed_point`: fsbpyr built from the venv in the test process
  (stage 1), stage 1 building stage 2 and stage 2 stage 3, with only uv and git on `PATH` and an
  empty cache: all three the same bytes ([Self-hosting](Dev-Self-Hosting.md)).
- `test_build_interpreter_optimization_does_not_change_the_output`: `PYTHONOPTIMIZE=2` for the
  building interpreter.
- `test_two_builds_are_the_same_bytes` (`tests/e2e/test_scripts.py`): the `scripts` fixture,
  whose wheel fsbpyr writes; `test_two_writes_are_the_same_bytes`
  (`tests/unit/builder/test_loose.py`) for that wheel alone.
- `tests/integration/test_bytecode_determinism.py`: canonical marshal output over the real
  stdlib, the same from worker processes of different shapes (clean, compiling in a batch, with
  stdlib modules imported first, each with another `PYTHONHASHSEED`); `tests/unit/test_bytecode.py`
  covers reference counts, interned strings and equal constants.
- `test_the_launcher_links_reproducibly` (`tests/e2e/test_launcher_build.py`): two links.
- `tests/e2e/test_el8.py`: a default build of `hello` made again, and a `--glibc 2.28` build by a
  release fsbpyr with the downloaded stub against one with the cache's glibc 2.28 runtime.
- Unit tests: payload encoding under permuted input (`test_roundtrip.py`), compression whatever
  the thread count (`test_spill.py`, `test_zstd_pieces.py`), the compiler pool against
  in-process compiling (`test_compiler.py`), the manifest from two directories
  (`test_manifest.py`), the link's relative and mapped paths (`test_toolchain_and_link.py`), the
  SBOM (`test_sbom.py`).

## The README's quickstart as a test

The README marks its quickstart with `<!-- quickstart: tests/e2e/test_build.py runs these sh
blocks -->` and `<!-- /quickstart -->`; `quickstart()` in
[`tests/support/readme.py`](../tests/support/readme.py) returns the `sh` blocks between them, in
order (not the `pyproject.toml` shown there).

`test_readme_quickstart_works_on_a_new_account` (`tests/e2e/test_build.py`) runs them with
`/bin/sh -ec` in a copy of the `hello` fixture, as on a new account: an empty `HOME` whose new
`~/.local/bin` is not on `PATH`, and a `PATH` of `/usr/bin`, `/bin` and a directory with uv and a
`curl` stand-in that serves only the installer's URL, with an installer that copies a wrapper for
the fsbpyr under test into `~/.local/bin`. It requires exit 0 and the `hello []` line.
`tests/unit/test_cli_docs.py` checks that the CLI accepts the quickstart's `fsbpyr` command lines
(`test_readme_quickstart_runs_command_lines_fsbpyr_accepts`) and that the `pyproject.toml` it
shows has the `hello` fixture's build system and console script
(`test_readme_quickstart_project_is_the_fixture_it_is_tested_with`).

## Coverage

`[tool.coverage.run]` in `pyproject.toml` measures `src/fsbpyr` with branch coverage, by path,
since the boot code also runs in-process under its embedded names (`_fsbpyr_boot`,
`_fsbpyr_rt`). `[tool.coverage.report]` sets `fail_under = 92.75`, `make test-ci`'s total minus 1,
against the total rounded to two decimals (`precision = 2`), and shows missing lines of files
not fully covered. `make test-ci` runs with `--cov --cov-report=term --cov-report=xml`
(`coverage.xml`), so it fails below `fail_under`. The launcher's C code is covered only through
the probes and the fuzz harness, when `FSBPYR_LLVM_COV_DIR` is set.

## make targets

| Target | What it runs |
|---|---|
| `make check` | `ruff check` and `mypy` (strict, over `src` and `tests` but the fixtures and the corpus). |
| `make check-c` | clang's static analyzer over the launcher's C sources (`tools/check-c.py`); exits 1 on findings, which fails `full-suite.yml`'s run. |
| `make test-ci` | The core: `tests/unit`, `tests/integration`, and fifteen end-to-end files: `test_build.py`, `test_runtime_app.py`, `test_imports.py`, `test_stub_hardening.py`, `test_multicall.py`, `test_scripts.py`, `test_loose_directory.py`, `test_single_script.py`, `test_install_links.py`, `test_pid1.py`, `test_unbuffered.py`, `test_file_overlay.py`, `test_pipfile_lock.py`, `test_signing.py` and `test_selfupdate.py`, in `JOBS` pytest-xdist processes (`--dist loadfile`), with `-k "not (sanitized and not release)"` (the release half of the `sanitized` tests) and coverage. |
| `make test` | Everything but the corpus, in `JOBS` processes (`--dist loadfile`: each file in one process). |
| `make test-prereqs` | `python -m tests.support.requires` ([When the machine lacks something](#when-the-machine-lacks-something)). No pytest run. |
| `make test-container` | Everything but `test_portability.py` and the corpus, with `-m "not podman"`, in the Containerfile's `test` stage (Debian trixie, user `tester`, `--privileged`, Pythons 3.12 to 3.15, a `/var/tmp` tmpfs that allows set-id bits); needs podman. |
| `make test-corpus` | `tests/e2e/test_corpus.py` in `JOBS` processes (`--dist load`); needs the network for wheels. |
| `make test-portability` | Builds the four portability apps with `dist/fsbpyr` (`tools/portability-apps.sh`) and runs `tests/e2e/test_portability.py` on 12 distributions; needs podman, llvm-objdump and a `make build` first. |
| `make test-el8-stub` | In the [EL8 test mode](#the-el8-test-mode), the sixteen end-to-end files that run the stub and `tests/unit/test_test_modes.py` (`--dist loadfile`, `-m "not podman"`); then `tests/e2e/test_el8.py` outside the mode. |
| `make test-el8-portability` | `test_portability.py` in the EL8 test mode, on the 12 distributions and `rockylinux:8`, `almalinux:8`; needs podman. |
| `make test-el8` | Its prerequisites `test-el8-stub` and `test-el8-portability`. |
| `make test-vm` | `tools/vm-smoke.py $(VM_ARGS)` ([The page-size smoke test](#the-page-size-smoke-test)); needs `qemu-system-aarch64`. No pytest run. |

Every test target but `test-container` runs in a fresh temporary directory under `TESTTMP`,
removed at the end, and all but `test-vm` with LLVM 23 first on `PATH`; those that
[Containment](#containment-of-app-runs) lists also in its cgroup. CI's full suite runs the tests
in 4 processes (`pytest -n 4 --dist loadfile`, with the kept downloads);
[CONTRIBUTING.md](../CONTRIBUTING.md#running-the-tests) describes it, the tiers and their
timings.

No two pytest-xdist workers share a virtual environment: each gets a new one from `uv.lock` for
the run, with its own Python, `sys.path`, `PATH` and `VIRTUAL_ENV`, and a worker that finds
anything else stops the run ([`worker_venvs.py`](../tests/support/worker_venvs.py)). Python
writes a module's `.pyc` beside it on first import, so workers on one environment would change
it under each other. The checkout, `src/` and its `__pycache__` directories included, is still
shared.

## The page-size smoke test

`make test-vm` runs [`tools/vm-smoke.py`](../tools/vm-smoke.py), which is not a pytest test.
fsbpyr builds for its host, and an aarch64 host with 4 KiB pages never runs the
page-size-dependent code (mapping and aligning the payload and the natives) with larger pages. So
the script runs aarch64 apps in a QEMU system VM: `qemu-system-aarch64 -M virt -cpu max` under
TCG (no KVM), 2 GiB of memory, two CPUs and no network card (`-nic none`), booting the one kernel
pinned in [`tools/vm-pins.toml`](../tools/vm-pins.toml):

| `--kernel` | Kernel | Page size |
|---|---|---|
| `64k` | Rocky Linux 8.10's aarch64 kernel (`kernel-core`), from an RPM | 64 KiB |

Kernels with 16 KiB pages are not tested, having too few users (the pins file and the Makefile
say so). Without `--kernel`, every pinned kernel boots. The script takes the kernel image and its
config out of the RPM and refuses a kernel whose config does not set the pin's option
(`CONFIG_ARM64_64K_PAGES`) to `y`. The kernel command line is
`console=ttyAMA0 earlycon rdinit=/init panic=-1`, without `quiet`, so a boot that hangs before
`/init` shows how far it got. The initramfs, a "newc" cpio archive the script writes, holds a
pinned root file system (Debian trixie's slim one, or with `--userland el8` Rocky Linux 8.10's
container base image, glibc 2.28, for apps built `--glibc 2.28`), each `--app` in `/apps` (first
on `PATH`), the command lines and an `/init`. `/init` mounts `/proc`, sets the console log level
to 4 so kernel messages do not break into its marker lines, mounts `/sys`, `/dev` and `/tmp`,
prints `VMSMOKE-KERNEL`, `VMSMOKE-PAGESIZE`, `VMSMOKE-BEGIN n`, `VMSMOKE-END n <status>` and
`VMSMOKE-DONE` on the serial console, runs each command and powers the guest off.

Each `--run COMMAND` runs as root in `/tmp` with `FSBPYR_CACHE_DIR=/tmp/cache` and a limit of
`--timeout` seconds (300 by default); it must exit 0, and written `COMMAND => TEXT` its output
must contain `TEXT`. Without `--run` only the boot and the page size are checked. A boot gets 300
seconds and fails on another page size than the pin's, a command that fails or lacks its text,
fewer finished commands than given, or an `/init` that did not reach its end. The apps are
aarch64 executables built on an aarch64 host, for example
`make test-vm VM_ARGS="--app dist/hello --run 'hello => hello'"` (the Makefile's example).

Every file is pinned by URL and SHA-256, checked on every use and kept in `FSBPYR_TEST_DOWNLOADS`
(else in `fsbpyr-vm-smoke` below `XDG_CACHE_HOME` or `~/.cache`; about 150 MB). The script exits
0 when every boot passed, 1 when a check failed, 2 for a bad command line, a pinned file that is
not what its pin says or cannot be used, or a missing `qemu-system-aarch64` (nothing is
installed), and 3 when a pinned file can no longer be fetched (the pin needs renewing).
[`tests/unit/test_vm_smoke.py`](../tests/unit/test_vm_smoke.py) tests the script without a VM.

## Test files

The counts are the test items pytest collects (`pytest --collect-only -q -p no:cacheprovider`,
which runs no test): 4,590 in 147 files, on x86-64 under Python 3.14, with `FSBPYR_TEST_GLIBC`
and `FSBPYR_CORPUS_APPS` unset; per-file counts come from the test ids. They include tests that
fail on a host that lacks what they need. Some depend on the host and the Python
([Cases that do not apply](#cases-that-do-not-apply)):
`test_portability.py` parametrizes over the host architecture's images, 35 on x86-64 and 33 on
aarch64 (two of its tests run once per image, and `archlinux:latest` has no arm64 image);
`test_corpus.py` leaves out `tensorflow` on 3.14 (64 of the 65 apps run) and 23 apps on 3.15,
where three of `test_compat.py`'s checks, `test_natives.py` and `test_portability.py`'s `heavy`
tests go too. Nothing was deselected in the count above. In the EL8 test mode `sanitized` tests
run with the release launcher only, those two portability tests also on the two EL8 images, and
`test_el8_apps_run_on_el8` is not generated.

| Layer | Files | Tests |
|---|---|---|
| `tests/unit` (top level) | 26 | 895 |
| `tests/unit/boot` | 23 | 358 |
| `tests/unit/builder` | 19 | 752 |
| `tests/unit/payload` | 11 | 424 |
| `tests/unit/runtime` | 9 | 249 |
| `tests/integration` | 17 | 815 |
| `tests/e2e` | 42 | 1,097 |
| All | 147 | 4,590 |

### tests/unit (top level): 26 files, 895 tests

| File | Tests | What it tests, and how |
|---|---|---|
| [`test_bytecode.py`](../tests/unit/test_bytecode.py) | 29 | `fsbpyr.bytecode`'s canonical marshal of `compile()` output: round trips, idempotence, no dependence on reference counts or interned strings, sorted frozensets, equal constants merged, code objects never; malformed, recursive and too deep streams refused. |
| [`test_cli.py`](../tests/unit/test_cli.py) | 53 | The CLI in-process: version, help, SIGTERM like SIGINT, which commands collect the cache, `runtime list` with each runtime's glibc, error output (tracebacks only for unexpected exceptions; without a usable home one line and exit 1), Pythons outside 3.12 to 3.15 refused, `--glibc` over the project's `glibc`, the Python from the lock source installed (two tests marked `version_rule`), the output path as given, `1 command`. |
| [`test_cli_docs.py`](../tests/unit/test_cli_docs.py) | 35 | One source for help, completions and man page: bash completion, the man page's sections, options, keys, variables, exit statuses and examples (checked with groff); and docs against code: the README's quickstart, `CONTRIBUTING.md`'s release verification naming no version, `docs/compatibility.md`'s tables, quoted messages, `src/` paths in `docs/*.md`, `docs/format.md`'s version. |
| [`test_compat.py`](../tests/unit/test_compat.py) | 3 | `fsbpyr.compat`: every module of the package imports, the zstd module, the usable core count. |
| [`test_contain.py`](../tests/unit/test_contain.py) | 5 | `tests/support/contain.py` ([Containment](#containment-of-app-runs)) with commands that stop by themselves: a normal run, and a detached survivor, a pipe-holding descendant, a timeout and a self-restarting command killed and failing. |
| [`test_corpus_locks.py`](../tests/unit/test_corpus_locks.py) | 75 | Each corpus app's lock against `no-wheels.txt` ([The corpus](#wheels-sdists-and-known-failures)), plus `locks.usable` and `locks.installed`. |
| [`test_dispatch.py`](../tests/unit/test_dispatch.py) | 29 | `fsbpyr.dispatch`: a build for another Python runs that Python's variant of the same release, fetched from `FakeReleases` (`subprocess.run` recorded): caching, offline use, verification, truncated downloads, a too-new glibc, developer builds, names the collector keeps, `--glibc` passed on, and the variant told where the handing fsbpyr is. Marked `version_rule`. |
| [`test_downloads.py`](../tests/unit/test_downloads.py) | 7 | `tests/support/downloads.py`: seeding from kept files, files not matching their pin, keeping a session's downloads, pruning only same-baseline runtimes, a kept PBS distribution. |
| [`test_el8_stub_tool.py`](../tests/unit/test_el8_stub_tool.py) | 4 | `tools/release/el8-stub.py` on synthetic ELF stubs: one needing at most glibc 2.28 with the expected runtime id written under its asset name with its SHA-256; one needing `GLIBC_2.34` or carrying another runtime id exits 1; a missing runtime names `fsbpyr runtime build --python 3.14 --glibc 2.28`; a bad command line exits 2. |
| [`test_elf_mutation.py`](../tests/unit/test_elf_mutation.py) | 1 | Hypothesis (1,000 examples): mutated ELF bytes are read by `fsbpyr.elf` or raise `ElfError`, nothing else. |
| [`test_inspect.py`](../tests/unit/test_inspect.py) | 54 | `fsbpyr inspect` on `FAKE_RUNTIME` executables: text and JSON, columns, `--verify`, licences, files, lock and settings lines, older formats, damaged records, a glibc 2.28 app's `baseline` and `glibc_baseline`, file text that cannot drive the terminal. |
| [`test_no_skips.py`](../tests/unit/test_no_skips.py) | 2 | The `ast` scan of [A skip fails the run](#a-skip-fails-the-run), and that it finds each use. |
| [`test_poetry1.py`](../tests/unit/test_poetry1.py) | 16 | `fsbpyr.poetry1`: a Poetry 1 `pyproject.toml` without `[project]` read as one: constraints as PEP 440, `\|\|` and git dependencies refused, optional dependencies as extras, markers, which projects are unpackaged. |
| [`test_portability_images.py`](../tests/unit/test_portability_images.py) | 4 | `tests/support/images.py`: all twelve images on x86-64, in order; those with an arm64 variant on aarch64. |
| [`test_pyversion.py`](../tests/unit/test_pyversion.py) | 57 | The version rule (`fsbpyr.pyversion`): the Python that `requires-python`, a `.python-version` pin and `--python` give, and the errors. Marked `version_rule`. |
| [`test_requires.py`](../tests/unit/test_requires.py) | 16 | `tests/support/requires.py` and `tests/conftest.py`'s hooks, with pytester, as [A skip fails the run](#a-skip-fails-the-run) lists. |
| [`test_sbom.py`](../tests/unit/test_sbom.py) | 18 | The CycloneDX SBOM generator `tools/release/sbom.py`: identical bytes for identical input, CPEs and purls, the executable's hash, incomplete or unverified input refused. |
| [`test_signing.py`](../tests/unit/test_signing.py) | 212 | `fsbpyr.signing` from source with real keys, the openssl command in place of `sig.c`: the statement, names from a user and from a file, forged build info, the trust store, sums files, each command's output and exit status. |
| [`test_selfupdate.py`](../tests/unit/test_selfupdate.py) | 93 | `fsbpyr self update` against `FakeGitHub`: in-place update keeping the mode, variants, symlinks; bad, oversized or too-new-glibc downloads never installed; unwritable directories; man page and completions rewritten; placed glibc 2.28 stubs left alone, named only when the new companion release does not list them; a cut download an update error, not a traceback. |
| [`test_targets.py`](../tests/unit/test_targets.py) | 48 | The target table (`fsbpyr.targets`) and host detection: each target's and Python's pins and sysroot, the EL8 sysroot (Rocky Linux 8.10's `glibc`, `glibc-devel`, `glibc-headers`, `kernel-headers`, `libgcc` and `gcc` RPMs), other baselines refused, runtime ids per baseline, the manylinux platform, each target's loader, filter and machine. |
| [`test_test_modes.py`](../tests/unit/test_test_modes.py) | 6 | `FSBPYR_CORPUS_APPS` and the [EL8 test mode](#the-el8-test-mode): builds for the run's baseline unless a `--glibc` says otherwise, `baseline.as_tuple`, the EL8 images. `make test-el8-stub` runs it in the mode. |
| [`test_text.py`](../tests/unit/test_text.py) | 8 | `fsbpyr.text`: `plural`, and `tail_lines`, which cuts output at a line start and notes the earlier lines left out. |
| [`test_tool_metas.py`](../tests/unit/test_tool_metas.py) | 4 | `fsbpyr.tools.tool_scripts`: which script-less distributions a tool binary follows to their requirements: a meta-package's every requirement, a code distribution's namesake only; a library beside a tool gives none, a library alone is an error. |
| [`test_vcs.py`](../tests/unit/test_vcs.py) | 33 | `fsbpyr.vcs` on temporary git repositories: the commit and tag a build records (never an `el8-vX.Y.Z` tag), the version string, the commit time, asset names, the companion release `el8-vX.Y.Z`. |
| [`test_vm_smoke.py`](../tests/unit/test_vm_smoke.py) | 29 | `tools/vm-smoke.py` without a VM ([The page-size smoke test](#the-page-size-smoke-test)): the pins file, the cpio writer and initramfs, the RPM reader, failing or mismatching downloads, console parsing and checks, the QEMU and kernel command lines, `/init`'s log level, a QEMU that exits at once. |
| [`test_wiki.py`](../tests/unit/test_wiki.py) | 54 | The wiki's pages and `tools/wiki/`: every link of `wiki/*.md` has its target, the published form of a page, the import graphs the build draws, how publishing treats the wikis' history, and the messages, refusal and pinned keys that [User-Security](User-Security.md) shows against the code. The PDFs need pandoc and LuaLaTeX and are not built here (`make wiki-build`). |

### tests/unit/boot: 23 files, 358 tests

Most build an app with `FAKE_RUNTIME` and run the boot code in-process through the `boot`
fixture ([Configuration and conftests](#configuration-and-conftests)). `test_fixups.py`,
`test_gc.py`, `test_links_names.py`, `test_mp.py`, `test_reexec.py`, `test_service.py` and
`test_way.py` import the `_fsbpyr_rt` module they test on its own, without an app.

| File | Tests | What it tests, and how |
|---|---|---|
| [`test_boot.py`](../tests/unit/boot/test_boot.py) | 44 | On `tests/support/apps.py`'s demo app: the path hook (never above the executable), specs and loader, namespace packages, `pkgutil`, `importlib.resources` (one resource name on 3.12), `importlib.metadata`, entry points, `boot.run()` (selftest, `exit`/`quit`/`help`, helper interpreters, multiprocessing children, the exit trace), argparse fixed in apps of scripts only, and the excepthook: no 3.15 site-initialization hint for a missing module, and a note naming the package `--fs-package` would take for an `OSError` inside the executable. |
| [`test_commands.py`](../tests/unit/boot/test_commands.py) | 32 | `_fsbpyr_rt.commands`: the `name=target` table, the command from `argv[0]` or else `argv[1]`, the list and exit 2 without one, the selftest importing every target, a link started with `-m`, a forkserver preloading the parent command's module. |
| [`test_distutils.py`](../tests/unit/boot/test_distutils.py) | 2 | `import distutils` answers with setuptools' copy before setuptools is imported. |
| [`test_execve_argv0.py`](../tests/unit/boot/test_execve_argv0.py) | 8 | `sys.argv[0]`: the path `execve` got (`execfn` in `_fsbpyr.info()`), made absolute without collapsing `..`; left alone without a path, after a renamed start or a re-exec, or without a working directory. |
| [`test_fixups.py`](../tests/unit/boot/test_fixups.py) | 18 | `_fsbpyr_rt.fixups`, with fake SSL contexts, `ctypes.CDLL` and parso `FileIO`: the CA bundle fallback, bundled libraries by name, multiprocessing's executable, OpenCV's loader path, file times, argparse's `prog`. |
| [`test_frozen_sources.py`](../tests/unit/boot/test_frozen_sources.py) | 1 | `inspect.getsource` of CPython-frozen stdlib modules falls back to the payload. |
| [`test_fs_packages.py`](../tests/unit/boot/test_fs_packages.py) | 12 | fs-packages: files on disk before their code runs, code from the payload, resources as directories, extracted once per process, rechecked on import. |
| [`test_gc.py`](../tests/unit/boot/test_gc.py) | 25 | The app cache's collector (`_fsbpyr_rt.gc`): what it removes and never touches, one run at a time, the marker, a tree taken during collection (`tests/support/cache.py`'s `take`). |
| [`test_init_module.py`](../tests/unit/boot/test_init_module.py) | 3 | `pkg.__init__` imports as a module of its own (`from .__init__ import x`), as in a venv. |
| [`test_links_names.py`](../tests/unit/boot/test_links_names.py) | 4 | `_fsbpyr_rt.links`: a command name with a directory part (`../../evil`, `a/b`, `..`, `.`) makes no link. |
| [`test_mp.py`](../tests/unit/boot/test_mp.py) | 40 | `_fsbpyr_rt.mp`: multiprocessing's re-execution command lines (spawn, forkserver, resource tracker) accepted only from children of the same executable with the same ids, never evaluated. One test fails by design under user-mode QEMU. |
| [`test_native.py`](../tests/unit/boot/test_native.py) | 36 | `_fsbpyr_rt.native`, with `dlopen` faked: cache directory precedence, extension specs, a closure extracted once, damaged or symlinked cached files, unwritable caches, unsafe app names, bundled libraries by file name; a refused cache directory (`CacheRefused`) ending the start with exit 70 while other extraction failures stay errors, whatever their message; the `--fs-package` note. |
| [`test_native_xz.py`](../tests/unit/boot/test_native_xz.py) | 1 | xz natives and their pieces extract without a thread of the runtime. |
| [`test_pth_roots.py`](../tests/unit/boot/test_pth_roots.py) | 1 | Modules under a `.pth` directory import by their names relative to it. |
| [`test_reexec.py`](../tests/unit/boot/test_reexec.py) | 45 | `_fsbpyr_rt.reexec`: `-m`, `-c` and payload scripts from the app's children run with interpreter-reexec, are refused without it, run the app with `false`; other callers get the app; `exe COMMAND -m word` runs that command. Without the setting, the app's own command scripts run as `python script` would, for its children only. |
| [`test_resource_tree.py`](../tests/unit/boot/test_resource_tree.py) | 4 | Resources without a digest are rechecked by every process; a corrupt resources id is reported. |
| [`test_restart.py`](../tests/unit/boot/test_restart.py) | 19 | `_fsbpyr_rt.restart`: in gunicorn's and Django's `[sys.executable] + sys.argv` restart, the executable's path after interpreter flags is dropped with them, as a venv script would see it; nothing else counts as a restart, and a common start loads nothing. |
| [`test_scripts_overlay.py`](../tests/unit/boot/test_scripts_overlay.py) | 9 | The scripts root beside the executable: `__file__` there while code is the payload's, the path hook's claim, the script's directory first on `sys.path`, `get_data` from the payload, notes for a missing file. |
| [`test_selftest.py`](../tests/unit/boot/test_selftest.py) | 8 | `FSBPYR_SELFTEST=natives`: the loader's trace parsed for missing libraries and versions; a refused cache directory ends it with exit 70. |
| [`test_service.py`](../tests/unit/boot/test_service.py) | 19 | `_fsbpyr_rt.service`: `JOURNAL_STREAM` matched to a stream made line-buffered; the SIGTERM handler exiting 143, in process 1 only, over the default disposition only, gone after fork. |
| [`test_syspath_subdirs.py`](../tests/unit/boot/test_syspath_subdirs.py) | 4 | `sys.path` entries below the executable (`<exe>/pkg/_vendor`): modules there import by their short names. |
| [`test_warning_frames.py`](../tests/unit/boot/test_warning_frames.py) | 3 | Import-time warnings with `stacklevel >= 2` name the importing module and show the payload file's source line. |
| [`test_way.py`](../tests/unit/boot/test_way.py) | 20 | A cache root's way from `/` (`gc.way_problem`): owners and write permissions, sticky directories, symlinks, `..`, user namespaces, mapped uids; the tool's cache and the collector refuse an untrusted way. |

### tests/unit/builder: 19 files, 752 tests

The build pipeline in-process, on trees made in `tmp_path`: fake site-packages, synthetic ELF
objects from `tests/support/elf.py`, local distributions, the `foreign` fixture's hand-written
locks, fake `uv` scripts and the uv command lines a plan records.

| File | Tests | What it tests, and how |
|---|---|---|
| [`test_build.py`](../tests/unit/builder/test_build.py) | 54 | `fsbpyr.build`: the smoke test and its error output, the output replaced only after it passes, stub and natives' glibc requirement against the baseline (`glibc_baseline` for 2.28 only), the project's extension rebuilt against the sysroot, uv version warnings, `--strict`, `--offline`, and hints about unpinned requirements, sdists and helper interpreters. |
| [`test_closure_and_classify.py`](../tests/unit/builder/test_closure_and_classify.py) | 79 | `fsbpyr.closure` and `fsbpyr.classify` on fake site-packages: the installed-dist index, the dependency walk, entry points, every file's class (modules, namespaces, natives, `.pth` files, fs-packages, shadowed modules, compression, spill), and the warnings and hints, among them main-guard reads next to `__file__` in the `__main__` module only. |
| [`test_compiler.py`](../tests/unit/builder/test_compiler.py) | 10 | `fsbpyr.compiler`: worker processes compile what the build's process would, in order; small builds compile in-process; compile warnings come back with their line; main-guard reads scanned in the scripts root's commands only. |
| [`test_early_checks.py`](../tests/unit/builder/test_early_checks.py) | 12 | Failures before uv stages anything: a script name that is no command, a static project without entry, a directory as output, an unknown extra. |
| [`test_entry.py`](../tests/unit/builder/test_entry.py) | 11 | `fsbpyr.entry`: an `if __name__ == "__main__":` guard in a module's body only, and the entry candidates of an installed project and a source tree. |
| [`test_foreign.py`](../tests/unit/builder/test_foreign.py) | 56 | `fsbpyr.foreign` on the `foreign` fixtures: `poetry.lock` (Poetry 2, 1.5 to 1.8, lock-version 1.0) and `pdm.lock` as a build's lock: the main group, markers, every hash demanded, extras, path, VCS and URL entries, other indexes refused, the stale-lock check, the lock's record and rank, option injection refused. |
| [`test_legacy.py`](../tests/unit/builder/test_legacy.py) | 11 | `fsbpyr.legacy`: which `scripts=` files of the project's wheel are Python, their stored modules and clashes, warnings for dropped scripts. |
| [`test_loose.py`](../tests/unit/builder/test_loose.py) | 58 | Projects without a build system (`fsbpyr.loose`, `fsbpyr.importcheck`): what makes one, which files are packaged (git decides in a work tree), fsbpyr's own wheel (a valid `RECORD`, the same bytes twice), the commands found, and the static import check (file and line, removed stdlib modules, the freeze hint, `--strict`). |
| [`test_loose_directory.py`](../tests/unit/builder/test_loose_directory.py) | 93 | Directories of loose scripts: discovery and what it refuses, the directory's name, shebangs, packaged files and links, mangled names and clashes, commands, merged PEP 723 blocks, the deploy recipe, `sys.path` changes from `__file__`. Discovery runs no user code. |
| [`test_manifest.py`](../tests/unit/builder/test_manifest.py) | 19 | `fsbpyr.manifest`: the lock and each distribution recorded with its artifact's hash, sdist builds, local dists, workspaces, the same records from two directories, the `compat` fixture traced to its `uv.lock`. |
| [`test_multicall.py`](../tests/unit/builder/test_multicall.py) | 34 | A multi-command executable's build side: the sorted `name=target` table, command names, relative links beside the output and what they may replace, an earlier output replaced rather than written through, VCS requirement lines in a hashed file failing early. |
| [`test_natives.py`](../tests/unit/builder/test_natives.py) | 44 | `fsbpyr.natives` and `fsbpyr.elf` on synthetic ELF objects and two host binaries: dynamic sections, RPATH and RUNPATH as ld.so applies them, libraries by name, unresolved libraries, symbol versions, malformed input. |
| [`test_pipenv.py`](../tests/unit/builder/test_pipenv.py) | 45 | `fsbpyr.pipenv` on `foreign/pipenv`: the `default` section, markers, hashes, unpinned versions refused, git, path and file entries, other indexes, the stale-lock check, `_meta.requires` (two tests marked `version_rule`), the lock's rank, a Python-less directory as a tool binary. |
| [`test_project_and_staging.py`](../tests/unit/builder/test_project_and_staging.py) | 78 | `fsbpyr.project` (discovery, lock source priority, settings, `glibc` among them) and `fsbpyr.staging` (uv commands per lock source, wheels for the baseline's platform, workspaces, rebuilds, `SOURCE_DATE_EPOCH`, pinned requirement lines, the source tree left as found). |
| [`test_scan.py`](../tests/unit/builder/test_scan.py) | 39 | `fsbpyr.scan` on snippets: reads relative to `__file__` (in a command's main guard when asked; not existence checks, nor a script starting a sibling through `sys.executable`, though a package doing so is), libraries opened by a literal name. |
| [`test_single_script.py`](../tests/unit/builder/test_single_script.py) | 20 | One script as the project: `.py` or extensionless `uv run` scripts, the import closure, one PEP 723 block, the directory's other files ignored, a script lock locking the build, nothing written beside it. |
| [`test_system_report.py`](../tests/unit/builder/test_system_report.py) | 21 | `fsbpyr.system`: what the target must provide beyond glibc, each target's manylinux_2_34 and manylinux_2_28 floors (libstdc++, CXXABI, GCC, zlib), and the warnings. |
| [`test_tools.py`](../tests/unit/builder/test_tools.py) | 47 | The tool binary (`fsbpyr.tools`) on hand-built staged sites: when a directory is one, which named distributions are tools, their scripts as commands, meta-packages and families (cycles and a depth limit stop), libraries, missing distributions, name clashes. |
| [`test_venvdeps.py`](../tests/unit/builder/test_venvdeps.py) | 21 | The venv rung (`fsbpyr.venvdeps`): the venv found, the distributions providing unresolved imports and their `Requires-Dist` closure at installed versions, nothing guessed from PyPI, editable installs refused, the venv's Python never started, `inferred` in the lock record, `--strict`. |

### tests/unit/payload: 11 files, 424 tests

Writer and reader on specs from `make_spec`; edge cases from `tests/support/edges.py`.

| File | Tests | What it tests, and how |
|---|---|---|
| [`test_blocks.py`](../tests/unit/payload/test_blocks.py) | 20 | Small blobs in shared zstd blocks: placement, round trips, identical content once, the block cache, bad member records refused. |
| [`test_edges.py`](../tests/unit/payload/test_edges.py) | 81 | The Python reader, with the stand-in's cache sync, on the intact edge-case executable and the 66 format and 14 path cases: refused with `PayloadError` (or `CacheError`, `OSError` where the cache refuses) or read, never failing otherwise. |
| [`test_file_digest.py`](../tests/unit/payload/test_file_digest.py) | 80 | A file's integrity digest, streamed a leaf at a time: sizes around leaves, ranges, holes as zeros, short files; an executable's digest and plain SHA-256 from one read, on 1, 3 and 8 threads, its holes unread (the reads counted). |
| [`test_hardening.py`](../tests/unit/payload/test_hardening.py) | 37 | Regression tests from review findings: hostile sizes, path traversal, reserved fields, overlapping sections, bad closures, zstd frames without checksum or content size, trailing bytes. |
| [`test_integrity.py`](../tests/unit/payload/test_integrity.py) | 15 | The whole-file digest: the tree, leaf counts, one construction for every representation, any changed byte failing, older versions. |
| [`test_roundtrip.py`](../tests/unit/payload/test_roundtrip.py) | 39 | Writer to reader: every table and field, lookups, deterministic encoding, deduplication, alignment, stub and trailer, an executable replaced only when committed. |
| [`test_spill.py`](../tests/unit/payload/test_spill.py) | 24 | Compressed bytes in a spill file: the same blobs as in memory, sparse files, pieces on 1 and 8 threads, pieces inside holes compressed once into the bytes their zeros make. |
| [`test_streaming_writer.py`](../tests/unit/payload/test_streaming_writer.py) | 7 | The streaming writer: the assembled image, zero runs as holes, a file changed since classification failing, the result verified. |
| [`test_validation.py`](../tests/unit/payload/test_validation.py) | 80 | Malformed specs and bytes refused, and two Hypothesis properties, XOR masks (300 examples) and 64-bit overwrites (500), raising only `PayloadError`. |
| [`test_xz.py`](../tests/unit/payload/test_xz.py) | 25 | xz natives: standard streams, raw when incompressible, pieces, streams and pieces that do not match their records refused. |
| [`test_zstd_pieces.py`](../tests/unit/payload/test_zstd_pieces.py) | 16 | zstd in pieces: a frame per piece, the same bytes on any thread count, round trips across boundaries, refusals. |

### tests/unit/runtime: 9 files, 249 tests

| File | Tests | What it tests, and how |
|---|---|---|
| [`test_collect.py`](../tests/unit/runtime/test_collect.py) | 20 | The tool's cache collector (`fsbpyr.runtime.collect`) and `fsbpyr cache gc`: what stays, what goes and when, the marker, a user's directory left alone. |
| [`test_fetch_and_build.py`](../tests/unit/runtime/test_fetch_and_build.py) | 39 | `fsbpyr.runtime.pbs` and the registry's build, on in-memory archives and a patched `urlopen`: verification, retries, path traversal refused, pins, runtime ids, a runtime per glibc baseline, `fsbpyr runtime build --glibc`, zstd's version from `libzstd.a`, a failing worker's output. |
| [`test_notices.py`](../tests/unit/runtime/test_notices.py) | 15 | Licence notices of the libraries a runtime links, from a made-up PBS distribution; zstd's text for every Python. |
| [`test_registry.py`](../tests/unit/runtime/test_registry.py) | 44 | `fsbpyr.runtime.registry`: payload to spec and back, saving and loading runtimes, app assembly, generation and resources ids, private directories, the embedded runtime, used for its own baseline only, a clear error without a home, the error naming `fsbpyr runtime build --python 3.14 --glibc 2.28`. |
| [`test_rpm.py`](../tests/unit/runtime/test_rpm.py) | 22 | `fsbpyr.runtime.rpm`, the EL8 sysroot's RPM reader, on synthetic packages: entries as tar members under the merged `/usr`; refused: no RPM, no xz, damage, truncation, bad cpio headers, escaping names, devices, incomplete hard link sets, oversized payloads. |
| [`test_stdlib.py`](../tests/unit/runtime/test_stdlib.py) | 6 | Stdlib classification, `-X importtime` parsing, compile-worker framing. |
| [`test_stubs.py`](../tests/unit/runtime/test_stubs.py) | 47 | `fsbpyr.runtime.stubs`, a release's glibc 2.28 stub, with `FakeReleases`: placed over cached over downloaded from the companion release `el8-vX.Y.Z` (checked against its `SHA256SUMS-glibc2.28`, kept for offline use), the runtime id checked whatever the source, a stub cached under its id so the next release reuses an unchanged one, `--offline`, a variant looking beside the fsbpyr that handed it the build, a size limit, the messages for what is missing or unreachable, a developer build asking the cache. |
| [`test_sysroot.py`](../tests/unit/runtime/test_sysroot.py) | 21 | The glibc sysroots: `ar` members, symlinks kept inside, escaping entries refused, verified HTTPS downloads, RPMs extracted as debs are, `lib` and `lib64` linked into `usr` for EL8 only. |
| [`test_toolchain_and_link.py`](../tests/unit/runtime/test_toolchain_and_link.py) | 35 | `ar` listing, clang discovery and the stub's link: static PIE, hardening flags, the sanitized variant, aarch64's outlined atomics and Cortex-A53 erratum 843419 fix, mapped paths, the frame limit, `-DFSBPYR_RUNTIME_ID`, a failed link's output. |

### tests/integration: 17 files, 820 tests

| File | Tests | What it tests, and how |
|---|---|---|
| [`test_bytecode_determinism.py`](../tests/integration/test_bytecode_determinism.py) | 2 | Canonical bytecode over the real stdlib and across build processes ([Determinism checks](#determinism-checks)). |
| [`test_check_release_el8.py`](../tests/integration/test_check_release_el8.py) | 16 | The two EL8 steps of `tools/release/check-release.sh` against a fake `gh` and `git`: no companion skips both; a draft, prerelease or latest companion, a misplaced tag, a missing asset and a failed attestation fail; a complete companion passes, and each release's stub is found where 0.26.0, 0.27.0 and later releases put it. |
| [`test_check_release_install.py`](../tests/integration/test_check_release_install.py) | 7 | The `FSBPYR_VERIFY=1` install step of `check-release.sh`, with a stand-in `install.sh` and a fake `gh`: logged in and not, a release from before signing left out, and each wrong outcome a failure, a missing login that reads as tampering among them. |
| [`test_el8_workflow.py`](../tests/integration/test_el8_workflow.py) | 36 | [`.github/workflows/el8.yml`](../.github/workflows/el8.yml) and `full-suite.yml`'s EL8 jobs, read as text: triggers and refs, nothing linked for a commit whose full suite failed, each stub linked once and nothing tested by el8.yml, EL8 jobs that fail the run, runners per architecture, pinned actions, write permissions in `draft` only, the companion a draft on its existing tag and not the latest; the draft job's steps also run against a fake `gh`. |
| [`test_install.py`](../tests/integration/test_install.py) | 140 | `install.sh` from fake release assets over HTTPS on 127.0.0.1, with `HOME`, install directories and a `PATH` of chosen tools under `tmp_path`, so a test can leave out or fake `xz`, `curl`, `gh`, `uname` or `ldd`. Each installer test runs under dash, bash and busybox sh (132 of the 140); the rest test the generator and that dash and busybox parse the template. |
| [`test_launcher_format.py`](../tests/integration/test_launcher_format.py) | 18 | `launcher/format.h` and `fsbpyr.payload.format` describe the same bytes: a C probe built with `cc` prints sizes, constants and offsets to compare. |
| [`test_make_release.py`](../tests/integration/test_make_release.py) | 41 | `make release` (`tools/release/release.sh`) with stand-ins for its parts: their order, what each failure stops, going on after a stop, its JSON report and no passphrase in it, a passphrase file run only when it is a program, `KEY` taken from make's command line only, never pulling. |
| [`test_mirror.py`](../tests/integration/test_mirror.py) | 28 | `make mirror` (`tools/release/mirror.sh`) on two bare repositories: GitLab follows only what this checkout and GitHub agree on, a disagreement stops it before any push, a branch goes only when main holds it. |
| [`test_pbs_distribution.py`](../tests/integration/test_pbs_distribution.py) | 8 | What the runtime build relies on in the pinned PBS distribution: startup modules, the compile worker, the link plan, licence texts. It comes from `FSBPYR_TEST_DOWNLOADS` or is fetched (`downloads.pbs_distribution`). |
| [`test_pbs_stdlib_versions.py`](../tests/integration/test_pbs_stdlib_versions.py) | 12 | The stdlib selection and startup modules for each pinned CPython version. |
| [`test_release.py`](../tests/integration/test_release.py) | 157 | The release scripts against throwaway git repositories, a bare origin and a fake `gh`, the companion release and its tag included; also the workflow files, `tools/bootstrap.sh` and the Makefile's `VERSION` and `PYTHON`. Nothing reaches GitHub. |
| [`test_release_drafts.py`](../tests/integration/test_release_drafts.py) | 36 | No workflow can reach a release key, publish a release or read a variable, and `release.yml` and `el8.yml` stop at a draft; the draft job's file check runs for real against files and sums that the built code in `prepare` could choose. |
| [`test_release_sign.py`](../tests/integration/test_release_sign.py) | 172 | `make release-sign` and `make release-audit` with the real scripts and keys and a bare `hub`, against [`FakeGitHub`](../tests/support/fakegithub.py), which can do all an account can but attest: moved commits and tags, foreign attestations, runners and builds, files swapped during signing, a second draft, a key in the checkout; each audit finding; no failed lookup ever a pass. |
| [`test_release_signing.py`](../tests/integration/test_release_signing.py) | 113 | [`tools/release/release-signing.sh`](../tools/release/release-signing.sh), `previous-release.sh` and `check-signature.sh`, with fake programs on `PATH` and passphrases typed on a pty: bad key files and `.pub`s, nothing replaced, burned tags, companions, the previous-release lookup, each pin rule of the sign job. |
| [`test_runner_disk.py`](../tests/integration/test_runner_disk.py) | 20 | [`tools/runner-disk.sh`](../tools/runner-disk.sh), which makes room on a runner's disk for [`full-suite.yml`](../.github/workflows/full-suite.yml)'s suite and corpus jobs, with a fake `df`: only the listed paths removed, the free-space check at its boundary, bad arguments refused first; the two jobs, read as text, making room early and reporting free space. |
| [`test_upstream_watch.py`](../tests/integration/test_upstream_watch.py) | 4 | `tools/upstream-watch.sh` against a fake `gh` serving python-build-standalone releases: up to date, a newer release opening or updating an issue, no release with the archive. |
| [`test_worker_venvs.py`](../tests/integration/test_worker_venvs.py) | 10 | [`worker_venvs.py`](../tests/support/worker_venvs.py) on real pytest runs with three workers: each in an environment of its own, subprocesses included, removed after the run, and a worker in anything else stopping the run. |

### tests/e2e: 42 files, 1,097 tests

| File | Tests | What it tests, and how |
|---|---|---|
| [`test_baseline.py`](../tests/e2e/test_baseline.py) | 4 | Each glibc baseline's stub and sysroot: the default stub needs no glibc symbol above 2.34, one linked against Rocky Linux 8.10's sysroot none above 2.28; `launcher/glibc.c` compiles against both header sets; each sysroot's `build_flags` build a C program. |
| [`test_build.py`](../tests/e2e/test_build.py) | 55 | `fsbpyr build` on the fixtures: `hello` with `inspect --json --verify` and its SBOM, `clicky` from each lock source, the README quickstart, determinism and self-hosting, `cext` rebuilt against the sysroot, workspaces, dependency groups, editable requirements, the smoke test, `--strict`, `pylock.toml` hashes, Poetry 1, projects without scripts, legacy `scripts=`, compile warnings, a failed build cleaned up. |
| [`test_cache_root.py`](../tests/e2e/test_cache_root.py) | 30 | The launcher's cache root checks (marked `sanitized`): roots below directories others may write to, symlinks and loops, `..`, relative roots, user namespaces, another user's symlink; a refused cache directory stopping an app at its extension's import with `fsbpyr: cannot start <executable>: refusing cache directory <path>: ...` and exit 70. |
| [`test_compat.py`](../tests/e2e/test_compat.py) | 41 | The `compat` fixture's matrix against the venv, the `mp` fixture's start methods, helper interpreters and the instance limit, forged multiprocessing children, scripts through `sys.executable`, `sys.argv[0]`, the restart idiom, the `datafiles` checks, the build information tracing every dist to the lock. |
| [`test_corpus.py`](../tests/e2e/test_corpus.py) | 66 | One test per corpus app with wheels for the Python and machine (64 apps on 3.14), the `expect` check, and the `plugins` fixture ([The corpus](#the-corpus)). |
| [`test_signatures.py`](../tests/e2e/test_signatures.py) | 153 | `launcher/sig.c` through `probes/sig.c`, and `_fsbpyr`'s functions on both launchers: RFC 8032, ACVP and all of Wycheproof's Ed25519 and ML-DSA-65 cases, malleated signatures, bad key files, the openssl.cnf matrix, the error queue, sig.c's declarations against OpenSSL's headers. |
| [`test_signing.py`](../tests/e2e/test_signing.py) | 36 | `key`, `sign`, `verify` and signing in `build` on a built fsbpyr with test keys pinned, against the release script: the passphrase on a pty, what the build's processes get, where a `.sig` goes, planted modules, this tree's own pins refusing test keys. |
| [`test_verify.py`](../tests/e2e/test_verify.py) | 66 | `verify` on a built fsbpyr with test keys pinned: files named by option and variable, no fallback; a file of fsbpyr against its release on a local server, with a replayed signature, another key or project, a forged tag, a bad file beside it, no network; the warning; the Security chapter's quick start as printed. |
| [`test_selfupdate.py`](../tests/e2e/test_selfupdate.py) | 9 | `self update` of a built fsbpyr with test keys pinned, from `FakeGitHub`, to a second build: unsigned, signed by an unpinned key, a `.xz` not matching its line, an older build served as new, three pins, a client that skipped a key change. |
| [`test_digests.py`](../tests/e2e/test_digests.py) | 6 | The launcher's digest routines through `probes/digests.c` against the Python reader's `tree_digest`; holes not read and leaves of zeros not hashed (`pread` and `SHA256_Update` counted), no hole past a file's end. |
| [`test_dispatch.py`](../tests/e2e/test_dispatch.py) | 6 | A project, and a single script, that exclude the running Python build through the variant: fsbpyr built from the checkout on another Python, served by `FakeReleases`. Needs that Python installed with uv. Marked `version_rule`. |
| [`test_due.py`](../tests/e2e/test_due.py) | 22 | When a cache collection is due, through `probes/due.c` (`statvfs` wrapped): a detached idle-priority process, one start of many claiming the run, `FSBPYR_GC`, the root the boot code finds. |
| [`test_edges.py`](../tests/e2e/test_edges.py) | 156 | The launcher, release and sanitized, on every edge case that leaves an executable (63 of 66) and every path case: refusing to start (exit 70), refusing the record, or accepting, as the table says, never crashing. The app walks its records with `tests/support/walk.py`. |
| [`test_el8.py`](../tests/e2e/test_el8.py) | 14 | `fsbpyr build --glibc 2.28` on real runtimes: each stub's runtime id, `glibc >= 2.28` and `baseline   glibc 2.28` in the summary and `inspect`, the recorded `glibc_baseline`, runtime and `rocky8-glibc2.28-` sysroot, a manylinux_2_28 cryptography wheel, apps run on the host, `cext` rebuilt. Then this checkout as release v1.2.0, with the stub from a `FakeReleases` companion `el8-v1.2.0` or placed beside it, builds what the cache's runtime builds; without it, exit 1 and the plain message, and a default stub under the EL8 name is refused. |
| [`test_el8_use_cases.py`](../tests/e2e/test_el8_use_cases.py) | 4 | `--glibc 2.28` and the `glibc` setting with a directory of scripts, a single script whose PEP 723 block sets it, and the `multi` fixture, inspected and run. Needs `el8_runtime`; `make test` runs it. |
| [`test_file_overlay.py`](../tests/e2e/test_file_overlay.py) | 14 | Files next to a script in a directory of scripts: the deployed file read while code is the payload's (the `pylib` hack, stale copies, tracebacks), argparse's `prog`, spawn and forkserver, `inspect.getsource`, self-restarts, the missing-file note; a packaged project's `__file__` stays inside. |
| [`test_find_object.py`](../tests/e2e/test_find_object.py) | 2 | The stub's `_dl_find_object` wrapper (`launcher/glibc.c`) through `probes/find_object.c`: glibc's own where it has one, else the program headers giving the same object. |
| [`test_fuzz.py`](../tests/e2e/test_fuzz.py) | 3 | The libFuzzer harnesses with ASan and UBSan: `tests/fuzz/payload_fuzz.c` on each edge case, then `FSBPYR_FUZZ_SECONDS` of exploring; `tests/fuzz/sig_fuzz.c` for 30 seconds ([Probes and the fuzzer](#probes-and-the-fuzzer)). |
| [`test_gc.py`](../tests/e2e/test_gc.py) | 15 | Collection by the launcher: the held tree, `FSBPYR_GC=1` and `=0`, a background run a day after the last, a user's directory never collected, the passwd home when `HOME` is unusable. |
| [`test_imports.py`](../tests/e2e/test_imports.py) | 16 | The demo app as a real executable (marked `sanitized`): the C module against the stand-in's contract, extracted files never synced (strace), boot imports limited to built-in, frozen and startup modules, resources, metadata, tracebacks, the working directory never shadowing payload sources. |
| [`test_install_links.py`](../tests/e2e/test_install_links.py) | 9 | `FSBPYR_INSTALL_LINKS=DIR`: links with `name.py` aliases to the executable's real path, each running its command; the app not run, reruns idempotent, obstacles reported and kept, none from the collection child, a bad directory an error first; a single script's name and alias. |
| [`test_interrupted_build.py`](../tests/e2e/test_interrupted_build.py) | 2 | A `fsbpyr build` process killed with SIGTERM, or out of room under a file-size limit, leaves the output as it was and no temporary file. |
| [`test_large.py`](../tests/e2e/test_large.py) | 3 | A package with a 5 GiB sparse file builds (compressed and raw), runs, streams, extracts and verifies in bounded memory, each process reading less than 1 GiB (`rchar`): no hole is read. |
| [`test_launcher_build.py`](../tests/e2e/test_launcher_build.py) | 16 | No launcher source has a variable-length array or a frame over the limit, and the check catches both; the sanitized launcher carries ASan and UBSan; the launcher links reproducibly. |
| [`test_loose_directory.py`](../tests/e2e/test_loose_directory.py) | 37 | Directories of loose scripts as one executable with a link per command, each printing what `python3 <script>` prints: names, shadowing, links from elsewhere, the `pylib` hack, siblings through `sys.executable`, undeclared imports, `--strict`, no user code run by the build, PEP 723 blocks, the venv rung, multiprocessing, one command becoming several and back. |
| [`test_multicall.py`](../tests/e2e/test_multicall.py) | 22 | The `multi` fixture's two console scripts as one executable: links, the summary and `inspect`, starts by path, name or first argument, the list and exit 2, a renamed copy, a file in the way, `-m` and `-c`, helper interpreters, multiprocessing, a hard link keeping the old build. |
| [`test_natives.py`](../tests/e2e/test_natives.py) | 16 | The `heavy` fixture (numpy, orjson, cryptography, pydantic, onnxruntime) against the venv: licences, `FSBPYR_SELFTEST=natives`, the first run's extraction, xz natives, concurrent first runs, a damaged extension repaired, the native generation reused. Deselected without an onnxruntime wheel. |
| [`test_pid1.py`](../tests/e2e/test_pid1.py) | 3 | An app as process 1 of a PID namespace (`unshare --user --map-root-user --pid --fork --mount-proc`, else `podman run` of `debian:13`): SIGTERM ends it with 143 unless it has its own handler; not as process 1 it dies of the signal. |
| [`test_pipfile_lock.py`](../tests/e2e/test_pipfile_lock.py) | 5 | `foreign/pipenv`: builds from the lock's `default` section, a stale lock warns (an error with `--strict`), a changed hash fails, a Python-less directory builds the tool. |
| [`test_portability.py`](../tests/e2e/test_portability.py) | 35 | `hello`, `heavy`, `heavy-pip` and `compat` in each image with `podman run --network=none`, with `FSBPYR_SELFTEST=natives` and the reported libraries and glibc against `llvm-objdump`; `test_el8_apps_run_on_el8` (two items) runs glibc 2.28 builds of `hello` and a cryptography app (prebuilt `hello-el8` and `native-el8`, else built) on `rockylinux:8` and `almalinux:8`. Marked `podman`. |
| [`test_read_failures.py`](../tests/e2e/test_read_failures.py) | 7 | The launcher's blob reads out of memory through `probes/reads.c` (`ZSTD_createDCtx`, `ZSTD_decompress`, `ZSTD_decompressStream`, `lzma_stream_buffer_decode` and `madvise` wrapped): every codec read and streamed fails with `ENOMEM` and `FSB_STREAM_MEMORY`, pieces on helper threads too, and corrupt data stays corrupt when dropping the payload's pages fails; a stub linked with `probes/no_context.c` starts out of memory with `out of memory`, not `corrupt startup module`. |
| [`test_restore.py`](../tests/e2e/test_restore.py) | 12 | The cache sync's writers through `probes/restore.c` (`renameat`, `linkat`, `pthread_create`, `pwrite` wrapped): whole files, helper threads, unnamed files linked or renamed into place, the thread limit, the first failure in path order, a full disk under a leaf of a pooled file and of one in pieces. |
| [`test_runtime_app.py`](../tests/e2e/test_runtime_app.py) | 90 | Small apps from `build_app`, mostly marked `sanitized`: exit codes, tracebacks, signals, set-gid refusal, the whole-file check, damaged records, unsafe paths, resource limits (the file-size one also on zstd pieces), the block cache, an owner's set-uid start, the payload on a 64 KiB boundary, `stubs/_fsbpyr.pyi` against the module. |
| [`test_scripts.py`](../tests/e2e/test_scripts.py) | 21 | The `scripts` fixture (`uv init --no-package`) runs as `uv run main.py` does: the tree untouched, reproducible builds, a module with `[project.scripts]`, no top-level code run by the build, several guarded modules, clashing names, missing imports, main-guard reads only for `--entry`'s module; and the `foreign` Poetry 2 and PDM projects. |
| [`test_single_script.py`](../tests/e2e/test_single_script.py) | 12 | One script as an executable printing what `python3 <script>` prints: `dist/<stem>`, only its closure, `uv run` scripts, its block's dependencies, settings and `requires-python`, the freeze hint, a venv's pins and a script lock, nothing written beside it. |
| [`test_slots.py`](../tests/e2e/test_slots.py) | 29 | The instance limit through `probes/slots.c` (`geteuid` wrapped): slots held and freed, forks and execs, files not ours alone, the override and its malformed values, the default. |
| [`test_stream_windows.py`](../tests/e2e/test_stream_windows.py) | 21 | The launcher's blob streams through `probes/streams.c` with 2 MiB windows: every codec around window boundaries, from the start and after seeks. |
| [`test_streaming.py`](../tests/e2e/test_streaming.py) | 6 | A 1.5 GiB file extracts in bounded memory, zero runs become holes, a raw file with holes between its data extracts whole and its leaves in the executable's holes are not read, resources stream and seek, `_fsbpyr.read()` returns what was asked. |
| [`test_stub_hardening.py`](../tests/e2e/test_stub_hardening.py) | 6 | The stub read with `readelf` and `objdump`: position independent, glibc libraries only, bound at load time, RELRO, non-executable stack, stack protector. |
| [`test_tool_binary.py`](../tests/e2e/test_tool_binary.py) | 17 | A Python-less directory with a requirements file as one executable of the tools' console scripts (real pyflakes, pycodestyle and flake8, local distributions): links printing what the tools print under uv, no script payload, licences, freezes, `pylock.toml`, `--requirements`, `--entry`, gui scripts, meta-packages, the errors, a venv never read. |
| [`test_unbuffered.py`](../tests/e2e/test_unbuffered.py) | 8 | `PYTHONUNBUFFERED` set and non-empty: a line printed to a pipe arrives at once and survives SIGTERM; `JOURNAL_STREAM` naming the stdout pipe makes it line-buffered; otherwise block-buffered. |
| [`test_way_targets.py`](../tests/e2e/test_way_targets.py) | 2 | The cache root's way through `probes/way.c` (`readlinkat` wrapped) on symlink targets no local file system makes: one of `PATH_MAX` bytes, which may have been cut short, refused; one a byte shorter followed to its end. |

## Support modules

| Module | Provides |
|---|---|
| [`apps.py`](../tests/support/apps.py) | `demo_app()`: the demo application (packages, a namespace package, resources, metadata, natives that are never loaded, damaged xz natives, thousands of small files for the cache sync's batches and chunks), with `contract.py` and `probe.py` packaged as modules; `library()`, a compressible stand-in for a native library. |
| [`baseline.py`](../tests/support/baseline.py) | `BASELINE`, the glibc baseline the tests build for (`FSBPYR_TEST_GLIBC`, else 2.34), `EL8` (whether that is the [EL8 test mode](#the-el8-test-mode)), and `as_tuple` (`"2.28"` as `(2, 28)`). |
| [`cache.py`](../tests/support/cache.py) | A pure-Python mirror of the launcher's cache sync (`launcher/cache.c`), step by step, for the stand-in; `take()` for the collector's tests. |
| [`contain.py`](../tests/support/contain.py) | `run()`, with `Runaway` and `Leftover` ([Containment](#containment-of-app-runs)). |
| [`contract.py`](../tests/support/contract.py) | The contract battery for the `_fsbpyr` API, standard library only, run against the C module and the stand-in (leaving out `info()`'s `execfn`, the path the process was started by). |
| [`corpus.py`](../tests/support/corpus.py) | The corpus: the app list and `FSBPYR_CORPUS_APPS`'s selection (`selected()`), `commands.toml`, settings, wheel gaps and the apps that run on a Python and machine (`runnable()`), each app's `Result`, its recording and the session summary. |
| [`differential.py`](../tests/support/differential.py) | `make_venv`, `run_both`, `assert_same` ([Differential testing](#differential-testing)). |
| [`downloads.py`](../tests/support/downloads.py) | The runtime's sha256-pinned downloads (the CPython distribution; Debian debs for 2.34, Rocky Linux RPMs for 2.28) and built runtimes kept in `FSBPYR_TEST_DOWNLOADS`: `pinned`, `fill`, `seed`, `keep`, `build_kept_runtime`, `seed_runtime`, `prune_runtimes`, each per glibc baseline, and `pbs_distribution` for the integration tests. Only files that match their pin go in or out. |
| [`edges.py`](../tests/support/edges.py) | The edge-case table: `edge_app` (every record kind and codec), `layout` (where each part of an intact executable is), `CASES` (one field corrupted each, the digest restamped so every check behind the integrity check is reached, with what the reader and the launcher do), `PATH_CASES`, `apply`, `refused`. |
| [`elf.py`](../tests/support/elf.py) | `make_elf`: minimal ELF64 shared objects with chosen `DT_NEEDED`, RPATH, RUNPATH, SONAME, version needs, machine and type. |
| [`images.py`](../tests/support/images.py) | The portability matrix's twelve images (glibc 2.34 to 2.44), `EL8_IMAGES` (`rockylinux:8` and `almalinux:8`, glibc 2.28, both with an arm64 variant), those without an arm64 variant, and `images_for(machine, glibc)`, which adds the EL8 images for glibc 2.28. |
| [`locks.py`](../tests/support/locks.py) | What a `uv.lock` installs on Linux for one Python and machine (markers and extras evaluated), and which of those packages lack a wheel usable at `baseline.py`'s baseline: manylinux_2_34, or manylinux_2_28 in the EL8 test mode. |
| [`markers.py`](../tests/support/markers.py) | `UNEVALUABLE`: markers that `packaging` parses but cannot evaluate, for the builder's tests of each place that evaluates one. |
| [`no-wheels.txt`](../tests/support/no-wheels.txt) | Packages without a wheel, one Python's X.Y and its packages per line. |
| [`probe.py`](../tests/support/probe.py) | Runs inside the demo executable: exercises `importlib.resources`, `pkgutil`, `importlib.util`, `importlib.metadata`, `inspect` and `linecache`, and prints the results as JSON. |
| [`readme.py`](../tests/support/readme.py) | `quickstart()`: the README quickstart's shell lines. |
| [`signatures.py`](../tests/support/signatures.py) | `run(fsb)`: `_fsbpyr`'s signature functions, called inside the demo app on both launchers, and what they must give. |
| [`signing.py`](../tests/support/signing.py) | `script`, `new_key`, `sign`: the release script's keys and signatures, made per session so none is committed; `OpenSSL`, `named_key` and `use_openssl`: `_fsbpyr`'s four functions for tests from source, done by the openssl command on real keys through `fsbpyr.signing`'s two seams; `on_a_terminal`: a command on a pty. |
| [`releases.py`](../tests/support/releases.py) | Servers on 127.0.0.1 that log the requests: `FakeReleases`, the release downloads (`/vX.Y.Z/ASSET`, `/el8-vX.Y.Z/ASSET` for the glibc 2.28 stubs and their sums), and `FakeGitHub`, a release document at `/api` with its assets below `/files`, for `self update`; given a signer, each serves signed sums. |
| [`fakegithub.py`](../tests/support/fakegithub.py) | `FakeGitHub` for `release-sign.sh` and `audit.sh`: a fake `gh` and `curl` over files and a bare repository as the remote `hub`, able to do all an account can; only its `attest` makes an attestation. |
| [`requires.py`](../tests/support/requires.py) | What the tests need from the machine: `TOOLS`, `tool()`, the `requires_tools` mark `tools()`, the environment conditions and `unmet()`; as a module, `make test-prereqs` ([When the machine lacks something](#when-the-machine-lacks-something)). |
| [`rpms.py`](../tests/support/rpms.py) | Synthetic RPMs for the RPM reader's and sysroot's tests, with no network and no `rpm` tool: `rpm` (lead, signature, a header naming the payload's format and compressor, and an xz payload or given bytes), `cpio` (a newc archive), `header`, and the entries `file` (hard links by inode), `directory` and `symlink`. |
| [`sbom.py`](../tests/support/sbom.py) | Loads `tools/release/sbom.py` as a module (`tools/` is no package). |
| [`standin.py`](../tests/support/standin.py) | `StandIn`: the `_fsbpyr` built-in module in pure Python over the validating payload reader, with `Stream`; it must behave as `launcher/module.c` does, which the contract checks. |
| [`stderr.py`](../tests/support/stderr.py) | `noise()` and the few patterns an end-to-end check allows on an app's stderr (onnxruntime's `hostname`/`blkid` and host warnings, trace lines). |
| [`walk.py`](../tests/support/walk.py) | `run(fsb)`: uses every record of a payload through the `_fsbpyr` API (reads, closures, module tree, syncs) and lists what was refused; standard library only, since it is packaged into the executables it walks. |
| [`wheels.py`](../tests/support/wheels.py) | Reads `no-wheels.txt`; `missing()`, and `needs()`, the `needs_wheel` mark. |
| [`worker_venvs.py`](../tests/support/worker_venvs.py) | The hooks that give each pytest-xdist worker an environment of its own and stop a run when a worker is in anything else; a plugin of `tests/conftest.py`, so every parallel run has them. |

## Probes and the fuzzer

Each probe is a C program that `LauncherBuild.compile` links with the launcher sources it needs,
but `probes/no_context.c`, which `build_stub` links into a whole stub. Small build-time values
make cases reachable with small inputs, and `-Wl,--wrap=` replaces a system call the test
controls. Each probe's header comment lists its commands and switches.

| Program | Launcher sources | Build flags | Test |
|---|---|---|---|
| [`probes/digests.c`](../tests/probes/digests.c) | `integrity.c`, `parallel.c` | `-DFSB_PASS_LEAVES=3u`, `--wrap=pread,--wrap=SHA256_Update`, ASan, UBSan | `test_digests.py` |
| [`probes/due.c`](../tests/probes/due.c) | `due.c`, `way.c` | `--wrap=statvfs`, UBSan | `test_due.py` |
| [`probes/find_object.c`](../tests/probes/find_object.c) | `glibc.c` | `--wrap=_dl_find_object`, `--wrap=dlvsym` | `test_find_object.py` |
| [`probes/no_context.c`](../tests/probes/no_context.c) | the whole stub (`build_stub`) | `--wrap=ZSTD_createDCtx` | `test_read_failures.py` |
| [`probes/reads.c`](../tests/probes/reads.c) | `payload.c`, `integrity.c`, `parallel.c` | `--wrap=ZSTD_createDCtx,--wrap=ZSTD_decompress,--wrap=ZSTD_decompressStream,--wrap=lzma_stream_buffer_decode,--wrap=madvise`, ASan, UBSan | `test_read_failures.py` |
| [`probes/restore.c`](../tests/probes/restore.c) | `cache.c`, `way.c`, `payload.c`, `integrity.c`, `parallel.c` | `--wrap=renameat,--wrap=linkat,--wrap=pthread_create,--wrap=pwrite`, ASan, UBSan | `test_restore.py` |
| [`probes/sig.c`](../tests/probes/sig.c) | `sig.c`, `integrity.c`, `parallel.c` | `-DFSB_SIG_NO_PYTHON`, `--wrap=EVP_DigestVerify`, ASan, UBSan, LeakSanitizer | `test_signatures.py` |
| [`probes/slots.c`](../tests/probes/slots.c) | `slots.c` | `--wrap=geteuid`, UBSan | `test_slots.py` |
| [`probes/streams.c`](../tests/probes/streams.c) | `payload.c`, `integrity.c`, `parallel.c` | `-DFSB_STREAM_WINDOW` of 2 MiB, ASan, UBSan | `test_stream_windows.py` |
| [`probes/way.c`](../tests/probes/way.c) | `way.c` | `--wrap=readlinkat`, ASan, UBSan | `test_way_targets.py` |
| [`fuzz/sig_fuzz.c`](../tests/fuzz/sig_fuzz.c) | `sig.c`, `integrity.c`, `parallel.c` | `-DFSB_SIG_NO_PYTHON`, libFuzzer, ASan, UBSan | `test_fuzz.py` |
| [`fuzz/payload_fuzz.c`](../tests/fuzz/payload_fuzz.c) | `payload.c`, `integrity.c`, `parallel.c`, `cache.c`, `way.c` | `-fsanitize=fuzzer,address,undefined`, `-DFSB_STREAM_WINDOW` of one digest leaf | `test_fuzz.py` |

The fuzz harness visits every record of every table (up to 4,096 per table), runs the lookups and
the path check, and decodes every blob up to 16 MiB, whole and through a stream; whatever the
input, the launcher's code must refuse or read it, never crash. Its seed corpus is the intact
edge-case executable and every case of `edges.CASES`, built with `FAKE_RUNTIME`. The fuzzing run
uses `-rss_limit_mb=2048 -timeout=30 -max_len=1048576`. The test starts the harness through a
shell, since started directly it would take the test process's peak RSS as its own, which
libFuzzer compares with `-rss_limit_mb`.

## Fixture projects

| Project | What it is | Used by |
|---|---|---|
| `hello` | No dependencies. | `test_build.py`, `test_interrupted_build.py`, `test_multicall.py`, `test_portability.py`, `test_el8.py`, `test_signing.py`, `test_verify.py`, `tests/unit/test_test_modes.py` |
| `clicky` | click, rich and package data, with `uv.lock`, `pylock.toml` and `requirements.txt`. | `test_build.py` |
| `cext` | The project's own C extension, built from source (`setup.py`). | `test_build.py`, `test_el8.py` |
| `compat` | The compatibility matrix: click, typer, rich, httpx, requests, certifi, pydantic, jinja2, pyyaml, numpy, cryptography, orjson, tzdata, sqlalchemy, setuptools and more. | `test_compat.py`, `test_portability.py`, `tests/unit/builder/test_manifest.py` |
| `datafiles` | Packages that read data files next to their modules: botocore, pytz, babel, matplotlib. | `test_compat.py` |
| `escapes` | A module whose normal string has an invalid escape sequence: a compile warning of the project's own file. | `test_build.py` |
| `foreign` | Hand-written locks of other tools, as its README explains: `poetry2`, `poetry1`, `poetry1-unpackaged`, `poetry-legacy`, `pdm` and `pipenv` (a directory of scripts with a `Pipfile` and its lock). Each depends on click and rich, on colorama for Windows only, on packaging in the extra `fancy` and on iniconfig in a dev group. | `tests/unit/builder/test_foreign.py`, `tests/unit/builder/test_pipenv.py`, `test_build.py`, `test_scripts.py`, `test_pipfile_lock.py` |
| `heavy` | A light CLI with heavy native dependencies: numpy, orjson, cryptography, pydantic, onnxruntime. | `test_natives.py`, `test_portability.py` |
| `mp` | Multiprocessing start methods re-executing the binary; also a payload script started through `sys.executable` (`mp/scripts/`), `sys.argv[0]`, the restart idiom and a missing module. | `test_compat.py` |
| `multi` | Two console scripts, `multi-alpha` and `multi-beta`, built as one multi-command executable. | `test_multicall.py`, `test_el8_use_cases.py` |
| `native` | Two small native dependencies, orjson and markupsafe, for the VM smoke test of `tools/vm-smoke.py`. | No test file: the VM smoke jobs of [`full-suite.yml`](../.github/workflows/full-suite.yml), `vm-smoke` and `el8-vm-smoke`, build it and run it with `tools/vm-smoke.py` |
| `plugins` | A host and two plugin distributions: entry points and a PEP 420 namespace. | `test_corpus.py` |
| `scripts` | The layout of `uv init --no-package`: `main.py`, `utils.py` and a `helpers` package, no build system. | `test_scripts.py` |

`CONTRIBUTING.md` asks that every packaging fix (a module, file or library that a built
executable lacks or gets wrong) add a fixture project or corpus app whose test fails without the
fix.
