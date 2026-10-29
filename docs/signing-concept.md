# Signed fsbpyr and signed apps: concept

Status: concept for the owner, 2026-10-29, at fadc3d9. Nothing is implemented. This is the fifth
version. Earlier versions went through a design panel, three reviews (attacker, owner alignment,
usability), a review against the code, runs of every command on the 14 supported distributions, a
round of fact-check, attacker and critic, an independent design review, and two checks of its
fixes.

You ruled: "there will be no https://devitops.com/fsbpyr/keys, keys distribution must be solved
differently", and approved repository-only keys. Keys and the script now come only from the
publisher's repository at a release tag, trusted on first use (§3.3). The one-time setup is one
paste (§3.2). A change of fsbpyr's own key is confirmed by the offline recovery key (§6.5).
`make release` guards the two files users save, and the key file's `.sig` (§3.1). This version
came from a design round: facts read and measured on GitHub, the release pipeline and the save
recipes, then three designs (usability, attacker, minimal) and a judge. Its user commands then ran
with a prototype of the script on 4 images (Debian 13, Ubuntu 26.04, AlmaLinux 8, Alpine), not on
all 14, and the release guard ran in a disposable clone (§3.1, §3.3); §12 lists the rest. Three
reviews of this version (attacker, facts, usability) followed: 34 of their 37 findings were
applied in full, 2 in part and 1 not; what the fixes added and nobody ran is marked [A].

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
  - The 0.28.1 launcher, `inspect --verify` of 0.26.0 and 0.28.1, and 0.26.0's `self update`
    accept that layout (tested); the readers of 0.21.0 to 0.28.1 are the same code (read; §4.3).
  - The file carries its signature everywhere it is copied.
  - Release files that are not executables are covered by a signed `SHA256SUMS`: `.xz`,
    `install.sh`, SBOMs, licences, and the EL8 stubs in their companion release.
- **Who signs.**
  - fsbpyr releases: a CI key in a protected GitHub environment, plus an offline recovery key.
    No step is added per release. `make release` stops only when allowed_signers, its `.sig` or
    `fsbpyr-verify.sh` changed since the previous release, until the recovery key confirms the
    key change or you approve the files (§3.1).
  - Apps: the builder, by setting `signing-key`; `fsbpyr build` then signs.
- **Who verifies, and with what.**
  - **Anyone, without fsbpyr:** they need the publisher's key and `fsbpyr-verify.sh`, saved once
    with one paste: both from a release tag, the key from the publisher's repository and the
    script from fsbpyr's (§3.2, §3.3). The script is short POSIX shell, and also an asset of
    every fsbpyr release (§5).
    - On the 12 modern distributions it needs only OpenSSH and coreutils:
      `sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh ~/.config/fsbpyr-keys/myapp myapp`.
    - EL8 lacks `ssh-keygen -Y`. There it can use EL8's own libcrypto through platform-python
      (decision 2).
  - **fsbpyr itself, in launcher C:** `self update`, the download of other Python variants and of
    EL8 stubs, and `inspect --verify`. A download costs one SHA-256 pass over the `.xz` before it is
    unpacked (about 3 ms for 15 MB) plus one Ed25519 check (0.3–0.7 ms). Nothing is checked twice.
  - **A built app does not check its own signature** (§6.4).
- **Trust anchor:** whoever controls the publisher's repository at the user's first save; for
  fsbpyr, the devitops-com organisation on GitHub. No other place is required: fsbpyr has none,
  and an app's second place is optional. fsbpyr's own checks use the keys built into fsbpyr, not
  a saved key (§3.3).

## 2. Your points, and where the concept answers them

| You said | Answer |
|---|---|
| "we do crypto in C launcher code only. Python is way too slow" | All of fsbpyr's checks hash and verify in launcher C, through `_fsbpyr`; no hashlib call sits on a signature path (§6.1). Signing is done by the user's `ssh-keygen`. |
| "why would signing be so slow if almost everything is C code anyway?" | The slow part was a single-threaded SHA-512 over the whole file. Here the signed message is the short statement. |
| "of course we should sign already computed digest" | The statement carries the inner trailer's digest as the build wrote it (§4.1). For a signed file the launcher checks the outer trailer, which covers that digest and the block; `inspect` shows both. |
| "launcher already checks digest at every start, so why should we do that again?" | Each file is checked once, when it arrives. The launcher's start check is never repeated, and cached files are not checked again (§6.3). |
| "download signing key independent of fsbpyr or app … small shell script available as release asset" | The key is saved once from the publisher's repository at a release tag. The script is fsbpyr's release asset and is saved from fsbpyr's repository at that tag, the same bytes. Both come with one paste (§3.3). |
| "there will be no https://devitops.com/fsbpyr/keys, keys distribution must be solved differently" | Keys come only from the repository at a tag, trusted on first use (§3.3). A change of fsbpyr's key is confirmed by the recovery key (§6.5). |
| "I dont see how built app could reliably check itself" | Agreed; the reasons are in §6.4. |
| "add cryptographic signature to the fsbpyr binary" | The signature is in the binary. Decision 1 shows the detached alternative at its best. |

## 3. The three parties' flows

### 3.1 fsbpyr maintainer

One time, in this order:
1. Create the environment `release-signing`. Its only deployment rule is the tag pattern `v*`,
   of type tag: a branch rule `v*` would admit a writer's branch named `v…` [A].
2. Restrict creation of `v*` and `el8-v*` tags, with the repository admin role (today only you)
   as the only bypass. Done on 2026-10-29 as the tag ruleset `release tag creation`. The ruleset
   `release tags` blocks updates and deletion of the same tags for everyone, with no bypass. Keep
   both. Without them, any credential with write access creates a `v*` tag, or moves one onto its
   own commit, and runs its own job in the environment (§8) [A for the move].
3. Make the release key K. It exists only in memory and in the secret. The parentheses remove the
   directory when the block ends, and the key's line is added only after the secret is set:
   ```
   (d=$(mktemp -d -p /dev/shm); trap 'rm -rf "$d"' EXIT
   ssh-keygen -q -t ed25519 -N '' -C "fsbpyr release key 1" -f "$d/k" &&
   gh secret set FSBPYR_RELEASE_KEY --env release-signing < "$d/k" &&
   printf 'fsbpyr namespaces="fsbpyr-release@devitops.com" %s\n' "$(cat "$d/k.pub")" >> src/fsbpyr/allowed_signers)
   ```
   Without them, in an interactive shell the trap ran only at exit, a second paste left the
   first key in /dev/shm after exit, and a failed `gh secret set` still added a line (measured in
   bash, dash and zsh 5.9, with a stand-in gh).
4. Make the recovery key R as a resident key on a FIDO token kept offline. `ssh-keygen -K`
   recovers its handle file; keep a copy of the handle file with the token. It is written outside
   the checkout, because `make release-prep` and `make release` refuse untracked files:
   ```
   ssh-keygen -t ed25519-sk -O resident -O verify-required -O application=ssh:fsbpyr-release -C "fsbpyr recovery key" -f ~/fsbpyr_recovery
   printf 'fsbpyr namespaces="fsbpyr-release@devitops.com" %s\n' "$(cat ~/fsbpyr_recovery.pub)" >> src/fsbpyr/allowed_signers
   ```
   R also confirms each later change of the key file (`make keys-confirm`, below).
5. Commit src/fsbpyr/allowed_signers (new). It is packaged into fsbpyr as the pinned keys. The
   first signed release asks once for `make release TRUSTED_SHA256=…`: no earlier tag has these
   files (measured, below).
6. Nothing is published outside GitHub. GitHub's settings are the anchor (§3.3), so keep immutable
   releases on and both tag rulesets in place; `make release-check` fails when a release is not
   immutable or a ruleset is missing. Your account is what a first-time user trusts: sign in with
   a security key, and keep the tokens that hold the admin role few. That is advice, not measured.

Per release: `make release-prep`, PR, full suite, `make release`; no manual step. release.yml
builds, signs, embeds and publishes (§7). It copies `tools/release/fsbpyr-verify.sh` from the
tag's checkout into the release. `make release-check` also checks:
- every signature;
- that the script asset is the tag's file;
- the immutable flag and the rulesets;
- that a key change since the previous tag is confirmed by its allowed_signers.sig. A change
  without one (the first signed release, R lost) gets an info line with the key lines added and
  removed, not a failure.

It prints an info line when the script changed.

**The guard.** `make release` stops before its prompt when src/fsbpyr/allowed_signers, its `.sig`
or `tools/release/fsbpyr-verify.sh` differs from the previous `v*` tag's. It prints the commits,
with date and author, the key lines added or removed, and the script's diffstat. It goes on only
when:
- the key file changed and the script did not, and src/fsbpyr/allowed_signers.sig confirms the
  change with the previous tag's recovery key: `ssh-keygen -Y verify` against the previous tag's
  file must say `Good` for an `ED25519-SK` key, and the flags must have touch and PIN (§4.2, read
  as §5 says); or
- you approve exactly these bytes with `make release TRUSTED_SHA256=<value>`, which it prints: the
  SHA-256 of what `sha256sum` prints for the three files at HEAD. A stale approval approves
  nothing else. An approval covers bytes, not a history, and a missing file counts as an empty
  one.

With the key file unchanged, a changed or deleted `.sig` stops like a script change. Otherwise a
writer could delete it at a later tag, and `--save fsbpyr` at that tag would answer KEY CHANGED
for a change R confirmed.

Before it prints anything, it applies §5's KEYS rules to HEAD's key file and stops at a refused
line, giving its number. It prints the key lines as `--list` lines, and the commit lines with
every byte outside 0x20–0x7E shown as `?` (`LC_ALL=C tr -c '\n -~' '?'`). The stop message names
every file that changed. The prototype printed raw bytes: an added key line whose comment ended
in `ESC[2K ESC[1G ESC[1A` vanished from its output in tmux, and the stop named only the script,
which had changed too (measured). fsbpyr applies the same rules to its pinned file when it loads
it, and the full suite checks src/fsbpyr/allowed_signers against them.

It sits after the hub/main check (release.sh:101-106) and before the CI-skip check, green-run and
the prompt (:108-151), so `FSBPYR_RELEASE_ASSUME_YES=1` cannot skip it. It runs git with
`GIT_NO_REPLACE_OBJECTS=1`. It fetches the tags with
`git fetch --quiet hub '+refs/tags/v*:refs/fsbpyr-guard/v*'`, with an error line of its own, and
takes as the previous tag the highest version among them that HEAD contains
(`git for-each-ref --merged HEAD --sort=-v:refname 'refs/fsbpyr-guard/v[0-9]*'`); with none,
release.sh stops there. The prototype used `git describe` over local tags: a local tag `v0.29.1`
at HEAD made it see no change, and so did two `git replace` refs, with `git status` clean. The
form above saw the change in both cases (measured, git 2.47.3, a local bare repository as hub).

The guard catches a change you did not notice. It cannot hold against a writer whose code you
run before it: release.sh itself, what release.sh runs earlier (tools/release/version-files.sh,
sourced at :32, and release-notes.sh at :92-94), the Makefile (Makefile:264), or tests and hooks
you ran after pulling main (§8). Restricting updates of main to the admin role closes that
(decision 4).

Measured in a disposable clone, with a local bare repository as hub and a stand-in gh, running
the real release.sh under `set -euo pipefail` with OpenSSH 10.0p2 and a synthetic sk recovery key:
31 cases gave the intended result. Among them: a valid `.sig`; a `.sig` by K, a stale one, one of
another change, and none; flags 0x00, 0x01 and 0x04; a script change and a deleted script; both
files changed; the first signed release; `make release TRUSTED_SHA256=…` through GNU make 4.4.1; a
previous tag present only on hub; and runs without `FSBPYR_RELEASE_ASSUME_YES` and without a
terminal. The guard is 53 lines of bash, and release.sh grows from 219 to 273 lines. It uses
`<(…)`, so it is bash only, as release.sh already is (Makefile:264 runs it with bash). Not run:
GitHub as hub, a real gh, a real security key, OpenSSH other than 10.0p2, and what this version
added to the prototype: the `.sig` as a third file, the KEYS rules and the escaped output, the tag
lookup above, and the recovery switch below [A].

After K is lost or stolen (§6.5):
1. Make K2 with step 3, with `-C "fsbpyr release key 2"`, and delete K's line from
   src/fsbpyr/allowed_signers.
2. `make keys-confirm` (tools/release/keys-confirm.sh, about 15 lines [A]):
   - builds the statement (§4.5) from the previous tag's file and the new one;
   - prints both sets as `--list` lines;
   - has R sign it through a file in a 0700 directory, never stdin;
   - checks the signature and the flag rule;
   - writes src/fsbpyr/allowed_signers.sig.

   Commit both files, and add the change to wiki/User-Verifying.md#key-changes: its tag and step
   5's `--save fsbpyr` command. End the branch with `make release-prep`; PR; merge.
