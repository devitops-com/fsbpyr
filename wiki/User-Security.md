# Security

**Security is built into fsbpyr: it is on by default, there is nothing to install and nothing to
configure.**

- Every release is signed twice, with Ed25519 and the post-quantum ML-DSA-65, and both must pass.
- The release keys are never on GitHub: GitHub builds, the maintainer signs on their own machine.
- `fsbpyr self update` checks a release against keys built into the fsbpyr you already run, before
  a byte of the download is unpacked, and it has no fallback.
- Every executable, yours and fsbpyr's own, checks its whole file before it runs.
- A build can be repeated byte for byte, and every release is attested and has an SBOM.
- You sign your own apps the same way, with one option, and your users check them with one command.
- You need not take fsbpyr's word for any of it: [four independent checks](#how-far-each-check-goes)
  are open to anyone with a basic knowledge of cryptography.

## At a glance

| fsbpyr | So that | You |
|---|---|---|
| signs every release twice; both signatures must pass | a quantum computer that breaks Ed25519, or a flaw in the young ML-DSA code, forges nothing | do nothing |
| keeps its release keys off GitHub | someone who steals the GitHub account can make a release that no fsbpyr accepts | do nothing |
| checks every update against the keys it carries | a hacked release, a mirror or TLS interception cannot install anything, and an old release cannot be replayed | run `fsbpyr self update` |
| checks its variants and EL8 stubs the same way | what a build downloads is held to the same rule | do nothing |
| checks the whole file at every start | a bit flip, a truncated download or a changed byte stops the start | do nothing |
| checks extracted native code before it loads it, in a cache only you can write | another local user cannot plant code in your app | do nothing |
| refuses set-uid, set-gid and file-capability starts | an app cannot be turned into a way to gain privileges | do nothing |
| treats arguments that look like Python as data | a sudo rule that runs your app does not become a way to run Python | do nothing |
| builds from pinned inputs and your lock | what goes into the file is what was reviewed | keep your lock |
| attests releases, ships SBOMs and rebuilds them byte for byte | you can prove where a file came from, without trusting a signature | check, if you wish |
| audits its own repository before every release | the maintainer finds tampering first, not you | do nothing |
| signs your apps on request | your users can check that a file is yours | `fsbpyr build --signing-key KEY` |
| lets anyone check it from outside | fsbpyr is not the only witness to itself | [choose a check](#how-far-each-check-goes), if you wish |

![The chain of trust of a fsbpyr release. GitHub builds and attests the commit the maintainer tagged and leaves an unsigned draft. On the maintainer's own machine the audit runs, the commit is compared everywhere, every job is checked to have run on GitHub's machines, and the release key signs SHA256SUMS; only the signature goes back. On your machine fsbpyr self update checks the sums against the keys built into it, then the .xz, then that it is the right newer asset, and renames it into place. A stolen GitHub account gets a draft nobody signs, and a release published by hand is refused.](images/security-chain.svg)

## How trust works

Trust has to start somewhere. With fsbpyr it starts at one of two places, and everything after that
is checked:

1. **You get fsbpyr from a source you trust.** Either you download a release from GitHub, and trust
   GitHub at that moment, or you read the sources and build it yourself, and trust what you read.
2. **fsbpyr checks itself.** `fsbpyr verify` on the fsbpyr you just got compares it with the signed
   `SHA256SUMS` of its release, using the keys built into it. That shows that those keys, the hashing
   and the signature check all work, on a signature made by a key that has never been on GitHub.
3. **The same procedure signs and checks your apps.** The statement, the two algorithms and the C
   code are the same, with your keys in place of the release keys. `fsbpyr sign` also checks each new
   signature with that code before it writes the file.
4. **Each later fsbpyr is checked by the one before it.** `fsbpyr self update` installs nothing but a
   release signed by the keys your fsbpyr already holds.

If your admin has done this for you, or you simply trust your download, there is nothing more to
do: go on to [Signing your own apps](#signing-your-own-apps).

## How far each check goes

`fsbpyr verify` on fsbpyr itself runs inside the file it reports on:

- **It can show** that the file is the one the release keys signed. It finds a damaged file, a wrong
  file and a file changed on GitHub, since no key on GitHub can sign a release.
- **It cannot show** that the fsbpyr running the check is genuine. A tampered fsbpyr can print the
  same `VERIFIED`, and this warning with it.

So it proves that the procedure works and the download is right, as long as your fsbpyr is genuine.
To rule out tampering, use a check that runs outside the file. Each of these stands on its own, so
use as many as the file is worth to you:

| Check | Needs | Whom you trust | Finds a tampered fsbpyr |
|---|---|---|---|
| [fsbpyr on itself](#with-fsbpyr-itself) | The network, or the release's two files | The fsbpyr that runs | No |
| [A copy you already checked](#with-fsbpyr-itself) | That copy, on this or another machine | That copy | Yes |
| [System tools](#with-system-tools) | OpenSSL 3.5 or newer, and `sha256sum` | Your system's tools, and the keys' fingerprints | Yes |
| [Attestation](#by-attestation) | The GitHub CLI, logged in | GitHub and Sigstore: no key of this project | Yes |
| [Your own build](#by-building-it-yourself) | git and uv; `make bootstrap` also fetches a compiler | The sources you read | Yes |

An app is checked with [`fsbpyr verify`](#checking-an-app). A tampered app cannot lie to that check,
since the app does not run: fsbpyr reads its bytes.

## Updating fsbpyr

```console
$ fsbpyr self update
fsbpyr: downloading fsbpyr v0.30.0
fsbpyr: updated /home/me/.local/bin/fsbpyr to fsbpyr v0.30.0
$ fsbpyr self update
fsbpyr: fsbpyr is up to date (0.30.0-1a2b3c4d; latest release v0.30.0)
```

That is all there is to it. In that one command, nothing from the download runs until all of this
has passed:

1. The release's `SHA256SUMS` is checked against the keys built into your fsbpyr: both signatures,
   Ed25519 and ML-DSA-65.
2. The `.xz` of your Python's build is checked against its line of the signed sums, before anything
   unpacks it.
3. The unpacked file must be the same asset, a newer version than yours, and the version of the
   tag it came from. Its whole file is checked.
4. It must suit this machine's glibc.
5. It is renamed over the running file, keeping its mode, so a failure at any step leaves your fsbpyr
   exactly as it was.

The man page and completions installed under the same prefix are written again; if they are not
there, you see a warning that they were left alone, which is harmless. A build fetches its variants
for another Python, and its EL8 stubs, with the same checks. An update does not check the
attestation: [`gh attestation verify`](#verifying-a-download) still does.

Your first fsbpyr is trusted as GitHub serves it; every later one is checked by the one before it.
A fsbpyr from before signing, 0.28.1 or older, runs old code for its one update to a signed
release, so that update is not checked: if that matters to you, [check the
result](#checking-an-installed-fsbpyr).

## Messages to watch for

**Normal.** Nothing to do.

| Message | Meaning |
|---|---|
| `fsbpyr: fsbpyr is up to date (…)` | There is no newer release |
| `fsbpyr: updated … to fsbpyr vX.Y.Z` | The update passed every check |
| `VERIFIED  myapp  (project myapp, Ed25519 + ML-DSA-65, key SHA256:…)` | The holder of that key signed exactly this file |
| `fsbpyr: WARNING … This check ran inside this fsbpyr …` | You checked a file of fsbpyr against files it downloaded by itself. [How far each check goes](#how-far-each-check-goes) says what that shows |
| `fsbpyr: warning: the man page or completions … were left alone` | Write them with `fsbpyr man` and `fsbpyr completion SHELL` |

**Refused.** You are protected: nothing was changed, and your fsbpyr is fine to go on using.

| Message | Meaning | What to do |
|---|---|---|
| `fsbpyr: error: new vX.Y.Z has not been installed because it has no valid release signature.` then `fsbpyr: currently installed fsbpyr version (A.B.C) has been kept unmodified.` | The release, or the file served for it, is covered by no key your fsbpyr holds. Either someone published or changed it who should not have, or you skipped a release that changed the [keys](#release-keys) | Keep the version you have. Look for an advisory; without one, check [which keys changed](#release-keys) |
| `… has not been installed because the download is not newer than this fsbpyr.` | A genuine older release was offered as a new one: a replay | Keep it. Look for an advisory |
| `… has not been installed because the download is not fsbpyr vX.Y.Z.` | A genuine file, under the wrong tag | The same |
| `… because it failed verification: …` | The file is damaged, although its signature passed | Try again later; if it stays, report it |
| `the new fsbpyr needs glibc X, this host has Y; nothing was changed.` | No threat: the release needs a newer system | Keep the version you have, or install a build for this glibc |
| `cannot read the latest release from …`, `downloading … failed: …` | The network or GitHub | Try again |

**Stop and look.** Something is wrong with a file you were about to run.

| Message | Exit | Meaning | What to do |
|---|---|---|---|
| `fsbpyr: cannot start APP: integrity check failed: the file was modified or damaged` | 70 | The file is not what was built: damage, or tampering | Do not work around it. Get it again; for fsbpyr itself, [check it](#checking-an-installed-fsbpyr) first |
| `NOT VERIFIED: …` | 1 | The signature does not pass, or there is none: [every form](#checking-an-app) | Do not run the file. Ask the publisher; for fsbpyr itself, [check it from outside](#checking-an-installed-fsbpyr) |
| `fsbpyr: …/… was modified after extraction; restoring it` | | A file in your cache changed after fsbpyr wrote it. The app goes on with the good copy | Find out what changed it: only you can write there |
| `refusing cache directory …: owned by another user`, `…: other users may write to it` | 70 | Someone else could plant code where the app loads it from | Fix the directory, or set `FSBPYR_CACHE_DIR` |
| `fsbpyr: cannot start APP: refusing a privileged start (set-uid, set-gid or file capabilities): …` | 70 | The file was given privileges, which fsbpyr does not run with | Remove them, and find out who set them |
| `dist/myapp is NOT SIGNED: …` | 0 | Your build finished, unsigned: the key could not be used | Do not publish it. Fix the key, or sign with `fsbpyr sign` |

An executable also exits 70 for reasons that are no threat, such as an instance limit: [Running a
built app](User-Running.md#refusals-exit-70) lists them all.

## Checking an installed fsbpyr

[How far each check goes](#how-far-each-check-goes) says what each of these is worth. Each needs
GitHub to be sound, and an advisory tells you if it was not. For the checks below the first, set
the version you believe you have, and your Python and target:

```sh
tag=vX.Y.Z asset=fsbpyr-3.14-x86_64-linux-gnu
exe=$(readlink -f ~/.local/bin/fsbpyr)
```

### With fsbpyr itself

With nothing beside it, fsbpyr downloads the `SHA256SUMS` and `SHA256SUMS.sig` of the release the
file records, checks them, and says what that is worth:

```console
$ fsbpyr verify ~/.local/bin/fsbpyr
fsbpyr: downloading SHA256SUMS and SHA256SUMS.sig of v0.29.0
VERIFIED  /home/me/.local/bin/fsbpyr  (project fsbpyr, listed as fsbpyr-3.14-x86_64-linux-gnu in SHA256SUMS of v0.29.0, Ed25519 + ML-DSA-65, key SHA256:ffb6…)
fsbpyr: WARNING ****************************************************************
fsbpyr: WARNING  This check ran inside this fsbpyr, with the keys built into
fsbpyr: WARNING  it, on files downloaded from GitHub.
fsbpyr: WARNING  It CAN show that the file is the one the release keys signed
fsbpyr: WARNING  for v0.29.0, so damage or a changed file is found, even if
fsbpyr: WARNING  GitHub was hacked, as long as this fsbpyr is genuine.
fsbpyr: WARNING  It CANNOT show that this fsbpyr is genuine: a tampered fsbpyr
fsbpyr: WARNING  can print the same result. To check it with tools it does not
fsbpyr: WARNING  control, read:
fsbpyr: WARNING  https://github.com/devitops-com/fsbpyr/wiki/User-Security#how-far-each-check-goes
fsbpyr: WARNING ****************************************************************
```

Without network, download the two files elsewhere and name them. Then nothing is downloaded, and
there is no warning, since you chose the files:

```sh
fsbpyr verify --checksums SHA256SUMS --signature SHA256SUMS.sig "$exe"
```

Check that the VERIFIED line names your asset: the sums file lists every file of the release. If
`fsbpyr.sig` or a sums file lies beside the file, that is what is checked, and nothing is downloaded,
so a bad one there is NOT VERIFIED even if the release is fine.

A release before 0.29.0 was not signed, so its files end in `NOT VERIFIED: …: release vX.Y.Z has no
valid release signature`. That is no sign of tampering: check such a file
[by attestation](#by-attestation), or update.

A check by **a copy you already checked**, on this machine or another, is worth more, since the
file under test takes no part in it: run that copy with the same command, naming the other file.

### With system tools

No fsbpyr takes part in this. You need OpenSSL 3.5 or newer and the release's own script and keys.
In an empty directory:

```sh
base=https://github.com/devitops-com/fsbpyr
curl -fLO "$base/releases/download/$tag/SHA256SUMS"
curl -fLO "$base/releases/download/$tag/SHA256SUMS.sig"
curl -fLO "$base/raw/$tag/tools/release/release-signing.sh"
curl -fLO "$base/raw/$tag/src/fsbpyr/release-keys/k.pub"
curl -fLO "$base/raw/$tag/src/fsbpyr/release-keys/r.pub"
/usr/bin/sha256sum k.pub r.pub
bash release-signing.sh verify SHA256SUMS k.pub r.pub
grep " $asset\$" SHA256SUMS
/usr/bin/sha256sum "$exe"
```

Three things must hold: the keys' digests are the [fingerprints below](#release-keys), the script
prints `VERIFIED`, and the last two lines show the same digest. Use the full path: a `sha256sum`
earlier on `PATH` can lie. Someone with root can fake that one too, so then copy the file to another
machine and check it there.

**With an OpenSSL older than 3.5 this is not a full verification.** A release carries two signatures,
and a check passes only if both do. ML-DSA-65 needs OpenSSL 3.5, so an older one cannot do the second
half, and `release-signing.sh` stops and says so:

| OpenSSL | For example | Ed25519 | ML-DSA-65 |
|---|---|---|---|
| 3.5 or newer | Debian 13 | Checked | Checked: a full verification |
| 3.0 to 3.4 | Debian 12, Ubuntu 24.04 | Checked | Not possible: `Could not read public key` |
| 1.1.1 | EL8 | Not possible: `unknown option -rawin` | Not possible |

`openssl version` shows yours. On an older system, do the check on a machine with OpenSSL 3.5 or
newer, a container will do, and only compare the digest here: `SHA256SUMS` and its signature are
small files, and `/usr/bin/sha256sum` works everywhere. Do not settle for Ed25519 alone: it is half
of what a release is signed with, and the half a quantum computer breaks.

This is for fsbpyr's own releases: the script knows no other project, and calls a genuine app
NOT VERIFIED. [Check an app with fsbpyr](#checking-an-app), which carries OpenSSL 3.5 itself.

### By attestation

With the [GitHub CLI](https://cli.github.com/), logged in with `gh auth login` or a `GH_TOKEN`, even
for this public repository. Name the tag: without it, this passes anything the workflow ever built,
from any tag:

```sh
gh attestation verify "$exe" --repo devitops-com/fsbpyr \
  --signer-workflow devitops-com/fsbpyr/.github/workflows/release.yml \
  --source-ref "refs/tags/$tag"
```

### By building it yourself

The most work, and you trust nothing but the sources you read. A release can be rebuilt byte for
byte, with the uv it records (`fsbpyr inspect "$exe"` names it):

```sh
git clone --branch "$tag" https://github.com/devitops-com/fsbpyr.git src
cd src
"$exe" build . --python 3.14 -o ../my-fsbpyr
/usr/bin/sha256sum ../my-fsbpyr "$exe"
```

`--python` is your asset's Python. The two digests must be the same; for v0.29.0 they were. This
build reuses the launcher and runtime of `$exe`, so it checks fsbpyr's Python code only. To trust no
fsbpyr at all, build from C and Python sources with `make bootstrap`:
[Self-hosting](Dev-Self-Hosting.md#from-c-and-python-sources-make-bootstrap) has both ways, and
[Verifying a release](../CONTRIBUTING.md#verifying-a-release) the comparison.

Your build is a whole fsbpyr: it pins the same keys and carries the same OpenSSL, so it checks
releases and signs and checks apps as the release does. If you changed a line of it, no release key
signed it, and `verify` rightly says NOT VERIFIED about it, as it does about any file nobody signed.

### If a check fails

Do not run the file, and do not use `self update`, which is its own code:

1. Install the release again. The installer does not run the old executable:
   ```sh
   curl -LsSf https://github.com/devitops-com/fsbpyr/releases/download/vX.Y.Z/install.sh | sh
   ```
2. Remove what a reinstall leaves. A poisoned file in the cache still runs under a genuine fsbpyr,
   and a key planted in the trust store still gives VERIFIED:
   ```sh
   rm -rf ~/.cache/fsbpyr ~/.config/fsbpyr/trusted
   ```
3. Add your publishers' keys again with `fsbpyr key trust`.
4. Rebuild the apps it built, and replace every signing key you used with it.
5. Whoever could write your fsbpyr has your account: look for what else they changed, such as shell
   files, SSH keys and cron jobs, or reinstall the machine.

## Release keys

fsbpyr pins the public keys that may sign a release: the release key, which signs each release on
the maintainer's own machine, then the recovery key, which stays offline. Neither is ever on GitHub.
Their fingerprints:

```text
SHA256:ffb630c522fdb1d0681f2e5027f6822d5c65b11e86539a83783382028a4efe27
SHA256:3184e5410af6ff52f3e6099c548cef0c058741288d8c8db7d0afd43d22f4806e
```

A release that changes the keys is signed so that the fsbpyr before it can still update to it. A
fsbpyr that skipped such a release refuses every later one, since it cannot tell a key change from
a forgery, and says there is no valid signature. Check here which keys changed, in which release,
then update through that release, or reinstall.

No key has changed yet.

## Signing your own apps

Four commands, and nothing to install:

```sh
fsbpyr key new myapp                             # once: myapp.key, myapp.pub and a fingerprint
fsbpyr build --signing-key myapp.key             # dist/myapp and dist/myapp.sig
fsbpyr key trust myapp myapp.pub SHA256:9f2c…    # once: all of the fingerprint from the first line
fsbpyr verify dist/myapp                         # VERIFIED
```

The last two are what your users run, with `myapp.pub` and the fingerprint from you. The rest of
this section is the detail.

fsbpyr signs a file and checks a signature by itself: nothing else has to be installed, and the
result is the same on every distribution fsbpyr runs on. A signature is two signatures, Ed25519
and ML-DSA-65, over a short statement that holds the file's SHA-256. A check passes only if both
are good: ML-DSA-65 guards against a future quantum computer, and Ed25519 against a flaw in the
young ML-DSA code. Neither costs you a step.

The signature is detached: `FILE.sig`, next to `FILE`, 3373 bytes. Whoever wants to check keeps the
two together.

### Publishing a signed app

Once, make a key, and keep `myapp.key` secret:

```sh
fsbpyr key new myapp
```

It asks for a passphrase twice, writes `myapp.key` (mode 600) and `myapp.pub`, and prints the
fingerprint. Hand out `myapp.pub` and the fingerprint by a way your users trust, such as your
project's site. How you do that is up to you, and so is keeping the key.

Then build with the key:

```sh
FSBPYR_SIGNING_KEY=~/keys/myapp.key fsbpyr build
```

It asks for the passphrase after the smoke test, and writes `dist/myapp` and `dist/myapp.sig`.
Publish both. `--signing-key KEY` does the same as the variable. A key has no `[tool.fsbpyr]`
setting: its path belongs to the machine that builds.

If the key cannot be used, because the file is missing or the passphrase is wrong, the build
prints a warning, finishes unsigned and exits 0. Signing is optional for apps:

```text
fsbpyr: WARNING ****************************************************************
fsbpyr: WARNING  dist/myapp is NOT SIGNED: cannot open /home/me/keys/myapp.key:
fsbpyr: WARNING  wrong passphrase? Unset FSBPYR_SIGNING_KEY to build unsigned
fsbpyr: WARNING  without this warning.
fsbpyr: WARNING ****************************************************************
```

A project named `fsbpyr`, or with a name over 64 characters, cannot be signed, and builds unsigned
with this warning.

`fsbpyr sign FILE` signs any file. For an executable fsbpyr built, the project comes from its
build info. For any other file, name it:

```sh
fsbpyr sign --project myapp --signing-key ~/keys/myapp.key myapp-docs.tar.gz
```

Nothing that exists is replaced. A `build` removes an old `.sig` before it replaces the output, so
that no signature stays beside a file it was not made for. Where the output is a symlink of yours,
the `.sig` goes beside the file the link leads to.

### Signing in CI

**Keep the key out of the job that builds.** A build runs third-party code as your user: uv, the
build backends of your dependencies, and your app in the smoke test. `fsbpyr build` removes both
signing variables from its environment before it starts any process, so a process that prints or
logs its environment shows neither. That keeps the passphrase from code that does not look for it,
not from code that does: such code can read `/proc/PID/environ` of `fsbpyr` and of the shell that
started it, read `fsbpyr`'s memory where `kernel.yama.ptrace_scope` is 0, read the terminal, and read
the key file. So a publisher who builds code they do not fully trust signs in a separate job, on a
machine or account the build never ran on.

**A key in CI is a key on GitHub.** Whoever takes the account can run a job that reads the secret,
and a copy stays valid until you replace the key: played against fsbpyr's own first design, that
was enough to get a backdoor signed. If your users' trust matters more than the convenience, do as
fsbpyr's own releases do: let CI build, and sign on a machine of your own. Only the `.sig` goes back.

1. The build job runs `fsbpyr build` with no signing variable set and hands `dist/myapp` on.
2. A separate job, with a released fsbpyr, writes the key from its secret to a file only its user
   can read, runs `fsbpyr sign --project myapp dist/myapp`, and removes the file.
   `FSBPYR_SIGNING_PASSPHRASE` gives the passphrase, since a job has no terminal. `--project` makes
   a build info that names another project an error.
3. A later step, without the key, checks as users will: `fsbpyr key trust myapp myapp.pub
   SHA256:…` with the `.pub` kept in your repository, then `fsbpyr verify --project myapp
   dist/myapp`. `sign` checks the new signature only against the key it signed with, so only this
   step catches a secret that holds another key.

On GitHub Actions:

```yaml
sign:
  needs: build
  runs-on: ubuntu-24.04
  steps:
    - uses: actions/download-artifact@v8
      with:
        name: myapp
        path: dist
    - name: Sign
      env:
        SIGNING_KEY: ${{ secrets.MYAPP_SIGNING_KEY }}
        FSBPYR_SIGNING_PASSPHRASE: ${{ secrets.MYAPP_SIGNING_PASSPHRASE }}
      run: |
        key="$RUNNER_TEMP/myapp.key"
        trap 'rm -f -- "$key"' EXIT
        (umask 077 && printf '%s\n' "$SIGNING_KEY" > "$key")
        fsbpyr sign --project myapp --signing-key "$key" dist/myapp
    - name: Check as users will
      run: |
        fsbpyr key trust myapp keys/myapp.pub "$(cat keys/myapp.fingerprint)"
        fsbpyr verify --project myapp dist/myapp
```

On GitLab, the key fits a File variable, whose value in the job is the path of a file that holds
it. A passphrase can be masked only if it is one line with no spaces and 8 or more characters.

`FSBPYR_SIGNING_PASSPHRASE` is meant for jobs without a terminal. Set on a shell's command line, it
stays in the shell's memory and in its history file; on a terminal, type it.

### Checking an app

Get `myapp.pub` and its fingerprint from the publisher, by a way you trust, and save the key once:

```sh
fsbpyr key trust myapp myapp.pub SHA256:9f2c…
```

All 64 digits of the fingerprint count. A shortened one is refused, and so is a file whose
fingerprint is another. Keys are saved in `${XDG_CONFIG_HOME:-~/.config}/fsbpyr/trusted/`; a
different key replaces a saved one only with `--replace`.

Then download `myapp` and `myapp.sig` and check them:

```sh
fsbpyr verify --project myapp myapp
```

`--project` makes sure the file is myapp's, and not a genuine file of another project whose key you
also trust. Without it, `verify` checks a file as the project its build info names, and prints
that name.

fsbpyr is all you need to check an app. It carries its own OpenSSL 3.5, so the version on your
system does not matter.

`verify` looks beside the file. If the signature is somewhere else, or the publisher signs a
checksums file, name it:

| You have | Run |
|---|---|
| The file's signature, elsewhere | `fsbpyr verify --project myapp --signature path/myapp.sig myapp` |
| A signed checksums file, with its `.sig` next to it | `fsbpyr verify --project myapp --checksums path/SHA256SUMS myapp` |
| Both, in different places | `fsbpyr verify --project myapp --checksums path/SHA256SUMS --signature other/SHA256SUMS.sig myapp` |

`FSBPYR_VERIFY_SIGNATURE` and `FSBPYR_VERIFY_CHECKSUMS` do the same for scripts, and an option goes
before its variable. What you name is the only thing tried: if it fails, a good signature beside the
file does not help. fsbpyr never goes looking for an app's signature: it knows where its own
releases are, not where your publisher keeps theirs.

| `fsbpyr verify` prints | Exit |
|---|---|
| `VERIFIED  myapp  (project myapp, Ed25519 + ML-DSA-65, key SHA256:…)` | 0 |
| `VERIFIED  myapp  (project myapp, listed as myapp-1.2-x86_64 in SHA256SUMS, Ed25519 + ML-DSA-65, key SHA256:…)` | 0 |
| `NOT VERIFIED: myapp: the signature does not match the trusted key of myapp` | 1 |
| `NOT VERIFIED: myapp: it is an executable of project otherapp, not of myapp` | 1 |
| `NOT VERIFIED: myapp: its build info names no valid project` | 1 |
| `NOT VERIFIED: myapp: no myapp.sig, and no signed SHA256SUMS beside it lists it` | 1 |
| `NOT VERIFIED: myapp: myapp.sig is not a signature (N bytes)` | 1 |
| `NOT VERIFIED: myapp: SHA256SUMS is not signed by the trusted key of myapp` | 1 |
| `NOT VERIFIED: myapp: the signed SHA256SUMS does not list it` | 1 |
| `cannot read path/myapp.sig: No such file or directory` | 2 |
| `no trusted key for myapp: get the publisher's key and its fingerprint, then: fsbpyr key trust myapp KEY.pub SHA256:…` | 2 |
| `myapp has no build info: name its project with --project` | 2 |

Exit 2 is a usage or setup error: nothing was said about the file. Build info is at the end of the
file, so a file changed there can end in the last line of this table, and with `--project` in NOT
VERIFIED.

A link is checked as the file it leads to: a command link of a multi-command app needs no `.sig` of
its own. Without a `FILE.sig`, a signed `SHA256SUMS` or `SHA256SUMS-glibc2.28` beside the file
that lists its SHA-256 counts, so every file that sums file lists passes, under any name. The
VERIFIED line then says what the file is listed as: if you expect a certain file, compare that
name.

**What a pass means.** The holder of the trusted key signed exactly this file, as the project
printed. It does not mean that the app is safe, that it is the newest version, or that the key
belongs to whom you think. An app does not check its own signature: whoever can change the file can
remove the check.

### Where fsbpyr does not run

Check on a machine where it does, then copy the checked file:

- EL8 (`--glibc 2.28` apps): fsbpyr itself needs glibc 2.34.
- musl, such as Alpine: fsbpyr and its apps are built for glibc only. Apps do not run there either,
  so this concerns only other files, signed with `sign --project`.

## What runs in every app

Every executable carries these, with nothing to set:

- **Its whole file is checked at every start**, with SHA-256 on up to 8 threads, before anything else
  in it is believed. [Integrity](User-Running.md#integrity) has the details, and how to check the
  digest with standard tools.
- **Native code is checked before it loads.** Extension modules and shared libraries are extracted
  to a cache whose way from `/` must be owned by you or root and writable by nobody else, and every
  process checks each file's digest before it first uses it. A changed file is reported and
  restored.
- **Privileged starts are refused:** set-uid, set-gid and file capabilities.
- **The launcher is hardened:** position-independent, with RELRO, BIND_NOW, a non-executable stack
  and a stack protector.
- **Arguments are not code.** A command line that looks like a multiprocessing child or a helper
  interpreter runs only in a child of this same executable, with the same ids, and is matched
  exactly, never executed. `python -c` through your app works only if you turn on
  [`interpreter-reexec`](User-Settings.md#interpreter-reexec).
- **Files beside an app of scripts are data.** Every module comes from the payload, so a file planted
  beside the executable does not run as code.
- **It uses no libraries of the target for its runtime.** CPython, OpenSSL and the others are inside
  the file, so a fix reaches an app only when it is rebuilt: see [Fixes](#when-something-goes-wrong).

## On the build machine

- **Inputs are pinned.** The Python runtime and each sysroot package are checked against a SHA-256,
  and dependencies are installed from your lock.
- **A project without a build system runs nothing.** A directory of scripts or a single script is
  built without running a build backend or the scripts. A packaged project does run its build
  backend and is imported in the smoke test, so build only projects you trust.
- **The output is reproducible.** The same sources and lock, built by the same fsbpyr and uv
  versions, give the same bytes.
- **Signing keys stay out of the build's processes.** `fsbpyr build` removes the signing variables
  from its environment before it starts anything.

## Proving where a file came from

### Verifying a download

GitHub Actions builds and attests every release asset. The attestation shows that this
repository's release workflow built the file from its tag. Check it before you run the file:

```sh
gh attestation verify ~/.local/bin/fsbpyr --repo devitops-com/fsbpyr \
  --signer-workflow devitops-com/fsbpyr/.github/workflows/release.yml \
  --source-ref refs/tags/vX.Y.Z
```

Then the file's own digest, which every start checks as well:

```sh
~/.local/bin/fsbpyr inspect --verify ~/.local/bin/fsbpyr
```

With `SHA256SUMS` and `SHA256SUMS.sig` of the release beside the file, a fsbpyr you already have
checks its signature:

```sh
fsbpyr verify --project fsbpyr fsbpyr-3.14-x86_64-linux-gnu
```

Check that the VERIFIED line names the asset you downloaded: the sums file lists every file of the
release. The keys of `fsbpyr` are pinned in fsbpyr, and `key trust` refuses that name.

A release can also be rebuilt from its tag and compared byte for byte: check (a) of
[Verifying a release](../CONTRIBUTING.md#verifying-a-release);
[Self-hosting](Dev-Self-Hosting.md#from-a-released-fsbpyr) shows it too.

`fsbpyr inspect` also names the runtime and, for builds that record them, the versions of the
libraries in it. Every release carries an SBOM, `fsbpyr-3.X-<target>.cdx.json`, which is attested
with its executable.

## How a release is made

So that you know what you are trusting:

1. The maintainer merges a tested commit, and the audit compares GitHub with what their own machine
   recorded: tags, releases, settings. Any difference stops the release.
2. GitHub builds the tagged commit for every Python and target, attests it, and leaves a draft.
   No key is there, and no workflow can publish.
3. On their own machine, the maintainer checks that their HEAD, GitHub's `main`, the tag and the
   attestation are one commit, and that every job of that run ran on GitHub's machines.
4. The release key signs the sums. Only the signature goes to GitHub, and the release is published,
   after which it cannot be changed.

Someone who steals the GitHub account can build what they like, and it stays an unsigned draft.
Published by hand, it is refused. This was played against a real repository, with real builds.
[The repository](Dev-Repository.md#release-keys) has the procedure, and
[`docs/signing.md`](../docs/signing.md) every decision and what it rests on.

## What this does not protect against

- **Your first download.** It is trusted as GitHub serves it, and during a hack of the repository a
  new install takes what is there. The audit is what limits how long that can last.
- **A tampered copy of fsbpyr itself.** Whoever can write the file can make it say anything,
  `VERIFIED` included. Check it [from outside](#how-far-each-check-goes).
- **Whoever can write your cache or your account.** Files already in the cache are not checked
  against a signature, and code running as you can do what you can.
- **What the app is.** A signature says who signed a file, not that it is safe. An executable runs
  what it was built from, dependencies included.
- **Held-back updates.** Someone who controls what you are told is the latest release can keep you
  on an older genuine one. They cannot install anything.
- **A stolen release key**, until you update past the release that replaces it. R signs that change,
  and the audit shows a release that is not on record.
- **GitHub itself being compromised**, as opposed to an account on it.
- **Old runtimes.** A built app keeps the runtime it was built with until you rebuild it.

## When something goes wrong

**You will hear of an incident through the usual channels:** security advisories, CVEs and mailing
lists, never through fsbpyr itself, which reaches nothing unless you run `self update`. If a release
was refused, you are protected and nothing is needed. An advisory says which versions and digests
are affected, so that you can [check yours](#checking-an-installed-fsbpyr). A fix is always a normal
install from the release page: never run anything else an alert links to.

**To report a vulnerability,** open a private
[GitHub security advisory](https://github.com/devitops-com/fsbpyr/security/advisories/new), not a
public issue. Reports are acknowledged within 7 days, and [SECURITY.md](../SECURITY.md) has the
policy.

**Fixes.** Only the latest release is supported. When a security issue is fixed in CPython or a
library linked into it, a patch release follows, within 7 days for high or critical issues and 30
otherwise. Its CHANGELOG entry names what changed and says "rebuild your executables": an app
built before it keeps the old runtime, and rebuilding it is the only fix.

**How it all works** is in the Developer Guide: [Security](Dev-Security.md) has the threat model,
every defence and where it sits in the code, and the known limits.
