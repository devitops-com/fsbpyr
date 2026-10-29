# Signed fsbpyr and signed apps: concept

Status: concept for the owner, 2026-10-29, at fadc3d9. Nothing is implemented. This is the third
version. It went through:
- a design panel;
- three reviews (attacker, owner alignment, usability);
- a review against the code;
- runs of every command on the 14 supported distributions;
- a final round of fact-check, attacker and critic.

Facts without a mark were run or read while the concept was made; the runs and the review
reports stayed in the session's scratch space and are not part of the repository. **[A]** marks an
assumption nobody has checked; §12 lists what to test before building.

## 1. Summary

- **What is signed.** The D31 digest that the build already wrote into the executable's trailer.
  Nothing is hashed to make a signature. The digest goes into a short text *statement* (format,
  project, version, digest), and the statement is signed. This holds for fsbpyr's executables and
  for apps.
- **Scheme.** OpenSSH signatures (SSHSIG) with `ssh-ed25519` or `sk-ssh-ed25519` keys.
  - fsbpyr never holds a private key: the signer's own `ssh-keygen` signs, with agent, passphrase
    or FIDO token.
  - Every distribution fsbpyr runs on has OpenSSH 8.7 or later.
- **Where it lives.** Inside the executable. The statement, the signature and a 64-byte footer
  are appended after the trailer, and a new trailer covers them.
  - Every existing launcher, every reader since 0.21.0 and 0.26.0's `self update` accept that
    layout (tested; §4.3).
  - The file carries its signature everywhere it is copied.
  - Release files that are not executables are covered by a signed `SHA256SUMS`: `.xz`,
    `install.sh`, SBOMs, licences, and the EL8 stubs in their companion release.
- **Who signs.**
  - fsbpyr releases: a CI key in a protected GitHub environment, plus an offline recovery key.
    No step is added per release.
  - Apps: the builder, by setting `signing-key`; `fsbpyr build` then signs.
- **Who verifies, and with what.**
  - **Anyone, without fsbpyr:** they need the publisher's key, saved once, and `fsbpyr-verify.sh`,
    a POSIX shell script of under 100 lines that is an asset of every fsbpyr release.
    - On the 12 modern distributions it needs only OpenSSH and coreutils:
      `sh fsbpyr-verify.sh ~/.config/fsbpyr-keys/myapp myapp`.
    - EL8 lacks `ssh-keygen -Y`. There it can use EL8's own libcrypto through platform-python
      (decision 2).
  - **fsbpyr itself, in launcher C:** `self update`, the download of other Python variants and of
    EL8 stubs, and `inspect --verify`. A download costs one SHA-256 pass over the `.xz` before it is
    unpacked (about 3 ms for 15 MB) plus one Ed25519 check (0.3–0.7 ms). Nothing is checked twice.
  - **A built app does not check its own signature** (§6.4).

## 2. Your points, and where the concept answers them

| You said | Answer |
|---|---|
| "we do crypto in C launcher code only. Python is way too slow" | All of fsbpyr's checks hash and verify in launcher C, through `_fsbpyr`; no hashlib call sits on a signature path (§6.1). Signing is done by the user's `ssh-keygen`. |
| "why would signing be so slow if almost everything is C code anyway?" | The slow part was a single-threaded SHA-512 over the whole file. Here the signed message is the short statement. |
| "of course we should sign already computed digest" | The statement carries the inner trailer's digest as the build wrote it (§4.1). For a signed file the launcher checks the outer trailer, which covers that digest and the block; `inspect` shows both. |
| "launcher already checks digest at every start, so why should we do that again?" | Each file is checked once, when it arrives. The launcher's start check is never repeated, and cached files are not checked again (§6.3). |
| "download signing key independent of fsbpyr or app … small shell script available as release asset" | That is the third-party flow (§3.3, §5): the key is saved once from the publisher's repository or website, and the script is fsbpyr's release asset. |
| "I dont see how built app could reliably check itself" | Agreed; the reasons are in §6.4. |
| "add cryptographic signature to the fsbpyr binary" | The signature is in the binary. Decision 1 shows the detached alternative at its best. |

## 3. The three parties' flows

### 3.1 fsbpyr maintainer

One time, in this order:
1. Create the environment `release-signing`, with deployments from tags `v*` only.
2. Add **restrict creations** to the tag ruleset `release tags`, with the repository admin role
   (today only you) as the only bypass. Today any credential with write access can create a
   `v*` tag, and with that run a job in the environment (§8).
3. Make the release key K. It exists only in memory and in the secret:
   ```
   d=$(mktemp -d -p /dev/shm); trap 'rm -rf "$d"' EXIT
   ssh-keygen -q -t ed25519 -N '' -C "fsbpyr release key 1" -f "$d/k"
   gh secret set FSBPYR_RELEASE_KEY --env release-signing < "$d/k"
   printf 'fsbpyr namespaces="fsbpyr-release@devitops.com" %s\n' "$(cat "$d/k.pub")" >> src/fsbpyr/allowed_signers
   ```
4. Make the recovery key R as a resident key on a FIDO token kept offline. `ssh-keygen -K`
   recovers its handle file; keep a copy of the handle file with the token:
   ```
   ssh-keygen -t ed25519-sk -O resident -O verify-required -O application=ssh:fsbpyr-release -C "fsbpyr recovery key" -f fsbpyr_recovery
   printf 'fsbpyr namespaces="fsbpyr-release@devitops.com" %s\n' "$(cat fsbpyr_recovery.pub)" >> src/fsbpyr/allowed_signers
   ```
5. Commit src/fsbpyr/allowed_signers (new). It is packaged into fsbpyr as the pinned keys.
6. Publish both fingerprints and the SHA-256 of `fsbpyr-verify.sh` on a devitops.com page, which
   is outside GitHub. Get the fingerprints with
   `cut -d' ' -f3- src/fsbpyr/allowed_signers | ssh-keygen -lf -`.

