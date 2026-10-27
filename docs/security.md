# Threat model

What fsbpyr protects, from whom, and where that stops. The decisions (D-numbers) are in
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md); reporting a vulnerability and the fix
targets are in [SECURITY.md](../SECURITY.md).

## Assets

- The code an executable runs: its payload, the extracted native libraries in the cache, and
  the embedded runtime.
- The user's account and files: an executable runs with its caller's rights.
- The privileges of programs that run an executable for someone else, such as a sudo rule.

## Attackers in scope

- **Corrupt or modified executables:** a bit flip, a truncated download, a changed byte
  anywhere in the file.
- **Other local users** who can write where the cache is, or who can redirect the path to it.
- **A caller with no right to run Python:** a shell, script or sudo rule that runs the app
  with arguments crafted to look like multiprocessing or interpreter re-exec.
- **A caller that chooses the environment of a privileged process.**

## Out of scope

- The user themselves, and any process running as that user: it can already do what the
  executable can.
- Root.
- An attacker who rewrites the executable and its digest together (see the limits below).
- The app's own code and dependencies: an executable runs what it was built from.
- **Code the build host runs for a project that has a build system:** `uv build` runs the
  project's build backend, sdists of dependencies are built, and the smoke test imports the
  entry module (`--entry`, an inferred module, `module:function`), so a packaged app's
  top-level code runs on the build host with the builder's rights. Build only projects you
  trust.

## What stops what

| Decision | Stops |
|---|---|
| D17 | Fixed addresses: the launcher and runtime are a position-independent executable, hardened with RELRO, BIND_NOW, a non-executable stack and stack protector. |
| D19 | Corrupt code objects: every code blob is a checksummed zstd frame, and readers require the checksum, so `marshal.loads` never sees damaged bytes. |
| D20 | Another user planting a runtime or stub: cache directories must be the user's own and are forced to 0700; the cached runtime's digests are verified on load; a stub kept in `downloads/` (EL8) is protected by the directory's ownership and mode and by its embedded runtime id, not by a digest (see the limits below). |
| D22 | A caller pretending to be multiprocessing: the three child command lines are matched exactly and decoded with `ast.literal_eval`, never executed, and run only if the parent is this same executable (device and inode of `/proc/<ppid>/exe`) with the same real and effective user and group ids. |
| D23, D44 | The same for helper interpreters (`-m`, `-c`, a `.py` file of the app), which run only with `--interpreter-reexec`; without the setting the app's own children asking for one exit 70, and any other caller gets the app. The one start allowed without the setting is a child of the app itself whose `.py` file below the executable is the script of one of the app's own commands (`[sys.executable, <exe>/_fsbpyr_scripts/x.py, args]`): it runs that command from the payload, never a file on the disk, and only after the D22 parent check, so it grants no more than the app does; any other script, `-m` or `-c` keeps the refusal. A start named for one of the commands under another name than the executable's (a link) runs that command and is no helper interpreter; a hard link is not that and keeps the refusal. The dispatcher form `exe B -m word` is no helper start at all: only the first argument after the interpreter flags is read (`reexec.child_command`), so it runs command `B` with `-m word` as its arguments. |
| D45 | Another user denying a user's starts through the instance limit: the slot file must be a regular file of the user's that nobody else may use, else it is passed over for the one in `$XDG_RUNTIME_DIR`, and with no file to trust the start runs unguarded. A file made first by someone else in `/dev/shm` can cost the user the guard, never a start. |
| D31 | A changed executable: the launcher checks the whole file against the digest in its trailer before it reads anything else. |
| D41 (SEC-3) | A privileged start: when the kernel sets AT_SECURE (set-uid or set-gid to another user or group, or file capabilities; a set-uid copy run by its owner changes nothing and starts) the executable exits 70, because the cache location would come from the caller's environment and, under set-gid or capabilities, the invoking user would own the cache. |
| D14 (`FSBPYR_INSTALL_LINKS`) | A caller using the links feature to create files as another user: it is an environment variable only, with no `--install` argument, because under a `<binary> *` sudo rule an argument would let any caller create root-owned links anywhere, and sudo's `env_reset` (a default) drops `FSBPYR_*` unless `env_keep` or `SETENV` allows it. It makes symlinks to the executable's real path only (`name` and `name.py`), never replaces a file, directory or a link to anything else (reported, exit 1), refuses names that are not plain file names, and cannot run in a privileged start (D41 exits first). |
| D24 (overlay) | A file planted beside the executable running as code: in an app of scripts, `__file__` is `dirname(executable)/<path>` so that config, state and logs deployed beside the binary are read by `open(dirname(__file__) + ...)`; the modules themselves always come from the payload (the path hook claims those directories, so a `.py` copy on the disk never shadows one). Data beside the executable is read as the app's input, so keep that directory writable by its owner only. |
| D5 (UC3, UC6) | Project code on the build host, for a project with no build system, a directory of scripts and a single script: no build backend runs and no script command is imported (the smoke test only finds a script module). A `module:function` command that such a project declares in `[project.scripts]` is the exception: the smoke test imports its module (`_check_entrypoint`, `_resolve`), so its top-level code runs on the build host. In a git work tree the wheel is written by fsbpyr from the `*.py` files (and extensionless files with a python or `uv run` shebang) that `git ls-files` lists as tracked, so an untracked `secrets.py` is never in it; a link to a file outside the project directory is an error. A venv's `*.dist-info`, `Pipfile.lock`, `poetry.lock`, `pdm.lock`, requirements files and PEP 723 blocks are read as data (METADATA, RECORD, TOML, `ast`), never executed, and the unresolved-import check follows imports with `ast` alone. |
| D46 | A stub built from other inputs (another Python, target or sysroot, other launcher sources) in an app: every stub embeds the runtime id it was linked as, and the build refuses a stub it is handed, the EL8 ones today (`--glibc 2.28`), whose id differs from the id it computes (`stubs._checked`). Any other build takes its runtime's stub, checked by the runtime's digests, or the running fsbpyr's own, and compares no ids. A download is checked against the published SHA-256 and kept whole or not at all. |

