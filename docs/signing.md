# Signed fsbpyr releases and signed apps: concept

Status: concept, 2026-10-29, at fadc3d9, revised the same day after an independent review. It
replaces the earlier concept on branch `signing-concept` (SSHSIG signatures embedded in the
executable), which is dropped. Implemented so far: `tools/release/release-signing.sh` only.

Facts without a mark were run or read while this concept was made. **[A]** marks an assumption
that nobody has checked; §11 lists what to test.

## 1. Summary

- **Two signatures, always both.** Ed25519 and ML-DSA-65 sign one short statement that holds the
  file's SHA-256. A check passes only if both are good:
  - ML-DSA-65 guards against a future quantum computer;
  - Ed25519 guards against a flaw in the young ML-DSA code.

  The pair costs the user nothing: no extra step, 3.3 KB per signature.
- **Detached.** The signature is `FILE.sig`, next to FILE. Whoever wants to check keeps the two
  together.
- **fsbpyr does the crypto itself.** It runs in launcher C (`_fsbpyr`), through the OpenSSL 3.5.9
  that every fsbpyr already carries. Checks need nothing installed and behave the same on every
  distribution fsbpyr runs on. Python reads files, asks for passphrases and prints.
- **fsbpyr's releases.** Each release signs one `SHA256SUMS`, and the EL8 companion release signs
  `SHA256SUMS-glibc2.28`.
  - CI signs with the release key K, using an openssl script, never with a built fsbpyr.
  - fsbpyr pins the public keys of K and of the recovery key R, which never enters CI.
  - A release that cannot be signed, or whose signature the clients would refuse, is not
    published.
- **Apps.** `fsbpyr build` signs when a key is set. If the key cannot be used, it prints a warning
  and builds unsigned. Users check with `fsbpyr verify --project`. How publishers hand out their
  keys, and how users keep them, is out of scope.
- **Trust.** The first fsbpyr a user installs is trusted as GitHub serves it. Each later fsbpyr is
  checked by the one before it (`self update`).

## 2. Formats

The script and fsbpyr write and read exactly these files. The script's `.pub` and `.sig` were read
and checked with the bundled libcrypto through EVP: `PEM_read_bio_PUBKEY_ex` twice on one file,
then `EVP_DigestVerify` on each half (measured; §4).

### 2.1 Statement

```
fsbpyr-signature 1
subject <project>
sha256 <the file's SHA-256 in lowercase hex>
```
- ASCII with LF line ends, ending in an LF.
- `subject` is the project name, PEP 503 normalized: `[a-z0-9-]`, 1–64 characters.
  - `fsbpyr` is reserved: only K and R sign it, and `key trust` refuses it.
- The statement is never stored. The signer and the checker each build it from the project name
  and the file.
- It binds the project: a signature for project A fails for project B, even with the same key.
- It holds no version. Where fsbpyr needs a version, it reads it from the checked file's build
  info (§5.5).

### 2.2 Signature, `FILE.sig`

- Exactly 3373 bytes:
  - the Ed25519 signature, 64 bytes;
  - then the ML-DSA-65 signature, 3309 bytes.

  Both sign the statement, in pure mode, with no context string.
- Any other size is not a signature of this format. A later format must differ in size.
- ML-DSA-65 signing is randomized. Signing the same file twice gives different bytes, and both
  verify (measured).

### 2.3 Keys

- `NAME.key`: two encrypted PKCS#8 PEM blocks under one passphrase, Ed25519 then ML-DSA-65
  (6088 bytes).
- `NAME.pub`: the two public keys as PEM, in the same order, and nothing else (2839 bytes).
- Fingerprint: `SHA256:` followed by the SHA-256 of the `.pub` file, in lowercase hex.
- Readers refuse a `.pub` with anything else in it, with the keys in the other order, or with other
  algorithms (the script does; measured).