Per release nothing changes: `make release-prep`, PR, full suite, `make release`. release.yml
builds, signs, embeds and publishes (§7), and `make release-check` also checks every signature.
When `fsbpyr-verify.sh` changed, add its new SHA-256 to the devitops.com page.

After K is lost or stolen (§6.5):
1. Replace the secret with K2 and commit the new `allowed_signers`.
2. Tag with the variable `FSBPYR_RECOVERY=1` set. release.yml then makes a draft.
3. `make release-recover` signs that draft's `SHA256SUMS` with R (PIN and touch, once), uploads
   the `.sig` and publishes.

### 3.2 App builder

**Check fsbpyr before using it** (goal point 1). Check the key file and the script against the
devitops.com page before running anything:
```
v=v0.29.0 d=https://github.com/devitops-com/fsbpyr/releases/download/$v
mkdir -p ~/.config/fsbpyr-keys
curl -LsSfo ~/.config/fsbpyr-keys/fsbpyr https://raw.githubusercontent.com/devitops-com/fsbpyr/$v/src/fsbpyr/allowed_signers
curl -LsSfO "$d/fsbpyr-verify.sh" -O "$d/fsbpyr-3.14-x86_64-linux-gnu"
cut -d' ' -f3- ~/.config/fsbpyr-keys/fsbpyr | ssh-keygen -lf -   # compare with devitops.com
sha256sum fsbpyr-verify.sh                                        # compare with devitops.com
sh fsbpyr-verify.sh ~/.config/fsbpyr-keys/fsbpyr fsbpyr-3.14-x86_64-linux-gnu
```
- The CHANGELOG and the wiki come from the same GitHub repository as the release. Only the
  devitops.com page is an independent second channel.
- An installed fsbpyr checks the same way, under any name:
  `sh fsbpyr-verify.sh ~/.config/fsbpyr-keys/fsbpyr ~/.local/bin/fsbpyr`.
- To use `install.sh`, download `SHA256SUMS` and `SHA256SUMS.sig` beside it, check it with the
  script (it is listed in the signed sums), then run `sh install.sh`.
- After that, `fsbpyr self update` checks every update itself.

**Sign locally.**
1. A key. An existing `~/.ssh/id_ed25519` in ssh-agent is the low-friction choice; the namespace
   keeps app signatures apart from logins. A FIDO key (`ssh-keygen -t ed25519-sk`) is stronger,
   but every signature then needs a touch, and a PIN with `verify-required`.
2. `pyproject.toml`: `[tool.fsbpyr] signing-key = "allowed_signers"`, a committed file listing
   every key that may sign the project (laptop, CI). It may also hold key text or name a
   committed `.pub`. A `~` or an absolute path is a ConfigError: "commit the key into the
   project, or paste the key text". On the first run without the file, fsbpyr prints the exact
   line to commit, built from the key it found.
3. `fsbpyr build`:
   - signs after the smoke test;
   - checks the signature with `ssh-keygen -Y verify`;
   - only then replaces the output;
   - prints what the builder publishes:
   ```
   fsbpyr: signed dist/myapp (myapp 1.4.2) with ED25519 key SHA256:Wm3…
     publish dist/myapp. Your users check it with your key and fsbpyr's script:
       mkdir -p ~/.config/fsbpyr-keys
       curl -LsSfo ~/.config/fsbpyr-keys/myapp https://raw.githubusercontent.com/OWNER/REPO/main/allowed_signers
       curl -LsSfO https://github.com/devitops-com/fsbpyr/releases/download/v0.29.0/fsbpyr-verify.sh
       sh fsbpyr-verify.sh ~/.config/fsbpyr-keys/myapp myapp
     EL8 (Rocky/Alma 8): see https://github.com/devitops-com/fsbpyr/wiki/User-Verifying#el8
   ```

**Options**, following cli.py's conventions:

| CLI | `[tool.fsbpyr]` | Meaning |
|---|---|---|
| `build --signing-key KEY` | `signing-key` | An `allowed_signers` file, key text or a `.pub`. The table's path is relative to the project, the CLI's to the cwd. |
| `build --sign/--no-sign` | — | Default None: sign when a listed key is usable, otherwise build unsigned and say so in one line. `--sign` makes signing mandatory (release builds). `--no-sign` writes the unsigned build and its statement plus the `ssh-keygen` line (CI). |
| `sign [--signing-key KEY] FILE` | — | Sign or re-sign a built file. An existing block is dropped first, and the inner D31 is checked in C first. |
| `sign --signature SIG FILE` | — | Embed a signature made elsewhere, such as the CI sign job, after checking it. |

**Finding the key, without guessing.**
- Keys are compared by type and base64 blob; comments are ignored.
- fsbpyr takes the first listed key it finds, in `ssh-add -L` first, then in `~/.ssh/*.pub`.
- fsbpyr writes the statement into a fresh 0700 directory and passes ssh-keygen that file, never
  stdin. Without a terminal, ssh-keygen would otherwise read the passphrase or PIN from stdin:
  that is, from the statement (measured).
- With the agent, fsbpyr signs through `-f` naming a copy of the `.pub` in that directory. It adds
  `-U` on OpenSSH 9.1 and later; without `-U`, 8.7 and 8.9 sign through the agent too (measured).
- With a private file, ssh-keygen asks on the terminal, or through askpass when a display is set.
  For sk keys fsbpyr first prints "touch the security key now".
- With no terminal and no agent, fsbpyr fails with "ssh-add the key first".

Failures:
- With `--sign` and no usable key: "none of the keys in allowed_signers is in ssh-agent or ~/.ssh
  (SHA256:…)"; nothing is built.