The service policies change no trust: `JOURNAL_STREAM` only line-buffers a stream that is the
file it names (a malformed value or another file does nothing), and as process 1 an app turns
the default SIGTERM into `SystemExit(143)`; neither reads a path or runs anything.

At run time, extracted files are loaded as code, so the cache root and every directory
leading to it must be owned by root or by the user and writable by nobody else ([Running](https://github.com/devitops-com/fsbpyr/wiki/User-Running#the-cache)
in the wiki); the launcher checks each file's digest before it is loaded.

## Known limits

- **A rewritten digest.** The digest sits in the file it protects. Someone who can change the
  file can change the digest too. Only checking from outside catches that: a release's
  provenance and SBOM attestations (made by release.yml, checked with `gh attestation
  verify`; the EL8 stubs, published in the companion release `el8-vX.Y.Z`, are attested by el8.yml, a
  workflow of its own whose only writing job is `publish`, checked with `--signer-workflow
  .../el8.yml --source-ref refs/tags/vX.Y.Z`), the installer (`install.sh` checks each download against the SHA-256 the release
  recorded and runs `fsbpyr inspect --verify`; `FSBPYR_VERIFY=1` adds the attestation) and the
  reproduction steps in [CONTRIBUTING.md](../CONTRIBUTING.md#verifying-a-release). A signature checked with an outside
  key (D31) comes with signed releases, after the MVP.
- **EL8 stubs** (D46). A stub is the launcher of every app built with `--glibc 2.28`. A
  download comes from the companion release `el8-vX.Y.Z` and is checked against
  `SHA256SUMS-glibc2.28` of the same release (over HTTPS; `FSBPYR_RELEASE_DOWNLOADS` can point
  elsewhere, and a mirror must serve the stubs under `.../el8-vX.Y.Z/<asset>`, not under
  `.../vX.Y.Z/`), which catches a damaged or swapped file but not a release whose assets and
  sums were both replaced; the attestations of el8.yml (a run on the release's tag `vX.Y.Z`, never the default branch's context
  executing the tag's code) catch that, and fsbpyr does not check them itself. Releases are
  immutable, and el8.yml publishes the companion release whole, so its assets cannot be replaced
  after publication. A stub placed beside the fsbpyr executable, or
  already in the cache, is accepted on the runtime id it embeds (a hash of the launcher sources and the compile and link flags,
  the Python pin, the sysroot and the layout version), not on its digest: that detects a stub
  built from other inputs (another Python, target or sysroot, other launcher sources), not a
  tampered one, because anyone who can write the file can write the id. Releases built from the
  same inputs share an id and take one another's stubs.
  Whoever can write the directory of the fsbpyr executable or `downloads/` in the cache can put
  code into every EL8 app built there; D20 keeps the cache to the user, and a placed file
  should be checked against the sums first.
- **Self update** verifies the new executable's digest (`inspect --verify`) and refuses one
  whose glibc baseline is newer than the host's before replacing anything; that is a guard
  against a broken install, not a trust check. Stubs placed beside the executable are not
  touched. The update names those that are not stubs of the new release (their SHA-256 is not
  in its companion's `SHA256SUMS-glibc2.28`), which a build refuses (the runtime id differs);
  when it cannot read that file, it names all of them, as refused if the runtime id changed.
- **POSIX ACLs and shared primary groups.** The cache root check reads the mode bits and the
  owner. A POSIX ACL on the cache root, or a primary group shared with other users, can give
  others write access that the check does not see. Keep the cache root in a directory only
  you can reach.
- **`/proc`.** An executable finds itself through `/proc/self/exe`, and the parent check reads
  `/proc/<ppid>`. Without `/proc` (or with one that is not procfs) it does not start, or the
  parent check says no.
- **Privileged starts are refused.** `setcap cap_net_bind_service+ep app` and set-gid
  deployments stop working (a set-uid start by the file's own owner changes no privileges
  and is not refused). A fixed root-owned cache that ignores `FSBPYR_*`, XDG and HOME
  would allow them, at the cost of a second cache policy; it was not chosen.
- **The instance limit is no security boundary** (D45). It stops an app that keeps starting
  itself, not an attacker: any process of the user can hold every slot (out of scope above),
  and code that closes its descriptors, as daemonizing code does, gives its slot up.
- **CVEs in the embedded runtime.** CPython and the libraries linked into it are fixed only by
  rebuilding with a newer fsbpyr (SECURITY.md, "Fixes to the runtime").