- Passphrase: `FSBPYR_SIGNING_PASSPHRASE`, otherwise asked for on the terminal.

## 3. Commands

| Command | What it does |
|---|---|
| `fsbpyr key new NAME` | Makes both keys, writes `NAME.key` (mode 600) and `NAME.pub`, and prints the fingerprint. Asks for the passphrase twice unless `FSBPYR_SIGNING_PASSPHRASE` is set. Replaces nothing. |
| `fsbpyr key trust PROJECT PUBKEY FINGERPRINT [--replace]` | Checks that PUBKEY has FINGERPRINT and saves it as PROJECT's key. If a different key is already saved for PROJECT, it is replaced only with `--replace`. |
| `fsbpyr sign FILE [--project P] [--signing-key KEY]` | Writes `FILE.sig`, after checking the new signature. Replaces nothing. |
| `fsbpyr verify FILE [--project P]` | Checks FILE and prints `VERIFIED` or `NOT VERIFIED`, with the project it checked FILE as. With `--project`, FILE must be P's. |

Rules shared by the commands:
- **Project.**
  - For an executable that fsbpyr built, the name comes from its build info.
  - For other files, `--project` is required: "FILE has no build info: name its project with
    --project".
  - `sign`: a `--project` that differs from the build info is an error (exit 2).
  - `verify`: a `--project` that differs from the build info is NOT VERIFIED (exit 1): the file
    is another project's. Without `--project`, `verify` checks FILE as the project its build info
    names, so a genuine file of any project the user trusts passes. Whoever expects a certain app,
    a script above all, passes `--project`.
- **Signing key.** `--signing-key KEY`, otherwise `FSBPYR_SIGNING_KEY` (a path).
- **Checking key.**
  - For `fsbpyr`: the keys this fsbpyr pins (K and R).
  - For any other project: the key that `key trust` saved, in
    `${XDG_CONFIG_HOME:-~/.config}/fsbpyr/trusted/<project>.pub`.
- **Which signature.**
  - `FILE.sig`.
  - Without one: a signed sums file beside FILE (`SHA256SUMS` or `SHA256SUMS-glibc2.28`, with its
    `.sig`) that lists FILE's SHA-256. The sums file is checked as a file of the project, then FILE
    is looked up by its SHA-256, so its local name does not matter.
  - This is how fsbpyr's own downloads are checked.
- **Exit status.**
  - 0: VERIFIED.
  - 1: NOT VERIFIED.
  - 2: usage or setup errors: no trusted key, an unreadable key file, a wrong passphrase.

| `fsbpyr verify` prints | Exit |
|---|---|
| `VERIFIED  myapp  (project myapp, Ed25519 + ML-DSA-65, key SHA256:…)` | 0 |
| `NOT VERIFIED: myapp: the signature does not match the trusted key of myapp` | 1 |
| `NOT VERIFIED: myapp: it is an executable of project otherapp, not of myapp` | 1 |
| `NOT VERIFIED: myapp: no myapp.sig, and no signed SHA256SUMS beside it lists it` | 1 |
| `NOT VERIFIED: myapp: myapp.sig is not a signature (N bytes)` | 1 |
| `no trusted key for myapp: get the publisher's key and its fingerprint, then: fsbpyr key trust myapp KEY.pub SHA256:…` | 2 |

A pass means that the holder of the trusted key signed exactly this file, as the project it
prints. It does not mean that the app is safe, that it is the newest version, or that the key
belongs to whom the user thinks.

### 3.1 `fsbpyr build`

- **A signing key is set** (`--signing-key` or `FSBPYR_SIGNING_KEY`): after the smoke test, the
  build signs the output as `fsbpyr sign` does and writes `<output>.sig` beside it:
  `dist/<name>.sig`, or `PATH.sig` with `-o PATH`.
