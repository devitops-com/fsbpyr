# Signed fsbpyr releases and signed apps: concept

Status: concept, 2026-10-29, at fadc3d9. It replaces the earlier concept on branch
`signing-concept` (SSHSIG signatures embedded in the executable), which is dropped. Implemented so
far: `tools/release/release-signing.sh` only.

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
  - fsbpyr pins K and the offline recovery key R.
  - A release that cannot be signed is not published.
- **Apps.** `fsbpyr build` signs when a key is set. If the key cannot be used, it prints a warning
  and builds unsigned. Users check with `fsbpyr verify`. How publishers hand out their keys, and
  how users keep them, is out of scope.
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
| `fsbpyr verify FILE [--project P]` | Checks FILE and prints `VERIFIED` or `NOT VERIFIED`. |

Rules shared by the commands:
- **Project.**
  - For an executable that fsbpyr built, the name comes from its build info.
  - For other files, `--project` is required: "FILE has no build info: name its project with
    --project".
  - A `--project` that differs from the build info is an error.
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
| `VERIFIED  myapp  (Ed25519 + ML-DSA-65, key SHA256:…)` | 0 |
| `NOT VERIFIED: myapp: the signature does not match the trusted key of myapp` | 1 |
| `NOT VERIFIED: myapp: no myapp.sig, and no signed SHA256SUMS beside it lists it` | 1 |
| `NOT VERIFIED: myapp: myapp.sig is not a signature (N bytes)` | 1 |
| `no trusted key for myapp: get the publisher's key and its fingerprint, then: fsbpyr key trust myapp KEY.pub SHA256:…` | 2 |

A pass means that the holder of the trusted key signed exactly this file, as this project. It
does not mean that the app is safe, that it is the newest version, or that the key belongs to whom
the user thinks.

### 3.1 `fsbpyr build`

- **A signing key is set** (`--signing-key` or `FSBPYR_SIGNING_KEY`): after the smoke test, the
  build signs the output as `fsbpyr sign` does and writes `dist/<name>.sig`.
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
- **Stale signatures:** when the build replaces `dist/<name>`, it removes an old `dist/<name>.sig`,
  so an old signature never sits next to a new build.
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

- **K**, the release key, exists only in CI, as a secret of the GitHub environment
  `release-signing`.
- **R**, the recovery key, is kept offline by the admin.
- fsbpyr pins both, as src/fsbpyr/release-keys/k.pub and r.pub (new), packaged into fsbpyr. Either
  key may sign a release.
- Keeping the private key files and their passphrases is the admin's job. fsbpyr stores no private
  key.

### 5.2 One-time setup

1. **Make the keys** on a machine with OpenSSL 3.5 or newer (Debian 13 has 3.5.7). Each command
   asks for its own passphrase:
   ```
   bash tools/release/release-signing.sh new-key ~/release-k
   bash tools/release/release-signing.sh new-key ~/release-r
   ```
   Keep `release-r.key` and its passphrase offline.
2. **Give K to the environment.** `gh secret set` asks for each value [A]:
   ```
   gh secret set FSBPYR_RELEASE_KEY --env release-signing < ~/release-k.key
   gh secret set FSBPYR_SIGNING_PASSPHRASE --env release-signing
   ```
3. **Restrict the environment.** `release-signing` gets one deployment rule: the tag pattern `v*`,
   as a tag rule rather than a branch rule [A]. Only tag runs then get K. The rulesets of
   2026-10-29 stay:
   - `release tag creation`: only the admin role creates `v*` and `el8-v*` tags;
   - `release tags`: nobody updates or deletes them.
4. **Pin the keys.** Copy `release-k.pub` and `release-r.pub` to src/fsbpyr/release-keys/k.pub
   and r.pub, and commit them with the release that brings signing.
5. **Release.** The first signed release goes through §5.3 like every other. Its signature is
   checked against its own keys, because no earlier release pins any (§5.6).

### 5.3 Release pipeline

