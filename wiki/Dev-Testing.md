# Testing

This page describes fsbpyr's test suite in [`tests/`](../tests):
how it is laid out, what each layer and each test file checks and how, the support code the
tests share, the real-world corpus, and the logic that runs through the suite: pytest's
configuration, the environment variables the tests read, what a test does when the machine
lacks a tool or setting (no test skips), the tests of builds for glibc 2.28 (EL8) and the EL8
test mode, the differential comparison with a venv, the determinism checks, the README's
quickstart run as a test, and coverage. Then come the `make` targets that run the tests, the
page-size smoke test in a virtual machine, and a reference of every test file, support module,
probe program and fixture project. Build, payload, runtime and boot modules are described on
their own pages ([Dev-Build-Modules](Dev-Build-Modules.md),
[Dev-Payload-Modules](Dev-Payload-Modules.md), [Dev-Runtime-Modules](Dev-Runtime-Modules.md),
[Dev-Boot-Modules](Dev-Boot-Modules.md)); the launcher on [Dev-Launcher](Dev-Launcher.md).

## Layout

| Directory | What it holds |
|---|---|
| [`tests/unit`](../tests/unit) | Tests of fsbpyr's modules, mostly in the test process. The top level covers the CLI and its documentation, inspection, bytecode, the version rule, dispatch, self update, targets, git state, the text helpers, Poetry 1 projects, the tools of a tool binary, the SBOM generator, `tools/release/el8-stub.py`, `tools/vm-smoke.py`, the test modes, the rule that no test skips, and some of the test support code itself. |
| [`tests/unit/boot`](../tests/unit/boot) | The boot code (`_fsbpyr_boot` and `_fsbpyr_rt`) run in-process, most of it against a pure-Python stand-in for the launcher's `_fsbpyr` module. |
| [`tests/unit/builder`](../tests/unit/builder) | The build pipeline: project discovery, commands, scripts and tools, the locks of other tools, inferred dependencies, the static import check, staging commands, classification, native closures, manifest, compiler pool, the build orchestrator. |
| [`tests/unit/payload`](../tests/unit/payload) | The payload writer and reader: round trips, validation, integrity digest, blocks, xz, zstd pieces, spill and streaming writer. |
| [`tests/unit/runtime`](../tests/unit/runtime) | The runtime build: PBS fetch, registry, sysroots (the RPM reader of the EL8 sysroot among them), toolchain and link plan, stdlib selection, licence notices, the glibc 2.28 stub of a release, the tool's cache collector. |
| [`tests/integration`](../tests/integration) | Tests that need real tools or real data but no built executable: the real stdlib, a C compiler, the cached PBS distribution, the installer under dash, bash and busybox sh, throwaway git repositories, the shell of the release scripts and of the EL8 workflow against a fake `gh`. |
| [`tests/e2e`](../tests/e2e) | Real executables built from a real runtime and run as subprocesses; C probe programs and the fuzzer built from the launcher's sources; the corpus and the portability matrix. |
| [`tests/probes`](../tests/probes) | Small C programs linked with launcher sources, each driving one part of the launcher for one end-to-end test. |
| [`tests/fuzz`](../tests/fuzz) | `payload_fuzz.c`, the libFuzzer harness for the launcher's payload parsing. |
| [`tests/corpus`](../tests/corpus) | The real-world corpus: one small application per risk class, built and compared with its venv. |
| [`tests/fixtures/projects`](../tests/fixtures/projects) | Projects the tests build: `hello`, `clicky`, `cext`, `compat`, `datafiles`, `escapes`, `foreign`, `heavy`, `mp`, `multi`, `native`, `plugins`, `scripts` (see [Fixture projects](#fixture-projects)). |
| [`tests/support`](../tests/support) | Code the tests share: the `_fsbpyr` stand-in and its contract, the containment of app runs, the differential runner, the corpus reader, the edge-case table, what the tests need from the machine, and more (see [Support modules](#support-modules)). |

`tests`, `tests/unit` and its subdirectories, `tests/integration`, `tests/e2e` and
`tests/support` are packages, so tests import shared code relatively
(`from ..support import corpus`). The corpus apps carry test files of their own, which their
built apps run, so [`tests/conftest.py`](../tests/conftest.py)
keeps pytest out of them (`collect_ignore_glob = ["corpus/*"]`).

![The test layers: make test-ci runs tests/unit, tests/integration and thirteen files of tests/e2e with the release launcher; make test and make test-container run unit, integration, e2e and the probes and fuzzer (make test also the portability tests, which need podman; make test-container leaves out the tests that need podman); make test-corpus runs the corpus; make test-portability the portability tests; make test-el8-stub runs sixteen end-to-end files that run the stub and tests/unit/test_test_modes.py in the EL8 test mode, without the tests that need podman, then tests/e2e/test_el8.py; make test-el8-portability runs the portability tests in the EL8 test mode; make test-vm runs tools/vm-smoke.py, no pytest test. Unit tests exercise the builder, payload and runtime build in-process and the boot code against the _fsbpyr stand-in; integration tests the Python code, the pinned PBS distribution, the shell scripts and workflow steps and the launcher's format.h; e2e tests fsbpyr build and the built executables with the release and ASan/UBSan launchers, and executables with the glibc 2.28 stub, which is linked against pinned Rocky Linux 8.10 RPMs; probes and fuzzer the launcher's C code; portability executables in older distributions and, for apps built for glibc 2.28, in rockylinux:8 and almalinux:8; the corpus real apps against their venv; vm-smoke.py aarch64 apps in a QEMU VM with 64 KiB pages, from a pinned kernel and root file systems.](images/testing-layers.svg)

## How the layers work

**Unit tests** run fsbpyr's Python modules in the test process. Most unit tests that need a
whole executable build one with `build_executable` and `FAKE_RUNTIME`, a runtime record defined in
[`tests/unit/boot/conftest.py`](../tests/unit/boot/conftest.py)
whose stub is a few bytes (`b"\x7fELF-fake"`): the file is a valid payload behind a stub that
cannot run, which the Python reader reads. The boot code runs against
[`StandIn`](../tests/support/standin.py), a
pure-Python `_fsbpyr` module over the payload reader. Its cache sync is
[`tests/support/cache.py`](../tests/support/cache.py),
a step-by-step mirror of `launcher/cache.c`. The payload tests build specs with `make_spec` from
[`tests/unit/payload/conftest.py`](../tests/unit/payload/conftest.py),
a small spec that uses every table and reference type.

**The C launcher and its Python mirror are held to one contract.**
[`tests/support/contract.py`](../tests/support/contract.py)
is a battery of calls on the `_fsbpyr` API (lookups, listings, reads, streams and cache syncs,
with damaged, missing and refused inputs) that returns its results as JSON. The same function
runs inside a built executable against the C module and in-process against the stand-in, and
`tests/e2e/test_imports.py` requires the two results to be equal. In the same way, one table of
format edge cases
([`tests/support/edges.py`](../tests/support/edges.py))
is run against the Python reader (`tests/unit/payload/test_edges.py`), the launcher
(`tests/e2e/test_edges.py`, release and sanitized) and the launcher's parsing code under
libFuzzer (`tests/e2e/test_fuzz.py`).

**Integration tests** need something real but no executable: the real standard library, a C
compiler (`cc`) for a probe of `launcher/format.h`, the pinned PBS distributions (from
`FSBPYR_TEST_DOWNLOADS`, else downloaded from python-build-standalone's releases),
`dash`, `bash` and `busybox sh` for the installer, `openssl` for the installer server's
certificate, `git` for the release scripts. Servers are local: an HTTPS server on 127.0.0.1 for
the installer, a fake `gh` that logs its calls for the release and watch scripts and for the
EL8 workflow's publish steps. Nothing reaches fsbpyr's GitHub repository.

**End-to-end tests** build real executables from a real runtime and run them. The `runtime`
fixture in [`tests/e2e/conftest.py`](../tests/e2e/conftest.py)
provides the runtime for the Python the tests run on (`TARGET_PYTHON`), once per session (and
once more for the sanitized launcher). It takes the one in the session's cache if there is one.
Otherwise, with `FSBPYR_TEST_DOWNLOADS` set, it copies the runtime kept in that directory into
the session's cache, building it there first from the kept downloads if it is missing; without
the variable it builds the runtime in the session's cache, downloading what it needs. Building
needs clang and lld 23 or newer; without them the test fails with one line that names what is
missing (see [When the machine lacks something](#when-the-machine-lacks-something)). Tests marked
`sanitized` that use the runtime run twice, with the release launcher and with its ASan/UBSan
build. A second fixture, `el8_runtime`, gives the runtime of the same Python linked for glibc
2.28 in the same way (release launcher only): its stub is the one `fsbpyr build --glibc 2.28`
puts into apps, linked against Rocky Linux 8.10's sysroot (see
[Builds for glibc 2.28](#builds-for-glibc-228-el8)). In the EL8 test mode `runtime` is that
runtime too.

The `launcher_build` fixture gives the runtime build's clang, a copy of the launcher's sources
and the runtime's own `libzstd.a`, `liblzma.a` and `libcrypto.a`. `LauncherBuild.compile`
links a probe program from
[`tests/probes`](../tests/probes) or the fuzz
harness with the launcher sources it names, as `clang -std=c11 -D_GNU_SOURCE -g -O1` plus the
test's flags. `app_content` turns a `{path: source}` dictionary of pure-Python files into an
app's modules and files, and the `build_app` fixture builds such an app into an executable.
Most files that build whole projects, directories of scripts or single scripts call
`fsbpyr build` in-process (`fsbpyr.cli.main`) on a copy in `tmp_path` and run what it wrote
through `contain.run` (see [Containment](#containment-of-app-runs)).

**The corpus** builds real applications with their real dependencies from PyPI and compares each
command's output with the same command run from the app's venv (see [The corpus](#the-corpus)).

## Configuration and conftests

`[tool.pytest.ini_options]` in [`pyproject.toml`](../pyproject.toml):

- `testpaths = ["tests"]`; `addopts = ["--strict-markers", "--strict-config", "-ra"]`: an
  unknown marker or configuration key is an error, and the summary lists every test that did
  not pass.
- `tmp_path_retention_policy = "failed"`: a run builds gigabytes of executables, so only the
  temporary directories of failed tests are kept.
- `empty_parameter_set_mark = "fail_at_collect"`: a parametrization left empty is a collection
  error, not a hidden skip.
- Six markers:
  - `slow`: takes more than a few seconds. Every end-to-end file, the PBS integration tests and
    the two bytecode-determinism tests carry it. No `make` target selects by it; `-m "not slow"`
    leaves those tests out.
  - `sanitized`: the test also runs with the ASan/UBSan launcher.
    `pytest_generate_tests` in the end-to-end conftest parametrizes the `runtime` fixture with
    `release` and `sanitized` for such tests when they use it, so their ids carry `release` or
    `sanitized`; in the EL8 test mode with `release` only, since the sanitized launcher is the
    default sysroot's. Four files use it: `test_cache_root.py`, `test_edges.py`,
    `test_imports.py` and `test_runtime_app.py`.
  - `version_rule`: the test runs the Python version rule as it is. `test_pyversion.py`, the
    unit and end-to-end `test_dispatch.py`, two tests of `test_pipenv.py` and two of
    `test_cli.py` carry it.
  - `requires_tools`: the test fails, before it runs, without these tools on `PATH`; set with
    `requires.tools(...)` (see
    [When the machine lacks something](#when-the-machine-lacks-something)).
  - `needs_wheel`: the test is deselected where `no-wheels.txt` lists no wheel of the package;
    set with `wheels.needs(package)`.
  - `podman`: the test requires podman. `tests/conftest.py` sets it on every test whose
    `requires_tools` names podman, so that `-m "not podman"` leaves those tests out where there
    is none.

[`tests/conftest.py`](../tests/conftest.py)
applies to every test:

- `_no_collection` (session, autouse) sets `FSBPYR_GC=0`: no cache collection runs unless a
  test asks for one, since the tool's daily check and the launcher's background runs would act
  on the caches the tests use.
- `_build_for_own_python` (session, autouse) wraps `pyversion.resolve` so that a build in a test
  is for the Python the tests run on, as `--python` would say; the fixture projects'
  `requires-python = ">=3.12"` would otherwise pick 3.12. `_version_rule` undoes the wrapping for
  tests marked `version_rule`.
- `_build_for_baseline` (session, autouse) does nothing unless `FSBPYR_TEST_GLIBC` selects the
  EL8 test mode. In the mode it wraps `config.load_config` so that a build in a test is
  `--glibc 2.28` unless the build itself passes a glibc: the project's `glibc` setting and the
  default give way (see [The EL8 test mode](#the-el8-test-mode)).
- Hypothesis runs without a per-example deadline: the default profile is registered with
  `deadline=None`, and a profile `ci` (`derandomize=True`, no deadline) is loaded when `CI` is
  set, so the same examples run every time there; locally the examples stay random.
- `pytest_configure` refuses an `FSBPYR_TEST_GLIBC` that is no glibc baseline. Under
  pytest-xdist with `FSBPYR_TEST_DOWNLOADS` set, the controller downloads the runtime's pinned
  files (the sysroots of both glibc baselines included) and builds the runtimes into that
  directory before the workers start, so that each worker does not fetch and build them for its
  own cache: release and sanitized, and the one linked for glibc 2.28 that the EL8 tests use; in
  the EL8 test mode only the glibc 2.28 one. Without a compiler it stops the run with a usage
  error, `missing tool: ...`.
- `pytest_runtest_setup` fails a test marked `requires_tools` before its fixtures build
  anything, when one of the tools is not on `PATH`.
- `pytest_collection_modifyitems` marks every test that requires podman `podman`, and
  deselects the tests marked `needs_wheel` whose package has no wheel for their Python
  (`no-wheels.txt`); the summary counts them as deselected.
- `pytest_runtest_logreport` records every skipped test, an xfail included, with its reason;
  `pytest_sessionfinish` prints them and fails the run (see
  [When the machine lacks something](#when-the-machine-lacks-something)).
- At the end of a run, `pytest_sessionfinish` removes from uv's cache the builds of local
  projects made since the run started (path and editable sources, wheels installed by URL) with
  the unpacked wheels they link to. The tests build their fixture projects and fsbpyr from new
  temporary copies every time, and uv keeps every build; what uv downloaded from an index stays.

[`tests/e2e/conftest.py`](../tests/e2e/conftest.py)
adds:

- `_private_cache` (session, autouse) sets `FSBPYR_CACHE_DIR` to a temporary directory, so
  executables run by the tests never write the user's cache.
- `runtime`, `el8_runtime`, `launcher_build`, `app_content` and `build_app`, described above.
- `pytest_runtest_logreport` collects the corpus results (also in pytest-xdist's controller), and
  `pytest_terminal_summary` prints a `corpus` section and a `runtime downloads` section that says
  `seeded` or `fetched` for each pinned file.

[`tests/unit/boot/conftest.py`](../tests/unit/boot/conftest.py)
defines `FAKE_RUNTIME`, `BOOT_DIR` and the `boot` fixture: it sets `FSBPYR_CACHE_DIR` below
`tmp_path`, puts a `StandIn` for the module's `executable` into `sys.modules["_fsbpyr"]`, puts
`src/fsbpyr/boot` first on `sys.path`, loads `_fsbpyr_boot.py` from the source tree, and
removes the `_fsbpyr_rt` modules afterwards.
[`tests/unit/payload/conftest.py`](../tests/unit/payload/conftest.py)
defines `make_spec` and the `spec` fixture.

## Environment variables

Variables that change how the tests run:

| Variable | Read by | Effect |
|---|---|---|
| `FSBPYR_TEST_DOWNLOADS` | `tests/conftest.py`, `tests/e2e/conftest.py`, `tests/e2e/test_dispatch.py`, `tests/e2e/test_baseline.py`, `tests/support/downloads.py` (the PBS distributions of the integration tests), `tools/vm-smoke.py` | A directory that keeps the runtime's pinned downloads (CPython and the sysroots' packages) and the runtimes built from them between sessions ([`downloads.py`](../tests/support/downloads.py)), and the kernels and root file systems of the [page-size smoke test](#the-page-size-smoke-test). The `make` targets set it to `$(TESTTMP)/fsbpyr-test-downloads` unless it is set. |
| `FSBPYR_CORPUS_APPS` | `tests/support/corpus.py` | A comma-separated subset of the corpus apps for `test_corpus.py` to run; a name that is no corpus app is an error. Unset or empty: every app. |
| `FSBPYR_LLVM_COV_DIR` | `tests/e2e/conftest.py` | `LauncherBuild.compile` builds the programs it links (the probes and the fuzz harness) with llvm-cov instrumentation (`-fprofile-instr-generate -fcoverage-mapping`) and copies each program there, for a launcher coverage report; `LLVM_PROFILE_FILE` says where the runs write their profiles. |
| `FSBPYR_FUZZ_SECONDS` | `tests/e2e/test_fuzz.py` | How long the fuzzing run explores; 30 seconds when unset. |
| `FSBPYR_FUZZ_CORPUS` | `tests/e2e/test_fuzz.py` | Directory that keeps the inputs a fuzzing run finds, so a later run starts from them; a temporary directory when unset. |
| `FSBPYR_LARGE_SIZE` | `tests/e2e/test_large.py` | Size of the large file; 5 GiB when unset (the module docstring names `1099511627776`, 1 TB, for a run on demand). |
| `FSBPYR_PORTABILITY_APPS` | `tests/e2e/test_portability.py` | A directory with the four apps already built (`make test-portability` makes it with `tools/portability-apps.sh`); they are used as they are and no runtime is needed. |
| `FSBPYR_TEST_GLIBC` | `tests/conftest.py`, `tests/support/baseline.py` | The glibc baseline the tests build for: `2.28` selects [the EL8 test mode](#the-el8-test-mode); a value that is no baseline (2.34, 2.28) is refused before any test runs. Unset or `2.34`: the default baseline. |
| `CI` | `tests/conftest.py` | Loads the derandomized Hypothesis profile. |

The tests also set variables for the code they run: `FSBPYR_CACHE_DIR` (a private cache for
almost every executable a test starts), `FSBPYR_GC`, `FSBPYR_SELFTEST`, `FSBPYR_TRACE`,
`FSBPYR_MAX_INSTANCES`, `FSBPYR_INSTALL_LINKS`, `PYTHONUNBUFFERED`, `JOURNAL_STREAM` and others
the launcher and boot code read, and `FSBPYR_RELEASE_DOWNLOADS`, which points the downloads of
release assets (a variant of another Python, a glibc 2.28 stub) at a `FakeReleases` server.
`contain.run` tags every process it starts with
`FSBPYR_TEST_CONTAINED=<token>`. The C probes read `PROBE_*` switches (`PROBE_FREE`,
`PROBE_RELEASE`, `PROBE_OUT`, `PROBE_SESSION`, `PROBE_OLD_GLIBC`, `PROBE_NO_LINK`, `PROBE_DECOY`,
`PROBE_EUID`), each described in its probe's header comment.

`make` variables that shape a run (see [`Makefile`](../Makefile)):
`JOBS` (pytest-xdist processes, default 8), `TESTTMP` (where each run's temporary directory
goes, default `~/tmp`, chosen because `/tmp` is often a tmpfs mounted `nosuid`, where a set-gid
start cannot be tested), `TEST_TASKS` and `TEST_MEMORY` (the run's cgroup limits, see
[Containment](#containment-of-app-runs)), `PYTHON` (another Python's environment `.venv-3.X`;
every test target but `test-vm` takes it), `FSBPYR_LLVM23=0` (leave `PATH` alone instead of
putting the pinned LLVM 23 first) and `VM_ARGS` (the arguments of `tools/vm-smoke.py` for
`make test-vm`). `PYTEST_ADDOPTS` passes pytest options through, as the corpus README does
with `PYTEST_ADDOPTS="-k <app>"`.

## When the machine lacks something

No test skips. A test whose tool is missing, or whose environment is not as it needs, fails with
one line that names what to install or change and points at `CONTRIBUTING.md`
([`tests/support/requires.py`](../tests/support/requires.py)):

```
missing tool: strace (Debian package: strace): traces the system calls of a start; see CONTRIBUTING.md, Running the tests
unmet environment: <condition>: <change>; see CONTRIBUTING.md, Running the tests
```

A tool is asked for with the `requires_tools` marker (`requires.tools(...)` on a module or a
test), which `tests/conftest.py` checks before the test's fixtures build anything, or with
`requires.tool(name)` where only a fixture or one branch needs it; the first of the tool's
commands on `PATH` is used. Without clang and lld 23 or newer, the `runtime` fixture fails with
`missing tool:` and the `ToolchainError`'s message. The `make` targets put LLVM 23 first on
`PATH` (fetched once by `tools/bootstrap.sh --llvm23`); run directly, pytest fails these tests
without a clang 23 on `PATH`. The tools, as `TOOLS` in `requires.py` lists them:

| Tool | Debian package | What needs it |
|---|---|---|
| `uv` | none: the uv that `tools/release/uv.version` names | runs the tests and builds their fixture projects |
| `git` | git | builds record the commit; tests read projects from repositories |
| `make` | make | a test runs the Makefile |
| `cc` (or `gcc`) | gcc | compiles the tests' C programs |
| `clang` (and lld) 23 | none: `make` puts fsbpyr's LLVM 23 first on `PATH` | links the runtime, the launcher and its test programs |
| `llvm-objdump` (`llvm-objdump-22` first) | llvm-22 | reads the executables' sections and symbols |
| `readelf`, `objdump` | binutils | read and disassemble the stub |
| `unshare`, `prlimit` | util-linux | run apps in namespaces and as PID 1; cap the tasks of contained runs |
| `podman` | podman, uidmap, passt | runs the portability images and maps a second range of user ids |
| `strace` | strace | traces the system calls of a start |
| `groff` | groff-base | renders the man page |
| `zstd`, `tar` | zstd, tar | unpack the LLVM tarball that `bootstrap.sh` fetches; pack the one its tests make |
| `xz`, `curl`, `wget`, `openssl` | xz-utils, curl, wget, openssl | `install.sh` and its tests: the release asset, the downloads, the TLS certificate |
| `dash`, `bash`, `busybox` | dash, bash, busybox | the shells `install.sh`'s tests run it under; the completion and release-script tests run `bash` or `dash` too |
| `ls` | coreutils | a host binary whose glibc requirement a test reads |

An environment condition is a function of `requires.py` that the test calls:

| Function | Fails unless | Called by |
|---|---|---|
| `non_root` | the tests run as a normal user | `e2e/test_cache_root.py`, `unit/boot/test_way.py` |
| `user_namespaces` | `unshare -r true` works | `e2e/test_cache_root.py`, `unit/boot/test_way.py` |
| `rootless_podman` | `podman unshare true` works (ranges in `/etc/subuid` and `/etc/subgid`) | `e2e/test_cache_root.py`, `unit/boot/test_way.py` |
| `supplementary_group` | the user has a group besides its effective one | `e2e/test_runtime_app.py`, `unit/boot/test_way.py` |
| `set_id_bits` | set-id bits apply: no `nosuid` mount, no `no_new_privs` | `e2e/test_runtime_app.py` |
| `foreign_directory` | a searchable directory of a user who is neither root nor the tester is under `/var/lib`, `/etc`, `/var/spool` or `/home` | `e2e/test_cache_root.py` |
| `root_directory_in_tmp` | a directory in `/tmp` is root's | `e2e/test_cache_root.py`, `unit/boot/test_way.py` |
| `uv_python` | uv has that Python installed (the tests never let it download one) | `e2e/test_dispatch.py` |
| `glibc_host` | the host's libc is glibc | `unit/builder/test_natives.py` |
| `enough_free_disk` | 10% of the file system is free, else a collection is due hourly | `e2e/test_gc.py` |

`e2e/test_pid1.py` (neither `unshare --user --map-root-user --pid` nor podman with `debian:13`
works), `unit/test_cli_docs.py` (no fsbpyr console script beside the interpreter) and
`unit/boot/test_way.py` (a directory that cannot be given to the group, a `/` that is not root's)
call `requires.unmet` themselves. `tools/test-setup.sh` makes the user, group and directories in
the Containerfile's `test` stage.

`make test-prereqs` runs `python -m tests.support.requires`, which lists everything the full
suite lacks on this machine at once: the missing tools' Debian packages as one `apt-get install`
line, uv and clang/lld 23 (judged by the toolchain search for 23) on lines of their own, then
every condition above that does not hold, with the Pythons 3.12 to 3.15 installed with uv and
the set-id and free-space checks made for `TMPDIR`. It exits 1 when it lists anything.

### Cases that do not apply

A case that does not apply to the Python, the target or the mode is not part of the run:

- A package with no wheel for the Python (listed in
  [`no-wheels.txt`](../tests/support/no-wheels.txt)): a parametrized test does not generate the
  case (the corpus apps through `corpus.runnable`, `test_compat.py`'s matrix checks,
  `test_portability.py`'s `heavy` apps and compat checks); any other test is marked
  `needs_wheel` and deselected (`test_natives.py`, `test_portability.py`'s two
  `FSBPYR_SELFTEST=natives` tests).
- podman: `-m "not podman"` leaves out the tests marked `podman` where there is none:
  `test_portability.py`, `test_cache_root.py`'s symlink of another user made with
  `podman unshare` and `test_way.py`'s mapped overflow uid. `make test-container` and
  `make test-el8-stub` run with it.
- `test_portability.py`'s `test_el8_apps_run_on_el8` exists outside the EL8 test mode only, whose
  matrix runs every app on the EL8 images, and, with a prebuilt set of apps, only when the set
  has `hello-el8`.
- A case that behaves otherwise asserts that behaviour: 3.12's functional `importlib.resources`
  API, which takes one resource name (`test_boot.py`); root, who ignores file modes
  (`test_native.py`, `test_collect.py`, `test_selfupdate.py`, `test_install_links.py`); one usable
  core, with no helper thread, and a file system that cannot link unnamed files, where files are
  renamed into place (`test_restore.py`).

The one place where a test fails by design is user-mode QEMU (aarch64 checks on an x86-64 host):
`/proc/<pid>/exe` names the emulator for every process, so `test_mp.py`'s
`test_children_of_any_other_program_are_refused` fails there (`CONTRIBUTING.md`).

### A skip fails the run

Whatever makes a test skip or xfail (a plugin, a helper, a marker), `pytest_runtest_logreport`
in [`tests/conftest.py`](../tests/conftest.py) records it, and `pytest_sessionfinish` prints each
under "skipped tests: skipping is not allowed in this suite (CONTRIBUTING.md)" and sets the exit
status to "tests failed". An empty parametrization is a collection error
(`empty_parameter_set_mark`).

`tests/unit/test_no_skips.py` reads every Python file under `tests/` but the corpus with `ast` and
fails on any use of `pytest.skip`, `pytest.xfail`, `pytest.importorskip`, the `skip`, `skipif`
and `xfail` marks, `unittest`'s skip decorators and `expectedFailure`, `skipTest`, or an import
of one of them from `pytest` or `unittest`.
`tests/unit/test_requires.py` tests the rest with pytester, on a suite of its own under
`tests/conftest.py`: the one-line failures, a marked test failing before its fixtures, the
`podman` mark and `-m "not podman"`, deselection by `needs_wheel`, a skip or xfail failing the
run; it also requires `CONTRIBUTING.md` to name every tool and package, and `tools/test-setup.sh`
and the Containerfile to install every package but podman's and those that Debian or gcc bring
along.

## Builds for glibc 2.28 (EL8)

`fsbpyr build --glibc 2.28`, or `glibc = "2.28"` in `[tool.fsbpyr]`, builds an app for glibc
2.28: its stub is linked against a sysroot read from pinned Rocky Linux 8.10 RPMs (`glibc`,
`glibc-devel`, `glibc-headers`, `kernel-headers`, `libgcc` and `gcc`, as
`tests/unit/test_targets.py` checks), and its wheels are chosen for manylinux_2_28. A runtime's
id covers its glibc baseline, and a stub records the id it was linked as. A released fsbpyr
pairs its own stdlib with the glibc 2.28 stub of its release: a file placed beside the
executable (for a build handed to the fsbpyr of another Python, beside the fsbpyr that handed it
first), the one in the cache, kept under the runtime id it carries, or a download from the
companion release `el8-vX.Y.Z`, checked against its `SHA256SUMS-glibc2.28` (never with
`--offline`); whatever the source, the stub must record the runtime id that fsbpyr expects. The
code is described on [Dev-Runtime-Modules](Dev-Runtime-Modules.md) and
[Dev-Build-Modules](Dev-Build-Modules.md).

### The EL8 tests

Outside the EL8 test mode, these files test EL8 builds beside default ones. The end-to-end
tests among them that need the glibc 2.28 runtime take the `el8_runtime` fixture, which comes
like `runtime` from the session's cache, else from the files kept in `FSBPYR_TEST_DOWNLOADS`,
else from downloads, and fails the test without clang and lld 23.

- Unit tests: `runtime/test_rpm.py` (the RPM reader of the EL8 sysroot),
  `runtime/test_stubs.py` (where a release's stub comes from, and its runtime id),
  `test_el8_stub_tool.py` (`tools/release/el8-stub.py`), and the EL8 cases of
  `test_targets.py`, `runtime/test_sysroot.py`, `runtime/test_fetch_and_build.py`,
  `runtime/test_registry.py`, `builder/test_project_and_staging.py`,
  `builder/test_system_report.py`, `builder/test_build.py`, `test_cli.py`, `test_dispatch.py`,
  `test_inspect.py`, `test_selfupdate.py`, `test_vcs.py` and `test_downloads.py`.
- Integration tests: `test_el8_workflow.py` (the shape of `.github/workflows/el8.yml` and its
  publish steps against a fake `gh`), `test_check_release_el8.py` (the release check's EL8
  steps), and the tests of `test_release.py` about the companion release and its tag.
- End-to-end tests: `test_el8.py` (EL8 builds on real runtimes, and a release fsbpyr that
  downloads or finds its stub), `test_el8_use_cases.py` (a directory of scripts, a single script
  and a project of several commands built for EL8), `test_baseline.py` (each baseline's stub and
  sysroot), and `test_portability.py`'s `test_el8_apps_run_on_el8` (`rockylinux:8` and
  `almalinux:8`).

[Test files](#test-files) says what each checks.

### The EL8 test mode

`FSBPYR_TEST_GLIBC=2.28`
([`tests/support/baseline.py`](../tests/support/baseline.py))
runs tests that build and run executables with the glibc 2.28 stub in place of the default
one, since the stub is what differs between an EL8 build and a default one (the Makefile's
comment). In the mode:

- the end-to-end `runtime` fixture is the glibc 2.28 runtime, and tests marked `sanitized` run
  with the release launcher only;
- every `fsbpyr build` of a test is `--glibc 2.28` unless the build passes another `--glibc`
  (`_build_for_baseline` in `tests/conftest.py`);
- the portability matrix adds `rockylinux:8` and `almalinux:8`, its apps are glibc 2.28 builds
  (the tests build them so unless `FSBPYR_PORTABILITY_APPS` names prebuilt ones), and each must
  report glibc 2.28 with no object needing more, and `test_el8_apps_run_on_el8` is not
  generated;
- the corpus test counts only wheels usable at manylinux_2_28 (`tests/support/locks.py`): an
  app whose lock has a package without one is not run (`corpus.runnable` leaves it out);
- under pytest-xdist with `FSBPYR_TEST_DOWNLOADS` set, only the glibc 2.28 runtime is built
  before the workers start.

`make test-el8-stub` runs these files in the mode, in `JOBS` processes, with `-m "not podman"`:
`test_runtime_app.py`, `test_imports.py`, `test_stub_hardening.py`, `test_edges.py`,
`test_cache_root.py`, `test_gc.py`, `test_slots.py`, `test_due.py`, `test_restore.py`,
`test_find_object.py`, `test_streaming.py`, `test_stream_windows.py`, `test_unbuffered.py`,
`test_digests.py`, `test_compat.py` and `test_natives.py` of `tests/e2e` (the launcher, the
`_fsbpyr` contract, and apps with natives from manylinux_2_28 wheels), and
`tests/unit/test_test_modes.py`, which checks the mode itself. It then runs
`tests/e2e/test_el8.py` outside the mode, since that file compares an EL8 build with a default
one and needs both runtimes. `make test-el8-portability` runs `test_portability.py` in the mode;
the tests build the apps with the glibc 2.28 runtime. `make test-el8` runs both.
`test_el8_use_cases.py` is in neither; `make test` runs it. The Makefile names
[`el8.yml`](../.github/workflows/el8.yml) as
the workflow that runs the mode and `full-suite.yml` as one that runs none of it;
`test_corpus.py`'s docstring adds that `el8.yml` runs a few corpus apps
(`FSBPYR_CORPUS_APPS`) in the mode.

## The corpus

[`tests/corpus`](../tests/corpus) holds 65
applications at v0.28.0 (counted as its directories). Each is a small project that uses real
libraries the way real code does, one per risk class. The apps, and the risk each covers, are
listed on [Dev-Compatibility](Dev-Compatibility.md).

### An app's files

An app is a directory `tests/corpus/<app>/` with:

- `pyproject.toml`: name `corpus-<app>`, the console script `corpus-<app>` (the test calls the
  script by the directory's name), `requires-python = ">=3.12"`, and a description naming the
  risk it covers. torch and torchvision come from PyTorch's CPU index.
- `uv.lock`, made with `uv lock` in the directory.
- `src/corpus_<package>/`: `__init__.py`, `cli.py` with `main`, and the rest of the app.
- `commands.toml`: what to run.
- `work/` (optional): input files the commands name.

### The rules

[`tests/corpus/README.md`](../tests/corpus/README.md)
sets the rules for an app, in short:

- The same output on every run, in the venv and in the executable: seeded random generators,
  rounded floating-point results, sorted output, no timestamps, process ids, ports, addresses,
  temporary paths or `0x...` reprs, no progress bars.
- Every write goes into a temporary directory of the run, with libraries' caches and
  configuration pointed there before they are imported. Nothing is written into the project
  directory. The test gives each run an empty home of its own.
- No network, except servers the app starts on 127.0.0.1, port 0, and stops before it exits; no
  downloads.
- Quick and small: each command well under a minute on one core.
- The libraries are used as their users use them, through their public API, with nothing
  fsbpyr-specific: finding where fsbpyr fails is the point.
- One line per check, starting with a short label. Warnings are output too; a warning that
  depends on the environment is not allowed.

When a run fails or differs, the corpus README says what to do: fix the app when it is at fault; add a
`[tool.fsbpyr]` setting (`fs-packages`, `interpreter-reexec`, see [User-Settings](User-Settings.md))
with a comment saying what failed without it when fsbpyr needs one; record a known failure when
no setting helps.

### commands.toml

[`tests/support/corpus.py`](../tests/support/corpus.py)
reads it with `tomllib`:

| Key | Read by | Meaning |
|---|---|---|
| `expect` | `expected()` | Text one command's stdout must contain, so that two runs failing alike do not pass. |
| `[[command]]` `argv` | `commands()` | The arguments, run from the project directory. |
| `[[command]]` `env` | `commands()` | Optional variables added to both runs' environment. |
| `[[command]]` `stdin` | `commands()` | Optional text both runs read on stdin; without it, no input. |
| `known-failure` | `known_failure()` | One line saying what fails: the test expects the failure. A table `{reason = "...", pythons = ["3.14", ...]}` limits it to those Pythons. |
| `sdists` | `sdists()` | Pure-Python packages that publish only an sdist, which the app builds from it (the hydra app lists `antlr4-python3-runtime`). |

`settings()` reads the app's `[tool.fsbpyr]` table from its `pyproject.toml`. What checks the
files: `test_every_corpus_app_says_what_its_output_contains` requires a non-empty `expect` in
every app (and at least 8 apps); `tests/unit/test_corpus_locks.py` checks every app's lock (see
below); `tests/unit/test_cli_docs.py` requires `docs/compatibility.md`'s corpus table to name
exactly the corpus directories.

### How an app is built and run

`test_corpus_app_matches_its_venv` in
[`tests/e2e/test_corpus.py`](../tests/e2e/test_corpus.py)
is parametrized over the corpus directories, or over those `FSBPYR_CORPUS_APPS` names
(`corpus.selected()`), but the apps whose lock installs a package, other than their `sdists`,
without a usable wheel for this Python on this machine (`corpus.runnable`): those are not run
there. For one app it:

1. Reads `known-failure`: when it names this Python, the steps below are expected to fail (see
   [Wheels, sdists and known failures](#wheels-sdists-and-known-failures)).
2. Makes a temporary directory for the app, with a build cache whose `runtimes` is a symlink to
   the session's runtimes, and sets `FSBPYR_CACHE_DIR` to it for the build, so that what the
   build's smoke test extracts goes away with the rest.
3. Copies the project, makes its venv with `make_venv` (`uv sync --locked --no-dev
   --no-default-groups --no-editable --python X.Y`) and builds it in-process with
   `fsbpyr build <project> -o <tmp>/bin/corpus-<app>`, using whatever `[tool.fsbpyr]` table the
   app has.
4. Runs every command twice through `run_both` (see [Differential testing](#differential-testing)):
   the venv's console script and the executable, from the project directory, with
   `FSBPYR_CACHE_DIR` below the app's directory, the command's `env` and `stdin`, and an empty
   home for each run. A difference where the executable exits 0 is a false success; any other
   difference is a failure.
5. Requires a build, no false success, no failure and the `expect` text in one stdout, and
   records the result in the test's report whatever the outcome (`corpus.record`, as a dict,
   which pytest-xdist can send to its controller). With a known failure the test passes when
   anything in steps 2 to 5 fails, and fails, saying to remove the entry, when nothing does.
   Otherwise, when the test passes, it removes the app's temporary files (venv, executable and
   caches), making read-only directories writable first.

The same file runs `test_plugins_across_distributions_match_their_venv` on the `plugins` fixture
(entry points across two distributions and a PEP 420 namespace split between them).

The session prints a `corpus` section: the number of apps, false successes (target 0) and how
many of them are in known failures, the first-build success rate (apps built whose every command
agreed with the venv), and the apps with `[tool.fsbpyr]` settings.

### Wheels, sdists and known failures

[`tests/support/no-wheels.txt`](../tests/support/no-wheels.txt)
lists, per Python (`3.15 <package> ...`), the packages that publish no wheel for that Python or
none for fsbpyr's manylinux_2_34 baseline. A corpus app whose lock installs such a package is
not run there: `corpus.runnable` leaves it out of the test's parameters.
[`tests/unit/test_corpus_locks.py`](../tests/unit/test_corpus_locks.py)
reads every app's `uv.lock` for each supported Python and both machines (x86-64 and aarch64)
with [`locks.py`](../tests/support/locks.py),
and fails where a package has no usable wheel and `no-wheels.txt` names none of the app's gaps:
no app is built from an sdist by surprise, and every gap that keeps an app from running is
written down.
Packages in the app's `sdists` are not gaps.

A `known-failure` makes the test assert that the app still fails: it fails if the app passes,
so the entry goes once fsbpyr is fixed. There is no xfail (see
[A skip fails the run](#a-skip-fails-the-run)). No app at v0.28.0 has one.

### Containment of app runs

A built app whose library restarts `sys.executable` re-runs the app. On 2026-10-24 the corpus
app sklearn grew to 1,550 processes in 90 seconds and took its host down
([`contain.py`](../tests/support/contain.py)'s
docstring). Two layers hold such runs:

- `contain.run` runs every command of the corpus and differential tests, and the app runs of
  the end-to-end files that build whole projects, directories of scripts or single scripts
  (`test_build.py`, `test_compat.py`, `test_multicall.py`, `test_scripts.py`,
  `test_loose_directory.py`, `test_single_script.py`, `test_tool_binary.py`,
  `test_install_links.py`, `test_file_overlay.py`, `test_pipfile_lock.py`, `test_pid1.py`,
  `test_el8.py` and `test_el8_use_cases.py`), in a session of its own, with its environment
  tagged by a token
  (`FSBPYR_TEST_CONTAINED`) that every descendant inherits, detached or reparented ones
  included. It polls `/proc` for the tagged processes every 0.2 seconds and kills them all with
  SIGKILL when there are more than 64 (`Runaway`), when the command times out
  (`subprocess.TimeoutExpired`), or when any survive the command by more than 2 seconds
  (`Leftover`); each fails the test. The command also runs under `prlimit --nproc` set to the
  machine's current task count plus 1,024, in case polling falls behind.
  `tests/unit/test_contain.py` tests this with commands that stop by themselves.
- `make test`, `make test-ci`, `make test-corpus`, `make test-el8-stub`,
  `make test-el8-portability` and `make test-vm` run
  in a systemd user scope where a user manager exists: at most `TEST_TASKS` tasks (default
  8,192) and `TEST_MEMORY` bytes (default 70% of the machine's memory), no swap,
  `OOMPolicy=continue` (only the process that runs out of memory is killed), and whatever is
  left in the scope is killed when the run ends. Without a user manager the run is not wrapped.

The corpus README asks for apps to be tried through the test (`make test-corpus
PYTEST_ADDOPTS="-k <app>"`), since an app run by hand gets no such protection.

## Differential testing

[`tests/support/differential.py`](../tests/support/differential.py)
checks that a built executable behaves like the original application. `make_venv` makes the
project's staging environment with uv (its wheel, not an editable copy, and its locked
dependencies without dev groups, on the runtime's Python). `run_both` runs one command twice
with the same environment and working directory: the venv's console script under
`python -I -X utf8`, as built apps run, and the executable. It returns both outcomes (return
code, stdout, stderr); `assert_same` fails unless they are equal.

Only documented differences are normalized: the venv's and the executable's own paths
(`<venv>`, `<executable>`), and the directory a module's file is named in (a warning's location,
a traceback's frames), which is `<site>` for the venv's site-packages, the executable's tree and
the cache's extracted site directory alike. With `home`, each run gets an empty home directory of
its own (`HOME` and the XDG base directories), written as `<home>`. onnxruntime's host warning,
which carries a timestamp, is removed from stderr. Both runs get two threads per BLAS and OpenMP
pool unless the environment sets its own, and lose `FSBPYR_TRACE`, `VIRTUAL_ENV` and the
`PYTEST_*` variables of the run around them. A command has 120 seconds; the venv's sync 300.

`test_compat.py` (the compatibility matrix, the `mp` fixture's commands, a multiprocessing child
that calls `exit`, and the `datafiles` checks), `test_natives.py` (every subcommand of `heavy`)
and the corpus use it.

## Determinism checks

Several tests require the same bytes from the same inputs:

- `test_builds_do_not_depend_on_the_build_environment` (`tests/e2e/test_build.py`): `clicky`
  from each lock source builds to the same bytes with an active `VIRTUAL_ENV` and
  `UV_VENV_SEED=1`, and from another checkout path with umask 077, other cache directories and
  one CPU core.
- `test_self_hosting_reaches_a_fixed_point`: fsbpyr built from the venv (stage 1), stage 1
  building fsbpyr (stage 2) and stage 2 building it (stage 3), with only uv and git on `PATH`
  and an empty cache: all three are the same bytes.
- `test_build_interpreter_optimization_does_not_change_the_output`: `PYTHONOPTIMIZE=2` for the
  building interpreter changes nothing.
- `test_two_builds_are_the_same_bytes` (`tests/e2e/test_scripts.py`): two builds of the
  `scripts` fixture, a project without a build system whose wheel fsbpyr writes itself; in unit
  tests, `test_two_writes_are_the_same_bytes` (`tests/unit/builder/test_loose.py`) for that
  wheel alone.
- `tests/integration/test_bytecode_determinism.py`: canonical marshal output over the real
  stdlib, and the same canonical bytes from worker processes of different shapes (a clean
  process, one compiling in a batch, one with stdlib modules imported first, each with another
  `PYTHONHASHSEED`). `tests/unit/test_bytecode.py` covers reference counts, interned strings and
  equal constants.
- `test_the_launcher_links_reproducibly` (`tests/e2e/test_launcher_build.py`): two links of
  the same inputs are the same bytes.
- `tests/e2e/test_el8.py`: a default build of `hello` made again is the same bytes, and a
  `--glibc 2.28` build by a release fsbpyr (its own stdlib with the stub downloaded from the
  companion release) is the same bytes as the build with the cache's glibc 2.28 runtime.
- In unit tests: the payload encoding under permuted input (`test_roundtrip.py`), compression
  and pieces whatever the number of threads (`test_spill.py`, `test_zstd_pieces.py`), the
  compiler pool against in-process compiling (`test_compiler.py`), the manifest from two
  directories (`test_manifest.py`), the link's relative paths and the cache paths mapped out of
  the launcher (`test_toolchain_and_link.py`), and the SBOM generator (`test_sbom.py`).

## The README's quickstart as a test

The README marks its quickstart with `<!-- quickstart: tests/e2e/test_build.py runs these sh
blocks -->` and `<!-- /quickstart -->`.
[`tests/support/readme.py`](../tests/support/readme.py)'s
`quickstart()` returns every `sh` block between the markers, in order (the `pyproject.toml` shown
between them is not run).

`test_readme_quickstart_works_on_a_new_account` (`tests/e2e/test_build.py`) runs those lines
with `/bin/sh -ec` in a copy of the `hello` fixture, as on a new account: an empty `HOME` whose
new `~/.local/bin` is not on `PATH`, and a `PATH` of `/usr/bin`, `/bin` and a directory with uv
and a stand-in for `curl`. The stand-in accepts only the installer's URL and prints an installer
that copies a wrapper for the fsbpyr under test into `~/.local/bin`. The test requires exit 0
and the `hello []` line. `test_readme_quickstart_runs_command_lines_fsbpyr_accepts`
(`tests/unit/test_cli_docs.py`) parses the quickstart's `fsbpyr` command lines with the CLI's own
commands, which then do nothing, and
`test_readme_quickstart_project_is_the_fixture_it_is_tested_with` requires the `pyproject.toml`
it shows to have the `hello` fixture's build system and console script.

## Coverage

`[tool.coverage.run]` in `pyproject.toml` measures `src/fsbpyr` by path with branch coverage;
by path because the boot code also runs in-process under its embedded names (`_fsbpyr_boot`,
`_fsbpyr_rt`), which the boot unit tests load from the source tree. `[tool.coverage.report]`
sets `fail_under = 92.75`, compared with the total rounded to two decimals (`precision = 2`),
and shows missing lines, skipping covered files. `make test-ci` runs with `--cov
--cov-report=term --cov-report=xml` (the report is `coverage.xml`), so branch coverage below
`fail_under` fails it. The value is `make test-ci`'s total minus 1 (the comment beside it).
Coverage of the launcher's C code comes
only from the programs `LauncherBuild.compile` links, the probes and the fuzz harness, when
`FSBPYR_LLVM_COV_DIR` is set (see [Environment variables](#environment-variables)).

## make targets

| Target | What it runs |
|---|---|
| `make check` | Static checks: `ruff check` and `mypy` (strict, over `src` and `tests` but the fixtures and the corpus). |
| `make check-c` | clang's static analyzer over the launcher's C sources (`tools/check-c.py`); informational, exits 1 when the analyzer has findings. |
| `make test-ci` | The core: `tests/unit`, `tests/integration`, and thirteen end-to-end files: `test_build.py`, `test_runtime_app.py`, `test_imports.py`, `test_stub_hardening.py`, `test_multicall.py`, `test_scripts.py`, `test_loose_directory.py`, `test_single_script.py`, `test_install_links.py`, `test_pid1.py`, `test_unbuffered.py`, `test_file_overlay.py` and `test_pipfile_lock.py`, in `JOBS` pytest-xdist processes (`--dist loadfile`), with `-k "not (sanitized and not release)"` (the release half of the `sanitized` tests) and coverage. |
| `make test` | Everything but the corpus, in `JOBS` pytest-xdist processes (`--dist loadfile`: each file in one process). |
| `make test-prereqs` | `python -m tests.support.requires`: the tools and environment conditions the full suite needs that this machine lacks, all at once (see [When the machine lacks something](#when-the-machine-lacks-something)). No pytest run. |
| `make test-container` | Everything but `test_portability.py` and the corpus, in the Containerfile's `test` stage (Debian trixie, as the user `tester`, `--privileged`, with the Pythons 3.12 to 3.15 and a `/var/tmp` tmpfs that allows set-id bits), with `-m "not podman"`; needs podman. |
| `make test-corpus` | `tests/e2e/test_corpus.py` in `JOBS` processes (`--dist load`); needs the network for wheels. |
| `make test-portability` | Builds the four portability apps with `dist/fsbpyr` (`tools/portability-apps.sh`) and runs `tests/e2e/test_portability.py` on 12 distributions; needs podman, llvm-objdump and a `make build` first. |
| `make test-el8-stub` | In the [EL8 test mode](#the-el8-test-mode) (`FSBPYR_TEST_GLIBC=2.28`), the sixteen end-to-end files that run the stub and `tests/unit/test_test_modes.py`, in `JOBS` processes (`--dist loadfile`, `-m "not podman"`); then `tests/e2e/test_el8.py` outside the mode. |
| `make test-el8-portability` | `tests/e2e/test_portability.py` in the EL8 test mode: apps built for glibc 2.28 by the tests, on the 12 distributions and `rockylinux:8`, `almalinux:8`; needs podman. |
| `make test-el8` | Its prerequisites `test-el8-stub` and `test-el8-portability`. |
| `make test-vm` | `tools/vm-smoke.py $(VM_ARGS)`: aarch64 apps in a QEMU VM on a kernel with 64 KiB pages (see [The page-size smoke test](#the-page-size-smoke-test)); needs `qemu-system-aarch64`. No pytest run. |

Every test target but `test-container` runs in a fresh temporary directory under `TESTTMP`,
removed when the run ends; all of them but `test-vm` with LLVM 23 first on `PATH`; `test`,
`test-ci`, `test-corpus`, `test-el8-stub`, `test-el8-portability` and `test-vm` also in the
cgroup described under [Containment](#containment-of-app-runs). The full suite in CI runs the
tests in 4 processes (`pytest -n 4 --dist loadfile`, with the kept downloads); it, the tiers and
their timings are described in
[CONTRIBUTING.md](../CONTRIBUTING.md#running-the-tests).

## The page-size smoke test

`make test-vm` runs [`tools/vm-smoke.py`](../tools/vm-smoke.py),
which is not a pytest test. fsbpyr builds for the host it runs on, and an aarch64 host with
4 KiB pages never runs the page-size-dependent code (mapping and aligning the payload and the
natives, as the script's docstring says) with larger pages. The script runs aarch64 apps in a
QEMU system VM instead: `qemu-system-aarch64 -M virt -cpu max` under TCG (no KVM), 2 GiB of
memory, two CPUs and no network card (`-nic none`). It boots the one kernel pinned in
[`tools/vm-pins.toml`](../tools/vm-pins.toml):

| `--kernel` | Kernel | Page size |
|---|---|---|
| `64k` | Rocky Linux 8.10's aarch64 kernel (`kernel-core`), from an RPM | 64 KiB |

Kernels with 16 KiB pages are not tested: the pins file and the Makefile record it as an owner
decision of 2026-10-27, too few users for now. Without `--kernel`, every pinned kernel boots.
The script reads the kernel image and its config out of the RPM and refuses a kernel whose
config does not set the pin's option (`CONFIG_ARM64_64K_PAGES`) to `y`. The kernel command line
is `console=ttyAMA0 earlycon rdinit=/init panic=-1`: kernel messages from the first line on and
no `quiet`, so a boot that hangs before `/init` shows how far it got. The initramfs, a "newc"
cpio archive the script writes, holds a pinned root file system (Debian trixie's slim root file
system by default, or Rocky Linux 8.10's container base image with `--userland el8`, glibc
2.28, for apps built `--glibc 2.28`), each `--app` in `/apps` (first on `PATH`), the command
lines and an `/init`. `/init` mounts `/proc`, sets the kernel's console log level to 4 (as
quiet as `quiet`, so kernel messages do not break into the marker lines), mounts `/sys`, `/dev`
and `/tmp`, prints marker lines (`VMSMOKE-KERNEL`, `VMSMOKE-PAGESIZE`, `VMSMOKE-BEGIN n`,
`VMSMOKE-END n <status>`, `VMSMOKE-DONE`) on the serial console, runs each command and powers
the guest off.

Each `--run COMMAND` is a shell command line run as root in `/tmp` with
`FSBPYR_CACHE_DIR=/tmp/cache` and a limit of `--timeout` seconds (300 by default); it must exit
0, and written `COMMAND => TEXT` its output must also contain `TEXT`. Without `--run` only the
boot and the page size are checked. The host reads the console and fails a boot on another page
size than the pin's, a command that exits non-zero or lacks its text, fewer finished commands
than given, or an `/init` that did not reach its end; a boot gets 300 seconds. The apps are
aarch64 executables built on an aarch64 host, for example
`make test-vm VM_ARGS="--app dist/hello --run 'hello => hello'"` (the Makefile's example); the
`native` fixture project, with two small native dependencies, names this test in its
description.

Every file is pinned by URL and SHA-256, checked on every use and kept in
`FSBPYR_TEST_DOWNLOADS` (else in `fsbpyr-vm-smoke` below `XDG_CACHE_HOME` or `~/.cache`; about
150 MB, `CONTRIBUTING.md` says). The script exits 0 when every boot passed, 1 when a check
failed, 2 for a bad command line, a pinned file that is not what its pin says or cannot be used,
or a missing `qemu-system-aarch64` (nothing is installed), and 3 when a pinned file cannot be
fetched any more (its mirror dropped it: the pin needs renewing).
[`tests/unit/test_vm_smoke.py`](../tests/unit/test_vm_smoke.py)
tests the script's own code without a VM.

## Test files

The counts below are test items at v0.28.0: 3,470 in 131 files. They are what pytest collects
(`pytest --collect-only -q -p no:cacheprovider`, which runs no test) in the source tree of the
v0.28.0 tag, with that tree's `src` first on `PYTHONPATH`, on x86-64 under Python 3.14, with
`FSBPYR_TEST_GLIBC` and `FSBPYR_CORPUS_APPS` unset; the per-file counts come from the test ids
pytest lists. Collection includes tests that fail on a host that lacks what they need. Some
counts depend on the host and the Python, since a case that does not apply is not generated or
is deselected ([Cases that do not apply](#cases-that-do-not-apply)). `test_portability.py`
parametrizes over the images for the host's architecture: 35 on x86-64, 33 on aarch64 (two of
its tests run once per image, and `archlinux:latest` has no arm64 image). `test_corpus.py`
leaves out the apps whose packages have no wheel for the Python: `tensorflow` on 3.14 (64 of
the 65 apps run), 23 apps on 3.15, where three of `test_compat.py`'s checks, `test_natives.py`
and `test_portability.py`'s tests of `heavy` are left out too; nothing was deselected in the
count above. The EL8 test mode changes the parametrization as well: tests marked `sanitized`
run with the release launcher only, those two portability tests also run on the two EL8
images, and `test_el8_apps_run_on_el8` is not generated.

| Layer | Files | Tests |
|---|---|---|
| `tests/unit` (top level) | 24 | 578 |
| `tests/unit/boot` | 23 | 357 |
| `tests/unit/builder` | 19 | 752 |
| `tests/unit/payload` | 11 | 359 |
| `tests/unit/runtime` | 9 | 245 |
| `tests/integration` | 9 | 371 |
| `tests/e2e` | 36 | 808 |
| All | 131 | 3,470 |

### tests/unit (top level): 24 files, 578 tests

| File | Tests | What it tests, and how |
|---|---|---|
| [`test_bytecode.py`](../tests/unit/test_bytecode.py) | 29 | `fsbpyr.bytecode`'s canonical marshal rewriting on `compile()` output: round trips, idempotence, output independent of reference counts and previously interned strings, sorted frozensets, equal constants as one node, code objects never merged, malformed, recursive and too deeply nested streams refused. |
| [`test_cli.py`](../tests/unit/test_cli.py) | 53 | The CLI called in-process: version against `pyproject.toml`, help, SIGTERM ending a command like SIGINT, which commands collect the cache, `runtime list` (each runtime with its glibc, 2.34 for one that records none), error output (warnings before the error, a traceback and where to report only for unexpected exceptions; no usable home directory one line and exit 1 for `build`, `runtime build`, `runtime list` and `cache gc`, which collects `FSBPYR_CACHE_DIR` without one), the build commands refusing any Python but 3.12 to 3.15, `--glibc` and the project's `glibc` (the command line wins; the choices are the baselines; an unknown value in the project an error before any build), the Python chosen from the lock source the build installs (a `requirements` setting beside a `Pipfile.lock`, the project loaded again only then; two tests marked `version_rule`), the output handed on as given, not through its link, and a build of one command saying `1 command`. |
| [`test_cli_docs.py`](../tests/unit/test_cli_docs.py) | 35 | One source for help, completions and man page: completion through the shell protocol and a real bash round trip; the man page's sections, options, `[tool.fsbpyr]` keys, variables, exit statuses and examples (roff checked with groff); and the docs against the code: the README's quickstart (its command lines, and the project it shows built as the `hello` fixture is, with its build system and console script), the commands under "Verifying a release" in `CONTRIBUTING.md` naming no version, `docs/compatibility.md`'s distribution, checks and corpus tables against `images.py` (the EL8 images included), `test_compat.py`'s matrix and the corpus directories, quoted messages against strings the sources can compose, `src/` paths named in `docs/*.md`, and `docs/format.md`'s version. |
| [`test_compat.py`](../tests/unit/test_compat.py) | 3 | `fsbpyr.compat`, where the code differs between Pythons: every module of the package imports, the zstd module, the usable core count. |
| [`test_contain.py`](../tests/unit/test_contain.py) | 5 | `tests/support/contain.py`: a command runs as `subprocess.run` would; a detached survivor, a descendant holding the pipes, a timeout (processes blocking SIGTERM included) and a command restarting itself end in killed processes and a failure. Every command stops by itself. |
| [`test_corpus_locks.py`](../tests/unit/test_corpus_locks.py) | 75 | One test per corpus app (its lock against `no-wheels.txt`, see [The corpus](#wheels-sdists-and-known-failures)), plus `locks.usable` on sample wheel names and `locks.installed` on a made-up lock with markers and extras. |
| [`test_dispatch.py`](../tests/unit/test_dispatch.py) | 20 | `fsbpyr.dispatch`: a build for another Python runs that Python's variant of the same release, fetched from `FakeReleases` on localhost; `subprocess.run` is recorded, since the stand-in variants cannot run. Caching, offline use, failed verification, truncated downloads, a variant that needs a newer glibc than the host's (neither kept nor run), developer builds, cache names the collector keeps, `--glibc` or the project's `glibc` reaching the variant (an unknown one stopping before the fetch), `--offline` running a cached variant and fetching none (saying how to get one), and the variant told where the fsbpyr that handed it the build is. Marked `version_rule`. |
| [`test_downloads.py`](../tests/unit/test_downloads.py) | 7 | `tests/support/downloads.py`: seeding a session from kept files, files that do not match their pin, keeping what a session fetched, a second session finding it, pruning only the runtimes of the same glibc baseline, and a PBS distribution taken from the kept cache with its archive kept. |
| [`test_el8_stub_tool.py`](../tests/unit/test_el8_stub_tool.py) | 4 | `tools/release/el8-stub.py`, loaded as a module, with the cache's runtime faked by synthetic ELF stubs: a stub that needs glibc 2.28 at most and carries the runtime id this checkout expects is written under its asset name, with its SHA-256 printed; one that needs `GLIBC_2.34`, or carries another runtime's id, is refused (exit 1); a cache without the runtime names `fsbpyr runtime build --python 3.14 --glibc 2.28`; a bad command line exits 2. |
| [`test_elf_mutation.py`](../tests/unit/test_elf_mutation.py) | 1 | A Hypothesis property (1,000 examples): mutated bytes of a synthetic ELF object make `fsbpyr.elf` read them or raise `ElfError`, never anything else. |
| [`test_inspect.py`](../tests/unit/test_inspect.py) | 54 | `fsbpyr inspect` on executables built with `FAKE_RUNTIME` from made-up modules, files and build information: text and JSON, columns, `--verify`, licences, files, lock and settings lines (dependencies inferred from a venv among them), older formats, damaged records, the `baseline` line and `glibc_baseline` of an app built for glibc 2.28 (2.34 in the JSON of an app that records none), and text from the file that cannot drive the terminal. |
| [`test_no_skips.py`](../tests/unit/test_no_skips.py) | 2 | No file under `tests/` but the corpus skips or expects a failure: each file read with `ast` for the uses of `pytest.skip`, `pytest.xfail`, `pytest.importorskip`, the skip and xfail marks, `unittest`'s skips and `skipTest`, with a test that the scan finds each (see [A skip fails the run](#a-skip-fails-the-run)). |
| [`test_poetry1.py`](../tests/unit/test_poetry1.py) | 16 | `fsbpyr.poetry1`: a Poetry 1 `pyproject.toml` with `package-mode = false` and no `[project]` read as a `[project]` table: `^`, `~`, bare and wildcard constraints as PEP 440 specifiers, `||` alternatives and git dependencies refused, optional dependencies as extras, markers with an `or` kept apart from the Python constraint, and which projects count as unpackaged. |
| [`test_portability_images.py`](../tests/unit/test_portability_images.py) | 4 | `tests/support/images.py`: all twelve images on x86-64, in order; those with an arm64 variant on aarch64. |
| [`test_pyversion.py`](../tests/unit/test_pyversion.py) | 57 | The version rule (`fsbpyr.pyversion`): the Python that `requires-python`, a `.python-version` pin and `--python` give, and the errors. Marked `version_rule`. |
| [`test_requires.py`](../tests/unit/test_requires.py) | 14 | `tests/support/requires.py` and the hooks of `tests/conftest.py`, with pytester on a suite of its own under that conftest: a missing tool or an unmet condition failing with one line that names the package or the change, a marked test failing before its fixtures run, `podman` marking and `-m "not podman"`, a test needing a wheel that does not exist deselected, a skip or an xfail failing the run, an empty parameter set a collection error; and every tool and package named in `CONTRIBUTING.md` and installed by the test container. |
| [`test_sbom.py`](../tests/unit/test_sbom.py) | 18 | The CycloneDX SBOM generator `tools/release/sbom.py`, loaded as a module: identical bytes for identical input, CPEs and purls, the executable's hash, refusal of incomplete or unverified input. |
| [`test_selfupdate.py`](../tests/unit/test_selfupdate.py) | 53 | `fsbpyr self update` against `FakeGitHub` on localhost: update in place keeping the mode, variants, symlinks, damaged, truncated or foreign downloads never installed, a download that unpacks beyond its limit, an asset that needs a newer glibc than the host's never installed (versions compared as numbers; no check where the host has no glibc), unwritable directories (root's case asserted as root sees it), man page and completions written again, glibc 2.28 stubs placed beside the executable or the path it was run by left alone and named in a warning only when the new release's companion does not list their SHA-256 (all of them, with a condition, when its sums cannot be read; the companion not asked when none is placed), and a download cut short an update error, not a traceback. |
| [`test_targets.py`](../tests/unit/test_targets.py) | 48 | The target table (`fsbpyr.targets`) and host detection, the pins and sysroot of each target and Python, the EL8 sysroot (Rocky Linux 8.10's `glibc`, `glibc-devel`, `glibc-headers`, `kernel-headers`, `libgcc` and `gcc` RPMs, the same builds for both targets) and the refusal of other baselines, runtime ids (different per baseline, the default's unchanged), the manylinux platform uv installs for (manylinux_2_28 for glibc 2.28), each target's loader, filter and machine. |
| [`test_test_modes.py`](../tests/unit/test_test_modes.py) | 6 | The corpus selection (`FSBPYR_CORPUS_APPS`: unset, every app; a selection; an unknown app refused) and the [EL8 test mode](#the-el8-test-mode): a build in a test is for the run's baseline (2.34, or 2.28 in the mode) while a `--glibc` still wins, `baseline.as_tuple`, and the EL8 images added to the matrix for glibc 2.28. `make test-el8-stub` runs it in the mode. |
| [`test_text.py`](../tests/unit/test_text.py) | 8 | `fsbpyr.text`: `plural` (no `s` for a count of one), and `tail_lines`: output within the limit unchanged, a cut inside a line moved to the next line start with a note of the earlier lines left out (`1 earlier line` in the singular), a cut on a line start keeping that line, and output with no newline after the cut cut where it falls. |
| [`test_tool_metas.py`](../tests/unit/test_tool_metas.py) | 4 | `fsbpyr.tools.tool_scripts`: which distributions without a script a tool binary follows to their requirements. A meta-package gives the commands of every requirement, a distribution with code only those of a requirement named after it; a library beside a tool gives none of its dependency's commands, and a library alone is an error. |
| [`test_vcs.py`](../tests/unit/test_vcs.py) | 33 | `fsbpyr.vcs` on temporary git repositories: the commit and tag a build records (an `el8-vX.Y.Z` tag on the same commit not taken for it), the version string, the commit time, the asset name of each Python and target, and the companion release `el8-vX.Y.Z` that holds a release's glibc 2.28 stubs (none for 2.34). |
| [`test_vm_smoke.py`](../tests/unit/test_vm_smoke.py) | 29 | `tools/vm-smoke.py` without a VM (see [The page-size smoke test](#the-page-size-smoke-test)): the shape of `tools/vm-pins.toml` (HTTPS URLs and SHA-256s, the one kernel, `64k`, an RPM naming its page size and config option; the Debian and EL8 userlands), the cpio writer and the initramfs, the RPM reader and the kernel image taken from the package, pinned downloads that fail or do not match, the console parsing and the checks, a QEMU command line without a network card, a kernel command line with `earlycon` and without `quiet`, `/init` turning the kernel's messages down before its first marker line, a QEMU that exits at once. |

### tests/unit/boot: 23 files, 357 tests

Most build an app with `FAKE_RUNTIME` and run the boot code in-process through the `boot`
fixture (see [Configuration and conftests](#configuration-and-conftests)). `test_fixups.py`,
`test_gc.py`, `test_links_names.py`, `test_mp.py`, `test_reexec.py`, `test_service.py` and
`test_way.py` import the `_fsbpyr_rt` module they test on its own, without an app.

| File | Tests | What it tests, and how |
|---|---|---|
| [`test_boot.py`](../tests/unit/boot/test_boot.py) | 44 | On the demo app of `tests/support/apps.py`: the path hook (`..` normalized, never above the executable; directories below the scripts root under their stored names), specs and loader, namespace packages, `pkgutil`, `importlib.resources` (traversal, functional API, which takes one resource name on 3.12, `as_file`), `importlib.metadata`, entry points, `boot.run()` (selftest, `exit`/`quit`/`help`, helper interpreters with and without the setting, multiprocessing children, the trace at exit), argparse fixed up only in apps of scripts, and the excepthook: a missing module named without Python 3.15's hint about site initialization, and the note for an `OSError` on a path inside the executable, which names the package `--fs-package` would take, if any. |
| [`test_commands.py`](../tests/unit/boot/test_commands.py) | 32 | `_fsbpyr_rt.commands` and the boot's use of it in a multi-command executable: the `name=target` table, the command from `argv[0]` (`.py` stripped) and else from `argv[1]`, which is removed; without a command the list on stderr and exit 2; the selftest importing every target; a link started with `-m` not taken for a helper interpreter; a forkserver preloading the module of the parent's command. |
| [`test_distutils.py`](../tests/unit/boot/test_distutils.py) | 2 | `import distutils` answers with setuptools' copy before setuptools is imported. |
| [`test_execve_argv0.py`](../tests/unit/boot/test_execve_argv0.py) | 8 | The boot's `sys.argv[0]`: the path `execve` was given (`execfn` in `_fsbpyr.info()`), made absolute against the working directory without collapsing `..`; left as it is when there is no path, the caller renamed the start or the launcher re-executed itself, or the working directory is gone. |
| [`test_fixups.py`](../tests/unit/boot/test_fixups.py) | 18 | `_fsbpyr_rt.fixups`, with fakes for SSL contexts, `ctypes.CDLL` and parso's `FileIO`: the CA bundle fallback (with `/etc/ssl` locations moved below `tmp_path`), bundled libraries loaded by name, multiprocessing's executable, OpenCV's loader path, file times, argparse's `prog` for a script started by its own file path. |
| [`test_frozen_sources.py`](../tests/unit/boot/test_frozen_sources.py) | 1 | `inspect.getsource` of CPython-frozen stdlib modules falls back to the payload. |
| [`test_fs_packages.py`](../tests/unit/boot/test_fs_packages.py) | 12 | Packages built as fs-packages: their files on disk before their code runs, code still from the payload, resources as real directories, extraction once per process, rechecks on import. |
| [`test_gc.py`](../tests/unit/boot/test_gc.py) | 25 | The app cache's collector (`_fsbpyr_rt.gc`): what it removes and what it never touches, one run at a time, the marker, and a tree taken while a run collects it (with `tests/support/cache.py`'s `take` as the launcher's side). |
| [`test_init_module.py`](../tests/unit/boot/test_init_module.py) | 3 | `pkg.__init__` imports as a module of its own (`from .__init__ import x`), as in a venv. |
| [`test_links_names.py`](../tests/unit/boot/test_links_names.py) | 4 | `_fsbpyr_rt.links`: a command name with a directory part (`../../evil`, `a/b`, `..`, `.`) makes no link. |
| [`test_mp.py`](../tests/unit/boot/test_mp.py) | 40 | `_fsbpyr_rt.mp`: the command lines multiprocessing re-executes the app with (spawn, forkserver, resource tracker) are recognized for children of the same executable and parents with the same ids, refused otherwise, never evaluated as code. Under user-mode QEMU one test fails by design: `/proc/<pid>/exe` names the emulator for every process. |
| [`test_native.py`](../tests/unit/boot/test_native.py) | 35 | `_fsbpyr_rt.native`, with `dlopen` faked where loading matters: cache directory precedence (the passwd entry's home when `HOME` cannot be used), extension specs, a closure extracted once, modified, unreadable or symlinked cached files, unwritable caches, unsafe app names, bundled libraries found by file name; a refused cache directory (others may write to it, or another user owns it) ending the start with one line and exit 70 while other extraction failures stay errors, a compiled module's own `OSError` not taken for an extraction failure, and the note naming `--fs-package` for a file missing below the native cache's `site/` directory. |
| [`test_native_xz.py`](../tests/unit/boot/test_native_xz.py) | 1 | xz natives and their pieces extract without a thread of the runtime. |
| [`test_pth_roots.py`](../tests/unit/boot/test_pth_roots.py) | 1 | Modules under a `.pth` directory import by their names relative to it. |
| [`test_reexec.py`](../tests/unit/boot/test_reexec.py) | 45 | `_fsbpyr_rt.reexec`: `-m`, `-c` and payload scripts from children of the app run with interpreter-reexec, are refused without the setting, run the app with `false`; other callers get the app; the dispatcher form `exe COMMAND -m word` runs that command whatever the setting. Without the setting a script that is one of the app's own commands runs as `python script` would, for children of the app only; a payload script has its directory first on `sys.path` and is named by the path it says it lives at; a host file beside the executable that is no script runs the app, and is looked up only after the parent check. |
| [`test_resource_tree.py`](../tests/unit/boot/test_resource_tree.py) | 4 | Resources without a digest are rechecked by every process; a corrupt resources id is reported. |
| [`test_restart.py`](../tests/unit/boot/test_restart.py) | 19 | `_fsbpyr_rt.restart` and the boot's use of it: in the `[sys.executable] + sys.argv` restart idiom of gunicorn and Django, the executable's own path (or a link to it) after interpreter flags is dropped with the flags, so the app gets the argv a venv's script gets; anything else is no restart, and a common start loads nothing. |
| [`test_scripts_overlay.py`](../tests/unit/boot/test_scripts_overlay.py) | 9 | The scripts root next to the executable: a module of `_fsbpyr_scripts/` has `__file__` at `<directory of the executable>/<path below the root>` while its code is the payload's; a packaged module is where it was; the path hook claims those directories (only in an app with a scripts root); a script's directory first on `sys.path`; `get_data` of such a path reads the payload; the notes for a file missing beside the executable. |
| [`test_selftest.py`](../tests/unit/boot/test_selftest.py) | 8 | `FSBPYR_SELFTEST=natives`: the dynamic loader's trace output parsed for missing libraries and versions, with the loader faked by lines copied from glibc's; a refused cache directory ending the check with one line and exit 70, as it ends a run. |
| [`test_service.py`](../tests/unit/boot/test_service.py) | 19 | `_fsbpyr_rt.service`: `JOURNAL_STREAM` read as a decimal `device:inode` pair and matched against a stream (a matching one made line-buffered; another, an unbuffered or a closed one left alone); the SIGTERM handler that exits 143, installed in process 1 only and only over the default disposition, and gone again in a forked child. |
| [`test_syspath_subdirs.py`](../tests/unit/boot/test_syspath_subdirs.py) | 4 | `sys.path` entries below the executable (`<exe>/pkg/_vendor`): modules there import by their short names. |
| [`test_warning_frames.py`](../tests/unit/boot/test_warning_frames.py) | 3 | Import-time warnings with `stacklevel >= 2` name the importing module and show the payload file's source line. |
| [`test_way.py`](../tests/unit/boot/test_way.py) | 20 | A cache root's way from `/` (`gc.way_problem`): owners and write permissions of every directory, sticky directories, symlinks, `..`, user namespaces (`unshare`) and mapped uids (`podman unshare`); the tool's cache and the collector refuse an untrusted way. |

### tests/unit/builder: 19 files, 752 tests

The build pipeline in-process, on trees made in `tmp_path`: fake site-packages, synthetic ELF
objects from `tests/support/elf.py`, local distributions, the hand-written locks of the
`foreign` fixture, fake `uv` scripts and the uv command lines a plan records.

| File | Tests | What it tests, and how |
|---|---|---|
| [`test_build.py`](../tests/unit/builder/test_build.py) | 54 | `fsbpyr.build`: the smoke test (on shell-script stand-ins for the new executable; the tail of its error output from a line start), the output replaced only after it passes, interrupts keeping the old output, the glibc requirement of stub and natives against the baseline (a native above 2.28 warning for glibc 2.28, the report never below 2.28 there, `glibc_baseline` recorded for 2.28 only), the sysroot rebuild of the project's own extension, uv version warnings, `--strict` (`1 warning` in the singular), `collecting 1 distribution`, `--offline` reaching the runtime selection, and the hints about unpinned requirements, split requirements files, sdists and helper interpreters. |
| [`test_closure_and_classify.py`](../tests/unit/builder/test_closure_and_classify.py) | 79 | `fsbpyr.closure` and `fsbpyr.classify` on fake site-packages: the installed-dist index, the dependency walk, entry points, and the classification of every file (modules, namespaces, natives and their closures, `.pth` files, fs-packages, shadowed modules, compression, the spill), with its warnings and hints: compile warnings of the project's own files, reads next to `__file__` in the scripts root (in the main guard only of the module the executable runs as `__main__`: a command script, or the module `--entry` names; never a sibling's, a package's or that of a module run through an entry function), `--hints` leaving out the packages below the scripts root, `--strict` not counting the file-read guess, Python files below a directory that is no Python name. |
| [`test_compiler.py`](../tests/unit/builder/test_compiler.py) | 10 | `fsbpyr.compiler`: worker processes compile what the build's own process would, in the classifier's order; few modules or one core compile in-process; compile warnings come back, also from the workers, with their line, and leave the bytecode alone; the main guard's reads are scanned in the commands of the scripts root only, by the workers too. |
| [`test_early_checks.py`](../tests/unit/builder/test_early_checks.py) | 12 | Failures reported before uv stages anything: a script name that is no command, a static project with no script and no candidate for an entry (a possible candidate passes), an output that is a directory, an unknown extra; several static scripts pass, with or without `--entry`. |
| [`test_entry.py`](../tests/unit/builder/test_entry.py) | 11 | `fsbpyr.entry`: an `if __name__ == "__main__":` guard found with `ast` in a module's body only (also behind a BOM or a coding cookie), and the entry candidates of an installed project (the top level of its `RECORD`) and of a source tree (a `__main__` file or a guarded module; none in a `site-packages` directory). |
| [`test_foreign.py`](../tests/unit/builder/test_foreign.py) | 56 | `fsbpyr.foreign` on the `foreign` fixtures: `poetry.lock` (Poetry 2, Poetry 1.5 to 1.8, lock-version 1.0) and `pdm.lock` as the lock of a build. The main group without dev and Windows-only packages, markers for the target, every hash demanded, extras, a lock without groups walked from the project's dependencies, path, VCS and URL entries, another index refused, the stale-lock check by each tool's content hash, the lock record, the rank among lock sources, and hashes or names that would add options to the requirements file refused. |
| [`test_legacy.py`](../tests/unit/builder/test_legacy.py) | 11 | `fsbpyr.legacy`: which `scripts=` files of the project's wheel are Python (`#!python`), the module each is stored under, clashes with console scripts and between stored names, the warnings for dropped and uncompilable scripts, and the missing-entry check left to the wheel for `setup.py` and `script-files`. |
| [`test_loose.py`](../tests/unit/builder/test_loose.py) | 58 | Projects without a build system (`fsbpyr.loose`, `fsbpyr.importcheck`): what makes one, which files are packaged (Python files only; hidden, build and dependency directories skipped; links; the tracked files of a git work tree, untracked ones reported), the wheel fsbpyr writes (files under `_fsbpyr_scripts/`, a valid `RECORD`, the same bytes twice, metadata from `[project]`, the staging epoch), the commands it finds (scripts, guarded modules, `main` named after the project, test files never), and the static import check (file and line, relative and optional imports, the closure of the project's own modules, removed stdlib modules named with the Python that has them, the freeze hint naming by its absolute path the environment the build reads (a `.venv`, `venv` or `env` with a `pyvenv.cfg`, else `$VIRTUAL_ENV`), `--strict`). |
| [`test_loose_directory.py`](../tests/unit/builder/test_loose_directory.py) | 93 | Directories of loose scripts: discovery (a `setup.cfg` alone refused, a `pyproject.toml` of settings only, a `uv.lock` beside the scripts not synced, not the home directory or `/`), the directory's name made valid, python and `uv run` shebangs, which files are packaged (git decides; links inside and outside), names that are no Python names stored under mangled ones and their clashes, commands (guards and shebangs, test files never, `main` and `__main__` named after the directory), the union of the PEP 723 blocks and the intersection of their `requires-python`, the deploy recipe, and `sys.path` changes built from `__file__` (the `pylib` hack, `..`) followed by the import check. Discovery runs no user code. |
| [`test_manifest.py`](../tests/unit/builder/test_manifest.py) | 19 | `fsbpyr.manifest`: the lock and each distribution recorded with the hash of the artifact the lock names, sdist builds, local dists, workspaces, identical records from two directories, and every dist of the `compat` fixture traced to its `uv.lock`. |
| [`test_multicall.py`](../tests/unit/builder/test_multicall.py) | 34 | The build side of a multi-command executable: the sorted `name=target` table (one script stays a plain target, `--entry` names one of several), command names, a single script's name that would leave the output directory, the relative links beside the output (an existing symlink replaced, and an fsbpyr executable of that command whose build information names the same project; any other file or a directory never; links follow the output's name), an output path that links to the earlier output of several commands replaced rather than written through while any other link is followed, and the early check that fails VCS requirement lines in a file with hashes (or with `--strict`). |
| [`test_natives.py`](../tests/unit/builder/test_natives.py) | 44 | `fsbpyr.natives` and `fsbpyr.elf` on synthetic ELF objects (and two host binaries, the `libc.so.6` the test process maps and `ls`): dynamic sections, RPATH and RUNPATH as ld.so applies them, libraries opened by name, unresolved libraries, symbol versions, bounds on malformed input. |
| [`test_pipenv.py`](../tests/unit/builder/test_pipenv.py) | 45 | `fsbpyr.pipenv` on the `foreign/pipenv` fixture: the `default` section without `develop` and Windows-only packages, markers, hashes, unpinned versions refused, git, path and file entries, the project itself not installed as a dependency, other indexes as a note, the stale-lock check against the Pipfile, the Python `_meta.requires` asks for (two tests marked `version_rule`), the lock's rank among the sources, and a directory with a `Pipfile.lock` and no Python file as a tool binary of the Pipfile's packages. |
| [`test_project_and_staging.py`](../tests/unit/builder/test_project_and_staging.py) | 78 | `fsbpyr.project` (discovery, a `setup.cfg` alone refused, lock source priority, `[tool.fsbpyr]` and command-line settings: `glibc` 2.34 by default, 2.28 from the project or the command line, the command line winning, other values and non-strings refused) and `fsbpyr.staging` (the uv commands per lock source, wheels for the glibc baseline's platform such as `x86_64-manylinux_2_28`, workspaces, rebuilds, `SOURCE_DATE_EPOCH` from the last commit, which requirement lines count as pinned, VCS lines named by file and line, a source tree left as the build found it, the tail of a failed uv step from a line start). |
| [`test_scan.py`](../tests/unit/builder/test_scan.py) | 39 | `fsbpyr.scan` on source snippets: reads relative to `__file__` (in a command's main guard when asked; existence checks not reported; a script starting a sibling through `sys.executable` reading nothing, while a package doing so is still reported), libraries opened by a literal name. |
| [`test_single_script.py`](../tests/unit/builder/test_single_script.py) | 20 | One script as the project: a `.py` file or an extensionless one with a `uv run` shebang (other files refused), its import closure packaged (siblings, and a directory added to `sys.path` from `__file__`), its PEP 723 block (one only; `requires-python` choosing the Python; `[tool.fsbpyr]` keys checked), the directory's requirements and `pyproject.toml` not read, a script lock beside it locking the build and synced into the staging environment, and nothing written beside it. |
| [`test_system_report.py`](../tests/unit/builder/test_system_report.py) | 21 | `fsbpyr.system`: what the target must provide beyond glibc, the manylinux_2_34 and manylinux_2_28 floors of each target (libstdc++, CXXABI, GCC and zlib versions, the 2.28 ones older in every namespace), and the warnings, among them a libstdc++ within the 2.34 floor but newer than what manylinux_2_28 systems provide. |
| [`test_tools.py`](../tests/unit/builder/test_tools.py) | 47 | The tool binary (`fsbpyr.tools`), on staged sites built by hand: when a directory is one (no Python file and a lock source, `--requirements` or the `requirements` setting), which distributions a requirements file (includes followed) or `pylock.toml` names, which of them are tools (all of a short file; in a complete listing such as a freeze, those no other requires), their console and gui scripts as commands, meta-packages and families followed (cycles and a depth limit stop), libraries and missing distributions, name clashes, and the project that takes the tools' scripts and requirements; one distribution is named in the singular. |
| [`test_venvdeps.py`](../tests/unit/builder/test_venvdeps.py) | 21 | The venv rung (`fsbpyr.venvdeps`): a venv found as `.venv`, `venv`, `env`, then `$VIRTUAL_ENV`; the distributions that provide the unresolved imports (`top_level.txt`, `RECORD`) and their `Requires-Dist` closure pinned at the installed versions, nothing guessed from PyPI; a needed editable or path install refused; the venv's Python never started; the lock record (`inferred`); `--strict`. |

### tests/unit/payload: 11 files, 359 tests

Writer and reader on specs from `make_spec`; edge cases from `tests/support/edges.py`.

| File | Tests | What it tests, and how |
|---|---|---|
| [`test_blocks.py`](../tests/unit/payload/test_blocks.py) | 20 | Small blobs packed into shared zstd blocks: placement, byte-for-byte round trips, identical content packed once, the reader's block cache, bad member records refused. |
| [`test_edges.py`](../tests/unit/payload/test_edges.py) | 81 | The Python reader, with the stand-in's cache sync, on the intact edge-case executable, each of the 66 format cases and each of the 14 path cases (counted from collection): refused with `PayloadError` (or `CacheError`, `OSError` where the cache refuses) or read, never failing another way. The walk is `tests/support/walk.py`. |
| [`test_file_digest.py`](../tests/unit/payload/test_file_digest.py) | 19 | The integrity digest of a file, streamed a leaf at a time: sizes around leaves, ranges, holes hashed as zeros, files shorter than asked. |
| [`test_hardening.py`](../tests/unit/payload/test_hardening.py) | 37 | Regression tests from review findings: hostile sizes, path traversal, reserved fields, overlapping sections, bad closures, zstd frames without checksum or content size, trailing bytes. |
| [`test_integrity.py`](../tests/unit/payload/test_integrity.py) | 15 | The whole-file digest: the tree construction, leaf counts, one construction for every representation, any changed byte failing verification, older versions. |
| [`test_roundtrip.py`](../tests/unit/payload/test_roundtrip.py) | 39 | Writer to reader: every table and field, lookups, sorted tables, deterministic encoding under permuted input, deduplication, alignment, the stub and trailer, replacing an executable only when committed. |
| [`test_spill.py`](../tests/unit/payload/test_spill.py) | 20 | Compressed bytes go to a spill file: the same blobs as compressing in memory, sparse files, pieces on 1 and 8 threads, holes never sent to threads. |
| [`test_streaming_writer.py`](../tests/unit/payload/test_streaming_writer.py) | 7 | The streaming writer gives the assembled image, writes runs of zeros as holes, fails on a file changed since it was classified, and verifies the file it opened. |
| [`test_validation.py`](../tests/unit/payload/test_validation.py) | 80 | Malformed specs refused at encode time, malformed bytes at read time (headers, sections, strings, records, trailer, truncations), and two Hypothesis properties: bytes XORed with random masks (300 examples) and random 64-bit overwrites (500) raise only `PayloadError`. |
| [`test_xz.py`](../tests/unit/payload/test_xz.py) | 25 | xz natives: standard streams, raw when incompressible, natives only, pieces of independent streams, refusal of streams and pieces that do not match their records. |
| [`test_zstd_pieces.py`](../tests/unit/payload/test_zstd_pieces.py) | 16 | zstd in pieces: one frame per piece with size and checksum, the same bytes whatever the number of threads, round trips across boundaries, refusals. |

### tests/unit/runtime: 9 files, 245 tests

| File | Tests | What it tests, and how |
|---|---|---|
| [`test_collect.py`](../tests/unit/runtime/test_collect.py) | 20 | The tool's cache collector (`fsbpyr.runtime.collect`) and `fsbpyr cache gc`: what stays (what the running fsbpyr builds with, what is held), what goes and when, the marker, and a user's directory left alone. |
| [`test_fetch_and_build.py`](../tests/unit/runtime/test_fetch_and_build.py) | 39 | `fsbpyr.runtime.pbs` and the registry's build orchestration, on archives made in memory and a patched `urllib.request.urlopen`: verification, retries of network failures only, path traversal refused, the pins, runtime ids; a runtime built for each glibc baseline (its stub linked against that baseline's sysroot, the baseline recorded, never found for the other one), the id covering the target's own link flags, `fsbpyr runtime build --glibc`; zstd's version read from the string in `libzstd.a`, left out rather than guessed when there is no single one; the error output of a failing interpreter query or compile worker from a line start. |
| [`test_notices.py`](../tests/unit/runtime/test_notices.py) | 15 | Licence notices of the libraries a runtime links, from a made-up PBS distribution; zstd's text for every Python, PBS's own or fsbpyr's copy where PBS ships none. |
| [`test_registry.py`](../tests/unit/runtime/test_registry.py) | 44 | `fsbpyr.runtime.registry`: payload to spec and back, saving and loading runtimes, app assembly, generation and resources ids, private directories, the runtime embedded in an executable, a clear error when neither `HOME` nor a passwd entry gives a home, the baseline read back from an executable's sysroot identity (the default for an unknown one or a runtime saved without one), the embedded runtime used only for its own baseline, and the error naming `fsbpyr runtime build --python 3.14 --glibc 2.28`. |
| [`test_rpm.py`](../tests/unit/runtime/test_rpm.py) | 22 | `fsbpyr.runtime.rpm`, the RPM reader of the EL8 sysroot, on synthetic packages from `tests/support/rpms.py`: files, directories and symlinks come out as tar members, a set of hard links after the file that carries the data, names under the merged `/usr` (`lib64/...` as `usr/lib64/...`), absolute names made relative. Refused: what is no RPM, payloads not compressed with xz, damaged or truncated payloads and headers, a bad cpio header, names that leave the package, devices, an incomplete set of hard links, a payload over the size limit. |
| [`test_stdlib.py`](../tests/unit/runtime/test_stdlib.py) | 6 | Stdlib classification, `-X importtime` parsing, compile-worker framing. |
| [`test_stubs.py`](../tests/unit/runtime/test_stubs.py) | 43 | `fsbpyr.runtime.stubs`, the glibc 2.28 stub of a released fsbpyr, with `FakeReleases` on localhost: a file placed beside the executable (by the path it was run by, then its real path; `.xz` unpacked) wins over the cache, the cache over the companion release `el8-vX.Y.Z`, whose `SHA256SUMS-glibc2.28` the download must match (the binary marker and other stubs' lines allowed) and which is kept for offline use; nothing is asked of `vX.Y.Z` itself. The runtime id in the stub is checked whatever the source: a placed stub of another release is refused naming the expected id, a downloaded one of another Python refused and not kept, a cached one fetched again. The cache keeps a stub under the runtime id it carries, so the next release reuses an unchanged one. `--offline` uses a placed or cached stub only and otherwise says where to place one. A variant run by the fsbpyr of another Python looks beside that fsbpyr first (an empty or relative value ignored, the stub there still checked) and names that place. A size limit; the plain message when the companion release, its sums line or its asset is missing, another when the server cannot be reached; a release pairing its own stdlib with the stub (`stubs.paired`, `select_runtime`, which passes `--offline` on), a developer build asking the cache instead. |
| [`test_sysroot.py`](../tests/unit/runtime/test_sysroot.py) | 21 | The glibc sysroots: `ar` members, symlinks rewritten into the sysroot, entries and links that would leave it refused, verified downloads over HTTPS only; RPMs extracted as debs are, less what linking never reads, a damaged RPM named, and `lib` and `lib64` linked into `usr` for the EL8 sysroot only. |
| [`test_toolchain_and_link.py`](../tests/unit/runtime/test_toolchain_and_link.py) | 35 | `ar` listing, clang discovery, and the stub's link command: static PIE, hardening flags, the sanitized variant, outlined atomics and the Cortex-A53 erratum 843419 fix on aarch64 (release and sanitized), paths mapped out of the launcher, a frame over the limit failing the link, the runtime id the stub records (`-DFSBPYR_RUNTIME_ID`, when one is given), a flag that is not part of what the runtime id covers, and a failed link's output from a line start. |

### tests/integration: 9 files, 371 tests

| File | Tests | What it tests, and how |
|---|---|---|
| [`test_bytecode_determinism.py`](../tests/integration/test_bytecode_determinism.py) | 2 | Canonical bytecode over the real stdlib, and across build processes of different shapes (see [Determinism checks](#determinism-checks)). |
| [`test_check_release_el8.py`](../tests/integration/test_check_release_el8.py) | 11 | The two EL8 steps of `tools/release/check-release.sh` (the companion release's stubs, and a build for glibc 2.28), cut out of the script and run with its own helpers against a fake `gh` and `git`: a version without a companion release skips both; a draft or prerelease companion, a companion that is the latest release, a companion tag on another commit, a missing asset (one failure, not two) and each file whose attestation does not verify fail; a complete companion passes and a later fsbpyr fetches its stub (cached under the tag by 0.27.0, under the runtime id by later releases), while 0.26.0's stub is placed beside the executable instead. |
| [`test_el8_workflow.py`](../tests/integration/test_el8_workflow.py) | 28 | [`.github/workflows/el8.yml`](../.github/workflows/el8.yml), which publishes the glibc 2.28 stubs in the companion release `el8-vX.Y.Z`, read as text (GitHub runs it, not the tests): its triggers, no checkout of another ref, the release and its gate naming none of it, one run per tag, every target on a runner of its architecture, the default Python read from the tag, actions pinned as the other workflows pin them, write permissions in `publish` only, the companion made as a draft on its tag, which must exist (`--verify-tag`, no target), and published as not the latest, asset names those `stubs.py` reads. The publish job's shell steps also run against a fake `gh`: nothing published yet, the same stubs already published, other stubs published (a failure, left alone), a draft left by a stopped run deleted, the wait for the release of the tag, a companion tag that is missing or on another commit, the check of the result. |
| [`test_install.py`](../tests/integration/test_install.py) | 137 | `install.sh`, generated from fake release assets and served over HTTPS from 127.0.0.1 with a self-signed certificate for `localhost` and 127.0.0.1 that the session makes with the `openssl` CLI; `HOME`, the install directories and a `PATH` of chosen tools are under `tmp_path`, so a test can leave out or fake `xz`, `curl`, `gh`, `uname` or `ldd`. Each installer test runs under dash, bash and busybox sh (129 of the 137); the rest test the installer's generator and that dash and busybox parse the template. |
| [`test_launcher_format.py`](../tests/integration/test_launcher_format.py) | 18 | `launcher/format.h` and `fsbpyr.payload.format` describe the same bytes: a C probe compiled with `cc` prints record sizes, constants and field offsets, compared with the Python structs. |
| [`test_pbs_distribution.py`](../tests/integration/test_pbs_distribution.py) | 8 | Facts about the pinned PBS distribution the runtime build relies on: startup modules, the compile worker, the link plan, a licence text for every linked library (zstd's among them). The session takes the distribution from `FSBPYR_TEST_DOWNLOADS` or fetches it (`downloads.pbs_distribution`). |
| [`test_pbs_stdlib_versions.py`](../tests/integration/test_pbs_stdlib_versions.py) | 12 | What the stdlib selection and startup modules give for each pinned CPython version's distribution, each taken or fetched as for `test_pbs_distribution.py`. |
| [`test_release.py`](../tests/integration/test_release.py) | 151 | The release scripts against throwaway git repositories, a bare origin and a fake `gh` (the tag and the companion's tag `el8-vX.Y.Z` pushed together, the release check refusing a companion release `el8-vX.Y.Z` as the latest release, and passing over it when it looks up the previous release); also checks of the workflow files, `tools/bootstrap.sh` and the Makefile's handling of `VERSION` and `PYTHON`. Nothing reaches GitHub. |
| [`test_upstream_watch.py`](../tests/integration/test_upstream_watch.py) | 4 | `tools/upstream-watch.sh` against a fake `gh` serving python-build-standalone releases: up to date, a newer release opening or updating an issue, no release with the archive. |

### tests/e2e: 36 files, 808 tests

| File | Tests | What it tests, and how |
|---|---|---|
| [`test_baseline.py`](../tests/e2e/test_baseline.py) | 4 | The stub against the sysroot of each glibc baseline (two items per baseline): the default runtime's stub needs no glibc symbol above 2.34, and a stub linked here against Rocky Linux 8.10's sysroot none above 2.28; `launcher/glibc.c` compiles against both header sets (its `_dl_find_object` wrapper compiled out where the headers lack it), and each sysroot's `build_flags` compile and link a C program against it. |
| [`test_build.py`](../tests/e2e/test_build.py) | 55 | `fsbpyr build` on the fixture projects: `hello` with `inspect --json --verify` and its SBOM (zstd with its CPE among the runtime's components), `clicky` from each lock source, the README quickstart, the determinism and self-hosting tests, `cext`'s extension rebuilt against the glibc sysroot, workspaces, dependency groups, editable requirements installed as regular packages, the smoke test and `--smoke-args`, `--strict` (VCS requirement lines refused before uv runs), `pylock.toml` hashes, Poetry 1 projects (`foreign/poetry1`, `foreign/poetry1-unpackaged`), projects with no scripts (a `__main__` package, one guarded module, both together refused), legacy `scripts=` files (a read next to `__file__` in a script's main guard reported), compile warnings of the project's own files (`escapes`), a setuptools project's tree left unchanged, the output directories a failed build created removed, build errors. |
| [`test_cache_root.py`](../tests/e2e/test_cache_root.py) | 30 | The launcher's cache root checks (marked `sanitized`): roots below directories others may write to, symlinks, symlink loops, `..`, relative roots, user namespaces, a symlink of another user made with `podman unshare`; and an app whose native extension is extracted at its import, which a refused cache directory stops with one line on stderr, `fsbpyr: cannot start <executable>: refusing cache directory <path>: ...`, and exit 70, as it stops `FSBPYR_SELFTEST=natives`. |
| [`test_compat.py`](../tests/e2e/test_compat.py) | 41 | The `compat` fixture's matrix (one check per package, compared with the venv's run, HTTP clients against a local server; none for a package without a wheel for the Python), the `mp` fixture's start methods, helper interpreters and the instance limit (in `contain.run`), forged multiprocessing children, a payload script started through `sys.executable` finding its sibling module, `sys.argv[0]` as `execve` got it (argparse's `prog` its basename), the `[sys.executable] + sys.argv` restart idiom, a missing module not blamed on site initialization, the `datafiles` checks, and the build information tracing every dist to the lock. |
| [`test_corpus.py`](../tests/e2e/test_corpus.py) | 66 | One test per corpus app (or per app `FSBPYR_CORPUS_APPS` names) that has wheels for the Python and machine (64 apps on 3.14), the `expect` check, and the `plugins` fixture (see [The corpus](#the-corpus)). |
| [`test_digests.py`](../tests/e2e/test_digests.py) | 4 | The launcher's digest routines through `probes/digests.c`, built with passes of 3 leaves: files hashed together and content given a piece at a time have the Python reader's `tree_digest`. |
| [`test_dispatch.py`](../tests/e2e/test_dispatch.py) | 2 | A project, and a single script whose PEP 723 block, that excludes the running Python builds through the variant: fsbpyr built from the checkout on another Python and served by `FakeReleases`. Needs that Python installed with uv, and builds its runtime in the cache when there is none. Marked `version_rule`. |
| [`test_due.py`](../tests/e2e/test_due.py) | 21 | When a cache collection is due, through `probes/due.c` (with `statvfs` wrapped): the started process detached at idle priority, one start of many claiming the run, `FSBPYR_GC` set, the root where the boot code finds it. |
| [`test_edges.py`](../tests/e2e/test_edges.py) | 156 | The launcher, release and sanitized, on every edge case that leaves an executable (63 of the 66) and every path case: it refuses to start (exit 70), refuses when the record is used, or accepts, as the table says; it never crashes, and the sanitized launcher reports nothing. The app walks its own records with `tests/support/walk.py`. |
| [`test_el8.py`](../tests/e2e/test_el8.py) | 14 | `fsbpyr build --glibc 2.28` on real runtimes (`runtime` and `el8_runtime`): each stub carries the runtime id it was linked as, and the two differ; the build summary and `inspect` say `glibc >= 2.28` and `baseline   glibc 2.28`; the build information records `glibc_baseline`, the EL8 runtime and a `rocky8-glibc2.28-` sysroot, while a default build records none of these and stays reproducible; a cryptography wheel at manylinux_2_28 where the default build takes a manylinux_2_34 one; `hello` and the cryptography app run on the host; `cext`'s extension rebuilt against the EL8 sysroot. Then a release fsbpyr, this checkout built from a copy tagged v1.2.0: with the stub downloaded from a `FakeReleases` companion release `el8-v1.2.0` (and cached under its runtime id, not the tag), or placed beside it with no network, it builds the same app as the cache's glibc 2.28 runtime; without the companion release it stops with exit 1 and the plain message, and the default stub under the EL8 name is refused. |
| [`test_el8_use_cases.py`](../tests/e2e/test_el8_use_cases.py) | 4 | `--glibc 2.28` and the `glibc` setting with the other ways to build, each built for EL8, inspected (`glibc_baseline`, `glibc`) and run on the host: a directory of scripts (from the option, or from a `pyproject.toml` that holds settings only) with its command links, a single script whose PEP 723 block sets `glibc = "2.28"`, and the `multi` fixture with its links. Needs `el8_runtime`; in no EL8 make target, so `make test` runs it. |
| [`test_file_overlay.py`](../tests/e2e/test_file_overlay.py) | 14 | Files next to a script, on a directory of scripts whose `dist/` also holds the host's files: a script reads the file deployed beside the executable, while its code is the payload's (the `pylib` hack, a stale copy beside the executable never imported, tracebacks); argparse's `prog` is the name the command was started by; multiprocessing with spawn and forkserver; `inspect.getsource` from the payload or from a copy beside the executable; a script restarting itself through its `__file__`; the note for a missing file; a single script alike; a packaged project's `__file__` stays inside the executable. |
| [`test_find_object.py`](../tests/e2e/test_find_object.py) | 2 | The stub's `_dl_find_object` wrapper (`launcher/glibc.c`) through `probes/find_object.c`: glibc's own where it has one, the program headers naming the same object as on glibc 2.34. |
| [`test_fuzz.py`](../tests/e2e/test_fuzz.py) | 2 | The libFuzzer harness `tests/fuzz/payload_fuzz.c` with ASan and UBSan: each edge case once, then a run of `FSBPYR_FUZZ_SECONDS` exploring from them; the harness is started through a shell, so that it does not take over the test process's peak RSS. |
| [`test_gc.py`](../tests/e2e/test_gc.py) | 15 | Collection by the launcher: the tree a process holds, `FSBPYR_GC=1` collecting instead of running, a background run a day after the last, `FSBPYR_GC=0`, a user's directory never collected, and the root the boot code uses (the passwd entry's home) found when `HOME` is unset, empty or relative. |
| [`test_imports.py`](../tests/e2e/test_imports.py) | 16 | The demo app as a real executable (marked `sanitized`): the contract of the C module against the stand-in, extracted files never synced to disk (strace), boot imports only built-in, frozen and startup modules, resources, `pkgutil`, metadata, tracebacks and `inspect`, the working directory never shadowing payload sources. |
| [`test_install_links.py`](../tests/e2e/test_install_links.py) | 9 | `FSBPYR_INSTALL_LINKS=DIR` on a built directory of scripts: links to the executable's real path, with `name.py` aliases for script commands (extensionless ones too), each running its command; the app not run, and a second run changing nothing; what is in the way reported and kept; no links from the background collection's child; a missing or unwritable directory an error before any link; a single script's name and alias. |
| [`test_interrupted_build.py`](../tests/e2e/test_interrupted_build.py) | 2 | A real `fsbpyr build` process killed with SIGTERM, or running out of room under a file-size limit, leaves the output as it was and no temporary file. |
| [`test_large.py`](../tests/e2e/test_large.py) | 3 | A package with a 5 GiB sparse file of zeros builds (compressed and raw), runs, streams and extracts, and `inspect --verify` reads it, within a bound on resident memory. |
| [`test_launcher_build.py`](../tests/e2e/test_launcher_build.py) | 15 | Each launcher source compiled without LTO has no variable-length array and no frame over the limit; the check catches both; the sanitized launcher really carries ASan and UBSan; the launcher links reproducibly. |
| [`test_loose_directory.py`](../tests/e2e/test_loose_directory.py) | 37 | Directories of loose scripts built as one executable with a link per command, each printing what `python3 <script>` prints with the same dependencies (local directories, no network): names that are no Python names, a sibling shadowing the standard library as for `python3`, the command from the first argument, absolute links from elsewhere, the `pylib` hack and `..` paths, a sibling started through `sys.executable`, from a mangled directory or through its link while other helper interpreters stay refused, an undeclared import skipping its command, `--strict`, no user code run by the build, PEP 723 blocks, the venv rung (with uv), multiprocessing from a command, a file read next to a script (a read in the main guard of a script that `--entry` leaves out not reported, so `--strict` builds), and a directory going from one command to several replacing its own earlier output, and back replacing its link, not what the link names, while any other file at a command's link still stops the build. |
| [`test_multicall.py`](../tests/e2e/test_multicall.py) | 22 | The `multi` fixture's two console scripts as one executable: a relative link per command (none for a one-command build of `hello`), the build summary and `inspect`, a link started by path, bare name or relative path, the command from the first argument, without a command the list and exit 2, a renamed copy, the smoke test and `--smoke-args`, a file in the way failing the build, sibling links started with `-m` or `-c`, `sys.executable` still refused as a helper interpreter, every multiprocessing start method from a link or the first argument, and a hard link that keeps the old build. |
| [`test_natives.py`](../tests/e2e/test_natives.py) | 16 | The `heavy` fixture (numpy, orjson, cryptography, pydantic, onnxruntime): every subcommand against the venv, licences, `FSBPYR_SELFTEST=natives`, the closure a first run extracts, xz natives, concurrent first runs, a damaged cached extension repaired, a Python-only change reusing the native generation. Deselected on a Python that onnxruntime has no wheel for. |
| [`test_pid1.py`](../tests/e2e/test_pid1.py) | 3 | An app as process 1 of a PID namespace (`unshare --user --map-root-user --pid --fork --mount-proc`, else `podman run` of `debian:13`): SIGTERM ends it with status 143, and an app's own handler is kept; an app that is not process 1 dies of the signal. |
| [`test_pipfile_lock.py`](../tests/e2e/test_pipfile_lock.py) | 5 | The `foreign/pipenv` fixture: a directory of scripts with a `Pipfile` and its lock builds from the lock's `default` section; a stale lock warns (an error with `--strict`); a changed hash fails the build; with no Python file, with or without the `Pipfile`, the directory builds the tool, not its dependencies. |
| [`test_portability.py`](../tests/e2e/test_portability.py) | 35 | `hello`, `heavy`, `heavy-pip` (heavy kept with a hash-pinned `requirements.txt`; both left out on a Python without an onnxruntime wheel) and `compat` run in each distribution image with `podman run --network=none`; `FSBPYR_SELFTEST=natives` there; the system libraries and glibc requirement the build reports against `llvm-objdump`. Two items (`test_el8_apps_run_on_el8`) run `hello` and a cryptography app built `--glibc 2.28` (from the prebuilt set's `hello-el8` and `native-el8`, else built with `el8_runtime`) on `rockylinux:8` and `almalinux:8`; they exist only where a prebuilt set has those apps or there is none. In the [EL8 test mode](#the-el8-test-mode) the matrix adds those two images, its apps are glibc 2.28 builds, and the two items are not generated. Marked `podman`. |
| [`test_restore.py`](../tests/e2e/test_restore.py) | 10 | The cache sync's writers through `probes/restore.c` (with `renameat`, `linkat` and `pthread_create` wrapped): every file written whole, helper threads (none with one usable core), unnamed files linked into place (renamed where the file system cannot link them), renames from the scratch directory, the thread limit, the first failure in path order. |
| [`test_runtime_app.py`](../tests/e2e/test_runtime_app.py) | 87 | Small apps from `build_app`, most tests marked `sanitized`: exit codes, tracebacks, signals, set-gid refusal, the whole-file check (any changed byte stops the start), damaged records, unsafe paths, address-space and file-size limits, the block cache, a set-uid start by the file's owner not taken for a privileged one, the payload offset on a 64 KiB boundary. |
| [`test_scripts.py`](../tests/e2e/test_scripts.py) | 21 | The `scripts` fixture (the layout of `uv init --no-package`, no build system) runs as `uv run main.py` does: nothing written into the source tree, two builds the same bytes, a single module with `[project.scripts]` giving the output and exit status it gave when setuptools' fallback packaged it, top-level code never run by the build, several guarded modules as one executable, two files with one command name, imports the executable lacks (file and line, `--strict`, removed stdlib modules), a file below a directory that is no Python name, a file read beside the executable, a module's read in its main guard reported when `--entry` runs it and only then; and the `foreign` Poetry 2 and PDM projects: the lock, an extra, a stale lock, a changed hash. |
| [`test_single_script.py`](../tests/e2e/test_single_script.py) | 12 | One script built as an executable (local directories as dependencies, no network): it prints what `python3 <script>` prints with the same dependencies; `dist/<stem>` beside the script by default; only the script's closure in a crowded directory; an extensionless `uv run` script; the block's dependencies, settings (unknown keys rejected) and `requires-python`; an import nothing provides skipping the script with the freeze hint; and, with uv, a venv beside a script with no block supplying the pins, and a script lock locking the build (failing when the block has outgrown it) with nothing written beside the script; a sibling's read in its main guard not reported, so `--strict` builds. |
| [`test_slots.py`](../tests/e2e/test_slots.py) | 29 | The instance limit through `probes/slots.c` (with `geteuid` wrapped): slots held and freed, forks and execs, files that are not ours alone, the override and its malformed values, the default. |
| [`test_stream_windows.py`](../tests/e2e/test_stream_windows.py) | 21 | The launcher's blob streams through `probes/streams.c`, built with 2 MiB windows: every codec around window boundaries, from the start and after seeks. |
| [`test_streaming.py`](../tests/e2e/test_streaming.py) | 4 | Files past the old 1 GiB cap extract in bounded memory, runs of zeros become holes, resources opened as files stream and seek, `_fsbpyr.read()` returns what was asked. |
| [`test_stub_hardening.py`](../tests/e2e/test_stub_hardening.py) | 6 | The built stub read with `readelf` and `objdump`: position independent, glibc libraries only, bound at load time, RELRO, non-executable stack, stack protector. |
| [`test_tool_binary.py`](../tests/e2e/test_tool_binary.py) | 17 | A directory with no Python file and a requirements file built as one executable of the tools' console scripts, with the real pyflakes, pycodestyle and flake8 (pinned, from the index through uv's cache) and local distributions for the odd cases: a link per command printing what the tool prints under uv, the command from the first argument, the tools and no script payload in the executable, their licences, a freeze giving the tool's commands alone, `pylock.toml`, a directory named like a tool, `--requirements`, `--entry`, gui scripts, meta-packages, the errors for no script and for clashing names, a venv never read, a directory with a script keeping its scripts, and the note for a library that starts helper interpreters. |
| [`test_unbuffered.py`](../tests/e2e/test_unbuffered.py) | 8 | `PYTHONUNBUFFERED` set and non-empty: a line printed to a pipe arrives at once and survives SIGTERM; unset or empty, the pipe is block-buffered; `JOURNAL_STREAM` naming the stdout pipe makes it line-buffered, another or a malformed value leaves it block-buffered. |

## Support modules

| Module | Provides |
|---|---|
| [`apps.py`](../tests/support/apps.py) | `demo_app()`: the demo application (packages, a namespace package, resources, metadata, natives that are never loaded, damaged xz natives, thousands of small files for the cache sync's batches and chunks), with `contract.py` and `probe.py` packaged as modules; `library()`, a compressible stand-in for a native library. |
| [`baseline.py`](../tests/support/baseline.py) | `BASELINE`, the glibc baseline the tests build for (`FSBPYR_TEST_GLIBC`, else the default, 2.34), `EL8` (whether that is the [EL8 test mode](#the-el8-test-mode)), and `as_tuple` (`"2.28"` as `(2, 28)`). |
| [`cache.py`](../tests/support/cache.py) | A pure-Python mirror of the launcher's cache sync (`launcher/cache.c`), step by step, used by the stand-in; `take()` for tests of the collector. |
| [`contain.py`](../tests/support/contain.py) | `run()`: a command run contained, with `Runaway` and `Leftover` (see [Containment](#containment-of-app-runs)). |
| [`contract.py`](../tests/support/contract.py) | The contract battery for the `_fsbpyr` API, standard library only, run against the C module and the stand-in (the `execfn` of `info()`, the path the process was started by, left out). |
| [`corpus.py`](../tests/support/corpus.py) | The corpus: app list and the selection `FSBPYR_CORPUS_APPS` makes (`selected()`), `commands.toml` reading, settings, wheel gaps and the apps that run on a Python and machine (`runnable()`), per-app `Result`, its recording and the session summary. |
| [`differential.py`](../tests/support/differential.py) | `make_venv`, `run_both`, `assert_same` (see [Differential testing](#differential-testing)). |
| [`downloads.py`](../tests/support/downloads.py) | The runtime's sha256-pinned downloads (the CPython distribution and the sysroot's packages: Debian debs for 2.34, Rocky Linux RPMs for 2.28) and built runtimes kept in `FSBPYR_TEST_DOWNLOADS`: `pinned`, `fill` (the sysroots of every baseline), `seed`, `keep`, `build_kept_runtime`, `seed_runtime`, `prune_runtimes`, each for a glibc baseline (the default unless given), and `pbs_distribution` (a Python's pinned PBS distribution, extracted, for the integration tests). Only files that match their pin go in or out. |
| [`edges.py`](../tests/support/edges.py) | The edge-case table: `edge_app` (every record kind and codec), `layout` (where each part of an intact executable is), `CASES` (one field corrupted each, the digest restamped so every check behind the integrity check is reached, with what the reader and the launcher do), `PATH_CASES`, `apply`, `refused`. |
| [`elf.py`](../tests/support/elf.py) | `make_elf`: minimal ELF64 shared objects with chosen `DT_NEEDED`, RPATH, RUNPATH, SONAME, version needs, machine and type. |
| [`images.py`](../tests/support/images.py) | The portability matrix's twelve container images (glibc 2.34 to 2.44, as its comments say), `EL8_IMAGES` (`rockylinux:8` and `almalinux:8`, glibc 2.28, both with an arm64 variant), those without an arm64 variant, and `images_for(machine, glibc)`, which adds the EL8 images for glibc 2.28. |
| [`locks.py`](../tests/support/locks.py) | What a `uv.lock` installs on Linux for one Python and machine (markers and extras evaluated), and which of those packages have no wheel usable at the baseline of `baseline.py`: manylinux_2_34, or manylinux_2_28 in the EL8 test mode. |
| [`no-wheels.txt`](../tests/support/no-wheels.txt) | Packages without a wheel per Python, one Python's X.Y and packages per line. |
| [`probe.py`](../tests/support/probe.py) | Run inside the demo executable: exercises `importlib.resources`, `pkgutil`, `importlib.util`, `importlib.metadata`, `inspect` and `linecache`, and prints the results as JSON. |
| [`readme.py`](../tests/support/readme.py) | `quickstart()`: the README quickstart's shell lines. |
| [`releases.py`](../tests/support/releases.py) | `FakeReleases`: a GitHub release downloads server on 127.0.0.1 that serves its files by path (`/vX.Y.Z/ASSET` for a release's assets, `/el8-vX.Y.Z/ASSET` for the glibc 2.28 stubs and their sums file) and logs the requests. |
| [`requires.py`](../tests/support/requires.py) | What the tests need from the machine: `TOOLS` (each tool's commands, Debian package and use), `tool()` and the `requires_tools` mark `tools()`, the environment conditions and `unmet()`, each failing with one line; run as a module (`make test-prereqs`), everything the full suite lacks (see [When the machine lacks something](#when-the-machine-lacks-something)). |
| [`rpms.py`](../tests/support/rpms.py) | Synthetic RPM packages for the RPM reader's and the sysroot's tests, with no network and no `rpm` tool: `rpm` (a lead, a signature and a main header naming the payload's format and compressor, and an xz-compressed payload, or given bytes), `cpio` (a newc archive), `header`, and the entries `file` (hard links by inode), `directory` and `symlink`. |
| [`sbom.py`](../tests/support/sbom.py) | Loads `tools/release/sbom.py` as a module (`tools/` is no package). |
| [`standin.py`](../tests/support/standin.py) | `StandIn`: the `_fsbpyr` built-in module in pure Python over the validating payload reader, with `Stream`; its behaviour must match `launcher/module.c`, which the contract checks. |
| [`stderr.py`](../tests/support/stderr.py) | `noise()` and the few patterns an end-to-end check allows on an app's stderr (onnxruntime's `hostname`/`blkid` and host warnings, trace lines). |
| [`walk.py`](../tests/support/walk.py) | `run(fsb)`: uses every record of a payload through the `_fsbpyr` API (reads, closures, module tree, syncs) and lists what was refused; standard library only, since it is packaged into the executables it walks. |
| [`wheels.py`](../tests/support/wheels.py) | Reads `no-wheels.txt`; `missing()`, and `needs()`, the `needs_wheel` mark by which `tests/conftest.py` deselects a test. |

## Probes and the fuzzer

Each probe is a C program that `LauncherBuild.compile` links with the launcher sources it needs.
Small build-time values make cases reachable with small inputs, and `-Wl,--wrap=` replaces a
system call the test needs to control. Each probe's header comment lists its commands and
switches.

| Program | Launcher sources | Build flags | Test |
|---|---|---|---|
| [`probes/digests.c`](../tests/probes/digests.c) | `integrity.c`, `parallel.c` | `-DFSB_PASS_LEAVES=3u`, ASan, UBSan | `test_digests.py` |
| [`probes/due.c`](../tests/probes/due.c) | `due.c`, `way.c` | `--wrap=statvfs`, UBSan | `test_due.py` |
| [`probes/find_object.c`](../tests/probes/find_object.c) | `glibc.c` | `--wrap=_dl_find_object`, `--wrap=dlvsym` | `test_find_object.py` |
| [`probes/restore.c`](../tests/probes/restore.c) | `cache.c`, `way.c`, `payload.c`, `integrity.c`, `parallel.c` | `--wrap=renameat,--wrap=linkat,--wrap=pthread_create`, ASan, UBSan | `test_restore.py` |
| [`probes/slots.c`](../tests/probes/slots.c) | `slots.c` | `--wrap=geteuid`, UBSan | `test_slots.py` |
| [`probes/streams.c`](../tests/probes/streams.c) | `payload.c`, `integrity.c`, `parallel.c` | `-DFSB_STREAM_WINDOW` of 2 MiB, ASan, UBSan | `test_stream_windows.py` |
| [`fuzz/payload_fuzz.c`](../tests/fuzz/payload_fuzz.c) | `payload.c`, `integrity.c`, `parallel.c`, `cache.c`, `way.c` | `-fsanitize=fuzzer,address,undefined`, `-DFSB_STREAM_WINDOW` of one digest leaf | `test_fuzz.py` |

The fuzz harness visits every record of every table (up to 4,096 per table), runs the lookups and
the path check, and decodes every blob up to 16 MiB, whole and through a stream; whatever the
input, the launcher's code must refuse it or read it, never crash. Its seed corpus is the intact edge-case
executable and every case of `edges.CASES`, built with `FAKE_RUNTIME`. The fuzzing run uses
`-rss_limit_mb=2048 -timeout=30 -max_len=1048576`. The test starts the harness through a shell:
started directly, it would take the test process's peak RSS as its own, which libFuzzer compares
with `-rss_limit_mb`.

## Fixture projects

| Project | What it is | Used by |
|---|---|---|
| `hello` | No dependencies. | `test_build.py`, `test_interrupted_build.py`, `test_multicall.py`, `test_portability.py`, `test_el8.py`, `tests/unit/test_test_modes.py` |
| `clicky` | click, rich and package data, with `uv.lock`, `pylock.toml` and `requirements.txt`. | `test_build.py` |
| `cext` | The project's own C extension, built from source (`setup.py`). | `test_build.py`, `test_el8.py` |
| `compat` | The compatibility matrix: click, typer, rich, httpx, requests, certifi, pydantic, jinja2, pyyaml, numpy, cryptography, orjson, tzdata, sqlalchemy, setuptools and more. | `test_compat.py`, `test_portability.py`, `tests/unit/builder/test_manifest.py` |
| `datafiles` | Packages that read data files next to their modules: botocore, pytz, babel, matplotlib. | `test_compat.py` |
| `escapes` | A module whose normal string has an invalid escape sequence: a compile warning of the project's own file. | `test_build.py` |
| `foreign` | Locks of other tools, written by hand as its README explains: `poetry2`, `poetry1`, `poetry1-unpackaged`, `poetry-legacy`, `pdm` and `pipenv` (a directory of scripts with a `Pipfile` and its lock). Each depends on click and rich, on colorama for Windows only, on packaging in the extra `fancy` and on iniconfig in a dev group. | `tests/unit/builder/test_foreign.py`, `tests/unit/builder/test_pipenv.py`, `test_build.py`, `test_scripts.py`, `test_pipfile_lock.py` |
| `heavy` | A light CLI with heavy native dependencies: numpy, orjson, cryptography, pydantic, onnxruntime. | `test_natives.py`, `test_portability.py` |
| `mp` | Multiprocessing start methods re-executing the binary; also a payload script started through `sys.executable` (`mp/scripts/`), `sys.argv[0]`, the restart idiom and a missing module. | `test_compat.py` |
| `multi` | Two console scripts, `multi-alpha` and `multi-beta`, built as one multi-command executable. | `test_multicall.py`, `test_el8_use_cases.py` |
| `native` | Two small native dependencies, orjson and markupsafe; its description names the VM smoke test of `tools/vm-smoke.py`. | No test file: the VM smoke jobs of [`full-suite.yml`](../.github/workflows/full-suite.yml) and [`el8.yml`](../.github/workflows/el8.yml) build it and run it with `tools/vm-smoke.py` |
| `plugins` | A host and two plugin distributions: entry points and a PEP 420 namespace. | `test_corpus.py` |
| `scripts` | The layout of `uv init --no-package`: `main.py`, `utils.py` and a `helpers` package, no build system. | `test_scripts.py` |

`CONTRIBUTING.md` asks that every packaging fix (a module, file or library that a built
executable lacks or gets wrong) add a fixture project or a corpus app whose test fails without
the fix.
