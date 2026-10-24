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

## What stops what

| Decision | Stops |
|---|---|
| D17 | Fixed addresses: the launcher and runtime are a position-independent executable, hardened with RELRO, BIND_NOW, a non-executable stack and stack protector. |
| D19 | Corrupt code objects: every code blob is a checksummed zstd frame, and readers require the checksum, so `marshal.loads` never sees damaged bytes. |
| D20 | Another user planting a runtime or stub: cache directories must be the user's own and are forced to 0700; the cached runtime's digests are verified on load. |
| D22 | A caller pretending to be multiprocessing: the three child command lines are matched exactly and decoded with `ast.literal_eval`, never executed, and run only if the parent is this same executable (device and inode of `/proc/<ppid>/exe`) with the same real and effective user and group ids. |
| D23, D44 | The same for helper interpreters (`-m`, `-c`, a `.py` file of the app), which run only with `--interpreter-reexec`; without the setting the app's own children asking for one exit 70, and any other caller gets the app. |
| D45 | Another user denying a user's starts through the instance limit: the slot file must be a regular file of the user's that nobody else may use, else it is passed over for the one in `$XDG_RUNTIME_DIR`, and with no file to trust the start runs unguarded. A file made first by someone else in `/dev/shm` can cost the user the guard, never a start. |
| D31 | A changed executable: the launcher checks the whole file against the digest in its trailer before it reads anything else. |
| D41 (SEC-3) | A privileged start: when the kernel sets AT_SECURE (set-uid, set-gid, file capabilities) the executable exits 70, because the cache location would come from the caller's environment and, under set-gid or capabilities, the invoking user would own the cache. |

At run time, extracted files are loaded as code, so the cache root and every directory
leading to it must be owned by root or by the user and writable by nobody else (README,
"At run time"); the launcher checks each file's digest before it is loaded.

## Known limits

- **A rewritten digest.** The digest sits in the file it protects. Someone who can change the
  file can change the digest too. Only checking from outside catches that: a release's
  provenance and SBOM attestations (made by release.yml, checked with `gh attestation
  verify`), the installer (`install.sh` checks each download against the SHA-256 the release
  recorded and runs `fsbpyr inspect --verify`; `FSBPYR_VERIFY=1` adds the attestation) and the
  reproduction steps in the README ("Verifying a release"). A signature checked with an outside
  key (D31) comes with signed releases, after the MVP.
- **POSIX ACLs and shared primary groups.** The cache root check reads the mode bits and the
  owner. A POSIX ACL on the cache root, or a primary group shared with other users, can give
  others write access that the check does not see. Keep the cache root in a directory only
  you can reach.
- **`/proc`.** An executable finds itself through `/proc/self/exe`, and the parent check reads
  `/proc/<ppid>`. Without `/proc` (or with one that is not procfs) it does not start, or the
  parent check says no.
- **Privileged uses are refused.** `setcap cap_net_bind_service+ep app` and set-gid
  deployments stop working. A fixed root-owned cache that ignores `FSBPYR_*`, XDG and HOME
  would allow them, at the cost of a second cache policy; it was not chosen.
- **The instance limit is no security boundary** (D45). It stops an app that keeps starting
  itself, not an attacker: any process of the user can hold every slot (out of scope above),
  and code that closes its descriptors, as daemonizing code does, gives its slot up.
- **CVEs in the embedded runtime.** CPython and the libraries linked into it are fixed only by
  rebuilding with a newer fsbpyr (SECURITY.md, "Fixes to the runtime").