| Job | Change |
|---|---|
| `build`, `sbom` | none |
| `prepare` (new) | The first half of today's `publish`: `make-install.sh`, then `SHA256SUMS` as written today (release.yml:141). It uploads both. It gets no secret. |
| `sign` (new) | Has `environment: release-signing`. Runs in a Debian 13 image pinned by digest, with `openssl` installed: the image has the library but not the command (measured). Checks out the tagged commit for the script. Writes K from the secret into `$RUNNER_TEMP`, runs `release-signing.sh sign`, which checks its own signature, and removes the key. Runs no fsbpyr, no built code and no third-party action. |
| `publish` | `gh release create` as today (release.yml:143), with `SHA256SUMS.sig` added. |
| el8.yml | The same `sign` job for `SHA256SUMS-glibc2.28` (written at el8.yml:669). Its `publish` adds `SHA256SUMS-glibc2.28.sig`. |

Any failure stops the release, so fsbpyr is never published unsigned.

### 5.4 Changing keys

- **A new pair** comes from `fsbpyr key new` or from the script; both write the same files.
- **The release that pins a new pair** is signed by a key the previous release pins: K normally,
  R when K is lost or stolen. Clients accept that release and pin the new keys once they run it.
  After it is published, replace the CI secret.
- **Signing with R.** R never enters CI:
  - with the repository variable `FSBPYR_RECOVERY=1`, release.yml skips `sign` and `publish`
    makes a draft;
  - the admin downloads the draft's `SHA256SUMS`, runs `release-signing.sh sign ~/release-r.key
    SHA256SUMS`, uploads the `.sig` and publishes the draft.
- **Skipped key-change release.** A client that skipped that release refuses one signed only by the
  new key, with: "release vX.Y.Z is signed by a key this fsbpyr does not pin (the release keys
  changed in vA.B.C): reinstall from <User-Installing URL>".
- **R lost:** the next release pins a new R and is signed by K.
- **Both lost:** users reinstall.
- **Limit:** a stolen K works against every client that still pins it, until that client updates
  past the key-change release.

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
  with no fallback.
- **dispatch**, the other Python variants (dispatch.py:54): the same steps at its own tag, with a
  version equal to its own.
- **EL8 stubs** (runtime/stubs.py:160–171): `SHA256SUMS-glibc2.28` and its `.sig` are checked before
  any line of it is used.
- **Cached files** are not checked again: their launcher checks D31 at every start.
- **Releases up to 0.28.x** stay unsigned. The first update from one of them runs old code and is
  not checked.

### 5.6 `make release-check`

`tools/release/check-release.sh` gains these checks. A failure is a FAIL like any other.
- `SHA256SUMS.sig` in the release's asset set, and `SHA256SUMS-glibc2.28.sig` in the companion's.
- `release-signing.sh verify SHA256SUMS <keys>` with the keys the previous release pins, because
  those are what clients check with. For the first signed release, its own keys are used. The same
  applies to `SHA256SUMS-glibc2.28`.
- `fsbpyr verify` of each executable with the released fsbpyr. This checks fsbpyr's reader against
  the script's files.
- `openssl` 3.5 or newer in its tool list.

## 6. Apps

**Publisher:**
1. Run `fsbpyr key new myapp` once. Keep `myapp.key`, and publish `myapp.pub` and its fingerprint
   wherever they choose.
2. Build with `FSBPYR_SIGNING_KEY=~/keys/myapp.key fsbpyr build`, which writes `dist/myapp` and
   `dist/myapp.sig`. Publish both.
3. In CI, keep the key out of the job that builds: build without a key, then sign in a separate job
   with a released fsbpyr (`fsbpyr sign dist/myapp`).

**User:**
1. Get `myapp.pub` and its fingerprint from the publisher, then run
   `fsbpyr key trust myapp myapp.pub SHA256:…` once.
2. Download `myapp` and `myapp.sig`, then run `fsbpyr verify myapp`.

**Out of scope:** how publishers hand out and change their keys, how users keep them, and whether
users check at all.

**EL8** (`--glibc 2.28` apps): fsbpyr itself needs glibc 2.34, so users check on another machine
and copy the checked file.

## 7. Wiki

- **User-Signing** (new):
  - §6 for publishers and users, with the messages table;
  - checking fsbpyr's own downloads: `fsbpyr verify fsbpyr-3.14-x86_64-linux-gnu` with
    `SHA256SUMS` and `SHA256SUMS.sig` beside it;
  - EL8: check on another machine.
- **User-Building, User-Settings:** `--signing-key`, `FSBPYR_SIGNING_KEY`,
  `FSBPYR_SIGNING_PASSPHRASE`, the warning.
- **User-Installing, User-Other-Commands:** updates are checked, and the new commands.
- **Dev-Repository:** §5.2–5.4 and `FSBPYR_RECOVERY`.
- **Dev-Security:** what a pass means, and §8.

## 8. Threats

| Threat | Result |
|---|---|
| File changed after signing | **Stopped**: its SHA-256 is signed |
| A signature for project A presented as project B's | **Stopped**: the project is in the statement |
| `.sig` removed | fsbpyr's own paths refuse. For apps, `NOT VERIFIED: no myapp.sig`; users must expect a signature |
| Ed25519 broken, by a quantum computer for instance | **Stopped**: ML-DSA-65 must pass too |
| A flaw in the ML-DSA-65 code | **Stopped**: Ed25519 must pass too |
| The first fsbpyr download tampered with | **Not stopped**: it is trusted as GitHub serves it (owner decision) |
| Malicious mirror or TLS interception, for updates | **Stopped** |
| Updates withheld | **Not stopped** |
| Rollback of fsbpyr through an update | **Stopped**: the version must be newer |
| Rollback of apps | **Not stopped** |
| Malicious workflow file at a tag | Gets a signed release and can copy K, as with any key in CI. Only tags made by the admin role reach K (§5.2) |
| Poisoned build | Gets a signed release. K is never in a job that runs built code |
| Stolen K | **Partly**: R signs the key change; clients that do not update stay exposed |
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

## 11. To test

- The four C functions against the script's files, in both directions, under the sanitized stubs,
  and the size they add per stub.
- EVP ML-DSA-65 on aarch64 [A]. The C tests ran on x86-64 only.
- The environment's tag rule: a job naming `release-signing` on a pushed branch, including a branch
  named `v…`, is refused [A].
- `gh secret set` asking for the value [A].
- The `sign` job in the pinned Debian 13 image, with `openssl` installed.
- The script in the test suite. It needs the OpenSSL 3.5 command where the suite runs, and the suite
  has no skips. The Ubuntu 24.04 image has 3.0.13 (measured), and GitHub's `ubuntu-24.04` runner
  probably has the same [A], so its test runs in the Debian 13 image there.
- shellcheck on the script. It was not installed where the script was written.

So far the script was run on the Debian 13 host (OpenSSL 3.5.7) for these cases:
- keys made, with modes 600 and 644;
- nothing replaced: key, `.pub` and `.sig`;
- signed, and checked against K; refused against R; with K and R, passed by R;
- a changed file, a flipped byte in either half, a 64-byte `.sig` and a missing `.sig`: all
  refused;
- a `.pub` with an extra line, or with its keys swapped: refused;
- a wrong passphrase, and a missing passphrase without a terminal: refused, nothing written;
- the passphrase typed on a terminal (a pty): signed; two different passphrases for a new key:
  refused, nothing written.

## 12. Implementation outline

1. **C.** `sig.c` with the four functions. A probe with fixed vectors made by the script, plus
   negative ones: each half flipped, sizes 64 and 3372, keys swapped, extra PEM content.
2. **Python.** `signing.py` for the statement, the trust store and the messages; the `key`, `sign`
   and `verify` commands; signing in `build`; their tests.
3. **Download paths.** `self update` downloads before unpacking; dispatch; stubs.
4. **Release.** The pinned keys; the `prepare`/`sign`/`publish` split; el8.yml;
   `FSBPYR_RECOVERY`; check-release.sh and its tests.
5. **The rest.** The script's test; the wiki (§7); a CHANGELOG "Added" entry, so the first signed
   release is the next minor; the man page; the security docs.
