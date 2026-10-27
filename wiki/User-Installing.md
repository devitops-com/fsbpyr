# Installing fsbpyr

fsbpyr is itself a single executable built by fsbpyr. It carries its own CPython and launcher, so
building apps with it needs no compiler. This page covers what the build machine needs, the
installer, a manual download, verifying a download, and updating.

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
per Python and target, and `SHA256SUMS-glibc2.28`, whose sums are of the unpacked stubs. A
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
  exist, and from fish's `conf.d` when fish is installed or configured.

| Option or variable | Effect |
|---|---|
| `--python X.Y` | install the fsbpyr that runs on that Python instead of 3.14's |
| `--no-modify-path`, `FSBPYR_NO_MODIFY_PATH=1` | leave shell start-up files alone |
| `FSBPYR_VERIFY=1` | also run `gh attestation verify`, and stop if it fails or `gh` is missing |
| `FSBPYR_INSTALL_DIR` | install into this directory |
| `-q/--quiet`, `-v/--verbose`, `-h/--help` | as usual |

Options go through the pipe like this:

```sh
curl -LsSf https://github.com/devitops-com/fsbpyr/releases/latest/download/install.sh | sh -s -- --python 3.13
```

A tag's own copy installs that version: `https://github.com/devitops-com/fsbpyr/releases/download/v0.27.0/install.sh`.

The shell the installer ran from does not see the change: the installer prints the `source`
line that puts the directory on its `PATH` (`source $HOME/.local/bin/env`). Until you run it or
start a new shell, call fsbpyr by its path (`~/.local/bin/fsbpyr`).

## Manually

Pick the asset for your Python and target. For Python 3.14 on x86-64:

```sh
mkdir -p ~/.local/bin
curl -fLo ~/.local/bin/fsbpyr https://github.com/devitops-com/fsbpyr/releases/latest/download/fsbpyr-3.14-x86_64-linux-gnu
chmod +x ~/.local/bin/fsbpyr
```

or the compressed copy:

```sh
curl -fL https://github.com/devitops-com/fsbpyr/releases/latest/download/fsbpyr-3.14-x86_64-linux-gnu.xz \
  | xz -d > ~/.local/bin/fsbpyr
chmod +x ~/.local/bin/fsbpyr
```

Shell completion and the man page are printed by fsbpyr itself; see
[Other commands](User-Other-Commands.md#completion-and-man).

## Verifying a download

GitHub Actions builds and attests every release asset. The attestation shows that this
repository's release workflow built the file from its tag. Check it before you run the file:

```sh
gh attestation verify ~/.local/bin/fsbpyr --repo devitops-com/fsbpyr \
  --signer-workflow devitops-com/fsbpyr/.github/workflows/release.yml
```

Then the file's own digest, which every start checks as well:

```sh
~/.local/bin/fsbpyr inspect --verify ~/.local/bin/fsbpyr
```

A release can also be rebuilt from its tag and compared byte for byte:
[`tools/release/check-release.sh`](../tools/release/check-release.sh)
(`make release-check`) does that among its checks of a published release.

## Updating

```sh
fsbpyr self update
```

asks GitHub for the latest release and, when it is newer, downloads the same Python's build of
it, checks it as `inspect --verify` does before anything runs it, and renames it over the
running executable. It refuses a build that needs a newer glibc than this machine has, naming
both versions, before anything is replaced. It never moves to another Python: run the installer
with `--python` for that. The man page and completions that were installed under the same prefix are written again.
A developer build (`fsbpyr --version` starts with `untagged`) refuses to update itself. The
update does not check the attestation; `gh attestation verify` still does.

## Versions

`fsbpyr --version` prints `X.Y.Z-<commit>`:

```console
$ fsbpyr --version
0.27.0-bdc2e0ed
```

A build from an untagged commit prints `untagged-<commit>`. What changed in each release is in
the [changelog](../CHANGELOG.md).

## Building fsbpyr from source

For contributors: `make bootstrap` takes a checkout to an installed fsbpyr. It needs `make`, uv,
and on Debian trixie the pinned Python, `curl` and `zstd`; elsewhere podman. The steps are
described at the top of
[`tools/bootstrap.sh`](../tools/bootstrap.sh);
see also the [Developer Guide](Dev-Overview.md).
