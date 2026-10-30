# Other commands

Besides `build` and `inspect`, fsbpyr has commands for its caches, its runtimes, updating itself,
shell completion and the man page. This page describes each one.

```console
$ fsbpyr --help
Usage: fsbpyr [OPTIONS] [COMMAND] [ARGS]...

  Package Python CLI projects as fast single-file executables.

Options:
  --version   show the version and exit
  -h, --help  Show this message and exit.

Commands:
  build       build a single-file executable from a project
  cache       manage fsbpyr's caches
  completion  print the shell completion script for SHELL
  inspect     show what an executable was built from
  man         print the man page (roff)
  runtime     manage runtimes (native stub + stdlib)
  self        manage this fsbpyr executable
```

## `fsbpyr key`, `sign` and `verify`

```sh
fsbpyr key new NAME
fsbpyr key trust PROJECT PUBKEY FINGERPRINT [--replace]
fsbpyr sign FILE [--project P] [--signing-key KEY]
fsbpyr verify FILE [--project P]
```

Make a signing key, save the key of a project after checking all of its fingerprint, sign a file,
and check one. [Signing](User-Signing.md) says how they fit together. `verify` exits 0 for
VERIFIED, 1 for NOT VERIFIED and 2 when it cannot tell, such as with no trusted key.

## `fsbpyr cache gc`

```
fsbpyr cache gc
```

Runs a collection now, under the rules of the automatic one (see
[The cache cleans itself](User-Running.md#the-cache)): what nobody used for 7 days goes, and
everything not in use when the file system has less than 10% free. It collects fsbpyr's own
cache (runtimes, Python distributions, sysroots and downloads, keeping what it builds with) and
every app cache it finds: those in `${XDG_CACHE_HOME:-~/.cache}`, and `$FSBPYR_CACHE_DIR` when
it is one. Without it, every app, fsbpyr included, collects its cache by itself in the
background, and fsbpyr collects the rest of its own cache when `build`, `inspect` or `runtime`
starts, a day after the last time (an hour, when the file system has less than 10% free). The
variants of other Pythons that a build downloaded (`~/.cache/fsbpyr/downloads/fsbpyr-3.X-...`) are left alone; delete them by hand to
reclaim the space.

## `fsbpyr runtime list` and `fsbpyr runtime build`

A **runtime** is what every built app starts with: the native launcher stub, linked with
CPython, plus the compiled standard library. A released fsbpyr carries its own runtime, so you
never need to build one to build apps.

```console
$ fsbpyr runtime list
cp314-x86_64-unknown-linux-gnu-6fe9008f23af  Python 3.14.10  glibc 2.34
cp314-x86_64-unknown-linux-gnu-5bb7820024be  Python 3.14.9  glibc 2.34
cp314-x86_64-unknown-linux-gnu-942f7ca276a0  Python 3.14.9  glibc 2.34
cp314-x86_64-unknown-linux-gnu-add67757fca9  Python 3.14.9  glibc 2.34
cp315-x86_64-unknown-linux-gnu-730de4afe228  Python 3.15.1  glibc 2.34
```

lists the runtime embedded in the running fsbpyr first, then the runtimes in the cache, each with
its Python and the glibc its launcher stub is linked for. (On the machine this ran on, the cache
also held runtimes of earlier fsbpyr versions: the id changes with the launcher's sources, the
CPython build and the sysroot, and the cache collects runtimes no one has used for 7 days.)

```
fsbpyr runtime build [--python X.Y] [--glibc 2.34|2.28] [--clang PATH]
```

fetches CPython from python-build-standalone, links the launcher and compiles the standard
library (`--python` defaults to the Python fsbpyr runs on). It is needed only when fsbpyr runs
from a source checkout or a wheel rather than as its own executable; it needs clang and lld 23 or
newer. Without `--clang` it tries `clang-23` to `clang-32`, then `clang`, on `PATH`, and takes
the first of version 23 or newer that has a matching `ld.lld`. Such an fsbpyr without a runtime
in the cache says
``no runtime for Python 3.14; run `fsbpyr runtime build --python 3.14`, or use the release binary, which carries its own``.

`--glibc` chooses the sysroot the launcher is linked against: `2.34` (the default) is Debian
12's, `2.28` is Rocky Linux 8.10's (glibc 2.28, libgcc 8.5 and kernel 4.18 headers, read from
the pinned RPM packages by fsbpyr itself, with no `rpm` tool), for apps that run on EL8 hosts
([Building for EL8](User-Building.md#building-for-el8-glibc-228)). The two runtimes have different
ids and both stay in the cache. A released fsbpyr does not use a runtime that
`runtime build --glibc 2.28` made: it pairs its own standard library with its release's EL8
stub instead (placed, cached or downloaded).

## `fsbpyr self update`

```
fsbpyr self update
```

Replaces the running executable with the latest release's build for the same Python, when that
release is newer, after checking the release's signature against the keys fsbpyr pins, the
download against its line of the signed sums, and the result as `inspect --verify` does. A release
that is not signed, or not by a pinned key, is refused. It refuses a build that needs a newer glibc than the
machine has, naming both versions, before it replaces anything. EL8 stubs placed beside the
executable (`fsbpyr-stub-*-glibc*`, beside the path it was run by or the file a link to it leads
to, where `--glibc` looks) are left alone. After the update a warning names those whose SHA-256
the new release's companion does not list in its `SHA256SUMS-glibc2.28`, says that `--glibc`
refuses them unless they carry the new runtime id, and asks you to place the new release's
stubs there, or to remove them and let `--glibc` download them; a stub that did not change is
not named. When that file cannot be read or fails its check (no companion release yet, no
network), the warning names every placed stub and says that `--glibc` refuses them if the runtime id changed. A
`--glibc 2.28` build refuses a stub only if its runtime id differs from the new release's,
which happens when the launcher, the pinned Python, the sysroot or the runtime layout changed.
See [Updating](User-Installing.md#updating).

## Completion and man

```sh
fsbpyr completion bash > ~/.local/share/bash-completion/completions/fsbpyr
fsbpyr completion zsh > ~/.zfunc/_fsbpyr     # a directory in $fpath, before compinit
fsbpyr completion fish > ~/.config/fish/completions/fsbpyr.fish
fsbpyr man > ~/.local/share/man/man1/fsbpyr.1
```

Both come from the same definitions as `--help`, so all three agree. Completion asks fsbpyr for
the words as you type: after `--entry` and `--extra` it offers the console scripts and extras
of the project being built (the directory named before them, or the current one). The installer
writes the man page and completions for you when it installs into a directory named `bin`.