- **The passphrase reaches no other code.** The build starts processes that run third-party code
  as the user: uv, the build backends of dependencies, and the new app in the smoke test
  (build.py:634 passes it the whole environment). So:
  - fsbpyr reads `FSBPYR_SIGNING_KEY` and `FSBPYR_SIGNING_PASSPHRASE` once, at start, and removes
    both from its environment before it starts any process;
  - without the variable, it asks for the passphrase only after the smoke test.

  That code can still read the key file, as the user can, but not the passphrase.
- **The key cannot be used** (missing file, not a key file, wrong or missing passphrase): the
  build prints a warning, finishes unsigned, and exits 0. Signing is optional for apps:
  ```
  fsbpyr: WARNING ****************************************************************
  fsbpyr: WARNING  dist/myapp is NOT SIGNED: cannot open /home/me/keys/myapp.key:
  fsbpyr: WARNING  wrong passphrase? Unset FSBPYR_SIGNING_KEY to build unsigned
  fsbpyr: WARNING  without this warning.
  fsbpyr: WARNING ****************************************************************
  ```
- **No key is set:** nothing is signed and nothing is printed about it.
- **Stale signatures:** when the build replaces its output, it removes an old `<output>.sig`, so
  an old signature never sits next to a new build.
- **fsbpyr's own release** is never signed by `fsbpyr build` (§5.3). For fsbpyr, a signature that
  fails stops the release.

## 4. Inside fsbpyr: C and Python

- `_fsbpyr` gains four private, undocumented functions. All of them go through EVP, in a private
  `OSSL_LIB_CTX` of the bundled OpenSSL 3.5.9:
  - `_sha256(fd)`: the file's SHA-256, in C, so no Python hashing sits on these paths;
  - `_key_new(passphrase)`: both keys, returned as the `.key` and `.pub` bytes;
  - `_sign(key_pem, passphrase, statement)`: the 3373-byte signature;
  - `_verify(pub_pem, statement, signature)`: true only if both halves are good.
- Python handles the arguments, the files, the statement, the passphrase (`getpass`), the trust
  store and the messages.
- **Measured:** a C test against the PBS `libcrypto.a` (cpython-3.14.10+20261026, OpenSSL 3.5.9)
  made and checked Ed25519 and ML-DSA-65 signatures in a private context. A second test read and
  checked the script's files. Each test is about 40 lines including its scaffolding. The four
  functions are estimated at 100–150 lines [A].
- `_fsbpyr` is in every app fsbpyr builds, so these functions are too. They stay private.
  libcrypto is linked already, so the glue adds little per stub [A: to measure].
- Run from source, without `_fsbpyr`, the commands fail with "signing needs a built fsbpyr".
- An app does not check its own signature: whoever can change the file can remove the check.

## 5. fsbpyr's releases

### 5.1 Keys

- **K**, the release key, signs in CI only, from secrets of the GitHub environment
  `release-signing`.
- **R**, the recovery key, never enters CI. The admin signs with it by hand (§5.4).
- **Pins.** Each `.pub` file in src/fsbpyr/release-keys/ (new) is packaged into fsbpyr, and a
  signature by any of them passes. Normally these are k.pub and r.pub. For one release during a
  change of K there are three (§5.4).
- **Key files.** On which machine the keys are made, and where the key files and passphrases are
  kept, is the admin's choice and job; R belongs offline. fsbpyr stores no private key.

### 5.2 One-time setup

1. **Make the keys** with OpenSSL 3.5 or newer (Debian 13 has 3.5.7), R preferably on a machine
   that is offline. Each command asks for its own passphrase:
   ```
   bash tools/release/release-signing.sh new-key ~/release-k
   bash tools/release/release-signing.sh new-key ~/release-r
   ```
2. **Give K to the environment.** `gh secret set` asks for each value [A]:
   ```
   gh secret set FSBPYR_RELEASE_KEY --env release-signing < ~/release-k.key
   gh secret set FSBPYR_SIGNING_PASSPHRASE --env release-signing
   ```
   CI needs no other copy of K. GitHub never shows a secret again, so whether to keep
   `release-k.key` to set it again is the admin's choice. `release-r.key` and its passphrase go
   offline.