3. `make release`. The guard sees that the release key line (`ssh-ed25519`) differs from the
   previous tag's and turns recovery mode on by itself: sign-sums would otherwise sign
   `SHA256SUMS` with K2, which no installed fsbpyr pins (§6.2), and the published release could
   not be changed. When R is lost too (§6.5, both lost), `make release RECOVERY=0` turns it off.
   - release.sh writes the tag message `fsbpyr vX.Y.Z (recovery)`; only the admin creates tags.
     An environment variable would reach no job: release.sh only writes and pushes the tags
     (:154-159).
   - release.yml reads the message through the API [A], signs the executables with K2 and makes
     a draft (`--draft`, as el8.yml:769 does).
   - release.sh prints "draft: run make release-recover".
4. `make release-recover`:
   - checks the draft's SHA256SUMS: its header, and its provenance with
     `gh attestation verify … --signer-workflow …/release.yml --source-ref refs/tags/$TAG`;
   - signs it with R (PIN and touch) and uploads the `.sig`;
   - runs `gh release edit "$TAG" --draft=false`;
   - runs `gh workflow run el8.yml --ref "$TAG"`, because el8.yml waits at most 1 h.
5. The release notes give the command that saves the new key, here for a change in 0.35.0 (§3.3,
   §6.5):
   ```
   sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh --save fsbpyr https://raw.githubusercontent.com/devitops-com/fsbpyr/refs/tags/v0.35.0/src/fsbpyr/allowed_signers
   ```

After R is lost: make R2 with step 4, replace R's line, delete src/fsbpyr/allowed_signers.sig,
add the change to wiki/User-Verifying.md#key-changes, then `make release TRUSTED_SHA256=…`.
Checks keep passing, since K is unchanged, but a saved {K, R} cannot follow the next key change.
fsbpyr's KEY CHANGED tells users to keep their file; the release notes tell them to delete it and
save again (§6.5).

### 3.2 App builder

**Check fsbpyr before using it** (goal point 1). Once, paste this into a shell. It saves fsbpyr's
checking script, unless one is saved, from the tag of the release that last changed it, and
fsbpyr's key from the tag that introduced the current key set; at the first signed release both
are v0.29.0 (§3.3, §4.5):
```
sh -c 's=~/.config/fsbpyr-keys/fsbpyr-verify.sh; mkdir -p ~/.config/fsbpyr-keys; rm -f "$s.new"
[ -f "$s" ] || { curl -LsSf https://raw.githubusercontent.com/devitops-com/fsbpyr/refs/tags/v0.29.0/tools/release/fsbpyr-verify.sh >"$s.new" &&
  [ -s "$s.new" ] && sh -n "$s.new" && mv "$s.new" "$s" && echo "saved $s"; }; rm -f "$s.new"
if [ -f "$s" ]; then sh "$s" --save fsbpyr https://raw.githubusercontent.com/devitops-com/fsbpyr/refs/tags/v0.29.0/src/fsbpyr/allowed_signers
else echo "NOT SAVED: fsbpyr-verify.sh could not be downloaded, see the line above"; exit 1; fi'
```
It prints two key lines, `fsbpyr release key 1` (ED25519) and `fsbpyr recovery key`
(ED25519-SK). It ran against a local https stand-in for raw.githubusercontent.com, with an
earlier last line (§3.3). On GitHub it answers NOT SAVED until v0.29.0 is tagged with the script:
today the script URL gives 404 (measured). User-Verifying's copy names in its script URL the
version the script carries, so a script fix reaches new builders (§5, `--save-script`).

Then check each version's executable, in the directory you downloaded it to. Nothing here fetches
a key or a script:
```
curl -LsSfO https://github.com/devitops-com/fsbpyr/releases/download/v0.29.0/fsbpyr-3.14-x86_64-linux-gnu
sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh ~/.config/fsbpyr-keys/fsbpyr fsbpyr-3.14-x86_64-linux-gnu
```
- `--save` never replaces a saved key, except fsbpyr's after a change its recovery key confirmed;
  a failed or cut-off download saves nothing, except an app's key file cut exactly between two
  lines behind a proxy that hides the length (§8).
- The commands carry no `#` comments: interactive zsh passes a comment on as arguments. In the
  third version, a `# compare …` after `ssh-keygen -lf -` made it fail with "Too many arguments."
  (measured).
- Everything here comes from the devitops-com organisation on GitHub, the CHANGELOG, wiki and
  release notes included, so they cannot confirm a key. There is no second channel (§3.3).
- An installed fsbpyr checks the same way, under any name:
  `sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh ~/.config/fsbpyr-keys/fsbpyr ~/.local/bin/fsbpyr`.
- To install with `install.sh`, the verified recipe that replaces `curl | sh` (User-Installing):
  download it with `SHA256SUMS` and `SHA256SUMS.sig`, check it with the script (it is listed in
  the signed sums), then run it:
  ```
  curl -LsSfO 'https://github.com/devitops-com/fsbpyr/releases/download/v0.29.0/{install.sh,SHA256SUMS,SHA256SUMS.sig}'
  sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh ~/.config/fsbpyr-keys/fsbpyr install.sh && sh install.sh
  ```
  curl's own `{…}` list, quoted, saved every file with one `-O` on curl 7.61.1 and 8.14.1, also
  from zsh and fish (measured with v0.28.1's `install.sh` and `SHA256SUMS`; it has no `.sig`, and
  this exact line exits 22 there). The check did not run: the script does not exist yet.
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
   - checks the signature with `ssh-keygen -Y verify` and §4.2's flag rule;
   - only then replaces the output;
   - prints the first line below. With `--sign`, and in `fsbpyr sign` (also with `--signature`,
     the CI path), the rest follows: what the builder publishes. Other builds add one line: "with
     --sign, fsbpyr also prints what to publish". fsbpyr cannot know where the key file is
     served, so the users' part has KEY-URL to fill in:
   ```
   fsbpyr: signed dist/myapp (myapp 1.4.2) with ED25519 key SHA256:Wm3…
     Publish dist/myapp. Your users save your allowed_signers once, from its https URL at one of
     your release tags (on GitHub, the raw.githubusercontent.com URL with refs/tags/<tag>/ in it).
     That first save trusts whoever controls your repository then; later saves never replace it,
     and a changed file reaches them only as KEY CHANGED. So keep your release tags fixed: on
     GitHub, a tag ruleset that blocks updates and deletion with no bypass, tag creation
     restricted to admins, and immutable releases. Name the tag that introduced your current
     allowed_signers, and a newer one after you change it.
     The save shows your users these lines. Optional: publish them outside the repository too,
     such as on your website, and tell your users where to compare them:
       myapp  myapp.fsbpyr-app@devitops.com  ED25519 SHA256:Wm3…  laptop
       myapp  myapp.fsbpyr-app@devitops.com  ED25519 SHA256:Qx7…  ci
     For your users, with KEY-URL replaced by that URL:
       Once, paste this into a shell. It saves the checking script of fsbpyr, the tool that built
       myapp, unless you saved it before, and my key:
         sh -c 's=~/.config/fsbpyr-keys/fsbpyr-verify.sh; mkdir -p ~/.config/fsbpyr-keys; rm -f "$s.new"
         [ -f "$s" ] || { curl -LsSf https://raw.githubusercontent.com/devitops-com/fsbpyr/refs/tags/v0.29.0/tools/release/fsbpyr-verify.sh >"$s.new" &&
           [ -s "$s.new" ] && sh -n "$s.new" && mv "$s.new" "$s" && echo "saved $s"; }; rm -f "$s.new"
         if [ -f "$s" ]; then sh "$s" --save myapp KEY-URL
         else echo "NOT SAVED: fsbpyr-verify.sh could not be downloaded, see the line above"; exit 1; fi'
       Then check each download in the directory you saved it to; run it only if this says VERIFIED:
         sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh ~/.config/fsbpyr-keys/myapp myapp
   ```
   - `v0.29.0` in the script URL is the building fsbpyr's own version, not a placeholder: fsbpyr
     prints its own script URL in full, at its own tag. KEY-URL is the builder's only
     placeholder.
   - When `signing-key` is a `.pub` or key text, this goes before "For your users":
     ```
       Your users' save takes an allowed_signers file. Commit this line as allowed_signers and serve that file:
         myapp namespaces="myapp.fsbpyr-app@devitops.com" ssh-ed25519 AAAA… laptop
     ```
   - The lines to publish are what `fsbpyr-verify.sh --list` prints for the allowed_signers file
     the users save (for a `.pub` or key text, the line printed above), so the users compare like
     with like with what `--save` shows.
   - An unfilled KEY-URL fails safe: `--save` refuses anything but an https:// URL before
     fetching, and nothing is saved (measured). No sample URL with OWNER/REPO is printed:
     github.com/OWNER is a real account (measured 2026-10-29), whose holder could serve a key
     there.
   - The `sh -c '…'` wrapper runs the same lines in bash, zsh, dash, BusyBox sh and fish. tcsh
     refuses the whole paste ("Unmatched '''.") and runs nothing (measured). A KEY-URL
     containing `'` would break it, and the inner sh would run a `$(…)` or `;` in it before
     `--save` sees it. Git accepts all three in a tag name (`git check-ref-format`, measured), so
     name the tag in KEY-URL with `[A-Za-z0-9._-]` only.
   - The block has no EL8 line: the script picks the EL8 path by itself, and its CANNOT CHECK HERE
     message carries the wiki link when it cannot (§3.3).

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
- An sk key made with `-O no-touch-required` signs with flags 0x00 (measured with sk-dummy), which
  the script refuses (§4.2): "this security key signs without a touch, which fsbpyr-verify.sh
  refuses: make one without -O no-touch-required and replace its line in allowed_signers". The
  output is not replaced.
- A line in the `signing-key` file that `fsbpyr-verify.sh` would refuse (§5): "line N of
  allowed_signers is not a key line"; nothing is built.
- Subjects `fsbpyr` and `fsbpyr-el8-stubs` are reserved for fsbpyr's own release and refused.
- No ssh-keygen: "signing needs ssh-keygen: install openssh-client (Debian, Ubuntu),
  openssh-clients (SUSE, RHEL family) or openssh (Fedora, Arch)". fsbpyr installs nothing.

**Sign in CI.** The build job runs build backends and the smoke test, so it gets no secret. Where
the key lives depends on the host (details below):
- GitHub, a public repository or a private one on Pro, Team or Enterprise Cloud: an environment
  secret.
- GitLab: a protected variable, scoped to the sign job's environment, with every protected branch
  closed to Developers.
- GitHub, a private repository on Free: no key in CI.
- A repository secret or an unprotected variable only when every person, app and token that can
  push a branch may hold the key.