- RSA, ECDSA or certificates: "fsbpyr signs with Ed25519 keys: ssh-keygen -t ed25519".
- Subjects `fsbpyr` and `fsbpyr-el8-stubs` are reserved for fsbpyr's own release and refused.
- No ssh-keygen: "signing needs ssh-keygen: install openssh-client (Debian, Ubuntu),
  openssh-clients (SUSE, RHEL family) or openssh (Fedora, Arch)". fsbpyr installs nothing.

**Sign in CI.** The build job runs build backends and the smoke test, so it gets no secret.
1. **Build job:** `fsbpyr build --no-sign`. It uploads `dist/myapp` and `dist/myapp.statement`.
2. **Sign job.** It holds the key and runs only coreutils and `ssh-keygen`: no fsbpyr, no
   `curl | sh`, no built code. It rebuilds the expected statement itself and signs only if the
   uploaded one is byte-equal:
   ```
   printf '# fsbpyr-signature 1\n# subject myapp\n# version %s\n# d31 %s\n' "${TAG#v}" \
     "$(tail -c 32 dist/myapp | od -An -tx1 | tr -d ' \n')" > expected
   cmp expected dist/myapp.statement && ssh-keygen -Y sign -f "$k" -n myapp.fsbpyr-app@devitops.com dist/myapp.statement
   ssh-keygen -Y verify -f allowed_signers -I myapp -n myapp.fsbpyr-app@devitops.com -s dist/myapp.statement.sig < dist/myapp.statement
   ```
3. **Embed job.** No secret. `fsbpyr sign --signature dist/myapp.statement.sig dist/myapp`.

The wiki gives both jobs in full for GitHub Actions and GitLab CI, measured where noted:
- The secret is mapped with `env:` and checked to be non-empty.
- The key is written with `mktemp`, ending in a newline (a key without one is refused), and
  removed by `trap`.
- Tags `v*` are protected (GitLab), or their creation is restricted (GitHub).
- Private repositories on GitHub Free have no environments. They can use a repository secret with
  a tag-only `if:`, which loses the deployment rule.
- A CI key is its own key: add its line to `allowed_signers`. A login key never goes into CI.

### 3.3 Third party (goal point 2)

1. **Save the publisher's key once**, from their repository, their website or their GitHub SSH
   signing keys, never from the download page, to `~/.config/fsbpyr-keys/<project>`. Compare its
   fingerprint (`cut -d' ' -f3- FILE | ssh-keygen -lf -`) with a second place the publisher
   names. Keep it: a later file that fails with the saved key is either an attack or a key
   change, which the publisher has to confirm.
2. **Get `fsbpyr-verify.sh`** from fsbpyr's release, or from fsbpyr's repository at a tag. Check
   its SHA-256 against the devitops.com page. It is the same script for every app, and the app's
   publisher cannot change it.
3. **Run** `sh fsbpyr-verify.sh ~/.config/fsbpyr-keys/myapp myapp`.

| Output | Exit | Meaning |
|---|---|---|
| `VERIFIED  myapp 1.4.2  signed by ED25519 SHA256:…` | 0 | The holder of that key signed this executable as myapp 1.4.2 |
| `NOT VERIFIED: the signature does not match your key` | 1 | Wrong key, or a forged or replaced file: do not run it |
| `NOT VERIFIED: the file differs from the signed one` | 1 | Changed after signing |
| `NOT VERIFIED: the signature block is malformed` | 1 | Do not run it |
| `NOT VERIFIED: not signed` | 1 | No signature; ask the publisher |
| `CANNOT CHECK HERE: ssh-keygen cannot check signatures (OpenSSH before 8.2)` | 3 | Older OpenSSH; for EL8 see decision 2 |
| `CANNOT CHECK HERE: needs ssh-keygen: install …` | 3 | Tool missing |