3. **Restrict the environment.** `release-signing` gets one deployment rule: the tag pattern `v*`,
   as a tag rule rather than a branch rule [A]. Only tag runs then get K. The rulesets of
   2026-10-29 stay:
   - `release tag creation`: only the admin role creates `v*` and `el8-v*` tags;
   - `release tags`: nobody updates or deletes them.
4. **Pin the keys.** Copy `release-k.pub` and `release-r.pub` to src/fsbpyr/release-keys/k.pub
   and r.pub, and commit them with the release that brings signing.
5. **Release.** The first signed release goes through §5.3 like every other. Its signature is
   checked against its own keys only, because no earlier release pins any (§5.3, §5.6).

### 5.3 Release pipeline

| Job | Change |
|---|---|
| `build`, `sbom` | none |
| `prepare` (new) | The part of today's `publish` that runs built code: `make-install.sh`, where the built fsbpyr reads the executables. Then `SHA256SUMS` as written today (release.yml:141). It uploads both. Its token is read-only (`contents: read`) and it gets no secret. Today's `publish` runs that fsbpyr while it holds `contents: write` and `id-token: write` (release.yml:66-71). |
| `sign` (new) | The steps below. |
| `publish` | Runs no built code. It attests as today, with `SHA256SUMS` added to the attested files, then runs `gh release create` as today (release.yml:143), with `SHA256SUMS.sig` added. |
| el8.yml | Its own `sign` job, with the same steps, for `SHA256SUMS-glibc2.28` (written at el8.yml:669; it gains the `.xz` lines, §5.5). Its publish step adds `SHA256SUMS-glibc2.28.sig`, with the same condition as `publish`. |

The `sign` job, in order:
1. `environment: release-signing`, in a Debian 13 image pinned by digest. It checks out the tagged
   commit with the tags, for the script and the pinned keys.
2. It installs `openssl`: the image has the library but not the command (measured). apt checks
   Debian's signatures. This step runs before K is anywhere in the job.
3. One step holds K. Only this step's `env` gets the secrets `FSBPYR_RELEASE_KEY` and
   `FSBPYR_SIGNING_PASSPHRASE`. It writes K into `$RUNNER_TEMP` and runs `release-signing.sh
   sign`, which checks the new signature against K's own public keys. Then it removes K.
4. It checks the signature as the clients will, with `release-signing.sh verify` (measured: a
   signature by the wrong key passes step 3 and fails here):
   - against the keys that the previous `v*` tag pins (`git show`): the fsbpyr that updates to
     this release, and that reads this tag's EL8 sums during the update (stubs.py:135);
   - against the keys that this tag pins: this release's own fsbpyr, whose dispatch and EL8
     stubs read this tag's sums.

   For the first signed release, the previous tag pins nothing, and only the second check runs.
5. It uploads the `.sig`.

It runs no fsbpyr, no built code and no third-party action.

`publish` runs only if `prepare` and `sign` succeeded. The one exception is recovery
(`vars.FSBPYR_RECOVERY == '1'`, §5.4): there, `sign` is skipped and `publish` makes a draft
(`--draft`). A skipped job skips the jobs that need it, so the condition starts with
`!cancelled()`. Nothing else in it may let a release through unsigned.

Any failure stops the release, so fsbpyr is never published unsigned.

Users with write access can start these jobs again on an existing tag: re-run a run, or
`gh workflow run el8.yml --ref vX.Y.Z`. That runs the tag's own workflow, which only the admin
role can tag (§5.2), and signs that tag's files again.

### 5.4 Changing keys

