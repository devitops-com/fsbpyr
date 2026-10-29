# Signed fsbpyr and signed apps: concept

Status: concept for the owner, 2026-10-29, at fadc3d9. Nothing is implemented. This is the fourth
version. It went through:
- a design panel;
- three reviews (attacker, owner alignment, usability);
- a review against the code;
- runs of every command on the 14 supported distributions;
- a final round of fact-check, attacker and critic;
- an independent design review. Its four findings were reproduced and are fixed here: the key
  save recipes (§3.2, §3.3), CI secrets (§3.2, §7), sk flags in the script (§4.2, §5) and the
  outer trailer (§5);
- two checks of those fixes (facts, attacker, usability). Their findings are fixed here too,
  among them the `--list` lines and the KEYS rules (§3.3, §5), the saved script (§3.3), the footer
  rule (§4.3) and the GitLab variable's scope (§3.2). The commands they changed ran on fewer
  distributions than the 14 (§3.3); §12 lists the rest.

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
    No step is added per release, except a line on the devitops.com page when `fsbpyr-verify.sh`
    changed (§3.1).
  - Apps: the builder, by setting `signing-key`; `fsbpyr build` then signs.
- **Who verifies, and with what.**
  - **Anyone, without fsbpyr:** they need the publisher's key, saved once, and `fsbpyr-verify.sh`,
    a short POSIX shell script that is an asset of every fsbpyr release (§5).
    - On the 12 modern distributions it needs only OpenSSH and coreutils:
      `sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh ~/.config/fsbpyr-keys/myapp myapp`.
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
5. Commit src/fsbpyr/allowed_signers (new). It is packaged into fsbpyr as the pinned keys.
6. Publish on one devitops.com page, outside GitHub:
   - the lines that `sh tools/release/fsbpyr-verify.sh --list src/fsbpyr/allowed_signers`
     prints, one per key with its fingerprint (§5);
   - the SHA-256 of each version of `fsbpyr-verify.sh`, with the first release that ships it, and
     next to it a line to paste. It checks the downloaded `.new` file, prints OK or FAILED, and
     only after OK moves the script to the path it runs from (GNU and BusyBox, measured):
     `echo "<sha256>  $HOME/.config/fsbpyr-keys/fsbpyr-verify.sh.new" | sha256sum -c && mv ~/.config/fsbpyr-keys/fsbpyr-verify.sh.new ~/.config/fsbpyr-keys/fsbpyr-verify.sh`

   The page's URL is a permanent name (decision 8), printed in full wherever the page is meant,
   because an app's users do not know fsbpyr. This doc uses https://devitops.com/fsbpyr/keys as an
   example [A].

Per release: `make release-prep`, PR, full suite, `make release`. release.yml builds, signs,
embeds and publishes (§7), and `make release-check` also checks every signature. A release that
changes `fsbpyr-verify.sh` needs one manual step: add its SHA-256 to the devitops.com page before
`make release`.

After K is lost or stolen (§6.5):
1. Make K2 with step 3, with `-C "fsbpyr release key 2"`. Delete K's line from
   src/fsbpyr/allowed_signers, commit it, and replace K's line with K2's on the devitops.com page.
2. Tag with the variable `FSBPYR_RECOVERY=1` set. release.yml then makes a draft.
3. `make release-recover` signs that draft's `SHA256SUMS` with R (PIN and touch, once), uploads
   the `.sig` and publishes.

### 3.2 App builder

**Check fsbpyr before using it** (goal point 1). Once, in two steps:
1. Download the script, then paste the check line for v0.29.0 from
   https://devitops.com/fsbpyr/keys. It must print OK, and only then moves the script into place:
   ```
   s=~/.config/fsbpyr-keys/fsbpyr-verify.sh; mkdir -p ~/.config/fsbpyr-keys; rm -f "$s.new"
   curl -LsSf https://github.com/devitops-com/fsbpyr/releases/download/v0.29.0/fsbpyr-verify.sh >"$s.new"
   ```
