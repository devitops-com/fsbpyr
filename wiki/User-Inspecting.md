# Inspecting an executable

```
fsbpyr inspect [--json] [--verify] [--all-modules] [--files] BINARY
fsbpyr inspect --licenses BINARY
```

`fsbpyr inspect` shows what an executable built by fsbpyr was built from and what it needs from
the machine that runs it. It reads the file without running it. This page explains its output
and every option, using the `pandas-csv` corpus app built in [Building an app](User-Building.md).

## The summary

```console
$ fsbpyr inspect dist/corpus-pandas-csv
app        corpus-pandas-csv 1.0.0
built with fsbpyr 0.27.0-bdc2e0ed
python     3.14.9, glibc >= 2.34
uv         0.13.6
size       65.1 MB, 5 distributions
compressed zstd 9, native files zstd 9
digest     e25687d785a30180c72294861f95bb6ab7a89096366fb18d662d020cdf8eedb6
toolchain  clang version 23.1.3
           clang version 23.1.3 (https://github.com/llvm/llvm-project 0d261d1ca552c95a8f007e061c787ac7132fbcbc)
           GCC: (Debian 12.2.0-14+deb12u1) 12.2.0
           Linker: LLD 23.1.3 (https://github.com/llvm/llvm-project 0d261d1ca552c95a8f007e061c787ac7132fbcbc)
components expat expat_2.9.0; libmpdec 4.0.0; openssl OpenSSL 3.5.9 29 Sep 2026; sqlite 3.53.1; zlib 1.3.2
lock       uv.lock sha256:9f05a943323fe8894ecc0d60ffcb166e02ea7fdeacfaa9fecc44189ffb9abbac
extras     (none)
fs pkgs    babel, botocore, certifi, litellm, matplotlib, torch
allow libs (none)
sources    kept
entry      corpus_pandas_csv.cli:main
needs      glibc >= 2.34; libgcc_s.so.1 (GCC_4.3.0); libstdc++.so.6 (CXXABI_1.3.9, GLIBCXX_3.4.21); libz.so.1
system libraries: 7
  ld-linux-x86-64.so.2 (GLIBC_2.3)               libz.so.1
  libc.so.6 (GLIBC_2.34)
  libgcc_s.so.1 (GCC_4.3.0)
  libm.so.6 (GLIBC_2.29)
  libpthread.so.0 (GLIBC_2.3.4)
  libstdc++.so.6 (CXXABI_1.3.9, GLIBCXX_3.4.21)
shared libraries: 3
  numpy.libs/libgfortran-83c28eba-468e71e5.so.5.0.0
  numpy.libs/libquadmath-2284e583-a9307bba.so.0.0.0
  numpy.libs/libscipy_openblas64_-f48b354e.so
extension modules: 64
  numpy/_core/_multiarray_tests.cpython-314-x86_64-linux-gnu.so
  ...
modules: 5 top-level names in 5 distributions
  corpus-pandas-csv  numpy  pandas  python-dateutil: dateutil  six
standard library and fsbpyr: 187 top-level names
  __future__  ...
stored bytes (without the trailer):
  stub                                   20,939,049
  zero padding to the 64 KiB boundary        32,471
  payload header                                208
  index and string tables                   564,860
  blob alignment padding                      8,129
  standard library and boot modules       6,748,362
  distribution              code      sources    resources      natives
  corpus-pandas-csv          575           81          165            0
  numpy                3,773,956    1,736,336      939,171   10,938,359
  pandas               9,752,976    3,964,555      122,171    5,165,346
  python-dateutil        125,426       64,121      156,106            0
  six                     18,007        8,762          671            0
  total                                  65,059,863
```

