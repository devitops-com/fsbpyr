# Security

What fsbpyr protects, from whom, and where that stops; where each defence sits in the code,
from the developer's build machine to the target machine; the known limits; and how to report a
vulnerability. The threat model is
[docs/security.md](../docs/security.md) at
v0.27.0, the reporting policy
[SECURITY.md](../SECURITY.md), and the user's
side of the same checks is in the User Guide:
[Integrity](User-Running.md#integrity) and [Refusals](User-Running.md#refusals-exit-70) on Running,
[`--verify`](User-Inspecting.md#--verify) on Inspecting, and
[Verifying a download](User-Installing.md#verifying-a-download) on Installing.

## Reporting a vulnerability

Report it privately, as a
[GitHub security advisory](https://github.com/devitops-com/fsbpyr/security/advisories/new),
not as a public issue; [SECURITY.md](../SECURITY.md)
has the details. Reports are acknowledged within 7 days. Say which fsbpyr is affected
(`fsbpyr --version`) and how to reproduce the problem; for a built executable, the output of
`fsbpyr inspect --json` on it helps too. Only the latest release is supported: fixes come in a
new release, and older releases are not patched.

SECURITY.md also explains the one thing a developer most often misses: every executable,
fsbpyr's own included, carries its own runtime, the CPython of the fsbpyr that built it from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone), linked
statically into the launcher with the libraries python-build-standalone links into CPython's
built-in modules (OpenSSL, SQLite, expat, zlib, libffi, liblzma, mpdecimal, bzip2, zstd,
ncurses, libedit and libuuid). The target's own copies are never used, so a fix in any of them
reaches an executable only when it is rebuilt with an fsbpyr that carries the fixed runtime.
`fsbpyr inspect` names the runtime (`build_info.runtime` in `--json`) and, for builds that
record them, the versions of those libraries (`components`, from `component_versions` in
[`runtime/pbs.py`](../src/fsbpyr/runtime/pbs.py)).
The app's own dependencies, and the libraries their wheels bundle, come from the app's lock
file; updating them is up to the app and takes a rebuild as well. SECURITY.md states how soon
a release with a fixed runtime follows a python-build-standalone release.

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
- An attacker who rewrites the executable and its digest together (see
  [Known limits](#known-limits)).
- The app's own code and dependencies: an executable runs what it was built from.
- **Code the build host runs for a project that has a build system.** `uv build` runs the
  project's build backend, sdists of dependencies are built, and the smoke test imports the
  entry module (`--entry`, an inferred module, a `module:function` command), so a packaged
  app's top-level code runs on the build host with the builder's rights. Build only projects
  you trust. A project without a build system, a directory of scripts and a single script are
  built without running their scripts: see [The project on the build host](#the-project-on-the-build-host).

## Trust boundaries

![The trust boundaries of fsbpyr: external inputs (package index, uv, python-build-standalone, the pinned sysroot packages, the companion release with the glibc 2.28 stubs) and the project tree feed the developer's build machine, where fsbpyr runtime build and fsbpyr build make an executable with a digest in its trailer, a glibc 2.28 stub placed beside fsbpyr or kept in its cache being checked on its runtime id; the executable crosses a transit zone to the target machine, where the launcher checks it at every start, the boot code checks the per-user cache and callers' arguments, reads the files beside the executable as data only and makes only symlinks into a link directory, and other local users are kept out of the cache and the slot file. Numbered labels mark where each check happens.](images/security-boundaries.svg)

Four zones. The **external inputs** are what the build takes from outside: the package index
(wheels and sdists, installed by uv), uv itself, the python-build-standalone archive the
runtime is linked from, the pinned packages of the glibc sysroot the launcher is linked
against, and, for an app built with `--glibc 2.28` by a release fsbpyr, the stub of its
companion release `el8-vX.Y.Z`. The **build machine** holds the project tree and turns it and the
inputs into an executable whose last 32 bytes are the digest of everything before them.
**In transit** the file is copied or downloaded; nothing in it can prove where it came from. On
the **target machine** the file, the per-user cache that native code is extracted to, the slot
file of the instance limit, the files deployed beside the executable and a directory the
executable is asked to link its commands into are what other local users and callers can
reach.

### Where each check happens

| | Check | Code |
|---|---|---|
| [1] | The python-build-standalone archive is checked against the SHA-256 pinned per Python and target, and each package of the glibc sysroot (Debian 12's, or Rocky Linux 8.10's for glibc 2.28) against the SHA-256 pinned per target and baseline, before it is extracted (with tar's `data` filter) | `download` and `fetch` in [`runtime/pbs.py`](../src/fsbpyr/runtime/pbs.py), `_package` in [`runtime/sysroot.py`](../src/fsbpyr/runtime/sysroot.py), pins in [`runtime/pins.py`](../src/fsbpyr/runtime/pins.py) |
| [2] | A built runtime records the digests of its stub and standard library in `runtime.json`; loading it checks them. A self-hosted fsbpyr verifies its own whole file before it reuses its embedded runtime | `Runtime.save`, `Runtime.load` and `Runtime.from_executable` in [`runtime/registry.py`](../src/fsbpyr/runtime/registry.py) |
| [3] | Dependencies are installed by uv from the lock: `uv sync --locked` for `uv.lock` and for a script's `<script>.lock`; a Poetry, PDM or Pipenv lock is converted into a requirements file in the staging work directory with every hash it records, and uv requires the hashes when every line has one; `--require-hashes` for a requirements file that carries hashes, or any requirements file under `--strict` | `staging_plan` in [`staging.py`](../src/fsbpyr/staging.py), [`foreign.py`](../src/fsbpyr/foreign.py), [`pipenv.py`](../src/fsbpyr/pipenv.py) |
| [4] | The build ends the file with the digest of everything before it, read back from the written file | `assemble` and `write_temporary` in [`payload/writer.py`](../src/fsbpyr/payload/writer.py) |
| [5] | Outside checks: `gh attestation verify`; the installer's SHA-256 of each download; `fsbpyr inspect --verify`; `fsbpyr self update` and the dispatch to another Python verify a download in-process, and refuse one that needs a newer glibc than the host has, before anything runs it | [`inspection.py`](../src/fsbpyr/inspection.py), [`selfupdate.py`](../src/fsbpyr/selfupdate.py), [`dispatch.py`](../src/fsbpyr/dispatch.py) |
| [6] | Every start hashes the whole file before it believes any byte of it; a start that changes privileges (set-uid, set-gid, file capabilities) is refused | [`launcher/integrity.c`](../src/fsbpyr/launcher/integrity.c), `fsb_payload_open` in [`launcher/payload.c`](../src/fsbpyr/launcher/payload.c), `main` in [`launcher/main.c`](../src/fsbpyr/launcher/main.c) |
| [7] | The cache root's way from `/` must be trusted, its directories the user's and private, and each extracted file's digest is checked before the process first uses it | [`launcher/way.c`](../src/fsbpyr/launcher/way.c), [`launcher/cache.c`](../src/fsbpyr/launcher/cache.c) |
| [8] | The instance limit uses only a slot file that is a regular file of the user's that nobody else may use | [`launcher/slots.c`](../src/fsbpyr/launcher/slots.c) |
| [9] | Arguments that ask for a multiprocessing child or a helper interpreter run as Python only in a child of this same executable, with the same real and effective ids; without `--interpreter-reexec` only a script that is one of the app's own commands runs, and from the payload | [`_fsbpyr_rt/mp.py`](../src/fsbpyr/boot/_fsbpyr_rt/mp.py), [`_fsbpyr_rt/reexec.py`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py) |
| [10] | A project without a build system, a directory of scripts and a single script: fsbpyr writes the wheel itself from the tracked Python files, runs no build backend and no script; locks, requirements files, PEP 723 blocks and a venv's metadata are read as data; the smoke test finds a script without importing it | [`loose.py`](../src/fsbpyr/loose.py), `_uv_build` in [`staging.py`](../src/fsbpyr/staging.py), [`venvdeps.py`](../src/fsbpyr/venvdeps.py), [`importcheck.py`](../src/fsbpyr/importcheck.py), `_check_entrypoint` in [`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py) |
| [11] | In an app of scripts, the files beside the executable are data: the path hook claims those directories, so every module comes from the payload | `path_hook` and `_overlay_finder` in [`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py) |
| [12] | `FSBPYR_INSTALL_LINKS` makes only symlinks to the executable's real path, with plain file names, and replaces nothing | `install` in [`_fsbpyr_rt/links.py`](../src/fsbpyr/boot/_fsbpyr_rt/links.py) |
| [13] | A release fsbpyr building for glibc 2.28: a stub downloaded from the companion release `el8-vX.Y.Z` must match the SHA-256 its `SHA256SUMS-glibc2.28` lists; that stub, one placed beside the fsbpyr executable and one kept in its cache must all carry the runtime id this fsbpyr computes | `stub_for`, `_download` and `_checked` in [`runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py), `stub_identity` in [`runtime/stub.py`](../src/fsbpyr/runtime/stub.py) |

## What stops what

The defences of docs/security.md, with the decision of the
[implementation plan](../docs/IMPLEMENTATION_PLAN.md)
that introduced each (code comments cite them by these numbers) and the code that implements
it.

| Decision | Stops | Code |
|---|---|---|
| D17 | Fixed addresses: the launcher and runtime are a position-independent executable, hardened with RELRO, BIND_NOW, a non-executable stack and stack protector | `COMPILE_FLAGS`, `LINK_FLAGS` in [`runtime/stub.py`](../src/fsbpyr/runtime/stub.py) |
| D19 | Corrupt code objects: every code blob is a checksummed zstd frame, and readers require the checksum, so `marshal.loads` never sees damaged bytes (at run time, for modules other than the startup ones, together with the whole-file digest: see [Code blobs are checksummed](#code-blobs-are-checksummed)) | `fsb_zstd_frame_ok` in [`launcher/payload.c`](../src/fsbpyr/launcher/payload.c); [`payload/writer.py`](../src/fsbpyr/payload/writer.py) and [`payload/reader.py`](../src/fsbpyr/payload/reader.py) |
| D20 | Another user planting a runtime or stub: cache directories must be the user's own and are forced to 0700; the cached runtime's digests are verified on load. A glibc 2.28 stub kept in `downloads/` is protected by the directory's owner and mode and by the runtime id it embeds, not by a digest (see [Known limits](#known-limits)) | `secure_directory`, `Runtime.load` in [`runtime/registry.py`](../src/fsbpyr/runtime/registry.py), `stub_for` in [`runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py) |
| D22 | A caller pretending to be multiprocessing: the three child command lines are matched exactly and decoded with `ast.literal_eval`, never executed, and run only if the parent is this same executable (device and inode of `/proc/<ppid>/exe`) with the same real and effective user and group ids | [`_fsbpyr_rt/mp.py`](../src/fsbpyr/boot/_fsbpyr_rt/mp.py) |
| D23, D44 | The same for helper interpreters (`-m`, `-c`, a `.py` file of the app), which run only with `--interpreter-reexec`; without the setting the app's own children asking for one exit 70, and any other caller gets the app. The one start allowed without the setting is a child of the app whose `.py` file is the script of one of the app's own commands (`[sys.executable, <exe>/_fsbpyr_scripts/x.py, args]`, or the path that script's `__file__` names beside the executable): it runs that command from the payload, never a file on the disk, and only after the D22 parent check, so it grants no more than the app does. A start named for one of the commands under another name than the executable's (a link) runs that command and is no helper interpreter; a hard link is not that and keeps the refusal. The dispatcher form `exe B -m word` asks for no interpreter at all: the word after the executable is the command `B`, which runs with `-m word` as its arguments | [`_fsbpyr_rt/reexec.py`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py), `is_sibling` in [`_fsbpyr_rt/commands.py`](../src/fsbpyr/boot/_fsbpyr_rt/commands.py) |
| D45 | Another user denying a user's starts through the instance limit: the slot file must be a regular file of the user's that nobody else may use, else it is passed over for the one in `$XDG_RUNTIME_DIR`, and with no file to trust the start runs unguarded. A file made first by someone else in `/dev/shm` can cost the user the guard, never a start | `fsb_hold_slot` in [`launcher/slots.c`](../src/fsbpyr/launcher/slots.c) |
| D31 | A changed executable: the launcher checks the whole file against the digest in its trailer before it reads anything else | `fsb_check_integrity` in [`launcher/integrity.c`](../src/fsbpyr/launcher/integrity.c) |
| D41 | A privileged start: when the kernel sets `AT_SECURE` (set-uid or set-gid to another user or group, or file capabilities; a set-uid file run by its owner changes no privileges and starts) the executable exits 70, because the cache location would come from the caller's environment and, under set-gid or capabilities, the invoking user would own the cache | `main` in [`launcher/main.c`](../src/fsbpyr/launcher/main.c) |
| D14 (`FSBPYR_INSTALL_LINKS`) | A caller using the links feature to create files as another user: it is an environment variable only, with no `--install` argument, because under a `<binary> *` sudo rule an argument would let any caller create root-owned links anywhere, and sudo's `env_reset` (a default) drops `FSBPYR_*` unless `env_keep` or `SETENV` allows it. It makes symlinks to the executable's real path only (`name`, and `name.py` for a script command), never replaces a file, a directory or a link to anything else (reported, exit 1), refuses names that are not plain file names, and cannot run in a privileged start (D41 exits first) | `install` in [`_fsbpyr_rt/links.py`](../src/fsbpyr/boot/_fsbpyr_rt/links.py), `_install_links` in [`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py) |
| D24 (the overlay) | A file planted beside the executable running as code: in an app of scripts `__file__` is `dirname(executable)/<path>`, so that config, state and logs deployed beside the binary are read by `open(dirname(__file__) + ...)`; the modules always come from the payload (the path hook claims those directories, so a `.py` copy on the disk never shadows one). Data beside the executable is read as the app's input, so keep that directory writable by its owner only | `_shown`, `_overlay_finder` and `path_hook` in [`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py) |
| D5 | Project code on the build host, for a project with no build system, a directory of scripts and a single script: no build backend runs and no script is imported (the smoke test only finds a script module). In a git work tree the wheel is written by fsbpyr from the Python files that `git ls-files` lists as tracked, so an untracked `secrets.py` is never in it; a link to a `.py` file outside the project directory is an error. A venv's `*.dist-info`, `Pipfile.lock`, `poetry.lock`, `pdm.lock`, requirements files and PEP 723 blocks are read as data, never executed, and the unresolved-import check follows imports with `ast` alone | [`loose.py`](../src/fsbpyr/loose.py), `work_tree_files` in [`vcs.py`](../src/fsbpyr/vcs.py), [`venvdeps.py`](../src/fsbpyr/venvdeps.py), [`foreign.py`](../src/fsbpyr/foreign.py), [`pipenv.py`](../src/fsbpyr/pipenv.py), [`importcheck.py`](../src/fsbpyr/importcheck.py) |
| D46 | A stub linked from other launcher sources, for another Python or target, or against another sysroot going into an app. Every stub, the default one included, records the runtime id it was linked as; a default build takes the stub of its runtime, which [2] checks. A release fsbpyr carries one runtime, its stub linked against the default sysroot; an app for glibc 2.28 (EL8) needs the stub linked against the EL8 sysroot, which the companion release of the tag, `el8-vX.Y.Z`, publishes apart from the executables, and the fsbpyr pairs its own standard library with it (the standard library does not depend on the sysroot). The stub comes from, in this order: a file the user placed beside the fsbpyr executable (for machines without GitHub), fsbpyr's cache, or a download from the companion release, checked against the SHA-256 that release publishes and then kept in the cache. Whichever it is, the stub must carry the runtime id this fsbpyr computes for its own launcher sources, pinned Python and EL8 sysroot, and is refused otherwise, so a stub linked from other launcher sources, for another Python or target, or against another sysroot never goes into an app. The id does not name the release: releases with the same launcher sources, pins and sysroot compute the same id and accept each other's stubs. A developer build has no release to fetch from: it uses a runtime of the cache, made by `fsbpyr runtime build --glibc 2.28`. See [The glibc 2.28 stub](#the-glibc-228-stub) | `identity` in [`launcher/main.c`](../src/fsbpyr/launcher/main.c), `stub_identity` in [`runtime/stub.py`](../src/fsbpyr/runtime/stub.py), `stub_for` and `paired` in [`runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py), `select_runtime` in [`runtime/registry.py`](../src/fsbpyr/runtime/registry.py) |

The service policies change no trust: `JOURNAL_STREAM` only line-buffers a stream that is the
file it names (a malformed value or another file does nothing), and as process 1 an app turns
the default SIGTERM into `SystemExit(143)`; neither reads a path or runs anything (see
[Service policies](#service-policies)).

At run time, extracted files are loaded as code, so the cache root and every directory leading
to it must be owned by root or by the user and writable by nobody else, and the launcher checks
each file's digest before it is loaded: see [The cache](#the-cache).

## The defences in the code

### The whole-file integrity check

`fsb_payload_open` in
[`launcher/payload.c`](../src/fsbpyr/launcher/payload.c)
is the first thing `main` does. It opens `/proc/self/exe` and calls `fsb_check_integrity`
([`launcher/integrity.c`](../src/fsbpyr/launcher/integrity.c))
on it before it reads the trailer, maps the payload or starts CPython. The digest is the one
construction of the format (see [Dev-Executable-Format](Dev-Executable-Format.md)): SHA-256 over
the SHA-256 of each 1 MiB leaf, stored in the file's last 32 bytes and covering everything
before them. The launcher hashes the leaves on up to 8 threads with OpenSSL's low-level
`SHA256_Init`/`Update`/`Final` from the static runtime, not the one-shot `SHA256()`: since
OpenSSL 3 that goes through EVP, which may read `openssl.cnf` (`$OPENSSL_CONF`) before the
check is done. The threads read the file with `pread` and never map it, so a file that shrinks
or cannot be read while it is hashed fails the check like a changed one. On a mismatch the
launcher prints

```
fsbpyr: cannot start <path>: integrity check failed: the file was modified or damaged
```

and exits 70 (`fatal` in `launcher/main.c`). Damage to the launcher's own code can also make it
crash before it gets to say so. Only then does it read and check the trailer, the header and
the section table, and `main` the bytecode magic and Python version. Table fields carry no
checksum of their own (the records in
[`launcher/format.h`](../src/fsbpyr/launcher/format.h));
the launcher bounds-checks each record it uses
([`launcher/payload.h`](../src/fsbpyr/launcher/payload.h))
and otherwise relies on the digest, which only the writer's output passes
([Who checks what](Dev-Executable-Format.md#who-checks-what)). What
the check costs is on
[Dev-Performance](Dev-Performance.md#the-integrity-check).

The build side computes the same digest with `tree_digest` and `file_digest` in
[`payload/format.py`](../src/fsbpyr/payload/format.py):
`write_temporary` in
[`payload/writer.py`](../src/fsbpyr/payload/writer.py)
streams the payload into a new `.{name}.XXXX.tmp` file beside the output (mode 0755, fsynced),
reads the digest back from it, and `commit` renames it into place.

### Checking an executable from outside

`fsbpyr inspect` shows the stored digest, which proves nothing by itself. `read(path,
verify=True)` in
[`inspection.py`](../src/fsbpyr/inspection.py)
(`fsbpyr inspect --verify`) recomputes it from the very file it opened (`Executable.verify` in
[`payload/reader.py`](../src/fsbpyr/payload/reader.py),
a leaf at a time through the descriptor) and fails with `<path> is damaged or was changed after
its build`. With `--verify` it also prints the file's plain SHA-256 as `sha256`, the value a
GitHub attestation names; the `digest` line is fsbpyr's own. It refuses a path that is not a
regular file (a FIFO would block the open), and prints text from the build information that is
not printable as its `repr`. Standard tools give the same digest:

```sh
head -c -32 app | split -b 1M --filter='openssl dgst -sha256 -binary' | sha256sum
split -b 1M --filter='openssl dgst -sha256 -binary' < cached-file | sha256sum
```

The integrity digest lives in the file it protects, so these checks catch damage, never a
deliberate rewrite. Origin is checked outside the file:

- **Attestations.** fsbpyr's releases are attested by GitHub Actions; `gh attestation verify`
  shows that this repository's release workflow built the file from its tag
  ([Verifying a download](User-Installing.md#verifying-a-download)); `make release-check` also
  rebuilds a release's executables for the machine's target from the tag and compares them
  byte for byte (`rebuild` in
  [`tools/release/check-release.sh`](../tools/release/check-release.sh)).
  The glibc 2.28 stubs are attested by their own workflow,
  [`.github/workflows/el8.yml`](../.github/workflows/el8.yml).
  An app can be attested the same way:
  [`.github/workflows/attest-example.yml`](../.github/workflows/attest-example.yml)
  is a complete workflow.
- **The installer** checks each download against the SHA-256 written into it, runs
  `fsbpyr inspect --verify` on the new file before it installs it, and with `FSBPYR_VERIFY=1`
  also runs `gh attestation verify`
  ([`install.sh.in`](../tools/release/install.sh.in);
  [The installer](User-Installing.md#the-installer)).
- **`fsbpyr self update`** (`update` in
  [`selfupdate.py`](../src/fsbpyr/selfupdate.py))
  asks GitHub's API for the latest release (`FSBPYR_RELEASE_API` names another release
  document; the tests serve their own), downloads the `.xz` of the asset the running executable
  was released as, unpacks it in-process with `lzma` into a temporary file in the executable's
  directory, and runs `read(new, verify=True)` on it. `check_glibc` then refuses a new
  executable whose build information records a glibc newer than the host's
  (`CS_GNU_LIBC_VERSION`; a host that is not glibc, and a build that records none, pass), with
  `the new fsbpyr needs glibc X, this host has Y; nothing was changed`. Only after both does it
  run the new file (to write the man page and completions) and rename it over the executable's
  real path. A developer build refuses to update. The update does not check the attestation,
  and the glibc check is a guard against a broken install, not a trust check. The glibc 2.28
  stubs placed beside the executable are left there and named in a warning (see
  [Known limits](#known-limits)).
- **The dispatch to another Python** (`variant` in
  [`dispatch.py`](../src/fsbpyr/dispatch.py)),
  when a project needs a Python the running fsbpyr does not carry, fetches the same release's
  variant the same way (`FSBPYR_RELEASE_DOWNLOADS` names another download URL), verifies it
  in-process, applies the same glibc check, and keeps it in `downloads/` of fsbpyr's own
  private cache. Like every executable, the cached variant checks its whole file at every
  start.

### Code blobs are checksummed

The writer stores a module's code only in a checksummed zstd frame: a frame of its own, a
member of a block (itself a frame), or zstd pieces, each a frame; anything else is
`code of ... must be checksummed` (`_check_module` in
[`payload/writer.py`](../src/fsbpyr/payload/writer.py)).
The Python reader requires the same of every module record and requires a content checksum in
every zstd frame (`zstd_frame_has_checksum` in
[`payload/reader.py`](../src/fsbpyr/payload/reader.py)).
In the launcher, `fsb_zstd_frame_ok` in
[`launcher/payload.c`](../src/fsbpyr/launcher/payload.c)
requires the frame magic, the `Content_Checksum_flag`, and a frame size and content size that
match the record before any zstd frame is decoded. `startup_table` in `launcher/main.c` accepts
for the startup modules CPython freezes from the payload only zstd or block code. For the other
modules the boot loader passes what `_fsbpyr.read` returns to `marshal.loads`; `_fsbpyr.read`
(`read_blob` in `launcher/module.c`) checks every zstd frame but would also return a raw blob,
so there the guarantee rests on the writer and on the whole-file digest, which only the
writer's output passes.

### The hardened launcher

[`runtime/stub.py`](../src/fsbpyr/runtime/stub.py)
compiles the launcher with `-fPIE -pie`, `-fstack-protector-strong` and `-D_FORTIFY_SOURCE=2`,
and links it with `-z noexecstack`, `-z relro` and `-z now`. `-Wvla` and
`-Wframe-larger-than=8192` keep the stack bounded; `build_stub` fails a link whose frames go
over the limit. The stub exports only the Python C API (`Py*`, `_Py*`), so wheel libraries
cannot bind to the static zlib, OpenSSL or libffi linked into it. The flags are hashed into the
runtime id (`launcher_digest`), so a flag change gives a new runtime.
[`tests/e2e/test_stub_hardening.py`](../tests/e2e/test_stub_hardening.py)
reads a built stub with `readelf` and `objdump` to check that it keeps them.

Every stub also carries the string `fsbpyr-stub-identity=<runtime id>`: `build_stub` passes
the id of the runtime it links (`runtime_id` in
[`runtime/registry.py`](../src/fsbpyr/runtime/registry.py),
a hash of the launcher sources and flags, the python-build-standalone pin, the sysroot and the
layout version) as `FSBPYR_RUNTIME_ID`, and `main.c` keeps it in a `used` array that LTO and
stripping leave in place. Nothing reads it at run time; `stub_identity` reads it back out of a
file, and a build for glibc 2.28 accepts or refuses a stub it did not link on it
([The glibc 2.28 stub](#the-glibc-228-stub)). The sysroot's part of the id is its name and a
digest of its pinned packages' SHA-256 (`SysrootPin.identity` in
[`runtime/pins.py`](../src/fsbpyr/runtime/pins.py)),
so the stubs of the two baselines never share an id.

The launcher also configures an isolated interpreter that reads no `PYTHON*` variable but
`PYTHONUNBUFFERED` (set and non-empty: unbuffered stdout and stderr) and imports no `site`
(`main` in
[`launcher/main.c`](../src/fsbpyr/launcher/main.c)).

### Privileged starts are refused

Once its file is checked and opened, `main` in `launcher/main.c` exits 70 when
`getauxval(AT_SECURE)` is set, before Python starts, with `refusing a privileged start
(set-uid, set-gid or file capabilities): the cache location comes from the caller's
environment`. The kernel sets it when a start changes privileges: a set-uid or set-gid file
run by another user or group than its own, or file capabilities. A set-uid file run by its
owner changes none and starts
(`test_a_set_uid_start_by_the_owner_is_not_privileged` in
[`tests/e2e/test_runtime_app.py`](../tests/e2e/test_runtime_app.py)).
The cache location comes from the caller's
environment (`FSBPYR_CACHE_DIR`, `XDG_CACHE_HOME`, `HOME`), and under set-gid or capabilities
the invoking user would own the cache. The background collection is not started for such a
start either (`fsb_collect_when_due` in
[`launcher/due.c`](../src/fsbpyr/launcher/due.c)),
nor for process 1. Since Python never starts, nothing the boot code does (such as
`FSBPYR_INSTALL_LINKS`) can run in a privileged start.

### The cache

Native extensions, the files of fs-packages and resources asked for as paths are extracted to
`$FSBPYR_CACHE_DIR`, else `${XDG_CACHE_HOME:-<home>/.cache}/<app>`, where `<home>` is `$HOME`
when it is absolute and otherwise (unset, empty or relative) the home directory of the user's
passwd entry (`cache_directory` and `home_directory` in
[`_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py),
which also refuses an app name with a `/` or one of `""`, `.` and `..`; the launcher's
`cache_root` and `home` in `launcher/due.c` find the same root). They are loaded from
there as code, so the launcher's `fsb_cache_sync`
([`launcher/cache.c`](../src/fsbpyr/launcher/cache.c),
called as `_fsbpyr.sync` from `launcher/module.c`) holds the cache to these rules:

- **The way to the root.** `fsb_way_check` in
  [`launcher/way.c`](../src/fsbpyr/launcher/way.c)
  walks from `/` to the root, the root included. Every directory must be owned by root or the
  user and writable by no other user unless sticky (`/tmp`): not by others, nor by a group
  other than the user's primary one. Symlinks are followed one at a time and must be root's or
  the user's; `..` goes up from where the walk is, as the kernel resolves it. In a user
  namespace an owner outside it shows as the overflow uid (nobody); such a directory counts as
  root's only where no uid inside maps to the overflow uid, and only above a directory anyone
  may add to. A missing part is made, then the whole way is checked again, so a part someone
  else made meanwhile is caught. `way_problem` in
  [`_fsbpyr_rt/gc.py`](../src/fsbpyr/boot/_fsbpyr_rt/gc.py)
  is the same rule in Python, for the collector and for fsbpyr's own cache; a contract test
  compares the two.
- **Directories.** The root must be the user's (`open_root`, `owned`); every directory below it
  is opened with `O_NOFOLLOW`, created 0700 if missing, must be the user's, and is forced to
  0700 (`open_private`).
- **Files.** A cached file is used only if it is a regular file of the user's with the
  payload's size and digest (`load_cached`, `check_batch`); it is opened with `O_NOFOLLOW`,
  `O_NONBLOCK` and `O_NOCTTY`, so a symlink, a FIFO or a terminal device put in its place is
  replaced, never followed, waited for or adopted. Each process hashes the files it uses once,
  before it first uses them. A changed file is reported on stderr, `fsbpyr: <path> was modified
  after extraction; restoring it` (`synced` in `launcher/module.c`), and written again.
- **Writing.** Content is hashed as it is written and put in place only with the payload's
  digest: a new file is written unnamed in its directory (`O_TMPFILE`) and linked into place
  (`place_unnamed`); one that replaces another, or that cannot be linked, is written in the
  tree's `.scratch` directory and renamed (`place_renamed`). Readers see a whole file or none.
  The file is then made readable by its owner, executable where the payload says so, never
  writable (`fill`). Nothing is synced to disk: a file a crash left incomplete fails its
  digest and is written again.
- **Failures.** A root that fails is refused before anything is written, with
  `refusing cache directory <path>: owned by another user` or `... other users may write to
  it`, naming the directory at fault. In an app that refusal is a refused start:
  `NativeCache.extract` in
  [`_fsbpyr_rt/native.py`](../src/fsbpyr/boot/_fsbpyr_rt/native.py),
  which every extraction of the import machinery and the resources API goes through, hands
  it to `refuse`, which flushes stdout and stderr, prints
  `fsbpyr: cannot start <executable>: refusing cache directory ...` and ends the process with
  `os._exit(70)`, so neither an `except` in the app nor the thread it happened in carries on.
  Other extraction failures stay errors the app sees. A file the runtime extracts on its own
  whose record has no digest, or a path that could leave the directory it is opened from
  (`fsb_cache_safe`), fails as `corrupt executable payload`.
- **Collection.** The collector removes only from a root with the marker `.fsbpyr-cache`, and
  only what fsbpyr made there; a directory of the user's that `FSBPYR_CACHE_DIR` names is never
  marked (`mark` in `_fsbpyr_rt/gc.py`, `markable` in `launcher/cache.c`). Its removal never
  follows a symlink (`remove`), and its trigger runs only once the root's way is trusted
  (`trusted` in `launcher/due.c`).

The tests:
[`tests/e2e/test_cache_root.py`](../tests/e2e/test_cache_root.py)
runs each case of the way rule with the release launcher and its ASan/UBSan build, and checks
that an app whose first import extracts a native file into a refused directory prints that one
line and exits 70 (`test_a_refused_cache_directory_ends_the_start_with_one_line_and_exit_70`);
[`tests/unit/boot/test_way.py`](../tests/unit/boot/test_way.py)
checks the rule's Python side.

### Multiprocessing children and helper interpreters

In an executable `sys.executable` is the app, so a child that multiprocessing, joblib or Jupyter
starts as an interpreter starts the app with interpreter arguments. `run` in
[`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py)
looks at the arguments before it runs the app:

- **multiprocessing** ([`_fsbpyr_rt/mp.py`](../src/fsbpyr/boot/_fsbpyr_rt/mp.py)):
  `child_command` matches the spawn, forkserver (per Python version) and resource-tracker
  command lines exactly and decodes their arguments with `ast.literal_eval`; argv is never
  executed. Descriptors must be 3 or above, not stdio, and a forkserver command that names a
  `main_path` or any key an fsbpyr parent never sends is not recognized. `run_child` then
  requires `_parent_runs_this_executable`: the parent's `/proc/<ppid>/exe` has the device and
  inode of this process's `/proc/self/exe`, and the real and effective user and group ids in
  `/proc/<ppid>/status` are this process's. Any doubt counts as no, and a recognized command
  from another parent ends with `refusing to run as a multiprocessing child: not started by
  this program`. A forkserver of an executable with several commands preloads the module of the
  command its parent runs, chosen from the parent's `/proc/<ppid>/cmdline` by the same rule a
  start uses (`_parent_target`).
- **Helper interpreters** ([`_fsbpyr_rt/reexec.py`](../src/fsbpyr/boot/_fsbpyr_rt/reexec.py)):
  `-m MODULE`, `-c CODE` or a `.py` file below the executable, after interpreter flags; in an
  app of scripts also a `.py` path beside the executable, which counts only if it is where a
  module of the payload says it lives (`_script_module`), so a file of the host is the app's
  own argument and is never run. Only a child that passes the same parent check counts as one:
  a shell, a script or a sudo rule gets the app, with these arguments, so a sudo rule for the
  app never hands out an interpreter. With `interpreter-reexec = true` such a child runs the
  module, code or script (a script from the payload); with `false` it gets the app; with
  neither, a child that `asks_for_python` exits 70 with the `refusal` message, since running
  the app in its place would start it again and again. The exception is a script that is the
  target of one of the app's own commands (`_own_command`): it runs that command, as `__main__`
  with its directory first on `sys.path`, which is no more than starting the app under that
  command's name does.
- **Commands named by a link** ([`_fsbpyr_rt/commands.py`](../src/fsbpyr/boot/_fsbpyr_rt/commands.py)):
  in an executable with several commands, a start whose `argv[0]` names a command under
  another basename than the executable's (`is_sibling`: a symlink, since `sys.executable` is
  the resolved file) runs that command with its arguments and is never taken for a helper
  interpreter. A hard link has a name of its own as `sys.executable`, so it keeps the check.
  The dispatcher form `exe COMMAND -m word` is not a helper interpreter either: the check reads
  only the word after the interpreter flags, which is the command's name, so `COMMAND` runs
  with `-m word` as its arguments.
- **The restart idiom** ([`_fsbpyr_rt/restart.py`](../src/fsbpyr/boot/_fsbpyr_rt/restart.py)):
  `[sys.executable, <interpreter flags>, sys.argv[0], *args]`, as gunicorn and Django's
  autoreloader restart themselves, drops the flags and the argument when that argument is this
  very executable (the device and inode of `/proc/self/exe`; a link counts). Only interpreter
  flags may come before it. It has no parent check: it removes an argument that names the
  executable, which gives the caller nothing that `app args` does not, and nothing is imported,
  run or opened from the arguments.

[`tests/unit/boot/test_mp.py`](../tests/unit/boot/test_mp.py)
and [`tests/unit/boot/test_reexec.py`](../tests/unit/boot/test_reexec.py)
cover the first two.

### Scripts and the files beside the executable

In an app built from a project without a build system, a directory of scripts or a single
script, the project's files are stored below the payload root `_fsbpyr_scripts/`, and a module
there says it lives beside the executable: `_shown` in
[`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py)
gives it the `__file__` `<directory of the executable>/<path in the project>`, so the config,
state and log files a script reads next to its `__file__` are the host's, deployed beside the
binary. Its code is still the payload's: `path_hook` claims, through `_overlay_finder`, the
executable's directory and each of its subdirectories that is a directory of the scripts root,
so a `.py` file on the disk there is never imported in place of the payload's module; other
files in those directories are not looked at. A `..` in a `sys.path` entry is resolved inside
the executable, and an entry that would climb above it is not served from the payload. Since
those files are read as the app's input, the directory the executable is deployed in should be
writable by its owner only (D24).

### Linking commands on the target

`FSBPYR_INSTALL_LINKS=DIR app` makes the executable link its commands into DIR and exit
instead of running the app (`_install_links` in `_fsbpyr_boot.py`, `install` in
[`_fsbpyr_rt/links.py`](../src/fsbpyr/boot/_fsbpyr_rt/links.py)).
Each link is an absolute symlink to the executable's real path: one per command, and `x.py`
besides `x` for a command that is a script. A name that is not a plain file name is refused; a
link that already names this executable is left as it is; a file, a directory or a link to
anything else in the way is reported and kept, the other links are still made, and the exit
status is 1. A DIR that is missing or not writable is one error before any link. There is no
argument form, so a `sudo <binary> *` rule cannot be used to make links, and sudo's `env_reset`
drops the variable unless `env_keep` or `SETENV` allows it. The background collection child,
which inherits the environment, does not link.

### Service policies

`_service_policies` in `_fsbpyr_boot.py` costs a start an environment lookup and a `getpid`;
[`_fsbpyr_rt/service.py`](../src/fsbpyr/boot/_fsbpyr_rt/service.py)
is imported only when `JOURNAL_STREAM` is set or the app is process 1. `JOURNAL_STREAM` is parsed
as `<device>:<inode>` (decimal); stdout or stderr is made line-buffered only if `fstat` shows it
is that file and it is not unbuffered already, and any other value does nothing. As process 1,
a handler that raises `SystemExit(143)` is installed only while SIGTERM has its default
disposition, the app's own handler replaces it, and a forked child gets the default back
(`os.register_at_fork`). Neither reads a path or runs anything.

### The instance limit

Before Python starts, `fsb_hold_slot` in
[`launcher/slots.c`](../src/fsbpyr/launcher/slots.c)
takes an OFD lock on one byte of the file `fsbpyr-<euid>` in `/dev/shm`, else in
`$XDG_RUNTIME_DIR`, within a range of bytes chosen from the executable's device and inode. A
start that finds every slot of its executable taken exits 70. The file is used only if it is
a regular file of the user's with no permission bits for group or others, opened with
`O_NOFOLLOW` and `O_NONBLOCK` and never as descriptor 0, 1 or 2. When no file can be trusted,
or the file system has no OFD locks, the start runs unguarded: the guard never refuses for its
own sake. All the names of one executable, its links and hard links, share its slots. The
limit stops an app that keeps starting itself; it is no security boundary (see below).
[`tests/e2e/test_slots.py`](../tests/e2e/test_slots.py)
probes it.

### fsbpyr's own cache and the build inputs

The build tool keeps runtimes, python-build-standalone archives, sysroots and downloads in its
own cache, `$FSBPYR_CACHE_DIR`, else `${XDG_CACHE_HOME:-<home>/.cache}/fsbpyr` with `<home>`
found as for an app's cache (`cache_root` and `_home` in
[`runtime/registry.py`](../src/fsbpyr/runtime/registry.py)).
Cached stubs end up inside every executable built from them, so `secure_directory` creates the
directory 0700 or requires it to be the user's (forcing it to 0700), refuses a symlink there
that is not root's or the user's, and checks the way from `/` with `gc.way_problem` before and
after. `Runtime.load` compares the stub and the standard library with the digests in
`runtime.json`; a mismatch is `corrupt runtime file`. An fsbpyr executable that builds with its
own embedded runtime first verifies its whole file (`Runtime.from_executable`).

What the build takes from outside:

- **python-build-standalone.** Each Python and target has a pinned URL and SHA-256
  ([`runtime/pins.py`](../src/fsbpyr/runtime/pins.py));
  `download` refuses any URL that is not `https://` and checks the SHA-256 before the archive
  is moved into place, and `fetch` checks a cached archive again before extracting it with
  tar's `data` filter
  ([`runtime/pbs.py`](../src/fsbpyr/runtime/pbs.py)).
  A distribution already extracted in fsbpyr's private cache is reused as it is.
- **The glibc sysroot** the launcher links against comes from pinned packages: Debian 12's
  `.deb` files for the default baseline (`SYSROOTS` in `runtime/pins.py`), Rocky Linux 8.10's
  RPMs for glibc 2.28 (`EL8_SYSROOTS`, whose SHA-256 are the ones the Rocky Linux repository
  lists in its `repodata/primary.xml`). Each is downloaded with the same `download` (HTTPS
  only), read once and checked against its SHA-256, so that what is verified is what gets
  extracted (`_package` in
  [`runtime/sysroot.py`](../src/fsbpyr/runtime/sysroot.py)).
  An RPM is read without rpm itself (`payload_tar` in
  [`runtime/rpm.py`](../src/fsbpyr/runtime/rpm.py)):
  its signatures are not read, since the pinned SHA-256 is what is verified; only an
  xz-compressed cpio payload is taken; an entry that is not a regular file, a directory or a
  symlink, a name that leaves the package and an incomplete set of hard links are refused.
  The result is a tar archive that is extracted like a Debian package's: absolute symlinks
  made relative to the sysroot, then tar's `data` filter (`_into_sysroot`).
- **The glibc 2.28 stub** of a release fsbpyr comes from its companion release or from a file
  someone placed, and is checked as the next section says.
- **clang and lld 23**, for building a runtime in `make bootstrap`, come from LLVM's release
  tarball pinned by SHA-256 in
  [`tools/release/llvm23`](../tools/release/llvm23),
  which `llvm23` in
  [`tools/bootstrap.sh`](../tools/bootstrap.sh)
  checks before it unpacks it (`FSBPYR_LLVM23=0` takes a clang/lld 23 or newer from `PATH`
  instead).
- **uv** is whatever is on `PATH`; the build records its version (`fsbpyr inspect` shows it as
  `uv`), since another uv can give other bytes. uv builds the project's wheel out of tree when
  the project has a build system, and installs the dependencies from the lock
  ([`staging.py`](../src/fsbpyr/staging.py)).
- **The package index.** What the lock names is what the executable runs; the app's code and
  dependencies are out of scope of the threat model.

### The glibc 2.28 stub

A stub is the launcher of every app built with it. A release fsbpyr carries the runtime of its
default baseline only, so for `--glibc 2.28` `select_runtime` in
[`runtime/registry.py`](../src/fsbpyr/runtime/registry.py)
calls `paired` in
[`runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py):
the standard library of the fsbpyr's own embedded runtime, read from its file after the whole-file
check of `Runtime.from_executable`, with the stub of its release for glibc 2.28. A developer
build (no release tag in its version, `own_tag`) gets `None` there and uses a runtime of its
cache, which `fsbpyr runtime build --glibc 2.28` links against the Rocky Linux 8.10 sysroot;
that runtime is checked like any other ([2]).

The names come from
[`vcs.py`](../src/fsbpyr/vcs.py): the asset
`fsbpyr-stub-<python>-<target>-glibc2.28` (`release_stub_asset`), the companion release
`el8-vX.Y.Z` of the tag `vX.Y.Z` (`release_stub_tag`), and its `SHA256SUMS-glibc2.28`
(`release_stub_sums`), which lists the SHA-256 of each unpacked stub as `sha256sum` prints
it. `stub_for` takes the first of:

1. **A file placed beside the fsbpyr executable**, named like the asset, plain or with `.xz`,
   in the directory of the path fsbpyr was run by (looked up on `PATH` when run by name, links
   kept) or of its real path (`own_paths`, `_placed`). An `.xz` file is unpacked in memory;
   either is read up to 256 MB, and a larger one is no stub.
2. **The stub kept in fsbpyr's cache**, `downloads/<asset>-<tag>` below the directory that
   `secure_directory` checks. A file there that does not carry the expected runtime id is
   deleted and fetched again.
3. **A download** from `<base>/el8-vX.Y.Z/`, where `<base>` is `FSBPYR_RELEASE_DOWNLOADS` or
   else `https://github.com/devitops-com/fsbpyr/releases/download`. `_published` reads
   `SHA256SUMS-glibc2.28` and takes the line of the asset; `_download` unpacks
   `<asset>.xz` as it arrives into a temporary file in `downloads/`, with the same 256 MB
   limit, and compares the SHA-256 of the unpacked stub with the listed one: on a mismatch
   nothing is kept. A stub that passes both checks is written to `downloads/<asset>-<tag>`
   whole or not at all (`_keep`, a rename).

Each of the three must carry the runtime id this fsbpyr computes for its own launcher
sources and flags, its pinned Python, the EL8 sysroot and the layout version (`runtime_id` in
`registry.py`),
which `_checked` compares with `stub_identity`; otherwise the build stops with
`<where> is not the stub this fsbpyr (vX.Y.Z) needs: ...; use the <asset> of release
el8-vX.Y.Z`. A companion release that is missing, or that lists no stub for this Python and
target, stops the build with `release el8-vX.Y.Z (the glibc 2.28 stubs of vX.Y.Z) does not
exist or has none for this Python: ...`, which names the file to place instead.

What these checks are worth:

- The SHA-256 catches a damaged or swapped download, not a release whose stub and sums were
  both replaced. The stubs are attested by the workflow that publishes them,
  [`.github/workflows/el8.yml`](../.github/workflows/el8.yml);
  fsbpyr does not check those attestations itself.
- The runtime id tells a stub of other launcher sources, another Python or target, or the
  default sysroot from the right one. It is no digest: whoever can write the file can write
  the id. A placed or cached stub is accepted on the id alone, so whoever can write the
  directory of the fsbpyr executable, or `downloads/` in fsbpyr's cache, can put code into
  every EL8 app built there. D20 keeps the cache to the user; check a placed file against
  `SHA256SUMS-glibc2.28` of the companion release before placing it (the sums are of the
  unpacked stub).
- The id covers what the stub is linked from, not the release: a release whose launcher
  sources and flags, Python pin, EL8 sysroot pins and layout version are those of another
  computes the same id, and the stub of either is accepted by both. v0.26.0 and v0.27.0 are
  such a pair: both release executables for Python 3.14 on x86-64 embed the default runtime
  `cp314-x86_64-unknown-linux-gnu-add67757fca9`, and their EL8 ids, computed from the same
  inputs with the same EL8 sysroot pins, match as well.

### The project on the build host

A project with a build system is built by its backend (`uv build`, `_uv_build` in
`staging.py`), and the smoke test imports the entry module: that code runs on the build host,
which the threat model leaves out of scope. A project with a `[project]` table and no
`[build-system]`, a directory of scripts and a single script are built so that none of their
scripts runs there:

- **No backend.** `_uv_build` returns no command for such a project; `loose.py` writes the wheel
  itself with the standard library, holding the project's Python files and nothing else under
  `_fsbpyr_scripts/`. In a git work tree the files are the ones `git ls-files` lists as tracked
  (`work_tree_files` in [`vcs.py`](../src/fsbpyr/vcs.py));
  an untracked Python file that is not ignored is left out with a warning naming it, so a
  `secrets.py` nobody committed is never in the executable. A link to a `.py` file outside the
  project directory fails the build. A single script takes only itself and its static local
  imports.
- **Data, not code.** Requirements files, `pylock.toml`, Poetry's and PDM's locks
  ([`foreign.py`](../src/fsbpyr/foreign.py),
  [`poetry1.py`](../src/fsbpyr/poetry1.py)),
  `Pipfile.lock` ([`pipenv.py`](../src/fsbpyr/pipenv.py))
  and PEP 723 blocks are parsed as TOML, JSON or text. When nothing declares a dependency,
  [`venvdeps.py`](../src/fsbpyr/venvdeps.py)
  reads the `*.dist-info` files (`METADATA`, `top_level.txt`, `RECORD`, `direct_url.json`) of
  the venv the scripts run in, never starts its Python and copies nothing from it: the
  distributions found become `name==version` pins that uv installs fresh.
- **Static checks.** The unresolved-import check
  ([`importcheck.py`](../src/fsbpyr/importcheck.py))
  follows each command's imports with `ast`, and the reads next to `__file__` the build warns
  about are found by the compiler's scan.
- **The smoke test finds a script and does not import it** (`_check_entrypoint` in
  `_fsbpyr_boot.py`): a command that runs a module as `__main__` is looked up in the payload
  only. A `module:function` command (from `[project.scripts]`) is imported to look the function
  up, as in any project.

Dependencies are still installed by uv, and a dependency without a wheel is built from its
sdist, which runs its build code; dependencies are out of scope as above.

## Known limits

- **A rewritten digest.** The digest sits in the file it protects. Someone who can change the
  file can change the digest too, or patch the check out, since the code that checks is in the
  same file. Only checking from outside catches that: a release's provenance and SBOM
  attestations (checked with `gh attestation verify`), the installer (SHA-256 of each download
  and `fsbpyr inspect --verify`; `FSBPYR_VERIFY=1` adds the attestation) and a rebuild from the
  tag compared byte for byte (`make release-check`). No signature checked with a key from outside the
  file exists at v0.27.0.
- **glibc 2.28 stubs** (D46). A downloaded stub is checked against the sums of its companion
  release, which catches a damaged or swapped file but not a release whose stubs and sums were
  both replaced; the attestations of
  [`el8.yml`](../.github/workflows/el8.yml)
  would, and fsbpyr does not check them. A stub placed beside the fsbpyr executable, or kept in
  `downloads/` of its cache, is accepted on the runtime id it embeds, which anyone who can
  write the file can write too: whoever can write either directory can put code into every
  EL8 app built there. See [The glibc 2.28 stub](#the-glibc-228-stub).
- **Self update's glibc check** refuses a new executable that needs a newer glibc than the host
  has, before replacing anything; it guards against a broken install and checks no trust.
  Self update leaves glibc 2.28 stubs placed beside the executable where they are, and names
  them in a warning (`fsbpyr-stub-*-glibc*`, `update` in
  [`selfupdate.py`](../src/fsbpyr/selfupdate.py)).
  A build checks them on their runtime id only: they pass when the new release computes the
  same id as the old one, and are refused when it does not (see [The glibc 2.28
  stub](#the-glibc-228-stub)).
- **Files beside the executable are input.** In an app of scripts, whoever can write the
  executable's directory chooses the config and data its scripts read there (not their code).
- **POSIX ACLs and shared primary groups.** The cache root check reads the mode bits and the
  owner. A POSIX ACL on the cache root, or a primary group shared with other users, can give
  others write access that the check does not see. Keep the cache root in a directory only
  you can reach.
- **`/proc`.** An executable finds itself through `/proc/self/exe`, and the parent check reads
  `/proc/<ppid>`. Without `/proc` (or with one that is not procfs) it does not start, or the
  parent check says no.
- **Privileged uses are refused.** `setcap cap_net_bind_service+ep app` and set-gid
  deployments stop working; a set-uid file run by its own owner changes no privileges and
  starts. A fixed root-owned cache that ignores `FSBPYR_*`, XDG and `HOME`
  would allow them, at the cost of a second cache policy; it was not chosen.
- **The instance limit is no security boundary.** It stops an app that keeps starting itself,
  not an attacker: any process of the user can hold every slot (out of scope above), and code
  that closes its descriptors, as daemonizing code does, gives its slot up.
- **CVEs in the embedded runtime.** CPython and the libraries linked into it are fixed only by
  rebuilding with a newer fsbpyr (SECURITY.md, "Fixes to the runtime").

## See also

- [Dev-Launcher](Dev-Launcher.md): the start sequence these checks are part of.
- [Dev-Executable-Format](Dev-Executable-Format.md): the trailer, the digest and the validation
  rules.
- [Dev-Compatibility](Dev-Compatibility.md): what the cache, the refusals and the files beside the
  executable mean for apps.
- [Dev-Performance](Dev-Performance.md): what the checks cost.
- [User-Running](User-Running.md) and [User-Installing](User-Installing.md): the same checks from a
  user's side.