- **A new pair** comes from `fsbpyr key new` or from the script; both write the same files.
- **Every reader must find a pinned key.** The sums of release N are read by the fsbpyr of N-1
  (self update, and the EL8 sums in stubs.py:135) and by N's own fsbpyr (dispatch, the EL8
  stubs). So N's signature must pass both N-1's pins and N's. The sign job checks both (§5.3).
- **A planned change of K** takes two releases:
  1. Release N pins the old K, the new K and R. CI still holds the old K and signs N and its EL8
     companion.
  2. Once both are published, the admin puts the new K into the environment's secrets.
  3. Release N+1 drops the old K and is signed by the new one.
- **A new R** replaces the old one in the next release's pins. K signs that release as usual.
- **K lost or stolen:** release N pins a new K and R, and drops the old K at once. Only R is
  pinned by both N-1 and N, so R signs both of N's sums files, by hand:
  1. The admin removes K's secrets and sets the repository variable `FSBPYR_RECOVERY=1`.
     release.yml and el8.yml then skip `sign`, and they publish drafts.
  2. Anyone with write access can replace a draft's files. So before signing, the admin downloads
     the draft's sums file and checks that the tag's run made it:
     ```
     gh attestation verify SHA256SUMS --repo devitops-com/fsbpyr \
         --signer-workflow devitops-com/fsbpyr/.github/workflows/release.yml \
         --source-ref refs/tags/vX.Y.Z
     ```
     For the companion: `SHA256SUMS-glibc2.28` and el8.yml (el8.yml attests it today).
  3. The admin runs `release-signing.sh sign ~/release-r.key SHA256SUMS`, then `verify` against
     both sets of pins as the sign job does. Then the admin uploads the `.sig` and publishes the
     draft.
  4. The same for the companion. el8.yml waits up to an hour for the main release to be
     published, otherwise it is dispatched again on the tag. A re-run of el8.yml deletes a draft
     companion and makes it again (el8.yml:703-708), so it is not re-run while its draft waits.
  5. The admin puts the new K into the environment's secrets and removes `FSBPYR_RECOVERY`.
     Release N+1 is signed by the new K.
- **A client that skipped the release that changed K** refuses every later release. It cannot tell
  a key change from a forgery, and the release that changed the keys is signed by no key it
  trusts. So the message names both causes and no version: "release vX.Y.Z is not signed by a key
  this fsbpyr pins: either the release keys changed in a release this fsbpyr did not install, or
  the download is not genuine. Nothing was changed; see <User-Installing URL>#release-keys". That
  wiki section lists each key change with its release and the new fingerprints.
- **R lost:** the next release pins a new R and is signed by K.
- **Both lost:** users reinstall.
- **Limit:** a stolen K works against every client that still pins it, until that client updates
  past the release that dropped it.

### 5.5 fsbpyr's own checks, mandatory from the first signed release

All of them run in C, against the pinned keys, with subject `fsbpyr`.
- **`self update`:**
  1. Fetch the latest release's `SHA256SUMS` and `SHA256SUMS.sig`, and check them.
  2. Download the `.xz` to a temporary file, with a size cap. Its SHA-256 must equal its line
     before lzma sees it. Today the `.xz` streams into lzma (selfupdate.py:152), so this is a
     restructure.
  3. Unpack it. The build info's `release_asset` must equal its own, and the version must be newer
     than its own.
  4. Continue as today: `inspect --verify`, then the rename.

  A release without `SHA256SUMS.sig` gives "release vX.Y.Z is not signed; nothing was changed",
  with no fallback. A release signed by no pinned key gives the message of §5.4.
- **dispatch**, the other Python variants (dispatch.py:54): the same steps at its own tag, with a
  version equal to its own.