| Line | Meaning |
|---|---|
| `app` | the project's name and version, and in brackets the git revision it was built from when the build recorded one, in the form of `fsbpyr --version` (`1.0.0 (untagged-4f9771de)`) |
| `built with` | the fsbpyr that built it, in the form `fsbpyr --version` prints |
| `python` | the embedded CPython, and the oldest glibc the executable starts on |
| `baseline` | only for an app built with `--glibc 2.28`: `glibc 2.28`, the glibc it was built for ([Building for EL8](User-Building.md#building-for-el8-glibc-228)) |
| `uv` | the uv that installed the distributions (needed to reproduce the build) |
| `size` | file size and number of distributions |
| `compressed` | the zstd level, and how native files are compressed (`xz -6` after `--best-compression`) |
| `digest` | the whole-file digest that every start checks (see [Integrity](User-Running.md#integrity)) |
| `toolchain` | the compilers and linker that built the launcher stub (from its `.comment` section) |
| `components` | versions of the libraries the runtime carries |
| `lock` | the lock file the app was built from, its path relative to the project, with its SHA-256: `uv.lock`, `pylock.toml`, a requirements file, `poetry.lock`, `pdm.lock`, `Pipfile.lock` or a single script's `<script>.lock`; `inferred from <venv>: <pins>` for pins read from a venv; no line for an unlocked build |
| `extras`, `fs pkgs`, `allow libs`, `sources` | the build settings (`fs pkgs` includes the built-in ones) |
| `entry` | the entry point the app runs: `module:function`, or a module, which scripts run as `__main__` (`_fsbpyr_scripts.main`) |
| `commands` | instead of `entry`, for an executable of several commands: each command and what it runs (`multi-alpha = multi.cli:alpha`) |
| `needs` | what the target must provide: glibc, and each system library with the newest symbol version used (`GLIBCXX`, `CXXABI`, `GCC`, `ZLIB`) |
| `system libraries` | every library the launcher and native files take from the system |
| `shared libraries`, `extension modules` | bundled native files, extracted to the cache on first use |
| `modules` | the top-level names of each distribution, in as many columns as the terminal fits |
| `stored bytes` | where the file's bytes go: the launcher stub (with CPython), the index, the standard library, and per distribution its bytecode, sources, data and native files |

The glibc shown is the newest glibc symbol version that the launcher and the bundled native
files use, so the oldest glibc the executable starts on. The launcher needs 2.34, so an app
without native files shows `glibc >= 2.34`. An app built with `--glibc 2.28` never shows less
than `glibc >= 2.28`, even where its launcher uses only older symbol versions.

## Options

| Option | Adds |
|---|---|
| `--verify` | recomputes the digest (`verified` after it, or an error), and prints the file's plain SHA-256 as `sha256` |
| `--json` | everything as JSON |
| `--all-modules` | every module, not only top-level names |
| `--files` | every packaged file with its SHA-256, size, kind and distribution |
| `--licenses` | the licence notices the executable carries, instead of the summary |

### `--verify`

```console
$ fsbpyr inspect --verify dist/corpus-pandas-csv
...
digest     e25687d785a30180c72294861f95bb6ab7a89096366fb18d662d020cdf8eedb6 verified
sha256     b449ca5fd22d06c1907d1ab7ed22e699b74828cefc97f96def2d26d0703332fb
...
$ sha256sum dist/corpus-pandas-csv
b449ca5fd22d06c1907d1ab7ed22e699b74828cefc97f96def2d26d0703332fb  dist/corpus-pandas-csv
```

`digest` is fsbpyr's own, checked at every start. `sha256` is the plain hash that `sha256sum`
and a GitHub attestation name. Without `--verify` both checks are skipped, so plain `inspect`
stays fast.

### `--json`

For scripts. The top-level keys in 0.27.0:

```
all_modules  app  build_info  built_with  digest  digest_covers  distributions  entry
extension_modules  glibc  glibc_baseline  may_load_from_system  modules  path  python
runtime_modules  schema  settings  sha256  shared_libraries  size  sizes  system_libraries
toolchain  uv  verified
```

An executable of several commands also has `commands`, a mapping of each command's name to its
target, and its `entry` is the table of `name=target` lines. `glibc_baseline` is `2.34` for an
app built the default way and `2.28` for one built with `--glibc 2.28`. With `--files` there is also
`files`, the list `--files` prints.

`schema` is the number of the JSON format, 1 in 0.27.0. `build_info` holds the build information recorded in
the file (project, lock, settings, runtime, git commit and more), described in
[Executable format](Dev-Executable-Format.md). For example, the glibc check from
[Building an app](User-Building.md#--strict):

```console
$ fsbpyr inspect --json dist/corpus-pandas-csv | jq -e '.glibc | split(".") | map(tonumber) <= [2, 34]'
true
```

### `--files`

Lists each packaged file with its SHA-256 (from the distribution's `RECORD`), size, kind and
owner:

```console
$ fsbpyr inspect --files dist/corpus-pandas-csv
...
files: 2484
  f22e179e58220ba6cf6cf36cf0ece316080523d55276aece952e42167cb219b2         158  metadata   corpus-pandas-csv: corpus_pandas_csv-1.0.0.dist-info/METADATA
  ----------------------------------------------------------------           -  metadata   corpus-pandas-csv: corpus_pandas_csv-1.0.0.dist-info/RECORD
  ...
```

### `--licenses`

Every executable statically links third-party libraries (OpenSSL, libffi, libedit, ncurses,
expat, mpdecimal, SQLite, zlib, liblzma, zstd, bzip2, libuuid and the HACL\* code of the hash
modules) and holds fsbpyr's launcher and boot modules. Their licence texts travel inside the
executable, except zstd's in an executable for Python 3.12 or 3.13 in 0.27.0 ([#56](https://github.com/devitops-com/fsbpyr/issues/56)). `--licenses`
prints them, then the notices each distribution ships:

```console
$ fsbpyr inspect --licenses dist/corpus-pandas-csv | grep '^==== '
==== fsbpyr: _fsbpyr_licenses/LICENSE.bzip2.txt ====
==== fsbpyr: _fsbpyr_licenses/LICENSE.cpython.txt ====
...
==== fsbpyr: _fsbpyr_licenses/LICENSE.zstd.txt ====
==== numpy 2.5.4: numpy-2.5.4.dist-info/licenses/LICENSE.txt ====
==== numpy 2.5.4: numpy-2.5.4.dist-info/licenses/numpy/_core/include/numpy/libdivide/LICENSE.txt ====
...
==== numpy 2.5.4: numpy/random/LICENSE.md ====
==== pandas 3.0.6: pandas-3.0.6.dist-info/LICENSE ====
==== python-dateutil 2.9.0.post0: python_dateutil-2.9.0.post0.dist-info/LICENSE ====
==== six 1.17.0: six-1.17.0.dist-info/LICENSE ====
```

The runtime's part is the exact list of what fsbpyr links. The distributions' part is best
effort: it finds the files of each dist-info `licenses/` directory, those `METADATA` names as
`License-File`, licence files in the dist-info root, and files named `LICENSE*`, `COPYING*`,
`NOTICE*` or `ThirdPartyNotices*` in the packages. That need not be every notice a package's
bundled libraries require.

fsbpyr's licence has a runtime exception for the launcher and the boot modules, so an app need
not reproduce fsbpyr's MIT notice for them. To redistribute an app, ship the output of
`inspect --licenses` with it (or point to the executable), and check the distributions' part
against what you know your dependencies bundle.
