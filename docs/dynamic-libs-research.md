# fsbpyr: OS libraries instead of built-in copies (`--dynamic-libs`), after verification

A research report, not a plan. The owner decided on 2026-10-26 not to implement anything at this point. Nothing here is in docs/IMPLEMENTATION_PLAN.md, and the decisions in section 10 are open.

Date: 2026-10-26. Repository: HEAD d76209e on branch `use-cases` (v0.25.0 plus the unreleased Phase 12). Runtimes: PBS 20261021 with CPython 3.12.15, 3.13.17, 3.14.9 and 3.15.1. Every build and run was on x86_64; the only aarch64 evidence is symbol-set and rpm queries (sections 9.7 and 9.8).

Inputs, in two rounds of agents:

- **Round 1** had seven lenses: plumbing, loader, linker, ABI, an end-to-end spike, outside research and wheels.
- **Round 2** had three designs: A reuses PBS's objects, B rebuilds the modules from CPython source, C rebuilds CPython per distribution. Each design had a feasibility critic and a cost critic.
- **Re-checks for this report:** the author re-ran the main result (section 6.2) and re-read the critics' strongest refutations.

The prototypes, logs and container experiments lived in a scratch directory, which was deleted after this report was written. This report is the record. Appendices A to C hold what is needed to redo the core experiments.

Status labels:

- **experiment-confirmed**: an agent or the author ran it (podman containers with pids and memory limits) and saw the result;
- **code-confirmed**: read in the source at HEAD;
- **outside research**: read from upstream sources, documentation or GitHub, not run here;
- **inferred**: reasoned from evidence, not run;
- **unverified**: neither.

File references without a leading `tests/`, `tools/`, `docs/` or `.github/` are under `src/fsbpyr/`.

Line counts, calendar days and CI minutes are estimates, not measurements, unless a section says otherwise.

---

## 1. Summary

- **Request.** `fsbpyr build --dynamic-libs LIST|all` should make an app use the target system's copies of the libraries PBS links statically into the stub (OpenSSL and the rest), as it already does for glibc. Only the interpreter's libraries are in scope, never libraries inside wheels. The default stays as it is.
- **Goal.** Enterprises standardised on one or two distributions (RHEL 9 and its rebuilds, Ubuntu 24.04) should get library security fixes through `dnf update` or `apt upgrade` and an app restart. Today they rebuild and redeploy every app. Naming the target distributions as a build parameter is acceptable to the owner.
- **Verdict: feasible.** One set of 13 shared extension modules works unchanged on RHEL 9, Ubuntu 24.04 and Debian 12 (experiment-confirmed). They are compiled from the pinned python.org source against Rocky 9.2 headers. The pack also loaded on AlmaLinux 10, Ubuntu 26.04 and Debian 13.
- **Recommended design** (section 8):
  - build that module pack in the runtime build;
  - embed it in every fsbpyr executable;
  - let `--dynamic-libs` put the chosen modules into the app's payload;
  - at start, load them `RTLD_LOCAL` through the existing native cache instead of the built-in copies.

  There are no per-distribution binaries, no new release assets and no compiler on the app-build host.
- **What reaches the app through OS updates** (experiment-confirmed on Rocky 9.8 and Ubuntu 24.04): OpenSSL, SQLite, zlib, bzip2, xz, zstd (Python 3.14 and later), libffi, expat, libuuid and ncurses. libedit (readline) works with one build per distribution family. mpdecimal stays built in, because Ubuntu has no package for it.
- **What still needs a rebuild:**
  - CPython's own security fixes;
  - fsbpyr's own fixes;
  - mpdecimal and, without a target family, libedit;
  - the launcher's private SHA-256, zstd and xz code, which reads only the app's own payload.
- **Effort** (inferred): about 1.2 to 1.4 times the EL8 work (EL8: 4,183 inserted lines over six commits, counted with git). That is about 5,000 to 6,000 lines, or 3 to 4 days with agents including aarch64 and CI shakeout. One critic put the same recipe at 1.55 times (about 6,500 lines).
- **CI** (inferred by the author; no critic costed this delivery): about +30 to +60 runner-minutes per full-suite run, against the measured 488. No new workflow and no new wall-clock time.
- **Release size** (inferred): about 0.35 MB more per fsbpyr executable.
- **Rejected:**
  - linking the stub itself against the system libraries ("RELINK", section 4.1);
  - rebuilding CPython per distribution (C-full, section 7). The rebuilt interpreter core needs only libc and libm, so a rebuild gains nothing for this goal. It also breaks byte-reproducibility and makes fsbpyr own CPython's security latency.
- **Hazards the feature brings** (section 9):
  - semantic floors: RHEL 9's SQLite 3.34.1 and expat 2.5.0 behave differently from PBS's 3.53.1 and 2.9.0;
  - an exec-capable writable cache becomes necessary even for stdlib-only apps;
  - missing host libraries must fail loudly;
  - a wheel that bundles an unhashed `libssl.so.3` can capture the system `_ssl`;
  - on RHEL, TLS follows the system crypto policy;
  - EL8 (`--glibc 2.28`) cannot take OpenSSL or zstd this way.
- **Not tested anywhere:** aarch64, a real RHEL 9.0 or 9.1 userland, a real FIPS kernel, SELinux enforcing, and real CI timings.

## 2. The request and its constraints

The owner's request (2026-10-26) was an investigation of the impact and effort only:

- `--dynamic-libs` lists the libraries to take from the system, or `all`;
- the default build stays as it is.

A follow-up gave the purpose. The goal is less deployment overhead for enterprises with a small number of target distributions. Naming those distributions as a parameter is fine. Rebuilding CPython per distribution is less attractive but had to be investigated.

Standing constraints that shaped the designs:

- the app-build host needs only uv, with no compiler, and fsbpyr installs nothing on the user's machine;
- CI cost is the owner's main objection to per-target matrices. EL8 got its own decoupled workflow (`el8.yml`) with default-Python-only tests;
- releases are byte-reproducible (README, "Verifying a release");
- requested features are built completely, and any deferral is listed up front.

## 3. What fsbpyr links today

The stub links PBS's "full" archives (LLVM bitcode) with clang and lld 23 and `-flto=full`. It uses the Debian 12 sysroot (glibc 2.36) at a glibc 2.34 baseline, or glibc 2.28 for EL8 with `--glibc 2.28`.