2. Save the key. The last command must print the lines on the page, and no more:
   ```
   k=~/.config/fsbpyr-keys/fsbpyr; rm -f "$k.new"
   curl -LsSf https://raw.githubusercontent.com/devitops-com/fsbpyr/v0.29.0/src/fsbpyr/allowed_signers >"$k.new" &&
     if ln "$k.new" "$k" 2>/dev/null; then echo "saved $k"
     elif [ "$(cat "$k")" = "$(cat "$k.new")" ]; then echo "already saved, unchanged: $k"
     else echo "KEY CHANGED: your saved key is kept. Only if https://devitops.com/fsbpyr/keys confirms the change, delete $k and run this again"; fi
   rm -f "$k.new"
   sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh --list "$k"
   ```

Then check each version's executable, from any directory. Nothing here fetches a key or a script:
```
curl -LsSfO https://github.com/devitops-com/fsbpyr/releases/download/v0.29.0/fsbpyr-3.14-x86_64-linux-gnu
sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh ~/.config/fsbpyr-keys/fsbpyr fsbpyr-3.14-x86_64-linux-gnu
```
- The key save never replaces a saved key, and a failed download saves nothing (§3.3).
- The commands carry no `#` comments: interactive zsh passes a comment on as arguments. In the
  third version, a `# compare …` after `ssh-keygen -lf -` made it fail with "Too many arguments."
  (measured).
- The CHANGELOG and the wiki come from the same GitHub repository as the release. Only the
  devitops.com page is an independent second channel.
- An installed fsbpyr checks the same way, under any name:
  `sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh ~/.config/fsbpyr-keys/fsbpyr ~/.local/bin/fsbpyr`.
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
   - checks the signature with `ssh-keygen -Y verify` and §4.2's flag rule;
   - only then replaces the output;
   - prints `fsbpyr: signed dist/myapp (myapp 1.4.2) with ED25519 key SHA256:Wm3…`. With
     `--sign`, and in `fsbpyr sign` (also with `--signature`, the CI path), the block below
     follows: what the builder publishes. Other builds add one line: "with --sign, fsbpyr also
     prints what to publish". fsbpyr cannot know where the key file is served or which second
     place the builder uses, so the users' part has KEY-URL and SECOND-PLACE to fill in:
   ```
     Publish dist/myapp. Publish these lines in a second place, outside the repository that holds
     allowed_signers, such as your website:
       myapp  myapp.fsbpyr-app@devitops.com  ED25519 SHA256:Wm3…  laptop
       myapp  myapp.fsbpyr-app@devitops.com  ED25519 SHA256:Qx7…  ci
     For your users, with KEY-URL (the raw URL of allowed_signers) and SECOND-PLACE filled in:
       Once, unless you saved it for another app, download fsbpyr's script. The check line for
       v0.29.0 from https://devitops.com/fsbpyr/keys must then print OK; only then is the script
       in place:
         s=~/.config/fsbpyr-keys/fsbpyr-verify.sh; mkdir -p ~/.config/fsbpyr-keys; rm -f "$s.new"
         curl -LsSf https://github.com/devitops-com/fsbpyr/releases/download/v0.29.0/fsbpyr-verify.sh >"$s.new"
       Once, save my key. The last command must print the lines at SECOND-PLACE, and no more:
         k=~/.config/fsbpyr-keys/myapp; rm -f "$k.new"
         curl -LsSf KEY-URL >"$k.new" &&
           if ln "$k.new" "$k" 2>/dev/null; then echo "saved $k"
           elif [ "$(cat "$k")" = "$(cat "$k.new")" ]; then echo "already saved, unchanged: $k"
           else echo "KEY CHANGED: your saved key is kept. Only if SECOND-PLACE confirms the change, delete $k and run this again"; fi
         rm -f "$k.new"
         sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh --list "$k"
       Then check each download, from any directory:
         sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh ~/.config/fsbpyr-keys/myapp myapp
       EL8 (Rocky/Alma 8): see https://github.com/devitops-com/fsbpyr/wiki/User-Verifying#el8
   ```
   - The lines to publish are what `fsbpyr-verify.sh --list` prints for the project's
     `allowed_signers` or `.pub`, so the users compare like with like.
   - An unfilled KEY-URL fails safe: curl stops with "Could not resolve host: KEY-URL", and
     nothing is saved (measured). No sample URL with OWNER/REPO is printed: github.com/OWNER is a
     real account (measured 2026-10-29), whose holder could serve a key there.
   - fsbpyr prints the devitops.com URL in full; it is not one of the builder's placeholders.

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