The split keeps built code away from the key only where every job starts on a clean machine, as
on GitHub-hosted runners. A self-hosted runner that keeps state "can be persistently compromised
by untrusted code in a workflow" (GitHub's documentation), and a GitLab shell executor likewise
[A]. A runner of the sign job's own does not help by itself: a job pushed on any branch can ask
for its labels (GitHub) or its tags (GitLab) and leave code there for the next sign job, and
GitHub says environments "are still susceptible to the same risks when run on a self-hosted
runner". Use an ephemeral runner that starts clean for each job, such as GitHub's just-in-time
runners, or on GitLab a protected runner, which "can run jobs only on protected branches"
(GitLab's documentation) [A: not run].

1. **Build job:** `fsbpyr build --no-sign`. It uploads `dist/myapp` and `dist/myapp.statement`.
2. **Sign job.** It holds the key and runs only coreutils, `cmp` and `ssh-keygen`: no fsbpyr, no
   `curl | sh`, no built code. It rebuilds the expected statement itself and signs only if the
   uploaded one is byte-equal:
   ```
   printf '# fsbpyr-signature 1\n# subject myapp\n# version %s\n# d31 %s\n' "${TAG#v}" \
     "$(tail -c 32 dist/myapp | od -An -tx1 | tr -d ' \n')" > expected
   cmp expected dist/myapp.statement && ssh-keygen -Y sign -f "$k" -n myapp.fsbpyr-app@devitops.com dist/myapp.statement
   ssh-keygen -Y verify -f allowed_signers -I myapp -n myapp.fsbpyr-app@devitops.com -s dist/myapp.statement.sig < dist/myapp.statement
   ```
3. **Embed job.** No secret. `fsbpyr sign --signature dist/myapp.statement.sig dist/myapp`.

The wiki gives the three jobs in full for GitHub Actions and GitLab CI, measured where noted:
- The secret is mapped with `env:` (GitHub) or read from the variable (GitLab), and checked to be
  non-empty.
- The key is written with `mktemp`, ending in a newline (a key without one is refused), and
  removed by `trap`.
- **Who can read the key.** A repository secret (GitHub) or an unprotected variable (GitLab)
  reaches a workflow pushed to any branch, and an `if:` in the sign job does not bind that
  workflow. GitHub says: "Any user with write access to your repository has read access to all
  secrets configured in your repository." So every person who can push a branch can copy the
  key, and so can every app or token that can push a branch and may write workflow files (the
  `workflow` scope or the Workflows permission). On a private repository with "Send secrets to
  workflows from pull requests" on, so can readers' forks. That is acceptable only when all of
  them may hold the key.
- **To keep the key from branch writers**, use one of:
  - GitHub: an environment secret, with the tag pattern `v*` as the environment's only deployment
    rule (a tag rule, not a branch rule), and a tag ruleset on `v*` that restricts creation and
    updates to the admin role.
    - Without that ruleset, any writer creates a `v*` tag, or moves an existing one onto a commit
      of their own, and reads the key [A for the move]. The default "Block force pushes" may not
      stop a move to a descendant commit [A].
    - Private repositories have environments and rulesets only on Pro, Team and Enterprise
      Cloud, not on Free.
  - GitLab: a protected variable with the environment scope `release-signing`, which only the
    sign job names (`environment: release-signing`); protected tags `v*` that only Maintainers
    may create (the API's default); and every protected branch, the default one included, with
    push and merge for Maintainers only (the default branch's default, "Fully protected").
    - Without the scope the build job gets the key as well. GitLab's documentation says: "By
      default, all CI/CD variables are available to all jobs in a pipeline", and a `v*` tag's
      pipeline runs the build job too.
    - The scope matches the name a job gives itself, so it binds only the jobs of a trusted
      `.gitlab-ci.yml`. Any job that names `release-signing`, in a pipeline on any protected
      branch or tag, gets the key. The boundary is whoever may push or merge to a protected
      branch, or create a protected branch or tag. A rule such as `develop` or `release/*` that
      lets Developers merge lets every Developer add such a job; "Allowed to merge" also
      "create[s] new protected branches through the UI and API" (GitLab's documentation) [A].
    - A protected environment `release-signing` with Maintainers as "Allowed to deploy" binds the
      job itself: "For Protected environments, you must be in the Allowed to deploy list"
      (GitLab's documentation) [A: tier and behaviour not checked].
    - Leave "Allow merge request pipelines to access protected variables and runners" cleared.
  - No key in CI, the way for private repositories on GitHub Free. CI runs only the build job
    (`--no-sign`). The builder downloads `dist/myapp`, runs `fsbpyr sign dist/myapp` on their own
    machine and uploads the signed file. A signer without fsbpyr runs step 2's `printf`, `cmp`
    and `ssh-keygen -Y sign` on `dist/myapp.statement`, and `fsbpyr sign --signature` then embeds
    the `.sig` on any machine.
- With a key in CI, the sign job runs the workflow file of the tagged commit, and whatever that
  file pulls in at run time: whoever can change either before the run can read the key. A GitLab
  `include:` without a pinned `ref:`, or a GitHub reusable workflow called at a branch, brings
  that branch's writers in; GitLab's documentation calls versionless includes unsafe. Pin both to
  a commit.
- These points rest on GitHub's and GitLab's documentation, quoted in the review; they were not
  run (§12).
- A CI key is its own key: add its line to `allowed_signers`. A login key never goes into CI.

### 3.3 Third party (goal point 2)

**What a first save trusts.** The first save trusts whoever controls the publisher's repository at
that moment, GitHub, and TLS to raw.githubusercontent.com. Later saves never replace what was
saved, and the routine check fetches nothing.

For fsbpyr this rests on GitHub settings anyone can see without logging in, except who may bypass
them (measured: rulesets 25423171 and 26124476, and `immutable=true` on all 13 releases):
- nobody can move or delete a `v*` tag (ruleset `release tags`);
- only the admin role can create one (ruleset `release tag creation`);
- a published release locks its assets and its tag. GitHub's documentation says this holds
  against the admin too, and that a deleted release's tag name cannot be reused [A].

So src/fsbpyr/allowed_signers and `tools/release/fsbpyr-verify.sh` at a tag are the same bytes for
everyone, whenever they are fetched: raw.githubusercontent.com at `refs/tags/v0.28.1/` served the
tag's exact bytes, and a missing file or tag gave 404 (measured). A changed file needs a new tag,
which `make release` stops for (§3.1). It shows in the file's public history, and everyone who
saved before gets `KEY CHANGED` or "does not match your key", except after a change of fsbpyr's
key that its recovery key confirmed (§6.5). fsbpyr's docs name the tag that introduced the current
keys, so a copy of the command made before a takeover still reads the old bytes.

Not protected:
- a first save after the organisation or its admin account was taken over;
- a first save through TLS interception;
- a user who follows instructions someone changed: release notes, the live wiki [A] and the
  README are editable by any write credential.

No scheme without a second channel covers the first. For an app, all of this holds only as far as
its builder serves allowed_signers at a protected tag; fsbpyr cannot check that.

The commands are the ones `fsbpyr build` prints (§3.2):
1. **Save fsbpyr's script once**, from fsbpyr's repository at a release tag, as
   `~/.config/fsbpyr-keys/fsbpyr-verify.sh`. It is the same script for every app, and the app's
   publisher cannot change it. Its address is exactly
   `https://raw.githubusercontent.com/devitops-com/fsbpyr/refs/tags/vX.Y.Z/tools/release/fsbpyr-verify.sh`,
   and its bytes are those of that release's asset (§3.1). Starting with that text is not
   enough: a `../` or `%2e%2e` segment after it took curl to another organisation's file
   (measured, curl 8.14.1).
   - The setup downloads it only when none is saved (`[ -f "$s" ] ||`). A re-run, or a second
     app's paste, touches nothing, so an older app's lines cannot downgrade the script and a newer
     one cannot swap it.
   - The download must be non-empty (`[ -s ]`): a 0-byte file passes `sh -n`.
   - It must pass `sh -n`. The script is one `{ … }` group with no shebang (§5), so a cut-off
     copy fails.
   - Only then `mv` gives it the saved name. The existence test has already decided, so no hard
     links are needed, and the first `rm -f` clears a leftover `.new`. If it was not saved, the
     paste prints `NOT SAVED: fsbpyr-verify.sh could not be downloaded, see the line above` after
     curl's or sh's own line ("curl: not found", a 404, or sh's syntax error for a cut-off copy),
     saves no key and exits 1 (measured, below).
   - Run it by that path only. A `fsbpyr-verify.sh` among an app's downloads, fetched with
     `curl -O`, replaced a checked copy in the download directory, and the replacement printed
     VERIFIED (measured).
   - A key file cannot take its name: project names have no `.` (§4.1).
   - A newer script comes only through
     `sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh --save-script vX.Y.Z` (§5), never by deleting the
     saved one: the setup would then take whatever URL a page shows. A fix reaches users only
     that way; the release's CHANGELOG entry says when the script changed and gives the command.
2. **Save the publisher's key once**, through `--save`, from the URL the publisher gives, which
   should name a release tag of their repository, never from the download page. It is saved as
   `~/.config/fsbpyr-keys/<project>`. `--save` checks the download before it saves it:
   - https only: the URL must start with `https://`, and curl runs with
     `--proto =https --proto-redir =https --max-filesize 65536`;
   - at most 64 KiB: `--max-filesize 65536`, a `wc -c` count, and curl in a subshell under
     `ulimit -f 128`, because curl 7.61.1 (EL8) ignores `--max-filesize` for a body sent without
     a length (measured, below);
   - §5's KEYS rules;
   - the file ends in an LF;
   - every blob is a well-formed ed25519 or sk-ed25519 key;
   - every line names NAME and its namespace;
   - `--save fsbpyr` takes only fsbpyr's own URL, at a `vN.N.N` tag (§5);
   - `--save fsbpyr` requires exactly one `sk-ssh-ed25519@openssh.com` line, the recovery key: a
     key file cut cleanly after K's line passes every other rule, and a save without R could
     follow no key change.

   It saves only under a free name, compares by SHA-256, and prints the `--list` lines whenever
   it read a key file (saved, already saved, KEY CHANGED): principal, namespace, type,
   fingerprint and comment, one line per key. If the publisher names a second place, compare:
   the same lines, and no more. `--list` and the check refuse any line that is not a plain key
   line, and any byte that is not printable ASCII (§5).
   `ssh-keygen -lf` skips such a line silently, while `ssh-keygen -Y verify` accepts some of
   them: a key added that way was missing from the fingerprints and gave VERIFIED (measured on
   10.0p2). `--list` also lists sk keys on EL8, where `ssh-keygen -lf` (OpenSSH 8.0) prints
   nothing for them (measured).

   Keep the file: the routine check fetches no key. A changed file is an attack or a key change.
   For an app, only after the publisher confirms it, delete the file and paste the setup again;
   that save trusts the repository afresh. For fsbpyr, `--save` follows a change that R confirmed
   (§6.5).
3. **Run** `sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh ~/.config/fsbpyr-keys/myapp myapp` for each
   download, in the directory you saved it to, and run the file only if it says VERIFIED.

**For a careful user.** With gh 2.81 or later, logged in, give the tag the saved script came
from: the one in the script URL of the setup you pasted, or of your last `--save-script`. An
app's setup names the fsbpyr version that built the app:
```
gh release verify-asset v0.29.0 ~/.config/fsbpyr-keys/fsbpyr-verify.sh -R devitops-com/fsbpyr
```
- A tag whose script has other bytes fails, so a failure at another tag says nothing.
- It adds a record of the script's bytes that GitHub signed, not the organisation, timestamped
  when the release was published. That also covers TLS interception at the save.
- It adds no protection after a takeover: the attacker's release is attested the same way. It does
  not check allowed_signers, which is not an asset.
- Measured on v0.28.1's assets: the real file passed, a changed one failed, and a renamed copy
  passed. Packaged gh is 2.46.0 on Debian 13 and Ubuntu 26.04 and 2.45.0 on Ubuntu 24.04, too old;
  Alpine has 2.97.0. Without a login gh exits 4.

The wiki adds two things, in prose. One is the release's lock, seen without gh:
`curl -s https://api.github.com/repos/devitops-com/fsbpyr/releases/tags/v0.29.0 | grep '"immutable"'`.
The other, for builders, is their GitHub account's SSH signing keys as an optional second place.

Measured for the setup and `--save`, with a prototype of `--list` and `--save` standing in for the
script (71 lines, 78 with fsbpyr's URL rule):
- The paste as a template; filled in, it was §3.2's two pastes byte for byte, but for the last
  line, which changed after these runs (below). 22 cases, among them:
  first save, re-run, a second app, a newer script served, a changed key, a key file that is 404
  or cut off, a script that is 404, cut off with or without a length, empty or only a shebang, an
  unfilled or http KEY-URL, a wrong file for the name, the builder's `--save fsbpyr`, and the real
  v0.29.0 script URL (404 today). Each ran in 48 modes: dash, bash, zsh and fish on Debian 13 and
  Ubuntu 26.04, tcsh on Debian 13, sh and bash on AlmaLinux 8 (curl 7.61.1), and BusyBox 1.37.0
  on Alpine 3.24; each as a file and as an interactive paste (`SHELL -i < file`), flush left and
  indented 6 spaces as in §3.2. All 1056 runs gave the expected result and left no `.new` file:
  the expected exit, saved state and messages in the 968 runs of the 11 shells that take the
  paste, and a refusal in tcsh's 88. Most cases used the prototype's test switch, which the real
  script does not have (§5).
- §3.2's two pastes as then printed, against an https stand-in for raw.githubusercontent.com with
  a test CA through `CURL_CA_BUNDLE` and no test switch: 13 cases in 24 modes (the same 12 shells,
  as a file and as an interactive paste), 312 runs, each as expected, tcsh's 26 as refusals, on
  curl 7.61.1, 8.14.1, 8.18.0 and 8.23.0. Among them: an unfilled KEY-URL gave exit 2 with no
  fetch, a key 404 or cut off with a length gave exit 1, and a re-run did not fetch the script
  again.
- tcsh 6.24.13 refused the whole paste ("Unmatched '''.") and saved nothing, in all 114 runs.
- The last line: the earlier one, "did not download completely; run this again", was wrong for a
  missing curl and a 404, and exited 0, which hid the failure in CI (measured on Debian 13 and
  Alpine 3.24). The line as printed gave exit 1 without curl and on GitHub's 404, in dash and
  bash, as a file and under `-i`, where the interactive shell kept running; zsh and fish did the
  same with a slightly longer text. The 22 and 13 cases did not run with it.
- Not run: a real terminal paste (no tty, no bracketed paste); `zsh -f` with this template (it ran
  with the previous one, which differed only in the script URL); the real script. On real GitHub
  the script URL gives 404 and NOT SAVED until v0.29.0 is tagged with the file.
- The 64 KiB limit: curl 7.61.1 wrote a 20 MB chunked body with exit 0, and the prototype, without
  the `wc -c` count, saved a 65,760-byte key file; curl 8.14.1 and 8.23.0 stopped at 64 KiB.
  `ulimit -f 128` stopped curl 7.61.1 at 65,536 B under EL8's sh (bash 4.4.20), and a writer at
  65,536 B under dash 0.5.12 and BusyBox 1.37.0 sh.
- The `sh -n` rule: every byte prefix of the 5,125-byte prototype fails `sh -n` under dash,
  BusyBox and bash, except the whole file without its final LF. Earlier, every prefix of a
  4,482-byte group failed on BusyBox, dash, bash 4.4 and zsh 5.9. With a shebang first, the
  prefixes ending inside or right after it passed `sh -n` on every shell, and a comment-only file
  was saved.
- `--save fsbpyr`'s URL rule, on dash 0.5.12, BusyBox 1.37.0 and AlmaLinux 8's sh: the real form
  at `v0.29.0`, `v0.0.0` or `v10.20.30` was fetched, and every other URL got exit 2 before any
  fetch. Refused were another host, a look-alike host, `user@host`, an explicit port, another
  organisation or repository, a branch, a bare tag, tags such as `v0.29`, `v0.29.0-rc1`,
  `v00.29.0` or `0.29.0`, `../` and `%2e%2e` paths, a query or fragment, uppercase, `http://`, a
  trailing `/`, space or newline, and `KEY-URL`. Without the rule, curl fetched from another host
  for `https://raw.githubusercontent.com@…`, and turned three of the `../` and `%2e%2e` forms
  into `/refs/heads/main/…` (two with curl 7.61.1), so the rule compares the whole URL, not a
  prefix.
- The strict check refused every prefix of a one-line key file (dash, host). For a two-line file
  it refused every prefix but the clean cut between the lines.
- The `--list` prototype refused lines without `namespaces=`, with a tab or with a CR, and printed
  the same fingerprints as `ssh-keygen -lf`, on the Debian 13 host, Alpine, AlmaLinux 8 (sk keys
  included) and Ubuntu 26.04. It printed a comment's escape sequences as they were; §5's byte
  rule refused them, and NUL, DEL and UTF-8, on the Debian 13 host, Alpine, Debian 12, Ubuntu
  24.04 and AlmaLinux 9.

Rejected (measured, except where marked):
- `curl -o`, as in the third version, replaced a saved key with the changed one, and with a
  100-byte fragment after a cut-off download;
- `curl --no-clobber` (7.83 and later; EL8 has 7.61) saves to `<name>.1` and exits 0;
- `set -C` with `>` leaves an empty or partial key after a failed download;
- `mv -n` differs by version: GNU 8.30, 8.32, 9.1, 9.7, 9.10 and 9.12 and BusyBox skip with exit 0
  and no message, while GNU 9.4 (Ubuntu 24.04) exits 1 with "not replacing";
- `cmp` is missing from 8 of the 14 base images, so `--save` compares by SHA-256;
- `cut -d' ' -f3- KEYS | ssh-keygen -lf -` for the fingerprints: it skips lines silently (above);
- re-downloading the script at each setup, with a SCRIPT DIFFERS message: its advice was one the
  script could not back, and a short download was kept for good;
- a shebang or a leading comment in the script: cut prefixes pass `sh -n`;
- `ln` for the script: it needs hard links (read);
- comparing with `$(cat …)`: it drops trailing newlines (read);
- save recipes in the paste instead of `--save`: a half key file was saved for good;
- an unwrapped paste with an unquoted `--proto =https`: zsh's `=` expansion fails;
- the bare `/<tag>/` raw URL: which of a branch and a tag with the same name raw serves is
  unknown [A].

| Output | Exit | Meaning |
|---|---|---|
| `VERIFIED  myapp 1.4.2  signed by ED25519 SHA256:…` | 0 | The holder of that key signed this executable as myapp 1.4.2 |
| `NOT VERIFIED: the signature does not match your key` | 1 | Wrong key, a forged or replaced file, or a key change: do not run it. For fsbpyr's files the script prints the `--save` command for that release, which saves a new key only if the recovery key confirmed it. For an app, ask the publisher |
| `NOT VERIFIED: the security key signed without touch or PIN` | 1 | An sk signature without the touch, or for fsbpyr without the recovery key's PIN (§4.2): do not run it; ask the publisher |
| `NOT VERIFIED: the file differs from the signed one` | 1 | Changed after signing |
| `NOT VERIFIED: the signature block is malformed` | 1 | Do not run it |
| `NOT VERIFIED: not signed` | 1 | No signature; ask the publisher |
| `CANNOT CHECK HERE: the file claims signature format 2; this fsbpyr-verify.sh reads format 1` | 3 | The number is the file's own claim, so anyone who hands you a file can show this. Keep the saved script; a newer one comes only through `--save-script`, which builds fsbpyr's URL itself (§5). If the new script says the same, do not run the file |
| `line N of KEYS is not a key line` | 2 | The key file holds a line the check does not read (§5): ask the publisher |
| `CANNOT CHECK HERE: ssh-keygen cannot check signatures (OpenSSH before 8.2)` | 3 | Older OpenSSH; for EL8 see decision 2 |
| `CANNOT CHECK HERE: needs ssh-keygen: install …` | 3 | Tool missing |

Some of these messages go on, so that each is complete on its own:
- "Does not match" for fsbpyr's files, when KEYS lists the principal `fsbpyr` in
  `fsbpyr-release@devitops.com` (the user's saved fsbpyr file), the subject is `fsbpyr` or
  `fsbpyr-el8-stubs`, and the version passes the same rule as `--save fsbpyr`'s tag (§5):
  ```
  NOT VERIFIED: the signature does not match your key
    If fsbpyr changed its key in 0.35.0 or before, this saves the new key, but only if the recovery key you saved confirmed the change:
    sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh --save fsbpyr https://raw.githubusercontent.com/devitops-com/fsbpyr/refs/tags/v0.35.0/src/fsbpyr/allowed_signers
    Then check the file again; run it only if that says VERIFIED.
  ```
  The version is the file's own claim, so it can name any tag, an older one included. `--save`
  follows only a change R confirmed from the user's own file, and fsbpyr's KEY CHANGED never says
  to delete it. So a forged claim ends in "already saved, unchanged", KEY CHANGED (kept) or NOT
  SAVED, and the check that follows still fails. An app user's check never prints this hint.
- "Does not match" for apps:
  ```
  NOT VERIFIED: the signature does not match your key
    Do not run it. If the publisher confirms a new key: delete ~/.config/fsbpyr-keys/myapp and paste their setup again.
  ```
- Format 2:
  ```
  CANNOT CHECK HERE: the file claims signature format 2; this fsbpyr-verify.sh reads format 1
    The number is the file's own claim. Do not run the file, and do not delete your saved script.
    If fsbpyr's CHANGELOG names a release that reads format 2, this replaces the script with that release's, from fsbpyr's repository only:
    sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh --save-script vX.Y.Z
  ```
- Old OpenSSH, or EL8 without the EL8 path: the message gains the line
  `  See https://github.com/devitops-com/fsbpyr/wiki/User-Verifying#el8`.

These lines were not run: the prototype has no check path.

**What a pass means:** the holder of a key in *your* saved file signed this executable as this
project and version. Every byte is checked: U against the signed digest, the block byte for byte,
and the outer trailer against both. So the file also passes the launcher's checks at start
(measured with the 0.28.1 launcher; [A] for the new launcher's strict parse, §12). It does not
mean the app is safe, that it is the newest version, or that the key belongs to whom you think.
A bare key (not the publisher's `allowed_signers` line) accepts any project that key signed. The
script then adds: "check that the name above is the one you want".

`--save`, `--save-script` and the setup:

| Output | Exit |
|---|---|
| `saved ~/.config/fsbpyr-keys/myapp. From now on myapp files pass only if one of these keys signed them:`, then the `--list` lines | 0 |
| `already saved, unchanged: …`, then the lines | 0 |
| `KEY CHANGED: your saved key is kept: …`, then `  yours:` and `  the download:`, each with its lines, then `This is an attack or a new key. Ask the publisher; only if they confirm a new key, delete … and run this again.` | 1 |
| The same for fsbpyr when R did not confirm the change, but its last line is instead: `fsbpyr's recovery key did not confirm a change from your saved file to this one. Keep your saved file. If you skipped a key change, run the command of each change since your save first, in order: https://github.com/devitops-com/fsbpyr/wiki/User-Verifying#key-changes` | 1 |
| `KEY CHANGED, confirmed by the recovery key you saved: saved ~/.config/fsbpyr-keys/fsbpyr`, then the new lines (fsbpyr only) | 0 |
| `NOT SAVED: the download failed: URL` | 1 |
| `NOT SAVED: URL is not myapp's key file: <reason>` | 2 |
| `NOT SAVED: URL is not an https:// URL; use the key URL the publisher gave` | 2 |
| `NOT SAVED: URL is not fsbpyr's key URL; it is saved only from https://raw.githubusercontent.com/devitops-com/fsbpyr/refs/tags/vX.Y.Z/src/fsbpyr/allowed_signers` | 2 |
| `CANNOT SAVE: needs curl: install curl` | 3 |
| `saved ~/.config/fsbpyr-keys/fsbpyr-verify.sh of vX.Y.Z`, then the old and the new SHA-256 (`--save-script`) | 0 |
| `NOT SAVED: vX.Y.Z is not a release newer than this script's vA.B.C` (`--save-script`) | 2 |
| `NOT SAVED: fsbpyr-verify.sh could not be downloaded, see the line above`, printed by the paste | 1 |

URL stands for the URL given. The two fsbpyr rows that involve R, and the `--save-script` rows,
are design only: the prototype has neither. The row for fsbpyr's URL is reworded from the one that
ran, so that it names the URL and the tag form.

The wiki keeps the reference recipe without the script: §5's steps as plain sh, the trailer
checks and the flag rule included, and a table that maps each ssh-keygen message to its likely
cause.

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
- the sk flags, tested as bits: user presence (0x01) in every sk signature, and user
  verification (0x04) as well in `fsbpyr-release@devitops.com`, because R is made with
  `verify-required`. Among the pinned keys only R is an sk key.
  - The signer's client asks for the touch and the PIN, so only the verifier can insist on them;
    sshd requires presence by default. For R, OpenSSH also sets credProtect 3 on the token, which
    a conforming token enforces (read in sk-usbhid.c; no token tested) [A].
  - ssh-keygen checks neither bit: synthetic, valid sk signatures with flags 0x00, 0x01 and 0x04
    give `Good` (measured on 8.2 to 10.6). allowed_signers cannot ask for them: `verify-required`
    or `no-touch-required` there gives "bad options: unknown key option", and the line is skipped
    (measured on 10.0p2).
  - Other bits are ignored: Fedora's sk-dummy signs a resident key with 0x25 (measured).
  - The script and the EL8 path apply the same rule (§5).

Sizes measured: 322–407 B for ssh-ed25519, depending on the namespace length, and 379–403 B for
sk keys.

### 4.3 Embedded block ("absorb" layout)

```
| U: the unsigned executable, unchanged (stub | pad | payload | inner trailer) |
| statement | armoured signature | footer (64 B) | outer trailer (64 B) |
```
- **Footer.** 64 ASCII bytes, exactly what `printf '%-63s\n' "FSBPYR-SIGNATURE 1 $U $B"` writes:
  the fields with single spaces, then spaces up to byte 63, then an LF.
  - U and B are decimal, with no leading zeros and at most 18 digits. A leading zero would make
    POSIX `$((…))` read the number as octal (measured). bash and BusyBox ash wrap a 20-digit
    number: 2^64 + 100 gives 100 (measured).
  - B is the length of statement, signature and footer together.
  - The script, the launcher and the reader rebuild the footer from U and B and compare all
    64 bytes. `read` alone takes tabs, a leading space, or an LF inside the footer followed by
    any bytes. A prototype that copied the file's footer into its rebuilt block gave VERIFIED for
    four such footers, one with 32 bytes of the attacker's choice, and the 0.28.1 launcher
    started two of them (measured).
  - Another version number in the magic, `FSBPYR-SIGNATURE 2` for instance, is a later format:
    the script answers "CANNOT CHECK HERE" with exit 3. Nothing signs the number, so the script
    names it as the file's claim.
- **Outer trailer.** A normal v7 trailer:
  - the same `payload_offset`;
  - `payload_size` = the inner one + 64 + B;
  - the digest = the D31 of [0, S-32), where S is the file size.
- **Byte-exact block.** [U, S-64) must be exactly the statement, then the armour from
  `-----BEGIN SSH SIGNATURE-----` to `-----END SSH SIGNATURE-----` and its LF, then the footer.
  Nothing else may be there. Otherwise bytes after U could be changed and still give VERIFIED (the
  round-2 attack). The script and fsbpyr rebuild the block from its parts and compare.
- **Strict parse in the new launcher and reader.** When the 64 bytes before the outer trailer
  begin with `FSBPYR-SIGNATURE ` (17 bytes, any format number):
  - the format number must be 1; a later signer writes format 1 for builds whose launcher reads
    only format 1;
  - the footer must equal the one rebuilt from its U and B, with U + B + 64 = S;
  - a v7 inner trailer must sit at U-64, with the magic, `format_version` 7, `flags` 0 and a
    64 KiB aligned `payload_offset`, and the inner payload must end there. The 0.28.1 launcher
    reads only the outer trailer: it starts a signed file whose inner trailer has `flags` 1 or
    version 6 (measured);
  - its `payload_offset` must equal the outer one;
  - the outer `payload_size` must be the inner one + 64 + B.

  Everything is then read through the inner trailer. A mismatch exits 70 with "the signature block
  is damaged". This stops a second payload hidden in the block with the outer trailer pointed at
  it. Every signed file carries this launcher, because the launcher sits inside U, and
  `fsbpyr sign` refuses executables built by older releases.
- **The script checks the same.** `fsbpyr-verify.sh` applies the outer trailer's rules and the
  strict parse's, and recomputes the outer digest (§5, steps 5 and 8). No signature covers the
  outer trailer, but these rules leave it no free byte.
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
  in `FSBPYR-SIGNATURE `: the writer asserts it.

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

Users save it from
`https://raw.githubusercontent.com/devitops-com/fsbpyr/refs/tags/<tag>/src/fsbpyr/allowed_signers`.
The `refs/tags/` form serves the tag (measured). With the bare `/<tag>/` form, which of a branch
and a tag with the same name raw serves is unknown [A], and any write credential can create a
branch with a tag's name. fsbpyr's docs name the tag that introduced the current key set, now
v0.29.0: its bytes were fixed before any later writer or takeover, and the command stays the same
across releases.

Every change after the first signed release commits src/fsbpyr/allowed_signers.sig, except after
R is lost, when it is deleted (§3.1): R's armoured SSHSIG, in `fsbpyr-release@devitops.com`,
principal `fsbpyr`, over exactly these four lines (ASCII, LF):
```
# fsbpyr-keys 1
# subject fsbpyr
# previous <sha256 of allowed_signers at the previous v* tag>
# keys <sha256 of the new allowed_signers>
```
- The first line keeps it apart from signature statements and sums, and fsbpyr's own paths never
  read it.
- Every later tag carries the pair until the next change.
- `make keys-confirm` writes it, `make release` checks it (§3.1), and `--save fsbpyr` follows it
  (§5, §6.5).
- Its size: 403 B with a synthetic sk R, 326 B with an ed25519 stand-in (measured).
- Whether packaging puts the `.sig` into the payload is [A], harmless either way.

## 5. `fsbpyr-verify.sh`

The script is in the repository at `tools/release/fsbpyr-verify.sh` and is an asset of every
release, listed in the signed `SHA256SUMS`; users save it from the repository at a tag (§3.3). It
is POSIX sh, runs with `LC_ALL=C` and installs nothing. The check needs no network; only `--save`
downloads, with curl. By this review's prototypes it is about 190 lines of sh, plus about 80 lines
of Python 3.6 for the EL8 path (decision 2) [A: estimated; the prototype of `--list` and `--save`,
with fsbpyr's URL rule, is 78 lines].

The whole file is one `{ … }` group: its first byte is `{`, with no shebang and no leading
comment, so a cut-off copy fails `sh -n` (§3.3). It has no test switch: the prototype's
`FSBPYR_VERIFY_PROTO_TEST`, which let it fetch `file://` and `http://`, does not ship. Its tests
serve https with a test CA through `CURL_CA_BUNDLE`, which worked with the prototype on curl
7.61.1, 8.14.1, 8.18.0 and 8.23.0 (measured).

**Usage** (users run it by its saved path, §3.3):
`sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh KEYS FILE…`,
`sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh --list KEYS`,
`sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh --save NAME URL` and
`sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh --save-script vN.N.N`
- `KEYS` is the publisher's `allowed_signers` file or line, a `.pub`, or a bare key.
  - Each line of an `allowed_signers` file must be
    `<principal> namespaces="<namespace>" <type> <base64>`, optionally followed by
    ` <comment>`: single spaces, one literal principal of `[a-z0-9-]`, one literal namespace of
    `[a-z0-9.@-]`, and type `ssh-ed25519` or `sk-ssh-ed25519@openssh.com`. A `.pub` is one line
    `<type> <base64>[ <comment>]`.
  - The file holds only printable ASCII (0x20 to 0x7E) and LF, comments included. The script
    checks that on the file's bytes, `tr -d '\n -~' <KEYS | wc -c` printing 0, because sh's
    `read` drops a NUL that ssh-keygen stops at (measured).
  - Any other line, a blank or `#` line included, gives exit 2: "line N of KEYS is not a key
    line". The sh path, the EL8 path and `fsbpyr build` apply the same rule.
  - Without the line rule, a fingerprint listing can miss a key that passes: `ssh-keygen -lf`
    skips a line it cannot read and says nothing, while `ssh-keygen -Y verify` accepts
    `myapp ssh-ed25519 AAAA…` with no options, or a line with tabs (measured on 10.0p2).
  - Without the byte rule, `--list` hides a key itself: it prints the comment as it is, and
    ssh-keygen ignores the comment. An added line whose comment ended in `ESC[2K ESC[1G ESC[1A`
    erased its own output in a terminal, so `--list` showed only the publisher's line, and a
    statement signed by the added key gave `Good` (measured on 10.0p2, under dash, bash and
    BusyBox sh, in tmux).
- `--list` prints each key of KEYS on one line: principal, namespace, type, the SHA-256
  fingerprint and the comment, with `-` for the principal and the namespace of a `.pub` or a bare
  key. It computes the fingerprint itself (`base64 -d | sha256sum`, then base64), so it lists sk
  keys on EL8 too. The prototype, 20 lines for `allowed_signers` lines only, matched
  `ssh-keygen -lf` (measured, §3.3).
- `FILE` is an executable with an embedded signature, or any file listed in a signed
  `SHA256SUMS` or `SHA256SUMS-glibc2.28` beside it.
- `--save NAME URL` saves the key file at URL as `~/.config/fsbpyr-keys/NAME`, for NAME of
  `[a-z0-9-]{1,64}`. It applies §3.3 step 2's checks, saves only under a free name, compares by
  SHA-256, and prints the messages of §3.3's `--save` table.
  - `--save fsbpyr` accepts only
    `https://raw.githubusercontent.com/devitops-com/fsbpyr/refs/tags/vN.N.N/src/fsbpyr/allowed_signers`,
    each N decimal with no leading zero (0 itself is fine). It compares the whole URL, in plain
    sh with no external command, before the https check and before any fetch: 5 lines of code in
    the prototype (measured, §3.3).
  - The R rule. When the download differs from a saved `fsbpyr` file, `--save fsbpyr` fetches
    `URL.sig` and rebuilds §4.5's statement from the SHA-256 of the saved file and of the
    download. It verifies the statement against the saved file (`ssh-keygen -Y verify` with
    principal `fsbpyr` and namespace `fsbpyr-release@devitops.com`, or the EL8 path), and
    requires `Good` for an `ED25519-SK` key and §4.2's flags. Only then does `mv` replace the
    saved file; otherwise it says KEY CHANGED and keeps it.
  - It reads the flags from the `.sig` as the release guard does (§3.1): drop the header line,
    stop at the first line that starts with the footer, remove all whitespace, decode into a file
    with base64's exit status checked (plain sh has no pipefail), and take the 5th-last byte.
    ssh-keygen skips whitespace inside the base64 and ignores lines after the footer. So a parse
    that dropped only the first and last lines read another byte from a touch-only (0x01)
    signature while ssh-keygen still said `Good`: 0x07 with one space in the base64 (dash with
    GNU coreutils), 0x05 with two lines after the footer (BusyBox 1.37.0). The guard's parse read
    the real flags in both cases. ssh-keygen refuses bytes after the blob or inside the signature
    string, and a footer in the middle of a line, so the 5th-last byte of what it decoded is the
    flags byte (all measured on OpenSSH 10.0p2). An embedded signature needs none of this: step 2
    below requires canonical armour, and the block is byte-exact (§4.3).
  - The R rule is not in the prototype [A]. It was measured with stand-ins (§6.5) and in the
    guard (§3.1); on the EL8 path and with a real token it is [A].
  - When a check of fsbpyr's own file fails with "does not match your key", the script prints the
    `--save` command for that version (§3.3), but only when KEYS lists the principal `fsbpyr` in
    `fsbpyr-release@devitops.com`, the subject is `fsbpyr` or `fsbpyr-el8-stubs`, and the version
    passes the tag rule above. A looser pattern, `[0-9]+.[0-9]+.[0-9]+`, passed `00.29.0`,
    `0.029.0` and a version with a `!` in it, and without anchors `0.29.0-rc1`. Each gave a
    command that `--save` refuses, and the one with `!` failed when pasted into interactive bash
    5.2, with "event not found" (measured).
- `--save-script vN.N.N` replaces the saved script with
  `https://raw.githubusercontent.com/devitops-com/fsbpyr/refs/tags/vN.N.N/tools/release/fsbpyr-verify.sh`.
  It builds that URL itself, with `--save fsbpyr`'s tag rule, and takes only a version newer than
  the one the script carries: the release that last changed it. `make release-prep` writes that
  version into the script, and into the script URL of User-Verifying's setup (§3.2), only when
  the script changed since the previous tag, so the guard (§3.1) stops for no other release. It
  checks `[ -s ]` and `sh -n` as the setup does, prints the old and the new SHA-256, then runs
  `mv`. So a forged format-2 claim or an edited CHANGELOG can only lead to a newer fsbpyr script
  at a tag only the admin creates (§8, takeover). Not prototyped [A].

**For an executable:**
1. Read the footer with `tail -c 128 | head -c 64`. U and B must be 1 to 18 digits with no leading
   zero, the 64 bytes must equal the footer rebuilt from them (§4.3), compared by SHA-256, and
   `U + B + 64 = S` must hold. A footer `FSBPYR-SIGNATURE N` with another N gives "CANNOT CHECK
   HERE" and exit 3.
2. Cut out the statement and the armour into temporary files with `head -c` and `tail -c`, never
   with `$(…)`, which would drop the final LF. Decode the armour with `base64 -d`, which must exit
   0, and require it to be canonical, as §4.2 does: the decoded bytes, encoded again with
   `base64 | tr -d '\n' | fold -w 70` between the two armour lines, give the same SHA-256. The
   script then reads the bytes that ssh-keygen checks. ssh-keygen accepts CRLF and a space inside
   a line, and the decoders differ on them (measured):
   - GNU 8.30, 9.1 and 9.7 exit 1 at a CR or a space, after writing part of the blob;
   - uutils 0.10.0 accepts the CR and exits 1 at the space;
   - BusyBox 1.37.0 skips both and exits 0.
3. Parse the statement strictly.
4. Rebuild the block and compare it with the file's bytes [U, S-64) by SHA-256. `cmp` is not used:
   diffutils is missing from 8 of the 14 base images (measured).
5. Check both trailers against §4.3. Copy the inner one (`tail -c +$((U-63)) | head -c 64`) and the
   outer one (`tail -c 64`) into temporary files, and read them byte by byte with `od -An -tu1`.
   Each u64 is added up from its 8 little-endian bytes in `$((…))`, and its top byte must be 0.
   `od -tu8` is not used: it reads in the host's byte order, and dash and bash cannot hold a value
   of 2^63 or more (dash saturates it, bash wraps it; measured).
   - Both: the first 16 bytes are the magic `FSBPYR\0T`, `format_version` 7 and `flags` 0.
   - Inner: `payload_offset` is a multiple of 65536, and `payload_offset + payload_size + 64 = U`.
   - Outer: the same `payload_offset`, and `payload_size` = the inner one + 64 + B.
6. Take the principal and the namespace from §4.2's table. For a bare key, write a temporary
   allowed_signers line `<principal> namespaces="<namespace>" <key>` and print the warning
   from §3.3.
7. Run `ssh-keygen -Y verify` as in §4.2. It checks no sk flag, so the script applies §4.2's
   flag rule after it:
   - the `Good` line must name an `ED25519` or an `ED25519-SK` key; anything else is "malformed";
   - for `ED25519-SK` the flags are the 5th-last byte of the decoded blob
     (`tail -c 5 | od -An -tu1 -N1`). After `Good` that byte is the flags: ssh-keygen refuses
     bytes after the counter and after the signature string (measured on 8.9p1 to 10.6p1; in its
     source from 8.2 on);
   - a missing bit gives `NOT VERIFIED: the security key signed without touch or PIN`.

   With step 2's decode, this is 11 lines of sh in the prototype.
8. Require the statement's `d31`, the inner trailer's digest and the D31 recomputed over
   [0, U-32) to be equal, and the outer trailer's digest to equal the D31 recomputed over
   [0, S-32). One pass over the file gives both:
   - the 1 MiB leaves of [0, S-32) are hashed once, with `split -b 1048576 --filter=sha256sum`, or
     a `dd` loop where `split` has no `--filter`, and turned into bytes with POSIX `printf`;
   - the inner D31 reuses the digests of the full leaves before U-32, and hashes only its own last
     leaf again (none when U-32 is a multiple of 1 MiB);
   - `split` never gets empty input: uutils 0.10.0 runs its filter once on empty input (measured).

A failure in step 5 gives "the signature block is malformed", and a digest mismatch in step 8 "the
file differs from the signed one". No signature covers the outer trailer, but steps 5 and 8 leave
it no free byte. As written in the third version, steps 1–7 printed VERIFIED for 8 kinds of
damaged outer trailer that the 0.28.1 launcher refused with exit 70 (measured).

**For a listed file:** verify the sums file's signature, find the file's line by its SHA-256 (so
the local name does not matter), and print the signed name.

**Tools:**
- Before any pipeline, the script checks for `sha256sum`, `base64`, `od`, `fold`, either `split`
  with `--filter` or `dd`, and `ssh-keygen`, which the EL8 path does not need. A missing tool
  gives exit 3 with the package to install, and never a wrong digest. The 14 base images and
  BusyBox all have `base64`, `od` and `fold` (measured).
- A missing `-Y` is recognised from ssh-keygen's usage error. `ssh -V` is shown only when it
  exists.
- `--save` also needs curl; without it, it stops with exit 3 (§3.3).
- On EL8, with decision 2's fallback, step 7 runs through platform-python (the EL8 path below).

**EL8 path** (decision 2). Where `ssh-keygen` is missing or has no `-Y`, and
/usr/libexec/platform-python exists, step 7 is one `platform-python -I` call. The other steps stay
in sh. The call:
- takes the KEYS line whose principal and `namespaces=` match, and whose key is byte-equal to the
  signature's;
- parses the blob as strictly as §4.2;
- builds the signed data with hashlib, which there is EL8's libcrypto (`openssl_sha512`,
  measured), checks S < L and accepts only a return of 1 from `EVP_DigestVerify` through ctypes;
- then applies §4.2's flag rule, after the signature check as in sh.

The Python is a heredoc inside the script, so there is still one file and one SHA-256 to check.
On Alma 8 and Rocky 8 (Python 3.6.8, OpenSSL 1.1.1k) the prototype passed and refused the same
shared vectors (§13) as the shell path; Rocky 8 ran all but `space` (measured). The message
differed for the six vectors with bytes after or cut from the blob: "the signature block is
malformed" from Python, "the signature does not match your key" from sh. The script gives one
message for them on both paths.

**Several files:** one verdict line per file, then the exit code: 1 if any file is not
verified, otherwise 3 if any could not be checked, otherwise 0. Exit 2 is usage.

**Platforms:**
- Linux: the 14 supported distributions; BusyBox through the `dd` loop.
- macOS: the `dd` loop with `shasum -a 256` [A: untested].
- Windows: through WSL [A].

**Trust:**
- It comes only from fsbpyr's repository at a release tag, the same bytes as that release's asset
  (`make release-check`), never from an app's page or through `curl | sh`.
- The setup saves it only where none is saved, after `[ -s ]` and `sh -n`, and it runs by that
  path (§3.3).
- It is short enough to read.
- The CHANGELOG entry says when it changed, with the `--save-script` command.

Measured with prototypes, 29.5 MB, 15 runs each, without step 2's armour check and step 7's flag
rule (prototyped apart, §4.2):
- steps 1–8 in 0.08–0.10 s on the Debian 13 host. The two digests from one pass take as long as
  one D31 pass (medians 77 and 75 ms);
- on AlmaLinux 8, with coreutils 8.30 only, the two digests in 0.13–0.16 s, also as long as one
  pass, and steps 1–8 without the signature check in 0.16–0.18 s;
- 600 MiB, 3 runs: the two digests in 1.4–1.5 s with `split`, 1.0 s with the `dd` loop;
- at 90 edge sizes the two digests equal hashlib's, with `split` and with the `dd` loop on GNU
  coreutils 9.7 and 8.30 and uutils 0.10.0, and with the `dd` loop on BusyBox 1.37, which has no
  `split --filter`.

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
- **Third parties** keep the key file they saved (§3.3). After a key change, a file signed with
  the new key fails with "does not match your key". For fsbpyr's files the script prints the
  `--save` command for that release; for an app it says to delete the saved file once the
  publisher confirms, and to paste their setup again. For fsbpyr, `--save` replaces the saved file
  only when R confirmed the change from it (allowed_signers.sig, §4.5): the same command as the
  first save, with no new trust in GitHub. Otherwise it says KEY CHANGED and keeps the file. A
  user who skipped two changes runs the command for each change's tag in order.
  - Measured with ed25519 stand-ins for K, K2, K3 and R (OpenSSH 10.0p2): {K, R} to {K2, R} was
    accepted at 0.35.0 and at 0.36.0, and {K2, R} to {K3, R} too. Refused: 0.35.0's change
    replayed against a saved {K3, R}, a skipped change, another change's `.sig`, and a change
    signed by K (`Good`, but not R).
  - Measured with a synthetic sk R: flags 0x05 gave `Good … ED25519-SK` and were accepted, 0x01
    was refused by the flag rule, a K signature was refused (ED25519, not SK), and a replayed
    statement gave "incorrect signature". No real token was used [A].
- **Limit:** a stolen K reaches every client that still pins it, until that client updates, and
  every third party whose saved file still holds it: one who has not run the release's
  `--save fsbpyr`, or whose first save came from a tag before the change (§8). Nothing tells
  them, because the check passes. That holds for any scheme without online revocation. Release
  notes and the wiki would then tell users of older versions to update or reinstall.
- **R lost:** a K-signed release pins `{K, R2}`. allowed_signers.sig is deleted. Saved {K, R}
  files keep checking K's releases, but cannot follow the next key change, so their users save
  again (§3.1).
- **Both lost:** users reinstall with the verified recipe.
- **Rollback of fsbpyr:** the signed version must equal the tag and build_info, and be newer than
  own (self update) or equal to own (dispatch, stubs).
- **Rollback of apps** is not stopped; the script prints the signed version.
- **Freeze** (updates withheld) is not stopped.

## 7. Release pipeline

| Job | Change |
|---|---|
| build (build.yml) | none: unsigned, reproducible executables, compared in the full suite as today |
| `sign-exe` (new) | `environment: release-signing`; only SHA-pinned first-party actions; runs only coreutils, `cmp` and ssh-keygen. Writes the 8 statements with `printf` from `tail -c 32` and the tag, signs each, checks each signature against the checkout's `allowed_signers` and uploads the `.sig` files. |
| `embed` (new) | No secret; in build.yml's pinned image, so `xz` makes the same bytes. Runs `fsbpyr sign --signature` with the built fsbpyr, which checks release statements against its pinned keys in C, so the image needs no ssh-keygen. Then makes the `.xz`, the SBOM and the licences of the signed files. |
| `sbom` | moves after embed; its subject is the signed file |
| `prepare` | The first half of today's publish: green-run check, notes, `make-install.sh` (the only step that runs built code), then copies `tools/release/fsbpyr-verify.sh` from the tag's checkout into `dist/`, then writes `SHA256SUMS`, outside the directory it scans, and provenance over the same globs plus `SHA256SUMS` and `fsbpyr-verify.sh`. `FULL_SUITE_RUN` and the notes move between jobs as artifacts or outputs. |
| `sign-sums` (new) | Same environment and rules. Signs `SHA256SUMS` after `cmp` with the expected header. When the tag message ends in `(recovery)`, read through the API [A], it is skipped, and publish makes a draft for `make release-recover`. |
| `publish` | `contents: write` only. One `gh release create`, with `SHA256SUMS.sig` and `fsbpyr-verify.sh` added; a draft in recovery mode. |
| el8.yml | Gains a `sums` job (with a checkout) and a `sign` job in the same environment. Its publish adds the `.sig` and the `.xz` lines. It stays outside the release gate, and a retry gives the same `.sig`, because Ed25519 is deterministic. |
| check-release.sh | See below. |
| make release / release-check | make release gains the guard (§3.1); `make keys-confirm` and `make release-recover` are new, for key changes only |

check-release.sh changes:
- every executable and both sums files checked with the script, against the tag's
  `allowed_signers`;
- the asset sets: +2 for the main release, +1 for EL8;
- `.sig` and the script added to the download patterns at :161;
- `.sig` excluded at :170 and in :179's jq select;
- `#` lines skipped at :169, :180 and :264;
- :180 run under `LC_ALL=C`;
- the previous tag's key file fetched;
- `cmp` of the script asset with `$work/src/tools/release/fsbpyr-verify.sh`, the tag's clone
  (:122), and the asset's provenance checked as at :194;
- the release's `immutable` flag;
- the two tag rulesets, read with your gh;
- a key change since the previous tag confirmed by its `.sig` (§4.5); without one (the first
  signed release, R lost), an info line with the key lines added and removed;
- an info line when the script changed since the previous tag;
- `ssh-keygen` added to the tool list;
- version gating for releases up to 0.28.x;
- test_release.py, test_el8_workflow.py and test_check_release_el8.py follow.

**Custody, stated plainly:**
- K is an environment secret whose only deployment rule is `v*` tags. Runs on a branch, a pull
  request or a fork do not get it. A repository secret would reach every writer's branch (§3.2).
- K is as safe as every credential that can create a `v*` tag, or change what a tagged commit
  runs:
  - Creating `v*` and `el8-v*` tags is restricted to the admin role (ruleset `release tag
    creation`, 2026-10-29), and updating or deleting them is blocked for everyone (ruleset
    `release tags`). The admin role is your login and every token of yours that may write the
    repository.
  - main requires no pull request: its ruleset blocks only deletion and force pushes (measured).
    So every write credential that may write workflow files, people, GitHub Apps and tokens, can
    change release.yml before you tag, and every write credential can change what it builds.
    release.sh tags only when local main equals hub/main, so such a commit is released only after
    you pull it. The key file and the script users save are in the same position: the guard
    catches a change of them, unless the same push changes release.sh or code you run before it
    (§3.1). Restricting
    updates of main to the admin role takes every other write credential out of this (decision
    4).
  - Admins can change the environment and the rulesets.
- A writer can re-run a tag's run, or dispatch el8.yml at an existing `v*` tag. Both run that
  tag's own workflow files with its ref. So they reach K but run no new code: they sign again what
  that tag builds.
- K's jobs run no built code and no third-party action, and K lives only in `$RUNNER_TEMP`.
- A malicious release.yml at a tag, or a poisoned build, gets a validly signed release, and such
  a release.yml can also copy K out. The attestations share the first limit, but hold no
  long-lived key [A].
- Decision 4 shows the offline alternative.

## 8. Threats

Who can change what on GitHub. The rulesets, the immutable flag and main's open pushes are
measured; the rest is GitHub's documentation, read, unless marked:

| Who | Can | Cannot |
|---|---|---|
| Any write credential (person, app, token) | push main; create any branch name; delete a release; edit release notes, title and the latest flag; publish a draft; change a draft's assets [A]; push the wiki [A] | create, move or delete `v*` tags; change a published release's assets or tag |
| The admin role (today only you) | all of the above; create `v*` tags; change rulesets, the environment, and immutable releases for later releases; read K through a release job | change a published release or its tag, or reuse a deleted one's tag name [A: GitHub's documentation] |
| Stolen K | sign anything in the release namespace | deliver it on GitHub without the admin; confirm a key change |

| Threat | Result |
|---|---|
| Tampered executable, digest rewritten | **Stopped** for the script and for fsbpyr; not at run time |
| Second payload hidden in the block, outer trailer repointed | **Stopped** by the strict launcher parse (§4.3) |
| Extra bytes put into the block | **Stopped**: the block must be byte-exact |
| Stripped signature | The script says "not signed", and fsbpyr's own paths refuse. For apps the publisher must say that releases are signed |
| Project A served as project B by the same key | **Stopped** with the publisher's line (per-project namespace); with a bare key, a warning |
| Another version of the same app | **Not stopped**; the version is shown |
| Rollback of fsbpyr | **Stopped** (§6.5) |
| Secret taken through a newly created or moved `v*` tag | **Stopped**: creation is restricted to the admin role, and updates and deletion are blocked (2026-10-29) |
| Secret read by a workflow pushed to a branch | **Stopped** for K by the environment's tag rule. A builder's repository secret is open to every writer (§3.2) |
| Malicious release.yml pushed or merged into main, then tagged | **Not stopped**: it gets a signed release and can copy K out (§7); the attestations share only the first. **Stopped** for every credential but yours if updates of main are restricted to the admin role (decision 4) |
| devitops-com organisation or its admin account taken over | **Not stopped** for new tags: a release job reads K, and releases signed with K pass for every fsbpyr and every saved {K, R}; a first save from a new tag gets the attacker's key and script. **Stopped** for saved keys and scripts, which are not replaced, and for key changes, which need R, except for a user who runs `--save-script` with a new tag of the attacker's, for instance after a file that claims format 2 (§5). Existing tags and releases keep their bytes [A]. Recovery: the R-signed release (§6.5) |
| Key line or script change pushed to main by a write credential, then released | **Caught** by `make release`, which prints the commits and lines and refuses until R confirms the key change or you approve the files' SHA-256 (§3.1, measured in a clone); not if the same push also changes release.sh, the Makefile, or code you run locally before `make release` (§3.1). **Stopped** for every credential but yours with decision 4 |
| Outer trailer changed (no signature covers it) | **Stopped**: the script leaves it no free byte (§5 steps 5 and 8), and the launcher refuses it |
| sk signature made without the touch or R's PIN | **Stopped** by the flag rule in fsbpyr, the script and the EL8 path (§4.2) |
| Stolen K | **Partly**: R signs the replacement; a saved key follows only a change R confirmed, so K cannot move a saved key; old clients are exposed until they update. **Not stopped** for a first save from a tag before the change, such as an old copy of §3.2's paste or a builder's CI that saves on every run: `--save fsbpyr` takes every `vN.N.N` tag, so the save holds K, and files signed with it pass with nothing to prompt the user. In CI, commit the saved file and run only the check; a later key change then fails there and prints the hint |
| An old fsbpyr key change replayed against a newer saved key, or a change confirmed by a stolen K | **Stopped**: the statement names the previous file, which the script takes from the user's own copy, and only an sk signature with touch and PIN counts (measured with stand-ins, §6.5) |
| Stolen builder key | **Not stopped** until the publisher replaces the line; a FIDO key makes theft hard |
| Malicious mirror, TLS interception, for fsbpyr's own downloads and for checks with a saved key | **Stopped**, except freeze |
| TLS interception or GitHub serving other bytes at a first save | **Not stopped**. With gh, `gh release verify-asset` checks the saved script against GitHub's release attestation (§3.3) |
| Key file replaced among the downloads, or in the publisher's repository after it was saved | **Stopped**: saved once, outside the download directory; the routine check fetches no key; `--save` never replaces a saved key, except fsbpyr's after a change the saved recovery key confirmed (§6.5) |
| fsbpyr's key file changed in its repository before a user's first save | **Not stopped** for that user (§3.3). It needs a new tag, which only the admin creates and `make release` stops for unless R confirmed it or you approved its SHA-256. It shows in the file's public history and reaches everyone who saved before only as KEY CHANGED or "does not match your key". Existing tags keep their bytes [A: against the admin] |
| An app's key file changed in the builder's repository before a user's first save | **Not stopped**: trust on first use. The same as fsbpyr only when the builder serves the file at a protected tag; a branch URL can change at any time. Caught at the builder's optional second place, if the user compares. `--save` refuses any line `--list` would not show, and any byte that is not printable ASCII (§5) |
| An app's page has users save "fsbpyr" from another URL | **Stopped** when the user's saved script runs it: `--save fsbpyr` takes only fsbpyr's own URL at a `vN.N.N` tag (§5; measured with the prototype). **Not stopped** for a user without a saved script, or for commands of the page's own (§3.3, "Not protected") |
| Script replaced | At an existing tag: **Stopped** (fixed bytes [A: against the admin]). In a new release: `make release` stops until you approve it, and check-release.sh compares the asset with the tag's file. After the first save: **Stopped**, because the setup never downloads the script again, so an older app's lines cannot downgrade it, and `--save-script` takes only fsbpyr's URL at a newer tag (§5). **Not stopped**: a first save from a tag made after a takeover, or from another URL an app's page prints; a URL that only starts with https://raw.githubusercontent.com/devitops-com/fsbpyr/refs/tags/ can fetch another organisation's file (`../`, `%2e%2e`; measured). A copy among the downloads does not run (§3.3) |
| File claiming a later signature format | **Not a pass**: CANNOT CHECK HERE, exit 3, names the number as the file's claim. It never says to delete the saved script; a newer one comes only through `--save-script`, at fsbpyr's own URL (§5). After a takeover, a new tag of the attacker's passes that rule (takeover row) |
| Release notes, title, latest flag, live wiki or README changed by a write credential | **Not trusted** by the recipes: they name fixed URLs at a tag, never `releases/latest`; fsbpyr's key changes only with R; the script's messages carry their whole instruction. A user who follows changed instructions is not protected |
| Release deleted, or a recovery draft published before its `.sig`, by a write credential | **Denial of service** for its assets: the per-version download (§3.2), and fsbpyr's own dispatch and stub downloads for that version (§6.3), for good if its tag cannot get a new release [A]; a release published without its `.sig` is refused as unsigned. The tag stays (ruleset), so the setup, which fetches from the tag, keeps working |
| Branch named like a release tag | **Stopped**: every URL uses `/refs/tags/` |
| Cut-off or empty download at the save | **Stopped**: curl fails on a short body when a Content-Length is sent; an empty or cut-off script fails `[ -s ]` or `sh -n`; `--save` refuses an empty key file, one without its final LF, one with a broken line, one over 64 KiB (on curl 7.61.1 through `ulimit -f` and the `wc -c` count, which the prototype lacked [A]), and fsbpyr's without its recovery key (§3.3). An app's key file cut exactly between two lines by a proxy that hides the length is saved as cut; the next save says KEY CHANGED |
| Unfilled or http KEY-URL | **Stopped**: `--save` refuses before fetching (measured) |
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
| third party | one paste per publisher, and the script saved once; the check 0.08–0.10 s per 30 MB, both digests from one pass; 0.16–0.18 s on AlmaLinux 8 without the signature check; the digests 1.0–1.5 s per 600 MiB; a bare ssh-keygen + sha256sum takes 12–99 ms depending on the distribution |
| bytes per executable | statement 116–242 B (124 B for myapp 1.4.2, measured), signature 322–407 B, footer 64 B, trailer 64 B: about 0.6–0.8 KB |
| per stub | +320 B (EVP glue), plus the `_sha256` and cut glue [A: small] |
| per release | `SHA256SUMS` +3 lines; 2 new assets (`SHA256SUMS.sig`, the script) |
| per key change | allowed_signers.sig in the repository, 326–403 B (measured, §4.5), plus one more R PIN and touch |

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
- **A second channel outside GitHub:** your ruling.
- **Sigstore or Rekor as a required channel:** it needs gh 2.81 or later, a login and a network,
  and GitHub's release attestation has no Rekor entry (measured). It stays an optional extra
  (§3.3).
- **Key continuity through a line in the recovery release's SHA256SUMS:** it works only at that
  release's URL, and a replay is not stopped.
- **The script saved from the release asset URL:** any write credential can delete a release
  (read, GitHub's REST documentation), while nobody can delete the tag.
- **A second saved name for a later format:** another permanent name with the same first-save
  trust. What matters is the URL, and the trusted script names it (§3.3).
- **fsbpyr's keys inside the script:** no step saved, and a key change could not be told from a
  script fix.
- **A self update of the script checked with K:** a takeover gets K.
- **Re-downloading the script at each setup**, as tested in the recipe round (§3.3).

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
   - *Recommended:* a fallback in the script for EL8 (§5, EL8 path). Python parses and applies
     the same flag rule as the shell path (§4.2). The Ed25519 check and the statement's SHA-512
     are EL8's own libcrypto (OpenSSL 1.1.1k), through ctypes and hashlib in
     `/usr/libexec/platform-python` 3.6, which is present wherever dnf is. The file is still
     hashed by coreutils. Measured on Rocky and Alma 8: `EVP_DigestVerify` returns 1 for a valid
     signature and 0 for a flipped one, and the prototype passed and refused the same shared
     vectors as the shell path (§5, §13).
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
   - *Recommended:* K as an environment secret that only `v*` tags reach, tag creation restricted
     to you, R offline: no signing step per release. K is then as safe as the credentials that can
     create a `v*` tag or push to main (§7). Also restrict updates of main to the admin role, a
     one-time setting. Without a second channel, the key file and the script at every new tag,
     and release.yml, rest on who can push to main, and today any write credential can
     (measured). Your merges as admin keep working [A].
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
   - *Recommended:* trust on first use from the repository (§3.3). One paste saves the script and
     the key from URLs at a tag, and the setup never replaces them. A change shows as KEY CHANGED,
     fsbpyr's key follows only changes R confirmed (decision 10), and the routine check fetches
     nothing.
   - *Alternative:* fetch the key at each release's tag. A pass then rests on write access to the
     repository at every release.
   - A second channel outside GitHub is ruled out by your ruling. Sigstore stays optional.
7. **Signatures mandatory in fsbpyr's paths** from the first signed release. *Recommended:* self
   update, dispatch and stub downloads refuse unsigned releases. *Alternative:* warn for one
   release, then refuse.
8. **Permanent names.**
   - *Recommended:* `fsbpyr-release@devitops.com` and `<project>.fsbpyr-app@devitops.com`,
     `fsbpyr-verify.sh`, `allowed_signers`, the footer magic `FSBPYR-SIGNATURE 1`, the saved
     paths under `~/.config/fsbpyr-keys/`, `fsbpyr-verify.sh --save NAME URL` and
     `--save-script vN.N.N`, which the format-2 message prints, the statement `# fsbpyr-keys 1`,
     src/fsbpyr/allowed_signers.sig, `devitops-com/fsbpyr`, the repository paths
     src/fsbpyr/allowed_signers and `tools/release/fsbpyr-verify.sh`, and the URL forms
     `https://raw.githubusercontent.com/devitops-com/fsbpyr/refs/tags/<tag>/…` for both.
   - Renaming the organisation or the repository breaks every printed recipe [A].
   - *Alternative:* a namespace without your domain, such as `<project>.fsbpyr-app`. That is
     shorter, but against OpenSSH's advice to use a domain you control.
9. **The script saves keys.**
   - *Recommended:* `fsbpyr-verify.sh --save NAME URL` (§3.3, §5): one paste, one tested
     implementation instead of a copy in every printed block, and a cut-off or oversized key file
     refused. It adds about 45 lines to the script [A; the prototype of `--list` and `--save` is
     78 lines].
   - *Alternative:* the fourth version's 7-line recipe per key in the paste. The recipe round
     measured a half key file saved for good.
10. **R confirms fsbpyr's key changes.**
    - *Recommended:* allowed_signers.sig (§4.5), at one more PIN and touch per change.
      `--save fsbpyr` follows a changed file only with it, and `make release` refuses a change
      without it unless you approve its SHA-256.
    - *Alternative:* none. Every builder then saves again on trust, at the moment a key was lost.

**Your rule, applied:** sign the stored D31 (§4.1).

**Decided while designing** (say if you disagree):
- SSHSIG with Ed25519 or Ed25519-SK only;
- every sk signature needs the touch, and fsbpyr's own also R's PIN (§4.2). App keys made with
  `-O no-touch-required` therefore cannot sign. The alternative is the rule in the release
  namespace only;
- signing by the user's own ssh-keygen, reading a file, never stdin;
- EVP in a private context, with Monocypher as the fallback;
- no self-check at start;
- each file checked once, on arrival;
- placed EL8 stubs as today;
- `inspect` checks only fsbpyr's own signatures;
- every URL that fetches a key or the script names a tag (`/refs/tags/`), never main or latest;
  the wiki links in messages are live;
- the setup never replaces a saved script;
- the script is one `{ … }` group without a shebang;
- the setup is wrapped in `sh -c '…'`;
- `--save` requires NAME's principal and namespace, and `--save fsbpyr` takes only fsbpyr's URL;
- no automatic update of the script: `--save-script vN.N.N` replaces it only when the user runs
  it, from fsbpyr's own URL at a newer tag (§5). This replaces "delete it and paste the setup
  again", which took any URL a page showed;
- the second place is optional;
- the script has no test switch.

**Known gaps (need your OK):**
- app rollback and freeze are not stopped;
- an app whose signature was stripped is caught only if users expect a signature;
- the first update from 0.28.x or older is not checked, and releases up to 0.28.x stay unsigned;
- fsbpyr itself does not handle SSH certificates or revocation lists (ssh-keygen's `-r` serves
  third parties);
- placed EL8 stubs are accepted by runtime id;
- the script on macOS and Windows (WSL) is untested;
- aarch64 timings are not measured on hardware;
- without decision 2's fallback, EL8 cannot check on the host;
- a first save after a takeover of the organisation (fsbpyr) or of the builder's repository
  (apps);
- a builder serving allowed_signers from a branch or from unprotected tags;
- a fixed script reaches a user only when they run `--save-script`;
- an app's key change, and fsbpyr's next one after R is lost, are a new first save;
- a first save of fsbpyr's key from a tag before a key change keeps the old K (§8);
- an app's key file cut cleanly between lines behind a proxy that hides the length;
- the guard lives in the repository it guards until decision 4;
- GitHub's lock against the admin is untested.

## 12. Test before building

- sk signatures from a real token, and with Fedora's `openssh-sk-dummy`, across the 12 modern
  distributions. The dummy and synthesised vectors already pass on 8.2p1, 8.7p1 and 9.9p1 to
  10.6p1. With a token: R's flags (0x05 expected) and a default key's (0x01), and whether the
  token signs R with up=false, or without the PIN, which credProtect 3 should refuse [A].
- The script's flag rule on Rocky 9 (8.7p1 and 9.9p1), AL2023, BCI 15.7, Ubuntu 24.04, Alma 10 and
  Leap 16. It passed the shared vectors on Alpine (BusyBox 1.37.0, OpenSSH 10.3p1), Debian 12,
  Ubuntu 26.04 (uutils 0.10.0) and the Debian 13 host, and all but `space` on Ubuntu 22.04,
  Fedora 44 (also with sk-dummy keys) and Arch. On 8.2p1 and 8.7p1, the vectors with trailing
  bytes must give NOT VERIFIED.
- Per-project `namespaces=` lines on OpenSSH 8.2 and 8.4.
- uutils `split --filter` (Ubuntu 26.04) with the whole script, and the `dd` fallback on macOS.
  The one-pass digests alone passed on uutils and BusyBox.
- The EL8 fallback (decision 2) on the microdnf minimal images. The 3.6 prototype, with the flag
  rule, passed the shared vectors on the standard Alma 8 image, and all but `space` on Rocky 8.
- The setup paste and §3.2's install recipe with the real script, as `fsbpyr build` and the wiki
  print them, on the 14 images
  and BusyBox; under sh, bash, zsh, `zsh -f` and fish; as a file, as an interactive paste and
  as a real terminal paste with bracketed paste; tcsh must refuse it.
  - Cases: the prototype's 22 (§3.3), plus the script cut through the EL8 heredoc. The builder's
    `--save fsbpyr` case is served over https, since the shipped script takes no other URL.
  - The tests serve https with a test CA through `CURL_CA_BUNDLE`, as the prototype's runs did.
- A unit test of the printed block:
  - KEY-URL as the only placeholder; no OWNER/REPO, no devitops.com URL, no `releases/latest` and
    no bare `/vX/`;
  - no `#` or `!`;
  - the `sh -c '…'` wrapper;
  - its own tag in the script URL;
  - the script saved only when absent, and only after `[ -s ]` and `sh -n`;
  - no fetch in the routine part.
- The script's shape test: first byte `{`, no shebang, and every byte prefix fails `sh -n` under
  dash, bash, BusyBox and zsh.
- **`--save` refusals** on every image and the EL8 path: non-https, over 64 KiB with and without
  a Content-Length, empty, no final LF, non-ASCII, wrong principal or namespace, a bad blob, a
  `.pub`, `--save fsbpyr` with every URL form refused in §3.3 and with a file without its sk
  line, and `--save-script` with a malformed tag or one not newer than its own. On curl 7.61.1 a
  body without a length passes `--max-filesize` (measured), so the `wc -c` count and
  `ulimit -f` must hold it.
- **R-confirmation vectors** in `tests/support/sshsig_vectors.py`, on the sh and EL8 paths:
  - accepted: a change, and the same change at a later tag;
  - refused: a replay, a skipped change, a K signature, another change's sig, flags 0x01 and
    0x00, a missing `.sig`, and a 0x01 signature with whitespace in the base64 or lines after
    the footer;
  - also with a real token, on OpenSSH 8.2, 8.7 and 10.x.
- The hint lines and the format-2 and EL8 messages with the real script; the prototype has no
  check path.
- **The guard in test_release.py**: the 31 cases measured in a disposable clone (§3.1), under
  `set -euo pipefail` and with `FSBPYR_RELEASE_ASSUME_YES=1`; then once with GitHub as hub, a
  real gh and a real token. Also: a key line with ESC or CR in its comment, and a commit subject
  and author name with ESC, each of which must show; a key change together with a script change,
  where the stop must name both files; a `.sig` changed or deleted alone; a local tag at HEAD and
  `git replace` refs; a release key line changed, which gives the recovery tag message, while
  an sk line changed (R lost) does not; and `make release RECOVERY=0` with both lines changed.
- **check-release.sh's new checks** on a real release.
- **On a scratch repository, as admin:**
  - an admin who deletes the rulesets and an immutable release cannot reuse its tag name;
  - raw with a branch named like a tag;
  - a non-admin token editing a draft asset, and pushing the wiki;
  - "restrict updates" on main refuses a non-admin push while admin merges work;
  - release.yml reads `(recovery)` from the tag message through the API and makes a draft.
- `--list` and the KEYS rules (§5) against hidden lines: no `namespaces=`, extra options, tabs, a
  CR, two spaces, `#` and blank lines, wildcards or commas in the principal or the namespace,
  and comments with ESC, NUL, DEL or UTF-8. Each must give exit 2, in sh and in the EL8 path, and
  the fingerprints must equal `ssh-keygen -lf`'s where that prints any.
- The footer rule (§4.3) against tabs, a leading space, an LF inside the footer, padding other
  than spaces, a leading zero and 19 or more digits: the script must say NOT VERIFIED and the new
  launcher must exit 70.
- The script's trailer checks against shared negative vectors in `tests/support/edges.py`, used
  by the script's test and the launcher's: each outer field (magic, version, flags,
  `payload_offset`, `payload_size`, digest) changed, with and without the outer digest
  recomputed; the outer trailer dropped; signed files whose inner trailer has `flags` 1,
  version 6, an unaligned `payload_offset`, or a payload that does not end at U-64. The script
  must say NOT VERIFIED and the new launcher must exit 70. Prototyped for the script with 13 of
  them on the Debian 13 host, AlmaLinux 8, Alpine and Ubuntu 26.04; the 0.28.1 launcher refuses
  all but the inner `flags` and version cases.
- The one-pass digests against `tree_digest` in the script's test, across the 14 images and
  BusyBox, at U-32 a multiple of 1 MiB and ±1, the outer tail across a 1 MiB boundary, S-32 a
  multiple of 1 MiB, and files under 1 MiB, each with `split` and with the `dd` loop. Prototyped
  at 90 sizes on GNU coreutils 9.7 and 8.30, uutils 0.10.0 and BusyBox 1.37.
- The strict parse's cost at start, the absorb layout under the fuzz harness, and the cut change
  under the sanitized stubs.
- EVP Ed25519 on aarch64 hardware, and an `openssl.cnf` that names an ENGINE.
- `gh secret set --env` before and after the environment exists; the ruleset bypass by role.
- With `release-signing` set up: a job naming it on a pushed branch, also one named `v…`, is
  refused; a non-admin write token cannot create a `v*` tag; a dispatch of el8.yml at a `v*` tag
  runs that tag's file. On a scratch repository, a repository secret is readable from a workflow
  pushed on a branch. On GitLab, a Developer's branch pipeline does not see a protected
  variable, a Developer cannot run a pipeline at a protected tag, and the build job of a `v*` tag
  pipeline does not see the scoped variable while the sign job does. A job naming
  `release-signing` on a protected branch that Developers may merge to does see it; with a
  protected environment it does not.

## 13. Implementation outline

1. **Launcher and format.**
   - The footer parse and the inner/outer consistency checks in payload.c and reader.py.
   - `_sha256`, `_tree_digest` with the cut, and `_ed25519_verify` in a new `sig.c`.
   - Signed cases in `tests/support/edges.py`, which shares them with the unit tests, the e2e tests
     and the fuzz corpus.
   - tests/support/sshsig_vectors.py (new): one table of SSHSIG vectors and the verdict each must
     give, as edges.py is for the format. It writes them at test time, the sk ones with `openssl
     pkeyutl -rawin` from a fixed seed and `ed` and `app-ed` with `ssh-keygen -Y sign`, so nothing
     generated is committed. `tests/probes/sshsig.c`,
     signing.py's tests, the script's test and the EL8 path's test all read it:

     | Vectors | Made as | `_ed25519_verify` | signing.py, script, EL8 path |
     |---|---|---|---|
     | `ed`, `app-ed` | `ssh-keygen -Y sign`, release and app namespace | 1 | VERIFIED |
     | `sk-05`, `sk-25` | sk, flags 0x05 and 0x25, release namespace | 1 | VERIFIED |
     | `sk-00`, `sk-01`, `sk-04` | sk, release namespace | 1 | without touch or PIN |
     | `app-sk-01`, `app-sk-05` | sk, app namespace | 1 | VERIFIED |
     | `app-sk-00`, `app-sk-04` | sk, app namespace | 1 | without touch or PIN |
     | `flip-flags` | `sk-05` with flags 0x04 after signing | 0 | does not match |
     | `trail-sig`, `trail-blob`, `ed-trail-blob` | 3 bytes after the counter or after the signature string | not reached | NOT VERIFIED |
     | `truncate`, `trunc-flags`, `ed-truncate` | 2 bytes cut, or the flags and counter cut | not reached | NOT VERIFIED |
     | `crlf`, `ed-crlf`, `wrap76`, `space`, `badb64`, `cut` | CRLF, wrapped at 76, a space in a line, a character dropped, cut at 200 B | not reached | malformed |
   - `tests/probes/sshsig.c` with RFC 8032 vectors, signatures that ssh-keygen makes at test time,
     the shared vectors, and the negative cases of §4.2–4.3. `sshsig_fuzz.c` in test_fuzz.py.
   - format.md "Signatures", which also describes the statement `# fsbpyr-keys 1` (§4.5);
     test_validation.py stays at version 7.
2. **Python.**
   - `signing.py`: statements, armour and policy, including §4.2's flag rule, which `fsbpyr build`
     also applies to its own signature.
   - The `signing-key` setting, `--signing-key`, `--sign/--no-sign`, `fsbpyr sign` with
     `--signature`.
   - `fsbpyr build`'s printed block (§3.2) and its unit test.
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
   - The one-time setup of §3.1.
   - The guard, `make keys-confirm`, the recovery tag message, which the guard sets when the
     release key line changed (read by release.yml), the draft path in release.sh and release.yml,
     the script copied from the checkout, check-release.sh's new checks, and the test_release.py
     cases.
5. **Script and docs.**
   - `tools/release/fsbpyr-verify.sh` in one `{ … }` group, with `--save`, `--save-script`, the R
     rule and the hint. `make release-prep` writes the version into it and into User-Verifying's
     script URL when the script changed (§5). Its own test runs the shared vectors and the setup
     matrix (§12) across the 14 images and BusyBox, and its EL8 path's test runs on Rocky and
     Alma 8.
   - `ssh-keygen` in `tests/support/requires.py` (no skips), openssh-client in the CI images,
     CONTRIBUTING.
   - security.md, where D31's known limit closes; a new D-record; the man page.
   - Wiki: User-Building, User-Settings, User-Installing with the verified recipe, a new
     User-Verifying page that builders link to, Dev-Security.
     - User-Verifying: "What a first save trusts", the setup, the message tables, `#key-changes`,
       the optional extras (§3.3), and `#el8`.
     - User-Building: protecting tags (GitHub rulesets, GitLab protected tags) and serving
       allowed_signers.
   - A release-notes template for a key change, with the `--save fsbpyr` command (§3.1).
   - A CHANGELOG "Added" entry, so the first signed release is the next minor.