- **EL8 stubs** (runtime/stubs.py:160–171):
  - `SHA256SUMS-glibc2.28` and its `.sig` are checked before any line of it is used.
  - Today the file lists only the unpacked stubs (vcs.py:138), and the stub's `.xz` streams into
    lzma (stubs.py:178). el8.yml now lists each `.xz` too. fsbpyr downloads the `.xz` to a
    temporary file, with a size cap, and checks it against its line before lzma sees it, as
    `self update` does.
  - Older fsbpyr are not disturbed by the extra lines: `_published` looks a stub up by its name,
    and `refused_after_update` only collects digests.
  - `refused_after_update` (stubs.py:135), which the old fsbpyr runs during self update, checks
    the new tag's EL8 sums against the old fsbpyr's pins. It only warns, and a failed check counts
    as "unknown", as an unreadable file does today.
- **Cached files** are not checked again. D31 only detects damage, so it is no defence. The
  assumption is that whoever can write the user's fsbpyr cache can also replace fsbpyr itself.
  Stubs that the user places beside fsbpyr are the user's choice and are checked by runtime id
  only, as today.
- **Releases before the first signed one** stay unsigned. The first update from one of them runs
  old code and is not checked.

### 5.6 `make release-check`

`tools/release/check-release.sh` gains these checks. A failure is a FAIL like any other.
- `SHA256SUMS.sig` in the release's asset set, and `SHA256SUMS-glibc2.28.sig` in the companion's.
  The checks that exist change with them:
  - "SHA256SUMS lists every other asset" and the check of GitHub's digests exempt only
    `SHA256SUMS` today (check-release.sh:170, :179). They exempt `SHA256SUMS.sig` too.
  - The companion's exact set of assets gains `SHA256SUMS-glibc2.28.sig`.
  - Its `.xz` lines are checked like its other lines.
- `release-signing.sh verify` of both sums files against the keys the previous release pins and
  against the release's own, as the sign job does (§5.3). For the first signed release, against
  its own only.
- `fsbpyr verify` of each executable with the released fsbpyr. This checks fsbpyr's reader against
  the script's files.
- `openssl` 3.5 or newer in its tool list.

## 6. Apps

**Publisher:**
1. Run `fsbpyr key new myapp` once. Keep `myapp.key`, and publish `myapp.pub` and its fingerprint
   wherever they choose.
2. Build with `FSBPYR_SIGNING_KEY=~/keys/myapp.key fsbpyr build`. It asks for the passphrase
   after the smoke test and writes `dist/myapp` and `dist/myapp.sig`. Publish both.
3. In CI, keep the key out of the job that builds: build without a key, then sign in a separate job
   with a released fsbpyr (`fsbpyr sign dist/myapp`).

**User:**
1. Get `myapp.pub` and its fingerprint from the publisher, then run
   `fsbpyr key trust myapp myapp.pub SHA256:…` once.
2. Download `myapp` and `myapp.sig`, then run `fsbpyr verify --project myapp myapp`.
   `--project` makes sure that the file is myapp's, and not a genuine file of another project
   whose key the user also trusts.

**Out of scope:** how publishers hand out and change their keys, how users keep them, and whether
users check at all.

**EL8** (`--glibc 2.28` apps): fsbpyr itself needs glibc 2.34, so users check on another machine
and copy the checked file.

## 7. Wiki

