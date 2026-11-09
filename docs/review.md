# Full Repository Review (`v0.31.0` / `b9a1e28`)

2026-11-06 — Review of `fsbpyr` covering security and edge-case bugs, documentation drift, code performance, code maintenance, CI/release/QA processes, and missing repository pieces.

## Executive Summary

- **Static checks & documentation linters pass cleanly**:
  - `make check` (`ruff check`, `mypy --strict` across 262 files): 0 issues.
  - `make check-c` (`clang --analyze` across all 11 C launcher files): 0 findings.
  - `make wiki-lint` (28 wiki pages) and `pytest tests/unit/test_cli_docs.py tests/unit/test_wiki.py tests/unit/test_no_skips.py`: 91 passed.
- **Core design strengths**:
  - 3-stage deterministic self-hosting bootstrap (`stage2` and `stage3` compared byte-for-byte).
  - Full-file SHA-256 Merkle-tree verification at every start (`D31`), hardened native cache path walk (`D20`), `AT_SECURE` refusal (`D41`), parent-process + credential verification for multiprocessing/re-exec (`D22`/`D23`/`D44`), and offline dual Ed25519 + ML-DSA-65 release signing.

Below are the findings from a line-by-line audit of `src/`, `tools/`, `.github/`, `tests/`, `docs/`, and `wiki/`.

---

## 1. Potential or Existing Security Issues & Edge-Case Bugs

### 1.1 C Launcher (`src/fsbpyr/launcher/`)