These libraries are linked in statically (experiment-confirmed from PBS 20261021's `PYTHON.json` and the link inputs; the launcher column is code-confirmed):

| Library | PBS version | Modules that use it | Also used by the launcher |
|---|---|---|---|
| OpenSSL (libssl, libcrypto) | 3.5.9 | `_ssl`, `_hashlib` | libcrypto: SHA-256 of the payload integrity digest (D31), at every start |
| SQLite | 3.53.1 | `_sqlite3` | |
| zlib | 1.3.2 | `zlib`, `binascii` | |
| bzip2 | 1.0.8 | `_bz2` | |
| xz (liblzma) | 5.8.4 | `_lzma` | xz-compressed natives |
| zstd | 1.5.7 | `_zstd` (3.14+), `_remote_debugging` (3.15) | payload decoding, at every start |
| libffi | 3.8.0 | `_ctypes` | |
| mpdecimal | 4.0.0 | `_decimal` | |
| expat | 2.9.0 | `pyexpat` | |
| ncurses (ncursesw, panelw) | 6.5 | `_curses`, `_curses_panel` | |
| libedit | | `readline` | |
| libuuid | | `_uuid` | |

Berkeley DB is only in `_dbm`, a lib-dynload module that fsbpyr leaves out, so it is not in apps.

Facts the designs rely on:

- The stub exports only `Py*` and `_Py*` (`runtime/stub.py:57`, code-confirmed). Its `NEEDED` list is libc and libm.
- The inittab is PBS's `build/Modules/config.c`. The launcher calls `PyImport_AppendInittab` once, for `_fsbpyr` (`launcher/main.c:144`, code-confirmed). That leaves `PyImport_Inittab` a mutable heap array before `Py_Initialize`.
- Neither fsbpyr's boot nor the STARTUP module set imports any of the 16 modules (experiment-confirmed). `importlib.metadata` pulls in zlib, bz2, lzma and zstd, and `secrets` pulls in `_hashlib`.
- PBS's module objects are compiled with `-DPy_BUILD_CORE_BUILTIN`, and `PyInit_*` has default visibility. `pyexpat`, `_ctypes` and `_remote_debugging` reference the hidden thread-local `_Py_tss_tstate`, which the stub does not export. So these three cannot become shared modules from PBS's objects; a source build with `-DPy_BUILD_CORE_MODULE` avoids that (experiment-confirmed).
- Library-backed modules can be loaded as payload extensions through a module record pair: `Module(EXTENSION)` and `File(NATIVE_EXT, Blob.zstd(digest=True), 0o755)`. `PayloadExtensionLoader` extracts the `.so` into the per-app native cache and `dlopen`s it `RTLD_LOCAL` (code-confirmed).
- The scanners do not see the bundled copies (outside research, inferred from tool documentation). Syft's OpenSSL classifier matches only files named `openssl`, and it has no SQLite or zlib classifier. Trivy does not detect self-compiled C libraries. With system libraries, scanners attribute them to distribution packages.
- PBS ships OpenSSL security releases fast (outside research). 3.5.9 and 3.5.8 came the same day, 3.5.7 after about 1.7 days and 3.5.6 after 7 days. The burden today is the fsbpyr release, then the app rebuild, then the redeploy.

## 4. Mechanisms considered

### 4.1 RELINK: the stub links the system libraries (rejected)

The stub would carry `DT_NEEDED` entries for `libssl.so.3` and the others. Round 1 rejected this on evidence (experiment-confirmed unless marked):

1. **Global-scope interposition.** A real wheel's library bound its references to the system copy although its own copies were also loaded. The wheel was psycopg_binary's libpq, with hashed `libssl`/`libcrypto` names. 41 libssl and 59 libcrypto references went to the system copy.
2. **Every library at startup.** Every app needs every selected library at startup, even when unused. Minimal images lack several of them.
3. **Smoke test** (inferred). The build's smoke test runs the new executable on the build host, so it would die in ld.so on a build host that lacks a soname. The spike saw this failure only when the finished app ran on bare debian:12.

### 4.2 MODULE: library-backed modules as shared extensions (adopted)

The modules ship in the payload as extension modules and are loaded `RTLD_LOCAL` from the native cache.

- **Wheel isolation** (experiment-confirmed). With the system `_ssl` loaded, psycopg_binary's libpq kept all of its 59 libcrypto and 41 libssl bindings on its own hashed copies. `_ssl.so`'s 106 libcrypto and 101 libssl bindings went to `/lib64`.
- **No clash with the stub's copies** (experiment-confirmed for design B's packs of all four Pythons; not checked for design A's pack). No global symbol of any module collides with the stub's exports. A dynamic symbol table check found 0 `SSL_`, `EVP_`, `sqlite3_`, `inflate`, `ffi_` and similar symbols in the stub.
- **Lazy loading.** Each library is needed only when its module is imported, unlike RELINK.

### 4.3 Making the built-in copy lose

A shared module of the same name is ignored while the built-in copy is in the inittab. In a control run, the built-in `_ssl` won although the payload carried `_ssl.so` (experiment-confirmed). Two ways out were prototyped:

- **Launcher inittab drop** (design A; Appendix C.2). The launcher compacts `PyImport_Inittab` before `Py_Initialize` and removes the replaced names. CPython copies the table at initialisation, so the removal holds. `sys.builtin_module_names` then tells the truth.
  - A prototype driven by an environment variable ran on 3.14 and 3.15 through the real payload path (experiment-confirmed). On 3.12 and 3.13 the drop is inferred from the unchanged `PyImport_Inittab` API, because those app builds failed for an unrelated reason.
  - The production form, not written, is a payload flag `FSB_MODULE_REPLACES_BUILTIN` in the spare bit of the module flags (code-confirmed: `format.h:68` uses bits 1 and 2, and `flags` is a `uint8_t` at `format.h:126`).
  - It needs a format bump from 7 to 8 (`FSB_FORMAT_VERSION`, `format.h:18`). The Python reader rejects unknown flag bits (`payload/reader.py:116-119`, code-confirmed), and an old launcher that ignored the flag would silently keep the built-in copy. The bump also touches `tests/integration/test_launcher_format.py`, `tests/unit/payload/test_integrity.py`, `test_xz.py`, the `tests/fuzz/payload_fuzz.c` seeds and docs/format.md, about 120 to 150 lines (cost critic, inferred).
  - The launcher change gives every stub and runtime id new bytes once, for default builds too.
- **Boot `ShadowFinder`** (design B, experiment-confirmed on 3.14 through the real payload path; Appendix C.1). A finder at `sys.meta_path[0]` claims the built-in names that also have an `EXTENSION` record in the payload.
  - It needs no launcher or format change.
  - The static copy stays importable by anything that runs before the finder is installed. No such import exists today.
  - `sys.builtin_module_names` still lists the replaced names.

Both need `assemble.py:90-92` (`_builtin_or_frozen`) changed. It takes the built-in set from the build host's `sys.builtin_module_names`, so the app build would drop the new records or report a clash (code-confirmed).

### 4.4 Where the shared modules come from

- **A: link PBS's own bitcode objects as shared modules** (section 5), with a small compatibility shim.
- **B: compile the modules from the python.org source** of the same patch release (section 6), against old distribution headers.
- **C: rebuild CPython per distribution** (section 7).

## 5. Design A: reuse PBS's objects

**How.**

- **Link.** For each module, the objects listed in `PYTHON.json` are linked `-shared` against a floor set of library symbols, here the Rocky 9.0 vault RPMs. The link uses the stub's toolchain and flags and takes 1.6 s for all 11 modules. Two builds were byte-identical.
- **Ship.** The pack is embedded in the runtime fragment, and so in every fsbpyr executable: 184 KB xz for 3.14.
- **Activate.** The launcher's inittab drop puts the pack in charge (section 4.3).

**What can be shared.** 11 modules: `_ssl`, `_hashlib`, `_sqlite3`, `zlib`, `binascii`, `_bz2`, `_lzma`, `_uuid`, `_zstd`, `_curses` and `_curses_panel`. That covers OpenSSL, SQLite, zlib, bzip2, xz, libuuid, zstd and ncurses.

**What cannot.** Four libraries stay built in:

- expat and libffi: `pyexpat` and `_ctypes` read the hidden `_Py_tss_tstate` and fail at `dlopen`;
- libedit: RHEL's libedit lacks `rl_completion_suppress_append`;
- mpdecimal: Ubuntu has no package.

**The shim.** PBS compiled its objects against OpenSSL 3.5, so they need symbols that OpenSSL 3.0 lacks. Undefined symbols above the 3.0 floor, per Python (experiment-confirmed):

| Python | OpenSSL symbols above 3.0 |
|---|---|
| 3.12 | `EVP_MD_CTX_get_size_ex`@3.4.0, `X509_STORE_get1_objects`@3.3.0 |
| 3.13, 3.14 | the 3.12 pair and `SSL_SESSION_get_time_ex`@3.3.0 |
| 3.15 | the 3.13 set, `SSL_get0_group_name`@3.2.0 and `SSL_get1_builtin_sigalgs`@3.4.0 |

RHEL 9.0's libsqlite3 (the vault RPMs) also lacks `sqlite3_serialize` and `sqlite3_deserialize`. Design A reported that only the 9.8 package exports them. Design B's `nm -D` of the Rocky 9.8 image's library found neither, so which RHEL 9 minors have them is unresolved.

- **How it works.** A `--wrap` shim of 196 lines looks the real function up with `dlopen(soname, RTLD_NOLOAD)` and `dlsym`. If the function is missing, it falls back to the `#else` branch of CPython's own source.
- **Two imperfect fallbacks.** On OpenSSL below 3.2 and 3.4, `SSLSocket.group()` returns `None` and `ssl.get_sigalgs()` returns `['']`. CPython would raise `NotImplementedError` instead.
- **A check at build time.** It compares every undefined symbol against the floor (version nodes and exports). A new symbol in a future PBS fails the build with its name. A new shim function then costs about 1 to 2 hours, roughly one per CPython minor (inferred).

**Verified** (experiment-confirmed unless marked):

- **Platforms.** The pack loads and runs on Rocky 9.8, RHEL 9.0 libraries on a 9.8 userland (OpenSSL 3.0.1), Ubuntu 24.04, CentOS Stream 9, AlmaLinux 10, Amazon Linux 2023, ubi9-minimal and rockylinux:9-minimal.
  - ubuntu:22.04 fails: `ZSTD_defaultCLevel` is missing from zstd 1.4.8.
- **CPython's own tests pass on Ubuntu 24.04** for all four Pythons, run on a modular test stub with `PYTHONHOME`, not on the shipped stub, payload and native cache: test_hashlib, test_hmac, test_ssl, test_sqlite3, test_zlib, test_bz2, test_lzma, test_uuid, test_binascii, test_zstd, test_gzip, test_tarfile, test_shutil, test_decimal, test_asyncio.test_ssl and test_curses. That is 2,965 to 3,736 tests per Python.
  - The critic found that test_curses had run without a tty, so it skipped 95 of its 112 tests. With `podman run -t` it passes on 3.14; the other Pythons and Rocky's ncurses were not re-run.
- **On RHEL the only failures are intended ones:**
  - `test_python_ciphers`, because the crypto policy replaces Python's cipher string;
  - with 9.0 libraries, the two serialize tests.
- **crypto-policies.** The shim also wraps `SSL_CTX_set_cipher_list` and skips Python's own cipher string where `/etc/crypto-policies/back-ends/opensslcnf.config` exists. With that, the cipher sets match RHEL's `python3` under DEFAULT, FUTURE and LEGACY (61/17/61). The TLS minimum stays Python's TLS 1.2.
- **FIPS** (kernel flag simulated by bind-mounting `/proc/sys/crypto/fips_enabled`). `_hashlib.get_fips_mode()` returns 1 and contexts have 21 ciphers, as with RHEL's `python3`. `hashlib.new("md5")` and `blake2b` still work through the HACL built-ins (`_md5`, `_blake2`), where RHEL's `python3` raises.
- **Startup cost.** Importing all 11 modules costs +5 ms cold (26 against 21 ms) and +3 ms warm. A plain start costs +1 ms, and the cache holds 0.76 MB per generation for 3.14.
- **A modular stub** (the 11 modules and their objects removed) is 11.38 MB against 20.94 MB, and its app 18.52 MB against 27.84 MB. `strings` then shows no OpenSSL, SQLite, zlib or bzip2 text.

**Critics' corrections:**

- **Shim leak.** The `sqlite3_deserialize` fallback leaks the buffer (code-confirmed and measured: 3,000 failed calls grew RSS by about 290 MB). With `SQLITE_DESERIALIZE_FREEONCLOSE` SQLite owns the buffer, and the fallback never frees it.
- **Floor data unproven.** The proposed "checked-in floor symbol lists plus generated link stubs" (about 1,500 data lines, so that builds need no RPM downloads) was never written. Every verified link used the real vault RPMs.
- **Other required work:**
  - the format bump, plus the `assemble.py:92` change;
  - turning off the CA-bundle fixup for a dynamic `_ssl` (`boot/_fsbpyr_rt/fixups.py:15-60`);
  - a policy for the sanitized (ASan/UBSan, D38) stub variant (`runtime/stub.py:63-78`);
  - SBOM marking (`tools/release/sbom.py` reads `runtime_components`).
- **Cost estimates.**
  - Effort: the author said 0.9 to 1.0 times EL8. The feasibility critic said 1.2 to 1.5 times (4,800 to 6,200 lines). The cost critic said about 1.2 times in lines (about 5,200) and 1.5 times in effort. Counting the generated floor data, 1.55 to 1.7 times.
  - CI: +25 to +90 runner-minutes per full-suite run, 0 new assets.

## 6. Design B: rebuild the modules from CPython source

### 6.1 Design and results

**How** (Appendix A has the recipe). The modules of the exact CPython patch release are compiled from the pinned python.org tarball with `-DPy_BUILD_CORE_MODULE`:

- **Headers.** PBS's own include tree, so the interpreter internals match. Its `pyconfig.h` gets four edits.
- **Libraries.** The headers and libraries of the pinned Rocky 9.2 vault -devel RPMs (Appendix B): OpenSSL 3.0.7, SQLite 3.34.1, expat 2.5.0, libffi 3.4.2, xz 5.2.5 and zstd 1.5.1.
- **Toolchain.** fsbpyr's LLVM 23.1.3 and the Debian 12 sysroot.

All 15 modules of 3.14.9 compile and link in about 4 s. The pack is 1.37 MB raw and 328 KB as tar.zst (experiment-confirmed).

**Verified** (experiment-confirmed):

- **ELF needs.** The modules' symbol-version needs are only `OPENSSL_3.0.0`, `ZLIB_1.2.0`, `XZ_5.0`, `LIBFFI_*_8.0`, `UUID_2.20` and at most `GLIBC_2.34` (`dlopen` in `_ctypes`). Their `NEEDED` sonames are `libssl.so.3`, `libcrypto.so.3`, `libsqlite3.so.0`, `libz.so.1`, `libbz2.so.1`, `liblzma.so.5`, `libuuid.so.1`, `libzstd.so.1`, `libffi.so.8`, `libexpat.so.1`, `libncursesw.so.6`, `libtinfo.so.6` and `libpanelw.so.6`. The author re-read these with readelf.
- **No shim needed.** Compiling against OpenSSL 3.0 headers compiles out what newer headers would expose.
- **13 of the 15 modules are distribution-neutral.** One build passes a functional test on:
  - Rocky 9.8 (OpenSSL 3.5.5), and Rocky with the real 9.2 libraries (3.0.7, zstd 1.5.1);
  - Ubuntu 24.04 (3.0.13) and Debian 12 (3.0.22);
  - AlmaLinux 10.2, Ubuntu 26.04 and Debian 13.

  The test does a local TLS 1.3 handshake, pbkdf2/sha3/md5, an SQLite query, zlib/bz2/lzma/zstd round trips, ctypes `strlen` plus a `qsort` callback, an ElementTree parse, `uuid1`, and imports curses.
- **Two modules are per family.** `readline` needs one build per family: RHEL's `libedit.so.0` against Debian and Ubuntu's `libedit.so.2`. A build for one family fails to import on the other. `_decimal` works only on RHEL, which has `libmpdec.so.3` 2.5.1 in AppStream; Ubuntu 24.04 and Debian 12 have no libmpdec package.
- **Other Pythons.** 3.12.15, 3.13.17 and 3.15.1 compile and link with the same recipe; module source lists come from each version's `Modules/Setup.stdlib.in`. The exception is `_decimal` on 3.12, which needs extra CONFIG flags. On 3.15, `_remote_debugging` compiles from source without any library. PBS's copy uses zstd (round 1 counted 11 zstd symbols), but the source build has no zstd support. It stays built in, with its static zstd.
  - The cost critic ran the functional test on 3.12, 3.13 and 3.15 with test stubs on Ubuntu 24.04. Every neutral module that exists on the version passed: 13 on 3.15, and 12 on 3.12 and 3.13, which have no `_zstd`. That includes a TLS 1.3 handshake, `pyexpat` and `_ctypes`.
  - Only 3.14 ran through the real fsbpyr payload path.
- **Prototype app.** A real fsbpyr app built on the unchanged default stub ran on Rocky 9.8, Ubuntu 24.04 and Debian 12 with all 13 modules loaded from the payload and reporting the system's versions. The prototype was a boot `ShadowFinder` plus a runtime-build record hook (the saved diff adds 44 lines). Its CA stores had 146, 120 and 148 certificates.
- **crypto-policies.** With `PY_SSL_DEFAULT_CIPHERS=2` (CPython's `--with-ssl-default-suites=openssl`, as RHEL builds its own `python3`), `_ssl` skips its cipher string and its forced TLS 1.2 minimum (`Modules/_ssl.c`, mode 2).
  - `MinProtocol=TLSv1.3` in openssl.cnf gave `minimum_version` TLSv1_3, and the handshake with a TLS 1.2-only server failed.
  - `CipherString` was honoured.
  - With no `MinProtocol` in openssl.cnf, `minimum_version` was MINIMUM_SUPPORTED: the TLS floor is whatever the host configures. On Rocky 9.8, `update-crypto-policies --set LEGACY` did not lower `TLS.MinProtocol`, so the minimum was tested with explicit cnf files.
  - `update-crypto-policies --set FUTURE` cut the ciphers from 61 to 17 and rejected a 2048-bit key.
  - Mode 1, PBS's and CPython's default, ignores all of this: always TLS 1.2 and 17 to 18 ciphers.
- **FIPS** (kernel flag simulated as in A). The rebuilt modules behaved exactly like RHEL's `python3`: `get_fips_mode()` returned 1, `md5()` was refused, `md5(usedforsecurity=False)` worked, and contexts had 40 ciphers under DEFAULT and 21 under FIPS. PBS's static OpenSSL ignores FIPS: mode 0, and `md5()` works. The HACL built-ins stay reachable by direct import.
- **The four `pyconfig.h` edits.** CPython's own `configure` was run in rockylinux:9, ubuntu:24.04 and debian:12 and compared with PBS's `pyconfig.h`. Only 8 of the 59 macros the module sources consult differ. Four edits on top of PBS's file suffice for the neutral pack:
  - undefine `PY_SQLITE_HAVE_SERIALIZE`;
  - undefine `HAVE_RL_COMPDISP_FUNC_T` and `HAVE_RL_COMPLETION_SUPPRESS_APPEND`;
  - set `PY_SSL_DEFAULT_CIPHERS` to 2.
- **Reproducible.** Two builds in the same tree and one in a relocated copy gave byte-identical modules, with no host paths. This was on one host only.
- **Silent fallback and the guard.** On debian:12, which has no libssl, `hashlib` silently lost `pbkdf2_hmac` and `scrypt`. With a guard that raises a non-`ImportError` (`DynamicLibraryError`), `import hashlib` failed loudly, naming `libcrypto.so.3`.
- **Performance** (one run, inferred). There was no meaningful difference for zlib, sha256, sqlite or lzma at the test's scale.

**Critics' corrections:**

- **The guard is too broad** (code-confirmed). `NativeCacheError` is an `ImportError` (`boot/_fsbpyr_rt/native.py:32`), raised for an unusable or noexec cache. The prototype's `except ImportError` re-labels those, and module-init errors too, as "this system lacks a library". The guard must react only to `dlopen` errors that name a missing object or symbol version.
- **New cache requirement** (inferred from code). Today a stdlib-only app extracts nothing. With replaced modules, `import shutil`, `zipfile`, `tarfile` or `hashlib` needs a writable, owner-private, exec-capable cache (docs/compatibility.md, "A cache on a file system that allows execution").
- **SONAME capture** (experiment-confirmed with a toy library on debian:12). ld.so satisfies a `DT_NEEDED` by soname from any object already loaded, whatever its path and `RTLD_LOCAL`. A wheel that bundles an *unhashed* `libssl.so.3`, `libz.so.1` and so on, imported first, therefore captures the system modules. In the corpus every bundled OpenSSL has a hashed name (`libcrypto-5d63cd29.so.3`). But casadi and ortools bundle an unhashed `libz.so.1`, and ortools an unhashed `libbz2.so.1` (round 1, wheels scan). Both sonames are in the pack's `NEEDED` set, so the hazard already applies to two of the 65 corpus apps.
- **SBOM wording.** The static OpenSSL, zstd and xz copies are not "unused": the launcher uses them at every start.
- **Cost estimates.**
  - Effort: about 1.25 times EL8 (about 5,200 lines), or 1.55 times per the C cost critic.
  - Calendar: 3 to 4 days.
  - CI: a decoupled `dyn.yml` of about 60 to 130 runner-minutes per release, with 9 new release assets (8 packs and a sums file, about 3 MB).

### 6.2 Re-checked for this report

The author ran B's 28 MB prototype app in podman (`--pids-limit 256 --memory 2g`, experiment-confirmed). In both images every one of the 13 modules was imported from `<cache>/runtime/<id>/site/*.so`, and the static copies were never used. In both, `hashlib.md5(usedforsecurity=False)`, `pbkdf2_hmac`, `scrypt`, zipfile deflate, ElementTree and ctypes worked.

| | Rocky 9.8 | Ubuntu 24.04 |
|---|---|---|
| OpenSSL | 3.5.5 | 3.0.13 |
| SQLite | 3.34.1 | 3.45.1 |
| zlib | 1.2.11 | 1.3 |
| expat | 2.5.0 | 2.6.1 |
| zstd | 1.5.5 | 1.5.5 |
| ncurses | 6.2 | 6.4 |
| CA certificates | 146 | 120 |
| Default context | TLS 1.2 minimum, 18 ciphers | TLS 1.2 minimum, 17 ciphers |

Judging by the TLS 1.2 minimum and the 17 to 18 ciphers, the prototype's `_ssl` was the mode-1 build (PBS's cipher policy), not the policy build.

On Ubuntu, libsqlite3-0, libncursesw6 and ca-certificates were installed in the container first; Rocky 9.8 ran as it comes. The first Ubuntu run then stopped with `DynamicLibraryError ... libexpat.so.1: cannot open shared object file`, because the base image lacks libexpat1. That error also carried the existing glibc hint (`the build requires glibc 2.34, this system has glibc 2.39`), which is misleading for a missing library. The second run, with libexpat1 added, succeeded.

## 7. Design C: rebuild CPython per distribution

**How.** CPython 3.14.9 was configured plainly in a rockylinux:9 container with Rocky's clang 21, LTO and `MODULE_BUILDTYPE=static`. That gives a PBS-shaped tree: a bitcode `libpython3.14.a` with `config.c` and per-module objects. fsbpyr's unchanged launcher linked against it, and with a hand-written `PYTHON.json` it built a 17.1 MB app that ran on Rocky 9 and Ubuntu 24.04 (experiment-confirmed). The app imported only `json`, `hashlib` and `sys`, so none of the shared library-backed modules was exercised, and `component_versions` was patched out.

**The decisive finding** (experiment-confirmed). With the 15 library-backed modules moved to the shared section, the core interpreter's `NEEDED` is only libm and libc on both distributions. All twelve libraries enter through those modules. Rebuilding the core per distribution therefore changes nothing for security updates. C-full reduces to C-lite, which is design B's recipe at a higher cost.

**Further findings** (experiment-confirmed unless marked):

- **Ubuntu 24.04 core fails the baseline.** The core built on Ubuntu references `__isoc23_strtol`, `__isoc23_strtoul`, `__isoc23_wcstol` and `__isoc23_sscanf` (glibc 2.38), so it does not link at the glibc 2.34 baseline. Its interpreter also needs GLIBC_2.38 to run the stdlib compile on the build host.
- **Build times.** The plain Rocky gcc build took 8.6 s of make on 24 CPUs. Clang LTO on 4 CPUs took 141 s. PGO+LTO took 382 s of make, after two failed attempts that needed a test-ignore list. A micro-benchmark showed PGO+LTO on par with PBS; the critic pointed out that it lacked PBS's tail-call interpreter.
- **Expat behaviour gap.** With Rocky's expat 2.5.0, CPython's own test_xml_etree and test_xml_etree_c fail 6 tests: `test_flush_reparse_deferral_disabled`, `test_simple_xml_chunk_1` and `test_simple_xml_chunk_5`, in both. Incremental XML parsing reports events at different times than with PBS's expat 2.9.0. This applies to any design that uses the system expat.
- **Runtime id collision** (code-confirmed). `runtime_id` hashes the pin, not the content (`runtime/registry.py:126-137`), so a self-built dist under a PBS pin gets the PBS runtime id.
- **No usable fork** (outside research). PBS's build system has about 5,200 lines in its four main files, about 40 patches and 13 dependency build scripts, and no maintained fork with system libraries exists. The distributions' own Python 3.12 is not reusable either: RHEL's is shared-only, and Ubuntu's is 3.12.3 with backports.
- **Not reproducible** (critic, inferred). A plain build embeds the build date, so stubs linked from it are not byte-reproducible without unscoped determinism work.

**Cost** (critics, inferred):

- **Effort.** About 1.65 to 2.1 times EL8, or 6,950 to 9,000 lines.
- **Dist rebuilds.** 240 to 290 runner-minutes per rebuild of the 16 dists (4 Pythons × 2 architectures × 2 distributions), on every CPython patch, image or recipe change.
- **Release tests.** Default-Python variant tests add about 200 runner-minutes per distribution and release (about 320 at the job timeouts), so 400 to 650 for two distributions.
- **Ownership.** fsbpyr would own CPython's security-fix latency, the opposite of the goal.

**Verdict:** reject C-full. C-lite is design B's module recipe.

## 8. Recommendation: B's modules with A's delivery

### 8.1 Design

- **Build:** in the runtime build, for each Python and architecture, compile the library-backed modules from the pinned python.org source with B's recipe (Appendix A).
  - It uses the toolchain and sysroot fsbpyr already has, plus the pinned Rocky 9.2 -devel RPMs (about 8.4 MB per architecture).
  - It runs post-link ELF checks: version needs at or below the floor, `GLIBC` at most the baseline, and every undefined `Py*`/`_Py*` symbol exported by the stub.
  - `fsbpyr runtime build` does the same, so development and release builds agree.
- **Ship:** embed the pack in the runtime fragment, and so in each fsbpyr executable, as in design A.
  - This adds no release asset, no sums file, no fetch or placement code and no separate workflow.
  - The pack is tied to the exact CPython patch, which the runtime already is. Its digest must enter the runtime id.
  - Default apps must not carry the pack; only the chosen modules' records reach a payload.
- **App build:** `--dynamic-libs LIST|all` adds `Module(EXTENSION)` and `File(NATIVE_EXT)` records for the chosen modules, plus a `build_info` entry. Nothing is compiled, so the app-build host still needs only uv.
- **Run:** either the launcher inittab drop with a payload flag (format 7 to 8) or the boot `ShadowFinder` (section 4.3; owner decision 2). The modules load `RTLD_LOCAL` through the native cache.
- **`--target-distro el9,ubuntu24.04`** (also `[tool.fsbpyr] target-distro`):
  - it validates the build against the distributions the pack was tested on;
  - it picks the `readline` build of the family;
  - it allows `_decimal` only when every target is RHEL-family;
  - it names the packages to install in "target needs" and in load errors;
  - it prints the minimum library versions (section 9.1).
  - Without it, only the distribution-neutral set is allowed.
- **Library names:** `openssl` (`_ssl`, `_hashlib`), `sqlite`, `zlib` (`zlib`, `binascii`), `bzip2`, `xz`, `zstd` (3.14+), `libffi`, `expat`, `uuid`, `ncurses` (`_curses`, `_curses_panel`), `libedit` (`readline`, per family) and `mpdecimal` (RHEL only).

Compared with A, this needs no shim to maintain. It adds libffi and expat. It follows RHEL's crypto policy fully, including the TLS minimum, and in the simulated FIPS environment it matched RHEL's `python3`.

Compared with B as designed, it drops the 9 assets, `dyn.yml`, and the download and placement code.

### 8.2 Coverage

| Library | RHEL 9 | Ubuntu 24.04 | Notes |
|---|---|---|---|
| OpenSSL | system | system | OpenSSL 3.0 interface only. RHEL 9.0 to 9.6 and Ubuntu 24.04 ship 3.0.x or 3.2.x |
| SQLite | system | system | `Connection.serialize()` and `deserialize()` unavailable. Floor 3.34.1 on RHEL 9 |
| zlib, bzip2, xz, libuuid, ncurses | system | system | `libbz2.so.1` exists on Debian and Ubuntu as a symlink |
| zstd | system | system | 3.14 and later; 3.12 and 3.13 have no `_zstd`. On 3.15, `_remote_debugging` keeps the built-in zstd |
| libffi | system | system | floor libffi 3.4 (`LIBFFI_*_8.0`) |
| expat | system | system | RHEL's 2.5.0 changes pull-parser event timing |
| libedit | per family | per family | needs `--target-distro` |
| mpdecimal | system (opt-in) | built in | no package on Ubuntu or Debian |
| launcher's SHA-256, zstd, xz | built in | built in | used only on the app's own payload |

The modules' built-in copies of the replaced libraries stay in the stub as unused code, so `strings` still shows OpenSSL 3.5.9 and apps do not shrink. The launcher still uses the static SHA-256 (libcrypto), zstd and xz code, so SBOM and notices must say so rather than "unused". A modular stub without them is optional: about +1,600 lines, 8 more assets of about 5 MB xz and about +40 CI minutes (owner decision 6).

### 8.3 Work and cost

Work items, combined from the designs and the critics' additions (lines inferred):

| Item | Lines, about |
|---|---|
| Module recipe, pack builder and ELF checks | 700 |
| Pins and the -devel sysroot: zstd RPM payloads in `runtime/rpm.py`, Rocky 9.2 x86_64 and aarch64 pins, CPython tarball pins, refresh tool | 530 |
| Pack in the runtime fragment; runtime id inputs | 280 |
| Activation: inittab drop, payload flag and format bump (or the `ShadowFinder`) | 390 |
| App build: CLI, config, LIST and target validation, records, `assemble.py:92`, `build_info` | 820 |
| Boot: narrow guard, messages, CA fixup off for a dynamic `_ssl`, selftest coverage | 310 |
| Reporting: "target needs", `inspect`, `runtime_components`, SBOM and notices | 460 |
| Smoke-test policy for a build host that is not a target; ELF guards | 320 |
| SONAME preflight against wheels' bundled libraries, with a test | 250 |
| Policy and test for an unusable cache | 210 |
| Container e2e tests, CPython test gate with a tty, policy and FIPS tests | 410 |
| CI: `build.yml` steps, gate job, `check-release.sh` | 300 |
| Docs: README, compatibility, security, man page, wiki, CHANGELOG | 350 |
| Cache GC for the new pins; tests for a pack and runtime identity mismatch and for the `_ctypes` fixup with a shared `_ctypes` | 150 |
| **Total** | **about 5,500** |

Places in the code the work touches (code-confirmed at HEAD):

- **Activation.** `launcher/main.c:144` (the inittab), `launcher/format.h:18`, `:68` and `:126` (format version and module flags), and `payload/reader.py:116-119` (unknown flag bits are refused).
- **App build.** `assemble.py:90-92` (`_builtin_or_frozen` takes the built-in set from the build host). `cli.py:113`, `config.py:130` and `build.py:231-237` (`settings_record`) hold `--allow-system-lib` today, which is where the new options sit.
- **Boot.**
  - `boot/_fsbpyr_rt/native.py:29` lists `_SYSTEM_LIBRARY_ERRORS`, which include "cannot open shared object file". That is why a missing libexpat got the glibc hint.
  - `native.py:32` makes `NativeCacheError` an `ImportError`. In design A, the outer `except OSError` in `PayloadExtensionLoader.create_module` re-wrapped the first guard attempt as a cache error, so the guard must raise before that handler.
  - `boot/_fsbpyr_rt/fixups.py:15-60` is the CA bundle fixup. `:71` is the `_ctypes` fixup, registered at `:157`; it was written for a static `_ctypes`. `find_library` and `CDLL(None)` were not exercised with a shared one.
- **Wheel checks.**
  - `natives.py:36-41` (`_SYSTEM_LIBRARIES`) has `libz.so.1` but none of the other sonames the pack needs. A wheel extension that needs them is refused without `--allow-system-lib`. Whether `--dynamic-libs openssl` should also allow `libssl.so.3` for wheels is an open design question.
  - Some wheels already take `libz.so.1` from the host: the libgfortran bundled with numpy (39 corpus apps) and scipy (17) needs it unbundled (round 1).
  - `system.py` (`target_needs`, `_over_floor`) has floors only for libstdc++, libgcc_s and libz.
- **Runtime.**
  - `runtime/registry.py:126-137`: `runtime_id` has no content digest.
  - `runtime/collect.py:27-56`: the cache GC names and `needed()`.
  - `runtime/rpm.py` reads xz payloads only.
  - `runtime/stub.py:57` and `:63-78`: the exports and the sanitized variant.
  - `tools/release/sbom.py` reads `runtime_components`.

- **Effort:** about 1.2 to 1.4 times EL8; 3 to 4 days with agents, including aarch64 and the first CI runs (inferred). One critic put B's recipe at 1.55 times (about 6,500 lines).
- **CI** (the author's inference; no critic costed this delivery):
  - the pack compiles in seconds per runtime entry in `build.yml`, plus the RPM fetch. B's cost critic put a standalone pack job, with toolchain, sysroot and RPM fetch, at about 4 minutes;
  - the e2e cases fold into `tests/e2e/test_portability.py`, which already runs a 12-image matrix on each of the 8 (Python × architecture) entries. The full suite's no-skip rule (9c1a1cd) means anything placed there runs for every Python, so the cases must reuse the images it already pulls;
  - policy, FIPS and family tests and the CPython gate go in one job parallel to the suite. B's cost critic warned that container tests inside the suite jobs would add 24 to 48 runner-minutes to every full-suite run;
  - estimate: about +30 to +60 runner-minutes per full-suite run, against a measured 488 runner-minutes and about 49 minutes wall clock (Full suite run 40316135072). No new wall-clock time. For comparison, the critics put B's decoupled `dyn.yml` at 60 to 130 runner-minutes per release. EL8's own CI cost is unmeasured: `el8.yml` has never run on GitHub.
- **Maintenance** (inferred):
  - per CPython patch release: a new tarball pin, and the pack rebuilds with the runtime. The pack is compiled against CPython's internal headers, so it fits only its exact patch release; a mismatch must be refused, with a test;
  - per PBS bump: nothing extra;
  - per distribution minor release: nothing, since the floor is frozen at Rocky 9.2;
  - per new distribution major: add it to the image matrix and re-run CPython's `configure` in it, about 2 minutes, to confirm the four `pyconfig.h` edits still hold;
  - the Rocky 9.2 vault is outside fsbpyr's control, so the RPMs should be mirrored or cached (about 17 MB for both architectures).

## 9. Behaviour changes and hazards

### 9.1 Semantic floors

An app tested against PBS's libraries meets older ones on the target (experiment-confirmed where a version is given):

| Library | PBS 20261021 | Rocky 9.2 floor | Rocky 9.8 image | Ubuntu 24.04 | Debian 12 |
|---|---|---|---|---|---|
| OpenSSL | 3.5.9 | 3.0.7 (9.0 and 9.1: 3.0.1; 9.5 and 9.6: 3.2.2) | 3.5.5 | 3.0.13 | 3.0.22 |
| SQLite | 3.53.1 | 3.34.1 | 3.34.1 | 3.45.1 | 3.40.1 |
| zlib | 1.3.2 | 1.2.11 | 1.2.11 | 1.3 | 1.2.13 |
| expat | 2.9.0 | 2.5.0 | 2.5.0 | 2.6.1 | 2.5.0 |
| xz | 5.8.4 | 5.2.5 | | 5.4.5 | |
| zstd | 1.5.7 | 1.5.1 | 1.5.5 | 1.5.5 | |
| libffi | 3.8.0 | 3.4.2 | | 3.4.6 | |
| ncurses | 6.5 | 6.2 | 6.2 | 6.4 | |

- **SQLite 3.34.1** lacks the `->` and `->>` operators, the math functions, `unixepoch()`, STRICT tables, RETURNING and DROP COLUMN (the first four probed; the rest from SQLite's release history).
- **expat 2.5.0** fails 6 of CPython's pull-parser tests (section 7).
- **OpenSSL 3.0** lacks the hybrid post-quantum groups of 3.5.

The build should print these floors for the chosen targets.

### 9.2 A cache becomes necessary

Replaced modules are extracted to the per-app native cache. Even a stdlib-only app then needs a writable, owner-private cache on a file system that allows execution (experiment-confirmed in A):

- a read-only root with no HOME gives `cannot extract native code for _ssl ... set FSBPYR_CACHE_DIR`;
- a noexec cache gives `failed to map segment from shared object ... noexec mount`.

Falling back to the built-in copy when the cache is unusable would defeat the purpose (owner decision 3).

### 9.3 Missing libraries must fail loudly

The stdlib swallows `ImportError` in many places:

- `hashlib` falls back to HACL and loses `pbkdf2_hmac` and `scrypt`;
- `hmac`, `uuid`, `shutil`, `tarfile`, `zipfile` and `importlib.metadata` degrade quietly;
- so does third-party `try: import ssl`.

The guard must therefore raise a non-`ImportError` (experiment-confirmed in A and B). Two refinements are needed:

- it must not re-label cache errors (section 6.1);
- it should not crash `import shutil` on a host without liblzma when the app never uses xz (critic, inferred).

One option is to fail at import only for `_ssl`, `_hashlib` and `_sqlite3`, and to report everything else at selftest and build time. `FSBPYR_SELFTEST=natives` already lists the missing sonames for payload extensions, with no code change (experiment-confirmed in A on debian:12, ubuntu:24.04 and rockylinux:9).

Base images lack libraries (experiment-confirmed in round 1):

| Image | Lacks |
|---|---|
| ubi9-minimal | expat, libedit, mpdecimal |
| rockylinux:9-minimal | expat, libedit, mpdecimal |
| ubuntu:24.04 | SQLite, expat, libedit, mpdecimal (round 1 disagreed about libpanelw) |
| debian:12 | libssl, SQLite, expat, ncursesw, libedit, mpdecimal |

Package names to print:

- **RHEL:** openssl-libs, sqlite-libs, zlib, bzip2-libs, xz-libs, libzstd, libuuid, ncurses-libs;
- **Ubuntu 24.04:** libssl3t64, libsqlite3-0, zlib1g, libbz2-1.0, liblzma5, libzstd1, libuuid1, libncursesw6.

These lists come from design A. For libffi and expat the package names are inferred: libffi and expat on RHEL, libffi8 and libexpat1 on Ubuntu. libexpat1 was the one installed for section 6.2.

### 9.4 Wheels that bundle unhashed libraries

Section 6.1 describes the SONAME capture. The build should scan the staged wheels' bundled libraries for sonames in the pack's `NEEDED` set and refuse or warn (owner decision 7).

Of the 65 corpus apps:

- 10 carry a bundled OpenSSL, among them cryptography, psycopg-binary, netCDF4, opencv, pyarrow, GDAL and pyproj;
- 14 carry some TLS stack;
- every bundled OpenSSL shared library observed had a hashed soname. cryptography links its OpenSSL statically;
- casadi and ortools bundle an unhashed `libz.so.1`, and ortools an unhashed `libbz2.so.1`. Both are in the pack's `NEEDED` set, so the capture applies to these apps today;
- SQLite is embedded statically in onnxruntime, tensorflow, jaxlib and GDAL, and zlib in 11 apps;
- cffi embeds libffi statically and exports `ffi_*` symbols (8 apps). Whether that interferes with a shared `_ctypes` that needs `libffi.so.8` was not run (inferred: both load `RTLD_LOCAL`).

cryptography 50.0.2 embeds OpenSSL 4.0.3 statically, which is out of scope. It might be rebuilt from its sdist against the system OpenSSL through `[tool.uv] no-binary-package` and `--allow-system-lib`, at the cost of Rust and headers on the build host. Round 1 confirmed only that uv then picks the sdist and that the setting reaches fsbpyr's staging. The sdist build itself, and cryptography's minimum OpenSSL against RHEL 9 and Ubuntu 24.04, were not checked.

### 9.5 TLS defaults follow the host on RHEL

With `PY_SSL_DEFAULT_CIPHERS=2`, the cipher list and the minimum TLS version come from openssl.cnf and crypto-policies (section 6.1). That is what enterprises on RHEL expect, but it changes behaviour against PBS: a host with weak defaults gets weak defaults. Where openssl.cnf sets no `MinProtocol`, the minimum is whatever OpenSSL supports (section 6.1); design A's wrap kept Python's TLS 1.2 minimum, this build does not. On Ubuntu and Debian the same build follows their openssl.cnf. How their defaults compare with Python's own cipher list was not checked.

FIPS: the system OpenSSL in FIPS mode is used. The HACL built-ins (`_md5`, `_sha1`, `_sha2`, `_sha3`, `_blake2`) stay reachable unless they are dropped as well. The launcher's integrity digest runs on its private, non-validated libcrypto. A real FIPS kernel was not tested, and no compliance claim follows.

### 9.6 CA-bundle fixup

`boot/_fsbpyr_rt/fixups.py:15-60` adapts the static OpenSSL to `CA_FILE = "/etc/ssl/cert.pem"` (code-confirmed). With the system OpenSSL, the system's own OPENSSLDIR applies. The fixup must be off for a dynamic `_ssl`, or it widens trust beyond the system's configured store (critic, inferred).

### 9.7 EL8

EL8's libraries cannot back this design. The versions are experiment-confirmed with rpm queries in a Rocky 8.10 container (an arm64 image under emulation). The zstd minimum is read from CPython 3.15.1's configure.ac; the libffi problem is inferred:

| Library | EL8 version | Problem |
|---|---|---|
| OpenSSL | 1.1.1k (`libssl.so.1.1`) | not the OpenSSL 3 interface |
| libzstd | 1.4.4 | below the 1.4.5 that CPython's configure requires |
| libffi | 3.1 | lacks the complex types |
| SQLite | 3.26.0 | |

`--glibc 2.28` together with `--dynamic-libs` should be a plain build error in a first version. That is a deferral and needs the owner's sign-off (owner decision 5). A reduced EL8 pack without OpenSSL and zstd would be a second floor, recipe and test matrix.

### 9.8 Not tested

- aarch64: the recommended recipe was never built or run there. Design A linked PBS's aarch64 objects with `-nostdlib` only to compare symbols. The library-API sets of its 11 modules match x86_64 for all four Pythons, apart from `__stack_chk_guard` and the outline-atomics helpers (`__aarch64_cas1_*`, `__aarch64_cas8_acq_rel`), which each aarch64 module would have to link. The 26 Rocky 9.2 RPMs exist for aarch64 (HTTP 200).
- A real RHEL 9.0 or 9.1 userland. Design A's pack ran with 9.0 libraries on a 9.8 userland; the recommended pack ran only with the real 9.2 libraries.
- A real FIPS kernel and SELinux enforcing with `.so` files executed from a user cache.
- The recommended pack through the real payload path on 3.12, 3.13 and 3.15. Only 3.14 ran; design A's inittab drop ran on 3.14 and 3.15.
- Measured CI times.

## 10. Owner decisions (open)

1. **Approach:** B's modules with A's delivery (recommended), A, or B with separate assets. Rebuilding CPython per distribution is not recommended.
2. **Activation:** the launcher inittab drop with a payload flag and a format bump (recommended: honest `sys.builtin_module_names`, no static copy reachable), or the boot `ShadowFinder` (no launcher or format change).
3. **Missing library or unusable cache:** fail loudly (recommended), and decide which modules fail at import and which at selftest. Falling back to the built-in copy defeats the purpose.
4. **TLS on RHEL:** follow the system crypto policy (recommended for enterprise use) or keep Python's built-in cipher list and TLS 1.2 minimum. Following it also means no TLS minimum of Python's own where the host sets none.
5. **EL8:** refuse `--glibc 2.28` with `--dynamic-libs` in a first version (a deferral), or ship a reduced EL8 pack.
6. **Modular stub without the unused copies:** now or later.
7. **A wheel bundling one of these libraries under an unhashed soname:** refuse the build or warn.
8. **What `all` means:** the ten neutral libraries (recommended), plus libedit and mpdecimal only where `--target-distro` allows them.
9. **Oldest supported release:** Rocky 9.2 headers were tested. RHEL 9.0 and 9.1 should work by symbol version but were not tested. A 9.7 floor would allow the OpenSSL 3.5 APIs but drop RHEL 9.0 to 9.6. Whether any RHEL 9 minor exports `sqlite3_serialize` is unresolved (section 5).
10. **Rocky 9.2 RPMs:** mirror them as release assets (about 17 MB) or cache them, since vault URLs are outside fsbpyr's control.
11. **The new cache requirement:** accept it, and document it.
12. **aarch64:** build and test it before shipping (required by the no-deferral rule).

## Appendix A. Module build recipe (design B, as run)

Inputs per Python and architecture:

- **CPython source.** The python.org tarball of the exact patch release, pinned by SHA-256 (Appendix B).
- **Headers.** PBS's `install/include/python3.X` copied, with four `pyconfig.h` edits:

  ```
  /* #undef PY_SQLITE_HAVE_SERIALIZE */
  /* #undef HAVE_RL_COMPDISP_FUNC_T */
  /* #undef HAVE_RL_COMPLETION_SUPPRESS_APPEND */
  #define PY_SSL_DEFAULT_CIPHERS 2
  ```

  The two `HAVE_RL_*` lines matter only for RHEL's `readline`. The Debian and Ubuntu `readline` build used PBS's `pyconfig.h` unchanged, against Ubuntu 24.04's libedit-dev, libedit2 and libbsd0 debs.
- **Floor headers and libraries.** The Rocky 9.2 -devel RPMs (Appendix B), extracted into a root.
- **Toolchain.** fsbpyr's LLVM 23.1.3 and the Debian 12 glibc sysroot.

Source files per module are the `.c` entries of the module's `@MODULE_<NAME>_TRUE@` line in `Modules/Setup.stdlib.in`, without the `@...@` tokens. For `_ctypes` that leaves out `_ctypes/malloc_closure.c`. Compiling it made `ffi_closure_alloc` resolve to the module itself and hang at import (found with gdb).

Compile each source:

```
clang --target=x86_64-unknown-linux-gnu --sysroot=<debian12-sysroot> -isystem <rocky92-root>/usr/include
  -ffile-prefix-map=<src>/Modules=/cpython/Modules -ffile-prefix-map=<inc>=/pbs/include
  -ffile-prefix-map=<rocky92-root>=/sysroot -ffile-prefix-map=<debian12-sysroot>=/glibc-sysroot
  -fPIC -O2 -std=c11 -fno-strict-overflow -fstack-protector-strong -fstack-clash-protection
  -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=2 -DNDEBUG -DPy_BUILD_CORE_MODULE
  -I<inc> -I<inc>/internal -I<src>/Modules [extra] -c <file>.c
```

Extras: `binascii` takes `-DUSE_ZLIB_CRC32` (as PBS's `configure` sets it). RHEL's `readline` takes `-Wno-error=incompatible-function-pointer-types`.

Link each module:

```
clang --target=x86_64-unknown-linux-gnu --sysroot=<debian12-sysroot> --ld-path=<llvm>/ld.lld -shared -fPIC
  -o <module>.so <objects> -Wl,-z,relro,-z,now,--build-id=none -Wl,--as-needed -L<rocky92-root>/usr/lib64 <libs>
```

| Module | Libraries |
|---|---|
| `_ssl` | `-lssl -lcrypto` |
| `_hashlib` | `-lcrypto` |
| `_sqlite3` | `-lsqlite3` |
| `zlib`, `binascii` | `-lz` |
| `_bz2` | `-lbz2` |
| `_lzma` | `-llzma` |
| `_uuid` | `-luuid` |
| `_zstd` | `-lzstd` |
| `_ctypes` | `-lffi -ldl` |
| `pyexpat` | `-lexpat` |
| `_decimal` | `-lmpdec -lm` |
| `readline` | `-ledit` |
| `_curses` | `-lncursesw -ltinfo` |
| `_curses_panel` | `-lpanelw -lncursesw -ltinfo` |

Checks after linking:

- `readelf -d` gives `NEEDED`, and `readelf -V` gives the version needs: `OPENSSL_3.0.0`, `ZLIB_1.2.0`, `XZ_5.0`, `LIBFFI_*_8.0`, `UUID_2.20` and at most `GLIBC_2.34`;
- every undefined `Py*`/`_Py*` symbol must be in the stub's dynamic symbol table. This held for 3.14 against the prototype app, and for 3.12 and 3.15 against the v0.25.0 fsbpyr binaries. 3.13 was not checked.

The whole pack builds in about 4 s.

## Appendix B. Pins used

python.org tarballs (SHA-256):

```
de1a241a519e0a3374fea98988d0b52c886743f9d953f23be8269cc7b59c5fab  Python-3.12.15.tgz
9cb057caa588f122577a5054a22531cd9c23ab6706f1a19bc95a55380a895177  Python-3.13.17.tgz
8b47e85ee67e93945e93620a051a8f44bfc1e9735ed67075d166f4a74b7a060b  Python-3.14.9.tgz
133d85e118dc32621cda1d9d1d7d9694fe0c5e82bc339a7d528c109fe8b36387  Python-3.15.1.tgz
```

Rocky 9.2 vault RPMs for x86_64, as `SHA-256 name version path`, checked against the repodata. The paths are relative to `https://dl.rockylinux.org/vault/rocky/9.2/`. The aarch64 counterparts exist at the same paths with `aarch64` in place of `x86_64` (HTTP 200); their hashes were not collected.

```
3986a67c447cae27f863c6982fdb511ad003975e958cd56b6606edb3608e3210 bzip2-devel 1.0.8-8.el9 AppStream/x86_64/os/Packages/b/bzip2-devel-1.0.8-8.el9.x86_64.rpm
9d88efca766d67129bf9e94f4fed072e6faf74fb8d905b1f37a5eefddcb248b2 bzip2-libs 1.0.8-8.el9 BaseOS/x86_64/os/Packages/b/bzip2-libs-1.0.8-8.el9.x86_64.rpm
588588c888cb957052ac7acd3d2727b82ce7a7b6f0eac38eaf7a252e45af8c28 expat 2.5.0-1.el9 BaseOS/x86_64/os/Packages/e/expat-2.5.0-1.el9.x86_64.rpm
281795cbcedace5821a67feb7d1a680fbe2f8e35e35be0680a9f601ff2aa2409 expat-devel 2.5.0-1.el9 AppStream/x86_64/os/Packages/e/expat-devel-2.5.0-1.el9.x86_64.rpm
ebf85e5d850e4d02a13aa028e709983d65829a80422d24d950844127f6db5188 krb5-devel 1.20.1-9.el9_2 AppStream/x86_64/os/Packages/k/krb5-devel-1.20.1-9.el9_2.x86_64.rpm
ad95adbf31939c2307fcc04246bbb635a1dd9c24a6d7cd38fcaf039482f1774c libedit 3.1-37.20210216cvs.el9 BaseOS/x86_64/os/Packages/l/libedit-3.1-37.20210216cvs.el9.x86_64.rpm
969c06e17148cffc6b4602a8ce31d44b48b2dc9f6c495453e47e607f15b4e944 libedit-devel 3.1-37.20210216cvs.el9 AppStream/x86_64/os/Packages/l/libedit-devel-3.1-37.20210216cvs.el9.x86_64.rpm
e019d481306d9fc56086b82ff543cbac94577db0d7d46378664f176e71fcfafd libffi 3.4.2-7.el9 BaseOS/x86_64/os/Packages/l/libffi-3.4.2-7.el9.x86_64.rpm
34334e854639f104a07a7a46dc93ce0a5bf69fafa3e329f3899f0c75350f1733 libffi-devel 3.4.2-7.el9 AppStream/x86_64/os/Packages/l/libffi-devel-3.4.2-7.el9.x86_64.rpm
fad09df856f0f8f97bb36f761fa2b36c2b78bd4c66718d3a0d35c0f45215c279 libuuid 2.37.4-11.el9_2 BaseOS/x86_64/os/Packages/l/libuuid-2.37.4-11.el9_2.x86_64.rpm
96054824a7a93ce9a6ffe3bacf20f301d947d8ea270e556c662186404c3a965d libuuid-devel 2.37.4-11.el9_2 AppStream/x86_64/os/Packages/l/libuuid-devel-2.37.4-11.el9_2.x86_64.rpm
517b3080de2e0891cc346ba6b1548ef438b95dbede0e6e7a6deae89fc241f5bc libzstd 1.5.1-2.el9 BaseOS/x86_64/os/Packages/l/libzstd-1.5.1-2.el9.x86_64.rpm
34e5189a8b7d9dee4bf217254d8f76e0ace425f917779a1e976440f58269ddef libzstd-devel 1.5.1-2.el9 AppStream/x86_64/os/Packages/l/libzstd-devel-1.5.1-2.el9.x86_64.rpm
708d6d283020a54e3766cec65767b063d01d0d4500f32ba1f12fabc2eb7ecb36 mpdecimal 2.5.1-3.el9 AppStream/x86_64/os/Packages/m/mpdecimal-2.5.1-3.el9.x86_64.rpm
a7e54355a47bc7eb9f2f4c0ec56e45ab027847d92a3d89e0668abe9c3f30306f mpdecimal-devel 2.5.1-3.el9 CRB/x86_64/os/Packages/m/mpdecimal-devel-2.5.1-3.el9.x86_64.rpm
57fcfb2ebb3c885a94c3c3ca290a13e597fad33726f555933ebb216270bc7937 ncurses-base 6.2-8.20210508.el9 BaseOS/x86_64/os/Packages/n/ncurses-base-6.2-8.20210508.el9.noarch.rpm
63e3cd6be1a424be25b5e871404d52c7d605cfea919336661bb77fc6f32ba8e0 ncurses-devel 6.2-8.20210508.el9 AppStream/x86_64/os/Packages/n/ncurses-devel-6.2-8.20210508.el9.x86_64.rpm
a48e28ac588578dbb085f4bb6f7c7a84a12680fc4133dbe143d72bfb518611d9 ncurses-libs 6.2-8.20210508.el9 BaseOS/x86_64/os/Packages/n/ncurses-libs-6.2-8.20210508.el9.x86_64.rpm
b52dc39bfe8f9bbf55fa276a0a176a4df6844d3ba05b82728f7ab9cc81331ed6 openssl-devel 3.0.7-17.el9_2 AppStream/x86_64/os/Packages/o/openssl-devel-3.0.7-17.el9_2.x86_64.rpm
50d45934a01a01e8a9212498d105b07d2229f4f839ac3d37a73dd8787a0ac977 openssl-libs 3.0.7-17.el9_2 BaseOS/x86_64/os/Packages/o/openssl-libs-3.0.7-17.el9_2.x86_64.rpm
e7949f7b0eb0ff292616f0efb3044ebf2f02b49c1123df64ffb08e6d2cf2bfd1 sqlite-devel 3.34.1-6.el9_1 AppStream/x86_64/os/Packages/s/sqlite-devel-3.34.1-6.el9_1.x86_64.rpm
128992e15f0a2a5361bb8240cf962c21feb450f4cb8652f7e9e023b6b0188bed sqlite-libs 3.34.1-6.el9_1 BaseOS/x86_64/os/Packages/s/sqlite-libs-3.34.1-6.el9_1.x86_64.rpm
36ef93c4def7a3e894cfadd92c5eeb61fb8709d55df51b21b9c1ab50ef2cda70 xz-devel 5.2.5-8.el9_0 AppStream/x86_64/os/Packages/x/xz-devel-5.2.5-8.el9_0.x86_64.rpm
7a6f5891537bbbf601ee574c5946c3f0f121369b85c6cdfdd07e974a4dc8d81d xz-libs 5.2.5-8.el9_0 BaseOS/x86_64/os/Packages/x/xz-libs-5.2.5-8.el9_0.x86_64.rpm
d9dee528ebff7cfd6e962ead12d9ef62ded71aa9f379c6a0ccfa72c65cbce3de zlib 1.2.11-39.el9 BaseOS/x86_64/os/Packages/z/zlib-1.2.11-39.el9.x86_64.rpm
08a046de9e6353b59ca31a86d08e08e31628e36514f98aa05b6ce6c0ad804af5 zlib-devel 1.2.11-39.el9 AppStream/x86_64/os/Packages/z/zlib-devel-1.2.11-39.el9.x86_64.rpm
```

The RPM payloads are zstd-compressed. `runtime/rpm.py` reads only xz today, and `tarfile` has no zstd on Python 3.12 and 3.13.

## Appendix C. Prototype code

These are throwaway prototypes, kept for their mechanism. They are not proposed as written.

### C.1 Boot `ShadowFinder` (design B)

Added to `boot/_fsbpyr_boot.py`, called from `run()` after the payload finder is installed. The `except ImportError` is the over-broad guard that section 6.1 corrects.

```python
class DynamicLibraryError(RuntimeError):
    """A library the app was built to take from the system is missing or too old."""


class _ShadowFinder:
    """Ahead of BuiltinImporter, only for the built-in modules that the payload also ships as an
    EXTENSION (linked against the system's libraries)."""

    def __init__(self, names: "frozenset[str]") -> None:
        self.names = names

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> ModuleSpec | None:
        if fullname not in self.names:
            return None
        from _fsbpyr_rt.native import extension_spec  # noqa: PLC0415

        spec = extension_spec(fullname, _fsbpyr.find_module(fullname), _PREFIX)
        loader = spec.loader
        create = loader.create_module

        def guarded(spec: ModuleSpec) -> object:
            try:
                return create(spec)
            except ImportError as exc:  # not ImportError: stdlib code falls back on that, silently
                raise DynamicLibraryError(
                    f"{spec.name} was built to use the system's libraries (--dynamic-libs), "
                    f"and this system lacks one: {exc}"
                ) from exc

        loader.create_module = guarded
        return spec


def _shadow_builtins() -> None:
    names = frozenset(
        n for n in sys.builtin_module_names
        if (r := _fsbpyr.find_module(n)) is not None and r[1] == EXTENSION
    )
    if names:
        sys.meta_path.insert(0, _ShadowFinder(names))
```

The prototype added the records in `registry.build_runtime`, for every `.so` in a directory: `Module(name, file, ModuleKind.EXTENSION, file=file)` and `File(file, FileKind.NATIVE_EXT, Blob.zstd(data, level=ZSTD_LEVEL, digest=True), mode=0o755)`. The real feature adds them per app.

### C.2 Launcher inittab drop (design A)

Added to `launcher/main.c` and called right after `PyImport_AppendInittab`. The prototype took the names from an environment variable; the production form reads the module records flagged `FSB_MODULE_REPLACES_BUILTIN`.

```c
static void drop_builtins(const char *list) {
    struct _inittab *tab = PyImport_Inittab;
    size_t out = 0;
    for (size_t in = 0; tab[in].name != NULL; in++) {
        const char *p = list;
        int drop = 0;
        size_t n = strlen(tab[in].name);
        while (*p) {
            const char *e = strchr(p, ',');
            size_t len = e ? (size_t)(e - p) : strlen(p);
            if (len == n && memcmp(p, tab[in].name, n) == 0) drop = 1;
            p += len + (e ? 1 : 0);
        }
        if (!drop) tab[out++] = tab[in];
    }
    memset(&tab[out], 0, sizeof tab[out]);
}
```