**What a pass means:** the holder of a key in *your* saved file signed this executable as this
project and version. It does not mean the app is safe, that it is the newest version, or that
the key belongs to whom you think. A bare key (not the publisher's `allowed_signers` line)
accepts any project that key signed. The script then adds: "check that the name above is the one
you want".

The wiki keeps the reference recipe without the script, about 10 lines of sh, and a table that
maps each ssh-keygen message to its likely cause.

## 4. Formats

### 4.1 Statement

ASCII with LF line ends, ending in an LF, at most 4 KiB. These four lines and nothing else:
```
# fsbpyr-signature 1
# subject myapp
# version 1.4.2
# d31 3a5f…64 hex…c1
```
- `subject` is the PEP 503 normalized project name: `[a-z0-9-]`, at most 64 characters.
  `fsbpyr` and `fsbpyr-el8-stubs` are reserved.
- `version` is the project version, or `-` when there is none: `[0-9A-Za-z.+!_-]{1,64}`.
- `d31` is the inner trailer's digest in lowercase hex.
- A reader accepts a statement only if parsing it and writing it back gives the same bytes.

### 4.2 Signature, namespaces, principals

The signature is the armoured SSHSIG that `ssh-keygen -Y sign` writes, with hash sha512.

| Subject | Principal | Namespace |
|---|---|---|
| `fsbpyr`, `fsbpyr-el8-stubs` | `fsbpyr` | `fsbpyr-release@devitops.com` |
| any other (an app) | the subject | `<subject>.fsbpyr-app@devitops.com` |

The verify call is always
`ssh-keygen -Y verify -f KEYS -I <principal> -n <namespace> -s SIG < STATEMENT`.

The per-project namespace binds the project. The publisher's line names it
(`myapp namespaces="myapp.fsbpyr-app@devitops.com" ssh-ed25519 AAAA… comment`), so a key that
signs two projects cannot pass project A off as project B. With the script's call, the other
project gives "key is not permitted for use in signature namespace" or "Could not verify
signature." (tested on OpenSSH 10.0; 8.2 and 8.4 are in §12). Lines carry only `namespaces=`:
`valid-after` and `valid-before` break OpenSSH 8.2 and 8.4, and would invalidate old signatures.

fsbpyr's own parser is stricter than OpenSSH's:
- one armour block, at most 8 KiB, canonical base64, LF only;
- version 1, the namespace byte-equal, `reserved` empty, hash `sha512`;
- the public key byte-equal to a pinned key, and the pinned key's 32 bytes used for the check;
- the signature type equal to the key type: 64 bytes, plus flags and counter for sk keys;
- no trailing bytes at any level;
- S < L;
- for R, user presence and user verification set, which ssh-keygen does not check (measured on
  8.2 to 10.6).

Sizes measured: 322–407 B for ssh-ed25519, depending on the namespace length, and 379–403 B for
sk keys.

### 4.3 Embedded block ("absorb" layout)

```
| U: the unsigned executable, unchanged (stub | pad | payload | inner trailer) |
| statement | armoured signature | footer (64 B) | outer trailer (64 B) |
```
- **Footer.** 64 ASCII bytes: `FSBPYR-SIGNATURE 1 <U> <B>`.
  - U and B are decimal with no leading zeros. A leading zero would make POSIX `$((…))` read the
    number as octal (measured).
  - Spaces pad the footer, and it ends in an LF.
  - B is the length of statement, signature and footer together.
- **Outer trailer.** A normal v7 trailer:
  - the same `payload_offset`;
  - `payload_size` = the inner one + 64 + B;
  - the digest = the D31 of [0, S-32), where S is the file size.
- **Byte-exact block.** [U, S-64) must be exactly the statement, then the armour from
  `-----BEGIN SSH SIGNATURE-----` to `-----END SSH SIGNATURE-----` and its LF, then the footer.
  Nothing else may be there. Otherwise bytes after U could be changed and still give VERIFIED (the
  round-2 attack). The script and fsbpyr rebuild the block from its parts and compare.
- **Strict parse in the new launcher and reader.** When the 64 bytes before the outer trailer
  begin with the footer magic:
  - the footer must parse, with U + B + 64 = S;
  - a v7 inner trailer must sit at U-64, and the inner payload must end there;
  - its `payload_offset` must equal the outer one;
  - the outer `payload_size` must be the inner one + 64 + B.

  Everything is then read through the inner trailer. A mismatch exits 70 with "the signature block
  is damaged". This stops a second payload hidden in the block with the outer trailer pointed at
  it. Every signed file carries this launcher, because the launcher sits inside U, and
  `fsbpyr sign` refuses executables built by older releases.
- **Compatibility.** Old readers see a payload 64 + B bytes longer than its tables. Tested:
  - the 0.28.1 launcher;
  - `inspect --verify` of 0.26.0 and 0.28.1;
  - `self update` of 0.26.0.
  The readers of 0.21.0 to 0.28.1 are the same code (read, not run). The format version stays 7.
- **Signing hashes no data for the signature.** The statement takes the stored inner digest.
  Writing the outer trailer needs one D31 pass over the new file, which runs in C through
  `_tree_digest` (about 1.6 ms per 30 MB). `fsbpyr sign` gets the check of the inner digest from
  the same pass. That uses the leaf cut prototyped this session: one pass, +66–88 ns.
- `head -c U` of a signed file is the unsigned build, byte for byte. An unsigned build never ends
  in the footer magic: the writer asserts it.

### 4.4 Signed sums

`SHA256SUMS` keeps today's lines and gains three header lines:
```
# fsbpyr-sums 1
# subject fsbpyr
# version 0.29.0
<sha256>  <asset>          every asset, as today
```
- `SHA256SUMS.sig` is in the release namespace.
- `SHA256SUMS-glibc2.28` of the EL8 companion takes the same form, with subject
  `fsbpyr-el8-stubs`. It keeps its stub lines, which older fsbpyrs read, and adds lines for the
  8 `.xz` assets.
- GNU and uutils `sha256sum --strict -c` skip `#` lines on every version tested (GNU 8.30 to 9.12,
  uutils 0.10.0). BusyBox fails closed, and the script does not depend on it.

### 4.5 Key file

src/fsbpyr/allowed_signers (new) has one name everywhere: in the repository, in the payload and in
the recipes. It is not a release asset, so nobody takes the download page's copy as the anchor.
```
fsbpyr namespaces="fsbpyr-release@devitops.com" ssh-ed25519 AAAAC3Nz… fsbpyr release key 1
fsbpyr namespaces="fsbpyr-release@devitops.com" sk-ssh-ed25519@openssh.com AAAAGnNr… fsbpyr recovery key
```

## 5. `fsbpyr-verify.sh`

The script is in the repository at `tools/release/fsbpyr-verify.sh` and is an asset of every
release, listed in the signed `SHA256SUMS`. It is POSIX sh, under 100 lines, runs with
`LC_ALL=C`, needs no network and installs nothing.

**Usage:** `sh fsbpyr-verify.sh KEYS FILE…`
- `KEYS` is the publisher's `allowed_signers` file or line, a `.pub`, or a bare key.
- `FILE` is an executable with an embedded signature, or any file listed in a signed
  `SHA256SUMS` or `SHA256SUMS-glibc2.28` beside it.

**For an executable:**
1. Read the footer with `tail -c 128 | head -c 64` and `read`. Check that `U + B + 64 = S`.
2. Cut out the statement and the armour into temporary files with `head -c` and `tail -c`, never
   with `$(…)`, which would drop the final LF.
3. Parse the statement strictly.
4. Rebuild the block and `cmp` it with the file's bytes [U, S-64).
5. Take the principal and the namespace from §4.2's table. For a bare key, write a temporary
   allowed_signers line `<principal> namespaces="<namespace>" <key>` and print the warning
   from §3.3.
6. Run `ssh-keygen -Y verify` as in §4.2.
7. Require the statement's `d31`, the stored inner digest (`head -c U | tail -c 32`) and the D31
   recomputed over `head -c $((U-32))` to be equal. The recomputation uses `split
   --filter=sha256sum` and POSIX `printf`, or a `dd` loop where `split` has no `--filter`.

**For a listed file:** verify the sums file's signature, find the file's line by its SHA-256 (so
the local name does not matter), and print the signed name.

**Tools:**
- Before any pipeline, the script checks for `ssh-keygen`, `sha256sum` and either `split` with
  `--filter` or `dd`. A missing tool gives exit 3 with the package to install, and never a wrong
  digest.
- A missing `-Y` is recognised from ssh-keygen's usage error. `ssh -V` is shown only when it
  exists.
- On EL8, with decision 2's fallback, the signature check runs through platform-python.

**Several files:** one verdict line per file, then the exit code: 1 if any file is not
verified, otherwise 3 if any could not be checked, otherwise 0. Exit 2 is usage.

**Platforms:**
- Linux: the 14 supported distributions; BusyBox through the `dd` loop.
- macOS: the `dd` loop with `shasum -a 256` [A: untested].
- Windows: through WSL [A].

**Trust:**
- It comes from fsbpyr's release or fsbpyr's repository at a tag, never from an app's release
  page and never through `curl | sh`.
- It is short enough to read, and its SHA-256 per version is on the devitops.com page.

Measured with prototypes, 29.5 MB:
- 0.09–0.10 s on the Debian 13 host;
- the D31 recomputation 0.13 s on AlmaLinux 8, with coreutils 8.30 only;
- 600 MiB in 2.9 s.

## 6. fsbpyr's own checks, in C

### 6.1 C and Python

`_fsbpyr` gets three private, undocumented functions:
- `_sha256(fd)`: the low-level `SHA256_*` that integrity.c already links.
- `_tree_digest(fd, length, cut)`: integrity.c's leaf code, with the optional cut, so that a signed
  file's inner and outer digests come from one pass. The start path calls it without a cut. The
  cut change touches that start-path code (stack frames grow from 360 to 632 B); it was prototyped
  and checked on 71 cases.
- `_ed25519_verify(signed_data, signature, key)`. The SSHSIG signed data is built in C with the
  low-level `SHA512_*`, which never reads `openssl.cnf`.

Ed25519 goes through EVP in a private `OSSL_LIB_CTX`:
- the calls are `EVP_PKEY_new_raw_public_key_ex`, `EVP_DigestVerifyInit_ex` (7 parameters) and
  `EVP_DigestVerify`, and only a return of 1 is accepted;
- S < L is checked first;
- `ERR_clear_error()` runs before returning, so CPython's `_ssl` and `_hashlib` never see a
  reject;
- the prototypes are declared by hand, as integrity.c does.

Measured:
- +320 B per stub, 0.33 ms warm and 0.65 ms cold;
- correct with no config, a base-only one, a FIPS `default_properties` one, a malformed one and a
  missing one;
- a private context still reads the process's `openssl.cnf` once, which fsbpyr's own
  `import hashlib` does anyway.

Rejected:
- `ossl_ed25519_verify`: an internal symbol with 6 parameters in 3.0/3.1 and 11 from 3.2. With
  the NULL context it returns 0 for a valid signature under a base-only config.
- `OPENSSL_init_crypto(NO_LOAD_CONFIG)`: it turns off the config for the whole process, the app's
  `ssl` included.

The fallback, if a §12 test fails, is Monocypher 4.0.3 under CC0: UBSan-clean, 62 µs cold,
+21.8 KB per stub.

Python fetches, parses statements and armour, and applies the policy. It hashes nothing on these
paths: `read(…, verify=False)` follows a C check. `_fsbpyr` is in every built app, so app code
could call these functions. They stay private and undocumented, and fsbpyr only ever passes its
pinned keys, so no small-order or non-canonical key can come in. Run from source, without
`_fsbpyr`, the checks fail closed: "signature checks need a built fsbpyr".

### 6.2 Trust rule

A statement is accepted when it is signed in `fsbpyr-release@devitops.com` by a key in the
`allowed_signers` that the running fsbpyr carries. There is no environment override. After an
update, the new binary's own file is the pinned one.

### 6.3 Paths: each file checked once, before any byte of it is used

**Self update:**
1. Fetch `SHA256SUMS` and `SHA256SUMS.sig` as bytes and verify them: subject `fsbpyr`, and a
   `version` equal to the tag and newer than own.
2. Look up `<asset>.xz` in the verified statement. A missing API entry is a server error.
3. Download the `.xz` to a temporary file, with a size cap. Its SHA-256 (`_sha256`) must equal
   its line before liblzma sees it. That needs a restructure: today the `.xz` is streamed into
   lzma (selfupdate.py:158).
4. Unpack with a cap. The unpacked bytes are fixed by the verified `.xz`, so they are not hashed
   again: `read(new, verify=False)`. Then check build_info: `release_asset` equals own asset, and
   the tag equals the version.
5. chmod, `check_glibc`, `_stage_extras`, then rename. `_stage_extras` runs the new binary, and
   its launcher catches any damage.

A missing `.sig` gives "release vX.Y.Z is not signed; nothing was changed", with no fallback. No
fsbpyr message suggests `curl | sh` any more; each points to the verified recipe in
User-Installing.

**Dispatch:** the same steps against its own tag, where `release_asset` is the variant's. The
variant is checked once, before it enters the cache under a temporary name and is renamed. It is
not checked again when reused: its own launcher checks its D31 at start, and the cache is the
user's own (D20).

**EL8 stub download:**
1. Fetch `SHA256SUMS-glibc2.28` and its `.sig` from `el8-vX.Y.Z`: subject `fsbpyr-el8-stubs`,
   version equal to own.
2. The `.xz`'s SHA-256 must equal its line before unpacking.
3. Then the runtime-id check and `_keep`, as today.

Cached and placed stubs work as today, by runtime id (D46). A cached stub was checked when it
arrived. A placed stub stays a documented limit, because el8.yml's own jobs place freshly
linked, unsigned stubs.

**`inspect`:**
- An app's block is shown without implying a check:
  `signature: present (claims myapp 1.4.2; not checked: run fsbpyr-verify.sh with the publisher's key)`.
  It shows no fingerprint, because that would be the file's own claim.
- `inspect` prints the inner `d31` on a line of its own, so it can be compared with the statement.
- `inspect --verify` of an fsbpyr executable or stub checks the pinned keys and prints
  `signature: good, fsbpyr 0.29.0, release key 1`. For an app it prints `signature: not checked
  by fsbpyr (use fsbpyr-verify.sh)`, which does not affect the exit code.
- The existing `digest … verified` line stays as it is; bootstrap.sh greps it. For a signed file
  it is the outer digest. The inner and outer digests come from the one pass with the cut.
- `--json` gains a `signature` object.
- This fulfils the plan's line for releases (docs/IMPLEMENTATION_PLAN.md:1574-1575). Its
  "signing changes nothing here" for self update no longer holds: self update gains the steps
  above.

### 6.4 An app does not check its own signature

- Whoever can change the file can remove the check or swap the key, because the checking code is
  in the same file.
- The key that matters is the one the user trusts, and the file cannot know it.
- D31 already catches damage at every start.
- A self-check would cost every start an Ed25519 verify, and would tie every deployed binary to
  the publisher's key changes.

The new launcher only parses the block strictly (§4.3).

### 6.5 Keys, rotation, recovery, rollback

- **Pinned keys:** K, in CI, and R, offline. Either may sign, and every fsbpyr from the first
  signed release on carries both.
- **K lost or stolen:** the recovery release (§3.1) has its `SHA256SUMS` signed by R and carries
  `{K2, R}`.
  - Every client accepts it, since R is pinned. Clients that update pin K2.
  - A client that skips the recovery release and later meets a K2-signed one stops with:
    "release vX.Y.Z is signed by a key this fsbpyr does not know, because the release key
    changed in vA.B.C: reinstall with the verified recipe (<User-Installing URL>)". `self update`
    has no version target, so "update to vA.B.C first" is not offered.
- **Limit:** a stolen K reaches every client that still pins it, until that client updates. That
  holds for any scheme without online revocation. Release notes and the wiki would then tell
  users of older versions to update or reinstall.
- **R lost:** a K-signed release pins `{K, R2}`.
- **Both lost:** users reinstall with the verified recipe.
- **Rollback of fsbpyr:** the signed version must equal the tag and build_info, and be newer than
  own (self update) or equal to own (dispatch, stubs).
- **Rollback of apps** is not stopped; the script prints the signed version.
- **Freeze** (updates withheld) is not stopped.

## 7. Release pipeline

| Job | Change |
|---|---|
| build (build.yml) | none: unsigned, reproducible executables, compared in the full suite as today |
| `sign-exe` (new) | `environment: release-signing`; only SHA-pinned first-party actions; runs only coreutils and ssh-keygen. Writes the 8 statements with `printf` from `tail -c 32` and the tag, signs each, checks each signature against the checkout's `allowed_signers` and uploads the `.sig` files. |
| `embed` (new) | No secret; in build.yml's pinned image, so `xz` makes the same bytes. Runs `fsbpyr sign --signature` with the built fsbpyr, which checks release statements against its pinned keys in C, so the image needs no ssh-keygen. Then makes the `.xz`, the SBOM and the licences of the signed files. |
| `sbom` | moves after embed; its subject is the signed file |
| `prepare` | The first half of today's publish: green-run check, notes, `make-install.sh` (the only step that runs built code), then `SHA256SUMS`, written outside the directory it scans, and provenance over the same globs plus `SHA256SUMS` and `fsbpyr-verify.sh`. `FULL_SUITE_RUN` and the notes move between jobs as artifacts or outputs. |
| `sign-sums` (new) | Same environment and rules. Signs `SHA256SUMS` after `cmp` with the expected header. With `FSBPYR_RECOVERY=1` it is skipped, and publish makes a draft for `make release-recover`. |
| `publish` | `contents: write` only. One `gh release create`, with `SHA256SUMS.sig` and `fsbpyr-verify.sh` added. |
| el8.yml | Gains a `sums` job (with a checkout) and a `sign` job in the same environment. Its publish adds the `.sig` and the `.xz` lines. It stays outside the release gate, and a retry gives the same `.sig`, because Ed25519 is deterministic. |
| check-release.sh | See below. |
| make release / release-check | no new step; `make release-recover` is new, for the recovery release only |

check-release.sh changes:
- every executable and both sums files checked with the script, against the tag's
  `allowed_signers`;
- the asset sets: +2 for the main release, +1 for EL8;
- `.sig` and the script added to the download patterns at :161;
- `.sig` excluded at :170 and in :179's jq select;
- `#` lines skipped at :169, :180 and :264;
- :180 run under `LC_ALL=C`;
- the previous tag's key file fetched;
- `ssh-keygen` added to the tool list;
- version gating for releases up to 0.28.x;
- test_release.py, test_el8_workflow.py and test_check_release_el8.py follow.

**Custody, stated plainly:**
- K is as safe as the credentials that can create a `v*` tag; with restricted creation, that is
  your GitHub login.
- K's jobs run no built code and no third-party action, and K lives only in `$RUNNER_TEMP`.
- It does not stop a malicious change to release.yml merged into main, or a poisoned build: either
  gets a validly signed release. The attestations have the same limit today.
- Decision 4 shows the offline alternative.

## 8. Threats

| Threat | Result |
|---|---|
| Tampered executable, digest rewritten | **Stopped** for the script and for fsbpyr; not at run time |
| Second payload hidden in the block, outer trailer repointed | **Stopped** by the strict launcher parse (§4.3) |
| Extra bytes put into the block | **Stopped**: the block must be byte-exact |
| Stripped signature | The script says "not signed", and fsbpyr's own paths refuse. For apps the publisher must say that releases are signed |
| Project A served as project B by the same key | **Stopped** with the publisher's line (per-project namespace); with a bare key, a warning |
| Another version of the same app | **Not stopped**; the version is shown |
| Rollback of fsbpyr | **Stopped** (§6.5) |
| Secret taken through a newly created `v*` tag | **Stopped** once creation is restricted (a one-time setting); **open today** |
| Malicious release.yml merged into main | **Not stopped**, the same as for the attestations |
| Stolen K | **Partly**: R signs the replacement, and old clients are exposed until they update |
| Stolen builder key | **Not stopped** until the publisher replaces the line; a FIDO key makes theft hard |
| Malicious mirror, TLS interception | **Stopped**, except freeze |
| Key file replaced among the downloads | **Stopped**: the key is saved once, outside the download directory |
| Script replaced | **Caught** by its SHA-256 on devitops.com, if the user compares it; not if they don't |
| `curl \| sh` | **Not stopped**; the verified recipe replaces it in all docs and messages |
| First update from ≤ 0.28.x | **Not stopped**: old code, TLS only |
| Hostile `OPENSSL_CONF` | Results unaffected (measured). An ENGINE named in it is untested [A]. Whoever sets fsbpyr's environment controls fsbpyr anyway |

## 9. Performance and size

| Item | Value |
|---|---|
| fsbpyr download check | SHA-256 of the `.xz`, about 3 ms for 15 MB, + Ed25519 0.33–0.65 ms |
| `inspect --verify` of a signed 29.5 MB file | one D31 pass, about 1.6 ms, + Ed25519 |
| signing | ssh-keygen about 3 ms with a key, plus a touch (and a PIN with verify-required) for sk |
| embedding | one D31 pass in C for the outer trailer, about 1.6 ms per 30 MB |
| app start | unchanged except the strict parse, which is one pread and a few comparisons [A: to measure] |
| third party | the script 0.09–0.13 s per 30 MB, 2.9 s per 600 MiB; a bare ssh-keygen + sha256sum takes 12–99 ms depending on the distribution |
| bytes per executable | statement 130–160 B, signature 322–407 B, footer 64 B, trailer 64 B: about 0.6–0.7 KB |
| per stub | +320 B (EVP glue), plus the `_sha256` and cut glue [A: small] |
| per release | `SHA256SUMS` +3 lines; 2 new assets (`SHA256SUMS.sig`, the script) |

## 10. Rejected alternatives

- **Flat SSHSIG over the file:** one single-threaded SHA-512 per check, 24 ms for 30 MB.
- **New digests for signing** (the first draft's whole-file tree and plain SHA-256 lines): the
  stored D31 and the existing SHA-256 lines are enough.
- **Re-checking downloads after unpacking, and cached files at reuse** (first draft): it repeats
  the start check.
- **A signed key list with a serial** (first draft): old lists stay valid forever (attacker
  review), and it needs more than two pinned keys do.
- **Sigstore as the signature:** needs gh and a network or a trust root, and cannot run in C. It
  stays as provenance.
- **OpenPGP/gpgv:** a second key format, and sk keys cannot produce it.
- **KMS:** a cloud account, custom SSHSIG code, and a signing oracle for a compromised job.
- **fsbpyr's own signer:** a key parser, bcrypt KDF and agent client, and still no FIDO.
- **Implicit key choice:** it signs silently as an unintended identity.
- **Vendored Ed25519 as the primary:** +18–28 KB in every app, against +320 B; it is the
  fallback.

## 11. Decisions for you (ranked; each has a recommendation)

1. **Where the signature lives.** *Recommended:* in the executable, plus signed sums for the other
   release files. *Alternative:* detached, as one `FILE.sig` holding statement and armour.

   | | In the executable | Detached `FILE.sig` |
   |---|---|---|
   | Third party fetches | file (+ saved key + script) | file and `.sig` (+ key + script) |
   | After `cp`, mirrors, images, package repos | still verifiable | the `.sig` is often lost |
   | Builder publishes | one file | two files, kept together under their names |
   | Published bytes | U plus about 0.7 KB | the reproducible, smoke-tested bytes, untouched |
   | Launcher | strict parse of the block in every app | untouched |
   | Signing later or by a second party | re-embeds | add another `.sig` |
   | CI jobs (fsbpyr and builders) | sign + embed; fsbpyr's `.xz` and SBOM move after embed | sign only; release order unchanged |
   | Work | layout, launcher and reader parse, fuzz and edge cases, `sign --signature`, two jobs | statement files and name rules |

   Your goal's "always can" and "signature to the fsbpyr binary" favour the first.
2. **EL8 third parties** (the users of `--glibc 2.28` apps).
   - *Recommended:* a fallback in the script for EL8. Python only parses; the Ed25519 check is
     EL8's own libcrypto (OpenSSL 1.1.1k), called through ctypes from `/usr/libexec/platform-python`
     3.6, which is present wherever dnf is. The file is still hashed by coreutils. Measured on Rocky
     and Alma 8: `EVP_DigestVerify` returns 1 for a valid signature and 0 for a flipped one.
   - *Alternative:* the script refuses, and the user checks on another machine and copies the
     checked file.
   - It is your call whether "crypto in C" allows Python as glue around a system library inside a
     third-party script.
3. **Builder default.**
   - *Recommended:* sign when a listed key is usable, otherwise build unsigned with a one-line
     notice; `--sign` makes signing mandatory for release builds. Contributors' builds, CI test
     jobs and Dependabot keep working.
   - *Alternative:* a configured key that cannot be used fails the build, and every non-release
     build site needs `--no-sign`.
4. **Release key custody.**
   - *Recommended:* K as an environment secret, tag creation restricted to you, R offline: zero
     steps per release.
   - *Alternative:* sign every release offline. release.yml makes a draft, and `make
     release-publish` signs and publishes. With embedded signatures that is 9 sk signatures, each a
     PIN and a touch (8 statements and `SHA256SUMS`), in two rounds with CI work between them, plus
     1 for the EL8 companion. With detached signatures (decision 1) it is 1 + 1. It stops a
     compromised workflow from signing, but still signs whatever CI built.
5. **Rotation.**
   - *Recommended:* two pinned keys, K and R, with the recovery release (§3.1, §6.5).
   - *Alternative:* K only, with no token, no handle file and no recovery path: a lost or stolen K
     means every user reinstalls once.
6. **The third parties' key model.**
   - *Recommended:* save the key once and treat a change as needing confirmation (§3.3).
   - *Alternative:* fetch the key at each release's tag. That is simpler to document, but a pass
     then rests on write access to the publisher's repository.
7. **Signatures mandatory in fsbpyr's paths** from the first signed release. *Recommended:* self
   update, dispatch and stub downloads refuse unsigned releases. *Alternative:* warn for one
   release, then refuse.
8. **Permanent names.**
   - *Recommended:* `fsbpyr-release@devitops.com` and `<project>.fsbpyr-app@devitops.com`,
     `fsbpyr-verify.sh`, `allowed_signers`, and the footer magic `FSBPYR-SIGNATURE 1`.
   - *Alternative:* a namespace without your domain, such as `<project>.fsbpyr-app`. That is
     shorter, but against OpenSSH's advice to use a domain you control.

**Your rule, applied:** sign the stored D31 (§4.1).

**Decided while designing** (say if you disagree):
- SSHSIG with Ed25519 or Ed25519-SK only;
- signing by the user's own ssh-keygen, reading a file, never stdin;
- EVP in a private context, with Monocypher as the fallback;
- no self-check at start;
- each file checked once, on arrival;
- placed EL8 stubs as today;
- `inspect` checks only fsbpyr's own signatures.

**Known gaps (need your OK):**
- app rollback and freeze are not stopped;
- an app whose signature was stripped is caught only if users expect a signature;
- the first update from 0.28.x or older is not checked, and releases up to 0.28.x stay unsigned;
- fsbpyr itself does not handle SSH certificates or revocation lists (ssh-keygen's `-r` serves
  third parties);
- placed EL8 stubs are accepted by runtime id;
- the script on macOS and Windows (WSL) is untested;
- aarch64 timings are not measured on hardware;
- without decision 2's fallback, EL8 cannot check on the host.

## 12. Test before building

- sk signatures from a real token, and with Fedora's `openssh-sk-dummy`, across the 12 modern
  distributions. The dummy and synthesised vectors already pass on 8.2p1, 8.7p1 and 9.9p1 to
  10.6p1.
- Per-project `namespaces=` lines on OpenSSH 8.2 and 8.4.
- uutils `split --filter` (Ubuntu 26.04), and the `dd` fallback on BusyBox and macOS.
- The EL8 fallback (decision 2) as a 3.6-compatible script on Rocky and Alma 8, including the
  microdnf minimal images.
- The strict parse's cost at start, the absorb layout under the fuzz harness, and the cut change
  under the sanitized stubs.
- EVP Ed25519 on aarch64 hardware, and an `openssl.cnf` that names an ENGINE.
- `gh secret set --env` before and after the environment exists; the ruleset bypass by role.

## 13. Implementation outline

1. **Launcher and format.**
   - The footer parse and the inner/outer consistency checks in payload.c and reader.py.
   - `_sha256`, `_tree_digest` with the cut, and `_ed25519_verify` in a new `sig.c`.
   - Signed cases in `tests/support/edges.py`, which shares them with the unit tests, the e2e tests
     and the fuzz corpus.
   - `tests/probes/sshsig.c` with RFC 8032 vectors, signatures that ssh-keygen makes at test time,
     synthesised sk vectors, and the negative cases of §4.2–4.3. `sshsig_fuzz.c` in test_fuzz.py.
   - format.md "Signatures"; test_validation.py stays at version 7.
2. **Python.**
   - `signing.py`: statements, armour and policy.
   - The `signing-key` setting, `--signing-key`, `--sign/--no-sign`, `fsbpyr sign` with
     `--signature`.
   - `inspect`'s signature and d31 lines and its JSON.
   - Hypothesis tests for the statement round trip.
3. **Paths** (§6.3).
   - self update, dispatch and stubs.py download to a file before unpacking.
   - FakeGitHub serves signed fakes. Tests patch the pinned keys in-process, with a stand-in that
     runs the compiled probe, so the real C runs.
   - A built fsbpyr's pinned path is covered by a self-build with a session key, and by
     check-release.sh.
4. **Release.**
   - The jobs of §7, `make release-recover`, check-release.sh and its tests.
   - The one-time setup of §3.1, including the devitops.com page.
5. **Script and docs.**
   - `tools/release/fsbpyr-verify.sh`, with its own test across the 14 images and BusyBox.
   - `ssh-keygen` in `tests/support/requires.py` (no skips), openssh-client in the CI images,
     CONTRIBUTING.
   - security.md, where D31's known limit closes; a new D-record; the man page.
   - Wiki: User-Building, User-Settings, User-Installing with the verified recipe, a new
     User-Verifying page that builders link to (with the message table and EL8), Dev-Security.
   - A CHANGELOG "Added" entry, so the first signed release is the next minor.
