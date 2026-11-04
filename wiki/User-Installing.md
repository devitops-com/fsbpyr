# Installing fsbpyr

fsbpyr is itself a single executable built by fsbpyr. It carries its own CPython and launcher, so
building apps with it needs no compiler. This page covers what the build machine needs, which
fsbpyr to install for your Python and architecture, the installer, a manual download, updating, and
building fsbpyr from its sources. How to check what you installed is in
[Security](User-Security.md#checking-an-installed-fsbpyr).

## What the build machine needs

- Linux x86-64 or aarch64 with glibc 2.34 or newer.
- [uv](https://docs.astral.sh/uv/) on `PATH`. uv installs every project, pip projects too.
- Network access the first time: uv fetches the Python it builds with and the locked
  dependencies. Later builds work from uv's cache, or fully offline with `--offline`.
- Optionally git: with it, an executable records the commit it was built from.

fsbpyr builds apps for the platform it runs on. There are no cross builds: build aarch64 apps on
an aarch64 machine.

## One fsbpyr per Python

Each [release](https://github.com/devitops-com/fsbpyr/releases) carries one fsbpyr per Python
version and target, named after both:

| Asset | Runs on and builds for |
|---|---|
| `fsbpyr-3.14-x86_64-linux-gnu` | Python 3.14 (the default), x86-64 |
| `fsbpyr-3.12-x86_64-linux-gnu`, `-3.13-`, `-3.15-` | that Python, x86-64 |
| `fsbpyr-3.14-aarch64-linux-gnu`, and so on | that Python, aarch64 (arm64) |

You need only one. When a project needs another Python, the installed fsbpyr downloads the
matching executable of its own release once, checks it, keeps it in its cache, and hands the
build to it (see [Which Python](User-Building.md#which-python)). The release also carries, per
executable, an `.xz` copy (half the download), its licence notices
`THIRD_PARTY_LICENSES-3.X-<target>.txt`, a CycloneDX SBOM `<asset>.cdx.json`, and `SHA256SUMS`
for every file.

**The EL8 stubs** are not executables and live in a release of their own, the companion
release `el8-vX.Y.Z` of `vX.Y.Z`, on the same commit: one `fsbpyr-stub-3.X-<target>-glibc2.28.xz`
per Python and target, and `SHA256SUMS-glibc2.28`, signed as `SHA256SUMS` is, with the sums of
each stub and of its `.xz`. A
`--glibc 2.28` build downloads the one it needs by itself; on a machine without access to
GitHub, place it next to the fsbpyr executable ([Building for EL8](User-Building.md#building-for-el8-glibc-228)).
Not every release has a companion release. It is published as not the latest release, so the
installer and `fsbpyr self update`, which read the latest release, do not see it.

## The installer

```sh
curl -LsSf https://github.com/devitops-com/fsbpyr/releases/latest/download/install.sh | sh
```

`install.sh` is a POSIX `sh` script (dash and busybox work too). Each release writes its version
and the SHA-256 of every executable and `.xz` into it, with the glibc each executable needs, so
a download is checked before it is used. It:

- detects the target and stops when the release has no executable for this machine (naming the
  targets it has), none for the Python asked for (naming its Pythons), or the machine's glibc is
  older than the executable needs (naming both versions);
- downloads with `curl` (HTTPS only, TLS 1.2 or newer) or else `wget`, the `.xz` when `xz` is
  installed, checks the SHA-256, and runs `fsbpyr inspect --verify` on the new file before it
  installs it;
- installs into the first of `$FSBPYR_INSTALL_DIR`, `$XDG_BIN_HOME`, `$XDG_DATA_HOME/../bin` and
  `~/.local/bin`, and, when that directory is named `bin`, writes the man page and shell
  completions under its prefix's `share/` (`~/.local/share/man/man1/fsbpyr.1` and so on);
- when that directory is not on `PATH`, writes `env` and `env.fish` scripts next to the
  executable and sources them from `~/.profile`, from `~/.bashrc` and `~/.zshrc` when they
  exist, and from fish's `conf.d` when fish is installed or configured (in GitHub Actions it adds
  the directory to `$GITHUB_PATH` instead).

| Option or variable | Effect |
|---|---|
| `--python X.Y` | install the fsbpyr that runs on that Python instead of 3.14's |
| `--no-modify-path`, `FSBPYR_NO_MODIFY_PATH=1` | leave shell start-up files alone |
| `FSBPYR_VERIFY=1` | also check the attestation with `gh attestation verify`, and stop if it fails. Needs the [GitHub CLI](https://cli.github.com/), logged in with `gh auth login` or a `GH_TOKEN`, even for this public repository: without them it stops before it downloads anything |
| `FSBPYR_INSTALL_DIR` | install into this directory |
| `-q/--quiet`, `-v/--verbose`, `-h/--help` | as usual |

The command is the same on x86-64 and aarch64. Options go after `sh -s --`, and variables before
`sh`:

```sh
url=https://github.com/devitops-com/fsbpyr/releases/latest/download/install.sh
curl -LsSf "$url" | sh -s -- --python 3.13                  # the fsbpyr that runs on Python 3.13
curl -LsSf "$url" | FSBPYR_VERIFY=1 sh                      # also check the attestation
curl -LsSf "$url" | FSBPYR_INSTALL_DIR=/opt/fsbpyr/bin sh   # into this directory
```

A tag's own copy installs that version: `https://github.com/devitops-com/fsbpyr/releases/download/v@VERSION@/install.sh`.

The shell the installer ran from does not see the change: the installer prints the `source`
line that puts the directory on its `PATH` (`source $HOME/.local/bin/env`). Until you run it or
start a new shell, call fsbpyr by its path (`~/.local/bin/fsbpyr`).

## Manually

Set your Python, and the rest of the name follows from the machine:

```sh
python=3.14 target=$(uname -m)-linux-gnu
mkdir -p ~/.local/bin
curl -fLo ~/.local/bin/fsbpyr "https://github.com/devitops-com/fsbpyr/releases/latest/download/fsbpyr-$python-$target"
chmod +x ~/.local/bin/fsbpyr
```

or the compressed copy:

```sh
curl -fL "https://github.com/devitops-com/fsbpyr/releases/latest/download/fsbpyr-$python-$target.xz" \
  | xz -d > ~/.local/bin/fsbpyr
chmod +x ~/.local/bin/fsbpyr
```

Shell completion and the man page are printed by fsbpyr itself; see
[Other commands](User-Other-Commands.md#completion-and-man).

## Updating

```console
$ fsbpyr self update
fsbpyr: fsbpyr is up to date (0.30.0-8d35b93c; latest release v0.30.0)
```

When a newer release is out, it prints `downloading fsbpyr vX.Y.Z` and then
`updated PATH to fsbpyr vX.Y.Z`. In that one command, nothing from the download runs until all of this
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

If a step fails, [Messages to watch for](User-Security.md#messages-to-watch-for) says what the
message means and what to do.

The man page and completions installed under the same prefix are written again; if they are not
there, you see a warning that they were left alone, which is harmless. A build fetches its variants
for another Python, and its EL8 stubs, with the same checks. An update does not check the
attestation: [`gh attestation verify`](User-Security.md#by-attestation) still does.

An update never moves to another Python: run the installer with `--python` for that. A developer
build (`fsbpyr --version` starts with `untagged`) refuses to update itself.

A fsbpyr from before signing, 0.28.1 or older, runs old code for its one update to a signed
release, so that update is not checked: if that matters to you, [check the
result](User-Security.md#checking-an-installed-fsbpyr).

## Versions

`fsbpyr --version` prints `X.Y.Z-<commit>`:

```console
$ fsbpyr --version
0.30.0-8d35b93c
```

A build from an untagged commit prints `untagged-<commit>`. What changed in each release is in
the [changelog](../CHANGELOG.md).

## Building fsbpyr from source

Your build is a whole fsbpyr: it pins the same keys and carries the same OpenSSL, so it checks
releases and signs and checks apps as the release does. If you changed a line of it, no release key
signed it, and `verify` rightly says NOT VERIFIED about it, as it does about any file nobody signed.
[Self-hosting](Dev-Self-Hosting.md) in the Developer Guide has both ways below in full.

### With a released fsbpyr

This needs git and uv, and no compiler:

```sh
git clone --branch vX.Y.Z https://github.com/devitops-com/fsbpyr.git src
cd src
fsbpyr build . --python 3.14 -o ../my-fsbpyr
```

`--python` is your fsbpyr's Python. With the fsbpyr of that tag and the uv it records (`fsbpyr
inspect` names it), the result is the release, byte for byte. This build reuses the launcher and
runtime of the fsbpyr that runs it, so only fsbpyr's Python code comes from your checkout.

### From C and Python sources

To trust no fsbpyr at all, `make bootstrap` takes a checkout to an installed fsbpyr. It needs `make`,
uv, and on Debian trixie the pinned Python, `curl` and `zstd`; elsewhere podman. The steps are
described at the top of [`tools/bootstrap.sh`](../tools/bootstrap.sh).
