# Security

What fsbpyr protects, from whom, and where that stops; where each defence sits in the code; the
known limits; and how to report a vulnerability. The threat model is
[docs/security.md](../docs/security.md) at v@VERSION@, the reporting policy
[SECURITY.md](../SECURITY.md), the user's side [Security](User-Security.md). Security has two
sides: the release chain, in which a stolen GitHub account gets nothing a client accepts
([Signatures](#signatures)), and the defences in every executable
([What stops what](#what-stops-what)).

## Reporting a vulnerability

Report it privately, as a
[GitHub security advisory](https://github.com/devitops-com/fsbpyr/security/advisories/new),
not as a public issue ([SECURITY.md](../SECURITY.md) has the details). Reports are acknowledged
within 7 days. Say which fsbpyr is affected (`fsbpyr --version`) and how to reproduce the
problem; for a built executable, `fsbpyr inspect --json` on it helps too. Only the latest release
is supported: fixes come in a new release, and older releases are not patched.

Every executable, fsbpyr's own included, carries the runtime of the fsbpyr that built it: CPython
from [python-build-standalone](https://github.com/astral-sh/python-build-standalone), linked
statically into the launcher with the libraries of its built-in modules (OpenSSL, SQLite, expat,
zlib, libffi, liblzma, mpdecimal, bzip2, zstd, ncurses, libedit and libuuid), never the target's
copies. A fix in any of them reaches an executable only when it is rebuilt with an fsbpyr that
carries it. `fsbpyr inspect` names the runtime (`build_info.runtime`) and, where recorded, those
libraries' versions (`components`: `component_versions` in
[`runtime/pbs.py`](../src/fsbpyr/runtime/pbs.py), zstd's by `zstd_version` from the launcher's
`libzstd.a`). The app's dependencies and the libraries their wheels bundle come from its lock file
and take a rebuild too. SECURITY.md says how soon a release with a fixed runtime follows a
python-build-standalone release.

## Threat model

### Assets

- The code an executable runs: its payload, the extracted native libraries in the cache, and
  the embedded runtime.
- The user's account and files: an executable runs with its caller's rights.
- The privileges of programs that run an executable for someone else, such as a sudo rule.

### Attackers in scope

- **Corrupt or modified executables:** a bit flip, a truncated download, a changed byte
  anywhere in the file.
- **Other local users** who can write where the cache is, or who can redirect the path to it.
- **A caller with no right to run Python:** a shell, script or sudo rule that runs the app
  with arguments crafted to look like multiprocessing or interpreter re-exec.
- **A caller that chooses the environment of a privileged process.**

### Out of scope

- The user themselves, and any process running as that user: it can already do what the
  executable can.
- Root.
- An attacker who rewrites the executable and its digest together ([Known limits](#known-limits)).
- The app's own code and dependencies: an executable runs what it was built from.
- **Code the build host runs for a project that has a build system**: its build backend
  (`uv build`), the builds of dependencies' sdists, and the entry module the smoke test imports
  (`--entry`, an inferred module, a `module:function` command), all with the builder's rights.
  Build only projects you trust. Projects without a build system, directories of scripts and
  single scripts are built without running their scripts
  ([The project on the build host](#the-project-on-the-build-host)).

## Trust boundaries

![The trust boundaries of fsbpyr: external inputs (package index, uv, python-build-standalone, the pinned sysroot packages, the companion release with the glibc 2.28 stubs) and the project tree feed the build machine, which makes an executable ending in its digest; it crosses a transit zone to the target machine, where the launcher and the boot code check it, the cache, callers' arguments, the files beside it and the link directory, and other local users are kept out of the cache and the slot file. Numbered labels mark the checks.](images/security-boundaries.svg)

Four zones, as drawn: the **external inputs**, the **build machine**, which turns them and the
project tree into an executable ending in its digest, the file **in transit**, where nothing in
it can prove where it came from, and the **target machine**, where other local users and callers
can reach the file, the per-user cache, the slot file, the files beside the executable and a
link directory.

### Where each check happens

| | Check | Code |
|---|---|---|
| [1] | The python-build-standalone archive and each glibc sysroot package are checked against their pinned SHA-256 before extraction ([build inputs](#fsbpyrs-own-cache-and-the-build-inputs)) | `download` and `fetch` in [`runtime/pbs.py`](../src/fsbpyr/runtime/pbs.py), `_package` in [`runtime/sysroot.py`](../src/fsbpyr/runtime/sysroot.py), pins in [`runtime/pins.py`](../src/fsbpyr/runtime/pins.py) |
| [2] | A built runtime's stub and standard library are checked on load against the digests in `runtime.json` (`corrupt runtime file`); a self-hosted fsbpyr verifies its whole file before it reuses its embedded runtime | `Runtime.save`, `Runtime.load` and `Runtime.from_executable` in [`runtime/registry.py`](../src/fsbpyr/runtime/registry.py) |
| [3] | uv installs from the lock: `uv sync --locked` for `uv.lock` and a script's `<script>.lock`; a Poetry, PDM or Pipenv lock becomes a staging requirements file with every hash it records, required when every line has one; `--require-hashes` for a requirements file with hashes, or any under `--strict` | `staging_plan` in [`staging.py`](../src/fsbpyr/staging.py), [`foreign.py`](../src/fsbpyr/foreign.py), [`pipenv.py`](../src/fsbpyr/pipenv.py) |
| [4] | The build ends the file with the digest of everything before it, read back from the written file | `assemble` and `write_temporary` in [`payload/writer.py`](../src/fsbpyr/payload/writer.py) |
| [5] | [Checks from outside](#checking-an-executable-from-outside): attestations, the installer's SHA-256, `fsbpyr inspect --verify`, and the in-process checks of `self update` and the dispatch to another Python | [`inspection.py`](../src/fsbpyr/inspection.py), [`selfupdate.py`](../src/fsbpyr/selfupdate.py), [`dispatch.py`](../src/fsbpyr/dispatch.py) |
| [6] | Every start hashes the whole file before it believes any byte of it, and refuses a privileged start | [`launcher/integrity.c`](../src/fsbpyr/launcher/integrity.c), `fsb_payload_open` in [`launcher/payload.c`](../src/fsbpyr/launcher/payload.c), `main` in [`launcher/main.c`](../src/fsbpyr/launcher/main.c) |
| [7] | [The cache](#the-cache): a trusted way from `/`, private directories, each file's digest checked before first use | [`launcher/way.c`](../src/fsbpyr/launcher/way.c), [`launcher/cache.c`](../src/fsbpyr/launcher/cache.c) |
| [8] | [The instance limit](#the-instance-limit) uses only a slot file nobody else may use | [`launcher/slots.c`](../src/fsbpyr/launcher/slots.c) |
| [9] | [Multiprocessing children and helper interpreters](#multiprocessing-children-and-helper-interpreters) run as Python only in a child of this executable with the same ids | [`_fsbpyr_rt/mp.py`](../src/fsbpyr/boot/_fsbpyr_rt/mp.py), [`_fsbpyr_rt/reexec.py`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py) |
| [10] | [Projects without a build system](#the-project-on-the-build-host): no build backend and no script runs; their inputs are read as data | [`loose.py`](../src/fsbpyr/loose.py), `_uv_build` in [`staging.py`](../src/fsbpyr/staging.py), [`venvdeps.py`](../src/fsbpyr/venvdeps.py), [`importcheck.py`](../src/fsbpyr/importcheck.py), `_check_entrypoint` in [`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py) |
| [11] | [The files beside the executable](#scripts-and-the-files-beside-the-executable) are data: every module comes from the payload | `path_hook` and `_overlay_finder` in [`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py) |
| [12] | [`FSBPYR_INSTALL_LINKS`](#linking-commands-on-the-target) makes only symlinks to the executable, with plain names, and replaces nothing | `install` in [`_fsbpyr_rt/links.py`](../src/fsbpyr/boot/_fsbpyr_rt/links.py) |
| [13] | [A glibc 2.28 stub](#the-glibc-228-stub) must match its release's signed sums when downloaded, and carry this fsbpyr's runtime id | `stub_for`, `_download` and `_checked` in [`runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py), `stub_identity` in [`runtime/stub.py`](../src/fsbpyr/runtime/stub.py) |

## What stops what

The defences of docs/security.md, with the decision of the
[implementation plan](../docs/IMPLEMENTATION_PLAN.md) that introduced each (code comments cite
these numbers); the sections below have the details.

| Decision | Stops | Code |
|---|---|---|
| D17 | Fixed addresses: the launcher and runtime are a position-independent executable with RELRO, BIND_NOW, a non-executable stack and stack protector ([hardened launcher](#the-hardened-launcher)) | `COMPILE_FLAGS`, `LINK_FLAGS` in [`runtime/stub.py`](../src/fsbpyr/runtime/stub.py) |
| D19 | Corrupt code objects: every code blob is a checksummed zstd frame, required by readers, so `marshal.loads` never sees damaged bytes ([Code blobs are checksummed](#code-blobs-are-checksummed)) | `fsb_zstd_frame_ok` in [`launcher/payload.c`](../src/fsbpyr/launcher/payload.c); [`payload/writer.py`](../src/fsbpyr/payload/writer.py) and [`payload/reader.py`](../src/fsbpyr/payload/reader.py) |
| D20 | Another user planting a runtime or stub: a private cache root, and private directories below an app's ([build inputs](#fsbpyrs-own-cache-and-the-build-inputs)), runtime digests checked on load; a glibc 2.28 stub in `downloads/` has only the directory and its runtime id ([Known limits](#known-limits)) | `secure_directory`, `Runtime.load` in [`runtime/registry.py`](../src/fsbpyr/runtime/registry.py), `stub_for` in [`runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py) |
| D22 | A caller pretending to be multiprocessing: exact command lines, decoded never executed, from a parent that is this executable with the same ids ([below](#multiprocessing-children-and-helper-interpreters)) | [`_fsbpyr_rt/mp.py`](../src/fsbpyr/boot/_fsbpyr_rt/mp.py) |
| D23, D44 | The same for helper interpreters (`-m`, `-c`, a `.py` file of the app), run only with `--interpreter-reexec` but for the app's own command scripts ([below](#multiprocessing-children-and-helper-interpreters)) | [`_fsbpyr_rt/reexec.py`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py), `is_sibling` in [`_fsbpyr_rt/commands.py`](../src/fsbpyr/boot/_fsbpyr_rt/commands.py) |
| D45 | Another user denying a user's starts through the instance limit ([The instance limit](#the-instance-limit)) | `fsb_hold_slot` in [`launcher/slots.c`](../src/fsbpyr/launcher/slots.c) |
| D31 | A changed executable: the whole file is checked against the digest in its trailer before anything else is read ([integrity check](#the-whole-file-integrity-check)) | `fsb_check_integrity` in [`launcher/integrity.c`](../src/fsbpyr/launcher/integrity.c) |
| D41 | A privileged start ([Privileged starts are refused](#privileged-starts-are-refused)) | `main` in [`launcher/main.c`](../src/fsbpyr/launcher/main.c) |
| D14 (`FSBPYR_INSTALL_LINKS`) | A caller using the links feature to create files as another user ([Linking commands](#linking-commands-on-the-target)) | `install` in [`_fsbpyr_rt/links.py`](../src/fsbpyr/boot/_fsbpyr_rt/links.py), `_install_links` in [`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py) |
| D24 (the overlay) | A file planted beside the executable running as code: modules always come from the payload ([Scripts](#scripts-and-the-files-beside-the-executable)) | `_shown`, `_overlay_finder` and `path_hook` in [`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py) |
| D5 | Project code on the build host for projects without a build system, directories of scripts and single scripts ([The project on the build host](#the-project-on-the-build-host)) | [`loose.py`](../src/fsbpyr/loose.py), `work_tree_files` in [`vcs.py`](../src/fsbpyr/vcs.py), [`venvdeps.py`](../src/fsbpyr/venvdeps.py), [`foreign.py`](../src/fsbpyr/foreign.py), [`pipenv.py`](../src/fsbpyr/pipenv.py), [`importcheck.py`](../src/fsbpyr/importcheck.py) |
| D46 | A stub linked from other launcher sources, for another Python or target, or against another sysroot going into an app ([The glibc 2.28 stub](#the-glibc-228-stub)) | `identity` in [`launcher/main.c`](../src/fsbpyr/launcher/main.c), `stub_identity` in [`runtime/stub.py`](../src/fsbpyr/runtime/stub.py), `stub_for` and `paired` in [`runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py), `select_runtime` in [`runtime/registry.py`](../src/fsbpyr/runtime/registry.py) |

The service policies change no trust ([Service policies](#service-policies)).

## The defences in the code

### The whole-file integrity check

`fsb_payload_open` in [`launcher/payload.c`](../src/fsbpyr/launcher/payload.c), the first thing
`main` does, opens `/proc/self/exe` and calls `fsb_check_integrity`
([integrity.c](Dev-Launcher.md#integrityc-and-integrityh)) before it reads the trailer, maps the
payload or starts CPython. The [digest](Dev-Executable-Format.md#integrity-digest), in the
file's last 32 bytes, covers everything before them. OpenSSL's low-level SHA-256 computes it, not
the one-shot `SHA256()`, which may read `openssl.cnf` (`$OPENSSL_CONF`) before the check is done;
the file is read, never mapped, so one that shrinks or cannot be read fails like a changed one.
A leaf inside a hole (`SEEK_DATA`) is not read: its digest is that of zeros, what reading it
would give; past the file's end there is no hole, so a file that shrank is still read, and fails.
On a mismatch the launcher prints

```
fsbpyr: cannot start <path>: integrity check failed: the file was modified or damaged
```

and exits 70; damage to the launcher's own code can also crash it before it says so. Table
fields carry no checksum of their own: the launcher bounds-checks each record it uses and
otherwise relies on the digest, which only the writer's output passes
([Who checks what](Dev-Executable-Format.md#who-checks-what)). The cost is in
[Dev-Performance](Dev-Performance.md#the-integrity-check). The build's `write_temporary`
([`payload/writer.py`](../src/fsbpyr/payload/writer.py)) streams the payload into a new
`.{name}.XXXX.tmp` file beside the output (mode 0755, fsynced), reads the digest back from it
(`file_digest` in [`payload/format.py`](../src/fsbpyr/payload/format.py)), and `commit` renames
it into place.

### Checking an executable from outside

`fsbpyr inspect` shows the stored digest, which proves nothing by itself. `--verify`
(`read(path, verify=True)` in [`inspection.py`](../src/fsbpyr/inspection.py)) recomputes it from
the very file it opened, a leaf at a time (`Executable.verify_sha256` in
[`payload/reader.py`](../src/fsbpyr/payload/reader.py)), fails with `<path> is damaged or was
changed after its build`, and prints the plain SHA-256 a GitHub attestation names as `sha256`,
taken from the same read.
It refuses a path that is not a regular file (a FIFO would block the open), and prints
unprintable build information as its `repr`. Standard tools give the same digest:

```sh
head -c -32 app | split -b 1M --filter='openssl dgst -sha256 -binary' | sha256sum
split -b 1M --filter='openssl dgst -sha256 -binary' < cached-file | sha256sum
```

These checks catch damage, never a deliberate rewrite. Origin is checked outside the file:

- **Attestations.** `gh attestation verify` shows that this repository's release workflow built
  the file from its tag ([By attestation](User-Security.md#by-attestation)); `make release-check`
  ([`tools/release/check-release.sh`](../tools/release/check-release.sh)) checks a published
  release, attestations included, without rebuilding it; check (a) of
  [Verifying a release](../CONTRIBUTING.md#verifying-a-release) rebuilds from the tag and compares
  byte for byte ([Self-hosting](Dev-Self-Hosting.md#from-a-released-fsbpyr)).
  [`el8.yml`](../.github/workflows/el8.yml) attests the glibc 2.28 stubs, and
  [`attest-example.yml`](../.github/workflows/attest-example.yml) an app.
- **The installer** checks each download against the SHA-256 written into it, runs
  `fsbpyr inspect --verify` on the new file before installing it, and with `FSBPYR_VERIFY=1`
  runs `gh attestation verify` ([`install.sh.in`](../tools/release/install.sh.in);
  [The installer](User-Installing.md#the-installer)).
- **`fsbpyr self update`** ([`selfupdate`](Dev-Build-Modules.md#selfupdate)) checks the `.xz` of
  its own release asset against the release's signed `SHA256SUMS`, unpacks it in-process into a
  temporary file beside the executable, verifies it (`read(new, verify=True)`), checks its asset
  name and version, and refuses one that records a glibc newer than the host's
  (`check_glibc`: `the new fsbpyr needs glibc X, this host has Y; nothing was changed`); only then
  does it run the new file and rename it over the executable's real path. A developer build
  refuses to update. No attestation is checked.
- **The dispatch to another Python** (`variant` in [`dispatch.py`](../src/fsbpyr/dispatch.py))
  fetches that release's variant the same way, verifies it in-process, applies the glibc check,
  and keeps it in `downloads/` of fsbpyr's private cache, which `--offline` builds must find it
  in. The cached variant checks its whole file at every start.

### Code blobs are checksummed

The writer stores a module's code only in a checksummed zstd frame (its own, a block's, or zstd
pieces'), else fails with `code of ... must be checksummed` (`_check_module` in
[`payload/writer.py`](../src/fsbpyr/payload/writer.py)). The Python reader requires the same and
a content checksum in every zstd frame (`zstd_frame_has_checksum` in
[`payload/reader.py`](../src/fsbpyr/payload/reader.py)). The launcher's `fsb_zstd_frame_ok`
([`launcher/payload.c`](../src/fsbpyr/launcher/payload.c)) requires the frame magic, the
`Content_Checksum_flag`, and frame and content sizes that match the record before any zstd frame
is decoded, and `startup_table` (`launcher/main.c`) takes only zstd or block code for startup
modules. For other modules `marshal.loads` gets what `_fsbpyr.read` (`read_blob` in
`launcher/module.c`) returns, which checks every zstd frame but would also return a raw blob, so
there the guarantee rests on the writer and the whole-file digest.

### The hardened launcher

[`runtime/stub.py`](../src/fsbpyr/runtime/stub.py) compiles the launcher with `-fPIE -pie`,
`-fstack-protector-strong` and `-D_FORTIFY_SOURCE=2`, and links it with `-z noexecstack`,
`-z relro` and `-z now`; `-Wvla` and `-Wframe-larger-than=8192` keep the stack bounded, and
`build_stub` fails a link whose frames go over the limit. The stub exports only the Python C API
(`Py*`, `_Py*`), so wheel libraries cannot bind to its static zlib, OpenSSL or libffi. The flags
are hashed into the runtime id (`launcher_digest`), whose sysroot part is a digest of its pinned
packages' SHA-256 (`SysrootPin.identity` in [`runtime/pins.py`](../src/fsbpyr/runtime/pins.py)).
[`tests/e2e/test_stub_hardening.py`](../tests/e2e/test_stub_hardening.py) checks a built stub
with `readelf` and `objdump`. Every stub carries its id as `fsbpyr-stub-identity=<runtime id>`
([the recorded id](Dev-Launcher.md#how-it-is-compiled-and-linked);
[The glibc 2.28 stub](#the-glibc-228-stub)). The interpreter runs isolated, reading no `PYTHON*`
variable but `PYTHONUNBUFFERED` and importing no `site` (`main` in
[`launcher/main.c`](../src/fsbpyr/launcher/main.c)).

### Privileged starts are refused

After its file is checked, `main` in `launcher/main.c` exits 70 before Python starts when
`getauxval(AT_SECURE)` is set, with `refusing a privileged start (set-uid, set-gid or file
capabilities): the cache location comes from the caller's environment` (`FSBPYR_CACHE_DIR`,
`XDG_CACHE_HOME`, `HOME`); under set-gid or capabilities the invoking user would also own the
cache. The kernel sets it when a start changes privileges: a set-uid or set-gid file run by
another user or group than its own, or file capabilities; a set-uid file run by its owner starts
(`test_a_set_uid_start_by_the_owner_is_not_privileged` in
[`tests/e2e/test_runtime_app.py`](../tests/e2e/test_runtime_app.py)). Such a start, like process
1, starts no background collection (`fsb_collect_when_due` in
[`launcher/due.c`](../src/fsbpyr/launcher/due.c)), and nothing the boot code does (such as
`FSBPYR_INSTALL_LINKS`) runs in it.

### The cache

Native extensions, fs-package files and resources asked for as paths are extracted to the app's
cache ([Native code and the cache](Dev-Boot-Modules.md#native-code-and-the-cache); an app name
with a `/`, or `""`, `.` or `..`, is refused) and loaded from there as code, so `fsb_cache_sync`
([cache.c](Dev-Launcher.md#cachec-and-cacheh), [way.c](Dev-Launcher.md#wayc-and-wayh)) holds it
to these rules:

- **The way to the root** (`fsb_way_check`): every directory from `/` to the root must be owned by
  root or the user and writable by no other user unless sticky; symlinks must be root's or the
  user's; in a user namespace an owner outside it counts as root's only where no uid inside maps
  to the overflow uid, and only above a directory anyone may add to. A missing part is made, then
  the whole way checked again. `way_problem` in
  [`_fsbpyr_rt/gc.py`](../src/fsbpyr/boot/_fsbpyr_rt/gc.py), the same rule in Python for the
  collector and fsbpyr's own cache, is compared with it by a contract test.
- **Directories and files.** The root must be the user's (`open_root`, `owned`), directories below
  it the user's, never followed as symlinks, forced to 0700 (`open_private`). A cached file is
  used only as a regular file of the user's with the payload's size and digest (`load_cached`,
  `check_batch`), hashed once per process before first use; a symlink, FIFO or terminal device in
  its place is replaced, never followed, waited for or adopted, and a changed file is written
  again, with a note on stderr. A file is put in place only with the payload's digest
  (`place_unnamed`, `place_renamed`), so readers see a whole file or none, and is never writable
  (`fill`); one a crash left incomplete fails its digest and is written again.
- **Failures.** A refused root or directory (`refusing cache directory <path>: owned by another
  user`, or `... other users may write to it`) is refused before anything is written and ends the
  start: `NativeCache.extract`, through which every extraction goes (`natives` in
  [`_fsbpyr_rt/selftest.py`](../src/fsbpyr/boot/_fsbpyr_rt/selftest.py) too), hands it to
  `refuse`, which prints `fsbpyr: cannot start <executable>: refusing cache directory ...` and
  calls `os._exit(70)`, so neither an `except` in the app nor its thread carries on. Other
  extraction failures stay errors the app sees. A file the runtime extracts on its own without a
  digest, or a path that could leave its directory (`fsb_cache_safe`), is a `corrupt executable
  payload`.
- **Collection** removes only from a root with the marker `.fsbpyr-cache`, never set on a
  directory of the user's that `FSBPYR_CACHE_DIR` names, only what fsbpyr made there (`mark` in
  `_fsbpyr_rt/gc.py`, `markable` in `launcher/cache.c`), never following a symlink (`remove`), and
  is triggered only once the root's way is trusted (`trusted` in `launcher/due.c`).

[`tests/e2e/test_cache_root.py`](../tests/e2e/test_cache_root.py) runs each case of the way rule
with the release and the ASan/UBSan launcher, and checks the one-line refusal and exit 70 of an
app and of its native check; [`tests/unit/boot/test_way.py`](../tests/unit/boot/test_way.py)
checks the Python side.

### Multiprocessing children and helper interpreters

A child that multiprocessing, joblib or Jupyter starts as an interpreter starts the app
(`sys.executable`) with interpreter arguments, which `run` in
[`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py) looks at first
([Children through `sys.executable`](Dev-Boot-Modules.md#children-through-sysexecutable)):

- **multiprocessing** ([`_fsbpyr_rt/mp.py`](../src/fsbpyr/boot/_fsbpyr_rt/mp.py)):
  `child_command` matches the spawn, forkserver (per Python version) and resource-tracker command
  lines exactly and decodes their arguments with `ast.literal_eval`, never executing argv.
  Descriptors must be 3 or above; a forkserver command naming a `main_path` or a key an fsbpyr
  parent never sends is not recognized. `run_child` requires `_parent_runs_this_executable`: the
  parent's `/proc/<ppid>/exe` has the device and inode of `/proc/self/exe`, and its real and
  effective user and group ids (`/proc/<ppid>/status`) are this process's. Any doubt counts as
  no, and a recognized command from another parent ends with `refusing to run as a
  multiprocessing child: not started by this program`. A forkserver with several commands
  preloads the parent's command (`_parent_target`, from `/proc/<ppid>/cmdline`).
- **Helper interpreters** ([`_fsbpyr_rt/reexec.py`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py)):
  `-m MODULE`, `-c CODE` or a `.py` file below the executable; in an app of scripts also a `.py`
  path beside it, and for an fs-package one below the native cache's site, but only where a
  payload module says it lives (`_script_module`), so a host file is never run: the script's code
  comes from the payload, never from the file extracted there. Only a child that passes the same parent check counts: a shell, a script or a sudo
  rule gets the app, so a sudo rule for the app never hands out an interpreter. With
  `interpreter-reexec = true` the child runs the module, code or script (a script from the
  payload); with `false` it gets the app; unset, a child that `asks_for_python` exits 70 with the
  `refusal` message, as running the app would start it again and again. The exception, the script
  of one of the app's own commands (`_own_command`: `<exe>/_fsbpyr_scripts/x.py`, or the path its
  `__file__` names), runs that command from the payload, never a file on the disk: no more than
  starting the app under that command's name.
- **Commands named by a link** ([`_fsbpyr_rt/commands.py`](../src/fsbpyr/boot/_fsbpyr_rt/commands.py)):
  a start whose `argv[0]` names a command under another basename than the executable's
  (`is_sibling`, a symlink) runs that command and is no helper interpreter; a hard link, its own
  `sys.executable`, keeps the check. In `exe COMMAND -m word` only the word after the flags,
  `COMMAND`, is looked at, which runs with `-m word` as its arguments.
- **The restart idiom** ([`_fsbpyr_rt/restart.py`](../src/fsbpyr/boot/_fsbpyr_rt/restart.py)):
  `[sys.executable, <interpreter flags>, sys.argv[0], *args]` loses the flags and the argument
  when that is this very executable (device and inode of `/proc/self/exe`; a link counts) after
  interpreter flags only. It needs no parent check: dropping an argument that names the executable
  gives nothing `app args` does not, and nothing is imported, run or opened from the arguments.

[`tests/unit/boot/test_mp.py`](../tests/unit/boot/test_mp.py) and
[`tests/unit/boot/test_reexec.py`](../tests/unit/boot/test_reexec.py) cover the first two.

### Scripts and the files beside the executable

In an app of scripts a module of `_fsbpyr_scripts/` has its `__file__` beside the executable
(`_shown` in [`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py);
[The scripts root](Dev-Boot-Modules.md#the-scripts-root)), so the config, state and logs a script
reads next to it are the host's. Its code is the payload's: `path_hook` claims, through
`_overlay_finder`, the executable's directory and those of its subdirectories that are
directories of the scripts root, so a `.py` file there never replaces the payload's module, and
looks at nothing else there. A `..` in a `sys.path` entry is resolved inside the executable, and
an entry that climbs above it is not served from the payload. Those files are the app's input, so
the executable's directory should be writable by its owner only (D24).

### Linking commands on the target

`FSBPYR_INSTALL_LINKS=DIR app` links the commands into DIR and exits (`_install_links` in
`_fsbpyr_boot.py`, `install` in [`_fsbpyr_rt/links.py`](../src/fsbpyr/boot/_fsbpyr_rt/links.py)):
an absolute symlink to the executable's real path per command, and `x.py` besides `x` for a
script command. A name that is not a plain file name is refused; a link that already names this
executable is left alone; anything else in the way is reported and kept, the other links are
made, and the exit status is 1; a missing or unwritable DIR is one error before any link. There
is no argument form: under a `sudo <binary> *` rule one would let any caller create root-owned
links anywhere, while sudo's `env_reset` (a default) drops the variable unless `env_keep` or
`SETENV` allows it. The background collection child, which inherits the environment, does not
link.

### Service policies

[`_fsbpyr_rt/service.py`](../src/fsbpyr/boot/_fsbpyr_rt/service.py) is imported only when
`JOURNAL_STREAM` names the file of stdout or stderr (`posix.fstat` on descriptors 1 and 2) or the
app is process 1 (`_service_policies` in `_fsbpyr_boot.py`).
`JOURNAL_STREAM` (`<device>:<inode>`, decimal) only line-buffers stdout or stderr when `fstat`
shows it is that file and not unbuffered already; a malformed value or another file does nothing.
As process 1, a handler raising `SystemExit(143)` is installed only while SIGTERM has its default
disposition, the app's own handler replaces it, and a forked child gets the default back
(`os.register_at_fork`). Neither reads a path or runs anything.

### The instance limit

`fsb_hold_slot` ([slots.c](Dev-Launcher.md#slotsc-and-slotsh)) locks one byte of
`fsbpyr-<euid>` in `/dev/shm`, else in `$XDG_RUNTIME_DIR`, in a range of the executable's device
and inode, shared by its links and hard links; a start that finds every slot taken exits 70. The
file must be a regular file of the user's with no group or other permission bits, opened with
`O_NOFOLLOW` and `O_NONBLOCK`, never as descriptor 0, 1 or 2. With no file to trust, or no OFD
locks, the start runs unguarded: a file someone else made first in `/dev/shm` can cost the user
the guard, never a start. [`tests/e2e/test_slots.py`](../tests/e2e/test_slots.py) probes it. It
is no security boundary ([Known limits](#known-limits)).

### fsbpyr's own cache and the build inputs

The build tool's own cache, `$FSBPYR_CACHE_DIR`, else `${XDG_CACHE_HOME:-<home>/.cache}/fsbpyr`
with `<home>` as for an app's (`cache_root` and `home_directory` in
[`runtime/registry.py`](../src/fsbpyr/runtime/registry.py)), holds runtimes, python-build-standalone
archives, sysroots and downloads. Cached stubs end up in every executable built from them, so
`secure_directory` creates the directory 0700 or requires it to be the user's (forcing 0700),
refuses a symlink there that is not root's or the user's, and checks the way from `/` with
`gc.way_problem` before and after; [2] covers the runtimes. From outside the build takes:

- **python-build-standalone**: a pinned URL and SHA-256 per Python and target
  ([`runtime/pins.py`](../src/fsbpyr/runtime/pins.py)); `download` refuses any URL that is not
  `https://` and checks the SHA-256 before the archive is moved into place, and `fetch` checks a
  cached archive again before extracting it with tar's `data` filter
  ([`runtime/pbs.py`](../src/fsbpyr/runtime/pbs.py)). A distribution already extracted in the
  private cache is reused as it is.
- **The glibc sysroot**: pinned packages, Debian 12's `.deb` files by default (`SYSROOTS`),
  Rocky Linux 8.10's RPMs for glibc 2.28 (`EL8_SYSROOTS`, with the SHA-256 its repository lists
  in `repodata/primary.xml`), each downloaded with the HTTPS-only `download`, read once and
  checked against its SHA-256, so what is verified is what gets extracted (`_package` in
  [`runtime/sysroot.py`](../src/fsbpyr/runtime/sysroot.py)). An RPM is read without rpm, its
  signatures unread since the pinned SHA-256 is what is verified, and refused unless it is a
  well-formed xz cpio payload of regular files, directories and symlinks that stay inside the
  package, with complete sets of hard links ([`rpm`](Dev-Runtime-Modules.md#rpm)); the tar it
  becomes is extracted like a Debian package's, absolute symlinks made relative to the sysroot,
  then tar's `data` filter (`_into_sysroot`).
- **The glibc 2.28 stub** of a release fsbpyr: [below](#the-glibc-228-stub).
- **clang and lld 23** for a runtime in `make bootstrap`: LLVM's release tarball pinned by
  SHA-256 in [`tools/release/llvm23`](../tools/release/llvm23), checked by `llvm23` in
  [`tools/bootstrap.sh`](../tools/bootstrap.sh) before unpacking (`FSBPYR_LLVM23=0` takes a
  clang/lld 23 or newer from `PATH`).
- **uv**: whatever is on `PATH`; its version is recorded (`uv` in `fsbpyr inspect`), since
  another uv can give other bytes. uv builds the project's wheel out of tree when the project
  has a build system, and installs the dependencies from the lock
  ([`staging.py`](../src/fsbpyr/staging.py)).
- **The package index**: what the lock names is what the executable runs; the app's code and
  dependencies are out of scope.

### The glibc 2.28 stub

A stub is the launcher of every app built with it. Only a stub a build is handed, the glibc 2.28
ones today, has its recorded runtime id compared; other builds take their runtime's stub, which
[2] checks, or the running fsbpyr's own. For `--glibc 2.28` a release fsbpyr, which carries the
default runtime only, pairs the standard library of its embedded runtime, read after the
whole-file check of `Runtime.from_executable`, with its release's glibc 2.28 stub
(`select_runtime` in [`runtime/registry.py`](../src/fsbpyr/runtime/registry.py), `paired` in
[`runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py)). A developer build (no release tag in its
version, `own_tag`) uses a cached runtime from `fsbpyr runtime build --glibc 2.28`, checked like
any other ([2]).

[`vcs.py`](../src/fsbpyr/vcs.py) names the asset `fsbpyr-stub-<python>-<target>-glibc2.28`
(`release_stub_asset`), the companion release `el8-vX.Y.Z` of the tag `vX.Y.Z`
(`release_stub_tag`), and its `SHA256SUMS-glibc2.28` (`release_stub_sums`) of the unpacked stubs.
`stub_for` takes the first of:

1. **A file placed beside the fsbpyr executable** (for machines that cannot reach GitHub), named
   like the asset, plain or `.xz`, in the directory of the path fsbpyr was run by (looked up on
   `PATH` when run by name, links kept) or of its real path (`own_paths`, `_placed`). For a build
   another fsbpyr handed over for another Python (`run` in
   [`dispatch.py`](../src/fsbpyr/dispatch.py)), that fsbpyr's directories, the absolute entries of
   `FSBPYR_DISPATCHED_BY`, come first and are named in messages; the variable only adds places to
   look. An `.xz` file is unpacked in memory; either is read up to 256 MB, a larger one is no stub.
2. **The stub kept in fsbpyr's cache**, `downloads/<asset>-<runtime id>` in the directory
   `secure_directory` checks, named by the id it must carry so an update that keeps the id reuses
   it; one without the expected id is deleted and fetched again.
3. **A download** from `<base>/el8-vX.Y.Z/`, `<base>` being `FSBPYR_RELEASE_DOWNLOADS` or
   `https://github.com/devitops-com/fsbpyr/releases/download`. `_download` checks the signature of
   `SHA256SUMS-glibc2.28` against the pinned keys before using a line, `<asset>.xz` against its
   line before lzma sees it, and the unpacked stub (in a temporary file in `downloads/`, 256 MB at
   most) against the listed SHA-256, keeping nothing on a mismatch; a stub that passes is written
   to `downloads/<asset>-<runtime id>` whole or not at all (`_keep`, a rename). With `--offline`
   nothing is downloaded, and without a placed or cached stub the build stops with `--offline:
   the <asset> is not placed or cached, and nothing is downloaded; place <asset> (or <asset>.xz)
   next to <fsbpyr>, or build once without --offline to cache it` (`_offline`), `<fsbpyr>` being
   the first of the paths above.

Each must carry the runtime id this fsbpyr computes for its launcher sources and flags, pinned
Python, the EL8 sysroot and the layout version (`runtime_id`), which `_checked` compares with
`stub_identity`; any other (another Python or target, the default baseline, other sources or
pins, no id) stops the build with `<where> is not the stub this fsbpyr (vX.Y.Z) needs: ...; use
the <asset> of release el8-vX.Y.Z`. A companion release that is missing, or lists no stub for
this Python and target, stops it with `release el8-vX.Y.Z (the glibc 2.28 stubs of vX.Y.Z) does
not exist or has none for this Python: ...`, naming the file to place instead.

What these checks are worth: the SHA-256 catches a damaged or swapped download, and the sums'
signature by a pinned key (`signed_sums`) a release whose stub and sums were both replaced;
[`.github/workflows/el8.yml`](../.github/workflows/el8.yml) attests the stubs and leaves them in a
draft of the companion release, which `make release-sign` signs and publishes; fsbpyr does not
check those attestations. The runtime id tells a stub of other launcher sources, another Python
or target, or the default sysroot from the right one, but it is no digest ([Known limits](#known-limits)): D20 keeps the cache to the user,
and a placed file should be checked against the companion's `SHA256SUMS-glibc2.28` (sums of the
unpacked stub) first. The id names what the stub is linked from, not the release: releases with
the same launcher sources and flags, Python pin, EL8 sysroot pins and layout version accept each
other's stubs, and a change to any of them gives every runtime a new id.

### The project on the build host

A project with a build system runs code on the build host ([out of scope](#out-of-scope)). A
project with a `[project]` table and no `[build-system]`, a directory of scripts and a single
script are built so that none of their scripts runs there:

- **No backend.** `_uv_build` returns no command for them; `loose.py` writes the wheel itself
  with the standard library, holding only the project's Python files (`*.py`, and extensionless
  files with a python or `uv run` shebang) under `_fsbpyr_scripts/`. In a git work tree these are
  the files `git ls-files` lists as tracked (`work_tree_files` in [`vcs.py`](../src/fsbpyr/vcs.py));
  an untracked, unignored Python file is left out with a warning naming it, so a `secrets.py`
  nobody committed is never in the executable. A link to a `.py` file outside the project fails
  the build. A single script takes only itself and its static local imports.
- **Data, not code.** Requirements files, `pylock.toml`, Poetry's and PDM's locks
  ([`foreign.py`](../src/fsbpyr/foreign.py), [`poetry1.py`](../src/fsbpyr/poetry1.py)),
  `Pipfile.lock` ([`pipenv.py`](../src/fsbpyr/pipenv.py)) and PEP 723 blocks are parsed as TOML,
  JSON or text. When nothing declares a dependency, [`venvdeps.py`](../src/fsbpyr/venvdeps.py)
  reads the `*.dist-info` files (`METADATA`, `top_level.txt`, `RECORD`, `direct_url.json`) of the
  scripts' venv, never starting its Python or copying from it, into `name==version` pins that uv
  installs fresh.
- **Static checks.** The unresolved-import check ([`importcheck.py`](../src/fsbpyr/importcheck.py))
  follows imports with `ast` alone, and the compiler's scan finds the reads next to `__file__` the
  build warns about.
- **The smoke test finds a script without importing it** (`_check_entrypoint` in
  `_fsbpyr_boot.py`). A `module:function` command from `[project.scripts]` is imported to look
  the function up (`_resolve`), so its top-level code runs on the build host, as in any project.

uv still installs the dependencies, and one without a wheel is built from its sdist, running its
build code; dependencies are out of scope.

## Known limits

- **A rewritten digest.** Whoever can change the file can change its digest too, or patch the
  check out, since the checking code is in the same file. Only
  [checks from outside](#checking-an-executable-from-outside) catch that: the release's
  provenance and SBOM attestations, the installer (`FSBPYR_VERIFY=1` adds the attestation), a
  rebuild from the tag compared byte for byte, and a signature checked with a key from outside the
  file ([Signatures](#signatures)).
- **glibc 2.28 stubs** (D46). A downloaded stub is checked against the sums of its companion
  release, whose signature by a pinned key is checked first, so a release whose stubs and sums
  were both replaced is refused too. A stub placed beside the fsbpyr executable, or kept in
  `downloads/` of its cache, is accepted on the runtime id it embeds, which anyone who can write
  the file can write: whoever can write either directory can put code into every EL8 app built
  there.
- **Self update** checks no trust with its glibc check, which only guards against a broken
  install. It leaves the glibc 2.28 stubs placed beside the executable (where `--glibc` looks)
  and warns of those the new release's companion `SHA256SUMS-glibc2.28` does not list, or, when
  it cannot read that file (no companion yet, no network, a download cut short), of all, saying
  `--glibc` refuses them if the runtime id changed (`_warn_about_placed_stubs` in
  [`selfupdate.py`](../src/fsbpyr/selfupdate.py), `refused_after_update` in
  [`runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py)). A build takes them only if the new
  release computes the old id.
- **Files beside the executable are input.** In an app of scripts, whoever can write the
  executable's directory chooses the config and data its scripts read there (not their code).
- **POSIX ACLs and shared primary groups.** The cache root check reads the mode bits and the
  owner. A POSIX ACL on the cache root, or a primary group shared with other users, can give
  others write access the check does not see. Keep the cache root in a directory only you can
  reach.
- **`/proc`.** An executable finds itself through `/proc/self/exe`, and the parent check reads
  `/proc/<ppid>`. Without `/proc` (or with one that is not procfs) it does not start, or the
  parent check says no.
- **Privileged uses are refused.** `setcap cap_net_bind_service+ep app` and set-gid deployments
  stop working (a set-uid file run by its owner starts). A fixed root-owned cache ignoring
  `FSBPYR_*`, XDG and `HOME` would allow them, but was not chosen: a second cache policy.
- **The instance limit is no security boundary.** It stops an app that keeps starting itself, not
  an attacker: any process of the user can hold every slot (out of scope above), and code that
  closes its descriptors, as daemonizing code does, gives its slot up.
- **CVEs in the embedded runtime.** CPython and the libraries linked into it are fixed only by
  rebuilding with a newer fsbpyr (SECURITY.md, "Fixes to the runtime").

## See also

- [Dev-Launcher](Dev-Launcher.md): the start sequence these checks are part of.
- [Dev-Executable-Format](Dev-Executable-Format.md): the trailer, the digest and the validation
  rules.
- [Dev-Compatibility](Dev-Compatibility.md): what the cache, the refusals and the files beside the
  executable mean for apps.
- [Dev-Performance](Dev-Performance.md): what the checks cost.
- [User-Security](User-Security.md): all of this from a user's side, with every message.

## Signatures

[`docs/signing.md`](../docs/signing.md) is the design, with what each choice rests on;
[Signing your own apps](User-Security.md#signing-your-own-apps) says how to use them. Ed25519 and
ML-DSA-65, always both, sign a short statement holding a file's SHA-256 and its project.

![The chain of trust of a fsbpyr release: GitHub builds and attests the tagged commit and leaves an unsigned draft; on the maintainer's machine the audit runs, the commit and jobs are checked, and the release key signs SHA256SUMS; fsbpyr self update checks the sums against its built-in keys, then the .xz and the asset, and renames it into place. A stolen GitHub account gets a draft nobody signs.](images/security-chain.svg)

No key is on GitHub: CI builds and attests, and stops at a draft. `make release-sign` signs on the
admin's machine, only if the commit is the same everywhere and every job of the attested run ran
on GitHub's machines; `make release-audit` finds a tampered account or repository first
([The repository](Dev-Repository.md#release-keys)).

The crypto is in [`launcher/sig.c`](../src/fsbpyr/launcher/sig.c), on the OpenSSL every fsbpyr
carries, the rest in [`signing.py`](../src/fsbpyr/signing.py). Each call makes its own library
context and loads no configuration into it, so openssl.cnf changes no check's result, and leaves
the thread's error queue as it found it, which CPython's `_ssl` reads. A public key file is taken
only as the exact bytes OpenSSL writes for its two keys, and a key file only as two encrypted
blocks with nothing around them; OpenSSL's readers alone take far more.

A name read from a file is not trusted, since a forged executable can name anything and pass its
D31 digest: a name from build info is checked before it is used or printed, never normalized,
and only a checked name is joined into a path.

| Threat | Result |
|---|---|
| A file changed after signing | Stopped: its SHA-256 is signed |
| A signature for project A presented as project B's | Stopped: the project is in the statement |
| A genuine file of another trusted project, served under this app's name | Stopped with `verify --project`; without it, `verify` passes and prints the other project's name |
| Another file of the same release, or an older release's file with its own sums file, served under an asset's name | Not stopped by `verify` through a sums file, whose line says what the file is listed as. `self update` checks the asset and the version |
| A forged executable whose build info names a path or a terminal escape | Stopped: refused unprinted, never joined into a path |
| A file of fsbpyr whose build info names a path as its tag, or the tag of another release | Stopped: only the numbers of a vX.Y.Z tag go into a URL, and a forged tag gets a signed release that does not list the file |
| A bad `.sig` or sums file laid beside a file, or named to `verify`, so that a better one is used instead | Stopped: what lies there or was named is the only way tried. `verify` falls back on nothing, and a download happens only when there is nothing |
| A tampered fsbpyr that prints VERIFIED about itself | Not stopped: no check inside a file can be. `verify` says so whenever it checks a file of fsbpyr against files it downloaded, and [the outside checks](User-Security.md#how-far-each-check-goes) find it |
| A relative `XDG_CONFIG_HOME` that brings trusted keys in a download | Stopped: ignored, as a relative `HOME` is; a trust store another user can write is refused |
| Ed25519 broken, or a flaw in the ML-DSA-65 code | Stopped: both must pass |
| The first fsbpyr download tampered with | Not stopped: it is trusted as GitHub serves it, a hacked repository's included. The audit limits how long that lasts |
| A malicious mirror or TLS interception, for updates | Stopped: the sums are checked against the pins, and each `.xz` against its line, before lzma sees it |
| Updates withheld | Not stopped, but the audit finds a latest release that is not the newest |
| Rollback of apps | Not stopped |
| Rollback of fsbpyr through an update | Stopped: the version must be newer |
| A stolen GitHub account: a backdoored commit or workflow on a pushed tag, or a release made by hand | Stopped: no key is on GitHub, so CI leaves an unsigned draft, `make release-sign` refuses an attestation that does not name your commit, every signed fsbpyr refuses a release published by hand, and the audit reports it. Played on GitHub |
| A change pushed to main that the admin then pulls and tags | Not stopped: what you tag, K signs. It is the admin's to know, and out of scope |
| GitHub itself compromised, so that it attests what it did not build | Not stopped: out of scope |
| A poisoned build | Gets a signed release, since what the commit builds is what the admin signs. K is never near built code, and the `draft` job writes the sums again, so the built code in `prepare` cannot get digests of unpublished files listed |
| A key the tag does not pin | Stopped: `make release-sign` checks against the pins of this release and of the previous published one before it uploads anything |
| Stolen K | Partly: it takes the admin's key medium and passphrase, not the GitHub account. R signs the key change, and the audit reports a release that is not on record; clients that do not update stay exposed |
| The passphrase logged by code that runs during `build` | Stopped: the build's processes lack both variables in their environments |
| The passphrase and the key file taken by code that runs during `build` and looks for them | Not stopped: sign in a separate job, on a machine or account the build never ran on |
| fsbpyr's cache, or stubs placed beside fsbpyr, changed by someone who can write them | Not stopped: they can replace fsbpyr itself. A reinstall does not clean them, nor the trust store: see [Checking an installed fsbpyr](User-Security.md#checking-an-installed-fsbpyr) |
| `OPENSSL_CONF`, `OPENSSL_MODULES` or `LD_PRELOAD` set by an attacker | Not stopped: they load code. A config that loads no code changes no check's result, but can choose the randomness of `key new` and `sign` |