The commands are the ones `fsbpyr build` prints (§3.2):
1. **Save fsbpyr's script once**, from fsbpyr's release, as
   `~/.config/fsbpyr-keys/fsbpyr-verify.sh`. It is the same script for every app, and the app's
   publisher cannot change it.
   - curl writes `fsbpyr-verify.sh.new`, and only the page's check line, after OK, moves it to the
     path it runs from. A FAILED or skipped check puts no new script there: after a first save,
     every command that runs it stops with "No such file", and a re-save keeps the checked copy.
     With `curl … && mv` before the check, a replaced asset stayed at that path after FAILED, and
     the next routine check ran it and printed its VERIFIED (measured).
   - Run it by that path only. A `fsbpyr-verify.sh` among an app's downloads, fetched with
     `curl -O`, replaced a checked copy in the download directory, and the replacement printed
     VERIFIED (measured).
   - A key file cannot take its name: project names have no `.` (§4.1).
   - Saving again replaces the saved copy, an older one included: each app prints the lines of the
     fsbpyr release that built it, and the page's line for that release prints OK (measured). So
     save again only when the page announces a fixed script, from the release it names, or when
     the script answers "CANNOT CHECK HERE" for a newer format.
2. **Save the publisher's key once**, from their repository, their website or their GitHub SSH
   signing keys, never from the download page, as `~/.config/fsbpyr-keys/<project>`:
   - curl writes `<project>.new`, and `ln` gives it the saved name. `ln` refuses a name that
     exists, so a saved key is never replaced, and a failed or cut-off download saves nothing.
   - The first line removes a `<project>.new` left by an interrupted run. Left as `ln`'s hard
     link to the saved key, it let the next download replace or empty the key with no message
     (measured).
   - The commands print `saved`, `already saved, unchanged` or `KEY CHANGED`, with the path.
   - `--list` prints one line per key: principal, namespace, type, fingerprint and comment.
     Compare them with the second place the publisher names: the same lines, and no more.
     `--list` and the check refuse any line that is not a plain key line, and any byte that is
     not printable ASCII (§5). `ssh-keygen -lf`
     skips such a line silently, while `ssh-keygen -Y verify` accepts some of them: a key added
     that way was missing from the fingerprints and gave VERIFIED (measured on 10.0p2). `--list`
     also lists sk keys on EL8, where `ssh-keygen -lf` (OpenSSH 8.0) prints nothing for them
     (measured).

   Keep the file: the routine check fetches no key. A later file that fails with the saved key,
   or `KEY CHANGED`, is either an attack or a key change, which the publisher has to confirm at
   the second place. Only then delete the old file and save again.
3. **Run** `sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh ~/.config/fsbpyr-keys/myapp myapp` for each
   download, from any directory.

Measured for the saves:
- The key save with `ln`, in an earlier form that showed `ln`'s own errors and printed only
  `KEY CHANGED`, on the 14 distributions and Alpine (BusyBox), under each image's sh (dash, bash
  or BusyBox ash), under bash on the 14, and in interactive zsh 5.9 on Debian 13 and Ubuntu
  26.04. A first run saves. A run against the same, a
  changed, a missing (404) or a cut-off file keeps the saved key. A failed first run leaves no
  file.
- The commands as printed now, with a stand-in for the script's `--list`: the same results, with
  the messages above and a leftover hard link removed, under sh and bash on AlmaLinux 8, Debian 13
  and Ubuntu 26.04 (uutils `ln`), BusyBox sh on Alpine, and `zsh -f` on Debian 13 and Ubuntu
  26.04.
- The `--list` prototype refused lines without `namespaces=`, with a tab or with a CR, and printed
  the same fingerprints as `ssh-keygen -lf`, on the Debian 13 host, Alpine, AlmaLinux 8 (sk keys
  included) and Ubuntu 26.04. It printed a comment's escape sequences as they were; §5's byte
  rule refused them, and NUL, DEL and UTF-8, on the Debian 13 host, Alpine, Debian 12, Ubuntu
  24.04 and AlmaLinux 9.
- The page's check line, with the move after OK: under dash, bash and BusyBox sh on the host and
  on Alpine. A bad first save left no script at the path, and a bad re-save kept the checked
  copy.

Rejected, measured:
- `curl -o`, as in the third version, replaced a saved key with the changed one, and with a
  100-byte fragment after a cut-off download;