1. **Symlink Target Truncation in Cache Path Validator ([`way.c`](../src/fsbpyr/launcher/way.c#L96-L112))**
   - In `follow()` ([`way.c:101-105`](../src/fsbpyr/launcher/way.c#L101-L105)), `readlinkat(w->fd, name, w->target, PATH_MAX - 1)` reads into a `PATH_MAX` (4,096-byte) buffer, and then checks `if ((size_t)n + 2 + rest > w->capacity)` against `w->capacity` (`2 * PATH_MAX + 2 = 8,194`).
   - Because `readlinkat` does not return `ENAMETOOLONG` when a symlink target is `>= PATH_MAX - 1` (4,095 bytes) and instead silently truncates the output to `PATH_MAX - 1` bytes, `way.c` validates the truncated symlink target when `rest <= 4,097`, while subsequent kernel path resolution (`open(s->job->root, DIRECTORY)` in [`cache.c:331`](../src/fsbpyr/launcher/cache.c#L331)) resolves the untruncated symlink target. Compare with [`payload.c:110-111`](../src/fsbpyr/launcher/payload.c#L110-L111), which explicitly checks `(size_t)n >= sizeof p->executable - 1`.
   - **Fix**: Check `if ((size_t)n >= PATH_MAX - 1) return fail(w, FSB_WAY_SYSTEM, ENAMETOOLONG, w->walked);` immediately after `readlinkat`.

2. **`errno` Clobbering and Misclassification of Corrupt Blobs as `OSError` ([`module.c`](../src/fsbpyr/launcher/module.c#L356-L372), [`payload.c`](../src/fsbpyr/launcher/payload.c#L405-L437))**
   - In `read_blob()` ([`module.c:359-365`](../src/fsbpyr/launcher/module.c#L359-L365)), `int error = errno;` is read *after* `Py_END_ALLOW_THREADS`, which runs pthread/futex calls to re-acquire the GIL and can overwrite `errno`.
   - Inside `fsb_read_blob()` ([`payload.c:405-437`](../src/fsbpyr/launcher/payload.c#L405-L437)), `errno = 0` is set at line 408, but helper calls before decompression failure—such as `sysconf`/`madvise` in `count_read()` ([`payload.c:286-287`](../src/fsbpyr/launcher/payload.c#L286-L287)) or `sched_getaffinity`/`pthread_tryjoin_np` in `decode_pieces()` ([`payload.c:386-L392`](../src/fsbpyr/launcher/payload.c#L386-L392))—can set a non-zero `errno`. If decompression then fails due to corrupt data, [`module.c:370`](../src/fsbpyr/launcher/module.c#L370) raises a bogus `OSError` instead of `PayloadError`. Conversely, if `fsb_decoder_new()` ([`payload.c:251-255`](../src/fsbpyr/launcher/payload.c#L251-L255)) or `ZSTD_createDCtx()` ([`payload.c:293`](../src/fsbpyr/launcher/payload.c#L293)) fails to allocate memory without setting `errno = ENOMEM`, `MemoryError` is not raised.

3. **Real UID (`getuid`) vs. Effective UID (`geteuid`) Mismatch ([`due.c`](../src/fsbpyr/launcher/due.c#L43), [`native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py#L67), [`registry.py`](../src/fsbpyr/runtime/registry.py#L94))**
   - When `$HOME` is unset or relative, `home()` in [`due.c:43`](../src/fsbpyr/launcher/due.c#L43), `home_directory()` in [`native.py:67`](../src/fsbpyr/boot/_fsbpyr_rt/native.py#L67), and `home_directory()` in [`registry.py:94`](../src/fsbpyr/runtime/registry.py#L94) look up `passwd` using `getuid()` (real UID), while every cache and slot ownership check ([`due.c:100`](../src/fsbpyr/launcher/due.c#L100), [`way.c:176`](../src/fsbpyr/launcher/way.c#L176), [`cache.c:1045`](../src/fsbpyr/launcher/cache.c#L1045), [`slots.c:53`](../src/fsbpyr/launcher/slots.c#L53)) checks `geteuid()`. In a non-`AT_SECURE` process that changed effective UID via `seteuid`, cache path validation fails against the real UID's home directory.

4. **Startup Module Size Limit (`16 MiB`) vs. `ZSTD_PIECES` Split (`8 MiB`) ([`main.c`](../src/fsbpyr/launcher/main.c#L38), [`spec.py`](../src/fsbpyr/payload/spec.py#L29))**
   - [`main.c:96-97`](../src/fsbpyr/launcher/main.c#L96-L97) requires a `STARTUP` module's code blob to have `codec in (FSB_CODEC_ZSTD, FSB_CODEC_BLOCK)` and `1 <= size <= 16 MiB` (`MAX_STARTUP_CODE`).
   - However, `Blob.code()` ([`spec.py:229-231`](../src/fsbpyr/payload/spec.py#L229-L231)) delegates to `Blob.zstd()` ([`spec.py:208-216`](../src/fsbpyr/payload/spec.py#L208-L216)), which splits any blob `> 8 MiB` (`ZSTD_PIECE`) into `Codec.ZSTD_PIECES`, and neither [`writer.py:409-440`](../src/fsbpyr/payload/writer.py#L409-L440) nor [`reader.py:230-236`](../src/fsbpyr/payload/reader.py#L230-L236) enforces the `STARTUP` codec/size invariant. A `STARTUP` module between `8 MiB + 1 B` and `16 MiB` (or `0` bytes) passes both `writer.py` and `fsbpyr inspect` but aborts at runtime with `fatal("invalid startup module")`.

5. **Resource Leak on Header Validation Failure in `fsb_payload_open` ([`payload.c`](../src/fsbpyr/launcher/payload.c#L131-L146))**
   - If `mmap()` succeeds at lines 132–134 and `attach()` fails at lines 140–145, `fsb_payload_open` returns `false` without unmapping `map` or closing `fd` (affecting callers such as test/fuzz harnesses that do not immediately `exit`).

6. **Unchecked `uint64_t` to `size_t` Allocation Arithmetic in Internal C Entry Points ([`cache.c`](../src/fsbpyr/launcher/cache.c#L1032-L1033), [`integrity.c`](../src/fsbpyr/launcher/integrity.c#L157-L158))**
   - While `module.c` caps `job->count <= 4096` and `count <= 1024`, `fsb_cache_sync()` and `fsb_check_blobs()` themselves compute `malloc((size_t)job->count * sizeof *entries + 1)` and `malloc((size_t)(count + 1) * sizeof *first)` without checking for multiplication overflow on `size_t` (unlike `reserve()` in [`cache.c:359`](../src/fsbpyr/launcher/cache.c#L359)).

---

### 1.2 Boot & Runtime Subsystems (`src/fsbpyr/boot/`, `src/fsbpyr/runtime/`)

1. **Interpreter Flag Parsing Flaw Hijacks Application CLI Options in Re-Executed Children ([`reexec.py`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py#L43-L52), [`restart.py`](../src/fsbpyr/boot/_fsbpyr_rt/restart.py#L23-L33))**
   - `restart_argv()` ([`restart.py:63`](../src/fsbpyr/boot/_fsbpyr_rt/restart.py#L63)) validates `_only_interpreter_flags(argv, i)` after calling `after_flags(argv)`.
   - However, `reexec.child_command()` ([`reexec.py:43`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py#L43)) calls `after_flags(argv)` **without** `_only_interpreter_flags(argv, i)`. Because `after_flags` skips *any* argument starting with `"-"` (except `"-m"`, `"-c"`, `"-"`) and consumes the next token after `"-X"` or `"-W"`, if an app spawns itself with its own CLI flags such as `[sys.executable, "--my-flag", "-m", "foo"]` or `[sys.executable, "--", "/path/to/exe/script.py"]`, `reexec.child_command` treats `--my-flag` or `--` as a Python interpreter flag and hijacks the child into running module `foo` (with `--interpreter-reexec`) or exiting 70 (under D44).
   - Meanwhile, `mp._code_position()` ([`mp.py:117`](../src/fsbpyr/boot/_fsbpyr_rt/mp.py#L117)) advances by 2 only for `"-X"` (`i += 2 if argv[i] == "-X" else 1`), whereas `restart.py` advances by 2 for both `"-X"` and `"-W"`; and `_multiprocessing_child()` ([`_fsbpyr_boot.py:754`](../src/fsbpyr/boot/_fsbpyr_boot.py#L754)) searches `argv.index("-c")` from index 0 instead of `argv.index("-c", 1)`.

2. **`--interpreter-reexec` and D44 Miss Scripts Inside `fs-packages` ([`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py#L799-L820), [`reexec.py`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py#L92-L115))**
   - Modules in `fs-packages` (including built-ins `babel`, `botocore`, `certifi`, `litellm`, `matplotlib`, `torch`) have their `__file__` (`spec.origin`) set to `<cache>/runtime/<generation>/site/<pkg>/<mod>.py` ([`_fsbpyr_boot.py:280-290`](../src/fsbpyr/boot/_fsbpyr_boot.py#L280-L290)).
   - `_interpreter_child()` only checks `roots = (_PREFIX, _BIN) if _SCRIPT_APP else _PREFIX`, and `_script_module()` requires `spec.origin == prefix + below` (or `overlay + rest`). Thus, `[sys.executable, __file__]` from an `fs-package` module is neither recognized as a payload script under `--interpreter-reexec` nor refused by D44 on the first child (looping until D45's 64-slot limit kills it).

3. **TOCTOU in Parent Process Check Across `/proc/<ppid>/{exe,status,cmdline}` ([`mp.py`](../src/fsbpyr/boot/_fsbpyr_rt/mp.py#L136-L195))**
   - `_parent_runs_this_executable()` ([`mp.py:156-158`](../src/fsbpyr/boot/_fsbpyr_rt/mp.py#L156-L158)) and `_parent_target()` ([`mp.py:190`](../src/fsbpyr/boot/_fsbpyr_rt/mp.py#L190)) call `os.getppid()` three separate times across `stat("/proc/<ppid>/exe")`, `open("/proc/<ppid>/status")`, and `open("/proc/<ppid>/cmdline")`. Capturing `ppid = os.getppid()` once and re-checking `os.getppid() == ppid` after reading prevents mixing `/proc` attributes if the parent exits mid-check.

4. **Multi-Command `<exe> COMMAND` Dispatcher Shadowed When Binary Name Matches a Command ([`commands.py`](../src/fsbpyr/boot/_fsbpyr_rt/commands.py#L40-L47), [`cli.py`](../src/fsbpyr/cli.py#L258-L260))**
   - When a multi-command project `acme` defines console scripts `acme` and `acme-db`, [`cli.py:259-260`](../src/fsbpyr/cli.py#L259-L260) prints:
     `fsbpyr: commands: acme, acme-db; start one through its link, or as acme COMMAND`
   - However, `commands.find()` ([`commands.py:43-46`](../src/fsbpyr/boot/_fsbpyr_rt/commands.py#L43-L46)) checks `named(commands, argv[0])` *before* `argv[1] in commands`. Because `basename(argv[0])` is `"acme"`, running `dist/acme acme-db` runs `acme` with `"acme-db"` as `sys.argv[1]` instead of dispatching to `acme-db`.

5. **Aliased Namespace Packages Below `.pth` / `sys.path` Subdirectories Bypass `PayloadNamespaceLoader` ([`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py#L434-L458))**
   - In `PayloadMetaFinder.find_spec()` ([`_fsbpyr_boot.py:438-440`](../src/fsbpyr/boot/_fsbpyr_boot.py#L438-L440)), if `_fsbpyr.find_module(fullname)` is `None`, it returns `None`. For a namespace package living only under a `.pth` subdirectory (e.g., `<exe>/pkg/_vendor/ns`), its module record in the payload is `pkg._vendor.ns`, so `_fsbpyr.find_module("ns")` is `None`. `PathFinder` then constructs a stock `NamespaceLoader` whose `importlib.resources.files(ns)` fails on `<exe>/pkg/_vendor/ns`. Meanwhile, the fallback `else location[len(_PREFIX) :]` in `PayloadNamespaceLoader.get_resource_reader()` ([`_fsbpyr_boot.py:403-404`](../src/fsbpyr/boot/_fsbpyr_boot.py#L403-L404)) (`# its directory, also below a .pth root`) is unreachable dead code.

6. **Uncaught Exception Hook Walks Suppressed `__context__` (`raise ... from None`) ([`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py#L526-L563))**
   - Both `_missing_module()` ([`_fsbpyr_boot.py:535`](../src/fsbpyr/boot/_fsbpyr_boot.py#L535)) and `_inside_executable_note()` ([`_fsbpyr_boot.py:562`](../src/fsbpyr/boot/_fsbpyr_boot.py#L562)) traverse `exc = exc.__cause__ or exc.__context__` without checking `exc.__suppress_context__`. If an application catches an optional `ModuleNotFoundError` and raises its own error with `raise AppError(...) from None`, `_excepthook` prints a false note claiming the optional module must be added to dependencies.

7. **Tool Cache GC Never Collects Downloaded EL8 Stubs or Leftover Stub Temp Files ([`collect.py`](../src/fsbpyr/runtime/collect.py#L33-L57) vs. [`stubs.py`](../src/fsbpyr/runtime/stubs.py#L181-L226))**
   - `stubs.stub_for()` ([`stubs.py:181-226`](../src/fsbpyr/runtime/stubs.py#L181-L226)) stores downloaded EL8 stubs in `<cache>/downloads/fsbpyr-stub-<py>-<target>-glibc<baseline>-<id>` and temp files as `.fsbpyr-stub-...`. Neither pattern is in `NAMES[DOWNLOADS]` or `needed()` in [`collect.py:33-57`](../src/fsbpyr/runtime/collect.py#L33-L57), so obsolete EL8 stubs and interrupted stub downloads accumulate indefinitely.

8. **RPM Hardlink + `_RPM_SKIP` Interaction in Sysroot Extraction ([`rpm.py`](../src/fsbpyr/runtime/rpm.py#L126-L142), [`sysroot.py`](../src/fsbpyr/runtime/sysroot.py#L58-L61))**
   - `rpm.payload_tar()` emits the `REGTYPE` entry carrying the file body under the last cpio path of a hardlink set and emits earlier paths as `LNKTYPE` pointing to that last path. If an RPM hardlink group has an earlier path outside `_RPM_SKIP` and its final cpio path inside `_RPM_SKIP`, `sysroot._lean()` drops the `REGTYPE` member carrying the data while keeping the `LNKTYPE` member.

9. **Truncated ELF Header Handling in `selftest.interpreter()` ([`selftest.py`](../src/fsbpyr/boot/_fsbpyr_rt/selftest.py#L28-L41))**
   - `interpreter(header)` does not verify `len(header) >= 0x3A`, `at + 40 <= len(header)`, or `start + size <= len(header)` with a NUL terminator inside the slice, returning a truncated path prefix instead of `None` on a truncated ELF header.

---

### 1.3 Builder, CLI, Packaging & ELF (`src/fsbpyr/*.py`)

1. **`scan._main_guarded` Treats `if __name__ != "__main__":` and `else:` Branches as Main-Guarded ([`scan.py`](../src/fsbpyr/scan.py#L103-L115))**
   - `_main_guarded()` ([`scan.py:103-115`](../src/fsbpyr/scan.py#L103-L115)) checks `isinstance(node.test, ast.Compare)` and `node.test.left.id == "__name__"` without checking `isinstance(op, ast.Eq)` in `node.test.ops`, and uses `node.end_lineno` (which includes `node.orelse`). As a result, code inside `if __name__ != "__main__":` or the `else:` block of `if __name__ == "__main__":`—which runs *on import*—is suppressed when scanning imported modules for `__file__`-relative reads.

2. **Unbounded XZ Decompression in `selfupdate.py` and `dispatch.py` ([`selfupdate.py`](../src/fsbpyr/selfupdate.py#L277-L304), [`dispatch.py`](../src/fsbpyr/dispatch.py#L76))**
   - `fetch_checked()` ([`selfupdate.py:292`](../src/fsbpyr/selfupdate.py#L292)) defaults `limit: int | None = None`, and neither `selfupdate.update()` ([`selfupdate.py:451`](../src/fsbpyr/selfupdate.py#L451)) nor `dispatch._fetch()` ([`dispatch.py:76`](../src/fsbpyr/dispatch.py#L76)) passes a `limit` (contradicting [`wiki/Dev-Build-Modules.md:1616`](../wiki/Dev-Build-Modules.md#L1616)). Furthermore, `_unpack()` ([`selfupdate.py:283`](../src/fsbpyr/selfupdate.py#L283)) calls `decoder.decompress(chunk)` without `max_length`, allowing a single 1 MiB XZ chunk to expand unboundedly in RAM before `out.tell() > limit` is checked.

3. **Leaked Temp File on `OSError` in `_stage_extras` ([`selfupdate.py`](../src/fsbpyr/selfupdate.py#L373-L383))**
   - In `_stage_extras()` ([`selfupdate.py:374-382`](../src/fsbpyr/selfupdate.py#L374-L382)), if `out.write(content)` or `temporary.chmod(...)` fails after `tempfile.mkstemp`, the `except OSError:` block warns and `continue`s without unlinking `temporary`.

4. **Domain Exceptions Escaping `cli.py` as Unhandled Internal Bugs (`_internal_error` Traceback)**
   - `UnsupportedHost` ([`targets.py:18`](../src/fsbpyr/targets.py#L18), subclass of `ValueError`) is not caught in `build_command()` ([`cli.py:213-215`](../src/fsbpyr/cli.py#L213-L215)) or `runtime_build()` ([`cli.py:521`](../src/fsbpyr/cli.py#L521), which catches `LookupError` instead of `ValueError`).
   - `UndefinedComparison` / `UndefinedEnvironmentName` from `packaging.markers.Marker.evaluate` are caught in `tools._holds()` ([`tools.py:173-175`](../src/fsbpyr/tools.py#L173-L175)), but **not** in [`closure.py:140`](../src/fsbpyr/closure.py#L140), [`foreign.py:362,382`](../src/fsbpyr/foreign.py#L362), [`pipenv.py:149`](../src/fsbpyr/pipenv.py#L149), [`tools.py:212`](../src/fsbpyr/tools.py#L212), or [`venvdeps.py:169`](../src/fsbpyr/venvdeps.py#L169).
   - `ElfError` ([`elf.py:30`](../src/fsbpyr/elf.py#L30)) is not caught in `build._glibc_needs()` ([`build.py:194`](../src/fsbpyr/build.py#L194)), so a non-ELF or foreign-arch file with `.so` in its name inside a locally-built wheel crashes the build before [`classify.py:424`](../src/fsbpyr/classify.py#L424) can warn and skip it.

5. **ELF Parser Bounds Checks ([`elf.py`](../src/fsbpyr/elf.py#L112-L156))**
   - In `_dynamic()` ([`elf.py:154-156`](../src/fsbpyr/elf.py#L154-L156)), if `PT_DYNAMIC`'s `size` (`p_filesz`) is not a multiple of `_DYN.size` (16), `range(offset, offset + min(size, ...), 16)` reads up to 15 bytes past `offset + size`.
   - In `_file_offset()` ([`elf.py:118-122`](../src/fsbpyr/elf.py#L118-L122)), `vaddr <= address < vaddr + filesz` does not verify `offset + filesz <= len(data)` or that `DT_STRTAB + DT_STRSZ` stays within the matched `PT_LOAD` segment.
   - In `section()` ([`elf.py:112-114`](../src/fsbpyr/elf.py#L112-L114)), an out-of-bounds `sh_name` (`entry[0] >= len(names)`) silently evaluates `names[entry[0]:]` to `b""` instead of raising `ElfError`.

6. **Foreign Lockfile Edge Cases ([`poetry1.py`](../src/fsbpyr/poetry1.py#L16-L109), [`foreign.py`](../src/fsbpyr/foreign.py#L143-L146), [`pipenv.py`](../src/fsbpyr/pipenv.py#L121-L166))**
   - `poetry1._CARET_TILDE` ([`poetry1.py:16`](../src/fsbpyr/poetry1.py#L16)) (`r"([\^~])\s*(\d+(?:\.\d+)*)$"`) rejects pre/post/dev versions such as `^1.2.0a1`; and `project_table()` ([`poetry1.py:105`](../src/fsbpyr/poetry1.py#L105)) overwrites `optional[canonicalize_name(name)] = line` when an optional dependency has multiple platform-specific constraint dicts.
   - `foreign._poetry_marker()` ([`foreign.py:143-146`](../src/fsbpyr/foreign.py#L143-L146)) only reads `marker.get("main")` on Poetry 2.1 locks, dropping markers on packages reachable only via `--extra` groups.
   - `pipenv.entries()` ([`pipenv.py:121-126`](../src/fsbpyr/pipenv.py#L121-L126)) drops all hashes (`files = {}`) when a `"file"`/URL entry has `len(digests) > 1`; and `pipenv.export()` ([`pipenv.py:160-161`](../src/fsbpyr/pipenv.py#L160-L161)) collects `extras` from all entries *before* filtering by `_applies(entry.marker, ...)`.

---

### 1.4 CI/CD, Release Scripts, and `install.sh.in`

1. **Unpinned `curl | sh` and Incomplete Attestation Check in [`.github/workflows/attest-example.yml`](../.github/workflows/attest-example.yml#L32-L39)**
   - Line 32 runs `curl -LsSf https://astral.sh/uv/install.sh | ... sh` inside a job with `id-token: write` and `attestations: write`, whereas all other workflows use [`tools/release/install-uv.sh`](../tools/release/install-uv.sh) with a pinned SHA-256.
   - Lines 38–39 run `gh attestation verify` without `--source-ref` and without checking `SHA256SUMS.sig` (`fsbpyr verify`), which—as documented in [`CHANGELOG.md:78-79`](../CHANGELOG.md#L78-L79)—accepts workflow runs from non-release refs.

2. **`passphrase.sh` Executes Passphrase Files If `+x` Is Set and Uses Looser Permission Masks Than Documented ([`passphrase.sh`](../tools/release/passphrase.sh#L23-L31))**
   - [`passphrase.sh:26-27`](../tools/release/passphrase.sh#L26-L27) checks `if [ -x "$file" ]; then passphrase=$("$file" </dev/null)`. Because removable USB drives formatted as VFAT/exFAT/NTFS mount files with execute bits set by default (e.g., `0700` or `0755`), a plain text passphrase file stored on a removable medium (as suggested in [`wiki/Dev-Repository.md:114`](../wiki/Dev-Repository.md#L114)) is **executed as a program** instead of read as data.
   - Additionally, lines 25 and 29 only check `022` and `044` (allowing `0755` for programs and `0611` for files), whereas [`wiki/Dev-Repository.md:81-82`](../wiki/Dev-Repository.md#L81-L82) states mode `600` or tighter for files and `700` or tighter for programs.

3. **`install.sh.in` Predictable Temp Names and Blind Reuse of `$INSTALL_DIR/env` ([`install.sh.in`](../tools/release/install.sh.in#L218-L340))**
   - `fetch_executable()` ([`install.sh.in:218-229`](../tools/release/install.sh.in#L218-L229)) and `put_extra()` ([`install.sh.in:272`](../tools/release/install.sh.in#L272)) redirect `>` to predictable PID-based names (`$INSTALL_DIR/.fsbpyr.$$.tmp`, `$file.$$.tmp`) without `rm -f` or `mktemp -p "$INSTALL_DIR"`.
   - `write_env_files()` ([`install.sh.in:302-304`](../tools/release/install.sh.in#L302-L304)) checks `if [ -e "$INSTALL_DIR/env" ]; then` and leaves any existing file at `$INSTALL_DIR/env` untouched, after which `modify_path()` ([`install.sh.in:336-340`](../tools/release/install.sh.in#L336-L340)) adds `. "$expr/env"` to `~/.profile`, `~/.bashrc`, and `~/.zshrc`. If `FSBPYR_INSTALL_DIR` points to a directory that already has an unrelated `env` binary or script, new shells will attempt to dot-source it.

4. **Single-Quote Breakout in `Makefile` Release Targets ([`Makefile`](../Makefile#L265-L311))**
   - `Makefile` lines 265, 268, 271, 275, 288, and 311 interpolate `'$(VERSION)'` and `'$(KEY)'` directly into shell commands before the scripts validate them.

5. **`tools/runner-disk.sh` Destructive `rm -rf` Without CI Guard ([`runner-disk.sh`](../tools/runner-disk.sh#L50-L55))**
   - Running `runner-disk.sh --root /` deletes `/usr/lib/jvm`, `/usr/share/dotnet`, `/opt/ghc`, etc., without checking `GITHUB_ACTIONS=true` or `RUNNER_ENVIRONMENT=github-hosted`.

---

## 2. Gaps Between Code and Documentation

| Location in Docs / Wiki / Stubs | Location in Code | Discrepancy |
|---|---|---|
| [`stubs/_fsbpyr.pyi`](../stubs/_fsbpyr.pyi#L23-L61) | [`module.c`](../src/fsbpyr/launcher/module.c#L786-L827), [`sig.c`](../src/fsbpyr/launcher/sig.c#L441-L447) | `Stats` `TypedDict` is missing `hole_bytes: int`, and the 4 private signing functions (`_sha256`, `_key_new`, `_sign`, `_verify`) are absent from `_fsbpyr.pyi`. |
| [`wiki/Dev-Launcher.md`](../wiki/Dev-Launcher.md#L138-L658) | [`sig.c`](../src/fsbpyr/launcher/sig.c), [`sig.h`](../src/fsbpyr/launcher/sig.h) | Line 138 mentions *"the eleven `.c` files"* and *"the ten headers"*, but `## Source files` only documents 10 `.c` files and 9 `.h` files—`sig.c` and `sig.h` have no subsections. |
| [`docs/format.md`](format.md#L236-L325), [`wiki/Dev-Executable-Format.md`](../wiki/Dev-Executable-Format.md#L328-L330) | [`writer.py`](../src/fsbpyr/payload/writer.py#L409-L440), [`reader.py`](../src/fsbpyr/payload/reader.py#L230-L236) | `docs/format.md:236` omits *"or zstd frames in pieces"* from `MODULES` invariants; both "Who checks what" tables claim `writer.py` and `reader.py` verify that `STARTUP` module code is `ZSTD`/`BLOCK` of `1 B to 16 MiB`, but only `main.c:96-97` checks this. |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md#L125-L131) | [`Makefile`](../Makefile#L149-L155), [`wiki/Dev-Testing.md`](../wiki/Dev-Testing.md#L549) | `CONTRIBUTING.md` lists only 13 e2e test files in `make test-ci`, omitting `test_signing.py` and `test_selfupdate.py` (`Makefile` and `wiki/Dev-Testing.md` list all 15). |
| [`wiki/Dev-Build-Modules.md`](../wiki/Dev-Build-Modules.md#L1616) | [`selfupdate.py`](../src/fsbpyr/selfupdate.py#L292), [`dispatch.py`](../src/fsbpyr/dispatch.py#L76) | Wiki states `fetch_checked(url, digest, target, limit)` unpacks *"at most `limit` bytes"*, but `limit` defaults to `None` and is omitted by both `selfupdate.py` and `dispatch.py`. |
| [`wiki/Dev-Runtime-Modules.md`](../wiki/Dev-Runtime-Modules.md#L72-L86) | [`stubs.py`](../src/fsbpyr/runtime/stubs.py#L224-L226), [`collect.py`](../src/fsbpyr/runtime/collect.py#L33-L57) | Both `collect.py` and `wiki/Dev-Runtime-Modules.md` omit the companion-release EL8 stubs stored in `<cache>/downloads/`. |
| [`wiki/Dev-Repository.md`](../wiki/Dev-Repository.md#L81-L82) | [`passphrase.sh`](../tools/release/passphrase.sh#L25-L29) | Wiki specifies mode `600` (passphrase file) / `700` (program) or tighter; `passphrase.sh` only checks bitwise masks `022` and `044`. |
| [`docs/performance.md`](performance.md#L25-L601), [`docs/executable-parts.md`](executable-parts.md#L4-L30) | [`wiki/Dev-Performance.md`](../wiki/Dev-Performance.md#L40-L482) | `docs/performance.md` still has `0.25.0` startup tables, `0.15.0` runtime build tables, and omits `reads`/`hole_bytes` from `stats()`, while `wiki/Dev-Performance.md` has `0.30.0` figures and all 5 `stats()` keys. |
| [`docs/signing.md`](signing.md#L127-L156), [`docs/IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md#L3) | [`signing.py`](../src/fsbpyr/signing.py#L485-L496), [`release-signing.sh`](../tools/release/release-signing.sh#L30) | `docs/signing.md` lines 127–134 and 156 still say `release-signing.sh` does not set `OPENSSL_CONF=/dev/null` and that typed passphrases use `os.fsencode`; `docs/IMPLEMENTATION_PLAN.md` header still says `fsbpyr 0.26.0` and that signed releases are not implemented. |

---

## 3. Code Performance

1. **`MADV_DONTNEED` Page-Rounding Down Evicts Payload Section Table Pages ([`payload.c`](../src/fsbpyr/launcher/payload.c#L286-L287), [`reader.py`](../src/fsbpyr/payload/reader.py#L691-L695))**
   - In `count_read()` ([`payload.c:286-287`](../src/fsbpyr/launcher/payload.c#L286-L287)):
     ```c
     uint64_t page = (uint64_t)sysconf(_SC_PAGESIZE), start = p->data_start / page * page;
     madvise((void *)(p->base + start), (size_t)(p->size - start), MADV_DONTNEED);
     ```
     Because `p->data_start` is 16-byte aligned, rounding `p->data_start` **down** to a page boundary includes the page holding the tail of the section tables (`DIGESTS`, `STARTUP`, and on 64 KiB page kernels potentially `MODULES`, `FILES`, and `STRTAB`), evicting them and causing page faults on subsequent lookups. Round `start` **up**: `(p->data_start + page - 1) / page * page`. The same applies to `Executable._dropper()` ([`reader.py:691-695`](../src/fsbpyr/payload/reader.py#L691-L695)).

2. **Unnecessary `PySet` + `PyList_Sort` in `list_dir` ([`module.c`](../src/fsbpyr/launcher/module.c#L199-L231))**
   - The `FILES` table is strictly sorted in byte order and `writer.py` forbids any path from being both a file and a directory prefix. Thus, the adjacent deduplication at [`module.c:213-214`](../src/fsbpyr/launcher/module.c#L213-L214) already emits each child name once in sorted order. Allocating a `PySet`, hashing every entry, converting to a `PyList`, and calling `PyList_Sort` is redundant; appending directly to `PyList_New(0)` suffices.

3. **Linear Python Scans Over All Payload `FILES` in `native.py` and `selftest.py` ([`native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py#L214-L218), [`selftest.py`](../src/fsbpyr/boot/_fsbpyr_rt/selftest.py#L53-L61))**
   - Importing *any* `fs-package` (such as pure-Python `certifi` or `botocore`) calls `materialize_package()` -> `_shipped_libraries()` ([`native.py:214-218`](../src/fsbpyr/boot/_fsbpyr_rt/native.py#L214-L218)), which loops over `range(_fsbpyr.info()["files"])` and allocates an 8-element Python tuple via `_fsbpyr.file_at(i)` for every file in the payload (up to 20,000+ files in large apps). Similarly, `bundled_library()` ([`native.py:292-311`](../src/fsbpyr/boot/_fsbpyr_rt/native.py#L292-L311)) scans `range(files)` up to twice.

4. **Redundant `Project.load` / `Package.load` and `git` Subprocesses During Build ([`cli.py`](../src/fsbpyr/cli.py#L217-L239), [`build.py`](../src/fsbpyr/build.py#L470-L756), [`vcs.py`](../src/fsbpyr/vcs.py#L41-L99))**
   - For loose scripts/directories, `Project.load` / `Package.load` is invoked up to **4 times** per build (`cli.py:217`, `cli.py:239`, `build.py:756`, `build.py:470`), re-running `git ls-files`, reading files, and parsing ASTs each time.
   - `vcs.commit_time` and `vcs.git_state` each invoke `_head(directory)` (2 `git` subprocesses) plus `git show` / `git tag`, spawning 6+ `git` subprocesses that can be consolidated.

5. **Native `.so` Files Opened, Mapped, and ELF-Parsed 4–5 Times Per Build ([`natives.py`](../src/fsbpyr/natives.py#L239-L451), [`classify.py`](../src/fsbpyr/classify.py#L422-L709))**
   - Each `.so` is opened and parsed separately in `classify._add_native`, `natives._libraries`, `natives._direct_dependencies`, `natives._warn_search_paths`, and `classify._Collector.glibc`. Caching `DynamicInfo` and `needed_versions` per native file eliminates 75% of ELF parsing passes.
   - Also in `classify._named_by_python()` ([`classify.py:685-701`](../src/fsbpyr/classify.py#L685-L701)), if any `.so` in the venv is unreached, every packaged `.py` file is re-read from disk from scratch.

6. **Double Read of ~20 MiB Stub in `stubs.stub_for` ([`stubs.py`](../src/fsbpyr/runtime/stubs.py#L227-L228)) and Repeated `/proc` Opens in `outside_owner` ([`way.c`](../src/fsbpyr/launcher/way.c#L41-L55), [`gc.py`](../src/fsbpyr/boot/_fsbpyr_rt/gc.py#L317-L327))**
   - [`stubs.py:227-228`](../src/fsbpyr/runtime/stubs.py#L227-L228) calls `final.read_bytes()` twice in a row on the 20 MiB stub.
   - In rootless containers where parent directories are owned by `65534` (`nobody`), `outside_owner` opens `/proc/sys/kernel/overflowuid` and `/proc/self/uid_map` on every path component instead of caching the result once per process.

---

## 4. Code Maintenance

1. **Bug in Test Helper `workflow_jobs` ([`test_release.py`](../tests/integration/test_release.py#L1780-L1782)) Silently Skips All Hyphenated CI Jobs**
   - `re.findall(r"^  ([a-z]+):\n(.*?)(?=^  [a-z]+:\n|\Z)", workflow, re.M | re.S)` only matches `[a-z]+` (no hyphens `-`).
   - As a result, `test_a_run_needs_every_job_to_pass_for_the_commit_to_be_released()` ([`test_release.py:1804-1810`](../tests/integration/test_release.py#L1804-L1810)) completely misses `vm-smoke`, `el8-python`, `el8-stub`, `el8-build`, `el8-tests`, `el8-portability`, `el8-corpus`, and `el8-vm-smoke` in `full-suite.yml` (and appends their entire YAML text onto the end of `portability`). Changing `[a-z]+` to `[a-z0-9-]+` fixes this immediately.

2. **Hardcoded Python Versions (`3.12 3.13 3.14 3.15`) and Container Digests Duplicated Across 15+ Locations**
   - The list `3.12 3.13 3.14 3.15` is hardcoded independently in `build.yml`, `full-suite.yml`, `release.yml`, `el8.yml`, `Makefile`, `tools/bootstrap.sh`, `tools/release/make-install.sh`, `tools/release/check-release.sh`, `tools/release/release-sign.sh`, and `tests/support/requires.py`.
   - The `debian:trixie@sha256:...` digest is duplicated in 11 places across `Containerfile` and the workflow files.

3. **Fragile Text/Regex Scraping Instead of Structured Parsing in `tools/` and `tests/`**
   - [`tools/upstream-watch.sh:17-20`](../tools/upstream-watch.sh#L17-L20) scrapes [`pins.py`](../src/fsbpyr/runtime/pins.py) using `grep -A4`.
   - [`test_release_drafts.py:31-35`](../tests/integration/test_release_drafts.py#L31-L35) and [`test_el8_workflow.py:42-46`](../tests/integration/test_el8_workflow.py#L42-L46) extract workflow steps using `.split("\n      #", 1)[0]`, which truncates any `run: |` script containing a 6-space-indented comment.
   - [`native.py:34`](../src/fsbpyr/boot/_fsbpyr_rt/native.py#L34) and [`gc.py:55`](../src/fsbpyr/boot/_fsbpyr_rt/gc.py#L55) decide whether to `os._exit(70)` (security refusal) vs. raise a catchable exception by matching the message prefix `"refusing cache directory "` from [`module.c`](../src/fsbpyr/launcher/module.c).

4. **Duplicated Helpers With Divergent Edge-Case Behavior**
   - `RECORD` CSV first-column parsing is written in 4 places; `tools._has_code()` ([`tools.py:297`](../src/fsbpyr/tools.py#L297)) uses `line.partition(",")[0]` instead of `csv.reader`.
   - `HOME` / `XDG_CACHE_HOME` / `FSBPYR_CACHE_DIR` resolution is implemented in [`due.c:39-96`](../src/fsbpyr/launcher/due.c#L39-L96), [`native.py:58-83`](../src/fsbpyr/boot/_fsbpyr_rt/native.py#L58-L83) (`os.path.abspath`), and [`registry.py:89-118`](../src/fsbpyr/runtime/registry.py#L89-L118) (leaves relative `FSBPYR_CACHE_DIR` un-normalized).
   - [`Makefile:313-314`](../Makefile#L313-L314) `make clean` only runs `rm -rf dist`, leaving `.coverage` and `coverage.xml` in the working tree.

---

## 5. PRs, Release Process, and Quality Assurance

1. **Zero CI Runs on Pull Requests (Including Dependabot PRs)**
   - None of `.github/workflows/*.yml` has a `pull_request` trigger, even though [`.github/dependabot.yml`](../.github/dependabot.yml) opens weekly PRs for `github-actions` and `uv`, and [`CONTRIBUTING.md:3`](../CONTRIBUTING.md#L3) invites external PRs. Running at least `make check` and `make test-ci` on `pull_request` would validate Dependabot and contributor PRs before merge.

2. **EL8 Stubs for Python 3.12, 3.13, and 3.15 Are Never Executed in Any Test**
   - [`.github/workflows/full-suite.yml:631-642`](../.github/workflows/full-suite.yml#L631-L642) links all 8 EL8 stubs (`3.12`–`3.15` × `x86_64`/`aarch64`), and [`el8.yml:79`](../.github/workflows/el8.yml#L79) publishes all 8. However, `el8-build`, `el8-tests`, `el8-portability`, `el8-corpus`, `el8-vm-smoke`, and [`check-release.sh:536-548`](../tools/release/check-release.sh#L536-L548) execute **only Python `3.14`**. The `3.12`, `3.13`, and `3.15` EL8 stubs are only checked with `inspect --verify` and `el8-stub.py` symbol inspection—never booted once.
   - In addition, [`tests/conftest.py:142-145`](../tests/conftest.py#L142-L145) disables the `sanitized` (ASan/UBSan) launcher variant when `FSBPYR_TEST_GLIBC=2.28`.

3. **Signing Fuzzer (`sig_fuzz.c`) Ignores `FSBPYR_FUZZ_SECONDS` and Discards Its Discovered Corpus ([`test_fuzz.py`](../tests/e2e/test_fuzz.py#L26-L110))**
   - `SIG_FUZZ_SECONDS = 30` ([`test_fuzz.py:26`](../tests/e2e/test_fuzz.py#L26)) is hardcoded (ignoring `FSBPYR_FUZZ_SECONDS=600` in CI), and `test_the_readers_of_keys_survive_a_fuzzing_run()` ([`test_fuzz.py:94-110`](../tests/e2e/test_fuzz.py#L94-L110)) writes newly discovered inputs to `tmp_path / "found"` instead of a subdirectory of `FSBPYR_FUZZ_CORPUS`, so `actions/cache` never preserves `sig_fuzz` corpus progress across runs.

4. **`make check-c` and `llvm-cov` Are Non-Blocking (`continue-on-error: true`) ([`full-suite.yml`](../.github/workflows/full-suite.yml#L276-L338))**
   - Since `make check-c` is currently 100% clean (`0 of 11 sources with findings`), removing `continue-on-error: true` on `make check-c` in `full-suite.yml` will prevent regressions in the C launcher from slipping into `main`.

5. **64K-Page VM Smoke Tests (`vm-smoke`, `el8-vm-smoke`) Pass Green When Mirror Pins Expire ([`full-suite.yml`](../.github/workflows/full-suite.yml#L620-L622))**
   - When [`tools/vm-smoke.py`](../tools/vm-smoke.py) exits `3` (a pinned kernel/rootfs URL in [`tools/vm-pins.toml`](../tools/vm-pins.toml) expired/404'd), the job logs a `::warning::` and exits `0`, allowing [`green-run.sh`](../tools/release/green-run.sh) to approve a release without running the 64K-page VM check.

---

## 6. Any Other Missing Parts

1. **No Signature Verification in `install.sh` Bootstrap**: While `fsbpyr self update` and `fsbpyr verify` check the dual Ed25519 + ML-DSA-65 signature (`SHA256SUMS.sig`), [`install.sh.in`](../tools/release/install.sh.in) only checks the SHA-256 table embedded inside `install.sh` itself and runs `"$STAGE" inspect --verify "$STAGE"` (unkeyed D31 digest). Having `install.sh` also download `SHA256SUMS` + `SHA256SUMS.sig` and run `"$STAGE" verify --project fsbpyr --checksums ...` would close the gap where a compromised release asset is checked against its own self-consistent `install.sh`.
2. **No `shellcheck` in `make check` / CI**: There are 27 shell scripts under `tools/` and `tools/release/` that are critical to the release and signing pipeline, but `shellcheck` is not part of `make check` or `full-suite.yml`.
3. **No Property/Fuzz Tests for [`rpm.py`](../src/fsbpyr/runtime/rpm.py) and [`ar.py`](../src/fsbpyr/runtime/ar.py)**: Unlike [`elf.py`](../src/fsbpyr/elf.py) (which has [`test_elf_mutation.py`](../tests/unit/test_elf_mutation.py)), the custom binary parsers for RPM headers/cpio and `ar` archives lack Hypothesis fuzz tests.
4. **Missing `.github/PULL_REQUEST_TEMPLATE.md` and `.github/ISSUE_TEMPLATE/config.yml`**: Adding a PR template reminding contributors of the packaging-regression-test and `wiki-lint` rules in `CONTRIBUTING.md`, and an issue template `config.yml` linking security reports to GitHub Private Security Advisories (`SECURITY.md`), will round out repository governance.
