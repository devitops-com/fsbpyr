# Signing

fsbpyr signs a file and checks a signature by itself: nothing else has to be installed, and the
result is the same on every distribution fsbpyr runs on. A signature is two signatures, Ed25519
and ML-DSA-65, over a short statement that holds the file's SHA-256. A check passes only if both
are good: ML-DSA-65 guards against a future quantum computer, and Ed25519 against a flaw in the
young ML-DSA code. Neither costs you a step.

The signature is detached: `FILE.sig`, next to `FILE`, 3373 bytes. Whoever wants to check keeps the
two together.

## Publishing a signed app

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

## Signing in CI

**Keep the key out of the job that builds.** A build runs third-party code as your user: uv, the
build backends of your dependencies, and your app in the smoke test. `fsbpyr build` removes both
signing variables from its environment before it starts any process, so a process that prints or
logs its environment shows neither. That keeps the passphrase from code that does not look for it,
not from code that does: such code can read `/proc/PID/environ` of `fsbpyr` and of the shell that
started it, read `fsbpyr`'s memory where `kernel.yama.ptrace_scope` is 0, read the terminal, and read
the key file. So a publisher who builds code they do not fully trust signs in a separate job, on a
machine or account the build never ran on.

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

## Checking an app

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

| `fsbpyr verify` prints | Exit |
|---|---|
| `VERIFIED  myapp  (project myapp, Ed25519 + ML-DSA-65, key SHA256:…)` | 0 |
| `VERIFIED  myapp  (project myapp, listed as myapp-1.2-x86_64 in SHA256SUMS, Ed25519 + ML-DSA-65, key SHA256:…)` | 0 |
| `NOT VERIFIED: myapp: the signature does not match the trusted key of myapp` | 1 |
| `NOT VERIFIED: myapp: it is an executable of project otherapp, not of myapp` | 1 |
| `NOT VERIFIED: myapp: its build info names no valid project` | 1 |
| `NOT VERIFIED: myapp: no myapp.sig, and no signed SHA256SUMS beside it lists it` | 1 |
| `NOT VERIFIED: myapp: myapp.sig is not a signature (N bytes)` | 1 |
| `no trusted key for myapp: get the publisher's key and its fingerprint, then: fsbpyr key trust myapp KEY.pub SHA256:…` | 2 |

Exit 2 is a usage or setup error: nothing was said about the file.

A link is checked as the file it leads to: a command link of a multi-command app needs no `.sig` of
its own. Without a `FILE.sig`, a signed `SHA256SUMS` or `SHA256SUMS-glibc2.28` beside the file
that lists its SHA-256 counts, so every file that sums file lists passes, under any name. The
VERIFIED line then says what the file is listed as: if you expect a certain file, compare that
name.

**What a pass means.** The holder of the trusted key signed exactly this file, as the project
printed. It does not mean that the app is safe, that it is the newest version, or that the key
belongs to whom you think. An app does not check its own signature: whoever can change the file can
remove the check.

## Checking fsbpyr's own downloads

`fsbpyr self update`, the variants a build fetches for another Python and the EL8 stubs are checked
by themselves ([Installing](User-Installing.md#updating)). To check a download by hand, keep
`SHA256SUMS` and `SHA256SUMS.sig` of the release beside it:

```sh
fsbpyr verify --project fsbpyr fsbpyr-3.14-x86_64-linux-gnu
```

Check that the VERIFIED line names the asset you downloaded: the sums file lists every file of the
release. The keys of `fsbpyr` are pinned in fsbpyr, and `key trust` refuses that name. Your first
fsbpyr is trusted as GitHub serves it; every later one is checked by the one before it.

## Where fsbpyr does not run

Check on a machine where it does, then copy the checked file:

- EL8 (`--glibc 2.28` apps): fsbpyr itself needs glibc 2.34.
- musl, such as Alpine: fsbpyr and its apps are built for glibc only. Apps do not run there either,
  so this concerns only other files, signed with `sign --project`.