- `curl --no-clobber` (7.83 and later; EL8 has 7.61) saves to `<name>.1` and exits 0;
- `set -C` with `>` leaves an empty or partial key after a failed download;
- `mv -n` differs by version: GNU 8.30, 8.32, 9.1, 9.7, 9.10 and 9.12 and BusyBox skip with exit 0
  and no message, while GNU 9.4 (Ubuntu 24.04) exits 1 with "not replacing";
- `cmp` is missing from 8 of the 14 base images, so the recipe compares with the shell's `[`;
- `cut -d' ' -f3- KEYS | ssh-keygen -lf -` for the fingerprints: it skips lines silently (above).

| Output | Exit | Meaning |
|---|---|---|
| `VERIFIED  myapp 1.4.2  signed by ED25519 SHA256:…` | 0 | The holder of that key signed this executable as myapp 1.4.2 |
| `NOT VERIFIED: the signature does not match your key` | 1 | Wrong key, a forged or replaced file, or a key change: do not run it; ask the publisher (step 2) |
| `NOT VERIFIED: the security key signed without touch or PIN` | 1 | An sk signature without the touch, or for fsbpyr without the recovery key's PIN (§4.2): do not run it; ask the publisher |
| `NOT VERIFIED: the file differs from the signed one` | 1 | Changed after signing |
| `NOT VERIFIED: the signature block is malformed` | 1 | Do not run it |
| `NOT VERIFIED: not signed` | 1 | No signature; ask the publisher |
| `CANNOT CHECK HERE: the file claims signature format 2; this fsbpyr-verify.sh reads format 1` | 3 | The number is the file's own claim. Save the newest script listed on https://devitops.com/fsbpyr/keys (step 1); if it says the same, do not run the file |
| `line N of KEYS is not a key line` | 2 | The key file holds a line the check does not read (§5): ask the publisher |
| `CANNOT CHECK HERE: ssh-keygen cannot check signatures (OpenSSH before 8.2)` | 3 | Older OpenSSH; for EL8 see decision 2 |
| `CANNOT CHECK HERE: needs ssh-keygen: install …` | 3 | Tool missing |