- **User-Signing** (new):
  - §6 for publishers and users, with the messages table, always with `verify --project`;
  - checking fsbpyr's own downloads: `fsbpyr verify --project fsbpyr
    fsbpyr-3.14-x86_64-linux-gnu` with `SHA256SUMS` and `SHA256SUMS.sig` beside it;
  - EL8: check on another machine.
- **User-Building, User-Settings:** `--signing-key`, `FSBPYR_SIGNING_KEY`,
  `FSBPYR_SIGNING_PASSPHRASE`, the warning, and that the passphrase is kept from the build's
  processes.
- **User-Installing, User-Other-Commands:** updates are checked, and the new commands.
  User-Installing gains the section "release keys": the fingerprints of the pinned keys, and each
  key change with its release (§5.4).
- **Dev-Repository:** §5.2–5.4 and `FSBPYR_RECOVERY`.
- **Dev-Security:** what a pass means, and §8.

## 8. Threats

| Threat | Result |
|---|---|
| File changed after signing | **Stopped**: its SHA-256 is signed |
| A signature for project A presented as project B's | **Stopped**: the project is in the statement |
| A genuine file of another trusted project, served under this app's name | **Stopped** with `verify --project` (exit 1). Without it, `verify` passes and prints the other project's name |
| `.sig` removed | fsbpyr's own paths refuse. For apps, `NOT VERIFIED: no myapp.sig`; users must expect a signature |
| Ed25519 broken, by a quantum computer for instance | **Stopped**: ML-DSA-65 must pass too |
| A flaw in the ML-DSA-65 code | **Stopped**: Ed25519 must pass too |
| The first fsbpyr download tampered with | **Not stopped**: it is trusted as GitHub serves it (owner decision) |
| Malicious mirror or TLS interception, for updates | **Stopped** |
| Updates withheld | **Not stopped** |
| Rollback of fsbpyr through an update | **Stopped**: the version must be newer |
| Rollback of apps | **Not stopped** |
| Malicious workflow file at a tag | Gets a signed release and can copy K, as with any key in CI. Only the workflows of tags made by the admin role reach K (§5.2); users with write access can only run those again (§5.3) |
| Poisoned build | Gets a signed release. K is never in a job that runs built code |
| CI secret holding the wrong key | **Stopped**: the sign job checks against the pins of the previous release and of this one (§5.3) |
| Stolen K | **Partly**: R signs the key change; clients that do not update stay exposed |
| A draft's files replaced before R signs them, in recovery | **Stopped**: the admin checks the sums file's attestation first (§5.4) |
| The passphrase read by code that runs during `build` | **Stopped**: it is removed from the environment, and typed after the smoke test. That code can still read the key file (§3.1) |
| fsbpyr's cache, or stubs placed beside fsbpyr, changed by someone who can write them | **Not stopped**: they can replace fsbpyr itself (§5.5) |
| An app key handed out by an attacker | Out of scope (publishers and users) |

## 9. Performance and size

| Item | Value |
|---|---|
| Checking a 30 MB file | One SHA-256 pass plus two signature checks over a statement of 101–164 bytes. The script took 0.04 s in all; `sha256sum` alone 0.01 s. In C [A: about the same] |
| Signing a 30 MB file with the script | 0.30 s, mostly opening the key: four key decryptions at 600,000 PBKDF2 iterations |
| `.sig` | 3373 bytes |
| `.pub`, `.key` | 2839 and 6088 bytes |
| Per release | one more asset (`SHA256SUMS.sig`), and one more for the EL8 companion |
| App start | unchanged |

## 10. Decided, 2026-10-29

- Signatures are detached. Users who want to check keep them themselves.
- Ed25519 and ML-DSA-65, both always: "it can't hurt us and it does not cost us".
- The crypto is in `_fsbpyr` C, with the bundled OpenSSL. There is no standalone check script for
  users.
- The first fsbpyr install trusts the GitHub release. App users who want to check download a
  released fsbpyr. Checking is optional.
- EL8 users check on another machine: too few users to justify more.
- The wiki explains checking with fsbpyr, not with external tools.
- Each release signs one `SHA256SUMS`.
- One K and one R. A new pair comes from `fsbpyr key new` or from the script.
- Signatures are mandatory in fsbpyr's own download paths.
- Commands are `key new`, `key trust`, `sign` and `verify`. `build` signs as soon as a key is set.
  The project comes from build info, otherwise from `--project`.
- Private keys are kept by the admin or the publisher, outside the project's scope. App key
  management is out of scope too.
- The statement carries SHA-256, not D31. `sign` takes any file, and `SHA256SUMS` has no D31.
- CI signs every release with the openssl script. The job holding K runs no fsbpyr.
- For fsbpyr, a signing failure stops the release, and `make release-check` checks the signature.
  For apps, a key that cannot be used gives a warning and an unsigned build.
- The names in §2 and §3.

After the review, the same day, the owner approved its fixes, among them three changes to the
design:
- a planned change of K pins three keys for one release (§5.4);
- a `verify --project` that differs from the file's project is NOT VERIFIED (§3);
- `SHA256SUMS-glibc2.28` lists each stub's `.xz` too (§5.5).

## 11. To test

- The four C functions against the script's files, in both directions, under the sanitized stubs,
  and the size they add per stub.
- EVP ML-DSA-65 on aarch64 [A]. The C tests ran on x86-64 only.
- The environment's tag rule: a job naming `release-signing` on a pushed branch, including a branch
  named `v…`, is refused [A].
- `gh secret set` asking for the value [A].
- The `sign` job in the pinned Debian 13 image, with `openssl` installed, and its two checks
  against the pins.
- `publish`'s condition [A]: a `sign` that failed, or was skipped without `FSBPYR_RECOVERY`,
  publishes nothing; in recovery, `publish` makes only a draft.
- A planned change of K and a recovery, end to end, on a test repository [A].
- Tests that need pinned keys build fsbpyr with test keys in its src/fsbpyr/release-keys/, as the
  e2e tests already build fsbpyr from a copy of the checkout (tests/e2e/test_dispatch.py). There
  is never an environment variable or option that changes the pins.
- The script in the test suite. It needs the OpenSSL 3.5 command where the suite runs, and the suite
  has no skips. The Ubuntu 24.04 image has 3.0.13 (measured), and GitHub's `ubuntu-24.04` runner
  probably has the same [A], so its test runs in the Debian 13 image there.
- shellcheck on the script. It was not installed where the script was written.

So far the script was run on the Debian 13 host (OpenSSL 3.5.7) for these cases:
- keys made, with modes 600 and 644;
- nothing replaced: key, `.pub` and `.sig`;
- signed, and checked against K; refused against R; with K and R, passed by R;
- signed with R: `sign` passes, `verify` against K alone refuses (the sign job's check);
- a changed file, a flipped byte in either half, a 64-byte `.sig` and a missing `.sig`: all
  refused;
- a `.pub` with an extra line, with CRLF line ends, or with its keys swapped: refused. Given
  alone, exit 2. Followed by a good `.pub`, that one is still tried: exit 0 when it matches,
  exit 1 when it does not;
- a wrong passphrase, and a missing passphrase without a terminal: refused, nothing written;
- the passphrase typed on a terminal (a pty): signed; two different passphrases for a new key:
  refused, nothing written. Leading and trailing spaces are kept, as typed and as in the
  variable;
- a temporary directory that cannot be made, and a key file of junk: exit 2, not 1.

The cases that came with the review fail with the script before it: the VERIFIED line without the
project, a bad `.pub` that stopped `verify`, exit 1 when `mktemp` fails, and typed passphrases
that lost their spaces.

## 12. Implementation outline

1. **C.** `sig.c` with the four functions. A probe with fixed vectors made by the script, plus
   negative ones: each half flipped, sizes 64 and 3372, keys swapped, extra PEM content.
2. **Python.** `signing.py` for the statement, the trust store and the messages; the `key`, `sign`
   and `verify` commands; signing in `build`, with the signing variables removed from the
   environment at start; their tests.
3. **Download paths.** `self update`, dispatch and the EL8 stubs download the `.xz` and check it
   before unpacking.
4. **Release.** The pinned keys; the `prepare`/`sign`/`publish` split with its permissions and
   its condition; el8.yml with the `.xz` lines; `FSBPYR_RECOVERY` in both workflows;
   check-release.sh and its tests.
5. **The rest.** The script's test; the wiki (§7); a CHANGELOG "Added" entry, so the first signed
   release is the next minor; the man page; the security docs.