**What a pass means:** the holder of a key in *your* saved file signed this executable as this
project and version. Every byte is checked: U against the signed digest, the block byte for byte,
and the outer trailer against both. So the file also passes the launcher's checks at start
(measured with the 0.28.1 launcher; [A] for the new launcher's strict parse, §12). It does not
mean the app is safe, that it is the newest version, or that the key belongs to whom you think.
A bare key (not the publisher's `allowed_signers` line) accepts any project that key signed. The
script then adds: "check that the name above is the one you want".

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

## 5. `fsbpyr-verify.sh`

The script is in the repository at `tools/release/fsbpyr-verify.sh` and is an asset of every
release, listed in the signed `SHA256SUMS`. It is POSIX sh, runs with `LC_ALL=C`, needs no
network and installs nothing. By this review's prototypes it is about 130 lines of sh, plus about
80 lines of Python 3.6 for the EL8 path (decision 2) [A: estimated].

**Usage** (users run it by its saved path, §3.3):
`sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh KEYS FILE…` and
`sh ~/.config/fsbpyr-keys/fsbpyr-verify.sh --list KEYS`
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
- It comes from fsbpyr's release or fsbpyr's repository at a tag, never from an app's release
  page and never through `curl | sh`.
- It reaches `~/.config/fsbpyr-keys/fsbpyr-verify.sh` only through the page's check line, and
  runs by that path (§3.3).
- It is short enough to read. The devitops.com page lists every SHA-256 it has had, each with the
  first release that ships it.

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
  the new key fails with "does not match your key". They delete the old file, save again and
  compare the `--list` lines with the devitops.com page.
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
| `sign-exe` (new) | `environment: release-signing`; only SHA-pinned first-party actions; runs only coreutils, `cmp` and ssh-keygen. Writes the 8 statements with `printf` from `tail -c 32` and the tag, signs each, checks each signature against the checkout's `allowed_signers` and uploads the `.sig` files. |
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
    you pull it.
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
| Malicious release.yml pushed or merged into main, then tagged | **Not stopped**: it gets a signed release and can copy K out (§7); the attestations share only the first |
| Outer trailer changed (no signature covers it) | **Stopped**: the script leaves it no free byte (§5 steps 5 and 8), and the launcher refuses it |
| sk signature made without the touch or R's PIN | **Stopped** by the flag rule in fsbpyr, the script and the EL8 path (§4.2) |
| Stolen K | **Partly**: R signs the replacement, and old clients are exposed until they update |
| Stolen builder key | **Not stopped** until the publisher replaces the line; a FIDO key makes theft hard |
| Malicious mirror, TLS interception | **Stopped**, except freeze |
| Key file replaced among the downloads, or in the publisher's repository after it was saved | **Stopped**: the key is saved once, outside the download directory; the routine check fetches no key, and the save never replaces a saved key (§3.3) |
| Key file replaced in the publisher's repository before the first save | **Caught** by the `--list` lines at the second place, if the user compares them; not if they don't. The script refuses any line that `--list` would not show, and any byte that is not printable ASCII, so no comment can rewrite the terminal (§5) |
| Script replaced | **Stopped** at the save: only the check line on devitops.com, after OK, puts the script at the path it runs from; not if the user moves it there by hand. A copy among the downloads does not run, because the saved copy runs by its path (§3.3) |
| File claiming a later signature format | **Not a pass**: "CANNOT CHECK HERE", exit 3, names the number as the file's claim; the newest script gives the real verdict |
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
| third party | the script 0.08–0.10 s per 30 MB, both digests from one pass; 0.16–0.18 s on AlmaLinux 8 without the signature check; the digests 1.0–1.5 s per 600 MiB; a bare ssh-keygen + sha256sum takes 12–99 ms depending on the distribution |
| bytes per executable | statement 116–242 B (124 B for myapp 1.4.2, measured), signature 322–407 B, footer 64 B, trailer 64 B: about 0.6–0.8 KB |
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
     create a `v*` tag or push to main (§7). Restricting updates of main to the admin role, a
     one-time setting, would take every other write credential out of that set; your merges as
     admin keep working.
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
   - *Recommended:* save the key once, with commands that never replace a saved key, and treat a
     change as needing confirmation (§3.3). The routine check fetches no key.
   - *Alternative:* fetch the key at each release's tag. That is simpler to document, but a pass
     then rests on write access to the publisher's repository.
7. **Signatures mandatory in fsbpyr's paths** from the first signed release. *Recommended:* self
   update, dispatch and stub downloads refuse unsigned releases. *Alternative:* warn for one
   release, then refuse.
8. **Permanent names.**
   - *Recommended:* `fsbpyr-release@devitops.com` and `<project>.fsbpyr-app@devitops.com`,
     `fsbpyr-verify.sh`, `allowed_signers`, the footer magic `FSBPYR-SIGNATURE 1`, the saved
     paths under `~/.config/fsbpyr-keys/`, and the URL of the devitops.com page (§3.1 step 6).
   - *Alternative:* a namespace without your domain, such as `<project>.fsbpyr-app`. That is
     shorter, but against OpenSSH's advice to use a domain you control.

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
- The save commands, as `fsbpyr build` and the wiki print them, with the real `--list`, in the
  script's test across the 14 images and BusyBox, under each sh, bash and interactive zsh: the
  cases measured in §3.3, a stale hard link, plus a home without hard links [A] and fish, where
  `k=…` is not valid syntax [A]. A unit test holds `fsbpyr build`'s printed text to KEY-URL and
  SECOND-PLACE, no OWNER/REPO, no `#` comment after a command, no key or script fetch in the
  routine part, the script run by its saved path, and the script at that path only after OK; a
  FAILED re-save keeps the checked copy.
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
   - format.md "Signatures"; test_validation.py stays at version 7.
2. **Python.**
   - `signing.py`: statements, armour and policy, including §4.2's flag rule, which `fsbpyr build`
     also applies to its own signature.
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
   - `tools/release/fsbpyr-verify.sh`, with its own test on the shared vectors across the 14
     images and BusyBox, and its EL8 path's test on Rocky and Alma 8.
   - `ssh-keygen` in `tests/support/requires.py` (no skips), openssh-client in the CI images,
     CONTRIBUTING.
   - security.md, where D31's known limit closes; a new D-record; the man page.
   - Wiki: User-Building, User-Settings, User-Installing with the verified recipe, a new
     User-Verifying page that builders link to (with the message table and EL8), Dev-Security.
   - A CHANGELOG "Added" entry, so the first signed release is the next minor.
